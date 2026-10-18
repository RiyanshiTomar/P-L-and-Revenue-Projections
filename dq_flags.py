"""More flags on the GEW data: things that look wrong or need a decision, found by checking the full copy of the enrollment sheet (the raw layer)
and the payments. Nothing here changes a revenue number. Every flag is a row in dq_check (what, how many, how much money, who can close it)
and the students / payments behind it are rows in dq_flag_row, so they can be listed and downloaded.

    negative_amounts          nothing is negative: payments, refunds, booking, collected, revenue per month (a green tick, or a flag if one appears)
    overpaid                  total_balance_cr is negative: the student paid more than the fee
    batch_start_after_end     batch_start_date is after batch_end_date
    admission_dates_disagree  joining_date and new_receipt_date are different (we use new_receipt_date, as agreed)
    collection_mismatch       the enrollment sheet's total_collection and the payments in the collection sheets do not agree
    form_incomplete           admission form is not 'Completed' (Stage 1/2/3): these students are counted on their full booking
    status_unclear            scheme status is UNKNOWN, or INACTIVE although the batch has not ended
    refund_payments           payments that carry a refund
    zero_payments             payments of 0 rupees
    payment_before_admission  payment dated more than 30 days before the admission date
"""
from __future__ import annotations

import pandas as pd

import config

FLAGS = ("negative_amounts", "overpaid", "batch_start_after_end", "admission_dates_disagree", "collection_mismatch", "form_incomplete",
         "status_unclear", "refund_payments", "zero_payments", "payment_before_admission")


def _latest(conn, table: str) -> pd.DataFrame:
    return pd.read_sql(f'SELECT * FROM "{table}" WHERE last_seen_on = (SELECT MAX(last_seen_on) FROM "{table}")', conn)


def build_flags(conn, log) -> None:
    conn.execute("DELETE FROM dq_flag_row")
    conn.execute(f"DELETE FROM dq_check WHERE check_id IN ({','.join('?' * len(FLAGS))})", FLAGS)
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    tabs = [t for t in ("raw_gew_enroll_dump_fy26", "raw_gew_enroll_dump_fy27") if t in have]
    if not tabs:
        log("extra data flags: the full copy of the enrollment sheet is not there, skipped")
        return
    e = pd.concat([_latest(conn, t) for t in tabs], ignore_index=True)
    e["regno"] = pd.to_numeric(e["regno"], errors="coerce")
    e = e.dropna(subset=["regno"]).copy()
    e["regno"] = e["regno"].astype(int)
    e = e.drop_duplicates("regno", keep="last")
    branch = dict(conn.execute("SELECT reg_no, branch_id FROM fact_enrollment"))
    rev = dict(conn.execute("SELECT reg_no, SUM(amount) FROM fact_revenue_monthly GROUP BY 1"))
    paid = dict(conn.execute("SELECT reg_no, SUM(net_paid_amount) FROM fact_collection GROUP BY 1"))
    snap = conn.execute("SELECT value FROM meta WHERE key='snapshot_date'").fetchone()[0]
    num = lambda c: pd.to_numeric(e[c], errors="coerce")
    d = lambda c: pd.to_datetime(e[c], errors="coerce")
    e["bal"], e["tc"], e["tp"] = num("total_balance_cr"), num("total_collection_cr"), num("total_payable_cr")
    e["rev"] = e["regno"].map(rev).fillna(0.0)
    e["branch"] = e["regno"].map(branch)
    checks = []

    def rows_for(flag, df, detail, amount, d1=None, d2=None):
        out = []
        for r in df.itertuples():
            out.append((flag, int(r.regno), None if pd.isna(r.branch) else int(r.branch), detail(r), None if amount is None else float(amount(r)),
                        None if d1 is None else d1(r), None if d2 is None else d2(r)))
        conn.executemany("INSERT INTO dq_flag_row VALUES (?,?,?,?,?,?,?)", out)

    def add(flag, title, sev, n, amount, detail, who):
        checks.append((flag, title, sev, int(n), None if amount is None else float(amount), detail, who))

    inr = lambda x: f"{abs(x):,.0f}"

    # ---- 1 nothing negative
    neg = {
        "payments (net paid)": conn.execute("SELECT COUNT(*) FROM fact_collection WHERE net_paid_amount < 0").fetchone()[0],
        "payments (paid)": conn.execute("SELECT COUNT(*) FROM fact_collection WHERE paid_amount < 0").fetchone()[0],
        "refunds": conn.execute("SELECT COUNT(*) FROM fact_collection WHERE refund_amount < 0").fetchone()[0],
        "booking (total payable)": conn.execute("SELECT COUNT(*) FROM fact_enrollment WHERE total_payable < 0").fetchone()[0],
        "collected (total collection)": conn.execute("SELECT COUNT(*) FROM fact_enrollment WHERE total_collection < 0").fetchone()[0],
        "revenue of a student in a month": conn.execute("SELECT COUNT(*) FROM fact_revenue_monthly WHERE amount < 0").fetchone()[0],
    }
    nneg = sum(neg.values())
    add("negative_amounts", "Negative amounts", "ok" if nneg == 0 else "note", nneg, None,
        "Checked: " + ", ".join(f"{k}: {v}" for k, v in neg.items()) + ". "
        + ("None of these is negative." if nneg == 0 else "Some are negative, see the list.") + " The only negative number in the sheet is the balance of students who paid more than their fee (next item).",
        "Data team")

    # ---- 2 overpaid (negative balance)
    x = e[e.bal < -100]
    rows_for("overpaid", x, lambda r: f"balance {r.bal:,.0f}: paid {r.tc:,.0f} against a fee of {r.tp:,.0f}", lambda r: r.bal)
    add("overpaid", "Students who paid more than their fee (negative balance)", "note", len(x), x.bal.sum() if len(x) else 0,
        "The sheet's total_balance_cr is negative: the student has paid more than the fee. Either the fee is too low in the sheet, or a payment is wrong or a refund is missing. "
        "Revenue counts the booking, so it does not change; the collection figure does.", "Finance / Data team")

    # ---- 3 batch start after batch end
    bs, be = d("batch_start_date"), d("batch_end_date")
    x = e[bs > be]
    rows_for("batch_start_after_end", x, lambda r: "batch start is after batch end", None, lambda r: str(r.batch_start_date)[:10], lambda r: str(r.batch_end_date)[:10])
    add("batch_start_after_end", "Batch starts after it ends", "note", len(x), None,
        "batch_start_date is later than batch_end_date. One of the two dates is wrong in the sheet. The revenue uses the admission date and batch_end_date, so the months of these students may be wrong.", "Data team")

    # ---- 4 the two admission dates
    jd, nr = d("joining_date"), d("new_receipt_date")
    x = e[(jd != nr) & jd.notna() & nr.notna()]
    diffm = int((x.joining_date.astype(str).str[:7] != x.new_receipt_date.astype(str).str[:7]).sum())
    rows_for("admission_dates_disagree", x, lambda r: f"joining_date {str(r.joining_date)[:10]}, new_receipt_date {str(r.new_receipt_date)[:10]}", None,
             lambda r: str(r.joining_date)[:10], lambda r: str(r.new_receipt_date)[:10])
    add("admission_dates_disagree", "Two admission dates that disagree", "note", len(x), None,
        f"joining_date and new_receipt_date are different for these students; in {diffm:,} of them they are in different months. We use new_receipt_date as the admission date (as agreed), "
        "so these students may move to another month if the other date is the right one.", "Data team")

    # ---- 5 enrollment says one thing, payments say another
    e["pay_sum"] = e["regno"].map(paid).fillna(0.0)
    x = e[(e.tc - e.pay_sum).abs() > 500]
    rows_for("collection_mismatch", x, lambda r: f"enrollment sheet says collected {r.tc:,.0f}; the collection sheets add up to {r.pay_sum:,.0f}", lambda r: r.tc - r.pay_sum)
    add("collection_mismatch", "Collected amount: enrollment sheet and collection sheets do not agree", "note", len(x), (x.tc - x.pay_sum).abs().sum() if len(x) else 0,
        "For these students the total_collection in the enrollment sheet is different from the sum of their payments in the collection sheets (more than Rs 500 apart). "
        "Revenue is not affected (it uses the booking), but the collection numbers depend on which sheet is right.", "Data team")

    # ---- 6 admission form not completed
    x = e[e.form_status.astype(str).str.strip().str.lower() != "completed"]
    rows_for("form_incomplete", x, lambda r: f"form status: {r.form_status}; paid {r.tc:,.0f} of {r.tp:,.0f}", lambda r: r.rev)
    add("form_incomplete", "Admission form not completed (Stage 1, 2 or 3)", "ask", len(x), x.rev.sum() if len(x) else 0,
        "These students are in the enrollment sheet with their full booking, so their full fee is counted as revenue this year (the amount shown). "
        f"Together they have paid {x.tc.sum() / max(x.tp.sum(), 1) * 100:.0f}% of their fee, against {e[~e.index.isin(x.index)].tc.sum() / max(e[~e.index.isin(x.index)].tp.sum(), 1) * 100:.0f}% for completed forms. "
        "To confirm: should a student whose form is not completed be counted as an admission?", "Operations / Data team")

    # ---- 7 scheme status unclear
    st = e.schemes_status.astype(str).str.strip().str.upper()
    x = e[(st == "UNKNOWN") | ((st == "INACTIVE") & (d("batch_end_date") >= pd.Timestamp(snap)))]
    rows_for("status_unclear", x, lambda r: f"scheme status {r.schemes_status}; batch ends {str(r.batch_end_date)[:10]}", lambda r: r.rev)
    add("status_unclear", "Scheme status unclear (UNKNOWN, or INACTIVE with the batch still running)", "info", len(x), x.rev.sum() if len(x) else 0,
        "schemes_status is UNKNOWN, or INACTIVE although the batch has not ended yet. The revenue of these students (amount shown) is counted. Worth confirming what the status means.", "Data team")

    # ---- 8, 9, 10 payments
    pay = pd.read_sql("""SELECT p.reg_no, p.branch_id, p.receipt_date, p.paid_amount, p.net_paid_amount, p.refund_amount, e.joining_date
                         FROM fact_collection p LEFT JOIN fact_enrollment e ON e.reg_no = p.reg_no""", conn)
    x = pay[pay.refund_amount > 0]
    conn.executemany("INSERT INTO dq_flag_row VALUES (?,?,?,?,?,?,?)", [("refund_payments", None if pd.isna(r.reg_no) else int(r.reg_no), None if pd.isna(r.branch_id) else int(r.branch_id),
                      f"refund {r.refund_amount:,.0f} on a payment of {r.paid_amount:,.0f}", float(r.refund_amount), r.receipt_date, None) for r in x.itertuples()])
    add("refund_payments", "Payments that carry a refund", "info", len(x), x.refund_amount.sum() if len(x) else 0,
        "The booking of these students is not reduced for the refund (revenue counts the full booking). The amount is the money refunded.", "Finance")
    x = pay[pay.net_paid_amount == 0]
    conn.executemany("INSERT INTO dq_flag_row VALUES (?,?,?,?,?,?,?)", [("zero_payments", None if pd.isna(r.reg_no) else int(r.reg_no), None if pd.isna(r.branch_id) else int(r.branch_id),
                      "payment of 0 rupees", 0.0, r.receipt_date, None) for r in x.itertuples()])
    add("zero_payments", "Payments of 0 rupees", "info", len(x), None, "Payment rows with a net amount of 0. Probably cancelled or fully refunded payments.", "Data team")
    pj = pd.to_datetime(pay.joining_date, errors="coerce")
    pr = pd.to_datetime(pay.receipt_date, errors="coerce")
    x = pay[(pj - pr).dt.days > 30]
    conn.executemany("INSERT INTO dq_flag_row VALUES (?,?,?,?,?,?,?)", [("payment_before_admission", None if pd.isna(r.reg_no) else int(r.reg_no), None if pd.isna(r.branch_id) else int(r.branch_id),
                      f"paid {r.net_paid_amount:,.0f} on {r.receipt_date}, admission {r.joining_date}", float(r.net_paid_amount), r.receipt_date, r.joining_date) for r in x.itertuples()])
    add("payment_before_admission", "Payments made more than 30 days before the admission date", "info", len(x), x.net_paid_amount.sum() if len(x) else 0,
        "A payment well before the admission date can be an advance, or a wrong date on the payment or on the admission. Payments up to 30 days before are not listed.", "Data team")

    conn.executemany("INSERT INTO dq_check VALUES (?,?,?,?,?,?,?)", checks)
    log("extra data flags: " + "; ".join(f"{c[0]}={c[3]}" for c in checks))

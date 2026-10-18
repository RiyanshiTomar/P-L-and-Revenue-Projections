"""Data-quality findings, kept as real tables in the database (not only in a report), so they can be listed, downloaded and shown on the Gap Check page.

Nothing here changes any revenue number. Each finding is stored twice:
    dq_check            one summary row per finding (what it is, how many rows, how much money)
    dq_orphan_payment / dq_duplicate_payment / dq_student_bad_dates      the actual rows behind it
"""
from __future__ import annotations

import datetime as dt

import config


def build_dq(conn, add_dq, log) -> None:
    for t in ("dq_check", "dq_orphan_payment", "dq_duplicate_payment", "dq_student_bad_dates"):
        conn.execute(f"DELETE FROM {t}")

    # 1) payments whose student number is not in the enrollment data: they can never become revenue
    conn.execute("""INSERT INTO dq_orphan_payment
        SELECT receipt_date, month, branch_id, reg_no, payment_mode, paid_amount, net_paid_amount, refund_amount, src
        FROM fact_collection WHERE reg_no IS NULL OR reg_no NOT IN (SELECT reg_no FROM fact_enrollment)""")
    n1, a1 = conn.execute("SELECT COUNT(*), COALESCE(SUM(net_paid_amount),0) FROM dq_orphan_payment").fetchone()
    total = conn.execute("SELECT COALESCE(SUM(net_paid_amount),0) FROM fact_collection").fetchone()[0] or 1.0

    # 2) payments that look duplicated: same student, date, amount and mode more than once
    conn.execute("""INSERT INTO dq_duplicate_payment
        SELECT reg_no, MIN(branch_id), receipt_date, payment_mode, net_paid_amount, COUNT(*), net_paid_amount * (COUNT(*) - 1)
        FROM fact_collection WHERE net_paid_amount > 0
        GROUP BY reg_no, receipt_date, net_paid_amount, payment_mode HAVING COUNT(*) > 1""")
    n2, a2 = conn.execute("SELECT COUNT(*), COALESCE(SUM(extra_amount),0) FROM dq_duplicate_payment").fetchone()

    # 3) students whose batch end is before their admission: kept, the whole amount is counted in the start month
    conn.execute("""INSERT INTO dq_student_bad_dates
        SELECT reg_no, branch_id, eff_start, eff_end, payable_ex_gst, CAST(julianday(eff_end) - julianday(eff_start) AS INTEGER)
        FROM fact_student WHERE end_month < start_month""")
    n3, a3 = conn.execute("SELECT COUNT(*), COALESCE(SUM(amount),0) FROM dq_student_bad_dates").fetchone()

    # 4) plan rows whose AOV was an error in the sheet (target 0, so 0 / 0)
    n4 = conn.execute("SELECT COALESCE(SUM(n_rows),0) FROM dq_issue WHERE code='AOV_DIV_BY_ZERO'").fetchone()[0]
    # 5) fees that look like mock cards
    n5, a5 = conn.execute("SELECT COUNT(*), COALESCE(SUM(total_payable),0) FROM fact_enrollment WHERE total_payable < ?", (config.MOCK_BILL_THRESHOLD,)).fetchone()

    rows = [
        ("orphan_payments", "Payments whose student is not in the enrollment data", "note", n1, a1,
         f"{a1 / total * 100:.2f}% of all money received. There is no student to attach the money to, so they cannot be counted in collection by student. Revenue is counted on the booking, so these payments do not change any revenue number. Ask the data team whether these students are missing from the enrollment sheet."),
        ("duplicate_payments", "Payments that look duplicated", "note", n2, a2,
         "Same student, date, amount and payment mode more than once. They are counted as they are in the sheet (they may be genuine part-payments on the same day). Revenue is counted on the booking, so this does not change revenue, only collection. The money shown is the extra amount if all of them were real duplicates."),
        ("students_end_before_start", "Students whose batch ends before they were admitted", "note", n3, a3,
         "Kept: the whole booking amount is counted in the admission month so nothing is lost. The batch end date or the admission date may be wrong in the sheet."),
        ("plan_fee_error", "Annual plan rows where the fee per student is an error in the sheet", "info", n4, None,
         "Months where nothing is planned (0 students), so the fee was 0 ÷ 0. Filled safely; no effect on any number."),
        ("mock_fees", "Students with a fee below ₹100 (mock cards)", "info" if n5 == 0 else "note", n5, a5,
         "Fees this small are not real admissions." if n5 else "None found in the enrollment data."),
    ]
    n6, a6 = conn.execute("SELECT COUNT(*), COALESCE(SUM(payable_ex_gst - adj_collection), 0) FROM fact_student WHERE payable_ex_gst - adj_collection > 1").fetchone()
    tb = conn.execute("SELECT COALESCE(SUM(payable_ex_gst), 0) FROM fact_student").fetchone()[0] or 1.0
    ve, vc, va = conn.execute("SELECT (SELECT COUNT(*) FROM archive_enrollment_removed), (SELECT COUNT(*) FROM archive_collection_removed), "
                              "(SELECT COALESCE(SUM(net_paid_amount),0) FROM archive_collection_removed)").fetchone()
    rows.append(("vanished_rows", "Rows that were in the sheets earlier but have disappeared", "note" if (ve + vc) else "info", ve + vc, va,
                 f"{ve:,} students and {vc:,} payments. They are kept safely in the archive tables and are not counted in any revenue number. "
                 "If many rows disappear (for example the sheet was cleaned or old rows were moved elsewhere), ask who changed the sheet.", "Whoever edits the sheets"))
    rows.append(("booked_not_collected", "Booked but not collected yet (fee without GST)", "info", n6, a6,
                 f"{n6:,} students have paid less than their booking so far: {a6 / tb * 100:.1f}% of all bookings. Revenue counts the full booking. This is the part of it that is still to be collected.", "Finance"))
    who = {"orphan_payments": "Data team", "duplicate_payments": "Finance", "students_end_before_start": "Data team", "plan_fee_error": "—", "mock_fees": "—"}
    rows = [r if len(r) == 7 else (*r, who.get(r[0])) for r in rows]
    rows += [
        ("missing_installments", "Instalment plans: who will pay how much, and when", "missing", 0, None,
         "Revenue is counted on the booking, so it does not need this. A collection forecast (when the unpaid part will come in) does.", "Operations / Finance"),
        ("missing_branch_info", "Opening month and capacity (seats, classrooms) of every branch", "missing", 0, None,
         "Needed to predict next year's plan for new branches and to separate branch age from season.", "Operations"),
        ("missing_funnel_key", "A phone number or lead number in the enrollment data", "missing", 0, None,
         "The walk-in and demo lists have a phone number, the enrollment data has none, so walk-in → demo → admission cannot be linked.", "Data team"),
        ("missing_before_2024", "Student data before January 2024", "missing", 0, None,
         "Last year can be compared completely, the year before cannot.", "Data team"),
        ("missing_live_pl_sheet", "A live (not hand-pasted) version of the Iteration sheet", "missing", 0, None,
         "It is a snapshot, so the Gap Check always shows a timing gap until they refresh it.", "P&L team"),
        ("missing_costs", "Branch-wise costs loaded from the cost workbook", "missing", 0, None,
         "The cost tabs exist but only the Govt exam - Offline revenue lines are loaded. Needed for the branch-wise P&L (next phase).", "Finance"),
    ]
    conn.executemany("INSERT INTO dq_check VALUES (?,?,?,?,?,?,?)", rows)
    add_dq(conn, "data quality", "DQ_CHECKS_BUILT", "; ".join(f"{r[0]}={r[3]}" for r in rows), sum(r[3] for r in rows))
    log(f"data-quality tables: orphan payments {n1} ({a1 / 1e5:.1f} lakh), possible duplicate payments {n2} groups ({a2 / 1e5:.1f} lakh), bad-date students {n3}")

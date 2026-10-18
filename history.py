"""History base: revenue for EVERY student in the enrollment dump, built only from the two live dumps.

Why: this is the revenue base for the whole portal. Everything comes from the two main Google Sheets (enrollment dump + collection dumps),
which refresh on their own and go back to Jan 2024, so FY26 and FY27 are built the same way and can be compared year on year.

Rule (as confirmed by the business: new_receipt_date = admission date = effective start; batch_end_date = effective end):
    start  = admission date (new_receipt_date)
    end    = batch end date
    amount = BOOKING = total_payable (the fee the student has to pay) / GST_DIVISOR
    revenue = amount spread equally over every calendar month start..end (both included)
Months are kept up to the end of the current financial year (config.FY_END).
"""
from __future__ import annotations

import pandas as pd

import config
import revenue_engine as eng

ISO = r"^\d{4}-\d{2}-\d{2}$"


def build_history(conn, add_dq, log) -> dict:
    snap = conn.execute("SELECT value FROM meta WHERE key='snapshot_date'").fetchone()[0]

    e = pd.read_sql(
        """SELECT reg_no, branch_id, scheme, course, joining_date jd, substr(batch_end,1,10) be, total_payable tp
           FROM fact_enrollment WHERE joining_date <= ?""", conn, params=(snap,))
    usable = e.jd.str.match(ISO).fillna(False).astype(bool) & e.be.str.match(ISO).fillna(False).astype(bool)
    if (~usable).any():
        add_dq(conn, "enrollment dump", "HISTORY_SKIPPED_BAD_DATES", "students without a usable admission date or batch end date are left out of the base",
               int((~usable).sum()))
    e = e[usable].copy()
    e["start"] = e["jd"]                               # admission date (new_receipt_date) = effective start date
    e["end"] = e["be"]
    e["amt"] = (e.tp.fillna(0.0).clip(lower=0.0) / config.GST_DIVISOR).round(2)      # booking without GST
    e["flag"] = None
    bad = e.end.str[:7] < e.start.str[:7]
    e.loc[bad, "flag"] = "END_MONTH_BEFORE_START_MONTH"
    e.loc[e.amt == 0, "flag"] = e.loc[e.amt == 0, "flag"].fillna("NO_BOOKING")

    fy_end = eng.ym_idx(config.FY_END)
    stu_rows, rev_rows = [], []
    for r in e.itertuples():
        s0, e0 = r.start[:7], r.end[:7]
        n = max(1, eng.months_inclusive(s0, e0))        # bad dates: whole amount in the start month, nothing dropped
        stu_rows.append((int(r.reg_no), int(r.branch_id), r.scheme, r.course, r.jd, r.start, r.end, s0, e0, n, float(r.amt), r.flag))
        if r.amt > 0:
            per, si = r.amt / n, eng.ym_idx(s0)
            for k in range(n):
                if si + k > fy_end:
                    break
                rev_rows.append((int(r.reg_no), int(r.branch_id), eng.idx_ym(si + k), per))
    conn.execute("DELETE FROM fact_student_dump")
    conn.execute("DELETE FROM fact_revenue_dump_monthly")
    conn.executemany("INSERT INTO fact_student_dump VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", stu_rows)
    conn.executemany("INSERT INTO fact_revenue_dump_monthly VALUES (?,?,?,?)", rev_rows)
    first = conn.execute("SELECT MIN(joining_date) FROM fact_student_dump").fetchone()[0]
    add_dq(conn, "history", "HISTORY_BASE_BUILT",
           f"{len(stu_rows)} students from the enrollment dump, revenue on the booking (total payable / 1.18), data till {snap} (first joiner {first}). Months before 2024-04 are not in the data, "
           f"so FY25 is incomplete; FY26 onwards is complete.", len(stu_rows), float(e.amt.sum()))
    n_bad, n_nopay = int(bad.sum()), int((e.amt == 0).sum())
    if n_bad:
        add_dq(conn, "history", "HISTORY_END_BEFORE_START", "batch end month before start month: whole amount booked in the start month", n_bad,
               float(e.loc[bad, "amt"].sum()))
    if n_nopay:
        add_dq(conn, "history", "HISTORY_NO_BOOKING", "students in the enrollment dump whose total payable is 0: no revenue", n_nopay)
    log(f"history base: {len(stu_rows)} students, {len(rev_rows)} student-months, Rs {e.amt.sum()/1e7:.2f} Cr")
    return {"students": len(stu_rows), "rows": len(rev_rows)}

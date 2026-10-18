"""Revenue recognition + projection engine. Pure functions over the SQLite database.

Business rule (confirmed with the owner):
    A student's revenue = Total Adjusted Collection, spread EQUALLY over each calendar month from the
    Effective Start month to the Effective End month, both inclusive.

Three layers are produced for every branch x month of the financial year:
    recognised : months up to the data as-of month         (what has already happened, from the rule above)
    committed  : months after as-of from students ALREADY enrolled (the run-off of existing students)
    expected   : months after as-of from FUTURE enrollments (AOP target x achievement %, not yet enrolled)
Only `recognised` + `committed` are backed by real students; `expected` is an assumption and is always
kept separate so a CXO can see what is certain and what is estimated.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
import statistics
from collections import defaultdict

import config


# ---------------------------------------------------------------- month helpers
def ym_idx(ym: str) -> int:
    y, m = ym.split("-")
    return int(y) * 12 + int(m) - 1


def idx_ym(i: int) -> str:
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


def fy_months() -> list[str]:
    return [idx_ym(i) for i in range(ym_idx(config.FY_START), ym_idx(config.FY_END) + 1)]


def months_inclusive(start_ym: str, end_ym: str) -> int:
    return ym_idx(end_ym) - ym_idx(start_ym) + 1


def amount_col() -> str:
    return "payable_ex_gst"            # the booking (total payable / GST): the revenue amount of a student


# ---------------------------------------------------------------- schedule (per student x month)
def rebuild_schedule(conn: sqlite3.Connection) -> dict:
    """Recompute fact_revenue_monthly from fact_student. Returns a small stats dict."""
    fy = set(fy_months())
    rows = conn.execute(
        f"SELECT reg_no, branch_id, COALESCE({amount_col()}, 0), start_month, end_month FROM fact_student"
    ).fetchall()
    out, invalid, invalid_amt = [], 0, 0.0
    for reg_no, branch_id, amt, sm, em in rows:
        n = months_inclusive(sm, em)
        if n < 1:                       # end month earlier than start month (bad dates): nothing is dropped,
            invalid += 1                # the whole amount is booked in the start month instead
            invalid_amt += amt
            n = 1
        conn.execute("UPDATE fact_student SET n_months=? WHERE reg_no=?", (n, reg_no))
        per = amt / n
        s = ym_idx(sm)
        for k in range(n):
            m = idx_ym(s + k)
            if m in fy:
                out.append((reg_no, branch_id, m, per))
    conn.execute("DELETE FROM fact_revenue_monthly")
    conn.executemany("INSERT INTO fact_revenue_monthly VALUES (?,?,?,?)", out)
    conn.commit()
    return {"students": len(rows), "schedule_rows": len(out), "bad_dates_booked_in_start_month": invalid,
            "bad_dates_amount": invalid_amt}


# ---------------------------------------------------------------- as-of month
def as_of_info(conn: sqlite3.Connection) -> dict:
    """Latest data date, and the last month that is fully actual."""
    snap = conn.execute("SELECT value FROM meta WHERE key='snapshot_date'").fetchone()[0]
    d = dt.date.fromisoformat(snap)
    next_day = d + dt.timedelta(days=1)
    month_complete = next_day.month != d.month           # data runs to the last day of the month
    last_actual = f"{d.year:04d}-{d.month:02d}" if month_complete else idx_ym(ym_idx(f"{d.year:04d}-{d.month:02d}") - 1)
    return {"snapshot_date": snap, "last_actual_month": last_actual, "month_complete": month_complete}


# ---------------------------------------------------------------- layers
def _by_branch_month(conn, sql, params=()):
    d = defaultdict(lambda: defaultdict(float))
    for b, m, v in conn.execute(sql, params):
        d[b][m] += v
    return d


def realised_and_committed(conn, last_actual: str):
    """Split the stored schedule at the as-of month."""
    sched = _by_branch_month(
        conn, "SELECT branch_id, month, SUM(amount) FROM fact_revenue_monthly GROUP BY branch_id, month")
    recog, comm = defaultdict(dict), defaultdict(dict)
    la = ym_idx(last_actual)
    for b, mm in sched.items():
        for m, v in mm.items():
            (recog if ym_idx(m) <= la else comm)[b][m] = v
    return recog, comm


def branch_ticket_profile(conn, last_actual: str):
    """Per-branch ticket size and typical duration used for FUTURE enrollments.

    Ticket size (confirmed by the business): use the CURRENT trend of this year, not the AOP and not 'collected so far'
    (recent students have only paid their first instalment, so a collected average is too low).
        booking AOV  = average total payable of everyone who joined this FY so far; it includes GST, same basis as the AOP's AOV
        revenue ticket = booking AOV / GST_DIVISOR   (revenue is booked ex-GST)
    Duration = median number of months of students that started in the last EXPECTED_WINDOW_MONTHS months."""
    la = ym_idx(last_actual)
    lo = idx_ym(la - config.EXPECTED_WINDOW_MONTHS + 1)
    drows = conn.execute(
        f"""SELECT branch_id, n_months FROM fact_student
           WHERE start_month BETWEEN ? AND ? AND n_months IS NOT NULL
             AND bill_amount >= ? AND {amount_col()} > 0""",
        (lo, last_actual, config.MOCK_BILL_THRESHOLD)).fetchall()
    dur = defaultdict(list)
    for br, n in drows:
        dur[br].append(n)
    all_n = [n for v in dur.values() for n in v]
    g_dur = max(1, round(statistics.median(all_n))) if all_n else 1

    arows = conn.execute(
        """SELECT branch_id, total_payable FROM fact_enrollment
           WHERE join_month BETWEEN ? AND ? AND total_payable >= ?""",
        (config.FY_START, last_actual, config.MOCK_BILL_THRESHOLD)).fetchall()
    aov = defaultdict(list)
    for br, p in arows:
        aov[br].append(p)
    all_p = [p for v in aov.values() for p in v]
    g_aov = sum(all_p) / len(all_p) if all_p else 0.0

    prof = {}
    for br, in conn.execute("SELECT branch_id FROM dim_branch WHERE is_active=1"):
        pv, dv = aov.get(br, []), dur.get(br, [])
        own_aov = len(pv) >= config.EXPECTED_MIN_STUDENTS
        booking = sum(pv) / len(pv) if own_aov else g_aov
        prof[br] = {"booking_aov": booking, "aov": booking / config.GST_DIVISOR,
                    "duration": max(1, round(statistics.median(dv))) if len(dv) >= config.EXPECTED_MIN_STUDENTS else g_dur,
                    "n_students": len(pv), "fallback": not own_aov}
    return prof, {"window_from": lo, "window_to": last_actual, "aov_from": config.FY_START, "aov_to": last_actual,
                  "global_booking_aov": g_aov, "global_duration": g_dur}


def expected_new(conn, aop_id: int, last_actual: str, achievement_pct: float, basis: str, gst_divisor: float):
    """Revenue from enrollments that have NOT happened yet (months after last_actual), driven by the AOP.
    basis 'trend' = current-year booking AOV / GST (default); 'aop' = the AOP's own AOV / gst_divisor."""
    prof, win = branch_ticket_profile(conn, last_actual)
    fy_end = ym_idx(config.FY_END)
    first = ym_idx(last_actual) + 1
    # The month in progress: students who have already joined in it are real (they are in 'current students'), so only the
    # REMAINING part of that month's plan is an estimate - otherwise they would be counted twice.
    partial = idx_ym(first) if not as_of_info(conn)["month_complete"] else None
    joined = defaultdict(int)
    if partial:
        for b_, n_ in conn.execute("SELECT branch_id, COUNT(*) FROM fact_enrollment WHERE join_month=? GROUP BY 1", (partial,)):
            joined[b_] = n_
    tgt = conn.execute(
        """SELECT t.branch_id, t.month, t.enroll_target, t.aov FROM fact_target t
           JOIN dim_branch b USING(branch_id) WHERE t.aop_id=? AND b.is_active=1""", (aop_id,)).fetchall()
    exp = defaultdict(lambda: defaultdict(float))
    for b, m, n_enr, aov_t in tgt:
        mi = ym_idx(m)
        if mi < first or mi > fy_end or n_enr <= 0:
            continue
        enr = n_enr * achievement_pct / 100.0
        if m == partial:
            enr = max(0.0, enr - joined[b])
        ticket = prof[b]["aov"] if basis == "trend" else aov_t / (gst_divisor or 1.0)
        dur = prof[b]["duration"]
        per = enr * ticket / dur
        for k in range(dur):
            if mi + k <= fy_end:
                exp[b][idx_ym(mi + k)] += per
    return exp, prof, win


def aov_trend(conn, aop_id: int):
    """Booking AOV (incl GST) of enrollments by joining month, next to the AOP's AOV for the same month."""
    aop = {m: (bk / en if en else 0.0) for m, en, bk in conn.execute(
        """SELECT t.month, SUM(t.enroll_target), SUM(t.booking_target) FROM fact_target t
           JOIN dim_branch b USING(branch_id) WHERE t.aop_id=? AND b.is_active=1 GROUP BY t.month""", (aop_id,))}
    out = []
    for m, n, avg in conn.execute(
            """SELECT e.join_month, COUNT(*), AVG(e.total_payable) FROM fact_enrollment e
               JOIN dim_branch b USING(branch_id)
               WHERE b.is_active=1 AND e.join_month BETWEEN ? AND ? AND e.total_payable >= ?
               GROUP BY e.join_month ORDER BY e.join_month""",
            (config.FY_START, config.FY_END, config.MOCK_BILL_THRESHOLD)):
        out.append({"month": m, "enrollments": n, "booking_aov": avg, "aop_aov": aop.get(m, 0.0)})
    return out


def enrollment_actuals(conn):
    """Actual enrollments per branch x month (from the enrollment dumps) within the FY."""
    d = defaultdict(lambda: defaultdict(int))
    for b, m, n in conn.execute(
        "SELECT branch_id, join_month, COUNT(*) FROM fact_enrollment WHERE join_month BETWEEN ? AND ? GROUP BY 1,2",
        (config.FY_START, config.FY_END)):
        d[b][m] = n
    return d


def targets(conn, aop_id: int):
    d = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
    for b, m, e, bk in conn.execute(
            "SELECT branch_id, month, enroll_target, booking_target FROM fact_target WHERE aop_id=?", (aop_id,)):
        d[b][m] = [e, bk]
    return d


def build_view(conn, aop_id: int, achievement_pct: float = 100.0, basis: str = "trend", gst_divisor: float = config.GST_DIVISOR):
    info = as_of_info(conn)
    months = fy_months()
    recog, comm = realised_and_committed(conn, info["last_actual_month"])
    exp, prof, win = expected_new(conn, aop_id, info["last_actual_month"], achievement_pct, basis, gst_divisor)
    enr = enrollment_actuals(conn)
    tg = targets(conn, aop_id)
    # where each month's revenue comes from: students admitted before this FY / earlier this FY / in that same month
    split = defaultdict(dict)
    for b_, m_, x1, x2, x3 in conn.execute(
            """SELECT r.branch_id, r.month,
                      SUM(CASE WHEN s.start_month <  ? THEN r.amount ELSE 0 END),
                      SUM(CASE WHEN s.start_month >= ? AND s.start_month < r.month THEN r.amount ELSE 0 END),
                      SUM(CASE WHEN s.start_month = r.month THEN r.amount ELSE 0 END)
               FROM fact_revenue_monthly r JOIN fact_student s USING(reg_no) GROUP BY r.branch_id, r.month""",
            (config.FY_START, config.FY_START)):
        split[b_][m_] = (x1, x2, x3)
    branches = []
    for b, name, short, cohort in conn.execute(
            "SELECT branch_id, branch_name, short_name, cohort_id FROM dim_branch WHERE is_active=1 ORDER BY cohort_id, branch_name"):
        branches.append({
            "branch_id": b, "name": name, "short": short or name, "cohort_id": cohort,
            "recognised": [round(recog[b].get(m, 0.0), 2) for m in months],
            "committed": [round(comm[b].get(m, 0.0), 2) for m in months],
            "expected": [round(exp[b].get(m, 0.0), 2) for m in months],
            "from_last_year": [round(split[b].get(m, (0, 0, 0))[0], 2) for m in months],
            "from_earlier_this_year": [round(split[b].get(m, (0, 0, 0))[1], 2) for m in months],
            "from_same_month": [round(split[b].get(m, (0, 0, 0))[2], 2) for m in months],
            "enroll_actual": [enr[b].get(m, 0) for m in months],
            "enroll_target": [tg[b][m][0] if m in tg[b] else 0 for m in months],
            "booking_target": [tg[b][m][1] if m in tg[b] else 0 for m in months],
            "profile": {**{k: (round(v, 2) if isinstance(v, float) else v) for k, v in prof[b].items()},
                        "aop_aov": round(sum(v[1] for v in tg[b].values()) / sum(v[0] for v in tg[b].values()), 2)
                        if sum(v[0] for v in tg[b].values()) else 0},
        })
    return {"as_of": info, "months": months, "branches": branches, "window": win,
            "params": {"aop_id": aop_id, "achievement_pct": achievement_pct, "basis": basis,
                       "gst_divisor": gst_divisor}}

"""Gap Check: where do OUR numbers (built only from the two main Google Sheets) differ from the Iteration sheet?

The "Govt Offline Iteration" sheet never drives any portal number. Our side is counted on the BOOKING (the fee the student has to pay,
without GST: our total_payable / 1.18). The Iteration side is its "Total Adjusted Collection" (column K), as the sheet gives it.
The difference in this year's revenue is split into causes that add up EXACTLY to the total gap:

    Ours - Sheet  =   students only we have                      (+)
                    - students only the sheet has                (-)
                    + different start/end dates                  (date)
                    + different amount for the student           (amount_def: our booking against the sheet's Total Adjusted Collection)
                    + (later_payments: always 0 now. Revenue is on the booking, so later payments change nothing in our number)

For a student present on both sides, with R(amount, dates) = this year's revenue of that amount spread over those dates:
    ours  = R(our booking,           our dates)
    sheet = R(sheet's Adj. Collection, sheet dates)
    date            = R(our booking, our dates)   - R(our booking, sheet dates)
    amount_def      = R(our booking, sheet dates) - R(sheet's Adj. Collection, sheet dates)
The two lines telescope, so they sum to ours - sheet with nothing left over (checked in code).
The sheet's collection stops on the day it was prepared; that day is found from the same column.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

import pandas as pd

import config
import revenue_engine as eng


def _spread(amount: float, sm: str, em: str, fy: set) -> dict:
    n = max(1, eng.months_inclusive(sm, em))        # same rule as the engine: bad dates -> whole amount in the start month
    per, si = amount / n, eng.ym_idx(sm)
    out = {}
    for k in range(n):
        m = eng.idx_ym(si + k)
        if m in fy:
            out[m] = per
    return out


def _sub(a: dict, b: dict) -> dict:
    return {m: a.get(m, 0.0) - b.get(m, 0.0) for m in set(a) | set(b)}


def build_gaps(conn, add_dq, log) -> bool:
    for t in ("ref_iter_student", "cmp_student", "cmp_effect_monthly"):
        conn.execute(f"DELETE FROM {t}")
    path = config.OPTIONAL_SOURCES["iteration"]
    if not path.exists():
        log("Gap Check: iteration sheet not present, skipped")
        return False
    df = pd.read_excel(path, header=4).iloc[:, 1:]
    df.columns = [" ".join(str(c).split()) for c in df.columns]
    need = ["Reg_no", "Center (Trim)", "Scheme", "Bill Amount", "Final Total Payable (w/o GST)", "Student Status",
            "Total Adjusted Collection", "Effective Start Date", "Effective End Date"]
    miss = [c for c in need if c not in df.columns]
    if miss:
        log(f"WARNING Gap Check skipped: iteration sheet is missing columns {miss}")
        return False
    df = df.dropna(subset=["Reg_no"]).drop_duplicates("Reg_no", keep="last")
    mp = dict(conn.execute("SELECT branch_name, branch_id FROM dim_branch"))
    df["bid"] = df["Center (Trim)"].astype(str).str.strip().map(mp)
    df["sd"] = pd.to_datetime(df["Effective Start Date"], errors="coerce").dt.strftime("%Y-%m-%d")
    df["ed"] = pd.to_datetime(df["Effective End Date"], errors="coerce").dt.strftime("%Y-%m-%d")
    bad = df.bid.isna() | df.sd.isna() | df.ed.isna()
    if bad.any():
        add_dq(conn, "iteration sheet", "ROW_NOT_USABLE", "unknown branch name or unreadable effective dates; row left out of the comparison", int(bad.sum()))
    df = df[~bad].copy()
    def opt(name, num=False, integer=False):
        if name not in df.columns:
            return [None] * len(df)
        s_ = pd.to_numeric(df[name], errors="coerce") if num else df[name].astype(str)
        out = []
        for v in s_:
            if num:
                out.append(None if pd.isna(v) else (int(v) if integer else float(v)))
            else:
                out.append(None if v in ("nan", "NaT", "None") else v)
        return out
    rows = list(zip(df["Reg_no"].astype(int), df["bid"].astype(int), df["Scheme"].astype(str), df["Bill Amount"].astype(float),
                    df["Final Total Payable (w/o GST)"].astype(float), df["Student Status"].astype(str),
                    df["Total Adjusted Collection"].astype(float), df["sd"], df["ed"], df["sd"].str[:7], df["ed"].str[:7],
                    opt("Student"), opt("Archive Date"), opt("Refund (FY 2026-27)", True), opt("Days Diff", True, True), opt("No of Months", True, True)))
    rows = [tuple(x.item() if hasattr(x, "item") else x for x in r) for r in rows]
    conn.executemany("INSERT INTO ref_iter_student VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)

    # ---- the date up to which the sheet's Adjusted Collection reflects payments (the sheet is a snapshot)
    snap = conn.execute("SELECT value FROM meta WHERE key='snapshot_date'").fetchone()[0]
    pay = pd.read_sql("SELECT reg_no, receipt_date d, net_paid_amount n FROM fact_collection", conn)
    sheet_amt = dict(zip(df.Reg_no.astype(int), df["Total Adjusted Collection"].astype(float)))
    sdf = pd.DataFrame({"reg_no": list(sheet_amt), "a": list(sheet_amt.values())})
    best = (-1.0, None)
    snap_d = dt.date.fromisoformat(snap)
    for k in range(0, 91):
        cut = (snap_d - dt.timedelta(days=k)).isoformat()
        tot = pay[pay.d <= cut].groupby("reg_no").n.sum()
        x = sdf.join(tot.rename("t"), on="reg_no").fillna({"t": 0})
        rate = float(((x.a - x.t / config.GST_DIVISOR).abs() <= 5).mean())
        if rate > best[0]:
            best = (rate, cut)
    match_rate, cut = best
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('iter_asof', ?)", (cut,))
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('iter_match_rate', ?)", (f"{match_rate:.4f}",))

    # ---- our side: every student whose batch runs into this financial year
    ours = {r[0]: r for r in conn.execute(
        """SELECT reg_no, branch_id, eff_start, eff_end, start_month, end_month, payable_ex_gst FROM fact_student
           WHERE MAX(start_month, end_month) >= ? AND start_month <= ?""", (config.FY_START, config.FY_END))}   # any revenue this year
    sheet = {r[0]: r for r in conn.execute("SELECT reg_no, branch_id, eff_start, eff_end, start_month, end_month, adj_collection, payable_ex_gst FROM ref_iter_student")}
    in_dump = {r[0]: r for r in conn.execute("SELECT reg_no, joining_date FROM fact_enrollment")}
    ended_before = {r[0] for r in conn.execute("SELECT reg_no FROM fact_student WHERE MAX(start_month, end_month) < ?", (config.FY_START,))}
    fy = set(eng.fy_months())
    eff = defaultdict(float)                     # (effect, branch, month) -> amount
    st_rows = []
    days = lambda a, b: (dt.date.fromisoformat(a) - dt.date.fromisoformat(b)).days

    for reg in sorted(set(ours) | set(sheet)):
        o, s = ours.get(reg), sheet.get(reg)
        if o and s:
            kind, reason = "both", None
            a_all = round(float(o[6]), 2)               # = the booking without GST, exactly as in the revenue base
            a_cut = a_all                               # kept for the table layout: later payments no longer matter
            R1 = _spread(a_all, o[4], o[5], fy)
            R2 = _spread(a_all, s[4], s[5], fy)
            R4 = _spread(float(s[6]), s[4], s[5], fy)
            R4b = _spread(float(s[7]), s[4], s[5], fy)          # the same student on the Iteration sheet's other amount: Final Total Payable
            R2a = _spread(a_all, s[4], o[5], fy)                # the Iteration start with our end: splits the date gap into start and end
            parts = {"date": _sub(R1, R2), "later_payments": {}, "amount_def": _sub(R2, R4), "amount_def_fp": _sub(R2, R4b),
                     "date_start": _sub(R1, R2a), "date_end": _sub(R2a, R2)}
            bid = o[1]
            for m, v in R1.items(): eff[("ours", bid, m)] += v
            for m, v in R4.items(): eff[("sheet", bid, m)] += v
            for m, v in R4b.items(): eff[("sheet_fp", bid, m)] += v
            for name, d in parts.items():
                for m, v in d.items(): eff[(name, bid, m)] += v
            sd, ed = days(o[2], s[2]), days(o[3], s[3])
            st_rows.append((reg, bid, kind, reason, o[2], s[2], o[3], s[3], sd, ed, int(o[4] != s[4]), int(o[5] != s[5]),
                            a_all, a_cut, float(s[6]), sum(R1.values()), sum(R4.values()),
                            sum(parts["date"].values()), sum(parts["later_payments"].values()), sum(parts["amount_def"].values())))
        elif o:
            a_all = round(float(o[6]), 2)               # = the booking without GST, exactly as in the revenue base
            a_cut = a_all
            R1 = _spread(a_all, o[4], o[5], fy)
            if o[2] > cut:
                reason = f"admitted after the Iteration data was prepared (after {cut})"
            elif a_all <= 0:
                reason = "the booking amount is zero"
            else:
                reason = "admitted before the Iteration data was prepared, but missing from it"
            bid = o[1]
            code = "oo_after" if o[2] > cut else ("oo_zero" if a_all <= 0 else "oo_missing")
            for m, v in R1.items():
                eff[("ours", bid, m)] += v
                eff[("only_ours", bid, m)] += v
                eff[(code, bid, m)] += v
            st_rows.append((reg, bid, "only_ours", reason, o[2], None, o[3], None, None, None, None, None,
                            a_all, a_cut, None, sum(R1.values()), None, None, None, None))
        else:
            R4 = _spread(s[6], s[4], s[5], fy)
            R4b = _spread(float(s[7]), s[4], s[5], fy)
            if reg not in in_dump:
                reason = "not in the GEW enrollment data"
            elif reg in ended_before:
                reason = "in the GEW data, but its batch ended before this year (the GEW end date is earlier)"
            else:
                reason = "in the GEW data, but its start date is after this year"
            bid = s[1]
            code = "os_notin" if reg not in in_dump else ("os_ended" if reg in ended_before else "os_later")
            for m, v in R4.items():
                eff[("sheet", bid, m)] += v
                eff[("only_sheet", bid, m)] -= v
                eff[(code, bid, m)] -= v
            for m, v in R4b.items():
                eff[("sheet_fp", bid, m)] += v
                eff[("only_sheet_fp", bid, m)] -= v
                eff[(code + "_fp", bid, m)] -= v
            st_rows.append((reg, bid, "only_sheet", reason, None, s[2], None, s[3], None, None, None, None,
                            None, None, s[6], None, sum(R4.values()), None, None, None))

    # the causes must add up exactly to the gap, otherwise something is wrong: stop loudly instead of showing wrong numbers
    tot = lambda e: sum(v for (n, _, _), v in eff.items() if n == e)
    gap = tot("ours") - tot("sheet")
    explained = tot("only_ours") + tot("only_sheet") + tot("date") + tot("later_payments") + tot("amount_def")
    if abs(gap - explained) > 1.0:
        raise SystemExit(f"Gap Check internal error: causes add up to {explained:,.0f} but the gap is {gap:,.0f}")

    gap_fp = tot("ours") - tot("sheet_fp")
    explained_fp = tot("only_ours") + tot("only_sheet_fp") + tot("date") + tot("later_payments") + tot("amount_def_fp")
    if abs(gap_fp - explained_fp) > 1.0:
        raise SystemExit(f"Gap Check internal error (Final Total Payable): causes add up to {explained_fp:,.0f} but the gap is {gap_fp:,.0f}")
    # the finer causes must also add up exactly to the coarse ones
    for a_, parts_ in (("only_sheet", ("os_notin", "os_ended", "os_later")), ("only_sheet_fp", ("os_notin_fp", "os_ended_fp", "os_later_fp")),
                       ("only_ours", ("oo_after", "oo_missing", "oo_zero")), ("date", ("date_start", "date_end"))):
        if abs(tot(a_) - sum(tot(p_) for p_ in parts_)) > 1.0:
            raise SystemExit(f"Gap Check internal error: {a_} is {tot(a_):,.0f} but its parts add up to {sum(tot(p_) for p_ in parts_):,.0f}")
    conn.executemany("INSERT INTO cmp_student VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", st_rows)
    conn.executemany("INSERT INTO cmp_effect_monthly VALUES (?,?,?,?)", [(n, b, m, round(v, 4)) for (n, b, m), v in eff.items()])
    both_rows = [r for r in st_rows if r[2] == "both"]
    same_fee = sum(1 for r in both_rows if abs(r[12] - r[14]) <= 5)
    book_rate = same_fee / len(both_rows) if both_rows else 0.0
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('iter_booking_match_rate', ?)", (f"{book_rate:.4f}",))
    kinds = pd.Series([r[2] for r in st_rows]).value_counts().to_dict()
    add_dq(conn, "gap check", "GAP_CHECK_BUILT",
           f"Iteration data vs GEW data: both {kinds.get('both', 0)}, only in GEW data {kinds.get('only_ours', 0)}, only in Iteration data {kinds.get('only_sheet', 0)}; "
           f"this year's revenue gap (GEW - Iteration) = {gap / 1e7:+.2f} Cr; the amount is the same (within Rs 5) for {book_rate:.1%} of the students found on both sides: our booking against the sheet's Total Adjusted Collection",
           len(st_rows), gap)
    log(f"gap check, Iteration on its Final Total Payable instead: Iteration {tot('sheet_fp') / 1e7:.2f} Cr (gap {gap_fp / 1e7:+.2f})")
    log(f"gap check: {kinds}; this year's revenue GEW {tot('ours') / 1e7:.2f} Cr vs Iteration {tot('sheet') / 1e7:.2f} Cr (gap {gap / 1e7:+.2f}); sheet frozen at {cut} ({match_rate:.1%}); same amount on both sides for {book_rate:.1%}")
    return True

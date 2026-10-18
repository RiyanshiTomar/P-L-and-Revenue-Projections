"""The gap register: every known difference between the GEW data, the Iteration data and Finance's P&L, in plain words, with the numbers and one
real student as proof. Rebuilt on every build (so it is always up to date) and one line per gap per day is added to gap_history.

GEW vs Iteration  (G-I1 ... G-I8): student by student. The effects add up EXACTLY to the total gap, and the build stops if they do not.
Finance P&L       (G-F1 ... G-F4): month by month (Finance has no student list): revenue, students admitted, collection.
"""
from __future__ import annotations

import datetime as dt
import json
from collections import defaultdict

import config
import gap_evidence as ev
import revenue_engine as eng


def inr(x) -> str:
    """Rupees the Indian way: 12,34,567"""
    x = int(round(float(x or 0)))
    s, sign = str(abs(x)), "-" if x < 0 else ""
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    return f"{sign}₹{s}"


def cr(x) -> str:
    return f"₹{x / 1e7:,.2f} Cr"


def dlabel(d) -> str:
    return dt.date.fromisoformat(str(d)[:10]).strftime("%d %b %Y").lstrip("0") if d else "—"


def mname(m: str) -> str:
    return dt.date(int(m[:4]), int(m[5:7]), 1).strftime("%b-%y")


def build_gap_report(conn, log) -> None:
    conn.execute("DELETE FROM gap_register")
    meta = dict(conn.execute("SELECT key, value FROM meta"))
    cut = meta.get("iter_asof")
    if not cut or not conn.execute("SELECT 1 FROM cmp_student LIMIT 1").fetchone():
        log("gap register: the Iteration comparison is not there, skipped")
        return
    today = dt.date.today().isoformat()
    fy = eng.fy_months()
    win_it = [m for m in fy if m <= cut[:7]]
    wlabel = f"{mname(win_it[0])} to {mname(win_it[-1])}" if win_it else ""
    E = defaultdict(lambda: defaultdict(float))
    for e, m, v in conn.execute("SELECT effect, month, SUM(amount) FROM cmp_effect_monthly GROUP BY 1, 2"):
        E[e][m] += v
    F = lambda e: sum(E[e].values()) if e else None
    W = lambda e: sum(E[e].get(m, 0.0) for m in win_it) if e else None
    rows = []

    def add(gid, grp, sort, title, plain, n, unit, eff, owner, proof_reg=None, proof_text=None, values=None, evidence=None, kfy=None, kwin=None, fpfy=None, fpwin=None):
        k, fp = (eff if eff else (None, None))
        rows.append((gid, grp, sort, title, plain, n, unit, F(k) if kfy is None else kfy, W(k) if kwin is None else kwin,
                     F(fp) if fpfy is None else fpfy, W(fp) if fpwin is None else fpwin, wlabel, owner, proof_reg, proof_text,
                     json.dumps(values) if values else None, evidence))

    q1 = lambda sql, *a: conn.execute(sql, a).fetchone()
    p = ev._params(conn)

    # ------------------------------------------------------------ GEW vs Iteration
    def T(gid):
        return ev.typical(conn, gid)

    def payments(reg):
        r = q1("SELECT COUNT(*), COALESCE(SUM(net_paid_amount), 0) FROM fact_collection WHERE reg_no = ?", reg)
        return r[0], r[1]

    # I1: only in Iteration, not in GEW enrollment
    n = ev.count(conn, "G-I1")
    if n:
        t = T("G-I1")
        f = q1("""SELECT COUNT(DISTINCT c.reg_no), COALESCE(SUM(c.net_paid_amount), 0) FROM fact_collection c
                  WHERE c.reg_no IN (SELECT s.reg_no FROM cmp_student s WHERE s.kind = 'only_sheet' AND s.reason LIKE 'not in the GEW%')""")
        it = q1("""SELECT COALESCE(SUM(i.payable_ex_gst), 0), COALESCE(SUM(i.adj_collection), 0) FROM ref_iter_student i
                   WHERE i.reg_no IN (SELECT s.reg_no FROM cmp_student s WHERE s.kind = 'only_sheet' AND s.reason LIKE 'not in the GEW%')""")
        add("G-I1", "GEW vs Iteration", 10, "Students in the Iteration data, but not in the GEW enrollment data",
            "The Iteration sheet has these students and counts their fee as revenue. Their student number is not in the GEW enrollment sheet (Enroll Dump FY26 or FY27) at all, "
            "so the GEW data has no revenue for them. " + f"{f[0]:,} of the {n:,} do have payments in the GEW collection sheet, together only {inr(f[1])}, "
            f"while the Iteration sheet counts {inr(it[0])} of fee for them and shows {inr(it[1])} collected. So most of them seem to have paid only a token amount.",
            n, "students", ("os_notin", "os_notin_fp"), "Iteration team / Data team",
            t["reg"], f"Student {t['reg']} ({t['branch']}): the Iteration sheet counts {inr(t['i_fp'])} fee from {dlabel(t['i_start'])} to {dlabel(t['i_end'])} and shows {inr(t['i_k'])} collected. "
            f"This student number is not in the GEW enrollment sheet. The GEW collection sheet has {t['g_n']} payment(s) for it, {inr(t['g_t'])} in total.", evidence="G-I1")

    # I2: in GEW, batch ended before this year; Iteration has a later batch
    n = ev.count(conn, "G-I2")
    if n:
        t = T("G-I2")
        d = q1("""SELECT COUNT(*), SUM(julianday(i.eff_end) - julianday(e.batch_end) > 0), CAST(AVG(julianday(i.eff_end) - julianday(e.batch_end)) AS INTEGER),
                         SUM(i.eff_start >= ?) FROM cmp_student s JOIN ref_iter_student i ON i.reg_no = s.reg_no JOIN fact_enrollment e ON e.reg_no = s.reg_no
                  WHERE s.kind = 'only_sheet' AND s.reason LIKE 'in the GEW data, but its batch ended%'""", config.FY_START + "-01")
        add("G-I2", "GEW vs Iteration", 20, "Students in both, but the GEW batch ended before this year and the Iteration sheet gives a later batch",
            "The student is in both sheets. In the GEW enrollment sheet the batch ended before this financial year, so the GEW data gives no revenue this year. "
            f"The Iteration sheet gives the same student a later start and end date: its end date is later by {d[2]} days on average, and for {d[3]:,} of the {n:,} students its start date is in this financial year. "
            "It looks like these students were moved to a new batch, and the new batch is in the Iteration sheet but not in the GEW enrollment sheet.",
            n, "students", ("os_ended", "os_ended_fp"), "Iteration team / Data team",
            t["reg"], f"Student {t['reg']} ({t['branch']}): GEW says admitted {dlabel(t['g_adm'])}, batch start {dlabel(t['g_bs'])}, batch end {dlabel(t['g_end'])} (booking {inr(t['g_book'])}). "
            f"The Iteration sheet gives {dlabel(t['i_start'])} to {dlabel(t['i_end'])} (fee {inr(t['i_fp'])}, collected {inr(t['i_k'])}).", evidence="G-I2")
    n = ev.count(conn, "G-I2b")
    if n:
        t = T("G-I2b")
        add("G-I2b", "GEW vs Iteration", 21, "Students in both, but the GEW start date is after this year", "The student is in both sheets, but in the GEW data the batch starts after this financial year.",
            n, "students", ("os_later", "os_later_fp"), "Iteration team / Data team", t["reg"],
            f"Student {t['reg']} ({t['branch']}): GEW {dlabel(t['g_adm'])} to {dlabel(t['g_end'])}; Iteration {dlabel(t['i_start'])} to {dlabel(t['i_end'])}.", evidence="G-I2b")

    # I3: only in GEW, admitted before the cut-off
    n = ev.count(conn, "G-I3")
    if n:
        t = T("G-I3")
        add("G-I3", "GEW vs Iteration", 30, f"Students in the GEW data admitted before {dlabel(cut)}, but missing from the Iteration data",
            f"These students were already admitted when the Iteration sheet was prepared ({dlabel(cut)}), yet they are not in it. Their revenue is in the GEW data only.",
            n, "students", ("oo_missing", "oo_missing"), "Iteration team", t["reg"],
            f"Student {t['reg']} ({t['branch']}): admitted {dlabel(t['g_adm'])}, batch ends {dlabel(t['g_end'])}, booking {inr(t['g_book'])}. Not in the Iteration sheet.", evidence="G-I3")
    n = ev.count(conn, "G-I3b")
    if n:
        t = T("G-I3b")
        add("G-I3b", "GEW vs Iteration", 31, "Students in the GEW data with a booking of 0, not in the Iteration data", "The booking is 0 in the GEW sheet, so there is no revenue.",
            n, "students", ("oo_zero", "oo_zero"), "Data team", t["reg"], f"Student {t['reg']} ({t['branch']}): admitted {dlabel(t['g_adm'])}, booking {inr(t['g_book'])}.", evidence="G-I3b")

    # I4: timing
    n = ev.count(conn, "G-I4")
    if n:
        t = T("G-I4")
        add("G-I4", "GEW vs Iteration", 40, f"Students admitted after {dlabel(cut)} (timing)",
            f"The Iteration sheet was prepared on {dlabel(cut)}, so students admitted later cannot be in it. They are in the GEW data. This is not an error: it goes away when the Iteration sheet is refreshed.",
            n, "students", ("oo_after", "oo_after"), "Nobody: timing", t["reg"],
            f"Student {t['reg']} ({t['branch']}): admitted {dlabel(t['g_adm'])}, booking {inr(t['g_book'])}, after the Iteration sheet was prepared.", evidence="G-I4")

    # I5: start date differs
    n = ev.count(conn, "G-I5")
    if n:
        t = T("G-I5")
        d = q1(f"""WITH rb AS ({ev._rb(conn)}) SELECT COUNT(*), SUM(s.start_sheet = rb.batch_start_date) FROM cmp_student s LEFT JOIN rb ON rb.reg_no = s.reg_no
                   WHERE s.kind = 'both' AND s.start_month_differs = 1""")
        later = q1("SELECT SUM(start_diff_days < 0) FROM cmp_student WHERE kind = 'both' AND start_month_differs = 1")[0] or 0
        add("G-I5", "GEW vs Iteration", 50, "Start date in a different month (students in both)",
            "The GEW data starts a student's revenue on the admission date (new_receipt_date). The Iteration sheet uses its own Effective Start Date. "
            f"For {d[1] or 0:,} of these {n:,} students the Iteration start date is exactly the batch start date, and for {later:,} of them it is later than the GEW admission date. "
            "So the Iteration sheet usually starts revenue later and spreads the same fee over fewer months. Which date is right? That has to be decided with the P&L team.",
            n, "students", ("date_start", "date_start"), "P&L / Iteration team + Finance",
            t["reg"], f"Student {t['reg']} ({t['branch']}): GEW starts on the admission date {dlabel(t['g_adm'])} (the batch started {dlabel(t['g_bs'])}). "
            f"The Iteration sheet starts on {dlabel(t['i_start'])}. Both end around {dlabel(t['g_end'])} / {dlabel(t['i_end'])}.", evidence="G-I5")

    # I6: end date differs
    n = ev.count(conn, "G-I6")
    if n:
        t = T("G-I6")
        d = q1("SELECT SUM(end_diff_days < 0), SUM(end_diff_days > 0) FROM cmp_student WHERE kind = 'both' AND end_month_differs = 1")
        add("G-I6", "GEW vs Iteration", 60, "End date in a different month (students in both)",
            "The GEW data ends a student's revenue on batch_end_date. The Iteration sheet uses its own Effective End Date. "
            f"For {d[0] or 0:,} of these {n:,} students the Iteration end date is later than the GEW date, for {d[1] or 0:,} it is earlier. A different end month changes how many months the fee is spread over.",
            n, "students", ("date_end", "date_end"), "P&L / Iteration team + Finance",
            t["reg"], f"Student {t['reg']} ({t['branch']}): GEW batch ends {dlabel(t['g_end'])}, the Iteration sheet ends {dlabel(t['i_end'])} ({abs(t['days'] or 0)} days apart).", evidence="G-I6")

    # I7: booking against collection
    n = ev.count(conn, "G-I7")
    if n:
        t = T("G-I7")
        d = q1("SELECT SUM(amt_ours_all > amt_sheet), SUM(amt_ours_all < amt_sheet) FROM cmp_student WHERE kind = 'both' AND ABS(amt_ours_all - amt_sheet) > 5")
        add("G-I7", "GEW vs Iteration", 70, "Amount: GEW booking against the Iteration 'Total Adjusted Collection'",
            "The GEW data counts the full booking (the fee the student has to pay, without GST). The Iteration column 'Total Adjusted Collection' is only what had been collected until "
            f"{dlabel(cut)}. For {d[0] or 0:,} of these students it is lower than the booking, because they had not paid in full yet. It is higher for {d[1] or 0:,}. "
            "This is a difference of method (booking against collection), not a mistake in a sheet.",
            n, "students", ("amount_def", None), "Decision: booking or collection?",
            t["reg"], f"Student {t['reg']} ({t['branch']}): GEW booking {inr(t['g_book'])} (without GST); the Iteration sheet shows {inr(t['i_k'])} collected. "
            f"The GEW collection sheet has {inr(t['g_col'])} (without GST) paid up to {dlabel(cut)}, which is what the Iteration sheet shows.", evidence="G-I7")

    # I8: fee against fee
    n = ev.count(conn, "G-I8")
    if n:
        t = T("G-I8")
        d = q1("SELECT SUM(s.amt_ours_all < i.payable_ex_gst), SUM(s.amt_ours_all > i.payable_ex_gst) FROM cmp_student s JOIN ref_iter_student i ON i.reg_no = s.reg_no WHERE s.kind = 'both' AND ABS(s.amt_ours_all - i.payable_ex_gst) > 5")
        add("G-I8", "GEW vs Iteration", 80, "Fee: GEW booking against the Iteration 'Final Total Payable'",
            "Both are the fee the student has to pay, without GST. For these students the two sheets show a different fee (more than ₹5 apart). "
            f"The Iteration fee is higher for {d[0] or 0:,} students and lower for {d[1] or 0:,}. It does not match any single column of the GEW enrollment sheet, so it looks like the Iteration team calculates it in their own way.",
            n, "students", (None, "amount_def_fp"), "Iteration team",
            t["reg"], f"Student {t['reg']} ({t['branch']}): GEW booking {inr(t['g_book_gst'])} with GST = {inr(t['g_book'])} without GST. The Iteration sheet shows a fee of {inr(t['i_fp'])} (bill amount {inr(t['i_bill'])}).", evidence="G-I8")

    # the total, and the proof that the gaps add up
    ours, sk, sfp = F("ours"), F("sheet"), F("sheet_fp")
    gap_k, gap_fp = ours - sk, ours - sfp
    k_sum = sum(r[7] or 0 for r in rows if r[1] == "GEW vs Iteration")
    fp_sum = sum((r[9] or 0) for r in rows if r[1] == "GEW vs Iteration")
    if abs(k_sum - gap_k) > 1.0 or abs(fp_sum - gap_fp) > 1.0:
        raise SystemExit(f"Gap register internal error: the gaps add up to {k_sum:,.0f} / {fp_sum:,.0f} but the total gaps are {gap_k:,.0f} / {gap_fp:,.0f}")
    rows.append(("TOTAL", "total", 90, "Total gap: GEW revenue minus Iteration revenue (this financial year)",
                 f"GEW {cr(ours)}; Iteration {cr(sk)} counted on Total Adjusted Collection, or {cr(sfp)} counted on Final Total Payable. The gaps above add up to this exactly.",
                 None, None, gap_k, W("ours") - W("sheet"), gap_fp, W("ours") - W("sheet_fp"), wlabel, "", None, None, None, None))

    # ------------------------------------------------------------ Finance P&L, month by month
    fin = {}
    for m, metric, v in conn.execute("SELECT month, metric, value FROM ref_pl_monthly WHERE month BETWEEN ? AND ? AND metric IN ('revenue', 'enrollment', 'net_collection')", (config.FY_START, config.FY_END)):
        fin.setdefault(metric, {})[m] = v
    fm = sorted(fin.get("revenue", {}))
    if fm:
        gew_rev = dict(conn.execute("SELECT month, SUM(amount) FROM fact_revenue_monthly GROUP BY 1"))
        gew_enr = dict(conn.execute("SELECT join_month, COUNT(*) FROM fact_enrollment GROUP BY 1"))
        gew_col = dict(conn.execute("SELECT month, SUM(net_paid_amount) FROM fact_collection GROUP BY 1"))
        flabel = f"{mname(fm[0])} to {mname(fm[-1])}"
        lines = lambda a, b, d: ", ".join(f"{mname(m)}: {a[i]:,.2f} against {b[i]:,.2f} ({d(a[i], b[i])})" for i, m in enumerate(fm))
        sg = lambda a, b: f"{a - b:+,.2f}"

        def addf(gid, sort, title, plain, unit, win_k, win_fp, owner, proof, vals):
            rows.append((gid, "Finance P&L", sort, title, plain, len(fm), unit, None, win_k, None, win_fp, flabel, owner, None, proof, json.dumps(vals), "finance"))

        # F1 / F2 revenue
        g = [gew_rev.get(m, 0) / 1e7 for m in fm]
        k = [E["sheet"].get(m, 0) / 1e7 for m in fm]
        fpv = [E["sheet_fp"].get(m, 0) / 1e7 for m in fm]
        f = [fin["revenue"][m] for m in fm]
        vals = {"months": fm, "gew": g, "it_k": k, "it_fp": fpv, "fin": f, "unit": "Cr"}
        worst = max(range(len(fm)), key=lambda i: abs(g[i] - f[i]))
        addf("G-F1", 100, "Revenue by month: GEW data against Finance P&L",
             "Finance's monthly P&L has a 'Revenue' line. The GEW data counts the booking spread over the months of each batch. Finance's collection and student counts are close to ours (see below), "
             "so the difference seems to come from how finance works out its Revenue line. That rule is not in the sheet and has to be confirmed with finance.",
             "months", (sum(g) - sum(f)) * 1e7, None, "Finance",
             f"{mname(fm[worst])}: GEW {g[worst]:.2f} Cr, Finance {f[worst]:.2f} Cr. Total {flabel}: GEW {sum(g):.2f} Cr, Finance {sum(f):.2f} Cr.", vals)
        addf("G-F2", 110, "Revenue by month: Iteration data against Finance P&L",
             "The Iteration sheet is not the same as Finance's monthly P&L. Counted on Total Adjusted Collection it is lower than Finance, counted on Final Total Payable it is higher. "
             "Neither matches Finance month by month.", "months", (sum(k) - sum(f)) * 1e7, (sum(fpv) - sum(f)) * 1e7, "Finance / Iteration team",
             f"{flabel}: Iteration {sum(k):.2f} Cr (Total Adjusted Collection) or {sum(fpv):.2f} Cr (Final Total Payable), Finance {sum(f):.2f} Cr.", vals)
        # F3 students
        ge = [float(gew_enr.get(m, 0)) for m in fm]
        fe = [fin.get("enrollment", {}).get(m) for m in fm]
        if all(x is not None for x in fe):
            diffs = [ge[i] - fe[i] for i in range(len(fm))]
            worst = max(range(len(fm)), key=lambda i: abs(diffs[i]))
            addf("G-F3", 120, "Students admitted by month: GEW data against Finance P&L",
                 "Finance's 'Batch enrollment (#)' is the number of students admitted in the month. The GEW data counts students by new_receipt_date (admission date). The counts are close but not the same.",
                 "months", sum(diffs), None, "Finance / Data team",
                 f"{mname(fm[worst])}: GEW {ge[worst]:,.0f} students, Finance {fe[worst]:,.0f}. Total {flabel}: GEW {sum(ge):,.0f}, Finance {sum(fe):,.0f}.",
                 {"months": fm, "gew": ge, "it_k": [None] * len(fm), "it_fp": [None] * len(fm), "fin": fe, "unit": "students"})
        # F4 collection
        gc = [gew_col.get(m, 0) / config.GST_DIVISOR / 1e7 for m in fm]
        fc = [fin.get("net_collection", {}).get(m) for m in fm]
        if all(x is not None for x in fc):
            worst = max(range(len(fm)), key=lambda i: abs(gc[i] - fc[i]))
            addf("G-F4", 130, "Collection (without GST) by month: GEW payments against Finance P&L",
                 "Finance's 'Collections (Ex. GST)' against the payments in the GEW collection sheets divided by 1.18. These are close, which tells us the collection data is the same on both sides.",
                 "months", (sum(gc) - sum(fc)) * 1e7, None, "Finance / Data team",
                 f"{mname(fm[worst])}: GEW payments {gc[worst]:.2f} Cr (without GST), Finance {fc[worst]:.2f} Cr. Total {flabel}: GEW {sum(gc):.2f} Cr, Finance {sum(fc):.2f} Cr.",
                 {"months": fm, "gew": gc, "it_k": [None] * len(fm), "it_fp": [None] * len(fm), "fin": fc, "unit": "Cr"})

    conn.executemany("INSERT INTO gap_register VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)

    # ------------------------------------------------------------ history: one line per gap per day, kept for ever
    import sqlite3
    try:
        if config.DB_PATH.exists():
            conn.execute("ATTACH DATABASE ? AS oldh", (str(config.DB_PATH),))
            if conn.execute("SELECT 1 FROM oldh.sqlite_master WHERE name = 'gap_history'").fetchone():
                conn.execute("INSERT OR IGNORE INTO gap_history SELECT * FROM oldh.gap_history WHERE load_date < ?", (today,))
            conn.commit()
            conn.execute("DETACH DATABASE oldh")
    except sqlite3.Error as e:
        log(f"WARNING gap history of the earlier days could not be carried over: {e}")
        try:
            conn.execute("DETACH DATABASE oldh")
        except sqlite3.Error:
            pass
    conn.execute("DELETE FROM gap_history WHERE load_date = ?", (today,))
    conn.executemany("INSERT INTO gap_history VALUES (?,?,?,?,?,?,?)", [(today, r[0], r[5], r[7], r[8], r[9], r[10]) for r in rows])
    log(f"gap register: {len(rows)} lines; GEW minus Iteration this year {gap_k / 1e7:+.2f} Cr (Total Adjusted Collection) / {gap_fp / 1e7:+.2f} Cr (Final Total Payable), all explained")

"""Builds docs/GEW_Backend_and_Database_Report.pdf : how the database is built, what is in it, and how the whole backend works.

    python docs/make_backend_report.py

Numbers (row counts, health checks, verification result) are read live from data/gew.db and verify.py, so re-running it keeps it true.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import pathlib
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import config  # noqa: E402

OUT_HTML = ROOT / "docs" / "GEW_Backend_and_Database_Report.html"
OUT_PDF = ROOT / "docs" / "GEW_Backend_and_Database_Report.pdf"
e = html.escape
c = sqlite3.connect(config.DB_PATH)
q = lambda sql, *a: c.execute(sql, a).fetchall()
q1 = lambda sql, *a: c.execute(sql, a).fetchone()[0]
n = lambda x: f"{x:,.0f}"
cr = lambda v: f"₹ {v / 1e7:,.2f} Cr"
day = lambda iso: dt.date.fromisoformat(iso[:10]).strftime("%d %b %Y")

# ------------------------------------------------------------------ verification (run now, never quoted from memory)
vr = subprocess.run([sys.executable, "-I", str(ROOT / "verify.py")], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1200)
m_ = re.search(r"(\d+) of (\d+) checks passed", vr.stdout)
v_pass, v_all = (int(m_.group(1)), int(m_.group(2))) if m_ else (0, 0)
v_ok = bool(m_) and v_pass == v_all and vr.returncode == 0
print("verify.py:", f"{v_pass} of {v_all}" if m_ else "could not run")

meta = dict(q("SELECT key, value FROM meta"))
snap = meta["snapshot_date"]
size_mb = os.path.getsize(config.DB_PATH) / 1e6
try:
    st = json.loads((ROOT / "data" / "last_refresh.json").read_text(encoding="utf-8"))
    last_refresh = f"{dt.datetime.fromisoformat(st['finished']):%d %b %Y, %I:%M %p} ({'OK' if st['ok'] else 'PROBLEM'}, {st.get('minutes', '?')} min)"
except Exception:
    last_refresh = "—"

# ------------------------------------------------------------------ live health checks
iso = re.compile(r"^\d{4}-\d{2}-\d{2}$")
orphan_pay = q1("SELECT COUNT(*) FROM fact_collection WHERE reg_no NOT IN (SELECT reg_no FROM fact_enrollment)")
orphan_amt = q1("SELECT COALESCE(SUM(net_paid_amount),0) FROM fact_collection WHERE reg_no NOT IN (SELECT reg_no FROM fact_enrollment)")
dup_groups = q1("SELECT COUNT(*) FROM (SELECT 1 FROM fact_collection GROUP BY reg_no, receipt_date, net_paid_amount, payment_mode HAVING COUNT(*) > 1)")
dup_amt = q1("SELECT COALESCE(SUM(net_paid_amount * (k - 1)),0) FROM (SELECT net_paid_amount, COUNT(*) k FROM fact_collection GROUP BY reg_no, receipt_date, net_paid_amount, payment_mode HAVING k > 1)")
total_paid = q1("SELECT SUM(net_paid_amount) FROM fact_collection")
bad_dates = sum(1 for (d,) in q("SELECT joining_date FROM fact_enrollment") if not iso.match(d)) + sum(1 for (d,) in q("SELECT receipt_date FROM fact_collection") if not iso.match(d))
conserve_a, conserve_b = q("""SELECT SUM(s.payable_ex_gst), (SELECT SUM(r.amount) FROM fact_revenue_monthly r WHERE r.reg_no IN
      (SELECT reg_no FROM fact_student WHERE start_month>=? AND end_month<=? AND end_month>=start_month))
      FROM fact_student s WHERE s.start_month>=? AND s.end_month<=? AND s.end_month>=s.start_month""", config.FY_START, config.FY_END, config.FY_START, config.FY_END)[0]
c.execute("PRAGMA foreign_keys=ON")
checks = [
    ("The database file is not damaged (SQLite integrity check)", "OK" if q("PRAGMA integrity_check")[0][0] == "ok" else "PROBLEM", ""),
    ("Every branch / student link points to something that exists (foreign keys)", "OK" if not q("PRAGMA foreign_key_check") else "PROBLEM", ""),
    ("One revenue-base row per admitted student", "OK" if q1("SELECT COUNT(*) FROM fact_student") == q1("SELECT COUNT(*) FROM fact_enrollment") else "PROBLEM",
     f"{n(q1('SELECT COUNT(*) FROM fact_student'))} students = {n(q1('SELECT COUNT(*) FROM fact_enrollment'))} admissions"),
    ("No revenue row without a student", "OK" if q1("SELECT COUNT(*) FROM fact_revenue_monthly WHERE reg_no NOT IN (SELECT reg_no FROM fact_student)") == 0 else "PROBLEM", ""),
    ("All dates are real dates in one format (YYYY-MM-DD)", "OK" if bad_dates == 0 else "PROBLEM", f"{bad_dates} bad"),
    ("No negative student amounts, revenue amounts or payments",
     "OK" if q1("SELECT COUNT(*) FROM fact_student WHERE payable_ex_gst<0") + q1("SELECT COUNT(*) FROM fact_revenue_monthly WHERE amount<0") + q1("SELECT COUNT(*) FROM fact_collection WHERE net_paid_amount<0") == 0 else "PROBLEM", ""),
    ("Money is conserved: for students whose whole batch is inside this year, revenue rows add up to their amount", "OK" if abs(conserve_a - conserve_b) < 5 else "PROBLEM",
     f"{cr(conserve_a)} = {cr(conserve_b)}"),
    ("Every branch has students", "OK" if not q("SELECT 1 FROM dim_branch WHERE branch_id NOT IN (SELECT DISTINCT branch_id FROM fact_student)") else "NOTE", ""),
    ("Payments whose student number is not in the enrollment data", "NOTE" if orphan_pay else "OK",
     f"{n(orphan_pay)} payments, {cr(orphan_amt)} ({orphan_amt / total_paid * 100:.2f}% of all money). Revenue is counted on the booking, so they change no revenue number; they only matter for collection."),
    ("Payments that look duplicated (same student, date, amount and mode)", "NOTE" if dup_groups else "OK",
     f"{n(dup_groups)} groups, {cr(dup_amt)} if all were real duplicates. They are counted exactly as in the sheet; they may also be genuine part-payments on the same day."),
    ("Students whose batch end month is before the start month", "NOTE", f"{n(q1('SELECT COUNT(*) FROM fact_student WHERE end_month<start_month'))} students. Kept; the whole amount is counted in the start month."),
]
health_rows = "".join(f"<tr><td>{e(a)}</td><td class='{'ok' if b == 'OK' else 'warn'}'>{b}</td><td>{e(d)}</td></tr>" for a, b, d in checks)

# ------------------------------------------------------------------ tables
GROUPS = [
    ("Lists and plan", [
        ("dim_branch", "The 27 branches, their cohort, short name, and whether they are shown.", "Typed once in config.py (branch → cohort); short names from the 'Enroll Target' tab.", "branch_id, branch_name, cohort_id, is_active"),
        ("aop_version", "The annual plans: AOP1 (read from the sheet every build) and any plan uploaded from the portal.", "AOP1 from tab Target_Long; uploaded plans are copied over from the previous database.", "aop_id, name, source, created_at"),
        ("fact_target", "The plan itself: students planned and fee per student, for every branch and month.", "Tab Target_Long (324 rows) or an uploaded CSV.", "aop_id, branch_id, month, enroll_target, aov, booking_target"),
    ]),
    ("What really happened (straight from the sheets)", [
        ("fact_enrollment", "One row per admitted student.", "Tabs Enroll Dump FY26 + Enroll Dump Fy27 of the main workbook. Duplicate student numbers: the latest is kept.", "reg_no, branch_id, joining_date (= admission date, new_receipt_date), batch_end, total_payable, status"),
        ("fact_collection", "One row per payment received.", "Tab Collection Dump (main workbook) + tab Collection FY27 (second workbook).", "receipt_date, month, branch_id, reg_no, net_paid_amount, refund_amount, payment_mode"),
    ]),
    ("Calculated: the revenue", [
        ("fact_student_dump", "Per student, for the full history: start, end, number of months, amount.", "Calculated from fact_enrollment + fact_collection by history.py.", "reg_no, start_date, end_date, n_months, amount, dq_flag"),
        ("fact_revenue_dump_monthly", "Per student per month since Jan 2024: the revenue (used by Branch Trends).", "The amount spread equally over the student's months, by history.py.", "reg_no, branch_id, month, amount"),
        ("fact_student", "The revenue base used by the Revenue Projection page: same students with fee, status, start, end, amount.", "Copied from fact_student_dump and fact_enrollment (students_from_dumps).", "reg_no, branch_id, bill_amount, payable_ex_gst (the revenue amount), adj_collection (collected so far, information), eff_start, eff_end, n_months, dq_flag"),
        ("fact_revenue_monthly", "Per student per month for this financial year: the numbers the page adds up.", "Calculated by revenue_engine.rebuild_schedule from fact_student.", "reg_no, branch_id, month, amount"),
    ]),
    ("Gap Check (reference only, never drives a number)", [
        ("ref_iter_student", "The Iteration sheet (student by student, made by the P&L team), as given.", "complete.xlsx (Govt Offline Iteration), loaded by gaps.py.", "reg_no, branch_id, payable_ex_gst (its booking), adj_collection, eff_start, eff_end"),
        ("cmp_student", "One row per student compared: found on both sides / only GEW / only P&L, reason, dates, amounts, revenue effects.", "Calculated by gaps.py.", "reg_no, kind, reason, start_ours, start_sheet, end_ours, end_sheet, eff_date, eff_later, eff_amount"),
        ("cmp_effect_monthly", "The revenue gap split into causes, per branch and month.", "Calculated by gaps.py; the causes must add up exactly to the gap or the build stops.", "effect, branch_id, month, amount"),
        ("ref_pl_monthly", "Finance's monthly P&L numbers (revenue, students, collection, cost).", "Workbook with the cost tabs, tab 'Govt exam - Offline'.", "month, metric, value"),
        ("ref_pl_full", "Every line of the monthly P&L sheet, month by month, exactly as given (71 lines).", "Same workbook and tab, read by build_db.load_pl_full.", "tab, row_no, group_label, line_label, month, value"),
    ]),
    ("Gaps and data problems (all shown on the Gap Check page)", [
        ("dq_check", "One row per finding: what it is, how many rows, how much money, who can close it. Includes the 'information we still need' items.", "Written by dq_checks.py on every build.", "check_id, title, severity, n_rows, amount, detail, who"),
        ("dq_orphan_payment", "Payments whose student number is not in the enrollment data.", "fact_collection rows with no matching student. CSV download on the page.", "receipt_date, branch_id, reg_no, net_paid_amount, src"),
        ("dq_duplicate_payment", "Payments that look duplicated (same student, date, amount, mode).", "Grouped from fact_collection. CSV download on the page.", "reg_no, receipt_date, net_paid_amount, times_seen, extra_amount"),
        ("dq_student_bad_dates", "Students whose batch ends before they were admitted.", "From fact_student. CSV download on the page.", "reg_no, eff_start, eff_end, amount"),
        ("dq_flag_row", "The students or payments behind the other flags on the GEW data: negative balance, batch start after end, two admission dates that disagree, collected amount that does not agree, admission form not completed, refunds, payments long before admission.", "Built by dq_flags.py from the full copy of the enrollment sheet and the payments. CSV download on the page.", "flag_id, reg_no, branch_id, detail, amount, date1, date2"),
        ("gap_register", "Every known gap between the GEW data, the Iteration data and Finance's P&L: what it is in plain words, how many students, the effect on this year's revenue (on both Iteration amounts), and one real student as proof. The gaps add up to the total exactly, or the build stops.", "Built by gap_report.py on every build, from cmp_student, cmp_effect_monthly and the Finance P&L numbers.", "gap_id, title, plain, n, eff_fy_k, eff_win_k, eff_fy_fp, eff_win_fp, proof_text"),
        ("gap_history", "One line per gap per day, kept for ever: how every gap moves over time.", "Copied from the previous database and added to at every build.", "load_date, gap_id, n, eff_fy_k, eff_fy_fp"),
    ]),
    ("The whole of every sheet (raw layer, grows every day)", [
        ("raw_catalog", "List of the raw tables: which sheet and tab each one holds.", "Written by raw_store.py on every build.", "table_name, source, tab, n_columns"),
        ("raw_load_log", "One line per table per day: rows in the sheet, rows new today, rows that disappeared.", "Written by raw_store.py on every build.", "load_date, table_name, rows_in_sheet, rows_new, rows_vanished_total"),
        ("raw_gew_walkin_dump_fy26", "Walk-ins FY26, every column as given.", "Tab 'Walkin Dump FY26' (GEW workbook). Every raw table has first_seen_on and last_seen_on.", "the tab's own columns + row_hash, first_seen_on, last_seen_on"),
        ("raw_gew_walkin_dump_fy27", "Walk-ins FY27.", "Tab 'Walkin Dump FY27'.", "same"),
        ("raw_gew_walkin_target_fy27", "Walk-in targets FY27.", "Tab 'Walkin Target FY27'.", "same"),
        ("raw_gew_demo_dump", "Demo classes.", "Tab 'Demo Dump'.", "same"),
        ("raw_gew_enroll_target", "Enrollment and AOV targets, as laid out in the sheet.", "Tab 'Enroll Target'.", "same"),
        ("raw_gew_target_long", "The plan in long form.", "Tab 'Target_Long'.", "same"),
        ("raw_gew_enroll_dump_fy26", "Enrollment dump FY26, all 22 columns.", "Tab 'Enroll Dump FY26'.", "same"),
        ("raw_gew_enroll_dump_fy27", "Enrollment dump FY27, all 22 columns.", "Tab 'Enroll Dump Fy27'.", "same"),
        ("raw_gew_collection_dump", "Payments up to FY26, all 13 columns.", "Tab 'Collection Dump'.", "same"),
        ("raw_gew_collection_fy27", "Payments FY27 (copy inside the GEW workbook).", "Tab 'Collection FY27'.", "same"),
        ("raw_collection2_collection_fy27", "Payments FY27 from the second workbook.", "collection.xlsx.", "same"),
        ("raw_pl_students_sheet1", "The whole Iteration sheet, all 15 columns.", "complete.xlsx.", "same"),
        ("raw_pl_all_tabs", "All 22 tabs of finance's monthly P&L workbook, one row per line per month.", "cost.xlsx.", "tab, group_label, line_label, unit, month, value, value_text"),
    ]),
    ("Daily accumulation (nothing is lost)", [
        ("archive_enrollment_removed", "Students that were in the sheet on an earlier day and have disappeared. Not counted in any revenue number.", "Found by comparing with the previous database at every build.", "all enrollment columns + removed_on"),
        ("archive_collection_removed", "Payments that were in the sheet earlier and have disappeared. Not counted in any revenue number.", "Same comparison, for payments.", "all payment columns + removed_on"),
        ("load_log", "One row per build and sheet: date, rows in the sheet, how many are new, how many vanished.", "Written by build_db.track_daily.", "load_date, source, rows_in_sheet, rows_new, rows_vanished"),
    ]),
    ("Housekeeping", [
        ("dq_issue", "Notes about data that needed care.", "Written by the build steps.", "source, code, detail, n_rows, amount"),
        ("meta", "Key dates: data till, build time, date the Iteration data was prepared.", "Written by the build steps.", "key, value"),
    ]),
]
tbl_html = ""
for gname, items in GROUPS:
    tbl_html += f"<h3>{e(gname)}</h3><table><tr><th style='width:21%'>Table</th><th style='width:8%'>Rows</th><th>What it holds</th><th style='width:27%'>How it is filled</th></tr>"
    for t, what, how, cols in items:
        tbl_html += (f"<tr><td><code>{t}</code><br><span class='m'>{e(cols)}</span></td><td>{n(q1(f'SELECT COUNT(*) FROM {t}'))}</td><td>{e(what)}</td><td>{e(how)}</td></tr>")
    tbl_html += "</table>"

NOTES = {
    "fact_enrollment": [("joining_date", "The ADMISSION date: column new_receipt_date of the sheet. This is the effective start date."), ("join_month", "Month of the admission date."),
                        ("batch_end", "batch_end_date of the sheet = the effective end date."), ("total_payable", "The booking amount (fee payable, includes GST)."), ("src", "FY26 or FY27 tab it came from.")],
    "fact_collection": [("month", "Month of receipt_date (not the sheet's own 'Month' column, which Excel turns into a wrong date)."), ("net_paid_amount", "Money received, includes GST. Used for collection figures only; revenue is counted on the booking.")],
    "fact_student": [("adj_collection", "The booking without GST (total_payable ÷ 1.18). THIS is the amount that becomes revenue."), ("adj_collection", "Money collected so far (payments up to the data date ÷ 1.18). Information only."), ("eff_start / eff_end", "Admission date and batch end date."),
                     ("n_months", "Number of calendar months from start to end, both counted. Never less than 1."), ("dq_flag", "Set when something needed care, for example END_MONTH_BEFORE_START_MONTH.")],
    "fact_revenue_monthly": [("amount", "payable_ex_gst ÷ n_months. The same amount in every month of the student's batch. Only months inside this financial year are stored.")],
    "cmp_student": [("kind", "both / only_ours (only GEW data) / only_sheet (only Iteration data)."), ("eff_date / eff_later / eff_amount", "Revenue effect of different dates, of a different booking amount (eff_later is always 0 now: later payments do not change revenue).")],
}
notes_html = "".join(f"<h3><code>{t}</code></h3><table>" + "".join(f"<tr><td style='width:22%'><code>{col}</code></td><td>{e(txt)}</td></tr>" for col, txt in rows) + "</table>" for t, rows in NOTES.items())

steps = [
    ("Download", "refresh.py → fetch_sheets.py", "Downloads the two main workbooks (and the P&L team's two reference sheets) into data/raw. A failed download keeps the previous copy.", "Reference sheets failing only gives a warning."),
    ("Create an empty NEW database", "build_db.py", "Builds a separate file (gew.building.db) from db/schema.sql. The live database is not touched yet.", "—"),
    ("Branches", "load_branches", "27 branches and their cohorts from config.py; the short names from the 'Enroll Target' tab.", "Any branch name in a sheet that is not in the list stops the build with a clear message."),
    ("Annual plan (AOP1)", "load_aop1", "Reads tab Target_Long into aop_version + fact_target.", "AOV shown as #DIV/0! where the target is 0 is filled safely (noted). If AOV is broken where students are planned, the build stops."),
    ("Keep uploaded plans", "carry_over_uploaded_plans", "Copies any plan uploaded from the portal (AOP2…) from the old database.", "So a rebuild never deletes your plans."),
    ("Students admitted", "load_enrollments", "Reads both enrollment tabs. Admission date = new_receipt_date, end date = batch_end_date. Keeps the latest row if a student number appears twice.",
     "If more than 2% of rows have no admission date or batch end date, the build stops: 'source sheet problem'."),
    ("Payments", "load_collections", "Reads both collection sheets; the month comes from the real receipt date.", "Unknown branch names stop the build."),
    ("Data-till date", "set_snapshot", "The latest date that exists in BOTH admissions and payments (never in the future).", "—"),
    ("Finance P&L (reference)", "load_pl_reference", "Reads finance's monthly revenue, students and collection if the file is present.", "Optional; a problem here is skipped with a warning."),
    ("Revenue per student (history)", "history.build_history", "For each student: amount = the booking (total payable) ÷ 1.18; months from admission month to batch end month; amount ÷ months in each month.",
     "End before start: whole amount in the start month, noted, never dropped."),
    ("Revenue base", "students_from_dumps", "Copies those students into fact_student (adds fee, status).", "—"),
    ("Revenue for this year", "revenue_engine.rebuild_schedule", "Writes fact_revenue_monthly for the financial year only.", "—"),
    ("Gap Check", "gaps.build_gaps", "Compares with the Iteration sheet student by student and splits the revenue gap into causes.", "The causes must add up to the gap exactly, otherwise the build stops and nothing is published."),
    ("Check, back up, swap", "build_db.swap_in", "New database must have rows in the main tables and must not be more than 3% smaller than the live one. The live one is copied to gew.prev.db, then replaced in one step.",
     "If any check fails, the live database stays exactly as it was and the portal shows a notice."),
]
step_rows = "".join(f"<tr><td>{i}</td><td><b>{e(a)}</b><br><code>{e(b)}</code></td><td>{e(d)}</td><td>{e(w)}</td></tr>" for i, (a, b, d, w) in enumerate(steps, 1))

HTML = f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>GEW portal: backend and database report</title><style>
@page {{ size: A4; margin: 14mm 14mm 16mm; }} * {{ box-sizing: border-box; }}
body {{ font-family: "Segoe UI", Arial, sans-serif; color: #1b2133; font-size: 10.3pt; line-height: 1.45; margin: 0; }}
h1 {{ font-size: 21pt; margin: 0 0 4px; }} h2 {{ font-size: 14pt; margin: 20px 0 8px; padding-bottom: 4px; border-bottom: 2px solid #2f6df6; break-after: avoid; }}
h3 {{ font-size: 11pt; margin: 12px 0 4px; break-after: avoid; }} p {{ margin: 6px 0; }} .sub {{ color: #5b6478; margin: 0 0 12px; }} .m {{ color: #5b6478; font-size: 8.3pt; }}
.box {{ background: #f3f6fd; border: 1px solid #d5dbe8; border-radius: 8px; padding: 8px 12px; margin: 8px 0; }} .box ul {{ margin: 4px 0 4px 18px; padding: 0; }}
table {{ border-collapse: collapse; width: 100%; font-size: 9.2pt; }} th, td {{ border-bottom: 1px solid #e3e8f2; padding: 4px 6px; text-align: left; vertical-align: top; }}
th {{ background: #f3f6fd; font-weight: 600; }} tr {{ break-inside: avoid; }} code {{ background: #f0f2f8; padding: 0 4px; border-radius: 3px; font-size: 8.8pt; }}
.ok {{ color: #12805c; font-weight: 700; }} .warn {{ color: #b26b00; font-weight: 700; }} .pb {{ break-before: page; }}
.flow {{ display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin: 8px 0; }} .flow div {{ border: 1px solid #d5dbe8; border-radius: 8px; padding: 6px 10px; font-size: 9pt; }} .flow .ar {{ border: none; padding: 0; font-size: 14pt; color: #5b6478; }}
.foot {{ color: #5b6478; font-size: 8.5pt; margin-top: 16px; }}
</style></head><body>
<h1>GEW portal: backend and database report</h1>
<p class="sub">How the database is built, what is in it, and how everything around it works · Government Exam – Offline · data till {day(snap)} · database built {dt.datetime.fromisoformat(meta['built_at']):%d %b %Y, %I:%M %p}</p>

<h2 style="margin-top:6px">1. The big picture</h2>
<div class="flow"><div><b>Google Sheets</b><br>2 main + 2 P&amp;L (reference)</div><div class="ar">→</div><div><b>Download</b><br>7:30 every morning</div><div class="ar">→</div><div><b>Build a new database</b><br>14 steps, with checks</div><div class="ar">→</div><div><b>Swap in</b><br>only if checks pass</div><div class="ar">→</div><div><b>API (server.py)</b><br>answers the pages</div><div class="ar">→</div><div><b>Portal pages</b><br>localhost:8000</div></div>
<div class="box"><ul>
<li><b>One database file</b>, <code>data/gew.db</code> ({size_mb:.0f} MB, SQLite, kept on this computer). It has {len(q("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))} tables. It is <b>rebuilt from scratch every morning</b> from the two main Google Sheets, so it can never drift away from them. Nothing in it is typed by hand, except the branch list and the plans you upload.</li>
<li>The design is a simple <b>star layout</b>: small lists (branches, plans), big fact tables (admissions, payments), and calculated tables (revenue). It uses only types that PostgreSQL also has, so moving to PostgreSQL later is a small change.</li>
<li><b>Safety first:</b> the new database is built on the side and replaces the live one only if every check passes. If anything fails (no internet, a sheet with missing dates, a branch name nobody has seen), the portal keeps showing the last good data and says so on screen.</li>
<li>Last automatic update: <b>{last_refresh}</b>. Independent check against the raw sheets: <b>{v_pass} of {v_all} checks matched</b>{'' if v_ok else ' (<span class="warn">not all matched, do not present</span>)'}.</li></ul></div>

<h2>2. Which file does what</h2>
<table><tr><th style="width:27%">File</th><th>Job</th></tr>
<tr><td><code>config.py</code></td><td>Every business setting in one place: sheet addresses, financial year, branch → cohort list, GST 1.18, branch-age groups, look-back for the estimate.</td></tr>
<tr><td><code>fetch_sheets.py</code></td><td>Downloads the sheets. A download goes to a temporary file first, so a failed download never damages the good copy.</td></tr>
<tr><td><code>db/schema.sql</code></td><td>The design of the database (all tables and columns).</td></tr>
<tr><td><code>build_db.py</code></td><td>The builder: reads the sheets, cleans and checks them, fills the tables, then swaps the new database in.</td></tr>
<tr><td><code>history.py</code></td><td>Works out each student's start, end and amount, and spreads it over months (the revenue rule).</td></tr>
<tr><td><code>revenue_engine.py</code></td><td>Turns the revenue rows into the three layers shown on the page, and makes the estimate for students who have not joined yet.</td></tr>
<tr><td><code>gaps.py</code></td><td>Compares with the P&amp;L team's student sheet and splits the revenue gap into causes.</td></tr>
<tr><td><code>server.py</code></td><td>The API the pages talk to (revenue, history, vintage, gaps, plans, CSV downloads). Keeps heavy answers in memory until the database changes.</td></tr>
<tr><td><code>refresh.py</code></td><td>The daily job: download, rebuild, swap, write a log (<code>data/refresh.log</code>) and the result (<code>data/last_refresh.json</code>).</td></tr>
<tr><td><code>install_schedule.ps1</code>, <code>install_server_autostart.ps1</code></td><td>Create the two Windows tasks: the daily refresh (7:30, retries every 20 minutes if it fails) and the portal server (starts when you log in).</td></tr>
<tr><td><code>verify.py</code></td><td>Recalculates everything from the raw sheets with separate code and compares with the database ({v_all} checks).</td></tr>
<tr><td><code>web/</code></td><td>The pages: Revenue Projection, Branch Trends, Gap Check, and the Executive Dashboard (which reads Google directly).</td></tr></table>

<h2>3. How the database is built, step by step</h2>
<p>Every morning, <code>refresh.py</code> runs these steps in this order. Nothing changes the live database until the last step.</p>
<table><tr><th style="width:4%">#</th><th style="width:27%">Step</th><th>What happens</th><th style="width:28%">What it checks</th></tr>{step_rows}</table>

<h2 class="pb">4. The revenue rule (the heart of the database)</h2>
<div class="box"><ul>
<li><b>Start</b> = admission date (<code>new_receipt_date</code>) and <b>end</b> = <code>batch_end_date</code>, used exactly as named in the sheet (confirmed by the business).</li>
<li><b>Amount</b> = the booking: the total fee the student has to pay, without GST (÷ 1.18). The Iteration sheet's "Total Adjusted Collection" is the same number (within ₹5) for {float(meta['iter_booking_match_rate']) * 100:.1f}% of the students found on both sides; for the others it is lower, because that sheet only counts money collected up to its date. Money collected so far does not change our revenue.</li>
<li><b>Spread</b> = the amount divided equally over every calendar month from the start month to the end month, both counted (at least 1 month).</li>
<li>Months up to the last complete month are <b>already earned</b>; later months of students who have already joined are <b>coming from current students</b>. Students not yet admitted are an <b>estimate</b>: planned students × % achieved × this year's average fee of the branch ÷ 1.18, spread over the branch's typical batch length. For the month in progress only the not-yet-admitted part of the plan is counted.</li></ul></div>

<h2>5. What is in the database</h2>
{tbl_html}

<h2 class="pb">6. The important columns, in plain words</h2>
{notes_html}

<h2 class="pb">7. Is the database healthy? (checked live for this report)</h2>
<table><tr><th>Check</th><th style="width:9%">Result</th><th style="width:36%">Detail</th></tr>{health_rows}</table>
<p>"OK" means nothing to do. "NOTE" means the data has a feature worth knowing about; it is already handled safely and is explained below.</p>

<h2>8. Safety and automation</h2>
<table><tr><th style="width:30%">Risk</th><th>What the system does</th></tr>
<tr><td>A sheet has missing dates</td><td>The build stops with a message, the live database stays, the portal and Executive Dashboard show a notice.</td></tr>
<tr><td>A download fails or the internet is not up yet</td><td>Keeps the previous copy; the daily job tries again every 20 minutes, up to 6 times.</td></tr>
<tr><td>New data is much smaller than before (more than 3% fewer rows)</td><td>Not swapped in. Can be forced by hand if the change is expected.</td></tr>
<tr><td>A branch name that is not in the list</td><td>The build stops and names the branch. One line in <code>config.py</code> fixes it.</td></tr>
<tr><td>The Gap Check causes do not add up</td><td>The build stops. Nothing wrong is ever published.</td></tr>
<tr><td>You uploaded a new plan (AOP2)</td><td>It is copied into every new database, so a rebuild never loses it.</td></tr>
<tr><td>The PC was off at 7:30</td><td>The job runs as soon as the PC is on again. The portal server starts by itself when you log in.</td></tr></table>

<h2>9. Things to know and open questions</h2>
<ul>
<li><b>Payments without a student:</b> {n(orphan_pay)} payments ({cr(orphan_amt)}, {orphan_amt / total_paid * 100:.2f}% of all money) belong to student numbers that are not in the enrollment data. They do not change revenue (revenue is on the booking), only collection. Worth asking the data team whether those students are missing from the enrollment sheet.</li>
<li><b>Possible duplicate payments:</b> {n(dup_groups)} groups of payments have the same student, date, amount and mode (up to {cr(dup_amt)} if all were true duplicates). They are counted as in the sheet. Worth a look with the finance team.</li>
<li><b>Revenue uses the booking.</b> The full fee counts even if the student has not paid it all yet: {cr(q1("SELECT amount FROM dq_check WHERE check_id='booked_not_collected'"))} of the booking is still to be collected. A collection forecast would need the instalment plans.</li>
<li><b>GST is fixed at 18%</b> (<code>GST_DIVISOR = 1.18</code> in <code>config.py</code>).</li>
<li><b>Dates:</b> {n(q1('SELECT COUNT(*) FROM fact_student WHERE end_month<start_month'))} students have a batch end before their admission month; their whole amount is counted in the admission month.</li>
<li><b>History starts in January 2024</b>, so last year's comparison is complete but the year before is not.</li>
<li><b>The P&amp;L team's student sheet is a snapshot</b> ({day(meta['iter_asof'])}) pasted by hand, so the Gap Check always shows a "timing" gap until they refresh it. The real differences are listed on the Gap Check page for discussion.</li>
<li><b>Only this computer</b> can open the portal (localhost). Showing it on other devices needs it hosted on a shared machine.</li></ul>

<h2>10. How to run and extend it</h2>
<table><tr><th style="width:42%">I want to…</th><th>Do this</th></tr>
<tr><td>Update the data right now</td><td><code>python refresh.py</code> (takes 2–6 minutes)</td></tr>
<tr><td>Check everything against the raw sheets</td><td><code>python -I verify.py</code></td></tr>
<tr><td>Add a new branch</td><td>Add its name and cohort in <code>config.py</code> (<code>COHORT_MAPPING</code>), then refresh</td></tr>
<tr><td>Add a new annual plan</td><td>Revenue Projection page → "Annual plan (AOP) versions" → download the file, edit, upload</td></tr>
<tr><td>Change GST, the financial year, branch-age groups</td><td><code>config.py</code></td></tr>
<tr><td>Rebuild these reports</td><td><code>python docs/make_guide_pdf.py</code> and <code>python docs/make_backend_report.py</code></td></tr>
<tr><td>Add costs / P&amp;L later</td><td>Add a cost table next to the fact tables (same branch list), fill it in <code>build_db.py</code>, and add a page</td></tr></table>
<p class="foot">Generated {dt.datetime.now():%d %b %Y, %I:%M %p} from the live database. Re-create with: python docs/make_backend_report.py</p>
</body></html>"""

OUT_HTML.write_text(HTML, encoding="utf-8")
c.close()                                   # never keep the database open while Chrome prints (it could block the daily update)
browser = next((p for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")
                if pathlib.Path(p).exists()), None) or shutil.which("chrome") or shutil.which("msedge")
if not browser:
    raise SystemExit(f"Chrome/Edge not found; open {OUT_HTML} in a browser and use Print > Save as PDF")
if OUT_PDF.exists():
    OUT_PDF.unlink()
with tempfile.TemporaryDirectory() as prof:
    subprocess.run([browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", "--virtual-time-budget=5000", f"--user-data-dir={prof}", f"--print-to-pdf={OUT_PDF}", OUT_HTML.as_uri()],
                   check=True, timeout=240, capture_output=True)
print(f"written {OUT_PDF} ({OUT_PDF.stat().st_size / 1024:.0f} KB)")

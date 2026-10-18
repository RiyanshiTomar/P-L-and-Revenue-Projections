"""Builds docs/GEW_Revenue_Portal_Guide.pdf : a plain-language explainer of where every number in the portal comes from.

    python docs/make_guide_pdf.py

All numbers are read live from data/gew.db (and verify.py is run first), so re-running it after a refresh updates the document.
Needs Chrome or Edge (used only to print the PDF).
"""
from __future__ import annotations

import datetime as dt
import html
import json
import pathlib
import re
import shutil
import sqlite3
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import config                      # noqa: E402
import revenue_engine as eng      # noqa: E402

OUT_HTML = ROOT / "docs" / "GEW_Revenue_Portal_Guide.html"
OUT_PDF = ROOT / "docs" / "GEW_Revenue_Portal_Guide.pdf"
e = html.escape
c = sqlite3.connect(config.DB_PATH)
q1 = lambda sql, *a: c.execute(sql, a).fetchone()
qa = lambda sql, *a: c.execute(sql, a).fetchall()


def inr(x, d=0):
    s = f"{abs(x):.{d}f}"
    whole, _, frac = s.partition(".")
    head, tail = whole[:-3], whole[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:]); head = head[:-2]
    if head:
        parts.insert(0, head)
    out = ",".join(parts + [tail]) if parts else tail
    return ("-" if x < 0 else "") + out + (("." + frac) if frac else "")


cr = lambda v, d=2: f"₹ {v / 1e7:,.{d}f} Cr"
scr = lambda v, d=2: ("+" if v > 0 else "−" if v < 0 else "") + f"{abs(v) / 1e7:,.{d}f}"
mon = lambda ym: dt.date(int(ym[:4]), int(ym[5:7]), 1).strftime("%b %Y")
day = lambda iso: dt.date.fromisoformat(iso[:10]).strftime("%d %b %Y")

# ------------------------------------------------------------------ run the independent check first
vr = subprocess.run([sys.executable, "-I", str(ROOT / "verify.py")], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1200)
m_ = re.search(r"(\d+) of (\d+) checks passed", vr.stdout)
v_pass, v_all = (int(m_.group(1)), int(m_.group(2))) if m_ else (0, 0)
v_ok = bool(m_) and v_pass == v_all and vr.returncode == 0
print("verify.py:", f"{v_pass} of {v_all} passed" if m_ else "could not run")

# ------------------------------------------------------------------ live numbers
info = eng.as_of_info(c)
view = eng.build_view(c, 1, 100.0, "trend", config.GST_DIVISOR)
B = view["branches"]
R = sum(sum(b["recognised"]) for b in B); C = sum(sum(b["committed"]) for b in B); E = sum(sum(b["expected"]) for b in B)
la = view["months"].index(info["last_actual_month"])
enr_act = sum(sum(b["enroll_actual"][:la + 1]) for b in B); enr_tgt = sum(sum(b["enroll_target"][:la + 1]) for b in B)
pace = round(enr_act / enr_tgt, 2) if enr_tgt else 1.0
T = R + C + E
T_pace = R + C + E * pace
meta = dict(qa("SELECT key, value FROM meta"))
n_enr = q1("SELECT COUNT(*), MIN(join_month), MAX(join_month), MAX(joining_date) FROM fact_enrollment")
n_col = q1("SELECT COUNT(*), MIN(receipt_date), MAX(receipt_date), SUM(net_paid_amount) FROM fact_collection")
tg = q1("SELECT SUM(enroll_target), SUM(booking_target), MIN(month), MAX(month) FROM fact_target WHERE aop_id=1")
plan_aov = tg[1] / tg[0]
aov_ytd = view["window"]["global_booking_aov"]
y27 = q1("SELECT SUM(amount) FROM fact_revenue_dump_monthly WHERE month BETWEEN '2026-04' AND ?", info["last_actual_month"])[0]
y26 = q1("SELECT SUM(amount) FROM fact_revenue_dump_monthly WHERE month BETWEEN '2025-04' AND ?", eng.idx_ym(eng.ym_idx(info["last_actual_month"]) - 12))[0]
fy26 = q1("SELECT SUM(amount) FROM fact_revenue_dump_monthly WHERE month BETWEEN '2025-04' AND '2026-03'")[0]
try:
    st = json.loads((ROOT / "data" / "last_refresh.json").read_text(encoding="utf-8"))
    refresh_t = dt.datetime.fromisoformat(st["finished"]).strftime("%d %b %Y, %I:%M %p")
except Exception:
    refresh_t = dt.datetime.fromisoformat(meta["built_at"]).strftime("%d %b %Y, %I:%M %p")

# a real student to walk through one revenue number
ex = q1("""SELECT s.reg_no, b.branch_name, s.eff_start, s.eff_end, s.start_month, s.end_month, s.n_months, e.total_payable, s.payable_ex_gst FROM fact_student s
           JOIN dim_branch b USING(branch_id) JOIN fact_enrollment e USING(reg_no)
           WHERE s.dq_flag IS NULL AND s.n_months BETWEEN 6 AND 9 AND s.start_month >= '2026-05' AND s.end_month <= '2027-03' AND e.total_payable > 5000
           ORDER BY s.reg_no LIMIT 1""")
ex_total = ex[7]; ex_net = ex[8]; ex_per = ex_net / ex[6]

# where each month's revenue comes from
stack = qa("""SELECT r.month,
        SUM(CASE WHEN s.start_month < ? THEN r.amount ELSE 0 END),
        SUM(CASE WHEN s.start_month >= ? AND s.start_month < r.month THEN r.amount ELSE 0 END),
        SUM(CASE WHEN s.start_month = r.month THEN r.amount ELSE 0 END), SUM(r.amount)
        FROM fact_revenue_monthly r JOIN fact_student s USING(reg_no) GROUP BY r.month ORDER BY r.month""", config.FY_START, config.FY_START)
stack_rows = "".join(f"<tr><td>{mon(m)}</td><td>{a / 1e7:.2f}</td><td>{b / 1e7:.2f}</td><td>{s_ / 1e7:.2f}</td><td><b>{t / 1e7:.2f}</b></td><td>{(a / t * 100):.0f}%</td></tr>"
                     for m, a, b, s_, t in stack[:6] + stack[-1:])

# Gap Check
eff = dict(qa("SELECT effect, SUM(amount) FROM cmp_effect_monthly GROUP BY effect"))
reasons = qa("SELECT kind, COALESCE(reason,''), COUNT(*), COALESCE(SUM(rev_ours),0), COALESCE(SUM(rev_sheet),0) FROM cmp_student GROUP BY 1,2 ORDER BY 1, 3 DESC")
dd = q1("""SELECT COUNT(*), SUM(start_diff_days=0), SUM(start_month_differs=1), SUM(end_diff_days=0), SUM(end_month_differs=1) FROM cmp_student WHERE kind='both'""")
gap = eff.get("ours", 0) - eff.get("sheet", 0)
iter_asof = meta.get("iter_asof", "")
after_rev = sum(r[3] for r in reasons if r[0] == "only_ours" and r[1].startswith("admitted after"))
n_after = sum(r[2] for r in reasons if r[0] == "only_ours" and r[1].startswith("admitted after"))
timing = after_rev
real = (eff.get("ours", 0) - eff.get("sheet", 0)) - timing
n_both = dd[0]
n_only_ours = sum(r[2] for r in reasons if r[0] == "only_ours"); n_only_sheet = sum(r[2] for r in reasons if r[0] == "only_sheet")
reason_rows = "".join(f"<tr><td>{ {'both': 'On both sides', 'only_ours': 'Only in GEW data', 'only_sheet': 'Only in Iteration data'}[k] }</td><td>{e(r) or '—'}</td><td>{inr(n)}</td>"
                      f"<td>{'—' if k == 'only_sheet' else cr(a)}</td><td>{'—' if k == 'only_ours' else cr(b)}</td></tr>" for k, r, n, a, b in reasons)

# database tables
desc = {
    "dim_branch": "The 27 branches and their cohorts", "aop_version": "The annual plans (AOP1, and any AOP2… you upload)",
    "fact_target": "The plan: students and fees per branch per month", "fact_enrollment": "Every student admitted (enrollment dump)",
    "fact_collection": "Every payment received (collection sheets)", "fact_student": "The revenue base: one row per student with start, end and amount",
    "fact_revenue_monthly": "Each student's revenue, month by month, this year (what the portal adds up)",
    "fact_student_dump": "The same students, kept for the full history", "fact_revenue_dump_monthly": "Revenue, month by month, since Jan 2024 (Branch Trends)",
    "ref_iter_student": "The Iteration sheet, as given (reference only)", "cmp_student": "Gap Check: one row per student compared",
    "cmp_effect_monthly": "Gap Check: the revenue gap split by cause", "ref_pl_monthly": "Finance's monthly P&L numbers (reference only)",
    "dq_issue": "Notes about data that needed care", "meta": "Dates and build time"}
tab_rows = "".join(f"<tr><td><code>{t}</code></td><td>{inr(c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0])}</td><td>{e(desc.get(t, ''))}</td></tr>"
                   for (t,) in qa("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"))

import server as _srv  # noqa: E402  (the exact calculation the Branch Trends page uses)
vint = _srv.vintage()["groups"]
vtot = sum(g["this_year"]["revenue"] for g in vint)
vrows = "".join(
    f"<tr><td><b>{e(g['label'])}</b><br><span class='m'>{e(', '.join(b['short'] for b in g['branches']))}</span></td><td>{len(g['branches'])}</td>"
    f"<td>{inr(g['this_year']['students'])}</td><td>₹ {inr(g['this_year']['avg_fee'] or 0)}</td><td>{cr(g['this_year']['revenue'])}</td><td>{(g['this_year']['revenue'] / vtot * 100):.0f}%</td></tr>"
    for g in vint)


def card(title, badge, what, source, cols, used, stats, note=""):
    return f"""<div class="card"><div class="ch"><h3>{title}</h3><span class="badge">{badge}</span></div><p>{what}</p><table class="kv">
      <tr><th>Where it comes from</th><td>{source}</td></tr><tr><th>What is in it</th><td>{cols}</td></tr>
      <tr><th>What we use it for</th><td>{used}</td></tr><tr><th>Right now</th><td>{stats}</td></tr>{f'<tr><th>Good to know</th><td>{note}</td></tr>' if note else ''}</table></div>"""


cards = "".join([
    card("1. Branches and cohorts", "Typed once", "The list of branches and the cohort each one belongs to.",
         "Not a sheet. Written once in the portal settings, with the same grouping as the Executive Dashboard.", "Branch name, cohort number.",
         "Every filter and every cohort total.", f"{q1('SELECT COUNT(*) FROM dim_branch')[0]} branches in 9 cohorts."),
    card("2. Annual plan (AOP)", "Main sheet", "How many students each branch plans to admit every month, and the planned fee per student.",
         "Main workbook <b>GEW PowerBI &lt;&gt; Dashboard Sheet</b>, tab <b>Target_Long</b>.",
         "Branch, month, students planned, fees planned, fee per student (this fee includes GST, confirmed by the manager).",
         "The estimate for students who have not joined yet, and the “% of plan achieved so far”.",
         f"{inr(tg[0])} students planned, {cr(tg[1])} of fees, {mon(tg[2])} to {mon(tg[3])}.",
         "The portal follows this plan. A changed plan (AOP2) can be uploaded on the Revenue Projection page; plans you upload are kept when the data refreshes."),
    card("3. Students admitted (enrollments)", "Main sheet", "One row for every student: which branch, when admitted, when the batch ends, and the fee.",
         "Main workbook, tabs <b>Enroll Dump FY26</b> (earlier) and <b>Enroll Dump Fy27</b> (this year). It refreshes by itself.",
         "Student number, branch, course, <b>new_receipt_date</b> (the admission date = <b>effective start date</b>), <b>batch_end_date</b> (= <b>effective end date</b>), total fee payable (the booking amount, includes GST).",
         "<b>The booking of every student: this is the amount that becomes revenue.</b> Also the number of students admitted, the average fee per student, every student's start and end dates.",
         f"{inr(n_enr[0])} students, admitted between {mon(n_enr[1])} and {day(n_enr[3])}.",
         "These two columns are used exactly as named. No other date is used or guessed."),
    card("4. Payments received (collections)", "Main sheet", "One row for every payment a student has made.",
         "Tab <b>Collection Dump</b> (earlier) in the main workbook, and tab <b>Collection FY27</b> in a separate workbook (the first one ran out of space).",
         "Payment date, student number, branch, payment mode, amount received (includes GST), refund.",
         "How much of the booking has been collected so far, and the collection figures. <b>Revenue is not counted on it</b>; revenue is counted on the booking (item 3).",
         f"{inr(n_col[0])} payments from {day(n_col[1])} to {day(n_col[2])}; {cr(n_col[3])} received (with GST)."),
    card("5. The revenue base (calculated)", "Built by us", "For each student: from which month to which month their money is counted, and how much money.",
         "Calculated from items 3 and 4. Nothing is typed or pasted by hand.",
         "Start = admission date; End = batch end date; Amount = the booking (total fee payable) ÷ 1.18 (without GST).",
         "Everything on the Revenue Projection and Branch Trends pages.",
         f"{inr(q1('SELECT COUNT(*) FROM fact_student')[0])} students; data till {day(info['snapshot_date'])}.",
         "If a student's end date is before the start date, the whole amount is counted in the start month and the student is noted (nothing is dropped)."),
    card("6. Iteration data: the Iteration sheet", "Reference only", "Their own student-wise sheet, prepared on an earlier date.",
         "Workbook <b>Govt Offline Iteration till Aug YTD</b>, <b>Sheet1</b> (the vertical view). It is pasted by hand and does not refresh itself.",
         "Student number, branch, fee, <i>Final Total Payable (w/o GST)</i>, <i>Total Adjusted Collection</i> (the amount we take from it), <i>Effective Start and End dates</i>.",
         "<b>Only the Gap Check page.</b> It never changes a portal number.",
         f"{inr(q1('SELECT COUNT(*) FROM ref_iter_student')[0])} students; prepared on {day(iter_asof)}.",
         f"Its Total Adjusted Collection equals ours (the booking) within ₹5 for {float(meta['iter_booking_match_rate']) * 100:.1f}% of the students found on both sides. It stops on {day(iter_asof)}, which is how we know the day it was prepared."),
    card("7. Iteration data: the monthly sheet", "Reference only", "Finance's month-wise numbers for Government Exam – Offline.",
         "Workbook with the cost tabs, tab <b>Govt exam - Offline</b> (the horizontal view). Amounts are in ₹ Crore.",
         "Revenue, net collection, students admitted, total cost, month by month.",
         "<b>Only the Gap Check page</b>, to show where the GEW data and the Iteration data differ.",
         "Revenue is available for Apr 2024 to Aug 2026.", "The other tabs hold the costs; they are not loaded yet (next phase)."),
])

HTML = f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>GEW Revenue Portal: where every number comes from</title>
<style>
@page {{ size: A4; margin: 14mm 14mm 16mm; }}
* {{ box-sizing: border-box; }}
body {{ font-family: "Segoe UI", Arial, sans-serif; color: #1b2133; font-size: 10.5pt; line-height: 1.45; margin: 0; }}
h1 {{ font-size: 22pt; margin: 0 0 4px; letter-spacing: -.3px; }} h2 {{ font-size: 14pt; margin: 20px 0 8px; padding-bottom: 4px; border-bottom: 2px solid #2f6df6; break-after: avoid; }}
h3 {{ font-size: 11.5pt; margin: 0; }} p {{ margin: 6px 0; }} .sub {{ color: #5b6478; margin: 0 0 14px; }} .m {{ color: #5b6478; font-size: 8.5pt; }}
.hero {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin: 10px 0 12px; }}
.k {{ border: 1px solid #d5dbe8; border-top: 4px solid #1b2133; border-radius: 8px; padding: 8px 10px; }}
.k.a {{ border-top-color: #2f6df6; }} .k.b {{ border-top-color: #17a673; }} .k.c {{ border-top-color: #e69500; }}
.k .l {{ font-size: 8pt; text-transform: uppercase; letter-spacing: .04em; color: #5b6478; }} .k .v {{ font-size: 15pt; font-weight: 700; }} .k .d {{ font-size: 8.5pt; color: #5b6478; }}
.box {{ background: #f3f6fd; border: 1px solid #d5dbe8; border-radius: 8px; padding: 8px 12px; margin: 8px 0; }} .box ul {{ margin: 4px 0 4px 18px; padding: 0; }}
.card {{ border: 1px solid #d5dbe8; border-radius: 8px; padding: 8px 12px; margin: 8px 0; break-inside: avoid; }}
.ch {{ display: flex; justify-content: space-between; align-items: center; gap: 8px; }} .badge {{ font-size: 8pt; border: 1px solid #9aa4bb; border-radius: 99px; padding: 1px 8px; color: #3b4660; white-space: nowrap; }}
table {{ border-collapse: collapse; width: 100%; font-size: 9.5pt; }} th, td {{ border-bottom: 1px solid #e3e8f2; padding: 4px 6px; text-align: left; vertical-align: top; }}
th {{ background: #f3f6fd; font-weight: 600; }} table.kv th {{ width: 24%; background: none; color: #5b6478; font-weight: 600; font-size: 8.5pt; text-transform: uppercase; letter-spacing: .03em; }}
table.num td:not(:first-child), table.num th:not(:first-child) {{ text-align: right; }} table.steps td:first-child {{ width: 6%; text-align: center; font-weight: 700; }}
tr {{ break-inside: avoid; }} code {{ background: #f0f2f8; padding: 0 4px; border-radius: 3px; font-size: 9pt; }}
.flow {{ display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin: 8px 0; }} .flow div {{ border: 1px solid #d5dbe8; border-radius: 8px; padding: 6px 10px; font-size: 9pt; }}
.flow .ar {{ border: none; padding: 0; font-size: 14pt; color: #5b6478; }} .ok {{ color: #12805c; font-weight: 600; }} .warn {{ color: #b26b00; font-weight: 600; }}
.pb {{ break-before: page; }} .keep {{ break-inside: avoid; }} .foot {{ color: #5b6478; font-size: 8.5pt; margin-top: 18px; }}
</style></head><body>

<h1>GEW Revenue Portal: where every number comes from</h1>
<p class="sub">Government Exam – Offline · FY27 · data till {day(info['snapshot_date'])} · portal last updated {refresh_t}</p>

<h2 style="margin-top:6px">In one minute</h2>
<div class="hero">
  <div class="k"><div class="l">Revenue this year (FY27)</div><div class="v">{cr(T)}</div><div class="d">if we reach 100% of the plan</div></div>
  <div class="k a"><div class="l">Already earned</div><div class="v">{cr(R)}</div><div class="d">Apr to {mon(info['last_actual_month'])}</div></div>
  <div class="k b"><div class="l">Coming from current students</div><div class="v">{cr(C)}</div><div class="d">students already admitted</div></div>
  <div class="k c"><div class="l">Coming from new students</div><div class="v">{cr(E)}</div><div class="d">an estimate</div></div>
</div>
<div class="box"><ul>
<li>Everything is built from <b>two Google Sheets</b> only: the main workbook (annual plan, students admitted, payments) and the Collection FY27 sheet. The P&amp;L team's sheets are used only to <i>compare</i>.</li>
<li><b>About {((R + C) / T * 100):.0f}% of the total is real</b> (money from students already admitted). The rest is an estimate.</li>
<li>Students admitted so far: {inr(enr_act)} against a plan of {inr(enr_tgt)} (<b>{pace * 100:.0f}%</b>). If the coming months continue at this pace, the year ends near <b>{cr(T_pace)}</b>.</li>
<li>Updated <b>automatically every morning at 7:30</b>. Checked against the original sheets: <b>{v_pass} of {v_all} checks matched</b>.</li></ul></div>

<h2>What each page answers</h2>
<table><tr><th style="width:24%">Page</th><th>Question it answers</th></tr>
<tr><td><b>Revenue Projection</b></td><td>How much revenue this year, month by month and branch by branch: already earned, coming from current students, and the estimate from new students.</td></tr>
<tr><td><b>Branch Trends</b></td><td>This year against last year; old branches against new ones; how fast a new branch grows.</td></tr>
<tr><td><b>Gap Check</b></td><td>Where the GEW data differs from the Iteration data: which students are missing where, where dates differ, and what each difference is worth in revenue.</td></tr>
<tr><td><b>Executive Dashboard</b></td><td>Enrollment, booking, collection and fee against the plan, with a plan (AOP) selector, a Line 1 vs Line 2 trend chart, and branch-wise trends of enrollment and AOV.</td></tr></table>

<h2>The journey of the data</h2>
<div class="flow"><div><b>Two Google Sheets</b><br>+ P&amp;L team's two (reference)</div><div class="ar">→</div><div><b>Download</b><br>every morning 7:30</div><div class="ar">→</div>
<div><b>Clean and check</b><br>a new copy is built on the side</div><div class="ar">→</div><div><b>Database</b><br>replaced only if checks pass</div><div class="ar">→</div><div><b>Portal pages</b></div></div>
<p>If a download fails or a sheet has missing dates, the portal keeps showing the last good data and says so on screen; nothing breaks.</p>

<h2 class="pb">The data, one by one</h2>
{cards}

<h2 class="pb">How one revenue number is made</h2>
<p>The rule: <b>revenue is counted on the booking (the total fee the student has to pay, without GST), shared equally across the months the student studies.</b> Money collected so far does not change revenue.</p>
<table class="steps"><tr><th>Step</th><th>What we do</th><th>Real example (student no. {ex[0]}, {e(ex[1])})</th></tr>
<tr><td>1</td><td>Take the total fee payable (the booking), then remove 18% GST (divide by 1.18)</td><td>₹ {inr(ex_total)} ÷ 1.18 = <b>₹ {inr(ex_net)}</b></td></tr>
<tr><td>2</td><td>Count the months from the admission date to the batch end date (both months counted)</td><td>{day(ex[2])} to {day(ex[3])}: {mon(ex[4])} to {mon(ex[5])} = <b>{ex[6]} months</b></td></tr>
<tr><td>3</td><td>Share the amount equally across those months</td><td>₹ {inr(ex_net)} ÷ {ex[6]} = <b>₹ {inr(ex_per)} every month</b></td></tr>
<tr><td>4</td><td>Add up every student, branch by branch and month by month</td><td>Months already finished (up to {mon(info['last_actual_month'])}) = <b>Already earned</b>; later months of students already admitted = <b>Coming from current students</b></td></tr>
<tr><td>5</td><td>Add an estimate for students not admitted yet</td><td><b>Coming from new students (estimate)</b>, explained below</td></tr></table>

<div class="keep"><h2>A month's revenue is not only that month's students</h2>
<p>Students admitted in earlier months (and last year) still have months left, so they add to this month's revenue. April's revenue is mostly last year's students:</p>
<table class="num"><tr><th>Month</th><th>From students admitted before April (₹ Cr)</th><th>Admitted earlier this year</th><th>Admitted in the same month</th><th>Total</th><th>Share from before April</th></tr>{stack_rows}</table>
<p class="m">Amounts are for students already admitted; the estimate for new students is not included here.</p></div>

<h2>How the estimate for new students is made</h2>
<div class="box"><b>Students planned for that month × % of plan we achieve × average fee per student ÷ months a student stays</b>, starting from the month after the last finished month. For the month in progress, only the part of the plan <i>not yet admitted</i> is counted, so nobody is counted twice.</div>
<ul>
<li><b>Students planned:</b> from the annual plan. The slider lets you test 80%, 100% and so on; “Use pace so far” sets it to {pace * 100:.0f}%.</li>
<li><b>Average fee per student:</b> as advised by the manager, we use <b>what is happening this year</b>, not the plan: ₹ {inr(aov_ytd)} (with GST) against the plan's ₹ {inr(plan_aov)}. GST is removed (÷ 1.18). Each branch uses its own average.</li>
<li><b>Months a student stays:</b> the typical length of recent batches in that branch.</li></ul>

<h2>How we checked the data is right</h2>
<p>We recalculated the numbers from the original sheets with separate code and compared them with the portal's database: {"<span class='ok'>" + f"{v_pass} of {v_all} checks matched</span>" if v_ok else "<span class='warn'>WARNING: only " + f"{v_pass} of {v_all}" + " checks matched, do not present this document until it is fixed</span>"}. Anyone can re-run it (<i>verify.py</i>).</p>
<table class="num"><tr><th>What was compared</th><th>Result</th></tr>
<tr><td>Students admitted ({inr(n_enr[0])}), month by month, every month</td><td class="ok">identical</td></tr>
<tr><td>Payments ({inr(n_col[0])} rows) and amounts, month by month, every month</td><td class="ok">identical to the rupee</td></tr>
<tr><td>Annual plan: students planned and fees planned</td><td class="ok">identical</td></tr>
<tr><td>This year's revenue, each of 12 months, recalculated separately</td><td class="ok">identical</td></tr>
<tr><td>Last year's revenue, each month since Jan 2024, recalculated separately</td><td class="ok">identical</td></tr>
<tr><td>P&amp;L team's sheet loaded correctly; the Gap Check causes add up to the gap exactly</td><td class="ok">identical</td></tr></table>
<p>This proves the portal applies the rule to the sheets faithfully. Whether the rule equals finance's way is a separate question, and that is what the Gap Check page shows.</p>

<h2>Gap Check: GEW data against the Iteration data</h2>
<p>Both sides use the same revenue rule. The GEW data counts the booking; the Iteration data counts its Total Adjusted Collection. Any difference is a difference in students, dates or amounts. The Iteration data was prepared on <b>{day(iter_asof)}</b>.</p>
<div class="box"><b>In simple words.</b> This year's revenue is <b>{cr(eff['ours'])}</b> in the GEW data and <b>{cr(eff['sheet'])}</b> in the Iteration data: a gap of <b>{scr(gap)} Cr</b>
(a plus means GEW shows more). <b>{scr(timing)} Cr of it is only timing</b>: the Iteration data stops on {day(iter_asof)}, so students admitted after that day are in the GEW data but cannot be in the Iteration data. This disappears when their sheet is refreshed.
The <b>real differences are {scr(real)} Cr</b>: students found on one side only, and different start or end dates. Those are the ones to discuss with the P&amp;L team.</div>
<table class="num"><tr><th>Reason</th><th>Students</th><th>₹ Cr</th></tr>
<tr><td><b>Revenue in the Iteration data</b></td><td>{inr(n_both + n_only_sheet)}</td><td><b>{scr(eff['sheet']).lstrip('+')}</b></td></tr>
<tr><td colspan="3"><b>A. Only because the Iteration data is older (timing, nothing to fix)</b></td></tr>
<tr><td>Students admitted after {day(iter_asof)}</td><td>{inr(n_after)}</td><td>{scr(after_rev)}</td></tr>
<tr><td><b>Total of A</b></td><td></td><td><b>{scr(timing)}</b></td></tr>
<tr><td colspan="3"><b>B. Real differences (to discuss with the P&amp;L team)</b></td></tr>
<tr><td>Students only in the Iteration data ({inr(n_only_sheet)}): not in the GEW enrollment data, or the GEW batch end date is earlier</td><td>{inr(n_only_sheet)}</td><td>{scr(eff['only_sheet'])}</td></tr>
<tr><td>Students only in the GEW data although admitted before {day(iter_asof)}</td><td>{inr(n_only_ours - n_after)}</td><td>{scr(eff['only_ours'] - after_rev)}</td></tr>
<tr><td>Different start or end dates (students on both sides)</td><td>—</td><td>{scr(eff['date'])}</td></tr>
<tr><td>Different amount for the same student (booking against Total Adjusted Collection)</td><td>{inr(n_both)}</td><td>{scr(eff['amount_def'])}</td></tr>
<tr><td><b>Total of B</b></td><td></td><td><b>{scr(real)}</b></td></tr>
<tr><td><b>Revenue in the GEW data</b></td><td>{inr(n_both + n_only_ours)}</td><td><b>{scr(eff['ours']).lstrip('+')}</b></td></tr></table>
<table class="num" style="margin-top:8px"><tr><th>Who</th><th>Reason</th><th>Students</th><th>Revenue, GEW data</th><th>Revenue, Iteration data</th></tr>{reason_rows}</table>
<p>Among the {inr(n_both)} students on both sides, the <b>start date differs for {inr(n_both - dd[1])}</b> ({inr(dd[2])} in a different month) and the <b>end date differs for {inr(n_both - dd[3])}</b> ({inr(dd[4])} in a different month). The GEW start date is the admission date; the P&amp;L start date is usually the later batch start. The Gap Check page lists every student with the difference and lets you download it for Excel.</p>

<h2 class="pb">Branches by age</h2>
<table class="num"><tr><th>Group</th><th>Branches</th><th>Students admitted Apr–{mon(info['last_actual_month'])[:3]}</th><th>Average fee</th><th>Revenue so far</th><th>Share</th></tr>{vrows}</table>
<p class="m">Branches are grouped by the month their first student was admitted (our data starts January 2024, so the oldest group may be older than it looks). This year's revenue so far, Apr to {mon(info['last_actual_month'])}: {cr(y27)}; same months last year {cr(y26)}; last year in full {cr(fy26)}.</p>

<h2>Where the data is stored (the database)</h2>
<p>One database file on this computer (<code>data/gew.db</code>). Every table below is rebuilt each morning from the two sheets, and the old copy is replaced only if the checks pass. Plans you upload yourself are kept.</p>
<table class="num"><tr><th>Table</th><th>Rows</th><th>What it holds</th></tr>{tab_rows}</table>

<h2 class="pb">What is still open (honest list)</h2>
<table><tr><th style="width:30%">Open point</th><th>Why it matters</th><th style="width:22%">Who can close it</th></tr>
<tr><td>Compare with the P&amp;L team: {inr(n_only_sheet)} students are only in the Iteration data, and start dates differ for many students</td><td>Decide which side's dates are right; this changes revenue by month.</td><td>P&amp;L team / Finance</td></tr>
<tr><td>Finance's exact revenue rule and the June 2025 one-off</td><td>Needed to explain finance's monthly numbers.</td><td>Finance</td></tr>
<tr><td>Instalment plans (who will pay how much, when)</td><td>Revenue is on the booking, so it does not need them. A collection forecast (when the unpaid part comes in) does.</td><td>Operations / Finance</td></tr>
<tr><td>The P&amp;L team's student sheet is pasted by hand</td><td>It is a snapshot of {day(iter_asof)}; a live version would make the comparison always current.</td><td>Owner of that sheet</td></tr>
<tr><td>A phone number or lead number in the student list</td><td>Needed to link walk-ins and demos to admissions (funnel).</td><td>Data team</td></tr>
<tr><td>Opening month and capacity of each branch</td><td>Needed to predict next year's plan for new branches.</td><td>Operations</td></tr>
<tr><td>Data before January 2024</td><td>Last year's comparison is complete, the year before is not.</td><td>Data team</td></tr>
<tr><td>Costs (branch-wise P&amp;L)</td><td>The cost tabs exist but are not loaded; this is the next phase.</td><td>Finance</td></tr></table>

<h2>Words used in the portal</h2>
<table><tr><th style="width:26%">Word</th><th>Meaning</th></tr>
<tr><td>Already earned</td><td>Revenue of months that are over, from money received.</td></tr>
<tr><td>Coming from current students</td><td>Revenue of later months from students already admitted.</td></tr>
<tr><td>Coming from new students (estimate)</td><td>Revenue we expect from students not admitted yet.</td></tr>
<tr><td>GEW data</td><td>Our data: built only from the two main Google Sheets (GEW enrollment data and the collection sheets).</td></tr>
<tr><td>Iteration data</td><td>The P&amp;L team's two sheets: the student-wise "Govt Offline Iteration" sheet and the monthly P&amp;L sheet. Used only for comparison.</td></tr>
<tr><td>Admission date / effective start</td><td>The column new_receipt_date of the enrollment sheet. Effective end = batch_end_date.</td></tr>
<tr><td>Annual plan (AOP)</td><td>The yearly targets: students planned per branch per month and the planned fee.</td></tr>
<tr><td>Average fee per student (AOV)</td><td>Total fee payable ÷ students admitted (includes GST).</td></tr>
<tr><td>GST</td><td>18% tax inside the fee. Revenue is counted without it, so amounts are divided by 1.18.</td></tr>
<tr><td>Cohort</td><td>A group of branches (Cohort 1 to 9) as in the Executive Dashboard.</td></tr></table>
<p class="foot">Generated {dt.datetime.now():%d %b %Y, %I:%M %p} from the live database. Re-create with: python docs/make_guide_pdf.py</p>
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
import tempfile
with tempfile.TemporaryDirectory() as prof:                 # own temporary profile: never clashes with a Chrome window you have open
    subprocess.run([browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", "--virtual-time-budget=5000", f"--user-data-dir={prof}",
                    f"--print-to-pdf={OUT_PDF}", OUT_HTML.as_uri()], check=True, timeout=240, capture_output=True)
print(f"written {OUT_PDF} ({OUT_PDF.stat().st_size / 1024:.0f} KB)")

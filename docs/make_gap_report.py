"""Builds docs/GEW_Gap_Report.pdf and docs/GEW_Gap_Report.xlsx : every gap between the GEW data, the Iteration data and Finance's P&L,
in plain words, with the numbers and a real student as proof, plus the problems found in the GEW data.

    python docs/make_gap_report.py

Everything is read from data/gew.db (tables gap_register and dq_check), so re-running it keeps it true. refresh.py runs it every morning
after the database is rebuilt, so these two files are always up to date.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import config  # noqa: E402
import gap_evidence  # noqa: E402

OUT_HTML = ROOT / "docs" / "GEW_Gap_Report.html"
OUT_PDF = ROOT / "docs" / "GEW_Gap_Report.pdf"
OUT_XLSX = ROOT / "docs" / "GEW_Gap_Report.xlsx"
e = html.escape
day = lambda iso: dt.date.fromisoformat(str(iso)[:10]).strftime("%d %b %Y").lstrip("0") if iso else "-"
mon = lambda m: dt.date(int(m[:4]), int(m[5:7]), 1).strftime("%b-%y")
n0 = lambda x: f"{x:,.0f}"
inr = lambda x: f"₹{x:,.0f}"


def sc(v):
    """signed Rs Cr; nothing under half a lakh (it would only show +0.00)"""
    if v is None:
        return "-"
    return f"{'+' if v >= 50000 else '−' if v <= -50000 else ''}{abs(v) / 1e7:.2f}"


c = sqlite3.connect(config.DB_PATH)
meta = dict(c.execute("SELECT key, value FROM meta"))
cols = ["gap_id", "grp", "sort", "title", "plain", "n", "unit", "eff_fy_k", "eff_win_k", "eff_fy_fp", "eff_win_fp", "window", "owner", "proof_reg", "proof_text", "values_json", "evidence"]
rows = [dict(zip(cols, r)) for r in c.execute(f"SELECT {', '.join(cols)} FROM gap_register ORDER BY sort")]
if not rows:
    raise SystemExit("gap_register is empty: rebuild the database first (python build_db.py)")
flags = [dict(zip(("id", "title", "sev", "n", "amount", "detail", "who"), r)) for r in c.execute(
    "SELECT check_id, title, severity, n_rows, amount, detail, who FROM dq_check ORDER BY CASE severity WHEN 'note' THEN 0 WHEN 'ask' THEN 1 WHEN 'info' THEN 2 WHEN 'ok' THEN 3 ELSE 4 END, n_rows DESC")]
xlsx = gap_evidence.build_workbook(c)
OUT_XLSX.write_bytes(xlsx)
c.close()                                      # never keep the database open while Chrome prints (it could block the daily update)

A = [r for r in rows if r["grp"] == "GEW vs Iteration"]
T = next(r for r in rows if r["gap_id"] == "TOTAL")
B = [r for r in rows if r["grp"] == "Finance P&L"]
win = A[0]["window"] if A else ""
for r in B:
    r["values"] = json.loads(r["values_json"])
F1 = next((r for r in B if r["gap_id"] == "G-F1"), None)
S = lambda arr: sum(x or 0 for x in arr)

a_rows = "".join(f"""<tr><td><b>{e(r['gap_id'])}</b></td><td class="w"><b>{e(r['title'])}</b><br><span class="m">{e(r['plain'])}</span><br><span class="m"><i>Who can close it: {e(r['owner'] or '-')}</i></span></td>
<td class="n">{n0(r['n'])}</td><td class="n">{sc(r['eff_fy_k'])}</td><td class="n">{sc(r['eff_win_k'])}</td><td class="n">{sc(r['eff_fy_fp'])}</td><td class="n">{sc(r['eff_win_fp'])}</td>
<td class="w m">{e(r['proof_text'] or '')}</td></tr>""" for r in A)
a_rows += f"""<tr class="tot"><td></td><td class="w">{e(T['title'])}<br><span class="m">{e(T['plain'])}</span></td><td></td><td class="n">{sc(T['eff_fy_k'])}</td><td class="n">{sc(T['eff_win_k'])}</td>
<td class="n">{sc(T['eff_fy_fp'])}</td><td class="n">{sc(T['eff_win_fp'])}</td><td class="m">The gaps above add up to this exactly (checked on every build).</td></tr>"""


def fmt(v, u):
    return "-" if v is None else (n0(v) if u == "students" else f"{v:.2f}")


b_rows = ""
for r in B:
    v, u = r["values"], r["values"]["unit"]
    g, f = S(v["gew"]), S(v["fin"])
    has = any(x is not None for x in v["it_k"])
    unit = "" if u == "students" else " Cr"
    mrows = "".join(f"<tr><td>{mon(m)}</td><td class='n'>{fmt(v['gew'][i], u)}</td>" + (f"<td class='n'>{fmt(v['it_k'][i], u)}</td><td class='n'>{fmt(v['it_fp'][i], u)}</td>" if has else "")
                    + f"<td class='n'>{fmt(v['fin'][i], u)}</td></tr>" for i, m in enumerate(v["months"]))
    mhead = "<tr><th>Month</th><th>GEW</th>" + ("<th>Iteration (Adj. Coll.)</th><th>Iteration (Final Pay.)</th>" if has else "") + "<th>Finance</th></tr>"
    b_rows += f"""<tr><td><b>{e(r['gap_id'])}</b></td><td class="w"><b>{e(r['title'])}</b><br><span class="m">{e(r['plain'])}</span><br><span class="m"><i>Who can close it: {e(r['owner'] or '-')}</i></span>
<table class="mini">{mhead}{mrows}</table></td>
<td class="n">{fmt(g, u)}{unit}</td><td class="n">{(fmt(S(v['it_k']), u) + unit) if has else '-'}</td><td class="n">{(fmt(S(v['it_fp']), u) + unit) if has else '-'}</td><td class="n">{fmt(f, u)}{unit}</td>
<td class="n">{'+' if g - f >= 0 else '−'}{fmt(abs(g - f), u)}{unit}</td></tr>"""

sev = {"note": ("Worth a look", "#b45309"), "ask": ("To confirm", "#9a3412"), "info": ("For the record", "#475569"), "ok": ("Checked: fine", "#15803d")}
f_rows = "".join(f"""<tr><td class="w"><b>{e(x['title'])}</b><br><span class="m">{e(x['detail'] or '')}</span><br><span class="m"><i>Who can close it: {e(x['who'] or '-')}</i></span></td>
<td><span style="color:{sev[x['sev']][1]};font-weight:600">{sev[x['sev']][0]}</span></td><td class="n">{n0(x['n'])}</td>
<td class="n">{'-' if x['amount'] is None else ('−' if x['amount'] < 0 else '') + inr(abs(x['amount']))}</td></tr>""" for x in flags if x["sev"] in sev)

rev = ""
if F1:
    v = F1["values"]
    rev = f"""<table class="num"><tr><th>Revenue, {e(F1['window'])} (₹ Cr)</th><th class="n">GEW data</th><th class="n">Iteration<br>Total Adjusted Collection</th><th class="n">Iteration<br>Final Total Payable</th><th class="n">Finance P&amp;L</th></tr>
<tr><td>Total</td><td class="n"><b>{S(v['gew']):.2f}</b></td><td class="n">{S(v['it_k']):.2f}</td><td class="n">{S(v['it_fp']):.2f}</td><td class="n"><b>{S(v['fin']):.2f}</b></td></tr></table>"""

built = meta.get("built_at", "")
doc = f"""<!doctype html><html><head><meta charset="utf-8"><title>GEW gap report</title><style>
@page {{ size: A4 landscape; margin: 12mm; }}
body {{ font-family: Segoe UI, Arial, sans-serif; font-size: 10.5px; color: #1a2233; line-height: 1.45; }}
h1 {{ font-size: 20px; margin: 0 0 2px; }} h2 {{ font-size: 14px; margin: 16px 0 6px; border-bottom: 2px solid #4f8ff7; padding-bottom: 2px; }}
.sub {{ color: #5b6577; margin-bottom: 8px; }}
table {{ border-collapse: collapse; width: 100%; margin: 6px 0; }}
th, td {{ border: 1px solid #cfd6e4; padding: 4px 6px; vertical-align: top; text-align: left; }}
th {{ background: #eef2fa; }} td.n, th.n {{ text-align: right; white-space: nowrap; }}
td.w {{ min-width: 330px; }} .m {{ color: #4b5568; font-size: 9.5px; }}
tr.tot td {{ font-weight: 700; background: #f4f7fc; }}
.box {{ background: #f4f7fc; border-left: 4px solid #4f8ff7; padding: 6px 10px; margin: 6px 0; }}
table.mini {{ width: auto; margin: 4px 0 0; font-size: 9px; }} table.mini td, table.mini th {{ padding: 1px 5px; }}
tr {{ page-break-inside: avoid; }}
</style></head><body>
<h1>Govt Exam Offline, FY 2026-27: gaps between the GEW data, the Iteration data and Finance's P&amp;L</h1>
<div class="sub">GEW data till {day(meta.get('snapshot_date'))} · Iteration sheet prepared on {day(meta.get('iter_asof'))} · updated {day(built)} · this file is rebuilt automatically every morning</div>

<div class="box"><b>The three data sets.</b>
<b>GEW data</b> = our own enrollment and collection sheets. Revenue = the booking (total fee payable, without GST) shared equally over the months from the admission date (new_receipt_date) to the batch end date (batch_end_date).
<b>Iteration data</b> = the "Govt Offline Iteration" sheet made by the P&amp;L team, student by student. It has two amounts, <i>Total Adjusted Collection</i> (money collected until its date) and <i>Final Total Payable</i> (the fee), so every gap is shown on both. Same revenue rule.
<b>Finance P&amp;L</b> = the "Revenue" line of the monthly P&amp;L workbook, month by month.</div>
{rev}

<h2>A. GEW data against Iteration data, student by student (₹ Cr, effect on this year's revenue; plus = GEW shows more)</h2>
<table><tr><th rowspan="2">ID</th><th rowspan="2">What is different</th><th rowspan="2" class="n">Students</th><th colspan="2">Iteration on Total Adjusted Collection</th><th colspan="2">Iteration on Final Total Payable</th><th rowspan="2">Proof: a real student</th></tr>
<tr><th class="n">This year</th><th class="n">{e(win)}</th><th class="n">This year</th><th class="n">{e(win)}</th></tr>{a_rows}</table>
<p class="m">"{e(win)}" are the months the Iteration sheet covers. The list of every student behind each gap is in the Excel file (GEW_Gap_Report.xlsx), one sheet per gap.</p>

<h2>B. GEW data and Iteration data against Finance P&amp;L, month by month</h2>
<table><tr><th>ID</th><th>What is different</th><th class="n">GEW data</th><th class="n">Iteration<br>Total Adj. Collection</th><th class="n">Iteration<br>Final Total Payable</th><th class="n">Finance P&amp;L</th><th class="n">GEW − Finance</th></tr>{b_rows}</table>

<h2>C. Problems found in the GEW data (flagged)</h2>
<table><tr><th>What</th><th>Kind</th><th class="n">Count</th><th class="n">Amount</th></tr>{f_rows}</table>

<h2>D. What we need to decide or ask</h2>
<ol>
<li><b>Booking or collection?</b> The GEW data counts the full booking. The Iteration column "Total Adjusted Collection" counts only the money collected until its date.</li>
<li><b>Which start date is right?</b> The GEW data starts revenue on the admission date, the Iteration sheet usually on the batch start date.</li>
<li><b>Students only in the Iteration sheet</b> (not in the GEW enrollment sheet, or with a later batch there): are they real admissions, and why are they not in the GEW enrollment sheet?</li>
<li><b>Finance:</b> how is the "Revenue" line worked out: on booking or collection, from which date, over how many months, with which adjustments?</li>
<li><b>Data team:</b> the flags in section C.</li>
</ol>
</body></html>"""
OUT_HTML.write_text(doc, encoding="utf-8")

browser = next((p for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")
                if pathlib.Path(p).exists()), None) or shutil.which("chrome") or shutil.which("msedge")
if not browser:
    raise SystemExit(f"Chrome/Edge not found; open {OUT_HTML} in a browser and use Print > Save as PDF (the Excel file was written)")
if OUT_PDF.exists():
    OUT_PDF.unlink()
with tempfile.TemporaryDirectory() as prof:
    subprocess.run([browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", "--virtual-time-budget=5000", f"--user-data-dir={prof}", f"--print-to-pdf={OUT_PDF}", OUT_HTML.as_uri()],
                   check=True, timeout=240, capture_output=True)
print(f"written {OUT_PDF.name} ({OUT_PDF.stat().st_size / 1024:.0f} KB) and {OUT_XLSX.name} ({OUT_XLSX.stat().st_size / 1024:.0f} KB)")

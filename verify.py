"""Independent data check.   Run:   python -I verify.py     (-I stops stray files in this folder from shadowing Python modules)

Everything is recomputed straight from the raw .xlsx files with DIFFERENT code than build_db.py / revenue_engine.py / gaps.py, then compared
with what is stored in data/gew.db (and, if the server is running, with what the API serves). A failed line means the database or a rule is wrong.
"""
import json
import pathlib
import sqlite3
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent
R = str(ROOT / "data" / "raw")
c = sqlite3.connect(str(ROOT / "data" / "gew.db"))
ok_all, results = True, []
GST = 1.18


def check(name, a, b, tol=0.5):
    global ok_all
    ok = abs(a - b) <= tol
    ok_all &= ok
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + f"{name}: raw={a:,.2f}  db={b:,.2f}")


def flag(name, bad):
    global ok_all
    ok_all &= not bad
    results.append(not bad)
    print(("PASS " if not bad else "FAIL ") + name + (f"  {bad[:4]}" if bad else ""))


def clean(df):
    df.columns = [" ".join(str(x).split()) for x in df.columns]
    return df


ym = lambda mi: f"{mi // 12:04d}-{mi % 12 + 1:02d}"
midx = lambda s: s.dt.year * 12 + s.dt.month - 1


def spread(amount, start, end, lo="2026-04", hi="2027-03"):
    """revenue per month: amount shared equally over every month start..end (both included); returns {month index: value} within lo..hi"""
    sp, ep = midx(pd.to_datetime(start)), midx(pd.to_datetime(end))
    n = (ep - sp + 1).clip(lower=1)
    per = amount / n
    ilo, ihi = int(lo[:4]) * 12 + int(lo[5:]) - 1, int(hi[:4]) * 12 + int(hi[5:]) - 1
    out = {}
    for k in range(80):
        idx = sp + k
        m = (k < n) & (idx >= ilo) & (idx <= ihi)
        for mi, v in per[m].groupby(idx[m]).sum().items():
            out[mi] = out.get(mi, 0) + v
    return out


gew = pd.ExcelFile(f"{R}/gew.xlsx")
# ---------------------------------------------------------------- 1 enrollments (admission date = new_receipt_date)
e = pd.concat([clean(pd.read_excel(gew, "Enroll Dump FY26")), clean(pd.read_excel(gew, "Enroll Dump Fy27"))]).drop_duplicates("regno", keep="last")
e["m"] = pd.to_datetime(e.new_receipt_date).dt.strftime("%Y-%m")
raw_e = e.groupby("m").size()
db_e = pd.Series(dict(c.execute("select join_month,count(*) from fact_enrollment group by 1").fetchall()))
check("enrollment rows total", len(e), db_e.sum(), 0)
flag(f"students admitted per month identical in all {len(raw_e)} months (by new_receipt_date)",
     [(m, raw_e.get(m, 0), db_e.get(m, 0)) for m in sorted(set(raw_e.index) | set(db_e.index)) if raw_e.get(m, 0) != db_e.get(m, 0)])
check("booking amount (total_payable) total", e.total_payable_cr.sum(), c.execute("select sum(total_payable) from fact_enrollment").fetchone()[0], 1)

# ---------------------------------------------------------------- 2 collections
col = pd.concat([clean(pd.read_excel(gew, "Collection Dump")), clean(pd.read_excel(f"{R}/collection.xlsx", sheet_name="Collection FY27"))])
col["m"] = pd.to_datetime(col.receipt_date).dt.strftime("%Y-%m")
raw_c = col.groupby("m").net_paid_amount.sum()
db_c = pd.Series(dict(c.execute("select month,sum(net_paid_amount) from fact_collection group by 1").fetchall()))
check("collection rows", len(col), c.execute("select count(*) from fact_collection").fetchone()[0], 0)
check("collection net amount total", col.net_paid_amount.sum(), db_c.sum(), 1)
flag(f"collection amount identical in all {len(raw_c)} months",
     [(m, round(raw_c.get(m, 0)), round(db_c.get(m, 0))) for m in sorted(set(raw_c.index) | set(db_c.index)) if abs(raw_c.get(m, 0) - db_c.get(m, 0)) > 1])

# ---------------------------------------------------------------- 3 annual plan
t = clean(pd.read_excel(gew, "Target_Long"))
check("annual plan: students planned", t["Enroll Target"].sum(), c.execute("select sum(enroll_target) from fact_target where aop_id=1").fetchone()[0], 0.01)
check("annual plan: booking planned", t["Booking Target"].sum(), c.execute("select sum(booking_target) from fact_target where aop_id=1").fetchone()[0], 1)

# ---------------------------------------------------------------- 4 the revenue rule, recomputed separately (start = new_receipt_date, end = batch_end_date)
snap = c.execute("select value from meta where key='snapshot_date'").fetchone()[0]
pay = col[pd.to_datetime(col.receipt_date) <= snap].groupby("reg_no").net_paid_amount.sum() / GST
h = e[pd.to_datetime(e.new_receipt_date) <= snap].copy()
h["amt"] = (h.total_payable_cr.fillna(0).clip(lower=0) / GST).round(2)         # revenue is on the BOOKING: total payable without GST
h["collected"] = h.regno.map(pay).fillna(0).clip(lower=0)
db_r = dict(c.execute("select month,sum(amount) from fact_revenue_monthly group by 1").fetchall())
db_h = dict(c.execute("select month,sum(amount) from fact_revenue_dump_monthly group by 1").fetchall())
this = spread(h.amt, h.new_receipt_date, h.batch_end_date)
flag("this year's revenue by month: independent recompute == database (12 months)",
     [(ym(mi), round(v), round(db_r.get(ym(mi), 0))) for mi, v in sorted(this.items()) if abs(v - db_r.get(ym(mi), 0)) > 5])
check("this year's revenue total (Rs)", sum(this.values()), sum(db_r.values()), 50)
check("students in the revenue base", len(h), c.execute("select count(*) from fact_student").fetchone()[0], 0)
check("booking without GST total (Rs): independent recompute == database", h.amt.sum(), c.execute("select sum(payable_ex_gst) from fact_student").fetchone()[0], 50)
check("money collected so far, for information (Rs)", h.collected.sum(), c.execute("select sum(adj_collection) from fact_student").fetchone()[0], 50)
hist = spread(h.amt, h.new_receipt_date, h.batch_end_date, lo="2024-01")
flag(f"history revenue by month: independent recompute == database ({len(hist)} months)",
     [(ym(mi), round(v), round(db_h.get(ym(mi), 0))) for mi, v in sorted(hist.items()) if abs(v - db_h.get(ym(mi), 0)) > 5])
check("last year (FY26) revenue (Rs)", sum(v for mi, v in hist.items() if 2025 * 12 + 3 <= mi <= 2026 * 12 + 2),
      sum(v for k, v in db_h.items() if "2025-04" <= k <= "2026-03"), 50)

# ---------------------------------------------------------------- 5 Gap Check: the P&L team's student sheet
it = pd.read_excel(f"{R}/complete.xlsx", header=4).iloc[:, 1:]
clean(it)
it = it.dropna(subset=["Reg_no"]).drop_duplicates("Reg_no", keep="last")
sdb = pd.read_sql("select reg_no, adj_collection, payable_ex_gst from ref_iter_student", c)
check("their sheet: students loaded", len(it), len(sdb), 0)
check("their sheet: Total Adjusted Collection total", it["Total Adjusted Collection"].sum(), sdb.adj_collection.sum(), 1)
check("their sheet: Final Total Payable (w/o GST) total (the booking)", it["Final Total Payable (w/o GST)"].sum(), sdb.payable_ex_gst.sum(), 1)
sheet_rev = spread(it["Total Adjusted Collection"], it["Effective Start Date"], it["Effective End Date"])
eff = dict(c.execute("select effect, sum(amount) from cmp_effect_monthly group by effect").fetchall())
check("their sheet: this year's revenue, independent recompute (Rs)", sum(sheet_rev.values()), eff.get("sheet", 0), 50)
sheet_rev_fp = spread(it["Final Total Payable (w/o GST)"], it["Effective Start Date"], it["Effective End Date"])
check("their sheet on Final Total Payable: this year's revenue, independent recompute (Rs)", sum(sheet_rev_fp.values()), eff.get("sheet_fp", 0), 50)
check("our revenue as used in the comparison == our revenue table (Rs)", sum(db_r.values()), eff.get("ours", 0), 50)
explained = sum(eff.get(k, 0) for k in ("only_ours", "only_sheet", "date", "later_payments", "amount_def"))
check("the causes add up to the gap exactly (Rs)", eff.get("ours", 0) - eff.get("sheet", 0), explained, 1)
explained_fp = sum(eff.get(k, 0) for k in ("only_ours", "only_sheet_fp", "date", "later_payments", "amount_def_fp"))
check("Final Total Payable variant: the causes add up to the gap exactly (Rs)", eff.get("ours", 0) - eff.get("sheet_fp", 0), explained_fp, 1)
ours_ids = set(h.regno[(pd.to_datetime(h.batch_end_date).clip(lower=pd.to_datetime(h.new_receipt_date)) >= "2026-04-01") & (pd.to_datetime(h.new_receipt_date) <= "2027-03-31")])
both = len(ours_ids & set(it.Reg_no.astype(int)))
nk = dict(c.execute("select kind, count(*) from cmp_student group by kind").fetchall())
flag("students on both sides / only ours / only theirs counted correctly",
     [] if (nk.get("both") == both and nk.get("only_ours") == len(ours_ids) - both and nk.get("only_sheet") == len(it) - both)
     else [("both", nk.get("both"), both), ("only_ours", nk.get("only_ours"), len(ours_ids) - both), ("only_sheet", nk.get("only_sheet"), len(it) - both)])

# ---------------------------------------------------------------- 5b the flags on the GEW data and the gap register, recomputed from the raw sheets
flagn = lambda k: (c.execute("select n_rows from dq_check where check_id=?", (k,)).fetchone() or [None])[0]
if flagn("overpaid") is not None:
    bal = pd.to_numeric(e.total_balance_cr, errors="coerce")
    check("flag: students who paid more than the fee (balance below -100)", int((bal < -100).sum()), flagn("overpaid"), 0)
    check("flag: batch starts after it ends", int((pd.to_datetime(e.batch_start_date, errors="coerce") > pd.to_datetime(e.batch_end_date, errors="coerce")).sum()), flagn("batch_start_after_end"), 0)
    jd_, nr_ = pd.to_datetime(e.joining_date, errors="coerce"), pd.to_datetime(e.new_receipt_date, errors="coerce")
    check("flag: joining_date differs from new_receipt_date", int(((jd_ != nr_) & jd_.notna() & nr_.notna()).sum()), flagn("admission_dates_disagree"), 0)
    check("flag: admission form not completed", int((e.form_status.astype(str).str.strip().str.lower() != "completed").sum()), flagn("form_incomplete"), 0)
    paysum_ = col.groupby("reg_no").net_paid_amount.sum()
    tc_ = pd.to_numeric(e.total_collection_cr, errors="coerce")
    check("flag: collected amount differs between enrollment and collection sheets (more than Rs 500)", int(((tc_ - e.regno.map(paysum_).fillna(0)).abs() > 500).sum()), flagn("collection_mismatch"), 0)
    nneg = int((col.net_paid_amount < 0).sum() + (pd.to_numeric(e.total_payable_cr, errors="coerce") < 0).sum() + (pd.to_numeric(e.total_collection_cr, errors="coerce") < 0).sum())
    check("negative payments / bookings / collected amounts in the raw sheets", nneg, flagn("negative_amounts"), 0)
reg_n = dict(c.execute("select gap_id, n from gap_register").fetchall()) if c.execute("select 1 from sqlite_master where name='gap_register'").fetchone() else {}
if reg_n:
    check("gap G-I1: students in the Iteration sheet that are not in the GEW enrollment sheet", len(set(it.Reg_no.astype(int)) - set(e.regno.astype(int))), reg_n.get("G-I1", 0), 0)
    rk = dict(c.execute("select gap_id, eff_fy_k from gap_register").fetchall()); rfp = dict(c.execute("select gap_id, eff_fy_fp from gap_register").fetchall())
    check("gap register: the gaps add up to the total (Iteration on Total Adjusted Collection, Rs)", sum(v or 0 for k_, v in rk.items() if k_.startswith("G-I")), rk["TOTAL"], 1)
    check("gap register: the total is GEW revenue minus Iteration revenue (Rs)", rk["TOTAL"], eff.get("ours", 0) - eff.get("sheet", 0), 1)
    check("gap register: the gaps add up to the total (Iteration on Final Total Payable, Rs)", sum(v or 0 for k_, v in rfp.items() if k_.startswith("G-I")), rfp["TOTAL"], 1)
    check("gap register: the total is GEW revenue minus Iteration revenue (Final Total Payable, Rs)", rfp["TOTAL"], eff.get("ours", 0) - eff.get("sheet_fp", 0), 1)

# ---------------------------------------------------------------- 6 finance P&L numbers copied correctly (only used by Gap Check)
try:
    raw = pd.read_excel(f"{R}/cost.xlsx", sheet_name="Govt exam - Offline", header=None)
    db_pl = dict(c.execute("select month,value from ref_pl_monthly where metric='revenue' and month between '2026-04' and '2026-08'").fetchall())
    hdr = next(i for i in range(len(raw)) if sum(str(v).strip().endswith("'26") for v in raw.iloc[i]) >= 3)
    row = next(i for i in range(hdr + 1, len(raw)) if any(str(v).strip() == "Revenue" for v in raw.iloc[i, :6]))
    cols = [j for j, v in enumerate(raw.iloc[hdr]) if str(v).strip() in ("April'26", "May'26", "June'26", "July'26", "Aug'26")]
    check("finance P&L revenue Apr-Aug 2026 (Cr)", sum(raw.iat[row, j] for j in cols), sum(db_pl.values()), 0.001)
except FileNotFoundError:
    print("SKIP finance P&L check (file not present)")

# ---------------------------------------------------------------- 7 the API (only if the server is running)
try:
    v = json.load(urllib.request.urlopen("http://127.0.0.1:8000/api/revenue?aop_id=1&achievement=100", timeout=15))
    check("API earned + current students == database schedule (Rs)",
          sum(sum(b["recognised"]) + sum(b["committed"]) for b in v["branches"]), sum(db_r.values()), 100)
except OSError:
    print("SKIP API check (server is not running)")

print(f"\n{sum(results)} of {len(results)} checks passed")
print("OVERALL:", "ALL CHECKS PASSED" if ok_all else "SOME CHECKS FAILED")
sys.exit(0 if ok_all else 1)

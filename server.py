"""Local API + web server.   Run:  python server.py   ->  http://127.0.0.1:8000

All numbers come from the SQLite database through revenue_engine.py; the browser only displays them.
"""
from __future__ import annotations

import base64
import csv
import datetime as dt
import hmac
import io
import json
import os
import re
import shutil
import sqlite3
import threading
from contextlib import contextmanager

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config
import gap_evidence
import revenue_engine as eng

app = FastAPI(title="GEW Revenue & P&L")

# ---- on the web server only: everybody needs a login, and the database is uploaded from the office PC with a secret token.
# On the PC none of these settings exist, so nothing changes there.
_USERS = {}
for _pair in (os.environ.get("GEW_USERS") or "").split(","):
    if ":" in _pair:
        _u, _, _p = _pair.strip().partition(":")
        if _u and _p:
            _USERS[_u] = _p
_TOKEN = os.environ.get("GEW_UPLOAD_TOKEN") or ""


@app.middleware("http")
async def _login(request: Request, call_next):
    if _USERS and request.url.path not in ("/healthz", "/api/upload_db"):       # the upload has its own token, the health check has no data
        ok = False
        h = request.headers.get("authorization", "")
        if h.lower().startswith("basic "):
            try:
                u, _, p = base64.b64decode(h[6:]).decode("utf-8").partition(":")
                ok = u in _USERS and hmac.compare_digest(p, _USERS[u])
            except Exception:
                ok = False
        if not ok:
            return Response("Login needed", status_code=401, headers={"WWW-Authenticate": 'Basic realm="GEW portal"'})
    return await call_next(request)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/api/upload_db")
async def upload_db(request: Request):
    """The office PC builds the database and sends it here (see deploy/push_to_server.py). It is checked before it replaces the live one."""
    if not _TOKEN or not hmac.compare_digest(request.headers.get("x-token", ""), _TOKEN):
        raise HTTPException(403, "wrong token")
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = config.DB_PATH.with_name(config.DB_PATH.name + ".uploading")
    size = 0
    with open(tmp, "wb") as fh:
        async for chunk in request.stream():
            fh.write(chunk)
            size += len(chunk)
    c = None
    try:
        c = sqlite3.connect(tmp)
        good = c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        for t in ("fact_enrollment", "fact_collection", "fact_student", "fact_revenue_monthly", "gap_register"):
            good = good and c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] > 0
        built = (c.execute("SELECT value FROM meta WHERE key='built_at'").fetchone() or [""])[0]
    except sqlite3.Error as e:
        good, built = False, str(e)
    finally:
        if c is not None:
            c.close()
    if not good:
        tmp.unlink(missing_ok=True)
        raise HTTPException(400, f"the uploaded database failed the checks, the live one is unchanged ({built})")
    if config.DB_PATH.exists():
        shutil.copyfile(config.DB_PATH, config.DB_PATH.with_name(config.DB_PATH.name + ".prev"))
    os.replace(tmp, config.DB_PATH)
    _CACHE.clear()
    threading.Thread(target=_warm_up, daemon=True).start()
    return {"ok": True, "bytes": size, "built_at": built}


@contextmanager
def db():
    if not config.DB_PATH.exists():
        raise HTTPException(503, "Database not built yet - run: python build_db.py")
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()



_CACHE: dict = {}


def cached(name, key, compute):
    """Heavy read-only answers are kept in memory until the database is rebuilt (meta.built_at changes)."""
    with db() as c:
        built = (c.execute("SELECT value FROM meta WHERE key='built_at'").fetchone() or [""])[0]
    k = (name, key)
    hit = _CACHE.get(k)
    if hit and hit[0] == built:
        return hit[1]
    val = compute()
    if len(_CACHE) > 200:
        _CACHE.clear()
    _CACHE[k] = (built, val)
    return val

@app.get("/api/meta")
def meta():
    with db() as c:
        info = eng.as_of_info(c)
        aops = [{"aop_id": i, "name": n, "fy": fy, "description": d, "source": s, "created_at": ca,
                 "enroll_total": et, "booking_total": bt}
                for i, n, fy, d, s, ca, et, bt in c.execute(
                    """SELECT v.aop_id, v.name, v.fy, v.description, v.source, v.created_at,
                              COALESCE(SUM(t.enroll_target),0), COALESCE(SUM(t.booking_target),0)
                       FROM aop_version v
                       LEFT JOIN (SELECT ft.* FROM fact_target ft JOIN dim_branch b USING(branch_id) WHERE b.is_active=1) t
                              USING(aop_id) GROUP BY v.aop_id ORDER BY v.aop_id""")]
        dq = [dict(zip(("source", "code", "detail", "n_rows", "amount"), r)) for r in c.execute(
            "SELECT source, code, detail, n_rows, amount FROM dq_issue ORDER BY id")]
        built = c.execute("SELECT value FROM meta WHERE key='built_at'").fetchone()
        return {"category": config.CATEGORY, "fy": config.FY_LABEL, "as_of": info, "aops": aops, "dq": dq,
                "built_at": built[0] if built else None, "months": eng.fy_months(),
                "refresh": (json.loads((config.ROOT / "data" / "last_refresh.json").read_text(encoding="utf-8"))
                            if (config.ROOT / "data" / "last_refresh.json").exists() else None)}


@app.get("/api/revenue")
def revenue(aop_id: int = 1, achievement: float = 100.0, basis: str = "trend", gst_divisor: float = config.GST_DIVISOR):
    if basis not in ("trend", "aop"):
        raise HTTPException(400, "basis must be 'trend' or 'aop'")
    if not 0 <= achievement <= 300:
        raise HTTPException(400, "achievement must be between 0 and 300")
    if not 1.0 <= gst_divisor <= 1.5:
        raise HTTPException(400, "gst_divisor must be between 1.0 and 1.5")
    def compute():
        with db() as c:
            if not c.execute("SELECT 1 FROM aop_version WHERE aop_id=?", (aop_id,)).fetchone():
                raise HTTPException(404, f"AOP {aop_id} not found")
            return eng.build_view(c, aop_id, achievement, basis, gst_divisor)
    return cached("revenue", (aop_id, achievement, basis, gst_divisor), compute)


@app.get("/api/reconcile")
def reconcile():
    """Model vs booked P&L, month by month, for every month where P&L has a value."""
    with db() as c:
        model_rev = dict(c.execute("SELECT month, SUM(amount)/1e7 FROM fact_revenue_monthly GROUP BY month"))
        model_enr = dict(c.execute(
            "SELECT join_month, COUNT(*) FROM fact_enrollment WHERE join_month>=? GROUP BY 1", (config.FY_START,)))
        out = []
        for m, v in c.execute(
                "SELECT month, value FROM ref_pl_monthly WHERE metric='revenue' AND month>=? ORDER BY month",
                (config.FY_START,)):
            enr_pl = c.execute("SELECT value FROM ref_pl_monthly WHERE metric='enrollment' AND month=?", (m,)).fetchone()
            out.append({"month": m, "pl_revenue_cr": v, "model_revenue_cr": model_rev.get(m, 0.0),
                        "pl_enrollment": enr_pl[0] if enr_pl else None, "model_enrollment": model_enr.get(m, 0)})
        return out


@app.get("/api/aov_trend")
def aov_trend(aop_id: int = 1):
    with db() as c:
        return eng.aov_trend(c, aop_id)


def _vintage_compute():
    """Branches grouped by age (month of their first student). Everything is built from the enrollment dump and the history revenue base."""
    with db() as c:
        info = eng.as_of_info(c)
        last = info["last_actual_month"]
        data_start = c.execute("SELECT MIN(join_month) FROM fact_enrollment").fetchone()[0]
        months = [eng.idx_ym(i) for i in range(eng.ym_idx(data_start), eng.ym_idx(last) + 1)]
        this_m = [m for m in months if m >= config.FY_START]
        last_m = [eng.idx_ym(eng.ym_idx(m) - 12) for m in this_m]
        brs = c.execute("""SELECT b.branch_id, b.branch_name, b.short_name, MIN(e.join_month) FROM dim_branch b
                           JOIN fact_enrollment e USING(branch_id) WHERE b.is_active=1 GROUP BY b.branch_id""").fetchall()
        enr, pay, npay, rev = {}, {}, {}, {}
        for b_, m, n, p in c.execute("SELECT branch_id, join_month, COUNT(*), SUM(total_payable) FROM fact_enrollment WHERE join_month<=? GROUP BY 1,2", (last,)):
            enr[(b_, m)] = n
        for b_, m, n, p in c.execute("""SELECT branch_id, join_month, COUNT(*), SUM(total_payable) FROM fact_enrollment
                                        WHERE join_month<=? AND total_payable>=? GROUP BY 1,2""", (last, config.MOCK_BILL_THRESHOLD)):
            npay[(b_, m)], pay[(b_, m)] = n, p
        for b_, m, v in c.execute("SELECT branch_id, month, SUM(amount) FROM fact_revenue_dump_monthly WHERE month<=? GROUP BY 1,2", (last,)):
            rev[(b_, m)] = v

        def group_of(first):
            for i, (_, lo, hi) in enumerate(config.VINTAGE_GROUPS):
                if (lo is None or first >= lo) and (hi is None or first <= hi):
                    return i
            return len(config.VINTAGE_GROUPS) - 1

        out = []
        for gi, (label, _, _) in enumerate(config.VINTAGE_GROUPS):
            mem = [(b_, name, short or name, first) for b_, name, short, first in brs if group_of(first) == gi]
            if not mem:
                continue
            spb, revs = [], []
            for m in months:
                active = [x for x in mem if x[3] <= m]
                spb.append(round(sum(enr.get((x[0], m), 0) for x in active) / len(active), 2) if active else None)
                revs.append(round(sum(rev.get((x[0], m), 0.0) for x in mem), 2))
            curve = []
            for k in range(1, 13):
                vals = [enr.get((x[0], eng.idx_ym(eng.ym_idx(x[3]) + k - 1)), 0) for x in mem
                        if x[3] > data_start and eng.ym_idx(x[3]) + k - 1 <= eng.ym_idx(last)]
                curve.append({"k": k, "avg": round(sum(vals) / len(vals), 1) if vals else None, "n": len(vals)})

            def window(ms):
                st = sum(enr.get((x[0], m), 0) for x in mem for m in ms)
                bm = sum(1 for x in mem for m in ms if m >= x[3])
                rv = sum(rev.get((x[0], m), 0.0) for x in mem for m in ms)
                pn = sum(npay.get((x[0], m), 0) for x in mem for m in ms)
                pp = sum(pay.get((x[0], m), 0.0) for x in mem for m in ms)
                return {"students": st, "branch_months": bm, "revenue": rv, "avg_fee": (pp / pn) if pn else None}

            out.append({"label": label, "branches": [{"name": x[1], "short": x[2], "first_month": x[3]} for x in sorted(mem, key=lambda z: z[3])],
                        "students_per_branch": spb, "revenue": revs, "curve": curve,
                        "this_year": window(this_m), "last_year": window(last_m)})
        total_rev_this = sum(g["this_year"]["revenue"] for g in out)
        return {"as_of": info, "months": months, "this_months": this_m, "data_starts": data_start, "groups": out, "total_revenue_this_year": total_rev_this}


@app.get("/api/gaps")
def gaps_summary():
    """Our numbers vs the P&L team's sheets (see gaps.py for how the revenue gap is split into causes that add up exactly)."""
    with db() as c:
        if not c.execute("SELECT 1 FROM cmp_student LIMIT 1").fetchone():
            return {"available": False}
        months = eng.fy_months()
        meta_ = dict(c.execute("SELECT key, value FROM meta"))
        eff_total = dict(c.execute("SELECT effect, SUM(amount) FROM cmp_effect_monthly GROUP BY effect"))
        eff_month = {}
        for e, m, v in c.execute("SELECT effect, month, SUM(amount) FROM cmp_effect_monthly GROUP BY 1, 2"):
            eff_month.setdefault(e, {})[m] = v
        reasons = [dict(zip(("kind", "reason", "n", "rev_ours", "rev_sheet"), r)) for r in c.execute(
            """SELECT kind, COALESCE(reason, ''), COUNT(*), COALESCE(SUM(rev_ours), 0), COALESCE(SUM(rev_sheet), 0)
               FROM cmp_student GROUP BY 1, 2 ORDER BY CASE kind WHEN 'both' THEN 0 WHEN 'only_ours' THEN 1 ELSE 2 END, 3 DESC""")]
        d = c.execute("""SELECT COUNT(*), SUM(start_diff_days = 0), SUM(start_diff_days != 0 AND start_month_differs = 0), SUM(start_month_differs = 1),
                                SUM(end_diff_days = 0), SUM(end_diff_days != 0 AND end_month_differs = 0), SUM(end_month_differs = 1),
                                SUM(start_diff_days < 0), SUM(start_diff_days > 0)
                         FROM cmp_student WHERE kind = 'both'""").fetchone()
        dates = dict(zip(("both", "start_same_day", "start_same_month", "start_other_month", "end_same_day", "end_same_month", "end_other_month",
                          "start_ours_earlier", "start_ours_later"), d))
        branches = []
        eff_b = {}
        for b_, e, v in c.execute("SELECT branch_id, effect, SUM(amount) FROM cmp_effect_monthly GROUP BY 1, 2"):
            eff_b.setdefault(b_, {})[e] = v
        for b_, name, cohort in c.execute("SELECT branch_id, branch_name, cohort_id FROM dim_branch ORDER BY cohort_id, branch_name"):
            n = dict(c.execute("SELECT kind, COUNT(*) FROM cmp_student WHERE branch_id=? GROUP BY 1", (b_,)).fetchall())
            e = eff_b.get(b_, {})
            branches.append({"name": name, "cohort_id": cohort, "both": n.get("both", 0), "only_ours": n.get("only_ours", 0), "only_sheet": n.get("only_sheet", 0),
                             "ours": e.get("ours", 0.0), "sheet": e.get("sheet", 0.0), "only_ours_rev": e.get("only_ours", 0.0), "only_sheet_rev": e.get("only_sheet", 0.0),
                             "date": e.get("date", 0.0), "later_payments": e.get("later_payments", 0.0), "amount_def": e.get("amount_def", 0.0)})
        # the horizontal P&L sheet, month by month
        pl = {}
        for m, metric, v in c.execute("SELECT month, metric, value FROM ref_pl_monthly WHERE month BETWEEN ? AND ? AND metric IN ('revenue','enrollment','net_collection')",
                                      (config.FY_START, config.FY_END)):
            pl.setdefault(m, {})[metric] = v
        ours_rev = dict(c.execute("SELECT month, SUM(amount) FROM fact_revenue_monthly GROUP BY month"))
        ours_enr = dict(c.execute("SELECT join_month, COUNT(*) FROM fact_enrollment WHERE join_month BETWEEN ? AND ? GROUP BY 1", (config.FY_START, config.FY_END)))
        ours_col = dict(c.execute("SELECT month, SUM(net_paid_amount) FROM fact_collection WHERE month BETWEEN ? AND ? GROUP BY 1", (config.FY_START, config.FY_END)))
        plrows = [{"month": m, "pl_revenue_cr": v.get("revenue"), "pl_enrollment": v.get("enrollment"), "pl_collection_cr": v.get("net_collection"),
                   "ours_revenue_cr": ours_rev.get(m, 0.0) / 1e7, "ours_enrollment": ours_enr.get(m, 0),
                   "ours_collection_cr": ours_col.get(m, 0.0) / config.GST_DIVISOR / 1e7} for m, v in sorted(pl.items())]
        # one real student for each cause, so the page can show "for example ..."
        def one(sql, keys):
            r = c.execute(sql).fetchone()
            return dict(zip(keys, r)) if r else None
        J = "FROM cmp_student s JOIN dim_branch d USING(branch_id) WHERE "
        examples = {
            "admitted_after": one("SELECT s.reg_no, d.branch_name, s.start_ours, s.amt_ours_all, s.rev_ours " + J +
                                  "s.kind='only_ours' AND s.reason LIKE 'admitted after%' AND s.rev_ours > 1000 ORDER BY s.reg_no LIMIT 1",
                                  ("reg_no", "branch", "start_gew", "amount", "revenue")),
            "later_payments": one("SELECT s.reg_no, d.branch_name, s.amt_ours_cut, s.amt_ours_all, s.eff_later " + J +
                                  "s.kind='both' AND s.eff_later > 500 ORDER BY s.reg_no LIMIT 1",
                                  ("reg_no", "branch", "amount_then", "amount_now", "revenue")),
            "missing_in_pl": one("SELECT s.reg_no, d.branch_name, s.start_ours, s.end_ours, s.amt_ours_all, s.rev_ours " + J +
                                 "s.kind='only_ours' AND s.reason LIKE 'admitted before%' ORDER BY s.reg_no LIMIT 1",
                                 ("reg_no", "branch", "start_gew", "end_gew", "amount", "revenue")),
            "not_in_gew": one("SELECT s.reg_no, d.branch_name, s.start_sheet, s.end_sheet, s.amt_sheet " + J +
                              "s.kind='only_sheet' AND s.reason LIKE 'not in%' ORDER BY s.reg_no LIMIT 1", ("reg_no", "branch", "start_pl", "end_pl", "amount")),
            "gew_end_earlier": one("SELECT s.reg_no, d.branch_name, s.start_sheet, s.end_sheet, s.amt_sheet " + J +
                                   "s.kind='only_sheet' AND s.reason LIKE 'in the GEW%' ORDER BY s.reg_no LIMIT 1", ("reg_no", "branch", "start_pl", "end_pl", "amount")),
            "dates_differ": one("SELECT s.reg_no, d.branch_name, s.start_ours, s.start_sheet, s.end_ours, s.end_sheet, s.eff_date " + J +
                                "s.kind='both' AND s.start_month_differs=1 AND ABS(s.eff_date) > 500 ORDER BY s.reg_no LIMIT 1",
                                ("reg_no", "branch", "start_gew", "start_pl", "end_gew", "end_pl", "revenue")),
        }
        other = [dict(zip(("check_id", "title", "severity", "n_rows", "amount", "detail", "who"), r)) for r in c.execute(
            "SELECT check_id, title, severity, n_rows, amount, detail, who FROM dq_check ORDER BY CASE severity WHEN 'note' THEN 0 WHEN 'ask' THEN 1 WHEN 'info' THEN 2 WHEN 'ok' THEN 3 ELSE 4 END, n_rows DESC, check_id")]
        with_rows = {r[0] for r in c.execute("SELECT DISTINCT flag_id FROM dq_flag_row")} if c.execute("SELECT 1 FROM sqlite_master WHERE name='dq_flag_row'").fetchone() else set()
        for o_ in other:
            o_['flag_list'] = o_['check_id'] in with_rows
        return {"available": True, "other_checks": other, "examples": examples, "months": months, "sheet_prepared_on": meta_.get("iter_asof"), "amount_match_rate": float(meta_.get("iter_booking_match_rate", 0) or 0),
                "data_till": meta_.get("snapshot_date"), "effects_total": eff_total, "effects_month": eff_month, "reasons": reasons, "dates": dates,
                "branches": branches, "pl_rows": plrows}


@app.get("/api/gaps/students.csv", response_class=PlainTextResponse)
def gaps_students_csv(kind: str = "all", only_differences: bool = False):
    """Student-level gap list for Excel: kind = all / both / only_ours / only_sheet."""
    if kind not in ("all", "both", "only_ours", "only_sheet"):
        raise HTTPException(400, "kind must be all, both, only_ours or only_sheet")
    with db() as c:
        sql = """SELECT CASE s.kind WHEN 'both' THEN 'on both' WHEN 'only_ours' THEN 'only in GEW data' ELSE 'only in Iteration data' END, COALESCE(s.reason,''), s.reg_no, b.branch_name, s.start_ours, s.start_sheet, s.start_diff_days, s.end_ours, s.end_sheet, s.end_diff_days,
                        s.amt_ours_all, s.amt_ours_cut, s.amt_sheet, s.rev_ours, s.rev_sheet, s.eff_date, s.eff_later, s.eff_amount
                 FROM cmp_student s JOIN dim_branch b USING(branch_id) WHERE 1=1"""
        if kind != "all":
            sql += f" AND s.kind = '{kind}'"
        if only_differences:
            sql += " AND (s.kind != 'both' OR s.start_diff_days != 0 OR s.end_diff_days != 0 OR ABS(COALESCE(s.eff_amount,0)) > 1 OR ABS(COALESCE(s.eff_later,0)) > 1)"
        sql += " ORDER BY s.kind, b.branch_name, s.reg_no"
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(["where_is_the_student", "reason", "student_no", "branch", "start_gew", "start_pl", "start_gew_minus_pl_days", "end_gew", "end_pl",
                    "end_gew_minus_pl_days", "amount_gew_all_payments", "amount_gew_payments_up_to_pl_date", "amount_in_pl_data", "revenue_this_year_gew",
                    "revenue_this_year_pl", "gap_from_dates", "gap_from_later_payments", "gap_from_amount"])
        for r in c.execute(sql):
            w.writerow([round(x, 2) if isinstance(x, float) else x for x in r])
        return PlainTextResponse(buf.getvalue(), headers={"Content-Disposition": f'attachment; filename="gap_students_{kind}.csv"'})


def _history_compute():
    """Year-on-year series built ONLY from the live dumps (same method for both years), plus the booked P&L for checking."""
    fy26 = [eng.idx_ym(i) for i in range(eng.ym_idx("2025-04"), eng.ym_idx("2026-03") + 1)]
    fy27 = eng.fy_months()
    with db() as c:
        info = eng.as_of_info(c)
        brs = c.execute("SELECT branch_id, branch_name, short_name, cohort_id FROM dim_branch WHERE is_active=1 ORDER BY cohort_id, branch_name").fetchall()
        rev, enr, pay = {}, {}, {}
        for b, m, v in c.execute("SELECT branch_id, month, SUM(amount) FROM fact_revenue_dump_monthly WHERE month BETWEEN '2025-04' AND ? GROUP BY 1,2", (config.FY_END,)):
            rev[(b, m)] = v
        for b, m, n, p in c.execute("""SELECT branch_id, join_month, COUNT(*), SUM(total_payable) FROM fact_enrollment
                                       WHERE join_month BETWEEN '2025-04' AND ? AND total_payable >= ? GROUP BY 1,2""",
                                    (config.FY_END, config.MOCK_BILL_THRESHOLD)):
            enr[(b, m)], pay[(b, m)] = n, p
        out = []
        for b, name, short, cohort in brs:
            row = {"branch_id": b, "name": name, "short": short or name, "cohort_id": cohort}
            for tag, ms in (("26", fy26), ("27", fy27)):
                row["rev" + tag] = [round(rev.get((b, m), 0.0), 2) for m in ms]
                row["enr" + tag] = [enr.get((b, m), 0) for m in ms]
                row["pay" + tag] = [round(pay.get((b, m), 0.0), 2) for m in ms]
            out.append(row)
        pl = {m: {"revenue_cr": v} for m, v in c.execute("SELECT month, value FROM ref_pl_monthly WHERE metric='revenue'")}
        for m, v in c.execute("SELECT month, value FROM ref_pl_monthly WHERE metric='enrollment'"):
            pl.setdefault(m, {})["enrollment"] = v
        n_students = c.execute("SELECT COUNT(*) FROM fact_student_dump").fetchone()[0]
        return {"as_of": info, "months26": fy26, "months27": fy27, "branches": out, "pl": pl, "students": n_students}


# ------------------------------------------------------------------ AOP management
class AopRow(BaseModel):
    center: str
    month: str
    enroll_target: float
    aov: float | None = None
    booking_target: float | None = None


class AopUpload(BaseModel):
    name: str
    description: str | None = None
    overwrite: bool = False
    rows: list[AopRow]


def _norm_month(s: str) -> str:
    s = s.strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})(?:-\d{1,2})?(?:[ T].*)?", s)
    if not m:
        raise ValueError(f"month '{s}' must look like 2026-04 or 2026-04-01")
    y, mo = int(m.group(1)), int(m.group(2))
    if not 1 <= mo <= 12:
        raise ValueError(f"month '{s}' is not a real month")
    return f"{y:04d}-{mo:02d}"


@app.get("/api/targets")
def targets_for_dashboard(aop_id: int = 1):
    """One saved plan in the same shape as the sheet's Target_Long tab (used by the Executive Dashboard's plan selector)."""
    mon = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    with db() as c:
        if not c.execute("SELECT 1 FROM aop_version WHERE aop_id=?", (aop_id,)).fetchone():
            raise HTTPException(404, f"plan {aop_id} not found")
        return [{"Center": n, "Month": f"1-{mon[int(m[5:7]) - 1]}-{m[:4]}", "Enroll Target": e, "Booking Target": bk}
                for n, m, e, bk in c.execute(
                    """SELECT b.branch_name, t.month, t.enroll_target, t.booking_target FROM fact_target t
                       JOIN dim_branch b USING(branch_id) WHERE t.aop_id=? ORDER BY b.cohort_id, b.branch_name, t.month""", (aop_id,))]


@app.get("/api/aop/template.csv", response_class=PlainTextResponse)
def aop_template():
    """Pre-filled with AOP1 so users only edit numbers. Columns: center,month,enroll_target,aov"""
    with db() as c:
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(["center", "month", "enroll_target", "aov"])
        for name, m, e, a in c.execute(
                """SELECT b.branch_name, t.month, t.enroll_target, t.aov FROM fact_target t
                   JOIN dim_branch b USING(branch_id) WHERE t.aop_id=1 AND b.is_active=1 ORDER BY b.cohort_id, b.branch_name, t.month"""):
            w.writerow([name, m, e, a])
        return PlainTextResponse(buf.getvalue(), headers={"Content-Disposition": 'attachment; filename="aop_template.csv"'})


@app.post("/api/aop")
def aop_upload(body: AopUpload):
    name = body.name.strip()
    if not re.fullmatch(r"[A-Za-z0-9 _.-]{1,30}", name):
        raise HTTPException(400, "AOP name: 1-30 letters/numbers/space/_/-/.")
    if name.upper() == "AOP1":
        raise HTTPException(400, "AOP1 is the original plan and is protected; pick another name (e.g. AOP2)")
    with db() as c:
        branches = dict(c.execute("SELECT branch_name, branch_id FROM dim_branch WHERE is_active=1"))
        closed = {n for (n,) in c.execute("SELECT branch_name FROM dim_branch WHERE is_active=0")}
        problems, rows = [], {}
        fy = set(eng.fy_months())
        for i, r in enumerate(body.rows, start=1):
            try:
                bid = branches.get(r.center.strip())
                if bid is None:
                    raise ValueError(f"branch '{r.center}' is closed - remove it from the file" if r.center.strip() in closed
                                     else f"unknown branch '{r.center}'")
                m = _norm_month(r.month)
                if m not in fy:
                    raise ValueError(f"month {m} is outside {config.FY_LABEL}")
                if r.enroll_target < 0:
                    raise ValueError("enroll_target is negative")
                if r.aov is not None and r.aov > 0:
                    aov = r.aov
                elif r.booking_target is not None and r.enroll_target > 0:
                    aov = r.booking_target / r.enroll_target
                elif r.enroll_target == 0:
                    aov = 0.0
                else:
                    raise ValueError("give aov (or booking_target) for rows with enrollments")
                if (bid, m) in rows:
                    raise ValueError(f"duplicate branch+month ({r.center}, {m})")
                rows[(bid, m)] = (r.enroll_target, aov, r.enroll_target * aov)
            except ValueError as e:
                problems.append(f"row {i}: {e}")
        if problems:
            raise HTTPException(422, {"message": f"{len(problems)} problem(s); nothing was saved", "problems": problems[:30]})
        if not rows:
            raise HTTPException(422, "no rows")
        existing = c.execute("SELECT aop_id FROM aop_version WHERE name=?", (name,)).fetchone()
        if existing and not body.overwrite:
            raise HTTPException(409, f"'{name}' already exists - tick overwrite or choose a new name")
        with c:                                            # one transaction: all or nothing
            if existing:
                aop_id = existing[0]
                c.execute("DELETE FROM fact_target WHERE aop_id=?", (aop_id,))
            else:
                aop_id = c.execute("SELECT COALESCE(MAX(aop_id),0)+1 FROM aop_version").fetchone()[0]
                c.execute("INSERT INTO aop_version VALUES (?,?,?,?,?,?)",
                          (aop_id, name, config.FY_LABEL, body.description, "upload",
                           dt.datetime.now().isoformat(timespec="seconds")))
            c.executemany("INSERT INTO fact_target VALUES (?,?,?,?,?,?)",
                          [(aop_id, b, m, *v) for (b, m), v in rows.items()])
        covered = {b for b, _ in rows}
        missing = [n for n, b in branches.items() if b not in covered]
        return {"aop_id": aop_id, "name": name, "rows": len(rows),
                "enroll_total": sum(v[0] for v in rows.values()), "booking_total": sum(v[2] for v in rows.values()),
                "branches_without_targets": missing}


@app.delete("/api/aop/{aop_id}")
def aop_delete(aop_id: int):
    if aop_id == 1:
        raise HTTPException(400, "AOP1 is protected")
    with db() as c:
        with c:
            n = c.execute("DELETE FROM fact_target WHERE aop_id=?", (aop_id,)).rowcount
            c.execute("DELETE FROM aop_version WHERE aop_id=?", (aop_id,))
        return {"deleted_rows": n}


@app.get("/api/vintage")
def vintage():
    return cached("vintage", (), _vintage_compute)


_DQ_CSV = {
    "orphan_payments": ("SELECT p.receipt_date, p.month, b.branch_name, p.reg_no, p.payment_mode, p.paid_amount, p.net_paid_amount, p.refund_amount, p.src FROM dq_orphan_payment p LEFT JOIN dim_branch b USING(branch_id) ORDER BY p.receipt_date",
                        ["receipt_date", "month", "branch", "student_no", "payment_mode", "paid_amount", "net_paid_amount", "refund_amount", "source_tab"]),
    "duplicate_payments": ("SELECT d.reg_no, b.branch_name, d.receipt_date, d.payment_mode, d.net_paid_amount, d.times_seen, d.extra_amount FROM dq_duplicate_payment d LEFT JOIN dim_branch b USING(branch_id) ORDER BY d.extra_amount DESC",
                           ["student_no", "branch", "receipt_date", "payment_mode", "net_paid_amount", "times_seen", "extra_amount_if_duplicate"]),
    "removed_payments": ("SELECT receipt_date, month, branch_name, reg_no, payment_mode, paid_amount, net_paid_amount, refund_amount, src, first_seen_on, removed_on FROM archive_collection_removed ORDER BY removed_on, receipt_date",
                         ["receipt_date", "month", "branch", "student_no", "payment_mode", "paid_amount", "net_paid_amount", "refund_amount", "source_tab", "first_seen_on", "removed_on"]),
    "removed_students": ("SELECT reg_no, branch_name, scheme, course, joining_date, batch_end, total_payable, total_collection, src, first_seen_on, removed_on FROM archive_enrollment_removed ORDER BY removed_on, reg_no",
                         ["student_no", "branch", "scheme", "course", "admission_date", "batch_end_date", "total_payable", "total_collection", "source_tab", "first_seen_on", "removed_on"]),
    "bad_dates": ("SELECT s.reg_no, b.branch_name, s.eff_start, s.eff_end, s.amount, s.days_between FROM dq_student_bad_dates s LEFT JOIN dim_branch b USING(branch_id) ORDER BY s.amount DESC",
                  ["student_no", "branch", "admission_date", "batch_end_date", "amount_without_gst", "days_between_admission_and_end"]),
}


@app.get("/api/dq/{name}.csv", response_class=PlainTextResponse)
def dq_csv(name: str):
    if name not in _DQ_CSV:
        raise HTTPException(404, "unknown list")
    sql, header = _DQ_CSV[name]
    with db() as c:
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(header)
        for r in c.execute(sql):
            w.writerow([round(x, 2) if isinstance(x, float) else x for x in r])
        return PlainTextResponse(buf.getvalue(), headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})


def _csv_response(header, rows, filename):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for r in rows:
        w.writerow([round(x, 2) if isinstance(x, float) else x for x in r])
    return PlainTextResponse("\ufeff" + buf.getvalue(), headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/gap_register")
def gap_register():
    """Every gap between the GEW data, the Iteration data and Finance's P&L, with the numbers and a real example (built by gap_report.py)."""
    def compute():
        with db() as c:
            if not c.execute("SELECT 1 FROM sqlite_master WHERE name='gap_register'").fetchone() or not c.execute("SELECT 1 FROM gap_register LIMIT 1").fetchone():
                return {"available": False}
            meta_ = dict(c.execute("SELECT key, value FROM meta"))
            cols = ["gap_id", "grp", "sort", "title", "plain", "n", "unit", "eff_fy_k", "eff_win_k", "eff_fy_fp", "eff_win_fp", "window", "owner", "proof_reg", "proof_text", "values_json", "evidence"]
            rows = []
            for r in c.execute(f"SELECT {', '.join(cols)} FROM gap_register ORDER BY sort"):
                d = dict(zip(cols, r))
                d["values"] = json.loads(d.pop("values_json")) if d["values_json"] else None
                rows.append(d)
            hist = {}
            for gid, day, n, k, fp in c.execute("SELECT gap_id, load_date, n, eff_fy_k, eff_fy_fp FROM gap_history ORDER BY load_date"):
                hist.setdefault(gid, []).append({"date": day, "n": n, "k": k, "fp": fp})
            flags = [dict(zip(("check_id", "title", "severity", "n_rows", "amount", "detail", "who"), r)) for r in c.execute(
                "SELECT check_id, title, severity, n_rows, amount, detail, who FROM dq_check ORDER BY CASE severity WHEN 'note' THEN 0 WHEN 'ask' THEN 1 WHEN 'info' THEN 2 WHEN 'ok' THEN 3 ELSE 4 END, n_rows DESC")]
            fin = c.execute("SELECT MAX(month) FROM ref_pl_monthly WHERE metric='revenue' AND month BETWEEN ? AND ?", (config.FY_START, config.FY_END)).fetchone()[0]
            return {"available": True, "rows": rows, "history": hist, "flags": flags, "snapshot_date": meta_.get("snapshot_date"), "iter_asof": meta_.get("iter_asof"),
                    "built_at": meta_.get("built_at"), "finance_last_month": fin}
    return cached("gap_register", (), compute)


@app.get("/api/gap_evidence/{gap_id}.csv", response_class=PlainTextResponse)
def gap_evidence_csv(gap_id: str):
    if gap_id not in gap_evidence.BY_ID:
        raise HTTPException(404, "unknown gap")
    with db() as c:
        header, rows = gap_evidence.evidence(c, gap_id)
    return _csv_response(header, rows, f"gap_{gap_id}.csv")


@app.get("/api/flag/{flag_id}.csv", response_class=PlainTextResponse)
def flag_csv(flag_id: str):
    with db() as c:
        header, rows = gap_evidence.flag_rows(c, flag_id)
    if not rows:
        raise HTTPException(404, "no rows for this flag")
    return _csv_response(header, rows, f"flag_{flag_id}.csv")


@app.get("/api/gap_report.xlsx")
def gap_report_xlsx():
    """One Excel file with every gap and every flag and the students behind each (to attach to a mail)."""
    def compute():
        with db() as c:
            return gap_evidence.build_workbook(c)
    data = cached("gap_xlsx", (), compute)
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="GEW_gap_report.xlsx"'})


@app.get("/api/history")
def history():
    return cached("history", (), _history_compute)


def _warm_up():
    """Work out the heavy pages' answers once at start-up, so the first visitor does not wait."""
    try:
        revenue(1, 100.0, "trend", config.GST_DIVISOR)
        history()
        vintage()
    except Exception:
        pass


@app.on_event("startup")
def _start_warm_up():
    import threading
    threading.Thread(target=_warm_up, daemon=True).start()


# static pages last so /api/* wins
app.mount("/", StaticFiles(directory=config.ROOT / "web", html=True), name="web")

if __name__ == "__main__":
    import sys
    if sys.stdout is None or sys.stderr is None:      # started without a console (pythonw / Windows task): write to a log file instead
        _log = open(config.ROOT / "data" / "server.log", "a", buffering=1, encoding="utf-8")
        sys.stdout = sys.stdout or _log
        sys.stderr = sys.stderr or _log
    uvicorn.run(app, host=os.environ.get("GEW_HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8000")))

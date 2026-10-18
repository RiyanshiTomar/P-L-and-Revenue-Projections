"""Build / rebuild the SQLite database from the source workbooks.

    python build_db.py

Idempotent: it builds a NEW database from data/raw/*.xlsx (the two main Google Sheets) and swaps it in only if the checks pass.
(refresh those files first with `python fetch_sheets.py` if the Google Sheets changed).
Every row that looks wrong is KEPT but recorded in dq_issue / fact_student.dq_flag - nothing is dropped silently.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import sqlite3
import sys

import pandas as pd

import config
import history
import revenue_engine as eng


def log(msg):
    print(msg, flush=True)


def ym(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce").dt.strftime("%Y-%m")


def iso(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce").dt.strftime("%Y-%m-%d")


def read_tab(src, sheet, **kw) -> pd.DataFrame:
    """read_excel + strip stray whitespace/newlines from column names (the sheets have e.g. 'Branch Name ')."""
    df = pd.read_excel(src, sheet, **kw)
    df.columns = [re.sub(r"\s+", " ", str(c)).strip() for c in df.columns]
    return df


def add_dq(conn, source, code, detail, n_rows=None, amount=None):
    conn.execute("INSERT INTO dq_issue(source, code, detail, n_rows, amount) VALUES (?,?,?,?,?)",
                 (source, code, detail, n_rows, amount))


def branch_ids(conn, names: pd.Series, source: str) -> pd.Series:
    mp = dict(conn.execute("SELECT branch_name, branch_id FROM dim_branch"))
    stripped = names.astype(str).str.strip()
    ids = stripped.map(mp)
    bad = sorted(set(stripped[ids.isna()]))
    if bad:
        raise SystemExit(f"[{source}] branch names not in dim_branch (fix config.COHORT_MAPPING or the sheet): {bad}")
    return ids.astype(int)


# ------------------------------------------------------------------ loaders
def load_branches(conn, gew):
    et = read_tab(gew, "Enroll Target")
    short = dict(zip(et["Branch"].astype(str).str.strip(), et["Branch Name"].astype(str).str.strip()))
    rows = [(i + 1, name, short.get(name), cohort, config.CATEGORY, 0 if name in config.CLOSED_BRANCHES else 1)
            for i, (name, cohort) in enumerate(sorted(config.COHORT_MAPPING.items(), key=lambda kv: (kv[1], kv[0])))]
    conn.executemany("INSERT INTO dim_branch VALUES (?,?,?,?,?,?)", rows)
    log(f"dim_branch: {len(rows)} branches ({sum(r[5] for r in rows)} active; closed: {sorted(config.CLOSED_BRANCHES)})")


def load_aop1(conn, gew):
    t = read_tab(gew, "Target_Long")
    t = t.dropna(subset=["Center", "Month"])
    t["bid"] = branch_ids(conn, t["Center"], "Target_Long")
    t["ym"] = ym(t["Month"])
    if t["ym"].isna().any():
        raise SystemExit("Target_Long has unparseable months")
    conn.execute("INSERT INTO aop_version(aop_id, name, fy, description, source, created_at) VALUES (1,'AOP1',?,?,?,?)",
                 (config.FY_LABEL, "Original annual operating plan (Target_Long tab)", "sheet:Target_Long",
                  dt.datetime.now().isoformat(timespec="seconds")))
    t["AOV"] = pd.to_numeric(t["AOV"], errors="coerce")             # sheet has #DIV/0! where target is 0 (0/0)
    broken = t["AOV"].isna()
    if (broken & (t["Enroll Target"] > 0)).any():
        raise SystemExit("Target_Long: AOV is an error although Enroll Target > 0 - fix the sheet")
    if broken.any():
        fill = t.groupby("bid")["AOV"].transform("median")           # branch's own AOV; irrelevant while target is 0
        t["AOV"] = t["AOV"].fillna(fill).fillna(0.0)
        add_dq(conn, "Target_Long", "AOV_DIV_BY_ZERO", "AOV was #DIV/0! (enroll target 0); filled with branch median AOV", int(broken.sum()))
    bad = (t["Enroll Target"] * t["AOV"] - t["Booking Target"]).abs() > 1.0
    if bad.any():
        add_dq(conn, "Target_Long", "BOOKING_NE_ENROLL_X_AOV", "Booking Target differs from Enroll Target x AOV", int(bad.sum()))
    conn.executemany("INSERT INTO fact_target VALUES (1,?,?,?,?,?)",
                     [(int(r.bid), r.ym, float(r._3), float(r.AOV), float(r._4))
                      for r in t.rename(columns={"Enroll Target": "_3", "Booking Target": "_4"}).itertuples()])
    log(f"AOP1: {len(t)} target rows, enroll {t['Enroll Target'].sum():,.0f}, booking Rs {t['Booking Target'].sum()/1e7:,.2f} Cr")


def load_enrollments(conn, gew):
    parts = []
    for tab, src in [("Enroll Dump FY26", "FY26"), ("Enroll Dump Fy27", "FY27")]:
        e = read_tab(gew, tab)
        e["src"] = src
        parts.append(e)
    for part, tab in zip(parts, ("Enroll Dump FY26", "Enroll Dump Fy27")):
        for col in ("new_receipt_date", "batch_end_date"):
            blank = int(pd.to_datetime(part[col], errors="coerce").isna().sum())
            if blank > 0.02 * len(part):
                raise SystemExit(f"SOURCE SHEET PROBLEM: tab '{tab}' has {blank:,} of {len(part):,} rows with no '{col}'. "
                                 f"The database was NOT updated and keeps the last good data. Please check that tab in the sheet.")
    e = pd.concat(parts, ignore_index=True)
    d = e["regno"].duplicated(keep="last")
    if d.any():
        add_dq(conn, "Enroll dumps", "DUPLICATE_REGNO", "same regno in FY26 and FY27 dump; latest kept", int(d.sum()))
        e = e[~d]
    e["bid"] = branch_ids(conn, e["center"], "Enroll dumps")
    e["jd"], e["jm"] = iso(e["new_receipt_date"]), ym(e["new_receipt_date"])      # admission date = effective start date
    e["bs"], e["be"] = iso(e["batch_start_date"]), iso(e["batch_end_date"])
    if e["jd"].isna().any():
        add_dq(conn, "Enroll dumps", "BAD_ADMISSION_DATE", "rows dropped: new_receipt_date (admission date) missing/unparseable", int(e["jd"].isna().sum()))
        e = e.dropna(subset=["jd"])
    nn = lambda v: None if pd.isna(v) else v
    rows = [(int(r.regno), int(r.bid), str(r.scheme), str(r.course), r.jd, r.jm, nn(r.bs),
             nn(r.be), str(r.schemes_status), float(r.total_collection_cr), float(r.total_payable_cr), r.src)
            for r in e.itertuples()]
    conn.executemany("INSERT INTO fact_enrollment (reg_no, branch_id, scheme, course, joining_date, join_month, batch_start, batch_end, status, total_collection, total_payable, src) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    log(f"fact_enrollment: {len(rows)} rows ({e['jm'].min()} .. {e['jm'].max()})")


def load_collections(conn, gew):
    hist = read_tab(gew, "Collection Dump"); hist["src"] = "FY26"
    cur = read_tab(config.SOURCES["collection"], "Collection FY27"); cur["src"] = "FY27"
    gew_cur = read_tab(gew, "Collection FY27")
    if len(gew_cur) != len(cur):
        add_dq(conn, "Collection FY27", "ROW_COUNT_MISMATCH",
               f"collection.xlsx has {len(cur)} rows, gew.xlsx tab has {len(gew_cur)}; collection.xlsx used", abs(len(cur) - len(gew_cur)))
    c = pd.concat([hist, cur], ignore_index=True)
    c["bid"] = branch_ids(conn, c["center"], "Collection")          # first 'center' column = branch
    c["rd"], c["rm"] = iso(c["receipt_date"]), ym(c["receipt_date"])
    if c["rd"].isna().any():
        add_dq(conn, "Collection", "BAD_RECEIPT_DATE", "rows dropped: receipt_date unparseable", int(c["rd"].isna().sum()))
        c = c.dropna(subset=["rd"])
    rows = [(r.rd, r.rm, int(r.bid), int(r.reg_no) if pd.notna(r.reg_no) else None, str(r.payment_mode),
             float(r.paid_amount), float(r.net_paid_amount), float(r.refund_amount), r.src) for r in c.itertuples()]
    conn.executemany("INSERT INTO fact_collection (receipt_date, month, branch_id, reg_no, payment_mode, paid_amount, net_paid_amount, refund_amount, src) VALUES (?,?,?,?,?,?,?,?,?)", rows)
    log(f"fact_collection: {len(rows)} rows ({c['rm'].min()} .. {c['rm'].max()})")


_MON = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def carry_over_uploaded_plans(conn):
    """Plans uploaded through the portal (AOP2, ...) live only in the database, so copy them into the new one.
    (AOP1 is re-read from the sheet's Target_Long tab on every build, so it always follows the sheet.)"""
    if not config.DB_PATH.exists():
        return
    try:
        old = sqlite3.connect(config.DB_PATH)
        plans = old.execute("SELECT aop_id, name, fy, description, source, created_at FROM aop_version WHERE source='upload' ORDER BY aop_id").fetchall()
        mp = dict(conn.execute("SELECT branch_name, branch_id FROM dim_branch"))
        for aop_id, name, fy, desc, src, created in plans:
            new_id = conn.execute("SELECT COALESCE(MAX(aop_id),0)+1 FROM aop_version").fetchone()[0]
            conn.execute("INSERT INTO aop_version VALUES (?,?,?,?,?,?)", (new_id, name, fy, desc, src, created))
            rows = old.execute("""SELECT b.branch_name, t.month, t.enroll_target, t.aov, t.booking_target FROM fact_target t
                                  JOIN dim_branch b USING(branch_id) WHERE t.aop_id=?""", (aop_id,)).fetchall()
            conn.executemany("INSERT INTO fact_target VALUES (?,?,?,?,?,?)", [(new_id, mp[n], m, e, a, bk) for n, m, e, a, bk in rows if n in mp])
        old.close()
        if plans:
            log(f"kept {len(plans)} uploaded plan(s): {[p[1] for p in plans]}")
    except sqlite3.Error as e:
        log(f"WARNING could not carry over uploaded plans: {e}")


def set_snapshot(conn):
    """'Data till' = the latest date for which BOTH enrollments and payments exist (never a future date)."""
    today = dt.date.today().isoformat()
    j = conn.execute("SELECT MAX(joining_date) FROM fact_enrollment WHERE joining_date <= ?", (today,)).fetchone()[0]
    r = conn.execute("SELECT MAX(receipt_date) FROM fact_collection WHERE receipt_date <= ?", (today,)).fetchone()[0]
    snap = min(j, r)
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('snapshot_date', ?)", (snap,))
    log(f"data till {snap} (latest enrollment {j}, latest payment {r})")


def students_from_dumps(conn):
    """The revenue base for the Revenue Projection page: every student of the enrollment dump, with the dates and the
    amount worked out by history.py (start = admission date, end = batch end, amount = BOOKING = total payable / GST).
    adj_collection keeps the money collected so far (payments up to the data date / GST) for information; it is not used for revenue."""
    snap = conn.execute("SELECT value FROM meta WHERE key='snapshot_date'").fetchone()[0]
    conn.execute("CREATE TEMP TABLE tpay AS SELECT reg_no, SUM(net_paid_amount) s FROM fact_collection WHERE receipt_date <= ? GROUP BY reg_no", (snap,))
    conn.execute("CREATE INDEX temp.ix_tpay ON tpay(reg_no)")
    conn.execute("""INSERT INTO fact_student(reg_no, student_name, branch_id, category, scheme, bill_amount, payable_ex_gst, refund_fy,
                        status, archive_date, adj_collection, eff_start, eff_end, start_month, end_month, n_months, dq_flag, src)
                    SELECT d.reg_no, NULL, d.branch_id, ?, d.scheme, e.total_payable, d.amount, NULL, e.status, NULL,
                           ROUND(MAX(COALESCE(p.s, 0), 0) / ?, 2), d.start_date, d.end_date, d.start_month, d.end_month, NULL, d.dq_flag, 'dumps'
                    FROM fact_student_dump d JOIN fact_enrollment e ON e.reg_no = d.reg_no LEFT JOIN temp.tpay p ON p.reg_no = d.reg_no""",
                 (config.CATEGORY, config.GST_DIVISOR))
    conn.execute("DROP TABLE temp.tpay")
    n = conn.execute("SELECT COUNT(*) FROM fact_student").fetchone()[0]
    log(f"revenue base: {n} students, revenue on the booking (total payable / {config.GST_DIVISOR})")


def load_pl_reference(conn):
    """Booked P&L numbers (Rs Cr) for this category - used ONLY to reconcile the model, never to drive it."""
    if not config.COST_FILE.exists():
        log("finance P&L workbook not present: skipped (not used by the portal for now)")
        return
    try:
        raw = pd.read_excel(config.COST_FILE, sheet_name=config.PL_TAB, header=None)
    except Exception as e:                       # optional and unused, so never let it stop an update
        log(f"WARNING finance P&L workbook could not be read, skipped: {e}")
        return
    pat = re.compile(r"^([A-Za-z]+)'(\d\d)$")
    hdr = next(i for i in range(len(raw)) if sum(bool(pat.match(str(v).strip())) for v in raw.iloc[i]) >= 12)
    col_month = {}
    for j, v in enumerate(raw.iloc[hdr]):
        m = pat.match(str(v).strip())
        if m and m.group(1)[:3].lower() in _MON:
            col_month[j] = f"20{m.group(2)}-{_MON[m.group(1)[:3].lower()]:02d}"
    wanted = {"revenue": "Revenue", "net_collection": "Net Collection", "enrollment": "Batch enrollment (#)", "total_cost": "Total Cost"}
    n = 0
    for metric, label in wanted.items():
        row = next((i for i in range(hdr + 1, len(raw))
                    if any(str(v).strip() == label for v in raw.iloc[i, :6])), None)
        if row is None:
            add_dq(conn, "cost", "PL_ROW_NOT_FOUND", f"label '{label}' not found in tab {config.PL_TAB}")
            continue
        for j, m in col_month.items():
            v = raw.iat[row, j]
            if isinstance(v, (int, float)) and pd.notna(v) and v != 0:
                conn.execute("INSERT OR REPLACE INTO ref_pl_monthly VALUES (?,?,?,?)", (config.CATEGORY, m, metric, float(v)))
                n += 1
    log(f"ref_pl_monthly: {n} values")


def track_daily(conn):
    """Make the database a growing record instead of a copy of today's sheet.

    * every row gets first_seen_on = the first day our daily load saw it (rows already known keep their earlier date);
    * a row that was in the previous database but is no longer in the sheets is NOT thrown away: it moves to
      archive_enrollment_removed / archive_collection_removed with the day it disappeared. It is not counted in any revenue number
      (the sheets are the truth for that), but it can never be lost, and the Gap Check page tells you how many rows disappeared;
    * load_log keeps one line per day and source."""
    today = dt.date.today().isoformat()
    conn.execute("UPDATE fact_enrollment SET first_seen_on = ?", (today,))
    conn.execute("UPDATE fact_collection SET first_seen_on = ?", (today,))
    new_e = vanished_e = new_c = vanished_c = 0
    if config.DB_PATH.exists():
        try:
            conn.execute("ATTACH DATABASE ? AS old", (str(config.DB_PATH),))
            built = conn.execute("SELECT substr(value,1,10) FROM old.meta WHERE key='built_at'").fetchone()
            base = built[0] if built else today               # rows that existed before this feature carry the day of the last older build
            has = lambda t: "first_seen_on" in {r[1] for r in conn.execute(f"PRAGMA old.table_info({t})")}
            seen_e = "COALESCE(o.first_seen_on, ?)" if has("fact_enrollment") else "?"
            tables = {r[0] for r in conn.execute("SELECT name FROM old.sqlite_master WHERE type='table'")}
            # ---- enrollments (key: student number); looked up through a temporary indexed copy (the old table has no index on it)
            conn.execute(f"CREATE TEMP TABLE oe AS SELECT o.reg_no reg_no, {seen_e} seen FROM old.fact_enrollment o", (base,))
            conn.execute("CREATE INDEX temp.ix_oe ON oe(reg_no)")
            conn.execute("""UPDATE fact_enrollment SET first_seen_on = (SELECT seen FROM oe WHERE oe.reg_no = fact_enrollment.reg_no)
                            WHERE reg_no IN (SELECT reg_no FROM oe)""")
            conn.execute("CREATE TEMP TABLE ne AS SELECT reg_no FROM fact_enrollment")
            conn.execute("CREATE INDEX temp.ix_ne ON ne(reg_no)")
            conn.execute(f"""INSERT INTO archive_enrollment_removed
                SELECT o.reg_no, bb.branch_name, o.scheme, o.course, o.joining_date, o.join_month, o.batch_start, o.batch_end, o.status,
                       o.total_collection, o.total_payable, o.src, oe.seen, ?
                FROM old.fact_enrollment o JOIN oe ON oe.reg_no = o.reg_no LEFT JOIN old.dim_branch bb ON bb.branch_id = o.branch_id
                WHERE NOT EXISTS (SELECT 1 FROM ne WHERE ne.reg_no = o.reg_no)""", (today,))
            vanished_e = conn.execute("SELECT changes()").fetchone()[0]
            if "archive_enrollment_removed" in tables:
                conn.execute("""INSERT INTO archive_enrollment_removed SELECT * FROM old.archive_enrollment_removed a
                                WHERE NOT EXISTS (SELECT 1 FROM ne WHERE ne.reg_no = a.reg_no)""")
            conn.execute("DROP TABLE temp.ne")
            conn.execute("DROP TABLE temp.oe")
            # ---- collections (no id in the sheet: a payment is identified by its values, counted once per repeat)
            # amounts are rounded to paise: the sheets export 13099.9999999999 for 13100, and that is not a changed payment
            key = "IFNULL(reg_no,-1) k1, receipt_date k2, ROUND(IFNULL(paid_amount,0),2) k3, ROUND(IFNULL(net_paid_amount,0),2) k4, ROUND(IFNULL(refund_amount,0),2) k5, IFNULL(payment_mode,'') k6"
            part = "IFNULL(reg_no,-1), receipt_date, ROUND(IFNULL(paid_amount,0),2), ROUND(IFNULL(net_paid_amount,0),2), ROUND(IFNULL(refund_amount,0),2), IFNULL(payment_mode,'')"
            conn.execute(f"CREATE TEMP TABLE tnew AS SELECT rowid rid, {key}, ROW_NUMBER() OVER (PARTITION BY {part} ORDER BY rowid) rn FROM fact_collection")
            seen_c2 = "COALESCE(first_seen_on, ?)" if has("fact_collection") else "?"
            conn.execute(f"CREATE TEMP TABLE told AS SELECT rowid orid, {key}, ROW_NUMBER() OVER (PARTITION BY {part} ORDER BY rowid) rn, {seen_c2} seen FROM old.fact_collection", (base,))
            conn.execute("CREATE INDEX temp.ix_tnew_rid ON tnew(rid)")
            conn.execute("CREATE INDEX temp.ix_tnew ON tnew(k1,k2,k3,k4,k5,k6,rn)")
            conn.execute("CREATE INDEX temp.ix_told ON told(k1,k2,k3,k4,k5,k6,rn)")
            same = "d.k1=n.k1 AND d.k2=n.k2 AND d.k3=n.k3 AND d.k4=n.k4 AND d.k5=n.k5 AND d.k6=n.k6 AND d.rn=n.rn"
            conn.execute(f"""CREATE TEMP TABLE tseen AS SELECT n.rid rid, d.seen seen FROM tnew n JOIN told d ON {same}""")
            conn.execute("CREATE INDEX temp.ix_tseen ON tseen(rid)")
            conn.execute("UPDATE fact_collection SET first_seen_on = (SELECT seen FROM tseen WHERE tseen.rid = fact_collection.rowid) WHERE rowid IN (SELECT rid FROM tseen)")
            conn.execute(f"""INSERT INTO archive_collection_removed
                SELECT o.receipt_date, o.month, bb.branch_name, o.reg_no, o.payment_mode, o.paid_amount, o.net_paid_amount, o.refund_amount, o.src, d.seen, ?
                FROM told d JOIN old.fact_collection o ON o.rowid = d.orid LEFT JOIN old.dim_branch bb ON bb.branch_id = o.branch_id
                WHERE NOT EXISTS (SELECT 1 FROM tnew n WHERE {same})""", (today,))
            vanished_c = conn.execute("SELECT changes()").fetchone()[0]
            if "archive_collection_removed" in tables:
                # earlier archived rows are kept, except those that are still in the sheet (they were only decimal noise)
                conn.execute("""INSERT INTO archive_collection_removed SELECT * FROM old.archive_collection_removed a
                                WHERE NOT EXISTS (SELECT 1 FROM tnew n WHERE n.k1 = IFNULL(a.reg_no,-1) AND n.k2 = a.receipt_date AND n.k3 = ROUND(IFNULL(a.paid_amount,0),2)
                                                  AND n.k4 = ROUND(IFNULL(a.net_paid_amount,0),2) AND n.k5 = ROUND(IFNULL(a.refund_amount,0),2) AND n.k6 = IFNULL(a.payment_mode,''))""")
            if "load_log" in tables:
                conn.execute("INSERT OR IGNORE INTO load_log SELECT * FROM old.load_log WHERE load_date <> ?", (today,))
            conn.execute("DROP TABLE temp.tseen")
            conn.execute("DROP TABLE temp.tnew")
            conn.execute("DROP TABLE temp.told")
            conn.commit()
            conn.execute("DETACH DATABASE old")
        except sqlite3.Error as e:
            log(f"WARNING daily tracking against the previous database failed, continuing without it: {e}")
            try:
                conn.execute("DETACH DATABASE old")
            except sqlite3.Error:
                pass
    new_e = conn.execute("SELECT COUNT(*) FROM fact_enrollment WHERE first_seen_on = ?", (today,)).fetchone()[0]
    new_c = conn.execute("SELECT COUNT(*) FROM fact_collection WHERE first_seen_on = ?", (today,)).fetchone()[0]
    n_e = conn.execute("SELECT COUNT(*) FROM fact_enrollment").fetchone()[0]
    n_c = conn.execute("SELECT COUNT(*) FROM fact_collection").fetchone()[0]
    conn.execute("INSERT OR REPLACE INTO load_log VALUES (?,?,?,?,?)", (today, "enrollment", n_e, new_e, vanished_e))
    conn.execute("INSERT OR REPLACE INTO load_log VALUES (?,?,?,?,?)", (today, "collection", n_c, new_c, vanished_c))
    log(f"daily tracking: enrollments {n_e:,} ({new_e:,} first seen today, {vanished_e:,} disappeared and archived) | payments {n_c:,} ({new_c:,} first seen today, {vanished_c:,} disappeared and archived)")


def load_pl_full(conn):
    """Every line of the monthly P&L sheet (tab config.PL_TAB), month by month, exactly as given. Reference only."""
    if not config.COST_FILE.exists():
        return
    try:
        raw = pd.read_excel(config.COST_FILE, sheet_name=config.PL_TAB, header=None)
    except Exception as e:
        log(f"WARNING full P&L sheet could not be read, skipped: {e}")
        return
    pat = re.compile(r"^([A-Za-z]+)'(\d\d)$")
    hdr = next((i for i in range(len(raw)) if sum(bool(pat.match(str(v).strip())) for v in raw.iloc[i]) >= 12), None)
    if hdr is None:
        log("WARNING full P&L sheet: month header row not found, skipped")
        return
    col_month = {}
    for j, v in enumerate(raw.iloc[hdr]):
        m = pat.match(str(v).strip())
        if m and m.group(1)[:3].lower() in _MON:
            col_month[j] = f"20{m.group(2)}-{_MON[m.group(1)[:3].lower()]:02d}"
    clean = lambda v: None if pd.isna(v) else re.sub(r"\s+", " ", str(v)).strip() or None
    rows = []
    for r in range(hdr + 1, len(raw)):
        label = clean(raw.iat[r, 3])
        if not label or label.lower() == "particulars":
            continue
        for j, month in col_month.items():
            v = raw.iat[r, j]
            if isinstance(v, (int, float)) and pd.notna(v):
                rows.append((config.PL_TAB, r + 1, clean(raw.iat[r, 2]), label, clean(raw.iat[r, 4]), month, float(v)))
    conn.executemany("INSERT INTO ref_pl_full VALUES (?,?,?,?,?,?,?)", rows)
    log(f"ref_pl_full: {len(rows)} values, {len({r[3] for r in rows})} lines")


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Rebuild data/gew.db from the source workbooks.")
    for name in config.SOURCES:
        ap.add_argument(f"--{name}", help=f"use this .xlsx instead of {config.SOURCES[name].name} (e.g. a file you downloaded from Google Sheets)")
    ap.add_argument("--force", action="store_true", help="swap in the new database even if the safety checks fail")
    args = ap.parse_args()
    from pathlib import Path
    for name in config.SOURCES:
        given = getattr(args, name)
        if given:
            path = Path(given).expanduser().resolve()
            if not path.is_file():
                raise SystemExit(f"--{name}: file not found: {path}")
            config.SOURCES[name] = path
            log(f"using {path} for '{name}'")
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # The new database is built in a separate file and only replaces the live one after it passes the safety checks,
    # so a failed run (no internet, bad sheet, crash) can never leave the dashboard without data.
    building = config.DB_PATH.with_name("gew.building.db")
    if building.exists():
        building.unlink()
    try:
        build_into(building)
        swap_in(building, force=args.force)
    except BaseException:
        if building.exists():
            try:
                building.unlink()
            except OSError:
                pass
        raise


def build_into(path):
    conn = sqlite3.connect(path)
    try:
        _build(conn)
    finally:
        conn.close()                      # always release the file, so a failed build can delete its temp copy


def _build(conn):
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(config.SCHEMA_PATH.read_text(encoding="utf-8"))
    log("reading gew.xlsx (large, ~1 min) ...")
    gew = pd.ExcelFile(config.SOURCES["gew"])
    load_branches(conn, gew)
    load_aop1(conn, gew)
    carry_over_uploaded_plans(conn)
    load_enrollments(conn, gew)
    load_collections(conn, gew)
    track_daily(conn)
    set_snapshot(conn)
    load_pl_reference(conn)
    history.build_history(conn, add_dq, log)
    students_from_dumps(conn)
    for name, n, amt in conn.execute(
            """SELECT b.branch_name, COUNT(*), SUM(s.payable_ex_gst) FROM fact_student s
               JOIN dim_branch b USING(branch_id) WHERE b.is_active=0 GROUP BY b.branch_name""").fetchall():
        add_dq(conn, "enrollment dump", "CLOSED_BRANCH_EXCLUDED", f"{name} is closed: its students stay in the database but are hidden from the dashboard", n, amt)
    stats = eng.rebuild_schedule(conn)
    log(f"revenue schedule: {stats}")
    if stats["bad_dates_booked_in_start_month"]:
        add_dq(conn, "enrollment dump", "BAD_DATES_BOOKED_IN_START_MONTH",
               "end month before start month - nothing dropped, whole amount booked in the start month",
               stats["bad_dates_booked_in_start_month"], stats["bad_dates_amount"])
    import dq_checks
    dq_checks.build_dq(conn, add_dq, log)
    load_pl_full(conn)
    try:                                               # the whole of every sheet, kept as it is (see raw_store.py)
        import raw_store
        raw_store.build_raw(conn, log)
    except Exception as e:
        log(f"WARNING raw layer could not be built, skipped: {e}")
        try:
            conn.execute("DETACH DATABASE old")
        except sqlite3.Error:
            pass
    try:                                               # more flags on the GEW data (negative balance, wrong dates, ...), from the full copy of the sheet
        import dq_flags
        dq_flags.build_flags(conn, log)
    except Exception as e:
        log(f"WARNING extra data flags could not be built, skipped: {e}")
    try:                                               # the P&L team's sheets are only compared, never used: a problem here must not stop an update
        import gaps
        gaps.build_gaps(conn, add_dq, log)
    except SystemExit:
        raise                                          # the gap causes must add up exactly; if they do not, do not publish
    except Exception as e:
        log(f"WARNING Gap Check could not be built, skipped: {e}")
        for t in ("ref_iter_student", "cmp_student", "cmp_effect_monthly"):
            conn.execute(f"DELETE FROM {t}")
    try:                                               # the register of every gap, with proof
        import gap_report
        gap_report.build_gap_report(conn, log)
    except SystemExit:
        raise
    except Exception as e:
        log(f"WARNING gap register could not be built, skipped: {e}")
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('built_at', ?)", (dt.datetime.now().isoformat(timespec="seconds"),))
    conn.commit()
    conn.close()


CHECKED_TABLES = ("fact_enrollment", "fact_collection", "fact_student", "fact_student_dump")


def swap_in(building, force=False):
    """Safety checks, then replace the live database with the freshly built one (the old one is kept as gew.prev.db)."""
    import shutil
    import time
    new = sqlite3.connect(building)
    problems = []
    for t in CHECKED_TABLES:
        n_new = new.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        if n_new == 0:
            problems.append(f"{t} is empty in the new database")
    if config.DB_PATH.exists():
        old = sqlite3.connect(config.DB_PATH)
        try:
            for t in CHECKED_TABLES:
                n_old = old.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                n_new = new.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                if n_old and n_new < n_old * 0.97:
                    problems.append(f"{t} shrank from {n_old} to {n_new} rows (more than 3%)")
        except sqlite3.Error:
            pass                                          # old database has an older layout: nothing to compare
        finally:
            old.close()
    new.close()
    if problems and not force:
        raise SystemExit("NOT swapped, live database left untouched:\n  - " + "\n  - ".join(problems) +
                         "\n  (if this change is expected, run: python build_db.py --force)")
    if config.DB_PATH.exists():
        shutil.copyfile(config.DB_PATH, config.DB_PATH.with_name("gew.prev.db"))
    for attempt in range(10):                             # the server may be reading the file for a moment
        try:
            os.replace(building, config.DB_PATH)
            break
        except PermissionError:
            time.sleep(3)
    else:
        raise SystemExit("could not replace data/gew.db (file is locked); live database left untouched")
    log(f"done -> {config.DB_PATH}")


if __name__ == "__main__":
    sys.exit(main())

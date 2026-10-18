"""The raw layer: every tab of every source sheet, every row and every column, exactly as given, kept in the database and growing daily.

Nothing here drives a revenue number. It exists so that the database holds the WHOLE business data (walk-ins, demos,
enrollments, payments, targets, the P&L team's student sheet and all 22 P&L tabs), and so that no row is ever lost.

For each tab there is one table  raw_<source>_<tab>  with
    <all columns of the tab, with the sheet's own header names>
    row_hash       identity of the row (its content); the same content seen again is the same row
    first_seen_on  the first day our daily load saw this row
    last_seen_on   the last day it was still in the sheet. If this is older than today, the row has disappeared (or been edited:
                   an edited row is a new row, the old version stays here)
raw_catalog lists the tables, raw_load_log has one line per table per day.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import re
import sqlite3

import pandas as pd

import config

_MON = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_MONTH_HDR = re.compile(r"^([A-Za-z]+)'(\d\d)$")
META_COLS = ("row_hash", "first_seen_on", "last_seen_on")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _val(v):
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return None
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d") if (v.hour, v.minute, v.second) == (0, 0, 0) else v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, dt.datetime):
        return _val(pd.Timestamp(v))
    if hasattr(v, "item"):
        v = v.item()
    if isinstance(v, float):
        v = round(v, 4 if abs(v) < 1000 else 6)         # the sheets export 13099.9999999999 for 13100, and 1.8110968 for 1.811097: that is not an edit
        if v.is_integer() and abs(v) < 1e15:
            return int(v)
        return v
    if isinstance(v, (str, int, float, bytes)):
        return v
    return str(v)                                      # times, booleans, anything else: kept as text


def _hashes(rows: list[tuple], groups: list | None = None) -> list[str]:
    seen: dict = {}
    out = []
    for i, r in enumerate(rows):
        h = hashlib.md5("\x1f".join("" if v is None else str(v) for v in r).encode("utf-8", "replace")).hexdigest()
        k = (groups[i], h) if groups is not None else h
        n = seen.get(k, 0)
        seen[k] = n + 1
        out.append(h if n == 0 else f"{h}-{n}")          # the same row twice in the sheet stays two rows
    return out


def _unique(cols: list[str]) -> list[str]:
    out, used = [], set()
    for c in cols:
        c = re.sub(r"\s+", " ", str(c)).strip() or "unnamed"
        base, k = c, 1
        while c.lower() in used or c in META_COLS:
            k += 1
            c = f"{base} ({k})"
        used.add(c.lower())
        out.append(c)
    return out


NOW = ''               # the time of THIS load: last_seen_on carries it, so two loads on the same day are told apart (the rows of the earlier one are then 'gone')
RAW_MODE = os.environ.get('GEW_RAW_MODE', 'all')   # 'no_pii' (used on the web server): the walk-in and demo tabs, which hold phone numbers, are not stored
MIGRATE = False        # True once: the previous database was built before values were rounded, so re-hash its rows the same way


def _carry_old_rows_rehashed(conn, table: str, cols: list[str], today: str, norm=None) -> None:
    norm = norm or _val
    old_cols = [r[1] for r in conn.execute(f"PRAGMA old.table_info({_q(table)})")]
    data_cols = [c for c in old_cols if c not in META_COLS]
    rows = conn.execute(f"SELECT {', '.join(_q(c) for c in data_cols)}, first_seen_on, last_seen_on FROM old.{_q(table)} ORDER BY rowid").fetchall()
    normed = [tuple(norm(v) for v in r[:-2]) for r in rows]
    pos = {c: i for i, c in enumerate(data_cols)}
    hashes = _hashes([tuple(r[pos[c]] if c in pos else None for c in cols) for r in normed], groups=[r[-1] for r in rows])   # per version (last_seen_on)
    merged: dict[str, list] = {}
    for h, r, raw in zip(hashes, normed, rows):
        m = merged.get(h)
        if m is None:
            merged[h] = [r, raw[-2], raw[-1]]
        else:                                             # two old versions that are the same once rounded
            m[1], m[2] = min(m[1], raw[-2]), max(m[2], raw[-1])
    have = {r[0] for r in conn.execute(f"SELECT row_hash FROM {_q(table)}")}
    upd = [(v[1], h) for h, v in merged.items() if h in have]
    conn.executemany(f"UPDATE {_q(table)} SET first_seen_on = ? WHERE row_hash = ?", upd)
    lst = ", ".join(_q(c) for c in data_cols)
    ins = [(*v[0], h, v[1], v[2]) for h, v in merged.items() if h not in have]
    conn.executemany(f"INSERT INTO {_q(table)} ({lst}, row_hash, first_seen_on, last_seen_on) VALUES ({','.join('?' * (len(data_cols) + 3))})", ins)


def _store(conn, table: str, source: str, tab: str, cols: list[str], rows: list[tuple], today: str, has_old: bool, log) -> dict:
    cols = _unique(cols)
    rows = [tuple(_val(v) for v in r) for r in rows]
    hashes = _hashes(rows)
    coldef = ", ".join(f"{_q(c)}" for c in cols)
    conn.execute(f"CREATE TABLE {_q(table)} ({coldef}, row_hash TEXT PRIMARY KEY, first_seen_on TEXT, last_seen_on TEXT)")
    ph = ",".join("?" * (len(cols) + 3))
    conn.executemany(f"INSERT INTO {_q(table)} VALUES ({ph})", [(*r, h, today, NOW) for r, h in zip(rows, hashes)])
    vanished = 0
    if has_old and conn.execute("SELECT 1 FROM old.sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        old_cols = [r[1] for r in conn.execute(f"PRAGMA old.table_info({_q(table)})")]
        for c in old_cols:                                   # a column the sheet used to have and no longer has: keep it
            if c not in cols and c not in META_COLS:
                conn.execute(f"ALTER TABLE {_q(table)} ADD COLUMN {_q(c)}")
        if MIGRATE:
            _carry_old_rows_rehashed(conn, table, cols, today)
        else:
            conn.execute(f"""UPDATE {_q(table)} SET first_seen_on = COALESCE((SELECT o.first_seen_on FROM old.{_q(table)} o
                             WHERE o.row_hash = {_q(table)}.row_hash), first_seen_on)""")
            data_cols = [c for c in old_cols if c not in META_COLS]
            lst = ", ".join(_q(c) for c in data_cols)
            conn.execute(f"""INSERT INTO {_q(table)} ({lst}, row_hash, first_seen_on, last_seen_on)
                             SELECT {lst}, row_hash, first_seen_on, last_seen_on FROM old.{_q(table)}
                             WHERE row_hash NOT IN (SELECT row_hash FROM {_q(table)})""")
        vanished = conn.execute(f"SELECT COUNT(*) FROM {_q(table)} WHERE last_seen_on < ?", (NOW,)).fetchone()[0]
    new = conn.execute(f"SELECT COUNT(*) FROM {_q(table)} WHERE first_seen_on = ?", (today,)).fetchone()[0]
    total = conn.execute(f"SELECT COUNT(*) FROM {_q(table)}").fetchone()[0]
    conn.execute("INSERT OR REPLACE INTO raw_catalog VALUES (?,?,?,?)", (table, source, tab, len(cols)))
    conn.execute("INSERT OR REPLACE INTO raw_load_log VALUES (?,?,?,?,?,?)", (today, table, len(rows), new, vanished, total))
    return {"table": table, "rows": len(rows), "new": new, "vanished": vanished}


def _store_sheet_tabs(conn, path, prefix, source, today, has_old, log, out):
    x = pd.ExcelFile(path)
    for tab in x.sheet_names:
        if RAW_MODE == 'no_pii' and any(k in tab.lower() for k in ('walkin', 'demo')):
            continue
        df = x.parse(tab)
        df = df.dropna(how="all")
        cols = [str(c) for c in df.columns]
        rows = [tuple(r) for r in df.itertuples(index=False, name=None)]
        out.append(_store(conn, f"raw_{prefix}_{_slug(tab)}", source, tab, cols, rows, today, has_old, log))
        log(f"  raw {source} / {tab}: {len(rows):,} rows, {len(cols)} columns")


def _store_cost_tabs(conn, path, today, has_old, log, out):
    """All tabs of the monthly P&L workbook, one row per line per month: tab, group, line, month, value."""
    x = pd.ExcelFile(path)
    rows = []
    for tab in x.sheet_names:
        raw = x.parse(tab, header=None)
        hdr = next((i for i in range(len(raw)) if sum(bool(_MONTH_HDR.match(str(v).strip())) for v in raw.iloc[i]) >= 12), None)
        if hdr is None:
            log(f"  WARNING P&L tab '{tab}': month header row not found, skipped")
            continue
        col_month = {}
        for j, v in enumerate(raw.iloc[hdr]):
            m = _MONTH_HDR.match(str(v).strip())
            if m and m.group(1)[:3].lower() in _MON:
                col_month[j] = f"20{m.group(2)}-{_MON[m.group(1)[:3].lower()]:02d}"
        clean = lambda v: None if pd.isna(v) else re.sub(r"\s+", " ", str(v)).strip() or None
        group = None
        for r in range(hdr + 1, len(raw)):
            g = clean(raw.iat[r, 2])
            group = g or group
            label = clean(raw.iat[r, 3])
            if not label or label.lower() == "particulars":
                continue
            for j, month in col_month.items():
                v = raw.iat[r, j]
                if pd.isna(v):
                    continue
                if isinstance(v, (int, float)):
                    rows.append((tab, group, label, clean(raw.iat[r, 4]), month, round(float(v), 8), None, r + 1))
                elif str(v).strip():
                    rows.append((tab, group, label, clean(raw.iat[r, 4]), month, None, str(v).strip(), r + 1))
    cols = ["tab", "group_label", "line_label", "unit", "month", "value", "value_text", "row_no"]
    # row_no is stored but does not take part in the row identity (inserting a row in the sheet must not make everything look new)
    ident = [r[:7] for r in rows]
    hashes = _hashes(ident)
    table = "raw_pl_all_tabs"
    conn.execute(f"CREATE TABLE {table} (tab, group_label, line_label, unit, month, value, value_text, row_no, row_hash TEXT PRIMARY KEY, first_seen_on TEXT, last_seen_on TEXT)")
    conn.executemany(f"INSERT INTO {table} VALUES (?,?,?,?,?,?,?,?,?,?,?)", [(*r, h, today, NOW) for r, h in zip(rows, hashes)])
    vanished = 0
    if has_old and conn.execute("SELECT 1 FROM old.sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        if MIGRATE:
            _carry_old_rows_rehashed(conn, table, ["tab", "group_label", "line_label", "unit", "month", "value", "value_text"], today,
                                     norm=lambda v: round(v, 8) if isinstance(v, float) else v)
        else:
            conn.execute(f"UPDATE {table} SET first_seen_on = COALESCE((SELECT o.first_seen_on FROM old.{table} o WHERE o.row_hash = {table}.row_hash), first_seen_on)")
            conn.execute(f"""INSERT INTO {table} SELECT * FROM old.{table} WHERE row_hash NOT IN (SELECT row_hash FROM {table})""")
        vanished = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE last_seen_on < ?", (NOW,)).fetchone()[0]
    new = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE first_seen_on = ?", (today,)).fetchone()[0]
    total = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    conn.execute("INSERT OR REPLACE INTO raw_catalog VALUES (?,?,?,?)", (table, "P&L workbook (monthly, all tabs)", "all 22 tabs", len(cols)))
    conn.execute("INSERT OR REPLACE INTO raw_load_log VALUES (?,?,?,?,?,?)", (today, table, len(rows), new, vanished, total))
    out.append({"table": table, "rows": len(rows), "new": new, "vanished": vanished})
    log(f"  raw P&L workbook: {len(rows):,} values from {len({r[0] for r in rows})} tabs")


def build_raw(conn, log) -> None:
    today = dt.date.today().isoformat()
    conn.execute("CREATE TABLE raw_catalog (table_name TEXT PRIMARY KEY, source TEXT, tab TEXT, n_columns INTEGER)")
    conn.execute("CREATE TABLE raw_load_log (load_date TEXT, table_name TEXT, rows_in_sheet INTEGER, rows_new INTEGER, rows_vanished_total INTEGER, rows_total INTEGER, PRIMARY KEY (load_date, table_name))")
    has_old = False
    if config.DB_PATH.exists():
        try:
            conn.execute("ATTACH DATABASE ? AS old", (str(config.DB_PATH),))
            has_old = True
            # keep the daily history of the load log too
            if conn.execute("SELECT 1 FROM old.sqlite_master WHERE name='raw_load_log'").fetchone():
                conn.execute("INSERT OR IGNORE INTO raw_load_log SELECT * FROM old.raw_load_log WHERE load_date < ?", (today,))
        except sqlite3.Error as e:
            log(f"WARNING raw layer: previous database not readable, history not carried over: {e}")
    global MIGRATE, NOW
    NOW = dt.datetime.now().isoformat(sep=' ', timespec='seconds')
    MIGRATE = bool(has_old and not conn.execute("SELECT 1 FROM old.meta WHERE key='raw_rounded_v5'").fetchone())
    if MIGRATE:
        log("raw layer: re-hashing the previous database's rows once, so decimal noise is not mistaken for edits")
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('raw_rounded_v5', '1')")
    out: list[dict] = []
    try:
        _store_sheet_tabs(conn, config.SOURCES["gew"], "gew", "GEW workbook", today, has_old, log, out)
        _store_sheet_tabs(conn, config.SOURCES["collection"], "collection2", "Collection FY27 workbook", today, has_old, log, out)
        if config.OPTIONAL_SOURCES["iteration"].exists():
            _store_sheet_tabs(conn, config.OPTIONAL_SOURCES["iteration"], "pl_students", "P&L team student sheet", today, has_old, log, out)
        if config.OPTIONAL_SOURCES["pl"].exists():
            _store_cost_tabs(conn, config.OPTIONAL_SOURCES["pl"], today, has_old, log, out)
        conn.commit()
    finally:
        if has_old:
            conn.commit()
            conn.execute("DETACH DATABASE old")
    log(f"raw layer: {len(out)} tables, {sum(o['rows'] for o in out):,} rows today, {sum(o['new'] for o in out):,} first seen today, "
        f"{sum(o['vanished'] for o in out):,} kept although no longer in the sheets")

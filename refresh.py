"""One command that brings the dashboard up to date.  Run by hand or by the daily Windows task.

    python refresh.py            # download every source sheet, rebuild the database, swap it in

Steps: 1) download every source sheet   2) build a NEW database in a separate file
       3) swap it in only if the safety checks pass.   The live database is never touched until step 3,
so a failure at any point (no internet, sheet not shared, crash) leaves yesterday's dashboard intact.
Everything is written to data/refresh.log; the outcome of the last run is in data/last_refresh.json.
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys

import config

LOG = config.ROOT / "data" / "refresh.log"
STATE = config.ROOT / "data" / "last_refresh.json"
PY = sys.executable


def log(msg: str):
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    try:
        print(line, flush=True)
    except Exception:                       # pythonw has no console
        pass


def run(args: list[str], timeout: int) -> tuple[int, str]:
    p = subprocess.run([PY, *args], cwd=config.ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main() -> int:
    started = dt.datetime.now()
    log("---- refresh started")

    fetch_ok, fetch_failed = {}, []
    for name in config.SOURCES:
        try:
            code, out = run(["fetch_sheets.py", name], timeout=900)
        except subprocess.TimeoutExpired:
            code, out = 1, "timed out"
        fetch_ok[name] = code == 0
        if code != 0:
            fetch_failed.append(name)
            log(f"WARNING download of '{name}' failed, keeping the previous copy: {out.strip().splitlines()[-1] if out.strip() else ''}")
        else:
            log(f"downloaded {name}")

    optional_failed = []
    for name in config.OPTIONAL_SOURCES:                      # the P&L team's sheets: a failure only warns
        try:
            code, out = run(["fetch_sheets.py", name], timeout=900)
        except subprocess.TimeoutExpired:
            code, out = 1, "timed out"
        if code != 0:
            optional_failed.append(name)
            log(f"WARNING reference sheet '{name}' could not be downloaded, keeping the previous copy")
        else:
            log(f"downloaded {name} (reference)")

    state = {"started": started.isoformat(timespec="seconds"), "fetch_ok": fetch_ok, "optional_failed": optional_failed,
             "built": False, "ok": True, "message": ""}

    # (A downloaded .xlsx differs byte-for-byte every time because Google stamps it, so "did it change?" cannot be told
    #  from the file; rebuilding takes about a minute and a half, so we simply rebuild every run.)
    if len(fetch_failed) == len(config.SOURCES):
        state["ok"] = False
        state["message"] = "no sheet could be downloaded (internet down or sheets not shared?); database not rebuilt"
        log(f"ERROR {state['message']}")
    else:
        log("rebuilding database ...")
        try:
            code, out = run(["build_db.py"], timeout=1800)
        except subprocess.TimeoutExpired:
            code, out = 1, "build timed out after 30 minutes"
        tail = " | ".join(out.strip().splitlines()[-3:])
        if code == 0:
            state["built"] = True
            state["message"] = "database rebuilt and swapped in"
            log(f"OK {tail}")
            try:                                              # the gap report (PDF + Excel) follows the data: always up to date
                code2, out2 = run(["docs/make_gap_report.py"], timeout=420)
                log(("gap report: " if code2 == 0 else "WARNING gap report could not be rebuilt: ") + (out2.strip().splitlines()[-1] if out2.strip() else ""))
            except Exception as ex:
                log(f"WARNING gap report could not be rebuilt: {ex}")
            if (config.ROOT / "data" / "deploy.json").exists():          # the portal on the web: send it the new database
                try:
                    code3, out3 = run(["deploy/push_to_server.py"], timeout=1200)
                    log(("web portal updated: " if code3 == 0 else "WARNING web portal could not be updated: ") + (out3.strip().splitlines()[-1] if out3.strip() else ""))
                except Exception as ex:
                    log(f"WARNING web portal could not be updated: {ex}")
        else:
            state["ok"] = False
            state["message"] = f"build FAILED, previous database kept: {tail}"
            log(f"ERROR {state['message']}")

    if fetch_failed:
        state["message"] += f" (download failed for: {', '.join(fetch_failed)})"
        state["ok"] = state["ok"] and not fetch_failed
    state["finished"] = dt.datetime.now().isoformat(timespec="seconds")
    state["minutes"] = round((dt.datetime.now() - started).total_seconds() / 60, 1)
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    log(f"---- refresh finished in {state['minutes']} min: {'OK' if state['ok'] else 'PROBLEM'}")
    return 0 if state["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

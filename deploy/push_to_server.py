"""Sends the newest database to the web portal.   python deploy/push_to_server.py

Needs data/deploy.json:   {"url": "https://your-portal.onrender.com", "token": "the GEW_UPLOAD_TOKEN you set on the server"}
refresh.py runs this every morning after the database is rebuilt, so the web portal always shows the same numbers as the PC.
"""
import json
import pathlib
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "deploy"))
import make_slim_db  # noqa: E402

cfg = json.loads((ROOT / "data" / "deploy.json").read_text(encoding="utf-8"))
slim = make_slim_db.make()
size = slim.stat().st_size
print(f"sending {slim.name} ({size / 1e6:.0f} MB) to {cfg['url']} ...", flush=True)
with open(slim, "rb") as fh:
    req = urllib.request.Request(cfg["url"].rstrip("/") + "/api/upload_db", data=fh, method="POST",
                                 headers={"X-Token": cfg["token"], "Content-Type": "application/octet-stream", "Content-Length": str(size)})
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            print("server answered:", r.read().decode("utf-8", "replace")[:200])
    except urllib.error.HTTPError as e:
        raise SystemExit(f"the server refused the database: {e.code} {e.read().decode('utf-8', 'replace')[:200]}")

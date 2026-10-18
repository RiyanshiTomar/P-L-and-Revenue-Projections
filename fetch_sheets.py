"""Download the latest copy of the source Google Sheets into data/raw/*.xlsx.

    python fetch_sheets.py              # the two main sheets + the P&L team's two reference sheets
    python fetch_sheets.py collection   # only the Collection FY27 sheet

Each sheet must be shared as "Anyone with the link: Viewer". Then run `python build_db.py` to reload the database.
Downloads go to a temp file first, so a failed download never overwrites a good copy.
"""
import sys
import time
import urllib.request
import zipfile

import config

ALL_PATHS = {**config.SOURCES, **config.OPTIONAL_SOURCES}
ALL_IDS = {**config.GOOGLE_SHEETS, **config.OPTIONAL_SHEETS}
names = sys.argv[1:] or list(ALL_PATHS)
unknown = [n for n in names if n not in ALL_PATHS]
if unknown:
    raise SystemExit(f"unknown sheet(s) {unknown}; choose from {list(ALL_PATHS)}")

WAITS = (10, 30, 60, 120, 240)          # a big download can break half way (IncompleteRead): try again, up to 6 times in all


def download(name, url, tmp):
    with urllib.request.urlopen(url, timeout=300) as r, open(tmp, "wb") as fh:
        head = r.read(4)
        if head[:2] != b"PK":                       # an xlsx is a zip; an HTML page means sharing is off
            raise SystemExit(f"{name}: Google did not return a spreadsheet - check the sheet is shared to 'Anyone with the link'")
        fh.write(head)
        while chunk := r.read(1 << 20):
            fh.write(chunk)
    if not zipfile.is_zipfile(tmp):                 # a half-downloaded file has no zip directory at its end
        raise OSError("the downloaded file is incomplete")


config.RAW.mkdir(parents=True, exist_ok=True)
for name in names:
    url = f"https://docs.google.com/spreadsheets/d/{ALL_IDS[name]}/export?format=xlsx"
    dest = ALL_PATHS[name]
    tmp = dest.with_suffix(".part")
    print(f"downloading {name} ...", flush=True)
    for attempt in range(len(WAITS) + 1):
        try:
            download(name, url, tmp)
            break
        except SystemExit:
            raise
        except Exception as e:                      # network dropped, timeout, incomplete read ...
            if attempt == len(WAITS):
                raise
            print(f"  attempt {attempt + 1} failed ({type(e).__name__}: {e}); trying again in {WAITS[attempt]} s", flush=True)
            time.sleep(WAITS[attempt])
    tmp.replace(dest)
    print(f"  saved {dest} ({dest.stat().st_size / 1e6:.1f} MB)")
print("now run: python build_db.py")

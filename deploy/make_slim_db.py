"""Makes the small copy of the database that goes on the web: data/gew.slim.db

The full database on the PC also keeps every raw row of every sheet (walk-in and demo lists with phone numbers, student names). None of that is
needed by the portal pages, so it is left out here. The slim copy has everything the pages and the gap register need.

    python deploy/make_slim_db.py
"""
import pathlib
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import config  # noqa: E402

OUT = ROOT / "data" / "gew.slim.db"


def make() -> pathlib.Path:
    if OUT.exists():
        OUT.unlink()
    src = sqlite3.connect(f"file:{config.DB_PATH.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(OUT)
    src.backup(dst)                                  # a consistent copy, even while the portal is reading the database
    src.close()
    names = [r[0] for r in dst.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'raw%'")]
    for name in names:
        if name.startswith("raw_"):
            dst.execute(f'DROP TABLE "{name}"')
    dst.execute("UPDATE ref_iter_student SET student_name = NULL")
    dst.commit()
    dst.execute("VACUUM")
    dst.close()
    return OUT


if __name__ == "__main__":
    p = make()
    print(f"slim database: {p} ({p.stat().st_size / 1e6:.0f} MB)")

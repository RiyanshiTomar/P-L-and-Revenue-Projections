"""Central config. Everything that is a business assumption or a file location lives here."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "data" / "raw"
DB_PATH = Path(os.environ.get("GEW_DB_PATH") or ROOT / "data" / "gew.db")   # on the web server: a permanent disk
SCHEMA_PATH = ROOT / "db" / "schema.sql"

# ---- Source workbooks (exported from the two Google Sheets; see fetch_sheets.py) ----
# Everything in the portal is built from these two sheets only.
SOURCES = {
    "gew":        RAW / "gew.xlsx",         # annual plan (Target_Long), enrollment dumps, collection history
    "collection": RAW / "collection.xlsx",  # Collection FY27 (own sheet, the first one hit Google's size limit)
}
GOOGLE_SHEETS = {
    "gew":        "14eBLfRqIjayPqI455sZptF5WxVawerNZL5ySTTgrJw4",
    "collection": "1pyAaQsmGdn1AeBxdeJwas6ppXG3h3ROSqdB-r1RIk2c",
}
# The P&L team's two sheets. They never drive any number in the portal; they are only used by the "Gap Check" page, to show where
# our numbers and theirs differ. If a download fails, the portal still updates (it just keeps the previous copy).
OPTIONAL_SOURCES = {
    "iteration": RAW / "complete.xlsx",     # student-wise "Govt Offline Iteration" sheet (vertical view)
    "pl":        RAW / "cost.xlsx",         # monthly P&L workbook (horizontal view)
}
OPTIONAL_SHEETS = {
    "iteration": "1EsPMSIHk6RUYk6ta_3hzem6p2eDsFbM4hcwhXgHfMWk",
    "pl":        "1gvC7trgaXziuU53CAtTWJOxr60EXmccf",
}
COST_FILE = OPTIONAL_SOURCES["pl"]

# ---- Scope ----
CATEGORY = "Government Exam Offline"
FY_LABEL = "FY27"
FY_START = "2026-04"   # first month of the financial year
FY_END = "2027-03"     # last month of the financial year

# Tab in the cost workbook that holds the P&L for this category
PL_TAB = "Govt exam - Offline"

# Category -> Cohort -> Branch (copied from the existing executive dashboard so both agree)
COHORT_MAPPING = {
    "Agra Government": 1, "Bareilly Government": 1, "Gorakhpur Government": 1,
    "Kanpur Government": 1, "Lucknow - Alambagh Government": 1, "Lucknow Government": 1,
    "Varanasi Government - Durgakund": 1,
    "Bhopal Government": 2, "Indore Government": 2, "Jabalpur Government": 2,
    "Nagpur Government": 2, "Raipur Government": 2,
    "Muzaffarpur Government": 3, "Patna - Kankarbagh Government": 3,
    "Patna Government": 3, "Ranchi Government - Lalpur": 3,
    "Dehradun Government": 4, "Delhi Government": 4,
    "Jaipur Government-Gopalpura": 4, "Jammu Government": 4,
    "Prayagraj Government-Katra": 5,
    "Berhampur Government": 6, "Bhubaneswar Government": 6, "Vishakhapatnam Government": 6,
    "Kolkata Government": 7,
    "Guwahati Government": 8,
    "Siliguri Government": 9,
}

# Branches to HIDE from every view, filter, AOP and projection (history stays in the database).
# Siliguri is intentionally NOT hidden: it earned revenue this year, so it stays in (it simply has no future enrollments in the AOP).
CLOSED_BRANCHES: set[str] = set()

# ---- Revenue rule (CHANGE HERE, not in code) ----
# Revenue of a student = everything the student has paid, without GST, spread equally over every calendar month from
#   start = admission date  (column new_receipt_date of the enrollment dump)   to   end = batch_end_date     (both months included)
# (Confirmed by the business: new_receipt_date is the date of admission = the effective start date.)
GST_DIVISOR = 1.18

# ---- Defaults for the "expected from future enrollments" layer ----
EXPECTED_WINDOW_MONTHS = 6     # look-back used to learn avg ticket + duration per branch
EXPECTED_MIN_STUDENTS = 30     # below this, fall back to the all-branch average
MOCK_BILL_THRESHOLD = 100.0    # bills below this (e.g. Rs 9.33 mock cards) are not real admissions


# ---- Branch "age" groups for the Trends page: by the month the first student joined (data starts Jan 2024) ----
# (label, first month from, first month to). None = open ended.
VINTAGE_GROUPS = [
    ("Started in 2024 or earlier", None, "2024-12"),
    ("Started in 2025", "2025-01", "2025-12"),
    ("Started Jan-Mar 2026", "2026-01", "2026-03"),
    ("Started Apr 2026 or later (new this year)", "2026-04", None),
]

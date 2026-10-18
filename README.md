# GEW Revenue Projection & P&L

Scope today: **Government Exam Offline**, FY27, 26 active branches (Siliguri is closed and hidden).

## Run it

```
pip install -r requirements.txt
python fetch_sheets.py        # optional: refresh data/raw/*.xlsx from the Google Sheets
python build_db.py            # rebuild data/gew.db from the xlsx files (~1-2 min)
python server.py              # open http://127.0.0.1:8000
```

## Files

| File | Job |
|---|---|
| `config.py` | Every business setting: file paths, branches/cohorts, closed branches, revenue column, look-back window |
| `db/schema.sql` | The database design (SQLite now, Postgres-compatible) |
| `build_db.py` | Sheets -> database. Validates, logs bad rows in `dq_issue`, never drops silently |
| `revenue_engine.py` | Revenue rule + projection layers (the only place the maths lives) |
| `server.py` | API (`/api/meta`, `/api/revenue`, `/api/reconcile`, `/api/aop`) + serves `web/` |
| `web/` | Revenue Projection page; `web/executive/` is the old Executive Dashboard (projections removed) |

## What the portal is built from

Only **two Google Sheets**: the main workbook (annual plan `Target_Long`, enrollment dumps, collection history) and the Collection FY27 sheet.
The P&L team's two sheets (the student-wise "Govt Offline Iteration" sheet and finance's monthly P&L workbook) are *reference only*: they feed the
**Gap Check** page and never change a portal number.

## Revenue rule

Student revenue = the student's **booking** (`total_payable` of the enrollment dump, the fee the student has to pay), divided by 1.18 (GST), shared equally over every calendar month from the
**admission date** (`new_receipt_date` of the enrollment dump = effective start) to `batch_end_date` (= effective end), both months included.

- **Already earned** = months up to the last fully covered month. **Current students** = later months of students already admitted.
- **New students (estimate)** = annual plan (AOP) students x achievement % x this year's average booking fee of the branch / 1.18, spread over the branch's
  typical duration. For the month in progress only the not-yet-admitted part of the plan is counted.
- A student whose end date is before the start date is not dropped: the whole amount goes to the start month and the student is noted.
- Plans uploaded through the portal (AOP2...) are kept when the database is rebuilt; AOP1 always follows the sheet.

## Gap Check

`gaps.py` compares our numbers with the Iteration sheet (made by the P&L team) and splits the revenue gap into causes that add up exactly (students only one side has,
different dates, different amount: our booking against the sheet's Total Adjusted Collection, and also against its Final Total Payable). The build stops if they ever do not add up. `/api/gaps/students.csv` gives the
student-level list for Excel.

## Checking and explaining

- `python -I verify.py` recalculates everything from the raw sheets with separate code and compares it with the database (21 checks).
- `python docs/make_guide_pdf.py` re-creates `docs/GEW_Revenue_Portal_Guide.pdf` (a plain-language explainer of where every number comes from,
  made for presenting). It runs the checks first and fills in live numbers. Needs Chrome or Edge only to print the PDF.
- Pages: Revenue Projection, Branch Trends (this year vs last year, branches grouped by age), Gap Check, Executive Dashboard (old dashboard; has a Plan (AOP) selector that defaults to the live sheet, a Line 1 vs Line 2 trend chart, and branch-wise Enrollment/AOV trends in four situations; it warns on screen if the sheet's enrollment dates are missing).

## Portal starts by itself

A Windows task **"GEW Portal Server"** starts `server.py` (no window) every time you log in to Windows and restarts it if it stops.
Open **http://localhost:8000**. Log: `data/server.log`. Install: `powershell -ExecutionPolicy Bypass -File install_server_autostart.ps1`; remove: same with `-Remove`.
It is reachable only from this computer (127.0.0.1). To show it on another device it has to be hosted on a shared machine.

## Automatic daily refresh

`refresh.py` downloads every source sheet, builds a NEW database in a separate file, runs safety checks (no table may shrink by more
than 3%), and only then swaps it in (the previous one is kept as `data/gew.prev.db`). A failed run never touches the live database.
A Windows task **"GEW Data Refresh"** runs it every day at 07:30 (runs as the logged-in user; if the PC was off it runs when it is next on).

- Install / change time: `powershell -ExecutionPolicy Bypass -File install_schedule.ps1 -Time 09:00`
- Remove: `powershell -ExecutionPolicy Bypass -File install_schedule.ps1 -Remove`
- Run now: `python refresh.py`; log: `data/refresh.log`; last result: `data/last_refresh.json` (also shown under the page title).
- The sheets must stay shared as "Anyone with the link". The server does not need a restart after a refresh.

## Adding new data later

- New month of students/collections: refresh the sheet -> `fetch_sheets.py` -> `build_db.py`.
- New AOP: download the template from the page, edit numbers, upload as `AOP2` (no code change).
- New vertical/category or new cost data: add tables next to the existing facts (`fact_cost`), keep `dim_branch` as the shared dimension.

## Moving to PostgreSQL

`schema.sql` uses only portable types. Create the tables in Postgres, change `sqlite3` to `psycopg` in `build_db.py`/`server.py`
(`?` placeholders become `%s`), and keep everything else.

## Gap register and flags (what to send when someone asks "where do the numbers differ?")
The Gap Check page opens with the **gap register**: every difference between the GEW data, the Iteration data (the Iteration sheet, shown on both of its amounts) and
Finance's monthly P&L, in plain words, with the effect on this year's revenue and one real student as proof. The gaps add up to the total gap exactly (`gap_report.py`; the build stops if they do not).
It has a **text for the mail** button and a download of **all proof as one Excel file** (every student behind every gap and every flag). `dq_flags.py` adds flags on the GEW data
(negative balance, batch starting after it ends, two admission dates that disagree, collected amount that disagrees between two sheets, forms not completed, ...).
After every daily build `refresh.py` also rewrites `docs/GEW_Gap_Report.pdf` and `docs/GEW_Gap_Report.xlsx`, so both files are always up to date. `gap_history` keeps one line per gap per day.

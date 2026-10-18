-- GEW Revenue / P&L database. SQLite today; every type below is valid in PostgreSQL too
-- (swap INTEGER PRIMARY KEY for GENERATED ... AS IDENTITY and TEXT months for DATE when you migrate).
--
-- Star-schema idea:
--   dimensions  : dim_branch   (branch -> cohort -> category)
--   facts       : fact_student (one row per student = revenue base)
--                 fact_enrollment, fact_collection (transaction dumps)
--                 fact_target    (AOP, versioned: AOP1, AOP2 ...)
--                 fact_revenue_monthly (DERIVED: student x month, rebuilt by revenue_engine.py)
--   reference   : ref_pl_monthly (booked P&L numbers, used only to reconcile the model)
--   housekeeping: meta, dq_issue
-- Months are stored as 'YYYY-MM' text so they sort and join correctly.

CREATE TABLE dim_branch (
    branch_id   INTEGER PRIMARY KEY,
    branch_name TEXT NOT NULL UNIQUE,      -- exact name used in every source sheet
    short_name  TEXT,
    cohort_id   INTEGER NOT NULL,
    category    TEXT NOT NULL,              -- e.g. 'Government Exam Offline'
    is_active   INTEGER NOT NULL DEFAULT 1  -- 0 = closed branch: history kept, hidden from views/AOP/projection
);

CREATE TABLE aop_version (
    aop_id      INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,       -- 'AOP1', 'AOP2' ...
    fy          TEXT NOT NULL,              -- 'FY27'
    description TEXT,
    source      TEXT,                       -- 'sheet:Target_Long' / 'upload'
    created_at  TEXT NOT NULL
);

CREATE TABLE fact_target (
    aop_id         INTEGER NOT NULL REFERENCES aop_version(aop_id) ON DELETE CASCADE,
    branch_id      INTEGER NOT NULL REFERENCES dim_branch(branch_id),
    month          TEXT NOT NULL,           -- 'YYYY-MM'
    enroll_target  REAL NOT NULL,
    aov            REAL NOT NULL,           -- target average order value (Rs per enrollment)
    booking_target REAL NOT NULL,           -- enroll_target * aov
    PRIMARY KEY (aop_id, branch_id, month)
);

CREATE TABLE fact_student (
    reg_no           INTEGER PRIMARY KEY,
    student_name     TEXT,
    branch_id        INTEGER NOT NULL REFERENCES dim_branch(branch_id),
    category         TEXT NOT NULL,
    scheme           TEXT,
    bill_amount      REAL,
    payable_ex_gst   REAL,                  -- THE REVENUE AMOUNT: booking = total_payable / 1.18 (fee without GST)
    refund_fy        REAL,
    status           TEXT,                  -- Active / Inactive
    archive_date     TEXT,
    adj_collection   REAL NOT NULL,         -- money collected so far: payments up to the data date / 1.18. Information only, NOT used for revenue
    eff_start        TEXT NOT NULL,         -- 'YYYY-MM-DD'
    eff_end          TEXT NOT NULL,
    start_month      TEXT NOT NULL,         -- 'YYYY-MM'
    end_month        TEXT NOT NULL,
    n_months         INTEGER,               -- calendar months start..end inclusive (NULL when invalid)
    dq_flag          TEXT,                  -- NULL when clean
    src              TEXT NOT NULL DEFAULT 'sheet'   -- 'sheet' = row of the complete sheet, 'derived' = rebuilt from enrollment dump + payments
);
CREATE INDEX ix_student_branch ON fact_student(branch_id, start_month);

CREATE TABLE fact_enrollment (
    reg_no        INTEGER NOT NULL,
    branch_id     INTEGER NOT NULL REFERENCES dim_branch(branch_id),
    scheme        TEXT,
    course        TEXT,
    joining_date  TEXT NOT NULL,           -- ADMISSION date = column new_receipt_date of the dump (= effective start date)
    join_month    TEXT NOT NULL,           -- month of the admission date
    batch_start   TEXT,
    batch_end     TEXT,
    status        TEXT,
    total_collection REAL,
    total_payable    REAL,
    src           TEXT NOT NULL,            -- 'FY26' (history) / 'FY27'
    first_seen_on TEXT                      -- the first day our daily load saw this row (see track_daily in build_db.py)
);
CREATE INDEX ix_enr_branch_month ON fact_enrollment(branch_id, join_month);

CREATE TABLE fact_collection (
    receipt_date  TEXT NOT NULL,
    month         TEXT NOT NULL,            -- 'YYYY-MM' derived from receipt_date
    branch_id     INTEGER NOT NULL REFERENCES dim_branch(branch_id),
    reg_no        INTEGER,
    payment_mode  TEXT,
    paid_amount   REAL,
    net_paid_amount REAL,
    refund_amount REAL,
    src           TEXT NOT NULL,
    first_seen_on TEXT                      -- the first day our daily load saw this row
);
CREATE INDEX ix_col_branch_month ON fact_collection(branch_id, month);

-- Derived: rebuilt from fact_student by revenue_engine.py. Never edited by hand.
CREATE TABLE fact_revenue_monthly (
    reg_no    INTEGER NOT NULL,
    branch_id INTEGER NOT NULL,
    month     TEXT NOT NULL,
    amount    REAL NOT NULL,
    PRIMARY KEY (reg_no, month)
);
CREATE INDEX ix_rev_branch_month ON fact_revenue_monthly(branch_id, month);

-- History base (see history.py): every student of the enrollment dump, revenue built only from the live dumps.
CREATE TABLE fact_student_dump (
    reg_no       INTEGER PRIMARY KEY,
    branch_id    INTEGER NOT NULL REFERENCES dim_branch(branch_id),
    scheme       TEXT,
    course       TEXT,
    joining_date TEXT NOT NULL,
    start_date   TEXT NOT NULL,           -- later of joining date and batch start
    end_date     TEXT NOT NULL,           -- batch end
    start_month  TEXT NOT NULL,
    end_month    TEXT NOT NULL,
    n_months     INTEGER NOT NULL,
    amount       REAL NOT NULL,           -- net payments to the snapshot date / GST_DIVISOR
    dq_flag      TEXT
);

CREATE TABLE fact_revenue_dump_monthly (
    reg_no    INTEGER NOT NULL,
    branch_id INTEGER NOT NULL,
    month     TEXT NOT NULL,
    amount    REAL NOT NULL,
    PRIMARY KEY (reg_no, month)
);
CREATE INDEX ix_revdump_branch_month ON fact_revenue_dump_monthly(branch_id, month);

CREATE TABLE ref_pl_monthly (
    category TEXT NOT NULL,
    month    TEXT NOT NULL,
    metric   TEXT NOT NULL,                 -- 'revenue' / 'net_collection' / 'enrollment' / 'total_cost' / 'ebitda'
    value    REAL NOT NULL,                 -- revenue/collection/cost in Rs Cr, enrollment in count
    PRIMARY KEY (category, month, metric)
);

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE dq_issue (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    source   TEXT NOT NULL,
    code     TEXT NOT NULL,
    detail   TEXT NOT NULL,
    n_rows   INTEGER,
    amount   REAL
);


-- ===== Gap Check (the P&L team's sheets are only compared with our numbers; they never drive them) =====
CREATE TABLE ref_iter_student (               -- the student-wise 'Govt Offline Iteration' sheet, as given
    reg_no           INTEGER PRIMARY KEY,
    branch_id        INTEGER NOT NULL,
    scheme           TEXT,
    bill_amount      REAL,
    payable_ex_gst   REAL,
    status           TEXT,
    adj_collection   REAL NOT NULL,           -- 'Total Adjusted Collection'
    eff_start        TEXT NOT NULL,
    eff_end          TEXT NOT NULL,
    start_month      TEXT NOT NULL,
    end_month        TEXT NOT NULL,
    student_name     TEXT,                    -- the remaining columns of the sheet, as given
    archive_date     TEXT,
    refund_fy        REAL,
    days_diff        INTEGER,
    no_of_months     INTEGER
);

CREATE TABLE cmp_student (                    -- one row per student that is in our data and/or in the sheet (FY27 scope)
    reg_no           INTEGER PRIMARY KEY,
    branch_id        INTEGER NOT NULL,
    kind             TEXT NOT NULL,           -- 'both' / 'only_ours' / 'only_sheet'
    reason           TEXT,                    -- plain-language reason for only_* rows
    start_ours TEXT, start_sheet TEXT, end_ours TEXT, end_sheet TEXT,
    start_diff_days  INTEGER, end_diff_days INTEGER,
    start_month_differs INTEGER, end_month_differs INTEGER,
    amt_ours_all REAL, amt_ours_cut REAL, amt_sheet REAL,
    rev_ours REAL, rev_sheet REAL,            -- this year's revenue by each side
    eff_date REAL, eff_later REAL, eff_amount REAL
);
CREATE INDEX ix_cmp_branch ON cmp_student(branch_id, kind);

CREATE TABLE cmp_effect_monthly (             -- the revenue gap, split into its causes, per branch and month
    effect    TEXT NOT NULL,                  -- ours / sheet / only_sheet / only_ours / date / later_payments / amount_def; sheet_fp / only_sheet_fp / amount_def_fp = the same with the Iteration sheet's Final Total Payable instead of its Total Adjusted Collection
    branch_id INTEGER NOT NULL,
    month     TEXT NOT NULL,
    amount    REAL NOT NULL,
    PRIMARY KEY (effect, branch_id, month)
);


-- ===== Data-quality findings kept as tables (see dq_checks.py) =====
CREATE TABLE dq_check (
    check_id  TEXT PRIMARY KEY,
    title     TEXT NOT NULL,
    severity  TEXT NOT NULL,                  -- 'note' = worth a look, 'info' = for the record, 'missing' = information we still need
    n_rows    INTEGER NOT NULL,
    amount    REAL,
    detail    TEXT,
    who       TEXT                            -- who can close it
);
CREATE TABLE dq_orphan_payment (              -- payments whose student number is not in the enrollment data
    receipt_date TEXT, month TEXT, branch_id INTEGER, reg_no INTEGER, payment_mode TEXT,
    paid_amount REAL, net_paid_amount REAL, refund_amount REAL, src TEXT
);
CREATE TABLE dq_duplicate_payment (           -- same student, date, amount and mode more than once
    reg_no INTEGER, branch_id INTEGER, receipt_date TEXT, payment_mode TEXT,
    net_paid_amount REAL, times_seen INTEGER, extra_amount REAL
);
CREATE TABLE dq_student_bad_dates (           -- batch end before admission
    reg_no INTEGER, branch_id INTEGER, eff_start TEXT, eff_end TEXT, amount REAL, days_between INTEGER
);

-- ===== The whole monthly P&L sheet (horizontal view) kept line by line, as given =====
CREATE TABLE ref_pl_full (
    tab         TEXT NOT NULL,                -- workbook tab, e.g. 'Govt exam - Offline'
    row_no      INTEGER NOT NULL,             -- row number in the sheet
    group_label TEXT,                         -- the heading of the block the line belongs to
    line_label  TEXT NOT NULL,                -- the line itself, e.g. 'Revenue', 'Marketing costs'
    code        TEXT,
    month       TEXT NOT NULL,                -- 'YYYY-MM'
    value       REAL NOT NULL                 -- exactly as in the sheet (money lines are in Rs Crore)
);
CREATE INDEX ix_plfull ON ref_pl_full(tab, line_label, month);


-- ===== Nothing is ever lost: rows that disappear from the Google Sheets are kept here (they are NOT counted in any revenue number) =====
CREATE TABLE archive_enrollment_removed (
    reg_no INTEGER, branch_name TEXT, scheme TEXT, course TEXT, joining_date TEXT, join_month TEXT, batch_start TEXT, batch_end TEXT,
    status TEXT, total_collection REAL, total_payable REAL, src TEXT, first_seen_on TEXT, removed_on TEXT
);
CREATE TABLE archive_collection_removed (
    receipt_date TEXT, month TEXT, branch_name TEXT, reg_no INTEGER, payment_mode TEXT, paid_amount REAL, net_paid_amount REAL,
    refund_amount REAL, src TEXT, first_seen_on TEXT, removed_on TEXT
);
CREATE TABLE load_log (                       -- one line per day and source: how much the sheet held, how much was new, how much disappeared
    load_date TEXT NOT NULL, source TEXT NOT NULL, rows_in_sheet INTEGER, rows_new INTEGER, rows_vanished INTEGER,
    PRIMARY KEY (load_date, source)
);


-- ===== Gap register: every known difference between the GEW data, the Iteration data and Finance's P&L, with proof (see gap_report.py) =====
CREATE TABLE gap_register (
    gap_id      TEXT PRIMARY KEY,               -- G-I1 ... (GEW vs Iteration), G-F1 ... (Finance P&L), TOTAL
    grp         TEXT NOT NULL,                  -- 'GEW vs Iteration' / 'Finance P&L' / 'total'
    sort        INTEGER NOT NULL,
    title       TEXT NOT NULL,
    plain       TEXT NOT NULL,                  -- what it means, in plain words
    n           INTEGER,                        -- students (or months for the Finance rows)
    unit        TEXT,                           -- 'students' / 'months'
    eff_fy_k    REAL,                           -- effect on this year's revenue (GEW minus other side), Iteration counted on Total Adjusted Collection
    eff_win_k   REAL,                           -- the same, only for the months the Iteration sheet covers
    eff_fy_fp   REAL,                           -- the same, Iteration counted on Final Total Payable
    eff_win_fp  REAL,
    window      TEXT,                           -- e.g. 'Apr-Aug'
    owner       TEXT,
    proof_reg   INTEGER,                        -- one real student that shows it
    proof_text  TEXT,
    values_json TEXT,                           -- month-by-month numbers for the Finance rows
    evidence    TEXT                            -- key of the download that lists every case
);
CREATE TABLE gap_history (                      -- one line per gap per day: how the gaps move over time (never deleted)
    load_date TEXT NOT NULL, gap_id TEXT NOT NULL, n INTEGER,
    eff_fy_k REAL, eff_win_k REAL, eff_fy_fp REAL, eff_win_fp REAL,
    PRIMARY KEY (load_date, gap_id)
);
CREATE TABLE dq_flag_row (                      -- the rows behind the data-quality flags of the GEW data (see dq_flags.py)
    flag_id TEXT NOT NULL, reg_no INTEGER, branch_id INTEGER, detail TEXT, amount REAL, date1 TEXT, date2 TEXT
);
CREATE INDEX ix_flagrow ON dq_flag_row(flag_id);

// =====================================================================
// GEW Executive Performance Dashboard — app.js
// Data source: LIVE Google Sheet (fetched fresh on every page load)
// Math.max(...bigArray) throws "Maximum call stack size exceeded" past ~100k items (we have 130k+ rows).
// These loop-based versions have no size limit.
function safeMax(arr) { let m = -Infinity; for (let i = 0; i < arr.length; i++) if (arr[i] > m) m = arr[i]; return m; }
function safeMin(arr) { let m = Infinity; for (let i = 0; i < arr.length; i++) if (arr[i] < m) m = arr[i]; return m; }

// =====================================================================

// ---- Live Google Sheet config ----
// Each tab can live in its own spreadsheet — Google Sheets has hard limits
// (rows/cells) that this project has already hit once, so data sometimes
// has to be split into a separate spreadsheet. Every tab below carries its
// own sheetId + tab name, so any tab can be pointed at a different sheet
// without touching anything else.
const PRIMARY_SHEET_ID = '14eBLfRqIjayPqI455sZptF5WxVawerNZL5ySTTgrJw4';
const COLLECTION_SHEET_ID = '1pyAaQsmGdn1AeBxdeJwas6ppXG3h3ROSqdB-r1RIk2c'; // Collection FY'27 outgrew the primary sheet, moved here

const TABS = {
  enroll: { sheetId: PRIMARY_SHEET_ID, tab: "Enroll Dump Fy'27" },        // Enrollment + Booking (this FY, date-wise)
  collection: { sheetId: COLLECTION_SHEET_ID, tab: "Collection FY'27" }, // Collection (this FY, date-wise) — moved to its own sheet
  targets: { sheetId: PRIMARY_SHEET_ID, tab: 'Target_Long' },             // Month-wise Enroll Target + Booking Target, per branch, full FY
  enrollHistory: { sheetId: PRIMARY_SHEET_ID, tab: "Enroll Dump FY'26" },     // All historical enrollments, up to end of previous FY (Jan'24 – Mar'26)
  collectionHistory: { sheetId: PRIMARY_SHEET_ID, tab: 'Collection Dump' }   // All historical collections, up to end of previous FY (Jan'24 – Mar'26)
};

function sheetCsvUrl(sheetId, tabName) {
  // (kept for reference/debugging — paste this in a new tab to sanity-check a
  // tab's data manually. Not used by the app itself, since fetch() to this
  // endpoint gets CORS-blocked; see fetchGvizTab() below for the real path.)
  return `https://docs.google.com/spreadsheets/d/${sheetId}/gviz/tq?tqx=out:csv&sheet=${encodeURIComponent(tabName)}`;
}

// Cohort mapping config (Category -> Cohort -> Branch hierarchy)
const COHORT_MAPPING = {
  "Agra Government": 1,
  "Bareilly Government": 1,
  "Gorakhpur Government": 1,
  "Kanpur Government": 1,
  "Lucknow - Alambagh Government": 1,
  "Lucknow Government": 1,
  "Varanasi Government - Durgakund": 1,

  "Bhopal Government": 2,
  "Indore Government": 2,
  "Jabalpur Government": 2,
  "Nagpur Government": 2,
  "Raipur Government": 2,

  "Muzaffarpur Government": 3,
  "Patna - Kankarbagh Government": 3,
  "Patna Government": 3,
  "Ranchi Government - Lalpur": 3,

  "Dehradun Government": 4,
  "Delhi Government": 4,
  "Jaipur Government-Gopalpura": 4,
  "Jammu Government": 4,

  "Prayagraj Government-Katra": 5,

  "Berhampur Government": 6,
  "Bhubaneswar Government": 6,
  "Vishakhapatnam Government": 6,

  "Kolkata Government": 7,

  "Guwahati Government": 8,

  "Siliguri Government": 9
};

function cohortLabel(id) {
  return `Cohort ${id}` + (Number(id) === 9 ? ' (Siliguri)' : '');
}

// Raw parsed data stores
let enrollmentsRaw = [];
let collectionsRaw = [];
let targetsRaw = [];
let liveTargetsRaw = [];      // targets exactly as in the sheet (the default, unchanged behaviour)
let aopChoice = 'live';       // 'live' or a saved plan id from the portal
let dataAsOfDate = null; // latest date actually present in the data (freshness indicator)

// Chart instances
let chartInstances = { 1: null, 2: null };

// =====================================================================
// Parsing helpers
// =====================================================================

// Robust numeric parser — strips currency symbols, commas, quotes, spaces
function toNumber(v) {
  if (v === null || v === undefined || v === '') return 0;
  const cleaned = String(v).replace(/[^0-9.\-]/g, '');
  const n = parseFloat(cleaned);
  return isNaN(n) ? 0 : n;
}

const MONTH_NAMES = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'];

// Flexible date parser — handles "1-Apr-2026", "01 Apr, 2026", "4/1/2026" etc.
function parseFlexibleDate(str) {
  if (!str) return null;
  str = String(str).trim();
  if (!str) return null;

  // Pattern: d-Mon-yyyy / d Mon, yyyy / d-Mon-yy
  let m = str.match(/^(\d{1,2})[\s-]+([A-Za-z]{3,})[,\s-]+(\d{2,4})/);
  if (m) {
    const day = parseInt(m[1], 10);
    const mon = MONTH_NAMES.indexOf(m[2].slice(0, 3).toLowerCase());
    let year = parseInt(m[3], 10);
    if (year < 100) year += 2000;
    if (mon !== -1 && !isNaN(day) && !isNaN(year)) return new Date(year, mon, day);
  }

  // Pattern: m/d/yyyy (US-style, used in Collection & Enroll dumps)
  m = str.match(/^(\d{1,2})\/(\d{1,2})\/(\d{2,4})/);
  if (m) {
    const month = parseInt(m[1], 10) - 1;
    const day = parseInt(m[2], 10);
    let year = parseInt(m[3], 10);
    if (year < 100) year += 2000;
    if (!isNaN(month) && !isNaN(day) && !isNaN(year)) return new Date(year, month, day);
  }

  // Fallback to native parser
  const d = new Date(str);
  return isNaN(d.getTime()) ? null : d;
}

function dayKey(d) {
  return d.getFullYear() * 10000 + (d.getMonth() + 1) * 100 + d.getDate();
}

// Get overlap days between date range [start, end] and a specific calendar month (year, monthIndex)
function getOverlapDays(start, end, year, month) {
  const monthStart = new Date(year, month, 1);
  const monthEnd = new Date(year, month + 1, 0);

  const overlapStart = new Date(Math.max(start, monthStart));
  const overlapEnd = new Date(Math.min(end, monthEnd));

  if (overlapStart > overlapEnd) return 0;

  const diffTime = Math.abs(overlapEnd - overlapStart);
  return Math.ceil(diffTime / (1000 * 60 * 60 * 24)) + 1;
}

// =====================================================================
// Formatting helpers
// =====================================================================
function formatCr(val) {
  return '₹ ' + (val / 10000000).toFixed(2) + ' Cr';
}
function formatAOV(val) {
  if (isNaN(val) || !isFinite(val)) return '₹ 0';
  return '₹ ' + Math.round(val).toLocaleString('en-IN');
}
function formatNum(val) {
  return Math.round(val).toLocaleString('en-IN');
}
function formatPct(val) {
  if (val === null) return '—';
  const sign = val >= 0 ? '+' : '';
  return sign + val.toFixed(1) + '%';
}
function formatDateLong(d) {
  if (!d) return '—';
  return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'long', year: 'numeric' });
}
// <input type="date"> always gives "YYYY-MM-DD". Feeding that string straight into
// `new Date(str)` parses it as UTC midnight (ISO 8601 rule), which only coincidentally
// matches the right local calendar day in timezones at/ahead of UTC (like IST). In any
// timezone BEHIND UTC it silently shifts the date back by one day. Parse manually instead
// so the selected date is always exactly what the user picked, regardless of the machine's
// timezone.
function parseInputDate(str) {
  if (!str) return null;
  const [y, m, d] = str.split('-').map(Number);
  return new Date(y, m - 1, d);
}

function toInputDateStr(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

// Indian Financial Year start (Apr 1) for whichever FY "today" falls in
function getFYStart(date) {
  const y = date.getFullYear();
  const m = date.getMonth(); // 0-indexed, April = 3
  return m >= 3 ? new Date(y, 3, 1) : new Date(y - 1, 3, 1);
}

// Quick date-range presets for the trend charts.
//   MTD    — Month Till Date: 1st of this month -> today
//   LMTD   — Last Month Till Date: 1st of previous month -> same day-of-month
//            last month (clamped if last month is shorter)
//   LYMTD  — Last Year Month Till Date: same month, one year earlier,
//            1st -> same day-of-month
//   YTD    — Year Till Date: start of this FY (Apr 1) -> today
//   LYTD   — Last Year Till Date: same FY-to-date window, one year earlier
function computePresetRange(preset, today) {
  const clampDay = (year, month, day) => {
    const daysInMonth = new Date(year, month + 1, 0).getDate();
    return Math.min(day, daysInMonth);
  };

  if (preset === 'MTD') {
    return { start: new Date(today.getFullYear(), today.getMonth(), 1), end: today };
  }
  if (preset === 'LMTD') {
    const y = today.getMonth() === 0 ? today.getFullYear() - 1 : today.getFullYear();
    const m = today.getMonth() === 0 ? 11 : today.getMonth() - 1;
    return { start: new Date(y, m, 1), end: new Date(y, m, clampDay(y, m, today.getDate())) };
  }
  if (preset === 'LYMTD') {
    const y = today.getFullYear() - 1;
    const m = today.getMonth();
    return { start: new Date(y, m, 1), end: new Date(y, m, clampDay(y, m, today.getDate())) };
  }
  if (preset === 'YTD') {
    return { start: getFYStart(today), end: today };
  }
  if (preset === 'LYTD') {
    const fyStart = getFYStart(today);
    return {
      start: new Date(fyStart.getFullYear() - 1, fyStart.getMonth(), fyStart.getDate()),
      end: new Date(today.getFullYear() - 1, today.getMonth(), today.getDate())
    };
  }
  return null; // 'Custom' — leave date inputs as they are
}

// =====================================================================
// Bootstrap
// =====================================================================
window.addEventListener('DOMContentLoaded', () => {
  loadData();
  setupEventListeners();
});

// ---- JSONP fetch for the sheet (avoids fetch()/XHR CORS entirely) ----
// Google's gviz endpoint does not reliably send Access-Control-Allow-Origin,
// so a plain fetch() to it fails with a CORS error in most browsers/setups.
// A <script> tag is NOT subject to CORS (only fetch/XHR are), so we use
// gviz's built-in JSONP support (tqx=responseHandler:<callback>) instead —
// this is the standard, reliable way to consume gviz from client-side JS.
let gvizCallbackCounter = 0;
function fetchGvizTab(sheetId, tabName, timeoutMs = 45000) {
  return new Promise((resolve, reject) => {
    const cbName = `__gviz_cb_${gvizCallbackCounter++}`;
    let script;

    const timeout = setTimeout(() => {
      // Don't delete the callback outright — some browsers (notably Edge with
      // Tracking Prevention on) heavily throttle third-party script loads from
      // file:// pages, so a response can still legitimately arrive *after* we
      // give up waiting. Swap in a no-op instead of `delete` so a late arrival
      // fails silently instead of throwing "callback is not defined" in the
      // console.
      window[cbName] = () => {};
      if (script && script.parentNode) script.parentNode.removeChild(script);
      reject(new Error(`Timed out loading "${tabName}" after ${Math.round(timeoutMs / 1000)}s — check your internet connection and that the sheet is shared as "Anyone with the link". (If you're on Microsoft Edge, its "Tracking Prevention" feature is known to badly throttle Google requests from local files — see README for the fix, or try Chrome.)`));
    }, timeoutMs);

    function cleanup() {
      clearTimeout(timeout);
      delete window[cbName];
      if (script && script.parentNode) script.parentNode.removeChild(script);
    }

    window[cbName] = (response) => {
      cleanup();
      if (!response || response.status === 'error') {
        const msg = (response && response.errors && response.errors[0] && response.errors[0].detailed_message) || 'unknown error';
        reject(new Error(`Sheet returned an error for tab "${tabName}": ${msg}. Check the tab name matches exactly, and that this sheet is shared as "Anyone with the link".`));
        return;
      }
      resolve(response.table);
    };

    script = document.createElement('script');
    script.src = `https://docs.google.com/spreadsheets/d/${sheetId}/gviz/tq?tqx=responseHandler:${cbName}&sheet=${encodeURIComponent(tabName)}&_=${Date.now()}`;
    script.onerror = () => {
      clearTimeout(timeout);
      window[cbName] = () => {};
      if (script.parentNode) script.parentNode.removeChild(script);
      reject(new Error(`Failed to load tab "${tabName}" — check the sheet is shared as "Anyone with the link" and the tab name is correct.`));
    };
    document.body.appendChild(script);
  });
}

// One retry on failure — helps with transient slowness (e.g. Edge Tracking
// Prevention throttling third-party script loads from file:// pages), which
// often succeeds well within the timeout on a second attempt.
function fetchGvizTabWithRetry(tabConfig) {
  return fetchGvizTab(tabConfig.sheetId, tabConfig.tab).catch(() => fetchGvizTab(tabConfig.sheetId, tabConfig.tab));
}

// Convert a gviz table (cols + rows) into an array of plain row-objects,
// keyed by column label — equivalent to what Papa.parse(csv, {header:true})
// would produce. Auto-dedupes repeated column labels (e.g. this sheet has
// two columns both named "center") by suffixing duplicates, so a later
// duplicate never silently overwrites the real value the way an object
// literal built naively from repeated keys would.
function gvizTableToObjects(table) {
  const seen = {};
  const headers = table.cols.map(c => {
    const label = c.label || c.id || '';
    seen[label] = (seen[label] || 0) + 1;
    return seen[label] > 1 ? `${label}_dup${seen[label] - 1}` : label;
  });

  return table.rows.map(row => {
    const obj = {};
    (row.c || []).forEach((cell, i) => {
      // Prefer the formatted display string (f) — matches what a CSV export
      // would have given us, so all existing date/number parsing just works.
      // Fall back to the raw value (v) when there's no formatted variant.
      obj[headers[i]] = cell ? (cell.f !== undefined && cell.f !== null ? cell.f : (cell.v ?? '')) : '';
    });
    return obj;
  });
}

function parseEnrollRows(rows) {
  return rows
    .filter(r => r.Month !== 'December99' && r.regno)
    .map(r => {
      const parsedDate = parseFlexibleDate(r.new_receipt_date);
      return {
        center: r.center,
        parsedDate,
        dayKey: parsedDate ? dayKey(parsedDate) : null,
        payable: toNumber(r.total_payable_cr)
      };
    })
    .filter(r => r.parsedDate !== null && r.center);
}

function parseCollectionRows(rows) {
  // This sheet has two columns both literally named "center". gvizTableToObjects
  // renames the 2nd occurrence to "center_dup1" so it doesn't silently clobber
  // the first. Which of the two is actually populated has been observed to
  // differ, so pick whichever one isn't blank per row instead of assuming a
  // fixed position — this is what actually matters, not which column index.
  return rows
    .map(r => {
      const parsedDate = parseFlexibleDate(r.receipt_date);
      const center = (r.center && String(r.center).trim()) || (r.center_dup1 && String(r.center_dup1).trim()) || '';
      return {
        center,
        parsedDate,
        dayKey: parsedDate ? dayKey(parsedDate) : null,
        netPaid: toNumber(r.net_paid_amount)
      };
    })
    .filter(r => r.parsedDate !== null && r.center);
}

function parseTargetRows(rows) {
  return rows
    .map(r => ({
      Center: r.Center,
      parsedDate: parseFlexibleDate(r.Month),
      enrollTarget: toNumber(r['Enroll Target']),
      bookingTarget: toNumber(r['Booking Target'])
    }))
    .filter(r => r.parsedDate !== null && r.Center);
}

function loadData() {
  Promise.all([
    fetchGvizTabWithRetry(TABS.enroll),
    fetchGvizTabWithRetry(TABS.collection),
    fetchGvizTabWithRetry(TABS.targets),
    fetchGvizTabWithRetry(TABS.enrollHistory),
    fetchGvizTabWithRetry(TABS.collectionHistory)
  ]).then(([enrollTable, collTable, targetTable, enrollHistTable, collHistTable]) => {
    // Current FY + historical (pre-FY'27) data merged into one continuous timeline.
    // No date overlap between the two (history ends Mar'26, current starts Apr'26),
    // so this is a safe concat — lets Section 2 & Section 3 date-pickers query any
    // date back to Jan 2024, while Section 1 (YTD vs AOP) still only ever looks at
    // the current-FY window, so it's unaffected.
    const enrollCurObjs = gvizTableToObjects(enrollTable);
    const enrollCur = parseEnrollRows(enrollCurObjs);
    showDataWarning(enrollCurObjs.filter(r => r.Month !== 'December99' && r.regno).length, enrollCur.length);
    enrollmentsRaw = [...parseEnrollRows(gvizTableToObjects(enrollHistTable)), ...enrollCur];
    collectionsRaw = [...parseCollectionRows(gvizTableToObjects(collHistTable)), ...parseCollectionRows(gvizTableToObjects(collTable))];
    liveTargetsRaw = parseTargetRows(gvizTableToObjects(targetTable));
    targetsRaw = liveTargetsRaw;

    // Data freshness: latest actual transaction date found anywhere in the data
    const allDates = [
      ...enrollmentsRaw.map(r => r.parsedDate),
      ...collectionsRaw.map(r => r.parsedDate)
    ];
    dataAsOfDate = allDates.length ? new Date(safeMax(allDates)) : new Date();
    document.getElementById('data-as-of').innerText = `Data as of: ${formatDateLong(dataAsOfDate)}`;

    document.getElementById('loading').style.opacity = '0';
    setTimeout(() => document.getElementById('loading').style.display = 'none', 300);

    populateBranches();
    initDefaultDateRanges();
    initAopSelector().then(() => renderDashboard());
  }).catch(err => {
    console.error('Error loading live sheet data:', err);
    document.querySelector('.loading-text').innerText = 'Error loading live sheet data: ' + err.message;
  });
}

// If many students in the sheet's enrollment tab have no usable date, Enrollment and Booking would silently come out far too low.
// Say so on the page instead of showing wrong numbers without a warning.
function showDataWarning(totalRows, usableRows) {
  const el = document.getElementById('data-warning');
  const missing = totalRows - usableRows;
  if (totalRows > 0 && missing > 0.02 * totalRows) {
    el.textContent = `Data problem in the sheet: the "Enroll Dump Fy27" tab has no date for ${missing.toLocaleString('en-IN')} of ${totalRows.toLocaleString('en-IN')} students. ` +
      `Enrollment and Booking below are therefore far too low and should not be used until the sheet is fixed. (Collection is not affected.)`;
    el.style.display = 'block';
  } else {
    el.style.display = 'none';
  }
}

// ---- Plan (AOP) selector --------------------------------------------------------------
// Default = the live sheet, exactly as before. When the page is opened from the portal server, the saved plans
// (AOP1, AOP2 ...) are listed too, and choosing one makes every "vs AOP / target" number follow that plan.
async function initAopSelector() {
  const group = document.getElementById('aop-group'), sel = document.getElementById('filter-aop');
  try {
    const r = await fetch('/api/meta');
    if (!r.ok) throw new Error('portal not reachable');
    const meta = await r.json();
    const keep = aopChoice;
    sel.innerHTML = '<option value="live">Live sheet (current plan)</option>' +
      meta.aops.map(a => `<option value="${a.aop_id}">${a.name} (saved)</option>`).join('');
    group.style.display = '';
    sel.value = [...sel.options].some(o => o.value === keep) ? keep : 'live';
    aopChoice = sel.value;
    await applyAopChoice(false);
  } catch (e) {
    group.style.display = 'none';       // opened as a plain file: the sheet's own plan is used, as before
    aopChoice = 'live'; targetsRaw = liveTargetsRaw; setAopBadge();
  }
}

async function applyAopChoice(render = true) {
  const sel = document.getElementById('filter-aop');
  aopChoice = sel.value;
  if (aopChoice === 'live') {
    targetsRaw = liveTargetsRaw;
  } else {
    const r = await fetch(`/api/targets?aop_id=${encodeURIComponent(aopChoice)}`);
    if (!r.ok) { alert('Could not load that plan. Showing the live sheet instead.'); sel.value = 'live'; aopChoice = 'live'; targetsRaw = liveTargetsRaw; }
    else targetsRaw = parseTargetRows(await r.json());
  }
  setAopBadge();
  if (render) renderDashboard();
}

function setAopBadge() {
  const sel = document.getElementById('filter-aop'), badge = document.getElementById('aop-badge');
  badge.textContent = aopChoice === 'live' ? '' : `· plan: ${sel.options[sel.selectedIndex].text.replace(' (saved)', '')}`;
}

function populateBranches() {
  const cohortFilter = document.getElementById('filter-cohort').value;
  const branches = [...new Set(enrollmentsRaw.map(r => r.center))].sort();
  const select = document.getElementById('filter-branch');
  select.innerHTML = '<option value="all" selected>All Branches</option>';
  branches.forEach(b => {
    if (cohortFilter === 'all' || COHORT_MAPPING[b] == cohortFilter) {
      const opt = document.createElement('option');
      opt.value = b;
      opt.innerText = b.replace(' Government', '');
      select.appendChild(opt);
    }
  });
}

function initDefaultDateRanges() {
  const today = dataAsOfDate || new Date();
  const fyStart = getFYStart(today);
  const monthStart = new Date(today.getFullYear(), today.getMonth(), 1);

  // Section 2 range: default Month-to-date
  document.getElementById('date-start').value = toInputDateStr(monthStart);
  document.getElementById('date-end').value = toInputDateStr(today);

  // Chart 1 default: full YTD so far
  document.getElementById('chart1-start').value = toInputDateStr(fyStart);
  document.getElementById('chart1-end').value = toInputDateStr(today);

  // Chart 2 default: last 30 days
  const last30 = new Date(today);
  last30.setDate(last30.getDate() - 29);
  document.getElementById('chart2-start').value = toInputDateStr(last30);
  document.getElementById('chart2-end').value = toInputDateStr(today);

  // "Line 2" (2nd line) date ranges — default to the equivalent period
  // one year earlier, so picking a KPI2 with no changes gives an instant
  // "this year vs last year" read. Both are still fully editable per chart.
  const oneYearBack = (d) => new Date(d.getFullYear() - 1, d.getMonth(), d.getDate());
  document.getElementById('chart1-start2').value = toInputDateStr(oneYearBack(fyStart));
  document.getElementById('chart1-end2').value = toInputDateStr(oneYearBack(today));
  document.getElementById('chart2-start2').value = toInputDateStr(oneYearBack(last30));
  document.getElementById('chart2-end2').value = toInputDateStr(oneYearBack(today));
}

// =====================================================================
// Event listeners
// =====================================================================
function setupEventListeners() {
  document.getElementById('filter-cohort').addEventListener('change', () => {
    populateBranches();
    document.getElementById('filter-branch').value = 'all';
    renderDashboard();
  });

  document.getElementById('filter-branch').addEventListener('change', renderDashboard);
  document.getElementById('filter-aop').addEventListener('change', () => applyAopChoice(true));
  document.getElementById('btn-back').addEventListener('click', goBack);
  if (location.protocol !== 'file:') document.getElementById('portal-link').style.display = 'inline-block';   // only when opened from the portal

  // Range quick toggles (Section 2)
  document.getElementById('btn-mtd').addEventListener('click', (e) => {
    const today = dataAsOfDate || new Date();
    const monthStart = new Date(today.getFullYear(), today.getMonth(), 1);
    setActiveToggle(e.target, toInputDateStr(monthStart), toInputDateStr(today));
  });
  document.getElementById('btn-l7d').addEventListener('click', (e) => {
    const today = dataAsOfDate || new Date();
    const start = new Date(today); start.setDate(start.getDate() - 6);
    setActiveToggle(e.target, toInputDateStr(start), toInputDateStr(today));
  });
  document.getElementById('btn-l15d').addEventListener('click', (e) => {
    const today = dataAsOfDate || new Date();
    const start = new Date(today); start.setDate(start.getDate() - 14);
    setActiveToggle(e.target, toInputDateStr(start), toInputDateStr(today));
  });
  document.getElementById('btn-custom').addEventListener('click', (e) => {
    document.querySelectorAll('.period-toggles button').forEach(b => b.classList.remove('active'));
    e.target.classList.add('active');
  });

  document.getElementById('date-start').addEventListener('change', () => {
    document.querySelectorAll('.period-toggles button').forEach(b => b.classList.remove('active'));
    document.getElementById('btn-custom').classList.add('active');
    renderDashboard();
  });
  document.getElementById('date-end').addEventListener('change', () => {
    document.querySelectorAll('.period-toggles button').forEach(b => b.classList.remove('active'));
    document.getElementById('btn-custom').classList.add('active');
    renderDashboard();
  });

  document.getElementById('btn-export-csv').addEventListener('click', exportCSV);
  document.getElementById('btn-refresh').addEventListener('click', () => {
    document.getElementById('loading').style.display = 'flex';
    document.getElementById('loading').style.opacity = '1';
    document.querySelector('.loading-text').innerText = 'Refreshing latest data from sheet...';
    loadData();
  });

  // Chart controls — each chart is fully independent (own KPI + own date
  // range), plus an optional 2nd "Compare with" line (own KPI + own range),
  // plus a color picker per line.
  ['chart1', 'chart2'].forEach(id => {
    ['-kpi', '-start', '-end', '-kpi2', '-start2', '-end2', '-color1', '-color2'].forEach(suffix => {
      document.getElementById(`${id}${suffix}`).addEventListener('change', updateCharts);
    });
  });

  // Quick date-range presets (MTD/LMTD/LYMTD/YTD/LYTD) for each of the 4
  // date-range pairs across the two charts.
  wirePresetDropdown('chart1-preset', 'chart1-start', 'chart1-end');
  wirePresetDropdown('chart1-preset2', 'chart1-start2', 'chart1-end2');
  wirePresetDropdown('chart2-preset', 'chart2-start', 'chart2-end');
  wirePresetDropdown('chart2-preset2', 'chart2-start2', 'chart2-end2');


  // Click a cohort row to drill into its branches
  document.getElementById('table-cohort-ytd-body').addEventListener('click', handleCohortRowClick);
  document.getElementById('table-cohort-range-body').addEventListener('click', handleCohortRowClick);
}

function handleCohortRowClick(e) {
  const tr = e.target.closest('tr[data-cohort-id]');
  if (!tr) return;
  document.getElementById('filter-cohort').value = tr.dataset.cohortId;
  populateBranches();
  document.getElementById('filter-branch').value = 'all';
  renderDashboard();
}

function setActiveToggle(btnEl, startStr, endStr) {
  document.querySelectorAll('.period-toggles button').forEach(b => b.classList.remove('active'));
  btnEl.classList.add('active');
  document.getElementById('date-start').value = startStr;
  document.getElementById('date-end').value = endStr;
  renderDashboard();
}

// =====================================================================
// Core aggregation — generalized: groups by Cohort (when "All Cohorts" is
// selected) OR by Branch (when a specific cohort is selected, showing that
// cohort's branches individually instead of one lumped total).
// =====================================================================
function buildGroupedData(start, end, cohortFilter, branchFilter) {
  const groupByBranch = cohortFilter !== 'all';
  const groups = new Map();

  const passesFilter = (center) => {
    if (!center) return false;
    if (branchFilter !== 'all' && center !== branchFilter) return false;
    const cid = COHORT_MAPPING[center];
    if (cohortFilter !== 'all' && cid != cohortFilter) return false;
    return true;
  };

  const keyFor = (center) => {
    if (groupByBranch) return center;
    const cid = COHORT_MAPPING[center];
    return cid ? String(cid) : null;
  };

  const labelFor = (key) => groupByBranch ? key.replace(' Government', '') : cohortLabel(key);

  function ensure(key) {
    if (!groups.has(key)) {
      groups.set(key, {
        key, label: labelFor(key), cohortId: groupByBranch ? COHORT_MAPPING[key] : Number(key),
        booking: 0, enroll: 0, collection: 0,
        bookingTarget: 0, enrollTarget: 0,
        fullAopBooking: 0, fullAopEnroll: 0
      });
    }
    return groups.get(key);
  }

  // Targets: full-year AOP + prorated target-till-date for the given window
  targetsRaw.forEach(t => {
    if (!passesFilter(t.Center)) return;
    const key = keyFor(t.Center);
    if (key === null) return;
    const g = ensure(key);
    g.fullAopBooking += t.bookingTarget;
    g.fullAopEnroll += t.enrollTarget;

    const overlap = getOverlapDays(start, end, t.parsedDate.getFullYear(), t.parsedDate.getMonth());
    if (overlap > 0) {
      const totalDaysInMonth = new Date(t.parsedDate.getFullYear(), t.parsedDate.getMonth() + 1, 0).getDate();
      const ratio = overlap / totalDaysInMonth;
      g.bookingTarget += t.bookingTarget * ratio;
      g.enrollTarget += t.enrollTarget * ratio;
    }
  });

  // Enrollments & Booking
  enrollmentsRaw.forEach(r => {
    if (!passesFilter(r.center)) return;
    if (r.parsedDate < start || r.parsedDate > end) return;
    const key = keyFor(r.center);
    if (key === null) return;
    const g = ensure(key);
    g.booking += r.payable;
    g.enroll += 1;
  });

  // Collection
  collectionsRaw.forEach(r => {
    if (!passesFilter(r.center)) return;
    if (r.parsedDate < start || r.parsedDate > end) return;
    const key = keyFor(r.center);
    if (key === null) return;
    const g = ensure(key);
    g.collection += r.netPaid;
  });

  const list = Array.from(groups.values());
  list.sort((a, b) => groupByBranch ? a.label.localeCompare(b.label) : a.cohortId - b.cohortId);
  return { list, groupByBranch };
}

// "vs Target" — how far the actual is from the target-till-date, in % of that target.
// (Actual - TargetTillDate) / TargetTillDate x 100. Returns null if target is 0 (undefined).
function vsTargetPct(actual, target) {
  if (!target) return null;
  return (actual - target) / target * 100;
}

// =====================================================================
// Master render
// =====================================================================
function renderDashboard() {
  const today = dataAsOfDate || new Date();
  const fyStart = getFYStart(today);

  const start = parseInputDate(document.getElementById('date-start').value);
  const end = parseInputDate(document.getElementById('date-end').value);
  const cohortFilter = document.getElementById('filter-cohort').value;
  const branchFilter = document.getElementById('filter-branch').value;

  const ytdGrouped = buildGroupedData(fyStart, today, cohortFilter, branchFilter);
  const rangeGrouped = buildGroupedData(start, end, cohortFilter, branchFilter);

  updateCardUI('ytd', sumGroup(ytdGrouped.list));
  updateCardUI('range', sumGroup(rangeGrouped.list));

  renderGroupTable('ytd', ytdGrouped);
  renderGroupTable('range', rangeGrouped);

  updateCharts();
  renderTrendsSection();
  if (typeof renderBranchTrends === 'function') renderBranchTrends();
  updateBackButton();
}

// One step back from a drill-down: branch -> its cohort -> all cohorts.
function updateBackButton() {
  const cohort = document.getElementById('filter-cohort').value, branch = document.getElementById('filter-branch').value;
  const btn = document.getElementById('btn-back');
  if (branch !== 'all') { btn.textContent = `← Back to Cohort ${cohort === 'all' ? (COHORT_MAPPING[branch] || '') : cohort}`; btn.style.display = ''; }
  else if (cohort !== 'all') { btn.textContent = '← Back to all cohorts'; btn.style.display = ''; }
  else btn.style.display = 'none';
}

function goBack() {
  const cohort = document.getElementById('filter-cohort');
  if (document.getElementById('filter-branch').value !== 'all') {
    if (cohort.value === 'all') { cohort.value = String(COHORT_MAPPING[document.getElementById('filter-branch').value] || 'all'); populateBranches(); }
    document.getElementById('filter-branch').value = 'all';
  } else {
    cohort.value = 'all'; populateBranches(); document.getElementById('filter-branch').value = 'all';
  }
  renderDashboard();
}

function sumGroup(list) {
  const total = { booking: 0, enroll: 0, collection: 0, bookingTarget: 0, enrollTarget: 0, fullAopBooking: 0, fullAopEnroll: 0 };
  list.forEach(g => {
    total.booking += g.booking;
    total.enroll += g.enroll;
    total.collection += g.collection;
    total.bookingTarget += g.bookingTarget;
    total.enrollTarget += g.enrollTarget;
    total.fullAopBooking += g.fullAopBooking;
    total.fullAopEnroll += g.fullAopEnroll;
  });
  return total;
}

function updateCardUI(prefix, d) {
  // Booking
  document.getElementById(`${prefix}-booking-actual`).innerText = formatCr(d.booking);
  const bookingAopPct = d.fullAopBooking ? (d.booking / d.fullAopBooking * 100) : 0;
  document.getElementById(`${prefix}-booking-aop-pct`).innerText = bookingAopPct.toFixed(1) + '%';
  document.getElementById(`${prefix}-booking-aop-label`).innerText = `vs AOP ${formatCr(d.fullAopBooking)}`;
  document.getElementById(`${prefix}-booking-target`).innerText = `Target till date: ${formatCr(d.bookingTarget)}`;
  updateVsTargetUI(`${prefix}-booking-vs-target`, vsTargetPct(d.booking, d.bookingTarget));

  // Enrollment
  document.getElementById(`${prefix}-enroll-actual`).innerText = formatNum(d.enroll);
  const enrollAopPct = d.fullAopEnroll ? (d.enroll / d.fullAopEnroll * 100) : 0;
  document.getElementById(`${prefix}-enroll-aop-pct`).innerText = enrollAopPct.toFixed(1) + '%';
  document.getElementById(`${prefix}-enroll-aop-label`).innerText = `vs AOP ${formatNum(d.fullAopEnroll)}`;
  document.getElementById(`${prefix}-enroll-target`).innerText = `Target till date: ${formatNum(d.enrollTarget)}`;
  updateVsTargetUI(`${prefix}-enroll-vs-target`, vsTargetPct(d.enroll, d.enrollTarget));

  // Collection (target/AOP mirrors Booking's, per business definition)
  document.getElementById(`${prefix}-collection-actual`).innerText = formatCr(d.collection);
  const collAopPct = d.fullAopBooking ? (d.collection / d.fullAopBooking * 100) : 0;
  document.getElementById(`${prefix}-collection-aop-pct`).innerText = collAopPct.toFixed(1) + '%';
  document.getElementById(`${prefix}-collection-aop-label`).innerText = `vs AOP ${formatCr(d.fullAopBooking)}`;
  document.getElementById(`${prefix}-collection-target`).innerText = `Target till date: ${formatCr(d.bookingTarget)}`;
  updateVsTargetUI(`${prefix}-collection-vs-target`, vsTargetPct(d.collection, d.bookingTarget));

  // AOV = Booking / Enrollment
  const actualAOV = d.enroll ? (d.booking / d.enroll) : 0;
  const targetAOV = d.enrollTarget ? (d.bookingTarget / d.enrollTarget) : 0;
  const aopAOV = d.fullAopEnroll ? (d.fullAopBooking / d.fullAopEnroll) : 0;

  document.getElementById(`${prefix}-aov-actual`).innerText = formatAOV(actualAOV);
  const aovAopPct = aopAOV ? (actualAOV / aopAOV * 100) : 0;
  document.getElementById(`${prefix}-aov-aop-pct`).innerText = aovAopPct.toFixed(1) + '%';
  document.getElementById(`${prefix}-aov-aop-label`).innerText = `vs AOP ${formatAOV(aopAOV)}`;
  document.getElementById(`${prefix}-aov-target`).innerText = `Target till date: ${formatAOV(targetAOV)}`;
  updateVsTargetUI(`${prefix}-aov-vs-target`, vsTargetPct(actualAOV, targetAOV));
}

function updateVsTargetUI(id, val) {
  const el = document.getElementById(id);
  if (val === null) {
    el.className = 'card-vs-target';
    el.innerHTML = `vs Target —`;
  } else if (val >= 0) {
    el.className = 'card-vs-target positive';
    el.innerHTML = `vs Target ▲ ${val.toFixed(1)}%`;
  } else {
    el.className = 'card-vs-target negative';
    el.innerHTML = `vs Target ▼ ${Math.abs(val).toFixed(1)}%`;
  }
}

// =====================================================================
// Cohort / Branch table rendering
// =====================================================================
function renderGroupTable(tablePrefix, grouped) {
  const tbody = document.getElementById(`table-cohort-${tablePrefix}-body`);
  const labelHeader = document.getElementById(`table-${tablePrefix}-label-header`);
  labelHeader.innerText = grouped.groupByBranch ? 'Branch' : 'Cohort';
  tbody.innerHTML = '';

  const rows = grouped.list.filter(g => g.booking || g.enroll || g.collection || g.bookingTarget || g.enrollTarget);

  rows.forEach(g => {
    const bookingAopPct = g.fullAopBooking ? (g.booking / g.fullAopBooking * 100) : 0;
    const bookingVsTarget = vsTargetPct(g.booking, g.bookingTarget);

    const enrollAopPct = g.fullAopEnroll ? (g.enroll / g.fullAopEnroll * 100) : 0;
    const enrollVsTarget = vsTargetPct(g.enroll, g.enrollTarget);

    const collectionAopPct = g.fullAopBooking ? (g.collection / g.fullAopBooking * 100) : 0;
    const collectionVsTarget = vsTargetPct(g.collection, g.bookingTarget);

    const actualAOV = g.enroll ? (g.booking / g.enroll) : 0;
    const aopAOV = g.fullAopEnroll ? (g.fullAopBooking / g.fullAopEnroll) : 0;
    const targetAOV = g.enrollTarget ? (g.bookingTarget / g.enrollTarget) : 0;
    const aovAopPct = aopAOV ? (actualAOV / aopAOV * 100) : 0;
    const aovVsTarget = vsTargetPct(actualAOV, targetAOV);

    const tr = document.createElement('tr');
    if (!grouped.groupByBranch) {
      tr.dataset.cohortId = g.cohortId;
      tr.classList.add('row-clickable');
      tr.title = 'Click to view branches in this cohort';
    }
    tr.innerHTML = `
      <td>${g.label}</td>

      <td class="col-booking">${(g.booking / 10000000).toFixed(2)}</td>
      <td class="col-booking">${(g.bookingTarget / 10000000).toFixed(2)}</td>
      <td class="col-booking ${vsClass(bookingVsTarget)}">${formatPct(bookingVsTarget)}</td>
      <td class="col-booking">${(g.fullAopBooking / 10000000).toFixed(2)}</td>
      <td class="col-booking">${bookingAopPct.toFixed(1)}%</td>

      <td class="col-enrollment">${formatNum(g.enroll)}</td>
      <td class="col-enrollment">${formatNum(g.enrollTarget)}</td>
      <td class="col-enrollment ${vsClass(enrollVsTarget)}">${formatPct(enrollVsTarget)}</td>
      <td class="col-enrollment">${formatNum(g.fullAopEnroll)}</td>
      <td class="col-enrollment">${enrollAopPct.toFixed(1)}%</td>

      <td class="col-collection">${(g.collection / 10000000).toFixed(2)}</td>
      <td class="col-collection">${(g.bookingTarget / 10000000).toFixed(2)}</td>
      <td class="col-collection ${vsClass(collectionVsTarget)}">${formatPct(collectionVsTarget)}</td>
      <td class="col-collection">${(g.fullAopBooking / 10000000).toFixed(2)}</td>
      <td class="col-collection">${collectionAopPct.toFixed(1)}%</td>

      <td class="col-aov">${Math.round(actualAOV)}</td>
      <td class="col-aov">${Math.round(targetAOV)}</td>
      <td class="col-aov ${vsClass(aovVsTarget)}">${formatPct(aovVsTarget)}</td>
      <td class="col-aov">${Math.round(aopAOV)}</td>
      <td class="col-aov">${aovAopPct.toFixed(1)}%</td>
    `;
    tbody.appendChild(tr);
  });

  // Total row
  const total = sumGroup(rows);
  const tBookingVsTarget = vsTargetPct(total.booking, total.bookingTarget);
  const tEnrollVsTarget = vsTargetPct(total.enroll, total.enrollTarget);
  const tCollVsTarget = vsTargetPct(total.collection, total.bookingTarget);
  const tActualAOV = total.enroll ? (total.booking / total.enroll) : 0;
  const tAopAOV = total.fullAopEnroll ? (total.fullAopBooking / total.fullAopEnroll) : 0;
  const tTargetAOV = total.enrollTarget ? (total.bookingTarget / total.enrollTarget) : 0;
  const tAovVsTarget = vsTargetPct(tActualAOV, tTargetAOV);

  const trTotal = document.createElement('tr');
  trTotal.className = 'row-total';
  trTotal.innerHTML = `
    <td>Total</td>
    <td class="col-booking">${(total.booking / 10000000).toFixed(2)}</td>
    <td class="col-booking">${(total.bookingTarget / 10000000).toFixed(2)}</td>
    <td class="col-booking ${vsClass(tBookingVsTarget)}">${formatPct(tBookingVsTarget)}</td>
    <td class="col-booking">${(total.fullAopBooking / 10000000).toFixed(2)}</td>
    <td class="col-booking">${(total.fullAopBooking ? total.booking / total.fullAopBooking * 100 : 0).toFixed(1)}%</td>

    <td class="col-enrollment">${formatNum(total.enroll)}</td>
    <td class="col-enrollment">${formatNum(total.enrollTarget)}</td>
    <td class="col-enrollment ${vsClass(tEnrollVsTarget)}">${formatPct(tEnrollVsTarget)}</td>
    <td class="col-enrollment">${formatNum(total.fullAopEnroll)}</td>
    <td class="col-enrollment">${(total.fullAopEnroll ? total.enroll / total.fullAopEnroll * 100 : 0).toFixed(1)}%</td>

    <td class="col-collection">${(total.collection / 10000000).toFixed(2)}</td>
    <td class="col-collection">${(total.bookingTarget / 10000000).toFixed(2)}</td>
    <td class="col-collection ${vsClass(tCollVsTarget)}">${formatPct(tCollVsTarget)}</td>
    <td class="col-collection">${(total.fullAopBooking / 10000000).toFixed(2)}</td>
    <td class="col-collection">${(total.fullAopBooking ? total.collection / total.fullAopBooking * 100 : 0).toFixed(1)}%</td>

    <td class="col-aov">${Math.round(tActualAOV)}</td>
    <td class="col-aov">${Math.round(tTargetAOV)}</td>
    <td class="col-aov ${vsClass(tAovVsTarget)}">${formatPct(tAovVsTarget)}</td>
    <td class="col-aov">${Math.round(tAopAOV)}</td>
    <td class="col-aov">${(tAopAOV ? tActualAOV / tAopAOV * 100 : 0).toFixed(1)}%</td>
  `;
  tbody.appendChild(trTotal);
}

function vsClass(val) {
  if (val === null) return '';
  return val >= 0 ? 'text-positive' : 'text-negative';
}

// =====================================================================
// Trend charts — 2 independent charts, each with an optional 2nd
// "Compare with" line (own KPI + own date range + own color), plus a
// quick-preset dropdown (MTD/LMTD/LYMTD/YTD/LYTD) per line.
// =====================================================================
function updateCharts() {
  const cohortFilter = document.getElementById('filter-cohort').value;
  const branchFilter = document.getElementById('filter-branch').value;

  [1, 2].forEach(idx => {
    const kpi = document.getElementById(`chart${idx}-kpi`).value;
    const start = parseInputDate(document.getElementById(`chart${idx}-start`).value);
    const end = parseInputDate(document.getElementById(`chart${idx}-end`).value);
    if (!start || !end || start > end) return;
    const color1 = document.getElementById(`chart${idx}-color1`).value;

    // Optional second line — "Compare with" — its own KPI + own date range + color.
    // "none" (the default) means single-line mode, same as before.
    const kpi2 = document.getElementById(`chart${idx}-kpi2`).value;
    let series2 = null;
    if (kpi2 !== 'none') {
      const start2 = parseInputDate(document.getElementById(`chart${idx}-start2`).value);
      const end2 = parseInputDate(document.getElementById(`chart${idx}-end2`).value);
      const color2 = document.getElementById(`chart${idx}-color2`).value;
      if (start2 && end2 && start2 <= end2) {
        series2 = { kpi: kpi2, start: start2, end: end2, color: color2 };
      }
    }

    renderTrendChart(idx, kpi, start, end, color1, series2, cohortFilter, branchFilter);
  });
}

// Wires a preset <select> (MTD/LMTD/LYMTD/YTD/LYTD/Custom) to a pair of
// date inputs — picking a preset fills both dates and re-renders; picking
// "Custom" leaves whatever is currently in the date inputs untouched.
function wirePresetDropdown(presetId, startId, endId) {
  document.getElementById(presetId).addEventListener('change', (e) => {
    const preset = e.target.value;
    if (preset === 'Custom') return;
    const today = dataAsOfDate || new Date();
    const range = computePresetRange(preset, today);
    if (!range) return;
    document.getElementById(startId).value = toInputDateStr(range.start);
    document.getElementById(endId).value = toInputDateStr(range.end);
    updateCharts();
  });
}


function computeKpiSeries(kpi, start, end, cohortFilter, branchFilter) {
  const passesFilter = (center) => {
    if (!center) return false;
    if (branchFilter !== 'all' && center !== branchFilter) return false;
    const cid = COHORT_MAPPING[center];
    if (cohortFilter !== 'all' && cid != cohortFilter) return false;
    return true;
  };

  // Bucket once by day for O(n) lookups instead of O(n*days)
  const enrollBuckets = new Map();
  enrollmentsRaw.forEach(r => {
    if (!passesFilter(r.center)) return;
    const b = enrollBuckets.get(r.dayKey) || { count: 0, sum: 0 };
    b.count += 1;
    b.sum += r.payable;
    enrollBuckets.set(r.dayKey, b);
  });

  const collBuckets = new Map();
  collectionsRaw.forEach(r => {
    if (!passesFilter(r.center)) return;
    const b = collBuckets.get(r.dayKey) || { sum: 0 };
    b.sum += r.netPaid;
    collBuckets.set(r.dayKey, b);
  });

  const labels = [];
  const values = [];
  const tempDate = new Date(start);
  while (tempDate <= end) {
    const dk = dayKey(tempDate);
    labels.push(tempDate.toLocaleDateString('en-IN', { day: '2-digit', month: 'short' }));

    if (kpi === 'Enrollment') {
      values.push(enrollBuckets.get(dk)?.count || 0);
    } else if (kpi === 'Booking') {
      values.push((enrollBuckets.get(dk)?.sum || 0) / 100000); // ₹ Lakh
    } else if (kpi === 'Collection') {
      values.push((collBuckets.get(dk)?.sum || 0) / 100000); // ₹ Lakh
    } else if (kpi === 'AOV') {
      const b = enrollBuckets.get(dk);
      values.push(b && b.count ? b.sum / b.count : 0);
    }
    tempDate.setDate(tempDate.getDate() + 1);
  }
  return { labels, values };
}

const KPI_COLORS = {
  'Enrollment': '#10b981',
  'Booking': '#3b82f6',
  'Collection': '#f59e0b',
  'AOV': '#8b5cf6'
};
const KPI_UNITS = {
  'Enrollment': 'Students',
  'Booking': '₹ Lakh',
  'Collection': '₹ Lakh',
  'AOV': '₹'
};

function renderTrendChart(chartIndex, kpi, start, end, color1, series2, cohortFilter, branchFilter) {
  const primary = computeKpiSeries(kpi, start, end, cohortFilter, branchFilter);

  const datasets = [{
    label: `Line 1: ${kpi} (${KPI_UNITS[kpi]})${series2 ? ' — ' + toInputDateStr(start) + ' to ' + toInputDateStr(end) : ''}`,
    data: primary.values,
    borderColor: color1,
    backgroundColor: color1 + '22',
    fill: !series2,
    borderWidth: 2,
    pointRadius: primary.labels.length > 45 ? 0 : 2,
    pointHoverRadius: 4,
    tension: 0.2,
    yAxisID: 'y'
  }];

  // x-axis labels: when comparing two lines, index-align by day-offset from
  // each line's own start date (not by calendar label) so e.g. "day 1 of
  // range A" lines up with "day 1 of range B" even if the two date ranges
  // don't share calendar dates (this is what makes "this month vs last
  // month" or "this KPI vs that KPI, different period" comparisons work).
  let labels = primary.labels;

  let usesDualAxis = false;
  if (series2) {
    const secondary = computeKpiSeries(series2.kpi, series2.start, series2.end, cohortFilter, branchFilter);
    const sameUnit = KPI_UNITS[kpi] === KPI_UNITS[series2.kpi];
    usesDualAxis = !sameUnit;

    // Align by day-offset, padding the shorter series so both plot against
    // a common "Day 1, Day 2, ..." x-axis instead of mismatched calendar dates.
    const maxLen = Math.max(primary.values.length, secondary.values.length);
    labels = Array.from({ length: maxLen }, (_, i) => `Day ${i + 1}`);
    datasets[0].data = padArray(primary.values, maxLen);

    datasets.push({
      label: `Line 2: ${series2.kpi} (${KPI_UNITS[series2.kpi]}) — ${toInputDateStr(series2.start)} to ${toInputDateStr(series2.end)}`,
      data: padArray(secondary.values, maxLen),
      borderColor: series2.color,
      backgroundColor: 'transparent',
      borderDash: [6, 4],
      fill: false,
      borderWidth: 2,
      pointRadius: maxLen > 45 ? 0 : 2,
      pointHoverRadius: 4,
      tension: 0.2,
      yAxisID: usesDualAxis ? 'y1' : 'y'
    });
    datasets[0].label = `Line 1: ${kpi} (${KPI_UNITS[kpi]}) — ${toInputDateStr(start)} to ${toInputDateStr(end)}`;
  }

  const ctx = document.getElementById(`chart-trend-${chartIndex}`).getContext('2d');
  if (chartInstances[chartIndex]) chartInstances[chartIndex].destroy();

  const scales = {
    x: { ticks: { color: '#9aa0b4', font: { size: 9 }, maxRotation: 0, autoSkip: true, maxTicksLimit: 12 }, grid: { display: false } },
    y: { ticks: { color: '#9aa0b4', font: { size: 9 } }, grid: { color: 'rgba(255,255,255,0.08)' }, position: 'left' }
  };
  if (usesDualAxis) {
    scales.y1 = { ticks: { color: '#9aa0b4', font: { size: 9 } }, grid: { display: false }, position: 'right' };
  }

  chartInstances[chartIndex] = new Chart(ctx, {
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: true, position: 'top', labels: { color: '#c7cad6', font: { size: 10 }, boxWidth: 20 } }
      },
      scales
    }
  });
}

function padArray(arr, len) {
  if (arr.length >= len) return arr;
  return [...arr, ...Array(len - arr.length).fill(null)];
}

// =====================================================================
// SECTION 4: Trends & Patterns — verified, data-derived findings.
// Every number here is computed live from enrollmentsRaw (the same merged
// FY26+FY27 dataset used everywhere else) — nothing here is hardcoded
// except the festival CALENDAR DATES themselves (verified via web search,
// see FESTIVAL_DATES below), since a festival's date can't be derived from
// the sheet data — only what happened on that date can.
// =====================================================================

// Verified festival/holiday dates. Source: web search (Oct 2026), cross-checked
// against multiple Indian festival calendars. Only dates that actually fall
// within our data's range (Jan 2024 onward) are useful — add future years'
// dates here as they're confirmed; there's no way to compute a lunar festival
// date from transaction data alone.
const FESTIVAL_DATES = [
  { name: 'Holi', date: new Date(2024, 2, 25) },
  { name: 'Holi', date: new Date(2025, 2, 14) },
  { name: 'Diwali', date: new Date(2024, 10, 1) },
  { name: 'Diwali', date: new Date(2025, 9, 20) },
  { name: 'Independence Day', date: new Date(2024, 7, 15) },
  { name: 'Independence Day', date: new Date(2025, 7, 15) },
  { name: 'Republic Day', date: new Date(2025, 0, 26) },
  { name: 'Dussehra', date: new Date(2024, 9, 12) }
];

function trendsPassesFilter(cohortFilter, branchFilter) {
  return (center) => {
    if (!center) return false;
    if (branchFilter !== 'all' && center !== branchFilter) return false;
    const cid = COHORT_MAPPING[center];
    if (cohortFilter !== 'all' && cid != cohortFilter) return false;
    return true;
  };
}

// Average Enrollments/day for each day of the week (Sun=0..Sat=6), across
// every calendar day of that weekday that exists within the filtered data's
// own date span (so a branch with only 3 months of history isn't compared
// against 2.5 years of "days" it never had).
function computeDayOfWeekPattern(cohortFilter, branchFilter) {
  const passesFilter = trendsPassesFilter(cohortFilter, branchFilter);
  const filtered = enrollmentsRaw.filter(r => passesFilter(r.center));
  if (filtered.length === 0) return null;

  const minDate = new Date(safeMin(filtered.map(r => r.parsedDate)));
  const maxDate = new Date(safeMax(filtered.map(r => r.parsedDate)));

  const dowCounts = Array(7).fill(0);
  const dowDays = Array(7).fill(0);
  filtered.forEach(r => dowCounts[r.parsedDate.getDay()]++);
  for (let d = new Date(minDate); d <= maxDate; d.setDate(d.getDate() + 1)) {
    dowDays[d.getDay()]++;
  }
  return dowCounts.map((c, i) => dowDays[i] ? c / dowDays[i] : 0);
}

// Average Enrollments/day for: early third of the month, middle third, last
// third, and specifically the final 3 calendar days (fee/admission-deadline
// window) vs the rest of the month.
function computeMonthPositionPattern(cohortFilter, branchFilter) {
  const passesFilter = trendsPassesFilter(cohortFilter, branchFilter);
  const filtered = enrollmentsRaw.filter(r => passesFilter(r.center));
  if (filtered.length === 0) return null;

  const minDate = new Date(safeMin(filtered.map(r => r.parsedDate)));
  const maxDate = new Date(safeMax(filtered.map(r => r.parsedDate)));
  const daysInMonth = (y, m) => new Date(y, m + 1, 0).getDate();

  const buckets = { early: 0, mid: 0, late: 0, last3: 0, rest: 0 };
  const bucketDays = { early: 0, mid: 0, late: 0, last3: 0, rest: 0 };

  const classify = (date) => {
    const dim = daysInMonth(date.getFullYear(), date.getMonth());
    const pct = date.getDate() / dim;
    return pct <= 0.33 ? 'early' : pct <= 0.66 ? 'mid' : 'late';
  };
  const isLast3 = (date) => date.getDate() > daysInMonth(date.getFullYear(), date.getMonth()) - 3;

  filtered.forEach(r => {
    buckets[classify(r.parsedDate)]++;
    buckets[isLast3(r.parsedDate) ? 'last3' : 'rest']++;
  });
  for (let d = new Date(minDate); d <= maxDate; d.setDate(d.getDate() + 1)) {
    bucketDays[classify(d)]++;
    bucketDays[isLast3(d) ? 'last3' : 'rest']++;
  }

  const avg = (k) => bucketDays[k] ? buckets[k] / bucketDays[k] : 0;
  return {
    early: avg('early'), mid: avg('mid'), late: avg('late'),
    last3: avg('last3'), rest: avg('rest')
  };
}

// For each known festival date: actual Enrollments that day vs the average
// of the SAME weekday in the surrounding +/-14 days (excluding a 3-day
// window around the festival itself, so the baseline isn't itself polluted
// by the festival's own dip). Only returns festivals where the filtered
// data actually has a baseline to compare against (e.g. skips a festival
// that predates a newly-opened branch).
function computeFestivalImpact(cohortFilter, branchFilter) {
  const passesFilter = trendsPassesFilter(cohortFilter, branchFilter);
  const filtered = enrollmentsRaw.filter(r => passesFilter(r.center));
  const byDayKey = new Map();
  filtered.forEach(r => byDayKey.set(r.dayKey, (byDayKey.get(r.dayKey) || 0) + 1));
  const countOnDate = (d) => byDayKey.get(dayKey(d)) || 0;

  const avgSameWeekdayAround = (d, excludeWindow) => {
    let total = 0, count = 0;
    for (let off = -14; off <= 14; off++) {
      if (Math.abs(off) <= excludeWindow) continue;
      const dd = new Date(d);
      dd.setDate(dd.getDate() + off);
      if (dd.getDay() === d.getDay() && dd >= new Date(safeMin(filtered.map(r => r.parsedDate))) && dd <= (dataAsOfDate || new Date())) {
        total += countOnDate(dd);
        count++;
      }
    }
    return count > 0 ? total / count : null;
  };

  if (filtered.length === 0) return [];

  return FESTIVAL_DATES.map(f => {
    const actual = countOnDate(f.date);
    const baseline = avgSameWeekdayAround(f.date, 3);
    return {
      name: f.name,
      date: f.date,
      actual,
      baseline,
      diffPct: baseline ? (actual - baseline) / baseline * 100 : null
    };
  }).filter(f => f.baseline !== null);
}

// Branch "vintage" = earliest Enrollment record seen for that branch in our
// data. This is a PROXY for age (limited by how far back our data goes,
// Jan 2024) — not a confirmed official branch-opening date. Growth compares
// this FY's YTD to the exact same calendar window one year earlier; branches
// with no prior-year data in that window show growth as "—" rather than a
// misleading number.
function computeBranchVintage(cohortFilter, branchFilter) {
  const passesFilter = trendsPassesFilter(cohortFilter, branchFilter);
  const filtered = enrollmentsRaw.filter(r => passesFilter(r.center));
  const firstSeen = {};
  filtered.forEach(r => {
    if (!firstSeen[r.center] || r.parsedDate < firstSeen[r.center]) firstSeen[r.center] = r.parsedDate;
  });

  const today = dataAsOfDate || new Date();
  const fyStart = getFYStart(today);
  const lastFYStart = new Date(fyStart.getFullYear() - 1, fyStart.getMonth(), fyStart.getDate());
  const lastFYEquivEnd = new Date(lastFYStart.getFullYear(), today.getMonth(), today.getDate());

  return Object.keys(firstSeen).sort((a, b) => firstSeen[a] - firstSeen[b]).map(center => {
    const ageMonths = Math.round((fyStart - firstSeen[center]) / (30 * 86400000));
    const fy26YtdEquiv = filtered.filter(r => r.center === center && r.parsedDate >= lastFYStart && r.parsedDate < lastFYEquivEnd).length;
    const fy27Ytd = filtered.filter(r => r.center === center && r.parsedDate >= fyStart && r.parsedDate <= today).length;
    const growth = fy26YtdEquiv > 0 ? (fy27Ytd / fy26YtdEquiv - 1) * 100 : null;
    return { center, firstSeen: firstSeen[center], ageMonths, fy26YtdEquiv, fy27Ytd, growth };
  });
}

let dowChartInstance = null, monthPosChartInstance = null;

function renderTrendsSection() {
  const cohortFilter = document.getElementById('filter-cohort').value;
  const branchFilter = document.getElementById('filter-branch').value;

  // --- Day of week chart ---
  const dow = computeDayOfWeekPattern(cohortFilter, branchFilter);
  const dowLabels = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  const dowCtx = document.getElementById('dow-chart').getContext('2d');
  if (dowChartInstance) dowChartInstance.destroy();
  if (dow) {
    dowChartInstance = new Chart(dowCtx, {
      type: 'bar',
      data: {
        labels: dowLabels,
        datasets: [{
          data: dow,
          backgroundColor: dowLabels.map((_, i) => i === 0 ? '#f45b69' : '#3b82f6'),
          borderRadius: 4
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { ticks: { color: '#9aa0b4', font: { size: 10 } }, grid: { display: false } },
          y: { ticks: { color: '#9aa0b4', font: { size: 10 } }, grid: { color: 'rgba(255,255,255,0.08)' } }
        }
      }
    });
  }

  // --- Month position chart ---
  const mp = computeMonthPositionPattern(cohortFilter, branchFilter);
  const mpCtx = document.getElementById('month-position-chart').getContext('2d');
  if (monthPosChartInstance) monthPosChartInstance.destroy();
  if (mp) {
    monthPosChartInstance = new Chart(mpCtx, {
      type: 'bar',
      data: {
        labels: ['Early (1st third)', 'Mid (2nd third)', 'Late (final third)', 'Last 3 days only'],
        datasets: [{
          data: [mp.early, mp.mid, mp.late, mp.last3],
          backgroundColor: ['#3b82f6', '#3b82f6', '#3b82f6', '#22c58e'],
          borderRadius: 4
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { ticks: { color: '#9aa0b4', font: { size: 9 } }, grid: { display: false } },
          y: { ticks: { color: '#9aa0b4', font: { size: 10 } }, grid: { color: 'rgba(255,255,255,0.08)' } }
        }
      }
    });
  }

  // --- Festival impact table ---
  const festivals = computeFestivalImpact(cohortFilter, branchFilter);
  const festBody = document.getElementById('festival-table-body');
  festBody.innerHTML = '';
  festivals.forEach(f => {
    const tr = document.createElement('tr');
    const diffClass = f.diffPct === null ? '' : (f.diffPct >= 0 ? 'text-positive' : 'text-negative');
    tr.innerHTML = `
      <td>${f.name}</td>
      <td>${f.date.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })}</td>
      <td>${f.actual}</td>
      <td>${f.baseline.toFixed(1)}</td>
      <td class="${diffClass}">${f.diffPct === null ? '—' : (f.diffPct >= 0 ? '+' : '') + f.diffPct.toFixed(0) + '%'}</td>
    `;
    festBody.appendChild(tr);
  });
  if (festivals.length === 0) {
    festBody.innerHTML = '<tr><td colspan="5" class="text-muted-small">No festival data available for this filter (branch may not have existed yet).</td></tr>';
  }

  // --- Branch vintage table ---
  const vintage = computeBranchVintage(cohortFilter, branchFilter);
  const vintBody = document.getElementById('vintage-table-body');
  vintBody.innerHTML = '';
  vintage.forEach(v => {
    const tr = document.createElement('tr');
    const growthClass = v.growth === null ? '' : (v.growth >= 0 ? 'text-positive' : 'text-negative');
    tr.innerHTML = `
      <td>${v.center.replace(' Government', '')}</td>
      <td>${v.firstSeen.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })}</td>
      <td>~${v.ageMonths} mo</td>
      <td>${formatNum(v.fy26YtdEquiv)}</td>
      <td>${formatNum(v.fy27Ytd)}</td>
      <td class="${growthClass}">${v.growth === null ? '—' : (v.growth >= 0 ? '+' : '') + v.growth.toFixed(0) + '%'}</td>
    `;
    vintBody.appendChild(tr);
  });
}

// =====================================================================
// Export
// =====================================================================
function exportCSV() {
  const start = document.getElementById('date-start').value;
  const end = document.getElementById('date-end').value;
  const cohortFilter = document.getElementById('filter-cohort').value;
  const branchFilter = document.getElementById('filter-branch').value;

  const grouped = buildGroupedData(parseInputDate(start), parseInputDate(end), cohortFilter, branchFilter);

  let csvContent = "data:text/csv;charset=utf-8,";
  csvContent += `GEW Performance Dashboard Export - Filters: Cohort=${cohortFilter} Branch=${branchFilter} DateRange=${start} to ${end}\n\n`;
  csvContent += `${grouped.groupByBranch ? 'Branch' : 'Cohort'},Booking (Cr),Booking Target (Cr),vs Target %,Enrollment,Enrollment Target,vs Target %,Collection (Cr),Collection Target (Cr),vs Target %,AOV,AOV Target,vs Target %\n`;

  grouped.list.forEach(g => {
    const actualAOV = g.enroll ? Math.round(g.booking / g.enroll) : 0;
    const targetAOV = g.enrollTarget ? Math.round(g.bookingTarget / g.enrollTarget) : 0;
    csvContent += [
      g.label,
      (g.booking / 10000000).toFixed(4), (g.bookingTarget / 10000000).toFixed(4), formatPct(vsTargetPct(g.booking, g.bookingTarget)),
      g.enroll, Math.round(g.enrollTarget), formatPct(vsTargetPct(g.enroll, g.enrollTarget)),
      (g.collection / 10000000).toFixed(4), (g.bookingTarget / 10000000).toFixed(4), formatPct(vsTargetPct(g.collection, g.bookingTarget)),
      actualAOV, targetAOV, formatPct(vsTargetPct(actualAOV, targetAOV))
    ].join(',') + '\n';
  });

  const encodedUri = encodeURI(csvContent);
  const link = document.createElement('a');
  link.setAttribute('href', encodedUri);
  link.setAttribute('download', `GEW_Dashboard_Export_${start}_to_${end}.csv`);
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}

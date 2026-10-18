// Gap Check page. All numbers come from /api/gaps (built in gaps.py); this file only formats and explains them in plain words.
'use strict';

const MON = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const state = { g: null, r: null, unit: 'cr', chart: null };
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const mlabel = (ym) => `${MON[+ym.slice(5, 7) - 1]}-${ym.slice(2, 4)}`;
const dlabel = (iso) => (iso ? new Date(iso.slice(0, 10) + 'T00:00:00').toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' }) : '—');
const n0 = (v) => Math.round(v).toLocaleString('en-IN');
const money = (v) => (state.unit === 'cr' ? (v / 1e7).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  : (v / 1e5).toLocaleString('en-IN', { minimumFractionDigits: 1, maximumFractionDigits: 1 }));
const signed = (v) => `${v > 0.5 ? '+' : v < -0.5 ? '−' : ''}${money(Math.abs(v))}`;
const cls = (v) => (Math.abs(v) < 0.5 ? '' : v > 0 ? 'pos' : 'neg');
const pct = (a, b) => (b ? `${a / b * 100 >= 0 ? '+' : ''}${(a / b * 100).toFixed(1)}%` : '—');
const unitName = () => (state.unit === 'cr' ? '₹ Cr' : '₹ Lakh');
const rupees = (v) => `₹ ${n0(Math.abs(v))}`;
const crTxt = (v) => `₹ ${(Math.abs(v) / 1e7).toFixed(2)} Cr`;

async function api(path) {
  const r = await fetch(path); const b = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof b.detail === 'string' ? b.detail : r.statusText);
  return b;
}

// ---- the numbers, split into "timing" (the Iteration data is older) and "real" differences
function parts(g) {
  const e = g.effects_total, R = g.reasons;
  const rs = (kind, test) => R.filter((r) => r.kind === kind && test(r.reason || ''));
  const sum = (arr, k) => arr.reduce((s, r) => s + (r[k] || 0), 0);
  const cnt = (arr) => arr.reduce((s, r) => s + r.n, 0);
  const after = rs('only_ours', (x) => x.startsWith('admitted after'));
  const missing = rs('only_ours', (x) => x.startsWith('admitted before'));
  const notInGew = rs('only_sheet', (x) => x.startsWith('not in the GEW'));
  const endedEarlier = rs('only_sheet', (x) => x.startsWith('in the GEW'));
  const both = (R.find((r) => r.kind === 'both') || { n: 0 }).n;
  const afterRev = sum(after, 'rev_ours');
  const gap = e.ours - e.sheet;
  const timing = afterRev;
  return { e, both, gap, timing, real: gap - timing, afterRev,
    nAfter: cnt(after), nMissing: cnt(missing), missingRev: sum(missing, 'rev_ours'),
    nNotIn: cnt(notInGew), notInRev: sum(notInGew, 'rev_sheet'), nEnded: cnt(endedEarlier), endedRev: sum(endedEarlier, 'rev_sheet'),
    nOnlySheet: cnt(R.filter((r) => r.kind === 'only_sheet')), nOnlyOurs: cnt(R.filter((r) => r.kind === 'only_ours')) };
}

function readFirst(g, p) {
  $('readfirst').innerHTML = `<ul>
    <li>This page compares two sets of numbers for the same year: the <b>GEW data</b> (our enrollment and collection sheets) and the <b>Iteration data</b> (the Iteration sheet, made by the P&amp;L team, prepared on <b>${dlabel(g.sheet_prepared_on)}</b>). Both use the <b>same revenue rule</b> (the amount is shared equally over the months from the start date to the end date). The GEW data counts the <b>booking</b> (the fee the student has to pay, without GST). The Iteration data counts its <b>Total Adjusted Collection</b>, as its sheet gives it. A difference means different <i>students</i>, <i>dates</i> or <i>amounts</i>.</li>
    <li>Finance's own monthly P&amp;L workbook is a third set of numbers. It is shown separately at the bottom of this page ("GEW, Iteration and Finance P&amp;L"). Finance's monthly sheet is not the same thing as the Iteration sheet.</li>
    <li>This year's revenue is <b>${crTxt(p.e.ours)}</b> in the GEW data and <b>${crTxt(p.e.sheet)}</b> in the Iteration data: a gap of <b>${p.gap >= 0 ? '+' : '−'}${crTxt(p.gap)}</b>. A plus means the GEW data shows more.</li>
    <li><b>Most of it is only timing (${p.timing >= 0 ? '+' : '−'}${crTxt(p.timing)}).</b> The Iteration data stops on ${dlabel(g.sheet_prepared_on)}. Students admitted after that day are in the GEW data but cannot be in the Iteration data. This goes away when the Iteration team refreshes their sheet. Nothing to fix.</li>
    <li><b>The real differences add up to ${p.real >= 0 ? '+' : '−'}${crTxt(p.real)}.</b> These are students found on one side only, and students whose start or end date is different. They are listed below, and every list can be downloaded for Excel to discuss with the Iteration team.</li></ul>`;
  const card = (c, l, v, d) => `<div class="kpi ${c}"><div class="lbl">${l}</div><div class="val">${v}</div><div class="det">${d}</div></div>`;
  $('kpis').innerHTML =
    card('total', 'Total gap (GEW minus Iteration)', `${p.gap >= 0 ? '+' : '−'}${crTxt(p.gap)}`, `GEW ${crTxt(p.e.ours)} · Iteration ${crTxt(p.e.sheet)}`) +
    card('recognised', 'Only because Iteration data is older', `${p.timing >= 0 ? '+' : '−'}${crTxt(p.timing)}`, 'Timing. Goes away when their sheet is refreshed') +
    card('expected', 'Real differences', `${p.real >= 0 ? '+' : '−'}${crTxt(p.real)}`, 'To discuss with the Iteration team') +
    card('committed', 'Students found on both sides', n0(p.both), `${n0(p.nOnlySheet)} only in Iteration data · ${n0(p.nOnlyOurs)} only in GEW data`);
}

function bridge(g, p) {
  const e = p.e, X = g.examples || {}, d = g.dates, when = dlabel(g.sheet_prepared_on);
  const ex = (txt) => (txt ? `<span class="ex">Example: ${txt}</span>` : '');
  const row = (label, n, v, what, example, sub) => `<tr><td class="what ${sub ? 'sub' : ''}">${label}<span class="ex" style="color:var(--text2);font-size:12px">${what}</span>${ex(example)}</td>
    <td>${n}</td><td class="${cls(v)}">${signed(v)}</td></tr>`;
  const grp = (title, tag, tagText) => `<tr class="grp"><td colspan="3">${title}<span class="tag ${tag}">${tagText}</span></td></tr>`;
  const sumRow = (label, v) => `<tr class="sum"><td class="what">${label}</td><td></td><td class="${cls(v)}">${signed(v)}</td></tr>`;
  $('bridge').innerHTML = `<thead><tr><th>Reason</th><th>Students</th><th>${unitName()}</th></tr></thead><tbody>
    <tr class="sum"><td class="what">Revenue in the Iteration data<span class="ex" style="color:var(--text2);font-size:12px">Every student whose batch runs into this year, with the Iteration sheet's own dates and amounts</span></td><td>${n0(p.both + p.nOnlySheet)}</td><td>${money(e.sheet)}</td></tr>

    ${grp('A. Only because the Iteration data is older', 'timing', 'timing, nothing to fix')}
    ${row(`Students admitted after ${when}`, n0(p.nAfter), p.afterRev, `The Iteration data was prepared on ${when}, so students admitted later are not in it. They are in the GEW data.`,
      X.admitted_after && `student ${X.admitted_after.reg_no} (${esc(X.admitted_after.branch.replace(' Government', ''))}) was admitted on ${dlabel(X.admitted_after.start_gew)}; booking ${rupees(X.admitted_after.amount)}, worth ${rupees(X.admitted_after.revenue)} of revenue this year, none of it in the Iteration data.`, true)}
    ${sumRow('Total of A (timing)', p.timing)}

    ${grp('B. Real differences', 'real', 'to discuss with the Iteration team')}
    ${row('Students only in the Iteration data: not in the GEW enrollment data', n0(p.nNotIn), -p.notInRev, 'The student number is in the Iteration sheet but cannot be found in the GEW enrollment sheet at all.',
      X.not_in_gew && `student ${X.not_in_gew.reg_no} (${esc(X.not_in_gew.branch.replace(' Government', ''))}): the Iteration data counts ${rupees(X.not_in_gew.amount)} from ${dlabel(X.not_in_gew.start_pl)} to ${dlabel(X.not_in_gew.end_pl)}.`, true)}
    ${row('Students only in the Iteration data: the GEW batch end date is earlier', n0(p.nEnded), -p.endedRev, 'The student is in the GEW data too, but the GEW batch end date is before this year, so the GEW data gives no revenue this year. The Iteration data has a later end date.',
      X.gew_end_earlier && `student ${X.gew_end_earlier.reg_no} (${esc(X.gew_end_earlier.branch.replace(' Government', ''))}): the Iteration data counts ${dlabel(X.gew_end_earlier.start_pl)} to ${dlabel(X.gew_end_earlier.end_pl)} (${rupees(X.gew_end_earlier.amount)}).`, true)}
    ${row(`Students only in the GEW data: admitted before ${when}, but missing from the Iteration data`, n0(p.nMissing), p.missingRev, 'These students were already admitted when the Iteration data was prepared, yet are not in it.',
      X.missing_in_pl && `student ${X.missing_in_pl.reg_no} (${esc(X.missing_in_pl.branch.replace(' Government', ''))}) was admitted on ${dlabel(X.missing_in_pl.start_gew)}, batch ends ${dlabel(X.missing_in_pl.end_gew)}, booking ${rupees(X.missing_in_pl.amount)}.`, true)}
    ${row('Different start or end dates (students found on both sides)', '—', e.date, `Start date differs in <b>month</b> for ${n0(d.start_other_month)} students and end date for ${n0(d.end_other_month)}. A different month moves revenue between months, and sometimes out of this year.`,
      X.dates_differ && `student ${X.dates_differ.reg_no} (${esc(X.dates_differ.branch.replace(' Government', ''))}): GEW counts ${dlabel(X.dates_differ.start_gew)} to ${dlabel(X.dates_differ.end_gew)}, the Iteration data counts ${dlabel(X.dates_differ.start_pl)} to ${dlabel(X.dates_differ.end_pl)}; this changes this year's revenue by ${X.dates_differ.revenue >= 0 ? '+' : '−'}${rupees(X.dates_differ.revenue)}.`, true)}
    ${row('Different amount for the same student', n0(p.both), e.amount_def, `The GEW booking and the Iteration "Total Adjusted Collection" are the same, within ₹5, for ${(g.amount_match_rate * 100).toFixed(1)}% of the students found on both sides. For the rest the two differ: for example, a student who has not paid the full fee yet has the full booking in the GEW data but only what was paid up to ${when} in the Iteration data.`, '', true)}
    ${sumRow('Total of B (real differences)', p.real)}

    <tr class="sum"><td class="what">Revenue in the GEW data<span class="ex" style="color:var(--text2);font-size:12px">Built only from the two main Google Sheets</span></td><td>${n0(p.both + p.nOnlyOurs)}</td><td>${money(e.ours)}</td></tr>
    <tr class="sum"><td class="what">Gap = GEW minus Iteration = A + B</td><td></td><td class="${cls(p.gap)}">${signed(p.gap)}</td></tr></tbody>`;
}

function months(g) {
  const E = g.effects_month, M = g.months;
  const v = (e, m) => (E[e] && E[e][m]) || 0;
  const rows = M.map((m) => {
    const gap = v('ours', m) - v('sheet', m);
    return `<tr><td>${mlabel(m)}</td><td>${money(v('sheet', m))}</td><td>${money(v('ours', m))}</td><td class="${cls(gap)}">${signed(gap)}</td>
      <td>${signed(v('only_sheet', m))}</td><td>${signed(v('only_ours', m))}</td><td>${signed(v('date', m))}</td><td>${signed(v('amount_def', m))}</td></tr>`;
  }).join('');
  const T = (e) => M.reduce((s, m) => s + v(e, m), 0);
  $('months').innerHTML = `<thead><tr><th>${unitName()}</th><th>Iteration data</th><th>GEW data</th><th>Gap</th><th>Only in Iteration data</th><th>Only in GEW data</th><th>Dates</th><th>Amount</th></tr></thead>
    <tbody>${rows}<tr class="total"><td>Year</td><td>${money(T('sheet'))}</td><td>${money(T('ours'))}</td><td class="${cls(T('ours') - T('sheet'))}">${signed(T('ours') - T('sheet'))}</td>
    <td>${signed(T('only_sheet'))}</td><td>${signed(T('only_ours'))}</td><td>${signed(T('date'))}</td><td>${signed(T('amount_def'))}</td></tr></tbody>`;
  $('month-note').textContent = 'The gap grows in later months because the Iteration data has no students admitted after its date. "Only in GEW data" is mostly those students';
  const ds = (label, key, color) => ({ label, data: M.map((m) => +(v(key, m) / 1e7).toFixed(4)), backgroundColor: color, borderWidth: 0 });
  if (state.chart) state.chart.destroy();
  state.chart = new Chart($('c-gap'), {
    type: 'bar',
    data: { labels: M.map(mlabel), datasets: [ds('Only in GEW data', 'only_ours', '#22c58e'),
      ds('Different dates', 'date', '#a78bfa'), ds('Different amount', 'amount_def', '#f5a524'), ds('Only in Iteration data', 'only_sheet', '#f45b69')] },
    options: { responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
      scales: { x: { stacked: true, ticks: { color: '#cbd0e0' }, grid: { display: false } }, y: { stacked: true, ticks: { color: '#cbd0e0', callback: (x) => `${x} Cr` }, grid: { color: 'rgba(255,255,255,.08)' } } },
      plugins: { legend: { labels: { color: '#cbd0e0' } }, tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ₹ ${c.parsed.y.toFixed(2)} Cr` } } } },
  });
}

function students(g) {
  $('students-note').innerHTML = `Every student is looked up by student number in both data sets. <b>On both sides</b> = found in both. <b>Only in GEW data</b> / <b>Only in Iteration data</b> = found on one side only; the reason says why. Use the download links above to get the full list with each student's dates and amounts.`;
  const kindName = { both: 'On both sides', only_ours: 'Only in GEW data', only_sheet: 'Only in Iteration data' };
  $('students').innerHTML = `<thead><tr><th>Where the student is</th><th>Reason</th><th>Students</th><th>Revenue this year, GEW data (${unitName()})</th><th>Revenue this year, Iteration data (${unitName()})</th></tr></thead><tbody>${
    g.reasons.map((r) => `<tr><td>${kindName[r.kind]}</td><td style="text-align:left;white-space:normal">${esc(r.reason || 'Found in both')}</td><td>${n0(r.n)}</td><td>${r.kind === 'only_sheet' ? '—' : money(r.rev_ours)}</td><td>${r.kind === 'only_ours' ? '—' : money(r.rev_sheet)}</td></tr>`).join('')}</tbody>`;
}

function dates(g) {
  const d = g.dates, p = (x) => `${(x / d.both * 100).toFixed(1)}%`, X = (g.examples || {}).dates_differ;
  $('dates-intro').innerHTML = `Both data sets give every student a <b>start date</b> and an <b>end date</b>, and the revenue is spread over the months in between. The GEW data starts from the <b>admission date</b> (<code>new_receipt_date</code>) and ends at <code>batch_end_date</code>. The Iteration data uses its own "Effective Start" (usually the later batch start date) and "Effective End".`
    + (X ? ` For example, student ${X.reg_no}: GEW ${dlabel(X.start_gew)} to ${dlabel(X.end_gew)}, Iteration ${dlabel(X.start_pl)} to ${dlabel(X.end_pl)}.` : '');
  const row = (what, a, b, c) => `<tr><td>${what}</td><td>${n0(a)} <span class="muted">(${p(a)})</span></td><td>${n0(b)} <span class="muted">(${p(b)})</span></td><td>${n0(c)} <span class="muted">(${p(c)})</span></td></tr>`;
  $('dates').innerHTML = `<thead><tr><th>${n0(d.both)} students found on both sides</th><th>Same day</th><th>Same month, different day</th><th>Different month</th></tr></thead><tbody>
    ${row('Start date', d.start_same_day, d.start_same_month, d.start_other_month)}${row('End date', d.end_same_day, d.end_same_month, d.end_other_month)}</tbody>`;
  $('dates-note').textContent = `Where the start dates differ, the GEW date is earlier for ${n0(d.start_ours_earlier)} students and later for ${n0(d.start_ours_later)}. Only a difference in month matters for the monthly revenue.`;
}

function glance(g, p) {
  const e = p.e, d = g.dates, when = dlabel(g.sheet_prepared_on), rows = [];
  const add = (name, tag, tagText, n, amt, who, see) => rows.push({ name, tag, tagText, n, amt, who, see });
  const T = ['timing', 'Timing'], R = ['real', 'Iteration vs GEW difference'];
  add(`Students admitted after ${when}`, ...T, n0(p.nAfter), p.afterRev, 'Nobody: goes away when the Iteration data is refreshed', '<a href="#bridge-card">details</a>');
  add('Students only in the Iteration data: not in the GEW enrollment data', ...R, n0(p.nNotIn), -p.notInRev, 'Data team / Iteration team', '<a class="ghost-link" href="/api/gaps/students.csv?kind=only_sheet">⬇ list</a>');
  add('Students only in the Iteration data: the GEW batch end date is earlier', ...R, n0(p.nEnded), -p.endedRev, 'Iteration team / Data team', '<a class="ghost-link" href="/api/gaps/students.csv?kind=only_sheet">⬇ list</a>');
  add(`Students only in the GEW data, admitted before ${when}`, ...R, n0(p.nMissing), p.missingRev, 'Iteration team', '<a class="ghost-link" href="/api/gaps/students.csv?kind=only_ours">⬇ list</a>');
  add('Different start or end dates', ...R, `${n0(d.start_other_month)} start · ${n0(d.end_other_month)} end`, e.date, 'Iteration team and Finance: which date is right?', '<a class="ghost-link" href="/api/gaps/students.csv?kind=both&only_differences=true">⬇ list</a>');
  add('Different booking amount for the same student', ...R, n0(p.both), e.amount_def, 'Decision needed: booking or Total Adjusted Collection?', '<a class="ghost-link" href="/api/gaps/students.csv?kind=both&only_differences=true">⬇ list</a>');
  const dl = { orphan_payments: ['orphan_payments'], duplicate_payments: ['duplicate_payments'], students_end_before_start: ['bad_dates'], vanished_rows: ['removed_payments', 'removed_students'] };
  for (const c of g.other_checks || []) {
    const missing = c.severity === 'missing';
    add(c.title, missing ? 'need' : 'gew', missing ? 'Information needed' : 'Found in GEW data', missing ? '—' : n0(c.n_rows), missing ? null : c.amount, c.who || '—',
      (dl[c.check_id] || []).map((x, i) => `<a class="ghost-link" href="/api/dq/${x}.csv">⬇ ${dl[c.check_id].length > 1 ? (i ? 'students' : 'payments') : 'list'}</a>`).join(' ') || '—');
  }
  $('glance').innerHTML = `<thead><tr><th>#</th><th>Gap</th><th>Kind</th><th>Rows</th><th>${unitName()}</th><th>Who can close it</th><th>See</th></tr></thead><tbody>${
    rows.map((r, i) => `<tr><td>${i + 1}</td><td class="what">${esc(r.name)}</td><td style="text-align:left"><span class="tag ${r.tag}" style="margin-left:0">${r.tagText}</span></td>
      <td>${r.n}</td><td class="${r.amt == null ? '' : cls(r.amt)}">${r.amt == null ? '—' : signed(r.amt)}</td><td style="text-align:left;white-space:normal">${esc(r.who)}</td><td>${r.see}</td></tr>`).join('')}</tbody>`;
}

function other(g) {
  const csv = { orphan_payments: ['orphan_payments'], duplicate_payments: ['duplicate_payments'], students_end_before_start: ['bad_dates'], vanished_rows: ['removed_payments', 'removed_students'] };
  const tagOf = { note: ['real', 'worth a look'], ask: ['ask', 'to confirm'], info: ['timing', 'for the record'], ok: ['ok', 'checked: fine'], missing: ['need', 'information needed'] };
  const links = (c) => (csv[c.check_id] ? csv[c.check_id].map((x, i) => `<a class="ghost-link" href="/api/dq/${x}.csv">⬇ ${csv[c.check_id].length > 1 ? (i ? 'students' : 'payments') : 'list'}</a>`).join(' ')
    : c.flag_list ? `<a class="ghost-link" href="/api/flag/${c.check_id}.csv">⬇ list</a>` : '—');
  const line = (c) => { const t = tagOf[c.severity] || tagOf.info; return `<tr><td class="what"><b>${esc(c.title)}</b><span class="tag ${t[0]}">${t[1]}</span>
      <span class="ex" style="color:var(--text2);font-size:12px">${esc(c.detail || '')}</span>${c.who ? `<span class="ex">Who can close it: ${esc(c.who)}</span>` : ''}</td>
      <td>${c.severity === 'missing' ? '—' : n0(c.n_rows)}</td><td>${c.amount == null ? '—' : money(c.amount)}</td><td>${links(c)}</td></tr>`; };
  const found = (g.other_checks || []).filter((c) => c.severity !== 'missing'), need = (g.other_checks || []).filter((c) => c.severity === 'missing');
  const grp = (t) => `<tr class="grp"><td colspan="4">${t}</td></tr>`;
  $('other').classList.add('gapt');
  $('other').innerHTML = `<thead><tr><th>What</th><th>Rows</th><th>${unitName()}</th><th>Download</th></tr></thead><tbody>${grp('Found in the GEW data')}${found.map(line).join('')}${grp('Information we still need from someone')}${need.map(line).join('')}</tbody>`;
}

function branches(g) {
  const rows = [...g.branches].sort((a, b) => Math.abs(b.ours - b.sheet) - Math.abs(a.ours - a.sheet)).map((b) => {
    const gap = b.ours - b.sheet;
    return `<tr><td>${esc(b.name.replace(' Government', ''))}</td><td>${n0(b.both)}</td><td>${n0(b.only_sheet)}</td><td>${n0(b.only_ours)}</td><td>${money(b.sheet)}</td><td>${money(b.ours)}</td>
      <td class="${cls(gap)}">${signed(gap)}</td><td>${signed(b.only_sheet_rev)}</td><td>${signed(b.only_ours_rev)}</td><td>${signed(b.date)}</td><td>${signed(b.amount_def)}</td></tr>`;
  }).join('');
  $('branches').innerHTML = `<thead><tr><th>Branch (largest gap first)</th><th>Students on both</th><th>Only in Iteration</th><th>Only in GEW</th><th>Iteration revenue (${unitName()})</th><th>GEW revenue</th><th>Gap</th>
    <th>Only in Iteration data</th><th>Only in GEW data</th><th>Dates</th><th>Amount</th></tr></thead><tbody>${rows}</tbody>`;
}

function pl(g) {
  const E = g.effects_month, T = g.effects_total, f2 = (a) => (a == null ? '—' : (+a).toFixed(2));
  const sg = (a) => (a == null ? '—' : `${a >= 0 ? '+' : '−'}${Math.abs(a).toFixed(2)}`);
  const d2 = (a, b) => (a == null || b == null ? '<td>—</td>' : `<td class="${cls(a - b)}">${sg(a - b)}</td>`);
  const mv = (k, m) => ((E[k] && E[k][m]) || 0) / 1e7;
  const rowsM = g.pl_rows.map((r) => ({ m: r.month, gew: r.ours_revenue_cr, k: mv('sheet', r.month), fp: mv('sheet_fp', r.month), fin: r.pl_revenue_cr }));
  const sum = (key) => rowsM.reduce((s, x) => s + (x[key] || 0), 0);
  const tr = (label, x, cls_) => `<tr${cls_ ? ` class="${cls_}"` : ''}><td>${label}</td><td>${f2(x.gew)}</td><td>${f2(x.k)}</td><td>${f2(x.fp)}</td><td>${f2(x.fin)}</td>${d2(x.gew, x.fin)}${d2(x.k, x.fin)}${d2(x.fp, x.fin)}</tr>`;
  $('three').innerHTML = `<thead><tr><th>Month</th><th>GEW data<br><small>booking</small></th><th>Iteration<br><small>Total Adjusted Collection</small></th><th>Iteration<br><small>Final Total Payable</small></th><th>Finance P&amp;L</th><th>GEW − Finance</th><th>Iteration (Adj. Collection) − Finance</th><th>Iteration (Final Payable) − Finance</th></tr></thead><tbody>${
    rowsM.map((x) => tr(mlabel(x.m), x)).join('')}${tr('Total', { gew: sum('gew'), k: sum('k'), fp: sum('fp'), fin: sum('fin') }, 'total')}</tbody>`;
  // which Iteration amount: the whole year, and the months finance has
  const cr = (v) => f2(v / 1e7);
  const part = (suffix) => ({ year: T['sheet' + suffix] || 0, only: -(T['only_sheet' + suffix] || 0), amt: T['amount_def' + suffix] || 0,
    am: rowsM.reduce((s, x) => s + (suffix ? x.fp : x.k), 0) });
  const gewYear = T.ours || 0, gewAm = sum('gew'), finAm = sum('fin');
  const which = (label, p) => `<tr><td class="what">${label}</td><td>${cr(p.year)}</td><td class="${cls(gewYear - p.year)}">${sg((gewYear - p.year) / 1e7)}</td><td>${f2(p.am)}</td><td class="${cls(gewAm - p.am)}">${sg(gewAm - p.am)}</td><td class="${cls(p.am - finAm)}">${sg(p.am - finAm)}</td><td>${cr(p.only)}</td><td class="${cls(p.amt)}">${sg(p.amt / 1e7)}</td></tr>`;
  $('which').innerHTML = `<thead><tr><th>Iteration amount used</th><th>Revenue this year (₹ Cr)</th><th>GEW − Iteration, this year</th><th>Revenue Apr–Aug</th><th>GEW − Iteration, Apr–Aug</th><th>Iteration − Finance, Apr–Aug</th><th>Students only in Iteration (₹ Cr)</th><th>Different amount, same student (₹ Cr)</th></tr></thead><tbody>
    <tr class="sum"><td class="what">GEW data (booking)</td><td>${cr(gewYear)}</td><td></td><td>${f2(gewAm)}</td><td></td><td class="${cls(gewAm - finAm)}">${sg(gewAm - finAm)} <small>vs Finance</small></td><td></td><td></td></tr>
    ${which('Iteration: Total Adjusted Collection (column K)', part(''))}${which('Iteration: Final Total Payable (w/o GST)', part('_fp'))}
    <tr class="sum"><td class="what">Finance P&amp;L (Revenue line)</td><td></td><td></td><td>${f2(finAm)}</td><td></td><td></td><td></td><td></td></tr></tbody>`;
  const rows = g.pl_rows.map((r) => {
    const f = (a) => (a == null ? '—' : `${(+a).toFixed(2)}`);
    const dv = (o, p) => (p ? `<td class="${cls(o - p)}">${pct(o - p, p)}</td>` : '<td>—</td>');
    return `<tr><td>${mlabel(r.month)}</td><td>${f(r.pl_revenue_cr)}</td><td>${r.ours_revenue_cr.toFixed(2)}</td>${dv(r.ours_revenue_cr, r.pl_revenue_cr)}
      <td>${r.pl_enrollment == null ? '—' : n0(r.pl_enrollment)}</td><td>${n0(r.ours_enrollment)}</td>${dv(r.ours_enrollment, r.pl_enrollment)}
      <td>${f(r.pl_collection_cr)}</td><td>${r.ours_collection_cr.toFixed(2)}</td>${dv(r.ours_collection_cr, r.pl_collection_cr)}</tr>`;
  }).join('');
  $('pl').innerHTML = `<thead><tr><th>Month</th><th>Revenue: Finance P&amp;L (₹ Cr)</th><th>GEW</th><th>Difference</th><th>Students admitted: Finance P&amp;L</th><th>GEW</th><th>Difference</th>
    <th>Collection without GST: Finance P&amp;L (₹ Cr)</th><th>GEW</th><th>Difference</th></tr></thead><tbody>${rows || '<tr><td colspan="10">No months available.</td></tr>'}</tbody>`;
}

// ---- the gap register: every difference, in plain words, with a real student as proof
const cr2 = (v) => (v == null ? '—' : (v / 1e7).toFixed(2));
const scr = (v) => (v == null ? '—' : `${v >= 50000 ? '+' : v <= -50000 ? '−' : ''}${(Math.abs(v) / 1e7).toFixed(2)}`);
const ecls = (v) => (v == null || Math.abs(v) < 50000 ? '' : v > 0 ? 'pos' : 'neg');

function register(r) {
  const A = r.rows.filter((x) => x.grp === 'GEW vs Iteration'), T = r.rows.find((x) => x.gap_id === 'TOTAL'), B = r.rows.filter((x) => x.grp === 'Finance P&L');
  const win = (A[0] && A[0].window) || '';
  $('reg-asof').textContent = `GEW data till ${dlabel(r.snapshot_date)} · Iteration prepared on ${dlabel(r.iter_asof)} · Finance P&L till ${r.finance_last_month ? mlabel(r.finance_last_month) : '—'} · updated ${dlabel(r.built_at)}`;
  $('reg-intro').innerHTML = `Every difference we know, one by one. <b>Effect</b> = how much it changes this year's revenue: a plus means the GEW data shows more than the Iteration data. The Iteration sheet has two amounts, so each gap is shown on <b>Total Adjusted Collection</b> (what it counts as collected) and on <b>Final Total Payable</b> (the fee). `
    + `"Months" = ${esc(win)}, the months the Iteration sheet covers. The lines add up exactly to the total at the bottom. Amounts in ${unitName()}.`;
  const sv = (v) => (v == null ? '<td>—</td>' : `<td class="${cls(v)}">${signed(v)}</td>`);
  const proof = (x) => `<td class="proof">${esc(x.proof_text || '')}${x.evidence && x.n ? `<br><a class="ghost-link" href="/api/gap_evidence/${x.evidence}.csv">⬇ all ${n0(x.n)} students</a>` : ''}</td>`;
  const what = (x) => `<td class="what"><b>${esc(x.gap_id)} · ${esc(x.title)}</b><span class="plain">${esc(x.plain)}</span><span class="owner">Who can close it: ${esc(x.owner || '—')}</span></td>`;
  $('reg-a').innerHTML = `<thead><tr><th rowspan="2">What is different</th><th rowspan="2">Students</th><th class="grp" colspan="2">Iteration on Total Adjusted Collection</th><th class="grp" colspan="2">Iteration on Final Total Payable</th><th rowspan="2">Proof: a real student</th></tr>
    <tr><th>This year</th><th>${esc(win)}</th><th>This year</th><th>${esc(win)}</th></tr></thead><tbody>${
    A.map((x) => `<tr>${what(x)}<td>${n0(x.n)}</td>${sv(x.eff_fy_k)}${sv(x.eff_win_k)}${sv(x.eff_fy_fp)}${sv(x.eff_win_fp)}${proof(x)}</tr>`).join('')}
    ${T ? `<tr class="totalrow"><td class="what">${esc(T.title)}<span class="plain">${esc(T.plain)}</span></td><td></td>${sv(T.eff_fy_k)}${sv(T.eff_win_k)}${sv(T.eff_fy_fp)}${sv(T.eff_win_fp)}<td class="proof">The lines above add up to this exactly (checked on every build).</td></tr>` : ''}</tbody>`;
  const num = (v, unit) => (v == null ? '—' : unit === 'students' ? n0(v) : (+v).toFixed(2));
  const sum = (arr) => arr.reduce((s, x) => s + (x || 0), 0);
  $('reg-b').innerHTML = `<thead><tr><th>What is different</th><th>GEW data</th><th>Iteration<br><small>Total Adj. Collection</small></th><th>Iteration<br><small>Final Total Payable</small></th><th>Finance P&amp;L</th><th>GEW − Finance</th><th>Proof</th></tr></thead><tbody>${
    B.map((x) => { const v = x.values, u = v.unit === 'students' ? 'students' : 'Cr'; const g = sum(v.gew), f = sum(v.fin);
      const hasIt = v.it_k.some((y) => y != null);
      const months = `<details class="months"><summary>month by month</summary><table><thead><tr><th>Month</th><th>GEW</th>${hasIt ? '<th>Iter. (Adj. Coll.)</th><th>Iter. (Final Pay.)</th>' : ''}<th>Finance</th></tr></thead><tbody>${
        v.months.map((m, i) => `<tr><td>${mlabel(m)}</td><td>${num(v.gew[i], u)}</td>${hasIt ? `<td>${num(v.it_k[i], u)}</td><td>${num(v.it_fp[i], u)}</td>` : ''}<td>${num(v.fin[i], u)}</td></tr>`).join('')}</tbody></table></details>`;
      return `<tr><td class="what"><b>${esc(x.gap_id)} · ${esc(x.title)}</b><span class="plain">${esc(x.plain)}</span><span class="owner">Months: ${esc(x.window)} · Who can close it: ${esc(x.owner || '—')}</span>${months}</td>
        <td>${num(g, u)}${u === 'Cr' ? ' Cr' : ''}</td><td>${hasIt ? num(sum(v.it_k), u) + ' Cr' : '—'}</td><td>${hasIt ? num(sum(v.it_fp), u) + ' Cr' : '—'}</td><td>${num(f, u)}${u === 'Cr' ? ' Cr' : ''}</td>
        <td class="${cls((g - f) * (u === 'Cr' ? 1e7 : 1))}">${(g - f) >= 0 ? '+' : '−'}${num(Math.abs(g - f), u)}${u === 'Cr' ? ' Cr' : ''}</td>${proof({ proof_text: x.proof_text })}</tr>`; }).join('')}</tbody>`;
  mailText(r);
}

function mailText(r) {
  const A = r.rows.filter((x) => x.grp === 'GEW vs Iteration'), T = r.rows.find((x) => x.gap_id === 'TOTAL'), B = r.rows.filter((x) => x.grp === 'Finance P&L');
  const win = (A[0] && A[0].window) || '';
  const F1 = B.find((x) => x.gap_id === 'G-F1'), sum = (arr) => arr.reduce((s, x) => s + (x || 0), 0);
  const cr = (v) => `Rs ${(Math.abs(v) / 1e7).toFixed(2)} Cr`, sg = (v) => (v == null ? 'n/a' : `${v >= 0 ? '+' : '-'}${cr(v)}`);
  const L = [];
  L.push('Subject: Govt Exam Offline FY27: gaps between GEW data, Iteration data and Finance P&L');
  L.push('', 'Hi,', '');
  L.push(`Sharing the gaps we have found between the three data sets (GEW data till ${dlabel(r.snapshot_date)}, Iteration sheet prepared on ${dlabel(r.iter_asof)}, Finance P&L till ${r.finance_last_month ? mlabel(r.finance_last_month) : '-'}).`);
  L.push('GEW data = our enrollment and collection sheets. Revenue is counted on the booking (fee without GST) spread equally over the months from admission to batch end.');
  L.push('');
  if (F1) {
    const v = F1.values, g = sum(v.gew), f = sum(v.fin);
    const f2 = B.find((x) => x.gap_id === 'G-F2');
    L.push(`REVENUE, ${F1.window}`, `- GEW data: ${g.toFixed(2)} Cr`, `- Iteration data: ${sum(v.it_k).toFixed(2)} Cr (on Total Adjusted Collection) / ${sum(v.it_fp).toFixed(2)} Cr (on Final Total Payable)`, `- Finance P&L: ${f.toFixed(2)} Cr`, '');
  }
  if (T) {
    L.push('GEW vs ITERATION (revenue of this financial year, GEW minus Iteration):');
    L.push(`Total gap: ${sg(T.eff_fy_k)} if Iteration is counted on Total Adjusted Collection, ${sg(T.eff_fy_fp)} if counted on Final Total Payable. It is fully explained by:`);
    A.forEach((x, i) => L.push(`${i + 1}. [${x.gap_id}] ${x.title}: ${n0(x.n)} students, ${x.eff_fy_k != null ? sg(x.eff_fy_k) : '-'}${x.eff_fy_fp != null && x.eff_fy_fp !== x.eff_fy_k ? ` (on Final Total Payable: ${sg(x.eff_fy_fp)})` : ''}`));
    L.push('');
  }
  L.push('GEW / ITERATION vs FINANCE P&L:');
  B.forEach((x) => { const v = x.values, u = v.unit === 'students', g = sum(v.gew), f = sum(v.fin);
    L.push(`- [${x.gap_id}] ${x.title}: GEW ${u ? n0(g) : g.toFixed(2) + ' Cr'}, Finance ${u ? n0(f) : f.toFixed(2) + ' Cr'}${v.it_k.some((y) => y != null) ? `, Iteration ${sum(v.it_k).toFixed(2)} / ${sum(v.it_fp).toFixed(2)} Cr` : ''}`); });
  L.push('', 'PROBLEMS FOUND IN THE GEW DATA (to be checked by the data team):');
  r.flags.filter((c) => ['note', 'ask'].includes(c.severity)).forEach((c) => L.push(`- ${c.title}: ${n0(c.n_rows)}${c.amount != null ? ` (${c.amount < 0 ? '-' : ''}Rs ${n0(Math.abs(c.amount))})` : ''}${c.severity === 'ask' ? ' [to confirm]' : ''}`));
  const okc = r.flags.find((c) => c.check_id === 'negative_amounts');
  if (okc && okc.severity === 'ok') L.push('- Checked: no negative payments, refunds, bookings or revenue.');
  L.push('', 'Points on which we need a decision or an answer:');
  L.push('1. Booking or collection: the GEW data counts the full booking, the Iteration "Total Adjusted Collection" counts only the money collected until its date.');
  L.push('2. Start dates: GEW starts revenue on the admission date, the Iteration sheet usually on the batch start date. Which is right?');
  L.push('3. Students found only in the Iteration sheet, and students whose batch end date differs (lists attached).');
  L.push("4. Finance: how is the 'Revenue' line worked out (booking or collection, from which date, over how many months)?");
  L.push('', 'The Excel file attached lists every student behind each gap and every flag. The same numbers are always up to date on the Gap Check page of the portal.', '', 'Thanks');
  $('mail-text').value = L.join('\n');
}

function render() {
  const g = state.g, p = parts(g);
  $('subline').textContent = `Government Exam Offline · Iteration data prepared on ${dlabel(g.sheet_prepared_on)} · GEW data till ${dlabel(g.data_till)}`;
  if (state.r && state.r.available) register(state.r);
  readFirst(g, p); glance(g, p); bridge(g, p); months(g); students(g); dates(g); other(g); branches(g); pl(g);
  document.getElementById('bridge').classList.add('gapt');
}

(async function main() {
  try {
    state.g = await api('/api/gaps');
    if (!state.g.available) { $('error').hidden = false; $('error').textContent = "The Iteration sheet is not loaded yet, so there is nothing to compare."; return; }
    try {
      const m = await api('/api/meta'); const st = $('stale'); st.hidden = !(m.refresh && m.refresh.ok === false);
      if (!st.hidden) st.textContent = 'The latest automatic update could not be completed, so you are seeing the last good data.';
    } catch (e) { /* optional */ }
    try { state.r = await api('/api/gap_register'); } catch (e) { state.r = null; }
    render();
    $('mail-btn').onclick = () => { $('mail-box').hidden = !$('mail-box').hidden; };
    $('mail-copy').onclick = async () => {
      try { await navigator.clipboard.writeText($('mail-text').value); $('mail-msg').textContent = 'Copied.'; }
      catch (e) { $('mail-text').select(); $('mail-msg').textContent = 'Select the text and press Ctrl+C.'; }
    };
    document.querySelectorAll('.unit-toggle button').forEach((b) => b.onclick = () => {
      state.unit = b.dataset.unit; document.querySelectorAll('.unit-toggle button').forEach((x) => x.classList.toggle('on', x === b)); render();
    });
  } catch (e) { $('error').hidden = false; $('error').textContent = e.message; }
})();

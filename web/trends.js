// Branch Trends page. Last year vs this year, built the same way for both years, plus branches grouped by age.
'use strict';

const MON = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const FYMON = ['Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec','Jan','Feb','Mar'];
const COLORS = ['#9aa0b8', '#4f8ff7', '#22c58e', '#f5a524'];
const state = { h: null, v: null, unit: 'cr', charts: {} };
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const sum = (a) => a.reduce((x, y) => x + (y || 0), 0);
const addArr = (a, b) => a.map((v, i) => v + (b[i] || 0));
const monthLabel = (ym) => `${MON[+ym.slice(5, 7) - 1]}-${ym.slice(2, 4)}`;
const cr = (v, d = 2) => `₹ ${(v / 1e7).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d })} Cr`;
const unitFmt = (v) => state.unit === 'cr' ? (v / 1e7).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  : (v / 1e5).toLocaleString('en-IN', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const inr = (x) => Math.round(x).toLocaleString('en-IN');
const growth = (a, b) => (b ? (a / b - 1) * 100 : null);
const gTxt = (g) => (g == null ? '—' : `${g >= 0 ? '+' : ''}${g.toFixed(0)}%`);
const gCls = (g) => (g == null ? '' : g >= 0 ? 'pos' : 'neg');
const ordinal = (k) => (k === 1 ? '1st' : k === 2 ? '2nd' : k === 3 ? '3rd' : `${k}th`);

async function api(path) {
  const r = await fetch(path); const b = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof b.detail === 'string' ? b.detail : r.statusText);
  return b;
}
const showError = (e) => { const el = $('error'); el.hidden = !e; el.textContent = e ? (e.message || String(e)) : ''; };

const lastIdx = () => state.h.months27.indexOf(state.h.as_of.last_actual_month);
const ytd = (arr) => sum(arr.slice(0, lastIdx() + 1));
function totals(list) {
  const z = () => Array(12).fill(0), t = { rev26: z(), rev27: z(), enr26: z(), enr27: z(), pay26: z(), pay27: z() };
  for (const b of list) for (const k of Object.keys(t)) t[k] = addArr(t[k], b[k]);
  return t;
}
const period = () => `Apr–${monthLabel(state.h.months27[lastIdx()]).slice(0, 3)}`;

// ---------------------------------------------------------------- top cards
function kpis(t) {
  const R26 = sum(t.rev26), y26 = ytd(t.rev26), y27 = ytd(t.rev27), e26 = ytd(t.enr26), e27 = ytd(t.enr27);
  const a26 = e26 ? ytd(t.pay26) / e26 : 0, a27 = e27 ? ytd(t.pay27) / e27 : 0;
  const card = (cls, l, v, d) => `<div class="kpi ${cls}"><div class="lbl">${l}</div><div class="val">${v}</div><div class="det">${d}</div></div>`;
  const g = (x) => `<b class="${gCls(x)}">${gTxt(x)}</b>`;
  const p = period();
  $('kpis').innerHTML =
    card('total', 'Revenue last year (full year)', cr(R26), 'Worked out the same way as this year, from the same two sheets') +
    card('recognised', `Revenue ${p} this year`, cr(y27), `Same months last year: ${cr(y26)} · ${g(growth(y27, y26))}`) +
    card('committed', `Students joined ${p} this year`, inr(e27), `Same months last year: ${inr(e26)} · ${g(growth(e27, e26))}`) +
    card('expected', `Average fee per student ${p}`, `₹ ${inr(a27)}`, `Last year: ₹ ${inr(a26)} · ${g(growth(a27, a26))} (fee includes GST)`);
}

// ---------------------------------------------------------------- charts
function lineChart(id, labels, datasets, yFmt, tipFmt) {
  if (state.charts[id]) state.charts[id].destroy();
  state.charts[id] = new Chart($(id), {
    type: 'line', data: { labels, datasets },
    options: {
      responsive: true, maintainAspectRatio: false, spanGaps: false, interaction: { mode: 'index', intersect: false },
      scales: { x: { ticks: { color: '#cbd0e0', maxRotation: 0, autoSkip: true, maxTicksLimit: 12 }, grid: { display: false } },
                y: { ticks: { color: '#cbd0e0', callback: yFmt }, grid: { color: 'rgba(255,255,255,.08)' }, beginAtZero: true } },
      plugins: { legend: { labels: { color: '#cbd0e0', boxWidth: 14 } }, tooltip: { callbacks: { label: (c) => (c.parsed.y == null ? '' : tipFmt(c)) } } },
    },
  });
}
const ds = (label, data, color, extra = {}) => ({ label, data, borderColor: color, backgroundColor: color, pointRadius: 3, tension: 0.25, borderWidth: 2, ...extra });

function revenueChart(t) {
  const li = lastIdx(), crv = (a) => a.map((v) => (v == null ? null : +(v / 1e7).toFixed(4)));
  lineChart('c-rev', FYMON, [
    ds('Last year', crv(t.rev26), '#9aa0b8'),
    ds('This year: already earned', crv(t.rev27.map((v, i) => (i <= li ? v : null))), '#4f8ff7'),
    ds('This year: from students who have already joined', crv(t.rev27.map((v, i) => (i >= li ? v : null))), '#22c58e', { borderDash: [6, 5] }),
  ], (v) => `${v} Cr`, (c) => `${c.dataset.label}: ₹ ${c.parsed.y.toFixed(2)} Cr`);
  $('rev-note').textContent = `After ${monthLabel(state.h.months27[li])}, this year only counts students who have already joined, so it dips`;
}

function vintageCharts() {
  const v = state.v, groups = v.groups;
  // 1) how a new branch grows: 1st, 2nd, 3rd month ...
  const labels = Array.from({ length: 12 }, (_, i) => ordinal(i + 1));
  lineChart('c-curve', labels, groups.map((g, i) => ds(g.label, g.curve.map((c) => (c.n ? c.avg : null)), COLORS[i % 4], { _n: g.curve.map((c) => c.n) })),
    (x) => x, (c) => `${c.dataset.label}: ${c.parsed.y} students per branch (${c.dataset._n[c.dataIndex]} branch${c.dataset._n[c.dataIndex] > 1 ? 'es' : ''})`);
  $('curve-note').textContent = 'The 1st month is the month the first student joined, so it can be a part month. Lucknow is left out here because our data starts in Jan 2024, so we cannot see when it really started.';
  // 2) students per branch every calendar month since Jan 2024
  lineChart('c-spb', v.months.map(monthLabel), groups.map((g, i) => ds(g.label, g.students_per_branch, COLORS[i % 4], { pointRadius: 0, pointHoverRadius: 4 })),
    (x) => x, (c) => `${c.dataset.label}: ${c.parsed.y} per branch`);
}

// ---------------------------------------------------------------- tables
function vintageTable() {
  const v = state.v, p = period();
  const totRev = v.groups.reduce((s, g) => s + g.this_year.revenue, 0);
  const row = (g) => {
    const a = g.this_year, b = g.last_year;
    const perBr = a.branch_months ? a.students / a.branch_months : 0;
    const gr = b.revenue > 0 ? growth(a.revenue, b.revenue) : null;
    return `<tr><td><b>${esc(g.label)}</b><br><span class="muted">${g.branches.map((x) => esc(x.short)).join(', ')}</span></td>
      <td>${g.branches.length}</td><td>${inr(a.students)}</td><td>${perBr.toFixed(0)}</td><td>${a.avg_fee ? '₹ ' + inr(a.avg_fee) : '—'}</td>
      <td>${unitFmt(a.revenue)}</td><td>${b.revenue > 0 ? unitFmt(b.revenue) : '—'}</td>
      <td class="${gCls(gr)}">${gr == null ? 'new this year' : gTxt(gr)}</td><td>${totRev ? ((a.revenue / totRev) * 100).toFixed(0) : 0}%</td></tr>`;
  };
  $('vintage').innerHTML = `<thead><tr><th>Branches that…</th><th>Number</th><th>Students joined ${p} this year</th><th>Students per branch, per month</th>
    <th>Average fee per student</th><th>Revenue ${p} this year (${state.unit === 'cr' ? '₹ Cr' : '₹ Lakh'})</th><th>Same months last year</th><th>Change</th><th>Share of revenue</th></tr></thead>
    <tbody>${v.groups.map(row).join('')}</tbody>`;
}

function yoyTable() {
  const p = period();
  $('yoy-sub').textContent = `${p}, this year vs last year`;
  const row = (name, t, cls = '') => {
    const r26 = ytd(t.rev26), r27 = ytd(t.rev27), e26 = ytd(t.enr26), e27 = ytd(t.enr27);
    const a26 = e26 ? ytd(t.pay26) / e26 : 0, a27 = e27 ? ytd(t.pay27) / e27 : 0;
    const gr = growth(r27, r26), ge = growth(e27, e26), ga = growth(a27, a26);
    return `<tr class="${cls}"><td>${name}</td><td>${unitFmt(r26)}</td><td>${unitFmt(r27)}</td><td class="${gCls(gr)}">${r26 ? gTxt(gr) : 'new'}</td>
      <td>${inr(e26)}</td><td>${inr(e27)}</td><td class="${gCls(ge)}">${e26 ? gTxt(ge) : 'new'}</td>
      <td>${a26 ? inr(a26) : '—'}</td><td>${inr(a27)}</td><td class="${gCls(ga)}">${a26 ? gTxt(ga) : '—'}</td></tr>`;
  };
  const list = state.h.branches; let body = '';
  for (const c of [...new Set(list.map((b) => b.cohort_id))]) {
    const bs = list.filter((b) => b.cohort_id === c);
    if (bs.length > 1) body += row(`Cohort ${c}`, totals(bs), 'cohort');
    for (const b of bs) body += row(bs.length > 1 ? `&nbsp;&nbsp;${esc(b.name)}` : `Cohort ${c} · ${esc(b.name)}`, totals([b]), 'branch');
  }
  body += row('Total', totals(list), 'total');
  $('yoy').innerHTML = `<thead><tr><th>${state.unit === 'cr' ? '₹ Cr' : '₹ Lakh'}</th><th>Revenue last year</th><th>Revenue this year</th><th>Change</th>
    <th>Students last year</th><th>Students this year</th><th>Change</th><th>Fee last year (₹)</th><th>Fee this year (₹)</th><th>Change</th></tr></thead><tbody>${body}</tbody>`;
}

async function init() {
  const h = state.h;
  try {
    const m = await api('/api/meta');
    const stale = $('stale');
    stale.hidden = !(m.refresh && m.refresh.ok === false);
    if (!stale.hidden) {
      stale.textContent = `The latest automatic update could not be completed, so you are seeing data from ${new Date(m.built_at).toLocaleString('en-IN', { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' })}. Nothing is lost; it will be tried again at 7:30 tomorrow.`;
      stale.title = m.refresh.message || '';
    }
  } catch (e) { /* freshness note is optional */ }
  $('subline').textContent = `Government Exam Offline · built from every student record since Jan 2024 · this year is counted till ${monthLabel(h.as_of.last_actual_month)}`;
  const t = totals(h.branches);
  kpis(t); revenueChart(t); vintageTable(); vintageCharts(); yoyTable();
}

(async function main() {
  try {
    [state.h, state.v] = await Promise.all([api('/api/history'), api('/api/vintage')]);
    await init();
    document.querySelectorAll('.unit-toggle button').forEach((b) => b.onclick = () => {
      state.unit = b.dataset.unit; document.querySelectorAll('.unit-toggle button').forEach((x) => x.classList.toggle('on', x === b));
      vintageTable(); yoyTable();
    });
  } catch (e) { showError(e); }
})();

// Revenue Projection page. Numbers come from /api/*; this file only filters, adds up and draws.
'use strict';

const MON = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const GST = 1.18;   // the annual plan's fee includes 18% GST (confirmed by the business)
const state = { meta: null, view: null, unit: 'cr', expanded: new Set(), chart: null, stack: null, pace: null };
const $ = (id) => document.getElementById(id);

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const monthLabel = (ym) => `${MON[+ym.slice(5, 7) - 1]}-${ym.slice(2, 4)}`;
const sum = (a) => a.reduce((x, y) => x + y, 0);
const addArr = (a, b) => a.map((v, i) => v + (b[i] || 0));
const cr = (v, d = 2) => `₹ ${(v / 1e7).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d })} Cr`;
const unitFmt = (v) => state.unit === 'cr'
  ? (v / 1e7).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  : (v / 1e5).toLocaleString('en-IN', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const pct = (v, d = 1) => `${v >= 0 ? '+' : ''}${v.toFixed(d)}%`;
const fmtDate = (iso) => new Date(iso.slice(0, 10) + 'T00:00:00').toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });

async function api(path, opts) {
  const r = await fetch(path, opts);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) {
    const d = body.detail;
    const err = new Error(typeof d === 'string' ? d : (d && d.message) || r.statusText); err.problems = d && d.problems; throw err;
  }
  return body;
}
function showError(e) { const el = $('error'); el.hidden = !e; el.textContent = e ? (e.message || String(e)) : ''; }

// ---------------------------------------------------------------- selection
function selected() {
  const cohort = $('cohort').value, branch = $('branch').value;
  return state.view.branches.filter((b) => (!cohort || String(b.cohort_id) === cohort) && (!branch || String(b.branch_id) === branch));
}
function totalsOf(list) {
  const z = () => Array(12).fill(0);
  const t = { recognised: z(), committed: z(), expected: z(), enroll_actual: z(), enroll_target: z(), from_last_year: z(), from_earlier_this_year: z(), from_same_month: z() };
  for (const b of list) for (const k of Object.keys(t)) t[k] = addArr(t[k], b[k]);
  return t;
}
const lastActualIdx = () => state.view.months.indexOf(state.view.as_of.last_actual_month);

// ---------------------------------------------------------------- drawing
function renderKpis(t) {
  const R = sum(t.recognised), C = sum(t.committed), E = sum(t.expected), T = R + C + E;
  const backed = T ? ((R + C) / T) * 100 : 0;
  const la = state.view.months[lastActualIdx()];
  const ach = +$('ach').value;
  const card = (cls, lbl, val, det) => `<div class="kpi ${cls}"><div class="lbl">${lbl}</div><div class="val">${val}</div><div class="det">${det}</div></div>`;
  const paceLine = state.pace != null && ach > 0
    ? `<br>If new joinings stay at the pace so far (${state.pace}% of plan): <b>${cr(R + C + E * state.pace / ach)}</b>` : '';
  $('kpis').innerHTML =
    card('total', `Total revenue this year (${esc(state.meta.fy)})`, cr(T), `${backed.toFixed(0)}% of it comes from students who have already joined${paceLine}`) +
    card('recognised', 'Already earned', cr(R), `${monthLabel(state.view.months[0])} to ${monthLabel(la)}`) +
    card('committed', 'Coming from current students', cr(C), 'Students who have already joined') +
    card('expected', 'Coming from new students (estimate)', cr(E),
      `${ach}% of the plan` + ($('basis').value === 'trend' ? ', at the average fee of this year so far' : ", at the plan's own fee"));
}

function renderChart(t) {
  const labels = state.view.months.map(monthLabel);
  const ds = (label, key, color) => ({ label, data: t[key].map((v) => +(v / 1e7).toFixed(4)), backgroundColor: color, borderWidth: 0 });
  if (state.chart) state.chart.destroy();
  state.chart = new Chart($('chart'), {
    type: 'bar',
    data: { labels, datasets: [ds('Already earned', 'recognised', '#4f8ff7'), ds('Coming from current students', 'committed', '#22c58e'), ds('Coming from new students (estimate)', 'expected', '#f5a524')] },
    options: {
      responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
      scales: { x: { stacked: true, ticks: { color: '#cbd0e0' }, grid: { display: false } },
                y: { stacked: true, ticks: { color: '#cbd0e0', callback: (v) => `${v} Cr` }, grid: { color: 'rgba(255,255,255,.08)' } } },
      plugins: { legend: { labels: { color: '#cbd0e0' } },
        tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ₹ ${c.parsed.y.toFixed(2)} Cr`,
                                footer: (items) => `Total: ₹ ${items.reduce((s, i) => s + i.parsed.y, 0).toFixed(2)} Cr` } } },
    },
  });
  $('chart-note').textContent = `Earned till ${monthLabel(state.view.months[lastActualIdx()])}; after that it is a forecast`;
}

function renderStack(t) {
  const labels = state.view.months.map(monthLabel);
  const ds = (label, key, color) => ({ label, data: t[key].map((v) => +(v / 1e7).toFixed(4)), backgroundColor: color, borderWidth: 0 });
  if (state.stack) state.stack.destroy();
  state.stack = new Chart($('chart-stack'), {
    type: 'bar',
    data: { labels, datasets: [
      ds("Students admitted before April (last year's students)", 'from_last_year', '#9aa0b8'),
      ds('Admitted earlier this year', 'from_earlier_this_year', '#4f8ff7'),
      ds('Admitted in the same month', 'from_same_month', '#22c58e'),
      ds('New students we expect (estimate)', 'expected', '#f5a524')] },
    options: {
      responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
      scales: { x: { stacked: true, ticks: { color: '#cbd0e0' }, grid: { display: false } },
                y: { stacked: true, ticks: { color: '#cbd0e0', callback: (v) => `${v} Cr` }, grid: { color: 'rgba(255,255,255,.08)' } } },
      plugins: { legend: { labels: { color: '#cbd0e0' } },
        tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ₹ ${c.parsed.y.toFixed(2)} Cr`,
                                footer: (items) => `Total: ₹ ${items.reduce((s, i) => s + i.parsed.y, 0).toFixed(2)} Cr` } } },
    },
  });
  const first = state.view.months[0];
  const sh = t.from_last_year[0] / (t.recognised[0] + t.committed[0] + t.expected[0] || 1) * 100;
  $('stack-note').textContent = `${monthLabel(first)}: about ${sh.toFixed(0)}% of the revenue comes from students admitted before April. ` +
    'Last year\'s share shrinks as their batches finish; this year\'s admissions take over. Same total as the chart above, only split by when the student was admitted.';
}

function renderGrid(list) {
  const months = state.view.months, la = lastActualIdx();
  const head = `<thead><tr><th>${state.unit === 'cr' ? '₹ Cr' : '₹ Lakh'}</th>${months.map((m, i) =>
    `<th class="${i > la ? 'proj' : ''}">${monthLabel(m)}<br><span class="muted">${i > la ? 'forecast' : 'earned'}</span></th>`).join('')}
    <th>Whole year</th><th>Already earned</th><th>Current students</th><th>New students (est.)</th></tr></thead>`;
  const rowHtml = (name, t, cls, attr = '') => {
    const tot = months.map((_, i) => t.recognised[i] + t.committed[i] + t.expected[i]);
    const cells = tot.map((v, i) => {
      const tip = `Already earned ${unitFmt(t.recognised[i])} · Current students ${unitFmt(t.committed[i])} · New students (estimate) ${unitFmt(t.expected[i])}`;
      return `<td class="${i > la ? 'proj' : ''}" title="${esc(tip)}">${unitFmt(v)}</td>`;
    }).join('');
    return `<tr class="${cls}" ${attr}><td>${name}</td>${cells}<td>${unitFmt(sum(tot))}</td><td>${unitFmt(sum(t.recognised))}</td><td>${unitFmt(sum(t.committed))}</td><td>${unitFmt(sum(t.expected))}</td></tr>`;
  };
  let body = '';
  for (const c of [...new Set(list.map((b) => b.cohort_id))]) {
    const bs = list.filter((b) => b.cohort_id === c);
    const open = state.expanded.has(c) || list.length === 1;
    body += rowHtml(`${open ? '▾' : '▸'} Cohort ${c} <span class="muted">(${bs.length} branch${bs.length > 1 ? 'es' : ''})</span>`, totalsOf(bs), 'cohort', `data-cohort="${c}"`);
    if (open) for (const b of bs) body += rowHtml(esc(b.name), totalsOf([b]), 'branch');
  }
  body += rowHtml('Total', totalsOf(list), 'total');
  $('grid').innerHTML = head + `<tbody>${body}</tbody>`;
}

function renderPace(list) {
  const la = lastActualIdx(), t = totalsOf(list);
  const act = sum(t.enroll_actual.slice(0, la + 1)), tgt = sum(t.enroll_target.slice(0, la + 1));
  state.pace = tgt ? Math.round((act / tgt) * 100) : null;
  $('pace-hint').textContent = tgt
    ? `So far this year: ${act.toLocaleString('en-IN')} students joined against a plan of ${Math.round(tgt).toLocaleString('en-IN')} (${((act / tgt) * 100).toFixed(0)}%)`
    : 'There is no plan for the months gone by in this selection';
}

function renderAll() {
  const list = selected(), t = totalsOf(list);
  renderPace(list); renderKpis(t); renderChart(t); renderStack(t); renderGrid(list);
}

function renderStatic() {
  const m = state.meta;
  const upd = m.built_at;                 // the database is only replaced after a successful update, so this is the last good one
  const stale = $('stale');
  stale.hidden = !(m.refresh && m.refresh.ok === false);
  if (!stale.hidden) {
    stale.textContent = `The latest automatic update could not be completed, so you are seeing data from ${new Date(upd).toLocaleString('en-IN', { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' })}. Nothing is lost; it will be tried again at 7:30 tomorrow.`;
    stale.title = m.refresh.message || '';
  }
  $('subline').textContent = `${m.category} · ${m.fy} · student data till ${fmtDate(m.as_of.snapshot_date)}` +
    (upd ? ` · updated automatically on ${new Date(upd).toLocaleString('en-IN', { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' })}` : '');
  $('aops').innerHTML = `<thead><tr><th>Plan</th><th>About</th><th>Students planned</th><th>Fees planned</th><th></th></tr></thead><tbody>${
    m.aops.map((a) => `<tr><td>${esc(a.name)}</td><td style="text-align:left">${esc(a.description || '')}</td><td>${Math.round(a.enroll_total).toLocaleString('en-IN')}</td><td>${cr(a.booking_total)}</td><td>${
      a.aop_id === 1 ? '<span class="muted">original plan</span>' : `<button class="ghost" data-del="${a.aop_id}">Delete</button>`}</td></tr>`).join('')}</tbody>`;
}

// ---------------------------------------------------------------- loading
let reqSeq = 0;
async function loadView() {
  const seq = ++reqSeq;
  const q = new URLSearchParams({ aop_id: $('aop').value, achievement: $('ach').value, basis: $('basis').value, gst_divisor: GST });
  try {
    const v = await api(`/api/revenue?${q}`);
    if (seq !== reqSeq) return;            // a newer request replaced this one
    state.view = v; showError(null);
    if (!$('cohort').options.length) fillFilters();
    renderStatic(); renderAll();
  } catch (e) { showError(e); }
}
function fillFilters() {
  const cohorts = [...new Set(state.view.branches.map((b) => b.cohort_id))];
  $('cohort').innerHTML = '<option value="">All cohorts</option>' + cohorts.map((c) => `<option value="${c}">Cohort ${c}</option>`).join('');
  fillBranches();
}
function fillBranches() {
  const c = $('cohort').value;
  $('branch').innerHTML = '<option value="">All branches</option>' + state.view.branches.filter((b) => !c || String(b.cohort_id) === c)
    .map((b) => `<option value="${b.branch_id}">${esc(b.name)}</option>`).join('');
}
async function loadMeta() {
  state.meta = await api('/api/meta');
  $('aop').innerHTML = state.meta.aops.map((a) => `<option value="${a.aop_id}">${esc(a.name)}</option>`).join('');
}

function parseCsv(text) {
  const rows = []; let row = [], cur = '', q = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (q) { if (ch === '"') { if (text[i + 1] === '"') { cur += '"'; i++; } else q = false; } else cur += ch; }
    else if (ch === '"') q = true;
    else if (ch === ',') { row.push(cur); cur = ''; }
    else if (ch === '\n' || ch === '\r') { if (ch === '\r' && text[i + 1] === '\n') i++; row.push(cur); cur = ''; if (row.some((c) => c.trim() !== '')) rows.push(row); row = []; }
    else cur += ch;
  }
  row.push(cur); if (row.some((c) => c.trim() !== '')) rows.push(row);
  return rows;
}

function wire() {
  let t;
  const reload = () => { clearTimeout(t); t = setTimeout(loadView, 200); };
  $('aop').onchange = loadView;
  $('basis').onchange = loadView;
  $('ach').oninput = () => { $('ach-val').textContent = `${$('ach').value}%`; reload(); };
  $('ach-pace').onclick = () => { if (state.pace != null) { $('ach').value = Math.min(150, Math.max(0, state.pace)); $('ach').oninput(); } };
  $('cohort').onchange = () => { fillBranches(); renderAll(); };
  $('branch').onchange = renderAll;
  document.querySelectorAll('.unit-toggle button').forEach((b) => b.onclick = () => {
    state.unit = b.dataset.unit;
    document.querySelectorAll('.unit-toggle button').forEach((x) => x.classList.toggle('on', x === b));
    renderGrid(selected());
  });
  $('grid').onclick = (e) => {
    const tr = e.target.closest('tr.cohort'); if (!tr) return;
    const c = +tr.dataset.cohort; state.expanded.has(c) ? state.expanded.delete(c) : state.expanded.add(c);
    renderGrid(selected());
  };
  $('aops').onclick = async (e) => {
    const id = e.target.dataset.del; if (!id) return;
    if (!confirm('Delete this plan? This cannot be undone.')) return;
    try { await api(`/api/aop/${id}`, { method: 'DELETE' }); await loadMeta(); $('aop').value = '1'; await loadView(); } catch (err) { showError(err); }
  };
  $('aop-form').onsubmit = async (e) => {
    e.preventDefault();
    const msg = $('aop-msg'); msg.className = 'note'; msg.textContent = 'Uploading…';
    try {
      const rows = parseCsv(await $('aop-file').files[0].text());
      const hdr = rows[0].map((h) => h.trim().toLowerCase());
      const ix = (n) => hdr.indexOf(n);
      for (const n of ['center', 'month', 'enroll_target']) if (ix(n) < 0) throw new Error(`The file is missing the "${n}" column`);
      if (ix('aov') < 0 && ix('booking_target') < 0) throw new Error('The file needs an "aov" or "booking_target" column');
      const num = (v) => (v == null || v.trim() === '' ? null : Number(v.replace(/,/g, '')));
      const payload = {
        name: $('aop-name').value, overwrite: $('aop-over').checked,
        rows: rows.slice(1).map((r) => ({ center: r[ix('center')], month: r[ix('month')], enroll_target: num(r[ix('enroll_target')]) ?? 0,
          aov: ix('aov') >= 0 ? num(r[ix('aov')]) : null, booking_target: ix('booking_target') >= 0 ? num(r[ix('booking_target')]) : null })),
      };
      const res = await api('/api/aop', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      msg.className = 'note pos';
      msg.textContent = `Saved ${res.name}: ${Math.round(res.enroll_total).toLocaleString('en-IN')} students planned, ${cr(res.booking_total)} in fees.` +
        (res.branches_without_targets.length ? ` No plan for: ${res.branches_without_targets.join(', ')}.` : '');
      await loadMeta(); $('aop').value = String(res.aop_id); await loadView();
    } catch (err) {
      msg.className = 'note neg';
      msg.textContent = err.message + (err.problems ? ' — ' + err.problems.slice(0, 8).join('; ') : '');
    }
  };
}

(async function init() {
  try { await loadMeta(); wire(); await loadView(); } catch (e) { showError(e); }
})();

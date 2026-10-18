// Section 4: Branch-wise trends of Enrollment and AOV, in four situations.
// Uses the dashboard's own data (enrollmentsRaw, targetsRaw) and respects the Cohort / Branch filters and the selected Plan (AOP).
'use strict';

let btChart = null;
const BT_GROUPS = [
  { label: 'Started in 2024 or earlier', from: null, to: '2024-12', color: '#9aa0b8' },
  { label: 'Started in 2025', from: '2025-01', to: '2025-12', color: '#4f8ff7' },
  { label: 'Started Jan-Mar 2026', from: '2026-01', to: '2026-03', color: '#22c58e' },
  { label: 'Started Apr 2026 or later', from: '2026-04', to: null, color: '#f5a524' },
];
const BT_MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const btKey = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
const btAdd = (k, n) => { const [y, m] = k.split('-').map(Number); const t = y * 12 + (m - 1) + n; return `${Math.floor(t / 12)}-${String(t % 12 + 1).padStart(2, '0')}`; };
const btLabel = (k) => `${BT_MON[+k.slice(5, 7) - 1]}-${k.slice(2, 4)}`;
const btRange = (a, b) => { const out = []; for (let k = a; k <= b; k = btAdd(k, 1)) out.push(k); return out; };
const btN = (v) => Math.round(v).toLocaleString('en-IN');
const btPct = (v) => (v == null ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(0)}%`);
const btShort = (c) => c.replace(' Government', '');

function btCollect(passes) {
  const by = {}, first = {};
  for (const r of enrollmentsRaw) {
    if (!passes(r.center)) continue;
    const k = btKey(r.parsedDate);
    ((by[r.center] ||= {})[k] ||= { n: 0, pay: 0 });
    by[r.center][k].n += 1;
    by[r.center][k].pay += r.payable || 0;
    if (!first[r.center] || k < first[r.center]) first[r.center] = k;
  }
  const centers = Object.keys(by).sort((a, b) => (COHORT_MAPPING[a] - COHORT_MAPPING[b]) || a.localeCompare(b));
  return { by, first, centers };
}

function btValue(cell, metric) {
  if (!cell || !cell.n) return null;
  return metric === 'enroll' ? cell.n : cell.pay / cell.n;
}
function btSum(by, centers, months) {
  const t = { n: 0, pay: 0 };
  for (const c of centers) for (const k of months) { const x = by[c] && by[c][k]; if (x) { t.n += x.n; t.pay += x.pay; } }
  return t;
}
function btHeat(v, max) {
  if (v == null || !max) return '';
  return `background:rgba(79,143,247,${(0.08 + 0.5 * Math.min(1, v / max)).toFixed(2)})`;
}
const btMetricName = (m) => (m === 'enroll' ? 'Enrollment' : 'AOV');
const btFmt = (v, m) => (v == null ? '—' : m === 'enroll' ? btN(v) : '₹ ' + btN(v));

function btDraw(type, labels, datasets, yTitle) {
  const wrap = document.getElementById('bt-chart-wrap');
  if (btChart) { btChart.destroy(); btChart = null; }
  if (!datasets) { wrap.style.display = 'none'; return; }
  wrap.style.display = '';
  btChart = new Chart(document.getElementById('bt-chart'), {
    type, data: { labels, datasets },
    options: {
      responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
      scales: { x: { ticks: { color: '#cbd0e0', maxRotation: 60, autoSkip: true }, grid: { display: false } },
                y: { beginAtZero: true, title: { display: !!yTitle, text: yTitle, color: '#9aa0b8' }, ticks: { color: '#cbd0e0' }, grid: { color: 'rgba(255,255,255,.08)' } } },
      plugins: { legend: { labels: { color: '#cbd0e0' } } },
    },
  });
}
const btLine = (label, data, color, extra = {}) => ({ label, data, borderColor: color, backgroundColor: color, tension: 0.25, pointRadius: 3, borderWidth: 2, ...extra });

function renderBranchTrends() {
  const metric = document.getElementById('bt-metric').value;
  const view = document.getElementById('bt-view').value;
  const passes = trendsPassesFilter(document.getElementById('filter-cohort').value, document.getElementById('filter-branch').value);
  const { by, first, centers } = btCollect(passes);
  const hint = document.getElementById('bt-hint'), table = document.getElementById('bt-table');
  if (!centers.length) { hint.textContent = 'No data for this selection.'; table.innerHTML = ''; btDraw('line', [], null); return; }

  const asOf = dataAsOfDate || new Date();
  const eom = new Date(asOf.getFullYear(), asOf.getMonth() + 1, 0).getDate() === asOf.getDate();
  const curKey = btKey(asOf), lastFull = eom ? curKey : btAdd(curKey, -1);
  const fyStartKey = btKey(getFYStart(asOf));
  const nm = btMetricName(metric);

  if (view === 'monthly') {
    const months = btRange(btAdd(lastFull, -11), lastFull).concat(eom ? [] : [curKey]);
    hint.textContent = `${nm} of every branch, month by month (last 12 full months${eom ? '' : ', plus the current month so far'}). Each row is shaded against its own highest month: darker means higher.`;
    const rows = centers.map((c) => {
      const vals = months.map((k) => btValue(by[c] && by[c][k], metric));
      const max = Math.max(0, ...vals.filter((v) => v != null));
      return `<tr><td>${btShort(c)}</td>${vals.map((v, i) => `<td style="${btHeat(v, max)}">${btFmt(v, metric)}</td>`).join('')}</tr>`;
    }).join('');
    const tot = months.map((k) => { const t = btSum(by, centers, [k]); return metric === 'enroll' ? t.n : (t.n ? t.pay / t.n : null); });
    table.innerHTML = `<thead><tr><th>Branch</th>${months.map((k) => `<th>${btLabel(k)}${k === curKey && !eom ? '<br><span class="muted">part month</span>' : ''}</th>`).join('')}</tr></thead>
      <tbody>${rows}<tr class="total-row"><td><b>All selected</b></td>${tot.map((v) => `<td><b>${btFmt(v, metric)}</b></td>`).join('')}</tr></tbody>`;
    btDraw('line', months.map(btLabel), [btLine(`All selected branches: ${nm}`, tot, '#4f8ff7')], metric === 'enroll' ? 'students' : '₹ per student');
    return;
  }

  if (view === 'yoy') {
    const cur = btRange(fyStartKey, lastFull), prev = cur.map((k) => btAdd(k, -12));
    hint.textContent = `${nm} this year (${btLabel(cur[0])} to ${btLabel(cur[cur.length - 1])}) against the same months last year. Branches that did not exist last year show "new".`;
    const val = (c, ms) => { const t = btSum(by, [c], ms); return metric === 'enroll' ? t.n : (t.n ? t.pay / t.n : null); };
    const data = centers.map((c) => ({ c, a: val(c, cur), b: val(c, prev) }));
    const tt = (ms) => { const t = btSum(by, centers, ms); return metric === 'enroll' ? t.n : (t.n ? t.pay / t.n : null); };
    const A = tt(cur), Bv = tt(prev);
    const growth = (a, b) => (a != null && b ? (a / b - 1) * 100 : null);
    const row = (name, a, b, bold) => { const g = growth(a, b); const cls = g == null ? '' : g >= 0 ? 'vs-pos' : 'vs-neg';
      return `<tr>${bold ? '<td><b>' + name + '</b></td>' : '<td>' + name + '</td>'}<td>${btFmt(b, metric)}</td><td>${btFmt(a, metric)}</td><td class="${cls}">${b ? btPct(g) : 'new'}</td></tr>`; };
    table.innerHTML = `<thead><tr><th>Branch</th><th>Last year</th><th>This year</th><th>Change</th></tr></thead>
      <tbody>${data.map((d) => row(btShort(d.c), d.a, d.b)).join('')}${row('All selected', A, Bv, true)}</tbody>`;
    btDraw('bar', data.map((d) => btShort(d.c)), [
      { label: 'Last year', data: data.map((d) => (d.b == null ? null : Math.round(d.b))), backgroundColor: '#9aa0b8' },
      { label: 'This year', data: data.map((d) => (d.a == null ? null : Math.round(d.a))), backgroundColor: '#4f8ff7' }], metric === 'enroll' ? 'students' : '₹ per student');
    return;
  }

  if (view === 'plan') {
    const months = btRange(fyStartKey, lastFull);
    const tgt = {};
    for (const t of targetsRaw) { if (!passes(t.Center)) continue; const k = btKey(t.parsedDate); ((tgt[t.Center] ||= {})[k] ||= { e: 0, b: 0 }); tgt[t.Center][k].e += t.enrollTarget; tgt[t.Center][k].b += t.bookingTarget; }
    const planName = document.getElementById('aop-badge') && document.getElementById('aop-badge').textContent ? document.getElementById('aop-badge').textContent.replace('· plan: ', '') : 'live sheet';
    hint.textContent = `${nm}: actual as a percentage of the plan (${planName}), month by month this year. Green = at or above plan, amber = within 20% below, red = more than 20% below. Change the plan at the top of the page.`;
    const planVal = (c, ks) => { let e = 0, b = 0; for (const k of ks) { const x = tgt[c] && tgt[c][k]; if (x) { e += x.e; b += x.b; } } return metric === 'enroll' ? e : (e ? b / e : null); };
    const actVal = (c, ks) => { const t = btSum(by, [c], ks); return metric === 'enroll' ? t.n : (t.n ? t.pay / t.n : null); };
    const cell = (a, p) => { if (p == null || !p || a == null) return '<td>—</td>'; const r = a / p * 100; const col = r >= 100 ? 'rgba(34,197,142,.35)' : r >= 80 ? 'rgba(245,165,36,.30)' : 'rgba(244,91,105,.35)'; return `<td style="background:${col}">${r.toFixed(0)}%</td>`; };
    const rows = centers.map((c) => `<tr><td>${btShort(c)}</td>${months.map((k) => cell(actVal(c, [k]), planVal(c, [k]))).join('')}${cell(actVal(c, months), planVal(c, months))}</tr>`).join('');
    const allA = (ks) => { const t = btSum(by, centers, ks); return metric === 'enroll' ? t.n : (t.n ? t.pay / t.n : null); };
    const allP = (ks) => { let e = 0, b = 0; for (const c of centers) for (const k of ks) { const x = tgt[c] && tgt[c][k]; if (x) { e += x.e; b += x.b; } } return metric === 'enroll' ? e : (e ? b / e : null); };
    table.innerHTML = `<thead><tr><th>Branch</th>${months.map((k) => `<th>${btLabel(k)}</th>`).join('')}<th>Year so far</th></tr></thead>
      <tbody>${rows}<tr class="total-row"><td><b>All selected</b></td>${months.map((k) => cell(allA([k]), allP([k]))).join('')}${cell(allA(months), allP(months))}</tr></tbody>`;
    btDraw('line', months.map(btLabel), [btLine('Actual', months.map((k) => allA([k])), '#4f8ff7'), btLine('Plan', months.map((k) => allP([k])), '#f5a524', { borderDash: [6, 5] })], metric === 'enroll' ? 'students' : '₹ per student');
    return;
  }

  // view === 'age'
  const dataStart = Object.values(first).reduce((m, k) => (k < m ? k : m), '9999-12');
  const months = btRange(dataStart, lastFull);
  const groupOf = (c) => BT_GROUPS.findIndex((g) => (!g.from || first[c] >= g.from) && (!g.to || first[c] <= g.to));
  const groups = BT_GROUPS.map((g, i) => ({ ...g, members: centers.filter((c) => groupOf(c) === i) })).filter((g) => g.members.length);
  hint.textContent = `Branches grouped by the month their first student joined (our data starts ${btLabel(dataStart)}, so the oldest group may be older than it looks). ${metric === 'enroll' ? 'Students joining per branch, per month.' : 'Average fee per student (AOV) of each group.'}`;
  const series = groups.map((g) => months.map((k) => {
    const active = g.members.filter((c) => first[c] <= k);
    if (!active.length) return null;
    const t = btSum(by, active, [k]);
    return metric === 'enroll' ? +(t.n / active.length).toFixed(1) : (t.n ? Math.round(t.pay / t.n) : null);
  }));
  const cur = btRange(fyStartKey, lastFull), prev = cur.map((k) => btAdd(k, -12));
  const rows = groups.map((g) => {
    const f = (ms) => { const t = btSum(by, g.members, ms); const bm = g.members.reduce((s, c) => s + ms.filter((k) => k >= first[c]).length, 0);
      return metric === 'enroll' ? (bm ? t.n / bm : null) : (t.n ? t.pay / t.n : null); };
    const a = f(cur), b = f(prev), gr = (a != null && b) ? (a / b - 1) * 100 : null;
    return `<tr><td><b>${g.label}</b><br><span class="muted">${g.members.map(btShort).join(', ')}</span></td><td>${g.members.length}</td>
      <td>${b == null ? '—' : btFmt(b, metric)}</td><td>${btFmt(a, metric)}</td><td class="${gr == null ? '' : gr >= 0 ? 'vs-pos' : 'vs-neg'}">${gr == null ? 'new this year' : btPct(gr)}</td></tr>`; }).join('');
  table.innerHTML = `<thead><tr><th>Branches that…</th><th>Number</th><th>Last year${metric === 'enroll' ? ' (per branch per month)' : ''}</th><th>This year${metric === 'enroll' ? ' (per branch per month)' : ''}</th><th>Change</th></tr></thead><tbody>${rows}</tbody>`;
  btDraw('line', months.map(btLabel), groups.map((g, i) => btLine(g.label, series[i], g.color, { pointRadius: 0, pointHoverRadius: 4 })), metric === 'enroll' ? 'students per branch' : '₹ per student');
}

document.getElementById('bt-metric').addEventListener('change', renderBranchTrends);
document.getElementById('bt-view').addEventListener('change', renderBranchTrends);

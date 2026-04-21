let STATE = { picks: [], sortKey: 'game_date', sortDir: -1 };

async function load() {
  try {
    const r = await fetch('history.json?t=' + Date.now());
    if (!r.ok) throw new Error('no history');
    const data = await r.json();
    STATE.picks = data.picks || [];
    document.getElementById('updated').textContent = data.generated_at || '—';
    renderSummary(data.summary || {});
    populateMarkets();
    render();
  } catch (e) {
    document.getElementById('empty').style.display = 'block';
    document.querySelector('#tbl').style.display = 'none';
  }
}

function renderSummary(s) {
  const total = s.total || 0;
  const wins = s.wins || 0;
  const losses = s.losses || 0;
  const pushes = s.pushes || 0;
  const units = s.units || 0;
  const wr = (wins + losses) ? (100 * wins / (wins + losses)) : 0;
  const roi = total ? (100 * units / total) : 0;
  const html = [
    stat('Picks graduadas', total),
    stat('Win rate', wr.toFixed(1) + '%'),
    stat('Unidades', (units >= 0 ? '+' : '') + units.toFixed(2), units >= 0 ? 'ev-pos' : 'ev-neg'),
    stat('ROI', roi.toFixed(1) + '%', roi >= 0 ? 'ev-pos' : 'ev-neg'),
    stat('Wins / Losses / Pushes', `${wins} / ${losses} / ${pushes}`),
  ].join('');
  document.getElementById('summary').innerHTML = html;
}

function stat(label, n, cls) {
  return `<div class="stat"><div class="n ${cls||''}">${n}</div><div class="l">${label}</div></div>`;
}

function populateMarkets() {
  const set = new Set(STATE.picks.map(p => p.market));
  const sel = document.getElementById('f-market');
  [...set].sort().forEach(m => {
    const o = document.createElement('option');
    o.value = m; o.textContent = m.replace('player_', '');
    sel.appendChild(o);
  });
}

function applyFilters(picks) {
  const q = document.getElementById('f-player').value.toLowerCase();
  const m = document.getElementById('f-market').value;
  const r = document.getElementById('f-result').value;
  return picks.filter(p => {
    if (q && !p.player_name.toLowerCase().includes(q)) return false;
    if (m && p.market !== m) return false;
    if (r === 'pending' && p.result) return false;
    if (r && r !== 'pending' && p.result !== r) return false;
    return true;
  });
}

function render() {
  let picks = applyFilters(STATE.picks);
  picks.sort((a, b) => {
    const k = STATE.sortKey;
    const av = a[k]; const bv = b[k];
    if (av == null) return 1;
    if (bv == null) return -1;
    if (av < bv) return -1 * STATE.sortDir;
    if (av > bv) return 1 * STATE.sortDir;
    return 0;
  });
  const rows = picks.map(p => `
    <tr>
      <td>${p.game_date || ''}</td>
      <td class="muted">${abbr(p.away_team)} @ ${abbr(p.home_team)}</td>
      <td>${p.player_name}</td>
      <td>${(p.market || '').replace('player_', '')}</td>
      <td><span class="side">${p.side}</span> ${p.line}</td>
      <td>${(p.decimal_odds || 0).toFixed(2)}</td>
      <td class="${p.ev >= 0 ? 'ev-pos' : 'ev-neg'}">${((p.ev||0)*100).toFixed(1)}%</td>
      <td class="hide-mobile muted">${(p.model_mean||0).toFixed(1)} ± ${(p.model_std||0).toFixed(1)}</td>
      <td class="hide-mobile muted">${p.bookmaker||''}</td>
      <td class="${p.result ? 'r-'+p.result : 'r-pending'}">${
        p.result ? p.result + (p.actual_value != null ? ` (${p.actual_value})` : '') : '—'
      }</td>
    </tr>
  `).join('');
  document.getElementById('rows').innerHTML = rows || '';
  document.getElementById('empty').style.display = picks.length ? 'none' : 'block';
}

function abbr(team) {
  if (!team) return '';
  const parts = team.split(' ');
  return parts[parts.length - 1].slice(0, 3).toUpperCase();
}

document.querySelectorAll('th[data-k]').forEach(th => {
  th.addEventListener('click', () => {
    const k = th.dataset.k;
    if (STATE.sortKey === k) STATE.sortDir *= -1;
    else { STATE.sortKey = k; STATE.sortDir = -1; }
    render();
  });
});
['f-player', 'f-market', 'f-result'].forEach(id => {
  document.getElementById(id).addEventListener('input', render);
  document.getElementById(id).addEventListener('change', render);
});

load();

let STATE = { picks: [], sortKey: 'game_date', sortDir: -1, charts: {} };

const MARKET_LABELS = {
  'player_points': 'Pontos',
  'player_rebounds': 'Ressaltos',
  'player_assists': 'Assistências',
  'player_threes': 'Triplos',
  'player_blocks': 'Bloqueios',
  'player_steals': 'Roubos',
  'player_turnovers': 'Erros',
  'player_points_rebounds_assists': 'Pts+Reb+Ast',
  'player_points_rebounds': 'Pts+Reb',
  'player_points_assists': 'Pts+Ast',
  'player_rebounds_assists': 'Reb+Ast',
};

const COLORS = {
  orange: '#FF8C00',
  blue: '#1E90FF',
  win: '#10b981',
  loss: '#ef4444',
  push: '#f59e0b',
};

// LOAD DATA
async function load() {
  try {
    const r = await fetch('history.json?t=' + Date.now());
    if (!r.ok) throw new Error('no history');
    const data = await r.json();
    STATE.picks = data.picks || [];
    document.getElementById('updated').textContent = data.generated_at || '—';
    renderHeroStats(data.summary || {});
    populateMarkets();
    renderMarketCards();
    renderCharts();
    render();
  } catch (e) {
    console.error(e);
    document.getElementById('empty').style.display = 'block';
    document.querySelector('#tbl').style.display = 'none';
  }
}

// HERO STATS
function renderHeroStats(s) {
  const total = s.total || 0;
  const wins = s.wins || 0;
  const losses = s.losses || 0;
  const pushes = s.pushes || 0;
  const units = s.units || 0;
  const wr = (wins + losses) ? (100 * wins / (wins + losses)) : 0;
  const roi = total ? (100 * units / total) : 0;

  const stats = [
    { label: 'Total Picks', value: total, color: 'orange' },
    { label: 'Win Rate', value: wr.toFixed(1) + '%', color: wr >= 50 ? 'orange' : 'loss' },
    { label: 'Unidades', value: (units >= 0 ? '+' : '') + units.toFixed(2), color: units >= 0 ? 'orange' : 'negative' },
    { label: 'ROI', value: roi.toFixed(1) + '%', color: roi >= 0 ? 'orange' : 'negative' },
    { label: 'W/L/P', value: `${wins}/${losses}/${pushes}`, color: 'blue' },
  ];

  const html = stats.map(s => `
    <div class="stat-card ${s.color === 'negative' ? 'negative' : ''}">
      <div class="stat-card-inner">
        <div class="stat-value">${s.value}</div>
        <div class="stat-label">${s.label}</div>
      </div>
    </div>
  `).join('');

  document.getElementById('hero-stats').innerHTML = html;
}

// MARKET CARDS
function renderMarketCards() {
  const byMarket = groupByMarket(STATE.picks);
  const markets = Object.entries(byMarket).map(([market, picks]) => {
    const graded = picks.filter(p => p.result);
    const wins = graded.filter(p => p.result === 'WIN').length;
    const losses = graded.filter(p => p.result === 'LOSS').length;
    const wr = (wins + losses) ? ((100 * wins / (wins + losses)).toFixed(1)) : '—';
    const units = graded.reduce((sum, p) => {
      if (p.result === 'WIN') return sum + (p.decimal_odds - 1);
      if (p.result === 'LOSS') return sum - 1;
      return sum;
    }, 0);

    return {
      market,
      label: MARKET_LABELS[market] || market.replace('player_', ''),
      total: picks.length,
      graded: graded.length,
      wins,
      losses,
      wr,
      units: units.toFixed(2),
      ev: (picks.reduce((sum, p) => sum + (p.ev || 0), 0) / picks.length).toFixed(3),
    };
  }).sort((a, b) => b.total - a.total);

  const html = markets.map(m => `
    <div class="market-card">
      <div class="market-name">${m.label}</div>
      <div class="market-stats">
        <div class="market-stat">
          <span class="market-stat-label">Total</span>
          <span class="market-stat-value">${m.total}</span>
        </div>
        <div class="market-stat">
          <span class="market-stat-label">Graduadas</span>
          <span class="market-stat-value">${m.graded}</span>
        </div>
        <div class="market-stat">
          <span class="market-stat-label">Win Rate</span>
          <span class="market-stat-value">${m.wr}%</span>
        </div>
        <div class="market-stat">
          <span class="market-stat-label">Unidades</span>
          <span class="market-stat-value" style="color: ${m.units >= 0 ? '#10b981' : '#ef4444'}">${m.units >= 0 ? '+' : ''}${m.units}</span>
        </div>
        <div class="market-stat">
          <span class="market-stat-label">EV Médio</span>
          <span class="market-stat-value">${(parseFloat(m.ev) * 100).toFixed(1)}%</span>
        </div>
        <div class="market-stat">
          <span class="market-stat-label">W/L</span>
          <span class="market-stat-value">${m.wins}/${m.losses}</span>
        </div>
      </div>
    </div>
  `).join('');

  document.getElementById('market-grid').innerHTML = html;
}

// CHARTS
function renderCharts() {
  renderCumulativeChart();
  renderDailyWRChart();
  renderResultsChart();
  renderMarketsChart();
}

function renderCumulativeChart() {
  const graded = STATE.picks.filter(p => p.result).sort((a, b) => a.game_date.localeCompare(b.game_date));
  let cumulative = 0;
  const labels = [];
  const data = [];

  graded.forEach(p => {
    if (p.result === 'WIN') cumulative += (p.decimal_odds - 1);
    else if (p.result === 'LOSS') cumulative -= 1;
    labels.push(p.game_date);
    data.push(cumulative);
  });

  const ctx = document.getElementById('cumulative-chart').getContext('2d');
  if (STATE.charts.cumulative) STATE.charts.cumulative.destroy();

  STATE.charts.cumulative = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: 'Ganho/Perda Cumulativo',
        data,
        borderColor: COLORS.orange,
        backgroundColor: `rgba(255, 140, 0, 0.1)`,
        borderWidth: 3,
        fill: true,
        tension: 0.4,
        pointRadius: 3,
        pointBackgroundColor: COLORS.orange,
        pointBorderColor: '#fff',
        pointBorderWidth: 2,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        filler: { propagate: true }
      },
      scales: {
        x: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#9ca3af' } },
        y: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#9ca3af' } }
      }
    }
  });
}

function renderDailyWRChart() {
  const byDate = {};
  STATE.picks.filter(p => p.result).forEach(p => {
    if (!byDate[p.game_date]) byDate[p.game_date] = { w: 0, l: 0 };
    if (p.result === 'WIN') byDate[p.game_date].w++;
    else if (p.result === 'LOSS') byDate[p.game_date].l++;
  });

  const labels = Object.keys(byDate).sort();
  const data = labels.map(d => {
    const { w, l } = byDate[d];
    return (w + l) ? (100 * w / (w + l)) : 0;
  });

  const ctx = document.getElementById('daily-wr-chart').getContext('2d');
  if (STATE.charts.dailyWR) STATE.charts.dailyWR.destroy();

  STATE.charts.dailyWR = new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        label: 'Win Rate (%)',
        data,
        backgroundColor: (ctx) => ctx.raw >= 50 ? COLORS.win : COLORS.loss,
        borderColor: 'transparent',
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { display: false }, ticks: { color: '#9ca3af' } },
        y: {
          grid: { color: 'rgba(255,255,255,0.05)' },
          ticks: { color: '#9ca3af' },
          max: 100,
          beginAtZero: true
        }
      }
    }
  });
}

function renderResultsChart() {
  const graded = STATE.picks.filter(p => p.result);
  const wins = graded.filter(p => p.result === 'WIN').length;
  const losses = graded.filter(p => p.result === 'LOSS').length;
  const pushes = graded.filter(p => p.result === 'PUSH').length;

  const ctx = document.getElementById('results-chart').getContext('2d');
  if (STATE.charts.results) STATE.charts.results.destroy();

  STATE.charts.results = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: ['Wins', 'Losses', 'Pushes'],
      datasets: [{
        data: [wins, losses, pushes],
        backgroundColor: [COLORS.win, COLORS.loss, COLORS.push],
        borderColor: '#0F0F0F',
        borderWidth: 2,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { labels: { color: '#9ca3af' } }
      }
    }
  });
}

function renderMarketsChart() {
  const byMarket = groupByMarket(STATE.picks);
  const markets = Object.entries(byMarket).map(([market, picks]) => {
    const graded = picks.filter(p => p.result);
    const wins = graded.filter(p => p.result === 'WIN').length;
    const losses = graded.filter(p => p.result === 'LOSS').length;
    const wr = (wins + losses) ? (100 * wins / (wins + losses)) : 0;
    return {
      label: MARKET_LABELS[market] || market.replace('player_', ''),
      wr: parseFloat(wr.toFixed(1))
    };
  }).sort((a, b) => b.wr - a.wr);

  const ctx = document.getElementById('markets-chart').getContext('2d');
  if (STATE.charts.markets) STATE.charts.markets.destroy();

  STATE.charts.markets = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: markets.map(m => m.label),
      datasets: [{
        label: 'Win Rate (%)',
        data: markets.map(m => m.wr),
        backgroundColor: COLORS.blue,
        borderColor: 'transparent',
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { display: false }, ticks: { color: '#9ca3af' } },
        y: {
          grid: { color: 'rgba(255,255,255,0.05)' },
          ticks: { color: '#9ca3af' },
          max: 100,
          beginAtZero: true
        }
      }
    }
  });
}

// HELPERS
function groupByMarket(picks) {
  const result = {};
  picks.forEach(p => {
    if (!result[p.market]) result[p.market] = [];
    result[p.market].push(p);
  });
  return result;
}

function populateMarkets() {
  const set = new Set(STATE.picks.map(p => p.market));
  const sel = document.getElementById('f-market');
  [...set].sort().forEach(m => {
    const o = document.createElement('option');
    o.value = m;
    o.textContent = MARKET_LABELS[m] || m.replace('player_', '');
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
    const av = a[k];
    const bv = b[k];
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
      <td>${((p.ev || 0) * 100).toFixed(1)}%</td>
      <td class="hide-mobile muted">${(p.model_mean || 0).toFixed(1)} ± ${(p.model_std || 0).toFixed(1)}</td>
      <td class="${p.result ? 'r-' + p.result : 'r-pending'}">${
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

// TABS
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));

    btn.classList.add('active');
    const tabId = btn.dataset.tab;
    document.getElementById('tab-' + tabId).classList.add('active');
  });
});

// TABLE SORTING
document.querySelectorAll('th[data-k]').forEach(th => {
  th.addEventListener('click', () => {
    const k = th.dataset.k;
    if (STATE.sortKey === k) STATE.sortDir *= -1;
    else {
      STATE.sortKey = k;
      STATE.sortDir = -1;
    }
    render();
  });
});

// FILTERS
['f-player', 'f-market', 'f-result'].forEach(id => {
  document.getElementById(id).addEventListener('input', render);
  document.getElementById(id).addEventListener('change', render);
});

load();

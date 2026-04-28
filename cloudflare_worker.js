/**
 * NBA Props Bot — Cloudflare Worker (Telegram Webhook)
 * Responde a comandos Telegram de forma INSTANTÂNEA (milissegundos).
 *
 * Variáveis de ambiente obrigatórias no Worker:
 *   TELEGRAM_BOT_TOKEN  — token do bot (ex: 123456:ABC...)
 *   GITHUB_TOKEN        — Personal Access Token com scope "repo"
 *
 * Constantes a ajustar se mudares de repo / URL das pages:
 */

const REPO       = "matos-666/triplethreataipicks";
const PAGES_URL  = "https://matos-666.github.io/triplethreataipicks/history.json";
const AFFILIATE  = "https://dashboard.onetwoaffiliates.com/click?campaign_id=797&ref_id=370";

// ─────────────────────────────────────────────────────────────
const MARKET_LABELS = {
  player_points:                    "Pontos",
  player_rebounds:                  "Ressaltos",
  player_assists:                   "Assistências",
  player_threes:                    "Triplos",
  player_blocks:                    "Bloqueios",
  player_steals:                    "Roubos de bola",
  player_turnovers:                 "Erros",
  player_points_rebounds_assists:   "Pts+Reb+Ast",
  player_points_rebounds:           "Pts+Reb",
  player_points_assists:            "Pts+Ast",
  player_rebounds_assists:          "Reb+Ast",
};

const HELP = `<b>🏀 TripleThreat AI Picks — comandos</b>

/start — registar e ver as picks do dia
/picks — últimas picks (ou histórico recente)
/stats — win rate e unidades históricas
/config — configuração actual
/setev &lt;num&gt; — EV mínimo (ex: /setev 0.05 = 5%)
/setoddsmin &lt;num&gt; — odd mínima (ex: /setoddsmin 1.5)
/setoddsmax &lt;num&gt; — odd máxima (ex: /setoddsmax 3.0)
/setbankroll &lt;num&gt; — bankroll total (ex: /setbankroll 500 → stakes com Kelly)
/stop — parar de receber picks
/help — esta mensagem`;

// ─────────────────────────────────────────────────────────────
// Analytics & Logging
// ─────────────────────────────────────────────────────────────

async function logEvent(eventType, data, env) {
  const kv = env.ANALYTICS;
  if (!kv) return;
  const timestamp = new Date().toISOString();
  const key = `event:${timestamp}:${Math.random()}`;
  const event = { type: eventType, data, timestamp };
  try {
    await kv.put(key, JSON.stringify(event), { expirationTtl: 86400 }); // 1 day
  } catch (e) {
    console.error("Analytics log error:", e);
  }
}

async function getDashboardStats(env) {
  const kv = env.ANALYTICS;
  if (!kv) return {};
  const settings = await getSettings(env);
  const history = await fetchHistory();

  const now = Date.now();
  const today = new Date().toISOString().slice(0, 10);

  // Count events by type
  const list = await kv.list({ prefix: "event:" });
  const events = [];
  for (const { name } of list.keys || []) {
    try {
      const data = await kv.get(name);
      if (data) events.push(JSON.parse(data));
    } catch (e) {}
  }

  // Stats
  const totalUsers = (settings.chat_ids || []).length;
  const totalUserJoins = events.filter(e => e.type === "user_join").length;
  const totalUserLeaves = events.filter(e => e.type === "user_leave").length;
  const totalPicksSent = events.filter(e => e.type === "pick_sent").length;
  const commandCounts = {};
  events.filter(e => e.type === "command").forEach(e => {
    const cmd = e.data?.command || "unknown";
    commandCounts[cmd] = (commandCounts[cmd] || 0) + 1;
  });

  // Daily stats (last 30 days)
  const dailyStats = {};
  for (let i = 29; i >= 0; i--) {
    const date = new Date(now - i * 86400000).toISOString().slice(0, 10);
    const dayEvents = events.filter(e => e.timestamp.slice(0, 10) === date);
    dailyStats[date] = {
      joins: dayEvents.filter(e => e.type === "user_join").length,
      leaves: dayEvents.filter(e => e.type === "user_leave").length,
      picks: dayEvents.filter(e => e.type === "pick_sent").length,
      commands: dayEvents.filter(e => e.type === "command").length,
    };
  }

  // Picks performance
  const picks = (history?.picks || []).filter(p => p.result);
  const wins = picks.filter(p => p.result === "WIN").length;
  const losses = picks.filter(p => p.result === "LOSS").length;
  const pushes = picks.filter(p => p.result === "PUSH").length;
  const units = picks.reduce((sum, p) => {
    if (p.result === "WIN") return sum + (p.decimal_odds - 1);
    if (p.result === "LOSS") return sum - 1;
    return sum;
  }, 0);
  const wr = (wins + losses) ? (wins / (wins + losses) * 100) : 0;

  // Recent users (from user_join events, sorted by timestamp descending)
  const userJoinEvents = events.filter(e => e.type === "user_join");
  const recentUsers = userJoinEvents
    .sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp))
    .slice(0, 10)
    .map(e => ({
      chatId: e.data?.chatId,
      username: e.data?.username || "unknown",
      languageCode: e.data?.languageCode || "unknown",
      timestamp: e.timestamp,
    }));

  return {
    totalUsers,
    totalUserJoins,
    totalUserLeaves,
    totalPicksSent,
    commandCounts,
    dailyStats,
    picks: { total: picks.length, wins, losses, pushes, wr, units },
    recentUsers,
    timestamp: new Date().toISOString(),
  };
}

// ─────────────────────────────────────────────────────────────
// /start Message Queue (KV-based, 15s between messages)
// ─────────────────────────────────────────────────────────────

async function enqueueStart(chatId, env) {
  const queue = env.START_QUEUE;
  if (!queue) {
    console.error("START_QUEUE KV not configured");
    return;
  }
  const key = `start:${chatId}:${Date.now()}`;
  const task = {
    chatId,
    stage: 1,  // 1=welcome, 2=bankroll, 3=help, 4=picks
    createdAt: Date.now(),
  };
  await queue.put(key, JSON.stringify(task), { expirationTtl: 600 }); // 10min expiry
}

async function processStartQueue(env) {
  const queue = env.START_QUEUE;
  if (!queue) return;

  const keys = await queue.list();
  for (const { name } of keys.keys || []) {
    if (!name.startsWith("start:")) continue;

    const data = await queue.get(name);
    if (!data) continue;

    const task = JSON.parse(data);
    const now = Date.now();
    const elapsed = now - task.createdAt;
    const stage = task.stage || 1;
    const settings = await getSettings(env);

    // Each stage is 15s apart
    const stageTime = (stage - 1) * 15000;
    if (elapsed < stageTime) continue; // Not yet time

    const chatId = task.chatId;

    try {
      switch (stage) {
        case 1:
          await tgSend(chatId,
            `✅ <b>Bem-vindo ao TripleThreat AI Picks!</b>\n\n` +
            `🏀 Recebes picks diárias de NBA Props com base num modelo estatístico.\n` +
            `📬 As picks chegam às <b>14h Lisboa</b>, espaçadas ao longo do dia.\n` +
            `📊 Resultados graduados às <b>12h do dia seguinte</b>.`, env);
          task.stage = 2;
          break;

        case 2:
          await tgSend(chatId,
            `💼 <b>IMPORTANTE: Define a tua bankroll!</b>\n\n` +
            `Cada pick inclui a <b>stake recomendada</b> baseada em Kelly Criterion.\n` +
            `Isto é essencial para proteger o teu bankroll e maximizar lucros.\n\n` +
            `<b>Usa este comando:</b>\n` +
            `<code>/setbankroll 500</code>\n\n` +
            `<i>Substitui 500 pelo teu bankroll em euros.</i>`, env);
          task.stage = 3;
          break;

        case 3:
          await tgSend(chatId, HELP, env);
          task.stage = 4;
          break;

        case 4:
          await sendTodayOrHistory(chatId, env);
          // Done - delete from queue
          await queue.delete(name);
          continue;
      }

      // Update task with new stage
      await queue.put(name, JSON.stringify(task), { expirationTtl: 600 });
    } catch (e) {
      console.error(`Start queue error for ${chatId}:`, e);
      await queue.delete(name);
    }
  }
}

// ─────────────────────────────────────────────────────────────
// Dashboard endpoints
// ─────────────────────────────────────────────────────────────

async function serveDashboard(env) {
  const html = `<!DOCTYPE html>
<html lang="pt">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>TripleThreat AI Picks — Dashboard</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f5f7fa; color: #2c3e50; }
    .container { max-width: 1400px; margin: 0 auto; padding: 20px; }
    header { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 30px; border-radius: 10px; margin-bottom: 30px; box-shadow: 0 4px 15px rgba(0,0,0,0.1); }
    h1 { font-size: 28px; margin-bottom: 5px; }
    .subtitle { opacity: 0.9; font-size: 14px; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; margin-bottom: 30px; }
    .card { background: white; padding: 20px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.05); border-left: 4px solid #667eea; }
    .card h3 { font-size: 12px; text-transform: uppercase; color: #7f8c8d; margin-bottom: 10px; font-weight: 600; }
    .card .value { font-size: 32px; font-weight: bold; color: #2c3e50; }
    .card .delta { font-size: 12px; color: #27ae60; margin-top: 5px; }
    .charts { display: grid; grid-template-columns: repeat(auto-fit, minmax(500px, 1fr)); gap: 20px; margin-bottom: 30px; }
    .chart-container { background: white; padding: 20px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.05); }
    .chart-container h2 { font-size: 16px; margin-bottom: 15px; color: #2c3e50; }
    .table { background: white; padding: 20px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.05); }
    .table h2 { font-size: 16px; margin-bottom: 15px; color: #2c3e50; }
    table { width: 100%; border-collapse: collapse; }
    th { background: #f8f9fa; padding: 12px; text-align: left; font-size: 12px; font-weight: 600; color: #7f8c8d; border-bottom: 1px solid #ecf0f1; }
    td { padding: 12px; border-bottom: 1px solid #ecf0f1; }
    .loading { text-align: center; padding: 40px; color: #7f8c8d; }
    .error { background: #fee; color: #c00; padding: 15px; border-radius: 5px; margin-bottom: 20px; }
    .refresh { font-size: 12px; color: #7f8c8d; margin-top: 10px; }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>🏀 TripleThreat AI Picks Dashboard</h1>
      <p class="subtitle">Analytics & User Growth</p>
    </header>

    <div id="error" class="error" style="display:none;"></div>
    <div id="loading" class="loading">Carregando dados...</div>
    <div id="content" style="display:none;">
      <div class="grid" id="stats"></div>
      <div class="charts" id="charts"></div>
      <div class="table">
        <h2>Top 10 Usuários Recentes</h2>
        <table>
          <thead>
            <tr><th>Username</th><th>País (Language)</th><th>Chat ID</th><th>Data</th></tr>
          </thead>
          <tbody id="usersTable"></tbody>
        </table>
      </div>

      <div class="table">
        <h2>Comandos Mais Usados</h2>
        <table>
          <thead>
            <tr><th>Comando</th><th>Usos</th></tr>
          </thead>
          <tbody id="commandsTable"></tbody>
        </table>
        <div class="refresh">Auto-refresh a cada hora</div>
      </div>
    </div>
  </div>

  <script>
    const TOKEN = new URLSearchParams(window.location.search).get('token') || '';

    async function loadDashboard() {
      try {
        const res = await fetch(\`/api/dashboard/stats?token=\${TOKEN}\`);
        if (!res.ok) throw new Error('Unauthorized');
        const data = await res.json();

        renderStats(data);
        renderCharts(data);
        renderUsers(data);
        renderCommands(data);

        document.getElementById('loading').style.display = 'none';
        document.getElementById('content').style.display = 'block';
      } catch (e) {
        document.getElementById('loading').style.display = 'none';
        document.getElementById('error').style.display = 'block';
        document.getElementById('error').textContent = '❌ Erro: Token inválido ou dados não disponíveis';
      }
    }

    function renderStats(data) {
      const html = \`
        <div class="card">
          <h3>Total Users</h3>
          <div class="value">\${data.totalUsers}</div>
          <div class="delta">+\${data.totalUserJoins} joins, -\${data.totalUserLeaves} leaves</div>
        </div>
        <div class="card">
          <h3>Picks Enviadas</h3>
          <div class="value">\${data.totalPicksSent}</div>
        </div>
        <div class="card">
          <h3>Win Rate</h3>
          <div class="value">\${data.picks.wr.toFixed(1)}%</div>
          <div class="delta">\${data.picks.wins}W \${data.picks.losses}L \${data.picks.pushes}P</div>
        </div>
        <div class="card">
          <h3>Units</h3>
          <div class="value" style="color: \${data.picks.units >= 0 ? '#27ae60' : '#e74c3c'};">\${data.picks.units > 0 ? '+' : ''}\${data.picks.units.toFixed(2)}</div>
          <div class="delta">De \${data.picks.total} picks</div>
        </div>
      \`;
      document.getElementById('stats').innerHTML = html;
    }

    function renderCharts(data) {
      const dates = Object.keys(data.dailyStats).sort();
      const joins = dates.map(d => data.dailyStats[d].joins);
      const picks = dates.map(d => data.dailyStats[d].picks);

      const chartsHtml = \`
        <div class="chart-container">
          <h2>User Joins (últimos 30 dias)</h2>
          <canvas id="joinsChart"></canvas>
        </div>
        <div class="chart-container">
          <h2>Picks Enviadas (últimos 30 dias)</h2>
          <canvas id="picksChart"></canvas>
        </div>
      \`;
      document.getElementById('charts').innerHTML = chartsHtml;

      setTimeout(() => {
        new Chart(document.getElementById('joinsChart'), {
          type: 'line',
          data: { labels: dates, datasets: [{ label: 'Joins', data: joins, borderColor: '#667eea', backgroundColor: 'rgba(102,126,234,0.1)', tension: 0.4 }] },
          options: { responsive: true, maintainAspectRatio: true }
        });

        new Chart(document.getElementById('picksChart'), {
          type: 'bar',
          data: { labels: dates, datasets: [{ label: 'Picks', data: picks, backgroundColor: '#764ba2' }] },
          options: { responsive: true, maintainAspectRatio: true }
        });
      }, 100);
    }

    function renderUsers(data) {
      const users = (data.recentUsers || []).slice(0, 10);
      const countryMap = {
        'pt': '🇵🇹 Portugal', 'pt-BR': '🇧🇷 Brasil', 'pt-PT': '🇵🇹 Portugal',
        'en': '🇬🇧 UK', 'en-US': '🇺🇸 USA', 'es': '🇪🇸 Spain', 'fr': '🇫🇷 France',
        'de': '🇩🇪 Germany', 'it': '🇮🇹 Italy', 'unknown': '❓ Unknown'
      };
      const html = users.map(u => {
        const country = countryMap[u.languageCode] || countryMap['unknown'];
        const date = new Date(u.timestamp).toLocaleDateString('pt-PT');
        return \`<tr><td>@\${u.username || 'anon'}</td><td>\${country}</td><td>\${u.chatId}</td><td>\${date}</td></tr>\`;
      }).join('');
      document.getElementById('usersTable').innerHTML = html || '<tr><td colspan="4">Sem dados</td></tr>';
    }

    function renderCommands(data) {
      const cmds = Object.entries(data.commandCounts).sort((a,b) => b[1] - a[1]).slice(0, 10);
      const html = cmds.map(([cmd, count]) => \`<tr><td>/\${cmd}</td><td>\${count}</td></tr>\`).join('');
      document.getElementById('commandsTable').innerHTML = html || '<tr><td colspan="2">Sem dados</td></tr>';
    }

    loadDashboard();
    setInterval(loadDashboard, 3600000); // refresh a cada hora
  </script>
</body>
</html>`;

  return new Response(html, { headers: { "Content-Type": "text/html; charset=utf-8" } });
}

async function serveDashboardStats(request, env) {
  const url = new URL(request.url);
  const token = url.searchParams.get("token");

  if (!token || token !== env.DASHBOARD_TOKEN) {
    return new Response("Unauthorized", { status: 401 });
  }

  const stats = await getDashboardStats(env);
  return new Response(JSON.stringify(stats), { headers: { "Content-Type": "application/json" } });
}

// ─────────────────────────────────────────────────────────────
// Broadcast picks message formatter
// ─────────────────────────────────────────────────────────────

function formatPicksMessage(picks, title = null) {
  if (!picks || picks.length === 0) return null;

  const today = new Date().toISOString().slice(0, 10);
  const lines = [title || ("🏀 <b>TripleThreat AI Picks — " + today + "</b>"), ""];

  picks.forEach((p, i) => {
    const ev = ((p.ev || 0) * 100).toFixed(0);
    const market = MARKET_LABELS[p.market] || p.market.replace("player_", "");

    // Format game time (commence_time is ISO 8601 UTC)
    let gameTime = "";
    if (p.commence_time) {
      const dt = new Date(p.commence_time);
      const hh = String(dt.getUTCHours()).padStart(2, "0");
      const mm = String(dt.getUTCMinutes()).padStart(2, "0");
      gameTime = ` @ ${hh}:${mm} UTC`;
    }

    const away = p.away_team ? p.away_team.split(" ").pop() : "?";
    const home = p.home_team ? p.home_team.split(" ").pop() : "?";
    const matchup = `${away} @ ${home}${gameTime}`;

    lines.push(
      `${i+1}️⃣ <b>${p.player_name}</b> ${p.side} ${p.line} ${market}`,
      `   <i>${matchup}</i>`,
      `   Odd: ${p.decimal_odds.toFixed(2)} | EV: <b>+${ev}%</b>`,
      ""
    );
  });

  lines.push("");
  lines.push("➡️ <a href=\"https://matos-666.github.io/triplethreataipicks/\"><b>Ver análise completa</b></a>");
  lines.push("💰 <a href=\"" + AFFILIATE + "\"><b>Apostar agora</b></a>");

  return lines.join("\n");
}

async function broadcastPicksToUser(chatId, picks, env) {
  try {
    const message = formatPicksMessage(picks);
    if (message) await tgSend(chatId, message, env);
  } catch (e) {
    console.error(`Failed to send picks to ${chatId}:`, e);
  }
}

async function broadcastDailyPicksNewsletter(env) {
  try {
    const history = await fetchHistory();
    if (!history || !history.picks) return;

    const today = new Date().toISOString().slice(0, 10);
    const todayPicks = history.picks.filter(p => p.game_date === today);

    if (todayPicks.length === 0) return;

    const message = formatPicksMessage(todayPicks);
    if (!message) return;

    // Get chat IDs from settings
    const settings = await getSettings(env);
    const chatIds = settings.chat_ids || [];

    // Send to all subscribers
    let sent = 0, failed = 0;
    for (const chatId of chatIds) {
      try {
        await tgSend(chatId, message, env);
        sent++;
      } catch (e) {
        failed++;
        console.error(`Failed to send to ${chatId}:`, e);
      }
    }

    console.log(`Newsletter sent: ${sent}/${chatIds.length} (${failed} failed)`);
    await logEvent("newsletter_sent", { count: sent, failed }, env);
  } catch (e) {
    console.error("Broadcast newsletter error:", e);
  }
}

// ─────────────────────────────────────────────────────────────
// Entry point
// ─────────────────────────────────────────────────────────────

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;

    // Dashboard endpoints (GET only)
    if (request.method === "GET") {
      if (path === "/dashboard") {
        return serveDashboard(env);
      }
      if (path === "/api/dashboard/stats") {
        return serveDashboardStats(request, env);
      }
      return new Response("NBA Props Bot Webhook OK ✅", { status: 200 });
    }

    // Broadcast picks endpoint
    if (request.method === "POST" && path === "/broadcast-picks") {
      try {
        const body = await request.json();
        if (body.token === "broadcast") {
          ctx.waitUntil(broadcastDailyPicksNewsletter(env));
          return new Response(JSON.stringify({ ok: true }), { status: 200 });
        }
      } catch (e) {
        console.error("Broadcast error:", e);
      }
      return new Response(JSON.stringify({ ok: false }), { status: 400 });
    }

    // Telegram webhook (POST only)
    if (request.method !== "POST") {
      return new Response("NBA Props Bot Webhook OK ✅", { status: 200 });
    }
    try {
      const update = await request.json();
      const msg = update.message;
      if (!msg) return new Response("ok");

      const chatId = msg.chat.id;
      const text   = (msg.text || "").trim();
      if (!text.startsWith("/")) return new Response("ok");

      const parts = text.split(/\s+/);
      const cmd   = parts[0].toLowerCase().split("@")[0];
      const arg   = parts[1] || null;
      const username = msg.from?.username || "unknown";
      const languageCode = msg.from?.language_code || "unknown";

      // Run async without blocking the 200 OK to Telegram
      ctx.waitUntil(handleCommand(cmd, arg, chatId, env, { username, languageCode }));
    } catch (e) {
      console.error("Worker error:", e);
    }
    return new Response("ok");
  },
  async scheduled(event, env, ctx) {
    // Process /start message queue every 5 seconds
    await processStartQueue(env);
  },
};

// ─────────────────────────────────────────────────────────────
// Command dispatcher
// ─────────────────────────────────────────────────────────────

async function handleCommand(cmd, arg, chatId, env, userInfo = {}) {
  const settings = await getSettings(env);

  switch (cmd) {
    case "/start": {
      const isNewUser = !settings.chat_ids.includes(chatId);
      if (isNewUser) {
        settings.chat_ids.push(chatId);
        await saveSettings(settings, env);

        // Log user info from Telegram
        await logEvent("user_join", {
          chatId,
          username: userInfo.username || "unknown",
          languageCode: userInfo.languageCode || "unknown"
        }, env);
      }

      // Queue the /start message sequence (15s between each)
      await enqueueStart(chatId, env);
      await tgSend(chatId, "⏳ Bem-vindo! As mensagens chegam em breve...", env);

      // Send today's picks after 50 seconds (after welcome sequence)
      setTimeout(async () => {
        const history = await fetchHistory();
        if (history && history.picks) {
          const today = new Date().toISOString().slice(0, 10);
          const todayPicks = history.picks.filter(p => p.game_date === today);
          if (todayPicks.length > 0) {
            await broadcastPicksToUser(chatId, todayPicks, env);
          }
        }
      }, 50000);

      await logEvent("command", { command: "start", chatId }, env);
      break;
    }

    case "/stop": {
      if (settings.chat_ids.includes(chatId)) {
        settings.chat_ids = settings.chat_ids.filter(id => id !== chatId);
        await saveSettings(settings, env);
        await logEvent("user_leave", { chatId }, env);
      }
      await tgSend(chatId, "🔕 Removido da lista de envio.\nUsa /start para voltar a receber picks.", env);
      await logEvent("command", { command: "stop", chatId }, env);
      break;
    }

    case "/picks":
    case "/hoje": {
      await sendTodayOrHistory(chatId, env);
      break;
    }

    case "/stats": {
      await sendStats(chatId, env);
      break;
    }

    case "/config": {
      await tgSend(chatId, fmtConfig(settings), env);
      break;
    }

    case "/help": {
      await tgSend(chatId, HELP, env);
      break;
    }

    case "/setev": {
      if (!arg) { await tgSend(chatId, "Uso: /setev 0.05  (= EV mínimo 5%)", env); break; }
      settings.min_ev = parseFloat(arg);
      await saveSettings(settings, env);
      await tgSend(chatId, `✅ EV mínimo: <b>${(parseFloat(arg) * 100).toFixed(1)}%</b>`, env);
      break;
    }

    case "/setoddsmin": {
      if (!arg) { await tgSend(chatId, "Uso: /setoddsmin 1.5", env); break; }
      settings.min_odds = parseFloat(arg);
      await saveSettings(settings, env);
      await tgSend(chatId, `✅ Odd mínima: <b>${arg}</b>`, env);
      break;
    }

    case "/setoddsmax": {
      if (!arg) { await tgSend(chatId, "Uso: /setoddsmax 3.0", env); break; }
      settings.max_odds = parseFloat(arg);
      await saveSettings(settings, env);
      await tgSend(chatId, `✅ Odd máxima: <b>${arg}</b>`, env);
      break;
    }

    case "/setbankroll": {
      if (!arg) { await tgSend(chatId, "Uso: /setbankroll 500  (em euros)", env); break; }
      try {
        const br = parseFloat(arg);
        settings.bankroll = br;
        await saveSettings(settings, env);
        await tgSend(chatId,
          `✅ Bankroll guardada: <b>€${br.toFixed(2)}</b>\n\n` +
          `Agora cada pick vai mostrar a stake sugerida (Kelly 12.5%).`, env);
      } catch (e) {
        await tgSend(chatId, "❌ Valor inválido. Ex: /setbankroll 500", env);
      }
      break;
    }

    default:
      await tgSend(chatId, "Comando desconhecido. /help para ver todos.", env);
  }

  // Log all commands (except start/stop already logged)
  if (!["start", "stop"].includes(cmd)) {
    await logEvent("command", { command: cmd.replace("/", ""), chatId }, env);
  }
}

// ─────────────────────────────────────────────────────────────
// /picks and /hoje logic
// ─────────────────────────────────────────────────────────────

async function sendTodayOrHistory(chatId, env) {
  const data = await fetchHistory();
  if (!data) {
    await tgSend(chatId, "⚠️ Não foi possível carregar picks de momento. Tenta em breve.", env);
    return;
  }

  // Today = UTC date (same as the bot uses)
  const today = new Date().toISOString().slice(0, 10);
  const todayPicks = (data.picks || []).filter(p => p.game_date === today);

  if (todayPicks.length > 0) {
    const sent   = todayPicks.filter(p => p.sent_at).length;
    const unsent = todayPicks.length - sent;
    const top3   = todayPicks.slice(0, 3);

    let msg = `🏀 <b>Picks do dia — ${today}</b>\n`;
    msg    += `━━━━━━━━━━━━━━━━━━━━━\n`;
    msg    += `📬 <b>${todayPicks.length} picks</b> em fila · ✅ ${sent} enviadas\n\n`;
    msg    += `🔝 <b>Top picks por EV:</b>\n`;

    for (let i = 0; i < top3.length; i++) {
      const p  = top3[i];
      const ml = MARKET_LABELS[p.market] || p.market.replace("player_", "");
      const team = p.player_team ? ` (${p.player_team})` : "";
      msg += `${i + 1}. <b>${esc(p.player_name)}</b>${team} — ${p.side} ${p.line} ${ml}`;
      msg += ` · EV <b>${(p.ev * 100).toFixed(1)}%</b> @ ${p.decimal_odds.toFixed(2)}\n`;
    }

    msg += `\n⏳ As picks chegam espaçadas ao longo do dia.`;

    for (const chunk of splitMsg(msg)) await tgSend(chatId, chunk, env);
  } else {
    // Show last graded day
    const graded = (data.picks || []).filter(p => p.result);
    if (graded.length > 0) {
      graded.sort((a, b) => b.game_date.localeCompare(a.game_date));
      const lastDate  = graded[0].game_date;
      const lastPicks = graded.filter(p => p.game_date === lastDate);
      const msg       = fmtResults(lastPicks, lastDate);
      for (const chunk of splitMsg(msg)) await tgSend(chatId, chunk, env);
    } else {
      await tgSend(chatId,
        "📭 <b>Sem picks hoje ainda.</b>\n" +
        "As picks chegam às 14h Lisboa.\n" +
        "Usa /help para ver todos os comandos.", env);
    }
  }
}

async function sendStats(chatId, env) {
  const data = await fetchHistory();
  if (!data || !data.summary || !data.summary.total) {
    await tgSend(chatId,
      "📭 Sem picks graduadas ainda.\nOs resultados chegam às 12h Lisboa do dia seguinte.", env);
    return;
  }
  const s      = data.summary;
  const wins   = s.wins   || 0;
  const losses = s.losses || 0;
  const pushes = s.pushes || 0;
  const units  = s.units  || 0;
  const wr     = (wins + losses) ? (wins / (wins + losses) * 100) : 0;
  const emoji  = units >= 0 ? "🟢" : "🔴";

  await tgSend(chatId,
    `📊 <b>Histórico total</b>\n` +
    `─────────────────────\n` +
    `${emoji} ${wins}W · ${losses}L · ${pushes}P\n` +
    `🎯 Win rate: <b>${wr.toFixed(1)}%</b>\n` +
    `💰 Unidades: <b>${units >= 0 ? "+" : ""}${units.toFixed(2)}u</b>\n` +
    `📝 Total picks: ${s.total}\n` +
    `─────────────────────\n` +
    `<a href="https://matos-666.github.io/triplethreataipicks/">📊 Ver histórico completo de picks →</a>`, env);
}

// ─────────────────────────────────────────────────────────────
// Formatters
// ─────────────────────────────────────────────────────────────

function fmtResults(picks, date) {
  const wins   = picks.filter(p => p.result === "WIN").length;
  const losses = picks.filter(p => p.result === "LOSS").length;
  const pushes = picks.filter(p => p.result === "PUSH").length;
  const units  = picks.reduce((sum, p) =>
    sum + (p.result === "WIN" ? p.decimal_odds - 1 : p.result === "LOSS" ? -1 : 0), 0);
  const wr     = (wins + losses) ? (wins / (wins + losses) * 100) : 0;
  const profit = units >= 0 ? "🟢" : "🔴";
  const trend  = wr >= 55 ? "🔥" : wr >= 50 ? "📈" : "📉";

  let msg = `${trend} <b>Resultados — ${date}</b>\n`;
  msg    += `━━━━━━━━━━━━━━━━━━━━━\n`;
  msg    += `${profit} <b>${wins}W · ${losses}L${pushes ? ` · ${pushes}P` : ""}</b>`;
  msg    += `  |  <b>${units >= 0 ? "+" : ""}${units.toFixed(2)}u</b>\n`;
  msg    += `🎯 Win rate: <b>${wr.toFixed(0)}%</b>   📋 ${wins + losses + pushes} picks\n`;
  msg    += `━━━━━━━━━━━━━━━━━━━━━\n\n`;

  // Top 3 wins por odds mais altas
  const topWins = picks
    .filter(p => p.result === "WIN")
    .sort((a, b) => b.decimal_odds - a.decimal_odds)
    .slice(0, 3);

  if (topWins.length) {
    msg += `🏆 <b>Destaques do dia:</b>\n`;
    for (const p of topWins) {
      const ml = MARKET_LABELS[p.market] || p.market.replace("player_", "");
      const av = p.actual_value != null ? ` · resultado: <b>${p.actual_value}</b>` : "";
      msg += `   ✅ ${esc(p.player_name)} ${p.side} ${p.line} ${ml} @ <b>${p.decimal_odds.toFixed(2)}</b>${av}\n`;
    }
    msg += "\n";
  }

  msg += `<a href="https://matos-666.github.io/triplethreataipicks/">📊 Ver histórico completo de picks →</a>`;
  return msg;
}

function fmtConfig(settings) {
  const markets = (settings.markets || [])
    .map(m => `   • ${MARKET_LABELS[m] || m}`)
    .join("\n");
  const bankrollLine = settings.bankroll
    ? `💼 Bankroll: <b>€${settings.bankroll.toFixed(2)}</b>\n`
    : "";
  return (
    `⚙️ <b>Configuração actual</b>\n` +
    `─────────────────────\n` +
    `📈 EV mínimo: <b>${(settings.min_ev * 100).toFixed(1)}%</b>\n` +
    `🎰 Odds: <b>${settings.min_odds} — ${settings.max_odds}</b>\n` +
    `📐 Kelly fraction: <b>${(settings.kelly_fraction * 100).toFixed(1)}%</b>\n` +
    `${bankrollLine}` +
    `📋 Mercados:\n${markets}\n` +
    `─────────────────────\n` +
    `👥 Chats registados: ${(settings.chat_ids || []).length}`
  );
}

// ─────────────────────────────────────────────────────────────
// GitHub API — read/write settings.json
// ─────────────────────────────────────────────────────────────

async function getSettings(env) {
  try {
    const r    = await ghFetch("GET", `contents/settings.json`, null, env);
    const json = await r.json();
    const obj  = JSON.parse(atob(json.content.replace(/\n/g, "")));
    obj._sha   = json.sha;   // needed for the PUT update
    return obj;
  } catch {
    return {
      chat_ids: [], min_ev: 0.05, min_odds: 1.5, max_odds: 3.0,
      kelly_fraction: 0.25,
      markets: ["player_points", "player_rebounds", "player_assists", "player_threes"],
      _sha: null,
    };
  }
}

async function saveSettings(settings, env) {
  const sha = settings._sha;
  const obj = { ...settings };
  delete obj._sha;

  await ghFetch("PUT", `contents/settings.json`, {
    message: "bot: settings update",
    content: btoa(unescape(encodeURIComponent(JSON.stringify(obj, null, 2)))),
    sha,
    committer: { name: "nba-bot", email: "nba-bot@users.noreply.github.com" },
  }, env);
}

async function ghFetch(method, path, body, env) {
  const url = `https://api.github.com/repos/${REPO}/${path}`;
  const opts = {
    method,
    headers: {
      Authorization: `token ${env.GITHUB_TOKEN}`,
      "User-Agent":  "nba-props-bot-worker",
      Accept:        "application/vnd.github.v3+json",
      "Content-Type": "application/json",
    },
  };
  if (body) opts.body = JSON.stringify(body);
  return fetch(url, opts);
}

// ─────────────────────────────────────────────────────────────
// GitHub Pages history.json
// ─────────────────────────────────────────────────────────────

async function fetchHistory() {
  try {
    const r = await fetch(PAGES_URL, {
      headers: { "Cache-Control": "no-cache" },
      cf: { cacheTtl: 0 },
    });
    return await r.json();
  } catch {
    return null;
  }
}

// ─────────────────────────────────────────────────────────────
// Telegram API
// ─────────────────────────────────────────────────────────────

async function tgSend(chatId, text, env) {
  try {
    await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        chat_id: chatId,
        text,
        parse_mode: "HTML",
        disable_web_page_preview: true,
      }),
    });
  } catch (e) {
    console.error("tgSend error:", e);
  }
}

// ─────────────────────────────────────────────────────────────
// Utils
// ─────────────────────────────────────────────────────────────

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function esc(s) {
  return (s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function splitMsg(text, max = 3800) {
  if (text.length <= max) return [text];
  const result = [];
  let buf = "";
  for (const line of text.split("\n")) {
    if (buf.length + line.length + 1 > max) {
      if (buf) result.push(buf.trimEnd());
      buf = line + "\n";
    } else {
      buf += line + "\n";
    }
  }
  if (buf.trim()) result.push(buf.trimEnd());
  return result;
}

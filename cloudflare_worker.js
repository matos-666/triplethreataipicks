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
const AFFILIATE  = "https://track.affshares.com/visit/?bta=657658&nci=5687";

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
/stop — parar de receber picks
/help — esta mensagem`;

// ─────────────────────────────────────────────────────────────
// Entry point
// ─────────────────────────────────────────────────────────────

export default {
  async fetch(request, env, ctx) {
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

      // Run async without blocking the 200 OK to Telegram
      ctx.waitUntil(handleCommand(cmd, arg, chatId, env));
    } catch (e) {
      console.error("Worker error:", e);
    }
    return new Response("ok");
  },
};

// ─────────────────────────────────────────────────────────────
// Command dispatcher
// ─────────────────────────────────────────────────────────────

async function handleCommand(cmd, arg, chatId, env) {
  const settings = await getSettings(env);

  switch (cmd) {
    case "/start": {
      if (!settings.chat_ids.includes(chatId)) {
        settings.chat_ids.push(chatId);
        await saveSettings(settings, env);
      }
      await tgSend(chatId,
        `✅ <b>Bem-vindo ao TripleThreat AI Picks!</b>\n\n` +
        `🏀 Recebes picks diárias de NBA Props com base num modelo estatístico.\n` +
        `📬 As picks chegam às <b>14h Lisboa</b>, espaçadas ao longo do dia.\n` +
        `📊 Resultados graduados às <b>12h do dia seguinte</b>.\n\n` + HELP, env);
      // Pequena pausa antes de mostrar picks/resultados
      await sleep(1500);
      await sendTodayOrHistory(chatId, env);
      break;
    }

    case "/stop": {
      settings.chat_ids = settings.chat_ids.filter(id => id !== chatId);
      await saveSettings(settings, env);
      await tgSend(chatId, "🔕 Removido da lista de envio.\nUsa /start para voltar a receber picks.", env);
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

    default:
      await tgSend(chatId, "Comando desconhecido. /help para ver todos.", env);
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

    let msg = `🏀 <b>Picks de hoje — ${today}</b>\n`;
    msg    += `📬 ${todayPicks.length} pick(s)  |  ✅ Enviadas: ${sent}  |  ⏳ Em fila: ${unsent}\n\n`;
    msg    += `<b>Lista completa:</b>\n`;

    for (let i = 0; i < todayPicks.length; i++) {
      const p  = todayPicks[i];
      const ml = MARKET_LABELS[p.market] || p.market.replace("player_", "");
      const st = p.sent_at ? "✅" : "⏳";
      msg += `${st} ${i + 1}. <b>${esc(p.player_name)}</b> ${p.side} ${p.line} ${ml}`;
      msg += ` · EV <b>${(p.ev * 100).toFixed(1)}%</b> @ ${p.decimal_odds.toFixed(2)} (${p.bookmaker || ""})\n`;
    }

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
  return (
    `⚙️ <b>Configuração actual</b>\n` +
    `─────────────────────\n` +
    `📈 EV mínimo: <b>${(settings.min_ev * 100).toFixed(1)}%</b>\n` +
    `🎰 Odds: <b>${settings.min_odds} — ${settings.max_odds}</b>\n` +
    `📐 Kelly fraction: <b>${settings.kelly_fraction}</b>\n` +
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

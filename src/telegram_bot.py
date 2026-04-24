"""Telegram bot: command polling and message sending.

Picks são enviadas uma a uma de 15 em 15 minutos (via send_queue.py).
Este módulo trata de: formatação, polling de comandos, /start inteligente.
"""
from __future__ import annotations

import html
import logging
import os
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from typing import Any

import requests

from src import config, db

log = logging.getLogger(__name__)

TG_API = "https://api.telegram.org/bot{token}/{method}"
TG_MAX = 3800
AFFILIATE_URL = "https://track.affshares.com/visit/?bta=657658&nci=5687"

MARKET_LABELS = {
    "player_points": "Pontos",
    "player_rebounds": "Ressaltos",
    "player_assists": "Assistências",
    "player_threes": "Triplos",
    "player_blocks": "Bloqueios",
    "player_steals": "Roubos de bola",
    "player_turnovers": "Erros",
    "player_points_rebounds_assists": "Pts+Reb+Ast",
    "player_points_rebounds": "Pts+Reb",
    "player_points_assists": "Pts+Ast",
    "player_rebounds_assists": "Reb+Ast",
}

SIDE_LABELS = {"Over": "Acima de", "Under": "Abaixo de"}

# CTAs rotativos por mercado — mantém o link afiliado mas varia o copy.
CTA_BY_MARKET = {
    "player_points": [
        "🎯 Aposta nos pontos com as melhores odds →",
        "🔥 Melhor casa para apostas em pontos →",
        "💯 Garante a tua odd de pontos aqui →",
    ],
    "player_rebounds": [
        "🏀 Melhor casa para apostas em ressaltos →",
        "🎯 Aposta nos ressaltos com a melhor odd →",
        "📈 Sobe a odd dos ressaltos aqui →",
    ],
    "player_assists": [
        "🎯 Aposta nas assistências com as melhores odds →",
        "🤝 Melhor casa para apostas em assistências →",
        "🔝 Odd de assistências imbatível aqui →",
    ],
    "player_threes": [
        "🎯 Aposta já nos triplos com a melhor odd →",
        "🏹 Melhor casa para apostas em triplos →",
        "💫 Garante a tua odd de triplos aqui →",
    ],
    "player_blocks": [
        "🛡️ Melhor casa para apostas em bloqueios →",
        "🎯 Aposta nos bloqueios com as melhores odds →",
    ],
    "player_steals": [
        "🥷 Melhor casa para apostas em roubos de bola →",
        "🎯 Aposta nos roubos com a melhor odd →",
    ],
    "player_turnovers": [
        "🎯 Aposta nos turnovers com a melhor odd →",
        "🔁 Melhor casa para apostas em erros →",
    ],
    "player_points_rebounds_assists": [
        "🎯 Aposta no combo PRA com a melhor odd →",
        "🔥 Melhor casa para Pts+Reb+Ast →",
    ],
    "player_points_rebounds": [
        "🎯 Aposta Pts+Reb com as melhores odds →",
        "📊 Melhor casa para o combo Pts+Reb →",
    ],
    "player_points_assists": [
        "🎯 Aposta Pts+Ast com as melhores odds →",
        "📊 Melhor casa para o combo Pts+Ast →",
    ],
    "player_rebounds_assists": [
        "🎯 Aposta Reb+Ast com as melhores odds →",
        "📊 Melhor casa para o combo Reb+Ast →",
    ],
}
CTA_DEFAULT = "🎯 Aposta aqui com as melhores odds →"


def _cta_for(market: str, seed: str) -> str:
    opts = CTA_BY_MARKET.get(market) or [CTA_DEFAULT]
    # rotação determinística por (market + seed) para que picks diferentes rodem
    idx = abs(hash(seed)) % len(opts)
    return opts[idx]


def _fmt_lisboa_time(iso_utc: str) -> str:
    if not iso_utc:
        return ""
    try:
        s = iso_utc.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        lx = dt.astimezone(ZoneInfo("Europe/Lisbon"))
        return lx.strftime("%H:%M")
    except Exception:
        return ""

HELP = """<b>🏀 NBA Props Bot — comandos</b>

/start — registar e ver as picks do dia
/picks — últimas picks do dia (ou histórico recente)
/config — configuração actual
/setev &lt;num&gt; — EV mínimo (ex: /setev 0.05 → 5%)
/setoddsmin &lt;num&gt; — odd mínima (ex: /setoddsmin 1.5)
/setoddsmax &lt;num&gt; — odd máxima (ex: /setoddsmax 3.0)
/setmaxevents &lt;n&gt; — máx jogos/dia (poupa créditos API)
/setkelly &lt;num&gt; — fracção Kelly (ex: /setkelly 0.25)
/markets — mercados disponíveis
/addmarket &lt;key&gt; — adicionar mercado
/rmmarket &lt;key&gt; — remover mercado
/stats — win rate e unidades históricas
/stop — parar de receber picks
/help — esta mensagem"""


# ─────────────────────────────────────────────────────────────
# Telegram API low-level
# ─────────────────────────────────────────────────────────────

def _token() -> str:
    t = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not t:
        raise RuntimeError("TELEGRAM_BOT_TOKEN env var not set")
    return t


def _call(method: str, **params) -> dict:
    url = TG_API.format(token=_token(), method=method)
    r = requests.post(url, data=params, timeout=30)
    r.raise_for_status()
    return r.json()


def send(chat_id: int, text: str, parse_mode: str = "HTML") -> None:
    try:
        _call("sendMessage", chat_id=chat_id, text=text,
              parse_mode=parse_mode, disable_web_page_preview="true")
    except Exception as e:
        log.error("Telegram send failed to %s: %s", chat_id, e)


def broadcast(text: str) -> None:
    s = config.load()
    for cid in s.get("chat_ids", []):
        send(int(cid), text)


def _chunk_text(text: str) -> list[str]:
    if len(text) <= TG_MAX:
        return [text]
    chunks: list[str] = []
    buf = ""
    for line in text.split("\n"):
        if len(buf) + len(line) + 1 > TG_MAX:
            if buf:
                chunks.append(buf.rstrip())
            buf = line + "\n"
        else:
            buf += line + "\n"
    if buf.strip():
        chunks.append(buf.rstrip())
    return chunks


# ─────────────────────────────────────────────────────────────
# Pick card formatting  (one beautiful pick per message)
# ─────────────────────────────────────────────────────────────

def format_pick_card(pick: dict, index: int, total: int) -> str:
    market_label = MARKET_LABELS.get(pick["market"], pick["market"].replace("player_", "").replace("_", "+"))
    side_label = SIDE_LABELS.get(pick["side"], pick["side"])
    ev_pct = pick["ev"] * 100
    kelly_pct = (pick.get("kelly") or 0) * 100
    market_pct = pick["market_prob"] * 100
    model_pct = pick["model_prob"] * 100
    edge_pct = (pick["model_prob"] - pick["market_prob"]) * 100
    american = pick.get("american_odds", 0)
    american_str = f"+{american}" if american and american > 0 else str(american) if american else ""

    # EV bar (visual indicator)
    ev_bar = _ev_bar(ev_pct)

    team = html.escape(pick.get('player_team') or '')
    team_str = f" <i>({team})</i>" if team else ""
    tipoff = _fmt_lisboa_time(pick.get("commence_time") or "")
    tipoff_line = f"🕐 Início: <b>{tipoff} Lisboa</b>\n" if tipoff else ""

    cta = _cta_for(pick["market"], f"{pick.get('player_name','')}|{pick['market']}|{pick.get('line','')}")

    return (
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏀 <b>Pick {index}/{total}</b> · {pick.get('game_date','')}\n"
        f"🆚 {html.escape(pick.get('away_team',''))} @ {html.escape(pick.get('home_team',''))}\n"
        f"{tipoff_line}"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"\n"
        f"👤 <b>{html.escape(pick['player_name'])}</b>{team_str}\n"
        f"📋 <b>{side_label} {pick['line']} {market_label}</b>\n"
        f"💰 <a href=\"{AFFILIATE_URL}\"><b>Melhor odd disponível: {pick['decimal_odds']:.2f}</b>"
        + (f" ({american_str})" if american_str else "") + "</a>\n"
        f"\n"
        f"📊 <b>Análise do modelo</b> <i>({pick.get('n_games',0)} jogos)</i>\n"
        f"   Média histórica: <b>{pick.get('model_mean',0):.1f}</b> · Desvio: ±{pick.get('model_std',0):.1f}\n"
        f"   P(acertar): <b>{model_pct:.0f}%</b> · Casa diz: {market_pct:.0f}%\n"
        f"   Edge: <b>{edge_pct:+.1f} pp</b>\n"
        f"\n"
        f"💹 <b>EV: {ev_pct:+.1f}%</b>  {ev_bar}\n"
        f"📐 Kelly: <b>{kelly_pct:.1f}%</b> do bankroll\n"
        f"\n"
        f"<a href=\"{AFFILIATE_URL}\"><b>{cta}</b></a>\n"
        f"\n"
        f"<i>⚠️ Não considera lesões ou rotações de último minuto.</i>"
    )


def _ev_bar(ev_pct: float) -> str:
    filled = min(int(ev_pct / 5), 10)
    return "🟩" * filled + "⬜" * (10 - filled)


def format_daily_summary(picks: list[dict]) -> str:
    """Teaser sem revelar as picks — mantém suspense."""
    if not picks:
        return "🏀 <b>Sem picks hoje</b> acima do EV mínimo.\nTenta baixar o EV mínimo com /setev 0.03"
    today = picks[0].get("game_date", "hoje")
    best_ev = max(p["ev"] for p in picks) * 100
    markets = len({p["market"] for p in picks})
    return (
        f"🏀 <b>Picks do dia — {today}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📬 <b>{len(picks)} pick(s)</b> selecionadas · {markets} mercados\n"
        f"💹 Melhor EV do dia: <b>+{best_ev:.1f}%</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"⏳ A primeira pick chega daqui a <b>~10 min</b>.\n"
        f"⏱️ Depois recebes <b>uma a cada 10 min</b>, até todas serem reveladas.\n\n"
        f"<i>Fica atento 🔔</i>"
    )


def format_results_card(picks: list[dict], date: str) -> str:
    """Full results message after grading."""
    wins = sum(1 for p in picks if p.get("result") == "WIN")
    losses = sum(1 for p in picks if p.get("result") == "LOSS")
    pushes = sum(1 for p in picks if p.get("result") == "PUSH")
    units = sum(
        (p["decimal_odds"] - 1) if p.get("result") == "WIN"
        else (-1 if p.get("result") == "LOSS" else 0)
        for p in picks
    )
    total = wins + losses + pushes
    wr = wins / (wins + losses) * 100 if (wins + losses) else 0
    profit_emoji = "🟢" if units >= 0 else "🔴"

    lines = [
        f"📊 <b>Resultados — {date}</b>",
        f"─────────────────────",
        f"{profit_emoji} {wins}W · {losses}L · {pushes}P  |  <b>{units:+.2f}u</b>",
        f"🎯 Win rate: <b>{wr:.0f}%</b>  ({total} picks graduadas)",
        f"─────────────────────\n",
    ]
    for p in picks:
        emoji = {"WIN": "✅", "LOSS": "❌", "PUSH": "➖"}.get(p.get("result",""), "⬜")
        market_label = MARKET_LABELS.get(p["market"], p["market"].replace("player_",""))
        actual = p.get("actual_value")
        actual_str = f" → real: <b>{actual}</b>" if actual is not None else ""
        lines.append(
            f"{emoji} {html.escape(p['player_name'])} {p['side']} {p['line']} {market_label}"
            f" @ {p['decimal_odds']:.2f}{actual_str}"
        )
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# Send queue (called by send_queue.py every 15 min)
# ─────────────────────────────────────────────────────────────

def send_next_queued(batch: int = 1) -> bool:
    """Send up to `batch` unsent picks per invocation. Skips picks whose game already started."""
    now_utc = datetime.now(timezone.utc)
    today = now_utc.date().isoformat()
    # cutoff: não enviar se o jogo já começou (5 min de margem antes do tip-off)
    cutoff_iso = (now_utc + timedelta(minutes=5)).isoformat()[:19]
    s = config.load()
    chat_ids = s.get("chat_ids", [])
    if not chat_ids:
        log.warning("No chat_ids registered")
        return False

    sent_any = False
    with db.connect() as conn:
        unsent_all = db.unsent_picks_today(conn, today)
        # Filtra picks onde o jogo ainda não começou (ou sem commence_time).
        unsent = []
        for row in unsent_all:
            ct = (dict(row).get("commence_time") or "").replace("Z", "")
            if ct and ct <= cutoff_iso:
                # jogo já começou — marca como sent para não voltar a tentar
                db.mark_sent(conn, dict(row)["id"])
                log.info("Skipped pick id=%s (game already started: %s)", dict(row)["id"], ct)
                continue
            unsent.append(row)
        total_today = len(db.today_picks(conn, today))
        if not unsent:
            log.info("No unsent picks for %s", today)
            return False
        to_send = unsent[:batch]
        for offset, row in enumerate(to_send):
            pick = dict(row)
            index = total_today - len(unsent) + 1 + offset
            msg = format_pick_card(pick, index, total_today)
            for cid in chat_ids:
                send(int(cid), msg)
            db.mark_sent(conn, pick["id"])
            log.info("Sent pick %d/%d: %s %s %s", index, total_today,
                     pick["player_name"], pick["side"], pick["line"])
            sent_any = True
    return sent_any


# ─────────────────────────────────────────────────────────────
# Command polling
# ─────────────────────────────────────────────────────────────

def poll() -> bool:
    s = config.load()
    offset = int(s.get("last_update_id", 0)) + 1
    try:
        resp = _call("getUpdates", offset=offset, timeout=0, allowed_updates='["message"]')
    except Exception as e:
        log.error("getUpdates failed: %s", e)
        return False
    updates = resp.get("result", [])
    if not updates:
        return False

    changed = False
    for u in updates:
        s["last_update_id"] = max(s["last_update_id"], u["update_id"])
        msg = u.get("message") or {}
        text = (msg.get("text") or "").strip()
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")
        if not chat_id or not text:
            continue
        try:
            if _handle(text, int(chat_id), s):
                changed = True
        except Exception as e:
            log.exception("command failed: %s", e)
            send(int(chat_id), f"❌ Erro: {html.escape(str(e))}")
    config.save(s)
    return changed or bool(updates)


def _handle(text: str, chat_id: int, s: dict) -> bool:
    parts = text.split()
    cmd = parts[0].lower().split("@")[0]
    arg = parts[1] if len(parts) > 1 else None

    if cmd == "/start":
        if chat_id not in s["chat_ids"]:
            s["chat_ids"].append(chat_id)
        send(chat_id, f"✅ <b>Bem-vindo ao NBA Props Bot!</b>\n"
                      f"Vais receber picks diárias de <b>15 em 15 minutos</b> a partir das 14h Lisboa.\n\n"
                      + HELP)
        _send_today_or_history(chat_id)
        return True

    if cmd in ("/picks", "/hoje"):
        _send_today_or_history(chat_id)
        return False

    if cmd == "/help":
        send(chat_id, HELP)
        return False

    if cmd == "/stop":
        if chat_id in s["chat_ids"]:
            s["chat_ids"].remove(chat_id)
            send(chat_id, "🔕 Removido. Usa /start para voltar a receber picks.")
            return True
        send(chat_id, "Não estavas registado.")
        return False

    if cmd == "/config":
        send(chat_id, _fmt_config(s))
        return False

    if cmd == "/markets":
        send(chat_id,
             "<b>Mercados disponíveis:</b>\n" +
             "\n".join(f"• <code>{k}</code> — {v}" for k, v in MARKET_LABELS.items()))
        return False

    if cmd == "/setev" and arg:
        s["min_ev"] = float(arg)
        send(chat_id, f"✅ EV mínimo: <b>{float(arg)*100:.1f}%</b>")
        return True
    if cmd == "/setoddsmin" and arg:
        s["min_odds"] = float(arg)
        send(chat_id, f"✅ Odd mínima: <b>{arg}</b>")
        return True
    if cmd == "/setoddsmax" and arg:
        s["max_odds"] = float(arg)
        send(chat_id, f"✅ Odd máxima: <b>{arg}</b>")
        return True
    if cmd == "/setmaxevents" and arg:
        s["max_events_per_day"] = int(arg)
        send(chat_id, f"✅ Máx jogos/dia: <b>{arg}</b>")
        return True
    if cmd == "/setkelly" and arg:
        s["kelly_fraction"] = float(arg)
        send(chat_id, f"✅ Kelly fraction: <b>{arg}</b>")
        return True
    if cmd == "/addmarket" and arg:
        if arg not in config.SUPPORTED_MARKETS:
            send(chat_id, "❌ Mercado desconhecido. /markets para ver a lista.")
            return False
        if arg not in s["markets"]:
            s["markets"].append(arg)
            label = MARKET_LABELS.get(arg, arg)
            send(chat_id, f"✅ Adicionado: <b>{label}</b> (<code>{arg}</code>)")
            return True
        send(chat_id, "Já estava na lista.")
        return False
    if cmd == "/rmmarket" and arg:
        if arg in s["markets"]:
            s["markets"].remove(arg)
            send(chat_id, f"✅ Removido: <code>{arg}</code>")
            return True
        send(chat_id, "Não está na lista.")
        return False

    if cmd == "/stats":
        with db.connect() as conn:
            summ = db.summary(conn)
        if not summ or not summ.get("total"):
            send(chat_id, "📭 Sem picks graduadas ainda. Os resultados chegam às 12h Lisboa do dia seguinte.")
            return False
        wins = summ.get("wins") or 0
        losses = summ.get("losses") or 0
        pushes = summ.get("pushes") or 0
        total = summ.get("total") or 0
        units = summ.get("units") or 0
        wr = wins / (wins + losses) * 100 if (wins + losses) else 0
        profit_emoji = "🟢" if units >= 0 else "🔴"
        send(chat_id,
             f"📊 <b>Histórico total</b>\n"
             f"─────────────────────\n"
             f"{profit_emoji} {wins}W · {losses}L · {pushes}P\n"
             f"🎯 Win rate: <b>{wr:.1f}%</b>\n"
             f"💰 Unidades: <b>{units:+.2f}u</b>\n"
             f"📝 Total picks: {total}\n"
             f"─────────────────────\n"
             f"<i>Vê o histórico completo em:\nhttps://matos-666.github.io/triplethreataipicks/</i>")
        return False

    send(chat_id, "Comando desconhecido. /help para ver todos os comandos.")
    return False


def _send_today_or_history(chat_id: int) -> None:
    """Send today's picks summary or last graded results if no picks today."""
    today = datetime.now(timezone.utc).date().isoformat()
    with db.connect() as conn:
        today_p = db.today_picks(conn, today)
        if today_p:
            sent = sum(1 for p in today_p if p.get("sent_at"))
            unsent = len(today_p) - sent
            lines = [
                f"🏀 <b>Picks de hoje — {today}</b>",
                f"📬 {len(today_p)} picks  |  ✅ Enviadas: {sent}  |  ⏳ Em fila: {unsent}\n",
                f"<b>Todas as picks de hoje:</b>",
            ]
            for i, p in enumerate(today_p, 1):
                market_label = MARKET_LABELS.get(p["market"], p["market"].replace("player_",""))
                status = "✅" if p.get("sent_at") else "⏳"
                lines.append(
                    f"{status} {i}. <b>{html.escape(p['player_name'])}</b> {p['side']} {p['line']} "
                    f"{market_label} · EV <b>{p['ev']*100:+.1f}%</b> @ {p['decimal_odds']:.2f} ({p.get('bookmaker','')})"
                )
            send(chat_id, "\n".join(lines))
        else:
            last_date = db.last_graded_date(conn)
            if last_date:
                graded = [p for p in db.all_picks(conn, 200) if p.get("game_date") == last_date and p.get("result")]
                if graded:
                    msg = format_results_card(graded, last_date)
                    for chunk in _chunk_text(msg):
                        send(chat_id, chunk)
                    return
            send(chat_id,
                 "📭 <b>Sem picks hoje ainda.</b>\n"
                 "As picks chegam às 14h Lisboa. Usa /help para ver todos os comandos.")


def _fmt_config(s: dict) -> str:
    return (
        "⚙️ <b>Configuração actual</b>\n"
        f"─────────────────────\n"
        f"📈 EV mínimo: <b>{s['min_ev']*100:.1f}%</b>\n"
        f"🎰 Odds: <b>{s['min_odds']} — {s['max_odds']}</b>\n"
        f"🏟️ Máx jogos/dia: <b>{s['max_events_per_day']}</b>\n"
        f"📐 Kelly fraction: <b>{s['kelly_fraction']}</b>\n"
        f"📋 Mercados:\n" +
        "\n".join(f"   • {MARKET_LABELS.get(m, m)}" for m in s["markets"]) + "\n"
        f"─────────────────────\n"
        f"👥 Chats registados: {len(s['chat_ids'])}"
    )


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "poll"
    if cmd == "poll":
        changed = poll()
        print("changed" if changed else "no-changes")
    elif cmd == "send_next":
        sent = send_next_queued()
        print("sent" if sent else "nothing-to-send")
    else:
        print(f"unknown: {cmd}")
        sys.exit(1)

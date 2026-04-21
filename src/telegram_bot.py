"""Telegram bot: command polling and message sending.

We don't run a long-lived process. Instead, a GitHub Actions cron calls
`poll()` every ~5 min; it fetches new updates via getUpdates, applies any
settings commands, and commits the updated settings.json. Picks workflow calls
`send_picks()`.
"""
from __future__ import annotations

import html
import logging
import os
from typing import Any

import requests

from src import config

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"


def _token() -> str:
    t = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not t:
        raise RuntimeError("TELEGRAM_BOT_TOKEN env var not set")
    return t


def _call(method: str, **params) -> dict:
    url = API.format(token=_token(), method=method)
    r = requests.post(url, data=params, timeout=30)
    r.raise_for_status()
    return r.json()


def send(chat_id: int, text: str, parse_mode: str = "HTML", disable_preview: bool = True) -> None:
    try:
        _call(
            "sendMessage",
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
            disable_web_page_preview="true" if disable_preview else "false",
        )
    except Exception as e:
        log.error("Telegram send failed to %s: %s", chat_id, e)


def broadcast(text: str) -> None:
    s = config.load()
    for cid in s.get("chat_ids", []):
        send(int(cid), text)


TG_MAX = 3800  # stay under 4096 hard limit with headroom


def send_picks(picks: list[dict]) -> None:
    s = config.load()
    chat_ids = s.get("chat_ids", [])
    if not chat_ids:
        log.warning("No chat_ids registered; skipping broadcast")
        return
    if not picks:
        msg = "<b>NBA Props</b>\nSem picks hoje acima do EV mínimo."
        for cid in chat_ids:
            send(int(cid), msg)
        return
    chunks = _chunk_picks(picks)
    for cid in chat_ids:
        for i, chunk in enumerate(chunks):
            send(int(cid), chunk)


def _pick_line(p: dict) -> str:
    market = p["market"].replace("player_", "").replace("_", "+")
    ev_pct = p["ev"] * 100
    kelly_pct = (p.get("kelly") or 0) * 100
    return (
        f"• <b>{html.escape(p['player_name'])}</b> {p['side']} {p['line']} {market}\n"
        f"  @ {p['decimal_odds']:.2f} ({p['bookmaker']}) — "
        f"<b>EV {ev_pct:+.1f}%</b> | Kelly {kelly_pct:.1f}%\n"
        f"  <i>modelo: {p['model_mean']:.1f} ± {p['model_std']:.1f} ({p['n_games']} jogos) | p={p['model_prob']:.2f}</i>"
    )


def _chunk_picks(picks: list[dict]) -> list[str]:
    header = f"<b>🏀 NBA Props — {picks[0].get('game_date','hoje')}</b>\n<i>{len(picks)} pick(s) acima do EV mínimo</i>\n\n"
    footer = "\n<i>Apostas envolvem risco. Aposta com responsabilidade.</i>"
    chunks: list[str] = []
    buf = header
    first = True
    for p in picks:
        line = _pick_line(p) + "\n"
        if len(buf) + len(line) + len(footer) > TG_MAX:
            chunks.append(buf.rstrip() + (footer if not chunks else ""))
            buf = f"<b>(cont.)</b>\n"
        buf += line
        first = False
    if buf.strip():
        chunks.append(buf.rstrip() + (footer if len(chunks) == 0 else ""))
    return chunks


# ────────────────────────────────────────────────────────────────────
# Command handling
# ────────────────────────────────────────────────────────────────────

HELP = """<b>🏀 NBA Props Bot — comandos</b>

/start — registar este chat para receber picks
/config — mostrar configuração actual
/setev &lt;num&gt; — definir EV mínimo (ex: 0.05 = 5%)
/setoddsmin &lt;num&gt; — odd decimal mínima (ex: 1.5)
/setoddsmax &lt;num&gt; — odd decimal máxima (ex: 3.0)
/setmaxevents &lt;n&gt; — máx jogos a analisar/dia (controla uso API)
/setkelly &lt;num&gt; — fracção Kelly (ex: 0.25)
/markets — listar mercados suportados
/addmarket &lt;key&gt; — adicionar mercado
/rmmarket &lt;key&gt; — remover mercado
/stats — estatísticas históricas
/stop — remover este chat
/help — mostrar esta mensagem
"""


def poll() -> bool:
    """Fetch and process pending updates. Returns True if settings changed."""
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
    return changed or updates


def _handle(text: str, chat_id: int, s: dict) -> bool:
    parts = text.split()
    cmd = parts[0].lower().split("@")[0]
    arg = parts[1] if len(parts) > 1 else None

    def ok(msg: str):
        send(chat_id, msg)

    if cmd == "/start":
        if chat_id not in s["chat_ids"]:
            s["chat_ids"].append(chat_id)
        ok(f"✅ Registado (chat_id={chat_id}). Vais receber picks diárias.\n\n{HELP}")
        return True
    if cmd == "/help":
        ok(HELP)
        return False
    if cmd == "/stop":
        if chat_id in s["chat_ids"]:
            s["chat_ids"].remove(chat_id)
            ok("Removido. Não irás receber mais picks. /start para voltar.")
            return True
        ok("Não estavas registado.")
        return False
    if cmd == "/config":
        ok(_fmt_config(s))
        return False
    if cmd == "/markets":
        ok("<b>Mercados suportados:</b>\n" + "\n".join(f"• <code>{m}</code>" for m in config.SUPPORTED_MARKETS))
        return False
    if cmd == "/setev" and arg:
        s["min_ev"] = float(arg)
        ok(f"✅ EV mínimo: {s['min_ev']}")
        return True
    if cmd == "/setoddsmin" and arg:
        s["min_odds"] = float(arg)
        ok(f"✅ Odd mínima: {s['min_odds']}")
        return True
    if cmd == "/setoddsmax" and arg:
        s["max_odds"] = float(arg)
        ok(f"✅ Odd máxima: {s['max_odds']}")
        return True
    if cmd == "/setmaxevents" and arg:
        s["max_events_per_day"] = int(arg)
        ok(f"✅ Máx jogos/dia: {s['max_events_per_day']}")
        return True
    if cmd == "/setkelly" and arg:
        s["kelly_fraction"] = float(arg)
        ok(f"✅ Kelly fraction: {s['kelly_fraction']}")
        return True
    if cmd == "/addmarket" and arg:
        if arg not in config.SUPPORTED_MARKETS:
            ok(f"❌ Mercado desconhecido. /markets para ver a lista.")
            return False
        if arg not in s["markets"]:
            s["markets"].append(arg)
            ok(f"✅ Adicionado: {arg}")
            return True
        ok("Já estava na lista.")
        return False
    if cmd == "/rmmarket" and arg:
        if arg in s["markets"]:
            s["markets"].remove(arg)
            ok(f"✅ Removido: {arg}")
            return True
        ok("Não está na lista.")
        return False
    if cmd == "/stats":
        from src import db
        with db.connect() as conn:
            summ = db.summary(conn)
        if not summ or not summ.get("total"):
            ok("Sem picks graduadas ainda.")
            return False
        wins = summ.get("wins") or 0
        losses = summ.get("losses") or 0
        pushes = summ.get("pushes") or 0
        total = summ.get("total") or 0
        units = summ.get("units") or 0
        wr = wins / (wins + losses) * 100 if (wins + losses) else 0
        ok(
            f"<b>📊 Histórico</b>\n"
            f"Picks graduadas: {total}\n"
            f"Wins: {wins} | Losses: {losses} | Pushes: {pushes}\n"
            f"Win rate: {wr:.1f}%\n"
            f"Unidades: {units:+.2f}"
        )
        return False

    ok("Comando desconhecido. /help")
    return False


def _fmt_config(s: dict) -> str:
    return (
        "<b>⚙️ Configuração</b>\n"
        f"EV mínimo: <code>{s['min_ev']}</code>\n"
        f"Odds: <code>{s['min_odds']}–{s['max_odds']}</code>\n"
        f"Max jogos/dia: <code>{s['max_events_per_day']}</code>\n"
        f"Kelly fraction: <code>{s['kelly_fraction']}</code>\n"
        f"Mercados:\n" + "\n".join(f"  • <code>{m}</code>" for m in s["markets"]) + "\n"
        f"Chats registados: {len(s['chat_ids'])}"
    )


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "poll"
    if cmd == "poll":
        changed = poll()
        print("changed" if changed else "no-changes")
    else:
        print(f"unknown command: {cmd}")
        sys.exit(1)

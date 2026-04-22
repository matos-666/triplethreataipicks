"""Daily pipeline: fetch odds → match players → compute EV → filter → store → broadcast."""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from src import config, db, model, odds, stats, telegram_bot

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent
HISTORY_JSON = ROOT / "pages" / "history.json"


def run() -> int:
    s = config.load()
    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        raise RuntimeError("ODDS_API_KEY not set")

    markets = s["markets"]
    if not markets:
        log.warning("No markets configured; nothing to do")
        telegram_bot.broadcast("⚠️ Sem mercados configurados. Usa /addmarket.")
        _write_history()
        return 0

    log.info("Fetching events...")
    events = odds.fetch_events(api_key)
    today_utc = datetime.now(timezone.utc).date().isoformat()
    todays = [e for e in events if e["commence_time"][:10] == today_utc]
    if not todays:
        # Some games cross midnight UTC — also include next-day within 24h
        todays = events
    todays = todays[: int(s["max_events_per_day"])]
    log.info("Processing %d event(s)", len(todays))

    picks: list[dict] = []
    with db.connect() as conn:
        for ev_raw in todays:
            try:
                raw_odds = odds.fetch_event_odds(
                    api_key,
                    ev_raw["id"],
                    markets=markets,
                    regions=s.get("regions", "us"),
                    bookmakers=s.get("bookmakers") or None,
                )
            except Exception as e:
                log.warning("odds fetch failed for %s: %s", ev_raw["id"], e)
                continue
            event = odds.parse_event(raw_odds)
            picks.extend(_process_event(event, s, conn))
            time.sleep(1)  # gentle rate limit
    picks.sort(key=lambda p: p["ev"], reverse=True)

    _write_history()
    log.info("Found %d qualifying picks", len(picks))
    # Envia apenas o resumo — as picks individuais saem de 15 em 15 min via send_queue
    s2 = config.load()
    for cid in s2.get("chat_ids", []):
        telegram_bot.send(int(cid), telegram_bot.format_daily_summary(picks))
    return len(picks)


def _process_event(event: odds.OddsEvent, settings: dict, conn) -> list[dict]:
    results: list[dict] = []
    game_date = event.commence_time[:10]
    for market, outcomes in event.outcomes_by_market.items():
        if market not in settings["markets"]:
            continue
        stat_cols = stats.MARKET_TO_STAT.get(market)
        if not stat_cols:
            continue
        dist = "poisson" if market in stats.POISSON_MARKETS else "normal"
        player_lines = odds.list_player_lines(outcomes)
        for player, line in player_lines:
            pid = stats.find_player_id(player)
            if not pid:
                log.debug("no player id for %s", player)
                continue
            rec = stats.fetch_player_recent(pid, n=int(settings["lookback_games"]))
            if not rec or len(rec.games) < int(settings["min_games_history"]):
                continue
            values = rec.values(stat_cols, int(settings["lookback_games"]))
            mo = model.fit_and_predict(values, line, distribution=dist)
            over, under = odds.best_pair(outcomes, player, line)
            for o, prob in ((over, mo.prob_over), (under, mo.prob_under)):
                if o is None:
                    continue
                if o.decimal < settings["min_odds"] or o.decimal > settings["max_odds"]:
                    continue
                e = model.ev(prob, o.decimal)
                if e < settings["min_ev"]:
                    continue
                pick = {
                    "game_date": game_date,
                    "event_id": event.event_id,
                    "home_team": event.home_team,
                    "away_team": event.away_team,
                    "player_name": player,
                    "player_id": pid,
                    "market": market,
                    "line": line,
                    "side": "Over" if o is over else "Under",
                    "bookmaker": o.bookmaker,
                    "decimal_odds": o.decimal,
                    "american_odds": o.american,
                    "model_prob": round(prob, 4),
                    "market_prob": round(model.implied_prob(o.decimal), 4),
                    "ev": round(e, 4),
                    "kelly": round(model.kelly(prob, o.decimal, settings["kelly_fraction"]), 4),
                    "model_mean": round(mo.mean, 2),
                    "model_std": round(mo.std, 2),
                    "n_games": mo.n,
                }
                db.insert_pick(conn, pick)
                results.append(pick)
    return results


def _write_history() -> None:
    """Dump denormalized picks to JSON so the static HTML page can render."""
    HISTORY_JSON.parent.mkdir(parents=True, exist_ok=True)
    with db.connect() as conn:
        picks = db.all_picks(conn, limit=1000)
        summ = db.summary(conn)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "summary": summ,
        "picks": picks,
    }
    with open(HISTORY_JSON, "w") as f:
        json.dump(payload, f, indent=2, default=str)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        n = run()
        print(f"picks={n}")
    except Exception as e:
        log.exception("pipeline failed")
        try:
            telegram_bot.broadcast(f"⚠️ Erro no pipeline diário: {e}")
        except Exception:
            pass
        sys.exit(1)

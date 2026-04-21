"""Grade yesterday's picks: fetch box scores, mark WIN/LOSS/PUSH."""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src import db, stats

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent
HISTORY_JSON = ROOT / "pages" / "history.json"


def run(date: str | None = None) -> int:
    if not date:
        date = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    log.info("Grading picks for %s", date)

    graded = 0
    with db.connect() as conn:
        pending = db.ungraded_picks(conn, date)
        if not pending:
            log.info("nothing to grade")
            _write_history()
            return 0

        # Group by event so we fetch box score once per game.
        by_event: dict[str, list] = {}
        for p in pending:
            by_event.setdefault(_event_key(p), []).append(p)

        for key, picks in by_event.items():
            first = picks[0]
            gid = stats.find_game_id_by_date_and_teams(date, first["home_team"], first["away_team"])
            if not gid:
                log.warning("no game id for %s vs %s on %s", first["home_team"], first["away_team"], date)
                continue
            box = stats.fetch_box_score(gid)
            if not box:
                continue
            for p in picks:
                pid = p["player_id"]
                if pid not in box:
                    continue
                cols = stats.MARKET_TO_STAT.get(p["market"], ())
                actual = sum(float(box[pid].get(c) or 0) for c in cols)
                line = float(p["line"])
                if actual == line:
                    result = "PUSH"
                elif (p["side"] == "Over" and actual > line) or (p["side"] == "Under" and actual < line):
                    result = "WIN"
                else:
                    result = "LOSS"
                db.grade_pick(conn, p["id"], result, actual)
                graded += 1
                log.info("%s %s %s %s -> %s (actual=%s)", p["player_name"], p["side"], line, p["market"], result, actual)

    _write_history()
    return graded


def _event_key(p) -> str:
    return f'{p["home_team"]}|{p["away_team"]}|{p["game_date"]}'


def _write_history() -> None:
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
    d = sys.argv[1] if len(sys.argv) > 1 else None
    n = run(d)
    print(f"graded={n}")

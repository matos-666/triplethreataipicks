"""NBA stats via nba_api. Per-player recent game logs with rolling mean/std.

Uses a local cache for player index to avoid hitting the API repeatedly. If
nba_api is blocked from the runner IP, pipeline degrades gracefully (player
skipped).
"""
from __future__ import annotations

import logging
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Iterable

import numpy as np

log = logging.getLogger(__name__)

# nba_api headers: emulate a browser so stats.nba.com doesn't 403 us.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}

# Map Odds API market keys to nba_api stat columns.
MARKET_TO_STAT: dict[str, tuple[str, ...]] = {
    "player_points": ("PTS",),
    "player_rebounds": ("REB",),
    "player_assists": ("AST",),
    "player_threes": ("FG3M",),
    "player_blocks": ("BLK",),
    "player_steals": ("STL",),
    "player_turnovers": ("TOV",),
    "player_points_rebounds_assists": ("PTS", "REB", "AST"),
    "player_points_rebounds": ("PTS", "REB"),
    "player_points_assists": ("PTS", "AST"),
    "player_rebounds_assists": ("REB", "AST"),
}

# Markets where a Poisson assumption fits better than Normal (low-count integer).
POISSON_MARKETS = {"player_threes", "player_blocks", "player_steals", "player_turnovers"}


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _normalize(name: str) -> str:
    return _strip_accents(name).lower().replace(".", "").replace("'", "").strip()


@lru_cache(maxsize=1)
def _player_index() -> dict[str, int]:
    from nba_api.stats.static import players as _players
    idx: dict[str, int] = {}
    for p in _players.get_players():
        if not p.get("is_active"):
            continue
        key = _normalize(p["full_name"])
        idx[key] = p["id"]
    return idx


def find_player_id(name: str) -> int | None:
    idx = _player_index()
    n = _normalize(name)
    if n in idx:
        return idx[n]
    # Try last-name fallback (e.g. "Shai Gilgeous-Alexander")
    for k, v in idx.items():
        if k.endswith(n.split()[-1]) and k.split()[0][0] == n.split()[0][0]:
            return v
    return None


def current_season() -> str:
    today = datetime.utcnow()
    y = today.year
    if today.month >= 10:
        return f"{y}-{str(y + 1)[-2:]}"
    return f"{y - 1}-{str(y)[-2:]}"


@dataclass
class PlayerRecent:
    player_id: int
    games: list[dict]     # most recent first

    def values(self, stat_cols: Iterable[str], n: int) -> np.ndarray:
        arr = []
        for g in self.games[:n]:
            arr.append(sum(float(g.get(c) or 0) for c in stat_cols))
        return np.array(arr, dtype=float)


def fetch_player_recent(player_id: int, n: int = 20, max_retries: int = 3) -> PlayerRecent | None:
    """Fetch last `n` regular-season games for a player."""
    from nba_api.stats.endpoints import playergamelog
    season = current_season()
    last_err = None
    for attempt in range(max_retries):
        try:
            gl = playergamelog.PlayerGameLog(
                player_id=player_id,
                season=season,
                headers=_HEADERS,
                timeout=30,
            )
            df = gl.get_data_frames()[0]
            if df.empty:
                # Try previous season as fallback (off-season or rookie)
                prev = f"{int(season[:4]) - 1}-{season[:4][-2:]}"
                gl = playergamelog.PlayerGameLog(
                    player_id=player_id, season=prev, headers=_HEADERS, timeout=30,
                )
                df = gl.get_data_frames()[0]
            if df.empty:
                return None
            games = df.head(n).to_dict(orient="records")
            return PlayerRecent(player_id=player_id, games=games)
        except Exception as e:
            last_err = e
            log.warning("nba_api retry %d for pid=%s: %s", attempt + 1, player_id, e)
            time.sleep(2 + attempt * 2)
    log.error("nba_api failed for pid=%s: %s", player_id, last_err)
    return None


def fetch_box_score(game_id: str, max_retries: int = 3) -> dict[int, dict] | None:
    """Return {player_id: {stat: value}} for a finished game."""
    from nba_api.stats.endpoints import boxscoretraditionalv2
    last_err = None
    for attempt in range(max_retries):
        try:
            bx = boxscoretraditionalv2.BoxScoreTraditionalV2(
                game_id=game_id, headers=_HEADERS, timeout=30,
            )
            df = bx.player_stats.get_data_frame()
            out: dict[int, dict] = {}
            for _, row in df.iterrows():
                out[int(row["PLAYER_ID"])] = {
                    "PTS": float(row.get("PTS") or 0),
                    "REB": float(row.get("REB") or 0),
                    "AST": float(row.get("AST") or 0),
                    "FG3M": float(row.get("FG3M") or 0),
                    "BLK": float(row.get("BLK") or 0),
                    "STL": float(row.get("STL") or 0),
                    "TOV": float(row.get("TO") or row.get("TOV") or 0),
                }
            return out
        except Exception as e:
            last_err = e
            time.sleep(2 + attempt * 2)
    log.error("boxscore failed for %s: %s", game_id, last_err)
    return None


def find_game_id_by_date_and_teams(date_iso: str, home_team: str, away_team: str) -> str | None:
    """Given a date (YYYY-MM-DD) and team names, return nba.com game id."""
    from nba_api.stats.endpoints import scoreboardv2
    try:
        sb = scoreboardv2.ScoreboardV2(game_date=date_iso, headers=_HEADERS, timeout=30)
        games = sb.game_header.get_data_frame()
        teams = sb.line_score.get_data_frame()
    except Exception as e:
        log.error("scoreboard failed for %s: %s", date_iso, e)
        return None
    for _, g in games.iterrows():
        gid = g["GAME_ID"]
        rows = teams[teams["GAME_ID"] == gid]
        names = " ".join(rows["TEAM_NAME"].astype(str).tolist()).lower()
        if home_team.split()[-1].lower() in names and away_team.split()[-1].lower() in names:
            return gid
    return None

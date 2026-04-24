"""NBA stats via ESPN public API.

ESPN's site.api.espn.com endpoints are unauthenticated and not blocked from
cloud IPs (unlike stats.nba.com). We hit three endpoints:

 - teams + rosters         → build name → athlete_id index (cached)
 - athletes/{id}/gamelog   → recent game stats per player
 - scoreboard + summary    → box scores for grading

Public module surface: MARKET_TO_STAT, POISSON_MARKETS, find_player_id,
fetch_player_recent, fetch_box_score, find_game_id_by_date_and_teams,
current_season, PlayerRecent.
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
import requests

log = logging.getLogger(__name__)

ESPN = "https://site.api.espn.com/apis"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

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

POISSON_MARKETS = {"player_threes", "player_blocks", "player_steals", "player_turnovers"}


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _normalize(name: str) -> str:
    return _strip_accents(name).lower().replace(".", "").replace("'", "").replace("-", " ").strip()


def _get(url: str, params: dict | None = None, timeout: int = 20, retries: int = 3) -> dict | None:
    last = None
    for i in range(retries):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
            time.sleep(1 + i)
    log.warning("ESPN GET failed %s: %s", url, last)
    return None


def current_season() -> str:
    today = datetime.utcnow()
    y = today.year
    if today.month >= 10:
        return f"{y}-{str(y + 1)[-2:]}"
    return f"{y - 1}-{str(y)[-2:]}"


# ────────────────────────────────────────────────────────────────────
# Player index
# ────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _player_index() -> tuple[dict[str, int], dict[int, str]]:
    """Build ({normalized_name: espn_id}, {espn_id: team_abbr}) from 30 rosters."""
    idx: dict[str, int] = {}
    teams_map: dict[int, str] = {}
    teams = _get(f"{ESPN}/site/v2/sports/basketball/nba/teams", params={"limit": 50})
    if not teams:
        return idx, teams_map
    team_list = teams["sports"][0]["leagues"][0]["teams"]
    for t in team_list:
        tid = t["team"]["id"]
        abbr = t["team"].get("abbreviation", "")
        roster = _get(f"{ESPN}/site/v2/sports/basketball/nba/teams/{tid}/roster")
        if not roster:
            continue
        for a in roster.get("athletes", []):
            aid = int(a["id"])
            teams_map[aid] = abbr
            names = [a.get("fullName"), a.get("displayName"), a.get("shortName")]
            for n in names:
                if n:
                    idx[_normalize(n)] = aid
    log.info("ESPN player index built: %d entries", len(idx))
    return idx, teams_map


def find_player_team(player_id: int) -> str:
    _, teams_map = _player_index()
    return teams_map.get(player_id, "")


def find_player_id(name: str) -> int | None:
    idx, _ = _player_index()
    n = _normalize(name)
    if n in idx:
        return idx[n]
    # Tolerant fallback: last-name + first-initial match
    parts = n.split()
    if len(parts) >= 2:
        first, last = parts[0], parts[-1]
        for k, v in idx.items():
            kp = k.split()
            if len(kp) >= 2 and kp[-1] == last and kp[0].startswith(first[0]):
                return v
    return None


# ────────────────────────────────────────────────────────────────────
# Gamelog
# ────────────────────────────────────────────────────────────────────

# ESPN top-level `names` order for box stats (per game in `events[].stats`):
#   0 minutes, 1 FG(m-a), 2 FG%, 3 3PT(m-a), 4 3P%, 5 FT(m-a), 6 FT%,
#   7 totalRebounds, 8 assists, 9 blocks, 10 steals, 11 fouls,
#   12 turnovers, 13 points
_STAT_IDX = {"REB": 7, "AST": 8, "BLK": 9, "STL": 10, "TOV": 12, "PTS": 13}
_MADE_ATT_IDX = {"FG3M": 3}  # "3-7" → 3


@dataclass
class PlayerRecent:
    player_id: int
    games: list[dict]   # newest first, {stat_key: value, game_date: iso}

    def values(self, stat_cols: Iterable[str], n: int) -> np.ndarray:
        arr = []
        for g in self.games[:n]:
            arr.append(sum(float(g.get(c) or 0) for c in stat_cols))
        return np.array(arr, dtype=float)


def _parse_stats_row(stats: list[str]) -> dict[str, float]:
    """Convert ESPN stats row (list of strings, possibly 'm-a') into dict."""
    out: dict[str, float] = {}
    for key, i in _STAT_IDX.items():
        try:
            out[key] = float(stats[i]) if stats[i] != "" else 0.0
        except (ValueError, IndexError):
            out[key] = 0.0
    for key, i in _MADE_ATT_IDX.items():
        try:
            v = stats[i]
            made = v.split("-")[0] if "-" in v else v
            out[key] = float(made) if made else 0.0
        except (ValueError, IndexError):
            out[key] = 0.0
    return out


def fetch_player_recent(player_id: int, n: int = 20) -> PlayerRecent | None:
    """Fetch last `n` regular/post-season games for an ESPN athlete id."""
    data = _get(f"{ESPN}/common/v3/sports/basketball/nba/athletes/{player_id}/gamelog")
    if not data:
        return None
    events_meta = data.get("events", {})
    games: list[dict] = []
    # Regular + Post season, skip preseason.
    for st in data.get("seasonTypes", []):
        name = (st.get("displayName") or "").lower()
        if "preseason" in name:
            continue
        for cat in st.get("categories", []):
            for ev in cat.get("events", []):
                eid = ev.get("eventId")
                meta = events_meta.get(eid, {})
                parsed = _parse_stats_row(ev.get("stats", []))
                parsed["game_date"] = meta.get("gameDate", "")
                parsed["event_id"] = eid
                games.append(parsed)
    if not games:
        return None
    # Sort newest first by ISO date string.
    games.sort(key=lambda g: g.get("game_date") or "", reverse=True)
    return PlayerRecent(player_id=player_id, games=games[: n * 2][:n])


# ────────────────────────────────────────────────────────────────────
# Box score lookup (for grading)
# ────────────────────────────────────────────────────────────────────

def find_game_id_by_date_and_teams(date_iso: str, home_team: str, away_team: str) -> str | None:
    """Return ESPN event id for the NBA game matching date + teams."""
    yyyymmdd = date_iso.replace("-", "")
    sb = _get(f"{ESPN}/site/v2/sports/basketball/nba/scoreboard", params={"dates": yyyymmdd})
    if not sb:
        return None
    target_home = _normalize(home_team).split()[-1]
    target_away = _normalize(away_team).split()[-1]
    for ev in sb.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        names = " ".join(
            _normalize(c.get("team", {}).get("displayName") or "")
            for c in comp.get("competitors", [])
        )
        if target_home in names and target_away in names:
            return ev["id"]
    return None


def fetch_box_score(game_id: str) -> dict[int, dict] | None:
    """Return {athlete_id: {stat: value}} for a finished game."""
    data = _get(
        f"{ESPN}/site/v2/sports/basketball/nba/summary",
        params={"event": game_id},
    )
    if not data:
        return None
    out: dict[int, dict] = {}
    boxscore = data.get("boxscore") or {}
    for team in boxscore.get("players", []):
        for statblock in team.get("statistics", []):
            keys = statblock.get("keys") or statblock.get("names") or []
            # ESPN sometimes uses identical ordering to top-level gamelog names.
            for a in statblock.get("athletes", []):
                ath = a.get("athlete") or {}
                try:
                    aid = int(ath.get("id"))
                except (TypeError, ValueError):
                    continue
                stats = a.get("stats") or []
                # Map by keys when present, fallback to index layout.
                row: dict[str, float] = {}
                if keys and len(keys) == len(stats):
                    key_to_val = dict(zip(keys, stats))
                    row["PTS"] = _f(key_to_val.get("points"))
                    row["REB"] = _f(key_to_val.get("totalRebounds") or key_to_val.get("rebounds"))
                    row["AST"] = _f(key_to_val.get("assists"))
                    row["BLK"] = _f(key_to_val.get("blocks"))
                    row["STL"] = _f(key_to_val.get("steals"))
                    row["TOV"] = _f(key_to_val.get("turnovers"))
                    threes = key_to_val.get("threePointFieldGoalsMade-threePointFieldGoalsAttempted", "")
                    row["FG3M"] = _f(str(threes).split("-")[0] if "-" in str(threes) else threes)
                else:
                    row = _parse_stats_row(stats)
                out[aid] = row
    return out if out else None


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0

"""Settings persistence. Single JSON file at repo root, edited via Telegram."""
import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = ROOT / "settings.json"

DEFAULTS: dict[str, Any] = {
    "min_ev": 0.05,
    "min_odds": 1.5,
    "max_odds": 3.0,
    "markets": ["player_points", "player_rebounds", "player_assists", "player_threes"],
    "max_events_per_day": 5,
    "kelly_fraction": 0.25,
    "regions": "us",
    "bookmakers": ["fanduel", "draftkings", "betmgm"],
    "chat_ids": [],
    "last_update_id": 0,
    "min_games_history": 5,
    "lookback_games": 10,
}

SUPPORTED_MARKETS = [
    "player_points",
    "player_rebounds",
    "player_assists",
    "player_threes",
    "player_blocks",
    "player_steals",
    "player_turnovers",
    "player_points_rebounds_assists",
    "player_points_rebounds",
    "player_points_assists",
    "player_rebounds_assists",
]


def load() -> dict:
    if not SETTINGS_PATH.exists():
        save(DEFAULTS.copy())
        return DEFAULTS.copy()
    with open(SETTINGS_PATH) as f:
        data = json.load(f)
    for k, v in DEFAULTS.items():
        data.setdefault(k, v)
    return data


def save(settings: dict) -> None:
    with open(SETTINGS_PATH, "w") as f:
        json.dump(settings, f, indent=2, sort_keys=True)
        f.write("\n")


def env(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key, default)

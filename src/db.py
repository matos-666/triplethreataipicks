"""SQLite storage for picks + results. Single file in data/history.db."""
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "history.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS picks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    game_date TEXT NOT NULL,
    event_id TEXT,
    home_team TEXT,
    away_team TEXT,
    player_name TEXT NOT NULL,
    player_id INTEGER,
    market TEXT NOT NULL,
    line REAL NOT NULL,
    side TEXT NOT NULL,
    bookmaker TEXT,
    decimal_odds REAL NOT NULL,
    american_odds INTEGER,
    model_prob REAL NOT NULL,
    market_prob REAL NOT NULL,
    ev REAL NOT NULL,
    kelly REAL,
    model_mean REAL,
    model_std REAL,
    n_games INTEGER,
    result TEXT,
    actual_value REAL,
    graded_at TEXT,
    UNIQUE(game_date, player_name, market, line, side, bookmaker)
);
CREATE INDEX IF NOT EXISTS idx_picks_date ON picks(game_date);
CREATE INDEX IF NOT EXISTS idx_picks_result ON picks(result);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def insert_pick(conn: sqlite3.Connection, pick: dict[str, Any]) -> int | None:
    pick = dict(pick)
    pick.setdefault("created_at", datetime.utcnow().isoformat(timespec="seconds") + "Z")
    cols = [
        "created_at", "game_date", "event_id", "home_team", "away_team",
        "player_name", "player_id", "market", "line", "side", "bookmaker",
        "decimal_odds", "american_odds", "model_prob", "market_prob", "ev",
        "kelly", "model_mean", "model_std", "n_games",
    ]
    values = [pick.get(c) for c in cols]
    placeholders = ",".join("?" for _ in cols)
    sql = f"INSERT OR IGNORE INTO picks ({','.join(cols)}) VALUES ({placeholders})"
    cur = conn.execute(sql, values)
    conn.commit()
    return cur.lastrowid if cur.rowcount else None


def ungraded_picks(conn: sqlite3.Connection, game_date: str) -> list[sqlite3.Row]:
    rows = conn.execute(
        "SELECT * FROM picks WHERE game_date = ? AND result IS NULL",
        (game_date,),
    ).fetchall()
    return list(rows)


def grade_pick(conn: sqlite3.Connection, pick_id: int, result: str, actual: float) -> None:
    conn.execute(
        "UPDATE picks SET result=?, actual_value=?, graded_at=? WHERE id=?",
        (result, actual, datetime.utcnow().isoformat(timespec="seconds") + "Z", pick_id),
    )
    conn.commit()


def all_picks(conn: sqlite3.Connection, limit: int = 500) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM picks ORDER BY game_date DESC, id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def summary(conn: sqlite3.Connection) -> dict:
    row = conn.execute("""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN result='LOSS' THEN 1 ELSE 0 END) AS losses,
            SUM(CASE WHEN result='PUSH' THEN 1 ELSE 0 END) AS pushes,
            SUM(CASE WHEN result='WIN' THEN (decimal_odds - 1) WHEN result='LOSS' THEN -1 ELSE 0 END) AS units
        FROM picks WHERE result IS NOT NULL
    """).fetchone()
    return dict(row) if row else {}

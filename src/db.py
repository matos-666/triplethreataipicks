"""SQLite storage for picks + results. Single file in data/history.db."""
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "history.db"

_TABLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS picks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    game_date TEXT NOT NULL,
    event_id TEXT,
    home_team TEXT,
    away_team TEXT,
    player_name TEXT NOT NULL,
    player_team TEXT,
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
    sent_at TEXT,
    UNIQUE(game_date, player_name, market, line, side, bookmaker)
);
"""

_INDEX_SCHEMA = """
CREATE INDEX IF NOT EXISTS idx_picks_date ON picks(game_date);
CREATE INDEX IF NOT EXISTS idx_picks_result ON picks(result);
CREATE INDEX IF NOT EXISTS idx_picks_sent ON picks(sent_at);
"""

_MIGRATIONS = [
    "ALTER TABLE picks ADD COLUMN sent_at TEXT",
    "ALTER TABLE picks ADD COLUMN player_team TEXT",
]


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Run table creation first (without indexes that depend on new columns).
    conn.executescript(_TABLE_SCHEMA)
    # Migrate old DBs — must happen before index creation.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(picks)").fetchall()}
    for migration in _MIGRATIONS:
        col = migration.split()[-2]  # e.g. "sent_at" or "player_team"
        if col not in cols:
            conn.execute(migration)
    conn.commit()
    # Now safe to create indexes (sent_at already exists).
    conn.executescript(_INDEX_SCHEMA)
    return conn


def insert_pick(conn: sqlite3.Connection, pick: dict[str, Any]) -> int | None:
    pick = dict(pick)
    pick.setdefault("created_at", datetime.utcnow().isoformat(timespec="seconds") + "Z")
    cols = [
        "created_at", "game_date", "event_id", "home_team", "away_team",
        "player_name", "player_team", "player_id", "market", "line", "side", "bookmaker",
        "decimal_odds", "american_odds", "model_prob", "market_prob", "ev",
        "kelly", "model_mean", "model_std", "n_games",
    ]
    values = [pick.get(c) for c in cols]
    placeholders = ",".join("?" for _ in cols)
    sql = f"INSERT OR IGNORE INTO picks ({','.join(cols)}) VALUES ({placeholders})"
    cur = conn.execute(sql, values)
    conn.commit()
    return cur.lastrowid if cur.rowcount else None


def unsent_picks_today(conn: sqlite3.Connection, game_date: str) -> list[sqlite3.Row]:
    """Picks from today that haven't been sent to Telegram yet, ordered by EV desc."""
    rows = conn.execute(
        "SELECT * FROM picks WHERE game_date = ? AND sent_at IS NULL ORDER BY ev DESC",
        (game_date,),
    ).fetchall()
    return list(rows)


def mark_sent(conn: sqlite3.Connection, pick_id: int) -> None:
    conn.execute(
        "UPDATE picks SET sent_at=? WHERE id=?",
        (datetime.utcnow().isoformat(timespec="seconds") + "Z", pick_id),
    )
    conn.commit()


def today_picks(conn: sqlite3.Connection, game_date: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM picks WHERE game_date = ? ORDER BY ev DESC",
        (game_date,),
    ).fetchall()
    return [dict(r) for r in rows]


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
        "SELECT * FROM picks ORDER BY game_date DESC, ev DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def last_graded_date(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT game_date FROM picks WHERE result IS NOT NULL ORDER BY game_date DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else None


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

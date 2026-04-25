"""The Odds API client. Free tier = 500 credits/month.

Quota math:
 - /sports/basketball_nba/events : 1 credit per call
 - /events/{id}/odds with markets=A,B regions=us : (#markets) credits per call
We keep calls minimal: one events call, then one odds call per event with all
selected markets batched.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Iterable

import requests

log = logging.getLogger(__name__)

BASE = "https://api.the-odds-api.com/v4"
SPORT = "basketball_nba"


@dataclass
class OddsOutcome:
    player: str
    side: str            # "Over" or "Under"
    line: float
    decimal: float
    american: int
    bookmaker: str


@dataclass
class OddsEvent:
    event_id: str
    commence_time: str
    home_team: str
    away_team: str
    outcomes_by_market: dict[str, list[OddsOutcome]]


def _get(url: str, params: dict, timeout: int = 30) -> requests.Response:
    r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    # Log API quota headers (the-odds-api returns these on every response)
    remaining = r.headers.get("x-requests-remaining")
    used = r.headers.get("x-requests-used")
    if remaining is not None or used is not None:
        log.info("Odds API quota — remaining: %s, used: %s", remaining, used)
    return r


def american_to_decimal(a: int) -> float:
    if a >= 100:
        return 1 + a / 100
    return 1 + 100 / abs(a)


def decimal_to_implied(d: float) -> float:
    return 1.0 / d


def fetch_events(api_key: str) -> list[dict]:
    url = f"{BASE}/sports/{SPORT}/events"
    r = _get(url, {"apiKey": api_key})
    return r.json()


def fetch_event_odds(
    api_key: str,
    event_id: str,
    markets: Iterable[str],
    regions: str = "us",
    bookmakers: Iterable[str] | None = None,
) -> dict:
    url = f"{BASE}/sports/{SPORT}/events/{event_id}/odds"
    params = {
        "apiKey": api_key,
        "regions": regions,
        "markets": ",".join(markets),
        "oddsFormat": "decimal",
    }
    if bookmakers:
        params["bookmakers"] = ",".join(bookmakers)
    r = _get(url, params)
    return r.json()


def parse_event(raw: dict) -> OddsEvent:
    out: dict[str, list[OddsOutcome]] = {}
    for book in raw.get("bookmakers", []):
        bk = book["key"]
        for market in book.get("markets", []):
            mkey = market["key"]
            bucket = out.setdefault(mkey, [])
            for o in market.get("outcomes", []):
                name = o.get("description") or o.get("name") or ""
                side = o.get("name", "")
                line = o.get("point")
                price = o.get("price")
                if line is None or price is None:
                    continue
                dec = float(price)
                american = int(round((dec - 1) * 100)) if dec >= 2 else int(round(-100 / (dec - 1)))
                bucket.append(OddsOutcome(
                    player=name,
                    side=side,
                    line=float(line),
                    decimal=dec,
                    american=american,
                    bookmaker=bk,
                ))
    return OddsEvent(
        event_id=raw["id"],
        commence_time=raw["commence_time"],
        home_team=raw["home_team"],
        away_team=raw["away_team"],
        outcomes_by_market=out,
    )


def best_pair(outcomes: list[OddsOutcome], player: str, line: float) -> tuple[OddsOutcome | None, OddsOutcome | None]:
    """Return (best Over, best Under) by highest decimal odds for the given player+line."""
    over, under = None, None
    for o in outcomes:
        if o.player != player or o.line != line:
            continue
        if o.side.lower().startswith("over"):
            if over is None or o.decimal > over.decimal:
                over = o
        elif o.side.lower().startswith("under"):
            if under is None or o.decimal > under.decimal:
                under = o
    return over, under


def list_player_lines(outcomes: list[OddsOutcome]) -> list[tuple[str, float]]:
    seen = set()
    out = []
    for o in outcomes:
        key = (o.player, o.line)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out

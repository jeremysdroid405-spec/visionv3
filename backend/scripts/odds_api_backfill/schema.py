"""
Collection bootstrap for `historical_odds_full`.

Schema (one row per (event, market, side, line, bookmaker, snapshot_time)):
    event_id           str       Odds API event id
    sport_key          str       'basketball_nba'
    commence_time      datetime  Game tipoff UTC
    game_date          str       'YYYY-MM-DD' (UTC)
    home_team          str
    away_team          str

    snapshot_time      datetime  When the odds snapshot was captured
    snapshot_label     str       'open' | 'pregame_-1h' | 'pregame_-10m'

    bookmaker          str       'draftkings' | 'fanduel' | …
    region             str       'us' (single region for now)
    market_key         str       'player_points' | 'player_points_alternate' | …
    is_alternate       bool      derived from `_alternate` suffix
    is_combo           bool      derived from market name (PRA / P+R / P+A / R+A / DD)
    stat_family        str       canonical: 'PTS','REB','AST','THREES','PRA',
                                            'PTS_REB','PTS_AST','REB_AST',
                                            'BLK','STL','DOUBLE_DOUBLE'

    player             str       canonical lower-case name
    line               float
    side               str       'OVER' | 'UNDER' | 'YES' | 'NO'
    odds_american      int

    source             str       'odds_api_v4_historical'
    ingested_at        datetime  UTC

Unique compound index: (event_id, market_key, snapshot_time, bookmaker,
                         player, line, side)
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from pymongo import ASCENDING, DESCENDING

logger = logging.getLogger(__name__)

COLLECTION_NAME = "historical_odds_full"


# Markets to backfill — exact order matches the user's spec.
TARGET_MARKETS = [
    "player_points_rebounds_assists",
    "player_points_rebounds_assists_alternate",
    "player_points_alternate",
    "player_rebounds_alternate",
    "player_assists_alternate",
    "player_threes_alternate",
    "player_points_rebounds_alternate",
    "player_points_assists_alternate",
    "player_rebounds_assists_alternate",
    "player_blocks",
    "player_steals",
    "player_double_double",
]

# Map raw market_key → canonical stat_family used elsewhere in the system.
_MARKET_TO_FAMILY = {
    "player_points":            "PTS",
    "player_points_alternate":  "PTS",
    "player_rebounds":          "REB",
    "player_rebounds_alternate":"REB",
    "player_assists":           "AST",
    "player_assists_alternate": "AST",
    "player_threes":            "THREES",
    "player_threes_alternate":  "THREES",
    "player_blocks":            "BLK",
    "player_blocks_alternate":  "BLK",
    "player_steals":            "STL",
    "player_steals_alternate":  "STL",
    "player_turnovers":         "TURNOVERS",
    "player_double_double":     "DOUBLE_DOUBLE",
    "player_points_rebounds":              "PTS_REB",
    "player_points_rebounds_alternate":    "PTS_REB",
    "player_points_assists":               "PTS_AST",
    "player_points_assists_alternate":     "PTS_AST",
    "player_rebounds_assists":             "REB_AST",
    "player_rebounds_assists_alternate":   "REB_AST",
    "player_points_rebounds_assists":           "PRA",
    "player_points_rebounds_assists_alternate": "PRA",
}

_COMBO_FAMILIES = {"PRA", "PTS_REB", "PTS_AST", "REB_AST"}


def market_to_family(market_key: str) -> str:
    """Canonical stat_family code for the given market key. Falls back
    to the upper-case raw key when no mapping exists (so we never lose
    the data for a brand-new market the API ships)."""
    return _MARKET_TO_FAMILY.get(market_key, market_key.upper())


def is_alternate(market_key: str) -> bool:
    return market_key.endswith("_alternate")


def is_combo(market_key: str) -> bool:
    return market_to_family(market_key) in _COMBO_FAMILIES


async def ensure_indexes(db) -> Dict[str, Any]:
    """Create the `historical_odds_full` collection indexes. Safe to
    call repeatedly — each `create_index` is a no-op if the index
    already exists with the same definition."""
    coll = db[COLLECTION_NAME]
    created = []

    # Unique compound index — protects against duplicate ingestions.
    created.append(await coll.create_index(
        [("event_id", ASCENDING), ("market_key", ASCENDING),
         ("snapshot_time", ASCENDING), ("bookmaker", ASCENDING),
         ("player", ASCENDING), ("line", ASCENDING), ("side", ASCENDING)],
        name="uniq_event_market_snapshot_book_player_line_side",
        unique=True,
    ))
    # Hot-path query indexes for the backtest replay layer.
    created.append(await coll.create_index(
        [("game_date", ASCENDING)], name="game_date"))
    created.append(await coll.create_index(
        [("event_id", ASCENDING)], name="event_id"))
    created.append(await coll.create_index(
        [("player", ASCENDING)], name="player"))
    created.append(await coll.create_index(
        [("market_key", ASCENDING)], name="market_key"))
    created.append(await coll.create_index(
        [("line", ASCENDING)], name="line"))
    created.append(await coll.create_index(
        [("snapshot_time", DESCENDING)], name="snapshot_time_desc"))
    created.append(await coll.create_index(
        [("stat_family", ASCENDING)], name="stat_family"))

    logger.info(f"[odds_api_backfill] indexes ready: {created}")
    return {"collection": COLLECTION_NAME, "indexes": created}


__all__ = [
    "COLLECTION_NAME", "TARGET_MARKETS",
    "ensure_indexes", "is_alternate", "is_combo", "market_to_family",
]

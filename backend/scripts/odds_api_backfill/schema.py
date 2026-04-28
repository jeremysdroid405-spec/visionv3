"""
Collection bootstrap for `historical_odds_full` (multi-sport).

One row per (sport, event, market, side, line, bookmaker, snapshot_time).
Sport-specific market lists, family maps, and combo sets live in
`sport_markets.py`. This module owns ONLY the persistent shape +
indexes.

Schema (additive — `sport_key` was added 2026-04-29 for multi-sport):
    sport_key          str       'basketball_nba' | 'baseball_mlb' | …
    event_id           str       Odds API event id (globally unique)
    commence_time      datetime  Game start UTC
    game_date          str       'YYYY-MM-DD' (UTC)
    home_team          str
    away_team          str
    snapshot_time      datetime  When the odds snapshot was captured
    snapshot_label     str       'open' | 'pregame_-1h' | 'pregame_-10m'
    bookmaker          str       'draftkings' | 'fanduel' | …
    region             str       'us'
    market_key         str       sport-specific market key (raw)
    is_alternate       bool      derived
    is_combo           bool      derived
    stat_family        str       canonical, sport-specific
    player             str       canonical lower-case
    line               float
    side               str       'OVER' | 'UNDER' | 'YES' | 'NO'
    odds_american      int
    source             str       'odds_api_v4_historical'
    ingested_at        datetime
    _first_seen        datetime  setOnInsert audit

Unique compound: (sport_key, event_id, market_key, snapshot_time,
                   bookmaker, player, line, side)
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from pymongo import ASCENDING, DESCENDING

from .sport_markets import (  # re-exports for backwards-compat callers
    DEFAULT_SPORT, SUPPORTED_SPORTS,
    is_alternate, is_combo, market_to_family, markets_for,
)

logger = logging.getLogger(__name__)

COLLECTION_NAME = "historical_odds_full"


# Backwards-compat shim: callers that imported `TARGET_MARKETS` from
# this module before multi-sport support get the NBA list (the only
# list that existed before this change).
TARGET_MARKETS = markets_for(DEFAULT_SPORT)


async def ensure_indexes(db) -> Dict[str, Any]:
    """Create the `historical_odds_full` collection indexes. Safe to
    call repeatedly. Each index is sport-aware where it makes sense
    (sport_key as leading field for hot-path queries that scope a
    single sport)."""
    coll = db[COLLECTION_NAME]
    created = []

    # Unique compound — protects against duplicates across all sports.
    # `sport_key` is leading because every backtest query is scoped to
    # one sport at a time.
    created.append(await coll.create_index(
        [("sport_key", ASCENDING), ("event_id", ASCENDING),
         ("market_key", ASCENDING), ("snapshot_time", ASCENDING),
         ("bookmaker", ASCENDING), ("player", ASCENDING),
         ("line", ASCENDING), ("side", ASCENDING)],
        name="uniq_sport_event_market_snapshot_book_player_line_side",
        unique=True,
    ))

    # Sport-scoped hot-path query indexes.
    created.append(await coll.create_index(
        [("sport_key", ASCENDING), ("game_date", ASCENDING)],
        name="sport_game_date"))
    created.append(await coll.create_index(
        [("sport_key", ASCENDING), ("event_id", ASCENDING)],
        name="sport_event_id"))
    created.append(await coll.create_index(
        [("sport_key", ASCENDING), ("player", ASCENDING)],
        name="sport_player"))
    created.append(await coll.create_index(
        [("sport_key", ASCENDING), ("market_key", ASCENDING)],
        name="sport_market_key"))
    created.append(await coll.create_index(
        [("sport_key", ASCENDING), ("stat_family", ASCENDING)],
        name="sport_stat_family"))

    # Sport-agnostic helpers (rarely scanned, but useful for cleanup
    # and analytics across sports).
    created.append(await coll.create_index(
        [("line", ASCENDING)], name="line"))
    created.append(await coll.create_index(
        [("snapshot_time", DESCENDING)], name="snapshot_time_desc"))

    logger.info(f"[odds_api_backfill] indexes ready: {created}")
    return {"collection": COLLECTION_NAME, "indexes": created}


__all__ = [
    "COLLECTION_NAME", "TARGET_MARKETS",
    "DEFAULT_SPORT", "SUPPORTED_SPORTS",
    "ensure_indexes", "is_alternate", "is_combo",
    "market_to_family", "markets_for",
]

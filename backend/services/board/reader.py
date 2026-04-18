"""
Universal board reader — ONE read path for every sport.

  get_board(db, sport, tier, limit=None) -> List[Dict]

The board is a LIVE QUERY against the master pool. No stored tier
collections, no atomic swap. A prop appears on the board the moment it is
scored with a qualifying tier; it disappears the moment it is marked
inactive (game_started / pulled).

Filter semantics:
    version_tag == adapter.version_tag
    tier        == requested tier
    active      != False                      # default True (missing field counts as active)
    game_start_utc > now  OR  game_start_utc IS NULL   # belt-and-suspenders guard

Sort:
    adapter.sort_key_for_tier(tier)  DESC
    secondary: pp_utility DESC  (stable tiebreak for equal primary-key values)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from services.board.adapters import get_adapter


async def get_board(
    db,
    sport: str,
    tier: str,
    limit: Optional[int] = None,
) -> List[Dict]:
    adapter = get_adapter(sport)
    cap = int(limit) if limit else adapter.capacity_for_tier(tier)
    now_utc = datetime.now(timezone.utc)
    primary = adapter.sort_key_for_tier(tier)

    # Main filter: exclude inactive + exclude tipped-off games.
    # `active` default-True semantics: we treat missing field as active
    # (legacy docs without the field are still valid).
    query = {
        "version_tag": adapter.version_tag,
        "tier": tier,
        "active": {"$ne": False},
        "$or": [
            {"game_start_utc": None},
            {"game_start_utc": {"$gt": now_utc}},
            {"game_start_utc": {"$exists": False}},
        ],
    }
    projection = {"_id": 0}

    # Deterministic sort: primary key desc, pp_utility desc as tiebreak.
    sort_spec = [(primary, -1)]
    if primary != "pp_utility":
        sort_spec.append(("pp_utility", -1))

    cursor = (
        db[adapter.scores_collection]
        .find(query, projection)
        .sort(sort_spec)
        .limit(cap)
    )
    return await cursor.to_list(length=cap)


async def get_board_count(db, sport: str, tier: str) -> int:
    """Total number of active, non-tipped-off props in a tier pool.
    Useful for observability (pool depth vs. board capacity)."""
    adapter = get_adapter(sport)
    now_utc = datetime.now(timezone.utc)
    return await db[adapter.scores_collection].count_documents({
        "version_tag": adapter.version_tag,
        "tier": tier,
        "active": {"$ne": False},
        "$or": [
            {"game_start_utc": None},
            {"game_start_utc": {"$gt": now_utc}},
            {"game_start_utc": {"$exists": False}},
        ],
    })

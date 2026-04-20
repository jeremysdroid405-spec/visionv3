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
    sort_key_override: Optional[str] = None,
) -> List[Dict]:
    adapter = get_adapter(sport)
    cap = int(limit) if limit else adapter.capacity_for_tier(tier)
    now_utc = datetime.now(timezone.utc)
    primary = sort_key_override or adapter.sort_key_for_tier(tier)

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
    # When sorting on ranking_score_v2, exclude rows where the field is
    # missing/null so MongoDB doesn't float null → top of DESC order.
    if primary == "ranking_score_v2":
        query["ranking_score_v2"] = {"$ne": None}
    projection = {"_id": 0}

    # Deterministic sort: primary key desc, pp_utility desc as tiebreak.
    sort_spec = [(primary, -1)]
    if primary != "pp_utility":
        sort_spec.append(("pp_utility", -1))

    # ------------------------------------------------------------------
    # TIER INTEGRITY — one player = max one pick per tier (board-wide).
    # ------------------------------------------------------------------
    # The scoring store legitimately holds multiple qualifying props per
    # player (e.g. Amen Thompson PTS 14.5 OVER *and* PTS 15.5 OVER — both
    # pass the safe_haven gates). Surfacing both on the same board violates
    # the product invariant that each tier lists distinct players. This
    # invariant is enforced here in the reader — the single entry point
    # every board route funnels through — so the dedup can never be
    # accidentally bypassed by a new caller.
    #
    # Strategy:
    #   1. Over-fetch up to `cap * OVER_FETCH_FACTOR` rows (bounded) sorted
    #      by the adapter's tier key so the best row per player appears
    #      FIRST in the stream.
    #   2. Walk the sorted stream; keep the first occurrence per
    #      normalized player_name, skip subsequent duplicates.
    #   3. Stop once we have `cap` distinct players.
    #
    # Sort already prioritizes `primary DESC, pp_utility DESC` — "first
    # seen" is therefore "best pick" (highest vision_score, tie-broken by
    # pp_utility). No additional comparison needed.
    OVER_FETCH_FACTOR = 6
    OVER_FETCH_MAX = 500
    fetch_limit = min(max(cap * OVER_FETCH_FACTOR, cap), OVER_FETCH_MAX)

    cursor = (
        db[adapter.scores_collection]
        .find(query, projection)
        .sort(sort_spec)
        .limit(fetch_limit)
    )
    raw = await cursor.to_list(length=fetch_limit)

    seen: set = set()
    deduped: List[Dict] = []
    for row in raw:
        player_key = (row.get("player_name") or "").strip().lower()
        if not player_key:
            # rows missing player_name are not a valid tier pick — skip
            continue
        if player_key in seen:
            continue
        seen.add(player_key)
        deduped.append(row)
        if len(deduped) >= cap:
            break

    return deduped


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

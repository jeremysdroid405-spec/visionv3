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
    # INTENTIONAL: Do NOT filter out rows where the primary sort key is
    # null/missing. MongoDB sorts BSON null/missing values as LOWEST, so
    # DESC naturally places them LAST — exactly where we want them.
    #
    # Previously this branch added `{$ne: null}` for the gap-sort key
    # (`ranking_score_v2`) under the false assumption that null would
    # float to the top of DESC order. That assumption was wrong AND the
    # filter had a destructive side effect: the filtered candidate pool
    # was passed to `publisher.reconcile(...)`, which interprets
    # "canonical_key missing from candidates" as "pick no longer
    # qualifying" and false-evicts the pick from the persisted
    # `board_state`. Every gap-sort read silently shrank the published
    # board; every non-gap read healed it. The dashboard flapped on
    # every poll.
    #
    # Regression tests: `tests/test_reader_gap_sort_null_filter.py`
    # (INV-G1 / G2 / G3 — 2026-04-30 bug).
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
    # NOTE: We dedup to the TIER-WIDE cap (not the side-split cap) so
    # the publisher below has enough candidates to fill both OVER and
    # UNDER pools for split tiers like Front Lines. For split tiers we
    # need up to 2× cap candidates (10 OVER + 10 UNDER); for combined
    # tiers `cap` is sufficient.
    from services.board.publisher import TIER_CONFIG as _TIER_CFG
    _split = bool((_TIER_CFG.get(tier) or {}).get("split_by_side"))
    pool_cap = cap * 2 if _split else cap
    for row in raw:
        player_key = (row.get("player_name") or "").strip().lower()
        if not player_key:
            # rows missing player_name are not a valid tier pick — skip
            continue
        if player_key in seen:
            continue
        seen.add(player_key)
        deduped.append(row)
        if len(deduped) >= pool_cap:
            break

    # ── Universal Stable Board Publisher (2026-04-29) ────────────────
    # Reconcile the persistent `board_state` with this fresh candidate
    # pool, then return picks in PUBLISHED order. The reconcile is:
    #   * fill mode while the board is below capacity (full re-rank OK)
    #   * stable mode at capacity (existing picks keep their slots; a
    #     new candidate may enter only if it outranks the current last
    #     pick and inserts at TRUE rank)
    # Pure publish-layer concern — NO scoring / model / gate / threshold
    # touched. Adding a new sport requires zero edits here.
    try:
        from services.board.publisher import (
            TIER_CONFIG,
            reconcile,
            get_published_board,
        )
        if tier in TIER_CONFIG:
            await reconcile(db, sport, tier, deduped)
            published = await get_published_board(db, sport, tier)
            # Re-attach the fresh score docs by canonical_key — the
            # board_state row only carries the score snapshot for ranking.
            by_key = {row.get("canonical_key"): row for row in deduped}
            ordered: List[Dict] = []
            for entry in published:
                ck = entry.get("canonical_key")
                doc = by_key.get(ck)
                if doc is not None:
                    ordered.append(doc)
            if ordered:
                # ── Shadow Board (Vision v2 ranking) ──────────────────
                # NBA-only, fire-and-forget, writes to DEDICATED
                # `board_state_shadow` collection. Does NOT influence
                # the returned `ordered` list. Wrapped in try/except
                # so any shadow failure can never 5xx the production
                # board read.
                try:
                    if sport == "nba":
                        from services.board.shadow_publisher import reconcile_shadow
                        await reconcile_shadow(db, sport, tier, deduped)
                except Exception as _sh_err:
                    import logging as _logging
                    _logging.getLogger(__name__).warning(
                        "[SHADOW_BOARD] reconcile failed sport=%s tier=%s: %s",
                        sport, tier, _sh_err,
                    )

                # `cap` honors the requested limit (route may pass any
                # value 1..50). For Front Lines: caller passes the
                # combined cap (e.g. 20 to see both sides, 10 to see
                # only the top of the combined feed).
                return ordered[:cap]
    except Exception as e:
        # Publisher MUST NEVER 5xx the live read — fall back to the
        # legacy fresh-sort behavior on any error.
        import logging as _logging
        _logging.getLogger(__name__).error(
            "[BOARD_PUBLISHER] reconcile failed sport=%s tier=%s: %s",
            sport, tier, e, exc_info=True,
        )

    return deduped[:cap]


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

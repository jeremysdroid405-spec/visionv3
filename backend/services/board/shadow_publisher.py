"""
Shadow Board Publisher — Vision v2 Ranking, Read-Only Side Channel
==================================================================

Runs the SAME stable-publish algorithm as `services.board.publisher`
but with a v2-aware ranking tuple, and writes to a DEDICATED collection
(`board_state_shadow`) so the production `board_state` is NEVER
mutated by this code path.

Goals:
  * No live user-facing behaviour change.
  * No destabilization of `board_state`.
  * Capture what the dashboard WOULD look like if v2 were the ranker.
  * Provide a comparison surface for `/api/debug/shadow_board/*`.

Strict rules:
  * Reads `board_state_shadow` for survivor state.
  * Writes ONLY to `board_state_shadow` and `board_state_shadow_events`.
  * Identical capacity / fill / stable / insertion semantics as prod.
  * NBA only for now (early-stage shadow). Other sports are silently
    skipped — production behaviour for them is unchanged.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from services.board import publisher as primary

logger = logging.getLogger(__name__)


SHADOW_COLL = "board_state_shadow"
SHADOW_EVENTS_COLL = "board_state_shadow_events"
SHADOW_SPORTS = ("nba",)  # extend as v2 stabilizes


# ─── Indexes (idempotent) ────────────────────────────────────────────
async def ensure_indexes(db) -> None:
    if db is None:
        return
    try:
        await db[SHADOW_COLL].create_index(
            [("sport", 1), ("tier", 1), ("side", 1), ("canonical_key", 1)],
            unique=True, name="shadow_board_state_identity_uq",
        )
        await db[SHADOW_COLL].create_index(
            [("sport", 1), ("tier", 1), ("side", 1), ("active", 1), ("rank", 1)],
            name="shadow_board_state_read_idx",
        )
        await db[SHADOW_EVENTS_COLL].create_index(
            "occurred_at",
            expireAfterSeconds=7 * 24 * 3600,
            name="shadow_board_state_events_ttl",
        )
    except Exception as e:  # pragma: no cover
        logger.warning("[SHADOW_PUB] index ensure failed: %s", e)


def _rank_score_v2(p: Dict[str, Any]) -> float:
    """v2-pure ranking signal — never falls back to v1 vision_score.

    Resolution chain: ranking_score_v2 → vision_score_v2.
    Picks lacking BOTH score float to -inf so they sort to the bottom
    in the shadow board (which is the safest behaviour for an
    experimental ranking).
    """
    for k in ("ranking_score_v2", "vision_score_v2"):
        v = p.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    return float("-inf")


# ─── v2 rank tuple ───────────────────────────────────────────────────
def rank_tuple_v2(p: Dict[str, Any]) -> Tuple[float, float, float, str]:
    """Shadow ranking tuple — v2-pure.

    Slot 1: `ranking_score_v2` (or `vision_score_v2` fallback)  DESC
    Slot 2: `vision_score_v2`                                   DESC
    Slot 3: `edge_vs_fair`                                      DESC
    Slot 4: `canonical_key`                                     ASC

    2026-05-07 P0 Phase 4A: legacy `edge_pct` replaced with canonical
    `edge_vs_fair` (matching primary publisher rank tuple).
    """
    return (
        -_rank_score_v2(p),
        -primary._num(p.get("vision_score_v2")),
        -primary._num(p.get("edge_vs_fair")),
        p.get("canonical_key") or "",
    )


# ─── I/O against the SHADOW collection only ──────────────────────────
async def _shadow_load_active(db, sport, tier, side):
    cursor = db[SHADOW_COLL].find(
        {"sport": sport, "tier": tier, "side": side, "active": True},
        {"_id": 0},
    ).sort("rank", 1)
    return await cursor.to_list(length=200)


async def _shadow_persist(db, sport, tier, side, ordered, evicted_keys,
                          eviction_reason):
    """Mirrors `primary._persist` but targets the shadow collection.
    Identical schema so the same observability tools work."""
    now = datetime.now(timezone.utc)
    keep_keys = [e["canonical_key"] for e in ordered]

    pre_active_keys: set = set()
    if db is not None:
        cursor = db[SHADOW_COLL].find(
            {"sport": sport, "tier": tier, "side": side, "active": True},
            {"_id": 0, "canonical_key": 1},
        )
        for r in await cursor.to_list(length=200):
            pre_active_keys.add(r.get("canonical_key"))

    if evicted_keys:
        await db[SHADOW_COLL].update_many(
            {"sport": sport, "tier": tier, "side": side,
             "canonical_key": {"$in": list(evicted_keys)}},
            {"$set": {"active": False,
                      "last_updated_at": now,
                      "last_seen_at": now,
                      "invalidation_reason": eviction_reason}},
        )

    await db[SHADOW_COLL].update_many(
        {"sport": sport, "tier": tier, "side": side, "active": True,
         "canonical_key": {"$nin": keep_keys}},
        {"$set": {"active": False, "last_updated_at": now,
                  "last_seen_at": now,
                  "invalidation_reason": "no_longer_qualifying"}},
    )

    for slot, entry in enumerate(ordered, start=1):
        ck = entry["canonical_key"]
        snap = {
            "ranking_score":   primary._rank_score(entry),
            "vision_score_v2": entry.get("vision_score_v2"),
            "vision_score":    entry.get("vision_score"),
            "edge_vs_fair":    entry.get("edge_vs_fair"),
            "p_true_active":   entry.get("p_true_active"),
            "vision_direction_alignment":
                              entry.get("vision_direction_alignment"),
        }
        await db[SHADOW_COLL].update_one(
            {"sport": sport, "tier": tier, "side": side, "canonical_key": ck},
            {
                "$set": {
                    "rank":            slot,
                    "active":          True,
                    "last_updated_at": now,
                    "last_seen_at":    now,
                    "score_snapshot":  snap,
                    "invalidation_reason": None,
                },
                "$setOnInsert": {
                    "first_seen_at":   now,
                    "sport":           sport,
                    "tier":            tier,
                    "side":            side,
                    "canonical_key":   ck,
                    "variant":         "v2",
                },
            },
            upsert=True,
        )

    new_active_keys = set(keep_keys)
    insertions = new_active_keys - pre_active_keys
    removals = pre_active_keys - new_active_keys
    events: List[Dict[str, Any]] = []
    for ck in insertions:
        events.append({"kind": "insertion", "sport": sport, "tier": tier,
                       "side": side, "canonical_key": ck,
                       "occurred_at": now, "variant": "v2"})
    for ck in removals:
        events.append({"kind": "removal", "sport": sport, "tier": tier,
                       "side": side, "canonical_key": ck,
                       "reason": eviction_reason
                                 if ck in (evicted_keys or [])
                                 else "no_longer_qualifying",
                       "occurred_at": now, "variant": "v2"})
    if events:
        try:
            await db[SHADOW_EVENTS_COLL].insert_many(events, ordered=False)
        except Exception as e:  # pragma: no cover
            logger.warning("[SHADOW_PUB] event emit failed: %s", e)


# ─── Public API — drop-in shape match with primary publisher ──────────
async def reconcile_shadow(db, sport: str, tier: str,
                           candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Reconcile the SHADOW board for (sport, tier). Sport-gated to
    NBA-only at launch. Identical contract to `primary.reconcile`."""
    if sport not in SHADOW_SPORTS:
        return {"sport": sport, "tier": tier, "skipped": "shadow_disabled_for_sport"}
    cfg = primary.TIER_CONFIG.get(tier)
    if cfg is None:
        return {"sport": sport, "tier": tier, "skipped": "unknown_tier"}

    cap = cfg["capacity_per_side"]
    sides = ("OVER", "UNDER") if cfg["split_by_side"] else (None,)
    audit = {"sport": sport, "tier": tier, "variant": "v2",
             "capacity_per_side": cap, "sides": {}}

    for side in sides:
        if side is None:
            side_cands = list(candidates)
        else:
            side_cands = [c for c in candidates
                          if primary._side_of(c) == side]
        state = await _shadow_load_active(db, sport, tier, side)
        ordered, evicted, mode = primary._reconcile_in_memory(
            state, side_cands, cap, rank_fn=rank_tuple_v2,
        )
        await _shadow_persist(db, sport, tier, side, ordered, evicted,
                              eviction_reason=("displaced" if mode == "stable"
                                               else "no_longer_qualifying"))
        audit["sides"][side or "combined"] = {
            "mode": mode,
            "candidates_count": len(side_cands),
            "board_count": len(ordered),
            "evicted_count": len(evicted),
        }
    return audit


async def get_shadow_board(db, sport: str, tier: str,
                           side: Optional[str] = None,
                           limit: Optional[int] = None) -> List[Dict[str, Any]]:
    cfg = primary.TIER_CONFIG.get(tier) or {}
    if cfg.get("split_by_side") and side is None:
        over = await _shadow_load_active(db, sport, tier, "OVER")
        under = await _shadow_load_active(db, sport, tier, "UNDER")
        merged = over + under
        merged.sort(key=lambda e: (
            -primary._num((e.get("score_snapshot") or {}).get("ranking_score")),
            -primary._num((e.get("score_snapshot") or {}).get("vision_score_v2")),
            -primary._num((e.get("score_snapshot") or {}).get("edge_vs_fair")),
            e.get("canonical_key") or "",
        ))
        rows = merged
    else:
        rows = await _shadow_load_active(db, sport, tier, side)
    if limit:
        rows = rows[:limit]
    return rows


__all__ = [
    "SHADOW_COLL",
    "SHADOW_EVENTS_COLL",
    "SHADOW_SPORTS",
    "rank_tuple_v2",
    "ensure_indexes",
    "reconcile_shadow",
    "get_shadow_board",
]

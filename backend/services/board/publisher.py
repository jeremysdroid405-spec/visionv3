"""
Universal Board Publisher — Stable, Incremental Tier Boards
===========================================================

ONE shared layer that turns the volatile `{sport}_prop_scores` pool into
a STABLE published board for Safe Haven, Front Lines, and War Zone.
Used by NBA, MLB, NFL (future), NHL (future) — no sport-specific code.

Why this exists
---------------
Every API read of `services/board/reader.py::get_board()` used to issue
a fresh top-N sort over the volatile prop_scores collection. The delta
engine rewrites those scores every minute, so a small change in
`vision_score` could re-rank the entire board, picks would fall off,
and new picks would appear. The dashboard "felt unstable" because it
WAS unstable.

This publisher persists the live board to a `board_state` collection
and applies fill / stable / insertion semantics:

* Fill mode (board < capacity): full ranking allowed.
* Stable mode (board at capacity): existing picks keep their slots;
  new candidates may only enter if they outrank the current last pick;
  on entry they insert at TRUE rank (not forced to #1).

Universal rules
---------------
- Capacities:
    safe_haven: 10 (combined)
    front_lines: 10 OVER + 10 UNDER  (two independent boards, 20 total)
    war_zone:   10 (combined)
- Deterministic sort tuple:
    1. ranking_score    DESC      (ranking_score_v2 → ranking_score → vision_score)
    2. vision_score     DESC
    3. edge_pct         DESC
    4. canonical_key    ASC

What this DOES NOT touch
------------------------
No scoring formulas, μ, σ, gates, thresholds, tier-routing, or
pick-selection logic. This is a publish-layer reordering / persistence
only. Verified by tests in `tests/test_board_publisher.py`.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ─── Universal tier configuration ─────────────────────────────────────
# Adding a new sport requires NO edit here — every sport routes its
# scored picks through this same config. Adding a new TIER for a new
# sport (e.g. NHL "rocket_zone") is a single TIER_CONFIG entry.
TIER_CONFIG: Dict[str, Dict[str, Any]] = {
    "safe_haven":  {"capacity_per_side": 10, "split_by_side": False},
    "front_lines": {"capacity_per_side": 10, "split_by_side": True},
    "war_zone":    {"capacity_per_side": 10, "split_by_side": False},
}

COLL = "board_state"


# ─── Indexes (idempotent, called at startup) ──────────────────────────
async def ensure_indexes(db) -> None:
    if db is None:
        return
    try:
        await db[COLL].create_index(
            [("sport", 1), ("tier", 1), ("side", 1), ("canonical_key", 1)],
            unique=True,
            name="board_state_identity_uq",
        )
        await db[COLL].create_index(
            [("sport", 1), ("tier", 1), ("side", 1), ("active", 1), ("rank", 1)],
            name="board_state_read_idx",
        )
        await db[COLL].create_index(
            [("active", 1), ("last_updated_at", 1)],
            name="board_state_gc_idx",
        )
    except Exception as e:  # pragma: no cover
        logger.warning("[BOARD_PUB] index ensure failed: %s", e)


# ─── Helpers ──────────────────────────────────────────────────────────
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _num(v: Any) -> float:
    """Treat None / non-numeric as -inf so DESC sorts push them last."""
    return float(v) if isinstance(v, (int, float)) else float("-inf")


def _rank_score(p: Dict[str, Any]) -> float:
    """Universal ranking-score signal with fallbacks.

    `ranking_score_v2` is the canonical projection-gap ranker on the
    score doc; legacy docs use `ranking_score`; everything else falls
    back to `vision_score`.
    """
    for k in ("ranking_score_v2", "ranking_score", "vision_score"):
        v = p.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    return float("-inf")


def rank_tuple(p: Dict[str, Any]) -> Tuple[float, float, float, str]:
    """Deterministic ordering tuple — the one rule. Lower is BETTER
    (we negate DESC fields)."""
    return (
        -_rank_score(p),
        -_num(p.get("vision_score")),
        -_num(p.get("edge_pct")),
        p.get("canonical_key") or "",
    )


def _side_of(p: Dict[str, Any]) -> str:
    raw = (p.get("recommendation") or p.get("direction") or "").upper()
    return "UNDER" if "UNDER" in raw else "OVER"


def _snapshot(p: Dict[str, Any]) -> Dict[str, Any]:
    """The minimal score fingerprint we persist. NEVER includes the
    full pick — that lives in `{sport}_prop_scores`. We just keep
    enough to compare ranks deterministically."""
    return {
        "ranking_score": _rank_score(p),
        "vision_score":  p.get("vision_score"),
        "edge_pct":      p.get("edge_pct"),
        "tp":            p.get("tp"),
        "p_true_active": p.get("p_true_active"),
    }


def _split_required(tier: str) -> bool:
    cfg = TIER_CONFIG.get(tier) or {}
    return bool(cfg.get("split_by_side"))


def _capacity(tier: str) -> int:
    cfg = TIER_CONFIG.get(tier) or {}
    return int(cfg.get("capacity_per_side", 10))


# ─── Persistence I/O ──────────────────────────────────────────────────
async def _load_active(db, sport: str, tier: str,
                       side: Optional[str]) -> List[Dict[str, Any]]:
    """All currently-active rows for a (sport, tier, side) bucket,
    ordered by stored `rank` ASC (slot 1 first)."""
    if db is None:
        return []
    cursor = db[COLL].find(
        {"sport": sport, "tier": tier, "side": side, "active": True},
        {"_id": 0},
    ).sort("rank", 1)
    return await cursor.to_list(length=200)


async def _persist(db, sport: str, tier: str, side: Optional[str],
                   ordered: List[Dict[str, Any]],
                   evicted_keys: Iterable[str],
                   eviction_reason: str) -> None:
    """Persist the new ordered slate; mark non-survivors inactive.

    Idempotent: rerunning with identical input is a no-op.
    """
    now = _now()
    keep_keys = [e["canonical_key"] for e in ordered]

    # 1. Mark explicitly-evicted rows inactive.
    if evicted_keys:
        await db[COLL].update_many(
            {"sport": sport, "tier": tier, "side": side,
             "canonical_key": {"$in": list(evicted_keys)}},
            {"$set": {"active": False,
                      "last_updated_at": now,
                      "invalidation_reason": eviction_reason}},
        )

    # 2. Mark anything else not in keep_keys inactive (defensive — covers
    #    rows that disappeared from the candidate pool).
    await db[COLL].update_many(
        {"sport": sport, "tier": tier, "side": side, "active": True,
         "canonical_key": {"$nin": keep_keys}},
        {"$set": {"active": False, "last_updated_at": now,
                  "invalidation_reason": "no_longer_qualifying"}},
    )

    # 3. Upsert each survivor with its new rank + refreshed snapshot.
    for slot, entry in enumerate(ordered, start=1):
        ck = entry["canonical_key"]
        await db[COLL].update_one(
            {"sport": sport, "tier": tier, "side": side, "canonical_key": ck},
            {
                "$set": {
                    "rank":             slot,
                    "active":           True,
                    "last_updated_at":  now,
                    "score_snapshot":   _snapshot(entry),
                    "invalidation_reason": None,
                },
                "$setOnInsert": {
                    "first_seen_at":    now,
                    "sport":            sport,
                    "tier":             tier,
                    "side":             side,
                    "canonical_key":    ck,
                },
            },
            upsert=True,
        )


# ─── Core reconciliation algorithm ────────────────────────────────────
def _reconcile_in_memory(
    state: List[Dict[str, Any]],
    candidates: List[Dict[str, Any]],
    capacity: int,
) -> Tuple[List[Dict[str, Any]], List[str], str]:
    """Pure-function reconciliation — easy to unit test.

    Inputs:
        state       — current persisted board, ordered by rank ASC.
                      Each entry MUST carry `canonical_key` and the
                      score fields (ranking_score / vision_score /
                      edge_pct) so its rank tuple is comparable.
        candidates  — fresh scored picks for this (sport, tier, side).
        capacity    — max picks for this side.

    Returns:
        ordered     — the new ordered slate (length ≤ capacity).
        evicted     — canonical_keys removed (for logging / counters).
        mode        — "fill" or "stable" or "noop".
    """
    cand_by_key: Dict[str, Dict[str, Any]] = {}
    for c in candidates:
        ck = c.get("canonical_key")
        if ck:
            cand_by_key[ck] = c

    # 1. Refresh metrics on survivors that are still in the pool;
    #    drop survivors that vanished. We start from the FRESH candidate
    #    dict (which carries player_name, line, recommendation, etc.) so
    #    downstream consumers see the latest data on every pass.
    survivors: List[Dict[str, Any]] = []
    evicted: List[str] = []
    for entry in state:
        ck = entry.get("canonical_key")
        if ck in cand_by_key:
            survivors.append(dict(cand_by_key[ck]))
        else:
            evicted.append(ck)

    # 2. FILL MODE — board below capacity. Full re-rank allowed.
    if len(survivors) < capacity:
        ranked = sorted(candidates, key=rank_tuple)
        ordered = ranked[:capacity]
        # Anything that USED to be on the board but isn't in `ordered`
        # is implicitly evicted — caller will mark inactive.
        return ordered, evicted, "fill"

    # 3. STABLE MODE — board at capacity. Survivor relative order is
    #    preserved. New candidates may insert ONLY if their rank tuple
    #    beats the current last pick.
    survivor_keys = {e["canonical_key"] for e in survivors}
    last_tuple = rank_tuple(survivors[-1])
    new_entrants = sorted(
        (c for c in candidates if c.get("canonical_key") not in survivor_keys
         and rank_tuple(c) < last_tuple),
        key=rank_tuple,
    )

    if not new_entrants:
        # Nothing better arrived — keep the slate exactly as-is. The
        # only update is the metric refresh on survivors (already
        # captured by `merged`).
        return survivors, evicted, "noop"

    # 4. INSERTION — for each new entrant, find its TRUE slot in the
    # current board ordering. Insertion-sort semantics: survivor
    # relative order is preserved; entrant slots in by rank tuple.
    ordered = list(survivors)
    for cand in new_entrants:
        cand_t = rank_tuple(cand)
        # Find smallest k such that cand_t < ordered[k]'s rank tuple.
        insert_at = None
        for k, existing in enumerate(ordered):
            if cand_t < rank_tuple(existing):
                insert_at = k
                break
        if insert_at is None:
            insert_at = len(ordered)
        # If inserting beyond capacity, the entrant doesn't fit even
        # though it beat the *original* last pick — possible after
        # multiple entrants in one pass. Skip.
        if insert_at >= capacity:
            continue
        ordered.insert(insert_at, cand)
        # Truncate to capacity — the displaced last pick falls off.
        if len(ordered) > capacity:
            dropped = ordered.pop()
            evicted.append(dropped["canonical_key"])

    return ordered, evicted, "stable"


# ─── Public API ───────────────────────────────────────────────────────
async def reconcile(
    db, sport: str, tier: str, candidates: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Run the universal stable-publish reconcile for ONE (sport, tier).

    Splits OVER/UNDER automatically when the tier is configured for it.
    Returns a small audit dict.
    """
    cfg = TIER_CONFIG.get(tier)
    if cfg is None:
        return {"sport": sport, "tier": tier, "skipped": "unknown_tier"}

    cap = cfg["capacity_per_side"]
    if cfg["split_by_side"]:
        sides = ("OVER", "UNDER")
    else:
        sides = (None,)

    audit: Dict[str, Any] = {
        "sport": sport, "tier": tier, "capacity_per_side": cap,
        "split_by_side": cfg["split_by_side"], "sides": {},
    }
    for side in sides:
        if side is None:
            side_cands = list(candidates)
        else:
            side_cands = [c for c in candidates if _side_of(c) == side]
        state = await _load_active(db, sport, tier, side)
        ordered, evicted, mode = _reconcile_in_memory(state, side_cands, cap)
        await _persist(db, sport, tier, side, ordered, evicted,
                       eviction_reason="displaced" if mode == "stable" else
                                       "no_longer_qualifying")
        audit["sides"][side or "combined"] = {
            "mode": mode,
            "candidates_count": len(side_cands),
            "board_count": len(ordered),
            "evicted_count": len(evicted),
        }
    return audit


async def get_published_board(
    db, sport: str, tier: str, side: Optional[str] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Read-only fetch of the persisted ordered board.

    For split tiers (front_lines), `side=None` returns OVER+UNDER
    interleaved by rank tuple. Pass `side="OVER"` / `"UNDER"` to get
    just one side.
    """
    cfg = TIER_CONFIG.get(tier) or {}
    if cfg.get("split_by_side") and side is None:
        over = await _load_active(db, sport, tier, "OVER")
        under = await _load_active(db, sport, tier, "UNDER")
        # Stable, deterministic merge — sort by stored rank tuple
        # signal then by canonical_key for tiebreak.
        merged = over + under
        merged.sort(key=lambda e: (
            -_num((e.get("score_snapshot") or {}).get("ranking_score")),
            -_num((e.get("score_snapshot") or {}).get("vision_score")),
            -_num((e.get("score_snapshot") or {}).get("edge_pct")),
            e.get("canonical_key") or "",
        ))
        rows = merged
    else:
        rows = await _load_active(db, sport, tier, side)
    if limit:
        rows = rows[:limit]
    return rows


__all__ = [
    "TIER_CONFIG",
    "rank_tuple",
    "ensure_indexes",
    "reconcile",
    "get_published_board",
    "_reconcile_in_memory",
]

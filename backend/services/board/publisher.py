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
from datetime import datetime, timedelta, timezone
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
EVENTS_COLL = "board_state_events"   # 7-day TTL — observability only


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
        # Events collection — TTL 7 days. Read-only observability path
        # (not used by publish logic).
        await db[EVENTS_COLL].create_index(
            "occurred_at",
            expireAfterSeconds=7 * 24 * 3600,
            name="board_state_events_ttl",
        )
        await db[EVENTS_COLL].create_index(
            [("sport", 1), ("tier", 1), ("side", 1), ("occurred_at", -1)],
            name="board_state_events_read_idx",
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
    """Canonical ranking-score signal.

    SSOT (FIELD_OWNERSHIP.md:ranking_score_v2, 2026-05-04): the only
    authoritative ranking signal on a score doc is `ranking_score_v2`,
    written by `services/scoring/recompute.py::recompute_sport`. It is
    legitimately `None` when `projection` / `line` / `p_model` is
    missing (e.g. identity-failed picks still go through the pipeline
    but have no model projection), so a pure hard-drop would hide a
    large slice of the slate.

    Behaviour:
    - If `ranking_score_v2` is present → use it (canonical).
    - Else fall back to `vision_score` ONLY — dropped the legacy
      `ranking_score` alias 2026-05-04 (it was a rename-era leftover
      with no live writer, producing stale sort orders). Also log a
      one-time-per-process SSOT violation so the missing-field rate
      stays observable.

    The registry spec for `ranking_score_v2` was retired from
    `fail_loud` to `return_null` in the same pass — the field
    returning None is a valid scoring outcome, not a data bug.
    """
    v = p.get("ranking_score_v2")
    if isinstance(v, (int, float)):
        return float(v)
    # Legitimate miss — use vision_score as the stable secondary sort
    # key. Emit a one-time warning per process so regressions in the
    # ranking_score_v2 writer stay visible in supervisor logs.
    try:
        seen = _rank_score.__dict__.setdefault("_warned", False)
        if not seen:
            logger.warning(
                "[SSOT:ranking_score_v2] at least one pick missing "
                "ranking_score_v2; falling back to vision_score. "
                "Canonical key sample: %s. "
                "Tracked via /api/health/active-transitions and "
                "future ranking-score-coverage probe.",
                p.get("canonical_key"),
            )
            _rank_score.__dict__["_warned"] = True
    except Exception:  # pragma: no cover — logging must never break sort
        pass
    vs = p.get("vision_score")
    if isinstance(vs, (int, float)):
        return float(vs)
    return float("-inf")


def rank_tuple(p: Dict[str, Any]) -> Tuple[float, float, float, str]:
    """Deterministic ordering tuple — the one rule. Lower is BETTER
    (we negate DESC fields).

    2026-05-07 P0 Phase 4A: legacy `edge_pct` replaced with canonical
    `edge_vs_fair`. The two fields carried the same value (modulo a
    rounding pass); the rename eliminates the SSOT violation that
    leaked `edge_pct` into 81,243 persisted score docs.
    """
    return (
        -_rank_score(p),
        -_num(p.get("vision_score")),
        -_num(p.get("edge_vs_fair")),
        p.get("canonical_key") or "",
    )


def _side_of(p: Dict[str, Any]) -> str:
    raw = (p.get("recommendation") or p.get("direction") or "").upper()
    return "UNDER" if "UNDER" in raw else "OVER"


def _snapshot(p: Dict[str, Any]) -> Dict[str, Any]:
    """The minimal score fingerprint we persist. NEVER includes the
    full pick — that lives in `{sport}_prop_scores`. We just keep
    enough to compare ranks deterministically.

    2026-05-07 P0 Phase 4A: `edge_pct` removed from the snapshot.
    Replaced with canonical `edge_vs_fair`. The score_snapshot is
    embedded in published board docs (`{sport}_cached_board.entries`)
    and was the on-disk source of the legacy alias for downstream
    sort tiebreaks.
    """
    return {
        "ranking_score": _rank_score(p),
        "vision_score":  p.get("vision_score"),
        "edge_vs_fair":  p.get("edge_vs_fair"),
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

    Also emits observability events:
      - `insertion` — new active row appearing for the first time.
      - `removal`   — previously-active row going inactive.
    Events live in `board_state_events` (TTL 7 days) and are consumed
    only by `/api/health/board`. They never influence publish logic.
    """
    now = _now()
    keep_keys = [e["canonical_key"] for e in ordered]

    # Snapshot which keys were active BEFORE this reconcile so we can
    # emit accurate insertion / removal events without re-reading.
    pre_active_keys: set = set()
    if db is not None:
        cursor = db[COLL].find(
            {"sport": sport, "tier": tier, "side": side, "active": True},
            {"_id": 0, "canonical_key": 1},
        )
        for r in await cursor.to_list(length=200):
            pre_active_keys.add(r.get("canonical_key"))

    # 1. Mark explicitly-evicted rows inactive.
    if evicted_keys:
        await db[COLL].update_many(
            {"sport": sport, "tier": tier, "side": side,
             "canonical_key": {"$in": list(evicted_keys)}},
            {"$set": {"active": False,
                      "last_updated_at": now,
                      "last_seen_at": now,
                      "invalidation_reason": eviction_reason}},
        )

    # 2. Mark anything else not in keep_keys inactive (defensive — covers
    #    rows that disappeared from the candidate pool).
    await db[COLL].update_many(
        {"sport": sport, "tier": tier, "side": side, "active": True,
         "canonical_key": {"$nin": keep_keys}},
        {"$set": {"active": False, "last_updated_at": now,
                  "last_seen_at": now,
                  "invalidation_reason": "no_longer_qualifying"}},
    )

    # 3. Upsert each survivor with its new rank + refreshed snapshot.
    #    `first_seen_at` is set ONLY on insert ($setOnInsert) so longevity
    #    is preserved across reconciles. `last_seen_at` updates every time.
    for slot, entry in enumerate(ordered, start=1):
        ck = entry["canonical_key"]
        await db[COLL].update_one(
            {"sport": sport, "tier": tier, "side": side, "canonical_key": ck},
            {
                "$set": {
                    "rank":             slot,
                    "active":           True,
                    "last_updated_at":  now,
                    "last_seen_at":     now,
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

    # 4. Emit observability events. Read-only path; never influences the
    #    publish logic above.
    new_active_keys = set(keep_keys)
    insertions = new_active_keys - pre_active_keys
    removals   = pre_active_keys - new_active_keys
    events: List[Dict[str, Any]] = []
    for ck in insertions:
        events.append({
            "kind":          "insertion",
            "sport":         sport,
            "tier":          tier,
            "side":          side,
            "canonical_key": ck,
            "occurred_at":   now,
        })
    for ck in removals:
        events.append({
            "kind":          "removal",
            "sport":         sport,
            "tier":          tier,
            "side":          side,
            "canonical_key": ck,
            "reason":        eviction_reason if ck in (evicted_keys or [])
                             else "no_longer_qualifying",
            "occurred_at":   now,
        })
    if events:
        try:
            await db[EVENTS_COLL].insert_many(events, ordered=False)
        except Exception as e:  # pragma: no cover
            logger.warning("[BOARD_PUB] event emit failed: %s", e)


# ─── Core reconciliation algorithm ────────────────────────────────────
def _reconcile_in_memory(
    state: List[Dict[str, Any]],
    candidates: List[Dict[str, Any]],
    capacity: int,
    rank_fn: Optional[Any] = None,
) -> Tuple[List[Dict[str, Any]], List[str], str]:
    """Pure-function reconciliation — easy to unit test.

    Inputs:
        state       — current persisted board, ordered by rank ASC.
                      Each entry MUST carry `canonical_key` and the
                      score fields (ranking_score / vision_score /
                      edge_pct) so its rank tuple is comparable.
        candidates  — fresh scored picks for this (sport, tier, side).
        capacity    — max picks for this side.
        rank_fn     — optional alternative ranking function. Defaults
                      to the production `rank_tuple` (v1 semantics).
                      Shadow boards pass `rank_tuple_v2` here.

    Returns:
        ordered     — the new ordered slate (length ≤ capacity).
        evicted     — canonical_keys removed (for logging / counters).
        mode        — "fill" or "stable" or "noop".
    """
    rank = rank_fn or rank_tuple
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
        ranked = sorted(candidates, key=rank)
        ordered = ranked[:capacity]
        # Anything that USED to be on the board but isn't in `ordered`
        # is implicitly evicted — caller will mark inactive.
        return ordered, evicted, "fill"

    # 3. STABLE MODE — board at capacity. Survivor relative order is
    #    preserved. New candidates may insert ONLY if their rank tuple
    #    beats the current last pick.
    survivor_keys = {e["canonical_key"] for e in survivors}
    last_tuple = rank(survivors[-1])
    new_entrants = sorted(
        (c for c in candidates if c.get("canonical_key") not in survivor_keys
         and rank(c) < last_tuple),
        key=rank,
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
        cand_t = rank(cand)
        # Find smallest k such that cand_t < ordered[k]'s rank tuple.
        insert_at = None
        for k, existing in enumerate(ordered):
            if cand_t < rank(existing):
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
            -_num((e.get("score_snapshot") or {}).get("edge_vs_fair")),
            e.get("canonical_key") or "",
        ))
        rows = merged
    else:
        rows = await _load_active(db, sport, tier, side)
    if limit:
        rows = rows[:limit]
    return rows


# ─── Longevity stamping (universal, sport-agnostic) ──────────────────
def _longevity_label(seconds: float) -> Optional[str]:
    if seconds is None:
        return None
    if seconds >= 6 * 3600:
        return "on board 6h+"
    if seconds >= 3 * 3600:
        return "on board 3h+"
    if seconds >= 1 * 3600:
        return "on board 1h+"
    return None


async def stamp_longevity_on_picks(
    db, sport: str, tier: str, picks: List[Dict[str, Any]],
) -> None:
    """Mutate `picks` in place to add the universal longevity contract:

        on_board_seconds : int   (0 if not yet persisted)
        on_board_minutes : int   (rounded)
        on_board_label   : str | None  ("on board 6h+" / "3h+" / "1h+" / null)

    Reads `first_seen_at` from `board_state` for the (sport, tier, *)
    bucket. Works for split tiers (Front Lines OVER/UNDER) without
    branching — `side` is included in the lookup key implicitly via
    `canonical_key` uniqueness.

    No-op if the bucket has no persisted state yet (e.g. first reconcile
    in-flight); pick gets `on_board_seconds=0`, `on_board_label=None`.
    """
    if not picks or db is None:
        return
    keys = [p.get("canonical_key") for p in picks if p.get("canonical_key")]
    if not keys:
        return
    by_key: Dict[str, datetime] = {}
    cursor = db[COLL].find(
        {"sport": sport, "tier": tier, "active": True,
         "canonical_key": {"$in": keys}},
        {"_id": 0, "canonical_key": 1, "first_seen_at": 1},
    )
    for r in await cursor.to_list(length=200):
        fs = r.get("first_seen_at")
        if isinstance(fs, datetime):
            if fs.tzinfo is None:
                fs = fs.replace(tzinfo=timezone.utc)
            by_key[r["canonical_key"]] = fs
    now = _now()
    for p in picks:
        ck = p.get("canonical_key")
        fs = by_key.get(ck)
        if fs is None:
            p["on_board_seconds"] = 0
            p["on_board_minutes"] = 0
            p["on_board_label"]   = None
            continue
        secs = max(0, int((now - fs).total_seconds()))
        p["on_board_seconds"] = secs
        p["on_board_minutes"] = round(secs / 60)
        p["on_board_label"]   = _longevity_label(secs)


# ─── Health probe (read-only, called by /api/health/board) ───────────
def _classify_status(count: int, capacity: int,
                     newest_age: Optional[float],
                     insertions: int, removals: int) -> str:
    """Universal status classifier — pure function, no I/O.

    healthy      → board at capacity, fresh activity, low churn
    underfilled  → count < capacity (initial fill / partial slate)
    stale        → newest pick older than 2h (no fresh activity)
    high_churn   → ≥ 5 REMOVALS in the last hour, OR ≥ 3 removals AND
                   ≥ 3 insertions (active replacement going on).
                   Pure insertions on a previously-empty board do NOT
                   count — that's filling, not churn.
    """
    rem = removals or 0
    ins = insertions or 0
    if rem >= 5 or (rem >= 3 and ins >= 3):
        return "high_churn"
    if count < capacity:
        return "underfilled"
    if newest_age is not None and newest_age > 2 * 3600:
        return "stale"
    return "healthy"


async def _bucket_health(db, sport: str, tier: str,
                         side: Optional[str]) -> Dict[str, Any]:
    cfg = TIER_CONFIG.get(tier) or {}
    capacity = int(cfg.get("capacity_per_side", 10))
    rows = await _load_active(db, sport, tier, side)
    now = _now()
    ages: List[float] = []
    last_update_at: Optional[datetime] = None
    for r in rows:
        fs = r.get("first_seen_at")
        lu = r.get("last_updated_at") or r.get("last_seen_at")
        if isinstance(fs, datetime):
            if fs.tzinfo is None:
                fs = fs.replace(tzinfo=timezone.utc)
            ages.append(max(0.0, (now - fs).total_seconds()))
        if isinstance(lu, datetime):
            if lu.tzinfo is None:
                lu = lu.replace(tzinfo=timezone.utc)
            if last_update_at is None or lu > last_update_at:
                last_update_at = lu

    one_hour_ago = now - timedelta(hours=1)
    insertions = await db[EVENTS_COLL].count_documents({
        "sport": sport, "tier": tier, "side": side,
        "kind": "insertion", "occurred_at": {"$gte": one_hour_ago},
    }) if db is not None else 0
    removals = await db[EVENTS_COLL].count_documents({
        "sport": sport, "tier": tier, "side": side,
        "kind": "removal", "occurred_at": {"$gte": one_hour_ago},
    }) if db is not None else 0

    count = len(rows)
    oldest = max(ages) if ages else None
    newest = min(ages) if ages else None
    avg    = (sum(ages) / len(ages)) if ages else None
    fill_pct = round(count / capacity, 4) if capacity else 0.0

    return {
        "sport":               sport,
        "tier":                tier,
        "side":                side,
        "count":               count,
        "capacity":            capacity,
        "fill_pct":            fill_pct,
        "oldest_pick_age_sec": int(oldest) if oldest is not None else None,
        "newest_pick_age_sec": int(newest) if newest is not None else None,
        "avg_pick_age_sec":    int(avg) if avg is not None else None,
        "insertions_last_hour": int(insertions),
        "removals_last_hour":   int(removals),
        "last_update_at":      last_update_at.isoformat() if last_update_at else None,
        "status":              _classify_status(count, capacity, newest,
                                                insertions, removals),
    }


async def board_health_report(db) -> Dict[str, Any]:
    """Aggregate per (sport, tier, side) status. Universal — discovers
    sports from the persisted state collection, so adding a new sport
    automatically appears here without code change."""
    out: Dict[str, Any] = {
        "generated_at": _now().isoformat(),
        "buckets":      [],
    }
    if db is None:
        return out
    # Discover (sport, tier, side) buckets that have any state row,
    # active or inactive. Even if a bucket has 0 active rows we still
    # want to report `count=0, capacity=N, status=underfilled`.
    sports = sorted(set(await db[COLL].distinct("sport")))
    for sport in sports:
        for tier, cfg in TIER_CONFIG.items():
            if cfg["split_by_side"]:
                sides: Tuple[Optional[str], ...] = ("OVER", "UNDER")
            else:
                sides = (None,)
            for side in sides:
                bucket = await _bucket_health(db, sport, tier, side)
                out["buckets"].append(bucket)
    # Roll-up convenience.
    out["overall_status"] = (
        "high_churn" if any(b["status"] == "high_churn" for b in out["buckets"]) else
        "stale"      if any(b["status"] == "stale"      for b in out["buckets"]) else
        "underfilled" if any(b["status"] == "underfilled" for b in out["buckets"]) else
        "healthy"
    )
    return out


__all__ = [
    "TIER_CONFIG",
    "rank_tuple",
    "ensure_indexes",
    "reconcile",
    "get_published_board",
    "stamp_longevity_on_picks",
    "board_health_report",
    "_reconcile_in_memory",
    "_classify_status",
    "_longevity_label",
]

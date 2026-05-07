"""
Cached-board freshness stamper (2026-05-07 P0 §3 follow-up).
============================================================

Purpose
-------
`{nba,mlb}_cached_board` carries the player-grain enrichment cache the
ferrari tier endpoints overlay onto live picks. Pre-2026-05-07 the
writers emitted only doc-level `sport` (and a stringy `built_at` on
the MLB rebuild path) — there was NO doc-level `updated_at` /
`last_publish_ts`, so SLO §3 (`tier_freshness`) had no canonical signal
to assert against and always FAILed.

This module provides ONE writer-side helper, `stamp_cached_board_freshness`,
that stamps the canonical Phase 4 freshness contract on every doc in
`{sport}_cached_board`:

    {
        "sport":                          "<sport>",
        "version_tag":                    "<sport-tag>",
        "updated_at":                     <datetime UTC, aware>,
        "last_publish_ts":                <datetime UTC, aware>,
        "source_score_max_scored_at":     <datetime UTC, aware> | None,
    }

The contract is documented and asserted by SLO §3:

    invariant_1:  updated_at == last_publish_ts
    invariant_2:  updated_at >= source_score_max_scored_at
    recency:      now - updated_at <= CACHED_BOARD_MAX_AGE_S

Callers
-------
    * services/master_sync.py
        End-of-run for both NBA and MLB — covers NBA's pure-enrichment
        flow (no full rebuild) and the MLB hourly rebuild path.

    * services/mlb_cached_board_builder.py
        Inline at insert_many() time — guarantees freshness fields are
        present from the moment a freshly-rebuilt MLB board lands,
        even before master_sync's Step 6 vision-intel mirror runs.

This is a writer-only metadata stamp. It DOES NOT touch ingestion,
scoring, gates, vision-intel, queue, frontend, or pick payloads.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Sport → SemVer-ish version tag carried on every cached_board doc.
# Bumping the tag is a side-effect-free signal for board-shape rollouts.
_VERSION_TAGS: Dict[str, str] = {
    "nba": "nba-cb-v1",
    "mlb": "mlb-cb-v1",
}


def cached_board_collection_name(sport: str) -> str:
    """Resolve `<sport>_cached_board`. Inlined to avoid a hard
    dependency on `services.config.collection_names.COLL` for callers
    outside the master_sync module tree."""
    return f"{sport.lower()}_cached_board"


async def _max_scored_at(db, sport: str) -> Optional[datetime]:
    """Latest `scored_at` in `<sport>_prop_scores` — the canonical
    "score publish" timestamp the cached_board is built FROM. Returns
    None if the collection is empty (allowed; recency check on
    `updated_at` still gates board freshness).

    `scored_at` is currently persisted as an ISO-8601 STRING in
    `{sport}_prop_scores` (see services/scoring/prop_scores_store.py).
    Parse it back to an aware UTC datetime so the freshness contract
    can stamp a typed value on cached_board (which the SLO §3 invariant
    `updated_at >= source_score_max_scored_at` relies on)."""
    coll = f"{sport.lower()}_prop_scores"
    try:
        doc = await db[coll].find_one(
            {"scored_at": {"$exists": True, "$ne": None}},
            projection={"_id": 0, "scored_at": 1},
            sort=[("scored_at", -1)],
        )
    except Exception:
        logger.exception(
            f"[BOARD_FRESHNESS:{sport}] failed to read max scored_at from {coll}"
        )
        return None
    if not doc:
        return None
    ts = doc.get("scored_at")
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts
    if isinstance(ts, str):
        try:
            parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    return None


def build_freshness_stamp(
    sport: str,
    *,
    now: Optional[datetime] = None,
    source_score_max_scored_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Compose the canonical freshness payload for one
    `{sport}_cached_board` doc. Pure — exposed for the
    mlb_cached_board_builder insert-time path and unit tests."""
    sport_l = (sport or "").lower()
    ts = now or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return {
        "sport": sport_l,
        "version_tag": _VERSION_TAGS.get(sport_l, f"{sport_l}-cb-v1"),
        "updated_at": ts,
        "last_publish_ts": ts,
        "source_score_max_scored_at": source_score_max_scored_at,
    }


async def stamp_cached_board_freshness(
    db,
    sport: str,
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Stamp the canonical freshness fields on EVERY doc in
    `<sport>_cached_board`. Single bulk `update_many({}, $set=...)`.
    Idempotent. Best-effort — a failure logs but does not raise so
    master_sync's outer try/except keeps the existing semantics.

    Returns a metrics dict suitable for nesting under
    `metrics["steps"]["7_cached_board_freshness_stamp"]`."""
    sport_l = (sport or "").lower()
    coll_name = cached_board_collection_name(sport_l)
    started = datetime.now(timezone.utc)

    score_max = await _max_scored_at(db, sport_l)
    stamp = build_freshness_stamp(
        sport_l, now=now, source_score_max_scored_at=score_max,
    )

    try:
        coll = db[coll_name]
        result = await coll.update_many({}, {"$set": stamp})
        matched = getattr(result, "matched_count", None)
        modified = getattr(result, "modified_count", None)
    except Exception as exc:
        logger.warning(
            f"[BOARD_FRESHNESS:{sport_l}] update_many failed on {coll_name}: {exc}"
        )
        return {
            "sport": sport_l,
            "collection": coll_name,
            "matched": 0,
            "modified": 0,
            "duration_seconds": (datetime.now(timezone.utc) - started).total_seconds(),
            "error": str(exc),
        }

    duration = (datetime.now(timezone.utc) - started).total_seconds()
    logger.info(
        f"[BOARD_FRESHNESS:{sport_l}] stamped {matched} doc(s) "
        f"({modified} modified) on {coll_name} "
        f"updated_at={stamp['updated_at'].isoformat()} "
        f"source_score_max={stamp['source_score_max_scored_at']} "
        f"in {duration:.2f}s"
    )
    return {
        "sport": sport_l,
        "collection": coll_name,
        "matched": int(matched or 0),
        "modified": int(modified or 0),
        "updated_at": stamp["updated_at"].isoformat(),
        "last_publish_ts": stamp["last_publish_ts"].isoformat(),
        "source_score_max_scored_at": (
            stamp["source_score_max_scored_at"].isoformat()
            if stamp["source_score_max_scored_at"] else None
        ),
        "version_tag": stamp["version_tag"],
        "duration_seconds": duration,
    }


__all__ = [
    "build_freshness_stamp",
    "cached_board_collection_name",
    "stamp_cached_board_freshness",
]

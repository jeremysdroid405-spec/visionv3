"""
Universal Multi-Sport Board Engine — Step 5: Real-Time Ingest
==============================================================

Sport-agnostic event-driven ingest path. When the odds-sync layer publishes
a `BoardEvent(event_type='new_props', ...)` with a set of canonical keys,
this engine scopes the scoring stack to JUST those keys and UPSERTs the
resulting score docs into `{sport}_prop_scores`. The universal reader
(`services/board/reader.py::get_board`) immediately reflects the new
active props on the board without any rebuild / atomic-swap.

Design:
  - ONE handler for every sport. No sport branches. The engine
    dispatches through the sport registry (`services/board/adapters/`).
  - Uses the same scoring stack as the hourly full-recompute — zero
    divergence in math. Only the WRITE mode differs (upsert vs. replace).
  - Per-sport asyncio.Lock prevents concurrent scoped recomputes for the
    same sport from racing over the pool.
  - Non-blocking in the hot path of odds-sync: the event publish returns
    immediately; scoring + upsert happens in this subscriber.
  - Never raises into the event bus. Logs and returns on error.

Usage from a publisher (e.g. odds_sync_service.py):

    from services.event_bus import get_event_bus, BoardEvent

    new_keys = [...]  # canonical keys discovered in this sync delta
    if new_keys:
        await get_event_bus().publish(BoardEvent(
            sport='nba', event_type='new_props', source='odds_sync',
            metadata={'canonical_keys': new_keys},
        ))

Subscribing at startup (see server.py):

    from services.board.engine import subscribe_new_props_handler
    subscribe_new_props_handler(db)
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from services.board.adapters import get_adapter, registered_sports
from services.event_bus import BoardEvent, get_event_bus
from services.scoring.adapters import get_scoring_adapter
from services.scoring.prop_scores_store import write_versioned_scores
from services.scoring.recompute import recompute_sport

logger = logging.getLogger(__name__)

# Per-sport lock to serialise concurrent scoped recomputes.
_sport_locks: Dict[str, asyncio.Lock] = {
    sport: asyncio.Lock() for sport in registered_sports()
}

# Module-level stats (read by observability endpoints).
_STATS: Dict[str, Dict[str, Any]] = {
    sport: {
        "events_received": 0,
        "events_processed": 0,
        "events_skipped": 0,
        "props_upserted": 0,
        "last_event_at": None,
        "last_source": None,
        "last_keys_count": 0,
        "last_written": 0,
        "last_skipped": 0,
        "last_duration_ms": None,
        "last_error": None,
    }
    for sport in registered_sports()
}


def stats_snapshot() -> Dict[str, Dict[str, Any]]:
    """Read-only copy of per-sport Step-5 ingest stats."""
    return {k: dict(v) for k, v in _STATS.items()}


async def on_new_props(
    db,
    sport: str,
    canonical_keys: List[str],
    source: str = "unknown",
) -> Dict[str, Any]:
    """Score ONLY these canonical keys and UPSERT them into the master
    pool. Single sport, single pass.

    Returns a compact stats dict. Never raises.
    """
    started_at = time.monotonic()
    sport_key = (sport or "").strip().lower()
    keys = [k for k in (canonical_keys or []) if k]
    result: Dict[str, Any] = {
        "sport": sport_key,
        "requested_keys": len(keys),
        "matched_keys": 0,
        "written": 0,
        "skipped": 0,
        "duration_ms": 0,
        "source": source,
    }
    _STATS.setdefault(sport_key, {"events_received": 0})["events_received"] += 1

    if not keys:
        _STATS[sport_key]["events_skipped"] = _STATS[sport_key].get("events_skipped", 0) + 1
        return {**result, "reason": "empty_keys"}

    try:
        board_adapter = get_adapter(sport_key)
        scoring_adapter = get_scoring_adapter(sport_key)
    except Exception as e:
        logger.exception(f"[BOARD_ENGINE] Unknown sport '{sport_key}': {e}")
        _STATS[sport_key]["events_skipped"] = _STATS[sport_key].get("events_skipped", 0) + 1
        _STATS[sport_key]["last_error"] = str(e)
        return {**result, "reason": "unknown_sport"}

    keys_set: Set[str] = set(keys)
    lock = _sport_locks.setdefault(sport_key, asyncio.Lock())

    # --- DB-side pre-filter: parse canonical_keys to extract event_id +
    # player_name subsets. Canonical key format is
    # '{sport}|{event_id}|{player}|{stat}|{line}|{side}'. Passing those
    # sets as $in filters cuts the O(N_live_props) scan to O(matched)
    # before the engine ever touches build_context. ---
    event_ids: Set[str] = set()
    player_names: Set[str] = set()
    for k in keys_set:
        parts = k.split("|")
        if len(parts) >= 4:
            if parts[1]:
                event_ids.add(parts[1])
            if parts[2]:
                player_names.add(parts[2])

    async with lock:
        live_coll_name = scoring_adapter.live_props_collection
        # Narrow query leveraging existing NBA dg_live_props indexes on
        # event_id / player_name. If the canonical_keys carry no usable
        # event_id/player (e.g., degenerate keys) we fall back to full
        # scan — which is how the un-optimised path worked before.
        query: Dict[str, Any] = {}
        if event_ids:
            query["event_id"] = {"$in": list(event_ids)}
        if player_names:
            query["player_name"] = {"$in": list(player_names)}
        try:
            raw_docs: List[Dict[str, Any]] = await db[live_coll_name].find(
                query, {"_id": 0}
            ).to_list(length=None)
        except Exception as e:
            logger.exception(
                f"[BOARD_ENGINE] {sport_key} pre-filtered load failed: {e}"
            )
            _STATS[sport_key]["events_skipped"] = _STATS[sport_key].get("events_skipped", 0) + 1
            _STATS[sport_key]["last_error"] = str(e)
            return {**result, "reason": "load_failed", "error": str(e)}

        # Fast key match: O(N_narrow) string interpolations. 10,000x
        # faster than calling build_context per prop.
        matched: List[Dict[str, Any]] = []
        fast_misses: List[Dict[str, Any]] = []
        for p in raw_docs:
            ck = board_adapter.canonical_key(p)
            if ck is None:
                fast_misses.append(p)
            elif ck in keys_set:
                matched.append(p)

        # Defensive fallback — if the fast-path missed any props (e.g.
        # a sport whose adapter override hasn't been written yet, or a
        # malformed doc), run build_context on just those fast_misses
        # to preserve correctness without paying the cost on every prop.
        if fast_misses and len(matched) < len(keys_set):
            for p in fast_misses:
                try:
                    ctx = await scoring_adapter.build_context(db, p, {})
                except Exception:
                    continue
                if ctx and ctx.canonical_key in keys_set:
                    matched.append(p)

        if not matched:
            duration_ms = int((time.monotonic() - started_at) * 1000)
            logger.info(
                f"[BOARD_ENGINE] {sport_key} on_new_props src={source} "
                f"requested={len(keys)} matched=0 (no live props match) {duration_ms}ms"
            )
            _STATS[sport_key]["events_processed"] += 1
            _STATS[sport_key]["last_event_at"] = datetime.now(timezone.utc).isoformat()
            _STATS[sport_key]["last_source"] = source
            _STATS[sport_key]["last_keys_count"] = len(keys)
            _STATS[sport_key]["last_written"] = 0
            _STATS[sport_key]["last_skipped"] = 0
            _STATS[sport_key]["last_duration_ms"] = duration_ms
            _STATS[sport_key]["last_error"] = None
            return {**result, "duration_ms": duration_ms, "reason": "no_match"}

        try:
            # 2026-04-29 — Step 6 cutover (path 1c per ROADMAP):
            # the real-time engine now writes DIRECTLY to the canonical
            # live tag. Previously this routed to a `<canonical>-rt`
            # shadow tag (e.g. `final-mlb-rt-rt`) which the live UI
            # reader did not consult — so newly posted props sat dark
            # for up to 60 min until the hourly master_sync rebuilt
            # the canonical tag.
            #
            # Hourly master_sync still runs as a periodic full
            # rebuild for the SAME canonical tag using `mode=replace`,
            # which is safe because master_sync re-loads every prop in
            # `{sport}_live_props` (kept fresh by the same watcher
            # that fires this engine). Stale realtime upserts cannot
            # outlive an hourly rebuild because the rebuild's load
            # already includes the prop.
            #
            # Drift audit (services/board/drift_audit.py) and the
            # `final-{sport}` baseline tag (written separately by
            # master_sync) remain untouched — they observe the same
            # canonical state without needing a separate write tag.
            rt_version_tag = board_adapter.version_tag
            rc = await recompute_sport(
                db=db,
                sport=sport_key,
                version_tag=rt_version_tag,
                dry_run=False,
                limit=None,
                override_config=None,
                write_mode="upsert",
                props=matched,
            )
        except Exception as e:
            logger.exception(
                f"[BOARD_ENGINE] {sport_key} on_new_props recompute failed: {e}"
            )
            _STATS[sport_key]["events_skipped"] = _STATS[sport_key].get("events_skipped", 0) + 1
            _STATS[sport_key]["last_error"] = str(e)
            return {**result, "reason": "recompute_failed", "error": str(e)}

    # Feed the drift-audit ledger: every real-time upsert captured as
    # a snapshot of tier / vision_score so the 48h Step 6 A/B window
    # can prove the full-rebuild coordinator converges on the same
    # scores. Two layers — in-memory ring buffer (fast, transient) +
    # persistent MongoDB collection (durable, 72h TTL).
    try:
        from services.board.drift_audit import (
            record_realtime_upsert, persist_entries,
        )
        record_realtime_upsert(
            sport=sport_key,
            score_docs=rc.get("score_docs") or [],
            source=source,
        )
        # Synchronous — adds one insert_many round-trip per event.
        # Measured ≤ 5 ms in the E2E verifier. Never raises.
        await persist_entries(
            db=db,
            sport=sport_key,
            score_docs=rc.get("score_docs") or [],
            source=source,
        )
    except Exception as e:
        logger.warning(f"[BOARD_ENGINE] drift-audit record skipped: {e}")

    return await _finalize_result(
        result, rc, sport_key, started_at, source
    )


async def _finalize_result(
    result: Dict[str, Any],
    rc: Dict[str, Any],
    sport_key: str,
    started_at: float,
    source: str,
) -> Dict[str, Any]:
    duration_ms = int((time.monotonic() - started_at) * 1000)
    result.update({
        "matched_keys": int(rc.get("processed", 0)),
        "written": int(rc.get("written", 0)),
        "skipped": int(rc.get("skipped", 0)),
        "duration_ms": duration_ms,
    })
    s = _STATS[sport_key]
    s["events_processed"] += 1
    s["props_upserted"] += int(rc.get("written", 0))
    s["last_event_at"] = datetime.now(timezone.utc).isoformat()
    s["last_source"] = source
    s["last_keys_count"] = int(result["requested_keys"])
    s["last_written"] = int(rc.get("written", 0))
    s["last_skipped"] = int(rc.get("skipped", 0))
    s["last_duration_ms"] = duration_ms
    s["last_error"] = None
    logger.info(
        f"[BOARD_ENGINE] {sport_key} on_new_props src={source} "
        f"requested={result['requested_keys']} matched={result['matched_keys']} "
        f"written={result['written']} skipped={result['skipped']} "
        f"{duration_ms}ms"
    )
    return result


# -----------------------------------------------------------------------------
# Event-bus handler
# -----------------------------------------------------------------------------

async def _handle_new_props_event(db, event: BoardEvent) -> None:
    """Async handler wired onto the event bus for event_type='new_props'.
    Pulls the canonical_keys list out of the event metadata.
    """
    if event.event_type != "new_props":
        return
    keys = []
    md = event.metadata or {}
    raw_keys = md.get("canonical_keys") or []
    if isinstance(raw_keys, (list, tuple, set)):
        keys = [str(k) for k in raw_keys if k]
    await on_new_props(
        db=db,
        sport=event.sport,
        canonical_keys=keys,
        source=event.source or "event_bus",
    )


def subscribe_new_props_handler(db) -> None:
    """Register the real-time ingest subscriber on the event bus.
    Idempotent — call once from server startup."""
    bus = get_event_bus()

    async def _subscriber(event: BoardEvent):
        await _handle_new_props_event(db, event)

    bus.subscribe(_subscriber, event_types={"new_props"})
    logger.info(
        "[BOARD_ENGINE] Subscribed real-time 'new_props' handler "
        f"(registered sports: {registered_sports()})"
    )


__all__ = [
    "on_new_props",
    "subscribe_new_props_handler",
    "stats_snapshot",
]

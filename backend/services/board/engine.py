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

    async with lock:
        # Load raw props from live_props, then filter to those whose
        # scoring-adapter canonical_key is in the target set. We filter
        # BEFORE calling recompute so the heavy scoring work runs only
        # on matched props. `get_scoring_adapter()` returns a fresh
        # instance every call, so we pre-load + filter here and pass
        # the result directly into recompute via the `props` kwarg.
        try:
            all_props = await scoring_adapter.load_live_props(db, limit=None)
        except Exception as e:
            logger.exception(
                f"[BOARD_ENGINE] {sport_key} load_live_props failed: {e}"
            )
            _STATS[sport_key]["events_skipped"] = _STATS[sport_key].get("events_skipped", 0) + 1
            _STATS[sport_key]["last_error"] = str(e)
            return {**result, "reason": "load_failed", "error": str(e)}

        matched: List[Dict[str, Any]] = []
        for p in all_props:
            try:
                ctx = await scoring_adapter.build_context(db, p, {})
            except Exception:
                continue
            if ctx is None:
                continue
            if ctx.canonical_key in keys_set:
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
            rc = await recompute_sport(
                db=db,
                sport=sport_key,
                version_tag=board_adapter.version_tag,
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

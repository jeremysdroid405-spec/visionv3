"""
Delta Engine — Admin Inspect Endpoint (D1)
==========================================
Read-only introspection surface for the PropVision near-real-time Delta
Engine. Performs zero writes. Does not trigger any upstream API calls.

Endpoint:
  GET /api/v3/admin/delta/inspect/{sport}

Response:
  {
    "sport": "nba",
    "watermark_utc": "2026-04-21T14:32:00+00:00" | null,
    "upstream_lock_held": true | false,
    "upstream_lock_detail": {...},
    "dirty_count":   <int>,
    "updated_count": <int>,
    "new_count":     <int>,
    "retired_count": <int>,
    "live_props_count":       <int>,
    "active_live_props_count":<int>,
    "scored_rt_count":        <int>,
    "missing_updated_at":     <int>,
    "sample_updated_keys": ["...", ...],
    "sample_new_keys":     ["...", ...],
    "sample_retired_keys": ["...", ...],
    "duration_ms": <int>
  }
"""
from __future__ import annotations

import logging
import time
from fastapi import APIRouter, HTTPException

from services.delta.detector import detect_changed_props
from services.scoring.adapters import SUPPORTED_SPORTS

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Delta Admin"])

# Dependency injection
_db = None


def set_delta_admin_db(db):
    global _db
    _db = db


def _check_upstream_lock(sport: str) -> dict:
    """Return current upstream-lock state for `sport`.

    D4 (2026-04-21): this now reads the real `UpstreamSyncLock` singleton.
    We still cross-reference `RebuildCoordinator._master_sync_state` so
    callers see WHO holds the lock (run_id + started_at) rather than
    just a boolean.
    """
    try:
        from services.upstream_sync_lock import get_upstream_sync_lock
        from services.rebuild_coordinator import get_coordinator
        lock = get_upstream_sync_lock()
        held = lock.is_held(sport)
        coord_state = (
            getattr(get_coordinator(), "_master_sync_state", {}).get(sport, {})
            or {}
        )
        last_run = coord_state.get("last_run") or {}
        detail = lock.describe(sport)
        detail.update({
            "coord_in_progress": bool(coord_state.get("in_progress")),
            "run_id": coord_state.get("run_id"),
            "started_at": coord_state.get("started_at"),
            "last_run_completed_at": last_run.get("completed_at"),
            "last_run_success": last_run.get("success"),
        })
        detail["held"] = held
        return detail
    except Exception as exc:
        logger.warning(f"[DELTA_ADMIN] upstream lock probe failed: {exc}")
        return {"held": False, "source": "probe_failed", "error": str(exc)}


@router.get("/v3/admin/delta/inspect/{sport}")
async def inspect_delta(sport: str):
    """Inspect the delta engine's current view of the world for a sport.

    Read-only: no writes, no upstream fetches, no rescoring.
    """
    if _db is None:
        raise HTTPException(status_code=500, detail="delta admin db not wired")

    sport = (sport or "").lower()
    if sport not in SUPPORTED_SPORTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported sport: {sport!r}. Supported: {list(SUPPORTED_SPORTS)}",
        )

    t0 = time.monotonic()
    result = await detect_changed_props(_db, sport)
    lock = _check_upstream_lock(sport)

    summary = result.to_summary()
    summary["upstream_lock_held"] = lock["held"]
    summary["upstream_lock_detail"] = lock
    summary["duration_ms"] = int((time.monotonic() - t0) * 1000)
    return summary


@router.get("/v3/admin/delta/inspect")
async def inspect_all_sports():
    """Aggregate inspect across all supported sports."""
    if _db is None:
        raise HTTPException(status_code=500, detail="delta admin db not wired")

    t0 = time.monotonic()
    out = {}
    for sport in SUPPORTED_SPORTS:
        result = await detect_changed_props(_db, sport)
        lock = _check_upstream_lock(sport)
        s = result.to_summary()
        s["upstream_lock_held"] = lock["held"]
        s["upstream_lock_detail"] = lock
        out[sport] = s
    return {
        "sports": list(SUPPORTED_SPORTS),
        "per_sport": out,
        "duration_ms": int((time.monotonic() - t0) * 1000),
    }


# ---------------------------------------------------------------------------
# D3+D4 — manual tick trigger + engine status + lock inspection
# ---------------------------------------------------------------------------

@router.post("/v3/admin/delta/run-once/{sport}")
async def delta_run_once(sport: str):
    """Manually run ONE delta-engine tick for `sport`.

    D4 does NOT auto-start the engine (D5's job). This endpoint lets
    operators verify the delta pipeline end-to-end on demand.
    """
    if _db is None:
        raise HTTPException(status_code=500, detail="delta admin db not wired")

    sport = (sport or "").lower()
    if sport not in SUPPORTED_SPORTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported sport: {sport!r}. Supported: {list(SUPPORTED_SPORTS)}",
        )

    from services.delta_engine import get_delta_engine
    engine = get_delta_engine(_db)
    result = await engine.tick(sport)
    return result.to_dict()


@router.get("/v3/admin/delta/engine-status")
async def delta_engine_status():
    """Observability view of the DeltaEngine's running state."""
    if _db is None:
        raise HTTPException(status_code=500, detail="delta admin db not wired")
    from services.delta_engine import get_delta_engine
    from services.upstream_sync_lock import get_upstream_sync_lock
    from services.scheduled_sports import describe_delta_engine_loops
    from services.delta_metrics import counters_snapshot
    engine = get_delta_engine(_db)
    lock = get_upstream_sync_lock()
    return {
        "engine": engine.describe(),
        "upstream_lock": lock.describe(),
        "startup_loops": describe_delta_engine_loops(),
        # Phase D6 — counters snapshot alongside the in-memory engine state.
        "metrics": counters_snapshot(),
    }


# ---------------------------------------------------------------------------
# D6 — rolling tick history + Prometheus metrics
# ---------------------------------------------------------------------------

@router.get("/v3/admin/delta/tick-history/{sport}")
async def delta_tick_history(sport: str, n: int = 50):
    """Return the last `n` delta ticks for `sport`, newest LAST.

    Each entry includes: timestamp, duration, dirty/updated/new/retired
    counts, rescored count, skipped_reason, upstream_lock_held, and
    batch-cap diagnostics.
    """
    if _db is None:
        raise HTTPException(status_code=500, detail="delta admin db not wired")
    sport = (sport or "").lower()
    if sport not in SUPPORTED_SPORTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported sport: {sport!r}. Supported: {list(SUPPORTED_SPORTS)}",
        )
    from services.delta_metrics import history_snapshot, HISTORY_BUFFER_SIZE
    n = max(1, min(n, HISTORY_BUFFER_SIZE))
    return {
        "sport": sport,
        "n_requested": n,
        "buffer_capacity": HISTORY_BUFFER_SIZE,
        "ticks": history_snapshot(sport, n=n),
    }


@router.get("/v3/admin/delta/tick-history")
async def delta_tick_history_all(n: int = 20):
    """Per-sport compact tick history across all supported sports."""
    if _db is None:
        raise HTTPException(status_code=500, detail="delta admin db not wired")
    from services.delta_metrics import history_snapshot, HISTORY_BUFFER_SIZE
    n = max(1, min(n, HISTORY_BUFFER_SIZE))
    return {
        "n_requested": n,
        "buffer_capacity": HISTORY_BUFFER_SIZE,
        "per_sport": {
            s: history_snapshot(s, n=n) for s in SUPPORTED_SPORTS
        },
    }


@router.get("/v3/admin/delta/metrics")
async def delta_metrics_prometheus():
    """Prometheus text exposition of delta-engine metrics.

    Content-Type: text/plain; version=0.0.4
    """
    from fastapi.responses import PlainTextResponse
    from services.delta_metrics import prometheus_text
    return PlainTextResponse(
        content=prometheus_text(),
        headers={"Content-Type": "text/plain; version=0.0.4; charset=utf-8"},
    )

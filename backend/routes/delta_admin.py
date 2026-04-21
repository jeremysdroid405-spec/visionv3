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
    """Proxy signal for `UpstreamSyncLock` (full implementation lands in D4).

    D1 sources this from the existing `RebuildCoordinator._master_sync_state`
    — the only place a full sync's `in_progress` flag currently lives.
    When D4 introduces the real lock, this helper will read from it
    directly without changing the response shape.
    """
    try:
        from services.rebuild_coordinator import get_coordinator
        coord = get_coordinator()
        state = getattr(coord, "_master_sync_state", {}).get(sport, {}) or {}
        last_run = state.get("last_run") or {}
        return {
            "held": bool(state.get("in_progress")),
            "source": "RebuildCoordinator._master_sync_state",
            "run_id": state.get("run_id"),
            "started_at": state.get("started_at"),
            "last_run_completed_at": last_run.get("completed_at"),
            "last_run_success": last_run.get("success"),
        }
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

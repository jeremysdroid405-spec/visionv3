"""
Adaptive Sync Routes
====================
Adaptive Sync Engine endpoints for mission-critical polling and data freshness.
"""
from fastapi import APIRouter, HTTPException
from typing import Optional
import logging

from services.engines.adaptive_sync_engine import get_adaptive_sync_engine

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Adaptive Sync"])


def get_engine():
    """Get the adaptive sync engine instance."""
    engine = get_adaptive_sync_engine()
    if not engine:
        raise HTTPException(status_code=500, detail="Adaptive Sync Engine not initialized")
    return engine


@router.get("/v3/sync-status")
async def get_adaptive_sync_status():
    """
    ADAPTIVE SYNC ENGINE - Get Current Sync Status
    
    Returns:
    - last_sync: When data was last refreshed
    - sync_age_display: Human-readable time since last sync (e.g., "45s ago")
    - engine_status: "running" | "stopped"
    - active_games: Number of games being tracked
    - mission_critical_games: Games within 60 mins of tip-off
    - game_registry: Full list of tracked games with their polling status
    
    Polling Tiers:
    - Standby (>6hrs): Refresh every 60 minutes
    - Active (1-6hrs): Refresh every 10 minutes
    - Mission Critical (<60mins): Refresh every 60 seconds
    - Post-Tip: Cease polling for that game
    """
    engine = get_adaptive_sync_engine()
    if not engine:
        return {"error": "Adaptive Sync Engine not initialized", "engine_status": "disabled"}
    
    status = await engine.get_sync_status()
    return status


@router.get("/v3/stale-intel-check")
async def check_for_stale_intel(game_id: Optional[str] = None):
    """
    STALE INTEL DETECTION - Check for outdated data in mission-critical windows.
    
    If data is older than 5 minutes during a mission-critical window (<60 mins to tip),
    this endpoint returns a warning.
    
    Args:
    - game_id: Optional - Check specific game only
    
    Returns:
    - has_stale_intel: True if any mission-critical data is stale
    - stale_games: List of games with stale data
    - threshold_seconds: Current stale threshold (300 = 5 minutes)
    """
    engine = get_adaptive_sync_engine()
    if not engine:
        return {"error": "Adaptive Sync Engine not initialized", "has_stale_intel": False}
    
    result = await engine.check_stale_intel(game_id)
    return result


@router.post("/v3/priority-refresh")
async def trigger_priority_refresh(game_id: Optional[str] = None):
    """
    PRIORITY REFRESH - Trigger immediate high-priority data refresh.
    
    Use this when stale intel is detected during mission-critical windows.
    Bypasses normal polling schedule for immediate refresh.
    
    Args:
    - game_id: Optional - Refresh specific game only
    
    Returns:
    - updated: Number of records updated
    - timestamp: Refresh completion time
    - trigger: "priority_refresh"
    """
    engine = get_engine()
    result = await engine.trigger_priority_refresh(game_id)
    return result


@router.get("/v3/intel-freshness")
async def get_intel_with_freshness(limit: int = 100):
    """
    INTEL WITH FRESHNESS - Get cached board data with freshness indicators.
    
    Returns all cached odds data with:
    - last_updated timestamp
    - freshness.seconds_ago: How old the data is
    - freshness.display: Human-readable (e.g., "45s ago")
    - freshness.is_stale: True if older than 5 minutes
    
    Use this for frontend to display "Intel updated 45s ago" on cards.
    """
    from fastapi.responses import JSONResponse
    import json
    
    try:
        engine = get_engine()
        result = await engine.get_board_with_freshness(limit)
        
        # Ensure no ObjectIds in response by converting to JSON and back
        safe_result = json.loads(json.dumps(result, default=str))
        return JSONResponse(content=safe_result)
    except Exception as e:
        return JSONResponse(content={
            "entries": [],
            "count": 0,
            "error": str(e),
            "message": "Failed to retrieve freshness data"
        })


@router.post("/v3/adaptive-sync/start")
async def start_adaptive_sync():
    """Start the adaptive sync engine (if stopped)."""
    engine = get_engine()
    await engine.start()
    return {"status": "started", "message": "Adaptive Sync Engine started"}


@router.post("/v3/adaptive-sync/stop")
async def stop_adaptive_sync():
    """Stop the adaptive sync engine."""
    engine = get_engine()
    await engine.stop()
    return {"status": "stopped", "message": "Adaptive Sync Engine stopped"}

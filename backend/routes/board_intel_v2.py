"""
Board Intel Routes V2 - Comprehensive Board Intelligence Endpoints
===================================================================
Extracted from server.py - all board intelligence and sync endpoints.
"""
from fastapi import APIRouter, HTTPException
from typing import Dict, Any, Optional
import logging

from board_intelligence_engine import get_board_intel_engine
from game_lock_engine import get_game_lock_engine

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Board Intelligence"])

# Reference to DemonGoblinEngine and db (set via dependency injection)
_demon_goblin_engine = None
_db = None


def set_board_intel_deps(db, demon_goblin_engine_class):
    """Set dependencies for board intel routes."""
    global _demon_goblin_engine, _db
    _db = db
    _demon_goblin_engine = demon_goblin_engine_class


def get_dg_engine():
    """Get a new DemonGoblinEngine instance."""
    if _demon_goblin_engine is None or _db is None:
        raise HTTPException(status_code=500, detail="Board Intel dependencies not initialized")
    return _demon_goblin_engine(_db)


@router.get("/v3/board-intel/status")
async def get_board_intel_status():
    """
    BOARD INTELLIGENCE STATUS
    
    Returns:
    - last_sync_time: When data was last synced
    - last_sync_type: "primary" (full + Vision) or "delta" (odds only)
    - time_since_sync: "MM:SS" format
    - time_since_sync_display: "Last Synced: MM:SS" for footer display
    - next_scheduled_sync: Next sync time and type
    - scheduler_running: Whether automated scheduler is active
    """
    try:
        engine = get_board_intel_engine()
        await engine.initialize()
        status = await engine.get_sync_status()
        return status
    except Exception as e:
        return {
            "error": str(e),
            "time_since_sync_display": "Sync status unavailable",
            "scheduler_running": False
        }


@router.post("/v3/board-intel/primary-sync")
async def run_primary_sync():
    """
    PRIMARY SYNC (Manual Trigger)
    
    Runs a full global fetch with Vision AI for all Goblins and Demons.
    Normally scheduled for 10:30 AM ET.
    """
    try:
        board_intel = get_board_intel_engine()
        await board_intel.initialize()
        
        dg_engine = get_dg_engine()
        
        result = await board_intel.run_primary_sync(dg_engine)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/v3/board-intel/delta-refresh")
async def run_delta_refresh():
    """
    DELTA REFRESH (Manual Trigger)
    
    Updates line and price values for existing players.
    - New Entry: Triggers one-time Vision AI for new players
    - Removal: Removes players whose lines are pulled
    
    Normally scheduled for 1:45 PM, 4:00 PM, 5:45 PM, 7:00 PM ET.
    """
    try:
        board_intel = get_board_intel_engine()
        await board_intel.initialize()
        
        dg_engine = get_dg_engine()
        
        result = await board_intel.run_delta_refresh(dg_engine)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/v3/board-intel/start-scheduler")
async def start_board_intel_scheduler():
    """
    START AUTOMATED SCHEDULER
    
    Starts background tasks for:
    - Primary Sync at 10:30 AM ET
    - Delta Refreshes at 1:45 PM, 4:00 PM, 5:45 PM, 7:00 PM ET
    - Live Ticker handover every 60 seconds
    """
    try:
        board_intel = get_board_intel_engine()
        await board_intel.initialize()
        
        dg_engine = get_dg_engine()
        lock_engine = get_game_lock_engine()
        
        await board_intel.start_scheduler(dg_engine, lock_engine)
        
        return {
            "status": "started",
            "message": "Board Intelligence scheduler started",
            "schedule": {
                "primary_sync": "10:30 AM ET (Full + Vision AI)",
                "delta_refreshes": ["1:45 PM", "4:00 PM", "5:45 PM", "7:00 PM"],
                "live_ticker": "Every 60 seconds"
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/v3/board-intel/stop-scheduler")
async def stop_board_intel_scheduler():
    """Stop the automated scheduler."""
    try:
        board_intel = get_board_intel_engine()
        board_intel.stop_scheduler()
        return {"status": "stopped", "message": "Board Intelligence scheduler stopped"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/v3/live-ticker")
async def get_live_ticker():
    """
    LIVE TICKER - Games that have started
    
    Returns games that have been moved from the betting board to the live ticker.
    Updated every 60 seconds when games start.
    """
    try:
        board_intel = get_board_intel_engine()
        await board_intel.initialize()
        games = await board_intel.get_live_ticker_games()
        return {
            "live_games": games,
            "count": len(games)
        }
    except Exception as e:
        return {"live_games": [], "count": 0, "error": str(e)}


@router.post("/v3/board-intel/early-bird")
async def run_early_bird_scan():
    """
    EARLY BIRD SCAN (8:15 AM ET - Manual Trigger)
    
    - First global fetch for star players
    - Creates "Scouting Mission Briefing" cards for games without lines
    - Smart Anchor Vision: Analyzes Season Avg vs Opponent Defense
    
    Returns projections for players awaiting official lines.
    """
    try:
        board_intel = get_board_intel_engine()
        await board_intel.initialize()
        
        dg_engine = get_dg_engine()
        
        result = await board_intel.run_early_bird_scan(dg_engine)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/v3/scouting-projections")
async def get_scouting_projections():
    """
    SCOUTING PROJECTIONS
    
    Returns "Scouting Mission Briefing" cards for players awaiting official lines.
    These are star players with projected stats but no live betting lines yet.
    
    Display with "Scouting" badge (orange themed) in the UI.
    
    Each projection includes:
    - player_name
    - team, opponent
    - status: "Awaiting Official Mission Parameters"
    - projections: {points, rebounds, assists, pra}
    - season_avg: Player's season averages
    - last_3_avg: Performance in last 3 games
    - smart_anchor_vision: AI analysis of expected line
    """
    try:
        board_intel = get_board_intel_engine()
        await board_intel.initialize()
        
        projections = await board_intel.get_scouting_projections()
        
        return {
            "projections": projections,
            "count": len(projections),
            "status": "early_bird_active" if len(projections) > 0 else "full_drop_complete"
        }
    except Exception as e:
        return {"projections": [], "count": 0, "error": str(e)}

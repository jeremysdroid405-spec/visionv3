"""
Board Intel Routes - Primary Sync Operations
=============================================
"""
from fastapi import APIRouter, HTTPException
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v3/board-intel", tags=["board-intel"])


# These will be set by the main server
engine = None


def set_engine(eng):
    """Set the engine instance"""
    global engine
    engine = eng


@router.get("/status")
async def get_board_intel_status() -> Dict[str, Any]:
    """Get board intel sync status"""
    if not engine:
        return {
            "success": False,
            "time_since_sync_display": "Engine offline",
            "last_sync_type": None,
            "scheduler_running": False
        }
    
    try:
        result = await engine.get_board_intel_status()
        return result
    except Exception as e:
        logger.error(f"Error getting board intel status: {e}")
        return {
            "success": False,
            "error": str(e),
            "time_since_sync_display": "Error",
            "scheduler_running": False
        }


@router.post("/primary-sync")
async def trigger_primary_sync() -> Dict[str, Any]:
    """Trigger full primary sync (Odds API → MongoDB → Scoring)"""
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    try:
        result = await engine.primary_sync()
        return result
    except Exception as e:
        logger.error(f"Error during primary sync: {e}")
        return {"success": False, "error": str(e)}


@router.post("/delta-sync")
async def trigger_delta_sync() -> Dict[str, Any]:
    """Trigger delta sync (only changed data)"""
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    try:
        result = await engine.delta_sync()
        return result
    except Exception as e:
        logger.error(f"Error during delta sync: {e}")
        return {"success": False, "error": str(e)}


@router.post("/priority-refresh")
async def trigger_priority_refresh() -> Dict[str, Any]:
    """Refresh priority picks only"""
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    try:
        result = await engine.priority_refresh()
        return result
    except Exception as e:
        logger.error(f"Error during priority refresh: {e}")
        return {"success": False, "error": str(e)}


@router.get("/schedule-intel-check")
async def get_schedule_intel_check() -> Dict[str, Any]:
    """Check scheduler status and next sync time"""
    if not engine:
        return {"success": False, "scheduler_running": False}
    
    try:
        result = await engine.get_schedule_intel_check()
        return result
    except Exception as e:
        logger.error(f"Error checking schedule: {e}")
        return {"success": False, "error": str(e)}

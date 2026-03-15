"""
Sync Routes - Data Sync and Status
===================================
"""
from fastapi import APIRouter, HTTPException
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v3", tags=["sync"])


# These will be set by the main server
engine = None


def set_engine(eng):
    """Set the engine instance"""
    global engine
    engine = eng


@router.get("/status")
async def get_status() -> Dict[str, Any]:
    """Get engine status"""
    if not engine:
        return {"success": False, "status": "Engine not initialized"}
    
    try:
        result = await engine.get_status()
        return result
    except Exception as e:
        logger.error(f"Error getting status: {e}")
        return {"success": False, "error": str(e)}


@router.post("/sync")
async def trigger_sync() -> Dict[str, Any]:
    """Trigger a data sync"""
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    try:
        result = await engine.sync()
        return result
    except Exception as e:
        logger.error(f"Error syncing: {e}")
        return {"success": False, "error": str(e)}


@router.post("/sync-to-mongo")
async def sync_to_mongo() -> Dict[str, Any]:
    """Sync data to MongoDB cache"""
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    try:
        result = await engine.sync_to_mongo()
        return result
    except Exception as e:
        logger.error(f"Error syncing to mongo: {e}")
        return {"success": False, "error": str(e)}


@router.get("/sync-status")
async def get_sync_status() -> Dict[str, Any]:
    """Get detailed sync status"""
    if not engine:
        return {"success": False, "engine_status": "offline"}
    
    try:
        result = await engine.get_sync_status()
        return result
    except Exception as e:
        logger.error(f"Error getting sync status: {e}")
        return {"success": False, "error": str(e)}


@router.get("/data-status")
async def get_data_status() -> Dict[str, Any]:
    """Get data freshness status"""
    if not engine:
        return {"success": False, "status": "offline"}
    
    try:
        result = await engine.get_data_status()
        return result
    except Exception as e:
        logger.error(f"Error getting data status: {e}")
        return {"success": False, "error": str(e)}


@router.get("/lock-status")
async def get_lock_status() -> Dict[str, Any]:
    """Get game lock status (games in progress)"""
    if not engine:
        return {"success": False, "locked_games": []}
    
    try:
        result = await engine.get_lock_status()
        return result
    except Exception as e:
        logger.error(f"Error getting lock status: {e}")
        return {"success": False, "error": str(e), "locked_games": []}


@router.get("/t-minus-games")
async def get_t_minus_games() -> Dict[str, Any]:
    """Get upcoming games (T-minus countdown)"""
    if not engine:
        return {"success": False, "games": []}
    
    try:
        result = await engine.get_t_minus_games()
        return result
    except Exception as e:
        logger.error(f"Error getting t-minus games: {e}")
        return {"success": False, "error": str(e), "games": []}


@router.get("/locked-games")
async def get_locked_games() -> Dict[str, Any]:
    """Get list of currently locked games"""
    if not engine:
        return {"success": False, "games": []}
    
    try:
        result = await engine.get_locked_games()
        return result
    except Exception as e:
        logger.error(f"Error getting locked games: {e}")
        return {"success": False, "error": str(e), "games": []}


@router.post("/check-locks")
async def check_locks() -> Dict[str, Any]:
    """Check and update game lock status"""
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    try:
        result = await engine.check_locks()
        return result
    except Exception as e:
        logger.error(f"Error checking locks: {e}")
        return {"success": False, "error": str(e)}

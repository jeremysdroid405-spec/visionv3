"""
Sync Routes - Data Sync and Status
===================================
Maps to DemonGoblinEngine methods for sync operations.
"""
from fastapi import APIRouter, HTTPException
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v3", tags=["sync"])


# These will be set by the main server
engine = None
game_lock_engine = None
db = None


def set_engine(eng, lock_eng=None, database=None):
    """Set the engine instance"""
    global engine, game_lock_engine, db
    engine = eng
    if lock_eng is not None:
        game_lock_engine = lock_eng
    if database is not None:
        db = database


@router.get("/status")
async def get_status() -> Dict[str, Any]:
    """Get engine sync status"""
    if not engine:
        return {"success": False, "status": "Engine not initialized"}
    
    try:
        status = await engine.get_sync_status()
        return {"success": True, "data": status}
    except Exception as e:
        logger.error(f"Error getting status: {e}")
        return {"success": False, "error": str(e)}


@router.post("/sync")
async def trigger_sync() -> Dict[str, Any]:
    """Trigger full Demon & Goblin sync"""
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    try:
        result = await engine.run_full_sync()
        return {"success": True, "result": result}
    except Exception as e:
        logger.error(f"Error syncing: {e}")
        return {"success": False, "error": str(e)}


@router.post("/sync-to-mongo")
async def sync_to_mongo() -> Dict[str, Any]:
    """Sync odds data to MongoDB cache"""
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    try:
        result = await engine.sync_odds_to_mongo()
        return {"success": True, "result": result}
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


@router.post("/sync-master-roster")
async def sync_master_roster() -> Dict[str, Any]:
    """Sync master roster from NBA API"""
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    try:
        result = await engine.sync_master_roster()
        return {"success": True, "result": result}
    except Exception as e:
        logger.error(f"Error syncing master roster: {e}")
        return {"success": False, "error": str(e)}


@router.post("/sync-player-photos")
async def sync_player_photos() -> Dict[str, Any]:
    """Sync player photos from master roster"""
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    try:
        result = await engine.sync_player_photos()
        return {"success": True, "result": result}
    except Exception as e:
        logger.error(f"Error syncing player photos: {e}")
        return {"success": False, "error": str(e)}


@router.post("/sync-active-players")
async def sync_active_players() -> Dict[str, Any]:
    """Sync active players with photos"""
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    try:
        result = await engine.sync_active_players_with_photos()
        return {"success": True, "result": result}
    except Exception as e:
        logger.error(f"Error syncing active players: {e}")
        return {"success": False, "error": str(e)}


@router.post("/sync-player-stats")
async def sync_player_stats() -> Dict[str, Any]:
    """Sync player stats from BDL"""
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    try:
        result = await engine.sync_player_stats()
        return {"success": True, "result": result}
    except Exception as e:
        logger.error(f"Error syncing player stats: {e}")
        return {"success": False, "error": str(e)}


@router.post("/sync-daily-insights")
async def sync_daily_insights() -> Dict[str, Any]:
    """Sync daily insights analytics"""
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    try:
        result = await engine.sync_daily_insights()
        return {"success": True, "result": result}
    except Exception as e:
        logger.error(f"Error syncing daily insights: {e}")
        return {"success": False, "error": str(e)}


@router.get("/lock-status")
async def get_lock_status() -> Dict[str, Any]:
    """Get game lock status (games in progress)"""
    if not game_lock_engine:
        return {"success": False, "locked_games": []}
    
    try:
        locked_games = await game_lock_engine.get_locked_games()
        return {"success": True, "locked_games": locked_games}
    except Exception as e:
        logger.error(f"Error getting lock status: {e}")
        return {"success": False, "error": str(e), "locked_games": []}


@router.get("/locked-games")
async def get_locked_games() -> Dict[str, Any]:
    """Get list of currently locked games"""
    if not game_lock_engine:
        return {"success": False, "games": []}
    
    try:
        games = await game_lock_engine.get_locked_games()
        return {"success": True, "games": games}
    except Exception as e:
        logger.error(f"Error getting locked games: {e}")
        return {"success": False, "error": str(e), "games": []}


@router.post("/check-locks")
async def check_locks() -> Dict[str, Any]:
    """Check and update game lock status"""
    if not game_lock_engine:
        raise HTTPException(status_code=500, detail="Game lock engine not initialized")
    
    try:
        result = await game_lock_engine.check_and_lock_games()
        return {"success": True, "result": result}
    except Exception as e:
        logger.error(f"Error checking locks: {e}")
        return {"success": False, "error": str(e)}


@router.get("/data-integrity")
async def get_data_integrity() -> Dict[str, Any]:
    """Get data integrity status"""
    if not engine:
        return {"success": False, "status": "offline"}
    
    try:
        result = await engine.get_data_integrity_status()
        return result
    except Exception as e:
        logger.error(f"Error getting data integrity: {e}")
        return {"success": False, "error": str(e)}

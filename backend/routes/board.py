"""
Board Routes - Player Board, Search, Cache
==========================================
"""
from fastapi import APIRouter, HTTPException, Query
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v3", tags=["board"])


# These will be set by the main server
engine = None


def set_engine(eng):
    """Set the engine instance"""
    global engine
    engine = eng


@router.get("/cached-props")
async def get_cached_props() -> Dict[str, Any]:
    """Get cached player board with all props"""
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    try:
        result = await engine.get_cached_board()
        return result
    except Exception as e:
        logger.error(f"Error getting cached props: {e}")
        return {"success": False, "error": str(e), "players": []}


@router.get("/cached-player/{player_name}")
async def get_cached_player(player_name: str) -> Dict[str, Any]:
    """Get cached data for a specific player"""
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    try:
        result = await engine.get_cached_player(player_name)
        return result
    except Exception as e:
        logger.error(f"Error getting cached player: {e}")
        return {"success": False, "error": str(e), "player": None}


@router.get("/players")
async def get_players() -> Dict[str, Any]:
    """Get all players with props"""
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    try:
        result = await engine.get_players()
        return result
    except Exception as e:
        logger.error(f"Error getting players: {e}")
        return {"success": False, "error": str(e), "players": []}


@router.get("/player/{player_name}")
async def get_player(player_name: str) -> Dict[str, Any]:
    """Get detailed data for a specific player"""
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    try:
        result = await engine.get_player(player_name)
        return result
    except Exception as e:
        logger.error(f"Error getting player: {e}")
        return {"success": False, "error": str(e), "player": None}


@router.get("/search")
async def search_players(q: str = Query(..., min_length=1)) -> Dict[str, Any]:
    """Search players by name"""
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    try:
        result = await engine.search_players(q)
        return result
    except Exception as e:
        logger.error(f"Error searching players: {e}")
        return {"success": False, "error": str(e), "players": []}


@router.get("/board")
async def get_board() -> Dict[str, Any]:
    """Get full board data"""
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    try:
        result = await engine.get_board()
        return result
    except Exception as e:
        logger.error(f"Error getting board: {e}")
        return {"success": False, "error": str(e)}


@router.get("/trending")
async def get_trending() -> Dict[str, Any]:
    """Get trending players"""
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    try:
        result = await engine.get_trending()
        return result
    except Exception as e:
        logger.error(f"Error getting trending: {e}")
        return {"success": False, "error": str(e), "trending": []}


@router.get("/static-shell")
async def get_static_shell() -> Dict[str, Any]:
    """Get static shell data (roster without live lines)"""
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    try:
        result = await engine.get_static_shell()
        return result
    except Exception as e:
        logger.error(f"Error getting static shell: {e}")
        return {"success": False, "error": str(e)}


@router.get("/hydrated-board")
async def get_hydrated_board() -> Dict[str, Any]:
    """Get hydrated board with all data merged"""
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    try:
        result = await engine.get_hydrated_board()
        return result
    except Exception as e:
        logger.error(f"Error getting hydrated board: {e}")
        return {"success": False, "error": str(e)}


# Photo storage endpoints
photo_service = None

def set_photo_service(svc):
    """Set the photo service instance"""
    global photo_service
    photo_service = svc


@router.post("/sync-photos")
async def sync_photos() -> Dict[str, Any]:
    """
    Download and store all active player photos as base64 in MongoDB.
    This is a one-time operation to cache all photos locally.
    """
    if not photo_service:
        raise HTTPException(status_code=500, detail="Photo service not initialized")
    
    try:
        stats = await photo_service.sync_all_active_player_photos()
        return {"success": True, "stats": stats}
    except Exception as e:
        logger.error(f"Error syncing photos: {e}")
        return {"success": False, "error": str(e)}


@router.get("/photo-stats")
async def get_photo_stats() -> Dict[str, Any]:
    """Get statistics about cached photos."""
    if not photo_service:
        raise HTTPException(status_code=500, detail="Photo service not initialized")
    
    try:
        stats = photo_service.get_sync_stats()
        return {"success": True, "stats": stats}
    except Exception as e:
        logger.error(f"Error getting photo stats: {e}")
        return {"success": False, "error": str(e)}


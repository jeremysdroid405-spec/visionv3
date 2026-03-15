"""
Picks Routes - War Zone, Safe Haven, Front Lines
=================================================
NOTE: War Zone, Goblin Vault, and Front Lines endpoints are defined in server.py
because they have additional game lock logic. This file contains legacy/deprecated endpoints.
"""
from fastapi import APIRouter, HTTPException
from datetime import datetime
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v3", tags=["picks"])


# These will be set by the main server
engine = None


def set_engine(eng):
    """Set the engine instance"""
    global engine
    engine = eng


# NOTE: /war-zone, /goblin-vault, /front-lines are defined in server.py with game lock logic

@router.get("/most-popular-bets")
async def get_most_popular_bets() -> Dict[str, Any]:
    """Get Most Popular Bets - Live ticker"""
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    try:
        result = await engine.get_most_popular_bets()
        return result
    except Exception as e:
        logger.error(f"Error getting popular bets: {e}")
        return {"success": False, "error": str(e), "bets": [], "status": "error"}


# Alias for backward compatibility
@router.get("/popular-bets")
async def get_popular_bets() -> Dict[str, Any]:
    """Alias for most-popular-bets"""
    return await get_most_popular_bets()


@router.get("/demons")
async def get_demons() -> Dict[str, Any]:
    """Get all demon picks (deprecated - use war-zone)"""
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    try:
        result = await engine.get_demons()
        return result
    except Exception as e:
        logger.error(f"Error getting demons: {e}")
        return {"success": False, "error": str(e), "demons": []}


@router.get("/goblins")
async def get_goblins() -> Dict[str, Any]:
    """Get all goblin picks (deprecated - use goblin-vault)"""
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    try:
        result = await engine.get_goblins()
        return result
    except Exception as e:
        logger.error(f"Error getting goblins: {e}")
        return {"success": False, "error": str(e), "goblins": []}

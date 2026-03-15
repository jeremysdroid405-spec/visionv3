"""
Intel Routes - AI Briefings and Insights
=========================================
"""
from fastapi import APIRouter, HTTPException
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v3", tags=["intel"])


# These will be set by the main server
engine = None


def set_engine(eng):
    """Set the engine instance"""
    global engine
    engine = eng


@router.post("/generate-intel-briefings")
async def generate_intel_briefings() -> Dict[str, Any]:
    """Generate AI briefings for all players"""
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    try:
        result = await engine.generate_intel_briefings()
        return result
    except Exception as e:
        logger.error(f"Error generating briefings: {e}")
        return {"success": False, "error": str(e)}


@router.get("/intel-briefing/{player_name}")
async def get_intel_briefing(player_name: str) -> Dict[str, Any]:
    """Get AI briefing for a specific player"""
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    try:
        result = await engine.get_intel_briefing(player_name)
        return result
    except Exception as e:
        logger.error(f"Error getting briefing: {e}")
        return {"success": False, "error": str(e)}


@router.get("/social-signals")
async def get_social_signals() -> Dict[str, Any]:
    """Get social signals for all players"""
    if not engine:
        return {"success": True, "signals": {}}
    
    try:
        result = await engine.get_social_signals()
        return result
    except Exception as e:
        logger.error(f"Error getting social signals: {e}")
        return {"success": False, "error": str(e), "signals": {}}


@router.get("/breaking-news")
async def get_breaking_news() -> Dict[str, Any]:
    """Get breaking news affecting player props"""
    if not engine:
        return {"success": True, "news": []}
    
    try:
        result = await engine.get_breaking_news()
        return result
    except Exception as e:
        logger.error(f"Error getting breaking news: {e}")
        return {"success": False, "error": str(e), "news": []}


@router.get("/live-scores")
async def get_live_scores() -> Dict[str, Any]:
    """Get live game scores"""
    if not engine:
        return {"success": True, "games": []}
    
    try:
        result = await engine.get_live_scores()
        return result
    except Exception as e:
        logger.error(f"Error getting live scores: {e}")
        return {"success": False, "error": str(e), "games": []}


@router.get("/injuries/alerts")
async def get_injury_alerts() -> Dict[str, Any]:
    """Get injury alerts"""
    if not engine:
        return {"success": True, "alerts": {}}
    
    try:
        result = await engine.get_injury_alerts()
        return result
    except Exception as e:
        logger.error(f"Error getting injury alerts: {e}")
        return {"success": False, "error": str(e), "alerts": {}}


@router.get("/scouting-projections")
async def get_scouting_projections() -> Dict[str, Any]:
    """Get scouting projections (early bird mode)"""
    if not engine:
        return {"success": True, "projections": [], "status": "inactive"}
    
    try:
        result = await engine.get_scouting_projections()
        return result
    except Exception as e:
        logger.error(f"Error getting projections: {e}")
        return {"success": False, "error": str(e), "projections": []}

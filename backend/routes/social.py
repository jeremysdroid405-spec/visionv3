"""
Social Signal Routes
====================
Social Signal Engine - News sentiment & revenge games tracking.
"""
from fastapi import APIRouter, HTTPException
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Social Signals"])

# Reference to social_signal_engine (set via dependency injection)
_social_signal_engine = None


def set_social_signal_engine(engine):
    """Set the social signal engine reference."""
    global _social_signal_engine
    _social_signal_engine = engine


def get_social_signal_engine():
    """Get the social signal engine instance."""
    if _social_signal_engine is None:
        raise HTTPException(status_code=500, detail="Social Signal Engine not initialized")
    return _social_signal_engine


@router.post("/v3/sync-social-signals")
async def sync_social_signals():
    """
    SOCIAL SIGNAL SYNC - Fetch news and sentiment for all players.
    
    Analyzes:
    - Recent news headlines
    - Revenge game narratives
    - Contract/extension situations
    - Milestone chasing (triple-double, 50 points, etc.)
    - Team chemistry signals
    """
    engine = get_social_signal_engine()
    
    logger.info("[SOCIAL SYNC] Manual sync triggered via API")
    result = await engine.sync_all_signals()
    
    return {
        "success": True,
        "players_processed": result.get("players_processed", 0),
        "signals_generated": result.get("signals_generated", 0),
        "synced_at": datetime.now(timezone.utc).isoformat()
    }


@router.get("/v3/social-signals")
async def get_social_signals(limit: int = 50):
    """
    Get all social signals for display in the UI.
    
    Returns:
    - player_name
    - signal_type: "revenge_game", "milestone", "hot_streak", etc.
    - headline: Brief description
    - sentiment_score: -1.0 to 1.0
    - impact_rating: "high", "medium", "low"
    """
    engine = get_social_signal_engine()
    signals = await engine.get_all_signals(limit=limit)
    
    return {
        "success": True,
        "signals": signals,
        "count": len(signals)
    }


@router.get("/v3/social-signal/{player_name}")
async def get_player_social_signal(player_name: str):
    """
    Get social signals for a specific player.
    
    Returns all active signals including:
    - Revenge games
    - Milestone chasing
    - Recent news sentiment
    - Hot/cold streak indicators
    """
    engine = get_social_signal_engine()
    
    signals = await engine.get_player_signals(player_name)
    
    if not signals:
        return {
            "player_name": player_name,
            "signals": [],
            "message": "No active social signals for this player"
        }
    
    return {
        "player_name": player_name,
        "signals": signals,
        "count": len(signals)
    }

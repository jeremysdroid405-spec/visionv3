"""
Intel Sync Routes
=================
AI briefing generation and intelligence sync endpoints.
"""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional
import logging

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Intel Sync"])

# Reference to DemonGoblinEngine (set via dependency injection)
_demon_goblin_engine = None


def set_intel_sync_engine(engine):
    """Set the demon goblin engine reference."""
    global _demon_goblin_engine
    _demon_goblin_engine = engine


def get_engine():
    """Get the demon goblin engine instance."""
    if _demon_goblin_engine is None:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    return _demon_goblin_engine


@router.post("/v3/sync-to-mongo")
async def sync_to_mongo(force: bool = False):
    """
    MAIN SYNC ENDPOINT - Syncs Odds API data to MongoDB.
    
    This is the primary endpoint for refreshing all betting data:
    1. Fetches fresh odds from Odds API
    2. Calculates hit rates and EV scores
    3. Tags demons and goblins
    4. Stores in cached_board collection
    
    Args:
    - force: If True, bypasses rate limiting and cache
    
    Should be called:
    - Manually for testing
    - Automatically by scheduler at 10:30 AM ET
    """
    engine = get_engine()
    result = await engine.sync_odds_to_mongo()
    return result


@router.post("/v3/generate-intel-briefings")
async def generate_intel_briefings(
    player_names: Optional[list] = None,
    force_regenerate: bool = False
):
    """
    Generate AI Vision briefings for players.
    
    Uses Google Gemini to create contextual analysis including:
    - Matchup analysis
    - Recent performance trends
    - Injury impact assessment
    - Confidence ratings
    
    Args:
    - player_names: List of specific players (None = all active players)
    - force_regenerate: If True, regenerates even if briefing exists
    """
    engine = get_engine()
    
    # Use the intel_briefing_engine if available
    try:
        from intel_briefing_engine import get_intel_briefing_engine
        briefing_engine = get_intel_briefing_engine()
        if briefing_engine and player_names:
            results = []
            for name in player_names[:10]:  # Limit to 10 players
                result = await briefing_engine.generate_briefing(name, force=force_regenerate)
                results.append(result)
            return {"success": True, "briefings_generated": len(results), "results": results}
        elif briefing_engine:
            result = await briefing_engine.generate_all_briefings(force=force_regenerate)
            return result
    except Exception as e:
        logger.warning(f"Intel briefing engine not available: {e}")
    
    return {"success": False, "message": "Intel briefing engine not available"}


@router.get("/v3/intel-briefing/{player_name}")
async def get_intel_briefing(
    player_name: str,
    prop_type: Optional[str] = Query(None, description="Specific prop type (PTS, REB, AST, etc.)")
):
    """
    Get AI Vision briefing for a specific player.
    
    Returns:
    - player_name
    - prop_type (if specified)
    - vision_summary: AI-generated analysis
    - confidence_rating: 1-5 stars
    - key_factors: List of factors affecting the pick
    - generated_at: Timestamp
    """
    engine = get_engine()
    
    # Try to get from daily_insights collection
    query = {"player_name": {"$regex": f"^{player_name}$", "$options": "i"}}
    if prop_type:
        query["prop_type"] = prop_type.upper()
    
    insight = await engine.daily_insights.find_one(query, {"_id": 0})
    
    if not insight:
        # Try cached_board for vision_summary
        player = await engine.cached_board.find_one(
            {"player_name": {"$regex": f"^{player_name}$", "$options": "i"}},
            {"_id": 0, "player_name": 1, "vision_summary": 1, "team": 1}
        )
        if player and player.get("vision_summary"):
            return {
                "success": True,
                "player_name": player.get("player_name"),
                "vision_summary": player.get("vision_summary"),
                "team": player.get("team")
            }
        
        raise HTTPException(
            status_code=404, 
            detail=f"No briefing found for '{player_name}'"
        )
    
    return {"success": True, **insight}

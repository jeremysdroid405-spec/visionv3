"""
Vision AI Routes Module
=======================
Handles Vision AI insight generation endpoints and player context badges.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import logging
import os

from services.config.collection_names import COLL

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v3/vision", tags=["vision"])
player_router = APIRouter(prefix="/player", tags=["player-vision"])
context_router = APIRouter(prefix="/context", tags=["context"])

# Vision service and db references (set by main app)
_vision_service = None
_db = None


def set_vision_service(service, db):
    """Set the vision service and db references."""
    global _vision_service, _db
    _vision_service = service
    _db = db


class FlagCreate(BaseModel):
    """Request model for creating a narrative flag."""
    player_id: int
    flag_type: str
    severity: int
    headline_reference: Optional[str] = ""
    travel_miles: Optional[int] = 0


def slug_to_name(slug: str) -> str:
    """Convert URL slug to player name search pattern."""
    parts = slug.replace("-", " ").split()
    return " ".join(p.capitalize() for p in parts)


class VisionInsightRequest(BaseModel):
    """Request model for single AI insight generation"""
    player_name: str
    stat_type: str = "points"
    current_line: float
    l10_rate: float = 50.0
    pace_factor: float = 1.0
    fatigue: str = "Normal"
    usage_bump: float = 0
    volatility: str = "Med"
    is_demon: bool = False
    is_goblin: bool = False
    projected_score: Optional[float] = None


@router.post("/generate-insight")
async def generate_vision_insight(request: VisionInsightRequest):
    """
    VISION AI - Generate a single AI insight for a player prop.
    
    Uses Claude Sonnet 4.5 to generate a "badass" 1-sentence insight.
    Only use for Demons, Goblins, or High Volatility players to manage costs.
    """
    if _vision_service is None:
        raise HTTPException(status_code=500, detail="Vision AI Service not initialized")
    
    result = await _vision_service.generate_single_insight(
        player_name=request.player_name,
        stat_type=request.stat_type,
        current_line=request.current_line,
        l10_rate=request.l10_rate,
        pace_factor=request.pace_factor,
        fatigue=request.fatigue,
        usage_bump=request.usage_bump,
        volatility=request.volatility,
        is_demon=request.is_demon,
        is_goblin=request.is_goblin,
        projected_score=request.projected_score
    )
    
    if not result.get('success'):
        raise HTTPException(status_code=400, detail=result.get('error', 'Failed to generate insight'))
    
    return result


@router.post("/trigger-batch")
async def trigger_vision_batch():
    """
    VISION AI BATCH - Generate insights for all eligible players.
    
    Filters for cost efficiency:
    - Only Demons (high payout potential)
    - Only Goblins (high safety picks)  
    - Only High Volatility players
    
    Should be called AFTER daily sync completes.
    """
    if _vision_service is None:
        raise HTTPException(status_code=500, detail="Vision AI Service not initialized")
    
    logger.info("[VISION] Batch insight generation triggered")
    
    result = await _vision_service.trigger_insights_for_sync()
    
    return {
        "success": result.get('success', False),
        "message": "Vision AI batch processing complete",
        "insights_generated": result.get('insights_generated', 0),
        "errors_count": result.get('errors_count', 0),
        "eligible_players": result.get('eligible_players', 0),
        "total_players": result.get('total_players', 0),
        "sample_results": result.get('results', [])[:3]
    }


@router.get("/status")
async def get_vision_status():
    """Get Vision AI service status and configuration."""
    google_key_configured = bool(os.environ.get('GOOGLE_API_KEY'))
    
    ai_insights_count = 0
    if _vision_service is not None and _db is not None:
        ai_insights_count = await _db.dg_daily_insights.count_documents({
            "ai_generated_at": {"$exists": True}
        })
    
    return {
        "success": True,
        "service_initialized": _vision_service is not None,
        "google_key_configured": google_key_configured,
        "model": "gemini-3-flash-preview",
        "provider": "google",
        "ai_insights_count": ai_insights_count,
        "cost_filters": {
            "demons_only": True,
            "goblins_only": True,
            "high_volatility_only": True
        }
    }


# ============================================
# PLAYER VISION ENDPOINTS (Badge System)
# ============================================

@player_router.get("/{player_slug}/vision")
async def get_player_vision(player_slug: str):
    """
    Get complete player vision data with stats and active badges.
    
    Args:
        player_slug: URL-friendly player name (e.g., "luka-doncic")
        
    Returns:
        Player vision object with stats, context badges, and narrative flags
    """
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    from services.badge_resolver import get_badge_resolver
    resolver = get_badge_resolver(_db)
    
    # Convert slug to name pattern
    name_pattern = slug_to_name(player_slug)
    
    # Find player by name (handle special characters like Dončić)
    player = await _db[COLL("master_hub", "nba")].find_one(
        {"display_name": {"$regex": name_pattern, "$options": "i"}},
        {"nba_player_id": 1, "display_name": 1}
    )
    
    if not player:
        raise HTTPException(status_code=404, detail=f"Player not found: {player_slug}")
    
    player_id = player.get("nba_player_id")
    if not player_id:
        raise HTTPException(status_code=404, detail=f"Player has no NBA ID: {player_slug}")
    
    vision = await resolver.get_player_vision(player_id)
    
    return vision


# ============================================
# CONTEXT ENGINE ENDPOINTS
# ============================================

@context_router.post("/flag")
async def add_context_flag(flag: FlagCreate):
    """
    Add a narrative flag for a player.
    
    Flags are used to generate context badges (Legal Noise, Jet Lag, etc.)
    """
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    from services.badge_resolver import get_badge_resolver
    resolver = get_badge_resolver(_db)
    
    result = await resolver.add_flag(
        player_id=flag.player_id,
        flag_type=flag.flag_type,
        severity=flag.severity,
        headline_reference=flag.headline_reference or "",
        travel_miles=flag.travel_miles or 0
    )
    
    # Remove MongoDB _id for JSON response
    if "_id" in result:
        result["_id"] = str(result["_id"])
    
    return result


@context_router.get("/badges")
async def list_badge_definitions():
    """
    List all available badge definitions.
    
    Returns the 10 standardized badges with their display info.
    """
    from services.badge_resolver import BADGE_DEFINITIONS
    
    badges = []
    for key, defn in BADGE_DEFINITIONS.items():
        badges.append({
            "badge_key": key,
            "display": defn.get("display"),
            "icon": defn.get("icon"),
            "color": defn.get("color"),
            "description": defn.get("description"),
            "trigger_flags": defn.get("trigger_flags", [])
        })
    
    return {"badges": badges, "count": len(badges)}


@context_router.get("/player/{player_id}/flags")
async def get_player_flags(player_id: int):
    """Get all active flags for a player."""
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    from services.badge_resolver import get_badge_resolver
    resolver = get_badge_resolver(_db)
    
    flags = await resolver.get_player_flags(player_id)
    
    # Serialize ObjectId
    for f in flags:
        if "_id" in f:
            f["_id"] = str(f["_id"])
    
    return {"player_id": player_id, "flags": flags, "count": len(flags)}

"""
Vision AI Routes Module
=======================
Handles Vision AI insight generation endpoints
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import logging
import os

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v3/vision", tags=["vision"])

# Vision service and db references (set by main app)
_vision_service = None
_db = None


def set_vision_service(service, db):
    """Set the vision service and db references."""
    global _vision_service, _db
    _vision_service = service
    _db = db


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
    if not _vision_service:
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
    if not _vision_service:
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
    emergent_key_configured = bool(os.environ.get('EMERGENT_LLM_KEY'))
    
    ai_insights_count = 0
    if _vision_service and _db:
        ai_insights_count = await _db.dg_daily_insights.count_documents({
            "ai_generated_at": {"$exists": True}
        })
    
    return {
        "success": True,
        "service_initialized": _vision_service is not None,
        "emergent_key_configured": emergent_key_configured,
        "model": "claude-sonnet-4.5",
        "provider": "anthropic",
        "ai_insights_count": ai_insights_count,
        "cost_filters": {
            "demons_only": True,
            "goblins_only": True,
            "high_volatility_only": True
        }
    }

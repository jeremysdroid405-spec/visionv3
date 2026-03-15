"""
Command Post Routes
===================
Risk Assessment Hub API endpoints.

Endpoints:
- POST /api/command/simulate - Simulate parlay configuration
- GET /api/command/search - Search players via BallDontLie
- GET /api/command/profile/{player_name} - Get tactical profile for player
"""
import os
import httpx
import logging
from typing import List, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from services.simulation_service import get_simulation_engine
from services.dvp_service import get_dvp_rank, get_dvp_rank_color, calculate_dvp_modifier

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/command", tags=["Command Post"])

# BallDontLie API config
BDL_API_KEY = os.environ.get("BALLDONTLIE_API_KEY", "")
BDL_BASE = "https://api.balldontlie.io/nba/v1"


# ==================== REQUEST MODELS ====================

class SimulationLeg(BaseModel):
    """A single leg for simulation."""
    player_name: str
    player_id: Optional[str] = None
    stat_type: str
    line: float
    direction: str = "over"
    team: str = ""
    opponent: str = ""
    game_id: Optional[str] = None
    is_home: bool = True
    h10_rate: float = 50.0
    h5_rate: float = 50.0
    season_avg: float = 0.0
    std_dev: float = 0.0
    usage_bump_percent: float = 0.0


class SimulationRequest(BaseModel):
    """Request body for simulation endpoint."""
    legs: List[SimulationLeg] = Field(..., min_length=1, max_length=10)


# ==================== ENDPOINTS ====================

@router.post("/simulate")
async def simulate_configuration(request: SimulationRequest):
    """
    Simulate a parlay configuration.
    
    Returns:
    - Convergence Rate (combined tactical probability)
    - Infiltration Grade (S/A/B/C/D)
    - Volatility Index
    - Correlation penalties
    - Risk flags
    - Conflict detection
    
    Grades:
    - S-Tier: High-Alpha / Optimal Alignment (75%+)
    - A-Tier: Strong Tactical Position (65-74%)
    - B-Tier: Standard Tactical Exposure (55-64%)
    - C-Tier: Elevated Friction (45-54%)
    - D-Tier: High-Friction / Volatile Environment (<45%)
    """
    try:
        engine = get_simulation_engine()
        
        # Convert Pydantic models to dicts
        legs_data = [leg.model_dump() for leg in request.legs]
        
        result = await engine.simulate_configuration(legs_data)
        
        return result
        
    except Exception as e:
        logger.error(f"[COMMAND] Simulation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/search")
async def search_players(
    query: str = Query(..., min_length=2, description="Player name to search"),
    limit: int = Query(10, ge=1, le=25, description="Max results")
):
    """
    Search players via BallDontLie API.
    
    Returns player list with basic info for Command Post selection.
    """
    if not BDL_API_KEY:
        raise HTTPException(status_code=503, detail="BallDontLie API not configured")
    
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{BDL_BASE}/players",
                params={
                    "search": query,
                    "per_page": limit
                },
                headers={"Authorization": BDL_API_KEY}
            )
            
            if response.status_code != 200:
                logger.warning(f"[COMMAND] BDL search failed: {response.status_code}")
                return {"success": False, "players": [], "error": "Search unavailable"}
            
            data = response.json()
            players = data.get("data", [])
            
            # Format for frontend
            results = []
            for player in players:
                results.append({
                    "id": player.get("id"),
                    "player_name": f"{player.get('first_name', '')} {player.get('last_name', '')}".strip(),
                    "team": player.get("team", {}).get("abbreviation", ""),
                    "team_name": player.get("team", {}).get("full_name", ""),
                    "position": player.get("position", ""),
                    "jersey": player.get("jersey_number"),
                    "height": player.get("height"),
                    "weight": player.get("weight")
                })
            
            return {
                "success": True,
                "query": query,
                "count": len(results),
                "players": results
            }
            
    except httpx.TimeoutException:
        return {"success": False, "players": [], "error": "Search timeout"}
    except Exception as e:
        logger.error(f"[COMMAND] Search error: {e}")
        return {"success": False, "players": [], "error": str(e)}


@router.get("/profile/{player_name}")
async def get_tactical_profile(
    player_name: str,
    opponent: str = Query("", description="Opponent team abbreviation for DvP calc")
):
    """
    Get tactical profile for a player.
    
    Returns:
    - Active prop lines (from cached board)
    - Usage Ripple status
    - DvP rankings per stat type
    - Volatility indicators
    """
    from motor.motor_asyncio import AsyncIOMotorClient
    
    try:
        # Get from cached board
        mongo_url = os.environ.get("MONGO_URL")
        db_name = os.environ.get("DB_NAME", "pickvision")
        
        if not mongo_url:
            raise HTTPException(status_code=503, detail="Database not configured")
        
        client = AsyncIOMotorClient(mongo_url)
        db = client[db_name]
        
        # Find player in cached board
        player = await db.dg_cached_board.find_one(
            {"player_name": {"$regex": player_name, "$options": "i"}},
            {"_id": 0}
        )
        
        if not player:
            return {
                "success": False,
                "error": "Player not found in today's slate",
                "player_name": player_name
            }
        
        # Get opponent from player data if not provided
        if not opponent:
            opponent = player.get("opponent_abbr") or player.get("opponent") or ""
        
        # Build tactical profile
        props = player.get("props", [])
        demons = player.get("demons", [])
        goblins = player.get("goblins", [])
        
        # Build prop lines with DvP data
        active_lines = []
        for prop in props:
            stat = prop.get("stat_type") or prop.get("market", "").replace("player_", "").upper()
            line = prop.get("line", 0)
            direction = prop.get("direction", "over")
            
            # Get DvP for this stat
            dvp_rank = get_dvp_rank(opponent, stat) if opponent else 15
            dvp_color = get_dvp_rank_color(dvp_rank)
            dvp_mod = calculate_dvp_modifier(opponent, stat) if opponent else 0.5
            
            # Determine friction level
            if dvp_rank >= 25:
                friction = "Low Friction (Soft Defense)"
            elif dvp_rank <= 5:
                friction = "High Friction (Elite Defense)"
            else:
                friction = "Standard Friction"
            
            active_lines.append({
                "stat_type": stat,
                "line": line,
                "direction": direction,
                "price": prop.get("price", -110),
                "dvp_rank": dvp_rank,
                "dvp_rank_color": dvp_color,
                "dvp_modifier": round(dvp_mod, 3),
                "friction_level": friction,
                "hit_rates": prop.get("hit_rates", {})
            })
        
        # Usage Ripple status
        usage_ripple = {
            "active": player.get("usage_bump_percent", 0) > 0,
            "bump_percent": player.get("usage_bump_percent", 0),
            "reason": player.get("usage_bump_reason"),
            "injured_teammates": player.get("injured_teammates", [])
        }
        
        # Volatility assessment
        volatility = {
            "flag": player.get("volatility_flag", False),
            "reason": player.get("volatility_reason"),
            "revenge_game": player.get("revenge_game", False)
        }
        
        profile = {
            "success": True,
            "player_name": player.get("player_name"),
            "player_id": player.get("player_id"),
            "team": player.get("team"),
            "team_name": player.get("team_name"),
            "position": player.get("position"),
            "photo_url": player.get("photo_url") or player.get("headshot_url"),
            "opponent": opponent,
            "is_verified": player.get("is_verified", False),
            
            # Tactical data
            "active_lines": active_lines,
            "demon_count": len(demons),
            "goblin_count": len(goblins),
            
            # Ripple & Volatility
            "usage_ripple": usage_ripple,
            "volatility": volatility,
            
            # Stats
            "season_avg": player.get("season_avg", {}),
            "l10_stats": player.get("l10_stats", {}),
            "l5_stats": player.get("l5_stats", {}),
            
            "fetched_at": datetime.now(timezone.utc).isoformat()
        }
        
        return profile
        
    except Exception as e:
        logger.error(f"[COMMAND] Profile error for {player_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/grades")
async def get_grade_definitions():
    """
    Get infiltration grade definitions.
    
    Returns grade thresholds and descriptions.
    """
    return {
        "grades": {
            "S": {
                "threshold": 75,
                "label": "High-Alpha / Optimal Alignment",
                "description": "Exceptional convergence. All tactical indicators aligned.",
                "color": "#10B981"  # Emerald
            },
            "A": {
                "threshold": 65,
                "label": "Strong Tactical Position", 
                "description": "Above-average alignment with manageable friction.",
                "color": "#3B82F6"  # Blue
            },
            "B": {
                "threshold": 55,
                "label": "Standard Tactical Exposure",
                "description": "Neutral risk profile. Standard variance expected.",
                "color": "#F59E0B"  # Amber
            },
            "C": {
                "threshold": 45,
                "label": "Elevated Friction",
                "description": "Multiple friction points. Review risk flags.",
                "color": "#F97316"  # Orange
            },
            "D": {
                "threshold": 0,
                "label": "High-Friction / Volatile Environment",
                "description": "Significant exposure. Exercise caution.",
                "color": "#EF4444"  # Red
            }
        },
        "volatility_thresholds": {
            "high": 0.25,
            "medium": 0.15,
            "low": 0.0
        },
        "terminology": {
            "convergence_rate": "Combined tactical probability based on all legs",
            "infiltration_grade": "Overall risk assessment grade (S to D)",
            "volatility_index": "Measure of outcome variance (higher = more unpredictable)",
            "defensive_friction": "DvP-based resistance (higher rank = softer defense)",
            "usage_ripple": "Increased opportunity from teammate injuries"
        }
    }

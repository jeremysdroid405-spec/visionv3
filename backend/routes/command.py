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
    Handles full name searches by trying last name if full name fails.
    """
    if not BDL_API_KEY:
        raise HTTPException(status_code=503, detail="BallDontLie API not configured")
    
    async def do_search(search_term: str) -> list:
        """Perform BDL search with given term."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{BDL_BASE}/players",
                params={
                    "search": search_term,
                    "per_page": limit
                },
                headers={"Authorization": BDL_API_KEY}
            )
            
            if response.status_code != 200:
                return []
            
            data = response.json()
            return data.get("data", [])
    
    try:
        players = await do_search(query)
        
        # If no results and query has multiple words, try last word (likely last name)
        if not players and " " in query:
            parts = query.strip().split()
            # Try last name
            players = await do_search(parts[-1])
            
            # Filter to match full query if possible
            if players:
                query_lower = query.lower()
                filtered = [p for p in players 
                           if query_lower in f"{p.get('first_name', '')} {p.get('last_name', '')}".lower()]
                if filtered:
                    players = filtered
        
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
    
    IMPORTANT: Full Intel Suite is ONLY for players on the board with 
    PropVision recommendations. Other players get basic stats only.
    
    Returns:
    - is_on_board: Whether player has active PropVision recommendations
    - lines: Prop lines (only recommended props get full intel)
    - radar_picks: List of stat_types that are PropVision objectives
    """
    from motor.motor_asyncio import AsyncIOMotorClient
    
    try:
        mongo_url = os.environ.get("MONGO_URL")
        db_name = os.environ.get("DB_NAME", "pickvision")
        
        if not mongo_url:
            raise HTTPException(status_code=503, detail="Database not configured")
        
        client = AsyncIOMotorClient(mongo_url)
        db = client[db_name]
        
        player_name_regex = {"$regex": player_name, "$options": "i"}
        
        # Check if player is on our board (has PropVision recommendations)
        radar_picks = await db.dg_radar_picks.find(
            {"player_name": player_name_regex},
            {"_id": 0}
        ).to_list(20)
        
        vault_picks = await db.dg_goblin_vault.find(
            {"player_name": player_name_regex},
            {"_id": 0}
        ).to_list(20)
        
        front_picks = await db.dg_front_lines.find(
            {"player_name": player_name_regex},
            {"_id": 0}
        ).to_list(20)
        
        # These are the RECOMMENDED picks (on the board)
        board_picks = radar_picks + vault_picks + front_picks
        is_on_board = len(board_picks) > 0
        
        # Build set of recommended stat types (these get Full Intel Suite)
        radar_stat_types = set()
        for pick in board_picks:
            stat = pick.get("stat_type", "")
            if stat:
                radar_stat_types.add(stat)
        
        if not is_on_board:
            # Player NOT on board - return basic profile only
            # They can still search but won't get Full Intel Suite
            return {
                "success": True,
                "player_name": player_name,
                "team": "",
                "position": "",
                "photo_url": "",
                "opponent": "",
                "is_on_board": False,
                "lines": [],  # No lines for non-board players
                "radar_picks": [],
                "usage_ripple": None,
                "message": "Player not on today's board. Full Intel Suite only available for PropVision recommendations."
            }
        
        # Player IS on board - build full profile with recommended props only
        first_pick = board_picks[0]
        player_team = first_pick.get("team", "")
        opponent = first_pick.get("opponent_abbr") or first_pick.get("opponent") or opponent
        
        # Build prop lines - ONLY for recommended props
        seen_stats = set()
        active_lines = []
        
        for pick in board_picks:
            stat = pick.get("stat_type", "")
            if not stat or stat in seen_stats:
                continue
            seen_stats.add(stat)
            
            line = pick.get("demon_line") or pick.get("goblin_line") or pick.get("line", 0)
            direction = pick.get("direction", "over")
            
            # All board picks are radar (PropVision recommendations)
            is_radar = True
            
            # Get DvP for this stat
            dvp_rank = pick.get("dvp_rank") or (get_dvp_rank(opponent, stat) if opponent else 15)
            dvp_color = pick.get("dvp_rank_color") or get_dvp_rank_color(dvp_rank)
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
                "odds": pick.get("demon_odds") or pick.get("goblin_odds") or pick.get("odds"),
                "is_radar": is_radar,  # True = Full Intel Suite
                "dvp_rank": dvp_rank,
                "dvp_rank_color": dvp_color,
                "dvp_modifier": round(dvp_mod, 3),
                "friction_label": friction,
                "season_avg": pick.get("season_avg"),
                "l5_avg": pick.get("l5_avg"),
                "l10_avg": pick.get("l10_avg"),
                "std_dev": pick.get("std_dev"),
                "hit_rates": {
                    "h5": pick.get("h5_rate", 0),
                    "h10": pick.get("h10_rate", 0),
                    "h5_over": pick.get("h5_over"),
                    "h10_over": pick.get("h10_over"),
                    "l5_avg": pick.get("l5_avg"),
                    "l10_avg": pick.get("l10_avg"),
                    "season_avg": pick.get("season_avg")
                },
                "pace_factor": pick.get("pace_factor", 1.0),
                "usage_ripple": pick.get("usage_bump_percent", 0)
            })
        
        # Get usage ripple info
        usage_ripple = None
        for pick in board_picks:
            if pick.get("usage_bump_percent", 0) > 0:
                usage_ripple = {
                    "bump_percent": pick.get("usage_bump_percent", 0),
                    "source": pick.get("usage_source", "teammate injury")
                }
                break
        
        return {
            "success": True,
            "player_name": first_pick.get("player_name", player_name),
            "player_id": first_pick.get("player_id"),
            "team": player_team,
            "position": first_pick.get("position", ""),
            "photo_url": first_pick.get("photo_url", ""),
            "opponent": opponent,
            "is_on_board": True,
            "lines": active_lines,
            "radar_picks": list(radar_stat_types),
            "usage_ripple": usage_ripple
        }
        
    except Exception as e:
        logger.error(f"[PROFILE] Error getting profile for {player_name}: {e}")
        return {
            "success": False,
            "error": str(e),
            "player_name": player_name,
            "is_on_board": False
        }
        
        # Get player info from first pick
        first_pick = all_picks[0]
        player_team = first_pick.get("team", "")
        opponent = first_pick.get("opponent_abbr") or first_pick.get("opponent") or opponent
        
        # Build prop lines with DvP data - dedupe by stat type
        seen_stats = set()
        active_lines = []
        radar_stat_types = []
        
        for pick in all_picks:
            stat = pick.get("stat_type", "")
            if not stat or stat in seen_stats:
                continue
            seen_stats.add(stat)
            
            line = pick.get("demon_line") or pick.get("goblin_line") or pick.get("line", 0)
            direction = pick.get("direction", "over")
            
            # Check if this is a radar pick (demon or goblin)
            is_radar = pick.get("is_demon") or pick.get("is_goblin") or \
                       pick.get("radar_score", 0) > 0 or pick.get("vault_score", 0) > 0
            
            if is_radar:
                radar_stat_types.append(stat)
            
            # Get DvP for this stat
            dvp_rank = pick.get("dvp_rank") or (get_dvp_rank(opponent, stat) if opponent else 15)
            dvp_color = pick.get("dvp_rank_color") or get_dvp_rank_color(dvp_rank)
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
                "odds": pick.get("demon_odds") or pick.get("goblin_odds") or pick.get("odds"),
                "is_radar": is_radar,
                "dvp_rank": dvp_rank,
                "dvp_rank_color": dvp_color,
                "dvp_modifier": round(dvp_mod, 3),
                "friction_label": friction,
                "season_avg": pick.get("season_avg"),
                "l5_avg": pick.get("l5_avg"),
                "l10_avg": pick.get("l10_avg"),
                "std_dev": pick.get("std_dev"),
                "hit_rates": {
                    "h5": pick.get("h5_rate", 0),
                    "h10": pick.get("h10_rate", 0),
                    "h5_over": pick.get("h5_over"),
                    "h10_over": pick.get("h10_over"),
                    "l5_avg": pick.get("l5_avg"),
                    "l10_avg": pick.get("l10_avg"),
                    "season_avg": pick.get("season_avg")
                },
                "pace_factor": pick.get("pace_factor", 1.0)
            })
        
        # Get usage ripple info if available
        usage_ripple = None
        for pick in all_picks:
            if pick.get("usage_bump_percent", 0) > 0:
                usage_ripple = {
                    "bump_percent": pick.get("usage_bump_percent", 0),
                    "source": pick.get("usage_source", "teammate injury")
                }
                break
        
        return {
            "success": True,
            "player_name": first_pick.get("player_name", player_name),
            "player_id": first_pick.get("player_id"),
            "team": player_team,
            "position": first_pick.get("position", ""),
            "photo_url": first_pick.get("photo_url", ""),
            "opponent": opponent,
            "lines": active_lines,
            "radar_picks": radar_stat_types,
            "usage_ripple": usage_ripple
        }
        
    except Exception as e:
        logger.error(f"[PROFILE] Error getting profile for {player_name}: {e}")
        return {
            "success": False,
            "error": str(e),
            "player_name": player_name
        }


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

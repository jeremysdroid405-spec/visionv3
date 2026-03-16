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
    Get tactical profile for a player with ALL available props.
    
    CONDITIONAL STATE HIGHLIGHTING:
    - Fetches ALL available props from dg_live_props
    - Cross-references with PropVision recommendations (radar_picks, goblin_vault, front_lines)
    - Target-Lock props (is_radar=true) get Full Intel Suite on click
    - Standard props (is_radar=false) get basic L5/L10/Season stats on click
    
    Returns:
    - lines: ALL prop lines with is_radar flag for Target-Lock identification
    - radar_picks: List of {stat_type, line, direction} that are PropVision objectives
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
        
        # ===== STEP 1: Fetch ALL available props from dg_live_props =====
        all_props = await db.dg_live_props.find(
            {"player_name": player_name_regex},
            {"_id": 0}
        ).to_list(100)
        
        # ===== STEP 2: Fetch PropVision recommendations (Target-Lock props) =====
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
        
        # Combine all recommendations
        board_picks = radar_picks + vault_picks + front_picks
        
        # ===== STEP 3: Build Target-Lock lookup (stat_type + line + direction) =====
        # This set identifies which specific props are PropVision recommendations
        target_lock_keys = set()
        target_lock_details = {}  # Store full intel for Target-Lock props
        
        for pick in board_picks:
            stat = pick.get("stat_type", "")
            line = pick.get("demon_line") or pick.get("goblin_line") or pick.get("line", 0)
            direction = (pick.get("direction") or "over").lower()
            
            if stat and line:
                key = f"{stat}|{line}|{direction}"
                target_lock_keys.add(key)
                target_lock_details[key] = pick
        
        # ===== STEP 4: Get player info from props or board =====
        player_team = ""
        player_position = ""
        photo_url = ""
        detected_opponent = opponent
        
        if all_props:
            first_prop = all_props[0]
            player_team = first_prop.get("home_team") if first_prop.get("direction") == "home" else first_prop.get("away_team", "")
            detected_opponent = first_prop.get("away_team") if player_team == first_prop.get("home_team") else first_prop.get("home_team", "")
        
        if board_picks:
            first_pick = board_picks[0]
            player_team = first_pick.get("team") or player_team
            player_position = first_pick.get("position", "")
            photo_url = first_pick.get("photo_url", "")
            detected_opponent = first_pick.get("opponent_abbr") or first_pick.get("opponent") or detected_opponent
        
        # ===== STEP 5: Build ALL prop lines with Target-Lock identification =====
        active_lines = []
        seen_props = set()  # Dedupe by stat_type + line + direction
        
        for prop in all_props:
            stat = prop.get("stat_type_extracted") or prop.get("market", "").replace("player_", "").upper()
            line = prop.get("line", 0)
            direction = (prop.get("direction") or "over").lower()
            
            if not stat or not line:
                continue
            
            # Dedupe key
            dedupe_key = f"{stat}|{line}|{direction}"
            if dedupe_key in seen_props:
                continue
            seen_props.add(dedupe_key)
            
            # Check if this prop is a Target-Lock (PropVision recommendation)
            target_key = f"{stat}|{line}|{direction}"
            is_radar = target_key in target_lock_keys
            
            # Get hit rates from prop
            hit_rates = prop.get("hit_rates", {})
            l5_data = hit_rates.get("l5", {})
            l10_data = hit_rates.get("l10", {})
            season_data = hit_rates.get("season", {})
            
            # Build base prop line (available for all props)
            prop_line = {
                "stat_type": stat,
                "line": line,
                "direction": direction,
                "odds": prop.get("price"),
                "is_radar": is_radar,
                "l5_avg": l5_data.get("avg"),
                "l10_avg": l10_data.get("avg"),
                "season_avg": season_data.get("avg"),
                "hit_rates": {
                    "l5": l5_data.get("hit_rate", 0) * 100 if l5_data.get("hit_rate") else 0,
                    "l10": l10_data.get("hit_rate", 0) * 100 if l10_data.get("hit_rate") else 0,
                    "season": season_data.get("hit_rate", 0) * 100 if season_data.get("hit_rate") else 0,
                    "l5_avg": l5_data.get("avg"),
                    "l10_avg": l10_data.get("avg"),
                    "season_avg": season_data.get("avg")
                }
            }
            
            # If Target-Lock, add Full Intel Suite data
            if is_radar and target_key in target_lock_details:
                board_pick = target_lock_details[target_key]
                
                # Get DvP for this stat
                dvp_rank = board_pick.get("dvp_rank") or (get_dvp_rank(detected_opponent, stat) if detected_opponent else 15)
                dvp_color = board_pick.get("dvp_rank_color") or get_dvp_rank_color(dvp_rank)
                dvp_mod = calculate_dvp_modifier(detected_opponent, stat) if detected_opponent else 0.5
                
                # Determine friction level
                if dvp_rank >= 25:
                    friction = "Low Friction (Soft Defense)"
                elif dvp_rank <= 5:
                    friction = "High Friction (Elite Defense)"
                else:
                    friction = "Standard Friction"
                
                # Add Full Intel Suite data for Target-Lock props
                prop_line.update({
                    "dvp_rank": dvp_rank,
                    "dvp_rank_color": dvp_color,
                    "dvp_modifier": round(dvp_mod, 3),
                    "friction_label": friction,
                    "std_dev": board_pick.get("std_dev"),
                    "pace_factor": board_pick.get("pace_factor", 1.0),
                    "usage_ripple": board_pick.get("usage_bump_percent", 0)
                })
            
            active_lines.append(prop_line)
        
        # Sort: Target-Lock props first, then by stat_type
        active_lines.sort(key=lambda x: (0 if x.get("is_radar") else 1, x.get("stat_type", "")))
        
        # ===== STEP 6: Get usage ripple info =====
        usage_ripple = None
        for pick in board_picks:
            if pick.get("usage_bump_percent", 0) > 0:
                usage_ripple = {
                    "bump_percent": pick.get("usage_bump_percent", 0),
                    "source": pick.get("usage_source", "teammate injury")
                }
                break
        
        # Build radar_picks list (stat_type only for backward compat)
        radar_stat_types = list(set(p.get("stat_type", "") for p in board_picks if p.get("stat_type")))
        
        return {
            "success": True,
            "player_name": board_picks[0].get("player_name", player_name) if board_picks else player_name,
            "player_id": board_picks[0].get("player_id") if board_picks else None,
            "team": player_team,
            "position": player_position,
            "photo_url": photo_url,
            "opponent": detected_opponent,
            "is_on_board": len(board_picks) > 0,
            "lines": active_lines,
            "radar_picks": radar_stat_types,
            "target_locks": [{"stat_type": k.split("|")[0], "line": float(k.split("|")[1]), "direction": k.split("|")[2]} for k in target_lock_keys],
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

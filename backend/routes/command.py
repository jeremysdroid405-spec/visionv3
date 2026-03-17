"""
Command Post Routes
===================
Risk Assessment Hub API endpoints.

SSOT ARCHITECTURE: All data comes from MongoDB (Master Hub / Cached Board).
NO external API calls allowed in this file.

Endpoints:
- POST /api/command/simulate - Simulate parlay configuration
- GET /api/command/search - Search players from Master Hub
- GET /api/command/profile/{player_name} - Get tactical profile for player
"""
import logging
import os
from typing import List, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from services.simulation_service import get_simulation_engine
from services.dvp_service import get_dvp_rank, get_dvp_rank_color, calculate_dvp_modifier
from services.intel_suite_calculator import get_intel_calculator
from services.stats_service import calculate_coupled_stats  # CRITICAL: For hit rate math
from utils.player_lookup import build_player_lookup, get_player_by_name

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/command", tags=["Command Post"])

# Database reference (set via dependency injection)
_db = None

def set_db(db):
    """Set database reference for Command Post routes."""
    global _db
    _db = db


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
    SSOT: Search players from NBA Master Hub.
    
    Returns player list with basic info for Command Post selection.
    All data comes from nba_master_hub_2026 - NO external API calls.
    """
    if _db is None:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        # Build lookup cache if needed
        lookup = await build_player_lookup(_db)
        
        # Search by name (case-insensitive)
        query_lower = query.lower()
        results = []
        
        for name_key, player in lookup.items():
            if query_lower in name_key:
                results.append({
                    "id": player.get("player_id"),
                    "player_name": player.get("display_name", ""),
                    "team": player.get("team", ""),
                    "position": player.get("position", ""),
                    "headshot_url": player.get("headshot_url"),
                    "nba_id": player.get("nba_id"),
                    "has_stats": bool(player.get("baseline_stats"))
                })
                
                if len(results) >= limit:
                    break
        
        return {
            "success": True,
            "query": query,
            "count": len(results),
            "players": results,
            "source": "master_hub"
        }
        
    except Exception as e:
        logger.error(f"[COMMAND] Search error: {e}")
        return {"success": False, "players": [], "error": str(e)}


# CONSOLIDATED: Player lookup moved to /app/backend/utils/player_lookup.py
# Use: from utils.player_lookup import build_player_lookup, get_player_by_name


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
        
        # ===== STEP 4: Get player info from nba_master_hub_2026 =====
        # This is the SINGLE SOURCE OF TRUTH for all player data and stats
        player_team = ""
        player_position = ""
        photo_url = ""
        player_id = None
        nba_id = None
        espn_id = None
        baseline_stats = {}
        detected_opponent = opponent
        
        # Get player data from master roster using shared lookup utility
        master_player = await get_player_by_name(db, player_name)
        
        if master_player:
            photo_url = master_player.get("headshot_url", "")
            player_team = master_player.get("team", "")
            player_position = master_player.get("position", "")
            player_id = master_player.get("player_id")
            # Check both nba_id and nba_player_id (master hub uses nba_player_id)
            nba_id = master_player.get("nba_id") or master_player.get("nba_player_id")
            espn_id = master_player.get("espn_id")
            baseline_stats = master_player.get("baseline_stats", {})
        
        # Fallback to props data if needed
        if all_props:
            first_prop = all_props[0]
            if not player_team:
                player_team = first_prop.get("home_team") if first_prop.get("direction") == "home" else first_prop.get("away_team", "")
            detected_opponent = first_prop.get("away_team") if player_team == first_prop.get("home_team") else first_prop.get("home_team", "")
        
        # Override with board picks data if available
        if board_picks:
            first_pick = board_picks[0]
            player_team = first_pick.get("team") or player_team
            player_position = first_pick.get("position") or player_position
            if not photo_url:
                photo_url = first_pick.get("photo_url", "")
            detected_opponent = first_pick.get("opponent_abbr") or first_pick.get("opponent") or detected_opponent
        
        # ===== STEP 5: Build ALL prop lines with COUPLED stats from MASTER HUB =====
        # CRITICAL FIX: Both L5_avg and L5_hit_rate MUST come from the SAME array
        active_lines = []
        seen_props = set()  # Dedupe by stat_type + line + direction
        
        # Get game_logs from master hub for coupled calculations
        game_logs = master_player.get("game_logs", []) if master_player else []
        
        # First pass: collect all props with their odds
        props_by_stat = {}  # {stat_type: [props]}
        
        for prop in all_props:
            stat = prop.get("stat_type_extracted") or prop.get("market", "").replace("player_", "").upper()
            line = prop.get("line", 0)
            direction = (prop.get("direction") or "over").lower()
            odds = prop.get("price", -110)
            
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
            
            # ===== CRITICAL: COUPLED STATS CALCULATION =====
            # Calculate L5/L10 avg AND hit_rate from the EXACT SAME game array
            # This guarantees mathematical consistency (no more 80% hit rate with 8.0 avg)
            coupled_stats = calculate_coupled_stats(game_logs, stat, line)
            
            l5_data = coupled_stats["l5"]
            l10_data = coupled_stats["l10"]
            season_data = coupled_stats["season"]
            
            # Log for verification
            if l5_data["total_games"] > 0:
                logger.debug(
                    f"[COUPLED_MATH] {player_name} {stat} O{line}: "
                    f"L5 avg={l5_data['avg']} hit_rate={l5_data['hit_rate']:.0%} "
                    f"({l5_data['games_over']}/{l5_data['total_games']})"
                )
            
            # Build base prop line with COUPLED stats (avg AND hit_rate from same source)
            prop_line = {
                "stat_type": stat,
                "line": line,
                "direction": direction,
                "odds": odds,
                "is_radar": is_radar,
                # COUPLED averages
                "l5_avg": l5_data["avg"],
                "l10_avg": l10_data["avg"],
                "season_avg": season_data["avg"],
                # COUPLED hit rates (from SAME array as averages - GUARANTEED)
                "h5_rate": round(l5_data["hit_rate"] * 100, 1),
                "h10_rate": round(l10_data["hit_rate"] * 100, 1),
                "season_hit_rate": round(season_data["hit_rate"] * 100, 1),
                # Detailed hit_rates object for frontend
                "hit_rates": {
                    "l5": {
                        "avg": l5_data["avg"],
                        "hit_rate": l5_data["hit_rate"],
                        "games_over": l5_data["games_over"],
                        "total_games": l5_data["total_games"]
                    },
                    "l10": {
                        "avg": l10_data["avg"],
                        "hit_rate": l10_data["hit_rate"],
                        "games_over": l10_data["games_over"],
                        "total_games": l10_data["total_games"]
                    },
                    "season": {
                        "avg": season_data["avg"],
                        "hit_rate": season_data["hit_rate"],
                        "games_over": season_data["games_over"],
                        "total_games": season_data["total_games"]
                    },
                    # Convenience aliases
                    "l5_avg": l5_data["avg"],
                    "l10_avg": l10_data["avg"],
                    "season_avg": season_data["avg"],
                    "h5": round(l5_data["hit_rate"] * 100, 1),
                    "h10": round(l10_data["hit_rate"] * 100, 1)
                },
                # Mark as coupled for debugging
                "stats_coupled": True,
                "stats_source": "game_logs_coupled"
            }
            
            # Collect props by stat type for anchor-based classification
            if stat not in props_by_stat:
                props_by_stat[stat] = []
            props_by_stat[stat].append(prop_line)
            
            active_lines.append(prop_line)
        
        # ===== STEP 5.5: ANCHOR-BASED TIER CLASSIFICATION =====
        # For each stat type, find the "standard" (anchor) line and classify all lines relative to it
        standard_lines = {}  # {stat_type: anchor_line_value}
        
        for stat_type, stat_props in props_by_stat.items():
            if not stat_props:
                continue
            
            # Find the standard line: closest to -110 juice (most balanced odds)
            def odds_distance_from_fair(prop):
                prop_odds = prop.get("odds") or -110
                # Distance from -110 (fair odds)
                if prop_odds >= 100:
                    return abs(prop_odds - 100) + 10  # Plus lines are further from fair
                else:
                    return abs(prop_odds + 110)  # Minus lines, -110 is baseline
            
            # Sort by odds distance from fair value
            sorted_props = sorted(stat_props, key=odds_distance_from_fair)
            standard_line_value = sorted_props[0]["line"] if sorted_props else None
            standard_lines[stat_type] = standard_line_value
        
        # Now apply GOBLIN/DEMON/STANDARD classification based on anchor
        for prop_line in active_lines:
            stat = prop_line["stat_type"]
            line = prop_line["line"]
            anchor = standard_lines.get(stat)
            
            if anchor is None:
                # No anchor found, use fallback logic
                prop_line["tier_style"] = "standard"
                prop_line["tier_label"] = "STANDARD"
                prop_line["is_goblin"] = False
                prop_line["is_demon"] = False
                prop_line["standard_line"] = None
            elif line == anchor:
                # This IS the standard line
                prop_line["tier_style"] = "standard"
                prop_line["tier_label"] = "STANDARD"
                prop_line["is_goblin"] = False
                prop_line["is_demon"] = False
                prop_line["is_anchor"] = True
                prop_line["standard_line"] = anchor
            elif line < anchor:
                # GOBLIN: Alt Over LOWER than standard (easier to hit)
                prop_line["tier_style"] = "green"
                prop_line["tier_label"] = "GOBLIN"
                prop_line["is_goblin"] = True
                prop_line["is_demon"] = False
                prop_line["standard_line"] = anchor
                prop_line["ladder_position"] = "below"
                prop_line["gap_from_anchor"] = round(anchor - line, 1)
            else:
                # DEMON: Alt Over HIGHER than standard (harder to hit)
                prop_line["tier_style"] = "red"
                prop_line["tier_label"] = "DEMON"
                prop_line["is_goblin"] = False
                prop_line["is_demon"] = True
                prop_line["standard_line"] = anchor
                prop_line["ladder_position"] = "above"
                prop_line["gap_from_anchor"] = round(line - anchor, 1)
            
            # If Target-Lock, add Full Intel Suite data
            target_key = f"{stat}|{line}|{prop_line.get('direction', 'over')}"
            if prop_line.get("is_radar") and target_key in target_lock_details:
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
                
                # Calculate full Intel Suite using the calculator
                intel_calculator = get_intel_calculator(db)
                intel_suite = await intel_calculator.calculate_intel_suite(
                    player_name=player_name,
                    stat_type=stat,
                    line=line,
                    direction=direction,
                    opponent=detected_opponent,
                    board_pick=board_pick
                )
                
                # Add Full Intel Suite data for Target-Lock props
                prop_line.update({
                    "dvp_rank": dvp_rank,
                    "dvp_rank_color": dvp_color,
                    "dvp_modifier": round(dvp_mod, 3),
                    "friction_label": friction,
                    "std_dev": board_pick.get("std_dev"),
                    "pace_factor": board_pick.get("pace_factor", 1.0),
                    "usage_ripple_legacy": board_pick.get("usage_bump_percent", 0),
                    # === FULL INTEL SUITE (only for is_radar=true) ===
                    "intel_suite": intel_suite
                })
        
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
        
        # ===== STEP 7: Fetch Player Badges from Context Engine =====
        badges = []
        vision_insight = None
        
        try:
            from services.badge_resolver import get_badge_resolver
            badge_resolver = get_badge_resolver(db)
            
            # Get player's NBA ID for badge lookup
            lookup_id = nba_id or player_id
            if lookup_id:
                resolved_badges = await badge_resolver.resolve_badges(int(lookup_id))
                badges = [{
                    "id": b.get("badge_key"),
                    "label": b.get("display"),
                    "icon": b.get("icon"),
                    "color": b.get("color"),
                    "severity": b.get("severity"),
                    "headline": b.get("headline")
                } for b in resolved_badges]
                
                # Generate narrative vision insight using the upgraded generator
                if badges:
                    # Get baseline stats for narrative
                    pts_baseline = baseline_stats.get("PTS", {})
                    stats_for_narrative = {
                        "ppg": pts_baseline.get("season_avg", 0),
                        "ppg_l5": pts_baseline.get("l5_avg", 0),
                        "ppg_l10": pts_baseline.get("l10_avg", 0)
                    }
                    
                    vision_insight = badge_resolver.generate_narrative_insight(
                        player_name=player_name,
                        badges=resolved_badges,
                        stats=stats_for_narrative
                    )
        except Exception as e:
            logger.debug(f"[PROFILE] Badge resolution skipped: {e}")
        
        return {
            "success": True,
            "player_name": master_player.get("display_name") if master_player else (board_picks[0].get("player_name", player_name) if board_picks else player_name),
            "player_id": player_id,
            "nba_id": nba_id,
            "espn_id": espn_id,
            "team": player_team,
            "position": player_position,
            "photo_url": photo_url,
            "headshot_url": photo_url,
            "opponent": detected_opponent,
            "is_on_board": len(board_picks) > 0,
            "lines": active_lines,
            "baseline_stats": baseline_stats,
            "radar_picks": radar_stat_types,
            "target_locks": [{"stat_type": k.split("|")[0], "line": float(k.split("|")[1]), "direction": k.split("|")[2]} for k in target_lock_keys],
            "usage_ripple": usage_ripple,
            "badges": badges,
            "vision_insight": vision_insight,
            # Anchor-based tier system metadata
            "standard_lines": standard_lines,
            "tier_logic": {
                "description": "Anchor-based tier classification",
                "standard": "Line with odds closest to -110 (fair value)",
                "goblin": "Alt Over lines LOWER than standard (safer, higher hit rate)",
                "demon": "Alt Over lines HIGHER than standard (riskier, ladder plays)"
            }
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

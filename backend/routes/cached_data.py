"""
Cached Data Routes
==================
Endpoints for reading cached/warehouse data with zero API calls.
"""
from fastapi import APIRouter, HTTPException, Query, Response
from typing import Optional, Dict, List, Set
import logging
import sys
from datetime import datetime, timezone

# Import DvP service and config for real matchup data
from services.dvp_service import (
    get_dvp_rank, 
    get_dvp_rank_color, 
    get_dvp_label,
    calculate_dvp_modifier
)
from config.settings import TEAM_PACE, LEAGUE_AVG_PACE, DVP_RANKINGS
from services.vision_summary_service import VisionSummaryService
from services.sidecar.hook_bait_detector import get_hook_bait_detector, HookBaitDetector

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Cached Data"])

# Reference to DemonGoblinEngine (set via dependency injection)
_demon_goblin_engine = None

# Vision Summary Service singleton
_vision_service = None

# Hook/Bait Detector Sidecar instance
_hook_bait_detector: Optional[HookBaitDetector] = None

# PERFORMANCE CACHE: Store board membership to avoid re-computing for each player
# Format: {player_name_lower: {"stat|line": "Board Name", ...}}
_board_membership_cache: Dict[str, Dict[str, str]] = {}
_board_cache_timestamp: Optional[datetime] = None
_BOARD_CACHE_TTL_SECONDS = 60  # Refresh every 60 seconds

def get_vision_service():
    global _vision_service
    if _vision_service is None:
        _vision_service = VisionSummaryService()
    return _vision_service


def get_detector():
    """Get the hook/bait detector instance"""
    global _hook_bait_detector
    if _hook_bait_detector is None:
        engine = get_engine()
        _hook_bait_detector = get_hook_bait_detector(engine.db)
    return _hook_bait_detector


def set_cached_data_engine(engine):
    """Set the demon goblin engine reference."""
    global _demon_goblin_engine
    _demon_goblin_engine = engine


def get_engine():
    """Get the demon goblin engine instance."""
    if _demon_goblin_engine is None:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    return _demon_goblin_engine


async def get_board_membership(player_name: str) -> Dict[str, str]:
    """
    Fast lookup of which boards a player is on.
    Uses a module-level cache that refreshes every 60 seconds.
    
    Returns:
        Dict mapping "STAT|LINE" to board name (e.g., {"PTS|14.5": "Safe Haven"})
    """
    global _board_membership_cache, _board_cache_timestamp
    
    now = datetime.now(timezone.utc)
    
    # Check if cache needs refresh (older than TTL or empty)
    cache_expired = (
        _board_cache_timestamp is None or 
        (now - _board_cache_timestamp).total_seconds() > _BOARD_CACHE_TTL_SECONDS
    )
    
    if cache_expired:
        # Refresh the entire cache in one batch
        logger.info("[BOARD_CACHE] Refreshing board membership cache...")
        engine = get_engine()
        picks_service = engine.picks_getter_service
        
        # Fetch all boards in parallel-ish (they're cached internally)
        safe_haven_data = await picks_service.get_goblin_vault()
        war_zone_data = await picks_service.get_war_zone()
        front_lines_data = await picks_service.get_front_lines()
        
        # Build new cache
        new_cache: Dict[str, Dict[str, str]] = {}
        
        # War Zone first (highest priority)
        for pick in war_zone_data.get("picks", []):
            pname = pick.get("player_name", "").lower()
            stat_type = pick.get("stat_type", "")
            line = pick.get("line", 0)
            key = f"{stat_type}|{line}"
            if pname not in new_cache:
                new_cache[pname] = {}
            new_cache[pname][key] = "War Zone"
        
        # Front Lines
        for pick in front_lines_data.get("picks", []):
            pname = pick.get("player_name", "").lower()
            stat_type = pick.get("stat_type", "")
            line = pick.get("line", 0)
            key = f"{stat_type}|{line}"
            if pname not in new_cache:
                new_cache[pname] = {}
            if key not in new_cache[pname]:  # Don't override higher priority
                new_cache[pname][key] = "Front Lines"
        
        # Safe Haven
        for pick in safe_haven_data.get("picks", []):
            pname = pick.get("player_name", "").lower()
            stat_type = pick.get("stat_type", "")
            line = pick.get("line", 0)
            key = f"{stat_type}|{line}"
            if pname not in new_cache:
                new_cache[pname] = {}
            if key not in new_cache[pname]:  # Don't override higher priority
                new_cache[pname][key] = "Safe Haven"
        
        _board_membership_cache = new_cache
        _board_cache_timestamp = now
        logger.info(f"[BOARD_CACHE] Cached {len(new_cache)} players' board memberships")
    
    # Fast lookup
    return _board_membership_cache.get(player_name.lower(), {})


# ==================== SIDECAR FEATURE TOGGLE ENDPOINTS ====================

@router.get("/v3/sidecar/status")
async def get_sidecar_status():
    """
    Get the current status of sidecar modules (Hook Protector & Bait Detector).
    """
    try:
        detector = get_detector()
        return {
            "success": True,
            "hook_bait_detector": {
                "enabled": detector.is_enabled(),
                "hook_line_tolerance": detector.HOOK_LINE_TOLERANCE,
                "hook_mode_frequency_min": detector.HOOK_MODE_FREQUENCY_MIN,
                "bait_high_volume_floor": detector.BAIT_HIGH_VOLUME_FLOOR,
                "description": "Detects hook lines (near Mode) and bait lines (below Median)"
            }
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "hook_bait_detector": {"enabled": False}
        }


@router.post("/v3/sidecar/toggle")
async def toggle_sidecar(enabled: bool = Query(..., description="Enable or disable the sidecar module")):
    """
    Toggle the Hook Protector & Bait Detector sidecar module on/off.
    This is the kill switch for the feature.
    """
    try:
        detector = get_detector()
        detector.toggle(enabled)
        return {
            "success": True,
            "message": f"Hook/Bait Detector {'ENABLED' if enabled else 'DISABLED'}",
            "enabled": enabled
        }
    except Exception as e:
        logger.error(f"[SIDECAR] Toggle failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/v3/sidecar/analyze/{player_name}")
async def analyze_player_sidecar(
    player_name: str,
    stat_type: str = Query("PTS", description="Stat type to analyze"),
    line: float = Query(10.0, description="DFS line to check")
):
    """
    Debug endpoint: Analyze a specific player/line for hook risk and bait detection.
    """
    try:
        detector = get_detector()
        result = await detector.analyze_prop(player_name, stat_type, line)
        return {
            "success": True,
            "player_name": player_name,
            "stat_type": stat_type,
            "line": line,
            "analysis": result
        }
    except Exception as e:
        logger.error(f"[SIDECAR] Analysis failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))



@router.post("/v3/trigger-board-enrichment")
async def trigger_board_enrichment(background: bool = Query(False, description="Run in background (fire-and-forget)")):
    """
    Manual trigger for Board Intelligence Enrichment.
    
    Runs the AI-Weighted Waterfall selection and full Intel Suite enrichment
    for all 3 boards (Safe Haven, Front Lines, War Zone).
    
    Args:
        background: If True, starts enrichment and returns immediately without waiting.
    
    Use this to force refresh the board intelligence after a sync.
    """
    import asyncio
    
    try:
        engine = get_engine()
        from services.board_intelligence_service import run_board_intelligence_enrichment
        
        if background:
            # Fire-and-forget mode - returns immediately
            async def _run_bg():
                try:
                    result = await run_board_intelligence_enrichment(engine.db)
                    logger.info(f"[TRIGGER_ENRICHMENT_BG] Completed: {result.get('enriched', 0)} enriched, {result.get('errors', 0)} errors")
                except Exception as e:
                    logger.error(f"[TRIGGER_ENRICHMENT_BG] Failed: {e}")
            
            asyncio.create_task(_run_bg())
            return {
                "success": True,
                "message": "Board Intelligence Enrichment started in background",
                "mode": "background"
            }
        else:
            # Synchronous mode - waits for completion
            result = await run_board_intelligence_enrichment(engine.db)
            return {
                "success": True,
                "message": "Board Intelligence Enrichment completed",
                "result": result
            }
    except Exception as e:
        logger.error(f"[TRIGGER_ENRICHMENT] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))



@router.get("/v3/static-shell")
async def get_static_shell():
    """
    Get STATIC SHELL data (24h TTL)
    Contains: Player metadata, teams, positions, historical stats
    Does NOT contain: Live betting lines
    
    Use this for initial page load - instant render of player cards
    """
    engine = get_engine()
    shell = await engine.get_static_shell()
    
    return {
        "success": True,
        "cache_hit": shell.get("cache_hit", False),
        "cache_age_seconds": shell.get("cache_age_seconds", 0),
        "sync_date": shell.get("sync_date"),
        "players_count": len(shell.get("players", [])),
        "players": shell.get("players", []),
        "trending": shell.get("trending", [])
    }


@router.get("/v3/enrichment-status")
async def get_enrichment_status():
    """
    Check the status of board intelligence enrichment cache.
    
    Returns:
        - Total enriched players count
        - Last enrichment timestamp
        - Players per board (Safe Haven, Front Lines, War Zone)
        - Cache staleness status
    
    Use this to verify background enrichment is working.
    """
    try:
        engine = get_engine()
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat().replace('+00:00', 'Z')
        
        # Count enriched players per board
        pipeline = [
            {"$unwind": "$props"},
            {"$match": {
                "props.commence_time": {"$gt": now_iso},
                "props.is_vision_enriched": True
            }},
            {"$group": {
                "_id": "$props.board",
                "count": {"$sum": 1}
            }}
        ]
        board_counts = {}
        async for doc in engine.db.dg_cached_board.aggregate(pipeline):
            board_counts[doc["_id"] or "unassigned"] = doc["count"]
        
        # Get latest enrichment timestamp
        latest = await engine.db.dg_cached_board.find_one(
            {"props.vision_enriched_at": {"$exists": True}},
            {"props.vision_enriched_at": 1, "_id": 0},
            sort=[("props.vision_enriched_at", -1)]
        )
        last_enriched_at = None
        if latest and latest.get("props"):
            for prop in latest.get("props", []):
                if prop.get("vision_enriched_at"):
                    last_enriched_at = prop["vision_enriched_at"]
                    break
        
        # Total enriched (all time)
        total_enriched = sum(board_counts.values())
        
        # Check staleness (>5 min since last enrichment)
        is_stale = True
        if last_enriched_at:
            try:
                last_dt = datetime.fromisoformat(last_enriched_at.replace('Z', '+00:00'))
                age_seconds = (now - last_dt).total_seconds()
                is_stale = age_seconds > 300  # >5 min is stale
            except:
                pass
        
        return {
            "status": "healthy" if total_enriched >= 30 and not is_stale else "degraded",
            "total_enriched": total_enriched,
            "last_enriched_at": last_enriched_at,
            "boards": {
                "safe_haven": board_counts.get("safe_haven", 0),
                "front_lines": board_counts.get("front_lines", 0),
                "war_zone": board_counts.get("war_zone", 0),
                "unassigned": board_counts.get("unassigned", 0)
            },
            "is_stale": is_stale,
            "timestamp": now.isoformat()
        }
    except Exception as e:
        logger.error(f"[ENRICHMENT_STATUS] Error: {e}")
        return {
            "status": "error",
            "error": str(e),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }


@router.get("/v3/live-lines")
async def get_live_lines():
    """
    Get DYNAMIC PULSE data (60s TTL)
    Contains ONLY: Live betting lines (price, point, demon/goblin tags)
    
    Use this to hydrate cards with live data after initial render
    Lightweight endpoint - minimal payload
    """
    engine = get_engine()
    lines = await engine.get_live_lines()
    
    # Count totals
    total_lines = sum(len(v) for v in lines.get("lines", {}).values())
    total_demons = sum(
        sum(1 for line in player_lines if line.get("is_demon"))
        for player_lines in lines.get("lines", {}).values()
    )
    total_goblins = sum(
        sum(1 for line in player_lines if line.get("is_goblin"))
        for player_lines in lines.get("lines", {}).values()
    )
    
    return {
        "success": True,
        "cache_hit": lines.get("cache_hit", False),
        "cache_age_seconds": lines.get("cache_age_seconds", 0),
        "last_update": lines.get("last_update"),
        "total_lines": total_lines,
        "total_demons": total_demons,
        "total_goblins": total_goblins,
        "players_count": len(lines.get("lines", {})),
        "lines": lines.get("lines", {})
    }


@router.get("/v3/hydrated-board")
async def get_hydrated_board():
    """
    DEPRECATED - Use /api/v3/cached-props instead.
    Redirects to cached board for backward compatibility.
    """
    return await get_cached_props()


@router.get("/v3/cached-props")
async def get_cached_props(include_locked: bool = True):
    """
    THE PRIMARY ENDPOINT - Reads ONLY from MongoDB.
    NO Odds API calls. Zero credit usage.
    
    Returns the full cached board with:
    - All players grouped by props (with locked status marked)
    - Trending 10
    - Demon/Goblin flags
    - Hit rates
    - AI Vision summaries
    
    Filter with include_locked=false to hide locked games from frontend.
    """
    engine = get_engine()
    result = await engine.get_cached_board(include_locked=include_locked)
    return result


@router.get("/v3/test-badges/{player_name}")
async def test_badges(player_name: str):
    """Test endpoint to check badge resolution."""
    engine = get_engine()
    
    badges = []
    
    try:
        # Get master hub collection from the picks_getter_service
        master_hub = engine.picks_getter_service.master_hub
        
        # Get master hub data
        hub_player = await master_hub.find_one(
            {"$or": [
                {"display_name": player_name},
                {"display_name": {"$regex": f"^{player_name}$", "$options": "i"}}
            ]},
            {"_id": 0, "baseline_stats": 1, "bdl_game_logs": 1, "display_name": 1}
        )
        
        if not hub_player:
            return {"error": f"No hub player found for: {player_name}"}
        
        baseline_stats = hub_player.get("baseline_stats", {})
        game_logs = hub_player.get("bdl_game_logs", []) or []
        
        # Check LOCKED_IN: L5 PPG > Season PPG + 5
        pts_stats = baseline_stats.get("PTS", {})
        season_ppg = pts_stats.get("season_avg", 0) if isinstance(pts_stats, dict) else pts_stats
        
        l5_ppg = 0
        if game_logs and len(game_logs) >= 5:
            l5_pts = [g.get("pts", 0) or 0 for g in game_logs[:5]]
            l5_ppg = sum(l5_pts) / len(l5_pts) if l5_pts else 0
            
            if l5_ppg > season_ppg + 5:
                badges.append({
                    "badge_key": "locked_in",
                    "display": "Locked In",
                    "description": f"L5 avg ({l5_ppg:.1f}) is +{l5_ppg - season_ppg:.1f} above season ({season_ppg:.1f})"
                })
        
        return {
            "player_name": hub_player.get("display_name"),
            "season_ppg": season_ppg,
            "l5_ppg": round(l5_ppg, 1),
            "diff": round(l5_ppg - season_ppg, 1) if season_ppg else 0,
            "game_logs_count": len(game_logs),
            "badges": badges,
            "badge_keys": [b["badge_key"] for b in badges]
        }
        
    except Exception as e:
        return {"error": str(e)}


async def resolve_context_badges(engine, player_name: str, player_data: dict) -> tuple:
    """
    Resolve all 10 context badges for a player.
    
    Badge Registry:
    1. locked_in: L5 PPG > Season PPG + 5
    2. milestone: Stat avg within 5% of round milestone (20, 25, 30...)
    3. gassed: Back-to-back game (2nd night)
    4. home_cookin: Home PPG 15%+ higher than Away
    5. jet_lag: Road game + traveled >1000mi
    6. legal_noise: Active legal/personal news flag
    7. distraction: Trade rumors or drama
    8. revenge: Playing against former team
    9. pay_day: Contract year
    10. deep_water: Elimination/playoff game 5+
    
    Returns: (badges_list, badge_keys_list)
    """
    badges = []
    
    try:
        master_hub = engine.picks_getter_service.master_hub
        db = master_hub.database
        context_engine = db['nba_context_engine']
        
        # Get master hub data for stats-based badges
        hub_player = await master_hub.find_one(
            {"$or": [
                {"display_name": player_name},
                {"display_name": {"$regex": f"^{player_name}$", "$options": "i"}}
            ]},
            {"_id": 0, "baseline_stats": 1, "bdl_game_logs": 1, "game_logs": 1, 
             "nba_player_id": 1, "nba_id": 1, "player_id": 1, "advanced_stats": 1, "bdl_id": 1}
        )
        
        if not hub_player:
            return badges, []
        
        baseline_stats = hub_player.get("baseline_stats", {})
        # Prefer bdl_game_logs, fallback to game_logs
        game_logs = hub_player.get("bdl_game_logs") or hub_player.get("game_logs") or []
        
        # Get player's NBA ID for context_engine lookup
        player_nba_id = (hub_player.get("nba_player_id") or 
                        hub_player.get("nba_id") or 
                        hub_player.get("player_id"))
        
        # ===== 1. LOCKED_IN: L5 PPG > Season PPG + 5 =====
        pts_stats = baseline_stats.get("PTS", {})
        season_ppg = pts_stats.get("season_avg", 0) if isinstance(pts_stats, dict) else 0
        
        if season_ppg and game_logs and len(game_logs) >= 5:
            l5_pts = [g.get("pts", 0) or 0 for g in game_logs[:5]]
            l5_ppg = sum(l5_pts) / len(l5_pts) if l5_pts else 0
            
            if l5_ppg > season_ppg + 5:
                badges.append({
                    "badge_key": "locked_in",
                    "display": "Locked In",
                    "icon": "Target",
                    "color": "#06b6d4",
                    "description": f"L5 avg ({l5_ppg:.1f}) is +{l5_ppg - season_ppg:.1f} above season ({season_ppg:.1f})",
                    "severity": 8
                })
        
        # ===== 2. MILESTONE: Career milestone tracking =====
        # Try both NBA API and static data, use whichever has more recent/higher stats
        milestone = None
        try:
            # Try static data FIRST (more reliable, manually curated)
            from data.career_milestones import get_best_milestone
            milestone = get_best_milestone(player_name)
        except Exception as e:
            logger.debug(f"Static milestone check failed for {player_name}: {e}")
        
        # ===== 2. MILESTONE: Career milestone tracking (static data) =====
        if milestone:
            badges.append({
                "badge_key": "milestone",
                "display": milestone.get("headline", "Milestone"),
                "icon": "Trophy",
                "color": "#eab308",
                "description": milestone.get("description"),
                "severity": milestone.get("severity", 7),
                "detail": milestone
            })
        
        # ===== 3. GASSED: Back-to-back (2nd night) =====
        if game_logs and len(game_logs) >= 2:
            from datetime import datetime, timedelta
            try:
                # Parse dates from game logs
                log1 = game_logs[0]
                log2 = game_logs[1]
                
                # Try different date formats from game logs
                date1_str = log1.get("game", {}).get("date") or log1.get("game_date")
                date2_str = log2.get("game", {}).get("date") or log2.get("game_date")
                
                if date1_str and date2_str:
                    # Parse date strings
                    for fmt in ["%Y-%m-%d", "%b %d, %Y"]:
                        try:
                            date1 = datetime.strptime(str(date1_str)[:10], fmt)
                            date2 = datetime.strptime(str(date2_str)[:10], fmt)
                            break
                        except:
                            continue
                    else:
                        date1, date2 = None, None
                    
                    if date1 and date2:
                        days_diff = abs((date1 - date2).days)
                        if days_diff == 1:
                            badges.append({
                                "badge_key": "gassed",
                                "display": "Gassed",
                                "icon": "BatteryLow",
                                "color": "#dc2626",
                                "description": f"2nd night of back-to-back games",
                                "severity": 6
                            })
            except Exception as e:
                logger.debug(f"[BADGE] Gassed check error: {e}")
        
        # Also check for heavy minutes (38+ in last game)
        if game_logs and len(game_logs) >= 1:
            try:
                last_game = game_logs[0]
                minutes = last_game.get("min") or last_game.get("minutes", "0")
                # Parse minutes - could be "38:20" or just 38
                if isinstance(minutes, str) and ":" in minutes:
                    minutes = int(minutes.split(":")[0])
                else:
                    minutes = int(float(minutes))
                
                if minutes >= 38:
                    # Check if gassed badge already exists
                    has_gassed = any(b.get("badge_key") == "gassed" for b in badges)
                    if not has_gassed:
                        badges.append({
                            "badge_key": "gassed",
                            "display": "Gassed",
                            "icon": "BatteryLow",
                            "color": "#dc2626",
                            "description": f"Played {minutes} min in last game (fatigue risk)",
                            "severity": 5
                        })
            except Exception as e:
                logger.debug(f"[BADGE] Heavy minutes check error: {e}")
        
        # ===== 4. HOME_COOKIN: Home PPG 15%+ higher than Away =====
        if game_logs and len(game_logs) >= 10:
            try:
                home_pts = []
                away_pts = []
                
                for log in game_logs[:20]:
                    pts = log.get("pts", 0) or 0
                    # Check is_home field or parse from game data
                    game_data = log.get("game", {})
                    team_data = log.get("team", {})
                    team_id = team_data.get("id")
                    home_team_id = game_data.get("home_team_id")
                    
                    # Determine if home game
                    matchup = log.get("matchup", "")
                    is_home = None
                    
                    if team_id and home_team_id:
                        is_home = team_id == home_team_id
                    elif "vs." in matchup:
                        is_home = True
                    elif "@" in matchup:
                        is_home = False
                    
                    if is_home is True:
                        home_pts.append(pts)
                    elif is_home is False:
                        away_pts.append(pts)
                
                if home_pts and away_pts:
                    home_avg = sum(home_pts) / len(home_pts)
                    away_avg = sum(away_pts) / len(away_pts)
                    
                    if away_avg > 0 and home_avg > away_avg * 1.15:
                        badges.append({
                            "badge_key": "home_cookin",
                            "display": "Home Cookin'",
                            "icon": "Home",
                            "color": "#22c55e",
                            "description": f"Home avg ({home_avg:.1f}) is {((home_avg/away_avg - 1) * 100):.0f}% higher than away ({away_avg:.1f})",
                            "severity": 7
                        })
            except Exception as e:
                logger.debug(f"[BADGE] Home cookin check error: {e}")
        
        # ===== Check context_engine for flag-based badges =====
        # Look up by player_id OR player_name for maximum coverage
        context_query = {"active": True, "$or": []}
        if player_nba_id:
            context_query["$or"].append({"player_id": player_nba_id})
        context_query["$or"].append({"player_name": {"$regex": f"^{player_name}$", "$options": "i"}})
        
        context_flags = []
        async for flag in context_engine.find(
            context_query,
            {"_id": 0, "flag_type": 1, "travel_miles": 1, "headline_reference": 1, "metadata": 1}
        ):
            context_flags.append(flag)
            
            for flag in context_flags:
                flag_type = flag.get("flag_type", "")
                
                # ===== 5. JET_LAG: Travel > 1000mi =====
                if flag_type == "travel":
                    travel_miles = flag.get("travel_miles", 0) or 0
                    if travel_miles >= 1000:
                        badges.append({
                            "badge_key": "jet_lag",
                            "display": "Jet Lag",
                            "icon": "Plane",
                            "color": "#a855f7",
                            "description": f"Road game + {travel_miles}mi travel",
                            "severity": 6
                        })
                
                # ===== 6. LEGAL_NOISE: Legal issues =====
                if "legal" in flag_type.lower() or flag_type == "legal_custody_battle":
                    headline = flag.get("headline_reference", "Active legal matter")
                    badges.append({
                        "badge_key": "legal_noise",
                        "display": "Legal Noise",
                        "icon": "Gavel",
                        "color": "#f97316",
                        "description": headline[:80] if headline else "Active legal/personal news",
                        "severity": 9
                    })
                
                # ===== 7. DISTRACTION: Trade rumors/drama =====
                if flag_type in ["distraction", "trade_rumors", "drama"]:
                    badges.append({
                        "badge_key": "distraction",
                        "display": "Distraction",
                        "icon": "AlertCircle",
                        "color": "#d97706",
                        "description": "Trade rumors or locker room drama",
                        "severity": 7
                    })
                
                # ===== 8. REVENGE: Playing former team =====
                if flag_type == "revenge":
                    metadata = flag.get("metadata", {})
                    opponent = metadata.get("opponent", "former team")
                    badges.append({
                        "badge_key": "revenge",
                        "display": "Revenge",
                        "icon": "Swords",
                        "color": "#ef4444",
                        "description": f"Playing against {opponent}",
                        "severity": 8
                    })
        
        # ===== 9 & 10: PAY_DAY, DEEP_WATER, and enhanced DISTRACTION =====
        
        # PAY_DAY: Contract year players (live Spotrac data with static fallback)
        pay_day = None
        try:
            # Try live Spotrac data first
            from services.spotrac_contract_service import get_contract_year_info
            pay_day = await get_contract_year_info(player_name, db)
            if pay_day:
                logger.debug(f"[BADGE] Pay day from Spotrac for {player_name}: {pay_day.get('type')}")
        except Exception as e:
            logger.debug(f"Spotrac pay day check failed for {player_name}: {e}")
        
        # Fallback to static data if Spotrac didn't find the player
        if not pay_day:
            try:
                from data.context_data import get_pay_day_info
                pay_day = get_pay_day_info(player_name)
                if pay_day:
                    pay_day["source"] = "static"
            except Exception as e:
                logger.debug(f"Static pay day check failed for {player_name}: {e}")
        
        if pay_day:
            badges.append({
                "badge_key": "pay_day",
                "display": "Pay Day",
                "icon": "DollarSign",
                "color": "#22c55e",
                "description": pay_day["description"],
                "severity": 7,
                "detail": pay_day
            })
        
        # DISTRACTION: Trade rumors or recent trade (enhanced)
        try:
            from data.context_data import get_distraction_info
            distraction = get_distraction_info(player_name)
            if distraction:
                # Check if distraction badge already added from context_engine
                has_distraction = any(b.get("badge_key") == "distraction" for b in badges)
                if not has_distraction:
                    if distraction["type"] == "trade_rumor":
                        badges.append({
                            "badge_key": "distraction",
                            "display": "Trade Rumors",
                            "icon": "AlertTriangle",
                            "color": "#f59e0b",
                            "description": distraction["reason"],
                            "severity": 8 if distraction["level"] == "high" else 6,
                            "detail": distraction
                        })
                    elif distraction["type"] == "recently_traded":
                        badges.append({
                            "badge_key": "distraction",
                            "display": "New Team",
                            "icon": "Repeat",
                            "color": "#3b82f6",
                            "description": distraction["reason"],
                            "severity": 5,
                            "detail": distraction
                        })
        except Exception as e:
            logger.debug(f"Distraction check failed for {player_name}: {e}")
        
        # ===== 10. DEEP_WATER: Injuries ONLY (BDL SSOT) =====
        # Only triggers from BDL injury data - no minutes-based logic
        bdl_injuries = db['bdl_injuries']
        injury = await bdl_injuries.find_one(
            {"player_name": {"$regex": f"^{player_name}$", "$options": "i"}},
            {"_id": 0}
        )
        
        if injury:
            severity = injury.get("severity", "unknown")
            if severity in ["out", "doubtful", "season_ending"]:
                badges.append({
                    "badge_key": "deep_water",
                    "display": "Deep Water",
                    "icon": "HeartPulse",
                    "color": "#dc2626",
                    "description": f"Injury: {injury.get('status')} - {injury.get('injury_type', 'See report')}",
                    "severity": 10
                })
            elif severity in ["questionable", "probable"]:
                badges.append({
                    "badge_key": "deep_water",
                    "display": "Injury Watch",
                    "icon": "HeartPulse",
                    "color": "#f59e0b",
                    "description": f"Status: {injury.get('status')}",
                    "severity": 6
                })
        
        # Remove duplicate badges (keep first occurrence)
        seen = set()
        unique_badges = []
        for badge in badges:
            if badge["badge_key"] not in seen:
                seen.add(badge["badge_key"])
                unique_badges.append(badge)
        
        badge_keys = [b["badge_key"] for b in unique_badges]
        return unique_badges, badge_keys
        
    except Exception as e:
        logger.error(f"[BADGE_RESOLVE] Error for {player_name}: {e}")
        return [], []


@router.get("/v3/player-with-badges/{player_name}")
async def get_cached_player(player_name: str):
    """
    Get cached data for a single player.
    No API calls - reads from MongoDB cached_board.
    
    Returns:
    - Player metadata
    - All props with lines
    - Hit rates
    - AI Vision summary
    - Demon/Goblin status
    - Context badges (10 situational indicators)
    """
    engine = get_engine()
    result = await engine.get_cached_player(player_name)
    
    if not result:
        raise HTTPException(status_code=404, detail=f"Player '{player_name}' not found in cache")
    
    # Add badge resolution for Vision Intel Suite
    if result.get("success") and result.get("player"):
        player = result["player"]
        pname = player.get("player_name")
        logger.info(f"[BADGE_RESOLVE] Processing badges for: {pname}")
        
        # Resolve all 10 context badges
        badges, badge_keys = await resolve_context_badges(engine, pname, player)
        logger.info(f"[BADGE_RESOLVE] Final badges for {pname}: {badge_keys}")
        
        # Add badges to player
        player["active_badges"] = badge_keys
        player["badges"] = badges
        
        # ========== FIX TEAM/OPPONENT FROM MASTER HUB ==========
        # The cached_board has incorrect team data - get correct team from master hub
        db = engine.db
        master_hub = db.nba_master_hub_2026
        
        # Look up correct team and photo from master hub
        hub_player = await master_hub.find_one(
            {"$or": [
                {"display_name": {"$regex": f"^{pname}$", "$options": "i"}},
                {"normalized_name": {"$regex": f"^{pname}$", "$options": "i"}}
            ]},
            {"_id": 0, "team": 1, "photo_url": 1, "position": 1, "nba_id": 1}
        )
        
        correct_team = hub_player.get("team") if hub_player else player.get("team", "Team")
        
        # Add photo from master hub - ALWAYS use photo_url (has correct NBA CDN ID)
        if hub_player:
            player["photo_url"] = hub_player.get("photo_url")
            player["nba_id"] = hub_player.get("nba_id")
            if not player.get("position"):
                player["position"] = hub_player.get("position")
        
        # Derive correct opponent from game info
        # Get home_team/away_team from raw cached_board documents
        game_id = player.get("game_id")
        raw_doc = await db.dg_cached_board.find_one(
            {"game_id": game_id, "home_team": {"$exists": True}},
            {"_id": 0, "home_team": 1, "away_team": 1}
        ) if game_id else None
        
        # Team name mapping for comparison
        TEAM_ABBREV_TO_FULL = {
            "ATL": "Atlanta Hawks", "BOS": "Boston Celtics", "BKN": "Brooklyn Nets",
            "CHA": "Charlotte Hornets", "CHI": "Chicago Bulls", "CLE": "Cleveland Cavaliers",
            "DAL": "Dallas Mavericks", "DEN": "Denver Nuggets", "DET": "Detroit Pistons",
            "GS": "Golden State Warriors", "GSW": "Golden State Warriors",
            "HOU": "Houston Rockets", "IND": "Indiana Pacers", "LAC": "Los Angeles Clippers",
            "LAL": "Los Angeles Lakers", "MEM": "Memphis Grizzlies", "MIA": "Miami Heat",
            "MIL": "Milwaukee Bucks", "MIN": "Minnesota Timberwolves", "NO": "New Orleans Pelicans",
            "NOP": "New Orleans Pelicans", "NY": "New York Knicks", "NYK": "New York Knicks",
            "OKC": "Oklahoma City Thunder", "ORL": "Orlando Magic", "PHI": "Philadelphia 76ers",
            "PHX": "Phoenix Suns", "POR": "Portland Trail Blazers", "SAC": "Sacramento Kings",
            "SA": "San Antonio Spurs", "SAS": "San Antonio Spurs", "TOR": "Toronto Raptors",
            "UTA": "Utah Jazz", "WAS": "Washington Wizards"
        }
        
        correct_opponent = None
        if raw_doc and correct_team:
            home_team = raw_doc.get("home_team")
            away_team = raw_doc.get("away_team")
            team_full = TEAM_ABBREV_TO_FULL.get(correct_team.upper(), correct_team)
            if team_full == home_team:
                correct_opponent = away_team
            elif team_full == away_team:
                correct_opponent = home_team
        
        if not correct_opponent:
            correct_opponent = player.get("opponent", "Opponent")
        
        # Update player with corrected team/opponent
        player["team"] = correct_team
        player["opponent"] = correct_opponent
        
        team = correct_team
        opponent = correct_opponent
        logger.info(f"[PLAYER_DETAIL] {pname}: team={team}, opponent={opponent}")
        
        # Get team abbreviations for DvP/Pace lookups
        from config.settings import TEAM_ABBREV_MAP
        team_abbr = TEAM_ABBREV_MAP.get(team, team[:3].upper() if team else "UNK")
        opp_abbr = TEAM_ABBREV_MAP.get(opponent, opponent[:3].upper() if opponent else "UNK")
        
        # ========== OFFICIATING IMPACT (WHISTLE MATRIX) ==========
        # Get referee data for this player's team
        from services.referee_scraper_service import get_referee_service
        ref_service = get_referee_service(db)
        ref_info = ref_service.get_ref_for_team(team_abbr) if team_abbr else None
        
        # Store officiating data at player level
        if ref_info:
            player["crew_chief"] = ref_info.get("crew_chief")
            player["ref_ou_pct"] = ref_info.get("ou_pct")
            player["ref_ppg"] = ref_info.get("ppg")
            player["whistle_class"] = ref_info.get("whistle_class", "neutral")
            logger.info(f"[PLAYER_DETAIL] {pname}: Ref={ref_info.get('crew_chief')}, Whistle={ref_info.get('whistle_class')}")
        
        # ========== IDENTIFY THE FEATURED PROP ==========
        # Only ONE prop per player gets the Vision Intel Suite (badges + summary)
        # Cross-reference with actual boards to find the REAL featured prop
        
        # FAST LOOKUP: Use cached board membership (refreshes every 60s)
        # This avoids re-computing all 3 boards for EACH player card click!
        featured_props = await get_board_membership(pname)
        
        logger.info(f"[PLAYER_DETAIL] {pname}: Featured props = {list(featured_props.keys())}")
        
        # ========== FETCH PRE-CACHED INTEL FROM dg_cached_board ==========
        # The Board Intelligence Service enriches props with full intel_suite
        # Fetch these pre-computed values instead of recalculating
        cached_player = await db.dg_cached_board.find_one(
            {"player_name": {"$regex": f"^{pname}$", "$options": "i"}},
            {"_id": 0, "props": 1}
        )
        
        # Build lookup map: {stat_type|line -> enriched_prop}
        # Also build a map by stat_type only (for line variance between sources)
        enriched_props_map = {}
        enriched_by_stat = {}
        if cached_player:
            for cp in cached_player.get("props", []):
                stat = cp.get("stat_type_extracted") or cp.get("stat_type", "")
                line = cp.get("line", 0)
                if cp.get("is_vision_enriched") and cp.get("intel_suite"):
                    key = f"{stat}|{line}"
                    enriched_props_map[key] = cp
                    # Also map by stat_type only (first enriched prop wins)
                    if stat not in enriched_by_stat:
                        enriched_by_stat[stat] = cp
            logger.info(f"[PLAYER_DETAIL] {pname}: Found {len(enriched_props_map)} pre-enriched props, stats={list(enriched_by_stat.keys())}")
        
        # Deduplicate props by stat_type + line + direction (keep both Over and Under)
        seen_props = set()
        unique_props = []
        for prop in player.get("props", []):
            stat_type = prop.get("stat_type_extracted", "PTS")
            line = prop.get("line", 0)
            direction = (prop.get("direction") or "over").lower()
            dedupe_key = f"{stat_type}|{line}|{direction}"
            if dedupe_key in seen_props:
                continue
            seen_props.add(dedupe_key)
            unique_props.append(prop)
        
        # Replace props with deduplicated list
        player["props"] = unique_props
        
        for prop in player.get("props", []):
            stat_type = prop.get("stat_type_extracted", "PTS")
            line = prop.get("line", 0)
            
            # ========== ADD OFFICIATING DATA TO PROP ==========
            if ref_info:
                whistle_class = ref_info.get("whistle_class", "neutral")
                ref_ppg = ref_info.get("ppg", 115.5)
                
                # Calculate whistle modifier
                whistle_modifier = ref_service.calculate_whistle_modifier(stat_type, whistle_class)
                
                # Calculate Point Lift translation
                point_lift_data = ref_service.calculate_point_lift(
                    stat_type=stat_type,
                    ref_ppg=ref_ppg,
                    whistle_class=whistle_class
                )
                
                # Add to prop
                prop["crew_chief"] = ref_info.get("crew_chief")
                prop["ref_ou_pct"] = ref_info.get("ou_pct")
                prop["ref_ppg"] = ref_ppg
                prop["whistle_class"] = whistle_class
                prop["has_whistle_modifier"] = whistle_modifier != 0
                prop["whistle_modifier"] = whistle_modifier
                prop["point_lift"] = point_lift_data.get("point_lift", 0)
                prop["lift_label"] = point_lift_data.get("lift_label", "")
                prop["lift_type"] = point_lift_data.get("lift_type", "neutral")
                prop["foul_rate_diff"] = point_lift_data.get("foul_rate_diff", 0)
            
            # Get hit rates - support BOTH nested and flat formats
            # FLAT format (from _flatten_hit_rates_to_props): l5_avg, l10_avg, h10_rate at prop level
            # NESTED format (legacy): hit_rates.l10.avg, hit_rates.l10.hit_rate
            hit_rates_obj = prop.get("hit_rates", {})
            
            # Try flat format first (prop-level), then nested (hit_rates.l10.*)
            l10_hit_rate = prop.get("h10_rate") or hit_rates_obj.get("l10_rate") or hit_rates_obj.get("l10", {}).get("hit_rate", 0) or 0
            l5_hit_rate = prop.get("h5_rate") or hit_rates_obj.get("l5_rate") or hit_rates_obj.get("l5", {}).get("hit_rate", 0) or 0
            l10_avg = prop.get("l10_avg") or hit_rates_obj.get("l10_avg") or hit_rates_obj.get("l10", {}).get("avg", 0)
            l5_avg = prop.get("l5_avg") or hit_rates_obj.get("l5_avg") or hit_rates_obj.get("l5", {}).get("avg", 0)
            season_avg = prop.get("season_avg") or hit_rates_obj.get("season_avg") or hit_rates_obj.get("season", {}).get("avg", 0)
            l10_games_over = hit_rates_obj.get("l10_hit_count") or hit_rates_obj.get("l10", {}).get("games_over", 0)
            l10_total_games = hit_rates_obj.get("l10", {}).get("total_games", 10)
            l5_games_over = hit_rates_obj.get("l5_hit_count") or hit_rates_obj.get("l5", {}).get("games_over", 0)
            
            # Normalize hit rates - ensure they're percentages (0-100)
            if l10_hit_rate and l10_hit_rate <= 1:
                l10_hit_rate = l10_hit_rate * 100
            if l5_hit_rate and l5_hit_rate <= 1:
                l5_hit_rate = l5_hit_rate * 100
            
            is_demon = prop.get("is_demon", False)
            is_goblin = prop.get("is_goblin", False)
            
            # Add stat_type for frontend compatibility
            prop["stat_type"] = stat_type
            
            # Add normalized hit rate fields for frontend
            prop["h10_rate"] = round(l10_hit_rate, 1) if l10_hit_rate else 0
            prop["h5_rate"] = round(l5_hit_rate, 1) if l5_hit_rate else 0
            prop["l5_avg"] = l5_avg
            prop["l10_avg"] = l10_avg
            prop["season_avg"] = season_avg
            
            # Check if THIS prop is a featured one (on any board)
            # Method 1: Check the prop's own board field (set during sync enrichment)
            prop_board = prop.get("board")
            # Method 2: Check the board membership cache (from live API)
            prop_key = f"{stat_type}|{line}"
            cache_board = featured_props.get(prop_key, None)
            # Prop is featured if it has a board assigned from EITHER source
            is_featured = bool(prop_board and prop_board != "NONE") or bool(cache_board)
            featured_board = prop_board or cache_board
            
            # Build hit_rates object for frontend compatibility (use actual values from data)
            prop["hit_rates"] = {
                "l10": {
                    "hit_rate": l10_hit_rate,
                    "games_over": l10_games_over,
                    "total_games": l10_total_games,
                    "avg": l10_avg
                },
                "l5": {
                    "hit_rate": l5_hit_rate,
                    "games_over": l5_games_over,
                    "total_games": 5,
                    "avg": l5_avg
                },
                "season": {
                    "avg": season_avg
                }
            }
            
            # ========== VISION INTEL SUITE - ONLY FOR FEATURED PROP ==========
            # Only the prop that qualified for the boards gets the full intel suite
            if not is_featured:
                # Regular prop - skip intel suite and badges, but PRESERVE vision_summary
                # The summary was generated during background sync and should remain
                prop.pop("intel_suite", None)
                prop.pop("active_badges", None)
                # NOTE: We keep vision_summary since it was pre-cached during sync
                continue
            
            # This IS the featured prop - add badges and intel suite
            prop["active_badges"] = badge_keys
            
            # ========== CHECK FOR GEMINI VISION INTEL FROM FERRARI TIERS ==========
            # The Vision Intel (Gemini) data is stored in ferrari_safe_haven/front_lines/war_zone
            # Query by stat_type only (lines change daily but analysis is for the stat)
            ferrari_prop = None
            for tier_collection in ['ferrari_safe_haven', 'ferrari_front_lines', 'ferrari_war_zone']:
                try:
                    ferrari_prop = await db[tier_collection].find_one(
                        {
                            "player_name": {"$regex": f"^{pname}$", "$options": "i"},
                            "stat_type": stat_type
                        },
                        {"_id": 0, "vision_intel": 1, "intel_verdict": 1, "intel_score": 1, 
                         "intel_risk": 1, "adjusted_confidence": 1, "composite_score": 1}
                    )
                    if ferrari_prop and ferrari_prop.get("vision_intel"):
                        break
                except Exception as e:
                    logger.error(f"[PLAYER_DETAIL] Error querying {tier_collection}: {e}")
            
            # Add Gemini data to prop if found
            if ferrari_prop:
                prop["vision_intel"] = ferrari_prop.get("vision_intel")
                prop["intel_verdict"] = ferrari_prop.get("intel_verdict")
                prop["intel_score"] = ferrari_prop.get("intel_score")
                prop["intel_risk"] = ferrari_prop.get("intel_risk")
                prop["adjusted_confidence"] = ferrari_prop.get("adjusted_confidence")
                prop["composite_score"] = ferrari_prop.get("composite_score")
                logger.info(f"[PLAYER_DETAIL] {pname} {stat_type}: Loaded Gemini intel - {ferrari_prop.get('intel_verdict')}")
            
            # ========== CHECK FOR PRE-CACHED INTEL SUITE ==========
            # If this prop was enriched by the Board Intelligence Service, USE THAT DATA
            # This ensures the same full intel suite is displayed regardless of how the player was accessed
            enriched_key = f"{stat_type}|{line}"
            enriched_prop = enriched_props_map.get(enriched_key)
            
            # If exact match not found, try by stat_type only (lines may differ between sources)
            if not enriched_prop:
                enriched_prop = enriched_by_stat.get(stat_type)
            
            if enriched_prop and enriched_prop.get("intel_suite"):
                # MERGE PRE-CACHED INTEL with calculated intel (from picks_getter_service)
                # Pre-cached has: momentum_data, whistle_data, vacuum_data, board, ferrari_power_score
                # Calculated has: vision_insight, matchup_dvp, pace_delta, stability_index, etc.
                pre_cached_intel = enriched_prop.get("intel_suite", {})
                calculated_intel = prop.get("intel_suite") or {}
                
                # Start with calculated intel (has vision_insight, matchup_dvp, etc.)
                merged_intel = {**calculated_intel}
                
                # MERGE CONTEXT BADGES: Combine pre-cached (Ferrari) badges with player badges
                # Ferrari badges: blowout_risk, trap_risk, soft_matchup, trend_alert, etc.
                # Player badges: pay_day, locked_in, gassed, home_cookin, deep_water, etc.
                ferrari_badges = pre_cached_intel.get("context_badges", [])
                merged_badges = list(set(ferrari_badges + badge_keys))
                merged_intel["context_badges"] = merged_badges
                prop["active_badges"] = merged_badges
                
                # Add pre-cached fields (momentum, whistle, vacuum, board, ferrari_score)
                if pre_cached_intel.get("momentum_data"):
                    merged_intel["momentum_data"] = pre_cached_intel["momentum_data"]
                if pre_cached_intel.get("whistle_data"):
                    merged_intel["whistle_data"] = pre_cached_intel["whistle_data"]
                if pre_cached_intel.get("vacuum_data"):
                    merged_intel["vacuum_data"] = pre_cached_intel["vacuum_data"]
                if pre_cached_intel.get("board"):
                    merged_intel["board"] = pre_cached_intel["board"]
                if pre_cached_intel.get("ferrari_power_score"):
                    merged_intel["ferrari_power_score"] = pre_cached_intel["ferrari_power_score"]
                
                prop["intel_suite"] = merged_intel
                
                # Add Gemini Vision Intel to intel_suite (from Ferrari tier lookup above)
                if ferrari_prop:
                    prop["intel_suite"]["gemini_intel"] = {
                        "vision_intel": ferrari_prop.get("vision_intel"),
                        "intel_verdict": ferrari_prop.get("intel_verdict"),
                        "intel_score": ferrari_prop.get("intel_score"),
                        "intel_risk": ferrari_prop.get("intel_risk"),
                        "adjusted_confidence": ferrari_prop.get("adjusted_confidence"),
                        "composite_score": ferrari_prop.get("composite_score")
                    }
                
                prop["vision_summary"] = enriched_prop.get("vision_summary")
                prop["vision_score"] = enriched_prop.get("vision_score")
                
                # Gemini Vision Intel fields
                prop["vision_intel"] = enriched_prop.get("vision_intel")
                prop["intel_verdict"] = enriched_prop.get("intel_verdict")
                prop["intel_score"] = enriched_prop.get("intel_score")
                prop["intel_risk"] = enriched_prop.get("intel_risk")
                prop["adjusted_confidence"] = enriched_prop.get("adjusted_confidence")
                prop["composite_score"] = enriched_prop.get("composite_score")
                prop["is_vision_enriched"] = True
                prop["board"] = enriched_prop.get("board")
                
                # Copy momentum data if available from enriched prop
                if enriched_prop.get("momentum_data"):
                    prop["momentum_data"] = enriched_prop["momentum_data"]
                    prop["momentum_modifier"] = enriched_prop.get("momentum_modifier", 0)
                    prop["has_momentum_modifier"] = enriched_prop.get("has_momentum_modifier", False)
                else:
                    # Fetch momentum data if not in enriched prop
                    try:
                        from services.defensive_momentum_service import get_momentum_service
                        momentum_service = get_momentum_service(db)
                        await momentum_service.ensure_cache()
                        modifier, momentum_data = momentum_service.calculate_momentum_modifier(opp_abbr, stat_type)
                        if momentum_data:
                            prop["momentum_data"] = momentum_data
                            prop["momentum_modifier"] = modifier
                            prop["has_momentum_modifier"] = modifier != 0
                    except Exception as e:
                        logger.warning(f"[PLAYER_DETAIL] Momentum data for enriched prop failed: {e}")
                
                # Copy officiating data if available
                for key in ["crew_chief", "ref_ou_pct", "ref_ppg", "whistle_class", "whistle_modifier", 
                            "has_whistle_modifier", "lift_label", "lift_type", "point_lift", "foul_rate_diff"]:
                    if key in enriched_prop:
                        prop[key] = enriched_prop[key]
                
                logger.debug(f"[PLAYER_DETAIL] Using pre-cached intel_suite for {pname} {stat_type}@{line} (from enriched {enriched_prop.get('stat_type_extracted')}@{enriched_prop.get('line')})")
                continue
            
            # ========== FALLBACK: CALCULATE INTEL SUITE ON-THE-FLY ==========
            # If no pre-cached intel exists, calculate it (legacy behavior)
            
            # Calculate stability index from hit rate
            # Note: l10_hit_rate is already a percentage (0-100), not a decimal
            stability_score = int(l10_hit_rate or 50) if l10_hit_rate is not None else 50
            if stability_score >= 70:
                consistency = "HIGHLY CONSISTENT"
            elif stability_score >= 50:
                consistency = "MODERATE VARIANCE"
            else:
                consistency = "HIGH VARIANCE"
            
            # ========== REAL DvP DATA ==========
            # Get actual defensive rank for opponent vs this stat type
            dvp_rank = get_dvp_rank(opp_abbr, stat_type)
            dvp_color = get_dvp_rank_color(dvp_rank)
            dvp_modifier = calculate_dvp_modifier(opp_abbr, stat_type)
            
            # Determine friction level based on defensive rank
            # Rank 1-10 = Top defense = High Friction (bad for player)
            # Rank 11-20 = Average defense = Medium Friction
            # Rank 21-30 = Poor defense = Low Friction (good for player)
            if dvp_rank >= 25:
                friction_level = "Low"
                friction_color = "green"
                friction_label = f"{opp_abbr} ranks #{dvp_rank} in {stat_type} defense (Bottom 6 - favorable)"
            elif dvp_rank >= 15:
                friction_level = "Medium"
                friction_color = "yellow"
                friction_label = f"{opp_abbr} ranks #{dvp_rank} in {stat_type} defense (Average)"
            elif dvp_rank >= 6:
                friction_level = "High"
                friction_color = "yellow"
                friction_label = f"{opp_abbr} ranks #{dvp_rank} in {stat_type} defense (Above average)"
            else:
                friction_level = "Elite"
                friction_color = "red"
                friction_label = f"{opp_abbr} ranks #{dvp_rank} in {stat_type} defense (Top 5 - tough)"
            
            # ========== REAL PACE DATA ==========
            team_pace = TEAM_PACE.get(team_abbr, LEAGUE_AVG_PACE)
            opp_pace = TEAM_PACE.get(opp_abbr, LEAGUE_AVG_PACE)
            
            # Expected game pace is average of both teams
            expected_pace = round((team_pace + opp_pace) / 2, 1)
            pace_delta = round(expected_pace - LEAGUE_AVG_PACE, 1)
            
            # Determine tempo label
            if pace_delta >= 3:
                tempo_label = "Fast-paced game expected"
                pace_display = f"+{pace_delta:.0f} POSS"
            elif pace_delta >= 1:
                tempo_label = "Slightly above average tempo"
                pace_display = f"+{pace_delta:.0f} POSS"
            elif pace_delta <= -3:
                tempo_label = "Slow-paced game expected"
                pace_display = f"{pace_delta:.0f} POSS"
            elif pace_delta <= -1:
                tempo_label = "Slightly below average tempo"
                pace_display = f"{pace_delta:.0f} POSS"
            else:
                tempo_label = "Neutral tempo game"
                pace_display = "0 POSS"
            
            # Determine if player is demon/goblin for usage display
            is_demon = prop.get("is_demon", False)
            is_goblin = prop.get("is_goblin", False)
            
            # Build vision insight based on stats
            reasons = []
            if l5_avg and line and l5_avg >= line:
                reasons.append(f"L5 avg ({l5_avg}) already exceeds target line ({line})")
            if l10_hit_rate and l10_hit_rate >= 60:
                # l10_hit_rate is a percentage (0-100), convert to X/10 format
                hits_out_of_10 = int(l10_hit_rate / 10)
                reasons.append(f"Hit this line in {hits_out_of_10}/10 recent games")
            if season_avg and line and line < season_avg:
                reasons.append(f"Line set below season average ({season_avg})")
            
            # Add DvP-based insight
            if dvp_rank >= 25:
                reasons.append(f"Favorable matchup: {opp_abbr} is #{dvp_rank} vs {stat_type}")
            elif dvp_rank <= 5:
                reasons.append(f"Tough matchup: {opp_abbr} is #{dvp_rank} vs {stat_type}")
            
            # Add pace-based insight for scoring stats
            if stat_type in ["PTS", "PRA", "PA", "PR", "AST"] and pace_delta >= 2:
                reasons.append(f"High-pace game (+{pace_delta:.0f} possessions) boosts {stat_type}")
            
            # Add badge-based reasons
            for badge in badges:
                if badge["badge_key"] == "locked_in":
                    reasons.append(f"Player on hot streak: {badge.get('description', '')}")
                elif badge["badge_key"] == "home_cookin":
                    reasons.append(f"Strong home performer: {badge.get('description', '')}")
                elif badge["badge_key"] == "revenge":
                    reasons.append(f"Revenge game motivation: {badge.get('description', '')}")
            
            primary_insight = reasons[0] if reasons else f"Analyzing {pname} for {stat_type} @ {line}"
            
            prop["intel_suite"] = {
                "context_badges": badge_keys,
                
                # Usage Ripple / Operational Volume
                "usage_ripple": {
                    "display": "Elevated Usage" if is_demon else "Standard Volume",
                    "reasoning": f"Based on team role and recent minutes",
                    "bump_percent": 3 if is_demon else 1,
                    "shift_label": "+3% Usage" if is_demon else "Normal",
                    "injuries_affecting": []
                },
                
                # Matchup DvP / Defensive Friction (REAL DATA)
                "matchup_dvp": {
                    "display": f"vs {opp_abbr}",
                    "opponent": opponent,
                    "opponent_abbr": opp_abbr,
                    "friction_level": friction_level,
                    "friction_label": friction_label,
                    "color": friction_color,
                    "dvp_rank": dvp_rank,
                    "dvp_modifier": round(dvp_modifier, 2),
                    "stat_type": stat_type
                },
                
                # Pace Delta / Tempo Multiplier (REAL DATA)
                "pace_delta": {
                    "display": pace_display,
                    "possessions": pace_delta,
                    "tempo_label": tempo_label,
                    "expected_game_pace": f"{expected_pace:.1f}",
                    "team_pace": team_pace,
                    "opp_pace": opp_pace,
                    "league_avg": LEAGUE_AVG_PACE
                },
                
                # Stability Index / Tactical Variance
                "stability_index": {
                    "display": f"{stability_score}%",
                    "score": stability_score,
                    "consistency": consistency,
                    "std_dev": None
                },
                
                # Vision Insight / Target-Lock Rationale
                "vision_insight": {
                    "primary": primary_insight,
                    "reasons": reasons if len(reasons) > 1 else [primary_insight],
                    "confidence": "HIGH" if len(reasons) >= 3 else "MEDIUM" if len(reasons) >= 2 else "STANDARD"
                }
            }
            
            # Add Gemini Vision Intel to intel_suite (from Ferrari tier lookup)
            if ferrari_prop:
                prop["intel_suite"]["gemini_intel"] = {
                    "vision_intel": ferrari_prop.get("vision_intel"),
                    "intel_verdict": ferrari_prop.get("intel_verdict"),
                    "intel_score": ferrari_prop.get("intel_score"),
                    "intel_risk": ferrari_prop.get("intel_risk"),
                    "adjusted_confidence": ferrari_prop.get("adjusted_confidence"),
                    "composite_score": ferrari_prop.get("composite_score")
                }
            
            # Calculate blowout risk and add to intel_suite
            try:
                from services.standings_service import StandingsService
                blowout_data = await StandingsService.calculate_blowout_risk(team_abbr, opp_abbr)
                prop["intel_suite"]["blowout_risk"] = blowout_data
                
                # Add warning to reasons if blowout risk is HIGH or MEDIUM
                if blowout_data.get("warning"):
                    reasons.append(blowout_data["warning"])
                    prop["intel_suite"]["vision_insight"]["reasons"] = reasons
            except Exception as e:
                logger.warning(f"[PLAYER_DETAIL] Blowout risk calculation failed: {e}")
            
            # Add momentum data from Defensive Momentum Service
            try:
                from services.defensive_momentum_service import get_momentum_service
                momentum_service = get_momentum_service(db)
                await momentum_service.ensure_cache()
                modifier, momentum_data = momentum_service.calculate_momentum_modifier(opp_abbr, stat_type)
                if momentum_data:
                    prop["momentum_data"] = momentum_data
                    prop["momentum_modifier"] = modifier
                    prop["has_momentum_modifier"] = modifier != 0
            except Exception as e:
                logger.warning(f"[PLAYER_DETAIL] Momentum data calculation failed: {e}")
            
            # ========== STATIC VISION AI SUMMARY (from pre-cache) ==========
            # Vision AI summaries are PRE-COMPUTED by the Board-Driven Vision Intel Service
            # This endpoint serves ONLY from cache - NO JIT Gemini calls!
            #
            # If intel isn't ready, return "loading" state instead of blocking.
            
            # Check for pre-cached vision_summary in the prop
            pre_cached_summary = prop.get("vision_summary")
            pre_cached_intel = prop.get("intel_suite", {})
            is_stale = prop.get("is_stale", False)
            
            if pre_cached_summary and not is_stale:
                # Use pre-cached AI summary
                prop["intel_suite"]["vision_insight"]["ai_summary"] = pre_cached_summary
                prop["intel_suite"]["vision_insight"]["source"] = "pre_cached"
                prop["intel_suite"]["vision_insight"]["status"] = "ready"
                logger.debug(f"[VISION] Served pre-cached summary for {pname} {stat_type}@{line}")
            elif pre_cached_intel.get("vision_insight", {}).get("ai_summary") and not is_stale:
                # Summary was cached in intel_suite
                prop["vision_summary"] = pre_cached_intel["vision_insight"]["ai_summary"]
                prop["intel_suite"]["vision_insight"]["source"] = "pre_cached"
                prop["intel_suite"]["vision_insight"]["status"] = "ready"
                logger.debug(f"[VISION] Served pre-cached intel_suite summary for {pname} {stat_type}@{line}")
            elif is_stale:
                # Intel is stale (player rotated off boards)
                prop["intel_suite"]["vision_insight"]["ai_summary"] = None
                prop["intel_suite"]["vision_insight"]["source"] = "stale"
                prop["intel_suite"]["vision_insight"]["status"] = "stale"
                logger.debug(f"[VISION] Stale intel for {pname} {stat_type}@{line}")
            else:
                # No pre-cached summary - return "loading" state (don't block on JIT call)
                # Frontend should show loading indicator, intel will be ready on next sync
                prop["intel_suite"]["vision_insight"]["ai_summary"] = None
                prop["intel_suite"]["vision_insight"]["source"] = "loading"
                prop["intel_suite"]["vision_insight"]["status"] = "loading"
                prop["intel_suite"]["vision_insight"]["message"] = "Generating insights..."
                logger.debug(f"[VISION] Intel loading for {pname} {stat_type}@{line}")
        
        # Add advanced stats to player object (for Vision Intel Suite header)
        master_hub = engine.picks_getter_service.master_hub
        hub_player = await master_hub.find_one(
            {"display_name": {"$regex": f"^{pname}$", "$options": "i"}},
            {"_id": 0, "advanced_stats": 1, "bdl_game_logs": 1, "baseline_stats": 1}
        )
        if hub_player:
            # Add advanced stats
            if hub_player.get("advanced_stats"):
                adv = hub_player["advanced_stats"]
                player["advanced_stats"] = {
                    "pie": adv.get("pie"),  # Player Impact Estimate
                    "net_rating": adv.get("net_rating"),  # Net Rating
                    "games_counted": adv.get("games_counted")
                }
            
            # Add game logs for Vision Intel display (filter DNPs, limit to recent 20)
            game_logs = hub_player.get("bdl_game_logs", [])
            if game_logs:
                # Filter out DNP games (0 minutes)
                played_games = []
                for g in game_logs:
                    mins = g.get("min", "0") or "0"
                    if isinstance(mins, str):
                        mins_val = int(mins.split(":")[0]) if ":" in mins else (int(mins) if mins.isdigit() else 0)
                    else:
                        mins_val = int(mins) if mins else 0
                    if mins_val > 0:
                        played_games.append(g)
                
                player["game_logs"] = played_games[:20]  # Most recent 20 played games
                player["game_logs_count"] = len(played_games)
            
            # Add aggregated hit_rates for primary prop (first prop)
            baseline = hub_player.get("baseline_stats", {})
            props = player.get("props", [])
            if props and baseline:
                first_prop = props[0]
                player["hit_rates"] = first_prop.get("hit_rates", {})
                player["baseline_stats"] = baseline
    
    return result

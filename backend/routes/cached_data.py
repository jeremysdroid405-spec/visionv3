"""
Cached Data Routes
==================
Endpoints for reading cached/warehouse data with zero API calls.
"""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional
import logging
import sys

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Cached Data"])

# Reference to DemonGoblinEngine (set via dependency injection)
_demon_goblin_engine = None


def set_cached_data_engine(engine):
    """Set the demon goblin engine reference."""
    global _demon_goblin_engine
    _demon_goblin_engine = engine


def get_engine():
    """Get the demon goblin engine instance."""
    if _demon_goblin_engine is None:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    return _demon_goblin_engine


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
        
        # Check stats-based badges using master hub data
        badges = []
        badge_keys = []
        
        try:
            # Get master hub collection from the picks_getter_service
            master_hub = engine.picks_getter_service.master_hub
            
            # Get master hub data
            hub_player = await master_hub.find_one(
                {"$or": [
                    {"display_name": pname},
                    {"display_name": {"$regex": f"^{pname}$", "$options": "i"}}
                ]},
                {"_id": 0, "baseline_stats": 1, "bdl_game_logs": 1}
            )
            
            logger.info(f"[BADGE_RESOLVE] Hub player found: {hub_player is not None}")
            
            if hub_player:
                baseline_stats = hub_player.get("baseline_stats", {})
                game_logs = hub_player.get("bdl_game_logs", []) or []
                
                # Check LOCKED_IN: L5 PPG > Season PPG + 5
                pts_stats = baseline_stats.get("PTS", {})
                season_ppg = pts_stats.get("season_avg", 0) if isinstance(pts_stats, dict) else pts_stats
                
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
                            "severity": 8,
                            "source_flag": "stats_based"
                        })
                
                # Check MILESTONE: If any stat is within 5% of a round number milestone
                for stat_key, stat_data in baseline_stats.items():
                    if isinstance(stat_data, dict):
                        season_avg = stat_data.get("season_avg", 0)
                        if season_avg and season_avg >= 20:
                            nearest_milestone = round(season_avg / 5) * 5
                            if nearest_milestone > 0 and abs(season_avg - nearest_milestone) / nearest_milestone < 0.05:
                                if "milestone" not in [b["badge_key"] for b in badges]:
                                    badges.append({
                                        "badge_key": "milestone",
                                        "display": "Milestone",
                                        "icon": "Trophy",
                                        "color": "#eab308",
                                        "description": f"Averaging {season_avg:.1f} {stat_key} (near {nearest_milestone})",
                                        "severity": 7,
                                        "source_flag": "stats_based"
                                    })
                                break
            
            badge_keys = [b.get("badge_key") for b in badges]
            logger.info(f"[BADGE_RESOLVE] Final badges for {pname}: {badge_keys}")
            
        except Exception as e:
            logger.debug(f"[CACHED_PLAYER] Badge check error: {e}")
        
        # Add badges to player
        player["active_badges"] = badge_keys
        player["badges"] = badges
        
        # Also add to each prop for Vision Intel Suite
        for prop in player.get("props", []):
            prop["active_badges"] = badge_keys
            prop["intel_suite"] = {"context_badges": badge_keys}
    
    return result

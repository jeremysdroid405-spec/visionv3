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
             "nba_player_id": 1, "nba_id": 1, "player_id": 1}
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
        
        # ===== 2. MILESTONE: Stat avg within 5% of round milestone =====
        for stat_key, stat_data in baseline_stats.items():
            if isinstance(stat_data, dict):
                season_avg = stat_data.get("season_avg", 0)
                if season_avg and season_avg >= 20:
                    nearest_milestone = round(season_avg / 5) * 5
                    if nearest_milestone > 0 and abs(season_avg - nearest_milestone) / nearest_milestone < 0.05:
                        badges.append({
                            "badge_key": "milestone",
                            "display": "Milestone",
                            "icon": "Trophy",
                            "color": "#eab308",
                            "description": f"Averaging {season_avg:.1f} {stat_key} (near {nearest_milestone})",
                            "severity": 7
                        })
                        break
        
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
        if player_nba_id:
            context_flags = []
            async for flag in context_engine.find(
                {"player_id": player_nba_id, "active": True},
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
        
        # ===== 9 & 10: PAY_DAY and DEEP_WATER =====
        # These require contract data and playoff context which we don't currently have
        # Mark as placeholders for future implementation
        
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
        
        # Also add to each prop for Vision Intel Suite
        for prop in player.get("props", []):
            prop["active_badges"] = badge_keys
            prop["intel_suite"] = {"context_badges": badge_keys}
    
    return result

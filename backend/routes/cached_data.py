"""
Cached Data Routes
==================
Endpoints for reading cached/warehouse data with zero API calls.
"""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional
import logging
import sys

# Import DvP service and config for real matchup data
from services.dvp_service import (
    get_dvp_rank, 
    get_dvp_rank_color, 
    get_dvp_label,
    calculate_dvp_modifier
)
from config.settings import TEAM_PACE, LEAGUE_AVG_PACE, DVP_RANKINGS

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
        # Tracks: All-time rankings, closing in on passing players, round number milestones
        try:
            from data.career_milestones import get_best_milestone
            milestone = get_best_milestone(player_name)
            if milestone:
                badges.append({
                    "badge_key": "milestone",
                    "display": milestone.get("headline", "Milestone"),
                    "icon": "Trophy",
                    "color": "#eab308" if milestone.get("type") != "record_holder" else "#f59e0b",
                    "description": milestone.get("description"),
                    "severity": milestone.get("severity", 7),
                    "detail": milestone
                })
        except Exception as e:
            logger.debug(f"Milestone check failed for {player_name}: {e}")
        
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
        
        # PAY_DAY: Contract year players
        try:
            from data.context_data import get_pay_day_info
            pay_day = get_pay_day_info(player_name)
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
        except Exception as e:
            logger.debug(f"Pay day check failed for {player_name}: {e}")
        
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
        
        # DEEP_WATER: Check injuries AND low minutes trend
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
        
        # Also check context_engine for deep_water flag (set by injury sync)
        deep_water_flag = await context_engine.find_one(
            {"player_name": {"$regex": f"^{player_name}$", "$options": "i"}, "deep_water": True},
            {"_id": 0}
        )
        if deep_water_flag and "deep_water" not in [b["badge_key"] for b in badges]:
            badges.append({
                "badge_key": "deep_water",
                "display": "Deep Water",
                "icon": "HeartPulse",
                "color": "#dc2626",
                "description": deep_water_flag.get("deep_water_reason", "Health/injury concern"),
                "severity": 10
            })
        
        # DEEP_WATER: Also check for declining minutes (losing rotation spot)
        if game_logs and len(game_logs) >= 5:
            has_deep_water = "deep_water" in [b["badge_key"] for b in badges]
            if not has_deep_water:
                try:
                    # Compare L5 minutes to season average minutes
                    recent_mins = []
                    for log in game_logs[:5]:
                        mins = log.get("min", "0") or "0"
                        if isinstance(mins, str) and ":" in mins:
                            mins = int(mins.split(":")[0])
                        else:
                            try:
                                mins = int(float(mins))
                            except:
                                mins = 0
                        if mins > 0:  # Only count games they played
                            recent_mins.append(mins)
                    
                    if len(recent_mins) >= 3:
                        avg_recent = sum(recent_mins) / len(recent_mins)
                        
                        # Get season minutes from baseline_stats if available
                        season_mins = baseline_stats.get("MIN", {}).get("season_avg") if baseline_stats else None
                        
                        # If L5 minutes < 20 AND season avg > 25, player is losing minutes
                        if avg_recent < 20 and (season_mins is None or season_mins > 25):
                            badges.append({
                                "badge_key": "deep_water",
                                "display": "Deep Water",
                                "icon": "TrendingDown",
                                "color": "#dc2626",
                                "description": f"Minutes trending down (L5: {avg_recent:.0f} MPG)",
                                "severity": 7
                            })
                except Exception as e:
                    logger.debug(f"[BADGE] Deep water minutes check error: {e}")
        
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
        
        # Look up correct team from master hub
        hub_player = await master_hub.find_one(
            {"$or": [
                {"display_name": {"$regex": f"^{pname}$", "$options": "i"}},
                {"normalized_name": {"$regex": f"^{pname}$", "$options": "i"}}
            ]},
            {"_id": 0, "team": 1}
        )
        
        correct_team = hub_player.get("team") if hub_player else player.get("team", "Team")
        
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
        
        for prop in player.get("props", []):
            prop["active_badges"] = badge_keys
            
            # Build full intel_suite with all expected fields
            stat_type = prop.get("stat_type_extracted", "PTS")
            line = prop.get("line", 0)
            l5_avg = prop.get("l5_avg", 0)
            l10_avg = prop.get("l10_avg", 0)
            season_avg = prop.get("season_avg", 0)
            l10_hit_rate = prop.get("l10_hit_rate", 0)
            l5_hit_rate = prop.get("l5_hit_rate", 0)
            
            # Build hit_rates object for frontend compatibility
            # Frontend expects: hit_rates.l10.hit_rate, hit_rates.l10.games_over, hit_rates.l10.total_games
            l10_games_over = int((l10_hit_rate or 0) * 10)  # Approximate from hit rate
            l5_games_over = int((l5_hit_rate or 0) * 5)
            
            prop["hit_rates"] = {
                "l10": {
                    "hit_rate": l10_hit_rate,
                    "games_over": l10_games_over,
                    "total_games": 10,
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
            
            # Calculate stability index from hit rate
            stability_score = int((l10_hit_rate or 0) * 100) if l10_hit_rate else 50
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
            if l10_hit_rate and l10_hit_rate >= 0.6:
                reasons.append(f"Hit this line in {int(l10_hit_rate * 10)}/10 recent games")
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

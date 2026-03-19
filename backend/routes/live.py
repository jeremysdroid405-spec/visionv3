"""
Live Data Routes
================
Real-time NBA scores and breaking news endpoints.

SSOT ARCHITECTURE: Scores/news come from cached data in MongoDB.
Data is refreshed daily at 4 AM EST via scheduler.

Endpoints:
- GET /api/live/scores - Today's NBA games from DB cache
- GET /api/live/news - Breaking news from DB cache
- POST /api/live/sync-ticker - Manual sync trigger (admin only)
"""
import os
import httpx
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/live", tags=["Live Data"])

# NBA Team name to abbreviation mapping
TEAM_ABBREV_MAP = {
    "Atlanta Hawks": "ATL",
    "Boston Celtics": "BOS",
    "Brooklyn Nets": "BKN",
    "Charlotte Hornets": "CHA",
    "Chicago Bulls": "CHI",
    "Cleveland Cavaliers": "CLE",
    "Dallas Mavericks": "DAL",
    "Denver Nuggets": "DEN",
    "Detroit Pistons": "DET",
    "Golden State Warriors": "GSW",
    "Houston Rockets": "HOU",
    "Indiana Pacers": "IND",
    "Los Angeles Clippers": "LAC",
    "Los Angeles Lakers": "LAL",
    "LA Clippers": "LAC",
    "LA Lakers": "LAL",
    "Memphis Grizzlies": "MEM",
    "Miami Heat": "MIA",
    "Milwaukee Bucks": "MIL",
    "Minnesota Timberwolves": "MIN",
    "New Orleans Pelicans": "NOP",
    "New York Knicks": "NYK",
    "Oklahoma City Thunder": "OKC",
    "Orlando Magic": "ORL",
    "Philadelphia 76ers": "PHI",
    "Phoenix Suns": "PHX",
    "Portland Trail Blazers": "POR",
    "Sacramento Kings": "SAC",
    "San Antonio Spurs": "SAS",
    "Toronto Raptors": "TOR",
    "Utah Jazz": "UTA",
    "Washington Wizards": "WAS"
}

def get_team_abbrev(team_name: str) -> str:
    """Get proper NBA team abbreviation from full name."""
    if not team_name:
        return "???"
    # Direct lookup
    if team_name in TEAM_ABBREV_MAP:
        return TEAM_ABBREV_MAP[team_name]
    # Try partial match
    for full_name, abbrev in TEAM_ABBREV_MAP.items():
        if team_name in full_name or full_name in team_name:
            return abbrev
    # Fallback to first 3 letters
    return team_name[:3].upper()

# Database reference
_db = None

def set_db(db):
    global _db
    _db = db


async def sync_todays_games():
    """
    Sync today's NBA games to the database.
    Called at 4 AM daily by the scheduler.
    
    Fetches from NBA API and stores in ticker_games collection.
    """
    logger.info("[TICKER] Starting daily games sync...")
    
    try:
        from nba_api.live.nba.endpoints import scoreboard
        
        # Get live scoreboard from NBA API
        board = scoreboard.ScoreBoard()
        data = board.get_dict()
        
        games = []
        scoreboard_data = data.get("scoreboard", {})
        game_date = scoreboard_data.get("gameDate", datetime.now().strftime("%Y-%m-%d"))
        
        for game in scoreboard_data.get("games", []):
            home = game.get("homeTeam", {})
            away = game.get("awayTeam", {})
            
            # Parse game time for display
            game_time_utc = game.get("gameTimeUTC", "")
            start_time_display = ""
            if game_time_utc:
                try:
                    # Convert to EST for display
                    utc_time = datetime.fromisoformat(game_time_utc.replace("Z", "+00:00"))
                    est_time = utc_time - timedelta(hours=5)
                    start_time_display = est_time.strftime("%-I:%M %p ET")
                except:
                    start_time_display = game.get("gameStatusText", "")
            
            games.append({
                "game_id": game.get("gameId"),
                "home_team": home.get("teamTricode", "???"),
                "home_score": home.get("score", 0),
                "home_name": home.get("teamName", ""),
                "away_team": away.get("teamTricode", "???"),
                "away_score": away.get("score", 0),
                "away_name": away.get("teamName", ""),
                "status": game.get("gameStatusText", ""),
                "status_code": game.get("gameStatus", 1),
                "period": game.get("period", 0),
                "start_time": game_time_utc,
                "start_time_display": start_time_display,
                "home_record": f"{home.get('wins', 0)}-{home.get('losses', 0)}",
                "away_record": f"{away.get('wins', 0)}-{away.get('losses', 0)}",
                "arena": game.get("arena", {}).get("arenaName", ""),
                "broadcasters": game.get("broadcasters", {})
            })
        
        # Store in database
        if _db is not None:
            await _db.ticker_cache.update_one(
                {"type": "games"},
                {"$set": {
                    "type": "games",
                    "date": game_date,
                    "games": games,
                    "games_count": len(games),
                    "synced_at": datetime.now(timezone.utc).isoformat()
                }},
                upsert=True
            )
            logger.info(f"[TICKER] Synced {len(games)} games for {game_date}")
        
        return {"success": True, "games_count": len(games), "date": game_date}
        
    except Exception as e:
        logger.error(f"[TICKER] Games sync error: {e}")
        return {"success": False, "error": str(e)}


async def sync_news_headlines():
    """
    Sync NBA news headlines to the database.
    Called at 4 AM daily by the scheduler.
    
    Fetches from ESPN, CBS Sports, and other RSS feeds.
    """
    logger.info("[TICKER] Starting daily news sync...")
    
    try:
        headlines = []
        import re
        
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            # ===== ESPN NBA News RSS =====
            try:
                espn_response = await client.get("https://www.espn.com/espn/rss/nba/news")
                if espn_response.status_code == 200:
                    items = re.findall(r'<item>.*?<title><!\[CDATA\[(.*?)\]\]></title>.*?</item>', 
                                      espn_response.text, re.DOTALL)[:5]
                    for item in items:
                        clean_title = item.strip()
                        if clean_title and len(clean_title) > 10:
                            headlines.append({
                                "text": f"ESPN: {clean_title}",
                                "type": "breaking",
                                "source": "espn",
                                "priority": 1
                            })
            except Exception as e:
                logger.debug(f"ESPN fetch failed: {e}")
            
            # ===== CBS Sports NBA RSS =====
            try:
                cbs_response = await client.get("https://www.cbssports.com/rss/headlines/nba/")
                if cbs_response.status_code == 200:
                    items = re.findall(r'<title><!\[CDATA\[(.*?)\]\]></title>', cbs_response.text)[:5]
                    for item in items[1:]:
                        clean_title = item.strip()
                        if clean_title and len(clean_title) > 10:
                            headlines.append({
                                "text": f"CBS: {clean_title}",
                                "type": "info",
                                "source": "cbs",
                                "priority": 2
                            })
            except Exception as e:
                logger.debug(f"CBS fetch failed: {e}")
            
            # ===== Bleacher Report NBA =====
            try:
                br_response = await client.get("https://bleacherreport.com/articles/feed")
                if br_response.status_code == 200:
                    # Try to find NBA items
                    items = re.findall(r'<title>(.*?)</title>', br_response.text)
                    nba_items = [i for i in items if any(term in i.lower() for term in ['nba', 'lakers', 'celtics', 'warriors', 'knicks', 'nets', 'heat', 'bucks'])][:3]
                    for item in nba_items:
                        clean_title = item.strip()
                        if clean_title and len(clean_title) > 10:
                            headlines.append({
                                "text": f"BR: {clean_title}",
                                "type": "info",
                                "source": "bleacher_report",
                                "priority": 3
                            })
            except Exception as e:
                logger.debug(f"Bleacher Report fetch failed: {e}")
        
        # Add injury updates from database
        if _db is not None:
            injuries = await _db.bdl_injuries.find(
                {"status": {"$in": ["Out", "Doubtful", "Questionable"]}},
                {"_id": 0, "player_name": 1, "status": 1, "reason": 1}
            ).limit(5).to_list(5)
            
            for inj in injuries:
                headlines.append({
                    "text": f"INJURY: {inj.get('player_name')} ({inj.get('status')}) - {inj.get('reason', 'N/A')}",
                    "type": "injury",
                    "source": "injuries_db",
                    "priority": 1
                })
        
        # Sort by priority and limit
        headlines.sort(key=lambda x: x.get("priority", 99))
        headlines = headlines[:15]
        
        # Store in database
        if _db is not None:
            await _db.ticker_cache.update_one(
                {"type": "news"},
                {"$set": {
                    "type": "news",
                    "headlines": headlines,
                    "headlines_count": len(headlines),
                    "synced_at": datetime.now(timezone.utc).isoformat()
                }},
                upsert=True
            )
            logger.info(f"[TICKER] Synced {len(headlines)} news headlines")
        
        return {"success": True, "headlines_count": len(headlines)}
        
    except Exception as e:
        logger.error(f"[TICKER] News sync error: {e}")
        return {"success": False, "error": str(e)}


@router.get("/scores")
async def get_live_scores():
    """
    Get today's NBA games from cached data.
    
    Returns games synced at 4 AM, with live updates if games are in progress.
    """
    try:
        # First try to get from DB cache
        if _db is not None:
            cached = await _db.ticker_cache.find_one(
                {"type": "games"},
                {"_id": 0}
            )
            
            if cached and cached.get("games"):
                # Check if any games are live - if so, fetch live scores
                games = cached.get("games", [])
                any_live = any(g.get("status_code") == 2 for g in games)
                
                if any_live:
                    # Fetch live scores for in-progress games
                    try:
                        from nba_api.live.nba.endpoints import scoreboard
                        board = scoreboard.ScoreBoard()
                        data = board.get_dict()
                        
                        live_games = {}
                        for game in data.get("scoreboard", {}).get("games", []):
                            live_games[game.get("gameId")] = {
                                "home_score": game.get("homeTeam", {}).get("score", 0),
                                "away_score": game.get("awayTeam", {}).get("score", 0),
                                "status": game.get("gameStatusText", ""),
                                "status_code": game.get("gameStatus", 1),
                                "period": game.get("period", 0)
                            }
                        
                        # Update cached games with live scores
                        for game in games:
                            if game.get("game_id") in live_games:
                                game.update(live_games[game["game_id"]])
                    except:
                        pass  # Use cached scores if live fetch fails
                
                return {
                    "success": True, 
                    "games": games, 
                    "date": cached.get("date"),
                    "synced_at": cached.get("synced_at"),
                    "cached": True
                }
        
        # Fallback: fetch live if no cache
        return await _fetch_live_scores_fallback()
        
    except Exception as e:
        logger.error(f"[LIVE] Scores error: {e}")
        return {"success": False, "games": [], "error": str(e)}


async def _fetch_live_scores_fallback():
    """Fallback to fetch live scores directly from NBA API."""
    try:
        from nba_api.live.nba.endpoints import scoreboard
        
        board = scoreboard.ScoreBoard()
        data = board.get_dict()
        
        games = []
        scoreboard_data = data.get("scoreboard", {})
        
        for game in scoreboard_data.get("games", []):
            home = game.get("homeTeam", {})
            away = game.get("awayTeam", {})
            
            game_time_utc = game.get("gameTimeUTC", "")
            start_time_display = ""
            if game_time_utc:
                try:
                    utc_time = datetime.fromisoformat(game_time_utc.replace("Z", "+00:00"))
                    est_time = utc_time - timedelta(hours=5)
                    start_time_display = est_time.strftime("%-I:%M %p ET")
                except:
                    start_time_display = game.get("gameStatusText", "")
            
            games.append({
                "game_id": game.get("gameId"),
                "home_team": home.get("teamTricode", "???"),
                "home_score": home.get("score", 0),
                "away_team": away.get("teamTricode", "???"),
                "away_score": away.get("score", 0),
                "status": game.get("gameStatusText", ""),
                "status_code": game.get("gameStatus", 1),
                "period": game.get("period", 0),
                "start_time": game_time_utc,
                "start_time_display": start_time_display,
                "home_record": f"{home.get('wins', 0)}-{home.get('losses', 0)}",
                "away_record": f"{away.get('wins', 0)}-{away.get('losses', 0)}"
            })
        
        return {"success": True, "games": games, "cached": False, "source": "nba_api_live"}
        
    except Exception as e:
        logger.error(f"[LIVE] Fallback scores error: {e}")
        return {"success": False, "games": [], "error": str(e)}


@router.get("/news")
async def get_breaking_news():
    """
    Get breaking NBA news from cached data.
    
    Returns headlines synced at 4 AM daily.
    """
    try:
        # Get from DB cache
        if _db is not None:
            cached = await _db.ticker_cache.find_one(
                {"type": "news"},
                {"_id": 0}
            )
            
            if cached and cached.get("headlines"):
                return {
                    "success": True,
                    "headlines": cached.get("headlines", []),
                    "synced_at": cached.get("synced_at"),
                    "cached": True
                }
        
        # Fallback: fetch live if no cache
        return await _fetch_news_fallback()
        
    except Exception as e:
        logger.error(f"[LIVE] News error: {e}")
        return {"success": False, "headlines": [], "error": str(e)}


async def _fetch_news_fallback():
    """Fallback to fetch news directly from RSS feeds."""
    try:
        headlines = []
        import re
        
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            try:
                espn_response = await client.get("https://www.espn.com/espn/rss/nba/news")
                if espn_response.status_code == 200:
                    items = re.findall(r'<item>.*?<title><!\[CDATA\[(.*?)\]\]></title>.*?</item>', 
                                      espn_response.text, re.DOTALL)[:5]
                    for item in items:
                        clean_title = item.strip()
                        if clean_title and len(clean_title) > 10:
                            headlines.append({
                                "text": f"ESPN: {clean_title}",
                                "type": "breaking",
                                "source": "espn"
                            })
            except:
                pass
        
        return {"success": True, "headlines": headlines, "cached": False}
        
    except Exception as e:
        return {"success": False, "headlines": [], "error": str(e)}


@router.post("/sync-ticker")
async def manual_sync_ticker():
    """
    Manually trigger ticker data sync.
    Syncs both games and news headlines.
    """
    games_result = await sync_todays_games()
    news_result = await sync_news_headlines()
    
    return {
        "success": True,
        "games": games_result,
        "news": news_result,
        "synced_at": datetime.now(timezone.utc).isoformat()
    }

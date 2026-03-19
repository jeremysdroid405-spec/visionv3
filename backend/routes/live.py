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
    Sync today's upcoming NBA games to the database.
    Called at 4 AM daily by the scheduler.
    
    Uses BallDontLie API to get today's scheduled games.
    """
    logger.info("[TICKER] Starting daily games sync...")
    
    try:
        # Use BDL API for today's games
        api_key = os.environ.get("BDL_API_KEY") or os.environ.get("BALLDONTLIE_API_KEY")
        if not api_key:
            logger.warning("[TICKER] No BDL API key found")
            return {"success": False, "error": "No BDL API key"}
        
        today = datetime.now().strftime("%Y-%m-%d")
        
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                "https://api.balldontlie.io/v1/games",
                params={"dates[]": today, "per_page": 20},
                headers={"Authorization": api_key}
            )
            
            if response.status_code != 200:
                logger.error(f"[TICKER] BDL API error: {response.status_code}")
                return {"success": False, "error": f"BDL API error: {response.status_code}"}
            
            data = response.json()
            bdl_games = data.get("data", [])
        
        games = []
        for game in bdl_games:
            home = game.get("home_team", {})
            away = game.get("visitor_team", {})
            
            # Parse game time for display
            game_time_utc = game.get("datetime", "")
            start_time_display = ""
            if game_time_utc:
                try:
                    # Convert to EST for display
                    utc_time = datetime.fromisoformat(game_time_utc.replace("Z", "+00:00"))
                    est_time = utc_time - timedelta(hours=5)
                    start_time_display = est_time.strftime("%-I:%M %p ET")
                except:
                    start_time_display = game.get("status", "")
            
            # Determine status
            status_text = game.get("status", "")
            game_status = game.get("period", 0)
            if status_text == "Final":
                status_display = "Final"
            elif game_status == 0:
                status_display = start_time_display or "Scheduled"
            else:
                status_display = status_text
            
            games.append({
                "game_id": str(game.get("id")),
                "home_team": home.get("abbreviation", "???"),
                "home_score": game.get("home_team_score", 0),
                "home_name": home.get("name", ""),
                "away_team": away.get("abbreviation", "???"),
                "away_score": game.get("visitor_team_score", 0),
                "away_name": away.get("name", ""),
                "status": status_display,
                "status_code": 1 if game_status == 0 else (3 if status_text == "Final" else 2),
                "period": game_status,
                "start_time": game_time_utc,
                "start_time_display": start_time_display,
                "home_record": "",  # BDL doesn't provide records in this endpoint
                "away_record": "",
                "arena": "",
                "broadcasters": {}
            })
        
        # Sort by start time
        games.sort(key=lambda x: x.get("start_time", ""))
        
        # Store in database
        if _db is not None:
            await _db.ticker_cache.update_one(
                {"type": "games"},
                {"$set": {
                    "type": "games",
                    "date": today,
                    "games": games,
                    "games_count": len(games),
                    "synced_at": datetime.now(timezone.utc).isoformat()
                }},
                upsert=True
            )
            logger.info(f"[TICKER] Synced {len(games)} games for {today}")
        
        return {"success": True, "games_count": len(games), "date": today}
        
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
    Get today's NBA games with live scores.
    
    Uses BDL live box scores endpoint for real-time updates.
    """
    try:
        # Always fetch fresh from BDL live endpoint for most current data
        api_key = os.environ.get("BDL_API_KEY") or os.environ.get("BALLDONTLIE_API_KEY")
        
        if api_key:
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.get(
                        "https://api.balldontlie.io/v1/box_scores/live",
                        headers={"Authorization": api_key}
                    )
                    
                    if response.status_code == 200:
                        data = response.json()
                        bdl_games = data.get("data", [])
                        
                        if bdl_games:
                            games = []
                            today = datetime.now().strftime("%Y-%m-%d")
                            
                            for game in bdl_games:
                                home = game.get("home_team", {})
                                away = game.get("visitor_team", {})
                                
                                # Parse game time
                                game_time_utc = game.get("datetime", "")
                                start_time_display = ""
                                if game_time_utc:
                                    try:
                                        utc_time = datetime.fromisoformat(game_time_utc.replace("Z", "+00:00"))
                                        est_time = utc_time - timedelta(hours=5)
                                        start_time_display = est_time.strftime("%-I:%M %p ET")
                                    except:
                                        pass
                                
                                # Determine status
                                period = game.get("period", 0)
                                time_remaining = game.get("time", "")
                                status_raw = game.get("status", "")
                                
                                if "Final" in str(status_raw):
                                    status_display = "Final"
                                    status_code = 3
                                elif period > 0:
                                    # Game in progress
                                    if time_remaining:
                                        status_display = f"Q{period} {time_remaining}"
                                    else:
                                        status_display = f"Q{period}"
                                    status_code = 2
                                else:
                                    # Game not started
                                    status_display = start_time_display or "Scheduled"
                                    status_code = 1
                                
                                games.append({
                                    "game_id": str(game.get("id")),
                                    "home_team": home.get("abbreviation", "???"),
                                    "home_score": game.get("home_team_score", 0),
                                    "home_name": home.get("name", ""),
                                    "away_team": away.get("abbreviation", "???"),
                                    "away_score": game.get("visitor_team_score", 0),
                                    "away_name": away.get("name", ""),
                                    "status": status_display,
                                    "status_code": status_code,
                                    "period": period,
                                    "start_time": game_time_utc,
                                    "start_time_display": start_time_display,
                                    "home_record": "",
                                    "away_record": ""
                                })
                            
                            # Sort by start time
                            games.sort(key=lambda x: x.get("start_time", ""))
                            
                            return {
                                "games": games,
                                "date": today,
                                "games_count": len(games),
                                "source": "bdl_live",
                                "synced_at": datetime.now(timezone.utc).isoformat()
                            }
            except Exception as e:
                logger.warning(f"[TICKER] BDL live fetch failed: {e}")
        
        # Fallback to DB cache if BDL fails
        if _db is not None:
            cached = await _db.ticker_cache.find_one(
                {"type": "games"},
                {"_id": 0}
            )
            
            if cached and cached.get("games"):
                return {
                    "games": cached.get("games", []),
                    "date": cached.get("date"),
                    "games_count": cached.get("games_count", 0),
                    "source": "cache",
                    "synced_at": cached.get("synced_at")
                }
        
        return {"games": [], "date": None, "games_count": 0, "source": "empty"}
        
    except Exception as e:
        logger.error(f"[LIVE] Scores error: {e}")
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

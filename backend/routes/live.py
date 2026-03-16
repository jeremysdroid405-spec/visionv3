"""
Live Data Routes
================
Real-time NBA scores and breaking news endpoints.

Endpoints:
- GET /api/live/scores - Live NBA game scores
- GET /api/live/news - Breaking news and injury updates
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

# BallDontLie API config
BDL_API_KEY = os.environ.get("BALLDONTLIE_API_KEY", "")
BDL_BASE = "https://api.balldontlie.io/nba/v1"

# Cache for scores (refresh every 30 seconds)
_scores_cache = {"data": [], "timestamp": None}
_news_cache = {"data": [], "timestamp": None}


@router.get("/scores")
async def get_live_scores():
    """
    Get live NBA game scores.
    
    Returns today's games with scores, periods, and status.
    Caches results for 30 seconds to reduce API calls.
    """
    global _scores_cache
    
    now = datetime.now(timezone.utc)
    
    # Return cached if fresh (< 30 seconds old)
    if _scores_cache["timestamp"] and (now - _scores_cache["timestamp"]).seconds < 30:
        return {"success": True, "games": _scores_cache["data"], "cached": True}
    
    try:
        if not BDL_API_KEY:
            return {"success": True, "games": [], "error": "API not configured"}
        
        # Get today's date in EST
        est_offset = timedelta(hours=-5)
        today_est = (now + est_offset).strftime("%Y-%m-%d")
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{BDL_BASE}/games",
                params={"dates[]": today_est},
                headers={"Authorization": BDL_API_KEY}
            )
            
            if response.status_code != 200:
                logger.warning(f"[LIVE] Scores fetch failed: {response.status_code}")
                return {"success": True, "games": _scores_cache.get("data", [])}
            
            data = response.json()
            games_raw = data.get("data", [])
            
            games = []
            for game in games_raw:
                home_team = game.get("home_team", {})
                away_team = game.get("visitor_team", {})
                
                # Determine game status
                status = game.get("status", "scheduled")
                if status == "Final":
                    status_label = "final"
                elif game.get("period", 0) > 0:
                    status_label = "live"
                    period = game.get("period", 1)
                    if period <= 4:
                        status_label = f"Q{period}"
                    else:
                        status_label = f"OT{period - 4}"
                else:
                    status_label = "upcoming"
                
                games.append({
                    "game_id": game.get("id"),
                    "home_team": home_team.get("abbreviation", ""),
                    "home_score": game.get("home_team_score", 0),
                    "away_team": away_team.get("abbreviation", ""),
                    "away_score": game.get("visitor_team_score", 0),
                    "status": status_label,
                    "period": game.get("period"),
                    "time": game.get("time"),
                    "start_time": game.get("datetime")
                })
            
            # Update cache
            _scores_cache = {"data": games, "timestamp": now}
            
            return {"success": True, "games": games, "cached": False}
            
    except httpx.TimeoutException:
        logger.warning("[LIVE] Scores request timeout")
        return {"success": True, "games": _scores_cache.get("data", [])}
    except Exception as e:
        logger.error(f"[LIVE] Scores error: {e}")
        return {"success": True, "games": _scores_cache.get("data", [])}


@router.get("/news")
async def get_breaking_news():
    """
    Get breaking NBA news from multiple sources.
    
    Pulls from:
    - ESPN NBA headlines
    - Injury reports from database
    - Line movements
    - Real-time game updates
    
    Caches results for 60 seconds.
    """
    global _news_cache
    
    now = datetime.now(timezone.utc)
    
    # Return cached if fresh (< 60 seconds old)
    if _news_cache["timestamp"] and (now - _news_cache["timestamp"]).seconds < 60:
        return {"success": True, "headlines": _news_cache["data"], "cached": True}
    
    try:
        mongo_url = os.environ.get("MONGO_URL")
        db_name = os.environ.get("DB_NAME", "pickvision")
        
        headlines = []
        
        # Fetch real NBA news from ESPN RSS
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                # ESPN NBA News RSS
                espn_response = await client.get("https://www.espn.com/espn/rss/nba/news")
                if espn_response.status_code == 200:
                    import re
                    # Parse RSS items (simple regex extraction)
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
        except Exception as e:
            logger.debug(f"ESPN fetch failed: {e}")
        
        # Fetch from NBA.com RSS
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                nba_response = await client.get("https://www.nba.com/news/rss/")
                if nba_response.status_code == 200:
                    import re
                    items = re.findall(r'<title>(.*?)</title>', nba_response.text)[2:6]  # Skip first 2 (channel title)
                    for item in items:
                        clean_title = item.strip()
                        if clean_title and len(clean_title) > 10 and 'CDATA' not in clean_title:
                            headlines.append({
                                "text": f"NBA.com: {clean_title}",
                                "type": "info",
                                "source": "nba"
                            })
        except Exception as e:
            logger.debug(f"NBA.com fetch failed: {e}")
        
        # Add injury updates from database
        if mongo_url:
            client = AsyncIOMotorClient(mongo_url)
            db = client[db_name]
            
            # Get recent injury updates
            injuries = await db.injury_cache.find(
                {"status": {"$ne": "Healthy"}},
                {"_id": 0, "player_name": 1, "team": 1, "status": 1, "return_date": 1}
            ).sort("updated_at", -1).limit(5).to_list(5)
            
            for injury in injuries:
                player = injury.get("player_name", "Unknown")
                team = injury.get("team", "")
                status = injury.get("status", "")
                
                if "Out" in status:
                    headlines.append({
                        "text": f"INJURY: {player} ({team}) ruled OUT - Monitor usage ripple",
                        "type": "injury",
                        "source": "internal"
                    })
                elif "Questionable" in status or "Doubtful" in status:
                    headlines.append({
                        "text": f"INJURY: {player} ({team}) {status} - Watch for late scratch",
                        "type": "injury",
                        "source": "internal"
                    })
            
            # Get line movements
            movements = await db.line_movements.find(
                {},
                {"_id": 0}
            ).sort("timestamp", -1).limit(3).to_list(3)
            
            for move in movements:
                player = move.get("player_name", "")
                stat = move.get("stat_type", "")
                direction = "up" if move.get("direction") == "up" else "down"
                
                if player and stat:
                    headlines.append({
                        "text": f"LINE MOVE: {player} {stat} shifted {direction}",
                        "type": "breaking",
                        "source": "internal"
                    })
        
        # Add live game updates
        if _scores_cache.get("data"):
            live_games = [g for g in _scores_cache["data"] if g.get("status", "").startswith("Q")]
            for game in live_games[:2]:
                away = game.get("away_team", "")
                home = game.get("home_team", "")
                away_score = game.get("away_score", 0)
                home_score = game.get("home_score", 0)
                period = game.get("status", "")
                headlines.append({
                    "text": f"LIVE: {away} {away_score} @ {home} {home_score} ({period})",
                    "type": "breaking",
                    "source": "live"
                })
        
        # Add fallback headlines if we don't have enough
        if len(headlines) < 5:
            defaults = [
                {"text": "AI insights powered by real-time defensive matchup analysis", "type": "info", "source": "system"},
                {"text": "Defense vs Position (DvP) rankings updated daily at 8AM EST", "type": "info", "source": "system"},
                {"text": "Usage Ripple adjusts projections when key players are OUT", "type": "info", "source": "system"},
                {"text": "Command Post: Build and simulate parlay risk profiles", "type": "info", "source": "system"},
                {"text": "Track line movements and betting trends in real-time", "type": "info", "source": "system"}
            ]
            headlines.extend(defaults[:8 - len(headlines)])
        
        # Shuffle to mix sources
        import random
        random.shuffle(headlines)
        
        # Update cache
        _news_cache = {"data": headlines, "timestamp": now}
        
        return {"success": True, "headlines": headlines, "cached": False, "count": len(headlines)}
        
    except Exception as e:
        logger.error(f"[LIVE] News error: {e}")
        # Return default headlines on error
        return {
            "success": True,
            "headlines": [
                {"text": "Real-time NBA prop analysis powered by AI", "type": "info"},
                {"text": "Injury reports and usage ripple tracked automatically", "type": "info"},
                {"text": "Defense vs Position rankings update daily", "type": "info"}
            ]
        }

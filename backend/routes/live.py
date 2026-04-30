"""
Live Data Routes
================
Real-time NBA/MLB scores and breaking news endpoints.

SSOT ARCHITECTURE: Scores/news come from cached data in MongoDB.
Data is refreshed daily at 4 AM EST via scheduler.

Endpoints:
- GET /api/live/scores - Today's games from DB cache (sport-aware)
- GET /api/live/news - Breaking news from DB cache (sport-aware)
- POST /api/live/sync-ticker - Manual sync trigger (admin only)
"""
import os
import httpx
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from motor.motor_asyncio import AsyncIOMotorClient

from services.config.collection_names import COLL
from services.observability import log_silent_failure

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

# MLB Team abbreviations
MLB_TEAM_ABBREV_MAP = {
    "Arizona Diamondbacks": "ARI", "Atlanta Braves": "ATL", "Baltimore Orioles": "BAL",
    "Boston Red Sox": "BOS", "Chicago Cubs": "CHC", "Chicago White Sox": "CHW",
    "Cincinnati Reds": "CIN", "Cleveland Guardians": "CLE", "Colorado Rockies": "COL",
    "Detroit Tigers": "DET", "Houston Astros": "HOU", "Kansas City Royals": "KC",
    "Los Angeles Angels": "LAA", "Los Angeles Dodgers": "LAD", "Miami Marlins": "MIA",
    "Milwaukee Brewers": "MIL", "Minnesota Twins": "MIN", "New York Mets": "NYM",
    "New York Yankees": "NYY", "Oakland Athletics": "OAK", "Philadelphia Phillies": "PHI",
    "Pittsburgh Pirates": "PIT", "San Diego Padres": "SD", "San Francisco Giants": "SF",
    "Seattle Mariners": "SEA", "St. Louis Cardinals": "STL", "Tampa Bay Rays": "TB",
    "Texas Rangers": "TEX", "Toronto Blue Jays": "TOR", "Washington Nationals": "WSH"
}

def get_team_abbrev(team_name: str, sport: str = "nba") -> str:
    """Get proper team abbreviation from full name."""
    if not team_name:
        return "???"
    
    abbrev_map = MLB_TEAM_ABBREV_MAP if sport == "mlb" else TEAM_ABBREV_MAP
    
    # Direct lookup
    if team_name in abbrev_map:
        return abbrev_map[team_name]
    # Try partial match
    for full_name, abbrev in abbrev_map.items():
        if team_name in full_name or full_name in team_name:
            return abbrev
    # Fallback to first 3 letters
    return team_name[:3].upper()

# Database reference
_db = None

def set_db(db):
    global _db
    _db = db


# ============================================================
# MLB GAMES SYNC (Circuit Breaker Pattern)
# ============================================================
MLB_SYNC_CIRCUIT_BREAKER = {
    "failures": 0,
    "last_failure": None,
    "is_open": False,
    "threshold": 3,
    "reset_timeout": 300  # 5 minutes
}

async def sync_mlb_todays_games():
    """
    Sync today's MLB games to the database.
    Uses BallDontLie MLB API with circuit breaker pattern.
    """
    global MLB_SYNC_CIRCUIT_BREAKER
    
    # Check circuit breaker
    if MLB_SYNC_CIRCUIT_BREAKER["is_open"]:
        if MLB_SYNC_CIRCUIT_BREAKER["last_failure"]:
            elapsed = (datetime.now(timezone.utc) - MLB_SYNC_CIRCUIT_BREAKER["last_failure"]).total_seconds()
            if elapsed < MLB_SYNC_CIRCUIT_BREAKER["reset_timeout"]:
                logger.warning(f"[MLB TICKER] Circuit breaker OPEN - skipping sync ({elapsed:.0f}s since last failure)")
                return {"success": False, "error": "Circuit breaker open", "retry_after": int(MLB_SYNC_CIRCUIT_BREAKER["reset_timeout"] - elapsed)}
            else:
                # Reset circuit breaker
                MLB_SYNC_CIRCUIT_BREAKER["is_open"] = False
                MLB_SYNC_CIRCUIT_BREAKER["failures"] = 0
                logger.info("[MLB TICKER] Circuit breaker RESET - attempting sync")
    
    logger.info("[MLB TICKER] Starting daily MLB games sync...")
    
    try:
        api_key = os.environ.get("BDL_API_KEY") or os.environ.get("BALLDONTLIE_API_KEY")
        if not api_key:
            logger.warning("[MLB TICKER] No BDL API key found")
            return {"success": False, "error": "No BDL API key"}
        
        today = datetime.now().strftime("%Y-%m-%d")
        
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                "https://api.balldontlie.io/mlb/v1/games",
                params={"dates[]": today, "per_page": 30},
                headers={"Authorization": api_key}
            )
            
            if response.status_code != 200:
                # Increment failure count
                MLB_SYNC_CIRCUIT_BREAKER["failures"] += 1
                MLB_SYNC_CIRCUIT_BREAKER["last_failure"] = datetime.now(timezone.utc)
                if MLB_SYNC_CIRCUIT_BREAKER["failures"] >= MLB_SYNC_CIRCUIT_BREAKER["threshold"]:
                    MLB_SYNC_CIRCUIT_BREAKER["is_open"] = True
                    logger.error(f"[MLB TICKER] Circuit breaker TRIPPED after {MLB_SYNC_CIRCUIT_BREAKER['failures']} failures")
                
                logger.error(f"[MLB TICKER] BDL API error: {response.status_code}")
                return {"success": False, "error": f"BDL API error: {response.status_code}"}
            
            data = response.json()
            bdl_games = data.get("data", [])
        
        games = []
        for game in bdl_games:
            home = game.get("home_team", {})
            away = game.get("away_team", {}) or game.get("visitor_team", {}) or {}
            
            # Parse game time
            game_time_utc = game.get("datetime", "") or game.get("date", "")
            start_time_display = ""
            if game_time_utc:
                try:
                    utc_time = datetime.fromisoformat(game_time_utc.replace("Z", "+00:00"))
                    est_time = utc_time - timedelta(hours=5)
                    start_time_display = est_time.strftime("%-I:%M %p ET")
                except:
                    start_time_display = game.get("status", "")
            
            # Determine status
            status_text = game.get("status", "")
            inning = game.get("inning", 0) or game.get("period", 0)
            inning_half = game.get("inning_half", "")
            
            if "Final" in str(status_text):
                status_display = "Final"
                status_code = 3
            elif inning > 0:
                half = "Top" if inning_half == "top" else "Bot" if inning_half == "bottom" else ""
                status_display = f"{half} {inning}" if half else f"Inning {inning}"
                status_code = 2
            else:
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
                "inning": inning,
                "inning_half": inning_half,
                "start_time": game_time_utc,
                "start_time_display": start_time_display,
                "sport": "mlb"
            })
        
        # Sort by start time
        games.sort(key=lambda x: x.get("start_time", ""))
        
        # Store in database
        if _db is not None:
            await _db[COLL.shared("ticker_cache")].update_one(
                {"type": "mlb_games"},
                {"$set": {
                    "type": "mlb_games",
                    "date": today,
                    "games": games,
                    "games_count": len(games),
                    "synced_at": datetime.now(timezone.utc).isoformat()
                }},
                upsert=True
            )
            logger.info(f"[MLB TICKER] Synced {len(games)} MLB games for {today}")
        
        # Reset failure count on success
        MLB_SYNC_CIRCUIT_BREAKER["failures"] = 0
        
        return {"success": True, "games_count": len(games), "date": today}
        
    except Exception as e:
        # Increment failure count
        MLB_SYNC_CIRCUIT_BREAKER["failures"] += 1
        MLB_SYNC_CIRCUIT_BREAKER["last_failure"] = datetime.now(timezone.utc)
        if MLB_SYNC_CIRCUIT_BREAKER["failures"] >= MLB_SYNC_CIRCUIT_BREAKER["threshold"]:
            MLB_SYNC_CIRCUIT_BREAKER["is_open"] = True
            logger.error(f"[MLB TICKER] Circuit breaker TRIPPED after {MLB_SYNC_CIRCUIT_BREAKER['failures']} failures")
        
        logger.error(f"[MLB TICKER] Games sync error: {e}")
        return {"success": False, "error": str(e)}


async def sync_mlb_news_headlines():
    """
    Sync MLB news headlines to the database.
    Fetches from ESPN MLB RSS feed.
    """
    logger.info("[MLB TICKER] Starting MLB news sync...")
    
    try:
        headlines = []
        import re
        
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            # ===== ESPN MLB News RSS =====
            try:
                espn_response = await client.get("https://www.espn.com/espn/rss/mlb/news")
                if espn_response.status_code == 200:
                    items = re.findall(r'<item>.*?<title><!\[CDATA\[(.*?)\]\]></title>.*?</item>', 
                                      espn_response.text, re.DOTALL)[:8]
                    for item in items:
                        clean_title = item.strip()
                        if clean_title and len(clean_title) > 10:
                            headlines.append({
                                "text": f"ESPN: {clean_title}",
                                "type": "breaking",
                                "source": "espn_mlb",
                                "priority": 1
                            })
            except Exception as e:
                logger.debug(f"ESPN MLB fetch failed: {e}")
            
            # ===== CBS Sports MLB RSS =====
            try:
                cbs_response = await client.get("https://www.cbssports.com/rss/headlines/mlb/")
                if cbs_response.status_code == 200:
                    items = re.findall(r'<title><!\[CDATA\[(.*?)\]\]></title>', cbs_response.text)[:5]
                    for item in items[1:]:
                        clean_title = item.strip()
                        if clean_title and len(clean_title) > 10:
                            headlines.append({
                                "text": f"CBS: {clean_title}",
                                "type": "info",
                                "source": "cbs_mlb",
                                "priority": 2
                            })
            except Exception as e:
                logger.debug(f"CBS MLB fetch failed: {e}")
            
            # ===== MLB.com News (via Bleacher Report) =====
            try:
                br_response = await client.get("https://bleacherreport.com/articles/feed")
                if br_response.status_code == 200:
                    items = re.findall(r'<title>(.*?)</title>', br_response.text)
                    mlb_items = [i for i in items if any(term in i.lower() for term in ['mlb', 'yankees', 'dodgers', 'mets', 'red sox', 'cubs', 'braves', 'astros', 'phillies'])][:3]
                    for item in mlb_items:
                        clean_title = item.strip()
                        if clean_title and len(clean_title) > 10:
                            headlines.append({
                                "text": f"BR: {clean_title}",
                                "type": "info",
                                "source": "bleacher_report_mlb",
                                "priority": 3
                            })
            except Exception as e:
                logger.debug(f"Bleacher Report MLB fetch failed: {e}")
        
        # Sort by priority and limit
        headlines.sort(key=lambda x: x.get("priority", 99))
        headlines = headlines[:15]
        
        # Store in database
        if _db is not None:
            await _db[COLL.shared("ticker_cache")].update_one(
                {"type": "mlb_news"},
                {"$set": {
                    "type": "mlb_news",
                    "headlines": headlines,
                    "headlines_count": len(headlines),
                    "synced_at": datetime.now(timezone.utc).isoformat()
                }},
                upsert=True
            )
            logger.info(f"[MLB TICKER] Synced {len(headlines)} MLB news headlines")
        
        return {"success": True, "headlines_count": len(headlines)}
        
    except Exception as e:
        logger.error(f"[MLB TICKER] News sync error: {e}")
        return {"success": False, "error": str(e)}


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
            away = game.get("away_team", {}) or game.get("visitor_team", {}) or {}
            
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
            await _db[COLL.shared("ticker_cache")].update_one(
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
            await _db[COLL.shared("ticker_cache")].update_one(
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
async def get_live_scores(sport: str = Query("nba", description="Sport: nba or mlb")):
    """
    Get today's games with live scores.
    
    Sport-aware: Returns NBA or MLB games based on query param.
    """
    # Route to sport-specific handler
    if sport == "mlb":
        return await get_mlb_live_scores()
    
    # NBA (default) - uses BDL live box scores endpoint
    try:
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
                                away = game.get("away_team", {}) or game.get("visitor_team", {}) or {}
                                
                                # Parse game time
                                game_time_utc = game.get("datetime", "")
                                start_time_display = ""
                                if game_time_utc:
                                    try:
                                        utc_time = datetime.fromisoformat(game_time_utc.replace("Z", "+00:00"))
                                        est_time = utc_time - timedelta(hours=5)
                                        start_time_display = est_time.strftime("%-I:%M %p ET")
                                    except Exception as _swept_exc:
                                        log_silent_failure("routes.live.get_live_scores", _swept_exc)  # sweep-auto-converted
                                
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
                            
                            # 2026-04-29 — Live Ticker filter contract:
                            #   commence_time >= now (UTC) AND status != Final
                            # Drop yesterday's finals and any other game that
                            # started in the past unless still in-play.
                            now_utc = datetime.now(timezone.utc)
                            kept = []
                            for g in games:
                                if g.get("status_code") == 3:  # Final → drop
                                    continue
                                ct = g.get("start_time")
                                try:
                                    if ct:
                                        ct_utc = datetime.fromisoformat(
                                            str(ct).replace("Z", "+00:00")
                                        )
                                        if ct_utc.tzinfo is None:
                                            ct_utc = ct_utc.replace(tzinfo=timezone.utc)
                                        if g.get("status_code") == 2:
                                            # In-play → keep regardless of ct
                                            kept.append(g)
                                        elif ct_utc >= now_utc:
                                            kept.append(g)
                                        # else: scheduled but commence_time in the past → drop
                                except (ValueError, TypeError):
                                    # Unparseable timestamp: keep only if currently in-play.
                                    if g.get("status_code") == 2:
                                        kept.append(g)
                            games = kept
                            # Sort by start time
                            games.sort(key=lambda x: x.get("start_time", ""))

                            # ── Runtime Contract Enforcer (2026-04-29) ────
                            # Final freshness gate. Any past-game / final
                            # row that slipped past the in-place filter is
                            # dropped here and counted in
                            # /api/health/contracts.
                            try:
                                from services.contract_enforcer import (
                                    enforce_ticker_freshness,
                                )
                                games = await enforce_ticker_freshness(_db, games, sport="nba")
                            except Exception as _ce_err:
                                logger.error(
                                    f"[CONTRACT_ENFORCER:nba:ticker] failed: {_ce_err}",
                                    exc_info=True,
                                )

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
            cached = await _db[COLL.shared("ticker_cache")].find_one(
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
async def get_breaking_news(sport: str = Query("nba", description="Sport: nba or mlb")):
    """
    Get breaking news from cached data.
    
    Sport-aware: Returns NBA or MLB news based on query param.
    """
    try:
        # Determine cache key based on sport
        cache_type = "mlb_news" if sport == "mlb" else "news"
        
        # Get from DB cache
        if _db is not None:
            cached = await _db[COLL.shared("ticker_cache")].find_one(
                {"type": cache_type},
                {"_id": 0}
            )
            
            if cached and cached.get("headlines"):
                return {
                    "success": True,
                    "headlines": cached.get("headlines", []),
                    "synced_at": cached.get("synced_at"),
                    "cached": True,
                    "sport": sport
                }
        
        # Fallback: fetch live if no cache
        return await _fetch_news_fallback(sport)
        
    except Exception as e:
        logger.error(f"[LIVE] News error: {e}")
        return {"success": False, "headlines": [], "error": str(e)}


async def _fetch_news_fallback(sport: str = "nba"):
    """Fallback to fetch news directly from RSS feeds."""
    try:
        headlines = []
        import re
        
        # Select RSS feed based on sport
        rss_url = "https://www.espn.com/espn/rss/mlb/news" if sport == "mlb" else "https://www.espn.com/espn/rss/nba/news"
        
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            try:
                espn_response = await client.get(rss_url)
                if espn_response.status_code == 200:
                    items = re.findall(r'<item>.*?<title><!\[CDATA\[(.*?)\]\]></title>.*?</item>', 
                                      espn_response.text, re.DOTALL)[:5]
                    for item in items:
                        clean_title = item.strip()
                        if clean_title and len(clean_title) > 10:
                            headlines.append({
                                "text": f"ESPN: {clean_title}",
                                "type": "breaking",
                                "source": f"espn_{sport}"
                            })
            except Exception as _swept_exc:
                log_silent_failure("routes.live._fetch_news_fallback", _swept_exc)  # sweep-auto-converted
        
        return {"success": True, "headlines": headlines, "cached": False, "sport": sport}
        
    except Exception as e:
        return {"success": False, "headlines": [], "error": str(e)}


async def get_mlb_live_scores():
    """
    Get today's MLB games with live scores.
    Uses BallDontLie MLB API which provides real-time game data.
    
    Note: Uses US Eastern timezone for date since MLB games are based on US times.
    
    BDL MLB Games Response Fields:
    - status: "STATUS_IN_PROGRESS", "STATUS_SCHEDULED", "STATUS_FINAL"
    - period: current inning number
    - home_team_data.runs / away_team_data.runs: current scores
    - home_team_data.inning_scores: array of runs per inning (length = innings played)
    - away_team_data.inning_scores: array of runs per inning
    
    Inning Half Logic:
    - If away_inning_scores.length > home_inning_scores.length: Bottom of inning
    - If away_inning_scores.length == home_inning_scores.length: Top of inning
    """
    try:
        api_key = os.environ.get("BDL_API_KEY") or os.environ.get("BALLDONTLIE_API_KEY")
        
        if not api_key:
            logger.warning("[MLB TICKER] No BDL API key found")
            return {"games": [], "date": None, "games_count": 0, "source": "no_key", "sport": "mlb"}
        
        # Use US Eastern timezone for date since MLB games are US-based
        # This ensures we query the correct day's games
        from zoneinfo import ZoneInfo
        eastern = ZoneInfo("America/New_York")
        today_eastern = datetime.now(eastern).strftime("%Y-%m-%d")
        
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                "https://api.balldontlie.io/mlb/v1/games",
                params={"dates[]": today_eastern, "per_page": 30},
                headers={"Authorization": api_key}
            )
            
            if response.status_code != 200:
                logger.error(f"[MLB TICKER] BDL API error: {response.status_code}")
                return await _get_mlb_cache_fallback()
            
            data = response.json()
            bdl_games = data.get("data", [])
            
            games = []
            for game in bdl_games:
                home_team = game.get("home_team", {})
                away_team = game.get("away_team", {})
                home_data = game.get("home_team_data", {})
                away_data = game.get("away_team_data", {})
                
                # Get scores from team_data
                home_score = home_data.get("runs", 0) or 0
                away_score = away_data.get("runs", 0) or 0
                
                # Get inning info
                current_inning = game.get("period", 0) or 0
                status_raw = game.get("status", "")
                
                # Determine inning half from inning_scores arrays
                home_innings = home_data.get("inning_scores", []) or []
                away_innings = away_data.get("inning_scores", []) or []
                
                # Inning half logic:
                # - Away team always bats first (top)
                # - If away has more inning entries than home, we're in bottom
                # - If equal, we're in top
                inning_half = ""
                if current_inning > 0:
                    if len(away_innings) > len(home_innings):
                        inning_half = "bottom"
                    else:
                        inning_half = "top"
                
                # Parse game time for scheduled games
                game_time_utc = game.get("date", "")
                start_time_display = ""
                if game_time_utc:
                    try:
                        utc_time = datetime.fromisoformat(game_time_utc.replace("Z", "+00:00"))
                        est_time = utc_time - timedelta(hours=4)  # EDT
                        start_time_display = est_time.strftime("%-I:%M %p ET")
                    except Exception as _swept_exc:
                        log_silent_failure("routes.live.get_mlb_live_scores", _swept_exc)  # sweep-auto-converted
                
                # Build status display
                if status_raw == "STATUS_FINAL":
                    status_display = "Final"
                    status_code = 3
                elif status_raw == "STATUS_IN_PROGRESS" and current_inning > 0:
                    # Format as "Top 5" or "Bot 9"
                    half_abbrev = "Top" if inning_half == "top" else "Bot" if inning_half == "bottom" else ""
                    status_display = f"{half_abbrev} {current_inning}" if half_abbrev else f"Inn {current_inning}"
                    status_code = 2
                else:
                    # Scheduled game - show start time
                    status_display = start_time_display or "Scheduled"
                    status_code = 1
                
                games.append({
                    "game_id": str(game.get("id")),
                    "home_team": home_team.get("abbreviation", "???"),
                    "home_score": home_score,
                    "home_name": home_team.get("name", ""),
                    "away_team": away_team.get("abbreviation", "???"),
                    "away_score": away_score,
                    "away_name": away_team.get("name", ""),
                    "status": status_display,
                    "status_code": status_code,
                    "inning": current_inning,
                    "inning_half": inning_half,
                    "start_time": game_time_utc,
                    "start_time_display": start_time_display,
                    "sport": "mlb",
                    "venue": game.get("venue", "")
                })
            
            # 2026-04-29 — Live Ticker filter contract:
            #   commence_time >= now (UTC) AND status != Final
            # Drops yesterday's tail-end games and same-day finished games.
            now_utc = datetime.now(timezone.utc)
            kept = []
            for g in games:
                if g.get("status_code") == 3:  # Final → drop
                    continue
                ct = g.get("start_time")
                try:
                    if ct:
                        ct_utc = datetime.fromisoformat(
                            str(ct).replace("Z", "+00:00")
                        )
                        if ct_utc.tzinfo is None:
                            ct_utc = ct_utc.replace(tzinfo=timezone.utc)
                        if g.get("status_code") == 2:
                            # In-play → keep regardless of ct
                            kept.append(g)
                        elif ct_utc >= now_utc:
                            kept.append(g)
                        # else: scheduled but ct in the past → drop
                except (ValueError, TypeError):
                    if g.get("status_code") == 2:
                        kept.append(g)
            games = kept

            # Sort by start time
            games.sort(key=lambda x: x.get("start_time", ""))

            # ── Runtime Contract Enforcer (2026-04-29) ────────────────
            # Final freshness gate. Past-game / final rows that slipped
            # past the in-place filter are dropped here and counted in
            # /api/health/contracts.
            try:
                from services.contract_enforcer import (
                    enforce_ticker_freshness,
                )
                games = await enforce_ticker_freshness(_db, games, sport="mlb")
            except Exception as _ce_err:
                logger.error(
                    f"[CONTRACT_ENFORCER:mlb:ticker] failed: {_ce_err}",
                    exc_info=True,
                )

            logger.info(f"[MLB TICKER] Fetched {len(games)} upcoming/in-play games from BDL")
            
            return {
                "games": games,
                "date": today_eastern,
                "games_count": len(games),
                "source": "bdl_mlb",
                "sport": "mlb",
                "synced_at": datetime.now(timezone.utc).isoformat()
            }
        
    except Exception as e:
        logger.error(f"[MLB LIVE] Scores error: {e}")
        return await _get_mlb_cache_fallback()


async def _get_mlb_cache_fallback():
    """Fallback to DB cache if BDL API fails."""
    if _db is not None:
        cached = await _db[COLL.shared("ticker_cache")].find_one(
            {"type": "mlb_games"},
            {"_id": 0}
        )
        
        if cached and cached.get("games"):
            return {
                "games": cached.get("games", []),
                "date": cached.get("date"),
                "games_count": cached.get("games_count", 0),
                "source": "cache",
                "sport": "mlb",
                "synced_at": cached.get("synced_at")
            }
    
    return {"games": [], "date": None, "games_count": 0, "source": "empty", "sport": "mlb"}


@router.post("/sync-ticker")
async def manual_sync_ticker(sport: str = Query("all", description="Sport: nba, mlb, or all")):
    """
    Manually trigger ticker data sync.
    Syncs games and news headlines for specified sport.
    """
    results = {
        "success": True,
        "synced_at": datetime.now(timezone.utc).isoformat()
    }
    
    if sport in ["nba", "all"]:
        results["nba_games"] = await sync_todays_games()
        results["nba_news"] = await sync_news_headlines()
    
    if sport in ["mlb", "all"]:
        results["mlb_games"] = await sync_mlb_todays_games()
        results["mlb_news"] = await sync_mlb_news_headlines()
    
    return results

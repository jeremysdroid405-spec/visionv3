"""
Live Scores Engine v1.0
========================

Fetches live NBA scores from The Odds API /scores endpoint.
Also includes RSS feed integration for breaking news.

Features:
- Live score fetching with in_play detection
- Game status tracking (not started, in progress, final)
- Breaking news from RSS feeds (Rotoworld, ESPN Injury Reports)
- Background polling for fresh data
"""

import os
import logging
import asyncio
import httpx
import feedparser
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

ODDS_API_KEY = os.environ.get("ODDS_API_KEY")
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# RSS Feed URLs for breaking news
RSS_FEEDS = [
    {
        "name": "ESPN NBA",
        "url": "https://www.espn.com/espn/rss/nba/news",
        "category": "news"
    },
    {
        "name": "Yahoo Sports NBA",
        "url": "https://sports.yahoo.com/nba/rss.xml",
        "category": "news"
    },
    {
        "name": "CBS Sports NBA",
        "url": "https://www.cbssports.com/rss/headlines/nba/",
        "category": "news"
    }
]


class LiveScoresEngine:
    """
    Fetches and manages live NBA scores from The Odds API.
    Also handles breaking news from RSS feeds.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.scores_cache = db.live_scores_cache
        self.news_cache = db.breaking_news_cache
        self._api_available = bool(ODDS_API_KEY)
        self._last_scores_fetch = None
        self._last_news_fetch = None
        
        if not self._api_available:
            logger.warning("[LIVE SCORES] No ODDS_API_KEY found - Live scores disabled")
        else:
            logger.info("[LIVE SCORES] Engine initialized")
    
    async def fetch_live_scores(self) -> Dict[str, Any]:
        """
        Fetch live scores from The Odds API /scores endpoint.
        
        Returns:
            Dict with games, live_count, and upcoming_count
        """
        if not self._api_available:
            return {"success": False, "error": "API key not configured", "games": []}
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{ODDS_API_BASE}/sports/basketball_nba/scores",
                    params={
                        "apiKey": ODDS_API_KEY,
                        "daysFrom": 1  # Get scores from today
                    }
                )
                
                if response.status_code != 200:
                    logger.error(f"[LIVE SCORES] API error: {response.status_code} - {response.text}")
                    return {"success": False, "error": f"API error: {response.status_code}", "games": []}
                
                raw_games = response.json()
                
                # Handle case where API returns empty or None
                if not raw_games:
                    logger.warning("[LIVE SCORES] API returned empty response")
                    return {"success": True, "games": [], "live_count": 0, "upcoming_count": 0, "final_count": 0}
                
                # Ensure raw_games is a list
                if not isinstance(raw_games, list):
                    logger.warning(f"[LIVE SCORES] Unexpected response type: {type(raw_games)}")
                    return {"success": True, "games": [], "live_count": 0, "upcoming_count": 0, "final_count": 0}
                
                # Process games into our format
                games = []
                live_count = 0
                upcoming_count = 0
                final_count = 0
                
                for game in raw_games:
                    try:
                        processed = self._process_game(game)
                        games.append(processed)
                        
                        if processed["status"] == "in_play":
                            live_count += 1
                        elif processed["status"] == "upcoming":
                            upcoming_count += 1
                        else:
                            final_count += 1
                    except Exception as e:
                        logger.warning(f"[LIVE SCORES] Error processing game: {e}")
                        continue
                
                # Sort: Live games first, then upcoming by time, then completed
                games.sort(key=lambda g: (
                    0 if g["status"] == "in_play" else (1 if g["status"] == "upcoming" else 2),
                    g.get("commence_time", "")
                ))
                
                # Cache the results
                await self._cache_scores(games)
                self._last_scores_fetch = datetime.now(timezone.utc)
                
                logger.info(f"[LIVE SCORES] Fetched {len(games)} games (Live: {live_count}, Upcoming: {upcoming_count}, Final: {final_count})")
                
                return {
                    "success": True,
                    "games": games,
                    "live_count": live_count,
                    "upcoming_count": upcoming_count,
                    "final_count": final_count,
                    "fetched_at": self._last_scores_fetch.isoformat()
                }
                
        except Exception as e:
            logger.error(f"[LIVE SCORES] Fetch error: {e}")
            return {"success": False, "error": str(e), "games": []}
    
    def _process_game(self, game: Dict) -> Dict[str, Any]:
        """Process a raw game from the API into our format."""
        
        home_team = game.get("home_team", "")
        away_team = game.get("away_team", "")
        commence_time = game.get("commence_time", "")
        completed = game.get("completed", False)
        
        # Get scores if available
        scores = game.get("scores") or []  # Handle None case
        home_score = None
        away_score = None
        
        if isinstance(scores, list):
            for score in scores:
                if score and isinstance(score, dict):
                    if score.get("name") == home_team:
                        home_score = score.get("score")
                    elif score.get("name") == away_team:
                        away_score = score.get("score")
        
        # Determine game status
        if completed:
            status = "final"
            status_display = "FINAL"
        elif home_score is not None and away_score is not None:
            status = "in_play"
            # Try to get quarter/time info (not always available from Odds API)
            status_display = "LIVE"
        else:
            status = "upcoming"
            # Convert to EST for display
            try:
                game_time = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
                est_time = game_time - timedelta(hours=5)  # Convert UTC to EST
                status_display = est_time.strftime("%I:%M %p ET")
            except:
                status_display = "TBD"
        
        # Get spread if available (for upcoming games)
        spread_display = ""
        if status == "upcoming":
            # We'd need to fetch odds separately, for now show generic
            spread_display = "Line TBD"
        
        return {
            "id": game.get("id", ""),
            "home_team": self._get_team_abbrev(home_team),
            "away_team": self._get_team_abbrev(away_team),
            "home_team_full": home_team,
            "away_team_full": away_team,
            "home_score": int(home_score) if home_score else None,
            "away_score": int(away_score) if away_score else None,
            "status": status,
            "status_display": status_display,
            "spread_display": spread_display,
            "commence_time": commence_time,
            "completed": completed
        }
    
    def _get_team_abbrev(self, team_name: str) -> str:
        """Convert full team name to abbreviation."""
        abbrevs = {
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
        return abbrevs.get(team_name, team_name[:3].upper())
    
    async def _cache_scores(self, games: List[Dict]):
        """Cache scores in MongoDB."""
        try:
            await self.scores_cache.update_one(
                {"type": "live_scores"},
                {
                    "$set": {
                        "type": "live_scores",
                        "games": games,
                        "updated_at": datetime.now(timezone.utc).isoformat()
                    }
                },
                upsert=True
            )
        except Exception as e:
            logger.error(f"[LIVE SCORES] Cache error: {e}")
    
    async def get_cached_scores(self) -> Dict[str, Any]:
        """Get cached scores from MongoDB."""
        try:
            cached = await self.scores_cache.find_one(
                {"type": "live_scores"},
                {"_id": 0}
            )
            
            if cached:
                games = cached.get("games", [])
                live = sum(1 for g in games if g.get("status") == "in_play")
                upcoming = sum(1 for g in games if g.get("status") == "upcoming")
                final = sum(1 for g in games if g.get("status") == "final")
                return {
                    "success": True,
                    "games": games,
                    "live_count": live,
                    "upcoming_count": upcoming,
                    "final_count": final,
                    "cached": True,
                    "updated_at": cached.get("updated_at")
                }
            
            return {"success": False, "error": "No cached scores", "games": []}
            
        except Exception as e:
            logger.error(f"[LIVE SCORES] Cache read error: {e}")
            return {"success": False, "error": str(e), "games": []}
    
    async def fetch_breaking_news(self, custom_headlines: List[str] = None) -> Dict[str, Any]:
        """
        Fetch breaking news from RSS feeds and combine with custom headlines.
        
        Args:
            custom_headlines: Optional list of custom headlines to include
        
        Returns:
            Dict with news items
        """
        news_items = []
        
        # Add custom headlines first (if provided)
        if custom_headlines:
            for headline in custom_headlines:
                news_items.append({
                    "title": headline,
                    "source": "Editorial",
                    "category": "breaking",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "is_custom": True
                })
        
        # Fetch from RSS feeds
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                for feed_config in RSS_FEEDS:
                    try:
                        response = await client.get(feed_config["url"])
                        if response.status_code == 200:
                            feed = feedparser.parse(response.text)
                            
                            # Get latest 5 items from each feed
                            for entry in feed.entries[:5]:
                                # Filter for NBA-relevant news
                                title = entry.get("title", "")
                                if self._is_nba_relevant(title):
                                    news_items.append({
                                        "title": title,
                                        "source": feed_config["name"],
                                        "category": feed_config["category"],
                                        "link": entry.get("link", ""),
                                        "timestamp": entry.get("published", datetime.now(timezone.utc).isoformat()),
                                        "is_custom": False
                                    })
                    except Exception as e:
                        logger.warning(f"[NEWS] Failed to fetch {feed_config['name']}: {e}")
                        continue
                        
        except Exception as e:
            logger.error(f"[NEWS] RSS fetch error: {e}")
        
        # Deduplicate and sort by timestamp
        seen_titles = set()
        unique_news = []
        for item in news_items:
            title_key = item["title"][:50].lower()
            if title_key not in seen_titles:
                seen_titles.add(title_key)
                unique_news.append(item)
        
        # Sort by custom first, then by timestamp
        unique_news.sort(key=lambda x: (0 if x.get("is_custom") else 1, x.get("timestamp", "")), reverse=True)
        
        # Cache the news
        await self._cache_news(unique_news)
        self._last_news_fetch = datetime.now(timezone.utc)
        
        logger.info(f"[NEWS] Fetched {len(unique_news)} news items")
        
        return {
            "success": True,
            "news": unique_news[:15],  # Limit to 15 items
            "news_count": len(unique_news),
            "fetched_at": self._last_news_fetch.isoformat()
        }
    
    def _is_nba_relevant(self, title: str) -> bool:
        """Check if a news title is NBA-relevant."""
        title_lower = title.lower()
        
        # NBA team keywords
        teams = ["lakers", "celtics", "warriors", "nets", "knicks", "heat", "bulls", 
                 "cavs", "cavaliers", "bucks", "sixers", "76ers", "suns", "mavericks",
                 "thunder", "nuggets", "clippers", "grizzlies", "pelicans", "hawks",
                 "hornets", "pistons", "pacers", "magic", "raptors", "jazz", "kings",
                 "spurs", "blazers", "rockets", "wizards", "timberwolves"]
        
        # NBA keywords
        keywords = ["nba", "injury", "out", "questionable", "probable", "doubtful",
                    "basketball", "points", "rebounds", "assists", "trade", "lineup"]
        
        return any(team in title_lower for team in teams) or any(kw in title_lower for kw in keywords)
    
    async def _cache_news(self, news: List[Dict]):
        """Cache news in MongoDB."""
        try:
            await self.news_cache.update_one(
                {"type": "breaking_news"},
                {
                    "$set": {
                        "type": "breaking_news",
                        "news": news,
                        "updated_at": datetime.now(timezone.utc).isoformat()
                    }
                },
                upsert=True
            )
        except Exception as e:
            logger.error(f"[NEWS] Cache error: {e}")
    
    async def get_cached_news(self) -> Dict[str, Any]:
        """Get cached news from MongoDB."""
        try:
            cached = await self.news_cache.find_one(
                {"type": "breaking_news"},
                {"_id": 0}
            )
            
            if cached:
                return {
                    "success": True,
                    "news": cached.get("news", []),
                    "cached": True,
                    "updated_at": cached.get("updated_at")
                }
            
            return {"success": False, "error": "No cached news", "news": []}
            
        except Exception as e:
            logger.error(f"[NEWS] Cache read error: {e}")
            return {"success": False, "error": str(e), "news": []}


# Singleton instance
_live_scores_engine: Optional[LiveScoresEngine] = None


def get_live_scores_engine(db: AsyncIOMotorDatabase = None) -> Optional[LiveScoresEngine]:
    """Get or create the Live Scores Engine singleton."""
    global _live_scores_engine
    
    if _live_scores_engine is None and db is not None:
        _live_scores_engine = LiveScoresEngine(db)
    
    return _live_scores_engine


def init_live_scores_engine(db: AsyncIOMotorDatabase) -> LiveScoresEngine:
    """Initialize the Live Scores Engine."""
    global _live_scores_engine
    _live_scores_engine = LiveScoresEngine(db)
    return _live_scores_engine

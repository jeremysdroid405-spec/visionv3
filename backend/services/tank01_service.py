"""
Tank01 API Service
==================
Extracted from demon_goblin_engine.py for modularity.

Handles all Tank01 API interactions:
- Injury data fetching
- News fetching
- Player injury status lookup
"""
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone, timedelta
import httpx
import asyncio
import os
import logging

from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

# API Configuration
TANK01_BASE = "https://tank01-nba-live-in-game-real-time-statistics-nba.p.rapidapi.com"
TANK01_API_KEY = os.environ.get("TANK01_API_KEY", "d3e0e7b93amshc7b9b60aff7cc7ep1a22c5jsn81dc2f41412d")
TANK01_CACHE_TTL = timedelta(hours=4)

# Keywords for injury detection in news
INJURY_KEYWORDS = [
    "injury", "injured", "out", "questionable", "doubtful",
    "day-to-day", "miss", "sidelined", "ankle", "knee", 
    "hamstring", "illness", "rest", "load management"
]


async def fetch_with_backoff(url: str, headers: Dict, params: Dict = None, max_retries: int = 3) -> Optional[Dict]:
    """
    Fetch with exponential backoff for rate limiting.
    
    Args:
        url: API endpoint
        headers: Request headers
        params: Query parameters
        max_retries: Maximum retry attempts
        
    Returns:
        JSON response or None on failure
    """
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    url, 
                    headers=headers, 
                    params=params, 
                    timeout=15.0
                )
                
                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 429:
                    # Rate limited - backoff
                    wait_time = 2 ** attempt
                    logger.warning(f"[TANK01] Rate limited, waiting {wait_time}s (attempt {attempt + 1})")
                    await asyncio.sleep(wait_time)
                else:
                    logger.warning(f"[TANK01] API returned {response.status_code}")
                    break
                    
        except Exception as e:
            logger.error(f"[TANK01] Request error: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)
    
    return None


class Tank01Service:
    """
    Service for Tank01 API interactions (injuries and news).
    
    Features:
    - 4-hour caching to reduce API calls
    - Exponential backoff for rate limiting
    - Graceful degradation on failure
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.cache = db.dg_tank01_cache
        
        # In-memory caches
        self._injury_data: Dict[str, Dict] = {}
        self._news_data: List[Dict] = []
    
    async def fetch_injuries(self) -> Dict[str, Any]:
        """
        Fetch injury data from Tank01 with:
        - 4-hour cache to reduce API calls
        - Exponential backoff for rate limiting
        - Graceful degradation on failure
        """
        # Check cache first
        cached = await self.cache.find_one({"type": "injuries"})
        if cached:
            cached_time = datetime.fromisoformat(cached["cached_at"])
            age = datetime.now(timezone.utc) - cached_time
            if age < TANK01_CACHE_TTL:
                logger.info(f"[TANK01] Using cached injury data (age: {age.total_seconds():.0f}s)")
                self._injury_data = cached.get("data", {})
                return self._injury_data
        
        # Fetch fresh data with exponential backoff
        url = f"{TANK01_BASE}/getNBATeams"
        params = {"rosters": "true", "schedules": "false"}
        headers = {
            "X-RapidAPI-Key": TANK01_API_KEY,
            "X-RapidAPI-Host": "tank01-nba-live-in-game-real-time-statistics-nba.p.rapidapi.com"
        }
        
        logger.info("[TANK01] Fetching injury data (with backoff)...")
        data = await fetch_with_backoff(url, headers, params)
        
        if data:
            teams = data.get("body", []) if isinstance(data, dict) else data
            
            injuries = {}
            if isinstance(teams, list):
                for team in teams:
                    roster = team.get("Roster", {})
                    if isinstance(roster, dict):
                        for player_id, player_data in roster.items():
                            injury_info = player_data.get("injury", {})
                            if injury_info and isinstance(injury_info, dict):
                                status = injury_info.get("designation", "")
                                if status:
                                    player_name = player_data.get("longName", "")
                                    injuries[player_name.lower()] = {
                                        "status": status,
                                        "description": injury_info.get("description", ""),
                                        "return_date": injury_info.get("injReturnDate", ""),
                                        "team": team.get("teamAbv", "")
                                    }
            
            # Cache the results
            await self.cache.update_one(
                {"type": "injuries"},
                {"$set": {
                    "type": "injuries",
                    "data": injuries,
                    "cached_at": datetime.now(timezone.utc).isoformat()
                }},
                upsert=True
            )
            
            self._injury_data = injuries
            logger.info(f"[TANK01] Found {len(injuries)} injured players (cached for 4h)")
            return injuries
        
        # Fallback to cached data if available (even if expired)
        if cached:
            logger.warning("[TANK01] Using stale cached injury data (API failed)")
            self._injury_data = cached.get("data", {})
            return self._injury_data
        
        logger.warning("[TANK01] No injury data available")
        return {}
    
    async def fetch_news(self) -> List[Dict[str, Any]]:
        """
        Fetch latest NBA news from Tank01 with:
        - 4-hour cache to reduce API calls
        - Exponential backoff for rate limiting
        - Graceful degradation on failure
        """
        # Check cache first
        cached = await self.cache.find_one({"type": "news"})
        if cached:
            cached_time = datetime.fromisoformat(cached["cached_at"])
            age = datetime.now(timezone.utc) - cached_time
            if age < TANK01_CACHE_TTL:
                logger.info(f"[TANK01] Using cached news data (age: {age.total_seconds():.0f}s)")
                self._news_data = cached.get("data", [])
                return self._news_data
        
        # Fetch fresh data with exponential backoff
        url = f"{TANK01_BASE}/getNBANews"
        headers = {
            "X-RapidAPI-Key": TANK01_API_KEY,
            "X-RapidAPI-Host": "tank01-nba-live-in-game-real-time-statistics-nba.p.rapidapi.com"
        }
        
        logger.info("[TANK01] Fetching news (with backoff)...")
        data = await fetch_with_backoff(url, headers)
        
        if data:
            news_items = data.get("body", []) if isinstance(data, dict) else data
            
            if isinstance(news_items, list):
                news_list = news_items[:100]
                
                # Cache the results
                await self.cache.update_one(
                    {"type": "news"},
                    {"$set": {
                        "type": "news",
                        "data": news_list,
                        "cached_at": datetime.now(timezone.utc).isoformat()
                    }},
                    upsert=True
                )
                
                self._news_data = news_list
                logger.info(f"[TANK01] Fetched {len(news_list)} news items (cached for 4h)")
                return news_list
        
        # Fallback to cached data if available (even if expired)
        if cached:
            logger.warning("[TANK01] Using stale cached news data (API failed)")
            self._news_data = cached.get("data", [])
            return self._news_data
        
        logger.warning("[TANK01] No news data available")
        return []
    
    def get_player_injury_status(self, player_name: str) -> Dict[str, Any]:
        """
        Get injury status for a player.
        
        Returns dict with:
        - has_injury: bool
        - injury_status: str or None
        - injury_description: str or None
        - has_news: bool
        - news_items: list
        - warning_level: "none" | "questionable" | "out"
        """
        player_lower = player_name.lower()
        result = {
            "has_injury": False,
            "injury_status": None,
            "injury_description": None,
            "has_news": False,
            "news_items": [],
            "warning_level": "none"
        }
        
        # Check injury data
        if player_lower in self._injury_data:
            injury = self._injury_data[player_lower]
            status = injury.get("status", "").lower()
            result["has_injury"] = True
            result["injury_status"] = injury.get("status", "").upper()
            result["injury_description"] = injury.get("description", "")
            
            if status in ["out"]:
                result["warning_level"] = "out"
            elif status in ["questionable", "doubtful", "day-to-day", "gtd"]:
                result["warning_level"] = "questionable"
        
        # Check news for injury mentions
        name_parts = player_lower.split()
        for news in self._news_data[:50]:
            title = (news.get("title", "") or "").lower()
            mentioned = any(part in title for part in name_parts if len(part) > 2)
            
            if mentioned:
                has_injury_keyword = any(kw in title for kw in INJURY_KEYWORDS)
                if has_injury_keyword:
                    result["has_news"] = True
                    result["news_items"].append({
                        "title": news.get("title", ""),
                        "link": news.get("link", "")
                    })
                    if result["warning_level"] == "none":
                        result["warning_level"] = "questionable"
        
        return result
    
    def get_injury_data(self) -> Dict[str, Dict]:
        """Get the in-memory injury data."""
        return self._injury_data
    
    def get_news_data(self) -> List[Dict]:
        """Get the in-memory news data."""
        return self._news_data
    
    def set_injury_data(self, data: Dict[str, Dict]) -> None:
        """Set the in-memory injury data."""
        self._injury_data = data
    
    def set_news_data(self, data: List[Dict]) -> None:
        """Set the in-memory news data."""
        self._news_data = data

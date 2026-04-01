"""
Data Scraper Service - Third-Party API Fetching
================================================
Handles all external API calls and raw data normalization.
"""
import asyncio
import aiohttp
from typing import Dict, List, Any, Optional
from datetime import datetime
import logging

from config.settings import (
    ODDS_API_KEY, BDL_API_KEY, 
    ODDS_API_BASE, BDL_API_BASE, NBA_API_BASE,
    MAX_RETRIES, RETRY_DELAY, TEAM_ABBREV_MAP
)

logger = logging.getLogger(__name__)


async def fetch_with_backoff(
    url: str, 
    headers: Dict = None, 
    params: Dict = None, 
    max_retries: int = MAX_RETRIES
) -> Optional[Dict]:
    """
    Fetch data from API with exponential backoff retry.
    """
    headers = headers or {}
    params = params or {}
    
    for attempt in range(max_retries):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, params=params, timeout=30) as response:
                    if response.status == 200:
                        return await response.json()
                    elif response.status == 429:  # Rate limited
                        wait_time = RETRY_DELAY * (2 ** attempt)
                        logger.warning(f"Rate limited. Waiting {wait_time}s...")
                        await asyncio.sleep(wait_time)
                    else:
                        logger.error(f"API error {response.status}: {url}")
                        return None
        except asyncio.TimeoutError:
            logger.warning(f"Timeout on attempt {attempt + 1}: {url}")
            await asyncio.sleep(RETRY_DELAY)
        except Exception as e:
            logger.error(f"Fetch error: {e}")
            await asyncio.sleep(RETRY_DELAY)
    
    return None


class OddsApiScraper:
    """Scraper for The Odds API"""
    
    def __init__(self):
        self.base_url = ODDS_API_BASE
        self.api_key = ODDS_API_KEY
    
    async def fetch_todays_events(self) -> List[Dict]:
        """Fetch today's NBA games/events"""
        url = f"{self.base_url}/sports/basketball_nba/events"
        params = {"apiKey": self.api_key}
        
        data = await fetch_with_backoff(url, params=params)
        if not data:
            return []
        
        return data if isinstance(data, list) else []
    
    async def fetch_player_props(self, event_id: str, markets: List[str] = None) -> Optional[Dict]:
        """Fetch player props for a specific event"""
        if not markets:
            markets = [
                "player_points", "player_rebounds", "player_assists",
                "player_threes", "player_blocks", "player_steals",
                "player_points_rebounds_assists", "player_points_rebounds",
                "player_points_assists", "player_rebounds_assists"
            ]
        
        url = f"{self.base_url}/sports/basketball_nba/events/{event_id}/odds"
        params = {
            "apiKey": self.api_key,
            "regions": "us",
            "markets": ",".join(markets),
            "oddsFormat": "american",
            "bookmakers": "prizepicks"
        }
        
        return await fetch_with_backoff(url, params=params)
    
    async def fetch_all_props_today(self) -> List[Dict]:
        """Fetch all player props for today's games"""
        events = await self.fetch_todays_events()
        if not events:
            return []
        
        all_props = []
        for event in events:
            event_id = event.get("id")
            if not event_id:
                continue
            
            props_data = await self.fetch_player_props(event_id)
            if props_data:
                extracted = self.extract_props(props_data, event)
                all_props.extend(extracted)
        
        return all_props
    
    def extract_props(self, odds_data: Dict, event_info: Dict) -> List[Dict]:
        """Extract and normalize props from raw API response"""
        props = []
        
        bookmakers = odds_data.get("bookmakers", [])
        for bookmaker in bookmakers:
            if bookmaker.get("key") != "prizepicks":
                continue
            
            markets = bookmaker.get("markets", [])
            for market in markets:
                market_key = market.get("key", "")
                outcomes = market.get("outcomes", [])
                
                for outcome in outcomes:
                    prop = {
                        "event_id": event_info.get("id"),
                        "home_team": event_info.get("home_team"),
                        "away_team": event_info.get("away_team"),
                        "commence_time": event_info.get("commence_time"),
                        "player_name": outcome.get("description"),
                        "market": market_key,
                        "stat_type": self._normalize_stat_type(market_key),
                        "line": outcome.get("point"),
                        "price": outcome.get("price"),
                        "multiplier": outcome.get("multiplier"),
                        "direction": outcome.get("name", "Over"),
                        "bookmaker": "prizepicks",
                        "fetched_at": datetime.utcnow().isoformat()
                    }
                    props.append(prop)
        
        return props
    
    def _normalize_stat_type(self, market: str) -> str:
        """Normalize market name to stat type"""
        mapping = {
            "player_points": "PTS",
            "player_assists": "AST", 
            "player_rebounds": "REB",
            "player_threes": "3PM",
            "player_blocks": "BLK",
            "player_steals": "STL",
            "player_points_rebounds_assists": "PRA",
            "player_points_rebounds": "P+R",
            "player_points_assists": "P+A",
            "player_rebounds_assists": "R+A"
        }
        return mapping.get(market, market.upper())


class BDLScraper:
    """Scraper for Ball Don't Lie API (player stats)"""
    
    def __init__(self):
        self.base_url = BDL_API_BASE
        self.api_key = BDL_API_KEY
    
    async def search_player(self, player_name: str) -> Optional[Dict]:
        """Search for a player by name"""
        url = f"{self.base_url}/players"
        headers = {"Authorization": self.api_key} if self.api_key else {}
        params = {"search": player_name}
        
        data = await fetch_with_backoff(url, headers=headers, params=params)
        if not data:
            return None
        
        players = data.get("data", [])
        if not players:
            return None
        
        # Find best match
        name_lower = player_name.lower()
        for player in players:
            full_name = f"{player.get('first_name', '')} {player.get('last_name', '')}".lower()
            if full_name == name_lower or name_lower in full_name:
                return player
        
        return players[0] if players else None
    
    async def fetch_player_stats(self, player_id: int, season: int = 2024) -> List[Dict]:
        """Fetch game-by-game stats for a player"""
        url = f"{self.base_url}/stats"
        headers = {"Authorization": self.api_key} if self.api_key else {}
        params = {
            "player_ids[]": player_id,
            "seasons[]": season,
            "per_page": 100
        }
        
        data = await fetch_with_backoff(url, headers=headers, params=params)
        if not data:
            return []
        
        return data.get("data", [])
    
    async def get_player_season_averages(self, player_id: int, season: int = 2024) -> Optional[Dict]:
        """Fetch season averages for a player"""
        url = f"{self.base_url}/season_averages"
        headers = {"Authorization": self.api_key} if self.api_key else {}
        params = {
            "player_ids[]": player_id,
            "season": season
        }
        
        data = await fetch_with_backoff(url, headers=headers, params=params)
        if not data:
            return None
        
        averages = data.get("data", [])
        return averages[0] if averages else None


class NBAScraper:
    """Scraper for NBA.com API (roster, headshots)"""
    
    def __init__(self):
        self.base_url = NBA_API_BASE
        self.headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.nba.com/",
            "Accept": "application/json"
        }
    
    async def fetch_team_roster(self, team_id: int) -> List[Dict]:
        """Fetch team roster"""
        url = f"{self.base_url}/commonteamroster"
        params = {
            "TeamID": team_id,
            "Season": "2025-26"
        }
        
        data = await fetch_with_backoff(url, headers=self.headers, params=params)
        if not data:
            return []
        
        result_sets = data.get("resultSets", [])
        for rs in result_sets:
            if rs.get("name") == "CommonTeamRoster":
                headers = rs.get("headers", [])
                rows = rs.get("rowSet", [])
                return [dict(zip(headers, row)) for row in rows]
        
        return []
    
    def get_headshot_url(self, nba_player_id: int) -> str:
        """Generate headshot URL for a player"""
        return f"https://cdn.nba.com/headshots/nba/latest/1040x760/{nba_player_id}.png"


def normalize_team_name(team_name: str) -> str:
    """Convert full team name to abbreviation"""
    if not team_name:
        return ""
    
    # Already abbreviated
    if len(team_name) <= 3:
        return team_name.upper()
    
    # Check map
    if team_name in TEAM_ABBREV_MAP:
        return TEAM_ABBREV_MAP[team_name]
    
    # Try partial match
    team_lower = team_name.lower()
    for full_name, abbrev in TEAM_ABBREV_MAP.items():
        if team_lower in full_name.lower() or full_name.lower() in team_lower:
            return abbrev
    
    return team_name[:3].upper()


def sanitize_player_name(name: str) -> str:
    """Clean and normalize player name"""
    if not name:
        return ""
    
    # Remove common suffixes
    suffixes = [" Jr.", " Sr.", " III", " II", " IV"]
    cleaned = name.strip()
    for suffix in suffixes:
        if cleaned.endswith(suffix):
            cleaned = cleaned[:-len(suffix)]
    
    return cleaned.strip()

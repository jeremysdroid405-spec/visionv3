"""
Odds API Service
================
Extracted from services.engines.demon_goblin_engine.py for modularity.

Handles all interactions with The Odds API:
- Fetching NBA events
- Fetching PrizePicks odds
- Extracting and classifying props
"""
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
import httpx
import os
import logging

from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

# API Configuration
ODDS_API_KEY = os.environ.get("ODDS_API_KEY")
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# PrizePicks Configuration
PRIZEPICKS_REGION = "us_dfs"
PRIZEPICKS_BOOKMAKER = "prizepicks"
DEMON_ODDS = 100  # Even odds = Demon

# Markets - REDUCED for API quota efficiency
# The Odds API charges PER MARKET per request
# Only fetch essential markets to conserve quota
PRIZEPICKS_ALTERNATE_MARKETS = [
    "player_points_alternate",
    "player_rebounds_alternate", 
    "player_assists_alternate",
    "player_points_rebounds_assists_alternate",  # PRA combo
]

PRIZEPICKS_STANDARD_MARKETS = [
    "player_points",
    "player_rebounds",
    "player_assists",
    "player_points_rebounds_assists",  # PRA combo
]

PRIZEPICKS_ALL_MARKETS = ",".join(PRIZEPICKS_ALTERNATE_MARKETS + PRIZEPICKS_STANDARD_MARKETS)


class OddsApiService:
    """Service for interacting with The Odds API"""
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.events_cache = db.dg_events_cache
        self.odds_cache = db.dg_odds_cache
        
        # In-memory caches
        self._player_popularity: Dict[str, int] = {}
        
        # Shared HTTP client for parallel requests
        self._client: Optional[httpx.AsyncClient] = None
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create shared HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                limits=httpx.Limits(max_connections=10)
            )
        return self._client
    
    async def close_client(self):
        """Close HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
    
    async def fetch_todays_events(self) -> List[Dict[str, Any]]:
        """
        Fetch all NBA events for today from The Odds API.
        
        Returns:
            List of NBA events with game info
        """
        try:
            url = f"{ODDS_API_BASE}/sports/basketball_nba/events"
            params = {"apiKey": ODDS_API_KEY, "dateFormat": "iso"}
            
            client = await self._get_client()
            response = await client.get(url, params=params)
            
            if response.status_code == 200:
                events = response.json()
                
                # Store all events in cache
                await self.events_cache.delete_many({})
                for event in events:
                    event["fetched_at"] = datetime.now(timezone.utc).isoformat()
                    await self.events_cache.insert_one(event)
                    
                    logger.info(f"[ODDS_API] Found {len(events)} NBA events")
                    for e in events[:10]:
                        logger.info(f"  • {e.get('away_team')} @ {e.get('home_team')}")
                    
                    return events
                else:
                    logger.warning(f"[ODDS_API] Events fetch returned {response.status_code}")
                    
        except Exception as e:
            logger.error(f"[ODDS_API] Event fetch error: {e}")
        
        return []
    
    async def fetch_prizepicks_odds(
        self,
        event_id: str,
        event_info: Dict,
        cache_ttl_minutes: int = 15  # Don't refetch if data is <15 min old
    ) -> Dict[str, Any]:
        """
        Fetch PrizePicks odds for an event WITH CACHING.
        
        Uses:
        - regions=us_dfs (Daily Fantasy Sports)
        - bookmakers=prizepicks
        - markets=ESSENTIAL markets only (reduced for API quota)
        
        Caching:
        - Checks MongoDB cache first
        - Only fetches from API if cache is older than cache_ttl_minutes
        
        Returns:
            Odds data with all player props
        """
        try:
            # CHECK CACHE FIRST - avoid redundant API calls
            cached = await self.odds_cache.find_one({
                "event_id": event_id, 
                "source": "prizepicks"
            })
            
            if cached:
                fetched_at = cached.get("fetched_at")
                if fetched_at:
                    if isinstance(fetched_at, str):
                        fetched_at = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
                    
                    age_minutes = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 60
                    
                    if age_minutes < cache_ttl_minutes:
                        logger.debug(f"  [PRIZEPICKS] Using cached data for {event_id} (age: {age_minutes:.1f}m)")
                        return cached
            
            # FETCH FROM API using shared client
            url = f"{ODDS_API_BASE}/sports/basketball_nba/events/{event_id}/odds"
            
            params = {
                "apiKey": ODDS_API_KEY,
                "regions": PRIZEPICKS_REGION,
                "markets": PRIZEPICKS_ALL_MARKETS,
                "bookmakers": PRIZEPICKS_BOOKMAKER,
                "oddsFormat": "american",
                "includeMultipliers": "true"
            }
            
            client = await self._get_client()
            response = await client.get(url, params=params)
            
            if response.status_code == 200:
                odds_data = response.json()
                odds_data["event_id"] = event_id
                odds_data["fetched_at"] = datetime.now(timezone.utc).isoformat()
                odds_data["source"] = "prizepicks"
                
                # Count outcomes
                total_outcomes = 0
                players_found = set()
                for bm in odds_data.get("bookmakers", []):
                    for market in bm.get("markets", []):
                        for outcome in market.get("outcomes", []):
                            total_outcomes += 1
                            if outcome.get("description"):
                                players_found.add(outcome.get("description"))
                
                logger.info(
                    f"  [PRIZEPICKS] {event_info.get('away_team')} @ "
                    f"{event_info.get('home_team')}: {total_outcomes} lines, "
                    f"{len(players_found)} players"
                )
                
                # Store in cache
                await self.odds_cache.update_one(
                    {"event_id": event_id, "source": "prizepicks"},
                    {"$set": odds_data},
                    upsert=True
                )
                
                return odds_data
                
            elif response.status_code == 422:
                # Try with basic markets only
                logger.warning(
                    f"  [PRIZEPICKS] Some markets unavailable for {event_id}, "
                    "trying basic markets"
                )
                params["markets"] = (
                    "player_points,player_points_alternate,"
                    "player_rebounds,player_rebounds_alternate,"
                    "player_assists,player_assists_alternate"
                )
                response = await client.get(url, params=params)
                if response.status_code == 200:
                    odds_data = response.json()
                    odds_data["event_id"] = event_id
                    odds_data["source"] = "prizepicks"
                    return odds_data
            else:
                logger.warning(
                    f"  [PRIZEPICKS] API returned {response.status_code} for {event_id}"
                )
                        
        except Exception as e:
            logger.error(f"[ODDS_API] PrizePicks odds fetch error for {event_id}: {e}")
        
        return {}
    
    async def fetch_standard_odds(
        self,
        event_id: str,
        event_info: Dict
    ) -> Dict[str, Any]:
        """
        Fetch standard markets from DraftKings/FanDuel for comparison.
        
        Returns:
            Odds data from traditional sportsbooks
        """
        try:
            url = f"{ODDS_API_BASE}/sports/basketball_nba/events/{event_id}/odds"
            params = {
                "apiKey": ODDS_API_KEY,
                "regions": "us",
                "markets": ",".join(PRIZEPICKS_STANDARD_MARKETS),
                "bookmakers": "draftkings,fanduel",
                "oddsFormat": "american",
                "includeMultipliers": "true"
            }
            
            client = await self._get_client()
            response = await client.get(url, params=params)
            
            if response.status_code == 200:
                return response.json()
                        
        except Exception as e:
            logger.error(f"[ODDS_API] Standard odds fetch error: {e}")
        
        return {}
    
    async def fetch_sharp_book_odds(
        self,
        event_id: str,
        event_info: Dict,
        cache_ttl_minutes: int = 10
    ) -> Dict[str, Any]:
        """
        Fetch Sharp Book odds (DraftKings + FanDuel) for arbitrage detection.
        
        These lines are used by the Sharp Edge Calculator to find +EV opportunities
        where Vegas smart money disagrees with PrizePicks pricing.
        
        Returns:
            Combined odds data from DraftKings and FanDuel
        """
        try:
            # CHECK CACHE FIRST
            cached = await self.odds_cache.find_one({
                "event_id": event_id, 
                "source": "sharp_books"
            })
            
            if cached:
                fetched_at = cached.get("fetched_at")
                if fetched_at:
                    if isinstance(fetched_at, str):
                        fetched_at = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
                    
                    age_minutes = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 60
                    
                    if age_minutes < cache_ttl_minutes:
                        logger.debug(f"  [SHARP_BOOKS] Using cached data for {event_id} (age: {age_minutes:.1f}m)")
                        return cached
            
            # FETCH FROM API
            url = f"{ODDS_API_BASE}/sports/basketball_nba/events/{event_id}/odds"
            params = {
                "apiKey": ODDS_API_KEY,
                "regions": "us",
                "markets": ",".join(PRIZEPICKS_STANDARD_MARKETS),
                "bookmakers": "draftkings,fanduel",
                "oddsFormat": "american",
                "includeMultipliers": "true"
            }
            
            client = await self._get_client()
            response = await client.get(url, params=params)
            
            if response.status_code == 200:
                odds_data = response.json()
                odds_data["event_id"] = event_id
                odds_data["fetched_at"] = datetime.now(timezone.utc).isoformat()
                odds_data["source"] = "sharp_books"
                
                # Count props by bookmaker
                draftkings_count = 0
                fanduel_count = 0
                
                for bm in odds_data.get("bookmakers", []):
                    bm_key = bm.get("key", "")
                    for market in bm.get("markets", []):
                        outcome_count = len(market.get("outcomes", []))
                        if bm_key == "draftkings":
                            draftkings_count += outcome_count
                        elif bm_key == "fanduel":
                            fanduel_count += outcome_count
                
                if draftkings_count > 0 or fanduel_count > 0:
                    logger.info(
                        f"  [SHARP_BOOKS] {event_info.get('away_team', '')} @ "
                        f"{event_info.get('home_team', '')}: "
                        f"DraftKings={draftkings_count}, FanDuel={fanduel_count}"
                    )
                    
                    # Store in cache
                    await self.odds_cache.update_one(
                        {"event_id": event_id, "source": "sharp_books"},
                        {"$set": odds_data},
                        upsert=True
                    )
                
                return odds_data
                
            elif response.status_code == 422:
                logger.debug(f"  [SHARP_BOOKS] No props available for {event_id}")
            else:
                logger.warning(f"  [SHARP_BOOKS] API returned {response.status_code} for {event_id}")
                        
        except Exception as e:
            logger.error(f"[ODDS_API] Sharp books fetch error for {event_id}: {e}")
        
        return {}
    
    def extract_prizepicks_props(
        self,
        odds_data: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Extract all PrizePicks props and classify correctly.
        
        Classification Rules:
        - STANDARD (no icon): Props from main markets (e.g., player_points)
        - DEMON (red icon): Alternate markets with even odds (+100)
        - GOBLIN (green icon): Alternate markets with odds ≠ +100
        
        Also tracks player order for popularity ranking.
        
        Returns:
            List of classified props
        """
        props = []
        event_id = odds_data.get("id") or odds_data.get("event_id")
        home_team = odds_data.get("home_team", "")
        away_team = odds_data.get("away_team", "")
        commence_time = odds_data.get("commence_time", "")
        
        # Track player appearance order
        player_order_counter = 0
        seen_players_in_event = set()
        
        for bookmaker in odds_data.get("bookmakers", []):
            book_key = bookmaker.get("key", "")
            
            # Only process PrizePicks data
            if book_key != "prizepicks":
                continue
            
            for market in bookmaker.get("markets", []):
                market_key = market.get("key", "")
                market_last_update = market.get("last_update", "")
                
                # Determine if alternate market
                is_alternate_market = "_alternate" in market_key
                
                for outcome in market.get("outcomes", []):
                    player_name = outcome.get("description", "")
                    direction = outcome.get("name", "")  # Over/Under
                    line = outcome.get("point")
                    price = outcome.get("price")  # American odds
                    
                    if player_name and line is not None:
                        # Track popularity order
                        if player_name not in seen_players_in_event:
                            seen_players_in_event.add(player_name)
                            player_order_counter += 1
                            
                            if player_name not in self._player_popularity:
                                self._player_popularity[player_name] = player_order_counter
                            else:
                                self._player_popularity[player_name] = min(
                                    self._player_popularity[player_name], 
                                    player_order_counter
                                )
                        
                        # Classification logic
                        if is_alternate_market:
                            is_demon = price is not None and price == DEMON_ODDS
                            is_goblin = price is not None and price != DEMON_ODDS
                            prop_type = "demon" if is_demon else "goblin"
                        else:
                            is_demon = False
                            is_goblin = False
                            prop_type = "standard"
                        
                        props.append({
                            "event_id": event_id,
                            "home_team": home_team,
                            "away_team": away_team,
                            "commence_time": commence_time,
                            "player_name": player_name,
                            "market": market_key,
                            "direction": direction,
                            "line": float(line),
                            "price": price,
                            "multiplier": outcome.get("multiplier"),
                            "bookmaker": "prizepicks",
                            "is_alternate_market": is_alternate_market,
                            "is_demon": is_demon,
                            "is_goblin": is_goblin,
                            "prop_type": prop_type,
                            "last_update": market_last_update,
                            "popularity_order": self._player_popularity.get(player_name, 999),
                            "fetched_at": datetime.now(timezone.utc).isoformat()
                        })
        
        return props
    
    def get_player_popularity(self) -> Dict[str, int]:
        """Get the player popularity cache"""
        return self._player_popularity
    
    def clear_popularity_cache(self) -> None:
        """Clear the popularity cache"""
        self._player_popularity = {}

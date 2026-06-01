"""
Sharp Edge Calculator
======================
Calculates the Sharp_Edge by comparing PrizePicks lines to Pinnacle juice.

The Logic:
- If Pinnacle has the same line but heavily juiced (e.g., -150 or worse for Over),
  this indicates smart money is betting that side.
- Sharp_Edge = Implied probability difference between Pinnacle and standard (-110)

Thresholds:
- Minimum +3.5% edge for Front Lines (primary)
- Fallback to +2.0% if < 10 players qualify

Uses OddsApiMapper for player name normalization via player_id lookups.
"""

import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone
import asyncio
import httpx
import os

from services.config.collection_names import COLL

logger = logging.getLogger(__name__)

# Configuration
ODDS_API_KEY = os.environ.get("ODDS_API_KEY")
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# Markets to compare
SHARP_MARKETS = [
    "player_points",
    "player_rebounds",
    "player_assists",
    "player_points_rebounds_assists",
]

# Edge thresholds
PRIMARY_EDGE_THRESHOLD = 3.5   # Minimum +EV for primary selection
FALLBACK_EDGE_THRESHOLD = 2.0  # Fallback if < 10 players qualify

# Pinnacle juice threshold (American odds, e.g., -150 means 60% implied)
HEAVY_JUICE_THRESHOLD = -140  # -140 or worse = heavy juice


def american_to_implied_probability(american_odds: int) -> float:
    """
    Convert American odds to implied probability percentage.
    
    Examples:
        -150 -> 60.0%
        -110 -> 52.38%
        +100 -> 50.0%
        +150 -> 40.0%
    """
    if american_odds == 0:
        return 50.0
    
    if american_odds < 0:
        # Favorite: probability = |odds| / (|odds| + 100)
        return abs(american_odds) / (abs(american_odds) + 100) * 100
    else:
        # Underdog: probability = 100 / (odds + 100)
        return 100 / (american_odds + 100) * 100


def calculate_sharp_edge(
    pinnacle_odds: int,
    prizepicks_odds: int = -110  # PrizePicks is typically standard
) -> Dict[str, Any]:
    """
    Calculate the Sharp Edge between Pinnacle and PrizePicks.
    
    Returns:
        {
            "sharp_edge": float (percentage difference),
            "pinnacle_implied": float,
            "prizepicks_implied": float,
            "is_sharp_play": bool,
            "edge_tier": str ("elite", "strong", "value", "noise")
        }
    """
    pinnacle_implied = american_to_implied_probability(pinnacle_odds)
    prizepicks_implied = american_to_implied_probability(prizepicks_odds)
    
    # Sharp edge = how much more Pinnacle thinks this side will hit
    # If Pinnacle is -150 (60%) and PrizePicks is -110 (52.4%), edge = 7.6%
    sharp_edge = pinnacle_implied - prizepicks_implied
    
    # Determine edge tier
    if sharp_edge >= 6.0:
        edge_tier = "elite"
    elif sharp_edge >= PRIMARY_EDGE_THRESHOLD:
        edge_tier = "strong"
    elif sharp_edge >= FALLBACK_EDGE_THRESHOLD:
        edge_tier = "value"
    else:
        edge_tier = "noise"
    
    return {
        "sharp_edge": round(sharp_edge, 2),
        "pinnacle_odds": pinnacle_odds,
        "pinnacle_implied": round(pinnacle_implied, 2),
        "prizepicks_implied": round(prizepicks_implied, 2),
        "is_sharp_play": sharp_edge >= FALLBACK_EDGE_THRESHOLD,
        "edge_tier": edge_tier
    }


class SharpEdgeCalculator:
    """
    Service to fetch and compare PrizePicks vs Pinnacle odds.
    Uses OddsApiMapper for reliable player name matching via player_id.
    """
    
    def __init__(self, db):
        self.db = db
        self._client: Optional[httpx.AsyncClient] = None
        self._pinnacle_cache: Dict[str, Dict] = {}  # event_id -> odds data
        self._cache_timestamp: Optional[datetime] = None
        self._cache_ttl_minutes = 10
        self._mapper = None  # OddsApiMapper instance
        self._player_id_map: Dict[str, str] = {}  # player_name.lower() -> player_id
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                limits=httpx.Limits(max_connections=5)
            )
        return self._client
    
    async def _load_mapper(self):
        """Load the OddsApiMapper for player name normalization."""
        if self._mapper is None:
            try:
                from services.odds_api_mapper import OddsApiMapper
                self._mapper = OddsApiMapper(self.db)
                await self._mapper.loadMapping()
                
                # Build a reverse lookup: player_name.lower() -> player_id
                all_mappings = self._mapper.getAllMappings()
                for odds_name, player_id in all_mappings.items():
                    self._player_id_map[odds_name.lower().strip()] = player_id
                
                logger.info(f"[SHARP] Loaded mapper with {len(self._player_id_map)} player mappings")
            except Exception as e:
                logger.error(f"[SHARP] Failed to load mapper: {e}")
    
    def _normalize_player_name(self, name: str) -> str:
        """
        Normalize player name for matching.
        Uses player_id if available from mapper, otherwise normalizes the name.
        """
        name_lower = name.lower().strip()
        
        # Try to get player_id for consistent matching
        player_id = self._player_id_map.get(name_lower)
        if player_id:
            return str(player_id)
        
        # Fallback: normalize the name
        # Remove Jr., III, II, etc.
        for suffix in [" jr.", " jr", " iii", " ii", " iv", " sr.", " sr"]:
            if name_lower.endswith(suffix):
                name_lower = name_lower[:-len(suffix)].strip()
        
        # Remove periods and extra spaces
        name_lower = name_lower.replace(".", "").replace("  ", " ")
        
        return name_lower
    
    async def close(self):
        """Close HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
    
    async def fetch_pinnacle_odds(self, event_id: str, event_info: Dict) -> Dict[str, Any]:
        """
        Fetch Pinnacle/DraftKings odds for an event.
        
        First checks the MongoDB cache (populated during sync),
        then falls back to API if needed.
        """
        # Check MongoDB cache first (populated by sync)
        cached = await self.db[COLL("odds_cache", "nba")].find_one({
            "event_id": event_id,
            "source": "sharp_books"
        })
        
        if cached and cached.get("bookmakers"):
            logger.debug(f"[SHARP] Using cached sharp_books for {event_id}")
            return cached
        
        # Check in-memory cache
        if event_id in self._pinnacle_cache:
            cache_data = self._pinnacle_cache[event_id]
            if self._cache_timestamp:
                age = (datetime.now(timezone.utc) - self._cache_timestamp).total_seconds() / 60
                if age < self._cache_ttl_minutes:
                    return cache_data
        
        # Fallback to API (for games not in cache)
        if not ODDS_API_KEY:
            logger.warning("[SHARP] No ODDS_API_KEY configured")
            return {}
        
        try:
            url = f"{ODDS_API_BASE}/sports/basketball_nba/events/{event_id}/odds"
            params = {
                "apiKey": ODDS_API_KEY,
                "regions": "us",
                "markets": ",".join(SHARP_MARKETS),
                "bookmakers": "pinnacle,draftkings",
                "oddsFormat": "american",
                "includeMultipliers": "true"
            }

            # ── Budget guard ─────────────────────────────────────────
            from services.odds_api_budget import (
                check_and_increment, current_caller, OddsApiBudgetExceeded,
            )
            try:
                check_and_increment(
                    caller=current_caller(), sport="nba",
                    endpoint="event_odds_sharp_edge")
            except OddsApiBudgetExceeded as exc:
                logger.error(f"[ODDS_BUDGET] sharp_edge_calculator blocked: {exc}")
                return {}

            client = await self._get_client()
            response = await client.get(url, params=params)
            
            if response.status_code == 200:
                data = response.json()
                self._pinnacle_cache[event_id] = data
                self._cache_timestamp = datetime.now(timezone.utc)
                
                # Count props found
                prop_count = 0
                for bm in data.get("bookmakers", []):
                    for market in bm.get("markets", []):
                        prop_count += len(market.get("outcomes", []))
                
                if prop_count > 0:
                    logger.info(f"[SHARP] Pinnacle/DK: {event_info.get('away_team', '')} @ {event_info.get('home_team', '')}: {prop_count} props")
                return data
            elif response.status_code == 422:
                logger.debug(f"[SHARP] No sharp book props for {event_id}")
                return {}
            else:
                logger.warning(f"[SHARP] Sharp books API returned {response.status_code}")
                return {}
                
        except Exception as e:
            logger.error(f"[SHARP] Error fetching sharp book odds: {e}")
            return {}
    
    def extract_pinnacle_lines(self, pinnacle_data: Dict) -> Dict[str, Dict]:
        """
        Extract Pinnacle and DraftKings lines into a lookup dict.
        Uses normalized player names (via player_id when available).
        
        Prioritizes Pinnacle lines over DraftKings when both exist.
        
        Returns:
            {
                "normalized_name|stat_type|line|direction": {
                    "odds": -150,
                    "line": 24.5,
                    "bookmaker": "pinnacle" or "draftkings",
                    ...
                }
            }
        """
        lines = {}
        
        # Process bookmakers in priority order (Pinnacle first)
        bookmakers = pinnacle_data.get("bookmakers", [])
        bookmakers_sorted = sorted(bookmakers, key=lambda b: 0 if b.get("key") == "pinnacle" else 1)
        
        for bookmaker in bookmakers_sorted:
            bm_key = bookmaker.get("key", "")
            if bm_key not in ["pinnacle", "draftkings"]:
                continue
            
            for market in bookmaker.get("markets", []):
                market_key = market.get("key", "")
                
                for outcome in market.get("outcomes", []):
                    player_name = outcome.get("description", "")
                    direction = outcome.get("name", "Over").lower()
                    line = outcome.get("point", 0)
                    odds = outcome.get("price", -110)
                    
                    # Normalize player name
                    normalized_name = self._normalize_player_name(player_name)
                    
                    # Extract stat type from market key
                    stat_type = self._market_to_stat_type(market_key)
                    
                    key = f"{normalized_name}|{stat_type}|{line}|{direction}"
                    
                    # Only add if not already present (Pinnacle takes priority)
                    if key not in lines:
                        lines[key] = {
                            "player_name": player_name,
                            "normalized_name": normalized_name,
                            "stat_type": stat_type,
                            "line": line,
                            "direction": direction,
                            "odds": odds,
                            "bookmaker": bm_key
                        }
        
        return lines
    
    def _market_to_stat_type(self, market_key: str) -> str:
        """Convert market key to stat type."""
        market_map = {
            "player_points": "PTS",
            "player_points_alternate": "PTS",
            "player_rebounds": "REB",
            "player_rebounds_alternate": "REB",
            "player_assists": "AST",
            "player_assists_alternate": "AST",
            "player_points_rebounds_assists": "PRA",
            "player_points_rebounds_assists_alternate": "PRA",
        }
        return market_map.get(market_key, "PTS")
    
    async def calculate_sharp_edges_for_props(
        self,
        props: List[Dict],
        events: List[Dict]
    ) -> Dict[str, Dict]:
        """
        Calculate Sharp Edge for a list of props.
        Uses player_id-based matching for reliability.
        
        Returns:
            {
                "player_name|stat_type|line": {
                    "sharp_edge": float,
                    "pinnacle_odds": int,
                    "edge_tier": str,
                    ...
                }
            }
        """
        # Load mapper for player name normalization
        await self._load_mapper()
        
        sharp_edges = {}
        
        # Group props by event_id
        event_map = {e.get("id"): e for e in events}
        props_by_event: Dict[str, List[Dict]] = {}
        
        for prop in props:
            event_id = prop.get("game_id") or prop.get("event_id")
            if event_id:
                if event_id not in props_by_event:
                    props_by_event[event_id] = []
                props_by_event[event_id].append(prop)
        
        logger.info(f"[SHARP] Processing {len(props)} props across {len(props_by_event)} events")
        
        # Fetch Pinnacle odds for each event
        for event_id, event_props in props_by_event.items():
            event_info = event_map.get(event_id, {})
            pinnacle_data = await self.fetch_pinnacle_odds(event_id, event_info)
            
            if not pinnacle_data:
                continue
            
            pinnacle_lines = self.extract_pinnacle_lines(pinnacle_data)
            logger.debug(f"[SHARP] Event {event_id}: {len(pinnacle_lines)} Pinnacle lines extracted")
            
            # Match props to Pinnacle lines
            for prop in event_props:
                player_name = prop.get("player_name", "")
                stat_type = prop.get("stat_type") or prop.get("stat_type_extracted") or "PTS"
                line = prop.get("line", 0)
                direction = prop.get("direction", "over").lower()
                
                # Normalize player name for matching
                normalized_name = self._normalize_player_name(player_name)
                
                # Create lookup key
                lookup_key = f"{normalized_name}|{stat_type}|{line}|{direction}"
                
                # Debug first few non-matches
                if lookup_key not in pinnacle_lines and len(sharp_edges) < 3:
                    # Show what keys ARE in pinnacle_lines for this player
                    matching_keys = [k for k in pinnacle_lines.keys() if normalized_name in k.lower()]
                    if matching_keys:
                        logger.debug(f"[SHARP] Near miss for {player_name}: prop={lookup_key}, pinnacle has: {matching_keys[:3]}")
                
                if lookup_key in pinnacle_lines:
                    pinnacle_line = pinnacle_lines[lookup_key]
                    pinnacle_odds = pinnacle_line.get("odds", -110)
                    
                    edge_result = calculate_sharp_edge(pinnacle_odds)
                    
                    # Store result with original player name
                    result_key = f"{player_name}|{stat_type}|{line}"
                    sharp_edges[result_key] = {
                        **edge_result,
                        "player_name": player_name,
                        "stat_type": stat_type,
                        "line": line
                    }
                    
                    if edge_result["is_sharp_play"]:
                        logger.info(f"[SHARP] +{edge_result['sharp_edge']:.1f}% edge: {player_name} {stat_type}@{line} (Pinnacle: {pinnacle_odds})")
        
        logger.info(f"[SHARP] Found {len(sharp_edges)} props with Pinnacle matches")
        return sharp_edges


async def find_sharp_plays(
    db,
    props: List[Dict],
    events: List[Dict],
    min_edge: float = PRIMARY_EDGE_THRESHOLD
) -> List[Dict]:
    """
    Find all props with Sharp Edge >= min_edge.
    
    Returns props sorted by sharp_edge descending.
    """
    calculator = SharpEdgeCalculator(db)
    
    try:
        sharp_edges = await calculator.calculate_sharp_edges_for_props(props, events)
        
        # Filter and attach sharp data to props
        sharp_props = []
        for prop in props:
            player_name = prop.get("player_name")
            stat_type = prop.get("stat_type") or prop.get("stat_type_extracted") or "PTS"
            line = prop.get("line", 0)
            
            key = f"{player_name}|{stat_type}|{line}"
            if key in sharp_edges:
                edge_data = sharp_edges[key]
                if edge_data.get("sharp_edge", 0) >= min_edge:
                    prop["sharp_edge_data"] = edge_data
                    prop["sharp_edge"] = edge_data["sharp_edge"]
                    sharp_props.append(prop)
        
        # Sort by sharp_edge descending
        sharp_props.sort(key=lambda x: x.get("sharp_edge", 0), reverse=True)
        
        logger.info(f"[SHARP] Found {len(sharp_props)} props with edge >= {min_edge}%")
        return sharp_props
        
    finally:
        await calculator.close()

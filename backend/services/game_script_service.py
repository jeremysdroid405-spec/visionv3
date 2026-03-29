"""
Game Script Service
====================
Fetches Vegas spreads and totals for game scripting filters.

Filters:
1. Blowout Filter: Exclude Safe Haven picks from games with spread > ±8.5
2. Shootout Filter: Prioritize War Zone picks in games with Total 225+ and spread < 6
3. DvP Top-5 Veto: Enforce no Safe Haven picks against Top-5 defenses

Data Source: The Odds API (spreads, totals markets)
"""

import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone
import asyncio
import httpx
import os

logger = logging.getLogger(__name__)

# Configuration
ODDS_API_KEY = os.environ.get("ODDS_API_KEY")
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# Game Script Thresholds
BLOWOUT_SPREAD_THRESHOLD = 8.5    # Games with spread > this are blowout risks
SHOOTOUT_TOTAL_THRESHOLD = 225.0  # High-scoring game threshold
SHOOTOUT_SPREAD_THRESHOLD = 6.0   # Tight game threshold
TOP_DEFENSE_VETO_RANK = 5         # Top 5 defenses vetoed from Safe Haven


class GameScriptService:
    """
    Service to fetch and cache Vegas spreads/totals for game scripting.
    """
    
    def __init__(self, db):
        self.db = db
        self._client: Optional[httpx.AsyncClient] = None
        self._game_scripts: Dict[str, Dict] = {}  # event_id -> game script data
        self._cache_timestamp: Optional[datetime] = None
        self._cache_ttl_minutes = 15
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                limits=httpx.Limits(max_connections=5)
            )
        return self._client
    
    async def close(self):
        """Close HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
    
    async def fetch_spreads_and_totals(self, events: List[Dict]) -> Dict[str, Dict]:
        """
        Fetch Vegas spreads and totals for all events.
        
        Returns:
            {
                "event_id": {
                    "spread": float (home team spread),
                    "total": float (O/U),
                    "home_team": str,
                    "away_team": str,
                    "is_blowout_risk": bool,
                    "is_shootout": bool
                }
            }
        """
        # Check cache freshness
        if self._cache_timestamp:
            age = (datetime.now(timezone.utc) - self._cache_timestamp).total_seconds() / 60
            if age < self._cache_ttl_minutes and self._game_scripts:
                logger.debug(f"[GAME_SCRIPT] Using cached data ({len(self._game_scripts)} games)")
                return self._game_scripts
        
        if not ODDS_API_KEY:
            logger.warning("[GAME_SCRIPT] No ODDS_API_KEY configured")
            return {}
        
        try:
            # Fetch odds with spreads and totals
            url = f"{ODDS_API_BASE}/sports/basketball_nba/odds"
            params = {
                "apiKey": ODDS_API_KEY,
                "regions": "us",
                "markets": "spreads,totals",
                "bookmakers": "draftkings,fanduel,pinnacle",  # Consensus lines
                "oddsFormat": "american"
            }
            
            client = await self._get_client()
            response = await client.get(url, params=params)
            
            if response.status_code != 200:
                logger.warning(f"[GAME_SCRIPT] API returned {response.status_code}")
                return {}
            
            data = response.json()
            
            # Process each event
            for event in data:
                event_id = event.get("id")
                if not event_id:
                    continue
                
                home_team = event.get("home_team", "")
                away_team = event.get("away_team", "")
                
                spread = None
                total = None
                
                # Extract consensus lines (use first available)
                for bookmaker in event.get("bookmakers", []):
                    for market in bookmaker.get("markets", []):
                        market_key = market.get("key", "")
                        
                        if market_key == "spreads" and spread is None:
                            # Get home team spread
                            for outcome in market.get("outcomes", []):
                                if outcome.get("name") == home_team:
                                    spread = outcome.get("point", 0)
                                    break
                        
                        elif market_key == "totals" and total is None:
                            # Get total (Over line)
                            for outcome in market.get("outcomes", []):
                                if outcome.get("name") == "Over":
                                    total = outcome.get("point", 0)
                                    break
                    
                    # Break if we have both
                    if spread is not None and total is not None:
                        break
                
                # Calculate game script indicators
                abs_spread = abs(spread) if spread is not None else 0
                is_blowout_risk = abs_spread > BLOWOUT_SPREAD_THRESHOLD
                is_shootout = (
                    total is not None and 
                    total >= SHOOTOUT_TOTAL_THRESHOLD and 
                    abs_spread < SHOOTOUT_SPREAD_THRESHOLD
                )
                
                self._game_scripts[event_id] = {
                    "event_id": event_id,
                    "home_team": home_team,
                    "away_team": away_team,
                    "spread": spread,
                    "abs_spread": abs_spread,
                    "total": total,
                    "is_blowout_risk": is_blowout_risk,
                    "is_shootout": is_shootout,
                    "fetched_at": datetime.now(timezone.utc).isoformat()
                }
                
                if is_blowout_risk:
                    logger.info(f"[GAME_SCRIPT] BLOWOUT RISK: {away_team} @ {home_team} (spread: {spread})")
                if is_shootout:
                    logger.info(f"[GAME_SCRIPT] SHOOTOUT: {away_team} @ {home_team} (total: {total}, spread: {spread})")
            
            self._cache_timestamp = datetime.now(timezone.utc)
            logger.info(f"[GAME_SCRIPT] Processed {len(self._game_scripts)} games")
            
            return self._game_scripts
            
        except Exception as e:
            logger.error(f"[GAME_SCRIPT] Error fetching spreads/totals: {e}")
            return {}
    
    def get_game_script(self, event_id: str) -> Optional[Dict]:
        """Get game script data for a specific event."""
        return self._game_scripts.get(event_id)
    
    def is_blowout_risk(self, event_id: str) -> bool:
        """Check if a game is a blowout risk."""
        script = self._game_scripts.get(event_id, {})
        return script.get("is_blowout_risk", False)
    
    def is_shootout(self, event_id: str) -> bool:
        """Check if a game is a shootout environment."""
        script = self._game_scripts.get(event_id, {})
        return script.get("is_shootout", False)
    
    def get_spread(self, event_id: str) -> Optional[float]:
        """Get the absolute spread for a game."""
        script = self._game_scripts.get(event_id, {})
        return script.get("abs_spread")
    
    def get_total(self, event_id: str) -> Optional[float]:
        """Get the total (O/U) for a game."""
        script = self._game_scripts.get(event_id, {})
        return script.get("total")


def apply_blowout_filter(props: List[Dict], game_scripts: Dict[str, Dict]) -> List[Dict]:
    """
    Filter out props from blowout risk games for Safe Haven.
    
    Args:
        props: List of candidate props
        game_scripts: Dict of event_id -> game script data
    
    Returns:
        Filtered list excluding blowout risk games
    """
    filtered = []
    excluded_count = 0
    
    for prop in props:
        event_id = prop.get("game_id") or prop.get("event_id")
        script = game_scripts.get(event_id, {})
        
        if script.get("is_blowout_risk", False):
            excluded_count += 1
            logger.debug(f"[BLOWOUT_FILTER] Excluding {prop.get('player_name')} - game is blowout risk")
            continue
        
        filtered.append(prop)
    
    if excluded_count > 0:
        logger.info(f"[BLOWOUT_FILTER] Excluded {excluded_count} props from blowout risk games")
    
    return filtered


def apply_dvp_veto(props: List[Dict], dvp_rankings: Dict[str, int]) -> List[Dict]:
    """
    Filter out props against Top-5 defenses for Safe Haven.
    
    Args:
        props: List of candidate props
        dvp_rankings: Dict of team -> DvP rank for stat type
    
    Returns:
        Filtered list excluding Top-5 defense matchups
    """
    filtered = []
    vetoed_count = 0
    
    for prop in props:
        dvp_rank = prop.get("dvp_rank")
        
        if dvp_rank is not None and dvp_rank <= TOP_DEFENSE_VETO_RANK:
            vetoed_count += 1
            logger.debug(f"[DVP_VETO] Excluding {prop.get('player_name')} - DvP rank {dvp_rank} is Top-5")
            continue
        
        filtered.append(prop)
    
    if vetoed_count > 0:
        logger.info(f"[DVP_VETO] Vetoed {vetoed_count} props against Top-5 defenses")
    
    return filtered


def apply_shootout_boost(props: List[Dict], game_scripts: Dict[str, Dict]) -> List[Dict]:
    """
    Apply Vision Score boost to props in shootout environments.
    
    Shootout = Total 225+ and spread < 6 points.
    
    Args:
        props: List of War Zone candidate props
        game_scripts: Dict of event_id -> game script data
    
    Returns:
        Props with shootout_boost flag and boosted vision_score
    """
    for prop in props:
        event_id = prop.get("game_id") or prop.get("event_id")
        script = game_scripts.get(event_id, {})
        
        if script.get("is_shootout", False):
            # Apply shootout boost to offensive props
            stat_type = (prop.get("stat_type") or prop.get("stat_type_extracted") or "").upper()
            if stat_type in ["PTS", "AST", "PRA", "FGA"]:
                prop["is_shootout"] = True
                prop["shootout_total"] = script.get("total")
                prop["shootout_spread"] = script.get("abs_spread")
                
                # Boost vision score by 5 points for shootout games
                current_score = prop.get("vision_score", 0)
                prop["vision_score"] = min(100, current_score + 5)
                prop["vision_score_breakdown"] = prop.get("vision_score_breakdown", {})
                prop["vision_score_breakdown"]["shootout_boost"] = 5
                
                logger.debug(f"[SHOOTOUT_BOOST] +5 to {prop.get('player_name')} {stat_type} (total: {script.get('total')})")
    
    return props


async def get_game_scripts(db, events: List[Dict]) -> Dict[str, Dict]:
    """
    Convenience function to fetch game scripts for events.
    """
    service = GameScriptService(db)
    try:
        return await service.fetch_spreads_and_totals(events)
    finally:
        await service.close()

"""
Universal Odds Sync Service
============================
Multi-sport odds synchronization using The Odds API.

Supports:
- NBA (basketball_nba): Points, Rebounds, Assists, PRA
- MLB (baseball_mlb): Pitcher strikeouts, walks, hits allowed; 
                      Batter hits, total bases, RBIs, runs, stolen bases

Each sport saves to its own collection:
- NBA: dg_live_props (legacy name)
- MLB: mlb_live_props
"""
import os
import logging
import asyncio
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone

import httpx
from motor.motor_asyncio import AsyncIOMotorDatabase

from config.db_config import get_collection_name, validate_sport, SPORT_CONFIG

logger = logging.getLogger(__name__)

# API Configuration
ODDS_API_KEY = os.environ.get("ODDS_API_KEY")
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# Region for DFS props
DFS_REGION = "us_dfs"
PRIZEPICKS_BOOKMAKER = "prizepicks"

# =============================================================================
# SPORT-SPECIFIC CONFIGURATION
# =============================================================================

SPORT_API_CONFIG = {
    "nba": {
        "sport_key": "basketball_nba",
        "display_name": "NBA",
        # NBA Markets - Standard props
        "markets": [
            "player_points",
            "player_rebounds",
            "player_assists",
            "player_points_rebounds_assists",  # PRA combo
            "player_points_alternate",
            "player_rebounds_alternate",
            "player_assists_alternate",
            "player_points_rebounds_assists_alternate",
        ],
        # Map Odds API market names to our stat types
        "stat_type_map": {
            "player_points": "PTS",
            "player_points_alternate": "PTS",
            "player_rebounds": "REB",
            "player_rebounds_alternate": "REB",
            "player_assists": "AST",
            "player_assists_alternate": "AST",
            "player_points_rebounds_assists": "PRA",
            "player_points_rebounds_assists_alternate": "PRA",
        }
    },
    "mlb": {
        "sport_key": "baseball_mlb",
        "display_name": "MLB",
        # MLB Markets - Pitcher and Batter props
        "markets": [
            # Pitcher props
            "pitcher_strikeouts",
            "pitcher_strikeouts_alternate",
            "pitcher_walks",
            "pitcher_walks_alternate",
            "pitcher_hits_allowed",
            "pitcher_hits_allowed_alternate",
            # Batter props
            "batter_hits",
            "batter_hits_alternate",
            "batter_total_bases",
            "batter_total_bases_alternate",
            "batter_rbis",
            "batter_rbis_alternate",
            "batter_runs_scored",
            "batter_runs_scored_alternate",
            "batter_stolen_bases",
            "batter_stolen_bases_alternate",
        ],
        # Map Odds API market names to our stat types
        "stat_type_map": {
            "pitcher_strikeouts": "Strikeouts",
            "pitcher_strikeouts_alternate": "Strikeouts",
            "pitcher_walks": "Walks",
            "pitcher_walks_alternate": "Walks",
            "pitcher_hits_allowed": "Hits Allowed",
            "pitcher_hits_allowed_alternate": "Hits Allowed",
            "batter_hits": "Hits",
            "batter_hits_alternate": "Hits",
            "batter_total_bases": "Total Bases",
            "batter_total_bases_alternate": "Total Bases",
            "batter_rbis": "RBIs",
            "batter_rbis_alternate": "RBIs",
            "batter_runs_scored": "Runs",
            "batter_runs_scored_alternate": "Runs",
            "batter_stolen_bases": "Stolen Bases",
            "batter_stolen_bases_alternate": "Stolen Bases",
        }
    }
}


class UniversalOddsSyncService:
    """
    Universal odds sync service supporting multiple sports.
    
    Fetches props from The Odds API and saves to sport-specific collections.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
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
    
    def _get_sport_config(self, sport: str) -> Dict[str, Any]:
        """Get API configuration for a sport."""
        sport = validate_sport(sport)
        if sport not in SPORT_API_CONFIG:
            raise ValueError(f"No API config for sport: {sport}")
        return SPORT_API_CONFIG[sport]
    
    async def fetch_events(self, sport: str = "nba") -> List[Dict[str, Any]]:
        """
        Fetch all events for a sport from The Odds API.
        
        Args:
            sport: Sport to fetch ('nba' or 'mlb')
            
        Returns:
            List of events with game info
        """
        config = self._get_sport_config(sport)
        sport_key = config["sport_key"]
        display_name = config["display_name"]
        
        logger.info(f"[UNIVERSAL_ODDS] Fetching {display_name} events...")
        
        try:
            url = f"{ODDS_API_BASE}/sports/{sport_key}/events"
            params = {"apiKey": ODDS_API_KEY, "dateFormat": "iso"}
            
            client = await self._get_client()
            response = await client.get(url, params=params)
            
            if response.status_code == 200:
                events = response.json()
                logger.info(f"[UNIVERSAL_ODDS] Found {len(events)} {display_name} events")
                
                for e in events[:5]:
                    logger.info(f"  • {e.get('away_team')} @ {e.get('home_team')}")
                
                return events
            else:
                logger.warning(f"[UNIVERSAL_ODDS] {display_name} events fetch returned {response.status_code}")
                return []
                
        except Exception as e:
            logger.error(f"[UNIVERSAL_ODDS] Event fetch error for {display_name}: {e}")
            return []
    
    async def fetch_event_odds(
        self,
        event_id: str,
        event_info: Dict[str, Any],
        sport: str = "nba"
    ) -> Dict[str, Any]:
        """
        Fetch PrizePicks odds for a single event.
        
        Args:
            event_id: The Odds API event ID
            event_info: Event metadata (teams, time, etc.)
            sport: Sport to fetch ('nba' or 'mlb')
            
        Returns:
            Odds data with all player props
        """
        config = self._get_sport_config(sport)
        sport_key = config["sport_key"]
        markets = ",".join(config["markets"])
        
        try:
            url = f"{ODDS_API_BASE}/sports/{sport_key}/events/{event_id}/odds"
            
            params = {
                "apiKey": ODDS_API_KEY,
                "regions": DFS_REGION,
                "markets": markets,
                "bookmakers": PRIZEPICKS_BOOKMAKER,
                "oddsFormat": "american",
                "includeMultipliers": "true"
            }
            
            client = await self._get_client()
            response = await client.get(url, params=params)
            
            if response.status_code == 200:
                odds_data = response.json()
                odds_data["event_id"] = event_id
                odds_data["sport"] = sport
                odds_data["fetched_at"] = datetime.now(timezone.utc).isoformat()
                
                # Count outcomes
                total_outcomes = 0
                for bm in odds_data.get("bookmakers", []):
                    for market in bm.get("markets", []):
                        total_outcomes += len(market.get("outcomes", []))
                
                logger.debug(
                    f"  [{config['display_name']}] {event_info.get('away_team')} @ "
                    f"{event_info.get('home_team')}: {total_outcomes} lines"
                )
                
                return odds_data
            elif response.status_code == 404:
                logger.debug(f"  [ODDS] No props available for event {event_id}")
                return {}
            else:
                logger.warning(f"  [ODDS] Event odds returned {response.status_code}")
                return {}
                
        except Exception as e:
            logger.error(f"  [ODDS] Error fetching odds for {event_id}: {e}")
            return {}
    
    def extract_props_from_odds(
        self,
        odds_data: Dict[str, Any],
        event_info: Dict[str, Any],
        sport: str = "nba"
    ) -> List[Dict[str, Any]]:
        """
        Extract individual props from odds data.
        
        Args:
            odds_data: Raw odds data from API
            event_info: Event metadata
            sport: Sport for stat type mapping
            
        Returns:
            List of normalized prop dictionaries
        """
        config = self._get_sport_config(sport)
        stat_type_map = config["stat_type_map"]
        
        props = []
        seen_keys = set()  # Deduplication
        
        for bookmaker in odds_data.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                market_key = market.get("key", "")
                stat_type = stat_type_map.get(market_key, market_key)
                
                for outcome in market.get("outcomes", []):
                    player_name = outcome.get("description", "")
                    if not player_name:
                        continue
                    
                    line = outcome.get("point")
                    if line is None:
                        continue
                    
                    # Determine over/under
                    outcome_name = outcome.get("name", "").lower()
                    recommendation = "OVER" if "over" in outcome_name else "UNDER"
                    
                    # Create deduplication key
                    dedup_key = f"{player_name}|{stat_type}|{line}|{recommendation}"
                    if dedup_key in seen_keys:
                        continue
                    seen_keys.add(dedup_key)
                    
                    # Build prop document
                    prop = {
                        "player_name": player_name,
                        "stat_type": stat_type,
                        "line": float(line),
                        "recommendation": recommendation,
                        "odds": outcome.get("price", -110),
                        "market_key": market_key,
                        "bookmaker": bookmaker.get("key", PRIZEPICKS_BOOKMAKER),
                        # Event context
                        "event_id": odds_data.get("event_id"),
                        "home_team": event_info.get("home_team"),
                        "away_team": event_info.get("away_team"),
                        "commence_time": event_info.get("commence_time"),
                        # Metadata
                        "sport": sport,
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                        "source": "prizepicks"
                    }
                    
                    # Determine player's team (heuristic based on name parsing)
                    # This will be enriched later by roster matching
                    prop["team"] = None  # Will be set during enrichment
                    
                    props.append(prop)
        
        return props
    
    async def sync_sport_props(self, sport: str = "nba") -> Dict[str, Any]:
        """
        Full sync of props for a sport.
        
        1. Fetch all events
        2. Fetch odds for each event
        3. Extract and normalize props
        4. Save to sport-specific collection
        
        Args:
            sport: Sport to sync ('nba' or 'mlb')
            
        Returns:
            Sync results summary
        """
        sport = validate_sport(sport)
        config = self._get_sport_config(sport)
        display_name = config["display_name"]
        
        sync_start = datetime.now(timezone.utc)
        
        logger.info("=" * 70)
        logger.info(f"[UNIVERSAL_ODDS] Starting {display_name} Props Sync")
        logger.info("=" * 70)
        
        results = {
            "success": True,
            "sport": sport,
            "synced_at": sync_start.isoformat(),
            "events_count": 0,
            "total_props": 0,
            "unique_players": set(),
            "stat_types": {},
            "api_calls": 0,
            "errors": []
        }
        
        try:
            # Step 1: Fetch events
            events = await self.fetch_events(sport)
            results["events_count"] = len(events)
            results["api_calls"] += 1
            
            if not events:
                logger.warning(f"[UNIVERSAL_ODDS] No {display_name} events found")
                results["success"] = False
                results["errors"].append("No events found")
                return results
            
            # Step 2: Fetch odds for each event (with rate limiting)
            all_props = []
            
            for event in events:
                event_id = event.get("id")
                if not event_id:
                    continue
                
                # Fetch odds
                odds_data = await self.fetch_event_odds(event_id, event, sport)
                results["api_calls"] += 1
                
                if odds_data:
                    # Extract props
                    props = self.extract_props_from_odds(odds_data, event, sport)
                    all_props.extend(props)
                    
                    # Track stats
                    for prop in props:
                        results["unique_players"].add(prop["player_name"])
                        stat_type = prop["stat_type"]
                        results["stat_types"][stat_type] = results["stat_types"].get(stat_type, 0) + 1
                
                # Rate limiting - The Odds API has limits
                await asyncio.sleep(0.1)
            
            results["total_props"] = len(all_props)
            results["unique_players"] = len(results["unique_players"])
            
            # Step 3: Save to sport-specific collection
            if all_props:
                collection_name = get_collection_name("live_props", sport)
                collection = self.db[collection_name]
                
                # Clear old props for today and insert new ones
                # Using upsert to prevent duplicates
                inserted = 0
                updated = 0
                
                for prop in all_props:
                    # Composite key for deduplication
                    filter_key = {
                        "player_name": prop["player_name"],
                        "stat_type": prop["stat_type"],
                        "line": prop["line"],
                        "recommendation": prop["recommendation"],
                        "event_id": prop["event_id"]
                    }
                    
                    result = await collection.update_one(
                        filter_key,
                        {"$set": prop},
                        upsert=True
                    )
                    
                    if result.upserted_id:
                        inserted += 1
                    elif result.modified_count > 0:
                        updated += 1
                
                results["inserted"] = inserted
                results["updated"] = updated
                results["collection"] = collection_name
                
                logger.info(f"[UNIVERSAL_ODDS] Saved {inserted} new, {updated} updated props to {collection_name}")
            
            # Log summary
            duration = (datetime.now(timezone.utc) - sync_start).total_seconds()
            logger.info(f"[UNIVERSAL_ODDS] {display_name} Sync Complete:")
            logger.info(f"  • Events: {results['events_count']}")
            logger.info(f"  • Props: {results['total_props']}")
            logger.info(f"  • Players: {results['unique_players']}")
            logger.info(f"  • API Calls: {results['api_calls']}")
            logger.info(f"  • Duration: {duration:.2f}s")
            logger.info(f"  • Stat Types: {results['stat_types']}")
            
            results["duration_seconds"] = round(duration, 2)
            
        except Exception as e:
            logger.error(f"[UNIVERSAL_ODDS] Sync error for {display_name}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            results["success"] = False
            results["errors"].append(str(e))
        
        finally:
            await self.close_client()
        
        return results


# Singleton instance
_universal_odds_service: Optional[UniversalOddsSyncService] = None


def get_universal_odds_service(db: AsyncIOMotorDatabase) -> UniversalOddsSyncService:
    """Get or create the universal odds sync service."""
    global _universal_odds_service
    if _universal_odds_service is None:
        _universal_odds_service = UniversalOddsSyncService(db)
    return _universal_odds_service

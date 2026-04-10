"""
Universal Odds Sync Service
============================
Multi-sport odds synchronization using The Odds API.

Supports:
- NBA (basketball_nba): Points, Rebounds, Assists, PRA
- MLB (baseball_mlb): Pitcher strikeouts, walks, hits allowed; 
                      Batter hits, total bases, RBIs, runs, stolen bases

Bookmakers Supported:
- PrizePicks (DFS)
- DraftKings (DK)
- FanDuel (FD)
- Sharp Books: Pinnacle, Circa, BetCRIS

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

# =============================================================================
# BOOKMAKER CONFIGURATION
# =============================================================================

# DFS Region (PrizePicks, Underdog, etc.)
DFS_REGION = "us_dfs"
# US sportsbooks region (DraftKings, FanDuel, etc.)
US_REGION = "us"
# EU region (Pinnacle, Bet365)
EU_REGION = "eu"

# Bookmaker categories
BOOKMAKER_CONFIG = {
    # DFS platforms
    "prizepicks": {
        "region": "us_dfs",
        "display_name": "PrizePicks",
        "is_dfs": True,
        "is_sharp": False,
        "priority": 1,  # Primary source
    },
    "underdog": {
        "region": "us_dfs",
        "display_name": "Underdog Fantasy",
        "is_dfs": True,
        "is_sharp": False,
        "priority": 2,
    },
    # US Sportsbooks
    "draftkings": {
        "region": "us",
        "display_name": "DraftKings",
        "is_dfs": False,
        "is_sharp": False,
        "priority": 3,
    },
    "fanduel": {
        "region": "us",
        "display_name": "FanDuel",
        "is_dfs": False,
        "is_sharp": False,
        "priority": 4,
    },
    "betmgm": {
        "region": "us",
        "display_name": "BetMGM",
        "is_dfs": False,
        "is_sharp": False,
        "priority": 5,
    },
    # Sharp Books (lower limits, sharper lines)
    "pinnacle": {
        "region": "eu",
        "display_name": "Pinnacle",
        "is_dfs": False,
        "is_sharp": True,
        "priority": 10,  # Sharp reference
    },
    "circa": {
        "region": "us",
        "display_name": "Circa",
        "is_dfs": False,
        "is_sharp": True,
        "priority": 11,
    },
    "betcris": {
        "region": "eu",
        "display_name": "BetCRIS",
        "is_dfs": False,
        "is_sharp": True,
        "priority": 12,
    },
}

# Default bookmakers to fetch (prioritized list)
DEFAULT_BOOKMAKERS = ["prizepicks", "draftkings", "fanduel", "pinnacle"]
SHARP_BOOKMAKERS = ["pinnacle", "circa", "betcris"]

# MLB-specific: PrizePicks + DraftKings + Pinnacle (for reference/sorting)
MLB_BOOKMAKERS = ["prizepicks", "draftkings", "pinnacle"]

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
        # MLB Markets - ALL available markets from PrizePicks (verified via API)
        "markets": [
            # Batter props - Standard
            "batter_home_runs",
            "batter_hits",
            "batter_total_bases",
            "batter_rbis",
            "batter_runs_scored",
            "batter_stolen_bases",
            "batter_walks",
            "batter_strikeouts",
            "batter_singles",
            "batter_doubles",
            "batter_triples",
            # Batter combo props
            "batter_hits_runs_rbis",
            "batter_first_home_run",
            # Batter props - Alternate lines (PrizePicks verified)
            "batter_home_runs_alternate",
            "batter_hits_alternate",
            "batter_total_bases_alternate",
            "batter_rbis_alternate",
            "batter_runs_scored_alternate",
            "batter_stolen_bases_alternate",
            "batter_walks_alternate",
            "batter_strikeouts_alternate",
            "batter_singles_alternate",
            "batter_doubles_alternate",
            "batter_triples_alternate",
            # Pitcher props - Standard
            "pitcher_strikeouts",
            "pitcher_hits_allowed",
            "pitcher_walks",
            "pitcher_earned_runs",
            "pitcher_outs",
            "pitcher_record_a_win",
            # Pitcher props - Alternate lines (PrizePicks verified)
            "pitcher_strikeouts_alternate",
            "pitcher_hits_allowed_alternate",
            "pitcher_walks_alternate",
            "pitcher_earned_runs_alternate",
            "pitcher_outs_alternate",
        ],
        # Map Odds API market names to our stat types
        "stat_type_map": {
            # Pitcher stats
            "pitcher_strikeouts": "Pitcher Strikeouts",
            "pitcher_strikeouts_alternate": "Pitcher Strikeouts",
            "pitcher_walks": "Walks Allowed",
            "pitcher_walks_alternate": "Walks Allowed",
            "pitcher_hits_allowed": "Hits Allowed",
            "pitcher_hits_allowed_alternate": "Hits Allowed",
            "pitcher_earned_runs": "Earned Runs",
            "pitcher_earned_runs_alternate": "Earned Runs",
            "pitcher_outs": "Pitcher Outs",
            "pitcher_outs_alternate": "Pitcher Outs",
            "pitcher_record_a_win": "Pitcher Win",
            # Batter stats
            "batter_home_runs": "Home Runs",
            "batter_home_runs_alternate": "Home Runs",
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
            "batter_walks": "Batter Walks",
            "batter_walks_alternate": "Batter Walks",
            "batter_strikeouts": "Batter Strikeouts",
            "batter_strikeouts_alternate": "Batter Strikeouts",
            "batter_singles": "Singles",
            "batter_singles_alternate": "Singles",
            "batter_doubles": "Doubles",
            "batter_doubles_alternate": "Doubles",
            "batter_triples": "Triples",
            "batter_triples_alternate": "Triples",
            # Combo stats
            "batter_hits_runs_rbis": "Hits+Runs+RBIs",
            "batter_hits_runs_rbis_alternate": "Hits+Runs+RBIs",
            "batter_total_bases_runs_rbis": "Total Bases+Runs+RBIs",
            "batter_total_bases_runs_rbis_alternate": "Total Bases+Runs+RBIs",
            "batter_hits_runs": "Hits+Runs",
            "batter_hits_runs_alternate": "Hits+Runs",
        },
        # PrizePicks + DK + Pinnacle for MLB (DK/Pinnacle for reference only)
        "bookmakers": ["prizepicks", "draftkings", "pinnacle"]
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
        sport: str = "nba",
        bookmakers: List[str] = None
    ) -> Dict[str, Any]:
        """
        Fetch odds for a single event from multiple bookmakers.
        
        Args:
            event_id: The Odds API event ID
            event_info: Event metadata (teams, time, etc.)
            sport: Sport to fetch ('nba' or 'mlb')
            bookmakers: List of bookmakers to fetch (default: prizepicks, draftkings, fanduel, pinnacle)
            
        Returns:
            Odds data with all player props from all bookmakers
        """
        config = self._get_sport_config(sport)
        sport_key = config["sport_key"]
        markets = ",".join(config["markets"])
        
        # Default bookmakers if not specified - use sport-specific config
        if bookmakers is None:
            if "bookmakers" in config:
                bookmakers = config["bookmakers"]
            else:
                bookmakers = DEFAULT_BOOKMAKERS
        
        # Build regions list based on bookmakers
        regions = set()
        for bm in bookmakers:
            bm_config = BOOKMAKER_CONFIG.get(bm)
            if bm_config:
                regions.add(bm_config["region"])
        
        regions_str = ",".join(regions)
        bookmakers_str = ",".join(bookmakers)
        
        try:
            url = f"{ODDS_API_BASE}/sports/{sport_key}/events/{event_id}/odds"
            
            params = {
                "apiKey": ODDS_API_KEY,
                "regions": regions_str,
                "markets": markets,
                "bookmakers": bookmakers_str,
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
                odds_data["bookmakers_requested"] = bookmakers
                
                # Count outcomes per bookmaker
                total_outcomes = 0
                bookmaker_counts = {}
                for bm in odds_data.get("bookmakers", []):
                    bm_key = bm.get("key", "unknown")
                    bm_count = 0
                    for market in bm.get("markets", []):
                        bm_count += len(market.get("outcomes", []))
                    bookmaker_counts[bm_key] = bm_count
                    total_outcomes += bm_count
                
                odds_data["outcome_counts"] = bookmaker_counts
                
                logger.debug(
                    f"  [{config['display_name']}] {event_info.get('away_team')} @ "
                    f"{event_info.get('home_team')}: {total_outcomes} lines ({bookmaker_counts})"
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
        Extract individual props from odds data with multi-bookmaker support.
        
        Groups props by player/stat and tracks lines from each bookmaker.
        Identifies sharp book lines for edge calculation.
        
        Args:
            odds_data: Raw odds data from API
            event_info: Event metadata
            sport: Sport for stat type mapping
            
        Returns:
            List of normalized prop dictionaries with multi-book data
        """
        config = self._get_sport_config(sport)
        stat_type_map = config["stat_type_map"]
        
        # First pass: Collect all props grouped by player/stat
        prop_groups: Dict[str, Dict] = {}  # key -> {lines: {bookmaker: line}, ...}
        
        for bookmaker in odds_data.get("bookmakers", []):
            bm_key = bookmaker.get("key", "unknown")
            bm_config = BOOKMAKER_CONFIG.get(bm_key, {})
            is_sharp = bm_config.get("is_sharp", False)
            is_dfs = bm_config.get("is_dfs", False)
            
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
                    
                    # Get the price/odds
                    price = outcome.get("price", -110)
                    
                    # Classify based on price: +100 = DEMON, -137 = GOBLIN
                    # Positive odds (e.g., +100) = DEMON (harder to hit, better payout)
                    # Negative odds (e.g., -137) = GOBLIN (easier to hit, worse payout)
                    is_demon = price >= 100   # +100 or better = DEMON
                    is_goblin = price < 0     # -137 or worse = GOBLIN
                    
                    # Create group key (unique per player/stat/line/recommendation)
                    group_key = f"{player_name}|{stat_type}|{line}|{recommendation}"
                    
                    if group_key not in prop_groups:
                        prop_groups[group_key] = {
                            "player_name": player_name,
                            "stat_type": stat_type,
                            "line": float(line),
                            "recommendation": recommendation,
                            "market_key": market_key,
                            "event_id": odds_data.get("event_id"),
                            "home_team": event_info.get("home_team"),
                            "away_team": event_info.get("away_team"),
                            "commence_time": event_info.get("commence_time"),
                            "sport": sport,
                            "lines": {},  # bookmaker -> line
                            "odds": {},   # bookmaker -> odds
                            "sharp_line": None,
                            "dfs_line": None,
                            "is_goblin": False,
                            "is_demon": False,
                            "is_alternate_market": "alternate" in market_key.lower(),
                        }
                    
                    # Store line by bookmaker
                    prop_groups[group_key]["lines"][bm_key] = float(line)
                    prop_groups[group_key]["odds"][bm_key] = price
                    
                    # Update goblin/demon flags based on PrizePicks price
                    if bm_key == "prizepicks":
                        prop_groups[group_key]["is_goblin"] = is_goblin
                        prop_groups[group_key]["is_demon"] = is_demon
                    
                    # Track sharp line
                    if is_sharp and prop_groups[group_key]["sharp_line"] is None:
                        prop_groups[group_key]["sharp_line"] = float(line)
                        prop_groups[group_key]["sharp_book"] = bm_key
                    
                    # Track DFS line (PrizePicks, Underdog)
                    if is_dfs and prop_groups[group_key]["dfs_line"] is None:
                        prop_groups[group_key]["dfs_line"] = float(line)
                        prop_groups[group_key]["dfs_book"] = bm_key
        
        # Second pass: Build prop documents with multi-book comparison
        props = []
        
        for group_key, group_data in prop_groups.items():
            # Use DFS line as primary if available, else use first available
            lines = group_data["lines"]
            primary_line = group_data.get("dfs_line")
            primary_book = group_data.get("dfs_book", "prizepicks")
            
            if primary_line is None and lines:
                # Fallback to first available line
                primary_book = list(lines.keys())[0]
                primary_line = lines[primary_book]
            
            if primary_line is None:
                continue
            
            # Calculate sharp edge if available
            sharp_edge = None
            sharp_line = group_data.get("sharp_line")
            if sharp_line is not None and primary_line > 0:
                # Positive edge = DFS line below sharp (value on OVER)
                # Negative edge = DFS line above sharp (value on UNDER)
                sharp_edge = round((sharp_line - primary_line) / primary_line * 100, 2)
            
            # Calculate DraftKings edge (DK line vs DFS line)
            dk_edge = None
            dk_line = lines.get("draftkings")
            if dk_line is not None and primary_line > 0:
                dk_edge = round((dk_line - primary_line) / primary_line * 100, 2)
            
            # Build prop document
            prop = {
                "player_name": group_data["player_name"],
                "stat_type": group_data["stat_type"],
                "line": group_data.get("line") or primary_line,
                "recommendation": group_data["recommendation"],
                "odds": group_data["odds"].get(primary_book, -110),
                "market_key": group_data["market_key"],
                "bookmaker": primary_book,
                # Event context
                "event_id": group_data["event_id"],
                "home_team": group_data["home_team"],
                "away_team": group_data["away_team"],
                "commence_time": group_data["commence_time"],
                # ============================================================
                # SEPARATED BOOK COLUMNS (PP, DK, Sharp/Pinnacle)
                # These are for reference and sorting - NOT displayed on frontend
                # ============================================================
                # PrizePicks (PP) - Primary display book
                "pp_line": lines.get("prizepicks"),
                "pp_odds": group_data["odds"].get("prizepicks"),
                # DraftKings (DK) - Reference book
                "dk_line": lines.get("draftkings"),
                "dk_odds": group_data["odds"].get("draftkings"),
                # Sharp/Pinnacle - Reference book for sharp line comparison
                "sharp_line": lines.get("pinnacle") or sharp_line,
                "sharp_odds": group_data["odds"].get("pinnacle"),
                "sharp_book": group_data.get("sharp_book") or ("pinnacle" if lines.get("pinnacle") else None),
                # Edge calculations
                "pp_dk_edge": round((lines.get("draftkings", 0) - lines.get("prizepicks", 0)) / lines.get("prizepicks", 1) * 100, 2) if lines.get("prizepicks") and lines.get("draftkings") else None,
                "pp_sharp_edge": round((lines.get("pinnacle", 0) - lines.get("prizepicks", 0)) / lines.get("prizepicks", 1) * 100, 2) if lines.get("prizepicks") and lines.get("pinnacle") else None,
                # ============================================================
                # Legacy multi-book data (kept for backwards compatibility)
                "all_lines": lines,
                "all_odds": group_data["odds"],
                "sharp_edge": sharp_edge,
                "dk_edge": dk_edge,
                "dfs_line": group_data.get("dfs_line"),
                "dfs_book": group_data.get("dfs_book"),
                # PrizePicks goblin/demon flags (based on price: +100=demon, negative=goblin)
                "is_goblin": group_data.get("is_goblin", False),
                "is_demon": group_data.get("is_demon", False),
                "is_alternate_market": group_data.get("is_alternate_market", False),
                # Metadata
                "sport": sport,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "source": "prizepicks" if lines.get("prizepicks") else primary_book,  # Always mark as PP source if PP line exists
                "bookmakers_available": list(lines.keys()),
                "team": None  # Will be set during enrichment
            }
            
            props.append(prop)
        
        return props
    
    async def sync_sport_props(
        self, 
        sport: str = "nba",
        bookmakers: List[str] = None,
        include_sharp: bool = True
    ) -> Dict[str, Any]:
        """
        Full sync of props for a sport from multiple bookmakers.
        
        1. Fetch all events
        2. Fetch odds for each event from all specified bookmakers
        3. Extract and normalize props with multi-book comparison
        4. Save to sport-specific collection
        
        Args:
            sport: Sport to sync ('nba' or 'mlb')
            bookmakers: List of bookmakers (default: prizepicks, draftkings, fanduel, pinnacle)
            include_sharp: Include sharp books for edge calculation
            
        Returns:
            Sync results summary
        """
        sport = validate_sport(sport)
        config = self._get_sport_config(sport)
        display_name = config["display_name"]
        
        # Build bookmaker list - use sport-specific config if available
        if bookmakers is None:
            # Check for sport-specific bookmaker config
            if "bookmakers" in config:
                bookmakers = config["bookmakers"].copy()
                # MLB uses ONLY PrizePicks, no sharp books
                include_sharp = False
            else:
                bookmakers = DEFAULT_BOOKMAKERS.copy()
        
        # Add sharp books if requested (not for MLB)
        if include_sharp:
            for sharp in SHARP_BOOKMAKERS:
                if sharp not in bookmakers:
                    bookmakers.append(sharp)
        
        sync_start = datetime.now(timezone.utc)
        
        logger.info("=" * 70)
        logger.info(f"[UNIVERSAL_ODDS] Starting {display_name} Props Sync")
        logger.info(f"[UNIVERSAL_ODDS] Bookmakers: {bookmakers}")
        logger.info("=" * 70)
        
        results = {
            "success": True,
            "sport": sport,
            "synced_at": sync_start.isoformat(),
            "bookmakers_requested": bookmakers,
            "events_count": 0,
            "total_props": 0,
            "unique_players": set(),
            "stat_types": {},
            "bookmaker_counts": {},
            "props_with_sharp_edge": 0,
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
                
                # Fetch odds from all bookmakers
                odds_data = await self.fetch_event_odds(event_id, event, sport, bookmakers)
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
                        
                        # Track bookmaker availability
                        for bm in prop.get("bookmakers_available", []):
                            results["bookmaker_counts"][bm] = results["bookmaker_counts"].get(bm, 0) + 1
                        
                        # Track props with sharp edge
                        if prop.get("sharp_edge") is not None:
                            results["props_with_sharp_edge"] += 1
                
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

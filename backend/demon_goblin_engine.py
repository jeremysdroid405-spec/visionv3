"""
Demon & Goblin Analytics Engine v3.0
=====================================

PrizePicks-Specific System for NBA Player Props

API Configuration:
- Region: us_dfs (Daily Fantasy Sports - includes PrizePicks)
- Bookmaker: prizepicks
- Markets: player_*_alternate (PrizePicks alternate lines)

Classification (PrizePicks Native):
- Goblin (Green): Default odds lines - easier, high-probability props
- Demon (Red): Even odds (+100) lines - harder, boosted props

Hybrid Caching Strategy:
1. STATIC SHELL (24h cache): Player metadata, teams, positions, historical stats
2. DYNAMIC PULSE (60s cache): Betting lines only (price, point, demon/goblin tags)

Triple-Pillar Integration:
1. The Odds API (us_dfs/prizepicks) - All PrizePicks lines
2. BallDontLie API - Player stats for hit rate calculation
3. Tank01 API - Injury reports and player news
"""

import httpx
import logging
import os
import asyncio
import random
from datetime import datetime, timezone, timedelta, time
from typing import Optional, Dict, List, Any, Set
from thefuzz import fuzz
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

# ==================== EXPONENTIAL BACKOFF CONFIG ====================
MAX_RETRIES = 4
BASE_DELAY = 1.0  # seconds
MAX_DELAY = 16.0  # seconds

# ==================== API CONFIGURATION ====================

ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "e1ae76ab21c34ee88ed552cffb4449fd")
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

BDL_API_KEY = os.environ.get("BDL_API_KEY", "ad5544be-9969-434b-9389-2b7cf658c8e0")
BDL_BASE_URL = "https://api.balldontlie.io/v1"

TANK01_API_KEY = os.environ.get("TANK01_API_KEY", "402edbcac6mshd04997e7ca01d17p1879eajsn65ab176cdb1e")
TANK01_BASE = "https://tank01-nba-live-in-game-real-time-statistics-nba.p.rapidapi.com"
TANK01_CACHE_TTL = timedelta(hours=4)  # Cache Tank01 data for 4 hours

CURRENT_SEASON = "2025"  # 2025-26 NBA Season

# ==================== CACHE TTL CONFIGURATION ====================
STATIC_CACHE_TTL = timedelta(hours=24)  # Player metadata, stats - refresh at 4 AM
DYNAMIC_CACHE_TTL = timedelta(seconds=60)  # Betting lines only - live data
STATS_CACHE_TTL = timedelta(hours=4)  # BDL stats cache

# PrizePicks-Specific Configuration
PRIZEPICKS_REGION = "us_dfs"  # Daily Fantasy Sports region - REQUIRED for PrizePicks
PRIZEPICKS_BOOKMAKER = "prizepicks"

# PrizePicks Alternate Markets - These contain Demons and Goblins
PRIZEPICKS_ALTERNATE_MARKETS = [
    "player_points_alternate",
    "player_rebounds_alternate", 
    "player_assists_alternate",
    "player_threes_alternate",
    "player_blocks_alternate",
    "player_steals_alternate",
    "player_turnovers_alternate",
    "player_points_rebounds_alternate",
    "player_points_assists_alternate",
    "player_rebounds_assists_alternate",
    "player_points_rebounds_assists_alternate",
]

# Standard markets - These are "Standard" lines (no Demon/Goblin icon)
PRIZEPICKS_STANDARD_MARKETS = [
    "player_points",
    "player_rebounds",
    "player_assists",
    "player_threes",
    "player_blocks",
    "player_steals",
    "player_turnovers",
    "player_points_rebounds",
    "player_points_assists",
    "player_rebounds_assists",
    "player_points_rebounds_assists",
]

# Combined markets for API call
PRIZEPICKS_ALL_MARKETS = ",".join(PRIZEPICKS_ALTERNATE_MARKETS + PRIZEPICKS_STANDARD_MARKETS)

# Demon/Goblin Classification (PrizePicks Native)
# 
# CLASSIFICATION RULES:
# 1. STANDARD (no icon): Props from MAIN markets (e.g., player_points, player_rebounds)
# 2. DEMON (red icon): Props from ALTERNATE markets with EVEN odds (+100)
# 3. GOBLIN (green icon): Props from ALTERNATE markets with any other odds (e.g., -119, -137)
#
DEMON_ODDS = 100  # Even odds = Demon (only applies to alternate markets)

# Hit rate threshold for Goblin warning
GOBLIN_HIT_RATE_WARNING = 0.90  # 90% hit rate

INJURY_KEYWORDS = [
    "injury", "injured", "out", "questionable", "doubtful", "probable",
    "day-to-day", "GTD", "game time decision", "load management", "rest",
    "ankle", "knee", "hamstring", "back", "shoulder", "concussion", 
    "illness", "personal", "sore", "sprain", "strain"
]

# ==================== NBA PLAYER ID MAPPING ====================
# NBA CDN headshot URL format: https://cdn.nba.com/headshots/nba/latest/1040x760/{nba_id}.png
# These IDs are from the official NBA stats API

NBA_PLAYER_IDS = {
    # Superstars
    "Shai Gilgeous-Alexander": 1628983,
    "Giannis Antetokounmpo": 203507,
    "Luka Doncic": 1629029,
    "Nikola Jokic": 203999,
    "Joel Embiid": 203954,
    "LeBron James": 2544,
    "Stephen Curry": 201939,
    "Kevin Durant": 201142,
    "Jayson Tatum": 1628369,
    "Anthony Davis": 203076,
    "Damian Lillard": 203081,
    "Devin Booker": 1626164,
    "Anthony Edwards": 1630162,
    "Ja Morant": 1629630,
    "Donovan Mitchell": 1628378,
    "Trae Young": 1629027,
    "Kyrie Irving": 202681,
    "Jimmy Butler": 202710,
    "Paul George": 202331,
    "Kawhi Leonard": 202695,
    "Zion Williamson": 1629627,
    "Jaylen Brown": 1627759,
    "Domantas Sabonis": 1627734,
    "De'Aaron Fox": 1628368,
    "LaMelo Ball": 1630163,
    "Karl-Anthony Towns": 1626157,
    "Bam Adebayo": 1628389,
    "Cade Cunningham": 1630595,
    "Paolo Banchero": 1631094,
    "Victor Wembanyama": 1641705,
    "Tyrese Haliburton": 1630169,
    "Tyrese Maxey": 1630178,
    "Jalen Brunson": 1628973,
    "Scottie Barnes": 1630567,
    "Franz Wagner": 1630532,
    "Alperen Sengun": 1630578,
    "Evan Mobley": 1630596,
    "Desmond Bane": 1630217,
    "Anfernee Simons": 1629014,
    "Mikal Bridges": 1628969,
    "OG Anunoby": 1628384,
    "Tyler Herro": 1629639,
    "Jaren Jackson Jr.": 1628991,
    "DeMar DeRozan": 201942,
    "Bradley Beal": 203078,
    "Zach LaVine": 203897,
    "Julius Randle": 203944,
    "Lauri Markkanen": 1628374,
    "Dejounte Murray": 1627749,
    "Fred VanVleet": 1627832,
    "Pascal Siakam": 1627783,
    "Khris Middleton": 203114,
    "Brandon Ingram": 1627742,
    "CJ McCollum": 203468,
    "Derrick White": 1628401,
    "Jrue Holiday": 201950,
    "Draymond Green": 203110,
    "Chris Paul": 101108,
    "Russell Westbrook": 201566,
    "James Harden": 201935,
    "Klay Thompson": 202691,
    "Andrew Wiggins": 203952,
    "Austin Reaves": 1630559,
    "Jalen Williams": 1631114,
    "Chet Holmgren": 1631096,
    "Jamal Murray": 1627750,
    "Michael Porter Jr.": 1629008,
    "Aaron Gordon": 203932,
    "Myles Turner": 1626167,
    "Brook Lopez": 201572,
    "Rudy Gobert": 203497,
    "Clint Capela": 203991,
    "Nikola Vucevic": 202696,
    "Jonas Valanciunas": 202685,
    "Deandre Ayton": 1629028,
    "Jarrett Allen": 1628386,
    "Onyeka Okongwu": 1630168,
    "Mark Williams": 1631109,
    "Walker Kessler": 1631117,
    "Jalen Suggs": 1630591,
    "Tre Mann": 1630544,
    "Cam Thomas": 1630560,
    "Immanuel Quickley": 1630193,
    "Coby White": 1629632,
    "Collin Sexton": 1629012,
    "Keldon Johnson": 1629640,
    "Herbert Jones": 1630546,
    "Josh Giddey": 1630581,
    "Keegan Murray": 1631099,
    "Bennedict Mathurin": 1631097,
    "Jaden Ivey": 1631093,
    "Shaedon Sharpe": 1631101,
    "Jabari Smith Jr.": 1631095,
    "Tari Eason": 1631106,
    "Dyson Daniels": 1631098,
    "Jeremy Sochan": 1631110,
    "Jalen Duren": 1631105,
    "AJ Griffin": 1631100,
    "Malaki Branham": 1631107,
    "Ochai Agbaji": 1631104,
    "Johnny Davis": 1631102,
    "MarJon Beauchamp": 1631173,
    "Nikola Jovic": 1631108,
    "Peyton Watson": 1631213,
    "Cooper Flagg": 1642355,
    "Dylan Harper": 1642356,
    "Ace Bailey": 1642357,
    "Grayson Allen": 1628960,
    "Collin Gillespie": 1631208,
    "Jalen Johnson": 1630552,
    "Cam Spencer": 1641734,
    "Danny Wolf": 1642358,
}

def get_nba_player_id(player_name: str) -> Optional[int]:
    """Get NBA player ID from static mapping or return None"""
    return NBA_PLAYER_IDS.get(player_name)


# ==================== EXPONENTIAL BACKOFF HELPER ====================

async def fetch_with_backoff(url: str, headers: Dict, params: Dict = None, max_retries: int = MAX_RETRIES) -> Optional[Dict]:
    """
    Fetch with exponential backoff for rate-limited APIs
    
    Retry delays: 1s -> 2s -> 4s -> 8s (with jitter)
    """
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers, params=params, timeout=30.0)
                
                if response.status_code == 200:
                    return response.json()
                
                elif response.status_code == 429:
                    # Rate limited - calculate backoff delay
                    delay = min(BASE_DELAY * (2 ** attempt), MAX_DELAY)
                    jitter = random.uniform(0, delay * 0.1)  # Add 0-10% jitter
                    total_delay = delay + jitter
                    
                    logger.warning(f"[BACKOFF] Rate limited. Attempt {attempt + 1}/{max_retries}. Waiting {total_delay:.1f}s")
                    await asyncio.sleep(total_delay)
                    continue
                
                elif response.status_code >= 500:
                    # Server error - retry with backoff
                    delay = min(BASE_DELAY * (2 ** attempt), MAX_DELAY)
                    logger.warning(f"[BACKOFF] Server error {response.status_code}. Retrying in {delay:.1f}s")
                    await asyncio.sleep(delay)
                    continue
                
                else:
                    logger.error(f"[BACKOFF] Request failed with status {response.status_code}")
                    return None
                    
        except httpx.TimeoutException:
            delay = min(BASE_DELAY * (2 ** attempt), MAX_DELAY)
            logger.warning(f"[BACKOFF] Timeout. Attempt {attempt + 1}/{max_retries}. Retrying in {delay:.1f}s")
            await asyncio.sleep(delay)
        except Exception as e:
            logger.error(f"[BACKOFF] Request error: {e}")
            return None
    
    logger.error(f"[BACKOFF] Max retries ({max_retries}) exceeded for {url}")
    return None


class DemonGoblinEngine:
    """
    The Demon & Goblin Analytics Engine - PrizePicks Edition
    
    WAREHOUSE MODEL (MongoDB):
    - LIVE_PROPS: All props stored with deduplication (synced via SyncBoard)
    - DEMON_RADAR: Top 10 pre-calculated picks flagged as is_radar_pick
    - Zero API calls from frontend - everything reads from MongoDB
    
    Classification (PrizePicks Native):
    - Standard (No Icon): Main market props
    - Demons (Red): Alternate market + Even odds (+100)
    - Goblins (Green): Alternate market + Non-even odds
    
    Features:
    - Demon Radar: Pre-calculated top 10 picks based on Hit Rate + Line Gap
    - Trending 10: Most popular players based on API order
    - Player-First Hierarchy: All props organized by player
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.events_cache = db.dg_events_cache
        self.odds_cache = db.dg_odds_cache
        self.player_data = db.dg_player_data
        self.stats_cache = db.dg_stats_cache
        self.sync_log = db.dg_sync_log
        self.trending_cache = db.dg_trending
        self.line_history = db.dg_line_history
        
        # WAREHOUSE MODEL COLLECTIONS
        self.live_props = db.dg_live_props  # Master props collection (deduplicated)
        self.radar_picks = db.dg_radar_picks  # Demon Radar top 10 picks
        self.cached_board = db.dg_cached_board  # Full cached board for frontend
        
        # Legacy caching collections
        self.static_shell_cache = db.dg_static_shell
        self.dynamic_lines_cache = db.dg_dynamic_lines
        self.tank01_cache = db.dg_tank01_cache
        
        # In-memory caches
        self._player_name_map: Dict[str, Any] = {}
        self._injury_data: Dict[str, Any] = {}
        self._news_data: List[Dict] = []
        self._last_sync: Optional[datetime] = None
        self._last_lines_fetch: Optional[datetime] = None
        self._current_date: Optional[str] = None
        self._player_popularity: Dict[str, int] = {}
    
    def get_current_date(self) -> str:
        """Auto-derive today's date from system clock"""
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    # ==================== WAREHOUSE MODEL: SINGLE BATCH SYNC ====================
    
    async def sync_odds_to_mongo(self) -> Dict[str, Any]:
        """
        THE ONLY API CALL - Single batch fetch to MongoDB
        
        This function:
        1. Makes ONE call to get all NBA events
        2. Makes ONE call per event to get PrizePicks odds
        3. Stores EVERYTHING in dg_live_props collection
        4. Uses composite key for deduplication
        5. Adds synced_at timestamp
        
        Frontend reads ONLY from MongoDB after this.
        """
        sync_start = datetime.now(timezone.utc)
        self._current_date = self.get_current_date()
        
        logger.info("=" * 70)
        logger.info("[SYNC_ODDS_TO_MONGO] Starting single batch sync...")
        logger.info(f"[SYNC_ODDS_TO_MONGO] Date: {self._current_date}")
        logger.info("=" * 70)
        
        results = {
            "success": True,
            "synced_at": sync_start.isoformat(),
            "events_count": 0,
            "total_props": 0,
            "unique_players": 0,
            "standard_count": 0,
            "demons_count": 0,
            "goblins_count": 0,
            "api_calls_made": 0,
            "errors": []
        }
        
        try:
            # Step 1: Fetch events (1 API call)
            events = await self.fetch_todays_events()
            results["events_count"] = len(events)
            results["api_calls_made"] += 1
            
            if not events:
                logger.warning("[SYNC_ODDS_TO_MONGO] No events found")
                results["success"] = False
                results["errors"].append("No NBA events found")
                return results
            
            # Step 2: Fetch odds for each event (1 API call per event)
            all_props = []
            seen_players = set()
            
            for event in events:
                event_id = event.get("id")
                if not event_id:
                    continue
                
                odds_data = await self.fetch_prizepicks_odds(event_id, event)
                results["api_calls_made"] += 1
                
                if odds_data:
                    props = self.extract_prizepicks_props(odds_data)
                    all_props.extend(props)
                    
                    for prop in props:
                        seen_players.add(prop.get("player_name"))
                
                # Small delay to avoid rate limiting
                await asyncio.sleep(0.3)
            
            results["unique_players"] = len(seen_players)
            
            # Step 3: Enrich props with BallDontLie hit rates
            logger.info(f"[SYNC_ODDS_TO_MONGO] Enriching {len(seen_players)} players with BallDontLie stats...")
            enriched_props = await self._enrich_props_with_stats(all_props, list(seen_players))
            results["stats_enriched"] = len([p for p in enriched_props if p.get("hit_rates")])
            
            # Step 4: Deduplicate and store in MongoDB
            if enriched_props:
                # Clear old data
                await self.live_props.delete_many({})
                
                # Deduplicate using composite key
                deduplicated = {}
                for prop in enriched_props:
                    # Composite key: player_name + market + line + direction
                    key = f"{prop['player_name']}|{prop['market']}|{prop['line']}|{prop['direction']}"
                    
                    # Add synced_at timestamp
                    prop["synced_at"] = sync_start.isoformat()
                    prop["_composite_key"] = key
                    
                    # Keep latest version
                    deduplicated[key] = prop
                
                # Insert deduplicated props (without _id to avoid serialization issues)
                props_list = list(deduplicated.values())
                for prop in props_list:
                    prop.pop("_id", None)  # Remove any existing _id
                    
                if props_list:
                    await self.live_props.insert_many(props_list)
                
                results["total_props"] = len(props_list)
                results["standard_count"] = sum(1 for p in props_list if p.get("prop_type") == "standard")
                results["demons_count"] = sum(1 for p in props_list if p.get("is_demon"))
                results["goblins_count"] = sum(1 for p in props_list if p.get("is_goblin"))
                
                logger.info(f"[SYNC_ODDS_TO_MONGO] Stored {len(props_list)} deduplicated props")
            
            # Step 5: Build cached board for frontend (grouped by player)
            await self._build_cached_board(props_list, sync_start)
            
        except Exception as e:
            logger.error(f"[SYNC_ODDS_TO_MONGO] Error: {e}")
            results["success"] = False
            results["errors"].append(str(e))
        
        duration = (datetime.now(timezone.utc) - sync_start).total_seconds()
        results["duration_seconds"] = duration
        
        logger.info("=" * 70)
        logger.info(f"[SYNC_ODDS_TO_MONGO] COMPLETE")
        logger.info(f"  Duration: {duration:.1f}s")
        logger.info(f"  API Calls Made: {results['api_calls_made']}")
        logger.info(f"  Props Stored: {results['total_props']}")
        logger.info(f"  Players: {results['unique_players']}")
        logger.info(f"  Standard: {results['standard_count']} | Demons: {results['demons_count']} | Goblins: {results['goblins_count']}")
        logger.info("=" * 70)
        
        return results
    
    async def _enrich_props_with_stats(self, props: List[Dict], player_names: List[str]) -> List[Dict]:
        """
        Enrich props with BallDontLie hit rates.
        
        Calculates:
        - L5: Last 5 games hit rate
        - L10: Last 10 games hit rate
        - Season: Full season hit rate
        
        This is used by the Demon Radar for accurate probability calculations.
        """
        logger.info(f"[STATS ENRICHMENT] Starting enrichment for {len(player_names)} players...")
        
        # Cache player stats to avoid duplicate API calls
        player_stats_cache = {}
        enriched_count = 0
        
        # Batch process players (limit concurrent requests)
        batch_size = 10
        for i in range(0, len(player_names), batch_size):
            batch = player_names[i:i+batch_size]
            
            for player_name in batch:
                if player_name in player_stats_cache:
                    continue
                
                try:
                    # Fetch player stats from BallDontLie
                    stats = await self._fetch_player_season_stats(player_name)
                    if stats:
                        player_stats_cache[player_name] = stats
                        enriched_count += 1
                except Exception as e:
                    logger.debug(f"[STATS] Error fetching stats for {player_name}: {e}")
                
                # Rate limiting
                await asyncio.sleep(0.1)
            
            # Log progress
            if i % 50 == 0 and i > 0:
                logger.info(f"[STATS ENRICHMENT] Progress: {i}/{len(player_names)} players")
        
        logger.info(f"[STATS ENRICHMENT] Fetched stats for {enriched_count}/{len(player_names)} players")
        
        # Enrich props with hit rates
        for prop in props:
            player_name = prop.get("player_name")
            player_stats = player_stats_cache.get(player_name, {})
            
            if not player_stats:
                continue
            
            # Extract stat type from market
            stat_type = self._extract_stat_type(prop.get("market", ""))
            line_value = prop.get("line", 0)
            
            if not stat_type or line_value <= 0:
                continue
            
            # Calculate hit rates for this line
            hit_rates = self._calculate_hit_rates(player_stats, stat_type, line_value)
            
            if hit_rates:
                prop["hit_rates"] = hit_rates
        
        return props
    
    async def _fetch_player_season_stats(self, player_name: str) -> Dict[str, Any]:
        """
        Fetch a player's season stats from BallDontLie API.
        Returns game-by-game stats for hit rate calculation.
        """
        try:
            # First, find the player ID
            player_id = await self._get_bdl_player_id(player_name)
            if not player_id:
                return {}
            
            # Fetch season stats
            url = f"{BDL_BASE_URL}/stats"
            params = {
                "player_ids[]": player_id,
                "seasons[]": CURRENT_SEASON,
                "per_page": 100
            }
            headers = {"Authorization": BDL_API_KEY}
            
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, headers=headers, timeout=15.0)
                
                if response.status_code == 200:
                    data = response.json()
                    games = data.get("data", [])
                    
                    if games:
                        return {
                            "player_name": player_name,
                            "player_id": player_id,
                            "games": games,
                            "total_games": len(games)
                        }
        except Exception as e:
            logger.debug(f"[BDL] Error fetching stats for {player_name}: {e}")
        
        return {}
    
    async def _get_bdl_player_id(self, player_name: str) -> Optional[int]:
        """Get BallDontLie player ID from name (with caching)"""
        # Check cache first
        if player_name in self._player_name_map:
            return self._player_name_map[player_name].get("id")
        
        try:
            url = f"{BDL_BASE_URL}/players"
            # Split name for better search
            name_parts = player_name.split()
            search_term = name_parts[-1] if len(name_parts) > 1 else player_name
            
            params = {"search": search_term, "per_page": 25}
            headers = {"Authorization": BDL_API_KEY}
            
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, headers=headers, timeout=10.0)
                
                if response.status_code == 200:
                    data = response.json()
                    players = data.get("data", [])
                    
                    # Find best match
                    for player in players:
                        full_name = f"{player.get('first_name', '')} {player.get('last_name', '')}"
                        if fuzz.ratio(player_name.lower(), full_name.lower()) > 85:
                            self._player_name_map[player_name] = player
                            return player.get("id")
        except Exception as e:
            logger.debug(f"[BDL] Error searching for {player_name}: {e}")
        
        return None
    
    def _calculate_hit_rates(self, player_stats: Dict, stat_type: str, line_value: float) -> Dict[str, Any]:
        """
        Calculate hit rates for a specific line.
        
        Returns:
        - l5: Last 5 games hit rate
        - l10: Last 10 games hit rate  
        - season: Full season hit rate
        """
        games = player_stats.get("games", [])
        if not games:
            return {}
        
        # Map stat type to BallDontLie field
        stat_field_map = {
            "PTS": "pts",
            "REB": "reb",
            "AST": "ast",
            "3PM": "fg3m",
            "BLK": "blk",
            "STL": "stl",
            "TO": "turnover",
            "P+R": ["pts", "reb"],
            "P+A": ["pts", "ast"],
            "R+A": ["reb", "ast"],
            "PRA": ["pts", "reb", "ast"]
        }
        
        fields = stat_field_map.get(stat_type)
        if not fields:
            return {}
        
        # Sort games by date (most recent first)
        sorted_games = sorted(games, key=lambda g: g.get("game", {}).get("date", ""), reverse=True)
        
        def get_stat_value(game):
            if isinstance(fields, list):
                return sum(game.get(f, 0) or 0 for f in fields)
            return game.get(fields, 0) or 0
        
        def calc_hit_rate(game_list):
            if not game_list:
                return {"hit_rate": 0, "games_over": 0, "total_games": 0, "avg": 0}
            
            values = [get_stat_value(g) for g in game_list]
            games_over = sum(1 for v in values if v >= line_value)
            avg = sum(values) / len(values) if values else 0
            
            return {
                "hit_rate": games_over / len(game_list) if game_list else 0,
                "games_over": games_over,
                "total_games": len(game_list),
                "avg": round(avg, 1)
            }
        
        return {
            "l5": calc_hit_rate(sorted_games[:5]),
            "l10": calc_hit_rate(sorted_games[:10]),
            "season": calc_hit_rate(sorted_games)
        }
    
    async def _build_cached_board(self, props: List[Dict], sync_time: datetime):
        """
        Build the cached board for frontend consumption.
        Groups props by player, adds nba_id, stores in dg_cached_board.
        """
        if not props:
            return
        
        # Group by player
        players_dict = {}
        for prop in props:
            player_name = prop.get("player_name", "Unknown")
            
            if player_name not in players_dict:
                nba_id = get_nba_player_id(player_name)
                players_dict[player_name] = {
                    "player_name": player_name,
                    "team": prop.get("home_team", "") or prop.get("away_team", ""),
                    "nba_id": nba_id,
                    "props": [],
                    "demons": [],
                    "goblins": [],
                    "standard": [],
                    "demons_count": 0,
                    "goblins_count": 0,
                    "standard_count": 0,
                    "synced_at": sync_time.isoformat()
                }
            
            players_dict[player_name]["props"].append(prop)
            
            if prop.get("is_demon"):
                players_dict[player_name]["demons"].append(prop)
                players_dict[player_name]["demons_count"] += 1
            elif prop.get("is_goblin"):
                players_dict[player_name]["goblins"].append(prop)
                players_dict[player_name]["goblins_count"] += 1
            else:
                players_dict[player_name]["standard"].append(prop)
                players_dict[player_name]["standard_count"] += 1
        
        # Store in cached_board collection
        await self.cached_board.delete_many({})
        
        # Sort players by prop count (most props first)
        sorted_players = sorted(
            players_dict.values(),
            key=lambda x: len(x["props"]),
            reverse=True
        )
        
        # Add ranking
        for idx, player in enumerate(sorted_players):
            player["rank"] = idx + 1
            player["popularity_order"] = idx + 1
        
        if sorted_players:
            await self.cached_board.insert_many(sorted_players)
        
        # Store sync metadata
        await self.sync_log.update_one(
            {"type": "cached_board"},
            {"$set": {
                "type": "cached_board",
                "synced_at": sync_time.isoformat(),
                "players_count": len(sorted_players),
                "total_props": sum(len(p["props"]) for p in sorted_players)
            }},
            upsert=True
        )
        
        logger.info(f"[CACHED_BOARD] Built board with {len(sorted_players)} players")
        
        # Build Demon Radar (Top 10 picks)
        await self._build_demon_radar(players_dict, sync_time)
    
    async def _build_demon_radar(self, players_dict: Dict[str, Dict], sync_time: datetime):
        """
        THE INTRICATE DEMON RADAR ALGORITHM
        
        Scoring Formula:
        1. Hit Probability (P) = (H10 × 0.6) + (H5 × 0.4)
           - If hit rates unavailable, estimate P based on line gap
        2. Line Gap (G) = (Demon_Value - Standard_Value) / Standard_Value  
        3. Final Score = P - (G × 100)
        
        Logic Guard: Only include if P >= 60%
        """
        logger.info("[DEMON RADAR] Calculating top 10 picks...")
        
        radar_picks = []
        
        for player_name, player_data in players_dict.items():
            demons = player_data.get("demons", [])
            standard = player_data.get("standard", [])
            
            if not demons:
                continue
            
            # Build a map of standard lines by stat type
            standard_map = {}
            for std_prop in standard:
                market = std_prop.get("market", "")
                stat_type = self._extract_stat_type(market)
                if stat_type:
                    key = f"{stat_type}_{std_prop.get('direction', '')}"
                    if key not in standard_map:
                        standard_map[key] = std_prop
            
            # Score each demon
            for demon in demons:
                demon_market = demon.get("market", "")
                demon_stat = self._extract_stat_type(demon_market)
                demon_line = demon.get("line", 0)
                demon_direction = demon.get("direction", "")
                
                if not demon_stat or demon_line <= 0:
                    continue
                
                # Find corresponding standard line
                std_key = f"{demon_stat}_{demon_direction}"
                std_prop = standard_map.get(std_key)
                
                # If no standard line, use a reference gap
                if std_prop:
                    std_line = std_prop.get("line", 0)
                else:
                    # No standard line - skip or estimate
                    # For now, use the demon line as a rough estimate
                    std_line = demon_line * 0.85  # Assume demon is ~15% above standard
                
                if std_line <= 0:
                    continue
                
                # Get hit rates from BallDontLie stats if available
                hit_rates = demon.get("hit_rates", {})
                h10 = hit_rates.get("l10", {}).get("hit_rate", 0)
                h5 = hit_rates.get("l5", {}).get("hit_rate", 0)
                
                # Calculate Line Gap (G)
                # G = (Demon_Value - Standard_Value) / Standard_Value
                G = (demon_line - std_line) / std_line if std_line > 0 else 0
                
                # If no hit rates, estimate P based on line gap
                # Closer gap = higher probability
                if h10 == 0 and h5 == 0:
                    # Estimate: P decreases as gap increases
                    # Gap of 0% = 80% P, Gap of 20% = 60% P, Gap of 50% = 40% P
                    estimated_P = max(0.40, 0.80 - (G * 1.0))  # Linear decay
                    h10 = estimated_P
                    h5 = estimated_P
                
                # Calculate Hit Probability (P)
                # P = (H10 × 0.6) + (H5 × 0.4)
                P = (h10 * 0.6) + (h5 * 0.4)
                
                # Logic Guard: Only include if P >= 60%
                if P < 0.60:
                    continue
                
                # Final Radar Score = P - (G × 100)
                radar_score = P - (G * 100)
                
                # Calculate gap difference for UI
                gap_diff = demon_line - std_line
                gap_pct = G * 100
                
                radar_picks.append({
                    "player_name": player_name,
                    "team": player_data.get("team", ""),
                    "nba_id": player_data.get("nba_id"),
                    "stat_type": demon_stat,
                    "direction": demon_direction,
                    "demon_line": demon_line,
                    "standard_line": round(std_line, 1),
                    "gap_diff": round(gap_diff, 1),
                    "gap_pct": round(gap_pct, 1),
                    "h10_rate": round(h10 * 100, 1),
                    "h5_rate": round(h5 * 100, 1),
                    "hit_probability": round(P * 100, 1),
                    "radar_score": round(radar_score, 2),
                    "radar_strength": min(100, max(0, round(P * 100, 1))),
                    "price": demon.get("price", 100),
                    "is_radar_pick": True,
                    "estimated_p": h10 == h5,  # Flag if P was estimated
                    "synced_at": sync_time.isoformat()
                })
        
        # Sort by radar_score descending
        radar_picks.sort(key=lambda x: x["radar_score"], reverse=True)
        
        # Take top 10
        top_10 = radar_picks[:10]
        
        # Store in radar_picks collection
        await self.radar_picks.delete_many({})
        if top_10:
            await self.radar_picks.insert_many(top_10)
        
        logger.info(f"[DEMON RADAR] Generated {len(top_10)} top picks from {len(radar_picks)} candidates")
        
        # Log top 3 for debugging
        for i, pick in enumerate(top_10[:3]):
            logger.info(f"  #{i+1}: {pick['player_name']} - {pick['stat_type']} {pick['demon_line']} "
                       f"(Gap: {pick['gap_diff']}, P: {pick['hit_probability']}%, Score: {pick['radar_score']})")
    
    def _extract_stat_type(self, market: str) -> str:
        """Extract stat type from market name"""
        # Remove _alternate suffix
        market = market.replace("_alternate", "")
        
        # Map to stat types
        stat_map = {
            "player_points": "PTS",
            "player_rebounds": "REB",
            "player_assists": "AST",
            "player_threes": "3PM",
            "player_blocks": "BLK",
            "player_steals": "STL",
            "player_turnovers": "TO",
            "player_points_rebounds": "P+R",
            "player_points_assists": "P+A",
            "player_rebounds_assists": "R+A",
            "player_points_rebounds_assists": "PRA",
        }
        
        return stat_map.get(market, "")
    
    async def get_demon_radar(self) -> Dict[str, Any]:
        """
        Get the Demon Radar top 10 picks from MongoDB.
        NO API CALLS - reads only from database.
        """
        picks = await self.radar_picks.find({}, {"_id": 0}).sort("radar_score", -1).to_list(10)
        
        sync_meta = await self.sync_log.find_one({"type": "cached_board"})
        
        return {
            "success": True,
            "synced_at": sync_meta.get("synced_at") if sync_meta else None,
            "picks_count": len(picks),
            "picks": picks,
            "algorithm": {
                "description": "Weighted Probability + Line Gap",
                "formula": "Score = P - (G × 100)",
                "hit_probability": "(H10 × 0.6) + (H5 × 0.4)",
                "line_gap": "(Demon - Standard) / Standard",
                "min_probability": "60%"
            }
        }
    
    async def get_cached_board(self) -> Dict[str, Any]:
        """
        Get the CACHED board from MongoDB.
        NO API CALLS - reads only from database.
        """
        # Get sync metadata
        sync_meta = await self.sync_log.find_one({"type": "cached_board"})
        
        if not sync_meta:
            return {
                "success": False,
                "synced_at": None,
                "message": "No cached data. Run /api/v3/sync first.",
                "players": [],
                "trending": []
            }
        
        # Get all players from cached_board (exclude _id)
        players = await self.cached_board.find({}, {"_id": 0}).sort("rank", 1).to_list(500)
        
        # Clean any remaining ObjectIds
        for player in players:
            for prop in player.get("props", []):
                prop.pop("_id", None)
            for prop in player.get("demons", []):
                prop.pop("_id", None)
            for prop in player.get("goblins", []):
                prop.pop("_id", None)
            for prop in player.get("standard", []):
                prop.pop("_id", None)
        
        # Get trending (top 10)
        trending = players[:10] if players else []
        
        return {
            "success": True,
            "synced_at": sync_meta.get("synced_at"),
            "players_count": len(players),
            "total_props": sync_meta.get("total_props", 0),
            "players": players,
            "trending": trending
        }
    
    async def get_cached_player(self, player_name: str) -> Dict[str, Any]:
        """
        Get a single player from the CACHED board.
        NO API CALLS - reads only from database.
        """
        # Try exact match first
        player = await self.cached_board.find_one(
            {"player_name": player_name},
            {"_id": 0}
        )
        
        if player:
            # Clean ObjectIds from nested arrays
            self._clean_object_ids(player)
            return {"success": True, "player": player}
        
        # Try case-insensitive search
        player = await self.cached_board.find_one(
            {"player_name": {"$regex": f"^{player_name}$", "$options": "i"}},
            {"_id": 0}
        )
        
        if player:
            self._clean_object_ids(player)
            return {"success": True, "player": player}
        
        # Fuzzy search
        all_players = await self.cached_board.find({}, {"player_name": 1, "_id": 0}).to_list(500)
        
        best_match = None
        best_score = 0
        for p in all_players:
            score = fuzz.ratio(player_name.lower(), p["player_name"].lower())
            if score > best_score and score > 70:
                best_score = score
                best_match = p["player_name"]
        
        if best_match:
            player = await self.cached_board.find_one(
                {"player_name": best_match},
                {"_id": 0}
            )
            self._clean_object_ids(player)
            return {"success": True, "player": player, "matched_name": best_match}
        
        return {
            "success": False,
            "message": "Lines loading... Player not in cache.",
            "player": None
        }
    
    def _clean_object_ids(self, player: Dict) -> None:
        """Remove all ObjectId fields from nested arrays to prevent serialization errors"""
        for key in ["props", "demons", "goblins", "standard"]:
            if key in player and isinstance(player[key], list):
                for item in player[key]:
                    if isinstance(item, dict):
                        item.pop("_id", None)
    
    # ==================== PILLAR 1: THE ODDS API (PrizePicks) ====================
    
    async def fetch_todays_events(self) -> List[Dict[str, Any]]:
        """Fetch all NBA events for today"""
        try:
            url = f"{ODDS_API_BASE}/sports/basketball_nba/events"
            params = {"apiKey": ODDS_API_KEY, "dateFormat": "iso"}
            
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, timeout=15.0)
                
                if response.status_code == 200:
                    events = response.json()
                    
                    # Store all events (don't filter by date to ensure we get all games)
                    await self.events_cache.delete_many({})
                    for event in events:
                        event["fetched_at"] = datetime.now(timezone.utc).isoformat()
                        await self.events_cache.insert_one(event)
                    
                    logger.info(f"[PILLAR 1] Found {len(events)} NBA events")
                    for e in events[:10]:
                        logger.info(f"  • {e.get('away_team')} @ {e.get('home_team')}")
                    
                    return events
                    
        except Exception as e:
            logger.error(f"[PILLAR 1] Event fetch error: {e}")
        
        return []
    
    async def fetch_prizepicks_odds(self, event_id: str, event_info: Dict) -> Dict[str, Any]:
        """
        Fetch PrizePicks odds using the correct API parameters:
        - regions=us_dfs (Daily Fantasy Sports)
        - bookmakers=prizepicks
        - markets=ALL markets (both standard and alternate)
        
        Classification will happen later based on market type:
        - Standard markets → "standard" (no icon)
        - Alternate markets + price=100 → "demon" (red icon)
        - Alternate markets + price≠100 → "goblin" (green icon)
        """
        try:
            url = f"{ODDS_API_BASE}/sports/basketball_nba/events/{event_id}/odds"
            
            # CRITICAL: Use us_dfs region and prizepicks bookmaker with ALL markets
            params = {
                "apiKey": ODDS_API_KEY,
                "regions": PRIZEPICKS_REGION,  # us_dfs - REQUIRED for PrizePicks
                "markets": PRIZEPICKS_ALL_MARKETS,  # Both standard and alternate markets
                "bookmakers": PRIZEPICKS_BOOKMAKER,  # prizepicks specifically
                "oddsFormat": "american"
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, timeout=30.0)
                
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
                    
                    logger.info(f"  [PRIZEPICKS] {event_info.get('away_team')} @ {event_info.get('home_team')}: {total_outcomes} lines, {len(players_found)} players")
                    
                    # Store in cache
                    await self.odds_cache.update_one(
                        {"event_id": event_id, "source": "prizepicks"},
                        {"$set": odds_data},
                        upsert=True
                    )
                    
                    return odds_data
                    
                elif response.status_code == 422:
                    # Try with just the basic alternate markets
                    logger.warning(f"  [PRIZEPICKS] Some markets unavailable for {event_id}, trying basic markets")
                    params["markets"] = "player_points,player_points_alternate,player_rebounds,player_rebounds_alternate,player_assists,player_assists_alternate"
                    response = await client.get(url, params=params, timeout=30.0)
                    if response.status_code == 200:
                        odds_data = response.json()
                        odds_data["event_id"] = event_id
                        odds_data["source"] = "prizepicks"
                        return odds_data
                else:
                    logger.warning(f"  [PRIZEPICKS] API returned {response.status_code} for {event_id}")
                        
        except Exception as e:
            logger.error(f"[PILLAR 1] PrizePicks odds fetch error for {event_id}: {e}")
        
        return {}
    
    async def fetch_standard_odds(self, event_id: str, event_info: Dict) -> Dict[str, Any]:
        """Also fetch standard markets from DraftKings/FanDuel for comparison"""
        try:
            url = f"{ODDS_API_BASE}/sports/basketball_nba/events/{event_id}/odds"
            params = {
                "apiKey": ODDS_API_KEY,
                "regions": "us",
                "markets": ",".join(PRIZEPICKS_STANDARD_MARKETS),
                "bookmakers": "draftkings,fanduel",
                "oddsFormat": "american"
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, timeout=30.0)
                
                if response.status_code == 200:
                    return response.json()
                        
        except Exception as e:
            logger.error(f"[PILLAR 1] Standard odds fetch error: {e}")
        
        return {}
    
    def extract_prizepicks_props(self, odds_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Extract all PrizePicks props and classify correctly:
        
        CLASSIFICATION RULES:
        - STANDARD (no icon): Props from MAIN markets (e.g., player_points)
        - DEMON (red icon): Props from ALTERNATE markets with EVEN odds (+100)
        - GOBLIN (green icon): Props from ALTERNATE markets with odds ≠ +100
        
        Also tracks player order for popularity ranking.
        """
        props = []
        event_id = odds_data.get("id") or odds_data.get("event_id")
        home_team = odds_data.get("home_team", "")
        away_team = odds_data.get("away_team", "")
        commence_time = odds_data.get("commence_time", "")
        
        # Track player appearance order (first players in response = more popular)
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
                
                # Determine if this is an alternate market
                is_alternate_market = "_alternate" in market_key
                
                for outcome in market.get("outcomes", []):
                    player_name = outcome.get("description", "")
                    direction = outcome.get("name", "")  # Over/Under
                    line = outcome.get("point")
                    price = outcome.get("price")  # American odds
                    
                    if player_name and line is not None:
                        # Track popularity order - first appearance = more popular
                        if player_name not in seen_players_in_event:
                            seen_players_in_event.add(player_name)
                            player_order_counter += 1
                            
                            # Store popularity (lower = more popular)
                            if player_name not in self._player_popularity:
                                self._player_popularity[player_name] = player_order_counter
                            else:
                                # Average with existing (in case player appears in multiple events)
                                self._player_popularity[player_name] = min(
                                    self._player_popularity[player_name], 
                                    player_order_counter
                                )
                        
                        # CLASSIFICATION LOGIC:
                        # 1. Standard: Main market (no _alternate) → no icon
                        # 2. Demon: Alternate market + price == +100 → red icon
                        # 3. Goblin: Alternate market + price != +100 → green icon
                        
                        if is_alternate_market:
                            # Alternate market classification
                            is_demon = price is not None and price == DEMON_ODDS
                            is_goblin = price is not None and price != DEMON_ODDS
                            prop_type = "demon" if is_demon else "goblin"
                        else:
                            # Standard market - no demon/goblin classification
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
    
    # ==================== PILLAR 2: BALLDONTLIE API ====================
    
    async def search_bdl_player(self, player_name: str) -> Optional[Dict[str, Any]]:
        """Map player name to BallDontLie player data"""
        if player_name in self._player_name_map:
            return self._player_name_map[player_name]
        
        try:
            name_parts = player_name.strip().split()
            search_terms = [name_parts[-1]] if len(name_parts) >= 2 else [player_name]
            if len(name_parts) >= 2:
                search_terms.append(name_parts[0])
            
            url = f"{BDL_BASE_URL}/players"
            headers = {"Authorization": BDL_API_KEY}
            
            for search_term in search_terms:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        url,
                        params={"search": search_term},
                        headers=headers,
                        timeout=10.0
                    )
                    
                    if response.status_code == 200:
                        players = response.json().get("data", [])
                        
                        if not players:
                            continue
                        
                        best_match = None
                        best_score = 0
                        
                        for player in players:
                            full_name = f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()
                            score = max(
                                fuzz.ratio(player_name.lower(), full_name.lower()),
                                fuzz.partial_ratio(player_name.lower(), full_name.lower())
                            )
                            
                            if score > best_score and score >= 60:
                                best_score = score
                                best_match = player
                        
                        if best_match:
                            self._player_name_map[player_name] = best_match
                            return best_match
            
        except Exception as e:
            logger.error(f"[PILLAR 2] BDL search error for {player_name}: {e}")
        
        return None
    
    async def fetch_player_season_stats(self, player_id: int) -> List[Dict[str, Any]]:
        """Fetch season stats for hit rate calculation"""
        try:
            # Check cache first
            cached = await self.stats_cache.find_one({"player_id": str(player_id)})
            if cached:
                cached_time = datetime.fromisoformat(cached["cached_at"])
                if datetime.now(timezone.utc) - cached_time < timedelta(hours=4):
                    return cached.get("games", [])
            
            url = f"{BDL_BASE_URL}/stats"
            params = {
                "player_ids[]": player_id,
                "seasons[]": CURRENT_SEASON,
                "per_page": 100
            }
            headers = {"Authorization": BDL_API_KEY}
            
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, headers=headers, timeout=15.0)
                
                if response.status_code == 200:
                    games = response.json().get("data", [])
                    
                    # Sort by date (most recent first)
                    games_sorted = sorted(
                        games,
                        key=lambda x: x.get("game", {}).get("date", ""),
                        reverse=True
                    )
                    
                    # Filter out DNP games
                    def player_played(game):
                        minutes = game.get("min")
                        if minutes:
                            min_str = str(minutes).replace(":", "").strip()
                            if min_str and min_str != "0" and min_str != "00":
                                return True
                        return (game.get("pts", 0) or 0) + (game.get("reb", 0) or 0) + (game.get("ast", 0) or 0) > 0
                    
                    played_games = [g for g in games_sorted if player_played(g)]
                    
                    # Cache results
                    await self.stats_cache.update_one(
                        {"player_id": str(player_id)},
                        {"$set": {
                            "player_id": str(player_id),
                            "games": played_games,
                            "cached_at": datetime.now(timezone.utc).isoformat()
                        }},
                        upsert=True
                    )
                    
                    return played_games
                    
        except Exception as e:
            logger.error(f"[PILLAR 2] Stats fetch error for player {player_id}: {e}")
        
        return []
    
    def calculate_hit_rates(self, games: List[Dict], market: str, line: float) -> Dict[str, Any]:
        """Calculate L5, L10, and Season hit rates"""
        market_to_stat = {
            "player_points": ["pts"],
            "alternate_player_points": ["pts"],
            "player_rebounds": ["reb"],
            "alternate_player_rebounds": ["reb"],
            "player_assists": ["ast"],
            "alternate_player_assists": ["ast"],
            "player_threes": ["fg3m"],
            "alternate_player_threes": ["fg3m"],
            "player_blocks": ["blk"],
            "player_steals": ["stl"],
            "player_turnovers": ["turnover"],
            "player_points_rebounds": ["pts", "reb"],
            "player_points_assists": ["pts", "ast"],
            "player_rebounds_assists": ["reb", "ast"],
            "player_points_rebounds_assists": ["pts", "reb", "ast"],
        }
        
        stat_keys = market_to_stat.get(market, ["pts"])
        
        def get_stat_value(game):
            return sum((game.get(key, 0) or 0) for key in stat_keys)
        
        def calc_window(game_list, line_val):
            if not game_list:
                return {"games_over": 0, "total_games": 0, "hit_rate": 0, "avg": 0}
            
            games_over = sum(1 for g in game_list if get_stat_value(g) > line_val)
            total = len(game_list)
            hit_rate = games_over / total if total > 0 else 0
            avg = sum(get_stat_value(g) for g in game_list) / total if total > 0 else 0
            
            return {
                "games_over": games_over,
                "total_games": total,
                "hit_rate": round(hit_rate, 3),
                "avg": round(avg, 1)
            }
        
        l5 = calc_window(games[:5], line)
        l10 = calc_window(games[:10], line)
        season = calc_window(games, line)
        
        # Trend detection
        trends = []
        if l5["total_games"] >= 3 and season["total_games"] >= 10:
            if l5["avg"] > season["avg"] * 1.15:
                trends.append("HOT")
            elif l5["avg"] < season["avg"] * 0.85:
                trends.append("COLD")
        
        return {
            "l5": l5,
            "l10": l10,
            "season": season,
            "trends": trends
        }
    
    # ==================== PILLAR 3: TANK01 API (with Exponential Backoff) ====================
    
    async def fetch_injuries(self) -> Dict[str, Any]:
        """
        Fetch injury data from Tank01 with:
        - 4-hour cache to reduce API calls
        - Exponential backoff for rate limiting
        - Graceful degradation on failure
        """
        # Check cache first
        cached = await self.tank01_cache.find_one({"type": "injuries"})
        if cached:
            cached_time = datetime.fromisoformat(cached["cached_at"])
            age = datetime.now(timezone.utc) - cached_time
            if age < TANK01_CACHE_TTL:
                logger.info(f"[PILLAR 3] Using cached injury data (age: {age.total_seconds():.0f}s)")
                self._injury_data = cached.get("data", {})
                return self._injury_data
        
        # Fetch fresh data with exponential backoff
        url = f"{TANK01_BASE}/getNBATeams"
        params = {"rosters": "true", "schedules": "false"}
        headers = {
            "X-RapidAPI-Key": TANK01_API_KEY,
            "X-RapidAPI-Host": "tank01-nba-live-in-game-real-time-statistics-nba.p.rapidapi.com"
        }
        
        logger.info("[PILLAR 3] Fetching injury data from Tank01 (with backoff)...")
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
            await self.tank01_cache.update_one(
                {"type": "injuries"},
                {"$set": {
                    "type": "injuries",
                    "data": injuries,
                    "cached_at": datetime.now(timezone.utc).isoformat()
                }},
                upsert=True
            )
            
            self._injury_data = injuries
            logger.info(f"[PILLAR 3] Found {len(injuries)} injured players (cached for 4h)")
            return injuries
        
        # Fallback to cached data if available (even if expired)
        if cached:
            logger.warning("[PILLAR 3] Using stale cached injury data (API failed)")
            self._injury_data = cached.get("data", {})
            return self._injury_data
        
        logger.warning("[PILLAR 3] No injury data available")
        return {}
    
    async def fetch_news(self) -> List[Dict[str, Any]]:
        """
        Fetch latest NBA news from Tank01 with:
        - 4-hour cache to reduce API calls
        - Exponential backoff for rate limiting
        - Graceful degradation on failure
        """
        # Check cache first
        cached = await self.tank01_cache.find_one({"type": "news"})
        if cached:
            cached_time = datetime.fromisoformat(cached["cached_at"])
            age = datetime.now(timezone.utc) - cached_time
            if age < TANK01_CACHE_TTL:
                logger.info(f"[PILLAR 3] Using cached news data (age: {age.total_seconds():.0f}s)")
                self._news_data = cached.get("data", [])
                return self._news_data
        
        # Fetch fresh data with exponential backoff
        url = f"{TANK01_BASE}/getNBANews"
        headers = {
            "X-RapidAPI-Key": TANK01_API_KEY,
            "X-RapidAPI-Host": "tank01-nba-live-in-game-real-time-statistics-nba.p.rapidapi.com"
        }
        
        logger.info("[PILLAR 3] Fetching news from Tank01 (with backoff)...")
        data = await fetch_with_backoff(url, headers)
        
        if data:
            news_items = data.get("body", []) if isinstance(data, dict) else data
            
            if isinstance(news_items, list):
                news_list = news_items[:100]
                
                # Cache the results
                await self.tank01_cache.update_one(
                    {"type": "news"},
                    {"$set": {
                        "type": "news",
                        "data": news_list,
                        "cached_at": datetime.now(timezone.utc).isoformat()
                    }},
                    upsert=True
                )
                
                self._news_data = news_list
                logger.info(f"[PILLAR 3] Fetched {len(news_list)} news items (cached for 4h)")
                return news_list
        
        # Fallback to cached data if available (even if expired)
        if cached:
            logger.warning("[PILLAR 3] Using stale cached news data (API failed)")
            self._news_data = cached.get("data", [])
            return self._news_data
        
        logger.warning("[PILLAR 3] No news data available")
        return []
    
    def get_player_injury_status(self, player_name: str) -> Dict[str, Any]:
        """Get injury status for a player"""
        player_lower = player_name.lower()
        result = {
            "has_injury": False,
            "injury_status": None,
            "injury_description": None,
            "has_news": False,
            "news_items": [],
            "warning_level": "none"  # none, questionable, out
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
    
    # ==================== MAIN ORCHESTRATION ====================
    
    async def process_player_prop(self, prop: Dict[str, Any]) -> Dict[str, Any]:
        """Process a single prop through all three pillars"""
        player_name = prop.get("player_name", "")
        market = prop.get("market", "")
        line = prop.get("line", 0)
        
        result = {
            **prop,
            "bdl_player_id": None,
            "bdl_team": None,
            "position": None,
            "hit_rates": None,
            "injury_info": {"warning_level": "none"},
            "has_goblin_warning": False,  # High hit rate + Questionable
            "processed_at": datetime.now(timezone.utc).isoformat()
        }
        
        # Pillar 2: BallDontLie stats
        bdl_player = await self.search_bdl_player(player_name)
        if bdl_player:
            result["bdl_player_id"] = bdl_player.get("id")
            result["bdl_team"] = bdl_player.get("team", {}).get("abbreviation", "")
            result["position"] = bdl_player.get("position", "")
            
            # Convert market name for stats lookup (remove _alternate suffix)
            stat_market = market.replace("_alternate", "")
            
            games = await self.fetch_player_season_stats(bdl_player.get("id"))
            if games:
                hit_rates = self.calculate_hit_rates(games, stat_market, line)
                result["hit_rates"] = hit_rates
        
        # Pillar 3: Injury check
        injury_info = self.get_player_injury_status(player_name)
        result["injury_info"] = injury_info
        
        # Special warning: Goblin with high hit rate but Questionable
        if prop.get("is_goblin") and result.get("hit_rates"):
            l10_hit_rate = result["hit_rates"].get("l10", {}).get("hit_rate", 0)
            if l10_hit_rate >= GOBLIN_HIT_RATE_WARNING and injury_info["warning_level"] == "questionable":
                result["has_goblin_warning"] = True
        
        return result
    
    async def run_full_sync(self) -> Dict[str, Any]:
        """Execute the full three-pillar sync with PrizePicks data"""
        sync_start = datetime.now(timezone.utc)
        self._current_date = self.get_current_date()
        
        logger.info("=" * 70)
        logger.info(f"DEMON & GOBLIN ENGINE v3.0 - PRIZEPICKS SYNC")
        logger.info(f"Date: {self._current_date}")
        logger.info(f"Region: {PRIZEPICKS_REGION} | Bookmaker: {PRIZEPICKS_BOOKMAKER}")
        logger.info("=" * 70)
        
        results = {
            "success": True,
            "sync_date": self._current_date,
            "sync_time": sync_start.isoformat(),
            "events_count": 0,
            "total_props": 0,
            "unique_players": 0,
            "standard_count": 0,
            "demons_count": 0,
            "goblins_count": 0,
            "stats_fetched": 0,
            "injuries_found": 0,
            "goblin_warnings": 0,
            "errors": [],
            "duration": 0
        }
        
        try:
            # ===== PILLAR 1: FETCH EVENTS AND PRIZEPICKS ODDS =====
            logger.info("\n[PILLAR 1] Fetching NBA events and PrizePicks lines...")
            logger.info(f"  Using region={PRIZEPICKS_REGION}, bookmaker={PRIZEPICKS_BOOKMAKER}")
            
            events = await self.fetch_todays_events()
            results["events_count"] = len(events)
            
            if not events:
                results["success"] = False
                results["errors"].append("No NBA events found")
                return results
            
            all_props = []
            all_players: Set[str] = set()
            
            # Fetch PrizePicks odds for EVERY event
            for event in events:
                event_id = event.get("id")
                if event_id:
                    # Fetch PrizePicks alternate lines
                    odds_data = await self.fetch_prizepicks_odds(event_id, event)
                    if odds_data:
                        props = self.extract_prizepicks_props(odds_data)
                        all_props.extend(props)
                        for p in props:
                            all_players.add(p.get("player_name", ""))
                    
                    await asyncio.sleep(0.3)  # Rate limiting
            
            results["total_props"] = len(all_props)
            results["unique_players"] = len(all_players)
            results["standard_count"] = sum(1 for p in all_props if p.get("prop_type") == "standard")
            results["demons_count"] = sum(1 for p in all_props if p.get("is_demon"))
            results["goblins_count"] = sum(1 for p in all_props if p.get("is_goblin"))
            
            logger.info(f"\n[PILLAR 1] PRIZEPICKS DATA COMPLETE:")
            logger.info(f"  Total Props: {len(all_props)}")
            logger.info(f"  Unique Players: {len(all_players)}")
            logger.info(f"  STANDARD (Main Markets): {results['standard_count']}")
            logger.info(f"  DEMONS (Alternate +100): {results['demons_count']}")
            logger.info(f"  GOBLINS (Alternate ≠+100): {results['goblins_count']}")
            
            # ===== PILLAR 3: FETCH INJURIES FIRST =====
            logger.info("\n[PILLAR 3] Fetching injury data from Tank01...")
            
            injuries = await self.fetch_injuries()
            await self.fetch_news()
            results["injuries_found"] = len(injuries)
            
            # ===== PILLAR 2: PROCESS STATS =====
            logger.info("\n[PILLAR 2] Processing stats from BallDontLie...")
            
            # Deduplicate by player+market+line+direction
            unique_props = {}
            for prop in all_props:
                key = f"{prop['player_name']}|{prop['market']}|{prop['line']}|{prop['direction']}"
                if key not in unique_props:
                    unique_props[key] = prop
            
            # Process ALL unique props - no limit!
            processed_props = []
            prop_list = list(unique_props.values())
            batch_size = 50
            
            logger.info(f"  Processing {len(prop_list)} unique props...")
            
            for i in range(0, len(prop_list), batch_size):
                batch = prop_list[i:i+batch_size]
                
                for prop in batch:
                    try:
                        processed = await self.process_player_prop(prop)
                        processed_props.append(processed)
                        
                        if processed.get("bdl_player_id"):
                            results["stats_fetched"] += 1
                        
                        if processed.get("has_goblin_warning"):
                            results["goblin_warnings"] += 1
                        
                        await asyncio.sleep(0.1)
                        
                    except Exception as e:
                        results["errors"].append(f"Prop error: {str(e)[:50]}")
                
                logger.info(f"  Processed {min(i+batch_size, len(prop_list))}/{len(prop_list)} props")
            
            # ===== STORE RESULTS GROUPED BY PLAYER =====
            logger.info("\n[STORAGE] Organizing data by player...")
            
            # Group props by player
            player_data = {}
            for prop in processed_props:
                player_name = prop.get("player_name", "Unknown")
                
                if player_name not in player_data:
                    # Get NBA player ID for headshot
                    nba_id = get_nba_player_id(player_name)
                    
                    player_data[player_name] = {
                        "player_name": player_name,
                        "team": prop.get("bdl_team", ""),
                        "position": prop.get("position", ""),
                        "nba_id": nba_id,  # NBA CDN headshot ID
                        "injury_info": prop.get("injury_info", {}),
                        "popularity_order": self._player_popularity.get(player_name, 999),
                        "props": [],
                        "standard": [],  # Standard props (main market, no icon)
                        "demons": [],    # Demon props (alternate market, +100)
                        "goblins": [],   # Goblin props (alternate market, ≠+100)
                        "has_goblin_warning": False,
                        "has_new_injury": False  # NEW: Track if this is a new injury update
                    }
                
                player_data[player_name]["props"].append(prop)
                
                # Classify into appropriate bucket
                if prop.get("is_demon"):
                    player_data[player_name]["demons"].append(prop)
                elif prop.get("is_goblin"):
                    player_data[player_name]["goblins"].append(prop)
                else:
                    player_data[player_name]["standard"].append(prop)
                
                if prop.get("has_goblin_warning"):
                    player_data[player_name]["has_goblin_warning"] = True
            
            # ===== BUILD TRENDING 10 =====
            logger.info("\n[TRENDING] Building Most Popular Today (Top 10)...")
            
            # Calculate popularity score for each player
            # Score = (API Order * 10) - (Demon Count * 5) - (Goblin Count * 3) - (Has Injury Flag * -50)
            # Lower score = more popular
            trending_list = []
            for name, data in player_data.items():
                demons_count = len(data.get("demons", []))
                goblins_count = len(data.get("goblins", []))
                special_count = demons_count + goblins_count
                
                # Only include players with at least 1 Demon or Goblin
                if special_count == 0:
                    continue
                
                popularity_order = data.get("popularity_order", 999)
                injury_info = data.get("injury_info", {})
                has_injury = injury_info.get("has_injury", False)
                
                # Popularity score (lower = more popular)
                score = popularity_order - (special_count * 2)
                if has_injury:
                    score += 20  # Penalize injured players slightly
                
                # Get best prop for display (highest hit rate Goblin or Demon)
                best_prop = None
                best_hit_rate = 0
                for prop in data.get("props", []):
                    if prop.get("is_demon") or prop.get("is_goblin"):
                        hit_rates = prop.get("hit_rates") or {}
                        l10 = hit_rates.get("l10") or {}
                        hit_rate = l10.get("hit_rate", 0) or 0
                        if hit_rate > best_hit_rate:
                            best_hit_rate = hit_rate
                            best_prop = prop
                
                trending_list.append({
                    "player_name": name,
                    "team": data.get("team", ""),
                    "position": data.get("position", ""),
                    "nba_id": data.get("nba_id"),  # NBA CDN headshot ID
                    "popularity_score": score,
                    "popularity_order": popularity_order,
                    "demons_count": demons_count,
                    "goblins_count": goblins_count,
                    "total_props": len(data.get("props", [])),
                    "injury_info": injury_info,
                    "has_new_injury": has_injury,  # Mark if they have any injury
                    "best_prop": best_prop,
                    "best_hit_rate": best_hit_rate
                })
            
            # Sort by popularity score (lower = better)
            trending_list.sort(key=lambda x: x["popularity_score"])
            
            # Take top 10
            trending_10 = trending_list[:10]
            
            # Store trending in DB
            await self.trending_cache.delete_many({})
            if trending_10:
                await self.trending_cache.insert_many(trending_10)
            
            results["trending_count"] = len(trending_10)
            logger.info(f"  Trending 10: {[t['player_name'] for t in trending_10]}")
            
            # Store all player data in MongoDB
            await self.player_data.delete_many({})
            if player_data:
                await self.player_data.insert_many(list(player_data.values()))
            
            # ===== STORE STATIC SHELL CACHE =====
            logger.info("\n[CACHE] Storing static shell (24h TTL)...")
            await self.store_static_shell(list(player_data.values()), trending_10)
            
            # Log sync result
            await self.sync_log.insert_one({
                "sync_date": self._current_date,
                "sync_time": sync_start.isoformat(),
                "results": results,
                "completed_at": datetime.now(timezone.utc).isoformat()
            })
            
            self._last_sync = datetime.now(timezone.utc)
            
        except Exception as e:
            logger.error(f"Sync error: {e}")
            results["success"] = False
            results["errors"].append(str(e))
        
        results["duration"] = (datetime.now(timezone.utc) - sync_start).total_seconds()
        
        logger.info("\n" + "=" * 70)
        logger.info(f"""
DEMON & GOBLIN SYNC COMPLETE - PRIZEPICKS EDITION
==================================================
Duration: {results['duration']:.1f}s
Date: {results['sync_date']}

PILLAR 1 - PRIZEPICKS (us_dfs region):
  Events: {results['events_count']}
  Total Props: {results['total_props']}
  Unique Players: {results['unique_players']}
  
CLASSIFICATION (Market-Based):
  STANDARD (Main Markets): {results['standard_count']}
  DEMONS (Alternate +100): {results['demons_count']}
  GOBLINS (Alternate ≠+100): {results['goblins_count']}
  
PILLAR 2 - BALLDONTLIE:
  Stats Fetched: {results['stats_fetched']}
  
PILLAR 3 - TANK01:
  Injuries Found: {results['injuries_found']}
  Goblin Warnings: {results['goblin_warnings']}
""")
        logger.info("=" * 70)
        
        return results
    
    # ==================== DATA ACCESS ====================
    
    async def get_all_players(self) -> List[Dict[str, Any]]:
        """Get all players with their props (collapsed view data)"""
        cursor = self.player_data.find({}, {"_id": 0})
        players = await cursor.to_list(1000)
        
        # Sort: Players with Demons/Goblins first
        def sort_key(p):
            has_special = len(p.get("demons", [])) + len(p.get("goblins", []))
            return (-has_special, p.get("player_name", ""))
        
        players.sort(key=sort_key)
        return players
    
    async def get_player_detail(self, player_name: str) -> Optional[Dict[str, Any]]:
        """Get full detail for a specific player (expanded view)"""
        player = await self.player_data.find_one(
            {"player_name": {"$regex": player_name, "$options": "i"}},
            {"_id": 0}
        )
        
        if player:
            # Sort props: Demons first, then Goblins, then rest by hit rate
            props = player.get("props", [])
            
            def prop_sort_key(p):
                priority = 2  # Standard
                if p.get("is_demon"):
                    priority = 0
                elif p.get("is_goblin"):
                    priority = 1
                
                hit_rate = 0
                if p.get("hit_rates") and p.get("hit_rates", {}).get("l10"):
                    hit_rate = p.get("hit_rates", {}).get("l10", {}).get("hit_rate", 0) or 0
                
                return (priority, -hit_rate)
            
            props.sort(key=prop_sort_key)
            player["props"] = props
        
        return player
    
    async def get_all_demons(self) -> List[Dict[str, Any]]:
        """Get all Demon lines across all players"""
        players = await self.player_data.find({}, {"_id": 0}).to_list(1000)
        
        demons = []
        for player in players:
            for demon in player.get("demons", []):
                demon["player_team"] = player.get("team", "")
                demon["player_injury"] = player.get("injury_info", {})
                demons.append(demon)
        
        # Sort by price (highest odds first)
        demons.sort(key=lambda x: x.get("price", 0), reverse=True)
        return demons
    
    async def get_all_goblins(self) -> List[Dict[str, Any]]:
        """Get all Goblin lines across all players"""
        players = await self.player_data.find({}, {"_id": 0}).to_list(1000)
        
        goblins = []
        for player in players:
            for goblin in player.get("goblins", []):
                goblin["player_team"] = player.get("team", "")
                goblin["player_injury"] = player.get("injury_info", {})
                goblin["has_warning"] = player.get("has_goblin_warning", False)
                goblins.append(goblin)
        
        # Sort by hit rate (highest first)
        def sort_key(g):
            hit_rate = 0
            if g.get("hit_rates") and g.get("hit_rates", {}).get("l10"):
                hit_rate = g.get("hit_rates", {}).get("l10", {}).get("hit_rate", 0) or 0
            return -hit_rate
        
        goblins.sort(key=sort_key)
        return goblins
    
    async def get_sync_status(self) -> Dict[str, Any]:
        """Get current sync status"""
        players_count = await self.player_data.count_documents({})
        
        # Count standard, demons and goblins
        pipeline = [
            {"$project": {
                "standard_count": {"$size": {"$ifNull": ["$standard", []]}},
                "demons_count": {"$size": {"$ifNull": ["$demons", []]}},
                "goblins_count": {"$size": {"$ifNull": ["$goblins", []]}},
                "props_count": {"$size": {"$ifNull": ["$props", []]}}
            }},
            {"$group": {
                "_id": None,
                "total_standard": {"$sum": "$standard_count"},
                "total_demons": {"$sum": "$demons_count"},
                "total_goblins": {"$sum": "$goblins_count"},
                "total_props": {"$sum": "$props_count"}
            }}
        ]
        
        agg_result = await self.player_data.aggregate(pipeline).to_list(1)
        counts = agg_result[0] if agg_result else {"total_standard": 0, "total_demons": 0, "total_goblins": 0, "total_props": 0}
        
        # Get last sync log
        last_sync = await self.sync_log.find_one({}, sort=[("sync_time", -1)])
        
        return {
            "last_sync": self._last_sync.isoformat() if self._last_sync else (last_sync.get("sync_time") if last_sync else None),
            "sync_date": self._current_date or self.get_current_date(),
            "unique_players": players_count,
            "total_props": counts.get("total_props", 0),
            "standard_count": counts.get("total_standard", 0),
            "demons_count": counts.get("total_demons", 0),
            "goblins_count": counts.get("total_goblins", 0),
            "season": CURRENT_SEASON
        }
    
    async def search_players(self, query: str) -> List[Dict[str, Any]]:
        """Search for players by name"""
        cursor = self.player_data.find(
            {"player_name": {"$regex": query, "$options": "i"}},
            {"_id": 0}
        )
        return await cursor.to_list(50)

    
    async def get_trending_10(self) -> List[Dict[str, Any]]:
        """
        Get the Top 10 Most Popular players today
        Based on PrizePicks board order and Demon/Goblin count
        """
        cursor = self.trending_cache.find({}, {"_id": 0}).sort("popularity_score", 1)
        trending = await cursor.to_list(10)
        
        # Enrich with full player data if needed
        enriched = []
        for t in trending:
            player_name = t.get("player_name")
            # Get full player data
            player = await self.player_data.find_one(
                {"player_name": player_name},
                {"_id": 0}
            )
            
            if player:
                # Get top 3 props (best hit rate Demons/Goblins)
                top_props = []
                all_props = player.get("props", [])
                
                # Filter to Demons and Goblins only
                special_props = [p for p in all_props if p.get("is_demon") or p.get("is_goblin")]
                
                # Sort by hit rate - handle None values
                def get_hit_rate(x):
                    hr = x.get("hit_rates") or {}
                    l10 = hr.get("l10") or {}
                    return l10.get("hit_rate", 0) or 0
                
                special_props.sort(key=get_hit_rate, reverse=True)
                
                top_props = special_props[:3]
                
                enriched.append({
                    **t,
                    "top_props": top_props,
                    "all_demons": player.get("demons", [])[:5],
                    "all_goblins": player.get("goblins", [])[:5]
                })
            else:
                enriched.append(t)
        
        return enriched

    
    # ==================== HYBRID CACHING LAYER ====================
    
    async def get_static_shell(self) -> Dict[str, Any]:
        """
        Get cached STATIC SHELL data (24h TTL)
        Contains: Player metadata, teams, positions, historical stats (L5, L10, Season)
        Does NOT contain: Live betting lines
        """
        # Check if we have valid cached data
        cached = await self.static_shell_cache.find_one({"type": "shell"}, {"_id": 0})
        
        if cached:
            cached_time = datetime.fromisoformat(cached["cached_at"])
            age = datetime.now(timezone.utc) - cached_time
            
            if age < STATIC_CACHE_TTL:
                logger.info(f"[CACHE HIT] Static shell (age: {age.total_seconds():.0f}s)")
                return {
                    "cache_hit": True,
                    "cache_age_seconds": age.total_seconds(),
                    "players": cached.get("players", []),
                    "trending": cached.get("trending", []),
                    "sync_date": cached.get("sync_date"),
                    "stats_version": cached.get("stats_version")
                }
        
        # Cache miss - need full sync
        logger.info("[CACHE MISS] Static shell expired or not found")
        return {"cache_hit": False, "players": [], "trending": []}
    
    async def store_static_shell(self, players: List[Dict], trending: List[Dict]):
        """
        Store STATIC SHELL data with 24h TTL
        Strips out live betting lines, keeps only metadata and historical stats
        """
        # Extract static data only (no live lines)
        static_players = []
        for p in players:
            static_player = {
                "player_name": p.get("player_name"),
                "team": p.get("team"),
                "position": p.get("position"),
                "injury_info": p.get("injury_info"),
                "popularity_order": p.get("popularity_order"),
                # Historical stats only (these don't change intra-day)
                "stats_summary": self._extract_stats_summary(p.get("props", []))
            }
            static_players.append(static_player)
        
        # Clean trending data (remove any _id fields)
        clean_trending = []
        for t in trending:
            clean_t = {k: v for k, v in t.items() if k != '_id'}
            clean_trending.append(clean_t)
        
        # Store with timestamp
        await self.static_shell_cache.update_one(
            {"type": "shell"},
            {"$set": {
                "type": "shell",
                "players": static_players,
                "trending": clean_trending,
                "sync_date": self.get_current_date(),
                "stats_version": datetime.now(timezone.utc).strftime("%Y%m%d"),
                "cached_at": datetime.now(timezone.utc).isoformat()
            }},
            upsert=True
        )
        logger.info(f"[CACHE STORE] Static shell saved ({len(static_players)} players)")
    
    def _extract_stats_summary(self, props: List[Dict]) -> Dict[str, Any]:
        """Extract aggregated stats summary from props for caching"""
        if not props:
            return {}
        
        # Get unique market stats
        stats = {}
        for prop in props:
            market = prop.get("market", "").replace("_alternate", "")
            hit_rates = prop.get("hit_rates") or {}
            
            if market and hit_rates and market not in stats:
                stats[market] = {
                    "l5": hit_rates.get("l5"),
                    "l10": hit_rates.get("l10"),
                    "season": hit_rates.get("season"),
                    "trends": hit_rates.get("trends", [])
                }
        
        return stats
    
    async def get_live_lines(self) -> Dict[str, Any]:
        """
        Get DYNAMIC PULSE data (60s TTL)
        Contains ONLY: Live betting lines (price, point, demon/goblin tags)
        This is the lightweight endpoint for real-time updates
        """
        # Check dynamic cache
        cached = await self.dynamic_lines_cache.find_one({"type": "lines"}, {"_id": 0})
        
        if cached:
            cached_time = datetime.fromisoformat(cached["cached_at"])
            age = datetime.now(timezone.utc) - cached_time
            
            if age < DYNAMIC_CACHE_TTL:
                logger.info(f"[CACHE HIT] Dynamic lines (age: {age.total_seconds():.0f}s)")
                return {
                    "cache_hit": True,
                    "cache_age_seconds": age.total_seconds(),
                    "lines": cached.get("lines", {}),
                    "last_update": cached.get("cached_at")
                }
        
        # Cache miss - fetch fresh lines
        logger.info("[CACHE MISS] Dynamic lines - fetching fresh data")
        lines = await self._fetch_fresh_lines()
        
        # Store in cache
        await self.dynamic_lines_cache.update_one(
            {"type": "lines"},
            {"$set": {
                "type": "lines",
                "lines": lines,
                "cached_at": datetime.now(timezone.utc).isoformat()
            }},
            upsert=True
        )
        
        return {
            "cache_hit": False,
            "cache_age_seconds": 0,
            "lines": lines,
            "last_update": datetime.now(timezone.utc).isoformat()
        }
    
    async def _fetch_fresh_lines(self) -> Dict[str, List[Dict]]:
        """
        Fetch ONLY live betting lines (lightweight)
        Returns: {player_name: [{market, line, price, is_demon, is_goblin, prop_type}, ...]}
        
        Classification:
        - Standard: Main market (no _alternate)
        - Demon: Alternate market + price == +100
        - Goblin: Alternate market + price != +100
        """
        lines_by_player = {}
        
        try:
            # Get events
            events = await self.fetch_todays_events()
            
            for event in events[:10]:  # Limit to 10 events for speed
                event_id = event.get("id")
                if not event_id:
                    continue
                
                # Fetch PrizePicks lines - both standard and alternate
                url = f"{ODDS_API_BASE}/sports/basketball_nba/events/{event_id}/odds"
                params = {
                    "apiKey": ODDS_API_KEY,
                    "regions": PRIZEPICKS_REGION,
                    "markets": PRIZEPICKS_ALL_MARKETS,  # Both standard and alternate
                    "bookmakers": PRIZEPICKS_BOOKMAKER,
                    "oddsFormat": "american"
                }
                
                async with httpx.AsyncClient() as client:
                    response = await client.get(url, params=params, timeout=15.0)
                    
                    if response.status_code == 200:
                        data = response.json()
                        
                        for bm in data.get("bookmakers", []):
                            if bm.get("key") != "prizepicks":
                                continue
                            
                            for market in bm.get("markets", []):
                                market_key = market.get("key", "")
                                is_alternate_market = "_alternate" in market_key
                                
                                for outcome in market.get("outcomes", []):
                                    player_name = outcome.get("description", "")
                                    if not player_name:
                                        continue
                                    
                                    price = outcome.get("price")
                                    
                                    # Classification logic
                                    if is_alternate_market:
                                        is_demon = price is not None and price == DEMON_ODDS
                                        is_goblin = price is not None and price != DEMON_ODDS
                                        prop_type = "demon" if is_demon else "goblin"
                                    else:
                                        is_demon = False
                                        is_goblin = False
                                        prop_type = "standard"
                                    
                                    line_data = {
                                        "market": market_key,
                                        "direction": outcome.get("name"),
                                        "line": outcome.get("point"),
                                        "price": price,
                                        "is_alternate_market": is_alternate_market,
                                        "is_demon": is_demon,
                                        "is_goblin": is_goblin,
                                        "prop_type": prop_type
                                    }
                                    
                                    if player_name not in lines_by_player:
                                        lines_by_player[player_name] = []
                                    lines_by_player[player_name].append(line_data)
                
                await asyncio.sleep(0.2)  # Rate limiting
                
        except Exception as e:
            logger.error(f"[LINES FETCH] Error: {e}")
        
        return lines_by_player
    
    async def get_hydrated_board(self) -> Dict[str, Any]:
        """
        Get board with hybrid caching:
        1. First load static shell (instant)
        2. Then hydrate with live lines (background)
        """
        # Get static shell first
        static = await self.get_static_shell()
        
        if not static.get("cache_hit"):
            # No cached data - need full sync
            return {
                "needs_sync": True,
                "players": [],
                "trending": []
            }
        
        # Get live lines
        lines_data = await self.get_live_lines()
        lines = lines_data.get("lines", {})
        
        # Hydrate static players with live lines
        hydrated_players = []
        for player in static.get("players", []):
            player_name = player.get("player_name")
            player_lines = lines.get(player_name, [])
            
            # Count demons and goblins from live lines
            demons_count = sum(1 for line in player_lines if line.get("is_demon"))
            goblins_count = sum(1 for line in player_lines if line.get("is_goblin"))
            
            hydrated_players.append({
                **player,
                "props": player_lines,
                "demons_count": demons_count,
                "goblins_count": goblins_count,
                "lines_loaded": len(player_lines) > 0
            })
        
        return {
            "needs_sync": False,
            "static_cache_age": static.get("cache_age_seconds"),
            "lines_cache_age": lines_data.get("cache_age_seconds"),
            "players": hydrated_players,
            "trending": static.get("trending", []),
            "sync_date": static.get("sync_date")
        }

"""
Demon & Goblin Analytics Engine v3.2
=====================================

PrizePicks-Specific System for NBA Player Props

ARCHITECTURE RESET (v3.2):
- Single source of truth: All data enrichment happens during sync
- Dumb components: War Zone, Goblin Recon, Gauntlet, Safe Haven just read data
- Tank01 playerID as primary key
- No runtime lookups

API Configuration:
- Region: us_dfs (Daily Fantasy Sports - includes PrizePicks)
- Bookmaker: prizepicks
- Markets: player_*_alternate (PrizePicks alternate lines)

Classification (PrizePicks Native):
- Goblin (Green): Default odds lines - easier, high-probability props
- Demon (Red): Even odds (+100) lines - harder, boosted props

Payout Calculation Engine (v3.2):
- Leg-level modifiers: Standard (1.0), Demon (1.1-1.5), Goblin (0.7-0.9)
- Formula: Total Payout = Base Multiplier × (Mod_1 × Mod_2 × ... × Mod_n)

Triple-Pillar Integration:
1. The Odds API (us_dfs/prizepicks) - All PrizePicks lines
2. BallDontLie API - Player stats for hit rate calculation
3. Tank01 API - Injury reports and player news

DATA INTEGRITY (v3.1):
- Triple-check verification for all stats
- source_verified tag on all Demon/Goblin records
- Auto-delete insights that fail verification gates
- Hallucination detection and prevention
"""

import httpx
import logging
import os
import asyncio
import random
import statistics
from datetime import datetime, timezone, timedelta, time
from typing import Optional, Dict, List, Any, Set, Tuple
from thefuzz import fuzz
from motor.motor_asyncio import AsyncIOMotorDatabase

# Data Integrity Module
from data_integrity import DataIntegrityVerifier, create_verified_insight

# Services - Extracted logic for modularity
from services.stats_service import (
    calculate_hit_rates as stats_calculate_hit_rates,
    calculate_heat_level as stats_calculate_heat_level,
    calculate_safety_level as stats_calculate_safety_level,
    calculate_bullet_level as stats_calculate_bullet_level,
    calculate_volatility as stats_calculate_volatility,
    STAT_FIELD_MAP
)
from services.dvp_service import calculate_dvp_modifier as dvp_calculate_modifier
from services.insights_service import (
    generate_insight_summary as insights_generate_summary,
    calculate_confidence_rating as insights_calculate_confidence,
    build_player_insights as insights_build_player,
    calculate_volatility as insights_calculate_volatility,
    get_team_pace as insights_get_team_pace,
    calculate_pace_factor as insights_calculate_pace_factor,
    get_high_usage_players as insights_get_high_usage,
    calculate_usage_bump_simple as insights_calculate_usage_bump
)
from services.utils_service import (
    normalize_team_name as utils_normalize_team,
    sanitize_player_name as utils_sanitize_player,
    create_composite_key as utils_create_key,
    get_current_date as utils_get_current_date,
    get_player_photo_url as utils_get_player_photo
)

# Payout Calculation Engine
from payout_engine import (
    calculate_payout_from_picks,
    calculate_leg_modifier,
    estimate_payout,
    AssetType,
    BASE_MULTIPLIERS
)

# NBA Master Hub - SINGLE SOURCE OF TRUTH
from nba_master_hub import fetchPlayerIntel, fetchPlayerIntelByName, get_master_hub

# Odds API Mapper - Permanent player name to ID mapping
from odds_api_mapper import get_odds_api_mapper, init_odds_api_mapper

# NBA.com API fallback for players missing from BallDontLie
try:
    from nba_api.stats.endpoints import playergamelog
    from nba_api.stats.static import players as nba_players
    NBA_API_AVAILABLE = True
except ImportError:
    NBA_API_AVAILABLE = False

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
TANK01_BASE = "https://tank01-fantasy-stats.p.rapidapi.com"
TANK01_HOST = "tank01-fantasy-stats.p.rapidapi.com"
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

# ==================== NBA TEAM MAPPING ====================
# Full team names to 3-letter abbreviations
# NBA Team mapping is now in services.utils_service
from services.utils_service import NBA_TEAM_MAP, NBA_TEAM_ABBREV_TO_FULL, NAME_ALIASES

# ==================== KNOWN PLAYER-TEAM MAPPING ====================
# Hardcoded for star players to ensure correct team assignment
# This overrides any incorrect API data
KNOWN_PLAYER_TEAMS = {
    # Boston Celtics
    "Derrick White": "BOS",
    "Jayson Tatum": "BOS",
    "Jaylen Brown": "BOS",
    "Jrue Holiday": "BOS",
    "Kristaps Porzingis": "BOS",
    "Payton Pritchard": "BOS",
    "Sam Hauser": "BOS",
    # Los Angeles Lakers
    "LeBron James": "LAL",
    "Anthony Davis": "LAL",
    "Austin Reaves": "LAL",
    "D'Angelo Russell": "LAL",
    "Max Christie": "LAL",
    "Luke Kennard": "LAL",
    # Denver Nuggets
    "Nikola Jokic": "DEN",
    "Jamal Murray": "DEN",
    "Michael Porter Jr.": "DEN",
    "Christian Braun": "DEN",
    "Bruce Brown": "DEN",
    # Milwaukee Bucks
    "Giannis Antetokounmpo": "MIL",
    "Damian Lillard": "MIL",
    "Khris Middleton": "MIL",
    # Phoenix Suns
    "Devin Booker": "PHX",
    "Bradley Beal": "PHX",
    # Dallas Mavericks
    "Luka Doncic": "DAL",
    "Kyrie Irving": "DAL",
    "Klay Thompson": "DAL",
    # Golden State Warriors
    "Stephen Curry": "GSW",
    "Draymond Green": "GSW",
    "Jonathan Kuminga": "GSW",
    # Oklahoma City Thunder
    "Shai Gilgeous-Alexander": "OKC",
    "Chet Holmgren": "OKC",
    "Jalen Williams": "OKC",
    "Cason Wallace": "OKC",
    "Jaylin Williams": "OKC",
    # Philadelphia 76ers
    "Joel Embiid": "PHI",
    "Tyrese Maxey": "PHI",
    "Jared McCain": "PHI",
    "Tim Hardaway Jr.": "PHI",
    # San Antonio Spurs
    "Victor Wembanyama": "SAS",
    "Devin Vassell": "SAS",
    "Dylan Harper": "SAS",
    # Orlando Magic
    "Paolo Banchero": "ORL",
    "Franz Wagner": "ORL",
    # Memphis Grizzlies
    "Ja Morant": "MEM",
    "Desmond Bane": "MEM",
    # Minnesota Timberwolves
    "Anthony Edwards": "MIN",
    "Robert Dillingham": "MIN",
    # Cleveland Cavaliers
    "Donovan Mitchell": "CLE",
    "Darius Garland": "CLE",
    # Miami Heat
    "Jimmy Butler": "MIA",
    "Bam Adebayo": "MIA",
    "Tyler Herro": "MIA",
    # Utah Jazz
    "Cody Williams": "UTA",
    "Kyle Filipowski": "UTA",
    "Brice Sensabaugh": "UTA",
    "Ace Bailey": "UTA",
    # Portland Trail Blazers
    "Toumani Camara": "POR",
    "Donovan Clingan": "POR",
    # New York Knicks
    "Karl-Anthony Towns": "NYK",
    "Jalen Brunson": "NYK",
    # Charlotte Hornets
    "Nicolas Richards": "CHA",
    # Houston Rockets - 2026 ROSTER UPDATE
    "Kevin Durant": "HOU",  # TRADED FROM PHX - 2026
    "Jalen Green": "HOU",
    "Alperen Sengun": "HOU",
    "Reed Sheppard": "HOU",
    "Jabari Smith Jr.": "HOU",
    # New Orleans Pelicans
    "Trey Murphy III": "NOP",
    "Zion Williamson": "NOP",
    "Brandon Ingram": "NOP",
}

# ==================== NAME NORMALIZATION ====================
# Name aliases are now imported from services.utils_service

INJURY_KEYWORDS = [
    "injury", "injured", "out", "questionable", "doubtful", "probable",
    "day-to-day", "GTD", "game time decision", "load management", "rest",
    "ankle", "knee", "hamstring", "back", "shoulder", "concussion", 
    "illness", "personal", "sore", "sprain", "strain"
]

# ==================== NBA PLAYER ID MAPPING ====================
# NBA Player IDs are now in config/settings.py
from config.settings import NBA_PLAYER_IDS

def get_nba_player_id(player_name: str) -> Optional[int]:
    """Get NBA player ID from static mapping or return None"""
    return NBA_PLAYER_IDS.get(player_name)


# ==================== EXPONENTIAL BACKOFF HELPER ====================

async def fetch_with_backoff(url: str, headers: Dict, params: Dict = None, max_retries: int = MAX_RETRIES) -> Optional[Dict]:
    """
    Fetch with exponential backoff for rate-limited APIs.
    PROXY: Delegates to services.data_scraper.fetch_with_backoff
    """
    from services.data_scraper import fetch_with_backoff as scraper_fetch_with_backoff
    return await scraper_fetch_with_backoff(url, headers, params, max_retries)


# ==================== DvP (Defense vs Position) MODULE ====================
"""
Real DvP calculation based on team defensive rankings per stat category.
Data source: NBA.com team stats, updated seasonally.
"""

# 2024-25 NBA Team Defensive Rankings by Stat Category
# Rankings 1-30 where 1 = BEST defense (allows LEAST), 30 = WORST (allows MOST)
# Lower rank = harder matchup for offense
# DVP Rankings are now in config/settings.py (imported by dvp_service)
# This reference is kept for backward compatibility
from config.settings import DVP_RANKINGS

# Stat type mapping - now imported from services
from services.stats_service import STAT_FIELD_MAP
STAT_TYPE_MAP = {
    "player_points": "PTS",
    "player_assists": "AST",
    "player_rebounds": "REB",
    "player_threes": "3PM",
    "player_blocks": "BLK",
    "player_steals": "STL",
    "player_points_rebounds_assists": "PRA",
    "player_points_rebounds": "P+R",
    "player_points_assists": "P+A",
    "player_rebounds_assists": "R+A",
}

# DVP calculation is now handled by services.dvp_service (imported at top)
# Keeping this as a proxy for backward compatibility
def calculate_dvp_modifier(opponent_team: str, stat_type: str) -> float:
    """
    Calculate DvP modifier based on opponent's defensive ranking.
    PROXY: Delegates to services.dvp_service.calculate_dvp_modifier
    """
    return dvp_calculate_modifier(opponent_team, stat_type)


def get_dvp_label(modifier: float) -> str:
    """Get human-readable DvP label.
    PROXY: Delegates to services.dvp_service.get_dvp_label
    """
    from services.dvp_service import get_dvp_label as dvp_get_label
    return dvp_get_label(modifier)


class DemonGoblinEngine:
    """
    The Demon & Goblin Analytics Engine - PrizePicks Edition
    
    WAREHOUSE MODEL (MongoDB):
    - LIVE_PROPS: All props stored with deduplication (synced via SyncBoard)
    - WAR_ZONE: Top 10 pre-calculated picks flagged as is_radar_pick
    - Zero API calls from frontend - everything reads from MongoDB
    
    Classification (PrizePicks Native):
    - Standard (No Icon): Main market props
    - Demons (Red): Alternate market + Even odds (+100)
    - Goblins (Green): Alternate market + Non-even odds
    
    Features:
    - War Zone: Pre-calculated top 10 picks based on Hit Rate + Line Gap
    - Trending 10: Most popular players based on API order
    - Player-First Hierarchy: All props organized by player
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        
        # Initialize Repository Manager for clean data access
        from repositories import RepositoryManager
        self.repo = RepositoryManager(db)
        
        # Initialize High-Level Services (extracted logic)
        from services import (
            RosterService, PhotoService, PropsService, SyncService, 
            TierBuilderService, ParlayBuilderService, CachedBoardBuilderService,
            OddsApiService, StatsApiService, Tank01Service, PicksGetterService,
            DataIntegrityService, StatsEnrichmentService
        )
        self.roster_service = RosterService(self.repo, db)
        self.photo_service = PhotoService(db)
        self.props_service = PropsService(db)
        self.sync_service = SyncService(self.repo, db)
        self.tier_builder_service = TierBuilderService(db)
        self.parlay_builder_service = ParlayBuilderService(db)
        self.cached_board_builder_service = CachedBoardBuilderService(
            db, self.tier_builder_service, self.parlay_builder_service
        )
        self.odds_api_service = OddsApiService(db)
        self.stats_api_service = StatsApiService(db)
        self.tank01_service = Tank01Service(db)
        self.picks_getter_service = PicksGetterService(db)
        self.data_integrity_service = DataIntegrityService(db)
        self.stats_enrichment_service = StatsEnrichmentService(db)
        
        # Legacy direct collection access (gradually migrating to repo)
        self.events_cache = db.dg_events_cache
        self.odds_cache = db.dg_odds_cache
        self.player_data = db.dg_player_data
        self.stats_cache = db.dg_stats_cache
        self.sync_log = db.dg_sync_log
        self.trending_cache = db.dg_trending
        self.line_history = db.dg_line_history
        
        # WAREHOUSE MODEL COLLECTIONS (migrating to repo.picks, repo.board)
        self.live_props = db.dg_live_props  # Master props collection (deduplicated)
        self.radar_picks = db.dg_radar_picks  # War Zone top 10 picks
        self.goblin_vault = db.dg_goblin_vault  # Goblin Vault top 10 safe picks
        self.front_lines = db.dg_front_lines  # Front Lines - middle tier picks
        self.parlay_builder = db.dg_parlay_builder  # Big Money Builder parlays
        self.goblin_recon = db.dg_goblin_recon  # Goblin Recon parlays (high-consistency)
        self.cached_board = db.dg_cached_board  # Full cached board for frontend
        self.master_roster = db.dg_master_roster  # SOURCE OF TRUTH: Player-to-team mapping
        self.flagged_players = db.dg_flagged_players  # Players not in master roster (manual review)
        self.player_stats = db.dg_player_stats  # CACHED PLAYER GAME LOGS (synced daily)
        
        # Legacy caching collections
        self.static_shell_cache = db.dg_static_shell
        self.dynamic_lines_cache = db.dg_dynamic_lines
        self.tank01_cache = db.dg_tank01_cache
        self.daily_insights = db.dg_daily_insights  # Advanced analytics cache
        
        # In-memory caches
        self._player_name_map: Dict[str, Any] = {}
        self._injury_data: Dict[str, Any] = {}
        self._news_data: List[Dict] = []
        self._last_sync: Optional[datetime] = None
        self._last_lines_fetch: Optional[datetime] = None
        self._current_date: Optional[str] = None
        self._player_popularity: Dict[str, int] = {}
        self._canonical_names: Dict[str, str] = {}  # Cache for normalized names
        self._master_roster_cache: Dict[str, str] = {}  # In-memory cache for quick lookups
        self._team_pace_cache: Dict[str, float] = {}  # Team pace cache for analytics
        
        # Advanced Analytics Constants (from config)
        from config.settings import (
            LEAGUE_AVG_PACE, VOLATILITY_HIGH_THRESHOLD, 
            VOLATILITY_MED_THRESHOLD, USAGE_REDISTRIBUTION_BASE
        )
        self.LEAGUE_AVG_PACE = LEAGUE_AVG_PACE
        self.B2B_PENALTY = 0.95
        self.THREE_IN_FOUR_PENALTY = 0.92
        self.VOLATILITY_HIGH_THRESHOLD = VOLATILITY_HIGH_THRESHOLD
        self.VOLATILITY_MED_THRESHOLD = VOLATILITY_MED_THRESHOLD
        self.USAGE_REDISTRIBUTION_BASE = USAGE_REDISTRIBUTION_BASE
        
        # Odds API Mapper - will be initialized on first sync
        self._odds_mapper = None
        self._odds_mapper_initialized = False
    
    async def _ensure_odds_mapper_loaded(self) -> bool:
        """
        Ensure the Odds API Mapper is initialized and loaded.
        READ-ONLY - never triggers rebuild during sync.
        
        Returns:
            True if mapper is ready, False otherwise
        """
        if self._odds_mapper_initialized and self._odds_mapper is not None:
            return True
        
        try:
            logger.info("[ODDS_MAPPER] Loading mapper (READ-ONLY)...")
            self._odds_mapper = await init_odds_api_mapper(self.db)
            self._odds_mapper_initialized = True
            
            stats = await self._odds_mapper.getStats()
            logger.info(f"[ODDS_MAPPER] Loaded {stats.get('in_memory_count', 0)} mappings")
            return True
            
        except Exception as e:
            logger.error(f"[ODDS_MAPPER] Failed to load: {e}")
            return False
    
    # ==================== MASTER ROSTER SYNC (SOURCE OF TRUTH) ====================
    
    async def sync_master_roster(self) -> Dict[str, Any]:
        """
        PROXY: Weekly roster sync delegated to RosterService.
        
        Establishes the Source of Truth for player-to-team mapping.
        """
        # Set API keys on service
        self.roster_service.set_api_keys(bdl_key=BDL_API_KEY)
        
        # Delegate to service
        result = await self.roster_service.sync_master_roster()
        
        # Update engine's in-memory cache from service
        if result.get("success"):
            await self.load_master_roster_cache()
        
        return result
    
    async def sync_player_stats(self, player_names: List[str] = None) -> Dict[str, Any]:
        """
        PROXY: Player stats sync delegated to RosterService.
        """
        # Set API key on service
        self.roster_service.set_api_keys(bdl_key=BDL_API_KEY)
        
        # Delegate to service
        return await self.roster_service.sync_player_stats(player_names)
    
    async def _fetch_bdl_stats_for_cache(self, player_name: str) -> Dict[str, Any]:
        """
        Fetch player stats from BallDontLie for caching.
        Returns raw game data without hit rate calculations.
        """
        try:
            player_id = await self._get_bdl_player_id(player_name)
            if not player_id:
                return {}
            
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
                            "games": games,
                            "total_games": len(games),
                            "source": "balldontlie"
                        }
        except Exception as e:
            logger.debug(f"[BDL CACHE] Error fetching {player_name}: {e}")
        
        return {}
    
    async def get_cached_player_stats(self, player_name: str) -> Dict[str, Any]:
        """
        PROXY: Get player stats from MongoDB cache.
        """
        return await self.roster_service.get_cached_player_stats(player_name)
    
    async def get_team_from_master_roster(self, player_name: str) -> Optional[str]:
        """
        PROXY: Look up player's team using RosterService.
        """
        return await self.roster_service.get_player_team(player_name)
    
    async def get_photo_and_team_from_master_roster(self, player_name: str) -> Optional[Dict]:
        """
        PROXY: Get photo and team from master roster delegated to PhotoService.
        """
        return await self.photo_service.get_photo_and_team_from_roster(player_name)
    
    def _get_name_variations(self, first_name: str) -> list:
        """PROXY: Get name variations delegated to PhotoService."""
        return self.photo_service._get_name_variations(first_name)

    async def get_photo_url_from_master_roster(self, player_name: str) -> Optional[str]:
        """
        Get photo URL from Master Hub (SSOT).
        Photos are pre-injected and LOCKED - no external API calls.
        """
        try:
            player = await fetchPlayerIntelByName(player_name)
            if player and player.get("headshot_url"):
                return player.get("headshot_url")
        except Exception as e:
            logger.warning(f"[MASTER HUB] Photo lookup failed for {player_name}: {e}")
        
        # Fallback: Generate ESPN URL using static player ID mapping
        nba_id = get_nba_player_id(player_name)
        if nba_id:
            return f"https://a.espncdn.com/combiner/i?img=/i/headshots/nba/players/full/{nba_id}.png"
        return None
    
    async def refresh_cached_board_photos(self) -> Dict[str, Any]:
        """
        DEPRECATED: All enrichment now happens during sync-to-mongo.
        
        To refresh photos, run the full sync:
        POST /api/v3/sync-to-mongo
        
        This endpoint is kept for backwards compatibility but does nothing.
        """
        logger.warning("[DEPRECATED] refresh_cached_board_photos called - use sync-to-mongo instead")
        return {
            "success": True,
            "message": "DEPRECATED: Use POST /api/v3/sync-to-mongo to refresh data",
            "photos_updated": 0
        }
    
    async def refresh_all_photos(self) -> Dict[str, Any]:
        """
        DEPRECATED: All enrichment now happens during sync-to-mongo.
        
        To refresh photos, run the full sync:
        POST /api/v3/sync-to-mongo
        
        This endpoint is kept for backwards compatibility but does nothing.
        """
        logger.warning("[DEPRECATED] refresh_all_photos called - use sync-to-mongo instead")
        return {
            "success": True,
            "message": "DEPRECATED: Use POST /api/v3/sync-to-mongo to refresh data",
            "total_photos_updated": 0
        }
    
    async def load_master_roster_cache(self):
        """Load the master roster into memory for fast lookups."""
        logger.info("[MASTER ROSTER] Loading roster into memory cache...")
        
        roster = await self.master_roster.find(
            {},
            {"_id": 0, "normalized_name": 1, "team_abbreviation": 1}
        ).to_list(None)
        
        self._master_roster_cache = {
            doc["normalized_name"]: doc["team_abbreviation"]
            for doc in roster
        }
        
        logger.info(f"[MASTER ROSTER] Loaded {len(self._master_roster_cache)} players into cache")
    
    async def flag_unknown_player(self, player_name: str, odds_api_team: str, game_info: Dict):
        """
        PROXY: Flag unknown player delegated to RosterService.
        """
        await self.roster_service.flag_unknown_player(player_name, odds_api_team, game_info)
    
    async def sync_player_photos(self) -> Dict[str, Any]:
        """
        PROXY: Global photo sync delegated to PhotoService.
        """
        # Set API key on service
        self.photo_service.set_api_key(TANK01_API_KEY)
        
        # Delegate to service
        return await self.photo_service.sync_all_photos()
    
    async def sync_active_players_with_photos(self) -> Dict[str, Any]:
        """
        PROXY: Active player sync delegated to PhotoService.
        """
        # Set API key on service
        self.photo_service.set_api_key(TANK01_API_KEY)
        
        # Delegate to service
        return await self.photo_service.sync_active_players_with_photos()
    
    def get_player_photo_url(self, player_name: str, team: str = None, nba_id: int = None) -> Dict[str, str]:
        """Get the best available photo URL for a player.
        PROXY: Delegates to services.utils_service.get_player_photo_url
        """
        from config.settings import TEAM_LOGOS
        
        # Get NBA ID if not provided
        if not nba_id:
            nba_id = NBA_PLAYER_IDS.get(player_name)
        
        result = utils_get_player_photo(player_name, team, nba_id)
        
        # Add fallback from TEAM_LOGOS
        fallback_url = TEAM_LOGOS.get(team, "") if team else ""
        
        return {
            "photo_url": result.get("nba_headshot") or result.get("espn"),
            "fallback_url": fallback_url or result.get("fallback"),
            "has_photo": bool(result.get("nba_headshot") or result.get("espn"))
        }
    
    def get_current_date(self) -> str:
        """Auto-derive today's date from system clock.
        PROXY: Delegates to services.utils_service.get_current_date
        """
        return utils_get_current_date()
    
    # ==================== DATABASE NORMALIZATION ====================
    
    def normalize_team_name(self, team_name: str) -> str:
        """Convert full team name to 3-letter abbreviation.
        PROXY: Delegates to services.utils_service.normalize_team_name
        """
        return utils_normalize_team(team_name)
    
    def sanitize_player_name(self, name: str) -> str:
        """Sanitize and normalize player name for consistent storage.
        PROXY: Delegates to services.utils_service.sanitize_player_name
        """
        # Use instance cache for efficiency
        return utils_sanitize_player(name, self._canonical_names)
    
    def create_composite_key(self, player_name: str, stat_type: str, game_date: str) -> str:
        """Create a unique composite key for deduplication.
        PROXY: Delegates to services.utils_service.create_composite_key
        """
        return utils_create_key(player_name, stat_type, game_date)
    
    # ==================== WAREHOUSE MODEL: SINGLE BATCH SYNC ====================
    
    async def sync_odds_to_mongo(self) -> Dict[str, Any]:
        """
        THE ONLY API CALL - Single batch fetch to MongoDB
        
        DATABASE NORMALIZATION (v2.0):
        1. Team names converted to 3-letter abbreviations (LAL, BKN, etc.)
        2. Player names sanitized and normalized (Nic → Nicolas, etc.)
        3. Composite key: player_name + stat_type + game_date for deduplication
        4. UPSERT mode: Update existing records instead of duplicating
        
        Frontend reads ONLY from MongoDB after this.
        """
        sync_start = datetime.now(timezone.utc)
        self._current_date = self.get_current_date()
        
        logger.info("=" * 70)
        logger.info("[SYNC_ODDS_TO_MONGO] Starting normalized batch sync v2.0...")
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
            "duplicates_prevented": 0,
            "names_normalized": 0,
            "teams_normalized": 0,
            "errors": []
        }
        
        try:
            # Step 0: Load Master Roster cache for team lookups
            await self.load_master_roster_cache()
            
            # Check if master roster exists
            roster_count = await self.master_roster.count_documents({})
            if roster_count == 0:
                logger.warning("[SYNC_ODDS_TO_MONGO] Master roster is empty! Running initial sync...")
                await self.sync_master_roster()
            else:
                logger.info(f"[SYNC_ODDS_TO_MONGO] Master roster loaded: {roster_count} players")
            
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
            seen_players_raw = set()
            seen_players_normalized = set()
            
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
                        seen_players_raw.add(prop.get("player_name"))
                
                # Small delay to avoid rate limiting
                await asyncio.sleep(0.3)
            
            # Step 3: Normalize all props (team names, player names)
            logger.info(f"[NORMALIZATION] Processing {len(all_props)} props...")
            normalized_props = []
            
            for prop in all_props:
                # Normalize team names to 3-letter abbreviations
                original_home = prop.get("home_team", "")
                original_away = prop.get("away_team", "")
                
                prop["home_team"] = self.normalize_team_name(original_home)
                prop["away_team"] = self.normalize_team_name(original_away)
                prop["home_team_full"] = original_home  # Keep original for reference
                prop["away_team_full"] = original_away
                
                if prop["home_team"] != original_home:
                    results["teams_normalized"] += 1
                
                # Normalize player names
                original_name = prop.get("player_name", "")
                normalized_name = self.sanitize_player_name(original_name)
                
                if normalized_name != original_name:
                    results["names_normalized"] += 1
                    logger.debug(f"[NORMALIZE] '{original_name}' → '{normalized_name}'")
                
                prop["player_name"] = normalized_name
                prop["player_name_raw"] = original_name  # Keep original for debugging
                
                seen_players_normalized.add(normalized_name)
                
                # Extract stat type for composite key
                market = prop.get("market", "")
                stat_type = self._extract_stat_type(market)
                
                # Create composite key: player_name + stat_type + line + direction + game_date
                composite_key = f"{normalized_name}|{stat_type}|{prop.get('line', 0)}|{prop.get('direction', '')}|{self._current_date}"
                prop["_composite_key"] = composite_key
                prop["stat_type_extracted"] = stat_type
                prop["game_date"] = self._current_date
                prop["synced_at"] = sync_start.isoformat()
                
                normalized_props.append(prop)
            
            results["unique_players"] = len(seen_players_normalized)
            logger.info(f"[NORMALIZATION] Normalized {results['names_normalized']} names, {results['teams_normalized']} teams")
            logger.info(f"[NORMALIZATION] Raw players: {len(seen_players_raw)} → Normalized: {len(seen_players_normalized)}")
            
            # Step 4: Enrich props with BallDontLie hit rates
            logger.info(f"[SYNC_ODDS_TO_MONGO] Enriching {len(seen_players_normalized)} players with BallDontLie stats...")
            enriched_props = await self._enrich_props_with_stats(normalized_props, list(seen_players_normalized))
            results["stats_enriched"] = len([p for p in enriched_props if p.get("hit_rates")])
            
            # Step 5: Wipe dirty data and insert clean normalized data with UPSERT
            if enriched_props:
                # Clear old data to start fresh (clean slate approach)
                deleted = await self.live_props.delete_many({})
                logger.info(f"[CLEANUP] Wiped {deleted.deleted_count} old records")
                
                # Deduplicate using composite key
                deduplicated = {}
                for prop in enriched_props:
                    key = prop.get("_composite_key", "")
                    if key:
                        if key in deduplicated:
                            results["duplicates_prevented"] += 1
                        # Keep latest version (overwrites duplicates)
                        deduplicated[key] = prop
                
                # Insert deduplicated props
                props_list = list(deduplicated.values())
                for prop in props_list:
                    prop.pop("_id", None)  # Remove any existing _id
                
                if props_list:
                    # Create unique index on composite key for future upserts
                    try:
                        await self.live_props.create_index("_composite_key", unique=True, sparse=True)
                    except Exception:
                        pass  # Index may already exist
                    
                    await self.live_props.insert_many(props_list)
                
                results["total_props"] = len(props_list)
                results["standard_count"] = sum(1 for p in props_list if p.get("prop_type") == "standard")
                results["demons_count"] = sum(1 for p in props_list if p.get("is_demon"))
                results["goblins_count"] = sum(1 for p in props_list if p.get("is_goblin"))
                
                logger.info(f"[SYNC_ODDS_TO_MONGO] Stored {len(props_list)} clean, deduplicated props")
                logger.info(f"[SYNC_ODDS_TO_MONGO] Duplicates prevented: {results['duplicates_prevented']}")
            
            # Step 6: Build cached board for frontend (grouped by player)
            await self._build_cached_board(props_list, sync_start)
            
        except Exception as e:
            logger.error(f"[SYNC_ODDS_TO_MONGO] Error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            results["success"] = False
            results["errors"].append(str(e))
        
        duration = (datetime.now(timezone.utc) - sync_start).total_seconds()
        results["duration_seconds"] = duration
        
        logger.info("=" * 70)
        logger.info(f"[SYNC_ODDS_TO_MONGO] COMPLETE (Normalized v2.0)")
        logger.info(f"  Duration: {duration:.1f}s")
        logger.info(f"  API Calls Made: {results['api_calls_made']}")
        logger.info(f"  Props Stored: {results['total_props']}")
        logger.info(f"  Props Enriched: {results.get('stats_enriched', 0)}")
        logger.info(f"  Players: {results['unique_players']}")
        logger.info(f"  Names Normalized: {results['names_normalized']}")
        logger.info(f"  Teams Normalized: {results['teams_normalized']}")
        logger.info(f"  Duplicates Prevented: {results['duplicates_prevented']}")
        logger.info(f"  Standard: {results['standard_count']} | Demons: {results['demons_count']} | Goblins: {results['goblins_count']}")
        logger.info("=" * 70)
        
        return results
    
    async def _enrich_props_with_stats(self, props: List[Dict], player_names: List[str]) -> List[Dict]:
        """PROXY: Enrich props with stats - delegated to StatsEnrichmentService."""
        return await self.stats_enrichment_service.enrich_props_with_stats(
            props, player_names, self._extract_stat_type, self._calculate_hit_rates
        )
    
    async def _fetch_player_season_stats(self, player_name: str) -> Dict[str, Any]:
        """PROXY: Fetch player season stats - delegated to StatsEnrichmentService."""
        return await self.stats_enrichment_service.fetch_player_season_stats(player_name)
    
    async def _get_bdl_player_id(self, player_name: str) -> Optional[int]:
        """PROXY: Get BDL player ID - delegated to StatsEnrichmentService."""
        return await self.stats_enrichment_service._get_bdl_player_id(player_name)
    
    def _fetch_nba_api_stats(self, player_name: str) -> Dict[str, Any]:
        """PROXY: Fetch NBA API stats - delegated to StatsEnrichmentService."""
        return self.stats_enrichment_service._fetch_nba_api_stats(player_name)
    
    async def _fetch_tank01_player_stats(self, player_name: str) -> Dict[str, Any]:
        """PROXY: Fetch Tank01 player stats - delegated to StatsEnrichmentService."""
        return await self.stats_enrichment_service._fetch_tank01_player_stats(player_name)
    
    def _calculate_hit_rates(self, player_stats: Dict, stat_type: str, line_value: float) -> Dict[str, Any]:
        """
        Calculate hit rates for a specific line.
        PROXY: Delegates to stats_service.calculate_hit_rates
        """
        return stats_calculate_hit_rates(player_stats, stat_type, line_value)
    
    async def _build_cached_board(self, props: List[Dict], sync_time: datetime):
        """
        PROXY: Cached board building delegated to CachedBoardBuilderService.
        """
        # Update the mapper reference in the service
        self.cached_board_builder_service.set_odds_mapper(self._odds_mapper)
        
        return await self.cached_board_builder_service.build_cached_board(
            props, 
            sync_time,
            ensure_mapper_loaded_callback=self._ensure_odds_mapper_loaded
        )
    
    async def _build_cached_board_legacy(self, props: List[Dict], sync_time: datetime):
        """
        PROXY: Legacy cached board building delegated to CachedBoardBuilderService.
        """
        return await self.cached_board_builder_service.build_cached_board_legacy(props, sync_time)
    
    async def _build_war_zone(self, players_dict: Dict[str, Dict], sync_time: datetime):
        """
        PROXY: War Zone tier building delegated to TierBuilderService.
        """
        return await self.tier_builder_service.build_war_zone(players_dict, sync_time)
    
    def _calculate_heat_level(self, h10: float, h5: float, h10_over: int, h5_over: int, h10_games: int, h5_games: int) -> int:
        """
        Calculate Heat Level (1-5 Flames) based on performance.
        PROXY: Delegates to stats_service.calculate_heat_level
        """
        return stats_calculate_heat_level(h10, h5, h10_over, h5_over, h10_games, h5_games)
    
    async def _build_goblin_vault(self, players_dict: Dict[str, Dict], sync_time: datetime):
        """
        PROXY: Safe Haven (Goblin Vault) tier building delegated to TierBuilderService.
        """
        return await self.tier_builder_service.build_goblin_vault(players_dict, sync_time)
    
    def _calculate_safety_level(self, h10: float, h5: float, h10_over: int, h5_over: int, h10_games: int, h5_games: int) -> int:
        """
        Calculate Safety Level (1-5 Shields) based on consistency.
        PROXY: Delegates to stats_service.calculate_safety_level
        """
        return stats_calculate_safety_level(h10, h5, h10_over, h5_over, h10_games, h5_games)
    
    async def _build_front_lines(self, players_dict: Dict[str, Dict], sync_time: datetime):
        """
        PROXY: Front Lines tier building delegated to TierBuilderService.
        """
        return await self.tier_builder_service.build_front_lines(players_dict, sync_time)
    
    def _calculate_bullet_level(self, h10: float, h5: float, h10_over: int, h5_over: int, h10_games: int, h5_games: int) -> int:
        """
        Calculate Bullet Level (1-6 Bullets) based on reliability.
        PROXY: Delegates to stats_service.calculate_bullet_level
        """
        return stats_calculate_bullet_level(h10, h5, h10_over, h5_over, h10_games, h5_games)

    async def _build_parlay_builder(self, players_dict: Dict[str, Dict], sync_time: datetime):
        """
        PROXY: Parlay Builder delegated to ParlayBuilderService.
        """
        return await self.parlay_builder_service.build_parlay_builder(players_dict, sync_time)
    
    async def _build_goblin_recon(self, players_dict: Dict[str, Dict], sync_time: datetime):
        """
        PROXY: Goblin Recon delegated to ParlayBuilderService.
        """
        return await self.parlay_builder_service.build_goblin_recon(players_dict, sync_time)
    
    def _extract_stat_type(self, market: str) -> str:
        """PROXY: Extract stat type from market name - delegates to utils_service."""
        from services.utils_service import extract_stat_type
        return extract_stat_type(market)
    
    async def get_war_zone(self) -> Dict[str, Any]:
        """PROXY: Get War Zone picks - delegated to PicksGetterService."""
        return await self.picks_getter_service.get_war_zone()
    
    async def get_goblin_vault(self) -> Dict[str, Any]:
        """PROXY: Get Goblin Vault picks - delegated to PicksGetterService."""
        return await self.picks_getter_service.get_goblin_vault()
    
    async def get_front_lines(self) -> Dict[str, Any]:
        """PROXY: Get Front Lines picks - delegated to PicksGetterService."""
        return await self.picks_getter_service.get_front_lines()
    
    async def get_parlay_builder(self) -> Dict[str, Any]:
        """PROXY: Get Parlay Builder data - delegated to PicksGetterService."""
        return await self.picks_getter_service.get_parlay_builder()
    
    async def get_goblin_recon(self) -> Dict[str, Any]:
        """PROXY: Get Goblin Recon data - delegated to PicksGetterService."""
        return await self.picks_getter_service.get_goblin_recon()
    
    async def get_cached_board(self) -> Dict[str, Any]:
        """PROXY: Get cached board - delegated to PicksGetterService."""
        return await self.picks_getter_service.get_cached_board()
    
    async def get_cached_player(self, player_name: str) -> Dict[str, Any]:
        """PROXY: Get cached player - delegated to PicksGetterService."""
        return await self.picks_getter_service.get_cached_player(player_name)
    
    # ==================== PILLAR 1: THE ODDS API (PrizePicks) ====================
    
    async def fetch_todays_events(self) -> List[Dict[str, Any]]:
        """PROXY: Fetch all NBA events - delegated to OddsApiService."""
        return await self.odds_api_service.fetch_todays_events()
    
    async def fetch_prizepicks_odds(self, event_id: str, event_info: Dict) -> Dict[str, Any]:
        """PROXY: Fetch PrizePicks odds - delegated to OddsApiService."""
        return await self.odds_api_service.fetch_prizepicks_odds(event_id, event_info)
    
    async def fetch_standard_odds(self, event_id: str, event_info: Dict) -> Dict[str, Any]:
        """PROXY: Fetch standard odds - delegated to OddsApiService."""
        return await self.odds_api_service.fetch_standard_odds(event_id, event_info)
    
    def extract_prizepicks_props(self, odds_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """PROXY: Extract PrizePicks props - delegated to OddsApiService."""
        return self.odds_api_service.extract_prizepicks_props(odds_data)
    
    # ==================== PILLAR 2: BALLDONTLIE API ====================
    
    async def search_bdl_player(self, player_name: str) -> Optional[Dict[str, Any]]:
        """PROXY: Map player name to BDL data - delegated to StatsApiService."""
        return await self.stats_api_service.search_player(player_name)
    
    async def fetch_player_season_stats(self, player_id: int) -> List[Dict[str, Any]]:
        """PROXY: Fetch season stats - delegated to StatsApiService."""
        return await self.stats_api_service.fetch_player_season_stats(player_id)
    
    def calculate_hit_rates(self, games: List[Dict], market: str, line: float) -> Dict[str, Any]:
        """PROXY: Calculate hit rates - delegated to StatsApiService."""
        return self.stats_api_service.calculate_hit_rates(games, market, line)
    
    def _extract_l10_values(self, games: List[Dict], market: str) -> List[float]:
        """PROXY: Extract L10 values - delegated to StatsApiService."""
        return self.stats_api_service.extract_l10_values(games, market)
    
    async def _log_verification_failure(self, player_name: str, failure_type: str, details: Dict[str, Any]):
        """PROXY: Log verification failure - delegated to DataIntegrityService."""
        return await self.data_integrity_service.log_verification_failure(
            player_name, failure_type, details, self.get_current_date()
        )
    
    async def get_data_integrity_status(self) -> Dict[str, Any]:
        """PROXY: Get data integrity status - delegated to DataIntegrityService."""
        return await self.data_integrity_service.get_data_integrity_status(self.get_current_date())
    
    async def verify_player_roster_match(self, player_name: str, player_id: int, team_abbrev: str) -> bool:
        """PROXY: Verify player roster match - delegated to DataIntegrityService."""
        return await self.data_integrity_service.verify_player_roster_match(
            player_name, player_id, team_abbrev
        )
    
    # ==================== PILLAR 3: TANK01 API (with Exponential Backoff) ====================
    
    async def fetch_injuries(self) -> Dict[str, Any]:
        """PROXY: Fetch injury data - delegated to Tank01Service."""
        injuries = await self.tank01_service.fetch_injuries()
        self._injury_data = injuries  # Keep local cache in sync
        return injuries
    
    async def fetch_news(self) -> List[Dict[str, Any]]:
        """PROXY: Fetch news data - delegated to Tank01Service."""
        news = await self.tank01_service.fetch_news()
        self._news_data = news  # Keep local cache in sync
        return news
    
    def get_player_injury_status(self, player_name: str) -> Dict[str, Any]:
        """PROXY: Get player injury status - delegated to Tank01Service."""
        # Sync local caches to service if needed
        if self._injury_data and not self.tank01_service.get_injury_data():
            self.tank01_service.set_injury_data(self._injury_data)
        if self._news_data and not self.tank01_service.get_news_data():
            self.tank01_service.set_news_data(self._news_data)
        return self.tank01_service.get_player_injury_status(player_name)
    
    # ==================== MAIN ORCHESTRATION ====================
    
    async def process_player_prop(self, prop: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a single prop through all three pillars with V3.1 "Truth Engine" verification.
        
        V3.1 NAJI SAFEGUARD:
        - Verify playerID from game logs matches playerID from active daily roster
        - Discard data if mismatch (prevents wrong player stats)
        - Log all discrepancies for audit
        """
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
            "source_verified": False,  # V3.1: Data integrity flag
            "verification_status": "unverified",  # V3.1: Verification status
            "verification_details": {},  # V3.1: Detailed verification info
            "naji_safeguard_passed": None,  # V3.1: Naji Safeguard result
            "processed_at": datetime.now(timezone.utc).isoformat()
        }
        
        # Pillar 2: BallDontLie stats
        bdl_player = await self.search_bdl_player(player_name)
        if bdl_player:
            bdl_player_id = bdl_player.get("id")
            result["bdl_player_id"] = bdl_player_id
            result["bdl_team"] = bdl_player.get("team", {}).get("abbreviation", "")
            result["position"] = bdl_player.get("position", "")
            
            # Convert market name for stats lookup (remove _alternate suffix)
            stat_market = market.replace("_alternate", "")
            
            games = await self.fetch_player_season_stats(bdl_player_id)
            if games:
                # ==================== V3.1 NAJI SAFEGUARD ====================
                # Verify that game log playerIDs match the expected BDL player ID
                # This prevents data from wrong players (e.g., Naji Marshall issue)
                naji_safeguard_passed = True
                mismatched_games = []
                
                for game in games:
                    # BallDontLie game logs contain player reference in "player" field
                    game_player = game.get("player", {})
                    game_player_id = game_player.get("id") if isinstance(game_player, dict) else None
                    
                    # If game log has player ID, verify it matches
                    if game_player_id is not None and game_player_id != bdl_player_id:
                        naji_safeguard_passed = False
                        mismatched_games.append({
                            "expected_id": bdl_player_id,
                            "found_id": game_player_id,
                            "game_date": game.get("game", {}).get("date", "unknown")
                        })
                
                result["naji_safeguard_passed"] = naji_safeguard_passed
                
                if not naji_safeguard_passed:
                    # DISCARD DATA - Player ID mismatch detected
                    result["source_verified"] = False
                    result["verification_status"] = "NAJI_SAFEGUARD_FAILED"
                    result["verification_details"] = {
                        "reason": "Player ID mismatch in game logs",
                        "expected_player_id": bdl_player_id,
                        "mismatched_games": mismatched_games[:5]  # Limit to 5 for log size
                    }
                    logger.error(
                        f"[NAJI SAFEGUARD] FAILED for {player_name}: "
                        f"Expected ID {bdl_player_id}, found mismatched games: {len(mismatched_games)}"
                    )
                    # Store the failure for audit
                    await self._log_verification_failure(player_name, "naji_safeguard", result["verification_details"])
                else:
                    # Naji Safeguard passed - proceed with hit rate calculation
                    hit_rates = self.calculate_hit_rates(games, stat_market, line)
                    result["hit_rates"] = hit_rates
                    
                    # V3.1: Triple-check verification
                    l10_data = self._extract_l10_values(games[:10], stat_market)
                    if l10_data:
                        calculated_hits = sum(1 for v in l10_data if v > line)
                        calculated_rate = (calculated_hits / len(l10_data) * 100) if l10_data else 0
                        claimed_rate = hit_rates.get("l10", {}).get("hit_rate", 0) * 100
                        raw_avg = sum(l10_data) / len(l10_data) if l10_data else 0
                        
                        # Store verification details for audit
                        result["verification_details"] = {
                            "calculated_hits": calculated_hits,
                            "calculated_rate": round(calculated_rate, 2),
                            "claimed_rate": round(claimed_rate, 2),
                            "raw_avg": round(raw_avg, 2),
                            "line": line,
                            "games_analyzed": len(l10_data)
                        }
                        
                        # Verification Gate: Detect hallucinations
                        is_hallucinated = (
                            claimed_rate > 80 and 
                            raw_avg < line and 
                            calculated_rate < 50
                        )
                        
                        major_discrepancy = abs(claimed_rate - calculated_rate) > 20
                        
                        if is_hallucinated or major_discrepancy:
                            result["source_verified"] = False
                            result["verification_status"] = "HALLUCINATION_DETECTED" if is_hallucinated else "DISCREPANCY"
                            logger.warning(
                                f"[VERIFY FAIL] {player_name} {stat_market}: "
                                f"Claimed {claimed_rate:.1f}% vs Calculated {calculated_rate:.1f}% "
                                f"(avg {raw_avg:.1f} vs line {line})"
                            )
                            # Log failure for audit
                            await self._log_verification_failure(player_name, result["verification_status"], result["verification_details"])
                        else:
                            result["source_verified"] = True
                            result["verification_status"] = "verified"
                    else:
                        result["verification_status"] = "no_game_data"
            else:
                result["verification_status"] = "no_games_found"
        
        # Pillar 3: Injury check
        injury_info = self.get_player_injury_status(player_name)
        result["injury_info"] = injury_info
        
        # Special warning: Goblin with high hit rate but Questionable
        if prop.get("is_goblin") and result.get("hit_rates"):
            l10_hit_rate = result["hit_rates"].get("l10", {}).get("hit_rate", 0)
            if l10_hit_rate >= GOBLIN_HIT_RATE_WARNING and injury_info["warning_level"] == "questionable":
                result["has_goblin_warning"] = True
        
        return result
    
    # ==================== ADVANCED ANALYTICS ENGINE v3.1 ====================
    
    def calculate_volatility(self, game_values: List[float]) -> Tuple[str, float]:
        """
        Calculate volatility score from recent game values.
        PROXY: Delegates to services.insights_service.calculate_volatility
        """
        return insights_calculate_volatility(game_values)
    
    def get_team_pace(self, team: str) -> float:
        """Get team's pace (possessions per 48 minutes).
        PROXY: Delegates to services.insights_service.get_team_pace
        """
        if team in self._team_pace_cache:
            return self._team_pace_cache[team]
        pace = insights_get_team_pace(team)
        self._team_pace_cache[team] = pace
        return pace
    
    def calculate_pace_factor(self, team: str, opponent: str) -> float:
        """
        Calculate pace adjustment factor.
        PROXY: Delegates to services.insights_service.calculate_pace_factor
        """
        return insights_calculate_pace_factor(team, opponent)
    
    def get_high_usage_players(self, team: str) -> List[str]:
        """Get list of high-usage players (>25% usage rate) on a team.
        PROXY: Delegates to services.insights_service.get_high_usage_players
        """
        return insights_get_high_usage(team)
    
    def calculate_usage_bump(
        self, 
        player_name: str, 
        team: str,
        injured_teammates: List[str]
    ) -> Tuple[float, List[str]]:
        """
        Calculate usage bump when high-usage teammates are out.
        PROXY: Delegates to services.insights_service.calculate_usage_bump_simple
        """
        return insights_calculate_usage_bump(player_name, team, injured_teammates)
    
    def generate_insight_summary(
        self,
        player_name: str,
        pace_factor: float,
        usage_bump: float,
        volatility: str,
        days_rest: int,
        is_b2b: bool,
        is_3in4: bool,
        injured_teammates: List[str],
        opponent: str
    ) -> str:
        """Generate template-based insight summary.
        PROXY: Delegates to services.insights_service.generate_insight_summary
        """
        return insights_generate_summary(
            player_name, pace_factor, usage_bump, volatility,
            days_rest, is_b2b, is_3in4, injured_teammates, opponent
        )
    
    def calculate_confidence_rating(
        self, 
        density_factor: float, 
        volatility: str, 
        sample_size: int
    ) -> int:
        """Calculate AI confidence rating (0-100).
        PROXY: Delegates to services.insights_service.calculate_confidence_rating
        """
        return insights_calculate_confidence(density_factor, volatility, sample_size)
    
    async def calculate_player_insights(
        self,
        player_name: str,
        team: str,
        opponent: str,
        game_stats: List[Dict],
        stat_type: str = "pts"
    ) -> Dict[str, Any]:
        """
        Calculate all advanced analytics for a player.
        
        Args:
            player_name: Player name
            team: Player's team abbreviation
            opponent: Opponent team abbreviation
            game_stats: List of recent game stats [{pts, reb, ast, ...}, ...]
            stat_type: Which stat to calculate volatility for
        
        Returns:
            Complete insights dictionary
        """
        # Extract stat values for volatility calculation
        stat_key_map = {
            "pts": "pts", "points": "pts",
            "reb": "reb", "rebounds": "reb",
            "ast": "ast", "assists": "ast",
            "fg3m": "fg3m", "3pm": "fg3m", "threes": "fg3m"
        }
        stat_key = stat_key_map.get(stat_type.lower(), "pts")
        
        recent_values = []
        for game in game_stats[:10]:
            val = game.get(stat_key, 0)
            if val is not None:
                recent_values.append(float(val))
        
        # Calculate volatility
        volatility, stddev = self.calculate_volatility(recent_values)
        
        # Calculate pace factor
        pace_factor = self.calculate_pace_factor(team, opponent) if opponent else 1.0
        
        # Get injured teammates (simplified - would need injury API integration)
        # For now, use empty list; will be populated by Tank01 in production
        injured_teammates = []
        
        # Calculate usage bump
        usage_bump, injured_stars = self.calculate_usage_bump(player_name, team, injured_teammates)
        
        # Determine schedule density (simplified)
        # In production, would check actual schedule
        days_rest = 2  # Default
        is_b2b = False
        is_3in4 = False
        density_factor = 1.0
        
        # Generate summary
        summary = self.generate_insight_summary(
            player_name=player_name,
            pace_factor=pace_factor,
            usage_bump=usage_bump,
            volatility=volatility,
            days_rest=days_rest,
            is_b2b=is_b2b,
            is_3in4=is_3in4,
            injured_teammates=injured_stars,
            opponent=opponent or "TBD"
        )
        
        # Calculate confidence
        confidence = self.calculate_confidence_rating(density_factor, volatility, len(recent_values))
        
        return {
            "schedule_density_factor": density_factor,
            "pace_adjustment_factor": pace_factor,
            "usage_bump_percent": usage_bump,
            "volatility_score": volatility,
            "volatility_stddev": stddev,
            "insight_summary": summary,
            "ai_confidence_rating": confidence,
            "is_back_to_back": is_b2b,
            "is_three_in_four": is_3in4,
            "days_rest": days_rest,
            "injured_teammates": injured_stars
        }
    
    async def sync_daily_insights(self) -> Dict[str, Any]:
        """
        Sync daily insights for all players with active props.
        Calculates advanced analytics and stores in MongoDB.
        Should be run daily at 8:00 AM EST.
        """
        sync_start = datetime.now(timezone.utc)
        logger.info("[INSIGHTS SYNC] Starting daily insights calculation...")
        
        insights_calculated = 0
        errors = []
        
        try:
            # Get all players from cached board
            players = await self.cached_board.find({}, {"_id": 0}).to_list(None)
            
            if not players:
                return {"success": True, "insights_calculated": 0, "message": "No players to process"}
            
            logger.info(f"[INSIGHTS SYNC] Processing {len(players)} players...")
            
            for player in players:
                try:
                    player_name = player.get("player_name", "")
                    team = player.get("team", "")
                    
                    # Get opponent from props (if available)
                    opponent = ""
                    if player.get("props"):
                        first_prop = player["props"][0]
                        opponent = first_prop.get("opponent", first_prop.get("away_team", ""))
                    
                    # Get cached stats for this player
                    stats_doc = await self.player_stats.find_one(
                        {"normalized_name": self.sanitize_player_name(player_name)},
                        {"_id": 0}
                    )
                    
                    game_stats = []
                    if stats_doc:
                        game_stats = stats_doc.get("games", [])[:10]
                    
                    # Calculate insights
                    insights = await self.calculate_player_insights(
                        player_name=player_name,
                        team=team,
                        opponent=opponent,
                        game_stats=game_stats,
                        stat_type="pts"
                    )
                    
                    # Add metadata
                    insights["player_name"] = player_name
                    insights["team"] = team
                    insights["opponent"] = opponent
                    insights["synced_at"] = sync_start.isoformat()
                    
                    # Store in MongoDB
                    await self.daily_insights.update_one(
                        {"player_name": player_name},
                        {"$set": insights},
                        upsert=True
                    )
                    
                    insights_calculated += 1
                    
                except Exception as e:
                    errors.append(f"{player.get('player_name', 'Unknown')}: {str(e)}")
            
            # Create indexes
            await self.daily_insights.create_index("player_name", unique=True)
            await self.daily_insights.create_index("team")
            
            duration = (datetime.now(timezone.utc) - sync_start).total_seconds()
            logger.info(f"[INSIGHTS SYNC] Completed: {insights_calculated} players in {duration:.1f}s")
            
            return {
                "success": True,
                "insights_calculated": insights_calculated,
                "duration_seconds": duration,
                "errors": errors[:5],
                "synced_at": sync_start.isoformat()
            }
            
        except Exception as e:
            logger.error(f"[INSIGHTS SYNC] Failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "insights_calculated": insights_calculated
            }
    
    async def get_player_insights(self, player_name: str) -> Optional[Dict[str, Any]]:
        """Get cached insights for a player."""
        doc = await self.daily_insights.find_one(
            {"player_name": player_name},
            {"_id": 0}
        )
        return doc
    
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
            # V3.1 Truth Engine verification stats
            "verification_stats": {
                "verified_count": 0,
                "failed_count": 0,
                "naji_safeguard_failures": 0,
                "hallucinations_detected": 0,
                "discrepancies_found": 0
            },
            "errors": [],
            "duration": 0
        }
        
        try:
            # V3.1 Truth Engine: Clear previous verification failures for today's sync
            await self.db.dg_verification_failures.delete_many({"sync_date": self._current_date})
            logger.info("[TRUTH ENGINE] Cleared previous verification failures for today")
            
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
                        
                        # V3.1 Truth Engine - Track verification stats
                        if processed.get("source_verified"):
                            results["verification_stats"]["verified_count"] += 1
                        else:
                            verification_status = processed.get("verification_status", "")
                            if verification_status == "NAJI_SAFEGUARD_FAILED":
                                results["verification_stats"]["naji_safeguard_failures"] += 1
                                results["verification_stats"]["failed_count"] += 1
                            elif verification_status == "HALLUCINATION_DETECTED":
                                results["verification_stats"]["hallucinations_detected"] += 1
                                results["verification_stats"]["failed_count"] += 1
                            elif verification_status == "DISCREPANCY":
                                results["verification_stats"]["discrepancies_found"] += 1
                                results["verification_stats"]["failed_count"] += 1
                        
                        await asyncio.sleep(0.1)
                        
                    except Exception as e:
                        results["errors"].append(f"Prop error: {str(e)[:50]}")
                
                logger.info(f"  Processed {min(i+batch_size, len(prop_list))}/{len(prop_list)} props")
            
            # ===== STORE RESULTS GROUPED BY PLAYER (VIA ODDS API MAPPER) =====
            logger.info("\n[STORAGE] Organizing data by player via OddsApiMapper...")
            
            # Initialize OddsApiMapper - REQUIRED for player enrichment
            mapper_ready = await self._ensure_odds_mapper_loaded()
            if not mapper_ready or not self._odds_mapper:
                logger.error("[STORAGE] CRITICAL: OddsApiMapper not available!")
                results["errors"].append("OddsApiMapper initialization failed")
            else:
                logger.info("[STORAGE] OddsApiMapper loaded - enriching all players from nba_master_hub_2026")
            
            # Collect unique player names for batch lookup
            unique_players = set(prop.get("player_name", "Unknown") for prop in processed_props)
            logger.info(f"[STORAGE] Found {len(unique_players)} unique players to enrich")
            
            # Batch lookup all players via mapper (pre-fetch for efficiency)
            player_hub_data = {}
            unmatched_players = []
            
            if mapper_ready and self._odds_mapper:
                for player_name in unique_players:
                    # Use getPlayerIdFromOddsName() then getFullPlayerData()
                    player_id = self._odds_mapper.getPlayerIdFromOddsName(player_name)
                    if player_id:
                        hub_data = self._odds_mapper.getFullPlayerData(player_name)
                        if hub_data:
                            player_hub_data[player_name] = hub_data
                        else:
                            unmatched_players.append(player_name)
                    else:
                        unmatched_players.append(player_name)
                
                logger.info(f"[STORAGE] Mapper matched: {len(player_hub_data)}/{len(unique_players)} players")
                if unmatched_players:
                    logger.warning(f"[STORAGE] Unmatched players ({len(unmatched_players)}): {unmatched_players[:10]}...")
            
            # Group props by player with full Hub enrichment
            player_data = {}
            for prop in processed_props:
                player_name = prop.get("player_name", "Unknown")
                
                if player_name not in player_data:
                    # Get pre-fetched Hub data
                    hub_player = player_hub_data.get(player_name)
                    
                    if hub_player:
                        # FULL ENRICHMENT from nba_master_hub_2026 via OddsApiMapper
                        player_data[player_name] = {
                            # PRIMARY IDENTIFIERS (from Hub)
                            "player_name": player_name,
                            "player_id": hub_player.get("player_id"),  # PRIMARY KEY
                            "espn_id": hub_player.get("espn_id"),
                            "nba_id": hub_player.get("nba_id"),
                            "tank01_id": hub_player.get("tank01_id"),
                            
                            # TEAM INFO (from Hub)
                            "team": hub_player.get("team") or prop.get("bdl_team", ""),
                            "team_name": hub_player.get("team_name"),
                            
                            # LOCKED PHOTO URL (from Hub - definitive source)
                            "photo_url": hub_player.get("headshot_url"),
                            "headshot_url": hub_player.get("headshot_url"),
                            "photo_source": "nba_master_hub_2026",
                            "photo_locked": hub_player.get("photo_locked", True),
                            
                            # PLAYER INFO (from Hub)
                            "position": hub_player.get("position") or prop.get("position", ""),
                            "jersey": hub_player.get("jersey"),
                            
                            # STATS (from Hub)
                            "season_avg": hub_player.get("stats", {}).get("season_avg", {}),
                            
                            # CONTEXTUAL DATA
                            "injury_info": prop.get("injury_info", {}),
                            "popularity_order": self._player_popularity.get(player_name, 999),
                            
                            # PROP CONTAINERS
                            "props": [],
                            "standard": [],
                            "demons": [],
                            "goblins": [],
                            "has_goblin_warning": False,
                            "has_new_injury": False,
                            
                            # VERIFICATION FLAGS
                            "is_mapper_matched": True,
                            "is_verified": True
                        }
                    else:
                        # FALLBACK: Player not in Hub (rare edge case)
                        # NO call to get_photo_url_from_master_roster - Hub is definitive
                        logger.debug(f"[STORAGE] Player not in Hub: {player_name}")
                        player_data[player_name] = {
                            "player_name": player_name,
                            "player_id": None,
                            "team": prop.get("bdl_team", ""),
                            "position": prop.get("position", ""),
                            "photo_url": None,
                            "headshot_url": None,
                            "injury_info": prop.get("injury_info", {}),
                            "popularity_order": self._player_popularity.get(player_name, 999),
                            "props": [],
                            "standard": [],
                            "demons": [],
                            "goblins": [],
                            "has_goblin_warning": False,
                            "has_new_injury": False,
                            "is_mapper_matched": False,
                            "is_verified": False
                        }
                
                # Add prop to player
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
                
                # Calculate opponent from home_team/away_team (do once per player)
                if not player_data[player_name].get("opponent"):
                    home_team = prop.get("home_team")
                    away_team = prop.get("away_team")
                    player_team = player_data[player_name].get("team")
                    
                    if player_team and home_team and away_team:
                        if player_team == home_team:
                            player_data[player_name]["opponent"] = away_team
                            player_data[player_name]["opponent_abbr"] = away_team
                        elif player_team == away_team:
                            player_data[player_name]["opponent"] = home_team
                            player_data[player_name]["opponent_abbr"] = home_team
                        else:
                            # Fallback: assign from game matchup
                            player_data[player_name]["opponent"] = away_team if home_team else None
                            player_data[player_name]["opponent_abbr"] = away_team if home_team else None
            
            # Log final enrichment stats
            mapper_matched = sum(1 for p in player_data.values() if p.get("is_mapper_matched"))
            with_player_id = sum(1 for p in player_data.values() if p.get("player_id"))
            with_photo = sum(1 for p in player_data.values() if p.get("headshot_url"))
            
            logger.info(f"[STORAGE] ENRICHMENT COMPLETE:")
            logger.info(f"  Total Players: {len(player_data)}")
            logger.info(f"  Mapper Matched: {mapper_matched}")
            logger.info(f"  With player_id: {with_player_id}")
            logger.info(f"  With headshot_url: {with_photo}")
            
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
            
            # ===== BUILD WAR ZONE & VAULT (Top 10 Picks) =====
            logger.info("\n[WAR ZONE/VAULT] Building top 10 pick sections...")
            
            # Build War Zone (Top 10 Demon Picks)
            try:
                await self._build_war_zone(player_data, sync_start)
                logger.info("[WAR ZONE] Rebuilt successfully")
            except Exception as e:
                logger.error(f"[WAR ZONE] Error building: {e}")
            
            # Build Front Lines (Top 6 Middle-Tier Picks)
            try:
                await self._build_front_lines(player_data, sync_start)
                logger.info("[FRONT LINES] Rebuilt successfully")
            except Exception as e:
                logger.error(f"[FRONT LINES] Error building: {e}")
            
            # Build Goblin Vault (Top 10 Safe Picks)
            try:
                await self._build_goblin_vault(player_data, sync_start)
                logger.info("[GOBLIN VAULT] Rebuilt successfully")
            except Exception as e:
                logger.error(f"[GOBLIN VAULT] Error building: {e}")
            
            # ===== BUILD PARLAY GENERATORS =====
            logger.info("\n[PARLAYS] Building parlay generators...")
            
            # Build demon parlays (The Gauntlet)
            try:
                await self._build_parlay_builder(player_data, sync_start)
            except Exception as e:
                logger.error(f"[PARLAY BUILDER] Error building demon parlays: {e}")
            
            # Build goblin parlays (The Safe Haven)
            try:
                await self._build_goblin_recon(player_data, sync_start)
            except Exception as e:
                logger.error(f"[GOBLIN RECON] Error building goblin parlays: {e}")
            
            logger.info("[PARLAYS] Parlay generators built successfully")
            
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
        
        # Calculate verification rate
        total_verifiable = results["verification_stats"]["verified_count"] + results["verification_stats"]["failed_count"]
        verification_rate = (results["verification_stats"]["verified_count"] / total_verifiable * 100) if total_verifiable > 0 else 0
        results["verification_stats"]["verification_rate"] = round(verification_rate, 2)
        
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

V3.1 TRUTH ENGINE - DATA INTEGRITY:
  Verified Props: {results['verification_stats']['verified_count']}
  Failed Props: {results['verification_stats']['failed_count']}
  Naji Safeguard Failures: {results['verification_stats']['naji_safeguard_failures']}
  Hallucinations Detected: {results['verification_stats']['hallucinations_detected']}
  Discrepancies Found: {results['verification_stats']['discrepancies_found']}
  Verification Rate: {results['verification_stats']['verification_rate']}%
""")
        logger.info("=" * 70)
        
        return results
    
    async def run_delta_sync(self) -> Dict[str, Any]:
        """
        DELTA SYNC - Odds-only update for Delta Refreshes
        
        Updates line and price values for existing players without
        re-fetching stats or regenerating Vision AI.
        
        Used by Board Intelligence Engine for:
        - 1:45 PM, 4:00 PM, 5:45 PM, 7:00 PM ET refreshes
        """
        sync_start = datetime.now(timezone.utc)
        self._current_date = self.get_current_date()
        
        logger.info("─" * 70)
        logger.info(f"DELTA SYNC - ODDS ONLY UPDATE")
        logger.info(f"Date: {self._current_date}")
        logger.info("─" * 70)
        
        results = {
            "success": True,
            "sync_type": "delta",
            "sync_date": self._current_date,
            "sync_time": sync_start.isoformat(),
            "lines_updated": 0,
            "new_players": [],
            "removed_players": [],
            "errors": []
        }
        
        try:
            # Get existing players before update
            existing_board = await self.dg_cached_board.find_one({"type": "main_board"})
            existing_players = set()
            if existing_board and "board" in existing_board:
                for p in existing_board["board"].get("players", []):
                    existing_players.add(p.get("player_name", ""))
            
            # Fetch fresh events and odds (PILLAR 1 only)
            logger.info("\n[DELTA] Fetching fresh odds from PrizePicks...")
            events = await self.fetch_todays_events()
            
            if not events:
                results["success"] = False
                results["errors"].append("No NBA events found")
                return results
            
            all_props = []
            all_players = set()
            
            for event in events:
                # Fetch PrizePicks odds for each event
                props = await self.fetch_prizepicks_odds(event)
                if props:
                    all_props.extend(props)
                    for prop in props:
                        all_players.add(prop.get("player_name", ""))
            
            logger.info(f"[DELTA] Fetched {len(all_props)} props for {len(all_players)} players")
            
            # Identify new and removed players
            new_players = all_players - existing_players
            removed_players = existing_players - all_players
            
            results["new_players"] = list(new_players)
            results["removed_players"] = list(removed_players)
            
            if new_players:
                logger.info(f"[DELTA] New players: {list(new_players)[:5]}...")
            if removed_players:
                logger.info(f"[DELTA] Removed players: {list(removed_players)[:5]}...")
            
            # Update existing players' odds in the cached board
            if existing_board and "board" in existing_board:
                players_list = existing_board["board"].get("players", [])
                
                # Create lookup for new props by player
                props_by_player = {}
                for prop in all_props:
                    pname = prop.get("player_name", "")
                    if pname not in props_by_player:
                        props_by_player[pname] = []
                    props_by_player[pname].append(prop)
                
                # Update each player's props with fresh odds
                for player in players_list:
                    pname = player.get("player_name", "")
                    if pname in props_by_player:
                        new_props = props_by_player[pname]
                        
                        # Update standard props
                        for old_prop in player.get("props", []):
                            for new_prop in new_props:
                                if (old_prop.get("market") == new_prop.get("market") and
                                    old_prop.get("direction") == new_prop.get("direction")):
                                    old_prop["line"] = new_prop.get("line", old_prop.get("line"))
                                    old_prop["price"] = new_prop.get("price", old_prop.get("price"))
                                    results["lines_updated"] += 1
                                    break
                        
                        # Update demons
                        for old_demon in player.get("demons", []):
                            for new_prop in new_props:
                                if (old_demon.get("market") == new_prop.get("market") and
                                    old_demon.get("direction") == new_prop.get("direction") and
                                    new_prop.get("is_demon")):
                                    old_demon["line"] = new_prop.get("line", old_demon.get("line"))
                                    old_demon["price"] = new_prop.get("price", old_demon.get("price"))
                                    results["lines_updated"] += 1
                                    break
                        
                        # Update goblins
                        for old_goblin in player.get("goblins", []):
                            for new_prop in new_props:
                                if (old_goblin.get("market") == new_prop.get("market") and
                                    old_goblin.get("direction") == new_prop.get("direction") and
                                    new_prop.get("is_goblin")):
                                    old_goblin["line"] = new_prop.get("line", old_goblin.get("line"))
                                    old_goblin["price"] = new_prop.get("price", old_goblin.get("price"))
                                    results["lines_updated"] += 1
                                    break
                
                # Remove players whose lines were pulled
                if removed_players:
                    players_list = [p for p in players_list if p.get("player_name") not in removed_players]
                    existing_board["board"]["players"] = players_list
                
                # Update the board
                existing_board["board"]["delta_updated_at"] = sync_start.isoformat()
                await self.dg_cached_board.update_one(
                    {"type": "main_board"},
                    {"$set": existing_board}
                )
            
            logger.info(f"[DELTA] Updated {results['lines_updated']} lines")
            
            # Rebuild Demon Radar and Goblin Vault with updated data
            if existing_board and "board" in existing_board:
                players_list = existing_board["board"].get("players", [])
                
                # Convert players_list to players_dict format for radar/vault builders
                player_data = {}
                for player in players_list:
                    pname = player.get("player_name", "")
                    if pname:
                        player_data[pname] = player
                
                if player_data:
                    logger.info("[DELTA] Rebuilding War Zone, Front Lines, and Goblin Vault...")
                    try:
                        await self._build_war_zone(player_data, sync_start)
                        logger.info("[WAR ZONE] Rebuilt with fresh data")
                    except Exception as e:
                        logger.error(f"[WAR ZONE] Rebuild error: {e}")
                    
                    try:
                        await self._build_front_lines(player_data, sync_start)
                        logger.info("[FRONT LINES] Rebuilt with fresh data")
                    except Exception as e:
                        logger.error(f"[FRONT LINES] Rebuild error: {e}")
                    
                    try:
                        await self._build_goblin_vault(player_data, sync_start)
                        logger.info("[GOBLIN VAULT] Rebuilt with fresh data")
                    except Exception as e:
                        logger.error(f"[GOBLIN VAULT] Rebuild error: {e}")
            
        except Exception as e:
            logger.error(f"[DELTA] Sync error: {e}")
            results["success"] = False
            results["errors"].append(str(e))
        
        results["duration"] = (datetime.now(timezone.utc) - sync_start).total_seconds()
        
        logger.info(f"[DELTA] Sync completed in {results['duration']:.1f}s")
        logger.info("─" * 70)
        
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

    async def get_most_popular_bets(self) -> Dict[str, Any]:
        """
        Get Top 20 Most Popular BETS (specific props, not just players)
        Returns actual bet lines with ticket volume/popularity scoring
        Includes Standard, Demon, and Goblin lines
        Auto-purges games that have already started
        
        48-HOUR HORIZON: Pulls from both cached_board AND odds_cache
        to ensure we always have upcoming games even if board isn't synced
        """
        try:
            now = datetime.now(timezone.utc)
            now_epoch = now.timestamp()  # Convert to Unix epoch for precise comparison
            horizon_48h = now + timedelta(hours=48)  # Look 48 hours ahead
            popular_bets = []
            games_filtered = 0
            games_included = 0
            
            # STRATEGY 1: Get bets from cached board (fully processed with hit rates)
            cursor = self.cached_board.find({}, {"_id": 0})
            players = await cursor.to_list(None)
            
            for player in players:
                player_name = player.get("player_name", "")
                team = player.get("team", "")
                photo_url = player.get("photo_url", "")
                
                props = player.get("props", [])
                for prop in props:
                    # STRICT LIVE FILTER: Only show bets that are CURRENTLY BETTABLE
                    commence_time_str = prop.get("commence_time")
                    
                    if not commence_time_str:
                        continue
                    
                    try:
                        commence_time = datetime.fromisoformat(commence_time_str.replace('Z', '+00:00'))
                        game_epoch = commence_time.timestamp()
                        
                        # Game must NOT have started yet
                        if game_epoch <= now_epoch:
                            games_filtered += 1
                            continue
                            
                        games_included += 1
                    except Exception as e:
                        logger.warning(f"[MOST_POPULAR] Failed to parse commence_time: {commence_time_str} - {e}")
                        continue
                    
                    # Calculate popularity score - TYPE AGNOSTIC (no demon/goblin boost)
                    # Uses popularity_order from PrizePicks if available (actual volume proxy)
                    # Otherwise uses hash-based score for variety
                    hit_rates = prop.get("hit_rates", {}) or {}
                    l10_data = hit_rates.get("l10", {}) or {}
                    h10_rate = l10_data.get("hit_rate", 0) or 0
                    
                    is_demon = prop.get("is_demon", False)
                    is_goblin = prop.get("is_goblin", False)
                    line_type = "demon" if is_demon else "goblin" if is_goblin else "standard"
                    
                    # PURE VOLUME SCORING - No type bias
                    # popularity_order from PrizePicks indicates actual ticket volume (lower = more popular)
                    pp_order = prop.get("popularity_order", 999)
                    volume_score = max(0, 100 - pp_order)  # Convert to descending score
                    
                    # Add some variety with hit rate and hash
                    hit_rate_score = h10_rate * 0.3
                    hash_variety = (hash(player_name + str(prop.get("line", 0))) % 15)
                    
                    popularity_score = volume_score + hit_rate_score + hash_variety
                    
                    line = prop.get("demon_line") or prop.get("goblin_line") or prop.get("line")
                    stat_type = prop.get("stat_type") or prop.get("stat_type_extracted") or prop.get("market", "").replace("player_", "").upper()
                    
                    popular_bets.append({
                        "player_name": player_name,
                        "team": team,
                        "photo_url": photo_url,
                        "stat_type": stat_type,
                        "line": line,
                        "line_type": line_type,
                        "is_demon": is_demon,
                        "is_goblin": is_goblin,
                        "direction": prop.get("direction", "over").lower(),
                        "h10_rate": h10_rate,
                        "h5_rate": (hit_rates.get("l5", {}) or {}).get("hit_rate", 0) or 0,
                        "gap_pct": prop.get("gap_pct", 0),
                        "popularity_score": round(popularity_score, 1),
                        "odds": prop.get("demon_odds") if is_demon else prop.get("goblin_odds") if is_goblin else prop.get("odds"),
                        "commence_time": commence_time_str,
                        "home_team": prop.get("home_team", ""),
                        "away_team": prop.get("away_team", ""),
                        "event_id": prop.get("event_id", ""),
                        "source": "cached_board"
                    })
            
            # STRATEGY 2: If we have <20 bets, supplement from odds_cache for upcoming games
            if len(popular_bets) < 20:
                logger.info(f"[MOST_POPULAR] Only {len(popular_bets)} from cached_board, checking odds_cache for upcoming games...")
                
                # Get upcoming events from events cache
                events_cursor = self.events_cache.find({}, {"_id": 0})
                events = await events_cursor.to_list(None)
                
                for event in events:
                    event_commence = event.get("commence_time", "")
                    if not event_commence:
                        continue
                    
                    try:
                        event_time = datetime.fromisoformat(event_commence.replace('Z', '+00:00'))
                        event_epoch = event_time.timestamp()
                        
                        # Skip if game already started or too far in future
                        if event_epoch <= now_epoch:
                            continue
                        if event_time > horizon_48h:
                            continue
                            
                    except:
                        continue
                    
                    event_id = event.get("id")
                    home_team = event.get("home_team", "")
                    away_team = event.get("away_team", "")
                    
                    # Get odds for this event
                    odds_doc = await self.odds_cache.find_one({"event_id": event_id}, {"_id": 0})
                    if not odds_doc:
                        continue
                    
                    # Parse bookmaker data
                    for bookmaker in odds_doc.get("bookmakers", []):
                        for market in bookmaker.get("markets", []):
                            market_key = market.get("key", "")
                            is_alternate = "alternate" in market_key.lower()
                            
                            # Extract stat type from market key
                            stat_type_raw = market_key.replace("player_", "").replace("_alternate", "").upper()
                            stat_map = {
                                "POINTS": "PTS", "REBOUNDS": "REB", "ASSISTS": "AST",
                                "THREES": "3PM", "STEALS": "STL", "BLOCKS": "BLK",
                                "TURNOVERS": "TO", "DOUBLE_DOUBLES": "DD",
                                "POINTS_REBOUNDS": "P+R", "POINTS_ASSISTS": "P+A",
                                "REBOUNDS_ASSISTS": "R+A", "POINTS_REBOUNDS_ASSISTS": "PRA"
                            }
                            stat_type = stat_map.get(stat_type_raw, stat_type_raw[:3])
                            
                            for outcome in market.get("outcomes", []):
                                player_name = outcome.get("description", "")
                                if not player_name:
                                    continue
                                
                                line = outcome.get("point")
                                price = outcome.get("price", 0)
                                direction = outcome.get("name", "Over").lower()
                                
                                # Classify: alternate + price=100 = demon, alternate + price!=100 = goblin
                                is_demon = is_alternate and price == 100
                                is_goblin = is_alternate and price != 100
                                line_type = "demon" if is_demon else "goblin" if is_goblin else "standard"
                                
                                # PURE VOLUME SCORING - No type bias for odds_cache entries
                                # Use hash-based variety to simulate ticket volume distribution
                                hash_variety = (hash(player_name + stat_type + str(line)) % 50)
                                popularity_score = hash_variety + 20  # Base score + variety
                                
                                # Check if we already have this bet from cached_board
                                existing = any(
                                    b["player_name"] == player_name and 
                                    b["stat_type"] == stat_type and 
                                    b["line"] == line 
                                    for b in popular_bets
                                )
                                if existing:
                                    continue
                                
                                popular_bets.append({
                                    "player_name": player_name,
                                    "team": "",  # Not available in odds_cache
                                    "photo_url": "",  # Not available
                                    "stat_type": stat_type,
                                    "line": line,
                                    "line_type": line_type,
                                    "is_demon": is_demon,
                                    "is_goblin": is_goblin,
                                    "direction": direction,
                                    "h10_rate": 0,  # Not available without full sync
                                    "h5_rate": 0,
                                    "gap_pct": 0,
                                    "popularity_score": round(popularity_score, 1),
                                    "odds": price,
                                    "commence_time": event_commence,
                                    "home_team": home_team,
                                    "away_team": away_team,
                                    "event_id": event_id,
                                    "source": "odds_cache"
                                })
            
            # Sort by popularity score (descending) and take top 20
            popular_bets.sort(key=lambda x: x["popularity_score"], reverse=True)
            top_20 = popular_bets[:20]
            
            # ENRICH: Add photos and team info for bets missing them (from odds_cache)
            # Build a lookup from cached_board for player metadata
            player_metadata = {}
            for player in players:
                pname = player.get("player_name", "")
                if pname:
                    player_metadata[pname.lower()] = {
                        "photo_url": player.get("photo_url", ""),
                        "team": player.get("team", "")
                    }
            
            # Also check player_data collection for more photos
            player_data_cursor = self.player_data.find({}, {"_id": 0, "player_name": 1, "photo_url": 1, "team": 1})
            player_data_list = await player_data_cursor.to_list(None)
            for pd in player_data_list:
                pname = pd.get("player_name", "")
                if pname and pname.lower() not in player_metadata:
                    player_metadata[pname.lower()] = {
                        "photo_url": pd.get("photo_url", ""),
                        "team": pd.get("team", "")
                    }
            
            # Enrich top 20 with missing metadata
            for bet in top_20:
                if not bet.get("photo_url") or not bet.get("team"):
                    pname_lower = bet["player_name"].lower()
                    if pname_lower in player_metadata:
                        if not bet.get("photo_url"):
                            bet["photo_url"] = player_metadata[pname_lower].get("photo_url", "")
                        if not bet.get("team"):
                            bet["team"] = player_metadata[pname_lower].get("team", "")
            
            board_count = sum(1 for b in top_20 if b.get("source") == "cached_board")
            odds_count = sum(1 for b in top_20 if b.get("source") == "odds_cache")
            
            logger.info(f"[MOST_POPULAR] Live filter: {games_included} upcoming from board, {games_filtered} tipped-off filtered")
            logger.info(f"[MOST_POPULAR] Final mix: {board_count} from cached_board, {odds_count} from odds_cache")
            
            return {
                "success": True,
                "count": len(top_20),
                "total_live_bets": len(popular_bets),
                "games_filtered": games_filtered,
                "board_source_count": board_count,
                "odds_source_count": odds_count,
                "last_updated": now.isoformat(),
                "status": "live" if len(top_20) > 0 else "awaiting_action",
                "bets": top_20
            }
            
        except Exception as e:
            logger.error(f"[MOST_POPULAR] Error getting popular bets: {e}")
            import traceback
            traceback.print_exc()
            return {
                "success": True,
                "count": 0,
                "total_live_bets": 0,
                "games_filtered": 0,
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "status": "awaiting_action",
                "bets": [],
                "error": str(e)
            }

    
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

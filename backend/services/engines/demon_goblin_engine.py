"""
Demon & Goblin Analytics Engine v3.2
=====================================

PrizePicks-Specific System for NBA Player Props

ARCHITECTURE RESET (v3.2):
- Single source of truth: All data enrichment happens during sync
- Dumb components: War Zone, Goblin Recon, Gauntlet, Safe Haven just read data
- BDL playerID as primary key
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
3. BDL API - Injury reports and player news

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
from services.engines.payout_engine import (
    calculate_payout_from_picks,
    calculate_leg_modifier,
    estimate_payout,
    AssetType,
    BASE_MULTIPLIERS
)

# NBA Master Hub - SINGLE SOURCE OF TRUTH
from services.engines.nba_master_hub import fetchPlayerIntel, fetchPlayerIntelByName, get_master_hub

# Odds API Mapper - Permanent player name to ID mapping
from services.odds_api_mapper import get_odds_api_mapper, init_odds_api_mapper

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

ODDS_API_KEY = os.environ.get("ODDS_API_KEY")
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

BDL_API_KEY = os.environ.get("BDL_API_KEY")
BDL_BASE_URL = "https://api.balldontlie.io/v1"

# NOTE: BDL is the only stats source. All stats come from BDL.

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
            OddsApiService, StatsApiService, PicksGetterService,
            DataIntegrityService, StatsEnrichmentService, OddsSyncService,
            PropProcessorService, InsightsSyncService
        )
        from services.sync_orchestration_service import SyncOrchestrationService
        from services.bdl_comprehensive_sync import get_bdl_sync_service
        
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
        self.bdl_service = get_bdl_sync_service(db)  # BDL sync service (BDL Only)
        self.picks_getter_service = PicksGetterService(db)
        self.data_integrity_service = DataIntegrityService(db)
        self.stats_enrichment_service = StatsEnrichmentService(db)
        self.odds_sync_service = OddsSyncService(db)
        self.sync_orchestration_service = SyncOrchestrationService(db)
        self.sync_orchestration_service.set_engine(self)
        self.prop_processor_service = PropProcessorService(db)
        self.prop_processor_service.set_engine(self)
        self.insights_sync_service = InsightsSyncService(db)
        self.insights_sync_service.set_engine(self)
        
        # Ferrari Tier Service - Best of Best filtering
        from services.ferrari_tier_service import FerrariTierService
        self.ferrari_tier_service = FerrariTierService(db)
        
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
        self.bdl_cache = db.dg_bdl_cache
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
        # Photo sync uses NBA CDN - uses NBA CDN
        return await self.photo_service.sync_all_photos()
    
    async def sync_active_players_with_photos(self) -> Dict[str, Any]:
        """
        PROXY: Active player sync delegated to PhotoService.
        """
        # Photo sync uses NBA CDN - uses NBA CDN
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
        PROXY: Main sync orchestration - delegated to OddsSyncService.
        THE ONLY API CALL - Single batch fetch to MongoDB.
        """
        return await self.odds_sync_service.sync_odds_to_mongo(
            get_current_date=self.get_current_date,
            load_master_roster_cache=self.load_master_roster_cache,
            fetch_todays_events=self.fetch_todays_events,
            fetch_prizepicks_odds=self.fetch_prizepicks_odds,
            extract_prizepicks_props=self.extract_prizepicks_props,
            normalize_team_name=self.normalize_team_name,
            sanitize_player_name=self.sanitize_player_name,
            extract_stat_type=self._extract_stat_type,
            enrich_props_with_stats=self._enrich_props_with_stats,
            build_cached_board=self._build_cached_board,
            sync_master_roster=self.sync_master_roster,
            fetch_sharp_book_odds=self.fetch_sharp_book_odds,  # Phase 2
            build_ferrari_tiers=self._build_ferrari_tiers  # Phase 3
        )
    
    async def _build_ferrari_tiers(self, sync_time: datetime) -> Dict[str, Any]:
        """
        PROXY: Ferrari tier building delegated to FerrariTierService.
        Applies Bovada separation filtering for Best of Best picks.
        """
        return await self.ferrari_tier_service.build_ferrari_tiers(sync_time)
    
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
    
    async def _fetch_bdl_player_stats(self, player_name: str) -> Dict[str, Any]:
        """PROXY: Fetch BDL player stats - delegated to StatsEnrichmentService."""
        return await self.stats_enrichment_service._fetch_bdl_player_stats(player_name)
    
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
        # Ensure mapper is loaded first
        await self._ensure_odds_mapper_loaded()
        
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
    
    # STATIC CACHE METHODS - Zero JIT calculations
    async def get_war_zone_static(self) -> Dict[str, Any]:
        """STATIC: Simple MongoDB read for War Zone - no calculations."""
        return await self.picks_getter_service.get_war_zone_static()
    
    async def get_goblin_vault_static(self) -> Dict[str, Any]:
        """STATIC: Simple MongoDB read for Safe Haven - no calculations."""
        return await self.picks_getter_service.get_goblin_vault_static()
    
    async def get_front_lines_static(self) -> Dict[str, Any]:
        """STATIC: Simple MongoDB read for Front Lines - no calculations."""
        return await self.picks_getter_service.get_front_lines_static()
    
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
    
    async def fetch_sharp_book_odds(self, event_id: str, event_info: Dict) -> Dict[str, Any]:
        """PROXY: Fetch Sharp Book odds (Pinnacle/DraftKings) - delegated to OddsApiService."""
        return await self.odds_api_service.fetch_sharp_book_odds(event_id, event_info)
    
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
    
    # ==================== INJURIES & NEWS (ESPN + BDL - BDL Only) ====================
    
    async def fetch_injuries(self) -> Dict[str, Any]:
        """Fetch injury data from ESPN + BDL."""
        # Injuries are synced via injury_service.py and stored in dg_injuries + bdl_injuries
        cursor = self.db.bdl_injuries.find({}, {"_id": 0})
        injuries = {}
        async for inj in cursor:
            name = inj.get("player_name", "").lower()
            injuries[name] = {
                "status": inj.get("status"),
                "team": inj.get("team"),
                "severity": inj.get("severity"),
                "source": "bdl"
            }
        self._injury_data = injuries
        return injuries
    
    async def fetch_news(self) -> List[Dict[str, Any]]:
        """Fetch news data from ESPN."""
        # News is synced via injury_service.py breaking news endpoint
        cursor = self.db.dg_breaking_news.find({}, {"_id": 0}).limit(50)
        news = await cursor.to_list(50)
        self._news_data = news
        return news
    
    def get_player_injury_status(self, player_name: str) -> Dict[str, Any]:
        """Get injury status for a specific player."""
        if not self._injury_data:
            return {"is_injured": False}
        
        name_lower = player_name.lower()
        injury = self._injury_data.get(name_lower)
        
        if injury:
            return {
                "is_injured": True,
                **injury
            }
        return {"is_injured": False}
    
    # ==================== MAIN ORCHESTRATION ====================
    
    async def process_player_prop(self, prop: Dict[str, Any]) -> Dict[str, Any]:
        """Process a single prop through all three pillars. Delegated to PropProcessorService."""
        return await self.prop_processor_service.process_player_prop(prop)
    
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
        """Calculate all advanced analytics for a player. Delegated to InsightsSyncService."""
        return await self.insights_sync_service.calculate_player_insights(
            player_name, team, opponent, game_stats, stat_type
        )
    
    async def sync_daily_insights(self) -> Dict[str, Any]:
        """Sync daily insights for all players. Delegated to InsightsSyncService."""
        return await self.insights_sync_service.sync_daily_insights()
    
    async def get_player_insights(self, player_name: str) -> Optional[Dict[str, Any]]:
        """Get cached insights for a player. Delegated to InsightsSyncService."""
        return await self.insights_sync_service.get_player_insights(player_name)
    
    async def run_full_sync(self) -> Dict[str, Any]:
        """Execute the full three-pillar sync with PrizePicks data. Delegated to SyncOrchestrationService."""
        self._current_date = self.get_current_date()
        result = await self.sync_orchestration_service.run_full_sync(
            current_date=self._current_date,
            prizepicks_region=PRIZEPICKS_REGION,
            prizepicks_bookmaker=PRIZEPICKS_BOOKMAKER
        )
        self._last_sync = datetime.now(timezone.utc)
        return result
    
    async def run_delta_sync(self) -> Dict[str, Any]:
        """DELTA SYNC - Odds-only update. Delegated to SyncOrchestrationService."""
        self._current_date = self.get_current_date()
        return await self.sync_orchestration_service.run_delta_sync(self._current_date)
    
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
        """Proxy to PicksGetterService.get_most_popular_bets()"""
        return await self.picks_getter_service.get_most_popular_bets()

    
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
                
                odds_url = f"{ODDS_API_BASE}/sports/basketball_nba/events/{event_id}/odds"
                
                async with httpx.AsyncClient() as client:
                    # === FETCH 1: Sharp Books - DraftKings + FanDuel (us region) ===
                    sharp_prices = {}  # {(player, market, line, direction): {draftkings, fanduel}}
                    
                    sharp_params = {
                        "apiKey": ODDS_API_KEY,
                        "regions": "us",
                        "bookmakers": "draftkings,fanduel",
                        "markets": PRIZEPICKS_ALL_MARKETS,
                        "oddsFormat": "american",
                        "includeMultipliers": "true"
                    }
                    
                    try:
                        sharp_response = await client.get(odds_url, params=sharp_params, timeout=15.0)
                        if sharp_response.status_code == 200:
                            sharp_data = sharp_response.json()
                            
                            for bm in sharp_data.get("bookmakers", []):
                                bm_key = bm.get("key", "")
                                if bm_key not in ["draftkings", "fanduel"]:
                                    continue
                                
                                for market in bm.get("markets", []):
                                    market_key = market.get("key", "")
                                    for outcome in market.get("outcomes", []):
                                        player_name = outcome.get("description", "")
                                        line = outcome.get("point", 0)
                                        direction = (outcome.get("name", "") or "over").lower()
                                        price = outcome.get("price")
                                        
                                        lookup_key = (player_name, market_key, line, direction)
                                        if lookup_key not in sharp_prices:
                                            sharp_prices[lookup_key] = {"draftkings_price": None, "fanduel_price": None}
                                        
                                        if bm_key == "draftkings":
                                            sharp_prices[lookup_key]["draftkings_price"] = price
                                        elif bm_key == "fanduel":
                                            sharp_prices[lookup_key]["fanduel_price"] = price
                                            
                            logger.info(f"  [SHARP] {event.get('away_team')} @ {event.get('home_team')}: {len(sharp_prices)} prices")
                    except Exception as e:
                        logger.warning(f"[SHARP_FETCH] Error for {event_id}: {e}")
                    
                    # === FETCH 2: PrizePicks lines ===
                    pp_params = {
                        "apiKey": ODDS_API_KEY,
                        "regions": PRIZEPICKS_REGION,
                        "markets": PRIZEPICKS_ALL_MARKETS,
                        "bookmakers": PRIZEPICKS_BOOKMAKER,
                        "oddsFormat": "american",
                        "includeMultipliers": "true"
                    }
                    
                    response = await client.get(odds_url, params=pp_params, timeout=15.0)
                    
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
                                    line = outcome.get("point", 0)
                                    direction = (outcome.get("name", "") or "over").lower()
                                    multiplier = outcome.get("multiplier")
                                    
                                    # Look up sharp prices
                                    lookup_key = (player_name, market_key, line, direction)
                                    sharp_data = sharp_prices.get(lookup_key, {})
                                    draftkings_price = sharp_data.get("draftkings_price")
                                    fanduel_price = sharp_data.get("fanduel_price")
                                    sharp_price = draftkings_price if draftkings_price is not None else fanduel_price
                                    sharp_source = "draftkings" if draftkings_price is not None else ("fanduel" if fanduel_price is not None else None)
                                    
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
                                        "line": line,
                                        "price": price,
                                        "multiplier": multiplier,
                                        "is_alternate_market": is_alternate_market,
                                        "is_demon": is_demon,
                                        "is_goblin": is_goblin,
                                        "prop_type": prop_type,
                                        # Sharp book prices
                                        "draftkings_price": draftkings_price,
                                        "fanduel_price": fanduel_price,
                                        "sharp_price": sharp_price,
                                        "sharp_source": sharp_source
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

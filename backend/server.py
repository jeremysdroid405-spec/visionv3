from fastapi import FastAPI, APIRouter, Depends, HTTPException, status, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Dict, Any
import uuid
from datetime import datetime, timezone, timedelta
from supabase import create_client, Client
from jose import JWTError, jwt
import httpx
from thefuzz import fuzz
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from stats_manager_bdl import StatsManager
from demon_tracker_engine import DeepIngestionEngine
from demon_goblin_engine import DemonGoblinEngine
from vision_ai_service import VisionAIService, get_vision_service
from injury_service import InjuryIntelligenceService, get_injury_service
from raw_stat_fetcher import RawStatFetcher
from social_signal_engine import SocialSignalEngine, get_social_signal_engine
from payout_engine import (
    calculate_payout_from_picks, 
    estimate_payout, 
    calculate_leg_modifier,
    AssetType,
    BASE_MULTIPLIERS
)
from adaptive_sync_engine import (
    AdaptiveSyncEngine,
    init_adaptive_sync_engine,
    get_adaptive_sync_engine,
    STALE_DATA_THRESHOLD_SECONDS
)
from intel_briefing_engine import (
    IntelBriefingEngine,
    init_intel_briefing_engine,
    get_intel_briefing_engine
)
from live_scores_engine import (
    LiveScoresEngine,
    init_live_scores_engine,
    get_live_scores_engine
)
from game_lock_engine import (
    GameLockEngine,
    init_game_lock_engine,
    get_game_lock_engine
)
from board_intelligence_engine import (
    BoardIntelligenceEngine,
    get_board_intel_engine
)
from nba_master_hub import (
    get_master_hub,
    fetchPlayerIntel,
    fetchPlayerIntelByName,
    searchPlayers as hubSearchPlayers,
    getHubStats,
    runDailySync as runHubSync
)
from odds_api_mapper import (
    get_odds_api_mapper,
    init_odds_api_mapper,
    getPlayerIdFromOddsName,
    getFullPlayerData,
    rebuildMapping as rebuildOddsMapping
)
from ai_context_engine import AiContextEngine
from routes import register_all_routes
from services.photo_storage_service import PhotoStorageService
from middleware import (
    RateLimitMiddleware,
    RequestTracerMiddleware,
    get_request_id,
    setup_tracing_logger,
    TracingFormatter,
    RequestIdFilter,
)

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# Enable rate limiting (set to False to disable)
RATE_LIMITING_ENABLED = os.environ.get("RATE_LIMITING_ENABLED", "true").lower() == "true"

CURRENT_SEASON = "2025"  # 2025-26 NBA season

mongo_url = os.environ['MONGO_URL']

# MongoDB connection with Atlas-compatible settings
# Build connection options based on whether this is Atlas or local
is_atlas = 'mongodb.net' in mongo_url or 'mongodb+srv' in mongo_url

connection_opts = {
    'serverSelectionTimeoutMS': 30000,  # 30 seconds for server selection
    'connectTimeoutMS': 30000,           # 30 seconds for initial connection
    'socketTimeoutMS': 60000,            # 60 seconds for socket operations
    'maxPoolSize': 50,                   # Connection pool size
    'minPoolSize': 5,                    # Minimum connections to keep
    'maxIdleTimeMS': 60000,              # Close idle connections after 60s
    'retryWrites': True,                 # Retry failed writes
    'retryReads': True,                  # Retry failed reads
}

# Only add TLS settings for Atlas connections
if is_atlas:
    connection_opts['tls'] = True
    connection_opts['tlsAllowInvalidCertificates'] = False
    connection_opts['w'] = 'majority'

client = AsyncIOMotorClient(mongo_url, **connection_opts)
db = client[os.environ['DB_NAME']]

SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_ANON_KEY = os.environ.get('SUPABASE_ANON_KEY')
SUPABASE_SERVICE_ROLE_KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
JWT_SECRET = os.environ.get('JWT_SECRET')
ODDS_API_KEY = os.environ.get('ODDS_API_KEY')

# Scheduler timezone (UTC)
SCHEDULER_TIMEZONE = 'UTC'
DAILY_SYNC_HOUR = 4  # 4:00 AM UTC
DAILY_SYNC_MINUTE = 0

supabase: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY) if SUPABASE_ANON_KEY else None

# ==================== OPENAPI CONFIGURATION ====================
# API Metadata and Documentation
API_TITLE = "PickVision API"
API_VERSION = "3.0.0"
API_DESCRIPTION = """
# PickVision - NBA Player Prop Intelligence Platform

🏀 **Military-grade betting intelligence for NBA player props**

## Overview
PickVision delivers AI-driven betting insights by identifying:
- **Demons** 🔴 High-risk, high-reward props with lines above season averages
- **Goblins** 🟢 Safer, consistent props with high hit rates

## API Tiers

### War Zone (Demons)
Aggressive plays for risk-tolerant bettors. High EV, lower hit rates.

### Safe Haven (Goblins)
Conservative plays for consistent returns. High hit rates, moderate payouts.

### Front Lines
Balanced mix of demons and goblins for diversified parlays.

## Key Features
- Real-time odds sync via Adaptive Sync Engine
- AI-powered Vision briefings (Gemini 3 Flash)
- Hit rate calculations from BallDontLie stats
- Game lock protection for live games
- Smart parlay builder with correlation data

## Rate Limits
- Standard: 100 requests/minute
- Sync endpoints: 10 requests/minute
- AI briefings: 5 requests/minute

## Authentication
Most read endpoints are public. Write operations require JWT authentication.
"""

# OpenAPI Tags with descriptions for documentation grouping
OPENAPI_TAGS = [
    # Core Data Endpoints
    {
        "name": "Core V3",
        "description": "Primary data endpoints: status, players, demons, goblins, board"
    },
    {
        "name": "Tiers",
        "description": "War Zone, Safe Haven, Front Lines - tiered pick sections"
    },
    {
        "name": "Cached Data",
        "description": "Zero-API-call warehouse endpoints for instant data access"
    },
    # Intelligence & Analytics
    {
        "name": "Intel Sync",
        "description": "Data synchronization and AI briefing generation"
    },
    {
        "name": "Board Intelligence",
        "description": "Smart board management with scheduled syncs"
    },
    {
        "name": "vision",
        "description": "AI Vision briefings powered by Claude Sonnet 4.5"
    },
    {
        "name": "ai-context",
        "description": "Contextual AI analysis for player props"
    },
    # Real-time Data
    {
        "name": "Adaptive Sync",
        "description": "Mission-critical polling with tiered refresh rates"
    },
    {
        "name": "live-scores",
        "description": "Real-time game scores and updates"
    },
    {
        "name": "Game Lock",
        "description": "Auto-cleanup when games start, parlay validation"
    },
    # Parlay & Payouts
    {
        "name": "parlays",
        "description": "Parlay builder and goblin recon combinations"
    },
    {
        "name": "Payouts",
        "description": "Payout calculations and estimates"
    },
    # Player Data
    {
        "name": "board",
        "description": "Player board, search, and trending"
    },
    {
        "name": "picks",
        "description": "Most popular bets and pick recommendations"
    },
    {
        "name": "Roster Sync",
        "description": "Master roster and player photo management"
    },
    {
        "name": "injuries",
        "description": "Injury intelligence from ESPN and BDL"
    },
    # External Data
    {
        "name": "master-hub",
        "description": "NBA Master Hub - Single Source of Truth"
    },
    {
        "name": "odds-mapper",
        "description": "Odds API V4 mapping and normalization"
    },
    {
        "name": "Social Signals",
        "description": "News sentiment and revenge game detection"
    },
    # Administration
    {
        "name": "Admin",
        "description": "Cache management, roster sync, rate limits"
    },
    {
        "name": "Scheduler",
        "description": "Scheduled job status and manual triggers"
    },
    {
        "name": "Validation",
        "description": "Raw stat validation for data integrity"
    },
    # Authentication
    {
        "name": "auth",
        "description": "User authentication and profile management"
    },
    # Legacy & Compatibility
    {
        "name": "Legacy",
        "description": "Backward-compatible endpoints (deprecated)"
    },
    {
        "name": "demon-tracker",
        "description": "Deep Ingestion Engine v2 (legacy)"
    },
    {
        "name": "intel",
        "description": "Intel briefings (use Intel Sync instead)"
    },
    {
        "name": "sync",
        "description": "Sync operations (use Board Intelligence instead)"
    },
    {
        "name": "board-intel",
        "description": "Board intel v1 (use Board Intelligence instead)"
    },
    {
        "name": "command-center",
        "description": "Command center operations"
    },
]

app = FastAPI(
    title=API_TITLE,
    version=API_VERSION,
    description=API_DESCRIPTION,
    openapi_tags=OPENAPI_TAGS,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    contact={
        "name": "PickVision Support",
        "url": "https://pickvision.app/support",
    },
    license_info={
        "name": "Proprietary",
    }
)
api_router = APIRouter(prefix="/api")
security = HTTPBearer()

# ==================== LOGGING CONFIGURATION ====================
# Configure logging with request ID tracing
def setup_logging():
    """Configure logging with request ID tracing support."""
    # Create formatter with request ID support
    formatter = TracingFormatter(
        fmt="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    
    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Add console handler with tracing formatter
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.addFilter(RequestIdFilter())
    root_logger.addHandler(console_handler)
    
    return root_logger

# Initialize logging
setup_logging()
logger = logging.getLogger(__name__)

# ==================== MIDDLEWARE CONFIGURATION ====================
# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add Request Tracer middleware (generates X-Request-ID)
app.add_middleware(RequestTracerMiddleware)

# Add Rate Limiter middleware (X-RateLimit-* headers)
if RATE_LIMITING_ENABLED:
    app.add_middleware(RateLimitMiddleware, enabled=True)
    logger.info("[MIDDLEWARE] Rate limiting ENABLED")
else:
    logger.info("[MIDDLEWARE] Rate limiting DISABLED")

logger.info("[MIDDLEWARE] Request tracing ENABLED (X-Request-ID)")

stats_manager = None
demon_tracker = None
demon_goblin_engine = None
vision_ai_service = None  # Vision AI service instance
injury_service = None  # Injury Intelligence service instance
raw_stat_fetcher = None  # RAW STAT FETCHER - Isolated data integrity service
social_signal_engine = None  # Social Signal Engine - News sentiment & revenge games
adaptive_sync = None  # Adaptive Sync Engine - Mission-critical polling
intel_briefing_engine = None  # Intel Briefing Engine - Gemini 3 Flash
live_scores_engine = None  # Live Scores Engine - Real-time scores and news
game_lock_engine = None  # Game Lock Engine - Auto-cleanup on game start
scheduler = None  # APScheduler instance

async def initial_autonomous_sync():
    """Run autonomous sync on startup - Demon & Goblin Engine v3"""
    await asyncio.sleep(5)  # Wait for app to fully start
    
    # Run Demon & Goblin sync (v3)
    if demon_goblin_engine:
        logger.info("DEMON & GOBLIN ENGINE v3.0 - AUTONOMOUS STARTUP SYNC")
        result = await demon_goblin_engine.run_full_sync()
        logger.info(f"Sync complete: {result.get('unique_players', 0)} players, {result.get('demons_count', 0)} demons, {result.get('goblins_count', 0)} goblins")


async def scheduled_daily_sync():
    """
    Scheduled job that runs at 4:00 AM EST (09:00 UTC) daily.
    
    FULL SYNC - All Vision Intel data refreshed together:
    1. Sync injuries from ESPN (for usage ripple calculations)
    2. Sync player stats to MongoDB (from BallDontLie + NBA.com fallback)
    3. Update Master Hub baseline stats (L5, L10, Season averages)
    4. Refresh DvP rankings (Defense vs Position for matchup analysis)
    5. Run full odds sync (uses cached stats for hit rate calculations)
    6. Calculate daily insights (advanced analytics)
    7. Generate Vision AI insights for Demons/Goblins/High Volatility
    8. Sync career stats from NBA.com (for milestone badges)
    9. Sync contract data from Spotrac (for pay_day badges)
    """
    logger.info("=" * 70)
    logger.info(f"[SCHEDULER] 4:00 AM FULL DAILY SYNC TRIGGERED")
    logger.info(f"[SCHEDULER] Time: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 70)
    
    if demon_goblin_engine:
        try:
            # Step 1: Sync injuries (ESPN) - Do first for usage ripple data
            if injury_service:
                logger.info("[SCHEDULER] Step 1/10: Syncing injury data from ESPN...")
                try:
                    injury_result = await injury_service.sync_injuries()
                    logger.info(f"[SCHEDULER] Injuries: {injury_result.get('injuries_synced', 0)} injuries, {injury_result.get('usage_ripple_updates', 0)} ripple updates")
                except Exception as ie:
                    logger.error(f"[SCHEDULER] Injury sync failed (non-critical): {ie}")
            
            # Step 2: BDL + NBA.com Comprehensive Sync - Primary stats source
            # This syncs ALL active NBA players with:
            # - Season averages from BDL /season_averages endpoint (OFFICIAL)
            # - L5/L10/L15/L20 from NBA.com playerdashboardbylastngames (OFFICIAL)
            logger.info("[SCHEDULER] Step 2/10: Running BDL + NBA.com comprehensive sync...")
            try:
                from services.bdl_comprehensive_sync import get_bdl_sync_service
                bdl_service = get_bdl_sync_service(db)
                bdl_result = await bdl_service.sync_all_active_players()
                logger.info(f"[SCHEDULER] BDL sync: {bdl_result.get('success', 0)}/{bdl_result.get('total', 0)} players synced")
                logger.info(f"[SCHEDULER] NBA.com L5/L10: {bdl_result.get('nba_enriched', 0)} players enriched with official stats")
            except Exception as bdl_e:
                logger.error(f"[SCHEDULER] BDL + NBA.com sync failed: {bdl_e}")
                # Fallback to legacy stats sync
                logger.info("[SCHEDULER] Falling back to legacy stats sync...")
                stats_result = await demon_goblin_engine.sync_player_stats()
                logger.info(f"[SCHEDULER] Legacy stats sync: {stats_result.get('stats_synced', 0)} players")
            
            # Step 3: Sync BDL Injuries for context badges
            logger.info("[SCHEDULER] Step 3/10: Syncing injury reports from BDL...")
            try:
                from services.bdl_enhanced_data import get_bdl_enhanced_service
                enhanced_service = get_bdl_enhanced_service(db)
                injury_result = await enhanced_service.sync_injuries()
                logger.info(f"[SCHEDULER] Injuries: {injury_result.get('injuries_count', 0)} injuries found, {injury_result.get('players_updated', 0)} context badges updated")
            except Exception as inj_e:
                logger.error(f"[SCHEDULER] BDL injuries sync failed (non-critical): {inj_e}")
            
            # Step 4: Sync Advanced Stats (PIE, Net Rating)
            logger.info("[SCHEDULER] Step 4/10: Syncing advanced stats from BDL...")
            try:
                from services.bdl_enhanced_data import get_bdl_enhanced_service
                enhanced_service = get_bdl_enhanced_service(db)
                adv_result = await enhanced_service.sync_advanced_stats()
                logger.info(f"[SCHEDULER] Advanced stats: {adv_result.get('players_synced', 0)} players updated with PIE/ratings")
            except Exception as adv_e:
                logger.error(f"[SCHEDULER] Advanced stats sync failed (non-critical): {adv_e}")
            
            # Step 5: Refresh DvP rankings (Defense vs Position)
            logger.info("[SCHEDULER] Step 5/10: Refreshing DvP rankings...")
            try:
                from services.dvp_service import force_refresh_dvp
                dvp_result = await force_refresh_dvp()
                logger.info(f"[SCHEDULER] DvP refresh: {dvp_result.get('source')} - {dvp_result.get('teams_count', 0)} teams, {len(dvp_result.get('stat_types', []))} stat types")
            except Exception as de:
                logger.error(f"[SCHEDULER] DvP refresh failed (non-critical): {de}")
            
            # Step 6: Run full odds sync
            logger.info("[SCHEDULER] Step 6/10: Running full odds sync...")
            result = await demon_goblin_engine.run_full_sync()
            logger.info(f"[SCHEDULER] Sync complete: {result.get('unique_players', 0)} players")
            logger.info(f"[SCHEDULER] Standard: {result.get('standard_count', 0)}, Demons: {result.get('demons_count', 0)}, Goblins: {result.get('goblins_count', 0)}")
            
            # Step 7: Calculate daily insights (advanced analytics)
            logger.info("[SCHEDULER] Step 7/10: Calculating daily insights...")
            insights_result = await demon_goblin_engine.sync_daily_insights()
            logger.info(f"[SCHEDULER] Insights: {insights_result.get('insights_calculated', 0)} players analyzed")
            
            # Step 8: Generate Vision AI insights for eligible players (using Google Gemini)
            if vision_ai_service and os.environ.get('GOOGLE_API_KEY'):
                logger.info("[SCHEDULER] Step 8/10: Generating Vision AI insights (Gemini)...")
                try:
                    vision_result = await vision_ai_service.trigger_insights_for_sync()
                    logger.info(f"[SCHEDULER] Vision AI: {vision_result.get('insights_generated', 0)} insights generated")
                except Exception as ve:
                    logger.error(f"[SCHEDULER] Vision AI failed (non-critical): {ve}")
            else:
                logger.info("[SCHEDULER] Step 8/10: Vision AI skipped (GOOGLE_API_KEY not configured)")
            
            # Step 9: Sync career stats from NBA.com (for milestone badges)
            logger.info("[SCHEDULER] Step 9/10: Syncing career stats from NBA.com...")
            try:
                from services.nba_career_service import sync_career_stats_for_players, TRACKED_PLAYERS
                career_result = await sync_career_stats_for_players(db, TRACKED_PLAYERS)
                logger.info(f"[SCHEDULER] Career stats: {career_result.get('synced', 0)}/{career_result.get('total', 0)} players updated")
            except Exception as career_e:
                logger.error(f"[SCHEDULER] Career stats sync failed (non-critical): {career_e}")
            
            # Step 10: Sync contract data from Spotrac (for pay_day badges)
            logger.info("[SCHEDULER] Step 10/10: Syncing contract data from Spotrac...")
            try:
                from services.spotrac_contract_service import sync_contract_data
                contract_result = await sync_contract_data(db)
                logger.info(f"[SCHEDULER] Contracts: {contract_result.get('players_count', 0)} contract year players synced")
            except Exception as contract_e:
                logger.error(f"[SCHEDULER] Contract sync failed (non-critical): {contract_e}")
            
            logger.info("=" * 70)
            logger.info(f"[SCHEDULER] 4:00 AM FULL SYNC COMPLETE (10 STEPS)")
            logger.info("=" * 70)
            
        except Exception as e:
            logger.error(f"[SCHEDULER] Daily sync failed: {e}")
    else:
        logger.error("[SCHEDULER] Demon & Goblin Engine not initialized")


async def scheduled_nba_l5l10_sync():
    """
    Scheduled job that runs at 4:05 AM EST (09:05 UTC) daily.
    
    Batch enriches ALL players with NBA.com L5/L10 stats.
    This runs 5 minutes AFTER the main odds sync to ensure the board
    has fresh hit rate data immediately.
    
    Uses playerdashboardbylastngames for official pre-calculated stats.
    """
    logger.info("=" * 70)
    logger.info(f"[SCHEDULER] 4:05 AM NBA.COM L5/L10 BATCH ENRICHMENT (deprecated - using staggered syncs)")
    logger.info("=" * 70)
    # This is now handled by staggered syncs at 4:00, 4:02, 4:04, 4:06, 4:08 AM


async def scheduled_nba_l5l10_batch(batch_num: int, limit: int = 125):
    """
    Staggered NBA.com L5/L10 batch enrichment.
    
    5 batches run 2 minutes apart starting at 4:00 AM EST:
    - Batch 1: 4:00 AM - 125 players
    - Batch 2: 4:02 AM - 125 players
    - Batch 3: 4:04 AM - 125 players
    - Batch 4: 4:06 AM - 125 players
    - Batch 5: 4:08 AM - 125 players
    
    Total: 625 players covered (handles all ~550 active players)
    """
    logger.info("=" * 70)
    logger.info(f"[SCHEDULER] NBA.COM L5/L10 BATCH {batch_num}/5 ({limit} players)")
    logger.info(f"[SCHEDULER] Time: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 70)
    
    try:
        from services.bdl_comprehensive_sync import get_bdl_sync_service
        bdl_service = get_bdl_sync_service(db)
        
        # Find players needing enrichment
        players_needing = await db.nba_master_hub_2026.count_documents({
            "nba_id": {"$exists": True, "$ne": None},
            "$or": [
                {"baseline_stats.PTS.l5_avg": {"$exists": False}},
                {"baseline_stats.PTS.l5_avg": None}
            ]
        })
        
        logger.info(f"[SCHEDULER] Batch {batch_num}: {players_needing} players need L5/L10 enrichment")
        
        if players_needing == 0:
            logger.info(f"[SCHEDULER] Batch {batch_num}: All players already enriched!")
            return
        
        # Get players to enrich
        players = await db.nba_master_hub_2026.find({
            "nba_id": {"$exists": True, "$ne": None},
            "$or": [
                {"baseline_stats.PTS.l5_avg": {"$exists": False}},
                {"baseline_stats.PTS.l5_avg": None}
            ]
        }, {"bdl_id": 1, "display_name": 1}).limit(limit).to_list(limit)
        
        enriched = 0
        for player in players:
            try:
                result = await bdl_service.enrich_baseline_with_nba_stats(player["bdl_id"])
                if result:
                    enriched += 1
            except Exception as e:
                logger.debug(f"[SCHEDULER] Failed to enrich {player.get('display_name')}: {e}")
        
        logger.info(f"[SCHEDULER] Batch {batch_num} complete: {enriched}/{len(players)} enriched")
        logger.info("=" * 70)
        
    except Exception as e:
        logger.error(f"[SCHEDULER] NBA.com L5/L10 batch {batch_num} failed: {e}")


# Wrapper functions for each batch (APScheduler requires named functions)
async def scheduled_nba_batch_1():
    await scheduled_nba_l5l10_batch(1, 125)

async def scheduled_nba_batch_2():
    await scheduled_nba_l5l10_batch(2, 125)

async def scheduled_nba_batch_3():
    await scheduled_nba_l5l10_batch(3, 125)

async def scheduled_nba_batch_4():
    await scheduled_nba_l5l10_batch(4, 125)

async def scheduled_nba_batch_5():
    await scheduled_nba_l5l10_batch(5, 125)


async def scheduled_bdl_game_values_sync():
    """
    BDL Game Values Enrichment - CRITICAL for hit rate calculations.
    
    Runs at 4:10 AM EST, after the NBA.com L5/L10 batches complete.
    Fetches actual game-by-game values from BDL /stats endpoint and stores
    them in baseline_stats[STAT].l10_values.
    
    Without l10_values, hit rates cannot be calculated per betting line!
    """
    logger.info("=" * 70)
    logger.info("[SCHEDULER] BDL GAME VALUES ENRICHMENT")
    logger.info(f"[SCHEDULER] Time: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 70)
    
    try:
        from services.bdl_comprehensive_sync import get_bdl_sync_service
        bdl_service = get_bdl_sync_service(db)
        
        # Enrich all players with active props
        result = await bdl_service.batch_enrich_game_values(limit=200)
        
        logger.info(f"[SCHEDULER] BDL game values: {result['enriched']} enriched, {result['failed']} failed, {result['skipped']} skipped")
        logger.info("=" * 70)
        
    except Exception as e:
        logger.error(f"[SCHEDULER] BDL game values enrichment failed: {e}")


async def scheduled_ticker_sync():
    """
    Scheduled job that syncs ticker data (games and news) at 4:15 AM EST.
    
    - Fetches today's NBA games from NBA API
    - Fetches breaking news from ESPN, CBS, and other sources
    - Stores both in ticker_cache collection for fast retrieval
    """
    logger.info("=" * 70)
    logger.info("[SCHEDULER] TICKER SYNC (GAMES + NEWS)")
    logger.info(f"[SCHEDULER] Time: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 70)
    
    try:
        from routes.live import sync_todays_games, sync_news_headlines
        
        # Sync today's games
        games_result = await sync_todays_games()
        logger.info(f"[SCHEDULER] Games sync: {games_result}")
        
        # Sync news headlines
        news_result = await sync_news_headlines()
        logger.info(f"[SCHEDULER] News sync: {news_result}")
        
        logger.info("=" * 70)
        
    except Exception as e:
        logger.error(f"[SCHEDULER] Ticker sync failed: {e}")


async def scheduled_badge_sync():
    """
    Scheduled job that syncs context badges at 4:20 AM EST.
    
    Computes badges for all players with active props:
    - home_cookin (home game)
    - jet_lag (long travel)
    - revenge (former team)
    - locked_in (hot streak)
    """
    logger.info("=" * 70)
    logger.info("[SCHEDULER] CONTEXT BADGE SYNC")
    logger.info(f"[SCHEDULER] Time: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 70)
    
    try:
        from services.context_badge_service import get_badge_service
        badge_service = get_badge_service(db)
        
        result = await badge_service.sync_badges_for_all_players(limit=500)
        
        logger.info(f"[SCHEDULER] Badge sync: {result['updated']} updated, {result['skipped']} skipped")
        logger.info("=" * 70)
        
    except Exception as e:
        logger.error(f"[SCHEDULER] Badge sync failed: {e}")


async def scheduled_roster_sync():
    """
    Scheduled job that runs every Sunday at midnight UTC.
    Syncs the master roster from BallDontLie to ensure accurate team mappings.
    """
    logger.info("=" * 70)
    logger.info(f"[SCHEDULER] WEEKLY MASTER ROSTER SYNC TRIGGERED")
    logger.info(f"[SCHEDULER] Time: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 70)
    
    if demon_goblin_engine:
        try:
            result = await demon_goblin_engine.sync_master_roster()
            logger.info(f"[SCHEDULER] Master Roster sync complete: {result.get('players_synced', 0)} players")
            logger.info(f"[SCHEDULER] Teams found: {result.get('teams_found', 0)}")
        except Exception as e:
            logger.error(f"[SCHEDULER] Master Roster sync failed: {e}")
    else:
        logger.error("[SCHEDULER] Demon & Goblin Engine not initialized")


async def scheduled_bdl_game_logs_sync():
    """
    Scheduled job that runs at 4:25 AM EST (09:25 UTC) daily.
    
    Syncs game-by-game stats from BDL /stats endpoint for all players.
    This data is CRITICAL for accurate per-line hit rate calculations.
    
    The sync fetches the current 2025-26 season data and stores it in
    nba_master_hub_2026.bdl_game_logs for each player.
    """
    logger.info("=" * 70)
    logger.info("[SCHEDULER] BDL GAME LOGS SYNC (2025-26 SEASON)")
    logger.info(f"[SCHEDULER] Time: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 70)
    
    try:
        from services.bdl_game_logs_sync import BDLGameLogsSync
        
        sync_service = BDLGameLogsSync(db)
        
        # Sync all players with bdl_id
        result = await sync_service.sync_all_players(batch_size=10)
        
        logger.info(f"[SCHEDULER] BDL Game Logs sync complete:")
        logger.info(f"[SCHEDULER]   - Players synced: {result.get('players_synced', 0)}/{result.get('total_players', 0)}")
        logger.info(f"[SCHEDULER]   - Total games: {result.get('total_games', 0)}")
        logger.info(f"[SCHEDULER]   - Failed: {result.get('players_failed', 0)}")
        logger.info(f"[SCHEDULER]   - Duration: {result.get('duration_seconds', 0):.1f}s")
        logger.info("=" * 70)
        
    except Exception as e:
        logger.error(f"[SCHEDULER] BDL game logs sync failed: {e}")


@app.on_event("startup")
async def startup_event():
    global stats_manager, demon_tracker, demon_goblin_engine, vision_ai_service, injury_service, raw_stat_fetcher, social_signal_engine, adaptive_sync, intel_briefing_engine, live_scores_engine, game_lock_engine, scheduler
    
    # Initialize stats manager (BallDontLie)
    stats_manager = StatsManager(db)
    logger.info("Stats Manager initialized (BallDontLie)")
    
    # Initialize utils cache collection for external module usage
    from utils import set_cache_collection
    set_cache_collection(db.cache)
    logger.info("Utils cache collection initialized")
    
    # Invalidate player lookup cache to ensure fresh data
    from utils.player_lookup import invalidate_cache as invalidate_player_cache
    invalidate_player_cache()
    logger.info("Player lookup cache invalidated (will rebuild on first request)")
    
    # Initialize Deep Ingestion Engine (v2 - legacy)
    demon_tracker = DeepIngestionEngine(db)
    logger.info("Deep Ingestion Engine initialized (v2)")
    
    # Initialize Demon & Goblin Engine (v3 - NEW)
    demon_goblin_engine = DemonGoblinEngine(db)
    logger.info("Demon & Goblin Engine v3.0 initialized")
    
    # Initialize Vision AI Service (Claude Sonnet 4.5)
    vision_ai_service = get_vision_service(db)
    logger.info("Vision AI Service initialized (Claude Sonnet 4.5)")
    
    # Initialize Injury Intelligence Service
    injury_service = get_injury_service(db)
    logger.info("Injury Intelligence Service initialized (ESPN + BDL)")
    
    # Initialize RAW STAT FETCHER - Isolated data integrity service
    raw_stat_fetcher = RawStatFetcher(db)
    bdl_key = os.environ.get('BDL_API_KEY')
    if bdl_key:
        raw_stat_fetcher.set_api_key(bdl_key)
    logger.info("Raw Stat Fetcher initialized (Data Integrity Service)")
    
    # Initialize Social Signal Engine - News sentiment & revenge games
    social_signal_engine = get_social_signal_engine(db)
    logger.info("Social Signal Engine initialized (News + Revenge Detection)")
    
    # Initialize DvP Service with MongoDB reference for persistent storage
    from services.dvp_service import set_db_reference as set_dvp_db_reference, initialize_dvp_cache
    set_dvp_db_reference(db)
    # Initialize cache from MongoDB to avoid static fallback on startup
    await initialize_dvp_cache()
    logger.info("DvP Service initialized (MongoDB-backed DvP rankings)")
    
    # Initialize Adaptive Sync Engine - Mission-critical polling
    adaptive_sync = init_adaptive_sync_engine(db, ODDS_API_KEY)
    logger.info("Adaptive Sync Engine initialized (Mission-Critical Polling)")
    
    # CRITICAL FIX: Wire up the adaptive sync to use the proper sync function
    # This ensures the adaptive sync uses CachedBoardBuilderService to create
    # nested player documents (with props arrays) instead of flat documents
    adaptive_sync.set_sync_callback(demon_goblin_engine.sync_odds_to_mongo)
    logger.info("[ADAPTIVE_SYNC] Callback wired to DemonGoblinEngine.sync_odds_to_mongo")
    
    # Initialize Intel Briefing Engine - Gemini 3 Flash
    intel_briefing_engine = init_intel_briefing_engine(db)
    google_key = os.environ.get('GOOGLE_API_KEY')
    if google_key:
        logger.info("Intel Briefing Engine initialized (Gemini 3 Flash)")
    else:
        logger.warning("[INTEL BRIEFING] No GOOGLE_API_KEY - Intel Briefing disabled")
    
    # Initialize Live Scores Engine - Real-time scores and news
    live_scores_engine = init_live_scores_engine(db)
    logger.info("Live Scores Engine initialized (Scores + Breaking News)")
    
    # Initialize Game Lock Engine - Auto-cleanup on game start
    game_lock_engine = init_game_lock_engine(db)
    await game_lock_engine.start()
    logger.info("Game Lock Engine initialized (60s auto-cleanup)")
    
    # Register modular routes (from /routes/ directory)
    from ai_context_engine import AiContextEngine
    
    # Create master hub function references
    master_hub_funcs = {
        "fetchPlayerIntel": fetchPlayerIntel,
        "fetchPlayerIntelByName": fetchPlayerIntelByName,
        "hubSearchPlayers": hubSearchPlayers,
        "getHubStats": getHubStats,
        "runHubSync": runHubSync,
        "get_master_hub": lambda: get_master_hub(db)
    }
    
    # Initialize APScheduler for daily and weekly syncs
    scheduler = AsyncIOScheduler(timezone=SCHEDULER_TIMEZONE)
    
    # Initialize photo storage service (for base64 photo caching)
    # Use synchronous client with same Atlas-compatible settings
    sync_opts = {
        'serverSelectionTimeoutMS': 30000,
        'connectTimeoutMS': 30000,
        'socketTimeoutMS': 60000,
        'maxPoolSize': 10,
        'retryWrites': True,
    }
    if is_atlas:
        sync_opts['tls'] = True
    
    sync_client = MongoClient(mongo_url, **sync_opts)
    sync_db = sync_client[os.environ['DB_NAME']]
    photo_service = PhotoStorageService(sync_db)
    
    register_all_routes(
        app, 
        demon_goblin_engine, 
        game_lock_engine=game_lock_engine, 
        db=db,
        injury_service=injury_service,
        vision_service=vision_ai_service,
        live_scores_engine=live_scores_engine,
        ai_context_engine_class=AiContextEngine,
        master_hub_funcs=master_hub_funcs,
        get_odds_mapper_func=get_odds_api_mapper,
        demon_tracker=demon_tracker,
        raw_stat_fetcher=raw_stat_fetcher,
        social_signal_engine=social_signal_engine,
        demon_goblin_engine_class=DemonGoblinEngine,
        stats_manager=stats_manager,
        scheduler=scheduler,
        photo_service=photo_service
    )
    logger.info("[ROUTES] Modular routes registered from /routes/ directory (Phase 18: +4 new modules)")
    
    # NOTE: The api_router below contains routes that will be migrated in future phases
    # For now, they coexist with the modular routes
    app.include_router(api_router)
    logger.info("[ROUTES] Legacy api_router routes included (migration in progress)")
    
    # Start the adaptive sync engine (background polling)
    if ODDS_API_KEY:
        await adaptive_sync.start()
        logger.info("[ADAPTIVE_SYNC] Background polling STARTED")
    else:
        logger.warning("[ADAPTIVE_SYNC] No Odds API key - adaptive sync disabled")
    
    # Daily sync at 4:00 AM EST (9:00 AM UTC) - FULL Vision Intel sync
    # Includes: Injuries, Player Stats, Master Hub, DvP Rankings, Odds, Insights, Vision AI
    scheduler.add_job(
        scheduled_daily_sync,
        CronTrigger(hour=9, minute=0, timezone=SCHEDULER_TIMEZONE),  # 4:00 AM EST = 9:00 AM UTC
        id='daily_sync',
        name='4:00 AM EST Full Vision Intel Sync',
        replace_existing=True
    )
    
    # NBA.com L5/L10 STAGGERED BATCH ENRICHMENT
    # 5 batches of 125 players each, 2 minutes apart
    # Ensures ALL players get fresh L5/L10 from NBA.com official stats
    
    # Batch 1: 4:00 AM EST (9:00 AM UTC) - 125 players
    scheduler.add_job(
        scheduled_nba_batch_1,
        CronTrigger(hour=9, minute=0, timezone=SCHEDULER_TIMEZONE),
        id='nba_l5l10_batch_1',
        name='4:00 AM EST NBA.com L5/L10 Batch 1/5',
        replace_existing=True
    )
    
    # Batch 2: 4:02 AM EST (9:02 AM UTC) - 125 players
    scheduler.add_job(
        scheduled_nba_batch_2,
        CronTrigger(hour=9, minute=2, timezone=SCHEDULER_TIMEZONE),
        id='nba_l5l10_batch_2',
        name='4:02 AM EST NBA.com L5/L10 Batch 2/5',
        replace_existing=True
    )
    
    # Batch 3: 4:04 AM EST (9:04 AM UTC) - 125 players
    scheduler.add_job(
        scheduled_nba_batch_3,
        CronTrigger(hour=9, minute=4, timezone=SCHEDULER_TIMEZONE),
        id='nba_l5l10_batch_3',
        name='4:04 AM EST NBA.com L5/L10 Batch 3/5',
        replace_existing=True
    )
    
    # Batch 4: 4:06 AM EST (9:06 AM UTC) - 125 players
    scheduler.add_job(
        scheduled_nba_batch_4,
        CronTrigger(hour=9, minute=6, timezone=SCHEDULER_TIMEZONE),
        id='nba_l5l10_batch_4',
        name='4:06 AM EST NBA.com L5/L10 Batch 4/5',
        replace_existing=True
    )
    
    # Batch 5: 4:08 AM EST (9:08 AM UTC) - 125 players
    scheduler.add_job(
        scheduled_nba_batch_5,
        CronTrigger(hour=9, minute=8, timezone=SCHEDULER_TIMEZONE),
        id='nba_l5l10_batch_5',
        name='4:08 AM EST NBA.com L5/L10 Batch 5/5',
        replace_existing=True
    )
    
    # BDL Game Values Enrichment at 4:10 AM EST (9:10 AM UTC)
    # CRITICAL: Provides l10_values for hit rate calculations
    scheduler.add_job(
        scheduled_bdl_game_values_sync,
        CronTrigger(hour=9, minute=10, timezone=SCHEDULER_TIMEZONE),
        id='bdl_game_values_sync',
        name='4:10 AM EST BDL Game Values Enrichment',
        replace_existing=True
    )
    
    # Ticker Sync at 4:15 AM EST (9:15 AM UTC) - Games and News
    scheduler.add_job(
        scheduled_ticker_sync,
        CronTrigger(hour=9, minute=15, timezone=SCHEDULER_TIMEZONE),
        id='ticker_sync',
        name='4:15 AM EST Ticker Games/News Sync',
        replace_existing=True
    )
    
    # Badge Sync at 4:20 AM EST (9:20 AM UTC) - Context Badges
    scheduler.add_job(
        scheduled_badge_sync,
        CronTrigger(hour=9, minute=20, timezone=SCHEDULER_TIMEZONE),
        id='badge_sync',
        name='4:20 AM EST Context Badge Sync',
        replace_existing=True
    )
    
    # Morning props sync at 5:00 AM EST (10:00 AM UTC) - Odds/Props refresh
    scheduler.add_job(
        scheduled_daily_sync,
        CronTrigger(hour=10, minute=0, timezone=SCHEDULER_TIMEZONE),  # 5:00 AM EST = 10:00 AM UTC
        id='morning_props_sync',
        name='5:00 AM EST Morning Props Sync',
        replace_existing=True
    )
    
    # Weekly Master Roster sync every Sunday at midnight UTC
    scheduler.add_job(
        scheduled_roster_sync,
        CronTrigger(day_of_week='sun', hour=0, minute=0, timezone=SCHEDULER_TIMEZONE),
        id='weekly_roster_sync',
        name='Sunday Midnight Master Roster Sync',
        replace_existing=True
    )
    
    # BDL Game Logs Sync at 4:25 AM EST (09:25 AM UTC) - CRITICAL for hit rates
    # This syncs game-by-game stats from BDL for accurate per-line hit rate calculations
    scheduler.add_job(
        scheduled_bdl_game_logs_sync,
        CronTrigger(hour=9, minute=25, timezone=SCHEDULER_TIMEZONE),  # 4:25 AM EST = 9:25 AM UTC
        id='bdl_game_logs_sync',
        name='4:25 AM EST BDL Game Logs Sync',
        replace_existing=True
    )
    
    scheduler.start()
    logger.info(f"[SCHEDULER] APScheduler started")
    logger.info(f"[SCHEDULER] Daily Full Sync: 04:00 AM EST (09:00 UTC)")
    logger.info(f"[SCHEDULER] NBA.com L5/L10: 5 batches x 125 players @ 04:00, 04:02, 04:04, 04:06, 04:08 AM EST")
    logger.info(f"[SCHEDULER] BDL Game Logs: 04:25 AM EST (09:25 UTC)")
    logger.info(f"[SCHEDULER] Morning Props: 05:00 AM EST (10:00 UTC)")
    logger.info(f"[SCHEDULER] Weekly Roster: Sunday 00:00 UTC")
    
    # AUTO-SYNC: Check if database is empty and trigger initial population
    # This runs only once when deployed to a new environment with empty DB
    asyncio.create_task(check_and_run_initial_sync(db))
    logger.info("[STARTUP] Initial sync check scheduled (runs in background)")


async def check_and_run_initial_sync(db):
    """
    Check if the database is empty and run initial sync if needed.
    This ensures newly deployed environments get populated automatically.
    """
    try:
        await asyncio.sleep(5)  # Wait for server to fully start
        
        # Check if master hub is empty
        master_hub_count = await db.nba_master_hub_2026.count_documents({})
        cached_board_count = await db.dg_cached_board.count_documents({})
        
        logger.info(f"[INITIAL_SYNC] Database check: master_hub={master_hub_count}, cached_board={cached_board_count}")
        
        if master_hub_count == 0:
            logger.info("[INITIAL_SYNC] Empty database detected! Starting initial population...")
            
            # Step 1: Sync player roster from BDL
            try:
                from services.bdl_comprehensive_sync import get_bdl_comprehensive_service
                bdl_service = get_bdl_comprehensive_service(db)
                
                logger.info("[INITIAL_SYNC] Step 1/4: Syncing player roster from BallDontLie...")
                result = await bdl_service.sync_active_players()
                logger.info(f"[INITIAL_SYNC] Roster sync complete: {result.get('players_synced', 0)} players")
            except Exception as e:
                logger.error(f"[INITIAL_SYNC] Roster sync failed: {e}")
            
            # Step 2: Sync game-by-game values for hit rates
            try:
                logger.info("[INITIAL_SYNC] Step 2/4: Syncing game-by-game stats for hit rates...")
                result = await bdl_service.enrich_with_game_values()
                logger.info(f"[INITIAL_SYNC] Game values sync complete: {result.get('enriched', 0)} players enriched")
            except Exception as e:
                logger.error(f"[INITIAL_SYNC] Game values sync failed: {e}")
            
            # Step 3: Sync DvP rankings
            try:
                from services.dvp_service import force_refresh_dvp
                logger.info("[INITIAL_SYNC] Step 3/4: Syncing DvP rankings...")
                result = await force_refresh_dvp()
                logger.info(f"[INITIAL_SYNC] DvP sync complete: {result.get('teams_count', 0)} teams")
            except Exception as e:
                logger.error(f"[INITIAL_SYNC] DvP sync failed: {e}")
            
            # Step 4: Sync context badges
            try:
                from services.context_badge_service import get_context_badge_service
                badge_service = get_context_badge_service(db)
                logger.info("[INITIAL_SYNC] Step 4/4: Populating context badges...")
                result = await badge_service.populate_all_badges()
                logger.info(f"[INITIAL_SYNC] Badge sync complete: {result.get('updated', 0)} players updated")
            except Exception as e:
                logger.error(f"[INITIAL_SYNC] Badge sync failed: {e}")
            
            logger.info("[INITIAL_SYNC] ✅ Initial database population complete!")
        else:
            logger.info(f"[INITIAL_SYNC] Database already populated ({master_hub_count} players). Skipping initial sync.")
            
    except Exception as e:
        logger.error(f"[INITIAL_SYNC] Error during initial sync check: {e}")


@app.on_event("shutdown")
async def shutdown_event():
    """Clean shutdown of background services."""
    global adaptive_sync, game_lock_engine, scheduler
    
    # Stop adaptive sync engine
    if adaptive_sync:
        await adaptive_sync.stop()
        logger.info("[SHUTDOWN] Adaptive Sync Engine stopped")
    
    # Stop game lock engine
    if game_lock_engine:
        await game_lock_engine.stop()
        logger.info("[SHUTDOWN] Game Lock Engine stopped")
    
    # Shutdown scheduler
    if scheduler:
        scheduler.shutdown()
        logger.info("[SHUTDOWN] APScheduler stopped")


class SignUpRequest(BaseModel):
    email: str
    password: str
    full_name: Optional[str] = None

class LoginRequest(BaseModel):
    email: str
    password: str

class ProfileResponse(BaseModel):
    id: str
    email: str
    full_name: Optional[str] = None
    tier: str = "free"
    created_at: str

class UserResponse(BaseModel):
    user_id: str
    email: str
    profile: ProfileResponse
    access_token: str

class PlayerPropFull(BaseModel):
    player_name: str
    team: str
    opponent: str
    prop_type: str
    market: str
    source: str
    line: float
    prizepicks_line: Optional[float] = None
    market_avg: Optional[float] = None
    market_edge: Optional[float] = None
    draftkings_line: Optional[float] = None
    fanduel_line: Optional[float] = None
    betmgm_line: Optional[float] = None
    caesars_line: Optional[float] = None
    is_demon: bool = False
    demon_line: Optional[float] = None
    is_goblin: bool = False
    goblin_line: Optional[float] = None
    hit_rate: Optional[float] = None
    best_bet_score: float
    matchup_grade: str
    confidence: float
    injury_impact: Optional[str] = None
    def_rank: Optional[int] = None

async def verify_jwt(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    try:
        if JWT_SECRET:
            payload = jwt.decode(
                token,
                JWT_SECRET,
                algorithms=["HS256"],
                audience="authenticated"
            )
        return token
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token"
        )

async def get_current_user(token: str = Depends(verify_jwt)):
    try:
        if not supabase:
            raise HTTPException(status_code=500, detail="Supabase not configured")
        user_response = supabase.auth.get_user(token)
        if not user_response.user:
            raise HTTPException(status_code=401, detail="User not found")
        return user_response.user
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Authentication failed: {str(e)}")

# NOTE: Cache utilities below are LOCAL to server.py and use the global `db` reference.
# Similar functions exist in utils.py but require initialization via set_cache_collection().
async def get_cached_data(cache_key: str, ttl_hours: int = 24):
    """Get data from cache if not expired. Uses server.py global db."""
    cached = await db.cache.find_one({"key": cache_key})
    if cached:
        cached_time = datetime.fromisoformat(cached["cached_at"])
        if datetime.now(timezone.utc) - cached_time < timedelta(hours=ttl_hours):
            return cached.get("data")
    return None

async def set_cached_data(cache_key: str, data: Any):
    """Store data in cache. Uses server.py global db."""
    await db.cache.update_one(
        {"key": cache_key},
        {"$set": {"key": cache_key, "data": data, "cached_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True
    )

# NOTE: fuzzy_match_player is also defined in utils.py for external module use
def fuzzy_match_player(name1: str, name2: str, threshold: int = 80) -> bool:
    """Fuzzy match two player names."""
    return fuzz.ratio(name1.lower(), name2.lower()) >= threshold

# NOTE: Auth routes (/auth/signup, /auth/login, /profile) moved to routes/auth.py


# ==================== ALL V3 ROUTES EXTRACTED ====================
# The following routes have been moved to modular route files:
# - Legacy routes (full-board, calculate-hit-rate, validate-demon, root) → routes/legacy.py
# - Core V3 routes (status, players, demons, goblins, search, board, trending) → routes/core_v3.py
# - Tier routes (war-zone, goblin-vault, front-lines, parlay-builder, goblin-recon) → routes/tiers.py
# - Intel sync routes (sync-to-mongo, generate-intel-briefings, intel-briefing) → routes/intel_sync.py
# ==================================================================

# NOTE: Game Lock routes moved to routes/game_lock.py (Phase 16)
# Includes: /v3/lock-status, t-minus-games, locked-games, validate-parlay, check-locks

# NOTE: Adaptive Sync routes moved to routes/adaptive_sync.py (Phase 16)
# Includes: /v3/sync-status, stale-intel-check, priority-refresh, intel-freshness,
# adaptive-sync/start, adaptive-sync/stop

# NOTE: Board Intelligence routes moved to routes/board_intel_v2.py (Phase 15)
# Includes: /v3/board-intel/status, primary-sync, delta-refresh, start-scheduler,
# stop-scheduler, live-ticker, early-bird, scouting-projections

# NOTE: MASTER_HUB routes moved to routes/ directory

# NOTE: ODDS_MAPPER routes moved to routes/ directory


# NOTE: The following route sections have been extracted to /routes/ modules:
# - Raw Validation routes → routes/validation.py
# - Social Signal routes → routes/social.py
# - Roster Sync routes → routes/roster_sync.py
# - Payout routes → routes/payouts.py

# NOTE: Scheduler routes (scheduler-status, breaking-news) moved to routes/scheduler.py (Phase 17)


from fastapi import FastAPI, APIRouter, Depends, HTTPException, status, Query
from fastapi.responses import ORJSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient, ASCENDING, DESCENDING
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
from apscheduler.jobstores.mongodb import MongoDBJobStore
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from services.stats_manager_bdl import StatsManager
from services.config.collection_names import COLL
from services.vision_ai_service import VisionAIService, get_vision_service
from services.injury_service import InjuryIntelligenceService, get_injury_service
from services.raw_stat_fetcher import RawStatFetcher
from services.engines.social_signal_engine import SocialSignalEngine, get_social_signal_engine
from services.engines.payout_engine import (
    calculate_payout_from_picks, 
    estimate_payout, 
    calculate_leg_modifier,
    AssetType,
    BASE_MULTIPLIERS
)
from services.engines.adaptive_sync_engine import (
    AdaptiveSyncEngine,
    init_adaptive_sync_engine,
    get_adaptive_sync_engine,
    STALE_DATA_THRESHOLD_SECONDS
)
from services.engines.intel_briefing_engine import (
    IntelBriefingEngine,
    init_intel_briefing_engine,
    get_intel_briefing_engine
)
from services.engines.live_scores_engine import (
    LiveScoresEngine,
    init_live_scores_engine,
    get_live_scores_engine
)
from services.engines.game_lock_engine import (
    GameLockEngine,
    init_game_lock_engine,
    get_game_lock_engine
)
from services.engines.board_intelligence_engine import (
    BoardIntelligenceEngine,
    get_board_intel_engine
)
from services.engines.nba_master_hub import (
    get_master_hub,
    fetchPlayerIntel,
    fetchPlayerIntelByName,
    searchPlayers as hubSearchPlayers,
    getHubStats,
    runDailySync as runHubSync
)
from services.odds_api_mapper import (
    get_odds_api_mapper,
    init_odds_api_mapper,
    getPlayerIdFromOddsName,
    getFullPlayerData,
    rebuildMapping as rebuildOddsMapping
)
from services.engines.ai_context_engine import AiContextEngine
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
from services.referee_scraper_service import get_referee_service

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
    },
    # PERFORMANCE: Use orjson for faster JSON serialization (3-10x faster)
    default_response_class=ORJSONResponse
)

# PERFORMANCE: Add Gzip compression for large responses (reduces payload size 70-90%)
app.add_middleware(GZipMiddleware, minimum_size=1000)  # Compress responses > 1KB

# Mount static files for local headshot serving
# CRITICAL: Use production path with fallback for development
STATIC_DIR_PROD = Path("/var/www/app/backend/static")
STATIC_DIR_DEV = Path("/app/backend/static")

# Use production path if it exists and has content, otherwise use dev path
if STATIC_DIR_PROD.exists() and any(STATIC_DIR_PROD.iterdir()):
    STATIC_DIR = STATIC_DIR_PROD
else:
    STATIC_DIR = STATIC_DIR_DEV
    
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
# Mirror mount under /api/static so the k8s ingress (which routes /api/*
# to the backend and /static/* to the frontend) can reach these files.
# Frontend payloads carry /api/static/player-headshots/{id}.png URLs so
# the browser hits the backend, not the React dev-server which replies
# with the app shell HTML (broken avatar images).
app.mount("/api/static", StaticFiles(directory=str(STATIC_DIR)), name="api-static")

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


async def run_mlb_startup_health_check():
    """
    MLB Startup Health Check — auto-populate on pod fork.

    Post HARD CONSOLIDATION (2026-04-22) the universal path owns the
    board: `mlb_live_props` → `mlb_prop_scores @ final-mlb-rt`. If
    either is empty we kick the canonical `master_sync.run_master_sync`.
    """
    logger.info("=" * 70)
    logger.info("[MLB_HEALTH] MLB Startup Health Check - Checking collections...")
    logger.info("=" * 70)

    try:
        mlb_live_props_count = await db[COLL("live_props", "mlb")].count_documents({})
        mlb_scores_count = await db.mlb_prop_scores.count_documents(
            {"version_tag": "final-mlb-rt"}
        )

        logger.info(f"[MLB_HEALTH] mlb_live_props: {mlb_live_props_count} docs")
        logger.info(f"[MLB_HEALTH] mlb_prop_scores (final-mlb-rt): {mlb_scores_count} docs")

        if mlb_live_props_count == 0 or mlb_scores_count == 0:
            logger.warning(
                "[MLB_HEALTH] MLB canonical board is empty — running universal master sync…"
            )
            try:
                # 2026-05-07 SSOT: acquire UpstreamSyncLock so master_sync
                # does not raise MasterSyncBypassError. (Was direct call.)
                from services.master_sync import run_master_sync
                from services.upstream_sync_lock import get_upstream_sync_lock
                lock = get_upstream_sync_lock()
                async with lock.exclusive("mlb", holder="mlb_startup_health"):
                    result = await run_master_sync(db, "mlb")
                logger.info(
                    f"[MLB_HEALTH] Universal master sync complete: success={result.get('success')}"
                )
            except Exception as e:
                logger.error(f"[MLB_HEALTH] Master sync failed: {e}")
        else:
            logger.info("[MLB_HEALTH] MLB canonical collections populated — no action needed")

        logger.info("=" * 70)
        logger.info("[MLB_HEALTH] MLB Startup Health Check COMPLETE")
        logger.info("=" * 70)
        
    except Exception as e:
        logger.error(f"[MLB_HEALTH] Health check failed: {e}")
        import traceback
        logger.error(traceback.format_exc())


async def initial_autonomous_sync():
    """
    Startup sync — routes through Rebuild Coordinator.

    Phase 2: NBA uses coordinator → UnifiedPipeline(NBAAdapter).
    demon_goblin_engine is preserved but disabled as live publisher.
    """
    await asyncio.sleep(5)  # Wait for app to fully start

    logger.info("=" * 70)
    logger.info("[STARTUP] AUTONOMOUS SYNC — via Rebuild Coordinator")
    logger.info("=" * 70)

    try:
        from services.event_bus import BoardEvent, get_event_bus
        await get_event_bus().publish(BoardEvent(
            sport="nba",
            event_type="scheduled_safety",
            severity="medium",
            source="startup_sync",
        ))
        logger.info("[STARTUP] NBA startup event published to coordinator")
    except Exception as e:
        logger.error(f"[STARTUP] Coordinator dispatch failed, falling back to legacy: {e}")
        # Fallback: use demon_goblin if coordinator fails
        if demon_goblin_engine:
            result = await demon_goblin_engine.run_full_sync()
            logger.info(f"[STARTUP] Legacy fallback complete: {result.get('unique_players', 0)} players")


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
            
            # Step 6: Publish NBA board via Coordinator → UnifiedPipeline
            logger.info("[SCHEDULER] Step 6/10: Publishing NBA board via Coordinator...")
            try:
                from services.event_bus import BoardEvent, get_event_bus
                await get_event_bus().publish(BoardEvent(
                    sport="nba",
                    event_type="scheduled_safety",
                    severity="high",
                    source="scheduler_daily_nba",
                ))
                logger.info("[SCHEDULER] NBA board publish event dispatched to coordinator")
            except Exception as pub_e:
                logger.error(f"[SCHEDULER] Coordinator dispatch failed, falling back to legacy: {pub_e}")
                result = await demon_goblin_engine.run_full_sync()
                logger.info(f"[SCHEDULER] Legacy fallback: {result.get('unique_players', 0)} players")
            
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
        players_needing = await db[COLL("master_hub", "nba")].count_documents({
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
        players = await db[COLL("master_hub", "nba")].find({
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


async def scheduled_mlb_ticker_sync():
    """
    Scheduled job that syncs MLB ticker data (games + news).

    Mirrors scheduled_ticker_sync() for the MLB pipeline. Wired hourly so
    mlb_news cannot freeze the way it did when only the NBA ticker was
    scheduled (ticker stabilization patch).
    """
    logger.info("=" * 70)
    logger.info("[SCHEDULER] MLB TICKER SYNC (GAMES + NEWS)")
    logger.info(f"[SCHEDULER] Time: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 70)

    try:
        from routes.live import sync_mlb_todays_games, sync_mlb_news_headlines

        games_result = await sync_mlb_todays_games()
        logger.info(f"[SCHEDULER] MLB Games sync: {games_result}")

        news_result = await sync_mlb_news_headlines()
        logger.info(f"[SCHEDULER] MLB News sync: {news_result}")

        logger.info("=" * 70)

    except Exception as e:
        logger.error(f"[SCHEDULER] MLB ticker sync failed: {e}")


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


async def scheduled_forward_test_capture(sport: str, capture_phase: str):
    """
    Scheduled job firing per (sport, capture_phase) combination.

    NBA phases: morning (11 AM ET), afternoon (2 PM ET), pre_lock (5 PM ET)
    MLB phases: morning (12 PM ET), lineup_window (3 PM ET), pre_lock (6 PM ET)

    Snapshots all tier-qualified props from `{sport}_prop_scores`
    (`final-{sport}-rt`) into `forward_test_snapshots` keyed on
    (sport, capture_date, capture_phase, player, stat, line) so the three
    daily windows do not overwrite each other.
    """
    logger.info("=" * 70)
    logger.info(f"[SCHEDULER] FORWARD-TEST: {sport.upper()} CAPTURE phase={capture_phase}")
    logger.info(f"[SCHEDULER] Time: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 70)

    try:
        from services.forward_testing_service import get_forward_testing_service

        service = get_forward_testing_service(db)
        result = await service.capture_daily_snapshot(
            sport=sport,
            capture_reason=f"scheduled_{capture_phase}",
            capture_phase=capture_phase,
        )

        total = result.get("total_props", 0)
        tiers = result.get("tiers", {})
        logger.info(f"[SCHEDULER] {sport.upper()}/{capture_phase}: {total} props captured")
        for tier, count in tiers.items():
            logger.info(f"[SCHEDULER]   - {tier}: {count}")

    except Exception as e:
        logger.error(
            f"[SCHEDULER] Forward-test capture failed for "
            f"{sport}/{capture_phase}: {e}"
        )
        import traceback
        logger.error(traceback.format_exc())


async def scheduled_shadow_divergence_check():
    """
    Wave 1 — Shadow-Write Divergence Monitor (60s interval).

    Runs every 60 seconds. Compares primary vs shadow collections for
    every concept registered in
    `services.config.collection_names._SHADOW_WRITES` and appends a
    snapshot row to the shared `board_drift_ledger`. Self-disables
    silently when no concepts are shadow-mapped.
    """
    try:
        from services.observability.shadow_divergence_monitor import (
            run_shadow_divergence_check,
        )
        await run_shadow_divergence_check(db)
    except Exception as e:
        logger.error(f"[SCHEDULER] shadow_divergence_monitor failed: {e}")
        import traceback
        logger.error(traceback.format_exc())


async def scheduled_pra_audit_settle():
    """
    PRA Dual-Projection Audit — daily settle job (2026-04-23).

    Walks `nba_pra_projection_audit` and resolves `actual_pts`,
    `actual_reb`, `actual_ast`, `actual_pra` on every row whose game
    has concluded, by joining with `nba_master_hub_2026.bdl_game_logs`
    on (player_name, date-of-game_start_utc ±1 day for UTC skew).

    Idempotent — already-settled rows are skipped.

    Log line format (post-run):
      [PRA_AUDIT_CRON] settled=N skipped=M total=T pending=P

    Scheduled 0430 EST daily, 30 min after the 0400 EST BDL Game
    Logs sync has refreshed last-night's logs.
    """
    from services.cron_scheduler import run_pra_audit_settle
    try:
        await run_pra_audit_settle()
    except Exception as e:
        logger.error(f"[SCHEDULER] PRA audit settle failed: {e}")
        import traceback
        logger.error(traceback.format_exc())





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
        # Use BATCHED sync for 10x faster performance
        from services.bdl_game_logs_sync_batched import run_bdl_game_logs_sync_batched
        
        result = await run_bdl_game_logs_sync_batched(db)
        
        logger.info(f"[SCHEDULER] BDL Game Logs BATCHED sync complete:")
        logger.info(f"[SCHEDULER]   - Players synced: {result.get('players_synced', 0)}/{result.get('total_players', 0)}")
        logger.info(f"[SCHEDULER]   - Total games: {result.get('total_games', 0)}")
        logger.info(f"[SCHEDULER]   - API calls: {result.get('api_calls', 0)}")
        logger.info(f"[SCHEDULER]   - Duration: {result.get('duration_seconds', 0):.1f}s")
        logger.info("=" * 70)
        
    except Exception as e:
        logger.error(f"[SCHEDULER] BDL game logs sync failed: {e}")


async def scheduled_mlb_game_logs_sync():
    """
    Scheduled job that runs at 4:35 AM EST (09:35 UTC) daily.
    10 minutes after NBA sync to prevent API overload.
    
    Syncs game-by-game stats from BDL /mlb/v1/stats endpoint for all MLB players.
    This data is CRITICAL for accurate per-line hit rate calculations.
    
    The sync fetches 2026 season data and stores it in
    mlb_master_hub_2026.bdl_game_logs for each player.
    """
    logger.info("=" * 70)
    logger.info("[SCHEDULER] MLB BDL GAME LOGS SYNC (2026 SEASON)")
    logger.info(f"[SCHEDULER] Time: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 70)
    
    try:
        from services.bdl_universal_sync import run_bdl_universal_sync
        
        result = await run_bdl_universal_sync(
            db,
            sport="mlb",
            include_players=True,
            include_stats=True
        )
        
        logger.info(f"[SCHEDULER] MLB BDL Game Logs sync complete:")
        logger.info(f"[SCHEDULER]   - Players synced: {result.get('players_synced', 0)}")
        logger.info(f"[SCHEDULER]   - Game logs: {result.get('game_logs_synced', 0)}")
        logger.info(f"[SCHEDULER]   - Duration: {result.get('duration_seconds', 0):.1f}s")
        logger.info("=" * 70)
        
    except Exception as e:
        logger.error(f"[SCHEDULER] MLB BDL game logs sync failed: {e}")


async def scheduled_mlb_daily_sync():
    """
    MLB Daily Sync — routes through Rebuild Coordinator → UnifiedPipeline(MLBAdapter).

    Phase 3: MLB uses coordinator → single authoritative publish path.
    Legacy mlb_sync_engine preserved as fallback.
    """
    logger.info("=" * 70)
    logger.info("[SCHEDULER] MLB DAILY SYNC (via Coordinator)")
    logger.info(f"[SCHEDULER] Time: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 70)

    try:
        from services.event_bus import BoardEvent, get_event_bus
        await get_event_bus().publish(BoardEvent(
            sport="mlb",
            event_type="scheduled_safety",
            severity="high",
            source="scheduler_daily_mlb",
        ))
        logger.info("[SCHEDULER] MLB daily event dispatched to coordinator")
    except Exception as e:
        logger.error(f"[SCHEDULER] Coordinator dispatch failed: {e}")


async def scheduled_mlb_game_values_sync():
    """
    MLB BDL Game Values Enrichment - Runs at 4:13 AM EST (3 min after NBA).
    
    Uses bdl_universal_sync to refresh game logs for all MLB players.
    BATCHED: 25 players per API call.
    """
    logger.info("=" * 70)
    logger.info("[SCHEDULER] MLB BDL GAME VALUES ENRICHMENT (4:13 AM EST)")
    logger.info(f"[SCHEDULER] Time: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 70)
    
    try:
        from services.bdl_universal_sync import run_bdl_universal_sync
        
        # Only sync stats (game values), not players
        result = await run_bdl_universal_sync(
            db,
            sport="mlb",
            include_players=False,
            include_stats=True
        )
        
        logger.info(f"[SCHEDULER] MLB game values: {result.get('game_logs_synced', 0)} game logs synced")
        logger.info(f"[SCHEDULER]   - Duration: {result.get('duration_seconds', 0):.1f}s")
        logger.info("=" * 70)
        
    except Exception as e:
        logger.error(f"[SCHEDULER] MLB game values sync failed: {e}")


# =============================================================================
# WEEKEND-READY INTERVAL JOBS (High-Performance Refresh)
# =============================================================================

async def scheduled_hourly_mlb_full_sync():
    """DEPRECATED in Stage 8 (2026-04-21, MLB↔NBA carbon-copy).

    This function is retained as a shim so any still-pending APScheduler
    job pointer stays resolvable during live hot-reload. New registrations
    use the unified `run_scheduled_master_sync(sport='mlb')` via
    `SCHEDULED_SPORTS`. See `services/scheduled_sports.py`.
    """
    from services.scheduled_sports import run_scheduled_master_sync
    await run_scheduled_master_sync("mlb")


async def scheduled_hourly_full_sync():
    """DEPRECATED in Stage 8 (2026-04-21, MLB↔NBA carbon-copy).

    This function is retained as a shim so any still-pending APScheduler
    job pointer stays resolvable during live hot-reload. New registrations
    use the unified `run_scheduled_master_sync(sport='nba')` via
    `SCHEDULED_SPORTS`. See `services/scheduled_sports.py`.
    """
    from services.scheduled_sports import run_scheduled_master_sync
    await run_scheduled_master_sync("nba")


async def scheduled_hourly_badge_sync():
    """
    HOURLY CONTEXT BADGE SYNC (The Intel)
    
    Runs every 60 minutes to update context badges for all players.
    Badges: home_cookin, jet_lag, revenge, locked_in, distraction, deep_water
    """
    logger.info("=" * 70)
    logger.info("[SCHEDULER] HOURLY BADGE SYNC (INTERVAL)")
    logger.info(f"[SCHEDULER] Time: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 70)
    
    try:
        from services.context_badge_service import get_badge_service
        badge_service = get_badge_service(db)
        
        result = await badge_service.sync_badges_for_all_players(limit=500)
        
        logger.info(f"[SCHEDULER] Hourly badge sync: {result['updated']} updated, {result['skipped']} skipped")
        logger.info("=" * 70)
        
    except Exception as e:
        logger.error(f"[SCHEDULER] Hourly badge sync failed: {e}")


async def scheduled_hourly_vision_intel_sync():
    """
    HOURLY VISION INTEL ENRICHMENT (AI Summaries)
    
    Runs every 60 minutes to pre-cache AI Vision summaries for featured picks.
    This eliminates the 1+ minute JIT load times in the Vision Intel Suite.
    
    Pre-caches:
    - AI Vision Summaries (Gemini)
    - Intel Suite metrics (DvP, Pace, Stability)
    - Context badge data
    """
    logger.info("=" * 70)
    logger.info("[SCHEDULER] HOURLY VISION INTEL ENRICHMENT (INTERVAL)")
    logger.info(f"[SCHEDULER] Time: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 70)
    
    try:
        from services.vision_intel_enrichment_service import run_vision_intel_enrichment
        
        result = await run_vision_intel_enrichment(db)
        
        logger.info(f"[SCHEDULER] Vision Intel: {result.get('players_enriched', 0)} players, {result.get('ai_summaries_generated', 0)} AI summaries")
        logger.info("=" * 70)
        
    except Exception as e:
        logger.error(f"[SCHEDULER] Vision Intel Enrichment failed: {e}")


async def scheduled_hourly_injury_sync():
    """
    HOURLY INJURY SYNC (The Roster)
    
    Runs every 60 minutes to catch injury report updates.
    Critical for Usage Ripple calculations and player availability.
    
    Also triggers Vacuum Service to detect usage vacuums from injured stars.
    """
    logger.info("=" * 70)
    logger.info("[SCHEDULER] HOURLY INJURY SYNC (INTERVAL)")
    logger.info(f"[SCHEDULER] Time: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 70)
    
    if injury_service:
        try:
            result = await injury_service.sync_injuries()
            logger.info(f"[SCHEDULER] Hourly injury sync: {result.get('injuries_synced', 0)} injuries, {result.get('usage_ripple_updates', 0)} ripple updates")
            
            # ALSO trigger Vacuum Service to detect usage vacuums
            from services.injury_vacuum_service import get_vacuum_service
            vacuum_service = get_vacuum_service(db)
            vacuum_result = await vacuum_service.check_injuries()
            logger.info(f"[SCHEDULER] Vacuum check: {vacuum_result.get('vacuums_triggered', [])} vacuums triggered")
            
            logger.info("=" * 70)
        except Exception as e:
            logger.error(f"[SCHEDULER] Hourly injury sync failed: {e}")
    else:
        logger.error("[SCHEDULER] Injury service not initialized")


async def scheduled_half_hourly_social_sync():
    """
    HALF-HOURLY SOCIAL SIGNAL SYNC (The News)
    
    Runs every 30 minutes to catch late-breaking lineup news and social signals.
    Faster refresh to capture game-time decisions and injury updates.
    """
    logger.info("=" * 70)
    logger.info("[SCHEDULER] HALF-HOURLY SOCIAL SYNC (INTERVAL)")
    logger.info(f"[SCHEDULER] Time: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 70)
    
    if social_signal_engine:
        try:
            result = await social_signal_engine.sync_social_signals()
            logger.info(f"[SCHEDULER] Half-hourly social sync: {result.get('players_checked', 0)} checked, {result.get('signals_updated', 0)} updated")
            logger.info("=" * 70)
        except Exception as e:
            logger.error(f"[SCHEDULER] Half-hourly social sync failed: {e}")
    else:
        logger.error("[SCHEDULER] Social signal engine not initialized")


async def scheduled_live_injury_check():
    """
    LIVE INJURY CHECK (Every 5 minutes)
    
    Runs every 5 minutes to catch late-breaking injury news.
    This powers the "Live Injury Advantage" section on the dashboard.
    
    - Syncs fresh injury data from ESPN/BDL
    - Triggers vacuum service to detect usage vacuums
    - Updates beneficiary calculations
    """
    logger.info("[SCHEDULER] LIVE INJURY CHECK (5 min interval)")
    
    try:
        # Sync injuries from ESPN
        if injury_service:
            result = await injury_service.sync_injuries()
            logger.info(f"[SCHEDULER] Injury sync: {result.get('injuries_synced', 0)} injuries")
        
        # Trigger vacuum service
        from services.injury_vacuum_service import get_vacuum_service
        vacuum_service = get_vacuum_service(db)
        vacuum_result = await vacuum_service.check_injuries()
        
        vacuums_triggered = vacuum_result.get('vacuums_triggered', [])
        if vacuums_triggered:
            logger.info(f"[SCHEDULER] Vacuums triggered: {[v.get('injured_player') for v in vacuums_triggered]}")
            
            # Note: InjuryWatcher handles event emission to coordinator (Phase 4)
        
    except Exception as e:
        logger.error(f"[SCHEDULER] Live injury check failed: {e}")


async def scheduled_hourly_referee_sync():
    """
    HOURLY REFEREE SCRAPER (The Whistle Matrix)
    
    Runs every 60 minutes to ensure referee assignments and stats are fresh.
    Critical for Vision Intel Suite data (ref_ppg, crew_chief, whistle_class).
    
    Without hourly scraping, new picks may have missing officiating data.
    """
    logger.info("=" * 70)
    logger.info("[SCHEDULER] HOURLY REFEREE SYNC (INTERVAL)")
    logger.info(f"[SCHEDULER] Time: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 70)
    
    try:
        referee_service = get_referee_service(db)
        result = await referee_service.sync_all()
        
        logger.info(f"[SCHEDULER] Referee sync complete: {result.get('stats_count', 0)} ref stats, {result.get('assignments_count', 0)} game assignments")
        logger.info("=" * 70)
        
    except Exception as e:
        logger.error(f"[SCHEDULER] Hourly referee sync failed: {e}")


async def scheduled_game_start_scan():
    """Universal game-start scanner — every 60s, flips tipped-off props to
    active=False across every registered sport. Sport-agnostic: iterates
    `registered_sports()`. New sports are auto-covered when their adapter
    is registered in services/board/adapters/__init__.py."""
    try:
        from services.board.scanner import scan_all
        await scan_all(db)
    except Exception as e:
        logger.error(f"[SCHEDULER] Game-start scan failed: {e}")


@app.on_event("startup")
async def startup_event():
    global stats_manager, demon_goblin_engine, vision_ai_service, injury_service, raw_stat_fetcher, social_signal_engine, adaptive_sync, intel_briefing_engine, live_scores_engine, game_lock_engine, scheduler
    
    # ==========================================================================
    # PERFORMANCE: Create MongoDB indexes for fast queries
    # ==========================================================================
    logger.info("[INDEXES] Creating MongoDB indexes for performance...")
    
    try:
        # nba_cached_board - Main board with player props
        await db[COLL("board_cache", "nba")].create_index([("player_name", ASCENDING)], background=True)
        await db[COLL("board_cache", "nba")].create_index([("team", ASCENDING)], background=True)
        await db[COLL("board_cache", "nba")].create_index([("synced_at", DESCENDING)], background=True)
        await db[COLL("board_cache", "nba")].create_index([("props.stat_type", ASCENDING)], background=True)
        
        # nba_master_hub_2026 - Player stats vault
        await db[COLL("master_hub", "nba")].create_index([("display_name", ASCENDING)], background=True)
        await db[COLL("master_hub", "nba")].create_index([("bdl_id", ASCENDING)], unique=True, sparse=True, background=True)
        await db[COLL("master_hub", "nba")].create_index([("nba_id", ASCENDING)], sparse=True, background=True)
        await db[COLL("master_hub", "nba")].create_index([("team", ASCENDING)], background=True)
        
        # odds_api_mapping_master - Player name mapping
        await db[COLL("odds_mapping", "nba")].create_index([("odds_api_name", ASCENDING)], background=True)
        await db[COLL("odds_mapping", "nba")].create_index([("hub_player_name", ASCENDING)], background=True)
        await db[COLL("odds_mapping", "nba")].create_index([("bdl_id", ASCENDING)], sparse=True, background=True)
        
        # dg_stats_cache - Stats cache with TTL
        await db.dg_stats_cache.create_index([("player_name", ASCENDING), ("stat_type", ASCENDING)], unique=True, background=True)
        await db.dg_stats_cache.create_index([("cached_at", ASCENDING)], expireAfterSeconds=21600, background=True)  # 6-hour TTL
        
        # dg_war_zone, dg_front_lines, dg_goblin_vault - Tier collections
        await db.dg_war_zone.create_index([("synced_at", DESCENDING)], background=True)
        await db.dg_front_lines.create_index([("synced_at", DESCENDING)], background=True)
        await db.dg_goblin_vault.create_index([("synced_at", DESCENDING)], background=True)
        
        # COMPOUND INDEXES for high-performance queries
        await db[COLL("board_cache", "nba")].create_index([("player_name", ASCENDING), ("nba_id", ASCENDING)], background=True)
        await db[COLL("board_cache", "nba")].create_index([("is_active", ASCENDING), ("props.stat_type", ASCENDING)], background=True)
        await db[COLL("master_hub", "nba")].create_index([("player_name", ASCENDING), ("nba_id", ASCENDING)], background=True)
        await db[COLL("master_hub", "nba")].create_index([("is_active", ASCENDING), ("team", ASCENDING)], background=True)
        
        # TIER INDEXES - Optimized for static tier queries (is_demon, is_goblin, h10_rate)
        await db[COLL("board_cache", "nba")].create_index([
            ("props.is_demon", ASCENDING), 
            ("props.is_goblin", ASCENDING),
            ("props.commence_time", ASCENDING)
        ], background=True)
        await db[COLL("board_cache", "nba")].create_index([
            ("props.h10_rate", DESCENDING),
            ("props.is_goblin", ASCENDING)
        ], background=True)
        
        # SSOT Tier F #3 (2026-05-04, Option C): dg_cached_board_temp
        # shadow-sync collection dropped. It was orphaned (last write
        # 2026-04-23) and had zero active readers/writers in the
        # codebase. Index creation removed below; collection dropped
        # manually via Mongo.
        
        # ticker_headlines - Per-headline lifecycle tracking
        await db[COLL.shared("ticker_headlines")].create_index([("fingerprint", ASCENDING)], unique=True, background=True)
        await db[COLL.shared("ticker_headlines")].create_index([("first_seen_at", DESCENDING)], background=True)

        # 2026-05-07 Step 3: dirty-queue compound index (sport + _id) so
        # the drain query `find({sport}).sort(_id).limit(N)` is index-
        # backed even as the queue accumulates millions of rows
        # historically.
        try:
            from services.delta.dirty_queue import ensure_indexes as _dq_ensure
            await _dq_ensure(db)
        except Exception as _dq_idx_err:
            logger.warning(f"[INDEXES] dirty_queue index create failed: {_dq_idx_err}")

        logger.info("[INDEXES] MongoDB indexes created successfully (including compound indexes)")
    except Exception as e:
        logger.error(f"[INDEXES] Error creating indexes: {e}")
    
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
    
    # Demon & Goblin Engine DELETED (hard consolidation 2026-04-22). NBA + MLB
    # now go through the universal path: universal_odds_sync + recompute_sport.

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
    
    # Initialize VK Model Enforcement with MongoDB reference for TRUE VARIANCE lookups
    from services.vk_model_enforcement import set_db_reference as set_vk_db_reference
    set_vk_db_reference(db)
    logger.info("VK Model Enforcement v2.0 initialized (TRUE VARIANCE - L10 std_dev lookups)")
    
    # Initialize Adaptive Sync Engine - Mission-critical polling
    adaptive_sync = init_adaptive_sync_engine(db, ODDS_API_KEY)
    logger.info("Adaptive Sync Engine initialized (Mission-Critical Polling)")
    
    # Wire adaptive sync callback to the live_props writer.
    async def _adaptive_sync_callback():
        # 2026-05-07 P0-A (callback split): the adaptive sync engine
        # is responsible for keeping `{sport}_live_props` fresh on the
        # 5-min STANDBY cadence — and only that. Previously it called
        # `run_master_sync(sport)`, which is a 7-10 min full pipeline
        # (odds_sync + recompute + Vision Intel enrichment + board
        # build). Doing all that work inline blew the <300s ingestion
        # SLO every cycle, because each sport's live_props row was
        # only fresh for ~5 min out of every ~17-min combined cycle.
        #
        # The split sends the heavy work to its proper owners:
        #   - Real-time scoring   → delta engine (per 30s tick, driven
        #                            by `delta_dirty_queue` enqueues
        #                            this callback produces).
        #   - Hourly board build  → existing APScheduler crons
        #                            (`hourly_nba_master_sync`,
        #                            `hourly_mlb_master_sync`).
        #   - Vision Intel        → master_sync's enrichment phase,
        #                            which now runs only inside the
        #                            hourly cron, NOT every 5 min.
        #
        # Sports run sequentially under per-sport UpstreamSyncLock so
        # each acquisition is local and either sport's failure is
        # isolated. Intra-cycle heartbeat write between sports keeps
        # the watchdog alive across the combined ~2-3 min cycle.
        from services.universal_odds_sync import get_universal_odds_service
        from services.upstream_sync_lock import get_upstream_sync_lock
        lock = get_upstream_sync_lock()
        service = get_universal_odds_service(db)
        results: Dict[str, Any] = {}
        for _idx, _sport in enumerate(("nba", "mlb")):
            sport_t0 = datetime.now(timezone.utc)
            logger.info(
                f"[ADAPTIVE_SYNC] BEGIN sync_sport_props({_sport})"
            )
            try:
                async with lock.exclusive(
                    _sport, holder="adaptive_sync_callback",
                ):
                    sport_result = await service.sync_sport_props(
                        sport=_sport,
                        enrich_features=False,
                    )
                results[_sport] = sport_result
                dur = (
                    datetime.now(timezone.utc) - sport_t0
                ).total_seconds()
                inserted = (
                    sport_result.get("props_synced", 0)
                    if isinstance(sport_result, dict) else 0
                )
                events = (
                    sport_result.get("events_synced", 0)
                    if isinstance(sport_result, dict) else 0
                )
                logger.info(
                    f"[ADAPTIVE_SYNC] END   sync_sport_props({_sport}) "
                    f"dur={dur:.1f}s events={events} props={inserted}"
                )
            except Exception as _ms_err:  # noqa: BLE001
                dur = (
                    datetime.now(timezone.utc) - sport_t0
                ).total_seconds()
                logger.error(
                    f"[ADAPTIVE_SYNC] FAIL  sync_sport_props({_sport}) "
                    f"dur={dur:.1f}s err={_ms_err!r}"
                )
                results[_sport] = {"error": repr(_ms_err)}

            # Intra-cycle heartbeat — keeps the watchdog alive across
            # the sequential sport cycle. The split callback is fast
            # (~30-90s per sport) so this is mostly defensive, but it
            # also distinguishes a hung NBA call from a hung MLB call
            # in the heartbeat doc (`after_sport` field).
            try:
                await db["adaptive_sync_heartbeat"].update_one(
                    {"_id": "adaptive_sync"},
                    {"$set": {
                        "last_heartbeat_at": datetime.now(timezone.utc),
                        "inter_sport_marker": True,
                        "after_sport": _sport,
                    }},
                    upsert=True,
                )
            except Exception as _hb_err:
                logger.warning(
                    f"[ADAPTIVE_SYNC] inter-sport heartbeat write "
                    f"failed after {_sport}: {_hb_err}"
                )

        # The engine logs `Sync complete` using `demons_count` and
        # `goblins_count` for backwards-compat. The split callback
        # doesn't compute those (that's a master_sync-side concern),
        # so report 0 — the hourly master_sync run still produces them.
        return {
            "demons_count": 0,
            "goblins_count": 0,
            "nba": results.get("nba") or {},
            "mlb": results.get("mlb") or {},
        }
    adaptive_sync.set_sync_callback(_adaptive_sync_callback)
    logger.info(
        "[ADAPTIVE_SYNC] Callback wired to live_props writer "
        "(sync_sport_props NBA → MLB sequential under per-sport "
        "UpstreamSyncLock; intra-cycle heartbeat). Master sync stays "
        "on APScheduler/RebuildCoordinator."
    )
    
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
    from services.engines.ai_context_engine import AiContextEngine
    
    # Create master hub function references
    master_hub_funcs = {
        "fetchPlayerIntel": fetchPlayerIntel,
        "fetchPlayerIntelByName": fetchPlayerIntelByName,
        "hubSearchPlayers": hubSearchPlayers,
        "getHubStats": getHubStats,
        "runHubSync": runHubSync,
        "get_master_hub": lambda: get_master_hub(db)
    }
    
    # Initialize APScheduler for daily and weekly syncs.
    #
    # Job persistence: use MongoDBJobStore so interval jobs survive backend
    # restarts. Without it, every restart resets `next_run_time` on every
    # interval job to (now + interval), which in production can delay
    # hourly syncs by up to 60 minutes after a redeploy.
    #
    # Interval jobs go through _register_interval_job() (defined below) so
    # their persisted next_run_time is preserved across restarts. Cron jobs
    # keep using replace_existing=True because their next_run_time is
    # deterministic from the cron expression.
    try:
        _scheduler_mongo_client = MongoClient(
            os.environ.get("MONGO_URL"),
            serverSelectionTimeoutMS=10000,
        )
        _scheduler_jobstores = {
            "default": MongoDBJobStore(
                database=os.environ.get("DB_NAME", "pick_vision"),
                collection="scheduler_jobs",
                client=_scheduler_mongo_client,
            )
        }
        scheduler = AsyncIOScheduler(
            jobstores=_scheduler_jobstores,
            timezone=SCHEDULER_TIMEZONE,
        )
        logger.info("[SCHEDULER] MongoDBJobStore enabled (collection=scheduler_jobs)")
    except Exception as e:
        logger.warning(
            f"[SCHEDULER] MongoDBJobStore init failed ({e}); falling back to "
            "in-memory jobstore. Restarts will skew next_run times."
        )
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
        db=db,
        injury_service=injury_service,
        vision_service=vision_ai_service,
        live_scores_engine=live_scores_engine,
        ai_context_engine_class=AiContextEngine,
        master_hub_funcs=master_hub_funcs,
        get_odds_mapper_func=get_odds_api_mapper,
        social_signal_engine=social_signal_engine,
        stats_manager=stats_manager,
    )
    logger.info("[ROUTES] Modular routes registered from /routes/ directory (Phase 18: +4 new modules)")
    
    # NOTE: The api_router below contains routes that will be migrated in future phases
    # For now, they coexist with the modular routes
    app.include_router(api_router)
    logger.info("[ROUTES] Legacy api_router routes included (migration in progress)")

    # 2026-04-28 — Universal sync health endpoint (Phase 7 of sync hardening).
    # Read-only aggregator for ops dashboards. No model state touched.
    try:
        from routes import health_sync as _health_sync
        _health_sync.set_db(db)
        app.include_router(_health_sync.router)
        # Also bootstrap sync_locks indexes (Phase 2). Idempotent.
        from services.sync_lock import ensure_indexes as _sync_lock_ensure
        await _sync_lock_ensure(db)
        # Contract-enforcer violation TTL collection (2026-04-29). Idempotent.
        from services.contract_enforcer import ensure_indexes as _ce_ensure
        await _ce_ensure(db)
        # Universal Stable Board Publisher (2026-04-29). Idempotent.
        from services.board.publisher import ensure_indexes as _bp_ensure
        await _bp_ensure(db)
        # Universal `active`-transition audit collection (2026-05-04).
        # Backs SSOT enforcement of the `active` field on
        # {sport}_prop_scores; TTL-indexed so the table self-prunes.
        from services.board.set_active import ensure_indexes as _sa_ensure
        await _sa_ensure(db)
        # SSOT Tier F (2026-05-04) — 7-day TTL on non-live prop-score
        # rows. `_project_score_doc` stamps `ttl_at` on every write
        # whose version_tag is outside _LIVE_VERSION_TAGS; this index
        # makes Mongo self-prune those rows. Live docs never carry
        # `ttl_at` and are immune by absence.
        from services.scoring.prop_scores_store import ensure_ttl_index as _ttl_ensure
        for _sport in ("nba", "mlb"):
            await _ttl_ensure(db, _sport)
        # Vision v2 SHADOW Board Publisher (2026-04-29). Idempotent.
        from services.board.shadow_publisher import ensure_indexes as _shp_ensure
        await _shp_ensure(db)
        # 2026-04-29 — admin-gated debug snapshots (tuning-only, NOT
        # consumed by the live dashboard). Holds short locks per-call.
        from routes import debug_snapshots as _debug_snapshots
        _debug_snapshots.set_db(db)
        app.include_router(_debug_snapshots.router, prefix="/api")
        # 2026-05-12 — token-gated DB-backup downloader. Lets the user
        # pull the preview-DB tarball + manifest files via HTTPS link
        # so they can mongorestore them to their production server.
        from routes import admin_backups as _admin_backups
        app.include_router(_admin_backups.router, prefix="/api")
        # 2026-05-14 — duplicate-prop + probability-trace diagnostics.
        # See routes/admin_diagnostics.py.
        from routes import admin_diagnostics as _admin_diagnostics
        _admin_diagnostics.set_db(db)
        app.include_router(_admin_diagnostics.router)
        # 2026-05-15 — system-wide ephemeral data cleanup utility.
        # See services/cleanup/ephemeral_cleanup.py.
        from routes import admin_ephemeral_cleanup as _admin_ephemeral
        _admin_ephemeral.set_db(db)
        app.include_router(_admin_ephemeral.router)
        # 2026-05-15 — universal cached board lifecycle admin.
        from routes import admin_board_lifecycle as _admin_board_lifecycle
        _admin_board_lifecycle.set_db(db)
        app.include_router(_admin_board_lifecycle.router)
        # Ensure TTL indexes on ttl_purge_at for every ephemeral
        # collection (idempotent — Mongo no-ops on re-create).
        try:
            from services.cleanup.ephemeral_cleanup import (
                ensure_ttl_indexes as _ensure_ephemeral_indexes,
            )
            _ttl_res = await _ensure_ephemeral_indexes(db)
            logger.info(
                "[EPHEMERAL_CLEANUP] startup TTL indexes ensured: %s",
                _ttl_res,
            )
        except Exception as _exc:  # noqa: BLE001
            logger.warning(
                "[EPHEMERAL_CLEANUP] startup TTL index ensure failed: %s",
                _exc,
            )
        # 2026-05-15 — universal cached board lifecycle startup audit.
        try:
            _bl_findings = await _admin_board_lifecycle.startup_validate(db)
            logger.info(
                "[BOARD_LIFECYCLE_VALIDATE] startup audit: %s",
                _bl_findings,
            )
        except Exception as _bl_exc:  # noqa: BLE001
            logger.warning(
                "[BOARD_LIFECYCLE_VALIDATE] startup audit failed: %s",
                _bl_exc,
            )
        logger.info(
            "[ROUTES] /api/health/sync + /api/health/contracts + "
            "/api/debug/snapshots/* + /api/admin/backups/* + "
            "/api/v3/admin/duplicate-props + /api/v3/admin/probability-trace "
            "registered + sync_locks + contract_violations indexes ensured"
        )
    except Exception as _hs_err:  # noqa: BLE001
        logger.error(f"[ROUTES] health_sync wiring failed: {_hs_err}")
    
    # Start the adaptive sync engine (background polling)
    if ODDS_API_KEY:
        await adaptive_sync.start()
        logger.info("[ADAPTIVE_SYNC] Background polling STARTED")
    else:
        logger.warning("[ADAPTIVE_SYNC] No Odds API key - adaptive sync disabled")
    
    # ==========================================================================
    # MLB STARTUP HEALTH CHECK - Auto-populate on pod fork
    # =========================================================================
    # If MLB collections are empty (common after pod fork), automatically sync
    # This prevents the "empty MLB board" issue on fresh environments
    # ==========================================================================
    await run_mlb_startup_health_check()

    # ==========================================================================
    # D5 (2026-04-21) — DELTA ENGINE CONTINUOUS LOOPS
    # ==========================================================================
    # One `DeltaEngine.run_forever(sport)` background task per sport with
    # `delta_enabled=True` in SCHEDULED_SPORTS. Sport-agnostic: adding a
    # new sport to the live delta layer is a single flag in the registry.
    # Respects UpstreamSyncLock (full-sync holds exclusive, delta ticks
    # clean-skip). Never calls upstream APIs — enforced by
    # tests/test_delta_upstream_isolation.py.
    # ==========================================================================
    try:
        from services.scheduled_sports import start_delta_engine_loops
        delta_startup = start_delta_engine_loops(db)
        logger.info(f"[DELTA_ENGINE] startup: {delta_startup}")
    except Exception as exc:
        logger.error(f"[DELTA_ENGINE] startup failed: {exc}")

    # ==========================================================================
    # CANONICAL COLLECTION HEALTH CHECK - one-shot, log-only
    # =========================================================================
    # Compares config/collections.py SPORT_OVERRIDES against the actual
    # MongoDB namespace. Warns on:
    #   OVERRIDE_MISSING — override points at a legacy coll that doesn't exist
    #   CANONICAL_BLEED  — both legacy + canonical carry data (drift)
    #   CANONICAL_READY  — canonical populated, override not yet retired
    #   LEGACY_EMPTY     — override points at an empty coll w/ no canonical
    # Never raises, never blocks startup. Runs once per restart.
    # ==========================================================================
    try:
        from services.board.health_check import run_canonical_collection_health_check
        await run_canonical_collection_health_check(db)
    except Exception as _e:
        logger.warning(f"[COLL_HEALTH] Audit skipped due to error: {_e}")

    # ==========================================================================
    # UNIVERSAL BOARD ENGINE — Step 5: real-time 'new_props' subscriber
    # =========================================================================
    # Subscribes a single, sport-agnostic handler to the event bus. Any
    # publisher (odds-sync, manual refresh, future watchers) that detects
    # net-new canonical_keys can emit BoardEvent(event_type='new_props',
    # sport=..., metadata={'canonical_keys': [...]}) and the engine will
    # score + UPSERT just those keys into {sport}_prop_scores. The
    # universal board reader surfaces them instantly — no full rebuild.
    # ==========================================================================
    try:
        from services.board.engine import subscribe_new_props_handler
        subscribe_new_props_handler(db)
    except Exception as _e:
        logger.error(f"[BOARD_ENGINE] Failed to subscribe new_props handler: {_e}")

    # ==========================================================================
    # DRIFT AUDIT PERSISTENT LEDGER — 72h TTL on board_drift_ledger
    # =========================================================================
    # Ensures TTL + secondary indexes on the persistent drift ledger so
    # 48h Step 6 A/B convergence reports survive backend restarts.
    # Idempotent — safe to call on every boot.
    # ==========================================================================
    try:
        from services.board.drift_audit import ensure_persistent_indexes
        await ensure_persistent_indexes(db)
    except Exception as _e:
        logger.error(f"[DRIFT_AUDIT] Failed to ensure persistent indexes: {_e}")
    
    # ==========================================================================
    # WEEKEND-READY SCHEDULER: High-Performance Interval System
    # ==========================================================================
    
    # ==========================================================================
    # DAILY SYNC SCHEDULE (4:00 AM EST / 9:00 AM UTC)
    # ORDER IS CRITICAL: Sync data FIRST, then run pipelines to recalculate
    #
    # NBA Schedule:
    # 4:00-4:08 AM - NBA.com L5/L10 batches (sync fresh stats)
    # 4:10 AM - NBA BDL Game Values (sync game values)
    # 4:15 AM - NBA BDL Game Logs (sync game logs) 
    # 4:20 AM - NBA Daily Pipeline (recalculate with fresh data)
    #
    # MLB Schedule (3 min after each NBA job):
    # 4:13 AM - MLB BDL Game Values (sync)
    # 4:18 AM - MLB BDL Game Logs (sync)
    # 4:23 AM - MLB Daily Pipeline (recalculate with fresh data)
    # ==========================================================================
    
    # --------------------------------------------------------------------
    # Interval-job registration helper
    # --------------------------------------------------------------------
    # Problem: re-calling scheduler.add_job(..., replace_existing=True) on
    # an IntervalTrigger job overwrites the persisted next_run_time in
    # MongoDBJobStore with (now + interval). On every backend restart that
    # delays hourly syncs by up to 60 minutes and 5-min checks by up to
    # 5 minutes - exactly the stale-pipeline bug we're fixing.
    #
    # Fix: if the interval job is already persisted in the jobstore, skip
    # re-registration entirely so its real next_run_time survives the
    # restart. Cron/date jobs are unaffected (their next_run_time is
    # deterministic) and keep using replace_existing=True so code/schedule
    # edits still propagate.
    #
    # NOTE: scheduler.get_job() ONLY consults _pending_jobs when the
    # scheduler is STATE_STOPPED, so we must query the MongoDB collection
    # directly to know whether the job is already persisted.
    # --------------------------------------------------------------------
    try:
        _scheduler_jobs_coll = _scheduler_mongo_client[
            os.environ.get("DB_NAME", "pick_vision")
        ]["scheduler_jobs"]
    except Exception:
        _scheduler_jobs_coll = None
    
    def _register_interval_job(func, trigger, *, job_id, name):
        already_persisted = False
        if _scheduler_jobs_coll is not None:
            try:
                already_persisted = (
                    _scheduler_jobs_coll.count_documents({"_id": job_id}, limit=1) > 0
                )
            except Exception as _e:
                logger.warning(
                    f"[SCHEDULER] Existence check failed for '{job_id}' ({_e}); "
                    "falling back to fresh registration."
                )
                already_persisted = False
        if already_persisted:
            logger.info(
                f"[SCHEDULER] Preserving persisted interval job '{job_id}' "
                f"(next_run_time kept)"
            )
            return
        logger.info(f"[SCHEDULER] Registering new interval job '{job_id}'")
        scheduler.add_job(
            func, trigger,
            id=job_id, name=name, replace_existing=False
        )
    
    # =========================================================================
    # Stage 8 (2026-04-21, MLB↔NBA carbon-copy): unified per-sport master-sync
    # registration. Adding a new sport (e.g. NFL) is a single-line entry in
    # `services.scheduled_sports.SCHEDULED_SPORTS` + the matching serialisable
    # module-level callable — NO server.py edits required. Replaces the former
    # hand-written `hourly_full_sync` and `hourly_mlb_full_sync` registrations.
    # Eliminates D7.
    # =========================================================================
    from services.scheduled_sports import (
        SCHEDULED_SPORTS,
        SPORT_INTERVAL_CALLABLES,
    )

    for _sport_key, _sport_cfg in SCHEDULED_SPORTS.items():
        _interval_callable = SPORT_INTERVAL_CALLABLES.get(_sport_key)
        if _interval_callable is None:
            logger.warning(
                f"[SCHEDULER] sport={_sport_key!r} in SCHEDULED_SPORTS but "
                "not in SPORT_INTERVAL_CALLABLES; skipping"
            )
            continue
        # Per-sport hourly master-sync interval job.
        _register_interval_job(
            _interval_callable,
            IntervalTrigger(
                minutes=_sport_cfg.master_sync_interval_minutes,
                timezone=SCHEDULER_TIMEZONE,
            ),
            job_id=f"hourly_{_sport_key}_master_sync",
            name=(
                f"Hourly {_sport_key.upper()} Master Sync "
                f"({_sport_cfg.master_sync_interval_minutes} min interval) "
                "[SCHEDULED_SPORTS]"
            ),
        )
    
    # 2. HOURLY BADGE SYNC (The Intel) - Every 60 minutes
    # Updates context badges for all players
    _register_interval_job(
        scheduled_hourly_badge_sync,
        IntervalTrigger(minutes=60, timezone=SCHEDULER_TIMEZONE),
        job_id='hourly_badge_sync',
        name='Hourly Badge Sync (60 min interval)',
    )

    # 2b. JIT VISION INTEL REAPER (closes the 0-60 min cold-pick window)
    # Every 5 minutes the reaper inspects ONLY the visible board for
    # canonical_keys whose LIVE-tag score doc carries no `vision_intel`.
    # If 0 are uncovered → cheap no-op (no Gemini calls). Otherwise the
    # existing `_enrich_*_board_vision_intel` is invoked, which content-
    # hash-filters and writes via the LIVE+BASELINE mirror (writer fix-D).
    # See services/jit_vision_intel_reaper.py for the full audit + caps.
    from services.jit_vision_intel_reaper import (
        scheduled_jit_vision_intel_reaper,
        JIT_REAPER_INTERVAL_MINUTES,
    )
    _register_interval_job(
        scheduled_jit_vision_intel_reaper,
        IntervalTrigger(
            minutes=JIT_REAPER_INTERVAL_MINUTES,
            timezone=SCHEDULER_TIMEZONE,
        ),
        job_id='jit_vision_intel_reaper',
        name=(
            f'JIT Vision Intel Reaper '
            f'({JIT_REAPER_INTERVAL_MINUTES} min interval, '
            f'visible-board-only)'
        ),
    )
    
    # 3. HOURLY INJURY SYNC (The Roster) - Every 60 minutes
    # Catches injury report updates for Usage Ripple
    _register_interval_job(
        scheduled_hourly_injury_sync,
        IntervalTrigger(minutes=60, timezone=SCHEDULER_TIMEZONE),
        job_id='hourly_injury_sync',
        name='Hourly Injury Sync (60 min interval)',
    )
    
    # 4. LIVE INJURY CHECK (Every 5 minutes)
    # Powers the "Live Injury Advantage" section - needs frequent updates
    _register_interval_job(
        scheduled_live_injury_check,
        IntervalTrigger(minutes=5, timezone=SCHEDULER_TIMEZONE),
        job_id='live_injury_check',
        name='Live Injury Check (5 min interval)',
    )
    
    # NOTE: Vision Intel enrichment now runs at the END of every sync cycle
    # in adaptive_sync_engine.py - no separate scheduled job needed
    
    # 5. HALF-HOURLY SOCIAL SYNC (The News) - Every 30 minutes
    # Catches late-breaking lineup news and social signals
    _register_interval_job(
        scheduled_half_hourly_social_sync,
        IntervalTrigger(minutes=30, timezone=SCHEDULER_TIMEZONE),
        job_id='half_hourly_social_sync',
        name='Half-Hourly Social Sync (30 min interval)',
    )
    
    # 6. HOURLY REFEREE SYNC (The Whistle Matrix) - Every 60 minutes
    # Scrapes referee assignments and stats for Vision Intel Suite
    # Critical for new picks to have officiating data (ref_ppg, crew_chief, whistle_class)
    _register_interval_job(
        scheduled_hourly_referee_sync,
        IntervalTrigger(minutes=60, timezone=SCHEDULER_TIMEZONE),
        job_id='hourly_referee_sync',
        name='Hourly Referee Sync (60 min interval)',
    )
    
    # 7. UNIVERSAL GAME-START SCANNER — Every 60 seconds
    # Flips tipped-off props to active=False across every registered sport.
    # Sport-agnostic by design (iterates registered_sports() inside).
    # The universal board reader filters active=False rows, so game-started
    # props disappear from the board within ~1 minute of tip-off with
    # zero rebuild / publish work.
    _register_interval_job(
        scheduled_game_start_scan,
        IntervalTrigger(seconds=60, timezone=SCHEDULER_TIMEZONE),
        job_id='universal_game_start_scanner',
        name='Universal Game-Start Scanner (60s interval)',
    )
    
    # ==========================================================================
    # DAILY SYNC JOBS - Correct Order: SYNC DATA FIRST, THEN PIPELINE
    # ==========================================================================
    
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
    scheduler.add_job(
        scheduled_bdl_game_values_sync,
        CronTrigger(hour=9, minute=10, timezone=SCHEDULER_TIMEZONE),
        id='bdl_game_values_sync',
        name='4:10 AM EST NBA BDL Game Values Sync',
        replace_existing=True
    )
    
    # MLB BDL Game Values Enrichment at 4:13 AM EST (9:13 AM UTC) - 3 min after NBA
    scheduler.add_job(
        scheduled_mlb_game_values_sync,
        CronTrigger(hour=9, minute=13, timezone=SCHEDULER_TIMEZONE),
        id='mlb_bdl_game_values_sync',
        name='4:13 AM EST MLB BDL Game Values Sync',
        replace_existing=True
    )
    
    # NBA BDL Game Logs Sync at 4:15 AM EST (09:15 AM UTC)
    scheduler.add_job(
        scheduled_bdl_game_logs_sync,
        CronTrigger(hour=9, minute=15, timezone=SCHEDULER_TIMEZONE),
        id='bdl_game_logs_sync',
        name='4:15 AM EST NBA BDL Game Logs Sync',
        replace_existing=True
    )
    
    # MLB BDL Game Logs Sync at 4:18 AM EST (09:18 AM UTC) - 3 min after NBA
    scheduler.add_job(
        scheduled_mlb_game_logs_sync,
        CronTrigger(hour=9, minute=18, timezone=SCHEDULER_TIMEZONE),
        id='mlb_bdl_game_logs_sync',
        name='4:18 AM EST MLB BDL Game Logs Sync',
        replace_existing=True
    )
    
    # NBA Daily Pipeline at 4:20 AM EST (9:20 AM UTC) - AFTER all data syncs
    # Recalculates hit rates with fresh data
    scheduler.add_job(
        scheduled_daily_sync,
        CronTrigger(hour=9, minute=20, timezone=SCHEDULER_TIMEZONE),
        id='daily_hard_refresh',
        name='4:20 AM EST NBA Daily Pipeline (with fresh data)',
        replace_existing=True
    )
    
    # MLB Daily Pipeline at 4:23 AM EST (9:23 AM UTC) - 3 min after NBA
    # Recalculates hit rates with fresh data
    scheduler.add_job(
        scheduled_mlb_daily_sync,
        CronTrigger(hour=9, minute=23, timezone=SCHEDULER_TIMEZONE),
        id='mlb_daily_refresh',
        name='4:23 AM EST MLB Daily Pipeline (with fresh data)',
        replace_existing=True
    )
    
    # Ticker Sync — every 15 minutes (NBA games + news).
    # Ticker stabilization patch: previously daily; now :00, :15, :30, :45.
    scheduler.add_job(
        scheduled_ticker_sync,
        CronTrigger(minute='0,15,30,45', timezone=SCHEDULER_TIMEZONE),
        id='ticker_sync',
        name='Every 15 min — NBA Ticker Games/News Sync',
        replace_existing=True
    )

    # MLB Ticker Sync — every 15 minutes (MLB games + news).
    # Offset by 5 min from NBA cadence (:05, :20, :35, :50) so the two
    # ticker jobs never overlap their HTTP fetches.
    scheduler.add_job(
        scheduled_mlb_ticker_sync,
        CronTrigger(minute='5,20,35,50', timezone=SCHEDULER_TIMEZONE),
        id='mlb_ticker_sync',
        name='Every 15 min — MLB Ticker Games/News Sync',
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
    
    # Forward-Testing: multi-phase daily prop captures (2026-05-08 upgrade).
    # Replaces the single 22:30 UTC / 6:30 PM ET legacy capture with three
    # market-state snapshots per sport so we can measure edge decay and
    # calibration drift across the day. Uniqueness key on the snapshot
    # collection now includes `capture_phase`.
    #
    # NBA  : 11 AM ET → morning      | 2 PM ET → afternoon     | 5 PM ET → pre_lock
    # MLB  : 12 PM ET → morning      | 3 PM ET → lineup_window | 6 PM ET → pre_lock
    _FORWARD_TEST_PHASES = (
        ("nba", "morning",       11),
        ("nba", "afternoon",     14),
        ("nba", "pre_lock",      17),
        ("mlb", "morning",       12),
        ("mlb", "lineup_window", 15),
        ("mlb", "pre_lock",      18),
    )
    for _sport, _phase, _hour_et in _FORWARD_TEST_PHASES:
        scheduler.add_job(
            scheduled_forward_test_capture,
            CronTrigger(hour=_hour_et, minute=0, timezone=SCHEDULER_TIMEZONE),
            id=f"forward_test_capture_{_sport}_{_phase}",
            name=f"Forward-Test: {_sport.upper()} {_phase} capture",
            kwargs={"sport": _sport, "capture_phase": _phase},
            replace_existing=True,
        )

    # PRA Dual-Projection Audit settle (2026-04-23).
    # Runs 4:30 AM EST, right after 4:15 AM BDL Game Logs sync has
    # refreshed last-night's per-game pts/reb/ast. Walks
    # `nba_pra_projection_audit` and joins actuals onto every row
    # whose game has concluded. Idempotent + safe to run repeatedly.
    scheduler.add_job(
        scheduled_pra_audit_settle,
        CronTrigger(hour=4, minute=30, timezone=SCHEDULER_TIMEZONE),
        id='pra_audit_settle',
        name='4:30 AM EST PRA Dual-Projection Audit Settle',
        replace_existing=True
    )

    # ----------------------------------------------------------------------
    # Phase 4 — Migrate MLB host-cron into APScheduler  (2026-04-28)
    # ----------------------------------------------------------------------
    # All three jobs are sync_lock-protected so accidental overlap
    # (manual trigger + cron tick + admin route) is auto-skipped.  See
    # /app/backend/services/scheduled/mlb_jobs.py for the lock keys
    # and step-level orchestration.  Replaces:
    #   - scripts/run_mlb_daily_pipeline.sh   (host-cron 04:00 UTC)
    #   - scripts/run_mlb_pregame_lineups.sh  (host-cron 22:00 UTC)
    # ----------------------------------------------------------------------
    try:
        from services.scheduled.mlb_jobs import (
            mlb_daily_pipeline as _mlb_daily_pipeline,
            mlb_pregame_lineups_18utc as _mlb_pregame_18,
            mlb_pregame_lineups_22utc as _mlb_pregame_22,
            mlb_pick_grade as _mlb_pick_grade,
        )

        # Daily pipeline at 04:00 UTC.  Holds sync:mlb for ≤30 min.
        scheduler.add_job(
            _mlb_daily_pipeline,
            CronTrigger(hour=4, minute=0, timezone=timezone.utc),
            id='mlb_daily_pipeline',
            name='04:00 UTC MLB Daily Pipeline (statcast+features+score)',
            replace_existing=True,
            misfire_grace_time=900,
        )

        # Pre-game lineup ingest x2: 18:00 UTC + 22:00 UTC.  Holds lineup:mlb.
        scheduler.add_job(
            _mlb_pregame_18,
            CronTrigger(hour=18, minute=0, timezone=timezone.utc),
            id='mlb_lineups_early',
            name='18:00 UTC MLB Pregame Lineup Ingest (early)',
            replace_existing=True,
            misfire_grace_time=600,
        )
        scheduler.add_job(
            _mlb_pregame_22,
            CronTrigger(hour=22, minute=0, timezone=timezone.utc),
            id='mlb_lineups_final',
            name='22:00 UTC MLB Pregame Lineup Ingest (final)',
            replace_existing=True,
            misfire_grace_time=600,
        )

        # MLB pick grader at 05:00 UTC — well after 04:00 UTC pipeline.
        scheduler.add_job(
            _mlb_pick_grade,
            CronTrigger(hour=5, minute=0, timezone=timezone.utc),
            id='mlb_pick_grade',
            name='05:00 UTC MLB Pick Result Grader',
            replace_existing=True,
            misfire_grace_time=600,
        )
        logger.info(
            "[SCHEDULER] Phase 4 MLB jobs registered: "
            "mlb_daily_pipeline (04:00), mlb_lineups_early (18:00), "
            "mlb_lineups_final (22:00), mlb_pick_grade (05:00)"
        )
    except Exception as _phase4_err:  # noqa: BLE001
        logger.error(
            "[SCHEDULER] Phase 4 MLB job registration FAILED: %s "
            "(host-cron remains as fallback)", _phase4_err,
        )

    # Wave 1 — Shadow-Write Divergence Monitor (every 60 seconds).
    # Compares primary vs shadow collections for every concept registered
    # in `services.config.collection_names._SHADOW_WRITES`. Writes
    # snapshots to `board_drift_ledger` and logs alerts on divergence.
    # Self-disables when the shadow map is empty — no behavior change.
    scheduler.add_job(
        scheduled_shadow_divergence_check,
        IntervalTrigger(seconds=60, timezone=SCHEDULER_TIMEZONE),
        id='shadow_divergence_monitor',
        name='Wave 1 Shadow-Write Divergence Monitor (60s)',
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    scheduler.start()

    # Retire the legacy single-shot forward-test capture job (2026-05-08).
    # Replaced by the six per-(sport, phase) jobs above. The MongoDBJobStore
    # persists job IDs across restarts, so we explicitly drop the old ID
    # if it still lives in `scheduler_jobs`. Must run AFTER scheduler.start()
    # because MongoDBJobStore lazy-loads job state on start.
    try:
        scheduler.remove_job("forward_test_capture")
        logger.info("[SCHEDULER] Removed legacy forward_test_capture job from store")
    except Exception:
        pass
    logger.info(f"[SCHEDULER] APScheduler started - WEEKEND-READY MODE")
    logger.info(f"[SCHEDULER] === INTERVAL JOBS (High-Performance) ===")
    logger.info(f"[SCHEDULER] Hourly Full Sync: Every 60 min (id: hourly_full_sync)")
    logger.info(f"[SCHEDULER] Hourly Badge Sync: Every 60 min (id: hourly_badge_sync)")
    logger.info(f"[SCHEDULER] Hourly Injury Sync: Every 60 min (id: hourly_injury_sync)")
    logger.info(f"[SCHEDULER] Hourly Referee Sync: Every 60 min (id: hourly_referee_sync)")
    logger.info(f"[SCHEDULER] Live Injury Check: Every 5 min (id: live_injury_check)")
    logger.info(f"[SCHEDULER] Half-Hourly Social: Every 30 min (id: half_hourly_social_sync)")
    logger.info(f"[SCHEDULER] === DAILY CRON JOBS (Sync Data FIRST, Then Pipeline) ===")
    logger.info(f"[SCHEDULER] 4:00-4:08 AM EST - NBA.com L5/L10 Batches (5 batches)")
    logger.info(f"[SCHEDULER] 4:10 AM EST - NBA BDL Game Values | 4:13 AM - MLB")
    logger.info(f"[SCHEDULER] 4:15 AM EST - NBA BDL Game Logs   | 4:18 AM - MLB")
    logger.info(f"[SCHEDULER] 4:20 AM EST - NBA Daily Pipeline  | 4:23 AM - MLB (recalc HR)")
    logger.info(f"[SCHEDULER] 4:26 AM EST - Ticker Sync")
    logger.info(f"[SCHEDULER] Forward-Test Captures: NBA 11/14/17 ET, MLB 12/15/18 ET (6 jobs)")
    logger.info(f"[SCHEDULER] 4:30 AM EST - PRA Dual-Projection Audit Settle")
    logger.info(f"[SCHEDULER] Sunday 00:00 UTC - Weekly Roster Sync")
    
    # AUTO-SYNC: Check if database is empty and trigger initial population
    # This runs only once when deployed to a new environment with empty DB
    asyncio.create_task(check_and_run_initial_sync(db))
    logger.info("[STARTUP] Initial sync check scheduled (runs in background)")

    # ==========================================================================
    # PHASE 1: SYNC ARCHITECTURE V2 — Foundation (Shadow Mode)
    # Runs alongside existing scheduler. Observes events, logs decisions,
    # does NOT dispatch actual rebuilds yet.
    # ==========================================================================
    try:
        from services.event_bus import get_event_bus
        from services.rebuild_coordinator import get_coordinator
        from services.odds_budget_manager import get_budget_manager

        event_bus = get_event_bus()
        coordinator = get_coordinator()
        coordinator.set_db(db)
        coordinator.set_sport_mode("nba", "live")    # Phase 2: NBA live
        coordinator.set_sport_mode("mlb", "live")    # Phase 3: MLB live
        await coordinator.start(event_bus)

        budget_mgr = get_budget_manager()

        # Phase 4: Start watchers and sensors (staged activation)
        from services.watchers import GameClockWatcher, OddsDeltaWatcher
        from services.injury_sources import BDLInjurySource, ESPNInjurySource, NBAOfficialInjurySource
        from services.injury_sensor import InjurySensor

        # Injury Sensor: multi-source detection (replaces old InjuryWatcher)
        # BDL = structural authority, ESPN + NBA Official = timing authorities
        injury_sensor = InjurySensor(
            db=db,
            sources=[BDLInjurySource(), ESPNInjurySource(), NBAOfficialInjurySource()],
            sports=["nba", "mlb"],
        )
        await injury_sensor.start()

        # Targeted injury-triggered re-score (Phase 3).
        # Subscribes to BoardEvent(injury_change) on the central event bus
        # and rescopes `nba_prop_scores` for the affected player set only
        # (rather than the hourly full-slate recompute). NBA-scoped.
        from services.injury_triggered_rescore import get_rescore_service
        get_rescore_service().start(db)

        # Game Clock + Odds Delta watchers
        game_clock_watcher = GameClockWatcher(db)
        odds_delta_watcher = OddsDeltaWatcher(db)

        await game_clock_watcher.start()
        # OddsDeltaWatcher: still in controlled mode
        # await odds_delta_watcher.start()

        # Store for admin endpoints
        app.state.injury_sensor = injury_sensor
        app.state.game_clock_watcher = game_clock_watcher
        app.state.odds_delta_watcher = odds_delta_watcher
        # Legacy compat alias
        app.state.injury_watcher = injury_sensor

        logger.info("[SYNC_V2] Event Bus initialized")
        logger.info(f"[SYNC_V2] Rebuild Coordinator — NBA={coordinator._sport_mode['nba'].upper()}, MLB={coordinator._sport_mode['mlb'].upper()}")
        logger.info("[SYNC_V2] Odds Budget Manager initialized")
        logger.info(f"[SYNC_V2] Daily budget: {budget_mgr.daily_budget:,} calls/day")
        logger.info("[SYNC_V2] Injury Sensor: ACTIVE (BDL + ESPN + NBA Official, dynamic cadence)")
        logger.info("[SYNC_V2] GameClockWatcher: ACTIVE (300s)")
        logger.info("[SYNC_V2] OddsDeltaWatcher: STANDBY (enable via admin)")
        logger.info(f"[SYNC_V2] Daily budget: {budget_mgr.daily_budget:,} calls/day")
    except Exception as e:
        logger.error(f"[SYNC_V2] Foundation init failed (non-fatal): {e}")


async def check_and_run_initial_sync(db):
    """
    Check if the database is empty and run initial sync if needed.
    This ensures newly deployed environments get populated automatically.
    """
    try:
        await asyncio.sleep(5)  # Wait for server to fully start
        
        # Check if master hub is empty
        master_hub_count = await db[COLL("master_hub", "nba")].count_documents({})
        cached_board_count = await db[COLL("board_cache", "nba")].count_documents({})
        
        logger.info(f"[INITIAL_SYNC] Database check: master_hub={master_hub_count}, cached_board={cached_board_count}")
        
        if master_hub_count == 0:
            logger.info("[INITIAL_SYNC] Empty database detected! Starting initial population...")
            
            # Step 1: Sync player roster from BDL
            try:
                from services.bdl_comprehensive_sync import get_bdl_comprehensive_service
                bdl_service = get_bdl_comprehensive_service(db)
                
                logger.info("[INITIAL_SYNC] Step 1/5: Syncing player roster from BallDontLie...")
                result = await bdl_service.sync_active_players()
                logger.info(f"[INITIAL_SYNC] Roster sync complete: {result.get('players_synced', 0)} players")
            except Exception as e:
                logger.error(f"[INITIAL_SYNC] Roster sync failed: {e}")
            
            # Step 2: Sync BDL Game Logs (CRITICAL for hit rate calculations) - BATCHED
            try:
                from services.bdl_game_logs_sync_batched import run_bdl_game_logs_sync_batched
                logger.info("[INITIAL_SYNC] Step 2/5: Syncing BDL game logs BATCHED for hit rates...")
                result = await run_bdl_game_logs_sync_batched(db)
                logger.info(f"[INITIAL_SYNC] BDL game logs sync complete: {result.get('players_synced', 0)} players, {result.get('total_games', 0)} games in {result.get('duration_seconds', 0):.1f}s")
            except Exception as e:
                logger.error(f"[INITIAL_SYNC] BDL game logs sync failed: {e}")
            
            # Step 3: Sync game-by-game values for additional enrichment
            try:
                logger.info("[INITIAL_SYNC] Step 3/5: Syncing game-by-game stats for hit rates...")
                result = await bdl_service.enrich_with_game_values()
                logger.info(f"[INITIAL_SYNC] Game values sync complete: {result.get('enriched', 0)} players enriched")
            except Exception as e:
                logger.error(f"[INITIAL_SYNC] Game values sync failed: {e}")
            
            # Step 4: Sync DvP rankings
            try:
                from services.dvp_service import force_refresh_dvp
                logger.info("[INITIAL_SYNC] Step 4/5: Syncing DvP rankings...")
                result = await force_refresh_dvp()
                logger.info(f"[INITIAL_SYNC] DvP sync complete: {result.get('teams_count', 0)} teams")
            except Exception as e:
                logger.error(f"[INITIAL_SYNC] DvP sync failed: {e}")
            
            # Step 5: Sync context badges
            try:
                from services.context_badge_service import get_context_badge_service
                badge_service = get_context_badge_service(db)
                logger.info("[INITIAL_SYNC] Step 5/5: Populating context badges...")
                result = await badge_service.populate_all_badges()
                logger.info(f"[INITIAL_SYNC] Badge sync complete: {result.get('updated', 0)} players updated")
            except Exception as e:
                logger.error(f"[INITIAL_SYNC] Badge sync failed: {e}")
            
            logger.info("[INITIAL_SYNC] ✅ Initial database population complete!")
        else:
            # Database has data, but check if game logs are stale
            # Game logs should be refreshed if they haven't been updated in 12+ hours
            sample_player = await db[COLL("master_hub", "nba")].find_one(
                {"bdl_game_logs": {"$exists": True, "$ne": []}},
                {"bdl_game_logs_updated_at": 1}
            )
            
            if sample_player:
                last_update = sample_player.get("bdl_game_logs_updated_at")
                if last_update:
                    # Check if update was more than 12 hours ago
                    from datetime import datetime, timezone
                    if isinstance(last_update, str):
                        last_update = datetime.fromisoformat(last_update.replace('Z', '+00:00'))
                    hours_since_update = (datetime.now(timezone.utc) - last_update).total_seconds() / 3600
                    
                    if hours_since_update > 12:
                        logger.info(f"[INITIAL_SYNC] Game logs are {hours_since_update:.1f}h old. Refreshing (BATCHED)...")
                        try:
                            from services.bdl_game_logs_sync_batched import run_bdl_game_logs_sync_batched
                            result = await run_bdl_game_logs_sync_batched(db)
                            logger.info(f"[INITIAL_SYNC] Game logs refresh complete: {result.get('players_synced', 0)} players in {result.get('duration_seconds', 0):.1f}s")
                        except Exception as e:
                            logger.error(f"[INITIAL_SYNC] Game logs refresh failed: {e}")
                    else:
                        logger.info(f"[INITIAL_SYNC] Game logs are fresh ({hours_since_update:.1f}h old). Skipping refresh.")
                else:
                    logger.warning("[INITIAL_SYNC] Game logs have no timestamp. Consider refreshing.")
            else:
                # No game logs at all - sync them (BATCHED)
                logger.info("[INITIAL_SYNC] No game logs found. Syncing (BATCHED)...")
                try:
                    from services.bdl_game_logs_sync_batched import run_bdl_game_logs_sync_batched
                    result = await run_bdl_game_logs_sync_batched(db)
                    logger.info(f"[INITIAL_SYNC] Game logs sync complete: {result.get('players_synced', 0)} players in {result.get('duration_seconds', 0):.1f}s")
                except Exception as e:
                    logger.error(f"[INITIAL_SYNC] Game logs sync failed: {e}")
            
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

    # D5 — cancel delta-engine continuous loops cleanly.
    try:
        from services.scheduled_sports import stop_delta_engine_loops
        stop_summary = await stop_delta_engine_loops()
        logger.info(f"[SHUTDOWN] Delta engine loops stopped: {stop_summary}")
    except Exception as exc:
        logger.error(f"[SHUTDOWN] Delta engine stop failed: {exc}")


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


from fastapi import FastAPI, APIRouter, Depends, HTTPException, status, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
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
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_ANON_KEY = os.environ.get('SUPABASE_ANON_KEY')
SUPABASE_SERVICE_ROLE_KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
JWT_SECRET = os.environ.get('JWT_SECRET')
TANK01_API_KEY = os.environ.get('TANK01_API_KEY')
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
        "description": "Injury intelligence from ESPN and Tank01"
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
    """
    logger.info("=" * 70)
    logger.info(f"[SCHEDULER] 4:00 AM FULL DAILY SYNC TRIGGERED")
    logger.info(f"[SCHEDULER] Time: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 70)
    
    if demon_goblin_engine:
        try:
            # Step 1: Sync injuries (ESPN) - Do first for usage ripple data
            if injury_service:
                logger.info("[SCHEDULER] Step 1/7: Syncing injury data from ESPN...")
                try:
                    injury_result = await injury_service.sync_injuries()
                    logger.info(f"[SCHEDULER] Injuries: {injury_result.get('injuries_synced', 0)} injuries, {injury_result.get('usage_ripple_updates', 0)} ripple updates")
                except Exception as ie:
                    logger.error(f"[SCHEDULER] Injury sync failed (non-critical): {ie}")
            
            # Step 2: BDL Comprehensive Sync - Primary stats source
            # This syncs game logs, season averages, and player profiles from BallDontLie
            # Season averages come DIRECTLY from BDL /season_averages endpoint (OFFICIAL)
            # L5/L10 averages are calculated from game logs
            logger.info("[SCHEDULER] Step 2/6: Running BDL comprehensive sync...")
            try:
                from services.bdl_comprehensive_sync import get_bdl_sync_service
                bdl_service = get_bdl_sync_service(db)
                bdl_result = await bdl_service.sync_prizepicks_players()
                logger.info(f"[SCHEDULER] BDL sync: {bdl_result.get('success', 0)}/{bdl_result.get('total', 0)} players synced from BallDontLie")
            except Exception as bdl_e:
                logger.error(f"[SCHEDULER] BDL sync failed: {bdl_e}")
                # Fallback to legacy stats sync
                logger.info("[SCHEDULER] Falling back to legacy stats sync...")
                stats_result = await demon_goblin_engine.sync_player_stats()
                logger.info(f"[SCHEDULER] Legacy stats sync: {stats_result.get('stats_synced', 0)} players")
            
            # Step 3: Sync DvP and matchup data
            
            # Step 4: Refresh DvP rankings (Defense vs Position)
            logger.info("[SCHEDULER] Step 4/7: Refreshing DvP rankings...")
            try:
                from services.dvp_service import force_refresh_dvp
                dvp_result = await force_refresh_dvp()
                logger.info(f"[SCHEDULER] DvP refresh: {dvp_result.get('source')} - {dvp_result.get('teams_count', 0)} teams, {len(dvp_result.get('stat_types', []))} stat types")
            except Exception as de:
                logger.error(f"[SCHEDULER] DvP refresh failed (non-critical): {de}")
            
            # Step 5: Run full odds sync
            logger.info("[SCHEDULER] Step 5/7: Running full odds sync...")
            result = await demon_goblin_engine.run_full_sync()
            logger.info(f"[SCHEDULER] Sync complete: {result.get('unique_players', 0)} players")
            logger.info(f"[SCHEDULER] Standard: {result.get('standard_count', 0)}, Demons: {result.get('demons_count', 0)}, Goblins: {result.get('goblins_count', 0)}")
            
            # Step 6: Calculate daily insights (advanced analytics)
            logger.info("[SCHEDULER] Step 6/7: Calculating daily insights...")
            insights_result = await demon_goblin_engine.sync_daily_insights()
            logger.info(f"[SCHEDULER] Insights: {insights_result.get('insights_calculated', 0)} players analyzed")
            
            # Step 7: Generate Vision AI insights for eligible players
            if vision_ai_service and os.environ.get('EMERGENT_LLM_KEY'):
                logger.info("[SCHEDULER] Step 7/7: Generating Vision AI insights...")
                try:
                    vision_result = await vision_ai_service.trigger_insights_for_sync()
                    logger.info(f"[SCHEDULER] Vision AI: {vision_result.get('insights_generated', 0)} insights generated")
                except Exception as ve:
                    logger.error(f"[SCHEDULER] Vision AI failed (non-critical): {ve}")
            else:
                logger.info("[SCHEDULER] Step 7/7: Vision AI skipped (not configured)")
            
            logger.info("=" * 70)
            logger.info(f"[SCHEDULER] 4:00 AM FULL SYNC COMPLETE")
            logger.info("=" * 70)
            
        except Exception as e:
            logger.error(f"[SCHEDULER] Daily sync failed: {e}")
    else:
        logger.error("[SCHEDULER] Demon & Goblin Engine not initialized")


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
    logger.info("Injury Intelligence Service initialized (ESPN + Tank01)")
    
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
    from services.dvp_service import set_db_reference as set_dvp_db_reference
    set_dvp_db_reference(db)
    logger.info("DvP Service initialized (MongoDB-backed DvP rankings)")
    
    # Initialize Adaptive Sync Engine - Mission-critical polling
    adaptive_sync = init_adaptive_sync_engine(db, ODDS_API_KEY)
    logger.info("Adaptive Sync Engine initialized (Mission-Critical Polling)")
    
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
        scheduler=scheduler
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
    
    # Weekly Master Roster sync every Sunday at midnight UTC
    scheduler.add_job(
        scheduled_roster_sync,
        CronTrigger(day_of_week='sun', hour=0, minute=0, timezone=SCHEDULER_TIMEZONE),
        id='weekly_roster_sync',
        name='Sunday Midnight Master Roster Sync',
        replace_existing=True
    )
    
    scheduler.start()
    logger.info(f"[SCHEDULER] APScheduler started")
    logger.info(f"[SCHEDULER] Daily Full Sync: 04:00 AM EST (09:00 UTC) - Stats + DvP + Vision Intel")
    logger.info(f"[SCHEDULER] Weekly Roster: Sunday 00:00 UTC")
    
    # DISABLED: Full auto-sync on startup to prevent credit drain
    # The adaptive sync engine handles real-time odds polling
    logger.info("[STARTUP] Full sync DISABLED - Adaptive Sync Engine handles real-time odds")


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
# These are kept here for Tank01 API calls which depend on the global db.
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

async def fetch_tank01_injuries():
    cached = await get_cached_data("tank01_injuries")
    if cached:
        return cached
    
    try:
        async with httpx.AsyncClient() as http_client:
            response = await http_client.get(
                "https://tank01-nba-live-in-game-real-time-statistics-nba.p.rapidapi.com/getNBAInjuryList",
                headers={
                    "X-RapidAPI-Key": TANK01_API_KEY,
                    "X-RapidAPI-Host": "tank01-nba-live-in-game-real-time-statistics-nba.p.rapidapi.com"
                },
                timeout=10.0
            )
            if response.status_code == 200:
                data = response.json().get("body", [])
                await set_cached_data("tank01_injuries", data)
                return data
    except Exception as e:
        logger.error(f"Tank01 injuries error: {e}")
    return []

async def fetch_tank01_teams():
    cached = await get_cached_data("tank01_teams")
    if cached:
        return cached
    
    try:
        async with httpx.AsyncClient() as http_client:
            response = await http_client.get(
                "https://tank01-nba-live-in-game-real-time-statistics-nba.p.rapidapi.com/getNBATeams",
                params={"teamStats": "true"},
                headers={
                    "X-RapidAPI-Key": TANK01_API_KEY,
                    "X-RapidAPI-Host": "tank01-nba-live-in-game-real-time-statistics-nba.p.rapidapi.com"
                },
                timeout=10.0
            )
            if response.status_code == 200:
                data = response.json().get("body", [])
                await set_cached_data("tank01_teams", data)
                return data
    except Exception as e:
        logger.error(f"Tank01 teams error: {e}")
    return []

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


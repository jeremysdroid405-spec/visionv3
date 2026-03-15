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

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

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

app = FastAPI(title="NBA Best Bets API - Demon & Goblin Engine v3.0")
api_router = APIRouter(prefix="/api")
security = HTTPBearer()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
    Scheduled job that runs at 4:00 AM UTC daily.
    
    Execution order:
    1. Sync injuries from ESPN (first for usage ripple calculations)
    2. Sync player stats to MongoDB (from BallDontLie + NBA.com fallback)
    3. Run full odds sync (uses cached stats for fast hit rate calculations)
    4. Calculate daily insights (advanced analytics)
    5. Generate Vision AI insights for Demons/Goblins/High Volatility
    """
    logger.info("=" * 70)
    logger.info(f"[SCHEDULER] 4:00 AM DAILY SYNC TRIGGERED")
    logger.info(f"[SCHEDULER] Time: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 70)
    
    if demon_goblin_engine:
        try:
            # Step 1: Sync injuries (ESPN) - Do first for usage ripple data
            if injury_service:
                logger.info("[SCHEDULER] Step 1/5: Syncing injury data from ESPN...")
                try:
                    injury_result = await injury_service.sync_injuries()
                    logger.info(f"[SCHEDULER] Injuries: {injury_result.get('injuries_synced', 0)} injuries, {injury_result.get('usage_ripple_updates', 0)} ripple updates")
                except Exception as ie:
                    logger.error(f"[SCHEDULER] Injury sync failed (non-critical): {ie}")
            
            # Step 2: Sync player stats to cache
            logger.info("[SCHEDULER] Step 2/5: Syncing player stats to cache...")
            stats_result = await demon_goblin_engine.sync_player_stats()
            logger.info(f"[SCHEDULER] Stats sync: {stats_result.get('stats_synced', 0)} players (BDL: {stats_result.get('from_balldontlie', 0)}, NBA: {stats_result.get('from_nba_api', 0)})")
            
            # Step 3: Run full odds sync
            logger.info("[SCHEDULER] Step 3/5: Running full odds sync...")
            result = await demon_goblin_engine.run_full_sync()
            logger.info(f"[SCHEDULER] Sync complete: {result.get('unique_players', 0)} players")
            logger.info(f"[SCHEDULER] Standard: {result.get('standard_count', 0)}, Demons: {result.get('demons_count', 0)}, Goblins: {result.get('goblins_count', 0)}")
            
            # Step 4: Calculate daily insights (advanced analytics)
            logger.info("[SCHEDULER] Step 4/5: Calculating daily insights...")
            insights_result = await demon_goblin_engine.sync_daily_insights()
            logger.info(f"[SCHEDULER] Insights: {insights_result.get('insights_calculated', 0)} players analyzed")
            
            # Step 5: Generate Vision AI insights for eligible players
            if vision_ai_service and os.environ.get('EMERGENT_LLM_KEY'):
                logger.info("[SCHEDULER] Step 5/5: Generating Vision AI insights...")
                try:
                    vision_result = await vision_ai_service.trigger_insights_for_sync()
                    logger.info(f"[SCHEDULER] Vision AI: {vision_result.get('insights_generated', 0)} insights generated")
                except Exception as ve:
                    logger.error(f"[SCHEDULER] Vision AI failed (non-critical): {ve}")
            else:
                logger.info("[SCHEDULER] Step 5/5: Vision AI skipped (not configured)")
            
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
    logger.info("[ROUTES] Modular routes registered from /routes/ directory (Phase 17: +3 new modules)")
    
    # Include the remaining api_router routes that are still in server.py
    app.include_router(api_router)
    logger.info("[ROUTES] Core api_router routes included")
    
    # Start the adaptive sync engine (background polling)
    if ODDS_API_KEY:
        await adaptive_sync.start()
        logger.info("[ADAPTIVE_SYNC] Background polling STARTED")
    else:
        logger.warning("[ADAPTIVE_SYNC] No Odds API key - adaptive sync disabled")
    
    # Daily sync at 4:00 AM EST (9:00 AM UTC) for static stats
    # Note: 04:00 EST = 09:00 UTC during standard time
    scheduler.add_job(
        scheduled_daily_sync,
        CronTrigger(hour=9, minute=0, timezone=SCHEDULER_TIMEZONE),  # 4:00 AM EST = 9:00 AM UTC
        id='daily_sync',
        name='4:00 AM EST Daily Stats Sync',
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
    logger.info(f"[SCHEDULER] APScheduler started - Daily stats sync at 04:00 EST (09:00 UTC)")
    logger.info(f"[SCHEDULER] Weekly roster sync scheduled: Sunday 00:00 UTC")
    
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

@api_router.get("/full-board")
async def get_full_board(market: str = "full"):
    try:
        import random
        
        injuries = await fetch_tank01_injuries()
        teams = await fetch_tank01_teams()
        
        mock_games = [
            {"home": "LAL", "away": "GSW"},
            {"home": "MIL", "away": "BKN"},
            {"home": "DAL", "away": "DEN"},
            {"home": "PHX", "away": "BOS"},
            {"home": "PHI", "away": "MIA"},
        ]
        
        mock_players_by_team = {
            "LAL": ["LeBron James", "Anthony Davis", "Austin Reaves"],
            "GSW": ["Stephen Curry", "Klay Thompson", "Draymond Green"],
            "MIL": ["Giannis Antetokounmpo", "Damian Lillard", "Brook Lopez"],
            "BKN": ["Mikal Bridges", "Nic Claxton", "Cameron Thomas"],
            "DAL": ["Luka Doncic", "Kyrie Irving", "Daniel Gafford"],
            "DEN": ["Nikola Jokic", "Jamal Murray", "Michael Porter Jr"],
            "PHX": ["Kevin Durant", "Devin Booker", "Bradley Beal"],
            "BOS": ["Jayson Tatum", "Jaylen Brown", "Kristaps Porzingis"],
            "PHI": ["Joel Embiid", "Tyrese Maxey", "Tobias Harris"],
            "MIA": ["Jimmy Butler", "Bam Adebayo", "Tyler Herro"],
        }
        
        prop_types = ["points", "rebounds", "assists"] if market == "full" else [f"{market}_points", f"{market}_assists", f"{market}_rebounds"]
        sources = ["PrizePicks", "DraftKings", "FanDuel", "BetMGM", "Caesars"]
        
        all_props = []
        
        for game in mock_games:
            for team in [game["home"], game["away"]]:
                opponent = game["away"] if team == game["home"] else game["home"]
                players = mock_players_by_team.get(team, [])
                
                for player in players:
                    for prop_type in prop_types:
                        base_line = random.uniform(15, 35) if "points" in prop_type else random.uniform(5, 12)
                        
                        pp_line = round(base_line, 1)
                        dk_line = round(base_line + random.uniform(-2, 2), 1)
                        fd_line = round(base_line + random.uniform(-2, 2), 1)
                        mgm_line = round(base_line + random.uniform(-2, 2), 1)
                        cae_line = round(base_line + random.uniform(-2, 2), 1)
                        
                        market_avg = round((dk_line + fd_line + mgm_line + cae_line) / 4, 1)
                        market_edge = round(market_avg - pp_line, 1)
                        
                        is_demon = random.random() > 0.85
                        is_goblin = random.random() > 0.9
                        hit_rate = random.uniform(0.35, 0.65) if is_demon else None
                        
                        best_bet_score = abs(market_edge) * 10 + (hit_rate * 20 if hit_rate else 0)
                        matchup_grade = random.choice(["A+", "A", "B+", "B", "C"])
                        confidence = min(95, max(50, best_bet_score + random.uniform(-5, 5)))
                        
                        for source in sources:
                            if source == "PrizePicks":
                                line = pp_line
                            elif source == "DraftKings":
                                line = dk_line
                            elif source == "FanDuel":
                                line = fd_line
                            elif source == "BetMGM":
                                line = mgm_line
                            else:
                                line = cae_line
                            
                            all_props.append(PlayerPropFull(
                                player_name=player,
                                team=team,
                                opponent=opponent,
                                prop_type=prop_type,
                                market=market,
                                source=source,
                                line=line,
                                prizepicks_line=pp_line if source != "PrizePicks" else None,
                                market_avg=market_avg,
                                market_edge=market_edge,
                                draftkings_line=dk_line,
                                fanduel_line=fd_line,
                                betmgm_line=mgm_line,
                                caesars_line=cae_line,
                                is_demon=is_demon and source == "PrizePicks",
                                demon_line=round(pp_line + random.uniform(4, 8), 1) if is_demon and source == "PrizePicks" else None,
                                is_goblin=is_goblin and source == "PrizePicks",
                                goblin_line=round(pp_line - random.uniform(2, 4), 1) if is_goblin and source == "PrizePicks" else None,
                                hit_rate=round(hit_rate, 2) if hit_rate and source == "PrizePicks" else None,
                                best_bet_score=round(best_bet_score, 1),
                                matchup_grade=matchup_grade,
                                confidence=round(confidence, 1),
                                def_rank=random.randint(1, 30)
                            ))
        
        all_props.sort(key=lambda x: x.best_bet_score, reverse=True)
        return {"success": True, "data": all_props, "total": len(all_props)}
    except Exception as e:
        logger.error(f"Error generating full board: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/calculate-hit-rate")
async def calculate_hit_rate_endpoint(
    player_name: str,
    prop_type: str,
    line: float
):
    """Calculate real hit rate from API-Sports L10 data"""
    if not stats_manager:
        raise HTTPException(status_code=500, detail="Stats manager not initialized")
    
    result = await stats_manager.calculate_hit_rate(player_name, prop_type, line)
    
    if not result:
        raise HTTPException(
            status_code=404,
            detail=f"Could not calculate hit rate for {player_name}"
        )
    
    return {"success": True, "data": result}

@api_router.get("/validate-demon")
async def validate_demon_endpoint(
    player_name: str,
    prop_type: str,
    demon_line: float
):
    """Validate if a demon line qualifies based on real data"""
    if not stats_manager:
        raise HTTPException(status_code=500, detail="Stats manager not initialized")
    
    is_valid = await stats_manager.validate_demon_line(player_name, prop_type, demon_line)
    
    hit_rate_data = await stats_manager.calculate_hit_rate(player_name, prop_type, demon_line)
    
    return {
        "success": True,
        "is_valid_demon": is_valid,
        "hit_rate_data": hit_rate_data
    }

# NOTE: Admin routes (cache-status, sync-rosters, etc.) moved to routes/admin.py (Phase 17)


@api_router.get("/")
async def root():
    return {"message": "NBA Best Bets API - Demon Tracker v2"}

# NOTE: DEMON_TRACKER routes moved to routes/ directory

# ==================== DEMON & GOBLIN ENGINE v3.0 ENDPOINTS ====================

@api_router.get("/v3/status")
async def get_dg_status():
    """Get Demon & Goblin Engine sync status"""
    if not demon_goblin_engine:
        raise HTTPException(status_code=500, detail="Demon & Goblin Engine not initialized")
    
    status = await demon_goblin_engine.get_sync_status()
    return {"success": True, "data": status}

@api_router.post("/v3/sync")
async def trigger_dg_sync():
    """Trigger full Demon & Goblin sync"""
    if not demon_goblin_engine:
        raise HTTPException(status_code=500, detail="Demon & Goblin Engine not initialized")
    
    result = await demon_goblin_engine.run_full_sync()
    return {"success": True, "result": result}

@api_router.get("/v3/players")
async def get_all_players_v3():
    """
    Get all players (collapsed view)
    Returns: player_name, team, injury_status, demon_count, goblin_count
    """
    if not demon_goblin_engine:
        raise HTTPException(status_code=500, detail="Demon & Goblin Engine not initialized")
    
    players = await demon_goblin_engine.get_all_players()
    
    # Format for collapsed view
    collapsed = []
    for p in players:
        collapsed.append({
            "player_name": p.get("player_name"),
            "team": p.get("team", ""),
            "position": p.get("position", ""),
            "injury_status": p.get("injury_info", {}).get("injury_status"),
            "injury_warning": p.get("injury_info", {}).get("warning_level", "none"),
            "demons_count": len(p.get("demons", [])),
            "goblins_count": len(p.get("goblins", [])),
            "total_props": len(p.get("props", [])),
            "has_goblin_warning": p.get("has_goblin_warning", False)
        })
    
    return {
        "success": True,
        "count": len(collapsed),
        "players": collapsed
    }

@api_router.get("/v3/player/{player_name}")
async def get_player_detail_v3(player_name: str):
    """
    Get full player detail (expanded view)
    Returns: All props with Demons and Goblins sorted to top
    """
    if not demon_goblin_engine:
        raise HTTPException(status_code=500, detail="Demon & Goblin Engine not initialized")
    
    player = await demon_goblin_engine.get_player_detail(player_name)
    
    if not player:
        raise HTTPException(status_code=404, detail=f"Player {player_name} not found")
    
    return {"success": True, "player": player}

@api_router.get("/v3/demons")
async def get_all_demons_v3():
    """
    Get all Demon lines (odds >= +200)
    Sorted by highest odds first
    """
    if not demon_goblin_engine:
        raise HTTPException(status_code=500, detail="Demon & Goblin Engine not initialized")
    
    demons = await demon_goblin_engine.get_all_demons()
    return {
        "success": True,
        "count": len(demons),
        "description": "Demons are alternate lines with odds >= +200 (harder, high-payout)",
        "demons": demons
    }

@api_router.get("/v3/goblins")
async def get_all_goblins_v3():
    """
    Get all Goblin lines (odds <= -300)
    Sorted by highest hit rate first
    """
    if not demon_goblin_engine:
        raise HTTPException(status_code=500, detail="Demon & Goblin Engine not initialized")
    
    goblins = await demon_goblin_engine.get_all_goblins()
    
    # Count warnings
    warnings_count = sum(1 for g in goblins if g.get("has_warning"))
    
    return {
        "success": True,
        "count": len(goblins),
        "warnings": warnings_count,
        "description": "Goblins are alternate lines with odds <= -300 (easier, high-probability)",
        "goblins": goblins
    }

@api_router.get("/v3/search")
async def search_players_v3(q: str = Query(..., description="Player name to search")):
    """Search for players by name"""
    if not demon_goblin_engine:
        raise HTTPException(status_code=500, detail="Demon & Goblin Engine not initialized")
    
    players = await demon_goblin_engine.search_players(q)
    return {
        "success": True,
        "query": q,
        "count": len(players),
        "players": players
    }

@api_router.get("/v3/board")
async def get_dg_board():
    """
    Get the full Demon & Goblin Board
    Hierarchical view organized by player
    """
    if not demon_goblin_engine:
        raise HTTPException(status_code=500, detail="Demon & Goblin Engine not initialized")
    
    players = await demon_goblin_engine.get_all_players()
    status = await demon_goblin_engine.get_sync_status()
    
    return {
        "success": True,
        "sync_date": status.get("sync_date"),
        "last_sync": status.get("last_sync"),
        "unique_players": len(players),
        "total_props": status.get("total_props", 0),
        "demons_count": status.get("demons_count", 0),
        "goblins_count": status.get("goblins_count", 0),
        "players": players
    }

@api_router.get("/v3/trending")
async def get_trending_10():
    """
    Get the Top 10 Most Popular players today (Trending 10)
    Based on PrizePicks board order and Demon/Goblin count
    
    Returns players with:
    - Popularity score
    - Top 3 props with hit rates
    - Injury status (with NEW INJURY tag if applicable)
    """
    if not demon_goblin_engine:
        raise HTTPException(status_code=500, detail="Demon & Goblin Engine not initialized")
    
    trending = await demon_goblin_engine.get_trending_10()
    
    return {
        "success": True,
        "description": "Most Popular Today - Top 10 trending players on PrizePicks",
        "count": len(trending),
        "trending": trending
    }


@api_router.get("/v3/most-popular-bets")
async def get_most_popular_bets():
    """
    LIVE TICKER - Top 20 Most Popular BETS (specific props, not players)
    
    Returns actual bet lines sorted by popularity/ticket volume proxy.
    Includes ALL line types: Standard, Demon, and Goblin.
    Auto-purges bets from games that have already started.
    
    Frontend should poll this every 30-60 seconds for live updates.
    
    Returns:
    - bets: Array of top 20 most popular bets with:
        - player_name, team, photo_url
        - stat_type, line, direction (over/under)
        - line_type: "standard" | "demon" | "goblin"
        - h10_rate, h5_rate, gap_pct
        - popularity_score (proxy for ticket volume)
        - commence_time, home_team, away_team
    - last_updated: ISO timestamp for cache freshness
    """
    if not demon_goblin_engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    result = await demon_goblin_engine.get_most_popular_bets()
    return result


# ==================== HYBRID CACHING ENDPOINTS ====================

# NOTE: Cached data routes (static-shell, live-lines, cached-props) moved to routes/cached_data.py (Phase 17)


@api_router.post("/v3/sync-to-mongo")
async def sync_to_mongo():
    """
    THE ONLY API CALL ENDPOINT - Single batch sync.
    
    This is the ONLY place where Odds API is called.
    Fetches all data and stores in MongoDB.
    Also calculates and stores Demon Radar top 10.
    After sync, triggers Intel Briefing generation for new entries.
    
    Use this endpoint manually or via scheduler.
    Frontend NEVER calls this.
    """
    if not demon_goblin_engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    logger.info("[MANUAL SYNC] sync_to_mongo triggered")
    
    result = await demon_goblin_engine.sync_odds_to_mongo()
    
    # After sync, generate intel briefings for new entries
    if intel_briefing_engine and os.environ.get('GOOGLE_API_KEY'):
        try:
            logger.info("[INTEL BRIEFING] Generating intel for new entries...")
            intel_result = await intel_briefing_engine.check_and_generate_for_board()
            result["intel_briefings"] = intel_result
        except Exception as e:
            logger.error(f"[INTEL BRIEFING] Post-sync generation failed: {e}")
            result["intel_briefings"] = {"error": str(e)}
    
    return result


@api_router.post("/v3/generate-intel-briefings")
async def generate_intel_briefings():
    """
    TARGETED STRATEGIC VISION - Generate bet-specific 2-sentence theses.
    
    Conditional Trigger: Only generates for:
    - is_demon = True (Radar picks)
    - is_goblin = True (Vault picks)
    - in_parlay_maker = True
    
    Output Format (2 Sentences):
    1. The Matchup Exploit - Why opponent's defense allows this stat
    2. The Math Leverage - Why the line is soft
    
    Example: "Cleveland is missing Jarrett Allen in the paint, leaving their 
    interior defense vulnerable to Luka's elite driving gravity. With Kyrie 
    Irving sidelined, Luka's projected usage rate jumps to 38%, making this 
    24.5 point line an easy exploitation."
    
    Returns:
    - generated: Number of Strategic Theses created
    - processed_players: List of players with new theses
    - errors: Number of failed generations
    """
    if not intel_briefing_engine:
        raise HTTPException(status_code=500, detail="Strategic Vision Engine not initialized")
    
    if not os.environ.get('GOOGLE_API_KEY'):
        raise HTTPException(status_code=500, detail="GOOGLE_API_KEY not configured")
    
    logger.info("[VISION] Targeted Strategic Thesis generation triggered")
    result = await intel_briefing_engine.generate_for_targeted_picks()
    
    return result


@api_router.get("/v3/intel-briefing/{player_name}")
async def get_player_intel_briefing(player_name: str, game_id: Optional[str] = None):
    """
    Get the Intel Briefing for a specific player.
    
    Returns the cached intel_briefing text or placeholder if not generated yet.
    
    Args:
    - player_name: Player's name
    - game_id: Optional specific game ID
    
    Returns:
    - intel_briefing: The tactical report text
    - status: "ready" | "pending" | "unavailable"
    """
    if not intel_briefing_engine:
        return {
            "player_name": player_name,
            "intel_briefing": "Analyzing Sector Data...",
            "status": "unavailable"
        }
    
    intel = await intel_briefing_engine.get_intel_for_player(player_name, game_id)
    
    if intel:
        return {
            "player_name": player_name,
            "intel_briefing": intel,
            "status": "ready"
        }
    else:
        return {
            "player_name": player_name,
            "intel_briefing": "Analyzing Sector Data...",
            "status": "pending"
        }


# ==================== WAR ZONE ENDPOINT ====================

@api_router.get("/v3/war-zone")
async def get_war_zone():
    """
    THE WAR ZONE - Top 10 picks based on mathematical analysis.
    
    Algorithm:
    1. Hit Probability (P) = (H10 × 0.6) + (H5 × 0.4)
    2. Line Gap (G) = (Demon_Value - Standard_Value) / Standard_Value
    3. Final Score = P - (G × 100)
    
    Logic Guard: Only includes picks with P >= 60%
    
    NO API CALLS - reads from pre-calculated MongoDB data.
    """
    if not demon_goblin_engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    result = await demon_goblin_engine.get_war_zone()
    
    # Add lock status to picks by checking player's game commence_time
    if game_lock_engine and result.get("picks"):
        locked_games = await game_lock_engine.get_locked_games()
        locked_event_ids = {g.get("event_id") for g in locked_games}
        
        # Get commence_time for each player from cached_board
        for pick in result["picks"]:
            player_name = pick.get("player_name")
            # Look up the player in cached_board to get their event_id/commence_time
            board_entry = await db.dg_cached_board.find_one(
                {"player_name": player_name},
                {"_id": 0, "props": 1}
            )
            if board_entry and board_entry.get("props"):
                first_prop = board_entry["props"][0]
                pick["event_id"] = first_prop.get("event_id")
                pick["commence_time"] = first_prop.get("commence_time")
                pick["home_team"] = first_prop.get("home_team")
                pick["away_team"] = first_prop.get("away_team")
                
                # Check if locked
                if first_prop.get("event_id") in locked_event_ids:
                    pick["locked"] = True
    
    return result


@api_router.get("/v3/goblin-vault")
async def get_goblin_vault():
    """
    THE GOBLIN VAULT - Top 10 safest plays based on hit rate analysis.
    
    Algorithm:
    1. Hit Rate Score (80% weight) = (L10 × 0.6) + (L5 × 0.4)
    2. Value Gap Score (20% weight) = Distance below standard line
    3. Final Score = (Hit_Rate × 0.8) + (Value_Gap_Bonus × 0.2)
    
    Target: 90%+ hit rate lines for maximum safety.
    
    NO API CALLS - reads from pre-calculated MongoDB data.
    """
    if not demon_goblin_engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    result = await demon_goblin_engine.get_goblin_vault()
    
    # Add lock status to picks by checking player's game commence_time
    if game_lock_engine and result.get("picks"):
        locked_games = await game_lock_engine.get_locked_games()
        locked_event_ids = {g.get("event_id") for g in locked_games}
        
        for pick in result["picks"]:
            player_name = pick.get("player_name")
            board_entry = await db.dg_cached_board.find_one(
                {"player_name": player_name},
                {"_id": 0, "props": 1}
            )
            if board_entry and board_entry.get("props"):
                first_prop = board_entry["props"][0]
                pick["event_id"] = first_prop.get("event_id")
                pick["commence_time"] = first_prop.get("commence_time")
                pick["home_team"] = first_prop.get("home_team")
                pick["away_team"] = first_prop.get("away_team")
                
                if first_prop.get("event_id") in locked_event_ids:
                    pick["locked"] = True
    
    return result


# ==================== FRONT LINES ENDPOINT ====================

@api_router.get("/v3/front-lines")
async def get_front_lines():
    """
    THE FRONT LINES - Top 6 middle-tier standard props.
    
    Algorithm:
    1. Hit Probability (P) = (H10 × 0.6) + (H5 × 0.4)
    2. Target Range: 60-90% hit rate (sweet spot between demons and goblins)
    3. Bullet Rating: • to •••••• based on reliability
    
    NO API CALLS - reads from pre-calculated MongoDB data.
    """
    if not demon_goblin_engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    result = await demon_goblin_engine.get_front_lines()
    
    # Attach intel briefings and lock status
    if result.get("picks"):
        locked_games = []
        locked_event_ids = set()
        if game_lock_engine:
            locked_games = await game_lock_engine.get_locked_games()
            locked_event_ids = {g.get("event_id") for g in locked_games}
        
        for pick in result["picks"]:
            player_name = pick.get("player_name")
            board_entry = await db.dg_cached_board.find_one(
                {"player_name": player_name},
                {"_id": 0, "props": 1}
            )
            if board_entry and board_entry.get("props"):
                first_prop = board_entry["props"][0]
                pick["event_id"] = first_prop.get("event_id")
                pick["commence_time"] = first_prop.get("commence_time")
                pick["home_team"] = first_prop.get("home_team")
                pick["away_team"] = first_prop.get("away_team")
                
                if first_prop.get("event_id") in locked_event_ids:
                    pick["locked"] = True
    
    return result


@api_router.get("/v3/parlay-builder")
async def get_parlay_builder():
    """
    THE BIG MONEY BUILDER - Parlay Generator
    
    Returns 5 parlay types (2-pick to 6-pick) with:
    - Player picks with demon stats
    - Estimated payout multipliers
    - Combined probability calculations
    - Game correlation info
    - Strategic Vision intel for each pick
    
    NO API CALLS - reads from pre-calculated MongoDB data.
    """
    if not demon_goblin_engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    result = await demon_goblin_engine.get_parlay_builder()
    
    # Attach intel briefings and lock status to all parlay picks
    if result.get("parlays"):
        locked_games = []
        locked_event_ids = set()
        if game_lock_engine:
            locked_games = await game_lock_engine.get_locked_games()
            locked_event_ids = {g.get("event_id") for g in locked_games}
        
        for parlay_type, parlay_data in result["parlays"].items():
            picks = parlay_data.get("picks", [])
            for pick in picks:
                player_name = pick.get("player_name")
                if player_name:
                    # Get intel and game info from cached_board
                    board_entry = await db.dg_cached_board.find_one(
                        {"player_name": player_name},
                        {"_id": 0, "intel_briefing": 1, "props": 1}
                    )
                    if board_entry:
                        pick["intel_briefing"] = board_entry.get("intel_briefing", "")
                        props = board_entry.get("props", [])
                        if props:
                            first_prop = props[0]
                            pick["event_id"] = first_prop.get("event_id")
                            pick["commence_time"] = first_prop.get("commence_time")
                            if first_prop.get("event_id") in locked_event_ids:
                                pick["locked"] = True
    
    return result


@api_router.get("/v3/goblin-recon")
async def get_goblin_recon():
    """
    THE GOBLIN RECON - High-Consistency Parlay Generator
    
    Returns Goblin-only parlays optimized for maximum win probability:
    - Daily Double (2-Pick): ~90%+ combined probability
    - Green Ladder (3 & 4-Pick): Diversified across games
    - 6-Pick Fortress (Flex): Designed for PrizePicks Flex play
    
    Uses Floor Scoring Algorithm:
    - 88%+ weighted hit rate threshold
    - Recon Lock = player's floor >= line
    - Blowout protection
    - Strategic Vision intel for each pick
    
    NO API CALLS - reads from pre-calculated MongoDB data.
    """
    if not demon_goblin_engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    result = await demon_goblin_engine.get_goblin_recon()
    
    # Add intel briefings and lock status to picks
    locked_games = []
    locked_event_ids = set()
    if game_lock_engine:
        locked_games = await game_lock_engine.get_locked_games()
        locked_event_ids = {g.get("event_id") for g in locked_games}
    
    if result.get("picks"):
        for pick in result["picks"]:
            player_name = pick.get("player_name")
            board_entry = await db.dg_cached_board.find_one(
                {"player_name": player_name},
                {"_id": 0, "props": 1, "intel_briefing": 1}
            )
            if board_entry:
                # Add intel briefing
                pick["intel_briefing"] = board_entry.get("intel_briefing", "")
                
                # Add lock status
                if board_entry.get("props"):
                    first_prop = board_entry["props"][0]
                    pick["event_id"] = first_prop.get("event_id")
                    pick["commence_time"] = first_prop.get("commence_time")
                    pick["home_team"] = first_prop.get("home_team")
                    pick["away_team"] = first_prop.get("away_team")
                    
                    if first_prop.get("event_id") in locked_event_ids:
                        pick["locked"] = True
    
    # Also process parlays if present
    if result.get("parlays"):
        for parlay_type, parlay_data in result["parlays"].items():
            parlay_picks = parlay_data.get("picks", [])
            for pick in parlay_picks:
                player_name = pick.get("player_name")
                if player_name:
                    board_entry = await db.dg_cached_board.find_one(
                        {"player_name": player_name},
                        {"_id": 0, "intel_briefing": 1, "props": 1}
                    )
                    if board_entry:
                        pick["intel_briefing"] = board_entry.get("intel_briefing", "")
                        props = board_entry.get("props", [])
                        if props:
                            first_prop = props[0]
                            pick["event_id"] = first_prop.get("event_id")
                            pick["commence_time"] = first_prop.get("commence_time")
                            if first_prop.get("event_id") in locked_event_ids:
                                pick["locked"] = True
    
    return result



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


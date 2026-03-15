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
        demon_goblin_engine_class=DemonGoblinEngine
    )
    logger.info("[ROUTES] Modular routes registered from /routes/ directory (Phase 15: +5 new modules)")
    
    # Start the adaptive sync engine (background polling)
    if ODDS_API_KEY:
        await adaptive_sync.start()
        logger.info("[ADAPTIVE_SYNC] Background polling STARTED")
    else:
        logger.warning("[ADAPTIVE_SYNC] No Odds API key - adaptive sync disabled")
    
    # Initialize APScheduler for daily and weekly syncs
    scheduler = AsyncIOScheduler(timezone=SCHEDULER_TIMEZONE)
    
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

# NOTE: Cache utilities below are duplicated in utils.py for modular use.
# These local versions use the global `db` reference directly.
async def get_cached_data(cache_key: str, ttl_hours: int = 24):
    cached = await db.cache.find_one({"key": cache_key})
    if cached:
        cached_time = datetime.fromisoformat(cached["cached_at"])
        if datetime.now(timezone.utc) - cached_time < timedelta(hours=ttl_hours):
            return cached.get("data")
    return None

async def set_cached_data(cache_key: str, data: Any):
    await db.cache.update_one(
        {"key": cache_key},
        {"$set": {"key": cache_key, "data": data, "cached_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True
    )

# NOTE: fuzzy_match_player also exists in utils.py
def fuzzy_match_player(name1: str, name2: str, threshold: int = 80) -> bool:
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

@api_router.get("/cache-status")
async def get_cache_status():
    """Get cache statistics"""
    if not stats_manager:
        raise HTTPException(status_code=500, detail="Stats manager not initialized")
    
    status = await stats_manager.get_cache_status()
    return {"success": True, "data": status}

@api_router.post("/clear-expired-cache")
async def clear_expired_cache():
    """Clear expired cache entries"""
    if not stats_manager:
        raise HTTPException(status_code=500, detail="Stats manager not initialized")
    
    deleted_count = await stats_manager.clear_expired_cache()
    return {"success": True, "deleted_count": deleted_count}

@api_router.post("/sync-rosters")
async def sync_rosters(force: bool = False):
    """
    Sync NBA rosters for all 30 teams
    This creates a global player database for fast lookups
    """
    if not stats_manager:
        raise HTTPException(status_code=500, detail="Stats manager not initialized")
    
    result = await stats_manager.sync_nba_rosters(force=force)
    return {"success": True, "sync_result": result}

@api_router.post("/clear-all-cache")
async def clear_all_cache():
    """Clear ALL cache (use when changing seasons)"""
    if not stats_manager:
        raise HTTPException(status_code=500, detail="Stats manager not initialized")
    
    deleted_count = await stats_manager.clear_all_cache()
    return {"success": True, "deleted_count": deleted_count, "reason": "Season change - cleared all 2024 data"}

@api_router.get("/todays-games")
async def get_todays_games():
    """Get today's NBA games from BallDontLie"""
    if not stats_manager:
        raise HTTPException(status_code=500, detail="Stats manager not initialized")
    
    result = await stats_manager.get_todays_games_summary()
    return result

@api_router.post("/trigger-daily-sync")
async def trigger_daily_sync():
    """Manually trigger the autonomous daily sync"""
    if not stats_manager:
        raise HTTPException(status_code=500, detail="Stats manager not initialized")
    
    result = await stats_manager.autonomous_daily_sync()
    return {"success": True, "sync_result": result}

@api_router.post("/sync-lakers-test")
async def sync_lakers_test():
    """
    Test Lakers roster sync for season 2025 using BallDontLie
    """
    if not stats_manager:
        raise HTTPException(status_code=500, detail="Stats manager not initialized")
    
    logger.info("🏀 Testing Lakers roster sync for season 2025 (BallDontLie)...")
    
    # Lakers team ID in BallDontLie is 14
    player_ids = await stats_manager.sync_players_for_team(14)
    
    return {
        "success": True,
        "message": "Lakers roster synced successfully via BallDontLie",
        "players_synced": len(player_ids),
        "data_source": "BallDontLie API"
    }

@api_router.get("/rate-limit-status")
async def get_rate_limit_status():
    """Get current API rate limit status"""
    if not stats_manager:
        raise HTTPException(status_code=500, detail="Stats manager not initialized")
    
    status = stats_manager.rate_limit.get_status()
    return {"success": True, "rate_limit": status}

@api_router.get("/roster-status")
async def get_roster_status():
    """Get roster sync status and statistics"""
    if not stats_manager:
        raise HTTPException(status_code=500, detail="Stats manager not initialized")
    
    try:
        total_players = await stats_manager.league_roster.count_documents({})
        
        # Get teams count
        teams = await stats_manager.league_roster.distinct("team_name")
        
        # Get last sync time
        latest = await stats_manager.league_roster.find_one(
            {},
            sort=[("synced_at", -1)]
        )
        
        last_synced = latest.get("synced_at") if latest else None
        
        return {
            "success": True,
            "total_players": total_players,
            "total_teams": len(teams),
            "teams": sorted(teams),
            "last_synced": last_synced,
            "season": CURRENT_SEASON
        }
    except Exception as e:
        logger.error(f"Roster status error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

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

@api_router.get("/v3/static-shell")
async def get_static_shell():
    """
    Get STATIC SHELL data (24h TTL)
    Contains: Player metadata, teams, positions, historical stats
    Does NOT contain: Live betting lines
    
    Use this for initial page load - instant render of player cards
    """
    if not demon_goblin_engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    shell = await demon_goblin_engine.get_static_shell()
    
    return {
        "success": True,
        "cache_hit": shell.get("cache_hit", False),
        "cache_age_seconds": shell.get("cache_age_seconds", 0),
        "sync_date": shell.get("sync_date"),
        "players_count": len(shell.get("players", [])),
        "players": shell.get("players", []),
        "trending": shell.get("trending", [])
    }

@api_router.get("/v3/live-lines")
async def get_live_lines():
    """
    Get DYNAMIC PULSE data (60s TTL)
    Contains ONLY: Live betting lines (price, point, demon/goblin tags)
    
    Use this to hydrate cards with live data after initial render
    Lightweight endpoint - minimal payload
    """
    if not demon_goblin_engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    lines = await demon_goblin_engine.get_live_lines()
    
    # Count totals
    total_lines = sum(len(v) for v in lines.get("lines", {}).values())
    total_demons = sum(
        sum(1 for line in player_lines if line.get("is_demon"))
        for player_lines in lines.get("lines", {}).values()
    )
    total_goblins = sum(
        sum(1 for line in player_lines if line.get("is_goblin"))
        for player_lines in lines.get("lines", {}).values()
    )
    
    return {
        "success": True,
        "cache_hit": lines.get("cache_hit", False),
        "cache_age_seconds": lines.get("cache_age_seconds", 0),
        "last_update": lines.get("last_update"),
        "total_lines": total_lines,
        "total_demons": total_demons,
        "total_goblins": total_goblins,
        "players_count": len(lines.get("lines", {})),
        "lines": lines.get("lines", {})
    }

@api_router.get("/v3/hydrated-board")
async def get_hydrated_board():
    """
    DEPRECATED - Use /api/v3/cached-props instead.
    Redirects to cached board for backward compatibility.
    """
    return await get_cached_props()


# ==================== WAREHOUSE MODEL ENDPOINTS (ZERO API CALLS) ====================

@api_router.get("/v3/cached-props")
async def get_cached_props(include_locked: bool = True):
    """
    THE PRIMARY ENDPOINT - Reads ONLY from MongoDB.
    NO Odds API calls. Zero credit usage.
    
    Returns the full cached board with:
    - All players grouped by props (with locked status marked)
    - Trending 10
    - synced_at timestamp
    - t_minus_games: Games starting in <15 minutes (for countdown timers)
    - lock_status: Overview of active/locked games
    
    Query params:
    - include_locked: If true (default), includes locked players with locked=true flag
    """
    if not demon_goblin_engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    board = await demon_goblin_engine.get_cached_board()
    
    if game_lock_engine:
        # Get list of locked event IDs
        locked_games = await game_lock_engine.get_locked_games()
        locked_event_ids = {g.get("event_id") for g in locked_games}
        
        # Mark players as locked if their event is locked
        if board.get("players"):
            for player in board["players"]:
                # Check if any of the player's props are from locked events
                player_props = player.get("props", [])
                for prop in player_props:
                    if prop.get("event_id") in locked_event_ids:
                        player["locked"] = True
                        player["commence_time"] = prop.get("commence_time")
                        break
            
            # Filter out locked if requested
            if not include_locked:
                board["players"] = [p for p in board["players"] if not p.get("locked")]
        
        # Add lock status and t-minus info
        lock_status = await game_lock_engine.get_lock_status()
        board["lock_status"] = lock_status
        board["t_minus_games"] = lock_status.get("t_minus_details", [])
        board["locked_count"] = len(locked_event_ids)
    
    return board


@api_router.get("/v3/cached-player/{player_name}")
async def get_cached_player(player_name: str):
    """
    Get a single player from the CACHED database.
    NO Odds API calls. Zero credit usage.
    
    If player not found, returns "Lines loading..." message.
    Does NOT trigger any API call.
    """
    if not demon_goblin_engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    result = await demon_goblin_engine.get_cached_player(player_name)
    
    return result


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


# ==================== GAME LOCK ENGINE ENDPOINTS ====================

@api_router.get("/v3/lock-status")
async def get_lock_status():
    """
    GAME LOCK STATUS - Dashboard overview of active/locked games.
    
    Returns:
    - active_games: Number of games still open for betting
    - locked_games: Number of games that have started (removed from feeds)
    - t_minus_games: Number of games starting in <15 minutes
    - t_minus_details: Top 5 soonest games with countdown timers
    - engine_running: Whether the 60-second lock check loop is active
    """
    if not game_lock_engine:
        raise HTTPException(status_code=500, detail="Game Lock Engine not initialized")
    
    result = await game_lock_engine.get_lock_status()
    return result


@api_router.get("/v3/t-minus-games")
async def get_t_minus_games():
    """
    T-MINUS COUNTDOWN - Games starting within 15 minutes.
    
    Returns games with:
    - t_minus_seconds: Seconds until tip-off
    - t_minus_display: Human-readable format (e.g., "T-12:45")
    - matchup info and player count
    
    Use for high-stakes countdown timers on player cards.
    """
    if not game_lock_engine:
        raise HTTPException(status_code=500, detail="Game Lock Engine not initialized")
    
    result = await game_lock_engine.get_t_minus_games()
    return {"games": result, "count": len(result)}


@api_router.get("/v3/locked-games")
async def get_locked_games():
    """
    LOCKED GAMES - Games that have started and are in progress.
    
    Use for Live Score Ticker integration - these games have been
    removed from the betting board but can be shown in real-time.
    """
    if not game_lock_engine:
        raise HTTPException(status_code=500, detail="Game Lock Engine not initialized")
    
    result = await game_lock_engine.get_locked_games()
    return {"games": result, "count": len(result)}


class ParlayValidationRequest(BaseModel):
    player_names: List[str]


@api_router.post("/v3/validate-parlay")
async def validate_parlay(request: ParlayValidationRequest):
    """
    PARLAY VALIDATION - Pre-lock-in safety check.
    
    Validates that no games in the parlay have started in the last 60 seconds.
    Call this before a user "Locks In" their parlay.
    
    Request:
    - player_names: List of player names in the parlay
    
    Returns:
    - valid: Boolean - whether all picks are valid
    - invalid_picks: List of picks with games that have started
    - message: Human-readable status
    """
    if not game_lock_engine:
        raise HTTPException(status_code=500, detail="Game Lock Engine not initialized")
    
    result = await game_lock_engine.validate_parlay(request.player_names)
    return result


@api_router.post("/v3/check-locks")
async def manual_check_locks():
    """
    MANUAL LOCK CHECK - Trigger immediate lock check.
    
    Forces an immediate check for games that should be locked.
    Normally runs automatically every 60 seconds.
    """
    if not game_lock_engine:
        raise HTTPException(status_code=500, detail="Game Lock Engine not initialized")
    
    result = await game_lock_engine.check_and_lock_games()
    return result

@api_router.get("/v3/data-status")
async def get_data_status():
    """
    V3.1 TRUTH ENGINE - Data Integrity Status
    
    Reports the integrity status of the latest data sync for the frontend status light.
    
    Returns:
    - status: "verified" | "discrepancy_found" | "no_data" | "pending_verification"
    - verified_count: Props that passed all verification checks
    - failed_count: Props that failed verification (hallucinations, discrepancies, Naji Safeguard)
    - verification_rate: Percentage of props verified
    - recent_failures: Last 5 verification failures with details
    
    Frontend uses this to display:
    - Green light: status = "verified" (all data verified)
    - Red light: status = "discrepancy_found" (verification failures detected)
    - Gray light: status = "no_data" or "pending_verification"
    """
    if not demon_goblin_engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    result = await demon_goblin_engine.get_data_integrity_status()
    
    return result


# ==================== ADAPTIVE SYNC ENGINE ENDPOINTS ====================
# Mission-Critical Polling System - Conserve API Credits + Maximize Freshness

@api_router.get("/v3/sync-status")
async def get_adaptive_sync_status():
    """
    ADAPTIVE SYNC ENGINE - Get Current Sync Status
    
    Returns:
    - last_sync: When data was last refreshed
    - sync_age_display: Human-readable time since last sync (e.g., "45s ago")
    - engine_status: "running" | "stopped"
    - active_games: Number of games being tracked
    - mission_critical_games: Games within 60 mins of tip-off
    - game_registry: Full list of tracked games with their polling status
    
    Polling Tiers:
    - Standby (>6hrs): Refresh every 60 minutes
    - Active (1-6hrs): Refresh every 10 minutes
    - Mission Critical (<60mins): Refresh every 60 seconds
    - Post-Tip: Cease polling for that game
    """
    engine = get_adaptive_sync_engine()
    if not engine:
        return {"error": "Adaptive Sync Engine not initialized", "engine_status": "disabled"}
    
    status = await engine.get_sync_status()
    return status


@api_router.get("/v3/stale-intel-check")
async def check_for_stale_intel(game_id: Optional[str] = None):
    """
    STALE INTEL DETECTION - Check for outdated data in mission-critical windows.
    
    If data is older than 5 minutes during a mission-critical window (<60 mins to tip),
    this endpoint returns a warning.
    
    Args:
    - game_id: Optional - Check specific game only
    
    Returns:
    - has_stale_intel: True if any mission-critical data is stale
    - stale_games: List of games with stale data
    - threshold_seconds: Current stale threshold (300 = 5 minutes)
    """
    engine = get_adaptive_sync_engine()
    if not engine:
        return {"error": "Adaptive Sync Engine not initialized", "has_stale_intel": False}
    
    result = await engine.check_stale_intel(game_id)
    return result


@api_router.post("/v3/priority-refresh")
async def trigger_priority_refresh(game_id: Optional[str] = None):
    """
    PRIORITY REFRESH - Trigger immediate high-priority data refresh.
    
    Use this when stale intel is detected during mission-critical windows.
    Bypasses normal polling schedule for immediate refresh.
    
    Args:
    - game_id: Optional - Refresh specific game only
    
    Returns:
    - updated: Number of records updated
    - timestamp: Refresh completion time
    - trigger: "priority_refresh"
    """
    engine = get_adaptive_sync_engine()
    if not engine:
        raise HTTPException(status_code=500, detail="Adaptive Sync Engine not initialized")
    
    result = await engine.trigger_priority_refresh(game_id)
    return result


@api_router.get("/v3/intel-freshness")
async def get_intel_with_freshness(limit: int = 100):
    """
    INTEL WITH FRESHNESS - Get cached board data with freshness indicators.
    
    Returns all cached odds data with:
    - last_updated timestamp
    - freshness.seconds_ago: How old the data is
    - freshness.display: Human-readable (e.g., "45s ago")
    - freshness.is_stale: True if older than 5 minutes
    
    Use this for frontend to display "Intel updated 45s ago" on cards.
    """
    engine = get_adaptive_sync_engine()
    if not engine:
        raise HTTPException(status_code=500, detail="Adaptive Sync Engine not initialized")
    
    result = await engine.get_board_with_freshness(limit)
    return result


@api_router.post("/v3/adaptive-sync/start")
async def start_adaptive_sync():
    """Start the adaptive sync engine (if stopped)."""
    engine = get_adaptive_sync_engine()
    if not engine:
        raise HTTPException(status_code=500, detail="Adaptive Sync Engine not initialized")
    
    await engine.start()
    return {"status": "started", "message": "Adaptive Sync Engine started"}


@api_router.post("/v3/adaptive-sync/stop")
async def stop_adaptive_sync():
    """Stop the adaptive sync engine."""
    engine = get_adaptive_sync_engine()
    if not engine:
        raise HTTPException(status_code=500, detail="Adaptive Sync Engine not initialized")
    
    await engine.stop()
    return {"status": "stopped", "message": "Adaptive Sync Engine stopped"}



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

@api_router.get("/v3/scheduler-status")
async def get_scheduler_status():
    """
    Get the status of the daily sync scheduler
    Shows next run time, job info, and scheduler state
    """
    global scheduler
    
    if not scheduler:
        return {
            "success": False,
            "error": "Scheduler not initialized"
        }
    
    jobs = scheduler.get_jobs()
    job_info = []
    
    for job in jobs:
        job_info.append({
            "id": job.id,
            "name": job.name,
            "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
            "trigger": str(job.trigger)
        })
    
    return {
        "success": True,
        "scheduler_running": scheduler.running,
        "timezone": SCHEDULER_TIMEZONE,
        "daily_sync_time": f"{DAILY_SYNC_HOUR:02d}:{DAILY_SYNC_MINUTE:02d} UTC",
        "jobs_count": len(jobs),
        "jobs": job_info,
        "current_time_utc": datetime.now(timezone.utc).isoformat()
    }


@api_router.post("/v3/trigger-scheduled-sync")
async def trigger_scheduled_sync_manually():
    """
    Manually trigger the scheduled daily sync
    Useful for testing or forcing an immediate refresh
    """
    if not demon_goblin_engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    logger.info("[MANUAL TRIGGER] Triggering scheduled sync manually")
    
    # Run the sync
    result = await demon_goblin_engine.run_full_sync()
    
    return {
        "success": True,
        "message": "Manual sync triggered successfully",
        "result": {
            "unique_players": result.get("unique_players", 0),
            "total_props": result.get("total_props", 0),
            "standard_count": result.get("standard_count", 0),
            "demons_count": result.get("demons_count", 0),
            "goblins_count": result.get("goblins_count", 0),
            "duration": result.get("duration", 0)
        }
    }


# NOTE: Vision AI, Injury, Live Scores, Command Center, and AI Context routes 
# have been moved to modular route files in /routes/ directory

@api_router.get("/v3/breaking-news")
async def get_breaking_news(injury_only: bool = False):
    """
    Get breaking NBA news from multiple sources.
    
    Uses live_scores_engine for RSS feeds + ESPN news.
    
    Args:
        injury_only: If true, prioritize injury-related news
    """
    # Try live_scores_engine first (has RSS feeds)
    if live_scores_engine:
        result = await live_scores_engine.fetch_breaking_news()
        if result.get("success") and result.get("news"):
            news = result.get("news", [])
            if injury_only:
                # Filter for injury-related news
                injury_keywords = ["injury", "out", "questionable", "doubtful", "probable", "day-to-day", "ruled out", "ankle", "knee", "back", "hamstring"]
                news = [n for n in news if any(kw in (n.get("title", "") + n.get("headline", "")).lower() for kw in injury_keywords)]
            
            return {
                "success": True,
                "news_count": len(news),
                "injury_filter": injury_only,
                "news": news[:15]
            }
    
    # Fallback to injury_service
    if injury_service:
        news = await injury_service.get_breaking_news(injury_only=injury_only)
        return {
            "success": True,
            "news_count": len(news),
            "injury_filter": injury_only,
            "news": news
        }
    
    return {"success": False, "news": [], "news_count": 0}


# NOTE: Live Scores (/v3/live-scores/*) and Command Center (/v3/command-center/*) 
# routes moved to routes/live_scores.py

# NOTE: AI Context (/v3/ai-context/*) routes moved to routes/ai_context.py


app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("shutdown")
async def shutdown_db_client():
    # Shutdown scheduler
    global scheduler
    if scheduler:
        scheduler.shutdown(wait=False)
        logger.info("[SCHEDULER] APScheduler shutdown")
    
    client.close()
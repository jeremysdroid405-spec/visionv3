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

@api_router.post("/auth/signup", response_model=UserResponse)
async def signup(request: SignUpRequest):
    try:
        if not supabase:
            raise HTTPException(status_code=500, detail="Supabase not configured")
        
        auth_response = supabase.auth.sign_up({
            "email": request.email,
            "password": request.password,
            "options": {
                "data": {
                    "full_name": request.full_name or ""
                }
            }
        })
        
        if not auth_response.user:
            raise HTTPException(status_code=400, detail="Signup failed")
        
        profile_data = {
            "id": auth_response.user.id,
            "email": auth_response.user.email,
            "full_name": request.full_name or "",
            "tier": "free",
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        return UserResponse(
            user_id=auth_response.user.id,
            email=auth_response.user.email or "",
            profile=ProfileResponse(**profile_data),
            access_token=auth_response.session.access_token if auth_response.session else ""
        )
    except Exception as e:
        logger.error(f"Signup error: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Signup failed: {str(e)}")

@api_router.post("/auth/login", response_model=UserResponse)
async def login(request: LoginRequest):
    try:
        if not supabase:
            raise HTTPException(status_code=500, detail="Supabase not configured")
        
        auth_response = supabase.auth.sign_in_with_password({
            "email": request.email,
            "password": request.password
        })
        
        if not auth_response.user:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        
        profile_data = {
            "id": auth_response.user.id,
            "email": auth_response.user.email,
            "full_name": auth_response.user.user_metadata.get("full_name", ""),
            "tier": "free",
            "created_at": auth_response.user.created_at
        }
        
        return UserResponse(
            user_id=auth_response.user.id,
            email=auth_response.user.email or "",
            profile=ProfileResponse(**profile_data),
            access_token=auth_response.session.access_token
        )
    except Exception as e:
        logger.error(f"Login error: {str(e)}")
        raise HTTPException(status_code=401, detail="Invalid credentials")

@api_router.get("/profile", response_model=ProfileResponse)
async def get_profile(current_user = Depends(get_current_user)):
    profile_data = {
        "id": current_user.id,
        "email": current_user.email,
        "full_name": current_user.user_metadata.get("full_name", ""),
        "tier": current_user.user_metadata.get("tier", "free"),
        "created_at": current_user.created_at
    }
    return ProfileResponse(**profile_data)

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

# ==================== DEMON TRACKER V2 ENDPOINTS ====================

@api_router.get("/demon-tracker/status")
async def get_demon_tracker_status():
    """Get current Demon Tracker sync status"""
    if not demon_tracker:
        raise HTTPException(status_code=500, detail="Demon Tracker not initialized")
    
    status = await demon_tracker.get_sync_status()
    return {"success": True, "data": status}

@api_router.post("/demon-tracker/sync")
async def trigger_demon_tracker_sync():
    """Manually trigger deep ingestion sync"""
    if not demon_tracker:
        raise HTTPException(status_code=500, detail="Demon Tracker not initialized")
    
    result = await demon_tracker.run_deep_ingestion()
    return {"success": True, "result": result}

@api_router.get("/demon-tracker/events")
async def get_todays_events():
    """Get today's NBA events from The Odds API"""
    if not demon_tracker:
        raise HTTPException(status_code=500, detail="Demon Tracker not initialized")
    
    events = await demon_tracker.fetch_todays_events()
    return {
        "success": True,
        "count": len(events),
        "events": [
            {
                "id": e.get("id"),
                "home_team": e.get("home_team"),
                "away_team": e.get("away_team"),
                "commence_time": e.get("commence_time")
            }
            for e in events
        ]
    }

@api_router.get("/demon-tracker/event/{event_id}/odds")
async def get_event_odds(event_id: str):
    """Get all odds for a specific event including player props"""
    if not demon_tracker:
        raise HTTPException(status_code=500, detail="Demon Tracker not initialized")
    
    odds = await demon_tracker.fetch_event_odds(event_id)
    if not odds:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")
    
    props = demon_tracker.extract_player_props_from_odds(odds)
    
    return {
        "success": True,
        "event": {
            "id": odds.get("id"),
            "home_team": odds.get("home_team"),
            "away_team": odds.get("away_team"),
            "commence_time": odds.get("commence_time")
        },
        "bookmakers_count": len(odds.get("bookmakers", [])),
        "player_props_count": len(props),
        "player_props": props[:50]  # Limit response size
    }

@api_router.get("/demon-tracker/props")
async def get_processed_props(
    color: Optional[str] = Query(None, description="Filter by card color: green, yellow, red, standard"),
    bookmaker: Optional[str] = Query(None),
    market: Optional[str] = Query(None),
    demons_only: bool = Query(False)
):
    """Get processed demon cards with filters"""
    if not demon_tracker:
        raise HTTPException(status_code=500, detail="Demon Tracker not initialized")
    
    cards = await demon_tracker.get_demon_cards(
        color=color,
        bookmaker=bookmaker,
        market=market,
        demons_only=demons_only
    )
    
    return {
        "success": True,
        "count": len(cards),
        "cards": cards
    }

@api_router.get("/demon-tracker/demons")
async def get_demon_lines():
    """Get all qualified Demon lines (L10 hit rate >= 40%)"""
    if not demon_tracker:
        raise HTTPException(status_code=500, detail="Demon Tracker not initialized")
    
    cards = await demon_tracker.get_demon_cards(demons_only=True)
    
    return {
        "success": True,
        "count": len(cards),
        "demons": cards
    }

@api_router.get("/demon-tracker/cards/green")
async def get_green_cards():
    """Get all GREEN demon cards (high hit rate >= 50%)"""
    if not demon_tracker:
        raise HTTPException(status_code=500, detail="Demon Tracker not initialized")
    
    cards = await demon_tracker.get_demon_cards(color="green")
    return {"success": True, "count": len(cards), "cards": cards}

@api_router.get("/demon-tracker/cards/yellow")
async def get_yellow_cards():
    """Get all YELLOW demon cards (injury/news warnings)"""
    if not demon_tracker:
        raise HTTPException(status_code=500, detail="Demon Tracker not initialized")
    
    cards = await demon_tracker.get_demon_cards(color="yellow")
    return {"success": True, "count": len(cards), "cards": cards}

@api_router.get("/demon-tracker/cards/red")
async def get_red_cards():
    """Get all RED demon cards (low hit rate < 30% or injured)"""
    if not demon_tracker:
        raise HTTPException(status_code=500, detail="Demon Tracker not initialized")
    
    cards = await demon_tracker.get_demon_cards(color="red")
    return {"success": True, "count": len(cards), "cards": cards}

@api_router.get("/demon-tracker/player/{player_name}")
async def get_player_analysis(player_name: str, line: float = Query(20.0), market: str = Query("player_points")):
    """Get full analysis for a specific player"""
    if not demon_tracker:
        raise HTTPException(status_code=500, detail="Demon Tracker not initialized")
    
    # Search for player
    bdl_player = await demon_tracker.search_bdl_player(player_name)
    if not bdl_player:
        raise HTTPException(status_code=404, detail=f"Player {player_name} not found")
    
    # Get stats
    games = await demon_tracker.fetch_player_season_stats(bdl_player.get("id"))
    
    # Calculate hit rates
    hit_rates = demon_tracker.calculate_hit_rates(games, market, line)
    
    # Get injury info
    injury_info = demon_tracker.check_player_injury_and_news(player_name)
    
    return {
        "success": True,
        "player": {
            "id": bdl_player.get("id"),
            "name": f"{bdl_player.get('first_name', '')} {bdl_player.get('last_name', '')}".strip(),
            "team": bdl_player.get("team", {}).get("full_name", ""),
            "position": bdl_player.get("position", "")
        },
        "market": market,
        "line": line,
        "hit_rates": hit_rates,
        "injury_info": injury_info,
        "games_analyzed": len(games),
        "last_5_games": [
            {
                "date": g.get("game", {}).get("date", "")[:10],
                "pts": g.get("pts", 0),
                "reb": g.get("reb", 0),
                "ast": g.get("ast", 0),
                "fg3m": g.get("fg3m", 0),
                "min": g.get("min", 0)
            }
            for g in games[:5]
        ]
    }

@api_router.get("/demon-tracker/search")
async def search_player_cards(
    q: str = Query(..., description="Player name to search"),
    market: Optional[str] = Query(None)
):
    """Search for a player's cards"""
    if not demon_tracker:
        raise HTTPException(status_code=500, detail="Demon Tracker not initialized")
    
    cards = await demon_tracker.get_demon_cards(player_name=q, market=market)
    
    return {
        "success": True,
        "query": q,
        "count": len(cards),
        "cards": cards
    }

@api_router.get("/demon-tracker/board")
async def get_full_demon_board():
    """
    Get the full Demon Board with color-coded cards
    Green: High hit rate (>=50%)
    Yellow: Injury/news warning
    Red: Low hit rate (<30%) or injured OUT
    """
    if not demon_tracker:
        raise HTTPException(status_code=500, detail="Demon Tracker not initialized")
    
    # Get all demon cards
    all_cards = await demon_tracker.get_demon_cards()
    
    # Filter out None cards
    all_cards = [c for c in all_cards if c is not None]
    
    # Count by color
    color_counts = {
        "green": sum(1 for c in all_cards if c and c.get("card_color") == "green"),
        "yellow": sum(1 for c in all_cards if c and c.get("card_color") == "yellow"),
        "red": sum(1 for c in all_cards if c and c.get("card_color") == "red"),
        "standard": sum(1 for c in all_cards if c and c.get("card_color") == "standard")
    }
    
    # Group by event
    events_map = {}
    for card in all_cards:
        if not card:
            continue
        event_id = card.get("event_id", "unknown")
        if event_id not in events_map:
            events_map[event_id] = {
                "event_id": event_id,
                "home_team": card.get("home_team"),
                "away_team": card.get("away_team"),
                "commence_time": card.get("commence_time"),
                "cards": []
            }
        events_map[event_id]["cards"].append(card)
    
    # Sort events by game time
    events_list = sorted(
        events_map.values(),
        key=lambda x: x.get("commence_time") or ""
    )
    
    # Count demons
    total_demons = sum(
        1 for c in all_cards 
        if c and c.get("hit_rates") and c.get("hit_rates", {}).get("is_demon")
    )
    
    return {
        "success": True,
        "sync_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "events_count": len(events_list),
        "total_cards": len(all_cards),
        "total_demons": total_demons,
        "card_colors": color_counts,
        "board": events_list
    }

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


# ==================== DEMON RADAR ENDPOINT ====================

@api_router.get("/v3/demon-radar")
async def get_demon_radar():
    """
    THE DEMON RADAR - Top 10 picks based on mathematical analysis.
    
    Algorithm:
    1. Hit Probability (P) = (H10 × 0.6) + (H5 × 0.4)
    2. Line Gap (G) = (Demon_Value - Standard_Value) / Standard_Value
    3. Final Score = P - (G × 100)
    
    Logic Guard: Only includes picks with P >= 60%
    
    NO API CALLS - reads from pre-calculated MongoDB data.
    """
    if not demon_goblin_engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    result = await demon_goblin_engine.get_demon_radar()
    
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


# ==================== BOARD INTELLIGENCE ENDPOINTS ====================
# Automated Board Intelligence & Sync System

@api_router.get("/v3/board-intel/status")
async def get_board_intel_status():
    """
    BOARD INTELLIGENCE STATUS
    
    Returns:
    - last_sync_time: When data was last synced
    - last_sync_type: "primary" (full + Vision) or "delta" (odds only)
    - time_since_sync: "MM:SS" format
    - time_since_sync_display: "Last Synced: MM:SS" for footer display
    - next_scheduled_sync: Next sync time and type
    - scheduler_running: Whether automated scheduler is active
    """
    try:
        engine = get_board_intel_engine()
        await engine.initialize()
        status = await engine.get_sync_status()
        return status
    except Exception as e:
        return {
            "error": str(e),
            "time_since_sync_display": "Sync status unavailable",
            "scheduler_running": False
        }


@api_router.post("/v3/board-intel/primary-sync")
async def run_primary_sync():
    """
    PRIMARY SYNC (Manual Trigger)
    
    Runs a full global fetch with Vision AI for all Goblins and Demons.
    Normally scheduled for 10:30 AM ET.
    """
    try:
        board_intel = get_board_intel_engine()
        await board_intel.initialize()
        
        # Get the demon goblin engine (uses global db instance)
        dg_engine = DemonGoblinEngine(db)
        
        result = await board_intel.run_primary_sync(dg_engine)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@api_router.post("/v3/board-intel/delta-refresh")
async def run_delta_refresh():
    """
    DELTA REFRESH (Manual Trigger)
    
    Updates line and price values for existing players.
    - New Entry: Triggers one-time Vision AI for new players
    - Removal: Removes players whose lines are pulled
    
    Normally scheduled for 1:45 PM, 4:00 PM, 5:45 PM, 7:00 PM ET.
    """
    try:
        board_intel = get_board_intel_engine()
        await board_intel.initialize()
        
        dg_engine = DemonGoblinEngine(db)
        
        result = await board_intel.run_delta_refresh(dg_engine)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@api_router.post("/v3/board-intel/start-scheduler")
async def start_board_intel_scheduler():
    """
    START AUTOMATED SCHEDULER
    
    Starts background tasks for:
    - Primary Sync at 10:30 AM ET
    - Delta Refreshes at 1:45 PM, 4:00 PM, 5:45 PM, 7:00 PM ET
    - Live Ticker handover every 60 seconds
    """
    try:
        board_intel = get_board_intel_engine()
        await board_intel.initialize()
        
        dg_engine = DemonGoblinEngine(db)
        
        lock_engine = get_game_lock_engine()
        
        await board_intel.start_scheduler(dg_engine, lock_engine)
        
        return {
            "status": "started",
            "message": "Board Intelligence scheduler started",
            "schedule": {
                "primary_sync": "10:30 AM ET (Full + Vision AI)",
                "delta_refreshes": ["1:45 PM", "4:00 PM", "5:45 PM", "7:00 PM"],
                "live_ticker": "Every 60 seconds"
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@api_router.post("/v3/board-intel/stop-scheduler")
async def stop_board_intel_scheduler():
    """Stop the automated scheduler."""
    try:
        board_intel = get_board_intel_engine()
        board_intel.stop_scheduler()
        return {"status": "stopped", "message": "Board Intelligence scheduler stopped"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@api_router.get("/v3/live-ticker")
async def get_live_ticker():
    """
    LIVE TICKER - Games that have started
    
    Returns games that have been moved from the betting board to the live ticker.
    Updated every 60 seconds when games start.
    """
    try:
        board_intel = get_board_intel_engine()
        await board_intel.initialize()
        games = await board_intel.get_live_ticker_games()
        return {
            "live_games": games,
            "count": len(games)
        }
    except Exception as e:
        return {"live_games": [], "count": 0, "error": str(e)}


@api_router.post("/v3/board-intel/early-bird")
async def run_early_bird_scan():
    """
    EARLY BIRD SCAN (8:15 AM ET - Manual Trigger)
    
    - First global fetch for star players
    - Creates "Scouting Mission Briefing" cards for games without lines
    - Smart Anchor Vision: Analyzes Season Avg vs Opponent Defense
    
    Returns projections for players awaiting official lines.
    """
    try:
        board_intel = get_board_intel_engine()
        await board_intel.initialize()
        
        dg_engine = DemonGoblinEngine(db)
        
        result = await board_intel.run_early_bird_scan(dg_engine)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@api_router.get("/v3/scouting-projections")
async def get_scouting_projections():
    """
    SCOUTING PROJECTIONS
    
    Returns "Scouting Mission Briefing" cards for players awaiting official lines.
    These are star players with projected stats but no live betting lines yet.
    
    Display with "Scouting" badge (orange themed) in the UI.
    
    Each projection includes:
    - player_name
    - team, opponent
    - status: "Awaiting Official Mission Parameters"
    - projections: {points, rebounds, assists, pra}
    - season_avg: Player's season averages
    - last_3_avg: Performance in last 3 games
    - smart_anchor_vision: AI analysis of expected line
    """
    try:
        board_intel = get_board_intel_engine()
        await board_intel.initialize()
        
        projections = await board_intel.get_scouting_projections()
        
        return {
            "projections": projections,
            "count": len(projections),
            "status": "early_bird_active" if len(projections) > 0 else "full_drop_complete"
        }
    except Exception as e:
        return {"projections": [], "count": 0, "error": str(e)}


# ==================== NBA MASTER HUB ENDPOINTS ====================
# SINGLE SOURCE OF TRUTH for all player data

@api_router.get("/v3/master-hub/player/{player_id}")
async def get_player_intel(player_id: str):
    """
    THE VALET FUNCTION - Fetch player intel from Master Hub
    
    This is the ONLY way to access player data from NBA_MASTER_HUB_2026.
    
    Args:
        player_id: Player ID (tank01_id, nba_id, or display_name)
        
    Returns:
        Complete player object with all fields
    """
    player = await fetchPlayerIntel(player_id)
    if not player:
        raise HTTPException(status_code=404, detail=f"Player not found: {player_id}")
    return player


@api_router.get("/v3/master-hub/player/name/{display_name}")
async def get_player_by_name(display_name: str):
    """Fetch player by display name."""
    player = await fetchPlayerIntelByName(display_name)
    if not player:
        raise HTTPException(status_code=404, detail=f"Player not found: {display_name}")
    return player


@api_router.get("/v3/master-hub/search")
async def search_hub_players(q: str, limit: int = 10):
    """Search players in Master Hub."""
    players = await hubSearchPlayers(q, limit)
    return {"players": players, "count": len(players)}


@api_router.get("/v3/master-hub/stats")
async def get_hub_statistics():
    """Get Master Hub statistics."""
    return await getHubStats()


@api_router.post("/v3/master-hub/sync")
async def trigger_hub_sync():
    """
    Manually trigger Master Hub daily sync.
    
    Normally runs at 4:00 AM ET automatically.
    """
    result = await runHubSync()
    return result


@api_router.post("/v3/master-hub/start-scheduler")
async def start_hub_scheduler():
    """Start the 4:00 AM ET daily sync scheduler."""
    hub = get_master_hub()
    await hub.startDailyScheduler()
    return {"status": "started", "schedule": "4:00 AM ET daily"}


# ==================== RAW STAT VALIDATION ENDPOINTS ====================
# DATA INTEGRITY CRISIS RESPONSE - Zero Processing, Raw API Data Only

@api_router.get("/v3/raw-validation/{player_name}")
async def get_raw_validation_for_player(player_name: str):
    """
    RAW STAT VALIDATION - Fetch unprocessed stats for manual ESPN verification.
    
    This endpoint returns EXACTLY what BallDontLie API returns.
    NO processing, NO adjustments, NO interpretation.
    
    Compare these values directly against ESPN box scores.
    If they don't match, we have an API data issue.
    
    Returns:
    - player_name: str
    - bdl_player_id: int
    - last_5_games: [
        { date, vs, pts (RAW), reb (RAW), ast (RAW) }
      ]
    """
    global raw_stat_fetcher
    if not raw_stat_fetcher:
        raise HTTPException(status_code=500, detail="Raw Stat Fetcher not initialized")
    
    result = await raw_stat_fetcher.fetch_and_validate_player(player_name)
    
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error", "Player not found"))
    
    return result


@api_router.post("/v3/raw-validation/batch")
async def batch_raw_validation(player_names: List[str]):
    """
    Fetch raw validation data for multiple players at once.
    
    Use this to populate the validation table UI.
    
    Request body: ["Luka Doncic", "Anthony Edwards", "Naji Marshall"]
    """
    global raw_stat_fetcher
    if not raw_stat_fetcher:
        raise HTTPException(status_code=500, detail="Raw Stat Fetcher not initialized")
    
    results = []
    for name in player_names[:20]:  # Limit to 20 players
        try:
            result = await raw_stat_fetcher.fetch_and_validate_player(name)
            if result.get("success"):
                results.append(result["validation_entry"])
            else:
                results.append({
                    "player_name": name,
                    "error": result.get("error", "Failed to fetch")
                })
        except Exception as e:
            results.append({
                "player_name": name,
                "error": str(e)
            })
    
    return {
        "success": True,
        "validation_entries": results,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": "balldontlie_raw_unprocessed"
    }


@api_router.get("/v3/raw-validation-table")
async def get_raw_validation_table():
    """
    Get the full validation table for the UI.
    
    Returns all players that have been fetched for validation,
    with their RAW stats for manual ESPN comparison.
    """
    global raw_stat_fetcher
    if not raw_stat_fetcher:
        raise HTTPException(status_code=500, detail="Raw Stat Fetcher not initialized")
    
    result = await raw_stat_fetcher.get_validation_table()
    return result


@api_router.get("/v3/raw-player-games/{player_name}")
async def get_raw_player_games(player_name: str, num_games: int = 10):
    """
    Get raw game logs for a player - FULL DETAIL.
    
    This returns the complete raw API response for deep inspection.
    Use this to debug data issues.
    """
    global raw_stat_fetcher
    if not raw_stat_fetcher:
        raise HTTPException(status_code=500, detail="Raw Stat Fetcher not initialized")
    
    result = await raw_stat_fetcher.get_raw_recent_games(player_name, num_games)
    return result


# ==================== SOCIAL SIGNAL ENGINE ENDPOINTS ====================

@api_router.post("/v3/sync-social-signals")
async def sync_social_signals():
    """
    SOCIAL SIGNAL SYNC - News sentiment & revenge game detection.
    
    Uses Tank01 API to:
    1. Scan player news for volatility keywords (injuries, trades, suspensions)
    2. Detect revenge games (player vs former team)
    
    Should be called every 30 minutes for fresh signals.
    
    Returns:
    - volatility_flags: Count of players with negative news
    - revenge_games: Count of players facing former teams
    """
    global social_signal_engine
    if not social_signal_engine:
        raise HTTPException(status_code=500, detail="Social Signal Engine not initialized")
    
    logger.info("[SOCIAL SIGNAL] Manual sync triggered via API")
    result = await social_signal_engine.sync_social_signals()
    
    # Apply signals to cached board
    if result.get("success"):
        apply_result = await social_signal_engine.apply_signals_to_board()
        result["applied"] = apply_result
    
    return result


@api_router.get("/v3/social-signals")
async def get_social_signals():
    """
    Get all cached social signals.
    
    Returns dict of player signals with:
    - volatility_flag: boolean (🗞️ Intel Icon)
    - volatility_reason: string (REDUCED USAGE or VOLATILITY type)
    - revenge_game: boolean (🗡️ Dagger Icon)
    - revenge_opponent: string (former team abbreviation)
    """
    global social_signal_engine
    if not social_signal_engine:
        raise HTTPException(status_code=500, detail="Social Signal Engine not initialized")
    
    result = await social_signal_engine.get_all_signals()
    return result


@api_router.get("/v3/social-signal/{player_name}")
async def get_player_social_signal(player_name: str):
    """
    Get social signal for a specific player.
    
    Returns the volatility and revenge flags for UI display.
    """
    global social_signal_engine
    if not social_signal_engine:
        raise HTTPException(status_code=500, detail="Social Signal Engine not initialized")
    
    signal = await social_signal_engine.get_player_signal(player_name)
    
    if not signal:
        return {
            "player_name": player_name,
            "volatility_flag": False,
            "revenge_game": False,
            "message": "No signals detected"
        }
    
    return signal


# ==================== SCHEDULER ENDPOINTS ====================

@api_router.post("/v3/sync-master-roster")
async def sync_master_roster():
    """
    WEEKLY ROSTER SYNC - Source of Truth for player-to-team mapping.
    
    Fetches ALL NBA players from BallDontLie API and stores them in 
    the player_master_roster collection. Should run weekly (Sunday midnight)
    but can be triggered manually.
    
    This ensures accurate team assignments by overriding Odds API data.
    """
    if not demon_goblin_engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    logger.info("[MASTER ROSTER] Manual sync triggered via API")
    result = await demon_goblin_engine.sync_master_roster()
    
    return result


@api_router.post("/v3/sync-player-photos")
async def sync_player_photos():
    """
    PHOTO PIPELINE - Sync headshots for all active players.
    
    Sources:
    1. NBA CDN (official high-res headshots)
    2. Team logo fallback for missing headshots
    
    Updates cached_board and master_roster with photo URLs.
    """
    if not demon_goblin_engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    logger.info("[PHOTO SYNC] Manual sync triggered via API")
    result = await demon_goblin_engine.sync_player_photos()
    
    return result


@api_router.post("/v3/sync-active-players")
async def sync_active_players():
    """
    ACTIVE PLAYER SYNC - Fetches ONLY current NBA players from Tank01 with headshots.
    
    This is the recommended way to populate the player database:
    - Gets ~530 active NBA players (not 5000+ historical)
    - Includes ESPN headshot URLs directly from Tank01
    - Stores player metadata: team, position, jersey, height, weight, college
    
    Run this once to populate the database, then use sync-player-photos for updates.
    """
    if not demon_goblin_engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    logger.info("[ACTIVE PLAYER SYNC] Manual sync triggered via API")
    result = await demon_goblin_engine.sync_active_players_with_photos()
    
    return result


@api_router.post("/v3/refresh-board-photos")
async def refresh_board_photos():
    """
    Refresh photo URLs in cached_board from master_roster with fuzzy matching.
    
    Use this after sync-active-players to fix any name mismatches between
    Odds API player names and Tank01 roster names.
    """
    if not demon_goblin_engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    logger.info("[PHOTO REFRESH] Manual refresh triggered via API")
    result = await demon_goblin_engine.refresh_cached_board_photos()
    
    return result


@api_router.post("/v3/refresh-all-photos")
async def refresh_all_photos():
    """
    MASTER PHOTO REFRESH - Updates photo URLs across ALL collections.
    
    Refreshes photos in:
    - cached_board (main player board)
    - goblin_recon (parlay picks)
    - demon_radar (demon picks)
    - goblin_vault (goblin picks)
    
    Use this after sync-active-players to ensure all player photos are updated.
    """
    if not demon_goblin_engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    logger.info("[MASTER PHOTO REFRESH] Manual refresh triggered via API")
    result = await demon_goblin_engine.refresh_all_photos()
    
    return result


@api_router.get("/v3/players")
async def get_all_players():
    """
    Get all active NBA players with their headshots.
    """
    if not demon_goblin_engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    players = await demon_goblin_engine.master_roster.find(
        {"is_active": True},
        {"_id": 0}
    ).sort("player_name", 1).to_list(None)
    
    return {
        "success": True,
        "count": len(players),
        "players": players
    }


@api_router.get("/v3/player/{player_name}/photo")
async def get_player_photo(player_name: str):
    """
    Get a specific player's headshot URL.
    """
    if not demon_goblin_engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    # Try exact match first
    player = await demon_goblin_engine.master_roster.find_one(
        {"player_name": player_name},
        {"_id": 0, "player_name": 1, "team_abbreviation": 1, "photo_url": 1, "photo_source": 1}
    )
    
    # If not found, try normalized name match
    if not player:
        normalized = demon_goblin_engine.sanitize_player_name(player_name)
        player = await demon_goblin_engine.master_roster.find_one(
            {"normalized_name": normalized},
            {"_id": 0, "player_name": 1, "team_abbreviation": 1, "photo_url": 1, "photo_source": 1}
        )
    
    if not player:
        raise HTTPException(status_code=404, detail=f"Player '{player_name}' not found")
    
    return player


@api_router.get("/v3/team/{team_abbrev}/roster")
async def get_team_roster(team_abbrev: str):
    """
    Get all players on a specific team with their headshots.
    """
    if not demon_goblin_engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    players = await demon_goblin_engine.master_roster.find(
        {"team_abbreviation": team_abbrev.upper()},
        {"_id": 0}
    ).sort("player_name", 1).to_list(None)
    
    if not players:
        raise HTTPException(status_code=404, detail=f"Team '{team_abbrev}' not found")
    
    return {
        "team": team_abbrev.upper(),
        "count": len(players),
        "players": players
    }


# ==================== PAYOUT CALCULATION ENGINE ====================

class PayoutRequest(BaseModel):
    """Request model for payout calculation."""
    picks: List[Dict[str, Any]] = Field(..., description="List of picks with player_name, stat_type, line, direction, is_demon, is_goblin")


@api_router.post("/v3/calculate-payout")
async def calculate_payout(request: PayoutRequest):
    """
    Calculate live estimated payout for a slip.
    
    Each pick should have:
    - player_name: str
    - stat_type: str (PTS, REB, AST, etc.)
    - line: float (the line being played)
    - direction: str (over/under)
    - is_demon: bool (optional)
    - is_goblin: bool (optional)
    - standard_line: float (optional, for modifier calculation)
    
    Returns:
    - estimated_payout: The cumulative payout multiplier
    - legs: Details for each pick including asset_type and modifier
    - asset_breakdown: Count of demons, goblins, standards
    """
    try:
        result = calculate_payout_from_picks(request.picks)
        return result
    except Exception as e:
        logger.error(f"[PAYOUT] Error calculating payout: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@api_router.get("/v3/payout-estimate")
async def get_payout_estimate(
    num_picks: int = Query(..., ge=2, le=6, description="Number of picks (2-6)"),
    demon_count: int = Query(0, ge=0, description="Number of demon picks"),
    goblin_count: int = Query(0, ge=0, description="Number of goblin picks")
):
    """
    Quick payout estimate based on slip composition.
    
    Use this for real-time UI updates as picks are added.
    
    Example:
    - /v3/payout-estimate?num_picks=3&demon_count=2&goblin_count=0 → ~9.8x
    - /v3/payout-estimate?num_picks=2&demon_count=1&goblin_count=1 → ~3.0x
    """
    if demon_count + goblin_count > num_picks:
        raise HTTPException(
            status_code=400, 
            detail="demon_count + goblin_count cannot exceed num_picks"
        )
    
    payout = estimate_payout(num_picks, demon_count, goblin_count)
    standard_count = num_picks - demon_count - goblin_count
    
    return {
        "num_picks": num_picks,
        "asset_breakdown": {
            "demons": demon_count,
            "goblins": goblin_count,
            "standards": standard_count
        },
        "base_multiplier": BASE_MULTIPLIERS.get(num_picks, 3.0),
        "estimated_payout": payout,
        "payout_display": f"{payout:.1f}x"
    }


@api_router.get("/v3/payout-table")
async def get_payout_table():
    """
    Get the full payout reference table.
    
    Shows base multipliers and example payouts for different compositions.
    """
    table = {
        "base_multipliers": BASE_MULTIPLIERS,
        "modifier_ranges": {
            "demon": {"min": 1.10, "max": 1.50, "average": 1.25},
            "standard": {"min": 0.95, "max": 1.05, "average": 1.00},
            "goblin": {"min": 0.70, "max": 0.90, "average": 0.80}
        },
        "examples": {
            "2_pick": {
                "all_standard": estimate_payout(2, 0, 0),
                "all_demons": estimate_payout(2, 2, 0),
                "all_goblins": estimate_payout(2, 0, 2),
                "mixed": estimate_payout(2, 1, 1)
            },
            "3_pick": {
                "all_standard": estimate_payout(3, 0, 0),
                "all_demons": estimate_payout(3, 3, 0),
                "all_goblins": estimate_payout(3, 0, 3),
                "2_demons_1_standard": estimate_payout(3, 2, 0)
            },
            "4_pick": {
                "all_standard": estimate_payout(4, 0, 0),
                "all_demons": estimate_payout(4, 4, 0),
                "3_demons_1_goblin": estimate_payout(4, 3, 1)
            },
            "6_pick": {
                "all_standard": estimate_payout(6, 0, 0),
                "all_demons": estimate_payout(6, 6, 0),
                "all_goblins": estimate_payout(6, 0, 6)
            }
        },
        "formula": "Total Payout = Base Multiplier × (Mod_1 × Mod_2 × ... × Mod_n)"
    }
    return table


@api_router.post("/v3/sync-player-stats")
async def sync_player_stats():
    """
    STATS CACHE - Sync player game logs to MongoDB.
    
    Fetches stats from:
    1. BallDontLie API (primary)
    2. NBA.com API (fallback for missing players)
    
    Stores in dg_player_stats collection for fast hit rate calculations.
    Should be run daily before sync-to-mongo.
    """
    if not demon_goblin_engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    logger.info("[STATS SYNC] Manual sync triggered via API")
    result = await demon_goblin_engine.sync_player_stats()
    
    return result


@api_router.post("/v3/sync-daily-insights")
async def sync_daily_insights():
    """
    ADVANCED ANALYTICS - Calculate and cache daily insights for all players.
    
    Calculates:
    - Schedule Density Factor (B2B, 3-in-4 fatigue)
    - Pace Adjustment Factor (matchup tempo)
    - Usage Ripple Effect (teammate injuries)
    - Volatility Score (consistency rating)
    - Template-based Insight Summaries
    
    Should be run daily at 8:00 AM EST.
    """
    if not demon_goblin_engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    logger.info("[INSIGHTS] Manual sync triggered via API")
    result = await demon_goblin_engine.sync_daily_insights()
    
    return result


@api_router.get("/v3/player-insights/{player_name}")
async def get_player_insights(player_name: str):
    """
    Get advanced analytics insights for a specific player.
    
    Returns:
    - schedule_density_factor
    - pace_adjustment_factor
    - usage_bump_percent
    - volatility_score
    - insight_summary
    - ai_confidence_rating
    """
    if not demon_goblin_engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    insights = await demon_goblin_engine.get_player_insights(player_name)
    
    if not insights:
        raise HTTPException(status_code=404, detail=f"No insights found for {player_name}")
    
    return insights


@api_router.get("/v3/master-roster-status")
async def get_master_roster_status():
    """
    Get the current status of the master roster.
    Shows player count, teams, last sync time, and any flagged players.
    """
    if not demon_goblin_engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    
    # Get roster stats
    roster_count = await demon_goblin_engine.master_roster.count_documents({})
    flagged_count = await demon_goblin_engine.flagged_players.count_documents({"reviewed": False})
    
    # Get last sync time
    latest = await demon_goblin_engine.master_roster.find_one(
        {}, {"_id": 0, "synced_at": 1}, sort=[("synced_at", -1)]
    )
    
    # Get team distribution
    pipeline = [
        {"$group": {"_id": "$team_abbreviation", "count": {"$sum": 1}}},
        {"$sort": {"_id": 1}}
    ]
    teams = await demon_goblin_engine.master_roster.aggregate(pipeline).to_list(None)
    
    # Get flagged players
    flagged = await demon_goblin_engine.flagged_players.find(
        {"reviewed": False}, {"_id": 0}
    ).to_list(20)
    
    return {
        "success": True,
        "roster_count": roster_count,
        "teams_count": len(teams),
        "teams": {t["_id"]: t["count"] for t in teams},
        "last_sync": latest.get("synced_at") if latest else None,
        "flagged_players_count": flagged_count,
        "flagged_players": flagged
    }


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


# ==================== VISION AI ENDPOINTS ====================

class VisionInsightRequest(BaseModel):
    """Request model for single AI insight generation"""
    player_name: str
    stat_type: str = "points"
    current_line: float
    l10_rate: float = 50.0
    pace_factor: float = 1.0
    fatigue: str = "Normal"  # "Fresh", "Normal", "Fatigued"
    usage_bump: float = 0
    volatility: str = "Med"  # "Low", "Med", "High"
    is_demon: bool = False
    is_goblin: bool = False
    projected_score: Optional[float] = None


@api_router.post("/v3/vision/generate-insight")
async def generate_vision_insight(request: VisionInsightRequest):
    """
    VISION AI - Generate a single AI insight for a player prop.
    
    Uses Claude Sonnet 4.5 to generate a "badass" 1-sentence insight.
    Only use for Demons, Goblins, or High Volatility players to manage costs.
    
    The AI analyzes:
    - Hit rate trends (L10)
    - Pace adjustments
    - Fatigue factors
    - Usage bumps from injured teammates
    - Volatility risk level
    
    If projected_score differs from current_line by >15%, 
    the AI will explicitly mention the "Edge".
    """
    if not vision_ai_service:
        raise HTTPException(status_code=500, detail="Vision AI Service not initialized")
    
    result = await vision_ai_service.generate_single_insight(
        player_name=request.player_name,
        stat_type=request.stat_type,
        current_line=request.current_line,
        l10_rate=request.l10_rate,
        pace_factor=request.pace_factor,
        fatigue=request.fatigue,
        usage_bump=request.usage_bump,
        volatility=request.volatility,
        is_demon=request.is_demon,
        is_goblin=request.is_goblin,
        projected_score=request.projected_score
    )
    
    if not result.get('success'):
        raise HTTPException(status_code=400, detail=result.get('error', 'Failed to generate insight'))
    
    return result


@api_router.post("/v3/vision/trigger-batch")
async def trigger_vision_batch():
    """
    VISION AI BATCH - Generate insights for all eligible players.
    
    Filters for cost efficiency:
    - Only Demons (high payout potential)
    - Only Goblins (high safety picks)  
    - Only High Volatility players
    
    Should be called AFTER daily sync completes.
    Updates the insight_summary field in daily_insights collection.
    
    Rate limited: Max 3 concurrent API calls with 0.5s delays.
    """
    if not vision_ai_service:
        raise HTTPException(status_code=500, detail="Vision AI Service not initialized")
    
    logger.info("[VISION] Batch insight generation triggered")
    
    result = await vision_ai_service.trigger_insights_for_sync()
    
    return {
        "success": result.get('success', False),
        "message": "Vision AI batch processing complete",
        "insights_generated": result.get('insights_generated', 0),
        "errors_count": result.get('errors_count', 0),
        "eligible_players": result.get('eligible_players', 0),
        "total_players": result.get('total_players', 0),
        "sample_results": result.get('results', [])[:3]
    }


@api_router.get("/v3/vision/status")
async def get_vision_status():
    """
    Get Vision AI service status and configuration.
    """
    emergent_key_configured = bool(os.environ.get('EMERGENT_LLM_KEY'))
    
    # Count players with AI-generated insights
    ai_insights_count = 0
    if vision_ai_service:
        ai_insights_count = await db.dg_daily_insights.count_documents({
            "ai_generated_at": {"$exists": True}
        })
    
    return {
        "success": True,
        "service_initialized": vision_ai_service is not None,
        "emergent_key_configured": emergent_key_configured,
        "model": "claude-sonnet-4.5",
        "provider": "anthropic",
        "ai_insights_count": ai_insights_count,
        "cost_filters": {
            "demons_only": True,
            "goblins_only": True,
            "high_volatility_only": True
        }
    }


# ==================== INJURY INTELLIGENCE ENDPOINTS ====================

@api_router.post("/v3/injuries/sync")
async def sync_injuries():
    """
    INJURY SYNC - Fetch latest injury data from ESPN.
    
    Updates:
    - dg_injuries collection with current injury statuses
    - Usage ripple calculations for teammates of injured stars
    - Breaking news from ESPN
    
    Should be called periodically (every 30 mins during game days).
    """
    if not injury_service:
        raise HTTPException(status_code=500, detail="Injury Service not initialized")
    
    logger.info("[INJURY] Manual injury sync triggered")
    
    result = await injury_service.sync_injuries()
    
    if not result.get('success'):
        raise HTTPException(status_code=400, detail=result.get('error', 'Sync failed'))
    
    return result


@api_router.get("/v3/injuries")
async def get_all_injuries():
    """
    Get all current NBA injuries grouped by severity.
    
    Returns:
    - high_risk: Out, Doubtful players
    - medium_risk: Questionable, Day-To-Day, GTD players
    - low_risk: Probable players
    """
    if not injury_service:
        raise HTTPException(status_code=500, detail="Injury Service not initialized")
    
    return await injury_service.get_all_injuries()


@api_router.get("/v3/injuries/player/{player_name}")
async def get_player_injury(player_name: str):
    """
    Get injury status for a specific player.
    """
    if not injury_service:
        raise HTTPException(status_code=500, detail="Injury Service not initialized")
    
    injury = await injury_service.get_player_injury_status(player_name)
    
    if not injury:
        return {"success": True, "injury": None, "message": f"{player_name} has no reported injury"}
    
    return {"success": True, "injury": injury}


@api_router.get("/v3/injuries/team/{team_abbr}")
async def get_team_injuries(team_abbr: str):
    """
    Get all injuries for a specific team.
    """
    if not injury_service:
        raise HTTPException(status_code=500, detail="Injury Service not initialized")
    
    injuries = await injury_service.get_team_injuries(team_abbr)
    
    return {
        "success": True,
        "team": team_abbr.upper(),
        "injuries_count": len(injuries),
        "injuries": injuries
    }


@api_router.get("/v3/injuries/alerts")
async def get_injury_alerts():
    """
    Get injury alerts formatted for the dashboard board.
    Returns a dict mapping player_name -> injury_info for quick lookup.
    Used by frontend to display injury badges on player cards.
    """
    if not injury_service:
        raise HTTPException(status_code=500, detail="Injury Service not initialized")
    
    alerts = await injury_service.get_injury_alerts_for_board()
    
    return {
        "success": True,
        "alerts_count": len(alerts),
        "alerts": alerts
    }


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


# ==================== LIVE SCORES & COMMAND CENTER ====================

@api_router.get("/v3/live-scores")
async def get_live_scores(refresh: bool = False):
    """
    Get live NBA scores from The Odds API.
    
    Uses /scores endpoint to get real-time game scores.
    
    Args:
        refresh: Force refresh from API (otherwise uses cache)
    
    Returns:
        - games: List of games with scores and status
        - live_count: Number of games currently in play
        - upcoming_count: Number of games not yet started
    """
    if not live_scores_engine:
        raise HTTPException(status_code=500, detail="Live Scores Engine not initialized")
    
    if refresh:
        result = await live_scores_engine.fetch_live_scores()
    else:
        # Try cache first
        result = await live_scores_engine.get_cached_scores()
        if not result.get("success") or not result.get("games"):
            result = await live_scores_engine.fetch_live_scores()
    
    return result


@api_router.post("/v3/live-scores/refresh")
async def refresh_live_scores():
    """Force refresh live scores from The Odds API."""
    if not live_scores_engine:
        raise HTTPException(status_code=500, detail="Live Scores Engine not initialized")
    
    result = await live_scores_engine.fetch_live_scores()
    return result


@api_router.get("/v3/command-center/news")
async def get_command_center_news(custom_headlines: Optional[str] = None):
    """
    Get breaking news for the Command Center ticker.
    
    Combines RSS feeds from Rotoworld and ESPN with optional custom headlines.
    
    Args:
        custom_headlines: Comma-separated list of custom headlines to include
    
    Returns:
        - news: List of news items with title, source, category
    """
    if not live_scores_engine:
        raise HTTPException(status_code=500, detail="Live Scores Engine not initialized")
    
    # Parse custom headlines if provided
    headlines = None
    if custom_headlines:
        headlines = [h.strip() for h in custom_headlines.split("|") if h.strip()]
    
    result = await live_scores_engine.fetch_breaking_news(custom_headlines=headlines)
    return result


@api_router.get("/v3/command-center/ticker")
async def get_ticker_data():
    """
    Get combined data for the Command Center tickers.
    
    Returns both live scores and breaking news in a single call,
    optimized for the frontend ticker display.
    """
    if not live_scores_engine:
        raise HTTPException(status_code=500, detail="Live Scores Engine not initialized")
    
    # Get scores (from cache)
    scores_result = await live_scores_engine.get_cached_scores()
    if not scores_result.get("success"):
        scores_result = await live_scores_engine.fetch_live_scores()
    
    # Get news (from cache)
    news_result = await live_scores_engine.get_cached_news()
    if not news_result.get("success"):
        news_result = await live_scores_engine.fetch_breaking_news()
    
    # Format for ticker display
    ticker_items = []
    
    # Add live scores first
    for game in scores_result.get("games", []):
        if game["status"] == "in_play":
            ticker_items.append({
                "type": "live_score",
                "text": f"{game['away_team']} {game['away_score']} @ {game['home_team']} {game['home_score']} - {game['status_display']}",
                "priority": 1,
                "category": "live"
            })
        elif game["status"] == "upcoming":
            ticker_items.append({
                "type": "upcoming",
                "text": f"{game['away_team']} @ {game['home_team']} - {game['status_display']}",
                "priority": 2,
                "category": "upcoming"
            })
    
    # Add breaking news
    for news in news_result.get("news", [])[:10]:
        ticker_items.append({
            "type": "news",
            "text": news["title"],
            "source": news.get("source", ""),
            "priority": 3 if news.get("is_custom") else 4,
            "category": news.get("category", "news")
        })
    
    return {
        "success": True,
        "ticker_items": ticker_items,
        "live_games": scores_result.get("live_count", 0),
        "upcoming_games": scores_result.get("upcoming_count", 0),
        "news_count": len(news_result.get("news", []))
    }


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
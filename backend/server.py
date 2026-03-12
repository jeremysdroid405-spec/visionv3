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
from stats_manager_bdl import StatsManager
from demon_tracker_engine import DeepIngestionEngine

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

supabase: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY) if SUPABASE_ANON_KEY else None

app = FastAPI(title="NBA Best Bets API - Demon Tracker v2")
api_router = APIRouter(prefix="/api")
security = HTTPBearer()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

stats_manager = None
demon_tracker = None

async def initial_autonomous_sync():
    """Run autonomous deep ingestion on startup"""
    await asyncio.sleep(5)  # Wait for app to fully start
    
    # Run deep ingestion (Odds API + BDL + Tank01)
    if demon_tracker:
        logger.info("🚀 Running DEEP INGESTION on startup...")
        result = await demon_tracker.run_deep_ingestion()
        logger.info(f"Deep ingestion result: {result.get('step2_unique_players', 0)} players, {result.get('demons_found', 0)} demons")

@app.on_event("startup")
async def startup_event():
    global stats_manager, demon_tracker
    
    # Initialize stats manager (BallDontLie)
    stats_manager = StatsManager(db)
    logger.info("✓ Stats Manager initialized (BallDontLie)")
    
    # Initialize Deep Ingestion Engine
    demon_tracker = DeepIngestionEngine(db)
    logger.info("✓ Deep Ingestion Engine initialized (Two-Step Process)")
    
    # Run autonomous sync on startup
    asyncio.create_task(initial_autonomous_sync())

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
    client.close()
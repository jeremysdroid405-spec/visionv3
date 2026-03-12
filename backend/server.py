from fastapi import FastAPI, APIRouter, Depends, HTTPException, status
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
from stats_manager import StatsManager

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

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

app = FastAPI(title="NBA Best Bets API")
api_router = APIRouter(prefix="/api")
security = HTTPBearer()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

stats_manager = None

@app.on_event("startup")
async def startup_event():
    global stats_manager
    stats_manager = StatsManager(db)
    logger.info("✓ Stats Manager initialized")

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

@api_router.get("/")
async def root():
    return {"message": "NBA Best Bets API - Full Board System"}

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
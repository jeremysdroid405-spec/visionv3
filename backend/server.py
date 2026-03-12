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

class NBAGame(BaseModel):
    game_id: str
    home_team: str
    away_team: str
    game_date: str
    game_time: str

class Injury(BaseModel):
    player_name: str
    team: str
    injury_status: str
    description: Optional[str] = None

class PlayerProp(BaseModel):
    player_name: str
    team: str
    prop_type: str
    prizepicks_line: float
    market_avg: float
    draftkings_line: Optional[float] = None
    fanduel_line: Optional[float] = None
    betmgm_line: Optional[float] = None
    caesars_line: Optional[float] = None
    is_demon: bool = False
    demon_line: Optional[float] = None
    hit_rate: Optional[float] = None
    best_bet_score: float
    matchup_grade: str
    confidence: float

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

@api_router.get("/nba/games")
async def get_nba_games():
    try:
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://tank01-nba-live-in-game-real-time-statistics-nba.p.rapidapi.com/getNBAGamesForDate",
                params={"gameDate": today},
                headers={
                    "X-RapidAPI-Key": TANK01_API_KEY,
                    "X-RapidAPI-Host": "tank01-nba-live-in-game-real-time-statistics-nba.p.rapidapi.com"
                },
                timeout=10.0
            )
            
            if response.status_code == 200:
                data = response.json()
                cached_data = {
                    "date": today,
                    "games": data.get("body", []),
                    "cached_at": datetime.now(timezone.utc).isoformat()
                }
                await db.nba_games.update_one(
                    {"date": today},
                    {"$set": cached_data},
                    upsert=True
                )
                return {"success": True, "data": data.get("body", [])}
            else:
                logger.error(f"Tank01 API error: {response.status_code}")
                return {"success": False, "error": "Failed to fetch games"}
    except Exception as e:
        logger.error(f"Error fetching NBA games: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/nba/injuries")
async def get_injuries():
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://tank01-nba-live-in-game-real-time-statistics-nba.p.rapidapi.com/getNBAInjuryList",
                headers={
                    "X-RapidAPI-Key": TANK01_API_KEY,
                    "X-RapidAPI-Host": "tank01-nba-live-in-game-real-time-statistics-nba.p.rapidapi.com"
                },
                timeout=10.0
            )
            
            if response.status_code == 200:
                data = response.json()
                return {"success": True, "data": data.get("body", [])}
            else:
                return {"success": False, "error": "Failed to fetch injuries"}
    except Exception as e:
        logger.error(f"Error fetching injuries: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/nba/teams")
async def get_teams():
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://tank01-nba-live-in-game-real-time-statistics-nba.p.rapidapi.com/getNBATeams",
                params={"teamStats": "true"},
                headers={
                    "X-RapidAPI-Key": TANK01_API_KEY,
                    "X-RapidAPI-Host": "tank01-nba-live-in-game-real-time-statistics-nba.p.rapidapi.com"
                },
                timeout=10.0
            )
            
            if response.status_code == 200:
                data = response.json()
                return {"success": True, "data": data.get("body", [])}
            else:
                return {"success": False, "error": "Failed to fetch teams"}
    except Exception as e:
        logger.error(f"Error fetching teams: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/odds/player-props")
async def get_player_props():
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.the-odds-api.com/v4/sports/basketball_nba/odds",
                params={
                    "apiKey": ODDS_API_KEY,
                    "regions": "us",
                    "markets": "player_points,player_rebounds,player_assists",
                    "oddsFormat": "american"
                },
                timeout=15.0
            )
            
            if response.status_code == 200:
                data = response.json()
                return {"success": True, "data": data}
            else:
                logger.error(f"Odds API error: {response.status_code}")
                return {"success": False, "error": "Failed to fetch odds"}
    except Exception as e:
        logger.error(f"Error fetching odds: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/best-bets-demo", response_model=List[PlayerProp])
async def get_best_bets_demo():
    """Demo endpoint - no auth required for testing"""
    try:
        import random
        
        mock_players = [
            {"name": "LeBron James", "team": "LAL", "prop": "points"},
            {"name": "Stephen Curry", "team": "GSW", "prop": "points"},
            {"name": "Giannis Antetokounmpo", "team": "MIL", "prop": "points"},
            {"name": "Luka Doncic", "team": "DAL", "prop": "assists"},
            {"name": "Nikola Jokic", "team": "DEN", "prop": "rebounds"},
            {"name": "Kevin Durant", "team": "PHX", "prop": "points"},
            {"name": "Joel Embiid", "team": "PHI", "prop": "points"},
            {"name": "Jayson Tatum", "team": "BOS", "prop": "points"},
        ]
        
        best_bets = []
        for player in mock_players:
            pp_line = random.uniform(20, 35)
            market_avg = pp_line + random.uniform(-3, 5)
            line_diff = market_avg - pp_line
            is_demon = random.random() > 0.7
            hit_rate = random.uniform(0.35, 0.65) if is_demon else None
            
            best_bet_score = abs(line_diff) * 10 + (hit_rate * 20 if hit_rate else 0)
            
            matchup_grades = ["A+", "A", "B+", "B", "C"]
            matchup_grade = random.choice(matchup_grades)
            
            confidence = min(95, max(50, best_bet_score + random.uniform(-5, 5)))
            
            best_bets.append(PlayerProp(
                player_name=player["name"],
                team=player["team"],
                prop_type=player["prop"],
                prizepicks_line=round(pp_line, 1),
                market_avg=round(market_avg, 1),
                draftkings_line=round(market_avg + random.uniform(-1, 1), 1),
                fanduel_line=round(market_avg + random.uniform(-1, 1), 1),
                betmgm_line=round(market_avg + random.uniform(-1, 1), 1),
                caesars_line=round(market_avg + random.uniform(-1, 1), 1),
                is_demon=is_demon,
                demon_line=round(pp_line + random.uniform(3, 7), 1) if is_demon else None,
                hit_rate=round(hit_rate, 2) if hit_rate else None,
                best_bet_score=round(best_bet_score, 1),
                matchup_grade=matchup_grade,
                confidence=round(confidence, 1)
            ))
        
        best_bets.sort(key=lambda x: x.best_bet_score, reverse=True)
        return best_bets
    except Exception as e:
        logger.error(f"Error generating best bets: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/best-bets", response_model=List[PlayerProp])
async def get_best_bets(current_user = Depends(get_current_user)):
    try:
        import random
        
        mock_players = [
            {"name": "LeBron James", "team": "LAL", "prop": "points"},
            {"name": "Stephen Curry", "team": "GSW", "prop": "points"},
            {"name": "Giannis Antetokounmpo", "team": "MIL", "prop": "points"},
            {"name": "Luka Doncic", "team": "DAL", "prop": "assists"},
            {"name": "Nikola Jokic", "team": "DEN", "prop": "rebounds"},
            {"name": "Kevin Durant", "team": "PHX", "prop": "points"},
            {"name": "Joel Embiid", "team": "PHI", "prop": "points"},
            {"name": "Jayson Tatum", "team": "BOS", "prop": "points"},
        ]
        
        best_bets = []
        for player in mock_players:
            pp_line = random.uniform(20, 35)
            market_avg = pp_line + random.uniform(-3, 5)
            line_diff = market_avg - pp_line
            is_demon = random.random() > 0.7
            hit_rate = random.uniform(0.35, 0.65) if is_demon else None
            
            best_bet_score = abs(line_diff) * 10 + (hit_rate * 20 if hit_rate else 0)
            
            matchup_grades = ["A+", "A", "B+", "B", "C"]
            matchup_grade = random.choice(matchup_grades)
            
            confidence = min(95, max(50, best_bet_score + random.uniform(-5, 5)))
            
            best_bets.append(PlayerProp(
                player_name=player["name"],
                team=player["team"],
                prop_type=player["prop"],
                prizepicks_line=round(pp_line, 1),
                market_avg=round(market_avg, 1),
                draftkings_line=round(market_avg + random.uniform(-1, 1), 1),
                fanduel_line=round(market_avg + random.uniform(-1, 1), 1),
                betmgm_line=round(market_avg + random.uniform(-1, 1), 1),
                caesars_line=round(market_avg + random.uniform(-1, 1), 1),
                is_demon=is_demon,
                demon_line=round(pp_line + random.uniform(3, 7), 1) if is_demon else None,
                hit_rate=round(hit_rate, 2) if hit_rate else None,
                best_bet_score=round(best_bet_score, 1),
                matchup_grade=matchup_grade,
                confidence=round(confidence, 1)
            ))
        
        best_bets.sort(key=lambda x: x.best_bet_score, reverse=True)
        return best_bets
    except Exception as e:
        logger.error(f"Error generating best bets: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/")
async def root():
    return {"message": "NBA Best Bets API - Ready"}

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
"""
Auth Routes Module
==================
Handles authentication endpoints: signup, login, profile
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone
import logging
import os

from supabase import create_client, Client
from jose import JWTError, jwt

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# Security
security = HTTPBearer()

# Supabase client (initialized on module load)
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_ANON_KEY = os.environ.get('SUPABASE_ANON_KEY')
JWT_SECRET = os.environ.get('JWT_SECRET')

supabase: Optional[Client] = None
if SUPABASE_URL and SUPABASE_ANON_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    except Exception as e:
        logger.warning(f"Failed to initialize Supabase: {e}")


# Pydantic models
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
    created_at: Optional[str] = None


class UserResponse(BaseModel):
    user_id: str
    email: str
    profile: ProfileResponse
    access_token: str


async def verify_jwt(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Verify JWT token from Authorization header."""
    token = credentials.credentials
    try:
        if not JWT_SECRET:
            raise HTTPException(status_code=500, detail="JWT secret not configured")
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"], audience="authenticated")
        return payload
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")


async def get_current_user(token: dict = Depends(verify_jwt)):
    """Get current user from JWT token."""
    try:
        if not supabase:
            raise HTTPException(status_code=500, detail="Supabase not configured")
        user_id = token.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
        user = supabase.auth.get_user(token.get("access_token", ""))
        return user.user
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Authentication failed: {str(e)}")


@router.post("/signup", response_model=UserResponse)
async def signup(request: SignUpRequest):
    """Register a new user."""
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


@router.post("/login", response_model=UserResponse)
async def login(request: LoginRequest):
    """Login with email and password."""
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


# Profile route is separate since it uses a different prefix
profile_router = APIRouter(tags=["auth"])


@profile_router.get("/profile", response_model=ProfileResponse)
async def get_profile(current_user = Depends(get_current_user)):
    """Get current user profile."""
    profile_data = {
        "id": current_user.id,
        "email": current_user.email,
        "full_name": current_user.user_metadata.get("full_name", ""),
        "tier": current_user.user_metadata.get("tier", "free"),
        "created_at": current_user.created_at
    }
    return ProfileResponse(**profile_data)

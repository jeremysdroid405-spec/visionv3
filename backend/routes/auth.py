"""
Auth Routes Module
==================
Handles authentication endpoints: signup, login, profile
- Regular users: Supabase Auth
- Master admin: JWT-based local auth
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone, timedelta
import logging
import os

from supabase import create_client, Client
from jose import JWTError, jwt

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# Security
security = HTTPBearer()

# Supabase client
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_ANON_KEY = os.environ.get('SUPABASE_ANON_KEY')
JWT_SECRET = os.environ.get('JWT_SECRET', 'propvision-secret-key-2026')

# Master Admin Credentials (JWT-based, bypasses Supabase)
MASTER_EMAIL = os.environ.get('MASTER_EMAIL', 'admin@propvision.ai')
MASTER_PASSWORD = os.environ.get('MASTER_PASSWORD', 'PropVision2026!')

supabase: Optional[Client] = None
if SUPABASE_URL and SUPABASE_ANON_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
        logger.info("[AUTH] Supabase client initialized successfully")
    except Exception as e:
        logger.warning(f"[AUTH] Failed to initialize Supabase: {e}")


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
    is_master: bool = False
    created_at: Optional[str] = None


class UserResponse(BaseModel):
    user_id: str
    email: str
    profile: ProfileResponse
    access_token: str


def create_master_token(user_id: str, email: str) -> str:
    """Create JWT token for master admin."""
    expire = datetime.now(timezone.utc) + timedelta(days=30)
    payload = {
        "sub": user_id,
        "email": email,
        "is_master": True,
        "tier": "master",
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "aud": "authenticated"
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


async def verify_jwt(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Verify JWT token from Authorization header."""
    token = credentials.credentials
    try:
        # Try to decode as master JWT first
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"], audience="authenticated")
            if payload.get("is_master"):
                return payload
        except JWTError:
            pass
        
        # Try Supabase JWT
        if JWT_SECRET:
            payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"], audience="authenticated")
            return payload
        
        raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")


async def get_current_user(token: dict = Depends(verify_jwt)):
    """Get current user from JWT token."""
    return {
        "id": token.get("sub"),
        "email": token.get("email"),
        "is_master": token.get("is_master", False)
    }


@router.post("/signup", response_model=UserResponse)
async def signup(request: SignUpRequest):
    """Register a new user via Supabase."""
    try:
        if not supabase:
            raise HTTPException(status_code=500, detail="Authentication service not configured")
        
        # Sign up with Supabase
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
            raise HTTPException(status_code=400, detail="Signup failed - please check your email and password")
        
        # Check if email confirmation is required
        if auth_response.user and not auth_response.session:
            # Email confirmation required
            logger.info(f"[AUTH] User signed up, awaiting email confirmation: {request.email}")
            return UserResponse(
                user_id=auth_response.user.id,
                email=auth_response.user.email or request.email,
                profile=ProfileResponse(
                    id=auth_response.user.id,
                    email=auth_response.user.email or request.email,
                    full_name=request.full_name or "",
                    tier="free",
                    is_master=False,
                    created_at=datetime.now(timezone.utc).isoformat()
                ),
                access_token=""  # No token until email confirmed
            )
        
        profile_data = {
            "id": auth_response.user.id,
            "email": auth_response.user.email or request.email,
            "full_name": request.full_name or "",
            "tier": "free",
            "is_master": False,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        logger.info(f"[AUTH] New user signed up: {request.email}")
        
        return UserResponse(
            user_id=auth_response.user.id,
            email=auth_response.user.email or request.email,
            profile=ProfileResponse(**profile_data),
            access_token=auth_response.session.access_token if auth_response.session else ""
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[AUTH] Signup error: {str(e)}")
        error_msg = str(e)
        if "already registered" in error_msg.lower() or "already exists" in error_msg.lower():
            raise HTTPException(status_code=400, detail="Email already registered")
        raise HTTPException(status_code=400, detail=f"Signup failed: {error_msg}")


@router.post("/login", response_model=UserResponse)
async def login(request: LoginRequest):
    """Login with email and password. Supports master admin (JWT) and regular users (Supabase)."""
    try:
        email_lower = request.email.lower().strip()
        
        # Check for master admin login (JWT-based, bypasses Supabase)
        if email_lower == MASTER_EMAIL.lower() and request.password == MASTER_PASSWORD:
            user_id = "master-admin-001"
            access_token = create_master_token(user_id, email_lower)
            
            profile_data = {
                "id": user_id,
                "email": email_lower,
                "full_name": "Master Admin",
                "tier": "master",
                "is_master": True,
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            
            logger.info(f"[AUTH] Master admin logged in")
            
            return UserResponse(
                user_id=user_id,
                email=email_lower,
                profile=ProfileResponse(**profile_data),
                access_token=access_token
            )
        
        # Regular user login via Supabase
        if not supabase:
            raise HTTPException(status_code=500, detail="Authentication service not configured")
        
        auth_response = supabase.auth.sign_in_with_password({
            "email": request.email,
            "password": request.password
        })
        
        if not auth_response.user:
            raise HTTPException(status_code=401, detail="Invalid email or password")
        
        if not auth_response.session:
            raise HTTPException(status_code=401, detail="Please confirm your email before logging in")
        
        profile_data = {
            "id": auth_response.user.id,
            "email": auth_response.user.email or request.email,
            "full_name": auth_response.user.user_metadata.get("full_name", "") if auth_response.user.user_metadata else "",
            "tier": "free",
            "is_master": False,
            "created_at": auth_response.user.created_at if auth_response.user.created_at else datetime.now(timezone.utc).isoformat()
        }
        
        logger.info(f"[AUTH] User logged in: {request.email}")
        
        return UserResponse(
            user_id=auth_response.user.id,
            email=auth_response.user.email or request.email,
            profile=ProfileResponse(**profile_data),
            access_token=auth_response.session.access_token
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[AUTH] Login error: {str(e)}")
        error_msg = str(e).lower()
        if "invalid" in error_msg or "credentials" in error_msg:
            raise HTTPException(status_code=401, detail="Invalid email or password")
        if "confirm" in error_msg or "verify" in error_msg:
            raise HTTPException(status_code=401, detail="Please confirm your email before logging in")
        raise HTTPException(status_code=401, detail="Login failed")


# Profile route
profile_router = APIRouter(tags=["auth"])


@profile_router.get("/profile", response_model=ProfileResponse)
async def get_profile(current_user = Depends(get_current_user)):
    """Get current user profile."""
    try:
        if current_user.get("is_master"):
            return ProfileResponse(
                id=current_user["id"],
                email=current_user["email"],
                full_name="Master Admin",
                tier="master",
                is_master=True,
                created_at=datetime.now(timezone.utc).isoformat()
            )
        
        # For Supabase users, return basic profile from token
        return ProfileResponse(
            id=current_user.get("id", ""),
            email=current_user.get("email", ""),
            full_name="",
            tier="free",
            is_master=False,
            created_at=datetime.now(timezone.utc).isoformat()
        )
    except Exception as e:
        logger.error(f"[AUTH] Profile error: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get profile")

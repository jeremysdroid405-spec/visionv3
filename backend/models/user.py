"""
User Models
===========
Pydantic models for user authentication and profiles.
"""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field, EmailStr


class UserBase(BaseModel):
    """Base user fields."""
    email: EmailStr
    username: Optional[str] = None
    
    class Config:
        extra = "allow"


class UserCreate(UserBase):
    """Model for creating a new user."""
    password: str = Field(..., min_length=8)
    confirm_password: Optional[str] = None


class UserLogin(BaseModel):
    """Model for user login."""
    email: EmailStr
    password: str


class User(UserBase):
    """
    Full user model from users collection.
    """
    id: Optional[str] = Field(None, alias="_id")
    
    # Profile
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    
    # Status
    is_active: bool = True
    is_verified: bool = False
    is_admin: bool = False
    
    # Preferences
    favorite_teams: Optional[List[str]] = Field(default_factory=list)
    notification_settings: Optional[dict] = None
    
    # Timestamps
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    last_login: Optional[datetime] = None
    
    # Subscription (future)
    subscription_tier: str = "free"
    subscription_expires: Optional[datetime] = None
    
    class Config:
        extra = "allow"
        populate_by_name = True


class Token(BaseModel):
    """JWT token response."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(default=86400, description="Token expiry in seconds")
    
    # Optional user info in response
    user: Optional[User] = None


class TokenPayload(BaseModel):
    """JWT token payload."""
    sub: str  # User ID
    email: Optional[str] = None
    is_admin: bool = False
    exp: Optional[datetime] = None
    iat: Optional[datetime] = None

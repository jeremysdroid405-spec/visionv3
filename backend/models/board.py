"""
Board Models
============
Pydantic models for dg_cached_board - the frontend-ready player data.
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

from .player import GameLog, BaselineStats
from .prop import Prop


class BoardPlayer(BaseModel):
    """
    Player data optimized for frontend display.
    This is the main model stored in dg_cached_board.
    """
    # Identity
    player_name: str
    player_id: Optional[str] = None
    bdl_player_id: Optional[int] = None
    nba_com_id: Optional[int] = None
    espn_id: Optional[str] = None
    
    # Team
    team: Optional[str] = None
    team_name: Optional[str] = None
    team_logo_url: Optional[str] = None
    
    # Photos
    photo_url: Optional[str] = None
    headshot_url: Optional[str] = None
    photo_source: Optional[str] = None
    
    # Player info
    position: Optional[str] = None
    jersey_number: Optional[str] = None
    
    # Statistics
    baseline_stats: Optional[Dict[str, Any]] = None
    bdl_game_logs: Optional[List[Dict[str, Any]]] = Field(default_factory=list)
    season_avg: Optional[Dict[str, float]] = None
    games_played: int = 0
    
    # Flags
    volatility_flag: bool = False
    revenge_game: bool = False
    injury_status: Optional[str] = None
    
    # Usage/Context
    usage_bump_percent: int = 0
    usage_bump_reason: Optional[str] = None
    injured_teammates: Optional[List[str]] = Field(default_factory=list)
    ripple_detected: bool = False
    
    # Verification
    is_verified: bool = True
    is_mapper_matched: bool = False
    
    # Props
    props: Optional[List[Dict[str, Any]]] = Field(default_factory=list)
    
    class Config:
        extra = "allow"


class CachedBoard(BaseModel):
    """
    The full cached board response model.
    Represents the complete dg_cached_board collection state.
    """
    success: bool = True
    synced_at: Optional[datetime] = None
    players_count: int = 0
    total_props: int = 0
    players: List[BoardPlayer] = Field(default_factory=list)
    
    # Metadata
    version: str = "v3"
    last_odds_sync: Optional[datetime] = None
    last_stats_sync: Optional[datetime] = None
    
    class Config:
        extra = "allow"


class TierPicks(BaseModel):
    """
    Picks organized by tier (War Zone, Safe Haven, Front Lines).
    """
    tier: str = Field(..., description="war_zone, safe_haven, front_lines")
    display_name: str
    picks: List[Dict[str, Any]] = Field(default_factory=list)
    count: int = 0
    
    # Metadata
    generated_at: Optional[datetime] = None
    source: str = "dg_cached_board"
    
    class Config:
        extra = "allow"


class ParlayPick(BaseModel):
    """Individual pick for a parlay."""
    player_name: str
    stat_type: str
    line: float
    direction: str
    hit_rate: float
    season_avg: Optional[float] = None
    confidence: Optional[str] = None
    
    class Config:
        extra = "allow"


class ParlayBuilder(BaseModel):
    """
    Pre-built parlay combination from dg_parlay_builder.
    """
    parlay_id: Optional[str] = None
    legs: List[ParlayPick] = Field(default_factory=list)
    leg_count: int = 0
    
    # Analysis
    combined_confidence: Optional[float] = None
    expected_value: Optional[float] = None
    risk_level: str = "medium"
    
    # Metadata
    generated_at: Optional[datetime] = None
    tier: str = "standard"
    
    class Config:
        extra = "allow"

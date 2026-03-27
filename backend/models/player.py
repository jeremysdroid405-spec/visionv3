"""
Player Models
=============
Pydantic models for player data in nba_master_hub_2026 and dg_master_roster.
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class GameLog(BaseModel):
    """Individual game statistics for a player."""
    game_id: int
    date: str
    season: int = 2025
    pts: int = 0
    reb: int = 0
    ast: int = 0
    fg3m: int = 0
    stl: int = 0
    blk: int = 0
    turnover: int = 0
    min: str = "0"
    fgm: int = 0
    fga: int = 0
    fg_pct: float = 0.0
    fg3a: int = 0
    fg3_pct: float = 0.0
    ftm: int = 0
    fta: int = 0
    ft_pct: float = 0.0
    oreb: int = 0
    dreb: int = 0
    pf: int = 0
    plus_minus: int = 0
    opponent_team_id: Optional[int] = None
    home_game: bool = False
    
    class Config:
        extra = "allow"


class StatAverage(BaseModel):
    """Average statistics for a specific stat type."""
    l5_avg: Optional[float] = None
    l10_avg: Optional[float] = None
    season_avg: Optional[float] = None
    std_dev_l10: Optional[float] = None
    
    class Config:
        extra = "allow"


class BaselineStats(BaseModel):
    """Baseline statistics for a player (PTS, REB, AST, etc.)"""
    PTS: Optional[StatAverage] = None
    REB: Optional[StatAverage] = None
    AST: Optional[StatAverage] = None
    PRA: Optional[StatAverage] = None  # Points + Rebounds + Assists
    PR: Optional[StatAverage] = None   # Points + Rebounds
    PA: Optional[StatAverage] = None   # Points + Assists
    RA: Optional[StatAverage] = None   # Rebounds + Assists
    STL: Optional[StatAverage] = None
    BLK: Optional[StatAverage] = None
    TOV: Optional[StatAverage] = None
    FG3M: Optional[StatAverage] = Field(None, alias="3PM")
    
    synced_from: Optional[str] = None
    synced_at: Optional[datetime] = None
    
    class Config:
        extra = "allow"
        populate_by_name = True


class PlayerStats(BaseModel):
    """Player statistics from various sources."""
    season_avg: Optional[Dict[str, float]] = None
    l10_avg: Optional[Dict[str, float]] = None
    l5_avg: Optional[Dict[str, float]] = None
    games_played: int = 0
    
    class Config:
        extra = "allow"


class Player(BaseModel):
    """
    Player model representing a document in nba_master_hub_2026.
    This is the SSOT for player identity and enriched stats.
    """
    # Identity
    display_name: str
    bdl_id: Optional[int] = Field(None, description="BallDontLie API ID")
    nba_id: Optional[int] = Field(None, description="NBA.com ID")
    espn_id: Optional[str] = Field(None, description="ESPN ID for photos")
    
    # Team info
    team: Optional[str] = None
    team_abbreviation: Optional[str] = None
    team_id: Optional[int] = None
    
    # Physical attributes
    position: Optional[str] = None
    jersey_number: Optional[str] = None
    height: Optional[str] = None
    weight: Optional[str] = None
    
    # Statistics
    baseline_stats: Optional[BaselineStats] = None
    bdl_game_logs: Optional[List[GameLog]] = Field(default_factory=list)
    games_played: int = 0
    
    # Photo
    photo_url: Optional[str] = None
    headshot_url: Optional[str] = None
    
    # Metadata
    last_synced: Optional[datetime] = None
    is_active: bool = True
    
    class Config:
        extra = "allow"
        populate_by_name = True


class RosterPlayer(BaseModel):
    """
    Player model for dg_master_roster collection.
    Basic roster information for active players.
    """
    player_name: str
    team: str
    team_abbreviation: Optional[str] = None
    position: Optional[str] = None
    jersey_number: Optional[str] = None
    is_active: bool = True
    bdl_id: Optional[int] = None
    
    class Config:
        extra = "allow"

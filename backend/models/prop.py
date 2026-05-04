"""
Prop Models
===========
Pydantic models for betting props in nba_live_props /
nba_cached_board (and MLB equivalents).
(Legacy NBA names `dg_live_props` / `dg_cached_board` were dropped
2026-04-30.)
"""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


class HitRates(BaseModel):
    """Hit rate calculations for a prop."""
    l5_rate: Optional[float] = Field(None, description="Last 5 games hit rate (0-100)")
    l10_rate: Optional[float] = Field(None, description="Last 10 games hit rate (0-100)")
    l5_hit_count: Optional[int] = Field(None, description="Hits in last 5 games")
    l10_hit_count: Optional[int] = Field(None, description="Hits in last 10 games")
    l5_avg: Optional[float] = Field(None, description="Last 5 games average")
    l10_avg: Optional[float] = Field(None, description="Last 10 games average")
    season_avg: Optional[float] = Field(None, description="Season average")
    
    class Config:
        extra = "allow"


class Prop(BaseModel):
    """
    Betting prop model for dg_live_props collection.
    Represents a single betting line for a player.
    """
    # Event info
    event_id: str
    home_team: str
    away_team: str
    home_team_full: Optional[str] = None
    away_team_full: Optional[str] = None
    commence_time: datetime
    game_date: Optional[str] = None
    
    # Player info
    player_name: str
    player_name_raw: Optional[str] = None
    
    # Prop details
    market: str = Field(..., description="e.g., player_points, player_assists")
    stat_type: Optional[str] = Field(None, description="Extracted stat type: PTS, AST, etc.")
    stat_type_extracted: Optional[str] = None
    direction: str = Field(..., description="Over or Under")
    line: float = Field(..., description="The betting line")
    price: Optional[int] = Field(None, description="Odds price")
    
    # Classification
    is_alternate_market: bool = False
    is_demon: bool = Field(False, description="High-risk/high-reward pick")
    is_goblin: bool = Field(False, description="Safe/conservative pick")
    prop_type: str = Field("standard", description="demon, goblin, or standard")
    tier_label: Optional[str] = None
    tier_source: Optional[str] = None
    
    # Stats
    hit_rates: Optional[HitRates] = None
    h5_rate: Optional[float] = None
    h10_rate: Optional[float] = None
    h5_hit_rate: Optional[float] = None
    h10_hit_rate: Optional[float] = None
    l5_avg: Optional[float] = None
    l10_avg: Optional[float] = None
    season_avg: Optional[float] = None
    l5_hit_count: Optional[int] = None
    l10_hit_count: Optional[int] = None
    
    # Analysis
    is_anomaly: bool = False
    is_demon_anomaly: bool = False
    is_goblin_anomaly: bool = False
    season_margin: Optional[float] = None
    margin: Optional[float] = None
    anchor_line: Optional[float] = None
    anchor_source: Optional[str] = None
    
    # Source info
    bookmaker: str = "prizepicks"
    last_update: Optional[datetime] = None
    fetched_at: Optional[datetime] = None
    synced_at: Optional[datetime] = None
    popularity_order: Optional[int] = None
    
    # Composite key for deduplication
    _composite_key: Optional[str] = None
    
    class Config:
        extra = "allow"
        populate_by_name = True


class PropCreate(BaseModel):
    """Model for creating a new prop."""
    event_id: str
    home_team: str
    away_team: str
    commence_time: datetime
    player_name: str
    market: str
    direction: str
    line: float
    price: Optional[int] = None
    bookmaker: str = "prizepicks"
    is_alternate_market: bool = False
    
    class Config:
        extra = "allow"

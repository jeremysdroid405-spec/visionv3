"""
Sync Models
===========
Pydantic models for sync status tracking in dg_sync_status and dg_sync_log.
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from enum import Enum


class SyncType(str, Enum):
    """Types of sync operations."""
    FULL = "full"
    ROSTER = "roster"
    PROPS = "props"
    STATS = "stats"
    GAME_LOGS = "game_logs"
    BDL = "bdl"
    ODDS = "odds"
    PHOTOS = "photos"
    DVP = "dvp"
    BOARD = "board"


class SyncStatus(BaseModel):
    """
    Current sync status from dg_sync_status collection.
    Tracks the state of various sync processes.
    """
    # Status flags
    is_syncing: bool = False
    last_sync_type: Optional[str] = None
    last_sync_status: Optional[str] = Field(None, description="success, failed, partial")
    
    # Timestamps
    last_sync_started: Optional[datetime] = None
    last_sync_completed: Optional[datetime] = None
    last_successful_sync: Optional[datetime] = None
    
    # Counts
    last_players_synced: Optional[int] = None
    last_props_synced: Optional[int] = None
    last_errors: Optional[int] = None
    
    # Component status
    roster_synced_at: Optional[datetime] = None
    props_synced_at: Optional[datetime] = None
    stats_synced_at: Optional[datetime] = None
    photos_synced_at: Optional[datetime] = None
    dvp_synced_at: Optional[datetime] = None
    
    # Error tracking
    error_message: Optional[str] = None
    consecutive_failures: int = 0
    
    class Config:
        extra = "allow"


class SyncLog(BaseModel):
    """
    Sync log entry for dg_sync_log collection.
    Records individual sync operations.
    """
    # Operation info
    sync_type: str
    sync_id: Optional[str] = None
    operation: Optional[str] = None
    
    # Timing
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    
    # Results
    status: str = Field("started", description="started, running, success, failed, partial")
    success: bool = False
    
    # Counts
    records_processed: int = 0
    records_created: int = 0
    records_updated: int = 0
    records_failed: int = 0
    
    # Error tracking
    errors: Optional[List[str]] = Field(default_factory=list)
    error_count: int = 0
    last_error: Optional[str] = None
    
    # Metadata
    triggered_by: str = Field("system", description="system, manual, schedule")
    metadata: Optional[Dict[str, Any]] = None
    
    class Config:
        extra = "allow"


class SyncResult(BaseModel):
    """Result object returned after a sync operation."""
    success: bool
    sync_type: str
    message: str
    
    # Counts
    total_processed: int = 0
    created: int = 0
    updated: int = 0
    failed: int = 0
    skipped: int = 0
    
    # Timing
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    
    # Errors
    errors: Optional[List[str]] = None
    
    class Config:
        extra = "allow"

"""
Collection Constants
====================
Centralized constants for MongoDB collection names.

This module provides a single source of truth for collection names
to prevent hardcoding and ensure consistency across the codebase.

Usage:
    from db.collections import Collections
    
    # In service
    self.board = db[Collections.CACHED_BOARD]
    
    # Or using helper
    from db.collections import get_collection
    board = get_collection(db, Collections.CACHED_BOARD)
"""

from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from motor.motor_asyncio import AsyncIOMotorDatabase, AsyncIOMotorCollection


class Collections(str, Enum):
    """
    MongoDB collection names.
    
    Organized by category:
    - AUTHORITATIVE: Source of truth collections
    - DERIVED: Rebuilt on sync
    - CACHE: Ephemeral cache collections
    - STATUS: Sync and status tracking
    - MAPPING: ID mapping collections
    """
    
    # =========================================================================
    # AUTHORITATIVE COLLECTIONS (Source of Truth)
    # =========================================================================
    
    # Player master data - SSOT for player identity, stats, game logs
    MASTER_HUB = "nba_master_hub_2026"
    
    # Active roster - SSOT for player-team mapping
    MASTER_ROSTER = "dg_master_roster"
    
    # Player photos - headshot URLs
    PLAYER_PHOTOS = "player_photos"
    
    # Defense vs Position rankings
    DVP_RANKINGS = "dvp_rankings"
    
    # User accounts
    USERS = "users"
    
    # =========================================================================
    # DERIVED COLLECTIONS (Rebuilt on Sync)
    # =========================================================================
    
    # Frontend-ready player/props data
    CACHED_BOARD = "dg_cached_board"
    
    # Live betting props from Odds API
    LIVE_PROPS = "dg_live_props"
    
    # Pre-built parlay combinations
    PARLAY_BUILDER = "dg_parlay_builder"
    
    # Safe parlay recommendations
    GOBLIN_RECON = "dg_goblin_recon"
    
    # War Zone (demon) picks
    RADAR_PICKS = "dg_radar_picks"
    
    # Safe Haven (goblin) picks
    GOBLIN_VAULT = "dg_goblin_vault"
    
    # Front Lines (mixed tier) picks
    FRONT_LINES = "dg_front_lines"
    
    # AI context analysis results
    CONTEXT_ENGINE = "nba_context_engine"
    
    # Career milestones for badges
    CAREER_STATS = "nba_career_stats"
    
    # Daily player insights
    DAILY_INSIGHTS = "dg_daily_insights"
    
    # Trending players
    TRENDING = "dg_trending"
    
    # =========================================================================
    # CACHE COLLECTIONS (Ephemeral)
    # =========================================================================
    
    # Raw Odds API responses
    ODDS_CACHE = "dg_odds_cache"
    
    # NBA events/games
    EVENTS_CACHE = "dg_events_cache"
    
    # Player stats cache
    STATS_CACHE = "dg_stats_cache"
    
    # Static UI shell
    STATIC_SHELL = "dg_static_shell"
    
    # News ticker
    TICKER_CACHE = "ticker_cache"
    
    # Spotrac contract data
    CONTRACTS_CACHE = "spotrac_contracts_cache"
    
    # Breaking news
    BREAKING_NEWS = "dg_breaking_news"
    
    # =========================================================================
    # STATUS/TRACKING COLLECTIONS
    # =========================================================================
    
    # Current sync state
    SYNC_STATUS = "dg_sync_status"
    
    # Sync history log
    SYNC_LOG = "dg_sync_log"
    
    # Flagged/suspended players
    FLAGGED_PLAYERS = "dg_flagged_players"
    
    # Games that have started (locked)
    LOCKED_GAMES = "dg_locked_games"
    
    # =========================================================================
    # MAPPING COLLECTIONS
    # =========================================================================
    
    # BallDontLie ID mappings
    BDL_PLAYER_MAPPING = "bdl_player_mapping"
    
    # Odds API name normalization
    ODDS_API_MAPPING = "odds_api_mapping_master"
    
    # =========================================================================
    # INJURY COLLECTIONS
    # =========================================================================
    
    # BDL injuries (primary source)
    BDL_INJURIES = "bdl_injuries"
    
    # ESPN injuries (secondary, often empty)
    DG_INJURIES = "dg_injuries"


def get_collection(db: "AsyncIOMotorDatabase", collection: Collections) -> "AsyncIOMotorCollection":
    """
    Get a collection by its enum constant.
    
    Args:
        db: AsyncIOMotorDatabase instance
        collection: Collections enum value
        
    Returns:
        AsyncIOMotorCollection
        
    Example:
        board = get_collection(db, Collections.CACHED_BOARD)
    """
    return db[collection.value]


# Convenience aliases for common collections
MASTER_HUB = Collections.MASTER_HUB.value
MASTER_ROSTER = Collections.MASTER_ROSTER.value
CACHED_BOARD = Collections.CACHED_BOARD.value
LIVE_PROPS = Collections.LIVE_PROPS.value
PLAYER_PHOTOS = Collections.PLAYER_PHOTOS.value
SYNC_STATUS = Collections.SYNC_STATUS.value
SYNC_LOG = Collections.SYNC_LOG.value
BDL_PLAYER_MAPPING = Collections.BDL_PLAYER_MAPPING.value
ODDS_API_MAPPING = Collections.ODDS_API_MAPPING.value


# Collection categories for documentation
AUTHORITATIVE_COLLECTIONS = [
    Collections.MASTER_HUB,
    Collections.MASTER_ROSTER,
    Collections.PLAYER_PHOTOS,
    Collections.DVP_RANKINGS,
    Collections.USERS,
]

DERIVED_COLLECTIONS = [
    Collections.CACHED_BOARD,
    Collections.LIVE_PROPS,
    Collections.PARLAY_BUILDER,
    Collections.GOBLIN_RECON,
    Collections.RADAR_PICKS,
    Collections.GOBLIN_VAULT,
    Collections.FRONT_LINES,
    Collections.CONTEXT_ENGINE,
    Collections.CAREER_STATS,
    Collections.DAILY_INSIGHTS,
    Collections.TRENDING,
]

CACHE_COLLECTIONS = [
    Collections.ODDS_CACHE,
    Collections.EVENTS_CACHE,
    Collections.STATS_CACHE,
    Collections.STATIC_SHELL,
    Collections.TICKER_CACHE,
    Collections.CONTRACTS_CACHE,
    Collections.BREAKING_NEWS,
]

STATUS_COLLECTIONS = [
    Collections.SYNC_STATUS,
    Collections.SYNC_LOG,
    Collections.FLAGGED_PLAYERS,
    Collections.LOCKED_GAMES,
]

MAPPING_COLLECTIONS = [
    Collections.BDL_PLAYER_MAPPING,
    Collections.ODDS_API_MAPPING,
]

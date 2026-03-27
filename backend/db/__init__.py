"""
Database Module
===============
Centralized database access and collection management.
"""

from .collections import (
    Collections,
    get_collection,
    MASTER_HUB,
    MASTER_ROSTER,
    CACHED_BOARD,
    LIVE_PROPS,
    PLAYER_PHOTOS,
    SYNC_STATUS,
    SYNC_LOG,
    BDL_PLAYER_MAPPING,
    ODDS_API_MAPPING,
    AUTHORITATIVE_COLLECTIONS,
    DERIVED_COLLECTIONS,
    CACHE_COLLECTIONS,
    STATUS_COLLECTIONS,
    MAPPING_COLLECTIONS,
)

__all__ = [
    'Collections',
    'get_collection',
    'MASTER_HUB',
    'MASTER_ROSTER', 
    'CACHED_BOARD',
    'LIVE_PROPS',
    'PLAYER_PHOTOS',
    'SYNC_STATUS',
    'SYNC_LOG',
    'BDL_PLAYER_MAPPING',
    'ODDS_API_MAPPING',
    'AUTHORITATIVE_COLLECTIONS',
    'DERIVED_COLLECTIONS',
    'CACHE_COLLECTIONS',
    'STATUS_COLLECTIONS',
    'MAPPING_COLLECTIONS',
]

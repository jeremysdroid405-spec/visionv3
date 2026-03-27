"""
Picks Service Module
====================
Modular services for picks, stats, and board operations.

This module contains extracted logic from picks_getter_service.py
organized into focused, maintainable services.

Services:
- game_utils: Utility functions for game state and player names
- hit_rate_service: Hit rate calculations
- photo_service: Photo URL management
- player_stats_resolver: Player stats lookup
- board_formatter: Board data formatting
"""

from .game_utils import (
    normalize_name,
    get_game_status,
    did_play,
    filter_played_games,
    get_opponent_from_game,
    clean_object_ids,
    extract_stat_type,
    normalize_stat_key,
)

from .hit_rate_service import HitRateCalculator

from .photo_service import PhotoService

from .player_stats_resolver import PlayerStatsResolver

from .board_formatter import BoardFormatter


__all__ = [
    # Utilities
    'normalize_name',
    'get_game_status',
    'did_play',
    'filter_played_games',
    'get_opponent_from_game',
    'clean_object_ids',
    'extract_stat_type',
    'normalize_stat_key',
    
    # Services
    'HitRateCalculator',
    'PhotoService',
    'PlayerStatsResolver',
    'BoardFormatter',
]

"""
Database Configuration for Multi-Sport Isolation
=================================================
Centralized configuration for sport-specific database collections.

Each sport has isolated collections with prefixes:
- NBA: nba_ prefix (or legacy names for backwards compatibility)
- MLB: mlb_ prefix

Usage:
    from config.db_config import get_collection_name, get_collection
    
    # Get collection name string
    collection_name = get_collection_name("cached_board", "mlb")  # -> "mlb_cached_board"
    
    # Get collection object directly
    collection = get_collection(db, "cached_board", "mlb")  # -> db.mlb_cached_board
"""

from typing import Literal

# Supported sports
SportType = Literal["nba", "mlb"]
SUPPORTED_SPORTS = ["nba", "mlb"]
DEFAULT_SPORT = "nba"

# =============================================================================
# COLLECTION PREFIXES
# =============================================================================
SPORT_PREFIXES = {
    "nba": "nba_",
    "mlb": "mlb_"
}

# =============================================================================
# BASE COLLECTION NAMES (without sport prefix)
# =============================================================================
# These are the logical collection names used throughout the app
BASE_COLLECTIONS = {
    # Core data collections
    "master_hub": "master_hub_2026",      # Player stats repository (BDL data)
    "cached_board": "cached_board",        # Enriched players with props
    "live_props": "live_props",            # Raw imported props from Odds API
    
    # Ferrari tier collections
    "safe_haven": "ferrari_safe_haven",    # Safe Haven tier picks
    "front_lines": "ferrari_front_lines",  # Front Lines tier picks
    "war_zone": "ferrari_war_zone",        # War Zone tier picks
    "discarded": "ferrari_discarded",      # Discarded/killed props
    "scored": "ferrari_scored",            # All scored props before tier split
    
    # Analysis collections
    "oracle_analyzed": "oracle_apex_analyzed",  # Vegas Killer model outputs
    
    # Supporting collections
    "injuries": "injuries",                # Injury reports
    "referee_stats": "referee_stats",      # Referee statistics
    "referee_assignments": "referee_assignments",  # Daily ref assignments
    "standings": "standings",              # Team standings
    "momentum_cache": "momentum_cache",    # Defensive momentum data
}

# =============================================================================
# LEGACY NBA COLLECTION NAMES (backwards compatibility)
# =============================================================================
# NBA uses some legacy names without the nba_ prefix for backwards compatibility
# with existing data. New sports (MLB) use consistent prefixing.
NBA_LEGACY_NAMES = {
    "cached_board": "nba_cached_board",
    "live_props": "nba_live_props",
    "injuries": "dg_injuries",
    "safe_haven": "ferrari_safe_haven",
    "front_lines": "ferrari_front_lines",
    "war_zone": "ferrari_war_zone",
    "discarded": "ferrari_discarded",
    "scored": "ferrari_scored",
    "oracle_analyzed": "oracle_apex_analyzed",
    "master_hub": "nba_master_hub_2026",
}


def get_collection_name(base_name: str, sport: str = DEFAULT_SPORT) -> str:
    """
    Get the sport-specific collection name.
    
    Args:
        base_name: Logical collection name (e.g., 'cached_board', 'safe_haven')
        sport: Target sport ('nba' or 'mlb')
    
    Returns:
        Full collection name with sport prefix
        
    Examples:
        get_collection_name('cached_board', 'nba')  -> 'dg_cached_board'
        get_collection_name('cached_board', 'mlb')  -> 'mlb_cached_board'
        get_collection_name('safe_haven', 'nba')    -> 'ferrari_safe_haven'
        get_collection_name('safe_haven', 'mlb')    -> 'mlb_ferrari_safe_haven'
    """
    sport = (sport or DEFAULT_SPORT).lower()
    
    if sport not in SUPPORTED_SPORTS:
        raise ValueError(f"Unsupported sport: {sport}. Must be one of {SUPPORTED_SPORTS}")
    
    # NBA uses legacy names for backwards compatibility
    if sport == "nba":
        if base_name in NBA_LEGACY_NAMES:
            return NBA_LEGACY_NAMES[base_name]
        # Fallback to prefixed name if not in legacy map
        return f"nba_{base_name}"
    
    # MLB and other sports use consistent prefixing
    prefix = SPORT_PREFIXES.get(sport, f"{sport}_")
    
    # For MLB tier collections, use simple names (mlb_safe_haven, etc.)
    # without the ferrari_ middle part
    if sport == "mlb" and base_name in ["safe_haven", "front_lines", "war_zone"]:
        return f"mlb_{base_name}"
    
    # Get the base collection name from config, or use the provided name
    actual_base = BASE_COLLECTIONS.get(base_name, base_name)
    
    return f"{prefix}{actual_base}"


def get_collection(db, base_name: str, sport: str = DEFAULT_SPORT):
    """
    Get a MongoDB collection object for the specified sport.
    
    Args:
        db: MongoDB database instance
        base_name: Logical collection name (e.g., 'cached_board', 'safe_haven')
        sport: Target sport ('nba' or 'mlb')
    
    Returns:
        MongoDB collection object
        
    Example:
        collection = get_collection(db, 'cached_board', 'mlb')
        await collection.find({}).to_list(100)
    """
    collection_name = get_collection_name(base_name, sport)
    return db[collection_name]


def get_all_collection_names(sport: str = DEFAULT_SPORT) -> dict:
    """
    Get all collection names for a specific sport.
    
    Args:
        sport: Target sport ('nba' or 'mlb')
    
    Returns:
        Dictionary mapping logical names to actual collection names
    """
    sport = (sport or DEFAULT_SPORT).lower()
    
    result = {}
    for base_name in BASE_COLLECTIONS.keys():
        result[base_name] = get_collection_name(base_name, sport)
    
    return result


def validate_sport(sport: str) -> str:
    """
    Validate and normalize sport parameter.
    
    Args:
        sport: Sport string to validate
    
    Returns:
        Normalized lowercase sport string
        
    Raises:
        ValueError if sport is not supported
    """
    sport = (sport or DEFAULT_SPORT).lower()
    
    if sport not in SUPPORTED_SPORTS:
        raise ValueError(f"Unsupported sport: {sport}. Must be one of {SUPPORTED_SPORTS}")
    
    return sport


def is_collection_for_sport(collection_name: str, sport: str) -> bool:
    """
    Check if a collection belongs to a specific sport.
    Used for validating sport isolation during sync operations.
    
    Args:
        collection_name: Actual MongoDB collection name
        sport: Sport to check against
    
    Returns:
        True if collection belongs to the sport, False otherwise
    """
    sport = (sport or DEFAULT_SPORT).lower()
    
    # Get all valid collection names for the sport
    valid_collections = set(get_all_collection_names(sport).values())
    
    return collection_name in valid_collections


# =============================================================================
# SPORT-SPECIFIC CONFIGURATION
# =============================================================================
SPORT_CONFIG = {
    "nba": {
        "name": "NBA",
        "full_name": "National Basketball Association",
        "stats_source": "balldontlie",  # BDL API for stats
        "odds_source": "odds_api",       # The Odds API for props
        "stat_types": ["PTS", "REB", "AST", "STL", "BLK", "3PM", "PRA", "PR", "PA", "RA"],
        "season": "2025-2026",
    },
    "mlb": {
        "name": "MLB",
        "full_name": "Major League Baseball",
        "stats_source": "tbd",           # TBD - need MLB stats API
        "odds_source": "odds_api",       # The Odds API for props
        "stat_types": ["Strikeouts", "Total Bases", "Hits", "RBIs", "Runs", "HRs", "Walks"],
        "season": "2026",
    }
}


def get_sport_config(sport: str = DEFAULT_SPORT) -> dict:
    """
    Get configuration for a specific sport.
    
    Args:
        sport: Target sport ('nba' or 'mlb')
    
    Returns:
        Sport configuration dictionary
    """
    sport = validate_sport(sport)
    return SPORT_CONFIG.get(sport, SPORT_CONFIG[DEFAULT_SPORT])

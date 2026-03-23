"""
DvP (Defense vs Position) Service
==================================
Dynamic, real-time DvP data fetching with intelligent fallback.

Features:
- Live data from BallDontLie API (team_season_averages/general?type=opponent)
- MongoDB storage with 24-hour refresh cycle
- Position-specific defensive rankings with matchup multipliers
- Automatic fallback to hardcoded data
- X-Data-Source and dvp_type metadata headers
"""
import httpx
import asyncio
import logging
import os
from typing import Optional, Dict, Any, Tuple, List
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from config.settings import DVP_RANKINGS, STAT_TYPE_MAP, TEAM_ABBREV_MAP

logger = logging.getLogger(__name__)

# ==================== CONFIGURATION ====================

# BallDontLie API v1 endpoint for team season averages
BDL_API_BASE = "https://api.balldontlie.io/nba/v1"
BDL_TEAM_SEASON_AVERAGES = f"{BDL_API_BASE}/team_season_averages/general"

# Cache settings
DVP_CACHE_TTL_HOURS = 24
DVP_REFRESH_HOUR_EST = 8  # 8:00 AM EST for morning refresh
DVP_REFRESH_HOUR_UTC = 13  # 8:00 AM EST = 13:00 UTC (standard time)

# MongoDB collection name for DvP data
DVP_COLLECTION = "dvp_rankings"

# Position to stat mapping for matchup calculations
POSITION_STAT_MAP = {
    "C": ["REB", "BLK"],  # Centers affect rebounds and blocks
    "PF": ["REB", "PTS"],  # Power Forwards affect rebounds and points
    "SF": ["PTS", "3PM"],  # Small Forwards affect points and 3PM
    "SG": ["PTS", "3PM", "AST"],  # Shooting Guards affect points, 3s, assists
    "PG": ["AST", "PTS", "STL"],  # Point Guards affect assists, points, steals
    "G": ["AST", "PTS", "3PM"],  # Generic Guard
    "F": ["REB", "PTS"],  # Generic Forward
    "G-F": ["PTS", "REB", "AST"],  # Combo Guard-Forward
    "F-C": ["REB", "BLK", "PTS"],  # Combo Forward-Center
}

# BDL Team ID to Abbreviation mapping
BDL_TEAM_ID_MAP = {
    1: "ATL", 2: "BOS", 3: "BKN", 4: "CHA", 5: "CHI",
    6: "CLE", 7: "DAL", 8: "DEN", 9: "DET", 10: "GSW",
    11: "HOU", 12: "IND", 13: "LAC", 14: "LAL", 15: "MEM",
    16: "MIA", 17: "MIL", 18: "MIN", 19: "NOP", 20: "NYK",
    21: "OKC", 22: "ORL", 23: "PHI", 24: "PHX", 25: "POR",
    26: "SAC", 27: "SAS", 28: "TOR", 29: "UTA", 30: "WAS"
}


# ==================== DATA STRUCTURES ====================

@dataclass
class DvPCacheEntry:
    """Cached DvP data with metadata."""
    rankings: Dict[str, Dict[str, int]]
    source: str
    fetched_at: datetime
    season: str
    expires_at: datetime
    
    @property
    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) > self.expires_at
    
    @property
    def age_hours(self) -> float:
        delta = datetime.now(timezone.utc) - self.fetched_at
        return delta.total_seconds() / 3600


class DvPDataSource:
    """Data source type constants."""
    STATIC_FALLBACK = "static-fallback"
    DYNAMIC_LIVE = "dynamic_live"
    CACHED = "cached"
    BDL_API = "bdl_api"
    MONGODB = "mongodb"
    MAINTENANCE = "maintenance"


# ==================== GLOBAL STATE ====================

_dvp_cache: Optional[DvPCacheEntry] = None
_fetch_lock = asyncio.Lock()
_last_fetch_attempt: Optional[datetime] = None
_fetch_failures: int = 0
_db_ref = None  # MongoDB reference (set during startup)


def set_db_reference(db):
    """Set MongoDB reference for persistent storage."""
    global _db_ref
    _db_ref = db
    logger.info("[DVP] MongoDB reference set for persistent storage")


async def initialize_dvp_cache():
    """
    Initialize DvP cache from MongoDB on startup.
    This ensures we have live data immediately instead of falling back to static.
    Should be called during server startup after DB connection is established.
    """
    global _dvp_cache, _db_ref
    
    if _db_ref is None:
        logger.warning("[DVP] Cannot initialize cache - no DB reference")
        return False
    
    try:
        # Look up by type (matches how _save_to_mongodb stores it)
        cached_data = await _db_ref[DVP_COLLECTION].find_one(
            {"type": "dvp_rankings"},
            {"_id": 0}
        )
        
        if cached_data and cached_data.get("rankings"):
            # Check if data is still valid (less than 24 hours old)
            # Try fetched_at first (new format), then synced_at (old format)
            fetched_at = cached_data.get("fetched_at") or cached_data.get("synced_at")
            
            if fetched_at:
                # Handle both datetime objects and strings
                if isinstance(fetched_at, str):
                    synced_at = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
                else:
                    synced_at = fetched_at.replace(tzinfo=timezone.utc) if fetched_at.tzinfo is None else fetched_at
                
                age_hours = (datetime.now(timezone.utc) - synced_at).total_seconds() / 3600
                
                if age_hours < DVP_CACHE_TTL_HOURS:
                    _dvp_cache = DvPCacheEntry(
                        rankings=cached_data["rankings"],
                        source=DvPDataSource.MONGODB,
                        fetched_at=synced_at,
                        season=cached_data.get("season", get_current_season_str()),
                        expires_at=synced_at + timedelta(hours=DVP_CACHE_TTL_HOURS)
                    )
                    teams_count = len(next(iter(_dvp_cache.rankings.values()), {}))
                    logger.info(f"[DVP] Cache initialized from MongoDB: {teams_count} teams, age {age_hours:.1f}h")
                    return True
                else:
                    logger.info(f"[DVP] MongoDB data expired ({age_hours:.1f}h old), will fetch fresh")
            else:
                # No timestamp - data exists but we can't verify age, use it anyway
                _dvp_cache = DvPCacheEntry(
                    rankings=cached_data["rankings"],
                    source=DvPDataSource.MONGODB,
                    fetched_at=datetime.now(timezone.utc),
                    season=cached_data.get("season", get_current_season_str()),
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=DVP_CACHE_TTL_HOURS)
                )
                teams_count = len(next(iter(_dvp_cache.rankings.values()), {}))
                logger.info(f"[DVP] Cache initialized from MongoDB (no timestamp): {teams_count} teams")
                return True
            
        logger.info("[DVP] No valid MongoDB cache found, will fetch on first request")
        return False
        
    except Exception as e:
        logger.error(f"[DVP] Cache initialization error: {e}")
        return False


# ==================== CORE FUNCTIONS ====================

def get_current_season() -> int:
    """Get current NBA season year (e.g., 2024 for 2024-25 season)."""
    now = datetime.now()
    # NBA season starts in October
    if now.month >= 10:
        return now.year
    else:
        return now.year - 1


def get_current_season_str() -> str:
    """Get current NBA season string (e.g., '2024-25')."""
    year = get_current_season()
    return f"{year}-{str(year + 1)[-2:]}"


def get_data_source() -> str:
    """Get the current data source type."""
    if _dvp_cache and not _dvp_cache.is_expired:
        return _dvp_cache.source
    return DvPDataSource.STATIC_FALLBACK


def get_data_source_header() -> Dict[str, str]:
    """Get headers indicating the data source."""
    headers = {
        "X-Data-Source": get_data_source(),
        "X-DVP-Last-Update": _dvp_cache.fetched_at.isoformat() if _dvp_cache else "never",
        "X-DVP-Cache-Age-Hours": f"{_dvp_cache.age_hours:.1f}" if _dvp_cache else "N/A"
    }
    
    if _dvp_cache and _dvp_cache.source == DvPDataSource.DYNAMIC_LIVE:
        headers["dvp_type"] = "dynamic_live"
    else:
        headers["dvp_type"] = "static_fallback"
    
    return headers


def _get_bdl_api_key() -> Optional[str]:
    """Get BallDontLie API key from environment."""
    key = os.environ.get("BALLDONTLIE_API_KEY") or os.environ.get("BDL_API_KEY")
    if not key:
        logger.warning("[DVP] No BallDontLie API key found in environment (BALLDONTLIE_API_KEY or BDL_API_KEY)")
    return key


async def _fetch_bdl_defensive_stats() -> Optional[Dict[str, Dict[str, float]]]:
    """
    Fetch defensive stats (opponent stats) from BallDontLie API.
    
    Endpoint: GET /nba/v1/team_season_averages/general?type=opponent
    
    The BDL API uses cursor-based pagination. We need to loop through all pages
    using the `next_cursor` from the `meta` object to get all 30 NBA teams.
    
    Returns raw opponent stats per team: pts_allowed, ast_allowed, reb_allowed, etc.
    """
    api_key = _get_bdl_api_key()
    if not api_key:
        logger.warning("[DVP] Cannot fetch from BDL API - no API key")
        return None
    
    season = get_current_season()
    
    try:
        headers = {
            "Authorization": api_key  # BDL uses API key directly, not Bearer
        }
        
        logger.info(f"[DVP] Fetching BallDontLie opponent stats for season {season}")
        
        all_teams_data = []
        cursor = None
        page_count = 0
        max_pages = 5  # Safety limit to prevent infinite loops
        
        async with httpx.AsyncClient(timeout=20.0) as client:
            while page_count < max_pages:
                page_count += 1
                
                params = {
                    "season": season,
                    "season_type": "regular",
                    "type": "opponent",  # Get opponent stats = defensive stats
                    "per_page": 100  # Request max per page
                }
                
                # Add cursor for subsequent pages
                if cursor:
                    params["cursor"] = cursor
                
                logger.info(f"[DVP] Fetching page {page_count} (cursor: {cursor})")
                
                response = await client.get(
                    BDL_TEAM_SEASON_AVERAGES,
                    params=params,
                    headers=headers
                )
                
                if response.status_code == 200:
                    data = response.json()
                    page_data = data.get("data", [])
                    all_teams_data.extend(page_data)
                    
                    logger.info(f"[DVP] Page {page_count}: Got {len(page_data)} teams (total: {len(all_teams_data)})")
                    
                    # Check for next page
                    meta = data.get("meta", {})
                    next_cursor = meta.get("next_cursor")
                    
                    if next_cursor and len(page_data) > 0:
                        cursor = next_cursor
                        logger.info(f"[DVP] More data available, next_cursor: {next_cursor}")
                    else:
                        # No more pages
                        logger.info(f"[DVP] Pagination complete. Total teams fetched: {len(all_teams_data)}")
                        break
                        
                elif response.status_code == 401:
                    logger.error("[DVP] BallDontLie API key unauthorized - check tier (GOAT required for team_season_averages)")
                    return None
                elif response.status_code == 429:
                    logger.warning("[DVP] BallDontLie rate limited - backing off")
                    return None
                else:
                    logger.warning(f"[DVP] BallDontLie returned status {response.status_code}: {response.text[:200]}")
                    return None
        
        # Parse the combined data from all pages
        if all_teams_data:
            return _parse_bdl_opponent_stats({"data": all_teams_data})
        
        return None
                
    except httpx.TimeoutException:
        logger.error("[DVP] BallDontLie API timeout")
        return None
    except Exception as e:
        logger.error(f"[DVP] BallDontLie fetch error: {e}")
        return None


def _parse_bdl_opponent_stats(data: Dict) -> Optional[Dict[str, Dict[str, int]]]:
    """
    Parse BallDontLie team_season_averages response (type=opponent).
    
    The 'stats' object contains opponent stats and PRE-CALCULATED ranks:
    - opp_pts_rank: Defensive rank for points (1 = allows fewest = best defense)
    - opp_ast_rank: Defensive rank for assists
    - opp_reb_rank: Defensive rank for rebounds
    - opp_fg3m_rank: Defensive rank for 3PM
    - opp_blk_rank: Opponent blocks rank
    - opp_stl_rank: Opponent steals rank
    
    We use BDL's pre-calculated ranks directly for accuracy.
    Returns rankings directly (not raw stats).
    """
    try:
        teams_data = data.get("data", [])
        if not teams_data:
            logger.warning("[DVP] No team data in BDL response")
            return None
        
        # Build rankings dict: {stat_type: {team: rank}}
        rankings: Dict[str, Dict[str, int]] = {
            "PTS": {},
            "AST": {},
            "REB": {},
            "3PM": {},
            "BLK": {},
            "STL": {},
        }
        
        for team_entry in teams_data:
            team_info = team_entry.get("team", {})
            team_abbrev = team_info.get("abbreviation")
            
            if not team_abbrev:
                # Try to map from ID
                team_id = team_info.get("id")
                team_abbrev = BDL_TEAM_ID_MAP.get(team_id)
            
            if not team_abbrev:
                continue
            
            stats = team_entry.get("stats", {})
            
            # Use BDL's pre-calculated ranks directly
            # These are already: 1 = best defense (allows least), 30 = worst defense
            rankings["PTS"][team_abbrev] = stats.get("opp_pts_rank", 15)
            rankings["AST"][team_abbrev] = stats.get("opp_ast_rank", 15)
            rankings["REB"][team_abbrev] = stats.get("opp_reb_rank", 15)
            rankings["3PM"][team_abbrev] = stats.get("opp_fg3m_rank", 15)
            rankings["BLK"][team_abbrev] = stats.get("opp_blk_rank", 15)
            rankings["STL"][team_abbrev] = stats.get("opp_stl_rank", 15)
        
        # Check we got enough teams
        teams_count = len(rankings.get("PTS", {}))
        if teams_count < 20:
            logger.warning(f"[DVP] Only got {teams_count} teams from BDL, expected ~30")
            return None
        
        logger.info(f"[DVP] Parsed BDL pre-calculated ranks for {teams_count} teams")
        return rankings
        
    except Exception as e:
        logger.error(f"[DVP] Parse error: {e}")
        return None


def _convert_stats_to_rankings(raw_stats: Dict[str, Dict[str, float]]) -> Dict[str, Dict[str, int]]:
    """
    DEPRECATED: This function is no longer used in production.
    Rankings are now calculated directly in _compute_dvp_rankings_from_raw_stats().
    Kept for backward compatibility with existing tests.
    
    Convert raw defensive stats to rankings (1-30).
    
    For opponent stats (pts allowed, ast allowed, etc.):
    - Lower allowed stats = better defense = rank 1 (best)
    - Higher allowed stats = worse defense = rank 30 (worst)
    
    So rank 30 (worst defense) = best matchup for players!
    """
    if not raw_stats:
        return {}
    
    rankings: Dict[str, Dict[str, int]] = {}
    
    # Get all stat types from first team
    sample_team = next(iter(raw_stats.values()))
    stat_types = list(sample_team.keys())
    
    for stat_type in stat_types:
        # Collect values for this stat
        team_values = []
        for team, stats in raw_stats.items():
            if stat_type in stats and stats[stat_type] > 0:
                team_values.append((team, stats[stat_type]))
        
        if not team_values:
            continue
        
        # Sort by value (ascending = less allowed = better defense = rank 1)
        team_values.sort(key=lambda x: x[1])
        
        # Assign ranks (1 = best defense, 30 = worst defense)
        rankings[stat_type] = {}
        for rank, (team, value) in enumerate(team_values, 1):
            rankings[stat_type][team] = rank
    
    return rankings


async def _load_from_mongodb() -> Optional[DvPCacheEntry]:
    """Load cached DvP rankings from MongoDB."""
    if _db_ref is None:
        return None
    
    try:
        collection = _db_ref[DVP_COLLECTION]
        doc = await collection.find_one({"type": "dvp_rankings"})
        
        if not doc:
            return None
        
        expires_at = doc.get("expires_at")
        
        # Handle timezone-aware comparison
        now = datetime.now(timezone.utc)
        if expires_at:
            # Make sure expires_at is timezone-aware
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if now > expires_at:
                logger.info("[DVP] MongoDB cache expired")
                return None
        
        fetched_at = doc.get("fetched_at", now)
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        
        return DvPCacheEntry(
            rankings=doc.get("rankings", {}),
            source=DvPDataSource.MONGODB,
            fetched_at=fetched_at,
            season=doc.get("season", get_current_season_str()),
            expires_at=expires_at or now + timedelta(hours=DVP_CACHE_TTL_HOURS)
        )
        
    except Exception as e:
        logger.error(f"[DVP] MongoDB load error: {e}")
        return None


async def _save_to_mongodb(rankings: Dict[str, Dict[str, int]], source: str):
    """Save DvP rankings to MongoDB for persistence."""
    if _db_ref is None:
        logger.warning("[DVP] Cannot save to MongoDB - no db reference")
        return
    
    try:
        collection = _db_ref[DVP_COLLECTION]
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=DVP_CACHE_TTL_HOURS)
        
        doc = {
            "type": "dvp_rankings",
            "rankings": rankings,
            "source": source,
            "fetched_at": now,
            "season": get_current_season_str(),
            "expires_at": expires_at,
            "updated_at": now
        }
        
        await collection.update_one(
            {"type": "dvp_rankings"},
            {"$set": doc},
            upsert=True
        )
        
        logger.info(f"[DVP] Saved rankings to MongoDB (expires: {expires_at.isoformat()})")
        
    except Exception as e:
        logger.error(f"[DVP] MongoDB save error: {e}")


async def fetch_live_dvp() -> Tuple[Dict[str, Dict[str, int]], str, Dict[str, Any]]:
    """
    Fetch live DvP rankings from BallDontLie API.
    
    This is the main entry point for getting DvP data.
    
    Priority:
    1. In-memory cache (if valid)
    2. MongoDB cache (if valid)
    3. Live BallDontLie API fetch
    4. Static fallback data
    
    Returns:
        Tuple of (rankings_dict, source_type, metadata)
    """
    global _dvp_cache, _last_fetch_attempt, _fetch_failures
    
    async with _fetch_lock:
        # Check in-memory cache first
        if _dvp_cache and not _dvp_cache.is_expired:
            logger.debug(f"[DVP] Using in-memory cache (age: {_dvp_cache.age_hours:.1f}h)")
            return _dvp_cache.rankings, DvPDataSource.CACHED, {
                "dvp_type": "dynamic_live",
                "cache_hit": True,
                "cache_age_hours": _dvp_cache.age_hours
            }
        
        # Check MongoDB cache
        mongo_cache = await _load_from_mongodb()
        if mongo_cache:
            _dvp_cache = mongo_cache
            logger.info(f"[DVP] Loaded from MongoDB (age: {mongo_cache.age_hours:.1f}h)")
            return mongo_cache.rankings, DvPDataSource.MONGODB, {
                "dvp_type": "dynamic_live",
                "cache_hit": True,
                "from_mongodb": True,
                "cache_age_hours": mongo_cache.age_hours
            }
        
        # Rate limit fetch attempts (max 1 per 5 minutes on failure)
        if _last_fetch_attempt and _fetch_failures > 0:
            time_since_last = (datetime.now(timezone.utc) - _last_fetch_attempt).total_seconds()
            if time_since_last < 300:  # 5 minutes
                logger.debug("[DVP] Rate limiting fetch attempts, using fallback")
                return DVP_RANKINGS, DvPDataSource.STATIC_FALLBACK, {
                    "dvp_type": "static_fallback",
                    "reason": "rate_limited_after_failure"
                }
        
        _last_fetch_attempt = datetime.now(timezone.utc)
        
        # Try BallDontLie API
        logger.info("[DVP] Attempting live data fetch from BallDontLie API")
        rankings = await _fetch_bdl_defensive_stats()
        
        # _parse_bdl_opponent_stats now returns rankings directly (using BDL's pre-calculated ranks)
        if rankings and len(rankings) >= 3:  # At least PTS, AST, REB
            # Save to MongoDB for persistence
            await _save_to_mongodb(rankings, DvPDataSource.BDL_API)
            
            # Update in-memory cache
            _dvp_cache = DvPCacheEntry(
                rankings=rankings,
                source=DvPDataSource.DYNAMIC_LIVE,
                fetched_at=datetime.now(timezone.utc),
                season=get_current_season_str(),
                expires_at=datetime.now(timezone.utc) + timedelta(hours=DVP_CACHE_TTL_HOURS)
            )
            _fetch_failures = 0
            
            logger.info(f"[DVP] Live data fetched: {len(rankings)} stat types, {len(next(iter(rankings.values())))} teams")
            
            return rankings, DvPDataSource.DYNAMIC_LIVE, {
                "dvp_type": "dynamic_live",
                "cache_hit": False,
                "teams_count": len(next(iter(rankings.values()))),
                "stat_types": list(rankings.keys()),
                "season": get_current_season_str()
            }
        
        # Fallback to hardcoded data
        _fetch_failures += 1
        logger.warning(f"[DVP] Live fetch failed (attempt {_fetch_failures}), using static fallback")
        
        return DVP_RANKINGS, DvPDataSource.STATIC_FALLBACK, {
            "dvp_type": "static_fallback",
            "reason": "api_failure",
            "failures": _fetch_failures
        }


async def get_dvp_rankings_with_source() -> Tuple[Dict[str, Dict[str, int]], Dict[str, str]]:
    """
    Get DvP rankings with data source headers.
    
    Returns:
        Tuple of (dvp_rankings, headers_dict)
    """
    rankings, source, metadata = await fetch_live_dvp()
    
    headers = {
        "X-Data-Source": source,
        "X-DVP-Last-Update": _dvp_cache.fetched_at.isoformat() if _dvp_cache else "static",
        "dvp_type": metadata.get("dvp_type", "static_fallback")
    }
    
    if source == DvPDataSource.STATIC_FALLBACK:
        headers["X-Data-Source"] = "static-fallback"
        headers["X-Data-Source-Warning"] = "Using static fallback data. Live API may be unavailable."
    
    return rankings, headers


async def get_dvp_rankings() -> Dict[str, Dict[str, int]]:
    """Get just the rankings (without metadata)."""
    rankings, _, _ = await fetch_live_dvp()
    return rankings


def calculate_dvp_modifier(opponent_team: str, stat_type: str) -> float:
    """
    Calculate DvP modifier based on opponent's defensive ranking.
    
    Uses cached live data if available, falls back to hardcoded data.
    
    Args:
        opponent_team: 3-letter team abbreviation (e.g., "LAL", "BOS")
        stat_type: Stat market type (e.g., "player_points", "PTS")
    
    Returns:
        float: 0.0 to 1.0 where:
        - 0.0-0.3 = TOUGH matchup (top 10 defense, rank 1-10)
        - 0.4-0.6 = NEUTRAL matchup (11-20 defense)
        - 0.7-1.0 = FAVORABLE matchup (21-30 defense, worst defenses)
    """
    if not opponent_team or not stat_type:
        return 0.5  # Neutral default
    
    # Use cached rankings if available, otherwise fallback
    rankings = _dvp_cache.rankings if _dvp_cache and not _dvp_cache.is_expired else DVP_RANKINGS
    
    # Normalize stat type
    stat_key = STAT_TYPE_MAP.get(stat_type, stat_type.upper())
    
    # Handle combo stats (PRA, P+R, etc.)
    if stat_key in ["PRA", "P+R", "P+A", "R+A"]:
        components = {
            "PRA": ["PTS", "REB", "AST"],
            "P+R": ["PTS", "REB"],
            "P+A": ["PTS", "AST"],
            "R+A": ["REB", "AST"],
        }
        comp_list = components.get(stat_key, [])
        if not comp_list:
            return 0.5
        
        # Average the rankings of component stats
        component_rankings = []
        for comp in comp_list:
            if comp in rankings and opponent_team in rankings[comp]:
                component_rankings.append(rankings[comp][opponent_team])
        
        if not component_rankings:
            return 0.5
        
        avg_rank = sum(component_rankings) / len(component_rankings)
        return round((avg_rank - 1) / 29, 3)
    
    # Single stat lookup
    if stat_key not in rankings:
        return 0.5
    
    team_rankings = rankings[stat_key]
    if not team_rankings or opponent_team not in team_rankings:
        return 0.5
    
    rank = team_rankings[opponent_team]
    # Convert ranking to modifier (rank 30 = 1.0 best matchup, rank 1 = 0.0 worst)
    modifier = (rank - 1) / 29
    return round(modifier, 3)


def calculate_matchup_multiplier(
    player_position: str, 
    opponent_team: str, 
    stat_type: str,
    direction: str = "over"
) -> float:
    """
    Calculate matchup multiplier for probability adjustment.
    
    This implements the position-based matchup boost logic:
    - If player is a Center and opponent rank for REB is >25 (Bottom 5), boost "Over" by 12%
    - Similar logic for other positions and stats
    
    Args:
        player_position: Player's position (C, PF, SF, SG, PG, G, F)
        opponent_team: Opponent's 3-letter abbreviation
        stat_type: Stat type being bet on
        direction: "over" or "under"
    
    Returns:
        float: Multiplier (1.0 = no change, 1.12 = 12% boost)
    """
    if not player_position or not opponent_team or not stat_type:
        return 1.0
    
    # Get rankings
    rankings = _dvp_cache.rankings if _dvp_cache and not _dvp_cache.is_expired else DVP_RANKINGS
    
    # Normalize stat type
    stat_key = STAT_TYPE_MAP.get(stat_type, stat_type.upper())
    
    # Get opponent's rank for this stat
    if stat_key not in rankings or opponent_team not in rankings.get(stat_key, {}):
        return 1.0
    
    rank = rankings[stat_key].get(opponent_team, 15)  # Default to middle
    
    # Check if this stat is relevant for the player's position
    position = player_position.upper()
    relevant_stats = POSITION_STAT_MAP.get(position, [])
    
    if stat_key not in relevant_stats:
        return 1.0  # This stat isn't position-relevant
    
    # Apply boost logic based on rank
    # Rank > 25 (Bottom 5 defense) = 12% boost for "Over"
    # Rank < 6 (Top 5 defense) = 12% boost for "Under"
    
    if direction.lower() == "over":
        if rank > 25:  # Bottom 5 worst defense
            return 1.12  # 12% boost
        elif rank > 20:  # Bottom 10
            return 1.06  # 6% boost
    elif direction.lower() == "under":
        if rank < 6:  # Top 5 best defense
            return 1.12  # 12% boost
        elif rank < 11:  # Top 10
            return 1.06  # 6% boost
    
    return 1.0


def get_dvp_label(modifier: float) -> str:
    """Get human-readable DvP label."""
    if modifier >= 0.7:
        return "FAVORABLE"
    elif modifier >= 0.4:
        return "NEUTRAL"
    else:
        return "TOUGH"


def get_full_dvp_analysis(opponent_team: str, stat_type: str, player_position: Optional[str] = None) -> Dict[str, Any]:
    """
    Get complete DvP analysis for a matchup.
    
    Returns:
        Dict with modifier, label, rank, multiplier, and data source info
    """
    modifier = calculate_dvp_modifier(opponent_team, stat_type)
    rank = _get_defensive_rank(opponent_team, stat_type)
    
    # Calculate position-based multiplier if position provided
    over_multiplier = 1.0
    under_multiplier = 1.0
    if player_position:
        over_multiplier = calculate_matchup_multiplier(player_position, opponent_team, stat_type, "over")
        under_multiplier = calculate_matchup_multiplier(player_position, opponent_team, stat_type, "under")
    
    return {
        "dvp_modifier": modifier,
        "dvp_label": get_dvp_label(modifier),
        "opponent_team": opponent_team,
        "stat_type": stat_type,
        "defensive_rank": rank,
        "over_multiplier": over_multiplier,
        "under_multiplier": under_multiplier,
        "player_position": player_position,
        "dvp_type": "dynamic_live" if _dvp_cache and not _dvp_cache.is_expired else "static_fallback",
        "data_source": get_data_source(),
        "data_age_hours": _dvp_cache.age_hours if _dvp_cache else None
    }


def _get_defensive_rank(opponent_team: str, stat_type: str) -> Optional[int]:
    """Get raw defensive rank (1-30). Uses in-memory cache or static fallback."""
    global _dvp_cache
    
    # Check in-memory cache first
    if _dvp_cache and not _dvp_cache.is_expired:
        rankings = _dvp_cache.rankings
    else:
        # Fallback to static if cache is empty/expired
        # Note: The async fetch_live_dvp() should be called periodically to refresh _dvp_cache
        rankings = DVP_RANKINGS
        logger.warning(f"[DVP] Using static fallback - cache is {'expired' if _dvp_cache else 'empty'}")
    
    stat_key = STAT_TYPE_MAP.get(stat_type, stat_type.upper())
    
    if stat_key in rankings and opponent_team in rankings[stat_key]:
        return rankings[stat_key][opponent_team]
    return None


def get_dvp_rank(opponent_team: str, stat_type: str) -> int:
    """
    Get defensive rank for an opponent/stat combination.
    
    Returns:
        int: 1-30 rank (1 = best defense, 30 = worst defense)
        Returns 15 (neutral) if data not available
    """
    rank = _get_defensive_rank(opponent_team, stat_type)
    return rank if rank is not None else 15


def get_dvp_rank_color(rank: int) -> str:
    """
    Get color code for DvP rank badge.
    
    Returns:
        str: 'green' (25-30, worst defense = best matchup),
             'yellow' (10-24, neutral),
             'red' (1-9, best defense = worst matchup)
    """
    if rank >= 25:
        return "green"
    elif rank >= 10:
        return "yellow"
    else:
        return "red"


def calculate_dvp_certainty_multiplier(rank: int) -> float:
    """
    Calculate the multiplier to apply to Statistical_Certainty based on DvP rank.
    
    Args:
        rank: Opponent defensive rank (1-30)
    
    Returns:
        float: Multiplier to apply
            - Rank >= 25 (Bottom 5 Defense): +10% boost (1.10)
            - Rank <= 5 (Top 5 Defense): -15% penalty (0.85)
            - Else: No change (1.0)
    """
    if rank >= 25:  # Bottom 5 defense = best matchup
        return 1.10  # +10% boost
    elif rank <= 5:  # Top 5 defense = worst matchup
        return 0.85  # -15% penalty
    else:
        return 1.0  # No change


# ==================== SCHEDULED REFRESH ====================

async def scheduled_dvp_refresh():
    """
    Daily DvP data refresh at 8:00 AM EST.
    
    This should be called by the APScheduler job in server.py.
    It forces a fresh fetch from the BallDontLie API and saves to MongoDB.
    """
    global _dvp_cache
    
    logger.info("=" * 50)
    logger.info("[DVP] SCHEDULED 8:00 AM EST REFRESH STARTED")
    logger.info("=" * 50)
    
    # Clear cache to force refetch
    _dvp_cache = None
    
    # Fetch fresh data
    rankings, source, metadata = await fetch_live_dvp()
    
    if source == DvPDataSource.DYNAMIC_LIVE:
        logger.info(f"[DVP] Refresh SUCCESS: {len(rankings)} stat categories, {len(next(iter(rankings.values())))} teams")
    else:
        logger.warning(f"[DVP] Refresh FAILED: Using {source}")
    
    return {
        "success": source != DvPDataSource.STATIC_FALLBACK,
        "source": source,
        "metadata": metadata
    }


async def force_refresh_dvp() -> Dict[str, Any]:
    """
    Force an immediate refresh of DvP data.
    
    Useful for manual refresh or testing.
    """
    global _dvp_cache
    
    logger.info("[DVP] MANUAL FORCE REFRESH TRIGGERED")
    
    # Clear cache to force refetch
    _dvp_cache = None
    
    rankings, source, metadata = await fetch_live_dvp()
    
    return {
        "success": source != DvPDataSource.STATIC_FALLBACK,
        "source": source,
        "metadata": metadata,
        "teams_count": len(next(iter(rankings.values()))) if rankings else 0,
        "stat_types": list(rankings.keys()) if rankings else []
    }


def get_dvp_status() -> Dict[str, Any]:
    """Get current DvP service status."""
    return {
        "has_live_data": _dvp_cache is not None and not _dvp_cache.is_expired,
        "data_source": get_data_source(),
        "dvp_type": "dynamic_live" if _dvp_cache and not _dvp_cache.is_expired else "static_fallback",
        "cache_age_hours": _dvp_cache.age_hours if _dvp_cache else None,
        "cache_expires_at": _dvp_cache.expires_at.isoformat() if _dvp_cache else None,
        "season": _dvp_cache.season if _dvp_cache else get_current_season_str(),
        "fetch_failures": _fetch_failures,
        "last_fetch_attempt": _last_fetch_attempt.isoformat() if _last_fetch_attempt else None,
        "api_key_configured": bool(_get_bdl_api_key()),
        "mongodb_configured": _db_ref is not None
    }


# ==================== INTEGRATION WITH PARLAY SERVICE ====================

def apply_dvp_to_prop(prop_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Apply DvP analysis to a prop pick.
    
    This function should be called by parlay_service.py when processing picks.
    It adds DvP-based probability adjustments to the prop.
    
    Args:
        prop_data: Dict containing at minimum:
            - opponent: Opponent team abbreviation
            - stat_type: Stat market type
            - player_position: Player's position (optional)
            - hit_probability: Base hit probability (optional)
    
    Returns:
        prop_data enhanced with DvP analysis
    """
    opponent = prop_data.get("opponent") or prop_data.get("opponent_team")
    stat_type = prop_data.get("stat_type") or prop_data.get("market")
    player_position = prop_data.get("player_position") or prop_data.get("position")
    
    if not opponent or not stat_type:
        return prop_data
    
    # Get full DvP analysis
    dvp_analysis = get_full_dvp_analysis(opponent, stat_type, player_position)
    
    # Apply matchup multiplier to probability if present
    base_prob = prop_data.get("hit_probability", 50)
    direction = prop_data.get("direction", "over")
    
    multiplier = dvp_analysis.get("over_multiplier", 1.0) if direction == "over" else dvp_analysis.get("under_multiplier", 1.0)
    
    adjusted_prob = min(99, base_prob * multiplier)  # Cap at 99%
    
    # Enhance prop data
    prop_data["dvp_modifier"] = dvp_analysis["dvp_modifier"]
    prop_data["dvp_label"] = dvp_analysis["dvp_label"]
    prop_data["defensive_rank"] = dvp_analysis["defensive_rank"]
    prop_data["matchup_multiplier"] = multiplier
    prop_data["adjusted_hit_probability"] = round(adjusted_prob, 1)
    prop_data["dvp_type"] = dvp_analysis["dvp_type"]
    
    return prop_data

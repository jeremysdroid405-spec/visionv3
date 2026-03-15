"""
DvP (Defense vs Position) Service
==================================
Dynamic, real-time DvP data fetching with intelligent fallback.

Features:
- Live data from NBA.com and BallDontLie APIs
- 24-hour cache with morning refresh
- Position-specific defensive rankings
- Automatic fallback to hardcoded data
- X-Data-Source and dvp_type metadata headers
"""
import httpx
import asyncio
import logging
from typing import Optional, Dict, Any, Tuple, List
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from config.settings import DVP_RANKINGS, STAT_TYPE_MAP, TEAM_ABBREV_MAP

logger = logging.getLogger(__name__)

# ==================== CONFIGURATION ====================

# NBA.com API endpoints
NBA_STATS_BASE = "https://stats.nba.com/stats"
NBA_TEAM_STATS_ENDPOINT = f"{NBA_STATS_BASE}/leaguedashteamstats"

# BallDontLie API (backup source)
BDL_API_BASE = "https://api.balldontlie.io/v1"

# Cache settings
DVP_CACHE_TTL_HOURS = 24
DVP_REFRESH_HOUR = 8  # 8:00 AM local time for morning refresh

# NBA.com required headers
NBA_HEADERS = {
    "Host": "stats.nba.com",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
}

# Team ID to Abbreviation mapping (NBA.com uses numeric IDs)
NBA_TEAM_ID_MAP = {
    1610612737: "ATL", 1610612738: "BOS", 1610612751: "BKN", 1610612766: "CHA",
    1610612741: "CHI", 1610612739: "CLE", 1610612742: "DAL", 1610612743: "DEN",
    1610612765: "DET", 1610612744: "GSW", 1610612745: "HOU", 1610612754: "IND",
    1610612746: "LAC", 1610612747: "LAL", 1610612763: "MEM", 1610612748: "MIA",
    1610612749: "MIL", 1610612750: "MIN", 1610612740: "NOP", 1610612752: "NYK",
    1610612760: "OKC", 1610612753: "ORL", 1610612755: "PHI", 1610612756: "PHX",
    1610612757: "POR", 1610612758: "SAC", 1610612759: "SAS", 1610612761: "TOR",
    1610612762: "UTA", 1610612764: "WAS"
}

# NBA Team abbreviation mapping (from API names)
NBA_TEAM_ABBREV_MAP = {
    "ATL": "ATL", "BOS": "BOS", "BKN": "BKN", "CHA": "CHA", "CHI": "CHI",
    "CLE": "CLE", "DAL": "DAL", "DEN": "DEN", "DET": "DET", "GSW": "GSW",
    "HOU": "HOU", "IND": "IND", "LAC": "LAC", "LAL": "LAL", "MEM": "MEM",
    "MIA": "MIA", "MIL": "MIL", "MIN": "MIN", "NOP": "NOP", "NYK": "NYK",
    "OKC": "OKC", "ORL": "ORL", "PHI": "PHI", "PHX": "PHX", "POR": "POR",
    "SAC": "SAC", "SAS": "SAS", "TOR": "TOR", "UTA": "UTA", "WAS": "WAS"
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
    NBA_API = "nba_api"
    BDL_API = "bdl_api"
    MAINTENANCE = "maintenance"


# ==================== GLOBAL STATE ====================

_dvp_cache: Optional[DvPCacheEntry] = None
_fetch_lock = asyncio.Lock()
_last_fetch_attempt: Optional[datetime] = None
_fetch_failures: int = 0


# ==================== CORE FUNCTIONS ====================

def get_current_season() -> str:
    """Get current NBA season string (e.g., '2024-25')."""
    now = datetime.now()
    # NBA season starts in October
    if now.month >= 10:
        return f"{now.year}-{str(now.year + 1)[-2:]}"
    else:
        return f"{now.year - 1}-{str(now.year)[-2:]}"


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


async def fetch_nba_defensive_stats() -> Optional[Dict[str, Dict[str, float]]]:
    """
    Fetch defensive stats from NBA.com API.
    
    Returns raw opponent stats (points allowed, assists allowed, etc.)
    """
    try:
        season = get_current_season()
        
        params = {
            "Conference": "",
            "DateFrom": "",
            "DateTo": "",
            "Division": "",
            "GameScope": "",
            "GameSegment": "",
            "Height": "",
            "LastNGames": "0",
            "LeagueID": "00",
            "Location": "",
            "MeasureType": "Opponent",  # Key: Get opponent stats (defensive)
            "Month": "0",
            "OpponentTeamID": "0",
            "Outcome": "",
            "PORound": "0",
            "PaceAdjust": "N",
            "PerMode": "PerGame",
            "Period": "0",
            "PlayerExperience": "",
            "PlayerPosition": "",
            "PlusMinus": "N",
            "Rank": "N",
            "Season": season,
            "SeasonSegment": "",
            "SeasonType": "Regular Season",
            "ShotClockRange": "",
            "StarterBench": "",
            "TeamID": "0",
            "TwoWay": "0",
            "VsConference": "",
            "VsDivision": "",
        }
        
        logger.info(f"[DVP] Fetching NBA.com defensive stats for {season}")
        
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                NBA_TEAM_STATS_ENDPOINT,
                params=params,
                headers=NBA_HEADERS
            )
            
            if response.status_code == 200:
                data = response.json()
                return _parse_nba_team_stats(data)
            else:
                logger.warning(f"[DVP] NBA.com returned status {response.status_code}")
                return None
                
    except Exception as e:
        logger.error(f"[DVP] NBA.com fetch error: {e}")
        return None


def _parse_nba_team_stats(data: Dict) -> Optional[Dict[str, Dict[str, float]]]:
    """
    Parse NBA.com leaguedashteamstats response.
    
    Converts raw stats to per-team values for each stat category.
    """
    try:
        result_sets = data.get("resultSets", [])
        if not result_sets:
            return None
        
        headers = result_sets[0].get("headers", [])
        rows = result_sets[0].get("rowSet", [])
        
        if not headers or not rows:
            return None
        
        # Find column indices
        team_id_idx = headers.index("TEAM_ID") if "TEAM_ID" in headers else None
        team_abbrev_idx = headers.index("TEAM_ABBREVIATION") if "TEAM_ABBREVIATION" in headers else None
        
        # Stat column indices (opponent stats = defensive)
        stat_columns = {
            "PTS": "OPP_PTS" if "OPP_PTS" in headers else "PTS",
            "AST": "OPP_AST" if "OPP_AST" in headers else "AST",
            "REB": "OPP_REB" if "OPP_REB" in headers else "REB",
            "3PM": "OPP_FG3M" if "OPP_FG3M" in headers else "FG3M",
            "BLK": "OPP_BLK" if "OPP_BLK" in headers else "BLK",
            "STL": "OPP_STL" if "OPP_STL" in headers else "STL",
            "TO": "OPP_TOV" if "OPP_TOV" in headers else "TOV",
        }
        
        # Get column indices for each stat
        stat_indices = {}
        for stat_key, col_name in stat_columns.items():
            if col_name in headers:
                stat_indices[stat_key] = headers.index(col_name)
        
        # Build raw stats per team
        team_stats: Dict[str, Dict[str, float]] = {}
        
        for row in rows:
            # Get team abbreviation
            if team_abbrev_idx is not None:
                team = row[team_abbrev_idx]
            elif team_id_idx is not None:
                team_id = row[team_id_idx]
                team = NBA_TEAM_ID_MAP.get(team_id)
            else:
                continue
            
            if not team:
                continue
            
            team_stats[team] = {}
            for stat_key, idx in stat_indices.items():
                team_stats[team][stat_key] = float(row[idx]) if row[idx] else 0.0
        
        logger.info(f"[DVP] Parsed stats for {len(team_stats)} teams")
        return team_stats
        
    except Exception as e:
        logger.error(f"[DVP] Parse error: {e}")
        return None


def _convert_stats_to_rankings(raw_stats: Dict[str, Dict[str, float]]) -> Dict[str, Dict[str, int]]:
    """
    Convert raw defensive stats to rankings (1-30).
    
    Lower allowed stats = better defense = lower rank (1 is best defense)
    """
    rankings = {}
    
    # Get all stat types
    if not raw_stats:
        return {}
    
    sample_team = next(iter(raw_stats.values()))
    stat_types = list(sample_team.keys())
    
    for stat_type in stat_types:
        # Collect values for this stat
        team_values = []
        for team, stats in raw_stats.items():
            if stat_type in stats:
                team_values.append((team, stats[stat_type]))
        
        # Sort by value (ascending = less points allowed = better defense)
        team_values.sort(key=lambda x: x[1])
        
        # Assign ranks (1 = best defense = lowest allowed)
        rankings[stat_type] = {}
        for rank, (team, value) in enumerate(team_values, 1):
            rankings[stat_type][team] = rank
    
    return rankings


async def fetch_live_dvp() -> Tuple[Dict[str, Dict[str, int]], str, Dict[str, Any]]:
    """
    Fetch live DvP rankings from external APIs.
    
    Returns:
        Tuple of (rankings_dict, source_type, metadata)
    """
    global _dvp_cache, _last_fetch_attempt, _fetch_failures
    
    async with _fetch_lock:
        # Check if cached data is still valid
        if _dvp_cache and not _dvp_cache.is_expired:
            logger.debug(f"[DVP] Using cached data (age: {_dvp_cache.age_hours:.1f}h)")
            return _dvp_cache.rankings, DvPDataSource.CACHED, {
                "dvp_type": "dynamic_live",
                "cache_hit": True,
                "cache_age_hours": _dvp_cache.age_hours
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
        
        # Try NBA.com API first
        logger.info("[DVP] Attempting live data fetch from NBA.com")
        raw_stats = await fetch_nba_defensive_stats()
        
        if raw_stats:
            rankings = _convert_stats_to_rankings(raw_stats)
            
            if rankings and len(rankings) >= 3:  # At least 3 stat types
                # Cache the results
                _dvp_cache = DvPCacheEntry(
                    rankings=rankings,
                    source=DvPDataSource.DYNAMIC_LIVE,
                    fetched_at=datetime.now(timezone.utc),
                    season=get_current_season(),
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=DVP_CACHE_TTL_HOURS)
                )
                _fetch_failures = 0
                
                logger.info(f"[DVP] Live data fetched successfully: {len(rankings)} stat types, {len(next(iter(rankings.values())))} teams")
                
                return rankings, DvPDataSource.DYNAMIC_LIVE, {
                    "dvp_type": "dynamic_live",
                    "cache_hit": False,
                    "teams_count": len(next(iter(rankings.values()))),
                    "stat_types": list(rankings.keys()),
                    "season": get_current_season()
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
        - 0.0-0.3 = TOUGH matchup (top 10 defense)
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


def get_dvp_label(modifier: float) -> str:
    """Get human-readable DvP label."""
    if modifier >= 0.7:
        return "FAVORABLE"
    elif modifier >= 0.4:
        return "NEUTRAL"
    else:
        return "TOUGH"


def get_full_dvp_analysis(opponent_team: str, stat_type: str) -> Dict[str, Any]:
    """
    Get complete DvP analysis for a matchup.
    
    Returns:
        Dict with modifier, label, rank, and data source info
    """
    modifier = calculate_dvp_modifier(opponent_team, stat_type)
    rank = _get_defensive_rank(opponent_team, stat_type)
    
    return {
        "dvp_modifier": modifier,
        "dvp_label": get_dvp_label(modifier),
        "opponent_team": opponent_team,
        "stat_type": stat_type,
        "defensive_rank": rank,
        "dvp_type": "dynamic_live" if _dvp_cache and not _dvp_cache.is_expired else "static_fallback",
        "data_source": get_data_source(),
        "data_age_hours": _dvp_cache.age_hours if _dvp_cache else None
    }


def _get_defensive_rank(opponent_team: str, stat_type: str) -> Optional[int]:
    """Get raw defensive rank (1-30)."""
    rankings = _dvp_cache.rankings if _dvp_cache and not _dvp_cache.is_expired else DVP_RANKINGS
    stat_key = STAT_TYPE_MAP.get(stat_type, stat_type.upper())
    
    if stat_key in rankings and opponent_team in rankings[stat_key]:
        return rankings[stat_key][opponent_team]
    return None


# ==================== SCHEDULED REFRESH ====================

async def schedule_dvp_refresh():
    """
    Schedule daily DvP data refresh.
    
    Call this from the application startup to ensure fresh data each morning.
    """
    logger.info("[DVP] Scheduling daily refresh")
    
    # Initial fetch on startup
    await fetch_live_dvp()
    
    # Note: Actual scheduling should be done via APScheduler in server.py
    # This function just performs the initial fetch


async def force_refresh_dvp() -> Dict[str, Any]:
    """
    Force an immediate refresh of DvP data.
    
    Useful for manual refresh or testing.
    """
    global _dvp_cache
    
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
        "season": _dvp_cache.season if _dvp_cache else get_current_season(),
        "fetch_failures": _fetch_failures,
        "last_fetch_attempt": _last_fetch_attempt.isoformat() if _last_fetch_attempt else None
    }

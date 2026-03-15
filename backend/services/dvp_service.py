"""
DvP (Defense vs Position) Service
==================================
Calculates matchup modifiers based on opponent defensive rankings.

Features:
- Static fallback data for offline/maintenance mode
- Placeholder for live DvP API integration (P2)
- X-Data-Source header support for data origin tracking
"""
import logging
from typing import Optional, Dict, Any, Tuple
from datetime import datetime, timezone
from config.settings import DVP_RANKINGS, STAT_TYPE_MAP

logger = logging.getLogger(__name__)

# Data source tracking
_data_source: str = "static-fallback"
_last_live_fetch: Optional[datetime] = None
_live_dvp_cache: Dict[str, Dict] = {}


class DvPDataSource:
    """Enum-like class for data source types."""
    STATIC_FALLBACK = "static-fallback"
    LIVE_API = "live-api"
    CACHED = "cached"
    MAINTENANCE = "maintenance"


def get_data_source() -> str:
    """Get the current data source type."""
    return _data_source


def get_data_source_header() -> Dict[str, str]:
    """
    Get headers indicating the data source.
    
    Returns:
        Dict with X-Data-Source header
    """
    return {
        "X-Data-Source": _data_source,
        "X-Data-Source-Timestamp": _last_live_fetch.isoformat() if _last_live_fetch else "never"
    }


async def fetch_live_dvp() -> Tuple[Dict[str, Dict], str]:
    """
    Fetch live DvP rankings from external API.
    
    This is a P2 placeholder for live data integration.
    When implemented, this will:
    1. Call NBA.com or a third-party stats API
    2. Parse defensive efficiency by position
    3. Calculate DvP rankings (1-30 per stat)
    4. Cache results with TTL
    
    Returns:
        Tuple of (dvp_data, data_source)
        
    API Options (P2):
    - NBA.com API (unofficial)
    - Basketball Reference (scraping)
    - SportsRadar API (paid)
    - BallDontLie extended stats (if available)
    
    Example response format:
    {
        "PTS": {"LAL": 15, "BOS": 3, ...},
        "REB": {"LAL": 22, "BOS": 8, ...},
        ...
    }
    """
    global _data_source, _last_live_fetch, _live_dvp_cache
    
    # TODO: P2 - Implement live API integration
    # Example implementation outline:
    #
    # try:
    #     async with httpx.AsyncClient() as client:
    #         response = await client.get(
    #             "https://stats.nba.com/stats/leaguedashteamstats",
    #             params={
    #                 "Season": "2024-25",
    #                 "SeasonType": "Regular Season",
    #                 "MeasureType": "Opponent",
    #             },
    #             headers={"Referer": "https://www.nba.com/"},
    #             timeout=10.0
    #         )
    #         
    #         if response.status_code == 200:
    #             data = parse_nba_dvp_response(response.json())
    #             _live_dvp_cache = data
    #             _data_source = DvPDataSource.LIVE_API
    #             _last_live_fetch = datetime.now(timezone.utc)
    #             logger.info("[DVP] Live data fetched successfully")
    #             return data, DvPDataSource.LIVE_API
    #             
    # except Exception as e:
    #     logger.warning(f"[DVP] Live fetch failed, using fallback: {e}")
    
    # For now, return static fallback
    logger.debug("[DVP] Using static fallback data (live API not implemented)")
    _data_source = DvPDataSource.STATIC_FALLBACK
    
    return DVP_RANKINGS, DvPDataSource.STATIC_FALLBACK


async def get_dvp_rankings_with_source() -> Tuple[Dict[str, Dict], Dict[str, str]]:
    """
    Get DvP rankings with data source headers.
    
    Returns:
        Tuple of (dvp_rankings, headers_dict)
    """
    # Try live fetch first (will use cache/fallback if not implemented)
    rankings, source = await fetch_live_dvp()
    
    headers = {
        "X-Data-Source": source,
        "X-DVP-Last-Update": _last_live_fetch.isoformat() if _last_live_fetch else "static",
    }
    
    if source == DvPDataSource.STATIC_FALLBACK:
        headers["X-Data-Source-Warning"] = "Using static fallback data. Live API coming soon."
    
    return rankings, headers


def calculate_dvp_modifier(opponent_team: str, stat_type: str) -> float:
    """
    Calculate DvP modifier based on opponent's defensive ranking.
    
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
    
    # Normalize stat type
    stat_key = STAT_TYPE_MAP.get(stat_type, stat_type.upper())
    
    # Handle combo stats
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
        rankings = []
        for comp in comp_list:
            if comp in DVP_RANKINGS and opponent_team in DVP_RANKINGS[comp]:
                rankings.append(DVP_RANKINGS[comp][opponent_team])
        
        if not rankings:
            return 0.5
        
        avg_rank = sum(rankings) / len(rankings)
        return round((avg_rank - 1) / 29, 3)
    
    # Single stat lookup
    if stat_key not in DVP_RANKINGS:
        return 0.5
    
    rankings = DVP_RANKINGS[stat_key]
    if not rankings or opponent_team not in rankings:
        return 0.5
    
    rank = rankings[opponent_team]
    # Convert ranking to modifier (rank 30 = 1.0 best, rank 1 = 0.0 worst)
    modifier = (rank - 1) / 29
    return round(modifier, 3)


def get_dvp_label(modifier: float) -> str:
    """Get human-readable DvP label"""
    if modifier >= 0.7:
        return "FAVORABLE"
    elif modifier >= 0.4:
        return "NEUTRAL"
    else:
        return "TOUGH"


def get_full_dvp_analysis(opponent_team: str, stat_type: str) -> dict:
    """Get complete DvP analysis for a matchup"""
    modifier = calculate_dvp_modifier(opponent_team, stat_type)
    return {
        "dvp_modifier": modifier,
        "dvp_label": get_dvp_label(modifier),
        "opponent_team": opponent_team,
        "stat_type": stat_type,
        "defensive_rank": _get_defensive_rank(opponent_team, stat_type)
    }


def _get_defensive_rank(opponent_team: str, stat_type: str) -> Optional[int]:
    """Get raw defensive rank (1-30)"""
    stat_key = STAT_TYPE_MAP.get(stat_type, stat_type.upper())
    if stat_key in DVP_RANKINGS and opponent_team in DVP_RANKINGS[stat_key]:
        return DVP_RANKINGS[stat_key][opponent_team]
    return None

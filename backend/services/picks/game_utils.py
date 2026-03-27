"""
Game Utilities
==============
Utility functions for game state, player names, and filtering.
Extracted from picks_getter_service.py for modularity.
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
import re


def normalize_name(name: str) -> str:
    """Normalize player name for consistent matching."""
    if not name:
        return ""
    # Remove suffixes like Jr., III, etc.
    name = re.sub(r'\s+(Jr\.?|Sr\.?|III|II|IV)$', '', name, flags=re.IGNORECASE)
    # Remove extra spaces and convert to lowercase
    return ' '.join(name.lower().split())


def get_game_status(commence_time_str: str) -> Dict[str, Any]:
    """
    Determine game status based on commence time.
    
    Returns:
        {
            "status": "upcoming" | "live" | "final",
            "is_live": bool,
            "is_final": bool,
            "minutes_until_start": int or None
        }
    """
    try:
        if not commence_time_str:
            return {"status": "unknown", "is_live": False, "is_final": False}
        
        # Parse ISO format
        if isinstance(commence_time_str, str):
            commence_time = datetime.fromisoformat(commence_time_str.replace('Z', '+00:00'))
        else:
            commence_time = commence_time_str
        
        now = datetime.now(timezone.utc)
        diff = commence_time - now
        minutes_until = int(diff.total_seconds() / 60)
        
        # Game hasn't started
        if minutes_until > 0:
            return {
                "status": "upcoming",
                "is_live": False,
                "is_final": False,
                "minutes_until_start": minutes_until
            }
        
        # Game started within last 3 hours (likely live or just ended)
        hours_since_start = abs(minutes_until) / 60
        if hours_since_start < 3:
            return {
                "status": "live",
                "is_live": True,
                "is_final": False,
                "minutes_since_start": abs(minutes_until)
            }
        
        # Game likely finished
        return {
            "status": "final",
            "is_live": False,
            "is_final": True,
            "hours_since_start": hours_since_start
        }
        
    except Exception as e:
        return {"status": "unknown", "is_live": False, "is_final": False, "error": str(e)}


def did_play(game: Dict) -> bool:
    """Check if a player actually played in a game (has meaningful stats)."""
    if not game:
        return False
    
    # Check if minutes played is meaningful
    min_str = game.get("min", "0")
    if isinstance(min_str, str):
        try:
            # Handle formats like "32:15" or "32"
            if ":" in min_str:
                minutes = int(min_str.split(":")[0])
            else:
                minutes = int(float(min_str))
            if minutes < 1:
                return False
        except (ValueError, TypeError):
            return False
    
    # Check for any actual stats
    stats = ['pts', 'reb', 'ast', 'stl', 'blk', 'fgm', 'fga']
    has_stats = any(game.get(stat, 0) > 0 for stat in stats)
    
    return has_stats


def filter_played_games(game_logs: List[Dict]) -> List[Dict]:
    """Filter game logs to only include games where player actually played."""
    return [g for g in game_logs if did_play(g)]


def get_opponent_from_game(game: Dict, player_team_id: int = None) -> Optional[str]:
    """
    Extract opponent team abbreviation from game data.
    
    Args:
        game: Game log dictionary
        player_team_id: Player's team ID to determine opponent
        
    Returns:
        Opponent team abbreviation or None
    """
    TEAM_ID_TO_ABBR = {
        1: 'ATL', 2: 'BOS', 3: 'BKN', 4: 'CHA', 5: 'CHI',
        6: 'CLE', 7: 'DAL', 8: 'DEN', 9: 'DET', 10: 'GSW',
        11: 'HOU', 12: 'IND', 13: 'LAC', 14: 'LAL', 15: 'MEM',
        16: 'MIA', 17: 'MIL', 18: 'MIN', 19: 'NOP', 20: 'NYK',
        21: 'OKC', 22: 'ORL', 23: 'PHI', 24: 'PHX', 25: 'POR',
        26: 'SAC', 27: 'SAS', 28: 'TOR', 29: 'UTA', 30: 'WAS'
    }
    
    opp_id = game.get('opponent_team_id')
    if opp_id:
        return TEAM_ID_TO_ABBR.get(opp_id)
    
    # Try to determine from home/away
    home_team = game.get('home_team', {})
    away_team = game.get('visitor_team', {}) or game.get('away_team', {})
    
    if player_team_id:
        if home_team.get('id') == player_team_id:
            return TEAM_ID_TO_ABBR.get(away_team.get('id'))
        elif away_team.get('id') == player_team_id:
            return TEAM_ID_TO_ABBR.get(home_team.get('id'))
    
    return None


def clean_object_ids(data: Dict) -> None:
    """
    Remove MongoDB ObjectId fields from dictionary in place.
    Prevents JSON serialization errors.
    """
    if not data:
        return
    
    # Remove _id fields
    if '_id' in data:
        del data['_id']
    
    # Recursively clean nested dicts and lists
    for key, value in list(data.items()):
        if isinstance(value, dict):
            clean_object_ids(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    clean_object_ids(item)


def extract_stat_type(market: str) -> str:
    """
    Extract normalized stat type from market name.
    
    Examples:
        "player_points" -> "PTS"
        "player_rebounds" -> "REB"
        "player_assists" -> "AST"
    """
    MARKET_TO_STAT = {
        'player_points': 'PTS',
        'player_rebounds': 'REB',
        'player_assists': 'AST',
        'player_threes': '3PM',
        'player_steals': 'STL',
        'player_blocks': 'BLK',
        'player_turnovers': 'TOV',
        'player_points_rebounds_assists': 'PRA',
        'player_points_rebounds': 'PR',
        'player_points_assists': 'PA',
        'player_rebounds_assists': 'RA',
        'player_double_double': 'DD',
        'player_triple_double': 'TD',
    }
    
    if not market:
        return 'UNKNOWN'
    
    market_lower = market.lower()
    return MARKET_TO_STAT.get(market_lower, market.upper().replace('PLAYER_', ''))


def normalize_stat_key(stat_type: str) -> str:
    """
    Normalize stat key for consistent lookup.
    
    Examples:
        "PTS" -> "PTS"
        "pts" -> "PTS"
        "3PM" -> "fg3m"
        "TOV" -> "turnover"
    """
    if not stat_type:
        return stat_type
    
    stat_upper = stat_type.upper()
    
    STAT_KEY_MAP = {
        'PTS': 'pts',
        'REB': 'reb',
        'AST': 'ast',
        'STL': 'stl',
        'BLK': 'blk',
        '3PM': 'fg3m',
        'TOV': 'turnover',
        'PRA': 'pra',  # Computed
        'PR': 'pr',    # Computed
        'PA': 'pa',    # Computed
        'RA': 'ra',    # Computed
    }
    
    return STAT_KEY_MAP.get(stat_upper, stat_type.lower())

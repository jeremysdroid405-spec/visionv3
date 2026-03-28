"""
Stats Service - Hit Rate and Statistical Calculations
======================================================
Extracted from services.engines.demon_goblin_engine.py for modularity.

CRITICAL: All hit rates and averages MUST be calculated from the SAME
array of games to prevent mathematical contradictions (e.g., 100% hit
rate on Over 9.5 but L5 avg of 8.2).
"""
from typing import Dict, Any, List, Tuple
import logging

logger = logging.getLogger(__name__)

# Stat type to game log field mapping
# Supports BOTH BDL and BDL field names
STAT_FIELD_MAP = {
    "PTS": "pts",
    "REB": "reb",
    "AST": "ast",
    "3PM": "fg3m",  # BDL uses fg3m, BDL uses tptfgm (we'll handle both)
    "BLK": "blk",
    "STL": "stl",
    "TO": "turnover",  # BDL uses turnover, BDL uses TOV
    "P+R": ["pts", "reb"],
    "P+A": ["pts", "ast"],
    "R+A": ["reb", "ast"],
    "PRA": ["pts", "reb", "ast"],
    # Normalized variants
    "PR": ["pts", "reb"],
    "PA": ["pts", "ast"],
    "RA": ["reb", "ast"]
}

# Alternative field names (BDL -> BDL)
STAT_FIELD_MAPPING = {
    "tptfgm": "fg3m",
    "TOV": "turnover"
}


def safe_float(val) -> float:
    """Safely convert value to float, handling None and strings."""
    try:
        return float(val) if val else 0
    except (ValueError, TypeError):
        return 0


def get_stat_value(game: Dict, fields) -> float:
    """Get combined stat value from a game (works with BDL and BDL format)"""
    if isinstance(fields, list):
        total = 0
        for f in fields:
            val = game.get(f, 0)
            # Try BDL field name if BDL field not found
            if val == 0 or val is None:
                if f == "tptfgm":
                    val = game.get("fg3m", 0)
                elif f == "TOV":
                    val = game.get("turnover", 0)
            total += safe_float(val)
        return total
    
    val = game.get(fields, 0)
    # Try BDL field name if BDL field not found
    if val == 0 or val is None:
        if fields == "tptfgm":
            val = game.get("fg3m", 0)
        elif fields == "TOV":
            val = game.get("turnover", 0)
    return safe_float(val)


def calculate_hit_rate_for_games(games: List[Dict], fields, line_value: float) -> Dict[str, Any]:
    """
    Calculate hit rate AND average for a list of games.
    
    CRITICAL: Both values are calculated from the EXACT SAME games array
    to guarantee mathematical consistency.
    """
    if not games:
        return {"hit_rate": 0, "games_over": 0, "total_games": 0, "avg": 0}
    
    values = [get_stat_value(g, fields) for g in games]
    # Filter out None values and ensure all are numeric
    values = [v for v in values if v is not None]
    if not values:
        return {"hit_rate": 0, "games_over": 0, "total_games": 0, "avg": 0}
    
    games_over = sum(1 for v in values if v >= line_value)
    avg = sum(values) / len(values) if values else 0
    
    return {
        "hit_rate": games_over / len(values) if values else 0,
        "games_over": games_over,
        "total_games": len(values),
        "avg": round(avg, 1)
    }


def calculate_coupled_stats(games: List[Dict], stat_type: str, line_value: float) -> Dict[str, Any]:
    """
    Calculate COUPLED hit rates and averages from the same game array.
    
    This is the SINGLE SOURCE OF TRUTH for both metrics. The L5/L10/Season
    averages and hit rates are calculated from the EXACT SAME games list,
    guaranteeing mathematical consistency.
    
    Args:
        games: List of game dicts from BDL API (with pts, reb, ast, etc.)
        stat_type: Type of stat (PTS, REB, AST, 3PM, PRA, etc.)
        line_value: The prop line value
    
    Returns:
        Dict with coupled l5, l10, and season stats where avg and hit_rate
        are guaranteed to be mathematically consistent.
    """
    if not games:
        return {
            "l5": {"hit_rate": 0, "games_over": 0, "total_games": 0, "avg": 0},
            "l10": {"hit_rate": 0, "games_over": 0, "total_games": 0, "avg": 0},
            "season": {"hit_rate": 0, "games_over": 0, "total_games": 0, "avg": 0}
        }
    
    # Normalize stat type for lookup
    normalized_type = stat_type
    norm_map = {"P+R": "PR", "P+A": "PA", "R+A": "RA"}
    if stat_type in norm_map:
        normalized_type = norm_map[stat_type]
    
    fields = STAT_FIELD_MAP.get(normalized_type) or STAT_FIELD_MAP.get(stat_type)
    if not fields:
        logger.warning(f"[COUPLED_STATS] Unknown stat type: {stat_type}")
        return {
            "l5": {"hit_rate": 0, "games_over": 0, "total_games": 0, "avg": 0},
            "l10": {"hit_rate": 0, "games_over": 0, "total_games": 0, "avg": 0},
            "season": {"hit_rate": 0, "games_over": 0, "total_games": 0, "avg": 0}
        }
    
    # Filter to games with minutes > 0 (actually played)
    # BDL uses "min", BDL uses "mins"
    played_games = []
    for g in games:
        # Try both field names
        mins_str = g.get("min") or g.get("mins", "0") or "0"
        try:
            # Handle "MM:SS" format
            if isinstance(mins_str, str) and ":" in mins_str:
                mins = int(mins_str.split(":")[0])
            else:
                mins = float(mins_str) if mins_str else 0
            if mins > 0:
                played_games.append(g)
        except (ValueError, TypeError):
            # If we can't parse minutes, include the game anyway (might have other stats)
            if g.get("pts") is not None or g.get("reb") is not None:
                played_games.append(g)
    
    if not played_games:
        return {
            "l5": {"hit_rate": 0, "games_over": 0, "total_games": 0, "avg": 0},
            "l10": {"hit_rate": 0, "games_over": 0, "total_games": 0, "avg": 0},
            "season": {"hit_rate": 0, "games_over": 0, "total_games": 0, "avg": 0}
        }
    
    # CRITICAL: Sort by game date (most recent first)
    # BDL format: game.game.date = "2025-03-16"
    # BDL format: game.game_date = "Mar 16, 2025"
    from datetime import datetime
    
    def parse_game_date(game):
        """Parse game_date string to sortable value."""
        try:
            # Try BDL nested format first
            date_str = game.get("game", {}).get("date", "") if isinstance(game.get("game"), dict) else ""
            
            # Fallback to BDL flat format
            if not date_str:
                date_str = game.get("game_date", "") or game.get("date", "")
            
            if not date_str:
                return datetime.min
            
            # Handle formats: "Mar 16, 2025" or "2025-03-16"
            # Note: Don't truncate the string - parse the full date
            for fmt in ["%Y-%m-%d", "%b %d, %Y", "%B %d, %Y"]:
                try:
                    # For ISO format, only take first 10 chars
                    if fmt == "%Y-%m-%d":
                        return datetime.strptime(date_str[:10], fmt)
                    else:
                        # For text formats, parse the full string
                        return datetime.strptime(date_str.strip(), fmt)
                except ValueError:
                    continue
            return datetime.min
        except:
            return datetime.min
    
    played_games = sorted(played_games, key=parse_game_date, reverse=True)
    
    # Extract exact L5 and L10 game arrays
    l5_games = played_games[:5]
    l10_games = played_games[:10]
    
    # Calculate BOTH avg and hit_rate from the SAME arrays
    return {
        "l5": calculate_hit_rate_for_games(l5_games, fields, line_value),
        "l10": calculate_hit_rate_for_games(l10_games, fields, line_value),
        "season": calculate_hit_rate_for_games(played_games, fields, line_value)
    }


def calculate_hit_rates(player_stats: Dict, stat_type: str, line_value: float) -> Dict[str, Any]:
    """
    Calculate hit rates for a specific line.
    
    DEPRECATED: Use calculate_coupled_stats() for new code to ensure
    hit rate and average are calculated from the same data source.
    
    Args:
        player_stats: Dict containing "games" list
        stat_type: Type of stat (PTS, REB, AST, etc.)
        line_value: The prop line value
    
    Returns:
        Dict with l5, l10, and season hit rate breakdowns
    """
    games = player_stats.get("games", [])
    if not games:
        return {}
    
    # Normalize stat type
    normalized_type = stat_type
    norm_map = {"P+R": "PR", "P+A": "PA", "R+A": "RA"}
    if stat_type in norm_map:
        normalized_type = norm_map[stat_type]
    
    fields = STAT_FIELD_MAP.get(normalized_type) or STAT_FIELD_MAP.get(stat_type)
    if not fields:
        return {}
    
    # Sort games by date/gameID (most recent first)
    # BDL games have gameID like "20250115_LAL@DEN"
    sorted_games = sorted(games, key=lambda g: g.get("gameID", g.get("game", {}).get("date", "")), reverse=True)
    
    return {
        "l5": calculate_hit_rate_for_games(sorted_games[:5], fields, line_value),
        "l10": calculate_hit_rate_for_games(sorted_games[:10], fields, line_value),
        "season": calculate_hit_rate_for_games(sorted_games, fields, line_value)
    }


def calculate_heat_level(h10: float, h5: float, h10_over: int, h5_over: int, h10_games: int, h5_games: int) -> int:
    """
    Calculate Heat Level (1-5 Flames) based on performance:
    - 5 Flames: L10 >= 90% (9-10/10 games hit) - FIRE
    - 4 Flames: L10 >= 80% OR on perfect 5-game streak - HOT
    - 3 Flames: L10 >= 70% OR L5 >= 80% - WARM
    - 2 Flames: L10 >= 60% - MILD
    - 1 Flame:  L10 >= 50% - COOL
    - 0 Flames: L10 < 50% - COLD
    """
    # 5 Flames: 9-10 out of 10 games hit
    if h10_games >= 10 and h10_over >= 9:
        return 5
    if h10 >= 0.90:
        return 5
    
    # 4 Flames: 80%+ L10 OR perfect 5-game streak
    if h10 >= 0.80:
        return 4
    if h5_games >= 5 and h5_over == 5:  # Perfect last 5
        return 4
    
    # 3 Flames: 70%+ L10 OR 80%+ L5 (hot streak)
    if h10 >= 0.70:
        return 3
    if h5 >= 0.80:
        return 3
    if h5_games >= 3 and h5_over >= 3:  # 3+ game streak
        return 3
    
    # 2 Flames: 60%+ L10
    if h10 >= 0.60:
        return 2
    
    # 1 Flame: 50%+ L10
    if h10 >= 0.50:
        return 1
    
    # 0 Flames: Cold
    return 0


def calculate_safety_level(h10: float, h5: float, h10_over: int, h5_over: int, h10_games: int, h5_games: int) -> int:
    """
    Calculate Safety Level (1-5 Shields) based on consistency:
    - 5 Shields: Perfect 10/10 or 95%+ hit rate - FORTRESS
    - 4 Shields: 90%+ hit rate OR perfect 5/5 - VAULT
    - 3 Shields: 85%+ hit rate - SAFE
    - 2 Shields: 80%+ hit rate - RELIABLE
    - 1 Shield:  70%+ hit rate - MODERATE
    - 0 Shields: < 70% hit rate - RISKY
    """
    # 5 Shields: Perfect 10/10 or 95%+
    if h10_games >= 10 and h10_over == 10:
        return 5
    if h10 >= 0.95:
        return 5
    
    # 4 Shields: 90%+ OR perfect 5/5
    if h10 >= 0.90:
        return 4
    if h5_games >= 5 and h5_over == 5:
        return 4
    
    # 3 Shields: 85%+
    if h10 >= 0.85:
        return 3
    if h5 >= 0.90:
        return 3
    
    # 2 Shields: 80%+
    if h10 >= 0.80:
        return 2
    
    # 1 Shield: 70%+
    if h10 >= 0.70:
        return 1
    
    # 0 Shields: Below 70%
    return 0


def calculate_bullet_level(h10: float, h5: float, h10_over: int, h5_over: int, h10_games: int, h5_games: int) -> int:
    """
    Calculate Bullet Level (1-6 Bullets) based on reliability:
    - 6 Bullets: 85%+ hit rate - ELITE
    - 5 Bullets: 80%+ hit rate - STRONG
    - 4 Bullets: 75%+ hit rate - SOLID
    - 3 Bullets: 70%+ hit rate - GOOD
    - 2 Bullets: 65%+ hit rate - FAIR
    - 1 Bullet:  60%+ hit rate - BASE
    """
    if h10 >= 0.85:
        return 6
    if h10 >= 0.80:
        return 5
    if h10 >= 0.75:
        return 4
    if h10 >= 0.70:
        return 3
    if h10 >= 0.65:
        return 2
    return 1


def calculate_volatility(game_values: List[float]) -> Tuple[str, float]:
    """
    Calculate performance volatility based on game-to-game variance.
    
    Returns:
        Tuple of (volatility_label, volatility_score)
    """
    if not game_values or len(game_values) < 2:
        return ("unknown", 0.0)
    
    mean = sum(game_values) / len(game_values)
    variance = sum((x - mean) ** 2 for x in game_values) / len(game_values)
    std_dev = variance ** 0.5
    
    # Coefficient of variation
    cv = (std_dev / mean * 100) if mean > 0 else 0
    
    if cv < 20:
        return ("low", cv)
    elif cv < 40:
        return ("medium", cv)
    else:
        return ("high", cv)


def calculate_season_average(games: List[Dict], stat_type: str) -> float:
    """Calculate season average for a stat type"""
    if not games:
        return 0.0
    
    fields = STAT_FIELD_MAP.get(stat_type)
    if not fields:
        return 0.0
    
    values = [get_stat_value(g, fields) for g in games]
    return round(sum(values) / len(values), 1) if values else 0.0

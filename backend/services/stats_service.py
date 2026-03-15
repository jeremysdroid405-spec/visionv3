"""
Stats Service - Hit Rate and Statistical Calculations
======================================================
Extracted from demon_goblin_engine.py for modularity.
"""
from typing import Dict, Any, List, Tuple
import logging

logger = logging.getLogger(__name__)

# Stat type to BallDontLie field mapping
STAT_FIELD_MAP = {
    "PTS": "pts",
    "REB": "reb",
    "AST": "ast",
    "3PM": "fg3m",
    "BLK": "blk",
    "STL": "stl",
    "TO": "turnover",
    "P+R": ["pts", "reb"],
    "P+A": ["pts", "ast"],
    "R+A": ["reb", "ast"],
    "PRA": ["pts", "reb", "ast"]
}


def get_stat_value(game: Dict, fields) -> float:
    """Get combined stat value from a game"""
    if isinstance(fields, list):
        return sum(game.get(f, 0) or 0 for f in fields)
    return game.get(fields, 0) or 0


def calculate_hit_rate_for_games(games: List[Dict], fields, line_value: float) -> Dict[str, Any]:
    """Calculate hit rate for a list of games"""
    if not games:
        return {"hit_rate": 0, "games_over": 0, "total_games": 0, "avg": 0}
    
    values = [get_stat_value(g, fields) for g in games]
    games_over = sum(1 for v in values if v >= line_value)
    avg = sum(values) / len(values) if values else 0
    
    return {
        "hit_rate": games_over / len(games) if games else 0,
        "games_over": games_over,
        "total_games": len(games),
        "avg": round(avg, 1)
    }


def calculate_hit_rates(player_stats: Dict, stat_type: str, line_value: float) -> Dict[str, Any]:
    """
    Calculate hit rates for a specific line.
    
    Args:
        player_stats: Dict containing "games" list from BDL
        stat_type: Type of stat (PTS, REB, AST, etc.)
        line_value: The prop line value
    
    Returns:
        Dict with l5, l10, and season hit rate breakdowns
    """
    games = player_stats.get("games", [])
    if not games:
        return {}
    
    fields = STAT_FIELD_MAP.get(stat_type)
    if not fields:
        return {}
    
    # Sort games by date (most recent first)
    sorted_games = sorted(games, key=lambda g: g.get("game", {}).get("date", ""), reverse=True)
    
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

"""
DvP (Defense vs Position) Service
==================================
Calculates matchup modifiers based on opponent defensive rankings.
"""
from typing import Optional
from config.settings import DVP_RANKINGS, STAT_TYPE_MAP


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

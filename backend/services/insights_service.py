"""
Insights Service - Player Analytics and AI Summaries
=====================================================
Extracted from demon_goblin_engine.py for modularity.
"""
from typing import Dict, Any, List, Tuple, Optional
import statistics
import logging

from config.settings import (
    LEAGUE_AVG_PACE, VOLATILITY_HIGH_THRESHOLD, VOLATILITY_MED_THRESHOLD,
    USAGE_REDISTRIBUTION_BASE, TEAM_PACE, HIGH_USAGE_PLAYERS
)

logger = logging.getLogger(__name__)


def generate_insight_summary(
    player_name: str,
    pace_factor: float,
    usage_bump: float,
    volatility: str,
    days_rest: int,
    is_b2b: bool,
    is_3in4: bool,
    injured_teammates: List[str],
    opponent: str
) -> str:
    """Generate template-based insight summary prioritizing highest-impact factor."""
    insights = []
    
    # Priority 1: Usage Bump (most impactful)
    if usage_bump > 10:
        teammates_str = " & ".join(injured_teammates[:2])
        insights.append(f"Usage Spike: With {teammates_str} out, usage +{usage_bump:.0f}%")
    elif usage_bump > 5:
        teammates_str = injured_teammates[0] if injured_teammates else "teammate"
        insights.append(f"Usage Up: {teammates_str} out, +{usage_bump:.0f}% opportunity")
    
    # Priority 2: Schedule Fatigue
    if is_3in4:
        insights.append("3-in-4 Fatigue: -8% performance expected")
    elif is_b2b:
        insights.append("Back-to-Back: -5% fatigue factor")
    
    # Priority 3: Pace Matchup
    if pace_factor > 1.05:
        insights.append(f"Fast Pace vs {opponent}: +{(pace_factor-1)*100:.0f}% boost")
    elif pace_factor < 0.95:
        insights.append(f"Slow Pace vs {opponent}: {(pace_factor-1)*100:.0f}% drag")
    
    # Priority 4: Rest Advantage
    if days_rest >= 3:
        insights.append(f"{days_rest} Days Rest: Fresh legs advantage")
    
    # Priority 5: Volatility Warning
    if volatility == "High":
        insights.append("High Variance: Inconsistent, proceed with caution")
    
    if not insights:
        return "Standard projection. No significant modifiers."
    
    return " | ".join(insights[:2])


def calculate_confidence_rating(
    density_factor: float, 
    volatility: str, 
    sample_size: int
) -> int:
    """Calculate AI confidence rating (0-100)."""
    confidence = 70
    
    if density_factor < 0.95:
        confidence -= 10
    
    if volatility == "High":
        confidence -= 20
    elif volatility == "Med":
        confidence -= 10
    
    if sample_size >= 10:
        confidence += 10
    elif sample_size < 5:
        confidence -= 15
    
    return max(0, min(100, confidence))


def calculate_volatility(game_values: List[float]) -> Tuple[str, float]:
    """
    Calculate volatility score from recent game values.
    
    Returns:
        (volatility_label "Low"/"Med"/"High", standard_deviation)
    """
    if not game_values or len(game_values) < 3:
        return ("Low", 0.0)
    
    try:
        stddev = statistics.stdev(game_values)
        
        if stddev > VOLATILITY_HIGH_THRESHOLD:
            return ("High", round(stddev, 2))
        elif stddev > VOLATILITY_MED_THRESHOLD:
            return ("Med", round(stddev, 2))
        else:
            return ("Low", round(stddev, 2))
    except:
        return ("Low", 0.0)


def get_team_pace(team: str) -> float:
    """Get team's pace (possessions per 48 minutes)."""
    return TEAM_PACE.get(team, LEAGUE_AVG_PACE)


def get_high_usage_players(team: str) -> List[str]:
    """Get list of high-usage players (>25% usage rate) on a team."""
    return HIGH_USAGE_PLAYERS.get(team, [])


def calculate_usage_bump(
    player_name: str,
    team: str,
    injured_players: List[Dict],
    high_usage_players: Dict[str, List[str]] = None
) -> Tuple[float, List[str]]:
    """
    Calculate usage bump based on injured teammates.
    
    Returns:
        Tuple of (usage_bump_percentage, list_of_injured_teammates)
    """
    usage_bump = 0.0
    injured_teammates = []
    
    # Use config if not provided
    if high_usage_players is None:
        high_usage_players = HIGH_USAGE_PLAYERS
    
    # Get high usage players for this team
    team_high_usage = high_usage_players.get(team, [])
    
    for injury in injured_players:
        injury_team = injury.get("team", "")
        injury_player = injury.get("player_name", "")
        injury_status = injury.get("status", "").lower()
        
        # Skip if not same team or if the injured player is the target
        if injury_team != team or injury_player == player_name:
            continue
        
        # Check if injured player is high-usage
        if injury_player in team_high_usage:
            if "out" in injury_status or "doubtful" in injury_status:
                usage_bump += 8.0
                injured_teammates.append(injury_player)
            elif "questionable" in injury_status:
                usage_bump += 3.0
                injured_teammates.append(f"{injury_player} (Q)")
    
    return usage_bump, injured_teammates


def calculate_usage_bump_simple(
    player_name: str, 
    team: str,
    injured_teammates: List[str]
) -> Tuple[float, List[str]]:
    """
    Calculate usage bump when high-usage teammates are out.
    Simpler version that takes direct list of injured players.
    
    Returns:
        (usage_bump_percent, list of injured high-usage teammates)
    """
    high_usage = get_high_usage_players(team)
    
    # Find injured high-usage players (excluding current player)
    injured_stars = [p for p in injured_teammates if p in high_usage and p != player_name]
    
    if not injured_stars:
        return (0.0, [])
    
    # Calculate usage bump with diminishing returns
    usage_bump = 0.0
    for i, _ in enumerate(injured_stars):
        multiplier = 1.0 / (i + 1)
        usage_bump += USAGE_REDISTRIBUTION_BASE * multiplier
    
    return (round(usage_bump, 1), injured_stars)


def calculate_pace_factor(team: str, opponent: str, team_pace_data: Dict[str, float] = None) -> float:
    """
    Calculate pace factor for a matchup.
    
    Args:
        team: Player's team
        opponent: Opposing team
        team_pace_data: Dict of team -> pace value (uses config if None)
    
    Returns:
        Pace factor (1.0 = neutral, >1.0 = faster, <1.0 = slower)
    """
    # Use config if not provided
    if team_pace_data is None:
        team_pace_data = TEAM_PACE
    
    team_pace = team_pace_data.get(team, LEAGUE_AVG_PACE)
    opp_pace = team_pace_data.get(opponent, LEAGUE_AVG_PACE)
    
    # Average both teams' pace relative to league average
    combined_pace = (team_pace + opp_pace) / 2
    pace_factor = combined_pace / LEAGUE_AVG_PACE
    
    return round(pace_factor, 3)


def calculate_rest_metrics(
    last_game_date: Optional[str],
    schedule_dates: List[str]
) -> Tuple[int, bool, bool]:
    """
    Calculate rest-related metrics.
    
    Returns:
        Tuple of (days_rest, is_back_to_back, is_3_in_4)
    """
    from datetime import datetime, timedelta
    
    days_rest = 2  # Default
    is_b2b = False
    is_3in4 = False
    
    if not last_game_date:
        return days_rest, is_b2b, is_3in4
    
    try:
        # Parse last game date
        if isinstance(last_game_date, str):
            last_game = datetime.fromisoformat(last_game_date.replace('Z', '+00:00'))
        else:
            last_game = last_game_date
        
        today = datetime.now()
        days_rest = (today - last_game).days
        
        # Check back-to-back
        is_b2b = days_rest <= 1
        
        # Check 3-in-4 (would need schedule data)
        if schedule_dates and len(schedule_dates) >= 3:
            recent_games = schedule_dates[-4:]
            if len(recent_games) >= 3:
                # If 3 games in last 4 days
                is_3in4 = True
    except Exception:
        pass
    
    return days_rest, is_b2b, is_3in4


def calculate_density_factor(
    game_values: List[float],
    line: float
) -> float:
    """
    Calculate density factor - how clustered results are around the line.
    Higher = more predictable player.
    """
    if not game_values or not line:
        return 1.0
    
    # Calculate standard deviation as percentage of line
    mean = sum(game_values) / len(game_values)
    variance = sum((x - mean) ** 2 for x in game_values) / len(game_values)
    std_dev = variance ** 0.5
    
    # Density = how tight results are relative to the line
    # Low std_dev/line ratio = high density (predictable)
    if line > 0:
        coefficient_of_variation = std_dev / line
        density = max(0.5, min(1.5, 1.0 - coefficient_of_variation + 0.5))
    else:
        density = 1.0
    
    return round(density, 3)


def build_player_insights(
    player_name: str,
    team: str,
    opponent: str,
    game_stats: List[Dict],
    stat_type: str = "pts",
    injured_players: List[Dict] = None,
    team_pace_data: Dict[str, float] = None,
    high_usage_players: Dict[str, List[str]] = None
) -> Dict[str, Any]:
    """
    Build comprehensive player insights package.
    
    This is the main entry point for generating all player analytics.
    """
    injured_players = injured_players or []
    team_pace_data = team_pace_data or {}
    high_usage_players = high_usage_players or {}
    
    # Get game values for stat type
    stat_field_map = {
        "pts": "pts", "reb": "reb", "ast": "ast",
        "3pm": "fg3m", "blk": "blk", "stl": "stl"
    }
    field = stat_field_map.get(stat_type.lower(), "pts")
    game_values = [g.get(field, 0) or 0 for g in game_stats] if game_stats else []
    
    # Calculate all metrics
    pace_factor = calculate_pace_factor(team, opponent, team_pace_data)
    usage_bump, injured_teammates = calculate_usage_bump(
        player_name, team, injured_players, high_usage_players
    )
    
    # Get last game date for rest calculation
    last_game_date = None
    if game_stats:
        last_game_date = game_stats[0].get("game", {}).get("date")
    days_rest, is_b2b, is_3in4 = calculate_rest_metrics(last_game_date, [])
    
    # Calculate volatility
    from services.stats_service import calculate_volatility
    volatility_label, volatility_score = calculate_volatility(game_values)
    
    # Calculate density
    line = sum(game_values) / len(game_values) if game_values else 0
    density_factor = calculate_density_factor(game_values, line)
    
    # Generate summary
    summary = generate_insight_summary(
        player_name=player_name,
        pace_factor=pace_factor,
        usage_bump=usage_bump,
        volatility=volatility_label.capitalize(),
        days_rest=days_rest,
        is_b2b=is_b2b,
        is_3in4=is_3in4,
        injured_teammates=injured_teammates,
        opponent=opponent
    )
    
    # Calculate confidence
    confidence = calculate_confidence_rating(
        density_factor=density_factor,
        volatility=volatility_label.capitalize(),
        sample_size=len(game_stats)
    )
    
    return {
        "insight_summary": summary,
        "ai_confidence_rating": confidence,
        "pace_factor": pace_factor,
        "usage_bump": usage_bump,
        "injured_teammates": injured_teammates,
        "days_rest": days_rest,
        "is_back_to_back": is_b2b,
        "is_3_in_4": is_3in4,
        "volatility": volatility_label,
        "volatility_score": volatility_score,
        "density_factor": density_factor,
        "sample_size": len(game_stats)
    }

"""
MLB Tempo Math - Plate Appearance & Pitching Depth Probability Modifiers

This module calculates tempo-based adjustments for MLB player projections.
Tempo accounts for factors that affect the NUMBER of opportunities a player gets,
not their skill - batting order position, team offensive pace, pitcher efficiency, etc.

HITTER TEMPO: Modifies expected Plate Appearances
- Away teams get guaranteed 9th inning AB (baseline boost)
- Top of order maximizes PAs, bottom of order risks only 3 PAs
- High OBP teams create more lineup turnover

PITCHER TEMPO: Modifies expected Innings Pitched
- Efficient pitchers (low P/PA) go deeper into games
- Grinders (high P/PA) get early hooks
- Tired bullpens give starters a longer leash

Usage:
    from services.mlb_tempo_math import calculate_hitter_tempo, calculate_pitcher_tempo
    
    # For hitters
    tempo_mult = calculate_hitter_tempo(
        batting_order=2,      # Batting 2nd
        is_away_team=True,    # Playing away
        team_obp_rank=8       # Team is 8th in OBP (Top 10)
    )
    # Returns: 1.12 (12% boost = 3% away + 5% top order + 4% high OBP)
    
    # For pitchers
    tempo_mult = calculate_pitcher_tempo(
        pitcher_ppa=3.65,     # Pitches per PA (efficient)
        bullpen_rest_days=0   # Bullpen worked yesterday
    )
    # Returns: 1.13 (13% boost = 8% efficient + 5% tired bullpen)

Author: PropVision MLB Engine
Version: 2026.1
"""

import logging
from typing import Optional, Dict, Tuple

logger = logging.getLogger(__name__)


# =============================================================================
# HITTER TEMPO CONSTANTS
# =============================================================================

# Away team baseline adjustment (guaranteed 9th inning AB vs home team risk)
AWAY_TEAM_BOOST = 0.03       # +3% for away team
HOME_TEAM_PENALTY = -0.02    # -2% for home team

# Batting order position adjustments
BATTING_ORDER_MODIFIERS = {
    1: 0.05,   # Leadoff: Maximized PAs, sees most ABs
    2: 0.05,   # 2-hole: High PA count, often best hitter
    3: 0.05,   # 3-hole: Protection, still top PA count
    4: 0.00,   # Cleanup: Neutral - power slot but fewer PAs than top
    5: 0.00,   # 5-hole: Neutral
    6: -0.08,  # 6-hole: High risk of only 3 PAs in low-scoring games
    7: -0.08,  # 7-hole: Same risk
    8: -0.08,  # 8-hole: Same risk (or pitcher spot in NL)
    9: -0.08,  # 9-hole: Fewest guaranteed PAs
}

# Team OBP rank thresholds for lineup turnover bonus
OBP_RANK_TOP_10_BONUS = 0.04    # +4% for Top 10 OBP teams (more lineup turnover)
OBP_RANK_BOTTOM_10_PENALTY = -0.04  # -4% for Bottom 10 OBP teams


# =============================================================================
# PITCHER TEMPO CONSTANTS
# =============================================================================

# Pitches per Plate Appearance (P/PA) thresholds
PPA_EFFICIENT_THRESHOLD = 3.8   # Below this = efficient pitcher
PPA_GRINDER_THRESHOLD = 4.2     # Above this = grinder/nibbler

PPA_EFFICIENT_BOOST = 0.08      # +8% for efficient pitchers (go deeper)
PPA_GRINDER_PENALTY = -0.10     # -10% for grinders (early hook risk)

# Bullpen rest adjustment
BULLPEN_NO_REST_BOOST = 0.05    # +5% if bullpen had 0 rest days (longer leash)


# =============================================================================
# HITTER TEMPO FUNCTION
# =============================================================================

def calculate_hitter_tempo(
    batting_order: Optional[int],
    is_away_team: Optional[bool],
    team_obp_rank: Optional[int]
) -> float:
    """
    Calculate tempo multiplier for hitter projections based on PA opportunity factors.
    
    This modifier adjusts the RAW projection based on factors that affect
    how many plate appearances the hitter is likely to get.
    
    Args:
        batting_order: Position in lineup (1-9). None defaults to neutral (5).
        is_away_team: True if playing away, False if home. None defaults to neutral.
        team_obp_rank: Team's OBP ranking (1-30). None skips this modifier.
        
    Returns:
        float: Tempo multiplier (e.g., 1.12 = 12% boost, 0.90 = 10% penalty)
        
    Example:
        >>> calculate_hitter_tempo(batting_order=2, is_away_team=True, team_obp_rank=5)
        1.12  # +3% away + 5% top order + 4% top OBP = +12%
    """
    total_modifier = 0.0
    breakdown = []
    
    # 1. Away Team Boost / Home Team Penalty
    if is_away_team is True:
        total_modifier += AWAY_TEAM_BOOST
        breakdown.append(f"Away: +{AWAY_TEAM_BOOST*100:.0f}%")
    elif is_away_team is False:
        total_modifier += HOME_TEAM_PENALTY
        breakdown.append(f"Home: {HOME_TEAM_PENALTY*100:.0f}%")
    
    # 2. Batting Order Position
    if batting_order is not None:
        # Clamp to valid range 1-9
        order_slot = max(1, min(9, batting_order))
        order_mod = BATTING_ORDER_MODIFIERS.get(order_slot, 0.0)
        total_modifier += order_mod
        if order_mod != 0:
            breakdown.append(f"Order {order_slot}: {order_mod*100:+.0f}%")
    
    # 3. Team OBP Rank (Lineup Turnover)
    if team_obp_rank is not None:
        if team_obp_rank <= 10:
            total_modifier += OBP_RANK_TOP_10_BONUS
            breakdown.append(f"OBP Top 10: +{OBP_RANK_TOP_10_BONUS*100:.0f}%")
        elif team_obp_rank >= 21:  # Bottom 10 (ranks 21-30)
            total_modifier += OBP_RANK_BOTTOM_10_PENALTY
            breakdown.append(f"OBP Bot 10: {OBP_RANK_BOTTOM_10_PENALTY*100:.0f}%")
    
    # Convert to multiplier (1.0 = no change)
    multiplier = 1.0 + total_modifier
    
    # Log the breakdown for debugging
    if breakdown:
        logger.debug(f"[HITTER_TEMPO] {' | '.join(breakdown)} → {multiplier:.3f}x")
    
    return round(multiplier, 4)


def get_hitter_tempo_breakdown(
    batting_order: Optional[int],
    is_away_team: Optional[bool],
    team_obp_rank: Optional[int]
) -> Dict:
    """
    Get detailed breakdown of hitter tempo calculation.
    Useful for displaying in UI or debugging.
    
    Returns:
        Dict with 'multiplier', 'total_pct', and 'factors' breakdown
    """
    factors = []
    total_pct = 0.0
    
    # Away/Home
    if is_away_team is True:
        factors.append({"name": "Away Team", "value": AWAY_TEAM_BOOST * 100, "reason": "Guaranteed 9th inning AB"})
        total_pct += AWAY_TEAM_BOOST * 100
    elif is_away_team is False:
        factors.append({"name": "Home Team", "value": HOME_TEAM_PENALTY * 100, "reason": "Risk of no 9th inning"})
        total_pct += HOME_TEAM_PENALTY * 100
    
    # Batting Order
    if batting_order is not None:
        order_slot = max(1, min(9, batting_order))
        order_mod = BATTING_ORDER_MODIFIERS.get(order_slot, 0.0)
        if order_mod > 0:
            reason = "Maximized PAs"
        elif order_mod < 0:
            reason = "Risk of only 3 PAs"
        else:
            reason = "Neutral position"
        factors.append({"name": f"Batting {order_slot}", "value": order_mod * 100, "reason": reason})
        total_pct += order_mod * 100
    
    # OBP Rank
    if team_obp_rank is not None:
        if team_obp_rank <= 10:
            factors.append({"name": f"OBP Rank #{team_obp_rank}", "value": OBP_RANK_TOP_10_BONUS * 100, "reason": "High lineup turnover"})
            total_pct += OBP_RANK_TOP_10_BONUS * 100
        elif team_obp_rank >= 21:
            factors.append({"name": f"OBP Rank #{team_obp_rank}", "value": OBP_RANK_BOTTOM_10_PENALTY * 100, "reason": "Low lineup turnover"})
            total_pct += OBP_RANK_BOTTOM_10_PENALTY * 100
    
    return {
        "multiplier": round(1.0 + (total_pct / 100), 4),
        "total_pct": round(total_pct, 1),
        "factors": factors
    }


# =============================================================================
# PITCHER TEMPO FUNCTION
# =============================================================================

def calculate_pitcher_tempo(
    pitcher_ppa: Optional[float],
    bullpen_rest_days: Optional[int]
) -> float:
    """
    Calculate tempo multiplier for pitcher projections based on workload factors.
    
    This modifier adjusts the RAW projection based on factors that affect
    how deep the pitcher is likely to go into the game.
    
    Args:
        pitcher_ppa: Pitches per Plate Appearance (season avg). None skips this modifier.
        bullpen_rest_days: Days since bullpen last worked (0 = worked yesterday). None skips.
        
    Returns:
        float: Tempo multiplier (e.g., 1.13 = 13% boost, 0.90 = 10% penalty)
        
    Example:
        >>> calculate_pitcher_tempo(pitcher_ppa=3.65, bullpen_rest_days=0)
        1.13  # +8% efficient + 5% tired bullpen = +13%
    """
    total_modifier = 0.0
    breakdown = []
    
    # 1. Pitches per Plate Appearance (P/PA) - Efficiency Check
    if pitcher_ppa is not None:
        if pitcher_ppa < PPA_EFFICIENT_THRESHOLD:
            # Efficient pitcher - goes deep into games
            total_modifier += PPA_EFFICIENT_BOOST
            breakdown.append(f"P/PA {pitcher_ppa:.2f} (Efficient): +{PPA_EFFICIENT_BOOST*100:.0f}%")
        elif pitcher_ppa > PPA_GRINDER_THRESHOLD:
            # Grinder - high early hook risk
            total_modifier += PPA_GRINDER_PENALTY
            breakdown.append(f"P/PA {pitcher_ppa:.2f} (Grinder): {PPA_GRINDER_PENALTY*100:.0f}%")
        else:
            breakdown.append(f"P/PA {pitcher_ppa:.2f} (Neutral)")
    
    # 2. Bullpen Rest Days
    if bullpen_rest_days is not None:
        if bullpen_rest_days == 0:
            # Bullpen worked yesterday - starter gets longer leash
            total_modifier += BULLPEN_NO_REST_BOOST
            breakdown.append(f"Bullpen 0 Rest: +{BULLPEN_NO_REST_BOOST*100:.0f}%")
    
    # Convert to multiplier (1.0 = no change)
    multiplier = 1.0 + total_modifier
    
    # Log the breakdown for debugging
    if breakdown:
        logger.debug(f"[PITCHER_TEMPO] {' | '.join(breakdown)} → {multiplier:.3f}x")
    
    return round(multiplier, 4)


def get_pitcher_tempo_breakdown(
    pitcher_ppa: Optional[float],
    bullpen_rest_days: Optional[int]
) -> Dict:
    """
    Get detailed breakdown of pitcher tempo calculation.
    Useful for displaying in UI or debugging.
    
    Returns:
        Dict with 'multiplier', 'total_pct', and 'factors' breakdown
    """
    factors = []
    total_pct = 0.0
    
    # P/PA Efficiency
    if pitcher_ppa is not None:
        if pitcher_ppa < PPA_EFFICIENT_THRESHOLD:
            factors.append({
                "name": f"P/PA {pitcher_ppa:.2f}",
                "value": PPA_EFFICIENT_BOOST * 100,
                "reason": "Efficient - high chance of going deep"
            })
            total_pct += PPA_EFFICIENT_BOOST * 100
        elif pitcher_ppa > PPA_GRINDER_THRESHOLD:
            factors.append({
                "name": f"P/PA {pitcher_ppa:.2f}",
                "value": PPA_GRINDER_PENALTY * 100,
                "reason": "Grinder - high early hook risk"
            })
            total_pct += PPA_GRINDER_PENALTY * 100
        else:
            factors.append({
                "name": f"P/PA {pitcher_ppa:.2f}",
                "value": 0,
                "reason": "Average efficiency"
            })
    
    # Bullpen Rest
    if bullpen_rest_days is not None:
        if bullpen_rest_days == 0:
            factors.append({
                "name": "Bullpen Rest",
                "value": BULLPEN_NO_REST_BOOST * 100,
                "reason": "Bullpen worked yesterday - longer leash"
            })
            total_pct += BULLPEN_NO_REST_BOOST * 100
        else:
            factors.append({
                "name": "Bullpen Rest",
                "value": 0,
                "reason": f"{bullpen_rest_days} days rest - normal usage"
            })
    
    return {
        "multiplier": round(1.0 + (total_pct / 100), 4),
        "total_pct": round(total_pct, 1),
        "factors": factors
    }


# =============================================================================
# COMBINED TEMPO CALCULATION
# =============================================================================

def calculate_tempo_multiplier(
    is_pitcher: bool,
    batting_order: Optional[int] = None,
    is_away_team: Optional[bool] = None,
    team_obp_rank: Optional[int] = None,
    pitcher_ppa: Optional[float] = None,
    bullpen_rest_days: Optional[int] = None
) -> Tuple[float, Dict]:
    """
    Calculate the appropriate tempo multiplier based on player type.
    
    Args:
        is_pitcher: True for pitchers, False for hitters
        batting_order: (Hitters only) Position in lineup 1-9
        is_away_team: True if playing away
        team_obp_rank: (Hitters only) Team's OBP ranking 1-30
        pitcher_ppa: (Pitchers only) Pitches per PA
        bullpen_rest_days: (Pitchers only) Days since bullpen worked
        
    Returns:
        Tuple of (multiplier: float, breakdown: Dict)
    """
    if is_pitcher:
        multiplier = calculate_pitcher_tempo(pitcher_ppa, bullpen_rest_days)
        breakdown = get_pitcher_tempo_breakdown(pitcher_ppa, bullpen_rest_days)
    else:
        multiplier = calculate_hitter_tempo(batting_order, is_away_team, team_obp_rank)
        breakdown = get_hitter_tempo_breakdown(batting_order, is_away_team, team_obp_rank)
    
    return multiplier, breakdown


# =============================================================================
# QUICK VALIDATION
# =============================================================================

if __name__ == "__main__":
    # Test hitter tempo
    print("=== HITTER TEMPO TESTS ===")
    
    # Best case: Away team, batting 2nd, top OBP team
    mult = calculate_hitter_tempo(batting_order=2, is_away_team=True, team_obp_rank=5)
    print(f"Best Case (Away, #2, Top OBP): {mult:.3f}x (+{(mult-1)*100:.0f}%)")
    
    # Worst case: Home team, batting 8th, bottom OBP team
    mult = calculate_hitter_tempo(batting_order=8, is_away_team=False, team_obp_rank=25)
    print(f"Worst Case (Home, #8, Bot OBP): {mult:.3f}x ({(mult-1)*100:.0f}%)")
    
    # Neutral
    mult = calculate_hitter_tempo(batting_order=5, is_away_team=None, team_obp_rank=15)
    print(f"Neutral Case: {mult:.3f}x")
    
    print("\n=== PITCHER TEMPO TESTS ===")
    
    # Best case: Efficient pitcher, tired bullpen
    mult = calculate_pitcher_tempo(pitcher_ppa=3.5, bullpen_rest_days=0)
    print(f"Best Case (P/PA 3.5, 0 rest): {mult:.3f}x (+{(mult-1)*100:.0f}%)")
    
    # Worst case: Grinder, rested bullpen
    mult = calculate_pitcher_tempo(pitcher_ppa=4.5, bullpen_rest_days=2)
    print(f"Worst Case (P/PA 4.5, 2 rest): {mult:.3f}x ({(mult-1)*100:.0f}%)")
    
    # Neutral
    mult = calculate_pitcher_tempo(pitcher_ppa=4.0, bullpen_rest_days=1)
    print(f"Neutral Case: {mult:.3f}x")

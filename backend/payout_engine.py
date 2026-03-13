"""
PAYOUT CALCULATION ENGINE
=========================

PrizePicks-style cumulative payout calculation based on leg-level modifiers.

ASSET TYPES:
- Standard: 1.0x modifier (normal line)
- Demon: 1.1x - 1.5x modifier (harder line, higher reward)
- Goblin: 0.7x - 0.9x modifier (easier line, lower reward)

FORMULA:
Total Payout = Base Multiplier × (Mod_1 × Mod_2 × ... × Mod_n)

BASE MULTIPLIERS (per number of picks):
- 2 picks: 3.0x base
- 3 picks: 5.0x base  
- 4 picks: 10.0x base
- 5 picks: 20.0x base
- 6 picks: 40.0x base
"""

from typing import List, Dict, Any, Tuple
from dataclasses import dataclass
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class AssetType(Enum):
    STANDARD = "standard"
    DEMON = "demon"
    GOBLIN = "goblin"


@dataclass
class LegModifier:
    """Represents a single leg's payout modifier."""
    player_name: str
    stat_type: str
    line: float
    direction: str
    asset_type: AssetType
    modifier: float
    team: str = ""


# Base multipliers by number of picks (PrizePicks style - Standard/Demon lines at +100 odds)
BASE_MULTIPLIERS = {
    2: 3.0,    # 2-pick: 3x base
    3: 5.0,    # 3-pick: 5x base
    4: 10.0,   # 4-pick: 10x base
    5: 20.0,   # 5-pick: 20x base
    6: 40.0,   # 6-pick: 40x base
}

# Base multipliers for Goblin-heavy parlays (picks at -137 to -150 odds)
# These reflect actual PrizePicks payouts for "safer" picks
GOBLIN_BASE_MULTIPLIERS = {
    2: 1.4,    # 2-pick goblin: ~1.4x (actual PrizePicks payout)
    3: 2.0,    # 3-pick goblin: ~2x
    4: 3.0,    # 4-pick goblin: ~3x
    5: 5.0,    # 5-pick goblin: ~5x
    6: 8.0,    # 6-pick goblin: ~8x (Flex: 5/6=1.25x, 6/6=8x)
}

# Modifier ranges by asset type
MODIFIER_RANGES = {
    AssetType.DEMON: (1.10, 1.50),      # 10-50% boost for harder lines
    AssetType.STANDARD: (0.95, 1.05),   # Near 1.0 for standard lines
    AssetType.GOBLIN: (0.85, 1.0),      # Slight reduction for easier lines (base already lower)
}


def calculate_leg_modifier(
    standard_line: float,
    actual_line: float,
    direction: str,
    is_demon: bool = False,
    is_goblin: bool = False
) -> Tuple[AssetType, float]:
    """
    Calculate the payout modifier for a single leg.
    
    Args:
        standard_line: The market's standard/average line
        actual_line: The actual line being played
        direction: 'over' or 'under'
        is_demon: Flag if this is a demon line
        is_goblin: Flag if this is a goblin line
    
    Returns:
        Tuple of (AssetType, modifier)
    """
    if standard_line <= 0:
        return AssetType.STANDARD, 1.0
    
    # Calculate gap ratio
    gap_ratio = actual_line / standard_line
    
    if is_demon or (direction == "over" and gap_ratio > 1.10):
        # Demon: Line is higher than standard (harder to hit over)
        # Modifier increases with difficulty
        asset_type = AssetType.DEMON
        # Scale from 1.10 to 1.50 based on gap
        modifier = min(1.50, max(1.10, 1.0 + (gap_ratio - 1.0) * 0.5))
        
    elif is_goblin or (direction == "over" and gap_ratio < 0.90):
        # Goblin: Line is lower than standard (easier to hit over)
        # Modifier decreases with ease
        asset_type = AssetType.GOBLIN
        # Scale from 0.70 to 0.90 based on gap
        modifier = max(0.70, min(0.90, gap_ratio))
        
    else:
        # Standard: Line is close to market standard
        asset_type = AssetType.STANDARD
        modifier = 1.0
    
    return asset_type, round(modifier, 2)


def calculate_parlay_payout(legs: List[LegModifier], use_goblin_base: bool = False) -> Dict[str, Any]:
    """
    Calculate the total payout for a parlay.
    
    Formula: Total Payout = Base Multiplier × (Mod_1 × Mod_2 × ... × Mod_n)
    
    Args:
        legs: List of LegModifier objects
        use_goblin_base: If True, use GOBLIN_BASE_MULTIPLIERS (for Safe Haven/Goblin Recon)
    
    Returns:
        Dict with payout calculation details
    """
    num_picks = len(legs)
    
    if num_picks < 2:
        return {
            "error": "Minimum 2 picks required",
            "estimated_payout": 0,
            "legs": []
        }
    
    if num_picks > 6:
        return {
            "error": "Maximum 6 picks allowed",
            "estimated_payout": 0,
            "legs": []
        }
    
    # Calculate cumulative modifier and count asset types first
    cumulative_modifier = 1.0
    leg_details = []
    
    demon_count = 0
    goblin_count = 0
    standard_count = 0
    
    for leg in legs:
        cumulative_modifier *= leg.modifier
        
        if leg.asset_type == AssetType.DEMON:
            demon_count += 1
        elif leg.asset_type == AssetType.GOBLIN:
            goblin_count += 1
        else:
            standard_count += 1
        
        leg_details.append({
            "player_name": leg.player_name,
            "stat_type": leg.stat_type,
            "line": leg.line,
            "direction": leg.direction,
            "team": leg.team,
            "asset_type": leg.asset_type.value,
            "modifier": leg.modifier,
            "modifier_display": f"{leg.modifier:.2f}x"
        })
    
    # Determine which base multiplier to use
    # Use goblin base if explicitly requested OR if majority are goblins
    is_goblin_heavy = goblin_count > (demon_count + standard_count)
    if use_goblin_base or is_goblin_heavy:
        base_multiplier = GOBLIN_BASE_MULTIPLIERS.get(num_picks, 1.4)
        payout_type = "goblin"
    else:
        base_multiplier = BASE_MULTIPLIERS.get(num_picks, 3.0)
        payout_type = "standard"
    
    # Calculate final payout
    estimated_payout = base_multiplier * cumulative_modifier
    
    return {
        "num_picks": num_picks,
        "base_multiplier": base_multiplier,
        "cumulative_modifier": round(cumulative_modifier, 3),
        "estimated_payout": round(estimated_payout, 2),
        "payout_display": f"{estimated_payout:.1f}x",
        "payout_type": payout_type,
        "asset_breakdown": {
            "demons": demon_count,
            "goblins": goblin_count,
            "standards": standard_count
        },
        "legs": leg_details
    }


def calculate_payout_from_picks(picks: List[Dict[str, Any]], use_goblin_base: bool = False) -> Dict[str, Any]:
    """
    Calculate payout from a list of pick dictionaries.
    
    This is the main function to use for existing pick data.
    
    Args:
        picks: List of pick dicts with keys like:
               - player_name, stat_type, line, direction
               - is_demon, is_goblin, standard_line (optional)
               - demon_line, goblin_line (optional)
        use_goblin_base: If True, use GOBLIN_BASE_MULTIPLIERS (for Safe Haven/Goblin Recon)
    
    Returns:
        Dict with payout calculation
    """
    legs = []
    
    for pick in picks:
        player_name = pick.get("player_name", "Unknown")
        stat_type = pick.get("stat_type", pick.get("prop_type", "PTS"))
        direction = pick.get("direction", "over")
        team = pick.get("team", "")
        
        # Determine the actual line being played
        actual_line = pick.get("line", 0)
        if pick.get("demon_line"):
            actual_line = pick.get("demon_line")
        elif pick.get("goblin_line"):
            actual_line = pick.get("goblin_line")
        
        # Get standard line for comparison
        standard_line = pick.get("standard_line", actual_line)
        
        # Determine asset type and modifier
        is_demon = pick.get("is_demon", False)
        is_goblin = pick.get("is_goblin", False)
        
        asset_type, modifier = calculate_leg_modifier(
            standard_line=standard_line,
            actual_line=actual_line,
            direction=direction,
            is_demon=is_demon,
            is_goblin=is_goblin
        )
        
        legs.append(LegModifier(
            player_name=player_name,
            stat_type=stat_type,
            line=actual_line,
            direction=direction,
            asset_type=asset_type,
            modifier=modifier,
            team=team
        ))
    
    return calculate_parlay_payout(legs, use_goblin_base=use_goblin_base)


# Quick payout estimate functions
def estimate_payout(num_picks: int, demon_count: int = 0, goblin_count: int = 0, use_goblin_base: bool = False) -> float:
    """
    Quick estimate of payout based on pick composition.
    
    Args:
        num_picks: Total number of picks
        demon_count: Number of demon picks
        goblin_count: Number of goblin picks
        use_goblin_base: If True, use goblin base multipliers
    
    Returns:
        Estimated payout multiplier
    """
    if num_picks < 2 or num_picks > 6:
        return 0.0
    
    # Use appropriate base multiplier
    is_goblin_heavy = goblin_count > (demon_count + (num_picks - demon_count - goblin_count))
    if use_goblin_base or is_goblin_heavy:
        base = GOBLIN_BASE_MULTIPLIERS.get(num_picks, 1.4)
    else:
        base = BASE_MULTIPLIERS.get(num_picks, 3.0)
    
    # Average modifiers (adjusted for new goblin range)
    demon_mod = 1.25  # Average demon modifier
    goblin_mod = 0.92  # Average goblin modifier (now 0.85-1.0 range)
    standard_mod = 1.0
    
    standard_count = num_picks - demon_count - goblin_count
    
    cumulative = (
        (demon_mod ** demon_count) *
        (goblin_mod ** goblin_count) *
        (standard_mod ** standard_count)
    )
    
    return round(base * cumulative, 2)


# Examples for reference - Updated with realistic goblin payouts
EXAMPLE_PAYOUTS = {
    "2_pick_standard": estimate_payout(2, 0, 0),                    # 3.0x
    "2_pick_all_demons": estimate_payout(2, 2, 0),                  # ~4.7x
    "2_pick_all_goblins": estimate_payout(2, 0, 2, use_goblin_base=True),  # ~1.2x (realistic)
    "2_pick_mixed": estimate_payout(2, 1, 1),                       # ~3.5x
    "3_pick_standard": estimate_payout(3, 0, 0),                    # 5.0x
    "3_pick_all_demons": estimate_payout(3, 3, 0),                  # ~9.8x
    "3_pick_all_goblins": estimate_payout(3, 0, 3, use_goblin_base=True),  # ~1.6x (realistic)
    "4_pick_standard": estimate_payout(4, 0, 0),                    # 10.0x
    "6_pick_standard": estimate_payout(6, 0, 0),                    # 40.0x
    "6_pick_all_demons": estimate_payout(6, 6, 0),                  # ~152x
    "6_pick_all_goblins": estimate_payout(6, 0, 6, use_goblin_base=True),  # ~5x (realistic)
}


if __name__ == "__main__":
    # Test the payout engine
    print("=== PAYOUT ENGINE TEST ===\n")
    
    print("Example payouts by composition:")
    for name, payout in EXAMPLE_PAYOUTS.items():
        print(f"  {name}: {payout}x")
    
    print("\nTest: Mixed 2-pick (1 Demon + 1 Goblin)")
    test_picks = [
        {"player_name": "LeBron James", "stat_type": "PTS", "line": 30.5, "standard_line": 27.5, "direction": "over", "is_demon": True, "team": "LAL"},
        {"player_name": "Stephen Curry", "stat_type": "PTS", "line": 22.5, "standard_line": 26.5, "direction": "over", "is_goblin": True, "team": "GSW"},
    ]
    result = calculate_payout_from_picks(test_picks)
    print(f"  Payout: {result['payout_display']}")
    print(f"  Breakdown: {result['asset_breakdown']}")

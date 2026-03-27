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

# Goblin/Safe Haven payout formula: 1.2^n (actual PrizePicks payouts for -137 odds picks)
# 2-pick: 1.2^2 = 1.44 → ~1.4x
# 3-pick: 1.2^3 = 1.73 → ~1.7x
# 4-pick: 1.2^4 = 2.07 → ~2.1x
# 5-pick: 1.2^5 = 2.49 → ~2.5x
# 6-pick: 1.2^6 = 2.99 → ~3.0x
GOBLIN_LEG_MULTIPLIER = 1.2

def calculate_goblin_base(num_picks: int) -> float:
    """Calculate goblin payout using actual PrizePicks formula: 1.2^n"""
    return round(GOBLIN_LEG_MULTIPLIER ** num_picks, 2)

# Demon/Gauntlet payout - PrizePicks uses progressive scaling
# Actual payouts from user:
# - 2-pick: 3.75x → Base 3.0 × 1.25 = 3.75 ✓
# - 6-pick: 109x → Base 40.0 × 2.725 = 109 ✓
#
# The cumulative modifier seems to follow: modifier ≈ 1.12 + (num_picks * 0.01)
# Or approximately: cumulative = 1.25 for 2-pick, scaling up to 2.725 for 6-pick
#
# Simplified: Use 1.18 per leg but slightly boost the base for demons
DEMON_LEG_MODIFIER = 1.18

# Demon base multipliers (slightly higher than standard to match actual payouts)
# These are tuned to match actual PrizePicks demon payouts
DEMON_BASE_MULTIPLIERS = {
    2: 2.7,    # 2.7 × 1.18² = 3.76x ≈ 3.75x ✓
    3: 4.8,    # 4.8 × 1.18³ = 7.89x
    4: 9.0,    # 9.0 × 1.18⁴ = 17.45x
    5: 18.0,   # 18.0 × 1.18⁵ = 41.1x
    6: 40.0,   # 40.0 × 1.18⁶ = 108x ≈ 109x ✓
}

# Modifier ranges by asset type (used for dynamic calculation when needed)
MODIFIER_RANGES = {
    AssetType.DEMON: (1.10, 1.20),      # 10-20% boost for harder lines
    AssetType.STANDARD: (0.95, 1.05),   # Near 1.0 for standard lines
    AssetType.GOBLIN: (0.95, 1.05),     # Near 1.0 (base already accounts for goblin odds)
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
    
    # If explicitly marked as demon or goblin, use fixed modifiers
    if is_demon:
        return AssetType.DEMON, DEMON_LEG_MODIFIER
    
    if is_goblin:
        return AssetType.GOBLIN, 1.0  # Goblin modifier is in the base calculation
    
    # Calculate gap ratio for auto-detection
    gap_ratio = actual_line / standard_line
    
    if direction == "over" and gap_ratio > 1.10:
        # Demon: Line is higher than standard (harder to hit over)
        return AssetType.DEMON, DEMON_LEG_MODIFIER
        
    elif direction == "over" and gap_ratio < 0.90:
        # Goblin: Line is lower than standard (easier to hit over)
        return AssetType.GOBLIN, 1.0
        
    else:
        # Standard: Line is close to market standard
        return AssetType.STANDARD, 1.0


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
    is_demon_heavy = demon_count > (goblin_count + standard_count)
    
    if use_goblin_base or is_goblin_heavy:
        # Use actual PrizePicks formula: 1.2^n
        base_multiplier = calculate_goblin_base(num_picks)
        payout_type = "goblin"
    elif is_demon_heavy:
        # Use demon base multipliers tuned to actual PrizePicks payouts
        base_multiplier = DEMON_BASE_MULTIPLIERS.get(num_picks, BASE_MULTIPLIERS.get(num_picks, 3.0))
        payout_type = "demon"
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
    
    standard_count = num_picks - demon_count - goblin_count
    
    # Use appropriate base multiplier
    is_goblin_heavy = goblin_count > (demon_count + standard_count)
    is_demon_heavy = demon_count > (goblin_count + standard_count)
    
    if use_goblin_base or is_goblin_heavy:
        # Use actual PrizePicks formula: 1.2^n
        base = calculate_goblin_base(num_picks)
    elif is_demon_heavy:
        # Use demon base multipliers
        base = DEMON_BASE_MULTIPLIERS.get(num_picks, BASE_MULTIPLIERS.get(num_picks, 3.0))
    else:
        base = BASE_MULTIPLIERS.get(num_picks, 3.0)
    
    # Use the fixed demon modifier constant
    demon_mod = DEMON_LEG_MODIFIER  # 1.18 per demon leg
    goblin_mod = 1.0   # Goblin modifier is ~1.0 (base already accounts for odds)
    standard_mod = 1.0
    
    cumulative = (
        (demon_mod ** demon_count) *
        (goblin_mod ** goblin_count) *
        (standard_mod ** standard_count)
    )
    
    return round(base * cumulative, 2)


# Examples for reference - Updated with actual PrizePicks payouts
# Demon: Demon Base × 1.18^n | Goblin: 1.2^n
EXAMPLE_PAYOUTS = {
    "2_pick_standard": estimate_payout(2, 0, 0),                              # 3.0x
    "2_pick_all_demons": estimate_payout(2, 2, 0),                            # 3.0 × 1.15² = 3.97x → ~3.75x actual
    "2_pick_all_goblins": estimate_payout(2, 0, 2, use_goblin_base=True),    # 1.2² = 1.44x → ~1.4x actual
    "3_pick_standard": estimate_payout(3, 0, 0),                              # 5.0x
    "3_pick_all_demons": estimate_payout(3, 3, 0),                            # 5.0 × 1.15³ = 7.6x
    "3_pick_all_goblins": estimate_payout(3, 0, 3, use_goblin_base=True),    # 1.2³ = 1.73x → ~1.7x actual
    "4_pick_standard": estimate_payout(4, 0, 0),                              # 10.0x
    "4_pick_all_goblins": estimate_payout(4, 0, 4, use_goblin_base=True),    # 1.2⁴ = 2.07x → ~2.1x actual
    "6_pick_standard": estimate_payout(6, 0, 0),                              # 40.0x
    "6_pick_all_demons": estimate_payout(6, 6, 0),                            # 40.0 × 1.15⁶ = 92.5x → ~109x actual
    "6_pick_all_goblins": estimate_payout(6, 0, 6, use_goblin_base=True),    # 1.2⁶ = 2.99x → ~3.0x actual
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

"""
Vision Score Calculator
=======================
Calculates AI-weighted Vision_Score (0-100) for each prop.

Weights:
- Hit Rate (L10): 60%
- DvP (Defense vs Position): 25%
- Status Badges: 15%
"""

import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

# DvP scoring: Top-5 defenses penalize, Bottom-5 boost
# Rank 1-5: penalty (-10 to -2), Rank 6-25: neutral, Rank 26-30: boost (+2 to +10)
DVP_SCORE_MAP = {
    1: -10, 2: -8, 3: -6, 4: -4, 5: -2,  # Top defenses (hardest matchups)
    6: 0, 7: 0, 8: 0, 9: 0, 10: 0,
    11: 0, 12: 0, 13: 0, 14: 0, 15: 0,
    16: 0, 17: 0, 18: 0, 19: 0, 20: 0,
    21: 0, 22: 0, 23: 0, 24: 0, 25: 0,
    26: 2, 27: 4, 28: 6, 29: 8, 30: 10   # Worst defenses (easiest matchups)
}

# Badge scoring: Positive badges add, Negative badges subtract
BADGE_SCORES = {
    # Positive badges
    "locked_in": 5,       # Hot streak
    "home_cookin": 3,     # Home advantage
    "pay_day": 4,         # Contract year motivation
    "revenge": 3,         # Playing former team
    "milestone": 2,       # Near career milestone
    
    # Negative badges
    "injured": -8,        # Currently injured/limited
    "gassed": -5,         # Back-to-back fatigue
    "jet_lag": -4,        # Travel fatigue
    "deep_water": -3,     # High-pressure situation (could go either way)
    "distraction": -4,    # Off-court issues
    "legal_noise": -6,    # Legal problems
}


def calculate_vision_score(
    h10_rate: float,
    dvp_rank: Optional[int] = None,
    active_badges: Optional[List[str]] = None,
    is_demon: bool = False,
    is_goblin: bool = False
) -> Dict[str, Any]:
    """
    Calculate Vision_Score (0-100) for a prop.
    
    Args:
        h10_rate: L10 hit rate (0-100)
        dvp_rank: Opponent's defense vs position rank (1-30, 1=best defense)
        active_badges: List of active badge keys
        is_demon: Is this a demon prop
        is_goblin: Is this a goblin prop
    
    Returns:
        {
            "vision_score": float (0-100),
            "hit_rate_component": float,
            "dvp_component": float,
            "badge_component": float,
            "breakdown": {...}
        }
    """
    # Normalize inputs
    h10_rate = float(h10_rate or 0)
    dvp_rank = int(dvp_rank) if dvp_rank else 15  # Default to middle
    active_badges = active_badges or []
    
    # ========== HIT RATE COMPONENT (60% weight) ==========
    # Direct mapping: 100% HR = 60 points, 0% HR = 0 points
    hit_rate_component = (h10_rate / 100) * 60
    
    # ========== DVP COMPONENT (25% weight) ==========
    # Base: 12.5 points (neutral)
    # Range: 2.5 to 22.5 based on matchup
    dvp_modifier = DVP_SCORE_MAP.get(dvp_rank, 0)
    dvp_component = 12.5 + dvp_modifier  # Range: 2.5 to 22.5
    
    # ========== BADGE COMPONENT (15% weight) ==========
    # Base: 7.5 points (neutral)
    # Range: 0 to 15 based on badges
    badge_score = 0
    badge_details = []
    
    for badge in active_badges:
        badge_key = badge.lower().replace(" ", "_").replace("-", "_")
        modifier = BADGE_SCORES.get(badge_key, 0)
        if modifier != 0:
            badge_score += modifier
            badge_details.append({"badge": badge, "modifier": modifier})
    
    # Clamp badge component to 0-15 range
    badge_component = max(0, min(15, 7.5 + badge_score))
    
    # ========== FINAL VISION SCORE ==========
    vision_score = hit_rate_component + dvp_component + badge_component
    
    # Clamp to 0-100
    vision_score = max(0, min(100, vision_score))
    
    return {
        "vision_score": round(vision_score, 1),
        "hit_rate_component": round(hit_rate_component, 1),
        "dvp_component": round(dvp_component, 1),
        "badge_component": round(badge_component, 1),
        "breakdown": {
            "h10_rate": h10_rate,
            "dvp_rank": dvp_rank,
            "dvp_modifier": dvp_modifier,
            "badge_details": badge_details,
            "badge_total": badge_score
        }
    }


def score_prop(prop: Dict[str, Any]) -> float:
    """
    Convenience function to score a prop dict.
    Returns just the vision_score float.
    """
    result = calculate_vision_score(
        h10_rate=prop.get("h10_rate") or prop.get("h10_hit_rate") or 0,
        dvp_rank=prop.get("dvp_rank"),
        active_badges=prop.get("active_badges") or prop.get("context_badges") or [],
        is_demon=prop.get("is_demon", False),
        is_goblin=prop.get("is_goblin", False)
    )
    return result["vision_score"]


# Board thresholds
# Vision Score = HR(60%) + DVP(25%) + Badges(15%)
# 100% HR = 60pts, neutral DVP = 12.5pts, neutral badges = 7.5pts = 80pts baseline
# Adjusted thresholds to be achievable:
SAFE_HAVEN_THRESHOLDS = {
    "is_goblin": True,
    "min_h10_rate": 80,
    "min_vision_score": 70  # 80% HR gives ~48pts + ~20pts dvp/badges = ~68
}

FRONT_LINES_THRESHOLDS = {
    "min_h10_rate": 60,
    "min_vision_score": 55  # 60% HR gives ~36pts + ~20pts = ~56
}

WAR_ZONE_THRESHOLDS = {
    "is_demon": True,
    "min_h10_rate": 50,
    "min_vision_score": 45  # 50% HR gives ~30pts + ~20pts = ~50
}

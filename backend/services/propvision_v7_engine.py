"""
PropVision v7 Engine - True Probability & Diversified Parlay Optimizer
=======================================================================

MISSION: Extract every possible 0.5% of edge through mathematical precision.

BOARD SCORE FORMULA (v7.1 - Edge-First):
=========================================
Props In → Filter Trap Risk → Score → Apply Penalties → Board Picks

Board_Score = Sharp_Implied + PP_Edge + Hit_Rate_Avg - Penalties

COMPONENTS:
- Sharp_Implied: What sharp books say (smart money) - 38%+ minimum
- PP_Edge: Sharp_Implied - PP_Implied (positive = PP giving better value)
- Hit_Rate_Avg: (L5 + L10) / 2 (historical consistency)

HARD KILLS (Auto-Disqualify):
1. Trap Risk / Hook Risk / Suspect Bait (filtered out entirely)
2. L3 < 33% (cold streak - 0/3 or 1/3)  
3. L5 < 40% (confirmed cold - 0-1/5)
4. Sharp Implied < 38% (no sharp edge)
5. Line > Season Median (against the grain) - except Demons
6. Blowout HIGH + PTS/PRA (bench risk)

SOFT KILLS (Penalties applied to Board_Score):
1. Std Dev > 6.0 → -10 points
2. DvP 10-20 (neutral matchup) → -5 points
3. Medium Blowout Risk → -5 points

NOTE: Trap Risk is a HARD FILTER, not a penalty.

TIER CLASSIFICATION:
- Safe Haven: Goblins only (alternate lines with edge)
- Front Lines: Both Goblins and standard props
- War Zone: Demons only (high-risk alternate lines)

PARLAY OPTIMIZER:
- 5 parlays per tier (2-leg through 6-leg)
- Max 2 appearances per player per tier
- Max 2 picks per team per parlay
- Max 3 picks per stat type per parlay
"""

from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone
from collections import Counter, defaultdict
from itertools import combinations
import logging
import math

logger = logging.getLogger(__name__)

# =============================================================================
# V7.1 CONSTANTS - EDGE-FIRST FORMULA
# =============================================================================

# PRIZEPICKS IMPLIED PROBABILITY
PP_IMPLIED_STANDARD = 57.8  # -137 odds = 57.8% (for Goblins/Standard)
PP_IMPLIED_DEMON = 50.0     # +100 odds = 50% (for Demons - even money)

# HARD KILL THRESHOLDS
HARD_KILL_L3_MIN = 33.0  # Must hit at least 1/3
HARD_KILL_L5_MIN = 40.0  # Must hit at least 2/5
HARD_KILL_SHARP_MIN = 38.0  # Sharp must see edge
HARD_KILL_SEPARATION_MIN = 3.0  # Min 3% separation

# SOFT KILL PENALTIES (applied to Board Score)
PENALTY_HIGH_VARIANCE = -10.0  # Std dev > 6.0
PENALTY_BLOWOUT_MEDIUM = -5.0  # Medium blowout risk
PENALTY_BLOWOUT_HIGH = -10.0   # High blowout risk (non-bench stats only)

# TIERED DVP PENALTIES (30 teams in league)
# Contextual: offensive stats vs defense rank, defensive stats vs offense rank
PENALTY_DVP_TIER_1 = -5.0   # Rank 1-5 (elite)
PENALTY_DVP_TIER_2 = -3.5   # Rank 6-10 (strong)
PENALTY_DVP_TIER_3 = -2.0   # Rank 11-15 (above average)
# Rank 16-30: No penalty (neutral to weak)

# NOTE: TRAP RISK is a HARD FILTER, not a penalty
# NOTE: NEUTRAL DVP (rank 16-30) is NOT penalized

# LEGACY CONSTANTS (kept for backward compatibility with helper functions)
WEIGHT_L3 = 0.40  # Most recent = most predictive
WEIGHT_L5 = 0.35  # Validates trend
WEIGHT_L10 = 0.25  # Baseline stability
WEIGHT_HISTORICAL = 0.45  # Legacy - not used in new formula
WEIGHT_SHARP = 0.25  # Legacy - not used in new formula
WEIGHT_FLOOR = 0.15  # Legacy - not used in new formula
WEIGHT_CONTEXT = 0.15  # Legacy - not used in new formula

# TIER THRESHOLDS - SHARP IMPLIED (Primary classifier)
# These match traditional sharp book tier windows
TIER_SAFE_HAVEN_SHARP_MIN = 70.0   # -233 or stronger (70%+ implied)
TIER_FRONT_LINES_SHARP_MIN = 58.0  # -138 to -232 (58-69% implied)
TIER_WAR_ZONE_SHARP_MIN = 38.0     # War Zone = Demons at 38%+ with L10 >= 50% and PP edge

# WAR ZONE SPECIAL CRITERIA (for demons only)
WAR_ZONE_L10_MIN = 50.0            # Must have 50%+ L10 hit rate
WAR_ZONE_PP_EDGE_MIN = 0.0         # PrizePicks implied must be > sharp implied

# TRUE PROBABILITY (Secondary - used for ranking within tiers)
TIER_SAFE_HAVEN_MIN = 72.0
TIER_FRONT_LINES_MIN = 62.0
TIER_WAR_ZONE_MIN = 39.0

# CONTEXTUAL MODIFIER CAPS
MAX_DVP_BOOST = 8.0
MAX_WHISTLE_BOOST = 5.0
MAX_VACUUM_BOOST = 5.0

# OUTPUT CAPS
MAX_PICKS_PER_TIER = 10
MAX_PARLAYS_PER_TIER = 5
MAX_PLAYER_APPEARANCES_PER_TIER = 2
MAX_TEAM_PER_PARLAY = 2
MAX_STAT_TYPE_PER_PARLAY = 3

# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def american_to_implied(odds: int) -> float:
    """Convert American odds to implied probability (0-1)."""
    if odds is None:
        return 0.0
    if odds < 0:
        return abs(odds) / (abs(odds) + 100)
    return 100 / (odds + 100)


def calculate_hit_rate(values: List[float], line: float, count: int = None) -> float:
    """Calculate hit rate for values above a line."""
    if not values:
        return 0.0
    
    check_values = values[:count] if count else values
    if not check_values:
        return 0.0
    
    hits = sum(1 for v in check_values if v >= line)
    return (hits / len(check_values)) * 100


def calculate_median(values: List[float]) -> Optional[float]:
    """Calculate median from a list."""
    if not values:
        return None
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n % 2 == 0:
        return (sorted_vals[n//2 - 1] + sorted_vals[n//2]) / 2
    return sorted_vals[n//2]


def calculate_mode(values: List[float]) -> Optional[float]:
    """Calculate mode (rounded to 0.5)."""
    if not values:
        return None
    rounded = [round(v * 2) / 2 for v in values]
    counts = Counter(rounded)
    if not counts:
        return None
    mode_val, mode_count = counts.most_common(1)[0]
    return mode_val if mode_count >= 2 else None


def calculate_std_dev(values: List[float]) -> float:
    """Calculate standard deviation."""
    if not values or len(values) < 2:
        return 0.0
    avg = sum(values) / len(values)
    variance = sum((v - avg) ** 2 for v in values) / len(values)
    return math.sqrt(variance)


# =============================================================================
# TRUE PROBABILITY ENGINE
# =============================================================================

class TrueProbabilityEngine:
    """
    Calculates True Probability for each pick using a multi-factor model
    designed to extract maximum edge.
    """
    
    def __init__(self):
        self.kill_reasons = []
    
    def calculate_historical_consistency(
        self,
        l3_rate: float,
        l5_rate: float,
        l10_rate: float
    ) -> float:
        """
        Historical Consistency (45% of True Prob)
        Weighted recency: L3 most important, L10 for stability
        
        Returns: 0-100 score
        """
        weighted = (
            (l3_rate * WEIGHT_L3) +
            (l5_rate * WEIGHT_L5) +
            (l10_rate * WEIGHT_L10)
        )
        return min(100, max(0, weighted))
    
    def calculate_sharp_signal(
        self,
        sharp_implied: float,
        separation_pct: float
    ) -> float:
        """
        Sharp Market Signal (25% of True Prob)
        Sharp implied probability with separation confidence multiplier
        
        Returns: 0-100 score
        """
        if sharp_implied <= 0:
            return 0.0
        
        # Base score = sharp implied as percentage
        base = sharp_implied * 100
        
        # Separation confidence multiplier (0.9 to 1.1)
        # Higher separation = more confidence in the edge
        confidence = 1.0
        if separation_pct >= 15:
            confidence = 1.10  # Strong edge
        elif separation_pct >= 10:
            confidence = 1.05  # Good edge
        elif separation_pct >= 5:
            confidence = 1.0   # Acceptable
        elif separation_pct < 3:
            confidence = 0.90  # Weak edge
        
        return min(100, base * confidence)
    
    def calculate_statistical_floor(
        self,
        line: float,
        median: Optional[float],
        mode: Optional[float],
        std_dev: float
    ) -> float:
        """
        Statistical Floor Analysis (15% of True Prob)
        Measures cushion below line and consistency
        
        Returns: 0-100 score
        """
        if not median or line <= 0:
            return 50.0  # Default neutral
        
        score = 50.0  # Start neutral
        
        # Cushion bonus: How far below median is the line?
        if median > line:
            cushion_pct = ((median - line) / line) * 100
            # Cap cushion contribution at 30 points
            cushion_bonus = min(30, cushion_pct)
            score += cushion_bonus
        else:
            # Line above median = penalty
            deficit_pct = ((line - median) / line) * 100
            score -= min(25, deficit_pct)
        
        # Mode proximity bonus
        if mode and mode > line:
            mode_cushion = ((mode - line) / line) * 100
            score += min(10, mode_cushion * 0.5)
        
        # Variance penalty
        if std_dev > 6.0:
            score -= 10  # High variance = less reliable floor
        elif std_dev > 4.0:
            score -= 5
        elif std_dev < 2.0:
            score += 5  # Very consistent = reliable floor
        
        return max(0, min(100, score))
    
    def calculate_contextual_modifiers(
        self,
        dvp_rank: Optional[int],
        is_elite_defense: bool,
        is_weak_defense: bool,
        whistle_class: str,
        vacuum_modifier: float,
        blowout_risk: str,
        stat_type: str
    ) -> float:
        """
        Contextual Modifiers (15% of True Prob)
        Game environment factors that affect probability
        
        Returns: -15 to +15 modifier (added to base 50)
        """
        modifier = 0.0
        
        # Defensive Momentum (+/- 8%)
        if is_weak_defense:
            modifier += MAX_DVP_BOOST
        elif is_elite_defense:
            modifier -= MAX_DVP_BOOST
        elif dvp_rank and 10 <= dvp_rank <= 20:
            modifier -= 2.0  # Neutral matchup slight penalty
        
        # Whistle Matrix (+/- 5%)
        if whistle_class == "high_whistle":
            if stat_type.upper() in ["PTS", "FTM"]:
                modifier += MAX_WHISTLE_BOOST
            elif stat_type.upper() == "PRA":
                modifier += MAX_WHISTLE_BOOST * 0.5
        elif whistle_class == "low_whistle":
            if stat_type.upper() in ["PTS", "FTM"]:
                modifier -= MAX_WHISTLE_BOOST
            elif stat_type.upper() == "PRA":
                modifier -= MAX_WHISTLE_BOOST * 0.5
        
        # Usage Vacuum (+5% if beneficiary)
        if vacuum_modifier > 0:
            modifier += min(MAX_VACUUM_BOOST, vacuum_modifier * 0.3)
        
        # Blowout Risk (penalty for bench risk)
        if blowout_risk == "HIGH":
            # Only penalize scoring stats (benched in blowout)
            if stat_type.upper() in ["PTS", "PRA", "AST"]:
                modifier -= 8.0
            else:
                modifier -= 3.0  # Less impact on REB/3PM
        elif blowout_risk == "MEDIUM":
            if stat_type.upper() in ["PTS", "PRA"]:
                modifier -= 4.0
        
        # Convert to 0-100 scale (base 50 +/- modifier)
        return 50.0 + modifier
    
    def check_hard_kills(
        self,
        l3_rate: float,
        l5_rate: float,
        sharp_implied: float,
        separation_pct: float,
        line: float,
        season_median: Optional[float],
        blowout_risk: str,
        stat_type: str,
        is_demon: bool = False
    ) -> Tuple[bool, Optional[str]]:
        """
        Hard Kill Switch - Returns (is_killed, reason)
        Any hard kill = prop is eliminated
        
        DEMONS get relaxed checks:
        - Skip L3 check (demons are high risk, recent cold is expected)
        - Skip Line > Median check (demon lines are intentionally high)
        """
        # Kill 0: NO L5 DATA (minimum required for evaluation)
        if l5_rate is None:
            return True, f"HARD_KILL: No L5 hit rate data available"
        
        # Kill 1: L3 < 33% (cold streak) - SKIP for demons (high risk plays)
        if not is_demon and l3_rate is not None and l3_rate < HARD_KILL_L3_MIN:
            return True, f"HARD_KILL: L3 rate {l3_rate:.0f}% < {HARD_KILL_L3_MIN:.0f}% (cold streak)"
        
        # Kill 2: L5 <= 40% (confirmed cold)
        if l5_rate <= HARD_KILL_L5_MIN:
            return True, f"HARD_KILL: L5 rate {l5_rate:.0f}% <= {HARD_KILL_L5_MIN:.0f}% (confirmed cold)"
        
        # Kill 3: Sharp implied < 38% (no edge) - SKIP if no sharp data for demons
        sharp_pct = sharp_implied * 100 if sharp_implied and sharp_implied < 1 else (sharp_implied or 0)
        if sharp_pct > 0 and sharp_pct < HARD_KILL_SHARP_MIN:
            return True, f"HARD_KILL: Sharp implied {sharp_pct:.1f}% < {HARD_KILL_SHARP_MIN:.0f}% (no edge)"
        
        # Kill 4: Separation < 3% (too close to call) - only if we have separation data
        if separation_pct and separation_pct < HARD_KILL_SEPARATION_MIN:
            return True, f"HARD_KILL: Separation {separation_pct:.1f}% < {HARD_KILL_SEPARATION_MIN:.0f}%"
        
        # Kill 5: Line > Season Median (against the grain)
        # SKIP this check for demons - demon lines are intentionally above the main line
        if not is_demon and season_median and line > season_median:
            return True, f"HARD_KILL: Line {line} > Season Median {season_median:.1f}"
        
        # Kill 6: Blowout HIGH + scoring stat (bench risk)
        if blowout_risk == "HIGH" and stat_type.upper() in ["PTS", "PRA"]:
            return True, f"HARD_KILL: HIGH blowout risk for {stat_type} (bench minutes)"
        
        return False, None
    
    def apply_soft_kills(
        self,
        base_score: float,
        std_dev: float,
        dvp_rank: Optional[float],
        blowout_risk: str = "NONE",
        stat_type: str = ""
    ) -> Tuple[float, List[str]]:
        """
        Soft Kill Penalties - Applied to Board Score
        NOTE: Trap risk is a HARD FILTER (filtered out entirely), not a penalty
        Returns (adjusted_score, penalty_reasons)
        """
        penalties = []
        adjusted = base_score
        
        # Penalty 1: High variance
        if std_dev and std_dev > 6.0:
            adjusted += PENALTY_HIGH_VARIANCE
            penalties.append(f"High variance (std_dev={std_dev:.1f})")
        
        # Penalty 2: TIERED MATCHUP PENALTIES
        # - Offensive props (PTS, AST, PRA, PA, 3PM): penalized by opponent DEFENSE rank
        # - Defensive props (REB, BLK, STL): penalized by opponent OFFENSE rank
        # DVP rank is already contextual to the stat type
        if dvp_rank:
            offensive_stats = ["PTS", "AST", "PA", "PRA", "3PM"]
            defensive_stats = ["REB", "BLK", "STL"]
            
            if stat_type in offensive_stats:
                # Offensive prop vs opponent defense
                if dvp_rank <= 5:
                    adjusted += PENALTY_DVP_TIER_1
                    penalties.append(f"Elite defense (#{dvp_rank:.0f})")
                elif dvp_rank <= 10:
                    adjusted += PENALTY_DVP_TIER_2
                    penalties.append(f"Strong defense (#{dvp_rank:.0f})")
                elif dvp_rank <= 15:
                    adjusted += PENALTY_DVP_TIER_3
                    penalties.append(f"Above avg defense (#{dvp_rank:.0f})")
                    
            elif stat_type in defensive_stats:
                # Defensive prop vs opponent offense
                if dvp_rank <= 5:
                    adjusted += PENALTY_DVP_TIER_1
                    penalties.append(f"Elite offense (#{dvp_rank:.0f})")
                elif dvp_rank <= 10:
                    adjusted += PENALTY_DVP_TIER_2
                    penalties.append(f"Strong offense (#{dvp_rank:.0f})")
                elif dvp_rank <= 15:
                    adjusted += PENALTY_DVP_TIER_3
                    penalties.append(f"Above avg offense (#{dvp_rank:.0f})")
            # Rank 16-30: No penalty (neutral to weak matchup)
        
        # Penalty 3: Medium blowout risk
        if blowout_risk == "MEDIUM":
            adjusted += PENALTY_BLOWOUT_MEDIUM
            penalties.append("Medium blowout risk")
        
        # Penalty 4: High blowout risk (only for scoring stats)
        if blowout_risk == "HIGH" and stat_type in ["PTS", "PRA", "PA"]:
            adjusted += PENALTY_BLOWOUT_HIGH
            penalties.append("High blowout risk (scoring stat)")
        
        return adjusted, penalties
    
    def calculate_true_probability(
        self,
        # Historical data
        l3_rate: float,
        l5_rate: float,
        l10_rate: float,
        # Sharp data
        sharp_implied: float,
        separation_pct: float,
        # Statistical data
        line: float,
        median: Optional[float],
        mode: Optional[float],
        std_dev: float,
        season_median: Optional[float],
        season_avg: Optional[float],  # Player's season average for this stat
        # Context data
        dvp_rank: Optional[float],
        is_elite_defense: bool,
        is_weak_defense: bool,
        whistle_class: str,
        vacuum_modifier: float,
        blowout_risk: str,
        stat_type: str,
        trap_risk: bool,
        # War Zone criteria
        is_demon: bool = False,
        pp_price: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        V7.2 COMPREHENSIVE BOARD SCORE FORMULA
        ======================================
        Board_Score = True_Probability + Sharp_Implied + PP_Edge 
                    + L5_Rate + L10_Rate + Line_Below_Avg_Bonus - Penalties
        
        Components:
        - True_Probability: Weighted (Historical 45% + Sharp 25% + Floor 15% + Context 15%)
        - Sharp_Implied: What smart money says (38%+ minimum)
        - PP_Edge: Edge over PrizePicks break-even (positive = value)
        - L5_Rate: Last 5 games hit rate
        - L10_Rate: Last 10 games hit rate
        - Line_Below_Avg_Bonus: Up to +15 for lines below player's average
        - Penalties: Variance, defense matchup, blowout risk
        
        Tier Classification: Based on True Probability
        - Safe Haven: >= 72%
        - Front Lines: 62-71%
        - War Zone: Demons with extreme volatility
        """
        result = {
            "true_probability": 0.0,
            "board_score": 0.0,
            "tier": "disqualified",
            "is_killed": False,
            "kill_reason": None,
            "soft_penalties": [],
            "components": {},
            "confidence": "LOW",
            "pp_edge": 0.0
        }
        
        # HARD FILTER: Trap risk (filtered out entirely, not penalized)
        # SKIP for demons - demons are high-risk plays, trap risk is expected
        if trap_risk and not is_demon:
            result["is_killed"] = True
            result["kill_reason"] = "Trap risk detected (filtered)"
            return result
        
        # Check other hard kills
        is_killed, kill_reason = self.check_hard_kills(
            l3_rate, l5_rate, sharp_implied, separation_pct,
            line, season_median, blowout_risk, stat_type, is_demon
        )
        
        if is_killed:
            result["is_killed"] = True
            result["kill_reason"] = kill_reason
            return result
        
        # =================================================================
        # V7.1 EDGE-FIRST FORMULA
        # Board_Score = Sharp_Implied + PP_Edge + Hit_Rate_Avg - Penalties
        # =================================================================
        
        # Component 1: Hit Rate Average (L5 + L10) / 2 - calculated first for demon edge
        l5_safe = l5_rate if l5_rate is not None else 0
        l10_safe = l10_rate if l10_rate is not None else 0
        hit_rate_avg = (l5_safe + l10_safe) / 2
        
        # Component 2: Sharp Implied (what smart money says)
        # For demons without sharp data, use hit rate as proxy
        if sharp_implied and sharp_implied > 0:
            sharp_pct = sharp_implied if sharp_implied > 1 else sharp_implied * 100
        else:
            # No sharp data - for demons, estimate from hit rate
            sharp_pct = hit_rate_avg  # Use hit rate avg as proxy
        
        # Component 3: PP Edge
        # For GOBLINS/STANDARD: PP Edge = Sharp - PP (positive = sharps see higher probability)
        # For DEMONS: PP Edge = Hit Rate - PP Break Even (positive = we beat the +100 odds)
        pp_edge = 0.0
        
        if is_demon:
            # DEMON EDGE: How much we beat the +100 break-even (50%)
            # This is the TRUE edge for demons - actual performance vs break-even
            pp_implied = PP_IMPLIED_DEMON  # 50% for +100 odds
            pp_edge = hit_rate_avg - pp_implied  # Positive = we hit more than 50%
        else:
            # GOBLIN/STANDARD EDGE: Sharp vs PP spread
            if pp_price is not None:
                if pp_price < 0:
                    pp_implied = abs(pp_price) / (abs(pp_price) + 100) * 100
                else:
                    pp_implied = 100 / (pp_price + 100) * 100
                pp_edge = sharp_pct - pp_implied
            else:
                pp_implied = PP_IMPLIED_STANDARD  # 57.8% for -137
                pp_edge = sharp_pct - pp_implied
        
        # Calculate Board Score (before penalties)
        # Ensure all components are numbers (not None)
        sharp_pct = sharp_pct or 0
        pp_edge = pp_edge or 0
        hit_rate_avg = hit_rate_avg or 0
        
        board_score_raw = sharp_pct + pp_edge + hit_rate_avg
        
        # Apply soft penalties
        board_score, penalties = self.apply_soft_kills(
            board_score_raw, std_dev, dvp_rank, blowout_risk, stat_type
        )
        
        # Store components for transparency
        result["components"] = {
            "sharp_implied": round(sharp_pct, 2),
            "pp_edge": round(pp_edge, 2),
            "hit_rate_avg": round(hit_rate_avg, 2),
            "raw_score": round(board_score_raw, 2),
            "penalties_applied": penalties
        }
        
        result["soft_penalties"] = penalties
        result["board_score"] = round(board_score, 2)
        
        # Calculate TRUE PROBABILITY for tier classification
        # True_Prob = (Historical × 0.45) + (Sharp × 0.25) + (Floor × 0.15) + (Context × 0.15)
        # Historical = (L3 × 0.40) + (L5 × 0.35) + (L10 × 0.25)
        l3_pct = l3_rate if l3_rate is not None else l5_rate
        l5_pct = l5_rate if l5_rate is not None else 0
        l10_pct = l10_rate if l10_rate is not None else 0
        
        historical = (l3_pct * 0.40) + (l5_pct * 0.35) + (l10_pct * 0.25)
        floor_pct = min(l3_pct, l5_pct, l10_pct) if all([l3_pct, l5_pct, l10_pct]) else min(l5_pct, l10_pct)
        context_score = 50  # Neutral context for now
        
        true_probability = (historical * 0.45) + (sharp_pct * 0.25) + (floor_pct * 0.15) + (context_score * 0.15)
        
        # =====================================================================
        # NEW BOARD SCORE FORMULA (v7.2)
        # =====================================================================
        # Board_Score = True_Probability + Sharp_Implied + PP_Edge + L5_Rate + L10_Rate 
        #             + Line_Below_Avg_Bonus - Penalties
        # =====================================================================
        
        # Line Below Average Bonus
        # If the line is below the player's season average, they have more cushion to hit
        # Bonus = (Season_Avg - Line) - actual points of cushion
        # Example: Avg 17.1, Line 9.5 → Bonus = 7.6 points
        line_below_avg_bonus = 0
        if season_avg is not None and season_avg > 0 and line is not None:
            avg_diff = season_avg - line  # Positive = line is below avg (good)
            if avg_diff > 0:
                line_below_avg_bonus = round(avg_diff, 1)  # Raw difference, no cap
        
        # Calculate new Board Score
        board_score_raw = (
            true_probability +      # Weighted historical + sharp + floor + context
            sharp_pct +             # Sharp implied probability
            pp_edge +               # Edge over PrizePicks break-even
            l5_pct +                # Last 5 games hit rate
            l10_pct +               # Last 10 games hit rate
            line_below_avg_bonus    # Bonus for lines below player's average
        )
        
        # Apply soft penalties
        board_score, penalties = self.apply_soft_kills(
            board_score_raw, std_dev, dvp_rank, blowout_risk, stat_type
        )
        
        # Store components for transparency
        result["components"] = {
            "true_probability": round(true_probability, 2),
            "sharp_implied": round(sharp_pct, 2),
            "pp_edge": round(pp_edge, 2),
            "l5_rate": round(l5_pct, 2),
            "l10_rate": round(l10_pct, 2),
            "line_below_avg_bonus": round(line_below_avg_bonus, 2),
            "raw_score": round(board_score_raw, 2),
            "penalties_applied": penalties
        }
        
        result["soft_penalties"] = penalties
        result["board_score"] = round(board_score, 2)
        result["true_probability"] = round(true_probability, 2)
        result["pp_edge"] = round(pp_edge, 2)
        
        # Classify tier based on TRUE PROBABILITY
        # Safe Haven: >= 72% True Prob
        # Front Lines: 62-71% True Prob
        # Below Threshold: < 62%
        has_sharp_data = sharp_implied and sharp_implied > 0
        
        if true_probability >= TIER_SAFE_HAVEN_MIN:
            # TRUE PROBABILITY qualifies for Safe Haven
            result["tier"] = "safe_haven"
            result["confidence"] = "HIGH"
        elif true_probability >= TIER_FRONT_LINES_MIN:
            # TRUE PROBABILITY qualifies for Front Lines
            result["tier"] = "front_lines"
            result["confidence"] = "MEDIUM"
        elif is_demon:
            # =====================================================================
            # DEMON TIER CLASSIFICATION (Updated Logic)
            # =====================================================================
            # Front Lines: "Safe" demons with good hit rates (middle ground)
            # War Zone: Highest risk demons (extreme plays, biggest payout)
            # =====================================================================
            l10_pct = l10_rate if l10_rate is not None else 0
            l5_pct = l5_rate if l5_rate is not None else 0
            hit_avg = (l5_pct + l10_pct) / 2
            
            # Basic qualification for any tier
            meets_basic = l10_pct >= WAR_ZONE_L10_MIN and l5_pct > 40
            meets_edge = pp_edge > WAR_ZONE_PP_EDGE_MIN if has_sharp_data else hit_avg >= 50
            
            if not meets_basic or not meets_edge:
                # Doesn't qualify for any tier
                result["tier"] = "below_threshold"
                result["confidence"] = "LOW"
                if l10_pct < WAR_ZONE_L10_MIN:
                    result["disqualify_reason"] = f"L10 {l10_pct}% < {WAR_ZONE_L10_MIN}% required"
                elif l5_pct <= 40:
                    result["disqualify_reason"] = f"L5 {l5_pct}% <= 40% required"
                elif has_sharp_data and pp_edge <= WAR_ZONE_PP_EDGE_MIN:
                    result["disqualify_reason"] = f"No PP edge ({pp_edge:.1f}%)"
            else:
                # Qualified demon - now classify Front Lines vs War Zone
                # FRONT LINES: "Safer" demons with strong hit rates
                #   - PP Edge >= 15% OR Hit Rate Avg >= 65%
                #   - These are high-probability demons
                # WAR ZONE: Highest risk demons (extreme volatility)
                #   - PP Edge < 15% AND Hit Rate Avg < 65%
                #   - L10 <= 60% (more volatile)
                #   - These are the "swing for the fences" plays
                
                is_safe_demon = pp_edge >= 15 or hit_avg >= 65
                is_extreme_demon = pp_edge < 10 and l10_pct <= 60
                
                if is_safe_demon:
                    result["tier"] = "front_lines"
                    result["confidence"] = "MEDIUM"
                    result["demon_class"] = "safe_demon"
                elif is_extreme_demon:
                    result["tier"] = "war_zone"
                    result["confidence"] = "STANDARD"
                    result["demon_class"] = "extreme_demon"
                else:
                    # Middle ground demons - default to Front Lines
                    result["tier"] = "front_lines"
                    result["confidence"] = "MEDIUM"
                    result["demon_class"] = "moderate_demon"
        elif has_sharp_data and sharp_pct >= TIER_WAR_ZONE_SHARP_MIN:
            # Non-demon with low sharp implied - below threshold
            result["tier"] = "below_threshold"
            result["confidence"] = "LOW"
        else:
            result["tier"] = "below_threshold"
            result["confidence"] = "LOW"
        
        return result


# =============================================================================
# PARLAY OPTIMIZER
# =============================================================================

class DiversifiedParlayOptimizer:
    """
    Builds EV-positive parlays with diversification constraints:
    - Max 2 appearances per player per tier
    - Max 2 picks from same team per parlay
    - Max 3 picks from same stat type per parlay
    """
    
    def __init__(self, picks: List[Dict[str, Any]]):
        self.picks = picks
        self.player_appearances = defaultdict(int)
    
    def _is_valid_combination(self, combo: List[Dict]) -> bool:
        """Check if parlay meets diversification rules."""
        # Rule 1: Max 2 from same team
        team_counts = Counter(p.get("team") for p in combo)
        if any(count > MAX_TEAM_PER_PARLAY for count in team_counts.values()):
            return False
        
        # Rule 2: Max 3 from same stat type
        stat_counts = Counter(p.get("stat_type", "").upper() for p in combo)
        if any(count > MAX_STAT_TYPE_PER_PARLAY for count in stat_counts.values()):
            return False
        
        # Rule 3: No duplicate players in same parlay
        players = [p.get("player_name") for p in combo]
        if len(players) != len(set(players)):
            return False
        
        return True
    
    def _calculate_parlay_ev(self, combo: List[Dict]) -> float:
        """
        Calculate Expected Value of parlay.
        EV = (Combined_True_Prob * Payout) - (1 - Combined_True_Prob)
        """
        # Combined probability (multiply individual probs)
        combined_prob = 1.0
        for pick in combo:
            prob = pick.get("true_probability", 50) / 100
            combined_prob *= prob
        
        # Standard parlay payout multiplier (approximate)
        legs = len(combo)
        payout_multiplier = {
            2: 2.6,   # 2-leg
            3: 6.0,   # 3-leg
            4: 10.0,  # 4-leg
            5: 20.0,  # 5-leg
            6: 40.0   # 6-leg
        }.get(legs, 2.0)
        
        # EV = (prob * payout) - (1 - prob)
        ev = (combined_prob * payout_multiplier) - (1 - combined_prob)
        return ev
    
    def _would_exceed_appearances(self, combo: List[Dict]) -> bool:
        """Check if any player would exceed max appearances."""
        temp_counts = self.player_appearances.copy()
        for pick in combo:
            player = pick.get("player_name")
            temp_counts[player] += 1
            if temp_counts[player] > MAX_PLAYER_APPEARANCES_PER_TIER:
                return True
        return False
    
    def _record_appearances(self, combo: List[Dict]):
        """Record player appearances after selecting a parlay."""
        for pick in combo:
            player = pick.get("player_name")
            self.player_appearances[player] += 1
    
    def build_optimized_parlays(self, tier: str, count: int = 5) -> List[Dict[str, Any]]:
        """
        Build diversified, EV-positive parlays for a tier.
        
        Strategy:
        1. Generate 2-leg through 6-leg combinations
        2. Filter by diversification rules
        3. Score by EV
        4. Select top parlays while respecting appearance limits
        """
        tier_picks = [p for p in self.picks if p.get("tier") == tier]
        
        if len(tier_picks) < 2:
            return []
        
        all_candidates = []
        
        # Generate combinations for each leg count
        for legs in range(2, min(7, len(tier_picks) + 1)):
            for combo in combinations(tier_picks, legs):
                combo_list = list(combo)
                
                # Check diversification rules
                if not self._is_valid_combination(combo_list):
                    continue
                
                # Calculate EV
                ev = self._calculate_parlay_ev(combo_list)
                
                # Only consider EV-positive parlays
                if ev > 0:
                    all_candidates.append({
                        "picks": combo_list,
                        "legs": legs,
                        "ev": ev,
                        "combined_prob": math.prod(
                            p.get("true_probability", 50) / 100 for p in combo_list
                        )
                    })
        
        # Sort by EV descending
        all_candidates.sort(key=lambda x: x["ev"], reverse=True)
        
        # Select top parlays while respecting appearance limits
        selected_parlays = []
        for candidate in all_candidates:
            if len(selected_parlays) >= count:
                break
            
            if not self._would_exceed_appearances(candidate["picks"]):
                # Record and select
                self._record_appearances(candidate["picks"])
                
                # Build parlay object
                parlay = {
                    "parlay_id": f"{tier}_{len(selected_parlays) + 1}",
                    "tier": tier,
                    "legs": candidate["legs"],
                    "expected_value": round(candidate["ev"], 3),
                    "combined_probability": round(candidate["combined_prob"] * 100, 2),
                    "picks": [
                        {
                            "player_name": p.get("player_name"),
                            "stat_type": p.get("stat_type"),
                            "line": p.get("line"),
                            "direction": "Over",
                            "true_probability": p.get("true_probability"),
                            "team": p.get("team")
                        }
                        for p in candidate["picks"]
                    ],
                    "diversification": {
                        "unique_teams": len(set(p.get("team") for p in candidate["picks"])),
                        "unique_stat_types": len(set(p.get("stat_type") for p in candidate["picks"]))
                    }
                }
                selected_parlays.append(parlay)
        
        # Ensure variety in leg counts if possible
        leg_counts = Counter(p["legs"] for p in selected_parlays)
        
        return selected_parlays


# =============================================================================
# HELPER: Calculate L3/L5/L10 from game values
# =============================================================================

def calculate_granular_hit_rates(
    stat_values: List[float],
    line: float
) -> Dict[str, float]:
    """
    Calculate L3, L5, L10 hit rates from recent game values.
    Values should be sorted newest first.
    """
    result = {
        "l3_rate": 0.0,
        "l5_rate": 0.0,
        "l10_rate": 0.0,
        "l3_hits": 0,
        "l5_hits": 0,
        "l10_hits": 0
    }
    
    if not stat_values:
        return result
    
    # L3
    l3_values = stat_values[:3]
    if l3_values:
        l3_hits = sum(1 for v in l3_values if v >= line)
        result["l3_hits"] = l3_hits
        result["l3_rate"] = (l3_hits / len(l3_values)) * 100
    
    # L5
    l5_values = stat_values[:5]
    if l5_values:
        l5_hits = sum(1 for v in l5_values if v >= line)
        result["l5_hits"] = l5_hits
        result["l5_rate"] = (l5_hits / len(l5_values)) * 100
    
    # L10
    l10_values = stat_values[:10]
    if l10_values:
        l10_hits = sum(1 for v in l10_values if v >= line)
        result["l10_hits"] = l10_hits
        result["l10_rate"] = (l10_hits / len(l10_values)) * 100
    
    return result


# =============================================================================
# EXPORT
# =============================================================================

__all__ = [
    "TrueProbabilityEngine",
    "DiversifiedParlayOptimizer", 
    "calculate_granular_hit_rates",
    "american_to_implied",
    "calculate_median",
    "calculate_mode",
    "calculate_std_dev"
]

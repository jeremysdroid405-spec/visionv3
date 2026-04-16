"""
Volatility Profile — Single Source of Truth
=============================================
One shared function for CV interpretation across NBA and MLB.

Raw CV = stdev / mean (always decimal, never percentage).

Problem: CV is naturally higher for low-line props (Hits 0.5 → binary outcome
→ CV ~0.5-0.8 is normal). A flat 0.70 threshold unfairly penalizes small-number
props while being too lenient on high-line props.

Solution: Prop-family-specific thresholds based on typical line ranges.

Usage:
    from services.volatility_profile import get_volatility_profile
    profile = get_volatility_profile(cv=0.55, stat_type="Hits", line=0.5)
    # profile.score, profile.label, profile.is_extreme, profile.badge_key
"""

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class VolatilityProfile:
    """Canonical volatility output — used by all consumers."""
    cv_raw: float                # raw CV (stdev/mean), always decimal
    score: float                 # normalized 0-100 score (higher = more volatile)
    label: str                   # "low" | "moderate" | "high" | "extreme"
    is_extreme: bool             # True → badge fires
    badge_key: Optional[str]     # "volatility_extreme" or None
    family: str                  # prop family used for threshold selection
    thresholds: dict             # the thresholds that were applied


# =========================================================================
# PROP FAMILY CLASSIFICATION
# =========================================================================

# Each family defines thresholds for (moderate, high, extreme) CV boundaries.
# Props with naturally low means need higher thresholds to avoid false flags.

PROP_FAMILIES = {
    # ----- MLB -----
    "mlb_binary": {
        # Binary/low-count outcomes: Hits, Runs, RBIs, HRs, SB, Earned Runs at line <= 1.5
        "moderate": 0.55,
        "high": 0.80,
        "extreme": 1.00,
        "stat_types": {"hits", "runs", "rbis", "home runs", "stolen bases",
                       "earned runs", "walks", "hbp", "singles"},
        "max_line": 1.5,
    },
    "mlb_counting": {
        # Mid-range counting stats: Hits, Runs, etc. at line > 1.5, or Total Bases, Strikeouts
        "moderate": 0.45,
        "high": 0.65,
        "extreme": 0.85,
        "stat_types": {"hits", "runs", "rbis", "total bases", "batter strikeouts",
                       "pitcher strikeouts", "earned runs", "walks", "runs",
                       "hits+runs+rbis", "singles", "doubles"},
        "max_line": 99,
    },
    # ----- NBA -----
    "nba_low_line": {
        # Low-line NBA: AST, REB, STL, BLK, 3PM at line <= 4.5
        "moderate": 0.50,
        "high": 0.70,
        "extreme": 0.90,
        "stat_types": {"ast", "assists", "reb", "rebounds", "stl", "steals",
                       "blk", "blocks", "3pm", "threes", "turnovers", "to"},
        "max_line": 4.5,
    },
    "nba_mid_line": {
        # Mid-line NBA: PTS, REB, AST at line 4.5-15
        "moderate": 0.40,
        "high": 0.60,
        "extreme": 0.80,
        "stat_types": {"pts", "points", "reb", "rebounds", "ast", "assists",
                       "stl", "steals", "blk", "blocks", "3pm", "threes",
                       "turnovers", "to", "pa", "pr", "pa"},
        "max_line": 15,
    },
    "nba_high_line": {
        # High-line NBA: PRA, PTS, P+A, P+R at line > 15
        "moderate": 0.30,
        "high": 0.50,
        "extreme": 0.70,
        "stat_types": {"pra", "pts", "points", "p+a", "p+r", "pr", "pa",
                       "pts+reb", "pts+ast", "pts+reb+ast", "fantasy score"},
        "max_line": 99,
    },
}

# Default fallback for unknown prop types
DEFAULT_THRESHOLDS = {
    "moderate": 0.40,
    "high": 0.60,
    "extreme": 0.80,
}


def _classify_family(stat_type: str, line: Optional[float]) -> tuple:
    """
    Match a prop to its family based on stat_type and line.
    Returns (family_name, thresholds_dict).
    """
    stat_lower = (stat_type or "").lower().strip()
    line_val = line if line is not None else 5.0  # default mid-range if unknown

    # Try families in order from most specific (low-line) to least specific (high-line)
    # Binary MLB first
    if stat_lower in PROP_FAMILIES["mlb_binary"]["stat_types"] and line_val <= PROP_FAMILIES["mlb_binary"]["max_line"]:
        return "mlb_binary", PROP_FAMILIES["mlb_binary"]

    if stat_lower in PROP_FAMILIES["mlb_counting"]["stat_types"]:
        return "mlb_counting", PROP_FAMILIES["mlb_counting"]

    if stat_lower in PROP_FAMILIES["nba_low_line"]["stat_types"] and line_val <= PROP_FAMILIES["nba_low_line"]["max_line"]:
        return "nba_low_line", PROP_FAMILIES["nba_low_line"]

    if stat_lower in PROP_FAMILIES["nba_mid_line"]["stat_types"] and line_val <= PROP_FAMILIES["nba_mid_line"]["max_line"]:
        return "nba_mid_line", PROP_FAMILIES["nba_mid_line"]

    if stat_lower in PROP_FAMILIES["nba_high_line"]["stat_types"]:
        return "nba_high_line", PROP_FAMILIES["nba_high_line"]

    return "default", {"moderate": DEFAULT_THRESHOLDS["moderate"],
                       "high": DEFAULT_THRESHOLDS["high"],
                       "extreme": DEFAULT_THRESHOLDS["extreme"]}


def normalize_cv(cv_input: Optional[float]) -> Optional[float]:
    """
    Ensure CV is in decimal form (stdev/mean).
    Detects and converts percentage-scale CV (> 5.0) to decimal.
    Returns None if input is None or invalid.
    """
    if cv_input is None:
        return None
    cv = float(cv_input)
    if cv > 5.0:
        # Almost certainly percentage scale (e.g., 35.5 means 0.355)
        cv = cv / 100.0
    if cv < 0:
        return None
    return round(cv, 4)


def get_volatility_profile(
    cv: Optional[float],
    stat_type: str = "",
    line: Optional[float] = None,
) -> VolatilityProfile:
    """
    Single source of truth for volatility interpretation.

    Args:
        cv: Raw coefficient of variation (stdev/mean). Can be decimal or percentage.
        stat_type: Prop stat type (e.g., "Hits", "PRA", "Earned Runs")
        line: Prop line (e.g., 0.5, 1.5, 29.5)

    Returns:
        VolatilityProfile with score, label, is_extreme, and badge_key.
    """
    cv_normalized = normalize_cv(cv)

    if cv_normalized is None:
        return VolatilityProfile(
            cv_raw=0.0, score=0.0, label="unknown", is_extreme=False,
            badge_key=None, family="unknown",
            thresholds=DEFAULT_THRESHOLDS,
        )

    family_name, family = _classify_family(stat_type, line)
    t_mod = family["moderate"]
    t_high = family["high"]
    t_extreme = family["extreme"]

    # Label
    if cv_normalized >= t_extreme:
        label = "extreme"
    elif cv_normalized >= t_high:
        label = "high"
    elif cv_normalized >= t_mod:
        label = "moderate"
    else:
        label = "low"

    # Score: 0-100 normalized within the family's range
    # 0 = cv at 0, 100 = cv at extreme threshold
    if t_extreme > 0:
        score = min(100.0, round((cv_normalized / t_extreme) * 100, 1))
    else:
        score = 0.0

    is_extreme = label == "extreme"
    badge_key = "volatility_extreme" if is_extreme else None

    return VolatilityProfile(
        cv_raw=cv_normalized,
        score=score,
        label=label,
        is_extreme=is_extreme,
        badge_key=badge_key,
        family=family_name,
        thresholds={"moderate": t_mod, "high": t_high, "extreme": t_extreme},
    )

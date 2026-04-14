"""
VK/MLR Model Enforcement Module v2.1
=====================================
Centralized Vegas-Killer (MLR) calculation and validation.

STRICT REQUIREMENTS:
- vk_prob_over: REQUIRED, never None (MLR Confidence Score)
- vk_prob_under: REQUIRED, never None (100 - vk_prob_over)
- vk_verdict: REQUIRED, never None (STRONG_OVER, LEAN_OVER, NEUTRAL, LEAN_UNDER, STRONG_UNDER)
- vk_edge: REQUIRED, never None (MLR Probability - Implied Probability)

Any record missing these fields is REJECTED from persistence.

v2.1 CHANGES - GLOBAL VARIANCE SYNCHRONIZATION (L20 Stabilized Shield):
------------------------------------------------------------------------
DUAL-ENGINE SYSTEM FOR BOTH NBA AND MLB:
- L10 = Heat (Hit Rate) - Captures current "streaks" (shooting, swing mechanics)
- L20 = Risk (CV/Sigma) - Captures "Stability" (dilutes 1-game flukes)

NBA Impact:
  - Player roles change with injuries. L10 CV spikes on "low usage" games.
  - L20 keeps CV grounded in player's TRUE average role.

MLB Impact:
  - Baseball is king of "fluke zeroes"
  - A 0-for-4 night is 5% of L20 vs 10% of L10
  - Keeps 90% hit rate edges alive by smoothing variance

Author: PropVision AI
Version: 2.1.0 - L20 Stabilized Shield
"""
import logging
from typing import Dict, Any, Tuple, Optional
from dataclasses import dataclass
import numpy as np

logger = logging.getLogger(__name__)

# =============================================================================
# DATABASE REFERENCE FOR L20 STDEV LOOKUPS (STABILIZED SHIELD)
# =============================================================================
# GLOBAL VARIANCE SYNCHRONIZATION:
# - L10 = Heat (Hit Rate) - captures current streaks
# - L20 = Risk (CV/Sigma) - captures stability, dilutes 1-game flukes
#
# This will be set by the service that imports this module
_db_reference = None

def set_db_reference(db):
    """Set the MongoDB database reference for L20 std_dev lookups."""
    global _db_reference
    _db_reference = db
    logger.info("[VK_ENFORCE] Database reference set for L20 variance lookups (Stabilized Shield)")

# =============================================================================
# VK/MLR THRESHOLDS
# =============================================================================

# Verdict thresholds
STRONG_THRESHOLD = 65.0      # >= 65% confidence = STRONG
LEAN_THRESHOLD = 55.0        # >= 55% confidence = LEAN
NEUTRAL_ZONE = 45.0          # 45-55% = NEUTRAL

# Reality gate caps (sanity checks)
MAX_PA_BUMP = 1.0            # Max +1.0 PA adjustment
MAX_USAGE_BUMP = 0.20        # Max +20% usage adjustment
MAX_EDGE_PCT = 50.0          # Max 50% edge (sanity cap)
MIN_EDGE_PCT = -50.0         # Min -50% edge

# Required VK fields
REQUIRED_VK_FIELDS = ['vk_prob_over', 'vk_prob_under', 'vk_verdict', 'vk_edge']

# Market-First requirement: dk_odds must be present for Elite tier eligibility
MARKET_FIRST_REQUIRED = True


# =============================================================================
# L20 STANDARD DEVIATION LOOKUP - "STABILIZED SHIELD"
# =============================================================================

def _get_l20_std_dev_from_db(
    player_name: str,
    stat_type: str,
    sport: str = "NBA"
) -> Tuple[Optional[float], str]:
    """
    Query the actual L20 Standard Deviation for a player from the master hub.
    
    GLOBAL VARIANCE SYNCHRONIZATION:
    - L10 = Heat (Hit Rate) - captures current streaks
    - L20 = Risk (CV/Sigma) - captures stability, dilutes 1-game flukes
    
    L20 Benefits:
    - NBA: Player roles change with injuries. L20 keeps CV grounded in true average role.
    - MLB: A 0-for-4 is only 5% of L20 sample vs 10% in L10. Keeps edges alive.
    
    Args:
        player_name: Player's name
        stat_type: Stat type (PTS, REB, AST, PRA, Hits, etc.)
        sport: "NBA" or "MLB"
        
    Returns:
        (std_dev, source_description)
    """
    global _db_reference
    
    if _db_reference is None:
        logger.warning("[VK_MODEL] No DB reference set - cannot lookup L20 std_dev")
        return None, "no_db_reference"
    
    try:
        # Select collection based on sport
        if sport.upper() == "MLB":
            collection = _db_reference.mlb_master_hub_2026
        else:
            collection = _db_reference.nba_master_hub_2026
        
        # Try to find player by display_name or player_name
        player_doc = collection.find_one(
            {"$or": [
                {"display_name": player_name},
                {"player_name": player_name}
            ]},
            {"_id": 0, "bdl_game_logs": 1, "game_logs": 1}
        )
        
        if not player_doc:
            logger.debug(f"[VK_MODEL] Player not found in {sport} hub: {player_name}")
            return None, "player_not_found"
        
        # Get game logs
        game_logs = player_doc.get('bdl_game_logs') or player_doc.get('game_logs') or []
        
        if len(game_logs) < 5:
            logger.debug(f"[VK_MODEL] Insufficient game logs for {player_name}: {len(game_logs)}")
            return None, "insufficient_games"
        
        # Extract stat values from L20 games (Stabilized Shield)
        stat_field_map = {
            "PTS": ["pts", "points"],
            "REB": ["reb", "rebounds"],
            "AST": ["ast", "assists"],
            "3PM": ["fg3m", "three_pointers_made"],
            "STL": ["stl", "steals"],
            "BLK": ["blk", "blocks"],
            "Hits": ["hits", "h"],
            "Total Bases": ["total_bases", "tb"],
            "Pitcher Strikeouts": ["strikeouts", "so", "k"],
            "Pitching Outs": ["outs", "pitching_outs"],
            "Hits+Runs+RBIs": ["hits_runs_rbis", "hrr"],
        }
        
        # Handle combo stats - USE L20 FOR RISK CALCULATION
        if stat_type == "PRA":
            values = []
            for g in game_logs[:20]:  # L20 for stability
                pts = g.get('pts') or g.get('points') or 0
                reb = g.get('reb') or g.get('rebounds') or 0
                ast = g.get('ast') or g.get('assists') or 0
                # Check if player actually played (has minutes)
                mins = g.get('min') or g.get('minutes') or "0"
                if isinstance(mins, str):
                    mins = int(mins.split(':')[0]) if ':' in mins else int(mins or 0)
                if mins > 0:
                    values.append(pts + reb + ast)
        else:
            # Single stat - USE L20 FOR RISK CALCULATION
            fields = stat_field_map.get(stat_type.upper(), [stat_type.lower()])
            values = []
            for g in game_logs[:20]:  # L20 for stability
                # Check if player actually played
                mins = g.get('min') or g.get('minutes') or "0"
                if isinstance(mins, str):
                    mins = int(mins.split(':')[0]) if ':' in mins else int(mins or 0)
                if mins <= 0:
                    continue
                    
                for field in fields:
                    val = g.get(field)
                    if val is not None:
                        try:
                            values.append(float(val))
                        except (ValueError, TypeError):
                            pass
                        break
        
        if len(values) < 10:
            # Fall back to L10 if not enough games for L20
            if len(values) >= 5:
                l10_values = values[:10]
                std_dev = float(np.std(l10_values, ddof=1))
                l10_mean = float(np.mean(l10_values))
                cv_value = std_dev / l10_mean if l10_mean > 0 else 0
                logger.info(
                    f"[VK_MODEL] L10 Fallback (insufficient L20): {player_name} {stat_type} | "
                    f"L10 Mean: {l10_mean:.2f}, L10 σ: {std_dev:.3f}, CV: {cv_value:.3f}"
                )
                return std_dev, f"l10_fallback_{sport.lower()}"
            logger.debug(f"[VK_MODEL] Not enough valid values for {player_name} {stat_type}: {len(values)}")
            return None, "insufficient_values"
        
        # Calculate L20 Standard Deviation (Stabilized Shield)
        l20_values = values[:20]
        std_dev = float(np.std(l20_values, ddof=1))  # Sample std dev (ddof=1)
        l20_mean = float(np.mean(l20_values))
        
        # Calculate CV for logging
        cv_value = std_dev / l20_mean if l20_mean > 0 else 0
        
        # Log for auditing
        logger.info(
            f"[VK_MODEL] L20 Variance Lookup SUCCESS (Stabilized Shield): {player_name} {stat_type} | "
            f"L20 Mean: {l20_mean:.2f}, L20 σ: {std_dev:.3f}, CV: {cv_value:.3f}"
        )
        
        return std_dev, f"l20_stabilized_shield_{sport.lower()}"
        
    except Exception as e:
        logger.error(f"[VK_MODEL] L10 std_dev lookup failed for {player_name}: {e}")
        return None, f"lookup_error: {e}"


def validate_market_first(record: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Market-First Filter: Validate that dk_odds is present and valid.
    
    A prop MUST have non-null, non-zero dk_odds to be eligible for any Elite Tier.
    
    Returns:
        (is_valid, error_message)
    """
    dk_odds = record.get('dk_odds')
    
    if dk_odds is None:
        return False, "dk_odds is None - REJECTED (Market-First Filter)"
    
    if dk_odds == 0:
        return False, "dk_odds is 0 - REJECTED (Market-First Filter)"
    
    # dk_odds should be a reasonable value (typically -500 to +1000)
    if not isinstance(dk_odds, (int, float)):
        return False, f"dk_odds invalid type: {type(dk_odds)} - REJECTED"
    
    return True, "Valid"


@dataclass
class VKResult:
    """Validated VK/MLR calculation result."""
    vk_prob_over: float
    vk_prob_under: float
    vk_verdict: str
    vk_edge: float
    vk_recommendation: str  # Frontend-compatible field
    confidence_score: float
    is_valid: bool = True
    capped: bool = False
    cap_reason: Optional[str] = None
    # v2.0: True Variance Auditing
    standard_deviation_used: Optional[float] = None
    sigma_source: str = "unknown"
    z_score: Optional[float] = None


def calculate_vk_model(
    predicted_value: float,
    line: float,
    dk_odds: int = None,
    pp_implied_prob: float = None,
    season_avg: float = None,
    adjustment_pct: float = 0.0,
    require_market: bool = True,
    cv: float = None,  # Coefficient of Variation (stdev/mean) from player's logs
    std_dev: float = None,  # Direct standard deviation if available
    player_name: str = None,  # v2.0: For L10 std_dev DB lookup
    stat_type: str = None,   # v2.0: For L10 std_dev DB lookup  
    sport: str = "NBA"       # v2.0: "NBA" or "MLB"
) -> VKResult:
    """
    Calculate VK/MLR model output using STATISTICAL DISTRIBUTION.
    
    v2.0: TRUE VARIANCE CALCULATION
    ===============================
    KILLED the 20% CV default trap. If no std_dev/cv provided:
    1. Lookup actual L10 Standard Deviation from nba_master_hub_2026 (or MLB)
    2. Use real player volatility for Z-score calculation
    3. Expose `standard_deviation_used` for auditing
    
    FORMULA (Normal Distribution CDF):
    ----------------------------------
    P(X > line) = 1 - Φ((line - predicted) / σ)
    
    Where:
    - Φ = Standard Normal CDF
    - σ = TRUE Standard Deviation from L10 game logs (NOT a 20% default)
    
    This properly converts a prediction vs line into a probability
    that accounts for the player's REAL historical variance.
    
    Args:
        predicted_value: MLR predicted stat value (mean of distribution)
        line: Betting line (threshold)
        dk_odds: DraftKings odds (REQUIRED for Elite tiers)
        pp_implied_prob: PrizePicks implied probability (optional)
        season_avg: Player's season average (fallback for prediction)
        adjustment_pct: Any lineup/injury adjustment percentage
        require_market: If True, dk_odds must be valid (default True)
        cv: Coefficient of Variation from player's L10/season logs (PREFERRED)
        std_dev: Direct standard deviation if available (PREFERRED)
        player_name: Player name for DB lookup (v2.0)
        stat_type: Stat type for DB lookup (v2.0)
        sport: "NBA" or "MLB" for DB lookup (v2.0)
        
    Returns:
        VKResult with statistically accurate probabilities and variance audit fields
    """
    import math
    
    # =========================================================================
    # MARKET-FIRST FILTER: Reject if no market price
    # =========================================================================
    if require_market and MARKET_FIRST_REQUIRED:
        if dk_odds is None or dk_odds == 0:
            logger.warning(f"[VK_MODEL] Market-First REJECT: dk_odds={dk_odds}")
            return VKResult(
                vk_prob_over=0.0,
                vk_prob_under=0.0,
                vk_verdict="INVALID",
                vk_edge=0.0,
                vk_recommendation="INVALID",
                confidence_score=0.0,
                is_valid=False,
                capped=False,
                cap_reason="Market-First Filter: dk_odds missing or zero"
            )
    
    # =========================================================================
    # STEP 1: Ensure we have a valid prediction
    # =========================================================================
    if not predicted_value or predicted_value <= 0:
        # Use season_avg as fallback
        if season_avg and season_avg > 0:
            predicted_value = season_avg
            logger.warning(f"[VK_MODEL] Using season_avg fallback: {season_avg}")
        else:
            # Last resort: use line as prediction (50/50)
            predicted_value = line if line > 0 else 1.0
            logger.warning(f"[VK_MODEL] Using line fallback: {predicted_value}")
    
    # Ensure line is valid
    if not line or line <= 0:
        line = predicted_value  # Use prediction as line (50/50)
    
    # =========================================================================
    # STEP 2: Apply Reality Gate (cap impossible adjustments)
    # =========================================================================
    capped = False
    cap_reason = None
    
    if adjustment_pct > MAX_USAGE_BUMP:
        adjustment_pct = MAX_USAGE_BUMP
        capped = True
        cap_reason = f"Adjustment capped at {MAX_USAGE_BUMP * 100}%"
        logger.warning(f"[VK_MODEL] Reality Gate: {cap_reason}")
    
    # Apply adjustment to prediction
    adjusted_prediction = predicted_value * (1 + adjustment_pct)
    
    # =========================================================================
    # STEP 3: Calculate Standard Deviation (σ) - v2.0 TRUE VARIANCE
    # =========================================================================
    # Priority: Direct std_dev > CV-based > DB Lookup (L10) > REJECT (no default!)
    # 
    # THE 20% DEFAULT TRAP HAS BEEN KILLED.
    # If we can't get real variance, we use a conservative fallback that
    # results in more neutral probabilities rather than fake confidence.
    
    sigma = None
    sigma_source = "none"
    
    # Priority 1: Direct std_dev passed in
    if std_dev and std_dev > 0:
        sigma = std_dev
        sigma_source = "direct_input"
    
    # Priority 2: CV-based calculation
    elif cv and cv > 0:
        sigma = cv * adjusted_prediction
        sigma_source = "cv_input"
    
    # Priority 3: DB Lookup for REAL L20 Standard Deviation (Stabilized Shield)
    elif player_name and stat_type:
        db_std_dev, db_source = _get_l20_std_dev_from_db(player_name, stat_type, sport)
        if db_std_dev and db_std_dev > 0:
            sigma = db_std_dev
            sigma_source = db_source
    
    # Priority 4: LAST RESORT - Calculate from prediction with stat-specific CV
    # This is NOT the 20% default trap. It's stat-specific and conservative.
    if sigma is None or sigma <= 0:
        # Use stat-specific historical CV ranges (based on NBA/MLB averages)
        stat_cv_defaults = {
            # NBA - generally lower variance
            "PTS": 0.25,   # Points are moderately variable
            "REB": 0.35,   # Rebounds are more variable  
            "AST": 0.35,   # Assists are more variable
            "3PM": 0.45,   # Threes are highly variable
            "STL": 0.50,   # Steals are very volatile
            "BLK": 0.55,   # Blocks are very volatile
            "PRA": 0.22,   # Combos self-correct
            # MLB - generally higher variance (baseball is binary/volatile)
            "Hits": 0.60,
            "Total Bases": 0.75,
            "Pitcher Strikeouts": 0.40,
            "Pitching Outs": 0.30,
            "Hits+Runs+RBIs": 0.55,
        }
        
        # Get stat-specific default CV (fallback to 0.30 which is conservative)
        stat_upper = (stat_type or "").upper() if stat_type else ""
        default_cv = stat_cv_defaults.get(stat_upper, 0.30)
        
        sigma = adjusted_prediction * default_cv
        sigma_source = f"stat_default_cv_{default_cv}"
        
        logger.warning(
            f"[VK_MODEL] TRUE VARIANCE FALLBACK: Using stat-specific CV={default_cv} for "
            f"{player_name or 'unknown'} {stat_type or 'unknown'} | σ={sigma:.3f}"
        )
    
    # =========================================================================
    # MLB VOLATILITY FLOOR: Prevent "God Mode" probability hallucinations
    # =========================================================================
    # MLB hitting props are inherently volatile (0-for-4 nights happen).
    # Enforce minimum CV of 0.35 for MLB hitting stats to prevent fake 90%+ probabilities.
    MLB_HITTING_STATS = ["Hits", "Total Bases", "Hits+Runs+RBIs", "RBIs", "Runs"]
    MLB_VOLATILITY_FLOOR_CV = 0.35
    
    if sport.upper() == "MLB" and stat_type:
        stat_normalized = stat_type.replace("_", " ").title()
        if stat_normalized in MLB_HITTING_STATS or any(s in stat_normalized for s in ["Hit", "Base", "RBI", "Run"]):
            # Calculate CV from current sigma
            current_cv = sigma / adjusted_prediction if adjusted_prediction > 0 else 0
            
            if current_cv < MLB_VOLATILITY_FLOOR_CV:
                old_sigma = sigma
                sigma = adjusted_prediction * MLB_VOLATILITY_FLOOR_CV
                logger.info(
                    f"[VK_MODEL] MLB VOLATILITY FLOOR: {player_name} {stat_type} | "
                    f"CV {current_cv:.3f} → {MLB_VOLATILITY_FLOOR_CV} | σ {old_sigma:.3f} → {sigma:.3f}"
                )
                sigma_source = f"mlb_volatility_floor_{MLB_VOLATILITY_FLOOR_CV}"
    
    # Ensure minimum sigma to avoid division by zero
    sigma = max(sigma, 0.01)
    
    # =========================================================================
    # STEP 4: Calculate VK Probabilities using Normal Distribution CDF
    # =========================================================================
    # FORMULA: P(X > line) = 1 - Φ((line - μ) / σ)
    # Where Φ is the standard normal CDF
    #
    # This is the CORRECT statistical approach:
    # - If prediction (μ) >> line: z-score is very negative, P(over) → 100%
    # - If prediction (μ) = line: z-score = 0, P(over) = 50%
    # - If prediction (μ) << line: z-score is very positive, P(over) → 0%
    
    # Calculate z-score
    z_score = (line - adjusted_prediction) / sigma
    
    # Standard Normal CDF approximation (accurate to 0.01%)
    # Using the error function approximation
    def normal_cdf(z):
        """Approximate standard normal CDF using error function."""
        # Abramowitz and Stegun approximation
        a1 =  0.254829592
        a2 = -0.284496736
        a3 =  1.421413741
        a4 = -1.453152027
        a5 =  1.061405429
        p  =  0.3275911
        
        sign = 1 if z >= 0 else -1
        z = abs(z) / math.sqrt(2)
        
        t = 1.0 / (1.0 + p * z)
        y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-z * z)
        
        return 0.5 * (1.0 + sign * y)
    
    # P(X > line) = 1 - CDF(z)
    prob_under = normal_cdf(z_score)
    prob_over = 1.0 - prob_under
    
    # Convert to percentage and round
    vk_prob_over = round(prob_over * 100, 1)
    vk_prob_under = round(prob_under * 100, 1)
    
    # Cap at 1-99% to avoid 0% or 100% (nothing is certain)
    vk_prob_over = max(1.0, min(99.0, vk_prob_over))
    vk_prob_under = max(1.0, min(99.0, vk_prob_under))
    
    # Log the calculation for debugging
    logger.debug(f"[VK_MODEL] Statistical Calc: pred={adjusted_prediction:.2f}, line={line}, "
                f"σ={sigma:.3f} ({sigma_source}), z={z_score:.3f}, P(over)={vk_prob_over}%")
    
    # =========================================================================
    # STEP 5: Calculate VK Edge (vs PrizePicks implied)
    # =========================================================================
    if pp_implied_prob is None:
        # Calculate implied probability from DK odds
        if dk_odds is not None and dk_odds != 0:
            if dk_odds < 0:
                pp_implied_prob = abs(dk_odds) / (abs(dk_odds) + 100) * 100
            else:
                pp_implied_prob = 100 / (dk_odds + 100) * 100
        else:
            # Default to 50% implied probability
            pp_implied_prob = 50.0
    
    # VK Edge = Our probability - Their implied probability
    vk_edge = vk_prob_over - pp_implied_prob
    
    # Cap edge at reasonable bounds
    vk_edge = max(min(vk_edge, MAX_EDGE_PCT), MIN_EDGE_PCT)
    vk_edge = round(vk_edge, 1)
    
    # =========================================================================
    # STEP 6: Determine Verdict (MLR Directional Signal)
    # =========================================================================
    if vk_prob_over >= STRONG_THRESHOLD:
        vk_verdict = "STRONG_OVER"
        vk_recommendation = "STRONG_OVER"
    elif vk_prob_over >= LEAN_THRESHOLD:
        vk_verdict = "LEAN_OVER"
        vk_recommendation = "LEAN_OVER"
    elif vk_prob_under >= STRONG_THRESHOLD:
        vk_verdict = "STRONG_UNDER"
        vk_recommendation = "STRONG_UNDER"
    elif vk_prob_under >= LEAN_THRESHOLD:
        vk_verdict = "LEAN_UNDER"
        vk_recommendation = "LEAN_UNDER"
    else:
        vk_verdict = "NEUTRAL"
        vk_recommendation = "NEUTRAL"
    
    # Confidence score = max of over/under probability
    confidence_score = max(vk_prob_over, vk_prob_under)
    
    # Log the TRUE VARIANCE audit for debugging/verification
    logger.info(
        f"[VK_MODEL] TRUE VARIANCE AUDIT: "
        f"player={player_name or 'N/A'}, stat={stat_type or 'N/A'}, "
        f"pred={adjusted_prediction:.2f}, line={line}, "
        f"σ={sigma:.3f} ({sigma_source}), z={z_score:.3f}, "
        f"P(over)={vk_prob_over}%, edge={vk_edge}%"
    )
    
    return VKResult(
        vk_prob_over=vk_prob_over,
        vk_prob_under=vk_prob_under,
        vk_verdict=vk_verdict,
        vk_edge=vk_edge,
        vk_recommendation=vk_recommendation,
        confidence_score=confidence_score,
        is_valid=True,
        capped=capped,
        cap_reason=cap_reason,
        # v2.0: TRUE VARIANCE AUDITING FIELDS
        standard_deviation_used=round(sigma, 4),
        sigma_source=sigma_source,
        z_score=round(z_score, 4)
    )


def validate_vk_fields(record: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Validate that a record has all required VK fields.
    
    Args:
        record: The record to validate
        
    Returns:
        (is_valid, error_message)
    """
    missing = []
    invalid = []
    
    for field in REQUIRED_VK_FIELDS:
        value = record.get(field)
        
        if value is None:
            missing.append(field)
        elif field in ['vk_prob_over', 'vk_prob_under', 'vk_edge']:
            # Must be numeric
            if not isinstance(value, (int, float)):
                invalid.append(f"{field} must be numeric, got {type(value)}")
        elif field == 'vk_verdict':
            # Must be valid verdict string
            valid_verdicts = ['STRONG_OVER', 'LEAN_OVER', 'NEUTRAL', 'LEAN_UNDER', 'STRONG_UNDER']
            if value not in valid_verdicts:
                invalid.append(f"{field} must be one of {valid_verdicts}, got {value}")
    
    if missing:
        return False, f"Missing required VK fields: {missing}"
    
    if invalid:
        return False, f"Invalid VK fields: {invalid}"
    
    return True, "Valid"


def enforce_vk_fields(record: Dict[str, Any], context: str = "unknown", sport: str = "NBA") -> Dict[str, Any]:
    """
    Enforce VK fields on a record. If missing, calculate them.
    
    This is the MANDATORY handshake - no record passes without valid VK fields.
    
    v2.0: Now passes player_name and stat_type for TRUE VARIANCE lookup.
    
    Args:
        record: The record to enforce
        context: Context string for logging
        sport: "NBA" or "MLB" for DB lookup
        
    Returns:
        Record with valid VK fields including variance audit data
    """
    # Check if already valid
    is_valid, error = validate_vk_fields(record)
    
    if is_valid:
        return record
    
    logger.warning(f"[VK_ENFORCE] {context}: {error} - Calculating VK fields with TRUE VARIANCE")
    
    # Extract values for calculation
    predicted = record.get('vk_predicted') or record.get('raw_vk_pred') or record.get('season_avg') or 0
    line = record.get('line', 0)
    dk_odds = record.get('dk_odds', -110)
    season_avg = record.get('season_avg') or record.get('season_average', 0)
    
    # v2.0: Extract player_name and stat_type for TRUE VARIANCE lookup
    player_name = record.get('player_name')
    stat_type = record.get('stat_type') or record.get('stat_type_extracted')
    
    # v2.0: Check if record has std_dev or cv already
    std_dev = record.get('l10_std_dev') or record.get('std_dev_l10') or record.get('std_dev')
    cv = record.get('cv')
    
    # Calculate VK model with TRUE VARIANCE
    vk_result = calculate_vk_model(
        predicted_value=predicted,
        line=line,
        dk_odds=dk_odds,
        season_avg=season_avg,
        std_dev=std_dev,
        cv=cv,
        player_name=player_name,
        stat_type=stat_type,
        sport=sport
    )
    
    # Apply to record
    record['vk_prob_over'] = vk_result.vk_prob_over
    record['vk_prob_under'] = vk_result.vk_prob_under
    record['vk_verdict'] = vk_result.vk_verdict
    record['vk_edge'] = vk_result.vk_edge
    record['vk_recommendation'] = vk_result.vk_recommendation
    record['vk_confidence'] = vk_result.confidence_score
    
    # v2.0: Add variance audit fields
    record['vk_sigma_used'] = vk_result.standard_deviation_used
    record['vk_sigma_source'] = vk_result.sigma_source
    record['vk_z_score'] = vk_result.z_score
    
    if vk_result.capped:
        record['vk_capped'] = True
        record['vk_cap_reason'] = vk_result.cap_reason
    
    logger.info(f"[VK_ENFORCE] {context}: VK fields applied - "
               f"Over: {vk_result.vk_prob_over}%, Verdict: {vk_result.vk_verdict}, "
               f"σ: {vk_result.standard_deviation_used} ({vk_result.sigma_source})")
    
    return record


def bulk_enforce_vk_fields(records: list, context: str = "bulk", sport: str = "NBA") -> Tuple[list, int, int]:
    """
    Enforce VK fields on a list of records.
    
    v2.0: Now supports sport parameter for TRUE VARIANCE lookups.
    
    Args:
        records: List of records
        context: Context for logging
        sport: "NBA" or "MLB" for DB lookup
        
    Returns:
        (valid_records, success_count, error_count)
    """
    valid_records = []
    success_count = 0
    error_count = 0
    
    for i, record in enumerate(records):
        try:
            enforced = enforce_vk_fields(record, f"{context}[{i}]", sport=sport)
            
            # Final validation
            is_valid, error = validate_vk_fields(enforced)
            
            if is_valid:
                valid_records.append(enforced)
                success_count += 1
            else:
                logger.error(f"[VK_ENFORCE] {context}[{i}]: REJECTED - {error}")
                error_count += 1
                
        except Exception as e:
            logger.error(f"[VK_ENFORCE] {context}[{i}]: Exception - {e}")
            error_count += 1
    
    logger.info(f"[VK_ENFORCE] {context}: {success_count} valid, {error_count} rejected")
    
    return valid_records, success_count, error_count

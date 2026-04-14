"""
VK/MLR Model Enforcement Module v1.0
=====================================
Centralized Vegas-Killer (MLR) calculation and validation.

STRICT REQUIREMENTS:
- vk_prob_over: REQUIRED, never None (MLR Confidence Score)
- vk_prob_under: REQUIRED, never None (100 - vk_prob_over)
- vk_verdict: REQUIRED, never None (STRONG_OVER, LEAN_OVER, NEUTRAL, LEAN_UNDER, STRONG_UNDER)
- vk_edge: REQUIRED, never None (MLR Probability - Implied Probability)

Any record missing these fields is REJECTED from persistence.

Author: PropVision AI
Version: 1.0.0
"""
import logging
from typing import Dict, Any, Tuple, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

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


def calculate_vk_model(
    predicted_value: float,
    line: float,
    dk_odds: int = None,
    pp_implied_prob: float = None,
    season_avg: float = None,
    adjustment_pct: float = 0.0,
    require_market: bool = True,
    cv: float = None,  # Coefficient of Variation (stdev/mean) from player's logs
    std_dev: float = None  # Direct standard deviation if available
) -> VKResult:
    """
    Calculate VK/MLR model output using STATISTICAL DISTRIBUTION.
    
    FORMULA (Normal Distribution CDF):
    --------------------------------
    P(X > line) = 1 - Φ((line - predicted) / σ)
    
    Where:
    - Φ = Standard Normal CDF
    - σ = Standard Deviation (calculated from CV if not provided)
    - If no CV/stdev: σ = predicted * 0.20 (default 20% volatility)
    
    This properly converts a prediction vs line into a probability
    that accounts for the player's historical variance.
    
    Args:
        predicted_value: MLR predicted stat value (mean of distribution)
        line: Betting line (threshold)
        dk_odds: DraftKings odds (REQUIRED for Elite tiers)
        pp_implied_prob: PrizePicks implied probability (optional)
        season_avg: Player's season average (fallback for prediction)
        adjustment_pct: Any lineup/injury adjustment percentage
        require_market: If True, dk_odds must be valid (default True)
        cv: Coefficient of Variation from player's L10/season logs
        std_dev: Direct standard deviation if available
        
    Returns:
        VKResult with statistically accurate probabilities
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
    # STEP 3: Calculate Standard Deviation (σ)
    # =========================================================================
    # Priority: Direct std_dev > CV-based > Default 20% volatility
    
    if std_dev and std_dev > 0:
        sigma = std_dev
        sigma_source = "direct"
    elif cv and cv > 0:
        # σ = CV * mean (CV = σ/μ, so σ = CV * μ)
        sigma = cv * adjusted_prediction
        sigma_source = "cv"
    else:
        # Default: 20% of predicted value as volatility
        # This is conservative - real player variance is often 15-30%
        sigma = adjusted_prediction * 0.20
        sigma_source = "default_20pct"
    
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
    
    return VKResult(
        vk_prob_over=vk_prob_over,
        vk_prob_under=vk_prob_under,
        vk_verdict=vk_verdict,
        vk_edge=vk_edge,
        vk_recommendation=vk_recommendation,
        confidence_score=confidence_score,
        is_valid=True,
        capped=capped,
        cap_reason=cap_reason
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


def enforce_vk_fields(record: Dict[str, Any], context: str = "unknown") -> Dict[str, Any]:
    """
    Enforce VK fields on a record. If missing, calculate them.
    
    This is the MANDATORY handshake - no record passes without valid VK fields.
    
    Args:
        record: The record to enforce
        context: Context string for logging
        
    Returns:
        Record with valid VK fields
    """
    # Check if already valid
    is_valid, error = validate_vk_fields(record)
    
    if is_valid:
        return record
    
    logger.warning(f"[VK_ENFORCE] {context}: {error} - Calculating VK fields")
    
    # Extract values for calculation
    predicted = record.get('vk_predicted') or record.get('raw_vk_pred') or record.get('season_avg') or 0
    line = record.get('line', 0)
    dk_odds = record.get('dk_odds', -110)
    season_avg = record.get('season_avg') or record.get('season_average', 0)
    
    # Calculate VK model
    vk_result = calculate_vk_model(
        predicted_value=predicted,
        line=line,
        dk_odds=dk_odds,
        season_avg=season_avg
    )
    
    # Apply to record
    record['vk_prob_over'] = vk_result.vk_prob_over
    record['vk_prob_under'] = vk_result.vk_prob_under
    record['vk_verdict'] = vk_result.vk_verdict
    record['vk_edge'] = vk_result.vk_edge
    record['vk_recommendation'] = vk_result.vk_recommendation
    record['vk_confidence'] = vk_result.confidence_score
    
    if vk_result.capped:
        record['vk_capped'] = True
        record['vk_cap_reason'] = vk_result.cap_reason
    
    logger.info(f"[VK_ENFORCE] {context}: VK fields applied - "
               f"Over: {vk_result.vk_prob_over}%, Verdict: {vk_result.vk_verdict}")
    
    return record


def bulk_enforce_vk_fields(records: list, context: str = "bulk") -> Tuple[list, int, int]:
    """
    Enforce VK fields on a list of records.
    
    Args:
        records: List of records
        context: Context for logging
        
    Returns:
        (valid_records, success_count, error_count)
    """
    valid_records = []
    success_count = 0
    error_count = 0
    
    for i, record in enumerate(records):
        try:
            enforced = enforce_vk_fields(record, f"{context}[{i}]")
            
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

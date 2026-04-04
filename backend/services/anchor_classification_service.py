"""
Sharp Movement Classification Service v2.0
===========================================
BOVADA LINE DELTA CLASSIFICATION

Analyzes the delta between DFS lines (PrizePicks) and Sharp Book lines (Bovada)
to determine Sharp Movement and Trap Risk indicators.

Classification Rules:
- sharp_movement (True): Favorable delta where Bovada line suggests value
- trap_risk (True): Hook risk or suspect line bait detected

Tier Routing:
- SAFE_HAVEN: High confidence, strong sharp movement, no traps
- FRONT_LINES: Moderate confidence, some sharp movement
- WAR_ZONE: Higher variance plays with sharp movement
- MINEFIELD: Trap risk detected (hook_risk OR suspect_line_bait)

Sharp Movement Detection:
- DFS line significantly below Bovada implied line = Sharp Value (Over)
- DFS line significantly above Bovada implied line = Sharp Value (Under)
- Delta threshold: >= 1.5 points for stat props

Trap Risk Detection:
- hook_risk: Line sits at psychological numbers (x.5 hooks)
- suspect_line_bait: Sharp money moving opposite direction
"""
import logging
from typing import Dict, List, Any, Optional
from collections import defaultdict

logger = logging.getLogger(__name__)

# Sharp Movement Thresholds
SHARP_MOVEMENT_THRESHOLD = 1.5  # Minimum delta for sharp movement
SIGNIFICANT_MOVEMENT_THRESHOLD = 3.0  # Major sharp movement

# Trap Risk Thresholds
# Hook risk only triggers when hit rate is borderline (50-70%)
# Lines ending in .5 are common on PrizePicks, so we need context
HOOK_RISK_DECIMALS = [0.5]  # Lines ending in .5 are potential hooks
HOOK_RISK_HIT_RATE_MAX = 70  # Only flag hook if hit rate is below this
HOOK_RISK_HIT_RATE_MIN = 40  # Don't flag if hit rate is very low (already suspect)
BAIT_ODDS_THRESHOLD = 150  # Odds above +150 with low hit rate = bait


def _normalize_name(name: str) -> str:
    """Normalize player name for matching."""
    if not name:
        return ""
    normalized = name.lower().strip()
    normalized = normalized.replace(".", "").replace(",", "")
    for suffix in [" jr", " sr", " ii", " iii", " iv", " v"]:
        if normalized.endswith(suffix):
            normalized = normalized[:-len(suffix)].strip()
    return normalized


def detect_sharp_movement(prop: Dict, bovada_line: float = None) -> Dict[str, Any]:
    """
    Detect sharp movement by comparing DFS line to Sharp Book (Bovada) line.
    
    Args:
        prop: Prop dictionary with line, bovada_odds, etc.
        bovada_line: Optional override for Bovada implied line
        
    Returns:
        Dict with sharp_movement, movement_delta, movement_direction, movement_strength
    """
    dfs_line = prop.get("line", 0)
    prop_bovada_line = bovada_line or prop.get("bovada_implied_line", prop.get("sharp_line"))
    
    # Calculate L5/L10 average as reference point
    l5_avg = prop.get("l5_avg", 0) or 0
    l10_avg = prop.get("l10_avg", 0) or 0
    season_avg = prop.get("season_avg", 0) or 0
    
    # Use best available reference
    reference_avg = l5_avg or l10_avg or season_avg or dfs_line
    
    # Calculate delta
    if prop_bovada_line and dfs_line:
        delta = prop_bovada_line - dfs_line
    elif reference_avg:
        delta = reference_avg - dfs_line
    else:
        delta = 0
    
    # Determine movement strength
    abs_delta = abs(delta)
    if abs_delta >= SIGNIFICANT_MOVEMENT_THRESHOLD:
        movement_strength = "significant"
    elif abs_delta >= SHARP_MOVEMENT_THRESHOLD:
        movement_strength = "moderate"
    else:
        movement_strength = "minimal"
    
    # Determine if this is favorable sharp movement
    sharp_movement = abs_delta >= SHARP_MOVEMENT_THRESHOLD
    
    # Direction: positive delta = player expected to exceed line (OVER value)
    # negative delta = player expected to stay under line (UNDER value)
    movement_direction = "over_value" if delta > 0 else "under_value" if delta < 0 else "neutral"
    
    return {
        "sharp_movement": sharp_movement,
        "movement_delta": round(delta, 2),
        "movement_direction": movement_direction,
        "movement_strength": movement_strength,
        "reference_line": prop_bovada_line or reference_avg,
        "dfs_line": dfs_line
    }


def detect_trap_risk(prop: Dict) -> Dict[str, Any]:
    """
    Detect trap risk indicators on a prop.
    
    Trap indicators:
    - hook_risk: Line ends in .5 (psychological hook)
    - suspect_line_bait: High odds with low historical hit rate
    - sharp_opposite: Sharp money moving opposite to public
    
    Args:
        prop: Prop dictionary
        
    Returns:
        Dict with trap_risk, hook_risk, suspect_line_bait, trap_reasons
    """
    line = prop.get("line", 0)
    price = prop.get("price", 0)
    l10_hit_rate = prop.get("l10_hit_rate", 50)
    
    # Get existing sidecar flags from HookBaitDetector (Mode-based detection)
    sidecar = prop.get("sidecar", {})
    existing_hook_risk = sidecar.get("hook_risk", False)
    existing_bait = sidecar.get("suspect_line_bait", False)
    
    trap_reasons = []
    
    # Hook Risk: Use refined Mode-based detection from sidecar
    # Only flag as hook if HookBaitDetector detected it (Mode frequency >= 25%, line ±0.5 from Mode)
    hook_risk = existing_hook_risk
    hook_warning = sidecar.get("hook_warning")
    if hook_risk and hook_warning:
        trap_reasons.append(f"Hook Risk: {hook_warning}")
    elif hook_risk:
        trap_reasons.append(f"Hook line at {line}")
    
    # Suspect Line Bait: Use refined detection from sidecar OR fallback to high odds + low hit rate
    bait_warning = sidecar.get("bait_warning")
    suspect_line_bait = existing_bait
    
    # Fallback for props without proper sidecar analysis
    if not suspect_line_bait and price >= BAIT_ODDS_THRESHOLD and l10_hit_rate < 40:
        suspect_line_bait = True
        trap_reasons.append(f"High odds (+{price}) with {l10_hit_rate}% L10 hit rate")
    elif suspect_line_bait and bait_warning:
        trap_reasons.append(f"Bait Risk: {bait_warning}")
    
    # Combined trap risk
    trap_risk = hook_risk or suspect_line_bait
    
    return {
        "trap_risk": trap_risk,
        "hook_risk": hook_risk,
        "suspect_line_bait": suspect_line_bait,
        "trap_reasons": trap_reasons
    }


def classify_props_by_movement(props: List[Dict], player_stats: Dict[str, Dict] = None) -> List[Dict]:
    """
    Apply Sharp Movement classification to all props.
    
    Replaces legacy Demon/Goblin classification with:
    - sharp_movement: Boolean indicating favorable line delta
    - trap_risk: Boolean indicating trap detected
    - tier_label: SAFE_HAVEN, FRONT_LINES, WAR_ZONE, or MINEFIELD
    
    Args:
        props: List of prop dictionaries
        player_stats: Optional player statistics lookup
        
    Returns:
        Props list with sharp movement classifications
    """
    if not props:
        return props
    
    classified_props = []
    sharp_count = 0
    trap_count = 0
    neutral_count = 0
    
    for prop in props:
        # Detect sharp movement
        movement = detect_sharp_movement(prop)
        prop["sharp_movement"] = movement["sharp_movement"]
        prop["movement_delta"] = movement["movement_delta"]
        prop["movement_direction"] = movement["movement_direction"]
        prop["movement_strength"] = movement["movement_strength"]
        
        # Detect trap risk
        trap = detect_trap_risk(prop)
        prop["trap_risk"] = trap["trap_risk"]
        prop["hook_risk"] = trap["hook_risk"]
        prop["suspect_line_bait"] = trap["suspect_line_bait"]
        prop["trap_reasons"] = trap["trap_reasons"]
        
        # Update sidecar for consistency
        if "sidecar" not in prop:
            prop["sidecar"] = {}
        prop["sidecar"]["hook_risk"] = trap["hook_risk"]
        prop["sidecar"]["suspect_line_bait"] = trap["suspect_line_bait"]
        
        # Determine tier routing
        if trap["trap_risk"]:
            prop["tier_label"] = prop["tier"].upper().replace("_", " ")
            trap_count += 1
        elif movement["sharp_movement"]:
            # Let Ferrari scoring determine Safe Haven vs Front Lines vs War Zone
            # Default to FRONT_LINE until Ferrari scoring refines it
            prop["tier_label"] = "FRONT_LINE"
            sharp_count += 1
        else:
            prop["tier_label"] = "STANDARD"
            neutral_count += 1
        
        # PRESERVE PRIZEPICKS CLASSIFICATION
        # is_demon (price = +100) and is_goblin (price != +100 on alternate markets)
        # These come from the odds API and are used for tier routing:
        # - Safe Haven: GOBLINS only (high probability favorites)
        # - Front Lines: BOTH demons and goblins
        # - War Zone: DEMONS only (higher variance with multipliers)
        # DO NOT overwrite these values!
        
        classified_props.append(prop)
    
    logger.info(f"[SHARP_CLASSIFY] Processed {len(classified_props)} props: "
                f"{sharp_count} sharp movement, {trap_count} traps, {neutral_count} neutral")
    
    return classified_props


def get_tier_from_odds(bovada_odds: int) -> str:
    """
    Determine tier based on Bovada odds.
    
    Tier Windows:
    - SAFE_HAVEN: odds <= -250 (very high implied probability)
    - FRONT_LINES: -245 < odds <= -115 (moderate implied probability)
    - WAR_ZONE: odds > -114 (lower implied probability, higher variance)
    
    Args:
        bovada_odds: American odds from Bovada
        
    Returns:
        Tier label string
    """
    if bovada_odds is None:
        return "FRONT_LINE"
    
    if bovada_odds <= -250:
        return "SAFE_HAVEN"
    elif bovada_odds <= -115:
        return "FRONT_LINE"
    else:
        return "WAR_ZONE"


def group_props_by_player(props: List[Dict]) -> Dict[str, Dict]:
    """
    Group props by player and organize by stat type.
    
    Args:
        props: List of classified props
        
    Returns:
        Dict mapping player names to their organized props
    """
    grouped = defaultdict(lambda: {"props": [], "stats": {}})
    
    for prop in props:
        player_name = prop.get("player_name", "Unknown")
        stat_type = prop.get("stat_type_extracted", "unknown")
        
        grouped[player_name]["props"].append(prop)
        
        if stat_type not in grouped[player_name]["stats"]:
            grouped[player_name]["stats"][stat_type] = []
        grouped[player_name]["stats"][stat_type].append(prop)
    
    return dict(grouped)


def get_prop_tier_summary(props: List[Dict]) -> Dict[str, int]:
    """
    Get summary counts by tier.
    
    Returns:
        Dict with counts per tier
    """
    summary = {
        "safe_haven": 0,
        "front_lines": 0,
        "war_zone": 0,
        "minefield": 0,
        "standard": 0,
        "total": len(props)
    }
    
    for prop in props:
        tier = prop.get("tier_label", "STANDARD").upper()
        if tier == "SAFE_HAVEN":
            summary["safe_haven"] += 1
        elif tier in ["FRONT_LINE", "FRONT_LINES"]:
            summary["front_lines"] += 1
        elif tier == "WAR_ZONE":
            summary["war_zone"] += 1
        elif tier == "MINEFIELD":
            summary["minefield"] += 1
        else:
            summary["standard"] += 1
    
    return summary


# =============================================================================
# LEGACY COMPATIBILITY FUNCTIONS (DEPRECATED)
# =============================================================================

def classify_props_by_anchor(props: List[Dict], player_stats: Dict[str, Dict] = None) -> List[Dict]:
    """
    DEPRECATED: Legacy function for backward compatibility.
    Now routes to classify_props_by_movement().
    """
    logger.warning("[DEPRECATED] classify_props_by_anchor called - routing to classify_props_by_movement")
    return classify_props_by_movement(props, player_stats)


def get_demon_insight(prop: Dict) -> str:
    """DEPRECATED: Returns generic insight instead."""
    if prop.get("sharp_movement"):
        return f"Sharp movement detected: {prop.get('movement_direction', 'neutral')}"
    return "Standard line"


def get_goblin_insight(prop: Dict) -> str:
    """DEPRECATED: Returns generic insight instead."""
    if prop.get("trap_risk"):
        reasons = prop.get("trap_reasons", [])
        return f"Trap risk detected: {', '.join(reasons)}" if reasons else "Potential trap"
    return "Standard line"

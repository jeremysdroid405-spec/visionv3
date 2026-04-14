"""
Normalize to Intel Mapping
===========================
Format Normalization Layer for Vision Intel Suite.

Ensures every prop has the exact keys required by the Vision Intel Component Registry:
- sp_matchup (Starting Pitcher matchup)
- park_factors (Stadium environment)
- split_stats (L/R, Home/Away splits)
- variance_data (L20 Stabilized Shield)
- vk_data (Vegas Killer prediction)
- scout_badges (Badge array)
- vision_intel (AI-generated summary)

NO "EMPTY SHELL" PROPS - If enrichment fails, prop is NOT cached.

Author: PropVision AI
Version: 1.0.0
"""

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# =============================================================================
# VISION INTEL COMPONENT REGISTRY - Required Keys
# =============================================================================

MLB_REQUIRED_KEYS = {
    'base': ['player_name', 'stat_type', 'line', 'team'],
    'intel': [
        'vk_data',           # Vegas Killer prediction block
        'vision_intel',      # AI summary string
        'vision_summary',    # Human-readable matchup summary
        'scout_badges',      # Badge array
        'matchup_analysis',  # Physical friction breakdown
        'l20_variance',      # L20 Stabilized Shield variance data
    ],
    'matchup_analysis_keys': [
        'splits',            # L/R splits
        'park',              # Park factors
        'opponent',          # Opponent K-rate
        'trends',            # L10/L20 trends
        'discipline',        # Plate discipline
    ],
    'vk_data_keys': [
        'predicted',         # MLR prediction
        'prob_over',         # P(OVER) percentage
        'prob_under',        # P(UNDER) percentage
        'edge',              # VK Edge
        'verdict',           # STRONG_OVER, LEAN_OVER, etc.
        'sigma_used',        # L20 sigma
        'sigma_source',      # Audit trail
        'z_score',           # Z-score value
    ],
}

NBA_REQUIRED_KEYS = {
    'base': ['player_name', 'stat_type', 'line', 'team'],
    'intel': [
        'vk_data',
        'vision_intel',
        'scout_badges',
        'intel_suite',
        'l20_variance',
    ],
    'vk_data_keys': [
        'predicted',
        'prob_over',
        'prob_under',
        'edge',
        'verdict',
        'sigma_used',
    ],
}


# =============================================================================
# FORMAT VALIDATION
# =============================================================================

def validate_prop_has_intel(prop: Dict, sport: str = "MLB") -> bool:
    """
    Check if a prop has complete intel data.
    
    Returns True only if ALL required intel keys are present.
    This prevents "Empty Shell" props from being cached.
    """
    required = MLB_REQUIRED_KEYS if sport.upper() == "MLB" else NBA_REQUIRED_KEYS
    
    # Check for vision_intel (AI summary) - REQUIRED
    if not prop.get('vision_intel') and not prop.get('vision_summary'):
        return False
    
    # Check for vk_data (prediction) - REQUIRED
    vk_data = prop.get('vk_data', {})
    if not vk_data:
        return False
    
    # Check vk_data has prediction (Lasso uses 'projection', legacy uses 'predicted')
    if vk_data.get('predicted') is None and vk_data.get('projection') is None:
        return False
    
    # Check for scout_badges (can be empty list, but must exist)
    if 'scout_badges' not in prop:
        return False
    
    return True


def get_missing_intel_keys(prop: Dict, sport: str = "MLB") -> List[str]:
    """Get list of missing intel keys for debugging."""
    required = MLB_REQUIRED_KEYS if sport.upper() == "MLB" else NBA_REQUIRED_KEYS
    missing = []
    
    for key in required['intel']:
        if key not in prop or prop[key] is None:
            missing.append(key)
    
    return missing


# =============================================================================
# FORMAT NORMALIZATION
# =============================================================================

def normalize_prop_format(prop: Dict, sport: str = "MLB") -> Dict:
    """
    Normalize prop format to ensure all required keys exist.
    
    This does NOT fill in values - just ensures structure exists
    for downstream processing.
    """
    normalized = prop.copy()
    
    # Ensure base keys
    normalized.setdefault('player_name', '')
    normalized.setdefault('stat_type', normalized.get('stat_type_raw', ''))
    normalized.setdefault('line', 0)
    normalized.setdefault('team', '')
    normalized.setdefault('sport', sport)
    
    # Ensure intel structure (empty placeholders)
    normalized.setdefault('vk_data', {})
    normalized.setdefault('vision_intel', None)
    normalized.setdefault('vision_summary', None)
    normalized.setdefault('scout_badges', [])
    normalized.setdefault('matchup_analysis', {})
    normalized.setdefault('l20_variance', {})
    
    # Ensure matchup_analysis sub-keys
    ma = normalized['matchup_analysis']
    ma.setdefault('splits', {})
    ma.setdefault('park', {})
    ma.setdefault('opponent', {})
    ma.setdefault('trends', {})
    ma.setdefault('discipline', {})
    
    return normalized


def normalize_from_nested_props(player_record: Dict, sport: str = "MLB") -> List[Dict]:
    """
    Flatten nested props format into individual normalized props.
    
    Input format (from cached_board):
    {
        'player_name': 'Aaron Judge',
        'team': 'NYY',
        'props': [
            {'stat_type': 'Hits', 'line': 1.5, ...},
            {'stat_type': 'Total Bases', 'line': 2.5, ...},
        ]
    }
    
    Output format (flat, normalized):
    [
        {'player_name': 'Aaron Judge', 'team': 'NYY', 'stat_type': 'Hits', 'line': 1.5, ...},
        {'player_name': 'Aaron Judge', 'team': 'NYY', 'stat_type': 'Total Bases', 'line': 2.5, ...},
    ]
    """
    flat_props = []
    
    player_name = player_record.get('player_name', '')
    team = player_record.get('team', '')
    bdl_id = player_record.get('bdl_id')
    
    props = player_record.get('props', [])
    
    if not props:
        # Single prop format - just normalize it
        normalized = normalize_prop_format(player_record, sport)
        flat_props.append(normalized)
    else:
        # Nested props format - flatten
        for prop in props:
            flat_prop = {
                'player_name': player_name,
                'team': team,
                'bdl_id': bdl_id,
                'sport': sport,
                **prop
            }
            normalized = normalize_prop_format(flat_prop, sport)
            flat_props.append(normalized)
    
    return flat_props


# =============================================================================
# INTEL ENRICHMENT MERGER
# =============================================================================

def merge_intel_into_prop(base_prop: Dict, intel_data: Dict) -> Dict:
    """
    Merge enrichment data into base prop.
    
    CRITICAL: This creates the "Apex" version of the prop.
    Only call this AFTER enrichment succeeds.
    """
    merged = base_prop.copy()
    
    # Merge vk_data
    if intel_data.get('vk_data'):
        merged['vk_data'] = intel_data['vk_data']
    
    # Merge vision summary
    if intel_data.get('vision_summary'):
        merged['vision_summary'] = intel_data['vision_summary']
    
    # Set vision_intel to vision_summary if not separately provided
    if intel_data.get('vision_intel'):
        merged['vision_intel'] = intel_data['vision_intel']
    elif intel_data.get('vision_summary'):
        merged['vision_intel'] = intel_data['vision_summary']
    
    # Merge scout badges
    if intel_data.get('scout_badges'):
        merged['scout_badges'] = intel_data['scout_badges']
    
    # Merge matchup analysis
    if intel_data.get('matchup_analysis'):
        merged['matchup_analysis'] = intel_data['matchup_analysis']
    
    # Merge L20 variance
    if intel_data.get('l20_variance'):
        merged['l20_variance'] = intel_data['l20_variance']
    
    # Merge intel_suite (NBA)
    if intel_data.get('intel_suite'):
        merged['intel_suite'] = intel_data['intel_suite']
    
    # Mark as enriched
    merged['_enriched'] = True
    merged['_enriched_at'] = datetime.now(timezone.utc).isoformat()
    
    return merged


# =============================================================================
# PROP ID GENERATION
# =============================================================================

def generate_prop_id(prop: Dict) -> str:
    """Generate unique prop ID from prop data."""
    player = prop.get('player_name', '') or ''
    stat = prop.get('stat_type', '') or prop.get('stat_type_raw', '') or ''
    line = prop.get('line', 0) or 0
    book = prop.get('bookmaker', 'dk') or 'dk'
    
    return f"{player}|{stat}|{line}|{book}".lower().replace(' ', '_')


# =============================================================================
# ENRICHMENT STATUS CHECKS
# =============================================================================

def prop_needs_enrichment(prop: Dict, sport: str = "MLB") -> bool:
    """
    Check if prop needs enrichment.
    
    Returns True if:
    - Missing vk_data.predicted
    - Missing vision_intel/vision_summary
    - Not marked as _enriched
    """
    # Check for enrichment marker
    if prop.get('_enriched'):
        return False
    
    # Check for prediction
    vk_data = prop.get('vk_data', {})
    if vk_data.get('predicted') is None:
        return True
    
    # Check for vision summary
    if not prop.get('vision_intel') and not prop.get('vision_summary'):
        return True
    
    return False


def get_enrichment_status(prop: Dict) -> Dict:
    """Get detailed enrichment status for debugging."""
    vk_data = prop.get('vk_data', {})
    
    return {
        'has_prediction': vk_data.get('predicted') is not None,
        'has_prob_over': vk_data.get('prob_over') is not None,
        'has_edge': vk_data.get('edge') is not None,
        'has_vision_intel': bool(prop.get('vision_intel')),
        'has_vision_summary': bool(prop.get('vision_summary')),
        'has_scout_badges': bool(prop.get('scout_badges')),
        'has_matchup_analysis': bool(prop.get('matchup_analysis')),
        'has_l20_variance': bool(prop.get('l20_variance')),
        'is_enriched': prop.get('_enriched', False),
        'enriched_at': prop.get('_enriched_at'),
    }

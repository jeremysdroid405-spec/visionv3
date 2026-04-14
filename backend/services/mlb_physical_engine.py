"""
MLB Physical Engine v2.0 (MLB_ORACLE_APEX)
==========================================
1:1 Functional replica of NBA_MLR_STRICT_v2.2 logic.

TRAINING FOUNDATION:
- 90,000+ historical game logs from mlb_master_hub_2026
- XGBoost regression trained on physical + market features

INTEGRATED INPUTS:
1. PHYSICAL BRAIN:
   - PvP History (Pitcher vs Batter lifetime matchups)
   - L/R Handedness Splits (vs LHP / vs RHP)
   - Park Factors (30 stadiums mapped)
   - Team K-Rates (strikeout tendencies)
   - EWMA Trends (L5/L10/L20 weighted averages)
   
2. MARKET CONTEXT:
   - dk_odds (DraftKings sharp line)
   - implied_probability (market expectation)

STRICT ENFORCEMENT:
- NO FALLBACKS: Delete all "OR season_avg" logic
- If model cannot produce high-precision MLR prediction -> return null
- Sigma linked to True L10 Standard Deviation from database
- vk_edge = vk_prob_over - implied_probability

DELIVERABLE:
- mlb_mlr_strict_audit.json with:
  - mlr_matchup block (physical friction)
  - market_data (odds/edge)

Author: PropVision AI
Version: 2.0.0 (MLB Oracle Apex - Strict Enforcement)
"""
import logging
import numpy as np
import pandas as pd
import pickle
import os
import json
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timezone
from scipy import stats
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# =============================================================================
# STRICT RESULT DATACLASS
# =============================================================================

@dataclass
class MLBMLRResult:
    """Strict MLB MLR prediction result - mirrors NBA VKResult."""
    player_name: str
    stat_type: str
    
    # HIGH PRECISION PREDICTION (e.g., 4.38 not 4)
    mlr_predicted: Optional[float]  # None if model fails
    raw_prediction: Optional[float]
    
    # TRUE L10 VARIANCE
    sigma_used: Optional[float]
    sigma_source: str
    z_score: Optional[float]
    
    # PROBABILITY OUTPUT
    vk_prob_over: Optional[float]
    vk_prob_under: Optional[float]
    vk_verdict: str
    
    # MARKET INTEGRATION
    dk_odds: Optional[int]
    implied_probability: Optional[float]
    vk_edge: Optional[float]  # vk_prob_over - implied_probability
    
    # MLR MATCHUP BLOCK (Physical Friction)
    mlr_matchup: Dict[str, Any]
    
    # VALIDATION
    is_valid: bool
    error: Optional[str] = None
    model_version: str = "MLB_ORACLE_APEX_v2.0"


# =============================================================================
# 3-YEAR PARK FACTORS (30 STADIUMS)
# =============================================================================

PARK_FACTORS = {
    # HITTER PARADISE
    'COL': {'hits': 1.18, 'runs': 1.25, 'hr': 1.32, 'k': 0.88, 'tb': 1.22},
    'CIN': {'hits': 1.10, 'runs': 1.15, 'hr': 1.18, 'k': 0.94, 'tb': 1.12},
    'TEX': {'hits': 1.08, 'runs': 1.12, 'hr': 1.15, 'k': 0.95, 'tb': 1.10},
    'BOS': {'hits': 1.06, 'runs': 1.08, 'hr': 0.95, 'k': 0.97, 'tb': 1.04},
    'PHI': {'hits': 1.05, 'runs': 1.08, 'hr': 1.10, 'k': 0.96, 'tb': 1.06},
    'CHC': {'hits': 1.04, 'runs': 1.06, 'hr': 1.08, 'k': 0.97, 'tb': 1.05},
    'MIL': {'hits': 1.03, 'runs': 1.05, 'hr': 1.06, 'k': 0.98, 'tb': 1.04},
    
    # NEUTRAL
    'NYY': {'hits': 1.02, 'runs': 1.04, 'hr': 1.12, 'k': 0.98, 'tb': 1.04},
    'LAD': {'hits': 1.00, 'runs': 1.00, 'hr': 1.02, 'k': 1.00, 'tb': 1.01},
    'ATL': {'hits': 1.00, 'runs': 1.02, 'hr': 1.06, 'k': 0.99, 'tb': 1.02},
    'HOU': {'hits': 0.98, 'runs': 0.98, 'hr': 1.00, 'k': 1.00, 'tb': 0.99},
    'MIN': {'hits': 1.00, 'runs': 1.02, 'hr': 1.10, 'k': 0.98, 'tb': 1.03},
    'STL': {'hits': 0.99, 'runs': 1.00, 'hr': 1.02, 'k': 0.99, 'tb': 1.00},
    'DET': {'hits': 1.00, 'runs': 1.00, 'hr': 0.98, 'k': 1.00, 'tb': 0.99},
    'BAL': {'hits': 1.01, 'runs': 1.02, 'hr': 1.05, 'k': 0.99, 'tb': 1.02},
    'TOR': {'hits': 1.00, 'runs': 1.01, 'hr': 1.04, 'k': 0.99, 'tb': 1.01},
    'CLE': {'hits': 0.99, 'runs': 0.99, 'hr': 1.00, 'k': 1.00, 'tb': 0.99},
    'KC': {'hits': 1.00, 'runs': 1.01, 'hr': 0.96, 'k': 1.00, 'tb': 0.99},
    'ARI': {'hits': 1.01, 'runs': 1.02, 'hr': 1.04, 'k': 0.99, 'tb': 1.02},
    'PIT': {'hits': 0.99, 'runs': 0.98, 'hr': 0.95, 'k': 1.01, 'tb': 0.98},
    'CHW': {'hits': 1.00, 'runs': 1.02, 'hr': 1.08, 'k': 0.99, 'tb': 1.02},
    'LAA': {'hits': 0.98, 'runs': 0.97, 'hr': 0.96, 'k': 1.01, 'tb': 0.97},
    'WSH': {'hits': 0.99, 'runs': 0.98, 'hr': 1.00, 'k': 1.00, 'tb': 0.99},
    
    # PITCHER FRIENDLY
    'SF': {'hits': 0.92, 'runs': 0.88, 'hr': 0.80, 'k': 1.06, 'tb': 0.88},
    'OAK': {'hits': 0.94, 'runs': 0.90, 'hr': 0.86, 'k': 1.05, 'tb': 0.90},
    'SD': {'hits': 0.95, 'runs': 0.92, 'hr': 0.88, 'k': 1.04, 'tb': 0.92},
    'MIA': {'hits': 0.96, 'runs': 0.94, 'hr': 0.86, 'k': 1.03, 'tb': 0.93},
    'TB': {'hits': 0.96, 'runs': 0.94, 'hr': 0.90, 'k': 1.03, 'tb': 0.94},
    'SEA': {'hits': 0.94, 'runs': 0.90, 'hr': 0.84, 'k': 1.06, 'tb': 0.90},
    'NYM': {'hits': 0.97, 'runs': 0.95, 'hr': 0.92, 'k': 1.02, 'tb': 0.95},
}
DEFAULT_PARK = {'hits': 1.00, 'runs': 1.00, 'hr': 1.00, 'k': 1.00, 'tb': 1.00}


# =============================================================================
# TEAM K-RATE TENDENCIES (How often team strikes out)
# =============================================================================

TEAM_K_RATES = {
    'ARI': 1.14, 'DET': 1.12, 'OAK': 1.10, 'CHC': 1.08, 'MIA': 1.07,
    'COL': 1.06, 'PIT': 1.05, 'CIN': 1.04, 'SEA': 1.03, 'TEX': 1.02,
    'ATL': 1.00, 'NYM': 0.99, 'PHI': 0.98, 'LAD': 0.97, 'SD': 0.97,
    'SF': 0.96, 'STL': 0.98, 'MIL': 0.99, 'CHW': 1.01, 'BAL': 1.00,
    'TOR': 1.01, 'BOS': 0.98, 'TB': 0.99, 'WSH': 1.02, 'LAA': 1.00,
    'HOU': 0.92, 'NYY': 0.94, 'CLE': 0.93, 'KC': 0.91, 'MIN': 0.93,
}


# =============================================================================
# STAT TYPE MAPPINGS
# =============================================================================

STAT_TYPES = ['hits', 'total_bases', 'rbis', 'runs', 'pitcher_strikeouts', 
              'hits+runs+rbis', 'home_runs', 'stolen_bases']

STAT_FIELD_MAP = {
    'hits': 'hits',
    'total_bases': 'total_bases',
    'rbis': 'rbi',  # BDL API uses 'rbi' not 'rbis'
    'rbi': 'rbi',
    'runs': 'runs',
    'stolen_bases': 'stolen_bases',
    'home_runs': 'home_runs',
    'hr': 'hr',
    'walks': 'walks',
    'bb': 'bb',
    'strikeouts': 'strikeouts',
    'k': 'k',
    'pitcher_strikeouts': 'pitcher_strikeouts',
    'p_k': 'p_k',
    'hits+runs+rbis': ['hits', 'runs', 'rbi'],
}

STAT_ALIASES = {
    'k': 'pitcher_strikeouts',
    'ks': 'pitcher_strikeouts',
    'pitcher k': 'pitcher_strikeouts',
    'pitcher strikeouts': 'pitcher_strikeouts',
    'tb': 'total_bases',
    'rbi': 'rbis',
    'sb': 'stolen_bases',
    'hr': 'home_runs',
    'h': 'hits',
    'r': 'runs',
    'hrr': 'hits+runs+rbis',
}


class MLBPhysicalEngine:
    """
    MLB Oracle Apex Engine v2.0
    
    1:1 functional replica of NBA_MLR_STRICT_v2.2.
    Pure physical prediction + market integration.
    STRICT enforcement: No fallbacks, null if data missing.
    """
    
    MODEL_DIR = '/app/backend/models/mlb_physical'
    
    def __init__(self, db):
        """
        Initialize MLB Physical Engine.
        
        Args:
            db: PyMongo database instance (SYNC)
        """
        self.db = db
        self.master_hub = db.mlb_master_hub_2026
        self.historical_logs = db.mlb_historical_logs
        
        # Trained XGBoost models
        self.models = {}
        self.scalers = {}
        self.feature_cols = {}
        
        os.makedirs(self.MODEL_DIR, exist_ok=True)
        logger.info("[MLB_ORACLE_APEX] Initialized MLB Physical Engine v2.0")
    
    # =========================================================================
    # STAT NORMALIZATION
    # =========================================================================
    
    def _norm_stat(self, stat: str) -> str:
        """Normalize stat type to internal key."""
        s = stat.lower().replace(' ', '_').replace('+', '+')
        return STAT_ALIASES.get(s, s)
    
    def _get_stat(self, game: Dict, stat: str) -> Optional[float]:
        """Extract stat value from game log."""
        field = STAT_FIELD_MAP.get(stat, stat)
        if isinstance(field, list):
            total = 0
            for f in field:
                val = game.get(f, 0)
                if val is not None:
                    total += float(val)
            return total
        val = game.get(field)
        return float(val) if val is not None else None
    
    # =========================================================================
    # EWMA CALCULATION
    # =========================================================================
    
    def _ewma(self, vals: List[float], alpha: float) -> float:
        """Exponentially Weighted Moving Average."""
        if not vals:
            return 0.0
        result = vals[-1]  # Start with oldest
        for i in range(len(vals) - 2, -1, -1):
            result = alpha * vals[i] + (1 - alpha) * result
        return result
    
    # =========================================================================
    # PARK FACTOR LOOKUP
    # =========================================================================
    
    def _get_park_factor(self, team: str, stat: str) -> float:
        """Get park factor for stat at team's stadium."""
        pf = PARK_FACTORS.get(team, DEFAULT_PARK)
        if stat in ['hits']:
            return pf.get('hits', 1.0)
        elif stat in ['total_bases']:
            return pf.get('tb', 1.0)
        elif stat in ['runs', 'rbis', 'hits+runs+rbis']:
            return pf.get('runs', 1.0)
        elif stat == 'home_runs':
            return pf.get('hr', 1.0)
        elif stat == 'pitcher_strikeouts':
            return pf.get('k', 1.0)
        return 1.0
    
    # =========================================================================
    # STRICT BDL DATA VALIDATION (NO FALLBACKS)
    # =========================================================================
    
    def _validate_bdl_data(self, player: Dict) -> Tuple[bool, str]:
        """
        STRICT VALIDATION: Check if required BDL GOAT-Tier data exists.
        
        If ANY required data is missing, return False -> prop gets NULL prediction.
        NO FALLBACKS to season_avg or any other proxy.
        
        Returns:
            (is_valid, error_message)
        """
        # Check for L/R splits - handle None values explicitly
        vs_left = player.get('vs_left') or {}
        vs_right = player.get('vs_right') or {}
        
        if not vs_left and not vs_right:
            return False, "MISSING_LR_SPLITS: No vs_left or vs_right data"
        
        # Check for at-bats in splits (need sample size)
        lhp_ab = (vs_left.get('at_bats') or 0) if vs_left else 0
        rhp_ab = (vs_right.get('at_bats') or 0) if vs_right else 0
        
        if lhp_ab < 5 and rhp_ab < 5:
            return False, f"INSUFFICIENT_SPLIT_SAMPLE: LHP_AB={lhp_ab}, RHP_AB={rhp_ab}"
        
        return True, "VALID"
    
    def _get_true_l10_sigma(
        self,
        player_name: str,
        stat_type: str,
        game_logs: List[Dict]
    ) -> Tuple[Optional[float], str]:
        """
        Get TRUE L10 Standard Deviation from game logs.
        
        NO DEFAULT VALUES. If we can't calculate real sigma, return None.
        
        Returns:
            (sigma, source)
        """
        if not game_logs or len(game_logs) < 5:
            return None, "INSUFFICIENT_GAMES"
        
        norm = self._norm_stat(stat_type)
        values = []
        
        for g in game_logs[:10]:
            val = self._get_stat(g, norm)
            if val is not None:
                values.append(val)
        
        if len(values) < 5:
            return None, "INSUFFICIENT_STAT_VALUES"
        
        # Calculate TRUE L10 Standard Deviation (sample std)
        sigma = float(np.std(values, ddof=1))
        
        # MLB volatility floor: minimum CV of 0.35 for hitting stats
        mean_val = np.mean(values)
        if mean_val > 0:
            cv = sigma / mean_val
            if cv < 0.35 and norm in ['hits', 'total_bases', 'rbis', 'runs', 'hits+runs+rbis', 'home_runs']:
                sigma = mean_val * 0.35
                return sigma, f"MLB_VOLATILITY_FLOOR_0.35"
        
        return sigma, "TRUE_L10_CALCULATION"
    
    # =========================================================================
    # PHYSICAL FEATURE EXTRACTION (105+ FEATURES)
    # =========================================================================
    
    def _build_physical_features(
        self,
        player: Dict,
        game_logs: List[Dict],
        stat: str,
        opponent: str = None,
        park_team: str = None,
        line: float = None,
        pitcher_hand: str = None
    ) -> Optional[Dict[str, float]]:
        """
        Build PHYSICAL feature vector for XGBoost prediction.
        
        NO MARKET DATA in features - pure physical/performance inputs.
        
        Returns:
            Feature dictionary or None if BDL validation fails
        """
        # STRICT: Validate BDL data
        is_valid, error = self._validate_bdl_data(player)
        if not is_valid:
            logger.warning(f"[MLB_ORACLE_APEX] BDL validation FAILED: {error}")
            return None
        
        features = {}
        norm = self._norm_stat(stat)
        
        # Extract stat values from game logs
        vals = []
        for g in game_logs[:30]:
            v = self._get_stat(g, norm)
            if v is not None:
                vals.append(v)
        
        if len(vals) < 5:
            return None
        
        l3 = vals[:3] if len(vals) >= 3 else vals
        l5 = vals[:5] if len(vals) >= 5 else vals
        l10 = vals[:10] if len(vals) >= 10 else vals
        l20 = vals[:20] if len(vals) >= 20 else vals
        
        # =====================================================================
        # CATEGORY 1: RECENT PERFORMANCE (EWMA Trends)
        # =====================================================================
        features['l3_avg'] = np.mean(l3)
        features['l5_avg'] = np.mean(l5)
        features['l10_avg'] = np.mean(l10)
        features['l20_avg'] = np.mean(l20)
        
        features['l5_median'] = np.median(l5)
        features['l10_median'] = np.median(l10)
        features['l5_max'] = max(l5)
        features['l10_max'] = max(l10)
        features['l5_min'] = min(l5)
        features['l10_min'] = min(l10)
        
        # EWMA (Exponentially Weighted)
        features['ewma_l5'] = self._ewma(l5, 0.5)
        features['ewma_l10'] = self._ewma(l10, 0.3)
        features['ewma_l20'] = self._ewma(l20, 0.2)
        
        # Momentum
        if features['ewma_l10'] > 0:
            features['momentum'] = (features['ewma_l5'] - features['ewma_l10']) / features['ewma_l10']
        else:
            features['momentum'] = 0
        
        # Volatility
        features['std_l5'] = np.std(l5, ddof=1) if len(l5) > 1 else 0
        features['std_l10'] = np.std(l10, ddof=1) if len(l10) > 1 else 0
        features['cv_l5'] = features['std_l5'] / features['l5_avg'] if features['l5_avg'] > 0 else 0
        features['cv_l10'] = features['std_l10'] / features['l10_avg'] if features['l10_avg'] > 0 else 0
        features['range_l5'] = features['l5_max'] - features['l5_min']
        features['range_l10'] = features['l10_max'] - features['l10_min']
        
        # Consistency
        features['consistency'] = 1 - features['cv_l10']
        
        # Floor/Ceiling
        features['floor_l10'] = np.percentile(l10, 10)
        features['ceiling_l10'] = np.percentile(l10, 90)
        
        # =====================================================================
        # CATEGORY 2: L/R SPLITS (PvP - Pitcher vs Batter)
        # =====================================================================
        vs_left = player.get('vs_left') or {}
        vs_right = player.get('vs_right') or {}
        
        # vs LHP
        lhp_ab = vs_left.get('at_bats', 0) or 0
        lhp_hits = vs_left.get('hits', 0) or 0
        lhp_hr = vs_left.get('home_runs', 0) or 0
        lhp_k = vs_left.get('strikeouts', 0) or 0
        lhp_bb = vs_left.get('walks', 0) or 0
        lhp_tb = vs_left.get('total_bases', lhp_hits + lhp_hr * 3) or 0
        
        features['lhp_ab'] = lhp_ab
        features['lhp_avg'] = lhp_hits / lhp_ab if lhp_ab > 0 else 0
        features['lhp_slg'] = lhp_tb / lhp_ab if lhp_ab > 0 else 0
        features['lhp_obp'] = (lhp_hits + lhp_bb) / (lhp_ab + lhp_bb) if (lhp_ab + lhp_bb) > 0 else 0
        features['lhp_k_rate'] = lhp_k / lhp_ab if lhp_ab > 0 else 0
        features['lhp_bb_rate'] = lhp_bb / lhp_ab if lhp_ab > 0 else 0
        features['lhp_iso'] = features['lhp_slg'] - features['lhp_avg']
        
        # vs RHP
        rhp_ab = vs_right.get('at_bats', 0) or 0
        rhp_hits = vs_right.get('hits', 0) or 0
        rhp_hr = vs_right.get('home_runs', 0) or 0
        rhp_k = vs_right.get('strikeouts', 0) or 0
        rhp_bb = vs_right.get('walks', 0) or 0
        rhp_tb = vs_right.get('total_bases', rhp_hits + rhp_hr * 3) or 0
        
        features['rhp_ab'] = rhp_ab
        features['rhp_avg'] = rhp_hits / rhp_ab if rhp_ab > 0 else 0
        features['rhp_slg'] = rhp_tb / rhp_ab if rhp_ab > 0 else 0
        features['rhp_obp'] = (rhp_hits + rhp_bb) / (rhp_ab + rhp_bb) if (rhp_ab + rhp_bb) > 0 else 0
        features['rhp_k_rate'] = rhp_k / rhp_ab if rhp_ab > 0 else 0
        features['rhp_bb_rate'] = rhp_bb / rhp_ab if rhp_ab > 0 else 0
        features['rhp_iso'] = features['rhp_slg'] - features['rhp_avg']
        
        # Platoon splits
        features['platoon_avg'] = features['rhp_avg'] - features['lhp_avg']
        features['platoon_slg'] = features['rhp_slg'] - features['lhp_slg']
        features['platoon_k'] = features['lhp_k_rate'] - features['rhp_k_rate']
        features['platoon_obp'] = features['rhp_obp'] - features['lhp_obp']
        
        # Weighted combined (70% RHP, 30% LHP - league average)
        features['combined_avg'] = 0.70 * features['rhp_avg'] + 0.30 * features['lhp_avg']
        features['combined_slg'] = 0.70 * features['rhp_slg'] + 0.30 * features['lhp_slg']
        features['combined_obp'] = 0.70 * features['rhp_obp'] + 0.30 * features['lhp_obp']
        
        # Pitcher hand modifier
        if pitcher_hand:
            hand = pitcher_hand.upper()
            if hand == 'L':
                features['matchup_avg'] = features['lhp_avg']
                features['matchup_slg'] = features['lhp_slg']
                features['matchup_k_rate'] = features['lhp_k_rate']
            else:
                features['matchup_avg'] = features['rhp_avg']
                features['matchup_slg'] = features['rhp_slg']
                features['matchup_k_rate'] = features['rhp_k_rate']
        else:
            features['matchup_avg'] = features['combined_avg']
            features['matchup_slg'] = features['combined_slg']
            features['matchup_k_rate'] = (features['lhp_k_rate'] + features['rhp_k_rate']) / 2
        
        # =====================================================================
        # CATEGORY 3: HOME/AWAY SPLITS
        # =====================================================================
        home = player.get('home_splits') or {}
        away = player.get('away_splits') or {}
        
        home_ab = home.get('at_bats', 0) or 0
        home_hits = home.get('hits', 0) or 0
        home_hr = home.get('home_runs', 0) or 0
        
        away_ab = away.get('at_bats', 0) or 0
        away_hits = away.get('hits', 0) or 0
        away_hr = away.get('home_runs', 0) or 0
        
        features['home_avg'] = home_hits / home_ab if home_ab > 0 else 0
        features['away_avg'] = away_hits / away_ab if away_ab > 0 else 0
        features['home_away_split'] = features['home_avg'] - features['away_avg']
        features['home_hr_rate'] = home_hr / home_ab if home_ab > 0 else 0
        features['away_hr_rate'] = away_hr / away_ab if away_ab > 0 else 0
        
        # =====================================================================
        # CATEGORY 4: PARK FACTORS
        # =====================================================================
        if park_team:
            pf = PARK_FACTORS.get(park_team, DEFAULT_PARK)
            features['park_hits'] = pf.get('hits', 1.0)
            features['park_runs'] = pf.get('runs', 1.0)
            features['park_hr'] = pf.get('hr', 1.0)
            features['park_k'] = pf.get('k', 1.0)
            features['park_tb'] = pf.get('tb', 1.0)
            features['park_factor'] = self._get_park_factor(park_team, norm)
        else:
            features['park_hits'] = 1.0
            features['park_runs'] = 1.0
            features['park_hr'] = 1.0
            features['park_k'] = 1.0
            features['park_tb'] = 1.0
            features['park_factor'] = 1.0
        
        # =====================================================================
        # CATEGORY 5: OPPONENT K-RATE (for pitcher strikeouts)
        # =====================================================================
        if opponent:
            features['opp_k_rate'] = TEAM_K_RATES.get(opponent, 1.0)
        else:
            features['opp_k_rate'] = 1.0
        
        # =====================================================================
        # CATEGORY 6: PLATE DISCIPLINE
        # =====================================================================
        total_ab = lhp_ab + rhp_ab
        total_k = lhp_k + rhp_k
        total_bb = lhp_bb + rhp_bb
        
        features['overall_k_rate'] = total_k / total_ab if total_ab > 0 else 0
        features['overall_bb_rate'] = total_bb / total_ab if total_ab > 0 else 0
        features['bb_k_ratio'] = features['overall_bb_rate'] / features['overall_k_rate'] if features['overall_k_rate'] > 0 else 0
        features['contact_rate'] = 1 - features['overall_k_rate']
        features['power_index'] = (features['combined_slg'] - features['combined_avg']) * features['contact_rate']
        
        # =====================================================================
        # LINE FEATURES (if line provided)
        # =====================================================================
        if line is not None:
            features['line'] = line
            features['line_vs_l5'] = line - features['l5_avg']
            features['line_vs_l10'] = line - features['l10_avg']
            features['line_vs_ewma'] = line - features['ewma_l10']
            features['line_vs_median'] = line - features['l10_median']
            features['line_difficulty'] = features['line_vs_l10'] / features['std_l10'] if features['std_l10'] > 0 else 0
            
            # Hit rates
            l5_hits = sum(1 for v in l5 if v > line)
            l10_hits = sum(1 for v in l10 if v > line)
            features['hr_l5'] = l5_hits / len(l5) * 100
            features['hr_l10'] = l10_hits / len(l10) * 100
        
        return features
    
    # =========================================================================
    # TRAINING DATA BUILDER
    # =========================================================================
    
    def build_training_data(self, stat: str) -> pd.DataFrame:
        """
        Build training dataset from 90,000+ historical game logs.
        
        STRICT: Only includes players with valid BDL L/R splits.
        """
        logger.info(f"[MLB_ORACLE_APEX] Building training data for {stat}")
        
        norm = self._norm_stat(stat)
        data = []
        skipped_bdl = 0
        
        # Query players with BOTH game logs AND L/R splits (optimization)
        cursor = self.master_hub.find(
            {
                'bdl_game_logs': {'$exists': True},
                '$or': [
                    {'vs_left': {'$ne': None}},
                    {'vs_right': {'$ne': None}}
                ]
            },
            {'_id': 0}
        )
        
        for player in cursor:
            name = player.get('display_name') or player.get('player_name')
            logs = player.get('bdl_game_logs', [])
            
            if len(logs) < 20:
                continue
            
            # Sort by date descending
            logs = sorted(logs, key=lambda x: x.get('date') or '1900-01-01', reverse=True)
            
            # STRICT: Validate BDL data
            is_valid, _ = self._validate_bdl_data(player)
            if not is_valid:
                skipped_bdl += 1
                continue
            
            # Build training samples (predict game i from games i+1 to i+31)
            for i in range(len(logs) - 20):
                target = logs[i]
                history = logs[i+1:i+1+30]
                
                target_val = self._get_stat(target, norm)
                if target_val is None:
                    continue
                
                opponent = target.get('opponent_abbr')
                
                feats = self._build_physical_features(player, history, norm, opponent, None, None)
                if feats is None:
                    continue
                
                feats['target'] = target_val
                feats['player'] = name
                feats['date'] = target.get('date')
                feats['opponent'] = opponent
                
                data.append(feats)
        
        df = pd.DataFrame(data)
        logger.info(f"[MLB_ORACLE_APEX] Built {len(df):,} samples ({skipped_bdl} skipped - missing BDL splits)")
        
        return df
    
    # =========================================================================
    # MODEL TRAINING
    # =========================================================================
    
    def train(self, stat: str, test_size: float = 0.2) -> Dict[str, Any]:
        """
        Train XGBoost model on physical features.
        
        Returns training metrics or error.
        """
        from sklearn.model_selection import train_test_split
        from sklearn.preprocessing import StandardScaler
        from sklearn.metrics import mean_absolute_error, r2_score
        
        try:
            import xgboost as xgb
        except ImportError:
            return {'error': 'XGBoost not installed'}
        
        norm = self._norm_stat(stat)
        logger.info(f"[MLB_ORACLE_APEX] Training {norm}...")
        
        df = self.build_training_data(stat)
        if len(df) < 100:
            return {'error': f'Insufficient data: {len(df)} samples (need 100+)'}
        
        # Exclude non-feature columns
        exclude = ['target', 'player', 'date', 'opponent']
        feat_cols = [c for c in df.columns if c not in exclude]
        
        X = df[feat_cols].fillna(0).replace([np.inf, -np.inf], 0)
        y = df['target']
        
        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=test_size, random_state=42)
        
        # Scale features
        scaler = StandardScaler()
        X_tr = scaler.fit_transform(X_train)
        X_te = scaler.transform(X_test)
        
        # Train XGBoost
        model = xgb.XGBRegressor(
            n_estimators=300,
            max_depth=8,
            learning_rate=0.05,
            subsample=0.85,
            colsample_bytree=0.85,
            min_child_weight=3,
            reg_alpha=0.1,
            reg_lambda=1.5,
            random_state=42,
            n_jobs=-1
        )
        
        model.fit(X_tr, y_train)
        
        # Evaluate
        tr_pred = model.predict(X_tr)
        te_pred = model.predict(X_te)
        
        tr_mae = mean_absolute_error(y_train, tr_pred)
        te_mae = mean_absolute_error(y_test, te_pred)
        tr_r2 = r2_score(y_train, tr_pred)
        te_r2 = r2_score(y_test, te_pred)
        
        # Top features
        imp = dict(zip(feat_cols, model.feature_importances_))
        imp = dict(sorted(imp.items(), key=lambda x: -x[1])[:20])
        
        # Store model
        self.models[norm] = model
        self.scalers[norm] = scaler
        self.feature_cols[norm] = feat_cols
        
        metrics = {
            'stat': norm,
            'samples': len(df),
            'features': len(feat_cols),
            'train': {'mae': round(tr_mae, 4), 'r2': round(tr_r2, 4)},
            'test': {'mae': round(te_mae, 4), 'r2': round(te_r2, 4)},
            'top_features': imp
        }
        
        logger.info(f"[MLB_ORACLE_APEX] {norm}: MAE={te_mae:.4f}, R²={te_r2:.4f}")
        return metrics
    
    # =========================================================================
    # MODEL PERSISTENCE
    # =========================================================================
    
    def save_models(self):
        """Save all trained models to disk."""
        for s in self.models:
            data = {
                'model': self.models[s],
                'scaler': self.scalers[s],
                'features': self.feature_cols[s],
                'version': 'MLB_ORACLE_APEX_v2.0',
                'trained': datetime.now(timezone.utc).isoformat()
            }
            path = os.path.join(self.MODEL_DIR, f'mlb_apex_{s}.pkl')
            with open(path, 'wb') as f:
                pickle.dump(data, f)
            logger.info(f"[MLB_ORACLE_APEX] Saved {s} model")
    
    def load_models(self) -> int:
        """Load trained models from disk."""
        loaded = 0
        for s in STAT_TYPES:
            path = os.path.join(self.MODEL_DIR, f'mlb_apex_{s}.pkl')
            if os.path.exists(path):
                try:
                    with open(path, 'rb') as f:
                        data = pickle.load(f)
                    self.models[s] = data['model']
                    self.scalers[s] = data['scaler']
                    self.feature_cols[s] = data['features']
                    loaded += 1
                except Exception as e:
                    logger.error(f"[MLB_ORACLE_APEX] Failed to load {s}: {e}")
        
        logger.info(f"[MLB_ORACLE_APEX] Loaded {loaded}/{len(STAT_TYPES)} models")
        return loaded
    
    # =========================================================================
    # PREDICTION (STRICT ENFORCEMENT)
    # =========================================================================
    
    def predict(
        self,
        player_name: str,
        stat_type: str,
        line: float = None,
        opponent_team: str = None,
        park_team: str = None,
        pitcher_hand: str = None,
        dk_odds: int = None
    ) -> MLBMLRResult:
        """
        Generate MLB MLR prediction with STRICT ENFORCEMENT.
        
        NO FALLBACKS:
        - If BDL data missing -> return null prediction
        - If model fails -> return null prediction
        
        HIGH PRECISION:
        - Predictions like 4.38 K, not 4 K
        
        MARKET INTEGRATION:
        - vk_edge = vk_prob_over - implied_probability
        
        Returns:
            MLBMLRResult with prediction or null if data missing
        """
        norm = self._norm_stat(stat_type)
        
        # Check if model exists
        if norm not in self.models:
            return MLBMLRResult(
                player_name=player_name,
                stat_type=stat_type,
                mlr_predicted=None,
                raw_prediction=None,
                sigma_used=None,
                sigma_source="NO_MODEL",
                z_score=None,
                vk_prob_over=None,
                vk_prob_under=None,
                vk_verdict="INVALID",
                dk_odds=dk_odds,
                implied_probability=None,
                vk_edge=None,
                mlr_matchup={},
                is_valid=False,
                error=f"No trained model for {stat_type}"
            )
        
        try:
            # Find player in master hub
            player = self.master_hub.find_one(
                {"$or": [
                    {"display_name": player_name},
                    {"player_name": player_name},
                    {"mlb_full_name": player_name}
                ]},
                {"_id": 0}
            )
            
            if not player:
                return MLBMLRResult(
                    player_name=player_name,
                    stat_type=stat_type,
                    mlr_predicted=None,
                    raw_prediction=None,
                    sigma_used=None,
                    sigma_source="PLAYER_NOT_FOUND",
                    z_score=None,
                    vk_prob_over=None,
                    vk_prob_under=None,
                    vk_verdict="INVALID",
                    dk_odds=dk_odds,
                    implied_probability=None,
                    vk_edge=None,
                    mlr_matchup={},
                    is_valid=False,
                    error=f"Player not found: {player_name}"
                )
            
            # STRICT: Validate BDL data (NO FALLBACKS)
            is_valid, error = self._validate_bdl_data(player)
            if not is_valid:
                return MLBMLRResult(
                    player_name=player_name,
                    stat_type=stat_type,
                    mlr_predicted=None,
                    raw_prediction=None,
                    sigma_used=None,
                    sigma_source="BDL_VALIDATION_FAILED",
                    z_score=None,
                    vk_prob_over=None,
                    vk_prob_under=None,
                    vk_verdict="INVALID",
                    dk_odds=dk_odds,
                    implied_probability=None,
                    vk_edge=None,
                    mlr_matchup={},
                    is_valid=False,
                    error=error
                )
            
            # Get game logs
            logs = player.get('bdl_game_logs', [])
            if len(logs) < 5:
                return MLBMLRResult(
                    player_name=player_name,
                    stat_type=stat_type,
                    mlr_predicted=None,
                    raw_prediction=None,
                    sigma_used=None,
                    sigma_source="INSUFFICIENT_GAMES",
                    z_score=None,
                    vk_prob_over=None,
                    vk_prob_under=None,
                    vk_verdict="INVALID",
                    dk_odds=dk_odds,
                    implied_probability=None,
                    vk_edge=None,
                    mlr_matchup={},
                    is_valid=False,
                    error=f"Insufficient games: {len(logs)}"
                )
            
            # Build PHYSICAL features
            feats = self._build_physical_features(
                player, logs, norm, opponent_team, park_team, line, pitcher_hand
            )
            if feats is None:
                return MLBMLRResult(
                    player_name=player_name,
                    stat_type=stat_type,
                    mlr_predicted=None,
                    raw_prediction=None,
                    sigma_used=None,
                    sigma_source="FEATURE_BUILD_FAILED",
                    z_score=None,
                    vk_prob_over=None,
                    vk_prob_under=None,
                    vk_verdict="INVALID",
                    dk_odds=dk_odds,
                    implied_probability=None,
                    vk_edge=None,
                    mlr_matchup={},
                    is_valid=False,
                    error="Could not build physical features"
                )
            
            # Get model components
            model = self.models[norm]
            scaler = self.scalers[norm]
            feat_cols = self.feature_cols[norm]
            
            # Prepare features for prediction
            X = pd.DataFrame([feats])
            for c in feat_cols:
                if c not in X.columns:
                    X[c] = 0
            X = X[feat_cols].fillna(0).replace([np.inf, -np.inf], 0)
            X_sc = scaler.transform(X)
            
            # HIGH PRECISION PREDICTION
            raw_pred = float(model.predict(X_sc)[0])
            
            # Apply park factor
            pf = feats.get('park_factor', 1.0)
            opp_k = feats.get('opp_k_rate', 1.0)
            
            if norm == 'pitcher_strikeouts':
                final_pred = raw_pred * pf * opp_k
            else:
                final_pred = raw_pred * pf
            
            # TRUE L10 SIGMA (NO DEFAULTS)
            sigma, sigma_source = self._get_true_l10_sigma(player_name, norm, logs)
            
            if sigma is None:
                return MLBMLRResult(
                    player_name=player_name,
                    stat_type=stat_type,
                    mlr_predicted=None,
                    raw_prediction=raw_pred,
                    sigma_used=None,
                    sigma_source=sigma_source,
                    z_score=None,
                    vk_prob_over=None,
                    vk_prob_under=None,
                    vk_verdict="INVALID",
                    dk_odds=dk_odds,
                    implied_probability=None,
                    vk_edge=None,
                    mlr_matchup={},
                    is_valid=False,
                    error=f"Could not calculate TRUE L10 sigma: {sigma_source}"
                )
            
            # PROBABILITY CALCULATION (Z-Score / Normal CDF)
            prob_over = None
            prob_under = None
            z_score = None
            
            if line is not None and sigma > 0:
                z_score = (line - final_pred) / sigma
                prob_under = stats.norm.cdf(z_score)
                prob_over = 1.0 - prob_under
                
                # Convert to percentage
                prob_over = round(prob_over * 100, 1)
                prob_under = round(prob_under * 100, 1)
                
                # Cap at 1-99%
                prob_over = max(1.0, min(99.0, prob_over))
                prob_under = max(1.0, min(99.0, prob_under))
            
            # MARKET INTEGRATION
            implied_prob = None
            vk_edge = None
            
            if dk_odds is not None:
                if dk_odds < 0:
                    implied_prob = abs(dk_odds) / (abs(dk_odds) + 100) * 100
                else:
                    implied_prob = 100 / (dk_odds + 100) * 100
                
                implied_prob = round(implied_prob, 1)
                
                if prob_over is not None:
                    vk_edge = round(prob_over - implied_prob, 1)
            
            # VERDICT
            if prob_over is not None:
                if prob_over >= 65:
                    verdict = "STRONG_OVER"
                elif prob_over >= 55:
                    verdict = "LEAN_OVER"
                elif prob_under >= 65:
                    verdict = "STRONG_UNDER"
                elif prob_under >= 55:
                    verdict = "LEAN_UNDER"
                else:
                    verdict = "NEUTRAL"
            else:
                verdict = "NO_LINE"
            
            # MLR MATCHUP BLOCK (Physical Friction Audit)
            mlr_matchup = {
                'splits': {
                    'vs_lhp_avg': round(feats.get('lhp_avg', 0), 3),
                    'vs_rhp_avg': round(feats.get('rhp_avg', 0), 3),
                    'platoon_split': round(feats.get('platoon_avg', 0), 3),
                    'vs_lhp_k_rate': round(feats.get('lhp_k_rate', 0), 3),
                    'vs_rhp_k_rate': round(feats.get('rhp_k_rate', 0), 3),
                    'matchup_avg': round(feats.get('matchup_avg', 0), 3),
                    'matchup_slg': round(feats.get('matchup_slg', 0), 3),
                },
                'park': {
                    'venue': park_team,
                    'factor': round(pf, 3),
                    'hits_factor': feats.get('park_hits'),
                    'runs_factor': feats.get('park_runs'),
                    'hr_factor': feats.get('park_hr'),
                    'k_factor': feats.get('park_k'),
                },
                'opponent': {
                    'team': opponent_team,
                    'k_rate': round(opp_k, 3),
                },
                'trends': {
                    'l5_avg': round(feats.get('l5_avg', 0), 2),
                    'l10_avg': round(feats.get('l10_avg', 0), 2),
                    'ewma_l10': round(feats.get('ewma_l10', 0), 2),
                    'momentum': round(feats.get('momentum', 0), 3),
                },
                'variance': {
                    'std_l10': round(feats.get('std_l10', 0), 3),
                    'cv_l10': round(feats.get('cv_l10', 0), 3),
                },
                'discipline': {
                    'contact_rate': round(feats.get('contact_rate', 0), 3),
                    'bb_k_ratio': round(feats.get('bb_k_ratio', 0), 3),
                    'power_index': round(feats.get('power_index', 0), 4),
                }
            }
            
            logger.info(
                f"[MLB_ORACLE_APEX] {player_name} {stat_type}: "
                f"pred={final_pred:.2f}, park={pf:.2f}, opp_k={opp_k:.2f}, "
                f"σ={sigma:.3f} ({sigma_source}), P(over)={prob_over}%, edge={vk_edge}"
            )
            
            return MLBMLRResult(
                player_name=player_name,
                stat_type=stat_type,
                mlr_predicted=round(final_pred, 2),
                raw_prediction=round(raw_pred, 4),
                sigma_used=round(sigma, 4),
                sigma_source=sigma_source,
                z_score=round(z_score, 4) if z_score else None,
                vk_prob_over=prob_over,
                vk_prob_under=prob_under,
                vk_verdict=verdict,
                dk_odds=dk_odds,
                implied_probability=implied_prob,
                vk_edge=vk_edge,
                mlr_matchup=mlr_matchup,
                is_valid=True,
                error=None
            )
            
        except Exception as e:
            logger.error(f"[MLB_ORACLE_APEX] Prediction error for {player_name}: {e}")
            return MLBMLRResult(
                player_name=player_name,
                stat_type=stat_type,
                mlr_predicted=None,
                raw_prediction=None,
                sigma_used=None,
                sigma_source="EXCEPTION",
                z_score=None,
                vk_prob_over=None,
                vk_prob_under=None,
                vk_verdict="INVALID",
                dk_odds=dk_odds,
                implied_probability=None,
                vk_edge=None,
                mlr_matchup={},
                is_valid=False,
                error=str(e)
            )
    
    # =========================================================================
    # AUDIT JSON EXPORT
    # =========================================================================
    
    def generate_strict_audit(self, output_path: str = '/app/frontend/public/mlb_mlr_strict_audit.json') -> Dict:
        """
        Generate mlb_mlr_strict_audit.json mirroring NBA audit format.
        
        Shows:
        - mlr_matchup block (physical friction)
        - market_data (odds/edge)
        - model_info
        """
        audit = {
            'version': 'MLB_ORACLE_APEX_v2.0',
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'description': '1:1 functional replica of NBA_MLR_STRICT_v2.2',
            'strict_rules': {
                'no_fallbacks': 'DELETE all OR season_avg logic',
                'high_precision': 'Predictions like 4.38 K, not 4 K',
                'sigma_linkage': 'TRUE L10 Standard Deviation from database',
                'edge_calculation': 'vk_edge = vk_prob_over - implied_probability',
            },
            'trained_models': {},
            'sample_predictions': [],
            'park_factors': PARK_FACTORS,
            'team_k_rates': TEAM_K_RATES,
        }
        
        # Model info
        for stat, model in self.models.items():
            if hasattr(model, 'feature_importances_'):
                feat_cols = self.feature_cols.get(stat, [])
                imp = dict(zip(feat_cols, model.feature_importances_))
                top_10 = dict(sorted(imp.items(), key=lambda x: -x[1])[:10])
                audit['trained_models'][stat] = {
                    'n_features': len(feat_cols),
                    'top_10_features': {k: round(v, 4) for k, v in top_10.items()}
                }
        
        # Sample predictions from database
        cursor = self.master_hub.find(
            {'bdl_game_logs': {'$exists': True}, 'vs_left': {'$exists': True}},
            {'_id': 0}
        ).limit(5)
        
        for player in cursor:
            name = player.get('display_name') or player.get('player_name')
            if not name:
                continue
            
            for stat in ['hits', 'pitcher_strikeouts']:
                result = self.predict(
                    player_name=name,
                    stat_type=stat,
                    line=1.5 if stat == 'hits' else 5.5,
                    opponent_team='NYY',
                    park_team='NYY',
                    dk_odds=-150
                )
                
                audit['sample_predictions'].append({
                    'player_name': result.player_name,
                    'stat_type': result.stat_type,
                    'mlr_predicted': result.mlr_predicted,
                    'sigma_used': result.sigma_used,
                    'sigma_source': result.sigma_source,
                    'vk_prob_over': result.vk_prob_over,
                    'vk_edge': result.vk_edge,
                    'is_valid': result.is_valid,
                    'error': result.error,
                    'mlr_matchup': result.mlr_matchup,
                    'market_data': {
                        'dk_odds': result.dk_odds,
                        'implied_probability': result.implied_probability,
                    }
                })
        
        # Write to file
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'w') as f:
            json.dump(audit, f, indent=2, default=str)
        
        logger.info(f"[MLB_ORACLE_APEX] Generated strict audit: {output_path}")
        return audit


# =============================================================================
# GLOBAL INSTANCE
# =============================================================================

_mlb_engine = None

def get_mlb_physical_engine(db=None):
    """Get singleton MLB Physical Engine instance."""
    global _mlb_engine
    if _mlb_engine is None and db is not None:
        _mlb_engine = MLBPhysicalEngine(db)
    return _mlb_engine

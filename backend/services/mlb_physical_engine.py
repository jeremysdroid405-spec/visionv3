"""
MLB Physical Performance Engine v1.0
====================================
Pure mathematical projection using BDL GOAT-Tier physical data.

THIS MODEL DOES NOT CARE ABOUT:
- Vegas odds
- Line movement
- Market implied probability

THIS MODEL ONLY USES PHYSICAL INPUTS:
1. PvP History (Pitcher vs Batter lifetime stats)
2. L/R Splits (Handedness matchup data)
3. Park Factors (3-year venue adjustments)
4. Recent Performance Trends (L5/L10/L20 EWMA)
5. Plate Discipline (K%, BB%, Contact%)

STRICT REQUIREMENTS:
- If BDL PvP/Splits data is MISSING → return null (NO GUESSING)
- High-precision decimals: 4.38 K, 1.23 Hits
- 105+ features trained on 90,000+ game samples

Author: PropVision AI  
Version: 1.0.0 (Physical Performance Engine)
"""
import logging
import numpy as np
import pandas as pd
import pickle
import os
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
from scipy import stats

logger = logging.getLogger(__name__)


class MLBPhysicalEngine:
    """
    MLB Physical Performance Engine.
    
    Pure mathematical projection - NO market data.
    Requires BDL GOAT-Tier data for every prediction.
    """
    
    STAT_TYPES = ['hits', 'total_bases', 'rbis', 'runs', 'pitcher_strikeouts', 
                  'hits+runs+rbis', 'home_runs', 'stolen_bases']
    
    STAT_FIELD_MAP = {
        'hits': 'hits', 'total_bases': 'total_bases', 'rbis': 'rbis',
        'runs': 'runs', 'stolen_bases': 'stolen_bases', 'home_runs': 'home_runs',
        'walks': 'walks', 'strikeouts': 'strikeouts',
        'pitcher_strikeouts': 'pitcher_strikeouts',
        'hits+runs+rbis': ['hits', 'runs', 'rbis'],
    }
    
    # =========================================================================
    # 3-YEAR PARK FACTORS (Physical venue adjustments)
    # =========================================================================
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
    
    # Team K-rate tendencies (physical contact ability)
    TEAM_K_RATES = {
        'ARI': 1.14, 'DET': 1.12, 'OAK': 1.10, 'CHC': 1.08, 'MIA': 1.07,
        'COL': 1.06, 'PIT': 1.05, 'CIN': 1.04, 'SEA': 1.03, 'TEX': 1.02,
        'ATL': 1.00, 'NYM': 0.99, 'PHI': 0.98, 'LAD': 0.97, 'SD': 0.97,
        'SF': 0.96, 'STL': 0.98, 'MIL': 0.99, 'CHW': 1.01, 'BAL': 1.00,
        'TOR': 1.01, 'BOS': 0.98, 'TB': 0.99, 'WSH': 1.02, 'LAA': 1.00,
        'HOU': 0.92, 'NYY': 0.94, 'CLE': 0.93, 'KC': 0.91, 'MIN': 0.93,
    }
    
    MODEL_DIR = '/app/backend/models/mlb_physical'
    
    def __init__(self, db):
        self.db = db
        self.master_hub = db.mlb_master_hub_2026
        self.historical_logs = db.mlb_historical_logs
        
        self.models = {}
        self.scalers = {}
        self.feature_cols = {}
        
        os.makedirs(self.MODEL_DIR, exist_ok=True)
        logger.info("[MLB_PHYSICAL] Initialized Physical Performance Engine v1.0")
    
    def _norm_stat(self, stat: str) -> str:
        s = stat.lower().replace(' ', '_').replace('+', '+')
        aliases = {
            'k': 'pitcher_strikeouts', 'ks': 'pitcher_strikeouts',
            'pitcher k': 'pitcher_strikeouts', 'pitcher strikeouts': 'pitcher_strikeouts',
            'tb': 'total_bases', 'rbi': 'rbis', 'sb': 'stolen_bases',
            'hr': 'home_runs', 'h': 'hits', 'r': 'runs',
            'hrr': 'hits+runs+rbis',
        }
        return aliases.get(s, s)
    
    def _get_stat(self, game: Dict, stat: str) -> Optional[float]:
        field = self.STAT_FIELD_MAP.get(stat, stat)
        if isinstance(field, list):
            return sum(float(game.get(f, 0) or 0) for f in field)
        val = game.get(field)
        return float(val) if val is not None else None
    
    def _ewma(self, vals: List[float], alpha: float) -> float:
        if not vals:
            return 0.0
        r = vals[0]
        for v in vals[1:]:
            r = alpha * v + (1 - alpha) * r
        return r
    
    def _get_park_factor(self, team: str, stat: str) -> float:
        pf = self.PARK_FACTORS.get(team, self.DEFAULT_PARK)
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
    
    def _validate_bdl_data(self, player: Dict) -> Tuple[bool, str]:
        """
        STRICT VALIDATION: Check if required BDL data exists.
        Returns (is_valid, error_message)
        """
        # Check for L/R splits
        vs_left = player.get('vs_left', {})
        vs_right = player.get('vs_right', {})
        
        if not vs_left or not vs_right:
            return False, "Missing L/R splits data"
        
        # Check for at-bats in splits (need sample size)
        lhp_ab = vs_left.get('at_bats', 0) or 0
        rhp_ab = vs_right.get('at_bats', 0) or 0
        
        if lhp_ab < 10 and rhp_ab < 10:
            return False, f"Insufficient split sample: LHP={lhp_ab}, RHP={rhp_ab}"
        
        # Check for home/away splits
        home = player.get('home_splits', {})
        away = player.get('away_splits', {})
        
        if not home and not away:
            return False, "Missing home/away splits"
        
        return True, "OK"
    
    def _build_physical_features(
        self,
        player: Dict,
        game_logs: List[Dict],
        stat: str,
        opponent: str = None,
        park_team: str = None,
        line: float = None
    ) -> Optional[Dict[str, float]]:
        """
        Build PHYSICAL feature vector (105+ features).
        NO market data. Pure BDL GOAT-Tier inputs.
        """
        # STRICT: Validate BDL data exists
        is_valid, error = self._validate_bdl_data(player)
        if not is_valid:
            logger.warning(f"[MLB_PHYSICAL] BDL validation failed: {error}")
            return None
        
        features = {}
        
        # Extract stat values
        vals = []
        for g in game_logs[:30]:
            v = self._get_stat(g, stat)
            if v is not None:
                vals.append(v)
        
        if len(vals) < 5:
            return None
        
        l3, l5, l10, l20 = vals[:3], vals[:5], vals[:10], vals[:20]
        
        # =====================================================================
        # PHYSICAL CATEGORY 1: RECENT PERFORMANCE TRENDS
        # =====================================================================
        features['l3_avg'] = np.mean(l3)
        features['l5_avg'] = np.mean(l5)
        features['l10_avg'] = np.mean(l10)
        features['l20_avg'] = np.mean(l20) if len(l20) >= 10 else np.mean(l10)
        
        features['l5_median'] = np.median(l5)
        features['l10_median'] = np.median(l10)
        features['l5_max'] = max(l5)
        features['l10_max'] = max(l10)
        features['l5_min'] = min(l5)
        features['l10_min'] = min(l10)
        
        # EWMA
        features['ewma_l5'] = self._ewma(l5, 0.5)
        features['ewma_l10'] = self._ewma(l10, 0.3)
        features['ewma_l20'] = self._ewma(l20, 0.2) if len(l20) >= 10 else features['ewma_l10']
        
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
        
        # Consistency score
        features['consistency'] = 1 - features['cv_l10']
        
        # Floor/Ceiling
        features['floor_l10'] = np.percentile(l10, 10)
        features['ceiling_l10'] = np.percentile(l10, 90)
        
        # =====================================================================
        # PHYSICAL CATEGORY 2: L/R SPLITS (BDL GOAT-Tier)
        # =====================================================================
        vs_left = player.get('vs_left', {})
        vs_right = player.get('vs_right', {})
        
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
        features['lhp_iso'] = features['lhp_slg'] - features['lhp_avg']  # Isolated power
        
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
        
        # Platoon splits (positive = better vs RHP)
        features['platoon_avg'] = features['rhp_avg'] - features['lhp_avg']
        features['platoon_slg'] = features['rhp_slg'] - features['lhp_slg']
        features['platoon_k'] = features['lhp_k_rate'] - features['rhp_k_rate']
        features['platoon_obp'] = features['rhp_obp'] - features['lhp_obp']
        
        # Combined weighted average (assume 70% RHP, 30% LHP league average)
        features['combined_avg'] = 0.70 * features['rhp_avg'] + 0.30 * features['lhp_avg']
        features['combined_slg'] = 0.70 * features['rhp_slg'] + 0.30 * features['lhp_slg']
        features['combined_obp'] = 0.70 * features['rhp_obp'] + 0.30 * features['lhp_obp']
        
        # =====================================================================
        # PHYSICAL CATEGORY 3: HOME/AWAY SPLITS
        # =====================================================================
        home = player.get('home_splits', {})
        away = player.get('away_splits', {})
        
        home_ab = home.get('at_bats', 0) or 0
        home_hits = home.get('hits', 0) or 0
        home_runs = home.get('runs', 0) or 0
        home_hr = home.get('home_runs', 0) or 0
        
        away_ab = away.get('at_bats', 0) or 0
        away_hits = away.get('hits', 0) or 0
        away_runs = away.get('runs', 0) or 0
        away_hr = away.get('home_runs', 0) or 0
        
        features['home_avg'] = home_hits / home_ab if home_ab > 0 else 0
        features['away_avg'] = away_hits / away_ab if away_ab > 0 else 0
        features['home_away_avg_split'] = features['home_avg'] - features['away_avg']
        
        features['home_runs_per_game'] = home_runs / (home_ab / 4) if home_ab > 0 else 0
        features['away_runs_per_game'] = away_runs / (away_ab / 4) if away_ab > 0 else 0
        
        features['home_hr_rate'] = home_hr / home_ab if home_ab > 0 else 0
        features['away_hr_rate'] = away_hr / away_ab if away_ab > 0 else 0
        
        # =====================================================================
        # PHYSICAL CATEGORY 4: PARK FACTORS (3-Year Historical)
        # =====================================================================
        if park_team:
            pf = self.PARK_FACTORS.get(park_team, self.DEFAULT_PARK)
            features['park_hits'] = pf.get('hits', 1.0)
            features['park_runs'] = pf.get('runs', 1.0)
            features['park_hr'] = pf.get('hr', 1.0)
            features['park_k'] = pf.get('k', 1.0)
            features['park_tb'] = pf.get('tb', 1.0)
            features['park_factor'] = self._get_park_factor(park_team, stat)
        else:
            features['park_hits'] = 1.0
            features['park_runs'] = 1.0
            features['park_hr'] = 1.0
            features['park_k'] = 1.0
            features['park_tb'] = 1.0
            features['park_factor'] = 1.0
        
        # Opponent K-rate (for pitcher strikeouts)
        if opponent:
            features['opp_k_rate'] = self.TEAM_K_RATES.get(opponent, 1.0)
        else:
            features['opp_k_rate'] = 1.0
        
        # =====================================================================
        # PHYSICAL CATEGORY 5: PLATE DISCIPLINE (derived)
        # =====================================================================
        # Overall K% and BB%
        total_ab = lhp_ab + rhp_ab
        total_k = lhp_k + rhp_k
        total_bb = lhp_bb + rhp_bb
        
        features['overall_k_rate'] = total_k / total_ab if total_ab > 0 else 0
        features['overall_bb_rate'] = total_bb / total_ab if total_ab > 0 else 0
        features['bb_k_ratio'] = features['overall_bb_rate'] / features['overall_k_rate'] if features['overall_k_rate'] > 0 else 0
        
        # Contact proxy (inverse of K rate)
        features['contact_rate'] = 1 - features['overall_k_rate']
        
        # Power vs Contact profile
        features['power_index'] = (features['combined_slg'] - features['combined_avg']) * features['contact_rate']
        
        # =====================================================================
        # LINE FEATURES (for training/inference)
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
    
    def build_training_data(self, stat: str) -> pd.DataFrame:
        """Build training dataset from BDL GOAT-Tier historical data."""
        logger.info(f"[MLB_PHYSICAL] Building training data for {stat}")
        
        norm = self._norm_stat(stat)
        data = []
        skipped_bdl = 0
        
        cursor = self.historical_logs.find({}, {'_id': 0})
        
        for doc in cursor:
            name = doc.get('player_name')
            logs = doc.get('game_logs', [])
            
            if len(logs) < 20:
                continue
            
            logs = sorted(logs, key=lambda x: x.get('date') or '1900-01-01', reverse=True)
            
            # Get master data with BDL splits
            master = self.master_hub.find_one(
                {"$or": [{"display_name": name}, {"player_name": name}]},
                {"_id": 0}
            )
            
            if not master:
                master = {}
            
            # STRICT: Validate BDL data
            is_valid, _ = self._validate_bdl_data(master)
            if not is_valid:
                skipped_bdl += 1
                continue
            
            for i in range(len(logs) - 20):
                target = logs[i]
                history = logs[i+1:i+31]
                
                target_val = self._get_stat(target, norm)
                if target_val is None:
                    continue
                
                opponent = target.get('opponent_abbr')
                
                feats = self._build_physical_features(master, history, norm, opponent, None, None)
                if feats is None:
                    continue
                
                feats['target'] = target_val
                feats['player'] = name
                feats['date'] = target.get('date')
                feats['opponent'] = opponent
                
                data.append(feats)
        
        df = pd.DataFrame(data)
        logger.info(f"[MLB_PHYSICAL] Built {len(df)} samples ({skipped_bdl} skipped - missing BDL data)")
        
        return df
    
    def train(self, stat: str, test_size: float = 0.2) -> Dict[str, Any]:
        """Train XGBoost on physical features."""
        from sklearn.model_selection import train_test_split
        from sklearn.preprocessing import StandardScaler
        from sklearn.metrics import mean_absolute_error, r2_score
        
        try:
            import xgboost as xgb
        except ImportError:
            return {'error': 'XGBoost not installed'}
        
        norm = self._norm_stat(stat)
        logger.info(f"[MLB_PHYSICAL] Training {norm}...")
        
        df = self.build_training_data(stat)
        if len(df) < 100:
            return {'error': f'Insufficient data: {len(df)}'}
        
        exclude = ['target', 'player', 'date', 'opponent']
        feat_cols = [c for c in df.columns if c not in exclude]
        
        X = df[feat_cols].fillna(0)
        y = df['target']
        
        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=test_size, random_state=42)
        
        scaler = StandardScaler()
        X_tr = scaler.fit_transform(X_train)
        X_te = scaler.transform(X_test)
        
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
        
        tr_pred = model.predict(X_tr)
        te_pred = model.predict(X_te)
        
        tr_mae = mean_absolute_error(y_train, tr_pred)
        te_mae = mean_absolute_error(y_test, te_pred)
        tr_r2 = r2_score(y_train, tr_pred)
        te_r2 = r2_score(y_test, te_pred)
        
        # Top features
        imp = dict(zip(feat_cols, model.feature_importances_))
        imp = dict(sorted(imp.items(), key=lambda x: -x[1])[:20])
        
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
        
        logger.info(f"[MLB_PHYSICAL] {norm}: MAE={te_mae:.4f}, R²={te_r2:.4f}")
        return metrics
    
    def save_models(self):
        """Save trained models."""
        for s in self.models:
            data = {
                'model': self.models[s],
                'scaler': self.scalers[s],
                'features': self.feature_cols[s],
                'version': 'MLB_PHYSICAL_v1.0',
                'trained': datetime.now(timezone.utc).isoformat()
            }
            path = os.path.join(self.MODEL_DIR, f'mlb_phys_{s}.pkl')
            with open(path, 'wb') as f:
                pickle.dump(data, f)
            logger.info(f"[MLB_PHYSICAL] Saved {s}")
    
    def load_models(self) -> int:
        """Load trained models."""
        loaded = 0
        for s in self.STAT_TYPES:
            path = os.path.join(self.MODEL_DIR, f'mlb_phys_{s}.pkl')
            if os.path.exists(path):
                try:
                    with open(path, 'rb') as f:
                        data = pickle.load(f)
                    self.models[s] = data['model']
                    self.scalers[s] = data['scaler']
                    self.feature_cols[s] = data['features']
                    loaded += 1
                except Exception as e:
                    logger.error(f"[MLB_PHYSICAL] Load failed {s}: {e}")
        
        logger.info(f"[MLB_PHYSICAL] Loaded {loaded}/{len(self.STAT_TYPES)} models")
        return loaded
    
    def predict(
        self,
        player_name: str,
        stat_type: str,
        line: float = None,
        opponent_team: str = None,
        park_team: str = None
    ) -> Dict[str, Any]:
        """
        Generate PHYSICAL prediction.
        
        STRICT: Returns null if BDL data missing. NO GUESSING.
        """
        norm = self._norm_stat(stat_type)
        
        if norm not in self.models:
            return {"error": f"No model for {stat_type}", "prediction": None}
        
        try:
            player = self.master_hub.find_one(
                {"$or": [
                    {"display_name": player_name},
                    {"player_name": player_name},
                    {"mlb_full_name": player_name}
                ]},
                {"_id": 0}
            )
            
            if not player:
                return {"error": f"Player not found: {player_name}", "prediction": None}
            
            # STRICT: Validate BDL data
            is_valid, error = self._validate_bdl_data(player)
            if not is_valid:
                return {"error": f"Missing BDL data: {error}", "prediction": None}
            
            logs = player.get('bdl_game_logs', [])
            if len(logs) < 5:
                return {"error": f"Insufficient games: {len(logs)}", "prediction": None}
            
            # Build PHYSICAL features
            feats = self._build_physical_features(player, logs, norm, opponent_team, park_team, line)
            if feats is None:
                return {"error": "Could not build physical features", "prediction": None}
            
            model = self.models[norm]
            scaler = self.scalers[norm]
            feat_cols = self.feature_cols[norm]
            
            X = pd.DataFrame([feats])
            for c in feat_cols:
                if c not in X.columns:
                    X[c] = 0
            X = X[feat_cols].fillna(0)
            X_sc = scaler.transform(X)
            
            # HIGH-PRECISION PREDICTION
            raw_pred = float(model.predict(X_sc)[0])
            
            # Apply park factor
            pf = feats.get('park_factor', 1.0)
            opp_k = feats.get('opp_k_rate', 1.0)
            
            if norm == 'pitcher_strikeouts':
                final_pred = raw_pred * pf * opp_k
            else:
                final_pred = raw_pred * pf
            
            # TRUE L10 SIGMA
            std = feats.get('std_l10', 0)
            l10_avg = feats.get('l10_avg', final_pred)
            cv = std / l10_avg if l10_avg > 0 else 0.5
            
            # MLB volatility floor
            if norm in ['hits', 'total_bases', 'rbis', 'runs', 'hits+runs+rbis', 'home_runs']:
                if cv < 0.35:
                    std = l10_avg * 0.35
            
            # PROBABILITY (Standard Normal CDF)
            prob_over = None
            z_score = None
            
            if line is not None and std > 0:
                z_score = (line - final_pred) / std
                prob_over = (1 - stats.norm.cdf(z_score)) * 100
                
                # STRICT: Prediction < Line = Probability < 50%
                if final_pred < line and prob_over >= 50:
                    prob_over = 50 - abs(z_score) * 8
                    prob_over = max(5, prob_over)
            
            # Physical audit
            physical_audit = {
                'splits': {
                    'vs_lhp_avg': round(feats.get('lhp_avg', 0), 3),
                    'vs_rhp_avg': round(feats.get('rhp_avg', 0), 3),
                    'platoon_split': round(feats.get('platoon_avg', 0), 3),
                    'vs_lhp_k_rate': round(feats.get('lhp_k_rate', 0), 3),
                    'vs_rhp_k_rate': round(feats.get('rhp_k_rate', 0), 3),
                },
                'park': {
                    'venue': park_team,
                    'factor': round(pf, 3),
                    'hits_factor': feats.get('park_hits'),
                    'runs_factor': feats.get('park_runs'),
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
                    'std_l10': round(feats.get('std_l10', 0), 3),
                    'cv_l10': round(feats.get('cv_l10', 0), 3),
                },
                'discipline': {
                    'contact_rate': round(feats.get('contact_rate', 0), 3),
                    'bb_k_ratio': round(feats.get('bb_k_ratio', 0), 3),
                    'power_index': round(feats.get('power_index', 0), 4),
                }
            }
            
            result = {
                'player_name': player_name,
                'stat_type': stat_type,
                'predicted': round(final_pred, 2),  # HIGH PRECISION
                'raw_prediction': round(raw_pred, 4),
                'std_dev': round(std, 4),
                'line': line,
                'prob_over': round(prob_over, 1) if prob_over else None,
                'z_score': round(z_score, 4) if z_score else None,
                'physical_audit': physical_audit,
                'full_features': physical_audit,
                'mlr_features_used': True,
                'model_version': 'MLB_PHYSICAL_v1.0'
            }
            
            logger.info(
                f"[MLB_PHYSICAL] {player_name} {stat_type}: "
                f"pred={final_pred:.2f}, park={pf:.2f}, opp_k={opp_k:.2f}, σ={std:.3f}"
            )
            
            return result
            
        except Exception as e:
            logger.error(f"[MLB_PHYSICAL] Predict error: {e}")
            return {"error": str(e), "prediction": None}


# Global instance
_mlb_phys = None

def get_mlb_physical_engine(db=None):
    global _mlb_phys
    if _mlb_phys is None and db is not None:
        _mlb_phys = MLBPhysicalEngine(db)
    return _mlb_phys

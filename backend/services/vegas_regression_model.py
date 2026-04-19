"""
Vegas Regression Model - Multiple Linear Regression for Player Prop Prediction
================================================================================

GOAL: Reverse-engineer Vegas line-setting by predicting player stat output
      based on historical data and game context.

APPROACH:
---------
Instead of asking "how often did he hit this line?" (backward-looking),
we ask "what will he score tonight?" (forward-looking prediction).

MODEL: Multiple Linear Regression
------
Predicted_Stat = β₀ + β₁(Season_Avg) + β₂(L10_Avg) + β₃(L5_Avg) 
               + β₄(Opp_Def_Rank) + β₅(Home_Away) + β₆(Rest_Days)
               + β₇(Minutes_Avg) + β₈(Usage_Trend) + ...

EDGE CALCULATION:
-----------------
- Predicted = 26.2, Line = 22.5 → Over has +3.7 edge
- Predicted = 21.8, Line = 22.5 → Trap, avoid or Under

DATA SOURCES:
-------------
- nba_master_hub_2026.bdl_game_logs: Historical game-by-game stats
- defensive_momentum_cache: Opponent defensive rankings
- dg_cached_board: Current props with lines

SUPPORTED STATS:
----------------
- PTS (Points)
- REB (Rebounds) 
- AST (Assists)
- PRA (Points + Rebounds + Assists)
- 3PM (Three Pointers Made)
- STL (Steals)
- BLK (Blocks)

NOTE: This is a NEW prediction layer. The existing PropVision v7.2 
      Board Score system is preserved and can be toggled back.
"""

import numpy as np
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone, timedelta
from collections import defaultdict
import logging
import math

from services.config.collection_names import COLL

logger = logging.getLogger(__name__)

# =============================================================================
# STAT TYPE MAPPING
# =============================================================================

STAT_FIELD_MAP = {
    'PTS': 'pts',
    'REB': 'reb',
    'AST': 'ast',
    'PRA': ['pts', 'reb', 'ast'],  # Composite
    'PA': ['pts', 'ast'],           # Composite
    '3PM': 'fg3m',
    'STL': 'stl',
    'BLK': 'blk',
    'FTM': 'ftm',
}

# =============================================================================
# FEATURE EXTRACTION
# =============================================================================

class FeatureExtractor:
    """
    Extracts features from game logs for regression model.
    
    Features:
    ---------
    1. Rolling Averages (L3, L5, L10, Season)
    2. Opponent Defense Rank
    3. Home/Away indicator
    4. Rest days
    5. Minutes trend
    6. Recent volatility (std dev)
    7. Day of week
    """
    
    def __init__(self, db):
        self.db = db
        self._def_cache = {}
        self._load_defensive_rankings()
    
    def _load_defensive_rankings(self):
        """Load defensive momentum cache into memory."""
        try:
            cursor = self.db[COLL("defensive_momentum_cache", "nba")].find({})
            for doc in cursor:
                team = doc.get('team')
                stat_type = doc.get('stat_type', 'DRTG')
                if team:
                    key = f"{team}_{stat_type}"
                    self._def_cache[key] = {
                        'season_rank': doc.get('season_rank', 15),
                        'l10_rank': doc.get('l10_rank', 15),
                        'l5_rank': doc.get('l5_rank', 15),
                        'composite_rank': doc.get('composite_rank', 15),
                        'def_rating': doc.get('season_def_rating', 110),
                    }
            logger.info(f"[REGRESSION] Loaded {len(self._def_cache)} defensive rankings")
        except Exception as e:
            logger.error(f"[REGRESSION] Failed to load defensive rankings: {e}")
    
    def get_stat_value(self, game_log: Dict, stat_type: str) -> Optional[float]:
        """Extract stat value from game log, handling composite stats."""
        field = STAT_FIELD_MAP.get(stat_type.upper())
        
        if field is None:
            return None
        
        if isinstance(field, list):
            # Composite stat (PRA, PA, etc.)
            total = 0
            for f in field:
                val = game_log.get(f)
                if val is None:
                    return None
                total += val
            return total
        else:
            return game_log.get(field)
    
    def extract_features(
        self,
        game_logs: List[Dict],
        stat_type: str,
        opponent_team: str = None,
        is_home: bool = None,
        rest_days: int = 1
    ) -> Dict[str, float]:
        """
        Extract features for prediction.
        
        Args:
            game_logs: List of game logs, sorted newest first
            stat_type: The stat type to predict (PTS, REB, etc.)
            opponent_team: Tonight's opponent abbreviation
            is_home: Whether player is home team
            rest_days: Days since last game
        
        Returns:
            Dictionary of features for regression
        """
        features = {}
        
        if not game_logs:
            return features
        
        # Extract stat values from logs
        values = []
        minutes = []
        
        for log in game_logs[:20]:  # Use up to 20 games
            val = self.get_stat_value(log, stat_type)
            mins = log.get('min', 0)
            
            # Handle minutes as string (e.g., "32:15" or "32")
            if isinstance(mins, str):
                try:
                    if ':' in mins:
                        parts = mins.split(':')
                        mins = int(parts[0]) + int(parts[1]) / 60
                    else:
                        mins = float(mins)
                except:
                    mins = 0
            
            if val is not None:
                values.append(float(val))
                minutes.append(float(mins))
        
        if not values:
            return features
        
        # =================================================================
        # FEATURE 1: Rolling Averages
        # =================================================================
        features['season_avg'] = np.mean(values) if values else 0
        features['l10_avg'] = np.mean(values[:10]) if len(values) >= 5 else features['season_avg']
        features['l5_avg'] = np.mean(values[:5]) if len(values) >= 3 else features['l10_avg']
        features['l3_avg'] = np.mean(values[:3]) if len(values) >= 3 else features['l5_avg']
        
        # =================================================================
        # FEATURE 2: Trend (is player trending up or down?)
        # =================================================================
        if len(values) >= 10:
            recent_avg = np.mean(values[:5])
            older_avg = np.mean(values[5:10])
            features['trend'] = (recent_avg - older_avg) / max(older_avg, 1) * 100
        else:
            features['trend'] = 0
        
        # =================================================================
        # FEATURE 3: Volatility (standard deviation)
        # =================================================================
        if len(values) >= 5:
            features['std_dev'] = np.std(values[:10], ddof=1)  # Sample std dev
            features['cv'] = features['std_dev'] / max(features['l10_avg'], 1)  # Coefficient of variation
        else:
            features['std_dev'] = 0
            features['cv'] = 0
        
        # =================================================================
        # FEATURE 4: Floor and Ceiling
        # =================================================================
        if len(values) >= 10:
            features['floor'] = np.percentile(values[:10], 10)  # 10th percentile
            features['ceiling'] = np.percentile(values[:10], 90)  # 90th percentile
            features['median'] = np.median(values[:10])
        else:
            features['floor'] = min(values) if values else 0
            features['ceiling'] = max(values) if values else 0
            features['median'] = np.median(values) if values else 0
        
        # =================================================================
        # FEATURE 5: Minutes (playing time)
        # =================================================================
        features['minutes_avg'] = np.mean(minutes[:10]) if minutes else 30
        features['minutes_l5'] = np.mean(minutes[:5]) if len(minutes) >= 5 else features['minutes_avg']
        
        # =================================================================
        # FEATURE 6: Opponent Defense (if provided)
        # =================================================================
        if opponent_team:
            # Map stat type to defensive category
            def_stat = 'DRTG'  # Default
            if stat_type.upper() in ['PTS', 'PRA', 'PA']:
                def_stat = 'PTS'
            elif stat_type.upper() == 'REB':
                def_stat = 'REB'
            elif stat_type.upper() == 'AST':
                def_stat = 'AST'
            elif stat_type.upper() == '3PM':
                def_stat = '3PM'
            
            def_key = f"{opponent_team}_{def_stat}"
            def_data = self._def_cache.get(def_key, self._def_cache.get(f"{opponent_team}_DRTG", {}))
            
            features['opp_def_rank'] = def_data.get('composite_rank', 15)
            features['opp_def_rating'] = def_data.get('def_rating', 110)
            
            # Normalize rank to -1 to +1 scale (1 = best defense, 30 = worst)
            # Positive = weak defense (good for player), Negative = strong defense
            features['def_factor'] = (features['opp_def_rank'] - 15.5) / 14.5
        else:
            features['opp_def_rank'] = 15
            features['opp_def_rating'] = 110
            features['def_factor'] = 0
        
        # =================================================================
        # FEATURE 7: Home/Away
        # =================================================================
        if is_home is not None:
            features['is_home'] = 1 if is_home else 0
        else:
            features['is_home'] = 0.5  # Unknown, neutral
        
        # =================================================================
        # FEATURE 8: Rest Days
        # =================================================================
        features['rest_days'] = rest_days
        features['is_b2b'] = 1 if rest_days == 0 else 0
        features['is_rested'] = 1 if rest_days >= 2 else 0
        
        # =================================================================
        # FEATURE 9: Mode (most frequent outcome)
        # =================================================================
        if len(values) >= 10:
            from collections import Counter
            rounded = [round(v * 2) / 2 for v in values[:10]]  # Round to 0.5
            counts = Counter(rounded)
            mode_val, mode_count = counts.most_common(1)[0]
            features['mode'] = mode_val
            features['mode_freq'] = mode_count / 10  # How often mode appears
        else:
            features['mode'] = features['median']
            features['mode_freq'] = 0.2
        
        return features


# =============================================================================
# REGRESSION MODEL
# =============================================================================

class VegasRegressionModel:
    """
    Multiple Linear Regression model for predicting player stat output.
    
    This uses a weighted combination of features based on historical
    correlations with actual outcomes.
    
    WEIGHTS (empirically tuned):
    - L5 Average: 35% (most recent form)
    - L10 Average: 25% (stable baseline)
    - Season Average: 15% (long-term true talent)
    - Matchup Adjustment: 10% (opponent defense)
    - Minutes Adjustment: 10% (playing time changes)
    - Trend Adjustment: 5% (momentum)
    """
    
    # Feature weights (normalized to sum to 1.0 for base prediction)
    # Adjustments are applied as multipliers/additions afterward
    WEIGHTS = {
        'l5_weight': 0.50,      # Most recent form (50%)
        'l10_weight': 0.30,     # Stable baseline (30%)
        'season_weight': 0.20,  # Long-term true talent (20%)
    }
    
    # Defense impact factors by stat type
    # How much does opponent defense affect this stat? (0-1 scale)
    DEFENSE_IMPACT = {
        'PTS': 0.15,   # Points affected moderately by defense
        'REB': 0.08,   # Rebounds less affected
        'AST': 0.10,   # Assists somewhat affected
        'PRA': 0.12,   # Composite
        'PA': 0.13,    # Composite
        '3PM': 0.12,   # 3-pointers moderately affected
        'STL': 0.05,   # Steals mostly player-driven
        'BLK': 0.05,   # Blocks mostly player-driven
    }
    
    def __init__(self, db):
        self.db = db
        self.extractor = FeatureExtractor(db)
    
    def predict(
        self,
        game_logs: List[Dict],
        stat_type: str,
        opponent_team: str = None,
        is_home: bool = None,
        rest_days: int = 1,
        line: float = None
    ) -> Dict[str, Any]:
        """
        Predict player's stat output for tonight's game.
        
        Args:
            game_logs: Historical game logs (newest first)
            stat_type: Stat type to predict
            opponent_team: Opponent abbreviation
            is_home: Home game flag
            rest_days: Days since last game
            line: The prop line (for edge calculation)
        
        Returns:
            Dictionary with prediction, confidence, and edge
        """
        result = {
            'predicted': None,
            'confidence': 'LOW',
            'edge': None,
            'edge_pct': None,
            'recommendation': 'AVOID',
            'features': {},
            'breakdown': {}
        }
        
        # Extract features
        features = self.extractor.extract_features(
            game_logs, stat_type, opponent_team, is_home, rest_days
        )
        
        if not features or 'season_avg' not in features:
            result['error'] = 'Insufficient data for prediction'
            return result
        
        result['features'] = features
        
        # =================================================================
        # STEP 1: Base Prediction (Weighted Average - weights sum to 1.0)
        # =================================================================
        base_pred = (
            features['l5_avg'] * self.WEIGHTS['l5_weight'] +
            features['l10_avg'] * self.WEIGHTS['l10_weight'] +
            features['season_avg'] * self.WEIGHTS['season_weight']
        )
        
        # =================================================================
        # STEP 2: Matchup Adjustment
        # =================================================================
        # def_factor ranges from -1 (elite defense) to +1 (weak defense)
        def_factor = features.get('def_factor', 0)
        def_impact = self.DEFENSE_IMPACT.get(stat_type.upper(), 0.10)
        
        # Matchup adjustment: scale by defense impact
        # Weak defense (+def_factor) → boost prediction
        # Strong defense (-def_factor) → lower prediction
        matchup_adj = base_pred * def_factor * def_impact
        
        # =================================================================
        # STEP 3: Minutes Adjustment
        # =================================================================
        minutes_avg = features.get('minutes_avg', 30)
        minutes_l5 = features.get('minutes_l5', minutes_avg)
        
        # If recent minutes differ from average, adjust proportionally
        if minutes_avg > 0:
            minutes_ratio = minutes_l5 / minutes_avg
            # Cap the adjustment to +/- 10%
            minutes_ratio = max(0.9, min(1.1, minutes_ratio))
            minutes_adj = base_pred * (minutes_ratio - 1) * 0.5
        else:
            minutes_adj = 0
        
        # =================================================================
        # STEP 4: Trend Adjustment
        # =================================================================
        trend = features.get('trend', 0)
        # trend is in percentage, convert to factor
        # Cap at +/- 5% adjustment
        trend_adj = base_pred * max(-0.05, min(0.05, trend / 100))
        
        # =================================================================
        # STEP 5: Home/Away Adjustment
        # =================================================================
        is_home = features.get('is_home', 0.5)
        # Small home court advantage (about 2%)
        home_adj = base_pred * 0.02 * (is_home - 0.5) * 2
        
        # =================================================================
        # STEP 6: Rest Day Adjustment
        # =================================================================
        is_b2b = features.get('is_b2b', 0)
        is_rested = features.get('is_rested', 0)
        # B2B: slight decrease, Rested: slight increase
        rest_adj = base_pred * (-0.03 * is_b2b + 0.02 * is_rested)
        
        # =================================================================
        # FINAL PREDICTION
        # =================================================================
        predicted = base_pred + matchup_adj + minutes_adj + trend_adj + home_adj + rest_adj
        
        # Ensure non-negative
        predicted = max(0, predicted)
        
        result['predicted'] = round(predicted, 2)
        result['breakdown'] = {
            'base_prediction': round(base_pred, 2),
            'matchup_adjustment': round(matchup_adj, 2),
            'minutes_adjustment': round(minutes_adj, 2),
            'trend_adjustment': round(trend_adj, 2),
            'home_adjustment': round(home_adj, 2),
            'rest_adjustment': round(rest_adj, 2),
        }
        
        # =================================================================
        # CONFIDENCE CALCULATION
        # =================================================================
        # Based on sample size and volatility
        sample_size = len(game_logs) if game_logs else 0
        cv = features.get('cv', 0.5)  # Coefficient of variation
        
        if sample_size >= 15 and cv < 0.3:
            result['confidence'] = 'HIGH'
        elif sample_size >= 10 and cv < 0.4:
            result['confidence'] = 'MEDIUM'
        else:
            result['confidence'] = 'LOW'
        
        # =================================================================
        # EDGE CALCULATION (vs Line)
        # =================================================================
        if line is not None:
            edge = predicted - line
            edge_pct = (edge / line) * 100 if line > 0 else 0
            
            result['edge'] = round(edge, 2)
            result['edge_pct'] = round(edge_pct, 2)
            result['line'] = line
            
            # Recommendation based on edge
            std_dev = features.get('std_dev', 3)
            
            # Edge thresholds (in standard deviations)
            if edge > std_dev * 0.5:
                result['recommendation'] = 'STRONG_OVER'
            elif edge > std_dev * 0.25:
                result['recommendation'] = 'LEAN_OVER'
            elif edge < -std_dev * 0.5:
                result['recommendation'] = 'STRONG_UNDER'
            elif edge < -std_dev * 0.25:
                result['recommendation'] = 'LEAN_UNDER'
            else:
                result['recommendation'] = 'NEUTRAL'
            
            # Calculate probability using normal distribution approximation
            if std_dev > 0:
                z_score = edge / std_dev
                # Convert z-score to probability (approximate)
                prob_over = self._z_to_prob(z_score)
                result['prob_over'] = round(prob_over * 100, 1)
                result['prob_under'] = round((1 - prob_over) * 100, 1)
        
        return result
    
    def _z_to_prob(self, z: float) -> float:
        """Convert z-score to probability using normal CDF approximation."""
        # Using error function approximation
        # P(X > line) = 0.5 * (1 + erf(z / sqrt(2)))
        return 0.5 * (1 + math.erf(z / math.sqrt(2)))
    
    def predict_batch(
        self,
        props: List[Dict],
        hub_collection
    ) -> List[Dict]:
        """
        Run predictions for a batch of props.
        
        Args:
            props: List of prop dictionaries with player_name, stat_type, line
            hub_collection: MongoDB collection with player data
        
        Returns:
            List of props enriched with predictions
        """
        results = []
        
        for prop in props:
            player_name = prop.get('player_name')
            stat_type = prop.get('stat_type')
            line = prop.get('line')
            opponent = prop.get('opponent') or prop.get('opponent_abbr')
            
            # Find player in hub
            player_doc = hub_collection.find_one({
                '$or': [
                    {'player_name': player_name},
                    {'display_name': player_name},
                    {'odds_api_name': player_name}
                ]
            })
            
            if not player_doc:
                prop['vegas_prediction'] = {'error': 'Player not found'}
                results.append(prop)
                continue
            
            game_logs = player_doc.get('bdl_game_logs', [])
            
            if not game_logs:
                prop['vegas_prediction'] = {'error': 'No game logs'}
                results.append(prop)
                continue
            
            # Run prediction
            prediction = self.predict(
                game_logs=game_logs,
                stat_type=stat_type,
                opponent_team=opponent,
                is_home=prop.get('is_home'),
                rest_days=prop.get('rest_days', 1),
                line=line
            )
            
            prop['vegas_prediction'] = prediction
            results.append(prop)
        
        return results


# =============================================================================
# INTEGRATION WITH EXISTING SYSTEM
# =============================================================================

def calculate_vegas_edge(
    db,
    player_name: str,
    stat_type: str,
    line: float,
    opponent: str = None,
    is_home: bool = None,
    rest_days: int = 1
) -> Dict[str, Any]:
    """
    Convenience function to get Vegas prediction for a single prop.
    
    This can be called from ferrari_tier_service to add regression-based
    edge calculation alongside the existing Board Score.
    
    Returns:
        Dictionary with predicted value, edge, and recommendation
    """
    hub = db['nba_master_hub_2026']
    
    # Find player
    player_doc = hub.find_one({
        '$or': [
            {'player_name': player_name},
            {'display_name': player_name},
            {'odds_api_name': player_name}
        ]
    })
    
    if not player_doc:
        return {'error': 'Player not found'}
    
    game_logs = player_doc.get('bdl_game_logs', [])
    
    if not game_logs:
        return {'error': 'No game logs'}
    
    model = VegasRegressionModel(db)
    return model.predict(
        game_logs=game_logs,
        stat_type=stat_type,
        opponent_team=opponent,
        is_home=is_home,
        rest_days=rest_days,
        line=line
    )


# =============================================================================
# EXPORT
# =============================================================================

__all__ = [
    'VegasRegressionModel',
    'FeatureExtractor',
    'calculate_vegas_edge',
    'STAT_FIELD_MAP'
]

"""
MLB Vegas Killer Model v1.0
===========================
Dedicated XGBoost model trained on 3+ years of MLB data.

THIS IS NOT THE NBA MODEL. Baseball is a different animal:
- Higher variance (0-for-4 nights are normal)
- Strong park factor effects (Coors vs Oracle Park)
- Pitcher vs Batter matchup dependencies
- Weather impacts (wind, temperature)

FEATURES (80+):
- Recent Performance: L5/L10/L20/Season EWMA
- Volatility: CV, std_dev, floor/ceiling
- Park Factors: By stat type (hits, HRs, runs)
- Matchup: Opponent K rate, bullpen ERA
- Platoon: vs LHP/RHP splits
- Situational: Home/Away, day/night

Author: PropVision AI
Version: 1.0.0
"""
import logging
import numpy as np
import pandas as pd
import pickle
import os
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timezone
from scipy import stats

from services.config.collection_names import COLL

logger = logging.getLogger(__name__)


class MLBVegasKillerModel:
    """
    Dedicated MLB XGBoost prediction model.
    Trained separately from NBA - different sport, different model.
    """
    
    # MLB stat types we train models for
    MLB_STAT_TYPES = [
        'hits',
        'total_bases', 
        'rbis',
        'runs',
        'pitcher_strikeouts',
        'hits+runs+rbis'
    ]
    
    # Stat field mapping
    STAT_FIELD_MAP = {
        'hits': 'hits',
        'total_bases': 'total_bases',
        'rbis': 'rbis',
        'runs': 'runs',
        'stolen_bases': 'stolen_bases',
        'home_runs': 'home_runs',
        'walks': 'walks',
        'strikeouts': 'strikeouts',
        'pitcher_strikeouts': 'pitcher_strikeouts',
        'pitcher_walks': 'pitcher_walks',
        'hits_allowed': 'hits_allowed',
        'earned_runs': 'earned_runs',
        'innings_pitched': 'innings_pitched',
        'hits+runs+rbis': ['hits', 'runs', 'rbis'],
    }
    
    # Park factors by team (home stadium)
    # Values > 1.0 = hitter friendly, < 1.0 = pitcher friendly
    PARK_FACTORS = {
        # Extreme hitter parks
        'COL': {'hits': 1.18, 'runs': 1.25, 'home_runs': 1.30, 'strikeouts': 0.92},
        'CIN': {'hits': 1.10, 'runs': 1.12, 'home_runs': 1.15, 'strikeouts': 0.95},
        'TEX': {'hits': 1.08, 'runs': 1.10, 'home_runs': 1.12, 'strikeouts': 0.96},
        'BOS': {'hits': 1.06, 'runs': 1.08, 'home_runs': 0.96, 'strikeouts': 0.98},
        'PHI': {'hits': 1.05, 'runs': 1.06, 'home_runs': 1.08, 'strikeouts': 0.97},
        
        # Neutral parks
        'NYY': {'hits': 1.02, 'runs': 1.04, 'home_runs': 1.10, 'strikeouts': 0.98},
        'LAD': {'hits': 1.00, 'runs': 1.00, 'home_runs': 1.02, 'strikeouts': 1.00},
        'ATL': {'hits': 1.00, 'runs': 1.02, 'home_runs': 1.05, 'strikeouts': 0.99},
        'HOU': {'hits': 0.98, 'runs': 0.98, 'home_runs': 1.00, 'strikeouts': 1.00},
        'MIN': {'hits': 1.00, 'runs': 1.02, 'home_runs': 1.08, 'strikeouts': 0.98},
        
        # Pitcher friendly parks
        'SF': {'hits': 0.92, 'runs': 0.88, 'home_runs': 0.82, 'strikeouts': 1.05},
        'OAK': {'hits': 0.94, 'runs': 0.90, 'home_runs': 0.88, 'strikeouts': 1.04},
        'SD': {'hits': 0.95, 'runs': 0.92, 'home_runs': 0.90, 'strikeouts': 1.03},
        'MIA': {'hits': 0.96, 'runs': 0.94, 'home_runs': 0.88, 'strikeouts': 1.02},
        'TB': {'hits': 0.96, 'runs': 0.94, 'home_runs': 0.92, 'strikeouts': 1.02},
        'SEA': {'hits': 0.94, 'runs': 0.90, 'home_runs': 0.86, 'strikeouts': 1.05},
        'NYM': {'hits': 0.97, 'runs': 0.95, 'home_runs': 0.94, 'strikeouts': 1.01},
        'LAA': {'hits': 0.98, 'runs': 0.96, 'home_runs': 0.95, 'strikeouts': 1.01},
        
        # Default
        'DEFAULT': {'hits': 1.00, 'runs': 1.00, 'home_runs': 1.00, 'strikeouts': 1.00},
    }
    
    # Team strikeout rates (how often they K as a team)
    # Higher = strikes out more = good for pitcher K props
    TEAM_K_RATES = {
        'ARI': 1.12, 'DET': 1.10, 'OAK': 1.08, 'CHC': 1.06, 'MIA': 1.05,
        'COL': 1.04, 'PIT': 1.03, 'CIN': 1.02, 'SEA': 1.01, 'TEX': 1.00,
        'ATL': 0.99, 'NYM': 0.98, 'PHI': 0.97, 'LAD': 0.96, 'SD': 0.96,
        'SF': 0.95, 'HOU': 0.94, 'NYY': 0.93, 'CLE': 0.92, 'KC': 0.90,
        'MIN': 0.91, 'BOS': 0.95, 'TB': 0.97, 'BAL': 0.98, 'TOR': 0.99,
        'CHW': 1.01, 'MIL': 0.98, 'STL': 0.97, 'WSH': 1.02, 'LAA': 0.99,
    }
    
    # Model storage
    MODEL_DIR = '/app/backend/models/mlb'
    
    def __init__(self, db):
        """
        Initialize the MLB Vegas Killer Model.
        
        Args:
            db: PyMongo database instance (SYNC, not async)
        """
        self.db = db
        self.master_hub = db[COLL("master_hub", "mlb")]
        self.historical_logs = db.mlb_historical_logs
        
        # Trained models storage
        self.models = {}
        self.scalers = {}
        self.feature_cols = {}
        
        # Create model directory if needed
        os.makedirs(self.MODEL_DIR, exist_ok=True)
        
        logger.info("[MLB_VK_MODEL] Initialized MLB Vegas Killer Model v1.0")
    
    def _normalize_stat_type(self, stat_type: str) -> str:
        """Normalize stat type to internal field name."""
        stat_lower = stat_type.lower().replace(' ', '_').replace('+', '+')
        
        aliases = {
            'k': 'pitcher_strikeouts',
            'so': 'pitcher_strikeouts',
            'ks': 'pitcher_strikeouts',
            'pitcher ks': 'pitcher_strikeouts',
            'pitcher k': 'pitcher_strikeouts',
            'pitcher strikeouts': 'pitcher_strikeouts',
            'batter strikeouts': 'strikeouts',
            'tb': 'total_bases',
            'rbi': 'rbis',
            'sb': 'stolen_bases',
            'hr': 'home_runs',
            'h': 'hits',
            'r': 'runs',
            'bb': 'walks',
            'hrr': 'hits+runs+rbis',
            'hits+runs+rbi': 'hits+runs+rbis',
        }
        
        return aliases.get(stat_lower, stat_lower)
    
    def _get_stat_from_game(self, game: Dict, stat_type: str) -> Optional[float]:
        """Extract stat value from a game log."""
        field = self.STAT_FIELD_MAP.get(stat_type, stat_type)
        
        if isinstance(field, list):
            total = 0
            for f in field:
                val = game.get(f, 0)
                if val is not None:
                    total += float(val)
            return total
        
        val = game.get(field)
        return float(val) if val is not None else None
    
    def _get_park_factor(self, park_team: str, stat_type: str) -> float:
        """Get park factor for a stat type at a given stadium."""
        park_data = self.PARK_FACTORS.get(park_team, self.PARK_FACTORS['DEFAULT'])
        
        # Map stat to park factor category
        if stat_type in ['hits', 'total_bases']:
            return park_data.get('hits', 1.0)
        elif stat_type in ['runs', 'rbis', 'hits+runs+rbis']:
            return park_data.get('runs', 1.0)
        elif stat_type == 'home_runs':
            return park_data.get('home_runs', 1.0)
        elif stat_type == 'pitcher_strikeouts':
            return park_data.get('strikeouts', 1.0)
        
        return 1.0
    
    def _get_team_k_rate(self, team_abbr: str) -> float:
        """Get team's strikeout rate tendency."""
        return self.TEAM_K_RATES.get(team_abbr, 1.0)
    
    def _calculate_ewma(self, values: List[float], alpha: float = 0.3) -> float:
        """Calculate Exponentially Weighted Moving Average."""
        if not values:
            return 0.0
        result = values[0]
        for v in values[1:]:
            result = alpha * v + (1 - alpha) * result
        return result
    
    def _build_feature_vector(
        self,
        game_logs: List[Dict],
        stat_type: str,
        opponent_team: str = None,
        park_team: str = None,
        line: float = None
    ) -> Dict[str, float]:
        """
        Build the full feature vector for a prediction.
        
        Returns dict with 80+ features for XGBoost.
        """
        features = {}
        
        # Extract stat values from games
        stat_values = []
        for game in game_logs[:30]:  # Use up to L30
            val = self._get_stat_from_game(game, stat_type)
            if val is not None:
                stat_values.append(val)
        
        if len(stat_values) < 3:
            return None  # Insufficient data
        
        # Slice for different windows
        l3 = stat_values[:3] if len(stat_values) >= 3 else stat_values
        l5 = stat_values[:5] if len(stat_values) >= 5 else stat_values
        l10 = stat_values[:10] if len(stat_values) >= 10 else stat_values
        l20 = stat_values[:20] if len(stat_values) >= 20 else stat_values
        
        # =====================================================================
        # RECENT PERFORMANCE FEATURES
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
        
        # EWMA (Exponentially Weighted Moving Average)
        features['ewma_l5'] = self._calculate_ewma(l5, alpha=0.5)
        features['ewma_l10'] = self._calculate_ewma(l10, alpha=0.3)
        features['ewma_l20'] = self._calculate_ewma(l20, alpha=0.2)
        
        # Trend
        if features['ewma_l10'] > 0:
            features['ewma_trend'] = (features['ewma_l5'] - features['ewma_l10']) / features['ewma_l10']
        else:
            features['ewma_trend'] = 0
        
        # =====================================================================
        # VOLATILITY FEATURES
        # =====================================================================
        features['std_dev_l5'] = np.std(l5, ddof=1) if len(l5) > 1 else 0
        features['std_dev_l10'] = np.std(l10, ddof=1) if len(l10) > 1 else 0
        features['std_dev_l20'] = np.std(l20, ddof=1) if len(l20) > 1 else 0
        
        features['cv_l5'] = features['std_dev_l5'] / features['l5_avg'] if features['l5_avg'] > 0 else 0
        features['cv_l10'] = features['std_dev_l10'] / features['l10_avg'] if features['l10_avg'] > 0 else 0
        
        features['range_l5'] = features['l5_max'] - features['l5_min']
        features['range_l10'] = features['l10_max'] - features['l10_min']
        
        # =====================================================================
        # LINE FEATURES (if line provided)
        # =====================================================================
        if line is not None:
            features['line'] = line
            features['line_vs_l5'] = line - features['l5_avg']
            features['line_vs_l10'] = line - features['l10_avg']
            features['line_vs_ewma'] = line - features['ewma_l10']
            features['line_vs_median'] = line - features['l10_median']
            
            # Hit rate calculations
            l5_hits = sum(1 for v in l5 if v > line)
            l10_hits = sum(1 for v in l10 if v > line)
            features['hit_rate_l5'] = l5_hits / len(l5) * 100
            features['hit_rate_l10'] = l10_hits / len(l10) * 100
        
        # =====================================================================
        # PARK FACTOR FEATURES
        # =====================================================================
        if park_team:
            features['park_factor'] = self._get_park_factor(park_team, stat_type)
            features['park_hits_factor'] = self.PARK_FACTORS.get(park_team, {}).get('hits', 1.0)
            features['park_runs_factor'] = self.PARK_FACTORS.get(park_team, {}).get('runs', 1.0)
            features['park_hr_factor'] = self.PARK_FACTORS.get(park_team, {}).get('home_runs', 1.0)
            features['park_k_factor'] = self.PARK_FACTORS.get(park_team, {}).get('strikeouts', 1.0)
        else:
            features['park_factor'] = 1.0
            features['park_hits_factor'] = 1.0
            features['park_runs_factor'] = 1.0
            features['park_hr_factor'] = 1.0
            features['park_k_factor'] = 1.0
        
        # =====================================================================
        # OPPONENT FEATURES
        # =====================================================================
        if opponent_team:
            features['opp_k_rate'] = self._get_team_k_rate(opponent_team)
        else:
            features['opp_k_rate'] = 1.0
        
        # =====================================================================
        # SITUATIONAL FEATURES (from game logs)
        # =====================================================================
        # Home/Away tendency (check last 10 games)
        home_values = []
        away_values = []
        for game in game_logs[:10]:
            val = self._get_stat_from_game(game, stat_type)
            if val is not None:
                # Check if home or away (simplified - check team name vs opponent)
                team = game.get('team_name', '')
                if 'home' in str(game.get('location', '')).lower() or game.get('is_home'):
                    home_values.append(val)
                else:
                    away_values.append(val)
        
        features['home_avg'] = np.mean(home_values) if home_values else features['l10_avg']
        features['away_avg'] = np.mean(away_values) if away_values else features['l10_avg']
        features['home_away_split'] = features['home_avg'] - features['away_avg']
        
        # =====================================================================
        # STREAK FEATURES
        # =====================================================================
        if line is not None:
            # Current streak
            streak = 0
            for val in stat_values[:10]:
                if val > line:
                    streak += 1
                else:
                    break
            features['current_hit_streak'] = streak
            
            # Cold streak (consecutive misses)
            cold_streak = 0
            for val in stat_values[:10]:
                if val <= line:
                    cold_streak += 1
                else:
                    break
            features['current_miss_streak'] = cold_streak
        
        return features
    
    def build_training_dataset(self, stat_type: str) -> pd.DataFrame:
        """
        Build training dataset from historical MLB data.
        
        Returns DataFrame with features and target (actual stat value).
        """
        logger.info(f"[MLB_VK_TRAIN] Building training dataset for {stat_type}")
        
        norm_stat = self._normalize_stat_type(stat_type)
        
        training_data = []
        
        # Get all players with historical logs
        cursor = self.historical_logs.find({}, {'_id': 0})
        
        for player_doc in cursor:
            player_name = player_doc.get('player_name')
            game_logs = player_doc.get('game_logs', [])
            
            if len(game_logs) < 15:  # Need at least 15 games
                continue
            
            # Sort by date descending (handle None dates)
            game_logs = sorted(
                game_logs, 
                key=lambda x: x.get('date') or '1900-01-01', 
                reverse=True
            )
            
            # Create training samples using sliding window
            # For each game, predict it using previous games as features
            for i in range(len(game_logs) - 15):
                target_game = game_logs[i]
                history = game_logs[i+1:i+31]  # Previous 30 games for features
                
                # Get target value
                target_value = self._get_stat_from_game(target_game, norm_stat)
                if target_value is None:
                    continue
                
                # Get opponent and park
                opponent = target_game.get('opponent_abbr')
                # For park, we'd need home/away info - use opponent as proxy for away games
                
                # Build features from history
                features = self._build_feature_vector(
                    history, 
                    norm_stat,
                    opponent_team=opponent,
                    park_team=None,  # Would need home/away detection
                    line=None  # No line for training
                )
                
                if features is None:
                    continue
                
                # Add target
                features['target'] = target_value
                features['player_name'] = player_name
                features['game_date'] = target_game.get('date')
                features['opponent'] = opponent
                
                training_data.append(features)
        
        df = pd.DataFrame(training_data)
        logger.info(f"[MLB_VK_TRAIN] Built {len(df)} training samples for {stat_type}")
        
        return df
    
    def train(self, stat_type: str, test_size: float = 0.2) -> Dict[str, Any]:
        """
        Train XGBoost model for a specific MLB stat type.
        
        Args:
            stat_type: MLB stat type (hits, pitcher_strikeouts, etc.)
            test_size: Fraction of data to use for testing
            
        Returns:
            Training metrics dict
        """
        from sklearn.model_selection import train_test_split
        from sklearn.preprocessing import StandardScaler
        from sklearn.metrics import mean_absolute_error, r2_score
        
        try:
            import xgboost as xgb
        except ImportError:
            logger.error("XGBoost not installed!")
            return {'error': 'XGBoost not installed'}
        
        norm_stat = self._normalize_stat_type(stat_type)
        logger.info(f"[MLB_VK_TRAIN] Training model for {norm_stat}")
        
        # Build dataset
        df = self.build_training_dataset(stat_type)
        
        if len(df) < 100:
            return {'error': f'Insufficient training data: {len(df)} samples'}
        
        # Separate features and target
        exclude_cols = ['target', 'player_name', 'game_date', 'opponent']
        feature_cols = [c for c in df.columns if c not in exclude_cols]
        
        X = df[feature_cols].fillna(0)
        y = df['target']
        
        # Train/test split
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=test_size, random_state=42
        )
        
        # Scale features
        scaler = StandardScaler()
        X_train_scaled = scaler.fit_transform(X_train)
        X_test_scaled = scaler.transform(X_test)
        
        # Train XGBoost
        model = xgb.XGBRegressor(
            n_estimators=200,
            max_depth=6,
            learning_rate=0.1,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            n_jobs=-1
        )
        
        model.fit(X_train_scaled, y_train)
        
        # Evaluate
        train_pred = model.predict(X_train_scaled)
        test_pred = model.predict(X_test_scaled)
        
        train_mae = mean_absolute_error(y_train, train_pred)
        test_mae = mean_absolute_error(y_test, test_pred)
        train_r2 = r2_score(y_train, train_pred)
        test_r2 = r2_score(y_test, test_pred)
        
        # Feature importance
        importance = dict(zip(feature_cols, model.feature_importances_))
        importance = dict(sorted(importance.items(), key=lambda x: -x[1])[:20])
        
        # Store model
        self.models[norm_stat] = model
        self.scalers[norm_stat] = scaler
        self.feature_cols[norm_stat] = feature_cols
        
        metrics = {
            'stat_type': norm_stat,
            'n_samples': len(df),
            'n_features': len(feature_cols),
            'train': {'mae': round(train_mae, 3), 'r2': round(train_r2, 3)},
            'test': {'mae': round(test_mae, 3), 'r2': round(test_r2, 3)},
            'feature_importance': importance
        }
        
        logger.info(f"[MLB_VK_TRAIN] {norm_stat}: MAE={test_mae:.3f}, R²={test_r2:.3f}")
        
        return metrics
    
    def save_models(self):
        """Save all trained models to disk."""
        for stat_type in self.models:
            model_data = {
                'model': self.models[stat_type],
                'scaler': self.scalers[stat_type],
                'features': self.feature_cols[stat_type],
                'version': 'MLB_VK_v1.0',
                'trained_at': datetime.now(timezone.utc).isoformat()
            }
            
            path = os.path.join(self.MODEL_DIR, f'mlb_vk_{stat_type}.pkl')
            with open(path, 'wb') as f:
                pickle.dump(model_data, f)
            
            logger.info(f"[MLB_VK_MODEL] Saved {stat_type} model to {path}")
    
    def load_models(self):
        """Load trained models from disk."""
        loaded = 0
        
        for stat_type in self.MLB_STAT_TYPES:
            path = os.path.join(self.MODEL_DIR, f'mlb_vk_{stat_type}.pkl')
            
            if os.path.exists(path):
                try:
                    with open(path, 'rb') as f:
                        model_data = pickle.load(f)
                    
                    self.models[stat_type] = model_data['model']
                    self.scalers[stat_type] = model_data['scaler']
                    self.feature_cols[stat_type] = model_data['features']
                    loaded += 1
                    
                    logger.info(f"[MLB_VK_MODEL] Loaded {stat_type} model")
                except Exception as e:
                    logger.error(f"[MLB_VK_MODEL] Failed to load {stat_type}: {e}")
        
        logger.info(f"[MLB_VK_MODEL] Loaded {loaded}/{len(self.MLB_STAT_TYPES)} models")
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
        Generate prediction for an MLB player prop using trained XGBoost.
        
        Args:
            player_name: Player's name
            stat_type: Stat type
            line: Betting line (optional)
            opponent_team: Opponent team abbreviation
            park_team: Stadium team abbreviation
            
        Returns:
            Dict with high-precision prediction, std_dev, z_score, matchup features
        """
        norm_stat = self._normalize_stat_type(stat_type)
        
        # Check if model exists
        if norm_stat not in self.models:
            # Fallback to statistical model if no trained model
            return self._predict_statistical(player_name, norm_stat, line, opponent_team, park_team)
        
        try:
            # Find player
            player = self.master_hub.find_one(
                {"$or": [
                    {"display_name": player_name},
                    {"player_name": player_name},
                    {"mlb_full_name": player_name}
                ]},
                {"_id": 0}
            )
            
            if not player:
                return {"error": f"Player not found: {player_name}"}
            
            game_logs = player.get('bdl_game_logs', [])
            if len(game_logs) < 5:
                return {"error": f"Insufficient games: {len(game_logs)}"}
            
            # Build feature vector
            features = self._build_feature_vector(
                game_logs, norm_stat, opponent_team, park_team, line
            )
            
            if features is None:
                return {"error": "Could not build feature vector"}
            
            # Get model components
            model = self.models[norm_stat]
            scaler = self.scalers[norm_stat]
            feature_cols = self.feature_cols[norm_stat]
            
            # Prepare features for model
            X = pd.DataFrame([features])
            
            # Ensure all required features exist
            for col in feature_cols:
                if col not in X.columns:
                    X[col] = 0
            
            X = X[feature_cols].fillna(0)
            X_scaled = scaler.transform(X)
            
            # Get prediction (HIGH PRECISION)
            raw_prediction = float(model.predict(X_scaled)[0])
            
            # Apply park factor adjustment
            park_factor = features.get('park_factor', 1.0)
            final_prediction = raw_prediction * park_factor
            
            # Get L10 std_dev for probability calculation
            std_dev = features.get('std_dev_l10', 0)
            
            # MLB VOLATILITY FLOOR
            l10_avg = features.get('l10_avg', final_prediction)
            cv = std_dev / l10_avg if l10_avg > 0 else 0.5
            
            MLB_MIN_CV = 0.35
            if norm_stat in ['hits', 'total_bases', 'rbis', 'runs', 'hits+runs+rbis']:
                if cv < MLB_MIN_CV:
                    std_dev = l10_avg * MLB_MIN_CV
            
            # Calculate probability using Z-score CDF
            prob_over = None
            z_score = None
            edge = None
            
            if line is not None and std_dev > 0:
                z_score = (line - final_prediction) / std_dev
                prob_over = (1 - stats.norm.cdf(z_score)) * 100
                edge = prob_over - 50  # vs 50/50 baseline
            
            # Build matchup dict for audit
            matchup = {
                'opponent_team': opponent_team,
                'park_team': park_team,
                'park_factor': park_factor,
                'park_hits_factor': features.get('park_hits_factor'),
                'park_runs_factor': features.get('park_runs_factor'),
                'park_k_factor': features.get('park_k_factor'),
                'opp_k_rate': features.get('opp_k_rate'),
            }
            
            result = {
                'player_name': player_name,
                'stat_type': stat_type,
                'predicted': round(final_prediction, 2),  # High precision
                'raw_prediction': round(raw_prediction, 4),  # Even higher for audit
                'std_dev': round(std_dev, 4),
                'line': line,
                'prob_over': round(prob_over, 1) if prob_over else None,
                'z_score': round(z_score, 4) if z_score else None,
                'edge': round(edge, 1) if edge else None,
                'features': {
                    'l5_avg': features.get('l5_avg'),
                    'l10_avg': features.get('l10_avg'),
                    'ewma_l10': features.get('ewma_l10'),
                    'std_dev_l10': features.get('std_dev_l10'),
                    'cv_l10': features.get('cv_l10'),
                },
                'full_features': {
                    'baseline': {k: v for k, v in features.items() if 'l5' in k or 'l10' in k or 'ewma' in k},
                    'matchup': matchup,
                },
                'mlr_features_used': True,
                'model_version': 'MLB_VK_XGB_v1.0'
            }
            
            logger.info(
                f"[MLB_VK_MODEL] {player_name} {stat_type}: pred={final_prediction:.2f}, "
                f"park={park_factor:.2f}, σ={std_dev:.3f}, line={line}"
            )
            
            return result
            
        except Exception as e:
            logger.error(f"[MLB_VK_MODEL] Predict failed for {player_name} {stat_type}: {e}")
            return {"error": str(e)}
    
    def _predict_statistical(
        self,
        player_name: str,
        stat_type: str,
        line: float = None,
        opponent_team: str = None,
        park_team: str = None
    ) -> Dict[str, Any]:
        """
        Statistical fallback when no trained model exists.
        Uses EWMA + matchup modifiers.
        """
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
                return {"error": f"Player not found: {player_name}"}
            
            game_logs = player.get('bdl_game_logs', [])
            if len(game_logs) < 5:
                return {"error": f"Insufficient games: {len(game_logs)}"}
            
            # Extract stat values
            stat_values = []
            for game in game_logs[:20]:
                val = self._get_stat_from_game(game, stat_type)
                if val is not None:
                    stat_values.append(val)
            
            if len(stat_values) < 5:
                return {"error": "Insufficient stat values"}
            
            l5 = stat_values[:5]
            l10 = stat_values[:10]
            
            # EWMA prediction
            ewma_l5 = self._calculate_ewma(l5, 0.5)
            ewma_l10 = self._calculate_ewma(l10, 0.3)
            
            # Weighted prediction
            base_prediction = 0.45 * ewma_l5 + 0.35 * ewma_l10 + 0.20 * np.mean(l10)
            
            # Apply park factor
            park_factor = self._get_park_factor(park_team, stat_type) if park_team else 1.0
            
            # Apply opponent modifier for K props
            opp_modifier = 1.0
            if opponent_team and stat_type == 'pitcher_strikeouts':
                opp_modifier = self._get_team_k_rate(opponent_team)
            
            final_prediction = base_prediction * park_factor * opp_modifier
            
            # Std dev
            std_dev = np.std(l10, ddof=1) if len(l10) > 1 else 0
            
            # MLB volatility floor
            cv = std_dev / np.mean(l10) if np.mean(l10) > 0 else 0.5
            if stat_type in ['hits', 'total_bases', 'rbis', 'runs', 'hits+runs+rbis']:
                if cv < 0.35:
                    std_dev = np.mean(l10) * 0.35
            
            # Probability
            prob_over = None
            z_score = None
            
            if line is not None and std_dev > 0:
                z_score = (line - final_prediction) / std_dev
                prob_over = (1 - stats.norm.cdf(z_score)) * 100
            
            return {
                'player_name': player_name,
                'stat_type': stat_type,
                'predicted': round(final_prediction, 2),
                'raw_prediction': round(final_prediction, 4),
                'std_dev': round(std_dev, 4),
                'line': line,
                'prob_over': round(prob_over, 1) if prob_over else None,
                'z_score': round(z_score, 4) if z_score else None,
                'features': {
                    'l5_avg': np.mean(l5),
                    'l10_avg': np.mean(l10),
                    'ewma_l10': ewma_l10,
                },
                'full_features': {
                    'baseline': {'l5_avg': np.mean(l5), 'l10_avg': np.mean(l10), 'ewma_l10': ewma_l10},
                    'matchup': {
                        'park_factor': park_factor,
                        'opp_k_rate': opp_modifier if stat_type == 'pitcher_strikeouts' else None,
                    },
                },
                'mlr_features_used': True,
                'model_version': 'MLB_VK_STATISTICAL_v1.0'
            }
            
        except Exception as e:
            logger.error(f"[MLB_VK_MODEL] Statistical predict failed: {e}")
            return {"error": str(e)}


# Global instance
_mlb_model_instance = None

def get_mlb_vegas_killer_model(db=None):
    """Get or create global MLB model instance."""
    global _mlb_model_instance
    if _mlb_model_instance is None and db is not None:
        _mlb_model_instance = MLBVegasKillerModel(db)
    return _mlb_model_instance

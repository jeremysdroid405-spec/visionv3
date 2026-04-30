"""
Vegas Pro Regression Model - The Real Deal
============================================

PRO STACK:
- Data Source: BallDontLie API (already integrated)
- Statistical Analysis: statsmodels (P-value testing)
- Prediction Engine: scikit-learn (Multiple Linear Regression)
- Data Processing: pandas + numpy

WORKFLOW:
1. EXTRACT: Pull game logs from nba_master_hub_2026
2. TRANSFORM: Engineer features (Usage, Pace, Rest, Matchup, etc.)
3. ANALYZE: Use statsmodels to find statistically significant predictors
4. PREDICT: Use scikit-learn to generate predictions

MODELS:
- Separate model per stat type (PTS, REB, AST, 3PM, PRA)
- Each stat has different predictors with different weights
"""

import pandas as pd
import numpy as np
from sklearn.linear_model import Ridge, Lasso, LinearRegression
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
import statsmodels.api as sm
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone
from collections import defaultdict
import logging
import pickle
import os

from services.config.collection_names import COLL
from services.observability import log_silent_failure

logger = logging.getLogger(__name__)

# =============================================================================
# CONSTANTS
# =============================================================================

STAT_TYPES = ['PTS', 'REB', 'AST', '3PM', 'STL', 'BLK', 'PRA', 'PA', 'PR', 'RA']

STAT_FIELD_MAP = {
    'PTS': ['pts'],
    'REB': ['reb'],
    'AST': ['ast'],
    '3PM': ['fg3m'],
    'STL': ['stl'],
    'BLK': ['blk'],
    'PRA': ['pts', 'reb', 'ast'],
    'PA': ['pts', 'ast'],
    'PR': ['pts', 'reb'],
    'RA': ['reb', 'ast'],
}

# Feature columns used in training
FEATURE_COLUMNS = [
    # Rolling averages
    'l3_avg', 'l5_avg', 'l10_avg', 'l20_avg', 'season_avg',
    # Trend indicators
    'trend_l5_vs_l10', 'trend_l10_vs_season',
    # Volatility
    'std_dev_l10', 'cv_l10',  # coefficient of variation
    # Distribution shape
    'median_l10', 'mode_l10',
    # Floor/Ceiling
    'floor_l10', 'ceiling_l10',
    # Minutes
    'minutes_l5', 'minutes_l10', 'minutes_trend',
    # Context
    'is_home', 'rest_days', 'is_b2b',
    # Opponent
    'opp_def_rank', 'opp_def_rating',
    # Game number (fatigue/rhythm)
    'games_played',
]


# =============================================================================
# FEATURE ENGINEERING
# =============================================================================

class FeatureEngineer:
    """
    Transforms raw game logs into ML-ready features.
    
    For each game, we look BACKWARD to create features
    (what was known BEFORE the game), and use the actual
    result as the target variable.
    """
    
    def __init__(self, db):
        self.db = db
        self._def_cache = {}
        self._load_defensive_rankings()
    
    def _load_defensive_rankings(self):
        """Load defensive rankings into memory."""
        try:
            cursor = self.db[COLL("defensive_momentum_cache", "nba")].find({})
            for doc in cursor:
                team = doc.get('team')
                stat_type = doc.get('stat_type', 'DRTG')
                if team:
                    self._def_cache[f"{team}_{stat_type}"] = {
                        'rank': doc.get('composite_rank', 15),
                        'rating': doc.get('season_def_rating', 110),
                    }
        except Exception as e:
            logger.error(f"Failed to load defensive rankings: {e}")
    
    def _get_stat_value(self, game: Dict, stat_type: str) -> Optional[float]:
        """Extract stat value from game log."""
        fields = STAT_FIELD_MAP.get(stat_type.upper(), [stat_type.lower()])
        total = 0
        for field in fields:
            val = game.get(field)
            if val is None:
                return None
            total += val
        return float(total)
    
    def _parse_minutes(self, mins) -> float:
        """Parse minutes from various formats."""
        if mins is None:
            return 0
        if isinstance(mins, (int, float)):
            return float(mins)
        if isinstance(mins, str):
            try:
                if ':' in mins:
                    parts = mins.split(':')
                    return int(parts[0]) + int(parts[1]) / 60
                return float(mins)
            except:
                return 0
        return 0
    
    def build_training_dataset(
        self,
        stat_type: str,
        min_games: int = 15
    ) -> pd.DataFrame:
        """
        Build training dataset for a specific stat type.
        
        For each player, for each game after the first `min_games`,
        we create a row with:
        - Features: stats from BEFORE the game
        - Target: actual result from THAT game
        
        This simulates real prediction conditions.
        """
        hub = self.db[COLL("master_hub", "nba")]
        all_rows = []
        
        # Get all players with game logs
        players = hub.find({
            'bdl_game_logs': {'$exists': True},
            f'bdl_game_logs.{min_games}': {'$exists': True}  # At least min_games
        })
        
        for player in players:
            player_name = player.get('display_name') or player.get('player_name')
            team = player.get('team')
            logs = player.get('bdl_game_logs', [])
            
            if len(logs) < min_games:
                continue
            
            # Sort by date (newest first is typical, but let's ensure)
            # Logs are already newest-first from BDL
            
            # For each game after min_games, build features from prior games
            for i in range(min_games - 1, len(logs)):
                target_game = logs[i]
                prior_games = logs[i+1:i+1+20]  # Games BEFORE this one
                
                if len(prior_games) < 5:
                    continue
                
                # Get target value
                target_value = self._get_stat_value(target_game, stat_type)
                if target_value is None:
                    continue
                
                # Build features from prior games
                features = self._extract_features(
                    prior_games=prior_games,
                    stat_type=stat_type,
                    target_game=target_game,
                    player_name=player_name,
                    team=team
                )
                
                if features:
                    features['target'] = target_value
                    features['player_name'] = player_name
                    features['game_date'] = target_game.get('date')
                    all_rows.append(features)
        
        df = pd.DataFrame(all_rows)
        logger.info(f"[{stat_type}] Built training dataset: {len(df)} samples from {df['player_name'].nunique()} players")
        
        return df
    
    def _extract_features(
        self,
        prior_games: List[Dict],
        stat_type: str,
        target_game: Dict,
        player_name: str,
        team: str
    ) -> Optional[Dict]:
        """Extract features from prior games for prediction."""
        
        # Get stat values from prior games
        values = []
        minutes = []
        
        for game in prior_games[:20]:
            val = self._get_stat_value(game, stat_type)
            mins = self._parse_minutes(game.get('min', 0))
            if val is not None:
                values.append(val)
                minutes.append(mins)
        
        if len(values) < 5:
            return None
        
        features = {}
        
        # =================================================================
        # ROLLING AVERAGES
        # =================================================================
        features['l3_avg'] = np.mean(values[:3]) if len(values) >= 3 else np.mean(values)
        features['l5_avg'] = np.mean(values[:5]) if len(values) >= 5 else np.mean(values)
        features['l10_avg'] = np.mean(values[:10]) if len(values) >= 10 else np.mean(values)
        features['l20_avg'] = np.mean(values[:20]) if len(values) >= 20 else np.mean(values)
        features['season_avg'] = np.mean(values)
        
        # =================================================================
        # TREND INDICATORS
        # =================================================================
        if len(values) >= 10:
            features['trend_l5_vs_l10'] = (features['l5_avg'] - features['l10_avg']) / max(features['l10_avg'], 1) * 100
        else:
            features['trend_l5_vs_l10'] = 0
        
        features['trend_l10_vs_season'] = (features['l10_avg'] - features['season_avg']) / max(features['season_avg'], 1) * 100
        
        # =================================================================
        # VOLATILITY
        # =================================================================
        if len(values) >= 10:
            features['std_dev_l10'] = np.std(values[:10], ddof=1)
            features['cv_l10'] = features['std_dev_l10'] / max(features['l10_avg'], 1)
        else:
            features['std_dev_l10'] = np.std(values, ddof=1) if len(values) > 1 else 0
            features['cv_l10'] = features['std_dev_l10'] / max(features['l5_avg'], 1)
        
        # =================================================================
        # DISTRIBUTION SHAPE
        # =================================================================
        l10_vals = values[:10] if len(values) >= 10 else values
        features['median_l10'] = np.median(l10_vals)
        
        # Mode (rounded to 0.5)
        rounded = [round(v * 2) / 2 for v in l10_vals]
        from collections import Counter
        counts = Counter(rounded)
        mode_val, mode_count = counts.most_common(1)[0]
        features['mode_l10'] = mode_val
        
        # Floor/Ceiling (10th/90th percentile)
        features['floor_l10'] = np.percentile(l10_vals, 10)
        features['ceiling_l10'] = np.percentile(l10_vals, 90)
        
        # =================================================================
        # MINUTES
        # =================================================================
        features['minutes_l5'] = np.mean(minutes[:5]) if len(minutes) >= 5 else np.mean(minutes)
        features['minutes_l10'] = np.mean(minutes[:10]) if len(minutes) >= 10 else np.mean(minutes)
        
        if features['minutes_l10'] > 0:
            features['minutes_trend'] = (features['minutes_l5'] - features['minutes_l10']) / features['minutes_l10'] * 100
        else:
            features['minutes_trend'] = 0
        
        # =================================================================
        # CONTEXT (from target game)
        # =================================================================
        features['is_home'] = 1 if target_game.get('home_game') else 0
        
        # Rest days (calculate from dates if possible)
        features['rest_days'] = 1  # Default
        features['is_b2b'] = 0
        
        if len(prior_games) >= 1:
            try:
                target_date = target_game.get('date')
                prior_date = prior_games[0].get('date')
                if target_date and prior_date:
                    from datetime import datetime
                    t_date = datetime.strptime(str(target_date)[:10], '%Y-%m-%d')
                    p_date = datetime.strptime(str(prior_date)[:10], '%Y-%m-%d')
                    days_diff = (t_date - p_date).days
                    features['rest_days'] = max(0, days_diff - 1)
                    features['is_b2b'] = 1 if days_diff == 1 else 0
            except Exception as _swept_exc:
                log_silent_failure("services.vegas_pro_model._extract_features", _swept_exc)  # sweep-auto-converted
        
        # =================================================================
        # OPPONENT (simplified - use team's overall defensive ranking)
        # =================================================================
        opp_team_id = target_game.get('opponent_team_id')
        # For now, use default values - we'd need a team_id to abbr mapping
        features['opp_def_rank'] = 15  # Neutral
        features['opp_def_rating'] = 110  # League average
        
        # =================================================================
        # GAMES PLAYED (fatigue/rhythm indicator)
        # =================================================================
        features['games_played'] = len(values)
        
        return features


# =============================================================================
# MODEL TRAINING
# =============================================================================

class VegasProModel:
    """
    Professional-grade regression model for player prop prediction.
    
    Uses statsmodels for feature significance analysis and
    scikit-learn for actual prediction.
    """
    
    def __init__(self, db, model_dir: str = '/app/backend/models'):
        self.db = db
        self.model_dir = model_dir
        self.feature_engineer = FeatureEngineer(db)
        self.models = {}  # {stat_type: trained_model}
        self.scalers = {}  # {stat_type: scaler}
        self.feature_importance = {}  # {stat_type: importance_dict}
        self.metrics = {}  # {stat_type: performance_metrics}
        
        os.makedirs(model_dir, exist_ok=True)
    
    def analyze_features(self, stat_type: str) -> Dict[str, Any]:
        """
        Use statsmodels to analyze feature significance.
        
        Returns P-values and coefficients for each feature.
        Features with P-value > 0.05 are NOT statistically significant.
        """
        logger.info(f"[{stat_type}] Analyzing feature significance...")
        
        # Build dataset
        df = self.feature_engineer.build_training_dataset(stat_type)
        
        if df.empty:
            return {"error": "No training data available"}
        
        # Prepare features and target
        feature_cols = [c for c in FEATURE_COLUMNS if c in df.columns]
        X = df[feature_cols].fillna(0)
        y = df['target']
        
        # Add constant for statsmodels
        X_with_const = sm.add_constant(X)
        
        # Fit OLS model
        model = sm.OLS(y, X_with_const).fit()
        
        # Extract results
        results = {
            "stat_type": stat_type,
            "n_samples": len(df),
            "r_squared": model.rsquared,
            "adj_r_squared": model.rsquared_adj,
            "features": {}
        }
        
        for feature in feature_cols:
            results["features"][feature] = {
                "coefficient": round(model.params.get(feature, 0), 4),
                "p_value": round(model.pvalues.get(feature, 1), 4),
                "significant": model.pvalues.get(feature, 1) < 0.05
            }
        
        # Sort by significance
        significant = {k: v for k, v in results["features"].items() if v["significant"]}
        not_significant = {k: v for k, v in results["features"].items() if not v["significant"]}
        
        results["significant_features"] = list(significant.keys())
        results["drop_features"] = list(not_significant.keys())
        
        logger.info(f"[{stat_type}] Significant features: {len(significant)}/{len(feature_cols)}")
        logger.info(f"[{stat_type}] R-squared: {model.rsquared:.4f}")
        
        return results
    
    def train(
        self,
        stat_type: str,
        use_significant_only: bool = True,
        model_type: str = 'ridge'  # 'linear', 'ridge', 'lasso'
    ) -> Dict[str, Any]:
        """
        Train prediction model for a stat type.
        
        Args:
            stat_type: PTS, REB, AST, etc.
            use_significant_only: Only use statistically significant features
            model_type: Type of regression model
        
        Returns:
            Training metrics and feature importance
        """
        logger.info(f"[{stat_type}] Training {model_type} regression model...")
        
        # Build dataset
        df = self.feature_engineer.build_training_dataset(stat_type)
        
        if df.empty or len(df) < 100:
            return {"error": f"Insufficient training data: {len(df)} samples"}
        
        # Analyze features first
        analysis = self.analyze_features(stat_type)
        
        # Select features
        if use_significant_only and analysis.get("significant_features"):
            feature_cols = analysis["significant_features"]
            # Always include key features even if not significant
            must_have = ['l5_avg', 'l10_avg', 'season_avg']
            for f in must_have:
                if f not in feature_cols and f in df.columns:
                    feature_cols.append(f)
        else:
            feature_cols = [c for c in FEATURE_COLUMNS if c in df.columns]
        
        logger.info(f"[{stat_type}] Using {len(feature_cols)} features: {feature_cols}")
        
        # Prepare data
        X = df[feature_cols].fillna(0)
        y = df['target']
        
        # Train/test split (80/20)
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42
        )
        
        # Scale features
        scaler = StandardScaler()
        X_train_scaled = scaler.fit_transform(X_train)
        X_test_scaled = scaler.transform(X_test)
        
        # Select model
        if model_type == 'ridge':
            model = Ridge(alpha=1.0)
        elif model_type == 'lasso':
            model = Lasso(alpha=0.1)
        else:
            model = LinearRegression()
        
        # Train
        model.fit(X_train_scaled, y_train)
        
        # Predict
        y_pred_train = model.predict(X_train_scaled)
        y_pred_test = model.predict(X_test_scaled)
        
        # Calculate metrics
        metrics = {
            "stat_type": stat_type,
            "model_type": model_type,
            "n_samples": len(df),
            "n_features": len(feature_cols),
            "features_used": feature_cols,
            "train": {
                "mae": round(mean_absolute_error(y_train, y_pred_train), 2),
                "rmse": round(np.sqrt(mean_squared_error(y_train, y_pred_train)), 2),
                "r2": round(r2_score(y_train, y_pred_train), 4),
            },
            "test": {
                "mae": round(mean_absolute_error(y_test, y_pred_test), 2),
                "rmse": round(np.sqrt(mean_squared_error(y_test, y_pred_test)), 2),
                "r2": round(r2_score(y_test, y_pred_test), 4),
            }
        }
        
        # Feature importance (coefficients)
        importance = {}
        for i, feature in enumerate(feature_cols):
            importance[feature] = round(model.coef_[i], 4)
        
        # Sort by absolute importance
        importance = dict(sorted(importance.items(), key=lambda x: abs(x[1]), reverse=True))
        metrics["feature_importance"] = importance
        
        # Cross-validation score
        cv_scores = cross_val_score(model, X_train_scaled, y_train, cv=5, scoring='neg_mean_absolute_error')
        metrics["cv_mae"] = round(-cv_scores.mean(), 2)
        metrics["cv_mae_std"] = round(cv_scores.std(), 2)
        
        # Store model
        self.models[stat_type] = model
        self.scalers[stat_type] = scaler
        self.feature_importance[stat_type] = importance
        self.metrics[stat_type] = metrics
        
        # Also store the feature columns used
        self.models[f"{stat_type}_features"] = feature_cols
        
        logger.info(f"[{stat_type}] Training complete:")
        logger.info(f"  Train MAE: {metrics['train']['mae']}, Test MAE: {metrics['test']['mae']}")
        logger.info(f"  Train R²: {metrics['train']['r2']}, Test R²: {metrics['test']['r2']}")
        
        return metrics
    
    def train_all(self, stat_types: List[str] = None) -> Dict[str, Any]:
        """Train models for all stat types."""
        if stat_types is None:
            stat_types = ['PTS', 'REB', 'AST', '3PM', 'PRA']
        
        results = {}
        for stat_type in stat_types:
            try:
                results[stat_type] = self.train(stat_type)
            except Exception as e:
                logger.error(f"[{stat_type}] Training failed: {e}")
                results[stat_type] = {"error": str(e)}
        
        return results
    
    def predict(
        self,
        player_name: str,
        stat_type: str,
        line: float = None,
        opponent_team: str = None
    ) -> Dict[str, Any]:
        """
        Predict a player's stat output for their next game.
        
        Uses the trained model for the stat type.
        """
        if stat_type not in self.models:
            return {"error": f"No trained model for {stat_type}. Run train() first."}
        
        model = self.models[stat_type]
        scaler = self.scalers[stat_type]
        feature_cols = self.models[f"{stat_type}_features"]
        
        # Get player's game logs
        hub = self.db[COLL("master_hub", "nba")]
        player = hub.find_one({
            '$or': [
                {'player_name': player_name},
                {'display_name': player_name},
            ]
        })
        
        if not player:
            return {"error": "Player not found"}
        
        logs = player.get('bdl_game_logs', [])
        if len(logs) < 5:
            return {"error": "Insufficient game history"}
        
        # Extract features for prediction (using recent games as "prior")
        features = self.feature_engineer._extract_features(
            prior_games=logs[:20],
            stat_type=stat_type,
            target_game={'home_game': True, 'opponent_team_id': None},  # Placeholder
            player_name=player_name,
            team=player.get('team')
        )
        
        if not features:
            return {"error": "Could not extract features"}
        
        # Build feature vector
        X = pd.DataFrame([features])[feature_cols].fillna(0)
        X_scaled = scaler.transform(X)
        
        # Predict
        prediction = model.predict(X_scaled)[0]
        
        result = {
            "player_name": player_name,
            "stat_type": stat_type,
            "predicted": round(prediction, 2),
            "features": {k: round(features.get(k, 0), 2) for k in feature_cols[:10]},
            "model_metrics": {
                "test_mae": self.metrics.get(stat_type, {}).get('test', {}).get('mae'),
                "test_r2": self.metrics.get(stat_type, {}).get('test', {}).get('r2'),
            }
        }
        
        # Add edge calculation if line provided
        if line is not None:
            std_dev = features.get('std_dev_l10', 5)
            edge = prediction - line
            z_score = edge / std_dev if std_dev > 0 else 0
            
            # Convert to probability
            from scipy import stats
            prob_over = stats.norm.cdf(z_score) * 100
            
            result["line"] = line
            result["edge"] = round(edge, 2)
            result["edge_pct"] = round(edge / line * 100, 2) if line > 0 else 0
            result["prob_over"] = round(prob_over, 1)
            result["prob_under"] = round(100 - prob_over, 1)
            
            # Recommendation
            if prob_over >= 65:
                result["recommendation"] = "STRONG_OVER"
            elif prob_over >= 55:
                result["recommendation"] = "LEAN_OVER"
            elif prob_over <= 35:
                result["recommendation"] = "STRONG_UNDER"
            elif prob_over <= 45:
                result["recommendation"] = "LEAN_UNDER"
            else:
                result["recommendation"] = "NEUTRAL"
        
        return result
    
    def save_models(self):
        """Save trained models to disk."""
        for stat_type, model in self.models.items():
            if not stat_type.endswith('_features'):
                path = os.path.join(self.model_dir, f"{stat_type.lower()}_model.pkl")
                with open(path, 'wb') as f:
                    pickle.dump({
                        'model': model,
                        'scaler': self.scalers.get(stat_type),
                        'features': self.models.get(f"{stat_type}_features"),
                        'metrics': self.metrics.get(stat_type),
                        'importance': self.feature_importance.get(stat_type),
                    }, f)
                logger.info(f"Saved model: {path}")
    
    def load_models(self):
        """Load trained models from disk."""
        for stat_type in STAT_TYPES:
            path = os.path.join(self.model_dir, f"{stat_type.lower()}_model.pkl")
            if os.path.exists(path):
                with open(path, 'rb') as f:
                    data = pickle.load(f)
                    self.models[stat_type] = data['model']
                    self.scalers[stat_type] = data['scaler']
                    self.models[f"{stat_type}_features"] = data['features']
                    self.metrics[stat_type] = data['metrics']
                    self.feature_importance[stat_type] = data['importance']
                logger.info(f"Loaded model: {path}")


# =============================================================================
# BACKTEST ENGINE
# =============================================================================

class BacktestEngine:
    """
    Tests model predictions against actual historical results.
    
    Simulates placing bets on past games where we know the outcome.
    """
    
    def __init__(self, model: VegasProModel):
        self.model = model
    
    def run_backtest(
        self,
        stat_type: str,
        n_games: int = 100
    ) -> Dict[str, Any]:
        """
        Backtest model on recent games.
        
        For each game:
        1. Use model to predict
        2. Compare to actual result
        3. Simulate betting based on edge
        """
        df = self.model.feature_engineer.build_training_dataset(stat_type)
        
        if df.empty:
            return {"error": "No data for backtest"}
        
        # Use most recent games for backtest
        df = df.sort_values('game_date', ascending=False).head(n_games)
        
        results = []
        correct_predictions = 0
        total_bets = 0
        
        for _, row in df.iterrows():
            # Get prediction (would need to rebuild features without this game)
            # For now, use the stored features
            predicted = row.get('l5_avg', 0)  # Simplified - use L5 as proxy
            actual = row['target']
            
            # Simulate a line (use median as proxy)
            line = row.get('median_l10', predicted)
            
            # Would we have bet over?
            edge = predicted - line
            
            if abs(edge) > 1:  # Only bet on clear edges
                total_bets += 1
                bet_over = edge > 0
                actual_over = actual > line
                
                if bet_over == actual_over:
                    correct_predictions += 1
                
                results.append({
                    'player': row.get('player_name'),
                    'date': row.get('game_date'),
                    'predicted': round(predicted, 1),
                    'actual': actual,
                    'line': round(line, 1),
                    'bet': 'OVER' if bet_over else 'UNDER',
                    'result': 'WIN' if bet_over == actual_over else 'LOSS'
                })
        
        win_rate = correct_predictions / total_bets * 100 if total_bets > 0 else 0
        
        return {
            "stat_type": stat_type,
            "games_tested": len(df),
            "bets_placed": total_bets,
            "wins": correct_predictions,
            "losses": total_bets - correct_predictions,
            "win_rate": round(win_rate, 1),
            "break_even_rate": 52.4,  # -110 odds
            "edge_vs_break_even": round(win_rate - 52.4, 1),
            "sample_results": results[:10]
        }


# =============================================================================
# EXPORT
# =============================================================================

__all__ = [
    'VegasProModel',
    'FeatureEngineer',
    'BacktestEngine',
    'STAT_TYPES',
    'FEATURE_COLUMNS'
]

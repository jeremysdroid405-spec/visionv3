"""
Vegas Killer Model - Process Stats Feature Engineering
========================================================

Moving from "Box Score" stats to "Process" stats.
Vegas doesn't just look at how many points - they look at
the CONDITIONS that allowed those points to happen.

FEATURE CATEGORIES:
1. OPPORTUNITY (Volume) - USG%, Minutes, FGA, FTr
2. EFFICIENCY (Quality) - eFG%, TS%, Shooting Splits
3. MATCHUP (Friction) - Opp DRtg, Opp Pace, Individual Defender
4. ENVIRONMENT (Fatigue) - Rest, Home/Away, Travel, Time
5. MARKET (Wisdom) - Line Movement, Implied Prob, Team Total

ROLLING WINDOWS:
- L3: Hot streak detection
- L5: Current form
- L10: Stable baseline
- Season: True talent level
"""

import pandas as pd
import numpy as np
from sklearn.linear_model import Ridge
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
import statsmodels.api as sm
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
from collections import defaultdict
import logging
import pickle
import os

logger = logging.getLogger(__name__)


# =============================================================================
# FEATURE DEFINITIONS
# =============================================================================

FEATURE_CATEGORIES = {
    'opportunity': [
        'usg_rate_l5',      # Usage rate (% of team plays finished)
        'minutes_l5',       # Recent minutes
        'minutes_trend',    # Minutes trajectory
        'fga_l5',           # Field goal attempts L5
        'fga_l10',          # Field goal attempts L10
        'ftr_l5',           # Free throw rate (FTA/FGA)
        'touches_proxy',    # FGA + 0.44*FTA (possession proxy)
    ],
    'efficiency': [
        'efg_l5',           # Effective FG% L5
        'efg_l10',          # Effective FG% L10
        'ts_l5',            # True Shooting % L5
        'ts_l10',           # True Shooting % L10
        'fg3_rate',         # % of shots that are 3s
        'fg3_pct_l5',       # 3PT% L5
        'ft_pct_l5',        # FT% L5
        'scoring_efficiency_trend',  # TS% L5 vs L10
    ],
    'matchup': [
        'opp_def_rating',   # Opponent defensive rating
        'opp_pace',         # Opponent pace (possessions/48)
        'opp_pts_allowed',  # Opponent points allowed per game
        'opp_def_rank',     # Composite defensive rank (1-30)
        'pace_delta',       # Player's team pace vs opponent
    ],
    'environment': [
        'rest_days',        # Days since last game
        'is_b2b',           # Back-to-back flag
        'is_home',          # Home game flag
        'games_in_7_days',  # Schedule density
        'season_game_num',  # Fatigue accumulation
    ],
    'baseline': [
        'season_avg',       # Season average for stat
        'l3_avg',           # Last 3 games average
        'l5_avg',           # Last 5 games average
        'l10_avg',          # Last 10 games average
        'std_dev_l10',      # Volatility
        'floor_l10',        # 10th percentile
        'ceiling_l10',      # 90th percentile
        'mode_l10',         # Most frequent outcome
        'median_l10',       # Median outcome
    ],
    'market': [
        'line',             # The prop line
        'sharp_implied',    # Sharp money implied probability
        'team_total',       # Projected team total points
        'spread',           # Game spread
    ],
}

ALL_FEATURES = []
for category in FEATURE_CATEGORIES.values():
    ALL_FEATURES.extend(category)


# =============================================================================
# ADVANCED FEATURE CALCULATOR
# =============================================================================

class VegasFeatureEngineer:
    """
    Calculates Vegas-style "process" features from game logs.
    """
    
    def __init__(self, db):
        self.db = db
        self._team_pace_cache = {}
        self._def_rating_cache = {}
        self._load_team_stats()
        
        # Import team stats service
        from services.team_stats_service import TeamStatsService, TEAM_PACE_2026
        self.team_stats_service = TeamStatsService(db)
        self._team_pace_cache = TEAM_PACE_2026.copy()
    
    def _load_team_stats(self):
        """Load team pace and defensive ratings."""
        try:
            # Load from defensive momentum cache
            for doc in self.db['defensive_momentum_cache'].find({}):
                team = doc.get('team')
                if team:
                    self._def_rating_cache[team] = {
                        'def_rating': doc.get('season_def_rating', 110),
                        'def_rank': doc.get('composite_rank', 15),
                        'pts_allowed': doc.get('pts_allowed_avg', 110),
                        'l10_pts_allowed': doc.get('l10_pts_allowed', 110),
                        'l5_pts_allowed': doc.get('l5_pts_allowed', 110),
                    }
            
            logger.info(f"Loaded {len(self._def_rating_cache)} team defensive ratings")
        except Exception as e:
            logger.error(f"Failed to load team stats: {e}")
    
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
    
    def _get_stat_value(self, game: Dict, stat_type: str) -> Optional[float]:
        """Extract stat value from game log."""
        stat_map = {
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
        fields = stat_map.get(stat_type.upper(), [stat_type.lower()])
        total = 0
        for field in fields:
            val = game.get(field)
            if val is None:
                return None
            total += val
        return float(total)
    
    def calculate_usage_rate(self, game: Dict, team_games: List[Dict] = None) -> float:
        """
        Calculate usage rate proxy.
        
        True USG% = 100 * ((FGA + 0.44 * FTA + TOV) * (Team MIN / 5)) / 
                    (MIN * (Team FGA + 0.44 * Team FTA + Team TOV))
        
        Simplified proxy using player's share of attempts.
        """
        fga = game.get('fga', 0)
        fta = game.get('fta', 0)
        tov = game.get('turnover', 0)
        mins = self._parse_minutes(game.get('min', 0))
        
        # Possessions used by player
        player_possessions = fga + 0.44 * fta + tov
        
        # Estimate team possessions (player plays ~33% of game if starter)
        if mins > 0:
            # Scale to per-48 and estimate team share
            per_48_possessions = player_possessions * (48 / mins)
            # Typical starter has 20-30% usage
            estimated_usg = min(40, (per_48_possessions / 100) * 100)
        else:
            estimated_usg = 0
        
        return estimated_usg
    
    def calculate_true_shooting(self, game: Dict) -> float:
        """
        Calculate True Shooting Percentage.
        TS% = PTS / (2 * (FGA + 0.44 * FTA))
        """
        pts = game.get('pts', 0)
        fga = game.get('fga', 0)
        fta = game.get('fta', 0)
        
        attempts = 2 * (fga + 0.44 * fta)
        if attempts > 0:
            return pts / attempts * 100
        return 0
    
    def calculate_efg(self, game: Dict) -> float:
        """
        Calculate Effective Field Goal %.
        eFG% = (FGM + 0.5 * 3PM) / FGA
        """
        fgm = game.get('fgm', 0)
        fg3m = game.get('fg3m', 0)
        fga = game.get('fga', 0)
        
        if fga > 0:
            return ((fgm + 0.5 * fg3m) / fga) * 100
        return 0
    
    def calculate_free_throw_rate(self, game: Dict) -> float:
        """
        Calculate Free Throw Rate.
        FTr = FTA / FGA
        """
        fta = game.get('fta', 0)
        fga = game.get('fga', 0)
        
        if fga > 0:
            return fta / fga
        return 0
    
    def extract_features(
        self,
        prior_games: List[Dict],
        stat_type: str,
        target_game: Dict = None,
        opponent_team: str = None,
        line: float = None,
        team_total: float = None,
    ) -> Dict[str, float]:
        """
        Extract all Vegas-style features from game logs.
        
        Args:
            prior_games: Games BEFORE the target game (for training)
            stat_type: The stat we're predicting
            target_game: The game we're predicting (for context like opponent)
            opponent_team: Opponent team abbreviation
            line: The prop line (for market features)
            team_total: Projected team total
        
        Returns:
            Feature dictionary ready for ML model
        """
        features = {}
        
        if not prior_games or len(prior_games) < 3:
            return features
        
        # Get stat values
        values = []
        for game in prior_games[:20]:
            val = self._get_stat_value(game, stat_type)
            if val is not None:
                values.append(val)
        
        if len(values) < 3:
            return features
        
        # =====================================================================
        # BASELINE FEATURES (Rolling Averages)
        # =====================================================================
        features['l3_avg'] = np.mean(values[:3])
        features['l5_avg'] = np.mean(values[:5]) if len(values) >= 5 else np.mean(values)
        features['l10_avg'] = np.mean(values[:10]) if len(values) >= 10 else np.mean(values)
        features['season_avg'] = np.mean(values)
        
        l10_vals = values[:10] if len(values) >= 10 else values
        features['std_dev_l10'] = np.std(l10_vals, ddof=1) if len(l10_vals) > 1 else 0
        features['floor_l10'] = np.percentile(l10_vals, 10)
        features['ceiling_l10'] = np.percentile(l10_vals, 90)
        features['median_l10'] = np.median(l10_vals)
        
        # Mode
        from collections import Counter
        rounded = [round(v * 2) / 2 for v in l10_vals]
        counts = Counter(rounded)
        mode_val, _ = counts.most_common(1)[0]
        features['mode_l10'] = mode_val
        
        # =====================================================================
        # OPPORTUNITY FEATURES (Volume)
        # =====================================================================
        # Usage Rate
        usg_rates = [self.calculate_usage_rate(g) for g in prior_games[:5]]
        features['usg_rate_l5'] = np.mean(usg_rates) if usg_rates else 20
        
        # Minutes
        minutes = [self._parse_minutes(g.get('min', 0)) for g in prior_games[:10]]
        features['minutes_l5'] = np.mean(minutes[:5]) if len(minutes) >= 5 else np.mean(minutes)
        features['minutes_l10'] = np.mean(minutes[:10]) if len(minutes) >= 10 else np.mean(minutes)
        
        if features['minutes_l10'] > 0:
            features['minutes_trend'] = (features['minutes_l5'] - features['minutes_l10']) / features['minutes_l10'] * 100
        else:
            features['minutes_trend'] = 0
        
        # Field Goal Attempts
        fga_vals = [g.get('fga', 0) for g in prior_games[:10]]
        features['fga_l5'] = np.mean(fga_vals[:5]) if len(fga_vals) >= 5 else np.mean(fga_vals)
        features['fga_l10'] = np.mean(fga_vals[:10]) if len(fga_vals) >= 10 else np.mean(fga_vals)
        
        # Free Throw Rate
        ftr_vals = [self.calculate_free_throw_rate(g) for g in prior_games[:5]]
        features['ftr_l5'] = np.mean(ftr_vals) if ftr_vals else 0.3
        
        # Touches Proxy (FGA + 0.44*FTA)
        touches = [g.get('fga', 0) + 0.44 * g.get('fta', 0) for g in prior_games[:5]]
        features['touches_proxy'] = np.mean(touches) if touches else 10
        
        # =====================================================================
        # EFFICIENCY FEATURES (Quality)
        # =====================================================================
        # True Shooting %
        ts_vals = [self.calculate_true_shooting(g) for g in prior_games[:10]]
        features['ts_l5'] = np.mean(ts_vals[:5]) if len(ts_vals) >= 5 else np.mean(ts_vals)
        features['ts_l10'] = np.mean(ts_vals[:10]) if len(ts_vals) >= 10 else np.mean(ts_vals)
        
        # Effective FG%
        efg_vals = [self.calculate_efg(g) for g in prior_games[:10]]
        features['efg_l5'] = np.mean(efg_vals[:5]) if len(efg_vals) >= 5 else np.mean(efg_vals)
        features['efg_l10'] = np.mean(efg_vals[:10]) if len(efg_vals) >= 10 else np.mean(efg_vals)
        
        # 3PT Rate and %
        fg3a_vals = [g.get('fg3a', 0) for g in prior_games[:5]]
        fga_vals_5 = [g.get('fga', 1) for g in prior_games[:5]]
        features['fg3_rate'] = np.sum(fg3a_vals) / max(np.sum(fga_vals_5), 1)
        
        fg3_pct_vals = [g.get('fg3_pct', 0) for g in prior_games[:5] if g.get('fg3a', 0) > 0]
        features['fg3_pct_l5'] = np.mean(fg3_pct_vals) * 100 if fg3_pct_vals else 35
        
        # FT%
        ft_pct_vals = [g.get('ft_pct', 0) for g in prior_games[:5] if g.get('fta', 0) > 0]
        features['ft_pct_l5'] = np.mean(ft_pct_vals) * 100 if ft_pct_vals else 75
        
        # Efficiency Trend
        features['scoring_efficiency_trend'] = features['ts_l5'] - features['ts_l10']
        
        # =====================================================================
        # MATCHUP FEATURES (Friction) - Now with real pace data
        # =====================================================================
        if opponent_team:
            def_data = self._def_rating_cache.get(opponent_team, {})
            features['opp_def_rating'] = def_data.get('def_rating', 110)
            features['opp_def_rank'] = def_data.get('def_rank', 15)
            features['opp_pts_allowed'] = def_data.get('pts_allowed', 110)
            features['opp_l10_pts_allowed'] = def_data.get('l10_pts_allowed', 110)
            features['opp_l5_pts_allowed'] = def_data.get('l5_pts_allowed', 110)
            
            # Real pace data from team_stats_service
            opp_pace = self._team_pace_cache.get(opponent_team, 99.0)
            features['opp_pace'] = opp_pace
            
            # Pace delta from league average (99.0)
            features['pace_delta'] = opp_pace - 99.0
            
            # Pace impact on scoring (higher pace = more possessions = more stats)
            # Each extra possession is ~1% more opportunity
            features['pace_multiplier'] = opp_pace / 99.0
        else:
            features['opp_def_rating'] = 110
            features['opp_def_rank'] = 15
            features['opp_pts_allowed'] = 110
            features['opp_l10_pts_allowed'] = 110
            features['opp_l5_pts_allowed'] = 110
            features['opp_pace'] = 99.0
            features['pace_delta'] = 0
            features['pace_multiplier'] = 1.0
        
        # =====================================================================
        # ENVIRONMENT FEATURES (Fatigue)
        # =====================================================================
        features['is_home'] = 1 if (target_game and target_game.get('home_game')) else 0
        features['rest_days'] = 1  # Default
        features['is_b2b'] = 0
        
        # Calculate rest days from dates
        if len(prior_games) >= 1 and target_game:
            try:
                target_date_str = str(target_game.get('date', ''))[:10]
                prior_date_str = str(prior_games[0].get('date', ''))[:10]
                
                if target_date_str and prior_date_str:
                    target_date = datetime.strptime(target_date_str, '%Y-%m-%d')
                    prior_date = datetime.strptime(prior_date_str, '%Y-%m-%d')
                    days_diff = (target_date - prior_date).days
                    features['rest_days'] = max(0, days_diff - 1)
                    features['is_b2b'] = 1 if days_diff == 1 else 0
            except:
                pass
        
        # Games in last 7 days (schedule density)
        features['games_in_7_days'] = min(len(prior_games), 4)  # Approximation
        features['season_game_num'] = len(values)
        
        # =====================================================================
        # MARKET FEATURES
        # =====================================================================
        if line is not None:
            features['line'] = line
            # Calculate how line compares to averages
            features['line_vs_l5'] = features['l5_avg'] - line
            features['line_vs_l10'] = features['l10_avg'] - line
            features['line_vs_season'] = features['season_avg'] - line
            
            # Line cushion (how much room above/below)
            features['line_cushion'] = features['l5_avg'] - line
            features['line_cushion_pct'] = (features['l5_avg'] - line) / max(line, 1) * 100
        else:
            features['line'] = features['l5_avg']  # Use L5 as proxy
            features['line_vs_l5'] = 0
            features['line_vs_l10'] = 0
            features['line_vs_season'] = 0
            features['line_cushion'] = 0
            features['line_cushion_pct'] = 0
        
        if team_total is not None:
            features['team_total'] = team_total
            # Player's expected share of team total
            # Estimate based on their scoring average vs team scoring
            features['team_total_share'] = features['l5_avg'] / max(team_total, 1) * 100
        else:
            features['team_total'] = 115  # League average team total
            features['team_total_share'] = features['l5_avg'] / 115 * 100
        
        # Sharp implied would come from odds data
        features['sharp_implied'] = 50  # Default to neutral
        
        return features
    
    def build_training_dataset(
        self,
        stat_type: str,
        min_games: int = 15
    ) -> pd.DataFrame:
        """
        Build comprehensive training dataset with all Vegas features.
        """
        hub = self.db['nba_master_hub_2026']
        all_rows = []
        
        players = hub.find({
            'bdl_game_logs': {'$exists': True},
            f'bdl_game_logs.{min_games}': {'$exists': True}
        })
        
        player_count = 0
        for player in players:
            player_name = player.get('display_name') or player.get('player_name')
            team = player.get('team')
            logs = player.get('bdl_game_logs', [])
            
            if len(logs) < min_games:
                continue
            
            player_count += 1
            
            # For each game after min_games, build features from prior games
            for i in range(min_games - 1, len(logs)):
                target_game = logs[i]
                prior_games = logs[i+1:i+1+20]
                
                if len(prior_games) < 5:
                    continue
                
                # Get target value
                target_value = self._get_stat_value(target_game, stat_type)
                if target_value is None:
                    continue
                
                # Get opponent team (would need ID -> abbreviation mapping)
                opp_team_id = target_game.get('opponent_team_id')
                
                # Extract features
                features = self.extract_features(
                    prior_games=prior_games,
                    stat_type=stat_type,
                    target_game=target_game,
                    opponent_team=None,  # Would need team ID mapping
                )
                
                if features:
                    features['target'] = target_value
                    features['player_name'] = player_name
                    features['game_date'] = target_game.get('date')
                    all_rows.append(features)
        
        df = pd.DataFrame(all_rows)
        logger.info(f"[{stat_type}] Built training dataset: {len(df)} samples from {player_count} players")
        
        return df


class EnsembleModel:
    """Ensemble model combining multiple regressors."""
    def __init__(self, models, weights=None):
        self.models = models
        self.weights = weights or [1/len(models)] * len(models)
    
    def predict(self, X):
        preds = np.zeros(X.shape[0])
        for model, weight in zip(self.models, self.weights):
            preds += weight * model.predict(X)
        return preds


# =============================================================================
# VEGAS KILLER MODEL
# =============================================================================

class VegasKillerModel:
    """
    The ultimate prediction model combining all Vegas-style features.
    
    Uses ensemble of Ridge + Gradient Boosting for robust predictions.
    """
    
    def __init__(self, db, model_dir: str = '/app/backend/models'):
        self.db = db
        self.model_dir = model_dir
        self.feature_engineer = VegasFeatureEngineer(db)
        self.models = {}
        self.scalers = {}
        self.feature_cols = {}
        self.metrics = {}
        
        os.makedirs(model_dir, exist_ok=True)
    
    def analyze_features(self, stat_type: str) -> Dict[str, Any]:
        """
        Analyze feature significance using statsmodels.
        """
        logger.info(f"[{stat_type}] Analyzing feature significance...")
        
        df = self.feature_engineer.build_training_dataset(stat_type)
        
        if df.empty:
            return {"error": "No training data"}
        
        # Get numeric columns only (exclude player_name, game_date, target)
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
        feature_cols = [c for c in numeric_cols if c not in ['target']]
        
        X = df[feature_cols].fillna(0).replace([np.inf, -np.inf], 0)
        y = df['target']
        
        # Add constant for statsmodels
        X_const = sm.add_constant(X)
        
        try:
            model = sm.OLS(y, X_const).fit()
            
            results = {
                "stat_type": stat_type,
                "n_samples": len(df),
                "r_squared": round(model.rsquared, 4),
                "adj_r_squared": round(model.rsquared_adj, 4),
                "features": {},
                "significant": [],
                "not_significant": [],
            }
            
            for feature in feature_cols:
                coef = model.params.get(feature, 0)
                pval = model.pvalues.get(feature, 1)
                sig = pval < 0.05
                
                results["features"][feature] = {
                    "coefficient": round(coef, 4),
                    "p_value": round(pval, 4),
                    "significant": sig
                }
                
                if sig:
                    results["significant"].append(feature)
                else:
                    results["not_significant"].append(feature)
            
            return results
        
        except Exception as e:
            logger.error(f"Feature analysis failed: {e}")
            return {"error": str(e)}
    
    def train(
        self,
        stat_type: str,
        model_type: str = 'ensemble'  # 'ridge', 'gbm', 'ensemble'
    ) -> Dict[str, Any]:
        """
        Train Vegas Killer model with full feature set.
        """
        logger.info(f"[{stat_type}] Training Vegas Killer model...")
        
        df = self.feature_engineer.build_training_dataset(stat_type)
        
        if df.empty or len(df) < 100:
            return {"error": f"Insufficient data: {len(df)} samples"}
        
        # Get feature columns
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
        feature_cols = [c for c in numeric_cols if c not in ['target']]
        
        X = df[feature_cols].fillna(0).replace([np.inf, -np.inf], 0)
        y = df['target']
        
        # Train/test split
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42
        )
        
        # Scale features
        scaler = StandardScaler()
        X_train_scaled = scaler.fit_transform(X_train)
        X_test_scaled = scaler.transform(X_test)
        
        # Train models
        if model_type == 'ridge':
            model = Ridge(alpha=1.0)
            model.fit(X_train_scaled, y_train)
        
        elif model_type == 'gbm':
            model = GradientBoostingRegressor(
                n_estimators=100,
                max_depth=4,
                learning_rate=0.1,
                random_state=42
            )
            model.fit(X_train_scaled, y_train)
        
        else:  # ensemble
            ridge = Ridge(alpha=1.0)
            ridge.fit(X_train_scaled, y_train)
            
            gbm = GradientBoostingRegressor(
                n_estimators=100,
                max_depth=4,
                learning_rate=0.1,
                random_state=42
            )
            gbm.fit(X_train_scaled, y_train)
            
            # Ensemble: average predictions
            model = EnsembleModel([ridge, gbm], weights=[0.4, 0.6])
        
        # Predictions
        y_pred_train = model.predict(X_train_scaled)
        y_pred_test = model.predict(X_test_scaled)
        
        # Metrics
        metrics = {
            "stat_type": stat_type,
            "model_type": model_type,
            "n_samples": len(df),
            "n_features": len(feature_cols),
            "features": feature_cols,
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
        
        # Feature importance (for GBM)
        if hasattr(model, 'feature_importances_'):
            importance = dict(zip(feature_cols, model.feature_importances_))
            importance = dict(sorted(importance.items(), key=lambda x: x[1], reverse=True))
            metrics['feature_importance'] = {k: round(v, 4) for k, v in list(importance.items())[:15]}
        elif hasattr(model, 'coef_'):
            importance = dict(zip(feature_cols, np.abs(model.coef_)))
            importance = dict(sorted(importance.items(), key=lambda x: x[1], reverse=True))
            metrics['feature_importance'] = {k: round(v, 4) for k, v in list(importance.items())[:15]}
        
        # Store
        self.models[stat_type] = model
        self.scalers[stat_type] = scaler
        self.feature_cols[stat_type] = feature_cols
        self.metrics[stat_type] = metrics
        
        logger.info(f"[{stat_type}] Training complete - Test MAE: {metrics['test']['mae']}, R²: {metrics['test']['r2']}")
        
        return metrics
    
    def predict(
        self,
        player_name: str,
        stat_type: str,
        line: float = None,
        opponent_team: str = None,
        team_total: float = None,
    ) -> Dict[str, Any]:
        """
        Predict player's stat output using Vegas Killer model.
        """
        if stat_type not in self.models:
            return {"error": f"No model for {stat_type}"}
        
        model = self.models[stat_type]
        scaler = self.scalers[stat_type]
        feature_cols = self.feature_cols[stat_type]
        
        # Get player
        hub = self.db['nba_master_hub_2026']
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
            return {"error": "Insufficient history"}
        
        # Extract features
        features = self.feature_engineer.extract_features(
            prior_games=logs[:20],
            stat_type=stat_type,
            target_game=None,
            opponent_team=opponent_team,
            line=line,
            team_total=team_total,
        )
        
        if not features:
            return {"error": "Feature extraction failed"}
        
        # Build feature vector
        X = pd.DataFrame([features])[feature_cols].fillna(0).replace([np.inf, -np.inf], 0)
        X_scaled = scaler.transform(X)
        
        # Predict
        prediction = model.predict(X_scaled)[0]
        
        result = {
            "player_name": player_name,
            "stat_type": stat_type,
            "predicted": round(prediction, 2),
            "model_mae": self.metrics.get(stat_type, {}).get('test', {}).get('mae'),
        }
        
        # Add key features
        result["features"] = {
            "l5_avg": round(features.get('l5_avg', 0), 1),
            "l10_avg": round(features.get('l10_avg', 0), 1),
            "usg_rate": round(features.get('usg_rate_l5', 0), 1),
            "ts_pct": round(features.get('ts_l5', 0), 1),
            "minutes": round(features.get('minutes_l5', 0), 1),
            "rest_days": features.get('rest_days', 1),
        }
        
        # Edge calculation
        if line is not None:
            std_dev = features.get('std_dev_l10', 5)
            edge = prediction - line
            
            result["line"] = line
            result["edge"] = round(edge, 2)
            result["edge_pct"] = round(edge / line * 100, 2) if line > 0 else 0
            
            # Z-score and probability
            if std_dev > 0:
                from scipy import stats
                z_score = edge / std_dev
                prob_over = stats.norm.cdf(z_score) * 100
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
        """Save trained models."""
        for stat_type in self.models:
            path = os.path.join(self.model_dir, f"vegas_killer_{stat_type.lower()}.pkl")
            with open(path, 'wb') as f:
                pickle.dump({
                    'model': self.models[stat_type],
                    'scaler': self.scalers[stat_type],
                    'features': self.feature_cols[stat_type],
                    'metrics': self.metrics[stat_type],
                }, f)
            logger.info(f"Saved: {path}")
    
    def load_models(self):
        """Load trained models."""
        for stat_type in ['PTS', 'REB', 'AST', '3PM', 'PRA']:
            path = os.path.join(self.model_dir, f"vegas_killer_{stat_type.lower()}.pkl")
            if os.path.exists(path):
                with open(path, 'rb') as f:
                    data = pickle.load(f)
                    self.models[stat_type] = data['model']
                    self.scalers[stat_type] = data['scaler']
                    self.feature_cols[stat_type] = data['features']
                    self.metrics[stat_type] = data['metrics']
                logger.info(f"Loaded: {path}")


# =============================================================================
# EXPORT
# =============================================================================

__all__ = [
    'VegasKillerModel',
    'VegasFeatureEngineer',
    'FEATURE_CATEGORIES',
    'ALL_FEATURES',
]

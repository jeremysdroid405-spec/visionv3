"""
Vegas Killer Model V2 - Enhanced Process Stats
================================================

IMPROVEMENTS IMPLEMENTED:
1. RECENCY WEIGHTING (EWMA) - Exponentially weighted moving averages
   - Recent games decay older games mathematically
   - Catches hot streaks and role changes instantly
   
2. FRICTION FEATURES - Game difficulty factors
   - Pace-adjusted stats (per 100 possessions)
   - Back-to-back penalty (rest factor)
   - Opponent matchup difficulty (defense ranking, shot profile)
   
3. GRADIENT BOOSTING - Non-linear decision trees
   - Handles "IF/AND" logic that linear models can't
   - XGBoost for superior accuracy
   
4. FEATURE SELECTION - P-value based noise reduction
   - Uses statsmodels to identify significant features
   - Drops noisy variables that cause overfitting

FEATURE CATEGORIES:
1. OPPORTUNITY (Volume) - USG%, Minutes, FGA, FTr
2. EFFICIENCY (Quality) - eFG%, TS%, Shooting Splits
3. MATCHUP (Friction) - Opp DRtg, Opp Pace, Individual Defender
4. ENVIRONMENT (Fatigue) - Rest, Home/Away, Travel, Time
5. MARKET (Wisdom) - Line Movement, Implied Prob, Team Total
6. RECENCY (Heat) - EWMA weighted recent performance
"""

import pandas as pd
import numpy as np
from sklearn.linear_model import Ridge
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
import statsmodels.api as sm
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timedelta
from collections import defaultdict
import logging
import pickle
import os

# XGBoost for better non-linear modeling
try:
    import xgboost as xgb
    HAS_XGBOOST = True
except ImportError:
    HAS_XGBOOST = False
    
logger = logging.getLogger(__name__)


# =============================================================================
# FEATURE DEFINITIONS (V2 - Enhanced)
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
    # NEW: EWMA Recency Features (Heat Factor)
    'recency_ewma': [
        'ewma_l5',          # EWMA of last 5 games (alpha=0.5)
        'ewma_l10',         # EWMA of last 10 games (alpha=0.3)
        'ewma_trend',       # EWMA L5 vs L10 (momentum)
        'heat_index',       # Weighted recent outperformance
    ],
    # NEW: Pace-Adjusted Features (per 100 possessions)
    'pace_adjusted': [
        'pts_per_100',      # Points per 100 possessions
        'reb_per_100',      # Rebounds per 100 possessions
        'ast_per_100',      # Assists per 100 possessions
        'pace_factor',      # Game pace adjustment factor
    ],
    # NEW: Friction Features (Game Difficulty)
    'friction': [
        'b2b_penalty',      # Back-to-back fatigue (0 or 1)
        'opp_interior_def', # Opponent interior defense rating
        'opp_perimeter_def',# Opponent perimeter defense rating
        'matchup_difficulty',# Composite matchup score
        'travel_factor',    # Rest + home/away combined
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
    
    NOW WITH V2 ADVANCED STATS from BDL API!
    Uses real USG%, TS%, Pace, Matchup data instead of proxies.
    """
    
    def __init__(self, db):
        self.db = db
        self._team_pace_cache = {}
        self._def_rating_cache = {}
        self._advanced_stats_cache = {}  # Cache V2 advanced stats
        self._load_team_stats()
        
        # Import team stats service
        from services.team_stats_service import TeamStatsService, TEAM_PACE_2026
        self.team_stats_service = TeamStatsService(db)
        self._team_pace_cache = TEAM_PACE_2026.copy()
        
        # V2 Advanced Stats collection
        self.advanced_stats = db['bdl_advanced_stats']
    
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
    
    # =========================================================================
    # EWMA (EXPONENTIALLY WEIGHTED MOVING AVERAGE) - RECENCY WEIGHTING
    # =========================================================================
    
    def calculate_ewma(
        self,
        values: List[float],
        alpha: float = 0.5,
        min_periods: int = 1
    ) -> float:
        """
        Calculate Exponentially Weighted Moving Average.
        
        Recent games have exponentially more weight than older games.
        
        Args:
            values: List of values (most recent first)
            alpha: Decay factor (0.5 = last game has 50% weight, 0.3 = 30%, etc.)
            min_periods: Minimum number of values needed
        
        Returns:
            EWMA value
            
        Math: EWMA_t = alpha * x_t + (1-alpha) * EWMA_{t-1}
        """
        if not values or len(values) < min_periods:
            return 0.0
        
        # Filter None values
        clean_values = [v for v in values if v is not None]
        if not clean_values:
            return 0.0
        
        # Calculate EWMA (values are most recent first)
        ewma = clean_values[0]
        for i in range(1, len(clean_values)):
            ewma = alpha * clean_values[i] + (1 - alpha) * ewma
        
        # Actually we want most recent to have highest weight
        # So reverse: start from oldest, apply decay
        ewma = clean_values[-1]  # Start with oldest
        for i in range(len(clean_values) - 2, -1, -1):
            ewma = alpha * clean_values[i] + (1 - alpha) * ewma
        
        return round(ewma, 2)
    
    def calculate_heat_index(
        self,
        recent_values: List[float],
        season_avg: float,
        window: int = 5
    ) -> float:
        """
        Calculate "Heat Index" - how much a player is outperforming baseline.
        
        A player with a season avg of 20 who just had 25, 28, 24 is "hot".
        
        Returns:
            Heat index (positive = hot, negative = cold)
        """
        if not recent_values or season_avg <= 0:
            return 0.0
        
        recent = [v for v in recent_values[:window] if v is not None]
        if not recent:
            return 0.0
        
        recent_avg = sum(recent) / len(recent)
        
        # Heat = (Recent - Baseline) / Baseline * 100
        heat = (recent_avg - season_avg) / season_avg * 100
        
        return round(heat, 2)
    
    def get_ewma_features(
        self,
        games: List[Dict],
        stat_type: str
    ) -> Dict[str, float]:
        """
        Calculate all EWMA-based features for a stat type.
        """
        stat_map = {
            'PTS': ['pts', 'points'],
            'REB': ['reb', 'rebounds'],
            'AST': ['ast', 'assists'],
            '3PM': ['fg3m', 'three_pointers_made'],
            'PRA': None,  # Calculated
        }
        
        def get_value(game, stat):
            if stat == 'PRA':
                pts = get_value(game, 'PTS')
                reb = get_value(game, 'REB')
                ast = get_value(game, 'AST')
                if all(v is not None for v in [pts, reb, ast]):
                    return pts + reb + ast
                return None
            
            keys = stat_map.get(stat, [])
            if not keys:
                return None
            for key in keys:
                if key in game and game[key] is not None:
                    val = game[key]
                    if val == '' or val == 'None':
                        continue
                    try:
                        return float(val)
                    except (ValueError, TypeError):
                        continue
            return None
        
        values = [get_value(g, stat_type) for g in games]
        clean_values = [v for v in values if v is not None]
        
        features = {}
        
        # EWMA with different alphas
        features['ewma_l5'] = self.calculate_ewma(clean_values[:5], alpha=0.5)
        features['ewma_l10'] = self.calculate_ewma(clean_values[:10], alpha=0.3)
        
        # EWMA trend (momentum)
        if features['ewma_l10'] > 0:
            features['ewma_trend'] = round(
                (features['ewma_l5'] - features['ewma_l10']) / features['ewma_l10'] * 100, 2
            )
        else:
            features['ewma_trend'] = 0.0
        
        # Heat index
        season_avg = sum(clean_values) / len(clean_values) if clean_values else 0
        features['heat_index'] = self.calculate_heat_index(clean_values, season_avg)
        
        return features
    
    # =========================================================================
    # PACE-ADJUSTED STATS (Per 100 Possessions)
    # =========================================================================
    
    def calculate_pace_adjusted(
        self,
        games: List[Dict],
        stat_type: str,
        league_pace: float = 100.0
    ) -> Dict[str, float]:
        """
        Calculate pace-adjusted stats (per 100 possessions).
        
        This levels the playing field between fast teams (Pacers) and slow teams (Heat).
        """
        features = {}
        
        # Get raw values and minutes
        def get_stat(game, keys):
            for key in keys:
                if key in game and game[key] is not None:
                    val = game[key]
                    # Handle empty strings and other invalid values
                    if val == '' or val == 'None':
                        continue
                    try:
                        return float(val)
                    except (ValueError, TypeError):
                        continue
            return None
        
        pts_values = []
        reb_values = []
        ast_values = []
        min_values = []
        
        for game in games[:10]:
            pts = get_stat(game, ['pts', 'points'])
            reb = get_stat(game, ['reb', 'rebounds'])
            ast = get_stat(game, ['ast', 'assists'])
            mins = get_stat(game, ['min', 'minutes'])
            
            if mins and mins > 0:
                pts_values.append((pts or 0, mins))
                reb_values.append((reb or 0, mins))
                ast_values.append((ast or 0, mins))
                min_values.append(mins)
        
        if not min_values:
            return {
                'pts_per_100': 0,
                'reb_per_100': 0,
                'ast_per_100': 0,
                'pace_factor': 1.0,
            }
        
        # Calculate per-minute rates then project to 100 possessions
        # Assuming ~100 possessions per 48 minutes
        avg_mins = sum(min_values) / len(min_values)
        
        # Per 100 possession estimates (simple: per 36 * pace factor)
        total_pts = sum(p[0] for p in pts_values)
        total_reb = sum(r[0] for r in reb_values)
        total_ast = sum(a[0] for a in ast_values)
        total_mins = sum(min_values)
        
        if total_mins > 0:
            features['pts_per_100'] = round(total_pts / total_mins * 36 * 1.05, 2)
            features['reb_per_100'] = round(total_reb / total_mins * 36 * 1.05, 2)
            features['ast_per_100'] = round(total_ast / total_mins * 36 * 1.05, 2)
        else:
            features['pts_per_100'] = 0
            features['reb_per_100'] = 0
            features['ast_per_100'] = 0
        
        # Pace factor (would ideally use real team pace)
        features['pace_factor'] = 1.0  # Default, will be overridden by V2 data
        
        return features
    
    # =========================================================================
    # FRICTION FEATURES (Game Difficulty)
    # =========================================================================
    
    def calculate_friction_features(
        self,
        games: List[Dict],
        opponent_team: str = None,
    ) -> Dict[str, float]:
        """
        Calculate "friction" features that describe game difficulty.
        
        - Back-to-back penalty
        - Opponent defensive quality
        - Travel/rest factors
        """
        features = {}
        
        # Back-to-back detection
        if len(games) >= 2:
            try:
                date1 = games[0].get('date', '')
                date2 = games[1].get('date', '')
                
                if date1 and date2:
                    d1 = datetime.fromisoformat(date1.replace('Z', ''))
                    d2 = datetime.fromisoformat(date2.replace('Z', ''))
                    days_diff = (d1 - d2).days
                    
                    features['b2b_penalty'] = 1 if days_diff <= 1 else 0
                else:
                    features['b2b_penalty'] = 0
            except:
                features['b2b_penalty'] = 0
        else:
            features['b2b_penalty'] = 0
        
        # Opponent defensive ratings
        if opponent_team and opponent_team in self._def_rating_cache:
            opp_def = self._def_rating_cache[opponent_team]
            features['opp_interior_def'] = opp_def.get('def_rating', 110)
            features['opp_perimeter_def'] = opp_def.get('def_rating', 110)  # Would need splits
            features['matchup_difficulty'] = opp_def.get('def_rank', 15) / 30.0  # Normalized 0-1
        else:
            features['opp_interior_def'] = 110
            features['opp_perimeter_def'] = 110
            features['matchup_difficulty'] = 0.5
        
        # Travel factor (simplified: rest days * home bonus)
        rest_days = features.get('rest_days', 2)
        is_home = 1  # Default, would need game data
        features['travel_factor'] = min(rest_days, 3) * 0.3 + (0.2 if is_home else 0)
        
        return features
    
    def calculate_interaction_multipliers(
        self,
        features: Dict[str, float],
        stat_type: str,
        opponent_team: str = None,
    ) -> Dict[str, float]:
        """
        Calculate Gemini's "Baddest Ass" Interaction Multipliers.
        
        These capture basketball-specific synergies:
        1. Green Light Multiplier - 3PT shooting opportunity
        2. Vacuum Multiplier - Usage redistribution  
        3. Track Meet Multiplier - Pace adjustment
        4. Rim Pressure Multiplier - Paint attack opportunity
        5. Freshness Multiplier - Fatigue adjustment
        """
        multipliers = {}
        
        # Helper to safely get float values
        def safe_get(key, default=0.0):
            val = features.get(key)
            if val is None:
                return default
            try:
                return float(val)
            except (TypeError, ValueError):
                return default
        
        # =================================================================
        # 1. GREEN LIGHT MULTIPLIER (3PT Shooting Opportunity)
        # Formula: 3PA_Rate × (1 - Opp_Contest%) × Opp_3P%_Allowed
        # =================================================================
        # 3PA Rate: 3-point attempt rate (3PA / FGA)
        fg3_rate = safe_get('fg3_rate', 0.35)  # Default league avg ~35%
        
        # Opp Contest%: How often opponent contests shots (use inverse for "openness")
        # From V2 stats: v2_contested_shots_l5 gives us contest rate
        opp_contest_rate = safe_get('v2_contested_fg_pct_l5', 0.45) if safe_get('has_v2_advanced', 0) else 0.45
        openness_factor = 1 - min(opp_contest_rate, 0.8)  # Cap at 80% contest rate
        
        # Opp 3P% Allowed: How well/poorly opponent defends the 3
        opp_3p_allowed = 0.36  # League average
        if opponent_team and opponent_team in self._def_rating_cache:
            opp_def = self._def_rating_cache[opponent_team]
            # Worse defense = higher allowed %
            opp_3p_allowed = 0.30 + (opp_def.get('def_rank', 15) / 30.0) * 0.12  # Scale 30-42%
        
        multipliers['green_light_multiplier'] = fg3_rate * openness_factor * (opp_3p_allowed / 0.36)
        
        # =================================================================
        # 2. VACUUM MULTIPLIER (Usage Redistribution when stars out)
        # Formula: Base_USG + (Missing_USG × Teammate_Correlation_Factor)
        # =================================================================
        # Base usage rate
        base_usg = safe_get('usg_rate_l5', 20) / 100  # Convert to decimal
        
        # For now, use a proxy based on team total share and recent usage trend
        # Higher team share + increasing usage = potential vacuum benefit
        team_share = safe_get('team_total_share', 15) / 100
        usg_trend = safe_get('ewma_trend', 0)
        
        # Correlation factor: players with higher usage tend to absorb more
        correlation_factor = 0.3 if base_usg > 0.25 else 0.2 if base_usg > 0.20 else 0.15
        
        # Estimated missing usage (if a star is out, ~30% USG is up for grabs)
        missing_usg_proxy = max(0, (team_share - base_usg)) * 0.5  # Estimated absorption
        
        multipliers['vacuum_multiplier'] = base_usg + (missing_usg_proxy * correlation_factor)
        # Boost if player shows upward trend (hot hand / role expansion)
        if usg_trend > 0:
            multipliers['vacuum_multiplier'] *= (1 + usg_trend * 0.1)
        
        # =================================================================
        # 3. TRACK MEET MULTIPLIER (Pace & Volume)
        # Formula: (Team_A_Pace + Team_B_Pace) / (2 × League_Avg_Pace)
        # =================================================================
        league_avg_pace = 100.0  # NBA average possessions per game
        
        # Get player's team pace from V2 stats
        player_pace = safe_get('v2_pace_l5', safe_get('player_pace_l5', 100))
        
        # Get opponent pace
        opp_pace = 100.0  # Default
        if opponent_team and opponent_team in self._def_rating_cache:
            opp_def = self._def_rating_cache[opponent_team]
            # Defensive rating correlates inversely with pace
            opp_pace = 105 - (opp_def.get('def_rank', 15) - 15) * 0.5  # Rough estimate
        
        combined_pace = (player_pace + opp_pace) / 2
        multipliers['track_meet_multiplier'] = combined_pace / league_avg_pace
        
        # =================================================================
        # 4. RIM PRESSURE MULTIPLIER (Paint Attack Opportunity)
        # Formula: Rim_Freq × (1 - Opp_BLK_Rate) × Opp_Rim_FG%_Allowed
        # =================================================================
        # Rim frequency: How often player attacks the paint
        # Proxy: Use % of points from paint if available
        rim_freq = safe_get('v2_pct_pts_paint_l5', 0.30) if safe_get('has_v2_advanced', 0) else 0.30
        
        # Opponent block rate and rim protection
        opp_blk_rate = 0.05  # League average ~5%
        opp_rim_fg_allowed = 0.62  # League average ~62% at rim
        
        if opponent_team and opponent_team in self._def_rating_cache:
            opp_def = self._def_rating_cache[opponent_team]
            # Better defense = lower rim FG allowed, higher block rate
            def_quality = opp_def.get('def_rank', 15) / 30.0  # 0 = best, 1 = worst
            opp_blk_rate = 0.08 - (def_quality * 0.04)  # 4-8% range
            opp_rim_fg_allowed = 0.58 + (def_quality * 0.08)  # 58-66% range
        
        multipliers['rim_pressure_multiplier'] = rim_freq * (1 - opp_blk_rate) * (opp_rim_fg_allowed / 0.62)
        
        # =================================================================
        # 5. FRESHNESS MULTIPLIER (Fatigue Adjustment)
        # Formula: β_rest - (B2B × 0.05) - (Miles × 0.0001)
        # =================================================================
        b2b_status = safe_get('is_b2b', 0)
        rest_days = safe_get('rest_days', 2)
        
        # Base freshness (fully rested = 1.0)
        base_freshness = 1.0
        
        # B2B penalty: -5% per back-to-back
        b2b_penalty = b2b_status * 0.05
        
        # Rest bonus: +2% per extra rest day (up to 3 days)
        rest_bonus = min(rest_days, 3) * 0.02
        
        # Games in 7 days penalty (schedule density)
        games_7d = safe_get('games_in_7_days', 3)
        density_penalty = max(0, (games_7d - 3)) * 0.015  # -1.5% per extra game
        
        # Travel proxy: Away games = ~500 miles average
        is_home = safe_get('is_home', 1)
        travel_penalty = 0.0 if is_home else 0.025  # -2.5% for road games
        
        multipliers['freshness_multiplier'] = base_freshness - b2b_penalty + rest_bonus - density_penalty - travel_penalty
        
        # =================================================================
        # COMPOSITE MULTIPLIERS (Stat-Type Specific)
        # =================================================================
        
        # Scoring multiplier (for PTS)
        multipliers['scoring_opportunity_multiplier'] = (
            multipliers['green_light_multiplier'] * 0.3 +
            multipliers['rim_pressure_multiplier'] * 0.3 +
            multipliers['track_meet_multiplier'] * 0.25 +
            multipliers['freshness_multiplier'] * 0.15
        )
        
        # Volume multiplier (for all stats)
        multipliers['volume_opportunity_multiplier'] = (
            multipliers['track_meet_multiplier'] * 0.4 +
            multipliers['vacuum_multiplier'] * 0.35 +
            multipliers['freshness_multiplier'] * 0.25
        )
        
        # Apply stat-type specific multiplier to base projection
        base_avg = safe_get('l5_avg', 10)
        
        if stat_type in ['pts', 'PTS']:
            multipliers['adjusted_projection'] = base_avg * multipliers['scoring_opportunity_multiplier']
        elif stat_type in ['3pm', '3PM']:
            multipliers['adjusted_projection'] = base_avg * multipliers['green_light_multiplier'] * multipliers['freshness_multiplier']
        elif stat_type in ['reb', 'REB']:
            # Rebounds: pace and physicality matter
            multipliers['adjusted_projection'] = base_avg * multipliers['track_meet_multiplier'] * multipliers['freshness_multiplier']
        elif stat_type in ['ast', 'AST']:
            # Assists: pace and usage matter
            multipliers['adjusted_projection'] = base_avg * multipliers['volume_opportunity_multiplier']
        else:  # PRA, etc.
            multipliers['adjusted_projection'] = base_avg * multipliers['volume_opportunity_multiplier']
        
        return multipliers
    
    def get_v2_advanced_stats(self, player_id: int, limit: int = 20) -> List[Dict]:
        """
        Get V2 Advanced Stats for a player from bdl_advanced_stats collection.
        
        Returns most recent games with real process stats:
        - usage_percentage (USG%)
        - true_shooting_percentage (TS%)
        - effective_field_goal_percentage (eFG%)
        - pace, pace_per_40
        - matchup data (matchup_fg_pct, matchup_player_points, etc.)
        - tracking data (touches, speed, distance)
        """
        # Check cache first
        cache_key = f"{player_id}_{limit}"
        if cache_key in self._advanced_stats_cache:
            return self._advanced_stats_cache[cache_key]
        
        try:
            stats = list(self.advanced_stats.find(
                {"player_id": player_id},
                {"_id": 0}
            ).sort("game_date", -1).limit(limit))
            
            self._advanced_stats_cache[cache_key] = stats
            return stats
        except Exception as e:
            logger.error(f"Failed to get V2 advanced stats for player {player_id}: {e}")
            return []
    
    def get_v2_features(self, player_id: int, window: int = 5) -> Dict[str, float]:
        """
        Extract V2 Advanced Stats features for a player.
        
        These are the REAL process stats Vegas uses, not proxies!
        """
        stats = self.get_v2_advanced_stats(player_id, limit=20)
        
        if not stats:
            return {}
        
        features = {}
        
        # Get L5 and L10 windows
        l5 = stats[:5]
        l10 = stats[:10]
        
        # Helper function to safely average non-null values
        def safe_avg(data: List[Dict], field: str) -> Optional[float]:
            vals = [d.get(field) for d in data if d.get(field) is not None]
            if not vals:
                return None
            return sum(vals) / len(vals)
        
        # =================================================================
        # CORE V2 ADVANCED STATS
        # =================================================================
        
        # Usage Rate (THE key stat for opportunity)
        features['v2_usg_rate_l5'] = safe_avg(l5, 'usage_percentage')
        features['v2_usg_rate_l10'] = safe_avg(l10, 'usage_percentage')
        
        # True Shooting % (Efficiency)
        features['v2_ts_pct_l5'] = safe_avg(l5, 'true_shooting_percentage')
        features['v2_ts_pct_l10'] = safe_avg(l10, 'true_shooting_percentage')
        
        # Effective FG% (Shooting quality)
        features['v2_efg_l5'] = safe_avg(l5, 'effective_field_goal_percentage')
        features['v2_efg_l10'] = safe_avg(l10, 'effective_field_goal_percentage')
        
        # Pace (Game tempo)
        features['v2_pace_l5'] = safe_avg(l5, 'pace')
        features['v2_pace_l10'] = safe_avg(l10, 'pace')
        
        # Offensive/Defensive Rating
        features['v2_off_rating_l5'] = safe_avg(l5, 'offensive_rating')
        features['v2_def_rating_l5'] = safe_avg(l5, 'defensive_rating')
        features['v2_net_rating_l5'] = safe_avg(l5, 'net_rating')
        
        # =================================================================
        # MATCHUP DATA (The gold for player prop betting!)
        # =================================================================
        
        # How well opposing players shoot against this player
        features['v2_matchup_fg_pct_l5'] = safe_avg(l5, 'matchup_fg_pct')
        features['v2_matchup_pts_allowed_l5'] = safe_avg(l5, 'matchup_player_points')
        features['v2_matchup_3pt_pct_l5'] = safe_avg(l5, 'matchup_3pt_pct')
        
        # =================================================================
        # TRACKING DATA (Physical activity)
        # =================================================================
        
        features['v2_touches_l5'] = safe_avg(l5, 'touches')
        features['v2_passes_l5'] = safe_avg(l5, 'passes')
        features['v2_speed_l5'] = safe_avg(l5, 'speed')
        features['v2_distance_l5'] = safe_avg(l5, 'distance')
        
        # =================================================================
        # SHOT QUALITY (Contested vs Uncontested)
        # =================================================================
        
        features['v2_contested_fg_pct_l5'] = safe_avg(l5, 'contested_fg_pct')
        features['v2_uncontested_fg_pct_l5'] = safe_avg(l5, 'uncontested_fg_pct')
        
        # =================================================================
        # ASSIST/PLAYMAKING
        # =================================================================
        
        features['v2_assist_pct_l5'] = safe_avg(l5, 'assist_percentage')
        features['v2_assist_ratio_l5'] = safe_avg(l5, 'assist_ratio')
        features['v2_ast_to_tov_l5'] = safe_avg(l5, 'assist_to_turnover')
        
        # =================================================================
        # REBOUNDING
        # =================================================================
        
        features['v2_reb_pct_l5'] = safe_avg(l5, 'rebound_percentage')
        features['v2_oreb_pct_l5'] = safe_avg(l5, 'offensive_rebound_percentage')
        features['v2_dreb_pct_l5'] = safe_avg(l5, 'defensive_rebound_percentage')
        
        # =================================================================
        # HUSTLE STATS
        # =================================================================
        
        features['v2_deflections_l5'] = safe_avg(l5, 'deflections')
        features['v2_contested_shots_l5'] = safe_avg(l5, 'contested_shots')
        features['v2_loose_balls_l5'] = safe_avg(l5, 'loose_balls_recovered_total')
        
        # =================================================================
        # PIE (Player Impact Estimate)
        # =================================================================
        
        features['v2_pie_l5'] = safe_avg(l5, 'pie')
        features['v2_pie_l10'] = safe_avg(l10, 'pie')
        
        # =================================================================
        # SCORING DISTRIBUTION
        # =================================================================
        
        features['v2_pct_pts_paint_l5'] = safe_avg(l5, 'pct_pts_paint')
        features['v2_pct_pts_3pt_l5'] = safe_avg(l5, 'pct_pts_3pt')
        features['v2_pct_pts_ft_l5'] = safe_avg(l5, 'pct_pts_free_throw')
        features['v2_pct_pts_fastbreak_l5'] = safe_avg(l5, 'pct_pts_fast_break')
        
        # Round all values
        for key, val in features.items():
            if val is not None:
                features[key] = round(val, 3)
        
        return features
    
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
        bdl_player_id: int = None,  # NEW: For V2 Advanced Stats
    ) -> Dict[str, float]:
        """
        Extract all Vegas-style features from game logs.
        
        NOW WITH V2 ADVANCED STATS when bdl_player_id is provided!
        
        Args:
            prior_games: Games BEFORE the target game (for training)
            stat_type: The stat we're predicting
            target_game: The game we're predicting (for context like opponent)
            opponent_team: Opponent team abbreviation
            line: The prop line (for market features)
            team_total: Projected team total
            bdl_player_id: BDL player ID for V2 Advanced Stats lookup
        
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
        
        # =====================================================================
        # V2 ADVANCED STATS FEATURES (The Real Process Stats!)
        # =====================================================================
        # These replace the proxy calculations with REAL data from BDL V2 API
        if bdl_player_id:
            v2_features = self.get_v2_features(bdl_player_id)
            
            if v2_features:
                # Merge V2 features into main feature dict
                features.update(v2_features)
                
                # OVERRIDE proxy calculations with real V2 data where available
                if v2_features.get('v2_usg_rate_l5') is not None:
                    # Convert decimal to percentage (BDL returns as decimal)
                    features['usg_rate_l5'] = v2_features['v2_usg_rate_l5'] * 100
                
                if v2_features.get('v2_ts_pct_l5') is not None:
                    features['ts_l5'] = v2_features['v2_ts_pct_l5'] * 100
                
                if v2_features.get('v2_ts_pct_l10') is not None:
                    features['ts_l10'] = v2_features['v2_ts_pct_l10'] * 100
                
                if v2_features.get('v2_efg_l5') is not None:
                    features['efg_l5'] = v2_features['v2_efg_l5'] * 100
                
                if v2_features.get('v2_efg_l10') is not None:
                    features['efg_l10'] = v2_features['v2_efg_l10'] * 100
                
                if v2_features.get('v2_pace_l5') is not None:
                    features['player_pace_l5'] = v2_features['v2_pace_l5']
                
                # Add flag indicating V2 data was used
                features['has_v2_advanced'] = 1
            else:
                features['has_v2_advanced'] = 0
        else:
            features['has_v2_advanced'] = 0
        
        # =====================================================================
        # EWMA FEATURES (Recency Weighting / "Heat" Factor)
        # =====================================================================
        ewma_features = self.get_ewma_features(prior_games, stat_type)
        features.update(ewma_features)
        
        # =====================================================================
        # PACE-ADJUSTED FEATURES (Per 100 Possessions)
        # =====================================================================
        pace_features = self.calculate_pace_adjusted(prior_games, stat_type)
        features.update(pace_features)
        
        # =====================================================================
        # FRICTION FEATURES (Game Difficulty)
        # =====================================================================
        friction_features = self.calculate_friction_features(prior_games, opponent_team)
        features.update(friction_features)
        
        # =====================================================================
        # INTERACTION MULTIPLIERS (Gemini's "Baddest Ass" Features)
        # These capture basketball-specific synergies and non-linear relationships
        # =====================================================================
        multiplier_features = self.calculate_interaction_multipliers(features, stat_type, opponent_team)
        features.update(multiplier_features)
        
        return features
    
    def build_training_dataset(
        self,
        stat_type: str,
        min_games: int = 15,
        use_v2_stats: bool = True,  # Include V2 Advanced Stats
        use_historical: bool = True,  # NEW: Include 2020-2025 historical data
        seasons: list = None  # Specific seasons to include (default: all)
    ) -> pd.DataFrame:
        """
        Build comprehensive training dataset with all Vegas features.
        
        Now includes:
        - V2 Advanced Stats when available
        - Historical data from 2020-2025 seasons
        """
        all_rows = []
        player_count = 0
        v2_enriched = 0
        
        # =====================================================================
        # SOURCE 1: Current Season (nba_master_hub_2026)
        # =====================================================================
        hub = self.db['nba_master_hub_2026']
        
        players = hub.find({
            'bdl_game_logs': {'$exists': True},
            f'bdl_game_logs.{min_games}': {'$exists': True}
        })
        
        logger.info(f"[{stat_type}] Building training dataset...")
        
        for player in players:
            player_name = player.get('display_name') or player.get('player_name')
            logs = player.get('bdl_game_logs', [])
            bdl_id = player.get('bdl_id')
            
            if len(logs) < min_games:
                continue
            
            player_count += 1
            
            for i in range(min_games - 1, len(logs)):
                target_game = logs[i]
                prior_games = logs[i+1:i+1+20]
                
                if len(prior_games) < 5:
                    continue
                
                target_value = self._get_stat_value(target_game, stat_type)
                if target_value is None:
                    continue
                
                features = self.extract_features(
                    prior_games=prior_games,
                    stat_type=stat_type,
                    target_game=target_game,
                    opponent_team=None,
                    bdl_player_id=bdl_id if use_v2_stats else None,
                )
                
                if features:
                    features['target'] = target_value
                    features['player_name'] = player_name
                    features['game_date'] = target_game.get('date')
                    features['season'] = 2025
                    all_rows.append(features)
                    
                    if features.get('has_v2_advanced') == 1:
                        v2_enriched += 1
        
        logger.info(f"[{stat_type}] Current season: {len(all_rows)} samples from {player_count} players")
        
        # =====================================================================
        # SOURCE 2: Historical Data (2020-2024)
        # =====================================================================
        if use_historical:
            historical_collection = self.db.get_collection('bdl_historical_game_logs')
            historical_count = historical_collection.count_documents({})
            
            if historical_count > 0:
                logger.info(f"[{stat_type}] Found {historical_count:,} historical game logs")
                
                # Get list of seasons to process
                target_seasons = seasons or [2020, 2021, 2022, 2023, 2024]
                
                for season in target_seasons:
                    season_rows = 0
                    
                    # Get unique players for this season
                    player_ids = historical_collection.distinct('player_id', {'season': season})
                    
                    for player_id in player_ids:
                        # Get all games for this player in this season, sorted by date
                        games = list(historical_collection.find(
                            {'player_id': player_id, 'season': season}
                        ).sort('date', -1))
                        
                        if len(games) < min_games:
                            continue
                        
                        player_name = games[0].get('player_name', f'Player_{player_id}')
                        
                        for i in range(min_games - 1, len(games)):
                            target_game = games[i]
                            prior_games = games[i+1:i+1+20]
                            
                            if len(prior_games) < 5:
                                continue
                            
                            target_value = self._get_stat_value(target_game, stat_type)
                            if target_value is None:
                                continue
                            
                            # Extract features (V2 stats from historical advanced collection)
                            features = self.extract_features_historical(
                                prior_games=prior_games,
                                stat_type=stat_type,
                                target_game=target_game,
                                player_id=player_id,
                                season=season
                            )
                            
                            if features:
                                features['target'] = target_value
                                features['player_name'] = player_name
                                features['game_date'] = target_game.get('date')
                                features['season'] = season
                                all_rows.append(features)
                                season_rows += 1
                                
                                if features.get('has_v2_advanced') == 1:
                                    v2_enriched += 1
                    
                    if season_rows > 0:
                        logger.info(f"[{stat_type}] Season {season}: {season_rows:,} samples added")
            else:
                logger.info(f"[{stat_type}] No historical data found - run fetch_historical_data.py first")
        
        df = pd.DataFrame(all_rows)
        logger.info(f"[{stat_type}] TOTAL: {len(df):,} training samples")
        logger.info(f"[{stat_type}] V2 Advanced Stats: {v2_enriched:,}/{len(df):,} samples ({v2_enriched/max(len(df),1)*100:.1f}%)")
        
        return df
    
    def extract_features_historical(
        self,
        prior_games: List[Dict],
        stat_type: str,
        target_game: Dict,
        player_id: int,
        season: int
    ) -> Dict[str, float]:
        """
        Extract features from historical game logs.
        Uses historical advanced stats when available.
        """
        # Use the base extract_features logic
        features = self.extract_features(
            prior_games=prior_games,
            stat_type=stat_type,
            target_game=target_game,
            opponent_team=None,
            bdl_player_id=None  # Don't use live V2 API
        )
        
        if not features:
            return None
        
        # Try to enrich with historical V2 advanced stats
        try:
            advanced_collection = self.db.get_collection('bdl_advanced_stats')
            
            # Get recent advanced stats for this player/season
            recent_advanced = list(advanced_collection.find(
                {'player_id': player_id, 'season': season}
            ).sort('game_date', -1).limit(10))
            
            if recent_advanced:
                # Calculate averages from recent advanced stats
                def avg_stat(key):
                    vals = [g.get(key) for g in recent_advanced if g.get(key) is not None]
                    return sum(vals) / len(vals) if vals else None
                
                features['v2_usg_rate_l5'] = avg_stat('usage_percentage')
                features['v2_ts_pct_l5'] = avg_stat('true_shooting_percentage')
                features['v2_efg_l5'] = avg_stat('effective_field_goal_percentage')
                features['v2_pace_l5'] = avg_stat('pace')
                features['v2_off_rating_l5'] = avg_stat('offensive_rating')
                features['v2_def_rating_l5'] = avg_stat('defensive_rating')
                features['v2_net_rating_l5'] = avg_stat('net_rating')
                features['v2_assist_pct_l5'] = avg_stat('assist_percentage')
                features['v2_reb_pct_l5'] = avg_stat('rebound_percentage')
                features['v2_pie_l5'] = avg_stat('pie')
                
                # Override proxy calculations
                if features.get('v2_usg_rate_l5'):
                    features['usg_rate_l5'] = features['v2_usg_rate_l5'] * 100
                if features.get('v2_ts_pct_l5'):
                    features['ts_l5'] = features['v2_ts_pct_l5'] * 100
                if features.get('v2_efg_l5'):
                    features['efg_l5'] = features['v2_efg_l5'] * 100
                
                features['has_v2_advanced'] = 1
            else:
                features['has_v2_advanced'] = 0
                
        except Exception as e:
            features['has_v2_advanced'] = 0
        
        return features


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
        model_type: str = 'xgboost',  # 'ridge', 'gbm', 'xgboost', 'ensemble'
        use_feature_selection: bool = False,  # DISABLED: Use ALL features (user preference)
        p_value_threshold: float = 0.10,  # Features with p > 0.10 would be dropped if enabled
    ) -> Dict[str, Any]:
        """
        Train Vegas Killer V2 model with:
        - XGBoost (handles IF/AND logic)
        - Feature selection via P-values (DISABLED by default - use all features)
        - Enhanced features (EWMA, Friction, Pace-adjusted)
        
        NOTE: Feature selection is disabled by default because more data = better predictions
        even if some features aren't statistically significant individually. XGBoost handles
        feature interactions well, so we let it use everything.
        """
        logger.info(f"[{stat_type}] Training Vegas Killer V2 model...")
        logger.info(f"  Model type: {model_type}")
        logger.info(f"  Feature selection: {use_feature_selection} (p < {p_value_threshold})")
        
        df = self.feature_engineer.build_training_dataset(stat_type)
        
        if df.empty or len(df) < 100:
            return {"error": f"Insufficient data: {len(df)} samples"}
        
        # Get feature columns
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
        feature_cols = [c for c in numeric_cols if c not in ['target']]
        
        X = df[feature_cols].fillna(0).replace([np.inf, -np.inf], 0)
        y = df['target']
        
        # =====================================================================
        # FEATURE SELECTION (P-Value based - Noise Killer)
        # =====================================================================
        selected_features = feature_cols
        dropped_features = []
        
        if use_feature_selection:
            logger.info(f"[{stat_type}] Running feature selection...")
            
            try:
                X_const = sm.add_constant(X)
                ols_model = sm.OLS(y, X_const).fit()
                
                selected_features = []
                for feature in feature_cols:
                    pval = ols_model.pvalues.get(feature, 1.0)
                    if pval < p_value_threshold:
                        selected_features.append(feature)
                    else:
                        dropped_features.append((feature, round(pval, 4)))
                
                logger.info(f"[{stat_type}] Kept {len(selected_features)} features, dropped {len(dropped_features)} noisy features")
                
                # Update X with selected features only
                if selected_features:
                    X = X[selected_features]
                    feature_cols = selected_features
                else:
                    logger.warning(f"[{stat_type}] No significant features found, using all")
                    
            except Exception as e:
                logger.warning(f"Feature selection failed: {e}, using all features")
        
        # Train/test split
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42
        )
        
        # Scale features
        scaler = StandardScaler()
        X_train_scaled = scaler.fit_transform(X_train)
        X_test_scaled = scaler.transform(X_test)
        
        # =====================================================================
        # MODEL TRAINING
        # =====================================================================
        if model_type == 'ridge':
            model = Ridge(alpha=1.0)
            model.fit(X_train_scaled, y_train)
        
        elif model_type == 'gbm':
            model = GradientBoostingRegressor(
                n_estimators=150,
                max_depth=5,
                learning_rate=0.08,
                subsample=0.8,
                random_state=42
            )
            model.fit(X_train_scaled, y_train)
        
        elif model_type == 'xgboost' and HAS_XGBOOST:
            # XGBoost - handles complex IF/AND logic better
            model = xgb.XGBRegressor(
                n_estimators=200,
                max_depth=6,
                learning_rate=0.05,
                subsample=0.8,
                colsample_bytree=0.8,
                min_child_weight=3,
                gamma=0.1,
                reg_alpha=0.1,
                reg_lambda=1.0,
                random_state=42,
                verbosity=0
            )
            model.fit(X_train_scaled, y_train)
            logger.info(f"[{stat_type}] Using XGBoost model")
        
        else:  # ensemble
            ridge = Ridge(alpha=1.0)
            ridge.fit(X_train_scaled, y_train)
            
            if HAS_XGBOOST:
                xgb_model = xgb.XGBRegressor(
                    n_estimators=150,
                    max_depth=5,
                    learning_rate=0.05,
                    subsample=0.8,
                    random_state=42,
                    verbosity=0
                )
                xgb_model.fit(X_train_scaled, y_train)
                model = EnsembleModel([ridge, xgb_model], weights=[0.3, 0.7])
            else:
                gbm = GradientBoostingRegressor(
                    n_estimators=100,
                    max_depth=4,
                    learning_rate=0.1,
                    random_state=42
                )
                gbm.fit(X_train_scaled, y_train)
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
            "n_dropped_features": len(dropped_features),
            "dropped_features": dropped_features[:10],  # Top 10 noisy
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
        
        NOW WITH V2 ADVANCED STATS when available!
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
        
        # Get BDL player ID for V2 Advanced Stats
        bdl_player_id = player.get('bdl_id')
        
        # Extract features (with V2 Advanced Stats if available)
        features = self.feature_engineer.extract_features(
            prior_games=logs[:20],
            stat_type=stat_type,
            target_game=None,
            opponent_team=opponent_team,
            line=line,
            team_total=team_total,
            bdl_player_id=bdl_player_id,  # NEW: Pass BDL ID for V2 stats
        )
        
        if not features:
            return {"error": "Feature extraction failed"}
        
        # Build feature vector - ensure all required features exist
        feature_dict = {col: features.get(col, 0) for col in feature_cols}
        X = pd.DataFrame([feature_dict])
        X = X.fillna(0).replace([np.inf, -np.inf], 0)
        X_scaled = scaler.transform(X)
        
        # Predict
        raw_prediction = model.predict(X_scaled)[0]
        prediction = float(raw_prediction)  # Convert numpy to Python float
        
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
        
        # =====================================================================
        # FULL FEATURE BREAKDOWN (All 8 Categories)
        # =====================================================================
        
        result["full_features"] = {
            # BASELINE FEATURES (Rolling Averages)
            "baseline": {
                "season_avg": round(features.get('season_avg', 0), 2),
                "l3_avg": round(features.get('l3_avg', 0), 2),
                "l5_avg": round(features.get('l5_avg', 0), 2),
                "l10_avg": round(features.get('l10_avg', 0), 2),
                "std_dev_l10": round(features.get('std_dev_l10', 0), 2),
                "floor_l10": round(features.get('floor_l10', 0), 2),
                "ceiling_l10": round(features.get('ceiling_l10', 0), 2),
                "mode_l10": round(features.get('mode_l10', 0), 2),
                "median_l10": round(features.get('median_l10', 0), 2),
            },
            # OPPORTUNITY FEATURES (Volume)
            "opportunity": {
                "usg_rate_l5": round(features.get('usg_rate_l5', 0), 2),
                "minutes_l5": round(features.get('minutes_l5', 0), 2),
                "minutes_trend": round(features.get('minutes_trend', 0), 2),
                "fga_l5": round(features.get('fga_l5', 0), 2),
                "fga_l10": round(features.get('fga_l10', 0), 2),
                "ftr_l5": round(features.get('ftr_l5', 0), 2),
                "touches_proxy": round(features.get('touches_proxy', 0), 2),
            },
            # EFFICIENCY FEATURES (Quality)
            "efficiency": {
                "efg_l5": round(features.get('efg_l5', 0), 2),
                "efg_l10": round(features.get('efg_l10', 0), 2),
                "ts_l5": round(features.get('ts_l5', 0), 2),
                "ts_l10": round(features.get('ts_l10', 0), 2),
                "fg3_rate": round(features.get('fg3_rate', 0), 2),
                "fg3_pct_l5": round(features.get('fg3_pct_l5', 0), 2),
                "ft_pct_l5": round(features.get('ft_pct_l5', 0), 2),
                "scoring_efficiency_trend": round(features.get('scoring_efficiency_trend', 0), 2),
            },
            # MATCHUP FEATURES (Friction)
            "matchup": {
                "opp_def_rating": round(features.get('opp_def_rating', 0), 2),
                "opp_pace": round(features.get('opp_pace', 0), 2),
                "opp_pts_allowed": round(features.get('opp_pts_allowed', 0), 2),
                "opp_def_rank": round(features.get('opp_def_rank', 0), 2),
                "pace_delta": round(features.get('pace_delta', 0), 2),
            },
            # ENVIRONMENT FEATURES (Fatigue)
            "environment": {
                "rest_days": features.get('rest_days', 1),
                "is_b2b": features.get('is_b2b', 0),
                "is_home": features.get('is_home', 0),
                "games_in_7_days": features.get('games_in_7_days', 0),
                "season_game_num": features.get('season_game_num', 0),
            },
            # RECENCY/EWMA FEATURES (Heat Factor)
            "recency": {
                "ewma_l5": round(features.get('ewma_l5', 0), 2),
                "ewma_l10": round(features.get('ewma_l10', 0), 2),
                "ewma_trend": round(features.get('ewma_trend', 0), 2),
                "heat_index": round(features.get('heat_index', 0), 2),
            },
            # PACE-ADJUSTED FEATURES (per 100 poss)
            "pace_adjusted": {
                "pts_per_100": round(features.get('pts_per_100', 0), 2),
                "reb_per_100": round(features.get('reb_per_100', 0), 2),
                "ast_per_100": round(features.get('ast_per_100', 0), 2),
                "pace_factor": round(features.get('pace_factor', 0), 2),
            },
            # FRICTION FEATURES (Game Difficulty)
            "friction": {
                "b2b_penalty": features.get('b2b_penalty', 0),
                "opp_interior_def": round(features.get('opp_interior_def', 0), 2),
                "opp_perimeter_def": round(features.get('opp_perimeter_def', 0), 2),
                "matchup_difficulty": round(features.get('matchup_difficulty', 0), 2),
                "travel_factor": round(features.get('travel_factor', 0), 2),
            },
        }
        
        # Add V2 Advanced Stats if available
        if features.get('has_v2_advanced') == 1:
            result["v2_advanced_stats"] = {
                "usage_rate": features.get('v2_usg_rate_l5'),
                "true_shooting": features.get('v2_ts_pct_l5'),
                "efg": features.get('v2_efg_l5'),
                "pace": features.get('v2_pace_l5'),
                "touches": features.get('v2_touches_l5'),
                "pie": features.get('v2_pie_l5'),
                "assist_pct": features.get('v2_assist_pct_l5'),
                "reb_pct": features.get('v2_reb_pct_l5'),
                "matchup_fg_pct": features.get('v2_matchup_fg_pct_l5'),
            }
            # Add FULL V2 stats for deep intel
            result["full_features"]["v2_advanced"] = {
                # Core efficiency
                "v2_usg_rate_l5": features.get('v2_usg_rate_l5'),
                "v2_usg_rate_l10": features.get('v2_usg_rate_l10'),
                "v2_ts_pct_l5": features.get('v2_ts_pct_l5'),
                "v2_ts_pct_l10": features.get('v2_ts_pct_l10'),
                "v2_efg_l5": features.get('v2_efg_l5'),
                "v2_efg_l10": features.get('v2_efg_l10'),
                # Tempo
                "v2_pace_l5": features.get('v2_pace_l5'),
                "v2_pace_l10": features.get('v2_pace_l10'),
                "v2_off_rating_l5": features.get('v2_off_rating_l5'),
                "v2_def_rating_l5": features.get('v2_def_rating_l5'),
                "v2_net_rating_l5": features.get('v2_net_rating_l5'),
                # Matchup (GOLD!)
                "v2_matchup_fg_pct_l5": features.get('v2_matchup_fg_pct_l5'),
                "v2_matchup_pts_allowed_l5": features.get('v2_matchup_pts_allowed_l5'),
                "v2_matchup_3pt_pct_l5": features.get('v2_matchup_3pt_pct_l5'),
                # Tracking
                "v2_touches_l5": features.get('v2_touches_l5'),
                "v2_passes_l5": features.get('v2_passes_l5'),
                "v2_speed_l5": features.get('v2_speed_l5'),
                "v2_distance_l5": features.get('v2_distance_l5'),
                # Shot quality
                "v2_contested_fg_pct_l5": features.get('v2_contested_fg_pct_l5'),
                "v2_uncontested_fg_pct_l5": features.get('v2_uncontested_fg_pct_l5'),
                # Playmaking
                "v2_assist_pct_l5": features.get('v2_assist_pct_l5'),
                "v2_assist_ratio_l5": features.get('v2_assist_ratio_l5'),
                "v2_ast_to_tov_l5": features.get('v2_ast_to_tov_l5'),
                # Rebounding
                "v2_reb_pct_l5": features.get('v2_reb_pct_l5'),
                "v2_oreb_pct_l5": features.get('v2_oreb_pct_l5'),
                "v2_dreb_pct_l5": features.get('v2_dreb_pct_l5'),
                # Hustle
                "v2_deflections_l5": features.get('v2_deflections_l5'),
                "v2_contested_shots_l5": features.get('v2_contested_shots_l5'),
                "v2_loose_balls_l5": features.get('v2_loose_balls_l5'),
                # Impact
                "v2_pie_l5": features.get('v2_pie_l5'),
                "v2_pie_l10": features.get('v2_pie_l10'),
                # Scoring distribution
                "v2_pct_pts_paint_l5": features.get('v2_pct_pts_paint_l5'),
                "v2_pct_pts_3pt_l5": features.get('v2_pct_pts_3pt_l5'),
                "v2_pct_pts_ft_l5": features.get('v2_pct_pts_ft_l5'),
                "v2_pct_pts_fastbreak_l5": features.get('v2_pct_pts_fastbreak_l5'),
            }
            result["data_source"] = "V2_ADVANCED"
        else:
            result["data_source"] = "PROXY_CALCULATIONS"
        
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

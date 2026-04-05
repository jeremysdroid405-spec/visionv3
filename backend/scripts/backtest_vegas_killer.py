"""
Vegas Killer Backtesting Simulation
=====================================
Validates the Vegas Killer model against historical outcomes.

Key metrics:
- Win Rate (must beat 52.4% for -110 odds)
- ROI (Return on Investment)
- Edge accuracy (predicted edge vs actual edge)
- Performance by confidence level

Usage:
    python scripts/backtest_vegas_killer.py
"""

import os
import sys
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import json

# Add backend to path
sys.path.insert(0, '/app/backend')

import numpy as np
import pandas as pd
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv('/app/backend/.env')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "pick_vision")


class VegasKillerBacktester:
    """
    Backtests the Vegas Killer model against historical game outcomes.
    
    Simulates betting on player props using model predictions and
    measures performance against the 52.4% break-even threshold.
    """
    
    # Standard juice for -110 odds
    BREAK_EVEN_RATE = 0.524
    
    def __init__(self, db):
        self.db = db
        self.hub = db['nba_master_hub_2026']
        self.advanced_stats = db['bdl_advanced_stats']
        
        # Import model
        from services.vegas_killer_model import VegasKillerModel, VegasFeatureEngineer
        self.model = VegasKillerModel(db)
        self.feature_engineer = VegasFeatureEngineer(db)
        
        # Load models
        self.model.load_models()
    
    def _get_stat_value(self, game: Dict, stat_type: str) -> Optional[float]:
        """Extract stat value from game log."""
        stat_map = {
            'PTS': ['pts', 'points'],
            'REB': ['reb', 'rebounds', 'total_rebounds'],
            'AST': ['ast', 'assists'],
            '3PM': ['fg3m', 'three_pointers_made', 'threes'],
            'PRA': None,  # Calculated
            'PR': None,
            'PA': None,
            'RA': None,
        }
        
        if stat_type == 'PRA':
            pts = self._get_stat_value(game, 'PTS')
            reb = self._get_stat_value(game, 'REB')
            ast = self._get_stat_value(game, 'AST')
            if all(v is not None for v in [pts, reb, ast]):
                return pts + reb + ast
            return None
        
        keys = stat_map.get(stat_type, [])
        if not keys:
            return None
        
        for key in keys:
            if key in game and game[key] is not None:
                return float(game[key])
        
        return None
    
    def _simulate_line(self, actual: float, noise: float = 0.5) -> float:
        """
        Simulate a betting line based on actual outcome.
        
        In reality, lines are set before the game. For backtesting,
        we simulate lines around the player's recent averages with noise.
        """
        # Add random noise to simulate market line setting
        return round(actual + np.random.uniform(-noise, noise) * 2, 1)
    
    def run_backtest(
        self,
        stat_type: str = 'PTS',
        min_games: int = 20,
        test_games: int = 10,
        confidence_threshold: float = 55.0,
    ) -> Dict[str, Any]:
        """
        Run backtest for a specific stat type.
        
        Args:
            stat_type: Stat to backtest (PTS, REB, AST, 3PM, PRA)
            min_games: Minimum games required before testing
            test_games: Number of recent games to test per player
            confidence_threshold: Min probability to place a bet
        
        Returns:
            Backtest results with win rate, ROI, etc.
        """
        logger.info(f"Running backtest for {stat_type}...")
        logger.info(f"  Min games: {min_games}, Test games: {test_games}")
        logger.info(f"  Confidence threshold: {confidence_threshold}%")
        
        results = []
        
        # Get all players with sufficient history
        players = list(self.hub.find({
            'bdl_game_logs': {'$exists': True},
            f'bdl_game_logs.{min_games + test_games}': {'$exists': True}
        }))
        
        logger.info(f"Found {len(players)} players with sufficient history")
        
        # Get model components directly for manual prediction
        if stat_type not in self.model.models:
            logger.error(f"Model for {stat_type} not loaded")
            return {"error": f"Model for {stat_type} not loaded"}
        
        model = self.model.models[stat_type]
        scaler = self.model.scalers[stat_type]
        feature_cols = self.model.feature_cols[stat_type]
        
        for player in players:
            player_name = player.get('display_name') or player.get('player_name')
            bdl_id = player.get('bdl_id')
            logs = player.get('bdl_game_logs', [])
            
            if len(logs) < min_games + test_games:
                continue
            
            # Test on the most recent `test_games` games
            for i in range(test_games):
                target_game = logs[i]
                prior_games = logs[i+1:i+1+min_games]
                
                if len(prior_games) < 10:
                    continue
                
                # Get actual outcome
                actual = self._get_stat_value(target_game, stat_type)
                if actual is None:
                    continue
                
                # Calculate line from prior average (simulating market)
                # Use half-point lines like real sportsbooks to eliminate pushes
                prior_values = [
                    self._get_stat_value(g, stat_type) 
                    for g in prior_games[:5] 
                    if self._get_stat_value(g, stat_type) is not None
                ]
                if not prior_values:
                    continue
                    
                prior_avg = np.mean(prior_values)
                # Round to nearest 0.5 to simulate real sportsbook lines
                line = round(prior_avg * 2) / 2
                # Ensure it's a half-point (.5) to guarantee no pushes
                if line == int(line):
                    line += 0.5
                
                # Extract features directly
                features = self.feature_engineer.extract_features(
                    prior_games=prior_games,
                    stat_type=stat_type,
                    target_game=target_game,
                    line=line,
                    bdl_player_id=bdl_id,
                )
                
                if not features:
                    continue
                
                # Build feature vector
                try:
                    feature_vector = []
                    for col in feature_cols:
                        val = features.get(col, 0)
                        if val is None or np.isinf(val) or np.isnan(val):
                            val = 0
                        feature_vector.append(val)
                    
                    X = np.array([feature_vector])
                    X_scaled = scaler.transform(X)
                    
                    # Predict
                    predicted = model.predict(X_scaled)[0]
                    
                    # Calculate probabilities (simple edge-based)
                    edge = predicted - line
                    mae = self.model.metrics.get(stat_type, {}).get('test', {}).get('mae', 5)
                    
                    # Convert edge to probability using MAE as std deviation
                    z_score = edge / mae if mae > 0 else 0
                    from scipy import stats
                    prob_over = stats.norm.cdf(z_score) * 100
                    prob_under = 100 - prob_over
                    
                    # Determine bet direction based on confidence
                    bet_direction = None
                    bet_prob = None
                    
                    if prob_over >= confidence_threshold:
                        bet_direction = 'OVER'
                        bet_prob = prob_over
                    elif prob_under >= confidence_threshold:
                        bet_direction = 'UNDER'
                        bet_prob = prob_under
                    
                    # Check outcome - no pushes with half-point lines
                    actual_direction = 'OVER' if actual > line else 'UNDER'
                    
                    result = {
                        'player_name': player_name,
                        'game_date': target_game.get('date'),
                        'stat_type': stat_type,
                        'line': line,
                        'predicted': round(predicted, 2),
                        'actual': actual,
                        'edge': round(edge, 2),
                        'prob_over': round(prob_over, 1),
                        'prob_under': round(prob_under, 1),
                        'bet_direction': bet_direction,
                        'bet_prob': round(bet_prob, 1) if bet_prob else None,
                        'actual_direction': actual_direction,
                        'win': bet_direction == actual_direction if bet_direction else None,
                        'has_v2_data': features.get('has_v2_advanced', 0) == 1,
                    }
                    
                    results.append(result)
                    
                except Exception as e:
                    logger.debug(f"Prediction error for {player_name}: {e}")
                    continue
        
        # Calculate statistics
        df = pd.DataFrame(results)
        
        if df.empty:
            return {"error": "No valid predictions", "stat_type": stat_type}
        
        logger.info(f"Generated {len(df)} predictions")
        
        # Filter to actual bets (where confidence met threshold)
        bets = df[df['bet_direction'].notna()].copy()
        no_bets = df[df['bet_direction'].isna()]
        
        logger.info(f"Bets meeting {confidence_threshold}% threshold: {len(bets)}")
        
        # Calculate win rate - no pushes
        if len(bets) > 0:
            wins = bets['win'].sum()
            losses = len(bets) - wins
            win_rate = wins / len(bets) if len(bets) > 0 else 0
            
            # ROI calculation (assuming -110 odds)
            # Win pays 0.91, loss costs 1.0
            profit = wins * 0.91 - losses * 1.0
            roi = profit / len(bets) * 100 if len(bets) > 0 else 0
        else:
            wins = losses = 0
            win_rate = 0
            roi = 0
        
        # Prediction accuracy (regardless of betting)
        df['pred_error'] = abs(df['predicted'] - df['actual'])
        mae = df['pred_error'].mean()
        
        # Direction accuracy (did we predict over/under correctly?)
        df['pred_direction'] = df.apply(
            lambda r: 'OVER' if r['predicted'] > r['line'] else 'UNDER', axis=1
        )
        df['direction_correct'] = df['pred_direction'] == df['actual_direction']
        direction_accuracy = df['direction_correct'].mean() * 100
        
        # V2 data impact
        v2_data = df[df['has_v2_data'] == True]
        no_v2_data = df[df['has_v2_data'] == False]
        
        v2_accuracy = v2_data['direction_correct'].mean() * 100 if len(v2_data) > 0 else 0
        no_v2_accuracy = no_v2_data['direction_correct'].mean() * 100 if len(no_v2_data) > 0 else 0
        
        return {
            "stat_type": stat_type,
            "confidence_threshold": confidence_threshold,
            "total_predictions": len(df),
            "total_bets": len(bets),
            "wins": int(wins),
            "losses": int(losses),
            "win_rate": round(win_rate * 100, 2),
            "break_even": self.BREAK_EVEN_RATE * 100,
            "edge_vs_break_even": round((win_rate - self.BREAK_EVEN_RATE) * 100, 2),
            "roi_percent": round(roi, 2),
            "prediction_mae": round(mae, 2),
            "direction_accuracy": round(direction_accuracy, 2),
            "v2_data_accuracy": round(v2_accuracy, 2),
            "no_v2_data_accuracy": round(no_v2_accuracy, 2),
            "v2_samples": len(v2_data),
            "profitable": win_rate > self.BREAK_EVEN_RATE,
            "sample_results": results[:20],  # First 20 for inspection
        }
    
    def run_full_backtest(
        self,
        stat_types: List[str] = None,
        confidence_levels: List[float] = None,
    ) -> Dict[str, Any]:
        """
        Run comprehensive backtest across all stat types and confidence levels.
        """
        if stat_types is None:
            stat_types = ['PTS', 'REB', 'AST', '3PM', 'PRA']
        
        if confidence_levels is None:
            confidence_levels = [50.0, 55.0, 60.0, 65.0, 70.0]
        
        results = {}
        
        for stat_type in stat_types:
            results[stat_type] = {}
            
            for conf in confidence_levels:
                logger.info(f"\n{'='*50}")
                logger.info(f"Backtesting {stat_type} @ {conf}% confidence")
                logger.info(f"{'='*50}")
                
                backtest = self.run_backtest(
                    stat_type=stat_type,
                    confidence_threshold=conf,
                )
                
                results[stat_type][f"conf_{int(conf)}"] = {
                    "total_bets": backtest.get('total_bets', 0),
                    "win_rate": backtest.get('win_rate', 0),
                    "roi": backtest.get('roi_percent', 0),
                    "profitable": backtest.get('profitable', False),
                    "edge": backtest.get('edge_vs_break_even', 0),
                }
                
                logger.info(f"  Bets: {backtest.get('total_bets', 0)}")
                logger.info(f"  Win Rate: {backtest.get('win_rate', 0)}%")
                logger.info(f"  ROI: {backtest.get('roi_percent', 0)}%")
                logger.info(f"  Profitable: {backtest.get('profitable', False)}")
        
        return results


def main():
    logger.info("=" * 70)
    logger.info("VEGAS KILLER BACKTESTING SIMULATION")
    logger.info("=" * 70)
    
    # Connect to MongoDB
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]
    
    # Create backtester
    backtester = VegasKillerBacktester(db)
    
    # Run full backtest
    results = {}
    
    stat_types = ['PTS', 'REB', 'AST', '3PM', 'PRA']
    
    for stat_type in stat_types:
        logger.info(f"\n{'='*60}")
        logger.info(f"BACKTESTING {stat_type}")
        logger.info(f"{'='*60}")
        
        result = backtester.run_backtest(
            stat_type=stat_type,
            min_games=15,
            test_games=5,
            confidence_threshold=55.0,
        )
        
        results[stat_type] = result
        
        logger.info(f"\n{stat_type} Results:")
        logger.info(f"  Total Predictions: {result.get('total_predictions', 0)}")
        logger.info(f"  Total Bets: {result.get('total_bets', 0)}")
        logger.info(f"  Win Rate: {result.get('win_rate', 0)}%")
        logger.info(f"  Break-Even: {result.get('break_even', 52.4)}%")
        logger.info(f"  Edge: {result.get('edge_vs_break_even', 0)}%")
        logger.info(f"  ROI: {result.get('roi_percent', 0)}%")
        logger.info(f"  Direction Accuracy: {result.get('direction_accuracy', 0)}%")
        logger.info(f"  V2 Data Accuracy: {result.get('v2_data_accuracy', 0)}%")
        logger.info(f"  PROFITABLE: {result.get('profitable', False)}")
    
    # Summary
    logger.info("\n" + "=" * 70)
    logger.info("BACKTEST SUMMARY")
    logger.info("=" * 70)
    
    print("\n| Stat | Bets | Win Rate | Edge | ROI | Profitable |")
    print("|------|------|----------|------|-----|------------|")
    
    for stat_type, data in results.items():
        profitable = "✅" if data.get('profitable') else "❌"
        print(f"| {stat_type} | {data.get('total_bets', 0)} | {data.get('win_rate', 0)}% | {data.get('edge_vs_break_even', 0)}% | {data.get('roi_percent', 0)}% | {profitable} |")
    
    # Save results
    output_path = '/app/backend/backtest_results.json'
    with open(output_path, 'w') as f:
        # Remove sample_results and convert numpy/bool types for JSON
        clean_results = {}
        for stat, data in results.items():
            clean_results[stat] = {}
            for k, v in data.items():
                if k == 'sample_results':
                    continue
                if isinstance(v, (np.bool_, bool)):
                    clean_results[stat][k] = bool(v)
                elif isinstance(v, (np.integer, np.floating)):
                    clean_results[stat][k] = float(v)
                else:
                    clean_results[stat][k] = v
        json.dump(clean_results, f, indent=2)
    
    logger.info(f"\nResults saved to {output_path}")
    
    return results


if __name__ == "__main__":
    main()

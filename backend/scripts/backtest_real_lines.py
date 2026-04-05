"""
Vegas Killer Backtest with REAL Lines
======================================
Backtests the Vegas Killer model against actual historical Vegas lines
from The Odds API, not simulated/fake lines.

This is the REAL test of profitability.
"""

import os
import sys
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import json

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


class RealLinesBacktester:
    """
    Backtests Vegas Killer model against REAL historical lines.
    
    Uses actual lines from The Odds API historical data
    matched with actual game outcomes from BDL.
    """
    
    BREAK_EVEN_RATE = 0.524  # -110 odds break-even
    
    def __init__(self, db):
        self.db = db
        self.hub = db['nba_master_hub_2026']
        self.historical_odds = db['historical_odds']
        self.backtest_games = db['backtest_game_logs']  # NEW: Use fetched historical stats
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
            'PRA': None,
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
    
    def _find_historical_line(
        self,
        player_name: str,
        stat_type: str,
        game_date: datetime,
    ) -> Optional[Dict]:
        """
        Find the actual Vegas line for this player/stat/game.
        
        Returns dict with line value and odds.
        """
        # Search window around game date
        start = game_date - timedelta(hours=24)
        end = game_date + timedelta(hours=6)
        
        # Try exact match first
        doc = self.historical_odds.find_one({
            "player_name": player_name,
            "stat_type": stat_type,
            "direction": "Over",
            "game_date": {"$gte": start, "$lte": end},
        })
        
        if doc:
            return {
                "line": doc.get('line'),
                "odds_american": doc.get('odds_american'),
                "bookmaker": doc.get('bookmaker'),
            }
        
        # Try fuzzy match on name
        doc = self.historical_odds.find_one({
            "player_name": {"$regex": player_name.split()[0], "$options": "i"},
            "stat_type": stat_type,
            "direction": "Over",
            "game_date": {"$gte": start, "$lte": end},
        })
        
        if doc:
            return {
                "line": doc.get('line'),
                "odds_american": doc.get('odds_american'),
                "bookmaker": doc.get('bookmaker'),
            }
        
        return None
    
    def run_backtest(
        self,
        stat_type: str = 'PTS',
        confidence_threshold: float = 55.0,
    ) -> Dict[str, Any]:
        """
        Run backtest using REAL historical lines matched with REAL game outcomes.
        """
        logger.info(f"Running REAL lines backtest for {stat_type}...")
        logger.info(f"  Confidence threshold: {confidence_threshold}%")
        
        results = []
        
        # Get model components
        if stat_type not in self.model.models:
            logger.error(f"Model for {stat_type} not loaded")
            return {"error": f"Model for {stat_type} not loaded"}
        
        model = self.model.models[stat_type]
        scaler = self.model.scalers[stat_type]
        feature_cols = self.model.feature_cols[stat_type]
        
        # Get all historical lines for this stat type (Over lines only)
        lines = list(self.historical_odds.find({
            "stat_type": stat_type,
            "direction": "Over",
        }))
        
        logger.info(f"Found {len(lines)} historical lines for {stat_type}")
        
        # Group by player name
        from collections import defaultdict
        lines_by_player = defaultdict(list)
        for line in lines:
            player_name = line.get('player_name')
            lines_by_player[player_name].append(line)
        
        logger.info(f"Lines for {len(lines_by_player)} unique players")
        
        matched = 0
        
        for player_name, player_lines in lines_by_player.items():
            # Find matching game stats for this player
            for line_doc in player_lines:
                game_date = line_doc.get('game_date')
                real_line = line_doc.get('line')
                real_odds = line_doc.get('odds_american')
                
                if not game_date or not real_line:
                    continue
                
                # Find the actual game outcome
                date_str = game_date.strftime('%Y-%m-%d')
                
                game_stat = self.backtest_games.find_one({
                    "player_name": player_name,
                    "game_date": date_str,
                })
                
                if not game_stat:
                    # Try partial match
                    game_stat = self.backtest_games.find_one({
                        "player_name": {"$regex": f"^{player_name.split()[0]}", "$options": "i"},
                        "game_date": date_str,
                    })
                
                if not game_stat:
                    continue
                
                # Get actual outcome
                actual = None
                if stat_type == 'PTS':
                    actual = game_stat.get('pts')
                elif stat_type == 'REB':
                    actual = game_stat.get('reb')
                elif stat_type == 'AST':
                    actual = game_stat.get('ast')
                elif stat_type == '3PM':
                    actual = game_stat.get('fg3m')
                elif stat_type == 'PRA':
                    pts = game_stat.get('pts') or 0
                    reb = game_stat.get('reb') or 0
                    ast = game_stat.get('ast') or 0
                    actual = pts + reb + ast
                
                if actual is None:
                    continue
                
                matched += 1
                
                # Get player's historical games for features
                # Find this player in our main hub
                hub_player = self.hub.find_one({
                    "$or": [
                        {"display_name": player_name},
                        {"player_name": player_name},
                        {"display_name": {"$regex": player_name.split()[0], "$options": "i"}},
                    ]
                })
                
                if not hub_player:
                    continue
                
                bdl_id = hub_player.get('bdl_id')
                prior_games = hub_player.get('bdl_game_logs', [])[:15]
                
                if len(prior_games) < 5:
                    continue
                
                # Extract features
                features = self.feature_engineer.extract_features(
                    prior_games=prior_games,
                    stat_type=stat_type,
                    line=real_line,
                    bdl_player_id=bdl_id,
                )
                
                if not features:
                    continue
                
                # Build feature vector and predict
                try:
                    feature_vector = []
                    for col in feature_cols:
                        val = features.get(col, 0)
                        if val is None or np.isinf(val) or np.isnan(val):
                            val = 0
                        feature_vector.append(val)
                    
                    X = np.array([feature_vector])
                    X_scaled = scaler.transform(X)
                    predicted = model.predict(X_scaled)[0]
                    
                    # Calculate edge and probability
                    edge = predicted - real_line
                    mae = self.model.metrics.get(stat_type, {}).get('test', {}).get('mae', 5)
                    
                    from scipy import stats
                    z_score = edge / mae if mae > 0 else 0
                    prob_over = stats.norm.cdf(z_score) * 100
                    prob_under = 100 - prob_over
                    
                    # Determine bet direction
                    bet_direction = None
                    bet_prob = None
                    
                    if prob_over >= confidence_threshold:
                        bet_direction = 'OVER'
                        bet_prob = prob_over
                    elif prob_under >= confidence_threshold:
                        bet_direction = 'UNDER'
                        bet_prob = prob_under
                    
                    # Check outcome (half point lines = no pushes)
                    actual_direction = 'OVER' if actual > real_line else 'UNDER'
                    
                    result = {
                        'player_name': player_name,
                        'game_date': date_str,
                        'stat_type': stat_type,
                        'real_line': real_line,
                        'real_odds': real_odds,
                        'predicted': round(predicted, 2),
                        'actual': actual,
                        'edge': round(edge, 2),
                        'prob_over': round(prob_over, 1),
                        'prob_under': round(prob_under, 1),
                        'bet_direction': bet_direction,
                        'bet_prob': round(bet_prob, 1) if bet_prob else None,
                        'actual_direction': actual_direction,
                        'win': bet_direction == actual_direction if bet_direction else None,
                    }
                    
                    results.append(result)
                    
                except Exception as e:
                    continue
        
        logger.info(f"Matched {matched} lines to game outcomes")
        
        # Calculate stats
        df = pd.DataFrame(results)
        
        if df.empty:
            return {
                "error": "No valid predictions with real lines",
                "stat_type": stat_type,
                "matched_games": matched,
            }
        
        logger.info(f"Generated {len(df)} predictions with REAL lines")
        
        # Filter to bets
        bets = df[df['bet_direction'].notna()].copy()
        logger.info(f"Bets meeting {confidence_threshold}% threshold: {len(bets)}")
        
        if len(bets) == 0:
            return {
                "stat_type": stat_type,
                "total_predictions": len(df),
                "total_bets": 0,
                "matched_games": matched,
                "message": "No bets met confidence threshold"
            }
        
        # Calculate win rate
        wins = bets['win'].sum()
        losses = len(bets) - wins
        win_rate = wins / len(bets)
        
        # ROI at -110
        profit = wins * 0.91 - losses * 1.0
        roi = profit / len(bets) * 100
        
        # Direction accuracy
        df['pred_direction'] = df.apply(
            lambda r: 'OVER' if r['predicted'] > r['real_line'] else 'UNDER', axis=1
        )
        df['direction_correct'] = df['pred_direction'] == df['actual_direction']
        direction_accuracy = df['direction_correct'].mean() * 100
        
        return {
            "stat_type": stat_type,
            "confidence_threshold": confidence_threshold,
            "data_source": "REAL_VEGAS_LINES",
            "matched_games": matched,
            "total_predictions": len(df),
            "total_bets": len(bets),
            "wins": int(wins),
            "losses": int(losses),
            "win_rate": round(win_rate * 100, 2),
            "break_even": self.BREAK_EVEN_RATE * 100,
            "edge_vs_break_even": round((win_rate - self.BREAK_EVEN_RATE) * 100, 2),
            "roi_percent": round(roi, 2),
            "direction_accuracy": round(direction_accuracy, 2),
            "profitable": win_rate > self.BREAK_EVEN_RATE,
            "sample_results": results[:10],
        }


def main():
    logger.info("=" * 70)
    logger.info("VEGAS KILLER BACKTEST WITH REAL LINES")
    logger.info("=" * 70)
    
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]
    
    # Check how many historical lines we have
    total_lines = db['historical_odds'].count_documents({})
    logger.info(f"Historical odds in database: {total_lines}")
    
    if total_lines == 0:
        logger.error("No historical odds data! Run fetch-date or fetch-range first.")
        return
    
    backtester = RealLinesBacktester(db)
    
    stat_types = ['PTS', 'REB', 'AST', '3PM']
    results = {}
    
    for stat_type in stat_types:
        logger.info(f"\n{'='*50}")
        logger.info(f"BACKTESTING {stat_type} WITH REAL LINES")
        logger.info(f"{'='*50}")
        
        result = backtester.run_backtest(
            stat_type=stat_type,
            confidence_threshold=55.0,
        )
        
        results[stat_type] = result
        
        if 'error' not in result:
            logger.info(f"\n{stat_type} Results (REAL LINES):")
            logger.info(f"  Total Bets: {result.get('total_bets', 0)}")
            logger.info(f"  Win Rate: {result.get('win_rate', 0)}%")
            logger.info(f"  ROI: {result.get('roi_percent', 0)}%")
            logger.info(f"  PROFITABLE: {result.get('profitable', False)}")
    
    # Summary
    logger.info("\n" + "=" * 70)
    logger.info("REAL LINES BACKTEST SUMMARY")
    logger.info("=" * 70)
    
    print("\n| Stat | Bets | Wins | Win Rate | ROI | Profitable |")
    print("|------|------|------|----------|-----|------------|")
    
    for stat_type, data in results.items():
        if 'error' in data or data.get('total_bets', 0) == 0:
            print(f"| {stat_type} | - | - | - | - | No data |")
        else:
            profitable = "✅" if data.get('profitable') else "❌"
            print(f"| {stat_type} | {data['total_bets']} | {data['wins']} | {data['win_rate']}% | {data['roi_percent']}% | {profitable} |")
    
    # Save results
    output_path = '/app/backend/backtest_real_lines.json'
    with open(output_path, 'w') as f:
        clean_results = {}
        for stat, data in results.items():
            clean_results[stat] = {k: v for k, v in data.items() if k != 'sample_results'}
            # Convert numpy types
            for k, v in clean_results[stat].items():
                if isinstance(v, (np.bool_, bool)):
                    clean_results[stat][k] = bool(v)
                elif isinstance(v, (np.integer, np.floating)):
                    clean_results[stat][k] = float(v)
        json.dump(clean_results, f, indent=2)
    
    logger.info(f"\nResults saved to {output_path}")
    
    return results


if __name__ == "__main__":
    main()

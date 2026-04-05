"""
Retrain Vegas Killer Model with V2 Advanced Stats + Historical Data
====================================================================
This script retrains all Vegas Killer models (PTS, REB, AST, 3PM, PRA)
using V2 Advanced Stats AND historical data from 2020-2025.

Usage:
    python scripts/retrain_vegas_killer.py
    
Options (via env vars):
    USE_HISTORICAL=1   Include 2020-2024 data (default: 1)
    MIN_GAMES=15       Min games required per player (default: 15)
"""

import os
import sys
import logging
import time

# Add backend to path
sys.path.insert(0, '/app/backend')

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


def main():
    logger.info("=" * 70)
    logger.info("VEGAS KILLER MODEL RETRAINING")
    logger.info("V2 Advanced Stats + Historical Data (2020-2025)")
    logger.info("=" * 70)
    
    # Connect to MongoDB
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]
    
    # Check data availability
    v2_count = db['bdl_advanced_stats'].count_documents({})
    historical_logs = db['bdl_historical_game_logs'].count_documents({})
    hub_count = db['nba_master_hub_2026'].count_documents({})
    
    logger.info(f"Data Sources:")
    logger.info(f"  - V2 Advanced Stats: {v2_count:,} records")
    logger.info(f"  - Historical Game Logs (2020-2024): {historical_logs:,} records")
    logger.info(f"  - Current Season Hub: {hub_count:,} players")
    
    use_historical = os.environ.get('USE_HISTORICAL', '1') == '1' and historical_logs > 0
    
    if use_historical:
        logger.info("\n✅ Including historical data (2020-2024) in training")
    else:
        logger.info("\n⚠️  Training with current season only")
    
    # Import model
    from services.vegas_killer_model import VegasKillerModel
    
    model = VegasKillerModel(db)
    
    # Stat types to train
    stat_types = ['PTS', 'REB', 'AST', '3PM', 'PRA']
    
    results = {}
    
    for stat_type in stat_types:
        logger.info(f"\n{'='*50}")
        logger.info(f"TRAINING {stat_type} MODEL")
        logger.info(f"{'='*50}")
        
        start_time = time.time()
        
        try:
            # Train with all features (no feature selection)
            # The train() method internally calls feature_engineer.build_training_dataset()
            metrics = model.train(
                stat_type, 
                model_type='xgboost',
                use_feature_selection=False,  # USE ALL FEATURES
                p_value_threshold=0.10
            )
            
            elapsed = time.time() - start_time
            
            results[stat_type] = {
                'samples': metrics.get('n_samples'),
                'features': metrics.get('n_features'),
                'train_mae': metrics.get('train', {}).get('mae'),
                'train_r2': metrics.get('train', {}).get('r2'),
                'test_mae': metrics.get('test', {}).get('mae'),
                'test_r2': metrics.get('test', {}).get('r2'),
                'time_seconds': round(elapsed, 1),
                'feature_importance': metrics.get('feature_importance', {}),
            }
            
            logger.info(f"{stat_type}: MAE={metrics.get('test', {}).get('mae')}, R²={metrics.get('test', {}).get('r2')}")
            
        except Exception as e:
            logger.error(f"Failed to train {stat_type}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            results[stat_type] = {'error': str(e)}
    
    # Save models
    logger.info("\nSaving models...")
    model.save_models()
    
    # Summary
    logger.info("\n" + "=" * 70)
    logger.info("TRAINING COMPLETE - SUMMARY")
    logger.info("=" * 70)
    
    print("\n| Stat | Samples | Features | Test MAE | Test R² | Time |")
    print("|------|---------|----------|----------|---------|------|")
    
    for stat_type, data in results.items():
        if 'error' in data:
            print(f"| {stat_type} | ERROR: {data['error'][:30]} | - | - | - | - |")
        else:
            print(f"| {stat_type} | {data['samples']:,} | {data['features']} | {data['test_mae']:.2f} | {data['test_r2']:.3f} | {data['time_seconds']}s |")
    
    # Feature importance analysis
    logger.info("\n" + "=" * 70)
    logger.info("TOP 10 FEATURES BY MODEL")
    logger.info("=" * 70)
    
    for stat_type, data in results.items():
        if 'feature_importance' in data and data['feature_importance']:
            logger.info(f"\n{stat_type}:")
            for i, (feat, imp) in enumerate(list(data['feature_importance'].items())[:10]):
                logger.info(f"  {i+1}. {feat}: {imp}")
    
    return results


if __name__ == "__main__":
    main()

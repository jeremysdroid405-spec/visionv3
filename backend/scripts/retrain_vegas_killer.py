"""
Retrain Vegas Killer Model with V2 Advanced Stats
===================================================
This script retrains all Vegas Killer models (PTS, REB, AST, 3PM, PRA)
using the new V2 Advanced Stats features.

Usage:
    python scripts/retrain_vegas_killer.py
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
    logger.info("VEGAS KILLER MODEL RETRAINING WITH V2 ADVANCED STATS")
    logger.info("=" * 70)
    
    # Connect to MongoDB
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]
    
    # Check V2 stats availability
    v2_count = db['bdl_advanced_stats'].count_documents({})
    logger.info(f"V2 Advanced Stats available: {v2_count} records")
    
    if v2_count == 0:
        logger.error("No V2 Advanced Stats found! Run fetch_historical_v2_stats.py first.")
        return
    
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
            metrics = model.train(stat_type, model_type='ensemble')
            
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
            print(f"| {stat_type} | ERROR | - | - | - | - |")
        else:
            print(f"| {stat_type} | {data['samples']} | {data['features']} | {data['test_mae']} | {data['test_r2']} | {data['time_seconds']}s |")
    
    # Feature importance analysis
    logger.info("\n" + "=" * 70)
    logger.info("TOP FEATURES BY MODEL")
    logger.info("=" * 70)
    
    for stat_type, data in results.items():
        if 'feature_importance' in data and data['feature_importance']:
            logger.info(f"\n{stat_type}:")
            for i, (feat, imp) in enumerate(list(data['feature_importance'].items())[:10]):
                logger.info(f"  {i+1}. {feat}: {imp}")
    
    return results


if __name__ == "__main__":
    main()

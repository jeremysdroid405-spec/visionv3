"""
Fetch Historical V2 Advanced Stats
===================================
Fetches V2 Advanced Stats for seasons 2020-2024 from BDL API.
Run this script in the background to populate the bdl_advanced_stats collection.

Usage:
    python scripts/fetch_historical_v2_stats.py
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
    logger.info("HISTORICAL V2 ADVANCED STATS FETCH")
    logger.info("=" * 70)
    
    # Connect to MongoDB
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]
    
    # Import fetcher
    from services.bdl_advanced_stats_fetcher import BDLAdvancedStatsFetcher
    
    fetcher = BDLAdvancedStatsFetcher(db)
    
    # Seasons to fetch (2020-2024)
    # Note: BDL uses season year for the start of the season
    # e.g., 2024 = 2024-25 season
    seasons = [2020, 2021, 2022, 2023, 2024]
    
    total_stats = 0
    results = {}
    
    for season in seasons:
        logger.info(f"\n{'='*50}")
        logger.info(f"FETCHING SEASON {season}-{season+1}")
        logger.info(f"{'='*50}")
        
        start_time = time.time()
        
        try:
            result = fetcher.fetch_advanced_stats_for_season(season)
            
            elapsed = time.time() - start_time
            stats_count = result.get('total_stats', 0)
            total_stats += stats_count
            
            results[season] = {
                'stats': stats_count,
                'pages': result.get('pages', 0),
                'time_seconds': round(elapsed, 1)
            }
            
            logger.info(f"Season {season}: {stats_count} stats in {elapsed:.1f}s")
            
        except Exception as e:
            logger.error(f"Failed to fetch season {season}: {e}")
            results[season] = {'error': str(e)}
    
    # Summary
    logger.info("\n" + "=" * 70)
    logger.info("FETCH COMPLETE - SUMMARY")
    logger.info("=" * 70)
    
    for season, data in results.items():
        if 'error' in data:
            logger.info(f"  {season}: ERROR - {data['error']}")
        else:
            logger.info(f"  {season}: {data['stats']} stats ({data['pages']} pages, {data['time_seconds']}s)")
    
    logger.info(f"\nTOTAL: {total_stats} advanced stats records")
    
    # Get final summary from DB
    summary = fetcher.get_stats_summary()
    logger.info(f"\nDatabase Summary:")
    logger.info(f"  Total records: {summary['total_stats']}")
    for season, data in summary.get('by_season', {}).items():
        logger.info(f"  Season {season}: {data['games']} games, {data['players']} players")
    
    return results


if __name__ == "__main__":
    main()

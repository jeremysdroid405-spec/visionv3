#!/usr/bin/env python3
"""
MLB Historical Backfill Script
Fetches 2024, 2025, 2026 season data from BDL and stores in mlb_historical_logs.
"""

import asyncio
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from motor.motor_asyncio import AsyncIOMotorClient
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "pick_vision")

async def main():
    # Connect to MongoDB
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    
    logger.info("=" * 70)
    logger.info("Starting MLB Historical Backfill for 2024, 2025, 2026")
    logger.info("=" * 70)
    
    # Import and run the backfill
    from services.mlb_vk_historical_backfill import MLBVKHistoricalBackfill
    
    backfill = MLBVKHistoricalBackfill(db)
    
    # Run for 2024, 2025, 2026 only
    result = await backfill.run_historical_backfill(
        seasons=[2024, 2025, 2026],
        save_to_db=True
    )
    
    logger.info("=" * 70)
    logger.info("BACKFILL COMPLETE")
    logger.info(f"  Success: {result.get('success')}")
    logger.info(f"  Seasons fetched: {result.get('seasons_fetched')}")
    logger.info(f"  Total stats: {result.get('total_stats')}")
    logger.info(f"  Players processed: {result.get('players_processed')}")
    logger.info(f"  Baselines calculated: {result.get('baselines_calculated')}")
    logger.info(f"  Duration: {result.get('duration_seconds')} seconds")
    if result.get('errors'):
        logger.error(f"  Errors: {result.get('errors')}")
    logger.info("=" * 70)
    
    client.close()
    return result

if __name__ == "__main__":
    result = asyncio.run(main())
    print(f"\nBackfill result: {result.get('success')}")

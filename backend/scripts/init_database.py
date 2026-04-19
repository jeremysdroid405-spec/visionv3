#!/usr/bin/env python3
"""
Database Initialization Script
==============================
Run this script after deploying to a new environment to:
1. Test MongoDB connection
2. Create necessary collections
3. Run initial data syncs to populate the database

Usage:
    python scripts/init_database.py

Environment Variables Required:
    MONGO_URL - MongoDB connection string
    DB_NAME - Database name (default: pick_vision)
    ODDS_API_KEY - The Odds API key
    BDL_API_KEY - BallDontLie API key
"""

import os
import sys
import asyncio
import logging
from datetime import datetime, timezone

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from services.config.collection_names import COLL

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def test_connection():
    """Test MongoDB connection."""
    from motor.motor_asyncio import AsyncIOMotorClient
    
    mongo_url = os.environ.get('MONGO_URL')
    db_name = os.environ.get('DB_NAME', 'pick_vision')
    
    if not mongo_url:
        logger.error("MONGO_URL not set in environment")
        return None
    
    logger.info(f"Connecting to MongoDB...")
    logger.info(f"URL: {mongo_url[:50]}..." if len(mongo_url) > 50 else f"URL: {mongo_url}")
    
    is_atlas = 'mongodb.net' in mongo_url or 'mongodb+srv' in mongo_url
    
    connection_opts = {
        'serverSelectionTimeoutMS': 30000,
        'connectTimeoutMS': 30000,
        'socketTimeoutMS': 60000,
        'maxPoolSize': 10,
        'retryWrites': True,
    }
    if is_atlas:
        connection_opts['tls'] = True
        logger.info("Atlas detected - TLS enabled")
    
    try:
        client = AsyncIOMotorClient(mongo_url, **connection_opts)
        db = client[db_name]
        
        # Test connection with a simple command
        await db.command('ping')
        logger.info(f"✓ Connected to database: {db_name}")
        
        # List existing collections
        collections = await db.list_collection_names()
        logger.info(f"✓ Existing collections: {collections}")
        
        return db
    except Exception as e:
        logger.error(f"✗ Connection failed: {e}")
        return None


async def sync_master_roster(db):
    """Sync player roster from BallDontLie API."""
    logger.info("\n" + "="*60)
    logger.info("STEP 1: Syncing Master Roster from BallDontLie...")
    logger.info("="*60)
    
    try:
        from services.engines.demon_goblin_engine import DemonGoblinEngine
        
        engine = DemonGoblinEngine(db)
        result = await engine.sync_master_roster()
        
        logger.info(f"✓ Players synced: {result.get('players_synced', 0)}")
        logger.info(f"✓ Teams found: {result.get('teams_found', 0)}")
        return True
    except Exception as e:
        logger.error(f"✗ Roster sync failed: {e}")
        return False


async def sync_bdl_game_logs(db):
    """Sync game-by-game stats for hit rate calculations."""
    logger.info("\n" + "="*60)
    logger.info("STEP 2: Syncing BDL Game Logs (for L5/L10 hit rates)...")
    logger.info("="*60)
    
    try:
        from services.bdl_game_logs_sync import BDLGameLogsSync
        
        sync_service = BDLGameLogsSync(db)
        result = await sync_service.sync_all_players(batch_size=10)
        
        logger.info(f"✓ Players synced: {result.get('players_synced', 0)}/{result.get('total_players', 0)}")
        logger.info(f"✓ Total games: {result.get('total_games', 0)}")
        return True
    except Exception as e:
        logger.error(f"✗ Game logs sync failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def sync_dvp_rankings(db):
    """Sync Defense vs Position rankings."""
    logger.info("\n" + "="*60)
    logger.info("STEP 3: Syncing DvP Rankings...")
    logger.info("="*60)
    
    try:
        from services.dvp_service import force_refresh_dvp, initialize_dvp_cache
        from services import dvp_service
        
        dvp_service._db_ref = db
        await force_refresh_dvp()
        await initialize_dvp_cache()
        
        logger.info("✓ DvP rankings synced")
        return True
    except Exception as e:
        logger.error(f"✗ DvP sync failed: {e}")
        return False


async def sync_odds_and_props(db):
    """Sync odds and props from The Odds API."""
    logger.info("\n" + "="*60)
    logger.info("STEP 4: Syncing Odds & Props from The Odds API...")
    logger.info("="*60)
    
    odds_key = os.environ.get('ODDS_API_KEY')
    if not odds_key:
        logger.warning("⚠ ODDS_API_KEY not set - skipping odds sync")
        return False
    
    try:
        from services.engines.demon_goblin_engine import DemonGoblinEngine
        
        engine = DemonGoblinEngine(db)
        result = await engine.sync_odds_to_mongo()
        
        logger.info(f"✓ Props synced: {result.get('total_props', 0)}")
        logger.info(f"✓ Unique players: {result.get('unique_players', 0)}")
        return True
    except Exception as e:
        logger.error(f"✗ Odds sync failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def create_indexes(db):
    """Create necessary database indexes for performance."""
    logger.info("\n" + "="*60)
    logger.info("STEP 5: Creating Database Indexes...")
    logger.info("="*60)
    
    try:
        # nba_master_hub_2026 indexes
        await db[COLL("master_hub", "nba")].create_index("display_name")
        await db[COLL("master_hub", "nba")].create_index("bdl_id")
        await db[COLL("master_hub", "nba")].create_index("team")
        logger.info("✓ nba_master_hub_2026 indexes created")
        
        # dg_cached_board indexes
        await db[COLL("board_cache", "nba")].create_index("player_name")
        await db[COLL("board_cache", "nba")].create_index([("player_name", 1), ("commence_time", 1)])
        logger.info("✓ dg_cached_board indexes created")
        
        # dvp_rankings indexes
        await db.dvp_rankings.create_index("type")
        logger.info("✓ dvp_rankings indexes created")
        
        return True
    except Exception as e:
        logger.error(f"✗ Index creation failed: {e}")
        return False


async def verify_data(db):
    """Verify data was populated correctly."""
    logger.info("\n" + "="*60)
    logger.info("VERIFICATION: Checking Data...")
    logger.info("="*60)
    
    checks = []
    
    # Check master hub
    hub_count = await db[COLL("master_hub", "nba")].count_documents({})
    checks.append(("nba_master_hub_2026", hub_count, hub_count > 0))
    
    # Check players with game logs
    with_logs = await db[COLL("master_hub", "nba")].count_documents({"bdl_game_logs": {"$exists": True, "$ne": []}})
    checks.append(("Players with game logs", with_logs, with_logs > 0))
    
    # Check cached board
    board_count = await db[COLL("board_cache", "nba")].count_documents({})
    checks.append(("dg_cached_board", board_count, board_count > 0))
    
    # Check DvP
    dvp_count = await db.dvp_rankings.count_documents({})
    checks.append(("dvp_rankings", dvp_count, dvp_count > 0))
    
    logger.info("\nData Summary:")
    all_passed = True
    for name, count, passed in checks:
        status = "✓" if passed else "✗"
        logger.info(f"  {status} {name}: {count} documents")
        if not passed:
            all_passed = False
    
    return all_passed


async def main():
    """Main initialization function."""
    logger.info("="*60)
    logger.info("PropVision Database Initialization")
    logger.info(f"Started at: {datetime.now(timezone.utc).isoformat()}")
    logger.info("="*60)
    
    # Test connection
    db = await test_connection()
    if not db:
        logger.error("\n✗ Cannot proceed without database connection")
        logger.error("Please check:")
        logger.error("  1. MONGO_URL is correct in .env")
        logger.error("  2. Your IP is whitelisted in Atlas")
        logger.error("  3. Network can reach MongoDB servers")
        sys.exit(1)
    
    # Run syncs
    results = {}
    
    results['roster'] = await sync_master_roster(db)
    results['game_logs'] = await sync_bdl_game_logs(db)
    results['dvp'] = await sync_dvp_rankings(db)
    results['odds'] = await sync_odds_and_props(db)
    results['indexes'] = await create_indexes(db)
    
    # Verify
    results['verified'] = await verify_data(db)
    
    # Summary
    logger.info("\n" + "="*60)
    logger.info("INITIALIZATION COMPLETE")
    logger.info("="*60)
    
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    logger.info(f"\nResults: {passed}/{total} steps completed successfully")
    
    for step, success in results.items():
        status = "✓" if success else "✗"
        logger.info(f"  {status} {step}")
    
    if passed == total:
        logger.info("\n✓ Database is ready for use!")
    else:
        logger.warning("\n⚠ Some steps failed - check logs above")
    
    logger.info(f"\nCompleted at: {datetime.now(timezone.utc).isoformat()}")


if __name__ == "__main__":
    asyncio.run(main())

"""
Index Management Script
=======================
Creates recommended indexes for optimal MongoDB performance.

Run with:
    python scripts/ensure_indexes.py
    
Or via mongosh:
    load('scripts/ensure_indexes.js')
"""

import asyncio
import os
import sys
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ASCENDING, DESCENDING

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "pick_vision")


# Index definitions: collection -> list of index specs
INDEX_DEFINITIONS = {
    # Master roster - player identity lookups
    "dg_master_roster": [
        {"keys": [("player_name", ASCENDING)], "name": "player_name_1"},
        {"keys": [("normalized_name", ASCENDING)], "name": "normalized_name_1"},
        {"keys": [("team_abbreviation", ASCENDING)], "name": "team_abbreviation_1"},
        {"keys": [("bdl_id", ASCENDING)], "name": "bdl_id_1"},
    ],
    
    # Master hub - primary player data
    "nba_master_hub_2026": [
        {"keys": [("display_name", ASCENDING)], "name": "display_name_1"},
        {"keys": [("bdl_id", ASCENDING)], "name": "bdl_id_1"},
        {"keys": [("nba_id", ASCENDING)], "name": "nba_id_1"},
        {"keys": [("team_abbreviation", ASCENDING)], "name": "team_abbreviation_1"},
        {"keys": [("espn_id", ASCENDING)], "name": "espn_id_1"},
    ],
    
    # Live props - prop lookups
    "dg_live_props": [
        {"keys": [("_composite_key", ASCENDING)], "name": "_composite_key_1", "unique": True, "sparse": True},
        {"keys": [("player_name", ASCENDING)], "name": "player_name_1"},
        {"keys": [("event_id", ASCENDING)], "name": "event_id_1"},
        {"keys": [("market", ASCENDING)], "name": "market_1"},
        {"keys": [("commence_time", DESCENDING)], "name": "commence_time_-1"},
        {"keys": [("bookmaker", ASCENDING), ("market", ASCENDING)], "name": "bookmaker_market_1"},
    ],
    
    # Cached board - frontend queries
    "dg_cached_board": [
        {"keys": [("player_name", ASCENDING)], "name": "player_name_1"},
        {"keys": [("team", ASCENDING)], "name": "team_1"},
    ],
    
    # Player photos
    "player_photos": [
        {"keys": [("player_name", ASCENDING)], "name": "player_name_1", "unique": True},
    ],
    
    # BDL player mapping
    "bdl_player_mapping": [
        {"keys": [("player_name", ASCENDING)], "name": "player_name_1"},
        {"keys": [("bdl_id", ASCENDING)], "name": "bdl_id_1"},
        {"keys": [("normalized_name", ASCENDING)], "name": "normalized_name_1"},
    ],
    
    # Odds API mapping
    "odds_api_mapping_master": [
        {"keys": [("player_name", ASCENDING)], "name": "player_name_1"},
        {"keys": [("odds_api_name", ASCENDING)], "name": "odds_api_name_1"},
    ],
    
    # Sync log
    "dg_sync_log": [
        {"keys": [("sync_type", ASCENDING)], "name": "sync_type_1"},
        {"keys": [("started_at", DESCENDING)], "name": "started_at_-1"},
        {"keys": [("status", ASCENDING)], "name": "status_1"},
    ],
    
    # DVP rankings
    "dvp_rankings": [
        {"keys": [("team", ASCENDING)], "name": "team_1"},
        {"keys": [("stat_type", ASCENDING)], "name": "stat_type_1"},
    ],
    
    # Events cache
    "dg_events_cache": [
        {"keys": [("event_id", ASCENDING)], "name": "event_id_1"},
        {"keys": [("commence_time", DESCENDING)], "name": "commence_time_-1"},
    ],
    
    # Injuries
    "bdl_injuries": [
        {"keys": [("player_name", ASCENDING)], "name": "player_name_1"},
        {"keys": [("team", ASCENDING)], "name": "team_1"},
    ],
    
    # Users
    "users": [
        {"keys": [("email", ASCENDING)], "name": "email_1", "unique": True},
    ],
}


async def ensure_indexes():
    """Create all required indexes."""
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    
    print(f"Connected to {DB_NAME}")
    print("=" * 60)
    
    for collection_name, indexes in INDEX_DEFINITIONS.items():
        print(f"\n{collection_name}:")
        collection = db[collection_name]
        
        # Get existing indexes
        existing = await collection.index_information()
        existing_names = set(existing.keys())
        
        for index_spec in indexes:
            name = index_spec["name"]
            keys = index_spec["keys"]
            unique = index_spec.get("unique", False)
            sparse = index_spec.get("sparse", False)
            
            if name in existing_names:
                print(f"  ✓ {name} (exists)")
            else:
                try:
                    await collection.create_index(
                        keys,
                        name=name,
                        unique=unique,
                        sparse=sparse,
                        background=True  # Non-blocking
                    )
                    print(f"  + {name} (created)")
                except Exception as e:
                    print(f"  ✗ {name} (error: {e})")
    
    print("\n" + "=" * 60)
    print("Index creation complete")
    
    client.close()


async def show_index_report():
    """Show current index status for all collections."""
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    
    print(f"Index Report for {DB_NAME}")
    print("=" * 60)
    
    collections = await db.list_collection_names()
    
    for col_name in sorted(collections):
        collection = db[col_name]
        indexes = await collection.index_information()
        
        # Get collection stats
        try:
            stats = await db.command("collStats", col_name)
            doc_count = stats.get("count", 0)
            size_mb = round(stats.get("size", 0) / 1024 / 1024, 2)
        except:
            doc_count = "?"
            size_mb = "?"
        
        print(f"\n{col_name} ({doc_count} docs, {size_mb} MB)")
        for name, spec in indexes.items():
            keys = spec.get("key", {})
            unique = "UNIQUE" if spec.get("unique") else ""
            print(f"  - {name}: {dict(keys)} {unique}")
    
    client.close()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="MongoDB Index Management")
    parser.add_argument("--report", action="store_true", help="Show index report only")
    args = parser.parse_args()
    
    if args.report:
        asyncio.run(show_index_report())
    else:
        asyncio.run(ensure_indexes())

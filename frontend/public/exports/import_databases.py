#!/usr/bin/env python3
"""
MongoDB Database Import Script
==============================
Imports the exported nba_props and pick_vision databases into your MongoDB instance.

Usage:
    python import_databases.py

Requirements:
    pip install pymongo

Environment Variables (or edit MONGO_URL below):
    MONGO_URL - Your MongoDB connection string
    
Example:
    export MONGO_URL="mongodb://localhost:27017"
    python import_databases.py
    
    # Or for MongoDB Atlas:
    export MONGO_URL="mongodb+srv://user:pass@cluster.xxxxx.mongodb.net"
    python import_databases.py
"""

import json
import os
import sys
import gzip
from datetime import datetime
from bson import ObjectId
from pymongo import MongoClient
from pymongo.errors import BulkWriteError

# ============================================================
# CONFIGURATION - Edit this if not using environment variable
# ============================================================
MONGO_URL = os.environ.get('MONGO_URL', 'mongodb://localhost:27017')

# Files to import (place in same directory as this script)
IMPORT_FILES = [
    'nba_props_export.json',
    'pick_vision_export.json',
]


def convert_mongo_types(obj):
    """Convert MongoDB extended JSON types back to native types."""
    if isinstance(obj, dict):
        # Handle ObjectId
        if '$oid' in obj and len(obj) == 1:
            return ObjectId(obj['$oid'])
        # Handle datetime
        if '$date' in obj and len(obj) == 1:
            date_str = obj['$date']
            try:
                # Try ISO format
                if 'T' in date_str:
                    return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                else:
                    return datetime.strptime(date_str, '%Y-%m-%d')
            except:
                return date_str
        # Recurse into dict
        return {k: convert_mongo_types(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_mongo_types(item) for item in obj]
    return obj


def load_export_file(filepath):
    """Load export file (supports both .json and .json.gz)."""
    print(f"\nLoading {filepath}...")
    
    # Check for gzipped version first
    gz_path = filepath + '.gz' if not filepath.endswith('.gz') else filepath
    json_path = filepath.replace('.gz', '') if filepath.endswith('.gz') else filepath
    
    if os.path.exists(gz_path):
        print(f"  Found compressed file: {gz_path}")
        with gzip.open(gz_path, 'rt', encoding='utf-8') as f:
            return json.load(f)
    elif os.path.exists(json_path):
        print(f"  Found JSON file: {json_path}")
        with open(json_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    else:
        print(f"  ERROR: File not found: {filepath}")
        return None


def import_database(client, export_data, drop_existing=True):
    """Import a database from export data."""
    db_name = export_data['database']
    collections = export_data['collections']
    
    print(f"\n{'='*60}")
    print(f"Importing database: {db_name}")
    print(f"{'='*60}")
    
    db = client[db_name]
    total_imported = 0
    
    for col_name, documents in collections.items():
        if not documents:
            print(f"  {col_name}: 0 documents (skipping)")
            continue
        
        # Convert MongoDB types
        converted_docs = [convert_mongo_types(doc) for doc in documents]
        
        collection = db[col_name]
        
        if drop_existing:
            # Drop and recreate for clean import
            collection.drop()
        
        try:
            if len(converted_docs) > 0:
                # Use insert_many with ordered=False to continue on errors
                result = collection.insert_many(converted_docs, ordered=False)
                imported_count = len(result.inserted_ids)
                print(f"  {col_name}: {imported_count} documents imported")
                total_imported += imported_count
        except BulkWriteError as e:
            # Some documents may have been inserted
            imported_count = e.details.get('nInserted', 0)
            print(f"  {col_name}: {imported_count} documents imported (some duplicates skipped)")
            total_imported += imported_count
        except Exception as e:
            print(f"  {col_name}: ERROR - {e}")
    
    print(f"\nTotal imported to {db_name}: {total_imported} documents")
    return total_imported


def create_indexes(client):
    """Create necessary indexes for performance."""
    print(f"\n{'='*60}")
    print("Creating indexes...")
    print(f"{'='*60}")
    
    # pick_vision indexes
    pv = client['pick_vision']
    
    try:
        pv.nba_master_hub_2026.create_index("display_name")
        pv.nba_master_hub_2026.create_index("bdl_id")
        pv.nba_master_hub_2026.create_index("team")
        print("  pick_vision.nba_master_hub_2026: indexes created")
    except Exception as e:
        print(f"  pick_vision.nba_master_hub_2026: {e}")
    
    try:
        pv.dg_cached_board.create_index("player_name")
        pv.dg_cached_board.create_index([("player_name", 1), ("commence_time", 1)])
        print("  pick_vision.dg_cached_board: indexes created")
    except Exception as e:
        print(f"  pick_vision.dg_cached_board: {e}")
    
    try:
        pv.dvp_rankings.create_index("type")
        print("  pick_vision.dvp_rankings: indexes created")
    except Exception as e:
        print(f"  pick_vision.dvp_rankings: {e}")
    
    try:
        pv.odds_api_mapping_master.create_index("odds_api_name")
        pv.odds_api_mapping_master.create_index("bdl_id")
        print("  pick_vision.odds_api_mapping_master: indexes created")
    except Exception as e:
        print(f"  pick_vision.odds_api_mapping_master: {e}")
    
    # nba_props indexes
    np = client['nba_props']
    
    try:
        np.nba_master_hub_2026.create_index("display_name")
        np.nba_master_hub_2026.create_index("bdl_id")
        print("  nba_props.nba_master_hub_2026: indexes created")
    except Exception as e:
        print(f"  nba_props.nba_master_hub_2026: {e}")


def verify_import(client):
    """Verify the import was successful."""
    print(f"\n{'='*60}")
    print("Verifying import...")
    print(f"{'='*60}")
    
    checks = []
    
    # Check pick_vision
    pv = client['pick_vision']
    checks.append(("pick_vision.nba_master_hub_2026", pv.nba_master_hub_2026.count_documents({})))
    checks.append(("pick_vision.dg_cached_board", pv.dg_cached_board.count_documents({})))
    checks.append(("pick_vision.odds_api_mapping_master", pv.odds_api_mapping_master.count_documents({})))
    checks.append(("pick_vision.dvp_rankings", pv.dvp_rankings.count_documents({})))
    
    # Check nba_props
    np = client['nba_props']
    checks.append(("nba_props.nba_master_hub_2026", np.nba_master_hub_2026.count_documents({})))
    
    print("\nDocument counts:")
    all_good = True
    for name, count in checks:
        status = "OK" if count > 0 else "EMPTY"
        print(f"  {name}: {count} ({status})")
        if count == 0:
            all_good = False
    
    return all_good


def main():
    print("="*60)
    print("PropVision Database Import")
    print(f"Started at: {datetime.now().isoformat()}")
    print("="*60)
    
    # Connect to MongoDB
    print(f"\nConnecting to MongoDB...")
    print(f"URL: {MONGO_URL[:50]}..." if len(MONGO_URL) > 50 else f"URL: {MONGO_URL}")
    
    try:
        client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=10000)
        # Test connection
        client.admin.command('ping')
        print("Connected successfully!")
    except Exception as e:
        print(f"\nERROR: Could not connect to MongoDB!")
        print(f"Details: {e}")
        print("\nPlease check:")
        print("  1. MongoDB is running")
        print("  2. MONGO_URL is correct")
        print("  3. Your IP is whitelisted (for Atlas)")
        sys.exit(1)
    
    # Import each file
    total_docs = 0
    for filename in IMPORT_FILES:
        export_data = load_export_file(filename)
        if export_data:
            total_docs += import_database(client, export_data)
    
    # Create indexes
    create_indexes(client)
    
    # Verify
    success = verify_import(client)
    
    # Summary
    print(f"\n{'='*60}")
    print("IMPORT COMPLETE")
    print(f"{'='*60}")
    print(f"Total documents imported: {total_docs}")
    
    if success:
        print("\nAll databases imported successfully!")
        print("\nNext steps:")
        print("  1. Start your backend server")
        print("  2. Access the app and verify data loads correctly")
        print("  3. Run the sync jobs to get fresh odds data")
    else:
        print("\nWARNING: Some collections may be empty.")
        print("Check the logs above for errors.")
    
    print(f"\nCompleted at: {datetime.now().isoformat()}")
    client.close()


if __name__ == "__main__":
    main()

"""Hard-verification harness for the universal game-start scanner."""
import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
sys.path.insert(0, "/app/backend")

import motor.motor_asyncio
from pymongo import MongoClient


def main():
    db_name = os.environ["DB_NAME"]
    sync_client = MongoClient(os.environ["MONGO_URL"])
    sdb = sync_client[db_name]

    now = datetime.now(timezone.utc)
    past = now - timedelta(minutes=5)

    nba_doc = sdb.nba_prop_scores.find_one(
        {"version_tag": "final-nba", "tier": "safe_haven", "active": {"$ne": False}}
    )
    mlb_doc = sdb.mlb_prop_scores.find_one(
        {"version_tag": "final-mlb", "tier": "safe_haven", "active": {"$ne": False}}
    )

    nba_key = nba_doc["canonical_key"] if nba_doc else None
    mlb_key = mlb_doc["canonical_key"] if mlb_doc else None
    print(f"Setup: real NBA prop = {nba_key}")
    print(f"Setup: real MLB prop = {mlb_key}")

    if nba_key:
        sdb.nba_prop_scores.update_one(
            {"canonical_key": nba_key, "version_tag": "final-nba"},
            {"$set": {"game_start_utc": past, "active": True, "inactive_reason": None}},
        )
    if mlb_key:
        sdb.mlb_prop_scores.update_one(
            {"canonical_key": mlb_key, "version_tag": "final-mlb"},
            {"$set": {"game_start_utc": past, "active": True, "inactive_reason": None}},
        )

    print()
    print("=== BEFORE scan ===")
    if nba_key:
        d = sdb.nba_prop_scores.find_one({"canonical_key": nba_key})
        print(f"  nba_prop_scores: active={d.get('active')} game_start_utc={d.get('game_start_utc')}")
    if mlb_key:
        d = sdb.mlb_prop_scores.find_one({"canonical_key": mlb_key})
        print(f"  mlb_prop_scores: active={d.get('active')} game_start_utc={d.get('game_start_utc')}")

    async def run():
        from services.board.scanner import scan_all
        from services.board.reader import get_board
        amdb = motor.motor_asyncio.AsyncIOMotorClient(os.environ["MONGO_URL"])[db_name]
        stats = await scan_all(amdb)
        print()
        print("=== SCANNER STATS ===")
        for sport, s in stats.items():
            print(f"  {sport}: {s}")

        print()
        print("=== AFTER scan ===")
        for coll, key in (("nba_prop_scores", nba_key), ("mlb_prop_scores", mlb_key)):
            if not key:
                continue
            d = sdb[coll].find_one({"canonical_key": key})
            print(f"  {coll}: active={d.get('active')} inactive_reason={d.get('inactive_reason')} active_changed_at={d.get('active_changed_at')}")

        print()
        print("=== BOARD READER EXCLUSION CHECK ===")
        for sport, key in (("nba", nba_key), ("mlb", mlb_key)):
            if not key:
                continue
            picks = await get_board(amdb, sport=sport, tier="safe_haven", limit=50)
            present = any(p.get("canonical_key") == key for p in picks)
            flag = "PASS" if not present else "FAIL"
            print(f"  [{flag}] {sport} safe_haven board: {len(picks)} picks, tipped-off prop present={present} (expected False)")

    asyncio.run(run())


if __name__ == "__main__":
    main()

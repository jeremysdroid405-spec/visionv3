#!/usr/bin/env python3
"""
NBA 2025-season Ghost Data Fix
===============================
1. For each player: merge bdl_game_logs (live sync, real data) into history.2025_season
2. Dedupe by game_id, prefer the entry with actual minutes/pts
3. Re-sort by date descending
"""
import time
from pymongo import MongoClient, UpdateOne
import os

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "pick_vision")


def is_real_game(log):
    """A game counts as real if the player actually played."""
    mins = log.get("min", "00")
    if isinstance(mins, str):
        return mins not in ("00", "", "0")
    return mins and mins > 0


def main():
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]
    hub = db.nba_master_hub_2026

    total = hub.count_documents({"history": {"$exists": True}})
    print(f"Processing {total} players...")

    ops = []
    fixed = 0
    start = time.time()

    for doc in hub.find(
        {"history": {"$exists": True}},
        {"_id": 1, "display_name": 1, "history.2025_season": 1, "bdl_game_logs": 1}
    ):
        s2025 = doc.get("history", {}).get("2025_season", [])
        bdl = doc.get("bdl_game_logs", [])

        if not bdl:
            continue

        # Build game_id -> best log map
        merged = {}

        # First add history entries
        for log in s2025:
            gid = log.get("game_id")
            if gid:
                merged[gid] = log

        # Then overlay bdl_game_logs (live sync = authoritative for current season)
        for log in bdl:
            gid = log.get("game_id")
            if not gid:
                continue
            existing = merged.get(gid)
            # Replace if bdl entry is real and existing is ghost
            if not existing or (is_real_game(log) and not is_real_game(existing)):
                merged[gid] = log
            # Also replace if existing has no pts but bdl does
            elif existing and (existing.get("pts") in (0, None)) and log.get("pts", 0) > 0:
                merged[gid] = log

        # Filter out DNPs and sort by date desc
        clean_logs = [v for v in merged.values() if is_real_game(v)]
        clean_logs.sort(key=lambda x: x.get("date") or x.get("game_id") or 0, reverse=True)

        # Count change
        old_real = sum(1 for g in s2025 if is_real_game(g))
        new_real = len(clean_logs)

        if new_real != old_real:
            fixed += 1

        ops.append(UpdateOne(
            {"_id": doc["_id"]},
            {"$set": {
                "history.2025_season": clean_logs,
                "history_stats.2025_games": len(clean_logs),
            }}
        ))

        if len(ops) >= 100:
            hub.bulk_write(ops, ordered=False)
            elapsed = time.time() - start
            print(f"  {fixed} fixed / {len(ops)} processed | {elapsed:.1f}s")
            ops = []

    if ops:
        hub.bulk_write(ops, ordered=False)

    elapsed = time.time() - start
    print(f"\n[COMPLETE] {fixed} players fixed in {elapsed:.1f}s")

    # Verify Herro
    herro = hub.find_one(
        {"display_name": {"$regex": "Herro", "$options": "i"}},
        {"_id": 0, "display_name": 1, "history.2025_season": {"$slice": -10}}
    )
    if herro:
        logs = herro.get("history", {}).get("2025_season", [])
        print(f"\nHerro 2025_season (last 10 after fix):")
        for log in logs[-10:]:
            print(f"  {log.get('date','?')}  pts={log.get('pts')} fga={log.get('fga')} min={log.get('min')}")

    client.close()


if __name__ == "__main__":
    main()

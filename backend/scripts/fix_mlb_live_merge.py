#!/usr/bin/env python3
"""
MLB 2026 Live Season Merge
===========================
Merges bdl_game_logs (2026 live sync with dates) into history.2025_season.
Reverse-maps field names back to BDL raw keys.
Dedupes by game_id. Filters DNPs (at_bats=None AND ip=None).
"""
import time, os
from pymongo import MongoClient, UpdateOne

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "pick_vision")

REVERSE_MAP = {
    "rbis": "rbi", "home_runs": "hr", "walks": "bb",
    "strikeouts": "k", "innings_pitched": "ip",
    "pitcher_strikeouts": "p_k", "pitcher_walks": "p_bb",
    "hits_allowed": "p_hits", "earned_runs": "er",
    "batting_avg": "avg", "runs_allowed": "p_runs",
    "home_runs_allowed": "p_hr",
}


def is_real_mlb(log):
    return log.get("at_bats") is not None or log.get("ip") is not None


def main():
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]
    hub = db.mlb_master_hub_2026

    total = hub.count_documents({"bdl_game_logs": {"$exists": True, "$ne": []}})
    print(f"Processing {total} MLB players with bdl_game_logs...")

    ops = []
    merged_count = 0
    start = time.time()

    for doc in hub.find(
        {"bdl_game_logs": {"$exists": True, "$ne": []}},
        {"_id": 1, "player_name": 1, "history.2025_season": 1, "bdl_game_logs": 1}
    ):
        s2025 = doc.get("history", {}).get("2025_season", [])
        bdl = doc.get("bdl_game_logs", [])

        # Build game_id -> log map from existing 2025 season
        merged = {}
        for log in s2025:
            gid = log.get("game_id")
            if gid:
                merged[gid] = log

        # Overlay bdl_game_logs with reverse-mapped field names
        new_logs_added = 0
        for log in bdl:
            gid = log.get("game_id")
            if not gid:
                continue

            # Reverse-map to BDL raw keys
            mapped = {}
            for k, v in log.items():
                if k in REVERSE_MAP:
                    mapped[REVERSE_MAP[k]] = v
                else:
                    mapped[k] = v

            if gid not in merged:
                new_logs_added += 1
            merged[gid] = mapped

        if new_logs_added == 0:
            continue

        # Filter DNPs and sort
        clean = [v for v in merged.values() if is_real_mlb(v)]
        clean.sort(key=lambda x: x.get("game_id") or 0)

        ops.append(UpdateOne(
            {"_id": doc["_id"]},
            {"$set": {
                "history.2025_season": clean,
                "history_stats.2025_games": len(clean),
            }}
        ))
        merged_count += 1

        if len(ops) >= 100:
            hub.bulk_write(ops, ordered=False)
            print(f"  Merged {merged_count} players | {time.time()-start:.1f}s")
            ops = []

    if ops:
        hub.bulk_write(ops, ordered=False)

    print(f"\n[COMPLETE] {merged_count} MLB players merged with 2026 live data in {time.time()-start:.1f}s")
    client.close()


if __name__ == "__main__":
    main()

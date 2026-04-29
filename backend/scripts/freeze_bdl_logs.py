"""
Freeze MLB BDL Game Logs
========================
After running `rehydrate_bdl_logs.py`, snapshot
`mlb_master_hub_2026.bdl_game_logs` into a separate, frozen collection
(`mlb_bdl_logs_frozen`) keyed on `bdl_id`. Background MLB sync jobs
overwrite the hub's array, so this gives the retrain script a stable,
ID-keyed source.

Use:
    cd /app/backend && python3 scripts/freeze_bdl_logs.py
"""
import os, statistics, sys, time
from datetime import datetime, timezone
sys.path.insert(0, "/app/backend")
os.chdir("/app/backend")
from dotenv import load_dotenv; load_dotenv()
import pymongo
from pymongo import UpdateOne

cli = pymongo.MongoClient(os.environ["MONGO_URL"])
db = cli[os.environ["DB_NAME"]]

src = db.mlb_master_hub_2026
dst = db.mlb_bdl_logs_frozen
dst.create_index("bdl_id", unique=True)

t0 = time.time()
ops = []
counts = []
for d in src.find({"bdl_id": {"$ne": None}, "bdl_game_logs": {"$exists": True, "$ne": []}},
                    {"_id": 0, "bdl_id": 1, "player_name": 1, "bdl_game_logs": 1,
                     "team": 1, "is_pitcher": 1, "is_batter": 1,
                     "vs_left": 1, "vs_right": 1, "home_splits": 1, "away_splits": 1,
                     "mlb_id": 1, "mlbam_id": 1, "statcast_id": 1, "display_name": 1}):
    try:
        bid = int(d["bdl_id"])
    except (TypeError, ValueError):
        continue
    logs = d.get("bdl_game_logs") or []
    counts.append(len(logs))
    ops.append(UpdateOne(
        {"bdl_id": bid},
        {"$set": {**d, "bdl_id": bid,
                    "frozen_at": datetime.now(timezone.utc).isoformat(),
                    "frozen_log_count": len(logs)}},
        upsert=True,
    ))
print(f"Preparing {len(ops):,} freeze ops …")
B = 200
written = 0
for i in range(0, len(ops), B):
    chunk = ops[i:i + B]
    res = dst.bulk_write(chunk, ordered=False)
    written += res.upserted_count + res.modified_count
print(f"Frozen {written:,} player docs in {time.time() - t0:.1f}s")
print(f"  total log rows frozen: {sum(counts):,}")
print(f"  mean logs/player: {statistics.mean(counts):.1f}")
print(f"  median: {statistics.median(counts):.0f}")
print(f"  max: {max(counts)}")

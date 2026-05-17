"""Layer-3-only rebuild — no Phase 2c, no model held past inference.

Tight memory profile for 05-06 (37,691 rows). The previous attempt to
run Layer-3 + Phase 2c in one process exceeded the pod RAM ceiling
while mongod's WT cache held ~half. This script does ONLY Layer-3,
then exits — orphan workers reaped at process exit.

Usage:
    python audits/path_a_layer3_only.py 2026-05-06
"""
import os
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")
os.environ.setdefault("XGBOOST_N_THREADS", "1")

import sys
import asyncio
import gc

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient
from services.replay.mlb_replay_engine import (
    replay_date, OUT_COLL, STATUS_COLL, SOURCE_VERSION,
)


async def main(date: str) -> None:
    snap = f"{date}T11:00:00Z"
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    # Clear prior status / partial rows for this date so force=True
    # starts clean.
    n_purged_out = (await db[OUT_COLL].delete_many(
        {"game_date": date})).deleted_count
    n_purged_st = (await db[STATUS_COLL].delete_many(
        {"game_date": date})).deleted_count
    print(f"[purge] outputs={n_purged_out}  status={n_purged_st}")

    print(f"\n=== {date} Layer-3 replay_date(force=True) ===")
    summary = await replay_date(
        db, date, snapshot_iso=snap, force=True,
        mem_limit_mb=5000,
    )
    print(f"summary: {summary}")

    # Source-version assertion
    sample = await db[OUT_COLL].find_one(
        {"game_date": date}, {"_id": 0, "source_version": 1})
    sv = (sample or {}).get("source_version")
    print(f"stamped source_version: {sv}")
    assert sv == SOURCE_VERSION, f"unexpected: {sv}"

    # Streaming μ stats
    agg = db[OUT_COLL].aggregate([
        {"$match": {"game_date": date, "stat_family": "total_bases"}},
        {"$group": {"_id": None,
                      "n": {"$sum": 1},
                      "max_mu": {"$max": "$projection_mu"},
                      "avg_mu": {"$avg": "$projection_mu"},
                      "n_gt_4p5": {"$sum": {"$cond": [
                          {"$gt": ["$projection_mu", 4.5]}, 1, 0]}},
                      "n_gt_6p0": {"$sum": {"$cond": [
                          {"$gt": ["$projection_mu", 6.0]}, 1, 0]}}}},
    ])
    async for r in agg:
        print(f"\n[μ stats total_bases @ {date}]")
        print(f"  n={r['n']}  max={r['max_mu']:.3f}  avg={r['avg_mu']:.3f}  "
              f"n>4.5={r['n_gt_4p5']}  n>6.0={r['n_gt_6p0']}")

    # Top-5
    print(f"\n[top-5 μ rows]")
    async for r in db[OUT_COLL].find(
        {"game_date": date, "stat_family": "total_bases"},
        {"_id": 0, "player_name": 1, "line": 1, "side": 1,
         "projection_mu": 1}
    ).sort("projection_mu", -1).limit(5):
        print(f"  {r.get('player_name'):>22}  L={r.get('line'):>5}/{r.get('side'):>5}  "
              f"μ={r.get('projection_mu'):.3f}")

    client.close()
    gc.collect()


if __name__ == "__main__":
    date_arg = sys.argv[1] if len(sys.argv) > 1 else "2026-05-06"
    asyncio.run(main(date_arg))

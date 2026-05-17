"""Run Phase 2c production_replay_runner against the rebuilt 05-05
Layer-3 outputs. No Layer-3 re-run (status=completed short-circuits
the model load), so memory stays bounded.
"""
import os
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")
os.environ.setdefault("XGBOOST_N_THREADS", "1")

import sys
import asyncio
import time
import psutil

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient
from services.replay.production_replay_runner import run_production_replay


def _rss(): return psutil.Process().memory_info().rss / (1024 * 1024)


async def main():
    t0 = time.time()
    rss0 = _rss()
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    DATE = "2026-05-05"
    SNAP = f"{DATE}T11:00:00Z"

    print(f"[start] rss={rss0:.1f}MB")

    # Purge any stale Phase 2c outputs for this date
    r = await db.mlb_production_replay_outputs.delete_many({"game_date": DATE})
    print(f"[purge] phase2c outputs for {DATE}: {r.deleted_count}")

    # Run Phase 2c (Layer-3 short-circuits, no model load)
    summary = await run_production_replay(
        db, sport="mlb", game_date=DATE, snapshot_iso=SNAP,
        tier="war_zone", dry_run=False, force_layer3=False,
        notes="post_hydration_fix_05_05_v1.1",
    )
    print(f"\n[phase2c summary]")
    for k, v in summary.items():
        if k == "layer3_summary": continue
        print(f"  {k}: {v}")
    print(f"\nelapsed: {time.time()-t0:.2f}s  rss_end: {_rss():.1f}MB")

    # Streaming μ stats post-Phase-2c
    agg = db.mlb_production_replay_outputs.aggregate([
        {"$match": {"game_date": DATE, "stat_family": "total_bases"}},
        {"$group": {"_id": None,
                      "n": {"$sum": 1},
                      "max_mu": {"$max": "$projection_mu"},
                      "avg_mu": {"$avg": "$projection_mu"},
                      "n_gt_4p5": {"$sum": {"$cond": [
                          {"$gt": ["$projection_mu", 4.5]}, 1, 0]}},
                      "qualified": {"$sum": {"$cond": ["$gate_pass", 1, 0]}}}},
    ])
    async for r in agg:
        print(f"\n[Phase 2c μ stats total_bases @ {DATE}]")
        print(f"  n={r['n']}  max={r['max_mu']:.3f}  "
              f"avg={r['avg_mu']:.3f}  n>4.5={r['n_gt_4p5']}  "
              f"qualified={r['qualified']}")

    # Top-5 by μ
    print(f"\n[top-5 μ]")
    async for r in db.mlb_production_replay_outputs.find(
        {"game_date": DATE, "stat_family": "total_bases"},
        {"_id": 0, "player_name": 1, "line": 1, "side": 1,
         "projection_mu": 1, "gate_pass": 1, "grade_status": 1}
    ).sort("projection_mu", -1).limit(5):
        print(f"  {r.get('player_name'):>22}  L={r.get('line'):>5}/{r.get('side'):>5}  "
              f"μ={r.get('projection_mu'):.3f}  gate={r.get('gate_pass')}  "
              f"grade={r.get('grade_status')}")

    # Top-5 qualified by edge
    print(f"\n[top-5 qualified by edge]")
    async for r in db.mlb_production_replay_outputs.find(
        {"game_date": DATE, "stat_family": "total_bases", "gate_pass": True},
        {"_id": 0, "player_name": 1, "line": 1, "side": 1, "book": 1,
         "odds": 1, "projection_mu": 1, "edge": 1, "grade_status": 1,
         "actual_value": 1, "profit_units": 1}
    ).sort("edge", -1).limit(8):
        print(f"  {r.get('player_name'):>22}  L={r.get('line'):>5}/{r.get('side'):>5} "
              f"{r.get('book'):>10}@{r.get('odds')}  "
              f"μ={r.get('projection_mu'):.2f} edge={r.get('edge'):.3f}  "
              f"actual={r.get('actual_value')} → {r.get('grade_status')} "
              f"({r.get('profit_units'):+.2f}u)")

    client.close()


if __name__ == "__main__":
    asyncio.run(main())

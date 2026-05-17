"""Slate-wide μ sanity check post-hydration fix.

Stream-aggregate μ stats for total_bases on 2026-05-05 (rebuilt with
the new hydration code) and compare to legacy 2026-05-06 (still
contaminated). Read-only, low memory.
"""
import os
import sys
import statistics

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from pymongo import MongoClient

client = MongoClient(os.environ["MONGO_URL"])
db = client[os.environ["DB_NAME"]]

for date in ("2026-05-05", "2026-05-06"):
    print(f"\n=== {date} ===")
    for coll in ("mlb_replay_model_outputs",
                  "mlb_production_replay_outputs"):
        # Streaming stats — no list buildup
        agg = db[coll].aggregate([
            {"$match": {"game_date": date, "stat_family": "total_bases"}},
            {"$group": {"_id": None,
                          "n": {"$sum": 1},
                          "max_mu": {"$max": "$projection_mu"},
                          "min_mu": {"$min": "$projection_mu"},
                          "avg_mu": {"$avg": "$projection_mu"},
                          "n_gt_4p5": {"$sum": {"$cond": [
                              {"$gt": ["$projection_mu", 4.5]}, 1, 0]}},
                          "n_gt_6p0": {"$sum": {"$cond": [
                              {"$gt": ["$projection_mu", 6.0]}, 1, 0]}},
                          "version": {"$first": "$source_version"}}},
        ])
        for r in agg:
            print(f"  {coll}: n={r['n']}  max={r['max_mu']:.3f}  "
                  f"min={r['min_mu']:.3f}  avg={r['avg_mu']:.3f}  "
                  f"n>4.5={r['n_gt_4p5']}  n>6.0={r['n_gt_6p0']}  "
                  f"sv={r['version']}")

    # Top-5 μ to spot any remaining inflation
    print(f"  top-5 μ in mlb_replay_model_outputs:")
    for r in db.mlb_replay_model_outputs.find(
        {"game_date": date, "stat_family": "total_bases"},
        {"_id": 0, "player_name": 1, "line": 1, "side": 1,
         "projection_mu": 1, "raw_prediction": 1}
    ).sort("projection_mu", -1).limit(5):
        print(f"    {r.get('player_name'):>22}  "
              f"L={r.get('line'):>5}/{r.get('side'):>5}  "
              f"μ={r.get('projection_mu'):.3f}  "
              f"raw={r.get('raw_prediction'):.3f}")

client.close()

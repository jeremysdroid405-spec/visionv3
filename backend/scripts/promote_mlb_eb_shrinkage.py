"""Promote MLB EB shrinkage: trigger recompute and verify."""
from __future__ import annotations
import asyncio, os, sys, time
sys.path.insert(0, "/app/backend")
os.chdir("/app/backend")
from dotenv import load_dotenv; load_dotenv()

import numpy as np
from motor.motor_asyncio import AsyncIOMotorClient
from services.scoring.recompute import recompute_sport

VERSION_TAG = "final-mlb-rt"

WHITELIST = ("home_runs", "rbis", "total_bases", "hits+runs+rbis")


def _canon(stat: str) -> str:
    s = (stat or "").lower().replace(" ", "_")
    al = {"tb": "total_bases", "rbi": "rbis", "hr": "home_runs",
          "hrr": "hits+runs+rbis", "hits+runs+rbi": "hits+runs+rbis"}
    return al.get(s, s)


async def snapshot(db, label):
    print(f"\n=== {label} ===")
    n = await db.mlb_prop_scores.count_documents(
        {"version_tag": VERSION_TAG, "active": True})
    print(f"active docs: {n}")
    tier_counts = {}
    async for d in db.mlb_prop_scores.find(
        {"version_tag": VERSION_TAG, "active": True}, {"tier": 1, "_id": 0}):
        tier_counts[d.get("tier") or "-"] = tier_counts.get(d.get("tier") or "-", 0) + 1
    print(f"tiers: {tier_counts}")
    # proj mean by stat
    for stat in WHITELIST:
        vals = []
        async for d in db.mlb_prop_scores.find(
            {"version_tag": VERSION_TAG, "active": True,
             "model_projection": {"$ne": None}},
            {"stat_type": 1, "model_projection": 1, "_id": 0}):
            if _canon(d.get("stat_type") or "") == stat:
                vals.append(float(d["model_projection"]))
        if vals:
            a = np.array(vals)
            print(f"  {stat}: n={len(a)} mean={a.mean():.3f} max={a.max():.2f}")
    return tier_counts


async def main():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    before = await snapshot(db, "BEFORE (pre-EB rescore)")

    # Sanity: flag should be on now
    import services.scoring.mlb_eb_shrinkage as ebs
    print(f"\n[flag] MLB_HF_EB_SHRINKAGE_ENABLED env = "
          f"{os.environ.get('MLB_HF_EB_SHRINKAGE_ENABLED')!r}  "
          f"helper flag_enabled()={ebs.flag_enabled()}")

    # Trigger recompute
    print("\n--- recompute_sport(mlb, final-mlb-rt) ---")
    t0 = time.time()
    result = await recompute_sport(
        db=db, sport="mlb", version_tag=VERSION_TAG, dry_run=False,
    )
    print(f"took {time.time() - t0:.1f}s")
    print(f"result keys: {list(result.keys()) if isinstance(result, dict) else result}")

    after = await snapshot(db, "AFTER (post-EB rescore)")

    # Verify audit fields landed
    print("\n--- EB audit field presence ---")
    n_eb_applied = await db.mlb_prop_scores.count_documents(
        {"version_tag": VERSION_TAG, "active": True,
         "eb_shrinkage_applied": True})
    n_has_field = await db.mlb_prop_scores.count_documents(
        {"version_tag": VERSION_TAG, "active": True,
         "eb_shrinkage_applied": {"$exists": True}})
    n_has_raw = await db.mlb_prop_scores.count_documents(
        {"version_tag": VERSION_TAG, "active": True,
         "raw_hf_projection": {"$exists": True}})
    print(f"eb_shrinkage_applied field present on: {n_has_field} docs")
    print(f"eb_shrinkage_applied=True on: {n_eb_applied} docs")
    print(f"raw_hf_projection field present on: {n_has_raw} docs")

    # Projection = shrunk for whitelisted
    print("\n--- Verify: projection == eb_shrunk where applied ---")
    mismatches = 0
    checked = 0
    async for d in db.mlb_prop_scores.find(
        {"version_tag": VERSION_TAG, "active": True,
         "eb_shrinkage_applied": True},
        {"model_projection": 1, "eb_shrunk_projection": 1,
         "raw_hf_projection": 1, "stat_type": 1, "_id": 0}):
        checked += 1
        mp = d.get("model_projection")
        ebp = d.get("eb_shrunk_projection")
        if mp is None or ebp is None:
            continue
        if abs(float(mp) - float(ebp)) > 1e-3:
            mismatches += 1
    print(f"checked: {checked}  mismatches: {mismatches}")

    # Non-whitelist: eb_shrinkage_applied=False
    print("\n--- Verify: non-whitelist stats have eb_shrinkage_applied=False ---")
    bad = 0
    async for d in db.mlb_prop_scores.find(
        {"version_tag": VERSION_TAG, "active": True,
         "eb_shrinkage_applied": True},
        {"stat_type": 1, "_id": 0}):
        if _canon(d.get("stat_type") or "") not in WHITELIST:
            bad += 1
    print(f"non-whitelist docs with EB applied: {bad} (should be 0)")

    # Negative projections
    n_neg = await db.mlb_prop_scores.count_documents(
        {"version_tag": VERSION_TAG, "active": True,
         "model_projection": {"$lt": 0}})
    print(f"negative projections: {n_neg} (should be 0)")

    # Top-20 picks after promotion
    print("\n--- Top-20 tiered MLB picks after promotion (by rs2 desc) ---")
    picks = []
    async for d in db.mlb_prop_scores.find(
        {"version_tag": VERSION_TAG, "active": True,
         "tier": {"$in": ["safe_haven", "front_lines", "war_zone"]}},
        {"player_name": 1, "stat_type": 1, "line": 1, "recommendation": 1,
         "model_projection": 1, "raw_hf_projection": 1,
         "eb_shrinkage_applied": 1, "tier": 1, "edge_pct": 1,
         "ranking_score_v2": 1, "_id": 0}):
        picks.append(d)
    picks.sort(key=lambda p: (p.get("ranking_score_v2") or 0), reverse=True)
    for i, p in enumerate(picks[:20], 1):
        eb_flag = "EB" if p.get("eb_shrinkage_applied") else "raw"
        print(f"{i:2d}. {p.get('player_name'):20s} {p.get('stat_type'):20s} "
              f"{p.get('line'):>4} {p.get('recommendation'):5s} "
              f"proj={float(p.get('model_projection') or 0):5.2f} "
              f"raw={float(p.get('raw_hf_projection') or 0):5.2f} [{eb_flag}] "
              f"tier={p.get('tier'):12s} "
              f"rs2={float(p.get('ranking_score_v2') or 0):+.3f}")

    # Extreme outliers
    print("\n--- HR projections > 1.0 (pre-EB outlier pattern) ---")
    count_hi = 0
    async for d in db.mlb_prop_scores.find(
        {"version_tag": VERSION_TAG, "active": True,
         "model_projection": {"$gt": 1.0}},
        {"stat_type": 1, "player_name": 1, "model_projection": 1,
         "raw_hf_projection": 1, "_id": 0}):
        if _canon(d.get("stat_type") or "") == "home_runs":
            count_hi += 1
            print(f"  {d.get('player_name')}: proj={d['model_projection']:.2f} "
                  f"raw={float(d.get('raw_hf_projection') or 0):.2f}")
    print(f"total HR projections > 1.0: {count_hi}")

    print("\n=== DONE ===")


if __name__ == "__main__":
    asyncio.run(main())

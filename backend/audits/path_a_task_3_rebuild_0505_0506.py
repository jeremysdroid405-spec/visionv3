"""Path A Task 3 — Rebuild contaminated replay collections for 05-05 + 05-06.

Steps:
  1. Delete legacy rows in `mlb_replay_model_outputs`, `mlb_replay_model_status`
     for the two dates so a clean re-run repopulates everything.
  2. Run Layer-3 replay via `replay_date()` for 05-05 and 05-06 (force=True).
  3. Print pre/post μ-distribution stats to confirm no μ > 4.5 outliers.
  4. Run Phase 2c `run_production_replay` for both dates to refresh
     `mlb_production_replay_outputs`.
  5. Print Olson μ trace for both dates.
"""
import os
import sys
import asyncio
import statistics

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient

from services.replay.mlb_replay_engine import (
    replay_date, OUT_COLL, STATUS_COLL, SOURCE_VERSION,
)
from services.replay.production_replay_runner import run_production_replay


DATES = ["2026-05-05", "2026-05-06"]


async def _mu_stats(db, coll, date, label):
    cursor = db[coll].find(
        {"game_date": date, "stat_family": "total_bases"},
        {"_id": 0, "projection_mu": 1})
    mus = []
    async for r in cursor:
        m = r.get("projection_mu")
        if m is not None:
            mus.append(float(m))
    if not mus:
        print(f"     {label}: NO rows for {coll} on {date}")
        return
    mus_sorted = sorted(mus)
    n = len(mus_sorted)
    p95 = mus_sorted[int(0.95 * (n - 1))]
    p99 = mus_sorted[int(0.99 * (n - 1))]
    n_gt_4p5 = sum(1 for m in mus if m > 4.5)
    print(f"     {label} [{coll}, {date}, TB]: n={n}  max={max(mus):.3f}  "
          f"p99={p99:.3f}  p95={p95:.3f}  median={statistics.median(mus):.3f}  "
          f"n>4.5={n_gt_4p5}")


async def _olson_mu(db, coll, date):
    docs = await db[coll].find(
        {"game_date": date, "player_name_normalized": "matt olson",
         "stat_family": "total_bases", "line": {"$in": [0.5, 1.5, 2.5, 4.5]}},
        {"_id": 0, "line": 1, "side": 1, "book": 1, "projection_mu": 1}
    ).sort([("line", 1), ("book", 1)]).limit(20).to_list(None)
    for d in docs[:8]:
        print(f"       {coll}  {date}  L={d['line']}/{d['side']}/{d['book']:>14}  "
              f"μ={d.get('projection_mu')}")


async def main():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    print("[A] BEFORE — current stored μ distribution (legacy contaminated)")
    for d in DATES:
        await _mu_stats(db, OUT_COLL, d, "Layer-3 (legacy)")
        await _mu_stats(db, "mlb_production_replay_outputs", d, "Phase 2c (legacy)")

    print("\n[B] Purging legacy outputs for 05-05 + 05-06 (Layer 3 + status only)")
    for d in DATES:
        r1 = await db[OUT_COLL].delete_many({"game_date": d})
        r2 = await db[STATUS_COLL].delete_many({"game_date": d})
        print(f"     {d}: deleted Layer-3 outputs={r1.deleted_count}  "
              f"status={r2.deleted_count}")

    print("\n[C] Re-running Layer-3 replay_date() with fixed engine (force=True)")
    for d in DATES:
        snap = f"{d}T11:00:00Z"
        summary = await replay_date(db, d, snapshot_iso=snap, force=True)
        print(f"     {d}: summary = {summary}")
        # confirm new source_version is stamped
        sample = await db[OUT_COLL].find_one(
            {"game_date": d, "stat_family": "total_bases"},
            {"_id": 0, "source_version": 1, "scoring_config_version": 1})
        print(f"     {d}: stamped source_version = "
              f"{sample.get('source_version') if sample else 'NONE'}")
        assert (sample or {}).get("source_version") == SOURCE_VERSION, \
            f"source_version mismatch: {sample}"

    print("\n[D] AFTER — μ distribution post-fix")
    for d in DATES:
        await _mu_stats(db, OUT_COLL, d, "Layer-3 (fixed)")

    print("\n[E] Olson μ trace (Layer-3 fixed)")
    for d in DATES:
        await _olson_mu(db, OUT_COLL, d)

    print("\n[F] Re-running Phase 2c production_replay_runner for both dates")
    for d in DATES:
        snap = f"{d}T11:00:00Z"
        s = await run_production_replay(
            db, sport="mlb", game_date=d, snapshot_iso=snap,
            tier="war_zone", dry_run=False, force_layer3=False,
            notes=f"post_hydration_fix_2026_05_17",
        )
        print(f"     {d}: serial={s['serial']}  "
              f"scanned={s['rows_scanned']}  qualified={s['rows_qualified']}  "
              f"W/L/P={s['wins']}/{s['losses']}/{s['pushes']}  "
              f"HR={s['hit_rate_pct']}%  ROI={s['roi_pct']}%")

    print("\n[G] AFTER — Phase 2c μ distribution")
    for d in DATES:
        await _mu_stats(db, "mlb_production_replay_outputs", d, "Phase 2c (fixed)")

    print("\n[H] Olson trace in Phase 2c outputs")
    for d in DATES:
        await _olson_mu(db, "mlb_production_replay_outputs", d)

    client.close()
    print("\n[✓] Rebuild for 05-05 + 05-06 complete.")


if __name__ == "__main__":
    asyncio.run(main())

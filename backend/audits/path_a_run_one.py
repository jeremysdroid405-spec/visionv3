"""Replay date runner — one date at a time, memory-bounded.

Env-bounded threading to suppress XGBoost/sklearn multiprocessing
orphans that have been OOM'ing the pod. Run via:
    python audits/path_a_run_one.py 2026-05-05
    python audits/path_a_run_one.py 2026-05-06
"""
import os
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")
os.environ.setdefault("XGBOOST_N_THREADS", "1")

import sys
import asyncio

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient

from services.replay.mlb_replay_engine import replay_date, OUT_COLL, SOURCE_VERSION
from services.replay.production_replay_runner import run_production_replay
from services.mlb_high_friction_model import MLBHighFrictionModel

# ── Force-single-thread every XGBoost booster after pickle load ──
# Each model.set_param({"nthread": 1}) prevents OpenMP/forking workers.
_orig_load_models = MLBHighFrictionModel.load_models


def _safe_load_models(self, *a, **kw):
    res = _orig_load_models(self, *a, **kw)
    for stat, mdl in list(self.models.items()):
        try:
            mdl.set_params(n_jobs=1)
        except Exception:
            pass
        try:
            booster = mdl.get_booster()
            booster.set_param({"nthread": 1})
        except Exception:
            pass
    print(f"[mp-guard] forced n_jobs=1 / nthread=1 on "
          f"{len(self.models)} XGBoost models")
    return res


MLBHighFrictionModel.load_models = _safe_load_models


async def main(date: str) -> None:
    snap = f"{date}T11:00:00Z"
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    print(f"\n=== {date} : Layer-3 replay_date(force=True) ===")
    s = await replay_date(db, date, snapshot_iso=snap, force=True,
                          mem_limit_mb=4500)
    print(f"summary: {s}")

    sv = await db[OUT_COLL].find_one(
        {"game_date": date}, {"_id": 0, "source_version": 1})
    print(f"stamped source_version: {(sv or {}).get('source_version')}")
    assert (sv or {}).get("source_version") == SOURCE_VERSION

    print(f"\n=== {date} : Phase 2c run_production_replay ===")
    # Phase 2c reads from Layer-3, so reset its prior rows for this date
    res = await db.mlb_production_replay_outputs.delete_many(
        {"game_date": date})
    print(f"purged prior Phase 2c outputs: {res.deleted_count}")
    summary = await run_production_replay(
        db, sport="mlb", game_date=date, snapshot_iso=snap,
        tier="war_zone", dry_run=False, force_layer3=False,
        notes="post_hydration_fix_2026_05_17",
    )
    print(f"phase2c summary: serial={summary['serial']}  "
          f"scanned={summary['rows_scanned']}  "
          f"qualified={summary['rows_qualified']}  "
          f"W/L/P={summary['wins']}/{summary['losses']}/{summary['pushes']}  "
          f"HR={summary['hit_rate_pct']}%  ROI={summary['roi_pct']}%")

    # μ stats for total_bases
    import statistics
    mus = []
    n_olson = 0
    olson_mus = []
    async for r in db[OUT_COLL].find(
        {"game_date": date, "stat_family": "total_bases"},
        {"_id": 0, "projection_mu": 1, "player_name_normalized": 1},
    ):
        if r.get("projection_mu") is None: continue
        m = float(r["projection_mu"])
        mus.append(m)
        if r.get("player_name_normalized") == "matt olson":
            n_olson += 1
            olson_mus.append(m)
    if mus:
        mus_s = sorted(mus); n = len(mus_s)
        print(f"\n[μ stats for total_bases @ {date}]")
        print(f"  n={n}  max={max(mus):.3f}  p99={mus_s[int(0.99*(n-1))]:.3f}  "
              f"p95={mus_s[int(0.95*(n-1))]:.3f}  "
              f"median={statistics.median(mus):.3f}  "
              f"n>4.5={sum(1 for m in mus if m > 4.5)}")
    if olson_mus:
        print(f"  Olson rows: {n_olson}  μ range: "
              f"{min(olson_mus):.3f}–{max(olson_mus):.3f}  "
              f"unique: {sorted(set(round(m, 2) for m in olson_mus))}")

    client.close()


if __name__ == "__main__":
    date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    if not date_arg:
        print("usage: python audits/path_a_run_one.py YYYY-MM-DD")
        sys.exit(1)
    asyncio.run(main(date_arg))

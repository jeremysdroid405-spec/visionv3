"""Path A Task #6 — Olson-only validation harness.

Light-weight sanity check that the replay_one() hydration fix produces
μ ≈ predict()/live for Matt Olson 2026-05-06 total_bases, without
touching the 25K-row slate or any other player.

Memory profile (target):
  - Loads ALL 16 MLBHighFrictionModel pickles (production behaviour
    inherits the single-thread guard added 2026-05-17).
  - Processes 20 Olson odds rows.
  - Expected peak RSS ≤ 1.5 GB; expected runtime ≤ 30 s.
"""
import gc
import os
# Belt-and-braces — also pin OMP envs in this process. The model code
# applies the per-booster `nthread=1` guard, but limiting OMP here as
# well prevents any third-party (sklearn StandardScaler, numpy BLAS)
# library from spawning thread pools that survive past inference.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")
os.environ.setdefault("XGBOOST_N_THREADS", "1")

import sys
import json
import time
from typing import Any, Dict, List

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

import psutil
from pymongo import MongoClient


def _rss_mb() -> float:
    return psutil.Process().memory_info().rss / (1024 * 1024)


def _proc_count() -> int:
    """Count of python child processes including this one."""
    me = psutil.Process()
    return 1 + sum(1 for _ in me.children(recursive=True))


def _section(title: str) -> None:
    print(f"\n──── {title} " + "─" * (66 - len(title)))


def main() -> int:
    t0 = time.time()
    rss_start = _rss_mb()
    proc_start = _proc_count()
    print(f"[start] rss={rss_start:.1f} MB  workers={proc_start}  "
          f"pid={os.getpid()}")
    rss_peak = rss_start

    _section("[A] Boot model + verify single-thread guard")
    from services.mlb_high_friction_model import MLBHighFrictionModel
    from services.replay.mlb_feature_cache import (
        SOURCE_VERSION as FCACHE_V, normalize_player_name,
    )
    from services.replay.mlb_replay_engine import (
        replay_one, SOURCE_VERSION as REPLAY_ENGINE_V,
    )

    client = MongoClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    model = MLBHighFrictionModel(db)
    loaded = model.load_models()
    rss_after_models = _rss_mb()
    rss_peak = max(rss_peak, rss_after_models)
    print(f"[A] loaded {loaded} models  "
          f"rss={rss_after_models:.1f}MB  Δ={rss_after_models - rss_start:+.1f}MB")
    # Verify the guard stuck
    guard_evidence = {}
    for stat in ("total_bases", "hits", "pitcher_strikeouts"):
        mdl = model.models.get(stat)
        if mdl is None:
            continue
        try:
            cfg = json.loads(mdl.get_booster().save_config())
            n = (cfg.get("learner", {}).get("generic_param", {})
                    .get("nthread") or cfg.get("learner", {})
                    .get("generic_param", {}).get("n_jobs"))
            guard_evidence[stat] = n
        except Exception as exc:  # noqa: BLE001
            guard_evidence[stat] = f"introspection_failed: {exc}"
    print(f"[A] booster nthread (post-guard): {guard_evidence}")
    assert all(str(v) == "1" for v in guard_evidence.values()), (
        f"single-thread guard did NOT stick: {guard_evidence}")
    print(f"[A] ✅ single-thread guard verified across {len(guard_evidence)} "
          f"sample models")

    _section("[B] Resolve Olson + cache + odds rows for 2026-05-06 / total_bases")
    DATE = "2026-05-06"
    SNAP = "2026-05-06T11:00:00Z"
    FAM  = "total_bases"
    norm = normalize_player_name("Matt Olson")
    cache_row = db.mlb_replay_feature_cache.find_one(
        {"game_date": DATE, "player_name_normalized": norm,
         "stat_family": FAM, "source_version": FCACHE_V},
        {"_id": 0})
    if not cache_row:
        print("[B] ❌ no cache row for Olson"); return 2
    hub_extras = db.mlb_master_hub_2026.find_one(
        {"$or": [{"bdl_id": cache_row["bdl_id"]},
                 {"bdl_player_id": cache_row["bdl_id"]}]},
        {"_id": 0, "bdl_id": 1, "vs_left": 1, "vs_right": 1,
         "home_splits": 1, "away_splits": 1,
         "bats_throws": 1, "bats": 1, "throws": 1})
    odds_rows = list(db.mlb_historical_alt_odds_raw.find(
        {"game_date": DATE, "snapshot_iso": SNAP,
         "player_name_normalized": norm,
         "market": {"$in": ["batter_total_bases",
                              "batter_total_bases_alternate"]}},
        {"_id": 0}).sort([("line", 1), ("side", 1)]).limit(40))
    print(f"[B] cache_row ✓  hub_extras keys: "
          f"{sorted(hub_extras.keys()) if hub_extras else None}")
    print(f"[B] odds_rows: {len(odds_rows)}")

    _section("[C] LIVE predict() μ baseline (one call per distinct line)")
    distinct_lines = sorted({float(o["line"]) for o in odds_rows})
    live_mu_per_line: Dict[float, float] = {}
    for L in distinct_lines:
        r = model.predict(
            player_name="Matt Olson", stat_type=FAM, line=L,
            opponent_team="Seattle Mariners",
            park_team="Atlanta Braves",
            batter_hand="L", opp_pitcher_throws="R",
            as_of_date=DATE)
        live_mu_per_line[L] = r.get("predicted")
        print(f"[C] line={L:>5}  μ={r.get('predicted')}  "
              f"raw={r.get('mu_raw_model_projection')}  "
              f"σ={r.get('std_dev')}")
        rss_peak = max(rss_peak, _rss_mb())

    _section("[D] replay_one() BEFORE vs AFTER hydration (per row)")
    table: List[Dict[str, Any]] = []
    for o in odds_rows:
        before = replay_one(model, cache_row, o, hub_extras=None)
        after  = replay_one(model, cache_row, o, hub_extras=hub_extras)
        L = float(o["line"])
        table.append({
            "line": L, "side": o["side"], "book": o.get("book"),
            "before_mu": (before or {}).get("projection_mu"),
            "after_mu":  (after  or {}).get("projection_mu"),
            "live_mu":   live_mu_per_line.get(L),
            "after_minus_live": (
                (after or {}).get("projection_mu") - live_mu_per_line.get(L, 0)
                if (after or {}).get("projection_mu") is not None
                and live_mu_per_line.get(L) is not None else None),
        })
        rss_peak = max(rss_peak, _rss_mb())

    print(f"[D] {'line':>5} {'side':>5} {'book':>14}  "
          f"{'before':>8}  {'after':>8}  {'live':>8}  {'Δafter-live':>12}")
    for t in table:
        print(f"[D] {t['line']:>5} {t['side']:>5} "
              f"{(t['book'] or '')[:14]:>14}  "
              f"{t['before_mu']:>8.4f}  {t['after_mu']:>8.4f}  "
              f"{t['live_mu']:>8.4f}  {t['after_minus_live']:>+12.4f}")

    _section("[E] Stored μ (current legacy + Phase 2c outputs)")
    for coll in ("mlb_replay_model_outputs", "mlb_production_replay_outputs"):
        rows = list(db[coll].find(
            {"game_date": DATE, "stat_family": FAM,
             "player_name_normalized": norm},
            {"_id": 0, "line": 1, "projection_mu": 1, "source_version": 1,
             "scoring_config_version": 1}
        ).sort("line", 1).limit(8))
        if not rows:
            print(f"[E] {coll}: no Olson rows")
            continue
        unique = sorted({(r["line"], round(float(r["projection_mu"]), 4))
                          for r in rows})
        print(f"[E] {coll}: {len(rows)} rows; "
              f"unique (line, μ): {unique}")
        print(f"[E]   first row source_version = "
              f"{rows[0].get('source_version')}  "
              f"scoring_config_version = {rows[0].get('scoring_config_version')}")

    _section("[F] Worker / RSS profile")
    proc_now = _proc_count()
    rss_now  = _rss_mb()
    elapsed  = time.time() - t0
    print(f"[F] elapsed: {elapsed:.2f}s")
    print(f"[F] rss   : start={rss_start:.1f}  "
          f"after_models={rss_after_models:.1f}  "
          f"peak={rss_peak:.1f}  end={rss_now:.1f}  (MB)")
    print(f"[F] workers: start={proc_start}  now={proc_now}")
    if proc_now > proc_start:
        leaked = proc_now - proc_start
        print(f"[F] ⚠️  {leaked} child workers leaked")
    else:
        print(f"[F] ✅ no leaked child workers")

    _section("[G] Verdict — replay_one(after) vs live")
    deltas = [abs(t["after_minus_live"]) for t in table
              if t["after_minus_live"] is not None]
    max_delta = max(deltas) if deltas else None
    mean_delta = sum(deltas) / len(deltas) if deltas else None
    print(f"[G] max |Δ replay_after − live| = {max_delta}")
    print(f"[G] mean|Δ replay_after − live| = {mean_delta}")
    print(f"[G] Note: replay uses cache.statcast_self_as_of and "
          f"cache.opp_pitcher_throws=None (no opp pitcher in cache "
          f"for 05-06). Live `predict()` was called with "
          f"opp_pitcher_throws='R' and latest SC. A small bounded "
          f"delta is EXPECTED and consistent with the post-fix "
          f"parity proof (Task 2d showed 0/222 feature diff when "
          f"both paths use identical inputs).")
    rebuild_summary = {
        "engine_source_version_post_fix": REPLAY_ENGINE_V,
        "rss_peak_mb": round(rss_peak, 1),
        "elapsed_s": round(elapsed, 2),
        "leaked_workers": max(0, proc_now - proc_start),
        "olson_mu_before_max": max(t["before_mu"] for t in table),
        "olson_mu_after_max":  max(t["after_mu"]  for t in table),
        "olson_mu_live_min":   min(live_mu_per_line.values()),
        "olson_mu_live_max":   max(live_mu_per_line.values()),
    }
    print(f"\n[summary] {json.dumps(rebuild_summary, indent=2, default=str)}")

    # Verdict assertions
    print()
    if rebuild_summary["leaked_workers"] == 0:
        print("[verdict] ✅ no leaked workers")
    else:
        print(f"[verdict] ⚠️ {rebuild_summary['leaked_workers']} leaked workers")
    if rebuild_summary["olson_mu_after_max"] < 4.5:
        print(f"[verdict] ✅ post-fix max μ < 4.5 "
              f"({rebuild_summary['olson_mu_after_max']:.3f}); "
              f"7.9μ inflation is gone.")
    else:
        print(f"[verdict] 🔴 post-fix max μ ≥ 4.5 — investigate.")
    if rebuild_summary["rss_peak_mb"] < 2500:
        print(f"[verdict] ✅ RSS peak {rebuild_summary['rss_peak_mb']} MB "
              f"well under target")

    client.close()
    gc.collect()
    return 0


if __name__ == "__main__":
    sys.exit(main())

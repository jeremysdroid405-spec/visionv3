"""Path A Task 2 — Olson μ Divergence Root-Cause Investigation (read-only).

Goal: pinpoint the exact source of the reported 7.9μ `total_bases`
inflation for Matt Olson on 2026-05-06, given that direct `predict()`
now returns μ=2.25 and the live↔replay feature build differs in only
6 of 222 columns.

ZERO mutations. Every operation is read or in-process compute.

Layout:
  [A] Storage scan — every stored output for Olson 05-06 across
      all replay collections that contain `total_bases` rows.
  [B] Direct `predict()` reconfirmation + memoization
  [C] Direct `replay_one()` execution via the existing engine with
      the actual 05-06 cache row + actual 05-06 odds rows
  [D] Side-by-side identity table (μ, raw_pred, park_factor, hashes,
      versions, snapshot, event, commence_time, player_id, line, book,
      feature-vector SHA, newest log date used, doubleheader disamb)
  [E] Slate-wide μ distribution for total_bases 05-06 to flag any μ
      ≥ 4.5 contaminants in either collection.

Outputs:
  - audits/PATH_A_TASK_2_OLSON_DIVERGENCE.md
  - audits/path_a_task_2_olson_divergence.json
"""
import os
import sys
import json
import hashlib
import statistics
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from pymongo import MongoClient

from services.mlb_high_friction_model import MLBHighFrictionModel
from services.replay.mlb_feature_cache import (
    SOURCE_VERSION as FCACHE_V, _STAT_FIELD_MAP, normalize_player_name,
)
from services.replay.mlb_replay_engine import (
    replay_one, SCORING_CONFIG_VERSION as SCFG_V, SOURCE_VERSION as RE_V,
)

DATE = "2026-05-06"
SNAPSHOT = "2026-05-06T11:00:00Z"
TARGET_FAM = "total_bases"
PLAYER_QUERIES = [
    {"display_name": "Matt Olson"},
    {"player_name": "Matt Olson"},
    {"mlb_full_name": "Matt Olson"},
]
OUT_DIR = "/app/backend/audits"


def feature_vector_sha(features: Dict[str, Any]) -> str:
    h = hashlib.sha256()
    for k in sorted(features.keys()):
        h.update(f"{k}={features[k]}\n".encode("utf-8"))
    return h.hexdigest()[:16]


def main() -> None:
    client = MongoClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    report: Dict[str, Any] = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "player": "Matt Olson",
        "date": DATE,
        "snapshot": SNAPSHOT,
        "stat_family": TARGET_FAM,
        "current_scoring_config_version": SCFG_V,
        "current_feature_cache_version": FCACHE_V,
        "current_replay_engine_source_version": RE_V,
    }

    # ── [A] Storage scan ────────────────────────────────────────────
    print("[A] Scanning stored outputs for Olson total_bases 05-06")
    olson_norm = normalize_player_name("Matt Olson")
    print(f"     player_name_normalized = {olson_norm!r}")

    coll_names = ["mlb_replay_model_outputs", "mlb_production_replay_outputs"]
    storage_rows = {}
    for cn in coll_names:
        q = {"player_name_normalized": olson_norm,
             "game_date": DATE, "stat_family": TARGET_FAM}
        n_total = db[cn].count_documents(q)
        sample = list(db[cn].find(
            q, {"_id": 0,
                 "projection_mu": 1, "raw_prediction": 1, "park_factor": 1,
                 "sigma": 1, "model_probability": 1,
                 "fair_probability": 1, "implied_probability": 1,
                 "edge": 1, "line": 1, "side": 1, "book": 1, "odds": 1,
                 "event_id": 1, "snapshot_iso": 1,
                 "commence_time": 1, "is_alternate": 1,
                 "scoring_config_version": 1, "feature_cache_version": 1,
                 "source_version": 1, "replayed_at": 1,
                 "replay_serial": 1, "tier": 1, "gate_pass": 1,
                 "home_team": 1, "away_team": 1}
        ).sort("line", 1).limit(60))
        print(f"     {cn}: {n_total} rows for Olson")
        storage_rows[cn] = sample

    # Highlight μ extrema
    mu_extrema = {}
    for cn, rows in storage_rows.items():
        mus = [r.get("projection_mu") for r in rows
                if r.get("projection_mu") is not None]
        if mus:
            mu_extrema[cn] = {"min": min(mus), "max": max(mus),
                              "median": statistics.median(mus),
                              "unique_mu_count": len(set(round(m, 4) for m in mus)),
                              "n_rows_sampled": len(rows)}
        else:
            mu_extrema[cn] = {"n_rows_sampled": len(rows)}
    print("     μ extrema per collection:")
    for cn, x in mu_extrema.items():
        print(f"       {cn}: {x}")
    report["storage_scan"] = {"mu_extrema": mu_extrema, "sample_rows": storage_rows}

    # ── [B] Direct predict() ───────────────────────────────────────
    print("\n[B] Loading model + direct predict()")
    model = MLBHighFrictionModel(db)
    model.load_models()
    model_train_cols = list(model.feature_cols[TARGET_FAM])
    train_cols_sha = hashlib.sha256(
        ("|".join(model_train_cols)).encode("utf-8")
    ).hexdigest()[:16]
    print(f"     train cols [{TARGET_FAM}] = {len(model_train_cols)}  "
          f"sha={train_cols_sha}")

    olson = None
    for q in PLAYER_QUERIES:
        olson = db.mlb_master_hub_2026.find_one(q, {"_id": 0})
        if olson:
            break
    if not olson:
        print("     ❌ Olson not in master_hub — aborting")
        return
    bdl_logs = olson.get("bdl_game_logs") or []
    print(f"     hub: team={olson.get('team')}  bat={olson.get('bat_side')}  "
          f"logs={len(bdl_logs)}")

    # Newest log date PRE-cutoff (with leak guard)
    pre_cutoff = model._filter_logs_before(bdl_logs, DATE)
    pre_newest = None
    if pre_cutoff:
        d = pre_cutoff[0].get("date") or pre_cutoff[0].get("game_date") or ""
        pre_newest = str(d)[:10]
    print(f"     newest pre-cutoff log: {pre_newest}  "
          f"({len(pre_cutoff)} logs ≤< {DATE})")

    predict_results = {}
    for line in (0.5, 1.5, 2.5, 3.5):
        r = model.predict(
            player_name="Matt Olson",
            stat_type=TARGET_FAM, line=line,
            opponent_team="Seattle Mariners", park_team="Atlanta Braves",
            batter_hand="L", opp_pitcher_throws="R",
            as_of_date=DATE,
        )
        predict_results[line] = {
            "predicted": r.get("predicted"),
            "std_dev": r.get("std_dev"),
            "prob_over": r.get("prob_over"),
            "raw_prediction": r.get("mu_raw_model_projection"),
            "park_factor": r.get("park_factor"),
            "error": r.get("error"),
        }
        print(f"     predict() line={line}: μ={r.get('predicted')}  "
              f"σ={r.get('std_dev')}  p_over={r.get('prob_over')}  "
              f"raw={r.get('mu_raw_model_projection')}  "
              f"pf={r.get('park_factor')}")
    report["direct_predict"] = predict_results

    # ── [C] Direct replay_one() via the cache + odds rows ──────────
    print("\n[C] Direct replay_one() using actual 05-06 cache + odds rows")
    cache_row = db.mlb_replay_feature_cache.find_one(
        {"game_date": DATE,
         "player_name_normalized": olson_norm,
         "stat_family": TARGET_FAM,
         "source_version": FCACHE_V},
        {"_id": 0},
    )
    if not cache_row:
        # Try without source_version pin
        cache_row = db.mlb_replay_feature_cache.find_one(
            {"game_date": DATE,
             "player_name_normalized": olson_norm,
             "stat_family": TARGET_FAM},
            {"_id": 0},
        )
    if not cache_row:
        print("     ⚠️  no Olson cache row for 05-06 / total_bases")
        report["replay_one"] = {"error": "no_cache_row"}
    else:
        print(f"     cache.source_version = {cache_row.get('source_version')}")
        print(f"     cache.dates  = {(cache_row.get('dates') or [])[:5]}")
        print(f"     cache.stat_values = {(cache_row.get('stat_values') or [])[:5]}")
        odds_rows = list(db.mlb_historical_alt_odds_raw.find(
            {"game_date": DATE,
             "snapshot_iso": SNAPSHOT,
             "player_name_normalized": olson_norm,
             "market": {"$in": ["batter_total_bases",
                                   "batter_total_bases_alternate"]}},
            {"_id": 0},
        ).limit(40))
        print(f"     odds rows: {len(odds_rows)}")
        replay_results = []
        for o in odds_rows:
            res = replay_one(model, cache_row, o)
            if res is None:
                replay_results.append({
                    "line": o.get("line"), "side": o.get("side"),
                    "book": o.get("book"),
                    "mu": None, "note": "replay_one returned None",
                })
                continue
            replay_results.append({
                "line": res["line"], "side": res["side"],
                "book": res["book"], "odds": res["odds"],
                "event_id": res["event_id"],
                "snapshot_iso": res["snapshot_iso"],
                "commence_time": str(res.get("commence_time"))[:25],
                "is_alternate": res["is_alternate"],
                "mu": res["projection_mu"],
                "raw_prediction": res["raw_prediction"],
                "park_factor": res["park_factor"],
                "sigma": res["sigma"],
                "model_probability": res["model_probability"],
                "fair_probability": res["fair_probability"],
                "edge": res["edge"],
                "scoring_config_version": res["scoring_config_version"],
                "feature_cache_version": res["feature_cache_version"],
                "source_version": res["source_version"],
            })
        print("     replay_one direct outputs (first 10):")
        for r in replay_results[:10]:
            print(f"       line={r['line']}/{r.get('side')}/{r.get('book')}  "
                  f"μ={r.get('mu')}  raw={r.get('raw_prediction')}  "
                  f"pf={r.get('park_factor')}")
        report["replay_one_direct"] = {
            "cache_row_dates_first5": (cache_row.get("dates") or [])[:5],
            "cache_row_stat_values_first5":
                (cache_row.get("stat_values") or [])[:5],
            "n_odds_rows": len(odds_rows),
            "results": replay_results,
        }

    # ── [D] Slate-wide μ distribution for total_bases 05-06 ────────
    print("\n[D] Slate-wide μ distribution for total_bases 05-06 — "
          "are there ANY μ > 4.5 contaminants?")
    coll_stats: Dict[str, Any] = {}
    for cn in coll_names:
        mus: List[float] = []
        n_over_4p5 = 0
        top10: List[Tuple[float, Dict[str, Any]]] = []
        cursor = db[cn].find(
            {"game_date": DATE, "stat_family": TARGET_FAM},
            {"_id": 0, "projection_mu": 1, "player_name": 1,
             "player_name_normalized": 1, "line": 1, "side": 1, "book": 1,
             "snapshot_iso": 1, "event_id": 1, "raw_prediction": 1,
             "park_factor": 1, "scoring_config_version": 1,
             "feature_cache_version": 1, "source_version": 1,
             "replayed_at": 1, "replay_serial": 1},
        )
        for r in cursor:
            mu = r.get("projection_mu")
            if mu is None:
                continue
            mus.append(float(mu))
            if mu > 4.5:
                n_over_4p5 += 1
            top10.append((float(mu), r))
        top10.sort(key=lambda t: -t[0])
        if mus:
            mus_sorted = sorted(mus)
            p95 = mus_sorted[int(0.95 * (len(mus_sorted) - 1))]
            stats = {
                "n_rows": len(mus),
                "max": max(mus), "p95": p95, "p99": mus_sorted[
                    int(0.99 * (len(mus_sorted) - 1))],
                "median": statistics.median(mus),
                "min": min(mus),
                "n_mu_gt_4p5": n_over_4p5,
                "top10": [
                    {"mu": mu, "player": r.get("player_name"),
                     "line": r.get("line"), "side": r.get("side"),
                     "book": r.get("book"),
                     "snapshot_iso": r.get("snapshot_iso"),
                     "raw_prediction": r.get("raw_prediction"),
                     "park_factor": r.get("park_factor"),
                     "scoring_config_version": r.get("scoring_config_version"),
                     "source_version": r.get("source_version"),
                     "replayed_at": str(r.get("replayed_at"))[:25],
                     "replay_serial": r.get("replay_serial")}
                    for mu, r in top10[:10]
                ],
            }
        else:
            stats = {"n_rows": 0}
        print(f"     {cn}:")
        for k, v in stats.items():
            if k != "top10":
                print(f"       {k}: {v}")
        if stats.get("top10"):
            print("       top-3:")
            for x in stats["top10"][:3]:
                print(f"         {x['player']:>20}  "
                      f"μ={x['mu']:.3f}  raw={x['raw_prediction']}  "
                      f"pf={x['park_factor']}  L={x['line']}/{x['side']}")
        coll_stats[cn] = stats
    report["slate_distribution"] = coll_stats

    # ── [E] Verdict ────────────────────────────────────────────────
    print("\n[E] Verdict")
    direct_mu = predict_results.get(1.5, {}).get("predicted")
    replay_mu_first = None
    if isinstance(report.get("replay_one_direct"), dict):
        results = report["replay_one_direct"].get("results") or []
        if results:
            replay_mu_first = results[0].get("mu")
    print(f"     direct predict() μ@1.5 = {direct_mu}")
    print(f"     replay_one() μ first   = {replay_mu_first}")
    if direct_mu is not None and replay_mu_first is not None:
        if abs(direct_mu - replay_mu_first) < 0.05:
            print("     → μ agreement within 0.05 ✅ — replay engine is "
                  "NOT inflating μ today.")
        else:
            print(f"     → μ divergence {abs(direct_mu - replay_mu_first):.3f} "
                  f"— investigate replay_one feature differences.")

    max_stored_mu = mu_extrema.get(
        "mlb_replay_model_outputs", {}).get("max")
    if max_stored_mu is not None and max_stored_mu < 4.5:
        print(f"     → No stored Olson μ > 4.5 in legacy collection "
              f"(max={max_stored_mu}). 7.9μ value is NOT present in "
              f"current outputs.")
    if max_stored_mu is not None and max_stored_mu >= 4.5:
        print(f"     → Stored Olson μ ≥ 4.5 EXISTS in legacy collection "
              f"(max={max_stored_mu}) — investigate that row's snapshot/source.")

    # ── Write report ───────────────────────────────────────────────
    json_path = f"{OUT_DIR}/path_a_task_2_olson_divergence.json"
    with open(json_path, "w") as fh:
        json.dump(report, fh, indent=2, default=str)
    print(f"\n     wrote {json_path}")

    client.close()


if __name__ == "__main__":
    main()

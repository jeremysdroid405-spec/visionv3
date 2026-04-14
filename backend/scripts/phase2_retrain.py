#!/usr/bin/env python3
"""
Phase 2 Retrain: NBA reb/ast + MLB rbi/runs
Uses float32 + 50-player slice to prevent OOM.
"""
import sys
sys.path.insert(0, "/app/backend")

import numpy as np
from scripts.auto_feature_discovery import (
    raw_scan, build_feature_matrix, lasso_survivor_selection, run_pipeline,
    ORIGINAL_64_MLB, ORIGINAL_NBA
)
from pymongo import MongoClient
from sklearn.preprocessing import StandardScaler
import json, time, os

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "pick_vision")
DATA_DIR = "/app/backend/data"

PHASE2_TARGETS = [
    ("nba", "all", "reb"),
    ("nba", "all", "ast"),
    ("mlb", "batter", "rbi"),
    ("mlb", "batter", "runs"),
]


def run_phase2_pipeline(sport, player_type, target_stat):
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]
    hub_name = "mlb_master_hub_2026" if sport == "mlb" else "nba_master_hub_2026"
    hub = db[hub_name]

    print("=" * 80)
    print(f"  PHASE 2 RETRAIN — {sport.upper()} {player_type.upper()} → {target_stat}")
    print("=" * 80)

    # Step 1: Raw Scan
    print("\n[STEP 1] RAW SCAN...")
    valid_keys, discovered, key_counts = raw_scan(hub, player_type, sport)
    known = ORIGINAL_64_MLB if sport == "mlb" else ORIGINAL_NBA
    print(f"  Valid keys: {len(valid_keys)} | Discovered: {len(discovered)}")

    # Step 2: AutoFE with 50-player cap + float32
    print(f"\n[STEP 2] AUTO FEATURE ENGINEERING (50 players, float32)...")
    start = time.time()
    X, y, feature_names, raw_keys, player_count = build_feature_matrix(
        hub, target_stat, player_type, sport, max_players=50
    )
    # Convert to float32 to halve memory
    X = X.astype(np.float32)
    y = y.astype(np.float32)
    print(f"  Players: {player_count} | Samples: {len(X):,} | Features: {len(feature_names)}")
    print(f"  Memory: {X.nbytes / 1024 / 1024:.1f} MB (float32)")
    print(f"  Time: {time.time() - start:.1f}s")

    # Step 3: Lasso
    print(f"\n[STEP 3] LASSO L1 SURVIVOR SELECTION...")
    start = time.time()
    survivors, killed, lasso, scaler = lasso_survivor_selection(X, y, feature_names, top_n=40)

    mask = np.isfinite(X).all(axis=1) & np.isfinite(y)
    X_clean = X[mask]
    y_clean = y[mask]
    r2 = lasso.score(scaler.transform(X_clean), y_clean)

    print(f"  Alpha: {lasso.alpha_:.6f} | Intercept: {lasso.intercept_:.4f}")
    print(f"  Survived: {len(survivors)} | Killed: {len(killed)} | R²: {r2:.4f}")
    print(f"  Time: {time.time() - start:.1f}s")

    # Print top 10
    print(f"\n  TOP 10 SURVIVORS:")
    for rank, (name, abs_coef, raw_coef) in enumerate(survivors[:10], 1):
        sign = "+" if raw_coef > 0 else "-"
        tag = ""
        if name.startswith("ix_"):
            tag = " [IX]"
        elif name.startswith("delta3_"):
            tag = " [DELTA]"
        elif name.startswith("momentum_") or name.startswith("target_"):
            tag = " [META]"
        print(f"    {rank:>2}. {name:<45} {abs_coef:>8.4f} {sign}{tag}")

    # Save with scaler params
    survivor_indices = []
    for name, _, _ in survivors:
        if name in feature_names:
            survivor_indices.append(feature_names.index(name))

    scaler_means = {feature_names[i]: float(scaler.mean_[i]) for i in survivor_indices}
    scaler_scales = {feature_names[i]: float(scaler.scale_[i]) for i in survivor_indices}

    results = {
        "sport": sport,
        "player_type": player_type,
        "target_stat": target_stat,
        "total_features_engineered": len(feature_names),
        "survivors": len(survivors),
        "killed": len(killed),
        "lasso_alpha": float(lasso.alpha_),
        "lasso_intercept": float(lasso.intercept_),
        "r_squared": float(r2),
        "discovered_features": sorted(discovered),
        "survivor_list": [(n, float(a), float(r)) for n, a, r in survivors],
        "raw_keys_found": sorted(valid_keys),
        "scaler_means": scaler_means,
        "scaler_scales": scaler_scales,
    }

    output_path = f"{DATA_DIR}/autofe_{sport}_{player_type}_{target_stat}.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n  Saved: {output_path}")

    client.close()
    return results


if __name__ == "__main__":
    for sport, ptype, target in PHASE2_TARGETS:
        run_phase2_pipeline(sport, ptype, target)
        print("\n")

#!/usr/bin/env python3
"""
Full 10-Model Retrain: All existing + 3 new targets
NBA: pts, reb, ast, fg3m (3PM), pra (pts+reb+ast combo)
MLB: hits, total_bases, rbi, runs, p_k (pitcher strikeouts)
Uses 3-year history for coefficients, float32, 50-player cap.
"""
import sys
sys.path.insert(0, "/app/backend")

import numpy as np
from scripts.auto_feature_discovery import (
    raw_scan, build_feature_matrix, lasso_survivor_selection,
    ORIGINAL_64_MLB, ORIGINAL_NBA
)
from pymongo import MongoClient
import json, time, os

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "pick_vision")
DATA_DIR = "/app/backend/data"

ALL_TARGETS = [
    # NBA (5 models)
    ("nba", "all", "pts"),
    ("nba", "all", "reb"),
    ("nba", "all", "ast"),
    ("nba", "all", "fg3m"),
    # MLB (5 models)
    ("mlb", "batter", "hits"),
    ("mlb", "batter", "total_bases"),
    ("mlb", "batter", "rbi"),
    ("mlb", "batter", "runs"),
    ("mlb", "pitcher", "p_k"),
]


def build_pra_feature_matrix(hub, max_players=50):
    """Special handler for PRA (Points + Rebounds + Assists) combo stat."""
    query = {"history": {"$exists": True}, "games_played_3yr": {"$gt": 100}}

    raw_keys_set = set()
    sample_doc = hub.find_one(query, {"_id": 0, "history.2024_season": {"$slice": 1}})
    if sample_doc:
        log = sample_doc["history"]["2024_season"][0]
        for k, v in log.items():
            if isinstance(v, (int, float)) and v is not None and k not in ("game_id", "season", "home_team_id", "visitor_team_id", "team_id"):
                raw_keys_set.add(k)
    raw_keys = sorted(raw_keys_set)

    from itertools import combinations
    interaction_keys = [
        "pts", "reb", "ast", "fga", "fgm", "fg3a", "fg3m",
        "fta", "ftm", "turnover", "usage_pct", "pace",
        "true_shooting_pct", "off_rating", "def_rating",
    ]

    X_all = []
    y_all = []
    feature_names = None
    player_count = 0

    for doc in hub.find(query, {"_id": 0, "history": 1}).limit(max_players):
        all_logs = []
        for sk in ["2023_season", "2024_season", "2025_season"]:
            all_logs.extend(doc.get("history", {}).get(sk, []))
        all_logs.sort(key=lambda x: x.get("game_id") or 0)
        if len(all_logs) < 15:
            continue

        raw_matrix = []
        target_vals = []
        for log in all_logs:
            row = [float(log.get(k) or 0) for k in raw_keys]
            raw_matrix.append(row)
            pra = (log.get("pts") or 0) + (log.get("reb") or 0) + (log.get("ast") or 0)
            target_vals.append(float(pra))

        raw_matrix = np.array(raw_matrix)
        target_vals = np.array(target_vals)
        key_idx = {k: i for i, k in enumerate(raw_keys)}
        n = len(raw_matrix)

        for i in range(10, n):
            features = {}
            for j, k in enumerate(raw_keys):
                fname = f"prev_{k}"
                features[fname] = raw_matrix[i-1, j]
            for window, label in [(3, "L3"), (5, "L5"), (10, "L10")]:
                if i >= window:
                    wd = raw_matrix[i-window:i]
                    for j, k in enumerate(raw_keys):
                        features[f"{label}_avg_{k}"] = float(np.mean(wd[:, j]))
                        features[f"{label}_std_{k}"] = float(np.std(wd[:, j]))
            if i >= 6:
                r3 = raw_matrix[i-3:i]
                p3 = raw_matrix[i-6:i-3]
                for j, k in enumerate(raw_keys):
                    features[f"delta3_{k}"] = float(np.mean(r3[:, j]) - np.mean(p3[:, j]))
            for k1, k2 in combinations(interaction_keys, 2):
                if k1 in key_idx and k2 in key_idx:
                    features[f"ix_{k1}_x_{k2}"] = raw_matrix[i-1, key_idx[k1]] * raw_matrix[i-1, key_idx[k2]]
            if i >= 10:
                l10_avg = float(np.mean(target_vals[i-10:i]))
                l5 = target_vals[i-5:i]
                features["momentum_hit_rate_L5"] = float(np.mean(l5 > l10_avg)) if l10_avg > 0 else 0.0
                features["target_L10_avg"] = l10_avg
                features["target_L5_avg"] = float(np.mean(l5))
                features["target_L3_avg"] = float(np.mean(target_vals[i-3:i]))
                features["target_cv_L10"] = float(np.std(target_vals[i-10:i]) / (l10_avg + 1e-9))

            if feature_names is None:
                feature_names = sorted(features.keys())
            X_all.append([features.get(fn, 0.0) for fn in feature_names])
            y_all.append(target_vals[i])
        player_count += 1

    return np.array(X_all), np.array(y_all), feature_names, raw_keys, player_count


def run_single(sport, player_type, target_stat):
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]
    hub_name = "mlb_master_hub_2026" if sport == "mlb" else "nba_master_hub_2026"
    hub = db[hub_name]

    print(f"\n{'='*70}")
    print(f"  RETRAIN: {sport.upper()} {player_type.upper()} → {target_stat}")
    print(f"{'='*70}")

    valid_keys, discovered, _ = raw_scan(hub, player_type, sport)

    start = time.time()
    if target_stat == "pra":
        X, y, feature_names, raw_keys, pc = build_pra_feature_matrix(hub, max_players=50)
    else:
        X, y, feature_names, raw_keys, pc = build_feature_matrix(
            hub, target_stat, player_type, sport, max_players=50
        )
    X = X.astype(np.float32)
    y = y.astype(np.float32)
    print(f"  Players={pc} | Samples={len(X):,} | Features={len(feature_names)} | {time.time()-start:.1f}s")

    start = time.time()
    survivors, killed, lasso, scaler = lasso_survivor_selection(X, y, feature_names, top_n=40)

    mask = np.isfinite(X).all(axis=1) & np.isfinite(y)
    r2 = lasso.score(scaler.transform(X[mask]), y[mask])
    print(f"  Alpha={lasso.alpha_:.6f} | Intercept={lasso.intercept_:.4f} | R²={r2:.4f}")
    print(f"  Survived={len(survivors)} | Killed={len(killed)} | {time.time()-start:.1f}s")

    # Top 5
    for rank, (name, ac, rc) in enumerate(survivors[:5], 1):
        sign = "+" if rc > 0 else "-"
        print(f"    {rank}. {name:<42} {ac:.4f} {sign}")

    # Save
    si = [feature_names.index(n) for n, _, _ in survivors if n in feature_names]
    results = {
        "sport": sport, "player_type": player_type, "target_stat": target_stat,
        "total_features_engineered": len(feature_names),
        "survivors": len(survivors), "killed": len(killed),
        "lasso_alpha": float(lasso.alpha_), "lasso_intercept": float(lasso.intercept_),
        "r_squared": float(r2),
        "discovered_features": sorted(discovered),
        "survivor_list": [(n, float(a), float(r)) for n, a, r in survivors],
        "raw_keys_found": sorted(valid_keys),
        "scaler_means": {feature_names[i]: float(scaler.mean_[i]) for i in si},
        "scaler_scales": {feature_names[i]: float(scaler.scale_[i]) for i in si},
    }
    path = f"{DATA_DIR}/autofe_{sport}_{player_type}_{target_stat}.json"
    with open(path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"  Saved: {path}")
    client.close()


if __name__ == "__main__":
    t0 = time.time()
    for sport, ptype, target in ALL_TARGETS:
        run_single(sport, ptype, target)

    # PRA combo (special handler)
    print(f"\n{'='*70}")
    print(f"  RETRAIN: NBA ALL → pra (combo)")
    print(f"{'='*70}")
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]
    hub = db.nba_master_hub_2026
    valid_keys, discovered, _ = raw_scan(hub, "all", "nba")
    start = time.time()
    X, y, fn, rk, pc = build_pra_feature_matrix(hub, 50)
    X = X.astype(np.float32); y = y.astype(np.float32)
    print(f"  Players={pc} | Samples={len(X):,} | Features={len(fn)} | {time.time()-start:.1f}s")
    survivors, killed, lasso, scaler = lasso_survivor_selection(X, y, fn, 40)
    mask = np.isfinite(X).all(axis=1) & np.isfinite(y)
    r2 = lasso.score(scaler.transform(X[mask]), y[mask])
    print(f"  Alpha={lasso.alpha_:.6f} | Intercept={lasso.intercept_:.4f} | R²={r2:.4f}")
    print(f"  Survived={len(survivors)} | Killed={len(killed)}")
    for rank, (n, ac, rc) in enumerate(survivors[:5], 1):
        print(f"    {rank}. {n:<42} {ac:.4f} {'+' if rc>0 else '-'}")
    si = [fn.index(n) for n, _, _ in survivors if n in fn]
    results = {
        "sport": "nba", "player_type": "all", "target_stat": "pra",
        "total_features_engineered": len(fn), "survivors": len(survivors), "killed": len(killed),
        "lasso_alpha": float(lasso.alpha_), "lasso_intercept": float(lasso.intercept_),
        "r_squared": float(r2), "discovered_features": sorted(discovered),
        "survivor_list": [(n, float(a), float(r)) for n, a, r in survivors],
        "raw_keys_found": sorted(valid_keys),
        "scaler_means": {fn[i]: float(scaler.mean_[i]) for i in si},
        "scaler_scales": {fn[i]: float(scaler.scale_[i]) for i in si},
    }
    with open(f"{DATA_DIR}/autofe_nba_all_pra.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"  Saved: {DATA_DIR}/autofe_nba_all_pra.json")
    client.close()

    print(f"\n{'='*70}")
    print(f"  ALL 10 MODELS COMPLETE — {time.time()-t0:.1f}s total")
    print(f"{'='*70}")

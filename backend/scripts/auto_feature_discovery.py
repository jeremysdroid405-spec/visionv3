#!/usr/bin/env python3
"""
Automated Feature Discovery + Lasso Survivor Selection
=======================================================
1. Raw Scan: Discovers every numeric key in game logs
2. AutoFE: Creates interactions + time-derivatives
3. Lasso L1: Kills useless features, keeps survivors

Starts with MLB batters, then NBA.
"""

import os
import time
import json
import numpy as np
from collections import defaultdict
from itertools import combinations
from pymongo import MongoClient
from sklearn.linear_model import LassoCV
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import cross_val_score

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "pick_vision")

# The "original 64" known features for MLB batters (what the model was told about)
ORIGINAL_64_MLB = {
    "at_bats", "hits", "runs", "rbi", "hr", "bb", "k", "doubles", "triples",
    "stolen_bases", "caught_stealing", "plate_appearances", "total_bases",
    "hit_by_pitch", "intentional_walks", "avg", "obp", "slg", "gidp",
    "sac_bunts", "sac_flies", "left_on_base", "fly_outs", "ground_outs",
    "line_outs", "pop_outs", "air_outs", "putouts", "assists", "errors",
    "fielding_chances", "fielding_pct",
    # Pitcher
    "ip", "p_hits", "p_runs", "er", "p_bb", "p_k", "p_hr", "pitch_count",
    "strikes", "era", "batters_faced", "pitching_outs", "wins", "losses",
    "saves", "holds", "blown_saves", "games_started", "wild_pitches",
    "balks", "pitching_hbp", "inherited_runners", "inherited_runners_scored",
}

ORIGINAL_NBA = {
    "pts", "reb", "ast", "stl", "blk", "turnover", "pf", "fgm", "fga",
    "fg_pct", "fg3m", "fg3a", "fg3_pct", "ftm", "fta", "ft_pct", "oreb",
    "dreb", "plus_minus",
    # Advanced overlay
    "usage_pct", "true_shooting_pct", "off_rating", "def_rating",
    "net_rating", "pace", "ast_pct", "reb_pct", "pie", "eFG_pct",
    "turnover_ratio", "ast_to_tov", "ast_ratio", "oreb_pct", "dreb_pct",
}


def raw_scan(hub, player_type, sport):
    """Step 1: Scan all game logs, discover every numeric key."""
    query = {"is_batter": True} if player_type == "batter" else {"is_pitcher": True}
    if sport == "nba":
        query = {"history": {"$exists": True}}

    all_keys = defaultdict(int)
    sample_count = 0

    for doc in hub.find(query, {"_id": 0, "history": 1}).limit(200):
        history = doc.get("history", {})
        for season_key in ["2023_season", "2024_season", "2025_season"]:
            for log in history.get(season_key, []):
                for k, v in log.items():
                    if isinstance(v, (int, float)) and v is not None:
                        all_keys[k] += 1
                sample_count += 1

    # Filter: key must appear in >10% of logs to be valid
    threshold = sample_count * 0.10
    valid_keys = {k for k, count in all_keys.items() if count > threshold}

    known = ORIGINAL_64_MLB if sport == "mlb" else ORIGINAL_NBA
    discovered = valid_keys - known - {"game_id", "season", "home_team_id",
                                        "visitor_team_id", "home_team_score",
                                        "visitor_team_score", "team_id"}

    return valid_keys, discovered, all_keys


def build_feature_matrix(hub, target_stat, player_type, sport, max_players=150):
    """Step 2: Build AutoFE feature matrix with interactions + time-derivatives."""
    query = {"is_batter": True, "games_played": {"$gt": 80}} if player_type == "batter" else {"is_pitcher": True, "games_played": {"$gt": 40}}
    if sport == "nba":
        query = {"history": {"$exists": True}, "games_played_3yr": {"$gt": 100}}

    # Determine which raw numeric keys to use
    raw_keys_set = set()
    sample_doc = hub.find_one(query, {"_id": 0, "history.2024_season": {"$slice": 1}})
    if sample_doc:
        log = sample_doc["history"]["2024_season"][0]
        for k, v in log.items():
            if isinstance(v, (int, float)) and v is not None and k not in ("game_id", "season", "home_team_id", "visitor_team_id", "team_id"):
                raw_keys_set.add(k)
    raw_keys = sorted(raw_keys_set)

    X_all = []
    y_all = []
    feature_names = None

    player_count = 0
    for doc in hub.find(query, {"_id": 0, "history": 1, "player_name": 1, "display_name": 1}).limit(max_players):
        # Flatten all seasons into one timeline sorted by game_id
        all_logs = []
        for sk in ["2023_season", "2024_season", "2025_season"]:
            all_logs.extend(doc.get("history", {}).get(sk, []))
        all_logs.sort(key=lambda x: x.get("game_id") or 0)

        if len(all_logs) < 15:
            continue

        # Extract raw numeric vectors per game
        raw_matrix = []
        target_vals = []
        for log in all_logs:
            row = []
            for k in raw_keys:
                val = log.get(k)
                row.append(float(val) if val is not None else 0.0)
            raw_matrix.append(row)
            target_vals.append(float(log.get(target_stat, 0) or 0))

        raw_matrix = np.array(raw_matrix)
        target_vals = np.array(target_vals)

        # === AUTO FEATURE ENGINEERING ===
        n_games = len(raw_matrix)

        for i in range(10, n_games):
            features = {}

            # 1. Raw features from previous game
            for j, k in enumerate(raw_keys):
                features[f"prev_{k}"] = raw_matrix[i - 1, j]

            # 2. Rolling averages (L3, L5, L10)
            for window, label in [(3, "L3"), (5, "L5"), (10, "L10")]:
                if i >= window:
                    window_data = raw_matrix[i - window:i]
                    for j, k in enumerate(raw_keys):
                        col = window_data[:, j]
                        features[f"{label}_avg_{k}"] = np.mean(col)
                        features[f"{label}_std_{k}"] = np.std(col)

            # 3. Time-derivatives (change in L3 avg vs L3 avg before that)
            if i >= 6:
                recent_3 = raw_matrix[i - 3:i]
                prior_3 = raw_matrix[i - 6:i - 3]
                for j, k in enumerate(raw_keys):
                    r_avg = np.mean(recent_3[:, j])
                    p_avg = np.mean(prior_3[:, j])
                    features[f"delta3_{k}"] = r_avg - p_avg

            # 4. Interaction features (top correlated pairs only — limit to 15 key stats)
            if sport == "mlb":
                interaction_keys = ["at_bats", "hits", "hr", "bb", "k", "avg", "obp", "slg",
                                    "total_bases", "rbi", "runs", "plate_appearances",
                                    "fly_outs", "ground_outs", "stolen_bases"]
            else:
                interaction_keys = ["pts", "reb", "ast", "fga", "fgm", "fg3a", "fg3m",
                                    "fta", "ftm", "turnover", "usage_pct", "pace",
                                    "true_shooting_pct", "off_rating", "def_rating"]

            for k1, k2 in combinations(interaction_keys, 2):
                idx1 = raw_keys.index(k1) if k1 in raw_keys else None
                idx2 = raw_keys.index(k2) if k2 in raw_keys else None
                if idx1 is not None and idx2 is not None:
                    v1 = raw_matrix[i - 1, idx1]
                    v2 = raw_matrix[i - 1, idx2]
                    features[f"ix_{k1}_x_{k2}"] = v1 * v2

            # 5. Momentum: hit rate over L5 (% of games player exceeded their L10 avg)
            if i >= 10:
                l10_avg = np.mean(target_vals[i - 10:i])
                l5_vals = target_vals[i - 5:i]
                features["momentum_hit_rate_L5"] = np.mean(l5_vals > l10_avg) if l10_avg > 0 else 0.0
                features["target_L10_avg"] = l10_avg
                features["target_L5_avg"] = np.mean(l5_vals)
                features["target_L3_avg"] = np.mean(target_vals[i - 3:i])
                features["target_cv_L10"] = np.std(target_vals[i - 10:i]) / (l10_avg + 1e-9)

            if feature_names is None:
                feature_names = sorted(features.keys())

            row_vec = [features.get(fn, 0.0) for fn in feature_names]
            X_all.append(row_vec)
            y_all.append(target_vals[i])

        player_count += 1

    return np.array(X_all), np.array(y_all), feature_names, raw_keys, player_count


def lasso_survivor_selection(X, y, feature_names, top_n=40):
    """Step 3: Lasso L1 to kill useless features."""
    # Clean: remove any NaN/Inf
    mask = np.isfinite(X).all(axis=1) & np.isfinite(y)
    X = X[mask]
    y = y[mask]

    if len(X) < 100:
        return [], [], None

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # LassoCV finds optimal alpha via cross-validation
    lasso = LassoCV(cv=5, max_iter=10000, n_jobs=-1, random_state=42)
    lasso.fit(X_scaled, y)

    # Get feature importances (absolute coefficients)
    coefs = np.abs(lasso.coef_)
    sorted_idx = np.argsort(coefs)[::-1]

    survivors = []
    killed = []
    for idx in sorted_idx:
        name = feature_names[idx]
        coef = coefs[idx]
        if coef > 0:
            survivors.append((name, float(coef), float(lasso.coef_[idx])))
        else:
            killed.append(name)

    return survivors[:top_n], killed, lasso, scaler


def run_pipeline(sport, player_type, target_stat):
    """Full pipeline: Scan → AutoFE → Lasso."""
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]
    hub_name = "mlb_master_hub_2026" if sport == "mlb" else "nba_master_hub_2026"
    hub = db[hub_name]

    print("=" * 80)
    print(f"  AUTOMATED FEATURE DISCOVERY — {sport.upper()} {player_type.upper()}")
    print(f"  Target: {target_stat}")
    print("=" * 80)

    # Step 1: Raw Scan
    print("\n[STEP 1] RAW SCAN — Discovering all numeric keys...")
    start = time.time()
    valid_keys, discovered, key_counts = raw_scan(hub, player_type, sport)
    known = ORIGINAL_64_MLB if sport == "mlb" else ORIGINAL_NBA
    print(f"  Valid numeric keys found:     {len(valid_keys)}")
    print(f"  Known (original set):         {len(known & valid_keys)}")
    print(f"  DISCOVERED (new):             {len(discovered)}")
    if discovered:
        print(f"\n  ** NEW FEATURES NOT IN ORIGINAL SET **")
        for k in sorted(discovered):
            print(f"     + {k}  (found in {key_counts[k]:,} game logs)")
    print(f"  Scan time: {time.time() - start:.1f}s")

    # Step 2: AutoFE
    print(f"\n[STEP 2] AUTO FEATURE ENGINEERING...")
    start = time.time()
    X, y, feature_names, raw_keys, player_count = build_feature_matrix(
        hub, target_stat, player_type, sport, max_players=150
    )
    print(f"  Players used:           {player_count}")
    print(f"  Training samples:       {len(X):,}")
    print(f"  Engineered features:    {len(feature_names):,}")
    print(f"  AutoFE categories:")

    # Count feature types
    prev_count = sum(1 for f in feature_names if f.startswith("prev_"))
    l_avg_count = sum(1 for f in feature_names if "_avg_" in f and f.startswith("L"))
    l_std_count = sum(1 for f in feature_names if "_std_" in f and f.startswith("L"))
    delta_count = sum(1 for f in feature_names if f.startswith("delta3_"))
    ix_count = sum(1 for f in feature_names if f.startswith("ix_"))
    meta_count = sum(1 for f in feature_names if f.startswith("target_") or f.startswith("momentum_"))
    print(f"    Raw (prev game):      {prev_count}")
    print(f"    Rolling averages:     {l_avg_count}")
    print(f"    Rolling volatility:   {l_std_count}")
    print(f"    Time-derivatives:     {delta_count}")
    print(f"    Interactions:         {ix_count}")
    print(f"    Momentum/Meta:        {meta_count}")
    print(f"  AutoFE time: {time.time() - start:.1f}s")

    # Step 3: Lasso
    print(f"\n[STEP 3] LASSO L1 SURVIVOR SELECTION...")
    start = time.time()
    survivors, killed, lasso, scaler = lasso_survivor_selection(X, y, feature_names, top_n=40)

    # Compute R² on clean data
    mask = np.isfinite(X).all(axis=1) & np.isfinite(y)
    X_clean = X[mask]
    y_clean = y[mask]
    X_clean_scaled = scaler.transform(X_clean)
    r2_score = lasso.score(X_clean_scaled, y_clean)

    print(f"  Optimal alpha:          {lasso.alpha_:.6f}")
    print(f"  Features SURVIVED:      {len(survivors)}")
    print(f"  Features KILLED:        {len(killed)}")
    print(f"  Lasso R² (CV):          {r2_score:.4f}")
    print(f"  Intercept:              {lasso.intercept_:.4f}")
    print(f"  Selection time: {time.time() - start:.1f}s")

    print(f"\n{'='*80}")
    print(f"  TOP {min(40, len(survivors))} SURVIVOR FEATURES — {sport.upper()} {target_stat}")
    print(f"{'='*80}")
    print(f"  {'Rank':<5} {'Feature':<50} {'|Coef|':>8} {'Sign':>5}")
    print(f"  {'-'*70}")
    for rank, (name, abs_coef, raw_coef) in enumerate(survivors, 1):
        sign = "+" if raw_coef > 0 else "-"
        marker = ""
        # Flag discovered features
        base_key = name.replace("prev_", "").replace("L3_avg_", "").replace("L5_avg_", "").replace("L10_avg_", "").replace("L3_std_", "").replace("L5_std_", "").replace("L10_std_", "").replace("delta3_", "")
        if base_key in discovered:
            marker = " ** DISCOVERED **"
        if name.startswith("ix_"):
            marker = " [INTERACTION]"
        if name.startswith("delta3_"):
            marker = " [TIME-DERIV]"
        if name.startswith("momentum_") or name.startswith("target_"):
            marker = " [MOMENTUM]"
        print(f"  {rank:<5} {name:<50} {abs_coef:>8.4f} {sign:>5}{marker}")

    # Save results with scaler params for live prediction
    # Build scaler map: only for survivor features
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
        "r_squared": float(r2_score),
        "discovered_features": sorted(discovered),
        "survivor_list": [(n, float(a), float(r)) for n, a, r in survivors],
        "raw_keys_found": sorted(valid_keys),
        "scaler_means": scaler_means,
        "scaler_scales": scaler_scales,
    }

    output_path = f"/app/backend/data/autofe_{sport}_{player_type}_{target_stat}.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n  Results saved to: {output_path}")

    client.close()
    return results


if __name__ == "__main__":
    # MLB Batters — target: hits (most common PrizePicks line)
    run_pipeline("mlb", "batter", "hits")
    print("\n\n")
    # MLB Batters — target: total_bases
    run_pipeline("mlb", "batter", "total_bases")
    print("\n\n")
    # NBA — target: pts
    run_pipeline("nba", "all", "pts")

"""Low-Minutes Blend evaluation harness (2026-04-23).

Evaluates the structural blend:

    final_projection =
        (1 - low_minutes_prob) * baseline_projection
        + low_minutes_prob * low_minutes_projection

for every VK2 stat (PTS / REB / AST / 3PM / PRA) on the 2024 hold-out,
against the 52-feature baseline. `low_minutes_projection` is each
player's historical mean for that stat across games with
`minutes_played <= 12`, computed from 2020-2023 data only (leakage-safe).
If a player has < 3 such games, the projection falls back to the
league-wide low-minutes mean with shrinkage weight.

Target segments per spec:
  * PTS <10, PRA <10
  * bench players (L10 < 20)
  * declining-minutes (L5 - L20 < -2)

Report: `/app/backend/reports/low_minutes_blend_eval.json`
Markdown: `/app/backend/reports/low_minutes_blend_summary.md`
"""
from __future__ import annotations

import json
import math
import os
import pickle
import sys
import time
from collections import OrderedDict, defaultdict

import numpy as np
import pymongo
from sklearn.metrics import mean_absolute_error, mean_squared_error

sys.path.insert(0, "/app/backend")
os.chdir("/app/backend")

from scripts.retrain_nba_vk2 import (  # noqa: E402
    build_training_matrix, preload_advanced_stats,
)
from scripts.train_low_minutes_classifier import (  # noqa: E402
    FEATURE_SCHEMA as CLF_FEATS,
    LOW_MIN_THRESHOLD, _parse_minutes,
)

REPORT = "/app/backend/reports/low_minutes_blend_eval.json"
SUMMARY = "/app/backend/reports/low_minutes_blend_summary.md"
os.makedirs(os.path.dirname(REPORT), exist_ok=True)

VK2_MODEL_FMT = "/app/backend/models/vk2_{stat}.pkl"
LOW_MIN_MODEL = "/app/backend/models/low_minutes_classifier.pkl"
STATS = [("PTS", "pts"), ("REB", "reb"), ("AST", "ast"),
         ("3PM", "fg3m"), ("PRA", "pra")]

PRIOR_SEASONS = [2020, 2021, 2022, 2023]  # leakage-safe for 2024 eval
SHRINK_K = 5.0  # effective "prior" sample size for player-level shrinkage


def _metrics(y_true, y_pred, line=None):
    if len(y_true) == 0:
        return {"n": 0}
    mae = float(mean_absolute_error(y_true, y_pred))
    rmse = float(math.sqrt(mean_squared_error(y_true, y_pred)))
    bias = float(np.mean(y_pred - y_true))
    # Same-side accuracy at the segment's empirical line (mean of y_true).
    if line is None:
        line = float(y_true.mean())
    over_act = y_true > line
    over_pred = y_pred > line
    same_side = float(np.mean(over_act == over_pred)) * 100.0
    return {
        "n": int(len(y_true)),
        "rmse": round(rmse, 3),
        "mae": round(mae, 3),
        "bias": round(bias, 3),
        "actual_mean": round(float(y_true.mean()), 3),
        "pred_mean": round(float(y_pred.mean()), 3),
        "same_side_acc_pct": round(same_side, 2),
    }


def load_low_minutes_player_projections(stat_field):
    """Build a per-player mean of `stat_field` across games with
    `minutes_played <= LOW_MIN_THRESHOLD`, restricted to seasons 2020-2023.
    Returns (per_player_map, league_prior)."""
    client = pymongo.MongoClient(os.environ.get("MONGO_URL"))
    db = client[os.environ.get("DB_NAME")]
    coll = db.bdl_historical_game_logs

    per_player_sum = defaultdict(float)
    per_player_n = defaultdict(int)
    total_sum = 0.0
    total_n = 0
    pipeline = [
        {"$match": {"season": {"$in": PRIOR_SEASONS}}},
        {"$project": {
            "_id": 0, "player_id": 1, "min": 1,
            stat_field: 1, "pts": 1, "reb": 1, "ast": 1,
        }},
    ]
    for doc in coll.aggregate(pipeline, allowDiskUse=True):
        m = _parse_minutes(doc.get("min"))
        if m is None or m > LOW_MIN_THRESHOLD:
            continue
        # Compute stat value (PRA synthesizes)
        if stat_field == "pra":
            p = doc.get("pts"); r = doc.get("reb"); a = doc.get("ast")
            if p is None or r is None or a is None:
                continue
            v = float(p) + float(r) + float(a)
        else:
            raw = doc.get(stat_field)
            if raw is None:
                continue
            v = float(raw)
        pid = doc.get("player_id")
        per_player_sum[pid] += v
        per_player_n[pid] += 1
        total_sum += v
        total_n += 1
    client.close()
    league_prior = total_sum / max(total_n, 1)
    player_means = {}
    for pid in per_player_sum:
        n = per_player_n[pid]
        # Empirical-Bayes shrinkage toward league prior:
        #   mean_shrunk = (n*player_mean + K*league) / (n + K)
        player_mean = per_player_sum[pid] / max(n, 1)
        shrunk = (n * player_mean + SHRINK_K * league_prior) / (n + SHRINK_K)
        player_means[pid] = {
            "raw_mean": round(player_mean, 3),
            "n_games": n,
            "shrunk_mean": round(shrunk, 3),
        }
    return player_means, round(league_prior, 3)


def _attach_player_ids(X_te, feature_cols, test_sample_player_ids):
    """The training matrix doesn't carry player_id — the eval harness
    rebuilds it by re-iterating the same per-player sweep. For the
    purpose of the blend, we need an aligned player_id array. Rather
    than re-architect build_training_matrix, we re-sweep the same
    aggregation and collect player_ids in parallel."""
    # This function is delegated to a per-player aggregator in main().
    return None


def _predict_vk2(stat, X, feature_cols):
    with open(VK2_MODEL_FMT.format(stat=stat.lower()), "rb") as f:
        payload = pickle.load(f)
    schema = payload["features"]
    idx = [feature_cols.index(f) for f in schema]
    X_s = payload["scaler"].transform(X[:, idx])
    return payload["model"].predict(X_s), payload.get("version", "?")


def _predict_low_minutes_proba(X, feature_cols):
    """Build the 15-feat classifier input from the VK2 matrix and predict."""
    with open(LOW_MIN_MODEL, "rb") as f:
        p = pickle.load(f)
    # Map feature_cols → classifier feature set. Classifier has
    # role flags & counts which need to be derived.
    l5  = X[:, feature_cols.index("min_played_L5_mean")]
    l10 = X[:, feature_cols.index("min_played_L10_mean")]
    l20 = X[:, feature_cols.index("min_played_L20_mean")]
    l10s = X[:, feature_cols.index("min_played_L10_std")]
    l20s = X[:, feature_cols.index("min_played_L20_std")]
    # L3 mean / std not in VK2 → approximate with L5 (next-shortest).
    l3 = l5.copy()
    trend = l5 - l20
    starter = (l5 >= 28.0).astype(np.float32)
    rotation = ((l5 >= 18.0) & (l5 < 28.0)).astype(np.float32)
    bench = (l5 < 18.0).astype(np.float32)
    games_played = np.where(l10 > 0, 10.0, 0.0).astype(np.float32)
    games_started = np.where(l10 >= 20.0, 10.0, 0.0).astype(np.float32)
    # Situational: at eval time we don't have the per-sample context.
    # Use the same training defaults so the model sees a consistent
    # distribution (rest_days=3, others 0).
    N = len(l10)
    home_flag = np.zeros(N, dtype=np.float32)
    rest_days = np.full(N, 3.0, dtype=np.float32)
    b2b = np.zeros(N, dtype=np.float32)
    cols = {
        "min_played_L3_mean": l3,
        "min_played_L5_mean": l5,
        "min_played_L10_mean": l10,
        "min_played_L20_mean": l20,
        "min_played_L10_std": l10s,
        "min_played_L20_std": l20s,
        "min_trend_L5_vs_L20": trend,
        "games_played_last_10": games_played,
        "games_started_last_10": games_started,
        "starter_flag": starter,
        "rotation_flag": rotation,
        "bench_flag": bench,
        "home_flag": home_flag,
        "rest_days": rest_days,
        "back_to_back_flag": b2b,
    }
    M = np.stack([cols[f] for f in CLF_FEATS], axis=1).astype(np.float32)
    M_s = p["scaler"].transform(M)
    proba = p["model_low_12"].predict_proba(M_s)[:, 1]
    return proba, p.get("version", "?")


def rebuild_player_ids_for_matrix(feature_cols):
    """Re-sweep the same pipeline as `build_training_matrix` and return
    an array of player_ids aligned to each 2024 test-set sample (i.e.
    only the rows where season=2024). This lets us look up each
    sample's player_low_minutes_projection."""
    client = pymongo.MongoClient(os.environ.get("MONGO_URL"))
    db = client[os.environ.get("DB_NAME")]
    coll = db.bdl_historical_game_logs
    ROLLING_WINDOW = 20
    MIN_GAMES_PER_PLAYER = 12
    MIN_HISTORY_REQUIRED = 5
    SEASONS = [2020, 2021, 2022, 2023, 2024]
    SEASON_WEIGHTS = {2024: 1.00, 2023: 0.85, 2022: 0.70, 2021: 0.55, 2020: 0.40}

    pipeline = [
        {"$match": {"season": {"$in": SEASONS}}},
        {"$sort": {"player_id": 1, "game_id": 1}},
        {"$project": {"_id": 0, "player_id": 1, "game_id": 1,
                      "season": 1, "min": 1, "pts": 1, "reb": 1,
                      "ast": 1, "fg3m": 1, "fga": 1, "fg3a": 1,
                      "fta": 1, "fg_pct": 1, "fg3_pct": 1, "ft_pct": 1,
                      "date": 1, "team_id": 1}},
    ]
    current_pid = None
    current_logs = []
    all_pids = []
    all_seasons = []

    def flush(pid, logs_chrono):
        if len(logs_chrono) < MIN_GAMES_PER_PLAYER:
            return
        for i in range(MIN_HISTORY_REQUIRED, len(logs_chrono)):
            tgt = logs_chrono[i]
            if tgt.get("season") not in SEASON_WEIGHTS:
                continue
            # IMPORTANT: mirror the target-value guard from VK2's matrix —
            # sample is only produced if the target stat is parseable
            # and history yields a feature dict. Approximate: require
            # min parseable.
            tgt_min = _parse_minutes(tgt.get("min"))
            if tgt_min is None:
                continue
            # History must have >=5 games
            history_desc = logs_chrono[max(0, i - ROLLING_WINDOW):i]
            if len(history_desc) < 5:
                continue
            all_pids.append(pid)
            all_seasons.append(tgt.get("season"))

    cursor = coll.aggregate(pipeline, allowDiskUse=True, batchSize=5000)
    for doc in cursor:
        pid = doc.get("player_id")
        if pid != current_pid:
            if current_pid is not None and current_logs:
                flush(current_pid, current_logs)
            current_pid = pid
            current_logs = []
        current_logs.append(doc)
    if current_pid is not None and current_logs:
        flush(current_pid, current_logs)
    client.close()
    return np.asarray(all_pids), np.asarray(all_seasons)


def main():
    print("[lm_blend] preloading advanced stats...", flush=True)
    adv_map = preload_advanced_stats()

    out = OrderedDict()
    for stat_label, stat_field in STATS:
        print(f"[lm_blend] {stat_label} building matrix...", flush=True)
        t0 = time.time()
        X, y, sw, feature_cols, pids = build_training_matrix(
            stat_label, stat_field, adv_map=adv_map, target_schema=None,
            collect_player_ids=True,
        )
        if X is None:
            print(f"[lm_blend] {stat_label} SKIPPED: empty matrix", flush=True)
            continue
        test_mask = sw >= 0.99
        X_te, y_te = X[test_mask], y[test_mask]
        pids_te = pids[test_mask]
        print(f"[lm_blend] {stat_label} n_test={len(y_te):,} "
              f"unique_players={len(set(pids_te.tolist())):,} "
              f"build={time.time() - t0:.1f}s", flush=True)

        # Baseline VK2 predictions
        base_pred, base_ver = _predict_vk2(stat_label, X_te, feature_cols)
        # Low-minutes probabilities
        p_low, clf_ver = _predict_low_minutes_proba(X_te, feature_cols)
        # Per-player low-minutes projection (leakage-safe, 2020-2023 only)
        player_map, league_prior = load_low_minutes_player_projections(stat_field)
        lm_proj = np.empty(len(y_te), dtype=np.float32)
        used_league_prior = 0
        for i, pid in enumerate(pids_te):
            pm = player_map.get(int(pid))
            if pm is None:
                lm_proj[i] = league_prior
                used_league_prior += 1
            else:
                lm_proj[i] = pm["shrunk_mean"]
        # Blend — two variants:
        #  1. universal      : uses full probability-weighted mix
        #  2. gated_narrow   : only fires for PTS/PRA when p_low >= 0.5
        blend_pred = (1.0 - p_low) * base_pred + p_low * lm_proj
        blend_gated = base_pred.copy()
        if stat_label in ("PTS", "PRA"):
            gate = p_low >= 0.5
            blend_gated[gate] = (
                (1.0 - p_low[gate]) * base_pred[gate]
                + p_low[gate] * lm_proj[gate]
            )

        l10 = X_te[:, feature_cols.index("min_played_L10_mean")]
        l5 = X_te[:, feature_cols.index("min_played_L5_mean")]
        l20 = X_te[:, feature_cols.index("min_played_L20_mean")]
        segments = OrderedDict([
            ("overall", np.ones(len(y_te), dtype=bool)),
            (f"{stat_label} <10",               y_te < 10),
            ("bench (L10<20)",                  l10 < 20),
            ("rotation (L10 18-28)",            (l10 >= 18) & (l10 < 28)),
            ("starter (L10>=28)",               l10 >= 28),
            ("declining (L5-L20<-2)",           (l5 - l20) < -2),
            (f"{stat_label} 10-20",             (y_te >= 10) & (y_te < 20)),
            (f"{stat_label} >=20",              y_te >= 20),
        ])
        rows = OrderedDict()
        for seg_name, mask in segments.items():
            if mask.sum() < 30:
                continue
            rows[seg_name] = {
                "baseline":     _metrics(y_te[mask], base_pred[mask]),
                "blend":        _metrics(y_te[mask], blend_pred[mask]),
                "gated_narrow": _metrics(y_te[mask], blend_gated[mask]),
            }
        out[stat_label] = {
            "baseline_version": base_ver,
            "classifier_version": clf_ver,
            "test_rows": int(len(y_te)),
            "league_low_minutes_prior": league_prior,
            "players_using_prior_fallback": used_league_prior,
            "players_with_history": len(player_map),
            "p_low_summary": {
                "mean": round(float(p_low.mean()), 4),
                "median": round(float(np.median(p_low)), 4),
                "p95": round(float(np.quantile(p_low, 0.95)), 4),
                "high_risk_count": int((p_low >= 0.5).sum()),
            },
            "gate_fired_count": int((p_low >= 0.5).sum()) if stat_label in ("PTS", "PRA") else 0,
            "segments": rows,
        }
        with open(REPORT, "w") as f:
            json.dump(out, f, indent=2)
        print(f"[lm_blend] {stat_label} saved. p_low_mean={p_low.mean():.3f}",
              flush=True)
        print(f"[lm_blend]  === {stat_label} (RMSE / bias) ===", flush=True)
        for seg_name, vals in rows.items():
            b = vals["baseline"]; x = vals["blend"]; g = vals["gated_narrow"]
            print(f"[lm_blend]  {seg_name:25s} "
                  f"base:{b['rmse']:.2f}/{b['bias']:+.2f}  "
                  f"blend:{x['rmse']:.2f}/{x['bias']:+.2f}  "
                  f"gated:{g['rmse']:.2f}/{g['bias']:+.2f}", flush=True)
    print(f"[lm_blend] ALL DONE. Report: {REPORT}", flush=True)


if __name__ == "__main__":
    main()

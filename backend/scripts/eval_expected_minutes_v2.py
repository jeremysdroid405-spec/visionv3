"""Expected Minutes STRUCTURAL RATE-SCALING eval (2026-04-23).

STRICT SPEC: compare VK2 baseline projections vs projections scaled
through the new universal rate-scaling formula:

    rate = model_projection / min_played_L10_mean   (historical per-min rate)
    adjusted = rate * predicted_minutes_tonight

Applied UNIVERSALLY across all stats (PTS, REB, AST, 3PM, PRA) on the
2024 hold-out. Reports RMSE / MAE / bias before vs after in target
segments:
  * PRA < 10
  * PTS < 10
  * bench (min_played_L10_mean < 20)

Writes:
  reports/expected_minutes_eval.json
"""
from __future__ import annotations

import json
import math
import os
import pickle
import sys
import time
from collections import OrderedDict

import numpy as np
from sklearn.metrics import mean_absolute_error, mean_squared_error

sys.path.insert(0, "/app/backend")
os.chdir("/app/backend")

from scripts.retrain_nba_vk2 import (  # noqa: E402
    build_training_matrix, preload_advanced_stats, PRUNED_FEATURES,
)
from scripts.train_expected_minutes import (  # noqa: E402
    FEATURE_SCHEMA as MIN_FEATS_V2,
)

REPORT = "/app/backend/reports/expected_minutes_eval.json"
os.makedirs(os.path.dirname(REPORT), exist_ok=True)

MIN_MODEL_PATH = "/app/backend/models/expected_minutes.pkl"
VK2_MODEL_FMT = "/app/backend/models/vk2_{stat}.pkl"
STATS = [("PTS", "pts"), ("REB", "reb"), ("AST", "ast"),
         ("3PM", "fg3m"), ("PRA", "pra")]
MIN_L10_FLOOR = 2.0  # minutes floor to avoid divide-by-tiny


def _metrics(y_true, y_pred, line=None):
    if len(y_true) == 0:
        return {"n": 0}
    mae = float(mean_absolute_error(y_true, y_pred))
    rmse = float(math.sqrt(mean_squared_error(y_true, y_pred)))
    bias = float(np.mean(y_pred - y_true))
    actual_mean = float(y_true.mean())
    return {
        "n": int(len(y_true)),
        "rmse": round(rmse, 3),
        "mae": round(mae, 3),
        "bias": round(bias, 3),
        "actual_mean": round(actual_mean, 3),
        "pred_mean": round(float(y_pred.mean()), 3),
    }


def _predict_vk2(stat, X, feature_cols):
    with open(VK2_MODEL_FMT.format(stat=stat.lower()), "rb") as f:
        payload = pickle.load(f)
    schema = payload["features"]
    idx = [feature_cols.index(f) for f in schema]
    X_s = payload["scaler"].transform(X[:, idx])
    return payload["model"].predict(X_s), payload.get("version", "?")


def _build_minutes_feat_matrix(X, feature_cols):
    """Derive the 12-feat minutes-model input from the VK2 matrix.
    All 12 features are computable from the VK2 column set + the
    mutually-exclusive role flags derived from min_played_L5_mean."""
    idx = {
        "L5":  feature_cols.index("min_played_L5_mean"),
        "L10": feature_cols.index("min_played_L10_mean"),
        "L20": feature_cols.index("min_played_L20_mean"),
        "L10s": feature_cols.index("min_played_L10_std"),
        "L20s": feature_cols.index("min_played_L20_std"),
    }
    l5  = X[:, idx["L5"]]
    l10 = X[:, idx["L10"]]
    l20 = X[:, idx["L20"]]
    l10s = X[:, idx["L10s"]]
    l20s = X[:, idx["L20s"]]
    # L3 mean not in VK2; approximate with L5 (next-shortest window).
    l3 = l5.copy()
    # L3 std not needed — our v2 spec doesn't include it.
    trend = l5 - l20
    starter = (l5 >= 28.0).astype(np.float32)
    rotation = ((l5 >= 18.0) & (l5 < 28.0)).astype(np.float32)
    bench = (l5 < 18.0).astype(np.float32)
    # Count approximations — we don't have the raw minutes series at
    # eval time, so assume the player played/started every game with
    # mean > threshold. Matches how live scoring will infer these
    # when no raw series is available.
    games_played = np.where(l10 > 0, 10.0, 0.0).astype(np.float32)
    games_started = np.where(l10 >= 20.0, 10.0, 0.0).astype(np.float32)
    cols = {
        "min_played_L3_mean":     l3,
        "min_played_L5_mean":     l5,
        "min_played_L10_mean":    l10,
        "min_played_L20_mean":    l20,
        "min_played_L10_std":     l10s,
        "min_played_L20_std":     l20s,
        "min_trend_L5_vs_L20":    trend,
        "starter_flag":           starter,
        "rotation_flag":          rotation,
        "bench_flag":             bench,
        "games_played_last_10":   games_played,
        "games_started_last_10":  games_started,
    }
    M = np.stack([cols[f] for f in MIN_FEATS_V2], axis=1).astype(np.float32)
    return M


def _predict_minutes(X, feature_cols):
    with open(MIN_MODEL_PATH, "rb") as f:
        p = pickle.load(f)
    M = _build_minutes_feat_matrix(X, feature_cols)
    M_s = p["scaler"].transform(M)
    return p["model"].predict(M_s), p.get("version", "?")


def rate_scaled_projection(base_pred, X, feature_cols, pred_min):
    """adjusted = (base_pred / L10_mean) * pred_min, with safeguards."""
    l10 = X[:, feature_cols.index("min_played_L10_mean")]
    # Clamp L10 to a floor so a 1-min rolling average doesn't blow up
    # the rate. Players with <2 min L10 keep the baseline projection.
    use_rate = l10 >= MIN_L10_FLOOR
    adjusted = np.where(
        use_rate,
        (base_pred / np.where(l10 > 0, l10, 1.0)) * pred_min,
        base_pred,
    )
    # Non-negativity
    adjusted = np.clip(adjusted, 0.0, None)
    return adjusted


def main():
    print("[v2_eval] preload adv map...", flush=True)
    t0 = time.time()
    adv_map = preload_advanced_stats()
    print(f"[v2_eval] adv_map done in {time.time() - t0:.1f}s", flush=True)

    out = OrderedDict()
    for stat_label, stat_field in STATS:
        t1 = time.time()
        print(f"[v2_eval] {stat_label} building matrix...", flush=True)
        X, y, sw, feature_cols = build_training_matrix(
            stat_label, stat_field, adv_map=adv_map, target_schema=None,
        )
        test_mask = sw >= 0.99
        X_te, y_te = X[test_mask], y[test_mask]
        print(f"[v2_eval] {stat_label} n_test={len(y_te):,} "
              f"build={time.time() - t1:.1f}s", flush=True)

        base_pred, base_ver = _predict_vk2(stat_label, X_te, feature_cols)
        pred_min, min_ver = _predict_minutes(X_te, feature_cols)
        adj_pred = rate_scaled_projection(base_pred, X_te, feature_cols, pred_min)

        l10 = X_te[:, feature_cols.index("min_played_L10_mean")]
        segments = OrderedDict([
            ("overall",          np.ones(len(y_te), dtype=bool)),
            (f"{stat_label} <10",             y_te < 10),
            ("bench (L10<20)",                l10 < 20),
            ("rotation (L10 18-28)",          (l10 >= 18) & (l10 < 28)),
            ("starter (L10>=28)",             l10 >= 28),
            ("declining (L5-L20<-2)",
                (X_te[:, feature_cols.index("min_played_L5_mean")] -
                 X_te[:, feature_cols.index("min_played_L20_mean")]) < -2),
            (f"{stat_label} 10-20",           (y_te >= 10) & (y_te < 20)),
            (f"{stat_label} >=20",            y_te >= 20),
        ])
        rows = OrderedDict()
        for seg_name, mask in segments.items():
            if mask.sum() < 30:
                continue
            rows[seg_name] = {
                "baseline": _metrics(y_te[mask], base_pred[mask]),
                "rate_scaled": _metrics(y_te[mask], adj_pred[mask]),
            }
        pred_min_stats = {
            "min": round(float(pred_min.min()), 3),
            "max": round(float(pred_min.max()), 3),
            "mean": round(float(pred_min.mean()), 3),
            "median": round(float(np.median(pred_min)), 3),
        }
        out[stat_label] = {
            "baseline_model_version": base_ver,
            "minutes_model_version": min_ver,
            "test_rows": int(len(y_te)),
            "predicted_minutes_summary": pred_min_stats,
            "segments": rows,
        }
        with open(REPORT, "w") as f:
            json.dump(out, f, indent=2)
        print(f"[v2_eval] {stat_label} saved", flush=True)
        print(f"[v2_eval]  === {stat_label} summary (RMSE / bias) ===",
              flush=True)
        for seg_name, vals in rows.items():
            b = vals["baseline"]; r = vals["rate_scaled"]
            drmse = r["rmse"] - b["rmse"]
            dbias = r["bias"] - b["bias"]
            print(f"[v2_eval]  {seg_name:26s} "
                  f"base:{b['rmse']:.2f}/{b['bias']:+.2f}  "
                  f"rate:{r['rmse']:.2f}/{r['bias']:+.2f}  "
                  f"Δ:{drmse:+.2f}/{dbias:+.2f}", flush=True)
    print(f"[v2_eval] ALL DONE. Report: {REPORT}", flush=True)


if __name__ == "__main__":
    main()

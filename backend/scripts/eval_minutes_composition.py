"""Segmented evaluation: VK2 baseline vs expected-minutes composed predictor.

Compares on the 2024 held-out set:
  1. baseline   — VK2 pruned 52-feat model prediction (current prod).
  2. min_only   — predicted_minutes (new model) × historical per-min rate
                  (pts_L5_mean / min_played_L5_mean, etc.). Pure opportunity
                  × efficiency decomposition.
  3. blend_05   — 0.5 * baseline + 0.5 * min_only. Safe averaging blend.
  4. blend_bench — use min_only for bench regime (min_L10<20), baseline
                   otherwise. Surgical intervention on the documented
                   regression surface.

Segments focused on the success condition agreed with the user:
  * PRA <10     (critical low-line bench regression)
  * PTS <10     (critical low-line bench regression)
  * bench players     (min_played_L10_mean < 20)
  * declining players (min_played_L3_mean - L10_mean < -2)

Report: /app/backend/reports/vk2_expected_minutes_segmented.json
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
from scripts.train_nba_minutes_model import (  # noqa: E402
    FEATURE_SCHEMA as MIN_FEATS, build_minutes_features,
)

REPORT = "/app/backend/reports/vk2_expected_minutes_segmented.json"
os.makedirs(os.path.dirname(REPORT), exist_ok=True)

MIN_MODEL_PATH = "/app/backend/models/nba_expected_minutes.pkl"
VK2_MODEL_FMT = "/app/backend/models/vk2_{stat}.pkl"
STATS = [("PTS", "pts"), ("REB", "reb"), ("AST", "ast"),
         ("3PM", "fg3m"), ("PRA", "pra")]


def _metrics(y_true, y_pred):
    if len(y_true) == 0:
        return {"n": 0}
    mae = float(mean_absolute_error(y_true, y_pred))
    rmse = float(math.sqrt(mean_squared_error(y_true, y_pred)))
    bias = float(np.mean(y_pred - y_true))
    line = float(np.mean(y_true))
    if len(y_true) >= 5:
        over_act = y_true > line
        over_pred = y_pred > line
        same_side = float(np.mean(over_act == over_pred)) * 100.0
    else:
        same_side = None
    return {
        "n": int(len(y_true)),
        "rmse": round(rmse, 3),
        "mae": round(mae, 3),
        "bias": round(bias, 3),
        "line": round(line, 2),
        "pred_mean": round(float(np.mean(y_pred)), 3),
        "actual_mean": round(line, 3),
        "same_side_acc_pct": round(same_side, 2) if same_side is not None else None,
    }


def _predict_vk2(stat_label, X, feature_cols):
    path = VK2_MODEL_FMT.format(stat=stat_label.lower())
    with open(path, "rb") as f:
        payload = pickle.load(f)
    schema = payload["features"]
    idx = [feature_cols.index(f) for f in schema]
    X_s = payload["scaler"].transform(X[:, idx])
    return payload["model"].predict(X_s), payload.get("version", "?")


def _predict_minutes(X, feature_cols):
    with open(MIN_MODEL_PATH, "rb") as f:
        payload = pickle.load(f)
    schema = payload["features"]  # subset of retrain feature_cols
    # The minutes model schema contains features that the
    # VK2 matrix does NOT directly emit (min_L3_mean etc. were
    # rolled back). We build them from the VK2 matrix columns
    # using an inline mapping that mirrors `build_minutes_features`.
    i_min = {
        "L3_mean":   feature_cols.index("min_played_L3_mean") if "min_played_L3_mean" in feature_cols else None,
        "L5_mean":   feature_cols.index("min_played_L5_mean"),
        "L10_mean":  feature_cols.index("min_played_L10_mean"),
        "L20_mean":  feature_cols.index("min_played_L20_mean"),
        "L3_std":    feature_cols.index("min_played_L3_std") if "min_played_L3_std" in feature_cols else None,
        "L5_std":    feature_cols.index("min_played_L5_std"),
        "L10_std":   feature_cols.index("min_played_L10_std"),
        "L20_std":   feature_cols.index("min_played_L20_std"),
    }
    N = X.shape[0]
    M = np.zeros((N, len(schema)), dtype=np.float32)
    # We only have rolling means/stds, not raw minutes series. For the
    # non-trivial floor/ceiling/rate features we approximate from
    # available moments (max≈mean+2*std, min≈max(0,mean-2*std)).
    for j, f in enumerate(schema):
        if f == "min_L3_mean":
            col = i_min["L3_mean"]
            M[:, j] = X[:, col] if col is not None else X[:, i_min["L5_mean"]]
        elif f == "min_L5_mean":
            M[:, j] = X[:, i_min["L5_mean"]]
        elif f == "min_L10_mean":
            M[:, j] = X[:, i_min["L10_mean"]]
        elif f == "min_L20_mean":
            M[:, j] = X[:, i_min["L20_mean"]]
        elif f == "min_L3_std":
            col = i_min["L3_std"]
            M[:, j] = X[:, col] if col is not None else X[:, i_min["L5_std"]]
        elif f == "min_L5_std":
            M[:, j] = X[:, i_min["L5_std"]]
        elif f == "min_L10_std":
            M[:, j] = X[:, i_min["L10_std"]]
        elif f == "min_L20_std":
            M[:, j] = X[:, i_min["L20_std"]]
        elif f == "min_L3_L10_diff":
            col = i_min["L3_mean"]
            a = X[:, col] if col is not None else X[:, i_min["L5_mean"]]
            M[:, j] = a - X[:, i_min["L10_mean"]]
        elif f == "min_L5_L20_diff":
            M[:, j] = X[:, i_min["L5_mean"]] - X[:, i_min["L20_mean"]]
        elif f == "min_floor_L20":
            mean = X[:, i_min["L20_mean"]]
            std = X[:, i_min["L20_std"]]
            M[:, j] = np.maximum(0.0, mean - 2.0 * std)
        elif f == "min_ceiling_L20":
            mean = X[:, i_min["L20_mean"]]
            std = X[:, i_min["L20_std"]]
            M[:, j] = mean + 2.0 * std
        elif f == "min_dnp_rate_L20":
            # rough approximation: fraction of a normal distribution below 5
            mean = X[:, i_min["L20_mean"]]
            std = np.maximum(X[:, i_min["L20_std"]], 0.5)
            z = (5.0 - mean) / std
            M[:, j] = 0.5 * (1.0 + np.tanh(z / math.sqrt(2)))
        elif f == "min_low_rate_L10":
            mean = X[:, i_min["L10_mean"]]
            std = np.maximum(X[:, i_min["L10_std"]], 0.5)
            z = (15.0 - mean) / std
            M[:, j] = 0.5 * (1.0 + np.tanh(z / math.sqrt(2)))
        elif f == "appearances_L20":
            # assume played all 20 if L20_mean>0, else 0
            M[:, j] = np.where(X[:, i_min["L20_mean"]] > 0, 20.0, 0.0)
        else:
            raise RuntimeError(f"unhandled minutes feature: {f}")
    M_s = payload["scaler"].transform(M)
    return payload["model"].predict(M_s), payload.get("version", "?")


def _per_min_rate(X, feature_cols, stat_field):
    """Historical per-minute stat rate from rolling features."""
    # Use L5 means: stat_L5_mean / min_played_L5_mean
    min_l5 = X[:, feature_cols.index("min_played_L5_mean")]
    if stat_field == "pra":
        # PRA: use pra_L5_mean / min_played_L5_mean
        pra_l5 = X[:, feature_cols.index("pra_L5_mean")] \
            if "pra_L5_mean" in feature_cols else (
                X[:, feature_cols.index("pts_L5_mean")] +
                X[:, feature_cols.index("reb_L5_mean")] +
                X[:, feature_cols.index("ast_L5_mean")]
            )
        stat_l5 = pra_l5
    else:
        key = f"{stat_field}_L5_mean"
        stat_l5 = X[:, feature_cols.index(key)]
    denom = np.where(min_l5 > 1.0, min_l5, 1.0)
    rate = stat_l5 / denom
    # Clamp to avoid absurd rates when L5 minutes ≈ 0
    rate = np.clip(rate, 0.0, 5.0)
    return rate, min_l5


def main():
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    print("[eval] preloading advanced stats...", flush=True)
    t0 = time.time()
    adv_map = preload_advanced_stats()
    print(f"[eval] adv_map ready in {time.time() - t0:.1f}s", flush=True)

    out = OrderedDict()
    for stat_label, stat_field in STATS:
        t1 = time.time()
        print(f"[eval] building {stat_label} matrix...", flush=True)
        X, y, sw, feature_cols = build_training_matrix(
            stat_label, stat_field, adv_map=adv_map, target_schema=None,
        )
        test_mask = sw >= 0.99
        X_te, y_te = X[test_mask], y[test_mask]
        print(
            f"[eval]  {stat_label} test={len(y_te):,} cols={len(feature_cols)} "
            f"build={time.time() - t1:.1f}s", flush=True,
        )

        # Baseline
        base_pred, base_ver = _predict_vk2(stat_label, X_te, feature_cols)
        # Minutes composed
        pred_min, _ = _predict_minutes(X_te, feature_cols)
        rate, hist_min_l5 = _per_min_rate(X_te, feature_cols, stat_field)
        min_only_pred = pred_min * rate
        # Blend variants
        blend_05 = 0.5 * base_pred + 0.5 * min_only_pred
        role_bench = (X_te[:, feature_cols.index("min_played_L10_mean")] < 20)
        blend_bench = np.where(role_bench, min_only_pred, base_pred)

        predictions = {
            "baseline":     base_pred,
            "min_only":     min_only_pred,
            "blend_05":     blend_05,
            "blend_bench":  blend_bench,
        }

        segment_masks = OrderedDict([
            ("overall",
                np.ones(len(y_te), dtype=bool)),
            ("bench (min_L10<20)",
                X_te[:, feature_cols.index("min_played_L10_mean")] < 20),
            ("starter (min_L10>=30)",
                X_te[:, feature_cols.index("min_played_L10_mean")] >= 30),
            ("declining (L5-L20<-2)",
                (X_te[:, feature_cols.index("min_played_L5_mean")] -
                 X_te[:, feature_cols.index("min_played_L20_mean")]) < -2),
            ("line<10", y_te < 10),
            ("line 10-20", (y_te >= 10) & (y_te < 20)),
            ("line>=20", y_te >= 20),
        ])

        rows = OrderedDict()
        for seg_name, mask in segment_masks.items():
            if mask.sum() < 30:
                continue
            rows[seg_name] = {
                pred_name: _metrics(y_te[mask], p[mask])
                for pred_name, p in predictions.items()
            }
        out[stat_label] = {
            "baseline_model_version": base_ver,
            "test_rows": int(len(y_te)),
            "predicted_minutes_range": {
                "min": round(float(pred_min.min()), 2),
                "max": round(float(pred_min.max()), 2),
                "mean": round(float(pred_min.mean()), 2),
            },
            "historical_min_l5_mean": round(float(hist_min_l5.mean()), 2),
            "segments": rows,
        }
        with open(REPORT, "w") as f:
            json.dump(out, f, indent=2)
        print(f"[eval] {stat_label} saved incremental to {REPORT}", flush=True)
        # print summary
        print(f"[eval]  === {stat_label} summary (RMSE / bias) ===", flush=True)
        for seg_name, vals in rows.items():
            cells = "  ".join(
                f"{k}:{v['rmse']:.2f}/{v['bias']:+.2f}"
                for k, v in vals.items()
            )
            print(f"[eval]  {seg_name:28s} {cells}", flush=True)

    print(f"[eval] ALL DONE. Report: {REPORT}", flush=True)


if __name__ == "__main__":
    main()

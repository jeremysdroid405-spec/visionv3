"""Opponent-context model comparison (2026-04-23).

Compares the 52-feature pruned baseline (`vk2_{stat}.pkl`) to the
66-feature +opp variant (`vk2_{stat}_opp.pkl`) on the 2024 hold-out.
Emits:
  * Aggregate RMSE / MAE / R² / bias for both models
  * Segmented metrics (line<10, line 10-20, line>=20, bench, starter,
    declining)
  * Directional calibration (bucketed hit rate vs expected)
  * Top-10 feature importance for the +opp model
  * A summary of whether matchup features improved low-line performance
    or reduced over-confidence.

Report: `/app/backend/reports/opp_context_segmented.json`
Markdown summary: `/app/backend/reports/opp_context_summary.md`
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
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

sys.path.insert(0, "/app/backend")
os.chdir("/app/backend")

from scripts.retrain_nba_vk2 import (  # noqa: E402
    build_training_matrix, preload_advanced_stats,
    PRUNED_FEATURES, PRUNED_OPP_FEATURES, SEASONS,
    build_opponent_context_store, db as SYNC_DB,
)

REPORT = "/app/backend/reports/opp_context_segmented.json"
SUMMARY = "/app/backend/reports/opp_context_summary.md"
os.makedirs(os.path.dirname(REPORT), exist_ok=True)

VK2_BASELINE_FMT = "/app/backend/models/vk2_{stat}.pkl"
VK2_OPP_FMT = "/app/backend/models/vk2_{stat}_opp.pkl"

STATS = [("PTS", "pts"), ("REB", "reb"), ("AST", "ast"),
         ("3PM", "fg3m"), ("PRA", "pra")]


def _metrics(y_true, y_pred):
    if len(y_true) == 0:
        return {"n": 0}
    mae = float(mean_absolute_error(y_true, y_pred))
    rmse = float(math.sqrt(mean_squared_error(y_true, y_pred)))
    bias = float(np.mean(y_pred - y_true))
    if len(y_true) >= 30:
        r2 = float(r2_score(y_true, y_pred))
    else:
        r2 = None
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
        "r2": round(r2, 4) if r2 is not None else None,
        "actual_mean": round(line, 3),
        "pred_mean": round(float(np.mean(y_pred)), 3),
        "same_side_acc_pct": round(same_side, 2) if same_side is not None else None,
    }


def _predict_vk2(path, X, feature_cols):
    with open(path, "rb") as f:
        payload = pickle.load(f)
    schema = payload["features"]
    missing = [f for f in schema if f not in feature_cols]
    if missing:
        raise RuntimeError(
            f"{path} schema references {len(missing)} features not in matrix: "
            f"{missing[:5]}"
        )
    idx = [feature_cols.index(f) for f in schema]
    X_s = payload["scaler"].transform(X[:, idx])
    pred = payload["model"].predict(X_s)
    top_feats = sorted(
        zip(schema, payload["model"].feature_importances_),
        key=lambda x: -x[1],
    )[:10]
    return pred, payload, [(n, float(v)) for n, v in top_feats]


def _calibration_buckets(y_true, y_pred):
    """Bucket predictions into quantiles and measure actual-vs-expected
    over rate at each bucket's mean as a virtual line."""
    if len(y_true) < 200:
        return []
    edges = np.quantile(y_pred, [0.1, 0.25, 0.5, 0.75, 0.9])
    rows = []
    for edge in edges:
        mask = (y_pred >= edge - 0.5) & (y_pred <= edge + 0.5)
        if mask.sum() < 50:
            continue
        actual_over = float((y_true[mask] > edge).mean())
        rows.append({
            "line": round(float(edge), 2),
            "n": int(mask.sum()),
            "actual_over_rate": round(actual_over, 3),
            "expected_over_rate": 0.5,
            "calibration_error": round(abs(actual_over - 0.5), 3),
        })
    return rows


def main():
    print("[opp_eval] preloading...", flush=True)
    t0 = time.time()
    adv_map = preload_advanced_stats()
    opp_store = build_opponent_context_store(SYNC_DB, SEASONS)
    print(f"[opp_eval] preload done in {time.time() - t0:.1f}s", flush=True)

    out = OrderedDict()
    for stat_label, stat_field in STATS:
        t1 = time.time()
        print(f"[opp_eval] building {stat_label} matrix...", flush=True)
        # Build with opp features enabled so both models can be evaluated.
        X, y, sw, feature_cols = build_training_matrix(
            stat_label, stat_field, adv_map=adv_map,
            target_schema=None, opp_store=opp_store,
        )
        test_mask = sw >= 0.99
        X_te, y_te = X[test_mask], y[test_mask]
        print(f"[opp_eval] {stat_label} matrix n_test={len(y_te):,} "
              f"build={time.time() - t1:.1f}s", flush=True)

        base_pred, base_payload, base_top = _predict_vk2(
            VK2_BASELINE_FMT.format(stat=stat_label.lower()),
            X_te, feature_cols,
        )
        opp_pred, opp_payload, opp_top = _predict_vk2(
            VK2_OPP_FMT.format(stat=stat_label.lower()),
            X_te, feature_cols,
        )

        predictions = {"baseline_52": base_pred, "opp_66": opp_pred}

        segment_masks = OrderedDict([
            ("overall", np.ones(len(y_te), dtype=bool)),
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

        seg_rows = OrderedDict()
        for seg_name, mask in segment_masks.items():
            if mask.sum() < 30:
                continue
            seg_rows[seg_name] = {
                pred_name: _metrics(y_te[mask], p[mask])
                for pred_name, p in predictions.items()
            }

        out[stat_label] = {
            "baseline_version": base_payload.get("version"),
            "baseline_features": len(base_payload["features"]),
            "opp_version": opp_payload.get("version"),
            "opp_features": len(opp_payload["features"]),
            "test_rows": int(len(y_te)),
            "segments": seg_rows,
            "calibration_baseline": _calibration_buckets(y_te, base_pred),
            "calibration_opp": _calibration_buckets(y_te, opp_pred),
            "top_features_baseline": base_top,
            "top_features_opp": opp_top,
        }
        with open(REPORT, "w") as f:
            json.dump(out, f, indent=2)
        print(f"[opp_eval] {stat_label} saved to {REPORT}", flush=True)
        print(f"[opp_eval]  === {stat_label} overall (RMSE / bias) ===",
              flush=True)
        for seg_name, vals in seg_rows.items():
            cells = "  ".join(
                f"{k}:{v['rmse']:.2f}/{v['bias']:+.2f}"
                for k, v in vals.items()
            )
            print(f"[opp_eval]  {seg_name:25s} {cells}", flush=True)

    # Markdown summary
    with open(SUMMARY, "w") as f:
        f.write("# Opponent-Context Model Comparison (2026-04-23)\n\n")
        f.write("Compares 52-feature pruned baseline vs 66-feature "
                "pruned+opp on the 2024 hold-out.\n\n")
        f.write("## Overall (RMSE / MAE / R² / bias)\n\n")
        f.write("| Stat | baseline RMSE | +opp RMSE | Δ | baseline bias | +opp bias | baseline R² | +opp R² |\n")
        f.write("|------|---:|---:|---:|---:|---:|---:|---:|\n")
        for stat_label, _ in STATS:
            if stat_label not in out:
                continue
            overall = out[stat_label]["segments"].get("overall")
            if not overall:
                continue
            b = overall["baseline_52"]; o = overall["opp_66"]
            f.write(
                f"| {stat_label} | {b['rmse']:.3f} | {o['rmse']:.3f} | "
                f"{o['rmse'] - b['rmse']:+.3f} | "
                f"{b['bias']:+.3f} | {o['bias']:+.3f} | "
                f"{b['r2']} | {o['r2']} |\n"
            )
        f.write("\n## Low-line impact (line<10)\n\n")
        f.write("| Stat | baseline RMSE/bias | +opp RMSE/bias | Δ RMSE | Δ bias |\n")
        f.write("|------|---:|---:|---:|---:|\n")
        for stat_label, _ in STATS:
            if stat_label not in out:
                continue
            seg = out[stat_label]["segments"].get("line<10")
            if not seg:
                continue
            b = seg["baseline_52"]; o = seg["opp_66"]
            f.write(
                f"| {stat_label} | {b['rmse']:.3f} / {b['bias']:+.3f} | "
                f"{o['rmse']:.3f} / {o['bias']:+.3f} | "
                f"{o['rmse'] - b['rmse']:+.3f} | "
                f"{o['bias'] - b['bias']:+.3f} |\n"
            )
        f.write("\n## Top opponent-context features by importance (+opp model)\n\n")
        for stat_label, _ in STATS:
            if stat_label not in out:
                continue
            top = out[stat_label]["top_features_opp"]
            opp_feats_in_top = [
                (n, v) for n, v in top
                if n.startswith("opp_") or n in (
                    "team_pace", "home_flag", "rest_days", "back_to_back_flag",
                )
            ]
            if opp_feats_in_top:
                f.write(f"### {stat_label}\n")
                for n, v in opp_feats_in_top[:6]:
                    f.write(f"- `{n}`: {v:.4f}\n")
                f.write("\n")
    print(f"[opp_eval] ALL DONE. Reports: {REPORT} + {SUMMARY}", flush=True)


if __name__ == "__main__":
    main()

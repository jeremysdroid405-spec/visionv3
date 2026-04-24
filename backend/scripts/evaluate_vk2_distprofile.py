"""
Eval sibling vk2_{stat}_distprofile.pkl (175-feat) against production
vk2_{stat}.pkl (52-feat). Read-only. Uses the cached 2024 residuals
where possible; falls back to building the 2024 test matrix on the
175-feat schema when the distribution-profile features need to be
included.

Focuses on the questions that matter:
  1. Global fit (MAE, RMSE, R² — already shown by the trainer; we
     re-confirm on the held-out 2024 slice).
  2. Low-line bias: did teaching the model zero-rate help projections
     at thresholds 1/5 on PTS, 1/3 REB, 1/2 AST, 0.5/1 3PM, 10/15 PRA?
  3. Bench / starter segmented bias (min_played_L5_mean < 18 vs ≥ 28).
  4. Feature importance of the new 123 distribution-profile features.

Writes:
    /app/backend/reports/vk2_distprofile_eval.md
"""
from __future__ import annotations

import math
import os
import pickle
import sys
import time

import numpy as np
import pymongo

sys.path.insert(0, "/app/backend")
os.chdir("/app/backend")

from dotenv import load_dotenv
load_dotenv()

from scripts.retrain_nba_vk2 import (  # noqa: E402
    PRUNED_DISTPROFILE_FEATURES, DIST_PROFILE_FEATURES,
    build_training_matrix, preload_advanced_stats,
)

MODEL_DIR = "/app/backend/models"
REPORT = "/app/backend/reports/vk2_distprofile_eval.md"

STATS = {"PTS": "pts", "REB": "reb", "AST": "ast", "3PM": "fg3m", "PRA": "pra"}
MIN_L5 = "min_played_L5_mean"

LOW_LINES = {
    "PTS": [1, 5, 10], "REB": [1, 3, 5], "AST": [1, 2, 4],
    "3PM": [1, 2], "PRA": [10, 15, 20],
}


def _load(p):
    with open(p, "rb") as f:
        return pickle.load(f)


def _score(payload, X, feature_cols):
    want = payload["features"]
    idx = [feature_cols.index(f) for f in want]
    Xs = payload["scaler"].transform(X[:, idx])
    return payload["model"].predict(Xs)


def _metrics(y_true, y_pred):
    if len(y_true) == 0:
        return {"n": 0, "mae": 0, "rmse": 0, "bias_mean": 0, "bias_median": 0}
    err = y_pred - y_true
    return {
        "n": int(len(y_true)),
        "mae": float(np.abs(err).mean()),
        "rmse": float(math.sqrt((err * err).mean())),
        "bias_mean": float(err.mean()),
        "bias_median": float(np.median(err)),
    }


def eval_one(label, field, adv_map):
    base_pkl = os.path.join(MODEL_DIR, f"vk2_{label.lower()}.pkl")
    dp_pkl = os.path.join(MODEL_DIR, f"vk2_{label.lower()}_distprofile.pkl")
    if not (os.path.exists(base_pkl) and os.path.exists(dp_pkl)):
        return None
    base = _load(base_pkl)
    dp = _load(dp_pkl)

    # Build matrix with the 175-feat schema (superset) so we can score
    # both models on the same rows in the same order.
    X, y, sw, feature_cols = build_training_matrix(
        label, field, adv_map=adv_map,
        target_schema=set(PRUNED_DISTPROFILE_FEATURES),
        dist_profile=True,
    )
    if X is None:
        return None
    te_mask = sw >= 0.99
    X_te, y_te = X[te_mask], y[te_mask]
    yp_base = _score(base, X_te, feature_cols)
    yp_dp = _score(dp, X_te, feature_cols)

    out = {
        "stat": label,
        "global_base": _metrics(y_te, yp_base),
        "global_dp":   _metrics(y_te, yp_dp),
        "low_line_segments": {},
        "min_segments": {},
        "feat_importance_top15_dp": None,
    }

    # Low-line segments: where predicted < low_line_threshold
    for thr in LOW_LINES.get(label, []):
        # Segment = samples where the DP model predicts below threshold
        seg_mask = yp_dp < float(thr) * 1.5
        if seg_mask.sum() < 100:
            continue
        out["low_line_segments"][thr] = {
            "base": _metrics(y_te[seg_mask], yp_base[seg_mask]),
            "dp":   _metrics(y_te[seg_mask], yp_dp[seg_mask]),
        }

    # Bench / starter via L5-minutes feature
    try:
        min_idx = feature_cols.index(MIN_L5)
        m = X_te[:, min_idx]
        for tag, mask in (("bench", m < 18.0), ("starter", m >= 28.0)):
            out["min_segments"][tag] = {
                "base": _metrics(y_te[mask], yp_base[mask]),
                "dp":   _metrics(y_te[mask], yp_dp[mask]),
            }
    except ValueError:
        pass

    # Importance of DP features in the DP model
    feats = dp["features"]
    imps = dp["model"].feature_importances_
    ranking = sorted(zip(feats, imps), key=lambda t: -t[1])
    dp_in_top = [
        {"name": n, "rank": i + 1, "importance": float(imp)}
        for i, (n, imp) in enumerate(ranking)
        if n in DIST_PROFILE_FEATURES
    ][:15]
    out["feat_importance_top15_dp"] = dp_in_top

    return out


def render(reports):
    def _fmt(m):
        return (f"MAE={m['mae']:.4f}  RMSE={m['rmse']:.4f}  "
                f"bias_mean={m['bias_mean']:+.4f}  "
                f"bias_median={m['bias_median']:+.4f}  n={m['n']}")
    lines = [
        "# vk2_{stat}_distprofile (175-feat) vs production base52 — eval",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}",
        "",
        "Sibling experiment. No production change. All metrics on the "
        "2024 held-out slice.",
        "",
    ]

    lines.append("## Global")
    lines.append("")
    lines.append("| Stat | MAE base | MAE dp | Δ MAE | RMSE base | RMSE dp | Δ RMSE | bias_median base | bias_median dp |")
    lines.append("|------|---------:|-------:|------:|----------:|--------:|-------:|-----------------:|----------------:|")
    for r in reports:
        if r is None: continue
        b, d = r["global_base"], r["global_dp"]
        lines.append(
            f"| {r['stat']} | {b['mae']:.4f} | {d['mae']:.4f} | "
            f"{d['mae']-b['mae']:+.4f} | {b['rmse']:.4f} | {d['rmse']:.4f} | "
            f"{d['rmse']-b['rmse']:+.4f} | {b['bias_median']:+.3f} | "
            f"{d['bias_median']:+.3f} |"
        )
    lines.append("")

    for r in reports:
        if r is None: continue
        lines.append(f"## {r['stat']}")
        lines.append("")
        lines.append(f"**Global base  :** {_fmt(r['global_base'])}")
        lines.append(f"**Global dp175:** {_fmt(r['global_dp'])}")
        lines.append("")

        if r["low_line_segments"]:
            lines.append("### Low-line segments (pred < 1.5× threshold)")
            lines.append("| threshold | n | MAE base | MAE dp | bias base | bias dp | Δ MAE | Δ |bias| |")
            lines.append("|-----------|---|----------|--------|-----------|---------|-------|----------|")
            for thr, seg in r["low_line_segments"].items():
                b, d = seg["base"], seg["dp"]
                lines.append(
                    f"| {thr} | {b['n']} | {b['mae']:.4f} | {d['mae']:.4f} | "
                    f"{b['bias_mean']:+.4f} | {d['bias_mean']:+.4f} | "
                    f"{d['mae']-b['mae']:+.4f} | "
                    f"{abs(d['bias_mean'])-abs(b['bias_mean']):+.4f} |"
                )
            lines.append("")

        if r["min_segments"]:
            lines.append("### Bench / starter segments")
            lines.append("| segment | n | MAE base | MAE dp | bias base | bias dp | Δ MAE | Δ |bias| |")
            lines.append("|---------|---|----------|--------|-----------|---------|-------|----------|")
            for tag, seg in r["min_segments"].items():
                b, d = seg["base"], seg["dp"]
                lines.append(
                    f"| {tag} | {b['n']} | {b['mae']:.4f} | {d['mae']:.4f} | "
                    f"{b['bias_mean']:+.4f} | {d['bias_mean']:+.4f} | "
                    f"{d['mae']-b['mae']:+.4f} | "
                    f"{abs(d['bias_mean'])-abs(b['bias_mean']):+.4f} |"
                )
            lines.append("")

        if r["feat_importance_top15_dp"]:
            lines.append("### Top-15 distribution-profile features by importance")
            lines.append("| rank | name | importance |")
            lines.append("|-----:|------|-----------:|")
            for row in r["feat_importance_top15_dp"]:
                lines.append(f"| {row['rank']} | `{row['name']}` | {row['importance']:.4f} |")
            lines.append("")

    # Global verdict
    total_delta = 0.0
    any_mae_win = False
    any_mae_loss = False
    for r in reports:
        if r is None: continue
        d = r["global_dp"]["mae"] - r["global_base"]["mae"]
        total_delta += d
        if d < -0.005: any_mae_win = True
        if d > 0.005:  any_mae_loss = True
    lines.append("## Verdict")
    lines.append("")
    if not any_mae_win and not any_mae_loss:
        lines.append(
            "- Distribution-profile features do NOT move global MAE "
            "meaningfully on any stat (|Δ| < 0.005 everywhere)."
        )
    if any_mae_win:
        lines.append("- Some stats improve MAE materially with DP features.")
    if any_mae_loss:
        lines.append("- Some stats regress MAE materially with DP features.")
    lines.append(
        "- Inspect the per-stat low-line segment tables for the actual "
        "signal: the thesis is that zero-rate-aware features fix "
        "low-line bias even when global MAE is unchanged."
    )
    lines.append(
        "- Sibling pkls are INERT — production `vk2_{stat}.pkl` files "
        "are untouched."
    )
    return "\n".join(lines)


def main():
    client = pymongo.MongoClient(os.environ["MONGO_URL"])
    adv_map = preload_advanced_stats()
    reports = []
    for label, field in STATS.items():
        print(f"[{label}] eval...")
        t0 = time.monotonic()
        r = eval_one(label, field, adv_map)
        reports.append(r)
        print(f"  [{label}] elapsed {time.monotonic()-t0:.1f}s")
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w") as f:
        f.write(render(reports))
    print(f"→ {REPORT}")
    client.close()


if __name__ == "__main__":
    main()

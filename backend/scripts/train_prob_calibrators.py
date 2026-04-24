"""
Per-stat isotonic probability calibrator training (2026-04-23).

Addresses the calibration gap exposed in `reports/vk2_calibration_audit.md`:
at extreme lines the Gaussian-CDF `p_over` over-states the true over-rate by
+13 pp (AST @ 1.5) to +21 pp (3PM @ 0.5), and under-states it by up to -8 pp
at very high lines. Sigma rescale doesn't help because the empirical residual
std already matches the model's σ — the failure is the Gaussian tail
assumption at the distribution edges.

Fix: train a monotonic `IsotonicRegression` per stat that maps the raw
Gaussian `p_over` to an empirical over-rate, learnt from the 2024 held-out
split. Projection is untouched; only `p_over` is rewritten.

Training dataset per stat
-------------------------
For each 2024 held-out sample (projection, actual_value):
    for line in SYMBOLIC_LINES[stat]:
        raw_p_over  = Phi((projection - line) / sigma)
        actual_over = (actual_value > line)
    → (raw_p_over, actual_over) pair
Isotonic fit: y = actual_over, x = raw_p_over. Output: 1-D step function
mapping [0,1] → [0,1], non-decreasing, matching empirical over-rates.

Output pkls (one per stat):
    /app/backend/models/prob_calibrator_{stat}.pkl
      {
        "stat": "PTS",
        "version": "NBA_VK2_ISOTONIC_v1",
        "trained_at": "...",
        "calibrator": IsotonicRegression instance,
        "sigma_used": float,
        "symbolic_lines": [...],
        "n_pairs": int,
        "eval_before": [...],
        "eval_after":  [...],
      }

No live I/O. Read-only on MongoDB + production VK2 pkls.
"""
from __future__ import annotations

import json
import math
import os
import pickle
import sys
import time
from datetime import datetime, timezone

import numpy as np
import pymongo
from scipy.stats import norm
from sklearn.isotonic import IsotonicRegression

sys.path.insert(0, "/app/backend")
os.chdir("/app/backend")

from dotenv import load_dotenv
load_dotenv()

from scripts.retrain_nba_vk2 import (  # noqa: E402
    PRUNED_FEATURES, build_training_matrix, preload_advanced_stats,
)

MODEL_DIR = "/app/backend/models"
REPORT_PATH = "/app/backend/reports/vk2_prob_calibration.md"

STATS = {
    "PTS": "pts", "REB": "reb", "AST": "ast",
    "3PM": "fg3m", "PRA": "pra",
}
# Symbolic lines — span the operational range we care about per stat. Must
# match (roughly) the line grid used in the calibration audit so the
# before/after tables compare like for like.
SYMBOLIC_LINES = {
    "PTS": [4.5, 6.5, 9.5, 12.5, 15.5, 19.5, 24.5, 29.5],
    "REB": [2.5, 3.5, 4.5, 5.5, 7.5, 9.5, 11.5],
    "AST": [1.5, 2.5, 3.5, 4.5, 6.5, 8.5, 10.5],
    "3PM": [0.5, 1.5, 2.5, 3.5, 4.5],
    "PRA": [9.5, 15.5, 22.5, 29.5, 36.5, 45.5, 55.5],
}


def _load(path):
    with open(path, "rb") as f:
        return pickle.load(f)


def _score(payload, X, feature_cols):
    want = payload["features"]
    idx = [feature_cols.index(f) for f in want]
    Xs = payload["scaler"].transform(X[:, idx])
    return payload["model"].predict(Xs)


def _eval_gap(y_actual, y_pred, sigma, lines, calibrator=None):
    """For each symbolic line, compute n, projected-P(over) mean and
    actual over-rate. `calibrator` (optional) is applied to the raw
    Gaussian p_over before aggregation."""
    rows = []
    for line in lines:
        tol = max(2.0, 0.25 * max(1.0, line))
        near = np.abs(y_pred - line) < tol
        if near.sum() < 100:
            continue
        raw = 1.0 - norm.cdf(line, loc=y_pred[near], scale=sigma)
        if calibrator is not None:
            prob = calibrator.transform(raw)
        else:
            prob = raw
        actual = (y_actual[near] > line).astype(float)
        rows.append({
            "line": float(line), "n": int(near.sum()),
            "pred_mean": float(prob.mean()),
            "actual_over_rate": float(actual.mean()),
            "gap": float(prob.mean() - actual.mean()),
        })
    return rows


def train_one(label, field, adv_map):
    path = os.path.join(MODEL_DIR, f"vk2_{label.lower()}.pkl")
    if not os.path.exists(path):
        return None
    payload = _load(path)
    sigma = float(payload["residual_sigma_empirical"])

    X, y, sw, feature_cols = build_training_matrix(
        label, field, adv_map=adv_map,
        target_schema=set(PRUNED_FEATURES),
    )
    if X is None:
        return None

    # 2024 held-out mask — same split VK2 used.
    test_mask = sw >= 0.99
    X_te, y_te = X[test_mask], y[test_mask]
    yp = _score(payload, X_te, feature_cols)

    # Build (raw_p_over, actual_over) pairs across all symbolic lines.
    lines = SYMBOLIC_LINES[label]
    raw_probs, targets, weights = [], [], []
    for line in lines:
        raw = 1.0 - norm.cdf(line, loc=yp, scale=sigma)
        actual_over = (y_te > line).astype(float)
        # Weight by inverse distance to line center of the stat's dynamic
        # range — no: use uniform weight. Isotonic benefits from high
        # density at all probability buckets, and we already filter to
        # lines near the projection range.
        raw_probs.append(raw)
        targets.append(actual_over)
        weights.append(np.ones_like(raw))
    raw_probs = np.concatenate(raw_probs)
    targets = np.concatenate(targets)
    weights = np.concatenate(weights)

    # Fit isotonic regression.
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    iso.fit(raw_probs, targets, sample_weight=weights)

    # Before / after evaluation on the same symbolic-line grid.
    eval_before = _eval_gap(y_te, yp, sigma, lines, calibrator=None)
    eval_after  = _eval_gap(y_te, yp, sigma, lines, calibrator=iso)

    # Persist
    pkl_path = os.path.join(MODEL_DIR, f"prob_calibrator_{label.lower()}.pkl")
    out = {
        "stat": label,
        "version": "NBA_VK2_ISOTONIC_v1",
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "calibrator": iso,
        "sigma_used": sigma,
        "symbolic_lines": lines,
        "n_pairs": int(len(raw_probs)),
        "eval_before": eval_before,
        "eval_after": eval_after,
    }
    with open(pkl_path, "wb") as f:
        pickle.dump(out, f)
    return out


def render_md(results):
    lines = [
        "# VK2 Isotonic Probability Calibration — train + audit",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}",
        "",
        "Per-stat `IsotonicRegression` trained on (raw Gaussian P(over),"
        " empirical over-rate) pairs from the 2024 held-out split. "
        "Projection (`model_projection`) is UNCHANGED — only "
        "`p_over` passes through the calibrator.",
        "",
    ]
    for r in results:
        if r is None:
            continue
        lines.append(f"## {r['stat']} (n pairs = {r['n_pairs']:,}, σ = {r['sigma_used']:.3f})")
        lines.append("")
        lines.append("| line | n | before P(over) | after P(over) | actual over-rate | Δ before | Δ after |")
        lines.append("|------|---|----------------|---------------|------------------|----------|---------|")
        before_by_line = {b["line"]: b for b in r["eval_before"]}
        after_by_line = {a["line"]: a for a in r["eval_after"]}
        for line in r["symbolic_lines"]:
            b = before_by_line.get(line); a = after_by_line.get(line)
            if b is None or a is None:
                continue
            lines.append(
                f"| {line} | {b['n']} | {b['pred_mean']:.3f} | "
                f"{a['pred_mean']:.3f} | {b['actual_over_rate']:.3f} | "
                f"{b['gap']:+.3f} | {a['gap']:+.3f} |"
            )
        lines.append("")
    lines.append("## Interpretation")
    lines.append(
        "- `before P(over)` is the raw Gaussian output Phi((proj − line) / σ) "
        "— the current production value.")
    lines.append(
        "- `after P(over)` is the isotonic-calibrated output — what the "
        "scoring adapter will emit after the layer is wired in.")
    lines.append(
        "- `Δ before` / `Δ after` = pred − actual over-rate. "
        "Smaller |Δ after| ⇒ better calibration at that line.")
    lines.append("")
    return "\n".join(lines)


def main():
    client = pymongo.MongoClient(os.environ["MONGO_URL"])
    adv_map = preload_advanced_stats()
    results = []
    for label, field in STATS.items():
        print(f"[{label}] training calibrator...")
        t0 = time.monotonic()
        r = train_one(label, field, adv_map)
        results.append(r)
        print(f"  [{label}] elapsed {time.monotonic()-t0:.1f}s")

    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        f.write(render_md(results))
    print(f"\n→ {REPORT_PATH}")
    for r in results:
        if r:
            print(f"  {r['stat']}: n={r['n_pairs']:,} "
                  f"pkl=/app/backend/models/prob_calibrator_{r['stat'].lower()}.pkl")
    client.close()


if __name__ == "__main__":
    main()

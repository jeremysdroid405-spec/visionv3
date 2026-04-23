"""
VK2 Production Calibration / Bias Audit (2026-04-23)

Read-only. Scores the 2024 held-out split (same mask VK2 trained with)
under each production `vk2_{stat}.pkl` and measures systematic bias +
calibration at global, bucketed, and probability levels. Also simulates
three correction strategies without touching any pkl.

Outputs:
  * /app/backend/reports/vk2_calibration_audit.md  (markdown report)
  * /app/backend/reports/vk2_calibration_audit.json (raw metrics)

Bucketing conventions
---------------------
We have historical game labels (target actuals) but NO market lines for
these historical rows — so every "line" reference below uses the
**projection itself as a line proxy**. This is the standard calibration
proxy and it is what the scoring pipeline uses at inference:
  - `line_bucket`    : deciles of projection magnitude
  - `minutes_bucket` : bench / rotation / starter based on min_played_L5_mean
  - `tier_bucket`    : proxy tiers from relative projection magnitude
                       (low / medium / high) — approximates the UI tier split
  - `over_vs_under`  : whether the projection exceeds the per-player L10 mean
                       (model's own implied "over" vs its rolling baseline)

Calibration corrections tested (all simulated — no pkl mutation)
----------------------------------------------------------------
  (A) Global intercept shift  : y' = y - mean_bias_global
  (B) Line-bucket correction  : y' = y - mean_bias[proj_bucket(y)]
  (C) Minutes-bucket correction: y' = y - mean_bias[min_bucket(x)]
  (D) Probability-only recal  : widen/tighten residual σ to match
                                 empirical 2024 residual std
"""
from __future__ import annotations

import json
import math
import os
import pickle
import sys
import time
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional

import numpy as np
import pymongo
from scipy.stats import norm

sys.path.insert(0, "/app/backend")
os.chdir("/app/backend")

from dotenv import load_dotenv
load_dotenv()

from scripts.retrain_nba_vk2 import (  # noqa: E402
    PRUNED_FEATURES,
    build_training_matrix,
    preload_advanced_stats,
)

MODEL_DIR = "/app/backend/models"
REPORT_MD   = "/app/backend/reports/vk2_calibration_audit.md"
REPORT_JSON = "/app/backend/reports/vk2_calibration_audit.json"

STATS = {
    "PTS": "pts",
    "REB": "reb",
    "AST": "ast",
    "3PM": "fg3m",
    "PRA": "pra",
}
# Lines (symbolic; used for prob-calibration slice only)
PROB_LINES = {
    "PTS": [6.5, 9.5, 12.5, 15.5, 19.5, 24.5],
    "REB": [2.5, 3.5, 4.5, 5.5, 7.5, 9.5],
    "AST": [1.5, 2.5, 3.5, 4.5, 6.5, 8.5],
    "3PM": [0.5, 1.5, 2.5, 3.5],
    "PRA": [15.5, 22.5, 29.5, 36.5, 45.5],
}

MIN_L5_COL = "min_played_L5_mean"


# ---------- helpers ----------
def _load(path):
    with open(path, "rb") as f:
        return pickle.load(f)


def _metrics(y_true, y_pred):
    if len(y_true) == 0:
        return {"n": 0, "mean_pred": 0, "mean_actual": 0,
                "bias_mean": 0, "bias_median": 0, "mae": 0, "rmse": 0}
    err = y_pred - y_true
    return {
        "n": int(len(y_true)),
        "mean_pred":   float(y_pred.mean()),
        "mean_actual": float(y_true.mean()),
        "bias_mean":   float(err.mean()),
        "bias_median": float(np.median(err)),
        "mae":  float(np.abs(err).mean()),
        "rmse": float(math.sqrt((err * err).mean())),
    }


def _score(payload, X_full, feature_cols_full):
    want = payload["features"]
    idx = [feature_cols_full.index(f) for f in want]
    Xs = payload["scaler"].transform(X_full[:, idx])
    return payload["model"].predict(Xs)


def _tier_buckets(y_pred):
    """Approximate PropVision tier split:
       low <= p33, medium p33-p66, high > p66."""
    p33, p66 = np.percentile(y_pred, [33.33, 66.66])
    tiers = np.full(len(y_pred), "medium", dtype=object)
    tiers[y_pred <= p33] = "low"
    tiers[y_pred >= p66] = "high"
    return tiers, p33, p66


def _minutes_buckets(min_l5):
    """Same thresholds as the opportunity adapter / scoring pipeline."""
    b = np.full(len(min_l5), "rotation", dtype=object)
    b[min_l5 < 18.0] = "bench"
    b[min_l5 >= 28.0] = "starter"
    return b


def _decile_edges(y_pred):
    return np.quantile(y_pred, np.linspace(0.1, 0.9, 9))


def _segment(y_true, y_pred, mask, label):
    m = _metrics(y_true[mask], y_pred[mask]) if mask.sum() > 0 else None
    return {"label": label, **(m or {})}


def audit_one(label, field, adv_map):
    t0 = time.monotonic()
    path = os.path.join(MODEL_DIR, f"vk2_{label.lower()}.pkl")
    if not os.path.exists(path):
        return None
    payload = _load(path)

    # Build pruned52 matrix (no opportunity adapter — prod doesn't use it).
    X, y, sw, feature_cols = build_training_matrix(
        label, field, adv_map=adv_map,
        target_schema=set(PRUNED_FEATURES),
        opp_store=None, opportunity_adapter=None,
    )
    if X is None:
        return None

    # 2024-held-out mask (sample_weight == 1.0 in training).
    test_mask = sw >= 0.99
    X_te, y_te = X[test_mask], y[test_mask]

    yp = _score(payload, X_te, feature_cols)
    residual_sigma = float(payload.get("residual_sigma_empirical", 1.0))

    rep = {"stat": label, "n_test": int(len(y_te)),
           "residual_sigma_model": residual_sigma}

    # -- 1-4: global
    rep["global"] = _metrics(y_te, yp)

    # -- 5: line bucket (deciles on predicted value)
    edges = _decile_edges(yp)
    bucket_idx = np.digitize(yp, edges)  # 0..9
    line_buckets = []
    for b in range(10):
        m = bucket_idx == b
        if m.sum() < 30:
            continue
        rng = (float(yp[m].min()), float(yp[m].max()))
        seg = _metrics(y_te[m], yp[m])
        seg["bucket"] = b
        seg["range"] = [round(rng[0], 2), round(rng[1], 2)]
        line_buckets.append(seg)
    rep["line_buckets"] = line_buckets

    # -- 6: minutes bucket via feature column
    try:
        min_idx = feature_cols.index(MIN_L5_COL)
        min_l5 = X_te[:, min_idx]
        mbuckets = _minutes_buckets(min_l5)
        rep["minutes_buckets"] = [
            _segment(y_te, yp, mbuckets == name, name)
            for name in ("bench", "rotation", "starter")
        ]
    except ValueError:
        rep["minutes_buckets"] = []

    # -- 7: tier proxy
    tiers, p33, p66 = _tier_buckets(yp)
    rep["tier_buckets"] = [
        _segment(y_te, yp, tiers == name, name)
        for name in ("low", "medium", "high")
    ]
    rep["tier_thresholds"] = {"p33": float(p33), "p66": float(p66)}

    # -- 8: OVER vs UNDER relative to per-sample L10 mean
    try:
        l10_idx = feature_cols.index(f"{field}_L10_mean") if field != "pra" else feature_cols.index("pra_L10_mean")
        l10 = X_te[:, l10_idx]
        over_mask = yp >= l10   # model says "over its own rolling baseline"
        rep["ou_buckets"] = [
            _segment(y_te, yp, over_mask, "model_says_over"),
            _segment(y_te, yp, ~over_mask, "model_says_under"),
        ]
    except ValueError:
        rep["ou_buckets"] = []

    # -- 9-10: probability calibration at symbolic lines using σ
    prob_rows = []
    for line in PROB_LINES[label]:
        # Only rows whose projection is within ±25% of the line
        tol = max(2.0, 0.25 * max(1.0, line))
        near = np.abs(yp - line) < tol
        if near.sum() < 100:
            continue
        # Model-implied P(actual > line) assuming N(yp, σ)
        p_over_model = 1 - norm.cdf(line, loc=yp[near], scale=residual_sigma)
        actual_over = (y_te[near] > line).astype(float)
        prob_rows.append({
            "line": line,
            "n": int(near.sum()),
            "pred_over_mean": float(p_over_model.mean()),
            "actual_over_rate": float(actual_over.mean()),
            "calibration_gap": float(p_over_model.mean() - actual_over.mean()),
        })
    rep["prob_calibration"] = prob_rows

    # ------------------------------------------------------------------
    # Correction simulations (before/after on 2024 held-out)
    # ------------------------------------------------------------------
    # (A) Global intercept shift
    bias_global = rep["global"]["bias_mean"]
    yp_A = yp - bias_global
    rep["correction_A_global"] = {
        "delta": float(-bias_global),
        **_metrics(y_te, yp_A),
    }

    # (B) Line-bucket correction — fit on train (use bucket mean bias on
    # 2024 itself would be trivially zero; we fit on 80% and test on 20%
    # inside 2024).
    rng = np.random.default_rng(42)
    idx = np.arange(len(y_te))
    rng.shuffle(idx)
    split = int(0.8 * len(idx))
    fit_idx, val_idx = idx[:split], idx[split:]
    edges_fit = np.quantile(yp[fit_idx], np.linspace(0.1, 0.9, 9))
    bucket_fit = np.digitize(yp[fit_idx], edges_fit)
    bucket_val = np.digitize(yp[val_idx], edges_fit)
    bucket_bias = {}
    for b in range(10):
        m = bucket_fit == b
        bucket_bias[b] = float((yp[fit_idx][m] - y_te[fit_idx][m]).mean()) if m.sum() >= 30 else 0.0
    corr = np.array([bucket_bias.get(int(b), 0.0) for b in bucket_val])
    yp_B_val = yp[val_idx] - corr
    rep["correction_B_line_bucket"] = {
        "fit_n": int(len(fit_idx)),
        "val_n": int(len(val_idx)),
        "bucket_bias": {str(k): round(v, 4) for k, v in bucket_bias.items()},
        **_metrics(y_te[val_idx], yp_B_val),
    }
    rep["correction_B_val_baseline"] = _metrics(y_te[val_idx], yp[val_idx])

    # (C) Minutes-bucket correction — same 80/20 inside 2024
    try:
        min_idx = feature_cols.index(MIN_L5_COL)
        min_l5 = X_te[:, min_idx]
        mb_fit = _minutes_buckets(min_l5[fit_idx])
        mb_val = _minutes_buckets(min_l5[val_idx])
        mb_bias = {}
        for name in ("bench", "rotation", "starter"):
            m = mb_fit == name
            mb_bias[name] = float((yp[fit_idx][m] - y_te[fit_idx][m]).mean()) if m.sum() >= 30 else 0.0
        corr = np.array([mb_bias[name] for name in mb_val])
        yp_C_val = yp[val_idx] - corr
        rep["correction_C_minutes_bucket"] = {
            "bias_by_bucket": {k: round(v, 4) for k, v in mb_bias.items()},
            **_metrics(y_te[val_idx], yp_C_val),
        }
    except ValueError:
        rep["correction_C_minutes_bucket"] = None

    # (D) Probability-only recalibration — scale σ so that the empirical
    # residual std matches the model's implied std. Projection unchanged.
    emp_sigma = float(np.std(y_te - yp, ddof=1))
    sigma_ratio = emp_sigma / residual_sigma if residual_sigma > 0 else 1.0
    # Re-compute prob calibration at the same lines with the new σ
    prob_rows_D = []
    for line in PROB_LINES[label]:
        tol = max(2.0, 0.25 * max(1.0, line))
        near = np.abs(yp - line) < tol
        if near.sum() < 100:
            continue
        p_over_new = 1 - norm.cdf(line, loc=yp[near], scale=emp_sigma)
        actual_over = (y_te[near] > line).astype(float)
        prob_rows_D.append({
            "line": line, "n": int(near.sum()),
            "pred_over_mean": float(p_over_new.mean()),
            "actual_over_rate": float(actual_over.mean()),
            "calibration_gap": float(p_over_new.mean() - actual_over.mean()),
        })
    rep["correction_D_prob_only"] = {
        "model_sigma": residual_sigma,
        "empirical_sigma": emp_sigma,
        "sigma_ratio": sigma_ratio,
        "prob_calibration": prob_rows_D,
    }

    rep["_elapsed_s"] = round(time.monotonic() - t0, 2)
    return rep


# ---------- Markdown renderer ----------
def render_md(reports):
    def fmt_m(m):
        return (f"n={m['n']}  pred={m['mean_pred']:.3f}  actual={m['mean_actual']:.3f}  "
                f"bias_mean={m['bias_mean']:+.4f}  bias_median={m['bias_median']:+.4f}  "
                f"MAE={m['mae']:.4f}  RMSE={m['rmse']:.4f}")
    out = [
        "# VK2 Production Calibration / Bias Audit",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}",
        "",
        "Models audited: production `vk2_{pts,reb,ast,3pm,pra}.pkl` (52-feat pruned). "
        "Held-out = 2024 test mask (sample_weight==1.0 in training).",
        "",
        "Directional convention: **bias = projection − actual**. "
        "**Positive bias ⇒ VK2 over-projects; negative ⇒ under-projects.**",
        "",
    ]
    # Headline table
    out.append("## Headline (2024 held-out)")
    out.append("")
    out.append("| Stat | n | mean pred | mean actual | bias mean | bias median | MAE | RMSE | σ_residual |")
    out.append("|------|---|-----------|-------------|-----------|-------------|-----|------|------------|")
    for r in reports:
        if r is None: continue
        g = r["global"]
        out.append(f"| {r['stat']} | {g['n']} | {g['mean_pred']:.3f} | {g['mean_actual']:.3f} | "
                   f"{g['bias_mean']:+.4f} | {g['bias_median']:+.4f} | {g['mae']:.4f} | {g['rmse']:.4f} | "
                   f"{r['residual_sigma_model']:.3f} |")
    out.append("")

    for r in reports:
        if r is None: continue
        stat = r["stat"]
        out.append(f"## {stat}")
        out.append("")

        out.append(f"**Global:** {fmt_m(r['global'])}")
        out.append("")

        # 5. line buckets
        out.append("### 5. Bias by line-bucket (deciles of projection)")
        out.append("| bucket | range | n | bias mean | bias median | MAE |")
        out.append("|--------|-------|---|-----------|-------------|-----|")
        for s in r["line_buckets"]:
            out.append(f"| {s['bucket']} | [{s['range'][0]}, {s['range'][1]}] | {s['n']} | "
                       f"{s['bias_mean']:+.4f} | {s['bias_median']:+.4f} | {s['mae']:.4f} |")
        out.append("")

        # 6. minutes buckets
        out.append("### 6. Bias by minutes bucket")
        out.append("| bucket | n | bias mean | bias median | MAE |")
        out.append("|--------|---|-----------|-------------|-----|")
        for s in r["minutes_buckets"]:
            out.append(f"| {s['label']} | {s['n']} | {s['bias_mean']:+.4f} | "
                       f"{s['bias_median']:+.4f} | {s['mae']:.4f} |")
        out.append("")

        # 7. tier buckets
        out.append(f"### 7. Bias by tier (proxy: p33={r['tier_thresholds']['p33']:.2f}, "
                   f"p66={r['tier_thresholds']['p66']:.2f})")
        out.append("| tier | n | bias mean | bias median | MAE |")
        out.append("|------|---|-----------|-------------|-----|")
        for s in r["tier_buckets"]:
            out.append(f"| {s['label']} | {s['n']} | {s['bias_mean']:+.4f} | "
                       f"{s['bias_median']:+.4f} | {s['mae']:.4f} |")
        out.append("")

        # 8. OU
        out.append("### 8. Bias by model OVER vs UNDER its own L10 baseline")
        out.append("| stance | n | bias mean | bias median | MAE |")
        out.append("|--------|---|-----------|-------------|-----|")
        for s in r["ou_buckets"]:
            out.append(f"| {s['label']} | {s['n']} | {s['bias_mean']:+.4f} | "
                       f"{s['bias_median']:+.4f} | {s['mae']:.4f} |")
        out.append("")

        # 9-10. prob calibration
        out.append("### 9-10. Actual vs projected over-rate by symbolic line")
        out.append("| line | n | projected P(over) | actual over-rate | gap |")
        out.append("|------|---|-------------------|------------------|-----|")
        for row in r["prob_calibration"]:
            out.append(f"| {row['line']} | {row['n']} | {row['pred_over_mean']:.3f} | "
                       f"{row['actual_over_rate']:.3f} | {row['calibration_gap']:+.3f} |")
        out.append("")

        # Corrections
        out.append("### Correction simulations (before / after)")
        base = r["global"]
        A = r["correction_A_global"]
        out.append(f"**(A) Global intercept shift**  y' = y − {A['delta']:+.4f}")
        out.append(f"- baseline: MAE={base['mae']:.4f} RMSE={base['rmse']:.4f} bias_mean={base['bias_mean']:+.4f}")
        out.append(f"- after   : MAE={A['mae']:.4f} RMSE={A['rmse']:.4f} bias_mean={A['bias_mean']:+.4f}")
        out.append("")

        B = r["correction_B_line_bucket"]
        vb = r["correction_B_val_baseline"]
        out.append("**(B) Line-bucket correction** (fit 80% / val 20% inside 2024)")
        out.append(f"- val baseline: MAE={vb['mae']:.4f} RMSE={vb['rmse']:.4f} bias_mean={vb['bias_mean']:+.4f}")
        out.append(f"- val after   : MAE={B['mae']:.4f} RMSE={B['rmse']:.4f} bias_mean={B['bias_mean']:+.4f}")
        out.append(f"- bucket_bias: {B['bucket_bias']}")
        out.append("")

        C = r["correction_C_minutes_bucket"]
        if C is not None:
            out.append("**(C) Minutes-bucket correction** (fit 80% / val 20%)")
            out.append(f"- val after   : MAE={C['mae']:.4f} RMSE={C['rmse']:.4f} bias_mean={C['bias_mean']:+.4f}")
            out.append(f"- bias by bucket: {C['bias_by_bucket']}")
            out.append("")

        D = r["correction_D_prob_only"]
        out.append("**(D) Probability-only recalibration** (σ rescale, projection unchanged)")
        out.append(f"- model σ={D['model_sigma']:.3f}  empirical σ={D['empirical_sigma']:.3f}  "
                   f"ratio={D['sigma_ratio']:.3f}")
        if D["prob_calibration"]:
            out.append("| line | proj P(over) [new σ] | actual over-rate | gap |")
            out.append("|------|-----------------------|------------------|-----|")
            for row in D["prob_calibration"]:
                out.append(f"| {row['line']} | {row['pred_over_mean']:.3f} | "
                           f"{row['actual_over_rate']:.3f} | {row['calibration_gap']:+.3f} |")
        out.append("")

    # Overall recommendation (computed at render time)
    out.append("## Overall Recommendation")
    recs = []
    any_over = any(r and r["global"]["bias_mean"] > 0.10 for r in reports if r)
    any_bench_over = any(
        r and any(b["label"] == "bench" and b["bias_mean"] > 0.05
                  for b in r["minutes_buckets"])
        for r in reports if r
    )
    any_prob_miss = any(
        r and any(abs(row["calibration_gap"]) > 0.03 for row in r["prob_calibration"])
        for r in reports if r
    )
    if any_over:
        recs.append("- One or more stats show global mean bias > 0.10 — "
                    "VK2 IS globally over-projecting.")
    else:
        recs.append("- No stat shows global bias > 0.10 — VK2 is NOT globally "
                    "over-projecting; residual bias is segment-specific.")
    if any_bench_over:
        recs.append("- Bench-bucket bias exceeds 0.05 — localised low-minute overshoot persists.")
    if any_prob_miss:
        recs.append("- Probability calibration gap > 3 pp at some lines — σ rescale will help.")
    out.extend(recs)
    out.append("")
    return "\n".join(out)


def recommend(reports):
    """Produce a per-stat KEEP / REJECT recommendation for each correction."""
    recs = []
    for r in reports:
        if r is None: continue
        stat = r["stat"]
        A = r["correction_A_global"]; base = r["global"]
        a_ok = (abs(A["bias_mean"]) < abs(base["bias_mean"]) * 0.5) and (A["mae"] <= base["mae"] + 0.01)
        B = r["correction_B_line_bucket"]; vb = r["correction_B_val_baseline"]
        b_ok = (abs(B["bias_mean"]) < abs(vb["bias_mean"]) * 0.5) and (B["mae"] <= vb["mae"] + 0.01)
        C = r["correction_C_minutes_bucket"]
        if C:
            # Compare C on val split vs baseline on val split (same indices were
            # used internally). Approximate using val baseline from B.
            c_ok = (abs(C["bias_mean"]) < abs(vb["bias_mean"]) * 0.5) and (C["mae"] <= vb["mae"] + 0.01)
        else:
            c_ok = False
        D = r["correction_D_prob_only"]
        sigma_delta = abs(D["sigma_ratio"] - 1.0)
        d_ok = sigma_delta > 0.05  # worth applying if σ mis-scaled > 5%
        recs.append({
            "stat": stat,
            "A_global":          "KEEP" if a_ok else "REJECT",
            "B_line_bucket":     "KEEP" if b_ok else "REJECT",
            "C_minutes_bucket":  "KEEP" if c_ok else "REJECT",
            "D_prob_only":       "KEEP" if d_ok else "REJECT",
            "A_delta":           round(A["delta"], 4),
            "D_sigma_ratio":     round(D["sigma_ratio"], 4),
        })
    return recs


def main():
    client = pymongo.MongoClient(os.environ["MONGO_URL"])
    adv_map = preload_advanced_stats()
    reports = []
    for label, field in STATS.items():
        print(f"[{label}] auditing...")
        reports.append(audit_one(label, field, adv_map))
    rec = recommend(reports)
    # Append KEEP/REJECT table to markdown
    md = render_md(reports)
    md += "\n## KEEP / REJECT per correction\n\n"
    md += "| stat | A global | B line-bucket | C minutes-bucket | D prob-only | A Δ | D σ-ratio |\n"
    md += "|------|----------|---------------|------------------|-------------|-----|-----------|\n"
    for row in rec:
        md += (f"| {row['stat']} | {row['A_global']} | {row['B_line_bucket']} | "
               f"{row['C_minutes_bucket']} | {row['D_prob_only']} | "
               f"{row['A_delta']:+.4f} | {row['D_sigma_ratio']:.3f} |\n")

    os.makedirs(os.path.dirname(REPORT_MD), exist_ok=True)
    with open(REPORT_MD, "w") as f:
        f.write(md)
    with open(REPORT_JSON, "w") as f:
        json.dump({"reports": reports, "recommendations": rec}, f, indent=2, default=float)
    print(f"\n→ {REPORT_MD}")
    print(f"→ {REPORT_JSON}")
    client.close()


if __name__ == "__main__":
    main()

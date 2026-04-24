"""
VK2 Distribution Audit + Probability-Method Comparison (2026-04-23)

Read-only. Tests 6 alternatives to the current Gaussian P(over) on the
2024 held-out slice, diagnoses residual distributions, and picks a
winner per stat. No retraining, no feature changes, no projection
changes.

Methods compared
----------------
  A: Gaussian_raw       — current production baseline (Phi)
  B: Isotonic_global    — current `prob_calibrator_{stat}.pkl`
  C: Isotonic_bucketed  — 3 line-magnitude buckets × isotonic per bucket
  D: Empirical_CDF      — non-parametric; draws P(over) from the
                          empirical residual distribution of samples
                          in the same projection bucket
  E: Lognormal          — for PTS / PRA (long-tailed, right-skewed)
  F: Poisson_NB         — for 3PM (count stat with few discrete values)
  G: Skewnormal         — for PTS / PRA

Diagnostics
-----------
For every stat we report: residual mean, std, skew, excess kurtosis,
5th / 95th percentile, and ratio of tail-beyond-2σ to the Gaussian
expectation of 4.55%. Anything meaningfully different from (0, 0, ~4.55%)
flags Gaussian as structurally wrong for that stat.

Scoring each method
-------------------
For each symbolic line per stat we compute:
  n       : samples within ±25% of that line
  pred    : mean(predicted P(over)) for that method
  actual  : empirical over-rate
  gap     : pred - actual
Then the headline is `Σ n · |gap|` — a volume-weighted calibration
error — alongside Brier score and edge-stability std of Δp.

Outputs
-------
  /app/backend/reports/vk2_distribution_audit.md
  /app/backend/reports/vk2_distribution_audit.json
  /app/backend/reports/_residual_cache.npz  (rebuilt lazily)
"""
from __future__ import annotations

import json
import math
import os
import pickle
import sys
import time
from typing import Any, Dict, List, Optional

import numpy as np
import pymongo
from scipy import stats as sstats
from scipy.stats import norm, lognorm, nbinom, poisson, skewnorm
from sklearn.isotonic import IsotonicRegression

sys.path.insert(0, "/app/backend")
os.chdir("/app/backend")

from dotenv import load_dotenv
load_dotenv()

from scripts.retrain_nba_vk2 import (  # noqa: E402
    PRUNED_FEATURES, build_training_matrix, preload_advanced_stats,
)

MODEL_DIR = "/app/backend/models"
CACHE_PATH = "/app/backend/reports/_residual_cache.npz"
REPORT_MD = "/app/backend/reports/vk2_distribution_audit.md"
REPORT_JSON = "/app/backend/reports/vk2_distribution_audit.json"

STATS = {
    "PTS": "pts",
    "REB": "reb",
    "AST": "ast",
    "3PM": "fg3m",
    "PRA": "pra",
}

SYMBOLIC_LINES = {
    "PTS": [4.5, 6.5, 9.5, 12.5, 15.5, 19.5, 24.5, 29.5],
    "REB": [2.5, 3.5, 4.5, 5.5, 7.5, 9.5, 11.5],
    "AST": [1.5, 2.5, 3.5, 4.5, 6.5, 8.5, 10.5],
    "3PM": [0.5, 1.5, 2.5, 3.5, 4.5],
    "PRA": [9.5, 15.5, 22.5, 29.5, 36.5, 45.5, 55.5],
}


# -----------------------------------------------------------------------
# Phase 1 — cache (yp, y_te, sigma) per stat on 2024 held-out
# -----------------------------------------------------------------------
def build_or_load_cache():
    if os.path.exists(CACHE_PATH):
        data = dict(np.load(CACHE_PATH, allow_pickle=True))
        print(f"[cache] loaded {CACHE_PATH}")
        # Compose dict keyed by stat
        out = {}
        for stat in STATS:
            out[stat] = {
                "yp": data[f"{stat}__yp"],
                "y_te": data[f"{stat}__y_te"],
                "sigma": float(data[f"{stat}__sigma"]),
            }
        return out

    print("[cache] building residual cache (one-time; ~8-10 min)")
    client = pymongo.MongoClient(os.environ["MONGO_URL"])
    adv_map = preload_advanced_stats()
    out = {}
    savable: Dict[str, Any] = {}
    for label, field in STATS.items():
        t0 = time.monotonic()
        path = os.path.join(MODEL_DIR, f"vk2_{label.lower()}.pkl")
        with open(path, "rb") as f:
            payload = pickle.load(f)
        sigma = float(payload["residual_sigma_empirical"])
        X, y, sw, feature_cols = build_training_matrix(
            label, field, adv_map=adv_map,
            target_schema=set(PRUNED_FEATURES),
        )
        test_mask = sw >= 0.99
        X_te, y_te = X[test_mask], y[test_mask]
        want = payload["features"]
        idx = [feature_cols.index(f) for f in want]
        Xs = payload["scaler"].transform(X_te[:, idx])
        yp = payload["model"].predict(Xs)
        yp = np.asarray(yp, dtype=np.float64)
        y_te = np.asarray(y_te, dtype=np.float64)
        out[label] = {"yp": yp, "y_te": y_te, "sigma": sigma}
        savable[f"{label}__yp"] = yp
        savable[f"{label}__y_te"] = y_te
        savable[f"{label}__sigma"] = sigma
        print(f"  [{label}] n={len(yp):,} σ={sigma:.3f} ({time.monotonic()-t0:.1f}s)")
    np.savez(CACHE_PATH, **savable)
    client.close()
    return out


# -----------------------------------------------------------------------
# Phase 2 — residual diagnostics
# -----------------------------------------------------------------------
def diagnose(res):
    """res = y_true - y_pred"""
    arr = np.asarray(res, dtype=np.float64)
    mu = float(arr.mean())
    sd = float(arr.std(ddof=1))
    skew = float(sstats.skew(arr))
    kurt = float(sstats.kurtosis(arr))  # Fisher (excess)
    p5, p95 = np.percentile(arr, [5, 95])
    tail_rate = float(np.mean(np.abs(arr - mu) > 2 * sd))
    return {
        "mean": round(mu, 4),
        "std": round(sd, 4),
        "skew": round(skew, 4),
        "excess_kurtosis": round(kurt, 4),
        "p5": round(float(p5), 4),
        "p95": round(float(p95), 4),
        "tail_outside_2sigma_pct": round(100.0 * tail_rate, 3),
        "gaussian_expected_tail_pct": 4.55,
    }


# -----------------------------------------------------------------------
# Phase 3 — probability methods
# -----------------------------------------------------------------------
def p_over_gaussian(yp, lines, sigma):
    """Method A — current production. Returns dict {line: per-sample p_over array}."""
    return {line: 1.0 - norm.cdf(line, loc=yp, scale=sigma) for line in lines}


def load_global_isotonic(stat):
    path = os.path.join(MODEL_DIR, f"prob_calibrator_{stat.lower()}.pkl")
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return pickle.load(f).get("calibrator")


def p_over_isotonic(base_map, iso):
    """Method B/C — apply an isotonic calibrator to a raw-prob array map."""
    out = {}
    for line, raw in base_map.items():
        out[line] = iso.transform(raw)
    return out


def fit_bucketed_isotonic(yp, y_te, sigma, lines, n_buckets=3):
    """Method C — split samples by projection magnitude into n_buckets
    quantile-based bins; fit one isotonic per bin on pooled (raw_p, actual)
    pairs across all lines. Apply to each sample using its own bin."""
    # Bin samples by projection
    edges = np.quantile(yp, np.linspace(0, 1, n_buckets + 1))
    edges[0], edges[-1] = -np.inf, np.inf
    bins = np.digitize(yp, edges[1:-1])
    calibrators = {}
    for b in range(n_buckets):
        mask = bins == b
        if mask.sum() < 200:
            continue
        raw_probs, acts = [], []
        for line in lines:
            raw = 1.0 - norm.cdf(line, loc=yp[mask], scale=sigma)
            act = (y_te[mask] > line).astype(float)
            raw_probs.append(raw); acts.append(act)
        rp = np.concatenate(raw_probs); at = np.concatenate(acts)
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        iso.fit(rp, at)
        calibrators[b] = iso
    return calibrators, bins, edges


def p_over_bucketed_isotonic(yp, lines, sigma, calibrators, bins):
    out = {}
    for line in lines:
        raw = 1.0 - norm.cdf(line, loc=yp, scale=sigma)
        res = np.empty_like(raw)
        for b, iso in calibrators.items():
            mask = bins == b
            if mask.any():
                res[mask] = iso.transform(raw[mask])
        # Samples not in any calibrated bin: keep raw
        covered = np.zeros_like(raw, dtype=bool)
        for b in calibrators.keys():
            covered |= (bins == b)
        res[~covered] = raw[~covered]
        out[line] = res
    return out


def p_over_empirical_cdf(yp, y_te, lines, n_buckets=10):
    """Method D — non-parametric. For each sample, estimate P(y > line)
    using empirical residuals from the same projection-magnitude bucket.
    residual = y_te - yp inside bucket. P(over|line, yp) = P(y > line)
    = P(yp + residual > line) = P(residual > line - yp), with the
    residual distribution drawn from the bucket."""
    edges = np.quantile(yp, np.linspace(0, 1, n_buckets + 1))
    edges[0], edges[-1] = -np.inf, np.inf
    bins = np.digitize(yp, edges[1:-1])
    # Pre-sort residuals per bucket for fast CDF lookup
    bucket_residuals = {}
    for b in range(n_buckets):
        mask = bins == b
        if mask.sum() >= 200:
            bucket_residuals[b] = np.sort(y_te[mask] - yp[mask])
    out = {}
    for line in lines:
        needed = line - yp  # P(residual > needed)
        res = np.empty_like(needed)
        for i in range(len(yp)):
            b = bins[i]
            r = bucket_residuals.get(b)
            if r is None or len(r) == 0:
                # fallback: gaussian with bucket-specific std if we had one;
                # otherwise leave as 0.5 (uninformed)
                res[i] = 0.5
                continue
            # P(residual > needed[i]) = 1 - empirical_cdf(needed[i])
            idx = np.searchsorted(r, needed[i], side="right")
            res[i] = 1.0 - float(idx) / len(r)
        out[line] = np.clip(res, 0.0, 1.0)
    return out


def p_over_lognormal(yp, lines, residuals):
    """Method E — fit a lognormal to y_te (not residual) per stat.
    P(y > line) where y ~ LogNormal(s, scale) fit from the training-set
    y_te distribution. Doesn't use yp at all at inference — that's the
    trade-off; we use it as a shape-only reference."""
    y = residuals["y_te"]
    yp_local = residuals["yp"]
    # Shift so we can fit lognormal — y values in NBA are non-negative.
    y_pos = y[y > 0]
    if len(y_pos) < 100:
        return {line: np.full_like(yp_local, 0.5) for line in lines}
    shape, loc, scale = lognorm.fit(y_pos, floc=0)
    # For each sample, shift the lognormal to center at yp rather than
    # global mean. We do this by solving: new_scale such that mean
    # shifts to yp. Mean of lognormal(shape, 0, scale) = exp(log(scale)+shape^2/2)
    # If we want mean = yp, new_scale = yp / exp(shape^2/2)
    factor = math.exp(shape * shape / 2.0)
    out = {}
    for line in lines:
        # per-sample scale = yp / factor (clip to a small floor)
        scale_samples = np.maximum(yp_local / factor, 1e-3)
        # P(y > line) = 1 - CDF(line)
        res = 1.0 - lognorm.cdf(line, shape, loc=0, scale=scale_samples)
        out[line] = np.clip(res, 0.0, 1.0)
    return out


def p_over_skewnormal(yp, lines, residuals):
    """Method G — fit a skew-normal to residuals then apply P(y>line).
    We fit one (a, loc, sc) globally, then shift loc by yp at inference."""
    r = residuals["y_te"] - residuals["yp"]
    a, loc, sc = skewnorm.fit(r)
    out = {}
    for line in lines:
        # P(y > line) = P(yp + ε > line) = P(ε > line - yp)
        #             = 1 - SN.cdf(line - yp | a, loc, sc)
        z = line - yp
        res = 1.0 - skewnorm.cdf(z, a, loc=loc, scale=sc)
        out[line] = np.clip(res, 0.0, 1.0)
    return out


def p_over_poisson(yp, lines):
    """Method F — 3PM only. P(Poisson(λ=yp) > line).
    PropPicks lines are half-integers so line=0.5 is actually P(Y>=1)."""
    out = {}
    yp_clip = np.maximum(yp, 1e-3)
    for line in lines:
        # P(Y > line) where line is x.5 → P(Y >= ceil(line)) = 1 - cdf(floor(line))
        k = int(math.floor(line))
        res = 1.0 - poisson.cdf(k, mu=yp_clip)
        out[line] = np.clip(res, 0.0, 1.0)
    return out


def p_over_negbinom(yp, y_te, lines):
    """Method F variant — negative binomial with global dispersion fit
    from (mean, var) moment matching per stat."""
    mean = y_te.mean(); var = y_te.var(ddof=1)
    if var <= mean + 1e-3:
        # Underdispersed — NB degenerates to Poisson.
        return p_over_poisson(yp, lines)
    # method-of-moments: p = mean/var; n = mean^2 / (var - mean)
    p = mean / var
    # Per-sample scaling: n_i = yp_i * p / (1 - p)
    n_i = np.maximum(yp * p / max(1.0 - p, 1e-6), 1e-3)
    out = {}
    for line in lines:
        k = int(math.floor(line))
        res = 1.0 - nbinom.cdf(k, n_i, p)
        out[line] = np.clip(res, 0.0, 1.0)
    return out


# -----------------------------------------------------------------------
# Phase 4 — evaluation
# -----------------------------------------------------------------------
def eval_method(yp, y_te, probs_by_line, lines, name):
    """Returns {line: {n, pred_mean, actual_over, gap}} + headline metrics."""
    rows = []
    total_weighted_gap = 0.0
    brier = 0.0
    brier_n = 0
    for line in lines:
        probs = probs_by_line.get(line)
        if probs is None:
            continue
        tol = max(2.0, 0.25 * max(1.0, line))
        near = np.abs(yp - line) < tol
        if near.sum() < 100:
            continue
        pred = probs[near]
        actual = (y_te[near] > line).astype(float)
        row = {
            "line": float(line),
            "n": int(near.sum()),
            "pred_mean": float(pred.mean()),
            "actual_over_rate": float(actual.mean()),
            "gap": float(pred.mean() - actual.mean()),
        }
        rows.append(row)
        total_weighted_gap += row["n"] * abs(row["gap"])
        # Per-sample Brier
        brier += float(np.sum((pred - actual) ** 2))
        brier_n += int(near.sum())
    return {
        "method": name,
        "lines": rows,
        "weighted_abs_gap": round(total_weighted_gap, 2),
        "brier": round(brier / max(brier_n, 1), 5) if brier_n else None,
    }


def _edge_stability_vs_gaussian(method_probs, gauss_probs):
    """How does the output vary from the Gaussian baseline? We report
    the std dev of (new − gaussian) across lines × samples. Low std ⇒
    calibrated output moves in a consistent direction relative to the
    baseline — stable edge predictions."""
    deltas = []
    for line, p_new in method_probs.items():
        p_old = gauss_probs[line]
        deltas.append(p_new - p_old)
    arr = np.concatenate(deltas) if deltas else np.array([0.0])
    return {
        "mean_delta_p": round(float(arr.mean()), 5),
        "std_delta_p": round(float(arr.std(ddof=1)), 5),
        "p5_delta_p": round(float(np.percentile(arr, 5)), 5),
        "p95_delta_p": round(float(np.percentile(arr, 95)), 5),
    }


# -----------------------------------------------------------------------
# Main pipeline
# -----------------------------------------------------------------------
def main():
    residuals_per_stat = build_or_load_cache()
    results_per_stat = {}
    for stat in STATS:
        r = residuals_per_stat[stat]
        yp, y_te, sigma = r["yp"], r["y_te"], r["sigma"]
        lines = SYMBOLIC_LINES[stat]
        print(f"\n=== {stat} (n={len(yp):,}, σ={sigma:.3f}) ===")

        residual_diag = diagnose(y_te - yp)

        method_probs = {}
        method_evals = {}

        # A — Gaussian
        method_probs["A_gaussian_raw"] = p_over_gaussian(yp, lines, sigma)
        method_evals["A_gaussian_raw"] = eval_method(
            yp, y_te, method_probs["A_gaussian_raw"], lines, "A_gaussian_raw",
        )

        # B — global isotonic
        iso_global = load_global_isotonic(stat)
        if iso_global is not None:
            method_probs["B_isotonic_global"] = p_over_isotonic(
                method_probs["A_gaussian_raw"], iso_global,
            )
            method_evals["B_isotonic_global"] = eval_method(
                yp, y_te, method_probs["B_isotonic_global"], lines, "B_isotonic_global",
            )

        # C — bucketed isotonic (3 buckets)
        calibrators, bins, edges = fit_bucketed_isotonic(
            yp, y_te, sigma, lines, n_buckets=3,
        )
        method_probs["C_isotonic_bucketed"] = p_over_bucketed_isotonic(
            yp, lines, sigma, calibrators, bins,
        )
        method_evals["C_isotonic_bucketed"] = eval_method(
            yp, y_te, method_probs["C_isotonic_bucketed"], lines, "C_isotonic_bucketed",
        )

        # D — empirical CDF
        method_probs["D_empirical_cdf"] = p_over_empirical_cdf(
            yp, y_te, lines, n_buckets=10,
        )
        method_evals["D_empirical_cdf"] = eval_method(
            yp, y_te, method_probs["D_empirical_cdf"], lines, "D_empirical_cdf",
        )

        # E — lognormal (long-tailed stats only: PTS, PRA)
        if stat in ("PTS", "PRA"):
            method_probs["E_lognormal"] = p_over_lognormal(
                yp, lines, {"yp": yp, "y_te": y_te},
            )
            method_evals["E_lognormal"] = eval_method(
                yp, y_te, method_probs["E_lognormal"], lines, "E_lognormal",
            )

        # F — Poisson / Negative-Binomial for count-like stats (3PM, REB, AST)
        if stat in ("3PM", "REB", "AST"):
            method_probs["F_poisson_nb"] = p_over_negbinom(yp, y_te, lines)
            method_evals["F_poisson_nb"] = eval_method(
                yp, y_te, method_probs["F_poisson_nb"], lines, "F_poisson_nb",
            )

        # G — skew-normal (PTS, PRA)
        if stat in ("PTS", "PRA"):
            method_probs["G_skewnormal"] = p_over_skewnormal(
                yp, lines, {"yp": yp, "y_te": y_te},
            )
            method_evals["G_skewnormal"] = eval_method(
                yp, y_te, method_probs["G_skewnormal"], lines, "G_skewnormal",
            )

        # Edge stability vs Gaussian
        stability = {}
        for name, probs in method_probs.items():
            if name == "A_gaussian_raw":
                continue
            stability[name] = _edge_stability_vs_gaussian(
                probs, method_probs["A_gaussian_raw"],
            )

        # Pick winner = lowest weighted_abs_gap
        best = min(method_evals.values(), key=lambda e: e["weighted_abs_gap"])

        results_per_stat[stat] = {
            "n_test": int(len(yp)),
            "sigma": round(sigma, 3),
            "residual_diagnostics": residual_diag,
            "methods": method_evals,
            "edge_stability_vs_gaussian": stability,
            "winner": best["method"],
            "winner_weighted_gap": best["weighted_abs_gap"],
        }
        print(f"  winner: {best['method']} (weighted |gap| = {best['weighted_abs_gap']})")

    # Final verdict + recommendation
    recommendations = []
    for stat, r in results_per_stat.items():
        gauss = r["methods"]["A_gaussian_raw"]["weighted_abs_gap"]
        best_name = r["winner"]
        best_gap = r["winner_weighted_gap"]
        improvement = 100.0 * (gauss - best_gap) / max(gauss, 1e-9)
        verdict = "KEEP gaussian" if best_name == "A_gaussian_raw" else f"SWITCH to {best_name}"
        # Only recommend switch if improvement >= 10% (material)
        if best_name != "A_gaussian_raw" and improvement < 10.0:
            verdict = f"marginal ({improvement:+.1f}%) — KEEP gaussian"
        recommendations.append({
            "stat": stat,
            "baseline_gauss_gap": gauss,
            "best_method": best_name,
            "best_gap": best_gap,
            "improvement_vs_gauss_pct": round(improvement, 2),
            "verdict": verdict,
        })

    # Render
    lines_md = [
        "# VK2 Distribution-Aware Probability Audit",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}",
        "",
        "Goal: identify whether Gaussian-CDF P(over) is structurally "
        "wrong per stat, and if so, which distribution/family performs "
        "better on the 2024 held-out split. No retraining, no "
        "projection changes, no feature changes.",
        "",
        "## Residual distribution diagnostics",
        "",
        "| Stat | mean | std | skew | excess kurt | p5 | p95 | tail>2σ | vs 4.55% Gaussian |",
        "|------|------|-----|------|-------------|----|-----|---------|-------------------|",
    ]
    for stat, r in results_per_stat.items():
        d = r["residual_diagnostics"]
        lines_md.append(
            f"| {stat} | {d['mean']:+.4f} | {d['std']:.4f} | "
            f"{d['skew']:+.4f} | {d['excess_kurtosis']:+.4f} | "
            f"{d['p5']:.3f} | {d['p95']:.3f} | {d['tail_outside_2sigma_pct']}% "
            f"| {'YES' if abs(d['tail_outside_2sigma_pct'] - 4.55) > 1.0 else 'close'} |"
        )
    lines_md.append("")

    lines_md.append("## Headline weighted |gap| per method per stat")
    lines_md.append("")
    method_cols = ["A_gaussian_raw", "B_isotonic_global", "C_isotonic_bucketed",
                   "D_empirical_cdf", "E_lognormal", "F_poisson_nb", "G_skewnormal"]
    lines_md.append("| Stat | A Gauss | B Iso-G | C Iso-B | D ECDF | E LogN | F NB | G Skew |")
    lines_md.append("|------|---------|---------|---------|--------|--------|------|--------|")
    for stat, r in results_per_stat.items():
        row = [stat]
        for m in method_cols:
            ev = r["methods"].get(m)
            row.append(f"{ev['weighted_abs_gap']:.1f}" if ev else "—")
        lines_md.append("| " + " | ".join(row) + " |")
    lines_md.append("")

    lines_md.append("## Brier score per method per stat (lower is better)")
    lines_md.append("")
    lines_md.append("| Stat | A Gauss | B Iso-G | C Iso-B | D ECDF | E LogN | F NB | G Skew |")
    lines_md.append("|------|---------|---------|---------|--------|--------|------|--------|")
    for stat, r in results_per_stat.items():
        row = [stat]
        for m in method_cols:
            ev = r["methods"].get(m)
            row.append(f"{ev['brier']:.4f}" if ev else "—")
        lines_md.append("| " + " | ".join(row) + " |")
    lines_md.append("")

    lines_md.append("## Edge stability (std of Δp vs Gaussian baseline)")
    lines_md.append("")
    lines_md.append("| Stat | Method | mean Δp | std Δp | p5 Δp | p95 Δp |")
    lines_md.append("|------|--------|---------|--------|-------|--------|")
    for stat, r in results_per_stat.items():
        for name, s in r["edge_stability_vs_gaussian"].items():
            lines_md.append(
                f"| {stat} | {name} | {s['mean_delta_p']:+.4f} | "
                f"{s['std_delta_p']:.4f} | {s['p5_delta_p']:+.4f} | "
                f"{s['p95_delta_p']:+.4f} |"
            )
    lines_md.append("")

    lines_md.append("## KEEP / REJECT + recommended production method")
    lines_md.append("")
    lines_md.append("| Stat | Gauss gap | Best method | Best gap | improvement | Verdict |")
    lines_md.append("|------|-----------|-------------|----------|-------------|---------|")
    for rec in recommendations:
        lines_md.append(
            f"| {rec['stat']} | {rec['baseline_gauss_gap']:.1f} | "
            f"{rec['best_method']} | {rec['best_gap']:.1f} | "
            f"{rec['improvement_vs_gauss_pct']:+.1f}% | {rec['verdict']} |"
        )
    lines_md.append("")

    lines_md.append("## Per-stat line tables (best-method vs gaussian)")
    lines_md.append("")
    for stat, r in results_per_stat.items():
        winner = r["winner"]
        lines_md.append(f"### {stat}  (winner: **{winner}**)")
        lines_md.append("")
        lines_md.append(
            "| line | n | gauss P(over) | best P(over) | actual | Δ gauss | Δ best |"
        )
        lines_md.append(
            "|------|---|---------------|--------------|--------|---------|--------|"
        )
        g_rows = {row["line"]: row for row in r["methods"]["A_gaussian_raw"]["lines"]}
        w_rows = {row["line"]: row for row in r["methods"][winner]["lines"]}
        for line in SYMBOLIC_LINES[stat]:
            g = g_rows.get(line); w = w_rows.get(line)
            if g is None or w is None: continue
            lines_md.append(
                f"| {line} | {g['n']} | {g['pred_mean']:.3f} | "
                f"{w['pred_mean']:.3f} | {g['actual_over_rate']:.3f} | "
                f"{g['gap']:+.3f} | {w['gap']:+.3f} |"
            )
        lines_md.append("")

    os.makedirs(os.path.dirname(REPORT_MD), exist_ok=True)
    with open(REPORT_MD, "w") as f:
        f.write("\n".join(lines_md))
    with open(REPORT_JSON, "w") as f:
        json.dump({"results": results_per_stat,
                   "recommendations": recommendations}, f, indent=2, default=float)
    print(f"\n→ {REPORT_MD}")
    print(f"→ {REPORT_JSON}")


if __name__ == "__main__":
    main()

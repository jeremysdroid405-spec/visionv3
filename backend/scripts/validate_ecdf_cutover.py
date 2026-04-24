"""
ECDF Probability Cutover — Validation Report (2026-04-24)

Simulates the full fallback chain on the cached 2024 held-out residuals
(`reports/_residual_cache.npz`) — the same data the distribution audit
used — so we can measure the exact impact of the switch to ECDF WITHOUT
waiting for a live scoring pass to populate the counters.

Does NOT touch production scoring, DB, or pkls.

Sections produced in `reports/vk2_ecdf_cutover_validation.md`:
  1. Per-stat before/after calibration table (raw Gaussian → ECDF).
  2. Focal lines requested by spec: 3PM 0.5, AST 1.5, REB 2.5,
     PTS 24.5+, PRA 40+.
  3. Edge-movement summary (direction + magnitude of Δp).
  4. Gate pass/fail movement at a representative ATS-style threshold.
  5. Top-20 largest p_over corrections (row-level).
"""
from __future__ import annotations

import json
import os
import pickle
import sys
import time

import numpy as np
from scipy.stats import norm

sys.path.insert(0, "/app/backend")
os.chdir("/app/backend")

from dotenv import load_dotenv
load_dotenv()

CACHE = "/app/backend/reports/_residual_cache.npz"
ECDF_DIR = "/app/backend/models"
REPORT_MD = "/app/backend/reports/vk2_ecdf_cutover_validation.md"

STATS = ["PTS", "REB", "AST", "3PM", "PRA"]
INTERCEPT = {"PTS": -0.094, "PRA": -0.103, "REB": 0.0, "AST": 0.0, "3PM": 0.0}
FOCAL_LINES = {
    "PTS": [4.5, 6.5, 24.5, 29.5],
    "REB": [2.5, 3.5, 9.5, 11.5],
    "AST": [1.5, 2.5, 8.5, 10.5],
    "3PM": [0.5, 1.5, 3.5, 4.5],
    "PRA": [15.5, 22.5, 40.5, 45.5, 55.5],
}
LINE_GRID = {
    "PTS": [4.5, 6.5, 9.5, 12.5, 15.5, 19.5, 24.5, 29.5],
    "REB": [2.5, 3.5, 4.5, 5.5, 7.5, 9.5, 11.5],
    "AST": [1.5, 2.5, 3.5, 4.5, 6.5, 8.5, 10.5],
    "3PM": [0.5, 1.5, 2.5, 3.5, 4.5],
    "PRA": [9.5, 15.5, 22.5, 29.5, 36.5, 45.5, 55.5],
}
# Representative "gate" threshold — scored docs typically require
# p_over above ~0.55 (OVER) or below ~0.45 (UNDER) to become a pick.
GATE_OVER = 0.55
GATE_UNDER = 0.45


def load_ecdf(stat):
    path = os.path.join(ECDF_DIR, f"prob_ecdf_{stat.lower()}.pkl")
    with open(path, "rb") as f:
        return pickle.load(f)


def ecdf_prob(art, proj_arr, line):
    edges_inner = art["bucket_edges"][1:-1]
    bins = np.digitize(proj_arr, edges_inner)
    needed = line - proj_arr
    out = np.empty_like(proj_arr, dtype=np.float64)
    for b, r in art["sorted_residuals_by_bucket"].items():
        m = bins == b
        if not m.any():
            continue
        n = len(r)
        if n < 20:
            # Fallback would kick in in prod; simulate with Gaussian
            sigma = float(art["source_sigma"])
            out[m] = 1.0 - norm.cdf(line, loc=proj_arr[m], scale=sigma)
            continue
        idx = np.searchsorted(r, needed[m], side="right")
        out[m] = 1.0 - idx / n
    return np.clip(out, 0.0, 1.0)


def eval_line(y_true, p_vals, near_mask):
    y = y_true[near_mask]
    p = p_vals[near_mask]
    if len(y) == 0:
        return None
    gap = float(p.mean() - (y > 0.5).astype(float).mean())  # placeholder
    return gap  # unused


def main():
    data = dict(np.load(CACHE, allow_pickle=True))
    md = [
        "# ECDF Probability Cutover — Validation (2026-04-24)",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}",
        "",
        "Simulates the full fallback chain — intercept shift (PTS/PRA), "
        "ECDF lookup per-stat, Gaussian fallback for low-n buckets — on "
        "the cached 2024 held-out residuals (same data the distribution "
        "audit used). NO production scoring or DB writes.",
        "",
    ]

    # Phase 1 — global before/after per stat
    md.append("## Section 1 — before/after per-stat (weighted |gap|)")
    md.append("")
    md.append("| Stat | Σ n·|gap| raw | Σ n·|gap| ECDF | improvement |")
    md.append("|------|----------------|-----------------|-------------|")
    global_stats = {}
    for stat in STATS:
        yp = np.asarray(data[f"{stat}__yp"], dtype=np.float64)
        y_te = np.asarray(data[f"{stat}__y_te"], dtype=np.float64)
        sigma = float(data[f"{stat}__sigma"])
        # Apply intercept shift (what scoring adapter does now)
        yp_shifted = np.maximum(yp + INTERCEPT[stat], 0.0)
        art = load_ecdf(stat)

        sum_raw_gap = 0.0
        sum_ecdf_gap = 0.0
        line_rows = []
        for line in LINE_GRID[stat]:
            raw = 1.0 - norm.cdf(line, loc=yp_shifted, scale=sigma)
            ecdf = ecdf_prob(art, yp_shifted, line)
            tol = max(2.0, 0.25 * max(1.0, line))
            near = np.abs(yp_shifted - line) < tol
            if near.sum() < 100:
                continue
            actual = (y_te[near] > line).astype(float).mean()
            r_mean = float(raw[near].mean())
            e_mean = float(ecdf[near].mean())
            sum_raw_gap += int(near.sum()) * abs(r_mean - actual)
            sum_ecdf_gap += int(near.sum()) * abs(e_mean - actual)
            line_rows.append({
                "line": line, "n": int(near.sum()),
                "raw_p_over": round(r_mean, 3),
                "ecdf_p_over": round(e_mean, 3),
                "actual_over_rate": round(actual, 3),
                "raw_gap": round(r_mean - actual, 3),
                "ecdf_gap": round(e_mean - actual, 3),
            })
        imp = 100.0 * (sum_raw_gap - sum_ecdf_gap) / max(sum_raw_gap, 1e-9)
        md.append(f"| {stat} | {sum_raw_gap:.1f} | {sum_ecdf_gap:.1f} | {imp:+.1f}% |")
        global_stats[stat] = {
            "lines": line_rows, "sum_raw_gap": sum_raw_gap,
            "sum_ecdf_gap": sum_ecdf_gap, "improvement_pct": imp,
            "yp_shifted": yp_shifted, "y_te": y_te,
            "sigma": sigma, "artifact": art,
        }
    md.append("")

    # Phase 2 — focal lines
    md.append("## Section 2 — Focal lines (request spec)")
    md.append("")
    md.append("| Stat | line | n | raw Gauss | ECDF | actual | Δ raw | Δ ECDF |")
    md.append("|------|------|---|-----------|------|--------|-------|--------|")
    for stat in STATS:
        for row in global_stats[stat]["lines"]:
            if row["line"] in FOCAL_LINES.get(stat, []):
                md.append(
                    f"| {stat} | {row['line']} | {row['n']} | "
                    f"{row['raw_p_over']} | {row['ecdf_p_over']} | "
                    f"{row['actual_over_rate']} | {row['raw_gap']:+} | "
                    f"{row['ecdf_gap']:+} |"
                )
    md.append("")

    # Phase 3 — edge movement summary
    md.append("## Section 3 — edge movement summary (Δp = ECDF − raw Gaussian)")
    md.append("")
    md.append("| Stat | samples | mean Δp | std Δp | p5 Δp | p95 Δp | %|Δp|>5pp |")
    md.append("|------|---------|---------|--------|-------|--------|------------|")
    for stat in STATS:
        g = global_stats[stat]
        # Aggregate Δp across all grid lines / all samples
        dps = []
        for line in LINE_GRID[stat]:
            raw = 1.0 - norm.cdf(line, loc=g["yp_shifted"], scale=g["sigma"])
            ec = ecdf_prob(g["artifact"], g["yp_shifted"], line)
            dps.append(ec - raw)
        arr = np.concatenate(dps)
        md.append(
            f"| {stat} | {len(arr):,} | {arr.mean():+.4f} | "
            f"{arr.std(ddof=1):.4f} | {np.percentile(arr, 5):+.4f} | "
            f"{np.percentile(arr, 95):+.4f} | "
            f"{100.0 * np.mean(np.abs(arr) > 0.05):.2f}% |"
        )
    md.append("")

    # Phase 4 — gate pass/fail movement (OVER gate only)
    md.append("## Section 4 — gate pass/fail movement "
              "(representative gate: p_over > 0.55 OVER / p_over < 0.45 UNDER)")
    md.append("")
    md.append("| Stat | samples | raw OVER gate | ECDF OVER gate | Δ OVER | "
              "raw UNDER gate | ECDF UNDER gate | Δ UNDER |")
    md.append("|------|---------|---------------|----------------|--------|"
              "----------------|-----------------|---------|")
    for stat in STATS:
        g = global_stats[stat]
        total_raw_over = 0; total_ecdf_over = 0
        total_raw_under = 0; total_ecdf_under = 0
        total_n = 0
        for line in LINE_GRID[stat]:
            raw = 1.0 - norm.cdf(line, loc=g["yp_shifted"], scale=g["sigma"])
            ec = ecdf_prob(g["artifact"], g["yp_shifted"], line)
            total_raw_over  += int(np.sum(raw > GATE_OVER))
            total_ecdf_over += int(np.sum(ec  > GATE_OVER))
            total_raw_under  += int(np.sum(raw < GATE_UNDER))
            total_ecdf_under += int(np.sum(ec  < GATE_UNDER))
            total_n += len(raw)
        md.append(
            f"| {stat} | {total_n:,} | {total_raw_over:,} | "
            f"{total_ecdf_over:,} | {total_ecdf_over - total_raw_over:+,} | "
            f"{total_raw_under:,} | {total_ecdf_under:,} | "
            f"{total_ecdf_under - total_raw_under:+,} |"
        )
    md.append("")

    # Phase 5 — top-20 largest p_over corrections (global, across stats)
    md.append("## Section 5 — Top-20 largest p_over corrections (absolute Δp)")
    md.append("")
    md.append("| Stat | line | sample idx | raw Gauss | ECDF | Δp |")
    md.append("|------|------|-----------:|----------:|-----:|----:|")
    candidates = []
    for stat in STATS:
        g = global_stats[stat]
        yp_s = g["yp_shifted"]; sigma = g["sigma"]; art = g["artifact"]
        # Sample 2000 indices per stat to keep the pool manageable
        idxs = np.random.default_rng(0).choice(
            len(yp_s), size=min(2000, len(yp_s)), replace=False,
        )
        for line in LINE_GRID[stat]:
            raw = 1.0 - norm.cdf(line, loc=yp_s[idxs], scale=sigma)
            ec = ecdf_prob(art, yp_s[idxs], line)
            for i, (r, e) in enumerate(zip(raw, ec)):
                candidates.append({
                    "stat": stat, "line": line, "idx": int(idxs[i]),
                    "raw": float(r), "ecdf": float(e),
                    "delta": float(e - r),
                })
    candidates.sort(key=lambda c: -abs(c["delta"]))
    for c in candidates[:20]:
        md.append(
            f"| {c['stat']} | {c['line']} | {c['idx']} | "
            f"{c['raw']:.3f} | {c['ecdf']:.3f} | {c['delta']:+.3f} |"
        )
    md.append("")

    os.makedirs(os.path.dirname(REPORT_MD), exist_ok=True)
    with open(REPORT_MD, "w") as f:
        f.write("\n".join(md))
    print(f"→ {REPORT_MD}")


if __name__ == "__main__":
    main()

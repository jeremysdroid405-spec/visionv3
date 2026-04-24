"""Validate the MLB ECDF cutover on .5-line priority cases.

Replays (projection, actual) pairs through the ECDF service and
compares against the Gaussian baseline (normal CDF using residual
std as sigma — same assumption the hf model used before).

Writes `/app/backend/reports/mlb_ecdf_cutover_validation.md`.
"""
from __future__ import annotations

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

from services.probability.ecdf import UniversalECDFProbability

ART_ROOT = "/app/backend/models/probability/ecdf/mlb"
REPORT = "/app/backend/reports/mlb_ecdf_cutover_validation.md"

FOCAL = {
    # (stat_family, [lines])
    "hits": [0.5, 1.5, 2.5],
    "total_bases": [0.5, 1.5, 2.5, 3.5],
    "strikeouts": [0.5, 1.5, 2.5],
    "pitcher_strikeouts": [4.5, 5.5, 6.5, 7.5],
    "home_runs": [0.5],
    "rbis": [0.5, 1.5],
    "runs": [0.5, 1.5],
    "walks": [0.5],
    "singles": [0.5, 1.5],
    "hits_allowed": [4.5, 5.5, 6.5],
}

# Gate thresholds (representative — same values as the NBA audit).
GATE_OVER = 0.55
GATE_UNDER = 0.45


def _load_residuals(fam: str):
    path = os.path.join(ART_ROOT, f"{fam}.pkl")
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        art = pickle.load(f)
    # Reconstruct raw (proj, actual) from the artifact bucket data.
    # We don't have the full (proj, actual) file, only residuals per
    # bucket + bucket edges. Gaussian baseline needs σ which we can
    # compute from pooled residuals.
    all_res = np.concatenate([
        np.asarray(r) for r in art["sorted_residuals_by_bucket"].values()
    ])
    sigma = float(all_res.std(ddof=1))
    return art, sigma, all_res


def main():
    md = [
        "# MLB ECDF Cutover — Validation Report",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}",
        "",
        "Compares the universal ECDF output against the residual-σ "
        "Gaussian baseline (what the hf model used before), focusing "
        "on .5 lines and other user-priority low/high thresholds.",
        "",
        "Methodology note: since the in-memory validation rebuilds "
        "(projection, actual) pairs from the ECDF artifact's per-"
        "bucket residuals, the 'actual' column below is reconstructed "
        "by sampling proj + residual for each residual in the bucket.",
        "",
    ]
    uni = UniversalECDFProbability()

    for fam in FOCAL.keys():
        art_info = _load_residuals(fam)
        if art_info is None:
            md.append(f"## {fam} — (artifact missing)\n")
            continue
        art, sigma, all_res = art_info
        md.append(f"## {fam}")
        md.append("")
        md.append(f"- samples = {art['sample_count']:,}  residual σ = {sigma:.3f}")
        md.append(f"- buckets = {art['n_buckets']}  min_bucket_n = {art['min_bucket_n']:,}")
        md.append("")
        md.append("| line | proj (bucket mid) | Gaussian P(over) | ECDF P(over) | empirical over-rate | Δ Gauss | Δ ECDF |")
        md.append("|------|-------------------|------------------|--------------|---------------------|---------|--------|")
        # Iterate over each bucket — pair it with each focal line.
        for line in FOCAL[fam]:
            for b, residuals in sorted(art["sorted_residuals_by_bucket"].items()):
                if len(residuals) < 50:
                    continue
                # Use bucket's median projection as the representative.
                # We don't store projections post-fit; derive proj from
                # edges: take the midpoint between inner edges. Edge
                # array has ±inf at ends, so pick concrete neighbors
                # per bucket.
                edges = art["projection_bucket_edges"]
                lo = edges[b] if np.isfinite(edges[b]) else edges[b + 1] - 1.0
                hi = edges[b + 1] if np.isfinite(edges[b + 1]) else edges[b] + 1.0
                proj = float(0.5 * (lo + hi))
                # Gaussian
                gauss_p = float(1.0 - norm.cdf(line, loc=proj, scale=sigma))
                # ECDF via the service
                pred = uni.predict_over_probability("mlb", fam, proj, line)
                ecdf_p = pred.p_over if pred is not None else None
                # Empirical over-rate = P(residual > line - proj)
                needed = line - proj
                emp = float(np.mean(residuals > needed))
                delta_g = gauss_p - emp
                delta_e = (ecdf_p - emp) if ecdf_p is not None else None
                ecdf_p_str = f"{ecdf_p:.3f}" if ecdf_p is not None else "None"
                md.append(
                    f"| {line} | {proj:.2f} (b={b}) | {gauss_p:.3f} | "
                    f"{ecdf_p_str} | {emp:.3f} | "
                    f"{delta_g:+.3f} | "
                    f"{(delta_e if delta_e is not None else 0):+.3f} |"
                )
        md.append("")

    # Edge & gate movement summary
    md.append("## Edge movement and gate pass/fail (across all .5 lines, all buckets)")
    md.append("")
    md.append(
        "| stat | Σ samples | mean Δp (ECDF−Gauss) | std Δp | "
        "Gauss OVER-gates | ECDF OVER-gates | Δ OVER | "
        "Gauss UNDER-gates | ECDF UNDER-gates | Δ UNDER |"
    )
    md.append("|------|-----------|----------------------|--------|"
              "------------------|-----------------|--------|"
              "-------------------|-------------------|---------|")
    for fam in FOCAL.keys():
        art_info = _load_residuals(fam)
        if art_info is None:
            continue
        art, sigma, _ = art_info
        deltas = []
        g_over = e_over = g_under = e_under = total_n = 0
        for line in FOCAL[fam]:
            for b, residuals in sorted(art["sorted_residuals_by_bucket"].items()):
                if len(residuals) < 50: continue
                edges = art["projection_bucket_edges"]
                lo = edges[b] if np.isfinite(edges[b]) else edges[b + 1] - 1.0
                hi = edges[b + 1] if np.isfinite(edges[b + 1]) else edges[b] + 1.0
                proj = float(0.5 * (lo + hi))
                gauss_p = float(1.0 - norm.cdf(line, loc=proj, scale=sigma))
                pred = uni.predict_over_probability("mlb", fam, proj, line)
                if pred is None:
                    continue
                ecdf_p = pred.p_over
                deltas.append(ecdf_p - gauss_p)
                # Count as "triggered" when p > / < gate. Samples are
                # bucket populations at this (line, bucket).
                n = len(residuals)
                total_n += n
                if gauss_p > GATE_OVER:   g_over  += n
                if ecdf_p  > GATE_OVER:   e_over  += n
                if gauss_p < GATE_UNDER:  g_under += n
                if ecdf_p  < GATE_UNDER:  e_under += n
        if not deltas:
            continue
        arr = np.asarray(deltas)
        md.append(
            f"| {fam} | {total_n:,} | {arr.mean():+.4f} | {arr.std(ddof=1):.4f} | "
            f"{g_over:,} | {e_over:,} | {e_over - g_over:+,} | "
            f"{g_under:,} | {e_under:,} | {e_under - g_under:+,} |"
        )
    md.append("")

    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w") as f:
        f.write("\n".join(md))
    print(f"→ {REPORT}")


if __name__ == "__main__":
    main()

"""
Train per-stat Empirical-CDF probability calibrators (2026-04-23).

Reuses the 2024 held-out residual cache produced by
`vk2_distribution_audit.py` (`reports/_residual_cache.npz`). If the
cache is missing the script asks the caller to run that audit first.

Artifact format (one pkl per stat):
    /app/backend/models/prob_ecdf_{stat}.pkl

Payload:
    {
      "stat": "PTS",
      "version": "NBA_VK2_ECDF_v1",
      "trained_at": "...",
      "bucket_edges": np.ndarray of length N_BUCKETS+1 (first/last = ±inf),
      "sorted_residuals_by_bucket": {bucket_idx: np.ndarray (sorted, ascending)},
      "bucket_ns": {bucket_idx: int},
      "min_bucket_n": int,
      "n_buckets": int,
      "source_sigma": float,
      "training_rows": int,
    }

Serving contract (see services/scoring/calibration.py):
    bucket = np.digitize(projection, bucket_edges[1:-1])
    needed = line - projection
    r = sorted_residuals_by_bucket[bucket]
    p_over = 1 - np.searchsorted(r, needed, side="right") / len(r)
"""
from __future__ import annotations

import os
import pickle
import sys
import time
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, "/app/backend")
os.chdir("/app/backend")

from dotenv import load_dotenv
load_dotenv()

CACHE_PATH = "/app/backend/reports/_residual_cache.npz"
MODEL_DIR = "/app/backend/models"
N_BUCKETS = 10
STATS = ["PTS", "REB", "AST", "3PM", "PRA"]


def main():
    if not os.path.exists(CACHE_PATH):
        print(f"ERROR: residual cache missing at {CACHE_PATH}.")
        print("Run `python scripts/vk2_distribution_audit.py` first to build it.")
        sys.exit(1)
    data = dict(np.load(CACHE_PATH, allow_pickle=True))
    print(f"[cache] loaded {CACHE_PATH}")

    for stat in STATS:
        t0 = time.monotonic()
        yp = np.asarray(data[f"{stat}__yp"], dtype=np.float64)
        y_te = np.asarray(data[f"{stat}__y_te"], dtype=np.float64)
        sigma = float(data[f"{stat}__sigma"])
        residuals = y_te - yp

        # Build quantile-based projection buckets. Clamp edge extremes to
        # ±inf so inference is safe for out-of-range projections.
        quantiles = np.linspace(0.0, 1.0, N_BUCKETS + 1)
        edges = np.quantile(yp, quantiles)
        edges = edges.astype(np.float64)
        edges[0], edges[-1] = -np.inf, np.inf

        # Assign samples to buckets using the inner edges only.
        bins = np.digitize(yp, edges[1:-1])

        sorted_res = {}
        bucket_ns = {}
        for b in range(N_BUCKETS):
            mask = bins == b
            r = np.sort(residuals[mask])
            sorted_res[int(b)] = r
            bucket_ns[int(b)] = int(mask.sum())

        min_n = min(bucket_ns.values()) if bucket_ns else 0

        payload = {
            "stat": stat,
            "version": "NBA_VK2_ECDF_v1",
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "bucket_edges": edges,
            "sorted_residuals_by_bucket": sorted_res,
            "bucket_ns": bucket_ns,
            "min_bucket_n": min_n,
            "n_buckets": N_BUCKETS,
            "source_sigma": sigma,
            "training_rows": int(len(yp)),
        }
        out_path = os.path.join(MODEL_DIR, f"prob_ecdf_{stat.lower()}.pkl")
        with open(out_path, "wb") as f:
            pickle.dump(payload, f)
        print(
            f"  [{stat}] n={len(yp):,}  buckets={N_BUCKETS}  "
            f"min_bucket_n={min_n}  σ={sigma:.3f}  "
            f"({time.monotonic()-t0:.2f}s)  → {out_path}"
        )


if __name__ == "__main__":
    main()

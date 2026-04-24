"""
Migrate legacy NBA-only ECDF pkls to the universal layout.

Source (legacy):
    /app/backend/models/prob_ecdf_{stat}.pkl
Destination (universal):
    /app/backend/models/probability/ecdf/nba/{stat}.pkl

The legacy pkls are kept on disk as a fallback for the short window
between this migration and the next scoring-code deploy; the NBA
scoring adapter will read the universal layout first and fall back
to the legacy path only when the universal artifact is missing.

Also creates empty directories for future sports so the folder layout
is documented in the repo from day one:
    /app/backend/models/probability/ecdf/mlb/    (scaffold)
    /app/backend/models/probability/ecdf/nfl/    (scaffold)
"""
from __future__ import annotations

import os
import pickle
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/app/backend")

from services.probability.ecdf import (  # noqa: E402
    UniversalECDFProbability, VERSION,
)

LEGACY_DIR = "/app/backend/models"
UNIVERSAL_ROOT = "/app/backend/models/probability/ecdf"
NBA_STATS = ["pts", "reb", "ast", "3pm", "pra"]
SCAFFOLD_SPORTS = ["mlb", "nfl"]


def migrate_nba():
    ecdf = UniversalECDFProbability(root=UNIVERSAL_ROOT)
    for stat in NBA_STATS:
        legacy_path = os.path.join(LEGACY_DIR, f"prob_ecdf_{stat}.pkl")
        if not os.path.exists(legacy_path):
            print(f"  [SKIP] {stat}: legacy pkl missing at {legacy_path}")
            continue
        with open(legacy_path, "rb") as f:
            legacy = pickle.load(f)
        # Build universal artifact. Legacy schema used `bucket_edges`;
        # universal uses `projection_bucket_edges`.
        artifact = {
            "sport": "nba",
            "stat_family": stat,
            "version": VERSION,
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "source_model_version": (
                legacy.get("source_model_version")
                or legacy.get("version")
                or "NBA_VK_v2_5yr_weighted_pruned52"
            ),
            "projection_bucket_edges": legacy["bucket_edges"],
            "sorted_residuals_by_bucket": legacy["sorted_residuals_by_bucket"],
            "bucket_ns": legacy.get(
                "bucket_ns",
                {k: int(len(v)) for k, v in legacy["sorted_residuals_by_bucket"].items()},
            ),
            "sample_count": int(legacy.get("training_rows") or 0),
            "min_bucket_n": int(legacy.get("min_bucket_n") or min(
                (len(v) for v in legacy["sorted_residuals_by_bucket"].values()),
                default=0,
            )),
            "n_buckets": int(legacy.get("n_buckets") or len(
                legacy["sorted_residuals_by_bucket"]
            )),
        }
        path = ecdf.save("nba", stat, artifact)
        print(
            f"  [OK]   {stat}: n_buckets={artifact['n_buckets']}  "
            f"min_bucket_n={artifact['min_bucket_n']}  "
            f"samples={artifact['sample_count']}  → {path}"
        )


def scaffold_future_sports():
    for sport in SCAFFOLD_SPORTS:
        d = os.path.join(UNIVERSAL_ROOT, sport)
        os.makedirs(d, exist_ok=True)
        readme = os.path.join(d, "README.md")
        if not os.path.exists(readme):
            with open(readme, "w") as f:
                f.write(
                    f"# {sport.upper()} universal-ECDF artifacts\n\n"
                    f"Populate this directory by training via "
                    f"`services.probability.UniversalECDFProbability.fit("
                    f"sport='{sport}', stat_family='...', records=...)`.\n"
                )
            print(f"  [SCAFFOLD] {sport} → {d}/  (README.md written)")


def main():
    print("=== migrate NBA legacy ECDF pkls → universal layout ===")
    migrate_nba()
    print()
    print("=== scaffold future-sport directories ===")
    scaffold_future_sports()
    print()
    print("Done.")


if __name__ == "__main__":
    main()

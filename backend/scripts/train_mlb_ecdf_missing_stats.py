"""Train the 3 missing MLB ECDF artifacts (2026-04-24, completion pass).

Mirrors the pipeline of `scripts/train_mlb_ecdf_artifacts.py` but
restricts to the stat families that were not produced in the first
training run:

  - hits+runs+rbis   (composite batter stat)
  - doubles
  - stolen_bases

The original script listed `doubles` but did not produce an artifact
— likely insufficient pairs or stat-field plumbing issue for
composite/low-base stats. This script reuses the same HF model and
UniversalECDFProbability.fit() contract so the artifacts are
interchangeable with the first 10.
"""
from __future__ import annotations

import os
import sys
import time
from typing import Dict, List

import numpy as np

sys.path.insert(0, "/app/backend")
os.chdir("/app/backend")

from dotenv import load_dotenv
load_dotenv()

import pymongo

from services.mlb_high_friction_model import MLBHighFrictionModel
from services.probability.ecdf import UniversalECDFProbability

MISSING_STATS = ["hits+runs+rbis", "doubles", "stolen_bases"]
MIN_PAIRS_FOR_FIT = 500

# Some stat families (e.g. `doubles`) are not populated in the
# `mlb_historical_logs` collection but ARE populated in the
# `mlb_master_hub_2026.bdl_game_logs` embedded log. For those stats
# we walk the hub directly.
USE_HUB_LOGS = {"doubles"}


def _walk_source_logs(hf: MLBHighFrictionModel, stat_family: str):
    """Yield (player_master, game_logs_sorted) tuples from the source
    collection most likely to contain the stat. For `doubles` we pull
    from `mlb_master_hub_2026.bdl_game_logs`; everything else uses the
    legacy `mlb_historical_logs.game_logs`."""
    if stat_family in USE_HUB_LOGS:
        for hub_row in hf.master_hub.find(
            {"bdl_game_logs": {"$exists": True, "$ne": []}},
            {"_id": 0},
        ):
            logs = hub_row.get("bdl_game_logs") or []
            if len(logs) < 20:
                continue
            logs_sorted = sorted(
                logs, key=lambda x: x.get("date") or "1900-01-01",
                reverse=True,
            )
            yield hub_row, logs_sorted
    else:
        for player_doc in hf.historical_logs.find({}, {"_id": 0}):
            logs = player_doc.get("game_logs") or []
            if len(logs) < 20:
                continue
            player_name = player_doc.get("player_name")
            logs_sorted = sorted(
                logs, key=lambda x: x.get("date") or "1900-01-01",
                reverse=True,
            )
            player_master = hf.master_hub.find_one(
                {"$or": [{"display_name": player_name},
                         {"player_name": player_name}]},
                {"_id": 0},
            ) or {}
            yield player_master, logs_sorted


def regenerate_pairs(hf: MLBHighFrictionModel, stat_family: str) -> np.ndarray:
    norm = hf._normalize_stat(stat_family)
    if norm not in hf.models:
        print(f"  [{stat_family}] no HF model for canonical '{norm}' — skip")
        return np.empty((0, 2), dtype=np.float64)

    model = hf.models[norm]
    scaler = hf.scalers[norm]
    feature_cols = hf.feature_cols[norm]

    pairs: List[List[float]] = []
    skipped_no_feats = 0
    skipped_no_target = 0
    players_seen = 0

    for player_master, game_logs in _walk_source_logs(hf, stat_family):
        players_seen += 1
        for i in range(len(game_logs) - 20):
            target_game = game_logs[i]
            history = game_logs[i + 1:i + 31]
            target_value = hf._get_stat_value(target_game, norm)
            if target_value is None:
                skipped_no_target += 1
                continue
            opponent = target_game.get("opponent_abbr")
            feats = hf._build_friction_features(
                player_master, history, norm,
                opponent=opponent,
                park_team=None, dk_odds=None, line=None,
            )
            if feats is None:
                skipped_no_feats += 1
                continue
            x = np.asarray(
                [float(feats.get(c, 0.0) or 0.0) for c in feature_cols],
                dtype=np.float64,
            ).reshape(1, -1)
            try:
                x_s = scaler.transform(x)
                yp = float(model.predict(x_s)[0])
            except Exception:
                continue
            pairs.append([yp, float(target_value)])

    arr = (np.asarray(pairs, dtype=np.float64)
           if pairs else np.empty((0, 2), dtype=np.float64))
    print(
        f"  [{stat_family}→{norm}] pairs={len(arr):,}  "
        f"skip_no_feats={skipped_no_feats}  skip_no_target={skipped_no_target}  "
        f"players={players_seen:,}"
    )
    return arr


def main():
    t0 = time.monotonic()
    client = pymongo.MongoClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    hf = MLBHighFrictionModel(db)
    hf.load_models()
    ecdf = UniversalECDFProbability()

    summary: Dict[str, Dict] = {}
    for stat in MISSING_STATS:
        print(f"=== {stat} ===")
        pairs = regenerate_pairs(hf, stat)
        if len(pairs) < MIN_PAIRS_FOR_FIT:
            print(f"  [{stat}] INSUFFICIENT PAIRS ({len(pairs)}). Skipping fit.")
            summary[stat] = {"skipped": True, "pairs": int(len(pairs))}
            continue
        projs, acts = pairs[:, 0], pairs[:, 1]
        try:
            norm = hf._normalize_stat(stat)
            artifact = ecdf.fit(
                sport="mlb", stat_family=norm,
                records={"projection": projs, "actual": acts},
                source_model_version=f"MLB_HF_v1.0:{norm}",
            )
            summary[stat] = {
                "canonical": norm, "samples": artifact["sample_count"],
                "min_bucket_n": artifact["min_bucket_n"],
                "buckets": artifact["n_buckets"],
                "path": ecdf.artifact_path("mlb", norm),
            }
            print(
                f"  [{stat}→{norm}] FITTED  samples={artifact['sample_count']:,}  "
                f"min_bucket_n={artifact['min_bucket_n']:,}  "
                f"buckets={artifact['n_buckets']}"
            )
        except ValueError as e:
            print(f"  [{stat}] fit rejected: {e}")
            summary[stat] = {"error": str(e), "pairs": int(len(pairs))}

    print(f"\nDone in {time.monotonic() - t0:.1f}s")
    print("Summary:")
    for s, info in summary.items():
        print(f"  {s}: {info}")
    client.close()


if __name__ == "__main__":
    main()

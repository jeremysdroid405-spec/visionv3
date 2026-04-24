"""
Train per-stat Universal-ECDF artifacts for MLB (2026-04-24).

Pipeline (in-sample OOF is out of scope; we use in-sample predictions
since the hf models were trained on this same corpus and we don't have
a clean held-out corpus yet — same pragmatic trade-off we made for
the NBA first cut):

  1. For each requested stat_family (canonical, post-_normalize_stat):
     - Load the trained MLBHighFrictionModel pkl.
     - Re-walk `mlb_historical_logs`, regenerate the feature vector
       per (player, target_game) exactly the way
       `MLBHighFrictionModel.build_training_dataset` does, predict,
       and capture `(projection, actual)` pairs.
     - Feed the pairs into UniversalECDFProbability.fit() with
       sport='mlb', stat_family=<canonical>.
  2. Writes artifacts to /app/backend/models/probability/ecdf/mlb/.

Stat families targeted (priority): hits, total_bases, strikeouts,
pitcher_strikeouts (aliased as pitcher_outs in markets), runs, rbis,
home_runs, hits_allowed, walks, doubles, singles.

No production code is touched; this is strictly an offline trainer.
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

# User-priority subset — each mapped to canonical stat_family used
# both by the hf model on disk and by the universal ECDF service.
# NB: "pitcher_outs" aliases to "pitcher_strikeouts" per the hf
# model's own _normalize_stat; we store under the canonical key and
# document the alias so the MLB adapter's lookup finds the artifact.
STAT_FAMILIES = [
    "hits",
    "total_bases",
    "strikeouts",          # batter strikeouts (canonical "Batter Strikeouts")
    "pitcher_strikeouts",  # canonical for "Pitcher Strikeouts" / "Pitcher Outs"
    "home_runs",
    "rbis",
    "runs",
    "doubles",
    "walks",                # batter walks
    "hits_allowed",
    "singles",
]

# Minimum training pairs required before we attempt to fit an ECDF
# artifact with 10 buckets × 20-per-bucket floor = 200 absolute
# minimum. In practice MLB scaling means the floor is set by the
# Universal ECDF `fit` method itself; we short-circuit earlier with
# a more helpful log message.
MIN_PAIRS_FOR_FIT = 500


def regenerate_pairs(
    hf: MLBHighFrictionModel, stat_family: str, max_players: int | None = None,
) -> np.ndarray:
    """Regenerate (projection, actual) pairs from historical logs by
    running the hf model's own feature builder + predictor. Returns an
    (n, 2) float64 array. Skips rows the model can't score."""
    norm_stat = hf._normalize_stat(stat_family)
    if norm_stat not in hf.models:
        print(f"  [{stat_family}] hf pkl missing for canonical '{norm_stat}'; skip")
        return np.empty((0, 2), dtype=np.float64)

    model = hf.models[norm_stat]
    scaler = hf.scalers[norm_stat]
    feature_cols = hf.feature_cols[norm_stat]

    cursor = hf.historical_logs.find({}, {"_id": 0})
    pairs: List[List[float]] = []
    skipped_no_feats = 0
    skipped_no_target = 0
    players_seen = 0
    for player_doc in cursor:
        if max_players is not None and players_seen >= max_players:
            break
        players_seen += 1
        game_logs = player_doc.get("game_logs") or []
        if len(game_logs) < 20:
            continue
        player_name = player_doc.get("player_name")
        game_logs = sorted(
            game_logs, key=lambda x: x.get("date") or "1900-01-01",
            reverse=True,
        )
        player_master = hf.master_hub.find_one(
            {"$or": [{"display_name": player_name},
                     {"player_name": player_name}]},
            {"_id": 0},
        ) or {}
        for i in range(len(game_logs) - 20):
            target_game = game_logs[i]
            history = game_logs[i + 1:i + 31]
            target_value = hf._get_stat_value(target_game, norm_stat)
            if target_value is None:
                skipped_no_target += 1
                continue
            opponent = target_game.get("opponent_abbr")
            feats = hf._build_friction_features(
                player_master, history, norm_stat,
                opponent=opponent,
                park_team=None, dk_odds=None, line=None,
            )
            if feats is None:
                skipped_no_feats += 1
                continue
            # Build fixed-schema vector in training-time column order
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

    arr = np.asarray(pairs, dtype=np.float64) if pairs else np.empty((0, 2), dtype=np.float64)
    print(
        f"  [{stat_family}→{norm_stat}] pairs={len(arr):,}  "
        f"skip_no_feats={skipped_no_feats}  skip_no_target={skipped_no_target}  "
        f"players_walked={players_seen:,}"
    )
    return arr


def main():
    t0 = time.monotonic()
    client = pymongo.MongoClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    hf = MLBHighFrictionModel(db)
    hf.load_models()
    ecdf = UniversalECDFProbability()

    fit_summary: Dict[str, Dict] = {}
    for stat in STAT_FAMILIES:
        print(f"=== {stat} ===")
        pairs = regenerate_pairs(hf, stat)
        if len(pairs) < MIN_PAIRS_FOR_FIT:
            print(f"  [{stat}] insufficient pairs ({len(pairs)} < "
                  f"{MIN_PAIRS_FOR_FIT}); skip fit.")
            fit_summary[stat] = {"skipped": True, "pairs": int(len(pairs))}
            continue
        projs = pairs[:, 0]
        acts = pairs[:, 1]
        records = {"projection": projs, "actual": acts}
        try:
            norm_stat = hf._normalize_stat(stat)
            artifact = ecdf.fit(
                sport="mlb",
                stat_family=norm_stat,
                records=records,
                source_model_version=f"MLB_HF_v1.0:{norm_stat}",
            )
            print(
                f"  [{stat}→{norm_stat}] fitted  samples={artifact['sample_count']}  "
                f"min_bucket_n={artifact['min_bucket_n']}  "
                f"buckets={artifact['n_buckets']}"
            )
            fit_summary[stat] = {
                "canonical": norm_stat,
                "samples": artifact["sample_count"],
                "min_bucket_n": artifact["min_bucket_n"],
                "path": ecdf.artifact_path("mlb", norm_stat),
            }
        except ValueError as e:
            print(f"  [{stat}] fit rejected: {e}")
            fit_summary[stat] = {"error": str(e), "pairs": int(len(pairs))}

    print()
    print(f"Done in {time.monotonic() - t0:.1f}s")
    print("Summary:")
    for stat, info in fit_summary.items():
        print(f"  {stat}: {info}")
    client.close()


if __name__ == "__main__":
    main()

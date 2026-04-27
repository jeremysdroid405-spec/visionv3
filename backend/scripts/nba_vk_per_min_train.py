"""
NBA VK Per-Minute Retrain — Code-Ready Draft (DO NOT RUN YET)
=============================================================
Trains five separate per-minute regressors:

  PTS/min  REB/min  AST/min  PRA/min   — XGBRegressor (Gaussian / squared error)
  3PM/min                                — XGBRegressor (count:poisson loss)

Saves to NEW model paths only — DOES NOT overwrite existing VK2 .pkl files:

  /app/backend/models/vk2_per_min_pts.pkl
  /app/backend/models/vk2_per_min_reb.pkl
  /app/backend/models/vk2_per_min_ast.pkl
  /app/backend/models/vk2_per_min_pra.pkl
  /app/backend/models/vk2_per_min_3pm.pkl

Pipeline:
  1. Load training rows from `nba_vk2_training_set` (or equivalent table
     used to train the existing VK2 models — same source the existing
     .pkl files were trained from).
  2. Compute target = stat / minutes (Gaussian loss); for 3PM use
     `count:poisson` with offset = ln(minutes), then label = stat directly.
  3. Filter rows to `min ≥ MIN_MINUTES_THRESHOLD = 10`.
  4. Use the same 52 VK2 features (loaded from existing .pkl meta).
  5. 70/30 train/test split keyed by (player_id, season) so no leakage.
  6. Train, then bucket residuals by `expected_minutes` and store
     bucketed residual sigma so inference can pick a context-aware σ.
  7. Persist with the same dict-shape as existing VK2 .pkl files
     (model, scaler, features, samples_train, samples_test, mae_test,
     rmse_test, residual_sigma_empirical, plus new
     residual_sigma_by_minutes_bucket and target_kind="per_minute").

Usage (do NOT run until reviewed):
    python /app/backend/scripts/nba_vk_per_min_train.py --dry-run

Then to train all 5 once approved:
    python /app/backend/scripts/nba_vk_per_min_train.py --train all

For a single stat:
    python /app/backend/scripts/nba_vk_per_min_train.py --train pts

Environment:
    MONGO_URL, DB_NAME loaded from /app/backend/.env
"""
import os
import sys
import argparse
import pickle
import time
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# Optional imports — guarded so --dry-run works without xgb installed.
try:
    import xgboost as xgb
    from sklearn.preprocessing import StandardScaler
    from sklearn.model_selection import train_test_split
    _ML_OK = True
except ImportError:
    _ML_OK = False

sys.path.insert(0, "/app/backend")
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
import asyncio


# =====================================================================
# Configuration
# =====================================================================
MIN_MINUTES_THRESHOLD = 10           # rows with `min < 10` excluded
TRAIN_TEST_SPLIT = 0.30
RANDOM_SEED = 42

MODEL_OUT_DIR = "/app/backend/models"
EXISTING_VK2_TEMPLATE = {            # we copy the feature list from these
    "pts":  f"{MODEL_OUT_DIR}/vk2_pts.pkl",
    "reb":  f"{MODEL_OUT_DIR}/vk2_reb.pkl",
    "ast":  f"{MODEL_OUT_DIR}/vk2_ast.pkl",
    "pra":  f"{MODEL_OUT_DIR}/vk2_pra.pkl",
    "3pm":  f"{MODEL_OUT_DIR}/vk2_3pm.pkl",
}
NEW_MODEL_PATH = {
    s: f"{MODEL_OUT_DIR}/vk2_per_min_{s}.pkl" for s in EXISTING_VK2_TEMPLATE
}

# Stat-to-target mapping. Returned as raw stat value; the trainer divides
# by minutes (or sets ln-minutes offset for Poisson 3PM).
STAT_FIELD = {
    "pts": "pts", "reb": "reb", "ast": "ast",
    "pra": "pra",      # synthesized; trainer will compute pts+reb+ast
    "3pm": "fg3m",
}

# XGBoost hyperparameters — start with the same shape as existing VK2
# (n_estimators=400 was used in the prior training round per the .pkl
# meta `train_seconds` profile). Production tuning happens after a
# first clean run.
XGB_BASE = dict(
    n_estimators=400,
    max_depth=6,
    learning_rate=0.05,
    subsample=0.85,
    colsample_bytree=0.85,
    reg_lambda=1.0,
    reg_alpha=0.0,
    tree_method="hist",
    random_state=RANDOM_SEED,
    n_jobs=-1,
)
# Poisson-specific params for 3PM. count:poisson requires non-negative
# integer-like targets and uses ln(minutes) as offset to model rate.
XGB_POISSON = dict(XGB_BASE, objective="count:poisson")


# =====================================================================
# Training-data loader
# =====================================================================
async def load_training_rows(db, stat: str, features: List[str],
                             min_minutes: int = MIN_MINUTES_THRESHOLD,
                             ) -> Tuple[np.ndarray, np.ndarray,
                                        np.ndarray, List[Dict[str, Any]]]:
    """Pull labeled training rows from the same training collection
    the existing VK2 was built from.

    Returns (X, y, minutes, meta_rows).
    `y` is the RAW stat value (per-game total). The caller divides by
    minutes for Gaussian targets, or passes minutes as offset for Poisson.

    NOTE: the historical VK2 training collection is whichever Mongo
    collection persisted the (features, target, minutes) tuples at the
    time of the previous train. Adjust `TRAINING_COLLECTION` if your
    project uses a different name.
    """
    TRAINING_COLLECTION = "nba_vk2_training_rows"

    cur = db[TRAINING_COLLECTION].find(
        {"min": {"$gte": min_minutes}},
        {"_id": 0, "features": 1, STAT_FIELD[stat]: 1, "min": 1,
         "player_id": 1, "season": 1, "game_id": 1},
    )

    X_rows: List[List[float]] = []
    y_rows: List[float]       = []
    m_rows: List[float]       = []
    meta_rows: List[Dict[str, Any]] = []

    async for d in cur:
        feats = d.get("features") or {}
        # Skip rows missing any feature — let the model see clean data.
        try:
            row = [float(feats[f]) for f in features]
        except (KeyError, TypeError, ValueError):
            continue
        if stat == "pra":
            p = d.get("pts"); r = d.get("reb"); a = d.get("ast")
            if None in (p, r, a):
                continue
            y = float(p) + float(r) + float(a)
        else:
            v = d.get(STAT_FIELD[stat])
            if v is None:
                continue
            y = float(v)
        m = d.get("min")
        if m is None or float(m) < min_minutes:
            continue
        X_rows.append(row); y_rows.append(y); m_rows.append(float(m))
        meta_rows.append({
            "player_id": d.get("player_id"),
            "season":    d.get("season"),
            "game_id":   d.get("game_id"),
        })

    return (np.array(X_rows, dtype=np.float32),
            np.array(y_rows, dtype=np.float32),
            np.array(m_rows, dtype=np.float32),
            meta_rows)


# =====================================================================
# Player/season-aware split (avoids leakage)
# =====================================================================
def _player_season_split(meta_rows: List[Dict[str, Any]],
                         test_size: float = TRAIN_TEST_SPLIT,
                         seed: int = RANDOM_SEED) -> Tuple[np.ndarray, np.ndarray]:
    """Split by (player_id, season) so no player-season appears in both
    sets. Returns (train_idx, test_idx)."""
    keys = list({(m.get("player_id"), m.get("season")) for m in meta_rows})
    rng = np.random.default_rng(seed)
    rng.shuffle(keys)
    n_test = int(len(keys) * test_size)
    test_keys = set(keys[:n_test])
    test_idx = np.array([i for i, m in enumerate(meta_rows)
                         if (m.get("player_id"), m.get("season")) in test_keys])
    train_idx = np.array([i for i, m in enumerate(meta_rows)
                          if (m.get("player_id"), m.get("season")) not in test_keys])
    return train_idx, test_idx


# =====================================================================
# Residual-sigma bucketing (for inference σ calibration)
# =====================================================================
def _residual_sigma_buckets(predictions: np.ndarray, actuals: np.ndarray,
                            minutes: np.ndarray) -> Dict[str, Any]:
    """Per-minutes-bucket residual sigma. Inference looks up the bucket
    that contains expected_minutes and uses that σ instead of the global."""
    edges = [10, 14, 18, 22, 26, 30, 34, 38, 50]
    buckets: List[Dict[str, Any]] = []
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        mask = (minutes >= lo) & (minutes < hi)
        n = int(mask.sum())
        if n < 50:
            buckets.append({"min_lo": lo, "min_hi": hi, "n": n,
                            "sigma": None, "mae": None})
            continue
        resid = predictions[mask] - actuals[mask]
        buckets.append({
            "min_lo": lo, "min_hi": hi, "n": n,
            "sigma": float(np.sqrt(np.mean(resid ** 2))),
            "mae":   float(np.mean(np.abs(resid))),
            "bias":  float(np.mean(resid)),
        })
    return {"buckets": buckets, "edges": edges}


# =====================================================================
# Per-stat trainer
# =====================================================================
async def train_one(stat: str, dry_run: bool = False) -> Dict[str, Any]:
    if not _ML_OK and not dry_run:
        raise RuntimeError(
            "xgboost / sklearn not importable — install before --train"
        )
    print(f"\n[TRAIN] {'(DRY-RUN) ' if dry_run else ''}Building per-minute "
          f"model for {stat.upper()}")

    # Reuse the feature list from the existing VK2 model so the new
    # per-minute model is feature-compatible at inference.
    with open(EXISTING_VK2_TEMPLATE[stat], "rb") as f:
        existing = pickle.load(f)
    features: List[str] = list(existing["features"])
    print(f"  features: {len(features)}  (copied from {EXISTING_VK2_TEMPLATE[stat]})")

    if dry_run:
        print(f"  DRY-RUN — skipping data load, model build, save")
        return {"stat": stat, "features": len(features), "dry_run": True}

    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db  = cli[os.environ["DB_NAME"]]

    t0 = time.time()
    X, y_total, minutes, meta = await load_training_rows(
        db, stat=stat, features=features,
        min_minutes=MIN_MINUTES_THRESHOLD,
    )
    print(f"  loaded {len(X):,} rows  ({time.time() - t0:.1f}s)")
    if len(X) < 5_000:
        raise RuntimeError(f"Insufficient training rows for {stat}: {len(X)}")

    # Player/season-aware split.
    tr_idx, te_idx = _player_season_split(meta)
    X_tr, X_te = X[tr_idx], X[te_idx]
    y_tot_tr, y_tot_te = y_total[tr_idx], y_total[te_idx]
    m_tr, m_te = minutes[tr_idx], minutes[te_idx]

    # Scale features.
    scaler = StandardScaler()
    X_tr_s = scaler.fit_transform(X_tr)
    X_te_s = scaler.transform(X_te)

    # Build target. Gaussian: rate = stat / min. Poisson: stat with ln(min) offset.
    if stat == "3pm":
        # Poisson with ln(min) offset → predicts rate, scaled by exp(offset).
        # XGBoost supports `base_margin` for offset.
        model = xgb.XGBRegressor(**XGB_POISSON)
        offset_tr = np.log(m_tr).astype(np.float32)
        offset_te = np.log(m_te).astype(np.float32)
        model.fit(X_tr_s, y_tot_tr, sample_weight=None,
                  base_margin=offset_tr,
                  eval_set=[(X_te_s, y_tot_te)],
                  verbose=False)
        # Predict as rate * minutes (XGBoost handles the offset internally).
        y_pred_tot_te = model.predict(X_te_s, base_margin=offset_te)
        y_pred_tot_tr = model.predict(X_tr_s, base_margin=offset_tr)
        y_target_tr   = y_tot_tr / m_tr  # for residual analysis
        y_target_te   = y_tot_te / m_te
        target_kind = "per_minute_poisson"
    else:
        # Gaussian: train directly on per-minute rate.
        y_target_tr = y_tot_tr / m_tr
        y_target_te = y_tot_te / m_te
        model = xgb.XGBRegressor(**XGB_BASE)
        model.fit(X_tr_s, y_target_tr,
                  eval_set=[(X_te_s, y_target_te)], verbose=False)
        # Predicted total = predicted rate × minutes, for top-line metrics.
        rate_pred_tr = model.predict(X_tr_s)
        rate_pred_te = model.predict(X_te_s)
        y_pred_tot_tr = rate_pred_tr * m_tr
        y_pred_tot_te = rate_pred_te * m_te
        target_kind = "per_minute_gaussian"

    # ----- Metrics on the TOTAL scale (apples-to-apples vs current VK2) -
    mae_test  = float(np.mean(np.abs(y_pred_tot_te - y_tot_te)))
    rmse_test = float(np.sqrt(np.mean((y_pred_tot_te - y_tot_te) ** 2)))
    bias_test = float(np.mean(y_pred_tot_te - y_tot_te))
    sigma_emp = rmse_test
    # R² on totals
    ss_res = float(np.sum((y_tot_te - y_pred_tot_te) ** 2))
    ss_tot = float(np.sum((y_tot_te - np.mean(y_tot_te)) ** 2))
    r2_test = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else None

    # ----- Residual σ buckets by expected_minutes ------------------------
    sigma_buckets = _residual_sigma_buckets(y_pred_tot_te, y_tot_te, m_te)

    # ----- Persist (NEW path; existing VK2 untouched) --------------------
    out = {
        "stat_label":    stat.upper(),
        "stat_field":    STAT_FIELD[stat],
        "version":       "per_min_v1",
        "trained_at":    datetime.now(timezone.utc).isoformat(),
        "target_kind":   target_kind,                      # NEW
        "min_minutes_threshold": MIN_MINUTES_THRESHOLD,    # NEW
        "model":         model,
        "scaler":        scaler,
        "features":      features,
        "feature_count": len(features),
        "samples_train": int(len(X_tr)),
        "samples_test":  int(len(X_te)),
        "mae_test":      mae_test,                # on TOTAL scale
        "rmse_test":     rmse_test,
        "bias_test":     bias_test,
        "r2_test":       r2_test,
        "residual_sigma_empirical": sigma_emp,    # global, on totals
        "residual_sigma_by_minutes_bucket": sigma_buckets,  # NEW
        # Carry forward the existing residual sigma so a downstream
        # comparator can see the lift in calibration. NOT used at inference.
        "prior_vk2_residual_sigma": existing.get("residual_sigma_empirical"),
    }
    out_path = NEW_MODEL_PATH[stat]
    with open(out_path, "wb") as f:
        pickle.dump(out, f)
    print(f"  saved → {out_path}")
    print(f"  test metrics (on totals): MAE={mae_test:.3f}  "
          f"RMSE={rmse_test:.3f}  bias={bias_test:+.3f}  R²={r2_test:.3f}")
    print(f"  prior VK2 σ:  {existing.get('residual_sigma_empirical'):.3f}   "
          f"new σ:  {sigma_emp:.3f}   "
          f"Δ = {sigma_emp - existing.get('residual_sigma_empirical'):+.3f}")
    return out


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", choices=list(EXISTING_VK2_TEMPLATE) + ["all"],
                    help="train one stat or 'all' five stats")
    ap.add_argument("--dry-run", action="store_true",
                    help="walk through model wiring without loading data")
    return ap.parse_args()


async def main():
    args = parse_args()
    if not args.train and not args.dry_run:
        print("Specify --train <stat|all> or --dry-run.")
        return
    stats = list(EXISTING_VK2_TEMPLATE) if args.train == "all" else (
        [args.train] if args.train else list(EXISTING_VK2_TEMPLATE))
    summary: List[Dict[str, Any]] = []
    for s in stats:
        info = await train_one(s, dry_run=args.dry_run)
        summary.append(info)
    print("\n[TRAIN] DONE")
    print(f"  trained {len(summary)} stat(s)  (dry-run={args.dry_run})")


if __name__ == "__main__":
    asyncio.run(main())

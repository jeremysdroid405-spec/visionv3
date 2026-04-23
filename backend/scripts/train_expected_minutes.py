"""Expected Minutes training script (2026-04-23).

STRICT SPEC: Train a dedicated minutes regressor on a narrow, purpose-built
feature set. This model is a structural projection correction — it is
later composed with every VK2 stat prediction via
`adjusted_projection = (model_projection / min_played_L10_mean) * predicted_minutes`.

Feature set (locked):
  Core:    min_played_L3_mean, min_played_L5_mean, min_played_L10_mean,
           min_played_L20_mean, min_played_L10_std, min_played_L20_std
  Trend:   min_trend_L5_vs_L20
  Role:    starter_flag, rotation_flag, bench_flag
  Counts:  games_played_last_10, games_started_last_10

Target: minutes_played per game (float).

Dataset:
  Same `bdl_historical_game_logs` seasons 2020-2024 as VK2, same
  recency weighting (2024=1.00 → 2020=0.40). 2024 held out as test.
"""
from __future__ import annotations

import gc
import logging
import math
import os
import pickle
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, "/app/backend")
os.chdir("/app/backend")

import numpy as np
import pymongo
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.preprocessing import StandardScaler
from xgboost import XGBRegressor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("min_v2")

client = pymongo.MongoClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
db = client[os.environ.get("DB_NAME", "pick_vision")]
coll = db.bdl_historical_game_logs

SEASONS = [2020, 2021, 2022, 2023, 2024]
SEASON_WEIGHTS = {2024: 1.00, 2023: 0.85, 2022: 0.70, 2021: 0.55, 2020: 0.40}
ROLLING_WINDOW = 20
MIN_HISTORY_REQUIRED = 5
MIN_GAMES_PER_PLAYER = 12
MODEL_DIR = "/app/backend/models"
os.makedirs(MODEL_DIR, exist_ok=True)
OUT_PATH = os.path.join(MODEL_DIR, "expected_minutes.pkl")

FEATURE_SCHEMA = [
    "min_played_L3_mean",
    "min_played_L5_mean",
    "min_played_L10_mean",
    "min_played_L20_mean",
    "min_played_L10_std",
    "min_played_L20_std",
    "min_trend_L5_vs_L20",
    "starter_flag",
    "rotation_flag",
    "bench_flag",
    "games_played_last_10",
    "games_started_last_10",
]
assert len(FEATURE_SCHEMA) == 12


def _parse_minutes(value):
    """Parse BDL `min` → float minutes. Accepts "30", "31:24", 30, None."""
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        if ":" in s:
            try:
                mm, ss = s.split(":")
                return float(mm) + float(ss) / 60.0
            except Exception:
                return None
        try:
            return float(s)
        except Exception:
            return None
    try:
        return float(value)
    except Exception:
        return None


def _mean_std(vals):
    if not vals:
        return 0.0, 0.0
    arr = np.asarray(vals, dtype=np.float32)
    return float(arr.mean()), (float(arr.std(ddof=1)) if len(arr) >= 2 else 0.0)


def build_features_v2(history_logs):
    """history_logs: chronological-descending (newest first) BEFORE target.

    Returns a dict of the 12 spec features, or None if fewer than 5
    usable prior games. DNP entries (None minutes) are included as
    0-minute appearances so the role flags reflect actual recent usage.
    """
    if len(history_logs) < MIN_HISTORY_REQUIRED:
        return None

    mins_series = []
    for g in history_logs[:ROLLING_WINDOW]:
        m = _parse_minutes(g.get("min"))
        mins_series.append(m if m is not None else 0.0)

    # Rolling means / stds on the resolved minutes series.
    m_L3_mean,  _ = _mean_std(mins_series[:3])
    m_L5_mean,  _ = _mean_std(mins_series[:5])
    m_L10_mean, m_L10_std = _mean_std(mins_series[:10])
    m_L20_mean, m_L20_std = _mean_std(mins_series[:20])

    trend = m_L5_mean - m_L20_mean

    # Role flags — mutually exclusive by construction.
    starter_flag  = 1.0 if m_L5_mean >= 28.0 else 0.0
    rotation_flag = 1.0 if (18.0 <= m_L5_mean < 28.0) else 0.0
    bench_flag    = 1.0 if m_L5_mean < 18.0 else 0.0

    # Count features (L10 window). A "game played" is any log with
    # resolved minutes > 0. "Games started" is approximated by games
    # with min >= 20 since bdl_historical_game_logs doesn't carry a
    # started flag — this matches how PropVision distinguishes rotation
    # minutes elsewhere.
    last10 = mins_series[:10]
    games_played_last_10  = float(sum(1 for m in last10 if m > 0))
    games_started_last_10 = float(sum(1 for m in last10 if m >= 20))

    return {
        "min_played_L3_mean":     m_L3_mean,
        "min_played_L5_mean":     m_L5_mean,
        "min_played_L10_mean":    m_L10_mean,
        "min_played_L20_mean":    m_L20_mean,
        "min_played_L10_std":     m_L10_std,
        "min_played_L20_std":     m_L20_std,
        "min_trend_L5_vs_L20":    trend,
        "starter_flag":           starter_flag,
        "rotation_flag":          rotation_flag,
        "bench_flag":             bench_flag,
        "games_played_last_10":   games_played_last_10,
        "games_started_last_10":  games_started_last_10,
    }


def build_training_matrix():
    log.info("building matrix from bdl_historical_game_logs...")
    t0 = time.monotonic()
    pipeline = [
        {"$match": {"season": {"$in": SEASONS}}},
        {"$sort": {"player_id": 1, "game_id": 1}},
    ]
    current_pid = None
    current_logs = []
    X_chunks, y_chunks, w_chunks = [], [], []
    players_used = 0

    def flush_player(pid, logs_chrono):
        nonlocal players_used
        if len(logs_chrono) < MIN_GAMES_PER_PLAYER:
            return
        px, py, pw = [], [], []
        for i in range(MIN_HISTORY_REQUIRED, len(logs_chrono)):
            tgt = logs_chrono[i]
            if tgt.get("season") not in SEASON_WEIGHTS:
                continue
            tgt_min = _parse_minutes(tgt.get("min"))
            if tgt_min is None:
                tgt_min = 0.0
            history_desc = list(reversed(
                logs_chrono[max(0, i - ROLLING_WINDOW):i]
            ))
            feats = build_features_v2(history_desc)
            if feats is None:
                continue
            row = [feats[c] for c in FEATURE_SCHEMA]
            px.append(row)
            py.append(float(tgt_min))
            pw.append(SEASON_WEIGHTS.get(tgt.get("season"), 0.4))
        if px:
            X_chunks.append(np.asarray(px, dtype=np.float32))
            y_chunks.append(np.asarray(py, dtype=np.float32))
            w_chunks.append(np.asarray(pw, dtype=np.float32))
        players_used += 1

    cursor = coll.aggregate(pipeline, allowDiskUse=True, batchSize=5000)
    seen = 0
    for doc in cursor:
        pid = doc.get("player_id")
        if pid != current_pid:
            if current_pid is not None and current_logs:
                flush_player(current_pid, current_logs)
                seen += 1
                if seen % 200 == 0:
                    gc.collect()
            current_pid = pid
            current_logs = []
        current_logs.append(doc)
    if current_pid is not None and current_logs:
        flush_player(current_pid, current_logs)

    if not X_chunks:
        return None, None, None

    X = np.vstack(X_chunks)
    y = np.concatenate(y_chunks)
    w = np.concatenate(w_chunks)
    log.info(
        f"matrix ready: X={X.shape} y={y.shape} players={players_used} "
        f"elapsed={time.monotonic() - t0:.1f}s"
    )
    return X, y, w


def _segment(y_true, y_pred, name):
    if len(y_true) == 0:
        return {"segment": name, "n": 0}
    rmse = math.sqrt(mean_squared_error(y_true, y_pred))
    mae = mean_absolute_error(y_true, y_pred)
    bias = float(np.mean(y_pred - y_true))
    return {
        "segment": name,
        "n": int(len(y_true)),
        "rmse": round(rmse, 4),
        "mae": round(mae, 4),
        "bias": round(bias, 4),
        "y_mean": round(float(y_true.mean()), 4),
        "pred_mean": round(float(y_pred.mean()), 4),
    }


def train():
    X, y, sw = build_training_matrix()
    if X is None:
        raise SystemExit("no samples")

    test_mask = sw >= 0.99  # 2024 rows
    train_mask = ~test_mask
    X_tr, y_tr, w_tr = X[train_mask], y[train_mask], sw[train_mask]
    X_te, y_te = X[test_mask], y[test_mask]

    scaler = StandardScaler()
    X_tr_s = scaler.fit_transform(X_tr)
    X_te_s = scaler.transform(X_te)

    xgb = XGBRegressor(
        n_estimators=300, max_depth=5, learning_rate=0.07,
        subsample=0.85, colsample_bytree=0.85,
        reg_alpha=0.1, reg_lambda=1.0,
        random_state=42, verbosity=0, n_jobs=4, tree_method="hist",
    )
    t0 = time.monotonic()
    xgb.fit(X_tr_s, y_tr, sample_weight=w_tr)
    train_seconds = time.monotonic() - t0

    y_pred_te = xgb.predict(X_te_s)
    r2_te = r2_score(y_te, y_pred_te)
    mae_te = mean_absolute_error(y_te, y_pred_te)
    rmse_te = math.sqrt(mean_squared_error(y_te, y_pred_te))
    bias_te = float(np.mean(y_pred_te - y_te))
    sigma = float((y_te - y_pred_te).std(ddof=1))

    # Segmented metrics (on 2024 hold-out) — role based.
    seg = []
    seg.append(_segment(y_te, y_pred_te, "overall"))
    mask_starter = X_te[:, FEATURE_SCHEMA.index("starter_flag")] >= 0.5
    mask_rotation = X_te[:, FEATURE_SCHEMA.index("rotation_flag")] >= 0.5
    mask_bench = X_te[:, FEATURE_SCHEMA.index("bench_flag")] >= 0.5
    seg.append(_segment(y_te[mask_starter],  y_pred_te[mask_starter],  "starter (min_L5>=28)"))
    seg.append(_segment(y_te[mask_rotation], y_pred_te[mask_rotation], "rotation (18-28)"))
    seg.append(_segment(y_te[mask_bench],    y_pred_te[mask_bench],    "bench (<18)"))
    declining_mask = X_te[:, FEATURE_SCHEMA.index("min_trend_L5_vs_L20")] < -2.0
    seg.append(_segment(y_te[declining_mask], y_pred_te[declining_mask],
                        "declining (L5-L20<-2)"))

    fi = sorted(
        [(FEATURE_SCHEMA[i], float(xgb.feature_importances_[i]))
         for i in range(len(FEATURE_SCHEMA))],
        key=lambda x: -x[1],
    )

    payload = {
        "model": xgb,
        "scaler": scaler,
        "features": list(FEATURE_SCHEMA),
        "version": "NBA_EXPECTED_MINUTES_v2_strict",
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "seasons_used": SEASONS,
        "season_weights": SEASON_WEIGHTS,
        "samples_train": int(X_tr.shape[0]),
        "samples_test": int(X_te.shape[0]),
        "train_seconds": round(train_seconds, 2),
        "r2_test": round(r2_te, 4),
        "mae_test": round(mae_te, 4),
        "rmse_test": round(rmse_te, 4),
        "bias_test": round(bias_te, 4),
        "sigma_minutes": round(sigma, 4),
        "top_features": fi,
        "segment_metrics": seg,
    }
    with open(OUT_PATH, "wb") as f:
        pickle.dump(payload, f)

    log.info(
        f"TRAINED in {train_seconds:.1f}s  |  features={len(FEATURE_SCHEMA)} "
        f"train={X_tr.shape[0]} test={X_te.shape[0]}"
    )
    log.info(
        f"  R²_test={r2_te:.4f}  MAE={mae_te:.3f}  RMSE={rmse_te:.3f}  "
        f"bias={bias_te:+.3f}  σ_minutes={sigma:.3f}"
    )
    log.info("Top-5 features by gain:")
    for name, val in fi[:5]:
        log.info(f"  {name:26s}  {val:.4f}")
    log.info("Segment metrics (2024 hold-out):")
    for s in seg:
        log.info(
            f"  {s['segment']:26s}  n={s['n']:>6d}  "
            f"RMSE={s.get('rmse','-')}  MAE={s.get('mae','-')}  "
            f"bias={s.get('bias','-'):+}" if s.get('n')
            else f"  {s['segment']:26s}  n=0  skipped"
        )
    log.info(f"saved: {OUT_PATH}")
    return payload


if __name__ == "__main__":
    t_all = time.monotonic()
    train()
    log.info(f"DONE in {time.monotonic() - t_all:.1f}s")
    client.close()

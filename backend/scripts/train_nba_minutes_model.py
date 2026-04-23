"""NBA expected-minutes regression model (2026-04-23).

Purpose
-------
Predict a player's **minutes tonight** from their rolling minutes history.
Composed downstream with VK2 stat models to correct the +2 to +4 bias VK2
currently has on low-line bench props: the bench guy's rolling PTS_L5
says 8.0 because the rolling average includes high-minute nights, but
tonight he actually plays 12 min. The expected-minutes signal lets us
scale the per-minute rate to tonight's actual opportunity.

Inputs
------
Read-only from `bdl_historical_game_logs` (seasons 2020-2024).
No live I/O.

Features (minutes-only, 15)
---------------------------
  min_L3_mean, min_L5_mean, min_L10_mean, min_L20_mean
  min_L3_std, min_L5_std, min_L10_std, min_L20_std
  min_L3_L10_diff               (trend: L3 mean - L10 mean)
  min_L5_L20_diff               (longer-horizon trend)
  min_floor_L20, min_ceiling_L20
  min_dnp_rate_L20              (fraction of last 20 logs with min < 5)
  min_low_rate_L10              (fraction of last 10 logs with min < 15)
  appearances_L20               (count of non-null minutes in last 20)

Target
------
Actual minutes played in next game, after the same `_mins()` parser as
the production feature builder. DNP rows (min=0 or None) are *included*
as training samples because bench-player DNPs ARE the regime we most
need to learn.

Training
--------
Temporal split: 2024 held out as test set (matches VK2 convention).
Weighted XGBRegressor (2024 = 1.00 recency weight, older seasons
taper down to 0.40 for 2020).

Output
------
`/app/backend/models/nba_expected_minutes.pkl` with
  { model, scaler, features, version, trained_at, metrics, residual_sigma }.
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
log = logging.getLogger("min_train")

client = pymongo.MongoClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
db = client[os.environ.get("DB_NAME", "pick_vision")]
coll = db.bdl_historical_game_logs

SEASONS = [2020, 2021, 2022, 2023, 2024]
SEASON_WEIGHTS = {2024: 1.00, 2023: 0.85, 2022: 0.70, 2021: 0.55, 2020: 0.40}
MIN_GAMES_PER_PLAYER = 12
ROLLING_WINDOW = 20
MODEL_DIR = "/app/backend/models"
os.makedirs(MODEL_DIR, exist_ok=True)

# The definitive minutes-only feature schema for this model.
FEATURE_SCHEMA = [
    "min_L3_mean", "min_L5_mean", "min_L10_mean", "min_L20_mean",
    "min_L3_std", "min_L5_std", "min_L10_std", "min_L20_std",
    "min_L3_L10_diff", "min_L5_L20_diff",
    "min_floor_L20", "min_ceiling_L20",
    "min_dnp_rate_L20", "min_low_rate_L10",
    "appearances_L20",
]
assert len(FEATURE_SCHEMA) == 15


def parse_minutes(value):
    """Parse a BDL `min` field into float minutes, or None if unparseable.

    Accepts plain "30", legacy "31:24", numeric, or None.
    """
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


def _mean_std(vals, w):
    sub = vals[:w]
    if not sub:
        return 0.0, 0.0
    arr = np.asarray(sub, dtype=np.float32)
    mean = float(arr.mean())
    std = float(arr.std(ddof=1)) if len(arr) >= 2 else 0.0
    return mean, std


def build_minutes_features(history_logs):
    """history_logs: chronological-descending (newest first) logs BEFORE
    the target game. Returns a flat dict of minutes-only features or
    None if fewer than 5 usable games.

    DNP games (min parse-fail) are treated as 0-minute appearances so
    the bench-regime signal is preserved.
    """
    if len(history_logs) < 5:
        return None

    # Resolve minutes — treat None as 0.0 (DNP) so dnp_rate captures it.
    mins_series = []
    for g in history_logs[:ROLLING_WINDOW]:
        m = parse_minutes(g.get("min"))
        mins_series.append(m if m is not None else 0.0)

    feats = {}
    for w, key in ((3, "L3"), (5, "L5"), (10, "L10"), (20, "L20")):
        mean, std = _mean_std(mins_series, w)
        feats[f"min_{key}_mean"] = mean
        feats[f"min_{key}_std"] = std

    feats["min_L3_L10_diff"] = feats["min_L3_mean"] - feats["min_L10_mean"]
    feats["min_L5_L20_diff"] = feats["min_L5_mean"] - feats["min_L20_mean"]

    last20 = mins_series[:20]
    feats["min_floor_L20"] = float(min(last20)) if last20 else 0.0
    feats["min_ceiling_L20"] = float(max(last20)) if last20 else 0.0
    feats["min_dnp_rate_L20"] = (
        sum(1 for m in last20 if m < 5) / float(len(last20))
    ) if last20 else 0.0
    last10 = mins_series[:10]
    feats["min_low_rate_L10"] = (
        sum(1 for m in last10 if m < 15) / float(len(last10))
    ) if last10 else 0.0
    feats["appearances_L20"] = float(sum(1 for m in last20 if m > 0))
    return feats


def build_training_matrix():
    log.info("building minutes training matrix...")
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
        for i in range(5, len(logs_chrono)):
            tgt = logs_chrono[i]
            if tgt.get("season") not in SEASON_WEIGHTS:
                continue
            tgt_min = parse_minutes(tgt.get("min"))
            if tgt_min is None:
                # treat DNP as 0 min — this is the regime we want to learn
                tgt_min = 0.0
            history_desc = list(reversed(logs_chrono[max(0, i - ROLLING_WINDOW):i]))
            feats = build_minutes_features(history_desc)
            if feats is None:
                continue
            row = [feats.get(c, 0.0) for c in FEATURE_SCHEMA]
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
        f"weighted_sum={float(w.sum()):.1f} elapsed={time.monotonic() - t0:.1f}s"
    )
    return X, y, w


def train():
    X, y, sw = build_training_matrix()
    if X is None:
        raise SystemExit("no samples built")

    # Temporal split: 2024 (weight==1.00) is held out as test.
    test_mask = sw >= 0.99
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
    residuals = y_te - y_pred_te
    sigma = float(residuals.std(ddof=1))
    bias = float(residuals.mean())

    # Segment metrics on the 2024 test set so the operator sees whether
    # the minutes model covers the bench regime we care about.
    segs = {}
    role_bench_mask = X_te[:, FEATURE_SCHEMA.index("min_L10_mean")] < 20
    role_starter_mask = X_te[:, FEATURE_SCHEMA.index("min_L10_mean")] >= 30
    declining_mask = X_te[:, FEATURE_SCHEMA.index("min_L3_L10_diff")] < -2
    for name, mask in (
        ("bench (min_L10<20)", role_bench_mask),
        ("starter (min_L10>=30)", role_starter_mask),
        ("declining (L3-L10<-2)", declining_mask),
    ):
        if mask.sum() < 30:
            continue
        yt = y_te[mask]
        yp = y_pred_te[mask]
        segs[name] = {
            "n": int(mask.sum()),
            "rmse": round(float(math.sqrt(mean_squared_error(yt, yp))), 3),
            "mae": round(float(mean_absolute_error(yt, yp)), 3),
            "bias": round(float((yp - yt).mean()), 3),
            "y_mean": round(float(yt.mean()), 3),
            "pred_mean": round(float(yp.mean()), 3),
        }

    fi = xgb.feature_importances_
    top_features = sorted(
        [(FEATURE_SCHEMA[i], float(fi[i])) for i in range(len(FEATURE_SCHEMA))],
        key=lambda x: -x[1],
    )

    payload = {
        "model": xgb,
        "scaler": scaler,
        "features": list(FEATURE_SCHEMA),
        "version": "NBA_EXPECTED_MINUTES_v1",
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "seasons_used": SEASONS,
        "season_weights": SEASON_WEIGHTS,
        "samples_train": int(X_tr.shape[0]),
        "samples_test": int(X_te.shape[0]),
        "train_seconds": round(train_seconds, 2),
        "r2_test": round(r2_te, 4),
        "mae_test": round(mae_te, 4),
        "rmse_test": round(rmse_te, 4),
        "bias_test": round(bias, 4),
        "residual_sigma_empirical": round(sigma, 4),
        "top_features": top_features,
        "segment_metrics": segs,
    }
    out_path = os.path.join(MODEL_DIR, "nba_expected_minutes.pkl")
    with open(out_path, "wb") as f:
        pickle.dump(payload, f)

    log.info(
        f"TRAINED in {train_seconds:.1f}s | features={len(FEATURE_SCHEMA)} "
        f"train={X_tr.shape[0]} test={X_te.shape[0]} | "
        f"R2_test={r2_te:.4f} MAE={mae_te:.3f} RMSE={rmse_te:.3f} "
        f"σ_residual={sigma:.3f} bias={bias:+.3f}"
    )
    log.info("top 5 features: " + ", ".join(
        f"{n}={v:.3f}" for n, v in top_features[:5]
    ))
    for seg_name, m in segs.items():
        log.info(f"  seg {seg_name}: {m}")
    return payload


if __name__ == "__main__":
    t0 = time.monotonic()
    train()
    log.info(f"DONE in {time.monotonic() - t0:.1f}s")
    client.close()

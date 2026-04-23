"""Low-Minutes / DNP-Risk Classifier (2026-04-23).

Binary XGBoost classifier predicting whether a player's `minutes_played`
next game will be <= 12 (and an optional variant for <= 8). Used
downstream in NBA scoring to blend the VK2 baseline projection with a
per-player "low-minutes" expected stat — a structural fix for the
zero-inflated DNP tail that causes systematic overprediction on low-line
bench props.

Training corpus mirrors VK2 exactly:
  * seasons 2020–2024
  * recency weights {2024:1.00, 2023:0.85, 2022:0.70, 2021:0.55, 2020:0.40}
  * 2024 held out as test

Feature schema (locked by spec, 12 required + 3 optional situational):
  Core minutes:
    min_played_L3_mean, min_played_L5_mean, min_played_L10_mean,
    min_played_L20_mean, min_played_L10_std, min_played_L20_std
  Trend:
    min_trend_L5_vs_L20
  Counts:
    games_played_last_10, games_started_last_10
  Role flags:
    starter_flag, rotation_flag, bench_flag
  Situational (optional, from `opponent_context.OpponentContextStore`):
    home_flag, rest_days, back_to_back_flag

Output: `/app/backend/models/low_minutes_classifier.pkl`
  { model, scaler, features, version, metrics, calibration, ...
    low_12_threshold=12, low_8_threshold=8 }
"""
from __future__ import annotations

import gc
import json
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
from sklearn.calibration import calibration_curve
from sklearn.metrics import (
    average_precision_score, brier_score_loss, confusion_matrix,
    log_loss, precision_recall_fscore_support, roc_auc_score,
)
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("low_mins")

client = pymongo.MongoClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
db = client[os.environ.get("DB_NAME", "pick_vision")]
coll = db.bdl_historical_game_logs

SEASONS = [2020, 2021, 2022, 2023, 2024]
SEASON_WEIGHTS = {2024: 1.00, 2023: 0.85, 2022: 0.70, 2021: 0.55, 2020: 0.40}
ROLLING_WINDOW = 20
MIN_HISTORY_REQUIRED = 5
MIN_GAMES_PER_PLAYER = 12
LOW_MIN_THRESHOLD = 12
VERY_LOW_MIN_THRESHOLD = 8
MODEL_DIR = "/app/backend/models"
REPORT_PATH = "/app/backend/reports/low_minutes_classifier_eval.json"
os.makedirs(MODEL_DIR, exist_ok=True)
os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)

FEATURE_SCHEMA = [
    # Core minutes
    "min_played_L3_mean",
    "min_played_L5_mean",
    "min_played_L10_mean",
    "min_played_L20_mean",
    "min_played_L10_std",
    "min_played_L20_std",
    # Trend
    "min_trend_L5_vs_L20",
    # Counts
    "games_played_last_10",
    "games_started_last_10",
    # Role flags
    "starter_flag",
    "rotation_flag",
    "bench_flag",
    # Situational (optional)
    "home_flag",
    "rest_days",
    "back_to_back_flag",
]
assert len(FEATURE_SCHEMA) == 15


def _parse_minutes(value):
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


def build_classifier_features(history_logs, situational=None):
    """history_logs: chronological-descending. Returns feature dict or
    None if <5 usable prior games.

    `situational` (optional dict with home_flag / rest_days /
    back_to_back_flag) is provided by training / live wrapper. When
    None, all three default to 0 / 3 / 0 — these represent "unknown"
    but nonzero values the model has seen during training with the
    same default policy.
    """
    if len(history_logs) < MIN_HISTORY_REQUIRED:
        return None

    mins_series = []
    for g in history_logs[:ROLLING_WINDOW]:
        m = _parse_minutes(g.get("min"))
        mins_series.append(m if m is not None else 0.0)

    m_L3_mean, _ = _mean_std(mins_series[:3])
    m_L5_mean, _ = _mean_std(mins_series[:5])
    m_L10_mean, m_L10_std = _mean_std(mins_series[:10])
    m_L20_mean, m_L20_std = _mean_std(mins_series[:20])
    trend = m_L5_mean - m_L20_mean

    last10 = mins_series[:10]
    games_played = float(sum(1 for m in last10 if m > 0))
    games_started = float(sum(1 for m in last10 if m >= 20))

    starter_flag = 1.0 if m_L5_mean >= 28.0 else 0.0
    rotation_flag = 1.0 if (18.0 <= m_L5_mean < 28.0) else 0.0
    bench_flag = 1.0 if m_L5_mean < 18.0 else 0.0

    situational = situational or {}
    return {
        "min_played_L3_mean": m_L3_mean,
        "min_played_L5_mean": m_L5_mean,
        "min_played_L10_mean": m_L10_mean,
        "min_played_L20_mean": m_L20_mean,
        "min_played_L10_std": m_L10_std,
        "min_played_L20_std": m_L20_std,
        "min_trend_L5_vs_L20": trend,
        "games_played_last_10": games_played,
        "games_started_last_10": games_started,
        "starter_flag": starter_flag,
        "rotation_flag": rotation_flag,
        "bench_flag": bench_flag,
        "home_flag": float(situational.get("home_flag", 0.0)),
        "rest_days": float(situational.get("rest_days", 3.0)),
        "back_to_back_flag": float(situational.get("back_to_back_flag", 0.0)),
    }


def build_training_matrix(opp_store=None):
    log.info("building matrix from bdl_historical_game_logs...")
    t0 = time.monotonic()
    pipeline = [
        {"$match": {"season": {"$in": SEASONS}}},
        {"$sort": {"player_id": 1, "game_id": 1}},
    ]
    current_pid = None
    current_logs = []
    X_chunks, y12_chunks, y8_chunks, w_chunks = [], [], [], []

    def flush_player(pid, logs_chrono):
        if len(logs_chrono) < MIN_GAMES_PER_PLAYER:
            return
        px, py12, py8, pw = [], [], [], []
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
            # Pull situational from the opp_store, if provided (training).
            situational = None
            if opp_store is not None:
                team_id = tgt.get("team_id")
                game_id = tgt.get("game_id")
                if team_id is not None and game_id is not None:
                    from services.features.opponent_context import (  # noqa: E402
                        resolve_opponent_team_id,
                    )
                    opp_id = resolve_opponent_team_id(opp_store, int(team_id), int(game_id))
                    opp_feats = opp_store.get_features(
                        team_id=int(team_id),
                        opponent_team_id=opp_id,
                        game_id=int(game_id),
                        game_date=tgt.get("date"),
                        is_home=None,
                    )
                    situational = {
                        "home_flag": opp_feats.get("home_flag", 0.0),
                        "rest_days": opp_feats.get("rest_days", 3.0),
                        "back_to_back_flag": opp_feats.get("back_to_back_flag", 0.0),
                    }
            feats = build_classifier_features(history_desc, situational)
            if feats is None:
                continue
            row = [feats[c] for c in FEATURE_SCHEMA]
            px.append(row)
            py12.append(1 if tgt_min <= LOW_MIN_THRESHOLD else 0)
            py8.append(1 if tgt_min <= VERY_LOW_MIN_THRESHOLD else 0)
            pw.append(SEASON_WEIGHTS.get(tgt.get("season"), 0.4))
        if px:
            X_chunks.append(np.asarray(px, dtype=np.float32))
            y12_chunks.append(np.asarray(py12, dtype=np.int8))
            y8_chunks.append(np.asarray(py8, dtype=np.int8))
            w_chunks.append(np.asarray(pw, dtype=np.float32))

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
        return None, None, None, None

    X = np.vstack(X_chunks)
    y12 = np.concatenate(y12_chunks)
    y8 = np.concatenate(y8_chunks)
    w = np.concatenate(w_chunks)
    log.info(
        f"matrix: X={X.shape} pos_rate_12={y12.mean():.3f} "
        f"pos_rate_8={y8.mean():.3f} elapsed={time.monotonic() - t0:.1f}s"
    )
    return X, y12, y8, w


def _segmented_metrics(X_te, y_te, p_te, feature_cols, label):
    """Compute AUC / AP / precision / recall at threshold 0.5, plus
    segment breakdowns."""
    overall = {
        "n": int(len(y_te)),
        "pos_rate": round(float(y_te.mean()), 4),
        "auc": round(float(roc_auc_score(y_te, p_te)), 4) if len(set(y_te)) > 1 else None,
        "avg_precision": round(float(average_precision_score(y_te, p_te)), 4),
        "log_loss": round(float(log_loss(y_te, p_te, labels=[0, 1])), 4),
        "brier": round(float(brier_score_loss(y_te, p_te)), 4),
        "pred_mean": round(float(p_te.mean()), 4),
    }
    # Threshold sweep
    thresholds = {}
    for thr in (0.3, 0.4, 0.5, 0.6, 0.7):
        y_hat = (p_te >= thr).astype(int)
        p, r, f1, _ = precision_recall_fscore_support(
            y_te, y_hat, labels=[1], zero_division=0,
        )
        tn, fp, fn, tp = confusion_matrix(y_te, y_hat, labels=[0, 1]).ravel()
        thresholds[f"{thr:.2f}"] = {
            "precision": round(float(p[0]), 4),
            "recall": round(float(r[0]), 4),
            "f1": round(float(f1[0]), 4),
            "tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp),
        }
    # Calibration buckets (reliability)
    calib = []
    fractions, preds = calibration_curve(y_te, p_te, n_bins=10, strategy="quantile")
    for emp, pred in zip(fractions, preds):
        calib.append({
            "predicted": round(float(pred), 4),
            "actual": round(float(emp), 4),
            "delta": round(float(emp - pred), 4),
        })

    # Segments
    idx_l10 = feature_cols.index("min_played_L10_mean")
    idx_l5 = feature_cols.index("min_played_L5_mean")
    idx_l20 = feature_cols.index("min_played_L20_mean")
    seg = {}
    for name, mask in (
        ("bench (L10<20)", X_te[:, idx_l10] < 20),
        ("rotation (L10 18-28)", (X_te[:, idx_l10] >= 18) & (X_te[:, idx_l10] < 28)),
        ("starter (L10>=28)", X_te[:, idx_l10] >= 28),
        ("declining (L5-L20<-2)", (X_te[:, idx_l5] - X_te[:, idx_l20]) < -2),
    ):
        n = int(mask.sum())
        if n < 30:
            continue
        y_seg = y_te[mask]
        p_seg = p_te[mask]
        seg[name] = {
            "n": n,
            "pos_rate": round(float(y_seg.mean()), 4),
            "auc": (round(float(roc_auc_score(y_seg, p_seg)), 4)
                    if len(set(y_seg)) > 1 else None),
            "pred_mean": round(float(p_seg.mean()), 4),
            "brier": round(float(brier_score_loss(y_seg, p_seg)), 4),
        }
    return {
        "label": label,
        "overall": overall,
        "thresholds": thresholds,
        "calibration_deciles": calib,
        "segments": seg,
    }


def train():
    # Opp store for optional home/rest/b2b situational features.
    opp_store = None
    try:
        from services.features.opponent_context import build_opponent_context_store  # noqa: E402
        opp_store = build_opponent_context_store(db, SEASONS)
    except Exception as e:
        log.warning(f"opp_store unavailable, situational defaults will be used: {e}")

    X, y12, y8, sw = build_training_matrix(opp_store)
    if X is None:
        raise SystemExit("no samples")

    test_mask = sw >= 0.99
    train_mask = ~test_mask
    X_tr = X[train_mask]
    X_te = X[test_mask]
    w_tr = sw[train_mask]

    scaler = StandardScaler()
    X_tr_s = scaler.fit_transform(X_tr)
    X_te_s = scaler.transform(X_te)

    results = {}
    models = {}
    for label, y_full in (("low_12", y12), ("very_low_8", y8)):
        y_tr = y_full[train_mask]
        y_te = y_full[test_mask]
        # Class imbalance weight (positive class).
        pos = float(y_tr.sum())
        neg = float(len(y_tr) - pos)
        scale_pos_weight = neg / max(pos, 1.0)
        log.info(
            f"[{label}] train n={len(y_tr)} pos_rate={y_tr.mean():.3f} "
            f"scale_pos_weight={scale_pos_weight:.3f}"
        )
        clf = XGBClassifier(
            n_estimators=300, max_depth=5, learning_rate=0.07,
            subsample=0.85, colsample_bytree=0.85,
            reg_alpha=0.1, reg_lambda=1.0,
            random_state=42, verbosity=0, n_jobs=4, tree_method="hist",
            scale_pos_weight=scale_pos_weight,
            eval_metric="logloss",
        )
        t0 = time.monotonic()
        clf.fit(X_tr_s, y_tr, sample_weight=w_tr)
        train_seconds = time.monotonic() - t0
        p_te = clf.predict_proba(X_te_s)[:, 1]
        seg_metrics = _segmented_metrics(X_te, y_te, p_te, FEATURE_SCHEMA, label)
        seg_metrics["train_seconds"] = round(train_seconds, 2)
        seg_metrics["samples_train"] = int(len(y_tr))
        seg_metrics["samples_test"] = int(len(y_te))
        fi = sorted(
            [(FEATURE_SCHEMA[i], float(clf.feature_importances_[i]))
             for i in range(len(FEATURE_SCHEMA))],
            key=lambda x: -x[1],
        )
        seg_metrics["top_features"] = fi
        results[label] = seg_metrics
        models[label] = clf
        log.info(
            f"[{label}] AUC={seg_metrics['overall']['auc']}  "
            f"AP={seg_metrics['overall']['avg_precision']}  "
            f"brier={seg_metrics['overall']['brier']}  "
            f"train={train_seconds:.1f}s"
        )

    out_path = os.path.join(MODEL_DIR, "low_minutes_classifier.pkl")
    payload = {
        "model_low_12": models["low_12"],
        "model_very_low_8": models["very_low_8"],
        "scaler": scaler,
        "features": list(FEATURE_SCHEMA),
        "version": "NBA_LOW_MINUTES_CLASSIFIER_v1",
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "seasons_used": SEASONS,
        "season_weights": SEASON_WEIGHTS,
        "low_12_threshold": LOW_MIN_THRESHOLD,
        "very_low_8_threshold": VERY_LOW_MIN_THRESHOLD,
        "metrics": results,
    }
    with open(out_path, "wb") as f:
        pickle.dump(payload, f)
    with open(REPORT_PATH, "w") as f:
        json.dump(results, f, indent=2)
    log.info(f"saved model: {out_path}")
    log.info(f"saved report: {REPORT_PATH}")

    # Human-readable log summary
    log.info("=" * 68)
    log.info("CLASSIFIER QUALITY — low_12 (minutes <= 12)")
    log.info("=" * 68)
    ov = results["low_12"]["overall"]
    log.info(f"  AUC={ov['auc']}  AP={ov['avg_precision']}  brier={ov['brier']}")
    log.info(f"  pos_rate={ov['pos_rate']}  pred_mean={ov['pred_mean']}")
    log.info("  Thresholds:")
    for thr, m in results["low_12"]["thresholds"].items():
        log.info(f"    thr={thr}  P={m['precision']}  R={m['recall']}  F1={m['f1']}")
    log.info("  Segments:")
    for s, m in results["low_12"]["segments"].items():
        log.info(f"    {s:25s}  n={m['n']:>6d}  AUC={m['auc']}  "
                 f"pos_rate={m['pos_rate']}  pred_mean={m['pred_mean']}")
    log.info("  Top-5 features:")
    for n, v in results["low_12"]["top_features"][:5]:
        log.info(f"    {n:26s}  {v:.4f}")
    return payload


if __name__ == "__main__":
    t_all = time.monotonic()
    train()
    log.info(f"DONE in {time.monotonic() - t_all:.1f}s")
    client.close()

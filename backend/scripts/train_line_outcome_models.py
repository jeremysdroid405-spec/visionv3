"""Universal Line-Outcome Model trainer (v1, MLB home_runs only).

For every player game in mlb_historical_logs that passes the same
starter filter the ECDF training uses, generate the HF projection on
the pre-game state, then synthesize one feature row per (line) tuple
in the family's line grid. Fit a logistic regression with isotonic
calibration; report Brier + per-bucket reliability on a 20% holdout.

Features (v1, no-market — see services/probability/line_outcome.py):
  projection, line, line_distance, line_distance_ratio,
  hr_at_line, hr_sample_size, cv,
  avg_hit_margin, avg_miss_margin,
  hr_missing, margin_missing

Artifact written to:
  /app/backend/models/probability/lom/mlb/{stat_family}.pkl
"""
from __future__ import annotations

import argparse
import os
import pickle
import statistics
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import pymongo
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss

sys.path.insert(0, "/app/backend")
from services.mlb_high_friction_model import MLBHighFrictionModel  # noqa: E402

ARTIFACT_DIR = "/app/backend/models/probability/lom/mlb"
LINE_GRID: Dict[str, List[float]] = {
    "home_runs": [0.5, 1.5],
}
PITCHER_STATS = {
    "pitcher_strikeouts", "hits_allowed", "earned_runs", "pitcher_walks",
}
BATTER_AT_BAT_FLOOR = 2

FEATURE_COLS = [
    "projection", "line", "line_distance", "line_distance_ratio",
    "hr_at_line", "hr_sample_size", "cv",
    "avg_hit_margin", "avg_miss_margin",
    "hr_missing", "margin_missing",
]


def _hit_rate_at_line(values: List[Any], line: float
                      ) -> Tuple[Optional[float], int]:
    if len(values) >= 20:
        w = 20
    elif len(values) >= 10:
        w = 10
    else:
        return None, 0
    sel = values[:w]
    hits = sum(1 for v in sel if v is not None and v > line)
    return float(round((hits / w) * 100)), w


def _margins(values: List[Any], line: float
             ) -> Tuple[Optional[float], Optional[float]]:
    if len(values) >= 20:
        w = 20
    elif len(values) >= 10:
        w = 10
    else:
        return None, None
    sel = values[:w]
    h: List[float] = []
    m: List[float] = []
    for v in sel:
        if v is not None and v > line:
            h.append(float(v) - line)
        else:
            mv = float(v) if v is not None else 0.0
            m.append(line - mv)
    return (
        sum(h) / len(h) if h else None,
        sum(m) / len(m) if m else None,
    )


def _cv(values: List[Any]) -> Optional[float]:
    vals = [v for v in values[:20] if v is not None]
    if len(vals) < 5:
        return None
    mean = sum(vals) / len(vals)
    if mean == 0:
        return None
    sd = statistics.stdev(vals) if len(vals) > 1 else 0.0
    return sd / mean


def build_training_set(stat: str, hf: MLBHighFrictionModel,
                       max_players: Optional[int] = None) -> pd.DataFrame:
    norm_stat = hf._normalize_stat(stat)
    if norm_stat not in hf.models:
        raise SystemExit(f"HF model for {norm_stat} not loaded")
    model = hf.models[norm_stat]
    scaler = hf.scalers[norm_stat]
    feature_cols = hf.feature_cols[norm_stat]

    rows: List[Dict[str, Any]] = []
    cursor = hf.historical_logs.find({}, {"_id": 0})
    skipped = 0
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
            tg = game_logs[i]
            history = game_logs[i + 1:i + 31]
            target_value = hf._get_stat_value(tg, norm_stat)
            if target_value is None:
                skipped += 1
                continue
            if norm_stat in PITCHER_STATS:
                ip = tg.get("innings_pitched")
                try:
                    ip_f = float(ip) if ip is not None else 0.0
                except (TypeError, ValueError):
                    ip_f = 0.0
                if ip_f <= 0:
                    continue
            else:
                ab = tg.get("at_bats")
                try:
                    ab_f = float(ab) if ab is not None else 0.0
                except (TypeError, ValueError):
                    ab_f = 0.0
                if ab_f < BATTER_AT_BAT_FLOOR:
                    continue
            opponent = tg.get("opponent_abbr")
            feats = hf._build_friction_features(
                player_master, history, norm_stat,
                opponent=opponent,
                park_team=None, dk_odds=None, line=None,
            )
            if feats is None:
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
            sigma = float(feats.get("std_dev_l10") or 0.0) or 0.5
            hist_values = [hf._get_stat_value(g, norm_stat) for g in history]
            cv_val = _cv(hist_values)
            for line in LINE_GRID[stat]:
                hr_at, hr_n = _hit_rate_at_line(hist_values, line)
                avg_hit, avg_miss = _margins(hist_values, line)
                line_distance = float(yp) - float(line)
                rows.append({
                    "projection": float(yp),
                    "line": float(line),
                    "line_distance": line_distance,
                    "line_distance_ratio": line_distance / max(sigma, 0.1),
                    "hr_at_line": hr_at if hr_at is not None else 0.0,
                    "hr_sample_size": float(hr_n),
                    "cv": cv_val if cv_val is not None else 0.0,
                    "avg_hit_margin": avg_hit if avg_hit is not None else 0.0,
                    "avg_miss_margin": avg_miss if avg_miss is not None else 0.0,
                    "hr_missing": 0.0 if hr_at is not None else 1.0,
                    "margin_missing": 0.0 if avg_hit is not None else 1.0,
                    "label": 1 if target_value > line else 0,
                })
        if players_seen % 500 == 0:
            print(f"  walked {players_seen} players, rows={len(rows)}",
                  flush=True)
    return pd.DataFrame(rows)


def train_and_eval(df: pd.DataFrame) -> Dict[str, Any]:
    X = df[FEATURE_COLS].values
    y = df["label"].values
    rng = np.random.RandomState(42)
    idx = np.arange(len(df))
    rng.shuffle(idx)
    cut = int(len(df) * 0.8)
    train_idx, test_idx = idx[:cut], idx[cut:]

    base = LogisticRegression(max_iter=500, C=1.0)
    model = CalibratedClassifierCV(base, method="isotonic", cv=5)
    model.fit(X[train_idx], y[train_idx])
    y_pred = model.predict_proba(X[test_idx])[:, 1]
    brier = brier_score_loss(y[test_idx], y_pred)
    ll = log_loss(y[test_idx], y_pred, labels=[0, 1])

    bins = np.linspace(0, 1, 11)
    rel: List[Dict[str, Any]] = []
    for j in range(10):
        lo, hi = bins[j], bins[j + 1]
        mask = ((y_pred >= lo) & (y_pred < hi)) if j < 9 else (y_pred >= lo)
        n_in = int(mask.sum())
        if n_in > 0:
            rel.append({
                "lo": float(lo), "hi": float(hi), "n": n_in,
                "avg_pred": float(y_pred[mask].mean()),
                "actual_rate": float(y[test_idx][mask].mean()),
                "error": abs(
                    float(y_pred[mask].mean())
                    - float(y[test_idx][mask].mean())
                ),
            })
        else:
            rel.append({
                "lo": float(lo), "hi": float(hi), "n": 0,
                "avg_pred": None, "actual_rate": None, "error": None,
            })

    return {
        "model": model,
        "feature_cols": FEATURE_COLS,
        "n_rows": int(len(df)),
        "n_train": int(len(train_idx)),
        "n_test": int(len(test_idx)),
        "brier": float(brier),
        "log_loss": float(ll),
        "reliability": rel,
        "version": "v1-no-market",
        "trained_at": datetime.now(timezone.utc).isoformat(),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stat", default="home_runs")
    ap.add_argument("--max-players", type=int, default=None)
    args = ap.parse_args()

    t0 = time.monotonic()
    client = pymongo.MongoClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    hf = MLBHighFrictionModel(db)
    hf.load_models()

    print(f"=== Training MLB LOM stat={args.stat} ===")
    df = build_training_set(args.stat, hf, max_players=args.max_players)
    print(f"\nMatrix: rows={len(df)}  pos_rate={df['label'].mean():.4f}")
    if df.empty:
        raise SystemExit("empty matrix")

    art = train_and_eval(df)
    art["stat_family"] = args.stat
    art["sport"] = "mlb"

    print(f"\nBrier:   {art['brier']:.4f}")
    print(f"LogLoss: {art['log_loss']:.4f}")
    print(f"\nReliability buckets (holdout {art['n_test']:,}):")
    print(f'{"bucket":<14}{"n":<8}{"avg_pred":<10}{"actual":<10}{"err":<8}')
    for b in art["reliability"]:
        if b["n"] > 0:
            print(f'[{b["lo"]:.1f},{b["hi"]:.1f}]   {b["n"]:<8}'
                  f'{b["avg_pred"]:<10.3f}{b["actual_rate"]:<10.3f}'
                  f'{b["error"]:<8.3f}')
        else:
            print(f'[{b["lo"]:.1f},{b["hi"]:.1f}]   0       -         -         -')

    out = f"{ARTIFACT_DIR}/{args.stat}.pkl"
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "wb") as f:
        pickle.dump(art, f)
    print(f"\nSaved: {out}  ({os.path.getsize(out):,} B)  elapsed={time.monotonic()-t0:.1f}s")


if __name__ == "__main__":
    main()

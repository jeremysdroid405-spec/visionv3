"""
train_player_xgb.py — Player-prop XGB classifier trainer.

CONTRACT
    Mirrors train_team_xgb.py exactly. Trains one XGBClassifier per
    (sport, stat_family) using `player_model_prop_features` as input.

    Key difference from team trainer:
      - Partitioned by stat_family instead of market_category
      - Feature vector uses player rolling hit-rates + CV (not team priors)
      - stat_family is label-encoded as an ordinal feature
      - Chronological train/test split on game_date (80th-percentile cutoff)
        NOT a random split — prevents temporal leakage

INPUT
    `player_model_prop_features` (Phase 2B). Each resolved row carries
    hit_rate_l5/l10/l20, cv, avg_line, rest_days, sample_size,
    implied_probability, clean_odds, outcome_numeric (1=WIN, 0=LOSS).
    PUSH rows excluded from training.

ARTIFACT
    models/player_xgb/{sport}/{stat_family}.pkl
    Pickle dict:
        {
          model:          CalibratedClassifierCV (XGBClassifier inner)
          scaler:         StandardScaler
          features:       List[str]  (column order — must match scorer)
          stat_families:  List[str]  (label encoding order)
          version:        'player_xgb_v1'
          trained_at:     ISO timestamp
          sport, stat_family, samples, feature_count
          metrics: {auc_train/test, logloss_train/test, brier_test,
                    calibration_deciles, roi_by_tier}
        }

USAGE
    python -m scripts.sgo.train_player_xgb --sport nba --dry-run
    python -m scripts.sgo.train_player_xgb --sport nba
    python -m scripts.sgo.train_player_xgb --sport all
"""
from __future__ import annotations
import argparse
import asyncio
import math
import os
import pickle
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
for _env in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(_env):
        load_dotenv(_env)
        break

import numpy as np
from motor.motor_asyncio import AsyncIOMotorClient
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

from scripts.sgo.historical_full_pipeline_replay import _odds_bucket

VERSION = "player_xgb_v1"
ARTIFACT_ROOT = Path("/app/backend/models/player_xgb")
SUPPORTED_SPORTS = ("nba", "mlb")

# Ordered feature columns — must stay in sync with row_to_features().
FEATURE_COLS = [
    "hit_rate_l5",
    "hit_rate_l5_missing",
    "hit_rate_l10",
    "hit_rate_l10_missing",
    "hit_rate_l20",
    "hit_rate_l20_missing",
    "cv",
    "cv_missing",
    "avg_line",
    "avg_line_missing",
    "line",
    "line_missing",
    "implied_probability",
    "implied_probability_missing",
    "rest_days",
    "rest_days_missing",
    "sample_size",
    "is_over",
    "stat_family_encoded",
]


# ───── feature extraction ─────
def _f(v: Any, default: float = 0.0) -> float:
    if v is None or isinstance(v, bool):
        return float(default)
    try:
        x = float(v)
        return float(default) if (math.isnan(x) or math.isinf(x)) else x
    except (TypeError, ValueError):
        return float(default)


def _missing(v: Any) -> float:
    return 1.0 if v is None else 0.0


def row_to_features(
    row: Dict[str, Any],
    stat_family_index: Dict[str, int],
) -> List[float]:
    """Extract the fixed-length feature vector from one prop row."""
    sf = row.get("stat_family") or ""
    sf_enc = float(stat_family_index.get(sf, -1))

    line_raw = row.get("line")
    if line_raw is not None:
        try:
            line_raw = float(str(line_raw).strip())
        except (ValueError, TypeError):
            line_raw = None

    return [
        _f(row.get("hit_rate_l5")),
        _missing(row.get("hit_rate_l5")),
        _f(row.get("hit_rate_l10")),
        _missing(row.get("hit_rate_l10")),
        _f(row.get("hit_rate_l20")),
        _missing(row.get("hit_rate_l20")),
        _f(row.get("cv")),
        _missing(row.get("cv")),
        _f(row.get("avg_line")),
        _missing(row.get("avg_line")),
        _f(line_raw),
        _missing(line_raw),
        _f(row.get("implied_probability")),
        _missing(row.get("implied_probability")),
        _f(row.get("rest_days")),
        _missing(row.get("rest_days")),
        _f(row.get("sample_size"), default=0.0),
        1.0 if (row.get("side") or "").upper() == "OVER" else 0.0,
        sf_enc,
    ]


# ───── ROI helpers (mirrors train_team_xgb.py) ─────
def _american_to_decimal(odds: float) -> float:
    if odds >= 0:
        return 1.0 + odds / 100.0
    return 1.0 + 100.0 / abs(odds)


def _tier_for_odds_bucket(bucket: str) -> str:
    if bucket in ("odds_-200_-100", "odds_lt_-200"):
        return "safe_haven"
    if bucket in ("odds_+150_+300", "odds_+300p"):
        return "war_zone"
    return "front_lines"


def compute_roi_breakdowns(
    clean_odds_list: List[Optional[int]],
    outcomes: List[int],
    picks: List[int],
) -> Dict[str, Any]:
    by_tier: Dict[str, Dict[str, float]] = defaultdict(
        lambda: {"n": 0, "wins": 0, "profit": 0.0})
    for co, y, p in zip(clean_odds_list, outcomes, picks):
        if not p or co is None:
            continue
        dec = _american_to_decimal(float(co))
        profit = (dec - 1.0) if y == 1 else -1.0
        bucket = _odds_bucket(int(co))
        tier = _tier_for_odds_bucket(bucket)
        cell = by_tier[tier]
        cell["n"] += 1
        cell["wins"] += int(y == 1)
        cell["profit"] += profit
    return {
        "by_tier": {
            t: {"n": int(c["n"]), "wins": int(c["wins"]),
                "hit_rate": round(c["wins"] / c["n"], 4) if c["n"] else None,
                "roi": round(c["profit"] / c["n"], 4) if c["n"] else None}
            for t, c in by_tier.items()
        }
    }


def calibration_deciles(
    y_true: np.ndarray, y_prob: np.ndarray,
) -> List[Dict[str, Any]]:
    if len(y_true) == 0:
        return []
    order = np.argsort(y_prob)
    n = len(y_true)
    bucket_size = max(1, n // 10)
    out: List[Dict[str, Any]] = []
    for i in range(10):
        s = i * bucket_size
        e = (i + 1) * bucket_size if i < 9 else n
        idx = order[s:e]
        if len(idx) == 0:
            continue
        out.append({
            "decile": i + 1,
            "n": int(len(idx)),
            "mean_pred": round(float(y_prob[idx].mean()), 4),
            "mean_actual": round(float(y_true[idx].mean()), 4),
        })
    return out


# ───── data loader ─────
async def load_training_rows(
    db, *, sport: str, stat_family: str, max_rows: int,
) -> List[Dict[str, Any]]:
    """Pull all resolved non-push rows for one (sport, stat_family).

    Uses a chronological cutoff (game_date >= '2024-01-01' for NBA,
    '2025-01-01' for MLB) to align with team trainer conventions.
    """
    match = {
        "sport": sport,
        "stat_family": stat_family,
        "outcome_resolved": True,
        "push": {"$ne": True},
        "outcome_numeric": {"$in": [0, 1]},
        "implied_probability": {"$ne": None},
        "game_date": {"$gte": "2024-01-01" if sport == "nba" else "2025-01-01"},
    }
    proj = {
        "_id": 0,
        "hit_rate_l5": 1, "hit_rate_l10": 1, "hit_rate_l20": 1,
        "cv": 1, "avg_line": 1, "line": 1,
        "implied_probability": 1, "clean_odds": 1,
        "rest_days": 1, "sample_size": 1,
        "side": 1, "stat_family": 1, "game_date": 1,
        "outcome_numeric": 1,
    }
    cur = db["sgo_propvision_full_pipeline_replay"].find(match, proj).limit(max_rows)
    return [d async for d in cur]


# ───── chronological split ─────
def chrono_split(
    rows: List[Dict[str, Any]], *, train_frac: float = 0.8,
) -> int:
    """Return the split index for chronological train/test separation.

    Sorts rows by game_date and cuts at the 80th-percentile date.
    This guarantees that training uses only past data and the test
    holdout is genuinely out-of-sample in time."""
    dates = sorted(set(r.get("game_date") or "" for r in rows if r.get("game_date")))
    if not dates:
        return int(len(rows) * train_frac)
    cutoff_date = dates[int(len(dates) * train_frac)]
    split_idx = sum(1 for r in rows if (r.get("game_date") or "") < cutoff_date)
    return max(1, min(split_idx, len(rows) - 1))


# ───── trainer ─────
def _train_one(
    rows: List[Dict[str, Any]],
    *,
    sport: str,
    stat_family: str,
    seed: int = 42,
) -> Dict[str, Any]:
    """Train + calibrate XGBClassifier. Returns metrics dict.

    Uses chronological split, NOT random shuffle, to prevent temporal
    leakage. Rows are sorted by game_date before splitting."""
    if len(rows) < 200:
        return {"ok": False, "reason": f"too few rows: {len(rows)} (<200)"}

    # Build stat_family label encoding from all families present in dataset
    families_sorted = sorted(set(r.get("stat_family") or "" for r in rows))
    stat_family_index = {sf: i for i, sf in enumerate(families_sorted)}

    rows_sorted = sorted(rows, key=lambda r: r.get("game_date") or "")
    split = chrono_split(rows_sorted)
    train_rows = rows_sorted[:split]
    test_rows = rows_sorted[split:]

    if len(train_rows) < 100 or len(test_rows) < 50:
        return {"ok": False,
                "reason": f"split too small: train={len(train_rows)} test={len(test_rows)}"}

    X_tr = np.array([row_to_features(r, stat_family_index) for r in train_rows],
                    dtype=np.float64)
    X_te = np.array([row_to_features(r, stat_family_index) for r in test_rows],
                    dtype=np.float64)
    y_tr = np.array([int(r["outcome_numeric"]) for r in train_rows], dtype=np.int64)
    y_te = np.array([int(r["outcome_numeric"]) for r in test_rows], dtype=np.int64)
    co_te = [r.get("clean_odds") for r in test_rows]

    scaler = StandardScaler().fit(X_tr)
    X_tr_s = scaler.transform(X_tr)
    X_te_s = scaler.transform(X_te)

    n_rows = len(X_tr)
    if n_rows < 30000:
        _params = dict(n_estimators=200, max_depth=2, learning_rate=0.02,
                       subsample=0.7, colsample_bytree=0.5,
                       min_child_weight=20, gamma=1.0,
                       reg_alpha=1.0, reg_lambda=5.0)
    else:
        _params = dict(n_estimators=150, max_depth=3, learning_rate=0.03,
                       subsample=0.6, colsample_bytree=0.6,
                       min_child_weight=50, gamma=2.0,
                       reg_alpha=2.0, reg_lambda=10.0)

    inner = XGBClassifier(
        **_params,
        random_state=seed, verbosity=0,
        objective="binary:logistic",
        eval_metric="logloss",
        tree_method="hist",
    )
    cal_method = "isotonic" if len(X_tr) >= 20000 else "sigmoid"
    model = CalibratedClassifierCV(inner, method=cal_method, cv=5)
    model.fit(X_tr_s, y_tr)

    p_tr = model.predict_proba(X_tr_s)[:, 1]
    p_te = model.predict_proba(X_te_s)[:, 1]

    try:
        auc_tr = float(roc_auc_score(y_tr, p_tr))
    except ValueError:
        auc_tr = None
    try:
        auc_te = float(roc_auc_score(y_te, p_te))
    except ValueError:
        auc_te = None

    if auc_te is not None and auc_te > 0.75:
        print(f"  WARNING: AUC_test={auc_te:.4f} > 0.75 — model likely overfit")

    ll_tr = float(log_loss(y_tr, p_tr, labels=[0, 1]))
    ll_te = float(log_loss(y_te, p_te, labels=[0, 1]))
    brier_te = float(brier_score_loss(y_te, p_te))
    cal = calibration_deciles(y_te, p_te)
    roi = compute_roi_breakdowns(
        co_te, y_te.tolist(),
        picks=[1 if prob >= 0.55 else 0 for prob in p_te],
    )

    return {
        "ok":             True,
        "model":          model,
        "scaler":         scaler,
        "features":       FEATURE_COLS,
        "stat_families":  families_sorted,
        "version":        VERSION,
        "trained_at":     datetime.now(timezone.utc).isoformat(),
        "sport":          sport,
        "stat_family":    stat_family,
        "samples":        len(rows),
        "train_n":        len(train_rows),
        "test_n":         len(test_rows),
        "feature_count":  len(FEATURE_COLS),
        "metrics": {
            "auc_train":          round(auc_tr, 4) if auc_tr is not None else None,
            "auc_test":           round(auc_te, 4) if auc_te is not None else None,
            "logloss_train":      round(ll_tr, 5),
            "logloss_test":       round(ll_te, 5),
            "brier_test":         round(brier_te, 5),
            "calibration_deciles": cal,
            "roi":                roi,
        },
    }


def save_artifact(payload: Dict[str, Any]) -> Path:
    sport = payload["sport"]
    sf = payload["stat_family"].replace(" ", "_").replace("/", "_")
    out_dir = ARTIFACT_ROOT / sport
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{sf}.pkl"
    with path.open("wb") as f:
        pickle.dump(payload, f)
    return path


# ───── orchestrator ─────
async def train_for_sport(
    db,
    *,
    sport: str,
    dry_run: bool,
    max_rows_per_family: int,
    stat_families: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    if stat_families is None:
        raw = await db["sgo_propvision_full_pipeline_replay"].distinct(
            "stat_family", {"sport": sport})
        stat_families = sorted([s for s in raw if s])

    print(f"\n  [{sport.upper()}] stat_families to train: {stat_families}")
    reports: List[Dict[str, Any]] = []

    for sf in stat_families:
        print(f"\n  [{sport.upper()}/{sf}] loading rows…")
        rows = await load_training_rows(
            db, sport=sport, stat_family=sf,
            max_rows=max_rows_per_family)
        print(f"  [{sport.upper()}/{sf}] rows loaded: {len(rows):,}")
        if len(rows) < 200:
            print(f"  [{sport.upper()}/{sf}] SKIP — too few rows.")
            reports.append({"sport": sport, "stat_family": sf,
                            "ok": False,
                            "reason": f"too few rows ({len(rows)})"})
            continue

        r = _train_one(rows, sport=sport, stat_family=sf)
        if not r["ok"]:
            print(f"  [{sport.upper()}/{sf}] SKIP — {r.get('reason')}")
            reports.append({"sport": sport, "stat_family": sf, **r})
            continue

        if dry_run:
            print(f"  [{sport.upper()}/{sf}] DRY-RUN — not saving artifact.")
        else:
            path = save_artifact(r)
            print(f"  [{sport.upper()}/{sf}] saved → {path}")

        m = r["metrics"]
        print(f"  [{sport.upper()}/{sf}] "
              f"train={r['train_n']:,}  test={r['test_n']:,}  "
              f"AUC_te={m['auc_test']}  LL_te={m['logloss_test']}  "
              f"Brier={m['brier_test']}")
        for t, c in m["roi"]["by_tier"].items():
            print(f"    tier={t:<11s} n={c['n']:>5}  "
                  f"hit_rate={c['hit_rate']!s:<8}  roi={c['roi']!s}")

        reports.append({
            "sport": sport, "stat_family": sf,
            "ok": True, "samples": r["samples"],
            "metrics": m,
            "artifact": (None if dry_run
                         else str(ARTIFACT_ROOT / sport / f"{sf}.pkl")),
        })
    return reports


async def amain(args: argparse.Namespace) -> int:
    sports = [args.sport] if args.sport != "all" else list(SUPPORTED_SPORTS)
    stat_families = args.stat_families or None

    print(f"[{datetime.now(timezone.utc).isoformat()}] "
          f"train_player_xgb  version={VERSION}")
    print(f"  sports={sports}  dry_run={args.dry_run}  "
          f"max_rows_per_family={args.max_rows_per_family}")

    mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = mongo[os.environ["DB_NAME"]]
    try:
        for sp in sports:
            await train_for_sport(
                db, sport=sp, dry_run=args.dry_run,
                max_rows_per_family=args.max_rows_per_family,
                stat_families=stat_families)
    finally:
        mongo.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sport", choices=list(SUPPORTED_SPORTS) + ["all"],
                   default="all")
    p.add_argument("--dry-run", action="store_true",
                   help="Train + report metrics but do NOT write the .pkl artifact.")
    p.add_argument("--max-rows-per-family", type=int, default=500_000)
    p.add_argument("--stat-families", nargs="+", default=None,
                   help="Specific stat families to train (default: all in collection).")
    return asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    rc = main()
    from scripts.sgo.handoff import update_handoff
    update_handoff()
    raise SystemExit(rc)

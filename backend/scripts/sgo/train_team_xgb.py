"""
train_team_xgb.py — Team-prop XGB classifier trainer.

CONTRACT
    Mirrors the player retrain pattern (`scripts/retrain_mlb_models_v2.py`):
        XGB model + scaler + features + version saved in one pickle.
    Mirrors the player LOM scaling pattern (`models/probability/lom/`):
        One artifact per (sport, market_category). Calibration via
        isotonic regression on a holdout split.

INPUT
    `team_model_prop_features` (Phase 2B). Each resolved row carries
    team_features + opponent_features priors + bet shape +
    outcome_numeric (1=WIN, 0=LOSS). PUSH rows excluded from training.

ARTIFACT
    /app/backend/models/team_xgb/<sport>/<market_category>.pkl
    Pickle dict:
        {
          model:         CalibratedClassifierCV (XGBClassifier inner)
          scaler:        StandardScaler
          features:      List[str]  (column order — must match scorer)
          version:       'team_xgb_v1'
          trained_at:    ISO timestamp
          sport, market_category
          samples, feature_count
          metrics: {auc_train/test, logloss_train/test, brier_test,
                    calibration_deciles, roi_by_tier, roi_by_odds_bucket}
        }

ROI METRICS
    Computed on the test holdout. For each row, picks the side and
    grades vs `outcome_numeric`. Net units assuming $1 stake at the
    row's `odds`:
        win  → +decimal_payout - 1
        loss → -1
    ROI = sum(profit) / n_bets.

USAGE
    python -m scripts.sgo.train_team_xgb --sport mlb --dry-run
    python -m scripts.sgo.train_team_xgb --sport all
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
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, "/var/www/app/backend")
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
for env_path in ("/var/www/app/backend/.env", "/app/backend/.env"):
    if os.path.exists(env_path):
        load_dotenv(env_path)
        break

import numpy as np
from motor.motor_asyncio import AsyncIOMotorClient
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import (brier_score_loss, log_loss, roc_auc_score)
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier, XGBRegressor

# Reuse the same odds-bucket helper player code uses — guarantees
# bucket parity across player + team rows in the replay collection.
from scripts.sgo.historical_full_pipeline_replay import _odds_bucket

VERSION = "team_xgb_v1"
ARTIFACT_ROOT = Path("/app/backend/models/team_xgb")
SUPPORTED_SPORTS = ("mlb", "nba", "nfl")

_BASE_PRIOR_FIELDS: Tuple[str, ...] = (
    "sample_size", "mu_points_scored", "sigma_points_scored", "cv_points_scored",
    "win_rate_l5", "win_rate_l10", "win_rate_season",
    "avg_scored_l5", "avg_scored_l10", "avg_scored_season",
    "avg_allowed_l5", "avg_allowed_l10", "avg_allowed_season",
    "spread_cover_rate_l10", "spread_cover_rate_l5",
    "ou_hit_rate_l10", "ou_hit_rate_l5",
    "home_win_rate", "away_win_rate",
    "rest_days", "tempo_l10", "run_trend_l10",
    "sp_k_rate_avg", "sp_woba_allowed_avg", "sp_hard_hit_rate_avg",
    "sp_bb_rate_avg", "sp_xwoba_allowed_avg",
)

_BDL_FIELDS: Tuple[str, ...] = (
    "batting_avg", "batting_obp", "batting_slg", "batting_ops",
    "batting_k_rate", "batting_bb_rate", "batting_hr_rate",
    "pitching_era", "pitching_whip", "pitching_k_per_9",
    "pitching_bb_per_9", "pitching_hr_per_9", "pitching_qs_rate",
    "fielding_fp",
    "runs_scored_l5_bdl", "runs_scored_l10_bdl", "runs_scored_l20_bdl",
    "runs_allowed_l5_bdl", "runs_allowed_l10_bdl", "runs_allowed_l20_bdl",
    "first5_runs_l5", "first5_runs_l10", "first5_runs_l20",
    "first_inning_score_rate", "total_runs_l10_avg",
)

_LINE_SPREAD_FIELDS: Tuple[str, ...] = (
    "spread_cover_neg1_5_l5", "spread_cover_neg1_5_l10",
    "spread_cover_1_5_l5",    "spread_cover_1_5_l10",
    "spread_cover_neg2_5_l5", "spread_cover_neg2_5_l10",
    "spread_cover_2_5_l5",    "spread_cover_2_5_l10",
    "spread_cover_neg3_5_l5", "spread_cover_neg3_5_l10",
    "spread_cover_3_5_l5",    "spread_cover_3_5_l10",
)

_NBA_BDL_FIELDS: Tuple[str, ...] = (
    "off_rating_l5", "off_rating_l10", "off_rating_l20",
    "def_rating_l5", "def_rating_l10", "def_rating_l20",
    "net_rating_l5", "net_rating_l10", "net_rating_l20",
    "pace_l5", "pace_l10", "pace_l20",
    "ts_pct_l10", "fg_pct_l10", "fg3_pct_l10",
    "reb_pct_l10", "ast_pct_l10",
    "pie_l10", "ft_pct_l10",
    "ast_l5", "ast_l10",
    "blk_l10", "stl_l10", "tov_l10", "tov_rate_l10",
    "first_half_scored_l5", "first_half_scored_l10", "first_half_scored_l20",
    "is_back_to_back", "games_in_last_7",
)

_BET_SHAPE_FIELDS: Tuple[str, ...] = (
    "line", "is_alternate_int", "is_home_int", "is_over_int",
)

_DIFF_FIELDS: Tuple[str, ...] = (
    "team_minus_opp_mu", "team_minus_opp_win_l10", "team_minus_opp_tempo_l10",
)


# ───── feature extraction (unit-testable) ─────
def _f(v: Any, default: float = 0.0) -> float:
    """Coerce to float. None / non-numeric → default. Pure helper."""
    if v is None or isinstance(v, bool):
        return float(default)
    try:
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return float(default)
        return x
    except (TypeError, ValueError):
        return float(default)


def _missing_flag(v: Any) -> float:
    return 1.0 if v is None else 0.0


_NBA_BASE_FIELDS: Tuple[str, ...] = (
    "sample_size", "mu_points_scored", "sigma_points_scored", "cv_points_scored",
    "win_rate_l5", "win_rate_l10", "win_rate_season",
    "avg_scored_l5", "avg_scored_l10", "avg_scored_season",
    "avg_allowed_l5", "avg_allowed_l10", "avg_allowed_season",
    "home_win_rate", "away_win_rate",
    "rest_days", "run_trend_l10",
)

def get_prior_fields(sport: str) -> Tuple[str, ...]:
    """Return the ordered list of per-team prior fields for the given sport."""
    if sport == "nba":
        return tuple(list(_NBA_BASE_FIELDS) + list(_NBA_BDL_FIELDS))
    fields: List[str] = list(_BASE_PRIOR_FIELDS)
    if sport == "mlb":
        fields.extend(_BDL_FIELDS)
        fields.extend(_LINE_SPREAD_FIELDS)
    return tuple(fields)


def feature_columns(sport: str) -> List[str]:
    """Canonical column order — must stay in sync with row_to_features."""
    cols: List[str] = []
    for f in get_prior_fields(sport):
        cols.append(f"team_{f}")
        cols.append(f"team_{f}_missing")
        cols.append(f"opp_{f}")
        cols.append(f"opp_{f}_missing")
    cols.extend(_BET_SHAPE_FIELDS)
    cols.extend(_DIFF_FIELDS)
    cols.append("combined_pace")
    cols.append("combined_pace_missing")
    cols.append("off_rating_diff")
    cols.append("off_rating_diff_missing")
    cols.append("net_rating_diff")
    cols.append("net_rating_diff_missing")
    cols.append("pace_diff")
    cols.append("pace_diff_missing")
    return cols


def row_to_features(row: Dict[str, Any], sport: str) -> List[float]:
    tf = row.get("team_features") or {}
    of = row.get("opponent_features") or {}
    vec: List[float] = []
    for f in get_prior_fields(sport):
        tv = tf.get(f)
        ov = of.get(f)
        vec.append(_f(tv))
        vec.append(_missing_flag(tv))
        vec.append(_f(ov))
        vec.append(_missing_flag(ov))
    # bet shape
    vec.append(_f(row.get("line")))
    vec.append(1.0 if row.get("is_alternate") else 0.0)
    vec.append(1.0 if (row.get("home_away") == "home") else 0.0)
    vec.append(1.0 if (row.get("side") == "over") else 0.0)
    # differential signals
    vec.append(_f(tf.get("mu_points_scored")) - _f(of.get("mu_points_scored")))
    vec.append(_f(tf.get("win_rate_l10")) - _f(of.get("win_rate_l10")))
    vec.append(_f(tf.get("tempo_l10")) - _f(of.get("tempo_l10")))
    # combined pace
    cp = row.get("combined_pace")
    vec.append(_f(cp))
    vec.append(_missing_flag(cp))
    # NBA h2h differential features (stored on row by build_team_prop_features)
    _off_diff = row.get("off_rating_diff")
    vec.append(_f(_off_diff))
    vec.append(_missing_flag(_off_diff))
    _net_diff = row.get("net_rating_diff")
    vec.append(_f(_net_diff))
    vec.append(_missing_flag(_net_diff))
    _pace_diff = row.get("pace_diff")
    vec.append(_f(_pace_diff))
    vec.append(_missing_flag(_pace_diff))
    return vec


# ───── ROI metric helpers ─────
def _american_to_decimal(odds: float) -> float:
    """American → Decimal. Pure."""
    if odds >= 0:
        return 1.0 + odds / 100.0
    return 1.0 + 100.0 / abs(odds)


def _american_implied_prob(odds: float) -> float:
    if odds >= 0:
        return 100.0 / (odds + 100.0)
    return abs(odds) / (abs(odds) + 100.0)


def _tier_for_odds_bucket(bucket: str) -> str:
    """Mirrors `_tier_odds_filter` in routes/emergent_admin/optimizer.py.
    Pure function — kept here so the trainer's ROI breakdown matches
    the optimizer's tier semantics exactly."""
    if bucket in ("odds_-200_-100", "odds_lt_-200"):
        return "safe_haven"
    if bucket in ("odds_+150_+300", "odds_+300p"):
        return "war_zone"
    return "front_lines"   # +0_+150, -100_-0


def compute_roi_breakdowns(
    odds_list: List[float], outcomes: List[int], picks: List[int],
) -> Dict[str, Any]:
    """`picks` is 0 (lay/skip) or 1 (place the side as-bet). For team
    rows we always pick the as-bet side. ROI in units per $1 stake.
    Pure function."""
    by_tier: Dict[str, Dict[str, float]] = defaultdict(
        lambda: {"n": 0, "wins": 0, "profit": 0.0})
    by_bucket: Dict[str, Dict[str, float]] = defaultdict(
        lambda: {"n": 0, "wins": 0, "profit": 0.0})
    for o, y, p in zip(odds_list, outcomes, picks):
        if not p:
            continue
        dec = _american_to_decimal(o)
        profit = (dec - 1.0) if y == 1 else -1.0
        bucket = _odds_bucket(int(o))
        tier = _tier_for_odds_bucket(bucket)
        for cell in (by_tier[tier], by_bucket[bucket]):
            cell["n"] += 1
            cell["wins"] += int(y == 1)
            cell["profit"] += profit
    return {
        "by_tier": {
            t: {"n": int(c["n"]), "wins": int(c["wins"]),
                  "hit_rate": round(c["wins"] / c["n"], 4) if c["n"] else None,
                  "roi": round(c["profit"] / c["n"], 4) if c["n"] else None}
            for t, c in by_tier.items()
        },
        "by_odds_bucket": {
            b: {"n": int(c["n"]), "wins": int(c["wins"]),
                  "hit_rate": round(c["wins"] / c["n"], 4) if c["n"] else None,
                  "roi": round(c["profit"] / c["n"], 4) if c["n"] else None}
            for b, c in by_bucket.items()
        },
    }


def calibration_deciles(y_true: np.ndarray, y_prob: np.ndarray
                          ) -> List[Dict[str, Any]]:
    """Reliability diagram in 10 equal-population buckets."""
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
            "mean_pred":  round(float(y_prob[idx].mean()), 4),
            "mean_actual": round(float(y_true[idx].mean()), 4),
        })
    return out


# ───── data loader ─────
async def load_training_rows(
    db, *, sport: str, market_category: str, max_rows: int,
) -> List[Dict[str, Any]]:
    """Pull resolved (non-push) rows for one (sport, market_category)."""
    match = {
        "sport": sport,
        "market_category": market_category,
        "outcome_resolved": True,
        "push": False,
        "outcome_numeric": {"$in": [0, 1]},
        "odds": {"$ne": None},
        "game_date": {"$gte": "2024-01-01" if sport == "nba" else "2025-01-01"},
        "implied_probability": {"$ne": None},
    }
    if market_category in ("game_total", "team_total"):
        match["side"] = "OVER"
    if market_category == "h2h" and sport == "mlb":
        match["home_away"] = "home"
    proj = {
        "_id": 0,
        "team_features": 1, "opponent_features": 1,
        "line": 1, "odds": 1, "is_alternate": 1,
        "home_away": 1, "side": 1, "implied_probability": 1,
        "outcome_numeric": 1,
        "home_score_used": 1, "away_score_used": 1,
        "event_id": 1, "team_id": 1, "game_date": 1,
        "combined_pace": 1,
    }
    cur = db["team_model_prop_features"].find(match, proj).limit(max_rows)
    rows = [d async for d in cur]
    return rows


# ───── trainer ─────
def _train_one(
    rows: List[Dict[str, Any]], *, sport: str, market_category: str,
    seed: int = 42,
) -> Dict[str, Any]:
    """Train + calibrate XGBClassifier + emit metrics. Pure-ish (no IO).
    `rows` is the resolved row payload from `load_training_rows`."""
    if len(rows) < 200:
        return {"ok": False, "reason":
                f"too few rows: {len(rows)} (<200)"}
    cols = feature_columns(sport)
    X = np.array([row_to_features(r, sport) for r in rows], dtype=np.float64)
    y = np.array([int(r["outcome_numeric"]) for r in rows], dtype=np.int64)
    odds_arr = np.array([float(r["odds"]) for r in rows], dtype=np.float64)
    rng = np.random.RandomState(seed)
    perm = rng.permutation(len(X))
    # Extract raw scores in permuted order BEFORE the arrays are sliced.
    raw_home = [rows[i].get("home_score_used") for i in perm]
    raw_away = [rows[i].get("away_score_used") for i in perm]
    home_scores_all = np.array(
        [float(v) if v is not None else np.nan for v in raw_home],
        dtype=np.float64)
    away_scores_all = np.array(
        [float(v) if v is not None else np.nan for v in raw_away],
        dtype=np.float64)
    X = X[perm]
    y = y[perm]
    odds_arr = odds_arr[perm]
    split = int(len(X) * 0.8)
    X_tr, X_te = X[:split], X[split:]
    y_tr, y_te = y[:split], y[split:]
    odds_te = odds_arr[split:]

    scaler = StandardScaler().fit(X_tr)
    X_tr_s = scaler.transform(X_tr)
    X_te_s = scaler.transform(X_te)

    # Regularization scaled to dataset size.
    # Large datasets (MLB ~300K): heavy regularization to prevent memorization.
    # Small datasets (NBA ~16K): lighter regularization to allow learning.
    n_rows = len(X_tr)
    if n_rows < 30000:
        # Small dataset — shallower trees, moderate regularization
        _params = dict(n_estimators=200, max_depth=2, learning_rate=0.02,
                       subsample=0.7, colsample_bytree=0.5,
                       min_child_weight=20, gamma=1.0,
                       reg_alpha=1.0, reg_lambda=5.0)
    else:
        # Large dataset — heavy regularization
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
        print(f"  WARNING: AUC_test={auc_te:.4f} > 0.75 — model likely overfit for sports data")
    out_of_range = float(np.mean((p_te < 0.35) | (p_te > 0.65)))
    if out_of_range > 0.10:
        print(f"  WARNING: {out_of_range:.1%} of test predictions are outside [0.35, 0.65] — check calibration")
    ll_tr = float(log_loss(y_tr, p_tr, labels=[0, 1]))
    ll_te = float(log_loss(y_te, p_te, labels=[0, 1]))
    brier_te = float(brier_score_loss(y_te, p_te))
    cal = calibration_deciles(y_te, p_te)
    roi = compute_roi_breakdowns(
        odds_te.tolist(), y_te.tolist(),
        picks=[1 if p >= 0.55 else 0 for p in p_te],   # only grade high-confidence predictions
    )

    # ── Score regressors ──────────────────────────────────────────────
    # Train one XGBRegressor each for home_score and away_score. Uses
    # the same scaled X_tr_s / X_te_s so the scaler is shared. Rows
    # where either score is NaN are excluded from regressor training.
    reg_mask_tr = ~np.isnan(home_scores_all[:split]) & ~np.isnan(away_scores_all[:split])
    reg_mask_te = ~np.isnan(home_scores_all[split:]) & ~np.isnan(away_scores_all[split:])
    regressor_home = None
    regressor_away = None
    mae_home_te = None
    mae_away_te = None
    if reg_mask_tr.sum() >= 50:
        X_reg_tr = X_tr_s[reg_mask_tr]
        y_home_tr = home_scores_all[:split][reg_mask_tr]
        y_away_tr = away_scores_all[:split][reg_mask_tr]
        _reg_kwargs = dict(
            n_estimators=200, max_depth=4, learning_rate=0.08,
            subsample=0.8, colsample_bytree=0.8,
            random_state=seed, verbosity=0, tree_method="hist",
        )
        regressor_home = XGBRegressor(**_reg_kwargs)
        regressor_home.fit(X_reg_tr, y_home_tr)
        regressor_away = XGBRegressor(**_reg_kwargs)
        regressor_away.fit(X_reg_tr, y_away_tr)
        X_reg_te = X_te_s[reg_mask_te]
        if len(X_reg_te) > 0:
            mae_home_te = float(np.mean(
                np.abs(regressor_home.predict(X_reg_te) - home_scores_all[split:][reg_mask_te])))
            mae_away_te = float(np.mean(
                np.abs(regressor_away.predict(X_reg_te) - away_scores_all[split:][reg_mask_te])))
        print(f"    regressors trained on {int(reg_mask_tr.sum())} rows  "
              f"mae_home={mae_home_te!r}  mae_away={mae_away_te!r}")

    return {
        "ok": True,
        "model": model,
        "scaler": scaler,
        "features": cols,
        "regressor_home": regressor_home,
        "regressor_away": regressor_away,
        "version": VERSION,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "sport": sport,
        "market_category": market_category,
        "samples": int(len(X)),
        "feature_count": int(len(cols)),
        "metrics": {
            "auc_train": round(auc_tr, 4) if auc_tr is not None else None,
            "auc_test":  round(auc_te, 4) if auc_te is not None else None,
            "logloss_train": round(ll_tr, 5),
            "logloss_test":  round(ll_te, 5),
            "brier_test":    round(brier_te, 5),
            "calibration_deciles": cal,
            "roi": roi,
            "test_n_bets": int(len(y_te)),
            "mae_home_test": round(mae_home_te, 3) if mae_home_te is not None else None,
            "mae_away_test": round(mae_away_te, 3) if mae_away_te is not None else None,
        },
    }


def save_artifact(payload: Dict[str, Any]) -> Path:
    sport = payload["sport"]
    mc = payload["market_category"]
    out_dir = ARTIFACT_ROOT / sport
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{mc}.pkl"
    with path.open("wb") as f:
        pickle.dump(payload, f)
    return path


# ───── orchestrator ─────
async def train_for_sport(
    db, *, sport: str, dry_run: bool, max_rows_per_market: int,
    market_categories: List[str],
) -> List[Dict[str, Any]]:
    reports: List[Dict[str, Any]] = []
    for mc in market_categories:
        print(f"\n  [{sport.upper()}/{mc}] loading rows…")
        rows = await load_training_rows(
            db, sport=sport, market_category=mc,
            max_rows=max_rows_per_market)
        print(f"  [{sport.upper()}/{mc}] resolved rows: {len(rows):,}")
        if len(rows) < 200:
            print(f"  [{sport.upper()}/{mc}] SKIP — too few rows.")
            reports.append({"sport": sport, "market_category": mc,
                              "ok": False,
                              "reason": f"too few rows ({len(rows)})"})
            continue
        r = _train_one(rows, sport=sport, market_category=mc)
        if not r["ok"]:
            print(f"  [{sport.upper()}/{mc}] SKIP — {r.get('reason')}")
            reports.append({"sport": sport, "market_category": mc, **r})
            continue
        if dry_run:
            print(f"  [{sport.upper()}/{mc}] DRY-RUN — not saving artifact.")
        else:
            path = save_artifact(r)
            print(f"  [{sport.upper()}/{mc}] saved → {path}")
        m = r["metrics"]
        print(f"  [{sport.upper()}/{mc}] samples={r['samples']:,}  "
              f"AUC_te={m['auc_test']}  LL_te={m['logloss_test']}  "
              f"Brier={m['brier_test']}  test_n={m['test_n_bets']}")
        for t, c in (m["roi"]["by_tier"]).items():
            print(f"    tier={t:<11s} n={c['n']:>6}  "
                  f"hit_rate={c['hit_rate']!s:<8}  roi={c['roi']!s}")
        reports.append({"sport": sport, "market_category": mc,
                          "ok": True, "samples": r["samples"],
                          "metrics": m,
                          "artifact": (None if dry_run
                                          else str(ARTIFACT_ROOT / sport / f"{mc}.pkl"))})
    return reports


async def amain(args: argparse.Namespace) -> int:
    sports = [args.sport] if args.sport != "all" else list(SUPPORTED_SPORTS)
    markets = args.market_categories
    print(f"[{datetime.now(timezone.utc).isoformat()}] "
          f"train_team_xgb  version={VERSION}")
    print(f"  sports={sports}  dry_run={args.dry_run}  "
          f"max_rows_per_market={args.max_rows_per_market}  "
          f"markets={markets}")
    mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = mongo[os.environ["DB_NAME"]]
    try:
        for sp in sports:
            await train_for_sport(
                db, sport=sp, dry_run=args.dry_run,
                max_rows_per_market=args.max_rows_per_market,
                market_categories=markets)
    finally:
        mongo.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sport", choices=list(SUPPORTED_SPORTS) + ["all"],
                    default="all")
    p.add_argument("--dry-run", action="store_true",
                    help="Train + report metrics but do NOT write the "
                         ".pkl artifact.")
    p.add_argument("--max-rows-per-market", type=int, default=300_000,
                    help="Safety cap on rows loaded per market.")
    p.add_argument("--market-categories", nargs="+",
                    default=["h2h", "spread", "game_total", "team_total"],
                    help="Market categories to train.")
    return asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    rc = main()
    from scripts.sgo.handoff import update_handoff
    update_handoff()
    raise SystemExit(rc)

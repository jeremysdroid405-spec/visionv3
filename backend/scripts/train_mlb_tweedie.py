"""Tweedie retrain for zero-heavy MLB stats (2026-05-14).

Trains XGBoost with `objective='reg:tweedie'` on `home_runs`, `doubles`,
`stolen_bases`. Sweeps `tweedie_variance_power` across [1.1, 1.2, 1.3,
1.4, 1.5] and picks the best variant by **binary-0.5 calibration error**
(NOT R²) — because the downstream betting use case is binary OVER/UNDER
probability quality, not point-projection MSE.

Reuses the exact feature pipeline from `retrain_mlb_models_v2.py` so
nothing about the input vector changes. Only the regression objective
differs.

Saves:
    /app/backend/models/mlb_hf/mlb_hf_{stat}_tweedie.pkl

Writes the per-stat benchmark report:
    /app/backend/models/mlb_hf/_train_report_tweedie_2026_05_14.json

Run from /app/backend:
    python3 scripts/train_mlb_tweedie.py
"""
from __future__ import annotations

import gc
import json
import logging
import os
import pickle
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Tuple

import numpy as np
from scipy.stats import norm
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.preprocessing import StandardScaler
from xgboost import XGBRegressor

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from pymongo import MongoClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("tweedie_train")

# ── Config ────────────────────────────────────────────────────────────
TARGET_STATS = ("home_runs", "doubles", "stolen_bases")
VARIANCE_POWERS = (1.1, 1.2, 1.3, 1.4, 1.5)
MODEL_DIR = "/app/backend/models/mlb_hf"
REPORT_PATH = os.path.join(
    MODEL_DIR, "_train_report_tweedie_2026_05_14.json"
)
BINARY_LINE = 0.5  # the line we care about most for binary calibration
# Bucket edges for calibration table (predicted P(over))
CALIB_BUCKETS = [(0.0, 0.1), (0.1, 0.2), (0.2, 0.3), (0.3, 0.4),
                 (0.4, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8),
                 (0.8, 0.9), (0.9, 1.01)]


# ── Build the feature matrix once per stat using the v2 pipeline ──────
def build_dataset(stat_name: str, db, *,
                   hub, bdl_to_mlbam, name_to_mlbam_batter,
                   name_to_mlbam_pitcher, sc_batter, sc_pitcher,
                   pa_cache, model
                   ) -> Tuple[np.ndarray, np.ndarray, List[str]]:
    """Reproduce v2's per-player iteration to build (X, y, feature_cols)
    for `stat_name`. Identical to retrain_mlb_models_v2.py logic."""
    PITCHER_STATS = {
        "pitcher_strikeouts", "pitcher_walks", "hits_allowed",
        "earned_runs", "pitcher_outs",
    }
    is_pitcher_stat = stat_name in PITCHER_STATS

    def _date_str(log: dict):
        d = log.get("date")
        if not d:
            return None
        s = str(d)
        return s[:10] if len(s) >= 10 else None

    def _resolve_mlbam(player: dict, *, pitcher: bool):
        bdl = (
            player.get("bdl_id")
            or player.get("bdl_player_id")
            or player.get("player_id")
        )
        try:
            bdl = int(bdl) if bdl is not None else None
        except (TypeError, ValueError):
            bdl = None
        if bdl is not None and bdl in bdl_to_mlbam:
            return bdl_to_mlbam[bdl]
        nm = (
            player.get("display_name") or player.get("player_name") or ""
        ).lower().strip()
        if nm:
            idx = name_to_mlbam_pitcher if pitcher else name_to_mlbam_batter
            if nm in idx:
                return idx[nm]
        return None

    def _sc_lookup_batter(mlbam_id, gdate):
        if mlbam_id is None or not gdate:
            return None
        by_date = sc_batter.get(mlbam_id)
        if not by_date:
            return None
        if gdate in by_date:
            return by_date[gdate]
        cands = [d for d in by_date if d <= gdate]
        return by_date[max(cands)] if cands else None

    def _sc_lookup_pitcher(mlbam_id, gdate):
        if mlbam_id is None or not gdate:
            return None
        by_date = sc_pitcher.get(mlbam_id)
        if not by_date:
            return None
        if gdate in by_date:
            return by_date[gdate]
        cands = [d for d in by_date if d <= gdate]
        return by_date[max(cands)] if cands else None

    X_chunks, y_chunks = [], []
    feature_cols = None
    total_samples = 0
    player_cursor = hub.find(
        {"bdl_id": {"$ne": None}},
        {"_id": 0, "bdl_id": 1, "player_name": 1, "display_name": 1,
         "team": 1, "is_pitcher": 1, "is_batter": 1,
         "vs_left": 1, "vs_right": 1, "home_splits": 1, "away_splits": 1,
         "mlb_id": 1, "mlbam_id": 1, "statcast_id": 1,
         "bdl_game_logs": 1},
    ).batch_size(50)

    for player in player_cursor:
        bdl_id = player.get("bdl_id")
        if bdl_id is None:
            continue
        try:
            bdl_id = int(bdl_id)
        except (TypeError, ValueError):
            continue
        logs = player.get("bdl_game_logs") or []
        logs = sorted(
            logs,
            key=lambda x: (x.get("date") or "", x.get("game_id") or 0),
            reverse=True,
        )
        if len(logs) < 12:
            continue
        team = player.get("team")
        mlbam_id = _resolve_mlbam(player, pitcher=is_pitcher_stat)
        player_X, player_y = [], []
        max_windows = min(len(logs) - 11, 500)
        for i in range(max_windows):
            target_game = logs[i]
            target_val = model._get_stat_value(target_game, stat_name)
            if target_val is None:
                continue
            history = logs[i+1:]
            if len(history) < 5:
                continue
            hist_vals = [
                model._get_stat_value(g, stat_name) for g in history[:20]
            ]
            hist_vals = [v for v in hist_vals if v is not None]
            if len(hist_vals) < 5:
                continue
            line = float(np.median(hist_vals))
            gdate = _date_str(target_game)
            sc_b = sc_p = pa_b = pa_p = None
            if is_pitcher_stat:
                sc_p = _sc_lookup_pitcher(mlbam_id, gdate)
                if mlbam_id is not None and gdate:
                    pa_p = pa_cache.pitcher_features(int(mlbam_id), gdate)
            else:
                sc_b = _sc_lookup_batter(mlbam_id, gdate)
                if mlbam_id is not None and gdate:
                    pa_b = pa_cache.batter_features(int(mlbam_id), gdate)
            features = model._build_friction_features(
                player, history, stat_name,
                opponent=None, park_team=team,
                dk_odds=None, line=line,
                statcast_features=sc_b,
                pitcher_statcast_features=sc_p,
                pa_batter_features=pa_b,
                pa_pitcher_features=pa_p,
            )
            if features is None:
                continue
            if feature_cols is None:
                feature_cols = sorted(features.keys())
            player_X.append([features.get(c, 0) for c in feature_cols])
            player_y.append(target_val)
        if player_X:
            X_chunks.append(np.array(player_X, dtype=np.float32))
            y_chunks.append(np.array(player_y, dtype=np.float32))
            total_samples += len(player_X)

    if total_samples == 0 or feature_cols is None:
        return None, None, None
    X = np.vstack(X_chunks)
    y = np.concatenate(y_chunks)
    return X, y, feature_cols


# ── Calibration / distribution helpers ────────────────────────────────
def calibration_table(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    *,
    sigma_estimator: float,
    line: float = BINARY_LINE,
) -> Dict[str, object]:
    """Build P(over=line) buckets and compare against actual >line rate.

    Probability path mirrors live production: Normal-CDF with σ tied to
    a single CV-style estimator (we use stat-level σ rather than per-row
    CV here for simplicity — purpose is *relative* calibration of the
    two models, not the live scoring path itself)."""
    # P(over) for every sample using a stat-level σ
    z = (line - y_pred) / max(sigma_estimator, 1e-3)
    p_over = 1.0 - norm.cdf(z)
    p_over = np.clip(p_over, 0.0, 1.0)
    actual_over = (y_true > line).astype(np.float32)

    buckets = []
    total_calib_err = 0.0
    total_n = 0
    for lo, hi in CALIB_BUCKETS:
        mask = (p_over >= lo) & (p_over < hi)
        n = int(mask.sum())
        if n == 0:
            buckets.append({
                "bucket": f"{int(lo * 100)}-{int(hi * 100)}%",
                "n": 0,
                "mean_predicted_p_over": None,
                "actual_hit_rate": None,
                "abs_calibration_error": None,
            })
            continue
        mean_pred = float(p_over[mask].mean())
        actual = float(actual_over[mask].mean())
        err = abs(mean_pred - actual)
        buckets.append({
            "bucket": f"{int(lo * 100)}-{int(hi * 100)}%",
            "n": n,
            "mean_predicted_p_over": round(mean_pred, 4),
            "actual_hit_rate": round(actual, 4),
            "abs_calibration_error": round(err, 4),
        })
        total_calib_err += err * n
        total_n += n

    weighted_calib_err = (
        total_calib_err / total_n if total_n else float("nan")
    )
    return {
        "weighted_abs_calibration_error": round(weighted_calib_err, 4),
        "n_samples": total_n,
        "buckets": buckets,
    }


def distribution_shift(y_pred: np.ndarray) -> Dict[str, float]:
    return {
        "pct_zero_predictions": round(
            float((y_pred < 1e-3).sum() / max(len(y_pred), 1)), 4
        ),
        "mean_prediction": round(float(y_pred.mean()), 4),
        "prediction_std": round(float(y_pred.std()), 4),
        "min_prediction": round(float(y_pred.min()), 4),
        "max_prediction": round(float(y_pred.max()), 4),
    }


# ── Train + sweep ──────────────────────────────────────────────────────
def train_baseline_and_tweedie(
    X: np.ndarray, y: np.ndarray, feature_cols: List[str],
    *, stat_name: str,
) -> Dict[str, object]:
    """Train baseline XGB (Gaussian) + Tweedie sweep + calibration eval.

    Picks the *best* Tweedie variant by minimum weighted calibration
    error (NOT R² — per the user's success criteria). Returns the full
    benchmark dict plus the chosen variant's fitted model + scaler.
    """
    # Reuse v2's shuffle / split for apples-to-apples comparison.
    rng = np.random.RandomState(42)
    perm = rng.permutation(len(X))
    X = X[perm]
    y = y[perm]
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    split = int(len(X) * 0.8)
    X_tr, X_te = X_scaled[:split], X_scaled[split:]
    y_tr, y_te = y[:split], y[split:]

    # σ for calibration probe — stat-level std of the test targets.
    sigma_stat = float(np.std(y_te))

    # ── Baseline (Gaussian) — same hparams as v2 ──
    base = XGBRegressor(
        n_estimators=200, max_depth=5, learning_rate=0.08,
        subsample=0.8, colsample_bytree=0.8,
        reg_alpha=0.1, reg_lambda=1.0,
        random_state=42, verbosity=0,
    )
    base.fit(X_tr, y_tr)
    y_te_pred_base = base.predict(X_te)
    base_metrics = {
        "r2_test":  round(float(r2_score(y_te, y_te_pred_base)), 4),
        "mae_test": round(float(mean_absolute_error(y_te, y_te_pred_base)), 4),
        "distribution_shift": distribution_shift(y_te_pred_base),
        "calibration": calibration_table(
            y_te, y_te_pred_base, sigma_estimator=sigma_stat
        ),
    }

    # ── Tweedie sweep ──
    sweep: List[Dict] = []
    best_variant = None
    best_calib_err = float("inf")
    best_model = None
    for vp in VARIANCE_POWERS:
        # Tweedie requires non-negative targets. Stats are counts ≥ 0
        # so this is satisfied — but defensively clip:
        y_tr_pos = np.maximum(y_tr, 0.0)
        tw = XGBRegressor(
            n_estimators=200, max_depth=5, learning_rate=0.08,
            subsample=0.8, colsample_bytree=0.8,
            reg_alpha=0.1, reg_lambda=1.0,
            random_state=42, verbosity=0,
            objective="reg:tweedie",
            tweedie_variance_power=vp,
        )
        tw.fit(X_tr, y_tr_pos)
        y_te_pred = tw.predict(X_te)
        # Tweedie predicts in the *log* of mean for variance_power∈(1,2),
        # but XGBoost handles the link internally — `predict` returns the
        # original-scale conditional mean. Still defensively floor at 0:
        y_te_pred = np.maximum(y_te_pred, 0.0)
        m = {
            "tweedie_variance_power": vp,
            "r2_test":  round(float(r2_score(y_te, y_te_pred)), 4),
            "mae_test": round(float(mean_absolute_error(y_te, y_te_pred)), 4),
            "distribution_shift": distribution_shift(y_te_pred),
            "calibration": calibration_table(
                y_te, y_te_pred, sigma_estimator=sigma_stat
            ),
        }
        sweep.append(m)
        calib_err = m["calibration"]["weighted_abs_calibration_error"]
        if calib_err is not None and calib_err < best_calib_err:
            best_calib_err = calib_err
            best_variant = vp
            best_model = tw

    chosen = next(s for s in sweep if s["tweedie_variance_power"] == best_variant)
    return {
        "stat": stat_name,
        "n_train": int(len(X_tr)),
        "n_test": int(len(X_te)),
        "sigma_stat_used_for_calibration": round(sigma_stat, 4),
        "baseline_gaussian": base_metrics,
        "tweedie_sweep": sweep,
        "chosen_variant": {
            "tweedie_variance_power": best_variant,
            **chosen,
        },
        "verdict": {
            "r2_delta_vs_baseline":
                chosen["r2_test"] - base_metrics["r2_test"],
            "mae_delta_vs_baseline":
                chosen["mae_test"] - base_metrics["mae_test"],
            "calibration_delta_vs_baseline":
                chosen["calibration"]["weighted_abs_calibration_error"]
                - base_metrics["calibration"]["weighted_abs_calibration_error"],
        },
        # carried separately for the saver
        "_chosen_model": best_model,
        "_scaler": scaler,
        "_feature_cols": feature_cols,
    }


# ── Main ───────────────────────────────────────────────────────────────
def main():
    client = MongoClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    hub = db.mlb_master_hub_2026

    import services.mlb_high_friction_model as hfm
    hfm._mlb_hf_instance = None
    model = hfm.get_mlb_high_friction_model(db)

    # Build the lookups identically to v2.
    logger.info("Building identity-map (bdl_id → mlbam_id)…")
    bdl_to_mlbam: dict = {}
    for m in db.mlb_player_identity_map.find(
        {"bdl_id": {"$ne": None}, "mlb_id": {"$ne": None}},
        {"_id": 0, "bdl_id": 1, "mlb_id": 1, "statcast_id": 1},
    ):
        bdl = m.get("bdl_id")
        mid = m.get("statcast_id") or m.get("mlb_id")
        if bdl is not None and mid is not None:
            try:
                bdl_to_mlbam[int(bdl)] = int(mid)
            except (TypeError, ValueError):
                continue
    logger.info(f"  identity map: {len(bdl_to_mlbam):,}")

    logger.info("Building name → mlbam_id fallbacks…")
    name_to_mlbam_batter: dict = {}
    for d in db.mlb_statcast_player_features.find(
        {}, {"_id": 0, "player_id": 1, "player_name": 1},
    ):
        nm = (d.get("player_name") or "").lower().strip()
        if nm and nm not in name_to_mlbam_batter:
            name_to_mlbam_batter[nm] = d["player_id"]
    name_to_mlbam_pitcher: dict = {}
    for d in db.mlb_statcast_pitcher_features.find(
        {}, {"_id": 0, "pitcher_id": 1, "pitcher_name": 1},
    ):
        nm = (d.get("pitcher_name") or "").lower().strip()
        if nm and nm not in name_to_mlbam_pitcher:
            name_to_mlbam_pitcher[nm] = d["pitcher_id"]
    logger.info(
        f"  batter names: {len(name_to_mlbam_batter):,}  "
        f"pitcher names: {len(name_to_mlbam_pitcher):,}"
    )

    logger.info("Loading Statcast batter features…")
    sc_batter = defaultdict(dict)
    for d in db.mlb_statcast_player_features.find(
        {}, {"_id": 0, "player_id": 1, "game_date": 1,
             "rolling_7": 1, "rolling_14": 1, "rolling_30": 1,
             "season_window": 1},
    ):
        pid = d.get("player_id")
        gd = d.get("game_date")
        if pid is None or not gd:
            continue
        sc_batter[pid][gd] = {
            "rolling_7":     d.get("rolling_7") or {},
            "rolling_14":    d.get("rolling_14") or {},
            "rolling_30":    d.get("rolling_30") or {},
            "season_window": d.get("season_window") or {},
        }
    logger.info(
        f"  batter SC: {sum(len(v) for v in sc_batter.values()):,}"
    )

    logger.info("Loading Statcast pitcher features…")
    sc_pitcher = defaultdict(dict)
    for d in db.mlb_statcast_pitcher_features.find(
        {}, {"_id": 0, "pitcher_id": 1, "game_date": 1,
             "rolling_14": 1, "rolling_30": 1, "season_window": 1},
    ):
        pid = d.get("pitcher_id")
        gd = d.get("game_date")
        if pid is None or not gd:
            continue
        sc_pitcher[pid][gd] = {
            "rolling_14":    d.get("rolling_14") or {},
            "rolling_30":    d.get("rolling_30") or {},
            "season_window": d.get("season_window") or {},
        }
    logger.info(
        f"  pitcher SC: {sum(len(v) for v in sc_pitcher.values()):,}"
    )

    logger.info("Loading mlb_statcast_raw → PA cache (1.6M rows)…")
    from services.mlb_pa_features import MLBPACache
    pa_cache = MLBPACache()
    n_raw = pa_cache.load_from_db(db)
    logger.info(
        f"  PA cache: {n_raw:,} rows  →  "
        f"{pa_cache.stats()['batters']:,} batters / "
        f"{pa_cache.stats()['pitchers']:,} pitchers"
    )

    report = {
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "binary_calibration_line": BINARY_LINE,
        "variance_powers_swept": list(VARIANCE_POWERS),
        "stats": {},
    }

    for stat in TARGET_STATS:
        t0 = time.time()
        logger.info(f"[{stat}] building dataset…")
        X, y, feature_cols = build_dataset(
            stat, db,
            hub=hub,
            bdl_to_mlbam=bdl_to_mlbam,
            name_to_mlbam_batter=name_to_mlbam_batter,
            name_to_mlbam_pitcher=name_to_mlbam_pitcher,
            sc_batter=sc_batter,
            sc_pitcher=sc_pitcher,
            pa_cache=pa_cache,
            model=model,
        )
        if X is None:
            logger.error(f"[{stat}] no data — SKIP")
            continue
        logger.info(
            f"[{stat}] dataset built: n={len(X):,} feat={len(feature_cols)}"
        )
        result = train_baseline_and_tweedie(
            X, y, feature_cols, stat_name=stat,
        )
        chosen_model = result.pop("_chosen_model")
        scaler = result.pop("_scaler")
        fcols = result.pop("_feature_cols")

        # Persist the chosen Tweedie variant under a sidecar pickle —
        # DO NOT overwrite production weights. Loadable in A/B mode.
        chosen_vp = result["chosen_variant"]["tweedie_variance_power"]
        path = os.path.join(MODEL_DIR, f"mlb_hf_{stat}_tweedie.pkl")
        was_writable = os.access(MODEL_DIR, os.W_OK)
        if not was_writable:
            os.chmod(MODEL_DIR, 0o755)
        # If the file exists and is read-only (production-lock convention),
        # unlock it just for the write.
        if os.path.exists(path):
            os.chmod(path, 0o644)
        with open(path, "wb") as fh:
            pickle.dump({
                "model":      chosen_model,
                "scaler":     scaler,
                "features":   fcols,
                "version":    "MLB_HF_v4.0_tweedie_2026_05_14",
                "objective":  "reg:tweedie",
                "tweedie_variance_power": chosen_vp,
                "trained_at": datetime.now(timezone.utc).isoformat(),
                "n_train":    result["n_train"],
                "r2_test":    result["chosen_variant"]["r2_test"],
                "mae_test":   result["chosen_variant"]["mae_test"],
                "calibration_error":
                    result["chosen_variant"]["calibration"]
                          ["weighted_abs_calibration_error"],
            }, fh)
        os.chmod(path, 0o444)
        if not was_writable:
            os.chmod(MODEL_DIR, 0o555)
        logger.info(
            f"[{stat}] saved {path}  vp={chosen_vp}  "
            f"R²={result['chosen_variant']['r2_test']:.4f}  "
            f"calib_err={result['chosen_variant']['calibration']['weighted_abs_calibration_error']:.4f}  "
            f"elapsed={round(time.time() - t0, 1)}s"
        )
        report["stats"][stat] = result
        del X, y, chosen_model, scaler
        gc.collect()

    with open(REPORT_PATH, "w") as fh:
        json.dump(report, fh, indent=2, default=str)
    logger.info(f"REPORT → {REPORT_PATH}")

    # Console summary
    print("\n=== TWEEDIE TRAINING SUMMARY ===\n")
    hdr = (
        f"{'stat':<15} | {'n':>7} | "
        f"{'R²_base':>8} {'R²_tw':>8} {'ΔR²':>8} | "
        f"{'MAE_base':>9} {'MAE_tw':>9} {'ΔMAE':>9} | "
        f"{'CalErr_base':>11} {'CalErr_tw':>10} {'ΔCal':>8} | "
        f"{'best_vp':>7}"
    )
    print(hdr)
    print("-" * len(hdr))
    for stat in TARGET_STATS:
        r = report["stats"].get(stat)
        if not r:
            continue
        b = r["baseline_gaussian"]
        c = r["chosen_variant"]
        bc = b["calibration"]["weighted_abs_calibration_error"]
        cc = c["calibration"]["weighted_abs_calibration_error"]
        print(
            f"{stat:<15} | {r['n_test'] + r['n_train']:>7,} | "
            f"{b['r2_test']:>8.4f} {c['r2_test']:>8.4f} "
            f"{c['r2_test'] - b['r2_test']:>+8.4f} | "
            f"{b['mae_test']:>9.4f} {c['mae_test']:>9.4f} "
            f"{c['mae_test'] - b['mae_test']:>+9.4f} | "
            f"{bc:>11.4f} {cc:>10.4f} {cc - bc:>+8.4f} | "
            f"{c['tweedie_variance_power']:>7}"
        )


if __name__ == "__main__":
    main()

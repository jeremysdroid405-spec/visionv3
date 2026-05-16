"""Phase 2B retrain — resumable, bounded-per-invocation worker.

Mirrors `phase2a_retrain_worker.py` for the pitcher stat family. Same
chunked-per-stat / resumable architecture; same pickled cache layout
for memory safety on the constrained pod.

Pitcher-specific additions
──────────────────────────
1. **Opposing-lineup resolver** (`services.mlb_lineup_resolver`):
   one-time aggregation over `mlb_statcast_raw` producing
   `{(pitcher_id, game_date): [{batter_id, stand, ...}, ...]}`.
   Pickled to `_phase2b_workdir/lineup_resolver.pkl` for resumability.
2. **Batter rolling-14 cache** — reuses the Phase 2A `sc_caches.pkl`
   already pickled in `_phase2a_workdir/sc_caches.pkl` (same data
   source: `mlb_statcast_player_features`). One file, shared.
3. **Pitcher hand resolution** — pulled from
   `mlb_statcast_pitcher_features` (preferred) → master-hub
   `pitching_hand` (fallback).
4. **Inline rolling decoration** — each batter dict in the lineup is
   decorated with its rolling-14 block (k_rate/bb_rate/wOBA/xwOBA/
   hard_hit_rate/barrel_rate) so `build_lineup_features` can compute
   `lineup_strength_*` without a separate cache.

The trained pickle uses the same shape as Phase 2A (model + scaler +
features list + metadata) but tagged `MLB_HF_v3.2_phase2b`.

State paths
───────────
    /app/backend/models/mlb_hf/_phase2b_workdir/
        ├── lineup_resolver.pkl   (one-time, ~120MB)
        ├── _progress.json        (which stats are done)
        └── _train_report.json    (per-stat metrics)

    Reused from Phase 2A workdir:
        /app/backend/models/mlb_hf/_phase2a_workdir/
        ├── sc_caches.pkl   (batter + pitcher SC, ~80MB)
        ├── pa_cache.pkl    (PA-windowed Statcast cache)

Usage
─────
    cd /app/backend && python scripts/phase2b_retrain_worker.py --status
    cd /app/backend && python scripts/phase2b_retrain_worker.py --build-resolver
    cd /app/backend && python scripts/phase2b_retrain_worker.py --stat pitcher_strikeouts
    cd /app/backend && python scripts/phase2b_retrain_worker.py --stat earned_runs
    cd /app/backend && python scripts/phase2b_retrain_worker.py --stat pitcher_walks
    cd /app/backend && python scripts/phase2b_retrain_worker.py --stat hits_allowed
    cd /app/backend && python scripts/phase2b_retrain_worker.py --all
"""
from __future__ import annotations

import argparse
import gc
import json
import logging
import os
import pickle
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

sys.path.insert(0, "/app/backend")
os.chdir("/app/backend")

from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

import numpy as np
import pymongo
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.preprocessing import StandardScaler
from xgboost import XGBRegressor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("phase2b_retrain")

WORKDIR_2B = "/app/backend/models/mlb_hf/_phase2b_workdir"
WORKDIR_2A = "/app/backend/models/mlb_hf/_phase2a_workdir"
MODEL_DIR = "/app/backend/models/mlb_hf"
os.makedirs(WORKDIR_2B, exist_ok=True)

LINEUP_RESOLVER_PATH = os.path.join(WORKDIR_2B, "lineup_resolver.pkl")
SC_CACHE_PATH_2A = os.path.join(WORKDIR_2A, "sc_caches.pkl")
PROGRESS_PATH = os.path.join(WORKDIR_2B, "_progress.json")
REPORT_PATH = os.path.join(WORKDIR_2B, "_train_report.json")

VERSION = "MLB_HF_v3.2_phase2b"

# Pitcher stats with trainable XGBoost models.
# 2026-05-17 — `pitcher_outs` is now a first-class model target. When
# the trained pickle is loaded, `MLBHighFrictionModel.predict()` uses
# it for μ; the analytical `expected_IP × 3` projection in
# `_predict_pitcher_outs` becomes the cold-start fallback and a
# permanent diagnostic on every response.
PITCHER_STATS = [
    "pitcher_strikeouts",
    "pitcher_walks",
    "earned_runs",
    "hits_allowed",
    "pitcher_outs",
]


def _db():
    client = pymongo.MongoClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


# ─────────────────────────────────────────────────────────────────────
# Phase A: Opposing-lineup resolver (one-time pickle, reused per stat)
# ─────────────────────────────────────────────────────────────────────
def load_or_build_lineup_resolver(db) -> Dict[str, Any]:
    """Wraps `services.mlb_lineup_resolver.load_or_build_resolver`
    but uses the Phase 2B workdir path."""
    from services.mlb_lineup_resolver import (
        build_lineup_resolver as _build,
    )
    if os.path.exists(LINEUP_RESOLVER_PATH):
        with open(LINEUP_RESOLVER_PATH, "rb") as f:
            r = pickle.load(f)
        logger.info(
            f"Loaded lineup resolver from disk: "
            f"{r['n_pairs']:,} pitcher-game pairs."
        )
        return r
    r = _build(db)
    with open(LINEUP_RESOLVER_PATH, "wb") as f:
        pickle.dump(r, f, protocol=pickle.HIGHEST_PROTOCOL)
    logger.info(f"Saved lineup resolver → {LINEUP_RESOLVER_PATH}")
    return r


# ─────────────────────────────────────────────────────────────────────
# Phase B: Statcast caches.
# Phase 2A's full `sc_caches.pkl` is 149MB pickled and unpacks heavy
# enough to push the pod over memory pressure when combined with the
# PA cache + XGBoost training memory. Phase 2B uses a leaner shape:
#   • sc_pitcher (~50MB) — loaded from disk for pitcher self-features
#   • sc_batter (lazy) — only the batters actually referenced by the
#     lineup resolver, fetched directly from
#     `mlb_statcast_player_features` in a single batched query
# This keeps peak RSS well under 6GB during training.
# ─────────────────────────────────────────────────────────────────────
def load_sc_pitcher_cache() -> Dict[int, Dict[str, Any]]:
    if not os.path.exists(SC_CACHE_PATH_2A):
        raise RuntimeError(
            f"Phase 2A SC cache not found at {SC_CACHE_PATH_2A}. "
            f"Run Phase 2A `--build-caches` first."
        )
    with open(SC_CACHE_PATH_2A, "rb") as f:
        c = pickle.load(f)
    sc_pitcher = c.get("sc_pitcher", {}) or {}
    # Free the unused batter half ASAP.
    c.pop("sc_batter", None)
    del c
    gc.collect()
    logger.info(
        f"Loaded sc_pitcher cache: {len(sc_pitcher):,} pids "
        f"(sc_batter dropped — lazy-loaded below)"
    )
    return sc_pitcher


def build_lazy_sc_batter_cache(
    db, batter_ids: List[int],
) -> Dict[int, Dict[str, Any]]:
    """Pull rolling-14 only for the batters appearing in any lineup —
    typically ~3,000-5,000 distinct batters across the historical
    pitcher×date set. Cheap, bounded query.
    """
    if not batter_ids:
        return {}
    out: Dict[int, Dict[str, Any]] = {}
    t0 = time.time()
    for d in db.mlb_statcast_player_features.find(
            {"player_id": {"$in": list(batter_ids)}},
            {"_id": 0, "player_id": 1, "game_date": 1,
             "rolling_14": 1}):
        pid = d.get("player_id")
        gd = d.get("game_date")
        if pid is None or not gd:
            continue
        out.setdefault(int(pid), {})[str(gd)[:10]] = {
            "rolling_14": d.get("rolling_14") or {},
        }
    logger.info(
        f"  lazy sc_batter: pids={len(out):,}, "
        f"sum(dates)={sum(len(v) for v in out.values()):,}, "
        f"elapsed={time.time()-t0:.1f}s"
    )
    return out


# ─────────────────────────────────────────────────────────────────────
# Identity resolution
# ─────────────────────────────────────────────────────────────────────
def build_identity_maps(db) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "bdl_to_mlbam": {},
        "name_pitcher": {},
    }
    for m in db.mlb_player_identity_map.find(
            {"bdl_id": {"$ne": None}, "mlb_id": {"$ne": None}},
            {"_id": 0, "bdl_id": 1, "mlb_id": 1, "statcast_id": 1}):
        bdl = m.get("bdl_id")
        mid = m.get("statcast_id") or m.get("mlb_id")
        if bdl is not None and mid is not None:
            try:
                out["bdl_to_mlbam"][int(bdl)] = int(mid)
            except (TypeError, ValueError):
                pass
    for d in db.mlb_statcast_pitcher_features.find(
            {}, {"_id": 0, "pitcher_id": 1, "pitcher_name": 1}):
        nm = (d.get("pitcher_name") or "").lower().strip()
        if nm and nm not in out["name_pitcher"]:
            out["name_pitcher"][nm] = d["pitcher_id"]
    return out


def _resolve_mlbam(player: Dict[str, Any], idmaps: Dict[str, Any]) -> Optional[int]:
    bdl = (player.get("bdl_id") or player.get("bdl_player_id")
           or player.get("player_id"))
    try:
        bdl = int(bdl) if bdl is not None else None
    except (TypeError, ValueError):
        bdl = None
    if bdl is not None and bdl in idmaps["bdl_to_mlbam"]:
        return idmaps["bdl_to_mlbam"][bdl]
    nm = (player.get("display_name") or player.get("player_name")
          or "").lower().strip()
    if nm and nm in idmaps["name_pitcher"]:
        return idmaps["name_pitcher"][nm]
    return None


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _date_str(log: Dict[str, Any]) -> Optional[str]:
    d = log.get("date")
    if not d:
        return None
    s = str(d)
    return s[:10] if len(s) >= 10 else None


def _sc_lookup(cache: Dict[int, Dict[str, Any]],
               mid: Optional[int],
               gd: Optional[str]) -> Optional[Dict[str, Any]]:
    if mid is None or not gd:
        return None
    by_date = cache.get(mid)
    if not by_date:
        return None
    if gd in by_date:
        return by_date[gd]
    cands = [d for d in by_date if d <= gd]
    if not cands:
        return None
    return by_date[max(cands)]


def _decorate_lineup_with_rolling(
    lineup: List[Dict[str, Any]],
    sc_batter: Dict[int, Dict[str, Any]],
    gdate: str,
) -> List[Dict[str, Any]]:
    """Attach inline `rolling_14` per batter as-of `gdate`. Mirrors
    the live-prediction wiring in `feature_hydration.py`."""
    out: List[Dict[str, Any]] = []
    for b in lineup:
        b2 = dict(b)
        bid = b.get("batter_id")
        sc = _sc_lookup(sc_batter, bid, gdate) if bid is not None else None
        if sc and sc.get("rolling_14"):
            b2["rolling_14"] = sc["rolling_14"]
        out.append(b2)
    return out


# ─────────────────────────────────────────────────────────────────────
# Progress
# ─────────────────────────────────────────────────────────────────────
def _load_progress() -> Dict[str, Any]:
    if os.path.exists(PROGRESS_PATH):
        with open(PROGRESS_PATH) as f:
            return json.load(f)
    return {
        "completed": [],
        "started_at": datetime.now(timezone.utc).isoformat(),
    }


def _save_progress(p: Dict[str, Any]) -> None:
    with open(PROGRESS_PATH, "w") as f:
        json.dump(p, f, indent=2)


def _load_report() -> Dict[str, Any]:
    if os.path.exists(REPORT_PATH):
        with open(REPORT_PATH) as f:
            return json.load(f)
    return {
        "version": VERSION,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "stats": {},
    }


def _save_report(r: Dict[str, Any]) -> None:
    with open(REPORT_PATH, "w") as f:
        json.dump(r, f, indent=2, default=str)


# ─────────────────────────────────────────────────────────────────────
# Train one stat
# ─────────────────────────────────────────────────────────────────────
def train_one_stat(stat_name: str, *, force: bool = False) -> Dict[str, Any]:
    progress = _load_progress()
    if not force and stat_name in progress["completed"]:
        logger.info(f"{stat_name}: already trained, skipping (use --force).")
        return {"skipped": True}

    db = _db()
    hub = db.mlb_master_hub_2026

    logger.info(f"=== Training {stat_name} (Phase 2B) ===")
    t_start = time.time()

    # Load lean caches (resolver + sc_pitcher + identity + lazy sc_batter).
    resolver = load_or_build_lineup_resolver(db)
    sc_pitcher = load_sc_pitcher_cache()
    idmaps = build_identity_maps(db)
    lineup_map = resolver["lineup"]

    # Phase 2B memory budget: skip the PA cache (would add ~1-2GB).
    # The pitcher-side `pa_p_*` features stay imputed during training,
    # so the model learns to discount them. SC rolling features
    # (`sc_p_*`) carry the pitcher recent-form signal already.

    # Lazy sc_batter: collect every batter_id referenced by any
    # (pitcher, date) pair in the resolver, then issue ONE query.
    batter_ids: set = set()
    for lineup in lineup_map.values():
        for b in lineup:
            bid = b.get("batter_id")
            if bid is not None:
                batter_ids.add(int(bid))
    sc_batter = build_lazy_sc_batter_cache(db, sorted(batter_ids))
    del batter_ids
    gc.collect()

    import services.mlb_high_friction_model as hfm
    hfm._mlb_hf_instance = None
    model = hfm.get_mlb_high_friction_model(db)

    X_chunks, y_chunks = [], []
    feature_cols = None
    total_samples = 0
    sc_p_hit = sc_p_miss = 0
    lineup_hit = lineup_miss = 0

    cursor = hub.find(
        {"bdl_id": {"$ne": None}, "is_pitcher": True},
        {"_id": 0, "bdl_id": 1, "player_name": 1, "display_name": 1,
         "team": 1, "is_pitcher": 1, "pitching_hand": 1,
         "throws": 1, "pitcher_throws": 1,
         "vs_left": 1, "vs_right": 1, "home_splits": 1, "away_splits": 1,
         "mlb_id": 1, "mlbam_id": 1, "statcast_id": 1,
         "bdl_game_logs": 1},
    ).batch_size(50)

    player_count = 0
    for player in cursor:
        try:
            int(player.get("bdl_id"))
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
        player_count += 1
        mlbam_id = _resolve_mlbam(player, idmaps)

        player_X, player_y = [], []
        max_windows = min(len(logs) - 11, 500)
        for i in range(max_windows):
            target_game = logs[i]
            target_val = model._get_stat_value(target_game, stat_name)
            if target_val is None:
                continue
            history = logs[i + 1:]
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

            # Pitcher SC self-features.
            sc_p = _sc_lookup(sc_pitcher, mlbam_id, gdate)
            pa_p = None  # Phase 2B: PA cache dropped for memory budget.
            sc_p_hit += 1 if sc_p is not None else 0
            sc_p_miss += 1 if sc_p is None else 0

            # Phase 2B opposing-lineup resolution.
            opposing_lineup = None
            if mlbam_id is not None and gdate:
                key = (int(mlbam_id), gdate)
                lineup_raw = lineup_map.get(key)
                if lineup_raw:
                    opposing_lineup = _decorate_lineup_with_rolling(
                        lineup_raw, sc_batter, gdate,
                    )
                    lineup_hit += 1
                else:
                    lineup_miss += 1
            else:
                lineup_miss += 1

            features = model._build_friction_features(
                player, history, stat_name,
                opponent=target_game.get("opponent_abbr"),
                park_team=team,
                dk_odds=None, line=line,
                statcast_features=None,
                pitcher_statcast_features=sc_p,
                pa_batter_features=None,
                pa_pitcher_features=pa_p,
                # Phase 2A inputs — batter-side, not relevant for
                # pitcher props. Emitted imputed by the builder.
                batter_hand=None,
                opp_pitcher_throws=None,
                opp_pitcher_features=None,
                # Phase 2B opposing-lineup payload.
                opposing_lineup=opposing_lineup,
                sc_batter_cache=None,
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
        if player_count % 100 == 0:
            gc.collect()
            logger.info(
                f"  {stat_name}: {player_count} pitchers, "
                f"{total_samples} samples, "
                f"sc_p_hit={sc_p_hit}/{sc_p_hit+sc_p_miss}, "
                f"lineup_hit={lineup_hit}/{lineup_hit+lineup_miss}"
            )

    if total_samples < 100 or feature_cols is None:
        logger.warning(f"{stat_name}: insufficient samples ({total_samples})")
        return {"skipped": True, "samples": total_samples}

    X = np.vstack(X_chunks)
    y = np.concatenate(y_chunks)
    del X_chunks, y_chunks
    gc.collect()

    zero_rate = (X == 0).mean(axis=0)
    fully_zero = [feature_cols[i] for i, z in enumerate(zero_rate)
                  if z >= 0.999]

    scaler = StandardScaler()
    Xs = scaler.fit_transform(X)
    rng = np.random.RandomState(42)
    perm = rng.permutation(len(Xs))
    Xs = Xs[perm]
    y = y[perm]
    split = int(len(Xs) * 0.8)
    X_tr, X_te = Xs[:split], Xs[split:]
    y_tr, y_te = y[:split], y[split:]

    xgb = XGBRegressor(
        n_estimators=200, max_depth=5, learning_rate=0.08,
        subsample=0.8, colsample_bytree=0.8,
        reg_alpha=0.1, reg_lambda=1.0,
        random_state=42, verbosity=0,
    )
    xgb.fit(X_tr, y_tr)
    y_pred_te = xgb.predict(X_te)
    y_pred_tr = xgb.predict(X_tr)
    r2_te = r2_score(y_te, y_pred_te)
    mae_te = mean_absolute_error(y_te, y_pred_te)
    r2_tr = r2_score(y_tr, y_pred_tr)
    mae_tr = mean_absolute_error(y_tr, y_pred_tr)

    importances = sorted(
        zip(feature_cols, xgb.feature_importances_.tolist()),
        key=lambda x: -x[1],
    )

    # Phase 2B-specific feature importance — lineup block.
    from services.mlb_lineup_features import PHASE2B_LINEUP_FEATURE_NAMES
    p2b_set = set(PHASE2B_LINEUP_FEATURE_NAMES)
    p2b_importance = [
        (k, round(v, 5)) for k, v in importances if k in p2b_set
    ]
    park_set = {
        "park_hits_factor", "park_runs_factor", "park_hr_factor",
        "park_k_factor", "park_tb_factor", "park_factor",
    }
    park_importance = [
        (k, round(v, 5)) for k, v in importances if k in park_set
    ]

    artifact = {
        "model": xgb, "scaler": scaler, "features": feature_cols,
        "version": VERSION,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "samples": total_samples, "feature_count": len(feature_cols),
        "r2_train": round(r2_tr, 4), "r2_test": round(r2_te, 4),
        "mae_train": round(mae_tr, 4), "mae_test": round(mae_te, 4),
        "sc_p_hit_rate": round(sc_p_hit / max(1, sc_p_hit + sc_p_miss), 4),
        "lineup_hit_rate": round(
            lineup_hit / max(1, lineup_hit + lineup_miss), 4,
        ),
        "top_features": importances[:25],
        "p2b_lineup_feature_importance": p2b_importance,
        "park_feature_importance": park_importance,
        "fully_zero_features": fully_zero,
    }
    out_path = os.path.join(MODEL_DIR, f"mlb_hf_{stat_name}.pkl")
    with open(out_path, "wb") as f:
        pickle.dump(artifact, f)
    elapsed = time.time() - t_start
    logger.info(
        f"{stat_name}: samples={total_samples} feat={len(feature_cols)} "
        f"R2_te={r2_te:.4f} MAE_te={mae_te:.4f} "
        f"lineup_hit={lineup_hit}/{lineup_hit+lineup_miss} "
        f"elapsed={elapsed:.1f}s"
    )

    progress["completed"].append(stat_name)
    progress["completed"] = sorted(set(progress["completed"]))
    progress["last_updated"] = datetime.now(timezone.utc).isoformat()
    _save_progress(progress)

    report = _load_report()
    report["stats"][stat_name] = {
        "samples": total_samples,
        "feature_count": len(feature_cols),
        "r2_train": round(r2_tr, 4),
        "r2_test": round(r2_te, 4),
        "mae_test": round(mae_te, 4),
        "sc_p_hit_rate": round(sc_p_hit / max(1, sc_p_hit + sc_p_miss), 4),
        "lineup_hit_rate": round(
            lineup_hit / max(1, lineup_hit + lineup_miss), 4,
        ),
        "p2b_lineup_feature_importance": p2b_importance,
        "park_feature_importance": park_importance,
        "top_features": [[k, round(v, 5)] for k, v in importances[:20]],
        "fully_zero_features": fully_zero,
        "elapsed_sec": round(elapsed, 1),
    }
    _save_report(report)
    return {"trained": True, "r2_test": r2_te, "mae_test": mae_te}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stat", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--force", action="store_true",
                     help="Re-train even if progress.json says done.")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--build-resolver", action="store_true",
                     help="Build the lineup resolver pickle and exit.")
    args = ap.parse_args()

    if args.status:
        p = _load_progress()
        print(f"Completed: {p['completed']}")
        print(f"Remaining: {[s for s in PITCHER_STATS if s not in p['completed']]}")
        return

    if args.build_resolver:
        db = _db()
        load_or_build_lineup_resolver(db)
        logger.info("Resolver built — workdir ready for stat training.")
        return

    if args.all:
        for s in PITCHER_STATS:
            train_one_stat(s, force=args.force)
            gc.collect()
    elif args.stat:
        train_one_stat(args.stat, force=args.force)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()

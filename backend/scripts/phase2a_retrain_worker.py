"""Phase 2A retrain — resumable, bounded-per-invocation worker.

Why this exists
───────────────
Pod is under heavy memory pressure (24GB used / 31GB total before
retrain even starts). The original retrain script loads 1.5GB+ of
caches end-to-end before any model trains, then does all 10 stats in
one shot. Pod restarts mid-run lose everything.

This worker is invoked **once per stat** by an external orchestrator.
State is persisted between invocations to:

    /app/backend/models/mlb_hf/_phase2a_workdir/
        ├── matchup_resolver.pkl   (one-time, ~30MB)
        ├── sc_caches.pkl          (one-time, ~80MB) — batter+pitcher SC
        ├── _progress.json         (which stats are done)
        └── _train_report.json     (per-stat metrics)

On startup:
  1. Load (or build) the matchup resolver and SC caches from disk.
  2. Read --stat from CLI. If already trained, exit 0.
  3. Train just that one stat. Save artifact + update progress. Exit.

A single invocation typically finishes in 60-180 sec (well under any
restart window). On pod death we lose just the in-flight stat.

Usage:
    cd /app/backend && python scripts/phase2a_retrain_worker.py --stat hits
    cd /app/backend && python scripts/phase2a_retrain_worker.py --stat total_bases
    ...
    cd /app/backend && python scripts/phase2a_retrain_worker.py --all
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
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Optional

sys.path.insert(0, "/app/backend")
os.chdir("/app/backend")

from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

import numpy as np
import pymongo
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.preprocessing import StandardScaler
from xgboost import XGBRegressor

logging.basicConfig(level=logging.INFO,
                     format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("phase2a_retrain")

WORKDIR = "/app/backend/models/mlb_hf/_phase2a_workdir"
MODEL_DIR = "/app/backend/models/mlb_hf"
os.makedirs(WORKDIR, exist_ok=True)

RESOLVER_PATH = os.path.join(WORKDIR, "matchup_resolver.pkl")
SC_CACHE_PATH = os.path.join(WORKDIR, "sc_caches.pkl")
PA_CACHE_PATH = os.path.join(WORKDIR, "pa_cache.pkl")
PROGRESS_PATH = os.path.join(WORKDIR, "_progress.json")
REPORT_PATH = os.path.join(WORKDIR, "_train_report.json")

BATTER_STATS = [
    "hits", "total_bases", "rbis", "runs", "home_runs",
    "doubles", "walks", "singles", "hits+runs+rbis", "stolen_bases",
]

# ─────────────────────────────────────────────────────────────────────
# Lazy heavy imports — only when actually needed.
# ─────────────────────────────────────────────────────────────────────
def _db():
    client = pymongo.MongoClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


# ─────────────────────────────────────────────────────────────────────
# Phase A: matchup resolver (one-time build, pickle to disk)
# ─────────────────────────────────────────────────────────────────────
def build_matchup_resolver(db) -> dict:
    """Server-side aggregation over mlb_statcast_raw → ~110k pairs."""
    logger.info("Building matchup resolver via Mongo aggregation…")
    t0 = time.time()
    out = {
        "batter_first_pitcher": {},
        "batter_stand": {},
        "pitcher_throws": {},
    }
    pipe = [
        {"$match": {
            "batter": {"$ne": None}, "pitcher": {"$ne": None},
            "game_date": {"$ne": None},
        }},
        {"$sort": {"batter": 1, "game_date": 1,
                    "at_bat_number": 1, "pitch_number": 1}},
        {"$group": {
            "_id": {"b": "$batter", "gd": "$game_date"},
            "p": {"$first": "$pitcher"},
            "stand": {"$first": "$stand"},
            "p_throws": {"$first": "$p_throws"},
        }},
    ]
    n = 0
    for d in db.mlb_statcast_raw.aggregate(
            pipe, allowDiskUse=True, batchSize=2000):
        _id = d.get("_id") or {}
        b = _id.get("b"); gd = _id.get("gd"); p = d.get("p")
        if b is None or p is None or not gd:
            continue
        try:
            b = int(b); p = int(p)
        except (TypeError, ValueError):
            continue
        out["batter_first_pitcher"][(b, gd)] = p
        s = d.get("stand")
        if s:
            out["batter_stand"][(b, gd)] = str(s).strip().upper()[:1]
        if p not in out["pitcher_throws"]:
            t = d.get("p_throws")
            if t:
                out["pitcher_throws"][p] = str(t).strip().upper()[:1]
        n += 1
    logger.info(
        f"  resolver pairs={len(out['batter_first_pitcher']):,}, "
        f"throws={len(out['pitcher_throws']):,}, elapsed={time.time()-t0:.1f}s"
    )
    return out


def load_or_build_resolver(db) -> dict:
    if os.path.exists(RESOLVER_PATH):
        with open(RESOLVER_PATH, "rb") as f:
            r = pickle.load(f)
        logger.info(
            f"Loaded matchup resolver from disk: "
            f"{len(r['batter_first_pitcher']):,} pairs"
        )
        return r
    r = build_matchup_resolver(db)
    with open(RESOLVER_PATH, "wb") as f:
        pickle.dump(r, f, protocol=pickle.HIGHEST_PROTOCOL)
    logger.info(f"Saved matchup resolver → {RESOLVER_PATH}")
    return r


# ─────────────────────────────────────────────────────────────────────
# Phase B: Statcast feature caches (one-time pickle)
# ─────────────────────────────────────────────────────────────────────
def build_sc_caches(db) -> dict:
    logger.info("Building Statcast feature caches…")
    t0 = time.time()
    sc_batter: dict = defaultdict(dict)
    sc_pitcher: dict = defaultdict(dict)
    for d in db.mlb_statcast_player_features.find(
            {}, {"_id": 0, "player_id": 1, "game_date": 1,
                 "rolling_7": 1, "rolling_14": 1, "rolling_30": 1,
                 "season_window": 1}):
        pid = d.get("player_id"); gd = d.get("game_date")
        if pid is None or not gd:
            continue
        sc_batter[pid][gd] = {
            "rolling_7": d.get("rolling_7") or {},
            "rolling_14": d.get("rolling_14") or {},
            "rolling_30": d.get("rolling_30") or {},
            "season_window": d.get("season_window") or {},
        }
    for d in db.mlb_statcast_pitcher_features.find(
            {}, {"_id": 0, "pitcher_id": 1, "game_date": 1,
                 "rolling_14": 1, "rolling_30": 1, "season_window": 1}):
        pid = d.get("pitcher_id"); gd = d.get("game_date")
        if pid is None or not gd:
            continue
        sc_pitcher[pid][gd] = {
            "rolling_14": d.get("rolling_14") or {},
            "rolling_30": d.get("rolling_30") or {},
            "season_window": d.get("season_window") or {},
        }
    logger.info(
        f"  sc_batter: {sum(len(v) for v in sc_batter.values()):,} keys, "
        f"sc_pitcher: {sum(len(v) for v in sc_pitcher.values()):,} keys, "
        f"elapsed={time.time()-t0:.1f}s"
    )
    return {"sc_batter": dict(sc_batter), "sc_pitcher": dict(sc_pitcher)}


def load_or_build_sc_caches(db) -> dict:
    if os.path.exists(SC_CACHE_PATH):
        with open(SC_CACHE_PATH, "rb") as f:
            c = pickle.load(f)
        logger.info("Loaded SC caches from disk.")
        return c
    c = build_sc_caches(db)
    with open(SC_CACHE_PATH, "wb") as f:
        pickle.dump(c, f, protocol=pickle.HIGHEST_PROTOCOL)
    logger.info(f"Saved SC caches → {SC_CACHE_PATH}")
    return c


# ─────────────────────────────────────────────────────────────────────
# Phase C: Identity map + name-fallback
# ─────────────────────────────────────────────────────────────────────
def build_identity_maps(db) -> dict:
    out = {
        "bdl_to_mlbam": {},
        "name_batter": {},
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
    for d in db.mlb_statcast_player_features.find(
            {}, {"_id": 0, "player_id": 1, "player_name": 1}):
        nm = (d.get("player_name") or "").lower().strip()
        if nm and nm not in out["name_batter"]:
            out["name_batter"][nm] = d["player_id"]
    for d in db.mlb_statcast_pitcher_features.find(
            {}, {"_id": 0, "pitcher_id": 1, "pitcher_name": 1}):
        nm = (d.get("pitcher_name") or "").lower().strip()
        if nm and nm not in out["name_pitcher"]:
            out["name_pitcher"][nm] = d["pitcher_id"]
    return out


def _resolve_mlbam(player: dict, idmaps: dict) -> Optional[int]:
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
    if nm and nm in idmaps["name_batter"]:
        return idmaps["name_batter"][nm]
    return None


def _date_str(log: dict) -> Optional[str]:
    d = log.get("date")
    if not d:
        return None
    s = str(d)
    return s[:10] if len(s) >= 10 else None


def _sc_lookup(cache: dict, mid: Optional[int],
                gd: Optional[str]) -> Optional[dict]:
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


# ─────────────────────────────────────────────────────────────────────
# Progress tracking
# ─────────────────────────────────────────────────────────────────────
def _load_progress() -> dict:
    if os.path.exists(PROGRESS_PATH):
        with open(PROGRESS_PATH) as f:
            return json.load(f)
    return {"completed": [], "started_at": datetime.now(timezone.utc).isoformat()}


def _save_progress(p: dict) -> None:
    with open(PROGRESS_PATH, "w") as f:
        json.dump(p, f, indent=2)


def _load_report() -> dict:
    if os.path.exists(REPORT_PATH):
        with open(REPORT_PATH) as f:
            return json.load(f)
    return {"version": "MLB_HF_v3.1_phase2a",
             "trained_at": datetime.now(timezone.utc).isoformat(),
             "stats": {}}


def _save_report(r: dict) -> None:
    with open(REPORT_PATH, "w") as f:
        json.dump(r, f, indent=2, default=str)


# ─────────────────────────────────────────────────────────────────────
# Phase D: Train a single stat
# ─────────────────────────────────────────────────────────────────────
def train_one_stat(stat_name: str, *, force: bool = False) -> dict:
    progress = _load_progress()
    if not force and stat_name in progress["completed"]:
        logger.info(f"{stat_name}: already trained, skipping (use --force).")
        return {"skipped": True}

    db = _db()
    hub = db.mlb_master_hub_2026

    logger.info(f"=== Training {stat_name} ===")
    t_start = time.time()

    # Load all caches
    resolver = load_or_build_resolver(db)
    sc_caches = load_or_build_sc_caches(db)
    idmaps = build_identity_maps(db)
    sc_batter = sc_caches["sc_batter"]
    sc_pitcher = sc_caches["sc_pitcher"]
    batter_first_pitcher = resolver["batter_first_pitcher"]
    batter_stand = resolver["batter_stand"]
    pitcher_throws = resolver["pitcher_throws"]

    # Heavy PA cache — load only once we know we're actually training.
    from services.mlb_pa_features import MLBPACache
    pa_cache = MLBPACache()
    pa_cache.load_from_db(db)

    # Singleton HF model — provides _build_friction_features + _get_stat_value
    import services.mlb_high_friction_model as hfm
    hfm._mlb_hf_instance = None
    model = hfm.get_mlb_high_friction_model(db)

    X_chunks, y_chunks = [], []
    feature_cols = None
    total_samples = 0
    sc_hit = sc_miss = 0
    mc_hit = mc_miss = 0  # matchup-context hit/miss

    cursor = hub.find(
        {"bdl_id": {"$ne": None}},
        {"_id": 0, "bdl_id": 1, "player_name": 1, "display_name": 1,
         "team": 1, "is_pitcher": 1, "is_batter": 1,
         "vs_left": 1, "vs_right": 1, "home_splits": 1, "away_splits": 1,
         "mlb_id": 1, "mlbam_id": 1, "statcast_id": 1,
         "bdl_game_logs": 1},
    ).batch_size(50)

    player_count = 0
    for player in cursor:
        try:
            bdl_id = int(player.get("bdl_id"))
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

            sc_b = _sc_lookup(sc_batter, mlbam_id, gdate)
            pa_b = None
            if mlbam_id is not None and gdate:
                pa_b = pa_cache.batter_features(int(mlbam_id), gdate)
            sc_hit += 1 if sc_b is not None else 0
            sc_miss += 1 if sc_b is None else 0

            bh_t = None
            opt_t = None
            opp_pitcher_feats_t = None
            if mlbam_id is not None and gdate:
                key = (int(mlbam_id), gdate)
                bh_t = batter_stand.get(key)
                opp_pid = batter_first_pitcher.get(key)
                if opp_pid is not None:
                    opt_t = pitcher_throws.get(opp_pid)
                    opp_pitcher_feats_t = _sc_lookup(sc_pitcher, opp_pid, gdate)
            if opt_t:
                mc_hit += 1
            else:
                mc_miss += 1

            features = model._build_friction_features(
                player, history, stat_name,
                opponent=None, park_team=team,
                dk_odds=None, line=line,
                statcast_features=sc_b,
                pitcher_statcast_features=None,
                pa_batter_features=pa_b,
                pa_pitcher_features=None,
                batter_hand=bh_t,
                opp_pitcher_throws=opt_t,
                opp_pitcher_features=opp_pitcher_feats_t,
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
        if player_count % 200 == 0:
            gc.collect()
            logger.info(
                f"  {stat_name}: {player_count} players, "
                f"{total_samples} samples, "
                f"sc_hit={sc_hit}/{sc_hit+sc_miss}, "
                f"matchup_hit={mc_hit}/{mc_hit+mc_miss}"
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
    Xs = Xs[perm]; y = y[perm]
    split = int(len(Xs) * 0.8)
    X_tr, X_te = Xs[:split], Xs[split:]
    y_tr, y_te = y[:split], y[split:]

    xgb = XGBRegressor(
        n_estimators=200, max_depth=5, learning_rate=0.08,
        subsample=0.8, colsample_bytree=0.8,
        reg_alpha=0.1, reg_lambda=1.0, random_state=42, verbosity=0,
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

    # Capture matchup-feature importances specifically.
    matchup_feats = {
        "batter_is_lhh", "batter_is_rhh", "batter_is_switch",
        "batter_hand_is_imputed",
        "opp_pitcher_throws_l", "opp_pitcher_throws_r",
        "opp_pitcher_throws_is_imputed",
        "same_hand_matchup", "opposite_hand_matchup", "matchup_is_imputed",
        "opp_pitcher_k_rate_14d", "opp_pitcher_bb_rate_14d",
        "opp_pitcher_xwoba_allowed_14d", "opp_pitcher_quality_is_imputed",
    }
    matchup_importance = [
        (k, round(v, 5)) for k, v in importances if k in matchup_feats
    ]

    artifact = {
        "model": xgb, "scaler": scaler, "features": feature_cols,
        "version": "MLB_HF_v3.1_phase2a",
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "samples": total_samples, "feature_count": len(feature_cols),
        "r2_train": round(r2_tr, 4), "r2_test": round(r2_te, 4),
        "mae_train": round(mae_tr, 4), "mae_test": round(mae_te, 4),
        "sc_hit_rate": round(sc_hit / max(1, sc_hit + sc_miss), 4),
        "matchup_hit_rate": round(mc_hit / max(1, mc_hit + mc_miss), 4),
        "top_features": importances[:25],
        "matchup_feature_importance": matchup_importance,
        "fully_zero_features": fully_zero,
    }
    out_path = os.path.join(MODEL_DIR, f"mlb_hf_{stat_name}.pkl")
    with open(out_path, "wb") as f:
        pickle.dump(artifact, f)
    elapsed = time.time() - t_start
    logger.info(
        f"{stat_name}: samples={total_samples} feat={len(feature_cols)} "
        f"R2_te={r2_te:.4f} MAE_te={mae_te:.4f} "
        f"matchup_hit={mc_hit}/{mc_hit+mc_miss} elapsed={elapsed:.1f}s"
    )

    # Update progress + report
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
        "sc_hit_rate": round(sc_hit / max(1, sc_hit + sc_miss), 4),
        "matchup_hit_rate": round(mc_hit / max(1, mc_hit + mc_miss), 4),
        "matchup_feature_importance": matchup_importance,
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
    ap.add_argument("--build-caches", action="store_true",
                     help="Just build resolver + SC caches and exit.")
    args = ap.parse_args()

    if args.status:
        p = _load_progress()
        print(f"Completed: {p['completed']}")
        print(f"Remaining: {[s for s in BATTER_STATS if s not in p['completed']]}")
        return

    if args.build_caches:
        db = _db()
        load_or_build_resolver(db)
        load_or_build_sc_caches(db)
        logger.info("Caches built — workdir ready for stat training.")
        return

    if args.all:
        for s in BATTER_STATS:
            train_one_stat(s, force=args.force)
            gc.collect()
    elif args.stat:
        train_one_stat(args.stat, force=args.force)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()

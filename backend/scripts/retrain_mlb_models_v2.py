"""
MLB HF v2.0 retrain — adds Statcast quality-of-contact features
================================================================
Differences vs v1 (`retrain_mlb_models.py`):
  • Pre-builds an in-memory (mlbam_id, game_date) → SC dict lookup
    for batters and pitchers.
  • Resolves mlbam_id per player via mlb_player_identity_map first,
    then a normalized-name fallback on statcast collections.
  • For each training sample (target game), looks up the SC features
    for that player ON the target's game_date, NOT the prediction
    date — so we use the snapshot the model would have had at
    prediction time. Missing → imputed defaults (handled inside
    `_build_friction_features`).
  • Saves new artifacts to /app/backend/models/mlb_hf/ with version
    tag `MLB_HF_v2.0_statcast`. Old artifacts must be backed up by
    the caller BEFORE running this script.
"""
import sys, os
sys.path.insert(0, '/app/backend')
os.chdir('/app/backend')

# 2026-04-29 — HARD LOCK GUARD. If `/app/backend/models/mlb_hf/.LOCKED`
# exists, this script must refuse to run. To unlock, the operator must
# manually delete the lock file (root permission). Bypassing this guard
# requires deliberate human action.
from services.mlb_model_lock import enforce_lock
enforce_lock(action="retrain_mlb_models_v2")

import pymongo, numpy as np, pickle, logging, gc, json
from collections import defaultdict
from datetime import datetime, timezone
from xgboost import XGBRegressor
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import r2_score, mean_absolute_error

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
logger = logging.getLogger(__name__)

client = pymongo.MongoClient(os.environ.get('MONGO_URL', 'mongodb://localhost:27017'))
db = client[os.environ.get('DB_NAME', 'pick_vision')]
hub = db.mlb_master_hub_2026
# NOTE: `mlb_historical_logs` is intentionally NOT referenced here —
# the rehydrated `mlb_master_hub_2026.bdl_game_logs` is the sole
# source of truth as of 2026-04-29.

import services.mlb_high_friction_model as hfm
hfm._mlb_hf_instance = None
model = hfm.get_mlb_high_friction_model(db)

# Pre-load master_hub splits keyed by bdl_id so the per-player join is O(1).
logger = logging.getLogger(__name__)  # ensure it's defined before this block
logger.info("Loading master_hub split blocks (bdl_id → splits dict)...")
hub_splits: dict = {}
for h in hub.find({'bdl_id': {'$ne': None}},
                    {'_id': 0, 'bdl_id': 1, 'team': 1, 'is_pitcher': 1, 'is_batter': 1,
                     'vs_left': 1, 'vs_right': 1, 'home_splits': 1, 'away_splits': 1,
                     'mlb_id': 1, 'mlbam_id': 1, 'statcast_id': 1, 'player_name': 1,
                     'display_name': 1}):
    try:
        bid = int(h['bdl_id'])
    except (TypeError, ValueError):
        continue
    hub_splits[bid] = h
logger.info(f"  hub splits: {len(hub_splits):,} entries")

STATS = [
    'hits', 'total_bases', 'rbis', 'runs', 'pitcher_strikeouts',
    'home_runs', 'stolen_bases', 'strikeouts', 'doubles', 'walks',
    'singles', 'hits+runs+rbis', 'earned_runs', 'hits_allowed', 'pitcher_walks',
]
# 2026-05-01 — Optional CLI/env filter for resuming after partial run.
# Pass `MLB_HF_STATS=foo,bar,baz` to train only those stats. The lookup
# tables and PA cache are still built once; this just filters which
# target stats actually iterate.
_stat_filter = os.environ.get("MLB_HF_STATS", "").strip()
if _stat_filter:
    requested = {s.strip() for s in _stat_filter.split(",") if s.strip()}
    STATS = [s for s in STATS if s in requested]
    logging.getLogger(__name__).warning(
        f"MLB_HF_STATS env filter active → training only: {STATS}"
    )
PITCHER_STATS = {'pitcher_strikeouts', 'pitcher_walks', 'hits_allowed',
                 'earned_runs', 'pitcher_outs'}

MODEL_DIR = '/app/backend/models/mlb_hf'
os.makedirs(MODEL_DIR, exist_ok=True)

# =============================================================================
# Pre-build identity + Statcast lookups in memory
# =============================================================================
logger.info("Building identity-map (bdl_id → mlbam_id)...")
bdl_to_mlbam: dict = {}
for m in db.mlb_player_identity_map.find(
        {"bdl_id": {"$ne": None}, "mlb_id": {"$ne": None}},
        {"_id": 0, "bdl_id": 1, "mlb_id": 1, "statcast_id": 1}):
    bdl = m.get("bdl_id")
    mid = m.get("statcast_id") or m.get("mlb_id")
    if bdl is not None and mid is not None:
        try:
            bdl_to_mlbam[int(bdl)] = int(mid)
        except (TypeError, ValueError):
            continue
logger.info(f"  identity map: {len(bdl_to_mlbam):,} entries")

logger.info("Building name → mlbam_id fallbacks...")
name_to_mlbam_batter: dict = {}
for d in db.mlb_statcast_player_features.find({}, {"_id": 0,
        "player_id": 1, "player_name": 1}):
    nm = (d.get("player_name") or "").lower().strip()
    if nm and nm not in name_to_mlbam_batter:
        name_to_mlbam_batter[nm] = d["player_id"]
name_to_mlbam_pitcher: dict = {}
for d in db.mlb_statcast_pitcher_features.find({}, {"_id": 0,
        "pitcher_id": 1, "pitcher_name": 1}):
    nm = (d.get("pitcher_name") or "").lower().strip()
    if nm and nm not in name_to_mlbam_pitcher:
        name_to_mlbam_pitcher[nm] = d["pitcher_id"]
logger.info(f"  batter name index: {len(name_to_mlbam_batter):,}")
logger.info(f"  pitcher name index: {len(name_to_mlbam_pitcher):,}")

logger.info("Loading Statcast batter features into memory...")
sc_batter: dict = defaultdict(dict)  # mlbam_id → date → feature dict
for d in db.mlb_statcast_player_features.find(
        {}, {"_id": 0, "player_id": 1, "game_date": 1,
             "rolling_7": 1, "rolling_14": 1, "rolling_30": 1,
             "season_window": 1}):
    pid = d.get("player_id"); gd = d.get("game_date")
    if pid is None or not gd: continue
    sc_batter[pid][gd] = {
        "rolling_7": d.get("rolling_7") or {},
        "rolling_14": d.get("rolling_14") or {},
        "rolling_30": d.get("rolling_30") or {},
        "season_window": d.get("season_window") or {},
    }
logger.info(f"  batter SC: {sum(len(v) for v in sc_batter.values()):,} (player,date) keys "
            f"across {len(sc_batter):,} players")

logger.info("Loading Statcast pitcher features into memory...")
sc_pitcher: dict = defaultdict(dict)
for d in db.mlb_statcast_pitcher_features.find(
        {}, {"_id": 0, "pitcher_id": 1, "game_date": 1,
             "rolling_14": 1, "rolling_30": 1, "season_window": 1}):
    pid = d.get("pitcher_id"); gd = d.get("game_date")
    if pid is None or not gd: continue
    sc_pitcher[pid][gd] = {
        "rolling_14": d.get("rolling_14") or {},
        "rolling_30": d.get("rolling_30") or {},
        "season_window": d.get("season_window") or {},
    }
logger.info(f"  pitcher SC: {sum(len(v) for v in sc_pitcher.values()):,} (pitcher,date) keys "
            f"across {len(sc_pitcher):,} pitchers")

# =============================================================================
# 2026-05-15 — Phase 2A: per-(batter, game_date) opposing pitcher resolver
# =============================================================================
# Build server-side via aggregation to avoid streaming 1.6M raw rows
# into the Python process. We group statcast_raw by
# (batter, game_date) and keep the FIRST observation (lowest
# at_bat_number → lowest pitch_number) per pair. That's the
# opposing-game starter from the batter's perspective.
#
# Output: three compact maps, all in-process:
#   • batter_first_pitcher[(b, gd)] = opp_pitcher_mlbam_id
#   • batter_stand[(b, gd)]         = "L" | "R"  (per-game)
#   • pitcher_throws[pid]           = "L" | "R"  (stable)
#
# The aggregation runs in MongoDB, returning ~N pair-level docs
# (where N ≈ unique batter-games, ~1M rows max). Pure scan of those
# docs into in-memory dicts is the only Python-side cost.
logger.info("Aggregating mlb_statcast_raw → per-game opp-pitcher resolver…")
batter_first_pitcher: dict = {}
batter_stand: dict = {}
pitcher_throws: dict = {}
agg_pipe = [
    {"$match": {
        "batter": {"$ne": None}, "pitcher": {"$ne": None},
        "game_date": {"$ne": None},
    }},
    # Order by earliest plate appearance per game so $first picks
    # the opener (the opposing starter we want).
    {"$sort": {"batter": 1, "game_date": 1,
                "at_bat_number": 1, "pitch_number": 1}},
    {"$group": {
        "_id": {"b": "$batter", "gd": "$game_date"},
        "p": {"$first": "$pitcher"},
        "stand": {"$first": "$stand"},
        "p_throws": {"$first": "$p_throws"},
    }},
]
n_pairs = 0
try:
    for d in db.mlb_statcast_raw.aggregate(
            agg_pipe, allowDiskUse=True, batchSize=2000):
        _id = d.get("_id") or {}
        b = _id.get("b"); gd = _id.get("gd"); p = d.get("p")
        if b is None or p is None or not gd:
            continue
        try:
            b = int(b); p = int(p)
        except (TypeError, ValueError):
            continue
        batter_first_pitcher[(b, gd)] = p
        s = d.get("stand")
        if s:
            batter_stand[(b, gd)] = str(s).strip().upper()[:1]
        if p not in pitcher_throws:
            t = d.get("p_throws")
            if t:
                pitcher_throws[p] = str(t).strip().upper()[:1]
        n_pairs += 1
        if n_pairs % 100000 == 0:
            logger.info(f"  aggregated pairs streamed: {n_pairs:,}")
except Exception as exc:  # noqa: BLE001
    logger.error(
        f"[PHASE2A] statcast_raw aggregation failed: {exc!r} — "
        f"continuing with empty matchup resolver (rows train with "
        f"imputed flags)."
    )
logger.info(
    f"  → {len(batter_first_pitcher):,} (batter,date) pairs, "
    f"{len(pitcher_throws):,} pitcher-throws entries"
)

# 2026-04-29 v2.1 — Per-PA Statcast cache (mlb_statcast_raw)
logger.info("Loading mlb_statcast_raw → PA cache …")
from services.mlb_pa_features import MLBPACache
pa_cache = MLBPACache()
n_raw = pa_cache.load_from_db(db)
logger.info(f"  PA cache: {n_raw:,} rows  →  "
            f"{pa_cache.stats()['batters']:,} batters / "
            f"{pa_cache.stats()['pitchers']:,} pitchers")


def _resolve_mlbam(player: dict, *, pitcher: bool) -> int | None:
    bdl = player.get("bdl_id") or player.get("bdl_player_id") or player.get("player_id")
    try: bdl = int(bdl) if bdl is not None else None
    except (TypeError, ValueError): bdl = None
    if bdl is not None and bdl in bdl_to_mlbam:
        return bdl_to_mlbam[bdl]
    nm = (player.get("display_name") or player.get("player_name") or "").lower().strip()
    if nm:
        idx = name_to_mlbam_pitcher if pitcher else name_to_mlbam_batter
        if nm in idx: return idx[nm]
    return None


def _date_str(log: dict) -> str | None:
    """Extract YYYY-MM-DD from a bdl game log."""
    d = log.get("date")
    if not d: return None
    s = str(d)
    return s[:10] if len(s) >= 10 else None


def _sc_lookup_batter(mlbam_id: int | None, gdate: str | None) -> dict | None:
    if mlbam_id is None or not gdate: return None
    by_date = sc_batter.get(mlbam_id)
    if not by_date: return None
    if gdate in by_date: return by_date[gdate]
    # Fallback: most recent SC doc <= gdate
    candidates = [d for d in by_date if d <= gdate]
    if not candidates: return None
    return by_date[max(candidates)]


def _sc_lookup_pitcher(mlbam_id: int | None, gdate: str | None) -> dict | None:
    if mlbam_id is None or not gdate: return None
    by_date = sc_pitcher.get(mlbam_id)
    if not by_date: return None
    if gdate in by_date: return by_date[gdate]
    candidates = [d for d in by_date if d <= gdate]
    if not candidates: return None
    return by_date[max(candidates)]


# =============================================================================
# Train each stat
# =============================================================================
report: dict = {"trained_at": datetime.now(timezone.utc).isoformat(),
                 "version": "MLB_HF_v3.1_phase2a",
                 "stats": {}}

for stat_name in STATS:
    is_pitcher_stat = stat_name in PITCHER_STATS
    X_chunks, y_chunks = [], []
    feature_cols = None
    total_samples = 0
    sc_hit = 0
    sc_miss = 0

    # Source of truth: master_hub.bdl_game_logs (rehydrated)
    # `mlb_historical_logs` is deprecated and intentionally ignored here.
    player_cursor = hub.find(
        {'bdl_id': {'$ne': None}},
        {'_id': 0, 'bdl_id': 1, 'player_name': 1, 'display_name': 1,
         'team': 1, 'is_pitcher': 1, 'is_batter': 1,
         'vs_left': 1, 'vs_right': 1, 'home_splits': 1, 'away_splits': 1,
         'mlb_id': 1, 'mlbam_id': 1, 'statcast_id': 1,
         'bdl_game_logs': 1}
    ).batch_size(50)

    player_count = 0
    for player in player_cursor:
        bdl_id = player.get('bdl_id')
        if bdl_id is None:
            continue
        try: bdl_id = int(bdl_id)
        except (TypeError, ValueError): continue

        logs = player.get('bdl_game_logs') or []
        # Sort by date desc (most recent first); training windows then
        # use logs[i] as the target with logs[i+1:] as history.
        logs = sorted(logs, key=lambda x: (x.get('date') or '', x.get('game_id') or 0), reverse=True)
        if len(logs) < 12:
            continue
        team = player.get('team')
        player_count += 1
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

            hist_vals = [model._get_stat_value(g, stat_name) for g in history[:20]]
            hist_vals = [v for v in hist_vals if v is not None]
            if len(hist_vals) < 5:
                continue

            line = float(np.median(hist_vals))
            gdate = _date_str(target_game)

            sc_b = None; sc_p = None
            pa_b = None; pa_p = None
            if is_pitcher_stat:
                sc_p = _sc_lookup_pitcher(mlbam_id, gdate)
                if mlbam_id is not None and gdate:
                    pa_p = pa_cache.pitcher_features(int(mlbam_id), gdate)
            else:
                sc_b = _sc_lookup_batter(mlbam_id, gdate)
                if mlbam_id is not None and gdate:
                    pa_b = pa_cache.batter_features(int(mlbam_id), gdate)
            if sc_b is not None or sc_p is not None: sc_hit += 1
            else: sc_miss += 1

            # 2026-05-15 — Phase 2A: resolve opposing pitcher for BATTER
            # stat retrain. Pitcher-stat retrain is out of scope this
            # pass (skip the lookup entirely for those targets).
            bh_t = None
            opt_t = None
            opp_pitcher_feats_t = None
            if (not is_pitcher_stat and mlbam_id is not None
                    and gdate is not None):
                key = (int(mlbam_id), gdate)
                bh_t = batter_stand.get(key)
                opp_pid = batter_first_pitcher.get(key)
                if opp_pid is not None:
                    opt_t = pitcher_throws.get(opp_pid)
                    opp_pitcher_feats_t = _sc_lookup_pitcher(opp_pid, gdate)

            features = model._build_friction_features(
                player, history, stat_name,
                opponent=None, park_team=team,
                dk_odds=None, line=line,
                statcast_features=sc_b,
                pitcher_statcast_features=sc_p,
                pa_batter_features=pa_b,
                pa_pitcher_features=pa_p,
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
            logger.info(f"  {stat_name}: {player_count} players, {total_samples} samples, "
                        f"sc_hit={sc_hit}/{sc_hit + sc_miss}")

    if total_samples < 100 or feature_cols is None:
        logger.warning(f"{stat_name}: {total_samples} samples, SKIP")
        report["stats"][stat_name] = {"samples": total_samples, "skipped": True}
        continue

    X = np.vstack(X_chunks); y = np.concatenate(y_chunks)
    del X_chunks, y_chunks; gc.collect()

    # Imputation rate per feature (on the FULL training matrix).
    # A feature is considered "imputed/zero" when its value equals the
    # default (0.0). We surface the feature columns whose 100% of
    # samples are zero so the operator can verify upstream coverage.
    zero_rate = (X == 0).mean(axis=0)
    full_zero_features = [feature_cols[i] for i, z in enumerate(zero_rate) if z >= 0.999]

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # Shuffle indices for an i.i.d. test split — sequential cursor
    # order otherwise places one player per chunk, biasing the split.
    rng = np.random.RandomState(42)
    perm = rng.permutation(len(X))
    X_scaled = X_scaled[perm]; y = y[perm]
    split = int(len(X) * 0.8)
    X_tr, X_te = X_scaled[:split], X_scaled[split:]
    y_tr, y_te = y[:split], y[split:]

    xgb = XGBRegressor(
        n_estimators=200, max_depth=5, learning_rate=0.08,
        subsample=0.8, colsample_bytree=0.8,
        reg_alpha=0.1, reg_lambda=1.0, random_state=42, verbosity=0,
    )
    xgb.fit(X_tr, y_tr)

    y_te_pred = xgb.predict(X_te)
    y_tr_pred = xgb.predict(X_tr)
    r2_te = r2_score(y_te, y_te_pred)
    mae_te = mean_absolute_error(y_te, y_te_pred)
    r2_tr = r2_score(y_tr, y_tr_pred)
    mae_tr = mean_absolute_error(y_tr, y_tr_pred)

    # Top feature importances
    importances = list(zip(feature_cols, xgb.feature_importances_.tolist()))
    importances.sort(key=lambda x: -x[1])
    top_feats = importances[:25]

    data = {
        'model': xgb, 'scaler': scaler, 'features': feature_cols,
        'version': 'MLB_HF_v3.1_phase2a',
        'trained_at': datetime.now(timezone.utc).isoformat(),
        'samples': total_samples, 'feature_count': len(feature_cols),
        'r2_train': round(r2_tr, 4), 'r2_test': round(r2_te, 4),
        'mae_train': round(mae_tr, 4), 'mae_test': round(mae_te, 4),
        'sc_hit_rate': round(sc_hit / max(1, sc_hit + sc_miss), 4),
        'sc_hit_count': sc_hit, 'sc_miss_count': sc_miss,
        'top_features': top_feats,
        'fully_zero_features': full_zero_features,
    }
    path = os.path.join(MODEL_DIR, f'mlb_hf_{stat_name}.pkl')
    with open(path, 'wb') as f:
        pickle.dump(data, f)

    report["stats"][stat_name] = {
        "samples": total_samples,
        "feature_count": len(feature_cols),
        "r2_train": round(r2_tr, 4),
        "r2_test": round(r2_te, 4),
        "mae_test": round(mae_te, 4),
        "sc_hit_rate": round(sc_hit / max(1, sc_hit + sc_miss), 4),
        "fully_zero_features": full_zero_features,
        "top_features": [[k, round(v, 5)] for k, v in top_feats[:20]],
    }

    del X, y, X_scaled, X_tr, X_te, y_tr, y_te, xgb
    gc.collect()

    logger.info(f"{stat_name:22s} | {total_samples:>8} samples | {len(feature_cols)} feat | "
                f"R2_tr={r2_tr:.4f} | R2_te={r2_te:.4f} | MAE_te={mae_te:.4f} | "
                f"sc_hit={sc_hit}/{sc_hit + sc_miss}")

# Persist a JSON report for inspection. Merge with any prior partial
# report so a resumed run preserves earlier stats' metrics.
report_path = os.path.join(MODEL_DIR, "_train_report_v3.json")
if os.path.exists(report_path):
    try:
        with open(report_path, "r") as fh:
            prior = json.load(fh) or {}
        prior_stats = (prior.get("stats") or {})
        for k, v in prior_stats.items():
            report["stats"].setdefault(k, v)
    except Exception:
        pass
with open(report_path, "w") as fh:
    json.dump(report, fh, indent=2, default=str)
logger.info(f"REPORT WRITTEN → {report_path}")
logger.info("ALL MODELS RETRAINED — MLB_HF_v3.0_bayes")
client.close()

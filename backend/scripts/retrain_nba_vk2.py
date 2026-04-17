"""
NBA Vegas Killer v2 — 3-season retrain with recency weighting.
Mirrors the MLB retrain pattern: batched cursor, sliding-window feature
extraction per player, single XGBRegressor per stat, weighted by season.

Data  : bdl_historical_game_logs (seasons 2022, 2023, 2024)
Output: /app/backend/models/vk2_{stat}.pkl  (model + scaler + features + sigma)

No live-data I/O. Read-only from historical collection.
"""
import os, sys, pickle, gc, logging, math, time
from datetime import datetime, timezone
from collections import defaultdict

sys.path.insert(0, '/app/backend')
os.chdir('/app/backend')

import pymongo, numpy as np
from xgboost import XGBRegressor
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import r2_score, mean_absolute_error, mean_squared_error

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
log = logging.getLogger('vk2_train')

client = pymongo.MongoClient(os.environ.get('MONGO_URL', 'mongodb://localhost:27017'))
db = client[os.environ.get('DB_NAME', 'pick_vision')]
coll = db.bdl_historical_game_logs
adv_coll = db.bdl_advanced_stats

# ---------- Config ----------
SEASONS = [2022, 2023, 2024]          # last 3 seasons
SEASON_WEIGHTS = {2024: 1.00, 2023: 0.85, 2022: 0.70}
MIN_GAMES_PER_PLAYER = 12
ROLLING_WINDOW = 20
MODEL_DIR = '/app/backend/models'
os.makedirs(MODEL_DIR, exist_ok=True)

STATS = {
    'PTS': 'pts',
    'REB': 'reb',
    'AST': 'ast',
    '3PM': 'fg3m',
    'PRA': 'pra',  # synthesized
}

# Advanced-stat fields to incorporate as rolling features
ADV_FIELDS = [
    'usage_percentage', 'true_shooting_percentage', 'effective_field_goal_percentage',
    'pace', 'possessions', 'offensive_rating', 'defensive_rating', 'net_rating',
    'assist_percentage', 'rebound_percentage', 'defensive_rebound_percentage',
    'offensive_rebound_percentage', 'turnover_ratio', 'pie',
    'touches', 'passes', 'distance', 'speed',
    'pct_pts_paint', 'pct_pts_3pt', 'pct_pts_fast_break', 'pct_pts_free_throw',
    'deflections', 'contested_shots', 'pct_fga',
]


# ---------- Advanced stats preload ----------
def preload_advanced_stats():
    """Build {(player_id, game_id): {adv fields}} dict once."""
    log.info('Preloading bdl_advanced_stats...')
    t0 = time.monotonic()
    adv_map = {}
    projection = {'_id': 0, 'player_id': 1, 'game_id': 1}
    for f in ADV_FIELDS: projection[f] = 1
    for doc in adv_coll.find({'season': {'$in': SEASONS + [2020, 2021]}}, projection).batch_size(5000):
        pid = doc.get('player_id'); gid = doc.get('game_id')
        if pid is None or gid is None: continue
        adv_map[(pid, gid)] = doc
    log.info(f'Loaded {len(adv_map):,} advanced-stat rows in {time.monotonic()-t0:.1f}s')
    return adv_map


# ---------- Feature builder (pure Python, fast) ----------
def build_features(history_logs, target_game=None, adv_map=None):
    """history_logs: chronological-descending (newest first) game logs BEFORE
    the target game. Returns a flat dict of ~100 features (basic + advanced)."""
    if len(history_logs) < 5:
        return None
    feats = {}
    # Windowed means/std for every canonical stat
    for stat_key, field in [
        ('pts', 'pts'), ('reb', 'reb'), ('ast', 'ast'),
        ('fg3m', 'fg3m'), ('fga', 'fga'), ('fg3a', 'fg3a'),
        ('fta', 'fta'), ('min_played', 'min'),
    ]:
        vals = []
        for g in history_logs[:ROLLING_WINDOW]:
            v = g.get(field)
            if field == 'min' and isinstance(v, str):
                # "31:24" format
                try:
                    mm, ss = v.split(':')
                    v = float(mm) + float(ss) / 60.0
                except Exception:
                    v = None
            if v is None:
                continue
            vals.append(float(v))
        for w in (3, 5, 10, 20):
            window_vals = vals[:w]
            if len(window_vals) >= 2:
                arr = np.asarray(window_vals, dtype=np.float32)
                feats[f'{stat_key}_L{w}_mean'] = float(arr.mean())
                feats[f'{stat_key}_L{w}_std'] = float(arr.std(ddof=1)) if len(arr) >= 2 else 0.0
            else:
                feats[f'{stat_key}_L{w}_mean'] = 0.0
                feats[f'{stat_key}_L{w}_std'] = 0.0
        if vals:
            feats[f'{stat_key}_season_mean'] = float(np.mean(vals))
        else:
            feats[f'{stat_key}_season_mean'] = 0.0

    # PRA series (synthesized)
    pra_vals = []
    for g in history_logs[:ROLLING_WINDOW]:
        p = g.get('pts'); r = g.get('reb'); a = g.get('ast')
        if p is not None and r is not None and a is not None:
            pra_vals.append(float(p) + float(r) + float(a))
    for w in (3, 5, 10, 20):
        wv = pra_vals[:w]
        feats[f'pra_L{w}_mean'] = float(np.mean(wv)) if wv else 0.0

    # Basic efficiency
    for ef_field in ('fg_pct', 'fg3_pct', 'ft_pct'):
        vv = [g.get(ef_field) for g in history_logs[:10] if g.get(ef_field) is not None]
        feats[f'{ef_field}_L10_mean'] = float(np.mean(vv)) if vv else 0.0

    # Volume proxies
    vol = [g.get('fga', 0) + 0.44 * g.get('fta', 0) for g in history_logs[:10]
           if g.get('fga') is not None and g.get('fta') is not None]
    feats['usg_proxy_L10'] = float(np.mean(vol)) if vol else 0.0

    # Recent momentum (EWMA on pts)
    pts_vals = [g.get('pts') for g in history_logs[:10] if g.get('pts') is not None]
    if pts_vals:
        alpha = 0.35
        ewma = pts_vals[0]
        for v in pts_vals[1:]:
            ewma = alpha * v + (1 - alpha) * ewma
        feats['pts_ewma'] = float(ewma)
    else:
        feats['pts_ewma'] = 0.0

    # Game-count features
    feats['logs_used'] = float(len(history_logs[:ROLLING_WINDOW]))

    # -------- ADVANCED ROLLING FEATURES (from bdl_advanced_stats) ----------
    # Rolling means of each advanced field over L5 and L10 of the history window.
    # Missing advanced rows → skip that game for those fields only (left-join).
    if adv_map is not None:
        for adv_f in ADV_FIELDS:
            l5_vals, l10_vals = [], []
            adv_hit_l10 = 0
            for idx, g in enumerate(history_logs[:10]):
                key = (g.get('player_id'), g.get('game_id'))
                a = adv_map.get(key)
                if not a: continue
                v = a.get(adv_f)
                if v is None: continue
                try: v = float(v)
                except (TypeError, ValueError): continue
                if idx < 5: l5_vals.append(v)
                l10_vals.append(v)
                adv_hit_l10 += 1
            feats[f'adv_{adv_f}_L5_mean'] = float(np.mean(l5_vals)) if l5_vals else 0.0
            feats[f'adv_{adv_f}_L10_mean'] = float(np.mean(l10_vals)) if l10_vals else 0.0
        # Coverage flag: how many of the last 10 games had advanced data
        adv_coverage = 0
        for g in history_logs[:10]:
            if (g.get('player_id'), g.get('game_id')) in adv_map:
                adv_coverage += 1
        feats['adv_coverage_L10'] = float(adv_coverage)

    # Target-game context
    if target_game is not None:
        feats['is_home'] = 1.0 if target_game.get('home_team_id') == target_game.get('team_id') else 0.0
        # target minutes proxy from rolling avg (we use pre-game info only)
        feats['minutes_proxy'] = feats.get('min_played_L5_mean', 0.0)

    return feats


# ---------- Data assembly per stat (single pass over players) ----------
def build_training_matrix(stat_label, stat_field):
    """Returns (X, y, sample_weights, feature_cols)."""
    log.info(f'[{stat_label}] building training matrix...')
    t0 = time.monotonic()

    pipeline = [
        {'$match': {'season': {'$in': SEASONS}}},
        {'$sort': {'player_id': 1, 'game_id': 1}},
    ]
    # Group in-memory by player_id (indexes exist, so sort is cheap)
    current_pid = None
    current_logs = []

    feature_cols = None
    X_chunks, y_chunks, w_chunks = [], [], []
    total_samples = 0
    players_used = 0

    def flush_player(pid, logs_chrono):
        """logs_chrono: chronological ascending. For each game i (except first N),
        target = stat at game i, features = games [i-1 .. i-20] (newest-first)."""
        nonlocal feature_cols, total_samples
        if len(logs_chrono) < MIN_GAMES_PER_PLAYER:
            return
        # Produce samples by sweeping forward
        px, py, pw = [], [], []
        for i in range(5, len(logs_chrono)):
            tgt = logs_chrono[i]
            if tgt.get('season') not in SEASON_WEIGHTS:
                continue
            # Target value
            if stat_field == 'pra':
                p = tgt.get('pts'); r = tgt.get('reb'); a = tgt.get('ast')
                if p is None or r is None or a is None:
                    continue
                tval = float(p) + float(r) + float(a)
            else:
                v = tgt.get(stat_field)
                if v is None:
                    continue
                tval = float(v)
            # Features from reversed history
            history_desc = list(reversed(logs_chrono[max(0, i - ROLLING_WINDOW):i]))
            if len(history_desc) < 5:
                continue
            feats = build_features(history_desc, target_game=tgt)
            if feats is None:
                continue
            if feature_cols is None:
                feature_cols = sorted(feats.keys())
            row = [feats.get(c, 0.0) for c in feature_cols]
            px.append(row)
            py.append(tval)
            pw.append(SEASON_WEIGHTS.get(tgt.get('season'), 0.4))
        if px:
            X_chunks.append(np.asarray(px, dtype=np.float32))
            y_chunks.append(np.asarray(py, dtype=np.float32))
            w_chunks.append(np.asarray(pw, dtype=np.float32))

    cursor = coll.aggregate(pipeline, allowDiskUse=True, batchSize=5000)
    seen_players = 0
    for doc in cursor:
        pid = doc.get('player_id')
        if pid != current_pid:
            if current_pid is not None and current_logs:
                flush_player(current_pid, current_logs)
                players_used += 1
                seen_players += 1
                if seen_players % 100 == 0:
                    gc.collect()
            current_pid = pid
            current_logs = []
        current_logs.append(doc)
    # final flush
    if current_pid is not None and current_logs:
        flush_player(current_pid, current_logs)
        players_used += 1

    if not X_chunks:
        return None, None, None, None

    X = np.vstack(X_chunks)
    y = np.concatenate(y_chunks)
    w = np.concatenate(w_chunks)
    total_samples = len(y)
    elapsed = time.monotonic() - t0
    log.info(f'[{stat_label}] matrix ready: X={X.shape}  y={y.shape}  '
             f'players={players_used}  samples={total_samples}  '
             f'weighted_sum={float(w.sum()):.1f}  elapsed={elapsed:.1f}s')
    return X, y, w, feature_cols


# ---------- Train + calibrate per stat ----------
def train_one(stat_label, stat_field):
    X, y, sw, feature_cols = build_training_matrix(stat_label, stat_field)
    if X is None:
        log.warning(f'[{stat_label}] no samples; SKIP')
        return

    # Temporal split: first 80% of rows (sorted by player_id, game_id in pipeline)
    # is close enough to temporal since game_id is monotonic within season
    # and seasons are ordered. For cleaner temporal split we re-sort rows by
    # season-attached sample_weight: rows with weight 0.70 (2022) -> train,
    # 0.85 (2023) -> train, 1.00 (2024) -> test.
    test_mask = sw >= 0.99  # 2024 rows
    train_mask = ~test_mask
    X_tr, y_tr, w_tr = X[train_mask], y[train_mask], sw[train_mask]
    X_te, y_te = X[test_mask], y[test_mask]

    # Standard scaler
    scaler = StandardScaler()
    X_tr_s = scaler.fit_transform(X_tr)
    X_te_s = scaler.transform(X_te)

    xgb = XGBRegressor(
        n_estimators=300, max_depth=5, learning_rate=0.07,
        subsample=0.85, colsample_bytree=0.85,
        reg_alpha=0.1, reg_lambda=1.0,
        random_state=42, verbosity=0, n_jobs=4, tree_method='hist',
    )
    t0 = time.monotonic()
    xgb.fit(X_tr_s, y_tr, sample_weight=w_tr)
    train_time = time.monotonic() - t0

    # Metrics on held-out 2024
    y_pred_tr = xgb.predict(X_tr_s)
    y_pred_te = xgb.predict(X_te_s)
    r2_tr = r2_score(y_tr, y_pred_tr, sample_weight=w_tr)
    r2_te = r2_score(y_te, y_pred_te)
    mae_te = mean_absolute_error(y_te, y_pred_te)
    rmse_te = math.sqrt(mean_squared_error(y_te, y_pred_te))

    # Empirical residual SD on validation set (for prob_over CDF)
    residuals_te = (y_te - y_pred_te)
    sigma_emp = float(residuals_te.std(ddof=1))

    # Rough calibration audit: bucket projections into deciles and measure
    # empirical hit rate vs theoretical Gaussian prob at a held-out "line =
    # mean(bucket)" — i.e., are we right on average?
    bucket_edges = np.quantile(y_pred_te, [0.1, 0.25, 0.5, 0.75, 0.9])
    calib_rows = []
    for edge in bucket_edges:
        mask = (y_pred_te >= edge - 0.5) & (y_pred_te <= edge + 0.5)
        if mask.sum() < 20: continue
        actual_over_rate = float((y_te[mask] > edge).mean())
        expected_over_rate = 0.5  # projection == line → expected 50%
        calib_rows.append({
            'line': float(edge), 'n': int(mask.sum()),
            'actual_over': round(actual_over_rate, 3),
            'expected': 0.5,
        })

    # Top-20 feature importances
    fi = xgb.feature_importances_
    top = sorted(zip(feature_cols, fi), key=lambda x: -x[1])[:20]
    top_features = [(n, float(v)) for n, v in top]

    # Persist
    payload = {
        'stat_label': stat_label,
        'stat_field': stat_field,
        'version': 'NBA_VK_v2_3yr_weighted',
        'trained_at': datetime.now(timezone.utc).isoformat(),
        'seasons_used': SEASONS,
        'season_weights': SEASON_WEIGHTS,
        'model': xgb,
        'scaler': scaler,
        'features': feature_cols,
        'feature_count': len(feature_cols),
        'samples_train': int(X_tr.shape[0]),
        'samples_test': int(X_te.shape[0]),
        'weighted_sum_train': float(w_tr.sum()),
        'train_seconds': round(train_time, 2),
        'r2_train_weighted': round(r2_tr, 4),
        'r2_test': round(r2_te, 4),
        'mae_test': round(mae_te, 4),
        'rmse_test': round(rmse_te, 4),
        'residual_sigma_empirical': round(sigma_emp, 4),
        'calibration_buckets': calib_rows,
        'top_features': top_features,
    }
    out_path = os.path.join(MODEL_DIR, f'vk2_{stat_label.lower()}.pkl')
    with open(out_path, 'wb') as f:
        pickle.dump(payload, f)

    log.info(
        f'[{stat_label}] TRAINED in {train_time:.1f}s | '
        f'features={len(feature_cols)} train={X_tr.shape[0]} test={X_te.shape[0]} | '
        f'R2_test={r2_te:.4f}  MAE={mae_te:.3f}  RMSE={rmse_te:.3f}  '
        f'σ_residual={sigma_emp:.3f}'
    )
    return payload


# ---------- Main ----------
if __name__ == '__main__':
    t_all = time.monotonic()
    results = {}
    for label, field in STATS.items():
        log.info(f'=== {label} ({field}) ===')
        try:
            results[label] = train_one(label, field)
        except Exception as e:
            log.exception(f'[{label}] FAILED: {e}')
        gc.collect()
    total = time.monotonic() - t_all
    log.info(f'ALL MODELS DONE in {total:.1f}s')
    for label, r in results.items():
        if r is None: continue
        log.info(f'  {label}: R2={r["r2_test"]}  MAE={r["mae_test"]}  σ={r["residual_sigma_empirical"]}  '
                 f'train_samples={r["samples_train"]}  feat={r["feature_count"]}')
    client.close()

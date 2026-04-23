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
SEASONS = [2020, 2021, 2022, 2023, 2024]          # last 5 seasons (full DB history)
SEASON_WEIGHTS = {2024: 1.00, 2023: 0.85, 2022: 0.70, 2021: 0.55, 2020: 0.40}
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

# -----------------------------------------------------------------------------
# Pruned feature schema (2026-04-23) — from /reports/vk2_feature_audit.
#
# 52 features = union(core across 5 stats) ∪ (top representative of each
# |r|>=0.85 correlation cluster). Ablation confirmed removing bottom 60%
# of features costs essentially zero RMSE; this schema formalises that
# result as the new baseline BEFORE we add opponent-context features.
#
# Enabled via `python retrain_nba_vk2.py --pruned`. Writes sibling
# `vk2_{stat}_pruned.pkl` so production models remain untouched until
# the head-to-head comparison passes and you explicitly swap.
# -----------------------------------------------------------------------------
PRUNED_FEATURES = [
    # --- Base stat rolling / deviation family (26) -------------------
    'fga_L3_mean', 'fga_L3_std', 'fga_L5_std', 'fga_L20_std',
    'pra_L3_mean', 'pra_L20_mean',
    'pts_L3_std', 'pts_L5_std', 'pts_L10_std', 'pts_L20_std',
    'fg_pct_L10_mean',
    'fg3a_L3_mean', 'fg3a_L20_mean', 'fg3a_L5_std', 'fg3_pct_L10_mean',
    'ast_L3_mean', 'ast_L5_mean', 'ast_L20_mean', 'ast_L20_std',
    'reb_L3_mean', 'reb_L3_std', 'reb_L5_mean', 'reb_L5_std',
    'reb_L10_std', 'reb_L20_mean', 'reb_L20_std',
    # --- Minutes family (6) -----------------------------------------
    'min_played_L5_mean', 'min_played_L10_mean',
    'min_played_L20_mean',
    'min_played_L5_std', 'min_played_L10_std', 'min_played_L20_std',
    # --- Advanced tracking — kept (19) ------------------------------
    'adv_assist_percentage_L5_mean', 'adv_assist_percentage_L10_mean',
    'adv_contested_shots_L5_mean', 'adv_contested_shots_L10_mean',
    'adv_defensive_rebound_percentage_L5_mean',
    'adv_defensive_rebound_percentage_L10_mean',
    'adv_deflections_L5_mean', 'adv_deflections_L10_mean',
    'adv_distance_L5_mean',
    'adv_net_rating_L5_mean',
    'adv_passes_L5_mean',
    'adv_pct_pts_3pt_L5_mean', 'adv_pct_pts_3pt_L10_mean',
    'adv_pct_pts_paint_L5_mean', 'adv_pct_pts_paint_L10_mean',
    'adv_pie_L5_mean', 'adv_pie_L10_mean',
    'adv_possessions_L10_mean',
    'adv_rebound_percentage_L10_mean',
    'adv_missing_season',
]
assert len(PRUNED_FEATURES) == 52, (
    f'PRUNED_FEATURES must be exactly 52, got {len(PRUNED_FEATURES)}'
)

# NOTE (2026-04-23): the --usage (59-feat) and --minutes (69-feat)
# schema attempts were rolled back. Both caused +2 to +4 bias on
# low-line bench props (see reports/vk2_usage_segmented.json). The
# 52-feature fixed-mins pruned schema is the locked baseline; the
# opportunity-vs-efficiency decomposition now lives in the separate
# `scripts/train_nba_minutes_model.py` regression (expected-minutes
# model) which is composed downstream rather than merged into VK2.

# -----------------------------------------------------------------------------
# Opponent-context additions (2026-04-23). 14 features on top of the
# 52-feature pruned baseline → 66-feature "+opp" schema.
# Enabled via `--opponent` (requires `--pruned`). Writes
# `vk2_{stat}_opp.pkl`. Source module: `services.features.opponent_context`.
# -----------------------------------------------------------------------------
from services.features.opponent_context import (
    FEATURE_SCHEMA as OPPONENT_CONTEXT_FEATURES,
    build_opponent_context_store,
    resolve_opponent_team_id,
)
PRUNED_OPP_FEATURES = list(PRUNED_FEATURES) + list(OPPONENT_CONTEXT_FEATURES)
assert len(PRUNED_OPP_FEATURES) == 66

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
    for doc in adv_coll.find({'season': {'$in': SEASONS}}, projection).batch_size(5000):
        pid = doc.get('player_id'); gid = doc.get('game_id')
        if pid is None or gid is None: continue
        adv_map[(pid, gid)] = doc
    log.info(f'Loaded {len(adv_map):,} advanced-stat rows in {time.monotonic()-t0:.1f}s')
    return adv_map


# ---------- Feature builder (pure Python, fast) ----------
def build_features(history_logs, target_game=None, adv_map=None,
                   target_schema=None, opp_store=None):
    """history_logs: chronological-descending (newest first) game logs BEFORE
    the target game. Returns a flat dict of features.

    target_schema (optional set[str]): when provided, skip expensive
    feature-family loops whose outputs would be immediately discarded
    by the trainer's column mask. The function still emits SUPERSETS
    of the requested schema (cheap features are always computed), but
    the ADV block (24 fields × 10 games ≈ 47M ops over the full
    matrix) and unused per-stat rolling windows are short-circuited.
    Passing None preserves the legacy "compute everything" behaviour
    so existing callers are unaffected.

    opp_store (optional OpponentContextStore): when provided AND the
    target_game carries a resolvable (team_id, game_id), emits the
    14 opponent-context features from
    `services/features/opponent_context.py`. Enabled by `--opponent`
    (66-feature schema).
    """
    if len(history_logs) < 7:  # upstream guard; no behavioural change
        pass
    if len(history_logs) < 5:
        return None
    feats = {}

    # Build a cheap per-stat needed set so the inner loop can skip
    # entire stats whose features aren't in the target schema.
    def _stat_needed(stat_key):
        if target_schema is None:
            return True
        # Any rolling window / season / std output for this stat.
        for w in (3, 5, 10, 20):
            if f'{stat_key}_L{w}_mean' in target_schema:
                return True
            if f'{stat_key}_L{w}_std' in target_schema:
                return True
        return (f'{stat_key}_season_mean' in target_schema)
    # Windowed means/std for every canonical stat
    for stat_key, field in [
        ('pts', 'pts'), ('reb', 'reb'), ('ast', 'ast'),
        ('fg3m', 'fg3m'), ('fga', 'fga'), ('fg3a', 'fg3a'),
        ('fta', 'fta'), ('min_played', 'min'),
    ]:
        # Skip the entire per-stat computation when nothing in the
        # target schema references it. `min_played` is always retained
        # because the minutes-aware block below depends on a
        # fully-resolved minutes list and the small cost buys us
        # correctness when schema filters are applied.
        if not _stat_needed(stat_key) and stat_key != 'min_played':
            continue
        vals = []
        for g in history_logs[:ROLLING_WINDOW]:
            v = g.get(field)
            if field == 'min':
                # BDL game logs: `min` ships as a string. Seen formats:
                # - "30"       plain integer-minutes (2020–2026 production)
                # - "31:24"    legacy MM:SS (rare; defensive parse kept)
                # - 30 / 30.5  numeric (defensive)
                # None / empty → skip row.
                if isinstance(v, str):
                    s = v.strip()
                    if not s:
                        v = None
                    elif ':' in s:
                        try:
                            mm, ss = s.split(':')
                            v = float(mm) + float(ss) / 60.0
                        except Exception:
                            v = None
                    else:
                        try:
                            v = float(s)
                        except Exception:
                            v = None
                elif v is not None:
                    try:
                        v = float(v)
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

    # ---------------------------------------------------------------
    # Minutes opportunity-vs-efficiency decomposition (2026-04-23).
    # ROLLED BACK: the in-model per-minute / expected_minutes / usage
    # additions caused +2 to +4 over-prediction bias on low-line bench
    # props. The decomposition now lives in the separate
    # `scripts/train_nba_minutes_model.py` regression, which is composed
    # downstream (expected_minutes * per_min_rate) rather than merged
    # into VK2. The core `min_played_L{5,10,20}_{mean,std}` features
    # remain in the 52-feature pruned schema above.
    # ---------------------------------------------------------------

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

    # --------- OPPONENT CONTEXT FEATURES (2026-04-23) ---------------------
    # Emitted only when an opponent-context store is provided AND the
    # target game carries a resolvable (team_id, game_id). All 14
    # features are lagged: the target game's own stats never contribute.
    # See `services/features/opponent_context.py` for schema + invariants.
    if opp_store is not None and target_game is not None:
        tgt_team_id = target_game.get('team_id')
        tgt_game_id = target_game.get('game_id')
        if tgt_team_id is not None and tgt_game_id is not None:
            tgt_opp_id = resolve_opponent_team_id(
                opp_store, int(tgt_team_id), int(tgt_game_id),
            )
            opp_feats = opp_store.get_features(
                team_id=int(tgt_team_id),
                opponent_team_id=tgt_opp_id,
                game_id=int(tgt_game_id),
                game_date=target_game.get('date'),
                is_home=None,  # resolved internally from adv-stats row
            )
            feats.update(opp_feats)

    # -------- ADVANCED ROLLING FEATURES (from bdl_advanced_stats) ----------
    # Rolling means of each advanced field over L5 and L10 of the history window.
    # Missing advanced rows default to 0.0 ONLY when explicit missingness flags
    # are also emitted so the model can distinguish "real zero" from "no data":
    #   - adv_coverage_L10     : # of last-10 history games with any adv row
    #   - adv_missing_season   : fraction of last-10 history games from the
    #                             known season-wide gap (season 2023 has 0 rows)
    #   - adv_{field}_L{w}_miss: 1.0 if window had zero valid samples for field
    if adv_map is not None:
        # Schema-aware pruning (2026-04-23): when target_schema is
        # provided, restrict the ADV_FIELDS loop to just the fields
        # whose output features actually appear in the schema. This
        # is the dominant perf win — 102 adv features → the 16 the
        # pruned schema keeps → ~6× fewer dict lookups overall.
        if target_schema is not None:
            needed_adv_fields = [
                f for f in ADV_FIELDS
                if (f'adv_{f}_L5_mean' in target_schema
                    or f'adv_{f}_L10_mean' in target_schema
                    or f'adv_{f}_L5_miss' in target_schema
                    or f'adv_{f}_L10_miss' in target_schema)
            ]
        else:
            needed_adv_fields = list(ADV_FIELDS)
        for adv_f in needed_adv_fields:
            l5_vals, l10_vals = [], []
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
            feats[f'adv_{adv_f}_L5_mean'] = float(np.mean(l5_vals)) if l5_vals else 0.0
            feats[f'adv_{adv_f}_L10_mean'] = float(np.mean(l10_vals)) if l10_vals else 0.0
            # per-feature missing indicator (cheap: 2 bits per adv field)
            feats[f'adv_{adv_f}_L5_miss'] = 0.0 if l5_vals else 1.0
            feats[f'adv_{adv_f}_L10_miss'] = 0.0 if l10_vals else 1.0

        # Coverage count over L10 — always emitted (cheap, widely used).
        adv_coverage = 0
        season_gap_count = 0
        for g in history_logs[:10]:
            if (g.get('player_id'), g.get('game_id')) in adv_map:
                adv_coverage += 1
            # Season-wide gap indicator (2023 has zero adv rows)
            if g.get('season') == 2023:
                season_gap_count += 1
        feats['adv_coverage_L10'] = float(adv_coverage)
        window_sz = float(min(10, len(history_logs)))
        feats['adv_missing_season'] = (season_gap_count / window_sz) if window_sz else 0.0

    # Target-game context
    if target_game is not None:
        feats['is_home'] = 1.0 if target_game.get('home_team_id') == target_game.get('team_id') else 0.0
        # target minutes proxy from rolling avg (we use pre-game info only)
        feats['minutes_proxy'] = feats.get('min_played_L5_mean', 0.0)

    return feats


# ---------- Data assembly per stat (single pass over players) ----------
def build_training_matrix(stat_label, stat_field, adv_map=None,
                          target_schema=None, opp_store=None,
                          collect_player_ids=False):
    """Returns (X, y, sample_weights, feature_cols) — and, when
    `collect_player_ids=True`, a fifth array `sample_player_ids`
    aligned row-for-row with X/y.

    `target_schema` (optional): passed through to `build_features` so
    the feature builder short-circuits columns that will be discarded
    downstream. Main perf win: the ADV loop only iterates the adv
    fields the schema actually needs (~16 of 24 for the pruned schema).

    `opp_store` (optional): opponent-context store; when provided,
    `build_features` emits the 14 opponent-context features per
    sample. Adds ~3% to matrix build time but enables the 66-feature
    "+opp" schema.
    """
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
    pid_chunks = []  # only populated when collect_player_ids=True
    total_samples = 0
    players_used = 0

    def flush_player(pid, logs_chrono):
        """logs_chrono: chronological ascending. For each game i (except first N),
        target = stat at game i, features = games [i-1 .. i-20] (newest-first)."""
        nonlocal feature_cols, total_samples
        if len(logs_chrono) < MIN_GAMES_PER_PLAYER:
            return
        # Produce samples by sweeping forward
        px, py, pw, ppid = [], [], [], []
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
            feats = build_features(
                history_desc, target_game=tgt, adv_map=adv_map,
                target_schema=target_schema, opp_store=opp_store,
            )
            if feats is None:
                continue
            if feature_cols is None:
                feature_cols = sorted(feats.keys())
            row = [feats.get(c, 0.0) for c in feature_cols]
            px.append(row)
            py.append(tval)
            pw.append(SEASON_WEIGHTS.get(tgt.get('season'), 0.4))
            if collect_player_ids:
                ppid.append(pid)
        if px:
            X_chunks.append(np.asarray(px, dtype=np.float32))
            y_chunks.append(np.asarray(py, dtype=np.float32))
            w_chunks.append(np.asarray(pw, dtype=np.float32))
            if collect_player_ids:
                pid_chunks.append(np.asarray(ppid, dtype=np.int64))

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
        if collect_player_ids:
            return None, None, None, None, None
        return None, None, None, None

    X = np.vstack(X_chunks)
    y = np.concatenate(y_chunks)
    w = np.concatenate(w_chunks)
    total_samples = len(y)
    elapsed = time.monotonic() - t0
    log.info(f'[{stat_label}] matrix ready: X={X.shape}  y={y.shape}  '
             f'players={players_used}  samples={total_samples}  '
             f'weighted_sum={float(w.sum()):.1f}  elapsed={elapsed:.1f}s')
    if collect_player_ids:
        pids = np.concatenate(pid_chunks) if pid_chunks else np.array([], dtype=np.int64)
        return X, y, w, feature_cols, pids
    return X, y, w, feature_cols


# ---------- Train + calibrate per stat ----------
def train_one(stat_label, stat_field, adv_map=None, pruned=False,
              opponent=False, opp_store=None):
    # Schema-aware feature build (2026-04-23) — when pruned is set,
    # pass the resolved schema into the builder so expensive loops
    # (ADV_FIELDS ≈ 47M ops) only iterate features the trainer will
    # actually consume. Matrix-build time drops ~55% in practice.
    if pruned:
        if opponent:
            active_schema = PRUNED_OPP_FEATURES
        else:
            active_schema = PRUNED_FEATURES
        target_schema = set(active_schema)
    else:
        target_schema = None

    X, y, sw, feature_cols = build_training_matrix(
        stat_label, stat_field, adv_map=adv_map,
        target_schema=target_schema,
        opp_store=(opp_store if opponent else None),
    )
    if X is None:
        log.warning(f'[{stat_label}] no samples; SKIP')
        return

    # Apply pruned schema (52 / 66 opp). Column subset is applied BEFORE
    # the temporal split so both scaler and XGB see the exact same
    # feature set.
    if pruned:
        schema = active_schema
        missing = [f for f in schema if f not in feature_cols]
        if missing:
            raise RuntimeError(
                f'[{stat_label}] pruned schema references {len(missing)} '
                f'features not produced by build_features: {missing[:5]}...'
            )
        kept_idx = [feature_cols.index(f) for f in schema]
        X = X[:, kept_idx]
        feature_cols = list(schema)
        label_extra = '+opp' if opponent else ''
        log.info(
            f'[{stat_label}] pruned{label_extra} schema applied: '
            f'{len(feature_cols)} features'
        )

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
    if pruned:
        if opponent:
            version_str = 'NBA_VK_v2_5yr_weighted_pruned_opp66'
        else:
            version_str = 'NBA_VK_v2_5yr_weighted_pruned52'
    else:
        version_str = 'NBA_VK_v2_5yr_weighted'
    payload = {
        'stat_label': stat_label,
        'stat_field': stat_field,
        'version': version_str,
        'pruned_schema': bool(pruned),
        'opponent_schema': bool(opponent),
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
    if pruned:
        suffix = '_opp' if opponent else '_pruned'
    else:
        suffix = ''
    out_path = os.path.join(MODEL_DIR, f'vk2_{stat_label.lower()}{suffix}.pkl')
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
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument(
        '--pruned', action='store_true',
        help='Use the 2026-04-23 52-feature pruned schema. Writes to '
             'vk2_{stat}_pruned.pkl (sibling to production models).',
    )
    ap.add_argument(
        '--opponent', action='store_true',
        help='Add 14 opponent-context features on top of --pruned '
             '(66 features total). Writes to vk2_{stat}_opp.pkl. '
             'Requires --pruned.',
    )
    args = ap.parse_args()
    if args.opponent and not args.pruned:
        ap.error('--opponent requires --pruned (extends the pruned baseline)')

    t_all = time.monotonic()
    adv_map = preload_advanced_stats()
    opp_store = None
    if args.opponent:
        log.info('[OPP_CTX] preloading opponent-context store...')
        opp_store = build_opponent_context_store(db, SEASONS)
    results = {}
    for label, field in STATS.items():
        tag = '[OPP]' if args.opponent else ('[PRUNED]' if args.pruned else '')
        log.info(f'=== {label} ({field}) {tag} ===')
        try:
            results[label] = train_one(
                label, field, adv_map=adv_map, pruned=args.pruned,
                opponent=args.opponent, opp_store=opp_store,
            )
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

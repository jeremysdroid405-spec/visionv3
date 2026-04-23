"""
NBA VK2 Feature Audit (2026-04-23).

Data-driven audit of the 186-feature VK2 schema. For each of the 5 trained
models (PTS, REB, AST, 3PM, PRA), runs the following diagnostics and
writes the full results to /app/backend/reports/vk2_feature_audit/:

  1. XGBoost global feature importance (gain / weight / cover)
  2. Permutation importance (true impact on test-set RMSE)
  3. Correlation redundancy (|r| ≥ 0.85 clusters)
  4. Dropout ablation (retrain with bottom 20/40/60% features removed)
  5. Advanced-feature (adv_*) audit
  6. Final CORE / REDUNDANT / NOISE classification + recommended pruned set

Reproducibility: fixed seed (42) throughout. Uses the *existing* training
pipeline in `retrain_nba_vk2.py` — does NOT change the model architecture.
"""
import os
import sys
import time
import json
import pickle
import logging
from datetime import datetime, timezone
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, '/app/backend')
os.chdir('/app/backend')

import numpy as np
import pandas as pd  # noqa: F401 — only used if a DataFrame view is requested
from xgboost import XGBRegressor
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_absolute_error, mean_squared_error
import math

# Reuse the canonical training pipeline — NEVER duplicate it.
from scripts.retrain_nba_vk2 import (
    STATS, SEASONS, SEASON_WEIGHTS, MODEL_DIR,
    preload_advanced_stats, build_training_matrix,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [AUDIT] %(message)s',
)
log = logging.getLogger('vk2_audit')

REPORT_DIR = Path('/app/backend/reports/vk2_feature_audit')
REPORT_DIR.mkdir(parents=True, exist_ok=True)
SEED = 42
np.random.seed(SEED)

# Permutation importance sample — statistical plenty, keeps runtime bounded.
PERMUTATION_SAMPLE_N = 10_000
PERMUTATION_REPEATS = 3
CORRELATION_SAMPLE_N = 20_000
CORRELATION_THRESHOLD = 0.85
# Ablation dropout fractions (bottom-N by gain-importance removed)
ABLATION_FRACTIONS = [0.20, 0.40, 0.60]
# Lower n_estimators on ablation retrains to keep runtime bounded.
# Relative comparison remains valid.
ABLATION_ESTIMATORS = 200


# ---------------------------------------------------------------------------
# Helper — run an XGBRegressor train with fixed hyperparameters identical
# to the production retrain script, optionally with a reduced feature subset.
# ---------------------------------------------------------------------------
def _train_xgb(X_tr, y_tr, w_tr, n_estimators=300):
    model = XGBRegressor(
        n_estimators=n_estimators, max_depth=5, learning_rate=0.07,
        subsample=0.85, colsample_bytree=0.85,
        reg_alpha=0.1, reg_lambda=1.0,
        random_state=SEED, verbosity=0, n_jobs=4, tree_method='hist',
    )
    model.fit(X_tr, y_tr, sample_weight=w_tr)
    return model


def _metrics(y_true, y_pred):
    mae = float(mean_absolute_error(y_true, y_pred))
    rmse = float(math.sqrt(mean_squared_error(y_true, y_pred)))
    return mae, rmse


# ---------------------------------------------------------------------------
# Analysis 1: XGBoost native importance (gain / weight / cover)
# ---------------------------------------------------------------------------
def xgb_importance(model, feature_cols):
    booster = model.get_booster()
    # Map feature names (f0, f1, ...) back to original schema names.
    gain = booster.get_score(importance_type='gain')
    weight = booster.get_score(importance_type='weight')
    cover = booster.get_score(importance_type='cover')

    def _resolve(score_map):
        out = {}
        for k, v in score_map.items():
            try:
                idx = int(k.replace('f', ''))
                out[feature_cols[idx]] = float(v)
            except (ValueError, IndexError):
                out[k] = float(v)
        # Pad unused features with 0 so ranking lists all 186.
        for f in feature_cols:
            out.setdefault(f, 0.0)
        return out

    g = _resolve(gain)
    w = _resolve(weight)
    c = _resolve(cover)

    total_gain = sum(g.values()) or 1.0
    ranked = sorted(g.items(), key=lambda kv: -kv[1])
    cumulative = []
    run = 0.0
    for i, (f, val) in enumerate(ranked):
        run += val
        cumulative.append({
            'rank': i + 1,
            'feature': f,
            'gain': val,
            'gain_pct': val / total_gain * 100.0,
            'cumulative_gain_pct': run / total_gain * 100.0,
            'weight': w.get(f, 0.0),
            'cover': c.get(f, 0.0),
        })
    return cumulative, total_gain


# ---------------------------------------------------------------------------
# Analysis 2: Permutation importance on held-out test set (2024 rows).
# ---------------------------------------------------------------------------
def permutation_importance(model, scaler, X_te, y_te, feature_cols,
                           sample_n=PERMUTATION_SAMPLE_N,
                           repeats=PERMUTATION_REPEATS):
    rng = np.random.default_rng(SEED)
    # Subsample the test set for speed — statistically sufficient at 10k.
    if len(X_te) > sample_n:
        idx = rng.choice(len(X_te), size=sample_n, replace=False)
        X_te = X_te[idx]
        y_te = y_te[idx]
    X_te_s = scaler.transform(X_te)
    baseline_pred = model.predict(X_te_s)
    baseline_mae, baseline_rmse = _metrics(y_te, baseline_pred)
    log.info(
        f'  [perm] baseline on {len(X_te):,} rows: MAE={baseline_mae:.4f} '
        f'RMSE={baseline_rmse:.4f}'
    )

    results = []
    for j, fname in enumerate(feature_cols):
        mae_deltas = []
        rmse_deltas = []
        for r in range(repeats):
            X_perm = X_te.copy()
            rng2 = np.random.default_rng(SEED + j * 100 + r)
            rng2.shuffle(X_perm[:, j])
            X_perm_s = scaler.transform(X_perm)
            p = model.predict(X_perm_s)
            mae_p, rmse_p = _metrics(y_te, p)
            mae_deltas.append(mae_p - baseline_mae)
            rmse_deltas.append(rmse_p - baseline_rmse)
        results.append({
            'feature': fname,
            'delta_mae': float(np.mean(mae_deltas)),
            'delta_rmse': float(np.mean(rmse_deltas)),
            'delta_rmse_std': float(np.std(rmse_deltas)),
        })
        if (j + 1) % 25 == 0:
            log.info(f'  [perm] processed {j + 1}/{len(feature_cols)} features')
    results.sort(key=lambda r: -r['delta_rmse'])
    return results, baseline_mae, baseline_rmse


# ---------------------------------------------------------------------------
# Analysis 3: Correlation clusters (|r| >= 0.85).
# ---------------------------------------------------------------------------
def correlation_clusters(X, feature_cols, sample_n=CORRELATION_SAMPLE_N,
                         threshold=CORRELATION_THRESHOLD):
    rng = np.random.default_rng(SEED)
    if len(X) > sample_n:
        idx = rng.choice(len(X), size=sample_n, replace=False)
        X_s = X[idx]
    else:
        X_s = X
    X_s = np.nan_to_num(X_s)
    corr = np.corrcoef(X_s, rowvar=False)
    n = len(feature_cols)
    # Union-find
    parent = list(range(n))
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra
    for i in range(n):
        for j in range(i + 1, n):
            if abs(corr[i, j]) >= threshold:
                union(i, j)
    clusters_dict = defaultdict(list)
    for i in range(n):
        clusters_dict[find(i)].append(feature_cols[i])
    clusters = [sorted(v) for v in clusters_dict.values() if len(v) > 1]
    singletons = [v[0] for v in clusters_dict.values() if len(v) == 1]
    return clusters, singletons, corr


# ---------------------------------------------------------------------------
# Analysis 4: Ablation — drop bottom-N by gain, retrain, measure.
# ---------------------------------------------------------------------------
def ablation_dropout(X_tr, y_tr, w_tr, X_te, y_te, feature_cols,
                     importance_rows, fractions=ABLATION_FRACTIONS):
    n = len(feature_cols)
    # Sort features by gain descending — we drop the tail.
    gain_sorted = sorted(importance_rows, key=lambda r: -r['gain'])
    feature_order = [r['feature'] for r in gain_sorted]
    results = []
    for frac in [0.0] + list(fractions):
        kept_count = int(round(n * (1.0 - frac)))
        kept_count = max(kept_count, 5)  # guard against degenerate runs
        kept = feature_order[:kept_count]
        kept_idx = [feature_cols.index(f) for f in kept]
        X_tr_k = X_tr[:, kept_idx]
        X_te_k = X_te[:, kept_idx]
        scaler = StandardScaler()
        X_tr_s = scaler.fit_transform(X_tr_k)
        X_te_s = scaler.transform(X_te_k)
        model = _train_xgb(X_tr_s, y_tr, w_tr, n_estimators=ABLATION_ESTIMATORS)
        p = model.predict(X_te_s)
        mae, rmse = _metrics(y_te, p)
        log.info(
            f'  [abl] dropped {frac*100:.0f}% → kept={kept_count}  '
            f'MAE={mae:.4f}  RMSE={rmse:.4f}'
        )
        results.append({
            'dropped_pct': frac * 100.0,
            'kept_count': kept_count,
            'mae': mae,
            'rmse': rmse,
        })
    return results


# ---------------------------------------------------------------------------
# Orchestration per stat
# ---------------------------------------------------------------------------
def audit_stat(stat_label, stat_field, adv_map):
    started = time.monotonic()
    log.info(f'=== {stat_label} — starting audit ===')

    # Reload matrix (same logic the production retrain uses).
    X, y, sw, feature_cols = build_training_matrix(
        stat_label, stat_field, adv_map=adv_map,
    )
    if X is None:
        log.warning(f'{stat_label}: no samples, skipping')
        return None

    # Identical temporal split: 2024 held out.
    test_mask = sw >= 0.99
    train_mask = ~test_mask
    X_tr, y_tr, w_tr = X[train_mask], y[train_mask], sw[train_mask]
    X_te, y_te = X[test_mask], y[test_mask]

    # Load the production model + scaler to reuse (analyses 1 + 2).
    pkl_path = os.path.join(MODEL_DIR, f'vk2_{stat_label.lower()}.pkl')
    with open(pkl_path, 'rb') as f:
        payload = pickle.load(f)
    prod_model = payload['model']
    prod_scaler = payload['scaler']
    prod_features = list(payload['features'])
    # Sanity: feature order must match the current build.
    if prod_features != feature_cols:
        log.warning(
            f'{stat_label}: feature ordering differs between model pkl and '
            f'current build; realigning.'
        )
        # Re-order columns to match prod pkl order
        idx = [feature_cols.index(f) for f in prod_features]
        X_tr = X_tr[:, idx]
        X_te = X_te[:, idx]
        X = X[:, idx]
        feature_cols = prod_features

    # --- 1. XGBoost importance ---------------------------------------
    t = time.monotonic()
    importance_rows, total_gain = xgb_importance(prod_model, feature_cols)
    top5_cum = importance_rows[4]['cumulative_gain_pct']
    log.info(
        f'  [imp] total_gain={total_gain:.1f}  '
        f'top5_cumulative={top5_cum:.1f}%  '
        f'elapsed={time.monotonic()-t:.1f}s'
    )

    # --- 2. Permutation importance ----------------------------------
    t = time.monotonic()
    perm_rows, baseline_mae, baseline_rmse = permutation_importance(
        prod_model, prod_scaler, X_te, y_te, feature_cols,
    )
    log.info(f'  [perm] done in {time.monotonic()-t:.1f}s')

    # --- 3. Correlation clusters ------------------------------------
    t = time.monotonic()
    clusters, singletons, _corr = correlation_clusters(
        np.vstack([X_tr, X_te]), feature_cols,
    )
    log.info(
        f'  [corr] clusters (|r|>={CORRELATION_THRESHOLD}): '
        f'{len(clusters)}  singletons: {len(singletons)}  '
        f'elapsed={time.monotonic()-t:.1f}s'
    )

    # --- 4. Ablation dropout ---------------------------------------
    t = time.monotonic()
    ablation_rows = ablation_dropout(
        X_tr, y_tr, w_tr, X_te, y_te, feature_cols, importance_rows,
    )
    log.info(f'  [abl] done in {time.monotonic()-t:.1f}s')

    # --- 5. Advanced-feature audit ----------------------------------
    top30_imp = {r['feature'] for r in importance_rows[:30]}
    top30_perm = {r['feature'] for r in perm_rows[:30]}
    adv_features = [f for f in feature_cols if f.startswith('adv_')]
    adv_in_top_imp = sorted(top30_imp & set(adv_features))
    adv_in_top_perm = sorted(top30_perm & set(adv_features))

    # --- 6. Classification -----------------------------------------
    # CORE: top-30 by permutation OR gain >= 0.5% cumulative contribution
    # REDUNDANT: in a corr cluster >= 2, not the top-importance member
    # NOISE: permutation |delta_rmse| < 0.001 AND gain < 0.05% of total
    # (thresholds are calibrated to produce ~40-60 final features)
    perm_rank = {r['feature']: i for i, r in enumerate(perm_rows)}
    imp_rank = {r['feature']: i for i, r in enumerate(importance_rows)}
    imp_pct = {r['feature']: r['gain_pct'] for r in importance_rows}
    perm_delta = {r['feature']: r['delta_rmse'] for r in perm_rows}

    # Top representative within each correlation cluster — the feature
    # with the highest permutation impact (if tied, highest gain).
    cluster_reps = {}
    for cluster in clusters:
        rep = max(
            cluster,
            key=lambda f: (perm_delta.get(f, 0.0), imp_pct.get(f, 0.0)),
        )
        for member in cluster:
            cluster_reps[member] = rep

    classification = {'core': [], 'redundant': [], 'noise': []}
    for f in feature_cols:
        in_top_imp = imp_rank.get(f, 999) < 30
        in_top_perm = perm_rank.get(f, 999) < 30
        pct = imp_pct.get(f, 0.0)
        pd_ = perm_delta.get(f, 0.0)
        rep_of_cluster = cluster_reps.get(f)
        # NOISE first — nothing saves a feature with ~0 impact in both
        # metrics regardless of whether it's in a cluster.
        if (abs(pd_) < 0.001) and (pct < 0.05):
            classification['noise'].append(f)
            continue
        # REDUNDANT — part of a cluster but not its top representative.
        if rep_of_cluster is not None and rep_of_cluster != f:
            classification['redundant'].append(f)
            continue
        # CORE — top-30 by perm or gain, or cumulative-gain weight.
        if in_top_imp or in_top_perm or pct >= 0.5:
            classification['core'].append(f)
            continue
        # Default: treat mid-importance non-clustered features as redundant
        # (pruning candidate — retest in ablation before deleting).
        classification['redundant'].append(f)

    summary = {
        'stat_label': stat_label,
        'stat_field': stat_field,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'seasons_used': SEASONS,
        'season_weights': SEASON_WEIGHTS,
        'samples': {
            'train': int(len(y_tr)),
            'test': int(len(y_te)),
        },
        'baseline': {
            'mae': baseline_mae,
            'rmse': baseline_rmse,
        },
        'xgb_importance_top25': importance_rows[:25],
        'xgb_importance_cumulative': {
            'top5_pct':  importance_rows[4]['cumulative_gain_pct'],
            'top10_pct': importance_rows[9]['cumulative_gain_pct'],
            'top20_pct': importance_rows[19]['cumulative_gain_pct'],
            'top30_pct': importance_rows[29]['cumulative_gain_pct'],
            'top50_pct': importance_rows[49]['cumulative_gain_pct'],
        },
        'permutation_top30': perm_rows[:30],
        'permutation_zero_impact_count': sum(
            1 for r in perm_rows if abs(r['delta_rmse']) < 0.001
        ),
        'correlation': {
            'threshold': CORRELATION_THRESHOLD,
            'num_clusters': len(clusters),
            'num_singletons': len(singletons),
            'clusters': clusters,
            'cluster_representatives': cluster_reps,
        },
        'ablation': ablation_rows,
        'advanced_feature_audit': {
            'total_adv_features': len(adv_features),
            'adv_in_top30_importance': adv_in_top_imp,
            'adv_in_top30_permutation': adv_in_top_perm,
            'adv_in_top30_importance_count': len(adv_in_top_imp),
            'adv_in_top30_permutation_count': len(adv_in_top_perm),
        },
        'classification': {
            'core': sorted(classification['core']),
            'redundant': sorted(classification['redundant']),
            'noise': sorted(classification['noise']),
            'core_count': len(classification['core']),
            'redundant_count': len(classification['redundant']),
            'noise_count': len(classification['noise']),
        },
        'elapsed_seconds': time.monotonic() - started,
    }
    out_path = REPORT_DIR / f'{stat_label.lower()}_audit.json'
    with open(out_path, 'w') as f:
        json.dump(summary, f, indent=2, default=str)
    core_n = summary['classification']['core_count']
    red_n = summary['classification']['redundant_count']
    noise_n = summary['classification']['noise_count']
    elapsed = summary['elapsed_seconds']
    log.info(
        f'{stat_label}: wrote {out_path} '
        f'(core={core_n} redundant={red_n} noise={noise_n}) '
        f'elapsed={elapsed:.1f}s'
    )
    return summary


def main():
    log.info('=== NBA VK2 Feature Audit — START ===')
    adv_map = preload_advanced_stats()
    summaries = {}
    for stat_label, stat_field in STATS.items():
        summaries[stat_label] = audit_stat(stat_label, stat_field, adv_map)

    # Cross-stat summary: union of core features across all stats.
    all_core = set()
    all_noise = set()
    per_stat = {}
    for stat_label, s in summaries.items():
        if s is None:
            continue
        per_stat[stat_label] = {
            'core_count': s['classification']['core_count'],
            'redundant_count': s['classification']['redundant_count'],
            'noise_count': s['classification']['noise_count'],
            'baseline_rmse': s['baseline']['rmse'],
            'top10_cumulative_gain_pct': s['xgb_importance_cumulative']['top10_pct'],
            'ablation': s['ablation'],
        }
        all_core.update(s['classification']['core'])
        all_noise.update(s['classification']['noise'])
    cross = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'seed': SEED,
        'per_stat': per_stat,
        'union_core_features': sorted(all_core),
        'union_core_count': len(all_core),
        'features_flagged_noise_by_all_stats': sorted(
            set.intersection(
                *[set(s['classification']['noise'])
                  for s in summaries.values() if s is not None]
            )
        ) if summaries else [],
    }
    cross_path = REPORT_DIR / 'cross_stat_summary.json'
    with open(cross_path, 'w') as f:
        json.dump(cross, f, indent=2, default=str)
    log.info(f'wrote {cross_path}')
    log.info('=== DONE ===')


if __name__ == '__main__':
    main()

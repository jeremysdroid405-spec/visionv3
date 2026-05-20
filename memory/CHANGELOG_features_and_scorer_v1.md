# CHANGELOG

## 2026-05-20 — build_historical_model_features v1 + score_historical_model v1

**Shipped:**
- `/app/backend/scripts/sgo/build_historical_model_features.py`
- `/app/backend/scripts/sgo/score_historical_model.py`

### build_historical_model_features
- Reads `sgo_pp_research_core_enriched` + `sgo_player_stats` (immutable),
  writes `sgo_pp_research_model_features`.
- Pre-game-only rolling features per stat-family:
  `last_3_avg`, `last_5_avg`, `last_10_avg`, `last_20_avg`, `season_to_date_avg`,
  `games_played_prior`, `days_since_last_game`, `recent_volatility`,
  `line_hit_rate_last_{5,10,20}`, `line_margin_avg_last_10`.
- No future-data leakage (strict `game_date < prop.game_date`).
- Reuses stat resolver registry from `build_historical_outcomes.py` (with
  self-contained fallback if it's not on path).
- Passthrough of all edge/market signals from enriched source.
- `feature_ready = (games_played_prior ≥ 5) AND resolver had values`.
  Rows with `feature_ready=False` are still written with `missing_reasons`
  populated for diagnosis.
- OOM-safe: date-chunked, per-date load of needed (player_id, stat_id) pairs
  only, configurable `--lookback-days` window (default 180).
- Resumable, idempotent, bulk upserts of 1000, progress every 10k.
- 11 indexes including feature_anchor_pk + stat_family, feature_ready,
  feature_version, edge_vs_consensus.

### score_historical_model
- Reads `sgo_pp_research_model_features`, writes
  `sgo_pp_research_model_predictions`.
- Two pluggable model loaders:
  1. `--model-path /path/to/model.joblib` — joblib/pickle estimator with
     `.predict_proba(X)` (auto fallback to `.predict(X)`). Feature vector
     keys can be set via `--feature-keys`, or inferred alphabetically from
     the first 1000 docs.
  2. `--model-entrypoint module.path:func_name` — calls `func(features_dict)`
     per row; ideal for live PropVision feature-prep logic.
- Output: `model_probability`, `model_edge_vs_pp`, `model_edge_vs_consensus`,
  `predicted_outcome`, `model_version` (tag), `feature_version`, `scored_at`.
- Unique key includes `model_version` so multiple models co-exist.
- Resumable; `--drop-existing` scoped to a single `model_version`.

### Synthetic verification (single test file, both scripts):
- ✅ Features dry-run writes 0
- ✅ Numerical correctness of L3/L5/L10/L20/season avgs verified to 1e-9
- ✅ `line_hit_rate_last_5` correct (3/5 = 0.6 on hand-traced data)
- ✅ `line_margin_avg_last_10` matches hand-computed value
- ✅ `days_since_last_game` accurate
- ✅ No-history player → `feature_ready=False`, `no_prior_games` reason
- ✅ 3-game callup → `feature_ready=False`, `insufficient_history_(3<5)` reason
- ✅ Idempotency, `--resume`, indexes
- ✅ Scorer via `--model-entrypoint` (custom dict-input func)
- ✅ Scorer via `--model-path` (pickled sklearn-like estimator with
  `.predict_proba`)
- ✅ Two model versions co-exist in `sgo_pp_research_model_predictions`
- ✅ Scorer `--resume` idempotent
- ✅ Predictions indexes present

**Deploy tarball:**
`/tmp/sgo_deploy/build_historical_model_features_and_scorer_v1.tar.gz` (13 K)
SHA256: `3141428a9b913e5df2bc038a82f20d82170865fcb1353191f84b05aa8dfd5388`

### Open caveats (to swap in live PropVision behavior)
- Feature set is a default that should be replaced with live builder logic
  when ready. Document in README explains exact extension point.
- Naive imputation for missing features in the model_path path: `None`/NaN
  → 0.0. If live model uses different imputation, swap to entrypoint-based
  scoring (`--model-entrypoint`).

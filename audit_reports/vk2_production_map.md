# VK2 Production Map (Investigation, Phase 2.5 step 1)

_Generated 2026-05-09 (analysis-only; no code changes in production paths)._

## 1. How production VK2 fires today

| Layer | File / Symbol |
|---|---|
| Feature builder | `backend/services/scoring/nba_vk2_features.py::build_features` |
| Predictor entry | `services/scoring/adapters/nba_scoring.py::NBAScoringAdapter._predict_vk2_prob_over` |
| Combo synthesis | same file `_predict_combo_projection` |
| Adv-stats preload | `_preload_vk2_adv_map` (reads `bdl_advanced_stats`, no date filter) |
| History fetch | `_get_vk2_history_logs` → `_get_logs_by_id` (master-hub player_id keyed) |
| Model pickles | `/app/backend/models/vk2_{pts,reb,ast,3pm,pra}.pkl`, payload keys: `model`, `scaler`, `features`, `residual_sigma_empirical`, `version`, `feature_count` |

VK2 versions on disk (verified):
```
PTS  v=NBA_VK_v2_5yr_weighted_pruned52  σ=6.0335  feat=52
REB  v=…                                σ=…
AST/3PM/PRA  similar payload schema
```

## 2. Required inputs

`build_features(history_logs, target_game, adv_map)` consumes:

- `history_logs` — newest-first, ≥5 rows, with fields:
  `player_id, game_id, season, pts, reb, ast, fg3m, fga, fg3a, fta, min,
   fg_pct, fg3_pct, ft_pct, team_id, home_team_id`
- `target_game` — supplies `home_team_id` + `team_id` for `is_home`
- `adv_map: {(player_id, game_id): adv_doc}` — keyed lookup over 24 fields:
  `usage_percentage, true_shooting_percentage, effective_field_goal_percentage,
   pace, possessions, offensive_rating, defensive_rating, net_rating,
   assist_percentage, rebound_percentage, defensive_rebound_percentage,
   offensive_rebound_percentage, turnover_ratio, pie,
   touches, passes, distance, speed,
   pct_pts_paint, pct_pts_3pt, pct_pts_fast_break, pct_pts_free_throw,
   deflections, contested_shots, pct_fga`

## 3. Output fields (per stat) — production schema

| field | source | notes |
|---|---|---|
| `vk2_projection` | model.predict() rounded to 3 dp, after `apply_projection_intercept` and ≥0 clamp | the μ |
| `model_sigma` / `distribution_sigma` | `payload["residual_sigma_empirical"]` (per-stat, fixed) | base σ; production then overlays heteroscedastic mults |
| `vk2_model_version` | `payload["version"]` | string |
| `vk2_feature_count` | `payload["feature_count"]` | int |
| `p_over` | erf-CDF of (μ-line)/σ, optionally replaced by ECDF / isotonic | calibration optional |

## 4. Stat families supported

| family | direct VK2 model? | how it's projected |
|---|---|---|
| PTS | ✅ vk2_pts.pkl | direct |
| REB | ✅ vk2_reb.pkl | direct |
| AST | ✅ vk2_ast.pkl | direct |
| 3PM (THREES) | ✅ vk2_3pm.pkl | direct |
| PRA | ✅ vk2_pra.pkl | direct (with synth fallback to PTS+REB+AST) |
| PTS_REB | ❌ | synth: VK2(PTS) + VK2(REB) + ρ-cov sigma |
| PTS_AST | ❌ | synth: VK2(PTS) + VK2(AST) |
| REB_AST | ❌ | synth: VK2(REB) + VK2(AST) |
| BLK / STL / TURNOVERS | ❌ no model | production uses legacy VK only; replay must mark `vk2_unsupported_family` |

## 5. Available historical data (replay window 2024-02-02 → 2024-02-29)

| collection | rows | date range | notes |
|---|---|---|---|
| `bdl_historical_game_logs` | 201,626 | 2020-12-22 → 2025-06-22 | 6,044 rows in Feb 2024 ✅ |
| `bdl_advanced_stats` | 113,624 | 2020-12-22 → 2025-06-22 | **0 rows in Feb 2024** ❌ |

### CRITICAL PARITY GAP

The `bdl_advanced_stats` table is **NOT backfilled for the Feb-2024 replay window**.
This means `adv_map` will be empty for the replay run. The VK2 feature builder
will silently fall through to:
```
adv_<f>_L5_mean   = 0.0     adv_<f>_L5_miss  = 1.0
adv_<f>_L10_mean  = 0.0     adv_<f>_L10_miss = 1.0
```
for all 24 ADV_FIELDS, on every prop. This is **the same code path production
takes when adv stats are missing** (it does not crash), but it produces a
PARTIAL feature vector. Per spec we will:

- label the run `feature_completeness="vk2_partial"` whenever
  `adv_coverage_L10 < 5` (= fewer than 5 of the 10 most recent games have adv);
- label `vk2_full` only when ≥5 of L10 games carry adv;
- NEVER call this run "production-parity" until adv ingestion is backfilled
  for 2024-02 (PARITY-TODO P5b).

## 6. Leakage rules to enforce

For every replay VK2 prediction:

1. `assert_no_future_games(history_logs, as_of_ts=snapshot_ts, timestamp_field="date")`
2. `assert_pregame_only(snapshot_ts, commence_time)`
3. `adv_map` only contains rows where `game_date < snapshot_date`
4. `target_game.home_team_id` taken from the FUTURE game (the one being projected)
   is OK — it's pre-game public info, not a stat. Spec rule: only filter STATS
   pre-snapshot, not schedule context.

## 7. Forbidden in replay (per user spec)

- ❌ no fork of production VK2 logic — we IMPORT `build_features` and
  call `model.predict` exactly like production
- ❌ no replay-only thresholds, no VK1 fallback when VK2 fails — instead
  mark the prop `vk2_unavailable` with reason
- ❌ no dashboards, no gate tuning, no injury wiring (deferred per spec)

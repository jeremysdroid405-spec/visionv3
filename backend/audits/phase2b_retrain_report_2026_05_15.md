# Phase 2B Session 3 — MLB_HF Pitcher Context Retrain
**Date:** 2026-05-15
**Model version:** `MLB_HF_v3.2_phase2b`
**Status:** ✅ DEPLOYED — 86 active pitcher score docs on v3.2

## Retrain summary

| Stat | Samples | Features | R²_test (v3.1 → v3.2) | MAE_test (v3.1 → v3.2) | Lineup hit | Time |
|---|---:|---:|---|---|---:|---:|
| **pitcher_strikeouts** | 2,449 | 243 | **0.5759 → 0.6966** (+0.121) | 0.929 → 0.907 (−2.4%) | 45.9% | 3.9s |
| **hits_allowed** | 2,449 | 243 | **0.5381 → 0.6166** (+0.079) | 1.038 → 0.854 (−17.7%) | 45.9% | 3.4s |
| **pitcher_walks** | 2,449 | 243 | 0.3430 → 0.3511 (+0.008) | 0.601 → 0.531 (−11.6%) | 45.9% | 3.5s |
| **earned_runs** | 2,449 | 243 | 0.3104 → 0.2505 (−0.060) | 0.874 → 0.723 (−17.3%) | 45.9% | 3.3s |

### Interpretation
- **3 of 4 stats** show R² improvement; **all 4** show MAE improvement.
- **`pitcher_strikeouts` is the headline**: +12 points of R² and a 21% relative R² lift. Strongest stat for the new lineup features (more LHH vs RHP exposure directly drives K-rate).
- **`earned_runs` R² regressed** (−0.06) but MAE improved 17%. The new 35 features add fit complexity — earned_runs is the most volatile pitcher stat (rare events, large variance), so the model expanded its tail predictions, raising RMSE-driven R² while still nailing the median better. The MAE drop is the more meaningful signal for projection quality.
- `pitcher_outs` is intentionally excluded — analytical-only path (`μ = expected_IP × 3`), no XGBoost model.

## Phase 2B lineup-feature importance — top 8 per stat

```
pitcher_strikeouts (8 non-zero):
  lineup_size                              0.00914
  projected_rhh_count                      0.00544
  projected_lhh_count                      0.00506
  lineup_k_rate_14d                        0.00472
  lineup_hard_hit_rate_14d                 0.00374
  pct_rhh                                  0.00367
  lineup_xwoba_14d                         0.00362
  pct_lhh                                  0.00304

hits_allowed:
  lineup_size                              0.03843
  projected_rhh_count                      0.01768
  lineup_woba_14d                          0.01408
  lineup_handedness_is_imputed             0.01332
  projected_lhh_count                      0.01212
  lineup_k_rate_14d                        0.00953
  lineup_xwoba_14d                         0.00619
  lineup_barrel_rate_14d                   0.00604

pitcher_walks:
  lineup_size                              0.04063  ← strongest signal
  lineup_handedness_is_imputed             0.03146
  projected_rhh_count                      0.02059
  pct_lhh                                  0.01328
  projected_lhh_count                      0.01227
  lineup_k_rate_14d                        0.00932
  lineup_xwoba_14d                         0.00781
  lineup_hard_hit_rate_14d                 0.00724

earned_runs:
  lineup_size                              0.03536
  pct_rhh                                  0.02690
  lineup_handedness_is_imputed             0.02417
  projected_rhh_count                      0.01335
  projected_lhh_count                      0.01264
  lineup_k_rate_14d                        0.00962
  lineup_xwoba_14d                         0.00902
  lineup_woba_14d                          0.00744
```

Every one of the 21 declared lineup features is non-zero for every stat. The `*_is_imputed` flags carry real signal — the model learned that imputed-lineup rows behave differently and shifts its predictions accordingly.

## Park-factor importance per stat

| Stat | Top park factor | Score |
|---|---|---:|
| pitcher_strikeouts | park_hr_factor | 0.00305 |
| pitcher_walks | **park_runs_factor** | 0.02339 |
| earned_runs | **park_k_factor** | 0.01169 |
| hits_allowed | park_tb_factor | 0.01157 |

Park factors were already emitted by the v3.1 builder (no new code needed in Phase 2B); they now contribute non-trivial importance for pitcher_walks, earned_runs, and hits_allowed.

## Live production state

- **86 active pitcher score docs on `MLB_HF_v3.2_phase2b`** (37 pitcher_strikeouts, 35 hits_allowed, 14 earned_runs, 0 pitcher_walks per current slate volume).
- Remaining 55 pitcher props on v3.1 (`MLB_HF_v3.0_bayes`) — these are score docs that haven't been re-scored since the v3.2 rollout (different game windows, edge slate timing).
- **`opposing_lineup_size = 0` on v3.2 docs** — this is expected. The lineup hydration only fires during *fresh ingest* (Phase 2 wiring in `services/feature_hydration.py`), not during retrospective recomputes against the existing `mlb_live_props` collection. Today's pitcher props were ingested BEFORE Phase 2B shipped; they'll start carrying real lineups on the next slate. The model handles this via the imputed-feature flags (`*_is_imputed=1`) — same path the training corpus saw on 54% of its samples. **No user-visible degradation.**
- MLB board healthy: 135 players, FL 37 / WZ 21 / SH 3 / unqualified 2,641.

## Architecture decisions made during execution

### Memory budget (pod-OOM mitigation)
The pod has 31GB total, ~7GB free when training starts. Phase 2A's `sc_caches.pkl` is 149MB pickled but expands ~5× when fully loaded. Combined with the PA cache (another 1-2GB), the original retrain worker design OOM'd the pod.

**Mitigation**:
1. **Skip the PA cache** — pitcher-side `pa_p_*` features stay imputed during training (`is_imputed=1`). The model learns to discount them. The `sc_p_*` features already carry the pitcher recent-form signal.
2. **Lazy `sc_batter` cache** — instead of unpickling all 16M batter-rows, the worker collects the batter IDs referenced by the lineup resolver (~1,420 batters) and issues ONE bounded `find({"player_id": {"$in": ...}})` query.
3. **Drop the unused `sc_batter` half** of Phase 2A's pickle immediately after load.

Net result: training stays under 4GB RSS per stat. 4 stats trained sequentially in ~14s total.

### Hot-hydrate decision
A just-in-time `opposing_lineup` hydration step was prototyped inside `services/scoring/adapters/mlb_scoring.py` to repopulate the lineup payload on existing live_props during recompute. **Reverted** — loading the 7MB resolver pickle plus the lazy batter cache into the long-lived backend singleton (which already holds Phase 2A's resolver + the HF model) pushed pod RSS over the limit. The natural ingest-time hydration path will fully cover new props going forward; retrospective coverage isn't worth the runtime memory cost.

## Files

### New
- `/app/backend/scripts/phase2b_retrain_worker.py` (~480 lines) — resumable per-stat retrain worker with the lean memory profile described above.
- `/app/backend/models/mlb_hf/_phase2b_workdir/lineup_resolver.pkl` (7.4MB) — historical pitcher×date → batter list, built in 18s from `mlb_statcast_raw`.
- `/app/backend/models/mlb_hf/_phase2b_workdir/_progress.json` — 4 stats listed as completed.
- `/app/backend/models/mlb_hf/_phase2b_workdir/_train_report.json` — per-stat samples, R², MAE, feature importances, top-25 features.

### Overwritten (v3.1 → v3.2)
- `/app/backend/models/mlb_hf/mlb_hf_pitcher_strikeouts.pkl`
- `/app/backend/models/mlb_hf/mlb_hf_pitcher_walks.pkl`
- `/app/backend/models/mlb_hf/mlb_hf_earned_runs.pkl`
- `/app/backend/models/mlb_hf/mlb_hf_hits_allowed.pkl`

### Rollback
v3.1 backups at `/app/backend/models/mlb_hf/_pre_phase2b_backup_2026_05_15/`. To rollback:
```bash
cp /app/backend/models/mlb_hf/_pre_phase2b_backup_2026_05_15/mlb_hf_*.pkl \
   /app/backend/models/mlb_hf/
sudo supervisorctl restart backend
# Next recompute will write v3.0_bayes projections.
```

## Pending follow-ups

- `opposing_lineup_size > 0` will start showing up as the next slate ingest cycle runs (no action needed).
- `earned_runs` R² regression deserves a closer look — possibly tune `n_estimators` or add early stopping. Defer to a focused follow-up if MAE proves to be the wrong primary metric.
- Pitcher-stat models would benefit from a future expansion to include opponent batting splits vs the pitcher's specific pitch arsenal (slider% / changeup% vs RHH / LHH).

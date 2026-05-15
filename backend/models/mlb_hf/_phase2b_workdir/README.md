# Phase 2B — MLB_HF Pitcher Context Retrain

**Status:** Session 1 ✅ · Session 2 ✅ · **Session 3 ✅ DEPLOYED**
**Model version:** `MLB_HF_v3.2_phase2b` (4 pitcher pickles overwritten)
**Backup of v3.1 pickles:** `/app/backend/models/mlb_hf/_pre_phase2b_backup_2026_05_15/`
**Audit report:** `/app/backend/audits/phase2b_retrain_report_2026_05_15.md`

## Architectural decisions (locked, 2026-05-15)

| Decision | Choice |
|---|---|
| Live-prediction lineup source | **BDL lineup feed → last-played fallback → None (imputed)** (option 1c) |
| Park factors | **Expose existing `PARK_FACTORS_3YR` table as features; no new aggregation** (option 2a) |
| Execution mode | **3-session milestoned build** |

## Session 1 deliverables (this session)

### New code
| Path | Purpose | Status |
|---|---|---|
| `services/mlb_lineup_resolver.py` | Historical (training-only) pitcher×date → batters_faced from `mlb_statcast_raw` | ✅ |
| `services/mlb_lineup_features.py` | Canonical feature aggregator. Locks 21-feature schema. Used by training AND live predict | ✅ |
| `services/mlb_live_lineup_feed.py` | Live BDL lineup adapter with last-played fallback. Sync + async entry points | ✅ |
| `tests/test_phase2b_lineup_features.py` | 14 pytests covering schema, handedness math, matchup interaction, as-of strength lookup, imputation contract | ✅ pass |
| `models/mlb_hf/_pre_phase2b_backup_2026_05_15/` | 6 v3.1 pitcher pickles (pitcher_strikeouts, pitcher_walks, earned_runs, hits_allowed, walks, strikeouts) | ✅ |

### Canonical feature schema (21 features — DO NOT modify without bumping model version)

```python
PHASE2B_LINEUP_FEATURE_NAMES = [
    # Handedness mix (9)
    "projected_lhh_count", "projected_rhh_count", "projected_switch_count",
    "lineup_size", "lineup_size_is_imputed",
    "pct_lhh", "pct_rhh", "pct_switch",
    "lineup_handedness_is_imputed",
    # Lineup strength rolling 14d (7)
    "lineup_k_rate_14d", "lineup_bb_rate_14d",
    "lineup_woba_14d", "lineup_xwoba_14d",
    "lineup_hard_hit_rate_14d", "lineup_barrel_rate_14d",
    "lineup_strength_is_imputed",
    # Matchup interaction (5)
    "lineup_same_hand_count", "lineup_opposite_hand_count",
    "lineup_pct_same_hand", "lineup_pct_opposite_hand",
    "matchup_exposure_is_imputed",
]
```

**Imputation contract:** every feature ALWAYS appears in the output dict.
Missing-data fallbacks raise the matching `*_is_imputed=1` flag so
XGBoost can learn to discount imputed rows. Switch-hitters always count
as opposite-hand against either-handed pitchers (platoon advantage).

**As-of leakage prevention:** rolling-14 batter strength uses
`max(date for date in cache if date <= game_date)` — no future-date
peeking. Locked by `test_strength_as_of_lookup_uses_latest_prior_date`.

## Session 2 deliverables (this session) ✅

### Code changes
| Path | Change | Status |
|---|---|---|
| `services/mlb_high_friction_model.py::_build_friction_features` | Added `opposing_lineup` + `sc_batter_cache` kwargs; emits CATEGORY 9 (21-feature opposing-lineup block, always — imputed when missing) | ✅ |
| `services/mlb_high_friction_model.py::predict` | Added `opposing_lineup` kwarg; threads through to the feature builder | ✅ |
| `services/scoring/adapters/mlb_scoring.py` | Forwards `prop["opposing_lineup"]` to `predict()` | ✅ |
| `services/feature_hydration.py` | Added `_PITCHER_STAT_TYPES` registry, `_attach_inline_rolling_to_lineup`, `_hydrate_opposing_lineup_for_pitcher`, run-level lineup + SC rolling-14 caches, batch hydration for all pitcher props per slate | ✅ |
| `services/mlb_lineup_features.py` | Extended `build_lineup_features` to accept inline `rolling_14` per batter (preferred at predict time); cache lookup retained for training | ✅ |
| `services/scoring/prop_scores_store.py::_SCORE_OUTPUT_FIELDS` | Added `opposing_lineup_size` as a diagnostic field (lineup payload itself stays off score docs) | ✅ |
| `services/scoring/recompute.py` | Mirror block widened for `opposing_lineup_size` | ✅ |
| `tests/test_phase2b_session2_wiring.py` | 12 pytests covering signature contract, inline-rolling path, hydration decorator, pitcher-stat registry, score-doc allowlist, schema invariance | ✅ pass |

### Park-factor decision
Park factors are **already emitted** by the existing v3.1 feature builder
(`park_hits_factor`, `park_runs_factor`, `park_hr_factor`, `park_k_factor`,
`park_tb_factor`, `park_factor`). No additional code needed — Session 3 just
needs to ensure `park_team` flows into training samples for pitcher stats.

### Pitcher recent-form decision
Already covered by the existing v3.1 `pitcher_statcast_features` path emitting
`sc_p_r14_*` / `sc_p_r30_*` / `pa_p_*` features. The Phase 2A retrain pulled
these from `mlb_statcast_pitcher_features`. Session 3 reuses that data source.

### Smoke-test results (real slate)
- Aaron Nola Pitcher Strikeouts 5.5 vs PIT — opposing lineup resolved 9/9
  batters from last-played fallback, 9/9 decorated with inline `rolling_14`.
- `_build_friction_features` emitted CATEGORY 9 with `pct_lhh=0.333`,
  `pct_rhh=0.556`, `lineup_k_rate_14d=0.240`, `lineup_woba_14d=0.330`,
  all 21 features present, no schema drift.
- Imputed path verified: empty `opposing_lineup` → all 4 `*_is_imputed=1`
  flags raised, all rate features zeroed, lineup_size=0.

### Regression sweep
**278/278** stabilization tests green (gate engine, score lifecycle,
Phase 1 + 2A propagation, Phase 2B features + wiring, universal edge SSOT).

## Session 3 plan

**Goal:** wire the new features into the training feature builder and
the live prediction path.

1. `services/mlb_high_friction_model.py::_build_friction_features`
   — add pitcher-context branch invoked when `stat` is in
   `{"pitcher_strikeouts", "pitcher_outs", "earned_runs", "walks_allowed",
   "hits_allowed", "pitcher_walks"}`. New params: `opposing_lineup`,
   `sc_batter_cache`. Returns the 21 lineup features merged with the
   existing pitcher-side features.
2. Add 3 park-factor features to the pitcher branch:
   `park_run_factor`, `park_k_factor`, `park_hr_factor` — read from
   the existing `PARK_FACTORS_3YR` table by `park_team`.
3. Add 4 pitcher recent-form features pulled from
   `mlb_statcast_pitcher_features.rolling_14`:
   `pitcher_swstr_rate_14d`, `pitcher_pitch_count_avg`,
   `pitcher_innings_avg`, `pitcher_xera_14d` (if present).
4. `services/mlb_high_friction_model.py::predict()` — thread the
   new params through. Read `opposing_lineup` from `prop` dict.
5. `services/feature_hydration.py` — populate `opposing_lineup` on
   each live MLB pitcher prop via
   `mlb_live_lineup_feed.fetch_opposing_lineup(...)`. Wire the new
   field into the imputed-fields counter.
6. `services/scoring/prop_scores_store._SCORE_OUTPUT_FIELDS` and
   `services/scoring/recompute.py` mirror block — allowlist new
   feature names + `opposing_lineup_size` for diagnostic visibility.
7. Add 8-12 tests covering the feature builder pitcher-branch shape,
   the predict-path threading, and the live feed fallback chain.

**Goal:** wire the new features into the training feature builder and
the live prediction path. ✅ COMPLETE — see *Session 2 deliverables* above.

1. `services/mlb_high_friction_model.py::_build_friction_features`
   — added pitcher-context branch. ✅
2. Park-factor features — confirmed already emitted by v3.1 builder. ✅
3. Pitcher recent-form — confirmed already emitted via
   `pitcher_statcast_features` path. ✅
4. `services/mlb_high_friction_model.py::predict()` — threads
   `opposing_lineup` through. ✅
5. `services/feature_hydration.py` — populates `opposing_lineup` per
   live MLB pitcher prop with inline `rolling_14` per batter. ✅
6. `_SCORE_OUTPUT_FIELDS` widened for `opposing_lineup_size`. ✅
7. 12 builder/predict/hydration tests added. ✅

## Session 3 plan (next)

**Goal:** retrain pitcher models on v3.2 features, validate, recompute.

1. `scripts/phase2b_retrain_worker.py` — mirrors phase2a worker.
   Builds + caches `lineup_resolver` + extended SC caches.
2. `scripts/_phase2b_launcher.py` — daemonized launcher.
3. Run retrain across 5 pitcher stats:
   `pitcher_strikeouts`, `pitcher_outs`, `earned_runs`,
   `walks_allowed`, `hits_allowed`. Target ~90-180s/stat.
4. Chunked recompute (`POST /api/scores/recompute/mlb/chunked`)
   restricted to pitcher canonical_keys.
5. Validation script: per-stat R² delta, calibration delta,
   fake-negative-edge cluster reduction. Mirrors Phase 2A audit.
6. Critical audits: pitcher_strikeouts, earned_runs, pitcher_outs.

## Workdir paths

- `/app/backend/models/mlb_hf/_phase2b_workdir/` — resolver pickles,
  progress.json, train_report.json (created by Session 3).
- `/app/backend/models/mlb_hf/_pre_phase2b_backup_2026_05_15/` —
  rollback target if v3.2 calibration regresses.

## Rollback procedure (Session 3 safety net)

```bash
cd /app/backend/models/mlb_hf
cp _pre_phase2b_backup_2026_05_15/mlb_hf_*.pkl ./
sudo supervisorctl restart backend
# Recompute pitcher canonical_keys to overwrite v3.2 score docs.
```

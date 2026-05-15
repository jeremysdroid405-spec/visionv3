# Phase 2B — MLB_HF Pitcher Context Retrain

**Status:** Session 1 (infrastructure) complete · Sessions 2 + 3 pending.
**Target model version:** `MLB_HF_v3.2_phase2b`
**Backup of v3.1 pickles:** `/app/backend/models/mlb_hf/_pre_phase2b_backup_2026_05_15/`

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

## Session 2 plan (next)

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

## Session 3 plan

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

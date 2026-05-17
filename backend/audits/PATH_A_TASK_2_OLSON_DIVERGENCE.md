# Path A Task 2 — Olson μ Divergence Root-Cause Report

**Date:** 2026-05-17
**Mode:** READ-ONLY investigation (no patches applied)
**Subject:** Matt Olson, `total_bases`, 2026-05-06

---

## 1. The contradiction (resolved)

| Path | Raw XGBoost output | Final μ | Source code |
|---|---:|---:|---|
| `MLBHighFrictionModel.predict()` | **1.23** | **2.25** | `services/mlb_high_friction_model.py:1457` |
| `mlb_replay_engine.replay_one()` | **7.80** | **7.80** | `services/replay/mlb_replay_engine.py:157` |

Both paths load **byte-identical** model files:
- Model pickle: `/app/backend/models/mlb_hf/mlb_hf_total_bases.pkl`
  (last modified May 16 01:30; directory locked read-only).
- Same in-process `MLBHighFrictionModel` instance loaded once,
  `model.models[total_bases]` is one Python object shared by both calls.
- Backups exist (`_pre_phase2a_backup_*/`, `_pre_phase2b_backup_*/`) but
  neither code path touches them. They are not the cause.

**Verdict:** The divergence is NOT a stale-model artifact. The two
paths build **different feature vectors** from the same player/date,
and the model — correctly — produces different μ values.

---

## 2. Where the feature vectors diverge

Direct comparison of the 222 trained columns for Olson 05-06:

- **115 features differ** between the two vectors.
- **74 features are populated in `predict()` but exactly zero in
  `replay_one()`.** These are the model's missing inputs.
- 8 differ in the opposite direction (e.g. handedness one-hots —
  immaterial).

### Top categories of the divergence

| category | feats differing | total in category | example |
|---|---:|---:|---|
| **`pa_b_*` (PA-windowed batter Statcast)** | **49** | 49 | `pa_b_pa_season_plate_appearances` predict=1598, replay=0 |
| **`vs_lhp_*` / `vs_rhp_*` (platoon splits)** | **21** | 21 | `vs_rhp_ab` predict=428, replay=0 |
| `sc_b_r*` (rolling Statcast batter) | 33 | 33 | `sc_b_r7_plate_appearances` predict=24, replay=31 |
| home/away splits | 6 | 6 | `home_avg` predict=0.278, replay=0 |
| `matchup_*` (handedness one-hot) | 6 | 14 | `batter_is_lhh` predict=1, replay=0 |

### Why replay's feature vector is impoverished

Looking at `mlb_replay_engine.replay_one()`:

```python
player = _build_player_dict(cache_row)   # ← strips vs_left/vs_right
game_logs = _build_game_logs(cache_row)  # ← only `{stat,date,PA}` per log
…
feats = model._build_friction_features(
    player, game_logs, stat_family,
    …
    pa_batter_features=None,              # ← always None
    batter_hand=None,                     # ← always None
    opp_pitcher_throws=None,              # ← always None
    opp_pitcher_features=None,            # ← always None
    opposing_lineup=None,                 # ← always None
)
```

Compare to `MLBHighFrictionModel.predict()`:

```python
player = master_hub.find_one({…})         # full doc — has vs_left/vs_right
game_logs = player.get("bdl_game_logs")   # full per-game stats
sc_batter = self._get_batter_sc_latest(player)
pa_batter = self._get_pa_cache().batter_features(mlbam_id, as_of)
feats = self._build_friction_features(
    player, game_logs, stat_family,
    statcast_features=sc_batter,
    pa_batter_features=pa_batter,         # ← real PA cache
    batter_hand=batter_hand,              # ← caller-supplied
    …
)
```

**The original handoff hypothesis was directionally correct** —
roughly 70–115 features ARE missing at replay inference. The Phase 2C
Olson trace earlier in this session (which showed Δμ=0 between predict
modes) MISSED this because it used the master-hub player dict for
both modes; it never reproduced `replay_one`'s synth-from-cache path.

---

## 3. Answers to the user's six specific questions

| # | Question | Answer |
|---|---|---|
| 1 | Did 7.9 μ exist in stored replay outputs, or stale artifact? | **STORED.** 7.90 μ appears in `mlb_replay_model_outputs` AND `mlb_production_replay_outputs`. Not a stale artifact. |
| 2 | Was 7.9 caused by `replay_one()` post-processing? | **No.** Raw XGBoost output is already 7.80. Post-processing only adds `mu = raw_pred * park_factor` (pf=1.0 here). |
| 3 | Doubleheader / cross-midnight attribution? | **No.** event_id is single (one game on 05-06 for Olson). Issue is upstream of grading. |
| 4 | Park factor or stat-family multiplier? | **No.** `park_factor=1.0` in both paths. |
| 5 | Line-specific handling? | **No.** μ varies 7.78–7.90 across lines 0.5/1.5/2.5/3.5/4.5/5.5 — driven by line-aware features (`current_hit_streak`, `hit_rate_l5`), not a per-line multiplier. |
| 6 | Stale pre-Phase-2b outputs? | **No.** Outputs carry `scoring_config_version=scoring_v3.1_phase2a__wz_rewrite_2026_05_16`, same as current code. |
| 7 | Does current replay ever produce μ > 4.5 for hitter total_bases? | **YES, 1,248 rows** on 2026-05-06 alone. p95=5.16, p99=7.07, max=7.90. Olson is not unique — every batter with a similar feature-vector profile inflates. |

---

## 4. Slate-wide contamination

For `total_bases` on 2026-05-06:

| Collection | rows | max μ | p99 μ | p95 μ | median μ | rows μ > 4.5 |
|---|---:|---:|---:|---:|---:|---:|
| `mlb_replay_model_outputs` | 12,372 | 7.90 | 7.07 | 5.16 | 1.97 | **1,248** |
| `mlb_production_replay_outputs` (Phase 2c) | 12,372 | 7.90 | 7.07 | 5.16 | 1.97 | **1,248** |

Both collections are equally contaminated because the Phase 2c runner
consumes its inputs from `mlb_replay_model_outputs`. Phase 2c did not
introduce the issue; it faithfully forwarded what was already wrong.

A reasonable upper bound for a batter `total_bases` projection is ~4.0
(only ~3% of MLB starts produce ≥4 total bases). The 1,248 rows above
4.5 are all spurious.

---

## 5. Is current replay still contaminated?

**Yes.** Every output row in:

- `mlb_replay_model_outputs` (Layer 3, the source of truth that Phase 2c reads)
- `mlb_replay_gate_results` (Layer 4 gate eval reads from Layer 3)
- `mlb_replay_backtest_runs` (Layer 4 backtest stats)
- `mlb_production_replay_outputs` (Phase 2c runner consumes Layer 3)
- `mlb_production_replay_runs` (run-level aggregates derived from above)

…is contaminated for any prop where `replay_one`'s feature
synthesis skipped a heavily-weighted feature (platoon, PA-windowed
Statcast, home/away splits). This is most batter props; pitcher props
similarly miss `pa_pitcher_*` and `opposing_lineup` features.

---

## 6. Do stored outputs need invalidation?

Yes. The 15-day sweep results in `mlb_replay_backtest_runs`,
`mlb_replay_gate_results`, `mlb_replay_model_outputs`, and the Phase 2c
collections are all suspect for ranking/ROI conclusions.

Recommended scope of invalidation (decision deferred to user):

- **Soft mark**, not delete: add a flag column to existing rows so
  audit history is preserved. E.g. tag the run docs with
  `feature_hydration_status: "PRE_REPLAY_HYDRATION_FIX_2026_05_17"`.
- **Hard delete + rebuild**: drop and re-ingest Layer 3 / Layer 4 /
  Phase 2c collections for the affected dates after the replay
  engine's feature synthesis is fixed.

The replay feature cache (`mlb_replay_feature_cache`) and historical
alt-odds (`mlb_historical_alt_odds_raw`) DO NOT need rebuilding —
they're inputs, not outputs.

---

## 7. The actual fix (NOT applied — pending user direction)

`mlb_replay_engine.replay_one()` needs to hydrate the synthesized
feature inputs to match what `predict()` provides live. Concrete steps:

1. **PA-window features (49 zeros → real).** Use the existing
   `MLBHighFrictionModel._get_pa_cache()` (already as-of-date-aware via
   `pa_cache.batter_features(mlbam_id, as_of=cache_row['game_date'])`).
   Drop the hard-coded `pa_batter_features=None`.

2. **Platoon splits (21 zeros → real).** Extend the feature-cache row
   builder (`mlb_feature_cache.py`) to persist as-of-date `vs_left` and
   `vs_right` rolling totals so `_build_player_dict` can include them.
   OR: short-circuit by fetching them on-demand at replay time from
   `mlb_master_hub_2026` and snapshot to as-of-date (master_hub holds
   season-cumulative which is acceptable when the cutoff date is
   within the same season).

3. **Home/away splits (6 zeros → real).** Same approach as platoon.

4. **Phase-2A matchup** (`batter_hand`, `opp_pitcher_throws`,
   `opp_pitcher_id`). Already resolvable from the historical alt-odds
   row's `home_team`/`away_team` + Phase-3+ probable-pitcher lookup.
   For now, at minimum, populate `batter_hand` from the synth-player
   `bat_side` (which IS already on the cache row).

5. **Validation check (insert after fix).** Hash the feature dict
   produced for a known-good sample (Olson 05-06) and assert μ ≈ 2.25
   ± 0.3 to lock the fix in CI.

---

## 8. Can Phase 3 proceed safely?

**No, not yet.** Phase 3 (production card extraction) is a downstream
ranking/dedup layer. Running it on contaminated μ values produces
contaminated cards. The order must be:

1. Fix `replay_one` feature synthesis (the bug found in this report).
2. Add a μ-sanity regression test (Olson μ ≈ 2.25 ± 0.3).
3. Rebuild Layer 3 + Layer 4 + Phase 2c outputs for the 15-day window.
4. THEN ship Phase 3.

If you want to ship Phase 3 immediately on top of contaminated data
to validate the extraction logic itself (parallel work), the
production card collection must carry a
`feature_hydration_status` tag so we can purge them after the rebuild.

---

## 9. Artifacts (read-only)

- `audits/path_a_task_2_olson_divergence.py` — slate scan + identity table
- `audits/path_a_task_2_olson_divergence.json` — raw findings
- `audits/path_a_task_2b_feature_diff.py` — 222-column diff
- `audits/PATH_A_TASK_2_OLSON_DIVERGENCE.md` — this report

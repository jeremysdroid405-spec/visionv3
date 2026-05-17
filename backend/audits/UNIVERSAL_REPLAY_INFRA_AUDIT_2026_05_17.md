# Universal Testing/Backtesting Infrastructure — Read-Only Audit

**Date:** 2026-05-17
**Mode:** READ-ONLY — no mutations executed

---

## 1. Replay/Backtest Files (Python modules)

### 1a. `services/replay/` — sport-agnostic infrastructure + MLB layers

| File | Lines | Purpose | Universal? | Active? |
|---|---:|---|:---:|:---:|
| `providers/sport_adapter.py` | 120 | Abstract `SportReplayAdapter` ABC | ✅ Universal | ✅ |
| `providers/base.py` | 123 | Abstract provider ABCs | ✅ Universal | ✅ |
| `providers/historical.py` | 174 | `UniversalReplayProvider` historical mode | ✅ Universal | ✅ |
| `providers/live.py` | 89 | `UniversalReplayProvider` live mode | ✅ Universal | ✅ Phase 2c |
| `providers/audit.py` | 126 | Serial-counter + git-SHA + version-pin helpers | ✅ Universal | ✅ |
| `providers/schemas.py` | 127 | Pydantic: `ProductionReplayRun/Output/Card` | ✅ Universal | ✅ |
| `providers/mlb_adapter.py` | 205 | MLB concrete adapter | MLB | ✅ |
| `providers/nba_adapter.py` | 66 | **SKELETON ONLY** | — | 🔶 stub |
| `providers/nfl_adapter.py` | 65 | **SKELETON ONLY** | — | 🔶 stub |
| `production_replay_runner.py` | 472 | Phase 2c orchestrator (Layer-3 + Layer-4 + writes to `*_production_replay_*`) | ✅ Universal | ✅ |
| `mlb_replay_engine.py` | 616 | MLB Layer-3 model replay (post-hydration `v1.1`) | MLB | ✅ |
| `mlb_feature_cache.py` | 538 | MLB Layer-2 feature-cache builder | MLB | ✅ |
| `mlb_replay_gate_eval.py` | 454 | MLB Layer-4 gate eval + grading (war-zone-only) | MLB | ✅ |
| `mlb_replay_multi_tier_eval.py` | 637 | MLB Layer-4 **multi-tier** sweep (SH/FL/WZ) | MLB | ✅ |
| `historical_alt_odds_ingest.py` | 539 | MLB Layer-1 alt-odds ingest | MLB | ✅ |
| `engine.py` | 1,201 | **NBA-era** replay scoring engine (pre-universal) | NBA | 🟡 legacy |
| `vk2_historical.py` | 560 | NBA VK2 historical scorer | NBA | 🟡 legacy |
| `full_ingest.py` | 321 | "Full 30-day NBA historical ingest driver" | NBA | 🟡 legacy |
| `ingest_odds.py` | 272 | NBA per-event odds ingest helper | NBA | 🟡 legacy |
| `ingest_progress.py` | 142 | NBA resumable checkpoint store | NBA | 🟡 legacy |
| `ingest_telemetry.py` | 194 | NBA ingest telemetry | NBA | 🟡 legacy |
| `injury_history.py` | 537 | NBA as-of-time injury layer | NBA | 🟡 legacy |
| `matchup.py` | 395 | NBA matchup/pace context | NBA | 🟡 legacy |
| `normalizer.py` | 196 | NBA odds normalizer | NBA | 🟡 legacy |
| `result_ingester.py` | 326 | NBA outcomes ingester | NBA | 🟡 legacy |
| `resolver.py` | 192 | NBA outcomes joiner | NBA | 🟡 legacy |
| `scoring_only.py` | 390 | NBA Stage-C scoring (no ingest) | NBA | 🟡 legacy |
| `snapshot_plan.py` | 93 | NBA 8-window pregame ladder | NBA | 🟡 legacy |
| `markets.py` | 59 | NBA market + book whitelist | NBA | 🟡 legacy |
| `cache.py` | 300 | NBA cache + fingerprint registry | NBA | 🟡 legacy |
| `canary_events.py` | 57 | NBA canary fixture | NBA | 🟡 legacy |
| `leakage_checks.py` | 134 | As-of-time integrity checks | ✅ Universal | 🟢 reused |
| `run_header.py` | 120 | Run-versioning helper | ✅ Universal | 🟢 reused |
| `schema.py` | 208 | Collection schema + indexes | mostly NBA | 🟡 |
| `odds_fetch.py` | 119 | The Odds API envelope wrapper | ✅ Universal | ✅ |

### 1b. `services/scoring/` — production live pipeline (reused by replay)

| File | Universal? | Active? |
|---|:---:|:---:|
| `tier_evaluator.py` | ✅ (with Phase-2a `feature_provider=None` seam) | ✅ |
| `metrics_builder.py` | ✅ | ✅ |
| `gates/engine.py` | ✅ | ✅ |
| `gates/schema.py` | ✅ | ✅ |
| `best_book.py` | ✅ | ✅ |
| `stat_family.py` | ✅ | ✅ |
| `adapters/mlb_scoring.py` | MLB | ✅ |
| `adapters/nba_scoring.py` | NBA | ✅ |

### 1c. `audits/` — one-off scripts (NOT part of SSOT)

19 files, all ad-hoc. Key recent additions:

- `path_a_*.py` — feature-parity + hydration-fix scripts
- `phase2c_smoke_test.py` — 8-check Phase 2c smoke
- `run_replay_canaries.py` — Tier 2 μ canary runner
- `rebless_canary_bands.py` — band re-calibration
- `path_a_layer3_only.py` — per-date Layer-3 rebuild driver
- `path_a_phase2c_0505.py` — Phase 2c-only driver
- `path_a_task_6_olson_only_harness.py` — 8s harness

These are **investigative**, not infrastructure. They should NOT become the production test runner.

### 1d. `tests/replay/` — Tier 1 pytest canaries (NEW 2026-05-17)

| File | Universal? |
|---|:---:|
| `conftest.py` (session-scoped fixtures: db, model, Olson context) | hooks for any sport |
| `test_predict_replay_parity.py` (predict↔replay parity, MLB Olson anchor) | MLB-anchored |
| `test_hub_extras_hydration.py` | MLB-anchored |
| `test_single_thread_guard.py` | universal |
| `test_feature_vector_hash.py` (3-player hash lock) | MLB-anchored |

13 pytest passes, 1 skip. Runtime ~8s.

---

## 2. Mongo collections — replay/backtest universe (21 collections, 967 MB on disk)

| Collection | Docs | BSON MB | Disk MB | Time range | Status |
|---|---:|---:|---:|---|:---:|
| **`replay_props_normalized`** | 3.45 M | 2,381 | 281 | snap_label `close…t-90m` | NBA legacy |
| **`mlb_replay_gate_results`** | 1.26 M | 1,340 | 188 | 2026-05-01 → 05-15 | ✅ active (multi-tier output) |
| `replay_vk2_cache` | 314 K | 1,337 | 307 | snap `t-30m` | NBA legacy |
| **`mlb_replay_model_outputs`** | 487 K | 478 | 79 | 2026-05-01 → 05-15 | ✅ active (Layer-3) |
| `replay_odds_snapshots` | 24 K | 451 | 86 | NBA snap labels | NBA legacy |
| **`mlb_production_replay_outputs`** | 88 K | 85 | 7 | 2026-05-05 only | ✅ active (Phase 2c) |
| **`mlb_replay_feature_cache`** | 16 K | 43 | 8 | 2026-05-01 → 05-15 | ✅ active (Layer-2) |
| **`forward_test_snapshots`** | 3,360 | 30 | 8 | 2026-04-13 → 05-15 | ✅ active (live capture) |
| `forward_test_outcomes` | 510 | 2 | 1 | 2026-04-13 → 04-22 | 🟡 STALE since 04-22 |
| `replay_results` | 2,894 | 2 | 0.4 | — | NBA legacy |
| `replay_ingest_progress` | 1,438 | 1 | 0.2 | — | NBA legacy |
| `backtest_game_logs` | 6,455 | 1 | 1 | — | unclear, probably one-off |
| **`mlb_replay_backtest_runs`** | 45 | 0.4 | 0.2 | 2026-05-17 | ✅ active (multi-tier summary) |
| **`mlb_replay_audit`** | 42 | 0 | 0.1 | 2026-05-17 | ✅ active (per-run audit) |
| `mlb_replay_model_status` | 14 | 0 | 0 | per-date | ✅ active (Layer-3 status) |
| `forward_test_metrics` | 18 | 0 | 0 | 2026-04-27 last update | 🟡 stale |
| `mlb_replay_feature_status` | 15 | 0 | 0 | per-date | ✅ active (Layer-2 status) |
| **`mlb_production_replay_runs`** | 4 | 0 | 0 | 2026-05-17 | ✅ active (Phase 2c per-run doc) |
| `replay_engine_progress` | 1 | 0 | 0 | — | NBA legacy |
| `mlb_prodreplay_serial_counter` | 1 | 0 | 0 | — | ✅ active (atomic seq) |
| `mlb_replay_serial_counter` | 0 | 0 | 0 | — | empty, probably unused |

**~2.1 GB on disk is NBA-legacy replay collections** (`replay_props_normalized`, `replay_vk2_cache`, `replay_odds_snapshots`, `replay_results`, `replay_ingest_progress`, `replay_engine_progress`) — candidates for cleanup if NBA replay isn't actively in use.

---

## 3. Audit-ID fields already stored

| Collection | serial | git_commit | pipeline_version | adapter_version | scoring_v | gate_v | feature_cache_v | model_versions |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| `mlb_production_replay_runs` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `mlb_production_replay_outputs` | ✅ | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ |
| `mlb_replay_audit` | ✅ | ❌ | ❌ | ❌ | ✅ | ✅ | ✅ | ❌ |
| `mlb_replay_backtest_runs` | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ | ❌ | ❌ |
| `mlb_replay_model_outputs` | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ | ✅ | ❌ |
| `mlb_replay_gate_results` | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ | ❌ | ❌ |
| `forward_test_snapshots` | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| `forward_test_outcomes` | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |

**Phase 2c (`mlb_production_replay_runs/outputs`) is the most audit-complete.** Multi-tier (`backtest_runs`) and Layer-3/4 (`model_outputs`, `gate_results`) carry partial pins. Forward test (`forward_test_*`) has **NO audit pins** — that's a real gap.

---

## 4. Existing pipeline capability matrix

| Capability | Where | Status |
|---|---|:---:|
| Run one date | `mlb_replay_engine.replay_date(date)` + `production_replay_runner.run_production_replay(date)` | ✅ |
| Run one sport | adapter registry in `production_replay_runner._ADAPTER_REGISTRY` | ✅ for MLB; 🔶 NBA/NFL stubs |
| Run all props | yes (Layer-3 iterates all alt-odds rows × cache) | ✅ |
| Run model inference | `MLBHighFrictionModel.predict()` + cached via `replay_one()` | ✅ |
| Apply gates | Layer-4 `mlb_replay_gate_eval.evaluate_gates` (WZ only) + `mlb_replay_multi_tier_eval` (SH+FL+WZ) | ✅ |
| Simulate $1 wager | `MLBReplayAdapter.grade_outcome(stake=1.0)` | ✅ |
| Grade outcomes | `MLBReplayAdapter.grade_outcome` + multi-tier grader | ✅ |
| Return tier HR/ROI | `mlb_replay_multi_tier_eval` writes `overall` + `by_*` | ✅ |
| Return odds-bucket ROI | `by_odds_bucket` dict in `mlb_replay_backtest_runs` | ✅ |
| Return prop-type win rate | `by_stat_family` in `mlb_replay_backtest_runs` | ✅ |
| Re-run same date with diff gates | Layer-4 multi-tier (separate `gate_config_version` per tier) | ✅ for tier sweep; ❌ for arbitrary gate-knob testing |
| Compare two test IDs | `mlb_replay_audit` has `pick_set_checksum` | 🔶 stored but no diff/compare tool exists |

**Gap:** no first-class CLI/endpoint to A/B-compare two stored runs by serial.

---

## 5. Universal vs MLB-only split

| Component | Universal? | MLB-only? | Notes |
|---|:---:|:---:|---|
| `SportReplayAdapter` ABC | ✅ | — | Concrete: MLB done, NBA/NFL stub |
| `UniversalReplayProvider` | ✅ | — | Used by Phase 2c |
| `production_replay_runner.run_production_replay` | ✅ | — | Sport switch via `sport=` kwarg |
| `ProductionReplayRun/Output/Card` schemas | ✅ | — | Sport-agnostic Pydantic |
| Serial counter + audit pins | ✅ | — | One counter per sport (per-adapter prefix) |
| Layer-1 (alt-odds ingest) | — | MLB | `historical_alt_odds_ingest.py` |
| Layer-2 (feature cache) | — | MLB | `mlb_feature_cache.py` |
| Layer-3 (model replay) | — | MLB | `mlb_replay_engine.py` |
| Layer-4 (gates + grading) | — | MLB | `mlb_replay_gate_eval.py`, `mlb_replay_multi_tier_eval.py` |
| Live tier evaluator | ✅ | — | `scoring/tier_evaluator.py` (with `feature_provider=None` seam) |
| Live scoring | ✅ | — | `scoring/scoring_stack.py` + per-sport adapters |
| Forward test capture | ✅ | — | `forward_test_snapshots` carries `sport=mlb/nba` |
| Tier 1 pytest | ✅ infra, MLB anchors | — | Adding NBA/NFL anchor tests is trivial |
| Tier 2 canaries (μ band JSON) | ✅ | — | Currently 8 MLB canaries |
| `engine.py` (NBA replay) | — | NBA | Pre-universal legacy |
| `vk2_historical.py` | — | NBA | Pre-universal legacy |

**Net:** the **scaffold is universal**; the **concrete pipeline implementation is MLB-only**. NBA/NFL adapters exist as stubs that throw `NotImplementedError` for `fetch_actuals` / `grade_outcome` / `feature_provider`.

---

## 6. Duplicate / legacy classification

### KEEP (active SSOT)
- `services/replay/providers/*` — universal scaffold
- `services/replay/production_replay_runner.py` — Phase 2c orchestrator
- `services/replay/mlb_replay_engine.py` — MLB Layer-3 (post-hydration v1.1)
- `services/replay/mlb_feature_cache.py` — MLB Layer-2
- `services/replay/mlb_replay_gate_eval.py` — single-tier (WZ)
- `services/replay/mlb_replay_multi_tier_eval.py` — multi-tier (SH/FL/WZ)
- `services/replay/historical_alt_odds_ingest.py` — MLB Layer-1
- `services/scoring/tier_evaluator.py` (Phase-2a seam)
- `tests/replay/*` — Tier 1 pytest
- `audits/run_replay_canaries.py` + `replay_mu_canaries.json` — Tier 2
- `mlb_production_replay_runs/outputs` — most-audited canonical store
- `mlb_replay_model_outputs/feature_cache/gate_results/backtest_runs/audit` — supporting MLB pipeline

### DEPRECATE (NBA legacy, pre-universal)
- `services/replay/engine.py` (1,201 lines, NBA replay scoring)
- `services/replay/vk2_historical.py`
- `services/replay/full_ingest.py`
- `services/replay/ingest_odds.py`, `ingest_progress.py`, `ingest_telemetry.py`
- `services/replay/injury_history.py`, `matchup.py`, `normalizer.py`
- `services/replay/result_ingester.py`, `resolver.py`, `scoring_only.py`
- `services/replay/snapshot_plan.py`, `markets.py`, `cache.py`, `canary_events.py`, `schema.py`
- Collections: `replay_props_normalized`, `replay_vk2_cache`, `replay_odds_snapshots`,
  `replay_results`, `replay_ingest_progress`, `replay_engine_progress`
- ~2.1 GB on disk recoverable

### DELETE LATER (after migration & one final test sweep)
- The above NBA-legacy services should be re-implemented under the universal scaffold via `NBAReplayAdapter` before deleting the NBA-legacy code. The DB collections can be dropped once nothing references them.

### MIGRATE NEXT
- `forward_test_snapshots` / `forward_test_outcomes` should be moved under the universal schema (add `replay_serial` + `production_pipeline_version` audit pins). Outcomes collection has been stale since 2026-04-22 — possibly already broken.

---

## 7. Final answer

### What we already have ✅
- A **fully universal scaffold** (`SportReplayAdapter`, `UniversalReplayProvider`,
  `production_replay_runner`, Pydantic schemas, audit/serial helpers).
- A **complete MLB Layer-1→4 pipeline** end-to-end (ingest → feature cache → model
  replay → gates+grading → multi-tier sweep).
- **Phase 2c orchestrator** that writes the audit-pinned canonical store
  (`mlb_production_replay_runs/outputs`).
- **Tier 1 pytest** + **Tier 2 μ-canary** regression suite running clean (13/13 + 8/8).
- **NBA-legacy replay** that historically worked but is pre-universal.
- **NBA/MLB live forward-test** capture (`forward_test_snapshots`, 3,360 docs).

### What is missing 🟡
- **NBA + NFL concrete adapters** (stubs only — no `feature_provider`, `fetch_actuals`, or `grade_outcome`).
- **A/B comparison tool** that takes two `replay_serial`s and produces a diff report (HR, ROI, qualified set, edge buckets).
- **Audit pins on forward-test collections** — currently zero version/serial/git pins on `forward_test_*`.
- **Phase 3 card extraction** — top-N + per-game dedup not yet a pure function callable from both live and replay.
- **Phase 4 gate engine swap** — Layer-4 still uses `mlb_replay_gate_eval.evaluate_gates`, not the production gate engine.
- **CLI / admin endpoint** to launch a replay run from outside Python.

### What is duplicate ❌
- The NBA-legacy replay code path (`engine.py`, `vk2_historical.py`, et al.) is a
  parallel universe to the universal scaffold. Until NBA is ported, the two coexist.

### What should become SSOT
- **Code:** `services/replay/providers/` + `services/replay/production_replay_runner.py`
  (universal); plus per-sport `*_replay_engine.py` / `*_feature_cache.py` /
  `*_replay_*_eval.py` (Layer-1 through Layer-4 per sport).
- **DB:** `{sport}_production_replay_runs` + `{sport}_production_replay_outputs` +
  `{sport}_production_replay_cards` (when Phase 3 lands).
- **Audit pins:** the 9-field set used by `mlb_production_replay_runs` (serial,
  git_commit_sha, production_pipeline_version, adapter_version, scoring_config_version,
  gate_config_version, feature_cache_version, model_versions, input_collection_versions).
- **Tests:** `tests/replay/` (Tier 1) + `audits/replay_mu_canaries.json` (Tier 2).

### Exact next smallest step
**Add a single A/B-compare CLI** that takes two `replay_serial`s and produces a
unified diff report (HR delta, ROI delta, qualified-set Jaccard distance, top-edge
picks, by-bucket comparison).

Rationale:
1. It's pure read-only over already-pinned data.
2. It uses the audit fields we already store.
3. It unblocks "did this change make things better or worse?" testing in seconds
   without rebuilding anything.
4. Sport-agnostic by construction (reads `{sport}_production_replay_runs`).
5. Lays the groundwork for testing Phase 3 / Phase 4 / Path-A-style code changes
   against a stable baseline.

Estimated effort: ~150 lines, 1 hour, zero schema changes.

---

**No mutations executed.** This is a discovery report only.

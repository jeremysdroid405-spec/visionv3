# Phase 2c — Universal Production Replay Runner

**Date:** 2026-05-17 (continued)
**Status:** ✅ Complete (Phase 3 NOT started — stopped per user directive)

## What ships
- `services/replay/production_replay_runner.py` — sport-agnostic
  orchestrator: `run_production_replay(db, sport=, game_date=, snapshot_iso=, tier=)`
- `audits/phase2c_smoke_test.py` — full end-to-end smoke test (8 checks)

## Hard guarantees verified
1. **Zero live-path edits** — Phase 2a + 2b regressions still byte-identical
   (`tier_evaluator(feature_provider=None)` + `predict(as_of_date=None)`).
2. **Read-only on legacy** — `mlb_replay_model_outputs` count preserved
   (37,691 rows untouched after 05-06 real run).
3. **Audit-pinned runs** — every run persisted with:
   - 64-char `production_pipeline_version` SHA over the adapter's pinned files
   - 64-char `adapter_version` SHA over the MLB adapter module
   - Input collection counts (`mlb_historical_alt_odds_raw`,
     `mlb_replay_feature_cache`, `mlb_master_hub_2026`,
     `mlb_statcast_player_features`)
   - Per-tier serial: `MLB-PRODREPLAY-YYYYMMDD-WZ-HHMMUTC-NNNNN`
   - `git_commit_sha`, `replay_started_at`, `replay_completed_at`,
     `elapsed_s`, `rss_mb_peak`
4. **Schema-conformant outputs** — every persisted row passes
   `ProductionReplayOutput` Pydantic validation; 31 fields including
   grade_status / actual_value / profit_units.
5. **Sport-agnostic** — `_resolve_adapter()` routes MLB/NBA/NFL via
   registry; unsupported sport rejected with `ValueError`.

## Real-world run results
| Date       | Scanned | Qualified | W / L / P     | Hit Rate | ROI    | Elapsed |
|------------|--------:|----------:|---------------|---------:|-------:|--------:|
| 2026-05-05 |  25,431 |       768 | 461 / 93 / 0  |  83.21 % |  24.13%|   1.06s |
| 2026-05-06 |  37,691 |     1,080 | 561 / 421 / 0 |  57.13 % | -12.43%|   5.64s |

The 26-point hit-rate collapse between consecutive dates IS the
Train/Serve P0 signature (the 7.9μ Matt Olson inflation lives here).
Path A (after Phase 3) will close that gap.

## What's NOT in Phase 2c (per directive: "stop before Phase 3")
- **No card-rendering / top-N dedup.** `ProductionReplayCard` collection
  is not written yet. Phase 3 surgically extracts the `picks_getter_service.py`
  per-game dedup + top-N logic into a pure function.
- **No swap of Layer-4 gates to production gate engine.** Phase 4 will
  replace `mlb_replay_gate_eval.evaluate_gates()` with
  `evaluate_tier_with_overrides(metrics, feature_provider=...)` once
  Phase 3 ships the NormalizedMetrics builder for replay rows.

## Collections written
- `mlb_production_replay_runs`        (1 doc / run, indexed on serial + sport+date+tier)
- `mlb_production_replay_outputs`     (1 doc / scanned prop, compound-unique key)
- `mlb_prodreplay_serial_counter`     (atomic seq counter)

## Files touched (full list)
- `services/replay/production_replay_runner.py` (NEW, 393 lines)
- `audits/phase2c_smoke_test.py` (NEW, 240 lines)
- `audits/PHASE2C_PRODUCTION_REPLAY_HARNESS.md` (this doc)

NO live-pipeline files were edited in Phase 2c.

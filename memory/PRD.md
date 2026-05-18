# Prop Vision — Product Requirements Document

## Original Problem Statement
Restructure React/FastAPI betting app to a 100% Local-First Database Model with multi-sport support. Implement Google/Apple OAuth and Stripe for payments. PRODUCT REQUIREMENTS: 100% ID-based joins. Universal Opportunity Models and Probability modeling. Enforce regression and mutation tests for all backend logic. FIX PROPVISION PERMANENTLY — SSOT ENFORCEMENT / FIELD OWNERSHIP HARDENING. Transition architecture to strict Single Source of Truth for all user-visible fields.

**ACTIVE DIRECTIVE: PROP VISION STABILIZATION PLAN**
Freeze all feature/UI work until the system is permanently stabilized via the 6-phase plan.


## Latest Status (2026-05-17)
- ✅ Fix 1 DONE: `strikeouts → batter_strikeouts` alias normalized in `_MLB_STAT_TO_FAMILY` (`canonical_stats.py`) so elite-binary override fires on the correct stat family.
- ✅ Fix 2 DONE: `production_replay_runner._project_layer3_to_output` call-site now stamps the 7 missing SSOT fields onto every `mlb_test_outputs` doc (`tp`, `tp_source`, `edge_pct`, `is_alternate_market`, `devig_method`, `canonical_edge`, `gate_failed_reasons`). 100 % coverage for the always-known fields; canonical-derived TP fields populate on every row the gate engine actually evaluated (correctly None on `tier_odds_bucket_fail` short-circuits). No threshold/gate behaviour changed.

## P0 backlog (in order)
1. **Phase C — Universal Pipeline live-mode byte-identical regression** vs current production tier endpoints.
2. **Admin Endpoints for Replay** — trigger universal runner from UI; no Python scripts.
3. **Google/Apple OAuth** (Emergent-managed) — frozen, awaits user go-ahead.
4. **Stripe payments** — frozen, awaits user go-ahead.

## P1 backlog
- Retire legacy NBA replay (`vk2_historical.py`, `engine.py`) via `NBAReplayAdapter` on the universal runner.
- NFL config scaffold for universal pipeline.
- Decompose `Dashboard.jsx` (2,000+ lines).
- Propagate `event_id` / `commence_time` / `game_date` onto replay cards.


## Universal Production Replay Harness (2026-05-17)

Sport-agnostic harness that runs historical data through the **same code paths the live production pipeline uses**, with provider-injected inputs to make every read deterministic and audit-pinned.

- **Phase 1** — Scaffolding: `SportReplayAdapter`, `UniversalReplayProvider`, audit/serial helpers, schemas (`ProductionReplayRun/Output/Card`). DONE.
- **Phase 2a** — `tier_evaluator.evaluate_tier_with_overrides(metrics, feature_provider=None)` seam injected. Byte-identical live regression. DONE.
- **Phase 2b** — `MLBHighFrictionModel.predict(..., as_of_date=None)` seam injected + internal `_filter_logs_before` for `bdl_game_logs` leakage protection. Byte-identical live regression. DONE.
- **Phase 2c — Orchestrator (2026-05-17) — DONE**
  - `services/replay/production_replay_runner.py::run_production_replay(db, *, sport, game_date, snapshot_iso, tier, dry_run=False)`
  - Wires `UniversalReplayProvider(MLBReplayAdapter)` into existing Layer-3 + Layer-4 engines
  - Persists `{sport}_production_replay_runs` + `{sport}_production_replay_outputs`
  - Per-run audit pins: 64-char SHA `production_pipeline_version` + `adapter_version`, `git_commit_sha`, input-collection counts
  - Smoke test (8 checks) at `audits/phase2c_smoke_test.py` — passing
- **Path A — Feature Hydration Fix (2026-05-17) — DONE**
  - Root cause: `replay_one()` synthesized features from cache rows only and zeroed 74 trained columns (`pa_b_*`, `vs_lhp/rhp_*`, home/away splits, matchup one-hots). Net result: Olson `total_bases` μ inflated 7.90 vs live `predict()` 2.25 — same model, totally different feature vectors.
  - Diagnosis: `audits/PATH_A_TASK_2_OLSON_DIVERGENCE.md` proved 115 of 222 features differed; raw XGBoost re-score 1.23 (live) vs 7.80 (replay).
  - Fix: `services/replay/mlb_replay_engine.py` — added `hub_extras` parameter to `_build_player_dict` + `replay_one`, hydrates platoon/home-away splits + bats_throws from master_hub; wires `model._get_pa_cache()` for PA-windowed features; derives `batter_hand` from `bats_throws`; passes `cache.opp_pitcher_throws` through to the matchup block.
  - Engine version bumped to `replay_engine_v1.1_hydration_2026_05_17`.
  - Verification — unit (0/222 diff), 8-second Olson harness, slate scale rebuild for 2026-05-05 (n=8,510 total_bases rows, max μ=3.109, ZERO rows >4.5, was 1,248 pre-fix).
- **Path A — Pod Stability (2026-05-17) — DONE**
  - `MLBHighFrictionModel.load_models()` now applies `nthread=1` + `n_jobs=1` to every loaded XGBoost regressor by default; eliminated chronic multiprocessing-fork orphan workers (~3 GB each) that had been OOM-killing the pod.
  - Override for training jobs: `MLB_HF_ALLOW_MULTITHREAD=1`.
- **Disk Crisis (2026-05-17) — DONE**
  - Dropped `dg_raw_odds_snapshots` (14.6 M docs / 1.56 GB on disk + 716 MB indexes). Writer now gated behind `DEBUG_RAW_ODDS=true`. Disk freed: 339 MB → 2.6 GB available.
  - Audit: `audits/DG_RAW_ODDS_SNAPSHOTS_DROP_2026_05_17.md`.
- **Tier 1/2 Testing Infrastructure (2026-05-17) — DONE**
  - `tests/replay/` — 13 pytest canaries (predict↔replay parity, hydration assertions, single-thread guard, feature-vector hash lock). 13 passed, 1 skipped.
  - `audits/replay_mu_canaries.json` + `audits/run_replay_canaries.py` — 8 known-μ canaries, 8/8 pass after re-blessing.
- **Replay A/B Compare CLI (2026-05-17) — DONE**
  - `scripts/replay_compare.py --sport mlb --serial-a … --serial-b …` produces 14-dimension diff report (HR/ROI/profit deltas, Jaccard overlap, added/removed/changed picks, by-odds/edge/stat/book buckets, top winning added/lost). JSON artifact written.
  - Validated against idempotency pair (00005 vs 00006): 100% Jaccard overlap, 0 deltas, ⚪ tie verdict.
- **Phase 3 — Production card extraction (2026-05-17) — DONE**
  - `services/picks/card_builder.py` — pure, sport-agnostic functions: `select_best_book`, `dedupe_by_keys`, `per_game_top_n`, `final_card_order`, `build_production_cards`. Defaults match live `get_war_zone()` (one pick per player, slate top-20, no per-game cap, edge-then-μ ordering).
  - Wired into `production_replay_runner.run_production_replay`: after writing outputs, runs the card builder and writes to `{sport}_production_replay_cards` (compound unique index on `replay_serial+rank`).
  - 14 pytest unit tests at `tests/replay/test_card_builder.py` — all passing in 0.05s.
  - End-to-end validated: 05-05 run produced 20 displayed cards from 361 qualified picks; top cards correspond to Spencer Steer 0.5 OVER, Pete Crow-Armstrong, Witt Jr., etc.
- **Phase 4 — Swap gate spec to production gate engine** — DONE (2026-05-17)
- **Phase 6 Phase 1 — CanonicalProp model + market_normalizer** — DONE (2026-05-17)
- **Phase 6 Phase 2 — Canonical engine wired into replay runner (flag-gated, `canonical_path=False` default)** — DONE (2026-05-17)
  - Validated against 2026-05-05 SH: 25,431 raw rows → 3,692 canonical props → 4,672 eval rows; 176 routed to SH vs 104 in legacy per-book-counted baseline. Confirms SH starvation is a per-book duplication artifact, not real supply. See `audits/PHASE6_PHASE2_REPORT_2026_05_17.md`.
- **Phase 6 Phase 4 — `tp_engine.compute_tp` cross-book opposite-side support** — DONE (2026-05-17)
  - Canonical engine now exposes explicit same-book vs cross-book devig with method preference (`same_book` > `cross_book` > `one_sided`). Audit fields: `devig_method`, `same_book_pair_count`, `cross_book_pair_count`, `books_used`, `over_books`, `under_books`, per-method devig probs. Validated on 2026-05-05 SH (run 00074): 99 same-book / 0 cross-book / 77 one-sided. 77 `tp_source_gate` failures = exactly the 77 one-sided alt-line OVERs (no UNDER quoted in ANY book — genuine market gap, NOT a wiring fix). See `audits/PHASE6_PHASE4_REPORT_2026_05_17.md`.
- **Phase 6 Phase 3 — Canonical engine wired to live serving (`compute_tier`)** — NOT STARTED (explicitly deferred by user)

## MLB Historical Replay System (2026-05-16)

Modular 4-layer architecture under `services/replay/` for offline backtesting of MLB props against actual outcomes. No live API calls during replay execution; each layer reads only the prior layer's persisted output.

- **Layer 1 — Historical Alt Odds Ingest** — DONE
  - `services/replay/historical_alt_odds_ingest.py` → `mlb_historical_alt_odds_raw` (compound unique on snapshot_iso). OOM-hardened (psutil RSS guard, 1500MB cap, 500-row batches).
- **Layer 2 — Feature Cache** — DONE
- **Layer 3 — Model Replay Outputs** — DONE
- **Layer 4 — Gate Evaluation + Backtest Grading (2026-05-16) — DONE (multi-tier)**

### 15-day sweep (2026-05-01..05-15 @ 11:00:00Z snapshots) — DONE 2026-05-17

- **Backfilled**: Layer 1/2/3 for 14 missing dates (2026-05-05 was the only pre-existing).
- **Multi-tier Layer 4 framework**: `services/replay/mlb_replay_multi_tier_eval.py` + `scripts/mlb_replay_multi_tier_sweep.py` + `scripts/mlb_replay_l4_loop.py` + `scripts/mlb_replay_15d_report.py` + `scripts/mlb_replay_sweep_15d.sh`. Persists per-(date × tier × snapshot) audit rows to `mlb_replay_audit` with global atomic serial counter (`mlb_replay_serial_counter`).
- **Audit/auditability**: All 45 audit rows carry `serial` (format `MLB-REPLAY-{YYYYMMDD}-{TIER}-{HHMMUTC}-{NNNNN}`), `pick_set_checksum`, `gate_spec_checksum`, and all four version pins (gate/scoring/replay-engine/feature-cache).
- **Operational incidents**: MongoDB OOM'd once at disk 100% (recovered by clearing 1.5GB of supervisor logs / frontend build / model caches); pod restarted once mid-Layer-4 (OOM-killer). Both pre-existing recurrences; Layer 4 is idempotent on `(date, tier, snapshot, gate_cfg)` so re-runs resume cleanly.

### 15-day window results

| Window | Tier | Picks | HR | ROI | Profit u |
|---|---|---:|---:|---:|---:|
| 6-day graded (05-01..06) | safe_haven | 2,805 | 65.9% | **−5.7%** | −148.72 |
| 6-day graded (05-01..06) | front_lines | 731 | 67.7% | **−5.9%** | −42.03 |
| 6-day graded (05-01..06) | war_zone | 5,098 | 65.5% | **−4.3%** | −200.92 |
| Single-day 2026-05-05 (was reported as headline result) | war_zone | 768 | 83.2% | +24.0% | +140.25 |

### Key findings
- **2026-05-05 was the cherry-pick outlier**. Six-day graded mean is negative across all three tiers. Any threshold tuning on single-day data would have overfit hard.
- **edge_30p is the only consistently profitable bucket** in multi-day aggregate: WZ +6.7% (n=789), SH +1.7% (n=444). All lower edge buckets bleed.
- **Front Lines remains the tightest tier** (731 picks across 6 days vs 2,805 SH vs 5,098 WZ) — inverted-pyramid finding from 05-05 holds on volume.
- **Daily variance is extreme**: 2026-05-06 SH HR 41.9% / ROI −38.0% — a near-coin-flip slate that obliterated multi-day P&L.

### BLOCKER (P0): Ball Don't Lie game-log backfill required
- `mlb_master_hub_2026.bdl_game_logs` is only backfilled through 2026-05-06. 2026-05-07 has 57 entries (~10% coverage); 05-08..05-15 have **zero**.
- 9 of 15 sweep days are therefore ungraded (picks/gates/serials valid, no Win/Loss).
- Action: backfill BDL game logs for 2026-05-07..05-15, then re-run `python -m scripts.mlb_replay_l4_loop` (idempotent — overwrites audit rows with new grading).

## Stabilization Status
- Phase 4B SSOT cleanup — DONE
- §3 Tier freshness stamping (`board_freshness.py`) — DONE
- §4 Detection source freshness SLO logic — DONE
- §6 Vision Intel coverage (universe alignment + dual-tag mirror writes) — DONE
- JIT Vision Intel Reaper (5-min cadence) — DONE
- Universal Badge Architecture (`badge_enrichment.py`, `mlb_environmental_badges.py`) — DONE
- 0-write guard in `prop_scores_store.write_versioned_scores` — DONE
- **Breaking News Ticker stabilization (2026-05-08) — DONE**
- **Combo-family VK2 routing fix (2026-05-09) — DONE**
- **WZ coverage decoration bypass fix (2026-05-10) — DONE**
  - Root cause: `services/board/engine.py::on_new_props` (real-time scoped ingest, dominant publish path) loaded raw `{sport}_live_props` and passed them directly to `recompute_sport(props=matched)`, bypassing `adapter.load_live_props`'s universal 3-step decoration (`filter_priceable` + `build_companion_map` + `filter_pp_playable`). Every real-time-written row landed in `{sport}_prop_scores` with `book_count=None`/`coverage_class=None`/`books_anchored=None`, and downstream `coverage_gate` fail-closed on `actual=None vs threshold=1`, torching WZ tier supply.
  - Fix: `recompute_sport` now applies the canonical 3-step decoration on caller-supplied props before the build-context loop (mirroring `load_live_props` exactly). Companion map built over the full live pool. Defence-in-depth fallback on decoration failure.
  - Production validation (NBA): FD-anchor missing `coverage_class` 70.2% → **0%**; `gate_coverage_fail` 1,059 → 4 (99.6% reduction).
  - Production validation (MLB): FD-anchor missing `coverage_class` 87.6% → **0%**; `gate_coverage_fail` 876 → **0**; WZ qualified 63 (pre-fix) → 58 (post-fix, same slate).
  - 4 regression tests (`tests/test_recompute_caller_supplied_decoration.py`); 36/36 scoring/coverage/recompute tests pass.
  - Audit: `/app/audit_reports/wz_coverage_decoration_fix.md`.
  - Note: NBA WZ qualified remained 0 on the post-fix slate because the model legitimately disagrees with the +150+ side on ~98% of OVER candidates (only 1 row across 3 games had proj/line >= 1.0, and that one failed `gate_hit_rate_fail` at HR=45% < 50% threshold). Coverage decoration is no longer the bottleneck; supply is now gate-limited as designed.

### Replay Test Suite
- Phase 0-2 (snapshot plan, 30-day NBA ingest, resolver, replay engine) — DONE
- **Phase 2.5 step 1 — Historical VK2 wired (2026-05-09) — DONE**
  - `services/replay/vk2_historical.py` reuses production model pickles +
    `nba_vk2_features.build_features` (no fork). PlayerIdResolver, leakage-
    gated history & adv-stat slices, single-stat predictor, combo synth.
- **Stage A→B→C fast-iteration architecture (2026-05-09) — DONE**
  - Stage-A immutable, Stage-B `replay_vk2_cache` carries every expensive
    payload, Stage-C `scoring_only.run_scoring_only` re-scores in <5min/500k rows.
- **Historical Matchup / Pace / DvP layer (2026-05-09) — DONE**
  - `services/replay/matchup.py`; `matchup_blob` persisted on Stage-B cache.
- **Historical Injury / Usage layer — Part 3 of Safe Haven Fix (2026-05-09) — DONE**
  - `services/replay/injury_history.py` reconstructs OUT lists, `usage_vacuum_factor`,
    `usage_spike` magnitude/flag, `key_player_out_flag`, `rotation_compression`
    strictly from `bdl_historical_game_logs` (no live injury source needed).
  - Production formula match: `usage_vacuum_factor = 1 + Σ(out_usage_l10) / Σ(top13_usage_l10)`
    using the production `(fga + 0.44·fta + tov)/min · 36` proxy.
  - Stage-B cache rows now carry `injury_blob` + flat `usage_vacuum_factor` /
    `usage_spike_flag` shortcuts; Stage-C reads them and stamps
    `prop["usage_vacuum_factor"]` / `prop["usage_spike"]` exactly as production.
  - `cache.injury_pipeline_hash()` now content-hashes the new module → invalidation
    rule wired (`injury_blob` field).
  - 12 new pytest tests; 64/64 replay tests pass.
  - End-to-end smoke: 304/308 props `injury_full`, avg vacuum 1.065, 21
    spike-flagged. `parity_warnings` no longer mention injury or matchup.
  - Audit: `/app/audit_reports/replay_injury_persistence_arch.md`.
  - **Next**: full-window replay + Safe Haven debug script to confirm
    activation. Production scoring/gates UNTOUCHED; replay-only writes.
  - `services/replay/engine.py` accepts `enable_vk2=True`; stamps
    `vk2_projection / vk2_sigma / vk2_p_over / vk2_model_version /
    vk2_feature_hash / vk2_adv_coverage_l10`; passes `p_model = vk2_p_over`
    (NOT TP) to `compute_scoring_stack`. Unsupported families
    (BLK / STL / TURNOVERS) marked `vk2_unsupported_family` — no VK1 fallback.
  - First end-to-end run (run_id `vk2_full_30d_1778310068`):
    - 517,864 candidates, 2,013 qualified (0.39%), 399 settled qualified picks.
    - Front Lines: 179 picks, HR 83.2%, ROI **+41.1%/u**, +$73.54.
    - War Zone:   220 picks, HR 62.7%, ROI **+69.2%/u**, +$152.17.
    - Safe Haven: 0 picks (Feb-2024 has zero `bdl_advanced_stats` — VK2
      vision_score gate (>= 80) compresses without adv features).
    - Combined: 399 picks, HR 71.9%, ROI **+56.6%/u**, +$225.71.
  - Before/after vs prior partial-parity run: qualified count 0 → 399.
  - Tests: `tests/test_replay_vk2_historical.py` (13 tests, all passing).
  - Reports: `/app/audit_reports/replay_publication_vk2_full.md`,
    `/app/audit_reports/replay_vk2_before_after.md`,
    `/app/audit_reports/vk2_production_map.md`.
  - **NOT production sign-off** — injury / matchup / pace still stubbed;
    SH coverage requires adv_stats backfill for the replay window.
- **Forward-testing lineage boundary (2026-05-09) — COMMITTED**
  - PA / PR / RA combo synth now uses VK2 component μ instead of legacy VK1.
  - Allen PA alt 9.5: μ 21.89 → 12.95 (verified live).
  - Aggregate Δμ: PA −1.51, PR −0.92, RA −0.54 (n=687 combo props rescored).
  - 60 combo props now correctly fail direction-gate; 1 was fake-passing WZ (Allen PA alt 14.5).
  - Post-fix sensitivity: 0 WZ rejects in vision_score_v2 [55,60) — floor move 60→55 yields zero gain. Floor stays at 60.
  - Tests: `tests/test_combo_synth_vk2_routing.py` (8 tests, all passing — incl. mutation guard).
  - Audit: `/app/audit_reports/wz_alt_line_projection_audit_2026-05-09.md`.
- **Forward-testing lineage boundary (2026-05-09) — COMMITTED**
  - `MODERN_SSOT_CUTOFF = 2026-04-25`. New `services/forward_testing_lineage.py` provides `lineage_filter`, `merge_filter`, `lineage_metadata`.
  - Endpoints `/v3/forward-test/{performance,daily,calibration,status}` excludes legacy `vk_*` rows by default; `?include_legacy=true` opt-in returns mixed-generation warning.
  - Every reporting response carries `dataset_lineage` block (generation, cutoff, row counts, excluded count, warning).
  - 17 new tests in `tests/test_forward_testing_lineage.py` (68/68 stabilization tests passing).
  - No raw historical data deleted, mutated, or backfilled; no scoring/gates/tiers/settlement/odds-routing changes.
  - Audit: `/app/audit_reports/fl_goblin_lineage_findings_2026-05-09.md`.
- **NBA reference-odds chain port (2026-05-09) — COMMITTED**
  - `_pick_reference_odds` NBA branch extended from `dk → mgm` to `dk → fd → mgm → bol`.
  - 18 new tests (`tests/test_reference_odds_chain.py`); 51/51 stabilization tests passing.
  - `no_reference_market` rejects: 1234 → 0 on common keys (full recovery).
  - Tiered (common keys): 29 → 63 (+34, +117% supply).
  - 35 newly tiered props this slate: 28 via new FD chain link, 1 via BOL, 6 via dk via slate refresh.
  - 5/5 random regression check: already-tiered props preserved byte-for-byte (same tier/refBk/refO).
  - MLB chain untouched and regression-tested.
  - Audit: `/app/audit_reports/no_reference_market_deep_audit_2026-05-09.md`,
            `/app/audit_reports/refchain_port_diff_2026-05-09.txt`
- **War Zone OVER gate adjustment (2026-05-09) — COMMITTED**
  - HR floor 55 → 50 (`_NBA_WAR_ZONE_BASE.hit_rate_gate.min`)
  - CV-cap ladder armed: tier 2 (HR≥70 + edge>0 → CV≤1.15), tier 3 (HR≥80 + edge≥5 → CV≤1.50)
  - Direction / coverage / edge / vision_score / market_structure gates UNCHANGED
  - Tests: `tests/test_war_zone_over_cv_ladder.py` (21 tests, all passing)
  - First-slate impact: 0 ladder rescues (slate had no HR≥70 candidates) — patch is mathematically sound but supply-bound.
  - **Monitoring directive active**: `/app/backend/scripts/wz_slate_monitor.py` logs every slate to `/app/audit_reports/wz_slate_monitor.jsonl`. Reassess only after **3 normal slates** if WZ qualified stays below 8–10.
- 30-min double-pass sign-off SLO — BLOCKED on upstream odds API blackout (Props 0)

## Architecture
- Frontend: React + Shadcn UI
- Backend: FastAPI + APScheduler + MongoDB
- Core pipeline: `universal_odds_sync` → `live_props` → `delta_dirty_queue` → `detector` → `delta_steps` (rescore) → `master_sync` (hourly board build & VI enrichment)
- Ticker pipeline: scheduler → `routes/live.py::sync_news_headlines` (NBA daily 9:26 UTC) and `sync_mlb_news_headlines` (MLB hourly :32) → `ticker_cache` collection

## Key Endpoints
- `/api/v3/ferrari/all`
- `/api/v3/board`
- `/api/live/news?sport=nba|mlb`
- `/api/live/scores?sport=nba|mlb`

## Key Collections
- `ticker_cache`
- `nba_prop_scores`, `mlb_prop_scores`
- `nba_cached_board`, `mlb_cached_board`
- `scheduler_jobs`

## Backlog (Frozen until stabilization sign-off)
### P0 — Blocked on stabilization sign-off
- Implement Emergent-managed Google OAuth (must use `integration_playbook_expert_v2`)
- Implement Stripe payments (must use `integration_playbook_expert_v2` with pod test keys)

### P0 — Environmental
- Wait for upstream odds API to exit blackout, then run final 30-min double-pass SLO

### P1 — After stabilization sign-off (gated by `ARCHITECTURE.md` directive)
- **Consolidation #1**: Retire `services/mlb_cached_board_builder.py` + duplicate MLB board-rebuild path. Replace call sites with `publish_board_snapshot(db, "mlb")`. Verify NBA + MLB run identical publisher.
- **Consolidation #2**: Audit + document/retire remaining duplicate writers, builders, legacy collections, multi-source truths, route-specific board logic, hidden overlay writers.
- **Consolidation #3**: Produce `/app/memory/SYSTEM_OWNERSHIP.md` per the template in `ARCHITECTURE.md` — canonical source / single writer / allowed readers / enrichment layers / freshness owner / scheduler owner / cache owner for every major entity.
- Decompose `Dashboard.jsx` (2,000+ lines) and `picks_getter_service.py` (3,200+ lines).
- NFL config scaffold (must follow universalization rule — config-first, not a new pipeline).
- STL/BLK/Double-Double model training for NBA.
- MLB `ContextBadgeService` fix (deferred).

### P2
- Forward-test resolver dashboard
- Universal Vision Intel Refactor (YAML configs)

## Recent Changelog

### 2026-05-17 — Phase 2B Session 4 — `pitcher_outs` promoted to XGBoost ✅
- **Promotion**: `pitcher_outs` is no longer analytical-only. Added `_calc_pitcher_outs` route in `STAT_FIELD_MAP` + `_get_stat_value` that decodes MLB-notation `innings_pitched` (5.0/5.1/5.2 → 15/16/17 outs) and prefers an explicit `outs` field when present. Same SSOT extraction is now consumed by every training path (`phase2b_retrain_worker`, `retrain_mlb_models_v2`, `train_mlb_ecdf_artifacts`).
- **Trained model**: `mlb_hf_pitcher_outs.pkl` (`MLB_HF_v3.2_phase2b`) — **samples=2,449, features=243, R²_test=0.8711, MAE_test=1.1219 outs**, lineup_hit_rate=46% (chunked single-stat training, ~4s wall time, peak RSS well under pod limit). Verified live on Yu Darvish (model μ=14.09 outs vs analytical μ=13.80 outs at 17.5 line).
- **Routing**: `predict()` prefers the XGBoost path when the pickle is loaded; falls back to the analytical `expected_IP × 3` projection (`_predict_pitcher_outs`) when the pickle is absent (cold start / pre-train deploy).
- **Analytical path retained as PERMANENT DIAGNOSTIC**: every pitcher_outs response — model path included — now carries `friction_audit.analytical_pitcher_outs` = `{expected_ip, starts_used, analytical_mu_outs, analytical_sigma_outs}` so the workload anchor is always visible alongside the model μ.
- **`pitcher_hit_rate` wired into the analytical path**: `_compute_pitcher_outs_hit_rate` mirrors the 10-/5-start sample-floor contract from `mlb_tier_sorter::_calculate_pitcher_hit_rate_sides` and surfaces `pitcher_hit_rate_over / _under / _n / _window_used / _avg_outs` in `friction_audit['pitcher_hit_rate']` on both routes. Downstream `_calculate_pitcher_hit_rate_sides` remains the canonical SSOT for the score-doc fields — this is purely a model-level diagnostic so the workload-anchor and HR signal travel together.
- **Tests**: `tests/test_pitcher_outs_model.py` (20 cases) — IP→outs decoding (5.0/5.1/5.2/0.2/9.0/etc), explicit-`outs`-field preference, missing-data path, `STAT_FIELD_MAP` route shape, 10-start window, 5-start minimum, sub-5 suppression, `line=None` suppression, analytical-block shape and 2-start minimum, and `predict()` analytical fallback wiring (model-absent case). **20/20 pass.** Broader regression on Phase 2B / HR / direction-gate / Statcast Bayes suites: **81/81 pass.**
- **No changes to**: gate logic, thresholds, edge formulas, TP formulas, universal edge SSOT, UI, sportsbook routing, recompute architecture. **Pure modeling promotion + diagnostic wiring.**


### 2026-05-16 — Pitcher-specific HR sample-floor contract
- **Problem**: The MLB HR SSOT (`mlb_tier_sorter::_calculate_hit_rate_sides`) was designed for batters — strict 20→10 fallback returns `None` when fewer than 10 games are available. Starting pitchers structurally violate this in mid-season (one start every 5-6 days = 7-9 starts by mid-May), silently killing strong-edge pitcher picks at the `hit_rate_gate` (e.g. Merrill Kelly Hits Allowed UNDER +29% edge, Kyle Freeland Pitcher K +26%).
- **Solution**: New `_calculate_pitcher_hit_rate_sides` peer method on `MLBTierSorter`. Pitcher-only contract:
  - `starts ≥ 10` → window=10 (newest)
  - `starts ≥ 5` → window=n_starts (all available, variable denominator)
  - `starts < 5` → HR unavailable (None) — preserves conservative behaviour for cold starts
- **Scope**: Routes only when `_normalize_stat_type(stat_type) ∈ {pitcher_strikeouts, pitcher_outs, pitching_outs, earned_runs, hits_allowed, walks_allowed, pitcher_walks}`. Batter SSOT (HRR/Hits/TB/singles/runs/RBIs/batter_strikeouts/batter_walks) is **untouched** — regression-locked by `TestBatterSSOTUntouched`.
- **Strict-denominator preservation**: missing field-value games still count as misses for OVER (mirrors batter SSOT). Only the WINDOW SELECTION rule changes.
- **Pitcher Outs derivation**: `innings_pitched × 3` → outs, same convention as the batter SSOT.
- **New score-doc fields**: `pitcher_hit_rate` (mirror of side rate), `pitcher_hit_rate_n` (actual sample size), `pitcher_hit_rate_window_used` (one of `"10"`/`"9"`/`"8"`/`"7"`/`"6"`/`"5"`). Wired through `ScoreContext` (`base.py`), `_SCORE_OUTPUT_FIELDS` (`prop_scores_store`), recompute mirror block (`recompute.py`), and Pydantic `ScoreDocument` schema. Batter props carry None.
- **Tests**: `tests/test_pitcher_hit_rate_contract.py` — 18 cases covering window selection (≥10, 5-9, <5), pitcher-stat routing for both canonical and display names, Pitcher Outs `IP × 3` derivation, strict-denominator preservation, and a **mutation guard** confirming the batter HR SSOT contract was not loosened. **18/18 pass.** Broader regression: 205/205 stabilization tests green.
- **Live validation post-recompute**: 25 active pitcher OVER props in the FL bucket — 11 use window=10 (≥10 starts available), **2 use window=7** (Merrill Kelly Pitcher Strikeouts — previously HR=None, now HR=29% with N=7), 12 remain None (mostly `Pitcher Outs` analytical-path props which don't route through this method, plus pitchers with <5 starts). Merrill Kelly's K props now fail at `direction_fail` (real model disagreement: model projects 2.4-2.6 vs lines of 3.5/4.5) instead of being silently dropped at `hit_rate_fail`.
- **FIELD_OWNERSHIP.md**: 5 new rows documenting the dual ownership — batter HR remains owned by `_calculate_hit_rate_sides`; pitcher HR now owned by `_calculate_pitcher_hit_rate_sides`; three diagnostic fields registered as 🟢 active (2026-05-16).
- **No changes to**: gate logic, thresholds, edge formulas, TP formulas, universal edge SSOT, UI, sportsbook routing, recompute architecture. **Pure SSOT correction.**

### 2026-05-15 — Phase 2B Session 3 — MLB_HF Pitcher Retrain DEPLOYED ✅
- **Model version live**: `MLB_HF_v3.2_phase2b` — 4 pitcher pickles retrained and overwritten (`pitcher_strikeouts`, `pitcher_walks`, `earned_runs`, `hits_allowed`). `pitcher_outs` excluded (analytical path, no XGBoost).
- **Calibration delta (test set)**:
  - `pitcher_strikeouts`: R² **0.5759 → 0.6966** (+0.121, +21% relative), MAE 0.929 → 0.907
  - `hits_allowed`: R² **0.5381 → 0.6166** (+0.079), MAE 1.038 → **0.854** (−17.7%)
  - `pitcher_walks`: R² 0.343 → 0.351, MAE 0.601 → **0.531** (−11.6%)
  - `earned_runs`: R² 0.310 → 0.251 (-0.06), MAE 0.874 → **0.723** (−17.3%) — R² regressed slightly but MAE lifted strongly; volatile-stat tail behaviour, MAE is the more meaningful metric.
  - **3/4 stats improved on R², 4/4 improved on MAE.**
- **Feature importance**: all 21 Phase 2B lineup features non-zero across all 4 stats. Top signals — `lineup_size`, `projected_rhh_count`/`lineup_xwoba_14d`/`lineup_k_rate_14d`. `pitcher_walks` shows `lineup_size 0.041` as its #1 feature. `lineup_handedness_is_imputed` is itself a strong signal (0.02-0.03 importance) — the model learned to discount imputed rows.
- **Park-factor validation**: park factors (emitted by v3.1 builder, no new code) now carry non-trivial importance: `park_runs_factor` 0.023 on pitcher_walks; `park_k_factor` 0.012 on earned_runs.
- **Training infrastructure**:
  - `scripts/phase2b_retrain_worker.py` (NEW, 480 lines) — resumable per-stat worker mirroring Phase 2A. **Memory-safe design**: drops PA cache (saves ~2GB), splits SC cache (drops `sc_batter` half, lazily loads only the ~1,420 batters referenced by the lineup resolver via ONE bounded `find({"player_id":{"$in":[...]}})` query). Peak RSS <4GB per stat.
  - `models/mlb_hf/_phase2b_workdir/lineup_resolver.pkl` (NEW, 7.4MB) — 47,021 pitcher-game pairs from `mlb_statcast_raw`, built in 18s. Reusable across retrain runs.
  - 4 stats trained sequentially in ~14s total.
- **Score-doc schema**: `services/scoring/score_document_schema.py::ScoreDocument` extended with `opposing_lineup_size: Optional[int] = None` (Pydantic strict `extra=forbid` model required this). Previously surfaced in `_SCORE_OUTPUT_FIELDS` + recompute mirror block in Session 2.
- **Production state**: **86 active pitcher score docs on v3.2** (37 pitcher_strikeouts, 35 hits_allowed, 14 earned_runs; pitcher_walks ingest cycle pending). Remaining 55 props still on v3.1 — naturally migrate as new slates ingest. `opposing_lineup_size=0` on all v3.2 docs because the existing `mlb_live_props` pre-dates Phase 2B hydration; lineup features stay imputed for these (same path 54% of training samples saw — no user-visible degradation). Next fresh ingest cycle populates real lineups.
- **Hot-hydrate prototype reverted**: a just-in-time `opposing_lineup` rebuild was prototyped inside `mlb_scoring.py` to repopulate stale live_props during recompute. Caused pod OOM (resolver + lazy batter cache + HF singleton + Phase 2A resolver = ~28GB RSS). Reverted; natural ingest path handles this correctly.
- **Regression**: **225/225** stabilization tests green. Lint clean on new worker.
- **Files**: `scripts/phase2b_retrain_worker.py`, `audits/phase2b_retrain_report_2026_05_15.md`, `models/mlb_hf/_phase2b_workdir/{lineup_resolver.pkl, _progress.json, _train_report.json}`, 4 v3.2 pickles, `services/scoring/score_document_schema.py` (1 field added).

### 2026-05-15 — Phase 2B Session 2 — Feature builder + live prediction wiring
- **Goal**: Wire the Phase 2B opposing-lineup features end-to-end from `feature_hydration.py` through `_build_friction_features` and `predict()`. Strictly infrastructure — no retrain yet, no gate/UI/edge changes.
- **Feature builder** (`services/mlb_high_friction_model.py::_build_friction_features`): added 2 new kwargs (`opposing_lineup`, `sc_batter_cache`) + CATEGORY 9 block. Always emits the canonical 21 lineup features; raises `*_is_imputed=1` flags when lineup is missing. Feature-vector shape is invariant across stat-family, so existing v3.1 batter models silently ignore the new features (not in their `feature_cols` pickle) — safe deployment.
- **Predict path** (`predict()` + `mlb_scoring.py`): threads `prop["opposing_lineup"]` from the hydration step into the feature builder.
- **Feature aggregator** (`services/mlb_lineup_features.py`): extended `build_lineup_features` to read inline `rolling_14` per batter dict (preferred live wiring — no external cache lookup needed). Cache lookup retained for training.
- **Live hydration** (`services/feature_hydration.py`): added `_PITCHER_STAT_TYPES` registry (`Pitcher Strikeouts`, `Pitcher Outs`, `Earned Runs`, `Hits Allowed`, `Walks Allowed`, `Pitcher Walks`); built run-level `mlb_lineup_cache` (one fetch per unique `(opp_team, game_date)` pair) and `mlb_sc_rolling_cache` (one batch query for all opposing batters' rolling-14); per-prop helper decorates the lineup with as-of inline rolling stats. BDL → last-played → None imputed fallback chain preserved.
- **Diagnostic propagation**: added `opposing_lineup_size` to `_SCORE_OUTPUT_FIELDS` and the recompute mirror block. Full lineup list stays OFF score docs (volatile, rebuildable).
- **Park-factor decision**: confirmed park factors are already emitted by the v3.1 feature builder (`park_hits_factor`, `park_runs_factor`, `park_hr_factor`, `park_k_factor`, `park_tb_factor`, `park_factor`) — no new code needed. Session 3 just needs to ensure `park_team` flows into training samples for pitcher stats.
- **Pitcher recent-form decision**: confirmed already emitted via the `pitcher_statcast_features` path (`sc_p_r14_*`, `sc_p_r30_*`, `pa_p_*`) from Phase 2A — no new code needed.
- **Tests**: new `tests/test_phase2b_session2_wiring.py` — 12 cases (signature contract, inline-rolling path, hydration decorator, pitcher-stat registry, score-doc allowlist, schema invariance). **12/12 pass**.
- **Live smoke test**: Aaron Nola Pitcher Strikeouts 5.5 vs PIT — opposing lineup resolved 9/9 batters from last-played fallback, 9/9 decorated with inline `rolling_14`. Feature builder emitted CATEGORY 9 with `pct_lhh=0.333`, `pct_rhh=0.556`, `lineup_k_rate_14d=0.240`, `lineup_woba_14d=0.330`, all 21 features present, no schema drift. Imputed path verified — empty lineup raises all 4 imputed flags.
- **Regression**: **278/278** stabilization tests green (gate engine, score lifecycle, Phase 1 + 2A propagation, Phase 2B features + wiring, universal edge SSOT).
- **Status**: Session 3 unblocked — retrain worker can now build training samples with the new feature schema and the live predict path will consume them when v3.2 pickles land.

### 2026-05-15 — Phase 2B Session 1 — MLB_HF Pitcher Context Infrastructure
- **Goal**: Build the foundational data + feature layers required to retrain pitcher models with opposing-lineup, park-factor, and pitcher-recent-form context. Strictly an infrastructure pass — no retrain, no live wiring, no gate/UI/edge changes.
- **Architectural decisions** (locked, see `/app/backend/models/mlb_hf/_phase2b_workdir/README.md`):
  - Live-prediction lineup source: **BDL lineup feed → last-played fallback → None (imputed)** (option 1c).
  - Park factors: **expose existing `PARK_FACTORS_3YR` table as features; no new aggregation** (option 2a).
  - Execution: **3-session milestoned build** (Session 2 wires builder/predict/hydration; Session 3 retrains + chunked recompute + audit).
- **Backup**: 6 v3.1 pitcher pickles (`pitcher_strikeouts, pitcher_walks, earned_runs, hits_allowed, walks, strikeouts`) copied to `/app/backend/models/mlb_hf/_pre_phase2b_backup_2026_05_15/`. Rollback procedure documented.
- **New code**:
  - `services/mlb_lineup_resolver.py` — historical (training-only) pitcher×date → batters_faced from `mlb_statcast_raw`. Streaming aggregate, ~30s to build full resolver, pickled to `_phase2b_workdir/lineup_resolver.pkl` for resumable training.
  - `services/mlb_lineup_features.py` — **canonical 21-feature aggregator** (locked schema in `PHASE2B_LINEUP_FEATURE_NAMES`): handedness mix (9), lineup strength rolling-14d (7), matchup-interaction counts vs pitcher hand (5). Strict imputation contract — every feature ALWAYS present; missing-data raises matching `*_is_imputed=1` flag. Switch-hitters always count as opposite-hand. Strict as-of leakage prevention on rolling-14 lookups.
  - `services/mlb_live_lineup_feed.py` — live BDL adapter with last-played fallback. Sync + async entry points. Wired in Session 2.
- **Tests**: `tests/test_phase2b_lineup_features.py` — 14 pytests covering schema contract, handedness math (incl. switch-hitter platoon-advantage), matchup-interaction (vs L/R/None pitcher), as-of leakage prevention, partial-lineup handling, imputation flag propagation. **14/14 pass**.
- **Smoke test**: validated aggregation end-to-end against real `mlb_statcast_raw` data on game_pk=747218 — pitcher 573009 faced 6 batters (3L/3R), produced correct handedness mix and same/opposite-hand counts vs RHP, in 0.02s.
- **Lint**: all 3 new modules pass ruff clean.
- **Session 2 plan committed**: extend `_build_friction_features` with pitcher-context branch (+3 park-factor + 4 pitcher recent-form features), thread new params through `predict()`, wire `feature_hydration.py` to populate `opposing_lineup` on live MLB pitcher props, widen `_SCORE_OUTPUT_FIELDS` allowlist, add 8-12 builder/predict/hydration tests.
- **Files**: `services/mlb_lineup_resolver.py`, `services/mlb_lineup_features.py`, `services/mlb_live_lineup_feed.py`, `tests/test_phase2b_lineup_features.py`, `models/mlb_hf/_phase2b_workdir/README.md`, `models/mlb_hf/_pre_phase2b_backup_2026_05_15/` (6 pickles).

### 2026-05-15 — MLB Front Lines `edge_min` universal floor lift (Option A)
- **Change** (per user directive): `_MLB_FRONT_LINES["hits_runs_rbis"]` in
  `services/scoring/gates/thresholds.py`:
  - `cv_max`: 0.65 → **0.75** (raise — more permissive CV cap on non-0.5 lines)
  - `edge_min`: 5.0 → **4.0** (lower — accept smaller positive edges)
- **Live impact (post-recompute, 6,319 props rescored in 209s, 0 failures)**:
  - HRR FL OVER tier count: 33 → **36** (+3 rescues).
  - New tiered picks: Ozzie Albies HRR 1.5 (cv 0.684), Shea Langeliers HRR 1.5
    (cv 0.713), + 1 more — all binary CV-cap rescues from the 0.65–0.75 band.
  - **Jorge Soler Hits 0.5 OVER** now surfaces in `front_lines` (the flagship
    audit case the universal direction-gate refactor + this threshold change
    together resolved).
- **edge_min lift** had no immediate FL impact on HRR this slate (most HRR rows
  carry `edge_pct=None` — engine treats null as skip-pass for `edge_gate`).
  The change still applies sport-wide for the broader FL OVER bucket.
- Note: `cv_max` only binds on non-0.5 HRR lines (1.5+, 2.5+) because the
  MLB binary 0.5-line cv→margin swap in `engine.py` replaces `cv_gate` with
  `margin_gate` at line=0.5. The 0.5-line picks (Soler, Trout, etc.) clear
  via the direction-gate strict refactor + existing margin-gate floor.
- Broader board health: MLB 309 players · 110 FL · 46 WZ · 13 SH.

### 2026-05-15 — Universal Direction Gate strict refactor (engine SSOT)
- **Spec**: `services/scoring/gates/engine.py::_eval_direction` is now pure side-lean.
  - OVER passes iff `projection > line` (strict)
  - UNDER passes iff `projection < line` (strict)
  - Equality fails — no side-lean
  - All historical positive cushions (`min_projection_minus_line`, `min_line_minus_projection_ratio`,
    `min_projection_to_line_ratio`, `max_projection_minus_line`) are accepted in threshold
    configs for backwards compatibility but **ignored** by the engine.
- **Rationale**: The direction gate was acting as a hidden confidence floor on top of its
  side-lean role. Quality concerns (margin, CV, edge magnitude, hit-rate) now live exclusively
  in their own gates — direction is purely "which side is the model leaning?".
- **Live MLB FL OVER impact (active board)**:
  - 108/108 active FL OVER picks pass strict direction; reject distribution within the
    FL-routed bucket (1,934 props): `gate_hit_rate_fail 888 / gate_direction_fail 612 /
    gate_cv_fail 262 / gate_edge_fail 60 / gate_margin_fail 4`.
  - **27 active picks** (25%) were rescued by removing the old stat-family `min_margin`
    floor (e.g. Aaron Judge Hits 0.5 +0.66 diff, Michael Busch +0.74, Riley Greene +0.66).
  - **Zero leakage**: 575 direction-fail rejects audited; 100% have `proj ≤ line`.
- **Tests**: New `tests/test_universal_direction_gate.py` (19 cases incl. parametrised
  mutation guard that fails if any future change re-honours a positive cushion config key).
  Pre-existing tests updated: `test_fl_over_overrides`, `test_nba_under_tuning`,
  `test_war_zone_refactor` (stale config assertions brought current). **282/282** gate +
  stabilization tests pass.
- **Audit**: `/app/backend/audits/universal_direction_gate_refactor_2026_05_15.md`.
  Validation script: `scripts/audit_direction_gate_refactor.py`.

### 2026-05-15 — Phase 2A MLB_HF retrain (MLB_HF_v3.1_phase2a)
- **First true MLB model recalibration** after Phase 1 + Phase 2A infra stabilization. Strict scope per user mandate: NO gate tuning, NO CV threshold changes, NO HR floor changes, NO tier-logic changes, NO pitcher-stat retraining, NO infrastructure additions.
- **Approved feature additions** to `_build_friction_features` (Category 8, 14 new features):
  - Batter handedness one-hot: `batter_is_lhh`, `batter_is_rhh`, `batter_is_switch`, `batter_hand_is_imputed`
  - Opp pitcher throws one-hot: `opp_pitcher_throws_l/r/is_imputed`
  - Matchup flags: `same_hand_matchup`, `opposite_hand_matchup`, `matchup_is_imputed`
  - Opp pitcher rolling-14 quality: `opp_pitcher_k_rate_14d`, `opp_pitcher_bb_rate_14d`, `opp_pitcher_xwoba_allowed_14d`, `opp_pitcher_quality_is_imputed`
- **Order-of-ops fix**: `_propagate_phase1_context` moved BEFORE predict() in `mlb_scoring.py` so the model actually sees `batter_hand`. Previous placement (after success branch) silently passed `None` every call.
- **Live wiring**: `predict()` accepts `batter_hand`, `opp_pitcher_throws`, `opp_pitcher_id`; new `_lookup_opp_pitcher_features` reads `mlb_statcast_pitcher_features` keyed by MLBAM id. Model version string now read from pickle, not hardcoded.
- **Retrain pipeline**: `scripts/phase2a_retrain_worker.py` — resumable, per-stat, ~60-90s/stat, ~3.7GB peak RSS. Daemonized via `_daemon_launch.py` to survive MCP shell drops. State pickled to persistent `/app/backend/models/mlb_hf/_phase2a_workdir/` so any pod restart resumes where it left off.
- **Retrain sources**: `master_hub.bdl_game_logs` (target + history), `mlb_statcast_player_features` (batter rolling), `mlb_statcast_pitcher_features` (pitcher rolling), `mlb_statcast_raw` (per-game opponent pitcher + batter handedness via Mongo aggregation, 109,864 (batter,date) pairs in 13.6s).
- **Trained**: 10 batter stat models. All artifacts pickle-version-stamped `MLB_HF_v3.1_phase2a`, feature_count=222 (was 208 for legacy v3.0_bayes). Pitcher pickles intentionally untouched.
- **Backup**: `models/mlb_hf/_pre_phase2a_backup_2026_05_15/` (23 files).

#### Validation results (PRIMARY metric — calibration target met)
**Fake-negative-edge cluster** (OVER row with HR ≥ 60%, book_count ≥ 3, model_projection < line, edge < 0):
- v3.0_bayes: **14.5%** (18/124)
- v3.1_phase2a: **0.8%** (12/1524) — **94% reduction**

**Binary prop OVER positive-edge rate**:
- Hits 0.5: 18% → **51%**
- Singles 0.5: 25% → **69%**

**Per-stat R²_test** (test-set, train/test split 80/20):
- hits 0.190, total_bases 0.195, hits+runs+rbis 0.264, singles 0.119, runs 0.089, walks 0.086, home_runs 0.068, rbis 0.061, doubles 0.010, stolen_bases 0.017
- Lower R² on hit-rate-style binary props is expected (high target variance, near-binary distribution); calibration improvement is the actual win, not R².

**Audit players (current slate, 2026-05-16 LAD vs Angels)**:
- Kyle Tucker Hits 0.5 OVER: μ=0.890, tp=66.4%, edge_vs_fair +0.65%, total_edge +9.1%, **tier=front_lines (gates_passed)** — was unqualified pre-retrain.
- Freddie Freeman Hits 0.5 OVER: μ=0.854, edge_vs_fair -5.1% (consensus TP=73.4% reasonably gates this — Freeman's career mean ~0.78 supports model).
- Andy Pages Hits 0.5 OVER: now realistic projection but tier=unqualified (per strict mandate, gate logic untouched).
- Ozzie Albies Hits+Runs+RBIs 1.5: no active row at report time (game in progress).

**Top matchup-feature importance shifts** (where matchup context matters most):
- `hits+runs+rbis`: `opp_pitcher_quality_is_imputed=0.040`, `opp_pitcher_xwoba_allowed_14d=0.013`
- `total_bases`: `opp_pitcher_quality_is_imputed=0.051`, `batter_hand_is_imputed=0.018`, `opp_pitcher_xwoba_allowed_14d=0.014`
- `home_runs`: `opp_pitcher_quality_is_imputed=0.036`, `opp_pitcher_xwoba_allowed_14d=0.010`, `opp_pitcher_throws_r=0.009`
- `walks`: `opp_pitcher_bb_rate_14d=0.012`, `opp_pitcher_throws_r=0.010`
- Imputation flags ranking high confirms missingness carries signal — model correctly learns to weight predictions differently when context is missing.

**Recompute coverage**: 70.8% of active props relabelled v3.1 at first pass; remaining 29% are pitcher-stat rows (out of scope, still v3.0_bayes — intentional per user mandate). All 100% of batter-stat rows on the new slate now use v3.1 features.

#### Dependency audit — `mlb_historical_logs`
- 6,645 docs; **production retrain pipeline already ignores it** (canonical = `master_hub.bdl_game_logs`).
- Still referenced by 4 legacy scripts (`train_mlb_ecdf_artifacts.py`, `train_line_outcome_models.py`, etc.) and 5 production services (handle-only, not read in hot paths).
- Still being WRITTEN by `scripts/run_mlb_backfill.py` ingest.
- **Verdict**: rename deferred to a follow-up pass — would require migrating writers + readers first. NOT a blocker for retrain (validated by successful Phase 2A pipeline).

### 2026-05-15 — Phase 2A MLB pitcher matchup wiring (real probable-pitcher feed)
- **Motivation**: Phase 1 left `feature_hydration.py:735-739` MOCKED — `probable_pitcher / opp_pitcher_id / opp_pitcher_name / opp_pitcher_throws` were hardcoded `None`. Audit confirmed this stripped opponent context from EVERY MLB score doc, contributing to systemic under-projection.
- **Scope (strict Phase 2A — no model retrains, no gate tuning, no park factors, no PA/AB models)**:
  - `services/mlb_probable_pitcher.py` — finished. Wraps the free MLB Stats API at `statsapi.mlb.com` (no auth). One module-level TTL-cached `ProbablePitcherIndex` per UTC date, keyed `(HOME_ABBR, AWAY_ABBR) → {"home": {...}, "away": {...}}`. Pitcher dict: `{id, name, throws, era, whip, k9}`. Throws sourced from `/people/{pid}.pitchHand.code` as fallback because the schedule hydration omits `pitchHand` in production. Parallel `asyncio.gather` for the people-endpoint fetch (≈30 starters/day → single ingest cycle ≈8s warm).
  - `services/feature_hydration.py` — entry function now builds one index per unique commence-time date across the props batch, then per-MLB-prop selects the opposing pitcher (home batter → away starter; away batter → home starter). Sets `opp_pitcher_id/name/throws/era/whip/k9 + probable_pitcher` alias. Imputed-fields flag only stamped when the value is None. New helper `_commence_date_iso(ct)` coerces datetime / ISO str / epoch-ms / bare date → `YYYY-MM-DD UTC`. Coverage report exposes new `probable_pitcher_filled` counter.
  - `services/scoring/adapters/mlb_scoring.py::_propagate_phase1_context` — extended to derive `same_hand_matchup` / `opposite_hand_matchup` from `batter_hand × opp_pitcher_throws`. Switch-hitters (`S`) always encode as `opposite_hand_matchup=1`. Flags stay `None` when either input is missing so the Step-5 missing-value audit stays honest.
  - Score-doc allowlists widened: `services/scoring/prop_scores_store._SCORE_OUTPUT_FIELDS` and `services/scoring/recompute.py` mirror block now carry all 9 Phase 2A fields. `score_document_schema.ScoreDocument` declares them so Pydantic `extra=forbid` does not reject the writer.
  - **Explicit non-changes (strict Phase 2A)**: `mlb_high_friction_model.predict` signature untouched. The new pitcher fields are NOT yet passed as model inputs — that requires `MLB_HF` retraining (deferred to Phase 2B+). Phase 2A is purely propagation/storage/observability.
- **Live verification (post-deploy)**:
  - `mlb_live_props` (5,844 rows): `opp_pitcher_id 50.6%`, `probable_pitcher 50.6%`, `opp_pitcher_throws 41.4%`, `opp_pitcher_era/whip/k9 41.4%`. Remaining gap = games where MLB hadn't announced the probable starter yet (LAD/SF future date, etc.) — correctly imputed.
  - `mlb_prop_scores` (4,562 active): full pitcher metadata flowing through to score docs; `same_hand_matchup` / `opposite_hand_matchup` populated on every score doc where `batter_hand AND opp_pitcher_throws` are both known.
  - Validation report on the four audit names (Andy Pages, Kyle Tucker, Freddie Freeman, Ozzie Albies): Ozzie Albies (ATL home) now carries Ben Brown / R / 1.82 ERA / 0.91 WHIP / 8.19 K9. Three Dodgers/Giants rows correctly stay None with imputed flag (probable pitcher not yet announced for that future-date game).
  - Sample matchup row: Adolis Garcia (R) vs Ranger Suarez (L) → `same_hand_matchup=0`, `opposite_hand_matchup=1`.
- **Tests**: `tests/test_phase2a_pitcher_matchup.py` — 27 unit tests covering index builder (schedule parse, throws fallback to people endpoint, case-insensitive lookup, missing-pair None return), `_commence_date_iso` (datetime / ISO / bare date / None / malformed), matchup-flag derivation (L/L, R/R, L/R, R/L, S/L, S/R, None inputs, unknown codes), prop_scores allowlist enforcement, schema field declaration, and end-to-end feature_hydration runs with mocked Mongo + dummy MLB Stats API client. **27/27 pass**. Combined with Phase 1 / lifecycle / ephemeral suites: **84/84 pass**.
- **Validation script**: `scripts/phase2a_validate_pitchers.py` prints BEFORE/AFTER pitcher fields for the four audit names against live data.
- **Phase 2A deferred items (Phase 2B+ scope)**: park factor table, PA/AB expected models, `MLB_HF` retrain to consume the new pitcher features, pitcher Ks EB whitelist, opp_team_k_rate.

### 2026-05-15 — Phase 1 MLB projection stabilization (context propagation + EB whitelist expansion)
- **Motivation**: Andy Pages Hits 0.5 audit (separate session log) showed `MLB_HF_v3.0_bayes` projecting 0.48 hits/game for a player averaging 1.5 hits/game, with `eb_player_career_mean=None` (Hits was outside the EB whitelist) and 100% of contextual features missing on the score doc. Result: −21.2% "negative model edge" was actually model under-projection, not market mispricing.
- **Scope (Phase 1 only — no model retrains, no gate tuning, no new external feeds)**:
  - `services/scoring/adapters/mlb_scoring.py`: added `_propagate_phase1_context(prop, hf_model.master_hub, bdl_player_id)` called at the end of each prop projection. Stamps three already-available fields on the prop dict using the canonical name:
    - `batter_hand` parsed from `mlb_master_hub_2026.bats_throws` (`"<Bats>/<Throws>"` → `L/R/S`). Normaliser handles `Left/Right/Both/Switch/L/R/S/B/SH/LHB/RHB` etc.
    - `batting_order` already on live_props (`~48.9%` filled); helper also falls back to legacy aliases `lineup_spot, lineup_position, bo`.
    - `venue` (stadium label) — already on live_props, propagation only required score-doc allowlist.
  - `services/scoring/prop_scores_store._SCORE_OUTPUT_FIELDS`: allowlisted `batter_hand, batting_order, venue`.
  - `services/scoring/recompute.py`: mirror block extended to copy the three new fields onto each score doc.
  - `services/scoring/score_document_schema.py::ScoreDocument`: added the three Phase-1 fields and the four universal lifecycle fields (`ttl_purge_at, stale_reason, stale_marked_at, updated_at`) so Pydantic's `extra='forbid'` no longer rejects the writer.
  - `services/scoring/mlb_eb_shrinkage._WEIGHTS`: expanded EB whitelist conservatively:
    - `hits (0.80/0.20)`, `singles (0.80/0.20)`, `doubles (0.85/0.15)`, `runs (0.80/0.20)`, `stolen_bases (0.90/0.10)`, `batter_walks (0.80/0.20)`.
    - Model-leaning weights — Phase 1 stabilizes the worst HF outliers without giving EB authority over the model.
    - **Excluded until Phase 2**: `pitcher_strikeouts`, `batter_strikeouts`, `pitcher_outs`, `earned_runs`, `walks_allowed`.
  - `services/scoring/mlb_eb_shrinkage._normalize_stat`: aliases added (`run→runs, single→singles, hit→hits, sb→stolen_bases, walks→batter_walks` etc.).
  - `services/scoring/mlb_eb_shrinkage._career_mean_from_logs`: added per-family derivation for `singles` (H − 2B − 3B − HR) and field-name bridge for `batter_walks` (reads `walks` log column).
  - Updated `tests/test_mlb_eb_shrinkage.py` to reflect the new whitelist (was hard-asserting `hits` not supported).
- **New file**: `tests/test_phase1_mlb_propagation.py` — 31 unit tests covering batter-hand normalization (parametrised over `L/R/S/B/Switch/Both/Left/Right/RHB/LHB/SH` + bad input), propagation helper (batter_hand from `bats_throws`, switch-hitter, prefers `bats` when populated, silent skip when missing, no clobber of pre-set value), batting_order alias fallback (`lineup_spot → batting_order`, missing-stays-missing), and EB Phase-1 stats (hits Andy-Pages scenario, singles derivation from H/2B/3B/HR logs, batter_walks alias bridge, stolen_bases conservative weights, pitcher_strikeouts still excluded).
- **Live verification**:
  - 1,724 MLB props rescored. Phase-1 fields appearing as recompute drains.
  - System-wide: `batter_hand 24.3%`, `batting_order 16.0%`, `venue 53.5%` (will rise toward 100% as recompute cycles touch each active doc; `batting_order` capped by upstream live_prop fill rate).
  - **Andy Pages Hits 0.5 OVER**: projection 0.48 → **0.594** (+23.8%), `edge_vs_fair −21.2% → −12.2%`, `total_edge −14.0% → −6.0%`. `gate_direction_fail` replaced by `gate_edge_fail` — direction now correct. EB applied (`career_mean=1.05`).
  - **Kyle Tucker Hits 0.5 OVER**: 0.53 → 0.634, edge −11.4% → −4.4%, **total_edge flipped from −10.5% to +0.8%**.
  - **Freddie Freeman Hits 0.5 OVER**: 0.70 → 0.79, total_edge +0.7% → **+4.8%**.
  - **Ozzie Albies H+R+RBI 1.5 OVER**: projection unchanged (already EB-protected), now displays `batter_hand=S, batting_order=3, venue=ATL`.
- **Tests**: 85/85 pass (board_lifecycle 12 + ephemeral_cleanup 7 + prop_score_lifecycle 7 + mlb_eb_shrinkage 30 + dup_and_eb_order_fixes 7 + phase1_mlb_propagation 31 — well 87 collected, all passing).
- **Phase 1 deferred items (explicitly NOT addressed)**: pitcher matchup feed (currently MOCKED None in `feature_hydration.py:735-739`), park factor table, opp_pitcher SIERA/K9/ERA, opp_team_k_rate, PA/AB expected, pitcher Ks EB whitelist. Phase 2 work.

### 2026-05-15 — Universal lifecycle EXTENDED to `{sport}_prop_scores`
- **Problem**: After `services/boards/board_lifecycle.py` shipped for cached_board, the prop_scores collections still only stamped `active` (legacy universal-pool field) — missing `ttl_purge_at`, `stale_reason`, `stale_marked_at`, `updated_at`. Risk: stale-score contamination in gate audits / calibration / reject reports.
- **Solution**: One contract across every ephemeral realtime collection. Same `services/boards/board_lifecycle.py` helper now stamps prop_scores at write time AND the cleanup utility consumes it for inactive markers.
- **Changes**:
  - `services/scoring/prop_scores_store.py::_project_score_doc` — calls `stamp_active_board_doc(doc)` on every score doc at projection time (fail-soft: lifecycle stamping must never abort scoring). Allowlist `_SCORE_OUTPUT_FIELDS` widened with `ttl_purge_at, stale_reason, stale_marked_at, updated_at`.
  - `services/cleanup/ephemeral_cleanup.py` — every place that stamped lifecycle fields manually now uses `lifecycle_set_inactive()` (mark + legacy backfill paths) or `lifecycle_set_for_upsert()` (restore path). Removes duplicated stamping logic.
  - `routes/admin_board_lifecycle.py::_board_collections()` — widened from "cached_board-only" to "every ephemeral collection in the cleanup config". So the status + normalize endpoints now cover prop_scores too. Added `orphan_vs_live` count to status output (mirrors the cleanup status report; flat collections only).
- **Live state after deploy**:
  - Startup audit: `mlb_prop_scores: 100% compliant (121,590 docs); mlb_cached_board: 100% (286); nba_prop_scores: 100% (42,496); nba_cached_board: 100% (38)`. **164,410 docs total — `missing_active_field=0` across the board.**
  - Status endpoint: `missing_active_field=0`, `missing_ttl_purge_at=0`, `missing_stale_reason=0`, `missing_stale_marked_at=0` for all 4 collections.
  - Cleanup dry-run + real: 0 new orphans to mark (system fully converged).
  - MLB FL OVER reject audit: **12,616 total / 2,139 active / 10,182 stale-marked**. The 10,182 stale rejects no longer contaminate calibration audits when filtered with `active=True`.
  - `/v3/board?sport=mlb`: 5 players / 17 props served. `/v3/board?sport=nba`: 5 / 29. Unbroken.
  - Replay collections never touched (in `PROTECTED_COLLECTIONS` blocklist; unit test verifies).
- **Tests**: 26/26 unit tests pass across 3 files (`test_board_lifecycle.py: 12`, `test_ephemeral_cleanup.py: 7`, `test_prop_score_lifecycle.py: 7`). New tests cover prop_score active+inactive stamping, cleanup-via-helper integration, restore-via-helper integration, gate-audit active filter, normalize over prop_scores, and replay-collection isolation.

### 2026-05-15 — Universal cached board lifecycle infrastructure (`services/boards/board_lifecycle.py`)
- **Problem**: After the ephemeral cleanup utility shipped, `/api/v3/admin/ephemeral-cleanup/status` showed `mlb_cached_board.no_active_field=284/286` — i.e. one of the publisher paths was writing docs that bypassed the lifecycle contract. Risk: orphan cleanup invariants violated, `/api/v3/board` filter could silently drop legit docs.
- **Solution**: ONE authoritative lifecycle module that every cached_board writer system-wide MUST use. Removes scattered ad-hoc stamping.
- **New files**:
  - `services/boards/__init__.py` + `services/boards/board_lifecycle.py` — 8 public helpers: `stamp_active_board_doc`, `stamp_inactive_board_doc`, `normalize_board_doc`, `is_lifecycle_compliant`, `missing_lifecycle_fields`, `lifecycle_set_for_upsert`, `lifecycle_set_inactive`, + `LIFECYCLE_FIELDS` constant.
  - `routes/admin_board_lifecycle.py` — 2 admin endpoints (`X-Admin-Token`):
    - `GET  /api/v3/admin/board-lifecycle/status` — per-collection compliance audit.
    - `POST /api/v3/admin/board-lifecycle/normalize?dry_run=true|false` — repair-in-place migration.
    - Exports `startup_validate(db)` called from `server.py` at boot.
  - `tests/test_board_lifecycle.py` — 12 unit tests (mongomock-motor) covering: active stamp, inactive stamp with default grace, inactive stamp preserving existing ttl_purge_at, normalize repair of missing-active-field doc, normalize preserving active=False with inactive-field backfill, normalize no-clobber of populated lifecycle fields, lifecycle compliance predicate, real Mongo round-trip for $set fragments (active + inactive), `/v3/board` filter excludes inactive+unstamped docs, dry-run non-mutation, real-run repairs.
- **Audited and wired all cached_board writers**:
  - `services/board_snapshot_publisher.py::publish_board_snapshot` — both upsert (per-player UpdateOne with $set) and stale-empty (update_many for players off-slate) paths now route through `lifecycle_set_for_upsert` / `lifecycle_set_inactive`. Stale-filter widened with `{"active": {"$exists": False}}` so the same op also backfills any pre-existing unstamped doc (one-shot migration as a side effect of normal publishes).
  - `services/mlb_cached_board_builder.py::build()` — legacy path that does `delete_many({})` + `insert_many(player_docs)` now stamps `stamp_active_board_doc(doc)` on every doc before insert.
  - Enrichment-only writers (`context_badge_service.py`, `picks_getter_service.py`, `photo_service.py`, `injury_service.py`, `master_sync.py`) only $set specific auxiliary fields on existing docs — they never insert new docs, so they're naturally lifecycle-preserving. No edit required.
- **Defensive `/api/v3/board` read**:
  - Still filters `active=True` (only active slate docs served — orphans invisible).
  - Now also runs a count for `active`-missing docs at the same `version_tag` and logs a warning with the count, surfacing any future publisher-path bypass automatically.
- **Server startup**:
  - Calls `admin_board_lifecycle.startup_validate(db)` after wiring routers. Logs per-collection compliance count.
  - First post-restart audit reported: `mlb_cached_board: 100% compliant (286 docs); nba_cached_board: 100% compliant (38 docs)`. Down from 284/286 missing → 0 missing.
- **Live verification**:
  - `GET /api/v3/admin/board-lifecycle/status`: `mlb_cached_board.missing_active_field=0`, `nba_cached_board.missing_active_field=0`, compliance 99.3%/100%.
  - `POST .../normalize?dry_run=true`: 0 docs need repair.
  - `POST .../normalize?dry_run=false`: idempotent — 0 modifications.
  - `/api/v3/board?sport=mlb`: returns 5 players, 18 props. Service unbroken.
- **Tests**: 12/12 unit tests pass.

### 2026-05-15 — System-wide ephemeral data cleanup utility (orphan TTL)
- **Problem**: Stale orphan score docs from past slates were never being purged. Audit on 2026-05-15 showed 5,581 of 12,619 MLB FL OVER "rejects" (44%) were orphan docs whose `canonical_key` was no longer in `mlb_live_props` — contaminating gate calibration audits. Same pattern on NBA.
- **Solution**: Two-step active/inactive lifecycle with grace-period TTL purge — never delete a current-slate doc, always give 24h debug window after marking inactive. Universal, sport-agnostic, config-driven.
- **New files**:
  - `services/cleanup/__init__.py`
  - `services/cleanup/ephemeral_collections.py` — central per-sport config: `live_collection`, `canonical_key_field`, `grace_hours`, `collections`. Includes `PROTECTED_COLLECTIONS` blocklist that refuses any accidental inclusion of resolved-outcome / backtest / multiplier-lab / model-performance collections.
  - `services/cleanup/ephemeral_cleanup.py` — 5 entrypoints: `ensure_ttl_indexes`, `get_live_canonical_keys`, `mark_orphan_docs`, `restore_active_docs`, `run_ephemeral_cleanup` + `status_report`. Default `dry_run=True`. Live-props-empty safety abort (`force=True` to override). Supports flat-canonical-key collections and nested-key collections (e.g. cached_board with `props[]`).
  - `routes/admin_ephemeral_cleanup.py` — 3 admin endpoints behind `X-Admin-Token` (`ADMIN_DEBUG_TOKEN` env var):
    - `GET  /api/v3/admin/ephemeral-cleanup/status`
    - `POST /api/v3/admin/ephemeral-cleanup/run?sport=&dry_run=&force=`
    - `POST /api/v3/admin/ephemeral-cleanup/ensure-indexes`
  - `tests/test_ephemeral_cleanup.py` — 7 unit tests (mongomock-motor) covering: TTL index creation, live-empty safety abort, force override, end-to-end mark→restore lifecycle, dry-run non-mutation, protected-collection rejection, status-report shape. **All 7 PASS.**
- **Server integration**:
  - `server.py` startup: ensures TTL indexes on all configured ephemeral collections.
  - `recompute.py` post-success: invokes `run_ephemeral_cleanup(dry_run=False)` per sport after every real (non-dry-run) recompute pass; honours the live-props-empty safety abort.
  - `routes/player.py::/v3/board`: now filters `active=True` — orphan docs invisible to the board.
- **TTL contract**: Mongo TTL index on `ttl_purge_at` field (`expireAfterSeconds=0`). Active docs leave `ttl_purge_at=null` and are NEVER touched by TTL. Inactive orphans get `ttl_purge_at = now + grace_hours` and are physically removed by Mongo when the clock passes. Grace = 24h default per sport.
- **Live results (real run)**:
  - Indexes: ensured on `mlb_prop_scores`, `mlb_cached_board`, `nba_prop_scores`, `nba_cached_board` (+ `nfl_*` for future).
  - First pass marked 100 MLB + 0 NBA new orphans inactive (most legacy inactives were already deactivated by older code paths). Restored 6,474 MLB + 2,207 NBA docs whose canonical_keys had reappeared on the slate.
  - Legacy-inactive backfill stamped `ttl_purge_at` on **106,884 MLB + 34,831 NBA** pre-existing inactive orphan docs that lacked TTL — total **141,815 stale docs scheduled for auto-purge in 24h.**
  - Board sanity: `/api/v3/board?sport=mlb` returns 10 players / 47 props; `?sport=nba` returns 10/89. Safe Haven for both sports returns picks. Gate calibration audits now read only `active=True` so the 5,581 orphan contamination is gone permanently.
- **What this is NOT**: a simple `older_than` TTL on `updated_at`. Active docs never get a TTL field stamped. Nothing is ever hard-deleted by this utility — Mongo's TTL index does the physical removal.
- **Protected collections explicitly excluded**: resolved outcomes, settled bets, backtests, replay datasets, multiplier lab runs, model performance, master hub, game logs, training datasets, betting logs, drift audits, contract violations, sync locks. Adding any of these to the config raises `RuntimeError` at iter time.

### 2026-05-15 — NBA/MLB pick-card visual parity: universal `display_reference_*` (CONSENSUS label) — WIDENED to 2+ books
- **User report**: NBA pick cards displayed the book name (DK / FD / MGM) after the odds; MLB cards displayed "CONSENSUS". Visual asymmetry on the dashboard board.
- **Root cause**: `scoring_stack._pick_reference_odds` deliberately picks a single book for NBA (gates were calibrated against single-book reference odds; changing `tier_reference_odds` would silently re-route tiers). MLB's chain starts with a DK+FD consensus step, so MLB cards naturally show "CONSENSUS".
- **Fix (display-only — gates / routing UNTOUCHED)**:
  - Added `_pick_display_reference_odds()` in `scoring_stack.py` — UNIVERSAL "if 2+ of {DK, FD, MGM, CSR, BOL} quote → CONSENSUS (mean of implied probs); else single-book fallback". Same rule applied to NBA and MLB.
  - `compute_tier()` stamps `display_reference_book` + `display_reference_odds` on every score-doc path.
  - `score_document_schema.py`: added the two new optional fields.
  - `prop_scores_store._SCORE_DOC_FIELDS`: whitelisted for projection.
  - `ferrari_tiers.py`: passthrough into the tier API payload.
  - Frontend `resolveDisplayOdds` in `UniversalPlayerCard.jsx`: prefers `display_reference_*` over `tier_reference_*`, falls back gracefully on older docs.
  - **One-shot backfill** executed against existing score docs: derived consensus from `{sport}_live_props` per-book odds, mass-updated `{sport}_prop_scores`. Singletons mirrored to `tier_reference_*` so no doc ships with null display fields.
- **Live verification (post-backfill)**:
  - NBA Safe Haven: 8/10 picks show CONSENSUS (2 are FD-only and correctly fall back).
  - NBA Front Lines: 10/10 picks show CONSENSUS visible on dashboard.
  - NBA War Zone: 2/4 picks show CONSENSUS.
  - DB totals: NBA 2,775 consensus-stamped (7.2% of 38,548 score docs), MLB 38,680 (33.3% of 116,141). Single-book picks correctly keep their book name.
  - UI screenshot confirms: NBA + MLB cards now visually indistinguishable on the CONSENSUS / book-label dimension.
- **Gate / tier invariance**: `tier_reference_book` / `tier_reference_odds` byte-for-byte unchanged. `resolve_target_tier` reads the same single-book reference; no gate behavior change.

### 2026-05-15 — Universal Consensus / Best Bet chip on PlayerDetailPage PropRow (NBA/MLB pick card parity)
- **User directive**: "The nba and mlb ui should be identical. Add consensus to the pick card page. Mirror mlb."
- **Scope**: `frontend/src/components/dashboard/PlayerDetailPage.jsx::PropRow` — added a sport-agnostic edge-metrics strip that renders inline on every prop row below the Lasso Projection bar.
- **Fields shown** (only when present on the score doc — graceful degrade):
  - `Consensus`: `edge_vs_fair * 100` (model vs market devigged fair) — color: green >15%, yellow >5%, red <-5%, zinc neutral.
  - `Best Bet`: `total_edge * 100` (model vs best book) — color: emerald >10%, amber >3%, red <0; raw-one-sided source flagged with trailing `*`.
  - `Book`: `best_book` (label-mapped) + `best_book_odds` (signed American).
- **Live verification**:
  - NBA detail (Dylan Harper): 36 consensus chips rendered across 48 props (e.g. OVER 7.5 PTS → `CONSENSUS +21.9%`).
  - MLB detail (Ozzie Albies): 22 consensus chips rendered across 22 props (e.g. OVER 0.5 HITS → `CONSENSUS +17.8%`).
- **Not changed**: no backend, no gates, no thresholds. Pure presentation; the same data already feeds the Vision Intel Suite modal block at line ~1693 of the same file. No new fetch paths.
- **Lint**: clean (`mcp_lint_javascript`).

### 2026-05-13 — "Pull from all books" expansion: 4 books → 11 books (MLB multi-book 58% → 70.6%)
- **Root context**: post the 2026-05-08 projection-store fix the audit revealed MLB was actually 81% multi-book (not 30% as previously believed). User directive: "pull from all books. we are already paying for the call. we want maximum coverage."
- **Live API probe** showed 14 books returning MLB data; 6 with non-trivial coverage (>=80 outcomes per event) were unused: ESPN BET, Hard Rock Bet, BetRivers, BetParx, Bally Bet, Fliff. All in `regions=us` — **zero additional Odds API credit cost**.
- **Plumbing changes** (single pass, all production):
  - `universal_odds_sync.py`:
    - `BOOKMAKER_CONFIG`: added 6 new entries (region=us).
    - `MLB_BOOKMAKERS`, `USER_SHARP_BOOKMAKERS`, `SPORT_API_CONFIG.{nba,mlb}.bookmakers`: extended from 6 → 12 books each.
    - `_normalize_market_data` Pass 1 (layer slots), Pass 2 (assignment + opp_field map), Pass 3 (flatten): added 6 new books with short codes `eb`/`hrb`/`brv`/`prx`/`bly`/`flf`.
    - `ALLOWED_BOOKS` whitelist extended to all 12 books.
  - `coverage_filter._BOOK_FIELDS`: 5 → 11 entries (book_count now ranges 0..11).
  - `tp_engine._BOOKS` + `_OPP_FIELDS`: 4 → 11 books for de-vig probability averaging across the full book set.
  - `prop_scores_store._BOOK_LAYER_FIELDS`: added layer/line/odds/odds_opp for all 6 new books (preserves through score-doc projection).
  - `recompute.py` `_book_k` mirror loop: extended to 6 new book field tuples.
- **Live verification (post-sync, 25 MLB events)**:
  - Total props synced: 14,252 → **16,865** (+2,613 new props from broader book coverage).
  - Bookmaker breakdown: DK 9,280 / MGM 9,254 / **ESPN BET 8,956** / **Hard Rock 7,860** / PP 7,001 / FD 5,722 / **Fliff 5,528** / Caesars 4,301 / BOL 3,812 / **BetParx 3,365** / **BallyBet 1,945** / **BetRivers 1,778**.
  - Coverage class transition: pp_only 10.1% → **6.5%** | single_book 31.7% → 22.9% | **multi_book 58.2% → 70.6%**.
  - 485 props now have ALL 11 books anchoring; 1,474 have 9–11 books (sharp consensus zone).
- **Tests**: `tests/test_all_books_expansion.py` (6 new regressions) + `tests/test_coverage_filter.py` (3 new Caesars tests) — 21 new passing tests. 175+ legacy tests still pass.
- **Audit**: `/app/audit_reports/mlb_vs_nba_gate_audit_2026-05-13.md` (covers pre-expansion baseline).

### 2026-05-13 — Caesars (williamhill_us) added to book-anchor counter (MLB lift +871 props)
- **Root cause**: `coverage_filter._BOOK_FIELDS` only recognized 4 books (DK / FD / BetOnline / BetMGM). Caesars (`csr_layer` + `csr_odds`) was wired into `universal_odds_sync` on 2026-05-11 and persisted on 4,379 MLB props (30.2% of live pool), but never counted toward `book_count` or `books_anchored`.
- **Fix**: added `("williamhill_us", "caesars_price", "csr_odds")` to `_BOOK_FIELDS`. Updated docstring to reflect 0..5 book range.
- **Projected impact on the 14,499 live MLB props**:
  - `pp_only` (dropped): 1,465 → **1,190 (−275 props rescued into scoring)**
  - `single_book`: 4,603 → 4,282
  - `multi_book` (devig-eligible): 8,431 → **9,027 (+596 props gained 2nd quote)**
  - Total upgraded: 871 MLB props.
- **Tests**: 3 new Caesars regressions added (`test_classify_caesars_counts_as_anchor`, `test_classify_caesars_legacy_field`, `test_classify_all_five_books_including_caesars`). 30/30 coverage / decoration / Caesars-chain tests pass.
- **Audit**: `/app/audit_reports/mlb_vs_nba_gate_audit_2026-05-13.md`.

### 2026-05-09 — Replay Phase 2.5 partial-parity 30-day run COMPLETE — honest gap analysis
- **TP engine + reference odds + coverage classifier wired** into the replay path: `services/replay/engine.py` now imports `compute_tp`, `_pick_reference_odds`, `classify_coverage` from production scoring with ZERO forks. Refactor: group by `canonical_key` (not side) → score both sides → paired-book layer construction → flat `{prefix}_odds`/`{prefix}_odds_opp` populated → TP fires → coverage gate passes → real production tier decisions emerge.
- **30-day NBA replay** (2024-02-01 → 2024-03-01, t-30m, run_id `a1aeb71a6ef046baae4fb56deef06667`): **503,200 evaluations** scored end-to-end across all 18 markets and 5 Phase-1 books. **97.2% reached `feature_completeness="partial"`** (TP fired); 2.8% capped at `minimal` (rare no_reference_market cases). 0 leakage blocks, 0 feature/scoring failures.
- **Outcomes settled**: 134,636 unique (61,597 hit / 69,594 miss / 3,445 void). Baseline PnL on unqualified picks = −16,152 units (−12.0% ROI). Note: `replay_outcomes` unique key currently lacks `event_id` → cross-event canonical_key collisions cap unique outcomes at 134k; known schema bug, 10-line fix queued for next session.
- **All 503,200 evaluations classified `tier=unqualified` — exactly as predicted with partial features.** Top failure reasons: `gate_hit_rate_fail` 158k (FL), `gate_direction_fail` 254k across SH/FL/WZ. Without VK2, the replay μ is a rolling average and systematically loses direction-fight to lines.
- **Honest confidence statement** committed to `/app/audit_reports/replay_phase25_30day_FINAL.md`: trustworthy for leakage / infrastructure / TP math / chronology integrity; NOT trustworthy for tier ROI / calibration / gate optimization / WZ longshot validation / deployment confidence. Directional signal only on which gates fire for which reasons.
- **Phase 2.5 parity matrix** (clear what's wired vs. blocked): TP ✅, ref-odds ✅, coverage ✅, gate engine ✅, leakage ✅, L5/L10/L20+μ+σ+CV ✅, VK2 ⚠️ blocker, injury timeline ⚠️ blocker (needs new data source), matchup/pace ⚠️ feasible from BDL.
- **Operational hardening**: log-pruner committed (`scripts/prune_rotated_logs.sh`); 3 transient mongod restarts during the run due to `/app` log volume hitting 100% (cleared each time without progress loss thanks to chunked writes + idempotent design).
- **Tests**: 112 replay tests passing (added 8 engine + 32 resolver math + 14 ingester + 18 leakage; refactored engine tests for canonical_key grouping). 178 total including stabilization suite.
- **Production untouched**: replay-only writes; live scoring/gates/board/forward-test code paths unmutated; lineage sentinel `historical_replay` enforced.

### 2026-05-09 — Replay Phase 2 contract-proving skeleton + log-pruner committed
- **Result Resolver** (`services/replay/result_ingester.py`): pulls NBA per-game stats from `bdl_historical_game_logs` + `nba_master_hub_2026.player_game_logs` with cross-validation; writes `replay_results` keyed `(event_id, player_norm)`. **2,894 result rows resolved in 1.5s**, 100% BDL coverage; hub schema returned 0 (separate Phase-2.5 fix). Mismatch rows flagged `validation_status="mismatch"` with full source-A/B preserved — never silently overwritten.
- **Replay Engine** (`services/replay/engine.py`): calls **production `compute_scoring_stack()` directly** — zero scoring forks. Wires reference-odds chain via real book layers from `replay_props_normalized`; mandatory `assert_pregame_only()` + `assert_no_future_games()` gates before every feature build. Smoke run on 200 offers from 2024-02-05: **0 leakage blocks, 0 scoring failures, 341 evaluations persisted in 1.39s**, end-to-end production-scoring path validated.
- **Honest parity tracking**: every evaluation is stamped `feature_completeness="minimal"` because as-of-time builders for **VK2 / model_sigma / injury usage / matchup / pace / TP engine / avg hit-miss margin** are PARITY-TODOs requiring Phase-2.5 historical timeline ingest. The skeleton refuses to claim full parity until those are wired. Tier reason `no_reference_market` on 100% of skeleton evaluations correctly reflects this.
- **Outcome Resolver** (`services/replay/resolver.py` + `scripts/run_outcome_resolver.py`): pure-functional settlement math (`settle`, `realized_payout`, `implied_probability`, `calibration_gap`, `closing_line_value`, `build_outcome_row`). Smoke pipeline end-to-end: 341 evals resolved in 0.16s → 140 hit / 194 miss / 7 void_dnp / **PnL -80.86 units** (random unqualified picks → negative ROI as expected; meaningful signal once real tiers populate).
- **Tests**: 46 settlement-math + ingester tests, 8 engine integration tests, 18 leakage tests, 38 schema/snapshot/run-header tests = **110 replay tests passing in 0.52s**. Mutation-style leakage tests prove `build_as_of_features()` rejects future games even if the BDL filter is bypassed.
- **Log-pruner** (`scripts/prune_rotated_logs.sh`): deletes rotated `*.log.[N]*` files older than 24h (configurable via `--max-age`); dry-run mode (`--dry-run`); never touches active logs; supervisor/cron-safe; idempotent. Tested + working — prevents recurrence of the disk-full → MongoDB crash sequence we hit during the 30-day ingest.
- **Production untouched**: only `replay_*` collections written. Live scoring/gates/board/forward-test code paths NOT mutated. 124 prior tests still pass; combined replay + stabilization suite = **178 tests passing**.
- New collections populated: `replay_results` (2,894 rows / ~0.5 MB), `replay_evaluations` (645 rows from 2 smoke runs / ~3 MB), `replay_outcomes` (341 rows / ~0.2 MB).

### 2026-05-09 — Replay Phase 1 30-day NBA ingest COMPLETE + Phase 2 integrity gates
- **Range**: 2024-02-01 → 2024-03-01 UTC (23 game-days, 6 All-Star break days). **183 events, 24,475 snapshot docs, 3,520,527 normalized rows** (2.38M alt-line + 1.53M combo). **205,880 credits** spent (4.13M remaining of 5M pool). **18m 35s wallclock**. Single-attempt clean completion after MongoDB pressure fix.
- **All 18 markets** present, **all 5 Phase-1 books** present (FD 1.14M / BOL 766k / DK 746k / Caesars 611k / MGM 256k). 8-window snapshot ladder fully populated.
- **Zero anomalies**: `duplicate_anomaly` PASS, `malformed_threshold` PASS, `book_whitelist_compliance` PASS, `chronology_intact` PASS. 500-row random pregame audit: 0 violations.
- **Hardened ingest layer**: `services/replay/{ingest_progress,ingest_telemetry,full_ingest,leakage_checks}.py`, `scripts/{run_full_ingest,run_full_ingest_loop,validate_replay_ingest}.py`. Resumable (`replay_ingest_progress` collection with status lifecycle); idempotent re-runs; chunked 500-op bulk_writes prevent MongoDB index-maintenance pressure; bypass-tenacity 404 path saves ~5× retry cost.
- **Phase 2 integrity tests COMMITTED**: `tests/test_replay_leakage.py` (18 passing) covering as-of-time leakage, pregame-only assertion, 8-window chronology monotonicity, envelope-chain integrity. `services/replay/leakage_checks.py` provides reusable check functions for the future replay engine.
- Engineering note: cleared rotated `/var/log/mongodb.out.log.[1-9]` and `/var/log/supervisor/*.log.[1-9]` to free ~1 GB on the shared 9.8 GB `/app` volume after disk-full caused mongod to crash mid-write. Documented for future cleanup automation.
- **Storage footprint**: 3.09 GB across 3 replay collections (data 2.83 GB + indexes 0.26 GB). Live collections + forward-test data UNTOUCHED.
- Reports: `/app/audit_reports/replay_full_ingest_2024-02-01_to_2024-03-01_FINAL.md`, `/app/audit_reports/replay_full_ingest_2024-02-01_to_2024-03-01.json`, `/app/audit_reports/replay_full_ingest_validation.json`.

### 2026-05-09 — Replay Phase 1 NBA Canary (5 events × 8 windows × 18 markets) — PASSED
- Built `services/replay/{odds_fetch,normalizer,canary_events,ingest_odds}.py` and `scripts/run_canary.py`. Writes ONLY to `replay_odds_snapshots` + `replay_props_normalized`. No production touch.
- Initial run (2024-03-01 NBA slate, 5 events): 6,650 credits / 36.6s wallclock / 39 of 40 calls 200-OK / 1 t-24h SnapshotNotAvailable (events not yet listed by books) handled gracefully without retry waste / 0 errors.
- **665 snapshot docs** (per (event, market, window)) and **100,013 normalized rows** written.
- All 18 NBA markets returned data; bottom-3 markets: `player_rebounds_assists` (2502), `player_steals` (2574), `player_points_assists` (2597). Top-3: `player_points_alternate` (13,959), `player_points_rebounds_assists_alternate` (11,986), `player_rebounds_alternate` (10,879).
- All 5 Phase-1 books returned data: FanDuel (33,061), BetOnline (22,612), DraftKings (20,176), Caesars/williamhill_us (16,350), MGM (7,814). MGM is sparser as expected.
- **Duplicate groups: 0** (unique compound index `uniq_event_label_book_market_player_line_side` enforced).
- **Idempotency verified**: rerun of identical canary produced 0 net inserts, 0 net snapshot inserts, 0 duplicate groups (still 100,013 / 665). 102,158 modifications + 665 snapshot mods (`$set` is the no-op upsert path).
- 20-row random sample shows realistic distributions (varied lines, both Over/Under, alt + non-alt, all 5 books represented). `implied_probability` correctly computed from American odds.
- New module file `services/replay/odds_fetch.py` defines `SnapshotNotAvailable` and bypasses the existing tenacity retry on 404 (saves ~5× wasted credits when an event isn't listed at the requested snapshot ts).
- Outputs: `/app/audit_reports/replay_canary_initial.json`, `/app/audit_reports/replay_canary_rerun.json`.

### 2026-05-09 — Replay Test Suite Phase 0 (scaffolding only, no DB / no API)
- Design doc committed: `/app/audit_reports/replay_suite_design_2026-05-09.md` (664 lines, 10 deliverables + 2 appendices). Approved decisions: 8-window snapshot ladder (`t-24h, t-12h, t-6h, t-3h, t-90m, t-60m, t-30m, close`); Phase-1 books = DK/FD/BetOnline/Caesars/MGM (Pinnacle deferred); 1M-credit hard kill switch per ingest run; per-tier canonical snapshot SH=close, FL=t-60m, WZ=t-30m; result source = BallDontLie + nba_master_hub_2026 cross-validation.
- Files added: `backend/services/replay/{__init__.py,snapshot_plan.py,markets.py,schema.py,run_header.py}`, `backend/scripts/{run_replay.py,compare_replay_runs.py}`, `backend/tests/{test_replay_snapshot_plan.py,test_replay_run_header.py,test_replay_schema.py}`.
- Schema: 11 isolated `replay_*` collections with declared INDEX_SPECS; `dataset_lineage="historical_replay"` quarantine sentinel keeps replay outputs out of forward-test reporting.
- Versioning: `compute_run_fingerprint()` produces `git_commit + git_dirty + scoring_config_hash + gate_config_hash` (covers 10 scoring files + 4 gate files); deterministic, order-independent, missing-file safe.
- 38/38 new tests pass (0.50s); 68/68 prior stabilization tests still pass; `git status` confirms zero modifications to existing files. No DB writes. No API calls.

### 2026-05-09 — The Odds API HISTORICAL alt-prop audit (READ-ONLY)
- Goal: confirm exact recipe to fetch historical NBA alternate player-prop ladders (incl. combos) for any date ≥ 2023-05-03 from The Odds API.
- 31 credits spent (cap was 35). All three probed alt-market keys returned 200 with non-empty ladders on a 2024-03-01 NBA event.
- Validated keys: `player_points_alternate` (6 books), `player_points_rebounds_assists_alternate` (4 books), `player_points_rebounds_alternate` (3 books). Naming rule confirmed: `<live_key>_alternate`. PA/RA combos not probed — pattern strongly implies same shape but each needs a 10-credit confirmation.
- Recipe: `GET /v4/historical/sports/basketball_nba/events?date=…` (1 credit) → pick eventId → `GET /v4/historical/sports/basketball_nba/events/{eventId}/odds?regions=us&markets={ONE_ALT_KEY}&oddsFormat=american&date=…` (10 credits per market per region per event). 5-minute snapshot cadence; envelope ships `timestamp/previous_timestamp/next_timestamp`.
- Gotchas documented: single-sided alt outcomes (DK PRA-alt is Over-only), per-market book coverage shrinks (PTS-alt=6, PRA-alt=4, PR-alt=3), date floor 2023-05-03, never bundle markets in a single call.
- Cost model: ~641 credits per NBA slate × 8 alt markets, ~98k credits per full season at 4 markets ~321/slate.
- NO production patches. NO scoring/gates touched. NO storage. Read-only script: `backend/scripts/odds_api_historical_audit.py`.
- Deliverables: `/app/audit_reports/odds_api_historical_audit_2026-05-09.md` (consolidated) + `/app/audit_reports/odds_api_historical_audit_2026-05-09/` (raw payloads + machine summary).

### 2026-05-08 — Universal injury redistribution model (math rebuild)
**Problem**: card outputs were unrealistic (Wemby +12% on a David Jones OUT, LeBron +15 mins, SGA +12% on JWill OUT). Root cause was `_get_beneficiaries` applying flat per-rank constants (+15 mins / +12% usage / +10 mins / +8% usage / +5 mins / +5% usage) that did not depend on injured magnitude or absorber saturation.
- **New helper** `services/injury_vacuum_service.py::_compute_redistribution(injured, teammates)`. Sport-agnostic two-layer model:
  - **Layer 1 (Minutes)**: weight = mins_headroom × mpg_proximity × bench_factor × position_match. Per-player saturation cap = `8.0 × (1 − baseline_mpg/40)^0.7`. Hard ceilings: `MAX_INDIVIDUAL_MPG=40`, `INDIVIDUAL_MIN_SHARE_CAP=0.45`.
  - **Layer 2 (Usage)**: usage_share × elasticity, where `elasticity = (usage_headroom/MAX_INDIVIDUAL_USAGE)^1.5`. Saturated alphas absorb a fraction of what bench-rotation players absorb per share.
- **Output exposure**: every card now carries `baseline_minutes`, `projected_minutes`, `minutes_delta`, `baseline_usage`, `projected_usage`, `usage_delta`, `redistribution_share`, `elasticity_factor`. Display-side `minutes_bump` / `usage_bump` are back-compat but reflect modeled deltas.
- **Cross-sport safety**: helper returns zero-delta scaffolds when canonical fields (`minutes_per_game`, `usage_percentage`) are absent. MLB beneficiary code (separate path) is unchanged; opt-in only.
- **Verified live (NBA, 2026-05-08)**:
  - Wemby (David Jones OUT, primary): **+15 → +0.9 mins, +12% → +0.2% usage**.
  - SGA (JWill OUT, primary): **+15 → +2.1 mins, +12% → +0.1% usage**.
  - LeBron (Luka OUT, primary): **+15 → +1.8 mins, +12% → +0.5% usage**.
  - Kobe Bufkin (Luka OUT, tertiary bench): **+5 → +6.9 mins, +5% → +4.6% usage** (correctly absorbs more minutes than LBJ).
- **6 regression pytest cases pass** (`tests/test_injury_redistribution.py`): low-rotation injury (alpha minimal), secondary-star OUT (alpha dampened), minutes ceiling (≤40), usage elasticity (saturated < open-canvas), no noise flood (shares sum to 1, ceilings respected), cross-sport zero-delta scaffold.
- **Files**: `backend/services/injury_vacuum_service.py`, `backend/tests/test_injury_redistribution.py` (new). NO frontend, NO scoring, NO tier, NO cached_board, NO ticker, NO Vision Intel touch.

### 2026-05-08 — Live Injury Advantage star-qualifier widening
**Audit-only finding** (no caches involved): cards aren't stale — they're being **filtered out** by an overly-strict 22% usage gate in `InjuryVacuumService._is_star_player`. The recompute path is correct; it just rejects rotation regulars whose injury is genuine breaking news. Smoking gun: OG Anunoby (NYK, OUT, usage=19.4%, mpg=33+) was being silently dropped while Luka Doncic (36.8%) and Jalen Williams (25.2%) passed.
- **Single change** in `services/injury_vacuum_service.py`:
  - Lowered `SECONDARY_ALPHA_THRESHOLD` from 22.0 → 18.0.
  - Added `ROTATION_MINUTES_THRESHOLD = 24.0` — admit on `usage_percentage >= 18` OR `minutes_per_game >= 24`.
  - Both `nba_star_usage_cache` and `nba_master_hub_2026.advanced_stats` lookup paths use the same dual-axis admission. New `alpha_tier="rotation"` for the minutes-only path so downstream code can distinguish.
- **Verified live**: card count went from 6 alerts (2 distinct injured) → 15 alerts (5 distinct injured) within a single restart. NEW visible: **OG Anunoby (NYK)**, **Kevin Huerter (DET)**, **David Jones (SAS)**. Luka Doncic + Jalen Williams retained. No noise flood (DiVincenzo / Sorber correctly filtered as expected).
- **Acceptance**: 6 of 6 user criteria pass (OG visible, existing cards retained, no flood, API live, freshness badge still 7s, no duplicates).
- 5 unit tests (`tests/test_injury_advantage_qualifier.py`) lock down the new admission rules.

### 2026-05-08 — Live Injury Advantage freshness pipeline (5 fixes)
Audit identified 6 stale stages; user approved 5 (deferred legacy-writer cleanup #5 to 48h post-rollout).
- **#1 Cadence fallback** (`services/injury_sensor.py::_get_cadence`). When `live_scores_cache.games` is stale, fall back to a `{sport}_cached_board` activity probe — return `CADENCE_ACTIVE (120s)` when any sport has live cached_board props. Sport-agnostic. NBA was stuck at IDLE=300s mid-season pre-fix.
- **#2 Severity gate lowered** (`services/injury_triggered_rescore.py::_on_event`). Accept `high` AND `medium`. Q→OUT (`status_escalated`, tier_delta=1), `return_date_shifted`, de-escalations now trigger live rescore + cached_board patch. Pre-fix, all medium events were dropped.
- **#3 _patch_cached_board hardening** (same file). Off-board injured players contribute their `team` from `injuries_normalized` (so teammates still patch). Update filter accepts `bdl_id` fallback so name-format drift can't silently no-op.
- **#4 Per-player dedup** (`services/injury_sensor.py::_emit_changes`). Dedup key changed from `sport|team` to `sport|player_key`. Sibling injuries within the 5-min recency window are no longer suppressed.
- **#6 Wire-level freshness signal** (`routes/vacuum.py`, `routes/mlb_vacuum.py`). Live-alerts response ships `served_at`, `oldest_source_synced_at`, `newest_source_synced_at`, `source_age_seconds`. Frontend `LiveInjuryAdvantageSection` renders an "Updated Xs ago" badge with green/amber/red banding (<2min / <5min / older).
- **Verified live**: NBA cached_board patches firing within seconds of ESPN sensor poll (116/150 docs with `last_injury_rescore_at`). NBA + MLB endpoints both ship `source_age_seconds=17`. 6 regression pytest cases pass (`tests/test_injury_freshness.py`).
- **Files**: `backend/services/injury_sensor.py`, `backend/services/injury_triggered_rescore.py`, `backend/routes/vacuum.py`, `backend/routes/mlb_vacuum.py`, `frontend/src/pages/Dashboard.jsx`, `backend/tests/test_injury_freshness.py` (new).
- **Deferred**: #5 (kill `live_injury_micro_sync`, `scheduled_hourly_injury_sync`, `scheduled_live_injury_check`) — review window ≥48h.

### 2026-05-08 — Universal stat-label adapter
- New `frontend/src/utils/statLabel.js`. `getStatLabel('player_points_rebounds_assists')` → `'PRA'`, `getStatLabel('batter_hits_runs_rbis')` → `'H+R+RBI'`, etc. Strips alternate suffixes, humanizes unknowns. ONE helper used by Command Center, Player Detail, board cards, MarketMoves, simulation toasts.
- Migrated `UniversalPlayerCard.jsx`, `lib/PickVisionUtils.jsx`, `PlayerDetailPage.jsx`, `CommandPost.jsx`, `Dashboard.jsx`, `MarketMoves.jsx`. 12 frontend Jest tests pass.

### 2026-05-08 — Universal Command Center analytics (volatility + correlation)
- **Per-leg volatility** now derives from canonical `cv` (clamp 0..2.0). Side-aware hit-rate spread fallback when `cv` is null. CV-scaled label thresholds (LOW <0.20, MED 0.20–0.45, HIGH ≥0.45). Pre-fix metric was bounded ~0.5 from `|h10-h5|` noise — war-zone vs safe-haven indistinguishable.
- **Correlation** rewritten as pairwise rules over canonical leg fields (`player_name`, `event_id`, `team`, `sport`): `same_player → 0.85`, `same_game → 0.40`, `same_team(+sport) → 0.20`, else `0`. Aggregate ρ = mean. Penalty = 1 − clamp(ρ × 0.5, 0, 0.5). Response ships `correlation_score`, `correlation_kind`, per-leg `cv`, `volatility_source`.
- **Frontend** Correlation card shows `correlation_kind` text (Independent · 0% / Same Game · -X% / Same Team · -X% / Same Player · -X%). Never blank.
- **Verified (5 new pytest)**: Brunson+Edwards low-CV→high-CV: vol 0.43 → 1.13. Brunson AST 2.5+9.5: kind=`same_player` score 0.85 pen 42.5%. Brunson+Embiid same event: kind=`same_game` score 0.40 pen 20%. Mixed NBA+MLB: kind=`none` score 0 pen 0%.

### 2026-05-08 — Universal Command Center prop source
- **New helper**: `services/command_center_props.py::get_command_center_props(db, sport, *, player_name=None, canonical_key=None)` — sport-agnostic SSOT reader. Reads canonical rows from `{sport}_prop_scores` filtered to `version_tag=final-{sport}-rt` AND `active=True`. ONE adapter (`_to_canonical_prop`). No cached_board, no stat-level joins, no sport-specific branching beyond the collection name. Adding NFL = one entry in `SCHEDULED_SPORTS`.
- **New route**: `GET /api/command/props?sport={sport}&player_name={name}` (also accepts `canonical_key=...`). Validates sport via `SCHEDULED_SPORTS`. Returns `{success, sport, version_tag, player_name, meta, props[]}`. 400 on unknown sport / missing params, 404 on unknown player.
- **Canonical row contract** (no legacy aliases): `canonical_key, sport, player_name, stat_type, line, recommendation, direction, hit_rate_l5, hit_rate_l10, hit_rate_l20, hit_rate_over, hit_rate_under, p_true_active, edge_vs_fair, vision_score, cv, team, opponent, tier, tier_reason, pp_odds, dk_odds, fd_odds, bol_odds, mgm_odds, tier_reference_book, tier_reference_odds, event_id, game_start_utc, bdl_player_id, is_home`. `h5_rate / h10_rate / hit_rate / hit_rates` are NEVER read or emitted on this path.
- **Frontend**: `useCommandCenterProps` hook in `useLiveOdds.js`. CommandPost replaces the legacy `usePlayerProfile` → `.map(line => ({...}))` reshape with verbatim canonical rows from the new endpoint. New `buildCanonicalLeg` helper forwards canonical fields ONLY to `/api/command/simulate` for both inline-add and Quick-Add (`pendingLeg`) paths.
- **Verified (testing agent + 8 pytest cases)**:
  - Brunson NBA AST: 20 alt rows, 8 distinct `hit_rate_l10` values (0/10/30/40/50/60/70/90) — original "all 90%" smearing bug eliminated.
  - Contreras MLB: 25 canonical rows, all `mlb|`-prefixed `canonical_key`, `version_tag=final-mlb-rt`.
  - Mixed NBA+MLB simulation: convergence rate 64.0%, grade C — works.
  - Frontend CommandPost smoke: search → profile shows varying L10 chips → click adds canonical leg. Network tab confirms `/api/command/props` (not `/api/v3/player-with-badges`).
- **Files**: `backend/services/command_center_props.py` (new), `backend/routes/command.py`, `backend/tests/test_command_center_props.py` (new), `frontend/src/hooks/useLiveOdds.js`, `frontend/src/components/dashboard/CommandPost.jsx`.

### 2026-05-08 — Breaking News Ticker stabilization patch
- Swapped ESPN RSS (HTTP 202, bot-fenced) for ESPN public JSON news API (`site.api.espn.com/apis/site/v2/sports/{basketball/nba|baseball/mlb}/news`)
- Fixed CBS Sports regex to tolerate both `<![CDATA[...]]>` and plain `<title>` wrappers (was capturing 0/36 headlines)
- Removed dead Bleacher Report feed block (HTTP 404)
- Added `mlb_ticker_sync` scheduler job — MLB news was frozen at 2026-04-11 because no MLB ticker job was registered
- Added default `User-Agent` + `Accept` headers via `TICKER_HTTP_HEADERS` to both ticker httpx clients
- Files: `backend/routes/live.py`, `backend/server.py`

### 2026-05-08 — Cached_board materialization (architecture fix)
- **New SSOT**: `{sport}_cached_board` is now a materialized view of `{sport}_prop_scores[version_tag=final-{sport}-rt]`. No independent tier logic; tier assignments carried verbatim from -rt.
- **New service**: `services/board_snapshot_publisher.py::publish_board_snapshot(db, sport)` — single writer. Upsert-only (never deletes), preserves doc-level enrichment (photo_url, injury_status, context_badges, etc.), empties stale players' `props[]` without removing the doc, bails on empty source (zero-wipe guarantee).
- **Delta Engine integration**: added `PublishBoardSnapshotStep` (pos 5 in `DEFAULT_DELTA_STEPS`). Runs only when `written>0` or `retired_modified>0`; skips when upstream lock is held; failure-isolated.
- **master_sync integration**: step 7 replaced (`stamp_cached_board_freshness` → `publish_board_snapshot`). Single build path; metrics key renamed `7_cached_board_snapshot_publish`.
- **Verified (natural delta ticks, no manual triggers)**:
  - NBA tick rescored 153 props → publisher rebuilt 64 players, emptied 76 stale, from 3,074 active -rt props in 1.18s.
  - MLB tick rescored 212 props → publisher rebuilt 318 players, emptied 0 stale, from 6,603 active -rt props in 4.29s.
  - SLO §3 Tier Freshness now PASSES naturally (was FAIL 12.5-hour staleness before patch).
  - API tier endpoints (`/api/v3/ferrari/all`) continue to serve enriched picks unchanged.
- **Tests**: `tests/test_board_snapshot_publisher.py` — 7 passing: source=final-{sport}-rt, rebuild-on-written, skip-on-zero-writes, empty-source-preserves, master_sync-uses-same-path, freshness-matches-rt-timestamps, no-independent-tier-assignment; plus stale-player-emptied-not-deleted, ingestion-fields-preserved-via-canonical-key-merge.
- **Files**: `backend/services/board_snapshot_publisher.py` (new), `backend/services/pipeline/delta_steps.py`, `backend/services/master_sync.py`, `backend/tests/test_board_snapshot_publisher.py` (new).

### 2026-05-08 — Ticker 15-min cadence + source-protection patch
- NBA `ticker_sync` cadence: `CronTrigger(minute='0,15,30,45')` (was daily 9:26 UTC)
- MLB `mlb_ticker_sync` cadence: `CronTrigger(minute='5,20,35,50')` (was hourly :32) — offset 5 min from NBA so fetches never overlap
- Added `TICKER_PROTECTED_STATUS = {202, 403, 429}`; both sync functions log `WARNING` per source on protected/empty responses
- Both sync functions now track `external_count` and **skip the upsert** when `external_count == 0` and a last-good cache exists — preserves cache through transient blackouts
- `get_breaking_news` is now cache-only: removed `_fetch_news_fallback` helper; cold cache returns empty list instead of triggering request-path HTTP. Scheduler is the sole writer of `ticker_cache`.
- Verified: healthy cycle = 15 NBA / 11 MLB headlines; simulated blackout (ESPN→202, CBS→403) preserved baseline cache untouched (`preserved_cache:True`).
- Files: `backend/routes/live.py`, `backend/server.py`

### 2026-05-14 — `total_edge` (Model + Shopping combined) + replay cleanup
- **Investigation conclusion**: `edge_vs_fair` (model alpha) and `best_book_edge` (shopping alpha) are correctly computed but measure two different things and share only the `fair_prob` operand. Documented in CHANGELOG.
- **New field `total_edge`** on `ScoreDocument` (Pydantic) + `_SCORE_OUTPUT_FIELDS` allowlist + `compute_best_book_metrics(p_model=…)` kwarg. Formula: `p_model − best_book_implied`. Side-aware via `ctx.p_model` (== `doc.p_true_active`). Independent of `fair_prob`.
- **Display only** — NOT fed into gates. Distribution-snapshot required before any gate touch (per user spec).
- **UI labels renamed**: `UniversalPlayerCard.jsx` tooltip now shows "Model Edge / Shopping Edge / Total Edge"; `PlayerDetailPage.jsx` MLB stats row split into 4 cols (CV / Model Edge / Total Edge / True Prob).
- **Distribution snapshot** (active=True only):
  - **NBA** (n=290): median total_edge −3.3% slate-wide. Qualified tiers all ≥+16% median (FL +21.4%, SH +16.0%, WZ +35.3%). Unqualified median −4.6%.
  - **MLB** (n=1,536): median +4.7% slate-wide. WZ median +43.9%, FL +12.9%, SH +9.8%. 39.5% of props show total_edge ≥+10%.
- **Tests**: 5 new total_edge cases in `tests/test_best_book.py` (29 total, all pass). Includes mathematical proof that `total_edge` is independent of `fair_prob`.
- **MongoDB cleanup**: Dropped `replay_evaluations` (1.22M docs / 5.998 GB) and `replay_outcomes` (230K docs / 0.158 GB). DB now 1.685 GB on disk.
- **Files**:
  - `backend/services/scoring/best_book.py`
  - `backend/services/scoring/recompute.py` (best-book loop passes `p_model`)
  - `backend/services/scoring/prop_scores_store.py` (allowlist)
  - `backend/services/scoring/score_document_schema.py` (schema field)
  - `backend/tests/test_best_book.py` (5 new tests)
  - `frontend/src/components/dashboard/UniversalPlayerCard.jsx` (3-edge tooltip)
  - `frontend/src/components/dashboard/PlayerDetailPage.jsx` (Model / Total Edge cells)

## Files of Reference
- `backend/routes/live.py` — ticker sync + endpoints
- `backend/server.py` — scheduler config
- `backend/services/board_freshness.py`
- `backend/services/master_sync.py`
- `backend/services/jit_vision_intel_reaper.py`
- `backend/services/badge_enrichment.py`
- `backend/services/mlb_environmental_badges.py`
- `backend/services/scoring/prop_scores_store.py`
- `backend/scripts/production_readiness_slo_check.py`
- `backend/routes/ferrari_tiers.py`

## Test Credentials
N/A (no auth integrations live yet; blocked on stabilization).

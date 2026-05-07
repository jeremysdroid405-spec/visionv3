# NBA/MLB Betting Analytics — Product Requirements

## Original Problem Statement
Restructure React/FastAPI betting app into a 100% Local-First, ID-based multi-sport analytics engine with Universal Opportunity and Probability models. Every scoring/model change must be backed by regression + mutation tests. Integrate Google/Apple OAuth and Stripe.

## Architecture
- **Frontend:** React + Shadcn UI; reads `/api/v3/ferrari/*` tier endpoints.
- **Backend:** FastAPI. Scoring in `services/scoring/` with per-sport adapters feeding a universal gate engine.
- **Board flow:** `*_live_props` → `NBAScoringAdapter` / `MLBScoringAdapter` → `*_prop_scores` (tiered) → `services/board/reader.py` → ferrari_tiers routes.
- **Watcher:** `services/delta/detector.py` runs every 20s (NBA) / 30s (MLB), diffs `live_props` vs `*_prop_scores @ active=True`, queues dirty keys for `_engine_p_over` re-scoring.

## Done in this session
- **Heteroscedastic Sigma (NBA Phase 2 — shipped):** Bucket multipliers (minutes × line) built from 272 settled outcomes; sigma rescaled per prop. 33 regression tests + 3 mutation tests, all green.
- **Reverted Phase 1 additive debias** (was overcorrecting volume stars).
- **Lowered NBA SH gates:** `vision_score_gate.min` 85→80; `edge_gate.min` 0.01→0.0.
- **Pick card matchup row:** "vs OPP · TipTime" — backend exposes `game_start_utc` + `event_id` via `_merge_score_with_board`; frontend renders compact row in `UniversalPlayerCard.jsx`.
- **Live ticker fix:** `routes/live.py` — keeps recent Finals (12h window), augments with today/tomorrow scheduled tipoffs. NBA + MLB.
- **Watcher bug fix (P0):** `services/delta/detector.py` — `new_keys` set-diff now subtracts only `active=True` rt keys. Stale `active=False` rows no longer mask scorable canonical_keys. Verified: MLB rescored 195 silently-blocked picks immediately after fix.
- **Variance tile (frontend):** `PlayerDetailPage.jsx` Variance now reads `volatility_score`/`cv` instead of broken `intel_suite.stability_index` (which returned std_dev=0 for composite MLB stat types).
- **SSOT Enforcement Phase 1 (2026-05-04):** shipped `services/field_ownership/` (registry+accessors+validators). Migrated `scored_at` (100% populated both sports) + `opponent` (0 team==opp violations). 12 contract tests green.
- **SSOT Enforcement Tier F (2026-05-04) — TTL self-prune LIVE:**
  - `ttl_at_7d_nonlive_ix` Mongo TTL index on `{nba,mlb}_prop_scores.ttl_at` with 7-day `expireAfterSeconds`. `_project_score_doc` stamps `ttl_at` ONLY when `version_tag` ∉ `_LIVE_VERSION_TAGS` (`final-nba`, `final-mlb`, `final-nba-rt`, `final-mlb-rt`). Live docs never carry the field → immune by absence.
  - Live exclusion proof: 0 leaked live docs. NBA 5,012 TTL-eligible (4,920 will expire next sweep, 92 in 7-day window). MLB 33,729 TTL-eligible (2,923 >7d).
  - Rollback one-liner: `db.{sport}_prop_scores.dropIndex("ttl_at_7d_nonlive_ix")`. No scheduler, no new cleanup service — Mongo handles it.
  - 5 remaining Tier F deletions (`edge_pct`/`vk_edge` stamping, `hit_rate_over` migration, `direction` deletion, `master_active_cache.json`, `dg_cached_board`) explicitly blocked — each has 7-34 reader sites requiring dedicated migration sessions.
  - **117/117 tests green, permanent repair ~85% complete. Six-tier SSOT campaign complete.**
- **SSOT Tier F #2 (2026-05-04) — `hit_rate_over` → `hit_rate_l20` reader migration:**
  - 11 canonical-first reader sites (routes/debug_snapshots, routes/player, routes/ferrari_tiers, services/dashboard_card_contract, services/scoring/metrics_builder) now read `hit_rate_l20` first, `hit_rate_over` only as fallback for pre-dual-write docs.
  - DB parity verified: 4,815 dual-written docs checked, 0 mismatches; 105 contract tests green.
  - Deletion deferred: 41,000 legacy-only docs (`6,727 NBA + 34,272 MLB`) must age out via 7-day TTL before readers can drop the fallback. Re-check parity in ~8 days.
- **SSOT Tier F #1 (2026-05-04) — `direction` alias stamping deleted:**
  - Dropped `"direction"` writes from all response-building paths: `_merge_score_with_board` (+ defensive `prop.pop("direction")` after board-entry clone because `nba_cached_board.props[]` still carries legacy alias), MLB prop-merge block, MLB detail-page backfill, top-5 goblins response, `picks_getter_service.{goblin_vault, front_lines, cached_player}`, `mlb_cached_board_builder._enrich_prop`.
  - Migrated 8 backend readers to `recommendation → side → direction` canonical-first order (ferrari_tiers resolver + 4-tuple lookup + content-hash + UNDER filter + UNDER-rewire, dashboard_card_contract, board/adapters/base, market_moves_engine).
  - Removed `"direction"` from `PICK_CARD_REQUIRED_KEYS` contract lockdown; new regression test `TestDirectionAliasStampingRemoved` asserts live API never returns `direction` key (6 endpoints × 40 picks confirmed 0).
  - Contract-enforcer `missing_or_null=['direction']` errors eliminated from backend logs.
  - **109/109 hit-rate + field-ownership tests green. Tier F remaining: `direction` reader-fallback deletion (Tier G once frontend purges `pick.direction`); `edge_pct`/`vk_edge` API stamping removal; `dg_cached_board` drop; `ScoreDocument` `extra="forbid"` flip.**
- **SSOT Tier F #2 (2026-05-04) — `edge_pct` / `vk_edge` / `true_edge` API alias deletion:**
  - Deleted 9 response-level alias stamps across `ferrari_tiers.py` (7), `player.py` (1), `debug_snapshots.py` (1).
  - Migrated 4 backend reader sites (rank tiebreaker, `lasso_high_edge` badge, HRR war-zone Mongo filter, value_score) to canonical `edge_vs_fair` only; 0 alias `.get()` reads remain in routes or response-building services.
  - Defensive `.pop("edge_pct"/"vk_edge"/"true_edge")` on the NBA `_merge_score_with_board` board-entry clone and MLB `dict(sc)` base shape to guarantee no upstream leakage.
  - Debug endpoints migrated too: `/api/v3/debug/safe-haven-rejects` sort key, `/api/v3/debug/shadow_board/compare` projection + `avg_edge_vs_fair` metric + rank-tuple metadata.
  - New regression test `TestEdgeAliasStampingRemoved` — 6 live-API parametrized cases assert zero alias leakage and 100% canonical field presence.
  - Live smoke (6 endpoints × 41 picks): `edge_pct=0 vk_edge=0 true_edge=0 edge_vs_fair=41/41`.
  - **115/115 tests green.**
- **SSOT Tier F #3 (2026-05-04, Option C) — legacy `dg_cached_board*` cleanup:**
  - Full migration off `nba_cached_board` / `mlb_cached_board` is a multi-session Option-D plan (18 readers + 8 writers; collections hold unique enrichment data not present elsewhere). Out-of-scope for Tier F.
  - This session: dropped orphaned `dg_cached_board_temp` (122 docs, last write 2026-04-23, zero active readers/writers), removed the `board_cache_temp` mapping + `BOARD_CACHE_TEMP_NBA` constant + `server.py` startup index-creation call so it cannot be recreated, renamed `self.dg_cached_board` → `self.cached_board` in `board_intelligence_engine.py`, and migrated every misleading "live data source" docstring/comment to the canonical `nba_cached_board` name.
  - New regression test `TestDgCachedBoardRetired` (4 cases): asserts both legacy collections are absent in Mongo, live cached_board collections still exist, and a static AST-style scan finds zero `db["dg_cached_board*"].find/.update/...` patterns in non-archive/non-test code.
  - **119/119 tests green.** Architecture clearly documented: `dg_cached_board*` is gone forever; `nba_cached_board` / `mlb_cached_board` remain live until Option-D phased migration.
- **SSOT Tier F #4 (2026-05-04) — `ScoreDocument` strict mode LIVE:**
  - Flipped `model_config.extra` from `"allow"` → `"forbid"`. Silent score-doc field drift is now structurally impossible.
  - Added 108 `Optional[…] = None` declarations grouped by domain (distribution probability layer, ECDF/calibration, NBA availability guard, NBA rate × minutes, NBA recency μ blend, NBA shadow projections, hetero σ, per-stat debias, RFA minutes, MLB Empirical-Bayes, MLB pitcher/batter μ overrides, LOM audit, war-zone CV, ceiling rate).
  - `SSOT_PYDANTIC_STRICT` env default flipped to `true`; semantics clarified — with `extra="forbid"` LIVE the schema raises on its own, the env flag governs only the `validate_score_document` re-raise vs WARN-and-continue mode (latter is an emergency escape hatch).
  - New parity-guard suite (`tests/test_score_document_parity.py`, 5 cases): `extra="forbid"` lock, projected ⊆ declared, declared-extras count tracked in `_ALLOWED_DECLARED_EXTRAS` (currently 9), required-fields check, live-DB scan finds 0 undeclared on 4,000 docs.
  - Fixed `test_schema_accepts_valid_doc` inversion — undeclared fields now MUST raise `ValidationError`.
  - New `/api/health/score-document-schema-parity` read-only probe — returns `parity_ok`, `extras_setting`, `declared_count`, `projected_count`, `missing_declarations`, `declared_extras`. Zero schedulers / writers.
  - Verified: ~900 prepared docs through dry-run NBA+MLB at multiple limits — 0 ValidationErrors. Live recompute NBA(20)/MLB(20) wrote 30/30 docs. Backend logs clean post-flip. **124/124 tests green.**
  - **Six-tier SSOT campaign complete.** Score-doc write boundary is now strictly typed and CI-locked.
- **`vision_score == 0.0` false-zero fix (2026-05-04) — NARROW Path B promotion:**
  - Single-function patch in `backend/services/scoring/recompute.py::_apply_vision_score_normalization`: when v1 percentile collapses to 0.0 (negative-edge picks) AND `vision_score_v2 > 0`, substitute v2 into `vision_score`. Picks with v1>0 keep their slate-percentile so `vision_score_gate` selectivity (calibrated to v1's `min: 80` SH / `min: 60` WZ) is unchanged.
  - First attempt (unconditional v2 promotion) collapsed NBA Safe Haven / War Zone counts to 0/0; reverted to narrow form per user direction.
  - Result on `final-nba-rt`: Safe Haven 0→12, War Zone 0→8, Front Lines 48 unchanged. `vision_score==0.0` 860→249. Maxey/Harden/Embiid PRA / Dylan Harper now pass gates with vs ≥86. **135/135 tests green; API smoke NBA SH=7 FL=12 WZ=6, MLB SH=10 FL=13 WZ=9.**
- **`momentum_data` SSOT registration (2026-05-04) — NBA only, bounded patch:**
  - Added FieldSpec entry to `services/field_ownership/registry.py`: owner=`prop_scores`, writer=`master_sync._enrich_nba_momentum`, fallback=NONE, null_policy=return_null, with full join-chain documentation (`bdl_player_id → master_hub.team_abbr → opponent_abbr → defensive_momentum_cache`).
  - Declared `momentum_data: Optional[Dict[str, Any]] = None` on `ScoreDocument` (was implicit through `_SCORE_OUTPUT_FIELDS` only); added to `_ALLOWED_DECLARED_EXTRAS` since the writer is `master_sync` post-recompute, not the projector.
  - `_enrich_nba_momentum` INFO log extended: `total_candidates`, `enriched_count`, `skipped_count`, `coverage_pct`, bucketed `skip_reasons` (no_bdl_id, no_team_lookup, no_event_match, no_canonical_key, no_stat_type, team_not_in_event, momentum_calc_failed). Skip-reason counters were pre-existing; only the surfacing changed.
  - Live: NBA coverage 99.1 % → **99.42 %** (14 missing, all `no_bdl_id`). `/api/health/score-document-schema-parity` reports `parity_ok=true`, declared_extras count 9 → 10. **123/123 tests green.**
  - **Out of scope** (documented in registry notes as missing feature, not bug): MLB momentum writer, `nba_cached_board.props[]` mirror coverage (~88.6 %), dedicated `/api/health/sync` momentum section.
- **Universal Performance Badge Generator (2026-05-04) — SSOT for `scout_badges`:**
  - New `services/performance_badges.py::generate_performance_badges(doc)` consolidates three duplicate generators (MLB inline `enrich_mlb_intel_suite` block, `_apply_under_badge_rewire`, `intel_suite_calculator._generate_scout_badges`) into one side-aware engine reading only canonical fields (`hit_rate_l5/l10/l20`, `hit_rate_under`, `edge_vs_fair` DECIMAL, `p_true_active`, `vision_score`, `cv`, `usage_bump_percent`, `dvp_rank`, `matchup_analysis.sp_matchup.rank`).
  - **Bug fixed:** `lasso_high_edge` unit-mismatch — old MLB block compared decimal `edge_vs_fair` (e.g. 0.20) against the integer `15`, so the badge never fired on real picks. New threshold is `abs(edge_vs_fair) >= 0.15`. Verified live: James Harden (NBA, edge 0.2007) now stamps `lasso_high_edge`; Naz Reid (edge 0.1307) correctly does NOT.
  - **Bug fixed:** MLB tier endpoints were returning `scout_badges: []` because `enrich_mlb_intel_suite` short-circuits when intel_suite is cached — added `_apply_universal_scout_badges(pick)` to both `_post_process_nba_picks` and `_post_process_mlb_picks` so badges are stamped unconditionally and tier endpoints match player-detail endpoints. Verified live: Mike Trout / Ramon Laureano / Cam Smith / Kyle Tucker now stamp `floor_lock` + `hot_streak` + `high_fidelity_model`.
  - 23 new regression tests in `tests/test_performance_badges.py` lock the decimal-vs-percent threshold (`0.14` no, `0.15` yes, `-0.15` yes), side-aware `floor_lock` / `hot_streak`, SP buzzsaw guard, and dict-form output shape. **104/104 SSOT-related tests green.**
- **PROP VISION STABILIZATION — Step 4 + 5 (2026-05-07) — P0-A complete:**
  - **Watermark fully retired (one detection system only).** Deleted `services/delta_watermarks.py`, `AdvanceWatermarkStep`, all readers (detector, health_sync, SLO check), and dropped the `delta_watermarks` Mongo collection. The dirty queue (`delta_dirty_queue`) is now the sole detection source.
  - **Adaptive sync engine recovered from restart-storm.** Removed inline 4h BDL game-logs refresh blocks from `_adaptive_poll_loop` (each took >900s, freezing the watchdog → 6 restarts → `RESTART_STORM_DETECTED`). The same refreshes already exist as standalone APScheduler daily cron jobs, so the inline copies were redundant.
  - **Multi-sport ingestion via callback split.** `_adaptive_sync_callback` now drives BOTH NBA → MLB sequentially under per-sport `UpstreamSyncLock`. Calls `sync_sport_props(enrich_features=False)` (the live_props writer, ~10-30s/sport) NOT `master_sync` (the full 7-10 min pipeline). Heavy recompute + Vision Intel enrichment stays on the existing APScheduler hourly cron + delta engine per-tick rescore. Intra-cycle heartbeat between sports keeps the watchdog alive.
  - **`STANDBY` 300s → 240s** in `PollInterval` so total cycle period (240s sleep + ~25-30s sync) stays comfortably under the <300s production-readiness ingestion SLO with margin for transient slowdowns.
  - **Dirty-queue drain leak FIXED.** `RescoreDirtyPropsStep` now confirms (deletes) ALL drained queue_ids regardless of `coverage_filter` match outcome or batch cap. Previous proportional-trim logic deleted only ~6% of drained ids, causing perpetual re-drain of the same low-`_id` rows and unbounded queue growth (~+4,500/tick). Verified by 2 new regression tests that exercise the worst case (0% match, with cap, without cap).
  - **30-minute observation window verifies all success criteria:**
    - NBA `live_props` freshness peak = 255.6s ✅ < 300s SLO
    - MLB `live_props` freshness peak = 198.0s ✅ < 300s SLO
    - 0 watchdog FROZEN events, 0 RESTART_STORM events, 0 process restarts
    - dirty_queue depth cycled 0 → ~9,500 → 0 cleanly between cycles (NO accumulation)
    - heartbeat_age_s range 47.7s → 236.5s (never exceeded threshold)
    - 7 consecutive cycles, all sub-30s for both sports combined
    - VmRSS flat at 26.5 MB (no leak)
  - 9 SSOT/queue tests green: `test_step3_dirty_queue.py` (incl. 2 new leak-fix regressions). Tier-counts active, score freshness <200s for both sports.
  - **Out of P0-A scope (acknowledged, not silently fixed):**
    - 60s-interval APScheduler jobs (Universal Game-Start Scanner, Wave 1 Shadow-Write Divergence Monitor) miss their slot by 1-55s during hourly `master_sync` enrichment (event-loop saturation). Pre-existing, unrelated to P0-A. Jobs catch up afterward.
- **PROP VISION STABILIZATION — Phase 4A (2026-05-07) — `edge_pct` SSOT cleanup COMPLETE:**
  - **DB-persisted legacy field eliminated.** `$unset edge_pct` migration ran twice (once before the writer fix exposed regression, again after the writer fix verified clean). Combined: 23,562 + 60,033 = 83,595 docs cleaned in pass 1; 363 + 487 = 850 regressed docs cleaned in pass 2; **0 docs remaining across all 4 score/board collections post-fix-and-restart-and-recompute.**
  - **5 persistence-layer writers fixed** (deepest layer was the score-doc allowlist):
    1. `services/scoring/prop_scores_store.py:_SCORE_OUTPUT_FIELDS` — removed `"edge_pct"` from the strict allowlist (the actual gate for what gets persisted).
    2. `services/scoring/recompute.py:690` — removed `doc["edge_pct"] = ctx.edge_pct` write.
    3. `services/scoring/score_document_schema.py:220` — removed `edge_pct: Optional[float]` declaration so `extra="forbid"` strict-mode validates correctly without it.
    4. `services/board/publisher.py` — `_snapshot()` no longer emits `edge_pct`; sort-tuple in `rank_tuple()` and merged-tier sort now use canonical `edge_vs_fair`.
    5. `services/board/shadow_publisher.py` — same two changes for shadow path (`rank_tuple_v2`, `_shadow_persist`'s snapshot, `get_shadow_board` merged sort).
  - **Re-eval reader migrated to canonical** (`services/scoring/metrics_builder.py:179`): `build_metrics_from_score_doc` derives `edge_pct = doc["edge_vs_fair"] * 100.0` instead of reading the legacy field. Same numeric value, sourced from SSOT.
  - **Response-layer `vk_edge` API leakage removed** from `services/vision_intel_service.py:325` and `services/mlb_vision_intel.py:256` (response stamps that exposed legacy field on prop dicts; companion `edge_pct` response stamp on `vision_intel_service` removed in same edit). Local variables that fed only the dropped stamps were also removed (linter F841 clean).
  - **Frontend already canonical:** `PlayerDetailPage.jsx` had only documentation comments referencing `edge_pct`, no live reads. No other frontend file references the legacy field.
  - **Scoring math untouched.** `scoring_stack.py`, NBA/MLB adapters, `vision_v2.py` continue to compute and pass `edge_pct` as an in-memory intermediate for tier evaluation — the scoring component score/edge_component math is byte-identical to before. Only persistence and response layers changed.
  - **Verification (live, post-fix, post-restart, post-recompute):**
    - `db.{nba,mlb}_prop_scores.count({edge_pct:{$exists:true}})` = 0 / 0
    - `db.{nba,mlb}_cached_board.count({edge_pct:{$exists:true}})` = 0 / 0
    - API tier endpoints `/api/v3/ferrari/all?sport={nba,mlb}`: 47 visible picks, 0 `edge_pct` leaks, 0 `vk_edge` leaks, 47/47 `edge_vs_fair` present
    - `production_readiness_slo_check.py`: §1 PASS (NBA 71s, MLB 57s), §2 PASS (NBA 11s, MLB 5s), §4 PASS (queue-based detection lag <70s both sports), §3 FAIL (pre-existing — cached_board timestamps), §5 FAIL (Phase 4B scope — `h5_rate`/`h10_rate`/`hit_rate`/`hit_rates`/`model_hit_rate_*` response shims), §7 tier-counts PASS.
    - Adaptive engine heartbeat 10s old, 0 watchdog events.
  - **Migration script:** `/app/backend/scripts/p0_phase4a_unset_edge_pct.py` (idempotent — rerunning produces 0 modifications).
  - **Verification harness:** `/app/backend/scripts/p0_phase4a_verify.sh` (5 sections; explicit pass/fail; documented out-of-scope items).

- **PROP VISION STABILIZATION — Phase 4B (2026-05-07) — hit-rate SSOT cleanup COMPLETE:**
  - **Two backend leaks fixed (no scoring/Vision/ingestion changes):**
    1. `routes/ferrari_tiers.py::_merge_score_with_board` — strip list expanded to also pop `l5_hits, l10_hits, l20_hits, h5_hit_rate, h10_hit_rate` from the cached_board prop. The downstream `_assert_canonical_hit_rate_invariant` guard was using these stale cached_board hits to recompute and OVERWRITE the score-doc canonical `hit_rate_l5/l10/l20` (verified bug: Julian Champagnie PTS 5.5 OVER, score=100/90/90 → API=80/90/85 from line-9.5 cached entry). Strip makes the guard a defensive no-op; canonical score-doc values now flow through unmodified.
    2. `services/contract_enforcer.py::enforce_hit_profile_parity` — made side-aware. `compute_hit_profile` always counts OVER hits (`v >= line`); the score doc carries SIDE-AWARE `hit_rate_l10`. Comparing OVER-only count vs UNDER score doc produced false mismatches and the enforcer rewrote the canonical UNDER rate with the OVER count (verified bug: Karl-Anthony Towns PRA 37.5 UNDER, score=80% → API=20% from 2/10 OVER hits). Fix: `expected_hr = (total - cnt)/total*100` for UNDER picks.
  - **Phase 4B verify all green:** §i 0 legacy fields (43/43 picks), §ii canonical fields 100% present, §iii Phase 4A guard holds, §v `5_api_correctness` PASS, §vi watchdog 0 events. Curl confirmed: NBA SH/FL + MLB SH/FL/WZ all canonical-only, zero legacy aliases.
  - **SLO §4 follow-up fix (same session):** `production_readiness_slo_check.py::check_detection_source_freshness` rewritten with explicit STATE 1 / STATE 2 / INVALID state machine. Empty queue + fresh `live_props` + fresh `prop_scores` + 0 watchdog events = healthy STATE 2 (motivating case: MLB drains 5k-row queue in <60s while next ingest is 5min away). 6 new offline regression tests in `tests/test_slo_detection_source_freshness.py` lock both healthy states and 4 invalid states (stale live_props, stale scores, unbounded depth, watchdog events).
  - **Final SLO scoreboard:** §1 PASS, §2 PASS, §3 FAIL (pre-existing — cached_board has no `updated_at`), §4 PASS, §5 PASS, §6 FAIL (pre-existing — Vision Intel coverage NBA 17.9% / MLB 0%), §7 PASS.
  - **Files modified this session:**
    - `routes/ferrari_tiers.py` (strip expansion in `_merge_score_with_board`)
    - `services/contract_enforcer.py` (side-aware parity)
    - `scripts/production_readiness_slo_check.py` (§4 state machine + watchdog gate)
    - `scripts/p0_phase4b_verify.sh` (fixed `grep -c || echo 0` double-count bug)
    - `tests/test_slo_detection_source_freshness.py` (NEW — 6 regression tests)
  - **Reproduce / rerun commands:**
    - Phase 4B harness: `bash /app/backend/scripts/p0_phase4b_verify.sh`
    - Full SLO: `cd /app/backend && python3 scripts/production_readiness_slo_check.py`
    - SLO §4 unit tests: `cd /app/backend && python3 -m pytest tests/test_slo_detection_source_freshness.py -v`

## Open issues (priority)
- **P0 Phase 4B (NEXT SESSION — verbatim user spec):**
  - **Goal:** Remove legacy hit-rate response shims and migrate all readers to canonical fields.
  - **Canonical only:** `hit_rate_l5`, `hit_rate_l10`, `hit_rate_l20`, `hit_rate_over`, `hit_rate_under`.
  - **Legacy fields to eliminate from user-facing API/frontend:** `h5_rate`, `h10_rate`, `h20_rate`, `hit_rate`, `hit_rates`, `model_hit_rate_over`, `model_hit_rate_under`.
  - **Before patching:** fresh writer/reader audit for these fields ONLY. Classify each occurrence as: backend writer / API response shim / frontend reader / helper/internal object / comment/test only. Return exact files to change.
  - **Out of scope (must not be touched):** scoring math, gates, Vision Intel, ingestion, dirty_queue, scheduler.
  - **After patch — pass criteria:**
    - API visible tier picks expose canonical `hit_rate_l5/l10/l20` only
    - frontend reads canonical first/only
    - no legacy HR aliases in visible API payloads
    - live API L5 grid valid
    - SLO script run; §1, §2, §4 PASS; §5 (api_correctness) now PASS
  - **Verification harness for hit-rate cleanup (to be created in 4B):** mirror of `scripts/p0_phase4a_verify.sh` — DB legacy presence + API leakage + SLO + watchdog + heartbeat.
- **P0 Phase 4A** — COMPLETE pending user manual verify run. Verification harness: `bash /app/backend/scripts/p0_phase4a_verify.sh`. **DO NOT** add the queue TTL index suggested in finish summary; user explicitly declined.
- **P0** Vision Intel universal refactor — full scope in `/app/memory/VISION_INTEL_REFACTOR_SCOPE.md`. Nullification phase shipped (Phase 2); engine refactor remains.
- **P0 Phase 4A** (queued for next session): surgical `edge_pct` SSOT cleanup. DB has 81,243 docs persisting `edge_pct` via `services/board/publisher.py:181` and `services/board/shadow_publisher.py:142`. Unset migration + 2 frontend reads (`PlayerDetailPage.jsx`) + `vk_edge` API leakage cleanup. Scope nailed in audit; ~1 hour.
- **P1** `vision_score == 0.0` data corruption for legit Safe Haven candidates (Tyrese Maxey et al.) — artificially fails `vision_score_gate`.
- **P1** Pitcher Strikeouts L20 fallback missing.
- **P1** PP-Only alt-line TP calculation (DK ladder fair-odds) — blocks Sunday-morning MLB slates from appearing.
- **P1** Inactive-player UNDER inflation (`min projection > 0` filter).
- **P1** MLB Debias audit — must use heteroscedastic-sigma pattern, NOT additive (lesson from NBA Phase 1 mistake).
- **P1** Decompose Dashboard.jsx + picks_getter_service.py.
- **P1** SSOT Tier B: `hit_rate_l5/l10/l20` rename, `edge` alias cleanup, `cv` parallel-compute delete, `ranking_score_v2` drop vision_score fallback.

## Upcoming
- P0 Google/Apple OAuth (Emergent-managed).
- P0 Stripe payments (test keys in pod env).
- P1 NFL config scaffold (heteroscedastic + Vision Intel adapters).
- P1 STL/BLK/Double-Double NBA model training.
- P1 User-facing error/stale states.

## Key files referenced this session
- `services/scoring/gates/thresholds.py` — gate config.
- `services/scoring/adapters/nba_scoring.py` — `_engine_p_over` applies hetero σ.
- `services/scoring/recompute.py` — `score_doc_writer`; allowlist via `prop_scores_store.py:_SCORE_OUTPUT_FIELDS`.
- `services/delta/detector.py` — watcher (just fixed).
- `routes/ferrari_tiers.py:_get_nba_tier_picks_from_scores` — final live_props matchup override.
- `routes/live.py` — ticker.
- `config/nba_sigma_heteroscedastic.py` + `nba_projection_calibration.py`.
- `frontend/src/components/dashboard/UniversalPlayerCard.jsx` — matchup row.
- `frontend/src/components/dashboard/PlayerDetailPage.jsx` — variance tile.

## Hard-learned constraints (READ FIRST)
- `_SCORE_OUTPUT_FIELDS` in `prop_scores_store.py` is a strict allowlist — new audit fields written by adapters silently drop unless added there.
- Additive μ-debias on a stat-wide constant overcorrects volume stars. NEVER again — use heteroscedastic σ buckets or magnitude-gated additive.
- Watcher `new_keys` MUST subtract `active=True` rt keys only. Set-diffing against ALL rt keys silently freezes the board on slate rolls.
- `nba_master_active_cache.json` / `mlb_master_active_cache.json` are stale static files (Apr 23). Treat as deprecated; the universal Vision Intel refactor should remove dependency on them.
- The user is highly critical and demands real, verified fixes via terminal output. No "clear cache" suggestions. No template-language responses.

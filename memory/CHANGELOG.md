# Changelog

## 2026-04-19 — Wave 0 Batch 12 plumbing (long-tail sweep + odds_mapping closure)
- Registry reconciliation: `odds_mapping` was **already** present in registry at
  `collection_names.py:70` as per-sport NBA concept. Batch 11 mis-used
  `COLL.shared("odds_mapping")` — correct call is `COLL("odds_mapping","nba")`.
  **No code change to registry needed.**
- Routed 3 deferred refs in `server.py:1316-1318` (boot-time odds_mapping indexes).
- 14 Batch 12 files + server.py routed through `COLL(...)`:
  `services/historical_data_fetcher.py`, `services/mlb_vegas_killer_model.py`,
  `services/optimized_sync_engine.py`, `services/bdl_game_logs_sync_batched.py`,
  `services/photo_storage_service.py`, `services/master_hub_sync.py`,
  `services/vision_ai_service.py`, `services/oracle_apex_service.py` (2nd-pass),
  `services/bdl_game_logs_sync.py`, `services/stats_enrichment_service.py`,
  `services/intel_suite_calculator.py`, `services/probability_score_service.py`,
  `services/mlb_deep_ingestion.py`, `services/ferrari_tier_service.py` (2nd-pass).
- 17 code-level literals removed → 17 `COLL(...)` call-sites added (3 server.py
  + 14 Batch 12). 12 imports added (2 files already had imports from prior batches).
- In-scope hardcoded-ref count in these files: **17 → 0**.
- Regression suite: 80 passed / 1 skipped / 0 failed — matches baseline.
- Live smoke after restart: all 10 endpoints HTTP 200. Boot-time odds_mapping
  index creation (3 calls) executed cleanly via new COLL routing.
  Backend uptime clean. 0 new errors.
- Global broader-scanner residual: **43 → 29 refs across 28 files**.
- Breakdown: 14 runtime (Batch 13 candidates), 13 scripts/* (non-runtime),
  2 `scripts/layer_audit.py` (user excluded), 1 registry self-reference.
- **Wave 0 progress: 56 files plumbed, 93% of baseline refs eliminated.**
- Audit: `/app/memory/wave0_batch12_audit.md`.


## 2026-04-19 — Wave 0 Batch 11 plumbing (second-pass + long-tail)
- 14 files routed through `services/config/collection_names.py::COLL`:
  `server.py` (second-pass), `services/mlb_tier_sorter.py`,
  `services/vegas_killer_model.py` (second-pass),
  `services/forward_testing_service.py`,
  `services/vegas_pro_model.py` (second-pass), `services/mlb_badge_system.py`,
  `services/vegas_regression_model.py` (second-pass),
  `services/bdl_enhanced_data.py` (second-pass),
  `services/mlb_tier_service.py` (second-pass),
  `services/live_injury_micro_sync.py`, `services/bdl_player_badge_service.py`,
  `services/injury_sensor.py`, `routes/vision.py`,
  `repositories/board_repo.py` (second-pass).
- In-scope concepts: `master_hub` (NBA+MLB), `board_cache` (NBA+MLB), `live_props`
  (NBA+MLB), `context_flags` (NBA), `injuries` (shared), `live_scores_cache` (shared).
- 20 code-level literals removed → 20 `COLL(...)` call-sites added. 7 imports added
  (7 files already had import from prior batches).
- **Stop & Report**: `odds_mapping` concept NOT in registry. Per strict-refactor
  rules, did not add registry entry. 3 `odds_api_mapping_master` refs in
  `server.py:1316-1318` deferred pending user approval to add
  `"odds_mapping": "odds_api_mapping_master"` to `_SHARED_COLLECTIONS`.
- Batch 11 in-scope residuals in these files: **20 → 0**.
- Regression suite: 80 passed / 1 skipped / 0 failed — matches baseline.
- Live smoke after restart: NBA + MLB Ferrari, master-hub lookup, live scores,
  vision/status all HTTP 200. Backend uptime clean. 0 new errors.
  (Pre-existing 404 on `/v3/vision/player/{slug}` — `player_router` not mounted
  in `server.py`; NOT a regression from this batch.)
- Global in-scope broader-scanner residual: **66 → 43 refs across 42 files**.
- Deferred refs awaiting registry entry: 3 (`odds_api_mapping_master`).
- Audit: `/app/memory/wave0_batch11_audit.md`.


## 2026-04-19 — Wave 0 Batch 10 plumbing (broader scanner sweep)
- 9 files routed through `services/config/collection_names.py::COLL`:
  `routes/master_hub.py`, `routes/ai_context.py`, `routes/live.py`,
  `routes/cached_data.py`, `services/injury_triggered_rescore.py`,
  `services/board_intelligence_service.py`, `routes/ferrari_tiers.py`,
  `services/team_stats_service.py`, `routes/qa_testing.py`.
- In-scope concepts: `master_hub` (NBA+MLB), `board_cache` (NBA+MLB),
  `ticker_cache` (shared), `injuries` (shared).
- 44 code-level literals removed → 44 `COLL(...)` call-sites added. 7 imports added
  (2 files already had import from prior batches).
- Broader scanner now authoritative (covers `db.X`, `db["X"]`, `_db.X`, `_db["X"]`,
  `engine.db.X`, `self.db[...]` variants).
- Second-pass completed on `board_intelligence_service.py` (4 refs added since Batch 2).
- In-scope hardcoded-ref count in these files: **44 → 0**.
- Regression suite: 80 passed / 1 skipped / 0 failed — matches baseline.
- Live smoke after restart: NBA + MLB Ferrari + master-hub player lookup +
  ai-context + live scores/news + command search all HTTP 200.
  `master-hub/player/name/Luka Doncic` returned real 8-key document via new
  bracket-access COLL routing. Backend uptime clean. 0 new errors.
- Global broader-scanner residual: **107 → 66 refs across 56 files** (41-ref
  reduction, 38% drop). Remaining refs are long-tail (mostly 1–2 per file).
- Audit: `/app/memory/wave0_batch10_audit.md`.


## 2026-04-19 — Wave 0 Batch 9 plumbing (resolvers/repos/routes/utility services)
- 10 files routed through `services/config/collection_names.py::COLL`:
  `services/picks/player_stats_resolver.py`, `advanced_analytics.py`,
  `repositories/player_repo.py`, `repositories/sync_repo.py`,
  `routes/command.py`, `routes/mlb_ripple.py`,
  `services/usage_spike_detector.py`, `services/props_service.py`,
  `services/injury_service.py`, `services/injury_advantage.py`.
- In-scope concepts: `master_hub` (NBA+MLB), `master_roster`, `board_cache`,
  `sync_log` (shared), `injuries` (shared), `live_scores_cache` (shared).
- 13 code-level literals removed → 13 `COLL(...)` call-sites added. 10 imports added.
- First-time routing of bracket-access pattern `_db["mlb_master_hub_2026"]` and
  `db["injuries_normalized"]` — both handled correctly via `COLL(...)`.
- In-scope hardcoded-ref count in these files: **13 → 0**.
- Regression suite: 80 passed / 1 skipped / 0 failed — matches baseline.
- Live smoke after restart: NBA + MLB Ferrari, NBA + MLB player search,
  MLB ripple alerts all HTTP 200. Backend uptime clean. 0 new errors.
- Global code-level residual (apples-to-apples attr-access pattern): 59 → **48 refs**.
- Broader scanner added (now covers `db["..."]` bracket-access and `_db` variable
  patterns that were invisible before) — surfaces 107 total refs across 65 files
  as honest accounting for Batch 10 scope. This is not a regression; it's
  newly-surfaced visibility.
- Audit: `/app/memory/wave0_batch9_audit.md`.


## 2026-04-19 — Wave 0 Batch 8 plumbing (adapter/writer/sync orchestration)
- 9 files routed through `services/config/collection_names.py::COLL`:
  `services/odds_api_service.py`, `services/sync_service.py`,
  `services/badge_resolver.py`, `services/mlb_high_friction_model.py`,
  `services/tier_builder_service.py`, `services/ssot_data_layer.py`,
  `services/sync_orchestration_service.py`, `services/bdl_comprehensive_sync.py`,
  `services/insights_sync_service.py`.
- In-scope concepts: `master_hub` (NBA+MLB), `board_cache`, `live_props` (MLB),
  `events_cache`, `odds_cache`, `sync_log` (shared), `context_flags` (new this batch).
- 18 code-level literals removed → 18 `COLL(...)` call-sites added. 9 imports added.
- `context_flags` concept first used this batch — registry resolves it to
  `nba_context_engine` for NBA sport.
- `_archive_mlb_v1/` not touched per exclusion.
- Out-of-scope refs reported and left: `dg_radar_picks`, `dg_goblin_vault`,
  `dg_front_lines`, `dg_parlay_builder`, `dg_goblin_recon`, `dg_daily_insights`,
  `mlb_historical_logs`, `dg_verification_failures`, `dg_player_data`,
  `dg_trending` (none in registry).
- In-scope hardcoded-ref count in these files: **18 → 0**.
- Regression suite: 80 passed / 1 skipped / 0 failed — matches baseline.
- Live smoke after restart: all 9 NBA + MLB endpoints HTTP 200.
  Backend uptime clean. 0 new errors.
- Global code-level residual: 81 → **59 refs across 47 files**.
- Audit: `/app/memory/wave0_batch8_audit.md`.


## 2026-04-19 — Wave 0 Batch 7 plumbing (MLB pipeline + roster services)
- 8 files routed through `services/config/collection_names.py::COLL`:
  `services/mlb_tier_service.py`, `services/mlb_lineup_ripple_service.py`,
  `services/mlb_master_sync.py`, `services/mlb_oracle_apex_service.py`,
  `services/roster_service.py`, `services/roster_sync_service.py`,
  `services/data_integrity_service.py`, `services/photo_service.py`.
- In-scope concepts: `master_hub` (NBA + MLB), `master_roster` (NBA),
  `board_cache` (NBA + MLB), `live_props` (MLB), `sync_log` (shared).
- 30 code-level literals removed → 30 `COLL(...)` call-sites added. 8 imports added.
- This batch closes the 2 out-of-scope residuals from Batch 6 in
  `data_integrity_service.py` (`master_roster` + `sync_log`).
- Full MLB pipeline now plumbed end-to-end: mlb_master_sync (writer) +
  mlb_tier_service + mlb_oracle_apex_service + mlb_lineup_ripple_service (readers).
- Out-of-scope refs reported and left: `nba_context_engine` in mlb_tier_service
  (not in priority list/registry), `dg_flagged_players`, `dg_verification_failures`
  (not in registry).
- In-scope hardcoded-ref count in these files: **30 → 0**.
- Regression suite: 80 passed / 1 skipped / 0 failed — matches baseline.
- Live smoke after restart: NBA Ferrari endpoints + MLB tier endpoints all HTTP 200,
  MLB endpoints returned 60KB+ payloads (real data through COLL routing).
  Backend uptime clean. 0 new errors.
- Global code-level residual: 103 → **81 refs across 58 files**.
- Audit: `/app/memory/wave0_batch7_audit.md`.


## 2026-04-19 — Wave 0 Batch 6 plumbing (injury/live/social/cache/integrity)
- 5 files routed through `services/config/collection_names.py::COLL`:
  `services/injury_vacuum_service.py`, `services/engines/live_scores_engine.py`,
  `services/engines/social_signal_engine.py`, `services/rolling_cache_manager.py`,
  `services/data_integrity_service.py`.
- In-scope concepts: `star_usage_cache`, `master_hub` (NBA + MLB), `board_cache`
  (NBA + MLB), `injuries` (shared), `live_scores_cache` (shared), `ticker_cache`
  (shared), `ticker_headlines` (shared), `breaking_news_cache` (shared).
- 23 code-level literals removed → 23 `COLL(...)` call-sites added. 5 imports added.
- Cross-driver pattern validated: `db[COLL(...)]` subscript works identically
  on both PyMongo sync (`sync_db`) and Motor async (`self.db`) clients.
- Out-of-scope refs reported and intentionally left: `dg_master_roster` +
  `dg_sync_log` in data_integrity_service.py (concepts not in Batch 6 priority list),
  plus `injury_log`, `vacuum_alerts`, `bdl_injuries`, `dg_social_signals`,
  `dg_player_news_cache`, `dg_verification_failures` (not in registry).
- In-scope hardcoded-ref count in these files: **23 → 0**.
- Regression suite: 80 passed / 1 skipped / 0 failed — matches baseline.
- Live smoke after full restart: all Ferrari endpoints + live-scores + vacuum/updates
  HTTP 200. Backend uptime clean. 0 new errors.
- Global code-level residual (broad pattern incl. MLB variants): 103 refs across 66 files.
- Audit: `/app/memory/wave0_batch6_audit.md`.


## 2026-04-19 — Wave 0 Batch 5 plumbing (intel/enrichment/repos)
- 5 files routed through `services/config/collection_names.py::COLL`:
  `services/market_moves_engine.py`, `services/oracle_apex_service.py`,
  `services/vision_intel_enrichment_service.py`, `services/headshot_service.py`,
  `repositories/board_repo.py`.
- In-scope concepts: `board_cache`, `master_hub`, `injuries` (shared),
  `live_scores_cache` (shared).
- 13 code-level literals removed → 13 `COLL(...)` call-sites added. 5 imports added.
- Out-of-scope refs reported and intentionally left: 2× `dg_live_props`
  (oracle_apex L429 + board_repo L20 — `live_props` not in Batch 5 priority list),
  plus `ferrari_scored`, `oracle_apex_picks` (not in registry).
- In-scope hardcoded-ref count in these files: **13 → 0**.
- Regression suite: 80 passed / 1 skipped / 0 failed — matches baseline.
- Live smoke after restart: all Ferrari endpoints + market-moves read path HTTP 200,
  supervisor clean, 0 new errors in backend.err.log.
- Global code-level residual (broader pattern incl. live_scores_cache/ticker/news):
  **85 refs across 53 files**.
- Audit: `/app/memory/wave0_batch5_audit.md`.


## 2026-04-19 — Wave 0 Batch 4 plumbing (boot/indexes/scheduler/legacy engine)
- 4 files routed through `services/config/collection_names.py::COLL`:
  `server.py`, `scripts/init_database.py`, `routes/scheduler.py`,
  `services/engines/demon_goblin_engine.py`.
- In-scope concepts: `board_cache`, `board_cache_temp`, `master_hub`,
  `master_roster`, `live_props`, `odds_cache`, `events_cache`, `sync_log`,
  `ticker_headlines` (shared).
- 44 code-level literals removed → 44 `COLL(...)` call-sites added. 4 imports added.
- Boot-time index creation block (22 `create_index` calls in `server.py`) fully
  routed through `COLL(...)` — confirmed healthy on full `supervisorctl restart`.
- Response-shape preserved: JSON response keys in `/v3/init-database` left
  unchanged (labels only, not DB access).
- `routes_archive/roster.py` excluded per scope.
- In-scope hardcoded-ref count in these files: **44 → 0**.
- Regression suite: 80 passed / 1 skipped / 0 failed — matches baseline.
- Live smoke after restart: all Ferrari endpoints + `/v3/scheduler-status` HTTP 200,
  scheduler running with 22 jobs, 0 errors in backend.err.log.
- Global code-level residual: 153 → **109 refs across 70 files**.
- Audit: `/app/memory/wave0_batch4_audit.md`.


## 2026-04-19 — Wave 0 Batch 3 plumbing (core pipeline, no renames)
- 3 files routed through `services/config/collection_names.py::COLL`:
  `services/ferrari_tier_service.py`, `services/picks_getter_service.py`,
  `services/cached_board_builder_service.py`.
- In-scope concepts: `board_cache`, `board_cache_temp`, `master_hub`,
  `master_roster`, `events_cache`, `odds_cache`, `sync_log`.
- 18 code-level literals removed → 18 `COLL(...)` call-sites added. 3 imports added.
- Includes atomic `renameCollection` admin command (cached_board_builder L390-391)
  now routed through `COLL(...)` — behavior preserved.
- In-scope hardcoded-ref count in these files: **18 → 0** (docstring/log prose untouched).
- Out-of-scope refs reported and intentionally left: `ferrari_*` legacy collections
  (no registry concept), `nba_context_engine` (excluded from user's priority list),
  `dg_radar_picks/goblin_vault/front_lines/parlay_builder/goblin_recon/player_data/`
  `daily_insights/flagged_players` (not in registry).
- Regression suite: 80 passed / 1 skipped / 0 failed — matches baseline.
- Live smoke: `/api/v3/ferrari/{safe-haven,front-lines,war-zone}` all HTTP 200.
  Backend hot-reloaded cleanly; 0 new errors in supervisor log.
- Global code-level residual count: 170 refs across 75 files (down from ~188 pre-Batch-3).
- Audit: `/app/memory/wave0_batch3_audit.md`.


## 2026-04-19 — Wave 0 Batch 2 plumbing (ingest layer, no renames)
- 5 files routed through `services/config/collection_names.py::COLL`:
  `services/odds_sync_service.py`, `services/sharp_edge_calculator.py`,
  `services/board_intelligence_service.py`, `services/bdl_enhanced_data.py`,
  `services/bdl_stats_calculator.py`.
- In-scope concepts: `live_props`, `master_roster`, `odds_cache`, `master_hub`.
- 9 code-level literals removed → 9 `COLL(...)` call-sites added. 5 imports added.
- In-scope hardcoded-ref count in these files: **9 → 0** (docstring prose untouched).
- Regression suite: 80 passed / 1 skipped / 0 failed — matches baseline.
- Live smoke: `/api/v3/ferrari/{safe-haven,front-lines,war-zone}` all HTTP 200.
- Resolver parity verified: physical names unchanged → zero behavior change.
- Audit: `/app/memory/wave0_batch2_audit.md`.


## 2026-04-19 — Wave 0 Batch 1 plumbing (no renames)
- 9 files routed through `services/config/collection_names.py::COLL`:
  `db/collections.py`, `config/collections.py`, `services/nba_career_service.py`,
  `services/defensive_momentum_service.py`, `services/vegas_killer_model.py`,
  `services/vegas_regression_model.py`, `services/vegas_pro_model.py`,
  `services/context_badge_service.py`, `routes/cached_data.py` (context_engine only).
- Regression suite: 80 passed / 1 skipped / 0 failed (incl. `test_collection_names.py` 61/61).
- Live `/api/v3/ferrari/front-lines?sport=nba` returns HTTP 200 post-restart.
- No renames, no data migrations, no behavior changes. Indirection only.
- Residual scan: `/app/memory/wave0_batch1_residuals.md`.



## 2026-04-19 — Decision-Layer Integrity (Sengun AST 6.5 audit response)
Five ordered fixes after the user-flagged Sengun card exposed systemic
decision-layer contradictions between badges / tile / narrative / direction:

1. **Floor Lock window + tooltip honesty** (`services/intel_suite_calculator.py`)
   - Switched `values[-10:]` → `values[:10]` in both `_generate_scout_badges`
     and `_calculate_stability_index`. `history.2025_season` is stored
     newest-first, so the prior index took the OLDEST 10 games.
   - Floor Lock now requires `hit_rate >= 90` (matches its public tooltip).
     Previously fired on `std<=2.0 AND mean>line` at 70% hit rates.
2. **Canonical projection contract** (`services/intel_suite_calculator.py::_generate_vision_insight`)
   - Narrative now quotes `board_pick.vk_predicted` (the same field the UI
     tile binds to in `UniversalPlayerCard.jsx:508`) instead of
     `lasso.projection`. Lasso projection exposed as a separate
     `lasso_projection` field — never conflated.
3. **Model-disagreement flag**
   - `vision_insight.models_disagree = True` when `|vk−lasso|/line > 0.10`
     and the two models sit on opposite sides of the line. Narrative
     explicitly calls out disagreement instead of writing a one-sided thesis.
4. **PP-anchor direction veto** (`services/scoring/scoring_stack.py`)
   - New `_model_contradicts_anchor(prop, side)` runs inside `compute_tier`
     BEFORE tier dispatch. Rejects an OVER when all three contradict the
     anchor side: `vk_edge<0`, `hit_rate_over<50`, `l10_avg<line` (symmetric
     for UNDER). Sets `tier="unqualified"`,
     `tier_reason="model_contradicts_anchor: …"`.
5. **"Clear Read" → "Model Fit"** (`components/ui/BadgePill.jsx`)
   - Relabelled to precisely reflect the gate (Lasso R² ≥ 0.35 on held-out
     games). Tooltip sentiment changed from "positive / lean in" to
     "neutral / model-fit quality only — NOT a prop recommendation".
6. **War Zone gate review** (`services/scoring/adapters/nba_scoring.py`)
   - Documented comment: war_zone gates stay market-facing; directional
     evidence handled by Fix #4. Separation of concerns enforced in code
     comments; no threshold changes.

**Expected live effect**: on the next scoring pass (next `odds_sync` or
hourly rebuild), the Sengun AST 6.5 OVER pick drops from `war_zone` to
`unqualified` and vanishes from the board. No retroactive mutation of
existing score docs.

**Regression suite added**: `tests/test_decision_layer_sengun.py` (7 tests).
Total passing: 19 (12 pre-existing + 7 new).


## 2026-04-19 — Tier Integrity + Post-Dedup Backfill Verified
- **Bug fix**: player duplicates across a single tier (same player ×N on
  alternate lines or across stat families). Confirmed on NBA Safe Haven
  (Amen Thompson ×3) and MLB War Zone (James Wood ×2).
- **Fix at two levels**:
  - `services/board/reader.py::get_board` — over-fetch `cap*6`, walk sorted
    stream, keep first per normalized `player_name`, stop at `cap` distinct
    players. Reader is the single entry point for NBA boards.
  - `routes/ferrari_tiers.py::_dedupe_picks_by_player` — route-level
    fallback wired into all 5 tier exit points; covers MLB legacy
    collections (`mlb_safe_haven` / `mlb_front_lines` / `mlb_war_zone`)
    that bypass the reader. Ranks by `(vision_score, pp_utility, |edge|)`.
- **Post-dedup backfill audit** (2026-04-19 00:00 UTC):
  | Tier (sport)     | pool rows | distinct | surfaced | reason if <cap          |
  |------------------|----------:|---------:|---------:|-------------------------|
  | NBA safe_haven   |         3 |        1 |        1 | pool exhausted          |
  | NBA front_lines  |         9 |        4 |        3 | 1 × JIT injury (Durant) |
  | NBA war_zone     |         2 |        2 |        2 | pool exhausted          |
  | MLB safe_haven   |        10 |       10 |       10 | FULL                    |
  | MLB front_lines  |        10 |       10 |       10 | FULL                    |
  | MLB war_zone     |        10 |        9 |        9 | 1 × dedup (James Wood)  |
  Invariant `surfaced == min(cap, distinct − injury_excluded)` holds for
  every tier. No hidden exclusions.
- **Regression suite**: `/app/backend/tests/test_tier_integrity.py`
  (8 tests). Total backend regression surface now 13 passing.


## 2026-04-18 — Priority realignment (user directive)

## 2026-04-19 — Step 6 A/B Comparator Isolation + Prop-Scores Hygiene
- **Hygiene pass**: archived 15 stale experimental version_tags (41,793 docs)
  from `nba_prop_scores` → `nba_prop_scores_archive_stale_tags`; same for MLB
  (10,090 docs → `mlb_prop_scores_archive_stale_tags`). Canonical tags
  (`final-nba`, `final-mlb`) are the only writable tags left on the live
  collections. Indexes rebuilt via `ensure_indexes()`.
- **Stray `live` tag plugged**: `write_prop_scores()` (MLB legacy wrapper) now
  writes to `final-mlb` instead of `live`, eliminating the off-window writer.
- **Real-time engine → shadow tag**: `services/board/engine.py` now upserts
  under `{canonical}-rt` (`final-nba-rt` / `final-mlb-rt`). Legacy full-rebuild
  continues writing under the canonical tag. No writer collision on the live
  tag during the 48h window.
- **Drift auditor cross-tag**: `_audit_one_window` now queries BOTH tags per
  ledger entry. Report surfaces `rt_tag`, `legacy_tag`, `rt_present`,
  `legacy_present`, `rt_presence_ratio`, `legacy_presence_ratio`, and each
  divergence sample carries `rt_ledger`, `rt_materialized`, `legacy_current`
  side-by-side.
- **Reader unchanged**: live board readers (ferrari routes, optimized_sync,
  injury_triggered_rescore) stay pinned to `final-{sport}`.
- **Verified**: ledger flushed; direct `recompute_sport(tag=final-nba-rt)`
  wrote 3 docs to the shadow tag (visible under the new `-rt` distribution);
  audit report confirms `rt_presence_ratio=1.0` against `legacy_presence_ratio=0.0`
  for the seeded keys. Regression tests (5) still pass.


- Per user: next priority is Universal Board Migration + Legacy Writer
  Retirement (Step 6). No new features until that lands.
- New file `/app/memory/ROADMAP.md` created with P0/P1/P2 and explicit
  acceptance gates for Step 6.
- 48h observation clock deferred pending decision on blocker 1a (A/B
  comparator race between `write_versioned_scores(mode="replace")` in the
  legacy rebuild and `mode="upsert"` in the real-time engine, both sharing
  `version_tag=final-nba`). Raw evidence captured in ROADMAP.md §1a.



## 2026-04-18 — Canonical Stat-Window Contract (Ferrari h10_rate Clobber Fix)
- **Root cause**: `ferrari_tiers.py:1059-1072` was overwriting `prop["h10_rate"]`
  with `score.hit_rate_over` (L20/p_true-derived, 20-game window). Chart binding
  (`GameLogBarChart` over `game_logs[:10]`) stayed correct while the "L10 Hit"
  tile displayed L20 math under an "L10" label.
- **Evidence trail**: Grayson Allen PTS OVER 7.5 — chart 9/10 = 90%, tile 95%.
  Scope: 3/3 front-lines picks + 3/4 safe-haven picks were affected pre-fix.
- **Fix**: Stopped writing `score.hit_rate_over/under` into `h10_rate`. Model-
  derived rates moved to a separate namespace: `model_hit_rate_over`,
  `model_hit_rate_under`, `model_hit_rate_active`.
- **Invariant guard**: `_assert_canonical_hit_rate_invariant(prop)` + 
  `_guard_board_picks(picks)` installed at the exit of all Ferrari endpoints
  (front-lines, safe-haven, war-zone, oracle-apex). Asserts
  `h{5,10,20}_rate == round(l{5,10,20}_hits / window * 100, 1)`; on mismatch
  logs a `[CANONICAL_GUARD]` warning and auto-corrects from the count field.
- **Regression test**: `/app/backend/tests/test_hit_rate_canonical.py` (5 tests,
  all passing).
- **Files**: `routes/ferrari_tiers.py`, `tests/test_hit_rate_canonical.py`.



## 2026-04-16 — Non-Manual MLB Path Verified + CV Standardization
- Traced full non-manual MLB pipeline end-to-end: `scheduled_safety` → Coordinator → UnifiedPipeline(MLBAdapter) → Atomic Publish → Gemini enrichment
- Raw log trace: run_id `a8fa4aa3`, 2171 props → SH=2, FL=10, WZ=10, 22/22 Gemini enriched in 16.1s
- Fixed CV calculation inconsistency: standardized all `np.std()` calls to use `ddof=1` (sample std dev) across `oracle_apex_service.py`, `intel_suite_calculator.py`
- Files already using `ddof=1`: `vegas_regression_model.py`
- Discovered DB_NAME mismatch: server uses `pick_vision` (from .env), not `propvision`
- Added test endpoint `POST /api/v3/mlb/test-scheduled-sync` for non-manual path verification

## 2026-04-16 — Event-Driven Sync Architecture Complete
- Wired legacy data sync paths to emit BoardEvent coordinator triggers
- MLB sync engine, Ferrari tier service, MLB tier service all emit `scored_data_refresh`
- Closed the "open-door" publish gap
- Market Moves exit-reason classification (prop_removed, displaced_by_higher, etc.)
- 3-part prop identity keys (player|stat|line) for alternate lines

## 2026-04-14 - MLB Deep Ingestion Complete
- Built async 15x parallel ingestion engine
- Ingested 777 active MLB players with 149,989 game logs (3 seasons)
- Fixed dk_odds parameter mismatch in rolling_cache_manager.py

## 2026-04-14 - NBA Deep Ingestion + Advanced Overlay Complete
- NBA async ingestion: 559 players, 112,778 game logs, 181.4s, 0 errors
- Advanced Overlay: 15 metrics merged into every NBA game log

## 2026-04-14 - Automated Feature Discovery + Lasso Prediction Engine
- AutoFE pipeline: 366 engineered features → Lasso L1 selection
- 3 models trained for MLB, NBA

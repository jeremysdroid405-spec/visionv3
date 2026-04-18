# Changelog

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

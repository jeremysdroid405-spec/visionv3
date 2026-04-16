# Changelog

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

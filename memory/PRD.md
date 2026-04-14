# PropVision - Product Requirements Document

## Original Problem Statement
Restructure the React/FastAPI betting app to a 100% Local-First Database Model, integrating multi-sport support (NBA/MLB) and an exact MLB/NBA evaluation system. Implement "Strict Board Lockdown" for Rolling Cache to only enrich live Ferrari tier props. Populate `mlb_master_hub_2026` with 3-year historical BDL data (2023-2025) as the Single Source of Truth, merging all historical logs into the primary player document.

## Core Architecture
- **Frontend**: React with Shadcn/UI components
- **Backend**: FastAPI (Python)
- **Database**: MongoDB
- **ML Models**: XGBoost regression (64 features)
- **Cache**: Rolling Cache Architecture (master_active_cache.json)

## What's Been Implemented

### Gemini Scout Intelligence Engine (COMPLETE - 4/14/2026)
- Ripped out all static f-string templates from intel_suite_calculator.py, rolling_cache_manager.py, ferrari_tiers.py
- Built `gemini_scout_engine.py` with `generate_gemini_scout_intel()` async function
- Gemini Flash (gemini-3-flash-preview) via emergentintegrations LlmChat
- System prompt: "You are a highly analytical NBA/MLB DFS scout... Write exactly two gritty sentences..."
- Strict JSON payload: player, stat, line, lasso_proj, edge, top_drivers, h10_rate, matchup_context
- Concurrent batch processing via asyncio for all ~30 Ferrari Tier props (22.6s for 19 props)
- Fallback: baseline string if LLM fails (so UI never crashes)
- API: vision_intel text overlaid onto Ferrari tier endpoints via enrichment cache

### Vision Intel Suite v2 Refactor (COMPLETE - 4/14/2026)
- Removed `baseline_stats` dependency from IntelSuiteCalculator — all metrics from live game logs
- Lasso v2 wired into NBA JIT enrichment (`_calculate_nba_intel`)
- Opponent resolution from `dg_cached_board` → fixes pace delta and DvP calculations
- Human-readable Lasso driver names via `FEATURE_DISPLAY` mapping
- 10 conditional vision_intel text templates (Smash spot / FADE ALERT / Hot hand / Floor play / Tempo play / etc.)
- Scout badges as string arrays (`hot_streak`, `floor_lock`, `lasso_high_edge`, `high_fidelity_model`, etc.)
- Frontend PropRow updated: Lasso projection bar (green/red edge), confidence tier badge (HI-FI/MOD/HI-VAR), scout badge pills, vision_intel italic text
- Vision Intel Suite modal rendering: Operational Volume, Matchup Analysis, Tempo, Variance, Blowout Risk, Hit Rate Analysis
- 3 models: MLB Hits, MLB Total Bases, NBA Points
- AutoFE: 366 engineered features → Lasso L1 kills to 40 survivors each
- StandardScaler normalization: Projection = Intercept + SUM(beta_i * (x_i - mean_i) / std_i)
- SurvivorFeatureBuilder: computes ONLY the 40 needed features (ix_, delta3_, L{n}_std_)
- Vision Score: (Projection - Line), flags >15% as High Edge
- Auto-fetches live PrizePicks lines from cached board, or accepts `?line=` override
- NBA Points R²=0.486 (HIGH_FIDELITY), MLB R²≈0.03 (HIGH_VARIANCE)
- API: `GET /api/v3/lasso/predict/{sport}/{player}/{stat}?line=X`
- API: `GET /api/v3/lasso/models`

### NBA Master Hub 2026 - 3-Year + Advanced Overlay (COMPLETE - 4/14/2026)
- 559 players, 112,778 game logs (2023: 32,119 / 2024: 38,590 / 2025: 42,069)
- Advanced overlay: usage_pct, true_shooting_pct, off_rating, def_rating, pace, ast_pct, reb_pct, pie, net_rating, eFG_pct, turnover_ratio (100% coverage)
- No-Loss Merge: existing metadata (headshots, badges) preserved

### MLB Master Hub 2026 - 3-Year Deep Ingestion (COMPLETE - 4/14/2026)
- **777 active MLB players** ingested from BDL GOAT Tier API
- **149,989 game logs** across 3 seasons (2023: 42,382 | 2024: 49,706 | 2025: 57,901)
- **Schema v2.0 SSOT** with `history: {2023_season: [...], 2024_season: [...], 2025_season: [...]}`
- **bdl_game_logs** flat array with mapped field names for backward compatibility
- **is_pitcher / is_batter** flags derived from BDL position data
- Async ingestion engine: `asyncio.Semaphore(15)` + `aiohttp` + `bulk_write` every 100 players
- Completed in 257.9s (0.33s/player avg, 0 errors)

### Rolling Cache Architecture v2.0 (COMPLETE - 4/14/2026)
- APEX-ONLY CACHING: Props only cached AFTER enrichment succeeds
- Strict Board Lockdown: Only Ferrari tier props (~30 max) enriched
- dk_odds parameter mismatch FIXED in `_calculate_nba_intel`

### Key DB Collections
| Collection | Docs | Purpose |
|-----------|------|---------|
| mlb_master_hub_2026 | 777 | SSOT - 3yr history per player |
| mlb_cached_board | 191 | Live prop board |
| nba_master_hub_2026 | 559 | NBA player hub |

### API Endpoints
- `GET /api/v3/mlb/player/{player_name}` - Full player data w/ game logs + props
- `GET /api/v3/intel-cache/mlb` - Instant MLB props from JSON cache
- `GET /api/v3/mlb/ferrari/safe-haven|front-lines|war-zone` - Ferrari tier picks

## Pending Tasks

### P1 - High Priority
- Google OAuth integration
- Stripe payments integration

### P2 - Medium Priority
- Wind Tunnel weather API integration
- Refactor frontend Dashboard.jsx
- Refactor ferrari_tiers.py (technical debt)

---
*Last Updated: April 14, 2026*
*MLB SSOT Ingestion Complete - 777 players, 149,989 game logs*

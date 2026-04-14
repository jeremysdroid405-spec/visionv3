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

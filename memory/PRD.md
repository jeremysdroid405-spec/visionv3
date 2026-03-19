# PropVision - Product Requirements Document

## Overview
PropVision is a sports analytics platform for NBA player props, providing data-driven insights for betting decisions.

## Core Architecture

### Data Flow
1. **BDL Sync** (`bdl_comprehensive_sync.py`) - Syncs ALL active NBA players (~548) to `nba_master_hub_2026`
   - Uses GOAT tier batch API: `/season_averages/general` + `/stats`
   - Runs daily at 5 AM and 8 AM EST
   - ~14 seconds for full sync

2. **NBA.com Integration** (NEW) - Official L5/L10/L15/L20 stats via `nba_api`
   - Uses `playerdashboardbylastngames` endpoint
   - Master hub stores both `bdl_id` (BDL) and `nba_id` (NBA.com)
   - Provides pre-calculated last N games averages - no manual calculation needed
   - Endpoints: `/api/v3/master-hub/nba-stats/{bdl_id}`, `/api/v3/master-hub/enrich-nba-stats/{bdl_id}`

3. **Odds API Mapper** (`odds_api_mapper.py`) - Maps player names to bdl_id
   - Rebuilt from master hub on demand
   - 548 mappings in memory
   - Lookup: name → bdl_id → full player data

4. **Cached Board Builder** (`cached_board_builder_service.py`) - Builds props board
   - Uses mapper for player lookups (not legacy name matching)
   - Calculates hit rates from baseline_stats
   - Stores in `dg_cached_board`

5. **Tiers** - War Zone (demons), Safe Haven (goblins), Gauntlet (all)

### Database Schema (MongoDB)
- `nba_master_hub_2026` - Player data with unique indexes on both `bdl_id` AND `nba_id`
- `odds_api_mapping_master` - Name → bdl_id mappings
- `dg_cached_board` - Built board with props and hit rates

### Authentication
- **Master Admin**: `admin@propvision.ai` / `PropVision2026!` (local JWT)
- **Regular Users**: Supabase auth

## Completed Features ✅
- [x] GOAT tier BDL batch sync (2026-03-19)
- [x] Odds API Mapper with bdl_id keys (2026-03-19)
- [x] War Zone hit rate calculation (2026-03-19)
- [x] NBA.com L5/L10 API integration via nba_api (2026-03-19)
- [x] Dual ID system: bdl_id + nba_id in master hub (2026-03-19)
- [x] HTML title updated to "PropVision" (2026-03-19)
- [x] Dual auth system (JWT + Supabase)
- [x] Dashboard scroll position restoration
- [x] Locked picks UI (blur + countdown)
- [x] Mobile responsiveness
- [x] Second daily sync at 5 AM EST

## In Progress
- [ ] None

## Pending Issues
- [ ] P2: 46 unmatched players without nba_id (rookies/two-way players)
- [ ] P3: Remove deprecated components (code cleanup)

## Backlog
- [ ] Google/Apple OAuth
- [ ] Stripe payments
- [ ] Copy Parlay feature
- [ ] Automate distraction/deep_water badges
- [ ] Tooltips for context badges
- [ ] War Zone score breakdown UI

## API Endpoints (New)
- `GET /api/v3/master-hub/nba-stats/{bdl_id}` - Fetch L5/L10/L15/L20 from NBA.com (read-only)
- `POST /api/v3/master-hub/enrich-nba-stats/{bdl_id}` - Persist NBA.com stats to player record

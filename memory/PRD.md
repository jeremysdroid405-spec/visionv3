# PropVision - Product Requirements Document

## Overview
PropVision is a sports analytics platform for NBA player props, providing data-driven insights for betting decisions.

## Core Architecture

### Data Flow
1. **BDL Sync** (`bdl_comprehensive_sync.py`) - Syncs ALL active NBA players (~548) to `nba_master_hub_2026`
   - Uses GOAT tier batch API: `/season_averages/general`
   - Runs daily at 5 AM and 8 AM EST
   - ~14 seconds for full sync

2. **NBA.com Integration** (via `nba_api`) - Official L5/L10/L15/L20 stats
   - Uses `playerdashboardbylastngames` endpoint
   - Master hub stores both `bdl_id` (BDL) and `nba_id` (NBA.com)
   - Provides pre-calculated last N games averages - no manual calculation needed
   - Endpoints: 
     - `GET /api/v3/master-hub/nba-stats/{bdl_id}` - Fetch L5/L10 (read-only)
     - `POST /api/v3/master-hub/enrich-nba-stats/{bdl_id}` - Persist to player
     - `POST /api/v3/master-hub/enrich-all-nba-stats` - Batch enrich players

3. **Odds API Mapper** (`odds_api_mapper.py`) - Maps player names to bdl_id
   - Rebuilt from master hub on demand
   - ~548 mappings in memory

4. **Cached Board Builder** (`cached_board_builder_service.py`) - Builds props board
   - Uses mapper for player lookups (not legacy name matching)
   - Calculates hit rates from baseline_stats (which now has NBA.com L5/L10)
   - Stores in `dg_cached_board`

5. **Adaptive Sync Engine** (`adaptive_sync_engine.py`) - Enriches props with stats
   - Reads L5/L10 from baseline_stats (populated by NBA.com)
   - Falls back to game log calculation if baseline_stats missing
   - Does NOT overwrite L5/L10 if already present

6. **Tiers** - War Zone (demons), Safe Haven (goblins), Gauntlet (all)

### Database Schema (MongoDB)
- `nba_master_hub_2026` - Player data with unique indexes on both `bdl_id` AND `nba_id` (sparse)
- `odds_api_mapping_master` - Name → bdl_id mappings
- `dg_cached_board` - Built board with props and hit_rates

### Hit Rates Structure
In the cached board, each prop has a `hit_rates` object:
```json
{
  "l10_rate": 50,
  "l5_rate": 50,
  "l10_hit_count": 5,
  "l5_hit_count": 2,
  "l5_avg": 31.2,
  "l10_avg": 31.2,
  "season_avg": 28.5
}
```

### Authentication
- **Master Admin**: `admin@propvision.ai` / `PropVision2026!` (local JWT)
- **Regular Users**: Supabase auth

## Completed Features ✅
- [x] GOAT tier BDL batch sync (2026-03-19)
- [x] Odds API Mapper with bdl_id keys (2026-03-19)
- [x] War Zone hit rate calculation (2026-03-19)
- [x] NBA.com L5/L10 API integration via nba_api (2026-03-19)
- [x] Dual ID system: bdl_id + nba_id in master hub (2026-03-19)
- [x] Batch NBA.com enrichment endpoint (2026-03-19)
- [x] Fixed adaptive sync to use baseline_stats L5/L10 (2026-03-19)
- [x] HTML title updated to "PropVision" (2026-03-19)
- [x] Dual auth system (JWT + Supabase)

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

## API Endpoints
- `POST /api/v3/master-hub/sync-bdl-all` - Sync all active players
- `GET /api/v3/master-hub/nba-stats/{bdl_id}` - Fetch L5/L10 from NBA.com
- `POST /api/v3/master-hub/enrich-nba-stats/{bdl_id}` - Enrich single player
- `POST /api/v3/master-hub/enrich-all-nba-stats` - Batch enrich (default 100)
- `POST /api/v3/sync` - Trigger full sync (rebuilds board)
- `GET /api/v3/war-zone` - Get War Zone picks with hit rates

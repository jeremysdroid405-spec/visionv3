# PropVision - Product Requirements Document

## Overview
PropVision is a sports analytics platform for NBA player props, providing data-driven insights for betting decisions.

## Core Architecture

### Data Flow
1. **BDL Sync** (`bdl_comprehensive_sync.py`) - Syncs ALL active NBA players (~548) to `nba_master_hub_2026`
   - Uses GOAT tier batch API: `/season_averages/general` + `/stats`
   - Runs daily at 5 AM and 8 AM EST
   - ~14 seconds for full sync

2. **Odds API Mapper** (`odds_api_mapper.py`) - Maps player names to bdl_id
   - Rebuilt from master hub on demand
   - 548 mappings in memory
   - Lookup: name → bdl_id → full player data

3. **Cached Board Builder** (`cached_board_builder_service.py`) - Builds props board
   - Uses mapper for player lookups (not legacy name matching)
   - Calculates hit rates from baseline_stats
   - Stores in `dg_cached_board`

4. **Tiers** - War Zone (demons), Safe Haven (goblins), Gauntlet (all)

### Database Schema (MongoDB)
- `nba_master_hub_2026` - Player data with `bdl_id` as unique key
- `odds_api_mapping_master` - Name → bdl_id mappings
- `dg_cached_board` - Built board with props and hit rates

### Authentication
- **Master Admin**: `admin@propvision.ai` / `PropVision2026!` (local JWT)
- **Regular Users**: Supabase auth

## Completed Features ✅
- [x] GOAT tier BDL batch sync (2026-03-19)
- [x] Odds API Mapper with bdl_id keys (2026-03-19)
- [x] War Zone hit rate calculation (2026-03-19)
- [x] Dual auth system (JWT + Supabase)
- [x] Dashboard scroll position restoration
- [x] Locked picks UI (blur + countdown)
- [x] Mobile responsiveness
- [x] Second daily sync at 5 AM EST

## In Progress
- [ ] None

## Pending Issues
- [ ] P1: News ticker slow/empty
- [ ] P2: 7 unmatched players (name variations)
- [ ] P3: Update title to "PropVision"
- [ ] P3: Remove deprecated components

## Backlog
- [ ] Google/Apple OAuth
- [ ] Stripe payments
- [ ] Copy Parlay feature
- [ ] Automate distraction/deep_water badges

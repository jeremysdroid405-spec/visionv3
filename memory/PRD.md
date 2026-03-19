# PropVision - Product Requirements Document

## Overview
PropVision is a sports analytics platform for NBA player props, providing data-driven insights for betting decisions.

## Core Architecture

### Data Pipeline (Fixed 2026-03-19)
1. **BDL Sync** - Season averages from GOAT tier batch API
2. **NBA.com Integration** (`nba_api`) - Pre-calculated L5/L10/L15/L20 from `playerdashboardbylastngames`
3. **Dual ID System** - Master hub stores both `bdl_id` and `nba_id` (502/548 matched)
4. **Hit Rate Flow**:
   - `baseline_stats` populated from NBA.com (preferred) or BDL
   - `_flatten_hit_rates_to_props()` flattens nested hit_rates to prop level
   - Frontend reads flat `l5_avg`, `l10_avg`, `h10_rate` from props

### Key Fixes (2026-03-19)
- Fixed `_enrich_player_with_master_hub_stats()` to prefer NBA.com baseline_stats over BDL game logs
- Fixed `/api/v3/player-with-badges/` to read from flat hit_rates structure
- Fixed None comparisons in `intel_suite_calculator.py`
- Fixed `calculate_hit_rate_for_games()` to filter None values
- Added `normalizeHitRate()` in frontend to handle both decimal and percentage formats

### Database Schema
- `nba_master_hub_2026` - Unique indexes on `bdl_id` AND `nba_id`
- `dg_cached_board` - Props with `hit_rates` object
- `odds_api_mapping_master` - Name → bdl_id mappings

### Hit Rates Structure
Props have both nested and flat formats:
```json
{
  "l5_avg": 27.0,
  "l10_avg": 27.0,
  "season_avg": 21.4,
  "h10_rate": 100,
  "hit_rates": {
    "l10_rate": 100,
    "l5_rate": 100,
    "l10_avg": 27.0,
    "l5_avg": 27.0,
    "season_avg": 21.4
  }
}
```

### Authentication
- **Master Admin**: `admin@propvision.ai` / `PropVision2026!`
- **Demo Mode**: Click "DEMO MODE" button on auth page

## Completed Features ✅
- [x] NBA.com L5/L10 integration via nba_api
- [x] Hit rates displaying correctly on Dashboard
- [x] Hit rates displaying correctly on Player Detail page
- [x] War Zone, Safe Haven, Front Lines all working
- [x] Dual ID system (bdl_id + nba_id)
- [x] GOAT tier BDL batch sync
- [x] HTML title "PropVision"

## Pending Issues
- [ ] P2: 46 players without nba_id (rookies/two-way)
- [ ] P3: Deprecated code cleanup

## API Endpoints
- `POST /api/v3/master-hub/sync-bdl-all` - Sync all active players
- `POST /api/v3/master-hub/enrich-all-nba-stats` - Batch enrich NBA.com data
- `GET /api/v3/player-with-badges/{player_name}` - Player detail with hit rates
- `GET /api/v3/war-zone` - War Zone picks
- `GET /api/v3/front-lines` - Front Lines picks
- `GET /api/v3/goblin-vault` - Safe Haven picks

## Backlog
- [ ] Google/Apple OAuth
- [ ] Stripe payments
- [ ] Copy Parlay feature
- [ ] Automate context badges

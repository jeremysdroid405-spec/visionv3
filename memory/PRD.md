# PropVision - Product Requirements Document

## Overview
PropVision is a sports analytics platform for NBA player props, providing data-driven insights for betting decisions.

## Core Architecture

### Data Pipeline (Updated 2026-03-19)

**Data Sources:**
1. **BDL (BallDontLie)** - Season averages ONLY (GOAT tier subscription)
   - Endpoint: `/season_averages/general`
   - ⚠️ Game logs have many DNPs - NOT reliable for L5/L10

2. **NBA.com (nba_api)** - L5/L10/L15/L20 stats (PRIMARY SOURCE)
   - Endpoint: `playerdashboardbylastngames`
   - Pre-calculated, official, accurate
   - ~550 active players

### Scheduled Syncs (EST)

| Time | Job | Description |
|------|-----|-------------|
| 4:00 AM | Daily Full Sync | BDL season averages, injuries, DvP |
| 4:00 AM | NBA Batch 1/5 | 125 players L5/L10 from NBA.com |
| 4:02 AM | NBA Batch 2/5 | 125 players L5/L10 from NBA.com |
| 4:04 AM | NBA Batch 3/5 | 125 players L5/L10 from NBA.com |
| 4:06 AM | NBA Batch 4/5 | 125 players L5/L10 from NBA.com |
| 4:08 AM | NBA Batch 5/5 | 125 players L5/L10 from NBA.com |
| 5:00 AM | Morning Props | Odds/props refresh |
| Sunday 00:00 UTC | Roster Sync | Weekly team mappings |

**Total Coverage:** 625 players (handles all ~550 active)

### Database Schema
- `nba_master_hub_2026` - Unique indexes on `bdl_id` AND `nba_id`
  - `baseline_stats.synced_from`: `bdl_season_avg_plus_nba_l5l10` (good) or `pending_nba_enrichment` (needs sync)
- `dg_cached_board` - Props with `hit_rates` object
- `odds_api_mapping_master` - Name → bdl_id mappings

### Hit Rates Structure
```json
{
  "l5_avg": 5.0,
  "l10_avg": 5.9,
  "season_avg": 6.2,
  "h10_rate": 60,
  "hit_rates": {
    "l10_rate": 60,
    "l5_rate": 40,
    "l10_avg": 5.9,
    "l5_avg": 5.0
  }
}
```

### Authentication
- **Master Admin**: `admin@propvision.ai` / `PropVision2026!`
- **Demo Mode**: Click "DEMO MODE" button

## API Endpoints

### Sync Endpoints
- `POST /api/v3/sync` - Rebuild cached board
- `POST /api/v3/sync-bdl` - BDL + NBA.com full sync
- `POST /api/v3/sync-nba-l5l10?limit=125` - Manual NBA.com batch enrichment
- `POST /api/v3/master-hub/enrich-nba-stats/{bdl_id}` - Single player enrichment

### Data Endpoints
- `GET /api/v3/war-zone` - War Zone picks
- `GET /api/v3/front-lines` - Front Lines picks
- `GET /api/v3/goblin-vault` - Safe Haven picks
- `GET /api/v3/player-with-badges/{name}` - Player detail with hit rates

## Completed Features ✅
- [x] NBA.com L5/L10 as PRIMARY source (2026-03-19)
- [x] 5 staggered NBA.com syncs at 4:00-4:08 AM (2026-03-19)
- [x] Fixed bad BDL L5/L10 data (cleared 392 players)
- [x] Hit rates displaying correctly
- [x] War Zone, Safe Haven, Front Lines working
- [x] Dual ID system (bdl_id + nba_id)

## Known Issues
- ~46 players without `nba_id` (rookies/two-way)
- NBA.com API can timeout - staggered syncs handle retries

## Backlog
- [ ] Google/Apple OAuth
- [ ] Stripe payments
- [ ] Code cleanup

# PropVision - Product Requirements Document

## Overview
PropVision is a sports analytics platform for NBA player props, providing data-driven insights for betting decisions.

## Core Architecture

### Data Pipeline (Updated 2026-03-20)

**Data Sources:**
1. **BDL (BallDontLie)** - Season averages + Game Logs (GOAT tier subscription)
   - Endpoint: `/season_averages/general` - Season stats
   - Endpoint: `/stats` - **GAME-BY-GAME VALUES** (CRITICAL for hit rates)
   - ⚠️ Must filter DNPs (0 minute games)

2. **NBA.com (nba_api)** - L5/L10/L15/L20 averages
   - Endpoint: `playerdashboardbylastngames`
   - Pre-calculated averages (no game values)
   - ~550 active players

3. **The Odds API** - Player props (DFS markets)
   - **PRIMARY: Underdog** (as of 2026-03-20)
   - Region: `us_dfs` (Daily Fantasy Sports)
   - ⚠️ PrizePicks removed from API as of 2026-03-20

### Odds API Data Source Change (CRITICAL - 2026-03-20)

**Issue:** PrizePicks data no longer available from The Odds API.

**Resolution:** Switched to **Underdog** as primary DFS data source.

**Files Updated:**
- `/app/backend/adaptive_sync_engine.py` - Changed bookmaker filter from `prizepicks` to `underdog`
- `/app/backend/services/picks_getter_service.py` - Updated queries to use flat document structure, filter for upcoming games only

### Probability Score System (NEW - 2026-03-19)

**Comprehensive Pick Ranking** - Picks are now sorted by `probability_score`:

```
probability_score = base_hit_rate + dvp_modifier + badge_modifier + line_modifier
```

**Components:**
- **Base Hit Rate**: L10 × 0.6 + L5 × 0.4
- **DvP Modifier**: -8% to +8% based on opponent defensive rank
  - Elite Defense (1-5): -8%
  - Poor Defense (26-30): +8%
- **Badge Modifier**: -5% to +5% per badge
  - Positive: home_cookin (+5%), revenge (+4%), locked_in (+4%)
  - Negative: gassed (-5%), jet_lag (-4%), distraction (-3%)
- **Line Modifier**: Based on gap between line and L5 average

**Files:**
- `/app/backend/services/probability_score_service.py` - Scoring logic
- `/app/backend/services/picks_getter_service.py` - Uses prob_score in War Zone, Safe Haven, Front Lines

### Hit Rate Calculation (CRITICAL FIX - 2026-03-20)

**Problem Solved:** Hit rates were showing incorrect values (e.g., 90% when actual was 60%) due to:
1. **Wrong season data**: BDL sync was using `CURRENT_SEASON = 2024` (2024-25 playoffs) instead of `2025` (2025-26 season)
2. **Wrong field mappings**: Hit rate calculation looked for `l10_values` in `baseline_stats` instead of extracting from `bdl_game_logs`
3. **Date field mismatch**: Sorting used `game_date` but `bdl_game_logs` uses `date`
4. **Stat field mismatch**: 3PM mapping used `tptfgm` but `bdl_game_logs` uses `fg3m`

**Solution (2026-03-20):** 
1. **Fixed season**: Updated `/app/backend/services/bdl_game_logs_sync.py` to use `CURRENT_SEASON = 2025`
2. **Fixed data source**: Updated `/app/backend/services/cached_board_builder_service.py`:
   - `_load_stats_map()` now includes `bdl_game_logs` from master hub
   - `_create_matched_player()` passes `bdl_game_logs` to player dict
   - `_add_prop_to_player()` extracts game values from `bdl_game_logs` for per-line hit rate calculation
3. **Fixed field mappings**: 
   - Date sorting: `x.get("date", "") or x.get("game_date", "")`
   - 3PM: Uses `fg3m` 
   - TO: Uses `turnover`

**Data Flow:**
```
BDL /stats endpoint (season=2025)
    ↓
nba_master_hub_2026.bdl_game_logs
    ↓
cached_board_builder_service._load_stats_map() 
    ↓
_add_prop_to_player() extracts values per stat type
    ↓
Calculates L5/L10 hit rates against prop line
    ↓
dg_cached_board.props[].hit_rates
```

**Verification:**
- Jamal Murray REB 3.5 Over: L5=60% (3/5), L10=60% (6/10) ✓

### Scheduled Syncs (EST)

| Time | Job | Description |
|------|-----|-------------|
| 4:00 AM | Daily Full Sync | BDL season averages, injuries, DvP |
| 4:00 AM | NBA Batch 1/5 | 125 players L5/L10 from NBA.com |
| 4:02 AM | NBA Batch 2/5 | 125 players L5/L10 from NBA.com |
| 4:04 AM | NBA Batch 3/5 | 125 players L5/L10 from NBA.com |
| 4:06 AM | NBA Batch 4/5 | 125 players L5/L10 from NBA.com |
| 4:08 AM | NBA Batch 5/5 | 125 players L5/L10 from NBA.com |
| 4:20 AM | Context Badges | Badge sync and enrichment |
| **4:25 AM** | **BDL Game Logs Sync** | **2025-26 season game-by-game stats for hit rates** |
| 5:00 AM | Morning Props | Odds/props refresh |
| Sunday 00:00 UTC | Roster Sync | Weekly team mappings |

**Total Coverage:** 625 players (handles all ~550 active)

### Database Schema
- `nba_master_hub_2026` - Unique indexes on `bdl_id` AND `nba_id`
  - `baseline_stats.PTS.l10_values`: `[14, 27, 21, 19, 20, ...]` (REQUIRED for hit rates)
  - `baseline_stats.PTS.l5_avg`: 20.2
  - `baseline_stats.PTS.l10_avg`: 19.1
- `dg_cached_board` - Props with calculated `hit_rates` object
- `odds_api_mapping_master` - Name → bdl_id mappings

### Hit Rates Structure
```json
{
  "l5_avg": 20.2,
  "l10_avg": 19.1,
  "season_avg": 21.4,
  "h5_rate": 20,
  "h10_rate": 10,
  "hit_rates": {
    "l10_rate": 10,
    "l5_rate": 20,
    "l10_hit_count": 1,
    "l5_hit_count": 1
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
- `POST /api/v3/master-hub/enrich-game-values?limit=200` - BDL game values enrichment
- `POST /api/v3/master-hub/enrich-player-game-values/{bdl_id}` - Single player game values

### Data Endpoints
- `GET /api/v3/war-zone` - War Zone picks
- `GET /api/v3/front-lines` - Front Lines picks
- `GET /api/v3/goblin-vault` - Safe Haven picks
- `GET /api/v3/player-with-badges/{name}` - Player detail with hit rates

## Completed Features ✅
- [x] **DvP Pagination Fix** (2026-03-19) - Fixed BDL API pagination to fetch all 30 teams
- [x] **AI Summary DvP Fix** (2026-03-19) - Vision AI summaries now include actual DvP rank data
- [x] Hit rate calculation fixed with `l10_values` (2026-03-19) 
- [x] BDL game logs integration for game-by-game values (2026-03-19)
- [x] 4:10 AM BDL game values sync added to scheduler (2026-03-19)
- [x] Fixed `l5_values` extraction (was using last 5, now first 5 of sorted array)
- [x] NBA.com L5/L10 as PRIMARY source for averages
- [x] 5 staggered NBA.com syncs at 4:00-4:08 AM
- [x] War Zone, Safe Haven, Front Lines working correctly
- [x] Dual ID system (bdl_id + nba_id)
- [x] Probability score system for comprehensive pick ranking
- [x] Context badge population (home_cookin, jet_lag, locked_in)
- [x] Daily ticker sync (news + scores) at 4:15 AM EST
- [x] Vision Intel Suite percentage display bug fixed

### DvP Service Details
- **File:** `/app/backend/services/dvp_service.py`
- **Fix:** Added cursor-based pagination loop to `_fetch_bdl_defensive_stats()`
- **Result:** All 30 NBA teams now included (Spurs was missing before)
- **Endpoint:** BDL `/nba/v1/team_season_averages/general?type=opponent`

### AI Vision Summary DvP Fix
- **File:** `/app/backend/services/vision_summary_service.py`
- **Fix:** Added `dvp_rank` and `dvp_friction` parameters to `generate_pick_summary()`
- **Result:** AI summaries now accurately reflect opponent defensive strength
- **Before:** AI would hallucinate matchup quality (e.g., "favorable" when facing elite defense)
- **After:** AI uses actual DvP rank data to correctly describe matchup difficulty

## Known Issues
- ~46 players without `nba_id` (rookies/two-way)
- NBA.com API can timeout - staggered syncs handle retries

## Completed (2026-03-23)
- [x] News ticker animation optimization (P2) - Updated to use translate3d, will-change, backface-visibility for GPU acceleration. Reduced news ticker duration from 120s to 60s for better readability.
- [x] Deprecated code cleanup (P2) - Added deprecation notice to `_convert_stats_to_rankings()` in dvp_service.py

## Backlog
- [ ] Add tooltips for context badges (P1)
- [ ] Add UI for War Zone score breakdown (P1)
- [ ] Google/Apple OAuth
- [ ] Stripe payments
- [ ] Automate distraction/deep_water badges

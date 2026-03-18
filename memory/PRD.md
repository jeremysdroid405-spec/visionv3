# PickVision AI - Product Requirements Document

## Original Problem Statement
Build a sports betting analytics application (PickVision AI) that provides:
1. **War Zone**: High-risk, high-reward "DEMON" picks using composite scoring
2. **Safe Haven**: Conservative "GOBLIN" picks with high consistency
3. **Most Popular Bets**: Volume-based popular bets (uses synthetic score due to API limitations)
4. **Vision Intel Suite**: Context badges and advanced analytics
5. **Parlay Builder**: AI-generated parlay combinations

## Core Architecture
- **Frontend**: React + TanStack Query + Tailwind CSS + Shadcn UI
- **Backend**: FastAPI + MongoDB
- **Data Sources**: 
  - **BallDontLie API** (PRIMARY) - Official season averages, injuries, advanced stats
  - The Odds API (via emergentintegrations) - Betting lines
  - ESPN API - Breaking news

## BDL API Endpoints Used
| Endpoint | Purpose | Status |
|----------|---------|--------|
| `/season_averages` | Official season stats | ✅ Active |
| `/stats` | Game logs for L5/L10 | ✅ Active |
| `/players/active` | Player ID mapping | ✅ Active |
| `/player_injuries` | Injury reports | ✅ Active |
| `/stats/advanced` | PIE, Net Rating | ✅ Active |
| `/lineups` | Starting lineups | ⏳ Pending (need game_id) |

## Data Model
- `dg_cached_board`: Player-centric documents with props arrays
- `nba_master_hub_2026`: Master player data vault with:
  - `bdl_raw_stats`: Raw official stats from BDL /season_averages
  - `baseline_stats`: Transformed stats with season_avg (official), L5/L10 (calculated)
  - `bdl_game_logs`: Individual game box scores
  - `advanced_stats`: PIE, Net Rating
- `bdl_player_mapping`: Name → BDL ID mappings (537 players)
- `bdl_injuries`: Current injury reports
- `nba_context_engine`: Context flags for badges

## What's Implemented (March 2026)

### Completed Features
- [x] War Zone with composite scoring
- [x] Safe Haven (Goblin Vault)
- [x] Most Popular Bets (synthetic popularity)
- [x] Vision Intel Suite with 10 context badges
- [x] Parlay Builder (The Gauntlet, The Shield, The Strike)
- [x] BDL Comprehensive Sync - uses OFFICIAL season averages
- [x] BDL Player ID Mapping (537 active players)
- [x] BDL Injuries Sync (25 injuries tracked)
- [x] BDL Advanced Stats (PIE, Net Rating)
- [x] Daily sync scheduler at 4 AM EST (8 steps)
- [x] Intel Search deduplication fix
- [x] L5/L10 stats consistency fix - all calculation functions now sort game logs by date (Dec 2025)
- [x] Team/Opponent display fix - shows player's actual team (from master hub) and correct opponent (Dec 2025)
- [x] Injury data display - Combined ESPN + BDL injury sources, `is_injured` flag on picks (Dec 2025)
- [x] Gassed badge enhanced - Now checks for back-to-back AND heavy minutes (38+) (Dec 2025)
- [x] **BDL API Game Log Fix** (Dec 2025):
  - Increased `fetch_player_game_logs` limit from 15 to 100 games
  - Added sorting by date (most recent first) - BDL returns oldest first by default
  - Added DNP filtering for L5/L10/hit rate calculations (excludes 0-minute games)
  - Fixed `runDailySync()` to use BDL instead of Tank01
- [x] **Tank01 Complete Purge** (Dec 2025): All Tank01 endpoints and references removed from codebase

### Context Badges
- Live from BDL injuries: `deep_water` (injury)
- Live from context engine: `jet_lag`, `revenge`, `legal_noise`, `milestone`
- Needs data sources: `gassed`, `distraction`, `pay_day`, `altitude`, `market_sharp`

### API Endpoints
- `POST /api/v3/sync-bdl` - Full BDL player sync
- `POST /api/v3/sync-bdl-mapping` - Sync player ID mappings
- `POST /api/v3/sync-injuries` - Sync injury reports
- `POST /api/v3/sync-advanced-stats` - Sync PIE/ratings
- `GET /api/v3/injuries` - Get current injuries
- `GET /api/command/profile/{name}` - Player profile with stats

## Prioritized Backlog

### P1 - High Priority
- [ ] Display injury badges in UI
- [ ] Add gassed badge (from game schedule analysis)

### P2 - Medium Priority
- [ ] Add lineups when BDL game schedule is synced
- [ ] Fix route name conflict (`/api/v3/player-with-badges`)
- [ ] Delete deprecated UI components

### P3 - Future
- [ ] Add tooltips for context badges
- [ ] Show War Zone composite score breakdown
- [ ] Implement Stripe payments
- [ ] Add "Copy Parlay" feature

## Technical Notes
- **Stats Source**: BDL `/season_averages` for official stats (DO NOT calculate)
- **Player IDs**: Always use BDL ID for lookups (faster than name search)
- **Injuries**: Synced from BDL + ESPN, stored in `bdl_injuries`
- **Advanced Stats**: PIE, Net Rating from BDL `/stats/advanced`
- **Game Logs**: BDL API returns games in ASCENDING order (oldest first). Always sort by date descending before using.
- **DNP Filtering**: Games with 0 minutes are excluded from L5/L10 calculations. Use `_filter_played_games()` helper.

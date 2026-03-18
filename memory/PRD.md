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
  - **BallDontLie API** (PRIMARY) - Official season averages via `/season_averages` endpoint
  - The Odds API (via emergentintegrations) - Betting lines
  - nba_api - Supplementary stats

## Data Model
- `dg_cached_board`: Player-centric documents with props arrays
- `nba_master_hub_2026`: Master player data vault with:
  - `bdl_raw_stats`: Raw official stats from BDL /season_averages
  - `baseline_stats`: Transformed stats with season_avg (official), L5/L10 (calculated)
  - `bdl_game_logs`: Individual game box scores
- `nba_context_engine`: Context flags for badges
- `dg_game_schedule`: Game schedules for adaptive sync

## What's Implemented (March 2026)

### Completed Features
- [x] War Zone with composite scoring
- [x] Safe Haven (Goblin Vault)
- [x] Most Popular Bets (synthetic popularity)
- [x] Vision Intel Suite with 10 context badges
- [x] Parlay Builder (The Gauntlet, The Shield, The Strike)
- [x] BDL Comprehensive Sync - uses OFFICIAL season averages
- [x] Daily sync scheduler at 4 AM EST
- [x] Manual sync endpoint `/api/v3/sync-bdl`
- [x] Intel Search deduplication fix

### Data Sources
- **Season Averages**: Official from BDL `/season_averages` (NOT calculated)
- **L5/L10 Averages**: Calculated from BDL game logs
- **Games Played**: Official from BDL

### Bug Fixes This Session
- Fixed Intel Search duplicate results
- Fixed baseline_stats using wrong source (was calculating instead of using official)
- Fixed games_played showing wrong count
- Integrated BDL sync into daily scheduler

## Prioritized Backlog

### P0 - Critical
- [ ] Fix adaptive sync type error: `unsupported operand type(s) for +: 'int' and 'str'`

### P1 - High Priority
- [ ] Handle BDL "Player not found" cases (add name aliases)
- [ ] Populate remaining context badges with live data

### P2 - Medium Priority
- [ ] Fix route name conflict (`/api/v3/player-with-badges`)
- [ ] Delete deprecated UI components
- [ ] Add "Last Updated" timestamp

### P3 - Future
- [ ] Add tooltips for context badges
- [ ] Show War Zone composite score breakdown
- [ ] Implement Stripe payments
- [ ] Add "Copy Parlay" feature

## Key API Endpoints
- `POST /api/v3/sync-bdl` - Manual BDL sync
- `GET /api/command/profile/{player_name}` - Player profile with stats
- `GET /api/command/search?query={q}` - Intel search
- `GET /api/v3/war-zone` - Demon picks
- `GET /api/v3/most-popular-bets` - Popular bets

## Technical Notes
- **Stats Source**: BDL `/season_averages` for official stats (DO NOT calculate)
- **Synthetic Popularity**: Bet volume is synthetic (API limitation)
- **Authentication**: Demo mode bypass; real auth pending

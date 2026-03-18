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
- **Data Sources**: The Odds API (via emergentintegrations), BallDon'tLie API, nba_api

## Data Model
- `dg_cached_board`: Player-centric documents with props arrays
- `nba_context_engine`: Context flags for badges (jet_lag, revenge, legal_noise, etc.)
- `nba_master_hub_2026`: Master player data vault
- `dg_game_schedule`: Game schedules for adaptive sync

## What's Implemented (March 2026)

### Completed Features
- [x] War Zone with composite scoring (L5 avg, H10 hit rate, anchor line comparison)
- [x] Safe Haven (Goblin Vault) with strict safety filters
- [x] Front Lines tactical plays
- [x] Most Popular Bets (synthetic popularity score)
- [x] Vision Intel Suite with 10 context badges
- [x] Parlay Builder (The Gauntlet, The Shield, The Strike)
- [x] Adaptive Sync Engine for real-time odds polling
- [x] Daily data sync scheduler (4 AM EST)
- [x] DvP and team pace data integration
- [x] Player detail modal with full analytics

### Context Badges (10 total)
- Live data: `jet_lag`, `revenge`, `legal_noise`, `milestone`
- Needs data sources: `gassed`, `distraction`, `pay_day`, `deep_water`, `altitude`, `market_sharp`

### Bug Fixes This Session
- Fixed IndentationError in picks_getter_service.py (orphaned code block removed)
- Fixed TypeError in stat calculation functions (added safe type conversion)

## Prioritized Backlog

### P1 - High Priority
- [ ] Populate remaining context badges with live data sources
- [ ] Add player injury data to Operational Volume calculation

### P2 - Medium Priority
- [ ] Fix route name conflict (`/api/v3/player-with-badges` workaround)
- [ ] Delete deprecated UI components (PickCard, PlayerCard, TacticalPlayerCard)
- [ ] Add "Last Updated" timestamp to dashboard

### P3 - Low Priority / Future
- [ ] Add tooltips for context badges
- [ ] Show War Zone composite score breakdown in UI
- [ ] Add "Copy Parlay" feature
- [ ] Implement Stripe payments
- [ ] Integrate real Google/Apple OAuth

## Technical Notes
- **Synthetic Popularity**: The Odds API doesn't provide bet volume data, so "Most Popular" uses a synthetic score based on player rank
- **Data Schema**: Player-centric documents in `dg_cached_board` with props arrays (critical for correct data flow)
- **Authentication**: Currently uses demo mode bypass; real auth pending

## Key Files
- `/app/backend/services/picks_getter_service.py`: Core business logic
- `/app/backend/routes/cached_data.py`: Player details and Vision Intel Suite API
- `/app/backend/adaptive_sync_engine.py`: Real-time odds polling
- `/app/frontend/src/pages/Dashboard.jsx`: Main dashboard UI
- `/app/frontend/src/hooks/useLiveOdds.js`: TanStack Query hooks

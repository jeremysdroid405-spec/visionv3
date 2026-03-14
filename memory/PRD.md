# PickVision - NBA Player Prop Dashboard

## Overview
PickVision is a high-performance NBA Player Prop Dashboard with a "military tech" aesthetic. The application delivers AI-driven betting insights by identifying "Demons" (high-payout props) and "Goblins" (safer props).

## Core Architecture

### Data Pipeline (Single Source of Truth)
```
nba_master_hub_2026 (SSOT)
        ↓
odds_api_mapping_master (V4 Mapper)
        ↓
_build_cached_board() in demon_goblin_engine.py
        ↓
dg_cached_board → dg_demon_radar → dg_goblin_vault → dg_goblin_recon
```

### Tech Stack
- **Frontend:** React 18 with Shadcn/UI components
- **Backend:** FastAPI (Python 3.11)
- **Database:** MongoDB
- **External APIs:** The Odds API V4, Tank01, BallDontLie, Google Gemini

## What's Been Implemented

### 2026-03-14: V4 Odds Master Mapping
- Created `odds_api_mapper.py` module for permanent player name → ID mapping
- Created `odds_api_mapping_master` collection (534 players)
- Updated `_build_cached_board()` to use mapper instead of name-based lookups
- Added 5 new API endpoints for mapper operations

### 2026-03-14: Frontend Refactoring (80% reduction)
- Created `DemonGoblinDashboardRefactored.jsx` (~350 lines)
- Created reusable `PlayerCard.jsx` and `ParlayCard.jsx` components
- Created `GlobalUtilities.js` library
- New dashboard accessible at `/v4/demo`

### 2026-03-14: Data Sync & Integrity
- Fixed 5-pick Goblin parlay bug
- Fortified sync process with comprehensive error handling
- Implemented player photo injection (534 players with locked headshots)
- Created `board_intelligence_engine.py` for automated syncs

### 2026-03-13: NBA Master Hub (SSOT)
- Created `nba_master_hub.py` as single source of truth
- All 534 active players with permanent headshot URLs
- Daily sync scheduler (4:00 AM ET)

## Key Collections
- `nba_master_hub_2026` - Master player data (534 players)
- `odds_api_mapping_master` - Odds API name → player_id mapping
- `dg_cached_board` - Enriched player prop data
- `dg_demon_radar` - Top 10 demon picks
- `dg_goblin_vault` - Top 10 safe picks
- `dg_goblin_recon` - Pre-built goblin parlays

## API Endpoints

### Odds Mapper (NEW)
- `GET /api/v3/odds-mapper/stats`
- `GET /api/v3/odds-mapper/lookup/{odds_api_name}`
- `POST /api/v3/odds-mapper/lookup-batch`
- `POST /api/v3/odds-mapper/rebuild`
- `GET /api/v3/odds-mapper/player-id/{player_id}`

### Data Sync
- `POST /api/v3/sync`
- `POST /api/v3/board-intel/primary-sync`
- `POST /api/v3/board-intel/delta-refresh`
- `POST /api/v3/board-intel/early-bird`

### Data Retrieval
- `GET /api/v3/demon-radar`
- `GET /api/v3/goblin-vault`
- `GET /api/v3/goblin-recon`
- `GET /api/v3/hydrated-board`

## Prioritized Backlog

### P0 - Critical
- **Stripe Integration & Authentication** - User's next priority
  - Subscription tiers (Free/Pro)
  - Checkout flow
  - Webhook handling

### P1 - High Priority
- **Finalize Frontend Refactor**
  - Replace `DemonGoblinDashboardOptimized.js` with refactored version
  - Update `App.js` routing
  - Delete deprecated files

- **Fix Deployment Blocker**
  - Parameterize hardcoded `MONGO_URL`
  - Ensure production environment variable injection

### P2 - Medium Priority
- Complete SSOT backend integration (purge old lookup functions)
- Pro Tier feature gating
- Copy Parlay button

### P3/P4 - Future
- Mobile bottom navigation
- T-Minus live countdown timer
- Real Google/Apple OAuth (currently placeholders)

## Known Issues
- Old dashboard (`DemonGoblinDashboardOptimized.js`, 4500+ lines) still in use on main route
- `MONGO_URL` hardcoded in `backend/.env` (deployment blocker)
- Google/Apple auth buttons are placeholders

## File Structure
```
/app
├── backend/
│   ├── odds_api_mapper.py         # NEW: V4 mapping module
│   ├── demon_goblin_engine.py     # Core analytics engine
│   ├── board_intelligence_engine.py
│   ├── nba_master_hub.py          # SSOT module
│   └── server.py
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   │   ├── DemonGoblinDashboardRefactored.jsx  # NEW (refactored)
│   │   │   └── DemonGoblinDashboardOptimized.js    # OLD (to be removed)
│   │   ├── components/dashboard/
│   │   │   ├── PlayerCard.jsx     # NEW
│   │   │   └── ParlayCard.jsx     # NEW
│   │   └── lib/GlobalUtilities.js # NEW
└── memory/
    └── PRD.md
```

## Environment Variables Required
- `MONGO_URL` - MongoDB connection string
- `DB_NAME` - Database name (default: test_database)
- `ODDS_API_KEY` - The Odds API key
- `TANK01_API_KEY` - Tank01 RapidAPI key
- `BDL_API_KEY` - BallDontLie API key
- `EMERGENT_LLM_KEY` - For AI insights
- `GOOGLE_API_KEY` - Google Gemini API key

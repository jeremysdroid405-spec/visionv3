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
dg_cached_board → dg_radar_picks (War Zone) → dg_goblin_vault → dg_goblin_recon
```

### Tech Stack
- **Frontend:** React 18 with Shadcn/UI components
- **Backend:** FastAPI (Python 3.11)
- **Database:** MongoDB
- **External APIs:** The Odds API V4, Tank01, BallDontLie, Google Gemini

## What's Been Implemented

### 2025-12: P0 Database Configuration Fix
- Fixed hardcoded `MONGO_URL` and `DB_NAME` in `backend/.env`
- Removed quotes from env values (can cause parsing issues in some environments)
- Standardized default DB_NAME to `test_database` across all backend files:
  - `ai_context_engine.py`
  - `board_intelligence_engine.py`
  - `nba_master_hub.py`
- Backend now uses `os.environ['MONGO_URL']` (fails fast if missing) vs `os.environ.get()` (silent fallback)

### 2026-03-15: Unified 3-Tier Card UI
- Created `UniversalPickCard` component as single template for all 30 picks
- Deleted legacy `VaultCard` and `FrontLinesCard` components
- Only visual differences: `colorTheme` prop (red/amber/green) and `emblem` prop (fire/bullet/gem)
- **War Zone**: Red theme + Fire emblem (🔥) + labels: ON FIRE, HOT, WARM
- **Front Lines**: Amber theme + Bullet emblem (SVG ammunition graphic) + labels: ELITE, STRONG, SOLID
- **Safe Haven**: Green theme + Gem emblem (💎) + labels: FORTRESS, DIAMOND, VAULT, SAFE
- Consistent card layout, fonts, margins, padding across all sections

### 2026-03-15: THE FRONT LINES Section Complete
- Implemented "THE FRONT LINES" mid-tier picks section
- Backend: `_build_front_lines()` generates 10 "mild" alternates (5-18% gap from standard)
- Uses "God-Tier" 4-Pillar scoring formula:
  - Pillar 1: Base Consistency (50%)
  - Pillar 2: Vegas Implied Probability (20%)
  - Pillar 3: DvP Matchup (15% - placeholder)
  - Pillar 4: AI Context Score (15%)
- Frontend: `FrontLinesSwipeSection` with amber/yellow theme
- Features: Bullet ranking system (6 bullets = top 2, down to 2 bullets = 9-10)
- Shows DEMON (red) or GOBLIN (green) badge per pick based on `is_demon` flag
- API: `GET /api/v3/front-lines`

### 2026-03-14: AI Context Engine Created
- New standalone service `ai_context_engine.py` using Google Gemini
- Evaluates player news and generates `ai_context_score` (0.0-1.0)
- Injects scores into `nba_master_hub_2026` collection
- APIs: `/api/v3/ai-context/run`, `/api/v3/ai-context/status`, `/api/v3/ai-context/evaluate-player/{player_name}`

### 2026-03-14: App-wide Renaming
- Renamed "Demon Radar" to "THE WAR ZONE"
- Renamed app to "PickVision AI"
- Removed "Goblin" branding from "THE SAFE HAVEN" UI

### 2026-03-14: Frontend Refactor Finalized
- Consolidated routes in App.js - main dashboard at `/dashboard` and `/demo`
- Removed legacy `DemonGoblinDashboard.js` file (31KB)
- Kept `DemonGoblinDashboardOptimized.js` as production dashboard (all features intact)
- Kept `DemonGoblinDashboardRefactored.jsx` for future lightweight version
- Legacy routes (`/v3`, `/v3-legacy`, `/v4/demo`) redirect to main routes

### 2026-03-14: Renamed "Demon Radar" to "War Zone"
- Updated all backend code (`demon_goblin_engine.py`, `server.py`)
- Changed API endpoint from `/api/v3/demon-radar` to `/api/v3/war-zone`
- Updated all frontend components and services
- CSS class renamed from `.demon-radar-section` to `.war-zone-section`
- Updated Auth.js feature card title

### 2026-03-14: Functionality-Preserving Logic Clean-Up
- Created `/src/services/DataService.js` (370 lines) - unified data fetching
- Removed direct axios calls, replaced with modular service
- NO hardcoded name-matching (backend OddsApiMapper handles this)
- Preserved all UI toggles, sorting, and Parlay Builder logic
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
- `nba_master_hub_2026` - Master player data (534 players) with `ai_context_score`
- `odds_api_mapping_master` - Odds API name → player_id mapping
- `dg_cached_board` - Enriched player prop data
- `dg_radar_picks` - War Zone top 10 demon picks
- `dg_goblin_vault` - Top 10 safe picks (God-Tier 4-Pillar scored)
- `dg_front_lines` - Top 10 mid-tier "mild" picks (5-18% gap)
- `dg_goblin_recon` - Pre-built goblin parlays

## API Endpoints

### Odds Mapper
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
- `GET /api/v3/war-zone` (formerly demon-radar)
- `GET /api/v3/goblin-vault`
- `GET /api/v3/front-lines` (NEW - mid-tier mild alternates)
- `GET /api/v3/goblin-recon`
- `GET /api/v3/hydrated-board`

### AI Context Engine
- `POST /api/v3/ai-context/run` - Run full context analysis
- `GET /api/v3/ai-context/status` - Check engine status
- `POST /api/v3/ai-context/evaluate-player/{player_name}` - Evaluate single player

## Prioritized Backlog

### P0 - Critical (COMPLETE)
- ✅ **THE FRONT LINES Section** - Mid-tier picks with 4-Pillar scoring

### P1 - High Priority
- **Fix Deployment Blocker** - Parameterize hardcoded `MONGO_URL` in `backend/.env`
- **Real DvP Data** - Replace placeholder in 4-Pillar formula with actual Defense vs Position stats
- **Stripe Integration & Authentication** - User subscriptions
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
- Backend refactor: Split `demon_goblin_engine.py` (7500+ lines) into modules
- Frontend refactor: Extract components from `DemonGoblinDashboardOptimized.js` (4800+ lines)
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

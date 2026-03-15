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

### Backend Modular Architecture (New)
```
/backend
├── config/              # Centralized configuration
│   └── settings.py      # DB, API keys, constants, DVP rankings
├── services/            # Business logic services (9,400+ lines extracted)
│   ├── picks_getter_service.py  # War Zone, Goblin Vault, Front Lines, Most Popular Bets
│   ├── cached_board_builder_service.py  # Board building & tier construction
│   ├── odds_api_service.py      # External odds API calls
│   ├── stats_api_service.py     # BallDontLie stats fetching
│   ├── tank01_service.py        # Injury & news data
│   ├── data_integrity_service.py # Verification & NAJI safeguard
│   ├── stats_enrichment_service.py # Multi-source stats enrichment
│   ├── odds_sync_service.py     # Odds sync orchestration
│   ├── sync_orchestration_service.py # Full/delta sync (created, needs integration)
│   ├── dvp_service.py           # DvP matchup calculation
│   ├── parlay_service.py        # Matrix & DFS compliance
│   ├── parlay_builder_service.py # Parlay construction
│   ├── tier_builder_service.py  # Tier building logic
│   ├── photo_service.py         # Player photos
│   ├── roster_service.py        # Roster management
│   ├── props_service.py         # Props handling
│   ├── sync_service.py          # Sync utilities
│   ├── data_scraper.py          # External API fetching
│   └── social_scout.py          # Social signals & sentiment
├── routes/              # API route handlers
│   ├── picks.py         # War Zone, Safe Haven, Front Lines
│   ├── parlays.py       # Parlay builder endpoints
│   ├── board.py         # Player board & search
│   ├── sync.py          # Sync operations
│   ├── intel.py         # AI briefings & insights
│   └── board_intel.py   # Primary sync operations
├── server.py            # Main entry (middleware, startup)
└── demon_goblin_engine.py # Core engine class (~2,443 lines)
```

### Tech Stack
- **Frontend:** React 18 with Shadcn/UI components
- **Backend:** FastAPI (Python 3.11)
- **Database:** MongoDB
- **External APIs:** The Odds API V4, Tank01, BallDontLie, Google Gemini

## What's Been Implemented

### 2026-03-15: Backend Engine Recovery & Continued Extraction (Phase 12)
- **Recovery:** Restored corrupted `demon_goblin_engine.py` from commit `79209ff`
- **Engine Reduction:** 2,707 → 2,443 lines (**264 lines extracted, 10% additional reduction**)
- **Cumulative Reduction:** 8,252 → 2,443 lines (**~70% total reduction**)
- **New Extraction:**
  - `get_most_popular_bets()` (267 lines) → `PicksGetterService`
- **PicksGetterService Updated:** Now 663 lines (includes Most Popular Bets functionality)
- **All API Endpoints Verified Working:**
  - `/api/v3/status` ✅
  - `/api/v3/war-zone` ✅
  - `/api/v3/goblin-vault` ✅
  - `/api/v3/front-lines` ✅
  - `/api/v3/most-popular-bets` ✅

### 2026-03-15: Backend Engine Deconstruction (Phase 11 - Odds Sync Service)
- **Engine Reduction:** 2,885 → 2,705 lines (**180 lines extracted, 6% additional reduction**)
- **Cumulative Reduction:** 8,252 → 2,705 lines (**~67% total reduction**)
- **New Service Created:**
  - `OddsSyncService` (244 lines): Main sync orchestration with callbacks
- **Methods Proxied in Engine:**
  - `sync_odds_to_mongo()` → OddsSyncService (callback-based delegation)
- **Total Services:** 8,806 lines of modular, reusable code
- **Test Results:** ALL 114 TESTS PASSED (full regression verified)

### 2026-03-15: Backend Engine Deconstruction (Phase 11 - Sync Orchestration Service)
- **Engine at:** 2,705 lines (**~67% total reduction from 8,252**)
- **New Service Created:**
  - `SyncOrchestrationService` (350 lines): Full sync and delta sync orchestration (CREATED, pending integration)
- **Total Services:** 9,156 lines of modular, reusable code
- **Test Results:** All API endpoints verified working
- **Note:** The SyncOrchestrationService was created but integration was deferred to avoid file corruption

### 2026-03-15: Backend Engine Deconstruction (Phase 10 - Stats Enrichment Service)
- **Engine Reduction:** 3,313 → 2,885 lines (**428 lines extracted, 13% additional reduction**)
- **Cumulative Reduction:** 8,252 → 2,885 lines (**~65% total reduction**)
- **New Services Created:**
  - `DataIntegrityService` (181 lines): Verification logging, integrity status, NAJI safeguard
  - `StatsEnrichmentService` (466 lines): Multi-source stats fetching (BDL, Tank01, NBA.com)
- **Methods Proxied in Engine:**
  - `_log_verification_failure()` → DataIntegrityService
  - `get_data_integrity_status()` → DataIntegrityService
  - `verify_player_roster_match()` → DataIntegrityService
  - `_enrich_props_with_stats()` → StatsEnrichmentService
  - `_fetch_player_season_stats()` → StatsEnrichmentService
  - `_get_bdl_player_id()` → StatsEnrichmentService
  - `_fetch_nba_api_stats()` → StatsEnrichmentService
  - `_fetch_tank01_player_stats()` → StatsEnrichmentService
- **Total Services:** 8,561 lines of modular, reusable code
- **Test Results:** All API endpoints verified working

### 2026-03-15: Backend Engine Deconstruction (Phase 9 - Picks Getter Service)
- **Engine Reduction:** 3,823 → 3,432 lines (**391 lines extracted, 10% additional reduction**)
- **Cumulative Reduction:** 8,252 → 3,432 lines (**~58% total reduction**)
- **New Service Created:**
  - `PicksGetterService` (407 lines): All tier data fetching (War Zone, Goblin Vault, Front Lines, Parlay Builder, Goblin Recon, Cached Board)
- **Methods Proxied in Engine:**
  - `get_war_zone()` → PicksGetterService
  - `get_goblin_vault()` → PicksGetterService
  - `get_front_lines()` → PicksGetterService
  - `get_parlay_builder()` → PicksGetterService
  - `get_goblin_recon()` → PicksGetterService
  - `get_cached_board()` → PicksGetterService
  - `get_cached_player()` → PicksGetterService
- **Helper Methods Removed:**
  - `_add_player_insights()` - moved to PicksGetterService
  - `_clean_object_ids()` - moved to PicksGetterService
- **Total Services:** 7,913 lines of modular, reusable code
- **Test Results:** All API endpoints verified working

### 2026-03-15: Backend Engine Deconstruction (Phase 8 - Stats & Tank01 Services)
- **Engine Reduction:** 4,196 → 3,823 lines (**373 lines extracted, 9% additional reduction**)
- **Cumulative Reduction:** 8,252 → 3,823 lines (**~54% total reduction**)
- **New Services Created:**
  - `StatsApiService` (283 lines): BallDontLie API integration, hit rate calculations
  - `Tank01Service` (293 lines): Injury and news fetching with caching
- **Methods Proxied in Engine:**
  - `search_bdl_player()` → StatsApiService
  - `fetch_player_season_stats()` → StatsApiService
  - `calculate_hit_rates()` → StatsApiService
  - `_extract_l10_values()` → StatsApiService
  - `fetch_injuries()` → Tank01Service
  - `fetch_news()` → Tank01Service
  - `get_player_injury_status()` → Tank01Service
- **Total Services:** 7,505 lines of modular, reusable code
- **Test Results:** All API endpoints verified working

### 2026-03-15: Backend Engine Deconstruction (Phase 7 - Odds API & Cached Board Services)
- **Engine Reduction:** 5,049 → 4,196 lines (**853 lines extracted, 17% additional reduction**)
- **Cumulative Reduction:** 8,252 → 4,196 lines (**~49% total reduction**)
- **New Services Created:**
  - `CachedBoardBuilderService` (522 lines): Centralized board building from props
  - `OddsApiService` (328 lines): The Odds API interactions, prop classification
- **Methods Proxied in Engine:**
  - `_build_cached_board()` → CachedBoardBuilderService
  - `_build_cached_board_legacy()` → CachedBoardBuilderService
  - `fetch_todays_events()` → OddsApiService
  - `fetch_prizepicks_odds()` → OddsApiService
  - `fetch_standard_odds()` → OddsApiService
  - `extract_prizepicks_props()` → OddsApiService
- **Dead Code Removed:** ~230 lines of unreachable legacy code after proxy conversions
- **Total Services:** 6,927 lines of modular, reusable code
- **Test Results:** All API endpoints verified working

### 2026-03-15: Backend Engine Deconstruction (Phase 5 - Parlay Builder Service)
- **Engine Reduction:** 7,509 → 5,105 lines (**2,404 lines extracted, 32% reduction**)
- **New ParlayBuilderService (809 lines):**
  - Big Money Builder: High-probability demon parlays
  - Goblin Recon: High-consistency goblin parlays
  - PrizePicks 2-Team Rule compliance
  - Game correlation and diversification logic
  - 2-6 pick parlay generation with payout calculations
- **Dead Code Removed:**
  - `_evaluate_front_lines_prop()` - 216 lines (replaced by TierBuilderService)
  - `_build_parlay_builder()` - ~400 lines (delegated to service)
  - `_build_goblin_recon()` - ~350 lines (delegated to service)
- **Test Results:** All 5 API endpoints verified working (Status, War Zone, Goblin Vault, Front Lines, Parlay Builder)

### 2026-03-15: Backend Engine Deconstruction (Phase 4 - Tier Builder Service)
- **Engine Reduction:** 7,509 → 6,238 lines (**1,271 lines extracted, 16.9% reduction**)
- **New TierBuilderService (855 lines):**
  - Complete 4-Pillar scoring implementation for all tiers
  - War Zone: `build_war_zone()` - high-ceiling demon plays
  - Safe Haven: `build_goblin_vault()` - high-consistency goblin plays
  - Front Lines: `build_front_lines()` - balanced mix with 50/50 split
  - All scoring formulas: ceiling consistency, Vegas implied, DvP matchup, AI context
- **Methods Proxied in Engine:**
  - `_build_war_zone()` → TierBuilderService
  - `_build_goblin_vault()` → TierBuilderService
  - `_build_front_lines()` → TierBuilderService
- **Total Services:** 5,241 lines of modular, reusable code
- **Total Repositories:** 708 lines of database abstraction
- **Test Results:** All 3 tier endpoints verified working (10 picks each)

### 2026-03-15: Backend Engine Deconstruction (Phase 3 - Repository Pattern & Service Proxies)
- **Engine Reduction:** 7,509 → 6,649 lines (**860 lines extracted, 11.5% reduction**)
- **Repository Layer Created (708 lines):**
  - `base.py` (122 lines): Abstract base repository class
  - `picks_repo.py` (110 lines): War Zone, Goblin Vault, Front Lines data access
  - `board_repo.py` (131 lines): Cached board operations
  - `player_repo.py` (155 lines): Player/roster data access
  - `sync_repo.py` (121 lines): Sync log operations
- **New Services Created:**
  - `roster_service.py` (579 lines): Master roster sync, player team lookups, stats caching
  - `photo_service.py` (564 lines): ESPN/NBA CDN headshots, photo sync operations
  - `props_service.py` (513 lines): Prop classification, scoring (War Zone/Goblin/Front Lines)
  - `sync_service.py` (476 lines): Tier building orchestration, parlay generation
- **Services Total:** 4,385 lines of modular, reusable code
- **Methods Proxied to Services:**
  - `sync_master_roster()` → RosterService
  - `sync_player_stats()` → RosterService  
  - `sync_player_photos()` → PhotoService
  - `sync_active_players_with_photos()` → PhotoService
  - `get_team_from_master_roster()` → RosterService
  - `get_cached_player_stats()` → RosterService
  - `flag_unknown_player()` → RosterService
  - `get_photo_and_team_from_master_roster()` → PhotoService
- **Test Results:** 38/38 tests passed (iteration_16.json)
- **All Endpoints Verified:** Status, War Zone, Goblin Vault, Front Lines, Parlay Builder

### 2026-03-15: Backend Engine Deconstruction (Phase 2 - Comprehensive)
- **Archive:** `demon_goblin_engine.py` (8,252 lines) backed up to `/backend/legacy_archive/demon_goblin_engine.backup`
- **Engine Reduction:** 8,252 → 7,491 lines (**761 lines extracted, 9.2% reduction**)
- **Services Created/Updated (~1,968 lines total):**
  - `stats_service.py` (221 lines): Hit rates, heat/safety/bullet levels, volatility
  - `insights_service.py` (372 lines): AI summaries, confidence, pace, usage bump
  - `parlay_service.py` (402 lines): Correlated parlays, probability, payout
  - `utils_service.py` (263 lines): Team names, player names, photo URLs, odds conversion
  - `dvp_service.py` (95 lines): Defense vs Position calculations
  - `data_scraper.py` (298 lines): API fetching with backoff
  - `social_scout.py` (275 lines): Social signal analysis
- **Config Centralized (365 lines):**
  - `TEAM_PACE`, `HIGH_USAGE_PLAYERS`, `DVP_RANKINGS`, `TEAM_LOGOS`, `NBA_PLAYER_IDS`
- **Proxied Methods (20+):**
  - `calculate_hit_rates()`, `calculate_heat_level()`, `calculate_safety_level()`
  - `calculate_volatility()`, `get_team_pace()`, `calculate_pace_factor()`
  - `calculate_usage_bump()`, `generate_insight_summary()`, `calculate_confidence_rating()`
  - `calculate_dvp_modifier()`, `get_dvp_label()`, `fetch_with_backoff()`
  - `normalize_team_name()`, `sanitize_player_name()`, `create_composite_key()`
  - `get_player_photo_url()`, `_build_correlated_parlay()`, `_calculate_parlay_probability()`
- **All Endpoints Verified:** War Zone, Goblin Vault, Front Lines, Parlay Builder, Cached Props, Board Intel

### 2026-03-15: P0 PickCard UI Enhancement - L5/L10/Season/AI Confidence
- **Backend Fix:** Updated `get_war_zone()`, `get_goblin_vault()`, and `get_front_lines()` methods in `demon_goblin_engine.py`
  - Changed from `ai_confidence` to `ai_confidence_rating` for frontend compatibility
  - Added fallback calculation: `ai_confidence_rating = int(pillar_4_context * 100)` when no daily_insights exist
- **Frontend Fix:** Added missing helper functions to `PickCard.jsx`:
  - `getHitRateColor(rate)` - Color codes hit rates (green ≥80%, yellow ≥60%, orange ≥40%, red <40%)
  - `getConfidenceColor(confidence)` - Color codes AI confidence (green ≥80%, purple ≥60%, yellow ≥40%, red <40%)
  - `getConfidenceGradient(confidence)` - Gradient styling for progress bar
- **PickCard now displays:**
  - L5 hit rate percentage + count (e.g., "100% - 5/5")
  - L10 hit rate percentage + count (e.g., "90% - 9/10")
  - Season average with visual comparison to line
  - AI Confidence meter with color-coded progress bar
- **Tested:** All 3 endpoints verified, 25+ PickCard components validated via testing agent

### 2025-12: P0 Database Configuration Fix
- Fixed hardcoded `MONGO_URL` and `DB_NAME` in `backend/.env`
- Removed quotes from env values (can cause parsing issues in some environments)
- Standardized default DB_NAME to `test_database` across all backend files:
  - `ai_context_engine.py`
  - `board_intelligence_engine.py`
  - `nba_master_hub.py`
- Backend now uses `os.environ['MONGO_URL']` (fails fast if missing) vs `os.environ.get()` (silent fallback)

### 2026-03-15: Parlay Modal Refactor & Highlight Fix
- **Refactored ExpandedParlayView** to use `UniversalPickCard` instead of custom `ParlayPickCard`
  - Removed ~280 lines of duplicate code
  - File reduced from 5308 lines to 5034 lines
  - Parlay modal now uses same card component as main dashboard
- **Fixed Highlight Feature Bug**: Clicking a player from parlay modal now correctly:
  - Navigates to player detail page
  - Auto-expands the category containing the selected prop
  - Applies `emerald-glow` (goblin) or `beacon-glow` (demon) CSS animation
  - Shows "VISION PICK" badge on highlighted row
  - Bug was: `handlePlayerClick()` default params were overwriting highlight values

### 2026-03-15: Modular Architecture Refactor (Phase 1)
- **Created modular file structure:**
  - `/src/logic/matrixEngine.js` (252 lines) - Parlay matrix and DFS validation engine
  - `/src/hooks/useDFSData.js` (365 lines) - Central data fetching hook with Demon validation
  - `/src/components/dashboard/PickCard.jsx` (297 lines) - Universal pick card component
  - `/src/components/dashboard/ParlayTicket.jsx` (158 lines) - Parlay ticket component
  - `/src/components/dashboard/SectionContainer.jsx` (248 lines) - Section wrapper components
  - `/src/pages/Dashboard.jsx` (606 lines) - New complete controller
- **Dead code eliminated:**
  - Removed unused `ScoutingBadge` component (26 lines)
  - Removed unused `RadarCard` alias
  - Removed unused state: `syncedAt`, `parlayData`, `reconData` and their setters
- **Data integrity:** Picks missing required fields are now filtered before reaching parlay matrix

### 2026-03-15: Backend Modular Refactor
- **Created service-oriented architecture:**
  - `/backend/config/settings.py` (161 lines) - Centralized config, DB, API keys, DVP rankings
  - `/backend/services/dvp_service.py` (95 lines) - DvP matchup calculation
  - `/backend/services/parlay_service.py` (294 lines) - Matrix & DFS compliance
  - `/backend/services/data_scraper.py` (298 lines) - External API fetching
  - `/backend/services/social_scout.py` (275 lines) - Social signals & sentiment
- **Created modular route handlers:**
  - `/backend/routes/picks.py` (112 lines) - War Zone, Safe Haven, Front Lines
  - `/backend/routes/parlays.py` (68 lines) - Parlay builder endpoints
  - `/backend/routes/board.py` (146 lines) - Player board & search
  - `/backend/routes/sync.py` (146 lines) - Sync operations
  - `/backend/routes/intel.py` (118 lines) - AI briefings & insights
  - `/backend/routes/board_intel.py` (100 lines) - Primary sync operations
- **Total modular code: 1,865 lines** (extracted from 8,000+ line engine)
- **API endpoints preserved** - No breaking changes to frontend

### 2026-03-15: P1 Tasks - PlayerDetailPage & Real DvP Data
- **PlayerDetailPage Connected:**
  - Created `/src/components/dashboard/PlayerDetailPage.jsx` (400+ lines)
  - Full prop ladder with category accordions (PTS, AST, REB, 3PM, BLK, STL, combos)
  - Demon/Goblin icon badges on each prop row
  - Line values, direction, L10/L5 hit rates displayed
  - Highlight support with beacon-glow/emerald-glow animations
  - Back button navigation to dashboard
  - Expand All / Collapse All functionality
- **Real DvP Data Implemented:**
  - Added `DVP_RANKINGS_2024_25` dictionary with NBA team defensive rankings by stat category
  - `calculate_dvp_modifier(opponent_team, stat_type)` converts rank (1-30) to modifier (0.0-1.0)
  - DvP labels: FAVORABLE (0.7+), NEUTRAL (0.4-0.7), TOUGH (<0.4)
  - Combo stats (PRA, P+R, P+A, R+A) use average of component stat rankings
  - Picks now include `dvp_modifier`, `dvp_label`, `opponent_team` in response data
- **Backend Enhancement:**
  - Opponent team calculated from home_team/away_team in _build_cached_board()
  - get_cached_player() reads from cached_board first for latest opponent data

### 2026-03-15: Complete Architectural Decoupling (Phase 2)
- **Monolith decommissioned:**
  - Moved 5,000-line `DemonGoblinDashboardOptimized.js` to `/src/legacy_archive/dashboard_monolith.backup`
  - Changed extension to `.backup` so bundler ignores it
- **App.js rewired:**
  - Primary route `/dashboard` now uses new `Dashboard.jsx`
  - All legacy routes redirect to new dashboard
- **useDFSData.js enhanced:**
  - Added `filterValidPicks()` with specific Demon attribute validation
  - Logs "DATA MISMATCH" warnings for drops (e.g., `is_demon=true` but no `demon_line`)
  - Filters picks BEFORE matrix engine receives them
- **CSS Consolidated:**
  - Created `/src/styles/components.css` with all animations and section glows
  - Removed inline `<style>` blocks from components
- **Final file counts:**
  - Logic layer: 252 lines
  - Hooks layer: 520 lines (including use-toast)
  - Dashboard components: 1,333 lines
  - Centralized styles: 726 lines
  - Main controller: 606 lines
  - **Total active code: ~3,437 lines** (vs 5,000+ in monolith)

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
- Google/Apple auth buttons are placeholders
- DvP rankings are hardcoded (DVP_RANKINGS_2024_25) - needs manual update each season

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

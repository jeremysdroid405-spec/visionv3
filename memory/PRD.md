# PropVision AI - Product Requirements Document

## Original Problem Statement
Build a local-first betting intelligence app restructuring React/FastAPI to integrate Vegas Killer ML models into the Prop Board. Establish cascading tier distribution (Safe Haven, Front Lines, War Zone) strictly gated by Hit Rate, CV, and ML Edge/Probability, using DraftKings odds as the separator. Integrate Gemini 3.1 Pro as "Vision Intel Layer" for composite scoring. Support multi-sport expansion (NBA + MLB).

## Core Architecture
```
/app
├── backend/
│   ├── routes_archive/              # 9 archived legacy route files
│   ├── routes/                      # Active route files (cleaned)
│   │   ├── core_v3.py, board.py, tiers.py, scheduler.py
│   │   ├── ferrari_tiers.py, vacuum.py, vision.py
│   │   └── __init__.py              # Route registration (cleaned)
│   ├── services/
│   │   ├── vision_intel_service.py  # Batched Gemini intel processing
│   │   ├── oracle_apex_service.py   # 3-Gate Qualification checks
│   │   ├── ferrari_tier_service.py  # Tier routing + Vision Intel (Sport-Aware)
│   │   ├── optimized_sync_engine.py # Sport-Exclusive Sync Engine
│   │   ├── odds_api_service.py, odds_sync_service.py
├── frontend/src/
│   ├── context/SportContext.jsx     # Global sport state manager
│   ├── components/dashboard/SportSwitcher.jsx # NBA/MLB dropdown
│   ├── pages/Dashboard.jsx
│   ├── hooks/useLiveOdds.js
│   ├── components/dashboard/
```

## Key Technical Concepts
- **Sport-Exclusive Architecture**: Isolated data pipelines per sport with collection prefixes (nba_ vs mlb_)
- **Locked State**: Prevents cross-sport data corruption during sync operations
- **3-Gate System:** HR >= 80%, CV <= 0.35, VK Edge/Prob thresholds per tier
- **DK Classification:** Safe Haven (DK <= -250), Front Lines (-249 to +199), War Zone (>= +200)
- **Vision Intel:** Batched Gemini 3.1 Pro API calls for composite scoring
- **No Fall-Throughs:** Props failing tier gates are discarded, not cascaded

## Sport-Specific Collections
### NBA Collections
- `nba_master_hub_2026`: BDL player stats
- `dg_cached_board`: Enriched props with intel
- `ferrari_safe_haven`, `ferrari_front_lines`, `ferrari_war_zone`: Tier collections

### MLB Collections (Prefixed)
- `mlb_master_hub_2026`: MLB player stats
- `mlb_cached_board`: MLB enriched props
- `mlb_ferrari_safe_haven`, `mlb_ferrari_front_lines`, `mlb_ferrari_war_zone`: MLB tier collections

## API Endpoints
- `GET /api/v3/ferrari/safe-haven?sport=nba|mlb`
- `GET /api/v3/ferrari/front-lines?sport=nba|mlb`
- `GET /api/v3/ferrari/war-zone?sport=nba|mlb`
- `POST /api/v3/ferrari/rebuild?sport=nba|mlb` (Sport-Exclusive sync)
- `GET /api/v3/player-with-badges/{name}`

## 3rd Party Integrations
- Gemini AI (google-genai, Model: gemini-3.1-pro-preview) - User API Key
- BallDontLie (BDL) API - User API Key
- The Odds API - User API Key

---

## Completed Work (April 2026)

### Session 6 - MLB VK Historical Backfill & BDL Date Fix (April 10, 2026)
- [x] **FIXED P0 BLOCKER**: MLB BDL stats returning `None` for dates
  - Root Cause: MLB BDL API returns flat structure (game_id at root) vs NBA (nested game object with date)
  - Solution: Added `_build_mlb_game_cache()` to fetch games separately and create date lookup
  - Solution: Added `_get_mlb_game_date()` and `_get_mlb_opponent()` cache lookup methods
- [x] **Fixed MLB stat field mappings** in `_transform_stat_to_game_log()`:
  - `rbi` → `rbis` (BDL uses short names)
  - `k` → `strikeouts`
  - `hr` → `home_runs`
  - `bb` → `walks`
  - `ip` → `innings_pitched`
  - `p_k` → `pitcher_strikeouts`
  - `p_bb` → `pitcher_walks`
  - `p_hits` → `hits_allowed`
  - `er` → `earned_runs`
- [x] **Built MLB VK Historical Backfill Service** (`/app/backend/services/mlb_vk_historical_backfill.py`):
  - Fetches 5 seasons (2021-2026) of historical data from BDL
  - Applies time-decaying weights (2026=1.0, 2021=0.5)
  - Calculates weighted baselines for all MLB stats
  - Stores in `mlb_historical_logs` and updates `mlb_master_hub_2026.vk_baselines`
- [x] **New VK Endpoints**:
  - `POST /api/v3/mlb/vk-backfill?seasons=2021,2022,2023,2024,2025,2026` - Run historical backfill
  - `GET /api/v3/mlb/vk-baselines/{player_name}` - Get player's weighted baselines
- [x] **Tested**: VK backfill for 2026 season completed successfully:
  - 2,884 games cached
  - 20,000 stats fetched
  - 3,252 player logs collected
  - 774 players with VK baselines calculated
- [x] All 17 backend tests passed (iteration_37.json)

### Session 5 - Multi-Sport Database Schema & Universal APIs (April 10, 2026)
- [x] Created `/app/backend/config/db_config.py` with collection prefixes for each sport
- [x] Implemented `get_collection_name(base_name, sport)` helper function
- [x] Updated `/api/v3/ferrari/*` endpoints to use `?sport=` parameter

**Universal Odds Sync (`/app/backend/services/universal_odds_sync.py`):**
- [x] NBA: `basketball_nba` → PTS, REB, AST, PRA → `dg_live_props`
- [x] MLB: `baseball_mlb` → Strikeouts, Walks, Hits Allowed, Hits, Total Bases, RBIs, Runs, Stolen Bases → `mlb_live_props`
- [x] Tested: NBA (9 events, 655 props), MLB (18 events, 1972 props)

**BDL Universal Sync (`/app/backend/services/bdl_universal_sync.py`):**
- [x] NBA: `https://api.balldontlie.io/nba/v1/stats` → `nba_master_hub_2026`
- [x] MLB: `https://api.balldontlie.io/mlb/v1/stats` → `mlb_master_hub_2026`
- [x] **STRICT cursor-based pagination** using `next_cursor` from meta object
- [x] Circuit breaker to prevent DB wipes on low results
- [x] Tested: NBA players (537), MLB players (777)

**MLB Headshot Sync (`/app/backend/services/mlb_headshot_sync.py`):**
- [x] **Phase 1: ID Discovery** - MLB Search API (`https://statsapi.mlb.com/api/v1/people/search`)
- [x] **Phase 2: Headshot Fetch** - MLB CDN + ESPN fallback
- [x] Local storage: `/app/frontend/public/images/mlb_headshots/{id}.png`
- [x] Mapping errors logged to `mlb_mapping_errors.log`
- [x] Tested: 60 players mapped, 50 headshots downloaded

**Frontend Wiring (UniversalPlayerCard.jsx):**
- [x] PlayerHeadshot supports `sport` and `mlbId` props
- [x] Priority: Local .png → ESPN fallback → BDL headshot_url → Team logo → Initials
- [x] MLB team logos added for fallback display

**New Endpoints:**
- `POST /api/v3/mlb/headshots/sync?phase=ids|headshots|full` - Sync headshots
- `GET /api/v3/mlb/headshots/status` - Get sync coverage status
- `GET /api/v3/mlb/headshots/errors` - Get unmapped players

**New Endpoints:**
- `POST /api/v3/odds/sync?sport=` - Fetch live props from Odds API
- `GET /api/v3/odds/props?sport=` - Query saved live props
- `POST /api/v3/bdl/sync?sport=` - Fetch stats from BDL v1 API
- `GET /api/v3/bdl/players?sport=` - Query player roster
- `GET /api/v3/bdl/stats/{player_name}?sport=` - Get player game logs
- `POST /api/v3/mlb/build-board` - Build MLB cached board with enrichment
- `GET /api/v3/mlb/cached-board` - Get MLB cached board
- `GET /api/v3/mlb/player/{name}` - Get MLB player's enriched props

### Session 4 - Sport-Exclusive Architecture (April 10, 2026)
- [x] Implemented Sport-Exclusive sync engine with `target_sport` argument
- [x] Added collection prefixing for MLB (`mlb_` prefix for all MLB collections)
- [x] Implemented "Locked State" protection preventing cross-sport data corruption
- [x] Updated `optimized_sync_engine.py` with SPORT_COLLECTION_MAP and validation
- [x] Modified `ferrari_tier_service.py` to accept sport context
- [x] Updated `/v3/ferrari/rebuild` endpoint to accept `?sport=` parameter
- [x] Console logging: "Syncing MLB... NBA Data Protected." (and vice versa)
- [x] Verified isolation: MLB sync skips BDL NBA game logs, uses empty cache

### Session 3 - Gemini Intelligence Gate (April 9, 2026)
- [x] Implemented Gemini 3.1 Pro as true intelligence gatekeeper
- [x] Added `adjusted_confidence` scoring (0-1) combining VK probability + contextual factors
- [x] TRAP verdicts now KILL props (removed from selection, not just labeled)
- [x] Fixed Vision Intel display in UniversalPlayerCard

### Session 2 - Route Cleanup (April 9, 2026)
- [x] Fixed 502 error from orphaned route imports
- [x] Archived 9 unused route files to routes_archive/

### Session 1 - BDL Patches + Sport Switcher
- [x] Emergency patches: cursor pagination, circuit breakers for BDL sync
- [x] Global Sport Switcher (NBA/MLB dropdown) in React header
- [x] Frontend hooks append `?sport=${currentSport}` to all API calls

---

## Priority Backlog

### P0 - Completed ✅
- [x] **MLB BDL Date Mapping Issue** - Fixed in Session 6
- [x] **5-Season Historical Backfill for MLB VK Model** - Implemented in Session 6

### P1 - Critical
- [ ] **Complete MLB Headshot Sync** - ~700 players remaining (60 done)
- [ ] **MLB Tier Qualification Logic** - Build 3-Gate system for MLB stats
- [ ] **Upstream Prop Duplication** - Investigate `odds_sync_service.py` for duplicate prop insertion

### P2 - Important
- [ ] **MLB Vision Intel** - Gemini TRAP filtering adapted for MLB stat rules
- [ ] Establish automated daily prop capture (Forward-Testing Infrastructure)
- [ ] Integrate Google/Apple OAuth (Emergent-managed)
- [ ] Implement Stripe for payments

### P3 - Nice to Have
- [ ] Refactor `vegas_killer_model.py` (~2000 lines)
- [ ] Further API controller optimization
- [ ] Archive legacy NBA-only scripts (`bdl_comprehensive_sync.py`, `odds_sync_service.py`)

---

## Testing Credentials
Use "Demo Mode" button on frontend login page.

## Critical Notes for Agents
1. **Sport-Exclusive Sync**: Always pass `target_sport` to `run_optimized_sync()` and `build_ferrari_tiers()`
2. **Locked State**: Cross-sport collection access is BLOCKED - logs show "[LOCKED_STATE] BLOCKED"
3. **BallDontLie Syncing**: Never use page-based pagination for BDL; use `next_cursor` from meta
4. **MLB BDL API Structure**: MLB uses flat structure (game_id at root, team_name at root) vs NBA (nested game/team objects). Always build game cache first for MLB to get dates.
5. **MLB Stat Field Names**: BDL MLB uses short names (`rbi`, `k`, `hr`, `bb`) - must map to internal names (`rbis`, `strikeouts`, etc.)
6. **Gemini Vision Intel**: Use BATCHED API calls only (one per tier)
7. **Google API Key**: Use user's `GOOGLE_API_KEY`, NOT Emergent LLM key

## Key Files Reference
- `/app/backend/services/bdl_universal_sync.py` - BDL sync with MLB game cache fix
- `/app/backend/services/mlb_vk_historical_backfill.py` - 5-Season VK backfill service
- `/app/backend/services/mlb_cached_board_builder.py` - MLB prop enrichment
- `/app/backend/config/db_config.py` - Sport-specific collection routing

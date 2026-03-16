# PickVision - NBA Player Prop Dashboard

## Overview
PickVision is a high-performance NBA Player Prop Dashboard with a "military tech" aesthetic. The application delivers AI-driven betting insights using PropVision Command Post technology.

## Latest Update: 2026-03-17

### Headshot URL Bootstrap - COMPLETED ✅
**All active NBA players now have valid headshot URLs in master hub**

**Actions Completed:**
1. ✅ Bootstrapped `nba_master_hub_2026` with 534 active NBA players from all 30 team rosters
2. ✅ Populated NBA CDN headshot URLs for every player: `https://cdn.nba.com/headshots/nba/latest/1040x760/{nba_player_id}.png`
3. ✅ Validated all 534 URLs - 0 invalid/404s - all returning HTTP 200

**Result:** No scraping needed - NBA's official CDN has photos for 100% of active roster players.

---

## Previous Update: 2026-03-16

### QA Verification - COMPLETED ✅
**Three comprehensive QA directives executed and passed**

**QA Directive 1: SSOT Math Verification** ✅
- Verified L5_avg and hit_rate share exact same denominator
- Confirmed no null or 0-minute games contaminate calculations
- **Result:** "Derrick Jones Jr. Contradiction" is now IMPOSSIBLE

**QA Directive 2: Reactivity Injection Test** ✅
- Created `/api/qa/inject-line-move` endpoint for live testing
- Successfully injected fake line (30.5 → 99.5) for Luka Doncic
- UI auto-updated within 30 seconds without page refresh
- **Result:** TanStack Query polling verified working

**QA Directive 3: Frontend Stress Test** ✅
- Implemented loading skeletons for War Zone, Safe Haven, Front Lines sections
- Added empty state messages when no games/data available
- Components now handle slow data (3+ seconds) gracefully
- **Result:** No crashes on empty arrays or slow responses

**Files Created:**
- `NEW /app/backend/routes/qa_testing.py` - QA injection endpoints

---

### P2 Tech Debt Eradication - COMPLETED ✅
**Final cleanup of legacy code and database collections**

**Actions Completed:**
1. ✅ **Dropped 5 legacy DB collections:** `stats_cache`, `dg_stats_cache`, `player_props`, `league_roster`, `demon_cards`
2. ✅ **Re-synced `dg_cached_board`:** Purged stale `l5_stats`/`l10_stats` fields from schema
3. ✅ **Consolidated backend code:** Created `/app/backend/utils/player_lookup.py` for DRY player lookup
4. ✅ **Deleted legacy frontend file:** Removed `GlobalUtilities.js`, created `PickVisionUtils.jsx`
5. ✅ **Fixed import shadowing:** Updated `utils/__init__.py` to re-export from `utils.py`

**Files Created:**
- `NEW /app/backend/utils/player_lookup.py` - Consolidated player lookup logic
- `NEW /app/frontend/src/lib/PickVisionUtils.jsx` - Shared UI utility components

**Files Deleted:**
- `/app/frontend/src/lib/GlobalUtilities.js`
- `/app/frontend/src/hooks/useDFSData.js`
- `/app/frontend/src/pages/FullBoard.js`

**Testing Status:** ✅ All tests passed (Backend 100%, Frontend 100%)

---

### TanStack Query Global State Implementation - COMPLETED
**Implemented Two-Pipe reactive architecture using TanStack Query (React Query)**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    TANSTACK QUERY GLOBAL STATE                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  PIPE 1: useMasterStats(playerId)                                          │
│  ├─ Source: /api/v3/cached-player/{playerName}                             │
│  ├─ staleTime: 24 hours (data only changes at 0400 EST CRON)              │
│  └─ Cache: Heavy - never refetch in same session                           │
│                                                                             │
│  PIPE 2: useLiveOdds() hooks                                               │
│  ├─ Source: Cached board endpoints                                         │
│  ├─ refetchInterval: 30 seconds (Open Door polling)                        │
│  └─ Hooks: useWarZone, useSafeHaven, useLiveScores, useBreakingNews       │
│                                                                             │
│  INTERSECTION: Components merge Pipe 1 + Pipe 2 via playerName            │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Files Created:**
- `NEW /app/frontend/src/providers/QueryProvider.jsx` - QueryClientProvider wrapper
- `NEW /app/frontend/src/hooks/useMasterStats.js` - PIPE 1 stats hook (24hr cache)
- `NEW /app/frontend/src/hooks/useLiveOdds.js` - PIPE 2 live data hooks (30s polling)

**Files Modified (Localized Fetches Purged & Re-wired):**
- `/app/frontend/src/App.js` - Wrapped with GlobalQueryProvider
- `/app/frontend/src/pages/Dashboard.jsx` - Uses useLiveScores, useBreakingNews, usePlayerSearch
- `/app/frontend/src/components/dashboard/PlayerDetailPage.jsx` - Uses useMasterStats
- `/app/frontend/src/components/dashboard/CommandPost.jsx` - Uses usePlayerProfile
- `/app/frontend/src/components/dashboard/CommandSearch.jsx` - Uses usePlayerSearch

**Key Hooks:**
| Hook | Pipe | Cache Strategy | Use Case |
|------|------|----------------|----------|
| `useMasterStats(playerName)` | PIPE 1 | 24hr stale | Player stats from Master Hub |
| `useLiveOdds()` | PIPE 2 | 30s refetch | Full cached board |
| `useWarZone()` | PIPE 2 | 30s refetch | Demon picks |
| `useSafeHaven()` | PIPE 2 | 30s refetch | Goblin picks |
| `useLiveScores()` | PIPE 2 | 30s refetch | Live game scores |
| `useBreakingNews()` | PIPE 2 | 60s refetch | News headlines |
| `usePlayerSearch(query)` | PIPE 2 | 30s cache | Player search |
| `usePlayerProfile(name)` | PIPE 2 | 60s cache | Command Post profiles |

---

### SSOT Architecture Implementation - COMPLETED
**Implemented strict Single Source of Truth (SSOT) architecture with two isolated data pipelines**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         SSOT DATA ARCHITECTURE                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  PIPE 1: Stats Vault (nba_master_hub_2026)                                 │
│  ├─ Source: Tank01 API (0400 EST CRON ONLY)                                │
│  ├─ Contains: baseline_stats, game_logs                                    │
│  └─ Protected Fields: player_id, player_name, photo_url (NEVER overwritten)│
│                                                                             │
│  PIPE 2: Live Wire (dg_cached_board / Active_Lines)                        │
│  ├─ Source: The Odds API (intraday adaptive polling)                       │
│  ├─ Contains: Live lines, odds, game times                                 │
│  └─ NO statistical calculations                                            │
│                                                                             │
│  INTERSECTION (UI Cards):                                                  │
│  └─ Joined via player_name, hit rates calculated from PIPE 1 game_logs    │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Key Rules Enforced:**
1. **FORBIDDEN:** Frontend/helpers calling external stat APIs (Tank01, BallDontLie)
2. **FORBIDDEN:** Creating secondary internal APIs for stats
3. **0400 EST CRON:** ONLY authorized Tank01 caller
4. **Protected Fields:** player_id, player_name, photo_url NEVER modified by stats sync

**Files Created/Modified:**
- `NEW /app/backend/services/ssot_data_layer.py` - SSOT enforcement layer
- `/app/backend/services/cron_scheduler.py` - Updated to 0400 EST, added SSOT docs
- `/app/backend/services/picks_getter_service.py` - All methods now SSOT-compliant
- `/app/backend/services/stats_service.py` - Coupled stats calculation

**Verification:**
```
API Response shows: stats_source: "ssot_game_logs" or "ssot_baseline"
```

---

### Stats Coupling Bug Fix - COMPLETED
**Fixed critical data inconsistency where hit rates and averages were calculated from different data sources**

**Problem:**
- Hit rates were calculated on-the-fly in `stats_service.py`
- L5/L10 averages came from pre-calculated `baseline_stats` in master hub
- This caused mathematically impossible scenarios (e.g., 100% hit rate on Over 9.5 with L5 avg of 8.2)

**Solution:**
1. **Refactored `stats_service.py`:**
   - Added `calculate_coupled_stats()` function
   - Both hit rate and average are now calculated from the **exact same** array of games
   - Updated stat field mapping for Tank01 API format (e.g., `tptfgm` for 3PM, `TOV` for turnovers)

2. **Updated `tank01_stats_service.py`:**
   - Added `_fetch_game_logs()` method to store raw game logs
   - Game logs are now saved to `nba_master_hub_2026.game_logs` during sync
   - Each game includes: gameID, pts, reb, ast, tptfgm, stl, blk, TOV, mins

3. **Updated `picks_getter_service.py`:**
   - `_enrich_player_with_master_hub_stats()` now uses coupled calculation
   - `_add_insights_to_pick()` also uses coupled calculation for War Zone/Safe Haven picks
   - Props now include `stats_coupled: true` flag when using coupled calculation
   - New fields: `l5_hit_rate`, `l10_hit_rate`, `l5_games_over`, `l10_games_over`

4. **Added sync endpoint:**
   - `POST /api/v3/master-hub/sync-player-logs/{player_name}` - Sync game logs for a single player

**Verification:**
```
Test: L5 Avg 8.0 for PTS Over 9.5 line
- L5 Hit Rate: 20% (1/5 games over)
- CONSISTENT: Avg below line, hit rate < 50%
```

**Files Modified:**
- `/app/backend/services/stats_service.py`
- `/app/backend/services/tank01_stats_service.py`
- `/app/backend/services/picks_getter_service.py`
- `/app/backend/routes/master_hub.py`

---

### API Provider Migration: BallDontLie → Tank01 - COMPLETED
**Migrated primary data engine from BallDontLie to Tank01 Fantasy Stats API (RapidAPI)**

**Changes:**
1. **Authentication Setup:**
   - Tank01 API Key: Configured in `.env` as `TANK01_API_KEY`
   - RapidAPI Host: `tank01-fantasy-stats.p.rapidapi.com`

2. **New Service Created:**
   - `/app/backend/services/tank01_stats_service.py`
   - Fetches real game logs via `getNBAGamesForPlayer`
   - Calculates L5, L10, Season averages from actual games (minutes > 0 only)
   - Includes std_dev calculation for stability metrics

3. **CRON Job Updated:**
   - `/app/backend/services/cron_scheduler.py` now uses Tank01
   - Daily sync at 0300 EST

4. **API Endpoints Added:**
   - `POST /api/v3/master-hub/sync-tank01` - Manual Tank01 sync
   - `POST /api/v3/master-hub/populate-tank01-ids` - ID population

5. **Legacy BallDontLie Code Deprecated:**
   - `/app/backend/services/master_hub_sync.py` - Simplified wrapper to Tank01
   - `.env` - BallDontLie key marked as deprecated

**Results:**
- 470 players synced with real game log data
- Stats now accurate: LeBron L5=25.4, L10=24.2, SZN=24.5 (75 games)
- Devin Vassell: L5=14.8, L10=17.3, SZN=16.3 (64 games)

### Intel Suite Advanced Metrics - COMPLETED
**Expanded backend schema to calculate and store advanced metrics for Radar Picks**

**New Metrics (only for is_radar = true props):**
1. **usage_ripple** (Operational Volume) - Projected Usage Rate changes based on lineup/injury data
   - Shows "+X.X% Vol. Shift" or "Standard Volume"
   - Includes injuries_affecting list

2. **matchup_dvp** (Defensive Friction) - Opponent's Defense vs. Position ranking
   - Shows "Rank #X vs. [Position]" (e.g., "Rank #14 vs. Scorers")
   - Friction levels: Low/Medium/High with color coding

3. **pace_delta** (Tempo Multiplier) - Projected game pace differential
   - Shows "+/-X.X Possessions" compared to player's average
   - Includes expected game pace calculation

4. **stability_index** (Tactical Variance) - 1-100 consistency score
   - Based on standard deviation of L10 games
   - Labels: Elite/High/Medium/Low

5. **vision_insight** (Target-Lock Rationale) - AI reasoning for flagging prop
   - Primary insight + supporting reasons
   - Confidence level (High/Medium-High/Medium)
   - Tactical notes

**Files Created:**
- `/app/backend/services/intel_suite_calculator.py` - Full calculation logic

**Files Updated:**
- `/app/backend/routes/command.py` - Added intel_suite to profile response
- `/app/backend/services/picks_getter_service.py` - Added intel_suite enrichment for radar picks
- `/app/frontend/src/components/dashboard/PlayerDetailPage.jsx` - Display all metrics in modal

**Security:** intel_suite data is ONLY returned when `is_radar = true` or `is_demon/is_goblin = true`

### Vision Pick Highlight Feature - COMPLETED
**When clicking a player from Safe Haven/War Zone, the specific bet is highlighted with VISION PICK styling**

**Features:**
- **Gold glow** - Amber gradient border with 20px glow shadow
- **Crosshair icon** - Pulsing crosshair emblem on the left
- **"VISION PICK" label** - Gold badge with crosshair icon
- **"Tap to view Intel Suite"** - Click indicator
- **Vision Intel Suite modal** - Full analysis panel on click

**Modal Contents:**
- Player name and stat type
- Vision Pick line and odds
- L5/L10/Season averages from master hub
- Hit Rate Analysis (L10 and L5 percentages)
- "VISION RECOMMENDS" badge

**Files Updated:**
- `/app/frontend/src/components/dashboard/PlayerDetailPage.jsx`
  - Added `showIntelSuite` and `selectedVisionProp` state
  - Updated `PropRow` with gold styling for highlighted props
  - Added Vision Intel Suite modal component
  - Fixed `isHighlightedProp` matching logic to use `stat_type_extracted`

### Stats Unified to Master Hub - COMPLETED
**ALL player stats (L5/L10/SZN) now come exclusively from `nba_master_hub_2026.baseline_stats`**

**Changes:**
- Updated `/app/backend/services/picks_getter_service.py`:
  - Added `_enrich_player_with_master_hub_stats()` method
  - Modified `get_cached_player()` to call enrichment before returning
  - Props now receive `l5_avg`, `l10_avg`, `season_avg` from master hub, not from `hit_rates`
  
- **Before:** Stats came from individual prop `hit_rates` calculated per-line
- **After:** Stats come from `baseline_stats` in master hub, consistent across all props of same type

**Data Flow:**
```
nba_master_hub_2026.baseline_stats → API enrichment → Frontend display
                                         ↓
                              props[].l5_avg = baseline_stats[stat_type].l5_avg
```

### Prop Arsenal UI Rework - COMPLETED
**Changed prop display from accordion to flat list layout across ALL views**

- **Before:** Props were displayed in nested accordions that required clicking to expand
- **After:** Props are displayed as a flat list grouped by category headers
- **Categories:** POINTS, REBOUNDS, ASSISTS, PRA (Points+Rebounds+Assists), PR, PA, RA, 3PM, STL, BLK, TO, etc.
- **Stat normalization:** API returns `P+R`, `P+A`, `R+A` - now normalized to `PR`, `PA`, `RA`
- **L5/L10/SZN columns:** Display actual averages from hit_rates data
- **Hit Rate columns:** L10 HR and L5 HR percentages shown
- **DEMON/GOBLIN badges:** Visual distinction for high-risk/safe picks

**Files Updated:**
- `/app/frontend/src/components/dashboard/TacticalPlayerCard.jsx`
  - Added `normalizeStatType()` function for combined stat handling
  - Updated `groupPropsByCategory()` to normalize stat types
  - Enhanced `PROP_LABELS` with alternate formats (P+R, P+A, R+A)
  
- `/app/frontend/src/components/dashboard/PlayerDetailPage.jsx` - **REWRITTEN**
  - Changed from accordion-based `CategoryAccordion` to flat list with `CategoryHeader`
  - Fixed data extraction to read from `hit_rates.l5.avg`, `hit_rates.l10.avg`, `hit_rates.season.avg`
  - Added hit rate percentage display (L10 HR, L5 HR)
  - Categories grouped by `stat_type_extracted` (PTS, REB, AST, PRA, PR, PA, RA, etc.)

### Database Sync - Session Work
- Synced `nba_master_hub_2026` collection with 1,124 players
- Merged headshot URLs from `dg_master_roster` (535 photos)
- Some players have `baseline_stats`, others pending daily CRON job sync

---

## Previous Updates

### Centralized Data Hub Implementation - COMPLETED
- **nba_master_hub_2026**: Single source of truth for all player data
- **Daily CRON job**: Syncs L5, L10, season averages via APScheduler
- **Backend refactor**: All endpoints pull stats from master hub

### Conditional State Highlighting - COMPLETED
- Target-Lock system for PropVision recommendations
- `is_radar: true/false` flag on each prop line
- Full Intel Suite for Target-Lock props, basic stats for standard props

### PropVision Command Post - COMPLETED
- Tactical Player Card System
- Parlay conflict detection engine
- Military terminology (Infiltration Grade, Convergence Rate, etc.)

---

## Architecture

```
/app
├── backend/
│   ├── routes/
│   │   ├── command.py      # Player profiles from master hub
│   │   ├── master_hub.py   # Master hub status/sync routes
│   │   └── tiers.py        # Board picks from master hub
│   ├── services/
│   │   ├── picks_getter_service.py  # Enriches picks with hub data
│   │   ├── master_hub_sync.py       # Daily stats sync
│   │   └── cron_scheduler.py        # APScheduler setup
│   └── server.py
├── frontend/
│   └── src/
│       ├── components/
│       │   └── dashboard/
│       │       ├── TacticalPlayerCard.jsx  # Flat list prop display
│       │       ├── CommandPost.jsx         # Command Post panel
│       │       └── PickCard.jsx            # Dashboard pick cards
│       └── pages/
│           └── Dashboard.jsx
└── ...
```

---

## Pending Tasks

### P0 - Immediate
- [x] Prop Arsenal UI rework (flat list layout)
- [x] Fix stats coupling bug (hit rate + avg from same data source)
- [ ] Test conflict detection (add Over + Under for same player prop)
- [ ] Run full Tank01 sync with game_logs to enable coupled stats for all players

### P1 - High Priority
- [ ] Consolidate duplicate player lookup functions into `/app/backend/utils/`

### P2/P3 - Future
- [ ] Stripe integration & authentication
- [ ] "Copy Parlay" button
- [ ] "Pro Tier" features
- [ ] Real Google/Apple OAuth
- [ ] Sync Status Dashboard UI
- [ ] `/api/health/services` endpoint

---

## Key API Endpoints

- `GET /api/command/profile/{player_name}` - Full tactical profile with all props
- `GET /api/v3/goblin-vault` - Safe Haven picks
- `GET /api/v3/most-popular-bets` - Most Popular picks
- `POST /api/v3/master-hub/sync` - Manual master hub sync
- `POST /api/v3/sync-baseline-stats` - Baseline stats sync trigger

---

## Data Flow

```
Tank01 API → tank01_stats_service.py → MongoDB (nba_master_hub_2026)
                                              ↓
                                     baseline_stats: {
                                       PTS: {l5_avg, l10_avg, season_avg},
                                       REB: {...},
                                       AST: {...},
                                       PRA: {...}
                                     }
                                     game_logs: [
                                       {gameID, pts, reb, ast, tptfgm, stl, blk, TOV, mins}
                                     ]
                                              ↓
                                     calculate_coupled_stats(game_logs, stat_type, line)
                                              ↓
                                     API Response (with stats_coupled: true)
                                              ↓
                                     Frontend (L5 Avg + L5 Hit Rate consistent)
```

---

## Testing Status
- TacticalPlayerCard flat list layout: Verified via screenshots
- L5/L10/SZN display: Shows values when available, "-" when null
- Target-Lock styling: Green highlight + TARGET badge working
- Stat type normalization: P+R → PR confirmed working
- Coupled stats consistency: Verified - avg and hit rate mathematically consistent

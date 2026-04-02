# PropVision - Product Requirements Document

## Overview
PropVision is a sports analytics platform for NBA player props, providing data-driven insights for betting decisions.

## Latest Update (2026-04-02): Usage Vacuum Intel Suite Integration

### Feature Description
Enhanced the Usage Vacuum feature with an interactive Intel Suite modal. When a player card displays a Usage Vacuum badge (indicating they're benefiting from an injured star's absence), clicking the badge opens a detailed explanation modal showing:

- **Who's injured** and their usage rate
- **Why this matters** for the beneficiary
- **How it affects** the player's projected performance
- **Ferrari Score modifier** applied

### UI Component: Usage Vacuum Modal
The IntelligenceModal now supports `type="usage_vacuum"` with:
- Injured star player name and team
- Injury reason (if available)
- Beneficiary rank (1st or 2nd option)
- Usage boost percentage
- Ferrari Score modifier (+15 primary, +10 secondary)
- Educational explanation of usage redistribution

### Files Modified (This Update)
- `/app/frontend/src/components/dashboard/IntelligenceModal.jsx` - Added `usage_vacuum` modal type
- `/app/frontend/src/components/dashboard/UniversalPlayerCard.jsx` - Made vacuum card clickable, added Info icon

### Test Status
- **Backend**: All 16 vacuum endpoint tests PASSED
- **Frontend**: UI components verified, modal opens correctly
- **Note**: Vacuum cards only display when beneficiary players have active props

---

## Previous Update (2026-04-02): Usage Vacuum Microservice

### Feature Description
Implemented the InjuryVacuumService - an event-driven microservice that monitors NBA injury reports and calculates "Usage Vacuum" beneficiaries when star players are ruled OUT.

### Architecture
- **Model**: Event-Driven (monitors state changes, broadcasts updates)
- **Cache**: In-memory star usage profiles (Redis-ready)
- **Database**: MongoDB `injury_log` and `vacuum_alerts` collections
- **Latency Goal**: < 30 seconds from source update to score recalculation

### Vacuum Engine Logic
1. **Star Detection**: Players with Usage Rate > 25%
2. **Trigger Conditions**: Status changes to "OUT" or "DOUBTFUL"
3. **Beneficiary Calculation**: Top 2 teammates with highest historical usage increase

### Modifiers
| Beneficiary | Ferrari Score Boost |
|-------------|---------------------|
| Primary     | +15 points          |
| Secondary   | +10 points          |

### API Endpoints (New)
- `POST /api/v3/vacuum/check` - Trigger injury check
- `GET /api/v3/vacuum/updates` - Get current vacuum state for Ferrari Engine
- `GET /api/v3/vacuum/active` - Get all active usage vacuums
- `GET /api/v3/vacuum/beneficiary/{player_name}` - Check if player is a beneficiary
- `POST /api/v3/vacuum/clear/{injured_player}` - Clear a vacuum

### UI Component: Usage Vacuum Alert
```
USAGE VACUUM ALERT
├── INJURY ALERT: Joel Embiid OUT
├── PRIMARY BENEFICIARY badge
├── "Tyrese Maxey usage projected +6.2%"
└── +15 Ferrari Boost
```

### Sample Output
```
VACUUM: Joel Embiid (PHI) - OUT
  Usage: 34.8%
  -> Tyrese Maxey: +6.2% usage, +15 score (primary)
  -> Paul George: +4.5% usage, +10 score (secondary)
```

### Files Created
- `/app/backend/services/injury_vacuum_service.py` (524 lines)
- `/app/backend/routes/vacuum.py` (168 lines)

### Files Modified
- `/app/backend/routes/__init__.py` (Added vacuum router)
- `/app/backend/services/ferrari_tier_service.py` (Integrated vacuum modifier)
- `/app/frontend/src/components/dashboard/PlayerDetailPage.jsx` (Usage Vacuum UI section)
- `/app/frontend/src/components/dashboard/UniversalPlayerCard.jsx` (Usage Vacuum card)

---

## Previous Update (2026-04-02): Point Lift Translation (Vegas Intel)

### Feature Description
Implemented "Point Lift" translation layer that converts the raw Whistle Matrix modifier (+15/-15) into human-readable projected stat boosts/ceilings.

### Point Lift Formula
```
Point_Lift = (Ref_PPG_Avg - League_PPG_Avg) * (Player_Usage_Rate / Team_Usage_Avg)

If usage unavailable, use flat defaults:
- PTS: ±3.5 points
- FTM: ±1.5 free throws
- PRA: ±2.5 combined stats
```

### Vegas Intel UI Component
**Header**: ⚖️ OFFICIATING IMPACT

**Content**:
- Lead Official: [Ref Name]
- Trend: [O/U %] Over | [PPG] Avg
- Impact: [+3.5 Projected PTS Boost] or [-3.5 Projected PTS Ceiling]

**Visual Indicators**:
- 🥇 Gold Whistle Icon: High whistle ref (PPG > 118 OR O/U > 60%)
- ❄️ Blue Snowflake Icon: Low whistle ref (PPG < 113 OR O/U < 45%)

### API Response Fields (Added)
```json
{
  "point_lift": 3.5,
  "lift_label": "+3.5 Projected PTS Boost",
  "lift_type": "boost",
  "foul_rate_diff": 3
}
```

### Tooltip Context
"This official calls shooting fouls at a [X]% higher/lower rate than league average, historically increasing/decreasing the scoring ceiling for high-usage players."

### Files Modified
- `/app/backend/services/referee_scraper_service.py` (Added `calculate_point_lift()` method)
- `/app/backend/services/ferrari_tier_service.py` (Integrated Point Lift into scored props)
- `/app/frontend/src/components/dashboard/UniversalPlayerCard.jsx` (Vegas Intel UI section)
- `/app/frontend/src/components/dashboard/PlayerDetailPage.jsx` (Vegas Intel badge)

---

## Previous Update (2026-04-02): Feature 5 - Dynamic Whistle Matrix

### Feature Description
Integrated daily NBA referee assignments and stats into the Ferrari Power Score calculation to account for referee-driven scoring variance.

### Data Sources
- **Daily Assignments**: official.nba.com/referee-assignments/
- **Referee Stats**: Covers.com O/U and PPG stats (with fallback data)

### Whistle Classification
- **High Whistle (Green Light)**: PPG > 118 OR O/U Win Rate > 60%
- **Low Whistle (Red Light)**: PPG < 113 OR O/U Win Rate < 45%
- **Neutral**: All other referees

### Whistle Modifier Logic
| Stat Type | High Whistle | Low Whistle | Neutral |
|-----------|--------------|-------------|---------|
| PTS, FTM  | +15 points   | -15 points  | 0       |
| PRA       | +7.5 points  | -7.5 points | 0       |

### Power Score Formula (Updated)
```
ferrari_power_score = base_power_score + whistle_modifier

base_power_score = (Edge × 0.4) + (Cushion × 0.3) + (Consistency × 0.3)
```

### API Endpoints (New)
- `POST /api/v3/ferrari/sync-refs` - Manually sync referee data
- `GET /api/v3/ferrari/refs` - Get today's referee assignments

### API Response Fields (Added)
```json
{
  "crew_chief": "Che Flores",
  "ref_ou_pct": 64.3,
  "ref_ppg": 117.1,
  "whistle_class": "high_whistle",
  "has_whistle_modifier": true,
  "whistle_modifier": 15.0,
  "base_power_score": 68.31,
  "ferrari_power_score": 83.31
}
```

### UI Update
- Volume2 icon + "Crew Chief (O/U%)" displayed on each player card
- Green text for high whistle, red for low whistle, gray for neutral
- Modifier value shown if non-zero

### Files Modified
- `/app/backend/services/referee_scraper_service.py` (NEW - 531 lines)
- `/app/backend/services/ferrari_tier_service.py` (Updated - Whistle integration)
- `/app/backend/routes/ferrari_tiers.py` (Updated - New endpoints)
- `/app/frontend/src/components/dashboard/UniversalPlayerCard.jsx` (Updated - Whistle display)
- `/app/frontend/src/components/dashboard/PlayerDetailPage.jsx` (Updated - Whistle badge)

---

## Previous Update (2026-04-02): Ferrari v6 - Global Power Ranking & Universal Scan

### Feature Description
Implemented the final "Ferrari v6" pipeline with Global Power Ranking. Every prop is scored using a mathematical `ferrari_power_score` formula, sorted globally, and limited to Top 10 per tier.

### Power Score Formula (0-100 scale)
```
ferrari_power_score = (Edge × 0.4) + (Cushion × 0.3) + (Consistency × 0.3)

Edge Weight (40%):
  = (Bovada_Implied_Prob - PrizePicks_Implied_Prob) × 4 × 25 (normalized)
  
Cushion Weight (30%):
  = ((Season_Median - PrizePicks_Line) / PrizePicks_Line) × 100

Consistency Weight (30%):
  = L10_Hit_Rate (0-100 scale)
```

### Universal Scan
- 100% scan of ALL props (no early breaks or lazy loading)
- Complete field analysis before selection
- Example: Scanned 1,986 props → 336 survivors → 30 elite opportunities

### Kill-Switch Filters
1. **5% Absolute Edge**: Props with <5% implied probability separation from Bovada are killed
2. **Median Anchor**: Props where PP line > Season Median are killed

### Tier Windows
- **Safe Haven**: Sharp price ≤ -250 (71%+ implied probability)
- **Front Lines**: Sharp price -245 to -115 (53-71% implied)
- **War Zone**: Sharp price -114 to +500 (value hunting zone)

### Output
- 10 picks per tier (30 total)
- Sorted globally by `ferrari_power_score` descending
- One player per tier (deduplication)

### Market Intel Footer
Frontend displays: `"Verified [X] active props to identify these [30] Elite opportunities."`

### API Endpoints
- `GET /api/v3/ferrari/all` - All tiers + verification stats
- `GET /api/v3/ferrari/safe-haven` - Top 10 Safe Haven picks
- `GET /api/v3/ferrari/front-lines` - Top 10 Front Lines picks
- `GET /api/v3/ferrari/war-zone` - Top 10 War Zone picks
- `GET /api/v3/ferrari/discarded` - Props killed by filters
- `POST /api/v3/ferrari/rebuild` - Trigger pipeline rebuild

### Verification Data Structure
```json
{
  "verification": {
    "active_props_verified": 336,
    "elite_opportunities": 30,
    "safe_haven_pool": 161,
    "front_lines_pool": 72,
    "war_zone_pool": 98,
    "message": "Verified 336 active props to identify these 30 Elite opportunities."
  }
}
```

### Files Modified
- `/app/backend/services/ferrari_tier_service.py` - Ferrari v6 pipeline with Power Score
- `/app/backend/routes/ferrari_tiers.py` - API endpoints with verification stats
- `/app/frontend/src/pages/Dashboard.jsx` - Market Intel footer

---

## Previous Update (2026-04-01): Ferrari Tiered Dashboard with Bovada Separation

### Feature Description
Implemented "Ferrari" tier filtering using Bovada as the primary sharp benchmark. This creates a "Best of the Best" dashboard with zero mid plays.

### Global Kill-Switch
- **15% Separation Rule**: Props with less than 15% implied probability separation from Bovada are discarded entirely
- Formula: `abs(PP_implied - Sharp_implied) / Sharp_implied * 100`
- Result: 650 props discarded, 1130 qualified

### Tier Definitions

**1. SAFE HAVEN (Elite Goblins)**
- Sharp price ≤ -250 (heavy favorite on sharp book)
- L10 hit rate ≥ 70%
- Sorted by: Most negative sharp_price
- Example: Brice Sensabaugh PTS 14.5 @ Sharp -900, 90% L10

**2. FRONT LINES (Battleground)**
- Sharp price between -149 and +110
- 40-cent price gap from PP -137
- L5 hit rate ≥ 60% (momentum check)
- Sorted by: Highest L10 hit rate
- Example: Dylan Harper PTS 12.5 @ Sharp -1, Gap 136, 80% L5

**3. WAR ZONE (Elite Demons)**
- Must be a demon (PP +100)
- Sharp price ≥ +500
- Bovada 200+ pts shorter than PP
- Hit at least 2 times in L10
- Sorted by: Highest sharp_price (biggest payout edge)
- Example: Jock Landale REB 9.5 @ Sharp +2000, 2 L10 hits

### API Endpoints
- `GET /api/v3/ferrari/all` - All tiers in one response
- `GET /api/v3/ferrari/safe-haven` - Elite goblins
- `GET /api/v3/ferrari/front-lines` - Battleground picks
- `GET /api/v3/ferrari/war-zone` - Elite demons
- `GET /api/v3/ferrari/discarded` - Props that failed kill-switch
- `POST /api/v3/ferrari/rebuild` - Manual tier rebuild

### Files Created/Modified
- `/app/backend/services/ferrari_tier_service.py` - Core Ferrari filtering logic
- `/app/backend/routes/ferrari_tiers.py` - API endpoints
- `/app/backend/routes/__init__.py` - Route registration
- `/app/backend/services/odds_sync_service.py` - Ferrari callback integration
- `/app/backend/services/engines/demon_goblin_engine.py` - Service initialization

### Verification
```bash
curl -s "https://best-bet-finder-1.preview.emergentagent.com/api/v3/ferrari/all" | jq
```

---

## Previous Update (2026-04-01): Sharp Market Odds Integration

### Feature Description
Integrated sharp bookmaker pricing into the odds sync pipeline to establish benchmark prices for identifying +EV opportunities. Each PrizePicks prop now includes a nested `sharp_market` object with real market prices from Bovada, DraftKings, and FanDuel.

### Data Sources
- **Bovada**: Primary sharp reference for ALTERNATE lines (better alternate line coverage)
- **DraftKings/FanDuel**: Sharp reference for STANDARD lines (averaged together)

### Implementation
1. **`/app/backend/services/odds_api_service.py`**:
   - Updated `fetch_sharp_book_odds()` to request from `bovada,draftkings,fanduel`
   - Added `regions=us,eu` for broader Bovada coverage
   - Now fetches both standard AND alternate markets from sharp books

2. **`/app/backend/services/odds_sync_service.py`**:
   - Builds `sharp_prices` lookup table from sharp book responses
   - Merges into each PrizePicks prop as nested `sharp_market` object:
     ```json
     {
       "bovada_price": -120,
       "draftkings_price": -110,
       "fanduel_price": -115,
       "dk_fd_average": -112,
       "sharp_price": -112,
       "sharp_source": "dk_fd_avg",
       "is_alternate": false
     }
     ```

### Coverage Results
- 597 alternate props with sharp prices (Bovada primary)
- 223 standard props with sharp prices (DK/FD average primary)
- Total: ~820 props with real market benchmarks

### Verification Commands
```bash
# View sharp market data for a player
curl -s 'http://localhost:8001/api/v3/cached-props' | jq '.players[0].props[0].sharp_market'

# Count props with sharp data
curl -s 'http://localhost:8001/api/v3/cached-props' | jq '[.players[].props[] | select(.sharp_market.sharp_price != null)] | length'
```

---

## Previous Update (2026-04-01): Hook Protector & Bait Detector Sidecar Modules

### Feature Description
Decoupled "sidecar" modules that detect potentially dangerous betting lines:

**Hook Protector**: Detects lines set at or near the statistical Mode (most frequent outcome). Vegas often sets lines at the Mode to maximize house edge.
- Trigger: Line within 0.5 of the player's L10 Mode
- Warning: "⚠️ Hook Risk - Line near Mode (X)"

**Bait Detector**: Detects suspiciously low lines that may be "Vegas bait" (too good to be true).
- Trigger: Line 25%+ below the player's L10 Median
- Warning: "🚨 SUSPECT LINE: Vegas Bait"
- Action: Overrides Safe Haven status

### Data Pipeline
- **Source**: BallDontLie (BDL) API via `bdl_game_logs_sync_batched.py`
- **Storage**: `nba_master_hub_2026.bdl_game_logs` - raw game-by-game box scores (PTS, REB, AST, etc.)
- **Calculation**: Exact Median and Mode computed from actual game data (NO estimation)

### API Endpoints
- `GET /api/v3/sidecar/status` - Check if sidecar is enabled
- `POST /api/v3/sidecar/toggle?enabled=true|false` - Toggle feature on/off (kill switch)
- `GET /api/v3/sidecar/analyze/{player_name}?stat_type=PTS&line=20.5` - Debug endpoint

### Frontend Changes
- Player cards now show "Med" (Median) instead of "Avg" when sidecar data available
- Hook Risk: Amber warning banner with mode value
- Suspect Bait: Red pulsing warning banner

### Files Created/Modified
- `/app/backend/services/sidecar/hook_bait_detector.py` - Core sidecar logic
- `/app/backend/services/sidecar/__init__.py` - Package init
- `/app/backend/routes/tiers.py` - Sidecar enrichment integration
- `/app/backend/routes/cached_data.py` - Sidecar status/toggle endpoints
- `/app/frontend/src/components/dashboard/UniversalPlayerCard.jsx` - Warning UI

---

## Previous Update (2026-03-29): Mobile Swipe Indicator

### Feature
Added swipe indicators on mobile to show users they can swipe horizontally through pick sections (Safe Haven, Front Lines, War Zone, etc.).

### Implementation
1. **`/app/frontend/src/pages/Dashboard.jsx`** - Enhanced `SwipeContainer` component:
   - Added dot pagination indicators below each section on mobile
   - Active dot is yellow/wider, inactive dots are smaller gray circles
   - Added "SWIPE" hint with chevron on right edge (disappears after first swipe)
   - Tracks scroll position to update active index

### Visual Design
- Dot indicators: First dot yellow (active), others gray
- Right edge gradient fade with "SWIPE" + chevron arrow
- Indicators hidden on desktop (grid layout) - only visible on mobile

---

## Previous Update (2026-03-29): Blowout Warning Feature Fix + Pulse Animation

### Problem
The Blowout Risk warning wasn't displaying in the Vision Intel Suite modal. The `blowout_risk` data was not being included in the `intel_suite` object.

### Root Cause
The `IntelSuiteCalculator.calculate_intel_suite()` method was not calling the `StandingsService.calculate_blowout_risk()` function to populate the blowout risk data.

### Fixes Applied
1. **`/app/backend/services/intel_suite_calculator.py`**:
   - Added `_calculate_blowout_risk()` method that calls `StandingsService.calculate_blowout_risk()`
   - Added `blowout_risk` to the returned intel_suite object

2. **`/app/frontend/src/App.css`**:
   - Added `blowout-pulse-high` animation (1.5s aggressive red pulse for HIGH risk)
   - Added `blowout-pulse-medium` animation (2s subtle orange pulse for MEDIUM risk)

3. **`/app/frontend/src/components/dashboard/PlayerDetailPage.jsx`**:
   - Applied pulse animation classes to the blowout warning container
   - Added `data-testid="blowout-risk-warning"` for testing

### Verification
- Donovan Clingan (POR vs WAS): Shows HIGH RISK with pulsing red warning, team records 37-38 vs 17-56
- Tyler Herro (MIA): Shows HIGH RISK warning
- Players without blowout risk don't display the warning section

---

## Previous Update (2026-03-29): Front Lines Vision Intel Fix

### Problem
Front Lines picks were showing as "vision shell" in the Detail View - no badges, AI summary, or intel_suite data was displaying despite being enriched by the Board Intelligence Service.

### Root Cause Analysis
1. **Data Persistence Issue**: The background sync was dropping the `dg_cached_board` collection and replacing it with fresh data, which ERASED all vision intel enrichment.
2. **Missing Field Mapping**: The `get_cached_player()` service was not including `board`, `vision_score`, and `active_badges` fields in the prop mapping.
3. **Featured Prop Detection**: The code checked only the live board API (which changes as games lock) instead of the prop's own `board` field from the database.

### Fixes Applied
1. **`/app/backend/services/cached_board_builder_service.py`**:
   - Added vision intel preservation logic BEFORE the atomic swap
   - Enriched data (vision_summary, intel_suite, board, etc.) is now merged into new player data

2. **`/app/backend/services/picks_getter_service.py`**:
   - Added `board`, `vision_score`, and `active_badges` to the prop field mapping in `get_cached_player()`

3. **`/app/backend/routes/cached_data.py`**:
   - Updated featured prop detection to check BOTH the prop's `board` field AND the board membership cache

### Verification
- All 10 Front Lines picks now have `intel_suite=True` and `vision_summary` populated
- Tyler Herro REB@4.5 shows: board=front_lines, vision_score=62.0, intel_suite with all keys
- Vision Intel Suite modal displays: Context Badges (Milestone, Pay Day), Operational Volume, Defensive Friction, Tempo, Variance, and AI Vision Summary

---

## Previous Update (2026-03-29): Gemini AI Model Updated

### Change
Updated Vision AI Summary service to use `gemini-3.1-flash-lite-preview` model as requested by user.

### File Changed
- `/app/backend/services/vision_summary_service.py` - Line 205: Changed model from `gemini-2.5-flash` to `gemini-3.1-flash-lite-preview`

### Verification
- Board enrichment: 30 players enriched, 0 errors, ~17s duration
- All 3 boards (Safe Haven, Front Lines, War Zone) have AI summaries populated
- No 404 errors or API timeouts

---

## CRITICAL FIX (2026-03-25): Anchor Classification Overhaul - PERMANENT

### Problem Summary
Props were being misclassified as demons when they were actually goblins (and vice versa). This happened because:
1. The anchor (reference line) came from **stale baseline_stats** instead of fresh game logs
2. When no main line existed, the anchor fell back to wrong/outdated L5 averages
3. The odds provider's main line was used even when it was set incorrectly

### Root Cause: ANCHOR = ODDS PROVIDER'S MAIN LINE (WRONG)
The old system used the odds provider's "main line" as the anchor for classification:
- Demon = line above main line
- Goblin = line below main line

**Problem:** The main line is the odds provider's opinion, which can be set LOW to entice bets.
If main line = 27.5 but player averages 30.0, then 29.5 is marked as "demon" even though it's BELOW the player's average.

### Fix Applied: ANCHOR = PLAYER'S L10 AVERAGE (CORRECT)
Now the system uses the **player's actual L10 average** as the anchor:
- Demon = line above player's L10 average (harder to hit)
- Goblin = line below player's L10 average (easier to hit)

**Changes Made:**
1. `/app/backend/services/anchor_classification_service.py`:
   - Priority: L10 avg > L5 avg > Season avg > Main line (last resort)
   
2. `/app/backend/services/cached_board_builder_service.py`:
   - Calculate fresh L5/L10 from `bdl_game_logs` (not stale `baseline_stats`)
   - Use `anchor_normalize_name()` for consistent key matching
   
3. `/app/backend/services/picks_getter_service.py`:
   - War Zone now filters for "hittable demons" (within 20% of avg + 50%+ hit rate)

### Verification Results
- Before: 67 "bad demons" (line < L10 avg)
- After: 0 "bad demons"
- Anchor sources: 493 l10_avg, 7 main_line (only for players without game logs)

### Architectural Rules (DO NOT VIOLATE)
1. **ANCHOR = Player's L10 Average** (not odds provider's main line)
2. **L5/L10 calculated from `bdl_game_logs`** (not stale `baseline_stats`)
3. **Use `anchor_normalize_name()` for key matching** (removes suffixes like Jr, III)

---

## CRITICAL FIX (2026-03-25): War Zone Logic Updated for New Anchor System

### Problem
After the anchor fix, demons are now properly defined as lines ABOVE the player's L10 average.
The old War Zone filter (L10 avg >= line) would never pass for true demons.

### Fix Applied
New War Zone criteria for "hittable demons":
1. Line is within 20% of player's L10 average
2. Hit rate >= 50% (or 40% if within 10% of average)

**Example:** Player with L10 avg of 20.0 PTS
- Line 21.5 with 60% HR = GOOD (7.5% above avg, good hit rate)
- Line 25.5 with 30% HR = BAD (27.5% above avg, low hit rate)

### Files Changed
- `/app/backend/services/picks_getter_service.py` - New hittable demon criteria

---

**WHY THIS IS CORRECT:**
- War Zone = DEMON bets (higher risk, higher reward)
- A demon bet requires the player to EXCEED their typical output
- If a player's average is below the line, even with high hit rate, it's not a true demon
- High hit rate on a low line is a GOBLIN bet, not a demon bet

### Verification
- Filter stats now show `rejected_avg_below_line` counter
- Test: 555 bad demon props rejected, 0 bad picks leak through
- All War Zone picks now have L10 avg >= line (positive margin)

### Files Changed
- `/app/backend/services/picks_getter_service.py` - Removed "close call" exception, strict L10 avg >= line gate

---

## CRITICAL FIX (2026-03-24): Adaptive Sync Engine Bug

### Problem
The adaptive sync engine was fetching odds from the API but **NOT saving them correctly to the database**. This caused:
- Empty picks on deployed instances (Emergent production, user's server)
- Manual sync worked, but background polling did not
- App appeared as an empty shell after deployment

### Root Cause
The `AdaptiveSyncEngine._update_cached_board()` method was saving **FLAT prop documents** directly to MongoDB, but the `picks_getter_service` expected **NESTED player documents** with a `props` array.

The manual `/api/v3/sync` endpoint used `DemonGoblinEngine.sync_odds_to_mongo()` which correctly called `CachedBoardBuilderService.build_cached_board()` to create nested documents.

### Fix Applied
Modified `adaptive_sync_engine.py`:
1. Added `set_sync_callback()` method to accept a proper sync function
2. Modified poll loop to call the callback instead of its own broken `_update_cached_board()`

Modified `server.py`:
1. After initializing the adaptive sync engine, wires up the callback:
   `adaptive_sync.set_sync_callback(demon_goblin_engine.sync_odds_to_mongo)`

### Files Changed
- `/app/backend/adaptive_sync_engine.py` - Added callback mechanism + periodic BDL game logs refresh every 4 hours
- `/app/backend/server.py` - Wired callback to DemonGoblinEngine + added BDL game logs to initial sync
- `/app/backend/routes/core_v3.py` - Fixed status endpoint to count demons/goblins correctly

## Additional Fix: BDL Game Logs Sync (2026-03-24)

### Problem
Hit rates were calculated incorrectly because `bdl_game_logs` (the source of truth for L5/L10 stats) were:
1. Only synced at 4:25 AM EST via scheduled job
2. NOT included in the initial startup sync
3. NOT refreshed by the adaptive sync engine

This meant that on a fresh deployment or after several hours, the game logs became stale, causing incorrect hit rate percentages.

### Fix Applied
1. **Initial Sync** (`server.py`): Now includes `BDLGameLogsSync` as Step 2/5
2. **Initial Sync** (`server.py`): Checks if game logs are >12 hours old and refreshes them
3. **Adaptive Sync** (`adaptive_sync_engine.py`): Refreshes BDL game logs every 4 hours

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

## Completed (2026-03-26)
- [x] **Front Lines Anomaly Detection** - Applied season average anomaly logic to Front Lines tier
  - All Front Lines picks now use same anomaly detection as War Zone/Safe Haven
  - Filters for middle-ground anomalies (65-79% hit rate)
  - Mutual exclusivity verified: 0 overlaps between War Zone, Safe Haven, and Front Lines
  - Fixed anomaly flags (`is_anomaly`, `is_goblin_anomaly`) to correctly mark all season-based anomalies

- [x] **Parlay Builder Fix** - Parlays now use tier picks as source of truth
  - Rewrote `get_parlay_builder()` and `get_goblin_recon()` to build parlays dynamically from tier endpoints
  - Safe Haven parlays now use `get_goblin_vault()` picks
  - War Zone parlays now use `get_war_zone()` picks
  - **NEW SORTING LOGIC (all tiers)**: Hit Rate → Season Margin → Probability Score
    - When hit rates match, picks with larger season margin (season_avg - line) rank higher
    - Bigger margin = bigger oddsmaker mistake = better value
  - Verified: Towns+Cardwell lead Daily Double, White+Brunson lead Quick Strike

- [x] **Photo Caching Optimization** - Aggressive caching for player photos
  - Added class-level `_photo_cache` dict for instant lookups (no DB queries per pick)
  - Pre-loads all player photo data on first request via `_load_photo_cache()`
  - Image proxy now caches up to 5000 images in memory
  - Returns placeholder on timeout instead of 502 errors
  - Cache-Control headers set to 30 days
  - Photos now loading correctly in all sections

- [x] **Probability Score Caching** - Eliminated N+1 query problem
  - Added `_dvp_cache` and `_badges_cache` class-level dicts in `ProbabilityScoreService`
  - Pre-loads DvP rankings and player badges on first request
  - All tier methods (War Zone, Safe Haven, Front Lines) pre-load caches at start
  - Response times improved: Safe Haven 325ms→175ms (46% faster), War Zone 179ms→120ms (33% faster)

- [x] **Player Detail Loading Optimization** - Fixed 10-20s load times → <200ms
  - **Board Membership Cache**: Added 60-second TTL cache to avoid re-computing all 3 boards for each player card click
  - **Vision AI Circuit Breaker**: Skips slow/failing Gemini API calls for 30 seconds after failure
  - **Vision AI Timeout**: 3-second timeout prevents hanging on slow API responses
  - **Vision AI Summary Cache**: Caches AI summaries for 1 hour
  - **Fixed Model**: Changed from non-existent `gemini-3.1-flash-lite-preview` to `gemini-flash-lite-latest`
  - Result: Player cards now load in ~200ms, Vision AI summaries generate in ~1-2 seconds

- [x] **Command Hub Fix** - Search overlay was blocking clicks on results
  - Removed problematic `fixed inset-0 z-40` overlay from CommandSearch.jsx
  - Added proper `useRef` + `handleClickOutside` event listener for closing dropdown
  - Search, player selection, prop adding, and simulation all working

- [x] **Game Log Bar Chart** - PrizePicks-style visual data representation
  - Created `/app/frontend/src/components/dashboard/GameLogBarChart.jsx`
  - Displays last 10 games as vertical bars with opponent abbreviations
  - GREEN bars bust through target line (hits), RED bars stop short with gap (misses)
  - Values displayed on top of each bar, opponent abbreviations at bottom
  - Averages (SZN AVG, L10 AVG, L5 AVG) shown as white text above chart
  - Full-width layout: chart stretches across entire row width
  - Taller prop boxes (py-4, height=110px) for better visual ratio
  - Games ordered: oldest on left → newest on right

- [x] **AI Vision Prompt Update** - More human, conversational tone
  - Updated `/app/backend/services/vision_summary_service.py`
  - Changed from formal "elite sports betting analyst" to "sharp sports bettor sharing a quick take with a friend"
  - Natural language structure: THE PLAY, THE MATCHUP, THE CATCH
  - "Keep it tight. No fluff. Talk like a real person, not a robot."

- [x] **Blowout Risk Warning System** - Protects against reduced minutes
  - Created `/app/backend/services/standings_service.py`
  - Fetches team standings from BallDontLie `/standings` endpoint
  - Calculates win % differential between teams
  - Risk levels: HIGH (25%+ diff), MEDIUM (18-25%), LOW (10-18%), NONE (<10%)
  - Warning displayed in Intel Suite modal when blowout risk is HIGH or MEDIUM
  - AI Vision prompt includes blowout context for analysis
  - Integrated into `/app/backend/routes/cached_data.py` intel_suite response

## Backlog
- [ ] Add tooltips for context badges (P1)
- [ ] Add UI for War Zone score breakdown (P1)
- [ ] Production deployment sync (P0) - Live server severely out of sync with current codebase
- [ ] Google/Apple OAuth
- [ ] Stripe payments
- [ ] Automate distraction/deep_water badges

## Completed (2026-03-27)

- [x] **P0 - Roster API Semantic Separation** - Created distinct, clearly-named endpoints for roster data
  - `/api/roster/full-active` - Full current NBA roster (~548 players) from Master Hub
  - `/api/roster/mapped` - Players with full system support (543 with BDL + stats = 99.1% coverage)
  - `/api/roster/live-today` - Players with active props today (~141 players)
  - `/api/roster/status` - Summary with counts for all three roster types
  - Added frontend DataService functions: `fetchFullActiveRoster()`, `fetchMappedRoster()`, `fetchLiveTodayRoster()`, `fetchRosterStatus()`
  - Deprecated old `/v3/roster/players` with warning logs
  - Updated `API_SURFACE.md` with complete documentation

- [x] **P1 - picks_getter_service.py Modularization** - Reviewed and validated existing extractions
  - Confirmed Phase 1-3 complete: `game_utils.py`, `hit_rate_service.py`, `photo_service.py`
  - Confirmed Phase 4 and 7 partial: `player_stats_resolver.py`, `board_formatter.py`
  - Main file remains 2866 lines (orchestrator pattern per Option C)
  - Tier builders (War Zone, Safe Haven, Front Lines) remain in main file due to complex interdependencies

- [x] **P2 - News Ticker Performance** - Verified already optimized
  - Ticker durations already at optimal values (30s live scores, 60s news)
  - GPU acceleration CSS already applied (`will-change: transform`, `backface-visibility: hidden`)
  - No changes needed

### Files Changed (2026-03-27)
- `/app/backend/routes/roster.py` (NEW) - Semantic roster endpoints
- `/app/backend/routes/__init__.py` - Registered new roster router
- `/app/backend/routes/roster_sync.py` - Deprecated `/v3/roster/players`
- `/app/frontend/src/services/DataService.js` - Added roster fetch functions
- `/app/API_SURFACE.md` - Documented new roster endpoints
- `/app/frontend/src/components/ui/BadgePill.jsx` - Added tooltip support for context badges
- `/app/frontend/src/components/dashboard/PlayerDetailPage.jsx` - Updated badge grid to use tooltips
- `/app/backend/services/odds_api_mapper.py` - Fixed photo_url mapping (was reading headshot_url instead of photo_url)
- `/app/backend/services/cached_board_builder_service.py` - Fixed photo enrichment to use photo_url with nba_id fallback

### Bug Fix: Player Headshots Missing (2026-03-27)
**Problem**: `dg_cached_board.photo_url` was null for all players despite `nba_master_hub_2026` having valid URLs.

**Root Cause Analysis**:
1. Odds API Mapper was reading `headshot_url` but master hub stores photos in `photo_url` field
2. Cached board builder was also reading from wrong field (`headshot_url` instead of `photo_url`)
3. **Critical**: `bdl_id` (the primary join key) was stored as `bdl_player_id` but not as `bdl_id`, causing inconsistent field naming

**Fix Applied**:
1. Updated `odds_api_mapper.py` to read `photo_url` (with headshot_url fallback) and include `nba_id`
2. Updated `cached_board_builder_service._create_matched_player()`:
   - Now stores BOTH `bdl_id` AND `bdl_player_id` for compatibility
   - Prefers `photo_url`, falls back to `headshot_url`, then constructs from `nba_id` proxy URL
3. Updated `_create_unmatched_player()` and `_create_matched_player_legacy()` to also include `bdl_id`
4. Rebuilt mapper and triggered full sync

**Result**: 
- 141/154 players now have `bdl_id` in `dg_cached_board`
- 140/154 players now have valid `photo_url`
- Remaining ~14 are rookies without `nba_id` in master hub yet

---

## Production White-Labeling & Lean Payloads (2026-03-28)

### P0 - Production White-Labeling (COMPLETED)

**Changes Made:**
1. **index.html cleanup** (`/app/frontend/public/index.html`):
   - Updated `<meta name="description">` from "A product of emergent.sh" to "PropVision - NBA Player Props Analytics Dashboard"
   - Removed `<script src="https://assets.emergent.sh/scripts/emergent-main.js"></script>`
   - Removed `#emergent-badge` anchor element (entire 45-line block)

### P0 - Lean Payloads (COMPLETED)

**Changes Made:**
1. **MongoDB Projections** (`/app/backend/services/picks_getter_service.py`):
   - Modified `get_cached_board()` to use a lean projection
   - Excludes heavy fields: `bdl_game_logs`, `game_logs`, `advanced_stats`, `baseline_stats`

---

## Mobile UI Fixes (2026-03-28)

### Card Centering & Sizing (COMPLETED)
- Fixed swipe cards not centering in Safe Haven, Front Lines, War Zone sections
- Cards now fill 100% of parent container width
- Added `scroll-snap-stop: always` for proper snap behavior on swipe
- Removed conflicting CSS from `DashboardTactical.css`

### Player Detail Header (COMPLETED)
- Reduced name text size on mobile: `text-base sm:text-2xl`
- Removed PROPS count badge to save space
- Hidden demon/goblin badges on mobile
- Moved season stats to centered row below header

### Bar Chart Heights (COMPLETED)
- Increased min-height from 120px to 140px
- Changed overflow to visible so value labels aren't clipped

### Stat Type Display (COMPLETED)
- Added `formatStatType()` function to convert internal names to abbreviations
- `points_alternate` → `PTS`, `rebounds` → `REB`, etc.

---

## Top Picks - Line Movement Tracking (2026-03-28)

### New Feature: Line Movement Tracker (COMPLETED)

**Problem:** Top Picks section was redundant, just showing picks from other sections.

**Solution:** Track line movements between syncs to show where money is flowing.

**Files Created:**
- `/app/backend/services/line_movement_tracker.py` - Tracks line changes

**How it works:**
1. Each sync records current lines to `line_history` collection
2. Next sync compares to previous lines, detects movements (0.5+ point changes)
3. Movements stored in `line_movements` collection
4. Top Picks shows picks with biggest movements (sorted by absolute change)
5. Falls back to section picks if no movements detected

**Movement Categories:**
- MASSIVE (2+ points): "🚨 MAJOR MOVE"
- SIGNIFICANT (1+ points): "🔥 HOT"  
- MODERATE (0.5+ points): "📈 MOVING"

**Integration:**
- `cached_board_builder_service.py` - Records lines and detects movements after each sync
- `picks_getter_service.py` - Returns trending picks from line movements
- `Dashboard.jsx` - Displays movement badges with line change info

**Status:** Baseline recorded (1804 lines). Will detect real movements on next sync.


---

## UPDATE (2026-03-28): Universal Sync Batching Implemented

### Completed Work
1. **BDL Batched Sync Service** (`bdl_game_logs_sync_batched.py`)
   - Uses `/stats` endpoint with multiple `player_ids[]` parameters (25 players per request)
   - Parallel batch execution with `asyncio.gather` (2 concurrent batches)
   - Bulk MongoDB updates for efficiency
   - Performance: 549 players synced in ~57 seconds (vs 3+ minutes sequential)

2. **Odds API Parallel Fetching** (`odds_sync_service.py` + `odds_api_service.py`)
   - All game events fetched in PARALLEL via `asyncio.gather`
   - Shared HTTP client for connection reuse
   - Performance: 9 events synced in ~7s (vs ~20s sequential)

3. **Integration Points Updated**
   - `server.py` - Scheduled BDL sync now uses batched version
   - `scheduler.py` - Init sync uses batched version  
   - `adaptive_sync_engine.py` - Background polling uses batched version

4. **Dead Code Cleanup**
   - Removed 246 lines of unreachable JIT calculation code from `picks_getter_service.py`
   - File reduced from 3215 to 2969 lines

### Configuration
```python
# BDL Batching
BATCH_SIZE = 25        # Players per API request
PARALLEL_BATCHES = 2   # Concurrent batch requests
RATE_LIMIT_DELAY = 1.0 # Seconds between parallel batch groups

# Odds API
# All events fetched in parallel via asyncio.gather
# Shared httpx.AsyncClient for connection pooling
```

### Performance Results
- BDL: API calls reduced from ~550 to ~22 per full sync
- BDL: Sync duration reduced from ~180s to ~60s (3x faster)
- Odds: 9 parallel event fetches in ~7s vs ~20s sequential
- No rate limiting issues with current configuration

**Status:** COMPLETE - Both BDL and Odds API using parallel/batched fetching

---

## Phase 5: Vision Intel Pre-Caching v2.0 (Dec 2025)

### Problem
Vision Intel Suite took 1+ minute to load due to JIT Gemini calls. Previous implementation enriched ALL 1,300+ props instead of just the ~40 props on tier boards.

### Solution: Board-Driven Vision Intel Enrichment
Completely rewrote `/app/backend/services/vision_intel_enrichment_service.py` with:

1. **Board-Driven Scope**
   - Queries `dg_cached_board` for props with `is_demon=True` or `is_goblin=True`
   - Takes top 20 demons (War Zone) + top 20 goblins (Safe Haven) = 40 picks max
   - Sorted by `h10_rate` (hit rate) descending

2. **60-Count Cap**
   - `MAX_PROPS_PER_CYCLE = 60` hard limit
   - Currently enriches ~40 board-eligible props per cycle

3. **Pre-Cache Priority**
   - Triggered immediately after Board Build completes
   - Wired into `cached_board_builder_service._build_derived_collections()`
   - No separate scheduled job needed

4. **Auto-Cleanup/Rotation**
   - `_mark_stale_intel()` flags props that rotated off boards
   - Stale props return `status: "stale"` in API response

5. **Static Serving (No JIT)**
   - `/api/v3/player-with-badges/` serves ONLY from cache
   - Returns `status: "loading"` if not yet enriched
   - Returns `status: "stale"` if player rotated off boards
   - Returns `status: "ready"` with AI summary for enriched props

### Logic Flow
```
BDL/Odds Sync → Cached Board Build → Tier Classification (is_demon/is_goblin) 
    → Vision Intel Enrichment (top 40 picks) → DB Cache → Frontend Request
```

### Performance
- Before: 60+ seconds (1,300+ props, all JIT)
- After: 5.7 seconds (40 props, pre-cached)
- Response time: <0.2 seconds (all from MongoDB)

### Testing Results
- 40 board-eligible picks enriched
- 20 unique players
- 40 AI summaries generated in 5.68 seconds
- 0 errors

**Status:** COMPLETE - Board-Driven Vision Intel now 100% local-first

---

## CRITICAL FIX (2026-03-26): Safe Haven & Front Lines Vision Intel Missing

### Problem
Safe Haven (`/api/v3/goblin-vault`) and Front Lines (`/api/v3/front-lines`) endpoints were NOT returning Vision Intel data (`vision_summary`, `intel_suite`, `is_vision_enriched`) while War Zone (`/api/v3/war-zone`) worked correctly.

### Root Cause
The MongoDB `$project` stages in `get_goblin_vault_static()` and `get_front_lines_static()` were missing the three vision intel projection fields that were present in `get_war_zone_static()`.

### Fix Applied
Added the missing projection fields to both methods in `/app/backend/services/picks_getter_service.py`:
```python
"vision_summary": "$props.vision_summary",
"intel_suite": "$props.intel_suite",
"is_vision_enriched": "$props.is_vision_enriched"
```

### Verification
All 3 endpoints now return complete vision intel data:
- War Zone: ✅ 20 picks with vision_summary + intel_suite
- Safe Haven: ✅ 20 picks with vision_summary + intel_suite  
- Front Lines: ✅ 20 picks with vision_summary + intel_suite

**Status:** COMPLETE

---

## MAJOR REFACTOR (2026-03-28): AI-Weighted Waterfall Selection & Unified Enrichment

### Problem
- Safe Haven and Front Lines boards showed "empty shell" data (missing usage_ripple, pace_delta, etc.)
- Player overlap between boards (same player appearing on multiple boards)
- No AI-driven scoring to prioritize quality picks
- Inconsistent intel suite depth between boards

### Solution: AI-Weighted Waterfall with Unified Enrichment

#### 1. Vision Score Calculator (`/app/backend/services/vision_score_calculator.py`)
AI-weighted scoring formula (0-100):
- **Hit Rate (L10)**: 60% weight
- **DvP (Defense vs Position)**: 25% weight (Top-5 penalize, Bottom-5 boost)
- **Context Badges**: 15% weight (Positive add, Negative subtract)

#### 2. Board Intelligence Service (`/app/backend/services/board_intelligence_service.py`)
Waterfall selection with NO player overlap:
1. **Safe Haven**: is_goblin=True, L10 HR >= 80%, Vision_Score >= 70 (10 players)
2. **Front Lines**: L10 HR >= 60%, Vision_Score >= 55 (10 players, excluding Safe Haven)
3. **War Zone**: is_demon=True, L10 HR >= 50%, Vision_Score >= 45 (10 players, excluding above)

#### 3. Unified Enrichment Pipeline
- **Same code path** for ALL boards (Safe Haven, Front Lines, War Zone)
- Full intel_suite populated: usage_ripple, matchup_dvp, pace_delta, stability_index, vision_insight, blowout_risk
- Each prop gets `board`, `vision_score`, `is_vision_enriched` fields

#### 4. Player Detail Endpoint (`/api/v3/player-with-badges/`)
- Now fetches pre-cached intel from `dg_cached_board`
- Falls back to on-the-fly calculation only when no pre-cached data exists
- Ensures consistent data display regardless of access path

### Files Changed
- `/app/backend/services/vision_score_calculator.py` (NEW)
- `/app/backend/services/board_intelligence_service.py` (NEW)
- `/app/backend/services/picks_getter_service.py` (Updated static methods)
- `/app/backend/routes/cached_data.py` (Pre-cached intel lookup, trigger endpoint)
- `/app/backend/services/engines/adaptive_sync_engine.py` (Wired new enrichment)

### Verification
```
Safe Haven: 10 unique players, vision_score assigned, full intel_suite ✅
Front Lines: 10 unique players, vision_score assigned, full intel_suite ✅
War Zone: 10 unique players, vision_score assigned, full intel_suite ✅
NO OVERLAP: All 30 players unique across boards ✅
UI Parity: Same intel display for all boards ✅
```

### Manual Trigger
POST `/api/v3/trigger-board-enrichment` - Forces refresh of board intelligence

**Status:** COMPLETE

---

## UPDATE (2026-04-01): VIP Room & Trap Graveyard Implementation

### Problem
The main dashboard was showing ALL picks, including those flagged with Hook Risk or Vegas Bait warnings. Users needed a way to see only clean picks while also being able to browse the flagged picks separately.

### Solution: VIP Room (Clean Picks) + Trap Graveyard (Flagged Picks)

#### Backend Changes (`/app/backend/routes/tiers.py`)
1. **`filter_clean_picks()` function**: Separates clean picks from flagged picks
   - Clean picks: No `hook_risk` and no `suspect_line_bait` in sidecar
   - Returns tuple of (clean_picks, trapped_picks)

2. **VIP Room Filtering on Tier Endpoints**:
   - `/api/v3/safe-haven`, `/api/v3/war-zone`, `/api/v3/front-lines`
   - Now filter out trapped picks by default
   - Returns `vip_filtered: true` and `trapped_count` in response
   - Use `include_traps=true` parameter to get unfiltered data

3. **New `/api/v3/trap-graveyard` Endpoint**:
   - Collects ALL trapped picks across all boards
   - Adds `source_board` field to each pick
   - Returns `board_stats` summary (trapped/total counts per board)
   - Sorts by trap severity (bait first, then hook)

#### Frontend Changes (`/app/frontend/src/pages/Dashboard.jsx`)
1. **Tab Interface**: Added two tabs below Intel Search
   - **VIP Room** (yellow, default): Shows clean picks only
   - **Trap Graveyard** (red): Shows flagged picks with warning badges

2. **`TrapGraveyardSection` Component**: 
   - Displays trapped picks in responsive grid (1-3 columns)
   - Shows board stats summary chips
   - Each card shows Hook Risk / Vegas Bait badges with Mode/Median values
   - Source board label (e.g., "Filtered from Safe Haven")

3. **`useTrapGraveyard` Hook** (`/app/frontend/src/hooks/useLiveOdds.js`):
   - Fetches `/api/v3/trap-graveyard` with 30s polling
   - Preloads player images

#### Test Results
- 14/14 backend tests passed
- All frontend UI tests passed
- 6 trapped picks currently from Safe Haven (Hook Risk + Vegas Bait)

#### Files Changed
- `/app/backend/routes/tiers.py` - VIP filtering + Trap Graveyard endpoint
- `/app/frontend/src/pages/Dashboard.jsx` - Tab UI + TrapGraveyardSection
- `/app/frontend/src/hooks/useLiveOdds.js` - useTrapGraveyard hook

**Status:** COMPLETE

---

## PENDING TASKS

### P1 - War Zone Score Breakdown UI
- Add UI element showing composite score factors (hit rate, DvP bonus, badges)
- Show users WHY a pick ranks highly

### P2 - Automate Context Badges
- `distraction` badge (currently MOCKED)
- `deep_water` badge (currently MOCKED)

### P2 - Future Features
- Google/Apple OAuth integration
- "Copy Parlay" feature
- Stripe payments integration

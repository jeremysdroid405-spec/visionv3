# PickVision - NBA Player Prop Dashboard

## Overview
PickVision is a high-performance NBA Player Prop Dashboard with a "military tech" aesthetic. The application delivers AI-driven betting insights using PropVision Command Post technology.

## Latest Update: 2026-03-16

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
- [ ] Test conflict detection (add Over + Under for same player prop)

### P1 - High Priority
- [ ] Consolidate duplicate player lookup functions into `/app/backend/utils/`
- [ ] Re-run baseline stats sync to populate more players

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
BallDontLie API → master_hub_sync.py → MongoDB (nba_master_hub_2026)
                                            ↓
                                   baseline_stats: {
                                     PTS: {l5_avg, l10_avg, season_avg},
                                     REB: {...},
                                     AST: {...},
                                     PRA: {...}
                                   }
                                            ↓
                                   API Response → Frontend
                                            ↓
                                   TacticalPlayerCard (flat list)
```

---

## Testing Status
- TacticalPlayerCard flat list layout: Verified via screenshots
- L5/L10/SZN display: Shows values when available, "-" when null
- Target-Lock styling: Green highlight + TARGET badge working
- Stat type normalization: P+R → PR confirmed working

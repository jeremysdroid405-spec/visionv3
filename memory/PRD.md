# PickVision - NBA Player Prop Dashboard

## Overview
PickVision is a high-performance NBA Player Prop Dashboard with a "military tech" aesthetic. The application delivers AI-driven betting insights using PropVision Command Post technology.

## Latest Update: 2026-03-16

### Full Intel Suite Display Fix ✅ (NEW - March 16, 2026)
- **Fixed:** Full Intel Suite now correctly displays ONLY for Radar/OBJECTIVE picks
- **Backend:** `/api/command/profile/{player_name}` returns `is_radar: true` for all board player props
- **Frontend:** `TacticalPlayerCard.jsx` uses `prop.is_radar` flag to conditionally render:
  - **Radar picks (is_radar: true):** Full Intel Suite (DvP, Pace Multiplier, Stability Index)
  - **Non-radar props (is_radar: false):** Basic stats only (L5/L10/Season)
- **Non-board players:** Receive `is_on_board: false` with empty `lines: []` and message
- **Files Updated:**
  - `/app/frontend/src/components/dashboard/CommandPost.jsx` (line 454 - added is_radar to props mapping)
  - `/app/frontend/src/components/dashboard/TacticalPlayerCard.jsx` (line 487 - uses prop.is_radar)

### PropVision Command Post Implementation ✅

#### Tactical Player Card System
- **TacticalPlayerCard Component:** Double-nested interactive military-style cards
- **Prop Arsenal:** Expandable list of all props per player
- **Standard vs Radar:** Visual distinction between regular props and PropVision Objectives
- **Radar Picks:** Glowing neon green border with pulsing Target-Lock icon

#### Stability Index (NEW)
- **High Stability (80-100):** Low variance, consistent performer
- **Moderate (50-79):** Average variance
- **Volatile (0-49):** High variance, boom-or-bust player
- Based on standard deviation / coefficient of variation

#### Intelligence Suite (ONLY for Radar/OBJECTIVE picks)
- L5, L10, and Season Averages
- Usage Ripple™ (e.g., "+8% Volume Shift")
- Live DvP (e.g., "Opponent Rank: #28")
- Pace Multiplier (High-Tempo/Grind-Out/Standard)
- Stability Index badge

#### Tactical Conflict Detection
- Detects mutually exclusive parameters (Over + Under on same player/stat)
- UI "Redacts" conflicting legs with warning message
- "Tactical Conflict: Mutually Exclusive Parameters"
- Simulation blocked until conflicts resolved

#### Military Terminology
- "Infiltration Grade" (replaces Success)
- "Stability Index" (replaces Safety)
- "Objectives" (replaces Picks)
- "Defensive Friction" (replaces DvP)
- "Convergence Rate" (replaces Combined Probability)

### Live Scores & Breaking News Tickers ✅
- **Live Scores Ticker:** Real-time NBA game scores from BallDontLie API
  - Auto-refreshes every 30 seconds
  - Shows team abbreviations, scores, and game status (Q1, Q2, Final, Upcoming)
  - Red "LIVE" badge with pulsing animation
  - Scrolling horizontal ticker with hover-pause
- **Breaking News Ticker:** Injury updates and system status
  - Auto-refreshes every 60 seconds  
  - Shows injury alerts, line movements, and AI feature highlights
  - Amber gradient background with scrolling animation
- **Layout Reorganization:** Most Popular section now at TOP of dashboard

### Quick-Add to Command Post ✅
- **Feature:** One-click button on PickCards to add props directly to the Command Post simulator
- **UI:** Cyan + icon button in card header (only visible when `onQuickAdd` prop provided)
- **Behavior:** Opens Command Post sidebar and automatically adds the prop as a leg
- **Duplicate Prevention:** Checks if leg already exists before adding
- **Toast Notification:** Shows confirmation "Added [Player] to Command Post"

### Intel Search API Integration ✅
- **Fixed:** Intel Search now uses live BallDontLie API instead of filtering empty local cache
- **Endpoint:** `GET /api/command/search?query={name}`
- **Features:** 300ms debounce, loading states, error handling
- **Result:** Can now search full NBA player database regardless of game day availability

### DvP (Defense vs Position) System - FULLY INTEGRATED ✅

#### 1. Live Data Fetching (from BallDontLie API)
- **Endpoint:** `GET /nba/v1/team_season_averages/general?type=opponent`
- **Stats:** `pts_allowed`, `reb_allowed`, `ast_allowed`, `fg3m_allowed`, `blk`, `stl`
- **Storage:** MongoDB `dvp_rankings` collection
- **Refresh:** Daily at 8:00 AM EST via APScheduler
- **API Key:** `BALLDONTLIE_API_KEY` in `.env` (GOAT tier required)

#### 2. Ranking Engine
- Ranks all 30 teams from **1 (Best Defense)** to **30 (Worst Defense)**
- Lower allowed stats = better defense = lower rank

#### 3. Success Multiplier (Statistical_Certainty)
- **Rank >= 25** (Bottom 5 Defense): **+10% boost** (`dvp_certainty_mult: 1.10`)
- **Rank <= 5** (Top 5 Defense): **-15% penalty** (`dvp_certainty_mult: 0.85`)
- **Rank 6-24** (Neutral): No change (`dvp_certainty_mult: 1.0`)

#### 4. AI Briefing Integration
- Vision AI service now includes mandatory DvP context sentence
- Format: "The [Opponent] are ranked #[Rank] against [Position] in [Stat], creating a [High/Low/Medium] friction environment."

#### 5. Dashboard DvP Badge
- Color-coded badge on every player card:
  - **Green** (25-30): Favorable matchup (Bottom 5 Defense)
  - **Yellow** (10-24): Neutral matchup
  - **Red** (1-9): Tough matchup (Top 10 Defense)

#### API Endpoints
- `GET /api/dvp-status` - Service health & configuration
- `GET /api/dvp-rankings` - Full team rankings data
- `GET /api/dvp-analysis/{team}/{stat}?player_position={pos}` - Matchup analysis
- `POST /api/dvp-refresh` - Manual refresh trigger

### Backend Changes
- **Files Updated:**
  - `/app/backend/services/dvp_service.py` - Complete rewrite with live fetching
  - `/app/backend/services/tier_builder_service.py` - DvP integration in scoring
  - `/app/backend/vision_ai_service.py` - DvP context in AI briefings
  - `/app/backend/routes/admin.py` - DvP management endpoints

### Frontend Changes  
- **Files Updated:**
  - `/app/frontend/src/components/dashboard/PlayerCard.jsx` - DvPBadge component
  - `/app/frontend/src/components/dashboard/PickCard.jsx` - DvPBadge component

### Tests
- `/app/backend/tests/test_dvp_service.py` - 25 tests PASSING
- `/app/backend/tests/test_critical_backend.py` - 16 tests PASSING

---

## Previous Updates

### Backend Test Suite ✅
- **File:** `/app/backend/tests/test_critical_backend.py`
- **Tests:** 16 tests across 6 categories - ALL PASSING

### Backend Refactoring Complete ✅
- Deconstructed monolithic `server.py` from 2,619 to 552 lines
- Extracted 81 route handlers into 29 modular files in `/app/backend/routes/`
- Implemented rate limiting and request tracing middleware
- Added API versioning infrastructure and OpenAPI documentation

---

## Architecture

```
/app
├── backend/
│   ├── config/
│   │   ├── api_versioning.py
│   │   └── settings.py
│   ├── middleware/
│   │   ├── rate_limiter.py
│   │   └── tracer.py
│   ├── routes/ (29 modular files)
│   ├── services/
│   │   ├── dvp_service.py (LIVE DvP data)
│   │   ├── tier_builder_service.py (DvP scoring)
│   │   └── ... (26 other services)
│   ├── tests/
│   │   ├── test_critical_backend.py
│   │   └── test_dvp_service.py
│   ├── server.py (552 lines, middleware only)
│   └── vision_ai_service.py (DvP AI briefings)
├── frontend/
│   └── src/components/dashboard/
│       ├── PlayerCard.jsx (DvPBadge)
│       └── PickCard.jsx (DvPBadge)
└── memory/
    └── PRD.md
```

---

## Environment Variables

```env
# BallDontLie API (GOAT tier required)
BALLDONTLIE_API_KEY=your-key-here

# Other existing keys
GOOGLE_API_KEY=...
ODDS_API_KEY=...
TANK01_API_KEY=...
```

---

## Pending Tasks

### P0 - Immediate (Complete)
- [x] Full Intel Suite display fix (only show for Radar/OBJECTIVE picks)
- [x] Conflict Detection engine implementation

### P1 - High Priority
- [ ] Trigger data sync to populate DvP badges on existing picks
- [ ] Helper function consolidation (remove duplicate cache functions from server.py)

### P2/P3 - Future
- [ ] Stripe integration & authentication
- [ ] "Copy Parlay" button
- [ ] "Pro Tier" features
- [ ] Real Google/Apple OAuth
- [ ] Sync Status Dashboard UI
- [ ] `/api/health/services` endpoint

---

## Data Flow

```
BallDontLie API → dvp_service.py → MongoDB (dvp_rankings)
                                        ↓
                               tier_builder_service.py
                               (applies dvp_certainty_mult)
                                        ↓
                               API Response with:
                               - dvp_rank (1-30)
                               - dvp_rank_color (green/yellow/red)
                               - dvp_certainty_mult (0.85/1.0/1.10)
                                        ↓
                               Frontend (DvPBadge component)
```

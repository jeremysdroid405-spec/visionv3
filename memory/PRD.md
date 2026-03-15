# PickVision - NBA Player Prop Dashboard

## Overview
PickVision is a high-performance NBA Player Prop Dashboard with a "military tech" aesthetic. The application delivers AI-driven betting insights by identifying "Demons" (high-payout props) and "Goblins" (safer props).

## Latest Update: 2026-03-15 - Full DvP Integration Complete 🎉

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

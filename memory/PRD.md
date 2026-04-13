# PropVision AI - Product Requirements Document

## Original Problem Statement
Restructure the React/FastAPI betting app to a 100% Local-First Database Model, integrating multi-sport support (NBA/MLB) and an exact MLB 4-Gate evaluation system/UI replica of the NBA side. Ensure the backend accurately computes edge projections, populates hit rates correctly, and outputs professional Gemini Oracle summaries.

## Core Features
- **Multi-Sport Support**: NBA and MLB pipelines with sport-specific dashboards
- **4-Gate Evaluation System**: Safe Haven, Front Lines, War Zone tiering
- **Vision Intel Suite**: Gemini-powered target lock rationales with caching
- **Live Injury Advantage**: High-frequency injury monitoring with Active Prop Gate
- **Sport-Specific Routing**: `/nba` and `/mlb` routes with dedicated dashboards

## Tech Stack
- **Frontend**: React + Tailwind CSS + Shadcn/UI
- **Backend**: FastAPI + Python
- **Database**: MongoDB
- **AI**: Gemini 3.1 Flash-Lite (via Emergent LLM Key)
- **APIs**: The Odds API, BallDontLie API

## Architecture
```
/app
├── frontend/
│   ├── src/pages/NBADashboard.jsx
│   ├── src/pages/MLBDashboard.jsx
│   ├── src/hooks/useLiveInjuries.js
├── backend/
│   ├── services/
│   │   ├── live_injury_micro_sync.py
│   │   ├── mlb_injury_vacuum_service.py
│   │   ├── mlb_vision_intel.py
│   ├── routes/
│   │   ├── ferrari_tiers.py
│   │   ├── injuries.py
│   │   ├── vacuum.py
```

## Key API Endpoints
- `GET /api/v3/ferrari/safe-haven?sport=mlb|nba`
- `GET /api/v3/ferrari/front-lines?sport=mlb|nba`
- `GET /api/v3/ferrari/war-zone?sport=mlb|nba`
- `GET /api/v3/injuries/live`
- `GET /api/v3/vacuum/live-alerts` (NBA)
- `GET /api/v3/mlb/vacuum/live-alerts` (MLB)

## Completed Work

### April 2026
- ✅ **HR Power Bypass Implementation** (E2E Test 3): MLB War Zone Gate 1 now includes explicit bypass for HR props
  - If stat_type == 'HR' and fails standard hit rate check
  - Check L10 HRs >= 2 OR ISO > .200 vs pitcher handedness
  - Forces PASS Gate 1 for power hitters with low base hit rates
  - Logged under `Gate 1 HR Power Bypass (L10 HRs >= 2 or ISO > .200)`

- ✅ **MLB Headshot Sync** (P2 Complete)
  - Service: `/app/backend/services/mlb_headshot_sync.py`
  - Endpoints: `POST /api/v3/mlb/headshots/sync`, `GET /api/v3/mlb/headshots/status`
  - Coverage: 96.7% (771/797 players with official MLB CDN headshots)
  - Storage: `/app/frontend/public/images/mlb_headshots/{mlb_id}.png`

- ✅ **Forward-Testing Infrastructure** (P2 Complete)
  - Service: `/app/backend/services/forward_testing_service.py`
  - Routes: `/app/backend/routes/forward_testing.py`
  - Collections: `forward_test_snapshots`, `forward_test_outcomes`, `forward_test_metrics`
  - Features:
    - Daily prop snapshot capture for all tiers
    - Outcome resolution with hit/miss tracking
    - Performance metrics by sport/tier
    - Calibration reports (predicted vs actual hit rates)
  - Endpoints:
    - `POST /api/v3/forward-test/capture` - Capture daily props
    - `POST /api/v3/forward-test/resolve` - Resolve outcomes
    - `GET /api/v3/forward-test/performance` - Performance summary
    - `GET /api/v3/forward-test/calibration` - Model calibration

### December 2025
- ✅ MLB Vision Intel Suite with Tempo calculations and badge mapping
- ✅ Gemini target lock rationales with MongoDB caching (reduced 57s → instant)
- ✅ Dashboard refactor into NBADashboard.jsx and MLBDashboard.jsx
- ✅ High-frequency live injury micro-sync (60s polling)
- ✅ JIT (Just-In-Time) injury checks before UI transmission
- ✅ Active Prop Gate for NBA and MLB vacuum alerts
- ✅ Fixed MongoDB truthiness evaluation bug in vacuum.py
- ✅ Percentage-Based Hit Rate (Gate 1) with dynamic sample size floor
- ✅ NBA War Zone logic updates (DK odds +140, Demon Override, Volatility Fast-Track)
- ✅ Fixed NoneType sorting crash in ferrari_tier_service.py
- ✅ Fixed NBA Vision Intel hallucination (stripped L3 from prompts)
- ✅ MLB GET endpoints strictly read from Oracle Apex collections
- ✅ Updated NBA Vision Intel prompt to "Lead NBA Scout" persona

## Prioritized Backlog

### P0 - Critical (In Progress)
- ✅ HR Power Bypass (Test 3) - COMPLETED

### P1 - High Priority
- [ ] Google OAuth integration (Emergent-managed)
- [ ] Stripe payments integration

### P2 - Medium Priority
- ✅ MLB headshot sync - COMPLETED (96.7% coverage)
- ✅ Forward-Testing Infrastructure - COMPLETED
- [ ] Wind Tunnel weather API integration
- [ ] Apple OAuth integration

### P3 - Low Priority / Refactoring
- [ ] Refactor ferrari_tiers.py (3,500+ lines) into modular handlers

## 3rd Party Integrations
| Service | Status | Key Source |
|---------|--------|------------|
| Gemini 3.1 Flash-Lite | ✅ Active | Emergent LLM Key |
| The Odds API | ✅ Active | User API Key |
| BallDontLie API | ✅ Active | User API Key |
| Google OAuth | ⏳ Pending | Emergent-managed |
| Stripe | ⏳ Pending | Pod test keys |

## Known Technical Debt
- `ferrari_tiers.py` exceeds 3,500 lines and needs modularization
- MLB injuries sourced from BDL API (consider ESPN fallback)

## Testing Status
- Active Prop Gate: ✅ Verified via curl
- Vacuum Alerts NBA: ✅ Returns 9 alerts
- Vacuum Alerts MLB: ✅ Returns 0 alerts (expected when no beneficiaries have props)

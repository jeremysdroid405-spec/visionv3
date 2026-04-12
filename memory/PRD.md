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

### December 2025
- ✅ MLB Vision Intel Suite with Tempo calculations and badge mapping
- ✅ Gemini target lock rationales with MongoDB caching (reduced 57s → instant)
- ✅ Dashboard refactor into NBADashboard.jsx and MLBDashboard.jsx
- ✅ High-frequency live injury micro-sync (60s polling)
- ✅ JIT (Just-In-Time) injury checks before UI transmission
- ✅ Active Prop Gate for NBA and MLB vacuum alerts
- ✅ Fixed MongoDB truthiness evaluation bug in vacuum.py

## Prioritized Backlog

### P0 - Critical (In Progress)
- None currently

### P1 - High Priority
- [ ] Google OAuth integration (Emergent-managed)
- [ ] Stripe payments integration

### P2 - Medium Priority
- [ ] MLB headshot sync
- [ ] Forward-Testing Infrastructure (automated daily prop capture)
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

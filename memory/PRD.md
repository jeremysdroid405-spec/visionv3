# PropVision - Best Bet Finder PRD

## Original Problem Statement
Restructure the React/FastAPI betting app to a 100% Local-First Database Model, integrating multi-sport support (NBA/MLB) and an exact MLB 4-Gate evaluation system/UI replica of the NBA side. Ensure the backend accurately computes edge projections, populates hit rates correctly, and outputs professional Gemini Oracle summaries.

## What's Been Implemented

### Session: April 12, 2026

#### MLB Vision Intel Suite
- Fixed frontend display of Vision Intel & VK Projections by ensuring `intel_suite` is preserved in prop data merging
- Enriched Tempo calculations with batting order (inferred from position) and team OBP rank for varied values (+8%, -5%)
- Fixed MLB badges to use correct BADGE_REGISTRY keys (`pure_contact`, `whiff_wizard`, etc.)
- Updated Gemini prompt to generate **gritty scout-style Target Lock Rationale** with betting slang
- Implemented **30-minute Gemini cache** in MongoDB for fast responses

#### Frontend Dashboard Refactor
- Created `/app/frontend/src/pages/NBADashboard.jsx` - NBA-specific route wrapper
- Created `/app/frontend/src/pages/MLBDashboard.jsx` - MLB-specific route wrapper
- Updated `App.js` with `/nba`, `/mlb`, `/demo/nba`, `/demo/mlb` routes
- SportSwitcher now navigates to sport-specific URLs

#### Live Injury Micro-Sync System
- Created `/app/backend/services/live_injury_micro_sync.py` - High-frequency injury polling (60s interval)
- Added `/api/v3/injuries/live` endpoint for real-time injury data
- Added `/api/v3/injuries/live/sync` endpoint for manual sync trigger
- Implemented **JIT (Just-In-Time) injury checks** before tier finalization:
  - OUT/DOUBTFUL players removed from tiers
  - DTD/GTD players flagged with reduced board scores
- Created `/app/frontend/src/hooks/useLiveInjuries.js` - Frontend polling hook (30s interval)
- NBADashboard and MLBDashboard now include live injury polling

## Architecture

### Backend Services
- `/app/backend/services/live_injury_micro_sync.py` - Injury micro-sync loop
- `/app/backend/services/mlb_vision_intel.py` - Gemini Vision Intel
- `/app/backend/services/mlb_tempo_math.py` - Tempo calculations

### Frontend Pages
- `/app/frontend/src/pages/Dashboard.jsx` - Main dashboard (sport-agnostic)
- `/app/frontend/src/pages/NBADashboard.jsx` - NBA route wrapper
- `/app/frontend/src/pages/MLBDashboard.jsx` - MLB route wrapper

### Key API Endpoints
- `GET /api/v3/injuries/live` - Live injury data (30-60s polling)
- `POST /api/v3/injuries/live/sync` - Trigger manual sync
- `GET /api/v3/ferrari/safe-haven?sport=mlb` - MLB Safe Haven (cached)
- `GET /api/v3/ferrari/front-lines?sport=mlb` - MLB Front Lines (cached)
- `GET /api/v3/ferrari/war-zone?sport=mlb` - MLB War Zone (cached)

## Upcoming Tasks (P1-P2)
- [ ] Integrate Google OAuth (via Emergent-managed auth)
- [ ] Implement Stripe for payments
- [ ] Start the injury micro-loop on app startup (background task)

## Future/Backlog (P3)
- [ ] MLB headshot sync
- [ ] Forward-Testing Infrastructure (automated daily prop capture)
- [ ] Weather API (Wind Tunnel badge) integration

## 3rd Party Integrations
- **Gemini 3.1 Flash** - Vision Intel summaries (user's API key)
- **The Odds API** - Prop data fetching (user's API key)
- **BallDontLie API** - MLB game logs (user's API key)

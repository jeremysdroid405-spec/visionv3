# PropVision - Best Bet Finder PRD

## Original Problem Statement
Restructure the React/FastAPI betting app to a 100% Local-First Database Model, integrating multi-sport support (NBA/MLB) and an exact MLB 4-Gate evaluation system/UI replica of the NBA side. Ensure the backend accurately computes edge projections, populates hit rates correctly, and outputs professional Gemini Oracle summaries.

## What's Been Implemented

### Session: April 12, 2026
- **MLB Vision Intel Suite Fix**: Fixed frontend rendering of `vision_intel` and `vk_predicted` by ensuring `intel_suite` is preserved in prop data merging
- **Tempo Enrichment**: Updated tempo calculation to infer batting order from position and estimate team OBP rank for varied tempo values (+8%, -5%, etc.)
- **MLB Context Badges**: Fixed badge system to use correct BADGE_REGISTRY keys (`pure_contact`, `barrel_master`, `workhorse`, `whiff_wizard`, etc.)
- **Gritty Scout-Style Target Lock Rationale**: Updated `MLB_VISION_INTEL_BATCH_PROMPT` in `mlb_vision_intel.py` to generate human-sounding scouting reports with baseball betting slang ("smash spot", "riding the hot hand", "printing money", etc.)
- **Gemini Batch Caching**: Implemented 30-minute cache for Gemini-enriched MLB props to avoid regenerating on every request. Cache stored in MongoDB collections (`mlb_gemini_cache_safe_haven`, `mlb_gemini_cache_front_lines`, `mlb_gemini_cache_war_zone`)

## Architecture

### Backend (FastAPI)
- `/app/backend/routes/ferrari_tiers.py` - Main tier API endpoints with caching
- `/app/backend/services/mlb_vision_intel.py` - Gemini Vision Intel service with gritty scout prompt
- `/app/backend/services/mlb_tempo_math.py` - Tempo multiplier calculations

### Frontend (React)
- `/app/frontend/src/components/dashboard/PlayerDetailPage.jsx` - Vision Intel Suite modal
- `/app/frontend/src/components/dashboard/UniversalPlayerCard.jsx` - Pick cards

### Key API Endpoints
- `GET /api/v3/ferrari/safe-haven?sport=mlb` - Safe Haven tier (cached)
- `GET /api/v3/ferrari/front-lines?sport=mlb` - Front Lines tier (cached)
- `GET /api/v3/ferrari/war-zone?sport=mlb` - War Zone tier (cached)

## Upcoming Tasks (P1-P2)
- [ ] Refactor `Dashboard.jsx` into `NBADashboard.jsx` and `MLBDashboard.jsx`
- [ ] Integrate Google OAuth (via Emergent-managed auth)
- [ ] Implement Stripe for payments

## Future/Backlog (P3)
- [ ] MLB headshot sync
- [ ] Forward-Testing Infrastructure (automated daily prop capture)
- [ ] Weather API (Wind Tunnel badge) integration

## 3rd Party Integrations
- **Gemini 3.1 Flash** - Vision Intel summaries (user's API key)
- **The Odds API** - Prop data fetching (user's API key)
- **BallDontLie API** - MLB game logs (user's API key)

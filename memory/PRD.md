# PropVision MLB Betting App - Product Requirements Document

## Original Problem Statement
Restructure the React/FastAPI betting app to a 100% Local-First Database Model, integrating multi-sport support (NBA/MLB) and an exact MLB 4-Gate evaluation system/UI replica of the NBA side.

## Core Requirements
- BallDontLie (BDL) API integration for player stats
- Automated daily prop capture (Forward-Testing Infrastructure)
- Google/Apple OAuth integration
- Stripe payments implementation
- Dashboard.jsx refactoring

## Architecture

### Backend Stack
- FastAPI (Python 3.11)
- MongoDB (Motor async driver)
- Gemini 3.1 Flash-Lite for Vision Intel

### Key Services
- `/app/backend/services/mlb_master_sync.py` - Master orchestrator for MLB sync pipeline
- `/app/backend/services/bdl_splits_cache.py` - BDL batch pre-fetcher
- `/app/backend/services/mlb_cached_board_builder.py` - Prop enrichment with lineup_status
- `/app/backend/services/mlb_oracle_apex_service.py` - Tier evaluation (Safe Haven 2.0, Front Lines, War Zone)

### Key Endpoints
- `POST /api/mlb/sync/master` - Full MLB pipeline execution
- `GET /api/v3/ferrari/safe-haven?sport=mlb` - Safe Haven tier picks

## Data Models

### lineup_status (NEW - 2026-04-13)
String field replacing boolean `is_lineup_confirmed`:
- `"CONFIRMED"` - Player in today's BDL lineup
- `"PROJECTED"` - Recent game activity (last 5 days), no lineup yet
- `"BENCHED"` - Team has lineup but player not included
- `"UNKNOWN"` - No lineup data or recent activity

### Safe Haven 2.0 Actuary Gate
4-Phase qualification pipeline:
1. Phase 1: Baseline Gates (lineup_status, weather, L20 rate, CV)
2. Phase 2: Internal Math (PropVision True Probability)
3. Phase 3: Actuary Gate - Kills props where PropVision Edge <= Casino Required Win Rate
4. Phase 4: Output sorting by board_score

## Completed Features (2026 Season)

### April 13, 2026
- [x] Implemented `lineup_status` string field in data pipeline
- [x] Updated Safe Haven Phase 1 gate to allow CONFIRMED/PROJECTED
- [x] Safe Haven 2.0 Actuary Gate verified working (63 props killed by Goblin Tax check)
- [x] MLB Master Sync pipeline functioning correctly

### Previous Sessions
- [x] MLB Master Sync orchestrator (`/api/mlb/sync/master`)
- [x] BDL batch prefetching (prevents rate limits)
- [x] CV decimal scale normalization
- [x] Vision Intel batch processing (single Gemini call)
- [x] Props comparison export (6,677 props)

## Pending Issues

### P0 - Critical
- None currently

### P1 - High Priority  
- Front Lines tier still uses old `is_lineup_confirmed` (user to decide if update needed)
- Google/Apple OAuth integration
- Stripe payments integration

### P2 - Medium Priority
- Wind Tunnel weather API integration
- Refactor `ferrari_tiers.py` (>2000 lines)
- Refactor `Dashboard.jsx`

## 3rd Party Integrations
- Gemini 3.1 Flash-Lite (Google GenAI SDK) - Emergent LLM Key
- The Odds API - User API Key required
- BallDontLie API - User API Key required

## Testing Credentials
- Use "Demo Mode" button on frontend login page

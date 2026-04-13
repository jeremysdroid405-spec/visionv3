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
- `/app/backend/services/mlb_oracle_apex_service.py` - Tier evaluation (Safe Haven 2.0 Predictive, Front Lines, War Zone)

### Key Endpoints
- `POST /api/mlb/sync/master` - Full MLB pipeline execution
- `GET /api/v3/ferrari/safe-haven?sport=mlb` - Safe Haven tier picks

## Safe Haven 2.0 - Predictive Actuary Model (April 13, 2026)

### Pipeline
1. **GOBLIN-only filter** - Rejects Demons and Standard props
2. **Lineup Status Gate** - Allows CONFIRMED/PROJECTED, rejects BENCHED/UNKNOWN
3. **Dynamic Hit Rate** - Uses `(hits / actual_games_played) * 100` (60% floor)
4. **CV Gate** - Maximum 0.70 for consistency

### Predictive Actuary Gate
```python
# Market Implied Probability from DK Odds
if dk_odds < 0:
    market_prob = (abs(dk_odds) / (abs(dk_odds) + 100)) * 100
else:
    market_prob = 50.0

# PropVision True Probability (50/50 blend)
propvision_true_prob = (market_prob * 0.50) + (true_hit_rate * 0.50)

# True Edge vs Casino
casino_req_rate = get_pp_required_win_rate(dk_odds, prop_type)
true_edge = propvision_true_prob - casino_req_rate

# Kill Switch
if true_edge <= 0.0:
    continue
```

### Board Score Formula
```python
board_score = (true_edge * 3.0) - (cv * 15)
```

## Data Models

### lineup_status (NEW - April 13, 2026)
String field replacing boolean `is_lineup_confirmed`:
- `"CONFIRMED"` - Player in today's BDL lineup
- `"PROJECTED"` - Recent game activity (last 5 days), no lineup yet
- `"BENCHED"` - Team has lineup but player not included
- `"UNKNOWN"` - No lineup data or recent activity

## Completed Features (2026 Season)

### April 13, 2026
- [x] Implemented Predictive Actuary Gate with Market/HitRate blend
- [x] Market Implied Probability calculation from DK odds
- [x] PropVision True Probability (50/50 market + hit rate blend)
- [x] New board score formula: (true_edge * 3.0) - (cv * 15)
- [x] 109 Goblins now qualify (vs 0 with pure required-rate model)
- [x] Top picks have +8% to +10.8% true edge

### Earlier April 13, 2026
- [x] Implemented `lineup_status` string field in data pipeline
- [x] Updated Safe Haven Phase 1 gate to allow CONFIRMED/PROJECTED
- [x] GOBLIN-only strict filter (rejects Demons/Standard)
- [x] Dynamic hit rate using actual games played (not hardcoded 20)

### Previous Sessions
- [x] MLB Master Sync orchestrator (`/api/mlb/sync/master`)
- [x] BDL batch prefetching (prevents rate limits)
- [x] CV decimal scale normalization
- [x] Vision Intel batch processing (single Gemini call)
- [x] Props comparison export (6,677 props)

## Pending Issues

### P1 - High Priority  
- Front Lines tier still uses old `is_lineup_confirmed` (fails all props)
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

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
- `/app/backend/services/mlb_oracle_apex_service.py` - Tier evaluation (Safe Haven 2.0, Front Lines 2.0, War Zone)

### Key Endpoints
- `POST /api/mlb/sync/master` - Full MLB pipeline execution
- `GET /api/v3/ferrari/safe-haven?sport=mlb` - Safe Haven tier picks
- `GET /api/v3/ferrari/front-lines?sport=mlb` - Front Lines tier picks

## Tier Logic (April 13, 2026)

### Safe Haven 2.0 - GOBLIN-Only Premium Stability
**Purpose:** Premium stability board for consistent, low-risk plays

**Gates:**
1. GOBLIN-only prop type filter
2. Lineup Status: CONFIRMED or PROJECTED only
3. Hit Rate >= 60%
4. CV <= 0.70
5. Predictive Actuary Gate (true_edge > 0)

**Board Score:** `(true_edge * 3.0) - (cv * 15)`

### Front Lines 2.0 - Predictive Arbitrage
**Purpose:** Early-morning value hunting for Demon/Goblin arbitrage

**Gates:**
1. Hybrid Lineup: Reject only BENCHED (allows CONFIRMED, PROJECTED, UNKNOWN)
2. Hit Rate >= 55% (lower threshold for Demon hunting)
3. CV <= 0.75
4. Predictive Actuary Gate (true_edge > 0)

**Board Score:** `(true_edge * 4.0) + (true_hit_rate * 0.5) - (cv * 10)`

### Predictive Actuary Gate (Shared Logic)
```python
# Market Implied Probability from DK Odds
if dk_odds < 0:
    market_prob = (abs(dk_odds) / (abs(dk_odds) + 100)) * 100
else:
    market_prob = 50.0

# PropVision True Probability (50/50 blend)
propvision_true_prob = (market_prob * 0.50) + (true_hit_rate * 0.50)

# True Edge vs Casino Required Rate
casino_req_rate = get_pp_required_win_rate(dk_odds, prop_type)
true_edge = propvision_true_prob - casino_req_rate

# Kill Switch
if true_edge <= 0.0:
    continue  # Reject prop
```

## Data Models

### lineup_status
String field for lineup state:
- `"CONFIRMED"` - Player in today's BDL lineup
- `"PROJECTED"` - Recent game activity (last 5 days), no lineup yet
- `"BENCHED"` - Team has lineup but player not included
- `"UNKNOWN"` - No lineup data or recent activity

## Completed Features (April 13, 2026)

### Safe Haven 2.0
- [x] GOBLIN-only strict filter
- [x] Lineup Status gate (CONFIRMED/PROJECTED)
- [x] Dynamic hit rate (actual games played)
- [x] Predictive Actuary Gate with 50/50 blend
- [x] 109 Goblins qualified, 10 picks with +8% to +10.8% true edge

### Front Lines 2.0
- [x] Hybrid Lineup Gate (BENCHED-only rejection) - **FIXED EMPTY BOARD BUG**
- [x] Lower thresholds for Demon hunting (55% HR, 0.75 CV)
- [x] Predictive Actuary Gate
- [x] Arbitrage-weighted board score
- [x] 88 props qualified, finding Demons with +15% to +20% true edge

### Previous Work
- [x] MLB Master Sync orchestrator
- [x] BDL batch prefetching
- [x] CV decimal scale normalization
- [x] Vision Intel batch processing

## Pending Issues

### P1 - High Priority  
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

# PropVision MLB Betting App - Product Requirements Document

## Original Problem Statement
Restructure the React/FastAPI betting app to a 100% Local-First Database Model, integrating multi-sport support (NBA/MLB) and an exact MLB 4-Gate evaluation system/UI replica of the NBA side.

## Architecture

### Backend Stack
- FastAPI (Python 3.11)
- MongoDB (Motor async driver)
- Gemini 3.1 Flash-Lite for Vision Intel

### Key Endpoints
- `POST /api/mlb/sync/master` - Full MLB pipeline execution
- `GET /api/v3/ferrari/safe-haven?sport=mlb` - Safe Haven tier picks
- `GET /api/v3/ferrari/front-lines?sport=mlb` - Front Lines tier picks
- `GET /api/v3/ferrari/war-zone?sport=mlb` - War Zone tier picks

## MLB Tier Logic (April 13, 2026)

### Common: Predictive Actuary Gate
All tiers share this core calculation:
```python
# Market Implied Probability from DK Odds
if dk_odds < 0:
    market_prob = (abs(dk_odds) / (abs(dk_odds) + 100)) * 100
else:
    market_prob = 50.0

# PropVision True Probability (blend varies by tier)
propvision_true_prob = (market_prob * X) + (true_hit_rate * Y)

# True Edge vs Casino Required Rate
casino_req_rate = get_pp_required_win_rate(dk_odds, prop_type)
true_edge = propvision_true_prob - casino_req_rate
```

### Safe Haven 2.0 - GOBLIN-Only Premium Stability
| Setting | Value |
|---------|-------|
| Prop Types | GOBLIN only |
| Lineup | CONFIRMED, PROJECTED |
| Hit Rate | >= 60% |
| CV | <= 0.70 |
| Edge Floor | 0% |
| Blend | 50% Market / 50% HR |
| Board Score | `(true_edge * 3.0) - (cv * 15)` |

### Front Lines 2.0 - Predictive Arbitrage
| Setting | Value |
|---------|-------|
| Prop Types | ALL (Goblins, Demons, Standards) |
| Lineup | CONFIRMED, PROJECTED, UNKNOWN (reject only BENCHED) |
| Hit Rate | >= 55% |
| CV | <= 0.75 |
| Edge Floor | 0% |
| Blend | 50% Market / 50% HR |
| Board Score | `(true_edge * 4.0) + (true_hit_rate * 0.5) - (cv * 10)` |

### War Zone 2.0 - Elite 10 Jackpot Ranker
| Setting | Value |
|---------|-------|
| Prop Types | DEMON only (GOBLINs strictly blocked) |
| Lineup | CONFIRMED, PROJECTED, UNKNOWN (reject only BENCHED) |
| Hit Rate | No strict floor |
| CV | No strict limit |
| **Edge Floor** | **>= 10%** (aggressive minimum) |
| Blend | **30% Market / 70% HR** (heavier on historical) |
| Board Score | `(true_edge * 15.0) + (true_hit_rate * 2.0) - (cv * 5)` |

## Data Models

### lineup_status
- `"CONFIRMED"` - Player in today's BDL lineup
- `"PROJECTED"` - Recent game activity (last 5 days)
- `"BENCHED"` - Team has lineup but player not included
- `"UNKNOWN"` - No lineup data or recent activity

## Completed Features (April 13, 2026)

### Safe Haven 2.0 ✅
- GOBLIN-only, 60% HR, 0.70 CV
- 50/50 blend Predictive Actuary Gate

### Front Lines 2.0 ✅  
- All prop types, 55% HR, 0.75 CV
- Hybrid lineup gate (BENCHED-only rejection)
- Fixed empty board bug

### War Zone 2.0 ✅
- DEMON-only (GOBLINs blocked)
- 10% minimum true_edge (aggressive floor)
- 30/70 blend for higher historical weighting
- Elite 10 cap

## Test Results (April 13, 2026)
```
Safe Haven: 10 picks | Top Edge: +10.6% (CJ Abrams HITS)
Front Lines: 10 picks | Top Edge: +20.0% (Brice Turang WALKS)
War Zone: 10 picks | Top Edge: +28.0% (Brice Turang WALKS)
```

## Pending

### P1 - High Priority  
- Google/Apple OAuth integration
- Stripe payments integration

### P2 - Medium Priority
- Wind Tunnel weather API
- Refactor `ferrari_tiers.py`
- Refactor `Dashboard.jsx`

## 3rd Party Integrations
- Gemini 3.1 Flash-Lite - Emergent LLM Key
- The Odds API - User API Key
- BallDontLie API - User API Key

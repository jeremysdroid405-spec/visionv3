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

## UNIFIED MATH SYSTEM (April 13, 2026)

### Master Probability Function
ALL tiers use `calculate_master_probability()` for consistent edge calculation:

```python
def calculate_master_probability(dk_odds, true_hit_rate, prop_type):
    # Market Implied Probability from DK Odds
    if dk_odds and dk_odds < 0:
        market_prob = (abs(dk_odds) / (abs(dk_odds) + 100)) * 100
    else:
        market_prob = 50.0
    
    # MASTER 50/50 BLEND (same across ALL tiers)
    propvision_true_prob = (market_prob * 0.50) + (true_hit_rate * 0.50)
    
    # True Edge vs Casino
    casino_req_rate = get_pp_required_win_rate(dk_odds, prop_type)
    true_edge = propvision_true_prob - casino_req_rate
    
    return {...}
```

### Tier Differentiation by CONTENT (Not Math)

| Tier | Prop Types Allowed | Edge Floor | HR Floor | CV Max |
|------|--------------------|------------|----------|--------|
| **Safe Haven** | GOBLIN only | 0% | 60% | 0.70 |
| **Front Lines** | GOBLIN + STANDARD (NO Demons) | 0% | 55% | 0.75 |
| **War Zone** | DEMON + High-Odds Standard (NO Goblins) | 10% | None | None |

### Deduplication Logic
- Props are filtered by tier based on prop_type
- A GOBLIN can appear in Safe Haven AND Front Lines (same True Edge)
- A DEMON can ONLY appear in War Zone (blocked from Front Lines)
- A STANDARD can appear in Front Lines AND War Zone (if high-odds)

## Board Score Formulas

```python
# Safe Haven - Stability focused
board_score = (true_edge * 3.0) - (cv * 15)

# Front Lines - Arbitrage focused  
board_score = (true_edge * 4.0) + (true_hit_rate * 0.5) - (cv * 10)

# War Zone - Jackpot focused
board_score = (true_edge * 15.0) + (true_hit_rate * 2.0) - (cv * 5)
```

## Test Results (April 13, 2026)

### Consistency Verification
Kyle Schwarber - BATTER_STRIKEOUTS [GOBLIN]:
- Safe Haven: TRUE EDGE: +10.0% ✅
- Front Lines: TRUE EDGE: +10.0% ✅ (IDENTICAL)

### Tier Isolation
- Safe Haven: 41 qualified (ALL Goblins)
- Front Lines: 54 qualified (54 Goblins, 0 Standards, 0 Demons)
- War Zone: 35 qualified (35 Demons, 0 Standards, 0 Goblins)

## Data Models

### lineup_status
- `"CONFIRMED"` - Player in today's BDL lineup
- `"PROJECTED"` - Recent game activity (last 5 days)
- `"BENCHED"` - Team has lineup but player not included
- `"UNKNOWN"` - No lineup data or recent activity

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

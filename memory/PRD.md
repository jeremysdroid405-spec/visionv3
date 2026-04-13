# PropVision MLB Betting App - Product Requirements Document

## Original Problem Statement
Restructure the React/FastAPI betting app to a 100% Local-First Database Model with multi-sport support (NBA/MLB) and a 4-Gate evaluation system.

## ELITE TOP 10 SORTING ENGINE (April 13, 2026)

### Sequential Claim Logic (Exclusivity Guaranteed)
No prop appears in multiple tiers - each tier "claims" its picks in priority order:

```
1. Build QUALIFIED POOL
   - All props pass: Lineup ≠ BENCHED, Weather OK, CV ≤ 0.80, HR ≥ 50%, true_edge > 0

2. WAR ZONE Claims FIRST (High-Alpha)
   - Content: Demons + Standards with DK > +100
   - Filter: true_edge ≥ 10%
   - Sort: true_edge DESC
   - Claim Top 10, REMOVE from pool

3. SAFE HAVEN Claims SECOND (Elite Stability)  
   - Content: Goblins only
   - Filter: HR ≥ 60%, CV ≤ 0.70
   - Sort: propvision_true_prob + true_edge DESC
   - Claim Top 10, REMOVE from pool

4. FRONT LINES Claims LAST (Universal Value)
   - Content: Everything remaining
   - Filter: HR ≥ 55%, CV ≤ 0.75
   - Sort: board_score DESC
   - Claim Top 10
```

### Master Probability Function (Single Source of Truth)
All tiers use `calculate_master_probability()` for consistent edge calculation:

```python
# 50/50 MASTER BLEND
market_prob = (abs(dk_odds) / (abs(dk_odds) + 100)) * 100  # if dk_odds < 0
propvision_true_prob = (market_prob * 0.50) + (true_hit_rate * 0.50)
true_edge = propvision_true_prob - casino_req_rate
```

### Board Score Formulas
```python
# Safe Haven - Stability
sh_board_score = (true_edge * 3.0) - (cv * 15)

# Front Lines - Arbitrage
fl_board_score = (true_edge * 4.0) + (true_hit_rate * 0.5) - (cv * 10)

# War Zone - Jackpot
wz_board_score = (true_edge * 15.0) + (true_hit_rate * 2.0) - (cv * 5)
```

## Test Results (April 13, 2026)

### Verified No Duplicates
```
WAR ZONE: 10 picks (Turang +20.0%, Herrera +20.0% LOCKED here)
SAFE HAVEN: 10 picks (Schwarber, Freeman, CJ Abrams HITS)
FRONT LINES: 10 picks (remaining high-value plays)

Total unique: 30 picks ✅
```

## Key Endpoints
- `POST /api/mlb/sync/master` - Triggers Elite Top 10 sorting
- `GET /api/v3/ferrari/safe-haven?sport=mlb`
- `GET /api/v3/ferrari/front-lines?sport=mlb`
- `GET /api/v3/ferrari/war-zone?sport=mlb`

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

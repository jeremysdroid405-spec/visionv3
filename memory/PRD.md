# PropVision - Product Requirements Document

## Original Problem Statement
Restructure the React/FastAPI betting app to a 100% Local-First Database Model, integrating multi-sport support (NBA/MLB) and an exact MLB/NBA evaluation system. Implement BallDontLie (BDL) API modifiers precisely, establish automated daily prop capture, integrate Google/Apple OAuth, implement Stripe for payments, refactor Dashboard.jsx. Implement a "High-Friction Ensemble" MLB MLR model that acts as a pure physical/performance prediction engine. Ensure Strict MLR output enforcement (high-precision decimals, True L10 Sigma, strictly `null` if advanced data is missing).

## Core Architecture
- **Frontend**: React with Shadcn/UI components
- **Backend**: FastAPI (Python)
- **Database**: MongoDB
- **ML Models**: XGBoost regression for NBA/MLB predictions
- **APIs**: BallDontLie (GOAT Tier), The Odds API

## What's Been Implemented

### Global Variance Synchronization v2.1 (COMPLETE - 4/14/2026)

#### DUAL-ENGINE SYSTEM FOR NBA & MLB:
| Logic Layer | Metric | Window | Purpose |
|-------------|--------|--------|---------|
| **Performance (The Gas)** | Hit Rate | L10 | Captures "Heat" - shooting/hitting streaks |
| **Risk (The Brakes)** | CV / Sigma | L20 | "Stabilized Shield" - dilutes 1-game flukes |

#### NBA Impact:
- Player roles change with injuries. L10 CV spikes on "low usage" games.
- L20 keeps CV grounded in player's TRUE average role.

#### MLB Impact:
- Baseball is king of "fluke zeroes"
- A 0-for-4 night is 5% of L20 sample vs 10% in L10
- Keeps 90% hit rate edges alive by smoothing variance

### MLB System (COMPLETE)

#### 1. MLB Deep Ingestion Service
- 3-Year backfill (2023-2025) using BDL GOAT Tier API
- 14,725 game logs, 380 players with L/R splits

#### 2. MLB Physical Engine v2.1
- **64-Feature XGBoost Model** with L20 Stabilized Shield
- **5 Trained Models**: hits, total_bases, rbis, runs, pitcher_strikeouts
- L20 CV and std_l20 now primary stability metrics

#### 3. Vision Summary Format
```
Park: COL (hitter-friendly, 1.18x) | vs RHP: .285 | L10: 0.8 | σ=0.72
```

### NBA System (COMPLETE)
- 105-feature XGBoost model
- L20 Stabilized Shield variance calculations
- Vault Isolation Architecture

### High Stability Badge
- **Criteria**: L10 HR >= 70% (MLB) or 80% (NBA) AND L20 CV < 0.35 (MLB) or 0.25 (NBA)
- **Meaning**: Player has consistent output AND recent hot streak

## Demo Results (L20 Shield)
```
Gabriel Moreno - HITS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
L10 CV: 1.355  |  L20 CV: 1.162
CV Reduction: 14.2% smoother
Sigma Used: 0.988 (TRUE_L20_STABILIZED_SHIELD)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Geraldo Perdomo - HITS  
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
L10 CV: 2.108  |  L20 CV: 1.496
CV Reduction: 29.1% smoother
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

## Audit Files
- **MLB**: https://best-bet-finder-1.preview.emergentagent.com/mlb_mlr_strict_audit.json
- **NBA**: https://best-bet-finder-1.preview.emergentagent.com/nba_mlr_strict_audit.json

## Key Technical Components
- `/app/backend/services/mlb_physical_engine.py` - 64-feature + L20 Shield
- `/app/backend/services/vk_model_enforcement.py` - L20 variance lookup (v2.1)
- `/app/backend/services/mlb_oracle_apex_service.py` - MLB tier orchestrator
- `/app/backend/services/oracle_apex_service.py` - NBA tier orchestrator

## Pending Tasks

### P1 - High Priority
- Google OAuth integration (Emergent-managed)
- Stripe payments integration (pod test keys)

### P2 - Medium Priority
- Wind Tunnel weather API (Atmospheric data)
- Refactor `ferrari_tiers.py` and `Dashboard.jsx`

## 3rd Party Integrations
- BallDontLie API (BDL_API_KEY)
- The Odds API (user key)
- Gemini 3.1 Flash-Lite (Emergent LLM Key)

---
*Last Updated: April 14, 2026*
*MLB Oracle Apex v2.1 - L20 Stabilized Shield Complete*

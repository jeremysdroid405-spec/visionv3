# PropVision - Product Requirements Document

## Original Problem Statement
Restructure the React/FastAPI betting app to a 100% Local-First Database Model, integrating multi-sport support (NBA/MLB) and an exact MLB/NBA evaluation system. Implement BallDontLie (BDL) API modifiers precisely, establish automated daily prop capture, integrate Google/Apple OAuth, implement Stripe for payments, refactor Dashboard.jsx. Implement a "High-Friction Ensemble" MLB MLR model that acts as a pure physical/performance prediction engine. Ensure Strict MLR output enforcement (high-precision decimals, True L10 Sigma, strictly `null` if advanced data is missing).

## Core Architecture
- **Frontend**: React with Shadcn/UI components
- **Backend**: FastAPI (Python)
- **Database**: MongoDB
- **ML Models**: XGBoost regression for NBA/MLB predictions (64 physical features)
- **APIs**: BallDontLie (GOAT Tier), The Odds API

## What's Been Implemented

### NBA System (COMPLETE)
- 105-feature XGBoost model trained on 5+ years of historical data
- True L10 Standard Deviation for variance calculations
- Strict enforcement: No season_avg fallbacks
- Vault Isolation Architecture: `elite_war_zone`, `elite_front_lines`, `elite_safe_haven`

### MLB System (COMPLETE - Session 4/14/2026)

#### 1. MLB Deep Ingestion Service (`mlb_deep_ingestion.py`)
- 3-Year backfill (2023-2025) using BDL GOAT Tier API
- Endpoints: /stats, /players/splits, /players/{id}/vs
- Ingested: 14,725 game logs, 380 players with L/R splits, 797 total players

#### 2. MLB Physical Engine v2.0 (`mlb_physical_engine.py`)
- **64-Feature XGBoost Model** - Trained on physical inputs only
- **5 Trained Models**: hits, total_bases, rbis, runs, pitcher_strikeouts
- Training samples: ~2,700 per stat

#### 3. Wired to Oracle Apex Service (`mlb_oracle_apex_service.py`)
- Primary: MLBPhysicalEngine (64-feature model)
- Fallback: Legacy MLBVegasKillerModel
- Vision Summary: Human-readable park + splits explanation

### Model Performance
| Stat | MAE | Top Feature |
|------|-----|-------------|
| hits | 0.70 | rhp_slg |
| total_bases | 1.28 | l5_min |
| rbis | 0.57 | matchup_slg |
| runs | 0.54 | l5_min |
| pitcher_strikeouts | 0.75 | contact_rate |

### Park Factor Proof (Coors vs Seattle)
```
HITS (line=1.5) - Gabriel Moreno
Coors Field: pred=1.81, P(OVER)=62.8%, Edge=+2.8%
Seattle: pred=1.40, P(OVER)=45.9%, Edge=-14.1%
Difference: +29.3% boost at Coors, +16.9% P(OVER) spread
```

### Key Technical Components
- `/app/backend/services/mlb_physical_engine.py` - 64-feature MLB Oracle Apex engine
- `/app/backend/services/mlb_deep_ingestion.py` - BDL 3-year data backfill service
- `/app/backend/services/mlb_oracle_apex_service.py` - MLB tier orchestrator (wired to Physical Engine)
- `/app/backend/models/mlb_physical/` - Trained XGBoost models (5 stats)

### Audit Files
- **MLB**: https://best-bet-finder-1.preview.emergentagent.com/mlb_mlr_strict_audit.json (13,245 bytes)
- **NBA**: /app/frontend/public/nba_mlr_strict_audit.json

## Strict Enforcement Rules
1. **NO FALLBACKS**: If BDL L/R splits missing -> return NULL, disqualify prop
2. **HIGH PRECISION**: Predictions like 4.38 K, not rounded integers
3. **SIGMA LINKAGE**: TRUE L10 Standard Deviation from database (0.35 CV floor for MLB)
4. **EDGE CALCULATION**: vk_edge = vk_prob_over - implied_probability

## Physical Brain Inputs (64 Features)
- **Recent Performance (15)**: l3/l5/l10/l20_avg, EWMA trends, momentum
- **L/R Splits PvP (20)**: vs_lhp/vs_rhp avg/slg/obp/k_rate/bb_rate/iso, platoon splits
- **Matchup Specific (3)**: matchup_avg, matchup_slg, matchup_k_rate
- **Home/Away (5)**: home_avg, away_avg, home_away_split
- **Park Factors (6)**: hits/runs/hr/k/tb factors, combined park_factor
- **Opponent (1)**: opp_k_rate
- **Plate Discipline (5)**: overall_k_rate, bb_k_ratio, contact_rate, power_index
- **Variance (6)**: std_l5, std_l10, cv_l5, cv_l10, floor_l10, ceiling_l10

## Database Collections
- `mlb_master_hub_2026` - 797 players with game logs and splits
- `nba_master_hub_2026` - 326 players with NBA data
- `elite_war_zone`, `elite_front_lines`, `elite_safe_haven` - Vault tiers

## Pending Tasks

### P1 - High Priority
- Google OAuth integration (Emergent-managed)
- Stripe payments integration (pod test keys)

### P2 - Medium Priority
- Wind Tunnel weather API (Atmospheric data for MLB)
- Refactor `ferrari_tiers.py` and `Dashboard.jsx`

## 3rd Party Integrations
- BallDontLie API (BDL_API_KEY in backend/.env)
- The Odds API (requires user key)
- Gemini 3.1 Flash-Lite (Emergent LLM Key)

---
*Last Updated: April 14, 2026*
*MLB Oracle Apex v2.0 - 5-Model Ensemble Complete*

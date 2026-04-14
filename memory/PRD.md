# PropVision - Product Requirements Document

## Original Problem Statement
Restructure the React/FastAPI betting app to a 100% Local-First Database Model, integrating multi-sport support (NBA/MLB) and an exact MLB/NBA evaluation system. Implement BallDontLie (BDL) API modifiers precisely, establish automated daily prop capture, integrate Google/Apple OAuth, implement Stripe for payments, refactor Dashboard.jsx. Implement a "High-Friction Ensemble" MLB MLR model that acts as a pure physical/performance prediction engine completely independent of market odds. Ensure Strict MLR output enforcement (high-precision decimals, True L10 Sigma, strictly `null` if advanced data is missing).

## Core Architecture
- **Frontend**: React with Shadcn/UI components
- **Backend**: FastAPI (Python)
- **Database**: MongoDB
- **ML Models**: XGBoost regression for NBA/MLB predictions
- **APIs**: BallDontLie (GOAT Tier), The Odds API

## What's Been Implemented

### NBA System (COMPLETE)
- 105-feature XGBoost model trained on 5+ years of historical data
- True L10 Standard Deviation for variance calculations
- Strict enforcement: No season_avg fallbacks
- Vault Isolation Architecture: `elite_war_zone`, `elite_front_lines`, `elite_safe_haven`
- Z-Score/Normal CDF probability calculations
- Audit exports: `nba_mlr_model_full_export.json`, `nba_vk_model_export.json`, `nba_mlr_strict_audit.json`

### MLB System (IN PROGRESS - Session 4/14/2026)
#### COMPLETED TODAY:
1. **MLB Deep Ingestion Service** (`mlb_deep_ingestion.py`)
   - 3-Year backfill (2023-2025) using BDL GOAT Tier API
   - Endpoints: /stats, /players/splits, /players/{id}/vs
   - Ingested: 14,725 game logs, 374 players with L/R splits

2. **MLB Physical Engine v2.0** (`mlb_physical_engine.py`)
   - 64-feature XGBoost model trained on ingested data
   - Features: L/R splits, Park Factors, Opponent K-Rate, EWMA trends
   - 4 models trained: hits, total_bases, runs, pitcher_strikeouts
   - Training samples: ~2,700 per stat
   - Strict validation: Returns NULL if BDL splits missing

3. **Audit File Generated** (`mlb_mlr_strict_audit.json`)
   - 12,239 bytes
   - Documents all 64 features, park factors, model weights
   - Download: https://best-bet-finder-1.preview.emergentagent.com/mlb_mlr_strict_audit.json

### Key Technical Components
- `/app/backend/services/mlb_physical_engine.py` - MLB Oracle Apex engine
- `/app/backend/services/mlb_deep_ingestion.py` - BDL data backfill service
- `/app/backend/services/vegas_killer_model.py` - NBA 105-feature model
- `/app/backend/services/oracle_apex_service.py` - NBA tier orchestrator
- `/app/backend/services/mlb_oracle_apex_service.py` - MLB tier orchestrator

## Pending Tasks

### P0 - Critical
1. **Wire MLB Physical Engine to mlb_oracle_apex_service.py**
   - Integrate trained models into tier generation
   - Ensure high-precision predictions flow to frontend

2. **RBI Model Training**
   - BDL API returns `rbi` not `rbis` - needs mapping fix in ingestion
   - Re-run ingestion with correct field mapping

### P1 - High Priority
- Google OAuth integration (Emergent-managed)
- Stripe payments integration (pod test keys)

### P2 - Medium Priority
- Wind Tunnel weather API (Atmospheric data for MLB)
- Refactor `ferrari_tiers.py` and `Dashboard.jsx`

## Database Collections
- `mlb_master_hub_2026` - 797 players with game logs and splits
- `nba_master_hub_2026` - 326 players with NBA data
- `elite_war_zone`, `elite_front_lines`, `elite_safe_haven` - Vault tiers

## Model Performance (MLB)
| Stat | Samples | Test MAE | Top Features |
|------|---------|----------|--------------|
| hits | 2,703 | 0.7019 | rhp_slg, matchup_slg, contact_rate |
| total_bases | 2,626 | 1.2766 | l5_min, rhp_slg, platoon_obp |
| runs | 2,703 | 0.5406 | l5_min, rhp_bb_rate, consistency |
| pitcher_strikeouts | 2,703 | 0.7483 | contact_rate, overall_k_rate, power_index |

## 3rd Party Integrations
- BallDontLie API (BDL_API_KEY in backend/.env)
- The Odds API (requires user key)
- Gemini 3.1 Flash-Lite (Emergent LLM Key)

---
*Last Updated: April 14, 2026*

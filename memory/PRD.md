# PropVision - Product Requirements Document

## Original Problem Statement
Restructure the React/FastAPI betting app to a 100% Local-First Database Model, integrating multi-sport support (NBA/MLB). Shift from static features to Automated Feature Engineering (Lasso Regression), integrate a generative AI layer (Gemini) for dynamic scout intelligence, and revamp the UI to display the Vision Intel Suite.

## Core Architecture
- **Frontend**: React with Shadcn/UI components
- **Backend**: FastAPI (Python)
- **Database**: MongoDB
- **ML Models**: Lasso Regression (10 calibrated models, 366 engineered features -> 40 survivors)
- **Cache**: Rolling Cache Architecture (master_active_cache.json)
- **LLM**: Gemini 3 Flash via emergentintegrations

## What's Been Implemented

### Scout Badges UI Restructure (COMPLETE - 4/15/2026)
- Removed inline scout badge pills and vision intel text from PropRow cards
- Added 7 scout badge definitions to BADGE_REGISTRY (hot_streak, floor_lock, lasso_high_edge, high_fidelity_model, soft_matchup, usage_spike, volatility_extreme)
- Created dedicated "Scout Badges" section in Vision Intel Suite modal below Context Badges
- 2-column grid layout with descriptions, tooltips, and active badge highlighting
- Mirrors Context Badges UX: click/hover for detailed tooltip with title, description, impact

### Gemini Scout Intelligence Engine (COMPLETE - 4/14/2026)
- Built `gemini_scout_engine.py` with async Gemini Flash scout summaries
- Concurrent batch processing for ~30 Ferrari Tier props
- Fallback baseline strings if LLM fails

### Vision Intel Suite v2 (COMPLETE - 4/14/2026)
- Lasso v2 wired into JIT enrichment
- 10 conditional vision_intel templates
- Scout badges as string arrays
- Frontend: Lasso projection bar, confidence tier badge, Vision Intel modal

### Data Foundation (COMPLETE - 4/14/2026)
- MLB: 777 active players, 149,989 game logs (3 seasons)
- NBA: 559 players, 112,778 game logs + advanced overlay
- Lasso: 10 models (NBA PTS/REB/AST/3PM/PRA, MLB Hits/TB/RBI/Runs/K)

### Rolling Cache v2.0 (COMPLETE - 4/14/2026)
- APEX-ONLY caching, Strict Board Lockdown (~30 max props)

## Key DB Collections
| Collection | Docs | Purpose |
|-----------|------|---------|
| mlb_master_hub_2026 | 777 | SSOT - 3yr history per player |
| nba_master_hub_2026 | 559 | NBA player hub |
| mlb_cached_board | ~191 | Live MLB prop board |
| dg_cached_board | varies | Live NBA prop board |

## Key API Endpoints
- `GET /api/v3/mlb/player/{player_name}` - Full player data
- `GET /api/v3/player-with-badges/{player_name}` - NBA player data
- `GET /api/v3/mlb/ferrari/safe-haven|front-lines|war-zone` - Ferrari tier picks
- `GET /api/v3/lasso/predict/{sport}/{player}/{stat}?line=X`

## Pending Tasks

### P1 - High Priority
- Google OAuth integration
- Stripe payments integration

### P2 - Medium Priority
- Wind Tunnel weather API integration
- Refactor frontend Dashboard.jsx
- Refactor ferrari_tiers.py (technical debt)

---
*Last Updated: April 15, 2026*

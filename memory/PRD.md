# PropVision PRD

## Original Problem Statement
Restructure the React/FastAPI betting app to a 100% Local-First Database Model, integrating multi-sport support (NBA/MLB) and an exact MLB 4-Gate evaluation system/UI replica of the NBA side. Implement BallDontLie (BDL) API modifiers precisely, establish automated daily prop capture (Forward-Testing Infrastructure), integrate Google/Apple OAuth, implement Stripe for payments, refactor Dashboard.jsx. Implement Safe Haven 2.0 (Actuary Gate) for MLB and apply unified predictive modeling and sorting logic across all tiers.

## Architecture Overview

### Elite Top 10 Sequential Claim Engine
The core engine that ensures exclusive tier assignment across both NBA and MLB:

1. **Unified Master Probability Function** (50/50 Blend):
   ```
   market_prob = (abs(dk_odds) / (abs(dk_odds) + 100)) * 100
   propvision_true_prob = (market_prob * 0.50) + (true_hit_rate * 0.50)
   true_edge = propvision_true_prob - casino_req_rate
   ```

2. **Sequential Claim Logic** (War Zone → Safe Haven → Front Lines):
   - Step A: War Zone claims Demons + High-Odds Standards (true_edge >= 8%)
   - Step B: Safe Haven claims Goblins (HR >= 60%, CV <= 0.35)
   - Step C: Front Lines claims remaining pool (HR >= 50%, CV <= 0.50)
   - Each claimed prop is REMOVED from pool before next tier

3. **Phase 7 Override** (NBA-specific):
   - Runs AFTER Ferrari Phases 1-6 complete
   - Sets `SKIP_LEGACY_TIER_BUILDER = True` to prevent overwrites
   - Hard overwrites tier collections with Elite Top 10 picks

### Dynamic Usage Vacuum Model v3.0 (NEW)
The system for calculating "Live Injury Advantage" beneficiaries:

1. **Dynamic Star Identification** (from BDL Advanced Stats):
   - Primary Alpha: `usage_percentage >= 28%`
   - Secondary Alpha: `usage_percentage 22-28%`
   - Data source: `star_usage_cache` collection (BDL advanced stats)

2. **Dynamic Beneficiary Calculation**:
   - When Alpha is OUT, query teammates sorted by `usage_percentage` DESC
   - Top 3 teammates become Primary/Secondary/Tertiary beneficiaries
   - Enrich with baseline stats from `nba_master_hub_2026`

3. **Boost Application**:
   - Primary: +12% PTS/PRA boost
   - Secondary: +8% PTS/PRA boost
   - Tertiary: +5% PTS/PRA boost

### MLB Lineup Ripple Engine v1.0 (NEW)
The system for calculating "Lineup Ripple" effects in MLB when Lineup Anchors are OUT:

1. **Lineup Anchor Definition**:
   - OPS > .850 OR wRC+ > 125
   - Data source: `mlb_master_hub_2026.advanced_stats.season_stats.2026.batting`

2. **Ripple Calculation**:
   - **PA Bump**: +10% expected PAs for players moving UP in batting order
   - **Protection Penalty**: -5% to hitters directly in front of/behind missing anchor

3. **Boost Application**:
   - Primary: +10% PA bump (moving up 2+ spots)
   - Secondary: +8% PA bump (moving up 1 spot)
   - Tertiary: +5% PA bump
   - Protection Penalty: -5% (adjacent hitters)

4. **Metadata Integration**:
   - `lineup_ripple_adj` added to `intel_suite` for MLB props
   - Tier collections updated with `missing_anchor` and `pa_bump_applied` flags

### Key API Endpoints
- `POST /api/nba/sync/master` - Full NBA pipeline with Phase 7
- `POST /api/mlb/sync/master` - Full MLB pipeline with Lineup Ripple
- `POST /api/v3/ferrari/rebuild` - Ferrari rebuild only
- `GET /api/v3/vacuum/live-alerts` - Live Injury Advantage alerts (NBA Dynamic Model)
- `GET /api/v3/mlb/ripple/alerts` - MLB Lineup Ripple alerts
- `GET /api/v3/mlb/ripple/top-gainers` - Top 3 PA gainers
- `GET /api/v3/mlb/ripple/anchors` - Get Lineup Anchors by team

## Completed Work

### April 14, 2026: MLB Lineup Ripple Engine v1.0 (COMPLETE)

#### Lineup Anchor Definition
- Defined Lineup Anchor as OPS > .850 OR wRC+ > 125
- Dynamic identification from `mlb_master_hub_2026` batting stats

#### Ripple Calculation Implementation
- PA Bump: +10% for primary, +8% secondary, +5% tertiary
- Protection Penalty: -5% for adjacent hitters
- `_calculate_ripple_beneficiaries()` with dynamic beneficiary ranking

#### API Endpoints
- `GET /api/v3/mlb/ripple/alerts` - Lineup ripple alerts
- `GET /api/v3/mlb/ripple/top-gainers` - Top 3 PA gainers
- `GET /api/v3/mlb/ripple/anchors` - Get Lineup Anchors by team
- `POST /api/v3/mlb/ripple/check` - Trigger lineup check

#### Phase 5 Integration
- Added Step 5 to `mlb_master_sync.py` for Lineup Ripple Engine
- Updates tier collections with `lineup_ripple_adj` in `intel_suite`

### April 14, 2026: Dynamic Usage Vacuum Model v3.0 (COMPLETE)

#### Removed Hardcoded Star Lists
- Deprecated `STAR_USAGE_PROFILES` dictionary
- Deprecated `BENEFICIARY_MAPPINGS` dictionary
- Deprecated `PLAYER_AVG_STATS` dictionary

#### Dynamic Star Identification
- `_is_star_player()` now queries `star_usage_cache` for BDL advanced stats
- Falls back to `nba_master_hub_2026.advanced_stats` if needed
- Returns alpha tier classification (primary/secondary)

#### Dynamic Beneficiary Calculation
- `_get_beneficiaries()` queries teammates by `usage_percentage` DESC
- Enriches with `baseline_stats` from `nba_master_hub_2026`
- Calculates real projections with +12%/+8%/+5% boosts

#### API Updates
- `/v3/vacuum/live-alerts` now returns `usage_bump` with boost percentage
- Added `usage_percentage`, `dynamic_calculation` fields
- Frontend correctly displays +12%, +8%, +5% boosts

### April 13-14, 2026: NBA Elite Top 10 Implementation

#### Phase 7 Override (COMPLETE)
- Implemented `nba_master_sync.py` with strict execution order
- Phases 1-6: Ferrari Rebuild (populates ferrari_scored)
- Phase 7: Elite Top 10 Hard Overwrite
- Added `SKIP_LEGACY_TIER_BUILDER` class flag to prevent tier_builder race conditions
- FINAL VERIFICATION confirms: WZ=10, FL=10, SH=0

#### NBA Oracle Apex Service Updates (COMPLETE)
- Added `calculate_nba_master_probability()` function
- Added `get_nba_pp_required_win_rate()` for Goblin Tax curve
- Added `build_elite_top_10_tiers()` method
- Preserves NBA intel: blowout_risk, intel_suite, momentum_data, vacuum_data

### April 12-13, 2026: MLB Elite Top 10 Implementation (COMPLETE)

#### MLB Safe Haven 2.0 (COMPLETE)
- Strictly GOBLIN-only
- Dynamic hit rate calculation (actual games played)
- 60% HR floor, 0.70 CV max
- 50/50 predictive blend

#### MLB Front Lines 2.0 (COMPLETE)
- Hybrid lineup gate (only rejects BENCHED)
- 55% HR floor
- Arbitrage-weighted scoring

#### MLB War Zone 2.0 (COMPLETE)
- Elite 10 model (strictly DEMONs)
- true_edge >= 10.0 required
- Sorted by true_edge DESC

#### Unified Master Probability (COMPLETE)
- Same 50/50 blend formula across all MLB tiers
- Eliminates "different edges for same player" issue

## Pending Tasks

### P1: Integrations
- [ ] Google OAuth (Emergent-managed)
- [ ] Stripe payments (test key available)

### P2: Enhancements
- [ ] Wind Tunnel weather API integration
- [ ] Refactor `ferrari_tiers.py` (technical debt)
- [ ] Refactor `Dashboard.jsx` (technical debt)

## Technical Debt
- `ferrari_tiers.py` needs cleanup
- `Dashboard.jsx` needs refactoring
- DB_NAME inconsistency: code uses 'pick_vision', some scripts used 'propvision'

## Key Files
- `/app/backend/services/nba_master_sync.py` - NBA Elite Top 10 orchestrator
- `/app/backend/services/oracle_apex_service.py` - NBA math and Elite engine
- `/app/backend/services/mlb_oracle_apex_service.py` - MLB Elite engine
- `/app/backend/services/mlb_master_sync.py` - MLB orchestrator with Lineup Ripple
- `/app/backend/services/mlb_lineup_ripple_service.py` - MLB Lineup Ripple Engine v1.0
- `/app/backend/services/injury_vacuum_service.py` - NBA Dynamic Usage Vacuum Model v3.0
- `/app/backend/services/cached_board_builder_service.py` - SKIP_LEGACY_TIER_BUILDER flag
- `/app/backend/routes/mlb_ripple.py` - MLB Ripple API routes

## 3rd Party Integrations
- Gemini 3.1 Flash-Lite (Google GenAI SDK) — uses Emergent LLM Key
- The Odds API — User API Key
- BallDontLie API — User API Key (used to hydrate advanced statistics)

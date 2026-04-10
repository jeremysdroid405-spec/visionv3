# Best Bet Finder - Product Requirements Document

## Original Problem Statement
Restructure the React/FastAPI betting app to a 100% Local-First Database Model, integrating multi-sport support (NBA/MLB) and an MLB 4-Gate evaluation system. Mirror NBA pick card features for MLB: accurately calculating L5/L10 hit rates, properly rendering MLB-specific Vision Intel Scout Badges, numerically sorting props in the UI, and ensuring MLB charts and calculations pull correct, up-to-date 2026 current season game logs from the BallDontLie (BDL) API.

## Core Features

### Completed Features
- [x] Single Source of Truth (SSOT) for hit rates via `mlb_master_hub_2026.bdl_game_logs`
- [x] L5/L10 hit rate sync between Pick Cards and Player Detail pages
- [x] MLB Safe Haven logic (DK <= -240, 3-Gate System for consistency)
- [x] MLB Front Lines logic (Mid-Juice, 3-Gate Pivot Rule)
- [x] MLB War Zone logic (Demons/Alt Lines, Ceiling Protocol with CV > 1.0)
- [x] MLB Ferrari tier fetching hooks (`useMLBSafeHaven`, `useMLBFrontLines`)
- [x] Vision Intel badges (`whiff_wizard`, `volatility_extreme`, `hitters_haven`)
- [x] Park factors and volatility index
- [x] VK Vision Model projections on MLB pick cards
- [x] **5-Season Historical Backfill (2021-2026)** - 140,000 stats, 6,516 players with weighted baselines
- [x] **P0: Vision Intel ObjectId Bug Fix** - Added recursive JSON cleaning
- [x] **P1: Real Statcast Data for Badges** - Badges now use 5-year historical `vk_baselines`

### In Progress
- [ ] MLB headshot sync (~700 players remaining)

### Upcoming/Backlog
- [ ] Gemini-powered Vision Intel (matchup splits, weather, umpire impact)
- [ ] Automated daily prop capture (Forward-Testing Infrastructure)
- [ ] Google/Apple OAuth (Emergent-managed Google Auth)
- [ ] Stripe payment integration
- [ ] Add badge evaluation to tier endpoints (Safe Haven/Front Lines pick cards)

## Technical Architecture

### Backend Services
- `/app/backend/services/mlb_vk_regression.py` - Vegas Killer weighted linear regression
- `/app/backend/services/mlb_vk_historical_backfill.py` - 5-season historical data backfill (2021-2026)
- `/app/backend/services/mlb_sharp_sorting_service.py` - Core gating logic for MLB tiers
- `/app/backend/services/mlb_badge_system.py` - Badge evaluation with real historical data
- `/app/backend/services/mlb_vision_intel_service.py` - Vision Intel with ObjectId cleaning

### Key API Endpoints
- `GET /api/v3/ferrari/safe-haven?sport=mlb`
- `GET /api/v3/ferrari/front-lines?sport=mlb`
- `GET /api/v3/mlb/player/{player_name}` - Returns vk_baselines + badges with historical data
- `POST /api/v3/mlb/vk-regression` - Now works with `vision_intel=true`
- `POST /api/v3/mlb/vk-backfill?seasons=2021,2022,2023,2024,2025,2026` - Trigger historical backfill

### Database Collections (MongoDB - pick_vision)
- `mlb_master_hub_2026`: 777 docs with `vk_baselines` field (5-year weighted data)
- `mlb_historical_logs`: 6,645 docs (raw game logs by player)
- `mlb_live_props`: 8,854 docs
- `mlb_cached_board`: 317 docs (active player props)
- `mlb_ferrari_safe_haven`, `mlb_ferrari_front_lines`, `mlb_ferrari_war_zone`: Ferrari tier picks

### Season Weights for VK Model
```python
SEASON_WEIGHTS = {
    2026: 1.0,
    2025: 0.85,
    2024: 0.7,
    2023: 0.55,
    2022: 0.4,
    2021: 0.25,
}
```

### Badge System Updates (P1)
- `pure_contact`: Uses hits/at_bats baselines with CV-adjusted whiff estimation
- `barrel_master`: Uses HR baseline to boost barrel % estimation  
- `whiff_wizard`: Uses historical K baseline and CV for SwStr% estimation
- `volatility_extreme`: Uses real weighted CV from baselines
- All badges now include `data_source` field: "historical_5yr" or "current_season"
- All badges now include `seasons_data` field showing which years contributed

## Known Issues
- Vision Intel returns ERROR verdicts without GOOGLE_API_KEY (expected behavior)

## 3rd Party Integrations
- BallDontLie (BDL) API - MLB stats and game logs
- Gemini AI (`gemini-3.1-pro-preview`) - Vision Intel (requires User API Key)

## Changelog
- **2026-04-10**: Completed 5-season MLB VK historical backfill (140k stats, 6.5k players)
- **2026-04-10**: Fixed VK regression endpoint (works with vision_intel=true)
- **2026-04-10**: Fixed Vision Intel ObjectId serialization bug (P0)
- **2026-04-10**: Wired real 5-year historical data to MLB badge system (P1)

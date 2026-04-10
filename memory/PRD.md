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

### In Progress
- [ ] Vision Intel ObjectId serialization fix
- [ ] Real Statcast data integration for MLB badges (currently mocked)
- [ ] MLB headshot sync (~700 players remaining)

### Upcoming/Backlog
- [ ] Gemini-powered Vision Intel (matchup splits, weather, umpire impact)
- [ ] Automated daily prop capture (Forward-Testing Infrastructure)
- [ ] Google/Apple OAuth (Emergent-managed Google Auth)
- [ ] Stripe payment integration

## Technical Architecture

### Backend Services
- `/app/backend/services/mlb_vk_regression.py` - Vegas Killer weighted linear regression
- `/app/backend/services/mlb_vk_historical_backfill.py` - 5-season historical data backfill
- `/app/backend/services/mlb_sharp_sorting_service.py` - Core gating logic for MLB tiers
- `/app/backend/services/mlb_badge_system.py` - Badge evaluation (badges partially mocked)

### Key API Endpoints
- `GET /api/v3/ferrari/safe-haven?sport=mlb`
- `GET /api/v3/ferrari/front-lines?sport=mlb`
- `GET /api/v3/mlb/player/{player_name}`
- `POST /api/v3/mlb/vk-regression` (use `?vision_intel=false` until ObjectId fix)
- `POST /api/v3/mlb/vk-backfill` - Trigger historical backfill

### Database Collections (MongoDB - pick_vision)
- `mlb_master_hub_2026`: 777 docs with `vk_baselines` field
- `mlb_historical_logs`: 6,645 docs (raw game logs by player)
- `mlb_live_props`: 8,854 docs
- `mlb_ferrari_safe_haven`, `mlb_ferrari_front_lines`, `mlb_ferrari_war_zone`: Ferrari tier picks
- `mlb_demons`, `mlb_goblins`: Demon/Goblin tier picks

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

## Known Issues
1. **Vision Intel serialization bug** - ObjectId not JSON serializable (use `vision_intel=false`)
2. **Mocked Statcast data** - Badges like `whiff_wizard` use placeholder logic

## 3rd Party Integrations
- BallDontLie (BDL) API - MLB stats and game logs
- Gemini AI (`gemini-3.1-pro-preview`) - Vision Intel (requires user API key)

## Changelog
- **2026-04-10**: Completed 5-season MLB VK historical backfill (140k stats, 6.5k players)
- **2026-04-10**: Fixed VK regression endpoint (works with vision_intel=false)

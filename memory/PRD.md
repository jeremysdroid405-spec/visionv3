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
- [x] **L5/L10 Hit Rate Fix** - Sharp sorting now populates tier collections with hit rates
- [x] **Earned Runs Stat Mapping Fix** - Added "Earned Runs" to STAT_FIELD_MAP

### In Progress
- [ ] MLB headshot sync (~700 players remaining)

### Upcoming/Backlog
- [ ] Gemini-powered Vision Intel (matchup splits, weather, umpire impact)
- [ ] Automated daily prop capture (Forward-Testing Infrastructure)
- [ ] Scheduled sharp-sort job for tier refresh
- [ ] Google/Apple OAuth (Emergent-managed Google Auth)
- [ ] Stripe payment integration

## Technical Architecture

### Backend Services
- `/app/backend/services/mlb_vk_regression.py` - Vegas Killer weighted linear regression
- `/app/backend/services/mlb_vk_historical_backfill.py` - 5-season historical data backfill (2021-2026)
- `/app/backend/services/mlb_sharp_sorting_service.py` - Core gating logic for MLB tiers + hit rate calculation
- `/app/backend/services/mlb_badge_system.py` - Badge evaluation with real historical data
- `/app/backend/services/mlb_vision_intel_service.py` - Vision Intel with ObjectId cleaning

### Key API Endpoints
- `GET /api/v3/ferrari/safe-haven?sport=mlb` - Returns picks with h5_rate/h10_rate
- `GET /api/v3/ferrari/front-lines?sport=mlb` - Returns picks with h5_rate/h10_rate  
- `GET /api/v3/mlb/player/{player_name}` - Returns vk_baselines + badges + game_logs per prop
- `POST /api/v3/mlb/sharp-sort?save_to_db=true` - Rebuilds tier collections with fresh hit rates
- `POST /api/v3/mlb/vk-regression` - Works with `vision_intel=true`
- `POST /api/v3/mlb/vk-backfill?seasons=2021,2022,2023,2024,2025,2026` - Historical backfill

### Database Collections (MongoDB - pick_vision)
- `mlb_master_hub_2026`: 777 docs with `vk_baselines` and `bdl_game_logs`
- `mlb_historical_logs`: 6,645 docs (raw game logs by player)
- `mlb_live_props`: 8,854 docs
- `mlb_cached_board`: 317 docs (active player props)
- `mlb_ferrari_safe_haven`: 5 docs (Safe Haven tier picks with hit rates)
- `mlb_ferrari_front_lines`: 20 docs (Front Lines tier picks)
- `mlb_ferrari_war_zone`: 15 docs (War Zone tier picks)

### Stat Type Field Mapping (SSOT)
```python
STAT_FIELD_MAP = {
    "Hits": "hits",
    "Total Bases": "total_bases",
    "RBIs": "rbis",
    "Runs": "runs",
    "Pitcher Strikeouts": "pitcher_strikeouts",
    "Earned Runs": "earned_runs",  # Added 2026-04-10
    "Earned Runs Allowed": "earned_runs",
    "Hits Allowed": "hits_allowed",
    # ... etc
}
```

## Known Issues
- Vision Intel returns ERROR verdicts without GOOGLE_API_KEY (expected behavior)
- Tier data goes stale if sharp-sort isn't called periodically

## 3rd Party Integrations
- BallDontLie (BDL) API - MLB stats and game logs
- Gemini AI (`gemini-3.1-pro-preview`) - Vision Intel (requires User API Key)

## Changelog
- **2026-04-10**: Completed 5-season MLB VK historical backfill (140k stats, 6.5k players)
- **2026-04-10**: Fixed VK regression endpoint (works with vision_intel=true)
- **2026-04-10**: Fixed Vision Intel ObjectId serialization bug (P0)
- **2026-04-10**: Wired real 5-year historical data to MLB badge system (P1)
- **2026-04-10**: Fixed L5/L10 hit rates on tier pick cards - re-ran sharp-sort with player logs cache
- **2026-04-10**: Added "Earned Runs" to STAT_FIELD_MAP for player detail hit rate analysis

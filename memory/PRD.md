# Best Bet Finder - Product Requirements Document

## Original Problem Statement
Restructure the React/FastAPI betting app to a 100% Local-First Database Model, integrating multi-sport support (NBA/MLB) and an MLB 4-Gate evaluation system with Gemini-powered Oracle summaries.

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
- [x] **5-Season Historical Backfill (2021-2026)** - 140,000 stats, 6,516 players
- [x] **P0: Vision Intel ObjectId Bug Fix**
- [x] **P1: Real Statcast Data for Badges**
- [x] **L5/L10 Hit Rate Fix**
- [x] **Earned Runs Stat Mapping Fix**
- [x] **MLB PropVision Ferrari Pipeline** - Full 4-phase quantitative system
  - Phase 1: Quantitative Sorting Gates (`mlb_tier_sorter.py`)
  - Phase 2: Vision Intel Scout Badges (`mlb_vision_scout.py`)
  - Phase 3: Gemini Oracle Summarizer (`mlb_oracle_summarizer.py`)
  - Phase 4: Save to Ferrari collections
- [x] **Edge Calculation Bug Fix** - Edge = Hit Rate - True Probability (not DK odds)
- [x] **Live MLB Ticker** - BDL scores + ESPN RSS news
- [x] **TARGET-LOCK RATIONALE Player-Specific Summaries (April 11, 2026)** - Summaries now reference actual game logs (last 3), L5/season averages, hot/cold streaks, and CV concerns

### In Progress
- [ ] MLB headshot sync (~700 players remaining)

### Upcoming/Backlog
- [ ] Scheduled daily pipeline execution
- [ ] Weather API integration for Wind Tunnel badge
- [ ] Google/Apple OAuth (Emergent-managed Google Auth)
- [ ] Stripe payment integration
- [ ] Refactor Dashboard.jsx into NBADashboard.jsx and MLBDashboard.jsx

## Technical Architecture

### New Services (Ferrari Pipeline)
- `/app/backend/services/mlb_tier_sorter.py` - Quantitative sorting gates
- `/app/backend/services/mlb_vision_scout.py` - Scout badge evaluation
- `/app/backend/services/mlb_oracle_summarizer.py` - Gemini 3.1 Pro Oracle
- `/app/backend/services/mlb_ferrari_pipeline.py` - Main pipeline orchestrator

### Key API Endpoints
- `POST /api/v3/mlb/ferrari-pipeline?save_to_db=true` - Run full pipeline
- `GET /api/v3/mlb/ferrari-pipeline/top-hrr` - Get top HRR props
- `GET /api/v3/ferrari/safe-haven?sport=mlb`
- `GET /api/v3/ferrari/front-lines?sport=mlb`
- `GET /api/v3/mlb/player/{player_name}`

### Tier Thresholds (Stat-Specific)
```python
SAFE_HAVEN_GATES = {
    "hits": {"max_cv": 0.60, "min_hit_rate": 80, "min_edge": 15, "min_tp": 70},
    "pitcher_strikeouts": {"max_cv": 0.45, "min_hit_rate": 75, "min_edge": 12, "min_tp": 75},
}
FRONT_LINES_GATES = {
    "hits": {"max_cv": 0.85, "min_hit_rate": 65, "min_edge": 10, "min_tp": 58},
}
WAR_ZONE_GATES = {
    "hits": {"min_cv": 1.0, "min_ceiling_rate": 35, "min_edge": 30},
}
```

### Database Collections
- `mlb_ferrari_safe_haven`: Lock-tier picks (currently 5)
- `mlb_ferrari_front_lines`: Value plays (currently 20)
- `mlb_ferrari_war_zone`: Moonshots (currently 15)

## 3rd Party Integrations
- **Gemini 3.1 Pro Preview** - Oracle Summarizer (GOOGLE_API_KEY)
- **BallDontLie API** - MLB stats and game logs

## Changelog
- **2026-04-10**: Completed 5-season MLB VK historical backfill
- **2026-04-10**: Fixed Vision Intel ObjectId serialization bug
- **2026-04-10**: Wired real 5-year historical data to MLB badge system
- **2026-04-10**: Fixed L5/L10 hit rates on tier pick cards
- **2026-04-10**: Added "Earned Runs" to STAT_FIELD_MAP
- **2026-04-10**: **Implemented full MLB PropVision Ferrari Pipeline with Gemini Oracle**
- **2026-04-10**: **Integrated Oracle summaries + Hit Rate Analysis into Vision Intel Suite frontend**
- **2026-04-10**: **Normalized MLB data structure to match NBA fields (vision_intel, intel_score, intel_verdict, etc.)**
- **2026-04-10**: **Fixed PlayerDetailPage prop merge to include ALL Vision Intel fields for MLB**
- **2026-04-10**: **Added "Batter Strikeouts" to STAT_FIELD_MAP - enabled L5/L10 averages and game log charts**
- **2026-04-10**: **MLB Vision Intel Suite now exactly matches NBA structure (L5 AVG, L10 AVG, SEASON AVG, game charts, TARGET-LOCK RATIONALE)**
- **2026-04-11**: **Updated MLB Oracle Summarizer with gritty Lead Scout persona - baseball slang, Statcast references, ABS system context, adversarial tone**
- **2026-04-11**: **Implemented MLB live ticker sync with circuit breakers - ESPN MLB news RSS + BDL MLB games API**
- **2026-04-11**: **Made /live/scores and /live/news endpoints sport-aware (supports ?sport=mlb)**

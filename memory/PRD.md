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
- [x] **TARGET-LOCK RATIONALE Player-Specific Summaries (April 11, 2026)**
  - Rewritten to "Professional Analyst" persona (no betting clichés)
  - Contextual insights: slider RPM, umpire zones, swing planes, platoon splits, humidity
  - Uses Emergent LLM Key with gemini-2.5-flash-lite for reliability
  - Batch processing (10-15 props per API call)
- [x] **Sport-Specific Intel Search (April 12, 2026)**
  - Dashboard Intel Search now passes `currentSport` to `usePlayerSearch` hook
  - MLB mode searches `mlb_master_hub_2026`, NBA mode searches `nba_master_hub_2026`
  - Backend `/api/command/search` accepts `sport` parameter for routing
- [x] **MLB Score Ticker Fix (April 12, 2026)**
  - Added MLB team logos (ESPN CDN) to constants.js
  - Updated LiveScoresTicker to handle MLB status codes (Inning 1, Top 5, etc.)
  - Sport-aware "live" detection for both NBA quarters and MLB innings
- [x] **MLB Live Scores Real-Time Fix (April 12, 2026)**
  - Fixed timezone issue: Now uses US Eastern date for BDL API queries (MLB games are US-based)
  - Properly parsing BDL `/mlb/v1/games` endpoint fields: `status`, `period`, `home_team_data.runs`, `inning_scores`
  - Inning half logic: Calculates "Top"/"Bot" from `away_inning_scores.length` vs `home_inning_scores.length`
  - Status displays: "Top 6", "Bot 9", "Final" with real scores (e.g., NYY 4 @ TB 3 Bot 10)
- [x] **MLB Live Injury Advantage Fix (April 12, 2026)**
  - Switched from ESPN API (returning 0 injuries) to BDL `/mlb/v1/player_injuries` endpoint
  - Now correctly fetches star player injuries (Mookie Betts, Juan Soto, etc.)
  - Displays beneficiary opportunities with AB bumps (Teoscar Hernandez +0.5 AB, etc.)
  - BDL injury status mapping: "10-Day-IL"/"60-Day-IL" → "OUT", "Day-To-Day" → "DTD"
- [x] **CRITICAL: Pipeline Race Condition & Vision Intel Fix (April 12, 2026)**
  - **Atomic Upsert**: Replaced `delete_many()` + `insert_many()` with `bulkWrite(upsert=True)` in `ferrari_tier_service.py`
    - Collections never empty during sync (prevents frontend glitching)
    - Stale picks cleaned up AFTER new picks are written
    - Applied to ALL tiers: Safe Haven, Front Lines, War Zone (NBA + MLB)
  - **Pipeline Reorder**: Vision Intel now runs ONLY on Final Top 10 picks (not pre-selection pool)
    - Guarantees ALL displayed picks have `vision_intel` populated
    - Fixed "Jokic bug" where newly-qualified picks had missing intel
  - **Naming Unification**: Standardized on `vision_intel` field across entire stack
    - `vision_summary` kept for backward compatibility but `vision_intel` is primary
- [x] **Just-In-Time Diff Check (April 12, 2026)**
  - Queries existing collection for cached `vision_intel` before calling Gemini
  - Only fires Gemini batch for DELTA picks (new players or missing intel)
  - Returning players get cached intel merged instantly (0 tokens used)
  - Log output shows: `[Safe Haven] Cached intel found: X, Delta: Y (need Gemini)`
  - Token savings: ~70% reduction when board is stable
- [x] **MLB Pipeline 1:1 Clone (April 12, 2026)**
  - Archived old MLB files to `_archive_mlb_v1/` (14 services, 2 routes)
  - Created exact clones of NBA architecture:
    - `mlb_tier_service.py` (from ferrari_tier_service.py)
    - `mlb_sync_engine.py` (from optimized_sync_engine.py)
    - `mlb_oracle_apex_service.py` (from oracle_apex_service.py)
    - `mlb_vision_intel.py` (from vision_intel_service.py)
  - New MLB collections: `mlb_safe_haven`, `mlb_front_lines`, `mlb_war_zone`
  - MLB-specific Gemini prompt with park factors, pitcher matchups, weather
  - New routes: `/api/v3/mlb/safe-haven`, `/api/v3/mlb/front-lines`, `/api/v3/mlb/war-zone`
  - Displays beneficiary opportunities with AB bumps (Teoscar Hernandez +0.5 AB, etc.)
  - BDL injury status mapping: "10-Day-IL"/"60-Day-IL" → "OUT", "Day-To-Day" → "DTD"

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

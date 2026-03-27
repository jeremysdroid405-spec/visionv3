# PickVision / PropVision - Codebase Audit Summary

## Phase 1: Audit Results

### Executive Summary

The codebase is **well-structured but complex**. It already follows many best practices:
- Clear separation between routes, services, and repositories
- MongoDB is the SSOT (Single Source of Truth)
- Frontend only calls backend endpoints (no direct external API calls)
- Scheduled sync jobs exist and are properly configured
- Repository pattern is partially implemented

**Main Issues Identified:**
1. **Monolithic Services**: Some services are extremely large (2800+ lines)
2. **Duplicate Logic**: Similar functionality spread across multiple files
3. **Root-Level Engine Files**: 8 engine files at `/backend/` root should be in `/services/`
4. **Collection Naming Inconsistency**: Mix of `dg_*`, `nba_*`, `bdl_*` prefixes
5. **Dead/Legacy Code**: `legacy_archive/` folders exist but some legacy code is still active

---

## Current Folder Structure

```
/app
├── backend/
│   ├── config/
│   │   ├── __init__.py
│   │   ├── api_versioning.py
│   │   └── settings.py         # DB config, API keys, constants
│   ├── data/
│   │   ├── __init__.py
│   │   ├── career_milestones.py
│   │   └── context_data.py
│   ├── middleware/
│   │   ├── __init__.py
│   │   ├── rate_limiter.py
│   │   └── tracer.py
│   ├── models/                  # EMPTY - needs population
│   ├── repositories/            # Good - BaseRepository + 4 repos
│   │   ├── base.py
│   │   ├── board_repo.py
│   │   ├── picks_repo.py
│   │   ├── player_repo.py
│   │   └── sync_repo.py
│   ├── routes/                  # 30 route files - well organized
│   │   ├── __init__.py          # Route registration
│   │   ├── admin.py, auth.py, board.py, cached_data.py, command.py
│   │   ├── core_v3.py, game_lock.py, injuries.py, intel.py, live.py
│   │   ├── master_hub.py, odds_mapper.py, parlays.py, payouts.py
│   │   ├── picks.py, roster_sync.py, scheduler.py, social.py
│   │   ├── tiers.py, validation.py, vision.py, etc.
│   ├── services/                # 45+ service files
│   │   ├── bdl_*.py            # BallDontLie API services (6 files)
│   │   ├── picks_getter_service.py  # 2866 lines - NEEDS REFACTOR
│   │   ├── cached_board_builder_service.py  # 969 lines
│   │   ├── tier_builder_service.py  # 962 lines
│   │   ├── dvp_service.py      # 981 lines
│   │   ├── odds_api_service.py
│   │   ├── sync_service.py, sync_orchestration_service.py
│   │   ├── vision_summary_service.py
│   │   └── ... (40+ more)
│   ├── scripts/
│   │   └── init_database.py
│   ├── tests/                   # Test files
│   ├── utils/
│   │   ├── __init__.py
│   │   └── player_lookup.py
│   ├── legacy_archive/          # Old backup files
│   │
│   │ ## ROOT-LEVEL ENGINE FILES (should be in /services/)
│   ├── adaptive_sync_engine.py
│   ├── advanced_analytics.py
│   ├── ai_context_engine.py
│   ├── board_intelligence_engine.py
│   ├── demon_goblin_engine.py   # Core engine - manages sync
│   ├── demon_tracker_engine.py
│   ├── game_lock_engine.py
│   ├── intel_briefing_engine.py
│   ├── injury_service.py
│   ├── live_scores_engine.py
│   ├── nba_master_hub.py
│   ├── odds_api_mapper.py
│   ├── payout_engine.py
│   ├── raw_stat_fetcher.py
│   ├── social_signal_engine.py
│   ├── stats_manager_bdl.py
│   ├── vision_ai_service.py
│   │
│   ├── server.py               # Main FastAPI app (1306 lines)
│   ├── requirements.txt
│   └── .env
│
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── dashboard/       # 15+ dashboard components
│   │   │   │   ├── UniversalPlayerCard.jsx
│   │   │   │   ├── PlayerDetailPage.jsx
│   │   │   │   ├── CommandPost.jsx
│   │   │   │   ├── ParlayTicket.jsx
│   │   │   │   ├── GameLogBarChart.jsx
│   │   │   │   └── Icons.jsx, constants.js, etc.
│   │   │   ├── ui/             # shadcn/ui components
│   │   │   └── ProtectedRoute.js
│   │   ├── context/
│   │   │   └── AuthContext.js
│   │   ├── hooks/
│   │   │   ├── use-toast.js
│   │   │   ├── useLiveOdds.js   # TanStack Query hooks
│   │   │   └── useMasterStats.js
│   │   ├── lib/
│   │   │   ├── PickVisionUtils.jsx
│   │   │   ├── supabase.js
│   │   │   └── utils.js
│   │   ├── logic/
│   │   │   └── matrixEngine.js
│   │   ├── pages/
│   │   │   ├── Auth.js
│   │   │   └── Dashboard.jsx    # Main dashboard (1163 lines)
│   │   ├── providers/
│   │   │   └── QueryProvider.jsx
│   │   ├── services/
│   │   │   └── DataService.js   # All backend API calls
│   │   └── styles/
│   │       ├── components.css
│   │       └── DashboardTactical.css
│   ├── public/
│   ├── package.json
│   └── tailwind.config.js
│
├── memory/
│   └── PRD.md
├── scripts/
│   └── init_database.sh
└── test_reports/
```

---

## MongoDB Collection Mapping

### Primary Data Collections (Source of Truth)

| Collection | Purpose | Doc Count | Status |
|------------|---------|-----------|--------|
| `nba_master_hub_2026` | **SSOT for player data**: profiles, stats, game logs, photos | 548 | ✅ Primary |
| `dg_master_roster` | Player-to-team mapping, active roster | 5000 | ✅ Primary |
| `bdl_player_mapping` | BDL API ID ↔ player name mapping | 537 | ✅ Primary |
| `odds_api_mapping_master` | Odds API player name normalization | 548 | ✅ Primary |
| `player_photos` | Player headshot URLs/base64 | 672 | ✅ Primary |
| `dvp_rankings` | Defense vs Position rankings | 1 | ✅ Primary |
| `users` | User accounts and profiles | ~10 | ✅ Primary |

### Live/Active Data Collections

| Collection | Purpose | Doc Count | Status |
|------------|---------|-----------|--------|
| `dg_cached_board` | Enriched player picks for frontend | 57 | ✅ Active (rebuilt on sync) |
| `dg_live_props` | Live betting props from Odds API | 1181 | ✅ Active (refreshed often) |
| `dg_parlay_builder` | Pre-built parlay combinations | ~50 | ✅ Active |
| `dg_goblin_recon` | Safe parlay recommendations | ~20 | ✅ Active |

### Cache Collections (Ephemeral)

| Collection | Purpose | Doc Count | Status |
|------------|---------|-----------|--------|
| `dg_odds_cache` | Raw Odds API response cache | ~100 | 📦 Cache |
| `dg_events_cache` | NBA events/games cache | 8 | 📦 Cache |
| `dg_stats_cache` | Player stats cache | 201 | 📦 Cache |
| `dg_static_shell` | Static UI shell data | ~10 | 📦 Cache |
| `ticker_cache` | News ticker data | ~50 | 📦 Cache |

### Sync/Status Collections

| Collection | Purpose | Doc Count | Status |
|------------|---------|-----------|--------|
| `dg_sync_status` | Current sync state | 1 | ✅ Active |
| `dg_sync_log` | Sync history log | ~100 | ✅ Active |
| `sync_log` | Alternate sync log | 1 | ⚠️ Duplicate? |

### Tier Collections (Computed)

| Collection | Purpose | Doc Count | Status |
|------------|---------|-----------|--------|
| `dg_radar_picks` | War Zone (Demon) picks | 0 | ⚠️ Empty |
| `dg_goblin_vault` | Safe Haven (Goblin) picks | ~30 | ✅ Active |
| `dg_front_lines` | Mixed tier picks | ~20 | ✅ Active |

### Context/Intel Collections

| Collection | Purpose | Doc Count | Status |
|------------|---------|-----------|--------|
| `nba_context_engine` | AI context analysis results | ~200 | ✅ Active |
| `nba_career_stats` | Career milestones | ~100 | ✅ Active |
| `bdl_injuries` | Injury reports | ~20 | ✅ Active |
| `dg_injuries` | Injury data (alternate) | ~20 | ⚠️ Duplicate? |

### Miscellaneous

| Collection | Purpose | Doc Count | Status |
|------------|---------|-----------|--------|
| `spotrac_contracts_cache` | Contract data for badges | ~200 | ✅ Active |
| `dg_breaking_news` | Breaking news cache | ~10 | ✅ Active |
| `dg_daily_insights` | Daily player insights | ~100 | ✅ Active |
| `dg_trending` | Trending players | ~20 | ✅ Active |
| `dg_flagged_players` | Flagged/suspended players | ~5 | ✅ Active |
| `dg_locked_games` | Games that have started | ~10 | ✅ Active |
| `dg_player_data` | Player data cache | 110 | ⚠️ Possibly redundant |
| `dg_player_stats` | Player stats | ~500 | ⚠️ Possibly redundant |

---

## Key Backend Entry Points

### Main Application
- `server.py` (1306 lines) - FastAPI app, startup/shutdown, scheduler jobs

### Core Engines (Root Level - Should Move to /services/)
- `demon_goblin_engine.py` - Core sync orchestrator
- `adaptive_sync_engine.py` - Background polling with adaptive refresh rates
- `game_lock_engine.py` - Auto-remove started games
- `live_scores_engine.py` - Real-time scores
- `nba_master_hub.py` - Master hub operations
- `odds_api_mapper.py` - Odds API name normalization

### Sync Services
- `services/bdl_comprehensive_sync.py` - BDL full sync (1438 lines)
- `services/bdl_game_logs_sync.py` - Game-by-game stats
- `services/sync_service.py` - Generic sync operations
- `services/sync_orchestration_service.py` - Sync coordination
- `services/odds_sync_service.py` - Odds API sync

### Data Services
- `services/picks_getter_service.py` - **MASSIVE** (2866 lines) - needs breakup
- `services/cached_board_builder_service.py` - Board building (969 lines)
- `services/tier_builder_service.py` - War Zone/Safe Haven/Front Lines
- `services/dvp_service.py` - Defense vs Position

---

## Frontend Data Flow

```
Frontend (React + TanStack Query)
    │
    ├── useLiveOdds.js → /api/v3/war-zone, /api/v3/safe-haven, /api/v3/front-lines
    ├── useMasterStats.js → /api/v3/master-hub/*
    ├── DataService.js → All other API calls
    │
    └── NO direct external API calls ✅
```

---

## Scheduled Jobs (APScheduler)

| Job | Time (EST) | Function |
|-----|------------|----------|
| Full Daily Sync | 4:00 AM | `scheduled_daily_sync` |
| NBA L5/L10 Batch 1-5 | 4:00-4:08 AM | `scheduled_nba_batch_*` |
| BDL Game Values | 4:10 AM | `scheduled_bdl_game_values_sync` |
| Ticker Sync | 4:15 AM | `scheduled_ticker_sync` |
| Badge Sync | 4:20 AM | `scheduled_badge_sync` |
| BDL Game Logs | 4:25 AM | `scheduled_bdl_game_logs_sync` |
| Morning Props | 5:00 AM | `scheduled_daily_sync` |
| Weekly Roster | Sunday 00:00 | `scheduled_roster_sync` |

---

## Issues Identified

### High Priority
1. **`picks_getter_service.py` is 2866 lines** - Should be split into:
   - `parlay_builder_logic.py`
   - `hit_rate_calculator.py`
   - `player_stats_resolver.py`
   - `board_formatter.py`

2. **Root-level engine files** - 18 Python files at `/backend/` root should be in `/services/engines/`

3. **Duplicate injury collections** - `bdl_injuries` and `dg_injuries`

4. **Duplicate sync logs** - `dg_sync_log` and `sync_log`

### Medium Priority
1. **Empty `/models/` directory** - Should contain Pydantic models
2. **Inconsistent collection naming** - Mix of `dg_*`, `nba_*`, `bdl_*`
3. **Some services bypass repository layer** - Direct `db.collection` access

### Low Priority
1. **Large route files** - `cached_data.py` (1145 lines)
2. **Some dead code in legacy_archive** - Can be safely deleted
3. **Test files need organization** - Mix of naming conventions

---

## Environment Variables

### Required
```
MONGO_URL=mongodb://...
DB_NAME=pick_vision
ODDS_API_KEY=...
BDL_API_KEY=...
GOOGLE_API_KEY=...  (for Vision AI)
JWT_SECRET=...
```

### Optional
```
SUPABASE_URL=...
SUPABASE_ANON_KEY=...
SUPABASE_SERVICE_ROLE_KEY=...
RATE_LIMITING_ENABLED=true
```

---

## Recommendations

### Phase 2: Proposed Structure Changes

1. Move root-level engines to `/backend/services/engines/`
2. Create `/backend/models/` with Pydantic schemas
3. Split `picks_getter_service.py` into focused modules
4. Consolidate duplicate collections (injuries, sync logs)
5. Update `.env.example` with all required vars

### Phase 3: Collection Normalization

1. Designate authoritative collections:
   - `nba_master_hub_2026` → Player SSOT
   - `dg_cached_board` → Frontend display data
   - `dg_live_props` → Live odds
   - `dg_master_roster` → Roster SSOT

2. Mark cache collections clearly:
   - All `*_cache` collections are ephemeral
   - Tier collections are computed from `dg_cached_board`

### Phase 4: Sync Pipeline

Current sync is solid but spread across multiple files:
1. `demon_goblin_engine.run_full_sync()` → Main orchestrator
2. `bdl_comprehensive_sync.sync_all_active_players()` → Player data
3. `odds_sync_service` → Live odds
4. `badge_resolver` → Context badges

Recommend creating a single `SyncOrchestrator` class.

---

## What Works Well ✅

1. **MongoDB is SSOT** - No SQL confusion
2. **Frontend is clean** - Only calls backend
3. **Repository pattern exists** - Just needs expansion
4. **Scheduled jobs work** - APScheduler is configured
5. **Rate limiting exists** - Middleware in place
6. **Good separation** - routes/services/config
7. **TanStack Query** - Frontend caching done right

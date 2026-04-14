# PickVision - Architecture Audit

## Table of Contents
1. [File Tree Structure](#file-tree-structure)
2. [API Endpoints](#api-endpoints)
3. [Database Collections](#database-collections)
4. [Frontend Architecture](#frontend-architecture)
5. [Backend Services](#backend-services)
6. [Data Flow](#data-flow)

---

## File Tree Structure

```
/app
├── backend/
│   ├── .env                              # Environment variables (API keys, MongoDB URL)
│   ├── server.py                         # Main FastAPI application entry point
│   ├── utils.py                          # Utility functions
│   │
│   ├── config/
│   │   ├── __init__.py
│   │   ├── api_versioning.py             # API version configuration
│   │   └── settings.py                   # Application settings
│   │
│   ├── middleware/
│   │   ├── __init__.py
│   │   ├── rate_limiter.py               # Rate limiting middleware
│   │   └── tracer.py                     # Request tracing (X-Request-ID)
│   │
│   ├── repositories/
│   │   ├── __init__.py
│   │   ├── base.py                       # Base repository class
│   │   ├── board_repo.py                 # Board data repository
│   │   ├── picks_repo.py                 # Picks data repository
│   │   ├── player_repo.py                # Player data repository
│   │   └── sync_repo.py                  # Sync status repository
│   │
│   ├── routes/                           # API Route Handlers (31 modules)
│   │   ├── __init__.py                   # Route registration
│   │   ├── adaptive_sync.py              # Adaptive sync endpoints
│   │   ├── admin.py                      # Admin/cache management
│   │   ├── ai_context.py                 # AI context engine
│   │   ├── auth.py                       # Authentication (signup/login)
│   │   ├── board.py                      # Board data endpoints
│   │   ├── board_intel.py                # Board intelligence v1
│   │   ├── board_intel_v2.py             # Board intelligence v2
│   │   ├── cached_data.py                # Cached data endpoints
│   │   ├── command.py                    # Command Post (player profiles, search)
│   │   ├── core_v3.py                    # Core V3 API
│   │   ├── demon_tracker.py              # Demon tracking system
│   │   ├── game_lock.py                  # Game lock management
│   │   ├── injuries.py                   # Injury data
│   │   ├── intel.py                      # Intel briefings
│   │   ├── intel_sync.py                 # Intel sync operations
│   │   ├── legacy.py                     # Legacy endpoints
│   │   ├── live.py                       # Live scores/news
│   │   ├── live_scores.py                # Live scores engine
│   │   ├── master_hub.py                 # Master hub management
│   │   ├── odds_mapper.py                # Odds API mapping
│   │   ├── parlays.py                    # Parlay builder
│   │   ├── payouts.py                    # Payout calculations
│   │   ├── picks.py                      # Picks endpoints
│   │   ├── qa_testing.py                 # QA testing endpoints
│   │   ├── roster_sync.py                # Roster synchronization
│   │   ├── scheduler.py                  # Scheduler management
│   │   ├── social.py                     # Social signals
│   │   ├── sync.py                       # Data sync operations
│   │   ├── tiers.py                      # War Zone/Safe Haven/Front Lines
│   │   ├── validation.py                 # Data validation
│   │   └── vision.py                     # Vision AI insights
│   │
│   ├── services/                         # Business Logic (32 services)
│   │   ├── __init__.py
│   │   ├── badge_resolver.py             # Badge resolution (10 narrative badges)
│   │   ├── board_service.py              # Board data service
│   │   ├── cached_board_builder_service.py
│   │   ├── cron_scheduler.py             # APScheduler setup
│   │   ├── data_integrity_service.py     # Data validation
│   │   ├── data_scraper.py               # Web scraping utilities
│   │   ├── dvp_service.py                # Defense vs Position rankings
│   │   ├── headshot_scraper.py           # Player photo scraping
│   │   ├── insights_service.py           # Player insights
│   │   ├── insights_sync_service.py      # Insights synchronization
│   │   ├── intel_suite_calculator.py     # Advanced metrics calculator
│   │   ├── master_hub_sync.py            # Master hub sync (Tank01 wrapper)
│   │   ├── nba_official_sync.py          # NBA official stats sync
│   │   ├── odds_api_service.py           # The Odds API integration
│   │   ├── odds_sync_service.py          # Odds synchronization
│   │   ├── parlay_builder_service.py     # Parlay building logic
│   │   ├── parlay_service.py             # Parlay calculations
│   │   ├── photo_service.py              # Photo URL management
│   │   ├── picks_getter_service.py       # CORE: Data retrieval & enrichment
│   │   ├── picks_service.py              # Picks business logic
│   │   ├── prop_processor_service.py     # Prop processing
│   │   ├── props_service.py              # Props management
│   │   ├── roster_service.py             # Team roster management
│   │   ├── simulation_service.py         # Prop simulation
│   │   ├── social_scout.py               # Social media monitoring
│   │   ├── ssot_data_layer.py            # SSOT enforcement
│   │   ├── stateless_tier_service.py     # Tier calculations
│   │   ├── stats_api_service.py          # Stats API wrapper
│   │   ├── stats_enrichment_service.py   # Stats enrichment
│   │   ├── stats_service.py              # Stats calculations
│   │   ├── sync_orchestration_service.py # Sync orchestration
│   │   ├── sync_service.py               # Sync operations
│   │   ├── tank01_service.py             # Tank01 API wrapper
│   │   ├── tank01_stats_service.py       # Tank01 stats processing
│   │   ├── tier_builder_service.py       # Tier building
│   │   └── utils_service.py              # Service utilities
│   │
│   ├── utils/
│   │   ├── __init__.py
│   │   └── player_lookup.py              # Centralized player lookup
│   │
│   ├── tests/                            # Backend test files
│   │   ├── test_advanced_analytics_insights.py
│   │   ├── test_ai_confidence_pickcard.py
│   │   ├── test_bdl_stats_enrichment.py
│   │   ├── test_critical_backend.py
│   │   ├── test_demon_goblin_v3.py
│   │   ├── test_demon_tracker_v2.py
│   │   ├── test_dvp_calculation.py
│   │   ├── test_dvp_service.py
│   │   ├── test_headshot_display.py
│   │   ├── test_nba_bestbets_api.py
│   │   ├── test_p1_features_v4.py
│   │   ├── test_p2_tech_debt.py
│   │   ├── test_payout_engine.py
│   │   ├── test_truth_engine.py
│   │   ├── test_v3_api_refactoring.py
│   │   ├── test_v3_new_services.py
│   │   └── test_v3_refactoring_phase3.py
│   │
│   └── [Standalone Engines]              # Root-level engine files
│       ├── adaptive_sync_engine.py       # CORE: PrizePicks data fetch & classification
│       ├── advanced_analytics.py         # Advanced analytics calculations
│       ├── ai_context_engine.py          # AI context processing
│       ├── board_intelligence_engine.py  # Board intelligence
│       ├── data_integrity.py             # Data integrity checks
│       ├── demon_goblin_engine.py        # Demon/Goblin classification
│       ├── demon_tracker_engine.py       # Demon tracking
│       ├── game_lock_engine.py           # Game lock management
│       ├── injury_service.py             # Injury data processing
│       ├── intel_briefing_engine.py      # Intel briefing generation
│       ├── live_scores_engine.py         # Live scores processing
│       ├── nba_master_hub.py             # Master hub operations
│       ├── odds_api_mapper.py            # Odds API name mapping
│       ├── payout_engine.py              # Payout calculations
│       ├── raw_stat_fetcher.py           # Raw stat retrieval
│       ├── social_signal_engine.py       # Social signals processing
│       ├── stats_manager_bdl.py          # BallDontLie stats manager
│       └── vision_ai_service.py          # Vision AI service
│
├── frontend/
│   ├── .env                              # Frontend env (REACT_APP_BACKEND_URL)
│   ├── package.json                      # NPM dependencies
│   ├── tailwind.config.js                # Tailwind CSS configuration
│   ├── craco.config.js                   # Create React App configuration
│   ├── postcss.config.js                 # PostCSS configuration
│   │
│   ├── src/
│   │   ├── App.js                        # Main React app component
│   │   ├── App.css                       # Global styles
│   │   ├── index.js                      # Entry point
│   │   ├── index.css                     # Base CSS
│   │   │
│   │   ├── components/
│   │   │   ├── ProtectedRoute.js         # Auth route protection
│   │   │   │
│   │   │   ├── dashboard/                # Dashboard components
│   │   │   │   ├── CacheService.js       # Client-side caching
│   │   │   │   ├── CommandPost.jsx       # Command Post sidebar
│   │   │   │   ├── CommandSearch.jsx     # Command Post search
│   │   │   │   ├── Icons.jsx             # DemonIcon, GoblinIcon, VisionBadge
│   │   │   │   ├── ParlayCard.jsx        # Parlay display card
│   │   │   │   ├── ParlayTicket.jsx      # Parlay ticket component
│   │   │   │   ├── PickCard.jsx          # CORE: Universal pick card
│   │   │   │   ├── PlayerCard.jsx        # Player card component
│   │   │   │   ├── PlayerDetailPage.jsx  # CORE: Player profile view
│   │   │   │   ├── SectionContainer.jsx  # Section wrapper
│   │   │   │   ├── TacticalPlayerCard.jsx# Tactical view card
│   │   │   │   ├── TacticalProfile.jsx   # Tactical profile view
│   │   │   │   ├── constants.js          # TEAM_LOGOS, STAT_CATEGORIES
│   │   │   │   └── index.js              # Component exports
│   │   │   │
│   │   │   └── ui/                       # Shadcn UI components (40+)
│   │   │       ├── BadgePill.jsx         # Custom badge component
│   │   │       ├── accordion.jsx
│   │   │       ├── badge.jsx
│   │   │       ├── button.jsx
│   │   │       ├── card.jsx
│   │   │       ├── dialog.jsx
│   │   │       ├── input.jsx
│   │   │       ├── sonner.jsx            # Toast notifications
│   │   │       └── [...40+ more UI components]
│   │   │
│   │   ├── context/
│   │   │   └── AuthContext.js            # Authentication context
│   │   │
│   │   ├── hooks/
│   │   │   ├── use-toast.js              # Toast hook
│   │   │   ├── useLiveOdds.js            # PIPE 2: Live data hooks (30s polling)
│   │   │   └── useMasterStats.js         # PIPE 1: Stats hook (24hr cache)
│   │   │
│   │   ├── lib/
│   │   │   ├── PickVisionUtils.jsx       # Shared UI utilities
│   │   │   ├── supabase.js               # Supabase client (unused)
│   │   │   └── utils.js                  # CN utility function
│   │   │
│   │   ├── logic/
│   │   │   └── matrixEngine.js           # Parlay ticket builder
│   │   │
│   │   ├── pages/
│   │   │   ├── Auth.js                   # Login/Signup page
│   │   │   └── Dashboard.jsx             # CORE: Main dashboard
│   │   │
│   │   ├── providers/
│   │   │   └── QueryProvider.jsx         # TanStack Query provider
│   │   │
│   │   ├── services/
│   │   │   └── DataService.js            # Data fetching utilities
│   │   │
│   │   └── styles/
│   │       ├── DashboardTactical.css     # Dashboard tactical styles
│   │       └── components.css            # Component styles (tickers, swipe)
│   │
│   └── build/                            # Production build output
│
├── memory/
│   └── PRD.md                            # Product Requirements Document
│
├── test_reports/                         # Test iteration reports
│   ├── iteration_1.json
│   ├── iteration_2.json
│   └── [...iteration_21.json]
│
├── UI_EXPORT.txt                         # Frontend UI skin export
├── ARCHITECTURE_AUDIT.md                 # This file
└── design_guidelines.json                # Design system configuration
```

---

## API Endpoints

### Authentication (`/api/auth`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/auth/signup` | User registration |
| POST | `/api/auth/login` | User login |

### Command Post (`/api/command`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/command/search` | Player search |
| GET | `/api/command/profile/{player_name}` | **CORE**: Full player profile with props |
| POST | `/api/command/simulate` | Parlay simulation |
| GET | `/api/command/grades` | Infiltration grades |

### Tier Endpoints (`/api`) - **MAIN DASHBOARD DATA**
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v3/war-zone` | **Demon picks** (boosted/high-risk) |
| GET | `/api/v3/goblin-vault` | **Goblin picks** (discount/safe) |
| GET | `/api/v3/safe-haven` | Alias for goblin-vault |
| GET | `/api/v3/front-lines` | **Standard picks** (main lines) |
| GET | `/api/v3/parlay-builder` | Pre-built parlays |
| GET | `/api/v3/goblin-recon` | Goblin scouting data |

### Core V3 (`/api`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v3/status` | API health status |
| POST | `/api/v3/sync` | Trigger data sync |
| GET | `/api/v3/players` | All players list |
| GET | `/api/v3/player/{player_name}` | Single player data |
| GET | `/api/v3/demons` | Demon picks list |
| GET | `/api/v3/goblins` | Goblin picks list |
| GET | `/api/v3/search` | Player search |
| GET | `/api/v3/board` | Full board data |
| GET | `/api/v3/trending` | Trending picks |
| GET | `/api/v3/most-popular-bets` | Most popular bets |

### Cached Data (`/api`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v3/static-shell` | Static board shell |
| GET | `/api/v3/live-lines` | Live betting lines |
| GET | `/api/v3/hydrated-board` | Full hydrated board |
| GET | `/api/v3/cached-props` | Cached props data |
| GET | `/api/v3/cached-player/{player_name}` | Cached player data |

### Live Data (`/api/live`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/live/scores` | **Live game scores** |
| GET | `/api/live/news` | **Breaking news headlines** |
| POST | `/api/live-scores/refresh` | Refresh live scores |

### Board Intelligence (`/api`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v3/board-intel/status` | Intel status |
| POST | `/api/v3/board-intel/primary-sync` | Full sync |
| POST | `/api/v3/board-intel/delta-refresh` | Delta update |
| POST | `/api/v3/board-intel/start-scheduler` | Start polling |
| POST | `/api/v3/board-intel/stop-scheduler` | Stop polling |
| GET | `/api/v3/live-ticker` | Live ticker data |
| GET | `/api/v3/scouting-projections` | Scouting data |

### Game Lock (`/api`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v3/lock-status` | Lock status |
| GET | `/api/v3/t-minus-games` | Upcoming games |
| GET | `/api/v3/locked-games` | Locked games list |
| POST | `/api/v3/validate-parlay` | Validate parlay locks |
| POST | `/api/v3/check-locks` | Check current locks |

### Master Hub (`/api/master-hub`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/master-hub/player/{player_id}` | Player by ID |
| GET | `/api/master-hub/player/name/{display_name}` | Player by name |
| GET | `/api/master-hub/search` | Search players |
| GET | `/api/master-hub/stats` | Hub statistics |
| POST | `/api/master-hub/sync` | Trigger hub sync |
| POST | `/api/master-hub/sync-tank01` | Sync from Tank01 |
| POST | `/api/master-hub/populate-tank01-ids` | Populate Tank01 IDs |
| POST | `/api/master-hub/sync-player-logs/{player_name}` | Sync player game logs |
| POST | `/api/master-hub/start-scheduler` | Start scheduler |
| POST | `/api/master-hub/sync-nba-official` | Sync NBA official |
| POST | `/api/master-hub/sync-nba-official/{player_name}` | Sync single player |

### Scheduler (`/api`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v3/scheduler-status` | Scheduler status |
| POST | `/api/v3/trigger-scheduled-sync` | Manual sync trigger |
| POST | `/api/v3/sync-baseline-stats` | Sync baseline stats |
| GET | `/api/v3/breaking-news` | Breaking news feed |

### Roster Sync (`/api`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v3/sync-master-roster` | Sync master roster |
| POST | `/api/v3/sync-player-photos` | Sync player photos |
| POST | `/api/v3/sync-active-players` | Sync active players |
| POST | `/api/v3/refresh-board-photos` | Refresh board photos |
| POST | `/api/v3/refresh-all-photos` | Refresh all photos |
| GET | `/api/v3/roster/players` | All roster players |
| GET | `/api/v3/player/{player_name}/photo` | Player photo URL |
| GET | `/api/v3/team/{team_abbrev}/roster` | Team roster |
| POST | `/api/v3/sync-player-stats` | Sync player stats |
| POST | `/api/v3/sync-daily-insights` | Sync daily insights |
| GET | `/api/v3/player-insights/{player_name}` | Player insights |
| GET | `/api/v3/master-roster-status` | Roster sync status |

### Injuries (`/api/injuries`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/injuries/sync` | Sync injury data |
| GET | `/api/injuries` | All injuries |
| GET | `/api/injuries/player/{player_name}` | Player injuries |
| GET | `/api/injuries/team/{team_abbr}` | Team injuries |
| GET | `/api/injuries/alerts` | Injury alerts |

### Payouts (`/api`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v3/calculate-payout` | Calculate parlay payout |
| GET | `/api/v3/payout-estimate` | Payout estimate |
| GET | `/api/v3/payout-table` | Payout lookup table |

### Social Signals (`/api`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v3/sync-social-signals` | Sync social signals |
| GET | `/api/v3/social-signals` | All social signals |
| GET | `/api/v3/social-signal/{player_name}` | Player social signals |

### Validation (`/api`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v3/raw-validation/{player_name}` | Validate player data |
| POST | `/api/v3/raw-validation/batch` | Batch validation |
| GET | `/api/v3/raw-validation-table` | Validation table |
| GET | `/api/v3/raw-player-games/{player_name}` | Raw player games |

### Vision AI (`/api/vision`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/vision/generate-insight` | Generate AI insight |
| POST | `/api/vision/trigger-batch` | Batch insight generation |
| GET | `/api/vision/status` | Vision service status |

### Context/Badges (`/api/context`, `/api/player`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/context/flag` | Add narrative flag |
| GET | `/api/context/badges` | List all badges |
| GET | `/api/context/player/{id}/flags` | Player's flags |
| GET | `/api/player/{slug}/vision` | Player vision + badges |

### Admin (`/api/admin`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/admin/cache-status` | Cache status |
| POST | `/api/admin/clear-expired-cache` | Clear expired cache |
| POST | `/api/admin/sync-rosters` | Sync all rosters |
| POST | `/api/admin/clear-all-cache` | Clear all cache |
| GET | `/api/admin/todays-games` | Today's games |
| POST | `/api/admin/trigger-daily-sync` | Manual daily sync |
| GET | `/api/admin/rate-limit-status` | Rate limit status |
| GET | `/api/admin/roster-status` | Roster sync status |
| GET | `/api/admin/dvp-status` | DvP service status |
| POST | `/api/admin/dvp-refresh` | Refresh DvP data |
| GET | `/api/admin/dvp-rankings` | DvP rankings |
| GET | `/api/admin/dvp-analysis/{opponent}/{stat}` | DvP analysis |

### QA Testing (`/api/qa`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/qa/inject-line-move` | Inject fake line move |
| POST | `/api/qa/revert-line-move` | Revert line move |

### Adaptive Sync (`/api`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v3/sync-status` | Sync status |
| GET | `/api/v3/stale-intel-check` | Check stale intel |
| POST | `/api/v3/priority-refresh` | Priority refresh |
| GET | `/api/v3/intel-freshness` | Intel freshness |
| POST | `/api/v3/adaptive-sync/start` | Start adaptive sync |
| POST | `/api/v3/adaptive-sync/stop` | Stop adaptive sync |

### Odds Mapper (`/api/odds-mapper`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/odds-mapper/stats` | Mapping stats |
| GET | `/api/odds-mapper/lookup/{odds_api_name}` | Lookup player |
| POST | `/api/odds-mapper/lookup-batch` | Batch lookup |
| POST | `/api/odds-mapper/rebuild` | Rebuild mapping |
| GET | `/api/odds-mapper/player-id/{player_id}` | Get by player ID |

### Intel Sync (`/api`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v3/sync-to-mongo` | Sync to MongoDB |
| POST | `/api/v3/generate-intel-briefings` | Generate briefings |
| GET | `/api/v3/intel-briefing/{player_name}` | Player briefing |

### Legacy (`/api`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/full-board` | Full board (legacy) |
| GET | `/api/calculate-hit-rate` | Calculate hit rate |
| GET | `/api/validate-demon` | Validate demon pick |
| GET | `/api/` | API root |

---

## Database Collections

### MongoDB Database: `pickvision`

| Collection | Purpose |
|------------|---------|
| `nba_master_hub_2026` | **SSOT**: Player stats, photos, game logs |
| `dg_cached_board` | **Live**: Cached betting lines from PrizePicks |
| `nba_context_engine` | Narrative flags for badge system |
| `dg_master_roster` | Master roster with photos |
| `injuries` | Player injury data |
| `social_signals` | News & social sentiment |
| `sync_status` | Sync operation tracking |
| `users` | User authentication |

---

## Frontend Architecture

### TanStack Query (React Query) - Global State

```
┌─────────────────────────────────────────────────────────────────────┐
│                    TANSTACK QUERY GLOBAL STATE                       │
├─────────────────────────────────────────────────────────────────────┤
│  PIPE 1: useMasterStats(playerName)                                 │
│  ├─ Source: /api/v3/cached-player/{playerName}                      │
│  ├─ staleTime: 24 hours                                             │
│  └─ Use: Player stats from Master Hub                               │
│                                                                     │
│  PIPE 2: useLiveOdds() hooks                                        │
│  ├─ refetchInterval: 30 seconds                                     │
│  └─ Hooks:                                                          │
│      ├─ useWarZone()     → /api/v3/war-zone                         │
│      ├─ useSafeHaven()   → /api/v3/goblin-vault                     │
│      ├─ useFrontLines()  → /api/v3/front-lines                      │
│      ├─ useLiveScores()  → /api/live/scores                         │
│      ├─ useBreakingNews()→ /api/live/news                           │
│      └─ usePlayerSearch()→ /api/command/search                      │
└─────────────────────────────────────────────────────────────────────┘
```

### Component Hierarchy

```
App.js
└── QueryProvider (TanStack Query)
    └── AuthContext.Provider
        └── Routes
            ├── /auth → Auth.js
            └── / → Dashboard.jsx (protected)
                    ├── LiveScoresTicker
                    ├── BreakingNewsTicker
                    ├── Intel Search Section
                    ├── MostPopularBetsSection
                    ├── SafeHavenSection → PickCard[]
                    ├── ParlaySection (Shield)
                    ├── FrontLinesSection → PickCard[]
                    ├── ParlaySection (Strike)
                    ├── WarZoneSection → PickCard[]
                    ├── ParlaySection (Gauntlet)
                    ├── CommandPost (sidebar)
                    └── PlayerDetailPage (modal view)
```

---

## Backend Services

### Core Services

| Service | File | Purpose |
|---------|------|---------|
| **picks_getter_service** | `services/picks_getter_service.py` | Data retrieval & enrichment for all endpoints |
| **adaptive_sync_engine** | `adaptive_sync_engine.py` | PrizePicks data fetch & Demon/Goblin classification |
| **badge_resolver** | `services/badge_resolver.py` | 10 narrative badges (Legal Noise, Jet Lag, etc.) |
| **intel_suite_calculator** | `services/intel_suite_calculator.py` | Advanced metrics (DvP, Pace Delta, Stability) |
| **tank01_stats_service** | `services/tank01_stats_service.py` | Tank01 API for game logs |
| **stats_service** | `services/stats_service.py` | Coupled stats calculation |

### External API Integrations

| API | Service | Purpose |
|-----|---------|---------|
| The Odds API | `odds_api_service.py` | Live betting lines (PrizePicks) |
| Tank01 (RapidAPI) | `tank01_stats_service.py` | Player game logs & stats |
| NBA CDN | Direct URLs | Player headshots |

---

## Data Flow

```
                    ┌─────────────────────────────────────────────────┐
                    │           EXTERNAL APIs                         │
                    │  ┌──────────────┐   ┌──────────────────────┐   │
                    │  │ The Odds API │   │  Tank01 (RapidAPI)   │   │
                    │  │  (PrizePicks)│   │   (Game Logs)        │   │
                    │  └──────┬───────┘   └──────────┬───────────┘   │
                    └─────────┼──────────────────────┼───────────────┘
                              │                      │
                              ▼                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        BACKEND (FastAPI)                            │
│  ┌──────────────────────┐    ┌────────────────────────────────┐    │
│  │  adaptive_sync_engine │    │    tank01_stats_service        │    │
│  │  - Fetch PrizePicks   │    │    - Fetch game logs           │    │
│  │  - Classify D/G/S     │    │    - Calculate L5/L10/Season   │    │
│  └──────────┬───────────┘    └────────────────┬───────────────┘    │
│             │                                  │                    │
│             ▼                                  ▼                    │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │                     MongoDB Collections                       │  │
│  │  ┌─────────────────┐        ┌───────────────────────────┐   │  │
│  │  │ dg_cached_board │        │   nba_master_hub_2026      │   │  │
│  │  │ (Live Lines)    │        │   (Stats, Photos, Logs)    │   │  │
│  │  └────────┬────────┘        └──────────────┬────────────┘   │  │
│  └───────────┼────────────────────────────────┼────────────────┘  │
│              │                                 │                    │
│              └────────────────┬────────────────┘                   │
│                               ▼                                     │
│                    ┌─────────────────────┐                         │
│                    │ picks_getter_service │                         │
│                    │ - Merge Lines + Stats│                         │
│                    │ - Enrich with Badges │                         │
│                    │ - Add Vision Insight │                         │
│                    └──────────┬──────────┘                         │
│                               │                                     │
│                               ▼                                     │
│                    ┌─────────────────────┐                         │
│                    │     API Routes       │                         │
│                    │  /api/v3/war-zone    │                         │
│                    │  /api/v3/goblin-vault│                         │
│                    │  /api/v3/front-lines │                         │
│                    │  /api/command/profile│                         │
│                    └──────────┬──────────┘                         │
└───────────────────────────────┼─────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       FRONTEND (React)                              │
│  ┌────────────────────────────────────────────────────────────┐    │
│  │                   TanStack Query Hooks                      │    │
│  │  useWarZone() │ useSafeHaven() │ useFrontLines()           │    │
│  │  useLiveScores() │ useBreakingNews() │ useMasterStats()    │    │
│  └───────────────────────────┬────────────────────────────────┘    │
│                              │                                      │
│                              ▼                                      │
│  ┌────────────────────────────────────────────────────────────┐    │
│  │                   Dashboard.jsx                             │    │
│  │  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐        │    │
│  │  │  War Zone    │ │  Safe Haven  │ │ Front Lines  │        │    │
│  │  │  (Demons)    │ │  (Goblins)   │ │  (Standard)  │        │    │
│  │  └──────────────┘ └──────────────┘ └──────────────┘        │    │
│  │         ↓                 ↓                 ↓               │    │
│  │  ┌──────────────────────────────────────────────────┐      │    │
│  │  │              PickCard Components                  │      │    │
│  │  └──────────────────────────────────────────────────┘      │    │
│  └────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
```

---

## PrizePicks Classification Rules

| Category | Market Type | Odds | Color |
|----------|-------------|------|-------|
| **DEMON** | `_alternate` | +100 | Red |
| **GOBLIN** | `_alternate` | != +100 | Green |
| **STANDARD** | Main line | Any | Gray |

---

## Badge System (10 Narrative Badges)

| Badge | Trigger |
|-------|---------|
| [Jet Lag] | Travel > 1000mi |
| [Gassed] | Back-to-back game |
| [Home Cookin'] | Home + 10% PPG split |
| [Legal Noise] | Divorce/custody/legal |
| [Distraction] | Trade rumors/drama |
| [Revenge] | Former team matchup |
| [Pay Day] | Contract year |
| [Milestone] | Chasing records |
| [Deep Water] | Playoff/elimination |
| [Locked In] | High perf despite distractions |

---

## Environment Variables

### Backend (`.env`)
```
MONGO_URL=mongodb://...
DB_NAME=pickvision
ODDS_API_KEY=...
TANK01_API_KEY=...
GOOGLE_API_KEY=... (Gemini for Intel)
BDL_API_KEY=... (deprecated)
```

### Frontend (`.env`)
```
REACT_APP_BACKEND_URL=https://local-first-hub-2.preview.emergentagent.com
```

---

## CRON Jobs

| Schedule | Job | Purpose |
|----------|-----|---------|
| 4:00 AM EST | `scheduled_daily_sync` | Stats sync from Tank01 |
| Sunday 00:00 UTC | `scheduled_roster_sync` | Weekly roster update |
| Every 30s | Adaptive Sync | Live odds polling |
| Every 60s | Game Lock Engine | Lock games in progress |

---

**Generated:** 2026-03-18
**Total Files:** ~200+ 
**API Endpoints:** 120+
**Services:** 32
**Route Modules:** 31

# NBA Best Bets - Demon Tracker v2

## Product Requirements Document

### Original Problem Statement
Build a high-performance NBA Player Prop Dashboard called "Best Bets" (now "Demon Tracker v2") to identify value plays by comparing betting lines across various sportsbooks.

### Core Strategy
1. **Cross-Book Comparison:** Pull prop lines from major sportsbooks (DraftKings, FanDuel) via The Odds API
2. **Line Discrepancy:** Flag plays where market lines differ significantly
3. **Demon Line Analysis:** Calculate if the historical hit rate justifies the risk (L10 >= 40%)

### Data Sources
1. **The Odds API** - Betting lines from DraftKings & FanDuel
   - Endpoint: `https://api.the-odds-api.com/v4/sports/basketball_nba/events`
   - Markets: player_points, player_rebounds, player_assists, player_threes
   - API Key: `e1ae76ab21c34ee88ed552cffb4449fd`

2. **BallDontLie API** - Player statistics and hit rate calculations
   - Endpoint: `https://api.balldontlie.io/v1`
   - Season: 2024 (2024-2025 NBA season data)
   - API Key: `ad5544be-9969-434b-9389-2b7cf658c8e0`

3. **Tank01 API** - Matchup verification (rate-limited)
   - Endpoint: `https://tank01-nba-live-in-game-real-time-statistics-nba.p.rapidapi.com`
   - API Key: `402edbcac6mshd04997e7ca01d17p1879eajsn65ab176cdb1e`

### Key Features
- **Triple-View Hit Rate:** L5, L10, Season Average
- **Demon Lines:** Props with L10 hit rate >= 40%
- **HOT/COLD Trends:** Based on L5 vs Season average
- **Full Board Display:** All daily props from DK & FD
- **Autonomous Daily Sync:** Auto-fetch on app startup

---

## Implementation Status

### Completed Features (March 12, 2026)

#### Backend (FastAPI)
- [x] Three-way data sync engine (`demon_tracker_engine.py`)
- [x] Odds API integration for player props
- [x] BallDontLie API for player stats
- [x] Tank01 API partial integration
- [x] Triple-View hit rate calculation
- [x] Demon line identification (L10 >= 40%)
- [x] Autonomous startup sync

#### API Endpoints
- [x] `GET /api/demon-tracker/status` - Sync status
- [x] `GET /api/demon-tracker/events` - Today's NBA events
- [x] `POST /api/demon-tracker/sync` - Trigger full sync
- [x] `GET /api/demon-tracker/event/{id}/odds` - Event odds
- [x] `GET /api/demon-tracker/props` - All processed props
- [x] `GET /api/demon-tracker/demons` - Demon lines only
- [x] `GET /api/demon-tracker/board` - Full board by event
- [x] `GET /api/demon-tracker/player/{name}` - Player analysis

#### Frontend (React)
- [x] "DEMON TRACKER v2" dashboard
- [x] Three-way sync status display
- [x] Full Board / Demons Only tabs
- [x] Props table with hit rates
- [x] DEMON/HOT/COLD badges
- [x] Search and filter functionality
- [x] Market filter (PTS, REB, AST, 3PM)
- [x] Bookmaker filter (DK, FD)
- [x] Pro tier toggle (shows prices)

### Test Results (Iteration 2)
- Backend: 100% (20/20 tests passed)
- Frontend: 100% (all features working)
- Events: 9 games loaded
- Props: 100 processed
- Demons: 63 identified

---

## Pending Items

### P0 - Critical
- [ ] Fix authentication flow (currently bypassed)
- [ ] Connect Pro tier to Supabase subscription

### P1 - High Priority
- [ ] Implement caching layer for API rate limits
- [ ] Add more player prop markets
- [ ] Price comparison between DK and FD

### P2 - Nice to Have
- [ ] Tank01 matchup strength display
- [ ] Clean up deprecated files
- [ ] Add trend detection badges

---

## Architecture

```
/app
├── backend/
│   ├── server.py              # FastAPI server
│   ├── demon_tracker_engine.py # Three-way sync engine
│   ├── stats_manager_bdl.py   # BallDontLie API
│   └── .env                   # API keys
├── frontend/
│   └── src/
│       └── pages/
│           └── FullBoard.js   # Main dashboard
└── test_reports/
    └── iteration_2.json       # Latest test results
```

---

## Current Data (March 12, 2026)

### Top Demon Lines
1. **Ivica Zubac** - REB O6.5 - L10: **100%** (10/10)
2. **Trae Young** - AST O5.5 - L10: **90%** (9/10)
3. **VJ Edgecombe** - REB O4.5 - L10: **80%** (8/10)
4. **Duncan Robinson** - AST O1.5 - L10: **80%** (8/10)
5. **Carlton Carrington** - AST O3.5 - L10: **80%** (8/10)

### Today's Games
1. Philadelphia 76ers @ Detroit Pistons
2. Phoenix Suns @ Indiana Pacers
3. Washington Wizards @ Orlando Magic
4. Chicago Bulls @ Los Angeles Lakers
5. Oklahoma City Thunder @ Boston Celtics
6. Brooklyn Nets @ Atlanta Hawks
7. Milwaukee Bucks @ Miami Heat
8. New Orleans Pelicans @ Dallas Mavericks
9. San Antonio Spurs @ Denver Nuggets

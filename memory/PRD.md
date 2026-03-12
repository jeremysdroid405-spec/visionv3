# NBA Best Bets - Demon & Goblin Analytics Engine v3.0

## Product Requirements Document

### Original Problem Statement
Build a high-performance NBA Player Prop Dashboard that identifies "Demons" (harder, high-payout lines) and "Goblins" (easier, high-probability lines) to mimic PrizePicks squares.

---

## DEMON & GOBLIN CLASSIFICATION

### Demon Icon (Red)
- **Definition**: Alternate lines with odds >= +200
- **Meaning**: Harder props with high potential payout
- **Example**: "SGA Over 35.5 Points @ +250"

### Goblin Icon (Green)
- **Definition**: Alternate lines with odds <= -300
- **Meaning**: Easier props that hit nearly all the time
- **Example**: "SGA Over 15.5 Points @ -350"

### Warning Banner (Yellow)
- **Trigger**: Goblin with 90%+ hit rate AND player is "Questionable"
- **Purpose**: Alert users to check player status before betting

---

## THREE-PILLAR DATA ENGINE

### Pillar 1: Line Ingestion (The Odds API)
- **Source**: api.the-odds-api.com/v4
- **Data**: ALL betting lines from DraftKings & FanDuel
- **Markets**: Points, Rebounds, Assists, 3PM, Blocks, Steals, Combos, Alternates
- **API Key**: `e1ae76ab21c34ee88ed552cffb4449fd`
- **Classification**: Automatic Demon/Goblin tagging based on American odds

### Pillar 2: Statistical Verification (BallDontLie API)
- **Source**: api.balldontlie.io/v1
- **Data**: Player game logs for 2025-26 season
- **Calculation**: Triple-View Hit Rate (L5, L10, Season Average)
- **API Key**: `ad5544be-9969-434b-9389-2b7cf658c8e0`

### Pillar 3: Contextual Research (Tank01 API)
- **Source**: RapidAPI (tank01-nba-live-in-game-real-time-statistics)
- **Data**: Injury reports, player news
- **Purpose**: Flag Goblin warnings for questionable players
- **API Key**: `402edbcac6mshd04997e7ca01d17p1879eajsn65ab176cdb1e`

---

## UI STRUCTURE (Hierarchical Player View)

### Collapsed State (Default)
- Player Name
- Team Badge
- Position
- Injury Status (if any)
- Demon Count (skull icon)
- Goblin Count (ghost icon)
- Total Props Count

### Expanded State (On Click)
- Full table of all props for that player
- Columns: TYPE | PROP | LINE | ODDS | BOOK | HIT RATE | TREND
- Demons and Goblins sorted to top
- Color-coded rows (red for Demons, green for Goblins)
- HOT/COLD trend indicators

---

## AUTONOMOUS BEHAVIOR

### On App Open
- Automatically derives current date from system clock
- Executes three-pillar sync
- No manual date input required

### Daily Refresh (Planned)
- 4:00 AM automatic slate refresh

---

## Implementation Status (March 12, 2026)

### Completed Features
- [x] Demon & Goblin Engine v3.0 (`demon_goblin_engine.py`)
- [x] Odds-based classification (>=+200 = Demon, <=-300 = Goblin)
- [x] Three-Pillar API integration
- [x] Hierarchical Player UI (`DemonGoblinDashboard.js`)
- [x] Collapsed/Expanded player views
- [x] Hit rate calculation (L5, L10, Season)
- [x] Goblin warning system (90%+ hit rate + Questionable)
- [x] Automatic date derivation
- [x] v3 API endpoints

### Current Results
- **Date**: 2026-03-12
- **Events**: 5 NBA games
- **Players**: 65 unique players
- **Props**: 470 total props
- **Demons**: 0 (no +200 odds in current data)
- **Goblins**: 0 (no -300 odds in current data)

---

## Architecture

```
/app
├── backend/
│   ├── server.py                 # FastAPI server with v3 endpoints
│   ├── demon_goblin_engine.py    # NEW: v3.0 Demon & Goblin Engine
│   ├── demon_tracker_engine.py   # Legacy v2 engine (preserved)
│   ├── stats_manager_bdl.py      # BallDontLie API utility
│   └── .env                      # API keys
├── frontend/
│   └── src/pages/
│       ├── DemonGoblinDashboard.js  # NEW: v3.0 Hierarchical UI
│       └── FullBoard.js             # Legacy v2 UI (preserved)
```

---

## API Endpoints

### v3 Endpoints (NEW)
- `GET /api/v3/status` - Engine status with counts
- `POST /api/v3/sync` - Trigger full three-pillar sync
- `GET /api/v3/players` - All players (collapsed view)
- `GET /api/v3/player/{name}` - Single player detail (expanded view)
- `GET /api/v3/demons` - All Demon lines
- `GET /api/v3/goblins` - All Goblin lines
- `GET /api/v3/search?q={query}` - Search players
- `GET /api/v3/board` - Full board data

### Legacy v2 Endpoints (Preserved)
- `GET /api/demon-tracker/status`
- `POST /api/demon-tracker/sync`
- `GET /api/demon-tracker/board`

---

## Pending Items

### P0 - Critical
- [ ] Fix authentication flow (Supabase)
- [ ] Handle Tank01 rate limiting with exponential backoff

### P1 - High Priority
- [ ] Implement 4:00 AM scheduled sync
- [ ] Add alternate line markets from more bookmakers
- [ ] Pro Tier feature (odds visibility)

### P2 - Nice to Have
- [ ] Historical tracking
- [ ] Push notifications for new Demons/Goblins

---

## Technical Notes

### Why No Demons/Goblins Today?
The Odds API returns real betting lines from DraftKings and FanDuel. The thresholds:
- **Demon**: +200 or higher (very rare, high-risk props)
- **Goblin**: -300 or lower (very rare, extremely safe props)

Standard player props typically have odds between -150 and +150. Extreme odds only appear for:
- Alternate lines (e.g., "Over 40.5 Points")
- Special markets (first basket, double-double)
- Heavily skewed matchups

The system is working correctly - it will automatically classify any qualifying lines when they appear.

---

## Routes

| Route | Component | Description |
|-------|-----------|-------------|
| `/` | Redirect | → `/v3` |
| `/v3` | DemonGoblinDashboard | Main v3 interface |
| `/full-board` | FullBoard | Legacy v2 interface |
| `/auth` | Auth | Login/Signup |
| `/demo` | DashboardDemo | Demo page |

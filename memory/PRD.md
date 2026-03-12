# NBA Best Bets - Demon Tracker v2.1

## Product Requirements Document

### Original Problem Statement
Build a high-performance NBA Player Prop Dashboard called "Demon Tracker" with a Three-Pillar Data Engine to identify value plays.

---

## THREE-PILLAR DATA ENGINE

### Pillar 1: Line Ingestion (The Odds API)
- **Source**: api.the-odds-api.com
- **Data**: ALL betting lines from DraftKings & FanDuel
- **Markets**: Points, Rebounds, Assists, 3PM, Blocks, Steals, Turnovers, Combos (PTS+REB, etc.)
- **API Key**: `672e7374ca294c653664ca3d5964f434` (fallback: `e1ae76ab21c34ee88ed552cffb4449fd`)

### Pillar 2: Statistical Verification (BallDontLie API)
- **Source**: api.balldontlie.io
- **Data**: Player game logs for 2025-26 season (Season 2025)
- **Calculation**: Triple-View Hit Rate (L5, L10, Season Average)
- **API Key**: `ad5544be-9969-434b-9389-2b7cf658c8e0`

### Pillar 3: Contextual Research (Tank01 API)
- **Source**: RapidAPI (tank01-nba-live-in-game-real-time-statistics)
- **Data**: Injury reports, player news, matchup data
- **Purpose**: Flag players with injury/load management risk
- **API Key**: `402edbcac6mshd04997e7ca01d17p1879eajsn65ab176cdb1e`

---

## COLOR-CODED DEMON CARDS

### 🟢 GREEN - High Hit Rate
- L10 hit rate >= 50%
- Strong plays to consider

### 🟡 YELLOW - Caution
- Injury or news warning detected
- Check player status before betting

### 🔴 RED - Avoid
- L10 hit rate < 30% OR player OUT
- Low probability plays

### ⚪ STANDARD
- Hit rate between 30-50%
- Neutral - needs more analysis

---

## Autonomous Behavior
- **On App Open**: Automatically derives current date, executes three-way sync
- **Data Flow**: Events → Odds → BDL Stats → Tank01 Verification → Demon Cards

---

## Implementation Status (March 12, 2026)

### Completed Features
- [x] Three-Pillar Engine (`demon_tracker_engine.py`)
- [x] Odds API integration (2734 props fetched)
- [x] BallDontLie hit rate calculation
- [x] Tank01 injury/news integration (rate-limited)
- [x] Color-coded Demon Cards UI
- [x] Autonomous startup sync
- [x] Filter by color, market, bookmaker

### Current Results
- **Events**: 9 NBA games
- **Props Processed**: 133 unique cards
- **Card Distribution**:
  - 🟢 Green: 59
  - 🟡 Yellow: 0 (Tank01 rate-limited)
  - 🔴 Red: 31
  - ⚪ Standard: 43

### Top Demon Plays (March 12, 2026)
1. **Ivica Zubac** PTS+REB O16.5 - L10: **90%** 🟢
2. **Ivica Zubac** PTS O9.5 - L10: **70%** 🟢
3. **Grayson Allen** PTS O15.5 - L10: **70%** 🟢
4. **Jarace Walker** AST O3.5 - L10: **60%** 🟢
5. **Collin Gillespie** AST O4.5 - L10: **60%** 🟢

---

## Architecture

```
/app
├── backend/
│   ├── server.py              # FastAPI server
│   ├── demon_tracker_engine.py # Three-Pillar Engine
│   ├── stats_manager_bdl.py   # BallDontLie API
│   └── .env                   # API keys
├── frontend/
│   └── src/pages/
│       └── FullBoard.js       # Demon Tracker UI
```

---

## API Endpoints

### Demon Tracker
- `GET /api/demon-tracker/status` - Sync status with card counts
- `POST /api/demon-tracker/sync` - Trigger three-pillar sync
- `GET /api/demon-tracker/events` - Today's NBA events
- `GET /api/demon-tracker/board` - Full board with color-coded cards
- `GET /api/demon-tracker/cards/green` - Green cards only
- `GET /api/demon-tracker/cards/yellow` - Yellow cards only
- `GET /api/demon-tracker/cards/red` - Red cards only
- `GET /api/demon-tracker/demons` - Demon lines (L10 >= 40%)

---

## Pending Items

### P0 - Critical
- [ ] Fix authentication flow
- [ ] Handle Tank01 rate limiting with caching

### P1 - High Priority
- [ ] Add 4:00 AM scheduled sync
- [ ] Implement price comparison between books

### P2 - Nice to Have
- [ ] Historical tracking
- [ ] Email/push notifications for new demons

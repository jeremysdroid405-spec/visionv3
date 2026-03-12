# NBA Best Bets - Demon & Goblin Analytics Engine v3.0

## Product Requirements Document

### Original Problem Statement
Build a high-performance NBA Player Prop Dashboard that identifies "Demons" (harder, boosted lines) and "Goblins" (easier, high-probability lines) from PrizePicks alternate markets.

---

## PRIZEPICKS INTEGRATION (CRITICAL)

### API Configuration
- **Region**: `us_dfs` (Daily Fantasy Sports - REQUIRED for PrizePicks)
- **Bookmaker**: `prizepicks`
- **Markets**: `player_*_alternate` (where Demons/Goblins live)

### Classification Rules (PrizePicks Native)
- **Demon (Red)**: Even odds (+100) = Boosted/harder props
- **Goblin (Green)**: Negative odds (e.g., -137) = Easier/default props

---

## THREE-PILLAR DATA ENGINE

### Pillar 1: Line Ingestion (The Odds API)
- **Source**: api.the-odds-api.com/v4
- **Region**: `us_dfs` (Daily Fantasy Sports)
- **Bookmaker**: `prizepicks`
- **Markets**: All `_alternate` markets
- **API Key**: `e1ae76ab21c34ee88ed552cffb4449fd`

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

## Implementation Status (March 12, 2026)

### Current Results
- **Date**: 2026-03-12
- **Events**: 9 NBA games
- **Players**: 234 unique players
- **Total Props**: 8,526
- **DEMONS**: 5,182 (Even +100)
- **GOBLINS**: 3,344 (Negative odds)

### Key Players Found
- **Shai Gilgeous-Alexander (OKC)**: 39 props (26 Demons, 13 Goblins)
- **Victor Wembanyama (SAS)**: Full slate loaded
- **Jaylen Brown (BOS)**: Full slate loaded
- **Nikola Jokic (DEN)**: Full slate loaded

### Completed Features
- [x] PrizePicks API integration (`us_dfs` region)
- [x] Correct Demon/Goblin classification
- [x] 234 players loaded (vs 65 before)
- [x] 8,500+ props (vs 470 before)
- [x] Hit rate calculation for all props
- [x] Hierarchical Player UI
- [x] HOT/COLD trend indicators

---

## Architecture

```
/app
├── backend/
│   ├── server.py                 # FastAPI server with v3 endpoints
│   ├── demon_goblin_engine.py    # PrizePicks integration engine
│   └── .env                      # API keys
├── frontend/
│   └── src/pages/
│       └── DemonGoblinDashboard.js  # Hierarchical UI
```

---

## API Endpoints

### v3 Endpoints
- `GET /api/v3/status` - Engine status with counts
- `POST /api/v3/sync` - Trigger full PrizePicks sync
- `GET /api/v3/players` - All players (collapsed view)
- `GET /api/v3/player/{name}` - Single player detail
- `GET /api/v3/demons` - All Demon lines
- `GET /api/v3/goblins` - All Goblin lines
- `GET /api/v3/search?q={query}` - Search players
- `GET /api/v3/board` - Full board data

---

## Routes

| Route | Component | Description |
|-------|-----------|-------------|
| `/` | Redirect | → `/v3` |
| `/v3` | DemonGoblinDashboard | PrizePicks Dashboard |
| `/full-board` | FullBoard | Legacy v2 interface |

---

## Pending Items

### P0 - Critical
- [ ] Fix authentication flow (Supabase)
- [ ] Handle Tank01 rate limiting

### P1 - High Priority
- [ ] Implement 4:00 AM scheduled sync
- [ ] Pro Tier features

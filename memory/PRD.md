# NBA Best Bets - Demon & Goblin Analytics Engine v3.0

## Product Requirements Document

### Original Problem Statement
Build a high-performance NBA Player Prop Dashboard that identifies "Demons" (harder, boosted lines) and "Goblins" (easier, high-probability lines) from PrizePicks alternate markets, with optimized caching for fast load times.

---

## HYBRID CACHING STRATEGY

### Static Shell (24h TTL)
- **Stored in**: MongoDB + localStorage
- **Contains**: Player metadata, teams, positions, historical stats (L5, L10, Season)
- **Does NOT contain**: Live betting lines
- **Refresh**: 4:00 AM daily or on manual sync

### Dynamic Pulse (60s TTL)
- **Stored in**: MongoDB only
- **Contains**: Live betting lines (price, point, demon/goblin tags)
- **Refresh**: Automatic every 60 seconds

### Load Sequence
1. **Instant**: Render player cards from static shell (localStorage)
2. **Background**: Fetch live lines and hydrate cards
3. **Result**: Fast initial load + real-time line updates

---

## PRIZEPICKS INTEGRATION

### API Configuration
- **Region**: `us_dfs` (Daily Fantasy Sports)
- **Bookmaker**: `prizepicks`
- **Markets**: `player_*_alternate`

### Classification
- **Demon (Red)**: Even odds (+100) = Boosted/harder props
- **Goblin (Green)**: Negative odds (e.g., -137) = Easier props

---

## Implementation Status (March 12, 2026)

### Current Results
- **Date**: 2026-03-12
- **Events**: 9 NBA games
- **Players**: 117 unique players
- **DEMONS**: 2,653 (Even +100)
- **GOBLINS**: 1,702 (Negative odds)

### Trending 10 (Live)
1. Grayson Allen (PHX) - 28 D, 22 G
2. Cade Cunningham (DET) - 27 D, 20 G
3. Jalen Suggs (ORL) - 29 D, 18 G
4. Jalen Johnson (MIA) - 24 D, 23 G
5. Cooper Flagg (DAL) - 26 D, 19 G
6. Cam Spencer (MEM) - 26 D, 18 G
7. Nikola Jokic (DEN) - 25 D, 19 G
8. Victor Wembanyama (SAS) - 27 D, 19 G
9. Austin Reaves (LAL) - 26 D, 19 G
10. Danny Wolf (BKN) - 25 D, 20 G

---

## API Endpoints

### v3 Endpoints
- `GET /api/v3/status` - Engine status
- `POST /api/v3/sync` - Full sync (updates static shell)
- `GET /api/v3/static-shell` - Static shell data (24h cache)
- `GET /api/v3/live-lines` - Live betting lines (60s cache)
- `GET /api/v3/hydrated-board` - Combined static + live
- `GET /api/v3/trending` - Trending 10
- `GET /api/v3/players` - All players
- `GET /api/v3/player/{name}` - Player detail
- `GET /api/v3/board` - Full board

---

## Routes

| Route | Component | Description |
|-------|-----------|-------------|
| `/` | Redirect | → `/v3` |
| `/v3` | DemonGoblinDashboardOptimized | Optimized with caching |
| `/v3-legacy` | DemonGoblinDashboard | Legacy version |

---

## Pending Items

### P0 - Critical
- [ ] Fix authentication flow (Supabase)

### P1 - High Priority
- [ ] Implement 4:00 AM scheduled sync (cron)
- [ ] Tank01 injury integration
- [ ] Virtual scrolling for 4,000+ rows

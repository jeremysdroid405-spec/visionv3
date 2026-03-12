# NBA Best Bets - Demon & Goblin Analytics Engine v3.0

## Product Requirements Document

### Original Problem Statement
Build a high-performance NBA Player Prop Dashboard that identifies "Demons" (harder, boosted lines) and "Goblins" (easier, high-probability lines) from PrizePicks alternate markets, with a "Trending 10" section showing the most popular players.

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

## TRENDING 10 - MOST POPULAR TODAY

### Popularity Algorithm
- Based on API response order (first = more popular on PrizePicks board)
- Weighted by Demon + Goblin count
- Penalty for injured players

### Display (Visible Without Click)
- Player Name, Team, Position
- Demon/Goblin counts
- Top 3 props with L10 and Season hit rates
- Injury warnings with "NEW INJURY" tag

---

## Implementation Status (March 12, 2026)

### Current Results
- **Date**: 2026-03-12
- **Events**: 9 NBA games
- **Players**: 117 unique players
- **Total Props**: 4,320
- **DEMONS**: 2,633 (Even +100)
- **GOBLINS**: 1,687 (Negative odds)

### Trending 10 (Live)
1. Jalen Suggs (ORL) - 30 D, 19 G
2. Cade Cunningham (DET) - 27 D, 20 G
3. Jalen Johnson (MIA) - 24 D, 23 G
4. Cooper Flagg (DAL) - 26 D, 19 G
5. Cam Spencer (MEM) - 26 D, 18 G
6. Victor Wembanyama (SAS) - 27 D, 19 G
7. Austin Reaves (LAL) - 26 D, 19 G
8. Danny Wolf (BKN) - 25 D, 20 G
9. Nikola Jokic (DEN) - 24 D, 19 G
10. Shai Gilgeous-Alexander (OKC) - 28 D, 15 G

### Celtics @ Thunder (SGA Game)
- SGA: 28 Demons, 15 Goblins
- Top Goblin: STL Over 0.5 (90% L10, 78% SZN)

---

## API Endpoints

### v3 Endpoints
- `GET /api/v3/status` - Engine status
- `POST /api/v3/sync` - Trigger full sync
- `GET /api/v3/trending` - **NEW: Trending 10**
- `GET /api/v3/players` - All players
- `GET /api/v3/player/{name}` - Player detail
- `GET /api/v3/demons` - All Demons
- `GET /api/v3/goblins` - All Goblins
- `GET /api/v3/search?q={query}` - Search
- `GET /api/v3/board` - Full board

---

## Pending Items

### P0 - Critical
- [ ] Fix authentication flow (Supabase)

### P1 - High Priority
- [ ] Implement 4:00 AM scheduled sync
- [ ] Tank01 injury integration (rate-limited)
- [ ] Pro Tier features

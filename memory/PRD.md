# NBA Best Bets - Demon & Goblin Analytics Engine v3.0

## Product Requirements Document

### Original Problem Statement
Build a high-performance NBA Player Prop Dashboard that identifies "Demons" (harder, boosted lines) and "Goblins" (easier, high-probability lines) from PrizePicks, with Standard lines from main markets, with optimized caching for fast load times.

---

## CLASSIFICATION LOGIC (FIXED March 12, 2026)

### Market-Based Classification
| Type | Market | Odds | Icon |
|------|--------|------|------|
| **STANDARD** | Main market (e.g., `player_points`) | Any | None |
| **DEMON** | Alternate market (e.g., `player_points_alternate`) | +100 | Red Fire |
| **GOBLIN** | Alternate market (e.g., `player_points_alternate`) | ≠+100 (e.g., -137) | Green Ghost |

### Key Implementation
- Standard lines come from main markets without `_alternate` suffix
- Demons and Goblins ONLY come from alternate markets
- Demons have exactly +100 odds (even money)
- Goblins have any odds that are NOT +100 (typically negative like -137)

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
- **Markets**: Both standard (e.g., `player_points`) and alternate (e.g., `player_points_alternate`)

### Classification
- **Standard (No Icon)**: Main market props
- **Demon (Red)**: Alternate market + Even odds (+100)
- **Goblin (Green)**: Alternate market + Other odds (e.g., -137)

---

## Implementation Status (March 12, 2026)

### Classification Fix - COMPLETED ✅
- Fixed line classification logic to correctly differentiate:
  - **Standard**: Props from main markets (no icon)
  - **Demons**: Props from alternate markets with +100 odds (red icon)
  - **Goblins**: Props from alternate markets with odds ≠+100 (green icon)

### Current Results
- **Date**: 2026-03-12
- **Events**: 9 NBA games
- **Players**: 115 unique players
- **Total Props**: 5,405
- **STANDARD**: 1,294 (Main Markets)
- **DEMONS**: 2,512 (Alternate +100)
- **GOBLINS**: 1,599 (Alternate ≠+100)

### Verified Player: Shai Gilgeous-Alexander (SGA)
- **Team**: OKC
- **Standard Props**: 14
- **Demon Props**: 26
- **Goblin Props**: 14

### Trending 10 (Live)
1. Grayson Allen (PHX)
2. Cade Cunningham (DET)
3. Jalen Suggs (ORL)
4. Jalen Johnson (MIA)
5. Cooper Flagg (DAL)
6. Cam Spencer (MEM)
7. Nikola Jokic (DEN)
8. Victor Wembanyama (SAS)
9. Austin Reaves (LAL)
10. Danny Wolf (BKN)

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
- [ ] Fix authentication flow (Supabase) - bypassed currently

### P1 - High Priority
- [ ] Implement 4:00 AM scheduled sync (cron job)
- [ ] Tank01 injury integration (rate-limited currently)
- [ ] Virtual scrolling for 5,000+ props (react-window)

### P2 - Medium Priority
- [ ] "Pro Tier" subscription features
- [ ] Historical line movement tracking

### P3 - Future
- [ ] Push notifications for high-value lines

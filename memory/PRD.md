# NBA Best Bets - Demon & Goblin Analytics Engine v3.0

## Product Requirements Document

### Original Problem Statement
Build a high-performance NBA Player Prop Dashboard that identifies "Demons" (harder, boosted lines) and "Goblins" (easier, high-probability lines) from PrizePicks, with Standard lines from main markets, with optimized caching for fast load times.

---

## BALLDONTLIE STATS ENRICHMENT - COMPLETED (March 12, 2026)

### Real Hit Rates Integration ✅
The Demon Radar now uses **real player statistics** from the BallDontLie API to calculate accurate hit probabilities.

**Key Achievements:**
- **96% Props Enriched**: 4,174 out of 4,354 props have real hit_rates
- **123/128 Players**: Stats found for 96% of players in the board
- **Demon Radar Accuracy**: 7 out of 10 top picks now use real BallDontLie data

### Hit Rates Structure
Each prop now contains:
```json
{
  "hit_rates": {
    "l5": { "hit_rate": 0.6, "games_over": 3, "total_games": 5, "avg": 24.5 },
    "l10": { "hit_rate": 0.7, "games_over": 7, "total_games": 10, "avg": 25.2 },
    "season": { "hit_rate": 0.65, "games_over": 43, "total_games": 66, "avg": 23.8 }
  }
}
```

### Player Search Improvements
- Exact first+last name matching for accuracy (handles "Cameron Johnson" vs "Armon Johnson")
- Partial first name matching (handles "Alex" → "Alexandre Sarr")
- Special character handling (handles "G.G. Jackson" → "GG Jackson")

---

## WAREHOUSE MODEL - IMPLEMENTED (March 12, 2026)

### Zero API Calls Architecture
The app now operates on a **Warehouse Model** where:
- **Frontend reads ONLY from MongoDB** - Zero Odds API calls per click
- **Single batch sync** stores all data in MongoDB
- **No auto-refresh** - Manual sync or 4:00 AM scheduled sync only

### Data Flow
```
Odds API → sync_odds_to_mongo() → MongoDB → /api/v3/cached-props → Frontend
              (SINGLE CALL)        (STORE)      (READ ONLY)
```

### Endpoints
| Endpoint | Purpose | API Calls |
|----------|---------|-----------|
| `GET /api/v3/cached-props` | Read all players from MongoDB | **ZERO** |
| `GET /api/v3/cached-player/{name}` | Read single player from MongoDB | **ZERO** |
| `POST /api/v3/sync-to-mongo` | Batch sync from Odds API | **1 per event** |

### MongoDB Collections
- `dg_live_props` - All props with deduplication
- `dg_cached_board` - Pre-built player board for frontend
- `dg_sync_log` - Sync metadata with timestamps

### Last Sync Results
- **Events**: 11 NBA games
- **API Calls Made**: 12 (1 events + 11 odds)
- **Props Stored**: 5,708 (deduplicated)
- **Players**: 127

---

## DEMON RADAR v2.0 - UPGRADED (March 12, 2026)

### The Intricate Demon Radar Algorithm v2.0 - Opportunity-Focused

**NEW Scoring Formula (Ratio-Based):**
```
Weighted Probability (P) = (L10 × 0.6) + (L5 × 0.4)
Gap Ratio (R) = Demon_Value / Standard_Value (e.g., 1.10 = 10% higher)
Final Score = P / Gap_Ratio

Example: P=0.80, Gap=1.10 → Score=0.727
Example: P=0.80, Gap=1.30 → Score=0.615
```

**Dynamic Thresholds (Ensures Top 10 is NEVER empty):**
1. **STRICT** (P >= 70%): Default mode when 10+ candidates
2. **OPPORTUNITY** (P >= 55%): Auto-lowers if <10 picks in strict
3. **MINIMUM** (P >= 40%): Final fallback to ensure picks exist

### Heat Level (1-5 Flame Icons)
| Flames | Condition | Label |
|--------|-----------|-------|
| 🔥🔥🔥🔥🔥 | L10 >= 90% (9-10/10 games hit) | ON FIRE |
| 🔥🔥🔥🔥 | L10 >= 80% OR perfect 5-game streak | HOT |
| 🔥🔥🔥 | L10 >= 70% OR L5 >= 80% OR 3-game streak | WARM |
| 🔥🔥 | L10 >= 60% | MILD |
| 🔥 | L10 >= 50% | COOL |

### Radar Card Display
- **Heat Level Flames**: Visual indicator (1-5 flames)
- **Heat Label**: HOT, WARM, MILD, COOL
- **Value Score**: P/Gap_Ratio as percentage
- **Gap %**: Percentage above standard line
- **🔥 STREAK**: Badge for 3+ game hot streaks

### Latest Results (51 strict + 44 opportunity candidates)
1. Brandon Williams - P+A 17.5 (P: 88.9%, Score: 0.839) 🔥🔥🔥🔥 HOT
2. Brandon Williams - PRA 21.5 (P: 84.7%, Score: 0.769) 🔥🔥🔥🔥 HOT
3. Derrick White - PRA 27.5 (P: 74.0%, Score: 0.713) 🔥🔥🔥 WARM 🔥STREAK

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

## P1 Features - COMPLETED (March 12, 2026)

### 1. 4:00 AM Daily Scheduler ✅
- **Implementation**: APScheduler with CronTrigger
- **Schedule**: Daily at 04:00 UTC
- **Endpoints**:
  - `GET /api/v3/scheduler-status` - Check scheduler status
  - `POST /api/v3/trigger-scheduled-sync` - Manual trigger
- **Status**: VERIFIED WORKING

### 2. Tank01 API Exponential Backoff ✅
- **Implementation**: fetch_with_backoff() helper function
- **Retry delays**: 1s → 2s → 4s → 8s (with jitter)
- **Cache**: 4-hour TTL in `dg_tank01_cache` collection
- **Graceful degradation**: Works even when API returns 404/429
- **Status**: VERIFIED WORKING

### 3. Frontend List Performance ✅
- **Implementation**: CSS overflow scrolling (`max-h-[60vh] overflow-y-auto`)
- **Note**: react-window v2 was tested but reverted due to API incompatibility
- **Result**: All 115 players render correctly with smooth scrolling
- **Status**: VERIFIED WORKING

---

## Player Detail Page Refactor - COMPLETED (March 12, 2026)

### Category Grouping Layout ✅
- **11 Stat Categories**: PTS, REB, AST, PRA, P+R, P+A, R+A, 3PM, BLK, STL, TO
- **Collapsible Accordions**: Click header to expand/collapse
- **Expand All / Collapse All**: Quick actions for all categories
- **Category Colors**: Each category has distinct gradient (purple/blue/yellow/etc.)

### Ladder Sorting ✅
- Within each category, lines sorted by Target Number (lowest to highest)
- Example for Points: 19.5 → 21.5 → 22.5 → 24.5 → 28.5 → 29.5 → 34.5

### Play Type Labels ✅
| Type | Label | Icon | Color |
|------|-------|------|-------|
| Goblin | Safety Play | Ghost (green) | Green background |
| Standard | Main Line | Gray dot | Gray background |
| Demon | Payout Play | Skull (red) | Red background |

### Hit Rates Display ✅
- **L10**: Last 10 games hit rate (e.g., "L10: 70%")
- **Season**: Full season hit rate (e.g., "Szn: 75%")
- **Color Coding**: Green (≥60%), Yellow (≥40%), Gray (<40%)

---

## NBA Player Headshots - IMPLEMENTED (March 12, 2026)

### Implementation ✅
- **NBA CDN URL**: `https://cdn.nba.com/headshots/nba/latest/1040x760/{nba_id}.png`
- **ID Mapping**: 100+ top NBA players mapped to official NBA IDs
- **Lazy Loading**: Images loaded on-demand with `loading="lazy"`
- **Fallback**: User icon displayed when no NBA ID or image fails

### PlayerHeadshot Component
- **Sizes**: sm (32px), md (48px), lg (64px), xl (96px)
- **Styling**: Circular mask, zoom to focus on face
- **Locations**: TrendingCard, PlayerRow, PlayerDetailPage header

### Mapped Players Include
- Shai Gilgeous-Alexander (1628983)
- Nikola Jokic (203999)
- Luka Doncic (1629029)
- Victor Wembanyama (1641705)
- Cade Cunningham (1630595)
- And 95+ more...

**Note**: Headshots will display once Odds API data is available (API quota was exceeded during testing)

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
- `GET /api/v3/scheduler-status` - Scheduler status (NEW)
- `POST /api/v3/trigger-scheduled-sync` - Manual sync trigger (NEW)

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
- [ ] Complete NBA Player ID Mapping (expand for all players or implement dynamic fetch)
- [ ] Automate 4:00 AM Daily Sync (connect sync_odds_to_mongo to APScheduler)

### P2 - Medium Priority
- [ ] "Pro Tier" subscription features
- [ ] Historical line movement tracking
- [ ] Improve BallDontLie search for remaining 5 players without stats

### P3 - Future
- [ ] Push notifications for high-value lines
- [ ] "Best Bets" summary card per player

---

## Completed Tasks (March 12, 2026)

### BallDontLie Stats Enrichment ✅
- Implemented `_enrich_props_with_stats` method in demon_goblin_engine.py
- Improved player search with exact name matching and fuzzy fallback
- 96% of props now have real L5/L10/Season hit rates
- Demon Radar uses real data for 7/10 top picks

### Fixes Applied
- Fixed `estimated_p` flag to correctly identify props without real BDL data
- Fixed React key warning for duplicate player entries in Demon Radar

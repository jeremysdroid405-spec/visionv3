# PickVision - NBA Player Prop Dashboard

## Overview
PickVision is a high-performance NBA Player Prop Dashboard with a "military tech" aesthetic. The application delivers AI-driven betting insights using PropVision Command Post technology.

## Latest Update: 2026-03-17

### UNIVERSAL PLAYER CARD ARCHITECTURE - COMPLETED ✅
**Single card component replaces ALL other card components across the entire app**

**Architecture: TWO-FUNNEL JOIN**
```
FUNNEL 1 - VAULT (nba_master_hub_2026):
  - Player Identity: Name, Team, Headshot URL
  - Season Stats: PTS, REB, AST, FG%, 3P%, STL, BLK
  - Source: BallDontLie API (synced daily via CRON)

FUNNEL 2 - ODDS (dg_cached_board):
  - Active Props: All PrizePicks lines for the player
  - Tier Classification: DEMON, GOBLIN, or STANDARD
  - Source: Odds API (polled every 30 seconds)
```

**File Path:** `/app/frontend/src/components/dashboard/UniversalPlayerCard.jsx`

**MAP Function (Data Join):**
Location: `/app/backend/services/picks_getter_service.py`, lines 433-444
```python
# Fetch stats from master hub (VAULT FUNNEL)
player_stats = await self._get_player_stats(pick["player_name"], pick["stat_type"], pick["line"])
pick.update(player_stats)  # JOIN: Merge vault data into odds data
```

**Card Behavior:**
- HEADER: Headshot + Name + Season Stats (PTS, REB, AST, FG%, 3P%, STL, BLK)
- BODY: All available props for that player
- GLOW: Card border/glow matches HIGHEST tier (DEMON > GOBLIN > STANDARD)

**Tier Theming:**
- DEMON: Red glow (`border-red-500/50`, `from-red-950/60`)
- GOBLIN: Green glow (`border-green-500/50`, `from-green-950/60`)
- STANDARD: Amber glow (`border-amber-500/40`, `from-amber-950/40`)

**Display Modes:**
- `full`: Complete card with stats and expandable props list
- `compact`: Condensed view for dashboard sections
- `mini`: Minimal inline display

**Used In:**
- Dashboard War Zone section (DEMON picks)
- Dashboard Safe Haven section (GOBLIN picks)
- Dashboard Front Lines section (STANDARD picks)
- Command Post player profile
- Command Post search results

**DEPRECATED Components (still exist but should NOT be used):**
- `/app/frontend/src/components/dashboard/UniversalPickCard.jsx` - DELETED
- `/app/frontend/src/components/dashboard/PickCard.jsx` - DEPRECATED
- `/app/frontend/src/components/dashboard/PlayerCard.jsx` - DEPRECATED
- `/app/frontend/src/components/dashboard/TacticalPlayerCard.jsx` - DEPRECATED

---

## Core Architecture

### Database: `pick_vision` (MongoDB)
- `dg_cached_board`: Live PrizePicks odds (ODDS FUNNEL)
- `nba_master_hub_2026`: Master player vault with BDL stats (VAULT FUNNEL)
- `nba_context_engine`: Manual flags for badges

### Backend Endpoints
- `GET /api/v3/war-zone`: DEMON picks with vault stats
- `GET /api/v3/safe-haven`: GOBLIN picks with vault stats
- `GET /api/v3/front-lines`: STANDARD picks with vault stats
- `GET /api/command/profile/{name}`: Full player profile with baseline_stats
- `GET /api/command/search`: Player search with headshot_url

### Frontend
- React + TanStack Query (SSOT)
- Tailwind CSS + shadcn/ui components
- lucide-react icons

---

## Backlog

### P1 - Near Term
- Fix L10 hit rates showing 0% (data calculation issue in cached board)
- Delete deprecated card components after migration verification
- Add VaultStatsRow to PlayerDetailPage header

### P2 - Medium Term
- Stripe payment integration
- "Copy Parlay" button
- Real Google/Apple OAuth

### P3 - Future
- Mobile-responsive redesign
- Push notifications for trending picks
- Historical performance tracking

---

## Test Verification
- UniversalPlayerCard tested across all sections (100% pass rate)
- Backend APIs verified returning correct data structures
- Command Post profile displays full BDL stats
- Tier-based styling working (DEMON=red, GOBLIN=green, STANDARD=amber)

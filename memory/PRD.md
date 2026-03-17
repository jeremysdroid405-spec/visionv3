# PickVision - NBA Player Prop Dashboard

## Overview
PickVision is a high-performance NBA Player Prop Dashboard with a "military tech" aesthetic. The application delivers AI-driven betting insights using PropVision Command Post technology.

## Latest Update: 2026-03-17

### HIT RATE CALCULATION FIX - COMPLETED ✅
**L5/L10 hit rates now calculated from game_logs**

**Problem:**
- Hit rates were showing 0% for all players
- BDL `/season_averages` endpoint doesn't provide game-by-game data
- Code was returning 0 for hit rates when using baseline_stats

**Fix Applied:**
- Modified `/app/backend/services/picks_getter_service.py`
- Added `calculate_hit_rates()` helper function that processes game_logs
- Hit rates now calculated from actual game data (L5, L10 windows)
- `stats_source` now shows `bdl_baseline+game_logs` when both are used

**Results:**
- Coby White: L10 90% (vs 0% before)
- Tyler Herro: L10 80% (vs 0% before)
- Grant Williams: L10 100% (vs 0% before)

### DEPRECATED COMPONENTS DELETED - COMPLETED ✅
**Removed all redundant card components:**
- `/app/frontend/src/components/dashboard/PickCard.jsx` - DELETED
- `/app/frontend/src/components/dashboard/PlayerCard.jsx` - DELETED
- `/app/frontend/src/components/dashboard/TacticalPlayerCard.jsx` - DELETED
- `/app/frontend/src/components/dashboard/SectionContainer.jsx` - DELETED

**Only card component remaining:** `UniversalPlayerCard.jsx`

---

### UNIVERSAL PLAYER CARD ARCHITECTURE - COMPLETED ✅
**Single card component for the entire app**

**File Path:** `/app/frontend/src/components/dashboard/UniversalPlayerCard.jsx`

**Architecture: TWO-FUNNEL JOIN**
```
FUNNEL 1 - VAULT (nba_master_hub_2026):
  - Player Identity: Name, Team, Headshot URL
  - Season Stats: PTS, REB, AST, FG%, 3P%, STL, BLK
  - Game Logs: For L5/L10 hit rate calculation
  - Source: BallDontLie API + NBA Official

FUNNEL 2 - ODDS (dg_cached_board):
  - Active Props: All PrizePicks lines
  - Tier Classification: DEMON, GOBLIN, STANDARD
  - Source: The Odds API (polled every 30 seconds)
```

**MAP Function (Data Join):**
Location: `/app/backend/services/picks_getter_service.py`
```python
# Calculate hit rates from game_logs
hit_rate_data = calculate_hit_rates(game_logs, stat_type, line)
# Merge vault stats
pick.update(player_stats)
```

---

## Core Architecture

### Database: `pick_vision` (MongoDB)
- `dg_cached_board`: Live PrizePicks odds (ODDS FUNNEL)
- `nba_master_hub_2026`: Master player vault with BDL stats + game_logs (VAULT FUNNEL)

### Backend Endpoints
- `GET /api/v3/war-zone`: DEMON picks with hit rates
- `GET /api/v3/safe-haven`: GOBLIN picks with hit rates
- `GET /api/v3/front-lines`: STANDARD picks with hit rates
- `GET /api/command/profile/{name}`: Full player profile

---

## Backlog

### P2 - Medium Term
- Stripe payment integration
- "Copy Parlay" button
- Real Google/Apple OAuth

### P3 - Future
- Mobile-responsive redesign
- Push notifications
- Historical performance tracking

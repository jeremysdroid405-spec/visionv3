# PickVision - NBA Player Prop Dashboard

## Overview
PickVision is a high-performance NBA Player Prop Dashboard with a "military tech" aesthetic. The application delivers AI-driven betting insights using PropVision Command Post technology.

## Latest Update: 2026-03-17

### ANCHOR-BASED CLASSIFICATION FIX - COMPLETED ✅
**Data APIs fixed and working correctly**

**Problem:**
- Main data APIs (`/api/v3/war-zone`, `/api/v3/safe-haven`, `/api/v3/front-lines`) were broken
- `picks_getter_service.py` had syntax errors from incomplete refactor (duplicate code lines 609-614)
- Frontend showing "No picks available"

**Fix Applied:**
- Fixed syntax error in `/app/backend/services/picks_getter_service.py`
- Removed duplicate code block from `get_front_lines()` method
- APIs now correctly query for DEMON/GOBLIN props from nested `props` array

**Anchor Classification Logic (Simple If/Else):**
```python
if prop_line > anchor_line:
    tier = "DEMON"     # Higher than standard = Hard over (Red)
elif prop_line < anchor_line:
    tier = "GOBLIN"    # Lower than standard = Easy over (Green)
else:
    tier = "STANDARD"  # Equal to standard (Gray)
```

**Results:**
- War Zone: 50 DEMON picks returned
- Safe Haven: 50 GOBLIN picks returned  
- Front Lines: 10 mixed picks (DEMON + GOBLIN interleaved)
- All picks include anchor_line for verification

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

**Deleted Deprecated Components:**
- `/app/frontend/src/components/dashboard/PickCard.jsx` - DELETED
- `/app/frontend/src/components/dashboard/PlayerCard.jsx` - DELETED
- `/app/frontend/src/components/dashboard/TacticalPlayerCard.jsx` - DELETED
- `/app/frontend/src/components/dashboard/SectionContainer.jsx` - DELETED

---

### BDL VAULT STATS - WORKING ✅
**Raw JSON Structure in Master Hub:**
```json
{
  "display_name": "Coby White",
  "team": "CHA",
  "baseline_stats": {
    "fg_pct": 0.45,
    "fg3_pct": 0.367,
    "stl": 0.933,
    "blk": 0.213
  }
}
```

---

## Core Architecture

### Database: `pick_vision` (MongoDB)
- `dg_cached_board`: Live PrizePicks odds with anchor-classified props
- `nba_master_hub_2026`: Master player vault with BDL stats + game_logs

### Backend Services
- `anchor_classification_service.py`: Simple if/else classification logic
- `picks_getter_service.py`: Data fetching and enrichment
- `cached_board_builder_service.py`: Sync and store classified props

### Backend Endpoints
- `GET /api/v3/war-zone`: DEMON picks (line > anchor)
- `GET /api/v3/safe-haven`: GOBLIN picks (line < anchor)
- `GET /api/v3/front-lines`: Mixed DEMON + GOBLIN picks
- `GET /api/command/profile/{name}`: Full player profile

---

## Backlog

### P1 - High Priority
- Run full roster sync to update all players' team/position data
- Verify player name normalization across pipeline
- Clarify Coby White PPG discrepancy (user mentioned 8.1 vs BDL's ~20.3)

### P2 - Medium Term
- Stripe payment integration
- "Copy Parlay" button
- Real Google/Apple OAuth

### P3 - Future
- Mobile-responsive redesign
- Push notifications
- Historical performance tracking

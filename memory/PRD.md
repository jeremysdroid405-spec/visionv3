# PickVision AI - Product Requirements Document

## Original Problem Statement
Build a sports betting analytics platform that helps users identify high-value betting opportunities using data-driven analysis. The core concept is PrizePicks anchor-based classification where betting props are classified as:
- **DEMON**: Alternate line > standard line (high risk/reward)
- **GOBLIN**: Alternate line < standard line (safer plays)
- **STANDARD**: No alternate line available

## Core Architecture

### Data Sources
- **BallDon'tLie (BDL) API**: Primary source for player stats and identity
- **nba_api (Python)**: Secondary source for supplemental player data
- **The Odds API**: Source for betting lines

### Database Structure
- `pick_vision.nba_master_hub_2026`: Master player data vault with deduplicated players, baseline stats, and game logs
- `pick_vision.dg_cached_board`: Cached betting board with player-centric documents containing props arrays

### Tier Logic
1. **Safe Haven (Goblins)**: 
   - Must be GOBLIN tier
   - Line value below player's season average
   - Hit rate >= 80% in last 10 games

2. **Front Lines (Mid-Tier)**:
   - Must be GOBLIN tier
   - 7-12% discount from standard line
   - Line value at least 5% lower than season average
   - Hit rate >= 72% in last 25 games
   - Must NOT qualify for Safe Haven

3. **War Zone (Demons)**:
   - Must be DEMON tier
   - High risk/reward plays

## What's Been Implemented

### Dec 2025 - Data Architecture
- Corrected player stats by updating all sync services to use 2025-26 season
- Performed full data sync updating 102 active players
- Merged 91 duplicate player documents in nba_master_hub_2026
- Implemented Safe Haven and Front Lines backend logic

### Dec 2025 - UI/UX
- Fixed prop labels (removed "O" prefix, consistent naming)
- Updated UniversalPlayerCard for all tier displays
- Expanded player detail header with season stats (PPG, RPG, APG)
- Corrected sorting on Player Detail page (DEMON -> STANDARD -> GOBLIN)

### Mar 2026 - Icon Update
- Icons now reflect actual pick type (DEMON=red, GOBLIN=green) regardless of card theme
- Removed "FRONT LINE" badge from cards
- Front Lines: Yellow card theme + Green Goblin icon
- War Zone: Red card theme + Red Demon icon
- Safe Haven: Green card theme + Green Goblin icon

## Key Files
- `/app/backend/services/picks_getter_service.py` - Safe Haven & Front Lines logic
- `/app/frontend/src/components/dashboard/UniversalPlayerCard.jsx` - Unified player card component
- `/app/frontend/src/components/dashboard/PlayerDetailPage.jsx` - Player detail view
- `/app/backend/services/bdl_comprehensive_sync.py` - BDL data sync

## API Endpoints
- `GET /api/v3/safe-haven` - High-probability GOBLIN picks
- `GET /api/v3/front-lines` - Mid-tier GOBLIN picks
- `GET /api/v3/war-zone` - High-risk DEMON picks
- `GET /api/v3/cached-player/{player_name}` - Single player with all props

## Prioritized Backlog

### P1 - Cleanup
- Delete deprecated components (PickCard, PlayerCard, TacticalPlayerCard)

### P2 - Features
- Add "Last Updated" timestamp to dashboard
- Add "Copy Parlay" feature
- Implement tooltip explaining DEMON/GOBLIN on icon hover

### P3 - Infrastructure
- Implement Stripe for payments
- Integrate real Google/Apple OAuth
- Refactor dg_cached_board collection schema

## Technical Notes
- Always filter dg_cached_board with `"props": {"$exists": True}` to avoid legacy flat documents
- All player documents use bdl_id or nba_api_id as unique identifiers
- Use datetime.now(timezone.utc) for timestamps
- Exclude _id from MongoDB responses

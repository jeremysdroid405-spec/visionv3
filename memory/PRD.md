# PickVision - PRD (Product Requirements Document)

## Overview
PickVision is an NBA player prop betting intelligence platform using AI-driven analysis to identify high-value picks.

## Version History
- **v4.0 (2026-04-04)**: SSOT Architecture Rebuild
  - BDL = Single Source of Truth for all NBA data
  - Odds API = Single Source of Truth for all props
  - ESPN = Source for injuries & news only
  - Removed 440K docs of redundant/stale data
  - Added failsafe retry logic (3x with exponential backoff)
  - Hit rates now calculated FRESH from game logs every time

## Architecture (SSOT Model)

### Data Sources
1. **BDL (BallDontLie)** - SSOT for NBA data
   - Player profiles → `nba_master_hub_2026`
   - Game logs → embedded in player docs as `bdl_game_logs`
   - Team stats → `dvp_rankings`
   - Hit rates → calculated fresh from `bdl_game_logs`

2. **Odds API** - SSOT for props
   - All betting lines → `odds_api_props`
   - Line movements (future)

3. **ESPN** - Injuries & News only
   - Injury reports → `espn_injuries`
   - Breaking news → `espn_news`

### Collections (Clean)
```
nba_master_hub_2026    - Players with embedded game_logs
bdl_player_mapping     - Name matching
dvp_rankings           - Team defense rankings
odds_api_props         - Current betting lines
odds_api_mapping_master - Odds API player name mapping
espn_injuries          - Injury reports
espn_news              - Breaking news
ferrari_safe_haven     - Tier 1 picks (Goblins)
ferrari_front_lines    - Tier 2 picks
ferrari_war_zone       - Tier 3 picks (Demons)
ferrari_parlays        - Generated parlays
ticker_cache           - Live scores
player_photos          - Headshots
nba_career_stats       - Career statistics
nba_context_engine     - AI context
```

### Collections REMOVED (was causing stale data)
- dg_cached_board (duplicate)
- dg_player_stats (stale)
- dg_live_props (duplicate)
- line_history (390K docs)
- line_movements (41K docs)
- dg_* caches (all)

## API Endpoints (V4 - Unified Sync)

### Sync Endpoints
- `POST /api/v4/sync/full` - Full sync with failsafe retry
- `POST /api/v4/sync/bdl` - Sync BDL data only
- `POST /api/v4/sync/odds` - Sync Odds API only
- `POST /api/v4/sync/espn` - Sync ESPN only
- `GET /api/v4/sync/status` - Get sync status

### Data Endpoints
- `GET /api/v4/hit-rates/{player}/{stat}/{line}` - Fresh hit rate calculation
- `GET /api/v4/player/{name}` - Complete player data
- `GET /api/v4/props/today` - All current props

## Tier Classification

### Safe Haven (Goblins) - Green
- L10 Hit Rate ≥ 80%
- L5 Hit Rate ≥ 80%
- Variance < 20 points
- No DNP in L10
- Max 10 picks

### Front Lines - Yellow
- L10 Hit Rate ≥ 70%
- L5 Hit Rate ≥ 70%
- Max 10 picks

### War Zone (Demons) - Red
- Higher risk/reward
- Edge calculation: Hit Rate - 50% (vs +100 odds)
- Max 10 picks

## Red Flags (Auto-Reject)

1. **Low Hit Rate**: L10 < 60% → REJECT
2. **High Variance**: Max-Min spread > 30 pts → TRAP FLAG
3. **DNP Games**: Any 0-minute games in L10 → FLAG
4. **Tough Matchup**: DvP rank ≤ 10 → PENALTY
5. **Line Movement**: >15% drop from open → TRAP FLAG

## Failsafe Retry Logic

Every sync operation:
1. Attempts up to 3 times
2. Exponential backoff (2s, 4s, 8s)
3. Validates data was written before success
4. Logs all failures with error details

## Backlog

### P0 - Critical (DONE)
- [x] SSOT Architecture rebuild
- [x] Unified sync service with failsafe
- [x] Fresh hit rate calculation from BDL
- [x] Database cleanup (removed 440K stale docs)
- [x] Odds API integration (player props via alternate markets)
- [x] Multi-book aggregation (7 books: FanDuel, DraftKings, BetMGM, BetRivers, BetOnline, WilliamHill, Fanatics)
- [x] Sharp line calculation (lowest line from sharp books)
- [x] Consensus line calculation (median across all books)
- [x] Line spread tracking (gap between highest/lowest)
- [x] Sharp edge formula (hit rate - implied probability)
- [x] Ferrari tier builder V2 with variance/DNP detection
- [x] BDL ID on all player docs in master hub

### P1 - High Priority
- [ ] Update frontend to use V4 API endpoints
- [ ] Add variance badge to pick cards ("HIGH VARIANCE" warning)
- [ ] Add multi-book edge visualization to pick cards

### P2 - Medium Priority
- [ ] Google OAuth
- [ ] Stripe payments
- [ ] Mobile optimization

### P3 - Future
- [ ] Push notifications
- [ ] Historical performance tracking
- [ ] Backtest engine

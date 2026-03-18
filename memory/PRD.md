# PickVision AI - Product Requirements Document

## Original Problem Statement
Build a sports betting analytics application (PickVision AI) that provides:
1. **War Zone**: High-risk, high-reward "DEMON" picks using composite scoring
2. **Safe Haven**: Conservative "GOBLIN" picks with high consistency
3. **Most Popular Bets**: Volume-based popular bets (uses synthetic score due to API limitations)
4. **Vision Intel Suite**: Context badges and advanced analytics
5. **Parlay Builder**: AI-generated parlay combinations

## Core Architecture
- **Frontend**: React + TanStack Query + Tailwind CSS + Shadcn UI
- **Backend**: FastAPI + MongoDB
- **Data Sources**: 
  - **BallDontLie API** (PRIMARY) - Official season averages, injuries, advanced stats
  - The Odds API (via emergentintegrations) - Betting lines
  - ESPN API - Breaking news
  - **nba_api** (v1.11.4) - Career stats from NBA.com
  - **Spotrac.com** (Web Scraping) - Contract data for pay_day badge

## BDL API Endpoints Used
| Endpoint | Purpose | Status |
|----------|---------|--------|
| `/season_averages` | Official season stats | ✅ Active |
| `/stats` | Game logs for L5/L10 | ✅ Active |
| `/players/active` | Player ID mapping | ✅ Active |
| `/player_injuries` | Injury reports | ✅ Active |
| `/stats/advanced` | PIE, Net Rating | ✅ Active |
| `/lineups` | Starting lineups | ⏳ Pending (need game_id) |

## Data Model
- `dg_cached_board`: Player-centric documents with props arrays
- `nba_master_hub_2026`: Master player data vault with:
  - `bdl_raw_stats`: Raw official stats from BDL /season_averages
  - `baseline_stats`: Transformed stats with season_avg (official), L5/L10 (calculated)
  - `bdl_game_logs`: Individual game box scores
  - `advanced_stats`: PIE, Net Rating
- `bdl_player_mapping`: Name → BDL ID mappings (537 players)
- `bdl_injuries`: Current injury reports
- `nba_context_engine`: Context flags for badges
- `nba_career_stats`: Cached career stats from nba_api (24h TTL)
- `spotrac_contracts_cache`: Contract year players from Spotrac (24h TTL)

## What's Implemented (March 2026)

### Completed Features
- [x] War Zone with composite scoring
- [x] Safe Haven (Goblin Vault)
- [x] Most Popular Bets (synthetic popularity)
- [x] Vision Intel Suite with 10 context badges
- [x] Parlay Builder (The Gauntlet, The Shield, The Strike)
- [x] BDL Comprehensive Sync - uses OFFICIAL season averages
- [x] BDL Player ID Mapping (537 active players)
- [x] BDL Injuries Sync (25 injuries tracked)
- [x] BDL Advanced Stats (PIE, Net Rating)
- [x] Daily sync scheduler at 4 AM EST (8 steps)
- [x] Intel Search deduplication fix
- [x] L5/L10 stats consistency fix - all calculation functions now sort game logs by date
- [x] Team/Opponent display fix - shows player's actual team (from master hub) and correct opponent
- [x] Injury data display - Combined ESPN + BDL injury sources
- [x] Gassed badge enhanced - Now checks for back-to-back AND heavy minutes (38+)
- [x] **BDL API Game Log Fix**: Increased limit to 100 games, date sorting, DNP filtering
- [x] **Tank01 Complete Purge**: All Tank01 references removed

### March 2026 Updates
- [x] **Anchor-Based Odds Classification**: Demon/Goblin classification now uses PrizePicks main line as anchor, with L5 average fallback
- [x] **Live Career Milestones**: Integrated `nba_api` for real-time career stats from NBA.com (service: `/app/backend/services/nba_career_service.py`)
- [x] **Spotrac Contract Scraper**: Automated `pay_day` badge with live contract data from Spotrac.com (service: `/app/backend/services/spotrac_contract_service.py`)
  - Scrapes UFAs, RFAs, and Player Options
  - 260+ contract year players tracked
  - 24-hour cache to avoid excessive scraping

### Context Badges
| Badge | Source | Status |
|-------|--------|--------|
| `locked_in` | Game logs (L5 PPG > Season + 5) | ✅ Live |
| `milestone` | nba_api career stats | ✅ Live |
| `gassed` | Game logs (back-to-back, 38+ min) | ✅ Live |
| `home_cookin` | Game logs (home vs away splits) | ✅ Live |
| `jet_lag` | nba_context_engine | ✅ Live |
| `legal_noise` | nba_context_engine | ✅ Live |
| `distraction` | Static data (context_data.py) | ⚠️ Static |
| `revenge` | nba_context_engine | ✅ Live |
| `pay_day` | Spotrac scraper | ✅ Live |
| `deep_water` | bdl_injuries + context_engine | ✅ Live |

### API Endpoints (New)
- `POST /api/v3/master-hub/sync-contracts` - Sync contract data from Spotrac
- `GET /api/v3/master-hub/contract-year-players` - List all contract year players
- `GET /api/v3/master-hub/contract/{player_name}` - Get contract info for a player
- `POST /api/v3/admin/sync-career-stats` - Sync career stats from nba_api

## Prioritized Backlog

### P1 - High Priority
- [ ] Schedule daily career stats sync (cron job)
- [ ] Schedule daily contract data sync

### P2 - Medium Priority  
- [ ] Automate `distraction` badge with live trade rumor source
- [ ] Delete deprecated UI components (PickCard, PlayerCard, TacticalPlayerCard)
- [ ] Remove unused route `/api/v3/cached-player/{player_name}`
- [ ] Clean up static data in career_milestones.py (now using live nba_api)

### P3 - Future
- [ ] Add "Last Updated" timestamp to dashboard
- [ ] Add tooltips for context badges
- [ ] Show War Zone composite score breakdown
- [ ] Implement Stripe payments
- [ ] Add "Copy Parlay" feature
- [ ] Google/Apple OAuth authentication

## Technical Notes
- **Stats Source**: BDL `/season_averages` for official stats (DO NOT calculate)
- **Player IDs**: Always use BDL ID for lookups (faster than name search)
- **Game Logs**: BDL API returns games in ASCENDING order. Always sort by date descending.
- **Career Stats**: Uses `nba_api` with 0.6s delay between calls to avoid rate limits. Cached 24h.
- **Contract Data**: Scraped from Spotrac.com. Cached 24h. ~260 contract year players.
- **DB_NAME**: Application uses `pick_vision` database (not `nba_props`).

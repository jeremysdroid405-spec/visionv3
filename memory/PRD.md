# NBA "Demon & Goblin" Analytics Engine v3.0 - PRD

## Original Problem Statement
Build a high-performance NBA Player Prop Dashboard that mimics the PrizePicks user experience by identifying:
- **Demons**: Harder, high-payout alternate lines
- **Goblins**: Easier, high-frequency alternate lines

## Core Requirements
1. Real-time NBA prop data from multiple sportsbooks via The Odds API
2. Player statistics from BallDontLie API with NBA.com fallback
3. Interactive hit-rate analysis for each prop (L5, L10, Season)
4. Advanced predictive analytics (fatigue, pace, usage bumps, volatility)
5. Smart parlay builders for both Demons and Goblins
6. Mandatory user authentication via Supabase
7. Data warehouse architecture with MongoDB caching

## User Personas
- **Sports Bettors**: Primary users seeking edge in NBA player props
- **Data Analysts**: Users interested in advanced statistical insights
- **Casual Users**: Looking for easy picks with high hit rates (Goblins)

---

## Features - Implementation Status

### COMPLETED
- [x] **Prop Data Pipeline** - Live odds from The Odds API, normalized and cached
- [x] **Demon Detection** - Identifies boosted alternate lines (>1.3x value factor)
- [x] **Goblin Detection** - Identifies high hit-rate easy overs (>70%)
- [x] **Player Stats Caching** - MongoDB cache with NBA.com fallback for missing data
- [x] **Interactive Hit-Rate Dropdowns** - L5/L10/Season stats in expandable UI
- [x] **Expanded Parlay View** - Modal showing individual picks in parlays
- [x] **Supabase Authentication** - Full login/signup with protected routes
- [x] **Player Headshots** - Tank01 API integration with proper data flow
- [x] **Advanced Analytics Backend** - Schedule density, pace, usage bumps, volatility
- [x] **Advanced Analytics Frontend** - Display insights in prop dropdowns (Dec 13, 2025)
- [x] **Daily Sync Automation** - APScheduler at 4:00 AM UTC for all data syncs
- [x] **Demo Mode** - Public /v3/demo route allows exploring full dashboard without account (Dec 13, 2025)
- [x] **Vision AI Integration** - Claude Sonnet 4.5 generates "badass" insights for Demons/Goblins (Dec 13, 2025)
- [x] **Injury Intelligence** - ESPN API integration with breaking news ticker and player injury badges (Dec 13, 2025)

### IN PROGRESS
- None currently

### RECENTLY COMPLETED (Dec 13, 2025 - Session 4)
- [x] **Static Vision Intel Briefing Engine (Gemini 3 Flash)**:
  - **New `intel_briefing_engine.py` module**: Generates AI-powered Mission Intel Briefings using Gemini 2.5 Flash
  - **Static Generation Logic**: One-time AI call for each unique PlayerID + GameID combination
  - **Conditional Execution**: Checks `intel_briefing` field before calling API - no duplicate calls
  - **Prompt Template**: Military Scout tone with `[Sector Trend]` and `[Engagement Context]` structure
  - **L10 Stats Integration**: Uses player's L10 average, hit rate, and current betting line
  - **API Endpoints**:
    - `POST /api/v3/generate-intel-briefings`: Manual trigger to generate missing intel
    - `GET /api/v3/intel-briefing/{player_name}`: Get cached intel for a player
  - **Auto-Generation**: Intel generated automatically after sync via `/api/v3/sync-to-mongo`
  - **UI Integration**: 
    - Displays in "THE VISION" section on Demon Radar and Goblin Recon cards
    - Shows `[Sector Trend]` and `[Engagement Context]` military tactical briefings
    - Placeholder text: "Analyzing Sector Data..." when pending
  - **Database**: 
    - Cached in `dg_intel_briefings` collection
    - Also stored as `intel_briefing` field on `dg_cached_board` entries
  - **Model**: `gemini-2.5-flash` with low thinking level for speed/cost efficiency
  - **GOOGLE_API_KEY**: User's Gemini API key stored in backend/.env

### RECENTLY COMPLETED (Dec 13, 2025 - Session 3)
- [x] **Adaptive Sync Engine (Mission-Critical Polling)**:
  - **Polling Tiers**: Standby (>6hrs = 60min), Active (1-6hrs = 10min), Critical (<60min = 60s), Post-Tip (stop)
  - **Stale Intel Detection**: Warns if data >5min old during Mission Critical windows
  - **Priority Refresh**: Immediate high-priority refresh endpoint for stale data
  - **Daily Stats Cron**: Scheduled at 04:00 EST for static player stats
  - **Frontend Display**: Shows "Intel: Xs ago" with live freshness indicator
  - **API Endpoints**: `/v3/sync-status`, `/v3/stale-intel-check`, `/v3/priority-refresh`, `/v3/intel-freshness`
- [x] **Mobile Swipeable Cards (Tinder-style)** - Major mobile UX improvement:
  - All card sections now use horizontal swipe navigation on mobile
  - Each section shows one card at a time with "N / Total" indicator and dots
  - Smooth CSS scroll-snap for native feel
  - Desktop: Remains as responsive grid (2-5 columns depending on screen)
  - Sections updated: Demon Radar, Goblin Recon, The Gauntlet, The Safe Haven, Trending
  - Added `tailwind-scrollbar-hide` for cleaner mobile appearance
- [x] **Dynamic Payout Calculation Engine** - Complete backend integration:
  - **New `payout_engine.py` module**: Handles PrizePicks-style payout calculations
  - **Demon/Standard Formula**: `Total Payout = Base Multiplier × (Mod_1 × Mod_2 × ... × Mod_n)`
  - **Standard Base Multipliers**: 2-pick=3.0x, 3-pick=5.0x, 4-pick=10.0x, 5-pick=20.0x, 6-pick=40.0x
  - **Goblin Formula (Safe Haven)**: `Payout = 1.2^n` (actual PrizePicks formula)
    - 2-pick: 1.4x, 3-pick: 1.7x, 4-pick: 2.1x, 5-pick: 2.5x, 6-pick: 3.0x
  - **Asset Types**: Demons (1.1-1.5x modifier boost), Standards (1.0x)
  - **`/api/v3/calculate-payout` endpoint**: Accepts picks array, returns dynamic payout calculation
  - **Parlay Builder Integration**: All tiers (2-6 pick) return accurate boosted demon payouts
  - **Goblin Recon / Safe Haven Integration**: Uses exact PrizePicks `1.2^n` formula
  - **Testing**: All payouts verified against actual PrizePicks values

### RECENTLY COMPLETED (Dec 13, 2025)
- [x] **V3.2 Data Integrity Crisis Response** - Isolated raw data fetching:
  - **RawStatFetcher service**: New isolated service (`/app/backend/raw_stat_fetcher.py`) that ONLY pulls raw JSON from BallDontLie - ZERO processing
  - **`/api/v3/raw-validation/{player_name}` endpoint**: Returns raw stats for a player
  - **`/api/v3/raw-validation/batch` endpoint**: Batch fetch raw stats for multiple players
  - **`/api/v3/raw-validation-table` endpoint**: Get all validation data
  - **RawValidationTable UI component**: Modal showing RAW API values (Date, Team, Score, PTS, REB, AST) for manual ESPN verification
  - **"VERIFY DATA" button**: Added to dashboard header next to status light
  - **Kill List tested**: Luka Doncic, Anthony Edwards, Naji Marshall all returning raw data
- [x] **V3.1 "Truth Engine" Data Integrity Overhaul** - Critical fix for data hallucination issues:
  - **Naji Safeguard**: Verifies playerID from game logs matches expected BDL player ID, discards data on mismatch
  - **source_verified flag**: All props now tagged with verification status (verified/failed/pending)
  - **verification_status field**: Tracks specific failure reasons (HALLUCINATION_DETECTED, DISCREPANCY, NAJI_SAFEGUARD_FAILED)
  - **`/api/v3/data-status` endpoint**: Reports data integrity status for frontend polling
  - **DataValidationLight component**: Live status light in dashboard header (Green=Verified, Red=Discrepancy, Amber=Pending)
  - **Verification failures logging**: Failures stored in `dg_verification_failures` collection for audit
  - **Sync verification stats**: `run_full_sync` returns verification_stats with counts and rates
- [x] **"War Room" Aesthetic Overhaul (Auth Page)** - Complete redesign with aggressive tactical theme:
  - **Hero Headline**: "The books have an edge. Now, you have a weapon."
  - **System Status Terminal**: Live status display with [SCANNING TANK01 FEEDS...], [LLM HANDSHAKE...], [DEMON TARGETS...], [GOBLIN LOCKS...]
  - **Kill List Section**: Technical spec sheet format (MODEL, LOGIC, INTEL, TARGETING, SAFETY)
  - **Signup Form**: Quote at top, terminal status, "ACCESS KEY" label, `[ CLAIM YOUR EDGE ]` CTA, "operators active" social proof
  - All monospace fonts, blinking terminal cursor, data stream background
- [x] **"PickVision AI" Premium Onboarding Flow** - Complete redesign of Auth page:
  - **Section 1 (Hero)**: PICKVISION AI logo, "Stop guessing. Start winning." headline, Live Scan visual with "GOBLIN DETECTED" reveal, "ENTER THE VAULT" CTA
  - **Section 2 (Bento Grid)**: 4 feature cards (The Seer Model, Demon Radar, Usage Ripple, The Goblin Vault) with elite icons
  - **Section 3 (Promise)**: "In 2026, data is noise. Vision is profit." quote with Demon × Goblin icons
  - **Section 4 (Form)**: Google/Apple one-tap auth, email/password fields, social proof ("Join 12,402 sharps..."), Demo Mode option
  - Interactive animations: scan bar speeds up when typing, silver flash on submit
- [x] **Elite Icon Redesign (Gemini Specs)** - Replaced icons with professional gaming badge style:
  - **Demon (Cyber-Horns)**: Red circular head with sharp horn shapes, white slash eyes, glow filter
  - **Goblin (Sneaky Elf)**: Green circular head with pointed ear fins, dot eyes, smirk
  - Added Vision Sparkle orbit animation for picks with AI insights
  - Glassmorphism containers and state animations (pulse on click)
- [x] **Vision Integration in Parlay Makers** - Added AI insights to all parlay picks:
  - Both "Big Money Builder" and "Goblin Goldmine" now enrich picks with `insight_summary` and `ai_confidence_rating`
  - Parlay card picks show ⚡ indicator and mini Vision preview text
  - Expanded parlay modal shows full Vision section with AI insight box and confidence meter
- [x] **"Ultra-Pro" Icon Refresh** - Replaced all old Skull/Ghost icons with custom SVG glyphs:
  - **DemonIcon**: Red spike/crown shape (King of Longshots)
  - **GoblinIcon**: Green hex-stack (Vault Hunter)
  - Updated across: Dashboard header, stats bar, Radar cards, Vault cards, Parlay Builder, Goldmine, Trending cards, Player detail pages, and all legends

### BACKLOG (P2/P3)
- [ ] "Pro Tier" feature gating based on user tier
- [ ] Historical line movement tracking
- [ ] Push notifications for high-value Demons/Goblins
- [ ] "Copy Parlay" button for social sharing

---

## Technical Architecture

### Backend (FastAPI)
```
/app/backend/
├── server.py                 # API endpoints, APScheduler, auth routes
├── demon_goblin_engine.py    # Core business logic (6500+ lines)
├── intel_briefing_engine.py  # NEW: Gemini 3 Flash AI Intel Briefings
├── adaptive_sync_engine.py   # Real-time odds polling with adaptive frequency
├── payout_engine.py          # Dynamic parlay payout calculations
├── requirements.txt          # Python dependencies
└── .env                      # Environment variables (GOOGLE_API_KEY added)
```

### Frontend (React)
```
/app/frontend/src/
├── App.js                    # Router with ProtectedRoute
├── context/AuthContext.js    # Supabase auth state
├── pages/
│   ├── Auth.js               # Login/Signup UI
│   └── DemonGoblinDashboardOptimized.js  # Main dashboard (2800+ lines)
└── components/
    └── ProtectedRoute.js     # Auth enforcement
```

### Database (MongoDB Collections)
- `player_master_roster` - Player identity source of truth
- `dg_live_props` - Raw prop data from The Odds API
- `dg_cached_board` - Denormalized player+props for fast reads (now includes `intel_briefing`)
- `dg_player_stats` - Cached game logs (L5/L10/Season)
- `dg_daily_insights` - Pre-calculated advanced analytics
- `dg_intel_briefings` - NEW: Gemini-generated Mission Intel Briefings
- `dg_parlays_demon/goblin` - Smart parlay picks
- `dg_verification_failures` - V3.1 Truth Engine audit log

### Scheduled Jobs (APScheduler)
| Job | Schedule | Purpose |
|-----|----------|---------|
| Daily Sync | 4:00 AM UTC | Stats → Odds → Insights |
| Weekly Roster | Sunday 00:00 UTC | Master roster update |

---

## API Endpoints

### V3 Core
- `GET /api/v3/cached-props` - Main dashboard data
- `GET /api/v3/cached-player/{name}` - Player detail with insights
- `GET /api/v3/data-status` - Data integrity status (Truth Engine v3.1)
- `POST /api/v3/sync-odds` - Trigger odds sync
- `POST /api/v3/sync-player-stats` - Sync game logs
- `POST /api/v3/sync-daily-insights` - Calculate analytics

### Analytics
- `GET /api/v3/player-insights/{name}` - Get player insights
- `GET /api/v3/demon-radar` - Top Demon picks
- `GET /api/v3/goblin-vault` - Top Goblin picks
- `GET /api/v3/parlays/{type}` - Get smart parlays

### Auth
- `POST /api/auth/signup` - Create account
- `POST /api/auth/login` - Login

---

## Data Models

### Player Insights (dg_daily_insights)
```json
{
  "player_name": "Kevin Durant",
  "team": "HOU",
  "opponent": "NOP",
  "schedule_density_factor": 1.0,
  "pace_adjustment_factor": 0.994,
  "usage_bump_percent": 0,
  "volatility_score": "Med",
  "volatility_stddev": 8.22,
  "ai_confidence_rating": 70,
  "insight_summary": "📈 Standard projection. No significant modifiers.",
  "is_back_to_back": false,
  "is_three_in_four": false,
  "days_rest": 2,
  "injured_teammates": []
}
```

---

## Integration Dependencies
- **The Odds API** - Live odds (requires API key)
- **BallDontLie API** - Player stats (free)
- **NBA.com API** - Stats fallback (via nba_api package)
- **Tank01 API** - Headshots (free tier)
- **Supabase** - User authentication
- **MongoDB** - Data storage

---

## Known Limitations
1. **Supabase Rate Limits** - Signup emails have rate limits in free tier
2. **Tank01 News** - Reliability for injury/news data unverified
3. **Odds API Limits** - 500 requests/month on free tier

---

## Recent Changes (Dec 13, 2025)
- **Code Refactoring** - Extracted components from 3353-line dashboard to separate files:
  - `/components/dashboard/Icons.jsx` - DemonIcon, GoblinIcon, VisionBadge (126 lines)
  - `/components/dashboard/constants.js` - API config, team logos, stat categories (102 lines)
  - `/components/dashboard/CacheService.js` - Local storage cache utilities (33 lines)
  - Main dashboard reduced from 3353 → 3183 lines (170 lines extracted)
- **"Goblin Goldmine" → "Goblin Recon"** - Renamed across entire codebase (87 occurrences)
- **AI Confidence Per-Prop** - Changed from static player-level to dynamic per-prop calculation
- **"Ultra-Pro" Icon Refresh** - Replaced all Skull/Ghost icons with premium custom SVG glyphs (DemonIcon: red spike, GoblinIcon: green hex-stack)
- **"THE VISION" UI Overhaul** - AI insights now prominent with featured box, confidence meter, and card explainers
- **AI Confidence Meter** - Color-coded progress bar (green >80%, yellow 60-80%, orange 40-60%, red <40%)
- **Demon Radar AI Explainers** - Each card shows "The Vision" insight explaining why it's flagged
- **Goblin Vault AI Explainers** - Safe play cards also show AI reasoning
- **Tank01 + ESPN Hybrid** - Injuries enriched from both sources with return dates
- **Vision AI Injury Context** - Prompt includes: "Factor in the latest injury news for [Team]"
- **Breaking News Ticker** - Scrolling injury-related news banner at top of dashboard
- **Injury Badges on Player Cards** - Red/Yellow pulsing badges with status (Out, Day-To-Day, Questionable)
- **Daily Sync Order** - Updated to 5 steps: injuries → stats → odds → insights → Vision AI

---

## Recent Changes (Dec 13, 2025 - Session 3)
- **Dynamic Payout Calculation Engine** - Complete backend integration:
  - Created `/app/backend/payout_engine.py` with PrizePicks-style payout calculation
  - Formula: `Total Payout = Base Multiplier × (Mod_1 × Mod_2 × ... × Mod_n)`
  - Base Multipliers: 2-pick=3.0x, 3-pick=5.0x, 4-pick=10.0x, 5-pick=20.0x, 6-pick=40.0x
  - Demon modifier: 1.10-1.50x (harder lines = higher payouts)
  - Goblin modifier: 0.70-0.90x (easier lines = lower payouts)
  - New endpoint: `POST /api/v3/calculate-payout`
  - Updated `_build_parlay_builder()` - All 5 tiers (2-6 pick) use dynamic payout engine
  - Updated `_build_goblin_recon()` - All 4 tiers use dynamic payout engine with goblin identification
  - New response fields: `estimated_payout`, `payout_display`, `base_multiplier`, `cumulative_modifier`, `asset_breakdown`
  - Testing: 16/16 backend tests passed

## Recent Changes (Dec 13, 2025 - Session 2)
- **Active Player Photo Sync** - New system to download ALL NBA player headshots:
  - Created `/api/v3/sync-active-players` endpoint that uses Tank01 as the primary source
  - Fetches ~534 active NBA players (not 5000+ historical players)
  - 100% ESPN headshot coverage - every player has a professional photo
  - Fixed Tank01 abbreviation mapping (GS→GSW, NO→NOP, NY→NYK, PHO→PHX, SA→SAS)
  - New endpoints: `/api/v3/players`, `/api/v3/player/{name}/photo`, `/api/v3/team/{abbrev}/roster`
  - Data stored: player_name, team, position, jersey, height, weight, college, espn_id, nba_com_id, photo_url
- **Auth Page "MISSION OBJECTIVES™" Update** - Redesigned Section 2 of onboarding page:
  - Renamed "THE KILL LIST" → "MISSION OBJECTIVES™"
  - Updated system status: "[OPERATIONAL // INTEL_SYNC_ACTIVE]"
  - Added new **SENTIMENT** spec row for "Social Signal™" feature (purple newspaper icon)
  - Updated all spec row text with refined "War Room" copy
  - Added 🔥🔥🔥🔥 fire emojis to Demon Radar and 💎💎💎💎 sapphire gems to Goblin Recon

## Next Steps
1. **UI Sync for Live Payouts** - Update frontend to display new `payout_display` and `asset_breakdown` fields in parlay views
2. **Refactor `DemonGoblinDashboardOptimized.js`** (3500+ lines monolith) - Extract components:
   - RadarCard, VaultCard, PlayerDetailView, TheGauntlet, TheSafeHaven
   - Move to `/app/frontend/src/components/dashboard/`
3. **"Pro Tier" Features** - Gate certain features behind user tier
4. **"Copy Parlay" Button** - Add clipboard copy for social sharing
5. **Social Signals Polling** - Add 30-minute auto-polling for news sentiment & revenge games

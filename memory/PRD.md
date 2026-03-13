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

### IN PROGRESS
- [ ] **Player News/Injury Data** - Tank01 API or alternative for real-time alerts

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
├── demon_goblin_engine.py    # Core business logic (5300+ lines)
├── requirements.txt          # Python dependencies
└── .env                      # Environment variables
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
- `dg_cached_board` - Denormalized player+props for fast reads
- `dg_player_stats` - Cached game logs (L5/L10/Season)
- `dg_daily_insights` - Pre-calculated advanced analytics
- `dg_parlays_demon/goblin` - Smart parlay picks

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
- **Demo Mode** - Added `/v3/demo` public route with demo banner and Login button for non-authenticated access
- **Advanced Analytics Frontend** - Completed integration of insights display in LadderPropRow
- **_add_player_insights()** - New method to merge insights into cached player data
- **Daily Sync Order** - Verified: stats → odds → insights

---

## Next Steps
1. Verify Advanced Analytics UI with authenticated user
2. Implement player news/injury integration
3. Add "Pro Tier" feature gating

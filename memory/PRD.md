# PickVision - NBA Player Prop Dashboard

## Original Problem Statement
Build "PickVision," a high-performance NBA Player Prop Dashboard with a "military tech" aesthetic. Core functionality: AI-driven betting insights identifying "Demons" (high-payout props) and "Goblins" (safer props).

## Core Requirements
1. **Data Integrity:** 100% data accuracy using centralized data pipeline (`dg_cached_board`) with Tank01 player IDs
2. **Advanced Analytics:** LLM for betting insights ("The Vision"), "Social Signal" engine
3. **"War Room" UI/UX:** Dark, cinematic, military tech aesthetic
4. **Dynamic Payout Engine:** Global payout calculator for dynamic parlay estimates
5. **Mobile-First UI:** Fully responsive, optimized for mobile
6. **Subscriptions & Auth:** Stripe for paid tiers, robust authentication
7. **Real-Time Data:** Live score ticker, breaking news feed

## Tech Stack
- **Frontend:** React, TailwindCSS, Shadcn/UI
- **Backend:** FastAPI, Python
- **Database:** MongoDB
- **AI:** Google Gemini Flash (for Strategic Vision)
- **Data:** The Odds API, BallDontLie, Tank01

## What's Been Implemented

### ✅ Completed (December 2025 - March 2026)

**Game Lock Engine v1.0**
- Background task checking `commence_time` every 60 seconds
- Auto-locks games/props that have started
- API endpoints: `/api/v3/lock-status`, `/api/v3/locked-games`, `/api/v3/validate-parlay`
- Frontend `LockedBadge` component

**Strategic Vision Engine v3.0**
- Gemini-powered 2-sentence thesis for each bet
- Correctly maps stat types (REB, PTS, AST, etc.)
- Vision for all pick types: Demon Radar, Goblin Recon, all Parlay Builder picks

**Parlay System**
- Demon/Gauntlet Parlays: 2-6 picks with opponent pairing
- **Goblin/Safe Haven Parlays: 2-6 picks including new 5-pick "Green Stack"**
- Two-team rule enforcement for PrizePicks compliance
- Dynamic payout calculation

**UI/UX Refinements**
- Redesigned Demon/Goblin icons (badass versions)
- Fixed Vision text truncation
- Compact player cards in parlay sections
- Mobile auth page fixes
- Header badge text updates

**Bug Fixes (March 14, 2026)**
- Fixed 5-pick Goblin parlay not generating
- Added null-safety for hit_rates data structures
- Error isolation for parlay builders

### 🔄 In Progress
- None currently

### ⏳ Upcoming (Priority Order)

**P0 - Automated Board Intelligence & Sync**
- Primary Sync (10:30 AM ET): Full global fetch with Vision AI
- Delta Refreshes (1:45 PM, 4:00 PM, 5:45 PM, 7:00 PM ET)
- New Entry/Removal Logic
- Live Ticker Handover (60-second checks)
- "Last Synced: MM:SS" footer display

**P1 - Stripe Integration & Authentication**
- Payment processing for subscription tiers
- User authentication system

**P2 - Refactor Dashboard**
- Break down `DemonGoblinDashboardOptimized.js` (4300+ lines)

### 📋 Backlog
- Pro Tier feature gating
- Copy Parlay button
- Mobile bottom navigation
- T-Minus countdown timer (live)

## Key API Endpoints
- `POST /api/v3/sync` - Full data sync
- `GET /api/v3/goblin-recon` - Goblin parlays
- `GET /api/v3/parlay-builder` - Demon parlays
- `GET /api/v3/demon-radar` - Top demon picks
- `POST /api/v3/run-lock-check` - Manual lock check
- `GET /api/v3/lock-status` - Current lock status

## Database Collections
- `dg_cached_board` - Main player/prop data
- `goblin_recon` - Pre-built goblin parlays
- `demon_radar` - Top demon picks
- `parlay_builder` - Demon parlays

## Known Issues
- Deployment requires proper MONGO_URL configuration for production
- Continue with Google/Apple buttons are placeholders

## File Structure
```
/app
├── backend/
│   ├── demon_goblin_engine.py   # Main sync & parlay logic
│   ├── game_lock_engine.py      # Auto-lock system
│   ├── intel_briefing_engine.py # AI Vision generation
│   ├── payout_engine.py         # Payout calculations
│   ├── server.py                # FastAPI routes
│   └── .env
├── frontend/
│   └── src/
│       ├── pages/
│       │   ├── DemonGoblinDashboardOptimized.js
│       │   └── Auth.js
│       └── components/
│           └── dashboard/Icons.jsx
└── memory/
    └── PRD.md
```

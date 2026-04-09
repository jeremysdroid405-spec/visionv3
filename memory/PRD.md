# PropVision AI - Product Requirements Document

## Original Problem Statement
Build a local-first betting intelligence app restructuring React/FastAPI to integrate Vegas Killer ML models into the Prop Board. Establish cascading tier distribution (Safe Haven, Front Lines, War Zone) strictly gated by Hit Rate, CV, and ML Edge/Probability, using DraftKings odds as the separator. Integrate Gemini 3.1 Pro as "Vision Intel Layer" for composite scoring.

## Core Architecture
```
/app
├── backend/
│   ├── routes_archive/              # 9 archived legacy route files
│   ├── routes/                      # Active route files (cleaned)
│   │   ├── core_v3.py, board.py, tiers.py, scheduler.py
│   │   ├── ferrari_tiers.py, vacuum.py, vision.py
│   │   └── __init__.py              # Route registration (cleaned)
│   ├── services/
│   │   ├── vision_intel_service.py  # Batched Gemini intel processing
│   │   ├── oracle_apex_service.py   # 3-Gate Qualification checks
│   │   ├── ferrari_tier_service.py  # Tier routing + Vision Intel
│   │   ├── odds_api_service.py, odds_sync_service.py
├── frontend/src/
│   ├── pages/Dashboard.jsx
│   ├── hooks/useLiveOdds.js
│   ├── components/dashboard/
```

## Key Technical Concepts
- **3-Gate System:** HR >= 80%, CV <= 0.35, VK Edge/Prob thresholds per tier
- **DK Classification:** Safe Haven (DK <= -250), Front Lines (-249 to +199), War Zone (>= +200)
- **Vision Intel:** Batched Gemini 3.1 Pro API calls for composite scoring
- **No Fall-Throughs:** Props failing tier gates are discarded, not cascaded

## Key DB Collections
- `oracle_apex_analyzed`: Vegas Killer model outputs
- `dg_cached_board`: Enriched props joined with Oracle data
- `ferrari_safe_haven` / `ferrari_front_lines` / `ferrari_war_zone`: Final tier collections

## API Endpoints
- `GET /api/v3/ferrari/safe-haven`
- `GET /api/v3/ferrari/front-lines`
- `GET /api/v3/ferrari/war-zone`
- `GET /api/v3/ferrari/rebuild`
- `GET /api/v3/player-with-badges/{name}`

## 3rd Party Integrations
- Gemini AI (google-genai, Model: gemini-3.1-pro-preview) - User API Key
- BallDontLie (BDL) API - User API Key
- The Odds API - User API Key

---

## Completed Work (April 2026)

### Session 3 - Gemini Intelligence Gate (April 9, 2026)
- [x] Implemented Gemini 3.1 Pro as true intelligence gatekeeper (not just summary generator)
- [x] Added `adjusted_confidence` scoring (0-1) combining VK probability + contextual factors
- [x] TRAP verdicts now KILL props (removed from selection, not just labeled)
- [x] Low intel_score (≤3) or low confidence (<45%) triggers automatic kill
- [x] Updated prompt to user's exact specification for PropVision Intelligence Engine
- [x] Gate logs show kills: "KILLED: Karl-Anthony Towns REB - TRAP verdict"
- [x] Fixed API endpoints to return Vision Intel data from stored collections
- [x] Added Vision Intel display to UniversalPlayerCard (CHALK/VALUE/TRAP badges, intel summary)

### Session 2 - Route Cleanup (April 9, 2026)
- [x] Fixed 502 error from orphaned route imports in __init__.py
- [x] Archived 9 unused route files to routes_archive/
- [x] Stripped 98 dead endpoints
- [x] Scrubbed 24 duplicate API routes

### Session 1 - Core Features
- [x] War Zone tier deduplication bug fixed (Demon probabilities: HR>=7, No Edge Req, Prob>=40%)
- [x] UI "VK Model" renamed to "Vision Model" with tier-specific glow colors
- [x] Filtered injuries from breaking news ticker
- [x] Capped parlay probabilities at 99%
- [x] Removed gate fall-through logic (strict tier assignment)
- [x] Vision Intel Service (vision_intel_service.py) with Gemini batched calls
- [x] Consolidated Gemini integrations to prevent overlapping API calls
- [x] Generated API route analytics exports

### Session 2 - Route Cleanup
- [x] Archived 9 unused route files to routes_archive/
- [x] Stripped 98 dead endpoints
- [x] Scrubbed 24 duplicate API routes
- [x] Fixed 502 error from orphaned route imports in __init__.py (April 9, 2026)

---

## Priority Backlog

### P1 - Critical
- [ ] **Upstream Prop Duplication**: Investigate `odds_sync_service.py` for duplicate prop insertion into `dg_cached_board`

### P2 - Important
- [ ] Establish automated daily prop capture (Forward-Testing Infrastructure)
- [ ] Integrate Google/Apple OAuth (Emergent-managed)
- [ ] Implement Stripe for payments

### P3 - Nice to Have
- [ ] Refactor `vegas_killer_model.py` (~2000 lines)
- [ ] Further API controller optimization

---

## Testing Credentials
Use "Demo Mode" button on frontend login page.

## Critical Notes for Agents
1. **Vision Intel**: Use BATCHED API calls only (one per tier) - single prop calls cause timeouts
2. **Google API Key**: Use user's `GOOGLE_API_KEY`, NOT Emergent LLM key
3. **DvP Context**: Low DvP rank (#1-5) = BAD matchup, High (#26-30) = GOOD matchup

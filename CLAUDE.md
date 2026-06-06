# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

PropVision is a real-time betting intelligence terminal for PrizePicks player props. It pre-computes edges using pace, usage, matchup data, and tempo, then ranks picks across three tiers: **Safe Haven** (bank plays, ≤-240 DK odds), **Front Lines** (value hunting, -145 to -239), and **War Zone** (high-ceiling demon props, ≥+150). It covers NBA and MLB with separate models.

## Commands

### Backend

```bash
cd /var/www/app/backend
source venv/bin/activate
uvicorn server:app --host 0.0.0.0 --port 8001 --reload
```

Run all backend tests:
```bash
cd /var/www/app/backend && python -m pytest tests/ -x
```

Run a single test file:
```bash
python -m pytest tests/test_propvision_v7_pipeline.py -x -v
```

Lint:
```bash
flake8 .
black .
```

### Frontend

```bash
cd /var/www/app/frontend
yarn install
yarn start         # dev server on port 3000
yarn build         # production build
```

## Architecture

### Stack

- **Frontend:** React 19 + Tailwind CSS + shadcn/ui (Radix primitives), React Query for server state, React Router v7, Framer Motion
- **Backend:** FastAPI + uvicorn, Motor (async MongoDB), APScheduler for cron jobs
- **Database:** MongoDB — collections are accessed exclusively through `COLL(concept, sport)` from `services/config/collection_names.py`, never via bare string literals
- **Auth:** Supabase (JWT). Backend verifies JWTs; frontend uses `@supabase/supabase-js`
- **AI:** Google Gemini (`google-genai`) for Vision Intel scouting reports
- **ML Models:** Scikit-learn + XGBoost `.pkl` files in `backend/models/` — the VK2 ("Vegas Killer 2") model family is the current production scorer

### Data Flow

1. **Ingest** — `services/odds_api_service.py` fetches lines from The Odds API; `services/engines/nba_master_hub.py` and BallDontLie API provide player stats. Lines land in `nba_live_props` / `mlb_live_props`.
2. **Scoring** — `services/propvision_v7_engine.py` applies the Board Score formula (Sharp Implied + PP Edge + Hit Rate − Penalties) and hard-kills disqualifying props. The VK2 model family (`vegas_killer_*.pkl`, `vk2_*.pkl`) generates calibrated probability scores.
3. **Board Build** — Scored props are tiered and cached into `nba_cached_board` / `mlb_cached_board` via `services/board_service.py`.
4. **Vision Intel** — `services/vision_ai_service.py` calls Gemini to generate scouting reports per pick. JIT generation is handled by `services/jit_vision_intel_reaper.py`.
5. **Serving** — `routes/ferrari_tiers.py` reads the cached board and serves the UI. All routes are registered in `routes/__init__.py` via `register_all_routes()`.

### Key Backend Services

| Service | Purpose |
|---|---|
| `services/engines/nba_master_hub.py` | SSOT for NBA player identity and stats — all reads must go through `fetchPlayerIntel()` |
| `services/propvision_v7_engine.py` | Core scoring engine: prop filtering, tier classification, parlay optimizer |
| `services/vegas_killer_model.py` / `vk2_*.pkl` | ML model loading and inference for probability calibration |
| `services/engines/adaptive_sync_engine.py` | Manages sync frequency based on data staleness |
| `services/engines/intel_briefing_engine.py` | Pre-caches Vision Intel summaries for the board |
| `services/injury_service.py` | 60s polling loop; surfaces actionable injury alerts for players with live props |
| `services/config/collection_names.py` | Collection name registry — `COLL("live_props", "nba")` → `"nba_live_props"` |

### Frontend Structure

- `src/pages/` — Route-level pages: `NBADashboard.jsx`, `MLBDashboard.jsx`, `Dashboard.jsx`, `Auth.js`
- `src/components/dashboard/` — Shared UI components: `UniversalPlayerCard.jsx`, `IntelligenceModal.jsx`, `ParlayTicket.jsx`, `PlayerDetailPage.jsx`
- `src/services/DataService.js` — All API calls go through this module using `REACT_APP_BACKEND_URL`
- `src/context/` — `AuthContext.js` (Supabase session), `SportContext.js` (NBA/MLB toggle), `ThemeContext.js`

### MongoDB Collections

All collection names are resolved through `COLL(concept, sport)`. The canonical concept keys:

- `live_props` → `nba_live_props` / `mlb_live_props`
- `board_cache` → `nba_cached_board` / `mlb_cached_board`
- `master_hub` → `nba_master_hub_2026` / `mlb_master_hub_2026`
- `master_roster` → `nba_master_roster` / `mlb_master_roster`
- `prop_scores` → `nba_prop_scores` / `mlb_prop_scores`
- Shared: `injuries_normalized`, `live_injuries`, `users`

Never use bare collection name strings — always `COLL(concept, sport)` or `COLL.shared(concept)`.

### Scheduled Jobs (APScheduler)

Jobs run in `server.py` startup via `AsyncIOScheduler` with MongoDB job store. Key times (EST):
- **4:00 AM** — Daily full sync (odds + stats + injuries)
- **4:25 AM** — BDL game logs sync (hit rates)
- **~5:00 AM** — Board rebuild from fresh data

### Environment Variables

Backend `.env` (`/var/www/app/backend/.env`):
```
MONGO_URL=mongodb://localhost:27017
DB_NAME=pick_vision
SUPABASE_URL=...
SUPABASE_ANON_KEY=...
SUPABASE_SERVICE_ROLE_KEY=...
JWT_SECRET=...
GEMINI_API_KEY=...
BALLDONTLIE_API_KEY=...
ODDS_API_KEY=...
CORS_ORIGINS=https://propvision.bet
```

Frontend `.env` (`/var/www/app/frontend/.env`):
```
REACT_APP_BACKEND_URL=https://propvision.bet
REACT_APP_SUPABASE_URL=...
REACT_APP_SUPABASE_ANON_KEY=...
```

The `frontend/scripts/ensure-backend-url.js` (run as `prestart`/`prebuild`) enforces `REACT_APP_BACKEND_URL` stays pointed at production and doesn't get reset by deployment platforms.

## Critical Constraints

- **No bare MongoDB collection strings.** Always use `COLL(concept, sport)` from `services/config/collection_names.py`.
- **No direct external API calls from the frontend.** All Odds API and stats API calls must go through backend services.
- **All NBA player data reads must use `fetchPlayerIntel()`** from `services/engines/nba_master_hub.py` — it is the SSOT.
- The DemonGoblinEngine and all its dependent routes were deleted as of 2026-04-22. Do not attempt to restore or reference them; the "universal path" (ferrari_tiers read-side) replaced it.

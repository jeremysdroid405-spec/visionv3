# Prop Vision — Product Requirements Document

## Original Problem Statement
Restructure React/FastAPI betting app to a 100% Local-First Database Model with multi-sport support. Implement Google/Apple OAuth and Stripe for payments. PRODUCT REQUIREMENTS: 100% ID-based joins. Universal Opportunity Models and Probability modeling. Enforce regression and mutation tests for all backend logic. FIX PROPVISION PERMANENTLY — SSOT ENFORCEMENT / FIELD OWNERSHIP HARDENING. Transition architecture to strict Single Source of Truth for all user-visible fields.

**ACTIVE DIRECTIVE: PROP VISION STABILIZATION PLAN**
Freeze all feature/UI work until the system is permanently stabilized via the 6-phase plan.

## Stabilization Status
- Phase 4B SSOT cleanup — DONE
- §3 Tier freshness stamping (`board_freshness.py`) — DONE
- §4 Detection source freshness SLO logic — DONE
- §6 Vision Intel coverage (universe alignment + dual-tag mirror writes) — DONE
- JIT Vision Intel Reaper (5-min cadence) — DONE
- Universal Badge Architecture (`badge_enrichment.py`, `mlb_environmental_badges.py`) — DONE
- 0-write guard in `prop_scores_store.write_versioned_scores` — DONE
- **Breaking News Ticker stabilization (2026-05-08) — DONE**
- 30-min double-pass sign-off SLO — BLOCKED on upstream odds API blackout (Props 0)

## Architecture
- Frontend: React + Shadcn UI
- Backend: FastAPI + APScheduler + MongoDB
- Core pipeline: `universal_odds_sync` → `live_props` → `delta_dirty_queue` → `detector` → `delta_steps` (rescore) → `master_sync` (hourly board build & VI enrichment)
- Ticker pipeline: scheduler → `routes/live.py::sync_news_headlines` (NBA daily 9:26 UTC) and `sync_mlb_news_headlines` (MLB hourly :32) → `ticker_cache` collection

## Key Endpoints
- `/api/v3/ferrari/all`
- `/api/v3/board`
- `/api/live/news?sport=nba|mlb`
- `/api/live/scores?sport=nba|mlb`

## Key Collections
- `ticker_cache`
- `nba_prop_scores`, `mlb_prop_scores`
- `nba_cached_board`, `mlb_cached_board`
- `scheduler_jobs`

## Backlog (Frozen until stabilization sign-off)
### P0 — Blocked on stabilization sign-off
- Implement Emergent-managed Google OAuth (must use `integration_playbook_expert_v2`)
- Implement Stripe payments (must use `integration_playbook_expert_v2` with pod test keys)

### P0 — Environmental
- Wait for upstream odds API to exit blackout, then run final 30-min double-pass SLO

### P1
- Decompose `Dashboard.jsx` (2,000+ lines)
- Decompose `picks_getter_service.py` (3,200+ lines)
- NFL-ready config scaffold
- STL/BLK/Double-Double model training for NBA
- MLB `ContextBadgeService` fix (deferred)

### P2
- Forward-test resolver dashboard
- Universal Vision Intel Refactor (YAML configs)

## Recent Changelog
### 2026-05-08 — Breaking News Ticker stabilization patch
- Swapped ESPN RSS (HTTP 202, bot-fenced) for ESPN public JSON news API (`site.api.espn.com/apis/site/v2/sports/{basketball/nba|baseball/mlb}/news`)
- Fixed CBS Sports regex to tolerate both `<![CDATA[...]]>` and plain `<title>` wrappers (was capturing 0/36 headlines)
- Removed dead Bleacher Report feed block (HTTP 404)
- Added hourly `mlb_ticker_sync` scheduler job (CronTrigger minute=32) — MLB news was frozen at 2026-04-11 because no MLB ticker job was registered
- Added default `User-Agent` + `Accept` headers via `TICKER_HTTP_HEADERS` to both ticker httpx clients
- Verified: NBA 15 headlines (5 ESPN + 5 CBS + 5 injuries_db), MLB 11 headlines (6 ESPN_MLB + 5 CBS_MLB), both tickers `synced_age=31s` post-patch
- Files: `backend/routes/live.py`, `backend/server.py`

## Files of Reference
- `backend/routes/live.py` — ticker sync + endpoints
- `backend/server.py` — scheduler config
- `backend/services/board_freshness.py`
- `backend/services/master_sync.py`
- `backend/services/jit_vision_intel_reaper.py`
- `backend/services/badge_enrichment.py`
- `backend/services/mlb_environmental_badges.py`
- `backend/services/scoring/prop_scores_store.py`
- `backend/scripts/production_readiness_slo_check.py`
- `backend/routes/ferrari_tiers.py`

## Test Credentials
N/A (no auth integrations live yet; blocked on stabilization).

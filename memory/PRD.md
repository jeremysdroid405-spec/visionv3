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
- Added `mlb_ticker_sync` scheduler job — MLB news was frozen at 2026-04-11 because no MLB ticker job was registered
- Added default `User-Agent` + `Accept` headers via `TICKER_HTTP_HEADERS` to both ticker httpx clients
- Files: `backend/routes/live.py`, `backend/server.py`

### 2026-05-08 — Cached_board materialization (architecture fix)
- **New SSOT**: `{sport}_cached_board` is now a materialized view of `{sport}_prop_scores[version_tag=final-{sport}-rt]`. No independent tier logic; tier assignments carried verbatim from -rt.
- **New service**: `services/board_snapshot_publisher.py::publish_board_snapshot(db, sport)` — single writer. Upsert-only (never deletes), preserves doc-level enrichment (photo_url, injury_status, context_badges, etc.), empties stale players' `props[]` without removing the doc, bails on empty source (zero-wipe guarantee).
- **Delta Engine integration**: added `PublishBoardSnapshotStep` (pos 5 in `DEFAULT_DELTA_STEPS`). Runs only when `written>0` or `retired_modified>0`; skips when upstream lock is held; failure-isolated.
- **master_sync integration**: step 7 replaced (`stamp_cached_board_freshness` → `publish_board_snapshot`). Single build path; metrics key renamed `7_cached_board_snapshot_publish`.
- **Verified (natural delta ticks, no manual triggers)**:
  - NBA tick rescored 153 props → publisher rebuilt 64 players, emptied 76 stale, from 3,074 active -rt props in 1.18s.
  - MLB tick rescored 212 props → publisher rebuilt 318 players, emptied 0 stale, from 6,603 active -rt props in 4.29s.
  - SLO §3 Tier Freshness now PASSES naturally (was FAIL 12.5-hour staleness before patch).
  - API tier endpoints (`/api/v3/ferrari/all`) continue to serve enriched picks unchanged.
- **Tests**: `tests/test_board_snapshot_publisher.py` — 7 passing: source=final-{sport}-rt, rebuild-on-written, skip-on-zero-writes, empty-source-preserves, master_sync-uses-same-path, freshness-matches-rt-timestamps, no-independent-tier-assignment; plus stale-player-emptied-not-deleted, ingestion-fields-preserved-via-canonical-key-merge.
- **Files**: `backend/services/board_snapshot_publisher.py` (new), `backend/services/pipeline/delta_steps.py`, `backend/services/master_sync.py`, `backend/tests/test_board_snapshot_publisher.py` (new).

### 2026-05-08 — Ticker 15-min cadence + source-protection patch
- NBA `ticker_sync` cadence: `CronTrigger(minute='0,15,30,45')` (was daily 9:26 UTC)
- MLB `mlb_ticker_sync` cadence: `CronTrigger(minute='5,20,35,50')` (was hourly :32) — offset 5 min from NBA so fetches never overlap
- Added `TICKER_PROTECTED_STATUS = {202, 403, 429}`; both sync functions log `WARNING` per source on protected/empty responses
- Both sync functions now track `external_count` and **skip the upsert** when `external_count == 0` and a last-good cache exists — preserves cache through transient blackouts
- `get_breaking_news` is now cache-only: removed `_fetch_news_fallback` helper; cold cache returns empty list instead of triggering request-path HTTP. Scheduler is the sole writer of `ticker_cache`.
- Verified: healthy cycle = 15 NBA / 11 MLB headlines; simulated blackout (ESPN→202, CBS→403) preserved baseline cache untouched (`preserved_cache:True`).
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

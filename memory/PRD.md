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
- **Combo-family VK2 routing fix (2026-05-09) — DONE**
  - PA / PR / RA combo synth now uses VK2 component μ instead of legacy VK1.
  - Allen PA alt 9.5: μ 21.89 → 12.95 (verified live).
  - Aggregate Δμ: PA −1.51, PR −0.92, RA −0.54 (n=687 combo props rescored).
  - 60 combo props now correctly fail direction-gate; 1 was fake-passing WZ (Allen PA alt 14.5).
  - Post-fix sensitivity: 0 WZ rejects in vision_score_v2 [55,60) — floor move 60→55 yields zero gain. Floor stays at 60.
  - Tests: `tests/test_combo_synth_vk2_routing.py` (8 tests, all passing — incl. mutation guard).
  - Audit: `/app/audit_reports/wz_alt_line_projection_audit_2026-05-09.md`.
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

### P1 — After stabilization sign-off (gated by `ARCHITECTURE.md` directive)
- **Consolidation #1**: Retire `services/mlb_cached_board_builder.py` + duplicate MLB board-rebuild path. Replace call sites with `publish_board_snapshot(db, "mlb")`. Verify NBA + MLB run identical publisher.
- **Consolidation #2**: Audit + document/retire remaining duplicate writers, builders, legacy collections, multi-source truths, route-specific board logic, hidden overlay writers.
- **Consolidation #3**: Produce `/app/memory/SYSTEM_OWNERSHIP.md` per the template in `ARCHITECTURE.md` — canonical source / single writer / allowed readers / enrichment layers / freshness owner / scheduler owner / cache owner for every major entity.
- Decompose `Dashboard.jsx` (2,000+ lines) and `picks_getter_service.py` (3,200+ lines).
- NFL config scaffold (must follow universalization rule — config-first, not a new pipeline).
- STL/BLK/Double-Double model training for NBA.
- MLB `ContextBadgeService` fix (deferred).

### P2
- Forward-test resolver dashboard
- Universal Vision Intel Refactor (YAML configs)

## Recent Changelog
### 2026-05-08 — Universal injury redistribution model (math rebuild)
**Problem**: card outputs were unrealistic (Wemby +12% on a David Jones OUT, LeBron +15 mins, SGA +12% on JWill OUT). Root cause was `_get_beneficiaries` applying flat per-rank constants (+15 mins / +12% usage / +10 mins / +8% usage / +5 mins / +5% usage) that did not depend on injured magnitude or absorber saturation.
- **New helper** `services/injury_vacuum_service.py::_compute_redistribution(injured, teammates)`. Sport-agnostic two-layer model:
  - **Layer 1 (Minutes)**: weight = mins_headroom × mpg_proximity × bench_factor × position_match. Per-player saturation cap = `8.0 × (1 − baseline_mpg/40)^0.7`. Hard ceilings: `MAX_INDIVIDUAL_MPG=40`, `INDIVIDUAL_MIN_SHARE_CAP=0.45`.
  - **Layer 2 (Usage)**: usage_share × elasticity, where `elasticity = (usage_headroom/MAX_INDIVIDUAL_USAGE)^1.5`. Saturated alphas absorb a fraction of what bench-rotation players absorb per share.
- **Output exposure**: every card now carries `baseline_minutes`, `projected_minutes`, `minutes_delta`, `baseline_usage`, `projected_usage`, `usage_delta`, `redistribution_share`, `elasticity_factor`. Display-side `minutes_bump` / `usage_bump` are back-compat but reflect modeled deltas.
- **Cross-sport safety**: helper returns zero-delta scaffolds when canonical fields (`minutes_per_game`, `usage_percentage`) are absent. MLB beneficiary code (separate path) is unchanged; opt-in only.
- **Verified live (NBA, 2026-05-08)**:
  - Wemby (David Jones OUT, primary): **+15 → +0.9 mins, +12% → +0.2% usage**.
  - SGA (JWill OUT, primary): **+15 → +2.1 mins, +12% → +0.1% usage**.
  - LeBron (Luka OUT, primary): **+15 → +1.8 mins, +12% → +0.5% usage**.
  - Kobe Bufkin (Luka OUT, tertiary bench): **+5 → +6.9 mins, +5% → +4.6% usage** (correctly absorbs more minutes than LBJ).
- **6 regression pytest cases pass** (`tests/test_injury_redistribution.py`): low-rotation injury (alpha minimal), secondary-star OUT (alpha dampened), minutes ceiling (≤40), usage elasticity (saturated < open-canvas), no noise flood (shares sum to 1, ceilings respected), cross-sport zero-delta scaffold.
- **Files**: `backend/services/injury_vacuum_service.py`, `backend/tests/test_injury_redistribution.py` (new). NO frontend, NO scoring, NO tier, NO cached_board, NO ticker, NO Vision Intel touch.

### 2026-05-08 — Live Injury Advantage star-qualifier widening
**Audit-only finding** (no caches involved): cards aren't stale — they're being **filtered out** by an overly-strict 22% usage gate in `InjuryVacuumService._is_star_player`. The recompute path is correct; it just rejects rotation regulars whose injury is genuine breaking news. Smoking gun: OG Anunoby (NYK, OUT, usage=19.4%, mpg=33+) was being silently dropped while Luka Doncic (36.8%) and Jalen Williams (25.2%) passed.
- **Single change** in `services/injury_vacuum_service.py`:
  - Lowered `SECONDARY_ALPHA_THRESHOLD` from 22.0 → 18.0.
  - Added `ROTATION_MINUTES_THRESHOLD = 24.0` — admit on `usage_percentage >= 18` OR `minutes_per_game >= 24`.
  - Both `nba_star_usage_cache` and `nba_master_hub_2026.advanced_stats` lookup paths use the same dual-axis admission. New `alpha_tier="rotation"` for the minutes-only path so downstream code can distinguish.
- **Verified live**: card count went from 6 alerts (2 distinct injured) → 15 alerts (5 distinct injured) within a single restart. NEW visible: **OG Anunoby (NYK)**, **Kevin Huerter (DET)**, **David Jones (SAS)**. Luka Doncic + Jalen Williams retained. No noise flood (DiVincenzo / Sorber correctly filtered as expected).
- **Acceptance**: 6 of 6 user criteria pass (OG visible, existing cards retained, no flood, API live, freshness badge still 7s, no duplicates).
- 5 unit tests (`tests/test_injury_advantage_qualifier.py`) lock down the new admission rules.

### 2026-05-08 — Live Injury Advantage freshness pipeline (5 fixes)
Audit identified 6 stale stages; user approved 5 (deferred legacy-writer cleanup #5 to 48h post-rollout).
- **#1 Cadence fallback** (`services/injury_sensor.py::_get_cadence`). When `live_scores_cache.games` is stale, fall back to a `{sport}_cached_board` activity probe — return `CADENCE_ACTIVE (120s)` when any sport has live cached_board props. Sport-agnostic. NBA was stuck at IDLE=300s mid-season pre-fix.
- **#2 Severity gate lowered** (`services/injury_triggered_rescore.py::_on_event`). Accept `high` AND `medium`. Q→OUT (`status_escalated`, tier_delta=1), `return_date_shifted`, de-escalations now trigger live rescore + cached_board patch. Pre-fix, all medium events were dropped.
- **#3 _patch_cached_board hardening** (same file). Off-board injured players contribute their `team` from `injuries_normalized` (so teammates still patch). Update filter accepts `bdl_id` fallback so name-format drift can't silently no-op.
- **#4 Per-player dedup** (`services/injury_sensor.py::_emit_changes`). Dedup key changed from `sport|team` to `sport|player_key`. Sibling injuries within the 5-min recency window are no longer suppressed.
- **#6 Wire-level freshness signal** (`routes/vacuum.py`, `routes/mlb_vacuum.py`). Live-alerts response ships `served_at`, `oldest_source_synced_at`, `newest_source_synced_at`, `source_age_seconds`. Frontend `LiveInjuryAdvantageSection` renders an "Updated Xs ago" badge with green/amber/red banding (<2min / <5min / older).
- **Verified live**: NBA cached_board patches firing within seconds of ESPN sensor poll (116/150 docs with `last_injury_rescore_at`). NBA + MLB endpoints both ship `source_age_seconds=17`. 6 regression pytest cases pass (`tests/test_injury_freshness.py`).
- **Files**: `backend/services/injury_sensor.py`, `backend/services/injury_triggered_rescore.py`, `backend/routes/vacuum.py`, `backend/routes/mlb_vacuum.py`, `frontend/src/pages/Dashboard.jsx`, `backend/tests/test_injury_freshness.py` (new).
- **Deferred**: #5 (kill `live_injury_micro_sync`, `scheduled_hourly_injury_sync`, `scheduled_live_injury_check`) — review window ≥48h.

### 2026-05-08 — Universal stat-label adapter
- New `frontend/src/utils/statLabel.js`. `getStatLabel('player_points_rebounds_assists')` → `'PRA'`, `getStatLabel('batter_hits_runs_rbis')` → `'H+R+RBI'`, etc. Strips alternate suffixes, humanizes unknowns. ONE helper used by Command Center, Player Detail, board cards, MarketMoves, simulation toasts.
- Migrated `UniversalPlayerCard.jsx`, `lib/PickVisionUtils.jsx`, `PlayerDetailPage.jsx`, `CommandPost.jsx`, `Dashboard.jsx`, `MarketMoves.jsx`. 12 frontend Jest tests pass.

### 2026-05-08 — Universal Command Center analytics (volatility + correlation)
- **Per-leg volatility** now derives from canonical `cv` (clamp 0..2.0). Side-aware hit-rate spread fallback when `cv` is null. CV-scaled label thresholds (LOW <0.20, MED 0.20–0.45, HIGH ≥0.45). Pre-fix metric was bounded ~0.5 from `|h10-h5|` noise — war-zone vs safe-haven indistinguishable.
- **Correlation** rewritten as pairwise rules over canonical leg fields (`player_name`, `event_id`, `team`, `sport`): `same_player → 0.85`, `same_game → 0.40`, `same_team(+sport) → 0.20`, else `0`. Aggregate ρ = mean. Penalty = 1 − clamp(ρ × 0.5, 0, 0.5). Response ships `correlation_score`, `correlation_kind`, per-leg `cv`, `volatility_source`.
- **Frontend** Correlation card shows `correlation_kind` text (Independent · 0% / Same Game · -X% / Same Team · -X% / Same Player · -X%). Never blank.
- **Verified (5 new pytest)**: Brunson+Edwards low-CV→high-CV: vol 0.43 → 1.13. Brunson AST 2.5+9.5: kind=`same_player` score 0.85 pen 42.5%. Brunson+Embiid same event: kind=`same_game` score 0.40 pen 20%. Mixed NBA+MLB: kind=`none` score 0 pen 0%.

### 2026-05-08 — Universal Command Center prop source
- **New helper**: `services/command_center_props.py::get_command_center_props(db, sport, *, player_name=None, canonical_key=None)` — sport-agnostic SSOT reader. Reads canonical rows from `{sport}_prop_scores` filtered to `version_tag=final-{sport}-rt` AND `active=True`. ONE adapter (`_to_canonical_prop`). No cached_board, no stat-level joins, no sport-specific branching beyond the collection name. Adding NFL = one entry in `SCHEDULED_SPORTS`.
- **New route**: `GET /api/command/props?sport={sport}&player_name={name}` (also accepts `canonical_key=...`). Validates sport via `SCHEDULED_SPORTS`. Returns `{success, sport, version_tag, player_name, meta, props[]}`. 400 on unknown sport / missing params, 404 on unknown player.
- **Canonical row contract** (no legacy aliases): `canonical_key, sport, player_name, stat_type, line, recommendation, direction, hit_rate_l5, hit_rate_l10, hit_rate_l20, hit_rate_over, hit_rate_under, p_true_active, edge_vs_fair, vision_score, cv, team, opponent, tier, tier_reason, pp_odds, dk_odds, fd_odds, bol_odds, mgm_odds, tier_reference_book, tier_reference_odds, event_id, game_start_utc, bdl_player_id, is_home`. `h5_rate / h10_rate / hit_rate / hit_rates` are NEVER read or emitted on this path.
- **Frontend**: `useCommandCenterProps` hook in `useLiveOdds.js`. CommandPost replaces the legacy `usePlayerProfile` → `.map(line => ({...}))` reshape with verbatim canonical rows from the new endpoint. New `buildCanonicalLeg` helper forwards canonical fields ONLY to `/api/command/simulate` for both inline-add and Quick-Add (`pendingLeg`) paths.
- **Verified (testing agent + 8 pytest cases)**:
  - Brunson NBA AST: 20 alt rows, 8 distinct `hit_rate_l10` values (0/10/30/40/50/60/70/90) — original "all 90%" smearing bug eliminated.
  - Contreras MLB: 25 canonical rows, all `mlb|`-prefixed `canonical_key`, `version_tag=final-mlb-rt`.
  - Mixed NBA+MLB simulation: convergence rate 64.0%, grade C — works.
  - Frontend CommandPost smoke: search → profile shows varying L10 chips → click adds canonical leg. Network tab confirms `/api/command/props` (not `/api/v3/player-with-badges`).
- **Files**: `backend/services/command_center_props.py` (new), `backend/routes/command.py`, `backend/tests/test_command_center_props.py` (new), `frontend/src/hooks/useLiveOdds.js`, `frontend/src/components/dashboard/CommandPost.jsx`.

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

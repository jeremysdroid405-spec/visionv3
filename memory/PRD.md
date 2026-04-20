# PRD — NBA/MLB Ferrari / PropVision AI

## Product Goal
Restructure React/FastAPI betting app into a 100% local-first MongoDB
architecture with multi-sport support, automated feature engineering, and
a unified pipeline anchored on canonical odds data. Surface pricing
anomalies through market-consensus probabilities.

## Architecture
- **Frontend:** React + Shadcn UI — `/app/frontend/src/pages/Dashboard.jsx`,
  hooks in `/app/frontend/src/hooks/useLiveOdds.js`.
- **Backend:** FastAPI — `/app/backend/server.py`,
  `/app/backend/routes/ferrari_tiers.py`.
- **Scoring:** `services/scoring/recompute.py` (ranking),
  `services/scoring/scoring_stack.py` (tier gates),
  `services/scoring/adapters/{nba,mlb}_scoring.py`.
- **Board reader (universal, sport-agnostic):** `services/board/reader.py`
  with adapters in `services/board/adapters/{nba,mlb}.py`.
- **MLB pipeline:** `services/mlb_master_sync.py` (Steps 1-5) + XGBoost
  models in `services/mlb_high_friction_model.py`,
  `services/mlb_physical_engine.py`, `services/mlb_vegas_killer_model.py`.
- **DB:** MongoDB — `nba_prop_scores`, `mlb_prop_scores`, `nba_live_props`,
  `mlb_live_props`, `historical_odds`, `bdl_historical_game_logs`,
  `nba_master_hub_2026`, `mlb_cached_board`.

## Key API Endpoints
- `GET /api/v3/ferrari/safe-haven?sport={nba|mlb}[&sort=gap]`
- `GET /api/v3/ferrari/front-lines?sport={nba|mlb}[&sort=gap]`
- `GET /api/v3/ferrari/war-zone?sport={nba|mlb}[&sort=gap]`
- `POST /api/v3/odds/sync?sport={nba|mlb}` — upstream Odds-API fetch
- `POST /api/v3/mlb/build-board` — mlb_cached_board intersection
- `POST /api/mlb/sync/master` — direct → `MLBMasterSync.run_master_sync()`
- `POST /api/nba/sync/master` — dispatches UnifiedPipeline(NBAAdapter)

---

## Completed Work (Session 2026-04-20)

### NBA
- **2026-04-20** Default sort flipped to projection-gap (`ranking_score_v2`).
  Removed the `default vs gap` toggle from `Dashboard.jsx`. Hardcoded
  `nbaSortParam = 'gap'`. Backend retains `?sort=default` for debug.
- Board-truth + board-faithful replay audits proved the board yields **+14.1 pp
  real-odds ROI over the equivalent candidate Top-25** at ~22% smaller sample.
  α=0.40 ranking + per-tier cap of 10 + player dedupe are accretive.

### MLB
- **MLB forensic audit** revealed: 3 XGBoost models on disk, all loadable
  (`MLBHighFrictionModel` with 15 stats, `MLBPhysicalEngine`/`MLBVegasKillerModel`
  with 5). Production live board was running on a linear cushion heuristic over
  a 5-year weighted stat average with `vk_source="weighted_avg"` on every row.
- **File 1 applied** — `services/scoring/adapters/mlb_scoring.py`:
  preserved `MLBHighFrictionModel`'s `predicted` and `std_dev`, passed
  `model_projection`, `model_sigma`, `p_true_method="model"`, `p_true_model`
  into `ScoringContext`. 92% of MLB rows now have the full model triplet.
- **File 2 applied** — `routes/ferrari_tiers.py`:
  added `_get_mlb_tier_picks_from_scores` (structural mirror of NBA helper),
  flipped the 3 MLB Ferrari branches to read from `mlb_prop_scores` via the
  universal board reader, gated `enrich_mlb_prop_with_averages` as a no-op
  for `p_true_method=="model"`, made `_dedupe_picks_by_player(sort=…)`
  sport-agnostic. MLB now supports `?sort=gap` identically to NBA.
- **MLB `commence_time` forensic**: proved upstream was fresh (22 future MLB
  events available) and our ingest had no stale-preservation; root cause
  was the misnamed `/api/mlb/sync/master` endpoint only dispatching the
  publish phase on cached data instead of calling the actual master sync.
- **Option C applied (end-to-end MLB refresh endpoint)** — two minimal diffs:
  - `routes/ferrari_tiers.py`: `/api/mlb/sync/master` made fire-and-forget
    (returns HTTP 202 in ~250 ms, runs in background via `asyncio.create_task`).
    Added `_mlb_master_sync_state` module-level tracker so a second call
    returns `{reason: "already_running", last_run: {...}}` for polling.
  - `services/mlb_master_sync.py`: added **Step 6 universal recompute**
    at the end of `run_master_sync()` so `mlb_prop_scores` gets
    `p_true_method`, `p_true_model`, `model_projection`, `model_sigma`,
    and `ranking_score_v2` populated in a single endpoint call.
  Verified: background run completed in 200 s, 6 Ferrari endpoints serve
  100% `model`-source picks with rs_v2 populated, no manual recompute needed.

### End-to-End Verification (2026-04-20)
All 6 MLB Ferrari endpoints return HTTP 200 with 100% `vk_source="model"`,
`p_true_method="model"`, `ranking_score_v2` populated on every served pick.
Default sort and `?sort=gap` both work. Picks visibly re-order on gap sort.

---

## Known Operational Gaps (Flagged, Not Fixed)

### P1 — Original roadmap
- **Injury-Rank Phase 2 (usage-sorted teammate semantics).** Replace
  `my_index` loop-order in `services/injury_advantage.py` with a descending
  sort against `nba_master_hub_2026.advanced_stats.usage_percentage`.
- **Emergent Google OAuth** via `integration_playbook_expert_v2`.
- **Stripe payments** (pod test keys; via `integration_playbook_expert_v2`).
- **Dashboard.jsx refactor** — break the 2000-line file into focused sections.

### Resolved this session (MLB)
- ~~MLBAdapter pipeline doesn't compute ranking_score_v2~~ — resolved via
  Step 6 universal recompute inside `run_master_sync()`.
- ~~/api/mlb/sync/master exceeds ingress proxy 120 s~~ — resolved via
  fire-and-forget 202 pattern.

### P2 — Backlog
- NBA-native tier admission table (NBA stats currently fall through to the
  MLB `"hits"` gate default — not a bug, worth formalizing).
- Wave 3 post-migration cleanup Drop Step B.
- "Batch 15" script plumb-through.
- Historical MLB odds corpus (for MLB board-faithful replay).
- Wind Tunnel weather API (MLB).
- Retire Legacy Writers.
- Regenerate stale introspection artifacts.
- Cross-sport logo collision audit.
- Audit `scripts/ensure_indexes.py` / `scripts/init_database.py` for legacy DB
  hardcodes.

---

## Integrations
- BallDontLie API (user key)
- The Odds API (user key — verified active with 4.7M requests remaining)
- Google Gemini (user key)
- Emergent LLM key available as fallback.

## Health
- Broken: None
- Mocked: None

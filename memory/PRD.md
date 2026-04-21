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

## Carbon-Copy Migration — Stage 2 Complete (2026-04-20)

### Shared scoring ladder (eliminates D3 + D10)
- **Added `resolve_p_true_ladder()`** in `services/scoring/scoring_stack.py`
  (exported via `services/scoring/__init__.py`). Single canonical
  probability resolver shared by every sport scoring adapter.
- **Ladder order:** `model → hit_rate → vk2 → fair`. `preferred_method`
  kwarg lets any rung jump to the front (NBA's "vk2" opt-in preserved).
  `fair` rung uses market-implied `tp` → `p_true_method` is never
  `"none"` whenever a reference market exists.
- **NBA scoring adapter** now delegates: replaced inline
  `if vk2 … elif model … else hit_rate` block with a ladder call. `tp`
  computed BEFORE the ladder so fair rung has input. `edge_pct` math
  preserved via `tp_for_gates`.
- **MLB scoring adapter** now delegates: previously emitted
  `p_true_method="model"` or `None`. Now computes `p_true_hit_rate`
  from existing hit-rate, calls the shared ladder, and applies the
  side-aware UNDER flip (which was missing before).
- **MLB pipeline adapter** (`services/adapters/mlb_adapter.py`)
  replaced the legacy `write_prop_scores(db, scored)` with
  `recompute_sport(db, 'mlb', version_tag='final-mlb')` so a single
  canonical scoring pass populates `mlb_prop_scores` identically to NBA.
- **MLB master sync** Step 6 renamed `UNIVERSAL RECOMPUTE` →
  `CANONICAL SCORING PASS`; metric key `6_universal_recompute` →
  `6_canonical_scoring`. No longer framed as a workaround.

### Stage 2 acceptance verification (`/api/mlb/sync/master`)
- Master sync: 143 s total (4 s odds / 1 s board / 61 s BDL / 62 s
  tiers / 0 s ripple / 16 s canonical scoring).
- `mlb_prop_scores` (tag=`final-mlb`): 2560 docs, 33 tiered picks,
  **100.00% `p_true_method` coverage among qualified rows**.
- `nba_prop_scores`: 2460 docs, 131 tiered picks,
  **100.00% `p_true_method` coverage among qualified rows**.
- Method breakdown: NBA model=2213 / hit_rate=171 / fair=49 / none=27
  (1.10%, all `tier=unqualified`); MLB model=2366 / hit_rate=131 /
  fair=47 / none=16 (0.62%, all `tier=unqualified`).
- Ferrari endpoints continue to serve with `p_true_method='model'` +
  `ranking_score_v2` populated; default and `?sort=gap` both correct.


## Carbon-Copy Migration — D1 Residual Cleanup Complete (2026-04-21)

### MLBMasterSync class removed; MLB now runs through UnifiedPipeline
- **`services/mlb_master_sync.py` DELETED** (592 LOC removed).
- **New module `services/pipeline/master_steps.py`** (205 LOC)
  introduces a sport-agnostic `PipelineStep` ABC + 4 concrete MLB
  steps (`MLBOddsSyncStep`, `MLBCachedBoardBuildStep`,
  `MLBBDLSplitsPrefetchStep`, `MLBCanonicalRTScoringStep`). Each step
  wraps an existing shared service function — no new computation is
  introduced. Legacy Steps 4 (oracle tier rebuild) + 5 (lineup ripple)
  are intentionally omitted since Stage 4 gated them off in the live
  carbon-copy flow.
- **`SportAdapter` base (unified_pipeline.py)** gains two registration
  hooks — `pre_score_pipeline_steps()` and `post_score_pipeline_steps()`
  — both returning `[]` by default so NBA is unaffected.
- **`MLBAdapter`** registers the three pre-score steps + one
  post-score step (RT shadow write).
- **`UnifiedPipeline.run_master_sync()` (new)** drives the full
  master-sync: pre-score steps → canonical `self.run()` → post-score
  steps. Returns a metrics dict compatible with the old
  `MLBMasterSync.run_master_sync()` shape (`{success, started_at,
  completed_at, total_duration_seconds, steps, errors}`).
- **`RebuildCoordinator.dispatch_master_sync("mlb")`** no longer
  imports `services.mlb_master_sync`; it instantiates
  `UnifiedPipeline(MLBAdapter(), self._db)` and awaits
  `run_master_sync()`.

### Pre-existing None-sort bug fixed en route
`MLBAdapter._apply_retention_cap` used `x.get(sort_key, 0)` which returns
`None` when the key is present but set to `None`. Sort crashed on mixed
float/None comparison. Fix: `x.get(sort_key) or 0`. Bug existed before
D1 cleanup but only surfaced once the full carbon-copy pipeline path
ran tier-selection via Phase 5 at master-sync time.

### Files changed
- **DELETED** `services/mlb_master_sync.py` (−592 LOC).
- **NEW** `services/pipeline/__init__.py`, `services/pipeline/master_steps.py` (+205 LOC).
- `services/unified_pipeline.py` — adapter hooks + `run_master_sync()` (+109 LOC).
- `services/adapters/mlb_adapter.py` — step registration + None-sort fix (+29 LOC).
- `services/rebuild_coordinator.py` — swap MLB dispatcher (+12 LOC).
- **Net: −251 LOC.**

### D1 acceptance verification
- `/api/mlb/sync/master` via `UnifiedPipeline.run_master_sync()`:
  - 97 s total, `success=True`, `errors=[]`.
  - Steps executed in order: `1_odds_sync` (3.5 s) → `2_cached_board`
    (0.8 s) → `3_bdl_prefetch` (13.0 s) → `6_canonical_scoring`
    (65.5 s) → `6rt_realtime_shadow` (14.4 s).
- `mlb_prop_scores` tag distribution: `final-mlb` = 1956 docs,
  `final-mlb-rt` = 1956 docs (bit-identical).
- All 3 Ferrari MLB endpoints HTTP 200 with `pipeline.source =
  mlb_prop_scores[tier={tier},version=final-mlb-rt]` and populated
  tiered picks.
- NBA endpoints HTTP 200 (no regressions — NBA path untouched).
- `services/mlb_master_sync.py` physically absent on disk; `grep` over
  the entire backend for `MLBMasterSync` / `get_mlb_master_sync`
  returns only comment/docstring references in Stage-narrative
  metadata; zero live imports.
- `dispatch_master_sync("mlb")` call-chain now resolves through
  `UnifiedPipeline(MLBAdapter()).run_master_sync()` exclusively.

### Carbon-Copy Migration Status — 12/12 ELIMINATED
All 12 identified deviations (D1–D12) are resolved. MLB is now a
true carbon copy of NBA architecturally:
- Same orchestration dispatch (`RebuildCoordinator.dispatch_master_sync`).
- Same scheduler registration (`SCHEDULED_SPORTS`).
- Same board reader path (universal adapter; UI reads `final-{sport}-rt`).
- Same Ferrari route resolver (`SPORT_TIER_HELPERS`).
- Same scoring ladder (`resolve_p_true_ladder`).
- Same scoring-write enrichment hook (`ScoringAdapter.enrich_score_doc`).
- Same master-sync driver (`UnifiedPipeline.run_master_sync`).
- Same `PipelineStep`-based ingest framework.

### Remaining caveats (post-migration)
1. **NBA hasn't yet opted into the new master-sync framework.**
   NBAAdapter's step lists return `[]`; `dispatch_master_sync("nba")`
   still calls the legacy `NBAMasterSync.run_full_pipeline()`.
   Optional follow-up: populate NBA's `pre_score_pipeline_steps()`
   with its NBA.com-scraper / BDL / etc. steps and delete
   `nba_master_sync.py` the same way we just deleted the MLB one.
2. **Legacy tier collections** (`mlb_safe_haven` / `mlb_front_lines` /
   `mlb_war_zone`) still receive writes from `UnifiedPipeline`'s
   `_atomic_publish` (driven by `adapter.tier_collections`). The UI
   does not read them (Stage 4), but they continue to accrue stale
   data. Optional cleanup: make `tier_collections` return `{}` for MLB
   and short-circuit `_atomic_publish` when empty.

---


## Carbon-Copy Migration — Stage 8 Complete (2026-04-21)

### Unified sport-agnostic scheduler (eliminates D7)
- **New module `services/scheduled_sports.py`** (146 LOC) providing:
  - `ScheduledSportConfig` frozen dataclass (sport, interval minutes,
    daily-cron UTC time, event severity).
  - `SCHEDULED_SPORTS` dict registry (`nba` + `mlb`).
  - `run_scheduled_master_sync(sport)` canonical entry point that
    publishes a `scheduled_safety` `BoardEvent` → consumed by
    `RebuildCoordinator.dispatch_master_sync(sport)` (same code path
    used by `/api/{sport}/sync/master`).
  - Two pickle-able module-level callables:
    `scheduled_master_sync_nba` and `scheduled_master_sync_mlb`.
  - `SPORT_INTERVAL_CALLABLES` dict mapping each sport to its
    serialisable callable (required by MongoDBJobStore).
- **`server.py` scheduler section** replaced the hand-written NBA +
  MLB interval-job registrations (jobs `hourly_full_sync`,
  `hourly_mlb_full_sync`) with a loop over `SCHEDULED_SPORTS`
  registering `hourly_{sport}_master_sync` jobs. Net
  **−19 LOC in `server.py`** (49 added, 68 deleted).
- Old per-sport shims `scheduled_hourly_full_sync` and
  `scheduled_hourly_mlb_full_sync` retained but collapsed to a
  one-line delegate that calls `run_scheduled_master_sync(sport)` —
  prevents breakage of any still-pending job pointers in MongoDB
  during hot-reload.
- **Legacy MongoDB job IDs deleted** (`hourly_full_sync`,
  `hourly_mlb_full_sync`) so there's no double-fire overlap with the
  new unified jobs.

### Sport-specific data-ingest crons LEFT IN PLACE
NBA `nba_l5l10_batch_{1..5}`, `bdl_game_values_sync`,
`bdl_game_logs_sync`, `daily_hard_refresh`, MLB `mlb_bdl_game_values_sync`,
`mlb_bdl_game_logs_sync`, `mlb_daily_refresh`, and the ticker sync
remain as bespoke daily crons — these are sport-specific data-ingest
workflows (NBA.com scraping, BDL enrichment), not master-sync
orchestration. They are outside D7's scope (which is specifically
about the master-sync scheduling layer). Stage 8's contract: one
scheduler mechanism for master-sync orchestration across every live
sport. Achieved.

### Stage 8 acceptance verification
- `SCHEDULED_SPORTS.keys()` = `['nba', 'mlb']`. Both configs carry
  interval=60 min + daily cron entries.
- MongoDB `scheduler_jobs` collection now contains
  `hourly_nba_master_sync` and `hourly_mlb_master_sync` entries (both
  with valid next-run timestamps). Legacy `hourly_full_sync` +
  `hourly_mlb_full_sync` purged.
- Manual master-sync endpoints still work: `POST
  /api/nba/sync/master` → HTTP 202, `POST /api/mlb/sync/master` →
  HTTP 202 (both return `accepted=True`).
- All 6 Ferrari endpoints return HTTP 200 with correct picks count and
  `pipeline.source` tags (`…,version=final-{sport}-rt]`).
- Pickle round-trip of `scheduled_master_sync_{nba,mlb}` callables
  succeeds → MongoDBJobStore serialisation works correctly.
- Adding NFL is a single-line entry in `SCHEDULED_SPORTS` plus a
  3-line `scheduled_master_sync_nfl` module-level function plus one
  dict entry in `SPORT_INTERVAL_CALLABLES` — zero `server.py` edits.

### `services/mlb_master_sync.py` deletion eligibility (D1 cleanup)
**NOT YET SAFE TO DELETE.** The coordinator's
`dispatch_master_sync("mlb")` still imports and calls
`MLBMasterSync.run_master_sync()` internally (Stage 1 decision — the
coordinator is the thin dispatch wrapper, the per-sport master-sync
classes are the actual pipelines). Full deletion of
`mlb_master_sync.py` requires first folding its 6-step pipeline into
a sport-agnostic orchestrator (e.g. `UnifiedPipeline(MLBAdapter)`),
which touches the odds-sync / BDL-splits / oracle / ripple stages and
is substantially larger than a pure deletion. Flagged as a
post-Stage-8 follow-up (D1 residual).

---


## Carbon-Copy Migration — Stage 7 Complete (2026-04-21)

### MLB real-time shadow parity (eliminates D9)
- **`final-mlb-rt` tag now exists and is populated.** MLB master sync
  writes both tags on every run:
  - **Step 6 (canonical baseline)** → `final-mlb` (unchanged).
  - **Step 6-RT (real-time shadow)** → `final-mlb-rt` (new, same
    `recompute_sport` pass with the `-rt` tag; bit-identical score
    fields to the canonical tag).
- **UI reader pinned to `final-mlb-rt`** via
  `MLBBoardAdapter.version_tag = "final-mlb-rt"` — structural parity
  with `NBABoardAdapter.version_tag = "final-nba-rt"`.
- **Stage-6 dispatch template** updated: MLB `source_tag_template` now
  reads `mlb_prop_scores[tier={tier},version=final-mlb-rt]`. All
  Ferrari MLB responses surface this new source identity in
  `pipeline.source` for observability.
- **Future work (out of Stage 7 scope):** wire an MLB equivalent of
  `services/injury_triggered_rescore.py` so `final-mlb-rt` receives
  sub-cycle patches on injury events, matching NBA's event-driven RT
  behaviour exactly. Until then, RT freshness = master-sync cadence
  (typically ~3 min, same as `final-mlb`).

### Files updated
- `services/mlb_master_sync.py` — added Step 6-RT block (second
  `recompute_sport` call with `version_tag='final-mlb-rt'`; metric key
  `6rt_realtime_shadow`).
- `services/board/adapters/mlb.py` — `version_tag` flipped to
  `"final-mlb-rt"`.
- `routes/ferrari_tiers.py` — `SPORT_TIER_HELPERS["mlb"]
  .source_tag_template` updated to `final-mlb-rt`.

### Stage 7 acceptance verification
- `/api/mlb/sync/master`: 157 s total (up from ~115 s — expected, adds
  one ~40 s RT recompute pass), `success=True`, `errors=[]`.
- `mlb_prop_scores` tag distribution:
  - `final-mlb`: 1749 docs, 28 tiered.
  - `final-mlb-rt`: 1749 docs, 28 tiered (bit-identical).
- All tiered rows on both tags have `p_true_method='model'`,
  `ranking_score_v2`, `intel_suite` populated.
- All 3 MLB Ferrari endpoints serve HTTP 200 with `pipeline.source`
  = `mlb_prop_scores[tier={tier},version=final-mlb-rt]`. `?sort=gap`
  still re-orders (rs2 1.058 → 2.046).

### Cross-sport comparison — NBA `final-nba-rt` empty-board state
- Pre-existing state (unchanged by Stage 7): NBA has 32 docs at
  `final-nba-rt` (0 currently tiered/active) — populated only by
  `injury_triggered_rescore` events, not by any master-sync seeding.
- NBA master sync (`nba_master_sync.py::run_elite_sync_phase7`) writes
  only legacy `elite_*` collections — it has no equivalent of MLB's
  new Step 6-RT.
- **Conclusion**: NBA's empty-board state is independent of Stage 7.
  The proper fix for NBA is to apply the same Step 6-RT seeding
  pattern (one-line `recompute_sport` call) inside NBA's master sync
  so the RT tag stays fresh between injury events. That is a separate
  ticket — flagged below under Next Action Items.

---


## Carbon-Copy Migration — Stage 6 Complete (2026-04-21)

### Ferrari endpoint IF-chain replaced with SPORT_TIER_HELPERS dispatch (eliminates D4)
- **New dispatch infrastructure** in `routes/ferrari_tiers.py`:
  - `@dataclass(frozen=True) SportTierHelpers` with three fields:
    `source_tag_template`, `fetch_picks`, `post_process`.
  - `SPORT_TIER_HELPERS: Dict[str, SportTierHelpers]` registry with
    entries for `"nba"` and `"mlb"`.
  - `_apply_jit_injury_filter(picks, sport, tier)` — sport-uniform
    wrapper around `live_injury_micro_sync.jit_filter_picks`.
  - `_post_process_nba_picks(picks, tier)` — side-aware strip +
    Gemini UNDER enrichment.
  - `_post_process_mlb_picks(picks, tier)` — defensive tempo +
    intel_suite (Stage-4 guards make these no-ops when persisted).
  - `_serve_ferrari_tier(sport, tier_name, tier_label_prefix, limit,
    sort)` — single canonical resolver that every tier endpoint
    delegates to.
- **All 3 Ferrari tier endpoint bodies** collapsed to a single
  one-liner that calls `_serve_ferrari_tier(...)`. Zero per-sport
  branching remains in the endpoint handlers.
- **NFL readiness**: adding a new sport is now a one-line
  `SPORT_TIER_HELPERS["nfl"] = SportTierHelpers(...)` registration
  plus (optionally) a sport-specific `_post_process_nfl_picks` helper.
  No route edits required.

### Response invariants preserved
- Same response shape: `{tier, tier_label, sport, picks, count, status,
  pipeline: {source, fully_validated, with_mlr, with_gemini}}`.
- Same default sort (adapter's `vision_score DESC`).
- Same `?sort=gap` behaviour (NBA + MLB — verified that gap changes
  top-pick rs2 for MLB from 1.058 → 2.046 as expected).
- Same JIT injury filter, `overlay_enrichment_cache`, sport-specific
  enrichers, `_generate_vision_fallback`, `_guard_board_picks`,
  `_dedupe_picks_by_player` — all called in identical order.
- Same reader path: `get_board(db, sport, tier, limit, sort_override)`.
- Same scoring-source semantics: NBA reads `final-nba-rt`, MLB reads
  `final-mlb`.

### Files updated
- `routes/ferrari_tiers.py` — dispatch infrastructure added (~140 lines
  near line 1583); 3 endpoint bodies collapsed. Net: **-80 lines**
  (242 removed, 162 added).

### Stage 6 acceptance verification
- All 6 endpoints return HTTP 200:
  - `nba safe-haven=0, front-lines=0, war-zone=0` (pre-existing NBA
    data state — real-time recompute hasn't run; not a regression).
  - `mlb safe-haven=8, front-lines=3, war-zone=4` — full payload with
    `p_true_method='model'`, `ranking_score_v2`, `intel_suite`.
- `?sort=gap` verified on MLB safe-haven (top rs2 jumps from 1.058 to
  2.046 on gap sort, proving the parameter still re-orders).
- `grep 'sport == "nba"|sport == "mlb"' within Ferrari endpoint bodies`:
  **0 matches** (vs ~15 before Stage 6).
- `SPORT_TIER_HELPERS.keys()` = `['nba', 'mlb']` — both sports wire up
  through the registry.

---


## Carbon-Copy Migration — Stage 5 Complete (2026-04-21)

### Route-time projection/probability enrichment removed (eliminates D5)
- **`enrich_mlb_prop_with_averages` deleted from the live MLB path.** It
  previously computed L5/L10/L20 rolling averages, hit rates (h5/h10/h20),
  VK projection via Lasso fallback, VK edge, VK probability, VK
  recommendation, and a vision-intel baseline at route time — all from
  scratch on every request.
- All 3 MLB Ferrari endpoints (`/v3/ferrari/{safe-haven|front-lines|war-zone}?sport=mlb`)
  and `MLBAdapter.enrich_intel` no longer call it.
- The function body was replaced with a stub that raises
  `RuntimeError("enrich_mlb_prop_with_averages was removed in Stage 5 of
  the MLB↔NBA carbon-copy migration...")` — a trip-wire that
  immediately surfaces any accidental re-introduction into the live
  path.

### Where the fields come from now
| Field formerly set by the enricher | New canonical source |
|---|---|
| `l5_avg`, `l10_avg`, `l20_avg`, `season_avg` | persisted in `mlb_prop_scores` by the canonical scoring pass when available; else absent (UI already tolerates this) |
| `h5_rate`, `h10_rate`, `h20_rate`, `hit_rate_l*` | persisted `hit_rate_over`/`hit_rate_under` on score doc; Stage-2 side-aware ladder |
| `vk_predicted`, `vk_source`, `vk_prob_over`, `vk_prob_under`, `vk_probability` | mirrored from `model_projection` + `p_true_model` in `_get_mlb_tier_picks_from_scores` (Stage 2) |
| `vk_edge`, `vk_recommendation` | derived client-side from `model_projection - line` and `p_true_method`; the primary UI signal is already `ranking_score_v2` |
| `lasso_confidence` | absent in live path (Lasso retained for research only) |
| `vision_intel` baseline | built by `_generate_vision_fallback()` + `enrich_mlb_intel_suite()` (Stage 4 persisted) |
| `tier`, `synced_at`, `opponent`, `game_time` | already on the score doc or raw prop |

### Files updated
- `routes/ferrari_tiers.py` — function body replaced with
  `RuntimeError` stub; 3 route call sites removed (safe-haven,
  front-lines, war-zone).
- `services/adapters/mlb_adapter.py::enrich_intel` — import + call
  removed.

### Stage 5 acceptance verification
- `/api/mlb/sync/master`: 115 s, `success=True`, `errors=[]`.
- `mlb_prop_scores(final-mlb)`: 2214 docs, 40 tiered picks.
- All 3 Ferrari MLB endpoints serve picks with `p_true_method='model'`,
  `model_projection`, `ranking_score_v2`, `vk_source='model'`,
  `vk_predicted`, `vk_prob_over`, full `intel_suite`.
- Grep for `enrich_mlb_prop_with_averages(` across `/app/backend/`
  returns **1 match** — the stub definition itself. Zero live callers.
- `grep RuntimeError.*Stage 5` in backend logs: zero hits → stub is
  never invoked in production.

---


## Carbon-Copy Migration — Stage 4 Complete (2026-04-21)

### Single source of truth + scoring-write enrichment (eliminates D2, D6, D11)
- **D2**: `MLBAdapter.load_board()` now reads from `mlb_live_props` (the
  canonical odds collection, same as `MLBScoringAdapter.load_live_props`)
  instead of `mlb_cached_board`. No other live-path reads of
  `mlb_cached_board` remain — it is now an internal pipeline intermediate
  only.
- **D6**: Legacy MLB tier writers (`mlb_safe_haven`, `mlb_front_lines`,
  `mlb_war_zone`) are gated behind `MLB_WRITE_LEGACY_TIERS` (default
  `false`). `MLBMasterSync._run_tier_rebuilds` now SKIPS both the tier
  upserts and the lineup-ripple updates against them unless the flag is
  set. No live UI endpoint depends on these collections — the canonical
  source of truth is `mlb_prop_scores`.
- **D11**: MLB-specific enrichers (`enrich_mlb_prop_with_tempo`,
  `enrich_mlb_intel_suite`) now run once at scoring-write time via a new
  adapter hook `ScoringAdapter.enrich_score_doc()`. Both `tempo_modifier`
  and `intel_suite` are persisted in `mlb_prop_scores` (added to
  `_SCORE_OUTPUT_FIELDS`). The original route-time enrichers got
  idempotent early-return guards — when the persisted fields already
  exist on the pick, the route-time pass is a NO-OP. NBA's adapter base
  keeps a default no-op `enrich_score_doc()`, so NBA is unaffected.

### Files updated
- `services/adapters/mlb_adapter.py` — `load_board()` rewired.
- `services/mlb_master_sync.py` — `_WRITE_LEGACY_TIERS` flag + gated
  tier writes + gated ripple updates.
- `services/scoring/adapters/base.py` — added `enrich_score_doc()`
  default hook (no-op).
- `services/scoring/adapters/mlb_scoring.py` — implemented
  `enrich_score_doc()` that invokes the tempo + intel_suite enrichers
  and folds the result into the score doc.
- `services/scoring/prop_scores_store.py` — extended
  `_SCORE_OUTPUT_FIELDS` with `tempo_modifier` + `intel_suite`.
- `services/scoring/recompute.py` — calls the adapter hook after the
  canonical doc is built, merges returned fields via the allow-list.
- `routes/ferrari_tiers.py` — idempotent guards on
  `enrich_mlb_prop_with_tempo` and `enrich_mlb_intel_suite`.

### Stage 4 acceptance verification
- `/api/mlb/sync/master`: 183 s total, zero errors.
- `mlb_prop_scores` (tag=`final-mlb`): 2364 docs, **100.00%** of tiered
  rows (51/51) now carry persisted `intel_suite`. `tempo_modifier`
  populated where upstream data allows (raw `mlb_live_props` from the
  odds sync lacks `team`/`batting_order` for most props — same data
  limitation existed at route-time pre-Stage 4).
- Legacy tier collections were NOT touched by this sync
  (counts unchanged from pre-sync baseline); log line confirms:
  `[MLB_MASTER] Legacy tier writes SKIPPED (canonical source =
  mlb_prop_scores). Set MLB_WRITE_LEGACY_TIERS=1 to re-enable for debug`.
- Ferrari MLB endpoints all serve picks with full `intel_suite`
  (`sport`, `context_badges`, `vision_insight`, `stability_index`,
  `matchup_dvp`, `tempo`, `pace_delta`) arriving from the persisted
  score doc — route-time enrichers returned early as expected.
- Structural parity: `mlb_live_props` (2364) == `mlb_prop_scores@final-mlb`
  (2364) — one canonical live source, one scored store, one reader.

### Collection status after Stage 4

| Collection | Written? | Read by live UI? |
|---|---|---|
| `mlb_live_props` | ✓ by odds sync | — internal input only |
| `mlb_cached_board` | ✓ by Step 2 (internal pipeline) | ✗ NOT read by live UI |
| `mlb_prop_scores` | ✓ by Step 6 canonical scoring | ✓ CANONICAL SOURCE |
| `mlb_safe_haven` | ✗ (gated OFF; stale data remains) | ✗ NOT read by live UI |
| `mlb_front_lines` | ✗ (gated OFF; stale data remains) | ✗ NOT read by live UI |
| `mlb_war_zone` | ✗ (gated OFF; stale data remains) | ✗ NOT read by live UI |
| `mlb_master_hub` | ✓ | — internal input only |
| `mlb_oracle_apex_analyzed` | ✓ (debug) | ✗ NOT read by live UI |

---


## Carbon-Copy Migration — Stage 3 Complete (2026-04-21)

### Single live MLB model (eliminates D12)
- **Retired from live path:** `MLBPhysicalEngine`, `MLBVegasKillerModel`,
  and the Physical→VegasKiller fallback cascade.
- **Sole live MLB model:** `MLBHighFrictionModel`, invoked exclusively
  through a new canonical entry point
  `services.mlb_high_friction_model.predict_live(...)`.
- **Attribute-shape shim:** `_LiveMLBPrediction` wrapper exposes the
  same attributes (`is_valid`, `mlr_predicted`, `sigma_used`,
  `vk_prob_over`, `vk_prob_under`, `vk_edge`, `vk_verdict`, `z_score`,
  `mlr_matchup`, `error`) legacy callers used with `MLBPhysicalEngine`,
  keeping the diff minimal.
- **Files updated:**
  - `services/mlb_high_friction_model.py` — added `predict_live()` +
    `_LiveMLBPrediction` + `_build_mlr_matchup_from_friction()`.
  - `services/mlb_oracle_apex_service.py` — removed legacy model
    imports / construction / load calls; cascade in
    `build_elite_top_10_tiers` replaced with one `mlb_predict_live(...)`
    call. Startup log: `[MLB_ORACLE] Live model: MLBHighFrictionModel
    (sole primary)`. `set_vegas_killer_model` is now a no-op.
  - `services/rolling_cache_manager.py::_get_mlb_engine` — JIT intel
    path now returns a `_HFLiveEngineShim` that delegates to
    `predict_live()`.
- **Legacy models on disk:** `mlb_physical_engine.py` and
  `mlb_vegas_killer_model.py` remain for research/backtests only; not
  imported by any live-path module.

### Stage 3 acceptance verification
- `/api/mlb/sync/master`: 148 s total (6 s odds / 1 s board / 69 s BDL
  / 57 s tiers / 0 s ripple / 16 s canonical scoring), zero errors.
- `mlb_prop_scores` (tag=`final-mlb`): 2426 docs, 39 tiered picks,
  **100.00% `p_true_method` coverage among qualified rows**.
- Method breakdown: model 2263 / hit_rate 106 / fair 41 / none 16
  (none rows all `tier=unqualified`).
- **Live-model audit:** backend logs show `[MLB_HF]` / `[MLB_HF_PRED]`
  only — zero `[MLB_APEX]` or `[MLB_VK_FALLBACK]` invocations.
- Ferrari MLB endpoints: safe_haven=10, front_lines=3, war_zone=5 —
  100% `p_true_method='model'` with `ranking_score_v2` populated.

---

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
- ~~D3: MLB writes `mlb_prop_scores` without model triplet in-pass~~ —
  resolved via Stage 2 (MLBAdapter routes through `recompute_sport`;
  MLB master sync Step 6 is now the canonical scoring pass, not a
  workaround).
- ~~D10: MLB scoring ladder truncated to model-only~~ — resolved via
  Stage 2 shared `resolve_p_true_ladder()` helper; NBA + MLB both
  delegate to the canonical `model → hit_rate → vk2 → fair` ladder.
- ~~D12: MLB has 3 live model classes stitched together~~ — resolved
  via Stage 3. `MLBHighFrictionModel` is the sole live MLB model via
  `predict_live()`; `MLBPhysicalEngine` and `MLBVegasKillerModel`
  retired from the live path (on-disk only for research/backtests).
- ~~D2: MLBAdapter reads `mlb_cached_board`~~ — resolved via Stage 4.
  `MLBAdapter.load_board()` now reads from the canonical `mlb_live_props`
  (same source as `MLBScoringAdapter`). No live UI path touches
  `mlb_cached_board`.
- ~~D6: legacy tier collections power the live board~~ — resolved via
  Stage 4. `mlb_safe_haven`/`mlb_front_lines`/`mlb_war_zone` writes
  gated behind `MLB_WRITE_LEGACY_TIERS` (default OFF). Zero live UI
  endpoints read from these collections. Canonical source is
  `mlb_prop_scores`.
- ~~D11: route-time MLB enrichers (tempo, intel_suite) run on every
  request~~ — resolved via Stage 4. Both moved into the scoring-write
  path via the new `ScoringAdapter.enrich_score_doc()` hook; persisted
  in `mlb_prop_scores`. Route-time enrichers now have idempotent
  early-return guards → no-op when persisted fields exist.
- ~~D5: `enrich_mlb_prop_with_averages` still in live route path~~ —
  resolved via Stage 5. Function body replaced with `RuntimeError`
  stub. All 3 Ferrari MLB endpoints + `MLBAdapter.enrich_intel` no
  longer call it. All fields it previously set either come from
  persisted `mlb_prop_scores` or from already-integrated Stage-4
  scoring-write enrichers (`intel_suite`, `tempo_modifier`,
  `vision_fallback`).
- ~~D4: Ferrari endpoint IF-chain by sport~~ — resolved via Stage 6.
  Introduced `SPORT_TIER_HELPERS` dispatch registry +
  `_serve_ferrari_tier()` canonical resolver. All 3 Ferrari tier
  endpoints (safe-haven, front-lines, war-zone) collapsed to a
  one-line delegate call. Adding NFL is a single-line registry
  entry — no route edits needed.
- ~~D9: MLB has no `final-mlb-rt` real-time shadow tag~~ — resolved
  via Stage 7. MLB master sync Step 6-RT writes `final-mlb-rt` every
  run; `MLBBoardAdapter.version_tag` pinned to the RT tag; Stage-6
  dispatch template updated. Both sports now serve from their `-rt`
  tag.
- ~~D7: No unified scheduler (`SCHEDULED_SPORTS`) config~~ — resolved
  via Stage 8. New `services/scheduled_sports.py` introduces a
  `SCHEDULED_SPORTS` registry and canonical
  `run_scheduled_master_sync(sport)` entry point. `server.py` now
  registers one unified `hourly_{sport}_master_sync` APScheduler job
  per sport via a loop over the registry. Adding NFL is three lines
  (registry entry + module-level callable + `SPORT_INTERVAL_CALLABLES`
  entry) — zero `server.py` edits.
- ~~D1: `MLBMasterSync` exists as a separate orchestrator class~~ —
  resolved via D1 residual cleanup. `services/mlb_master_sync.py`
  deleted entirely; replaced by sport-agnostic `PipelineStep` chain
  (`services/pipeline/master_steps.py`) + `UnifiedPipeline
  .run_master_sync()` + step registration hooks on `SportAdapter`.
  `dispatch_master_sync("mlb")` now resolves to
  `UnifiedPipeline(MLBAdapter()).run_master_sync()`.

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

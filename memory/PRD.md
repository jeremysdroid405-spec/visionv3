# PRD — NBA/MLB Ferrari / PropVision AI

## Product Goal
Restructure React/FastAPI betting app into a 100% local-first MongoDB
architecture with multi-sport support, automated feature engineering, and
a unified pipeline anchored on canonical odds data. Surface pricing
anomalies through market-consensus probabilities.

## Global Identity Rule (2026-04-23)
Every prop MUST carry `bdl_player_id` (identity) + `player_name` (display).
All scoring joins use `bdl_player_id` exclusively. Name-based matching is
FORBIDDEN in the scoring pipeline. Identity resolution happens ONCE at
ingest. Props without a resolvable ID are flagged
`identity_status="missing_bdl_id"` and skip HR / CV / projection computation.

- **Ingest** (`services/universal_odds_sync.py`): `_stamp_identity_on_props`
  runs after flatten, stamps every prop (NBA + MLB) with `bdl_player_id`
  + `identity_status`. Hub aliases are built once per sync from
  `{sport}_master_hub_2026.bdl_id`.
- **NBA scoring** (`services/scoring/adapters/nba_scoring.py`):
  `_logs_by_id` is the SOLE game-log cache (keyed on `bdl_player_id`).
  `_get_logs_by_id` is the sole lookup. `_compute_cv_and_hit_rate`,
  `_get_vk2_history_logs`, `_empirical_covariance`,
  `_predict_combo_projection`, `_predict_model_prob_over`, and
  `_predict_vk2_prob_over` ALL take `bdl_player_id` as the identity input.
- **MLB scoring** (`services/mlb_tier_sorter.py` +
  `services/scoring/adapters/mlb_scoring.py`): `_player_logs_cache` is
  `Dict[int, List[Dict]]` keyed on `bdl_player_id`. `_calculate_cv`,
  `_calculate_hit_rate`, `_calculate_ceiling_hit_rate`, and
  `_get_recent_game_logs` all take `bdl_player_id`. MLB adapter
  extracts and propagates the ID through every metric + model call
  and gates the HF predict on identity.
- **VK & HF models** (`services/vegas_killer_model.py::predict`,
  `services/mlb_high_friction_model.py::predict`) both accept an
  optional `bdl_player_id` and prefer ID-based hub lookup when
  supplied; name lookup remains only as legacy fallback.
- **Persistence** (`services/scoring/prop_scores_store.py`):
  `bdl_player_id` + `identity_status` added to `_SCORE_OUTPUT_FIELDS`.
- **Observability**: `GET /api/v3/admin/identity-status` returns per-sport
  live-props resolution %, scored-doc identity breakdown, HR/CV status
  counts, and top unresolved player names for triage.
- **Status values**: `computed` | `unavailable_stat_family` |
  `missing_source_distribution` | `missing_bdl_id`.

**Verification (2026-04-23, after MLB hub coverage improvement)**:
- NBA: 100.00% resolution — 5,960/5,960 live, 3,732/3,732 scored. 0 `missing_source_distribution`, 0 `missing_bdl_id`.
- MLB: 100.00% resolution — 3,606/3,606 live, 2,601/2,601 scored. Juan Soto, MJ Melendez, Royce Lewis etc. all resolved after `sync_players(mlb)` pulled BDL's `/players/active` roster and a one-shot backfill populated `bdl_id` on 5,802 hub stubs from `bdl_game_logs[0].player_id`.
- `master_sync.py` now runs Step 0 (`get_bdl_universal_service(db).sync_players(sport)`) before odds sync, keeping hub coverage fresh automatically.
- Frontend: `/admin/identity-status` page (read-only, token-gated) renders per-sport cards with traffic-light resolution badge (green ≥99%, yellow 95–99%, red <95%), scored-doc identity breakdown, HR/CV status counts, and top-20 unresolved player list.
- 42 identity + gate + coverage tests pass.

## Architecture
- **Frontend:** React + Shadcn UI — `/app/frontend/src/pages/Dashboard.jsx`,
  hooks in `/app/frontend/src/hooks/useLiveOdds.js`.
- **Backend:** FastAPI — `/app/backend/server.py`,
  `/app/backend/routes/ferrari_tiers.py`.
- **Scoring:** `services/scoring/recompute.py` (ranking),
  `services/scoring/scoring_stack.py` (tier gates),
  `services/scoring/adapters/{nba,mlb}_scoring.py`.
- **Master sync (universal):** `services/master_sync.py ::
  run_master_sync(sport)` — SOLE orchestration path for NBA + MLB
  (Hard Consolidation, 2026-04-22). Legacy paths (DemonGoblinEngine,
  NBAMasterSync, UnifiedPipeline, adapters/{nba,mlb}_adapter.py)
  are DELETED.
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


## NBA RT Shadow Seeding Follow-Up (2026-04-21)

### Symptom
Pre-Stage-7 behavior: NBA Ferrari endpoints returned 0 picks on
every tier. `final-nba-rt` aged out between injury-triggered
partial rescores; NBA's master sync never seeded it.

### Fix
One-block addition in `services/nba_master_sync.py` right after
Phase 7 Elite Overwrite completes — mirror of the MLB Stage 7
pattern:
```python
from services.scoring.recompute import recompute_sport
rt_result = await recompute_sport(
    db=self.db, sport="nba",
    version_tag="final-nba-rt", dry_run=False,
)
metrics["phases"]["rt_shadow_seed"] = {...}
```

### Verification
- NBA `/api/nba/sync/master`: 176 s, `success=True`, `errors=[]`.
- Logs: `[NBA_MASTER_V2] RT shadow complete: 2084 scored at
  final-nba-rt tiers={'unqualified': 1953, 'front_lines': 86,
  'safe_haven': 37, 'war_zone': 8}`.
- `nba_prop_scores` tags now include `final-nba-rt` with 2084
  docs (131 tiered). Previously 32 docs (0 active/tiered).
- All 3 NBA Ferrari endpoints now serve 10/10/5 picks with
  `pipeline.source = nba_prop_scores[tier=...,version=final-nba-rt]`
  and `p_true_method='model'`, `ranking_score_v2` populated.
- MLB regression-free (unchanged, Ferrari still serving from
  `final-mlb-rt`).

Net diff: **+37 LOC** in one file.

---


## Delta Engine — Phase D1 + D2 Complete (2026-04-21)

### Scope delivered
Near-real-time Delta Engine foundation: read-only detection layer +
backwards-compatible scoring filter. **No tier writes. No board
mutations. Delta path never hits upstream APIs.**

### New files
- `services/delta/__init__.py` — package root with upstream-isolation invariant.
- `services/delta/detector.py` — `detect_changed_props(db, sport)` returning
  `DeltaDetectionResult` (updated / new / retired / dirty key sets +
  samples). Sport-agnostic; keys resolved via `ScoringAdapter.canonical_key_from_raw`.
- `services/delta_watermarks.py` — per-sport Mongo-backed watermark store
  (`delta_watermarks` collection; 5s grace window; `get_watermark` /
  `advance_watermark` / `describe_watermarks`).
- `routes/delta_admin.py` — `GET /api/v3/admin/delta/inspect/{sport}`
  and `GET /api/v3/admin/delta/inspect` (read-only).
- `tests/test_delta_upstream_isolation.py` — CI-style grep guard;
  fails the instant any `services.delta.*` module imports an upstream
  fetcher (universal_odds_sync / bdl_* / nba_official_sync / etc).
- `tests/test_delta_only_canonical_keys.py` — regression suite for the
  D2 scoring filter.

### Edited files
- `services/universal_odds_sync.py` — stamp `updated_at: datetime` on
  every MLB prop at flatten-time (one-line addition).
- `services/scoring/adapters/base.py` — added
  `canonical_key_from_raw(raw_prop)` method (default: read persisted
  `canonical_key`; sport adapters may override).
- `services/scoring/adapters/nba_scoring.py` — NBA-specific override
  derives the same key shape `build_context` uses (since NBA ingest
  doesn't persist `canonical_key`). Zero ingest changes needed.
- `services/scoring/recompute.py` — added
  `only_canonical_keys: Optional[Set[str]] = None` parameter. Defaults
  preserve full pre-D2 behaviour. When supplied, forces `write_mode="upsert"`
  (with warning log) so the RT tag remains additive.
- `routes/__init__.py` — wired new delta admin router.

### Inspect endpoint response shape
`GET /api/v3/admin/delta/inspect/{sport}` →
```
{
  sport, watermark_utc, dirty_count, updated_count, new_count,
  retired_count, live_props_count, active_live_props_count,
  scored_rt_count, missing_updated_at,
  sample_updated_keys[10], sample_new_keys[10], sample_retired_keys[10],
  upstream_lock_held, upstream_lock_detail{...}, duration_ms
}
```
(D1 `upstream_lock_held` is proxied from
`RebuildCoordinator._master_sync_state[sport].in_progress`. D4 will
replace this with the real `UpstreamSyncLock`.)

### Acceptance verification
- NBA baseline: 2084 live / 2084 scored RT / 0 dirty (22 ms).
- MLB baseline: 1726 live / 1726 scored RT / 0 dirty (20 ms).
- Seeded 3 NBA updated props + 1 retired + 1 fake new → inspect
  correctly returned `updated_count=5, new_count=2, retired_count=1,
  dirty_count=5`, with deterministic sample keys for each bucket.
- All 6 Ferrari endpoints HTTP 200 with unchanged pick counts
  (nba: 10/10/5, mlb: 1/5/6).
- 6/6 unit tests pass (`test_delta_upstream_isolation.py` +
  `test_delta_only_canonical_keys.py`).
- Grep proof: `services/delta/` + `services/delta_watermarks.py` +
  `routes/delta_admin.py` contain **zero** imports from upstream-fetch
  modules. Invariant enforced by CI test.

### NOT YET IMPLEMENTED (D3+ gated on approval)
- `RescoreDirtyPropsStep` + `RebalanceTiersStep` (D3).
- `UpstreamSyncLock` singleton + `DeltaEngine.run_forever` driver (D4).
- `SCHEDULED_SPORTS` extension (`delta_interval_seconds`) (D5).
- Observability metrics + status endpoint (D6).
- Retirement of `rolling_cache_manager.DeltaManager` (D7).
- Change-streams upgrade (D8).


## Delta Engine — Phase D3 + D4 Complete (2026-04-21)

### Scope delivered
Wrote the tick orchestrator + lock coordinator on top of D1/D2 detection.
**Delta engine now writes** to `{sport}_prop_scores@final-{sport}-rt`
but still never calls upstream APIs and still respects the board
reader contract.

### New files (LOC)
- `services/scoring/tiering.py` (110) — shared retire-path helpers
  (`mark_retired_inactive`, `get_tier_distribution`). No per-sport branches.
- `services/upstream_sync_lock.py` (124) — per-sport asyncio.Lock
  coordinator with `exclusive(sport)` / `try_acquire_tick(sport)` /
  `describe()` / `is_held()`. Singleton via `get_upstream_sync_lock()`.
- `services/pipeline/delta_steps.py` (264) — `DeltaStep` ABC + six
  concrete steps: `DetectChangedPropsStep`, `UpstreamLockGateStep`,
  `RescoreDirtyPropsStep`, `RebalanceTiersStep`, `AdvanceWatermarkStep`,
  `EmitDeltaTickStep`. Ordered `DEFAULT_DELTA_STEPS` tuple.
- `services/delta_engine.py` (210) — `DeltaEngine.tick(sport)` +
  `DeltaEngine.run_forever(sport, interval_seconds)` + `describe()`.
  Per-sport tick lock prevents overlapping ticks. Singleton via
  `get_delta_engine(db)`. **Not auto-started — that's D5.**
- `tests/test_upstream_sync_lock.py` (88) — 5 lock-behaviour tests.
- `tests/test_delta_engine_tick.py` (323) — 4 tick-orchestration tests
  with a minimal fake Mongo.

### Edited files
- `services/rebuild_coordinator.py` — `dispatch_master_sync(sport)`
  now wraps the full-sync run with `async with lock.exclusive(sport,
  holder=f"master_sync:{run_id}")`. NBA AND MLB go through this single
  lock acquisition path — same framework.
- `services/scoring/recompute.py` — D2 filter now uses
  `adapter.canonical_key_from_raw(p)` (sport-agnostic) instead of
  `p.get("canonical_key")` (which silently missed NBA rows that don't
  persist the key on the raw prop).
- `routes/delta_admin.py` — added `POST /api/v3/admin/delta/run-once/{sport}`
  (manual trigger) and `GET /api/v3/admin/delta/engine-status`. Rewired
  `_check_upstream_lock` to read the real `UpstreamSyncLock` singleton.

### Before/after delta execution semantics
| Scenario | Before (D1+D2) | After (D3+D4) |
|---|---|---|
| New prop detected | Visible in inspect endpoint only | Rescored + inserted into RT tag within one tick |
| Prop retired | Visible in inspect endpoint only | Scored doc flipped `active=False` within one tick |
| Line-move detected | Visible in inspect endpoint only | Rescored (tier + rs2 updated) within one tick |
| Full sync in flight | No coordination | Delta ticks clean-skip with `reason=upstream_lock_held` |
| Cross-sport isolation | N/A | NBA full sync does NOT block MLB delta (verified) |

### Live acceptance run (2026-04-21 02:28 UTC)
```
NBA delta tick (203 new props, real live data):
  1_detect         → 26ms,  dirty=203 new=203
  2_lock_gate      → lock=free, proceed
  3_rescore_dirty  → 1630ms, keys_requested=203 matched=203 written=203
  4_rebalance_tiers→ 5ms,   retired=0, tier_dist={front_lines:97, safe_haven:41, war_zone:7, unqualified:2064}
  5_advance_wm     → 1ms,   advanced_to=2026-04-21T02:28:34+00:00
  6_emit           → 0ms,   published=true
  TOTAL: 1663ms → scored_rt: 2084 → 2287 ✓

MLB delta tick during active MLB master sync:
  2_lock_gate      → lock=HELD by master_sync:9d278c98, ABORT
  3_rescore_dirty  → SKIPPED reason=upstream_lock_held
  4_rebalance_tiers→ SKIPPED reason=upstream_lock_held
  5_advance_wm     → SKIPPED reason=upstream_lock_held
  RESULT: skipped=true, no writes, no collision ✓

Live retire simulation on NBA (Shaedon Sharpe):
  Seeded active=False on live prop + watermark → tick ran:
  retired_keys_processed=1 retired_docs_modified=1
  Scored doc: active=False, inactive_reason="retired_by_delta_engine"
  Ferrari safe-haven: target evicted from board ✓
```

### Guardrail proofs
- Upstream-isolation test (`test_delta_upstream_isolation.py`) now
  covers all 6 delta-path modules (`delta_engine.py`, `upstream_sync_lock.py`,
  `pipeline/delta_steps.py`, `delta/`, `delta_watermarks.py`,
  `delta_admin.py`). Zero forbidden imports. CI-gated.
- 15/15 unit tests pass across the 4 delta-path test files.
- `UpstreamSyncLock` verified on live infrastructure: exclusive hold
  during full sync → delta skip; cross-sport runs unblocked.
- Board reader / Ferrari endpoints unchanged: all 6 endpoints HTTP
  200 with correct pick counts (nba 10/10/5, mlb 1/5/6) both before
  and after delta ticks.

### Caveats before D5

## Delta Engine — Phase D5 Complete (2026-04-21)

### Scope delivered
Delta engine is now **auto-running continuously** for every sport in
`SCHEDULED_SPORTS` with `delta_enabled=True`. Manual run-once + engine-
status endpoints remain available. Startup adds two background tasks
(one per sport); shutdown cancels them cleanly.

### Files changed (4 files, +133 LOC)
- `services/scheduled_sports.py` (+100 LOC):
  - Added `delta_enabled: bool = True` and `delta_interval_seconds: int = 20`
    to `ScheduledSportConfig`. NBA registered at 20s, MLB at 30s.
  - New `start_delta_engine_loops(db)` — idempotent. Spawns one
    `asyncio.create_task(DeltaEngine.run_forever(sport, interval_seconds))`
    per delta-enabled sport, tracked in module-level `_DELTA_TASKS` dict.
  - New `stop_delta_engine_loops()` — awaits cancellation of all tracked
    tasks at shutdown.
  - New `describe_delta_engine_loops()` — per-sport `(delta_enabled,
    interval_s, running, task_name)` for the engine-status endpoint.
- `server.py` (+25 LOC):
  - `@app.on_event("startup")` calls `start_delta_engine_loops(db)`.
  - `@app.on_event("shutdown")` awaits `stop_delta_engine_loops()`.
- `services/odds_sync_service.py` (+6 LOC):
  - Stamp `updated_at = datetime.now(timezone.utc)` on every NBA prop
    at the insert-time (immediately after `deduplicated` list build).
    Parity with `universal_odds_sync` which already did this for MLB.
- `routes/delta_admin.py` (+2 LOC):
  - `/api/v3/admin/delta/engine-status` now also returns
    `startup_loops` map (from `describe_delta_engine_loops()`).

### Acceptance verification (live, 2026-04-21 02:35–02:39 UTC)
```
Startup log:
  [DELTA_ENGINE] startup: {'nba': {'started': True, 'interval_s': 20},
                           'mlb': {'started': True, 'interval_s': 30}}
  [DELTA:nba] run_forever START interval=20s
  [DELTA:mlb] run_forever START interval=30s

Continuous ticks (sampled):
  02:35:30 [DELTA:nba] detect: updated=0 new=2058 retired=0 dirty=2058
  02:35:30 [DELTA:mlb] detect: updated=0 new=0    retired=0 dirty=0
  02:36:00 [DELTA:mlb] detect: updated=0 new=0    retired=0 dirty=0
  02:36:06 [DELTA:nba] detect: updated=0 new=0    retired=0 dirty=0
  02:36:26 [DELTA:nba] (lock held by master_sync:69d77949 → SKIPPED)
  02:36:46 [DELTA:nba] (lock held → SKIPPED)
  ... 7 consecutive NBA delta ticks SKIPPED while master sync ran ...
  02:39:xx [DELTA:nba] lock released → 17.4s rescore of freshly-updated props
  02:39:xx engine-status: nba total_ticks=12, mlb total_ticks=9

NBA updated_at stamping:
  Before: 2199 live_props, 0 stamped (pre-D5 baseline).
  After:  2229 live_props, 2229 stamped (100% coverage after one master sync).

Ferrari endpoints: all 6 HTTP 200 (nba 10/10/6, mlb 2/6/8).
```

### Caveats before D6/D7
1. **Heavy delta tick post-full-sync.** When a full sync completes,
   the next delta tick may rescore every new prop in one pass (NBA
   saw a 17.4s tick with 2058 new keys). That's within the `interval_s`
   budget on MLB (30s) but tight on NBA (20s). Mitigation candidate for
   D6: cap `rescore_keys` per tick to N (e.g. 500), spreading the
   rescore across 2-3 ticks. Low priority — this only happens once
   per hour.
2. **Ticks during game-tip storms** (multiple games tipping off in
   the same 20s window) aren't bounded. No issue observed yet; worth
   watching once we're live during a full slate.
3. **No metrics surface yet.** Tick counts and durations are in-memory
   only (`engine.describe()`). D6 will add Prometheus-style metrics

## Delta Engine — Phase D6 Complete (2026-04-21)

### Scope delivered
Observability + safety brake. No D5 behavior changes.

### Files changed
- NEW: `services/delta_metrics.py` (310 LOC) — dependency-free metrics
  sink. Per-sport counters, duration histogram (11 buckets: 0.05s → 60s +Inf),
  200-entry rolling ring buffer. Prometheus 0.0.4 text exposition
  generated in-process.
- NEW: `tests/test_delta_metrics.py` (220 LOC) — 7 regression tests
  covering counter increments, skipped-reason buckets, history ordering,
  batch-cap field capture, Prometheus label shape, and the cap-priority
  logic in `RescoreDirtyPropsStep`.
- EDITED: `services/scheduled_sports.py` (+8 LOC) — added
  `delta_rescore_batch_cap: int = 500` field to `ScheduledSportConfig`.
- EDITED: `services/pipeline/delta_steps.py` (+35 LOC) — `RescoreDirtyPropsStep`
  now reads `context["rescore_batch_cap"]`, prioritises `updated → new`
  deterministically (sorted), truncates to cap, and returns
  `{batch_cap, batch_capped, keys_skipped_due_to_cap, total_dirty_requested}`.
- EDITED: `services/delta_engine.py` (+32 LOC) — `tick()` resolves the
  per-sport cap from `SCHEDULED_SPORTS`, injects into context, and calls
  `delta_metrics.record_tick(result)` after every tick including the
  prior-tick-still-running skip path.
- EDITED: `routes/delta_admin.py` (+63 LOC) — three new endpoints:
    - `GET /api/v3/admin/delta/tick-history/{sport}?n=N` (1 ≤ N ≤ 200)
    - `GET /api/v3/admin/delta/tick-history?n=N` (all sports compact)
    - `GET /api/v3/admin/delta/metrics` (Prometheus text, Content-Type
      `text/plain; version=0.0.4; charset=utf-8`)
  `engine-status` now includes a `metrics` counters snapshot.

### Metrics added (Prometheus names)
```
propvision_delta_ticks_total{sport=}                     counter
propvision_delta_ticks_success_total{sport=}             counter
propvision_delta_ticks_skipped_total{sport=,reason=}     counter
propvision_delta_dirty_props_total{sport=}               counter
propvision_delta_updated_props_total{sport=}             counter
propvision_delta_new_props_total{sport=}                 counter
propvision_delta_retired_props_total{sport=}             counter
propvision_delta_rescored_props_total{sport=}            counter
propvision_delta_batch_cap_truncations_total{sport=}     counter
propvision_delta_batch_cap_keys_skipped_total{sport=}    counter
propvision_delta_tick_duration_seconds_{bucket,sum,count}{sport=} histogram
propvision_delta_last_tick_duration_seconds{sport=}      gauge
```

### Batch cap behaviour (live proof)
```
MLB full-sync→first-delta-tick after lock release:
  Tick 0776342b: duration=6.61s, dirty=2668 (all updated),
                 rescored=500, capped=True, skip_cap=2168
  Cap protected the 30s interval budget — tick finished with ~23s slack.

Config: ScheduledSportConfig(..., delta_rescore_batch_cap=500)  # default
Visibility: batch_cap, batch_capped, keys_skipped_due_to_cap are in
            every tick-history entry AND surface as Prometheus counters.

Priority order inside the cap: sorted(updated_keys) first, then sorted
new_keys (deterministic, idempotent). Overflow NEW keys re-surface next
tick via set-diff; overflow UPDATED keys are deferred to the next
`updated_at > watermark` bump.
```

### Sample tick-history payload
```json
{"tick_id":"0776342b","timestamp":"2026-04-21T02:53:…","duration_seconds":6.614,
 "success":true,"skipped":false,"skipped_reason":null,"upstream_lock_held":false,
 "dirty_count":2668,"updated_count":2668,"new_count":0,"retired_count":0,
 "rescored_count":500,"retired_docs_modified":0,
 "batch_capped":true,"keys_skipped_due_to_cap":2168,"batch_cap":500,"errors":[]}
```

### Guardrail proofs
- 22/22 unit tests pass (pre-existing 15 + D6's 7).
- Upstream-isolation test auto-scans `services/delta_metrics.py` and
  `services/pipeline/delta_steps.py` — zero forbidden imports.
- Ferrari endpoints remain HTTP 200 with unchanged pick counts across
  the D6 rollout (NBA 10/10/6, MLB 2/6/8).
- Lock-held skips now visible in both Prometheus
  (`propvision_delta_ticks_skipped_total{reason="upstream_lock_held",sport="mlb"} 6`)

## Delta Engine — Phase D7 Complete (2026-04-21)

### Scope delivered
Retired the legacy `rolling_cache_manager` "rolling cache" system
(90s overlay loop, `RollingCacheManager` class, `DeltaManager` class,
manual-refresh endpoint). The continuous D5 `DeltaEngine.run_forever`
is now the ONLY background loop maintaining prop freshness.

### Files changed (3 files, net −848 LOC)
- EDITED: `services/rolling_cache_manager.py` — **862 LOC → 75 LOC**.
  Rewrote as a narrow read-only file-cache surface. Kept ONLY
  `get_cached_props()` and `get_cached_prop_by_id()` (both have live
  callers in `routes/ferrari_tiers.py:2683` and `routes/intel_cache.py`).
  Deleted: `RollingCacheManager` class (zero callers), `DeltaManager`
  class (replaced by `DeltaEngine`), `run_cache_refresh_loop`
  coroutine (superseded by D5), all enrichment/refresh plumbing.
- EDITED: `server.py` (−19 LOC) — removed the two
  `asyncio.create_task(run_cache_refresh_loop(...))` startup lines
  (NBA + MLB 90s loops) and their surrounding logging scaffold.
  Shutdown remains clean (no task was registered to cancel).
- EDITED: `routes/intel_cache.py` (−45 LOC effective) — removed
  `POST /api/v3/intel-cache/refresh/{sport}` endpoint and its
  `DeltaManager` import. GET endpoints (`/nba`, `/mlb`, `/prop/{id}`,
  `/status`) unchanged — they still read the on-disk cache files.

### Before/after runtime behaviour
| Loop | Before D7 | After D7 |
|---|---|---|
| `DeltaEngine.run_forever(nba)` | 20s cadence | 20s cadence ✓ unchanged |
| `DeltaEngine.run_forever(mlb)` | 30s cadence | 30s cadence ✓ unchanged |
| `run_cache_refresh_loop(NBA)` | 90s cadence | **REMOVED** |
| `run_cache_refresh_loop(MLB)` | 90s cadence | **REMOVED** |
| Manual refresh endpoint       | `POST /intel-cache/refresh/{sport}` | **GONE (404)** |
| File cache readers            | `/intel-cache/{sport}` (GET) | Still work ✓ |

### Verification
- **Grep proof**: zero code references to `RollingCacheManager`,
  `DeltaManager`, `run_cache_refresh_loop` outside module docstrings
  that document the retirement.
- **Startup log proof**: latest restart (03:03:36 UTC) emits ONLY
  `DELTA_STARTUP` and `DELTA_ENGINE` messages — zero `ROLLING_CACHE`
  entries.
- **Live tick counters**: post-restart, both sports ticking at
  configured intervals (NBA +4 ticks, MLB +3 ticks over 40s).
- **Ferrari endpoints**: all 6 HTTP 200 with unchanged pick counts
  (NBA 10/10/5, MLB 2/6/8).
- **intel-cache GET endpoints**: HTTP 200 (readers kept).
- **intel-cache refresh endpoint**: HTTP 404 (correctly gone).
- **All 22 unit tests pass** including the upstream-isolation guard.

### Edge cases discovered
1. **`get_cached_props` / `get_cached_prop_by_id` are now read-only
   against stale files.** The underlying `{sport}_master_active_cache.json`
   payloads are no longer refreshed by a background loop. They contain
   whatever the last full master sync wrote. This is intentional:
   if the MLB player-detail vision-intel merge starts showing stale
   data, the correct fix is to either (a) have the full-sync pipeline
   write these files directly, or (b) delete the readers and
   re-architect the Vision Intel merge on top of a database query.
   Neither is required right now — both readers were already gated
   on "cache file exists" and fall back gracefully.
2. **Dead data files on disk.** `/app/backend/data/nba_master_active_cache.json`
   and `/app/backend/data/mlb_master_active_cache.json` remain on disk
   but will no longer auto-update. Not a bug, just a footnote. Can be
   removed by a future cleanup ticket along with the readers, or left
   for backwards compatibility.

### Remaining caveats before D8
- 🟢 No regressions. D7 is a pure retirement.
- 🟣 **D8** (optional): Mongo change-streams upgrade to replace the
  polling-based detection. Only meaningful on replica-set deployments;

## Gemini Cost Fixes — Priority 1 Complete (2026-04-21)

### Scope delivered
Three cost-cut patches from `/tmp/gemini_cost_audit_report.md` applied
and live-verified. **Betting engine unchanged** — fallback
`_generate_vision_fallback` still covers the `GOOGLE_API_KEY=""` case.

### Files changed
- EDITED: `routes/ferrari_tiers.py` (+130 LOC, -50 LOC net +80)
  - **P1.1**: New module-level helper `_vision_intel_content_hash(pick)`
    producing a sha1 over only *material* inputs: sport, canonical_key,
    line, direction, opponent, edge bucket (10%). `_is_cache_fresh` now
    a module-level function comparing stored `vision_intel_content_hash`
    against the computed hash. `computed_at` is ignored — D3 delta ticks
    no longer cache-bust.
  - Persist block in `_enrich_under_picks_with_gemini` now writes
    `vision_intel_content_hash` alongside the narrative.
  - **P1.3**: Removed `await _enrich_under_picks_with_gemini(...)` from
    `_post_process_nba_picks`. Zero request-time Gemini calls remain
    on the Ferrari hot path.
- EDITED: `services/unified_pipeline.py` (+167 LOC, -50 LOC net +117)
  - **P1.2**: `_run_gemini_enrichment` now computes per-payload
    `payload_hash` (sha1 over player/stat/line/tier/direction/edge_pct/
    h10_rate/h20_rate/matchup_opponent/dvp_rank/sport), loads the
    existing `{sport}_master_active_cache.json`, and skips payloads whose
    hash matches the cached `payload_hash`. Cache write-back persists
    the hash so the next rebuild short-circuits on unchanged props.
    Logs `payload-hash skip: X / Y props unchanged`.
  - **P1.3**: New `UnifiedPipeline._run_nba_under_enrichment(tiers)`
    method invoked from Phase 7 after `_run_gemini_enrichment` (NBA only,
    non-blocking). Lazy-imports the UNDER helper from
    `routes/ferrari_tiers` to avoid a routes↔services circular.
- NEW: `tests/test_gemini_cost_fixes.py` (153 LOC) — 12 regression
  tests covering content-hash stability, D3-tick cache-bust fix,
  invalidation triggers, and cache-freshness semantics. **All pass.**

### Before/after Gemini execution paths
| Trigger | Before | After |
|---|---|---|
| `GET /api/v3/ferrari/{tier}?sport=nba` | Calls Gemini for every stale UNDER pick in the tier | **Zero Gemini calls**. Reads cached `vision_intel`, falls back to `_generate_vision_fallback` for new/unenriched picks. |
| D3 delta tick (20s NBA / 30s MLB) | Bumped `computed_at` → invalidated cache → next page load triggered Gemini | **No cache invalidation**. Content-hash stays stable across rescores. |
| Hourly master sync (`_run_gemini_enrichment`) | Called Gemini for ALL tier picks every rebuild | Called only for picks whose `payload_hash` changed. Logs skip count. |
| Hourly master sync (NBA UNDER) | Relied on the next page load to enrich UNDERs via request path | Runs `_run_nba_under_enrichment` once at sync time; content-hash gated. |

### Sample hash behaviour (live)
```
pick = {sport: nba, canonical_key: X, line: 25.5, direction: OVER,
        opponent: BOS, true_edge: 0.12}
→ hash = ce3f…9b (40-char sha1)

After D3 tick bumps computed_at + tier_rank:
→ hash = ce3f…9b  (UNCHANGED — cache HIT)

After line moves 25.5 → 26.5:
→ hash = 7a1c…82  (CHANGED — cache MISS, Gemini called)

After edge jitters 0.12 → 0.17 (same bucket):
→ hash = ce3f…9b  (UNCHANGED — cache HIT)
```

### Live verification
- **34/34 unit tests pass** (22 existing + 12 new).
- Ferrari endpoints: **6/6 HTTP 200 in 140-390 ms** (zero LLM latency).
  NBA 10/10/5, MLB 2/6/8 — identical to pre-fix counts.
- Backend logs during request + delta tick: **zero Gemini-path activity**
  (only the engine init line at startup).
- Delta tick run: `rescored=0 reason=no_dirty_props_to_rescore
  retired_docs_modified=0` in 33ms. Engine unchanged.

### Expected Gemini spend reduction
| Path | Before | After | Saved |
|---|---|---|---|
| Request-time UNDER enrichment | ~$12/mo | **$0/mo** | $12 |
| Hourly batch (payload-hash) | ~$22/mo | **~$2/mo** | $20 |
| MLB sync summary | ~$5/mo | unchanged (P2 scope) | - |
| Misc | ~$7/mo | ~$3/mo (less dev churn) | ~$4 |
| **Total** | **~$46/mo** | **~$5/mo (≈89% reduction)** | **~$41/mo** |

### Remaining cost hotspots (Priority 2 territory)
- `VisionSummaryService` (`services/vision_summary_service.py`) —
  still fires per-pick inside MLB sync cache warming. ~$5/mo. **P2.1 +
  P2.2**: consolidate with `VisionIntelService` or gate on content hash.
- `AIContextEngine._call_gemini` (`services/engines/ai_context_engine.py`)
  — still has no cache. **P3.1**: LRU content-hash cache, ~30 min work.
- `calculate_intel_suite(use_llm=True)` footgun in background_worker —
  2-minute cadence × 30 picks = 720 LLM calls/hour if flag flipped.
  **P3.2**: rename parameter to gate future mis-use.

### Presentation-only invariant preserved
Betting engine paths (scoring stack, Ferrari tier selection, D3 delta
engine) still call zero Gemini anywhere. `GOOGLE_API_KEY=""` is a
no-op — the scoring ladder + tier assignments are identical, and
`_generate_vision_fallback` populates the narrative field cleanly.

## Gemini Cost Fixes — P2 + P3 Complete (2026-04-21)

### Scope delivered
All five approved tasks from the P2/P3 batch applied:
P2.1 consolidate, P2.3 skip unqualified, P3.1 LRU, P3.2 mode enum,
P3.3 admin endpoint. No request-path Gemini calls. Scoring/delta/
tiering unchanged.

### Files changed
| File | Δ LOC | Change |
|---|---|---|
| NEW `services/gemini_metrics.py` | +130 | Shared `record_gemini_call` + `GeminiLRUCache` + `cache_stats` |
| NEW `routes/gemini_admin.py` | +33 | `GET /api/v3/admin/gemini/cache-stats` |
| NEW `tests/test_gemini_p2_p3_fixes.py` | +203 | 11 regression tests |
| `services/vision_summary_service.py` | 462 → 190 (**-272**) | P2.1: rewritten as thin delegator to `VisionIntelService.analyze_prop_strict`. No duplicate prompt. |
| `services/intel_suite_calculator.py` | +30 / -5 | P3.2: `mode="deterministic"\|"gemini"` param; `use_llm` deprecated + ignored |
| `services/engines/ai_context_engine.py` | +40 / -8 | P3.1: LRU(500) keyed on sha1(prompt) + metrics |
| `services/vision_intel_service.py` | +14 | P3.3: metrics tags on batch + strict calls |
| `services/gemini_scout_engine.py` | +7 | P3.3: metrics tag on single-prop scout call |
| `services/unified_pipeline.py` | +12 | P2.3: `_RENDERABLE_TIERS` gate in both Gemini paths |
| `routes/__init__.py` | +4 | Wire gemini_admin router |
| **Net** | **+218 / -411** = **−193 LOC** | |

### P2.1 — Consolidation detail
`VisionSummaryService.generate_pick_summary` no longer owns its own
prompt, Gemini client, or retry loop. It now:
1. Checks the class-level `_summary_cache` (6-hour TTL, unchanged key).
2. On miss, builds a prop dict and delegates to
   `VisionIntelService.analyze_prop_strict(prop, tier_name="safe_haven")`.
3. Records every outcome through `record_gemini_call("vision_summary", ...)`.

Result: **one Gemini prompt definition in the codebase, not two**. All
existing call sites (`mlb_sync_engine.py`, `optimized_sync_engine.py`,
`routes/cached_data.py`) continue to work unchanged — both the public
API and the class-level caches are preserved.

### P2.3 — Skip unqualified
Two Gemini call sites in `unified_pipeline.py`:
  * `_run_gemini_enrichment` payload loop: only processes tiers in
    `_RENDERABLE_TIERS = {"safe_haven", "front_lines", "war_zone"}`.
  * `_run_nba_under_enrichment` loop: same filter.
`unqualified` tier picks no longer trigger Gemini anywhere.

### P3.1 — LRU cache
`AiContextEngine._call_gemini` wraps every call in a class-level
`GeminiLRUCache(max_size=500)`. Identical prompts (same sha1 hash)
within the pod lifetime return from memory. Cache hits are tagged
as `ai_context` hits in `gemini_metrics`.

### P3.2 — `use_llm` → `mode`
`IntelSuiteCalculator.calculate_intel_suite`:
  * New param `mode: str = "deterministic"` (DEFAULT).
  * Legacy `use_llm: Optional[bool] = None` retained for
    backwards compatibility — emits a deprecation warning and is
    silently ignored. Future `use_llm=True` accidents cost $0.
  * Threaded through `_generate_vision_insight(..., mode=mode)` which
    only calls Gemini when `mode == "gemini"`.

### P3.3 — Admin endpoint
`GET /api/v3/admin/gemini/cache-stats[?window_hours=N]` returns:
```json
{
  "hits": 0, "misses": 0, "total": 0, "hit_rate": 0.0,
  "calls_last_24h": 0, "real_api_calls_last_24h": 0,
  "calls_by_sport": {}, "calls_by_kind": {}, "window_calls_by_sport": {}
}
```
Every Gemini call site now tags its outcome, making this endpoint the
single operational view of LLM spend. Window-filtered counters enable
"calls in the last hour/day/week" queries without restarting the pod.

### Live verification
- **45/45 unit tests pass** (34 prior + 11 new).
- **Ferrari endpoints**: 6/6 HTTP 200 in 129-377ms. Unchanged pick
  counts (NBA 10/10/6, MLB 2/6/8).
- **Delta engine**: both sports ticking, no latency regression.
- **Admin endpoint live**: returning proper JSON shape with zero-baseline
  at post-restart.

### Estimated monthly spend after P2/P3
| Category | Post-P1 | Post-P2/P3 |
|---|---|---|
| Request-path Gemini | $0 | $0 |
| Hourly master sync (payload-hash gated, unqualified skipped) | ~$2 | **~$1.50** |
| VisionSummaryService (now consolidated + tagged) | ~$5 | **~$1.50** (same prompt as VisionIntelService; unified cache) |
| AIContextEngine | ~$1 | **~$0.20** (LRU dedupe) |
| IntelSuite use_llm footgun | latent risk ($15+/day) | **$0** (mode gate + deprecation) |
| Misc (scout single-prop, briefings) | ~$1 | **~$0.80** |
| **TOTAL** | **~$5/mo** | **~$4/mo** (steady-state, one internal user) |

Primary remaining spend driver: the **hourly NBA+MLB master-sync batch**
(~$1.50/mo), which only fires on actual content changes. This is the
deterministic floor at dev pace — further cuts require reducing the
master-sync cadence below 1/hour, which is out of scope.

### Remaining hotspots
- 🟢 Nothing urgent. The P1+P2+P3 stack has reduced dev spend from
  ~$46/mo to ~$4/mo (≈91% total reduction).
- 🟣 **P3.3 shared budget decorator** (per-day cap env var) would be the
  next defence-in-depth layer. ~2 hours of work, defers until we see
  real production traffic.


  single-pod preview environment doesn't benefit.

  and the history endpoint per-tick.

### Caveats before D7
1. **Overflow UPDATED-key deferral.** When dirty > cap and the overflow
   is made of updated keys (not new), those keys do NOT automatically
   re-surface next tick (watermark advanced past them). In practice,
   this only happens right after a full sync — and the full sync already
   scored them, so the deferred rescore is a no-op anyway. If line moves
   ever spike above 500/sport in a 20s window we'd want to re-evaluate,
   but that's orders of magnitude above current traffic.
2. **Metrics reset on pod restart.** No persistent store. Prometheus
   scrapes delta-absolute, so this is the correct behaviour — we just
   lose the pre-restart tick history (not counters, which are fine to
   be reset).

## 0-Book Exclusion Rule — Pricing Integrity Hard Gate (2026-04-22)

### Scope
Implements the user-requested strict pricing-integrity rule: any prop
without an exact-line anchor from DraftKings, FanDuel, BetMGM, or
BetOnline is classified `coverage_class="pp_only"` and **excluded from
all downstream processing**. No fuzzy matching, no nearest-line fallback,
no probability inference for unpriced props.

### Rule
```
book_count == 0  → pp_only    → EXCLUDED from scoring, ranking,
                                tiers, cached board, parlay builder
book_count == 1  → single_book → kept, scored normally
book_count >= 2  → multi_book  → kept, scored normally
```

### Files touched
- **NEW** `services/scoring/coverage_filter.py` — `classify_coverage()`,
  `filter_priceable()`. Recognises both naming conventions
  (`draftkings_price`/`dk_odds`, `fanduel_price`/`fd_odds`,
  `betonline_price`/`bol_odds`, `betmgm_price`/`mgm_odds`) plus nested
  `sharp_market` prices. Rejects 0-value odds. Logs one
  `[COVERAGE_FILTER]` line per run with
  `total/excluded/remaining/coverage_rate/multi_book/single_book`
  counters.
- **EDITED** `services/scoring/adapters/mlb_scoring.py::load_live_props`
  + `services/scoring/adapters/nba_scoring.py::load_live_props` —
  apply the filter at the single RT scoring chokepoint per sport.
- **EDITED** `services/adapters/nba_adapter.py::load_board` +
  `services/adapters/mlb_adapter.py::load_board` — apply filter on
  the master-sync path.
- **EDITED** `services/scoring/prop_scores_store.py` — added
  `book_count`/`coverage_class`/`books_anchored` to
  `_SCORE_OUTPUT_FIELDS` so classification persists.
- **EDITED** `services/scoring/recompute.py` — copies coverage fields
  from `ctx.raw_prop` onto the score doc.
- **EDITED** `routes/ferrari_tiers.py` — `_guard_pp_only_exclusion()`
  read-side safety net + surface coverage fields on every Ferrari pick
  via NBA and MLB merge blocks.
- **NEW** `tests/test_coverage_filter.py` (15 tests) +
  `tests/test_coverage_fields_persisted.py` (2 tests).

### Verification (live)
| Sport | Live props | pp_only dropped | Kept | Coverage rate |
|---|---:|---:|---:|---:|
| NBA RT scoring | 7,037 | 2,619 | 4,418 | **62.8%** |
| NBA master sync | 622 | 165 | 457 | **73.5%** |
| MLB RT scoring | 4,414 | 664 | 3,750 | **85.0%** |

Persistence check (fresh `final-{sport}-rt`):
- NBA: 4,418 docs / 4,418 with `coverage_class` / **0 pp_only persisted**.
- MLB: 3,736 docs / 3,736 with `coverage_class` / **0 pp_only persisted**.

API smoke (all 6 tier endpoints): **0 pp_only leaks**. Every pick
carries `book_count`, `coverage_class`, `books_anchored`.

### Test coverage
**136/136 pass** (15 new `test_coverage_filter.py` + 2 new
`test_coverage_fields_persisted.py` + all existing delta-isolation /
carbon-copy / scoring / market-catalog / BetMGM / odds-sync regressions).

### Invariants preserved
- Delta engine isolation unchanged (filter is pre-delta).
- Carbon-copy standard: same filter module + projection for NBA + MLB.
- No changes to ranking math, tier gates, multipliers, or EV logic.
  Pure filtering layer.

---


## Pull All Markets / All 3 Books — Dynamic Discovery (2026-04-22)

### Scope delivered
User request: **"pull all available props and markets for NBA and MLB
available on Odds API. All props, all markets, for all 3 books
(DraftKings, FanDuel, BetOnline)"**. Replaced every hardcoded market
whitelist (`PRIZEPICKS_STANDARD_MARKETS`, `PRIZEPICKS_ALTERNATE_MARKETS`,
`SHARP_MARKETS`, `SPORT_API_CONFIG[sport]['markets']`) with dynamic
per-event market discovery via Odds API's
`/v4/sports/{sport}/events/{id}/markets` endpoint.

### Files changed
- **NEW** `services/market_catalog.py` (+200 LOC) — `MarketCatalog`
  class exposing `discover_event_markets` (per-event) and
  `discover_union_across_events` (sport-wide union across sample
  events). Per-event cache so each market-discovery credit is paid
  only once per sync. Filters to `player_*` / `batter_*` / `pitcher_*`
  markets by default; `include_game_markets=True` extends to
  `h2h` / `spreads` / `totals` / etc.
- **EDITED** `services/odds_api_service.py`:
  - `PRIZEPICKS_STANDARD_MARKETS`/`..._ALTERNATE_MARKETS` retained as
    **fallback only** when the catalog returns empty.
  - New `NBA_SHARP_BOOKMAKERS = ["draftkings","fanduel","betonlineag"]`
    and `NBA_SHARP_REGIONS = "us,us2"` constants documenting the
    user's requested book trio.
  - `OddsApiService.__init__` owns a `MarketCatalog` and tracks
    `credits_used` (`market_discovery`, `sharp_book_odds`,
    `prizepicks_odds`).
  - `fetch_prizepicks_odds` now calls discovery before hitting `/odds`
    so every market PP currently exposes for the event is fetched.
  - `fetch_sharp_book_odds` rewritten: discovers all DK+FD+BOL markets
    per event, then issues a single `/odds` call with the complete
    market list. Credits per event = 1 discovery + 1 odds.
- **EDITED** `services/universal_odds_sync.py`:
  - Added `betonlineag` to `BOOKMAKER_CONFIG` (region=`us2`).
  - `DEFAULT_BOOKMAKERS` + `MLB_BOOKMAKERS` + `SPORT_API_CONFIG['nba'].bookmakers`
    + `SPORT_API_CONFIG['mlb'].bookmakers` all updated to the user-
    requested trio alongside the PrizePicks anchor
    (`prizepicks, draftkings, fanduel, betonlineag`). BetMGM/Pinnacle
    removed from the MLB primary list.
  - `UniversalOddsSyncService.__init__` owns a `MarketCatalog`,
    per-sport union memo, and `credits_used` counter.
  - New `_resolve_markets_for_sport` discovers the union of markets
    across the first 3 events for the sport **once per sync** and
    reuses it across every event (3 discovery credits per sport).
  - `fetch_event_odds` accepts `markets_override` so the sync-wide
    discovered list is injected. Hardcoded list retained purely as
    fallback if discovery fails.
  - `sync_sport_props` resets credit counters, invokes discovery,
    injects the discovered list, and surfaces `credits_used` +
    `markets_discovered` in the sync result.
  - `extract_props_from_odds` (Pass 2+3) now attaches independent
    **FanDuel and BetOnline layers** alongside the existing DK/MGM/
    Sharp layers. Flattened props now carry `fd_line`/`fd_odds`/
    `bol_line`/`bol_odds`/`fd_layer`/`bol_layer` in addition to the
    legacy DK/MGM fields. `all_odds` + `bookmakers_available`
    aggregates include every matched book.
- **EDITED** `services/utils_service.py` — `STAT_TYPE_MAP` expanded
  with new NBA markets (`player_double_double`, `player_triple_double`,
  `player_blocks_steals`, `player_first_basket`, etc.). Unknown
  markets now fall back to the uppercased base key (previously
  `""`) so composite-key uniqueness is preserved when new markets
  surface.
- **NEW** `tests/test_market_catalog.py` (+220 LOC) — 10 tests covering
  player/game-market classification, discovery happy path, book
  filtering, 404 fallback, no-api-key guard, per-event caching, and
  union-across-events aggregation with max-events cap.
- **NEW** `tests/test_pull_all_markets.py` (+120 LOC) — 11 tests
  locking the user-requested book trio (DK/FD/BOL) across both sports,
  the BetOnline region mapping, the `credits_used` observability
  contract, the new `extract_stat_type` fallback, and grep-style
  structural guards preventing regression back to hardcoded market
  lists.
- **EDITED** `tests/test_delta_upstream_isolation.py` — added
  `services.market_catalog` and `services.odds_api_service` to the
  forbidden-import list so the delta engine can never accidentally
  import the new upstream fetcher.

### Before / after coverage
| Path | Before | After |
|---|---|---|
| NBA PrizePicks markets | Hardcoded 8 markets (PTS/REB/AST/PRA × std+alt) | **All markets** PP currently offers for each event |
| NBA DK/FD/BOL markets  | Same hardcoded 8 markets | **All markets** the 3 books expose for each event |
| MLB bookmakers | `prizepicks, draftkings, betmgm, pinnacle` | `prizepicks, draftkings, fanduel, betonlineag` |
| MLB markets | Hardcoded ~30 batter/pitcher markets | **All markets** DK/FD/BOL expose per event |
| FanDuel/BetOnline prop layers | silently dropped (only DK+MGM layers in extract) | persisted as `fd_layer`/`bol_layer` with flat price fields |
| Unknown market → stat_type | `""` (composite-key collisions) | uppercased base key (unique per market) |
| API-credit usage | Untracked | Surfaced per sync in `results.credits_used` |

### Invariants preserved
- **Delta engine isolation**: new `market_catalog.py` is an upstream
  fetcher and is blocked from the delta path via the CI grep guard.
- **PrizePicks anchor**: PP still anchors `extract_props_from_odds`
  canonical keys for both sports — DK/FD/BOL/MGM/Sharp attach as
  independent layers on exact-line match (existing layered architecture
  unchanged).
- **Back-compat**: every `/odds` call's fallback-on-catalog-miss path
  still uses the legacy hardcoded market list, so the pipeline cannot
  serve zero props in the event of a catalog outage.

### Verification
- **172/172 relevant unit tests pass** (155 existing + 17 new).
  Includes all delta-isolation / carbon-copy / scoring / Gemini-cost
  tests plus the new market-catalog and pull-all-markets suites.
- Ruff/lint clean on all 4 edited modules + the 2 new test files.
- Backend supervisor healthy, zero import errors, Ferrari endpoints
  live (NBA safe-haven=10, MLB 0 due to no games running — unrelated).

### Cost note
Credits per full sync after this change:
- NBA: `1 events` + `3 discovery (union)` + `N × 2 (discover+odds per event)` via OddsApiService path. On a 10-game night ≈ 24 credits (vs. ~12 pre-change). Offset by catching every market rather than the 8-market whitelist → proportionally many more props priced.
- MLB: `1 events` + `3 discovery` + `N × 1 odds` via UniversalOddsSync path ≈ 18 credits on a 15-game night.
- `sync_sport_props` result now surfaces `credits_used` for operator
  monitoring so the budget curve is observable.

### Remaining caveats
- The NBA sync still goes through two parallel paths:
  `DemonGoblinEngine.sync_odds_to_mongo` (PP + sharp-books) is what
  the legacy hourly NBA sync uses; `UniversalOddsSyncService` is
  triggered by the manual route `/api/v3/universal-odds-sync`. Both
  now pull "all markets" for their respective books. No unification
  work done — flagged for a future cleanup pass.
- If a brand-new market surfaces (not in `STAT_TYPE_MAP`) the prop
  lands in `live_props` with `stat_type == BASE_MARKET_KEY_UPPER`. The
  scoring models (VK, HighFriction, etc.) won't know what to do with
  it and the prop will simply not be tier-assigned. Add new stat-type
  mappings + training data as desired.

---


## Gemini Batching Fix — Non-Batched Bulk Path Eliminated (2026-04-21)

### Scope delivered
Removed the single remaining per-prop Gemini fan-out identified in the
batching audit. The UNDER enricher now makes **ONE** Gemini API call
per tier (capped by the existing content-hash cache-miss filter)
instead of N gathered calls.

### Files changed (3 files, +48 / -11)
- `services/vision_intel_service.py` (+45 / -6):
  Added `strict: bool = False` kwarg to `analyze_tier_batch`. When
  `strict=True`, returns `List[Optional[Dict]]` where each slot is
  either the Gemini-authored intel dict (prop_id echoed back AND
  `vision_intel` non-empty) or `None` — preserves the "only cache
  Gemini-authored text" invariant. Legacy (default `strict=False`)
  behaviour is byte-identical; existing callers (MLB tier service,
  VisionSummaryService delegation) are untouched.
- `routes/ferrari_tiers.py` (+3 / -5):
  Replaced the `asyncio.gather(*[analyze_prop_strict(p, tier) for p in
  to_call])` fan-out with
  `await vis.analyze_tier_batch(to_call, tier_name, strict=True)`.
  Output shape preserved — the persist loop still sees a list of
  `Optional[intel_dict]` values in input order.
- NEW: `tests/test_gemini_batching_fix.py` (~160 LOC) — 6 regression
  tests. Structural (fan-out gone, strict kwarg exists), behavioural
  (strict=True returns correct None slots; disabled service path;
  legacy mode unchanged), and the critical call-count test
  (10-prop tier → exactly 1 Gemini API call).

### Before / after call semantics
| Tier size (cache-missed UNDER picks) | Before | After |
|---|---|---|
| 10 UNDER picks | 10 parallel `generate_content` calls (gather) | **1 `generate_content` call** |
| 5 UNDER picks | 5 parallel calls | 1 call |
| 0 UNDER picks | 0 calls (unchanged) | 0 calls (unchanged) |

### Cache invariants preserved
- Only picks with Gemini-authored `vision_intel` (non-empty + prop_id
  echoed) get `vision_intel_content_hash` persisted.
- Picks where Gemini failed to echo fall back to
  `_generate_vision_fallback` downstream WITHOUT corrupting the cache
  (strict=True returns None for those slots; the persist loop's
  `if not out: continue` skips them).

### Grep-verified proof
- `grep "analyze_prop_strict(p, tier_name) for p in"` → **zero matches**
  anywhere under `/app/backend/**` (fan-out eliminated).
- `grep "asyncio.gather.*analyze_prop_strict"` → **zero matches**.
- `routes/ferrari_tiers.py:1411` shows the new batched call.
- Remaining `analyze_prop_strict` usages are limited to:
  * `services/vision_summary_service.py:169` — single-prop cached
    delegation (P2.1 consolidation; intentional single-call).
  * Docstring references only.

### Live verification
- **51/51 unit tests pass** (45 prior + 6 new).
- Ferrari endpoints: 6/6 HTTP 200 in 119-372 ms, unchanged pick counts
  (NBA 10/10/6, MLB 2/6/8). Response shape unchanged.
- Delta engine: both sports running, no regression.

### Estimated additional savings
| Scenario | Pre-fix | Post-fix |
|---|---|---|
| Per NBA master sync, avg 6-10 UNDER cache misses | 6-10 billed Gemini calls | **1 billed call** |
| Over 24 NBA syncs/day × 0.5 dirty rate | ~100 API calls/day | **~15 API calls/day** |
| Monthly UNDER-enrichment spend | ~$0.40 | **~$0.06** |
| Prod at 1000 users (proportional) | ~$4.00/mo | **~$0.60/mo** |

Absolute dev savings are small (~$0.35/mo) but the factor-of-7 reduction
matters at production scale. The structural improvement also closes the
last "spot-light" risk where a developer adding a new UNDER tier could
accidentally 10x their API bill.

### Remaining non-batched bulk Gemini paths
🟡 **Only one remaining — operator-triggered, bounded exposure**:
`AiContextEngine.update_master_hub_with_context` at
`services/engines/ai_context_engine.py:279-314` still iterates
`evaluate_player_context` → `_call_gemini` per player in a sequential
loop with 100 ms sleep.
- **Trigger**: only the admin `POST /api/v3/ai-context/run` endpoint.
- **Not on any scheduled / request / delta path** → does not burn tokens
  in steady state.
- **P3.1 LRU dedupe still helps** when the same player has identical
  news across runs.
- Per the batch instructions, `AiContextEngine` was explicitly OUT OF
  SCOPE for this batch. Flagged for P2.2 or a future "operator bulk
  endpoints" cleanup.

3. **`rolling_cache_manager.DeltaManager` still alive.** Its 90s overlay
   loop is now fully redundant — D7 cleanup is the next ticket.

   and a rolling tick history.
4. **`rolling_cache_manager.DeltaManager` is still running.** Its 90s
   overlay loop is structurally redundant post-Stage-4/D3 but has not
   been removed. D7 is the cleanup ticket.

1. **Engine not auto-started.** `POST /api/v3/admin/delta/run-once/{sport}`
   is the only trigger for now. D5 will spawn `asyncio.create_task(
   engine.run_forever(sport))` at server startup, one per entry in
   `SCHEDULED_SPORTS`.
2. **`updated_at` only stamped by MLB's `universal_odds_sync`.** NBA's
   legacy per-book sync path doesn't stamp it yet — so NBA's "line
   move" signal currently relies entirely on the NEW signal (set-diff).
   D5 should extend the stamp to the NBA sync path OR accept this gap
   (new-key set-diff already catches line pulls that produce new
   alternate-market props in practice).
3. **Single-process lock.** If we ever scale to multiple backend
   replicas, swap `asyncio.Lock` for a Mongo changestream lease or
   Redis lock (D8-adjacent).
4. **Tier slot fill is latent**, not explicit — Ferrari's `limit=10`
   query naturally promotes the next qualified pick on the next read.
   Verified working via the Shaedon Sharpe retire simulation.



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

## Feature: Universal Hit-Rate Status Field — 2026-04-23
- **Spec:** Compute hit_rate for every prop using L20 game logs; no
  exceptions for alt-lines or combos; if insufficient data, mark
  `hit_rate_status = missing` (no silent fallback).
- **Investigation:** The existing
  `NBAScoringAdapter._compute_cv_and_hit_rate` already computed HR
  from L20 for every supported family (pts, reb, ast, pra, threes,
  stl, blk, pts_reb, pts_ast, reb_ast, turnovers). What was missing
  was an explicit `hit_rate_status` field to disambiguate a
  legitimate 0% HR from a null "no data" case.
- **Implementation:**
  - Extended `_compute_cv_and_hit_rate` to return a 7-tuple,
    adding `hit_rate_status` as a peer of `cv_status`. Same state
    machine (computed / unavailable_stat_family / missing_source_distribution).
  - Diverges from `cv_status` on exactly one edge: when the L20
    window has ≥5 observations but mean=0 (e.g. a specialist with
    zero L20 steals), `cv_status = missing_source_distribution`
    (degenerate) but `hit_rate_status = computed` with `hit_rate = 0`
    — because 0% HR is a real, actionable signal. HR never silently
    falls back to CV's failure mode.
  - Added `hit_rate_status` to `ScoringContext` (base.py),
    `_SCORE_OUTPUT_FIELDS` (prop_scores_store.py), and
    `recompute.py` doc emission.
  - MLB adapter updated to emit the same contract.
- **Post-recompute verification (NBA `final-nba-rt`, 4,091 props):**
  - `hit_rate_status=computed`: **3,826 (93.5%)**
  - `hit_rate_status=missing_source_distribution`: 265 (6.5%)
  - No `unavailable_stat_family` — every family is covered.
  - Coverage mirrors CV exactly, as expected (same L20 source).
- **War Zone drilldown (110 props):**
  - 42 computed HR, 68 missing.
  - Every missing-HR War Zone prop maps 1:1 to a player whose
    `cv_status` is also missing — every row is C.J. McCollum /
    R.J. Barrett / Jabari Smith Jr / Robert Williams / Kelly Oubre
    / etc. i.e. players whose L20 game logs aren't in
    `nba_master_hub_2026` yet. Honest data-quality reporting.
- **No silent fallback:** Consumers that previously inferred "HR
  null = low quality" can now distinguish "HR=0.0 computed" (real
  signal, player never hit this line) from "HR=None status=missing"
  (insufficient data, re-pull game logs to fix).
- **Files:** `adapters/base.py`, `adapters/nba_scoring.py`,
  `adapters/mlb_scoring.py`, `prop_scores_store.py`, `recompute.py`.

## Gate Config Change: War Zone — All Filters Removed — 2026-04-23
- **Change:** `_NBA_WAR_ZONE_BASE = {"__pass_all__": True}`. Every
  War Zone-eligible prop now passes the gate engine. No ceiling,
  edge, or coverage filter.
- **Engine support:** Added `__pass_all__` sentinel handling to
  `UniversalGateEngine.evaluate` — short-circuits to passed=True
  with `reason_code=GATES_PASSED`. Distinguishable from a
  misconfigured/missing tier (which still fail-closes).
- **Impact:** 98/110 → **110/110 passing** (0 failing).
- **Tests:** 16/16 green. New `test_nba_war_zone_does_not_gate_cv`
  also asserts that a pathological prop (zero ceiling, zero edge,
  zero books, null CV) passes under the new config.

## Gate Config Change: Front Lines TP/HR Tradeoff — Scenario B — 2026-04-23
- **Change:** NBA Front Lines threshold tuned from the simulator's
  Scenario B result.
- **Config (`thresholds.py :: _NBA_FRONT_LINES_BASE`):**
  - `hit_rate_gate.min`: **60.0 → 70.0** (tightened)
  - `tp_gate.min`:        **55.0 → 50.0** (loosened, OVER-side)
  - `tp_gate.under_floor`: 65.0 (unchanged, per user scenario spec)
  - `cv_gate`, `edge_gate`, `coverage_gate`: unchanged
- **Rationale:** Simulator proved the combo raises the passing
  set's average HR from ~70% → **75.5%** while still admitting 37
  additional high-quality near-misses (Jarrett Allen PTS 11.5,
  Reed Sheppard 3PM 1.5, Stephon Castle AST 6.5, Donovan Mitchell
  REB+AST 8.5, etc.) that the previous TP-55 cut was killing.
  Scenario A (HR → 65) admitted 11 additional noise props with
  HR in [65, 69]; rejected in favor of cleaner lane.
- **Post-recompute verification (`final-nba-rt`, 4,024 props):**
  - Front Lines total: 673 props
  - Passing: **79** (was 53 under old config — +26 net; simulation
    predicted 90, delta explained by slate churn between sim and
    recompute)
  - Pass-set quality: avg HR **75.7%**, avg TP 55.8, avg CV 0.411,
    avg edge 18.6, min HR 70 (hard floor honored).
  - Gate failure mix now: hit_rate 66.6%, tp 62.0%, cv 12.3%,
    edge 11.4%, coverage 0.0%. Hit-rate and TP stay the dominant
    filters as expected.
- **Top promoted picks that weren't in last week's board:**
  Reed Sheppard 3PM 1.5 OVER -123 (85% HR), Jarrett Allen PTS 11.5
  OVER -158 (75%), Jarrett Allen PTS+REB 20.5 OVER -117 (85%),
  Stephon Castle AST 6.5 OVER -139 (75%), Immanuel Quickley AST
  3.5 OVER -155 (75%), Donovan Mitchell REB+AST 8.5 OVER -127 (80%).
- **Tests:** 16/16 universal gate-engine tests pass (existing
  Front Lines tests use thresholds loaded from the live config, so
  they pick up the new values automatically).

## Feature: PRA Audit — Auto-Settle Cron + Admin UI — 2026-04-23

### A. Auto-settle cron job
- New `scheduled_pra_audit_settle()` in `/app/backend/server.py`
  (wrapper) + `run_pra_audit_settle()` in
  `/app/backend/services/cron_scheduler.py` (idempotent implementation).
- Registered on the live APScheduler alongside the other daily
  jobs — cron `4:30 AM EST` daily (15 min after the 4:15 EST BDL
  Game Logs sync that refreshes last-night's pts/reb/ast).
- Log format (per prompt):
  `[PRA_AUDIT_CRON] settled=N skipped=M total=T pending=P`
- Idempotent — walks only `{settled: {$ne: True}}`. Safe to run
  repeatedly. Skips rows where the game hasn't concluded (no
  matching date in game logs) or the player has no game logs yet.
- Verified on startup: apscheduler log shows
  "Added job '4:30 AM EST PRA Dual-Projection Audit Settle'".
  Direct invocation test returned
  `[PRA_AUDIT_CRON] settled=0 skipped=515 total=515 pending=515`
  (0 settled is correct — tonight's slate hasn't started yet).

### B. Admin UI
- New page at `/admin/pra-audit` (no auth wrapper — the page
  itself token-gates every fetch with `X-Admin-Token`).
- File: `/app/frontend/src/pages/AdminPRAAudit.jsx` (single
  component, ~380 LOC, zero new dependencies — uses only existing
  `sonner` for toasts and inline styles to match shadcn's dark
  palette).
- Shows:
  - **Audit counts** (total / both / direct_only / synth_only /
    settled / pending)
  - **Accuracy audit** (only populated when settled > 0): direct
    MAE, synth MAE, direct/synth bias, direct/synth side-accuracy %
    — winner color-coded green/red. Archetype and line-bucket MAE
    tables with winner label. Top 10 synth-wins and direct-wins
    samples (edge ≥ 2.0 PRA).
  - **Divergence audit** (live, no actuals needed): overall,
    by-archetype, by-line-bucket stats on `|direct − synth| / direct`.
  - **Notes** from the API response.
- Token is stored in `localStorage.adminDebugToken` (not cookies;
  no server-side session). Re-paste on each device. Refresh and
  "Run Settle Job" buttons for on-demand control; auto-fetches
  on mount if a token is cached.
- Route registered at `/admin/pra-audit` in `App.js`.
- End-to-end verified via curl through the external preview URL:
  - `GET /admin/pra-audit` → 200 (SPA serves the route)
  - `GET /api/v3/admin/pra-audit/report` → 200, returns 515 rows,
    avg divergence 5.19%, 3 archetypes, 5 line buckets
  - `POST /api/v3/admin/pra-audit/settle` → 200, settles 0 (games
    haven't concluded in current UTC wall-clock).

### Scope boundaries honored
- Live projection selection unchanged (`model_projection` still
  direct-first, synth-fallback).
- No gate logic changes.
- No ranking logic changes.
- No retraining.

## Feature: PRA Dual-Projection Audit (A/B Infrastructure) — 2026-04-23
- **Goal:** Persist BOTH the direct PRA model projection and the 3-way
  component synth projection side-by-side on every PRA row so we can
  evaluate the two methods against actual PRA totals once games
  conclude. Live production behaviour is unchanged.
- **What changed:**
  - `ScoringContext` (base.py): +8 audit fields
    (`model_projection_direct/sigma_direct`,
    `model_projection_synth/sigma_synth`, `projection_delta_abs/pct`,
    `projection_compare_status`, `projection_primary_method`).
  - `NBAScoringAdapter` (nba_scoring.py): for PRA props, always runs
    the synth in parallel with the direct model; stamps both sets.
    Live `model_projection` still carries whatever the primary path
    chose (direct-first with synth fallback, unchanged).
  - `prop_scores_store.py`: audit fields added to
    `_SCORE_OUTPUT_FIELDS` so every NBA PRA score doc persists them.
  - `recompute.py`: new `_build_pra_audit_snapshots()` helper and
    upsert into `nba_pra_projection_audit` (idempotent by
    `event_id+player_name+line+recommendation`) so the dual
    projections survive `final-nba-rt` overwrites.
- **New admin endpoints:**
  - `POST /api/v3/admin/pra-audit/settle` — walks unsettled audit
    rows, joins with `nba_master_hub_2026.bdl_game_logs` on player
    name + game date (±1 day slack for UTC skew), writes actual
    pts/reb/ast/pra back into the audit row.
  - `GET /api/v3/admin/pra-audit/report` — counts, divergence audit
    (direct-vs-synth absolute delta %), MAE audit once settled data
    exists (direct vs actual / synth vs actual), per-archetype
    (guard/wing/big from `nba_master_hub_2026.position`) and
    per-line-bucket breakdowns, top 10 synth-outperforms-direct and
    direct-outperforms-synth samples with >2.0 MAE edge.
- **Snapshot created:** 511 PRA rows, **100% both_available**. Zero
  settled (tonight's slate hasn't started yet — game_start_utc is
  23:10 UTC, current 05:41 UTC). MAE audit will populate after
  `/settle` is re-run post-games.
- **Divergence audit (live, 511 samples):**
  - Overall: avg 5.18%, median 3.55%, max 49.5%
  - By archetype: bigs 5.85% / guards 5.61% / wings 4.24%
  - By line bucket: **<20 = 7.24%** (largest divergence); 50+ = 1.79% (methods agree on HIGH-total players)
  - Outliers: Jerami Grant cluster (49.5% → 35.9% across lines), Shaedon Sharpe (25.3% → 21.1%). Both traced to empirical covariance structure the direct model doesn't decompose.
- **Files:** `adapters/base.py`, `adapters/nba_scoring.py`,
  `prop_scores_store.py`, `recompute.py`, `routes/admin.py`.

## Feature: 3-Way Combo Projection Synthesis (Generalized N-Way) — 2026-04-23
- **Goal:** Generalize combo synthesis to arbitrary N components, and
  add 3-way synth as a FALLBACK path for direct-model families
  whose model returned None (e.g., PRA → PTS+REB+AST).
- **What changed:**
  - Renamed signature: `_predict_combo_projection(..., components=Sequence[str])`
    — no more `family` arg; caller passes the component tuple directly.
  - Math extended to arbitrary N:
    `var_combo = Σ var_i + 2·Σ_{i<j} cov(i, j)`.
  - Added `_SYNTH_FALLBACK_COMPONENTS = {"pra": ("PTS","REB","AST")}`.
  - Wired `build_context` so direct-model families first try the
    trained model; if `projection is None` AND the family has a synth
    recipe, run N-way synth as a rescue. Primary 2-way combos
    (pts_reb / pts_ast / reb_ast) continue to route straight to synth
    since no direct model exists.
- **Live impact (NBA `final-nba-rt`, 4,028 props recomputed):**
  - PRA: 137 direct-model projections (unchanged); 0 rescued by
    3-way synth. The 13 remaining PRA nulls are all **"Player not
    found"** in the VK training set (C.J. McCollum, R.J. Barrett,
    Jabari Smith Jr, etc.) — their PTS/REB/AST component models
    also fail the same lookup, so synth can't rescue them. That's
    honest data-quality reporting; the fallback infrastructure is
    live and will catch any future PRA-only model failures where
    components still work.
  - Overall projection_method distribution: 2,283 `model` +
    1,188 `combo_synth` (2-way primary).
- **Math sanity check (3-way synth vs direct PRA model for
  well-trained players):**
  - Jokic (55.5): direct 51.55 vs synth 50.68 (Δ 1.7%)
  - LeBron (41.5): direct 35.02 vs synth 34.37 (Δ 1.9%)
  - Giannis (48.5): direct 39.38 vs synth 39.00 (Δ 1.0%)
  - Luka (55.5): direct 43.67 vs synth 48.20 (Δ 10.4%) — largest
    divergence traced to Luka's empirical cov(PTS, AST) = −12.09
    (sharp usage trade-off) which the direct PRA model smooths over.
- **Files:** `/app/backend/services/scoring/adapters/nba_scoring.py`.

## Feature: Combo Projection Synthesis — 2026-04-23
- **Goal:** Give combo stat families (`pts_reb`, `pts_ast`, `reb_ast`)
  a real `model_projection` + `model_sigma` by synthesizing from the
  two component family models (no new training).
- **Math:**
  - `proj_combo = proj_a + proj_b`
  - `var_combo  = var_a + var_b + 2·cov(a,b)`
  - `sigma_combo = sqrt(var_combo)`
  - Covariance source: **empirical** (L20 paired game-log values via
    `np.cov(ddof=1)`, ≥5 paired observations required) with a
    **fallback** of `rho·sigma_a·sigma_b` (rho=0.25, deliberately
    conservative — widens sigma vs independence).
- **Wiring:** `NBAScoringAdapter._predict_combo_projection`. Called
  after the family-to-model routing when `resolved_family ∈
  {pts_reb, pts_ast, reb_ast}`. Reuses the existing
  `_predict_model_prob_over` (or `_predict_vk2_prob_over` when
  `active_method=='vk2'`) for each component, so VK2 activation
  transparently propagates to combos.
- **Persisted on every score doc:**
  - `model_projection` / `model_sigma` (same fields as direct model)
  - `projection_method` ∈ {"model","combo_synth",None}
- **Impact (NBA `final-nba-rt` after recompute, 4,005 props):**
  - `pts_reb`: 0 → **137** (+137) combo projections
  - `pts_ast`: 0 → **123** (+123)
  - `reb_ast`: 0 → **26** (+26)
  - Total combo projections stamped (incl. combo props that never
    reached gate eval): **1,173** `projection_method="combo_synth"`
    rows, **2,278** direct `projection_method="model"` rows.
  - **Empirical-covariance path: 286/286 (100%)** for combo props
    that passed gate evaluation. Fallback_rho was unused in this
    recompute.
  - Canonical PTS / REB / AST / PRA / threes: unchanged (no
    regression).
- **Worked example (LeBron pts_reb 32.5 UNDER):**
  - PTS proj 21.88 σ 5.80; REB proj 6.51 σ 2.41
  - Empirical cov(PTS,REB) = +2.3474
  - Combo proj = 28.39; combo σ = √(33.64 + 5.808 + 2·2.3474) = **6.644**
  - p(over 32.5) = 0.268 ⇒ p_true_model(UNDER) = 0.732
- **Files:** `adapters/base.py`, `adapters/nba_scoring.py`,
  `prop_scores_store.py`, `recompute.py`.

## Feature: Family-Based Model Projection Routing — 2026-04-23
- **Problem:** Projection model was gated on `stat_type in
  {"PTS","REB","AST","3PM","PRA"}`. Raw market names that didn't
  equal one of those strings received no `model_projection` even
  when their canonical family had a trained VK/VK2 model on disk.
  (In practice most PTS/REB/AST/PRA alt-markets were already being
  pre-normalized to the short names at the stat_type mapping step, so
  the real gap was `player_threes` + `player_threes_alternate`.)
- **Fix:**
  - Added `_FAMILY_TO_MODEL_KEY = {"pts":"PTS","reb":"REB","ast":"AST",
    "pra":"PRA","threes":"3PM"}` to `NBAScoringAdapter`.
  - Replaced `if stat_type in self._MODEL_STATS` with a resolution
    step: `model_key = _FAMILY_TO_MODEL_KEY.get(_resolve_family(stat_type))`
    and invoked `_predict_model_prob_over` / `_predict_vk2_prob_over`
    with the canonical `model_key`, not the raw `stat_type`. The
    predictors' internal `stat_type in _MODEL_STATS` check is now
    satisfied by any family-matching raw market.
- **Impact (`final-nba-rt`, 4,015 props recomputed):**
  - `player_threes_alternate`: 0 → **248 with projection** (+248)
  - `player_threes`: 0 → **22 with projection** (+22)
  - Canonical PTS / REB / AST / PRA: unchanged (622 / 510 / 371 / 509
    — no regression).
  - Other unsupported families (`pts_reb`, `pts_ast`, `reb_ast`,
    `stl`, `blk`) still have 0 projection — expected; scope was
    family-based routing only, no new training, no combo synthesis.
- **Files:** `/app/backend/services/scoring/adapters/nba_scoring.py`.

## Feature: Universal Per-Prop CV Computation — 2026-04-23
- **Problem:** ~1,300 NBA props (all alt-line + combo markets) had
  null CV because the adapter's `_STAT_FIELD_MAP` only knew the 8
  canonical short names (PTS, REB, AST, PRA, 3PM, STL, BLK, TO). Raw
  odds-market names (`player_blocks_alternate`,
  `player_points_rebounds`, etc.) fell through and returned
  `(None, ...)`. Gate engine then treated the missing CV differently
  per sport/tier, which was the root cause of the "War Zone kills
  alt-lines" behavior we removed yesterday.
- **Fix:**
  1. Extended NBA `STAT_FAMILY_ALIASES` in
     `/app/backend/services/scoring/gates/thresholds.py` to map every
     raw market name (standard + alternate) to a canonical family
     (`pts`, `reb`, `ast`, `pra`, `threes`, `stl`, `blk`, `pts_reb`,
     `pts_ast`, `reb_ast`, `turnovers`). Single source of truth shared
     by CV routing and gate-threshold resolution.
  2. Added `_FAMILY_SPEC` in `NBAScoringAdapter` mapping each family
     to the sum-of-game-log-fields that produces its per-game value.
     Combo families inherit variance of the combined stat (PRA sums
     pts+reb+ast per game, then takes stddev/mean of that vector).
  3. Rewrote `_compute_cv_and_hit_rate` so CV is derived from the
     resolved stat-family (line-independent) and cached per
     `(player_lower, family)`. Every line / alt-line / side for the
     same (player, family) now shares the exact same CV value.
  4. Added explicit `cv_status` on `ScoringContext` with values
     `computed | unavailable_stat_family | missing_source_distribution`.
     MLB adapter updated to emit the same contract.
  5. Persisted `cv` + `cv_status` as first-class fields on every
     score doc via `_SCORE_OUTPUT_FIELDS`; `recompute.py` writes them
     from `ctx.cv` / `ctx.cv_status`.
- **Impact (NBA `final-nba-rt` after recompute of 3,766 props):**
  - CV coverage: **0 → 3,522 docs (93.5%)**
  - `cv_status=computed`: 3,522
  - `cv_status=missing_source_distribution`: 244 (genuine data gaps —
    players with <5 L20 games, e.g. two-way callups, traded players)
  - `cv_status=unavailable_stat_family`: **0**
  - (Player, family) pairs with inconsistent CV across lines: **0 / 637**
  - War Zone: 95 props, 37 computed, 58 genuine-data-gap nulls (was
    effectively 100% null on alt-lines).
- **Files changed:**
  - `/app/backend/services/scoring/gates/thresholds.py` (alias map)
  - `/app/backend/services/scoring/adapters/base.py` (cv_status field)
  - `/app/backend/services/scoring/adapters/nba_scoring.py`
    (_FAMILY_SPEC + _resolve_family + rewritten cv/hit_rate computer)
  - `/app/backend/services/scoring/adapters/mlb_scoring.py` (cv_status)
  - `/app/backend/services/scoring/prop_scores_store.py` (persistence)
  - `/app/backend/services/scoring/recompute.py` (doc emission)

## Gate Config Change: War Zone CV Floor Removed — 2026-04-23
- **Design decision (not experiment):** War Zone must not penalize
  consistency. If a prop qualifies by odds/tier logic, low or missing CV
  cannot disqualify it.
- Removed `cv_gate {"min_cv_floor": 0.45}` from `_NBA_WAR_ZONE_BASE` in
  `/app/backend/services/scoring/gates/thresholds.py`. MLB and NFL War
  Zone configs had no CV gate — unchanged.
- `services.mlb_tier_sorter.war_zone_cv_modifier` (ranking score nudge)
  retained; it is informational only and does not affect pass/fail.
- Updated test `test_nba_war_zone_does_not_gate_cv` to assert low/high/
  missing-CV all pass War Zone. Full 16-test suite green.
- Recomputed `final-nba-rt` to refresh `gate_eval` on every prop.
- **Impact (NBA War Zone, 471 props):**
  - Before: 10 passing (2.12%), 460 cv_fail, 6 ceiling_fail.
  - After: 465 passing (98.73%), 0 cv_fail, 6 ceiling_fail.
  - Net unlock: +455 props. Alt-line markets (BLK/STL/THREES alternate)
    now flow through War Zone instead of dying on null CV.
- Primary remaining blocker: `ceiling_gate` (1.27% fail rate), as
  intended. No other hidden consistency penalty was introduced.

## Feature: Admin Threshold Simulator — 2026-04-23
- `POST /api/v3/admin/threshold-simulate` — preview single-gate threshold
  changes against live `{sport}_prop_scores @ final-{sport}-rt.gate_eval`.
- No recompute, no writes, no scoring logic touched. Re-evaluates ONE
  gate under a proposed threshold; every other gate's outcome stays
  frozen as originally stored.
- Returns: summary (currently_passing, newly_qualified, newly_rejected,
  net_change, projected_passing, unchanged_pass/fail), sample lists for
  each transition, `blocked_by_other_gates` (fake unlocks that would
  still fail other gates), and near-miss distribution vs current
  threshold (within 1/2/5 metric units).
- Auto-infers `mode` ('min'/'max') from each prop's stored gate
  comparator when not explicitly provided.
- Auth: `X-Admin-Token` (same as gate-stats).
- Implementation: `/app/backend/routes/admin.py :: threshold_simulate`.
- Verified live: NBA tp 55→52 unlocks 35 (net +35); tp 55→60 drops 12
  (net -6, flagged SGA AST as a rejection); cv 0.75→0.90 auto-infers
  'max' mode; MLB stat_family filter scopes correctly; 401/400/422
  error paths validated.

## Feature: Admin Gate Stats Endpoint — 2026-04-23
- `GET /api/v3/admin/gate-stats?sport={sport}&tier={optional}&stat_family={optional}`
- Data-driven threshold tuning over `{sport}_prop_scores @ final-{sport}-rt`.
- Pure aggregation of `gate_eval` — no recompute, no writes.
- Returns: total/passed/failed counts, per-gate failure breakdown (reason-code
  keyed), top multi-fail combos, per-gate near-miss deltas (avg / median /
  tight-band / wide-band), and breakdowns by `stat_family` and `tier`
  (tier only when no tier filter provided).
- Auth: protected by `X-Admin-Token` header matching `ADMIN_DEBUG_TOKEN` env.
- Implementation: `/app/backend/routes/admin.py :: gate_stats`.
- Verified live: NBA (1184 props, 4.81% pass rate) and MLB (157 props,
  11.46% pass rate). Filter + auth failure paths tested (401 / 422 / 200).


## Health
- Broken: None
- Mocked: None

---

## Expected-Minutes Model — Rollback + New Regression (2026-04-23)

### Scope delivered

1. **Rollback.** `scripts/retrain_nba_vk2.py` reverted to the 52-feat
   fixed-mins pruned baseline. Removed: `USAGE_FEATURES` (7 cols),
   `MINUTES_FEATURES` (17 cols), `PRUNED_USAGE_FEATURES`,
   `PRUNED_MINUTES_FEATURES`, the in-model per-minute / expected_minutes
   / usage_trend / fga_per_min / touches_per_min computation in
   `build_features`, and the `--usage` / `--minutes` CLI flags. Only
   the clean 52-feat `PRUNED_FEATURES` schema + `--pruned` flag remain.
2. **Production artifact swap.** Moved the live `vk2_{stat}.pkl`
   (59-feat usage models) to `models/archive_usage59/` and copied
   `models/archive_fixedmins_52feat/vk2_{stat}_52fixedmins.pkl` into
   the production paths. All 5 artifacts now report
   `version=NBA_VK_v2_5yr_weighted_pruned52`, `feature_count=52`.
   Backend restart clean. Ferrari endpoints HTTP 200.
3. **NEW: expected-minutes regression.**
   `scripts/train_nba_minutes_model.py` trains a 15-feature
   XGBRegressor predicting `minutes_tonight` per player per game.
   Artifact: `/app/backend/models/nba_expected_minutes.pkl`
   (`NBA_EXPECTED_MINUTES_v1`). Metrics on 2024 hold-out:
   R²=0.611, MAE=5.99 min, RMSE=8.73, bias=-0.21, σ=8.72. Top
   feature `min_L3_mean` 72% of gain.
4. **NEW: segmented composition eval.**
   `scripts/eval_minutes_composition.py` runs the 2024 hold-out across
   4 predictors (baseline / min_only / blend_05 / blend_bench) and 7
   segments (bench, starter, declining, line<10, line 10-20, line≥20,
   overall). Report:
   `/app/backend/reports/vk2_expected_minutes_segmented.json` +
   `expected_minutes_summary.md`.
5. **Regression tests.** `tests/test_expected_minutes_model.py` adds
   10 new tests locking in the rollback (VK2 version + feature count,
   forbidden-feature guard on `build_features`, grep-guard on the
   retrain script CLI) and the new minutes model (artifact exists,
   R² ≥ 0.5, MAE < 8, bias < ±1, top feature is a recency-minutes
   rolling mean). All 10 pass.

### Rollback resolved the structural bench regression

After rollback, the 52-feat fixed-mins baseline is near-zero bias on
bench and declining regimes:

| Stat | bias (bench)  | bias (declining) |
|------|---------------|------------------|
| PTS  | +0.04         | +0.13            |
| REB  | +0.00         | +0.04            |
| AST  | +0.00         | +0.02            |
| 3PM  | -0.01         | +0.00            |
| PRA  | +0.02         | +0.19            |

### Residual low-line over-prediction quantified

The baseline still over-predicts on `line<10`. The minutes model
composition (`blend_bench`: `predicted_minutes × historical per-min
rate` when `min_L10 < 20`, else baseline) trims the bias ~14% with
no RMSE penalty on the target stats (PTS, PRA):

| Stat | segment | baseline RMSE/bias | blend_bench RMSE/bias |
|------|---------|--------------------|------------------------|
| PTS  | line<10 | 4.73 / +2.29       | 4.73 / +1.96          |
| PRA  | line<10 | 7.33 / +3.93       | **7.22** / +3.39 (RMSE also improves) |
| REB  | line<10 | 2.10 / +0.28       | 2.14 / +0.19          |
| AST  | line<10 | 1.55 / +0.10       | 1.57 / +0.05          |
| 3PM  | line<10 | 1.08 / -0.00       | 1.10 / -0.03          |

### Success-condition status

- ✅ Low-line over-prediction reduced (PRA -14% bias + RMSE win,
  PTS -14% bias with unchanged RMSE).
- ✅ Bench + declining segments improved by the rollback itself.
- ✅ Expected-minutes model trained, validated, and ready to compose.
- ⚠️ **NOT YET WIRED INTO PRODUCTION SCORING.** The NBA scoring
  adapter (`services/scoring/adapters/nba_scoring.py`) still calls the
  VK2 baseline only. Adapter integration is a follow-up P0 gated on
  user approval.

### Files of record
- `/app/backend/scripts/retrain_nba_vk2.py` (rolled back)
- `/app/backend/scripts/train_nba_minutes_model.py` (NEW)
- `/app/backend/scripts/eval_minutes_composition.py` (NEW)

---

## Expected-Minutes Wiring — LIVE (2026-04-23)

### Scope delivered
Wired the `blend_bench` composition into the NBA scoring adapter for
**PTS and PRA only**, as validated by
`reports/vk2_expected_minutes_segmented.json`. Gate logic and TP logic
were NOT modified. REB / AST / 3PM untouched. Other sports untouched.

### Files changed
| File | Δ |
|------|---|
| `services/scoring/adapters/nba_scoring.py` | +230 LOC — loads `nba_expected_minutes.pkl`, builds a 15-feat minutes-model input from the shared VK2 feature dict, computes `predicted_minutes × per_min_rate` when bench regime (`min_played_L10_mean < 20`). Applied in BOTH `_predict_model_prob_over` (legacy VK path) and `_predict_vk2_prob_over` (VK2 path) so the composition fires regardless of which `p_true_method` is active. |
| `services/scoring/nba_vk2_features.py` | +18 LOC — fixed adapter-side `min` parsing to accept plain "30" (previously only "30:00"). This was the same bug previously fixed in the retrain script but never propagated to the adapter; without it, `min_played_L*` features fed the live model as 0 and bench detection was broken. |
| `services/scoring/adapters/base.py` | +6 fields on `ScoringContext` (`minutes_composition_applied` + 3 audit fields). |
| `services/scoring/prop_scores_store.py` | +4 persisted fields in `_SCORE_OUTPUT_FIELDS`. |
| `services/scoring/recompute.py` | +12 LOC — copies composition fields onto the score doc. |
| `tests/test_expected_minutes_adapter_wiring.py` | NEW — 13 tests covering narrow rollout, bench/starter split, rate clamp, plain-string min parsing. |
| `scripts/compare_minutes_composition.py` | NEW — live before/after diff tool. |
| `reports/minutes_composition_live_diff.md` | NEW — live live-board comparison. |

### Live impact (post first full recompute)
| Stat | Scored | Composition applied | % |
|------|-------:|--------------------:|--:|
| PTS  | 5,093  | 52 | 1.0% |
| PRA  | 4,768  | 45 | 0.9% |

- 24 unique bench-regime players composed (player-level dedup).
- Mean |Δ projection|: 1.43 stat units; max |Δ|: 2.97.
- 16 downward shifts (projection reduced) / 8 upward shifts (projection raised).

### Representative material changes
- **Nikola Vucevic PTS 7.5**: 10.15 → 7.18 (−2.97). Pred minutes
  19.3, per-min rate 0.37. Why: Vucevic's rolling PTS_L5 encodes his
  starter-minute production; the bench-regime flag (L10 recently
  dropped) pulls the projection to his actual per-min rate × expected minutes.
- **Mitchell Robinson PRA 12.5**: 14.66 → 17.15 (+2.49). Pred minutes
  17.7, per-min rate 0.97. Bench role but with high ball involvement →
  composition surfaces latent upside baseline missed.
- **Justin Edwards PRA 14.5**: 13.05 → 10.69 (−2.36). Canonical
  bench-regime downshift matching the `line<10` bias the eval flagged.

### Verification
- All 6 Ferrari endpoints HTTP 200; pick counts unchanged (NBA 10/10/10, MLB 9/9/10).
- Identity resolution remains 100% for both sports.
- 51 regression tests pass (13 new adapter-wiring + 10 minutes-model
  + 28 existing identity / coverage / delta).
- Scoring ladder, gates, TP engine, sigma computation are all
  untouched — composition only replaces `projection`, leaving
  `sigma` and market-derived `tp` unchanged.

### Observability
- `minutes_composition_applied` / `minutes_composition_baseline_projection`
  / `minutes_composition_predicted_minutes` /
  `minutes_composition_per_min_rate` persist on every scored PTS / PRA
  doc.
- Adapter counters (`_min_composition_applied` / skipped / errors)
  track run-level hit rate.

- `/app/backend/models/nba_expected_minutes.pkl` (NEW)
- `/app/backend/models/vk2_{pts,reb,ast,3pm,pra}.pkl` (swapped → 52feat)
- `/app/backend/models/archive_usage59/` (rolled-back 59feat models)

---

## Admin Observability — `/api/v3/admin/minutes-composition-stats` (2026-04-23)

### Scope delivered
Read-only observability endpoint for the NBA PTS/PRA minutes
composition. Paginates the live scoring table (no recompute, no
mutation), surfaces aggregate counters + top-10 material-change props
so we can validate the rollout over 1-2 slates.

### Endpoint
```
GET /api/v3/admin/minutes-composition-stats?sport=nba
X-Admin-Token: <ADMIN_DEBUG_TOKEN>
```

### Response shape
```json
{
  "sport": "nba",
  "version_tag": "final-nba-rt",
  "global": {
    "total_props": 4318,
    "composed_props_count": 97,
    "composed_pct": 2.25,
    "avg_projection_delta": -0.42,
    "median_projection_delta": -0.61,
    "max_positive_delta":  2.71,
    "max_negative_delta": -3.01
  },
  "directional": {
    "count_upward_adjustments": 34,
    "count_downward_adjustments": 63,
    "avg_upward_delta":  1.51,
    "avg_downward_delta": -1.46
  },
  "regime": {
    "bench_count": 97,
    "starter_count": 1209,
    "avg_delta_bench": -0.42,
    "avg_delta_starters": 0.0
  },
  "by_stat_family": {
    "PTS": {"composed_count": 52, "avg_delta": -0.73, "upward_count": 17, "downward_count": 35},
    "PRA": {"composed_count": 45, "avg_delta": -0.06, "upward_count": 17, "downward_count": 28}
  },
  "top_positive_delta": [ { player_name, stat_type, line, side, baseline_projection,
                            composed_projection, delta, predicted_minutes, per_min_rate, tp, tp_books_used }, ... up to 10 ],
  "top_negative_delta": [ ... up to 10 ],
  "notes": [...]
}
```

### Files changed
| File | Δ |
|------|---|
| `routes/admin.py` | +210 LOC — `/v3/admin/minutes-composition-stats` endpoint. |
| `tests/test_minutes_composition_stats_endpoint.py` | NEW — 15 tests covering auth, response shape, field presence, sort invariants, stat-family sum invariant, idempotency, MLB zero-composed guard. |

### Verification
- Auth: missing token ⇒ 401 ✓, wrong token ⇒ 401 ✓, correct token ⇒ 200 ✓.
- Latency: **140–210 ms** across 5 consecutive calls (under the 200 ms target at p50).
- Idempotent: consecutive calls return identical global counts.
- All 64 relevant regression tests pass (15 new + 49 existing).
- Ferrari endpoints HTTP 200 (NBA 10/10/10, MLB 9/9/10) — no regressions.

### Read-only invariants (enforced by code review + tests)
- Does NOT call `recompute_sport` or any scoring helper.
- Does NOT write to any collection.
- Reads only the four persisted composition audit fields plus
  `model_projection`, `tp`, `tp_books_used`, `recommendation`, etc.
- Returns `composed_props_count == 0` for MLB (composition is
  NBA-only by design).

- `/app/backend/reports/vk2_expected_minutes_segmented.json`
- `/app/backend/reports/expected_minutes_summary.md`
- `/app/backend/tests/test_expected_minutes_model.py` (10 tests)


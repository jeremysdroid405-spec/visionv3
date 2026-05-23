# Pick Vision — Internal Quant Terminal & Multi-Sport Betting Platform

## Original Problem Statement
Restructure React/FastAPI betting app to a 100% Local-First Database Model with
multi-sport support. Implement Google/Apple OAuth and Stripe for payments.
Strict requirements: 100% ID-based joins, Universal Opportunity Models, strict
SSOT, scalable MLB historical replay framework.

Current focus: Private Universal Historical Testing Command Center frontend UI
controlling historical replay pipelines via the Emergent Admin API.

## Architecture
- Backend: FastAPI + local MongoDB (`pick_vision`) on port 8001 (supervisor)
- Frontend: React + Shadcn UI on port 3000
- Emergent Admin API: `/app/backend/routes/emergent_admin/`
- SSOT historical replay pipeline:
  `scripts/sgo/historical_full_pipeline_replay.py` → `production_replay_runner`
- Quant Terminal UI: `/app/frontend/src/pages/AdminTesting.jsx` (~2.9k lines)

## Key DB Collections
- `sgo_player_stats`, `sgo_replay_alt_odds_raw`, `sgo_pp_research_outcomes`
- `sgo_propvision_full_pipeline_replay`, `..._replay_diff`
- `candidate_thresholds`, `emergent_admin_jobs`

## What's been implemented (chronological)
### 2026-05 — Quant Terminal foundation
- `/admin/testing` (sweeps, replay, results, coverage, optimizer, deploy)
- `emergent_admin` backend (jobs, optimizer, preflight, coverage, models)
- SSOT refactor of historical replay
- Local Replay Warehouse offline coverage modes
- Cache-first SGO stats ingest

### 2026-05-21 — Job runner silent-failure fix
- `_run_job` outer try/except (pre-spawn exceptions reach DB)
- `_backend_cwd()` 4-level fallback
- `_RUNNER_TASKS` strong-ref set
- `/jobs/_self_test` + `/jobs/_reconcile_stuck` operator endpoints
- Verified locally end-to-end

### 2026-05-21 — Failed-job diagnostics + pipeline preflight
- **Backend** already captured full tracebacks in `tail_preview`; UI was hiding them.
- **Pipeline orchestrator** now includes the two preflight prerequisites for
  full pipeline replay (previously caused "no_reshape_odds" hard-fail):
  - Step 4: Reshape Odds (`scripts.sgo.reshape_sgo_to_replay_odds`)
  - Step 5: Grade Outcomes (`scripts.sgo.build_historical_outcomes`)
  - Step 6: Full Pipeline Replay (was step 4)
- **Failed-step UI** auto-opens the traceback `<details>` panel, widens
  the inline error line (100 → 400 chars), extracts a human-readable
  message from the last traceback line when `job.error` is null, and
  keeps the log panel visible after halt (showing last 80 lines).

### 2026-05-23 — Unified Research Result Architecture (Option A)
Wired the Results tab to dedicated research endpoints instead of the
generic `/collections/{name}/find` route. The new endpoints understand
both sweep schemas (PP-free `market_truth_pp_free` + per-tier
`per_tier_per_stat_family`) and do server-side ranking, filtering, and
best-of bucketing.
- See `/app/backend/routes/emergent_admin/research.py` for full endpoint list.
- Schema-tolerant frontend rendering; MM-DD-YYYY date display; Optimizer
  auto-loads the cached pipeline window so sweeps consume zero SGO credits.
- 25 backend tests passing.

### 2026-05-23 — Pod split: dedicated research_worker daemon
Heavy compute (optimizer sweeps, historical replay, grid sweeps,
candidate generation) was running inside the FastAPI uvicorn process,
starving live scoring and API request handling. Split now enforced:
- **New supervisor service** `research_worker` running
  `python -m workers.research_worker` (NOT a uvicorn worker, NOT in the
  request lifecycle). Conf at `/etc/supervisor/conf.d/research_worker.conf`.
- **Mongo-backed queue** on the existing `emergent_admin_jobs` collection
  with `worker_queue=True`. Atomic `findOneAndUpdate` claim ensures
  exactly-once execution and serves as the single max-concurrent=1 gate.
- **HEAVY_MODULES** set in `workers/queue.py` lists modules that MUST
  route through the worker (optimizer CLI, full-pipeline replay, grid
  sweep, reshape, build_historical_*, ingest_historical_*, BDL ingest).
  Light preflight/coverage modules still spawn inline.
- **Per-job resource caps** applied to each spawned subprocess via
  `preexec_fn`: `nice +10`, `RLIMIT_AS = 4 GB`, `oom_score_adj = +500`,
  hard timeout 2 h. All overrideable via env vars.
- **Optimizer migration** — `POST /optimizer/run` persists the request to
  `optimizer_runs` then enqueues `scripts.research.run_optimizer_cli
  --run-id`. The CLI re-hydrates the in-process state slot and calls the
  same `_run_optimizer` logic. **Same endpoint signature**, same UI
  polling path. The frontend doesn't need to change anything except the
  new Worker Health bar.
- **Real-time output** + `rss_peak_bytes` + `cpu_seconds` captured per
  job. Crash recovery on worker restart force-finalizes orphaned claimed/
  running jobs as `errored`.
- **New endpoints** under `/api/emergent-admin/worker/*`:
  - `GET /worker/health` — queue depth, active job, worker PID/RSS/CPU,
    heartbeat age + staleness flag, backend PID/RSS/CPU for comparison.
  - `GET /worker/queue` — list jobs (status filter optional).
  - `POST /worker/cancel/{job_id}` — SIGTERM the in-flight child without
    killing the worker daemon.
- **Pagination** — `/research/grid-results/{run_id}` rewritten as a Mongo
  `$sort + $limit` aggregation; never loads the cell set into Python
  memory. New `/research/grid-results/{run_id}/cells` for paginated table
  scans (offset/limit hard-capped at 500/req).
- **Worker Health bar** in `AdminTesting.jsx` polls every 5 s. Shows
  worker status, heartbeat age, queue depth, active job, worker/backend
  RSS side-by-side.
- **Tests**: 32 backend tests passing (added 7 covering worker health,
  queue, cancel, auth, heavy-module routing, paginated cells + offsets).
- **Legacy `/optimizer` endpoints kept intact** per user preference.

## Backlog (priority order)
### P0 — Awaiting explicit user go-ahead
- Google/Apple OAuth via Emergent-managed Google Auth
  (must call `integration_playbook_expert_v2` before writing any auth code)
- Stripe payments (must call `integration_playbook_expert_v2`, use pod test keys)

### P1
- Backfill `sgo_player_stats` Mar–May 2025 for full feature hydration
- Retire legacy NBA replay → migrate to universal pipeline runner
- NFL-ready config scaffold for the universal pipeline
- Decompose `AdminTesting.jsx` into per-tab subcomponents

### P2
- Audit `points` stat_family anomaly on MLB props

## Critical Notes
- DO NOT run heavy MLB historical sweeps locally — pod OOM.
- Frontend changes require `git pull` AND `yarn build` on the prod host.
- `EMERGENT_ADMIN_TOKEN` lives in `backend/.env`.
- New pipeline steps 4 (reshape) + 5 (grade) are `skippable: true`; they
  default to NOT skipped so they run automatically. Toggle off for re-runs.

### 2026-05-23 — Scoring-layer contract drift fix (score_historical_with_live_mlb_hf)
Root cause of 1736/2020 "predict ok but mu/sigma/model_p incomplete":
the historical scorer read `projection_mu / sigma / model_probability`
off the live `MLBHighFrictionModel.predict()` return — fields that have
NEVER existed on the live response. The live model emits
`predicted / std_dev / prob_over` (`prob_over` as **percentage 0-100**).
The legacy keys silently returned None, the "missing" guard tripped,
zero rows ever reached `bulk_write`.
- **Single normalisation boundary** `_extract_live_outputs(result, side)`
  in `scripts/sgo/score_historical_with_live_mlb_hf.py` reads the live
  keys, converts `prob_over` → 0-1, flips for UNDER bets, clamps
  out-of-range, rejects non-numeric, treats σ=0 as missing.
- **Diagnostic logging on every run**: per stat_family scoreboard
  (scored / missing / errored / no_hub / no_hf), top error messages,
  missing-field breakdown by name, plus `--dump-predictions N` sample
  of raw `predict()` returns. Contract drift now impossible to miss.
- **Hard-fail mode**: `--strict-min-scored-ratio R` exits non-zero when
  `scored / (scanned − skipped) < R`. Worker marks the job `failed`.
  Use 0.30 in sweeps to fail fast on contract drift.
- **Probe parity**: `--probe` reuses the extractor so its missing-fields
  report matches the run.
- **Worker routing**: added to `HEAVY_MODULES` so the scorer runs under
  resource-capped worker (nice +10 / 4 GB / 2 h).
- **Tests**: `tests/test_score_historical_live_contract.py` — 10
  contract tests pin the extractor: happy path, UNDER flip, every
  missing-field combo, σ=0, legacy-schema rejection, clamp, non-numeric
  guard, family alias coverage. 42/42 backend tests pass.



### 2026-05-23 — NFL research pipeline (Phase 1 — probe + ingest + outcomes)
NFL backtest pipeline scaffolded with the **hybrid collection layout**
per user choice: raw ingestion stays on `sgo_*` shared collections keyed
by `league_id`; derived outputs split into NFL-suffixed collections so
MLB and NFL backtests stay isolated.

**New / changed:**
- `services/replay/nfl_stat_family_map.py` — canonical NFL family ↔ SGO
  stat_id aliases + family → player_stats lookup keys. Single SSOT for
  the NFL stat catalogue. Extend when new stat_ids show up in the probe.
- `scripts/sgo/probe_nfl_data.py` — **NEW**, read-only. Hits SGO with
  `expandResults=true`, dumps distinct (statID, marketName) pairs,
  sample playerStats keys, mapping coverage vs the family map, and 3
  sample raw player-stats dicts. Never writes Mongo.
- `scripts/sgo/ingest_historical_player_stats.py` — added
  `_normalize_nfl_stats()`, routed `--league=NFL` to it.
- `scripts/sgo/build_pp_research_core.py` — `--out-coll` plus
  auto-routing of `--league=NFL` to `sgo_nfl_research_core`. MLB path
  unchanged. Threaded through `build_month` / `ensure_out_indexes`.
- `scripts/sgo/build_historical_outcomes.py` — added NFL stat-family
  resolvers + SGO statID aliases. `--out-coll` / `--src-coll` accept
  per-league overrides; `--league=NFL` reads `sgo_nfl_research_core`
  and writes `sgo_nfl_research_outcomes`.
- `workers/queue.py` HEAVY_MODULES — probe + research-core builder
  routed through the worker.
- `routes/emergent_admin/policy.py` — `probe_nfl_data` allowed (read);
  `build_pp_research_core` re-enabled; outcomes exposes `--out-coll` /
  `--src-coll`.
- `tests/test_nfl_pipeline_unit.py` — 16 tests pinning aliases, NFL
  normalizer dispatch, every canonical family having a resolver, and
  resolvers reading the normalized fields.

**Phase 1 runbook (run on the prod host — preview pod has no SGO key):**

```bash
# 1. Probe — confirm SGO returns NFL data and our mapping covers it
python -m scripts.sgo.probe_nfl_data \
    --start=2025-09-04 --end=2025-09-09 \
    --max-events=200 --save-samples=/tmp/nfl_probe.json

# 2. Full SGO event ingest (writes sgo_events, sgo_props_raw,
#    sgo_player_stats, sgo_book_consensus, etc.)
python -m scripts.sgo.ingest --league=NFL \
    --start=2025-09-04 --end=2025-09-09

# 3. Cache-first historical player stats backfill (resumable)
python -m scripts.sgo.ingest_historical_player_stats \
    --league=NFL --start=2025-09-04 --end=2025-09-09 --source=auto

# 4. Build NFL research core (PrizePicks-anchored)
python -m scripts.sgo.build_pp_research_core \
    --league=NFL --start=2025-09-04 --end=2025-09-09
# → sgo_nfl_research_core

# 5. Grade NFL outcomes
python -m scripts.sgo.build_historical_outcomes \
    --league=NFL --start=2025-09-04 --end=2025-09-09 --resume \
    --debug-unresolved
# → sgo_nfl_research_outcomes
```

**Phase 2 deferred (after row counts validated):**
- `reshape_sgo_to_replay_odds.py` NFL support → `sgo_nfl_replay_alt_odds_raw`
- `services/replay/nfl_feature_cache.py` (rolling priors from `sgo_player_stats`)
- `scripts/nfl_replay_build_feature_cache.py` CLI
- `historical_full_pipeline_replay.py` NFL branch + `sgo_nfl_full_pipeline_replay`
- NFL model adapter / analytical baseline
- Grid optimizer NFL wiring

**Production safety: NFL is NOT wired into any tier and NOT on the
production board. Research/backtest only.**

### 2026-05-23 — Memory architecture fix (OOM-kill root cause)
**Root cause confirmed:** `optimizer.py` was accumulating every cell
result in `state["results"]` (a Python list living inside the uvicorn
process). On a 5-month MLB sweep that's 200k+ dicts → uvicorn RSS
ballooned to 4.3 GB, mongod's default WiredTiger cache (50% of RAM)
fought for the same pages, kernel OOM-killer triggered.

**Fixes shipped:**
1. **Stream-to-Mongo optimizer.** Removed `state["results"]` entirely.
   Cell rows now go straight to a new `optimizer_run_results` collection
   via batched `insert_many` (flush at 500 rows or every 5 s).
   Per-cell top-K cap (200) bounds total write volume. New compound
   index `(run_id, score desc)` so reads stay $sort+$limit on the
   server.
2. **API endpoints read from Mongo.** `/optimizer/{run_id}/results`
   is now paginated (`offset` + `limit`, hard-capped at 500/req) and
   the best_by_* maps run as a Mongo aggregation. Same for
   `/save_as_candidates`. Uvicorn never materialises the full set.
3. **Cancel via Mongo flag.** `/optimizer/{run_id}/cancel` writes
   `cancelled=true` on the run doc; the worker polls between cells.
   Removes the last reason uvicorn needed `_RUNS[run_id]` populated.
4. **Failures cap.** `state["failures"]` bounded by `MAX_INLINE_FAILURES
   = 50` so degenerate sweeps cannot grow it without limit.
5. **Testing-mode kill switch.**
   - Env: `TESTING_MODE=1` keeps APScheduler from ever starting.
   - Runtime: `POST /api/emergent-admin/worker/testing-mode {enabled}`
     pauses/resumes APScheduler in-process — no restart needed.
     `GET` returns current state (`running` / `paused` / `stopped`).
   - When paused, every SGO pull, recompute, and delta job is
     suspended; queued jobs survive intact for when you flip it back.
6. **WiredTiger cache capped at 1 GB.** `/etc/supervisor/conf.d/
   supervisord.conf` mongod command line now passes
   `--wiredTigerCacheSizeGB 1`. Default on this shared pod was
   ballooning past 4 GB.

**Verified memory impact (after restart):**
- backend uvicorn RSS:   4.3 GB → **142 MB**   (30× reduction)
- mongod RSS:            ≈3 GB  → **1.2 GB**
- available RAM:         8 GB   → **12 GB**

**Tests:** 58/58 still pass (10 contract + 32 research/worker + 16 NFL).


### 2026-05-23 — Half-migrated worker stall + uvicorn growth after Odds exhaustion
Two distinct issues bundled together:

**Issue A: jobs enqueued but never consumed.**
Root cause: the prod host never picked up `research_worker.conf`; the
service simply wasn't installed. The API kept happily enqueueing jobs
that nobody would ever execute, with no signal to the operator.

Fixes:
1. `workers/queue.py:enqueue(..., require_worker=True)` (default) refuses
   to enqueue when `/tmp/research_worker.heartbeat` is missing or older
   than `RW_STALE_AFTER_S` (30 s). Returns HTTP 503 with a fix recipe
   pointing at the installer. No more silent pile-up.
2. `/app/scripts/install_research_worker.sh` — idempotent one-command
   installer. Writes the supervisor conf, runs `reread`/`update`/`start`,
   waits up to 10 s for the heartbeat, fails loudly on timeout. Safe to
   re-run.
3. Two new tests pin the guard:
   - `test_enqueue_refused_when_no_worker_heartbeat` — missing file → 503
   - `test_enqueue_succeeds_when_heartbeat_fresh` — present + fresh → ok

**Issue B: uvicorn still ballooning despite TESTING_MODE.**
Root cause: many live-sync loops are spawned as direct
`asyncio.create_task` outside APScheduler. `TESTING_MODE` was only
pausing APScheduler, so these task-based loops kept hitting the Odds
API, retrying after rate-limit, and growing memory unboundedly.

Fixes (all in `server.py` startup):
1. `GameLockEngine.start()` — gated on `TESTING_MODE`.
2. `AdaptiveSync.start()` — gated on `TESTING_MODE`.
3. `InjurySensor.start()` — gated on `TESTING_MODE`.
4. `injury_triggered_rescore.get_rescore_service().start()` — gated.
5. `GameClockWatcher.start()` — gated.
6. `check_and_run_initial_sync` create_task — gated.

Now `TESTING_MODE=1` (env) or `POST /worker/testing-mode {enabled:true}`
(runtime) suspends EVERY background loop, not just APScheduler.

**Operator runbook to bring prod back online:**

```bash
# 1. Install / repair the worker (idempotent)
sudo bash /app/scripts/install_research_worker.sh

# 2. Verify
supervisorctl status research_worker
curl -sS http://127.0.0.1:8001/api/emergent-admin/worker/health \
     -H "X-Admin-Token: $EMERGENT_ADMIN_TOKEN" | jq .worker

# 3. Pause live sync during heavy research
curl -sS -X POST http://127.0.0.1:8001/api/emergent-admin/worker/testing-mode \
     -H "X-Admin-Token: $EMERGENT_ADMIN_TOKEN" \
     -H "Content-Type: application/json" -d '{"enabled":true}'

# 4. Re-enable when done
curl -sS -X POST http://127.0.0.1:8001/api/emergent-admin/worker/testing-mode \
     -H "X-Admin-Token: $EMERGENT_ADMIN_TOKEN" \
     -H "Content-Type: application/json" -d '{"enabled":false}'
```

**Tests:** 60/60 backend tests pass (10 contract + 16 NFL + 34
research/worker + 2 new for orphan guard).


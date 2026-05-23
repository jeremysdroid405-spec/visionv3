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

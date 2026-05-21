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
- Emergent Admin API: `/app/backend/routes/emergent_admin/` — token-gated
  REST surface that spawns allowlisted Python scripts as background subprocs
- SSOT historical replay pipeline:
  `scripts/sgo/historical_full_pipeline_replay.py` delegating to
  `production_replay_runner`
- Quant Terminal UI: `/app/frontend/src/pages/AdminTesting.jsx` (~2k lines)

## Key DB Collections
- `sgo_player_stats` — cached SGO events (PROTECTED read)
- `sgo_propvision_full_pipeline_replay` — main replay results
- `sgo_propvision_full_pipeline_replay_diff` — SSOT audit output
- `candidate_thresholds` — Auto-Optimizer saved configs
- `emergent_admin_jobs` — job status / exit codes / captured stdout

## What's been implemented
- 2026-05 — Quant Terminal at `/admin/testing` (sweeps, replay, results,
  coverage, optimizer, deploy)
- 2026-05 — `emergent_admin` backend ecosystem: jobs, optimizer, preflight,
  coverage, model registry, deployments
- 2026-05 — SSOT refactor of historical replay
- 2026-05 — Local Replay Warehouse offline coverage modes
- 2026-05 — Cache-first SGO stats ingest (no redundant external API calls)
- 2026-05 — Job runner persists stdout/stderr to Mongo; `tail_preview`,
  `traceback`, per-job `error` fields
- 2026-05-21 — Job runner silent-failure fix:
  - `_run_job` wrapped in outer try/except (pre-spawn exceptions now reach DB)
  - `_backend_cwd()` 4-level fallback (no more "queued" forever from missing cwd)
  - `_RUNNER_TASKS` strong-ref set prevents asyncio GC
  - `/jobs/_self_test` diagnostic endpoint
  - `/jobs/_reconcile_stuck` operator recovery endpoint
  - Verified locally: full queue→running→succeeded lifecycle + reconcile flow

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
  (ResultsTab, WorkflowTab, OptimizerTab)

### P2
- Audit `points` stat_family anomaly on MLB props

## Critical Notes
- DO NOT run heavy MLB historical sweeps locally — pod OOM. Use mocked/seeded data.
- React frontend changes require pulling AND `yarn build` on the prod host.
- `EMERGENT_ADMIN_TOKEN` lives in `backend/.env` for Admin API testing.

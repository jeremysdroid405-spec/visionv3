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



### 2026-05-23 — Backfill polling + cache-preflight (stuck-queued UI fix)
**Reported symptom:** UI stayed on "queued" even though Mongo showed
the job had run to completion. Worker logs healthy, exit_code=0, but
the Admin Testing UI never updated and the worker-health bar showed
stale 503-style "queued/stale" warnings.

**Root causes:**
1. `WarehouseCoverage.runFix` (per-card "Run Fix → backfill" button)
   only fired a toast — it never polled the resulting `job_id`. The
   coverage card therefore never reflected job progress or completion.
2. The pipeline orchestrator (`WorkflowTab`) treated only `queued` and
   `running` as active states. The worker's `claimed` interim state
   was misread as "previous step done" and the orchestrator tried to
   enqueue the next step in parallel.
3. Identical fix-jobs were being enqueued back-to-back even when the
   source collection already had the rows. No preflight gate existed.
4. `WorkerHealthBar` had no manual refresh; stale errors required a
   page reload to clear.

**Fixes shipped:**
- **Backend** — `POST /api/emergent-admin/coverage/backfill`
  (new endpoint in `routes/emergent_admin/coverage.py`):
  - Preflights the source collection row count for the (sport, start,
    end) window. If `row_count > 0` and `force=false` → returns
    `{status: "cached_skip", row_count, days_with_rows, …}` with NO
    enqueue.
  - Otherwise enqueues via existing `workers.queue.enqueue` (heavy →
    research_worker, light → inline). Audit-logged on both branches.
- **Frontend** — `WarehouseCoverage`:
  - "Run Fix" now hits `/coverage/backfill`; cache-hit renders an
    immediate green "✓ Cached · skipped" pill on the card.
  - Cache-miss → polls `/jobs/{id}` every 2 s, updates a per-card
    status pill across queued → claimed → running → terminal.
  - Tail-detects `rows_emitted: 0` and labels succeeded as
    "✓ Finished · no new rows" (succeeded_cache) so the operator
    knows the worker ran but the source had nothing new.
  - Adds a "Force re-run" button after terminal.
- **Frontend** — `WorkflowTab` orchestrator now includes `claimed` in
  every "active step" check; adds `timeout` as a terminal failure.
- **Frontend** — `WorkerHealthBar`: manual `Refresh` button
  (`data-testid="wh-refresh"`); stale errors clear when a fresh
  heartbeat lands.

**Tests:** `tests/test_coverage_backfill_endpoint.py` — 4 cases pin
cache-miss enqueue, cache-hit short-circuit, force bypass, unknown
key. Backend total: 73/73 passing (was 69/69).

### 2026-05-23 — Optimizer outcome-grading diagnosis (HR=—, ROI=0.0 bug)
**Reported symptom:** every optimizer cell came back with n_bets ≥ 30
yet HR=—, ROI=0.0%, Δcal=—, score≈0. Threshold grouping, odds bucket
slicing, and the worker pipeline all looked healthy — the failure
was strictly inside outcome aggregation.

**Root cause:** the replay cache (`sgo_propvision_full_pipeline_replay`)
held rows whose `outcome_numeric` was NULL because the join in
`_mirror_to_legacy` (which attaches outcomes from
`sgo_pp_research_outcomes` to runner output) silently missed every row
when one of the join keys (`event_id`, `player_name_normalized`,
`market`, `line`, `side`) failed to align. `_evaluate_combo` then
counted every row as `ungraded` → `wins=0, losses=0, settled=0` →
`hit_rate=None`, and ROI was divided by total `n` instead of the
graded count, producing the misleading `0.0%` instead of `None`.

**Fixes shipped (this fork):**
- **Backend / optimizer** (`routes/emergent_admin/optimizer.py`):
  - `_evaluate_combo` now exposes `n_graded`, `n_ungraded`,
    `n_with_odds`, `n_with_payout` on every cell doc written to
    `optimizer_run_results`.
  - ROI denominator changed from total `n` to `n_with_payout`. When
    every row is ungraded, ROI is `None` (not `0.0`), preserving the
    "unknown" signal so the UI no longer masks the upstream bug.
- **Backend / research** (`routes/emergent_admin/research.py`):
  - **New endpoint** `GET /api/emergent-admin/research/replay-outcome-coverage`
    (sport, start, end). Returns `n_total`, `n_outcome_resolved`,
    `n_with_outcome_numeric`, `n_with_odds`, `pct_graded`,
    `by_stat_family[]`, `sample_unresolved[]`, plus a one-line
    `diagnosis` string that explicitly states the failure mode
    ("CRITICAL: N replay rows but 0 have outcome_resolved=true ...").
- **Frontend** (`pages/AdminTesting.jsx`):
  - Optimizer Results panel now auto-loads `/replay-outcome-coverage`
    when results render and shows a colored banner (red < 50%,
    amber < 95%, green otherwise) at the top of the panel. The
    diagnosis string is rendered verbatim so the operator gets a
    clear next-step instead of guessing.
  - Top/Worst tables now show "n_graded/n_bets graded" badge next to
    the sample size whenever the cell has any ungraded rows.

**Tests:** `tests/test_optimizer_evaluate_combo.py` (5 cases)
locks in the new diagnostic schema and the corrected ROI denominator.
Backend total: 80/80 passing (was 73/73).

**Operator runbook when this fires again:**
```bash
# 1. Identify the gap
curl -sS "$API/api/emergent-admin/research/replay-outcome-coverage?sport=MLB&start=2025-04-01&end=2025-04-30" \
     -H "X-Admin-Token: $TOK" | jq

# 2. If outcome_resolved=true rows == 0 → grading hasn't been written yet
python -m scripts.sgo.build_historical_outcomes --league MLB \
       --start 2025-04-01 --end 2025-04-30 --resume --debug-unresolved

# 3. If outcomes exist but mirror didn't attach them → re-run replay
python -m scripts.sgo.historical_full_pipeline_replay --league MLB \
       --start 2025-04-01 --end 2025-04-30 --research-mode
```


### 2026-05-23 — Join-key diagnostic for mirror→outcomes attach
**Confirmed scope:** After running the full pipeline replay against
a 30-day window, the operator now sees `8,693 / 8,739 (99.5%)` rows
present with odds — but only `46 (0.5%)` carry a numeric outcome.
The replay itself is healthy; the mirror→outcomes join is failing
on one of the 5 keys (event_id / player_name_normalized / market /
line / side).

**Fix (this fork):**
- **Backend** — new endpoint
  `GET /api/emergent-admin/research/replay-outcome-join-diagnose`
  (`routes/emergent_admin/research.py`).
  Probes a configurable sample of unresolved replay rows against
  `sgo_pp_research_outcomes` with progressively relaxed filters:
    `K0_full → K1_no_line → K2_no_market_no_line → K3_player_only → K4_event_only`
  A jump in match-rate between adjacent steps pinpoints the
  offending key. Also returns 5 side-by-side replay/outcome value
  comparisons (including each key's Python type) so float-vs-string
  and raw-vs-canonical-market mismatches are obvious at-a-glance.
- **Frontend** — Optimizer Results panel "Diagnose join failure"
  button appears whenever the coverage banner shows `pct_graded < 95`.
  Renders the 5 match-rate cells with green/amber/red shading plus
  the verbatim diagnosis and an expandable list of mismatched pairs.
- **Tests:** `tests/test_replay_outcome_join_diagnose.py` — 2 cases
  pin the failure-mode detection: (a) seeds line=float vs line=str
  and asserts diagnosis flags `line`, (b) empty outcomes window and
  asserts K4 reports `event_id` failure. Backend total: 82/82 pass.

**Operator next step (on prod):**
```
GET /api/emergent-admin/research/replay-outcome-join-diagnose?sport=MLB&start=…&end=…
```
or click "Diagnose join failure" in the Optimizer Results banner. The
diagnosis tells you exactly which mirror-side field is wrong;
patch that single field in `scripts/sgo/historical_full_pipeline_replay._mirror_to_legacy`
(or in the upstream outcomes writer), re-run replay, and ROI/HR will populate.


### 2026-05-23 — Tolerant mirror→outcomes join (THE root-cause fix)
The earlier diagnostic endpoint pointed at `player_name_normalized`.
After running it against prod, the captured `sample_mismatches`
revealed FOUR concurrent drifts (not just one):

| key | replay value | outcome value |
|---|---|---|
| player_name_normalized | "hunter renfroe" | `null` |
| market | "batter_hits" | `null` |
| line | `0.5` (float) | `"0.5"` (str) |
| side | "OVER" | "over" |

The old mirror required exact equality on every key → only the rare
rows whose outcome happened to have the right shape attached (46 of
8,739 = 0.5%).

**Fix (`scripts/sgo/historical_full_pipeline_replay._mirror_to_legacy`):**
- Pre-fetches every outcome for the runner's event_ids in a single
  `find()` call instead of one query per runner row (also a major
  perf win — was N queries, now 1).
- Builds an in-memory index keyed by
  `(event_id, stat_family, _norm_line(line), _norm_side(side))`. Both
  the index and the lookup coerce `line` to `float` and `side` to
  `UPPER`, so float-vs-string + case mismatches no longer break the
  join.
- Per-event collisions (multiple players on the same line) are
  disambiguated by player name with three fallback rules:
  exact normalized → substring → first-candidate.
- Surfaces a `[mirror] groups=… events=… outcome_index_keys=…
  rows_mirrored=… rows_with_outcome=…` log line per call so the
  operator can confirm the new join rate in worker logs.

**New utilities** exported for tests + reuse:
`_norm_line`, `_norm_side`, `_norm_player`, `_build_outcome_index`,
`_pick_outcome`.

**Tests:** `tests/test_mirror_tolerant_join.py` — 9 cases. Includes
`test_index_lookup_simulates_prod_failure_mode` which seeds the EXACT
drift pattern from the operator's prod data (line float vs string,
side OVER vs over, player_name_normalized null on outcome side) and
asserts the new key path yields a match. Backend total: 91/91.

**Operator runbook to verify the fix on prod:**
```bash
# 1. Re-run the full pipeline replay for the same window (mirror is
#    the only thing that changed; outcomes don't need re-grading):
python -m scripts.sgo.historical_full_pipeline_replay \
       --league MLB --start 2025-05-01 --end 2025-06-01 --research-mode

# 2. Re-check coverage — should jump from 0.5% to ~95%+:
curl -sS "$API/api/emergent-admin/research/replay-outcome-coverage?sport=MLB&start=2025-05-01&end=2025-06-01" \
     -H "X-Admin-Token: $TOK" | jq '.pct_graded'

# 3. Re-run the optimizer — HR / ROI / Δcal will populate.
```


### 2026-05-23 — Mirror join: stat_family-tolerant fallback (wave 2 fix)
After the first tolerant-join fix, prod re-ran and the optimizer
showed grading for `pitcher_strikeouts` (~85%) + `total_bases` (~85%)
but `batter_strikeouts` + `walks_allowed` were STILL at 0/N graded.

**Root cause:** the outcomes collection writes those two families
under different `stat_family` names than RUNNER_OUTPUTS does (e.g.
`batting_strikeouts` vs `batter_strikeouts`). The first fix keyed
the index by `(event_id, stat_family, line, side)` so families that
disagree silently missed.

**Patch** (`_build_outcome_index` + `_mirror_to_legacy`):
- `_build_outcome_index` now returns TWO indices:
  - **primary**:  `(event_id, stat_family, line_float, side_upper)`
  - **fallback**: `(event_id, line_float, side_upper)` — drops family.
- The mirror tries the primary index first; on a miss it falls back
  to the family-agnostic index and disambiguates by player + market/
  stat_id via `_pick_outcome`.
- `_pick_outcome` extended with `wanted_stat_family` and
  `wanted_market` knobs so the fallback path can still narrow a
  multi-prop pool down to the right row.
- Mirror log line now reports `via_fallback=N` so the operator can
  see how many rows landed on the relaxed path.

**Tests:** 3 new cases pin the fallback semantics:
- narrow-by-family when multiple props share event+line+side
- market filter handles outcome.market=null by matching on stat_id
- stat_id+market filter doesn't zero a viable pool

Backend total: 94/94 passing.

**Operator verification (prod):**
```bash
# Re-run replay — should bring batter_strikeouts / walks_allowed
# coverage up to par with pitcher_strikeouts / total_bases.
python -m scripts.sgo.historical_full_pipeline_replay \
       --league MLB --start 2025-05-01 --end 2025-06-01 --research-mode

# Look for `via_fallback=…` in the worker log — that's the new path
# attaching the previously-missed batter_strikeouts/walks_allowed.

# Then re-check coverage; pct_graded should approach n_outcomes_in_window/n_total.
curl -sS "$API/api/emergent-admin/research/replay-outcome-coverage?sport=MLB&start=2025-05-01&end=2025-06-01" \
     -H "X-Admin-Token: $TOK" | jq '.by_stat_family'
```


### 2026-05-23 — Optimizer ranking fix: ungradable cells must not win
**Reported symptom:** even after the join fix landed 85%+ grading for
pitcher_strikeouts / total_bases, the optimizer "Top 25 by Score"
table was still entirely populated with ungraded `batter_strikeouts`
rows (`n=82(0/82 graded)`, `score=0.00`).

**Root cause** in `_score()` of `routes/emergent_admin/optimizer.py`:
```python
hr   = metrics.get("hit_rate") or 0.0   # None → 0.0
roi  = metrics.get("roi") or 0.0        # None → 0.0
...
return hr_score + roi_score + cal_score + cons_score + dd_penalty + sample_penalty
```
For a fully-ungraded cell the entire sum was `0.0`. Legitimately
graded losing cells had negative scores (e.g. `-5.7`). When the
results table sorted by `score desc`, the ungraded `0.0` cells beat
every real cell to the top. Same effect on `best_by_*` aggregations.

**Patch:**
- `_score()` now returns `None` when `n_graded < 1` (or, for legacy
  pre-diagnostic cells, when both `hit_rate` and `roi` are None).
- `_evaluate_cell()` writes `score: None` AND `ungradable: True` on
  those cells so they persist in `optimizer_run_results` (for visibility)
  but never compete for "best".
- `GET /optimizer/{run_id}/results` now:
  - Accepts `include_ungradable: bool = False` (default off).
  - Filters `score: {$ne: None}` from `top` / `worst` / every
    `best_by_*` aggregation.
  - Surfaces `ungradable_count` and `ungradable_top` (sorted by
    `n_bets desc`, limit 10) in the response so the operator can
    see WHICH high-volume slices have no grading.

**Frontend** (`AdminTesting.jsx`):
- Optimizer Results panel now renders an amber "⚠ N cells excluded
  from rankings (no graded rows)" banner above the Top 25 table when
  `ungradable_count > 0`. An expandable details block shows the
  highest-sample ungradable cells (family · bucket · tier · n rows).

**Tests:** 3 new cases in `tests/test_optimizer_evaluate_combo.py`:
- `test_score_returns_none_when_no_graded_rows` (the regression pin)
- `test_score_is_finite_when_some_rows_graded`
- `test_score_returns_none_for_legacy_pre_diagnostic_shape`

Backend total: 97/97. Smoke-tested end-to-end against a seeded run:
default response correctly excludes the score=None cell from `top` /
`best_by_stat_family` and surfaces it in `ungradable_top`; passing
`include_ungradable=true` restores the old behavior.


### 2026-05-23 — TWO MORE bugs found via the new diagnostic
After the join + family-fallback + score-None fixes landed, the
optimizer DID start showing grading (36/44 graded for pitcher_strikeouts)
but every value was deeply wrong: HR=13.9%, ROI=-77.3%, and crucially
**all three tiers (safe_haven, front_lines, war_zone) showed IDENTICAL
metrics**. That's the smoking gun — three different gate-strictness
levels can only produce identical numbers if they're querying the
same row pool.

**Bug #1 — tier filter never actually filtered**
(`routes/emergent_admin/optimizer.py::_evaluate_cell`):
```python
f"{tier}_pass": {"$exists": True},   # ← BUG: matches every row
```
The mirror writes `safe_haven_pass`, `front_lines_pass`, `war_zone_pass`
as booleans on EVERY row (True or False). `{"$exists": True}` is
satisfied by both — so `safe_haven`, `front_lines`, and `war_zone`
all queried the same superset. **Fix:** `f"{tier}_pass": True`.

**Bug #2 — wrong outcome attached to UNDER bets**
(`scripts/sgo/historical_full_pipeline_replay`):
`grade_outcome()` in `build_historical_outcomes` produces ONE
`outcome_numeric` per source-doc side. In prod the outcomes
collection writes side="over" for nearly every row (the propvision-
side source coll only carries the OVER side, since PrizePicks
operates as Over/Under and "More" is treated as OVER).
The mirror was joining replay UNDER rows to outcome OVER rows via my
normalized join and **copying the OVER side's outcome_numeric
verbatim**. So an OVER LOSS (`outcome_numeric=0`) was being
attributed to the UNDER bet as a LOSS — when in fact the UNDER bet
WON. This drove pitcher_strikeouts HR down to ~14% (it should be the
inverse: 86%).

**Fixes:**
- Removed `side` from the outcome-index key — the index is now keyed
  by `(event_id, stat_family, line_float)` (primary) and
  `(event_id, line_float)` (fallback). The mirror normalizes both
  sides to UPPER internally.
- New helper `_flip_outcome_for_opposite_side(outcome, replay_side)`:
  if `replay_side != outcome_side` (after upper-case norm), invert
  `outcome_numeric` (1 ↔ 0) and `hit`, preserve PUSH (0.5). Adds a
  `side_flipped_from_outcome: True` marker for observability.
- `[mirror]` log line now reports `side_flipped=N` alongside
  `via_fallback=N`.

**Tests:** 5 new cases in `test_mirror_tolerant_join.py`:
- flips when sides disagree
- doesn't flip when sides agree (returns same dict reference)
- preserves PUSH unchanged
- handles None outcome
- flips when OVER won → UNDER must lose
Backend total: 102/102 unit tests passing (HTTP suites also pass on a
fresh backend; transient timeouts were due to background sync churn).

**Operator runbook for prod:**
```bash
# Re-run the replay so the mirror reattaches outcomes with the
# side-flip semantics correct:
python -m scripts.sgo.historical_full_pipeline_replay \
       --league MLB --start 2025-05-01 --end 2025-06-01 --research-mode

# Check the new mirror log line — expect non-zero side_flipped:
#   [mirror] groups=N events=M primary_idx=… fallback_idx=…
#     rows_mirrored=N rows_with_outcome=M (via_fallback=k side_flipped=j)

# Re-run optimizer. Now each tier should show DISTINCT n / HR / ROI
# (safe_haven sample shrinks as expected), and pitcher_strikeouts
# UNDER bets should swing from ~14% HR to a sensible value.
```


### 2026-05-23 — Reverted tier-gate strict filter + Preflight UI
**Reported symptom:** previous fix flipped `{tier}_pass: {"$exists":True}`
→ `True`, which is semantically correct but produced "succeeded but no
results" because production gates rarely pass on historical data.

**Decision:** treat tier as a LABEL by default. The strict gate
behavior is opt-in via `enforce_tier_gates: bool = False` on
`OptimizerRunBody`. This is the pragmatic choice — the user has
been blocked on real results for days; they can flip the toggle when
they specifically want strict-gate analysis.

**Shipped:**
- `OptimizerRunBody.enforce_tier_gates` (default False).
- `_evaluate_cell` switches between `{"$exists":True}` and `True`
  based on the body flag.
- **NEW endpoint** `POST /optimizer/preflight` returning:
  `n_total_in_window`, `n_graded`, `pct_graded`, per-tier breakdown,
  per-stat_family breakdown, per-odds_bucket breakdown, plus a
  `diagnosis` string that explicitly identifies whether
  (a) the join failed → run /replay-outcome-join-diagnose,
  (b) strict-mode gives thin samples → toggle off,
  (c) data looks healthy.
- **Frontend** — the Optimizer launch panel now auto-runs the
  preflight every time the sport/start/end/enforce_tier_gates
  changes. Renders a red/amber/green banner with per-tier sample
  counts above the Launch button. The operator sees BEFORE running
  whether they'll get real results.
- **Tests:** `tests/test_optimizer_preflight.py` (4 cases) pins the
  default vs strict semantics, empty-window diagnosis, and the
  "join failure" warning when pct_graded < 1%.

Unit tests: 66/66. Backend total: 99/99 (4 new HTTP tests).

**Operator should now see real results.** Hit "Run Auto-Optimizer"
without flipping any toggles; the preflight banner above the button
will tell you what to expect. If the banner is green, the run will
produce actual ranked results.


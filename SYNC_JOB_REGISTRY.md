# SYNC_JOB_REGISTRY

Source-of-truth catalogue of every PropVision sync job. Updated 2026-04-28
as part of the Sync Hardening rollout.

Lock key conventions live in `services/sync_lock.py` and follow the
pattern `<scope>:<sport>` (e.g. `sync:nba`, `lineup:mlb`, `grade:nba`).
Lock holders advisory; voluntary acquisition by writers.

Status legend:
- `LIVE` — actively scheduled and running
- `MIGRATE` — should be moved into APScheduler (Phase 4 follow-up)
- `LEGACY` — kept available but explicitly deprecated; planned removal

---

## In-process APScheduler jobs (server.py)

| Job ID | Schedule | Sport | Purpose | Lock key | Collections touched | Expected runtime | Failure behavior | Status |
|---|---|---|---|---|---|---|---|---|
| `hourly_nba_master_sync` | every 60 m | nba | Universal master sync (odds + hydrate + recompute) | `sync:nba` (via RebuildCoordinator → UpstreamSyncLock) | `nba_live_props`, `nba_prop_scores`, `dg_cached_board` | 30–90 s | logs + `sync_history` row | LIVE |
| `hourly_mlb_master_sync` | every 60 m | mlb | Universal master sync | `sync:mlb` | `mlb_live_props`, `mlb_prop_scores`, `dg_cached_board` | 30–90 s | logs + `sync_history` row | LIVE |
| `nba_l5l10_batch_{1..5}` | 04:00–04:08 ET | nba | NBA L5/L10 BDL stat batches | (none — read-only DB writes to per-batch caches) | `bdl_l5_cache`, `bdl_l10_cache` | 5–30 s ea | logs | LIVE |
| `bdl_game_values_sync` | 04:10 ET | nba | BDL fantasy-game values | (none) | `bdl_game_values_*` | ~60 s | logs | LIVE |
| `mlb_bdl_game_values_sync` | 04:13 ET | mlb | BDL fantasy-game values | (none) | `bdl_game_values_*` | ~60 s | logs | LIVE |
| `bdl_game_logs_sync` | 04:15 ET | nba | BDL game-log batched sync | (none) | `bdl_historical_game_logs` | 5–10 m | logs | LIVE |
| `mlb_bdl_game_logs_sync` | 04:18 ET | mlb | BDL game-log batched sync | (none) | `bdl_historical_game_logs` | 5–10 m | logs | LIVE |
| `daily_hard_refresh` | 04:20 ET | nba | NBA full daily pipeline | `sync:nba` | NBA hub + props + scores | 1–3 m | logs | LIVE |
| `mlb_daily_refresh` | 04:23 ET | mlb | MLB full daily pipeline | `sync:mlb` | MLB hub + props + scores | 1–3 m | logs | LIVE |
| `ticker_sync` | 04:26 ET | both | News + games ticker | (none) | `news_ticker_*` | ~30 s | logs | LIVE |
| `pra_audit_settle` | 04:30 ET | nba | NBA PRA projection audit settle | (none — read prop_scores) | `nba_pra_projection_audit` | ~30 s | logs | LIVE |
| `mlb_daily_pipeline` | 04:00 UTC | mlb | Phase-4 migrated daily MLB pipeline (lineup + statcast + features + identity + validate + score) | **`sync:mlb`** | `mlb_projected_lineups`, `mlb_statcast_*`, `mlb_player_identity_map`, `mlb_pick_history`, `sync_history` | 5–15 m | logs + `sync_history` row + skip-on-busy | LIVE |
| `mlb_lineups_early` | 18:00 UTC | mlb | Phase-4 migrated pregame lineup ingest (early window) | **`lineup:mlb`** | `mlb_projected_lineups`, `mlb_live_props` (lineup fields), `sync_history` | <1 m | skip-on-busy | LIVE |
| `mlb_lineups_final` | 22:00 UTC | mlb | Phase-4 migrated pregame lineup ingest (final window) | **`lineup:mlb`** | `mlb_projected_lineups`, `mlb_live_props` (lineup fields), `sync_history` | <1 m | skip-on-busy | LIVE |
| `mlb_pick_grade` | 05:00 UTC | mlb | Grade unsettled MLB picks against actuals | **`grade:mlb`** | `mlb_pick_history`, `sync_history` | 30–60 s | skip-on-busy | LIVE |
| `weekly_roster_sync` | Sun 00:00 UTC | both | Master-hub roster refresh | (none) | `*_master_hub_2026` | 5–10 m | logs | LIVE |
| `forward_test_capture` | 18:30 ET | both | Forward-test snapshot capture | (none — read-only mostly) | `forward_test_snapshots` | ~30 s | logs | LIVE |
| `hourly_badge_sync` | 60 m | nba | Player badges | (none) | `nba_player_badges` | ~30 s | logs | LIVE |
| `hourly_injury_sync` | 60 m | both | Injury aggregation (BDL + ESPN + NBA Official) | (none) | `injuries_normalized`, `live_injuries` | ~30 s | logs | LIVE |
| `live_injury_check` | 5 m | both | Multi-source injury micro-sync | (none) | `injuries_normalized`, `live_injuries` | 5–15 s | logs | LIVE |
| `half_hourly_social_sync` | 30 m | both | Social posts | (none) | `social_posts_cache` | ~30 s | logs | LIVE |
| `hourly_referee_sync` | 60 m | nba | Referee feed | (none) | `nba_referee_data` | ~30 s | logs | LIVE |
| `universal_game_start_scanner` | 60 s | both | Flips `active=False` on tipped-off props | (none — per-row update) | `nba_live_props`, `mlb_live_props` | 1–3 s | logs | LIVE |
| `shadow_divergence_monitor` | 60 s | both | Shadow vs live divergence | (none — read-only) | `shadow_divergence_log` | <1 s | logs | LIVE |
| AdaptiveSync poll loop | adaptive 4 h ↔ 10 m | both | Adaptive odds polling | `sync:{sport}` (via callback) | `{sport}_live_props`, `dg_cached_board` | 30–60 s | logs | LIVE |
| DeltaEngine ticks | 20 s NBA / 30 s MLB | both | Set-diff detection + targeted rescore | `recompute:{sport}` (gated by `UpstreamLockGateStep` short-circuit when `sync:{sport}` held) | `{sport}_prop_scores` | 1–2 s | logs | LIVE |
| InjuryWatcher | 120 s | both | Injury-change detection | (none) | (publishes events) | <1 s | logs | LIVE |
| GameClockWatcher | 300 s | both | Game-clock state tracking | (none) | `game_clock_state` | <1 s | logs | LIVE |

## Host-cron / shell jobs (NOT in APScheduler)

> ✅ **Phase 4 complete (2026-04-28):** the MLB host-cron jobs have been
> migrated into APScheduler (see `services/scheduled/mlb_jobs.py`).
> The shell scripts below are kept as **manual rollback wrappers** only.
> Do NOT install in host crontab — they would race the in-process
> scheduler.

| Job ID | Schedule (former) | Sport | Purpose | Replacement APScheduler job |
|---|---|---|---|---|
| `run_mlb_daily_pipeline.sh` | 04:00 UTC | mlb | Statcast ingest + features + identity + scoring | `mlb_daily_pipeline` (lock=`sync:mlb`) |
| `run_mlb_pregame_lineups.sh` | 22:00 UTC | mlb | Lineup ingest + coverage monitor | `mlb_lineups_early` (18:00 UTC) + `mlb_lineups_final` (22:00 UTC), both lock=`lineup:mlb` |
| `mlb_propvision_total_bases.py --log-picks` | end of daily.sh | mlb | Score MLB TB + log picks | step 8 inside `mlb_daily_pipeline` |
| `update_mlb_pick_results.py` | manual | mlb | Grade unsettled MLB picks | `mlb_pick_grade` (05:00 UTC, lock=`grade:mlb`) |
| `update_nba_pick_results.py` | manual | nba | Grade unsettled NBA picks | (still manual; NBA grader not yet migrated — Phase 4b backlog) |

Manual debug entrypoints (these exist for ops, not for cron):
```bash
python -m services.scheduled.mlb_jobs daily
python -m services.scheduled.mlb_jobs lineups
python -m services.scheduled.mlb_jobs grade
```

## Admin-trigger routes (concurrency-protected as of 2026-04-28)

| Endpoint | Sport | Lock key | Notes |
|---|---|---|---|
| `POST /v3/ferrari/rebuild?sport={nba,mlb}` | both | `sync:{sport}` (via RebuildCoordinator → UpstreamSyncLock) | Re-routed through coordinator on 2026-04-28; previously bypassed all locks |
| `POST /v3/odds/sync?sport={nba,mlb}` | both | `odds:{sport}` (cross-process advisory) | Wraps `sync_sport_props` in `with_sync_lock`. 409 returned when busy |

## Deprecated / dead code

| Item | Status | Action |
|---|---|---|
| `services/cron_scheduler.start_scheduler()` | LEGACY | Never invoked — `server.py` uses its own APScheduler. Pending physical removal in Phase 10 |
| `master_sync.delete_many({})` pre-step in step 1 | REMOVED 2026-04-28 | Replaced by stage-then-prune inside `universal_odds_sync.sync_sport_props` |
| Pre-insert `delete_many({})` in `universal_odds_sync.sync_sport_props` | REMOVED 2026-04-28 | Replaced by `sync_batch_id` write + post-insert prune of non-current batches |

## Lock key registry

| Lock key | Owners | Acquire pattern |
|---|---|---|
| `sync:{sport}` | RebuildCoordinator → master_sync, AdaptiveSync callback, admin `/v3/ferrari/rebuild` | `UpstreamSyncLock.exclusive` (in-process). Recommended: also acquire `sync_lock.with_sync_lock("sync:{sport}")` so host-cron sees it |
| `odds:{sport}` | `/v3/odds/sync` (admin), AdaptiveSync direct path | `with_sync_lock` |
| `lineup:{sport}` | `_refresh_live_props` in `ingest_mlb_projected_lineups.py` | `with_sync_lock` |
| `recompute:{sport}` | (recommended; not yet wired into `recompute_sport`) | future |
| `grade:{sport}` | (recommended; not yet wired into pick result graders) | future |
| `context:{sport}` | (recommended; for ad-hoc hydrator runs) | future |

## Health observability

`GET /api/health/sync` aggregates all of the above into a single JSON
payload with `overall_status ∈ {healthy, warning, critical}` for
ops-dashboard polling. See `routes/health_sync.py` for probe details.

---

## Deferred phases (planned follow-ups)

| Phase | Scope | Justification for deferral |
|---|---|---|
| 4 — Migrate host-cron into APScheduler | Move all `MIGRATE`-tagged jobs into APScheduler with `sync_locks` integration | Cross-process advisory lock now protects host-cron from racing in-process; migration can land in next sprint without urgency |
| 5 — Universal Lineup Watcher | Refactor MLB lineup ingestor into a `BaseLineupWatcher` + `MLBLineupAdapter`/`NBALineupAdapter` shape under `services/watchers/lineup.py` | NBA lineup feed not yet identified; deferring until adapter contract is stabilised by a second sport |
| 6 — Universal Staleness Cleaner | Daily 08:00 UTC sweep of stale `{sport}_live_props` rows + retention prune of `mlb_projected_lineups` | Prune already happens incrementally inside the new stage-then-prune flow; standalone cleaner is reduced to retention-only and can land later |
| 8 — Sync Job Registry | This file | DONE in this PR |
| 10 — Cleanup / deprecation | Remove `cron_scheduler.start_scheduler()` definition, simplify legacy paths | Wait for one full week of green health-endpoint readings before pruning |

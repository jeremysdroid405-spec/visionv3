# Odds API Usage Audit — 2026-06-01

## Headline
The watcher (Adaptive Sync Engine) was calling **full** `sync_sport_props`
for BOTH `nba` and `mlb` every **240 seconds**, regardless of whether
anything actually changed. Each call hits the API once per upcoming
event (typically 8-20 events combined for NBA+MLB on a slate day) plus
1 events-discovery call + up to 3 markets-discovery probes per sport
(cached 1h). Compounded across multiple processes and game days, this
explains the ~548k calls/day reported on the dashboard.

## Where Odds API calls originate (full inventory)

### 1. `services/universal_odds_sync.py` — primary client
- `UniversalOddsSyncService.fetch_events()` — `GET /v4/sports/{sport_key}/events`
- `UniversalOddsSyncService.fetch_event_odds()` — `GET /v4/sports/{sport_key}/events/{event_id}/odds`
- `UniversalOddsSyncService.sync_sport_props()` — orchestrator that calls
  the two above in a loop over every event.

### 2. `services/market_catalog.py`
- `MarketCatalog.discover_event_markets()` — `GET /v4/sports/{sport_key}/events/{event_id}/markets`
- Called from `universal_odds_sync._resolve_markets_for_sport` (up to 3
  sample events / sport / sync, but **with 1h Mongo TTL cache so most
  calls are cache hits** — verified in logs:
  `[UNIVERSAL_ODDS] nba: market catalog cache HIT (114 markets, age=...s, ttl=3600s)`)

### 3. `services/team_live_sync_service.py`
- Reuses `UniversalOddsSyncService.fetch_event_odds` for team props.
- Manual trigger only: `POST /api/v3/team-live-sync/{sport}` —
  not on any scheduler. Safe.

### 4. `services/historical_odds_fetcher.py`
- Uses Odds API for historical backfills. Manual scripts only.
- Not invoked from runtime / watcher paths.

### 5. `services/data_scraper.py`, `services/sharp_edge_calculator.py`
- Legacy helpers. `grep` shows imports only, no live call paths from
  scheduler. Safe.

### 6. `services/engines/adaptive_sync_engine.py`
- Has its own `httpx.AsyncClient` for one specific lightweight call
  (scores/game_status probe — `_get_poll_interval` driver). The full
  PROP-odds fetch is delegated back to `universal_odds_sync` via the
  callback in `server.py:_adaptive_sync_callback`.

### 7. Manual / scheduled callers of `sync_sport_props` (audited)
| Caller                                                | Trigger             |
|-------------------------------------------------------|---------------------|
| `_adaptive_sync_callback` (server.py:1462)            | every 240s ← BUG    |
| `services/master_sync.py:run_master_sync` (line 136)  | hourly + manual     |
| `routes/ferrari_tiers.py:3096` (POST /v3/odds/sync)   | manual admin only   |
| `scripts/init_database.py:160` (bootstrap)            | one-time bootstrap  |

### 8. NO call paths from optimizer / replay / training
Verified by `grep -rn "fetch_event_odds\|sync_sport_props\|the-odds-api"`:
- `routes/emergent_admin/optimizer.py` — none (reads
  `sgo_propvision_full_pipeline_replay` only)
- `scripts/sgo/reshape_team_props_to_replay.py` — none
- `scripts/sgo/train_team_xgb.py` — none
- `services/team_xgb_loader.py` — none (loads local pickle artifacts only)
- All Quant Terminal endpoints — none

## Volume measurement (this pod's logs)

| Window                          | API calls captured        |
|---------------------------------|---------------------------|
| Current process (~2h 20min)     | 504                       |
| err.log.1 (~17h prior)          | 1,494                     |
| err.log.2 (older window)        | 1,702                     |
| err.log.3 (older window)        | 1,029                     |
| err.log.4 (older window)        | 1,866                     |
| err.log.5 (oldest captured)     | 2,551                     |
| **Sum across this pod's logs**  | **~9,150**                |

Production accounts may have higher rates if (a) the slate is larger or
(b) parallel pods/workers are running. The dashboard total **548 k** is
plausible only if 4-6 parallel processes are running and the slate is
loaded; otherwise it strongly implies the watcher is firing more often
than the 240s STANDBY interval (e.g., overlapping triggers on restart).

### Per-trigger cost breakdown (one `_adaptive_sync_callback` cycle)
For NBA + MLB combined with N events:
- 2 × `fetch_events` calls
- 0–6 `discover_event_markets` calls (cached 1h, usually 0)
- N × `fetch_event_odds` calls (where N ≈ 8–20 in season)
- **Total per cycle: ~10–22 calls every 240s = 150–330 calls/hour =
  3.6 k–7.9 k calls/day from the watcher alone.**

## Root causes

1. **Watcher does a FULL sync, not a delta.** `_adaptive_sync_callback`
   calls `sync_sport_props(sport)` directly — which is the full-board
   fetch. No per-event freshness tracking, no change detection.
2. **Both sports fire every tick.** Even when one sport has no slate
   change for hours, NBA+MLB are both re-synced.
3. **No call budget / safety guard.** No hard cap, no allow-list of
   callers, no kill switch.
4. **No per-caller logging.** `[ODDS_DIAG]` exists but doesn't tag
   `caller=` or `sync_mode=`, so triage requires log spelunking.
5. **Hourly master_sync cron also fires** for each sport in addition
   to the 240s watcher (a second 2-event-sync per hour).

## Confirmed CLEAN paths (no live Odds API)
- Optimizer (`/api/emergent-admin/optimizer/*`) — reads `sgo_propvision_full_pipeline_replay`.
- Replay/training (`scripts/sgo/*`, `services/team_xgb_loader.py`) — local artifacts + Mongo only.
- Frontend `/api` reads (`live_props`, `team_prop_scores`, etc.) — DB reads.
- BDL backfill scripts — separate API (BallDontLie), not Odds API.

## Fix being shipped (separate commit)

1. **`services/odds_api_budget.py`** — rolling-window counter, hard cap
   (`ODDS_API_MAX_CALLS_PER_HOUR`, default 500), per-call Mongo log
   (`odds_api_call_log`), kill switch (`ODDS_API_KILL_SWITCH=1`).
2. **Allow-list on `sync_sport_props`** — `caller` kwarg; only
   `startup`, `manual_admin`, `scheduled_cron` may run full sync.
   Watcher cannot.
3. **`sync_sport_props_delta(sport, caller)`** — fetches events list,
   checks per-event `last_synced_at` in new `odds_delta_state`
   collection, only refetches events with stale timestamps (`> DELTA_TTL_SECONDS`,
   default 600s), capped at `DELTA_MAX_EVENTS_PER_TICK` (default 3).
   Logs `sync_mode=delta`, trigger reason, scope, calls made, records
   updated.
4. **Watcher rewired** — `_adaptive_sync_callback` now calls the delta
   path instead of the full sync. Falls back to logged-error on any
   exception; never escalates to full sync.
5. **Endpoint** `GET /api/emergent-admin/odds-budget` — current
   hourly/daily count, breakdown by sport/endpoint/caller, last 50
   calls.
6. **Per-request log line** — every Odds API call now logs:
   `[ODDS_BUDGET] ts=... caller=... sport=... endpoint=... params={...}
    status=... hour_count=... budget=...`.

After deploy, watcher-triggered calls should drop from ~10–22 per 240s
cycle to ~1–3 per cycle when nothing has changed (typically just the
events-list ping), and `0–3` event_odds refetches when something *has*
changed. Expected hourly throughput: < 50 calls/hour from the watcher.

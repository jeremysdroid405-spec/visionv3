# Wave 0 — Batch 8 (Adapter / Writer / Sync Orchestration Plumbing) · Audit

**Scope**: 9 files routed through `COLL(...)` with zero behavior change.
**Priority concepts**: master_hub, master_roster, board_cache, live_props, odds_cache, events_cache, player_stats_agg, sync_log, context_flags, injuries, ticker_cache, ticker_headlines, breaking_news_cache, shared caches.
**Exclusions honored**: `_archive_mlb_v1/` not touched.
**Rules honored**: no renames · no data moves · no dual-writes · no logic/query changes · no helper utilities added.

---

## Files changed (9)
1. `backend/services/odds_api_service.py`
2. `backend/services/sync_service.py`
3. `backend/services/badge_resolver.py`
4. `backend/services/mlb_high_friction_model.py`
5. `backend/services/tier_builder_service.py`
6. `backend/services/ssot_data_layer.py`
7. `backend/services/sync_orchestration_service.py`
8. `backend/services/bdl_comprehensive_sync.py`
9. `backend/services/insights_sync_service.py`

---

## Exact literals removed → COLL(...) replacements added (18)

### `odds_api_service.py` — 2
| # | Line | Removed | Added |
|---|---:|---|---|
| 1 | 57 | `self.events_cache = db.dg_events_cache` | `self.events_cache = db[COLL("events_cache", "nba")]` |
| 2 | 58 | `self.odds_cache = db.dg_odds_cache` | `self.odds_cache = db[COLL("odds_cache", "nba")]` |

### `sync_service.py` — 2
| # | Line | Removed | Added |
|---|---:|---|---|
| 1 | 35 | `self.cached_board = db.dg_cached_board` | `self.cached_board = db[COLL("board_cache", "nba")]` |
| 2 | 41 | `self.sync_log = db.dg_sync_log` | `self.sync_log = db[COLL.shared("sync_log")]` |

### `badge_resolver.py` — 2
| # | Line | Removed | Added |
|---|---:|---|---|
| 1 | 170 | `self.context_engine = db.nba_context_engine` | `self.context_engine = db[COLL("context_flags", "nba")]` |
| 2 | 171 | `self.master_hub = db.nba_master_hub_2026` | `self.master_hub = db[COLL("master_hub", "nba")]` |

> Note: `context_flags` concept is registered with physical name `nba_context_engine` for NBA sport. Verified via `COLL("context_flags","nba") == "nba_context_engine"`.

### `mlb_high_friction_model.py` — 2
| # | Line | Removed | Added |
|---|---:|---|---|
| 1 | 165 | `self.master_hub = db.mlb_master_hub_2026` | `self.master_hub = db[COLL("master_hub", "mlb")]` |
| 2 | 167 | `self.live_props = db.mlb_live_props` | `self.live_props = db[COLL("live_props", "mlb")]` |

### `tier_builder_service.py` — 2
| # | Line | Removed | Added |
|---|---:|---|---|
| 1 | 78 | `self.sync_log = db.dg_sync_log` | `self.sync_log = db[COLL.shared("sync_log")]` |
| 2 | 109 | `self.db.nba_master_hub_2026.find(...)` | `self.db[COLL("master_hub", "nba")].find(...)` |

### `ssot_data_layer.py` — 2
| # | Line | Removed | Added |
|---|---:|---|---|
| 1 | 78 | `self.master_hub = db.nba_master_hub_2026` | `self.master_hub = db[COLL("master_hub", "nba")]` |
| 2 | 79 | `self.active_lines = db.dg_cached_board` | `self.active_lines = db[COLL("board_cache", "nba")]` |

### `sync_orchestration_service.py` — 2
| # | Line | Removed | Added |
|---|---:|---|---|
| 1 | 36 | `self.dg_cached_board = db.dg_cached_board` | `self.dg_cached_board = db[COLL("board_cache", "nba")]` |
| 2 | 40 | `self.sync_log = db.dg_sync_log` | `self.sync_log = db[COLL.shared("sync_log")]` |

### `bdl_comprehensive_sync.py` — 2
| # | Line | Removed | Added |
|---|---:|---|---|
| 1 | 102 | `self.master_hub = db.nba_master_hub_2026` | `self.master_hub = db[COLL("master_hub", "nba")]` |
| 2 | 1419 | `self.db.dg_cached_board.distinct("player_name")` | `self.db[COLL("board_cache", "nba")].distinct("player_name")` |

### `insights_sync_service.py` — 2
| # | Line | Removed | Added |
|---|---:|---|---|
| 1 | 35 | `self.cached_board = db.dg_cached_board` | `self.cached_board = db[COLL("board_cache", "nba")]` |
| 2 | 36 | `self.master_hub = db.nba_master_hub_2026` | `self.master_hub = db[COLL("master_hub", "nba")]` |

**Imports added** (9 files): `from services.config.collection_names import COLL`.

---

## Out-of-scope refs in these files (reported, left untouched)

| File:Line | Literal | Reason left |
|---|---|---|
| `sync_service.py:36–40` | `dg_radar_picks`, `dg_goblin_vault`, `dg_front_lines`, `dg_parlay_builder`, `dg_goblin_recon`, `dg_daily_insights` | Not in registry |
| `mlb_high_friction_model.py:166` | `self.historical_logs = db.mlb_historical_logs` | Not in registry |
| `tier_builder_service.py:71–75` | `dg_radar_picks`, `dg_goblin_vault`, `dg_front_lines`, `dg_parlay_builder`, `dg_goblin_recon` | Not in registry |
| `sync_orchestration_service.py:37,38,39` | `dg_verification_failures`, `dg_player_data`, `dg_trending` | Not in registry |
| `sync_orchestration_service.py:393` | `"photo_source": "nba_master_hub_2026"` (string label) | Response-value label, not DB access |
| `insights_sync_service.py:37` | `self.daily_insights = db.dg_daily_insights` | Not in registry |

All docstring / log-text / comment prose preserved.

---

## Hardcoded-reference count

### In the 9 target files (in-scope concepts, code-level only)
| File | Before | After |
|---|---:|---:|
| `odds_api_service.py` | 2 | **0** |
| `sync_service.py` | 2 | **0** |
| `badge_resolver.py` | 2 | **0** |
| `mlb_high_friction_model.py` | 2 | **0** |
| `tier_builder_service.py` | 2 | **0** |
| `ssot_data_layer.py` | 2 | **0** |
| `sync_orchestration_service.py` | 2 | **0** |
| `bdl_comprehensive_sync.py` | 2 | **0** |
| `insights_sync_service.py` | 2 | **0** |
| **Batch 8 total** | **18** | **0** |

### Global (entire backend, code-level, archives excluded)
- **Global residual: 59 refs across 47 files** (down from 81 pre-Batch-8 — **22-ref net reduction**)
- All 47 plumbed files across Batches 1–8 verified clean on their in-scope concepts.

**Top Batch 9 candidates (long-tail, mostly 1 ref each):**
```
  2  services/picks/player_stats_resolver.py
  1  advanced_analytics.py
  1  repositories/player_repo.py
  1  repositories/sync_repo.py
  1  routes/command.py
  1  routes/mlb_ripple.py
  1  services/usage_spike_detector.py
  1  services/props_service.py
  1  services/injury_service.py
  1  services/injury_advantage.py
```

---

## Resolver parity check (all concepts used)

```
context_flags(nba)     = nba_context_engine
events_cache(nba)      = dg_events_cache
odds_cache(nba)        = dg_odds_cache
board_cache(nba)       = dg_cached_board
master_hub(nba)        = nba_master_hub_2026
master_hub(mlb)        = mlb_master_hub_2026
live_props(mlb)        = mlb_live_props
sync_log(shared)       = dg_sync_log
```
Physical names unchanged → runtime identical.

---

## Regression results (canonical Wave-0 suite)

```
pytest tests/test_collection_names.py tests/test_hit_rate_canonical.py \
       tests/test_tier_integrity.py tests/test_decision_layer_sengun.py
--------------------------------------------------------------------
80 passed, 1 skipped, 1 warning in 18.09s
```
Matches baseline. **Zero new failures.**

---

## Live endpoint smoke (post full `supervisorctl restart`)

```
backend                                      RUNNING (pid 1979, uptime 0:00:06)

NBA:
/api/v3/ferrari/safe-haven                   HTTP 200 (0.21s)
/api/v3/ferrari/front-lines                  HTTP 200 (0.13s)
/api/v3/ferrari/war-zone                     HTTP 200 (0.14s)
/api/v3/scheduler-status                     HTTP 200 (0.10s)
/api/v3/ferrari/market-moves?sport=nba       HTTP 200 (0.11s)
/api/v3/live-scores                          HTTP 200 (0.11s)

MLB:
/api/v3/mlb/safe-haven                       HTTP 200 (0.10s)
/api/v3/mlb/front-lines                      HTTP 200 (0.16s)
/api/v3/mlb/war-zone                         HTTP 200 (0.11s)

backend.err.log                              0 errors (no traceback/ImportError/KeyError/AttributeError)
```

All 9 services instantiate cleanly at module-load. `context_flags` concept (new to this batch) resolves correctly via `badge_resolver.py`. Both NBA and MLB pipelines remain healthy end-to-end.

---

## Recommendation

**➡️ Proceed to Batch 9.**

All Batch 8 edits landed cleanly. This batch introduced routing for the `context_flags` concept (via `badge_resolver.py`) — verified resolver returns the expected `nba_context_engine` physical name. The remaining global residual (59 refs across 47 files) is now distributed as long-tail with mostly 1 ref per file — likely closeable in 1–2 more batches.

Zero regressions · zero new errors · zero behavior change.

No blockers detected.

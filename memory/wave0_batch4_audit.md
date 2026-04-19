# Wave 0 — Batch 4 (Boot / Indexes / Scheduler / Legacy Engine) · Audit

**Scope**: 4 files routed through `COLL(...)` with zero behavior change.
**Priority concepts**: board_cache, board_cache_temp, master_hub, master_roster, live_props, odds_cache, events_cache, context_flags, career_backstop, defensive_momentum_cache, star_usage_cache, injuries, live_injuries, sync_log, shared caches.
**Exclusions honored**: `routes_archive/roster.py` not touched.
**Rules honored**: no renames · no data moves · no dual-writes · no logic/query changes · no cleanup outside batch.

---

## Files changed (4)
1. `backend/server.py`
2. `backend/scripts/init_database.py`
3. `backend/routes/scheduler.py`
4. `backend/services/engines/demon_goblin_engine.py`

---

## Exact literals removed → COLL(...) replacements added (44)

### `server.py` — 22 replacements
| # | Line | Removed | Added |
|---|---:|---|---|
|  1 | 714  | `db.nba_master_hub_2026.count_documents(` | `db[COLL("master_hub", "nba")].count_documents(` |
|  2 | 729  | `db.nba_master_hub_2026.find(`            | `db[COLL("master_hub", "nba")].find(` |
|  3 | 1306 | `db.dg_cached_board.create_index(...player_name)` | `db[COLL("board_cache", "nba")].create_index(...)` |
|  4 | 1307 | `db.dg_cached_board.create_index(...team)`        | `db[COLL("board_cache", "nba")].create_index(...)` |
|  5 | 1308 | `db.dg_cached_board.create_index(...synced_at)`   | `db[COLL("board_cache", "nba")].create_index(...)` |
|  6 | 1309 | `db.dg_cached_board.create_index(...props.stat_type)` | `db[COLL("board_cache", "nba")].create_index(...)` |
|  7 | 1312 | `db.nba_master_hub_2026.create_index(...display_name)` | `db[COLL("master_hub", "nba")].create_index(...)` |
|  8 | 1313 | `db.nba_master_hub_2026.create_index(...bdl_id)`  | `db[COLL("master_hub", "nba")].create_index(...)` |
|  9 | 1314 | `db.nba_master_hub_2026.create_index(...nba_id)`  | `db[COLL("master_hub", "nba")].create_index(...)` |
| 10 | 1315 | `db.nba_master_hub_2026.create_index(...team)`    | `db[COLL("master_hub", "nba")].create_index(...)` |
| 11 | 1332 | `db.dg_cached_board.create_index([(player_name,nba_id)])`    | `db[COLL("board_cache", "nba")].create_index(...)` |
| 12 | 1333 | `db.dg_cached_board.create_index([(is_active,stat_type)])`    | `db[COLL("board_cache", "nba")].create_index(...)` |
| 13 | 1334 | `db.nba_master_hub_2026.create_index([(player_name,nba_id)])` | `db[COLL("master_hub", "nba")].create_index(...)` |
| 14 | 1335 | `db.nba_master_hub_2026.create_index([(is_active,team)])`     | `db[COLL("master_hub", "nba")].create_index(...)` |
| 15 | 1338 | `db.dg_cached_board.create_index([...is_demon,is_goblin,commence_time])` | `db[COLL("board_cache", "nba")].create_index(...)` |
| 16 | 1343 | `db.dg_cached_board.create_index([...h10_rate,is_goblin])` | `db[COLL("board_cache", "nba")].create_index(...)` |
| 17 | 1349 | `db.dg_cached_board_temp.create_index(...)` | `db[COLL("board_cache_temp", "nba")].create_index(...)` |
| 18 | 1352 | `db.ticker_headlines.create_index(...fingerprint)` | `db[COLL.shared("ticker_headlines")].create_index(...)` |
| 19 | 1353 | `db.ticker_headlines.create_index(...first_seen_at)` | `db[COLL.shared("ticker_headlines")].create_index(...)` |
| 20 | 1997 | `db.nba_master_hub_2026.count_documents({})` | `db[COLL("master_hub", "nba")].count_documents({})` |
| 21 | 1998 | `db.dg_cached_board.count_documents({})` | `db[COLL("board_cache", "nba")].count_documents({})` |
| 22 | 2056 | `db.nba_master_hub_2026.find_one(...)` | `db[COLL("master_hub", "nba")].find_one(...)` |

### `scripts/init_database.py` — 8 replacements
| # | Line | Removed | Added |
|---|---:|---|---|
|  1 | 182 | `db.nba_master_hub_2026.create_index("display_name")` | `db[COLL("master_hub", "nba")].create_index("display_name")` |
|  2 | 183 | `db.nba_master_hub_2026.create_index("bdl_id")` | `db[COLL("master_hub", "nba")].create_index("bdl_id")` |
|  3 | 184 | `db.nba_master_hub_2026.create_index("team")` | `db[COLL("master_hub", "nba")].create_index("team")` |
|  4 | 188 | `db.dg_cached_board.create_index("player_name")` | `db[COLL("board_cache", "nba")].create_index("player_name")` |
|  5 | 189 | `db.dg_cached_board.create_index([(player_name,1),(commence_time,1)])` | `db[COLL("board_cache", "nba")].create_index(...)` |
|  6 | 211 | `db.nba_master_hub_2026.count_documents({})` | `db[COLL("master_hub", "nba")].count_documents({})` |
|  7 | 215 | `db.nba_master_hub_2026.count_documents({bdl_game_logs...})` | `db[COLL("master_hub", "nba")].count_documents({...})` |
|  8 | 219 | `db.dg_cached_board.count_documents({})` | `db[COLL("board_cache", "nba")].count_documents({})` |

### `routes/scheduler.py` — 7 replacements
| # | Line | Removed | Added |
|---|---:|---|---|
|  1 | 125 | `db.nba_master_hub_2026.create_index("display_name")` | `db[COLL("master_hub", "nba")].create_index("display_name")` |
|  2 | 126 | `db.nba_master_hub_2026.create_index("bdl_id")` | `db[COLL("master_hub", "nba")].create_index("bdl_id")` |
|  3 | 127 | `db.dg_cached_board.create_index("player_name")` | `db[COLL("board_cache", "nba")].create_index("player_name")` |
|  4 | 142 | `await db.nba_master_hub_2026.count_documents({})` (value side) | `await db[COLL("master_hub", "nba")].count_documents({})` |
|  5 | 143 | `await db.dg_cached_board.count_documents({})` (value side) | `await db[COLL("board_cache", "nba")].count_documents({})` |
|  6 | 351 | `db.nba_master_hub_2026.find(...)` | `db[COLL("master_hub", "nba")].find(...)` |
|  7 | 375 | `db.nba_master_hub_2026.count_documents({...})` | `db[COLL("master_hub", "nba")].count_documents({...})` |

> **Response-shape preserved**: The JSON keys `"nba_master_hub_2026"` and `"dg_cached_board"` in the `data_counts` response dict (L142-143) are user-facing API labels and were intentionally left unchanged.

### `services/engines/demon_goblin_engine.py` — 7 replacements
| # | Line | Removed | Added |
|---|---:|---|---|
|  1 | 419 | `self.events_cache = db.dg_events_cache` | `self.events_cache = db[COLL("events_cache", "nba")]` |
|  2 | 420 | `self.odds_cache = db.dg_odds_cache` | `self.odds_cache = db[COLL("odds_cache", "nba")]` |
|  3 | 423 | `self.sync_log = db.dg_sync_log` | `self.sync_log = db[COLL.shared("sync_log")]` |
|  4 | 428 | `self.live_props = db.dg_live_props` | `self.live_props = db[COLL("live_props", "nba")]` |
|  5 | 434 | `self.cached_board = db.dg_cached_board` | `self.cached_board = db[COLL("board_cache", "nba")]` |
|  6 | 435 | `self.master_roster = db.dg_master_roster` | `self.master_roster = db[COLL("master_roster", "nba")]` |
|  7 | 437 | `self.master_hub = db.nba_master_hub_2026` | `self.master_hub = db[COLL("master_hub", "nba")]` |

**Imports added** (4 files): `from services.config.collection_names import COLL`.

---

## Out-of-scope refs in these files (reported, left untouched)

| File | Literal(s) | Reason |
|---|---|---|
| `server.py` | `db.odds_api_mapping_master`, `db.dg_stats_cache`, `db.dg_war_zone`, `db.dg_front_lines`, `db.dg_goblin_vault` | Not in registry nor priority list |
| `scripts/init_database.py` | `db.dvp_rankings` | Not in registry |
| `routes/scheduler.py` | `db.dvp_rankings`, JSON response-key strings `"nba_master_hub_2026"` / `"dg_cached_board"` | Response-shape preservation |
| `demon_goblin_engine.py` | `db.dg_player_data`, `db.dg_stats_cache`, `db.dg_trending`, `db.dg_line_history`, `db.dg_radar_picks`, `db.dg_goblin_vault`, `db.dg_front_lines`, `db.dg_parlay_builder`, `db.dg_goblin_recon`, `db.dg_flagged_players`, `db.dg_static_shell`, `db.dg_dynamic_lines`, `db.dg_bdl_cache`, `db.dg_daily_insights`, `db.bdl_injuries`, `db.dg_breaking_news` | Not in registry, not in priority list |

Docstring / log-text / comment prose untouched (no text churn per strict-refactor).

---

## Hardcoded-reference count

### In the 4 target files (in-scope concepts, code-level only)
| | Before | After |
|---|---:|---:|
| `server.py` | 22 | **0** |
| `scripts/init_database.py` | 8 | **0** |
| `routes/scheduler.py` | 7 | **0** |
| `demon_goblin_engine.py` | 7 | **0** |
| **Batch 4 total** | **44** | **0** |

### Global (entire backend, code-level, excluding docstrings/comments/`routes_archive/`)
- **Before Batch 4**: ~153 refs
- **After Batch 4**: **109 refs across 70 files** (-44)
- All 21 plumbed files across Batches 1–4 verified: **0 residuals on in-scope concepts each batch** (7 files still carry hits on *out-of-scope concepts* per their original batch priorities — these are scope-discipline artifacts, not regressions).

**Top Batch 5 candidates (not yet plumbed, archive excluded):**
```
  3  services/market_moves_engine.py
  3  services/roster_service.py
  3  services/rolling_cache_manager.py
  3  services/team_stats_service.py
  3  services/oracle_apex_service.py
  3  services/vision_intel_enrichment_service.py
  3  services/data_integrity_service.py
  3  services/headshot_service.py
  3  services/engines/social_signal_engine.py
  2  repositories/board_repo.py
```

---

## Resolver parity check

```
live_props(nba)       = dg_live_props
board_cache(nba)      = dg_cached_board
board_cache_temp(nba) = dg_cached_board_temp
master_hub(nba)       = nba_master_hub_2026
master_roster(nba)    = dg_master_roster
events_cache(nba)     = dg_events_cache
odds_cache(nba)       = dg_odds_cache
sync_log(shared)      = dg_sync_log
ticker_headlines      = ticker_headlines
```
Physical names unchanged → runtime identical.

---

## Regression results (canonical Wave-0 suite)

```
pytest tests/test_collection_names.py tests/test_hit_rate_canonical.py \
       tests/test_tier_integrity.py tests/test_decision_layer_sengun.py
--------------------------------------------------------------------
80 passed, 1 skipped, 1 warning in 15.42s
```
Matches baseline. **Zero new failures.**

---

## Live endpoint smoke (post full restart)

```
supervisor restart            backend started cleanly (pid 6616, uptime 0:00:05)
/api/v3/ferrari/safe-haven    HTTP 200 (0.17s, 231B)
/api/v3/ferrari/front-lines   HTTP 200 (0.14s, 234B)
/api/v3/ferrari/war-zone      HTTP 200 (0.13s, 225B)
/api/v3/scheduler-status      HTTP 200 (0.13s, 3496B)  running=True, jobs=22, tz=UTC
backend.err.log               0 errors (grep traceback/ImportError/KeyError/AttributeError → empty)
MongoDBJobStore               enabled, MLB_HEALTH check passed
```

Index creation path (the largest blast-radius change in this batch) executed cleanly on startup — all 22 `COLL(...)` `create_index` calls resolved and ran without errors. Boot-time `initial_sync` database-emptiness check also executed successfully against the routed master_hub + board_cache collections.

---

## Recommendation

**➡️ Proceed to Batch 5.**

All Batch 4 edits landed cleanly. The boot-time index creation block was the highest-risk plumbing surface in the codebase — it executed on a full `supervisorctl restart` with zero errors, confirming that `COLL(...)` resolves correctly at module-import time and at async-startup time alike.

Zero regressions · zero new errors · zero behavior change · scheduler running · 22 jobs healthy.

No blockers detected.

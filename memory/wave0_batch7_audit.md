# Wave 0 — Batch 7 (MLB Pipeline + Roster Services) · Audit

**Scope**: 8 files routed through `COLL(...)` with zero behavior change.
**Priority concepts**: master_hub, master_roster, player_stats_agg, board_cache, live_props, odds_cache, events_cache, sync_log, context_flags, injuries, live_injuries + MLB sport-specific caches.
**Rules honored**: no renames · no data moves · no dual-writes · no logic/query changes · no helper utilities added.

---

## Files changed (8)
1. `backend/services/mlb_tier_service.py`
2. `backend/services/mlb_lineup_ripple_service.py`
3. `backend/services/mlb_master_sync.py`
4. `backend/services/mlb_oracle_apex_service.py`
5. `backend/services/roster_service.py`
6. `backend/services/roster_sync_service.py`
7. `backend/services/data_integrity_service.py`
8. `backend/services/photo_service.py`

---

## Exact literals removed → COLL(...) replacements added (30)

### `mlb_tier_service.py` — 6 replacements
| # | Line | Removed | Added |
|---|---:|---|---|
| 1 | 197  | `self.cached_board = db.mlb_cached_board`        | `self.cached_board = db[COLL("board_cache", "mlb")]` |
| 2 | 199  | `self.master_hub = db.mlb_master_hub_2026`       | `self.master_hub = db[COLL("master_hub", "mlb")]` |
| 3 | 527  | `master_hub = self.db.mlb_master_hub_2026`       | `master_hub = self.db[COLL("master_hub", "mlb")]` |
| 4 | 1441 | `master_hub = self.db.mlb_master_hub_2026`       | `master_hub = self.db[COLL("master_hub", "mlb")]` |
| 5 | 1450 | `cached_board = self.db.mlb_cached_board`        | `cached_board = self.db[COLL("board_cache", "mlb")]` |
| 6 | 2563 | `async for player in self.db.mlb_master_hub_2026.find(...)` | `async for player in self.db[COLL("master_hub", "mlb")].find(...)` |

### `mlb_lineup_ripple_service.py` — 6 replacements
| # | Line | Removed | Added |
|---|---:|---|---|
| 1 | 115 | `sync_db.mlb_master_hub_2026.find_one(query, {'_id': 0})` | `sync_db[COLL("master_hub", "mlb")].find_one(query, {'_id': 0})` |
| 2 | 168 | `sync_db.mlb_master_hub_2026.find(...)` | `sync_db[COLL("master_hub", "mlb")].find(...)` |
| 3 | 220 | `sync_db.mlb_master_hub_2026.find(...)` | `sync_db[COLL("master_hub", "mlb")].find(...)` |
| 4 | 233 | `sync_db.mlb_cached_board.find_one(...)` | `sync_db[COLL("board_cache", "mlb")].find_one(...)` |
| 5 | 443 | `self.db.mlb_cached_board.find({}, {"team": 1})` | `self.db[COLL("board_cache", "mlb")].find({}, {"team": 1})` |
| 6 | 549 | `sync_db.mlb_master_hub_2026.find(...)` | `sync_db[COLL("master_hub", "mlb")].find(...)` |

### `mlb_master_sync.py` — 6 replacements
| # | Line | Removed | Added |
|---|---:|---|---|
| 1 | 58 | `self.db.mlb_live_props.count_documents({})` | `self.db[COLL("live_props", "mlb")].count_documents({})` |
| 2 | 59 | `self.db.mlb_live_props.delete_many({})` | `self.db[COLL("live_props", "mlb")].delete_many({})` |
| 3 | 196 | `self.db.mlb_live_props.find({"bookmaker": "prizepicks"})` | `self.db[COLL("live_props", "mlb")].find({"bookmaker": "prizepicks"})` |
| 4 | 207 | `self.db.mlb_live_props.find({"bookmaker": {"$in": ["draftkings", "pinnacle"]}})` | `self.db[COLL("live_props", "mlb")].find({"bookmaker": {"$in": ["draftkings", "pinnacle"]}})` |
| 5 | 245 | `self.db.mlb_cached_board.find({}, {...projection...})` | `self.db[COLL("board_cache", "mlb")].find({}, {...projection...})` |
| 6 | 280 | `self.db.mlb_cached_board.find({})` | `self.db[COLL("board_cache", "mlb")].find({})` |

### `mlb_oracle_apex_service.py` — 3 replacements
| # | Line | Removed | Added |
|---|---:|---|---|
| 1 | 325 | `self.cached_board = db.mlb_cached_board`    | `self.cached_board = db[COLL("board_cache", "mlb")]` |
| 2 | 326 | `self.live_props = db.mlb_live_props`        | `self.live_props = db[COLL("live_props", "mlb")]` |
| 3 | 327 | `self.master_hub = db.mlb_master_hub_2026`   | `self.master_hub = db[COLL("master_hub", "mlb")]` |

### `roster_service.py` — 3 replacements
| # | Line | Removed | Added |
|---|---:|---|---|
| 1 | 73  | `self.master_roster = db.dg_master_roster`   | `self.master_roster = db[COLL("master_roster", "nba")]` |
| 2 | 74  | `self.master_hub = db.nba_master_hub_2026`   | `self.master_hub = db[COLL("master_hub", "nba")]` |
| 3 | 233 | `cached_board = self.db.dg_cached_board`     | `cached_board = self.db[COLL("board_cache", "nba")]` |

### `roster_sync_service.py` — 2 replacements
| # | Line | Removed | Added |
|---|---:|---|---|
| 1 | 43  | `self.master_hub = db.nba_master_hub_2026`   | `self.master_hub = db[COLL("master_hub", "nba")]` |
| 2 | 201 | `await self.db.dg_cached_board.distinct("player_name")` | `await self.db[COLL("board_cache", "nba")].distinct("player_name")` |

### `data_integrity_service.py` — 2 replacements (closing Batch 6 out-of-scope residuals)
| # | Line | Removed | Added |
|---|---:|---|---|
| 1 | 35 | `self.master_roster = db.dg_master_roster`   | `self.master_roster = db[COLL("master_roster", "nba")]` |
| 2 | 36 | `self.sync_log = db.dg_sync_log`             | `self.sync_log = db[COLL.shared("sync_log")]` |

### `photo_service.py` — 2 replacements
| # | Line | Removed | Added |
|---|---:|---|---|
| 1 | 32 | `self.master_hub = db.nba_master_hub_2026`   | `self.master_hub = db[COLL("master_hub", "nba")]` |
| 2 | 33 | `self.cached_board = db.dg_cached_board`     | `self.cached_board = db[COLL("board_cache", "nba")]` |

**Imports added** (8 files): `from services.config.collection_names import COLL`.

---

## Out-of-scope refs in these files (reported, left untouched)

| File:Line | Literal | Reason left |
|---|---|---|
| `mlb_tier_service.py:2486` | `self.db['nba_context_engine']` | `nba_context_engine` not in Batch 7 priority list / not in registry |
| `roster_service.py:75` | `self.flagged_players = db.dg_flagged_players` | Not in registry |
| `data_integrity_service.py:37` | `self.verification_failures = db.dg_verification_failures` | Not in registry |

All docstring/log-text/comment prose preserved (no text churn).

---

## Hardcoded-reference count

### In the 8 target files (in-scope concepts, code-level only)
| File | Before | After |
|---|---:|---:|
| `mlb_tier_service.py` | 6 | **0** |
| `mlb_lineup_ripple_service.py` | 6 | **0** |
| `mlb_master_sync.py` | 6 | **0** |
| `mlb_oracle_apex_service.py` | 3 | **0** |
| `roster_service.py` | 3 | **0** |
| `roster_sync_service.py` | 2 | **0** |
| `data_integrity_service.py` | 2 | **0** |
| `photo_service.py` | 2 | **0** |
| **Batch 7 total** | **30** | **0** |

### Global (entire backend, code-level, `routes_archive/` excluded)
- **Global residual: 81 refs across 58 files** (down from 103 pre-Batch-7)
- All 38 plumbed files across Batches 1–7 verified clean on in-scope concepts.

**Top Batch 8 candidates (live code only, archive excluded):**
```
  3  _archive_mlb_v1/services/mlb_four_gate_system.py  ← archived, can skip
  2  services/odds_api_service.py
  2  services/sync_service.py
  2  services/badge_resolver.py
  2  services/mlb_high_friction_model.py
  2  services/tier_builder_service.py
  2  services/ssot_data_layer.py
  2  services/sync_orchestration_service.py
  2  services/bdl_comprehensive_sync.py
  2  services/insights_sync_service.py
```

---

## Resolver parity check (all concepts used)

```
master_hub(nba)     = nba_master_hub_2026
master_hub(mlb)     = mlb_master_hub_2026
master_roster(nba)  = dg_master_roster
board_cache(nba)    = dg_cached_board
board_cache(mlb)    = mlb_cached_board
live_props(mlb)     = mlb_live_props
sync_log(shared)    = dg_sync_log
```
Physical names unchanged → runtime identical.

---

## Regression results (canonical Wave-0 suite)

```
pytest tests/test_collection_names.py tests/test_hit_rate_canonical.py \
       tests/test_tier_integrity.py tests/test_decision_layer_sengun.py
--------------------------------------------------------------------
80 passed, 1 skipped, 1 warning in 15.74s
```
Matches baseline. **Zero new failures.**

---

## Live endpoint smoke (post full `supervisorctl restart`)

```
backend                              RUNNING (pid 12916, uptime 0:00:06)

NBA (existing regression):
/api/v3/ferrari/safe-haven           HTTP 200 (0.17s)
/api/v3/ferrari/front-lines          HTTP 200 (0.13s)
/api/v3/ferrari/war-zone             HTTP 200 (0.10s)
/api/v3/scheduler-status             HTTP 200 (0.14s, 22 jobs running)

MLB (exercises mlb_tier_service + mlb_oracle_apex_service end-to-end):
/api/v3/mlb/safe-haven               HTTP 200 (0.13s, 61025B)  ← real data, COLL routing live
/api/v3/mlb/front-lines              HTTP 200 (0.15s, 60986B)
/api/v3/mlb/war-zone                 HTTP 200 (0.12s, 59775B)

backend.err.log                      0 errors (no traceback/ImportError/KeyError/AttributeError)
```

The MLB endpoints returning 60KB+ payloads is strong evidence that `mlb_tier_service.py` and `mlb_oracle_apex_service.py` are reading real cached board and master hub data through the `COLL(...)` indirection — not just passing module-load.

---

## Recommendation

**➡️ Proceed to Batch 8.**

All Batch 7 edits landed cleanly. This is the first batch that completed an entire sport pipeline (MLB: `mlb_tier_service` + `mlb_master_sync` + `mlb_oracle_apex_service` + `mlb_lineup_ripple_service`) — all 4 MLB-specific services now route through `COLL(...)`. Live smoke confirms the MLB tier endpoints return real data, validating the end-to-end query path under the new indirection layer.

Zero regressions · zero new errors · zero behavior change.

No blockers detected.

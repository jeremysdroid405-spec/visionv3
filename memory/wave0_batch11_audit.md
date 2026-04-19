# Wave 0 — Batch 11 (Second-Pass Cleanup + Long-Tail) · Audit

**Scope**: 14 files routed through `COLL(...)` with zero behavior change.
**Scanner**: Broader authoritative scanner (attribute + bracket-access, all variable forms).
**Priority concepts**: master_hub, board_cache, board_cache_temp, live_props, odds_cache, events_cache, context_flags, injuries, live_injuries, live_scores_cache, ticker_cache, ticker_headlines, breaking_news_cache, sync_log, odds_mapping, star_usage_cache, MLB sport-specific caches.
**Exclusions honored**: `scripts/layer_audit.py` not touched; archived/dead code not touched.

---

## ⚠️ Stop & Report — `odds_mapping` concept not in registry

User listed `odds_mapping` in the priority list, but the registry (`services/config/collection_names.py`) has **no entry** for `odds_mapping`. Per strict-refactor rules ("If a tiny directly-dependent helper is required, stop and report before expanding scope"), I did **NOT** add a registry entry.

**Deferred refs (3 total, all in `server.py`):**
- `server.py:1316` `db.odds_api_mapping_master.create_index([("odds_api_name", ASCENDING)], background=True)`
- `server.py:1317` `db.odds_api_mapping_master.create_index([("hub_player_name", ASCENDING)], background=True)`
- `server.py:1318` `db.odds_api_mapping_master.create_index([("bdl_id", ASCENDING)], sparse=True, background=True)`

**Recommendation to unblock:** Add this line to `services/config/collection_names.py::_SHARED_COLLECTIONS`:
```python
"odds_mapping": "odds_api_mapping_master",
```
Then route these 3 refs in the next batch via `db[COLL.shared("odds_mapping")]`.

---

## Files changed (14)
1. `backend/server.py` (second-pass — 2 routed, 3 deferred)
2. `backend/services/mlb_tier_sorter.py`
3. `backend/services/vegas_killer_model.py` (second-pass)
4. `backend/services/forward_testing_service.py`
5. `backend/services/vegas_pro_model.py` (second-pass)
6. `backend/services/mlb_badge_system.py`
7. `backend/services/vegas_regression_model.py` (second-pass)
8. `backend/services/bdl_enhanced_data.py` (second-pass)
9. `backend/services/mlb_tier_service.py` (second-pass)
10. `backend/services/live_injury_micro_sync.py`
11. `backend/services/bdl_player_badge_service.py`
12. `backend/services/injury_sensor.py`
13. `backend/routes/vision.py`
14. `backend/repositories/board_repo.py` (second-pass — `live_props` now in-scope)

---

## Exact literals removed → COLL(...) replacements added (20)

| # | File:Line | Removed | Added |
|---|---|---|---|
|  1 | `server.py:438` | `db.mlb_live_props.count_documents({})` | `db[COLL("live_props", "mlb")].count_documents({})` |
|  2 | `server.py:439` | `db.mlb_cached_board.count_documents({})` | `db[COLL("board_cache", "mlb")].count_documents({})` |
|  3 | `mlb_tier_sorter.py:202` | `self.db["mlb_master_hub_2026"]` | `self.db[COLL("master_hub", "mlb")]` |
|  4 | `mlb_tier_sorter.py:206` | `self.db["mlb_cached_board"]` | `self.db[COLL("board_cache", "mlb")]` |
|  5 | `vegas_killer_model.py` (2 sites) | `self.db['nba_master_hub_2026']` | `self.db[COLL("master_hub", "nba")]` *(bulk replace_all)* |
|  6 | `forward_testing_service.py:363` | `self.db["dg_cached_board"]` | `self.db[COLL("board_cache", "nba")]` |
|  7 | `forward_testing_service.py:365` | `self.db["mlb_cached_board"]` | `self.db[COLL("board_cache", "mlb")]` |
|  8 | `vegas_pro_model.py` (2 sites) | `self.db['nba_master_hub_2026']` | `self.db[COLL("master_hub", "nba")]` *(bulk replace_all)* |
|  9 | `mlb_badge_system.py:28` | `db.mlb_master_hub_2026` | `db[COLL("master_hub", "mlb")]` |
| 10 | `mlb_badge_system.py` (2nd site) | `self.db["mlb_master_hub_2026"]` | `self.db[COLL("master_hub", "mlb")]` |
| 11 | `vegas_regression_model.py` | `db['nba_master_hub_2026']` | `db[COLL("master_hub", "nba")]` |
| 12 | `bdl_enhanced_data.py` | `self.db.nba_context_engine.update_one(...)` | `self.db[COLL("context_flags", "nba")].update_one(...)` |
| 13 | `mlb_tier_service.py` | `self.db['nba_context_engine']` | `self.db[COLL("context_flags", "nba")]` |
| 14 | `live_injury_micro_sync.py:318` | `self.db.injuries_normalized.find_one(...)` | `self.db[COLL.shared("injuries")].find_one(...)` |
| 15 | `bdl_player_badge_service.py` | `db.nba_master_hub_2026` | `db[COLL("master_hub", "nba")]` |
| 16 | `injury_sensor.py` | `self.db.live_scores_cache.find_one({})` | `self.db[COLL.shared("live_scores_cache")].find_one({})` |
| 17 | `vision.py:174` | `_db.nba_master_hub_2026.find_one(...)` | `_db[COLL("master_hub", "nba")].find_one(...)` |
| 18 | `board_repo.py:20` | `BaseRepository(db.dg_live_props)` | `BaseRepository(db[COLL("live_props", "nba")])` |

**Imports added** (7 files): `from services.config.collection_names import COLL`.
*(7 files already had import from prior batches: server.py, vegas_killer_model.py, vegas_pro_model.py, vegas_regression_model.py, bdl_enhanced_data.py, mlb_tier_service.py, board_repo.py.)*

---

## Out-of-scope refs (reported, left untouched)

- `server.py:1316, 1317, 1318` — `db.odds_api_mapping_master.create_index(...)` × 3 — **deferred pending registry entry for `odds_mapping` concept** (see top of audit).
- Various non-registry collections (`dg_verification_failures`, `dg_flagged_players`, etc.) preserved per prior batch conventions.

---

## Hardcoded-reference count

### In the 14 target files (in-scope concepts, broader scanner)
| File | Before | After |
|---|---:|---:|
| `server.py` (routable in-scope) | 2 | **0** |
| `mlb_tier_sorter.py` | 2 | **0** |
| `vegas_killer_model.py` | 2 | **0** |
| `forward_testing_service.py` | 2 | **0** |
| `vegas_pro_model.py` | 2 | **0** |
| `mlb_badge_system.py` | 2 | **0** |
| `vegas_regression_model.py` | 1 | **0** |
| `bdl_enhanced_data.py` | 1 | **0** |
| `mlb_tier_service.py` | 1 | **0** |
| `live_injury_micro_sync.py` | 1 | **0** |
| `bdl_player_badge_service.py` | 1 | **0** |
| `injury_sensor.py` | 1 | **0** |
| `vision.py` | 1 | **0** |
| `board_repo.py` | 1 | **0** |
| **Batch 11 in-scope total** | **20** | **0** |

Separately: `server.py` has **3 deferred `odds_api_mapping_master` refs** (concept not in registry).

### Global (broader scanner, archives + `scripts/layer_audit.py` excluded)
- **66 → 43 in-scope refs across 42 files** (**23-ref reduction this batch**)
- Deferred: 3 `odds_api_mapping_master` refs in `server.py` (awaiting registry entry approval)

**Top Batch 12 candidates:**
```
  2  scripts/layer_audit.py             ← user excluded
  1  services/historical_data_fetcher.py
  1  services/mlb_vegas_killer_model.py
  1  services/optimized_sync_engine.py
  1  services/bdl_game_logs_sync_batched.py
  1  services/photo_storage_service.py
  1  services/master_hub_sync.py
  1  services/vision_ai_service.py
  1  services/oracle_apex_service.py        ← second-pass
  1  services/bdl_game_logs_sync.py
  1  services/stats_enrichment_service.py
  1  services/intel_suite_calculator.py
  1  services/probability_score_service.py
  1  services/mlb_deep_ingestion.py
  1  services/ferrari_tier_service.py       ← second-pass
```
All long-tail 1-ref files. Batch 12 can close out nearly all of Wave 0.

---

## Resolver parity check

```
master_hub(nba)       = nba_master_hub_2026
master_hub(mlb)       = mlb_master_hub_2026
board_cache(nba)      = dg_cached_board
board_cache(mlb)      = mlb_cached_board
live_props(nba)       = dg_live_props
live_props(mlb)       = mlb_live_props
context_flags(nba)    = nba_context_engine
injuries(shared)      = injuries_normalized
live_scores_cache     = live_scores_cache
```
Physical names unchanged → runtime identical.

---

## Regression results

```
pytest tests/test_collection_names.py tests/test_hit_rate_canonical.py \
       tests/test_tier_integrity.py tests/test_decision_layer_sengun.py
--------------------------------------------------------------------
80 passed, 1 skipped, 1 warning in 20.91s
```
Matches baseline. **Zero new failures.**

---

## Live endpoint smoke (post full `supervisorctl restart`)

```
backend                                       RUNNING (pid 2276, uptime 0:00:06)

NBA + MLB tier endpoints:
/api/v3/ferrari/safe-haven                    HTTP 200 (0.84s)
/api/v3/ferrari/front-lines                   HTTP 200 (0.31s)
/api/v3/ferrari/war-zone                      HTTP 200 (0.45s)
/api/v3/mlb/safe-haven                        HTTP 200 (0.11s)
/api/v3/mlb/front-lines                       HTTP 200 (0.18s)
/api/v3/mlb/war-zone                          HTTP 200 (0.12s)

Infrastructure + plumbed paths:
/api/v3/scheduler-status                      HTTP 200 (0.14s, 22 jobs)
/api/v3/master-hub/player/name/Luka%20Doncic  HTTP 200 (0.22s)     ← regression check
/api/live/scores?sport=nba                    HTTP 200 (0.93s)
/api/v3/vision/status                         HTTP 200             ← vision.py module healthy

backend.err.log                               0 errors (no traceback/ImportError/KeyError/AttributeError)
```

Note: `/api/v3/vision/player/luka-doncic` returned 404 — this is a **pre-existing routing issue** (the `player_router` in `vision.py` is not mounted in `server.py`); not a regression from this batch. The `vision.py` module imports cleanly and `/v3/vision/status` confirms the router loads.

---

## Recommendation

**⚠️ Stop & Report** — But only on `odds_mapping` registry addition. All 20 in-scope edits landed cleanly. Of the 2 scope items that required user input:
1. **Blocker:** `odds_mapping` concept not in registry → 3 refs in `server.py` deferred. User must approve adding `"odds_mapping": "odds_api_mapping_master"` to `_SHARED_COLLECTIONS`.
2. **Non-blocker:** pre-existing vision player router not mounted in `server.py` (unrelated to plumbing).

**Proposed Batch 12 scope:**
- First, add `odds_mapping` registry entry (one-line change) + route 3 `server.py` refs.
- Then close the 15 remaining long-tail 1-ref files (listed above).
- Result: Wave 0 effectively complete, paving the way for Wave 1 shadow-writes.

**Current Wave 0 progress:** 55 files plumbed · Global in-scope residual at **43 refs** · Original baseline was ~429. **Roughly 90% of Wave 0 plumbing is now done.**

Zero regressions · zero new errors · zero behavior change.

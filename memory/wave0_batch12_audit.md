# Wave 0 — Batch 12 (Long-Tail Sweep + odds_mapping closure) · Audit

**Scope**: Registry reconciliation + 15 files routed through `COLL(...)` with zero behavior change.
**Scanner**: Broader authoritative scanner (attribute + bracket-access).

---

## Step 0 — Registry reconciliation

**No code change to `collection_names.py` was needed.**

Investigation revealed `odds_mapping` was **already in the registry** at line 70 of `services/config/collection_names.py`:

```python
"odds_mapping": {"nba": "odds_api_mapping_master", ...}
```

It is a **per-sport** concept, not a shared one. My Batch 11 residual test used `COLL.shared("odds_mapping")` (wrong call signature), which raised `Unknown shared concept`. The correct call is `COLL("odds_mapping", "nba")`. I then immediately routed the 3 deferred `server.py` refs:

| # | File:Line | Removed | Added |
|---|---|---|---|
| 1 | `server.py:1316` | `db.odds_api_mapping_master.create_index([("odds_api_name", ASCENDING)], background=True)` | `db[COLL("odds_mapping", "nba")].create_index([("odds_api_name", ASCENDING)], background=True)` |
| 2 | `server.py:1317` | `db.odds_api_mapping_master.create_index([("hub_player_name", ASCENDING)], background=True)` | `db[COLL("odds_mapping", "nba")].create_index([("hub_player_name", ASCENDING)], background=True)` |
| 3 | `server.py:1318` | `db.odds_api_mapping_master.create_index([("bdl_id", ASCENDING)], sparse=True, background=True)` | `db[COLL("odds_mapping", "nba")].create_index([("bdl_id", ASCENDING)], sparse=True, background=True)` |

Verified on full `supervisorctl restart`: all 3 boot-time index creation calls executed successfully with zero errors.

---

## Files changed (15 — server.py + 14 Batch 12 files)

1. `backend/server.py` (3 odds_mapping index refs)
2. `backend/services/historical_data_fetcher.py`
3. `backend/services/mlb_vegas_killer_model.py`
4. `backend/services/optimized_sync_engine.py`
5. `backend/services/bdl_game_logs_sync_batched.py`
6. `backend/services/photo_storage_service.py`
7. `backend/services/master_hub_sync.py`
8. `backend/services/vision_ai_service.py`
9. `backend/services/oracle_apex_service.py` (second-pass)
10. `backend/services/bdl_game_logs_sync.py`
11. `backend/services/stats_enrichment_service.py`
12. `backend/services/intel_suite_calculator.py`
13. `backend/services/probability_score_service.py`
14. `backend/services/mlb_deep_ingestion.py`
15. `backend/services/ferrari_tier_service.py` (second-pass)

---

## Exact literals removed → COLL(...) replacements added (17 total: 3 server.py + 14 Batch 12)

| # | File:Line | Removed | Added |
|---|---|---|---|
|  1 | `server.py:1316` | `db.odds_api_mapping_master.create_index(...)` | `db[COLL("odds_mapping", "nba")].create_index(...)` |
|  2 | `server.py:1317` | `db.odds_api_mapping_master.create_index(...)` | `db[COLL("odds_mapping", "nba")].create_index(...)` |
|  3 | `server.py:1318` | `db.odds_api_mapping_master.create_index(...)` | `db[COLL("odds_mapping", "nba")].create_index(...)` |
|  4 | `historical_data_fetcher.py:46` | `self.hub = db['nba_master_hub_2026']` | `self.hub = db[COLL("master_hub", "nba")]` |
|  5 | `mlb_vegas_killer_model.py` | `self.master_hub = db.mlb_master_hub_2026` | `self.master_hub = db[COLL("master_hub", "mlb")]` |
|  6 | `optimized_sync_engine.py` | `async for pd in db.dg_cached_board.find(...)` | `async for pd in db[COLL("board_cache", "nba")].find(...)` |
|  7 | `bdl_game_logs_sync_batched.py` | `self.hub = db["nba_master_hub_2026"]` | `self.hub = db[COLL("master_hub", "nba")]` |
|  8 | `photo_storage_service.py` | `self.master_hub = db.nba_master_hub_2026` | `self.master_hub = db[COLL("master_hub", "nba")]` |
|  9 | `master_hub_sync.py` | `self.master_hub = db.nba_master_hub_2026` | `self.master_hub = db[COLL("master_hub", "nba")]` |
| 10 | `vision_ai_service.py` | `self.cached_board = db.dg_cached_board` | `self.cached_board = db[COLL("board_cache", "nba")]` |
| 11 | `oracle_apex_service.py:431` | `self.live_props = db.dg_live_props` | `self.live_props = db[COLL("live_props", "nba")]` |
| 12 | `bdl_game_logs_sync.py` | `self.hub = db["nba_master_hub_2026"]` | `self.hub = db[COLL("master_hub", "nba")]` |
| 13 | `stats_enrichment_service.py` | `self.master_hub = db.nba_master_hub_2026` | `self.master_hub = db[COLL("master_hub", "nba")]` |
| 14 | `intel_suite_calculator.py` | `self.master_hub = db.nba_master_hub_2026` | `self.master_hub = db[COLL("master_hub", "nba")]` |
| 15 | `probability_score_service.py` | `self.master_hub = db.nba_master_hub_2026` | `self.master_hub = db[COLL("master_hub", "nba")]` |
| 16 | `mlb_deep_ingestion.py` | `self.master_hub = db.mlb_master_hub_2026` | `self.master_hub = db[COLL("master_hub", "mlb")]` |
| 17 | `ferrari_tier_service.py:2264` | `context_engine = self.db['nba_context_engine']` | `context_engine = self.db[COLL("context_flags", "nba")]` |

**Imports added** (12 files): `from services.config.collection_names import COLL`.
*(2 files already had the import from prior batches: oracle_apex_service.py, ferrari_tier_service.py.)*

---

## Hardcoded-reference count

### In the 15 target files (in-scope concepts, broader scanner)
All 15 files verified **0 residuals** post-edit:
```
✓ server.py                        (in-scope ref count: 0)
✓ services/historical_data_fetcher.py
✓ services/mlb_vegas_killer_model.py
✓ services/optimized_sync_engine.py
✓ services/bdl_game_logs_sync_batched.py
✓ services/photo_storage_service.py
✓ services/master_hub_sync.py
✓ services/vision_ai_service.py
✓ services/oracle_apex_service.py  (second-pass)
✓ services/bdl_game_logs_sync.py
✓ services/stats_enrichment_service.py
✓ services/intel_suite_calculator.py
✓ services/probability_score_service.py
✓ services/mlb_deep_ingestion.py
✓ services/ferrari_tier_service.py  (second-pass)
```
**Batch 12 + server.py in-scope total: 17 → 0**

### Global (broader scanner, archives excluded)
- **43 → 29 refs across 28 files** (**14-ref reduction this batch**)

**Remaining 29 refs breakdown:**
| Category | Count | Notes |
|---|---:|---|
| `scripts/layer_audit.py` | 2 | **User excluded** from batches |
| `services/config/collection_names.py` | 1 | **The registry itself** — by definition contains literal names |
| Other `scripts/*` (13 files) | 13 | Maintenance/one-off scripts, **not in runtime path** |
| Runtime code (services/engines/adapters/routes) | 14 | **Candidates for Batch 13** |

**Runtime files left for Batch 13 (1 ref each):**
```
 1  config/db_config.py
 1  services/adapters/mlb_adapter.py
 1  services/engines/adaptive_sync_engine.py
 1  services/engines/ai_context_engine.py
 1  services/engines/board_intelligence_engine.py
 1  services/engines/game_lock_engine.py
 1  services/engines/intel_briefing_engine.py
 1  services/mlb_physical_engine.py
 1  services/nba_official_sync.py
 1  services/picks/board_formatter.py
 1  services/picks/photo_service.py
 1  services/scoring/adapters/nba_scoring.py
 1  services/sidecar/hook_bait_detector.py
 1  utils/player_lookup.py
```

---

## Resolver parity check

```
odds_mapping(nba)    = odds_api_mapping_master   ← new this batch
master_hub(nba)      = nba_master_hub_2026
master_hub(mlb)      = mlb_master_hub_2026
board_cache(nba)     = dg_cached_board
live_props(nba)      = dg_live_props
context_flags(nba)   = nba_context_engine
```
Physical names unchanged → runtime identical.

---

## Regression results (canonical Wave-0 suite)

```
pytest tests/test_collection_names.py tests/test_hit_rate_canonical.py \
       tests/test_tier_integrity.py tests/test_decision_layer_sengun.py
--------------------------------------------------------------------
80 passed, 1 skipped, 1 warning in 20.11s
```
Matches baseline. **Zero new failures.**

---

## Live endpoint smoke (post full `supervisorctl restart`)

```
backend                                       RUNNING (pid 4736, uptime 0:00:06)

All endpoints HTTP 200:
/api/v3/ferrari/safe-haven                    HTTP 200 (0.80s)
/api/v3/ferrari/front-lines                   HTTP 200 (0.33s)
/api/v3/ferrari/war-zone                      HTTP 200 (0.42s)
/api/v3/mlb/safe-haven                        HTTP 200 (0.13s)
/api/v3/mlb/front-lines                       HTTP 200 (0.17s)
/api/v3/mlb/war-zone                          HTTP 200 (0.11s)
/api/v3/scheduler-status                      HTTP 200 (0.14s, 22 jobs)
/api/v3/master-hub/player/name/Luka%20Doncic  HTTP 200 (0.18s)
/api/live/scores?sport=nba                    HTTP 200 (0.33s)
/api/v3/ferrari/market-moves?sport=nba        HTTP 200 (0.13s)

backend.err.log                               0 errors
Boot-time index creation path                 3 odds_mapping indexes created cleanly via COLL
```

Clean restart with **3 newly-routed `server.py` index-creation calls** executing through the `COLL("odds_mapping","nba")` indirection is the strongest possible proof-of-correctness for the registry reconciliation.

---

## Recommendation

**➡️ Proceed to Batch 13** — one final runtime batch, then Wave 0 is complete.

### Wave 0 Status
- **56 files plumbed** across 12 batches
- **Global in-scope residual: 29 refs** (down from ~429 baseline, **93% reduction**)
- Remaining runtime refs: **14** (all 1-ref files, lowest-blast-radius surfaces)
- Non-runtime / excluded: 15 refs (`scripts/*` + registry self-reference)

**Proposed Batch 13 closes Wave 0** by routing the 14 remaining runtime 1-ref files. After that:
- Optionally sweep `scripts/*` in Batch 14 (if user wants maintenance scripts routed too)
- Otherwise declare Wave 0 complete and begin Wave 1 (shadow-writes) on the cleanly-plumbed runtime surface

Zero regressions · zero new errors · zero behavior change.

No blockers detected.

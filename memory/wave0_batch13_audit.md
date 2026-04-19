# Wave 0 — Batch 13 (Final Runtime Sweep) · Audit

**Scope**: Route the 14 remaining runtime files through `COLL(...)` with zero behavior change.
**Scanner**: Broader authoritative scanner (attribute + bracket-access, comments/docstrings stripped).
**User decision**: `config/db_config.py` excluded as a registry/self-reference file (Option A — parallel treatment to `services/config/collection_names.py`).

---

## Files changed (13)

1. `backend/services/adapters/mlb_adapter.py`
2. `backend/services/engines/adaptive_sync_engine.py`
3. `backend/services/engines/ai_context_engine.py`
4. `backend/services/engines/board_intelligence_engine.py`
5. `backend/services/engines/game_lock_engine.py`
6. `backend/services/engines/intel_briefing_engine.py`
7. `backend/services/mlb_physical_engine.py`
8. `backend/services/nba_official_sync.py`
9. `backend/services/picks/board_formatter.py`
10. `backend/services/picks/photo_service.py`
11. `backend/services/scoring/adapters/nba_scoring.py`
12. `backend/services/sidecar/hook_bait_detector.py`
13. `backend/utils/player_lookup.py`

**Excluded (user approved)**: `backend/config/db_config.py` — parallel legacy registry; routing its dict values through `COLL(...)` would create circular definition. Treated identically to `services/config/collection_names.py`.

---

## Exact literals removed → COLL(...) replacements added (18 swaps across 13 files)

| # | File:Line | Removed | Added |
|---|---|---|---|
|  1 | `services/adapters/mlb_adapter.py:49` | `db["mlb_cached_board"]` | `db[COLL("board_cache", "mlb")]` |
|  2 | `services/engines/adaptive_sync_engine.py:114` | `self.cached_board_collection = "dg_cached_board"` | `self.cached_board_collection = COLL("board_cache", "nba")` |
|  3 | `services/engines/adaptive_sync_engine.py:117` | `self.master_hub_collection = "nba_master_hub_2026"` | `self.master_hub_collection = COLL("master_hub", "nba")` |
|  4 | `services/engines/adaptive_sync_engine.py:1307` | `self.db["mlb_master_hub_2026"]` | `self.db[COLL("master_hub", "mlb")]` |
|  5 | `services/engines/ai_context_engine.py:44` | `self.master_hub = db.nba_master_hub_2026` | `self.master_hub = db[COLL("master_hub", "nba")]` |
|  6 | `services/engines/board_intelligence_engine.py:81` | `self.dg_cached_board = self.db["dg_cached_board"]` | `self.dg_cached_board = self.db[COLL("board_cache", "nba")]` |
|  7 | `services/engines/game_lock_engine.py:49` | `self.cached_board = db.dg_cached_board` | `self.cached_board = db[COLL("board_cache", "nba")]` |
|  8 | `services/engines/intel_briefing_engine.py:53` | `self.cached_board = db.dg_cached_board` | `self.cached_board = db[COLL("board_cache", "nba")]` |
|  9 | `services/mlb_physical_engine.py:205` | `self.master_hub = db.mlb_master_hub_2026` | `self.master_hub = db[COLL("master_hub", "mlb")]` |
| 10 | `services/nba_official_sync.py:101` | `self.hub = db.nba_master_hub_2026` | `self.hub = db[COLL("master_hub", "nba")]` |
| 11 | `services/picks/board_formatter.py:28` | `self.cached_board = db.dg_cached_board` | `self.cached_board = db[COLL("board_cache", "nba")]` |
| 12 | `services/picks/photo_service.py:28` | `self.master_hub = db.nba_master_hub_2026` | `self.master_hub = db[COLL("master_hub", "nba")]` |
| 13 | `services/scoring/adapters/nba_scoring.py:173` | `return "dg_live_props"` | `return COLL("live_props", "nba")` |
| 14 | `services/scoring/adapters/nba_scoring.py:177` | `return "nba_prop_scores"` | `return COLL("prop_scores", "nba")` |
| 15 | `services/scoring/adapters/nba_scoring.py:181` | `return "dg_cached_board"` | `return COLL("board_cache", "nba")` |
| 16 | `services/scoring/adapters/nba_scoring.py:390` | `hub = db["nba_master_hub_2026"]` | `hub = db[COLL("master_hub", "nba")]` |
| 17 | `services/sidecar/hook_bait_detector.py:57` | `self.master_hub = db.nba_master_hub_2026` | `self.master_hub = db[COLL("master_hub", "nba")]` |
| 18 | `utils/player_lookup.py:40` | `await db.nba_master_hub_2026.find(...)` | `await db[COLL("master_hub", "nba")].find(...)` |

**Imports added** (13 files): `from services.config.collection_names import COLL`.

**Out-of-registry refs deliberately left in place (behavior change forbidden):**
- `mlb_ferrari_safe_haven` (adaptive_sync_engine.py lines 1299/1317/1323) — no registry concept
- `board_sync_status`, `player_vision_log`, `live_ticker`, `scouting_projections` (board_intelligence_engine.py lines 79/80/82/83)
- `dg_locked_games` (game_lock_engine.py line 50)
- `dg_intel_briefings`, `dg_radar_picks`, `dg_goblin_vault`, `dg_parlays_demon`, `dg_parlays_goblin` (intel_briefing_engine.py)
- `mlb_historical_logs` (mlb_physical_engine.py line 206)
- `dg_sync_status` (board_formatter.py line 29)
- `player_photos` (photo_service.py line 27)
- `bdl_advanced_stats` (nba_scoring.py line 308)
- `nba_game_logs_2026` (hook_bait_detector.py line 58)
- `ai_news_cache` (ai_context_engine.py line 45)

---

## Resolver parity check

```
board_cache(nba)  = dg_cached_board
board_cache(mlb)  = mlb_cached_board
master_hub(nba)   = nba_master_hub_2026
master_hub(mlb)   = mlb_master_hub_2026
live_props(nba)   = dg_live_props
prop_scores(nba)  = nba_prop_scores
```
Physical names unchanged → runtime identical.

---

## Regression results (canonical Wave-0 suite)

```
pytest tests/test_collection_names.py tests/test_hit_rate_canonical.py \
       tests/test_tier_integrity.py tests/test_decision_layer_sengun.py
--------------------------------------------------------------------
80 passed, 1 skipped, 1 warning in 20.12s
```
Matches baseline. **Zero new failures.**

---

## Live endpoint smoke (post `supervisorctl restart backend`)

```
backend                                       RUNNING (pid 6106, uptime 0:00:08)

/api/v3/ferrari/safe-haven                    HTTP 200 (0.46s)
/api/v3/ferrari/front-lines                   HTTP 200 (0.30s)
/api/v3/ferrari/war-zone                      HTTP 200 (0.39s)
/api/v3/mlb/safe-haven                        HTTP 200 (0.16s)
/api/v3/mlb/front-lines                       HTTP 200 (0.15s)
/api/v3/mlb/war-zone                          HTTP 200 (0.15s)
/api/v3/scheduler-status                      HTTP 200 (0.10s)
/api/v3/master-hub/player/name/Luka%20Doncic  HTTP 200 (0.23s)
/api/live/scores?sport=nba                    HTTP 200 (0.41s)
/api/v3/ferrari/market-moves?sport=nba        HTTP 200 (0.12s)

backend.err.log                               0 errors
```

---

## Hardcoded-reference count (broader scanner · archives/tests/comments excluded)

| Category | Count | Notes |
|---|---:|---|
| **Batch 13 in-scope** (14 files) | **0** | ✅ **Closed.** `db_config.py` excluded by user approval. |
| Registry self-reference (`services/config/collection_names.py`) | 0 | Stripped comments; all refs are dict-value definitions, not `db.X` access. |
| Runtime (non-Batch-13 surface) | 12 | NEW surface caught by broader scanner’s expanded concept set. See below. |
| `scripts/*` | 14 | User excluded from all Wave 0 batches. |
| **Global total** | **26** | |

**Note on the 12 new "runtime" residuals:**
These files were **not in Batch 13 scope**. They appear now because this broader scanner regex includes additional registry concepts (`spotrac_contracts_cache`, `line_history`, `referee_assignments`, `ticker_cache`, `odds_api_mapping_master`, etc.) that prior-batch scanners did not cover. The Batch 12 audit reported "14 runtime refs remaining" — all 14 of those files are now at 0 residuals, as proven in the Batch-13 in-scope row.

Breakdown of the 12 new refs (not regressions — surface expansion):

```
 4  services/spotrac_contract_service.py   (spotrac_contracts_cache)
 2  routes/ferrari_tiers.py                (referee_assignments, odds_api_mapping_master)
 2  routes/master_hub.py
 2  services/referee_scraper_service.py    (referee_assignments)
 1  services/optimized_sync_engine.py
 1  services/line_movement_tracker.py      (line_history)
```

---

## Recommendation

**✅ Wave 0 — Batch 13 complete. Zero regressions, zero new errors, zero behavior change.**

### Wave 0 Status
- **57 files plumbed** across 13 batches (56 from Batches 2–12 + 13 new in Batch 13; `server.py` already plumbed in Batch 12 Step 0)
- **Original Batch-12 "14 runtime residuals" → 0** ✅
- `config/db_config.py` explicitly excluded as a registry file

### Next-step options for user decision
1. **Declare Wave 0 complete** on the original scanner basis (the 14 files are closed) and proceed to **Wave 1** (shadow-writes).
2. **Optional Batch 14** — plumb the 12 newly-surfaced runtime refs across 6 files (expanded-scanner closure).
3. **Optional Batch 15** — plumb 14 `scripts/*` refs across 13 maintenance scripts.
4. **Optional cleanup** — retire `backend/config/db_config.py` entirely in favor of `services/config/collection_names.py` (out of STRICT REFACTOR MODE; requires explicit approval).

No blockers detected.

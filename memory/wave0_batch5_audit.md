# Wave 0 — Batch 5 (Intel / Enrichment / Repos Plumbing) · Audit

**Scope**: 5 files routed through `COLL(...)` with zero behavior change.
**Priority concepts**: master_hub, board_cache, injuries, live_scores_cache, ticker_cache, ticker_headlines, breaking_news_cache, odds_mapping, shared caches.
**Rules honored**: no renames · no data moves · no dual-writes · no logic/query changes · no helper utilities added.

---

## Files changed (5)
1. `backend/services/market_moves_engine.py`
2. `backend/services/oracle_apex_service.py`
3. `backend/services/vision_intel_enrichment_service.py`
4. `backend/services/headshot_service.py`
5. `backend/repositories/board_repo.py`

---

## Exact literals removed → COLL(...) replacements added (13)

| # | File:Line | Removed | Added |
|---|---|---|---|
|  1 | `market_moves_engine.py:191` | `db.injuries_normalized.find(...)` | `db[COLL.shared("injuries")].find(...)` |
|  2 | `market_moves_engine.py:200` | `db.live_scores_cache.find_one({})` | `db[COLL.shared("live_scores_cache")].find_one({})` |
|  3 | `market_moves_engine.py:221` | `db.dg_cached_board.find(...)` | `db[COLL("board_cache", "nba")].find(...)` |
|  4 | `market_moves_engine.py:244` | `db.nba_master_hub_2026.find(...)` | `db[COLL("master_hub", "nba")].find(...)` |
|  5 | `oracle_apex_service.py:428` | `self.cached_board = db.dg_cached_board` | `self.cached_board = db[COLL("board_cache", "nba")]` |
|  6 | `oracle_apex_service.py:430` | `self.master_hub = db.nba_master_hub_2026` | `self.master_hub = db[COLL("master_hub", "nba")]` |
|  7 | `vision_intel_enrichment_service.py:26`  | `cached_board = db.dg_cached_board` | `cached_board = db[COLL("board_cache", "nba")]` |
|  8 | `vision_intel_enrichment_service.py:162` | `db.dg_cached_board.find_one(...)` | `db[COLL("board_cache", "nba")].find_one(...)` |
|  9 | `vision_intel_enrichment_service.py:209` | `db.dg_cached_board.update_one(...)` | `db[COLL("board_cache", "nba")].update_one(...)` |
| 10 | `headshot_service.py:212` | `self.db.nba_master_hub_2026.find(...)` | `self.db[COLL("master_hub", "nba")].find(...)` |
| 11 | `headshot_service.py:288` | `self.db.nba_master_hub_2026.find(...)` | `self.db[COLL("master_hub", "nba")].find(...)` |
| 12 | `headshot_service.py:304` | `self.db.nba_master_hub_2026.update_one(...)` | `self.db[COLL("master_hub", "nba")].update_one(...)` |
| 13 | `board_repo.py:19` | `BaseRepository(db.dg_cached_board)` | `BaseRepository(db[COLL("board_cache", "nba")])` |

**Imports added** (5 files): `from services.config.collection_names import COLL`.

---

## Out-of-scope refs in these files (reported, left untouched)

| File:Line | Literal | Reason left |
|---|---|---|
| `oracle_apex_service.py:429` | `self.live_props = db.dg_live_props` | `live_props` is **not** in Batch 5's priority-concept list |
| `oracle_apex_service.py:837` | `ferrari_scored = self.db.ferrari_scored` | Not in registry |
| `oracle_apex_service.py:431` | `self.oracle_apex_collection = db.oracle_apex_picks` | Not in registry |
| `board_repo.py:20` | `BaseRepository(db.dg_live_props)` | `live_props` not in Batch 5 priority list |

All docstring / log-text / comment prose references left untouched (no text churn per strict-refactor rules).

---

## Hardcoded-reference count

### In the 5 target files (in-scope concepts, code-level only)
| | Before | After |
|---|---:|---:|
| `market_moves_engine.py` | 4 | **0** |
| `oracle_apex_service.py` (in-scope) | 2 | **0** |
| `vision_intel_enrichment_service.py` | 3 | **0** |
| `headshot_service.py` | 3 | **0** |
| `board_repo.py` (in-scope) | 1 | **0** |
| **Batch 5 total** | **13** | **0** |

### Global (entire backend, code-level, `routes_archive/` excluded, broader pattern now including `live_scores_cache`, `ticker_*`, `breaking_news_cache`, `odds_api_mapping_master`)
- **Global residual: 85 refs across 53 files**
- Batch 5 files' only remaining hits are the 2 explicitly-reported `dg_live_props` refs (out-of-scope per Batch 5's priority list) — **zero actual regressions**.

**Top Batch 6 candidates (not yet plumbed):**
```
  3  services/roster_service.py
  3  services/rolling_cache_manager.py
  3  services/injury_vacuum_service.py
  3  services/data_integrity_service.py
  3  services/engines/social_signal_engine.py
  3  services/engines/live_scores_engine.py
  2  services/odds_api_service.py
  2  services/sync_service.py
  2  services/badge_resolver.py
  2  services/photo_service.py
```

---

## Resolver parity check

```
board_cache(nba)       = dg_cached_board
master_hub(nba)        = nba_master_hub_2026
injuries(shared)       = injuries_normalized
live_scores_cache      = live_scores_cache
ticker_headlines       = ticker_headlines
```
Physical names unchanged → runtime identical.

---

## Regression results (canonical Wave-0 suite)

```
pytest tests/test_collection_names.py tests/test_hit_rate_canonical.py \
       tests/test_tier_integrity.py tests/test_decision_layer_sengun.py
--------------------------------------------------------------------
80 passed, 1 skipped, 1 warning in 15.15s
```
Matches baseline. **Zero new failures.**

---

## Live endpoint smoke (post full `supervisorctl restart`)

```
backend                             RUNNING (pid 8158, uptime 0:00:07)
/api/v3/ferrari/safe-haven          HTTP 200 (0.19s)
/api/v3/ferrari/front-lines         HTTP 200 (0.09s)
/api/v3/ferrari/war-zone            HTTP 200 (0.13s)
/api/v3/scheduler-status            HTTP 200 (0.11s)
/api/v3/ferrari/market-moves?sport=nba  HTTP 200 (0.15s)  — exercises market_moves_engine.py read path
backend.err.log                     0 new errors (no traceback/ImportError/KeyError/AttributeError)
```

All plumbed services import + instantiate cleanly at module load. OracleApexService, BoardRepository, and the run_vision_intel_enrichment entry point all pass import-time checks.

---

## Recommendation

**➡️ Proceed to Batch 6.**

All Batch 5 edits landed cleanly. Zero regressions · zero new errors · zero behavior change. Repository layer (`board_repo.py`) and Intel pipeline (`vision_intel_enrichment_service.py`, `oracle_apex_service.py`, `market_moves_engine.py`) now route through `COLL(...)` for all in-scope concepts. Headshot service (which runs both ad-hoc and on a scheduled path) is also plumbed.

No blockers detected.

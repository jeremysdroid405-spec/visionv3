# Wave 0 — Batch 6 (Injury / Live / Social / Cache / Integrity) · Audit

**Scope**: 5 files routed through `COLL(...)` with zero behavior change.
**Priority concepts**: injuries, live_injuries, master_hub, board_cache, live_scores_cache, ticker_cache, ticker_headlines, breaking_news_cache, star_usage_cache, context_flags, shared caches.
**Rules honored**: no renames · no data moves · no dual-writes · no logic/query changes · no helper utilities added.

---

## Files changed (5)
1. `backend/services/injury_vacuum_service.py`
2. `backend/services/engines/live_scores_engine.py`
3. `backend/services/engines/social_signal_engine.py`
4. `backend/services/rolling_cache_manager.py`
5. `backend/services/data_integrity_service.py`

---

## Exact literals removed → COLL(...) replacements added (23)

### `injury_vacuum_service.py` — 10 replacements
| # | Line | Removed | Added |
|---|---:|---|---|
|  1 | 195  | `sync_db.star_usage_cache.find_one(...)` | `sync_db[COLL("star_usage_cache", "nba")].find_one(...)` |
|  2 | 246  | `sync_db.nba_master_hub_2026.find_one(...)` | `sync_db[COLL("master_hub", "nba")].find_one(...)` |
|  3 | 310  | `sync_db.star_usage_cache.find(...)` | `sync_db[COLL("star_usage_cache", "nba")].find(...)` |
|  4 | 321  | `sync_db.nba_master_hub_2026.find_one(...)` | `sync_db[COLL("master_hub", "nba")].find_one(...)` |
|  5 | 336  | `sync_db.nba_master_hub_2026.find(...)` | `sync_db[COLL("master_hub", "nba")].find(...)` |
|  6 | 463  | `sync_db.nba_master_hub_2026.find_one(...)` | `sync_db[COLL("master_hub", "nba")].find_one(...)` |
|  7 | 596  | `self.db.live_scores_cache.find_one({})` | `self.db[COLL.shared("live_scores_cache")].find_one({})` |
|  8 | 609  | `self.db.ticker_cache.find_one({"type": "games"})` | `self.db[COLL.shared("ticker_cache")].find_one({"type": "games"})` |
|  9 | 647  | `self.db.injuries_normalized.find(...)` | `self.db[COLL.shared("injuries")].find(...)` |
| 10 | 1070 | `sync_db.star_usage_cache.find(...)` | `sync_db[COLL("star_usage_cache", "nba")].find(...)` |

### `live_scores_engine.py` — 3 replacements
| # | Line | Removed | Added |
|---|---:|---|---|
|  1 | 85 | `self.scores_cache = db.live_scores_cache` | `self.scores_cache = db[COLL.shared("live_scores_cache")]` |
|  2 | 86 | `self.news_cache = db.breaking_news_cache` | `self.news_cache = db[COLL.shared("breaking_news_cache")]` |
|  3 | 87 | `self.headlines_col = db.ticker_headlines` | `self.headlines_col = db[COLL.shared("ticker_headlines")]` |

### `social_signal_engine.py` — 3 replacements
| # | Line | Removed | Added |
|---|---:|---|---|
|  1 | 81  | `self.db.dg_cached_board.find(...)` | `self.db[COLL("board_cache", "nba")].find(...)` |
|  2 | 133 | `self.db.nba_master_hub_2026.find_one(...)` | `self.db[COLL("master_hub", "nba")].find_one(...)` |
|  3 | 151 | `self.db.dg_cached_board.find_one(...)` | `self.db[COLL("board_cache", "nba")].find_one(...)` |

### `rolling_cache_manager.py` — 6 replacements (NBA + MLB sport variants of same concepts)
| # | Line | Removed | Added |
|---|---:|---|---|
|  1 | 489 | `self.db.mlb_cached_board.find_one(...)` | `self.db[COLL("board_cache", "mlb")].find_one(...)` |
|  2 | 503 | `hub = self.db.mlb_master_hub_2026` | `hub = self.db[COLL("master_hub", "mlb")]` |
|  3 | 630 | `self.db.dg_cached_board.find_one(...)` | `self.db[COLL("board_cache", "nba")].find_one(...)` |
|  4 | 652 | `hub = self.db.nba_master_hub_2026` | `hub = self.db[COLL("master_hub", "nba")]` |
|  5 | 782 | `collection = db.mlb_cached_board` (MLB branch) | `collection = db[COLL("board_cache", "mlb")]` |
|  6 | 785 | `collection = db.dg_cached_board` (NBA branch) | `collection = db[COLL("board_cache", "nba")]` |

> Note: Lines 489/503/782 were the MLB variants of in-scope concepts (`board_cache`, `master_hub`) that became visible only during final grep sweep. Routing same-concept/different-sport was treated as completing the scope, NOT as scope expansion (user explicitly allowed "any shared caches already present in the registry" and all items are in-scope concepts).

### `data_integrity_service.py` — 1 in-scope replacement
| # | Line | Removed | Added |
|---|---:|---|---|
|  1 | 32 | `self.cached_board = db.dg_cached_board` | `self.cached_board = db[COLL("board_cache", "nba")]` |

**Imports added** (5 files): `from services.config.collection_names import COLL`.

---

## Out-of-scope refs in these files (reported, left untouched)

| File:Line | Literal | Reason left |
|---|---|---|
| `data_integrity_service.py:33` | `self.master_roster = db.dg_master_roster` | `master_roster` not in Batch 6 priority list |
| `data_integrity_service.py:34` | `self.sync_log = db.dg_sync_log` | `sync_log` not in Batch 6 priority list |
| `data_integrity_service.py:35` | `self.verification_failures = db.dg_verification_failures` | Not in registry |
| `injury_vacuum_service.py:134,135` | `self.injury_log = db.injury_log`, `self.vacuum_alerts = db.vacuum_alerts` | Not in registry |
| `injury_vacuum_service.py:683` | `self.db.bdl_injuries.find(...)` | Not in registry |
| `social_signal_engine.py:55,56` | `db.dg_social_signals`, `db.dg_player_news_cache` | Not in registry |
| `live_scores_engine.py` | (none — all in-scope) | — |
| `rolling_cache_manager.py` | (none — all in-scope) | — |

All docstring/log-text/comment prose preserved (no text churn).

---

## Hardcoded-reference count

### In the 5 target files (in-scope concepts, code-level only)
| | Before | After |
|---|---:|---:|
| `injury_vacuum_service.py` | 10 | **0** |
| `live_scores_engine.py` | 3 | **0** |
| `social_signal_engine.py` | 3 | **0** |
| `rolling_cache_manager.py` | 6 | **0** |
| `data_integrity_service.py` (in-scope) | 1 | **0** |
| **Batch 6 total** | **23** | **0** |

### Global (entire backend, code-level, `routes_archive/` excluded, broad pattern incl. MLB variants + all shared caches)
- **Global residual: 103 refs across 66 files** (includes MLB variants newly surfaced by broader pattern)
- All 5 Batch 6 files verified clean on in-scope concepts (4 files fully clean; `data_integrity_service.py` has 2 explicitly-reported out-of-scope concept residuals).

**Top Batch 7 candidates:**
```
  6  services/mlb_tier_service.py
  6  services/mlb_lineup_ripple_service.py
  3  services/roster_service.py
  2  services/odds_api_service.py
  2  services/sync_service.py
  2  services/badge_resolver.py
  2  services/mlb_master_sync.py
  2  services/photo_service.py
  2  services/roster_sync_service.py
  2  services/mlb_oracle_apex_service.py
```

---

## Resolver parity check (all concepts used)

```
star_usage_cache(nba)  = star_usage_cache
master_hub(nba)        = nba_master_hub_2026
master_hub(mlb)        = mlb_master_hub_2026
board_cache(nba)       = dg_cached_board
board_cache(mlb)       = mlb_cached_board
injuries(shared)       = injuries_normalized
live_scores_cache      = live_scores_cache
ticker_cache           = ticker_cache
ticker_headlines       = ticker_headlines
breaking_news_cache    = breaking_news_cache
```
Physical names unchanged → runtime identical.

---

## Regression results (canonical Wave-0 suite)

```
pytest tests/test_collection_names.py tests/test_hit_rate_canonical.py \
       tests/test_tier_integrity.py tests/test_decision_layer_sengun.py
--------------------------------------------------------------------
80 passed, 1 skipped, 1 warning in 15.46s
```
Matches baseline. **Zero new failures.**

---

## Live endpoint smoke (post full `supervisorctl restart`)

```
backend                                      RUNNING (pid 10200, uptime 0:00:06)
/api/v3/ferrari/safe-haven                   HTTP 200 (0.26s)
/api/v3/ferrari/front-lines                  HTTP 200 (0.13s)
/api/v3/ferrari/war-zone                     HTTP 200 (0.13s)
/api/v3/scheduler-status                     HTTP 200 (0.09s)
/api/v3/ferrari/market-moves?sport=nba       HTTP 200 (0.12s)
/api/v3/live-scores                          HTTP 200  — exercises LiveScoresEngine.scores_cache (COLL.shared)
/api/v3/vacuum/updates                       HTTP 200  — exercises InjuryVacuumService get_vacuum_updates path
backend.err.log                              0 errors (no traceback/ImportError/KeyError/AttributeError)
```

All plumbed services instantiate cleanly at module load. LiveScoresEngine's 3 collection bindings (scores_cache, news_cache, headlines_col) resolve through `COLL.shared(...)` without issue. InjuryVacuumService's mixed sync/async DB access pattern (PyMongo `sync_db[COLL(...)]` + Motor `self.db[COLL.shared(...)]`) both resolve correctly — the `db[string_name]` subscript works identically on both drivers.

---

## Recommendation

**➡️ Proceed to Batch 7.**

All Batch 6 edits landed cleanly. Zero regressions · zero new errors · zero behavior change. Noteworthy: this batch validated the cross-driver routing pattern — `COLL(...)` returns a physical string name, which the `db[name]` subscript accepts identically on PyMongo sync clients and Motor async clients. This is important for `injury_vacuum_service.py` which uses both within the same class.

The 2 out-of-scope residuals in `data_integrity_service.py` (`master_roster` + `sync_log`) are good candidates for Batch 7 if those concepts are added to the next priority list.

No blockers detected.

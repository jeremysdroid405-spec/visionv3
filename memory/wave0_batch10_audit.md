# Wave 0 — Batch 10 (Broader Scanner Sweep — Routes + Second-Pass) · Audit

**Scope**: 9 files routed through `COLL(...)` with zero behavior change.
**Scanner**: Broader authoritative scanner now active (covers attribute-access `db.X`, bracket-access `db["X"]`, and variable variants `db`, `self.db`, `sync_db`, `_db`, `engine.db`).
**Priority concepts**: master_hub, board_cache, board_cache_temp, live_props, odds_cache, events_cache, context_flags, injuries, live_injuries, live_scores_cache, ticker_cache, ticker_headlines, breaking_news_cache, sync_log, odds_mapping, shared caches.
**Rules honored**: no renames · no data moves · no dual-writes · no logic/query changes · no helper utilities added.

---

## Files changed (9)
1. `backend/routes/master_hub.py`
2. `backend/routes/ai_context.py`
3. `backend/routes/live.py`
4. `backend/routes/cached_data.py`
5. `backend/services/injury_triggered_rescore.py`
6. `backend/services/board_intelligence_service.py`
7. `backend/routes/ferrari_tiers.py`
8. `backend/services/team_stats_service.py`
9. `backend/routes/qa_testing.py`

---

## Exact literals removed → COLL(...) replacements added (44)

### `routes/master_hub.py` — 8 replacements (all `_db.nba_master_hub_2026` attr-access)
Routed via bulk `replace_all`: every `_db.nba_master_hub_2026` → `_db[COLL("master_hub", "nba")]`.
Lines affected: **L126, L194, L295, L311, L355, L367, L404, L680** (per post-edit `git diff`).

### `routes/ai_context.py` — 7 replacements (all `_db.nba_master_hub_2026`)
Routed via bulk `replace_all`: every `_db.nba_master_hub_2026` → `_db[COLL("master_hub", "nba")]`.
Lines affected: **L31, L40, L52, L66, L75, L95, L108**.

### `routes/live.py` — 7 replacements (all `_db.ticker_cache`)
Routed via bulk `replace_all`: every `_db.ticker_cache` → `_db[COLL.shared("ticker_cache")]`.
Lines affected: **L203, L221, L255, L419, L456, L702, L779**.

### `routes/cached_data.py` — 5 replacements
| # | Line | Removed | Added |
|---|---:|---|---|
| 1 | 316  | `engine.db.dg_cached_board.aggregate(pipeline)` | `engine.db[COLL("board_cache", "nba")].aggregate(pipeline)` |
| 2 | 390  | `engine.db.dg_cached_board.find_one(...)` | `engine.db[COLL("board_cache", "nba")].find_one(...)` |
| 3 | 943  | `master_hub = db.nba_master_hub_2026` | `master_hub = db[COLL("master_hub", "nba")]` |
| 4 | 978  | `db.dg_cached_board.find_one(...)` | `db[COLL("board_cache", "nba")].find_one(...)` |
| 5 | 1038 | `db.dg_cached_board.find_one(...)` | `db[COLL("board_cache", "nba")].find_one(...)` |

### `services/injury_triggered_rescore.py` — 5 replacements
- 4× `_db.dg_cached_board` → `_db[COLL("board_cache", "nba")]` (via `replace_all`) — Lines **L104, L149, L172, L285**.
- 1× `_db.injuries_normalized` → `_db[COLL.shared("injuries")]` — Line **L243**.

### `services/board_intelligence_service.py` — 4 replacements (second-pass on Batch 2)
Routed via bulk `replace_all`: every remaining `db.dg_cached_board` → `db[COLL("board_cache", "nba")]`.
Lines affected: **L64, L274, L304, L330**. *(These were new accesses added since Batch 2; caught by broader scanner.)*

### `routes/ferrari_tiers.py` — 3 replacements
| # | Line | Removed | Added |
|---|---:|---|---|
| 1 | 1017 | `_db.dg_cached_board.find({})` | `_db[COLL("board_cache", "nba")].find({})` |
| 2 | 2842 | `_db["mlb_master_hub_2026"]` | `_db[COLL("master_hub", "mlb")]` |
| 3 | 4207 | `_db["mlb_cached_board"]` | `_db[COLL("board_cache", "mlb")]` |

### `services/team_stats_service.py` — 3 replacements (all bracket-access)
| # | Line | Removed | Added |
|---|---:|---|---|
| 1 | 203 | `self.db['nba_master_hub_2026']` | `self.db[COLL("master_hub", "nba")]` |
| 2 | 316 | `self.db['dg_cached_board']` | `self.db[COLL("board_cache", "nba")]` |
| 3 | 339 | `self.db['nba_master_hub_2026']` | `self.db[COLL("master_hub", "nba")]` |

### `routes/qa_testing.py` — 2 replacements (both `_db.dg_cached_board`)
Routed via bulk `replace_all`: every `_db.dg_cached_board` → `_db[COLL("board_cache", "nba")]`.
Lines affected: **L36, L101**.

**Imports added** (7 files — 2 files already had import from prior batches):
`from services.config.collection_names import COLL`.

---

## Hardcoded-reference count

### In the 9 target files (in-scope concepts, broader scanner)
| File | Before | After |
|---|---:|---:|
| `master_hub.py` | 8 | **0** |
| `ai_context.py` | 7 | **0** |
| `live.py` | 7 | **0** |
| `cached_data.py` | 5 | **0** |
| `injury_triggered_rescore.py` | 5 | **0** |
| `board_intelligence_service.py` | 4 | **0** |
| `ferrari_tiers.py` | 3 | **0** |
| `team_stats_service.py` | 3 | **0** |
| `qa_testing.py` | 2 | **0** |
| **Batch 10 total** | **44** | **0** |

### Global (broader scanner, archives excluded)
- **107 → 66 refs across 56 files** (**41-ref reduction this batch**)

**Top Batch 11 candidates:**
```
  5  server.py                      ← final second-pass residuals (bracket-access)
  2  services/mlb_tier_sorter.py
  2  services/vegas_killer_model.py      ← second-pass
  2  services/forward_testing_service.py
  2  services/vegas_pro_model.py         ← second-pass
  2  services/mlb_badge_system.py
  2  scripts/layer_audit.py
  1  repositories/board_repo.py          ← second-pass
  1  routes/vision.py
  1  services/vegas_regression_model.py  ← second-pass
  1  services/bdl_enhanced_data.py       ← second-pass
  1  services/mlb_tier_service.py        ← second-pass
  1  services/live_injury_micro_sync.py
  1  services/bdl_player_badge_service.py
  1  services/injury_sensor.py
```
*(~8 second-pass files with 1 ref each are leftovers the narrower scanner missed — they'll clear fast.)*

---

## Resolver parity

```
master_hub(nba)       = nba_master_hub_2026
master_hub(mlb)       = mlb_master_hub_2026
board_cache(nba)      = dg_cached_board
board_cache(mlb)      = mlb_cached_board
ticker_cache(shared)  = ticker_cache
injuries(shared)      = injuries_normalized
```
Physical names unchanged → runtime identical.

---

## Regression results

```
pytest tests/test_collection_names.py tests/test_hit_rate_canonical.py \
       tests/test_tier_integrity.py tests/test_decision_layer_sengun.py
--------------------------------------------------------------------
80 passed, 1 skipped, 1 warning in 19.64s
```
Matches baseline. **Zero new failures.**

---

## Live endpoint smoke (post full `supervisorctl restart`)

```
backend                                          RUNNING (pid 6511, uptime 0:00:07)

NBA:
/api/v3/ferrari/safe-haven                       HTTP 200 (0.25s)
/api/v3/ferrari/front-lines                      HTTP 200 (0.14s)
/api/v3/ferrari/war-zone                         HTTP 200 (0.13s)

MLB:
/api/v3/mlb/safe-haven                           HTTP 200 (0.16s)
/api/v3/mlb/front-lines                          HTTP 200 (0.12s)
/api/v3/mlb/war-zone                             HTTP 200 (0.11s)

Scheduler:
/api/v3/scheduler-status                         HTTP 200 (0.12s, 22 jobs)

Batch 10 code paths exercised end-to-end:
/api/v3/master-hub/search?q=luka                 HTTP 200           ← _db[COLL("master_hub","nba")] via L194
/api/v3/master-hub/player/name/Luka%20Doncic     HTTP 200  8 keys   ← L126 COLL routing returns real doc
/api/v3/ai-context/status                        HTTP 200           ← ai_context.py COLL-routed metadata endpoint
/api/v3/ai-context/player/Luka%20Doncic          HTTP 200           ← ai_context.py COLL-routed player lookup
/api/live/scores?sport=nba                       HTTP 200 (0.28s)   ← live.py _db[COLL.shared("ticker_cache")]
/api/live/news?sport=nba                         HTTP 200           ← live.py ticker_cache bulk access
/api/command/search?query=luka&sport=nba         HTTP 200           ← Batch 9 regression verified

backend.err.log                                  0 errors
```

`master-hub/player/name/Luka Doncic` returning a real 8-key document is strong evidence that the new bracket-access routing (`_db[COLL("master_hub","nba")]`) works correctly end-to-end under live request traffic.

---

## Recommendation

**➡️ Proceed to Batch 11.**

All 9 Batch 10 files fully clean under the broader scanner. This batch:
- Closed the bracket-access visibility gap on the biggest remaining route files (`master_hub.py` 8 refs, `ai_context.py` 7 refs, `live.py` 7 refs)
- Completed second-pass cleanup on `board_intelligence_service.py` (4 remaining refs that were added after its Batch 2 plumbing)
- Reduced global residual from 107 → 66 (38%)

The remaining 66 refs across 56 files are now long-tail (mostly 1–2 per file). Batch 11 can finish Wave 0 plumbing with:
- `server.py` second-pass (5 bracket-access refs)
- A handful of 1-ref files (quick sweep)

Zero regressions · zero new errors · zero behavior change.

No blockers detected.

# Wave 0 — Batch 9 (Long-Tail Plumbing: Resolvers, Repos, Routes, Utility Services) · Audit

**Scope**: 10 files routed through `COLL(...)` with zero behavior change.
**Priority concepts**: master_hub, master_roster, board_cache, live_props, odds_cache, events_cache, player_stats_agg, sync_log, injuries, live_injuries, context_flags, shared caches.
**Rules honored**: no renames · no data moves · no dual-writes · no logic/query changes · no helper utilities added.

---

## Files changed (10)
1. `backend/services/picks/player_stats_resolver.py`
2. `backend/advanced_analytics.py`
3. `backend/repositories/player_repo.py`
4. `backend/repositories/sync_repo.py`
5. `backend/routes/command.py`
6. `backend/routes/mlb_ripple.py`
7. `backend/services/usage_spike_detector.py`
8. `backend/services/props_service.py`
9. `backend/services/injury_service.py`
10. `backend/services/injury_advantage.py`

---

## Exact literals removed → COLL(...) replacements added (13)

### `services/picks/player_stats_resolver.py` — 2
| # | Line | Removed | Added |
|---|---:|---|---|
| 1 | 32 | `self.master_hub = db.nba_master_hub_2026` | `self.master_hub = db[COLL("master_hub", "nba")]` |
| 2 | 33 | `self.master_roster = db.dg_master_roster` | `self.master_roster = db[COLL("master_roster", "nba")]` |

### `advanced_analytics.py` — 1
| # | Line | Removed | Added |
|---|---:|---|---|
| 1 | 362 | `self.db.nba_master_hub_2026.find({...})` | `self.db[COLL("master_hub", "nba")].find({...})` |

### `repositories/player_repo.py` — 1
| # | Line | Removed | Added |
|---|---:|---|---|
| 1 | 19 | `BaseRepository(db.dg_master_roster)` | `BaseRepository(db[COLL("master_roster", "nba")])` |

### `repositories/sync_repo.py` — 1
| # | Line | Removed | Added |
|---|---:|---|---|
| 1 | 19 | `BaseRepository(db.dg_sync_log)` | `BaseRepository(db[COLL.shared("sync_log")])` |

### `routes/command.py` — 2
| # | Line | Removed | Added |
|---|---:|---|---|
| 1 | 126 | `_db["mlb_master_hub_2026"]` | `_db[COLL("master_hub", "mlb")]` |
| 2 | 234 | `db.dg_cached_board.find({"player_name": player_name_regex}, {"_id": 0})` | `db[COLL("board_cache", "nba")].find({"player_name": player_name_regex}, {"_id": 0})` |

> Note: L154 and L195 `"source": "mlb_master_hub_2026"` / `"source": "nba_master_hub_2026"` are JSON **response-value labels** (API contract strings, not DB access) — preserved.

### `routes/mlb_ripple.py` — 1
| # | Line | Removed | Added |
|---|---:|---|---|
| 1 | 169 | `sync_db.mlb_master_hub_2026.find(...)` | `sync_db[COLL("master_hub", "mlb")].find(...)` |

### `services/usage_spike_detector.py` — 1
| # | Line | Removed | Added |
|---|---:|---|---|
| 1 | 86 | `self.db.nba_master_hub_2026.find(...)` | `self.db[COLL("master_hub", "nba")].find(...)` |

### `services/props_service.py` — 1
| # | Line | Removed | Added |
|---|---:|---|---|
| 1 | 76 | `self.master_hub = db.nba_master_hub_2026` | `self.master_hub = db[COLL("master_hub", "nba")]` |

### `services/injury_service.py` — 1
| # | Line | Removed | Added |
|---|---:|---|---|
| 1 | 38 | `self.cached_board = db.dg_cached_board` | `self.cached_board = db[COLL("board_cache", "nba")]` |

### `services/injury_advantage.py` — 2
| # | Line | Removed | Added |
|---|---:|---|---|
| 1 | 87 | `await db.live_scores_cache.find_one({})` | `await db[COLL.shared("live_scores_cache")].find_one({})` |
| 2 | 156 | `db["injuries_normalized"].find(...)` | `db[COLL.shared("injuries")].find(...)` |

**Imports added** (10 files): `from services.config.collection_names import COLL`.

---

## Out-of-scope refs in these files (reported, left untouched)

| File:Line | Literal | Reason left |
|---|---|---|
| `repositories/player_repo.py:20` | `BaseRepository(db.dg_daily_insights)` | Not in registry |
| `routes/command.py:239, 244, 249` | `db.dg_radar_picks`, `db.dg_goblin_vault`, `db.dg_front_lines` | Not in registry |
| `routes/command.py:154, 195` | `"source": "mlb_master_hub_2026"` / `"nba_master_hub_2026"` | Response-value labels (API contract) |
| `services/props_service.py:77` | `self.daily_insights = db.dg_daily_insights` | Not in registry |
| `services/injury_service.py:36, 38, 39` | `dg_injuries`, `dg_daily_insights`, `dg_breaking_news` | Not in registry |
| `advanced_analytics.py:356, 358` (docstrings) | `nba_master_hub_2026` references | Docstring prose — preserved |

All docstring/log-text/comment prose preserved (no text churn).

---

## Hardcoded-reference count

### In the 10 target files (in-scope concepts, code-level only)
| File | Before | After |
|---|---:|---:|
| `player_stats_resolver.py` | 2 | **0** |
| `advanced_analytics.py` | 1 | **0** |
| `player_repo.py` | 1 | **0** |
| `sync_repo.py` | 1 | **0** |
| `command.py` | 2 | **0** |
| `mlb_ripple.py` | 1 | **0** |
| `usage_spike_detector.py` | 1 | **0** |
| `props_service.py` | 1 | **0** |
| `injury_service.py` | 1 | **0** |
| `injury_advantage.py` | 2 | **0** |
| **Batch 9 total** | **13** | **0** |

### Global (entire backend, code-level, archives excluded)
- **Apples-to-apples (same pattern as Batches 1–8 — attribute access only): 59 → 48 refs** (**11-ref reduction**)
- **Broader pattern (now also covers `db["..."]` bracket-access + `_db` variable): 107 refs across 65 files**

> The broader scanner was added because Batch 9 revealed bracket-access patterns (e.g., `_db["mlb_master_hub_2026"]` in routes/command.py, `db["injuries_normalized"]` in injury_advantage.py) that were invisible to prior scans. Those ~59 newly-surfaced refs will drop as subsequent batches process them. **This is honest accounting, not a regression.**

**Top Batch 10 candidates (broader pattern):**
```
  8  routes/master_hub.py                  ← bracket-access heavy
  7  routes/ai_context.py
  7  routes/live.py
  5  server.py                             ← small residual (bracket-access)
  5  routes/cached_data.py                 ← already plumbed for attr-access, has bracket-access
  5  services/injury_triggered_rescore.py
  4  services/board_intelligence_service.py  ← already plumbed for attr-access
  3  routes/ferrari_tiers.py
  3  services/team_stats_service.py
  2  routes/qa_testing.py
```

---

## Resolver parity check

```
master_hub(nba)       = nba_master_hub_2026
master_hub(mlb)       = mlb_master_hub_2026
master_roster(nba)    = dg_master_roster
board_cache(nba)      = dg_cached_board
sync_log(shared)      = dg_sync_log
injuries(shared)      = injuries_normalized
live_scores_cache     = live_scores_cache
```
Physical names unchanged → runtime identical.

---

## Regression results (canonical Wave-0 suite)

```
pytest tests/test_collection_names.py tests/test_hit_rate_canonical.py \
       tests/test_tier_integrity.py tests/test_decision_layer_sengun.py
--------------------------------------------------------------------
80 passed, 1 skipped, 1 warning in 18.80s
```
Matches baseline. **Zero new failures.**

---

## Live endpoint smoke (post full `supervisorctl restart`)

```
backend                                              RUNNING (pid 4046, uptime 0:00:06)

NBA:
/api/v3/ferrari/safe-haven                           HTTP 200 (0.24s)
/api/v3/ferrari/front-lines                          HTTP 200 (0.14s)
/api/v3/ferrari/war-zone                             HTTP 200 (0.14s)
/api/v3/scheduler-status                             HTTP 200 (0.18s)

MLB:
/api/v3/mlb/safe-haven                               HTTP 200 (0.10s)
/api/v3/mlb/front-lines                              HTTP 200 (0.12s)
/api/v3/mlb/war-zone                                 HTTP 200 (0.15s)

Plumbed route paths exercised this batch:
/api/command/search?query=luka&sport=nba             HTTP 200 (0.27s)   ← routes/command.py NBA branch
/api/command/search?query=judge&sport=mlb            HTTP 200 (0.10s)   ← routes/command.py MLB branch (L126 COLL route)
/api/v3/mlb/ripple/alerts                            HTTP 200 (0.14s)   ← routes/mlb_ripple.py (L169 COLL route)

backend.err.log                                      0 errors (no traceback/ImportError/KeyError/AttributeError)
```

Player search for both sports + MLB ripple alerts confirm that `routes/command.py` and `routes/mlb_ripple.py` both resolve `COLL(...)` cleanly at request time.

---

## Recommendation

**➡️ Proceed to Batch 10.**

All 10 Batch 9 files fully clean on in-scope concepts. This batch:
- Completed the long-tail attribute-access plumbing (59 → 48 apples-to-apples)
- Surfaced a new class of hardcoded refs: **bracket-access** (`db["..."]`) and `_db` variable patterns that prior scans missed. The broader scanner now tracks these (107 total refs) and they'll be the focus of the next few batches.

Natural Batch 10 scope (grep-ranked, archives excluded):
- `routes/master_hub.py` (8) · `routes/ai_context.py` (7) · `routes/live.py` (7)
- Second-pass on partially-plumbed files where bracket-access refs were missed:
  `server.py` (5), `routes/cached_data.py` (5), `services/board_intelligence_service.py` (4)

Zero regressions · zero new errors · zero behavior change.

No blockers detected.

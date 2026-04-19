# Wave 0 — Batch 3 (Core Pipeline Plumbing) · Audit

**Scope**: Route 3 core pipeline files through `COLL(...)` with zero behavior change.
**Priority concepts**: `board_cache`, `board_cache_temp`, `master_hub`, `master_roster`, `live_props`, `odds_cache`, `player_stats_agg`, `sync_log`, legacy Ferrari.
**Rules honored**: No renames · no data moves · no dual-writes · no logic/query changes · no helper utilities added · no cleanup outside batch.

---

## Files changed (3)
1. `backend/services/ferrari_tier_service.py`
2. `backend/services/picks_getter_service.py`
3. `backend/services/cached_board_builder_service.py`

---

## Exact literals removed → COLL(...) replacements added (18)

| # | File:Line | Removed | Added |
|---|---|---|---|
| 1 | `ferrari_tier_service.py:184` | `db.dg_cached_board` | `db[COLL("board_cache", "nba")]` |
| 2 | `ferrari_tier_service.py:186` | `db.nba_master_hub_2026` | `db[COLL("master_hub", "nba")]` |
| 3 | `ferrari_tier_service.py:519` | `self.db.nba_master_hub_2026` | `self.db[COLL("master_hub", "nba")]` |
| 4 | `ferrari_tier_service.py:1395` | `self.db.dg_cached_board` | `self.db[COLL("board_cache", "nba")]` |
| 5 | `ferrari_tier_service.py:2337` | `self.db.nba_master_hub_2026.find(` | `self.db[COLL("master_hub", "nba")].find(` |
| 6 | `picks_getter_service.py:246` | `db.dg_cached_board` | `db[COLL("board_cache", "nba")]` |
| 7 | `picks_getter_service.py:249` | `db.dg_sync_log` | `db[COLL.shared("sync_log")]` |
| 8 | `picks_getter_service.py:250` | `db.dg_events_cache` | `db[COLL("events_cache", "nba")]` |
| 9 | `picks_getter_service.py:251` | `db.dg_odds_cache` | `db[COLL("odds_cache", "nba")]` |
|10 | `picks_getter_service.py:254` | `db.nba_master_hub_2026` | `db[COLL("master_hub", "nba")]` |
|11 | `picks_getter_service.py:301` | `self.db.dg_master_roster.find(` | `self.db[COLL("master_roster", "nba")].find(` |
|12 | `cached_board_builder_service.py:49` | `db.dg_cached_board` | `db[COLL("board_cache", "nba")]` |
|13 | `cached_board_builder_service.py:50` | `db.dg_cached_board_temp` | `db[COLL("board_cache_temp", "nba")]` |
|14 | `cached_board_builder_service.py:51` | `db.dg_sync_log` | `db[COLL.shared("sync_log")]` |
|15 | `cached_board_builder_service.py:52` | `db.dg_master_roster` | `db[COLL("master_roster", "nba")]` |
|16 | `cached_board_builder_service.py:390` | `f"{self.db.name}.dg_cached_board_temp"` | `f"{self.db.name}.{COLL('board_cache_temp', 'nba')}"` |
|17 | `cached_board_builder_service.py:391` | `f"{self.db.name}.dg_cached_board"` | `f"{self.db.name}.{COLL('board_cache', 'nba')}"` |
|18 | `cached_board_builder_service.py:762` | `self.db.nba_master_hub_2026.find(` | `self.db[COLL("master_hub", "nba")].find(` |

**Imports added** (3 files): `from services.config.collection_names import COLL`.

---

## Out-of-scope, intentionally NOT touched in this batch

Per user's explicit priority-concept list, these legacy references in the 3 target files were **reported and left alone**:

| File:Line | Literal | Reason left |
|---|---|---|
| `ferrari_tier_service.py:189-193` | `ferrari_safe_haven`, `ferrari_front_lines`, `ferrari_war_zone`, `ferrari_discarded`, `ferrari_scored` | No concept key in registry; "do not add helper utilities" rule |
| `ferrari_tier_service.py:1858` | `ferrari_parlays` | Same — not in registry |
| `ferrari_tier_service.py:2262` | `nba_context_engine` | Explicitly excluded from user's priority-concept list |
| `picks_getter_service.py:241-247` | `dg_radar_picks`, `dg_goblin_vault`, `dg_front_lines`, `dg_parlay_builder`, `dg_goblin_recon`, `dg_player_data`, `dg_daily_insights` | Not in registry, not in priority list |
| `cached_board_builder_service.py:53` | `dg_flagged_players` | Not in registry, not in priority list |

All docstring / log-text / inline-comment prose references untouched (no text churn per strict-refactor rules).

---

## Hardcoded-reference count

### In the 3 target files (in-scope concepts)
| | Before | After |
|---|---:|---:|
| Code-level DB accesses (priority concepts) | 18 | **0** |
| Docstring/log/comment prose | 18 | 18 *(preserved — no text churn)* |

### Global (entire backend, code-level only, all Wave-0 concepts)
- Total residual code-level refs: **170** across 75 files.
- All 3 Batch 3 files verified clean: **0 residual hits** on in-scope concepts.
- Remaining Batch 3 file hits belong to out-of-scope concepts (above table).

**Top residual files (candidates for Batch 4):**
```
 20  server.py
 17  routes_archive/roster.py
  8  scripts/init_database.py
  7  routes/scheduler.py
  7  services/engines/demon_goblin_engine.py
  3  services/roster_service.py
  3  services/rolling_cache_manager.py
  3  services/team_stats_service.py
```

---

## Resolver parity check (behavior invariance)

```
board_cache(nba)      = dg_cached_board
board_cache_temp(nba) = dg_cached_board_temp
master_hub(nba)       = nba_master_hub_2026
master_roster(nba)    = dg_master_roster
events_cache(nba)     = dg_events_cache
odds_cache(nba)       = dg_odds_cache
sync_log(shared)      = dg_sync_log
```
Physical names unchanged → runtime identical. `renameCollection` admin command at L390-391 now reads both source & destination names through `COLL(...)` — atomic swap behavior preserved and will track any future rename-wave flip automatically.

---

## Regression results (canonical Wave-0 suite)

```
pytest tests/test_collection_names.py tests/test_hit_rate_canonical.py \
       tests/test_tier_integrity.py tests/test_decision_layer_sengun.py
--------------------------------------------------------------------
80 passed, 1 skipped, 1 warning in 41.03s
```
Matches baseline. **Zero new failures.**

---

## Live endpoint smoke

```
supervisor                       backend RUNNING (pid 3030, uptime 20m)
/api/v3/ferrari/safe-haven       HTTP 200 (0.13s, 231B)
/api/v3/ferrari/front-lines      HTTP 200 (0.13s, 234B)
/api/v3/ferrari/war-zone         HTTP 200 (0.10s, 225B)
backend.err.log since restart    0 new errors (grep traceback|ImportError|AttributeError → empty)
```
Hot-reload absorbed all 3 files without restart, confirming `FerrariTierService.__init__`, `PicksGetterService.__init__`, and `CachedBoardBuilderService.__init__` execute cleanly under the new `COLL(...)` call sites.

---

## Recommendation

**➡️  Proceed to Batch 4.**

All Batch 3 edits landed cleanly. Zero regressions, zero behavior change, zero new errors. The core pipeline is now fully plumbed — the riskiest files in the codebase (ferrari_tier_service.py, picks_getter_service.py, cached_board_builder_service.py) route every priority-concept DB access through `COLL(...)`.

Natural Batch 4 candidates (await user scoping):
- `server.py` (20 refs) — boot-time index creation + sample player queries
- `routes/scheduler.py` (7 refs) — index creation + stats queries
- `services/engines/demon_goblin_engine.py` (7 refs) — legacy coordinator
- `scripts/init_database.py` (8 refs) — only if scripts are in-scope for plumbing

No blockers detected.

# Wave 0 — Batch 2 (Ingest Layer Plumbing) · Audit

**Scope**: Route 5 ingest-layer files through `COLL(...)` with zero behavior change.
**Targets (concepts)**: `live_props`, `odds_cache`, `events_cache`, `master_roster`, `master_hub`.
**Rules honored**: No renames. No dual-writes. No query/business-logic changes. No docstring churn.

---

## Files changed (5)

1. `backend/services/odds_sync_service.py`
2. `backend/services/sharp_edge_calculator.py`
3. `backend/services/board_intelligence_service.py`
4. `backend/services/bdl_enhanced_data.py`
5. `backend/services/bdl_stats_calculator.py`

---

## Exact literals removed → COLL(...) replacements added

| # | File | Line | Literal removed | Replacement |
|---|------|-----:|---|---|
| 1 | `odds_sync_service.py`         |  32 | `db.dg_live_props`                  | `db[COLL("live_props", "nba")]`     |
| 2 | `odds_sync_service.py`         |  33 | `db.dg_master_roster`               | `db[COLL("master_roster", "nba")]`  |
| 3 | `sharp_edge_calculator.py`     | 189 | `self.db.dg_odds_cache.find_one(`   | `self.db[COLL("odds_cache", "nba")].find_one(` |
| 4 | `board_intelligence_service.py`| 272 | `db.dg_odds_cache.find_one(`        | `db[COLL("odds_cache", "nba")].find_one(` |
| 5 | `board_intelligence_service.py`| 360 | `db.dg_odds_cache.find_one(`        | `db[COLL("odds_cache", "nba")].find_one(` |
| 6 | `bdl_enhanced_data.py`         | 202 | `self.db.nba_master_hub_2026.find(` | `self.db[COLL("master_hub", "nba")].find(` |
| 7 | `bdl_enhanced_data.py`         | 256 | `self.db.nba_master_hub_2026.update_one(` | `self.db[COLL("master_hub", "nba")].update_one(` |
| 8 | `bdl_stats_calculator.py`      |  59 | `db.nba_master_hub_2026.find(`      | `db[COLL("master_hub", "nba")].find(` |
| 9 | `bdl_stats_calculator.py`      | 211 | `db.nba_master_hub_2026.update_one(`| `db[COLL("master_hub", "nba")].update_one(` |

**Imports added** (5 files): `from services.config.collection_names import COLL`.

---

## Hardcoded-reference count (in-scope concepts, 5 target files)

| | Before | After |
|---|---:|---:|
| Code-level DB accesses | 9 | **0** |
| Docstring prose mentions | 2 | 2 *(intentionally preserved — no text churn per strict-refactor rules)* |

Post-edit grep (`dg_live_props|dg_master_roster|dg_odds_cache|dg_events_cache|nba_master_hub_2026`) raw output:
```
services/odds_sync_service.py          (no matches)
services/sharp_edge_calculator.py      (no matches)
services/board_intelligence_service.py (no matches)
services/bdl_enhanced_data.py          (no matches)
services/bdl_stats_calculator.py       4:  docstring
                                      53:  comment
```

---

## Resolver parity check (behavior invariance)

```
live_props(nba)     = dg_live_props
master_roster(nba)  = dg_master_roster
odds_cache(nba)     = dg_odds_cache
master_hub(nba)     = nba_master_hub_2026
```
Physical names unchanged → runtime behavior identical.

Live collection counts via resolver (smoke):
```
live_props     -> dg_live_props          count=2611
master_roster  -> dg_master_roster       count=5000
odds_cache     -> dg_odds_cache          count=182
master_hub     -> nba_master_hub_2026    count=559
board_cache    -> dg_cached_board        count=115  (out-of-scope, unchanged)
```

---

## Regression results (canonical Wave-0 suite)

```
pytest tests/test_collection_names.py tests/test_hit_rate_canonical.py \
       tests/test_tier_integrity.py tests/test_decision_layer_sengun.py
--------------------------------------------------------------------
80 passed, 1 skipped, 1 warning in 16.48s
```

Matches pre-edit baseline (80 pass / 1 skip). **Zero new failures.**

*(Full 648-test suite shows 412 pre-existing failures untouched by this batch — those live outside Wave-0 scope and were failing before this edit.)*

---

## Live endpoint smoke

```
supervisor:       backend RUNNING (pid 3030)
/api/v3/ferrari/safe-haven     HTTP 200  (0.10s, 231B, picks=0 — off-slate, pre-existing)
/api/v3/ferrari/front-lines    HTTP 200  (0.11s, 234B, picks=0 — off-slate, pre-existing)
/api/v3/ferrari/war-zone       HTTP 200  (0.12s, 225B, picks=0 — off-slate, pre-existing)
/api/board-drift-audit?sport=nba  HTTP 401 {"detail":"invalid admin token"} — expected (auth-gated, no regression)
```

Backend error log (last 200 lines): **0 new errors** since batch applied.

---

## Remaining hardcoded-reference count (global, services/ tree)

Wave 0 scanner baseline (`/app/memory/wave0_hardcoded_refs.md`): **429 total refs across 115 files**.

After Batch 1 (prior) + Batch 2 (this): 9 code-level references eliminated in this batch.
Estimated remaining in-scope code-level references across backend: ~420 (docstrings excluded).

Next batches will route the remaining ingest/readmodel/writer files through `COLL(...)` in low-blast-radius groupings dictated by the user.

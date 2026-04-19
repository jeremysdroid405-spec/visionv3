# Wave 0 — Batch 14 (Runtime Closure) · Audit

**Scope**: Plumb the 12 newly-surfaced runtime refs across 6 files (exposed by the broader-scanner's expanded concept coverage in Batch 13). Zero behavior change.
**Scanner**: Broader authoritative scanner (attribute + bracket-access, comments/docstrings stripped).

---

## Files changed (6)

1. `backend/services/spotrac_contract_service.py`
2. `backend/routes/ferrari_tiers.py`
3. `backend/routes/master_hub.py`
4. `backend/services/referee_scraper_service.py`
5. `backend/services/optimized_sync_engine.py`
6. `backend/services/line_movement_tracker.py`

---

## Exact literals removed → COLL(...) replacements added (12 swaps)

| # | File:Line | Removed | Added |
|---|---|---|---|
|  1 | `services/spotrac_contract_service.py:84` | `db["spotrac_contracts_cache"]` | `db[COLL.shared("spotrac_contracts_cache")]` |
|  2 | `services/spotrac_contract_service.py:386` | `db["spotrac_contracts_cache"]` | `db[COLL.shared("spotrac_contracts_cache")]` |
|  3 | `services/spotrac_contract_service.py:456` | `db["spotrac_contracts_cache"]` | `db[COLL.shared("spotrac_contracts_cache")]` |
|  4 | `services/spotrac_contract_service.py:513` | `db["spotrac_contracts_cache"]` | `db[COLL.shared("spotrac_contracts_cache")]` |
|  5 | `routes/ferrari_tiers.py:1328` | `await _db.nba_prop_scores.find(` | `await _db[COLL("prop_scores", "nba")].find(` |
|  6 | `routes/ferrari_tiers.py:1399` | `_db.nba_prop_scores.update_one(` | `_db[COLL("prop_scores", "nba")].update_one(` |
|  7 | `routes/master_hub.py:649` | `await _db.spotrac_contracts_cache.find_one(` | `await _db[COLL.shared("spotrac_contracts_cache")].find_one(` |
|  8 | `routes/master_hub.py:658` | `await _db.spotrac_contracts_cache.find_one(` | `await _db[COLL.shared("spotrac_contracts_cache")].find_one(` |
|  9 | `services/referee_scraper_service.py:94` | `self.referee_assignments = db.referee_assignments` | `self.referee_assignments = db[COLL("referee_assignments", "nba")]` |
| 10 | `services/referee_scraper_service.py:643` | `_referee_service.referee_assignments = db.referee_assignments` | `_referee_service.referee_assignments = db[COLL("referee_assignments", "nba")]` |
| 11 | `services/optimized_sync_engine.py:320` | `db.nba_prop_scores.find(` | `db[COLL("prop_scores", "nba")].find(` |
| 12 | `services/line_movement_tracker.py:27` | `self.line_history = db["line_history"]` | `self.line_history = db[COLL("line_history", "nba")]` |

**Imports added** (3 files): `from services.config.collection_names import COLL`.
*(3 files already had the import from prior batches: `ferrari_tiers.py`, `master_hub.py`, `optimized_sync_engine.py`.)*

---

## Resolver parity check

```
spotrac_contracts_cache (shared) = spotrac_contracts_cache
line_history(nba)                = line_history
referee_assignments(nba)         = referee_assignments
prop_scores(nba)                 = nba_prop_scores
```
Physical names unchanged → runtime identical.

---

## Regression results (canonical Wave-0 suite)

```
pytest tests/test_collection_names.py tests/test_hit_rate_canonical.py \
       tests/test_tier_integrity.py tests/test_decision_layer_sengun.py
--------------------------------------------------------------------
80 passed, 1 skipped, 1 warning in 19.85s
```
Matches baseline. **Zero new failures.**

---

## Live endpoint smoke (post `supervisorctl restart backend`)

```
backend                                       RUNNING (pid 7558, uptime 0:00:07)

/api/v3/ferrari/safe-haven                    HTTP 200 (0.42s)
/api/v3/ferrari/front-lines                   HTTP 200 (0.26s)
/api/v3/ferrari/war-zone                      HTTP 200 (0.37s)
/api/v3/mlb/safe-haven                        HTTP 200 (0.15s)
/api/v3/mlb/front-lines                       HTTP 200 (0.12s)
/api/v3/mlb/war-zone                          HTTP 200 (0.13s)
/api/v3/scheduler-status                      HTTP 200 (0.10s)
/api/v3/master-hub/player/name/Luka%20Doncic  HTTP 200 (0.19s)
/api/live/scores?sport=nba                    HTTP 200 (0.29s)
/api/v3/ferrari/market-moves?sport=nba        HTTP 200 (0.10s)

backend.err.log                               0 errors
```

---

## Hardcoded-reference count (broader scanner · archives/tests/comments excluded)

| Category | Count |
|---|---:|
| **Batch 14 in-scope** (6 files) | **0** ✅ |
| Batch 13 earlier-plumbed files (13) | 0 |
| Registry files (`collection_names.py`, `db_config.py`) | 0 |
| Runtime (all other non-archive, non-script code) | **0** ✅ |
| `scripts/*` (user-deferred) | 14 |
| **Global** | **14** |

---

## Wave 0 — Complete 🏁

### Summary
- **13 batches** of plumbing across **63 files** total (56 + server.py in earlier batches, 13 in Batch 13, 6 in Batch 14)
- **Runtime hardcoded-reference surface: 0** (complete closure of `db.collection` / `db["collection"]` patterns for all registry concepts)
- Only remaining residuals: `scripts/*` (14 refs across 13 maintenance scripts, explicitly user-deferred) and the two registry files themselves (expected by design)

### What this unblocks
- **Wave 1 (shadow-writes)** can now be executed safely: every runtime reader/writer resolves its collection name through `COLL(...)` — flipping one concept's mapping at a time causes an atomic cutover per concept, with no stragglers.

### Deferred (not blockers)
- **Batch 15** — 14 `scripts/*` refs (user said: *"scripts can wait"*)
- **Retire `config/db_config.py`** — legacy parallel registry (requires explicit user approval; out of STRICT REFACTOR MODE scope)

Zero regressions · zero new errors · zero behavior change.

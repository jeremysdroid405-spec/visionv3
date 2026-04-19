# Wave 1 — `live_props` · NBA — Shadow-Write Audit

**Status:** ACTIVE (shadow-writes engaged; observation in progress)
**Concept:** `live_props`
**Sport:** `nba`
**Primary (reads + writes):** `dg_live_props`
**Shadow (writes only, mirror target):** `nba_live_props`
**Backfill strategy:** Option A — rely on natural odds-sync tick (no explicit one-shot)

---

## Scope of this audit

Mandatory pre-flight complete. Registry + 3 writer call-sites flipped.
Monitor wired. First post-wiring natural odds-sync tick executed successfully.
Wave 2 read-flip is NOT yet performed and is pending operator greenlight after
the observation window closes.

## Pre-flight facts (as captured)

| Item | Value |
|------|-------|
| Doc count (primary) | 2,518 → 2,560 after first fresh sync |
| Logical bytes | 2.86 MiB |
| Storage bytes (compressed) | 0.93 MiB |
| Avg doc size | 1,189 B |
| Field count | 32 |
| `_composite_key` coverage | 100 % (2,518 / 2,518) |
| `_composite_key` distinct values | 2,518 (0 duplicate groups) |
| Primary index | `_composite_key_1` — unique: true, sparse: true |
| Pre-existing shadow collection | absent |
| Pre-existing backup collection | absent |

## Pre-Wave-1 wiring changes applied

### 1. Registry: `services/config/collection_names.py`
Added to `_SHADOW_WRITES`:
```python
("live_props", "nba"): "nba_live_props",
```

### 2. `services/odds_sync_service.py` (THE main writer)
```diff
-    self.live_props = db[COLL("live_props", "nba")]
+    self.live_props = COLL.handle(db, "live_props", "nba")
```

### 3. `services/universal_odds_sync.py` (multi-sport writer — NBA shadow-maps only, MLB falls through to raw collection)
```diff
+from services.config.collection_names import COLL
...
-    collection_name = get_collection_name("live_props", sport)
-    collection = self.db[collection_name]
+    collection_name = get_collection_name("live_props", sport)
+    collection = COLL.handle(self.db, "live_props", sport)
```

### 4. `repositories/board_repo.py`
```diff
-    self.live_props = BaseRepository(db[COLL("live_props", "nba")])
+    self.live_props = BaseRepository(COLL.handle(db, "live_props", "nba"))
```

### Deferred to Wave 2 (reader hardcodes — intentionally NOT touched)
- `services/watchers.py:204` — hardcoded `("nba", "dg_live_props")`
- `services/scoring/calibration_store.py:31` — hardcoded `_LIVE_PROPS_BY_SPORT["nba"] = "dg_live_props"`

These remain on the primary today (reads still go there); they must be
flipped to `COLL("live_props","nba")` resolution just before the Wave 2
read-flip so they auto-follow the primary mapping change.

## Observed convergence (first post-wiring sync tick)

Registry effective at **18:30:19 UTC** (post-restart).

### Pre-sync ledger entries (expected gap)
| observed_at (UTC) | primary_count | shadow_count | delta_pct | hash_match_rate |
|---|---|---|---|---|
| 18:31:19 | 2518 | 0 | -100.0 % | 0.0 |
| 18:32:19 | 2518 | 0 | -100.0 % | 0.0 |

### First natural odds-sync tick at **18:33:10 UTC**
- `services.odds_sync_service` completed sync; stored 2,560 deduplicated props.
- ShadowWriter fanned `delete_many({})` + `insert_many(props_list)` to both
  `dg_live_props` and `nba_live_props` concurrently.

### Post-sync ledger entry
| observed_at (UTC) | primary_count | shadow_count | delta_pct | hash_match_rate | alerts |
|---|---|---|---|---|---|
| 18:34:19 | **2560** | **2560** | **0.0 %** | **1.0** | none |

### Direct DB re-verification
```
primary dg_live_props : 2560
shadow  nba_live_props: 2560
delta_pct             : 0.0000 %
hash match (sample=50): 50/50 = 1.0000
shadow index          : _composite_key_1 {unique, sparse} — mirrored correctly
MLB mlb_live_props    : 4944 (unaffected)
```

## ShadowWriter compatibility check

All mutations emitted by the three flipped call-sites are in the supported
`_MUTATION_METHODS` set of `ShadowWriter`:

| Method | Call-sites | Supported |
|---|---|---|
| `delete_many({})` | odds_sync_service, universal_odds_sync, board_repo (via BaseRepository) | ✅ |
| `insert_many(list)` | odds_sync_service, universal_odds_sync, board_repo (via BaseRepository) | ✅ |
| `update_one(..., upsert=True)` | board_repo.upsert_prop | ✅ |
| `create_index("_composite_key", unique=True, sparse=True)` | odds_sync_service | ✅ |
| `count_documents` / `find` / `find_one` (reads) | odds_sync_service circuit breaker, board_repo readers | ✅ delegated to primary only |

No `bulk_write`, `rename`, `drop`, or sub-collection access (`coll["..."]`)
against this concept — confirmed via grep.

## Observational note — `_STABLE_KEY` fallback behavior

The divergence monitor's `_STABLE_KEY` map in
`services/observability/shadow_divergence_monitor.py` does NOT yet include
`live_props`, so it falls back to `_id`. Empirically this still yielded
`hash_match_rate = 1.0` on the first post-sync tick because
pymongo/motor generates client-side `_id`s and mutates the input `props_list`
dicts **before** any I/O is issued, so both the primary and the shadow
receive the same `_id` values when `asyncio.gather` dispatches the two
`insert_many` coroutines.

This is a correct but fragile coincidence. A Wave 1 stretch-cleanup (NOT
performed here, per strict-refactor scope) would add the following entry to
`_STABLE_KEY` so the monitor pairs docs via the registry-declared unique
business key instead:

```python
"live_props": "_composite_key",
```

Deferred and flagged; current convergence proof remains strong because
every post-sync ledger tick shows `delta_pct == 0.0` and
`hash_match_rate == 1.0`.

### Update — `_STABLE_KEY` hardening applied (isolated micro-batch, post-Wave-1)

The fragile `_id`-fallback noted above has been eliminated. Added to
`services/observability/shadow_divergence_monitor.py::_STABLE_KEY`:

```python
"live_props": "_composite_key",
```

After restart, three consecutive monitor ticks confirm the new stable key
is active and convergence remains perfect:

| observed_at (UTC) | primary | shadow | delta_pct | sampled | matched | hash_match_rate | stable_key |
|---|---|---|---|---|---|---|---|
| 18:39:16 | 2560 | 2560 | 0.0 % | 50 | 50 | 1.0 | `_composite_key` |
| 18:40:16 | 2560 | 2560 | 0.0 % | 50 | 50 | 1.0 | `_composite_key` |
| 18:41:16 | 2560 | 2560 | 0.0 % | 50 | 50 | 1.0 | `_composite_key` |

A fresh natural odds-sync tick ran at 18:40:58 UTC (2,560 props stored),
so the 18:41:16 ledger row is a confirmed post-sync measurement under
the new stable key. MLB `mlb_live_props` remains at 4,944 docs — unaffected.

## Wave 2 readiness checklist (pending)

- [ ] Observation window remains clean (delta_pct == 0, hash_match_rate ≥ 0.99) for the operator-chosen duration.
- [ ] Flip reader hardcodes in `watchers.py` and `calibration_store.py` to `COLL(...)` resolution (Wave 2 prep).
- [ ] Flip `_SPORT_COLLECTIONS["live_props"]["nba"]` from `"dg_live_props"` to `"nba_live_props"`.
- [ ] Rename physical `dg_live_props` → `dg_live_props_backup` (atomic `rename` inside DB).
- [ ] Remove `("live_props","nba")` from `_SHADOW_WRITES`.
- [ ] Restart backend; verify readers now see `nba_live_props` directly.
- [ ] Audit backup drop eligibility after observation window.

## Status

**Wave 1 complete for `live_props` · NBA. HALTED pending user greenlight for Wave 2.**

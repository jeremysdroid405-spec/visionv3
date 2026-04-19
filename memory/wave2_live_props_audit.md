# Wave 2 — `live_props` · NBA — Read-Flip + Backup Audit

**Status:** ✅ COMPLETE — shadow phase retired, new primary live, old primary renamed to backup.
**Concept:** `live_props`
**Sport:** `nba`
**Old primary:** `dg_live_props` → renamed to `dg_live_props_backup` (eligible for drop after operator greenlight).
**New primary:** `nba_live_props` (2,567 docs post-flip natural sync).

---

## Edits Applied (7 + DB rename + restart)

### 1. `services/watchers.py`
Added `from services.config.collection_names import COLL` and replaced hardcoded pair:
```diff
-for sport, col_name in [("nba", "dg_live_props"), ("mlb", "mlb_live_props")]:
+for sport in ("nba", "mlb"):
+    col_name = COLL("live_props", sport)
```

### 2. `services/scoring/calibration_store.py`
Removed module-level `_LIVE_PROPS_BY_SPORT` dict; resolve at call time via `COLL(...)`:
```diff
-_LIVE_PROPS_BY_SPORT = {"mlb": "mlb_live_props", "nba": "dg_live_props"}
+from services.config.collection_names import COLL
...
-live_coll = _LIVE_PROPS_BY_SPORT.get(sport)
+try:
+    live_coll = COLL("live_props", sport)
+except KeyError:
+    live_coll = None
```

### 3. `config/db_config.py:70` (legacy `NBA_LEGACY_NAMES`)
```diff
-"live_props": "dg_live_props",
+"live_props": "nba_live_props",
```

### 4. `services/optimized_sync_engine.py:42` (sport-isolation guard)
```diff
-"live_props": "dg_live_props",
+"live_props": "nba_live_props",
```

### 5. `services/config/collection_names.py::_SPORT_COLLECTIONS`
```diff
-"live_props": {"nba": "dg_live_props",
+"live_props": {"nba": "nba_live_props",
                 "mlb": "mlb_live_props"},
```

### 6. `services/config/collection_names.py::_SHADOW_WRITES`
Removed the `("live_props","nba") → "nba_live_props"` entry. Replaced with a Wave-2-complete provenance comment matching the established pattern (`events_cache`, `odds_cache`, `master_roster`, `odds_mapping`, `player_stats_agg`, `referee_assignments`, `line_history`).

### 7. DB rename (atomic)
```
admin.command('renameCollection',
              'pick_vision.dg_live_props',
              to='pick_vision.dg_live_props_backup',
              dropTarget=False)   # → {'ok': 1.0}
```

### 8. Backend restart
`sudo supervisorctl restart backend` — re-materialises `__init__`-captured `self.live_props` handles across the three writer classes. Startup clean. No `live_props`- or `COLL`-related errors.

---

## Post-Flip Verification

### Registry state
```
COLL('live_props','nba')     = 'nba_live_props'
COLL.writes_to('live_props','nba') = ['nba_live_props']
COLL.active_shadows()        = {}
```

### DB state (direct counts)
| Collection | Docs | Latest `synced_at` | Role |
|---|---|---|---|
| `nba_live_props` | **2567** | 2026-04-19T18:55:11Z | NEW PRIMARY (freshly written by natural sync) |
| `dg_live_props_backup` | 2560 | 2026-04-19T18:40:57Z | STALE backup (last sync before rename) |
| `dg_live_props` | — | — | DOES NOT EXIST (renamed away) |
| `mlb_live_props` | 4944 | — | MLB — unaffected |

### Index parity on new primary
```
nba_live_props indexes: ['_composite_key_1', '_id_']
```
`_composite_key_1` preserved with `unique=True, sparse=True` (inherited from Wave 1 shadow-index mirror).

### Endpoint smoke (all 200)
| Endpoint | Result |
|---|---|
| `GET /api/v3/odds/props?sport=nba&limit=1` | 200 — `"collection": "nba_live_props"`, props returned |
| `GET /api/v3/odds/props?sport=mlb&limit=2` | 200 — `"collection": "mlb_live_props"`, unaffected |
| `GET /api/v3/ferrari/safe-haven` | 200 |
| `GET /api/v3/ferrari/front-lines` | 200 |
| `GET /api/v3/scheduler-status` | 200 |
| `GET /api/live/scores` | 200 |

### Natural odds-sync evidence
`services.odds_sync_service [SYNC_ODDS_TO_MONGO] Stored 2567 clean, deduplicated props` at **18:55:12 UTC** — the first post-Wave-2 tick landed on the new primary only (no shadow fan-out; `active_shadows` is empty, monitor ticks are no-ops).

### Shadow monitor state
- `Wave 1 Shadow-Write Divergence Monitor (60s)` still scheduled.
- No concepts currently registered → each tick returns `{"checked": 0, "alerts": 0}` silently. No warnings since 18:32:19 UTC.

---

## Sites Flipped vs Sites Still-Untouched

### Flipped this wave (7)
1. `services/watchers.py:204`
2. `services/scoring/calibration_store.py:31`
3. `config/db_config.py:70`
4. `services/optimized_sync_engine.py:42`
5. `services/config/collection_names.py:_SPORT_COLLECTIONS`
6. `services/config/collection_names.py:_SHADOW_WRITES`
7. DB `renameCollection`

### Still untouched (intentional, out of scope)
- `scripts/ensure_indexes.py:47` — known P2 maintenance-script issue, carries its own ticket.
- `_archive_mlb_v1/*` — archive directory.
- `models/prop.py` / `config/collections.py` / `services/config/collection_names.py` docstring examples — docstrings, not runtime.
- `tests/test_collection_names.py` — does NOT assert the primary-name string; tamper-resistance test, Wave-2-safe.

---

## Rollback (if needed later)

1. `admin.command('renameCollection', 'pick_vision.dg_live_props_backup', to='pick_vision.dg_live_props', dropTarget=False)`
2. Revert edits 1-6 via `git`.
3. Restart backend. System returns to Wave-1 shadow-write topology within one odds-sync tick.

No data-loss path: `nba_live_props` holds the freshest data post-flip; `dg_live_props_backup` holds a pre-rename snapshot. Both remain addressable.

---

## Status

**Wave 2 complete for `live_props` · NBA. Shadow phase retired, backup created, writers + readers on `nba_live_props`, MLB untouched.**
Backup drop is eligible after operator greenlight; recommend ≥ 24 h observation before drop.

# Wave 2 — `board_cache` · NBA — Read-Flip + Backup Audit

**Status:** ✅ COMPLETE (Phase 1 code cutover + Phase 2 atomic rename).
**Concept:** `board_cache`
**Sport:** `nba`
**Old primary:** `dg_cached_board` → renamed to `dg_cached_board_backup` (eligible for drop after operator greenlight).
**New primary:** `nba_cached_board` (114 docs, receiving fresh writes).
**Prior Wave 1 evidence:** `/app/memory/wave1_board_cache_batch1_audit.md` (Batches 1–6).

---

## Phase 1 — Code-first cutover (2026-04-19 20:31 UTC)

Code edits applied (7 files, 8 lines):

| # | File:line | Change |
|---|---|---|
| 1 | `services/watchers.py:311` | Hardcoded `"dg_cached_board"`/`"mlb_cached_board"` → `COLL("board_cache", sport)` |
| 2 | `routes/ferrari_tiers.py:4094` | f-string `_db[f"{sport}_cached_board"]` → `_db[COLL("board_cache", sport)]` (this fixed the latent NBA-empty-read bug flagged in Wave 2 pre-flight) |
| 3 | `routes/scheduler.py:143` | Stale response label `"dg_cached_board"` → `"nba_cached_board"` |
| 4 | `config/db_config.py:69` | `NBA_LEGACY_NAMES["cached_board"]` → `"nba_cached_board"` |
| 5 | `services/optimized_sync_engine.py:41` | `SPORT_COLLECTION_MAP["nba"]["cached_board"]` → `"nba_cached_board"` |
| 6 | `services/config/collection_names.py:_SPORT_COLLECTIONS` | `"board_cache": {"nba": "nba_cached_board", "mlb": "mlb_cached_board"}` |
| 7 | `services/config/collection_names.py:_SHADOW_WRITES` | `("board_cache","nba")` entry removed; Wave-2-complete provenance comment added |

Observation cycle at 20:35:17 UTC confirmed write-routing: `nba_cached_board` received fresh writes, `dg_cached_board` frozen with pre-restart stale timestamps.

## Phase 2 — Atomic DB rename (2026-04-19 20:39 UTC)

```
admin.command('renameCollection',
              'pick_vision.dg_cached_board',
              to='pick_vision.dg_cached_board_backup',
              dropTarget=False)   # → {'ok': 1.0}
```

Pre-rename: 114 docs on both `dg_cached_board` and `nba_cached_board`. Post-rename: `dg_cached_board_backup` = 114 (stale), `nba_cached_board` = 114 (live), `dg_cached_board` GONE.

### Backend restart at 20:40:19 UTC
`[COLL_HEALTH] Audit complete — 0 warnings, 39/54 pair(s) canonical, 15 pending` — the `CANONICAL_BLEED` warning that fired during Wave 1 has fully cleared.

### Post-restart registry assertions (all 7 passed)
```
COLL("board_cache","nba")             = 'nba_cached_board'
COLL.writes_to("board_cache","nba")   = ['nba_cached_board']
COLL.active_shadows()                 = {}
COLL("board_cache","mlb")             = 'mlb_cached_board'   (MLB isolation)
COLL("live_props","nba")              = 'nba_live_props'     (prior wave)
NBA_LEGACY_NAMES.get_collection_name  = 'nba_cached_board'
SPORT_COLLECTION_MAP["nba"]           = 'nba_cached_board'
```

### Natural sync verification at 20:43:15 UTC
Fresh `[SYNC_ODDS_TO_MONGO] Stored 2362 clean, deduplicated props` →
`[CACHED_BOARD] Bulk upsert completed - 114 players`.

| Collection | Docs | Latest `synced_at` | Role |
|---|---|---|---|
| `nba_cached_board` | **114** | **2026-04-19T20:43:15Z** (FRESH — post-restart sync) | NEW PRIMARY |
| `dg_cached_board_backup` | 114 | 2026-04-19T20:18:14Z (FROZEN — pre-Phase-1-restart) | backup |
| `dg_cached_board` | — | — | DOES NOT EXIST |
| `mlb_cached_board` | 306 | — | MLB, unaffected |

Random-player freshness check (3/3 show `fresher=new_primary`):
```
Rui Hachimura         new=20:43:15   backup=20:18:14  fresher=new_primary
Josh Okogie           new=20:43:15   backup=20:18:14  fresher=new_primary
Wendell Carter Jr.    new=20:43:15   backup=20:18:14  fresher=new_primary
```

`dg_cached_board` did **NOT** get recreated by the post-rename sync tick — writers target `nba_cached_board` only, and MongoDB does not implicitly recreate a renamed-away collection unless a writer explicitly addresses its legacy name. None do.

### Endpoint smoke (all 200)
- `GET /api/v3/ferrari/safe-haven` | `/front-lines` | `/war-zone`
- `GET /api/v3/scheduler-status`
- `GET /api/live/scores`
- `GET /api/v3/odds/props?sport=nba&limit=1`

### Regression / isolation
```
mlb_cached_board : 306 docs   (unaffected)
nba_live_props   : 2362 docs  (prior wave, unaffected)
mlb_live_props   : 4944 docs  (unaffected)
```

### Monitor behavior
`COLL.active_shadows() == {}` → monitor ticks are silent no-ops. **No `SHADOW_DIVERGENCE` warnings since Phase-2 restart.**

---

## Status

**board_cache · NBA migration: COMPLETE.** All writers and readers resolve through the canonical registry to `nba_cached_board`. Legacy `dg_cached_board` renamed to `dg_cached_board_backup` and is eligible for drop after operator greenlight (recommend ≥ 24 h observation before drop).

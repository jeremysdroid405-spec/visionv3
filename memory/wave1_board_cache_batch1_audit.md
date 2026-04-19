# Wave 1 — `board_cache` · NBA — Batch 1 Audit

**Status:** ACTIVE (Batch 1/N — main builder + repository only)
**Concept:** `board_cache`
**Sport:** `nba`
**Primary (reads + writes):** `dg_cached_board`
**Shadow (writes-only, mirror target):** `nba_cached_board`
**Backfill strategy applied:** Option B — explicit one-shot pre-populate + new `player_name_unique` index on shadow

## Scope of Batch 1

Only two writer files flipped in this batch. All other writers (13 peripheral
sites enumerated in the pre-flight report) remain on `db[COLL(...)]` and do NOT
fan out to the shadow yet — they will be flipped in subsequent mini-batches.

- ✅ `services/cached_board_builder_service.py` (main builder — authoritative
  for bulk-replace semantics)
- ✅ `repositories/board_repo.py` (BoardRepository facade)
- 🚫 Peripheral writers (context_badge, intel_briefing, game_lock,
  board_intelligence, injury_service, optimized_sync_engine, sync_orchestration,
  photo, picks_getter, propvision_oracle, adaptive_sync_engine, social_signal,
  demon_goblin handle, qa_testing) — deferred to later batches
- 🚫 Reader hardcodes (`watchers.py:311`, `ferrari_tiers.py:4094` f-string)
  — Wave 2
- 🚫 Split registries (`config/db_config.py`, `optimized_sync_engine.py::SPORT_COLLECTION_MAP`)
  — Wave 2

## Pre-flight facts (as captured)

| Item | Value |
|------|-------|
| Doc count (primary) | 121 |
| Logical / storage bytes | 16.53 MiB / 3.04 MiB |
| Avg doc size | ~140 KiB (fat: ~32 props × 78 fields) |
| `player_name` coverage | 100 % (121/121) |
| `player_name` distinct | 121 (0 duplicates) |
| Primary indexes | `_id_` only (no business-key index) |
| Shadow collection pre-Batch-1 | absent |
| Backup collection pre-Batch-1 | absent |

## Registry + Monitor Changes

### `services/config/collection_names.py::_SHADOW_WRITES`
```diff
+("board_cache", "nba"): "nba_cached_board",
```
with Batch-1 provenance comment explaining atomic-rename bypass risk.

### `services/observability/shadow_divergence_monitor.py::_STABLE_KEY`
```diff
+"board_cache": "player_name",
```

## Writer Call-Site Flips

### `services/cached_board_builder_service.py:51`
```diff
-self.cached_board = db[COLL("board_cache", "nba")]
+self.cached_board = COLL.handle(db, "board_cache", "nba")
```
(`self.cached_board_temp` on line 52 untouched — different concept, out of
scope.)

### `repositories/board_repo.py:20`
```diff
-self.cached_board = BaseRepository(db[COLL("board_cache", "nba")])
+self.cached_board = BaseRepository(COLL.handle(db, "board_cache", "nba"))
```

## Pre-populate Result (Option B)

```
[READ]     dg_cached_board docs fetched        : 121
[READ]     distinct player_name coverage       : 121 / 121 (100%)
[POPULATE] inserted into nba_cached_board      : 121
[INDEX]    created                              : player_name_unique (unique=True)
[VERIFY]   primary=121  shadow=121  parity=True
[VERIFY]   shadow indexes                       : ['_id_', 'player_name_unique']
```

Note: `player_name_unique` is a NEW constraint created on the shadow ONLY.
The primary (`dg_cached_board`) still has no `player_name` index today. If
Batch-1 observation is clean, we can consider promoting the unique index to
the primary at Wave 2 cutover time (no rename conflict — the shadow becomes
the new primary).

## Observational Evidence

### Monitor ledger (pre-sync)
| observed_at (UTC) | primary | shadow | delta_pct | sampled | matched | hash_match_rate | stable_key |
|---|---|---|---|---|---|---|---|
| 19:15:21 | 121 | 121 | 0.0 % | 50 | 50 | 1.0 | `player_name` |
| 19:16:21 | 121 | 121 | 0.0 % | 50 | 50 | 1.0 | `player_name` |

### First post-wiring natural sync tick
- 19:17:30 UTC — `[SYNC_ODDS_TO_MONGO] Stored 2582 clean, deduplicated props`
- 19:17:43 UTC — `[CACHED_BOARD] Atomic rename failed, using bulk upsert:
  renameCollection may only be run against the admin database.` (expected in this env)
- 19:17:43 UTC — `[CACHED_BOARD] Bulk upsert completed - 121 players`
- Path B (bulk-upsert fallback) ran as expected; Path A (atomic rename) blocked
  by env permissions — the exact scenario pre-flight anticipated.

### Writer fan-out proof
Post-sync inspection of `Nikola Jokic` doc:
```
[PRIMARY] synced_at = 2026-04-19T19:17:29.203137+00:00  rank=1
[SHADOW]  synced_at = 2026-04-19T19:17:29.203137+00:00  rank=1
```
Identical timestamp on both sides — the `bulk_write` inside `cached_board_builder_service`
fanned out to `nba_cached_board` via `ShadowWriter._make_fanout`. ✅

### Post-sync ledger
| observed_at (UTC) | primary | shadow | delta_pct | sampled | matched | hash_match_rate | alerts |
|---|---|---|---|---|---|---|---|
| 19:18:21 | 121 | 121 | 0.0 % | 50 | 50 | 1.0 | — |

### Manual 20-sample hash check (independent of monitor)
```
[MANUAL HASH 20-sample] matched=20 missed=0
```

### Peripheral-writer convergence note
Only the main builder is wired for shadow fan-out in Batch 1. Peripheral
writers (`context_badge`, `intel_briefing`, `game_lock`, etc.) still write
to the primary only. Because the main builder does a full bulk-replace of
player docs every sync tick — overwriting whatever the peripheral writers
mutated between ticks — convergence can still be achieved at coarse
granularity. However, fields mutated by peripheral writers AFTER the main
builder tick (e.g., vision intel added mid-cycle) will transiently live
only on the primary until the NEXT main-build cycle overwrites both sides
with reconciled data.

Observable signature: for samples taken just after a main-build tick,
`hash_match_rate == 1.0`. Samples taken between builds may occasionally
show mismatches, especially on intel-enriched players. This is expected
and will be eliminated in subsequent batches as each peripheral writer
is flipped.

## Endpoint Smoke (all 200)

- `GET /api/v3/ferrari/safe-haven` → 200 (pipeline present)
- `GET /api/v3/ferrari/front-lines` → 200
- `GET /api/v3/ferrari/war-zone` → 200
- `GET /api/v3/scheduler-status` → 200
- `GET /api/live/scores` → 200
- `GET /api/v3/odds/props?sport=nba&limit=1` → 200 (`"collection": "nba_live_props"`)

## Isolation

```
mlb_cached_board : 306 docs (unaffected)
dg_cached_board  : 121 docs (primary, still serves reads)
nba_cached_board : 121 docs (shadow, writes-only, fresh synced_at)
```

## Health-Check Warning (informational)

The pre-existing `services.board.health_check::COLL_HEALTH` emits:
```
[COLL_HEALTH] CANONICAL_BLEED nba.cached_board:
  legacy 'dg_cached_board'=121 docs AND canonical 'nba_cached_board'=121 docs
  both populated; reads resolve to legacy
```

This is **expected and correct** during Wave 1 — both collections ARE
populated; reads DO resolve to legacy (per shadow-write semantics). No
action required; this warning will naturally resolve at Wave 2 cutover
when `_SPORT_COLLECTIONS["board_cache"]["nba"]` flips.

## Batch 2 Recommendation

Flip the next most-active writer cluster in a single mini-batch:

- `services/engines/intel_briefing_engine.py:55` (Gemini vision intel updates)
- `services/engines/game_lock_engine.py:51` (game-lock flagging)
- `services/context_badge_service.py:146` (badge sync)

Rationale:
1. These three writers run on the inter-build cycle (seconds to minutes)
   and are responsible for the non-convergence signature noted above.
2. Same mutation shape (`update_one({"player_name":…}, {"$set":…})`) — a
   consistent, easy-to-audit diff.
3. All three use `db[COLL("board_cache","nba")]` pattern — no adapter
   indirection, no legacy registry dependency.
4. After Batch 2, convergence should be clean at all observation points,
   not just post-main-build.

Deferred for Batch 3 and later:
- Adapter-routed writers (`propvision_oracle_service` — shared cross-sport
  helper needs audit)
- `services/optimized_sync_engine.py:1201` — depends on its internal
  `get_collection_name`/`SPORT_COLLECTION_MAP` (parallel split registry)
- `services/engines/adaptive_sync_engine.py` — 6+ call-sites using a
  name-string cache; refactor to handle cache
- QA-only endpoints in `routes/qa_testing.py`
- All reader hardcodes (Wave 2 concern, not Wave 1)

## Status

**Batch 1 complete. Observation green across 3 ticks (2 pre-sync, 1 post-sync).
Halted pending greenlight for Batch 2.**

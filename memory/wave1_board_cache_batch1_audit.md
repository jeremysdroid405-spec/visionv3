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

---

# Batch 2 (Inter-build writers) — applied 2026-04-19 19:26 UTC

## Scope

Flipped three single-handle `__init__` assignments only. No logic, no filter,
no payload, no sub-collection, no reader, and no secondary file touched.

### Files changed
- `services/engines/intel_briefing_engine.py`
- `services/engines/game_lock_engine.py`
- `services/context_badge_service.py`

### Diff (identical three times)
```diff
-self.cached_board = db[COLL("board_cache", "nba")]
+self.cached_board = COLL.handle(db, "board_cache", "nba")
```

## Post-Batch-2 Evidence

### Writer fan-out
Post-natural-sync-cycle @ 19:29:19 UTC — `Nikola Jokic` sample (representative
of all 121 docs):

| field | primary | shadow | match |
|---|---|---|---|
| `synced_at` | `2026-04-19T19:29:19.693400+00:00` | `2026-04-19T19:29:19.693400+00:00` | ✅ |
| `badges` | None | None | ✅ |
| `active_badges` | None | None | ✅ |
| `game_locked` | None | None | ✅ |
| `intel_briefing` | None | None | ✅ |
| `vision_summary` | None | None | ✅ |

(Inter-build writers did not fire a mutation during this particular
observation window — no active games in lock threshold, no badge-sync
tick, no vision enrichment triggered. The field-level match confirms
there is NO stale state on either side; if these writers had fired
pre-Batch-2 and only written to primary, the full-doc hash sweep
below would fail.)

### Ledger — 6 consecutive clean ticks, all post-Batch-2 and spanning a natural odds-sync + rebuild
| observed_at (UTC) | primary | shadow | delta_pct | sampled | matched | hash_match_rate |
|---|---|---|---|---|---|---|
| 19:23:21 | 121 | 121 | 0.0 % | 50 | 50 | 1.0 |
| 19:25:21 | 121 | 121 | 0.0 % | 50 | 50 | 1.0 |
| 19:27:35 | 121 | 121 | 0.0 % | 50 | 50 | 1.0 |
| 19:28:35 | 121 | 121 | 0.0 % | 50 | 50 | 1.0 |
| 19:29:35 **(post-sync tick)** | 121 | 121 | 0.0 % | 50 | 50 | 1.0 |
| 19:30:35 | 121 | 121 | 0.0 % | 50 | 50 | 1.0 |

Zero alerts. Zero drift.

### FULL-DOC HASH SWEEP (all 121 players, not just 50-sample)
```
matched=121  missed=0  skipped=0
```
Every single player doc in `dg_cached_board` hashes identically (minus
volatile fields) to its twin in `nba_cached_board`. This is the strongest
convergence signature possible — it includes fields mutated by Batch 2
writers even when they haven't fired during this window, because any
pre-existing stale state would surface as a mismatch.

### Regression
```
mlb_cached_board : 306 docs (unaffected)
nba_live_props   : 2582 docs (prior-wave primary — unaffected)
mlb_live_props   : 4944 docs (unaffected)
```

### Endpoint smoke
| Endpoint | Status |
|---|---|
| `GET /api/v3/ferrari/safe-haven` | 200 |
| `GET /api/v3/ferrari/front-lines` | 200 |
| `GET /api/v3/ferrari/war-zone` | 200 |
| `GET /api/v3/scheduler-status` | 200 |
| `GET /api/live/scores` | 200 |
| `GET /api/v3/odds/props?sport=nba&limit=1` | 200 |

## Batch 3 recommendation (NOT yet approved)

The next natural grouping is the **non-adapter, non-registry-dependent
secondary writers** — single-handle swaps similar to Batch 2:

1. `services/injury_service.py:38` (teammate cache invalidation `update_many`)
2. `services/photo_service.py:33` (photo URL `update_many`)
3. `services/board_intelligence_service.py:64` (board intel `update_one`)
4. `services/sync_orchestration_service.py:36` (board-meta persistence)
5. `services/engines/social_signal_engine.py` (inline reads — audit whether
   there are any writers here; currently seeing only reads via
   `db[COLL(...)]` at lines 83 and 153)
6. `services/engines/demon_goblin_engine.py:436` (single `__init__` handle,
   already read-dominant)
7. `services/picks_getter_service.py:248` (service handle — powers 30+
   internal call-sites, mostly reads; writer at line 2928 will auto-follow)

Rationale: same low-risk mini-batch shape (single-handle swaps), still no
adapter indirection, still no legacy-registry coupling, still primary-only
sources of potential drift. After Batch 3 we are within 3 writer clusters
of full Wave-1 coverage.

Deferred beyond Batch 3:
- `services/optimized_sync_engine.py:1201` — depends on internal
  `SPORT_COLLECTION_MAP` (parallel split registry) — Batch 4
- `services/propvision_oracle_service.py:713` — adapter-routed shared
  helper `_get_collection` — needs sport-awareness audit first — Batch 5
- `services/engines/adaptive_sync_engine.py` — 6+ call-sites using a
  name-string cache; refactor to cache the handle — Batch 6
- `routes/qa_testing.py` writer endpoints — Batch 7 (low priority)
- All reader hardcodes / registry cleanups — Wave 2

## Status

**Batch 2 complete. 6 consecutive clean ledger ticks, FULL-DOC hash sweep
121/121 matched, regression clean. Halted pending greenlight for Batch 3.**

---

# Batch 3 (Secondary writers + 1 shared-module writer + 1 engine) — applied 2026-04-19 19:39 UTC

## Scope

Flipped 5 single-handle `__init__` assignments and 6 inline call-sites (4 in
`board_intelligence_service`, 2 in `social_signal_engine`). No logic, filters,
or payloads touched. No adapters, legacy registries, `SPORT_COLLECTION_MAP`,
`propvision_oracle_service`, or `optimized_sync_engine` touched.

### Files changed
1. `services/injury_service.py` (handle)
2. `services/photo_service.py` (handle)
3. `services/board_intelligence_service.py` (4 inline usages)
4. `services/sync_orchestration_service.py` (handle, attribute name `dg_cached_board`)
5. `services/engines/demon_goblin_engine.py` (handle, inline trailing comment preserved)
6. `services/picks_getter_service.py` (handle, inline trailing comment preserved)
7. `services/engines/social_signal_engine.py` (2 inline usages)

### Diff (consistent pattern across all sites)
```diff
-db[COLL("board_cache", "nba")]
+COLL.handle(db, "board_cache", "nba")
```

## Post-Batch-3 Evidence

### Stale-cleanup fan-out proof — STRONGEST SIGNAL YET
The natural odds-sync at 19:42:43 caused a legitimate player-set churn:
121 → 107 (14 players stale-deleted via
`delete_many({"player_name": {"$nin": …}, "synced_at": {"$lt": …}})`).

| side | count before sync | count after sync | delta |
|---|---|---|---|
| primary `dg_cached_board` | 121 | **107** | -14 |
| shadow  `nba_cached_board` | 121 | **107** | -14 |
| only-primary players | — | **0** | — |
| only-shadow players | — | **0** | — |

The stale-cleanup fanned out identically via `ShadowWriter._make_fanout →
asyncio.gather(primary.delete_many, shadow.delete_many)`. This is the first
observation tick in the entire migration program that exercised a DELETE
fan-out on a non-trivial doc count.

### Field-level parity samples (3 random post-sync survivors)

| player | synced_at | headshot_url | photo_url | rank | injured_teammates |
|---|---|---|---|---|---|
| Donovan Mitchell | primary/shadow identical | `.../1628378.png` matched | `.../1628378.png` matched | 30/30 | [] / [] |
| Luke Kornet | primary/shadow identical | `.../1628436.png` matched | `.../1628436.png` matched | 96/96 | [] / [] |
| Shaedon Sharpe | primary/shadow identical | `.../1631101.png` matched | `.../1631101.png` matched | 69/69 | [] / [] |

All fields (Batch-3-relevant and otherwise) matched exactly on both sides.

### FULL-DOC HASH SWEEP (all 107 post-churn players)
```
matched = 107
missed  =   0
skipped =   0
```

### Ledger — 6 consecutive clean ticks spanning the player-set churn
| observed_at (UTC) | primary | shadow | delta_pct | sampled | matched | hash_match_rate |
|---|---|---|---|---|---|---|
| 19:37:35 | 121 | 121 | 0.0 % | 50 | 50 | 1.0 |
| 19:38:35 | 121 | 121 | 0.0 % | 50 | 50 | 1.0 |
| 19:40:42 | 121 | 121 | 0.0 % | 50 | 50 | 1.0 |
| 19:41:42 | 121 | 121 | 0.0 % | 50 | 50 | 1.0 |
| 19:42:42 | 121 | 121 | 0.0 % | 50 | 50 | 1.0 |
| **19:43:42 (post-sync + stale-clean)** | **107** | **107** | **0.0 %** | **50** | **50** | **1.0** |

No `SHADOW_DIVERGENCE` warnings at any point.

### Endpoint smoke (all 200)
- `/api/v3/ferrari/safe-haven`
- `/api/v3/ferrari/front-lines`
- `/api/v3/ferrari/war-zone`
- `/api/v3/scheduler-status`
- `/api/live/scores`
- `/api/v3/odds/props?sport=nba&limit=1`

### Regression
```
mlb_cached_board : 306 docs  (unaffected)
nba_live_props   : 2502 docs (prior-wave primary — fresh sync, still primary-only, no regression)
mlb_live_props   : 4944 docs (unaffected)
```

## Batch 4 recommendation (NOT yet approved)

Three independent clusters remain. Recommend one at a time in the following
priority order:

### Batch 4 (proposed)
`services/optimized_sync_engine.py:1201` — writer using `db[cached_board_collection]`
where `cached_board_collection` is the string returned by the engine's INTERNAL
`get_collection_name(target_sport, "cached_board")` (which reads from
`SPORT_COLLECTION_MAP` at line 41, still `"dg_cached_board"` today).

The surgical flip: replace the single `db[cached_board_collection].update_one(...)`
call at line 1201 with `COLL.handle(db, "board_cache", target_sport).update_one(...)`.

Leave `SPORT_COLLECTION_MAP` as-is — it will be flipped at Wave 2. The
`cached_board_collection` local variable can remain for use in log-strings and
the existing `validate_sport_isolation` check (it's used as a STRING, not a
handle, in those contexts).

- Blast radius: 1 call-site in 1 file.
- Risk: low. Same `update_one({"player_name":…}, {"$set": enrichment_payload})`
  shape already validated in Batch 2 and Batch 3.

### Batch 5 (deferred, requires audit first)
`services/propvision_oracle_service.py:713` — the handle comes from
`self._get_collection("cached_board")` adapter. Needs pre-flight audit of
whether this adapter is shared across NBA and MLB, and how its sport context
is plumbed. Should NOT be attempted without that audit.

### Batch 6 (deferred, refactor-heavier)
`services/engines/adaptive_sync_engine.py` — caches a collection-name STRING
at `__init__` (line 116: `self.cached_board_collection = COLL("board_cache", "nba")`)
and uses `self.db[self.cached_board_collection]` across 6+ sites (lines 949,
1056, 1089, 1164, 1373). To shadow-route, the cache must be changed from a
string to a handle, and each call-site from `self.db[self.cached_board_collection]`
to `self.cached_board_handle`. Higher-risk refactor — recommend its own batch
with a pre-change static audit.

### Batch 7 (low priority)
`routes/qa_testing.py` — QA-only endpoints. Defer until all production
writers are flipped.

### Wave 2 housekeeping (not in Wave 1 scope)
- Reader hardcodes: `services/watchers.py:311`, `routes/ferrari_tiers.py:4094` (f-string)
- Legacy registry: `config/db_config.py::NBA_LEGACY_NAMES["cached_board"]`
- Sport-isolation registry: `services/optimized_sync_engine.py::SPORT_COLLECTION_MAP["nba"]["cached_board"]`
- Canonical registry flip: `_SPORT_COLLECTIONS["board_cache"]["nba"]` → `"nba_cached_board"`
- Shadow retirement: remove `("board_cache","nba")` from `_SHADOW_WRITES`
- DB rename: `dg_cached_board` → `dg_cached_board_backup`

## Status

**Batch 3 complete. Stale-cleanup DELETE fan-out validated. 121→107 churn
mirrored exactly. FULL-DOC hash sweep 107/107 matched. Regression clean.
Halted pending greenlight for Batch 4.**

---

# Batch 4 (optimized_sync_engine single writer) — applied 2026-04-19 19:46 UTC

## Scope

Surgical one-line flip of the `_persist_enriched_picks` writer in
`services/optimized_sync_engine.py:1201`. No other lines touched. No
changes to `cached_board_collection` string caching, `SPORT_COLLECTION_MAP`,
`validate_sport_isolation`, log strings, or readers. `COLL` import already
present at line 24; no import changes.

### File changed
- `services/optimized_sync_engine.py`

### Diff
```diff
-            result = await db[cached_board_collection].update_one(
+            result = await COLL.handle(db, "board_cache", target_sport).update_one(
                 {"player_name": player_name},
                 {"$set": full_update},
             )
```

## Post-Batch-4 Evidence

### Runtime exercise of the flipped writer
A manual `POST /api/v3/ferrari/rebuild?sport=nba&use_optimized=true` was
dispatched to the backend to trigger the optimized_sync_engine pipeline.
Logs show the full pipeline ran to completion:

```
[OPTIMIZED_SYNC] 🔒 SPORT-EXCLUSIVE MODE: NBA
[OPTIMIZED_SYNC] Step 0 (Game Logs): 59.21s - synced 550 players
[OPTIMIZED_SYNC] Step 1 (Global Cache): 0.24s
[OPTIMIZED_SYNC] Step 2 (Ferrari Pipeline): 68.27s — 30 picks
[OPTIMIZED_SYNC] Step 3 (Collect & Enrich): 0.00s
[OPTIMIZED_SYNC] Collected 0 NBA picks for AI summary generation
[OPTIMIZED_SYNC] Step 4 (Vision Intel Check): 0.00s
[OPTIMIZED_SYNC] Step 5 (Persist Enriched): 0.00s
[OPTIMIZED_SYNC] Step 6 (Tier Update): SKIPPED — handled by ferrari_tier_service
[OPTIMIZED_SYNC] 🏁 NBA Pipeline complete in 127.73s
```

Step 5 (the step containing the flipped writer) exited at 0.00s because
`Collected 0 NBA picks for AI summary generation` — the enrichment
collector returned an empty list, so the `update_one` call at line 1201
was not iterated. This is runtime behavior, not a code-path issue.

**Strict-refactor invariant**: the flip is a pure target-expression
replacement (from `db[name_string]` to `COLL.handle(db, "board_cache",
target_sport)`), both of which return objects that expose `update_one(...)`
with identical filter/payload semantics. The ShadowWriter returned by
`COLL.handle` is the same instance used by Batch 1-3 writers, which have
already exercised `update_one`, `update_many`, `insert_many`, `delete_many`,
and `bulk_write` successfully through this migration. When this writer
next fires (on a Ferrari rebuild that yields non-empty enrichment), it
will fan out by the same mechanism.

Additional safety note on sport routing: the flipped expression uses
`target_sport` (not hardcoded "nba"), preserving cross-sport correctness.
- `target_sport="nba"` → `COLL.handle` returns ShadowWriter →
  fans to `dg_cached_board` + `nba_cached_board`.
- `target_sport="mlb"` → `COLL.handle` returns raw `mlb_cached_board`
  collection (no shadow map for MLB) → unchanged MLB behavior.

### Field-level parity samples (post-main-build at 19:50:11)

| player | synced_at | intel_briefing | vision_summary | is_vision_enriched | rank | props-array length | keys |
|---|---|---|---|---|---|---|---|
| Aaron Gordon | identical | None/None | None/None | None/None | 33/33 | 27/27 match | 36/36 same set |
| Ajay Mitchell | identical | None/None | None/None | None/None | 46/46 | 25/25 match | 36/36 same set |

All field values match. Field-count and key-set identical. Props-array length identical.

### FULL-DOC HASH SWEEP
```
matched = 107
missed  =   0
skipped =   0
```

### Ledger — 6 consecutive clean ticks post-Batch-4
| observed_at (UTC) | primary | shadow | delta_pct | sampled | matched | hash_match_rate |
|---|---|---|---|---|---|---|
| 19:49:04 | 107 | 107 | 0.0 % | 50 | 50 | 1.0 |
| 19:50:04 | 107 | 107 | 0.0 % | 50 | 50 | 1.0 |
| 19:51:03 | 107 | 107 | 0.0 % | 50 | 50 | 1.0 |
| 19:52:04 | 107 | 107 | 0.0 % | 50 | 50 | 1.0 |
| 19:53:03 | 107 | 107 | 0.0 % | 50 | 50 | 1.0 |
| 19:54:03 | 107 | 107 | 0.0 % | 50 | 50 | 1.0 |

### Endpoint smoke (all 200)
- `/api/v3/ferrari/safe-haven`, `/front-lines`, `/war-zone`
- `/api/v3/scheduler-status`
- `/api/live/scores`
- `/api/v3/odds/props?sport=nba&limit=1`

### Regression
```
mlb_cached_board : 306 docs  (unaffected)
nba_live_props   : 2502 docs (prior-wave primary — unaffected)
mlb_live_props   : 4944 docs (unaffected)
```

## Next-step recommendation

Two distinct clusters remain in the Wave 1 writer surface:

### Option A — Batch 5: `propvision_oracle_service` (requires audit first)
`services/propvision_oracle_service.py:713` uses `self._get_collection("cached_board")`
— an adapter method. The pre-flight blocker is: does this adapter plumb
sport context per-instance, or is it shared cross-sport? If shared,
flipping to `COLL.handle(db, "board_cache", "nba")` would break MLB
callers, and we would need per-call sport resolution.

### Option B — Batch 6: `adaptive_sync_engine` (name-string cache refactor)
`services/engines/adaptive_sync_engine.py` caches `COLL("board_cache","nba")`
as a STRING at `__init__` (line 116) and uses it via
`self.db[self.cached_board_collection]` in 6+ writer/reader call-sites
(lines 949, 1056, 1089, 1164, 1373). To route through the ShadowWriter,
either:
1. Add a parallel `self.cached_board_handle = COLL.handle(db, "board_cache","nba")`
   and rewrite the 6+ call-sites from `self.db[self.cached_board_collection]`
   to `self.cached_board_handle`, OR
2. Keep the name-cache and rewrite each call-site to resolve
   `COLL.handle(...)` inline.

Option 1 is cleaner. Either way, a 6+-site rewrite in one file is a
"medium" mini-batch — larger than Batch 2 and 3 but still single-file.

### Recommended order
**Batch 5 first** — but ONLY after running a 10-minute read-only audit
of `_get_collection` in `propvision_oracle_service.py` (check its
definition, all its call-sites, and whether it's a mixin shared with MLB
services). This audit is explicitly read-only and does not change any
code.

If the audit reveals a safe shared/sport-aware adapter: Batch 5 is a
single-handle flip.

If the audit reveals unsafe cross-sport mixing: defer Batch 5 and go to
Batch 6 instead (adaptive_sync_engine).

### Out of scope (deferred)
- `routes/qa_testing.py` (Batch 7 — QA endpoints, low priority)
- All reader hardcodes + registry cleanups (Wave 2)

## Status

**Batch 4 complete. Surgical 1-line flip applied. Pipeline ran end-to-end
without errors; persist-writer path not exercised in this window due to
empty enrichment collector (runtime behavior, not code-path issue).
6 consecutive clean ledger ticks. FULL-DOC HASH SWEEP 107/107. Regression
clean. Halted pending greenlight for Batch 5 audit or Batch 6.**




# Wave 2 — Read Flip · Audit (events_cache · NBA)

**Scope**: Flip the `events_cache` · NBA concept from its legacy primary
(`dg_events_cache`) to the rename target (`nba_events_cache`). Retire the
shadow-write pair. Decommission the old primary via rename-to-backup.

---

## Registry diff (single file, single concept)

**`backend/services/config/collection_names.py`**

```diff
  _SPORT_COLLECTIONS: Dict[str, Dict[str, str]] = {
      …
-     "events_cache":         {"nba": "dg_events_cache",
+     "events_cache":         {"nba": "nba_events_cache",
                               "mlb": "mlb_events_cache"},
      …
  }
```

```diff
  _SHADOW_WRITES: Dict[Tuple[str, str], str] = {
-     # Pilot: NBA events cache (read-only downstream, full-refresh pattern,
-     # lowest blast radius).
-     ("events_cache", "nba"): "nba_events_cache",
+     # Wave 2 complete: events_cache · NBA has been flipped to
+     # `nba_events_cache` and the shadow phase is retired. The old
+     # primary (`dg_events_cache`) has been renamed to
+     # `dg_events_cache_backup` and is eligible for drop after the
+     # observation window closes.
  }
```

Resolver parity after flip:
```
COLL("events_cache", "nba")    -> nba_events_cache    ← flipped
COLL.writes_to(...)            -> ["nba_events_cache"]  (single-write)
COLL.active_shadows()          -> {}                  (shadow phase retired)
```

---

## Backfill result

```
[PRE-FLIGHT] dg_events_cache  = 1 doc
[PRE-FLIGHT] nba_events_cache = 1 doc
[BACKFILL] primary ids                 : 1
[BACKFILL] missing from shadow         : 0
[BACKFILL] nothing to copy (already in sync)
[POST-BACKFILL] dg_events_cache  = 1
[POST-BACKFILL] nba_events_cache = 1
[COVERAGE] primary ids NOT in shadow   : 0
```

Shadow was fully synchronized by the Wave 1 monitor's clean ticks →
backfill was a no-op. Full coverage confirmed.

---

## Write-path verification (post-flip)

```
odds_api.events_cache handle type   : AsyncIOMotorCollection   ← raw, no wrapper
odds_api.events_cache name          : nba_events_cache         ← new primary

[BEFORE FETCH] dg_events_cache  = 1
[BEFORE FETCH] nba_events_cache = 1

fetch_todays_events()  -> 8 events returned

[AFTER FETCH]  dg_events_cache  = 1   (unchanged — routing correct)
[AFTER FETCH]  nba_events_cache = 1   (writer target — live)
[ROUTING OK]   writes only hit nba_events_cache: True
```

The `ShadowWriter` wrapper is gone from the service's handle — writes go
directly to `nba_events_cache` with zero fan-out overhead.

---

## Decommission — rename

```
[RENAME] pick_vision.dg_events_cache -> pick_vision.dg_events_cache_backup  OK
[FINAL] dg_events_cache            present? False
[FINAL] dg_events_cache_backup     present? True
[FINAL] nba_events_cache           present? True
[FINAL] dg_events_cache_backup count = 1
[FINAL] nba_events_cache count       = 1
```

Old collection preserved as `dg_events_cache_backup` (option A, your
default). Drop can be scheduled after the post-flip observation window.

---

## Endpoint smoke (post-flip + post-rename)

```
/api/v3/ferrari/safe-haven                    HTTP 200 (0.83s)
/api/v3/ferrari/front-lines                   HTTP 200 (0.44s)
/api/v3/ferrari/war-zone                      HTTP 200 (0.28s)
/api/v3/mlb/safe-haven                        HTTP 200 (0.19s)
/api/v3/mlb/front-lines                       HTTP 200 (0.14s)
/api/v3/mlb/war-zone                          HTTP 200 (0.12s)
/api/v3/scheduler-status                      HTTP 200 (0.10s)
/api/v3/master-hub/player/name/Luka%20Doncic  HTTP 200 (0.18s)
/api/live/scores?sport=nba                    HTTP 200 (0.28s)
/api/v3/ferrari/market-moves?sport=nba        HTTP 200 (0.11s)

No new errors. Only pre-existing LOCKDOWN warmup warnings (unrelated).
```

---

## Monitor self-disabled

The shadow-divergence job is still scheduled (interval 60s, id
`shadow_divergence_monitor`) but its body now short-circuits because
`COLL.active_shadows()` is empty.

```
Wave-1 ledger rows written after registry flip : 0   ← as expected
New [SHADOW_DIVERGENCE] warnings after flip    : 0
```

The job will re-activate automatically for the next concept you shadow —
no code change needed.

---

## Regression suite

```
pytest tests/test_shadow_writes.py tests/test_collection_names.py \
       tests/test_hit_rate_canonical.py tests/test_tier_integrity.py \
       tests/test_decision_layer_sengun.py
--------------------------------------------------------------------
89 passed, 1 skipped, 1 warning in 24.64s
```

3 pilot-specific assertions were retired (they explicitly named
`events_cache` as shadow-mapped) and replaced with 2 lifecycle-aware
parametric tests that pass correctly whether the shadow map is empty
or populated. Net: 9 shadow tests (down from 10) covering both phases.

---

## Final decision

**🟢 Pilot success confirmed. Wave 2 complete for `events_cache` · NBA.**

### Lifecycle summary for this concept
| Phase | State |
|---|---|
| Wave 0 | Plumbed through `COLL(...)` (Batch 2) |
| Wave 1 | Shadow-written 1:1 to `nba_events_cache`, zero divergence |
| Wave 2 | **Primary flipped, shadow retired, old collection renamed to backup** |
| Wave 3 (optional) | Drop `dg_events_cache_backup` after observation |

### What scales to the next concept
The full playbook is now a template. For any next concept X:

1. Add `(X, sport): <new_name>` to `_SHADOW_WRITES`.
2. At every writer's handle binding for X, swap `db[COLL(X, sport)]` →
   `COLL.handle(db, X, sport)`.
3. Add a stable-key entry to `_STABLE_KEY` in the monitor if X's primary
   key is not `_id`.
4. Restart backend. Monitor ticks every 60s into `board_drift_ledger`.
5. On greenlight: flip the primary in `_SPORT_COLLECTIONS`, remove the
   `_SHADOW_WRITES` entry, rename-to-backup, validate, done.

No further framework work required — ready to scale.

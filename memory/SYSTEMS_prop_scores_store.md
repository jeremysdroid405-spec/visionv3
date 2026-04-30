# Prop Scores Store — Race Safety Invariants

## What this is
The storage layer for `{sport}_prop_scores`. Two write modes:
- `mode=replace` — used by hourly master_sync full rebuild
- `mode=upsert` — used by realtime engine (`services/board/engine.py`)

## Why this file exists
On 2026-04-30, analysis of `sync_history` showed **75 of 76** MLB sync
failures (98.7%) had one root cause: `E11000 duplicate key error` on
the `(canonical_key, version_tag)` unique index.

The bug was a race window in the old `mode=replace` implementation:

```python
# OLD (broken):
await coll.delete_many({"version_tag": tag})
# ← realtime engine upserts here — creates doc with same key
await coll.insert_many(new_docs, ordered=False)  # E11000
```

The realtime engine's `on_new_props` runs on every oddsAPI event
(every ~10–30 seconds during a slate). The hourly master_sync
`mode=replace` rebuild happens once per hour. Their overlap window
was small in absolute terms but hit during ~20% of rebuilds because
the rebuild takes 3–9 minutes and the realtime engine is firing the
whole time.

## The fix
Race-safe bulk replace:
```python
# NEW:
ops = [ReplaceOne({"canonical_key": ..., "version_tag": tag}, doc, upsert=True)
       for doc in new_docs]
await coll.bulk_write(ops, ordered=False)
await coll.delete_many({"version_tag": tag, "canonical_key": {"$nin": new_cks}})
```

Any concurrent upsert from the realtime engine just produces a
replace here — no E11000. The stale-sweep runs AFTER the replace, so
there's no window where stale rows exist without new rows.

## Invariants (DO NOT BREAK)

**INV-1: `mode=replace` is idempotent under concurrent upsert pressure.**
Enforced by `tests/test_prop_scores_store_race.py::test_inv1_replace_is_idempotent_under_concurrent_upsert`.
Runs the exact race pattern that caused 75/76 failures, 5 times.

**INV-2: Stale keys are swept.**
A reduced-slate replace removes canonical_keys no longer in the batch.
Enforced by `test_inv2_stale_keys_are_swept`.

**INV-3: Empty batch wipes the whole tag.**
Preserves the pre-fix contract. `score_docs=[]` must delete all rows
under `version_tag`. Enforced by `test_inv3_empty_batch_wipes_tag`.

**INV-4: Result-dict shape is stable.**
Always returns `{mode, written, replaced, prepared, computed_at, sport,
version_tag, collection, dry_run}`. Callers (master_sync, board/engine)
rely on these keys. Enforced by `test_inv4_result_shape_is_stable`.

**INV-5: `mode=upsert` is unchanged.**
Per-doc `update_one(upsert=True)`. The race fix is strictly scoped to
`mode=replace`. Enforced by `test_inv5_upsert_mode_unchanged`.

## Files

| Path | Purpose |
|---|---|
| `services/scoring/prop_scores_store.py::write_versioned_scores` | The write path |
| `tests/test_prop_scores_store_race.py` | 5 invariant tests (~2s) |
| `/app/memory/SYSTEMS_prop_scores_store.md` | This doc |

## Live verification (2026-04-30)

After the fix, a full MLB slate sync was triggered while the realtime
engine was actively upserting:

```
[SCORES_STORE:mlb] mode=upsert version='final-mlb-rt-shadow' upserted=2561
[BOARD_ENGINE] mlb on_new_props matched=2613 written=2613 (58s)
[SCORES_STORE:mlb] mode=upsert version='final-mlb-rt' upserted=66  ← realtime
[SCORES_STORE:mlb] mode=upsert version='final-mlb-rt' upserted=49  ← realtime
[SCORES_STORE:mlb] mode=upsert version='final-mlb-rt' upserted=38  ← realtime
...
```

**Zero E11000 / BulkWriteError / DuplicateKeyError** in the logs
during or after the full sync. Fix verified.

## Why this fix will stick

1. **INV-1 test literally reproduces the original race** — 5 concurrent
   replace+upsert cycles. If the race comes back, test fails loudly.
2. **The fix is structurally race-safe** — `ReplaceOne(upsert=True)` is
   atomic per-document; `$nin` sweep has no race window.
3. **Failures now log via `log_caught_exception`** — even edge-case
   BulkWriteError attributes get persisted to `error_log` with full
   context (version_tag, op_count). Visible in the admin dashboard.
4. **CHANGELOG documents the invariants** — forked agents reading the
   history see what breaks if INV-1 is violated.

## Related

- `services/observability/error_log.py` — where bulk_write failures
  now land if they ever recur.
- `services/board/engine.py::on_new_props` — the competing writer.
  Uses `mode=upsert` exclusively; unaffected by this change.

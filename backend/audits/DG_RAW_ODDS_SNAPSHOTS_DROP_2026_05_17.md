# `dg_raw_odds_snapshots` — Drop + Writer Gate (2026-05-17)

## Decision summary

Dropped `dg_raw_odds_snapshots` and gated the writer behind
`DEBUG_RAW_ODDS=true` (default false). The collection's forensic value
did not justify the 13.7 GB BSON / 1.56 GB on-disk footprint blocking
GitHub push, replay testing, and Mongo checkpointing.

## Reasons

1. **Replaceable.** Historical odds can be re-pulled from The Odds API.
2. **Not on any hot path.** `replay/`, `scoring/`,
   `picks_getter_service.py`, `mlb_high_friction_model.py` — zero
   references. Confirmed in `DG_RAW_ODDS_SNAPSHOTS_AUDIT.md` § 4.
3. **Only consumed by admin debug endpoints** (`/api/admin/odds/raw-snapshots`,
   `/api/admin/odds/canonical-trace`). Both now return empty results
   gracefully instead of erroring.
4. **Unbounded growth.** ~280 MB/day with no TTL.
5. **Disk pressure.** `/app` was 9.7 GB / 9.8 GB (99% full). Dropping
   freed 2.3 GB instantly.

## Pre/post measurements

| Metric | Pre | Post |
|---|---:|---:|
| `dg_raw_odds_snapshots` count | 14,602,326 | 0 (collection dropped) |
| Collection on-disk (WT) | 1,564 MB | 0 MB |
| Collection indexes | 716 MB | 0 MB |
| `/app` free | **339 MB** | **2.6 GB** |
| Reclaimed | — | **+2.3 GB** |
| Backend health (`/docs`, admin endpoint) | OK | OK |
| Replay collections | intact | intact |

### Replay/scoring collection counts (post-drop verification)

| Collection | docs |
|---|---:|
| `mlb_master_hub_2026` | 6,658 |
| `mlb_replay_feature_cache` | 15,797 |
| `mlb_replay_model_outputs` | 487,035 |
| `mlb_production_replay_outputs` | 88,553 |
| `mlb_historical_alt_odds_raw` | 456,050 |
| `mlb_prop_scores` | 108,352 |
| `dg_raw_odds_markets` | 510,054 |

All ✅ untouched.

## Changes applied

### Code — writer gate

`services/universal_odds_sync.py::_persist_raw_markets`:

```python
# Default OFF — writer disabled unless DEBUG_RAW_ODDS=true
if os.environ.get("DEBUG_RAW_ODDS", "").lower() == "true":
    snap_coll = self.db["dg_raw_odds_snapshots"]
    try:
        if snapshot_rows:
            await snap_coll.insert_many(snapshot_rows, ordered=False)
    except Exception as e:
        logger.warning(f"[UNIVERSAL_ODDS] raw-snapshots write error: {e}")
```

The original branch is preserved (just gated). Re-enable for short
forensic windows by setting `DEBUG_RAW_ODDS=true` in `backend/.env`
and restarting the backend.

### Database

```python
# Executed via pymongo at 2026-05-17T15:56Z
db.dg_raw_odds_snapshots.drop()
```

Mongo's WT checkpoint completed within 15 seconds; disk reclaim
visible at T+15s.

## Operational notes

- Admin audit endpoints (`/api/admin/odds/raw-snapshots`,
  `/api/admin/odds/canonical-trace`) still function. They now
  return empty result sets, which is the correct behaviour for a
  collection that has no data.
- `dg_raw_odds_markets` (the latest-state cache, 510K docs) is
  UNAFFECTED. This is the collection that backs live odds reads,
  not the dropped forensic store.
- If the collection somehow re-creates (e.g. someone temporarily sets
  `DEBUG_RAW_ODDS=true`), it will recreate WITHOUT indexes. Indexes
  should be rebuilt manually before any heavy use:
  ```python
  c = db.dg_raw_odds_snapshots
  c.create_index([("canonical_candidate", 1), ("fetched_at", -1)])
  c.create_index([("event_id", 1), ("fetched_at", -1)])
  c.create_index([("outcome_description", 1), ("fetched_at", -1)])
  c.create_index([("scrape_id", 1)])
  c.create_index([("fetched_at", -1)])
  ```

## Replacement source

The forensic functionality is replaceable by **The Odds API
historical endpoint** (already configured for the project). Any
future forensic interrogation can re-pull the upstream payload
on demand rather than maintain a 13.7 GB local mirror.

## Future / Backlog

- Decide on permanent solution before re-enabling writer:
  (a) TTL with proper BSON Date field (requires schema change —
      `fetched_at` is currently ISO string, TTL ignores strings),
  (b) Quota-bounded ring buffer (e.g. keep last 100K docs only),
  (c) Drop entirely, rely on Odds API historical for forensics.
- Until that decision lands, **keep `DEBUG_RAW_ODDS` unset**.

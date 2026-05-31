# NCAAF Historical Outcomes Pipeline — Trace & Runbook

> Updated after pre-flight finding: `sgo_props_raw` / `sgo_events` /
> `sgo_players` / `sgo_book_consensus` are EMPTY for NCAAF on the VPS.
> Canonical NCAAF data lives in `ncaaf_player_historical_props`,
> `ncaaf_matchups`, `ncaaf_historical_props`, and `sgo_player_stats`
> (league=NCAAF). Bridging script added.

---

## Architectural summary

```
                ┌────────────────────────────────────────────┐
                │  NCAAF canonical data (already on VPS)     │
                │    ncaaf_matchups                          │
                │    ncaaf_player_historical_props           │
                │    sgo_player_stats   (league_id=NCAAF)    │
                └────────────────┬───────────────────────────┘
                                 │
                                 ▼
        ┌─────────────────────────────────────────────────────┐
        │  STEP 1 — reshape_ncaaf_to_legacy_sgo.py            │
        │  (NEW; one-shot, idempotent, NCAAF-only)            │
        │  Writes:                                            │
        │    sgo_events     (from ncaaf_matchups)             │
        │    sgo_players    (from sgo_player_stats + props)   │
        │    sgo_props_raw  (from ncaaf_player_historical_props)│
        │  Skips: sgo_book_consensus (optional downstream)    │
        └────────────────┬────────────────────────────────────┘
                         │
                         ▼
        ┌──────────────────────────────────────────────────────┐
        │  STEP 2 — build_pp_research_core --league NCAAF      │
        │  Writes: sgo_ncaaf_research_core                     │
        └────────────────┬─────────────────────────────────────┘
                         │
                         ▼  (joins sgo_player_stats)
        ┌──────────────────────────────────────────────────────┐
        │  STEP 3 — build_historical_outcomes --league NCAAF   │
        │  Reads: sgo_ncaaf_research_core + sgo_player_stats   │
        │  Writes: sgo_ncaaf_research_outcomes                 │
        │  (NCAAF skips enrichment — same as NFL)              │
        └────────────────┬─────────────────────────────────────┘
                         │
                         ▼
        ┌──────────────────────────────────────────────────────┐
        │  STEP 4 — build_historical_model_features --league NCAAF │
        │  Reads: sgo_ncaaf_research_core + sgo_player_stats   │
        │  Writes: sgo_ncaaf_research_model_features           │
        └────────────────┬─────────────────────────────────────┘
                         │
                         ▼
        ┌──────────────────────────────────────────────────────┐
        │  STEP 5 — score_historical_model --league NCAAF      │
        │  Reads: sgo_ncaaf_research_model_features            │
        │  Writes: sgo_ncaaf_research_model_predictions        │
        └──────────────────────────────────────────────────────┘
```

---

## Code changes applied in this session (5 files)

| File | Change |
|---|---|
| `scripts/sgo/build_pp_research_core.py` | `_resolve_out_coll`: NCAAF → `sgo_ncaaf_research_core` |
| `scripts/sgo/build_historical_outcomes.py` | NCAAF branch: src=`sgo_ncaaf_research_core`, out=`sgo_ncaaf_research_outcomes` |
| `scripts/sgo/build_historical_model_features.py` | Added `--src-coll`/`--out-coll` + NCAAF auto-routing |
| `scripts/sgo/score_historical_model.py` | Added `_resolve_colls()` + NCAAF first-class routing |
| `scripts/sgo/ingest_historical_player_stats.py` | `normalize_stats()`: NCAAF → `_normalize_nfl_stats()` |
| **NEW** `scripts/sgo/reshape_ncaaf_to_legacy_sgo.py` | The bridge script (this trace's STEP 1) |

All five scripts dry-run cleanly with `--league NCAAF` / `--dry-run`.

---

## Reshape script design notes (for review)

**Idempotency contract** — every write uses
`bulk_write([UpdateOne(filter, {$set: doc}, upsert=True)], ordered=False)`
keyed by the EXACT same unique-key tuples as the production indexes
defined in `scripts/sgo/ingest.py::ensure_indexes()`:

- `sgo_events`     unique: `(event_id, snapshot_time)`
- `sgo_players`    unique: `(player_id)`
- `sgo_props_raw`  unique: `(event_id, odd_id, book_id, side, line, snapshot_time)`

Re-running the script produces zero net new rows.

**`odd_id` synthesis (per-anchor stable)** — the new NCAAF pipeline
discards SGO's market ID, but the legacy `sgo_props_raw` unique index
requires one. We synthesize a deterministic 24-char SHA1 prefix from
`(event_id, statID, statEntityID, periodID, side, line)`:

```python
synth_odd_id = sha1(f"{event_id}|{statID}|{statEntityID}|{periodID}|{side}|{line}").hexdigest()[:24]
```

Same anchor across multiple books → same `odd_id` (essential for
`build_pp_research_core`'s aggregation grouping). Different anchors →
different IDs. SHA1-24 is collision-safe at 400k rows.

**NCAAF-only filters** — every source query is gated by
`{league: "NCAAF"}` (or `{league_id: "NCAAF"}` for `sgo_player_stats`).
MLB / NBA / NFL rows are never read or written.

**Safety** — defaults to dry-run. Requires explicit `--apply` to write.

**`sgo_book_consensus`** — intentionally NOT populated. The new
NCAAF pipeline doesn't carry fair-odds / consensus data, and
`build_pp_research_core` already handles missing consensus rows
gracefully (`consensus_doc = None`; downstream stamps `fair_odds=None`,
`book_odds=None`, `consensus_probability=None` on the anchor). Skipping
this collection is the architecturally correct choice — synthesizing
fake consensus values would corrupt downstream model features.

---

## Execution order (run on the VPS)

All commands run from your prod backend directory (e.g.
`cd /var/www/app/backend`). Replace dates as needed for the seasons
you've acquired.

### Step 1 — Reshape NCAAF canonical → legacy SGO archive

```bash
# Dry-run first (counts only; no writes)
python -m scripts.sgo.reshape_ncaaf_to_legacy_sgo --dry-run

# Apply (idempotent — safe to re-run)
python -m scripts.sgo.reshape_ncaaf_to_legacy_sgo --apply
```

Expected output footer:

```
  POST-MIGRATION COUNTS (league_id=NCAAF):
    sgo_events     NCAAF:  <count from ncaaf_matchups>
    sgo_players    NCAAF:  <distinct player_ids>
    sgo_props_raw  NCAAF:  ~396,603 (matches ncaaf_player_historical_props)
```

### Step 2 — Build NCAAF research core (multi-book anchors)

```bash
# Dry-run (sizes the universe and prints sample anchor)
python -m scripts.sgo.build_pp_research_core \
    --league NCAAF --dry-run

# Apply — writes to sgo_ncaaf_research_core
python -m scripts.sgo.build_pp_research_core --league NCAAF
```

You can scope by season if you prefer:
`--start 2024-08-24 --end 2024-12-15`.

### Step 3 — Build NCAAF outcomes (grade props vs actual stats)

NCAAF skips the enrichment step (same as NFL); outcomes script reads
the raw research core directly.

```bash
# Dry-run
python -m scripts.sgo.build_historical_outcomes \
    --league NCAAF --dry-run

# Apply — writes to sgo_ncaaf_research_outcomes.
# --debug-unresolved is recommended on the first live run; it prints
# a grouped breakdown of every UNRESOLVED row so you can spot any
# resolver gaps before scaling up.
python -m scripts.sgo.build_historical_outcomes \
    --league NCAAF --debug-unresolved
```

### Step 4 — Build NCAAF model features (no-leakage rolling features)

```bash
# Dry-run
python -m scripts.sgo.build_historical_model_features \
    --league NCAAF --dry-run

# Apply — writes to sgo_ncaaf_research_model_features
python -m scripts.sgo.build_historical_model_features --league NCAAF
```

Default lookback is 90 days. For full-season `season_to_date_avg`
coverage, bump to 180:
`--lookback-days 180`.

### Step 5 — Score NCAAF historical model

```bash
# With a trained model file:
python -m scripts.sgo.score_historical_model \
    --league NCAAF \
    --model-path /path/to/your/ncaaf_model.joblib \
    --model-version ncaaf_v1

# OR with a custom Python entrypoint (module.path:func returning P(hit)):
python -m scripts.sgo.score_historical_model \
    --league NCAAF \
    --model-entrypoint your_module.predictors:ncaaf_v1_predict \
    --model-version ncaaf_v1
```

Writes to **`sgo_ncaaf_research_model_predictions`**.

---

## Step 6 — Final verification (after Step 5 completes)

Run this single mongosh script to verify the end-to-end NCAAF pipeline
produced the expected counts at every stage:

```bash
mongosh "$MONGO_URL/$DB_NAME" --quiet --eval '
const ncaaf = {league_id:"NCAAF"};
const NCAAF = {league:"NCAAF"};
print("===== LEGACY ARCHIVE (after reshape, Step 1) =====");
print("sgo_events                          NCAAF:",
      db.sgo_events.countDocuments(ncaaf));
print("sgo_players                         NCAAF:",
      db.sgo_players.countDocuments(ncaaf));
print("sgo_props_raw                       NCAAF:",
      db.sgo_props_raw.countDocuments(ncaaf));
print("sgo_player_stats                    NCAAF:",
      db.sgo_player_stats.countDocuments(ncaaf));
print("");
print("===== CANONICAL SOURCE (unchanged) =====");
print("ncaaf_matchups:                            ",
      db.ncaaf_matchups.countDocuments({}));
print("ncaaf_player_historical_props:             ",
      db.ncaaf_player_historical_props.countDocuments({}));
print("");
print("===== PIPELINE OUTPUTS =====");
print("sgo_ncaaf_research_core              (Step 2):",
      db.sgo_ncaaf_research_core.countDocuments({}));
print("sgo_ncaaf_research_outcomes          (Step 3):",
      db.sgo_ncaaf_research_outcomes.countDocuments({}));
const r = db.sgo_ncaaf_research_outcomes.aggregate([
    {$group: {_id:"$outcome", n:{$sum:1}}}, {$sort:{n:-1}}
]).toArray();
r.forEach(x => print("  outcome=" + x._id + ": " + x.n));
print("sgo_ncaaf_research_model_features    (Step 4):",
      db.sgo_ncaaf_research_model_features.countDocuments({}));
print("  feature_ready=true:",
      db.sgo_ncaaf_research_model_features.countDocuments({feature_ready:true}));
print("sgo_ncaaf_research_model_predictions (Step 5):",
      db.sgo_ncaaf_research_model_predictions.countDocuments({}));
print("");
print("===== CROSS-LEAGUE CONTAMINATION CHECK =====");
print("(should be 0 — reshape is NCAAF-only)");
print("non-NCAAF events with reshape_source:",
      db.sgo_events.countDocuments(
        {reshape_source:"ncaaf_legacy_bridge", league_id:{$ne:"NCAAF"}}));
print("non-NCAAF players with reshape_source:",
      db.sgo_players.countDocuments(
        {reshape_source:"ncaaf_legacy_bridge", league_id:{$ne:"NCAAF"}}));
print("non-NCAAF props_raw with reshape_source:",
      db.sgo_props_raw.countDocuments(
        {reshape_source:"ncaaf_legacy_bridge", league_id:{$ne:"NCAAF"}}));
'
```

### Expected counts (orders of magnitude)

| Collection                                  | Expected after pipeline                                  |
|---------------------------------------------|----------------------------------------------------------|
| `sgo_events`             NCAAF              | matches `ncaaf_matchups` count                           |
| `sgo_players`            NCAAF              | ≥ ~600–2k (distinct players in the dataset)              |
| `sgo_props_raw`          NCAAF              | ~396,603 (matches `ncaaf_player_historical_props`)       |
| `sgo_player_stats`       NCAAF              | 3,570 (unchanged)                                        |
| `sgo_ncaaf_research_core`                   | ≤ props_raw count (deduped to anchor tuples)             |
| `sgo_ncaaf_research_outcomes`               | = research_core count                                    |
|   — `outcome=WIN`                           | typical ~45-55% of resolved                              |
|   — `outcome=LOSS`                          | typical ~45-55% of resolved                              |
|   — `outcome=PUSH`                          | small (<5%)                                              |
|   — `outcome=UNRESOLVED`                    | varies; expect a chunk for stat-families w/o resolver yet|
| `sgo_ncaaf_research_model_features`         | = research_core count                                    |
|   — `feature_ready=true`                    | depends on player game-history depth (5+ prior games)    |
| `sgo_ncaaf_research_model_predictions`      | = `feature_ready=true` count                             |
| **cross-league contamination check**        | **MUST be 0**                                            |

---

## Rollback

If you ever need to undo the reshape (e.g. you re-acquire NCAAF via
the legacy SGO ingest later), the migrated rows are tagged with
`reshape_source="ncaaf_legacy_bridge"` and can be cleanly removed:

```bash
mongosh "$MONGO_URL/$DB_NAME" --quiet --eval '
db.sgo_events.deleteMany({league_id:"NCAAF",
    reshape_source:"ncaaf_legacy_bridge"});
db.sgo_players.deleteMany({league_id:"NCAAF",
    reshape_source:"ncaaf_legacy_bridge"});
db.sgo_props_raw.deleteMany({league_id:"NCAAF",
    reshape_source:"ncaaf_legacy_bridge"});
'
```

(MLB/NBA/NFL rows lack the `reshape_source` field so they're
untouched.)

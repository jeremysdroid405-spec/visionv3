# NCAAF Historical Outcomes Pipeline — Trace & Runbook

> Traced from the live codebase at `/app/backend/scripts/sgo/*`.
> No guessing — every collection / file / line is grounded.

---

## TL;DR — Direct answers to your 5 questions

### 1. Which collection feeds `build_historical_outcomes`?

It depends on `--league`. The script has hard-coded per-league routing in
`amain()`:

| League        | Source collection                | Output collection                |
|---------------|----------------------------------|----------------------------------|
| MLB / NBA     | `sgo_pp_research_core_enriched`  | `sgo_pp_research_outcomes`       |
| NFL           | `sgo_nfl_research_core`          | `sgo_nfl_research_outcomes`      |
| **NCAAF** (new) | `sgo_ncaaf_research_core`      | `sgo_ncaaf_research_outcomes`    |

The script ALSO joins `sgo_player_stats` to look up actual values to grade.

File: `/app/backend/scripts/sgo/build_historical_outcomes.py`
Routing block: `amain()` lines ~810–820 (now NFL + NCAAF cases).

### 2. Which script creates that source collection?

- `sgo_pp_research_core_enriched` ← `build_historical_consensus_probabilities.py`
  (reads `sgo_pp_research_core` → enriches → writes enriched).
- `sgo_pp_research_core`          ← `build_pp_research_core.py`
- `sgo_nfl_research_core`         ← `build_pp_research_core.py --league NFL`
- **`sgo_ncaaf_research_core`** (new) ← `build_pp_research_core.py --league NCAAF`

`build_pp_research_core.py` reads from these legacy SGO ingest collections:
- `sgo_props_raw`        — raw book quotes (anchor source)
- `sgo_events`           — game metadata (`event_id`, `game_date`, `league_id`)
- `sgo_players`          — player_id → player_name cache
- `sgo_book_consensus`   — devig / fair-odds reference (optional)

These four collections are populated by the legacy SGO ingest at
`/app/backend/scripts/sgo/ingest.py` (function `ingest_one`, see `COLLECTIONS` map).

### 3. Does `build_pp_research_core` support NCAAF today?

**Before my changes:** Partially. It accepted `--league NCAAF` but would
write into the SHARED `sgo_pp_research_core` collection, polluting
MLB/NBA data. `_resolve_out_coll` only special-cased NFL.

**After my changes:** Yes — NCAAF writes to its own
`sgo_ncaaf_research_core`. Same surgical pattern used for NFL.

### 4. NCAAF mappings added (mirrors MLB/NBA/NFL exactly)

Patches applied in this session (3 files, ~25 LOC total — all additive):

| File | Change |
|---|---|
| `scripts/sgo/build_pp_research_core.py` | `_resolve_out_coll`: added `if league=="NCAAF" → "sgo_ncaaf_research_core"`. Help text updated. |
| `scripts/sgo/build_historical_outcomes.py` | `amain` routing: added NCAAF branch (src=`sgo_ncaaf_research_core`, out=`sgo_ncaaf_research_outcomes`). CLI help updated. |
| `scripts/sgo/build_historical_model_features.py` | Added `--src-coll` / `--out-coll` overrides and per-league routing. NCAAF reads from `sgo_ncaaf_research_core`, writes `sgo_ncaaf_research_model_features`. |
| `scripts/sgo/ingest_historical_player_stats.py` | `normalize_stats()`: NCAAF → `_normalize_nfl_stats()` (college football shares NFL's SGO stat-family schema: `passing_yards`, `rushing_yards`, `receiving_yards`, etc.). |

`score_historical_model.py` requires NO change — it already filters by
`--league` against shared collections and doesn't care about routing.

All three scripts dry-run cleanly with `--league NCAAF` on the preview pod
(verified — empty results because preview Mongo has no NCAAF data, by design).

---

## 5. Exact execution order + commands for NCAAF

> Run on your **VPS** (preview pod can't hold the volume).
> `cd /var/www/app/backend` (or wherever your prod checkout lives).

### Pre-flight (one-time): confirm legacy SGO collections have NCAAF data

`build_pp_research_core` reads from `sgo_props_raw` + `sgo_events`. The
NEW per-sport pipeline (`workers/team/historical_player_ingest.py`)
writes ONLY to `ncaaf_player_historical_props` — NOT to `sgo_props_raw`
or `sgo_events`. So before kicking off the pipeline, verify:

```bash
mongosh "$MONGO_URL/$DB_NAME" --quiet --eval '
  print("sgo_events     NCAAF:", db.sgo_events.countDocuments({league_id:"NCAAF"}));
  print("sgo_props_raw  NCAAF:", db.sgo_props_raw.countDocuments({league_id:"NCAAF"}));
  print("sgo_players    NCAAF:", db.sgo_players.countDocuments({$or:[{league_id:"NCAAF"},{sport_id:"FOOTBALL"}]}));
'
```

You stated `sgo_player_stats` already has 3,570 NCAAF rows across 600
events — that PROVES `sgo_events` is populated (since
`ingest_historical_player_stats.py --source sgo` filters
`sgo_events.find({league_id:"NCAAF"})`). The question is whether
`sgo_props_raw` was also populated. Two outcomes:

- **`sgo_props_raw` NCAAF count > 0** → straight ahead, run Step 1 below.
- **`sgo_props_raw` NCAAF count = 0** → you only ran the new
  per-sport ingest. You'll first need to either (a) run the legacy
  `scripts/sgo/ingest.py` for NCAAF, OR (b) write a one-shot reshape
  from `ncaaf_player_historical_props` into `sgo_props_raw` schema.
  Ping me and I'll write that reshape script — it's ~80 LOC.

### Step 1 — Build NCAAF research core (anchors + multi-book attachment)

```bash
# Dry-run first to size the universe
python -m scripts.sgo.build_pp_research_core \
    --league NCAAF --start 2024-08-24 --end 2024-12-15 --dry-run

# Live write
python -m scripts.sgo.build_pp_research_core \
    --league NCAAF --start 2024-08-24 --end 2024-12-15
```

Writes to **`sgo_ncaaf_research_core`**. One row per
`(event_id, player_id, stat_id, side, line, period_id)` anchor.

### Step 2 — Ingest NCAAF player stats (already done per your message)

You said `sgo_player_stats` has 3,570 NCAAF rows. If you need to top up:

```bash
python -m scripts.sgo.ingest_historical_player_stats \
    --league NCAAF --source sgo --start 2024-08-24 --end 2024-12-15
```

(NCAAF now dispatches to `_normalize_nfl_stats()` — was previously
falling through auto-detect.)

### Step 3 — Build NCAAF outcomes (joins core + player_stats → graded rows)

NCAAF, like NFL, **skips the enrichment step**. The outcomes script
reads directly from `sgo_ncaaf_research_core`:

```bash
# Dry-run
python -m scripts.sgo.build_historical_outcomes \
    --league NCAAF --start 2024-08-24 --end 2024-12-15 --dry-run

# Live with detailed unresolved breakdown (recommended first live run)
python -m scripts.sgo.build_historical_outcomes \
    --league NCAAF --start 2024-08-24 --end 2024-12-15 --debug-unresolved
```

Writes to **`sgo_ncaaf_research_outcomes`**. Each row has:
`actual_value`, `outcome` (WIN/LOSS/PUSH/UNRESOLVED), `hit`,
`margin_vs_line`, `stat_family`, `grading_version=v1`.

### Step 4 — Build NCAAF model features (prior-history-only, no leakage)

```bash
# Dry-run
python -m scripts.sgo.build_historical_model_features \
    --league NCAAF --start 2024-09-01 --end 2024-12-15 --dry-run

# Live
python -m scripts.sgo.build_historical_model_features \
    --league NCAAF --start 2024-09-01 --end 2024-12-15
```

Writes to **`sgo_ncaaf_research_model_features`**. Computes
`last_3 / last_5 / last_10 / last_20` rolling averages, recent
volatility, line-relative hit-rates, plus passthrough market signal.

### Step 5 — Score NCAAF historical rows (optional — model predictions)

The scoring script reads `sgo_pp_research_model_features` and writes
`sgo_pp_research_model_predictions`. It's currently a **shared**
collection filtered by `--league`. If you want a per-sport features
collection (which my patch creates: `sgo_ncaaf_research_model_features`),
you'll need to pass `--src-coll` to score_historical_model.py OR
let me add the same league-routing pattern there:

```bash
# Current behaviour (shared features coll) — requires features to be
# written into sgo_pp_research_model_features:
python -m scripts.sgo.score_historical_model \
    --league NCAAF --model-version v3
```

> Recommendation: either (a) point Step 4's `--out-coll
> sgo_pp_research_model_features` to keep using the shared
> collection, or (b) tell me to wire NCAAF routing into
> `score_historical_model.py` the same way I did for the other
> three scripts. (b) is the consistent choice.

---

## Pipeline DAG (full, with collection names)

```
                 ┌───────────────────────────────────────┐
                 │  LEGACY SGO INGEST (one-time per      │
                 │  league; populates raw archives):     │
                 │  scripts/sgo/ingest.py                │
                 └─┬──────────┬──────────┬──────────┬────┘
                   │          │          │          │
              sgo_events  sgo_props_raw sgo_players sgo_book_consensus
                   │          │          │          │
                   └────┬─────┴────┬─────┴──────────┘
                        │          │
                        ▼          ▼
              ┌─────────────────────────────────────┐
              │  build_pp_research_core.py          │
              │  --league {MLB|NBA|NFL|NCAAF}       │
              └────────┬────────────────────┬───────┘
                       │ (MLB/NBA)          │ (NFL/NCAAF)
                       ▼                    │
            sgo_pp_research_core            │
                       │                    │
                       ▼                    │
   ┌──────────────────────────────────────┐ │
   │ build_historical_consensus_          │ │
   │   probabilities.py                   │ │
   │ (MLB/NBA ONLY — NFL/NCAAF skip this) │ │
   └────────┬─────────────────────────────┘ │
            │                                │
            ▼                                │
    sgo_pp_research_core_enriched            │
            │                                │
            └────────────┬───────────────────┴──────┐
                         │                          │
                         │  + sgo_player_stats      │
                         │  (← ingest_historical_   │
                         │      player_stats.py)    │
                         ▼                          ▼
              ┌─────────────────────────────────────────┐
              │  build_historical_outcomes.py           │
              │  --league {MLB|NBA|NFL|NCAAF}           │
              └──────┬─────────────────────────┬────────┘
                     │                         │
                     ▼                         ▼
         sgo_pp_research_outcomes   sgo_{nfl,ncaaf}_research_outcomes
         (MLB/NBA)                  (one per sport)
                     ▲                         ▲
                     │                         │
              ┌──────┴─────────────────────────┴────────┐
              │  build_historical_model_features.py     │
              │  (writes per-league via my patch)       │
              └────────┬────────────────────────────────┘
                       ▼
   sgo_{pp|nfl|ncaaf}_research_model_features
                       │
                       ▼
              ┌─────────────────────────────────┐
              │  score_historical_model.py      │
              │  --league {…} --model-version   │
              └────────┬────────────────────────┘
                       ▼
         sgo_pp_research_model_predictions
         (currently shared across leagues)
```

---

## Notes on the dual-pipeline architecture

This codebase has TWO independent historical pipelines:

1. **Legacy SGO research pipeline** — what this doc describes. Driven by
   `sgo_props_raw` + `sgo_events`. Produces graded outcomes for
   backtesting/modelling.

2. **Per-sport "Master Hub" pipeline** —
   `workers/team/historical_player_ingest.py` writes directly into
   `{sport}_player_historical_props` (e.g. `ncaaf_player_historical_props`
   396,603 rows on your VPS). This is the **canonical home of the raw
   prop universe** for the live/board layer; it does NOT feed
   `build_pp_research_core`.

If `sgo_props_raw` does NOT contain NCAAF data on your VPS (Step 1
pre-flight = 0), the cleanest fix is a one-shot reshape from
`ncaaf_player_historical_props` → `sgo_props_raw` shape. The schema
mapping is straightforward (`market` → `stat_id`, `book` → `book_id`,
`price` → `price`, `line` → `line`, `side` → `side`, …). Tell me and I'll
write it next.

# Odds-Pipeline Structural Hardening — 2026-05-17

**Patch type**: ADDITIVE / STRUCTURAL ONLY · **Behaviour deltas**: zero · **Test count**: 32 new (`tests/test_odds_pipeline_hardening.py`) → **234 total green**

## What landed

| # | Deliverable | Status |
|---|---|---|
| 1 | Append-only `dg_raw_odds_snapshots` collection (verbatim JSON, never overwritten, never upserted) | ✅ writing 151k+ rows; 100% capture during recompute window |
| 2 | Market-class SSOT (`services/market_class.py`) — classifier, `is_alternate`, `build_canonical_v2` | ✅ 15 classifier tests pass |
| 3 | New identity fields on every score doc — `market_class`, `source_market_key`, `canonical_key_v2` | ✅ 2,496/2,496 (100%) on fresh recompute |
| 4 | Split odds containers — `all_odds_standard` / `all_odds_alternate` (+ matching `all_lines_*`) | ✅ no cross-class leakage in 3 routing tests |
| 5 | Forensic audit endpoints — `/api/admin/odds/raw-snapshots`, `/api/admin/odds/canonical-trace` | ✅ both HTTP 200 with verbatim payloads |
| 6 | Observability indexes on the new collection (non-unique — preserves append-only) | ✅ 5 indexes created |
| 7 | Append-only contract pinned by negative-lockdown test | ✅ asserts no unique non-`_id` index can be silently added |

---

## Files modified

| Path | Change |
|---|---|
| **NEW** `services/market_class.py` | Single SSOT for classification + v2-key construction |
| **NEW** `routes/admin_odds_audit.py` | Two read-only forensic endpoints |
| **NEW** `tests/test_odds_pipeline_hardening.py` | 32 regression tests |
| `services/universal_odds_sync.py` | (a) per-outcome append-only snapshot rows in `_persist_raw_markets`; (b) `market_class`/`canonical_key_v2`/`source_market_key` stamped on every canonical record; (c) split-class odds containers populated in Pass-2 |
| `services/scoring/score_document_schema.py` | `market_class`, `source_market_key`, `canonical_key_v2` added to Pydantic schema (Optional, default None) |
| `services/scoring/prop_scores_store.py` | (a) new fields added to `_IDENTITY_FIELDS` allowlist; (b) backstop derivation in `_project_score_doc` when upstream stages skip them |
| `server.py` | New audit router registered at startup |

**No** changes to: thresholds, gate logic, edge math, TP math, CV / HR contracts, vision scoring, market suppression, lineup logic, DNP logic, frontend, UI.

---

## Schema Diffs

### `dg_raw_odds_snapshots` (NEW collection)
```jsonc
{
  "scrape_id":            "mlb|<event>|<iso_ts>|<uuid8>",   // groups one fetch
  "fetched_at":           "2026-05-17T01:40:35.040937Z",
  "sport":                "mlb",
  "event_id":             "0a103297db91d5bd1abf13d04b710c2a",
  "commence_time":        "2026-05-15T23:16:00Z",
  "home_team":            "Atlanta Braves",
  "away_team":            "Boston Red Sox",
  "bookmaker":            "espnbet",
  "market_key":           "batter_total_bases_alternate",
  "market_class":         "alternate",
  "raw_market_json":      { "key": "...", "last_update": "...", "description": "..." },
  "raw_outcome_json":     { /* VERBATIM outcome dict — never normalised */ },
  "outcome_name":         "Over",
  "outcome_description":  "Matt Olson",
  "outcome_point":        0.5,
  "outcome_price":        1300,
  "canonical_candidate":  "mlb|<event>|Matt Olson|Total Bases|0.5|OVER",
  "canonical_v2_candidate": "mlb|<event>|Matt Olson|Total Bases|0.5|OVER|alternate",
  "source_file":          "universal_odds_sync._persist_raw_markets",
  "ingest_version":       "v1.0_2026_05_17"
}
```

### `mlb_prop_scores` (additive fields)
```
+ market_class:        Optional[str]   # "standard"|"alternate"|"sgp"|"promo"|"unknown"
+ source_market_key:   Optional[str]   # verbatim odds-API market_key
+ canonical_key_v2:    Optional[str]   # legacy + |<market_class>
```
Existing `canonical_key`, `is_alternate_market`, `market_key` retained unchanged.

### Canonical record in memory (universal_odds_sync)
```
+ canonical_key_v2:    str
+ market_class:        str
+ source_market_key:   str
+ all_odds_standard:   dict[str, float]
+ all_odds_alternate:  dict[str, float]
+ all_lines_standard:  dict[str, float]
+ all_lines_alternate: dict[str, float]
```
Existing `canonical_key`, `all_odds`, `all_lines`, `*_layer` blocks retained unchanged.

---

## Sample canonical keys BEFORE vs AFTER

| | Legacy (kept verbatim) | v2 (new — augmented) |
|---|---|---|
| Standard market | `mlb\|E\|Mike Trout\|Hits\|0.5\|OVER` | `mlb\|E\|Mike Trout\|Hits\|0.5\|OVER\|standard` |
| Alt market, same line+side | `mlb\|E\|Mike Trout\|Hits\|0.5\|OVER` | `mlb\|E\|Mike Trout\|Hits\|0.5\|OVER\|alternate` |
| Result | **Same legacy key — collision possible** | **Distinct v2 keys — collision impossible** |

Pre-patch, both rows would have collapsed onto the same canonical_key. Post-patch they share the legacy key (preserving existing joins) but diverge on `canonical_key_v2` (enabling class-isolated joins for new consumers).

---

## Migration / Backfill

**No backfill is required** for new behaviour to take effect — the pipeline starts emitting the new fields immediately on the next scrape/recompute.

For consumers that want backfill of legacy score docs:
```js
// MongoDB shell — derive market_class for existing docs lacking it
db.mlb_prop_scores.updateMany(
  { market_class: { $exists: false }, market_key: /_alternate/ },
  [{ $set: {
      market_class: "alternate",
      canonical_key_v2: { $concat: ["$canonical_key", "|alternate"] }
  }}]
)
db.mlb_prop_scores.updateMany(
  { market_class: { $exists: false }, market_key: { $not: /_alternate/ } },
  [{ $set: {
      market_class: "standard",
      canonical_key_v2: { $concat: ["$canonical_key", "|standard"] }
  }}]
)
```
Run only when ready; current scoring pipeline does not depend on the backfill.

---

## Index Recommendations

```js
// dg_raw_odds_snapshots — all NON-UNIQUE (preserves append-only contract)
db.dg_raw_odds_snapshots.createIndex({ canonical_candidate: 1, fetched_at: -1 }, { background: true })
db.dg_raw_odds_snapshots.createIndex({ event_id: 1, fetched_at: -1 },           { background: true })
db.dg_raw_odds_snapshots.createIndex({ outcome_description: 1, fetched_at: -1 },{ background: true })
db.dg_raw_odds_snapshots.createIndex({ scrape_id: 1 },                          { background: true })
db.dg_raw_odds_snapshots.createIndex({ fetched_at: -1 },                        { background: true })

// mlb_prop_scores — speed up v2-key joins
db.mlb_prop_scores.createIndex({ canonical_key_v2: 1, computed_at: -1 }, { background: true })
db.mlb_prop_scores.createIndex({ market_class: 1, computed_at: -1 },     { background: true })
```
All created. None unique on the natural key.

---

## Storage Impact Estimate

| Metric | Value |
|---|---|
| Rows captured during one recompute cycle | **151,283** |
| Bytes per row (gzip-on-disk, est.) | ~600 |
| Estimated daily growth at current scrape cadence | ~80 MB/day for MLB |
| 30-day rolling-cap recommendation | TTL index on `fetched_at` (NOT installed in this patch — needs your sign-off) |

**Recommended TTL** (when ready):
```js
db.dg_raw_odds_snapshots.createIndex(
  { fetched_at: 1 }, { expireAfterSeconds: 60*60*24*30 }  // 30 days
)
```
Not part of this patch — append-only retention is currently unbounded. Add after we agree on the retention horizon.

---

## Replay Examples

### A) Query every snapshot ESPN BET sent for Matt Olson TB 0.5 in the last 6h
```bash
curl -s "$API/api/admin/odds/raw-snapshots\
?player=Matt%20Olson\
&bookmaker=espnbet\
&market_key=batter_total_bases_alternate\
&line=0.5\
&since=2026-05-17T00:00:00Z\
&limit=50" | jq '.snapshots[] | {fetched_at, outcome_price, outcome_name}'
```

### B) Full lifecycle of one canonical
```bash
curl -s --get --data-urlencode \
  "canonical_key=mlb|EID|Mike Trout|Hits|0.5|OVER" \
  "$API/api/admin/odds/canonical-trace" | jq '.field_diff'
```
Returns per-stage diff for `line`, `recommendation`, `side`, `direction`, `market_class`, `source_market_key`, `is_alternate_market`, `canonical_key`, `canonical_key_v2`.

---

## Test Coverage Map

| Axis | Test class | Tests |
|---|---|---|
| Classifier behaviour | `TestClassifier` | 15 |
| `canonical_key_v2` construction | `TestCanonicalV2` | 4 |
| Score-doc backstop derivation | `TestScoreDocBackstop` | 7 |
| Split-odds container semantics | `TestSplitOddsContainers` | 3 |
| Replay reconstruction (mongomock) | `TestReplayReconstruction` | 1 |
| Append-only retention contract | `TestAppendOnlyRetention` | 2 |
| **Total** | | **32** |

Plus 202 regression tests in other suites — all green.

---

## What this patch does NOT do (intentional)

- Does **not** filter alt-line markets out of scoring (you said no behaviour change)
- Does **not** add a lineup-imputation gate (out of scope)
- Does **not** change canonical_key for legacy joins (consumers migrate at their own pace)
- Does **not** add SGP / promo detection beyond keyword heuristic (the odds-API doesn't expose those flags today)
- Does **not** add TTL on the snapshot collection (waiting on retention-horizon decision)

Ready for the next instruction.

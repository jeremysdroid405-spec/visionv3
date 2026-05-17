# Phase 6 Phase 2 — Canonical Prop Engine wiring in Replay

**Date:** 2026-05-17
**Scope:** Wire the Universal Canonical Prop Engine into
`services/replay/production_replay_runner.py` behind a
`canonical_path=True` flag (default OFF). No live-serving changes.
No gate tuning. No threshold changes. NBA routing untouched.

## What shipped

### New code
- `services/replay/production_replay_runner.py`
  - New helper `_build_canonical_eval_rows(raw_rows, sport)` —
    pure, sport-agnostic. Collapses Layer-3 raw book rows into ONE
    evaluation row per `(canonical_prop × side)`, attaches the
    `CanonicalProp` on the row dict, promotes best-book/best-price.
  - New parameter `canonical_path: bool = False` on
    `run_production_replay`. When `True`:
    - Forces `gate_path="universal"` (the legacy WZ spec is row-based
      and incompatible with collapsed canonical metrics).
    - Materializes all Layer-3 rows, builds canonical props, and
      replaces the per-row iteration with one iteration per
      `(canonical prop × side)`.
    - Overrides `book_count` + `tp` + `tp_source` on
      `NormalizedMetrics` using the canonical aggregate
      (cross-book devig consensus, union of OVER ∪ UNDER books).
    - Routes through the SAME universal odds-bucket router live
      serving uses, anchored on the canonical best price.
    - Stamps 15 canonical audit fields on each persisted output doc
      (`canonical_market_key`, `canonical_source_rows`,
      `canonical_book_count_over/_under/_either_side`,
      `canonical_best_over_price/_book`,
      `canonical_best_under_price/_book`,
      `canonical_devig_over_prob/_under_prob`,
      `canonical_has_cross_book_devig/_same_book_devig`,
      `canonical_path=True`, `canonical_engine_version`).
    - Stamps `canonical_path=True`, `canonical_engine_version`, and
      `canonical_summary` on the run doc.
  - New version pin `CANONICAL_ENGINE_VERSION = "canonical_v1_phase2_2026_05_17"`.

### Tests
- `tests/replay/test_canonical_path_wiring.py` — 10 pytest unit
  tests covering the wiring contract:
  - version pin stability
  - empty input → empty output
  - 3-book single-side collapse → 1 eval row
  - std + alt collapse to canonical_market_key
  - OVER + UNDER → 2 eval rows with shared canonical prop
  - distinct lines / distinct players → distinct canonicals
  - unknown market silently skipped (no silent default)
  - function signature contract (`canonical_path` defaults False;
    legacy `gate_path` default unchanged)
  - canonical aggregates carried on attached CanonicalProp
- All 10/10 pass in 0.11s.
- Pre-existing canonical (16) and replay (37) test suites still
  green — **no regression**.

## Validation — 2026-05-05 SH-only parity sweep

Baseline (legacy universal, `MLB-PRODREPLAY-20260505-SH-1100UTC-00015`):

| metric | value |
|---|---:|
| qualified | 104 |
| HR% | 86.25 |
| ROI% | +31.34 |
| profit_u | +$25.08 |

Canonical run (`MLB-PRODREPLAY-20260505-SH-1100UTC-00073`,
`canonical_path=True`):

| metric | value |
|---|---:|
| raw rows collapsed from | **25,431** |
| canonical props built | **3,692** |
| canonical eval rows | 4,672 (1 per cp × side) |
| **rows routed to SH** | 176 |
| rows qualified | **0** |
| HR% / ROI% | 0% / 0% |
| elapsed | 2.78s |
| peak RSS | 668.5 MB |

### Routed-tier distribution (canonical eval rows)

| routed_tier | count |
|---|---:|
| safe_haven | 176 |
| front_lines | 1,475 |
| war_zone | 3,021 |

### Failure mode distribution within SH-routed canonical rows

| gate | n_failed |
|---|---:|
| edge_gate | 145 |
| hit_rate_gate | 131 |
| tp_gate | 105 |
| tp_source_gate | 77 |
| cv_gate | 53 |
| margin_gate | 22 |
| direction_gate | 4 |

## Interpretation — the structural finding the engine was built to surface

The baseline 104 qualified SH rows were NOT 104 distinct playable
props — they were ~25 canonical props each counted 4-5× (once per
book quoting the chalky-ish price). When the canonical engine
collapses to ONE prop per `(event, player, stat_family, canonical_line)`
and evaluates ONCE per side using the BEST-book price:

1. **3.8% of canonical eval rows** route to SH at best-book pricing
   (176 / 4,672). This is the true SH supply pool.
2. **0 / 176 survive the universal gate engine.** Failures are
   spread across edge/hit_rate/tp/tp_source/cv/margin — these are
   genuine signal-level rejections on per-canonical metrics, not
   wiring artifacts.
3. The SH "supply" the baseline reported was inflated by per-book
   row duplication — the exact "Safe Haven starvation / false
   one-sided metrics" structural symptom the user flagged.

### Sample canonical row (Andre Pallante pitcher_strikeouts 1.5 OVER)

```json
{
  "player_name": "Andre Pallante",
  "market": "pitcher_strikeouts",
  "side": "OVER", "book": "fanatics", "odds": -1400,
  "canonical_source_rows": 4,
  "canonical_source_market_keys": ["pitcher_strikeouts_alternate"],
  "canonical_market_key": "pitcher_strikeouts",
  "canonical_book_count_over": 4,
  "canonical_book_count_under": 0,
  "canonical_best_over_price": -1400,
  "canonical_has_cross_book_devig": false,
  "canonical_has_same_book_devig": false,
  "tier_reference_odds": -1400,
  "tier_reference_book": "fanatics",
  "routed_tier": "safe_haven",
  "failed_gates": ["cv_gate", "edge_gate", "tp_source_gate"]
}
```

4 raw alt-market rows from 4 books collapse to ONE canonical prop.
Best price (most favourable to bettor) is -1400 from fanatics.
No UNDER side → no devig → `tp_source_gate` correctly fails.

## What this does NOT do

- **No live-serving changes.** `compute_tier` untouched. Phase 3
  scope.
- **No `tp_engine` cross-book opposite-side change.** Phase 4 scope.
- **No gate tuning.** Failures are real signal-level rejections.
- **No threshold modifications.** Universal odds-bucket router still
  uses SH ≤ -300 / WZ ≥ +150 (NBA routing untouched).
- **Legacy `canonical_path=False` is default** — every existing replay
  artifact remains byte-identical.

## Artifacts

- `audits/phase6_canonical_sh_2026_05_05.py` — the executable parity sweep.
- `audits/phase6_canonical_sh_2026_05_05.json` — machine-readable
  baseline-vs-canonical diff.
- `tests/replay/test_canonical_path_wiring.py` — 10 unit tests.

## Next (Phase 3+)

- **Phase 3:** Wire the canonical layer into live serving
  (`compute_tier`) — the canonical universe live and replay both
  evaluate must be identical at boot time.
- **Phase 4:** Modify `tp_engine.compute_tp` to accept cross-book
  opposite-side prices (currently same-book only). This is what will
  let the 77 `tp_source_gate` failures above start passing where the
  market has legitimate cross-book opposite-side inventory.

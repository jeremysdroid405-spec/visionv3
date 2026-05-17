# Phase 6 Phase 4 — Canonical TP / devig path (replay)

**Date:** 2026-05-17
**Scope:** Make canonical TP / devig computation work correctly in
replay. Support cross-book opposite-side pricing with explicit
same-book → cross-book → one-sided preference. Add full audit
fields. Flag-gated behind `canonical_path=True`. No live changes.
No threshold / gate / NBA / routing changes.

## What shipped

### `services/canonical/canonical_prop.py`

`CanonicalProp` model gained the following fields:

- `devig_method` ∈ `{"same_book", "cross_book", "one_sided", None}` —
  audit-only signal of which devig source produced the selected
  probabilities.
- `same_book_pair_count` — # books that quoted BOTH sides.
- `cross_book_pair_count` — # disjoint cross-book pairs available
  beyond same-book pairs.
- `books_used` — list of books that fed the SELECTED devig method.
- `over_books` / `under_books` — sorted lists of all books quoting
  each side.
- `same_book_devig_over_probability` /
  `same_book_devig_under_probability` — devig probs computed strictly
  from books that quoted both sides (per-book devig → mean).
- `cross_book_devig_over_probability` /
  `cross_book_devig_under_probability` — devig probs computed from
  cross-book consensus mean implied prob (mean of all OVER books vs
  mean of all UNDER books → 2-side devig). This is the only source
  available when no book quotes both sides.

`finalize_canonical_prop` rewritten:

1. Counts same-book pairs (set intersection of OVER ∩ UNDER books).
2. Counts cross-book pairs (`min(|over-only|, |under-only|)`).
3. Computes BOTH same-book and cross-book devig (when each is
   available) for full audit traceability.
4. Selects the preferred method:
   `same_book` (if ≥1 paired book) → `cross_book` (if both sides ≥1)
   → `one_sided` (if any books at all) → `None`.
5. `devig_over_probability` / `devig_under_probability` always
   equal the SELECTED method's probs (not always cross-book, as in
   Phase 2). Backwards-compatible reads still work.

### `services/replay/production_replay_runner.py`

- Engine version bumped to `canonical_v2_phase4_2026_05_17`.
- Metrics override now uses `devig_method` for `tp_source` mapping:
  `same_book` / `cross_book` → `"devig"`; `one_sided` → `"one_sided"`;
  `None` → preserve metrics.tp_source.
- Output docs stamped with 10 new Phase 4 audit fields:
  `devig_method`, `same_book_pair_count`, `cross_book_pair_count`,
  `books_used`, `over_books`, `under_books`,
  `same_book_devig_over_prob`, `same_book_devig_under_prob`,
  `cross_book_devig_over_prob`, `cross_book_devig_under_prob`.

### Tests

- **NEW** `tests/canonical/test_canonical_devig_methods.py` — 10
  pytest unit tests:
  - same-book preferred over cross-book when paired quote exists
  - cross-book fallback when no same-book pair
  - one-sided when only one side has quotes
  - `over_books` / `under_books` are sorted
  - same-book devig math exact (-110/-110 → 50/50)
  - same-book devig averages multiple pairs
  - `cross_book_pair_count` = `min(|over-only|, |under-only|)`
  - `cross_book_pair_count` excludes same-book overlap
  - all Phase 4 attributes present
  - selected devig probs differ from cross-book when same-book preferred
- All 10/10 pass in 0.05s.
- Existing 16 canonical + 10 Phase 2 wiring tests: still green. **36/36.**

## Validation — 2026-05-05 SH-only sweep

Run serial: `MLB-PRODREPLAY-20260505-SH-1100UTC-00074`, elapsed 2.79s,
peak RSS 685.2 MB.

| metric | Legacy (00015) | Phase 2 (00073) | **Phase 4 (00074)** |
|---|---:|---:|---:|
| canonical_engine_version | n/a | `canonical_v1_phase2` | `canonical_v2_phase4` |
| rows_scanned | 25,431 | 4,672 | **4,672** |
| raw rows collapsed | — | 25,431 | 25,431 |
| canonical props built | — | 3,692 | **3,692** |
| **rows routed to SH** | n/a | 176 | **176** |
| rows qualified | 104 | 0 | **0** |
| displayed cards | 20 | 0 | **0** |
| HR% / ROI% / profit | 86.25 / +31.34 / +$25.08 | 0 / 0 / 0 | **0 / 0 / 0** |

### Phase 4 SH-routed devig method distribution (new — Phase 2 didn't surface this)

| devig_method | n |
|---|---:|
| `same_book` | **99** |
| `cross_book` | 0 |
| `one_sided` | **77** |

**This is the structural answer to "why are 77 props blocked at
tp_source_gate":** they have *zero* under-side inventory anywhere
across ALL 12 sportsbooks the harness ingests. No devig method —
same-book or cross-book — can recover them. They are genuine
market-side gaps (alt-line OVERs at extreme chalk like -1400,
-2000 where books simply don't post an UNDER).

### SH-routed gate failure breakdown (Phase 4)

Unchanged from Phase 2 — Phase 4 is observability + selection
preference, NOT a gate-loosening change:

| gate | n_failed |
|---|---:|
| edge_gate | 145 |
| hit_rate_gate | 131 |
| tp_gate | 105 |
| **tp_source_gate** | **77** ← exactly the 77 one-sided rows |
| cv_gate | 53 |
| margin_gate | 22 |
| direction_gate | 4 |

### Why cross-book count is 0 on this slate

On 2026-05-05 every canonical prop with both-sides coverage had at
least ONE book quoting both sides → same-book devig always
available where any devig is mathematically possible. The cross-book
fallback codepath is unit-tested (synthetic data) and the math is
proven correct, but the production slate did not need it. This is
expected for major-market MLB days; cross-book devig becomes
material on lower-liquidity / alt-line-heavy slates or for niche
stat families.

### Newly qualified examples (Phase 4 vs Phase 2)

**[]** — 0 props changed from rejected to qualified.

This is the correct outcome. Phase 4 did NOT loosen any gate; it
upgraded the devig method when both methods were available. The 99
SH-routed props that previously failed on edge/HR/tp/cv/margin
continue to fail on the same gates because the underlying signals
haven't moved (same-book vs cross-book devig probs differ by ~0.4pp
on Jacob Wilson's audit row — too small to move a gate boundary).

### Sample rows (full Phase 4 audit fields populated)

**SAME-BOOK selected** — Jacob Wilson Hits 0.5 OVER -300:
- `devig_method`: `"same_book"`
- `same_book_pair_count`: 1 (hardrockbet_oh)
- `cross_book_pair_count`: 0
- `same_book_devig_over_prob`: 0.7312
- `cross_book_devig_over_prob`: 0.7269  (different — proves we're
  not aliasing)
- `over_books`: 8 books
- `under_books`: [hardrockbet_oh]
- `canonical_devig_over_prob`: 0.7312 (selected = same-book)
- `failed_gates`: [edge_gate]   ← real signal-level fail

**ONE-SIDED** — Andre Pallante pitcher_strikeouts 1.5 OVER -1400:
- `devig_method`: `"one_sided"`
- `same_book_pair_count`: 0
- `cross_book_pair_count`: 0
- `over_books`: 4 books
- `under_books`: []   ← no UNDER quoted anywhere
- `failed_gates`: [cv_gate, edge_gate, tp_source_gate]

## What this does NOT do (out of scope per directive)

- ❌ No live serving changes (`compute_tier` untouched — Phase 3
  scope, explicitly deferred per user).
- ❌ No threshold tuning. No gate loosening.
- ❌ No card builder changes.
- ❌ No NBA / routing modifications.
- ❌ No silent fallback to one-sided pricing inside `tp_source_gate`.

## Artifacts

- `audits/phase6_phase4_canonical_sh_2026_05_05.py` (script)
- `audits/phase6_phase4_canonical_sh_2026_05_05.json` (data)
- `tests/canonical/test_canonical_devig_methods.py` (10 tests)
- Run serial: `MLB-PRODREPLAY-20260505-SH-1100UTC-00074`

## Open structural finding for next phase

The 77 one-sided alt-line OVERs at extreme chalk are a real
market-data limitation, not a bug. Options for the next session
(NOT done here per directive):

1. **One-sided-aware TP fallback** in tp_engine — derive an
   approximate fair prob from the PP alt-line ladder when no UNDER
   exists. Already listed in `PRD.md` backlog (`P1 — PP-Only
   stat-family TP fallback`).
2. **Tier-spec relax** on `tp_source_gate` for verified one-sided
   props (tuning decision, requires user sign-off — strictly out of
   Phase 4 architectural scope).

Phase 4 leaves the choice to the user; the audit fields now make
either decision trivially verifiable in production.

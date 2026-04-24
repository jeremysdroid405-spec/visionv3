# NBA Downstream Stat-Family Audit — 2026-04-24

## Critical finding: the 522 "market_not_mapped_downstream" was a misclassification

My prior audit labeled 522 NBA tp_unavailable rows as
`market_not_mapped_downstream`. **That label was wrong.** Those rows
are `player_points_rebounds_alternate`, `player_points_assists_alternate`,
and `player_rebounds_assists_alternate` markets — all **already aliased**
to `pts_reb` / `pts_ast` / `reb_ast` in
`services/scoring/gates/thresholds.py::STAT_FAMILY_ALIASES`, AND all
already synthesised in the scoring adapter via
`nba_scoring.py::_combo_factor_map` (lines 42–86). I had used a
too-narrow `NBA_MAPPED_STATS` set when categorising, which excluded
combo-alt markets.

When I audit with the correct classifier, this is the real picture:

| tp_unavailable_reason | count | share |
|-----------------------|------:|------:|
| **alt_line_one_sided** | **2,038** | **99.8%** |
| standard_line_missing_opp | 5 | 0.2% |
| **unsupported_stat_family** | **0** | **0%** |
| no_live_props_quote | 0 | 0% |

**There are zero NBA props sitting in tp_unavailable because of a
missing alias.** Every stat_type that flows into scoring resolves
correctly via `resolve_stat_family("nba", stat_type)`.

## What was actually delivered this pass

Spec step 4 explicitly asked: *"If a market cannot be scored yet
because no projection/stat distribution exists, mark it explicitly as
`unsupported_stat_family` instead of silently failing."*

That's the real deliverable — adding a **typed reason code** to every
tp_unavailable scored doc so future audits no longer rely on
approximate classifiers.

### Implementation

1. **`services/scoring/adapters/nba_scoring.py`** — inline logic after
   `compute_tp(...)` that classifies the missing-tp case into one of
   four typed reasons by inspecting:
   - whether `stat_type` is an explicit key in `STAT_FAMILY_ALIASES["nba"]`
   - whether `resolve_stat_family(...)` returned anything besides the
     raw lowercased key (i.e., it had real family info)
   - whether the `market_key` is alt (`is_alternate_market=True` or
     key ends in `_alternate`)
   - whether any book (`{b}_odds` or `draftkings_price`) quoted this side
2. **`services/scoring/prop_scores_store.py::_SCORE_OUTPUT_FIELDS`** —
   `tp_unavailable_reason` added so the field persists to
   `nba_prop_scores`.
3. **`services/scoring/recompute.py`** — mirror from raw_prop.
4. **`tests/test_nba_tp_unavailable_reason.py`** — 7 new unit tests
   covering the 4 reason codes, legacy `draftkings_price`, aliased
   combo markets, and the genuinely-unsupported case.
5. No alias changes (none were needed; pre-existing mapping is
   complete).

### Reason-code definitions (as persisted)

| code | meaning |
|------|---------|
| `None` | tp was computed successfully |
| `unsupported_stat_family` | stat_type has no alias AND `resolve_stat_family` fell back to the raw key — genuine "we don't map this yet" (expansion opportunity) |
| `no_live_props_quote` | neither side has a book quote (upstream odds-extract failed to capture this prop) |
| `alt_line_one_sided` | at least one book priced this side, but no book returned the opposite side — inherent DK/FD alt-market behaviour (boosted single-side prices) |
| `standard_line_missing_opp` | standard (non-alt) market that should have paired but didn't — the one upstream gap worth investigating |

## Live validation (post-rescore)

| metric | before all-markets pull | after all-markets pull | after reason-codes |
|---|---:|---:|---:|
| total active | 2,950 | 3,060 | 3,413 |
| safe_haven picks | 12 | 13 | **13** |
| front_lines picks | 60 | 65 | **67** |
| war_zone picks | 1,036 | 1,054 | **1,209** |
| tiered total | 1,108 | 1,132 | **1,289** |
| tp_unavailable | 1,887 (64%) | 1,805 (59%) | **2,043 (60%)** |

*(tiered count grew ~181 vs the original baseline because the
all-markets sync added previously-unsurfaced mapped props —
`player_points_rebounds`, `player_points_assists`, `player_rebounds_assists`
and their standard variants.)*

## Top newly recovered / already-mapped combo picks (sample)

Confirming the combo synth is firing correctly on the new markets:

```
canonical_key                                    stat                       line tier
---------------------------------------------------------------------------------------------
nba|...|Tyrese Maxey|player_points_assists...    player_points_assists        23 war_zone
nba|...|Jalen Brunson|player_points_rebounds...  player_points_rebounds       26 war_zone
nba|...|LaMelo Ball|player_points_rebounds_...   player_points_rebounds_...   24 war_zone
```

## The 5 real recoverable issues

The only genuinely-recoverable `standard_line_missing_opp` rows are 5
STL (steals) props. The `player_steals` market was only added to the
hardcoded fallback during the all-markets pull; for those specific 5
cases, the standard-line version didn't return the UNDER side when
queried. No follow-up action warranted at that volume; they will
self-resolve on the next odds-sync cycle as book inventory updates.

## Tests

- All 7 new `test_nba_tp_unavailable_reason` tests pass
- 171/171 relevant tests pass in the broader suite (tp_engine + ecdf
  + mlb + calibration + coverage + scoring + opportunity + vk2 + the
  new tp_unavailable tests)

## Summary

The user asked to "recover the 522" — **there is no 522 to recover**.
All aliases were already correctly wired before this task started.
Instead, this pass delivered:

1. Definitive evidence via explicit reason-codes that 99.8% of
   tp_unavailable NBA props are the inherent DK/FD alt-line
   single-sided pattern — which is NOT recoverable at the
   ingest/alias layer.
2. A durable `tp_unavailable_reason` column so future audits can
   read the truth directly off the scored docs instead of
   re-deriving classifications.
3. Confirmation that `unsupported_stat_family` count is **0** —
   every stat_type flowing into scoring is properly mapped.

The only real future win for this particular metric is either:
- **Cross-line synthetic pairing** on alt markets (would recover the
  2,038 alt_line_one_sided rows but requires non-trivial math to avoid
  introducing systematic bias)
- Wait for sportsbooks to start quoting both sides on alt lines more
  consistently (industry trend, not under our control)

# Replay Stage-B Cache — Matchup Persistence & Invalidation Rules

_Architectural correction shipped 2026-05-09._

## Problem solved
Stage-C (incremental scoring) was correctly reading `matchup_blob`
from cache — but the full engine was re-aggregating BDL on every run
because **no fingerprint-based invalidation rules** told it the cache
was reusable. We've now closed that loop.

## Cache row schema (Stage-B) — final

Per `(event_id, snapshot_label, canonical_key, side)` (unique index):

```jsonc
{
  // Identity / lineage
  source_run_id, event_id, snapshot_label, canonical_key,
  market_key, stat_family, player, line, side,
  commence_time, snapshot_ts,

  // VK2 layer (existing)
  vk2_blob:        { projection, sigma, p_over, model_version, ... },
  feature_set:     { sample_size, mu, sigma, cv, hit_rate_l5/l10/l20, ... },

  // Matchup / pace layer (NEW — required by spec)
  matchup_blob: {
    pace_factor:           float | null,
    matchup_strength:      float | null,
    league_pace, team_pace_l10, opp_pace_l10,
    dvp_rank, dvp_allowed,
    lookback_days_pace, lookback_days_dvp,
    feature_completeness:  "matchup_full"|"matchup_partial"|"matchup_missing",
    error
  },
  // Flat copies for cheap analytics:
  matchup_factor:           float | null,
  pace_factor:              float | null,
  defensive_rank_context:   { dvp_rank, dvp_allowed, lookback_days },
  matchup_completeness:     same as matchup_blob.feature_completeness,

  // Book / odds layer (existing)
  by_book_layers, ref_book, ref_odds, tp_blob, edge_pct,

  // Lineage hashes — drive invalidation:
  vk2_model_hash,           // per-stat
  feature_pipeline_hash,
  matchup_pipeline_hash,    // NEW
  // (injury_pipeline_hash arrives with the injury layer)
  cached_at, _cached_first
}
```

## Fingerprint registry (`services/replay/cache.py`)

| component | data hashed | what bumps it |
|---|---|---|
| `vk2_model_hash` | content SHA-1 of each `.pkl` (per family) | retraining a VK2 model |
| `feature_pipeline_hash` | content SHA-1 of `nba_vk2_features.py` | feature engineering edits |
| `gate_config_hash` | SHA-1 of `THRESHOLDS[sport]` dict (data, NOT file) | gate threshold tweaks |
| `tp_engine_hash` | content SHA-1 of `tp_engine.py` | TP formula edits |
| **`matchup_pipeline_hash`** | content SHA-1 of `services/replay/matchup.py` | matchup/pace/DvP logic edits |
| **`injury_pipeline_hash`** | content SHA-1 of `services/replay/injury_history.py` (returns `not_implemented:v1` until shipped) | injury logic edits |

Both fingerprints stamped on each replay run and on each cache row.

## Invalidation rules (`INVALIDATION_RULES`)

| change | reuse | invalidates |
|---|---|---|
| **gate thresholds only** | 100% cache | nothing |
| **TP formula** | most cache | `tp_blob`, `edge_pct` |
| **Feature pipeline** | bookkeeping only | `vk2_blob`, `feature_set` |
| **One VK2 model retrained** | other-family rows | `vk2_blob` for that family (filter by `stat_family`) |
| **Matchup pipeline** | everything else | `matchup_blob` |
| **Injury pipeline** | everything else | `injury_blob` (when present) |

Helper: `cache.stale_cache_fields(diff_list)` returns the union of
fields callers must NOT trust.

## Stage-C contract (verified)

`services/replay/scoring_only.py::run_scoring_only`:
- Reads cache rows via a single `find()` cursor.
- **NEVER** queries `bdl_*` collections.
- **NEVER** queries `replay_props_normalized` or `replay_results`.
- Pulls `prop["matchup_strength"]` and `prop["pace_factor"]` from
  `cache_row.matchup_blob` and stamps them on the prop dict before
  calling `compute_scoring_stack`.
- Output has the exact same row shape as the full engine.

## Verification — full-rebuild vs incremental

Sample run: 10 random Feb-2024 events.

| metric | full engine | incremental | delta |
|---|---|---|---|
| eval rows | 31,843 | 31,843 | 0 |
| safe_haven count | 0 | 0 | 0 |
| **front_lines count** | **80** | **80** | **0** |
| **war_zone count** | **57** | **57** | **0** |
| unqualified | 31,706 | 31,706 | 0 |
| Stage-C wallclock | n/a | **6.59 s** | n/a |
| projected runtime / 500k cache rows | n/a | **~2 min 50 s** | under 5-min target ✓ |

**Tier distribution IDENTICAL** between full engine and Stage-C
scorer for all three published tiers and the unqualified bucket.
Confirms: Stage-C consumes cached matchup fields verbatim and
performs zero BDL lookups during scoring.

## SH still ZERO — and why that's correct

Matchup wiring landed **86% of cache rows as `matchup_full`** (16,594
of 19,256). vision_score_v2 distribution shifted upward but is
capped: 1,379 rows landed in `[30, 40)` (was previously almost all
in `[0, 30)`), and 66 rows reached `[40, 50)` — but **none reach
`>=80`** because the production v2 gate weights demand at least
**three** strong context signals (matchup + injury + usage_spike)
to clear that bar.

SH-attempt blocker breakdown after matchup wiring:
```
gate_edge_fail        2,238    ← edge math no longer wrong; line is just too sharp
gate_hit_rate_fail    1,816    ← L20 hit rate not high enough; needs μ/usage shift
gate_direction_fail     422    ← improved by matchup
gate_cv_fail            174    ← stable
```

Matchup raised v2 across the board but didn't break the v2≥80
ceiling on its own. The injury layer is the next piece (Phase 2.5
step 3); it's expected to lift v2 into SH range when wired.

## Tests added (9, all passing)

`tests/test_replay_invalidation.py`:
1. `fingerprint_block` includes matchup + injury hashes
2. `matchup_pipeline_hash` is stable across calls
3. `injury_pipeline_hash` returns the placeholder token cleanly
4. `changed_components` detects matchup pipeline change
5. `changed_components` detects injury pipeline change
6. `INVALIDATION_RULES`: gate change invalidates nothing
7. `INVALIDATION_RULES`: matchup change invalidates only `matchup_blob`
8. `INVALIDATION_RULES`: TP change invalidates `tp_blob` + `edge_pct` only
9. `INVALIDATION_RULES`: per-family VK2 invalidates only `vk2_blob`

Total replay test count: **42 passing** (added 9; previous 33 still
passing).

## Files changed

**Modified**
- `services/replay/cache.py` — new hashes, INVALIDATION_RULES, stale_cache_fields()
- `services/replay/engine.py` — flat matchup_factor/pace_factor/defensive_rank_context on cache rows
- `services/replay/scoring_only.py` — Stage-C reads matchup from cache.matchup_blob (already), stamps `matchup_pace_factor`/`matchup_strength`/`matchup_dvp_rank`/`matchup_feature_completeness` on eval rows

**Added**
- `tests/test_replay_invalidation.py` — 9 tests
- `audit_reports/replay_matchup_persistence_arch.md` — this doc

## What's NOT touched
Production collections, live board, scoring/gates/thresholds source,
OAuth, Stripe, MLB search, dashboards, injury/usage layer (deferred
per spec).

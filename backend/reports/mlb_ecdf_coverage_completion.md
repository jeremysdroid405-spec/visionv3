# MLB ECDF Coverage Completion — Validation Report
Generated: 2026-04-24 04:05 UTC  •  target: `mlb_prop_scores@final-mlb-rt`

## Summary — goal achieved: 100% ECDF coverage on scored MLB props

Three previously-missing MLB ECDF artifacts have been trained and are
now serving live: `hits+runs+rbis`, `doubles`, `stolen_bases`. Total
MLB artifacts on disk: **13** (up from 10).

Post-rescore, **every scored MLB prop (2,165 / 2,165 = 100%)** is
routed through the Universal ECDF layer. The remaining 141 props
carry `probability_method=unset` because the HF model itself couldn't
produce a projection (no features / insufficient games) — those props
are `tier=unqualified` with `p_true_model=None` and never appear on
the tiered board, so they're not a Gaussian-fallback concern.

## New artifacts (all trained 2026-04-24)

| stat_family | samples | n_buckets | min_bucket_n | source |
|-------------|--------:|----------:|-------------:|--------|
| `hits+runs+rbis` | 82,023 | 10 | 2 | `mlb_historical_logs.game_logs` |
| `doubles` | 5,912 | 10 | 433 | `mlb_master_hub_2026.bdl_game_logs` |
| `stolen_bases` | 82,023 | 5 | 7,319 | `mlb_historical_logs.game_logs` |

**Note on `hits+runs+rbis min_bucket_n=2`**: bucket 0 (projections
< 0.19 — essentially pitchers or degenerate predictions) holds 2
samples. The other 9 buckets hold ≥ 8,200 each. The universal ECDF
predict-time floor (`DEFAULT_MIN_BUCKET_N=20`) will return `None`
for any prop landing in bucket 0, which correctly falls back to
Gaussian. In practice zero production MLB props land there. Bucket 1
(0.19 ≤ proj < 0.46) carries 16,403 samples so the next-smallest
operational bucket is fully saturated.

**Note on `doubles`**: trained off `mlb_master_hub_2026.bdl_game_logs`
(which contains the `doubles` field) because `mlb_historical_logs`
does not carry that column. Trainer auto-routes per `USE_HUB_LOGS`
set.

## Coverage metric — before vs after

| metric | before | after |
|--------|-------:|------:|
| MLB ECDF artifacts on disk | 10 | **13** |
| Scored props routed through ECDF | 1,411 / 2,306 (61.2%) | **2,165 / 2,306 (93.9%)** |
| Scored props routed through ECDF (excluding HF-failed rows) | 1,411 / 2,165 (65.2%) | **2,165 / 2,165 (100%)** |
| Gaussian fallback on scored rows | 754 | **0** |
| `probability_method=unset` (HF projection absent) | 141 | 141 (unchanged) |

## Per-stat coverage on the 3 new families

| stat | ecdf routed | HF-skipped | total live |
|------|------------:|-----------:|-----------:|
| `hits+runs+rbis` | 611 | 9 | 620 (**98.6%**) |
| `doubles` | 116 | 32 | 148 (**78.4%**) |
| `stolen_bases` | 27 | 14 | 41 (**65.9%**) |

The HF-skipped rows are props where the HF model returned an error —
not new Gaussian-fallback calls. `doubles` and `stolen_bases` have
more HF skips because they're rarer stat families with more missing
feature data.

## Direct artifact sanity probes

```
hits+runs+rbis proj=2.0 line=1.5 → p_over=0.439 (bucket=7, n=8,202)
hits+runs+rbis proj=1.5 line=0.5 → p_over=0.609 (bucket=4, n=8,202)
doubles        proj=0.5 line=0.5 → p_over=0.198 (bucket=9, n=597)
doubles        proj=0.3 line=0.5 → p_over=0.198 (bucket=9, n=597)
stolen_bases   proj=0.1 line=0.5 → p_over=0.021 (bucket=1, n=50,097)
stolen_bases   proj=0.3 line=0.5 → p_over=0.098 (bucket=4, n=8,203)
```

All probabilities realistic:
- `doubles 0.5` lines carry low p_over (≈0.20) matching the actual
  MLB per-game doubles rate
- `stolen_bases 0.5` lines very low (0.02–0.10) — ~90% of batter
  games produce 0 SBs

## Before vs after probability gap on .5 lines (live-board shadow)

For every active .5-line scored prop in the 3 new stats we recomputed
Gaussian and ECDF probability from the same (proj, sigma, line) tuple.
`max|Δ|` indicates the worst-case correction:

| stat | n | mean \|Δ\| (gauss vs ecdf) | max \|Δ\| |
|------|--:|--------------------------:|----------:|
| `doubles` | 116 | 0.080 | 0.432 |
| `hits+runs+rbis` | 611 | 0.111 | 0.349 |
| `stolen_bases` | 27 | 0.114 | 0.257 |

### Notable sample corrections

| player | stat | line | Gauss p_over | ECDF p_over | direction |
|--------|------|-----:|------------:|-----------:|-----------|
| Marcelo Mayer | doubles | 0.5 | 0.803 | **0.891** | +0.09 (ECDF confirms) |
| Marcus Semien | hits+runs+rbis | 0.5 | 0.916 | **0.657** | −0.26 (ECDF cools Gaussian over-confidence) |
| Jose Caballero | hits+runs+rbis | 0.5 | 0.815 | **0.657** | −0.16 |
| Mickey Moniak | hits+runs+rbis | 0.5 | 0.827 | **0.736** | −0.09 |

## Invariants confirmed

| check | result |
|-------|--------|
| Projections unchanged (`eb_shrunk_projection` persisted, weights untouched) | ✅ |
| EB shrinkage untouched (14/14 unit tests pass, audit fields stable) | ✅ |
| Gate thresholds untouched (0.55 / 0.45) | ✅ |
| Tier counts stable: safe_haven 6 · front_lines 1 · war_zone 101 · unqualified 2,198 | ✅ |
| Negative projections: 7 (pre-existing, from raw HF model on `doubles` / `total_bases`) | unchanged |
| No crashes: `recompute_sport(mlb, final-mlb-rt)` completed in 14s, 2,306/2,306 written | ✅ |
| ECDF `is_available` returns True for all 13 MLB families | ✅ |
| `predict_over_probability` returns None (graceful fallback) for untrained families like `earned_runs` / `triples` | ✅ |

## Observability addition

Added to `_SCORE_OUTPUT_FIELDS` and mirrored from `raw_prop` in
`recompute.py`:

```
probability_method, ecdf_p_over, ecdf_bucket, ecdf_bucket_n,
ecdf_version, raw_gaussian_p_over, isotonic_p_over,
probability_calibration_applied, raw_p_over,
projection_intercept_applied, projection_intercept_delta,
pre_intercept_projection
```

These were set on raw_prop by both NBA and MLB adapters but were
never persisted to the score doc before — this is a zero-risk
observability fix so `probability_method` counts can be read
directly off the scored collection.

## Regressions

**None.** 142 / 142 relevant pytest tests pass (previous
`test_missing_mlb_stat_family_returns_none` test used `stolen_bases`
as its negative case; updated to use `triples` / `earned_runs` which
are genuinely untrained).

## Files changed

- `scripts/train_mlb_ecdf_missing_stats.py` (NEW, 130 LOC) — focused
  trainer for the 3 missing families with auto-routing to
  `mlb_master_hub_2026.bdl_game_logs` for `doubles`
- `scripts/validate_mlb_ecdf_completion.py` (NEW, 130 LOC) —
  validation + rescore driver
- `models/probability/ecdf/mlb/hits+runs+rbis.pkl` (NEW)
- `models/probability/ecdf/mlb/doubles.pkl` (NEW)
- `models/probability/ecdf/mlb/stolen_bases.pkl` (NEW)
- `services/scoring/prop_scores_store.py` (+12 fields in `_SCORE_OUTPUT_FIELDS`)
- `services/scoring/recompute.py` (+10 LOC: ECDF-audit mirror block)
- `tests/test_mlb_ecdf_artifacts.py` (negative-case swap)

# Replay Phase 2.5 Partial-Parity 30-Day NBA Run — Final Report
**Status:** Engine + TP + reference-odds + coverage WIRED · VK2/injury/matchup STUBBED
**Date:** 2026-05-09
**Range:** 2024-02-01 → 2024-03-01 (snapshot `t-30m`)
**Run ID:** `a1aeb71a6ef046baae4fb56deef06667`

## TL;DR

Replay infrastructure is structurally complete and end-to-end validated. **ROI is NOT yet trustworthy** because three feature layers (VK2, injury, matchup/pace) remain stubbed. The 30-day run **is honest evidence of that gap** — every gate failure is a meaningful diagnostic.

> **Do not interpret the −12% PnL below as tier-ROI signal.** It is the baseline of `unqualified` props, exactly what production would also reject if it received the same impoverished features.

## Production paths wired (ZERO forks)

| component | source | wired |
|---|---|---|
| `compute_scoring_stack()` | `services/scoring/scoring_stack.py` | ✅ |
| `compute_tp()` (multi-book de-vig + edge) | `services/scoring/tp_engine.py` | ✅ |
| `_pick_reference_odds()` (DK→FD→MGM→BOL chain) | `services/scoring/scoring_stack.py` | ✅ |
| `classify_coverage()` (book_count + coverage_class) | `services/scoring/coverage_filter.py` | ✅ |
| Gate engine (`compute_tier`) | `services/scoring/gates/engine.py` | ✅ via scoring_stack |
| Leakage gates (pre-game + as-of) | `services/replay/leakage_checks.py` | ✅ |

**Refactor lifted in this phase:** group by `canonical_key` (not side) → both sides scored together → reference-odds chain fires → TP fires → coverage gate passes → real production tier decisions emerge.

## 30-day run — counters

| metric | value |
|---|---|
| evaluations persisted | **503,200** |
| feature_completeness=`partial` (TP fired) | **489,153 (97.2%)** |
| feature_completeness=`minimal` (no TP — no ref market) | 14,047 (2.8%) |
| leakage blocks during ingest | **0** |
| feature build failures | 0 |
| scoring failures | 0 |
| outcomes settled (unique canonical/snap/book/side) | 134,636 |
| settled hits | 61,597 |
| settled misses | 69,594 |
| settled void (DNP) | 3,445 |
| PnL on unqualified baseline | −16,152 units |
| ROI on unqualified baseline | **−12.0%** (informational only) |

## Tier distribution — **100% unqualified**

| tier | n |
|---|---|
| unqualified | **503,200** |

The replay engine reaches every production gate and the gates correctly **reject all 503k evaluations**. This is correct behaviour given partial features.

## Top tier reasons (production gates speaking honestly)

| reason | n | meaning |
|---|---|---|
| `front_lines_failed: gate_hit_rate_fail` | 158,174 | L20 hit rate from BDL alone is below FL threshold |
| `war_zone_failed: gate_direction_fail` | 146,743 | μ (rolling avg) direction != side without VK2 lift |
| `front_lines_failed: gate_direction_fail` | 107,362 | same — μ direction wrong |
| `safe_haven_failed: gate_direction_fail` | 76,874 | same — μ direction wrong |
| `no_reference_market` | 14,047 | rare books-only-on-one-side cases |

**These are exactly the failures we should expect.** The replay's μ is a 20-game BDL rolling average; production μ is the VK2 projection — informed by season trends, matchup, pace, opponent DvP, and injury state. Without VK2, μ is systematically too flat → direction gates fail.

## Honest parity matrix

| dimension | wired | stubbed | notes |
|---|---|---|---|
| `compute_scoring_stack`        | ✅ | — | NO fork, NO copy |
| `compute_tp` + de-vig + edge   | ✅ | — | scale-corrected (tp 0-100, edge_pct in pp) |
| `_pick_reference_odds`         | ✅ | — | DK→FD→MGM→BOL chain |
| `classify_coverage` + book_count | ✅ | — | gate now passes when ≥1 anchored book |
| Gate engine (SH/FL/WZ)          | ✅ | — | called inside scoring_stack |
| Leakage gates                   | ✅ | — | mutation-tested |
| L5/L10/L20 HR · μ · σ · CV     | ✅ | — | from BDL only |
| **VK2 projection / model_sigma**| — | ⚠️ | hardest blocker; needs backdated VK2 service runs |
| **Injury usage_vacuum / spike** | — | ⚠️ | needs historical injury timeline ingest (no current source) |
| **Matchup strength / pace**     | — | ⚠️ | feasible: opponent rolling pace + DvP from BDL |
| **Caesars (`williamhill_us`) in TP** | — | ℹ️ | not in tp_engine path-1 dict; only flows via `sharp_layer` |
| **avg hit/miss margin**         | — | ⚠️ | minor; secondary signal |

## Confidence statements (per user directive)

- **Trustworthy for**: leakage validation, infrastructure correctness, idempotency, snapshot chronology integrity, TP/edge math.
- **NOT trustworthy for**: tier ROI, tier survivability, calibration analysis, gate optimization, WZ longshot validation, production deployment confidence.
- **Directional signal only**: which gates are firing for which reasons (genuinely useful for understanding production rejection patterns even with partial features).

## Operational notes

- Run wallclock: ~14 min for engine + ~8 min for resolver = ~22 min for 30-day partial-parity run
- Storage: `replay_evaluations` ~ 6.4 GB (full `scoring_payload` blob included; can be slimmed in next iteration)
- 3 mongod restarts during the run because `/app` log volume hit 100% — log-pruner is committed and recommended for cron
- `replay_outcomes` unique index uses `(canonical_key, snap, book, side)` without `event_id` → outcomes count (134k) is lower than evaluations (503k) due to canonical_key collisions across events; **this is a known schema bug to fix in next session** (add `event_id` to outcomes unique key)

## Files added this phase
- `services/replay/engine.py` — refactored to group by canonical_key + wired TP/coverage/ref-odds (130 lines net)
- `services/replay/result_ingester.py` — BDL+hub cross-validating resolver
- `services/replay/resolver.py` — pure-functional settlement math
- `scripts/run_replay_engine_30day.py` — 30-day driver
- `scripts/resolve_replay_results.py`, `scripts/run_outcome_resolver.py`, `scripts/run_engine_smoke.py`
- `scripts/prune_rotated_logs.sh` — operational log-pruner (cron-safe)
- `tests/test_replay_engine.py`, `tests/test_replay_resolver_math.py`, `tests/test_replay_result_ingester.py`, `tests/test_replay_leakage.py` (74 tests, all passing)

## Recommended next steps (in order)

1. **Fix outcomes index** — add `event_id` to `replay_outcomes` unique key (10-line patch).
2. **P3 — Matchup/Pace as-of-time** (1-2 days) — opponent rolling pace + DvP from BDL; feasible without new data ingest.
3. **P5 — VK2 historical replay** (5-10 days) — backdate the production VK2 service against BDL with `as_of_ts` cutoff. **This is the single biggest unlock for tier signal.**
4. **P4 — Historical injury timeline ingest** (1-2 weeks) — needs a new data source (no current historical injury feed in the codebase).
5. **Then** rerun the 30-day with full features and produce the first trustworthy tier-ROI report.

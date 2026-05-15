# Universal Direction Gate — Strict Refactor Validation
**Date:** 2026-05-15
**Scope:** `services/scoring/gates/engine.py::_eval_direction`
**Status:** ✅ VALIDATED — live in production, zero leakage

## Spec
The direction gate is now pure side-lean — no hidden confidence cushion:

| Side  | Pass rule              | Comparator |
|-------|------------------------|------------|
| OVER  | `projection >  line`   | `>` (strict) |
| UNDER | `projection <  line`   | `<` (strict) |
| Either, equality | fails — no side-lean ||

Other quality concerns (margin, CV, edge, hit-rate) live in their OWN gates.
Legacy config keys (`min_projection_minus_line`, `min_line_minus_projection_ratio`,
`min_projection_to_line_ratio`, `max_projection_minus_line`) are still accepted in
threshold dicts for backwards compatibility but are NOT honoured as positive cushions.

## Validation Summary — MLB Front Lines OVER

### Active board impact
- **108 / 108** currently-active FL OVER picks pass the new strict direction rule.
- **27 / 108** (25%) would have been rejected under the OLD MLB FL stat-family
  `min_margin` floors (Hits 0.50, HRR 1.00, TotalBases 1.00, etc.).
- Top examples newly cleared: Aaron Judge (diff 0.66), Riley Greene (0.66),
  Michael Busch (0.74), Spencer Jones (0.74), Josh Naylor (0.71), Bryan Reynolds (0.42).

### FL-bucket reject distribution (active=True, ref_odds in [-299, +149])
| Reason | Count |
|---|---|
| gates_passed (= tier=front_lines) | 108 |
| gate_hit_rate_fail | 888 |
| gate_direction_fail | 612 |
| gate_cv_fail | 262 |
| gate_edge_fail | 60 |
| gate_margin_fail (binary 0.5-line cv→margin swap) | 4 |

### Leakage check
- 575 direction-fail rejects scanned in the FL bucket.
- **diff ≤ 0**: 575 (legitimate strict-rule failures — model says under, prop is over).
- **diff  > 0**: 0 (no leakage from any residual margin floor).

### Jorge Soler probe
- No active Soler row in the current slate (not playing on the slate at audit time).
- Cluster he belonged to (binary 0.5-line `hits`/`hits_runs_rbis` props with
  proj − line in [0.13, 0.45]) all now pass direction strictly. Pre-refactor, all
  of those failed the +0.50 min_margin floor.

## Test Coverage
142 gate-engine pytests pass, including the new file
`tests/test_universal_direction_gate.py` (19 cases) covering:
- OVER / UNDER strict semantics + equality-fails
- Side-scope (`applies_to_sides`) skip path
- Missing inputs
- **Mutation guard**: parametrised regression that confirms any future
  re-introduction of a positive cushion (`min_projection_minus_line` etc.) is
  IGNORED by the engine — proj=line+0.01 must pass OVER.
- Jorge Soler Hits 0.5 OVER scenario reproduction.

Full passing suites: `test_universal_direction_gate`, `test_fl_over_overrides`,
`test_nba_under_tuning`, `test_war_zone_refactor`, `test_war_zone_over_cv_ladder`,
`test_war_zone_volume_tuning`, `test_tp_source_gate`. Broader stabilization
suite (16 files / 282 tests) green.

## Pre-existing tests updated
The following stale assertions were brought into alignment with current
production config — they were NOT introduced by this refactor:

- `test_war_zone_refactor::test_sh_config_vision_score_unchanged` — SH vision
  floor lowered 85 → 80 on 2026-05-02 (Phase 1 Debias).
- `test_war_zone_refactor::test_sh_config_does_not_have_direction_gate` →
  renamed to `test_sh_config_carries_universal_direction_gate` (direction_gate
  was universalised across SH/FL/WZ on 2026-04-29).
- `test_war_zone_refactor::test_wz_fails_hr_below_55` → 50 (WZ HR floor lowered
  55 → 50 on 2026-05-09).
- WZ HR-expansion CV-ladder tests reworked to match the 2026-05-09 ladder
  (tier 2: HR≥70 + edge>0 → CV≤1.15; tier 3: HR≥80 + edge≥5 → CV≤1.50).

## Files changed
- `backend/tests/test_universal_direction_gate.py` (NEW, 19 tests)
- `backend/tests/test_fl_over_overrides.py` (equality + UNDER actual-shape updates)
- `backend/tests/test_nba_under_tuning.py` (removed obsolete 0.15-gap assertions)
- `backend/tests/test_war_zone_refactor.py` (config-drift cleanup)
- `backend/scripts/audit_direction_gate_refactor.py` (NEW — live audit harness)

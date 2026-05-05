"""
NBA push-handling regression tests (CHANGELOG 2026-05-05 NBA push fix).

Locks the contract that `hit_rate_over` and `hit_rate_under` are
calculated INDEPENDENTLY in `NBAScoringAdapter._compute_cv_and_hit_rate`,
not via `100 - active`. The old complement logic silently inflated the
inactive side by the push percentage on whole-number lines (e.g. Julius
Randle AST 4.0 with 40% pushes had `hit_rate_under=65` on the OVER
side instead of the correct 25%).

Strict `>` / `<` semantics are preserved — pushes count as miss for
both OVER and UNDER. The invariant therefore is:
    hit_rate_over + hit_rate_under + push_rate == 100
"""

import pytest
from typing import Optional


class _FakeStats:
    """Stand-in for the cache loader. Provides the two attributes
    `_compute_cv_and_hit_rate` needs (`_get_logs_by_id`,
    `_resolve_family`, `_FAMILY_SPEC`) wired to a fixed log set."""

    def __init__(self, logs):
        self._logs = logs

    def _get_logs_by_id(self, _pid):
        return self._logs

    def _resolve_family(self, stat_type):
        return "ast" if stat_type == "AST" else stat_type.lower()

    @property
    def _FAMILY_SPEC(self):
        return {"ast": ("ast",), "pts": ("pts",), "reb": ("reb",),
                "stl": ("stl",), "blk": ("blk",)}


def _make_adapter_with_logs(logs):
    """Construct an NBAScoringAdapter and pre-populate the in-memory
    log cache used by `_compute_cv_and_hit_rate`."""
    from services.scoring.adapters.nba_scoring import NBAScoringAdapter
    a = NBAScoringAdapter()
    a._logs_by_id = {1: logs}
    return a


def _logs_from_ast(values):
    return [{"date": f"2026-04-{(i+1):02d}", "ast": v} for i, v in enumerate(values)]


def test_push_does_not_inflate_inactive_side_for_over():
    """40% pushes; OVER pick. Old complement bug → under=65; correct=25."""
    # 7 OVER (>4), 5 UNDER (<4), 8 PUSH (==4)
    values = [5,6,7,8,9,10,11,  3,2,1,0,3,  4,4,4,4,4,4,4,4]
    assert len(values) == 20
    a = _make_adapter_with_logs(_logs_from_ast(values))
    out = a._compute_cv_and_hit_rate(1, "AST", 4.0, "OVER", window=20)
    _, _, hit_rate, _, hr_over, hr_under, _, hr_l5, hr_l10, _ = out
    assert hr_over == 35.0    # 7/20
    assert hr_under == 25.0   # 5/20  ← was 65 with the bug
    assert hit_rate == 35.0   # active = OVER
    assert hr_over + hr_under == 60.0   # 100 - push%(40)


def test_push_does_not_inflate_inactive_side_for_under():
    """Same data, UNDER side. Active (UNDER) unchanged; inactive (OVER) corrected."""
    values = [5,6,7,8,9,10,11,  3,2,1,0,3,  4,4,4,4,4,4,4,4]
    a = _make_adapter_with_logs(_logs_from_ast(values))
    out = a._compute_cv_and_hit_rate(1, "AST", 4.0, "UNDER", window=20)
    _, _, hit_rate, _, hr_over, hr_under, _, _, _, _ = out
    assert hr_over == 35.0
    assert hr_under == 25.0
    assert hit_rate == 25.0   # active = UNDER


def test_no_push_invariant_preserved():
    """Half-line: pushes impossible. OVER + UNDER == 100 always."""
    values = [3,3,3,3,3,3,3,3,3,3,  6,6,6,6,6,6,6,6,6,6]   # 10 under, 10 over @ line 4.5
    a = _make_adapter_with_logs(_logs_from_ast(values))
    out = a._compute_cv_and_hit_rate(1, "AST", 4.5, "OVER", window=20)
    _, _, _, _, hr_over, hr_under, _, _, _, _ = out
    assert hr_over == 50.0
    assert hr_under == 50.0
    assert hr_over + hr_under == 100.0


def test_active_side_unchanged_by_patch():
    """Whole-line OVER pick. Active-side hit_rate must equal hit_rate_over
    (the value gates and `hit_rate_l20` consume)."""
    values = [5,5,5,5,5,5,5,5,5,5,  3,3,3,3,4,4,4,4,4,4]   # 10 over, 4 under, 6 push @ line 4
    a = _make_adapter_with_logs(_logs_from_ast(values))
    over_out = a._compute_cv_and_hit_rate(1, "AST", 4.0, "OVER", window=20)
    under_out = a._compute_cv_and_hit_rate(1, "AST", 4.0, "UNDER", window=20)
    _, _, hit_over, _, hr_over_o, hr_under_o, _, _, _, _ = over_out
    _, _, hit_under, _, hr_over_u, hr_under_u, _, _, _, _ = under_out
    assert hit_over == hr_over_o == 50.0
    assert hit_under == hr_under_u == 20.0
    # Independent calculation: same OVER/UNDER pair regardless of side.
    assert hr_over_o == hr_over_u
    assert hr_under_o == hr_under_u


def test_l5_and_l10_remain_active_side():
    """L5 / L10 stay side-aware (no force_side override). They are the
    universal L5 / L10 sub-gate inputs and must reflect the prop's own
    side.

    Logs are date-sorted DESC by `_compute_cv_and_hit_rate`, so to
    guarantee the newest-first window we put the latest values at the
    END of the input list (highest date suffix wins).
    """
    # Oldest 10 all UNDER, middle 5 PUSH, NEWEST 5 all OVER.
    # After DESC sort: newest 5 = [5]*5 → OVER L5, newest 10 = [5]*5+[4]*5 → 5/10 OVER.
    values = [3,3,3,3,3,3,3,3,3,3, 4,4,4,4,4, 5,5,5,5,5]
    a = _make_adapter_with_logs(_logs_from_ast(values))
    over_out = a._compute_cv_and_hit_rate(1, "AST", 4.0, "OVER", window=20)
    under_out = a._compute_cv_and_hit_rate(1, "AST", 4.0, "UNDER", window=20)
    _, _, _, _, _, _, _, hr5_o, hr10_o, _ = over_out
    _, _, _, _, _, _, _, hr5_u, hr10_u, _ = under_out
    # OVER L5: 5/5 over → 100; OVER L10: 5/10 → 50
    assert hr5_o == 100.0
    assert hr10_o == 50.0
    # UNDER L5: 0/5 (all 5 > 4) → 0; UNDER L10: 0/10 (5 over + 5 push) → 0
    assert hr5_u == 0.0
    assert hr10_u == 0.0

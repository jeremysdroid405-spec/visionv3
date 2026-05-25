"""
Pin the "brute-force first, filter later" optimizer architecture
shipped 2026-05-24 per user directive:

  > "the grid should create the absolute 5 best combos for every
  >  tier from brute force. the settings on the grid should only be
  >  used to filter that AFTER the absolute best are displayed"

Backend MUST always brute-force on `DEFAULT_GRID`. The operator's
submitted grid is stored on the run state as `display_filter_grid`
for the UI to apply post-hoc — it MUST NOT narrow the actual search.
"""
from __future__ import annotations
import sys
sys.path.insert(0, "/app/backend")

from routes.emergent_admin.optimizer import (
    DEFAULT_GRID,
    GridSpec,
    _resolve_grid,
    _user_grid_to_display_filter,
)


def test_resolve_grid_always_returns_default_even_when_user_supplies_one():
    """The whole architecture point — user-supplied grid is IGNORED
    at search time. Brute force always."""
    user_spec = GridSpec(
        hr_l20_min=[0.55, 0.65, 0.70, 0.75, 0.80],
        hr_l10_min=[0.55, 0.65, 0.70],
        hr_l5_min=[0.50, 0.60, 0.70],
        cv_max=[0.50, 0.70, 0.90, 1.10],
        edge_min=[0.02, 0.05, 0.08, 0.10],
        tp_min=[0.50, 0.55, 0.60, 0.65],
    )
    resolved = _resolve_grid(user_spec)
    for axis, default_vals in DEFAULT_GRID.items():
        assert resolved[axis] == default_vals, (
            f"Axis {axis!r} must equal DEFAULT_GRID but got {resolved[axis]} "
            f"(user submitted {getattr(user_spec, axis)})")


def test_resolve_grid_returns_default_when_no_spec():
    resolved = _resolve_grid(None)
    assert resolved == {k: list(v) for k, v in DEFAULT_GRID.items()}


def test_default_grid_contains_wildcards_so_unfiltered_combos_exist():
    """DEFAULT_GRID must include wildcard sentinels (-inf for *_min,
    +inf for *_max) so the brute-force search includes "no constraint"
    combos. Otherwise the optimizer can't surface thin-but-real
    combos that pass on a sub-set of axes only."""
    for axis in ("hr_l20_min", "hr_l10_min", "hr_l5_min",
                  "edge_min", "tp_min"):
        assert float("-inf") in DEFAULT_GRID[axis], (
            f"{axis} must include -inf wildcard for unconstrained sweeps")
    assert float("+inf") in DEFAULT_GRID["cv_max"], (
        "cv_max must include +inf wildcard for unconstrained sweeps")


def test_user_grid_captured_as_display_filter():
    """User-supplied grid becomes a passive display filter — backend
    captures it verbatim, UI applies it post-hoc."""
    user_spec = GridSpec(
        hr_l20_min=[0.55, 0.65, 0.70],
        edge_min=[0.05, 0.10],
    )
    filt = _user_grid_to_display_filter(user_spec)
    assert filt["hr_l20_min"] == [0.55, 0.65, 0.70]
    assert filt["edge_min"]   == [0.05, 0.10]
    # Axes the user didn't specify must NOT appear in the filter
    # (otherwise the UI would try to filter on them).
    assert "hr_l10_min" not in filt
    assert "cv_max"     not in filt
    assert "tp_min"     not in filt


def test_user_grid_to_display_filter_handles_none():
    assert _user_grid_to_display_filter(None) == {}


def test_user_grid_to_display_filter_skips_empty_lists():
    """An axis with no values is treated as 'no display filter on this
    axis' — same as None."""
    spec = GridSpec(hr_l20_min=[], hr_l10_min=[0.60])
    filt = _user_grid_to_display_filter(spec)
    assert "hr_l20_min" not in filt
    assert filt["hr_l10_min"] == [0.60]


def test_brute_force_combo_count_lower_bound():
    """Sanity check — DEFAULT_GRID must produce at least 50k
    combinations per cell so we genuinely BRUTE-force every reasonable
    threshold combo, not just a handful."""
    import functools, operator
    n = functools.reduce(operator.mul,
                            (len(v) for v in DEFAULT_GRID.values()), 1)
    assert n >= 50_000, (
        f"DEFAULT_GRID generates only {n} combos per cell — too sparse "
        f"to call brute-force. Expand DEFAULT_GRID coverage.")

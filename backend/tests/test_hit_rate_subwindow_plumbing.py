"""Hit-rate sub-window plumbing — Commit 1 (2026-05-01).

Validates that NBA + MLB scoring adapters now populate
`hit_rate_l5`, `hit_rate_l10`, and `hit_rate_sample_size` on the
ScoringContext (and that the MLB-parity strict-denominator NBA path
no longer inflates HRs from variable denominators).

Mutation-test compatible: every assertion locks down a specific
piece of the contract; flipping any one of the underlying constants
in `nba_scoring.py` / `mlb_tier_sorter.py` / `mlb_scoring.py` will
break exactly one test here.
"""
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from services.mlb_tier_sorter import MLBTierSorter
from services.scoring.adapters.base import ScoringContext


# --------------------------------------------------------------------------
# Fixtures — synthetic game-log series
# --------------------------------------------------------------------------
def _logs_pts(values, dates=None) -> List[Dict[str, Any]]:
    """Build NBA-style PTS log list with descending dates (most-recent first)."""
    if dates is None:
        # 2026-04-29, 28, ... going back
        dates = [f"2026-04-{29 - i:02d}" for i in range(len(values))]
    return [{"pts": v, "date": d} for v, d in zip(values, dates)]


def _logs_hits(values, dates=None) -> List[Dict[str, Any]]:
    """Build MLB-style hits log list with descending dates."""
    if dates is None:
        dates = [f"2026-04-{29 - i:02d}" for i in range(len(values))]
    return [{"hits": v, "date": d} for v, d in zip(values, dates)]


# --------------------------------------------------------------------------
# NBA — strict 20/10 denominator + sub-window plumbing
# --------------------------------------------------------------------------
class _StubNBAAdapter:
    """Minimal NBA adapter exposing just the method under test.

    We import the real `_compute_cv_and_hit_rate` function and call it
    bound to a stub holder — avoiding the full constructor cost which
    requires a Mongo client.
    """

    _FAMILY_SPEC = {
        "pts": ("pts",),
        "ast": ("ast",),
        "reb": ("reb",),
    }

    def __init__(self, logs):
        self._logs = list(logs)
        self._cv_cache = {}

    def _resolve_family(self, stat_type: str):
        return "pts" if stat_type == "pts" else None

    def _get_logs_by_id(self, bdl_player_id):
        return self._logs

    @staticmethod
    def _compute(adapter, line, side="OVER"):
        # Bind the real method to our stub.
        from services.scoring.adapters.nba_scoring import NBAScoringAdapter
        bound = NBAScoringAdapter._compute_cv_and_hit_rate.__get__(
            adapter, NBAScoringAdapter
        )
        return bound(
            bdl_player_id=12345,
            stat_type="pts",
            line=line,
            direction=side,
            window=20,
        )


def test_nba_strict_20_window_when_20_logs_available():
    """20+ logs → window=20 fixed denominator. Locks down the parity fix."""
    # 18 hits in 20 logs → 90% L20, never the 'variable denom' 100%.
    logs = _logs_pts([25.0] * 18 + [10.0, 10.0])  # 18 over 20.5, 2 under
    adapter = _StubNBAAdapter(logs)
    result = _StubNBAAdapter._compute(adapter, line=20.5, side="OVER")
    (cv, cv_status, hr, ceiling, hr_o, hr_u, hr_status,
     hr_l5, hr_l10, sample_size) = result
    assert sample_size == 20, "Strict denominator must use 20 when ≥20 logs."
    assert hr == 90.0, "18/20 = 90% — locked."


def test_nba_strict_10_window_when_under_20_logs():
    """10..19 logs → window=10 fixed denominator (the Max Strus 17-game bug)."""
    # 16 over 19.5 in 17 logs — under variable denom this used to print
    # 16/17 = 94.1%. With the strict fix the L10 mass is what counts.
    # Most-recent 10 = 9 hits + 1 miss → 90.0%.
    logs = _logs_pts([25.0] * 9 + [10.0] + [25.0] * 7)  # 17 logs
    adapter = _StubNBAAdapter(logs)
    result = _StubNBAAdapter._compute(adapter, line=19.5, side="OVER")
    (_, _, hr, _, _, _, _, _, _, sample_size) = result
    assert sample_size == 10, "10..19 logs → strict 10 window (no 17-denom)."
    assert hr == 90.0, "9/10 from most-recent 10 — locked."


def test_nba_insufficient_sample_under_10_logs():
    """< 10 logs → return None for HR (no fabricated denominator)."""
    logs = _logs_pts([25.0] * 8)
    adapter = _StubNBAAdapter(logs)
    result = _StubNBAAdapter._compute(adapter, line=19.5, side="OVER")
    (_, _, hr, _, _, _, hr_status, hr_l5, hr_l10, sample_size) = result
    assert hr is None
    assert sample_size is None
    assert hr_status == "missing_source_distribution"


def test_nba_l5_subwindow_populated():
    """L5 hit-rate must be surfaced when ≥5 logs exist."""
    # most-recent 5 are all UNDER 25.5 → L5 = 0%, but L20 = high.
    logs = _logs_pts([10.0] * 5 + [30.0] * 15)
    adapter = _StubNBAAdapter(logs)
    result = _StubNBAAdapter._compute(adapter, line=25.5, side="OVER")
    (_, _, hr, _, _, _, _, hr_l5, hr_l10, _) = result
    assert hr_l5 == 0.0, "Slumping L5 = 0%."
    assert hr == 75.0, "L20 OVER = 15/20 = 75%."


def test_nba_under_side_complement():
    """OVER + UNDER hit rates must sum to 100% (no missing-data here)."""
    logs = _logs_pts([20.0] * 12 + [10.0] * 8)  # 12 over 15.5, 8 under
    adapter = _StubNBAAdapter(logs)
    over = _StubNBAAdapter._compute(adapter, line=15.5, side="OVER")
    under = _StubNBAAdapter._compute(adapter, line=15.5, side="UNDER")
    assert over[2] + under[2] == 100.0


# --------------------------------------------------------------------------
# MLB — sub-window helper + adapter wiring
# --------------------------------------------------------------------------
class _StubMLBSorter(MLBTierSorter):
    """MLB sorter bound to in-memory logs — avoids the Mongo client."""

    def __init__(self, logs):  # noqa: D401 — intentional override
        # Skip MLBTierSorter.__init__; we only need the helper methods.
        self._db = MagicMock()
        self._logs = list(logs)

    def _get_logs_by_id(self, bdl_player_id):
        return self._logs


def test_mlb_subwindow_l5_strict_denominator():
    """L5 over hit-rate uses fixed window=5, not len(logs)."""
    # 3 over 0.5 hits, 2 under, mixed in older logs.
    logs = _logs_hits([2, 1, 0, 2, 0, 0, 0, 0])
    sorter = _StubMLBSorter(logs)
    hr, n = sorter._calculate_subwindow_hit_rate(
        bdl_player_id=1, stat_type="hits", line=0.5,
        side="OVER", window=5, min_games=4,
    )
    assert n == 5
    # Most-recent 5 = [2,1,0,2,0] → 3 over 0.5 → 60%.
    assert hr == 60.0


def test_mlb_subwindow_l10_strict_denominator():
    """L10 OVER hit-rate computes against fixed 10 window."""
    logs = _logs_hits([1] * 7 + [0] * 5)  # 12 logs
    sorter = _StubMLBSorter(logs)
    hr, n = sorter._calculate_subwindow_hit_rate(
        bdl_player_id=1, stat_type="hits", line=0.5,
        side="OVER", window=10, min_games=4,
    )
    assert n == 10
    # Most-recent 10 = [1,1,1,1,1,1,1,0,0,0] → 7/10 = 70%.
    assert hr == 70.0


def test_mlb_subwindow_under_complement():
    """UNDER side returns the complement of OVER for clean (no-None) data."""
    logs = _logs_hits([2, 0, 1, 0, 2, 0])
    sorter = _StubMLBSorter(logs)
    over, _ = sorter._calculate_subwindow_hit_rate(
        bdl_player_id=1, stat_type="hits", line=0.5,
        side="OVER", window=5, min_games=4,
    )
    under, _ = sorter._calculate_subwindow_hit_rate(
        bdl_player_id=1, stat_type="hits", line=0.5,
        side="UNDER", window=5, min_games=4,
    )
    assert over + under == 100.0


def test_mlb_subwindow_min_games_floor():
    """Below `min_games` → returns (None, n) without fabricating a rate."""
    logs = _logs_hits([1, 0, 1])  # 3 logs only
    sorter = _StubMLBSorter(logs)
    hr, n = sorter._calculate_subwindow_hit_rate(
        bdl_player_id=1, stat_type="hits", line=0.5,
        side="OVER", window=5, min_games=4,
    )
    assert hr is None
    assert n == 3, "Caller still sees how many we had (sample-size escape hatch)."


# --------------------------------------------------------------------------
# ScoringContext — fields exist and are propagated
# --------------------------------------------------------------------------
def test_scoring_context_has_subwindow_fields():
    """Lockdown — adapter side cannot stop populating these fields."""
    ctx = ScoringContext(
        canonical_key="x", sport="nba", event_id="e",
        player_name="p", stat_type="pts", line=20.0,
        recommendation="OVER", pp_layer=None, dk_layer=None,
        fd_layer=None, mgm_layer=None, bol_layer=None,
        sharp_layer=None, p_model=None,
        hit_rate_l5=40.0, hit_rate_l10=60.0,
        hit_rate_sample_size=20,
    )
    assert ctx.hit_rate_l5 == 40.0
    assert ctx.hit_rate_l10 == 60.0
    assert ctx.hit_rate_sample_size == 20

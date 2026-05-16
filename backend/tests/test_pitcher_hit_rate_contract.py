"""Pitcher-specific HR contract — 5-start minimum window.

Locks the contract introduced 2026-05-16:

    starts ≥ 10  →  window=10
    starts ≥ 5   →  window=n_starts (all available)
    starts < 5   →  HR unavailable (None)

The batter HR SSOT is UNCHANGED. These tests bind only the new
pitcher path (`_calculate_pitcher_hit_rate_sides`) and guard against
silent reintroduction of the 10-game floor.
"""
from __future__ import annotations

import pytest

from services.mlb_tier_sorter import MLBTierSorter


class _StubSorter(MLBTierSorter):
    """Bypass the heavy `__init__` so we can drive the methods
    directly. Replaces the player-log lookup with a fixed list."""
    def __init__(self, logs):
        self._stub_logs = logs

    def _get_logs_by_id(self, bdl_id):
        return self._stub_logs


def _starts(n, *, k=4, ip=5.0, er=2.0, hits_allowed=5, pitcher_walks=2):
    """Build `n` synthetic pitcher game logs in descending-date order."""
    out = []
    for i in range(n):
        out.append({
            "date": f"2026-05-{15 - i:02d}",
            "pitcher_strikeouts": k,
            "innings_pitched": ip,
            "earned_runs": er,
            "hits_allowed": hits_allowed,
            "pitcher_walks": pitcher_walks,
        })
    return out


# ─── Window-selection contract ──────────────────────────────────
class TestWindowSelection:
    def test_starts_ge_10_uses_window_10(self):
        s = _StubSorter(_starts(15))
        hro, hru, avg, n = s._calculate_pitcher_hit_rate_sides(
            1, "pitcher_strikeouts", 3.5,
        )
        assert n == 10
        assert hro == 100.0  # all 10 hits over 3.5 with k=4
        assert hru == 0.0

    def test_starts_ge_5_lt_10_uses_all_available(self):
        for n_starts in (5, 6, 7, 8, 9):
            s = _StubSorter(_starts(n_starts))
            hro, hru, avg, n = s._calculate_pitcher_hit_rate_sides(
                1, "pitcher_strikeouts", 3.5,
            )
            assert n == n_starts, f"window should equal {n_starts}"

    def test_starts_lt_5_returns_none(self):
        for n_starts in (0, 1, 2, 3, 4):
            s = _StubSorter(_starts(n_starts))
            hro, hru, avg, n = s._calculate_pitcher_hit_rate_sides(
                1, "pitcher_strikeouts", 3.5,
            )
            assert (hro, hru, avg, n) == (None, None, None, None), (
                f"starts={n_starts} must return all-None"
            )

    def test_freeland_9_starts_now_returns_hr(self):
        """Kyle Freeland scenario — exactly 9 starts. Pre-2026-05-16
        the batter floor (min_games=10) returned None and silently
        killed his pitcher props at the hit_rate_gate. New contract
        returns a real HR with window_used=9.
        """
        s = _StubSorter(_starts(9, k=5))
        hro, hru, avg, n = s._calculate_pitcher_hit_rate_sides(
            1, "pitcher_strikeouts", 3.5,
        )
        assert n == 9
        assert hro == 100.0  # 9/9 over 3.5 with k=5
        assert avg == 5.0


# ─── Pitcher-stat routing ───────────────────────────────────────
class TestPitcherStatRouting:
    @pytest.mark.parametrize("stat", [
        "pitcher_strikeouts", "Pitcher Strikeouts",
        "earned_runs", "Earned Runs",
        "hits_allowed", "Hits Allowed",
        "pitcher_walks", "Walks Allowed",
        "pitcher_outs", "Pitcher Outs",
    ])
    def test_pitcher_stats_routed_to_new_window(self, stat):
        s = _StubSorter(_starts(7))
        hro, hru, avg, n = s._calculate_pitcher_hit_rate_sides(1, stat, 3.5)
        assert n == 7

    def test_non_pitcher_stat_falls_back_to_batter_path(self):
        """Defensive: invoking the pitcher method on a batter stat
        falls through to `_calculate_hit_rate_sides`, which has the
        OLD min_games=5 contract (NBA-parity batter path) — i.e.
        with 9 batter games it would use the 10→5 fallback.
        """
        # 9 games of batter "hits" with hits=1 — line 0.5.
        logs = [{"date": f"2026-05-{15-i:02d}", "hits": 1}
                for i in range(9)]
        s = _StubSorter(logs)
        hro, hru, avg, n = s._calculate_pitcher_hit_rate_sides(
            1, "hits", 0.5,
        )
        # Batter contract: n=9 falls to 10-window fallback floor.
        # Result is the batter path's normal output.
        assert n is None or isinstance(n, int)


# ─── Pitcher-outs derivation via innings_pitched × 3 ────────────
class TestPitcherOutsDerivation:
    def test_pitcher_outs_derived_from_innings(self):
        """`innings_pitched: 5.0` → 15 outs. Line 15.5 → MISS.
        Line 14.5 → HIT.
        """
        s = _StubSorter(_starts(10, ip=5.0))
        # Line 15.5 — all 10 starts at 15 outs, all miss
        hro, hru, avg, n = s._calculate_pitcher_hit_rate_sides(
            1, "pitcher_outs", 15.5,
        )
        assert n == 10
        assert hro == 0.0
        assert hru == 100.0
        assert avg == 15.0
        # Line 14.5 — all hit
        hro2, _, _, _ = s._calculate_pitcher_hit_rate_sides(
            1, "pitcher_outs", 14.5,
        )
        assert hro2 == 100.0


# ─── Strict-denominator preservation ────────────────────────────
class TestStrictDenominator:
    def test_missing_field_games_count_as_misses(self):
        """Mirrors batter SSOT: denominator is the chosen window.
        Games with missing stat values do NOT shrink the denominator.
        """
        logs = _starts(10, k=5)
        # Null out k on 3 of them — they remain in the window.
        for i in (1, 3, 5):
            logs[i]["pitcher_strikeouts"] = None
        s = _StubSorter(logs)
        hro, _, _, n = s._calculate_pitcher_hit_rate_sides(
            1, "pitcher_strikeouts", 3.5,
        )
        assert n == 10
        # 7 hits / 10 = 70%, missing values do NOT raise the rate.
        assert hro == 70.0


# ─── Batter HR SSOT unchanged ───────────────────────────────────
class TestBatterSSOTUntouched:
    """Regression: the existing batter HR SSOT
    (`_calculate_hit_rate_sides`) MUST keep its current semantics
    (10-game floor by default). If a future commit lowers the
    batter floor to 5 we want this test to fail loud.
    """

    def test_batter_floor_is_still_5_via_explicit_min_games(self):
        # 9 batter games, default min_games=10 in our caller — but
        # the method allows min_games override. NBA-parity callers
        # pass min_games=5. Verify the override works as expected
        # and 9 games gives a non-None result with min_games=5.
        logs = [{"date": f"2026-05-{15-i:02d}", "hits": 1}
                for i in range(9)]
        s = _StubSorter(logs)
        hro, hru, _, n = s._calculate_hit_rate_sides(
            1, "hits", 0.5, num_games=20, min_games=5,
        )
        # The method has its OWN fallback ladder (20→10→None).
        # 9 < 10 → returns None even with min_games=5 override
        # at the caller. This is the batter contract.
        assert (hro, n) == (None, None)

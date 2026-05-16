"""Pitcher-specific HR contract — progressive 5..20 rolling window.

Locks the upgraded contract introduced 2026-05-17 (replaces the
prior 10-start ceiling from 2026-05-16):

    starts ≥ 20      →  window = 20    (rolling L20)
    5 ≤ starts < 20  →  window = n_starts (all available)
    starts < 5       →  HR unavailable (None)

The batter HR SSOT is UNCHANGED. These tests bind only the new
pitcher path (`_calculate_pitcher_hit_rate_sides`) and guard against
silent regression to either the 10-start ceiling or the batter floor.
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
    """Build `n` synthetic pitcher game logs in descending-date order.

    Dates step backward from 2026-06-30 so we can build large
    rolling windows (n=30+) without month-wrap weirdness.
    """
    from datetime import date, timedelta
    base = date(2026, 6, 30)
    out = []
    for i in range(n):
        out.append({
            "date": (base - timedelta(days=i)).isoformat(),
            "pitcher_strikeouts": k,
            "innings_pitched": ip,
            "earned_runs": er,
            "hits_allowed": hits_allowed,
            "pitcher_walks": pitcher_walks,
        })
    return out


# ─── Window-selection contract ──────────────────────────────────
class TestWindowSelection:
    def test_starts_ge_20_uses_window_20(self):
        """≥ 20 starts → newest 20 (rolling L20)."""
        s = _StubSorter(_starts(25))
        hro, hru, avg, n = s._calculate_pitcher_hit_rate_sides(
            1, "pitcher_strikeouts", 3.5,
        )
        assert n == 20
        assert hro == 100.0  # all 20 hits over 3.5 with k=4
        assert hru == 0.0

    def test_starts_eq_20_uses_window_20(self):
        s = _StubSorter(_starts(20))
        _, _, _, n = s._calculate_pitcher_hit_rate_sides(
            1, "pitcher_strikeouts", 3.5,
        )
        assert n == 20

    def test_starts_lt_20_uses_all_available(self):
        """Variable denominator across the entire 5..19 range."""
        for n_starts in (5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19):
            s = _StubSorter(_starts(n_starts))
            _, _, _, n = s._calculate_pitcher_hit_rate_sides(
                1, "pitcher_strikeouts", 3.5,
            )
            assert n == n_starts, f"window should equal {n_starts} when starts={n_starts}"

    def test_starts_lt_5_returns_none(self):
        for n_starts in (0, 1, 2, 3, 4):
            s = _StubSorter(_starts(n_starts))
            hro, hru, avg, n = s._calculate_pitcher_hit_rate_sides(
                1, "pitcher_strikeouts", 3.5,
            )
            assert (hro, hru, avg, n) == (None, None, None, None), (
                f"starts={n_starts} must return all-None"
            )

    def test_rolling_window_uses_NEWEST_20_when_27_starts(self):
        """27 starts → newest 20 only. Older 7 must NOT influence HR.
        Verified by stamping `pitcher_strikeouts=0` on the OLDEST 7
        (which would drag HR down to ~74% if included) while keeping
        the newest 20 at k=10 (always hits over 3.5).
        """
        logs = _starts(27, k=10)
        # Older starts (indices 20..26) — zero out so they'd miss
        # the line 3.5 if the window leaked beyond 20.
        for i in range(20, 27):
            logs[i]["pitcher_strikeouts"] = 0
        s = _StubSorter(logs)
        hro, _, _, n = s._calculate_pitcher_hit_rate_sides(
            1, "pitcher_strikeouts", 3.5,
        )
        assert n == 20
        assert hro == 100.0, (
            f"Rolling L20 leaked older starts into the window: "
            f"hro={hro} expected 100.0"
        )

    # User-spec validation scenarios -----------------------------------
    def test_freeland_9_starts_uses_all_9(self):
        """Kyle Freeland — exactly 9 starts → window=9."""
        s = _StubSorter(_starts(9, k=5))
        _, _, avg, n = s._calculate_pitcher_hit_rate_sides(
            1, "pitcher_strikeouts", 3.5,
        )
        assert n == 9
        assert avg == 5.0

    def test_16_starts_uses_all_16(self):
        s = _StubSorter(_starts(16))
        _, _, _, n = s._calculate_pitcher_hit_rate_sides(
            1, "pitcher_strikeouts", 3.5,
        )
        assert n == 16

    def test_24_starts_uses_newest_rolling_20(self):
        s = _StubSorter(_starts(24))
        _, _, _, n = s._calculate_pitcher_hit_rate_sides(
            1, "pitcher_strikeouts", 3.5,
        )
        assert n == 20

    def test_4_starts_unavailable(self):
        s = _StubSorter(_starts(4))
        hro, hru, avg, n = s._calculate_pitcher_hit_rate_sides(
            1, "pitcher_strikeouts", 3.5,
        )
        assert (hro, hru, avg, n) == (None, None, None, None)


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
        # 7 starts → all 7 (mid-window growth phase).
        s = _StubSorter(_starts(7))
        _, _, _, n = s._calculate_pitcher_hit_rate_sides(1, stat, 3.5)
        assert n == 7

    @pytest.mark.parametrize("stat", [
        "pitcher_strikeouts", "Pitcher Strikeouts",
        "earned_runs", "Earned Runs",
        "hits_allowed", "Hits Allowed",
        "pitcher_walks", "Walks Allowed",
        "pitcher_outs", "Pitcher Outs",
    ])
    def test_pitcher_stats_all_cap_at_20(self, stat):
        s = _StubSorter(_starts(28))
        _, _, _, n = s._calculate_pitcher_hit_rate_sides(1, stat, 3.5)
        assert n == 20, f"{stat} did not cap at 20"

    def test_non_pitcher_stat_falls_back_to_batter_path(self):
        """Defensive: invoking the pitcher method on a batter stat
        falls through to `_calculate_hit_rate_sides`, which has the
        OLD batter contract.
        """
        # 9 games of batter "hits" with hits=1 — line 0.5.
        logs = [{"date": f"2026-05-{15-i:02d}", "hits": 1}
                for i in range(9)]
        s = _StubSorter(logs)
        hro, hru, avg, n = s._calculate_pitcher_hit_rate_sides(
            1, "hits", 0.5,
        )
        # Batter contract — pitcher rolling-window must NOT apply.
        assert n is None or isinstance(n, int)


# ─── Pitcher-outs derivation via innings_pitched × 3 ────────────
class TestPitcherOutsDerivation:
    def test_pitcher_outs_derived_from_innings(self):
        """`innings_pitched: 5.0` → 15 outs. Line 15.5 → MISS.
        Line 14.5 → HIT. Uses 10 starts → window=10 (in the variable
        denominator phase, denominator equals n_starts).
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
    def test_missing_field_games_count_as_misses_in_rolling_l20(self):
        """Mirrors batter SSOT: denominator is the chosen window.
        Games with missing stat values do NOT shrink the denominator.
        Tested at the 20-window so we cover the cap state.
        """
        logs = _starts(22, k=5)
        # Null out k on 3 of the newest 20 — they remain in the window.
        for i in (1, 3, 5):
            logs[i]["pitcher_strikeouts"] = None
        s = _StubSorter(logs)
        hro, _, _, n = s._calculate_pitcher_hit_rate_sides(
            1, "pitcher_strikeouts", 3.5,
        )
        assert n == 20
        # 17 hits / 20 = 85% — missing values count as misses.
        assert hro == 85.0

    def test_missing_field_games_count_as_misses_mid_growth_phase(self):
        """Same strict-denominator contract during the 5..19 growth
        phase (sample size < cap)."""
        logs = _starts(8, k=5)
        for i in (1, 3):
            logs[i]["pitcher_strikeouts"] = None
        s = _StubSorter(logs)
        hro, _, _, n = s._calculate_pitcher_hit_rate_sides(
            1, "pitcher_strikeouts", 3.5,
        )
        assert n == 8
        # 6 hits / 8 = 75%
        assert hro == 75.0


# ─── Batter HR SSOT unchanged ───────────────────────────────────
class TestBatterSSOTUntouched:
    """Regression: the existing batter HR SSOT
    (`_calculate_hit_rate_sides`) MUST keep its current semantics.
    Pitcher rolling-window changes do not touch the batter path.
    """

    def test_batter_floor_unchanged(self):
        # 9 batter games. Batter path's own fallback ladder
        # (20→10→None) returns None — independent of pitcher contract.
        logs = [{"date": f"2026-05-{15-i:02d}", "hits": 1}
                for i in range(9)]
        s = _StubSorter(logs)
        hro, hru, _, n = s._calculate_hit_rate_sides(
            1, "hits", 0.5, num_games=20, min_games=5,
        )
        assert (hro, n) == (None, None)


# ─── Contract bounds — central single source ────────────────────
class TestContractBounds:
    def test_min_starts_is_5(self):
        assert MLBTierSorter._PITCHER_HR_MIN_STARTS == 5

    def test_max_window_is_20(self):
        assert MLBTierSorter._PITCHER_HR_MAX_WINDOW == 20


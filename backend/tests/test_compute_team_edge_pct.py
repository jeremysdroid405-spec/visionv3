"""
Unit tests for `compute_team_edge_pct` — SSOT edge math.

User reported 2026-06-03:
  "we shouldn't be recommending an UNDER line when the projection is
   over. it looks like all of the math and logic is off on teams"

The naive `(projection - line) / line` formula produced:
  - garbage for negative spread lines (e.g. -201.8% on a +0.1 cover)
  - same value for OVER and UNDER (no direction sign), so picks were
    recommended on the side opposing the projection

`compute_team_edge_pct` is the SSOT helper. Tests below pin every
sport / category / side combination.
"""
import pytest
from services.team_prop_tier_service import compute_team_edge_pct


# ── Total markets — OVER/UNDER on a positive line ────────────────────
def test_team_total_over_agrees_with_high_projection():
    """TEAM_TOTAL OVER 110.5 + proj 115 → edge positive (proj > line)."""
    e = compute_team_edge_pct(115.0, 110.5, "OVER", "team_total")
    assert e is not None and e > 0


def test_team_total_over_disagrees_with_low_projection():
    """TEAM_TOTAL OVER 110.5 + proj 107.9 → edge negative."""
    e = compute_team_edge_pct(107.9, 110.5, "OVER", "team_total")
    assert e is not None and e < 0


def test_team_total_under_agrees_with_low_projection():
    """TEAM_TOTAL UNDER 110.5 + proj 107.9 → edge positive (UNDER wins)."""
    e = compute_team_edge_pct(107.9, 110.5, "UNDER", "team_total")
    assert e is not None and e > 0


def test_team_total_under_disagrees_with_high_projection():
    """TEAM_TOTAL UNDER 110.5 + proj 115 → edge negative."""
    e = compute_team_edge_pct(115.0, 110.5, "UNDER", "team_total")
    assert e is not None and e < 0


def test_game_total_symmetric():
    """OVER and UNDER must have equal-magnitude opposite signs."""
    over  = compute_team_edge_pct(210.0, 215.5, "OVER",  "game_total")
    under = compute_team_edge_pct(210.0, 215.5, "UNDER", "game_total")
    assert over is not None and under is not None
    assert abs(over + under) < 0.01


# ── Spread markets — signed line, side encodes which team's row ──────
def test_spread_home_favorite_covers_when_margin_exceeds_threshold():
    """HOME -5.5 + margin proj 5.6 → barely covers → small positive edge."""
    e = compute_team_edge_pct(5.6, -5.5, "HOME", "spread")
    assert e is not None and e > 0


def test_spread_home_favorite_fails_when_margin_short():
    """HOME -5.5 + margin proj 3.0 → fails to cover → negative edge."""
    e = compute_team_edge_pct(3.0, -5.5, "HOME", "spread")
    assert e is not None and e < 0


def test_spread_away_underdog_covers_when_keeps_it_close():
    """AWAY +5.5 + margin proj -1.7 → DAL loses by 1.7, covers +5.5 → positive."""
    e = compute_team_edge_pct(-1.7, 5.5, "AWAY", "spread")
    assert e is not None and e > 0


def test_spread_away_underdog_blowout_loses_cover():
    """AWAY +5.5 + margin proj -10 → DAL loses by 10, fails +5.5 → negative."""
    e = compute_team_edge_pct(-10.0, 5.5, "AWAY", "spread")
    assert e is not None and e < 0


# ── Edge cases ────────────────────────────────────────────────────────
def test_h2h_returns_none():
    """Moneyline has no line → edge undefined."""
    assert compute_team_edge_pct(0.7, None, "ML", "h2h") is None


def test_none_inputs_return_none():
    assert compute_team_edge_pct(None, 110.5, "OVER", "team_total") is None
    assert compute_team_edge_pct(110.0, None, "OVER", "team_total") is None


def test_zero_line_returns_none_for_totals():
    """A 0 total line is degenerate."""
    assert compute_team_edge_pct(100.0, 0.0, "OVER", "team_total") is None


def test_edge_magnitude_grows_with_distance_from_threshold():
    """Larger projection deviation → larger edge magnitude."""
    near  = compute_team_edge_pct(111.0, 110.5, "OVER", "team_total")
    far   = compute_team_edge_pct(120.0, 110.5, "OVER", "team_total")
    assert near is not None and far is not None
    assert far > near > 0


def test_no_value_above_minus_two_hundred_percent():
    """Sanity floor — the prior bug produced -201.8% on tiny deltas.
    Modern formula on the same inputs should be within ±10%."""
    e = compute_team_edge_pct(5.6, -5.5, "OVER", "spread")
    assert e is not None
    assert -10.0 <= e <= 10.0, (
        f"edge magnitude {e:.1f}% suggests the broken "
        f"`(projection - line)/line` formula came back"
    )

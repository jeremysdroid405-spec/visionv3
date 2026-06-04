"""
Regression test — Model-vs-Projection Consistency Gate.

User report 2026-06-03:
  "why are we recommending UNDER 3.5 with a projection of 3.8?
   that's contradictory"

The XGB model occasionally disagrees with the team's recent l10
average (e.g. model says UNDER 110.5 wins 77%, but team's actual
l10 average is 119.9). When that disagreement is STRONG, the model's
recommendation is unreliable — the team's actual recent form is the
stronger signal. We demote those picks.

Rule pinned here:
  edge_pct_signed (projection-derived) < −5%  →  tier=None
"""
import pytest

from services.team_prop_tier_service import compute_team_edge_pct


def test_projection_disagreement_caught_for_under():
    """UNDER 110.5 + l10_avg 119.9 → edge_pct ≈ −8.5 (strongly
    disagrees). This is the case that prompted the gate."""
    edge = compute_team_edge_pct(119.9, 110.5, "UNDER", "team_total")
    assert edge is not None
    assert edge < -5.0, (
        f"projection l10=119.9 vs UNDER 110.5 should produce strong "
        f"negative edge — got {edge}"
    )


def test_projection_disagreement_caught_for_over():
    """OVER 110.5 + l10_avg 105.0 → edge_pct ≈ −5.0 (disagrees)."""
    edge = compute_team_edge_pct(105.0, 110.5, "OVER", "team_total")
    assert edge is not None
    # 105.0 vs line 110.5 → −5.0% exactly on the threshold.
    assert edge <= -5.0


def test_projection_agreement_passes():
    """UNDER 110.5 + l10_avg 105.0 → edge_pct ≈ +5.0 (agrees)
    → gate does NOT trip."""
    edge = compute_team_edge_pct(105.0, 110.5, "UNDER", "team_total")
    assert edge is not None
    assert edge >= 5.0, (
        f"projection l10=105.0 vs UNDER 110.5 should agree strongly — "
        f"got {edge}"
    )


def test_h2h_bypass():
    """H2H / moneyline returns None → consistency gate cannot fire."""
    edge = compute_team_edge_pct(0.85, None, "ML", "h2h")
    assert edge is None


def test_spread_disagreement_caught():
    """SPREAD HOME -5.5 + margin l10 +2.0 → fails to cover, negative
    edge → gate trips."""
    edge = compute_team_edge_pct(2.0, -5.5, "HOME", "spread")
    assert edge is not None
    # margin 2.0 vs threshold 5.5 → delta −3.5 / 5.5 → −63.6%.
    assert edge < -5.0


def test_spread_agreement_passes():
    """SPREAD AWAY +5.5 + margin l10 −1.0 → barely covers → positive
    edge."""
    edge = compute_team_edge_pct(-1.0, 5.5, "AWAY", "spread")
    assert edge is not None
    assert edge > 0

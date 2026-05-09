"""Universal injury redistribution model regression tests.

Locks down the 2026-05-08 redistribution rewrite (Layer 1: minutes,
Layer 2: usage with elasticity). The previous flat per-rank constants
(+15 mins / +12% usage) caused the unrealistic outputs:

    - Wembanyama +12% on David Jones OUT
    - SGA      +12% on Jalen Williams OUT
    - LeBron   +15 minutes on Luka OUT

These tests assert the new model produces basketball-realistic
deltas in every case.
"""
from __future__ import annotations

import pytest

from services.injury_vacuum_service import (
    InjuryVacuumService,
    MAX_INDIVIDUAL_MPG,
    MAX_INDIVIDUAL_USAGE,
)


def _redis(injured, teammates):
    return InjuryVacuumService._compute_redistribution(injured, teammates)


# ---------------------------------------------------------------------------
# 1. Low-rotation player OUT → stars get tiny impact, bench takes minutes
# ---------------------------------------------------------------------------

def test_low_rotation_injury_minimal_alpha_impact():
    """David Jones (SAS, 16% usage, 16 MPG) OUT → Wembanyama gets a
    tiny delta; bench/rotation gets the bulk."""
    injured = {"player_name": "David Jones", "minutes_per_game": 16.0, "usage_percentage": 16.0}
    teammates = [
        {"player_name": "Wembanyama",   "minutes_per_game": 33.0, "usage_percentage": 30.0},
        {"player_name": "Devin Vassell", "minutes_per_game": 30.0, "usage_percentage": 22.0},
        {"player_name": "Stephon Castle", "minutes_per_game": 22.0, "usage_percentage": 19.0},
    ]
    out = _redis(injured, teammates)
    wemby = out[0]
    castle = out[2]

    assert wemby["minutes_delta"] <= 2.5, f"Wemby should get <=2.5 mpg; got {wemby}"
    assert wemby["usage_delta"] <= 0.6,  f"Wemby usage delta tiny; got {wemby}"
    # Rotation player should absorb more raw minutes than the alpha.
    assert castle["minutes_delta"] >= wemby["minutes_delta"]


# ---------------------------------------------------------------------------
# 2. Secondary star OUT → alpha dampened, others gain more marginal usage
# ---------------------------------------------------------------------------

def test_secondary_star_out_alpha_dampened():
    """Jalen Williams (OKC secondary, 25% usage, 33 MPG) OUT → SGA
    (alpha, 37% usage, 35 MPG) absorbs little; Holmgren / bench gain more."""
    injured = {"player_name": "Jalen Williams", "minutes_per_game": 33.0, "usage_percentage": 25.0}
    teammates = [
        {"player_name": "Shai Gilgeous-Alexander", "minutes_per_game": 35.0, "usage_percentage": 37.0},
        {"player_name": "Chet Holmgren",            "minutes_per_game": 31.0, "usage_percentage": 22.0},
        {"player_name": "Cason Wallace",            "minutes_per_game": 24.0, "usage_percentage": 16.0},
    ]
    out = _redis(injured, teammates)
    sga, chet, cason = out[0], out[1], out[2]

    assert sga["projected_minutes"] <= MAX_INDIVIDUAL_MPG
    assert sga["projected_usage"]   <= MAX_INDIVIDUAL_USAGE
    assert sga["usage_delta"]   <= 2.0, f"SGA usage delta should be small; got {sga}"
    assert sga["minutes_delta"] <= 3.0, f"SGA mpg delta should be small; got {sga}"
    assert chet["usage_delta"]  >= sga["usage_delta"], (
        "Holmgren marginal usage should exceed SGA's"
    )
    assert cason["minutes_delta"] >= sga["minutes_delta"], (
        "Cason should absorb more raw minutes than SGA"
    )


# ---------------------------------------------------------------------------
# 3. Minutes ceiling: no projected_minutes > 40
# ---------------------------------------------------------------------------

def test_minutes_ceiling_universal():
    """Luka Doncic OUT → LeBron (35 MPG) cannot project past 40."""
    injured = {"player_name": "Luka Doncic", "minutes_per_game": 36.0, "usage_percentage": 36.8}
    teammates = [
        {"player_name": "LeBron James",       "minutes_per_game": 35.0, "usage_percentage": 28.0},
        {"player_name": "Austin Reaves",      "minutes_per_game": 31.0, "usage_percentage": 22.0},
        {"player_name": "Rui Hachimura",      "minutes_per_game": 24.0, "usage_percentage": 16.0},
    ]
    out = _redis(injured, teammates)
    lbj = out[0]

    assert lbj["projected_minutes"] <= MAX_INDIVIDUAL_MPG, (
        f"LeBron projected MPG breaks ceiling: {lbj}"
    )
    assert lbj["minutes_delta"] <= 5.0, f"LeBron should not gain >5 mpg; got {lbj}"
    # Headroom-cap rule: no individual absorbs >45% of injured's mpg.
    for r in out:
        assert r["minutes_delta"] <= injured["minutes_per_game"] * 0.45 + 0.05


# ---------------------------------------------------------------------------
# 4. Usage elasticity: saturated alpha < open-canvas low-usage absorbs more
# ---------------------------------------------------------------------------

def test_usage_elasticity_dampens_saturated_alpha():
    injured = {"player_name": "Inj", "minutes_per_game": 33.0, "usage_percentage": 28.0}
    teammates = [
        {"player_name": "Saturated Alpha", "minutes_per_game": 35.0, "usage_percentage": 35.0},
        {"player_name": "Mid Rotation",     "minutes_per_game": 24.0, "usage_percentage": 16.0},
    ]
    out = _redis(injured, teammates)
    alpha, mid = out[0], out[1]

    assert alpha["projected_usage"] <= MAX_INDIVIDUAL_USAGE
    assert alpha["elasticity_factor"] < mid["elasticity_factor"], (
        "Saturated alpha must have lower elasticity than mid-rotation player"
    )
    # Per-share absorption: lower-usage absorbs more usage per share.
    if alpha["redistribution_share"] > 0 and mid["redistribution_share"] > 0:
        alpha_per_share = alpha["usage_delta"] / alpha["redistribution_share"]
        mid_per_share = mid["usage_delta"] / mid["redistribution_share"]
        assert mid_per_share > alpha_per_share, (
            f"low-usage marginal usage gain {mid_per_share:.2f} should exceed "
            f"alpha's {alpha_per_share:.2f}"
        )


# ---------------------------------------------------------------------------
# 5. No noise flood: card count stays sane (helper returns same length)
# ---------------------------------------------------------------------------

def test_no_noise_flood_helper_aligned_with_input():
    injured = {"player_name": "Inj", "minutes_per_game": 30.0, "usage_percentage": 24.0}
    teammates = [
        {"player_name": f"P{i}", "minutes_per_game": 28.0 - i,
         "usage_percentage": 22.0 - i}
        for i in range(8)
    ]
    out = _redis(injured, teammates)
    assert len(out) == len(teammates)
    # Sum of redistribution_share never exceeds 1.0
    total_share = sum(r["redistribution_share"] for r in out)
    assert 0.99 <= total_share <= 1.001, f"shares must sum to ~1; got {total_share}"
    # No ceiling violations anywhere.
    for r in out:
        assert r["projected_minutes"] <= MAX_INDIVIDUAL_MPG + 0.05
        assert r["projected_usage"]   <= MAX_INDIVIDUAL_USAGE + 0.05
        assert r["minutes_delta"] >= 0
        assert r["usage_delta"]   >= 0


# ---------------------------------------------------------------------------
# 6. Cross-sport safety: zero-minutes/usage injured profile yields empty deltas
# ---------------------------------------------------------------------------

def test_zero_minutes_injured_yields_zero_deltas():
    """Sports without canonical mpg/usage fields → helper returns
    zero-delta scaffolds rather than spurious boosts."""
    injured = {"player_name": "MLB Player"}  # no mpg/usage
    teammates = [
        {"player_name": "T1", "minutes_per_game": 0, "usage_percentage": 0},
        {"player_name": "T2", "minutes_per_game": 0, "usage_percentage": 0},
    ]
    out = _redis(injured, teammates)
    for r in out:
        assert r["minutes_delta"] == 0
        assert r["usage_delta"] == 0

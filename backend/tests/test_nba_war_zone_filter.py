"""Unit tests for NBA War Zone Final Hybrid Filter (spec 2026-04-24)."""
from __future__ import annotations

import pytest

from services.scoring.gates.war_zone import (
    CV_CAPS, evaluate_war_zone, resolve_cv_cap,
)


# ---------------- Step 1 — CV caps ---------------------------------------

def test_cv_cap_lookup_for_standard_stats():
    assert resolve_cv_cap("pts") == 0.45
    assert resolve_cv_cap("pra") == 0.45
    assert resolve_cv_cap("reb") == 0.55
    assert resolve_cv_cap("ast") == 0.55
    assert resolve_cv_cap("3pm") == 0.75


def test_cv_cap_lookup_for_alt_combo_markets():
    assert resolve_cv_cap("pts_ast") == 0.45
    assert resolve_cv_cap("pts_reb") == 0.45
    assert resolve_cv_cap("reb_ast") == 0.55
    assert resolve_cv_cap("pts_reb_ast") == 0.45
    # Raw market-key aliases
    assert resolve_cv_cap("player_points_assists_alternate") == 0.45
    assert resolve_cv_cap("player_rebounds_assists_alternate") == 0.55


def test_cv_cap_unknown_family_returns_none_and_rejects():
    assert resolve_cv_cap("player_first_basket") is None
    decision = evaluate_war_zone(
        stat_family="player_first_basket", hr=70, vs=92, cv=0.3,
        tp_source="devig", odds=+180,
    )
    assert decision.passed is False
    assert decision.reason == "unsupported_stat_family_for_war_zone"


def test_pts_cv_exceed_immediate_reject():
    decision = evaluate_war_zone(
        stat_family="pts", hr=80, vs=95, cv=0.50,  # > 0.45
        tp_source="devig", odds=+170,
    )
    assert decision.passed is False
    assert decision.reason == "cv_exceeded"


def test_reb_cv_at_cap_passes():
    decision = evaluate_war_zone(
        stat_family="reb", hr=70, vs=90, cv=0.55,  # == cap
        tp_source="devig", odds=+180,
    )
    assert decision.passed is True


def test_3pm_cv_0_76_fails():
    decision = evaluate_war_zone(
        stat_family="3pm", hr=65, vs=92, cv=0.76,
        tp_source="devig", odds=+225,
    )
    assert decision.passed is False
    assert decision.reason == "cv_exceeded"


def test_cv_none_rejects():
    decision = evaluate_war_zone(
        stat_family="pts", hr=80, vs=95, cv=None,
        tp_source="devig", odds=+170,
    )
    assert decision.passed is False
    assert decision.reason == "cv_exceeded"


# ---------------- Step 2 — base gates ------------------------------------

def test_hr_below_55_rejects():
    d = evaluate_war_zone(
        stat_family="pts", hr=54.9, vs=95, cv=0.3,
        tp_source="devig", odds=+225,
    )
    assert d.passed is False
    assert d.reason == "hr_below_55"


def test_vs_below_85_rejects():
    d = evaluate_war_zone(
        stat_family="pts", hr=70, vs=84.9, cv=0.3,
        tp_source="devig", odds=+225,
    )
    assert d.passed is False
    assert d.reason == "vs_below_85"


# ---------------- Step 3 — edge-type split -------------------------------

def test_devig_standard_passes():
    d = evaluate_war_zone(
        stat_family="pts", hr=55, vs=85, cv=0.3,
        tp_source="devig", odds=+225,
    )
    assert d.passed is True


def test_one_sided_needs_hr_60_or_vs_90_rejects_when_neither():
    d = evaluate_war_zone(
        stat_family="pts", hr=55, vs=85, cv=0.3,
        tp_source="one_sided", odds=+225,
    )
    assert d.passed is False
    assert d.reason == "one_sided_requires_hr60_or_vs90"


def test_one_sided_passes_with_high_hr():
    d = evaluate_war_zone(
        stat_family="pts", hr=60, vs=86, cv=0.3,
        tp_source="one_sided", odds=+225,
    )
    assert d.passed is True


def test_one_sided_passes_with_high_vs():
    d = evaluate_war_zone(
        stat_family="pts", hr=55, vs=90, cv=0.3,
        tp_source="one_sided", odds=+225,
    )
    assert d.passed is True


def test_missing_tp_source_rejects():
    d = evaluate_war_zone(
        stat_family="pts", hr=70, vs=95, cv=0.3,
        tp_source=None, odds=+225,
    )
    assert d.passed is False
    assert d.reason == "no_market_tp_source"


# ---------------- Step 4 — pricing-trap reject ---------------------------

def test_pricing_trap_rejects_mid_odds_mid_signal():
    # +150..+220 AND HR<60 AND VS<90 → trap. devig + CV ok.
    d = evaluate_war_zone(
        stat_family="pts", hr=57, vs=87, cv=0.3,
        tp_source="devig", odds=+175,
    )
    assert d.passed is False
    assert d.reason == "pricing_trap"


def test_pricing_trap_edges_exactly_150_rejects():
    d = evaluate_war_zone(
        stat_family="pts", hr=58, vs=85, cv=0.3,
        tp_source="devig", odds=+150,
    )
    assert d.passed is False
    assert d.reason == "pricing_trap"


def test_pricing_trap_edges_exactly_220_rejects():
    d = evaluate_war_zone(
        stat_family="pts", hr=58, vs=85, cv=0.3,
        tp_source="devig", odds=+220,
    )
    assert d.passed is False
    assert d.reason == "pricing_trap"


def test_pricing_trap_outside_band_passes():
    # +225 is above trap band → passes on base gates alone.
    d = evaluate_war_zone(
        stat_family="pts", hr=55, vs=85, cv=0.3,
        tp_source="devig", odds=+225,
    )
    assert d.passed is True


def test_pricing_trap_bypassed_by_strong_hr():
    # Odds in trap band, HR>=60 → passes (strong signal escapes trap).
    d = evaluate_war_zone(
        stat_family="pts", hr=60, vs=86, cv=0.3,
        tp_source="devig", odds=+175,
    )
    assert d.passed is True


def test_pricing_trap_bypassed_by_strong_vs():
    # Odds in trap band, VS>=90 → passes.
    d = evaluate_war_zone(
        stat_family="pts", hr=55, vs=90, cv=0.3,
        tp_source="devig", odds=+175,
    )
    assert d.passed is True


def test_pricing_trap_applies_to_one_sided_too():
    d = evaluate_war_zone(
        stat_family="pts", hr=58, vs=88, cv=0.3,
        tp_source="one_sided", odds=+175,
    )
    # Step 3 already rejected (hr<60 and vs<90 for one_sided).
    # Reason is one_sided-check, not pricing_trap, because Step 3
    # runs before Step 4.
    assert d.passed is False
    assert d.reason == "one_sided_requires_hr60_or_vs90"


def test_high_odds_longshot_with_strong_signal_passes():
    # +700 longshot but HR=65, VS=93 — the spec explicitly says
    # "do not remove plays solely due to high odds".
    d = evaluate_war_zone(
        stat_family="pts", hr=65, vs=93, cv=0.3,
        tp_source="one_sided", odds=+700,
    )
    assert d.passed is True


# ---------------- Happy-path combos --------------------------------------

def test_devig_low_odds_edge_case():
    # devig, odds below trap band, solid signal.
    d = evaluate_war_zone(
        stat_family="pra", hr=65, vs=88, cv=0.4,
        tp_source="devig", odds=+155,
    )
    # +155 IS in trap band (150..220). HR>=60 bypasses.
    assert d.passed is True


def test_combo_market_uses_dominant_stat_cap():
    # pts_reb_ast → 0.45 cap
    d = evaluate_war_zone(
        stat_family="pts_reb_ast", hr=60, vs=88, cv=0.44,
        tp_source="one_sided", odds=+250,
    )
    assert d.passed is True

    d = evaluate_war_zone(
        stat_family="pts_reb_ast", hr=60, vs=88, cv=0.46,  # > cap
        tp_source="one_sided", odds=+250,
    )
    assert d.passed is False
    assert d.reason == "cv_exceeded"

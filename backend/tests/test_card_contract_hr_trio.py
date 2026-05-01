"""Hit-rate window trio in dashboard card contract (2026-05-01).

Locks down that the card contract surfaces L20, L10, and L5 hit-rate
values side-correctly and that legacy `hit_rate` continues to work.
"""
from services.dashboard_card_contract import to_card_contract


def _base_pick(**overrides):
    pick = {
        "player_name": "Test Player",
        "team": "TOR",
        "stat_type": "Points",
        "line": 14.5,
        "recommendation": "OVER",
        "hit_rate_over": 80.0,
        "hit_rate_under": 20.0,
        "hit_rate_l5": 80.0,    # already side-aware on the score doc
        "hit_rate_l10": 70.0,
    }
    pick.update(overrides)
    return pick


def test_trio_present_for_over():
    c = to_card_contract(_base_pick())
    assert c["hit_rate"]      == 80.0      # legacy display (L20 OVER)
    assert c["hit_rate_l20"]  == 80.0
    assert c["hit_rate_l10"]  == 70.0
    assert c["hit_rate_l5"]   == 80.0


def test_trio_uses_under_side_for_l20():
    """For UNDER picks, L20 must use hit_rate_under."""
    pick = _base_pick(recommendation="UNDER", hit_rate_l5=20.0,
                      hit_rate_l10=30.0)
    c = to_card_contract(pick)
    assert c["hit_rate_l20"] == 20.0           # = hit_rate_under
    # L5 / L10 pass through verbatim — adapter persists side-aware.
    assert c["hit_rate_l5"]  == 20.0
    assert c["hit_rate_l10"] == 30.0


def test_trio_handles_missing_subwindow_fields():
    """Old picks without L5 / L10 fields → trio fields are None."""
    pick = _base_pick()
    pick.pop("hit_rate_l5")
    pick.pop("hit_rate_l10")
    c = to_card_contract(pick)
    assert c["hit_rate_l20"] == 80.0  # still computed from hit_rate_over
    assert c["hit_rate_l10"] is None
    assert c["hit_rate_l5"]  is None


def test_trio_handles_missing_l20_fields():
    """Old picks without hit_rate_over → L20 None, L10/L5 still surface."""
    pick = _base_pick()
    pick.pop("hit_rate_over")
    pick.pop("hit_rate_under")
    c = to_card_contract(pick)
    assert c["hit_rate_l20"] is None
    assert c["hit_rate_l10"] == 70.0
    assert c["hit_rate_l5"]  == 80.0


def test_legacy_hit_rate_field_unchanged():
    """The original `hit_rate` field must keep its old contract: side-
    correct percentage from hit_rate_over / hit_rate_under (NOT L10)."""
    over_pick  = _base_pick()
    under_pick = _base_pick(recommendation="UNDER")
    assert to_card_contract(over_pick)["hit_rate"]  == 80.0
    assert to_card_contract(under_pick)["hit_rate"] == 20.0


def test_mobley_real_world_case():
    """Locks down the user-reported bug: Mobley P+A 14.5 OVER in SH.
    L20=80 (gate input), L10=70, L5=80. All three must surface
    individually so the card no longer looks inconsistent with the
    gate decision."""
    pick = _base_pick(
        player_name="Evan Mobley",
        stat_type="Pts+Asts",
        line=14.5,
        hit_rate_over=80.0, hit_rate_under=20.0,
        hit_rate_l10=70.0, hit_rate_l5=80.0,
    )
    c = to_card_contract(pick)
    assert c["hit_rate_l20"] == 80.0  # gate value
    assert c["hit_rate_l10"] == 70.0  # graph value
    assert c["hit_rate_l5"]  == 80.0  # recent-form value
    assert c["hit_rate"]     == 80.0  # legacy headline = L20

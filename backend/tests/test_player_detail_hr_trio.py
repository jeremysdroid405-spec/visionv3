"""Card↔Detail parity for the hit-rate window trio (2026-05-01).

Tests that `routes/player.py::_score_to_prop` (NBA + MLB shared helper)
includes `hit_rate_l20`, `hit_rate_l10`, `hit_rate_l5`, and
`hit_rate_sample_size` so the player-detail page never drifts away
from the pick card on these fields.
"""
from routes.player import _score_to_prop


def test_score_to_prop_includes_trio_over():
    doc = {
        "stat_type": "Points", "line": 25.5, "recommendation": "OVER",
        "hit_rate_over": 80.0, "hit_rate_under": 20.0,
        "hit_rate_l5": 80.0, "hit_rate_l10": 70.0,
        "hit_rate_sample_size": 20,
    }
    p = _score_to_prop(doc)
    assert p["hit_rate_l20"] == 80.0  # uses hit_rate_over for OVER
    assert p["hit_rate_l10"] == 70.0
    assert p["hit_rate_l5"]  == 80.0
    assert p["hit_rate_sample_size"] == 20
    # Legacy fields preserved
    assert p["hit_rate_over"]  == 80.0
    assert p["hit_rate_under"] == 20.0


def test_score_to_prop_includes_trio_under_l20_uses_under_field():
    doc = {
        "stat_type": "Points", "line": 25.5, "recommendation": "UNDER",
        "hit_rate_over": 80.0, "hit_rate_under": 20.0,
        "hit_rate_l5": 20.0, "hit_rate_l10": 30.0,
        "hit_rate_sample_size": 20,
    }
    p = _score_to_prop(doc)
    assert p["hit_rate_l20"] == 20.0  # uses hit_rate_under for UNDER
    assert p["hit_rate_l10"] == 30.0
    assert p["hit_rate_l5"]  == 20.0


def test_score_to_prop_handles_missing_subwindow_fields():
    """Old docs without L5/L10 → trio fields surface as None, no crash."""
    doc = {
        "stat_type": "Points", "line": 25.5, "recommendation": "OVER",
        "hit_rate_over": 80.0, "hit_rate_under": 20.0,
    }
    p = _score_to_prop(doc)
    assert p["hit_rate_l20"] == 80.0   # still computed
    assert p["hit_rate_l10"] is None
    assert p["hit_rate_l5"]  is None
    assert p["hit_rate_sample_size"] is None


def test_jung_real_world_case_byte_parity():
    """Locks down Josh Jung Hits 0.5 OVER (the user-reported bug).
    Card surfaced L20=90 / L10=90 / L5=100; detail page used to
    surface L20=None. Now: byte-equivalent."""
    doc = {
        "stat_type": "Hits", "line": 0.5, "recommendation": "OVER",
        "hit_rate_over": 90.0, "hit_rate_under": 10.0,
        "hit_rate_l5": 100.0, "hit_rate_l10": 90.0,
        "hit_rate_sample_size": 20,
        "tier": "front_lines",
    }
    p = _score_to_prop(doc)
    assert p["hit_rate_l20"] == 90.0
    assert p["hit_rate_l10"] == 90.0
    assert p["hit_rate_l5"]  == 100.0
    assert p["hit_rate_sample_size"] == 20
    assert p["tier"] == "front_lines"

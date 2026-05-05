"""
Regression tests for the Universal Performance Badge Generator.

Locks the SSOT behavior of `services.performance_badges.generate_performance_badges`:

  • `lasso_high_edge` reads `edge_vs_fair` in DECIMAL units (0.15, NOT 15).
    Reproduces the unit-mismatch bug that previously hid the badge on
    every real pick because the legacy MLB block compared a decimal
    against the integer literal `15`.

  • `floor_lock` is side-aware (OVER reads `hit_rate_l10`; UNDER falls
    back to `hit_rate_under` when L10 is missing).

  • `hot_streak` reads side-aware `hit_rate_l5`.

  • Output shape is `[{"badge_key": str, "id": str}, ...]` so the
    response stays drop-in compatible with the legacy MLB / rewire
    callers.
"""

from services.performance_badges import (
    EDGE_VS_FAIR_TRIGGER,
    generate_performance_badges,
)


def _keys(badges):
    return {b["badge_key"] for b in badges}


# ── lasso_high_edge regression: DECIMAL units, not percent ────────────


def test_lasso_high_edge_just_below_threshold_does_not_trigger():
    doc = {"recommendation": "OVER", "edge_vs_fair": 0.14}
    assert "lasso_high_edge" not in _keys(generate_performance_badges(doc))


def test_lasso_high_edge_at_threshold_triggers():
    doc = {"recommendation": "OVER", "edge_vs_fair": 0.15}
    assert "lasso_high_edge" in _keys(generate_performance_badges(doc))
    # Threshold constant lives in module — assert it matches the spec
    # so a future "tighten to 0.20" change has to break this test
    # explicitly.
    assert EDGE_VS_FAIR_TRIGGER == 0.15


def test_lasso_high_edge_negative_threshold_triggers():
    doc = {"recommendation": "UNDER", "edge_vs_fair": -0.15}
    assert "lasso_high_edge" in _keys(generate_performance_badges(doc))


def test_lasso_high_edge_unit_is_decimal_not_percent():
    """Sanity guard: confirm threshold is decimal units. A doc with the
    decimal `0.18` triggers; a doc with `0.05` (5%) doesn't."""
    assert "lasso_high_edge" in _keys(
        generate_performance_badges({"edge_vs_fair": 0.18})
    )
    assert "lasso_high_edge" not in _keys(
        generate_performance_badges({"edge_vs_fair": 0.05})
    )


# ── floor_lock side-aware ─────────────────────────────────────────────


def test_floor_lock_over_at_90_l10_triggers():
    doc = {"recommendation": "OVER", "hit_rate_l10": 90.0}
    assert "floor_lock" in _keys(generate_performance_badges(doc))


def test_floor_lock_over_below_90_does_not_trigger():
    doc = {"recommendation": "OVER", "hit_rate_l10": 89.9}
    assert "floor_lock" not in _keys(generate_performance_badges(doc))


def test_floor_lock_under_uses_hit_rate_under_when_l10_missing():
    doc = {"recommendation": "UNDER", "hit_rate_under": 92.0}
    assert "floor_lock" in _keys(generate_performance_badges(doc))


def test_floor_lock_under_prefers_side_aware_l10_when_present():
    doc = {"recommendation": "UNDER", "hit_rate_l10": 95.0, "hit_rate_under": 50.0}
    assert "floor_lock" in _keys(generate_performance_badges(doc))


# ── hot_streak side-aware L5 ──────────────────────────────────────────


def test_hot_streak_l5_at_80_triggers():
    doc = {"recommendation": "OVER", "hit_rate_l5": 80.0}
    assert "hot_streak" in _keys(generate_performance_badges(doc))


def test_hot_streak_below_threshold_does_not_trigger():
    doc = {"recommendation": "OVER", "hit_rate_l5": 60.0}
    assert "hot_streak" not in _keys(generate_performance_badges(doc))


# ── high_fidelity_model ───────────────────────────────────────────────


def test_high_fidelity_requires_p_true_and_vision_score():
    # Both present, p_true above threshold → fires
    doc = {"p_true_active": 0.7, "vision_score": 80.0}
    assert "high_fidelity_model" in _keys(generate_performance_badges(doc))


def test_high_fidelity_skipped_without_vision_score():
    # vision_score missing → does not fire (signals scoring stack didn't run)
    doc = {"p_true_active": 0.7}
    assert "high_fidelity_model" not in _keys(generate_performance_badges(doc))


# ── usage_spike ───────────────────────────────────────────────────────


def test_usage_spike_via_bump_percent():
    doc = {"usage_bump_percent": 5.0}
    assert "usage_spike" in _keys(generate_performance_badges(doc))


def test_usage_spike_via_vacuum_modifier_flag():
    doc = {"has_vacuum_modifier": True}
    assert "usage_spike" in _keys(generate_performance_badges(doc))


def test_usage_spike_skipped_when_bump_below_threshold():
    doc = {"usage_bump_percent": 1.0}
    assert "usage_spike" not in _keys(generate_performance_badges(doc))


# ── soft_matchup with SP buzzsaw guard (MLB) ──────────────────────────


def test_soft_matchup_fires_with_high_dvp_and_supportive_hit_rate():
    doc = {
        "recommendation": "OVER",
        "dvp_rank": 25,
        "hit_rate_l10": 70.0,
    }
    assert "soft_matchup" in _keys(generate_performance_badges(doc))


def test_soft_matchup_blocked_by_top_15_starting_pitcher():
    doc = {
        "recommendation": "OVER",
        "dvp_rank": 25,
        "hit_rate_l10": 70.0,
        "matchup_analysis": {"sp_matchup": {"rank": 8}},
    }
    assert "soft_matchup" not in _keys(generate_performance_badges(doc))


def test_soft_matchup_kept_when_starter_outside_top_15():
    doc = {
        "recommendation": "OVER",
        "dvp_rank": 25,
        "hit_rate_l10": 70.0,
        "matchup_analysis": {"sp_matchup": {"rank": 22}},
    }
    assert "soft_matchup" in _keys(generate_performance_badges(doc))


# ── volatility_extreme delegates to volatility_profile SSOT ──────────


def test_volatility_extreme_delegates_to_profile():
    # cv well above any family's extreme threshold
    doc = {"cv": 1.0, "stat_type": "Points", "line": 25.5}
    assert "volatility_extreme" in _keys(generate_performance_badges(doc))


def test_volatility_extreme_skipped_for_low_cv():
    doc = {"cv": 0.05, "stat_type": "Points", "line": 25.5}
    assert "volatility_extreme" not in _keys(generate_performance_badges(doc))


# ── Output shape is dict-form, deduplicated, deterministic order ──────


def test_output_shape_is_badge_key_id_dict():
    doc = {"recommendation": "OVER", "hit_rate_l10": 95.0, "edge_vs_fair": 0.20}
    badges = generate_performance_badges(doc)
    assert isinstance(badges, list)
    for b in badges:
        assert set(b.keys()) == {"badge_key", "id"}
        assert b["badge_key"] == b["id"]


def test_empty_input_returns_empty_list():
    assert generate_performance_badges({}) == []
    assert generate_performance_badges(None) == []


def test_no_duplicates_when_multiple_signals_overlap():
    doc = {
        "recommendation": "OVER",
        "hit_rate_l5": 90.0,
        "hit_rate_l10": 95.0,
        "edge_vs_fair": 0.30,
    }
    badges = generate_performance_badges(doc)
    keys = [b["badge_key"] for b in badges]
    assert len(keys) == len(set(keys))

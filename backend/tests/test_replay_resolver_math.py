"""Unit tests for the replay outcome resolver settlement math.

No DB. Pure-functional verification of hit/miss/push/void rules,
American-odds → P&L conversion, calibration gap, and CLV math.
"""
from __future__ import annotations

import pytest

from services.replay.resolver import (
    STAT_FAMILY_TO_FIELD,
    build_outcome_row, calibration_gap, closing_line_value,
    implied_probability, realized_payout, settle,
)


# ----- settle ---------------------------------------------------------------

def test_settle_over_hit_strict():
    assert settle("OVER", 24.5, 30, did_play=True) == "hit"


def test_settle_over_miss_strict():
    assert settle("OVER", 24.5, 20, did_play=True) == "miss"


def test_settle_over_push_on_equal():
    assert settle("OVER", 24.0, 24, did_play=True) == "push"


def test_settle_under_hit():
    assert settle("UNDER", 24.5, 18, did_play=True) == "hit"


def test_settle_under_miss():
    assert settle("UNDER", 24.5, 35, did_play=True) == "miss"


def test_settle_under_push_on_equal():
    assert settle("UNDER", 24.0, 24, did_play=True) == "push"


def test_settle_dnp_zero_value_is_void():
    assert settle("OVER", 24.5, 0, did_play=False) == "void_dnp"
    assert settle("UNDER", 24.5, 0, did_play=False) == "void_dnp"


def test_settle_dnp_with_zero_played_is_miss_for_over():
    # If the player took the floor but recorded 0 (rare, e.g. 1 minute
    # played, no stat), it's a miss for OVER, hit for UNDER.
    assert settle("OVER", 0.5, 0, did_play=True) == "miss"
    assert settle("UNDER", 0.5, 0, did_play=True) == "hit"


def test_settle_actual_none_is_void():
    assert settle("OVER", 24.5, None, did_play=True) == "void_dnp"


def test_settle_unknown_side_raises():
    with pytest.raises(ValueError):
        settle("YES", 24.5, 30, did_play=True)


# ----- realized_payout -----------------------------------------------------

def test_payout_positive_odds():
    # +150 → win 1.50 per $1
    assert realized_payout("hit", 150) == pytest.approx(1.5)


def test_payout_negative_odds():
    # -200 → win 0.50 per $1
    assert realized_payout("hit", -200) == pytest.approx(0.5)


def test_payout_miss_is_negative_one():
    assert realized_payout("miss", -200) == -1.0
    assert realized_payout("miss", +200) == -1.0


def test_payout_push_zero():
    assert realized_payout("push", -200) == 0.0


def test_payout_void_zero():
    assert realized_payout("void_dnp", -200) == 0.0


def test_payout_zero_odds_raises():
    with pytest.raises(ValueError):
        realized_payout("hit", 0)


# ----- implied_probability -------------------------------------------------

def test_implied_prob_minus_200_is_two_thirds():
    assert implied_probability(-200) == pytest.approx(0.6667, rel=1e-3)


def test_implied_prob_plus_100_is_one_half():
    assert implied_probability(+100) == pytest.approx(0.5)


def test_implied_prob_plus_300_is_one_quarter():
    assert implied_probability(+300) == pytest.approx(0.25)


def test_implied_prob_zero_raises():
    with pytest.raises(ValueError):
        implied_probability(0)


# ----- calibration_gap -----------------------------------------------------

def test_calibration_gap_perfect_prediction_zero():
    assert calibration_gap(1.0, "hit") == 0.0
    assert calibration_gap(0.0, "miss") == 0.0


def test_calibration_gap_underconfident():
    # Predicted 50%, actually hit → gap = 0.5 - 1.0 = -0.5 (underconfident)
    assert calibration_gap(0.5, "hit") == -0.5


def test_calibration_gap_overconfident():
    # Predicted 80%, actually missed → gap = 0.8 - 0 = +0.8 (overconfident)
    assert calibration_gap(0.8, "miss") == 0.8


def test_calibration_gap_void_returns_none():
    assert calibration_gap(0.5, "void_dnp") is None
    assert calibration_gap(0.5, "push") is None


def test_calibration_gap_missing_p_returns_none():
    assert calibration_gap(None, "hit") is None


# ----- closing_line_value --------------------------------------------------

def test_clv_positive_when_we_beat_close():
    # Entered at 50% implied; closed at 45% → CLV = +5 pp
    assert closing_line_value(0.50, 0.45) == 0.05


def test_clv_negative_when_close_is_better():
    assert closing_line_value(0.45, 0.50) == pytest.approx(-0.05)


def test_clv_none_when_either_side_missing():
    assert closing_line_value(None, 0.5) is None
    assert closing_line_value(0.5, None) is None


# ----- stat-family map -----------------------------------------------------

def test_stat_family_field_map_covers_all_replay_families():
    expected = {"PTS", "REB", "AST", "THREES", "BLK", "STL",
                "PTS_REB", "PTS_AST", "REB_AST", "PRA"}
    assert expected.issubset(set(STAT_FAMILY_TO_FIELD.keys()))


# ----- build_outcome_row (integration of all pieces) -----------------------

def _eval(**kw):
    base = {
        "replay_run_id":   "run-x",
        "event_id":        "evt-1",
        "canonical_key":   "nba|player_points|abc|24.5",
        "snapshot_label":  "t-30m",
        "snapshot_ts":     None,
        "bookmaker":       "draftkings",
        "player":          "abc",
        "stat_family":     "PTS",
        "is_alternate":    False,
        "is_combo":        False,
        "side":            "OVER",
        "line":            24.5,
        "odds_american":   -110,
        "p_true_active":   0.55,
        "edge_vs_fair":    0.03,
        "tier":            "front_lines",
        "vision_score":    0.62,
        "vision_score_v2": 0.65,
    }
    base.update(kw)
    return base


def test_build_outcome_over_hit_dk_minus110():
    row = build_outcome_row(
        evaluation=_eval(),
        result={"pts": 30, "did_play": True},
        closing_implied_prob=0.50,
    )
    assert row["outcome"] == "hit"
    assert row["pnl_units"] == pytest.approx(0.909, rel=1e-3)
    assert row["actual_value"] == 30
    assert row["calibration_gap"] == pytest.approx(0.55 - 1.0)
    assert row["clv"] == pytest.approx(implied_probability(-110) - 0.50)


def test_build_outcome_void_when_no_result():
    row = build_outcome_row(
        evaluation=_eval(),
        result=None,
        closing_implied_prob=None,
    )
    assert row["outcome"] == "void_dnp"
    assert row["pnl_units"] == 0.0
    assert row["actual_value"] is None
    assert row["calibration_gap"] is None
    assert row["clv"] is None


def test_build_outcome_combo_pra_under_miss():
    row = build_outcome_row(
        evaluation=_eval(stat_family="PRA", side="UNDER",
                          line=39.5, odds_american=+105),
        result={"pra": 45, "did_play": True},
    )
    assert row["actual_value"] == 45
    assert row["outcome"] == "miss"
    assert row["pnl_units"] == -1.0
    assert row["calibration_gap"] == pytest.approx(0.55 - 0.0)

"""
Unit tests for scripts/sgo/_team_outcome_graders — pure grader functions.

Locks the Phase-1 contract on every market type:
  • h2h / moneyline
  • spreads
  • game totals
  • team totals (home + away)
  • PUSH semantics
  • malformed rows / missing scores / missing line
"""
from __future__ import annotations
import os
import sys
sys.path.insert(0, "/app/backend")

import pytest

from scripts.sgo._team_outcome_graders import (
    grade_game_total, grade_h2h, grade_row, grade_spread, grade_team_total,
)


# ─────────── h2h / ML ───────────
def test_h2h_home_wins_outright():
    r = grade_h2h("HOME", 24, 17)
    assert r["outcome"] == "WIN" and r["outcome_resolved"] is True
    assert r["hit"] is True and r["actual_value"] == 7


def test_h2h_home_loses_outright():
    r = grade_h2h("home", 17, 24)
    assert r["outcome"] == "LOSS" and r["outcome_resolved"] is True
    assert r["hit"] is False and r["actual_value"] == -7


def test_h2h_away_wins_outright():
    r = grade_h2h("AWAY", 17, 24)
    assert r["outcome"] == "WIN"
    assert r["actual_value"] == 7


def test_h2h_regulation_tie_pushes():
    r = grade_h2h("HOME", 24, 24)
    assert r["outcome"] == "PUSH" and r["push"] is True
    assert r["outcome_numeric"] == 0.5


def test_h2h_ml3way_draw_side_resolves_on_tie():
    r = grade_h2h("DRAW", 24, 24)
    assert r["outcome"] == "WIN"


def test_h2h_ml3way_draw_side_loses_on_non_tie():
    r = grade_h2h("DRAW", 24, 17)
    assert r["outcome"] == "LOSS"


def test_h2h_missing_score():
    r = grade_h2h("HOME", None, 17)
    assert r["outcome"] == "UNRESOLVED"
    assert r["unresolved_reason"] == "missing_score"


def test_h2h_unknown_side():
    r = grade_h2h("FOO", 24, 17)
    assert r["outcome"] == "UNRESOLVED"
    assert r["unresolved_reason"] == "unknown_side"


# ─────────── spreads ───────────
def test_spread_home_minus_3_covers():
    # home 24 - away 17 + (-3) = 4 → cover
    r = grade_spread("HOME", -3, 24, 17)
    assert r["outcome"] == "WIN" and r["margin_vs_line"] == 4


def test_spread_home_minus_3_does_not_cover():
    # home 17 - away 15 + (-3) = -1 → loss
    r = grade_spread("HOME", -3, 17, 15)
    assert r["outcome"] == "LOSS" and r["margin_vs_line"] == -1


def test_spread_push_on_integer():
    # home 20 - away 17 + (-3) = 0 → push
    r = grade_spread("HOME", -3, 20, 17)
    assert r["outcome"] == "PUSH" and r["margin_vs_line"] == 0.0


def test_spread_half_point_never_pushes():
    r = grade_spread("HOME", -3.5, 20, 17)
    # margin -0.5 → LOSS for home
    assert r["outcome"] == "LOSS"


def test_spread_away_plus_3():
    # margin from away perspective: (17 - 20) + 3 = 0 → push
    r = grade_spread("AWAY", 3, 20, 17)
    assert r["outcome"] == "PUSH"


def test_spread_missing_line():
    r = grade_spread("HOME", None, 24, 17)
    assert r["outcome"] == "UNRESOLVED"
    assert r["unresolved_reason"] == "missing_line"


def test_spread_unknown_side():
    r = grade_spread("OVER", -3, 24, 17)
    assert r["outcome"] == "UNRESOLVED"
    assert r["unresolved_reason"] == "unknown_side"


# ─────────── game totals ───────────
def test_game_total_over_hits():
    r = grade_game_total("OVER", 41.5, 24, 21)
    assert r["outcome"] == "WIN" and r["actual_value"] == 45.0


def test_game_total_over_loses():
    r = grade_game_total("OVER", 50, 21, 14)
    assert r["outcome"] == "LOSS" and r["actual_value"] == 35.0


def test_game_total_under_hits():
    r = grade_game_total("UNDER", 50, 21, 14)
    assert r["outcome"] == "WIN"


def test_game_total_push_on_integer():
    r = grade_game_total("OVER", 45, 24, 21)
    assert r["outcome"] == "PUSH"


def test_game_total_missing_score():
    r = grade_game_total("OVER", 41.5, None, 21)
    assert r["outcome"] == "UNRESOLVED"
    assert r["unresolved_reason"] == "missing_score"


# ─────────── team totals ───────────
def test_team_total_home_over():
    r = grade_team_total("OVER", "home", 23.5, 24, 17)
    assert r["outcome"] == "WIN" and r["actual_value"] == 24.0


def test_team_total_away_under():
    r = grade_team_total("UNDER", "away", 20.5, 24, 17)
    assert r["outcome"] == "WIN" and r["actual_value"] == 17.0


def test_team_total_push():
    r = grade_team_total("OVER", "home", 24, 24, 17)
    assert r["outcome"] == "PUSH"


def test_team_total_unknown_entity():
    r = grade_team_total("OVER", "neither", 20, 24, 17)
    assert r["outcome"] == "UNRESOLVED"


# ─────────── dispatcher (grade_row) ───────────
def test_grade_row_dispatches_h2h():
    row = {"betTypeID": "ml", "statEntityID": "home", "side": "HOME",
            "line": None}
    r = grade_row(row, 24, 17)
    assert r["outcome"] == "WIN"


def test_grade_row_dispatches_spread():
    row = {"betTypeID": "sp", "statEntityID": "home", "side": "HOME",
            "line": -3}
    r = grade_row(row, 24, 17)
    assert r["outcome"] == "WIN"


def test_grade_row_dispatches_game_total():
    row = {"betTypeID": "ou", "statEntityID": "all", "side": "OVER",
            "line": 41.5}
    r = grade_row(row, 24, 21)
    assert r["outcome"] == "WIN"


def test_grade_row_dispatches_team_total_home():
    row = {"betTypeID": "ou", "statEntityID": "home", "side": "OVER",
            "line": 23.5}
    r = grade_row(row, 24, 17)
    assert r["outcome"] == "WIN"


def test_grade_row_dispatches_team_total_away():
    row = {"betTypeID": "ou", "statEntityID": "away", "side": "UNDER",
            "line": 20.5}
    r = grade_row(row, 24, 17)
    assert r["outcome"] == "WIN"


def test_grade_row_unknown_bet_type():
    row = {"betTypeID": "alt-spread", "statEntityID": "home",
            "side": "HOME", "line": -3}
    r = grade_row(row, 24, 17)
    assert r["outcome"] == "UNRESOLVED"
    assert r["unresolved_reason"] == "unknown_bet_type"


def test_grade_row_malformed_no_dict():
    r = grade_row(None, 24, 17)
    assert r["outcome"] == "UNRESOLVED"
    assert r["unresolved_reason"] == "malformed_row"


def test_grade_row_malformed_bettype_not_string():
    row = {"betTypeID": 123}
    r = grade_row(row, 24, 17)
    assert r["unresolved_reason"] == "malformed_row"


def test_grade_row_ou_with_unknown_entity():
    row = {"betTypeID": "ou", "statEntityID": "1q", "side": "OVER",
            "line": 20}
    r = grade_row(row, 24, 17)
    assert r["unresolved_reason"] == "malformed_row"


# ─────────── outcome dict shape contract ───────────
@pytest.mark.parametrize("scenario", [
    {"side": "HOME", "hs": 24, "as": 17, "tag": "win"},
    {"side": "HOME", "hs": 14, "as": 17, "tag": "loss"},
    {"side": "HOME", "hs": 17, "as": 17, "tag": "push"},
    {"side": "HOME", "hs": None, "as": 17, "tag": "unresolved"},
])
def test_outcome_shape_always_consistent(scenario):
    r = grade_h2h(scenario["side"], scenario["hs"], scenario["as"])
    expected = {"outcome", "outcome_resolved", "outcome_numeric",
                 "hit", "push", "actual_value", "margin_vs_line",
                 "unresolved_reason"}
    assert set(r.keys()) == expected, (
        f"Schema drift: missing={expected - set(r.keys())} "
        f"extra={set(r.keys()) - expected}")

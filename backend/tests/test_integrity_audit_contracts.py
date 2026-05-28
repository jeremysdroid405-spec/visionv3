"""
Integrity audit contracts (2026-06-02). Pure-function pins so the
invariants the optimizer relies on can be regression-tested without
spinning up MongoDB or running the live pipeline.
"""
from __future__ import annotations
import sys

sys.path.insert(0, "/app/backend")
from services.replay.integrity_audit import (  # noqa: E402
    american_to_implied, expected_outcome_numeric, validate_row,
)


def test_american_to_implied_positive_odds() -> None:
    assert abs(american_to_implied(100) - 0.5) < 1e-9
    assert abs(american_to_implied(150) - 0.4) < 1e-9
    assert abs(american_to_implied(490) - 100 / 590) < 1e-9


def test_american_to_implied_negative_odds() -> None:
    assert abs(american_to_implied(-150) - 0.6) < 1e-9
    assert abs(american_to_implied(-200) - (200 / 300)) < 1e-9
    assert abs(american_to_implied(-50000) - (50000 / 50100)) < 1e-9


def test_outcome_numeric_pushes_on_equality() -> None:
    assert expected_outcome_numeric(actual=5, line=5, side="OVER") == 0.5
    assert expected_outcome_numeric(actual=5, line=5, side="UNDER") == 0.5


def test_outcome_numeric_over_logic() -> None:
    assert expected_outcome_numeric(actual=6, line=5, side="OVER") == 1
    assert expected_outcome_numeric(actual=4, line=5, side="OVER") == 0


def test_outcome_numeric_under_logic() -> None:
    assert expected_outcome_numeric(actual=4, line=5, side="UNDER") == 1
    assert expected_outcome_numeric(actual=6, line=5, side="UNDER") == 0


def test_outcome_numeric_none_inputs() -> None:
    assert expected_outcome_numeric(actual=None, line=5, side="OVER") is None
    assert expected_outcome_numeric(actual=5, line=None, side="OVER") is None
    assert expected_outcome_numeric(actual=5, line=5, side=None) is None


def test_validate_row_complete_healthy_row() -> None:
    row = {
        "odds": -150, "implied_probability": 0.6,
        "fair_probability": 0.65, "edge": 0.05,
        "tp": 0.65, "outcome_resolved": True,
        "outcome_numeric": 1, "actual_value": 6, "line": 5, "side": "OVER",
    }
    r = validate_row(row)
    assert r["odds_present"] is True
    assert r["implied_matches_odds"] is True
    assert r["edge_matches_components"] is True
    assert r["tp_in_probability_scale"] is True
    assert r["resolved_has_outcome"] is True
    assert r["outcome_arithmetic_correct"] is True


def test_validate_row_catches_percent_scale_tp() -> None:
    row = {"odds": 100, "implied_probability": 0.5,
            "fair_probability": 0.55, "edge": 0.05, "tp": 55.0,
            "outcome_resolved": False, "outcome_numeric": None,
            "line": 5, "side": "OVER"}
    r = validate_row(row)
    assert r["tp_in_probability_scale"] is False, (
        "Row with tp=55 (percent) should fail the [0,1] guard."
    )


def test_validate_row_catches_wrong_implied() -> None:
    row = {"odds": -150, "implied_probability": 0.5,  # wrong (should be 0.6)
            "fair_probability": 0.5, "edge": 0.0,
            "tp": 0.5, "outcome_resolved": False, "outcome_numeric": None}
    r = validate_row(row)
    assert r["implied_matches_odds"] is False


def test_validate_row_catches_outcome_mismatch() -> None:
    row = {"odds": 100, "implied_probability": 0.5,
            "fair_probability": 0.5, "edge": 0.0,
            "tp": 0.5, "outcome_resolved": True,
            "outcome_numeric": 1,  # claims WIN
            "actual_value": 4, "line": 5, "side": "OVER",  # but actual < line
                                                                 # for OVER → LOSS expected
    }
    r = validate_row(row)
    assert r["outcome_arithmetic_correct"] is False

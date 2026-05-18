"""Regression — eligibility predicate must canonicalize the row's
`stat_family` before matching the allow_set.

Before fix (2026-05-18): legacy `mlb_replay_model_outputs` rows carrying
the pre-canonicalisation family token (`"strikeouts"` / `"pitcher_walks"`)
were silently filtered out because the allow_set (built from
`historical_input.load_props`) keyed off canonical names
(`"batter_strikeouts"` / `"walks_allowed"`).
"""
from services.pipeline.runner import (
    _build_allow_set, _make_eligibility_predicate,
)


def _eligible(stat_family: str) -> dict:
    return {
        "event_id": "EV1",
        "player_name_normalized": "aaron judge",
        "stat_family": stat_family,
        "line": 0.5,
        "side": "OVER",
    }


def _layer3(stat_family: str) -> dict:
    """A Layer-3 raw row, mimicking `mlb_replay_model_outputs` shape."""
    return {
        "sport": "mlb",
        "event_id": "EV1",
        "player_name_normalized": "aaron judge",
        "stat_family": stat_family,
        "line": 0.5,
        "side": "OVER",
    }


def test_predicate_accepts_legacy_strikeouts_when_allow_set_uses_canonical():
    """Allow_set built with canonical name; Layer-3 row uses legacy alias.
    Predicate must accept the row."""
    allow_set = _build_allow_set([_eligible("batter_strikeouts")])
    pred = _make_eligibility_predicate(allow_set)
    assert pred(_layer3("strikeouts")) is True
    assert pred(_layer3("batter_strikeouts")) is True


def test_predicate_accepts_legacy_pitcher_walks_when_allow_set_uses_canonical():
    allow_set = _build_allow_set([_eligible("walks_allowed")])
    pred = _make_eligibility_predicate(allow_set)
    assert pred(_layer3("pitcher_walks")) is True
    assert pred(_layer3("walks_allowed")) is True


def test_predicate_rejects_unknown_family():
    allow_set = _build_allow_set([_eligible("batter_strikeouts")])
    pred = _make_eligibility_predicate(allow_set)
    assert pred(_layer3("hits")) is False


def test_predicate_rejects_when_line_differs():
    allow_set = _build_allow_set([_eligible("batter_strikeouts")])
    pred = _make_eligibility_predicate(allow_set)
    row = _layer3("strikeouts")
    row["line"] = 1.5
    assert pred(row) is False


def test_predicate_rejects_when_side_differs():
    allow_set = _build_allow_set([_eligible("batter_strikeouts")])
    pred = _make_eligibility_predicate(allow_set)
    row = _layer3("strikeouts")
    row["side"] = "UNDER"
    assert pred(row) is False

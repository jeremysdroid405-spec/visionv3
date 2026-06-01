"""
Unit tests for scripts/sgo/backfill_team_matchup_scores.

Locks the extractor against every observed SGO /v2/events shape:
  • top-level homeScore / awayScore (camelCase)
  • snake_case home_score / away_score
  • nested under results.{home,away}Score
  • nested under results.scores.{home,away}
  • nested under final_score.{home,away}
  • nested under homeTeam.score / awayTeam.score

Plus:
  • idempotency gate (_is_score_present)
  • malformed inputs return (None, None) rather than raising
"""
from __future__ import annotations
import os
import sys
sys.path.insert(0, "/app/backend")

import pytest

from scripts.sgo.backfill_team_matchup_scores import (
    _is_score_present, extract_scores_from_sgo_event,
)


# ─────────── extractor shape tolerance ───────────
def test_extract_top_level_camel():
    ev = {"homeScore": 24, "awayScore": 17}
    assert extract_scores_from_sgo_event(ev) == (24.0, 17.0)


def test_extract_top_level_snake():
    ev = {"home_score": 5, "away_score": 3}
    assert extract_scores_from_sgo_event(ev) == (5.0, 3.0)


def test_extract_nested_under_results():
    ev = {"results": {"homeScore": 110, "awayScore": 102}}
    assert extract_scores_from_sgo_event(ev) == (110.0, 102.0)


def test_extract_nested_results_with_scores_sub():
    ev = {"results": {"scores": {"home": 24, "away": 21}}}
    assert extract_scores_from_sgo_event(ev) == (24.0, 21.0)


def test_extract_nested_results_with_final_sub():
    ev = {"results": {"final": {"home": 7, "away": 14}}}
    assert extract_scores_from_sgo_event(ev) == (7.0, 14.0)


def test_extract_top_level_final_score():
    ev = {"final_score": {"home": 30, "away": 24}}
    assert extract_scores_from_sgo_event(ev) == (30.0, 24.0)


def test_extract_top_level_scores():
    ev = {"scores": {"home": 1, "away": 0}}
    assert extract_scores_from_sgo_event(ev) == (1.0, 0.0)


def test_extract_home_away_team_score():
    ev = {"homeTeam": {"score": 31}, "awayTeam": {"score": 28}}
    assert extract_scores_from_sgo_event(ev) == (31.0, 28.0)


def test_extract_partial_falls_through_chain():
    """Home score in results, away score under homeTeam (rare shape mix)."""
    ev = {"results": {"homeScore": 24}, "awayTeam": {"score": 17}}
    assert extract_scores_from_sgo_event(ev) == (24.0, 17.0)


def test_extract_returns_none_when_truly_missing():
    ev = {"event_id": "x", "status": "completed"}
    assert extract_scores_from_sgo_event(ev) == (None, None)


def test_extract_handles_non_dict():
    assert extract_scores_from_sgo_event(None) == (None, None)
    assert extract_scores_from_sgo_event(42) == (None, None)
    assert extract_scores_from_sgo_event("oops") == (None, None)


def test_extract_handles_string_scores_coerces():
    """SGO sometimes ships scores as strings."""
    ev = {"homeScore": "24", "awayScore": "17"}
    assert extract_scores_from_sgo_event(ev) == (24.0, 17.0)


def test_extract_partial_one_side_missing_returns_none_pair_safely():
    """If only one side present, the missing side is None — caller must
    treat that as 'unable to grade' (build_team_historical_outcomes does)."""
    ev = {"homeScore": 24}
    hs, as_ = extract_scores_from_sgo_event(ev)
    assert hs == 24.0 and as_ is None


# ─────────── idempotency gate ───────────
def test_score_present_true_when_both_set():
    row = {"home_score": 24, "away_score": 17}
    assert _is_score_present(row) is True


def test_score_present_false_when_missing():
    assert _is_score_present({}) is False
    assert _is_score_present({"home_score": 24}) is False
    assert _is_score_present({"away_score": 17}) is False
    assert _is_score_present({"home_score": None, "away_score": 17}) is False


def test_score_present_accepts_zero():
    """A real 0–0 final (e.g. weather-shortened MLB tie) must not be
    re-fetched."""
    row = {"home_score": 0, "away_score": 0}
    assert _is_score_present(row) is True

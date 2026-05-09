"""Phase 2 leakage + chronology test suite.

Pure unit tests — no DB, no API, no filesystem.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.replay.leakage_checks import (
    ChronologyViolation, LeakageDetected,
    assert_chronology, assert_no_future_games, assert_pregame_only,
    snapshot_lineage_chain_intact,
)


def _u(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


# ----- assert_no_future_games ----------------------------------------------

def test_no_future_games_passes_when_all_in_past():
    as_of = _u(2024, 3, 1, 22, 0)
    logs = [
        {"game_date": _u(2024, 1, 15)},
        {"game_date": _u(2024, 2, 10)},
        {"game_date": _u(2024, 2, 28)},
    ]
    assert_no_future_games(logs, as_of_ts=as_of)  # no raise


def test_no_future_games_raises_when_one_is_future():
    as_of = _u(2024, 3, 1, 22, 0)
    logs = [
        {"game_date": _u(2024, 2, 28)},
        {"game_date": _u(2024, 3, 2)},  # future!
    ]
    with pytest.raises(LeakageDetected):
        assert_no_future_games(logs, as_of_ts=as_of)


def test_no_future_games_handles_iso_strings():
    as_of = _u(2024, 3, 1, 22, 0)
    logs = [
        {"game_date": "2024-02-28T20:00:00Z"},
        {"game_date": "2024-03-02T00:00:00Z"},
    ]
    with pytest.raises(LeakageDetected):
        assert_no_future_games(logs, as_of_ts=as_of)


def test_no_future_games_naive_as_of_rejected():
    naive = datetime(2024, 3, 1, 22, 0)
    with pytest.raises(ValueError):
        assert_no_future_games([], as_of_ts=naive)


def test_no_future_games_ignores_missing_timestamp():
    as_of = _u(2024, 3, 1, 22, 0)
    logs = [{"player": "x"}, {"game_date": None}]
    assert_no_future_games(logs, as_of_ts=as_of)  # no raise


def test_no_future_games_boundary_equal_passes():
    """Equal-to-as-of is NOT future."""
    as_of = _u(2024, 3, 1, 22, 0)
    logs = [{"game_date": _u(2024, 3, 1, 22, 0)}]
    assert_no_future_games(logs, as_of_ts=as_of)  # no raise


# ----- assert_pregame_only -------------------------------------------------

def test_pregame_only_passes_when_strictly_before():
    assert_pregame_only(_u(2024, 3, 2, 0, 0), _u(2024, 3, 2, 0, 10))


def test_pregame_only_rejects_at_tip():
    with pytest.raises(ChronologyViolation):
        assert_pregame_only(_u(2024, 3, 2, 0, 10), _u(2024, 3, 2, 0, 10))


def test_pregame_only_rejects_post_tip():
    with pytest.raises(ChronologyViolation):
        assert_pregame_only(_u(2024, 3, 2, 0, 11), _u(2024, 3, 2, 0, 10))


# ----- assert_chronology --------------------------------------------------

def test_chronology_pass_full_8_window_ladder():
    ct = _u(2024, 3, 2, 0, 10)
    snaps = [
        {"snapshot_label": "t-24h",  "snapshot_ts": ct - timedelta(hours=24)},
        {"snapshot_label": "t-12h",  "snapshot_ts": ct - timedelta(hours=12)},
        {"snapshot_label": "t-6h",   "snapshot_ts": ct - timedelta(hours=6)},
        {"snapshot_label": "t-3h",   "snapshot_ts": ct - timedelta(hours=3)},
        {"snapshot_label": "t-90m",  "snapshot_ts": ct - timedelta(minutes=90)},
        {"snapshot_label": "t-60m",  "snapshot_ts": ct - timedelta(minutes=60)},
        {"snapshot_label": "t-30m",  "snapshot_ts": ct - timedelta(minutes=30)},
        {"snapshot_label": "close",  "snapshot_ts": ct - timedelta(minutes=5)},
    ]
    assert_chronology(snaps, commence_time=ct)  # no raise


def test_chronology_reject_non_monotonic():
    ct = _u(2024, 3, 2, 0, 10)
    snaps = [
        {"snapshot_label": "t-24h", "snapshot_ts": ct - timedelta(hours=24)},
        {"snapshot_label": "t-30m", "snapshot_ts": ct - timedelta(minutes=30)},
        {"snapshot_label": "t-12h", "snapshot_ts": ct - timedelta(hours=12)},
    ]
    with pytest.raises(ChronologyViolation):
        assert_chronology(snaps, commence_time=ct)


def test_chronology_reject_duplicate_label():
    ct = _u(2024, 3, 2, 0, 10)
    snaps = [
        {"snapshot_label": "close", "snapshot_ts": ct - timedelta(minutes=10)},
        {"snapshot_label": "close", "snapshot_ts": ct - timedelta(minutes=5)},
    ]
    with pytest.raises(ChronologyViolation):
        assert_chronology(snaps, commence_time=ct)


def test_chronology_reject_post_tip_snapshot():
    ct = _u(2024, 3, 2, 0, 10)
    snaps = [
        {"snapshot_label": "t-30m", "snapshot_ts": ct - timedelta(minutes=30)},
        {"snapshot_label": "live",  "snapshot_ts": ct + timedelta(minutes=1)},
    ]
    with pytest.raises(ChronologyViolation):
        assert_chronology(snaps, commence_time=ct)


# ----- snapshot_lineage_chain_intact --------------------------------------

def test_lineage_chain_intact_for_well_formed_chain():
    chain = [
        {"timestamp": "2024-03-01T22:00:40Z",
         "next_timestamp": "2024-03-01T22:05:40Z"},
        {"timestamp": "2024-03-01T22:05:40Z",
         "next_timestamp": "2024-03-01T22:10:39Z"},
        {"timestamp": "2024-03-01T22:10:39Z",
         "next_timestamp": "2024-03-01T22:15:39Z"},
    ]
    assert snapshot_lineage_chain_intact(chain) is True


def test_lineage_chain_broken_when_next_does_not_match():
    chain = [
        {"timestamp": "2024-03-01T22:00:40Z",
         "next_timestamp": "2024-03-01T22:05:40Z"},
        {"timestamp": "2024-03-01T22:07:40Z",   # gap!
         "next_timestamp": "2024-03-01T22:12:39Z"},
    ]
    assert snapshot_lineage_chain_intact(chain) is False


def test_lineage_chain_single_envelope_is_intact():
    assert snapshot_lineage_chain_intact([
        {"timestamp": "2024-03-01T22:00:40Z", "next_timestamp": None},
    ]) is True


def test_lineage_chain_empty_is_intact():
    assert snapshot_lineage_chain_intact([]) is True


def test_lineage_chain_tolerates_missing_pointers():
    chain = [
        {"timestamp": "2024-03-01T22:00:40Z"},
        {"timestamp": "2024-03-01T22:05:40Z"},
    ]
    # No next_timestamp on first → we don't claim a break.
    assert snapshot_lineage_chain_intact(chain) is True

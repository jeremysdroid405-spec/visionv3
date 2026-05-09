"""Unit tests for the replay snapshot plan (Phase 0).

No DB. No API. Pure math/contract checks.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.replay.snapshot_plan import (
    REPLAY_WINDOWS,
    REPLAY_WINDOW_LABELS,
    PER_TIER_CANONICAL_SNAPSHOT,
    snapshot_for,
    minutes_before_start,
)


# ----- Window ladder shape -------------------------------------------------

def test_eight_windows_in_user_specified_order():
    assert REPLAY_WINDOW_LABELS == [
        "t-24h", "t-12h", "t-6h", "t-3h",
        "t-90m", "t-60m", "t-30m", "close",
    ]


def test_window_offsets_strictly_decreasing():
    """Offsets must monotonically decrease — earliest first, close last."""
    minutes = [m for _, m in REPLAY_WINDOWS]
    assert all(a > b for a, b in zip(minutes, minutes[1:])), minutes


def test_no_duplicate_labels_or_offsets():
    labels = [w[0] for w in REPLAY_WINDOWS]
    offsets = [w[1] for w in REPLAY_WINDOWS]
    assert len(set(labels)) == len(labels)
    assert len(set(offsets)) == len(offsets)


def test_close_is_minimum_offset():
    """`close` is by definition the snapshot nearest to tip."""
    minutes_by_label = dict(REPLAY_WINDOWS)
    assert minutes_by_label["close"] == min(minutes_by_label.values())


# ----- Per-tier canonical mapping (user directive) -------------------------

def test_per_tier_canonical_matches_user_directive():
    assert PER_TIER_CANONICAL_SNAPSHOT["safe_haven"]  == "close"
    assert PER_TIER_CANONICAL_SNAPSHOT["front_lines"] == "t-60m"
    assert PER_TIER_CANONICAL_SNAPSHOT["war_zone"]    == "t-30m"


def test_per_tier_canonical_only_uses_known_labels():
    for tier, label in PER_TIER_CANONICAL_SNAPSHOT.items():
        assert label in REPLAY_WINDOW_LABELS, (tier, label)


# ----- snapshot_for() math --------------------------------------------------

def _ct():
    return datetime(2024, 3, 2, 0, 10, tzinfo=timezone.utc)


def test_snapshot_for_close_is_5_min_before_tip():
    snap = snapshot_for(_ct(), "close")
    assert snap == _ct() - timedelta(minutes=5)


def test_snapshot_for_t24h_is_24_hours_before():
    snap = snapshot_for(_ct(), "t-24h")
    assert snap == _ct() - timedelta(hours=24)


def test_snapshot_for_t30m_is_30_min_before():
    snap = snapshot_for(_ct(), "t-30m")
    assert snap == _ct() - timedelta(minutes=30)


def test_snapshot_for_all_labels_are_strictly_before_commence():
    ct = _ct()
    for label in REPLAY_WINDOW_LABELS:
        snap = snapshot_for(ct, label)
        assert snap < ct, label


def test_snapshot_for_naive_datetime_rejected():
    naive = datetime(2024, 3, 2, 0, 10)
    with pytest.raises(ValueError):
        snapshot_for(naive, "close")


def test_snapshot_for_non_utc_rejected():
    east_coast = datetime(2024, 3, 1, 19, 10,
                          tzinfo=timezone(timedelta(hours=-5)))
    with pytest.raises(ValueError):
        snapshot_for(east_coast, "close")


def test_snapshot_for_unknown_label_rejected():
    with pytest.raises(ValueError):
        snapshot_for(_ct(), "t-1m")


# ----- minutes_before_start ------------------------------------------------

def test_minutes_before_start_round_trip_through_snapshot_for():
    """For every window, minutes_before_start(snapshot_for(ct, lbl), ct)
    must match the declared offset minutes."""
    ct = _ct()
    declared = dict(REPLAY_WINDOWS)
    for label in REPLAY_WINDOW_LABELS:
        snap = snapshot_for(ct, label)
        assert minutes_before_start(snap, ct) == declared[label], label


def test_minutes_before_start_naive_rejected():
    ct = _ct()
    naive = datetime(2024, 3, 2, 0, 10)
    with pytest.raises(ValueError):
        minutes_before_start(naive, ct)
    with pytest.raises(ValueError):
        minutes_before_start(ct, naive)

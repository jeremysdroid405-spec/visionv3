"""Tests for `services.replay.matchup`."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from services.replay.matchup import (
    STAT_FIELD_MAP,
    _matchup_strength_from_rank,
    _normalize_stat_family,
    _date_window,
    compute_matchup_blob,
)


def test_matchup_strength_from_rank_endpoints():
    """Production contract: 0.5 = neutral, >0.5 favors OVER."""
    assert _matchup_strength_from_rank(1)   == 0.0   # best def → bad for OVER
    assert _matchup_strength_from_rank(15)  == pytest.approx(0.483, abs=0.01)
    assert _matchup_strength_from_rank(16)  == pytest.approx(0.517, abs=0.01)
    assert _matchup_strength_from_rank(30)  == 1.0   # worst def → great for OVER


def test_matchup_strength_from_rank_handles_degenerate_total():
    assert _matchup_strength_from_rank(1, total=1) == 0.5


def test_normalize_stat_family_known():
    assert _normalize_stat_family("PTS") == "PTS"
    assert _normalize_stat_family("threes") == "THREES"
    assert _normalize_stat_family("PRA") == "PRA"


def test_stat_field_map_combos_sum_components():
    assert STAT_FIELD_MAP["PRA"] == ["pts", "reb", "ast"]
    assert STAT_FIELD_MAP["PTS_REB"] == ["pts", "reb"]
    assert STAT_FIELD_MAP["THREES"] == ["fg3m"]


def test_date_window_inclusive_start_exclusive_end():
    s, e = _date_window("2024-02-15", lookback_days=10)
    assert s == "2024-02-05"
    assert e == "2024-02-15"


@pytest.mark.asyncio
async def test_compute_matchup_blob_returns_completeness_missing_on_no_inputs():
    """No team_ids → both signals None → matchup_missing."""
    class FakeColl:
        async def aggregate(self, *_a, **_k):
            if False: yield None  # async-generator stub
        def find(self, *_a, **_k):
            return self
        def sort(self, *_a, **_k):
            return self
        def limit(self, *_a, **_k):
            return self
        def __aiter__(self):
            return self
        async def __anext__(self):
            raise StopAsyncIteration
    class FakeDB(dict):
        def __getitem__(self, name):
            return FakeColl()
    snap = datetime(2024, 2, 15, tzinfo=timezone.utc)
    blob = await compute_matchup_blob(
        FakeDB(),
        player_team_id=None, opponent_team_id=None,
        stat_family="PTS", snapshot_ts=snap,
    )
    assert blob["pace_factor"] is None
    assert blob["matchup_strength"] is None
    assert blob["feature_completeness"] == "matchup_missing"
    assert blob["error"] is None

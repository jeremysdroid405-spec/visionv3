"""End-to-end smoke tests for the SGO-mode feature cache path.

Locks in: when feature_source="sgo_player_stats", the cache builds
prior-game-log rolling features WITHOUT touching mlb_master_hub_2026.
This is the SSOT path for SGO historical replay (no BDL backfill).
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Dict, List

import pytest

from services.replay.mlb_feature_cache import (
    _SGO_STAT_FIELD_MAP,
    _sgo_stat_values_as_of,
    normalize_player_name,
    _compute_hit_rate_panels,
)


def _gl(date: str, **stats: Any) -> Dict[str, Any]:
    """Shorthand: build an SGO-shaped flattened game-log row."""
    return {"date": date, "game_date": date, "player_name": "Aaron Judge",
              "player_id": "ply_judge", **stats}


def test_sgo_stat_field_map_covers_all_required_families():
    """The user's spec called out these canonical families. Each MUST
    have a key in _SGO_STAT_FIELD_MAP or the SGO path will silently
    skip every prop for that family."""
    REQUIRED = {
        "hits", "total_bases", "hits_runs_rbis", "runs", "rbis",
        "batter_walks", "batter_strikeouts",
        "pitcher_strikeouts", "earned_runs", "pitching_outs",
        "hits_allowed", "walks_allowed",
    }
    missing = REQUIRED - set(_SGO_STAT_FIELD_MAP.keys())
    assert not missing, f"missing _SGO_STAT_FIELD_MAP entries: {missing}"


def test_sgo_stat_values_as_of_filters_to_pre_replay_dates():
    """Future-leak guard. Replays must NEVER see logs on or after the
    replay_date itself."""
    logs = [
        _gl("2025-05-02", hits=3),   # equal — must be excluded
        _gl("2025-05-01", hits=2),   # the replay date — must be excluded
        _gl("2025-04-30", hits=1),   # prior — included
        _gl("2025-04-29", hits=0),
        _gl("2025-04-28", hits=2),
        _gl("2025-04-27", hits=1),
        _gl("2025-04-26", hits=3),
    ]
    vals, _, dates = _sgo_stat_values_as_of(logs, "hits", "2025-05-01")
    assert vals == [1.0, 0.0, 2.0, 1.0, 3.0]
    assert dates == ["2025-04-30", "2025-04-29",
                       "2025-04-28", "2025-04-27", "2025-04-26"]


def test_sgo_stat_values_as_of_returns_floats():
    logs = [_gl("2025-04-30", total_bases=2)]
    vals, _, _ = _sgo_stat_values_as_of(logs, "total_bases", "2025-05-01")
    assert vals == [2.0]
    assert all(isinstance(v, float) for v in vals)


def test_sgo_stat_values_as_of_pitcher_walks_routes_to_pitching_walks():
    """`walks_allowed` (canonical family) → `pitching_walks` (sgo field
    name). Regression guard for the map."""
    logs = [_gl("2025-04-30", pitching_walks=3, walks=0)]
    vals, _, _ = _sgo_stat_values_as_of(logs, "walks_allowed", "2025-05-01")
    assert vals == [3.0]
    # batter_walks routes to `walks`, not `pitching_walks`
    vals2, _, _ = _sgo_stat_values_as_of(logs, "batter_walks", "2025-05-01")
    assert vals2 == [0.0]


def test_sgo_pitcher_families_use_pitching_prefixed_fields():
    log = _gl("2025-04-30",
               pitcher_strikeouts=9, pitching_walks=1,
               pitching_hits_allowed=5, pitching_earned_runs=2,
               pitching_outs=21)
    cases = [
        ("pitcher_strikeouts",  9.0),
        ("walks_allowed",       1.0),
        ("hits_allowed",        5.0),
        ("earned_runs",         2.0),
        ("pitching_outs",      21.0),
    ]
    for family, expected in cases:
        vals, _, _ = _sgo_stat_values_as_of([log], family, "2025-05-01")
        assert vals == [expected], f"{family} got {vals}"


def test_sgo_stat_values_as_of_unknown_family_returns_empty():
    """Families that aren't in _SGO_STAT_FIELD_MAP must produce an
    EMPTY list (not raise). Caller increments skipped_stat_mapping."""
    logs = [_gl("2025-04-30", hits=1)]
    vals, pa, dates = _sgo_stat_values_as_of(
        logs, "definitely_not_a_real_family", "2025-05-01")
    assert vals == [] and pa == [] and dates == []


def test_sgo_logs_must_be_sorted_newest_first():
    """The caller in `cache_date` builds the idx already sorted newest-
    first. The reader assumes that; verify the reader honors it (slice
    via WINDOW_DEPTH always takes from the head)."""
    # 35 prior games — the reader truncates to WINDOW_DEPTH=30
    logs = [_gl(f"2025-04-{day:02d}", hits=day)
             for day in range(30, 0, -1)]   # 30,29,..,1 = 30 entries
    extras = [_gl(f"2025-03-{day:02d}", hits=99) for day in (28, 27, 26, 25, 24)]
    logs = logs + extras   # 35 total
    vals, _, _ = _sgo_stat_values_as_of(logs, "hits", "2025-05-01")
    assert len(vals) == 30
    # The first window is days 30..1 of April — none of the March
    # filler (hits=99) should have leaked in.
    assert 99.0 not in vals


def test_hit_rate_panels_match_sgo_walked_values():
    """Verifies the full pipeline: SGO logs → stat_values → rolling
    panels match what the existing _compute_hit_rate_panels emits.
    This is what Layer-3 ultimately reads."""
    logs = [
        _gl("2025-04-30", hits=2), _gl("2025-04-29", hits=1),
        _gl("2025-04-28", hits=3), _gl("2025-04-27", hits=0),
        _gl("2025-04-26", hits=2), _gl("2025-04-25", hits=1),
        _gl("2025-04-24", hits=2), _gl("2025-04-23", hits=4),
        _gl("2025-04-22", hits=1), _gl("2025-04-21", hits=2),
    ]
    vals, _, _ = _sgo_stat_values_as_of(logs, "hits", "2025-05-01")
    panels = _compute_hit_rate_panels(vals)
    assert panels["n_games"] == 10
    # l5_mean: (2+1+3+0+2)/5 = 1.6
    assert panels["l5_mean"] == pytest.approx(1.6, rel=1e-6)
    # l10_mean: avg of all 10
    expected_10 = (2+1+3+0+2+1+2+4+1+2) / 10.0
    assert panels["l10_mean"] == pytest.approx(expected_10, rel=1e-6)
    # CV must be non-negative
    assert panels["l10_cv"] >= 0
    assert panels["l5_cv"] >= 0


@pytest.mark.asyncio
async def test_sgo_prior_logs_for_window_filters_and_normalizes(monkeypatch):
    """`_sgo_prior_logs_for_window` must:
      • filter by league_id + game_date window [replay - lookback, replay)
      • normalize player_name into the index key
      • flatten stats out so the reader can do g.get(field)
      • compute hits_runs_rbis composite when all components present
      • sort each player's list newest-first
    """
    from services.replay.mlb_feature_cache import _sgo_prior_logs_for_window
    docs = [
        {"league_id": "MLB", "game_date": "2025-04-30",
          "player_name": "José Ramírez", "player_id": "p_jr",
          "stats": {"hits": 2, "runs": 1, "rbi": 3, "total_bases": 5}},
        {"league_id": "MLB", "game_date": "2025-04-29",
          "player_name": "José Ramírez", "player_id": "p_jr",
          "stats": {"hits": 1, "runs": 0, "rbi": 1}},
        # Out of window (lookback=60 days, replay 2025-05-01, so 2024-12-31 ok)
        {"league_id": "MLB", "game_date": "2025-02-01",
          "player_name": "José Ramírez", "player_id": "p_jr",
          "stats": {"hits": 99}},
        # Wrong league — must be excluded
        {"league_id": "NBA", "game_date": "2025-04-30",
          "player_name": "José Ramírez", "player_id": "p_jr",
          "stats": {"hits": 50}},
        # On/after replay_date — excluded
        {"league_id": "MLB", "game_date": "2025-05-01",
          "player_name": "José Ramírez", "player_id": "p_jr",
          "stats": {"hits": 7}},
    ]

    class FakeCursor:
        def __init__(self, data): self._data = list(data); self._i = 0
        def __aiter__(self): return self
        async def __anext__(self):
            if self._i >= len(self._data):
                raise StopAsyncIteration
            d = self._data[self._i]; self._i += 1; return d

    class FakeColl:
        def find(self, q, projection=None):
            # Filter docs to honour the test's q for league + date
            filt = []
            for d in docs:
                if d["league_id"] != q["league_id"]:
                    continue
                g = d["game_date"]
                if not (q["game_date"]["$gte"] <= g < q["game_date"]["$lt"]):
                    continue
                filt.append(d)
            return FakeCursor(filt)
    class FakeDB:
        def __getitem__(self, name):
            assert name == "sgo_player_stats"
            return FakeColl()

    idx = await _sgo_prior_logs_for_window(
        FakeDB(), league="MLB", replay_date="2025-05-01", lookback_days=60,
    )
    norm = normalize_player_name("José Ramírez")
    assert norm in idx, f"normalized key not found, got: {list(idx.keys())}"
    logs = idx[norm]
    # Only the two MLB-in-window rows survive
    assert len(logs) == 2
    # Newest-first
    assert logs[0]["date"] == "2025-04-30"
    assert logs[1]["date"] == "2025-04-29"
    # Composite hrr computed: 2+1+3 = 6
    assert logs[0]["hits_runs_rbis"] == 6.0
    assert logs[1]["hits_runs_rbis"] == 2.0  # 1+0+1
    # Stats flattened
    assert logs[0]["hits"] == 2 and logs[0]["total_bases"] == 5

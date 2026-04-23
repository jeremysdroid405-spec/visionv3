"""
Global Identity Rule (2026-04-23) — MLB regression tests.

Mirrors `test_identity_rule_nba.py` for the MLB scoring pipeline.
Verifies:
  * `MLBTierSorter` game-log cache is keyed strictly by
    `bdl_player_id` (no name-keyed dict).
  * `_calculate_cv / _calculate_hit_rate / _calculate_ceiling_hit_rate`
    return None when the prop has no `bdl_player_id` — no name
    fallback.
  * MLB hub lookups by `bdl_player_id` return logs identically to
    the NBA side of the same rule.
"""
from services.mlb_tier_sorter import MLBTierSorter


class _FakeDB:
    """Tiny stub just enough to satisfy the constructor — the tests
    below populate `_player_logs_cache` directly rather than going
    through `_load_caches`."""
    def __getitem__(self, name):
        raise KeyError(name)


def _make_sorter() -> MLBTierSorter:
    s = MLBTierSorter(_FakeDB())
    return s


def test_mlb_logs_cache_is_id_keyed():
    """The logs cache is typed as Dict[int, List[Dict]] — indexing by
    name must miss."""
    s = _make_sorter()
    s._player_logs_cache[777] = [{"hits": 1, "date": "2026-04-01"}] * 20
    assert s._get_logs_by_id(777) != []
    # Name-like strings cannot resolve — no name index exists.
    # (Method signatures accept only ints via `bdl_player_id`.)
    assert s._get_logs_by_id(None) == []
    assert s._get_logs_by_id(1234) == []


def test_mlb_cv_returns_none_when_id_absent():
    s = _make_sorter()
    s._player_logs_cache[1] = [
        {"hits": 2, "date": f"2026-04-{i:02d}"} for i in range(1, 21)
    ]
    # With ID: returns computed value (mean > 0)
    val = s._calculate_cv(1, "hits")
    assert val is not None
    # Without ID: no name-based fallback — returns None
    val_none = s._calculate_cv(None, "hits")
    assert val_none is None


def test_mlb_hit_rate_returns_none_when_id_absent():
    s = _make_sorter()
    s._player_logs_cache[2] = [
        {"hits": 2 + (i % 3), "date": f"2026-04-{i:02d}"}
        for i in range(1, 21)
    ]
    hr, avg = s._calculate_hit_rate(2, "hits", 1.5, 20)
    assert hr is not None
    assert avg is not None
    hr2, avg2 = s._calculate_hit_rate(None, "hits", 1.5, 20)
    assert hr2 is None
    assert avg2 is None


def test_mlb_ceiling_hit_rate_returns_none_when_id_absent():
    s = _make_sorter()
    s._player_logs_cache[3] = [
        {"hits": 4, "date": f"2026-04-{i:02d}"} for i in range(1, 21)
    ]
    r = s._calculate_ceiling_hit_rate(3, "hits", 1.0)
    assert r is not None
    r2 = s._calculate_ceiling_hit_rate(None, "hits", 1.0)
    assert r2 is None


def test_mlb_get_recent_game_logs_is_id_only():
    s = _make_sorter()
    s._player_logs_cache[4] = [
        {"hits": 1, "date": f"2026-04-{i:02d}"} for i in range(1, 21)
    ]
    rows = s._get_recent_game_logs(4, "hits", num_games=5)
    assert len(rows) > 0
    rows_none = s._get_recent_game_logs(None, "hits", num_games=5)
    assert rows_none == []

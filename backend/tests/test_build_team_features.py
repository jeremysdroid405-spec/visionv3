"""Unit tests for build_team_features (Phase 2 — team rolling priors)."""
from __future__ import annotations
import sys
from typing import Any, Dict, List

sys.path.insert(0, "/app/backend")

import pytest

from scripts.sgo.build_team_features import (
    TeamGameRecord,
    TeamAsOfFeatures,
    aggregate_team_games,
    compute_team_as_of_features,
    assert_no_future_games,
    _rate,
    _mean,
    _std,
    _rest_days,
    _num,
    FEATURE_VERSION,
    build_features_for_sport,
)


# ───── primitive helpers ─────
def test_num():
    assert _num("5") == 5.0
    assert _num(7) == 7.0
    assert _num(None) is None
    assert _num("abc") is None
    assert _num(True) is None


def test_mean_std():
    assert _mean([]) is None
    assert _mean([10, 20, 30]) == 20.0
    assert _std([]) is None
    assert _std([5]) is None
    # σ of [4, 6] = 1.0
    assert abs(_std([4, 6]) - 1.0) < 1e-9


def test_rate():
    assert _rate([]) is None
    assert _rate([None, None]) is None
    assert _rate([True, False, True, None]) == pytest.approx(2 / 3)
    assert _rate([True, True]) == 1.0
    assert _rate([False, False]) == 0.0


def test_rest_days_basic():
    assert _rest_days("2024-09-01", "2024-09-08") == 7
    assert _rest_days(None, "2024-09-08") is None
    assert _rest_days("2024-09-01", "2024-09-01") == 0
    # Capped at 14
    assert _rest_days("2024-01-01", "2024-09-01") == 14
    # Negative (data error) → None
    assert _rest_days("2024-10-01", "2024-09-01") is None


# ───── aggregate_team_games ─────
def _outcome(event_id, game_date, home_away, hs, as_,
              market_category, hit, push=False, resolved=True):
    return {
        "event_id": event_id, "game_date": game_date,
        "home_away": home_away, "home_score_used": hs, "away_score_used": as_,
        "market_category": market_category, "hit": hit,
        "push": push, "outcome_resolved": resolved,
    }


class TestAggregate:
    def test_collapses_multiple_rows_per_event(self):
        rows = [
            # Game A: team is home, won 25-20
            _outcome("A", "2024-09-01", "home", 25, 20, "h2h",        True),
            _outcome("A", "2024-09-01", "home", 25, 20, "h2h",        True),  # dup
            _outcome("A", "2024-09-01", "home", 25, 20, "spread",     True),
            _outcome("A", "2024-09-01", "home", 25, 20, "spread",     False),  # diff book
            _outcome("A", "2024-09-01", "home", 25, 20, "game_total", True),
        ]
        games = aggregate_team_games(rows)
        assert len(games) == 1
        g = games[0]
        assert g.event_id == "A"
        assert g.is_home is True
        assert g.points_scored == 25.0
        assert g.points_allowed == 20.0
        assert g.won_h2h is True
        assert g.spread_outcomes == [True, False]
        assert g.ou_outcomes == [True]

    def test_away_team_scoring_inverted(self):
        rows = [_outcome("A", "2024-09-01", "away", 25, 20, "h2h", False)]
        g = aggregate_team_games(rows)[0]
        assert g.is_home is False
        assert g.points_scored == 20.0   # team scored 20 (was away)
        assert g.points_allowed == 25.0
        assert g.won_h2h is False

    def test_push_excluded_from_h2h(self):
        rows = [_outcome("A", "2024-09-01", "home", 20, 20,
                          "h2h", None, push=True)]
        g = aggregate_team_games(rows)[0]
        assert g.won_h2h is None     # h2h push → unknown

    def test_push_recorded_as_none_for_spread(self):
        rows = [_outcome("A", "2024-09-01", "home", 25, 20,
                          "spread", None, push=True)]
        g = aggregate_team_games(rows)[0]
        assert g.spread_outcomes == [None]

    def test_unresolved_rows_skipped(self):
        rows = [_outcome("A", "2024-09-01", "home", 25, 20,
                          "h2h", None, resolved=False)]
        g = aggregate_team_games(rows)[0]
        assert g.won_h2h is None

    def test_games_sorted_by_date(self):
        rows = [
            _outcome("LATER", "2024-09-08", "home", 30, 28, "h2h", True),
            _outcome("EARLY", "2024-09-01", "away", 20, 24, "h2h", True),
        ]
        games = aggregate_team_games(rows)
        assert [g.game_date for g in games] == ["2024-09-01", "2024-09-08"]


# ───── compute_team_as_of_features ─────
def _mk_game(event, date, is_home, scored, allowed,
              won, spread=None, ou=None):
    return TeamGameRecord(
        event_id=event, game_date=date, is_home=is_home,
        points_scored=scored, points_allowed=allowed, won_h2h=won,
        spread_outcomes=spread or [], ou_outcomes=ou or [],
    )


class TestComputeFeatures:
    def test_empty_history_returns_zero_sample(self):
        f = compute_team_as_of_features([], as_of_date="2024-09-08", sport="nba")
        assert f.sample_size == 0
        assert f.mu_points_scored is None
        assert f.win_rate_l10 is None
        assert f.feature_completeness == FEATURE_VERSION

    def test_as_of_excludes_future_and_current_date(self):
        games = [
            _mk_game("g1", "2024-09-01", True,  100, 90,  True),
            _mk_game("g2", "2024-09-08", False, 80,  85, False),  # as-of date
            _mk_game("g3", "2024-09-15", True,  90,  95, False),  # future
        ]
        f = compute_team_as_of_features(games, as_of_date="2024-09-08",
                                          sport="nba")
        # Only g1 counts
        assert f.sample_size == 1
        assert f.mu_points_scored == 100.0
        assert f.win_rate_season == 1.0

    def test_l5_l10_season_ladder(self):
        # 12 games — 8 wins, 4 losses
        games = []
        for i in range(12):
            won = i % 3 != 0    # pattern: W W L W W L W W L W W L → 8/12
            games.append(_mk_game(
                f"g{i:02d}", f"2024-09-{(i+1):02d}",
                is_home=(i % 2 == 0), scored=100 + i, allowed=95 + (i % 4),
                won=won))
        f = compute_team_as_of_features(games, as_of_date="2024-10-01",
                                          sport="nba")
        assert f.sample_size == 12
        # Season win rate: 8/12
        assert f.win_rate_season == pytest.approx(8 / 12, abs=1e-4)
        # L10 includes last 10 games (indices 2..11 = 10 games, of which W:?)
        # Wins in last 10: i=2..11, won = i%3!=0 → indices where True:
        # 2 No (2%3==2 True),3 No(3%3==0 → False), 4 True, 5 True, 6 False,
        # 7 True, 8 True, 9 False, 10 True, 11 True → 7 wins / 10
        assert f.win_rate_l10 == pytest.approx(0.7, abs=1e-4)
        # Avg scored season = mean(100..111) = 105.5
        assert f.avg_scored_season == pytest.approx(105.5, abs=1e-3)

    def test_home_away_splits(self):
        games = [
            _mk_game("h1", "2024-09-01", True, 100, 90, True),
            _mk_game("h2", "2024-09-03", True, 95, 100, False),
            _mk_game("a1", "2024-09-02", False, 80, 85, False),
            _mk_game("a2", "2024-09-04", False, 88, 80, True),
        ]
        f = compute_team_as_of_features(games, as_of_date="2024-10-01",
                                          sport="nba")
        assert f.home_win_rate == 0.5
        assert f.away_win_rate == 0.5

    def test_spread_and_ou_collected_from_l10(self):
        games = [_mk_game(f"g{i}", f"2024-09-{i+1:02d}", True, 100, 95, True,
                            spread=[True], ou=[False])
                  for i in range(5)]
        f = compute_team_as_of_features(games, as_of_date="2024-10-01",
                                          sport="nba")
        assert f.spread_cover_rate_l10 == 1.0
        assert f.ou_hit_rate_l10 == 0.0

    def test_tempo_l10_nba(self):
        games = [_mk_game(f"g{i}", f"2024-09-{i+1:02d}", True, 100, 110, True)
                  for i in range(5)]
        f = compute_team_as_of_features(games, as_of_date="2024-10-01",
                                          sport="nba")
        # tempo = (100+110)/2 = 105
        assert f.tempo_l10 == 105.0

    def test_run_trend_mlb_only(self):
        games = [_mk_game(f"g{i}", f"2024-07-{i+1:02d}", True, 5, 4, True)
                  for i in range(20)]
        # Add a hot L10 stretch of 8-runs each
        for i in range(20, 30):
            games.append(_mk_game(f"g{i}", f"2024-07-{i+1:02d}", True,
                                    8, 4, True))
        f_mlb = compute_team_as_of_features(games, as_of_date="2024-08-15",
                                              sport="mlb")
        # run_trend_l10 = L10 avg - season avg = 8 - (5*20+8*10)/30 = 8 - 6 = 2
        assert f_mlb.run_trend_l10 == pytest.approx(2.0, abs=1e-3)
        f_nba = compute_team_as_of_features(games, as_of_date="2024-08-15",
                                              sport="nba")
        assert f_nba.run_trend_l10 is None

    def test_rest_days_computed_from_last_prior(self):
        games = [
            _mk_game("g1", "2024-09-01", True, 100, 90, True),
            _mk_game("g2", "2024-09-04", False, 95, 100, False),
        ]
        f = compute_team_as_of_features(games, as_of_date="2024-09-10",
                                          sport="nba")
        # Last prior game = 2024-09-04 → 6 days rest
        assert f.rest_days == 6

    def test_no_leakage_in_season_subset(self):
        games = [_mk_game(f"g{i}", f"2024-09-{i+1:02d}", True, 100, 95, True)
                  for i in range(5)]
        prior = [g for g in games if g.game_date < "2024-09-03"]
        # Should not raise
        assert_no_future_games(prior, as_of_date="2024-09-03")

    def test_leakage_guard_raises(self):
        games = [_mk_game("g1", "2024-09-03", True, 100, 95, True)]
        with pytest.raises(RuntimeError):
            assert_no_future_games(games, as_of_date="2024-09-03")


# ───── orchestrator (with fake mongo) ─────
class _FakeCursor:
    def __init__(self, docs): self._docs = docs
    def batch_size(self, _n): return self
    def __aiter__(self):
        async def gen():
            for d in self._docs: yield d
        return gen()


class _FakeColl:
    def __init__(self):
        self._docs: List[Dict[str, Any]] = []
        self._updates: List[Dict[str, Any]] = []
    def set_docs(self, docs): self._docs = list(docs)
    def find(self, _match, projection=None): return _FakeCursor(self._docs)
    async def distinct(self, field, match):
        items = []
        for d in self._docs:
            if all(d.get(k) == v for k, v in match.items()):
                items.append(d.get(field))
        return sorted({i for i in items if i})
    async def create_index(self, *args, **kwargs):
        return None
    async def update_one(self, filt, update, upsert=False):
        class R:
            modified_count = 0
            upserted_id = 1
        self._updates.append({"filter": filt, "update": update,
                                "upsert": upsert})
        return R()


class _FakeDB:
    def __init__(self): self._colls: Dict[str, _FakeColl] = {}
    def __getitem__(self, n):
        return self._colls.setdefault(n, _FakeColl())


@pytest.mark.asyncio
async def test_orchestrator_dry_run_emits_no_writes():
    db = _FakeDB()
    src = db["team_historical_outcomes"]
    src.set_docs([
        _outcome("g1", "2024-09-01", "home", 100, 90,  "h2h", True),
        _outcome("g1", "2024-09-01", "home", 100, 90,  "spread", True),
        _outcome("g2", "2024-09-08", "away", 95,  100, "h2h", True),
    ] + [{"sport": "nba", "team_id": "nba_xyz",
           **_outcome(f"g{i}", f"2024-09-{i+1:02d}", "home", 100, 90, "h2h", True)}
          for i in range(2)]
    )
    # Set sport+team_id on all docs
    for d in src._docs:
        d.setdefault("sport", "nba")
        d.setdefault("team_id", "nba_xyz")
    r = await build_features_for_sport(
        db, sport="nba", dry_run=True, force=False)
    assert r["counters"]["teams_processed"] == 1
    assert r["counters"]["team_dates_emitted"] >= 1
    assert r["counters"]["feature_rows_written"] == 0
    assert db["team_model_features"]._updates == []


@pytest.mark.asyncio
async def test_orchestrator_live_writes_one_row_per_game_date():
    db = _FakeDB()
    src = db["team_historical_outcomes"]
    src.set_docs([
        {**_outcome("g1", "2024-09-01", "home", 100, 90, "h2h", True),
         "sport": "nba", "team_id": "nba_bos"},
        {**_outcome("g2", "2024-09-08", "away", 95, 100, "h2h", True),
         "sport": "nba", "team_id": "nba_bos"},
    ])
    r = await build_features_for_sport(
        db, sport="nba", dry_run=False, force=False)
    upd = db["team_model_features"]._updates
    assert r["counters"]["feature_rows_written"] == 2
    # Each upsert keyed by (sport, team_id, as_of_date)
    keys = {(u["filter"]["sport"], u["filter"]["team_id"],
              u["filter"]["as_of_date"]) for u in upd}
    assert keys == {("nba", "nba_bos", "2024-09-01"),
                     ("nba", "nba_bos", "2024-09-08")}
    # All upserts use $set with feature_completeness tagged
    for u in upd:
        assert u["upsert"] is True
        assert u["update"]["$set"]["feature_completeness"] == FEATURE_VERSION


@pytest.mark.asyncio
async def test_orchestrator_skips_virtual_game_team_id():
    db = _FakeDB()
    src = db["team_historical_outcomes"]
    src.set_docs([
        {**_outcome("g1", "2024-09-01", "home", 100, 90, "h2h", True),
         "sport": "mlb", "team_id": "game"},   # virtual placeholder
        {**_outcome("g1", "2024-09-01", "home", 100, 90, "h2h", True),
         "sport": "mlb", "team_id": "mlb_bos"},
    ])
    r = await build_features_for_sport(
        db, sport="mlb", dry_run=False, force=False)
    # Only mlb_bos should be processed; "game" is filtered out
    assert r["counters"]["teams_processed"] == 1

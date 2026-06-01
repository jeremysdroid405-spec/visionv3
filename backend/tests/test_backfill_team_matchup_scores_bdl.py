"""Unit tests for backfill_team_matchup_scores_bdl."""
from __future__ import annotations
import sys
from typing import Any, Dict, List

sys.path.insert(0, "/app/backend")

import pytest

from scripts.sgo.backfill_team_matchup_scores_bdl import (
    _num,
    normalize_team_name,
    commence_date_iso,
    derive_seasons_from_game_dates,
    extract_scores_from_bdl_game,
    build_event_index,
    pick_closest_game,
    _date_distance_days,
    _is_final,
    _is_score_present,
    backfill_sport,
    SPORT_CONFIG,
    SCORE_SOURCE_BY_SPORT,
)
SCORE_SOURCE = SCORE_SOURCE_BY_SPORT["nfl"]  # back-compat for NFL tests


# ───── _num ─────
@pytest.mark.parametrize("v,exp", [
    ("25", 25.0), ("0", 0.0), (27, 27.0), (3.5, 3.5),
    (None, None), ("", None), ("abc", None),
    (True, None), (False, None),
])
def test_num(v, exp):
    assert _num(v) == exp


# ───── normalize_team_name ─────
class TestNormalize:
    @pytest.mark.parametrize("inp,exp", [
        ("Kansas City Chiefs",  "kansascitychiefs"),
        ("Baltimore Ravens",    "baltimoreravens"),
        ("San Francisco 49ers", "sanfrancisco49ers"),
        ("  Tampa Bay Buccaneers  ", "tampabaybuccaneers"),
    ])
    def test_basic(self, inp, exp):
        assert normalize_team_name(inp) == exp

    def test_franchise_renames(self):
        assert (normalize_team_name("Washington Football Team")
                == normalize_team_name("Washington Commanders"))
        assert (normalize_team_name("Oakland Raiders")
                == normalize_team_name("Las Vegas Raiders"))
        assert (normalize_team_name("San Diego Chargers")
                == normalize_team_name("Los Angeles Chargers"))


# ───── commence_date_iso ─────
@pytest.mark.parametrize("inp,exp", [
    ("2024-09-06T00:20:00.000Z", "2024-09-06"),
    ("2024-02-11T23:30:00Z", "2024-02-11"),
    ("2025-12-28", "2025-12-28"),
    (None, None), ("", None), ("2025", None),
])
def test_commence_date(inp, exp):
    assert commence_date_iso(inp) == exp


# ───── derive_seasons_from_game_dates ─────
class TestDeriveSeasons:
    def test_regular_season_sep_dec(self):
        assert derive_seasons_from_game_dates(
            ["2024-09-06", "2024-12-22"]) == [2024]

    def test_super_bowl_in_feb_is_prior_season(self):
        # SB after the 2023 season = Feb 2024 → season=2023
        assert derive_seasons_from_game_dates(["2024-02-11"]) == [2023]

    def test_multi_year_span(self):
        # 2024 regular season (Sep–Dec) + 2024 SB (Feb 2025) → seasons {2024, 2023}
        dates = ["2024-09-06", "2024-12-22", "2025-02-09", "2024-02-11"]
        assert derive_seasons_from_game_dates(dates) == [2023, 2024]

    def test_hof_game_in_august(self):
        # HOF game in early Aug is part of next regular season
        assert derive_seasons_from_game_dates(["2025-08-01"]) == [2025]

    def test_ignores_garbage_inputs(self):
        assert derive_seasons_from_game_dates(
            ["not-a-date", None, "", "2024-09-06"]) == [2024]


# ───── extract_scores_from_bdl_game ─────
class TestExtract:
    def _game(self, home, away, hs, vs):
        return {
            "id": 1, "status": "Final",
            "home_team": {"full_name": home},
            "visitor_team": {"full_name": away},
            "home_team_score": hs,
            "visitor_team_score": vs,
        }

    def test_matched(self):
        g = self._game("Kansas City Chiefs", "Baltimore Ravens", 27, 20)
        hs, as_ = extract_scores_from_bdl_game(
            g,
            home_team_norm=normalize_team_name("Kansas City Chiefs"),
            away_team_norm=normalize_team_name("Baltimore Ravens"))
        assert hs == 27.0 and as_ == 20.0

    def test_reversed_home_away_swaps(self):
        # Our matchup says home=Chiefs/away=Ravens; BDL has it the
        # other way around (rare data quirk) — extract should still
        # return the scores aligned to OUR view.
        g = self._game("Baltimore Ravens", "Kansas City Chiefs", 20, 27)
        hs, as_ = extract_scores_from_bdl_game(
            g,
            home_team_norm=normalize_team_name("Kansas City Chiefs"),
            away_team_norm=normalize_team_name("Baltimore Ravens"))
        assert hs == 27.0 and as_ == 20.0

    def test_wrong_teams_returns_none(self):
        g = self._game("Buffalo Bills", "Miami Dolphins", 30, 10)
        hs, as_ = extract_scores_from_bdl_game(
            g,
            home_team_norm=normalize_team_name("Kansas City Chiefs"),
            away_team_norm=normalize_team_name("Baltimore Ravens"))
        assert hs is None and as_ is None

    def test_non_dict(self):
        assert extract_scores_from_bdl_game(
            None,
            home_team_norm="a", away_team_norm="b") == (None, None)


# ───── build_event_index ─────
class TestIndex:
    def test_primary_and_fallback(self):
        games = [{
            "id": 1, "status": "Final",
            "home_team": {"full_name": "Kansas City Chiefs"},
            "visitor_team": {"full_name": "Baltimore Ravens"},
            "date": "2024-09-06T00:20:00Z",
        }]
        p, f = build_event_index(games)
        h = normalize_team_name("Kansas City Chiefs")
        a = normalize_team_name("Baltimore Ravens")
        assert ("2024-09-06", h, a) in p
        assert (h, a) in f
        assert isinstance(f[(h, a)], list)
        assert len(f[(h, a)]) == 1

    def test_missing_date_only_fallback(self):
        games = [{
            "home_team": {"full_name": "Bills"},
            "visitor_team": {"full_name": "Dolphins"},
            "date": None,
        }]
        p, f = build_event_index(games)
        assert not p
        assert (normalize_team_name("Bills"),
                normalize_team_name("Dolphins")) in f

    def test_fallback_collects_multiple_meetings(self):
        # Same team pair meets in Sep and Dec — fallback keeps both
        games = [
            {"home_team": {"full_name": "Chiefs"},
             "visitor_team": {"full_name": "Ravens"},
             "date": "2024-09-06T00:20:00Z"},
            {"home_team": {"full_name": "Chiefs"},
             "visitor_team": {"full_name": "Ravens"},
             "date": "2025-09-06T00:20:00Z"},
        ]
        _, f = build_event_index(games)
        key = (normalize_team_name("Chiefs"),
                normalize_team_name("Ravens"))
        assert len(f[key]) == 2


class TestPickClosestGame:
    def test_picks_closest_within_window(self):
        games = [
            {"date": "2024-09-05T20:00:00Z", "id": "near"},
            {"date": "2025-09-06T20:00:00Z", "id": "far"},
        ]
        out = pick_closest_game(games, matchup_date="2024-09-06",
                                  max_days=2)
        assert out and out["id"] == "near"

    def test_rejects_when_all_outside_window(self):
        games = [
            {"date": "2024-08-01T00:00:00Z"},
            {"date": "2025-09-06T20:00:00Z"},
        ]
        assert pick_closest_game(
            games, matchup_date="2024-09-06", max_days=2) is None

    def test_empty_candidates(self):
        assert pick_closest_game(
            [], matchup_date="2024-09-06", max_days=2) is None

    def test_distance_zero_wins(self):
        games = [
            {"date": "2024-09-06T20:00:00Z", "id": "same_day"},
            {"date": "2024-09-07T20:00:00Z", "id": "next_day"},
        ]
        out = pick_closest_game(games, matchup_date="2024-09-06",
                                  max_days=2)
        assert out and out["id"] == "same_day"


def test_date_distance_days():
    assert _date_distance_days("2024-09-06", "2024-09-06") == 0
    assert _date_distance_days("2024-09-06", "2024-09-07") == 1
    assert _date_distance_days("2024-09-06", "2024-09-04") == 2
    assert _date_distance_days(None, "2024-09-06") is None
    assert _date_distance_days("not-a-date", "2024-09-06") is None


# ───── _is_final / _is_score_present ─────
def test_is_final():
    assert _is_final({"status": "Final"})
    assert _is_final({"status": "Final/OT"})
    assert not _is_final({"status": "In Progress"})
    assert not _is_final({"status": "Scheduled"})
    assert not _is_final({})


def test_is_score_present():
    assert _is_score_present({"home_score": 27, "away_score": 20})
    assert not _is_score_present({"home_score": 27, "away_score": None})
    assert not _is_score_present({})


# ───── orchestrator (with fake mongo + stub fetcher) ─────
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
        self._update_calls: List[Dict[str, Any]] = []
    def set_docs(self, docs): self._docs = list(docs)
    def find(self, _m, projection=None): return _FakeCursor(self._docs)
    async def count_documents(self, _m): return len(self._docs)
    async def distinct(self, field, _filter):
        return sorted({d.get(field) for d in self._docs if d.get(field)})
    async def update_one(self, _f, _u):
        class R: modified_count = 1
        self._update_calls.append({"filter": _f, "update": _u})
        return R()


class _FakeDB:
    def __init__(self): self._colls: Dict[str, _FakeColl] = {}
    def __getitem__(self, n):
        return self._colls.setdefault(n, _FakeColl())


def _stub_fetcher(games):
    async def _f(api_key, *, url=None, seasons, per_page=100,
                  rate_sleep_ms=0, timeout_s=30.0, max_429_retries=5):
        assert isinstance(seasons, list) and seasons
        return games
    return _f


_FINAL_GAME = {
    "id": 7001, "status": "Final", "season": 2024, "week": 1,
    "home_team": {"full_name": "Kansas City Chiefs"},
    "visitor_team": {"full_name": "Baltimore Ravens"},
    "date": "2024-09-06T00:20:00Z",
    "home_team_score": 27, "visitor_team_score": 20,
}


@pytest.mark.asyncio
async def test_dry_run_does_not_write():
    db = _FakeDB()
    coll = db["nfl_matchups"]
    coll.set_docs([{
        "event_id": "e1", "home_team_name": "Kansas City Chiefs",
        "away_team_name": "Baltimore Ravens",
        "game_date": "2024-09-06",
    }])
    r = await backfill_sport(
        db, sport="nfl", api_key="k",
        start="2024-09-01", end="2024-09-30",
        seasons=[2024], dry_run=True, force=False,
        max_events=100, fetcher=_stub_fetcher([_FINAL_GAME]))
    assert r["counters"]["scores_found"] == 1
    assert r["counters"]["matched_primary"] == 1
    assert r["counters"]["updated"] == 0
    assert coll._update_calls == []


@pytest.mark.asyncio
async def test_live_write():
    db = _FakeDB()
    coll = db["nfl_matchups"]
    coll.set_docs([{
        "event_id": "e1", "home_team_name": "Kansas City Chiefs",
        "away_team_name": "Baltimore Ravens",
        "game_date": "2024-09-06",
    }])
    r = await backfill_sport(
        db, sport="nfl", api_key="k",
        start="2024-09-01", end="2024-09-30",
        seasons=[2024], dry_run=False, force=False,
        max_events=100, fetcher=_stub_fetcher([_FINAL_GAME]))
    assert r["counters"]["updated"] == 1
    upd = coll._update_calls[0]["update"]
    assert upd["$set"]["home_score"] == 27.0
    assert upd["$set"]["away_score"] == 20.0
    assert upd["$set"]["score_source"] == SCORE_SOURCE
    assert upd["$set"]["final_score"] == {"home": 27.0, "away": 20.0}
    assert upd["$set"]["bdl_game_id"] == 7001


@pytest.mark.asyncio
async def test_idempotent_skip():
    db = _FakeDB()
    coll = db["nfl_matchups"]
    coll.set_docs([{
        "event_id": "e1", "home_team_name": "Kansas City Chiefs",
        "away_team_name": "Baltimore Ravens",
        "game_date": "2024-09-06",
        "home_score": 27, "away_score": 20,
    }])
    r = await backfill_sport(
        db, sport="nfl", api_key="k",
        start="2024-09-01", end="2024-09-30",
        seasons=[2024], dry_run=False, force=False,
        max_events=100, fetcher=_stub_fetcher([_FINAL_GAME]))
    assert r["counters"]["already_scored_skip"] == 1
    assert r["counters"]["updated"] == 0


@pytest.mark.asyncio
async def test_force_rewrites():
    db = _FakeDB()
    coll = db["nfl_matchups"]
    coll.set_docs([{
        "event_id": "e1", "home_team_name": "Kansas City Chiefs",
        "away_team_name": "Baltimore Ravens",
        "game_date": "2024-09-06",
        "home_score": 0, "away_score": 0,
    }])
    r = await backfill_sport(
        db, sport="nfl", api_key="k",
        start="2024-09-01", end="2024-09-30",
        seasons=[2024], dry_run=False, force=True,
        max_events=100, fetcher=_stub_fetcher([_FINAL_GAME]))
    assert r["counters"]["updated"] == 1


@pytest.mark.asyncio
async def test_no_match_increments_not_in_bdl():
    db = _FakeDB()
    coll = db["nfl_matchups"]
    coll.set_docs([{
        "event_id": "e1", "home_team_name": "Bills",
        "away_team_name": "Dolphins",
        "game_date": "2024-09-15",
    }])
    r = await backfill_sport(
        db, sport="nfl", api_key="k",
        start="2024-09-01", end="2024-09-30",
        seasons=[2024], dry_run=True, force=False,
        max_events=100, fetcher=_stub_fetcher([_FINAL_GAME]))
    assert r["counters"]["not_in_bdl"] == 1
    assert r["counters"]["scores_found"] == 0


@pytest.mark.asyncio
async def test_fallback_on_date_drift_within_window():
    """Date drift of 1 day is within the ±2-day fallback window."""
    db = _FakeDB()
    coll = db["nfl_matchups"]
    coll.set_docs([{
        "event_id": "e1", "home_team_name": "Kansas City Chiefs",
        "away_team_name": "Baltimore Ravens",
        "game_date": "2024-09-05",   # ours says Sep 5
    }])
    # BDL says Sep 6 (UTC start time)
    r = await backfill_sport(
        db, sport="nfl", api_key="k",
        start="2024-09-01", end="2024-09-30",
        seasons=[2024], dry_run=True, force=False,
        max_events=100, fetcher=_stub_fetcher([_FINAL_GAME]))
    assert r["counters"]["matched_fallback"] == 1
    assert r["counters"]["matched_primary"] == 0
    assert r["counters"]["scores_found"] == 1


@pytest.mark.asyncio
async def test_fallback_rejects_wrong_season_rematch():
    """Regression: a 2024-08 preseason matchup should NOT silently
    match a 2025-08 rematch on the same team pair. ±2-day window
    must guard against the cross-season collision."""
    db = _FakeDB()
    coll = db["nfl_matchups"]
    coll.set_docs([{
        "event_id": "e_preseason_2024",
        "home_team_name": "Kansas City Chiefs",
        "away_team_name": "Baltimore Ravens",
        "game_date": "2024-08-08",
    }])
    # Only a 2025 game exists in BDL feed
    rematch_2025 = dict(_FINAL_GAME, date="2025-09-06T20:00:00Z", season=2025)
    r = await backfill_sport(
        db, sport="nfl", api_key="k",
        start="2024-08-01", end="2024-08-31",
        seasons=[2024], dry_run=True, force=False,
        max_events=100, fetcher=_stub_fetcher([rematch_2025]))
    assert r["counters"]["matched_fallback"] == 0
    assert r["counters"]["matched_primary"] == 0
    assert r["counters"]["not_in_bdl"] == 1
    assert r["counters"]["scores_found"] == 0


@pytest.mark.asyncio
async def test_non_final_games_filtered_out():
    db = _FakeDB()
    coll = db["nfl_matchups"]
    coll.set_docs([{
        "event_id": "e1", "home_team_name": "Kansas City Chiefs",
        "away_team_name": "Baltimore Ravens",
        "game_date": "2024-09-06",
    }])
    in_progress = dict(_FINAL_GAME, status="In Progress")
    r = await backfill_sport(
        db, sport="nfl", api_key="k",
        start="2024-09-01", end="2024-09-30",
        seasons=[2024], dry_run=True, force=False,
        max_events=100, fetcher=_stub_fetcher([in_progress]))
    assert r["counters"]["not_in_bdl"] == 1
    assert r["counters"]["scores_found"] == 0


@pytest.mark.asyncio
async def test_no_team_names_counter():
    db = _FakeDB()
    coll = db["nfl_matchups"]
    coll.set_docs([{
        "event_id": "e1", "home_team_name": None, "away_team_name": None,
        "game_date": "2024-09-06",
    }])
    r = await backfill_sport(
        db, sport="nfl", api_key="k",
        start="2024-09-01", end="2024-09-30",
        seasons=[2024], dry_run=True, force=False,
        max_events=100, fetcher=_stub_fetcher([_FINAL_GAME]))
    assert r["counters"]["no_team_names"] == 1


@pytest.mark.asyncio
async def test_seasons_auto_derived_from_matchups():
    """When --seasons is omitted, the orchestrator derives them from
    the distinct game_dates in the matchups collection."""
    db = _FakeDB()
    coll = db["nfl_matchups"]
    coll.set_docs([
        {"event_id": "e1", "home_team_name": "Kansas City Chiefs",
         "away_team_name": "Baltimore Ravens", "game_date": "2024-09-06"},
        {"event_id": "e2", "home_team_name": "Kansas City Chiefs",
         "away_team_name": "Baltimore Ravens", "game_date": "2025-02-09"},
    ])
    seen_seasons: List[List[int]] = []
    async def stub(api_key, *, url=None, seasons, per_page=100,
                    rate_sleep_ms=0, timeout_s=30.0, max_429_retries=5):
        seen_seasons.append(list(seasons))
        return []
    r = await backfill_sport(
        db, sport="nfl", api_key="k",
        start=None, end=None, seasons=None,
        dry_run=True, force=False, max_events=100, fetcher=stub)
    # Expect seasons derived: 2024 (Sep) + 2024 (Feb 2025 = SB of 2024 season)
    assert seen_seasons, "fetcher should have been called"
    assert sorted(seen_seasons[0]) == [2024]
    assert r["counters"]["scanned"] == 2


def test_sport_config():
    # Backwards-compat: SPORT_CONFIG maps sport → matchups collection.
    assert SPORT_CONFIG == {
        "nfl": "nfl_matchups",
        "mlb": "team_matchups",
        "nba": "team_matchups",
    }


# ───── MLB/NBA-specific tests ─────
_MLB_FINAL_GAME = {
    "id": 5046422, "status": "STATUS_FINAL", "season": 2024,
    "home_team": {"display_name": "San Diego Padres"},
    "away_team":  {"display_name": "Los Angeles Dodgers"},
    "date": "2024-10-22T23:30:00.000Z",
    "home_team_data": {"runs": 6},
    "away_team_data": {"runs": 5},
}

_NBA_FINAL_GAME = {
    "id": 15907731, "status": "Final", "season": 2024,
    "home_team": {"full_name": "Brooklyn Nets"},
    "visitor_team": {"full_name": "Orlando Magic"},
    "date": "2024-12-01",   # NBA returns date-only sometimes
    "home_team_score": 92, "visitor_team_score": 100,
}


class TestMLBExtraction:
    def test_mlb_extract_uses_team_data_runs(self):
        from scripts.sgo.backfill_team_matchup_scores_bdl import (
            _mlb_score_extractor)
        hs, as_ = _mlb_score_extractor(_MLB_FINAL_GAME)
        assert hs == 6.0 and as_ == 5.0

    def test_mlb_is_final(self):
        from scripts.sgo.backfill_team_matchup_scores_bdl import _mlb_is_final
        assert _mlb_is_final({"status": "STATUS_FINAL"})
        assert _mlb_is_final({"status": "FINAL"})
        assert not _mlb_is_final({"status": "STATUS_IN_PROGRESS"})

    def test_mlb_extract_via_orchestrator_kwargs(self):
        # Verify extract_scores_from_bdl_game routes the MLB shape
        # correctly when given the MLB spec kwargs.
        from scripts.sgo.backfill_team_matchup_scores_bdl import (
            extract_scores_from_bdl_game, _mlb_score_extractor)
        hs, as_ = extract_scores_from_bdl_game(
            _MLB_FINAL_GAME,
            home_team_norm=normalize_team_name("San Diego Padres"),
            away_team_norm=normalize_team_name("Los Angeles Dodgers"),
            away_team_key="away_team",
            team_name_field="display_name",
            score_extractor=_mlb_score_extractor,
        )
        assert hs == 6.0 and as_ == 5.0


class TestNBAExtraction:
    def test_nba_extract_uses_top_level_scores(self):
        from scripts.sgo.backfill_team_matchup_scores_bdl import (
            _nba_score_extractor)
        hs, as_ = _nba_score_extractor(_NBA_FINAL_GAME)
        assert hs == 92.0 and as_ == 100.0


@pytest.mark.asyncio
async def test_mlb_orchestrator_end_to_end():
    db = _FakeDB()
    coll = db["team_matchups"]
    coll.set_docs([{
        "event_id": "mlb_e1",
        "home_team_name": "San Diego Padres",
        "away_team_name": "Los Angeles Dodgers",
        "game_date": "2024-10-22",
        "sport": "mlb",
    }])
    r = await backfill_sport(
        db, sport="mlb", api_key="k",
        start="2024-10-01", end="2024-10-31",
        seasons=[2024], dry_run=False, force=False,
        max_events=100, fetcher=_stub_fetcher([_MLB_FINAL_GAME]))
    assert r["counters"]["scores_found"] == 1
    assert r["counters"]["updated"] == 1
    upd = coll._update_calls[0]["update"]
    assert upd["$set"]["home_score"] == 6.0
    assert upd["$set"]["away_score"] == 5.0
    assert upd["$set"]["score_source"] == "bdl_mlb_games"


@pytest.mark.asyncio
async def test_nba_orchestrator_end_to_end():
    db = _FakeDB()
    coll = db["team_matchups"]
    coll.set_docs([{
        "event_id": "nba_e1",
        "home_team_name": "Brooklyn Nets",
        "away_team_name": "Orlando Magic",
        "game_date": "2024-12-01",
        "sport": "nba",
    }])
    r = await backfill_sport(
        db, sport="nba", api_key="k",
        start="2024-12-01", end="2024-12-31",
        seasons=[2024], dry_run=False, force=False,
        max_events=100, fetcher=_stub_fetcher([_NBA_FINAL_GAME]))
    assert r["counters"]["scores_found"] == 1
    assert r["counters"]["updated"] == 1
    upd = coll._update_calls[0]["update"]
    assert upd["$set"]["home_score"] == 92.0
    assert upd["$set"]["away_score"] == 100.0
    assert upd["$set"]["score_source"] == "bdl_nba_games"


def test_calendar_year_seasons_deriver():
    from scripts.sgo.backfill_team_matchup_scores_bdl import (
        derive_calendar_year_seasons)
    # Each date contributes year Y and Y-1 (NBA-tolerant)
    assert derive_calendar_year_seasons(["2024-12-01"]) == [2023, 2024]
    assert derive_calendar_year_seasons(["2024-03-01", "2024-12-01"]) == [2023, 2024]

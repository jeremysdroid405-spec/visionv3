"""Unit tests for backfill_team_matchup_scores_oddsapi.

Focused on PURE helpers + the orchestrator's counter logic (no
real network, no real Mongo). The HTTP fetcher is monkeypatched via
the `fetcher=` kwarg on `backfill_sport`.
"""
from __future__ import annotations
import os
import sys
import asyncio
from typing import Any, Dict, List, Optional

sys.path.insert(0, "/app/backend")

import pytest

from scripts.sgo.backfill_team_matchup_scores_oddsapi import (
    _num,
    normalize_team_name,
    commence_date_iso,
    extract_scores_from_odds_event,
    build_event_index,
    _completed,
    _is_score_present,
    backfill_sport,
    SPORT_CONFIG,
    SCORE_SOURCE,
)


# ───── _num ─────
class TestNum:
    @pytest.mark.parametrize("v,exp", [
        ("25", 25.0),
        ("0", 0.0),
        (3, 3.0),
        (3.5, 3.5),
        (None, None),
        ("", None),
        ("abc", None),
        ([], None),
        ({}, None),
        (True, None),    # bool intentionally rejected — score "True" makes no sense
        (False, None),
    ])
    def test_parses_or_returns_none(self, v, exp):
        assert _num(v) == exp


# ───── normalize_team_name ─────
class TestNormalize:
    @pytest.mark.parametrize("inp,exp", [
        ("Kansas City Chiefs",     "kansascitychiefs"),
        ("San Francisco 49ers",    "sanfrancisco49ers"),
        ("kansas city chiefs",     "kansascitychiefs"),
        ("New York Jets",          "newyorkjets"),
        ("  Tampa Bay Buccaneers  ", "tampabaybuccaneers"),
    ])
    def test_basic_normalization(self, inp, exp):
        assert normalize_team_name(inp) == exp

    def test_franchise_renames_collapse(self):
        # Washington Football Team & Commanders should map together
        assert (normalize_team_name("Washington Football Team")
                == normalize_team_name("Washington Commanders"))
        assert (normalize_team_name("Oakland Raiders")
                == normalize_team_name("Las Vegas Raiders"))

    @pytest.mark.parametrize("v", [None, "", 42, [], {}])
    def test_safe_on_bad_input(self, v):
        assert normalize_team_name(v) == ""


# ───── commence_date_iso ─────
class TestCommenceDate:
    @pytest.mark.parametrize("inp,exp", [
        ("2025-09-21T17:00:00Z", "2025-09-21"),
        ("2024-02-11T23:30:00.000Z", "2024-02-11"),
        ("2025-12-28", "2025-12-28"),
        (None, None),
        ("", None),
        ("2025", None),
    ])
    def test_extracts_iso_date(self, inp, exp):
        assert commence_date_iso(inp) == exp


# ───── extract_scores_from_odds_event ─────
class TestExtractScores:
    def _ev(self, home, away, hs, as_, completed=True):
        return {
            "id": "x", "completed": completed,
            "home_team": home, "away_team": away,
            "scores": [
                {"name": home, "score": str(hs)},
                {"name": away, "score": str(as_)},
            ],
        }

    def test_simple_extract(self):
        ev = self._ev("Kansas City Chiefs", "San Francisco 49ers", 25, 22)
        hs, as_ = extract_scores_from_odds_event(
            ev,
            home_team=normalize_team_name("Kansas City Chiefs"),
            away_team=normalize_team_name("San Francisco 49ers"))
        assert hs == 25.0 and as_ == 22.0

    def test_order_independent(self):
        # API may list away first; match by name not position
        ev = {
            "scores": [
                {"name": "San Francisco 49ers", "score": "22"},
                {"name": "Kansas City Chiefs",  "score": "25"},
            ],
        }
        hs, as_ = extract_scores_from_odds_event(
            ev,
            home_team=normalize_team_name("Kansas City Chiefs"),
            away_team=normalize_team_name("San Francisco 49ers"))
        assert hs == 25.0 and as_ == 22.0

    def test_missing_scores_returns_none(self):
        hs, as_ = extract_scores_from_odds_event(
            {"scores": None},
            home_team="kansascitychiefs", away_team="sanfrancisco49ers")
        assert hs is None and as_ is None

    def test_one_team_missing(self):
        ev = {"scores": [{"name": "Kansas City Chiefs", "score": "25"}]}
        hs, as_ = extract_scores_from_odds_event(
            ev,
            home_team=normalize_team_name("Kansas City Chiefs"),
            away_team=normalize_team_name("San Francisco 49ers"))
        assert hs == 25.0 and as_ is None

    def test_non_dict_event(self):
        assert extract_scores_from_odds_event(
            None, home_team="a", away_team="b") == (None, None)


# ───── build_event_index ─────
class TestBuildIndex:
    def test_both_indices_populated(self):
        events = [{
            "home_team": "Kansas City Chiefs",
            "away_team": "San Francisco 49ers",
            "commence_time": "2024-02-11T23:30:00Z",
            "completed": True,
            "scores": [],
        }]
        primary, fallback = build_event_index(events)
        h = normalize_team_name("Kansas City Chiefs")
        a = normalize_team_name("San Francisco 49ers")
        assert ("2024-02-11", h, a) in primary
        assert (h, a) in fallback

    def test_missing_date_only_falls_back(self):
        events = [{
            "home_team": "A", "away_team": "B",
            "commence_time": None, "completed": True, "scores": [],
        }]
        primary, fallback = build_event_index(events)
        assert not primary
        assert ("a", "b") in fallback

    def test_skips_blank_teams(self):
        events = [{"home_team": "", "away_team": "B",
                     "commence_time": "2024-01-01T00:00:00Z"}]
        primary, fallback = build_event_index(events)
        assert not primary and not fallback


# ───── _completed / _is_score_present ─────
class TestPredicates:
    def test_completed(self):
        assert _completed({"completed": True})
        assert not _completed({"completed": False})
        assert not _completed({})

    def test_is_score_present(self):
        assert _is_score_present({"home_score": 25, "away_score": 22})
        assert not _is_score_present({"home_score": 25, "away_score": None})
        assert not _is_score_present({})


# ───── orchestrator (with fake Mongo + fake fetcher) ─────
class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs
    def batch_size(self, _n):  # no-op for tests
        return self
    def __aiter__(self):
        async def gen():
            for d in self._docs:
                yield d
        return gen()


class _FakeAggResult:
    def __init__(self, payload): self._payload = payload
    async def to_list(self, length=None): return self._payload


class _FakeColl:
    def __init__(self):
        self._docs: List[Dict[str, Any]] = []
        self._update_calls: List[Dict[str, Any]] = []
        self._agg_payload: List[Dict[str, Any]] = []
    def set_docs(self, docs):
        self._docs = list(docs)
    def find(self, _match, projection=None):
        # Return ALL docs; projection ignored for the test (we set the
        # fields directly). The orchestrator filter is sport+status etc.
        return _FakeCursor(self._docs)
    async def count_documents(self, _match):
        return len(self._docs)
    def aggregate(self, _pipeline):
        return _FakeAggResult(self._agg_payload)
    async def update_one(self, _filter, _update):
        class R:
            modified_count = 1
        self._update_calls.append({"filter": _filter, "update": _update})
        return R()


class _FakeDB:
    def __init__(self): self._colls: Dict[str, _FakeColl] = {}
    def __getitem__(self, name):
        return self._colls.setdefault(name, _FakeColl())


async def _stub_fetcher_factory(events):
    async def _stub(api_key, sport_key, *, days_back=3,
                     event_ids=None, timeout_s=30.0):
        assert days_back in (1, 2, 3)
        return events
    return _stub


@pytest.mark.asyncio
async def test_dry_run_does_not_write():
    db = _FakeDB()
    coll = db["nfl_matchups"]
    coll.set_docs([{
        "event_id": "e1", "home_team_name": "Kansas City Chiefs",
        "away_team_name": "San Francisco 49ers",
        "game_date": "2024-02-11",
        "commence_time": "2024-02-11T23:30:00Z",
    }])
    events = [{
        "id": "odd1",
        "home_team": "Kansas City Chiefs",
        "away_team": "San Francisco 49ers",
        "commence_time": "2024-02-11T23:30:00Z",
        "completed": True,
        "scores": [
            {"name": "Kansas City Chiefs", "score": "25"},
            {"name": "San Francisco 49ers", "score": "22"},
        ],
    }]
    stub = await _stub_fetcher_factory(events)
    r = await backfill_sport(
        db, sport="nfl", api_key="x", start="2024-02-01", end="2024-02-29",
        dry_run=True, force=False, days_back=3, max_events=100,
        fetcher=stub)
    assert r["counters"]["scores_found"] == 1
    assert r["counters"]["matched_primary"] == 1
    assert r["counters"]["updated"] == 0   # dry-run
    assert coll._update_calls == []


@pytest.mark.asyncio
async def test_live_writes_with_yes():
    db = _FakeDB()
    coll = db["nfl_matchups"]
    coll.set_docs([{
        "event_id": "e1", "home_team_name": "Kansas City Chiefs",
        "away_team_name": "San Francisco 49ers",
        "game_date": "2024-02-11",
        "commence_time": "2024-02-11T23:30:00Z",
    }])
    stub = await _stub_fetcher_factory([{
        "home_team": "Kansas City Chiefs",
        "away_team": "San Francisco 49ers",
        "commence_time": "2024-02-11T23:30:00Z",
        "completed": True,
        "scores": [
            {"name": "Kansas City Chiefs",  "score": "25"},
            {"name": "San Francisco 49ers", "score": "22"},
        ],
    }])
    r = await backfill_sport(
        db, sport="nfl", api_key="x", start="2024-02-01", end="2024-02-29",
        dry_run=False, force=False, days_back=3, max_events=100,
        fetcher=stub)
    assert r["counters"]["updated"] == 1
    assert len(coll._update_calls) == 1
    upd = coll._update_calls[0]["update"]
    assert upd["$set"]["home_score"] == 25.0
    assert upd["$set"]["away_score"] == 22.0
    assert upd["$set"]["score_source"] == SCORE_SOURCE
    assert upd["$set"]["final_score"] == {"home": 25.0, "away": 22.0}


@pytest.mark.asyncio
async def test_idempotent_skip_when_already_scored():
    db = _FakeDB()
    coll = db["nfl_matchups"]
    coll.set_docs([{
        "event_id": "e1", "home_team_name": "Kansas City Chiefs",
        "away_team_name": "San Francisco 49ers",
        "game_date": "2024-02-11",
        "commence_time": "2024-02-11T23:30:00Z",
        "home_score": 25, "away_score": 22,
    }])
    stub = await _stub_fetcher_factory([])
    r = await backfill_sport(
        db, sport="nfl", api_key="x", start="2024-02-01", end="2024-02-29",
        dry_run=False, force=False, days_back=3, max_events=100,
        fetcher=stub)
    assert r["counters"]["already_scored_skip"] == 1
    assert r["counters"]["updated"] == 0


@pytest.mark.asyncio
async def test_force_rewrites_existing_scores():
    db = _FakeDB()
    coll = db["nfl_matchups"]
    coll.set_docs([{
        "event_id": "e1", "home_team_name": "Kansas City Chiefs",
        "away_team_name": "San Francisco 49ers",
        "game_date": "2024-02-11",
        "commence_time": "2024-02-11T23:30:00Z",
        "home_score": 0, "away_score": 0,
    }])
    stub = await _stub_fetcher_factory([{
        "home_team": "Kansas City Chiefs",
        "away_team": "San Francisco 49ers",
        "commence_time": "2024-02-11T23:30:00Z",
        "completed": True,
        "scores": [
            {"name": "Kansas City Chiefs", "score": "25"},
            {"name": "San Francisco 49ers", "score": "22"},
        ],
    }])
    r = await backfill_sport(
        db, sport="nfl", api_key="x", start="2024-02-01", end="2024-02-29",
        dry_run=False, force=True, days_back=3, max_events=100,
        fetcher=stub)
    assert r["counters"]["already_scored_skip"] == 0
    assert r["counters"]["updated"] == 1


@pytest.mark.asyncio
async def test_not_in_window_counter():
    db = _FakeDB()
    coll = db["nfl_matchups"]
    coll.set_docs([{
        "event_id": "old1", "home_team_name": "Chicago Bears",
        "away_team_name": "Detroit Lions",
        "game_date": "2024-09-22",
        "commence_time": "2024-09-22T17:00:00Z",
    }])
    stub = await _stub_fetcher_factory([])  # API returned nothing
    r = await backfill_sport(
        db, sport="nfl", api_key="x", start="2024-09-01", end="2024-09-30",
        dry_run=True, force=False, days_back=3, max_events=100,
        fetcher=stub)
    assert r["counters"]["not_in_window"] == 1
    assert r["counters"]["scores_found"] == 0


@pytest.mark.asyncio
async def test_found_but_not_completed():
    db = _FakeDB()
    coll = db["nfl_matchups"]
    coll.set_docs([{
        "event_id": "e1", "home_team_name": "Kansas City Chiefs",
        "away_team_name": "San Francisco 49ers",
        "game_date": "2024-02-11",
        "commence_time": "2024-02-11T23:30:00Z",
    }])
    stub = await _stub_fetcher_factory([{
        "home_team": "Kansas City Chiefs",
        "away_team": "San Francisco 49ers",
        "commence_time": "2024-02-11T23:30:00Z",
        "completed": False,   # live, not final
        "scores": [
            {"name": "Kansas City Chiefs",  "score": "14"},
            {"name": "San Francisco 49ers", "score": "10"},
        ],
    }])
    r = await backfill_sport(
        db, sport="nfl", api_key="x", start="2024-02-01", end="2024-02-29",
        dry_run=True, force=False, days_back=3, max_events=100,
        fetcher=stub)
    assert r["counters"]["found_but_not_completed"] == 1
    assert r["counters"]["scores_found"] == 0
    assert r["counters"]["updated"] == 0


@pytest.mark.asyncio
async def test_fallback_match_when_date_mismatch():
    db = _FakeDB()
    coll = db["nfl_matchups"]
    # matchup says 2024-02-11; API says 2024-02-12 (timezone drift edge)
    coll.set_docs([{
        "event_id": "e1", "home_team_name": "Kansas City Chiefs",
        "away_team_name": "San Francisco 49ers",
        "game_date": "2024-02-11",
        "commence_time": "2024-02-11T23:30:00Z",
    }])
    stub = await _stub_fetcher_factory([{
        "home_team": "Kansas City Chiefs",
        "away_team": "San Francisco 49ers",
        "commence_time": "2024-02-12T03:30:00Z",   # next day UTC
        "completed": True,
        "scores": [
            {"name": "Kansas City Chiefs",  "score": "25"},
            {"name": "San Francisco 49ers", "score": "22"},
        ],
    }])
    r = await backfill_sport(
        db, sport="nfl", api_key="x", start="2024-02-01", end="2024-02-29",
        dry_run=True, force=False, days_back=3, max_events=100,
        fetcher=stub)
    assert r["counters"]["matched_fallback"] == 1
    assert r["counters"]["matched_primary"] == 0
    assert r["counters"]["scores_found"] == 1


@pytest.mark.asyncio
async def test_no_team_names_counter():
    db = _FakeDB()
    coll = db["nfl_matchups"]
    coll.set_docs([{
        "event_id": "e1", "home_team_name": None, "away_team_name": None,
        "game_date": "2024-02-11",
    }])
    stub = await _stub_fetcher_factory([])
    r = await backfill_sport(
        db, sport="nfl", api_key="x", start="2024-02-01", end="2024-02-29",
        dry_run=True, force=False, days_back=3, max_events=100,
        fetcher=stub)
    assert r["counters"]["no_team_names"] == 1


@pytest.mark.asyncio
async def test_sport_config_contracts():
    assert SPORT_CONFIG["nfl"] == ("nfl_matchups",  "americanfootball_nfl")
    assert SPORT_CONFIG["mlb"] == ("team_matchups", "baseball_mlb")
    assert SPORT_CONFIG["nba"] == ("team_matchups", "basketball_nba")

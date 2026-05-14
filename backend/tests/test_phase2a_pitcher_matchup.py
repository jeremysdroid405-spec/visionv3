"""Phase 2A MLB pitcher-matchup wiring tests (2026-05-15).

Covers four guarantees:

1. ``services/mlb_probable_pitcher.py`` parses the MLB Stats API
   schedule + people responses into a ``ProbablePitcherIndex`` keyed
   on (HOME_ABBR, AWAY_ABBR).

2. ``services/feature_hydration.hydrate_game_context_on_props``
   uses the index to populate the seven canonical pitcher fields on
   MLB props (id, name, throws, era, whip, k9, probable_pitcher) and
   sets them to ``None`` (with imputed flag) when no data is present.

3. ``services/scoring/adapters/mlb_scoring._propagate_phase1_context``
   derives ``same_hand_matchup`` / ``opposite_hand_matchup`` from
   ``batter_hand`` × ``opp_pitcher_throws`` (switch-hitters always
   face the opposite hand).

4. ``services/scoring/prop_scores_store._SCORE_OUTPUT_FIELDS`` and
   ``services/scoring/recompute`` allowlists carry all Phase 2A
   field names so they survive the score-doc projection step.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from services import feature_hydration as fh
from services import mlb_probable_pitcher as mpp
from services.scoring.adapters.mlb_scoring import _propagate_phase1_context
from services.scoring.prop_scores_store import _SCORE_OUTPUT_FIELDS


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────
SCHEDULE_FIXTURE = {
    "dates": [{
        "games": [{
            "teams": {
                "home": {
                    "team": {"abbreviation": "LAD", "teamName": "Dodgers"},
                    "probablePitcher": {
                        "id": 605400,
                        "fullName": "Yoshinobu Yamamoto",
                        "pitchHand": {"code": "R"},
                    },
                },
                "away": {
                    "team": {"abbreviation": "SF", "teamName": "Giants"},
                    "probablePitcher": {
                        "id": 656302,
                        "fullName": "Logan Webb",
                        "pitchHand": {"code": "R"},
                    },
                },
            },
        }],
    }],
}

# Pitcher stats fixture — keyed by pid for the people-endpoint mock.
PEOPLE_FIXTURE = {
    605400: {  # Yamamoto
        "people": [{
            "pitchHand": {"code": "R"},
            "stats": [{"splits": [{"stat": {
                "era": "2.70", "whip": "1.05",
                "strikeoutsPer9Inn": "10.20",
            }}]}],
        }],
    },
    656302: {  # Webb
        "people": [{
            "pitchHand": {"code": "R"},
            "stats": [{"splits": [{"stat": {
                "era": "3.45", "whip": "1.20",
                "strikeoutsPer9Inn": "8.40",
            }}]}],
        }],
    },
}


class _DummyResp:
    def __init__(self, payload):
        self._payload = payload
    def raise_for_status(self): pass  # noqa: E704
    def json(self): return self._payload  # noqa: E704


class _DummyClient:
    def __init__(self, *_a, **_k): pass  # noqa: E704
    async def __aenter__(self): return self  # noqa: E704
    async def __aexit__(self, *exc): pass  # noqa: E704
    async def get(self, url, params=None):
        if "/schedule" in url:
            return _DummyResp(SCHEDULE_FIXTURE)
        # /people/{pid} — extract pid from the trailing segment.
        try:
            pid = int(url.rstrip("/").rsplit("/", 1)[-1])
        except Exception:
            pid = None
        return _DummyResp(PEOPLE_FIXTURE.get(pid, {"people": []}))


# ─────────────────────────────────────────────────────────────────────
# 1. ProbablePitcherIndex builder
# ─────────────────────────────────────────────────────────────────────
def test_index_builder_parses_schedule_and_stats():
    mpp.reset_cache()
    with patch.object(mpp.httpx, "AsyncClient", _DummyClient):
        idx = asyncio.run(mpp.build_probable_pitcher_index("2026-05-15"))
    pair = idx.get("LAD", "SF")
    assert pair is not None
    home, away = pair["home"], pair["away"]
    assert home["id"] == 605400 and home["name"] == "Yoshinobu Yamamoto"
    assert home["throws"] == "R"
    assert home["era"] == 2.70 and home["whip"] == 1.05 and home["k9"] == 10.20
    assert away["id"] == 656302 and away["name"] == "Logan Webb"
    assert away["throws"] == "R"
    assert away["era"] == 3.45 and away["whip"] == 1.20 and away["k9"] == 8.40


def test_index_lookup_returns_none_on_missing_pair():
    idx = mpp.ProbablePitcherIndex()
    assert idx.get(None, None) is None
    assert idx.get("NYY", "BOS") is None
    assert len(idx) == 0


def test_index_get_is_case_insensitive():
    idx = mpp.ProbablePitcherIndex()
    idx._by_pair[("LAD", "SF")] = {"home": {}, "away": {}}
    assert idx.get("lad", "sf") is not None
    assert idx.get("LAD", "SF") is not None


# ─────────────────────────────────────────────────────────────────────
# 2. commence_date_iso helper
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("ct,expected", [
    ("2026-05-15T19:10:00Z", "2026-05-15"),
    ("2026-05-15", "2026-05-15"),
    (datetime(2026, 5, 15, 19, 10, tzinfo=timezone.utc), "2026-05-15"),
    (None, None),
    ("", None),
    ("not-a-date", None),
])
def test_commence_date_iso(ct, expected):
    assert fh._commence_date_iso(ct) == expected


# ─────────────────────────────────────────────────────────────────────
# 3. Matchup-flag derivation in MLB adapter
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("bh,ph,exp_same,exp_opp", [
    ("L", "L", 1, 0),
    ("R", "R", 1, 0),
    ("L", "R", 0, 1),
    ("R", "L", 0, 1),
    ("S", "L", 0, 1),     # switch always opposite
    ("S", "R", 0, 1),
    (None, "L", None, None),
    ("L", None, None, None),
    ("X", "L", None, None),
])
def test_matchup_flags(bh, ph, exp_same, exp_opp):
    prop: Dict[str, Any] = {
        "batter_hand": bh,
        "opp_pitcher_throws": ph,
    }
    _propagate_phase1_context(prop, master_hub=None, bdl_player_id=None)
    assert prop.get("same_hand_matchup") == exp_same
    assert prop.get("opposite_hand_matchup") == exp_opp


def test_matchup_flag_does_not_clobber_existing_batter_hand():
    """When the adapter already has batter_hand stamped, the flag
    derivation reads from the prop, not the master_hub fallback."""
    prop = {"batter_hand": "L", "opp_pitcher_throws": "R"}
    _propagate_phase1_context(prop, master_hub=None, bdl_player_id=None)
    assert prop["batter_hand"] == "L"
    assert prop["same_hand_matchup"] == 0
    assert prop["opposite_hand_matchup"] == 1


# ─────────────────────────────────────────────────────────────────────
# 4. Score-doc allowlist contains all Phase 2A fields
# ─────────────────────────────────────────────────────────────────────
PHASE_2A_FIELDS = (
    "opp_pitcher_id", "opp_pitcher_name", "opp_pitcher_throws",
    "probable_pitcher",
    "opp_pitcher_era", "opp_pitcher_whip", "opp_pitcher_k9",
    "same_hand_matchup", "opposite_hand_matchup",
)


def test_score_output_fields_contains_phase2a():
    for f in PHASE_2A_FIELDS:
        assert f in _SCORE_OUTPUT_FIELDS, (
            f"Phase 2A field {f!r} missing from "
            f"prop_scores_store._SCORE_OUTPUT_FIELDS allowlist"
        )


def test_recompute_mirror_contains_phase2a():
    import inspect
    from services.scoring import recompute
    src = inspect.getsource(recompute)
    for f in PHASE_2A_FIELDS:
        assert f'"{f}"' in src, (
            f"Phase 2A field {f!r} missing from recompute.py "
            f"score-doc mirror allowlist"
        )


def test_score_document_schema_declares_phase2a():
    from services.scoring.score_document_schema import ScoreDocument
    fields = ScoreDocument.model_fields if hasattr(
        ScoreDocument, "model_fields") else ScoreDocument.__fields__
    for f in PHASE_2A_FIELDS:
        assert f in fields, (
            f"Phase 2A field {f!r} missing from ScoreDocument schema"
        )


# ─────────────────────────────────────────────────────────────────────
# 5. Feature hydration end-to-end (mocked Mongo + mocked pitcher fetch)
# ─────────────────────────────────────────────────────────────────────
class _AsyncCursor:
    def __init__(self, docs): self._docs = list(docs)  # noqa: E704
    def __aiter__(self): return self  # noqa: E704
    async def __anext__(self):
        if not self._docs:
            raise StopAsyncIteration
        return self._docs.pop(0)


class _Coll:
    def __init__(self, docs=None): self._docs = docs or []  # noqa: E704
    def find(self, *a, **k):
        return _AsyncCursor(self._docs)


class _DB(dict):
    def __getitem__(self, k):
        return self.get(k) or _Coll([])


def test_feature_hydration_populates_pitcher_fields():
    """End-to-end: a single MLB prop hydrated against a mocked Mongo
    and the dummy MLB Stats API client must end up carrying real
    pitcher fields and a derived matchup-eligible state."""
    mpp.reset_cache()

    # Minimal master hub: one player on LAD + Giants alias row so
    # `_build_team_alias_map` can resolve both home and away teams.
    hub_docs = [
        {
            "bdl_player_id": 1001,
            "team_abbr": "LAD", "team_name": "Los Angeles Dodgers",
        },
        {
            "bdl_player_id": 1002,
            "team_abbr": "SF", "team_name": "San Francisco Giants",
        },
    ]

    db = _DB({
        "mlb_master_hub_2026": _Coll(hub_docs),
        "dg_raw_odds_markets": _Coll([]),
        "live_injuries": _Coll([]),
        "mlb_projected_lineups": _Coll([]),
    })

    props: List[Dict[str, Any]] = [{
        "bdl_player_id": 1001,
        "player_name": "Test Hitter",
        "event_id": "evt-1",
        "home_team": "Los Angeles Dodgers",
        "away_team": "San Francisco Giants",
        "commence_time": "2026-05-15T19:10:00Z",
    }]

    # Patch the heavy-lifters we don't want running for this unit.
    with patch.object(fh, "_build_last_game_date_map",
                      new=AsyncMock(return_value={})), \
         patch.object(fh, "_build_player_minutes_usage_map",
                      new=AsyncMock(return_value={})), \
         patch.object(fh, "_build_vegas_totals_map",
                      new=AsyncMock(return_value=({}, {}))), \
         patch.object(fh, "_build_injury_summary",
                      new=AsyncMock(return_value={})), \
         patch("services.mlb_lineups_loader.load_slot_map",
               new=AsyncMock(return_value={})), \
         patch("services.mlb_lineups_loader.lookup_slot",
               new=MagicMock(return_value=(None, False, None))), \
         patch.object(mpp.httpx, "AsyncClient", _DummyClient):

        report = asyncio.run(
            fh.hydrate_game_context_on_props(db, "mlb", props)
        )

    p = props[0]
    # Identity resolved → home batter (LAD = home_team).
    assert p["team"] == "LAD"
    assert p["is_home_team"] == 1
    # Opposing pitcher = Logan Webb (SF away starter).
    assert p["opp_pitcher_id"] == 656302
    assert p["opp_pitcher_name"] == "Logan Webb"
    assert p["opp_pitcher_throws"] == "R"
    assert p["probable_pitcher"] == "Logan Webb"
    assert p["opp_pitcher_era"] == 3.45
    assert p["opp_pitcher_whip"] == 1.20
    assert p["opp_pitcher_k9"] == 8.40
    # Counter exposed on the coverage report.
    assert report.get("probable_pitcher_filled") == 1
    # Imputed list no longer carries `probable_pitcher` since it's set.
    assert "probable_pitcher" not in p["context_imputed_fields"]
    assert "opp_pitcher_throws" not in p["context_imputed_fields"]


def test_feature_hydration_marks_pitcher_imputed_when_api_fails():
    """If the MLB Stats API returns no game, the four pitcher fields
    remain None and the imputed list includes them."""
    mpp.reset_cache()

    hub_docs = [
        {"bdl_player_id": 1001, "team_abbr": "NYY",
         "team_name": "New York Yankees"},
        {"bdl_player_id": 1002, "team_abbr": "BOS",
         "team_name": "Boston Red Sox"},
    ]
    db = _DB({
        "mlb_master_hub_2026": _Coll(hub_docs),
        "dg_raw_odds_markets": _Coll([]),
        "live_injuries": _Coll([]),
        "mlb_projected_lineups": _Coll([]),
    })

    props: List[Dict[str, Any]] = [{
        "bdl_player_id": 1001, "player_name": "Test Hitter",
        "event_id": "evt-2",
        "home_team": "New York Yankees",
        "away_team": "Boston Red Sox",
        "commence_time": "2026-05-15T19:10:00Z",
    }]

    # Empty schedule fixture → no games.
    class _EmptyClient(_DummyClient):
        async def get(self, url, params=None):
            return _DummyResp({"dates": []})

    with patch.object(fh, "_build_last_game_date_map",
                      new=AsyncMock(return_value={})), \
         patch.object(fh, "_build_player_minutes_usage_map",
                      new=AsyncMock(return_value={})), \
         patch.object(fh, "_build_vegas_totals_map",
                      new=AsyncMock(return_value=({}, {}))), \
         patch.object(fh, "_build_injury_summary",
                      new=AsyncMock(return_value={})), \
         patch("services.mlb_lineups_loader.load_slot_map",
               new=AsyncMock(return_value={})), \
         patch("services.mlb_lineups_loader.lookup_slot",
               new=MagicMock(return_value=(None, False, None))), \
         patch.object(mpp.httpx, "AsyncClient", _EmptyClient):

        asyncio.run(fh.hydrate_game_context_on_props(db, "mlb", props))

    p = props[0]
    assert p["opp_pitcher_id"] is None
    assert p["opp_pitcher_name"] is None
    assert p["opp_pitcher_throws"] is None
    assert p["probable_pitcher"] is None
    assert "probable_pitcher" in p["context_imputed_fields"]
    assert "opp_pitcher_throws" in p["context_imputed_fields"]


# ─────────────────────────────────────────────────────────────────────
# 6. _fetch_pitcher_stats handles missing / malformed payloads
# ─────────────────────────────────────────────────────────────────────
def test_fetch_pitcher_stats_handles_empty_people():
    class _NoPeopleClient(_DummyClient):
        async def get(self, url, params=None):
            return _DummyResp({"people": []})
    with patch.object(mpp.httpx, "AsyncClient", _NoPeopleClient):
        result = asyncio.run(mpp._fetch_pitcher_stats(999, 2026))
    assert result == {
        "throws": None, "era": None, "whip": None, "k9": None,
    }


def test_fetch_pitcher_stats_floats_string_inputs():
    class _ClientReturns(_DummyClient):
        async def get(self, url, params=None):
            return _DummyResp({"people": [{
                "pitchHand": {"code": "L", "description": "Left"},
                "stats": [{"splits": [{"stat": {
                    "era": "1.93", "whip": "0.86",
                    "strikeoutsPer9Inn": "13.5",
                }}]}],
            }]})
    with patch.object(mpp.httpx, "AsyncClient", _ClientReturns):
        result = asyncio.run(mpp._fetch_pitcher_stats(123, 2026))
    assert result == {
        "throws": "L", "era": 1.93, "whip": 0.86, "k9": 13.5,
    }


def test_index_builder_falls_back_to_people_throws():
    """When the schedule omits `pitchHand` (common production case),
    the index must fall back to the people-endpoint pitchHand."""
    mpp.reset_cache()
    schedule = {
        "dates": [{"games": [{
            "teams": {
                "home": {
                    "team": {"abbreviation": "ATL"},
                    "probablePitcher": {
                        "id": 700001, "fullName": "X Pitcher",
                        # No pitchHand on schedule
                    },
                },
                "away": {
                    "team": {"abbreviation": "CHC"},
                    "probablePitcher": {
                        "id": 700002, "fullName": "Y Pitcher",
                    },
                },
            },
        }]}],
    }
    people_responses = {
        700001: {"people": [{
            "pitchHand": {"code": "R"},
            "stats": [{"splits": [{"stat": {
                "era": "3.10", "whip": "1.10",
                "strikeoutsPer9Inn": "9.0",
            }}]}],
        }]},
        700002: {"people": [{
            "pitchHand": {"code": "L"},
            "stats": [],
        }]},
    }

    class _Client(_DummyClient):
        async def get(self, url, params=None):
            if "/schedule" in url:
                return _DummyResp(schedule)
            pid = int(url.rstrip("/").rsplit("/", 1)[-1])
            return _DummyResp(people_responses.get(pid, {"people": []}))

    with patch.object(mpp.httpx, "AsyncClient", _Client):
        idx = asyncio.run(mpp.build_probable_pitcher_index("2026-05-15"))

    pair = idx.get("ATL", "CHC")
    assert pair is not None
    assert pair["home"]["throws"] == "R"
    assert pair["away"]["throws"] == "L"
    # Stats still flow through for the pitcher that has them.
    assert pair["home"]["era"] == 3.10
    assert pair["away"]["era"] is None

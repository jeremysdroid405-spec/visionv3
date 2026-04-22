"""
Tests for services/market_catalog.py — dynamic Odds API market discovery.

Locks the 2026-04-21 "pull all available markets / all 3 books"
requirement: no hardcoded market whitelist can silently drop a prop
when DraftKings/FanDuel/BetOnline add a new market.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import pytest

from services.market_catalog import (
    MarketCatalog,
    _GAME_MARKET_PREFIXES,
    _PLAYER_MARKET_PREFIXES,
    _is_game_market,
    _is_player_market,
)


class _FakeResponse:
    def __init__(self, status_code: int, json_payload: Any):
        self.status_code = status_code
        self._json = json_payload

    def json(self):
        return self._json


class _FakeClient:
    """Minimal httpx.AsyncClient stub capturing calls."""

    def __init__(self, responses: List[_FakeResponse]):
        self.responses = list(responses)
        self.calls: List[Dict[str, Any]] = []

    async def get(self, url: str, params: Optional[Dict[str, Any]] = None):
        self.calls.append({"url": url, "params": dict(params or {})})
        if not self.responses:
            return _FakeResponse(500, {})
        return self.responses.pop(0)


# ---------------------------------------------------------------------------
# Pure helper behavior
# ---------------------------------------------------------------------------

def test_is_player_market_recognises_all_sport_prefixes():
    assert _is_player_market("player_points")
    assert _is_player_market("player_points_alternate")
    assert _is_player_market("batter_home_runs")
    assert _is_player_market("pitcher_strikeouts")
    assert _is_player_market("passer_passing_yards")


def test_is_player_market_rejects_game_markets():
    assert not _is_player_market("h2h")
    assert not _is_player_market("spreads")
    assert not _is_player_market("totals")


def test_is_game_market_recognises_game_prefixes():
    for key in _GAME_MARKET_PREFIXES:
        assert _is_game_market(key)
    assert not _is_game_market("player_points")


# ---------------------------------------------------------------------------
# Discovery behavior
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_discover_event_markets_returns_union_of_player_markets():
    """Union across the 3 books; game markets stripped by default."""
    payload = {
        "bookmakers": [
            {"key": "draftkings", "markets": [
                {"key": "player_points"},
                {"key": "player_rebounds"},
                {"key": "h2h"},  # must be filtered out
            ]},
            {"key": "fanduel", "markets": [
                {"key": "player_points"},
                {"key": "player_assists"},
            ]},
            {"key": "betonlineag", "markets": [
                {"key": "player_threes"},
                {"key": "player_first_basket"},
                {"key": "spreads"},  # filtered out
            ]},
        ]
    }
    client = _FakeClient([_FakeResponse(200, payload)])
    catalog = MarketCatalog(api_key="fake")

    got = await catalog.discover_event_markets(
        client=client,
        sport_key="basketball_nba",
        event_id="evt-123",
        regions="us,us2",
        bookmakers=["draftkings", "fanduel", "betonlineag"],
    )

    # Deterministic sorted order, union of player_* markets only.
    assert got == sorted([
        "player_points",
        "player_rebounds",
        "player_assists",
        "player_threes",
        "player_first_basket",
    ])


@pytest.mark.asyncio
async def test_discover_event_markets_include_game_markets_toggle():
    payload = {
        "bookmakers": [
            {"key": "draftkings", "markets": [
                {"key": "player_points"},
                {"key": "h2h"},
                {"key": "spreads"},
            ]},
        ]
    }
    client = _FakeClient([_FakeResponse(200, payload)])
    catalog = MarketCatalog(api_key="fake")

    got = await catalog.discover_event_markets(
        client=client,
        sport_key="basketball_nba",
        event_id="evt-123",
        regions="us",
        bookmakers=["draftkings"],
        include_game_markets=True,
    )

    assert got == sorted(["player_points", "h2h", "spreads"])


@pytest.mark.asyncio
async def test_discover_event_markets_filters_bookmakers_not_requested():
    """Response may echo extra books; we only keep the ones we asked for."""
    payload = {
        "bookmakers": [
            {"key": "draftkings", "markets": [{"key": "player_points"}]},
            {"key": "pinnacle", "markets": [{"key": "player_points_alternate"}]},
        ]
    }
    client = _FakeClient([_FakeResponse(200, payload)])
    catalog = MarketCatalog(api_key="fake")

    got = await catalog.discover_event_markets(
        client=client,
        sport_key="basketball_nba",
        event_id="evt-123",
        regions="us",
        bookmakers=["draftkings"],
    )
    assert got == ["player_points"]


@pytest.mark.asyncio
async def test_discover_event_markets_returns_empty_on_non_200():
    client = _FakeClient([_FakeResponse(404, {})])
    catalog = MarketCatalog(api_key="fake")
    got = await catalog.discover_event_markets(
        client=client,
        sport_key="baseball_mlb",
        event_id="evt-999",
        regions="us",
        bookmakers=["draftkings"],
    )
    assert got == []


@pytest.mark.asyncio
async def test_discover_event_markets_empty_without_api_key():
    catalog = MarketCatalog(api_key=None)
    client = _FakeClient([])
    got = await catalog.discover_event_markets(
        client=client,
        sport_key="basketball_nba",
        event_id="evt-123",
        regions="us",
        bookmakers=["draftkings"],
    )
    assert got == []
    assert client.calls == []  # No HTTP attempt.


@pytest.mark.asyncio
async def test_discover_event_markets_caches_per_event():
    """A repeat call for the same (sport, event, regions, books) tuple
    must NOT re-hit the HTTP client — we only pay the discovery credit
    once per event per sync."""
    payload = {"bookmakers": [
        {"key": "draftkings", "markets": [{"key": "player_points"}]},
    ]}
    client = _FakeClient([_FakeResponse(200, payload)])
    catalog = MarketCatalog(api_key="fake")

    got1 = await catalog.discover_event_markets(
        client=client, sport_key="basketball_nba", event_id="evt",
        regions="us", bookmakers=["draftkings"],
    )
    got2 = await catalog.discover_event_markets(
        client=client, sport_key="basketball_nba", event_id="evt",
        regions="us", bookmakers=["draftkings"],
    )
    assert got1 == got2 == ["player_points"]
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_discover_union_across_events_aggregates_and_caps_sample_size():
    """Only the first `max_events` events should be probed, and the
    union across probes should be returned."""
    responses = [
        _FakeResponse(200, {"bookmakers": [
            {"key": "draftkings", "markets": [{"key": "player_points"}]},
        ]}),
        _FakeResponse(200, {"bookmakers": [
            {"key": "draftkings", "markets": [{"key": "player_rebounds"}]},
        ]}),
        _FakeResponse(200, {"bookmakers": [
            {"key": "draftkings", "markets": [{"key": "player_assists"}]},
        ]}),
        # 4th event should NOT be probed (max_events=3 default).
        _FakeResponse(200, {"bookmakers": [
            {"key": "draftkings", "markets": [{"key": "player_threes"}]},
        ]}),
    ]
    client = _FakeClient(responses)
    catalog = MarketCatalog(api_key="fake")

    got = await catalog.discover_union_across_events(
        client=client,
        sport_key="basketball_nba",
        event_ids=["e1", "e2", "e3", "e4"],
        regions="us",
        bookmakers=["draftkings"],
    )
    assert got == ["player_assists", "player_points", "player_rebounds"]
    # Exactly 3 HTTP calls despite 4 events supplied.
    assert len(client.calls) == 3

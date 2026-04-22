"""
Market Catalog — Dynamic Odds API market discovery.
====================================================

Purpose
-------
Given a sport + event + set of bookmakers, discover the full list of
market keys each bookmaker currently offers for that event via The Odds
API's ``/v4/sports/{sport}/events/{event_id}/markets`` endpoint.

This replaces the hardcoded ``PRIZEPICKS_STANDARD_MARKETS`` /
``SHARP_MARKETS`` / ``SPORT_API_CONFIG[sport]['markets']`` lists that
capped our coverage and silently dropped every prop whose market key
wasn't on the whitelist.

Usage
-----
    catalog = MarketCatalog(api_key=ODDS_API_KEY)
    markets = await catalog.discover_event_markets(
        client=httpx_client,
        sport_key="basketball_nba",
        event_id="abc123",
        regions="us,us2",
        bookmakers=["draftkings", "fanduel", "betonlineag"],
        include_game_markets=False,  # player_* only by default
    )
    # ['player_points', 'player_points_alternate', 'player_rebounds', ...]

Invariants
----------
* This module is an UPSTREAM fetcher. It MUST NEVER be imported from the
  continuous delta path (``services/delta/*``, ``services/delta_engine``,
  ``services/pipeline/delta_steps.py``, etc.) — the delta isolation test
  guards this.
* Market-discovery calls are cheap (1 credit / request, regardless of
  region or bookmaker count per The Odds API docs).
"""
from __future__ import annotations

import logging
from typing import Iterable, List, Optional, Sequence, Set

import httpx

logger = logging.getLogger(__name__)

# Markets that are NOT per-player (these are game-level). We include them
# only when include_game_markets=True to keep the default credit cost down.
_GAME_MARKET_PREFIXES: tuple = (
    "h2h",
    "spreads",
    "totals",
    "outrights",
    "team_totals",
    "alternate_spreads",
    "alternate_totals",
    "alternate_team_totals",
)

# Any market key that starts with one of these is a "player prop" across
# every sport Odds API currently supports (NBA, NFL, MLB, NHL, etc).
_PLAYER_MARKET_PREFIXES: tuple = (
    "player_",
    "batter_",
    "pitcher_",
    "passer_",
    "rusher_",
    "receiver_",
    "kicker_",
    "goalie_",
    "skater_",
)


def _is_player_market(market_key: str) -> bool:
    return market_key.lower().startswith(_PLAYER_MARKET_PREFIXES)


def _is_game_market(market_key: str) -> bool:
    return market_key.lower().startswith(_GAME_MARKET_PREFIXES)


class MarketCatalog:
    """Thin wrapper over The Odds API's ``/events/{id}/markets`` endpoint.

    Per-event results are cached for the lifetime of the instance so a
    follow-up ``/odds`` call does not re-pay the discovery credit.
    """

    ODDS_API_BASE = "https://api.the-odds-api.com/v4"

    def __init__(self, api_key: Optional[str]) -> None:
        self._api_key = api_key
        # cache_key = (sport_key, event_id, regions, tuple(bookmakers))
        self._cache: dict = {}

    async def discover_event_markets(
        self,
        *,
        client: httpx.AsyncClient,
        sport_key: str,
        event_id: str,
        regions: str,
        bookmakers: Sequence[str],
        include_game_markets: bool = False,
    ) -> List[str]:
        """Return every market key the specified bookmakers currently
        expose for the given event.

        Only *player* markets are returned by default. Pass
        ``include_game_markets=True`` to also include ``h2h`` / ``spreads``
        / ``totals`` / etc.

        Failures (network error, 4xx) fall back to an empty list — the
        caller is responsible for selecting a sensible hardcoded fallback
        in that case.
        """
        if not self._api_key:
            logger.warning("[MARKET_CATALOG] No ODDS_API_KEY configured")
            return []

        bm_tuple = tuple(sorted(set(bookmakers)))
        cache_key = (sport_key, event_id, regions, bm_tuple, include_game_markets)
        if cache_key in self._cache:
            return list(self._cache[cache_key])

        url = f"{self.ODDS_API_BASE}/sports/{sport_key}/events/{event_id}/markets"
        params = {
            "apiKey": self._api_key,
            "regions": regions,
            "bookmakers": ",".join(bm_tuple),
        }

        try:
            resp = await client.get(url, params=params)
        except Exception as e:  # pragma: no cover
            logger.warning(
                f"[MARKET_CATALOG] {sport_key} {event_id[:8]}... request error: {e}"
            )
            return []

        if resp.status_code != 200:
            # 404 = event has no markets available yet (pre-game window).
            # 422 = invalid bookmakers (silently ignore). Both mean "no data".
            logger.debug(
                f"[MARKET_CATALOG] {sport_key} {event_id[:8]}... "
                f"returned {resp.status_code}"
            )
            self._cache[cache_key] = []
            return []

        payload = resp.json() or {}
        # payload shape: {id, sport_key, bookmakers: [{key, markets: [{key,...}]}]}
        seen: Set[str] = set()
        for bm in payload.get("bookmakers", []) or []:
            if bm.get("key") not in bm_tuple:
                continue
            for market in bm.get("markets", []) or []:
                key = (market or {}).get("key")
                if not key:
                    continue
                if _is_player_market(key):
                    seen.add(key)
                elif include_game_markets and _is_game_market(key):
                    seen.add(key)

        ordered = sorted(seen)
        self._cache[cache_key] = ordered
        logger.info(
            f"[MARKET_CATALOG] {sport_key} event={event_id[:8]} "
            f"books={list(bm_tuple)} → discovered {len(ordered)} markets"
        )
        return list(ordered)

    async def discover_union_across_events(
        self,
        *,
        client: httpx.AsyncClient,
        sport_key: str,
        event_ids: Iterable[str],
        regions: str,
        bookmakers: Sequence[str],
        include_game_markets: bool = False,
        max_events: int = 3,
    ) -> List[str]:
        """Shortcut: union of markets seen across the first ``max_events``
        events (3 by default — empirically enough to capture the full
        market catalog for a sport on any given day).

        Use this when you want ONE market list shared across every event
        rather than a per-event list (cheaper + simpler for bulk syncs).
        """
        sampled = list(event_ids)[:max_events]
        if not sampled:
            return []

        union: Set[str] = set()
        for eid in sampled:
            discovered = await self.discover_event_markets(
                client=client,
                sport_key=sport_key,
                event_id=eid,
                regions=regions,
                bookmakers=bookmakers,
                include_game_markets=include_game_markets,
            )
            union.update(discovered)
        ordered = sorted(union)
        logger.info(
            f"[MARKET_CATALOG] {sport_key} union across {len(sampled)} event(s) "
            f"for books={list(bookmakers)}: {len(ordered)} markets"
        )
        return ordered

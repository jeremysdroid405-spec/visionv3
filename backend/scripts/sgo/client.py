"""
SGO async HTTP client.

Features:
  • Token-bucket rate limiter (default 250 rpm — under the 300 rpm trial cap)
  • Exponential backoff on 429 + transient 5xx (1s → 2s → 4s → 8s → 16s, cap 60s)
  • Automatic `apiKey` injection
  • Lightweight call counter for live headroom reporting
  • /account/usage helper
  • Configurable base URL & version

Async-only (motor + asyncio everywhere else in our backend).
"""
from __future__ import annotations
import asyncio
import logging
import time
from typing import Any, Dict, Optional, AsyncIterator
import httpx

log = logging.getLogger("sgo.client")


class RateLimiter:
    """Sliding-window rate limiter — at most N requests in any rolling 60s."""

    def __init__(self, max_per_minute: int):
        self.max = max_per_minute
        self._stamps: list[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            cutoff = now - 60.0
            self._stamps = [t for t in self._stamps if t >= cutoff]
            if len(self._stamps) >= self.max:
                sleep_for = 60.0 - (now - self._stamps[0]) + 0.05
                if sleep_for > 0:
                    log.debug("rate-limiter sleeping %.2fs", sleep_for)
                    await asyncio.sleep(sleep_for)
                now = time.monotonic()
                cutoff = now - 60.0
                self._stamps = [t for t in self._stamps if t >= cutoff]
            self._stamps.append(now)


class SGOClient:
    DEFAULT_BASE_URL = "https://api.sportsgameodds.com/v2"

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        max_rpm: int = 250,
        timeout: float = 30.0,
        max_retries: int = 5,
    ):
        if not api_key:
            raise ValueError("api_key is required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.limiter = RateLimiter(max_rpm)
        self.max_retries = max_retries
        self._client = httpx.AsyncClient(timeout=timeout)
        # call telemetry
        self.calls_total = 0
        self.calls_2xx = 0
        self.calls_429 = 0
        self.calls_err = 0

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.aclose()

    # --------------------------------------------------------------------- core
    async def request(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        method: str = "GET",
    ) -> Dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        params = dict(params or {})
        params.setdefault("apiKey", self.api_key)

        backoff = 1.0
        for attempt in range(self.max_retries + 1):
            await self.limiter.acquire()
            self.calls_total += 1
            try:
                resp = await self._client.request(method, url, params=params)
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as e:
                self.calls_err += 1
                if attempt >= self.max_retries:
                    raise
                log.warning("net error %r — retrying in %.1fs", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
                continue

            if resp.status_code == 429:
                self.calls_429 += 1
                if attempt >= self.max_retries:
                    resp.raise_for_status()
                ra = resp.headers.get("retry-after")
                wait = float(ra) if ra and ra.replace(".", "").isdigit() else backoff
                log.warning("429 — sleeping %.1fs (attempt %d)", wait, attempt + 1)
                await asyncio.sleep(wait)
                backoff = min(backoff * 2, 60.0)
                continue

            if 500 <= resp.status_code < 600:
                self.calls_err += 1
                if attempt >= self.max_retries:
                    resp.raise_for_status()
                log.warning("%d — retrying in %.1fs", resp.status_code, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
                continue

            if resp.status_code >= 400:
                self.calls_err += 1
                resp.raise_for_status()

            self.calls_2xx += 1
            return resp.json()

        raise RuntimeError("retry budget exhausted")  # pragma: no cover

    # ------------------------------------------------------------------ helpers
    async def get_account_usage(self) -> Dict[str, Any]:
        return await self.request("/account/usage")

    async def get_events(self, **params: Any) -> Dict[str, Any]:
        return await self.request("/events", params=params)

    async def get_event_with_results(self, event_id: str,
                                       expand_results: bool = True,
                                       include_alt_lines: bool = False
                                       ) -> Optional[Dict[str, Any]]:
        """Fetch ONE event including its `results` object (player + team stat
        values). Returns the event dict or None if not found.

        SGO returns player/team stats inline inside `event.results` only when
        `expandResults=true` is passed.  This is the canonical SGO endpoint
        for historical player box-score values (there is no separate
        /v2/stats endpoint that returns per-player results).
        """
        data = await self.request(
            "/events",
            params={"eventID": event_id,
                     "expandResults": "true" if expand_results else "false",
                     "includeAltLines": "true" if include_alt_lines else "false"})
        events = (data.get("data") or data.get("events") or [])
        return events[0] if events else None

    async def iter_finalized_events_with_results(
        self,
        league_id: str,
        starts_after: Optional[str] = None,
        starts_before: Optional[str] = None,
        page_size: int = 50,
        max_pages: int = 4000,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Stream finalized events for a league with `expandResults=true`.

        Use this for the historical-stats ingest path: every yielded event
        carries a populated `results` object that we extract player stats from.
        """
        cursor: Optional[str] = None
        page = 0
        while page < max_pages:
            page += 1
            params: Dict[str, Any] = {
                "leagueID": league_id,
                "limit": page_size,
                "expandResults": "true",
                "finalized": "true",
                "includeAltLines": "false",
            }
            if starts_after:  params["startsAfter"]  = starts_after
            if starts_before: params["startsBefore"] = starts_before
            if cursor:        params["cursor"]       = cursor
            data = await self.get_events(**params)
            events = (data.get("data") or data.get("events") or [])
            if not events:
                break
            for ev in events:
                yield ev
            cursor = data.get("nextCursor") or (
                (data.get("meta") or {}).get("nextCursor"))
            if not cursor:
                break

    async def get_stats_taxonomy(self, sport_id: Optional[str] = None,
                                    stat_id: Optional[str] = None,
                                    stat_level: Optional[str] = None
                                    ) -> Dict[str, Any]:
        """SGO /v2/stats endpoint — returns the STAT TAXONOMY (metadata
        about supported statIDs), NOT per-event/per-player values.

        Params:
            sportID    "BASEBALL" | "BASKETBALL" | ...
            statID     specific statID to lookup
            statLevel  "all" | "player" | "team"
        """
        params: Dict[str, Any] = {}
        if sport_id:   params["sportID"]   = sport_id
        if stat_id:    params["statID"]    = stat_id
        if stat_level: params["statLevel"] = stat_level
        return await self.request("/stats", params=params)

    async def get_teams(self, **params: Any) -> Dict[str, Any]:
        return await self.request("/teams", params=params)

    async def get_players(self, **params: Any) -> Dict[str, Any]:
        return await self.request("/players", params=params)

    async def get_leagues(self, **params: Any) -> Dict[str, Any]:
        return await self.request("/leagues", params=params)

    async def get_sports(self, **params: Any) -> Dict[str, Any]:
        return await self.request("/sports", params=params)

    async def get_bookmakers(self, **params: Any) -> Dict[str, Any]:
        return await self.request("/bookmakers", params=params)

    # -------------------------------------------------------------- pagination
    async def iter_events(
        self,
        league_id: str = "MLB",
        page_size: int = 500,
        max_pages: int = 1000,
        **filters: Any,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Yield event objects across pages. `filters` flows through to `/events`."""
        page = 1
        while page <= max_pages:
            params = {"leagueID": league_id, "limit": page_size, "page": page, **filters}
            data = await self.get_events(**params)
            events = (data.get("events") or data.get("data") or
                      data.get("results") or [])
            if not events:
                break
            for ev in events:
                yield ev
            # End-of-data heuristic
            next_cursor = (data.get("nextPage") or data.get("nextCursor") or
                           data.get("next") or (data.get("meta") or {}).get("nextCursor"))
            if next_cursor and "page" not in str(next_cursor):
                # cursor-based pagination
                filters["cursor"] = next_cursor
                page += 1
                continue
            if len(events) < page_size:
                break
            page += 1

    def stats(self) -> Dict[str, int]:
        return {
            "total": self.calls_total,
            "2xx": self.calls_2xx,
            "429": self.calls_429,
            "err": self.calls_err,
        }

"""
Async HTTP client for The Odds API v4.

Critical features:
  * Bulk-markets-per-request (10× cheaper than market-by-market calls).
  * Tenacity retry with exponential backoff on 429 / transient errors.
  * Reads `x-requests-remaining` / `x-requests-last` headers; halts on
    credit budget breach.
  * Rate-limit semaphore (default 20 in-flight requests).
  * No SDK — direct aiohttp on documented v4 endpoints.

Quota math (verified per The Odds API docs):
  cost = markets × regions × events × 10  for /v4/historical/...

So a single call with all 12 markets, 1 region, 1 event = 120 credits.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import aiohttp
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

ODDS_API_BASE = "https://api.the-odds-api.com"
DEFAULT_RPS_SEMAPHORE = 20


class CreditBudgetExceeded(RuntimeError):
    """Raised when remaining API credits drop below the configured floor."""


class OddsAPIClient:
    """Async client for The Odds API v4. Use as an async context manager."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        max_in_flight: int = DEFAULT_RPS_SEMAPHORE,
        min_remaining_credits: int = 1_000,
    ) -> None:
        self.api_key = api_key or os.environ.get("ODDS_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "ODDS_API_KEY is not set. Aborting client init.")
        self._sem = asyncio.Semaphore(max_in_flight)
        self._min_remaining = int(min_remaining_credits)
        self._session: Optional[aiohttp.ClientSession] = None
        self.stats = {
            "requests": 0, "success": 0, "failed": 0, "rate_limited": 0,
            "credits_used_session": 0,
            "credits_remaining_last_seen": None,
        }

    async def __aenter__(self) -> "OddsAPIClient":
        connector = aiohttp.TCPConnector(limit=100, limit_per_host=10,
                                          ttl_dns_cache=300)
        self._session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=30, connect=10, sock_read=15),
        )
        return self

    async def __aexit__(self, *_a: Any) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    # ------------------------------------------------------------------
    @retry(
        retry=retry_if_exception_type((aiohttp.ClientError,
                                        asyncio.TimeoutError)),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        reraise=True,
    )
    async def _get(self, endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
        assert self._session is not None
        params = {**params, "apiKey": self.api_key}
        url = f"{ODDS_API_BASE}{endpoint}"
        self.stats["requests"] += 1
        async with self._sem:
            async with self._session.get(url, params=params) as resp:
                rem_hdr = resp.headers.get("x-requests-remaining")
                last_hdr = resp.headers.get("x-requests-last")
                rem = int(rem_hdr) if rem_hdr is not None else None
                last = int(last_hdr) if last_hdr is not None else None
                if rem is not None:
                    self.stats["credits_remaining_last_seen"] = rem
                if last is not None:
                    self.stats["credits_used_session"] += last

                if resp.status == 429:
                    self.stats["rate_limited"] += 1
                    logger.warning(f"[odds_api] 429 received remaining={rem}")
                    raise aiohttp.ClientError("HTTP 429 Too Many Requests")
                resp.raise_for_status()
                data = await resp.json()
                self.stats["success"] += 1
                logger.info(
                    f"[odds_api] {endpoint} 200 last_cost={last} remaining={rem}")
                if rem is not None and rem < self._min_remaining:
                    raise CreditBudgetExceeded(
                        f"Credits remaining ({rem}) below floor "
                        f"({self._min_remaining}). Halting.")
                return data

    # ------------------------------------------------------------------
    async def list_historical_events(
        self, *, sport: str, snapshot_iso: str,
    ) -> List[Dict[str, Any]]:
        """Returns event list at the given timestamp (≤ snapshot)."""
        endpoint = f"/v4/historical/sports/{sport}/events"
        data = await self._get(endpoint, {"date": snapshot_iso,
                                            "dateFormat": "iso"})
        return data.get("data", []) if isinstance(data, dict) else (data or [])

    async def get_historical_event_odds(
        self,
        *,
        sport: str,
        event_id: str,
        markets: List[str],
        regions: List[str],
        snapshot_iso: str,
        odds_format: str = "american",
    ) -> Dict[str, Any]:
        """One call, all markets — minimizes credit consumption.

        Historical responses wrap the odds payload in `{"timestamp": ...,
        "data": {...}}`. We unwrap `data` so callers get the same
        bookmakers-at-root shape used by the v4 live endpoint.
        """
        endpoint = f"/v4/historical/sports/{sport}/events/{event_id}/odds"
        params = {
            "regions": ",".join(regions),
            "markets": ",".join(markets),
            "date": snapshot_iso,
            "oddsFormat": odds_format,
            "dateFormat": "iso",
        }
        raw = await self._get(endpoint, params)
        if isinstance(raw, dict) and isinstance(raw.get("data"), dict):
            return raw["data"]
        return raw


__all__ = ["OddsAPIClient", "CreditBudgetExceeded"]

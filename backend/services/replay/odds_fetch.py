"""
Replay odds fetcher — envelope-preserving wrapper around The Odds API.

The existing `scripts.odds_api_backfill.client.OddsAPIClient` unwraps the
`data` field from historical event-odds responses, which throws away
`timestamp / previous_timestamp / next_timestamp`. The replay layer needs
those fields to reconstruct the snapshot ladder later.

This module makes its own direct aiohttp call, reusing the auth + session
+ semaphore from `OddsAPIClient` but bypassing its tenacity retry — which
otherwise retries 5× on a 404 ("event not yet carried at this snapshot")
and wastes time on a misclassified failure.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import aiohttp

from scripts.odds_api_backfill.client import (
    CreditBudgetExceeded, ODDS_API_BASE, OddsAPIClient,
)

logger = logging.getLogger(__name__)


class SnapshotNotAvailable(RuntimeError):
    """Returned by the API as 404 — event not carried at this snapshot ts.

    Not a failure; treat as 'empty snapshot' and proceed to next window.
    """


async def fetch_historical_event_odds_envelope(
    client: OddsAPIClient,
    *,
    sport: str,
    event_id: str,
    markets: List[str],
    regions: List[str],
    snapshot_iso: str,
    odds_format: str = "american",
) -> Dict[str, Any]:
    """Return the FULL historical event odds envelope, not just `data`.

    Raises:
        SnapshotNotAvailable: API returned 404 (event not yet listed).
        CreditBudgetExceeded: remaining quota fell below the floor.
        aiohttp.ClientResponseError: any other non-2xx status.
    """
    endpoint = f"/v4/historical/sports/{sport}/events/{event_id}/odds"
    params = {
        "regions":    ",".join(regions),
        "markets":    ",".join(markets),
        "date":       snapshot_iso,
        "oddsFormat": odds_format,
        "dateFormat": "iso",
        "apiKey":     client.api_key,
    }
    url = f"{ODDS_API_BASE}{endpoint}"
    assert client._session is not None, "OddsAPIClient not entered"  # noqa: SLF001

    client.stats["requests"] += 1
    async with client._sem:  # noqa: SLF001
        async with client._session.get(url, params=params) as resp:  # noqa: SLF001
            rem_hdr = resp.headers.get("x-requests-remaining")
            last_hdr = resp.headers.get("x-requests-last")
            rem = int(rem_hdr) if rem_hdr is not None else None
            last = int(last_hdr) if last_hdr is not None else None
            if rem is not None:
                client.stats["credits_remaining_last_seen"] = rem
            if last is not None:
                client.stats["credits_used_session"] += last

            if resp.status == 404:
                logger.info(
                    f"[replay.odds_fetch] 404 (no snapshot) "
                    f"event={event_id} ts={snapshot_iso} cost={last}")
                raise SnapshotNotAvailable(
                    f"404 from {endpoint} at {snapshot_iso}")
            if resp.status == 429:
                client.stats["rate_limited"] += 1
                logger.warning(
                    f"[replay.odds_fetch] 429 remaining={rem}")
                raise aiohttp.ClientError("HTTP 429 Too Many Requests")
            resp.raise_for_status()
            data = await resp.json()
            client.stats["success"] += 1
            logger.info(
                f"[replay.odds_fetch] 200 cost={last} remaining={rem} "
                f"event={event_id} ts={snapshot_iso}")
            if rem is not None and rem < client._min_remaining:  # noqa: SLF001
                raise CreditBudgetExceeded(
                    f"Credits remaining ({rem}) below floor "
                    f"({client._min_remaining}). Halting.")  # noqa: SLF001
            if not isinstance(data, dict):
                raise RuntimeError(
                    f"Unexpected non-dict response: type={type(data).__name__}"
                )
            return data


async def fetch_historical_events(
    client: OddsAPIClient,
    *,
    sport: str,
    snapshot_iso: str,
) -> List[Dict[str, Any]]:
    """Thin pass-through to the client's events list."""
    return await client.list_historical_events(
        sport=sport, snapshot_iso=snapshot_iso,
    )


__all__ = [
    "fetch_historical_event_odds_envelope",
    "fetch_historical_events",
]

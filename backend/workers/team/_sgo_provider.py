"""
Phase 1.A.3.3 — SGO payload provider.

Single source of truth for "fetch one SGO event-odds payload, run
sanitization, return both sanitized JSON + bytes". Used by BOTH:

  - the recorder CLI (`scripts/team_odds_fixture_record.py`)
  - the worker dry-run HTTP fetcher
    (`TeamOddsIngestWorker.fetch_and_run_pass`)

Contract:
  - ONE HTTP GET, no retries, no fan-out (per SNAPSHOT_LOOP_DESIGN §6
    when called from the recorder; the worker's repeated-pass loop in
    1.A.3.4 will own retries + backoff).
  - 30 s connect+read timeout.
  - API key passed as constructor arg — provider NEVER reads env.
  - Caller is responsible for the dispatch guard.
  - URL is sanitized BEFORE any logging (key stripped).
  - Response bytes go through `sanitize_response_bytes` BEFORE parsing.
  - Raises `SGOFetchError` on transport / HTTP / parse failure with
    a stable `kind` attribute (`transport`, `http_status`,
    `json_decode`, `empty_payload`).

Phase 1.A.3.3 does NOT write to `team_live_props`. The provider is
purely the HTTP+sanitize tier.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Tuple

import httpx

from services.team_master_hub.fixtures import (
    REDACTION_TOKEN,
    sanitize_response_bytes,
)

logger = logging.getLogger("workers.team.sgo_provider")

_SGO_HOST = "https://api.sportsgameodds.com"
_DEFAULT_TIMEOUT_SEC = 30.0

# League filter per sport — matches the canonical client convention
# (`sportID` + `leagueID` as separate camelCase query params).
_SPORT_TO_SGO_IDS: Dict[str, Dict[str, str]] = {
    "mlb": {"sportID": "BASEBALL",   "leagueID": "MLB"},
    "nba": {"sportID": "BASKETBALL", "leagueID": "NBA"},
    "nfl": {"sportID": "FOOTBALL",   "leagueID": "NFL"},
}


class SGOFetchError(RuntimeError):
    """Stable-kind error from the SGO provider."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(f"[{kind}] {message}")
        self.kind = kind


class SGOPayloadProvider:
    """Single-fetch SGO event-odds provider.

    Construct with the API key (caller resolves from env or config).
    Use `fetch_event_odds(sport, event_id)` to retrieve one event.
    """

    def __init__(
        self,
        api_key: str,
        *,
        host: str = _SGO_HOST,
        timeout_sec: float = _DEFAULT_TIMEOUT_SEC,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise SGOFetchError("transport",
                                  "api_key is required (empty given)")
        self._api_key = api_key
        self._host    = host.rstrip("/")
        self._timeout = float(timeout_sec)
        self._client  = client  # injectable for tests

    # ── URL builders ────────────────────────────────────────────────
    def _build_url(
        self,
        *,
        sport: str,
        event_id: str,
    ) -> Tuple[str, str]:
        """Build the SGO v2 /events?eventID=... URL with apiKey as a
        query param (canonical pattern from scripts/sgo/client.py).

        Returns `(real_url, sanitized_url)`. The sanitized form is
        used for logging — the real form is sent over the wire.
        """
        ids = _SPORT_TO_SGO_IDS[sport]
        real = (
            f"{self._host}/v2/events"
            f"?sportID={ids['sportID']}"
            f"&leagueID={ids['leagueID']}"
            f"&eventID={event_id}"
            f"&expandResults=true"
            f"&apiKey={self._api_key}"
        )
        sanitized = real.replace(self._api_key, REDACTION_TOKEN)
        return real, sanitized

    def _build_events_by_date_url(
        self,
        *,
        sport: str,
        starts_after: str,
        starts_before: str,
        cursor: str | None = None,
        page_size: int = 50,
    ) -> Tuple[str, str]:
        """Build the SGO v2 /events?startsAfter=...&startsBefore=... URL.

        Used by the team-events schedule sync (Phase 1.A.4a) to pull
        every event in a UTC date window. Pagination via `cursor`.
        """
        ids = _SPORT_TO_SGO_IDS[sport]
        parts = [
            f"{self._host}/v2/events",
            f"?sportID={ids['sportID']}",
            f"&leagueID={ids['leagueID']}",
            f"&startsAfter={starts_after}",
            f"&startsBefore={starts_before}",
            f"&limit={page_size}",
            f"&apiKey={self._api_key}",
        ]
        if cursor:
            parts.append(f"&cursor={cursor}")
        real = "".join(parts)
        sanitized = real.replace(self._api_key, REDACTION_TOKEN)
        return real, sanitized

    # ── Public API ──────────────────────────────────────────────────
    def fetch_event_odds(
        self,
        *,
        sport: str,
        event_id: str,
    ) -> Dict[str, Any]:
        """Synchronous single-fetch. Returns:

            {
              "sgo_endpoint": <sanitized URL>,
              "payload":      <parsed JSON dict>,
              "sanitized_bytes": <bytes that match `payload`>,
              "books_seen":   sorted unique bookmaker keys,
              "markets_seen": sorted unique market keys,
              "outcomes_count": int,
            }

        Raises `SGOFetchError(kind=…)` on any failure.
        """
        if sport not in _SPORT_TO_SGO_IDS:
            raise SGOFetchError(
                "transport", f"unsupported sport: {sport!r}")
        if not event_id:
            raise SGOFetchError(
                "transport", "event_id is required")

        real_url, sanitized_url = self._build_url(
            sport=sport, event_id=event_id)
        # Only the sanitized URL is ever logged
        logger.info("[sgo_provider] GET %s", sanitized_url)

        client_owned = self._client is None
        client = self._client or httpx.Client(timeout=self._timeout)
        try:
            try:
                resp = client.get(real_url)
            except httpx.HTTPError as exc:
                raise SGOFetchError(
                    "transport",
                    f"network error: {type(exc).__name__}: {exc}",
                ) from exc
        finally:
            if client_owned:
                client.close()

        if resp.status_code != 200:
            raise SGOFetchError(
                "http_status",
                f"HTTP {resp.status_code} from SGO "
                f"(body bytes len={len(resp.content)})",
            )

        sanitized = sanitize_response_bytes(
            resp.content, api_key_to_strip=self._api_key)
        try:
            payload = json.loads(sanitized.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise SGOFetchError(
                "json_decode",
                f"failed to parse SGO response as JSON: {exc}",
            ) from exc

        if not isinstance(payload, dict):
            raise SGOFetchError(
                "empty_payload",
                f"SGO response is not a dict (got "
                f"{type(payload).__name__})",
            )

        # SGO v2 returns `{"data": [<event>, ...]}` — older code-paths
        # used `{"events": [...]}`. Normalize to the latter so the
        # downstream `normalize_sgo_payload` walks a stable shape.
        if "events" not in payload:
            raw_events = payload.get("data") or []
            payload = dict(payload)        # avoid mutating caller view
            payload["events"] = raw_events

        if not payload.get("events"):
            raise SGOFetchError(
                "empty_payload",
                "SGO response has no events "
                f"(top-level keys: {sorted(payload.keys())})",
            )

        # Coverage summary for the meta file — walks the REAL SGO
        # event shape: `event.odds[market_key].byBookmaker[book]`.
        books: set[str] = set()
        markets: set[str] = set()
        outcomes = 0
        for ev in payload.get("events", []) or []:
            odds_block = ev.get("odds")
            if isinstance(odds_block, dict):
                for mk, mkt in odds_block.items():
                    if mk:
                        markets.add(mk)
                    if isinstance(mkt, dict):
                        by_bm = mkt.get("byBookmaker") or {}
                        if isinstance(by_bm, dict):
                            for bk in by_bm.keys():
                                if bk:
                                    books.add(bk.lower())
                            outcomes += len(by_bm)

        return {
            "sgo_endpoint":    sanitized_url,
            "payload":         payload,
            "sanitized_bytes": sanitized,
            "books_seen":      sorted(books),
            "markets_seen":    sorted(markets),
            "outcomes_count":  outcomes,
        }

    # ── Phase 1.A.4a — schedule sync entrypoint ─────────────────────
    def fetch_events_by_date(
        self,
        *,
        sport: str,
        game_date: str,
        max_pages: int = 50,
        page_size: int = 50,
    ) -> Dict[str, Any]:
        """Fetch every SGO event whose `startsAt` falls inside the UTC
        `game_date` window [00:00:00Z, +24h). Walks SGO cursor pages.

        Returns:
            {
              "events":         <list of raw event dicts>,
              "n_pages":        <int>,
              "n_events":       <int>,
              "sgo_endpoints":  <list of sanitized URLs walked>,
            }

        Raises `SGOFetchError(kind=…)` on any failure.
        """
        if sport not in _SPORT_TO_SGO_IDS:
            raise SGOFetchError(
                "transport", f"unsupported sport: {sport!r}")
        if not game_date or len(game_date) != 10:
            raise SGOFetchError(
                "transport",
                f"game_date must be 'YYYY-MM-DD' (got {game_date!r})")
        starts_after  = f"{game_date}T00:00:00Z"
        starts_before = f"{game_date}T23:59:59Z"

        client_owned = self._client is None
        client = self._client or httpx.Client(timeout=self._timeout)
        all_events: list[Dict[str, Any]] = []
        endpoints: list[str] = []
        cursor: str | None = None
        page = 0
        try:
            while page < max_pages:
                page += 1
                real_url, sanitized_url = self._build_events_by_date_url(
                    sport=sport, starts_after=starts_after,
                    starts_before=starts_before, cursor=cursor,
                    page_size=page_size,
                )
                endpoints.append(sanitized_url)
                logger.info("[sgo_provider] GET %s", sanitized_url)
                try:
                    resp = client.get(real_url)
                except httpx.HTTPError as exc:
                    raise SGOFetchError(
                        "transport",
                        f"network error: {type(exc).__name__}: {exc}",
                    ) from exc
                if resp.status_code != 200:
                    raise SGOFetchError(
                        "http_status",
                        f"HTTP {resp.status_code} from SGO "
                        f"(body bytes len={len(resp.content)})")
                sanitized = sanitize_response_bytes(
                    resp.content, api_key_to_strip=self._api_key)
                try:
                    payload = json.loads(sanitized.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise SGOFetchError(
                        "json_decode",
                        f"failed to parse SGO response as JSON: {exc}",
                    ) from exc
                if not isinstance(payload, dict):
                    raise SGOFetchError(
                        "empty_payload",
                        f"SGO response is not a dict (got "
                        f"{type(payload).__name__})")
                events = payload.get("data") or payload.get("events") or []
                if isinstance(events, list):
                    all_events.extend(events)
                cursor = payload.get("nextCursor") or (
                    (payload.get("meta") or {}).get("nextCursor"))
                if not cursor:
                    break
        finally:
            if client_owned:
                client.close()

        return {
            "events":        all_events,
            "n_pages":       page,
            "n_events":      len(all_events),
            "sgo_endpoints": endpoints,
        }


__all__ = ["SGOFetchError", "SGOPayloadProvider"]

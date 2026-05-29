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

# League filter per sport — must match `_PLANNED_ENDPOINTS` in
# `team_odds_ingest.py`.
_LEAGUE_BY_SPORT: Dict[str, str] = {
    "mlb": "MLB",
    "nba": "NBA",
    "nfl": "NFL",
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
        """Return `(real_url, sanitized_url)`. The sanitized form is
        used for logging — the real form is sent over the wire.
        """
        league = _LEAGUE_BY_SPORT[sport]
        real = (
            f"{self._host}/v2/events"
            f"?league={league}&event_id={event_id}"
            f"&api_key={self._api_key}"
        )
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
        if sport not in _LEAGUE_BY_SPORT:
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

        if not isinstance(payload, dict) or not payload.get("events"):
            raise SGOFetchError(
                "empty_payload",
                "SGO response has no `events` list "
                f"(top-level keys: {sorted(payload.keys()) if isinstance(payload, dict) else type(payload).__name__})",
            )

        # Coverage summary for the meta file
        books: set[str] = set()
        markets: set[str] = set()
        outcomes = 0
        for ev in payload.get("events", []) or []:
            for bm in ev.get("bookmakers", []) or []:
                bk = (bm.get("key") or "").lower()
                if bk:
                    books.add(bk)
                for mkt in bm.get("markets", []) or []:
                    mk = mkt.get("key")
                    if mk:
                        markets.add(mk)
                    outcomes += len(mkt.get("outcomes", []) or [])

        return {
            "sgo_endpoint":    sanitized_url,
            "payload":         payload,
            "sanitized_bytes": sanitized,
            "books_seen":      sorted(books),
            "markets_seen":    sorted(markets),
            "outcomes_count":  outcomes,
        }


__all__ = ["SGOFetchError", "SGOPayloadProvider"]

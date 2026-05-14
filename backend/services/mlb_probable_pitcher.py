"""MLB probable-pitcher feed (Phase 2A — 2026-05-15).

Wraps the free MLB Stats API at ``statsapi.mlb.com`` to populate the
four pitcher-context fields the Phase 2A audit identified as MOCKED
None in `services/feature_hydration.py:735-739`:

    opp_pitcher_id
    opp_pitcher_name
    opp_pitcher_throws
    probable_pitcher              # display alias of opp_pitcher_name

Plus three lightweight quality metrics (Phase 2A Task 3, no advanced
models):

    opp_pitcher_era
    opp_pitcher_whip
    opp_pitcher_k9

The MLB Stats API requires no authentication. The schedule endpoint
returns one record per game with the probable pitcher per side
embedded; the people endpoint returns season-level pitching stats.

Public entrypoint
─────────────────
``async fetch_probable_pitchers(date_iso: str) -> Dict[str, Dict]``

  Returns a ``{home_team_abbr+'@'+away_team_abbr: {...}}`` map but in
  practice we key on ``(home_abbr, away_abbr, commence_iso)`` triples
  because two teams can play a doubleheader. See
  ``ProbablePitcherIndex`` for the lookup API.

ProbablePitcherIndex
────────────────────
  Lightweight in-memory cache built once per ingest pass. Lookup by
  (home_abbr, away_abbr) returns:

      {"home": {pitcher_dict}, "away": {pitcher_dict}}

  pitcher_dict shape:

      {
        "id":     int,        # MLBAM person id
        "name":   str,
        "throws": str,        # 'L' or 'R'
        "era":    Optional[float],
        "whip":   Optional[float],
        "k9":     Optional[float],
      }

Caching
───────
The index is rebuilt per-ingest cycle (cheap — one schedule HTTP
call + N people HTTP calls where N is total starting pitchers on the
slate, typically ~30). A simple TTL cache (5 min) prevents repeated
calls for the same date within a single ingest cycle.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
_PEOPLE_URL = "https://statsapi.mlb.com/api/v1/people/{pid}"
_HTTP_TIMEOUT_SEC = 8.0
_CACHE_TTL_SEC = 300  # 5 min — covers an ingest cycle, no stale-pitcher risk

# Module-level cache; keyed by ISO date string.
_INDEX_CACHE: Dict[str, Tuple[float, "ProbablePitcherIndex"]] = {}


class ProbablePitcherIndex:
    """Look up tonight's probable pitcher by team-abbr pair.

    Built per-date — call ``rebuild_for_date()`` once at the start
    of an ingest run, then ``get(home_abbr, away_abbr)`` for each
    prop. Returns ``None`` when no game found (e.g. team played
    earlier in a doubleheader and the match-up is over).
    """

    def __init__(self):
        self._by_pair: Dict[Tuple[str, str], Dict[str, Any]] = {}

    def get(
        self, home_abbr: Optional[str], away_abbr: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        if not home_abbr or not away_abbr:
            return None
        return self._by_pair.get((home_abbr.upper(), away_abbr.upper()))

    def __len__(self) -> int:
        return len(self._by_pair)


async def _fetch_schedule_for_date(date_iso: str) -> List[Dict[str, Any]]:
    """Return the list of MLB games for ``date_iso`` (YYYY-MM-DD)
    with probable-pitcher hydrated."""
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as client:
        r = await client.get(
            _SCHEDULE_URL,
            params={
                "sportId": 1, "date": date_iso,
                "hydrate": "probablePitcher,team",
            },
        )
        r.raise_for_status()
        data = r.json()
    dates = data.get("dates") or []
    return (dates[0].get("games") if dates else []) or []


async def _fetch_pitcher_stats(
    person_id: int, season: int,
) -> Dict[str, Optional[float]]:
    """Fetch ERA/WHIP/K9 for ``person_id`` for ``season``.

    Returns dict with possibly-None values when the pitcher has no
    season stats yet (rookie called up mid-season is the common case).
    """
    out: Dict[str, Optional[float]] = {
        "era": None, "whip": None, "k9": None,
    }
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as client:
            r = await client.get(
                _PEOPLE_URL.format(pid=person_id),
                params={
                    "hydrate": (
                        f"stats(group=[pitching],type=[season],"
                        f"season={season})"
                    ),
                },
            )
            r.raise_for_status()
            data = r.json()
        people = data.get("people") or []
        if not people:
            return out
        stats = people[0].get("stats") or []
        if not stats:
            return out
        splits = stats[0].get("splits") or []
        if not splits:
            return out
        s = splits[0].get("stat") or {}
        # API ships strings ('2.70', '1.26', '14.04'); float-coerce
        # defensively.
        def _f(v):
            try:
                return float(v) if v not in (None, "", "-.--", "Infinity") else None
            except (TypeError, ValueError):
                return None
        out["era"] = _f(s.get("era"))
        out["whip"] = _f(s.get("whip"))
        out["k9"] = _f(s.get("strikeoutsPer9Inn"))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[MLB_PROBABLE_PITCHER] stats fetch failed for pid=%s: %s",
            person_id, exc,
        )
    return out


async def build_probable_pitcher_index(
    date_iso: str, season: Optional[int] = None,
) -> ProbablePitcherIndex:
    """Build a fresh index for ``date_iso``.

    Bypasses the cache. Used internally by
    ``get_probable_pitcher_index`` after a TTL miss.
    """
    if season is None:
        season = int(date_iso[:4])
    games = await _fetch_schedule_for_date(date_iso)
    idx = ProbablePitcherIndex()
    # Gather all unique pitcher ids first, then fetch stats in
    # parallel (httpx async).
    pids: List[int] = []
    pair_entries: List[Tuple[str, str, Dict, Dict]] = []
    for g in games:
        try:
            home = g["teams"]["home"]
            away = g["teams"]["away"]
            h_abbr = (home["team"].get("abbreviation")
                       or home["team"].get("teamName") or "").upper()
            a_abbr = (away["team"].get("abbreviation")
                       or away["team"].get("teamName") or "").upper()
            if not h_abbr or not a_abbr:
                continue
            hp = home.get("probablePitcher") or {}
            ap = away.get("probablePitcher") or {}
            if hp.get("id"):
                pids.append(int(hp["id"]))
            if ap.get("id"):
                pids.append(int(ap["id"]))
            pair_entries.append((h_abbr, a_abbr, hp, ap))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[MLB_PROBABLE_PITCHER] schedule game parse fail: %s",
                exc,
            )
            continue
    # Parallel stats fetch.
    stats_map: Dict[int, Dict[str, Optional[float]]] = {}
    if pids:
        tasks = [_fetch_pitcher_stats(pid, season) for pid in pids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for pid, res in zip(pids, results):
            stats_map[pid] = res if isinstance(res, dict) else {
                "era": None, "whip": None, "k9": None,
            }
    # Pitcher throws & bats are on the schedule hydration; if absent
    # we fall back to None (rare).
    for h_abbr, a_abbr, hp, ap in pair_entries:
        def _pitcher_dict(pitcher_json: Dict) -> Optional[Dict[str, Any]]:
            if not pitcher_json or not pitcher_json.get("id"):
                return None
            pid = int(pitcher_json["id"])
            throws = (
                (pitcher_json.get("pitchHand") or {}).get("code")
                or pitcher_json.get("throws")
            )
            stats = stats_map.get(pid, {})
            return {
                "id": pid,
                "name": pitcher_json.get("fullName"),
                "throws": (throws or "").upper() if throws else None,
                "era": stats.get("era"),
                "whip": stats.get("whip"),
                "k9": stats.get("k9"),
            }
        h_pitcher = _pitcher_dict(hp)
        a_pitcher = _pitcher_dict(ap)
        idx._by_pair[(h_abbr, a_abbr)] = {
            "home": h_pitcher,
            "away": a_pitcher,
        }
    logger.info(
        "[MLB_PROBABLE_PITCHER] index built date=%s games=%d pairs=%d "
        "pitcher_stats_fetched=%d",
        date_iso, len(games), len(idx), len(stats_map),
    )
    return idx


async def get_probable_pitcher_index(
    date_iso: str,
) -> ProbablePitcherIndex:
    """TTL-cached wrapper around ``build_probable_pitcher_index``."""
    now = time.monotonic()
    cached = _INDEX_CACHE.get(date_iso)
    if cached and (now - cached[0] < _CACHE_TTL_SEC):
        return cached[1]
    idx = await build_probable_pitcher_index(date_iso)
    _INDEX_CACHE[date_iso] = (now, idx)
    return idx


# Synchronous wrapper for use inside non-async hydrate paths
# (``feature_hydration.py`` runs sync inside an asyncio task).
def get_probable_pitcher_index_sync(
    date_iso: str,
) -> ProbablePitcherIndex:
    """Sync helper. Detects an existing event loop and dispatches to
    a background loop if needed."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is None:
        return asyncio.run(get_probable_pitcher_index(date_iso))
    # Running inside an event loop → use a fresh thread.
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(
            asyncio.run, get_probable_pitcher_index(date_iso),
        ).result()


def reset_cache() -> None:
    """For tests."""
    _INDEX_CACHE.clear()


__all__ = [
    "ProbablePitcherIndex",
    "build_probable_pitcher_index",
    "get_probable_pitcher_index",
    "get_probable_pitcher_index_sync",
    "reset_cache",
]

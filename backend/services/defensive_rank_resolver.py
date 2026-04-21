"""
Canonical Opponent Defensive-Rank Resolver  (multi-sport)
========================================================

ONE source of truth for `opponent_defensive_rank` across every sport.

Design rules (locked):
  * Every scored prop gets `opponent_defensive_rank` written at scoring time.
  * No downstream layer (Gemini, UI, caches) is allowed to compute its own rank.
  * If rank is not live / not available → return (None, "unavailable").
  * NEVER fall back to the static `config.settings.DVP_RANKINGS` table for live
    flows. That table is last-season data and was the root cause of the
    "24th-ranked Spurs" hallucination bug (see 2026-04-21 trace).

Provider routing:
  * NBA  → services.dvp_service._get_defensive_rank (BDL-backed, STRICT: no static)
  * MLB  → opponent-team defensive rank by stat is not a meaningful signal in the
           current MLB pipeline (matchup is pitcher-based). Returns (None, "unavailable").
  * NFL  → no provider yet; returns (None, "unavailable") — ready for plug-in
           by registering a resolver in `_PROVIDERS`.

Contract returned:
  (rank: Optional[int], source: str)
    source ∈ {"bdl_live", "unavailable", "<future-provider>"}

Multi-sport guarantee:
  Adding NFL (or any future sport) requires ONLY adding a provider function and
  registering it in `_PROVIDERS`. No changes to the pipeline, Gemini payload,
  API layer, or frontend.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

Rank = Optional[int]
Source = str
Provider = Callable[[str, str], Tuple[Rank, Source]]
Prewarm = Callable[[], Awaitable[None]]

_SOURCE_UNAVAILABLE = "unavailable"
_SOURCE_BDL_LIVE = "bdl_live"


# ---------------------------------------------------------------------------
# NBA provider (BDL-backed, STRICT)
# ---------------------------------------------------------------------------
def _nba_provider(opponent: str, stat_type: str) -> Tuple[Rank, Source]:
    """Resolve from services.dvp_service WITHOUT static fallback."""
    try:
        from services.dvp_service import (
            _dvp_cache,
            DvPDataSource,
        )
        from config.settings import STAT_TYPE_MAP

        # Only trust live / cached BDL data — refuse static fallback.
        if _dvp_cache is None or _dvp_cache.is_expired:
            return None, _SOURCE_UNAVAILABLE
        if _dvp_cache.source == DvPDataSource.STATIC_FALLBACK:
            return None, _SOURCE_UNAVAILABLE

        stat_key = STAT_TYPE_MAP.get(stat_type, stat_type.upper())
        rankings = _dvp_cache.rankings or {}
        rank = rankings.get(stat_key, {}).get(opponent)
        if rank is None:
            return None, _SOURCE_UNAVAILABLE
        return int(rank), _SOURCE_BDL_LIVE
    except Exception as e:  # pragma: no cover — defensive
        logger.debug(f"[DEF_RANK] NBA provider error: {e}")
        return None, _SOURCE_UNAVAILABLE


async def _nba_prewarm() -> None:
    """Ensure NBA DvP cache is warm before scoring-time annotation."""
    try:
        from services.dvp_service import fetch_live_dvp, _dvp_cache, DvPDataSource
        if _dvp_cache and not _dvp_cache.is_expired and _dvp_cache.source != DvPDataSource.STATIC_FALLBACK:
            return
        await fetch_live_dvp()
    except Exception as e:  # pragma: no cover — defensive
        logger.debug(f"[DEF_RANK] NBA prewarm error: {e}")


# ---------------------------------------------------------------------------
# MLB provider — intentionally no team-defense rank in current pipeline.
# ---------------------------------------------------------------------------
def _mlb_provider(opponent: str, stat_type: str) -> Tuple[Rank, Source]:
    return None, _SOURCE_UNAVAILABLE


async def _mlb_prewarm() -> None:  # no-op
    return None


# ---------------------------------------------------------------------------
# Provider registry (add NFL here when a data source lands)
# ---------------------------------------------------------------------------
_PROVIDERS: Dict[str, Provider] = {
    "nba": _nba_provider,
    "mlb": _mlb_provider,
    # "nfl": _nfl_provider,    # future – plug in without touching callers
}

_PREWARMERS: Dict[str, Prewarm] = {
    "nba": _nba_prewarm,
    "mlb": _mlb_prewarm,
}


def register_provider(
    sport: str,
    provider: Provider,
    prewarm: Optional[Prewarm] = None,
) -> None:
    """Register / override a provider for a given sport (tests, future sports)."""
    key = sport.lower()
    _PROVIDERS[key] = provider
    if prewarm is not None:
        _PREWARMERS[key] = prewarm


async def ensure_provider_warm(sport: str) -> None:
    """Give the sport's provider a chance to warm its cache. Never raises."""
    warmer = _PREWARMERS.get((sport or "").lower())
    if warmer is None:
        return
    try:
        await warmer()
    except Exception as e:  # pragma: no cover — defensive
        logger.debug(f"[DEF_RANK] prewarm({sport}) error: {e}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def get_opponent_defensive_rank(
    sport: Optional[str],
    opponent: Optional[str],
    stat_type: Optional[str],
) -> Tuple[Rank, Source]:
    """Canonical multi-sport resolver.

    Returns (rank, source). When data is unavailable for any reason
    (unknown sport, missing args, stale cache, unsupported stat) the
    result is `(None, "unavailable")` — callers must NOT invent a value.
    """
    if not sport or not opponent or not stat_type:
        return None, _SOURCE_UNAVAILABLE
    provider = _PROVIDERS.get(sport.lower())
    if provider is None:
        return None, _SOURCE_UNAVAILABLE
    try:
        return provider(opponent, stat_type)
    except Exception as e:  # pragma: no cover — defensive
        logger.debug(f"[DEF_RANK] provider error sport={sport}: {e}")
        return None, _SOURCE_UNAVAILABLE


def annotate_defensive_rank(picks, sport: str) -> int:
    """In-place annotate every pick with the canonical three fields.

    Writes on every pick (even when unavailable) so the contract is stable:
        opponent_defensive_rank        -> int | None
        opponent_defensive_source      -> "bdl_live" | "unavailable" | ...
        opponent_defensive_stat_type   -> str | None   (normalized stat key used)

    Returns number of picks where a live rank was resolved.
    """
    resolved = 0
    for p in picks:
        if not isinstance(p, dict):
            continue
        opponent = p.get("opponent") or p.get("opponent_abbr")
        stat_type = p.get("stat_type")
        rank, source = get_opponent_defensive_rank(sport, opponent, stat_type)
        p["opponent_defensive_rank"] = rank
        p["opponent_defensive_source"] = source
        p["opponent_defensive_stat_type"] = stat_type
        if rank is not None:
            resolved += 1
    return resolved


__all__ = [
    "get_opponent_defensive_rank",
    "annotate_defensive_rank",
    "register_provider",
    "ensure_provider_warm",
]

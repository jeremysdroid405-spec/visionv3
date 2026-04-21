"""
Canonical Player-Usage Resolver (multi-sport)
=============================================

Single source of truth for `usage_score` used by any feature that ranks
teammates by "how much they benefit" from a roster change (injury vacuum,
lineup ripple, future NFL depth-chart adjacency, etc.).

Design rules (locked):
  * Every caller gets a deterministic (score, source) tuple.
  * NBA score = blend of usage_percentage + minutes_per_game, pulled from
    the canonical hub collection.
  * MLB / NFL default to `(None, "unavailable")`. Provider-pluggable.
  * NEVER fall back to loop-order / iteration position. That was the root
    cause of the 2026-04-21 beneficiary-ordering bug.

Contract:
    get_player_usage_score(sport, player_name)  -> (float|None, source_str)
        source ∈ {"nba_hub", "star_usage_cache", "unavailable", ...}

Multi-sport guarantee:
  Adding MLB / NFL requires ONLY registering a provider; no changes to the
  beneficiary-ranking code, Ferrari routes, or frontend.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

Score = Optional[float]
Source = str
Provider = Callable[["object", str], Awaitable[Tuple[Score, Source]]]

_UNAVAILABLE = "unavailable"


# ---------------------------------------------------------------------------
# NBA provider — reads from nba_master_hub_2026, falls back to star_usage_cache
# ---------------------------------------------------------------------------
def _nba_blend(usage_pct: Optional[float], minutes: Optional[float]) -> Optional[float]:
    """Combine usage % (0-100) with minutes/game (0-48).

    Rationale:
      * A 30% usage player playing 14 min/g is a spot rotation piece, not
        a primary beneficiary. Multiplying by `minutes / 36` downweights
        low-minutes stars.
      * A 15% usage defensive starter playing 35 min/g can legitimately
        beat a 25% usage bench player at 18 min/g for role-stability
        purposes.

    Returns None if both inputs are missing.
    """
    if usage_pct is None and minutes is None:
        return None
    u = float(usage_pct) if usage_pct is not None else 0.0
    m = float(minutes) if minutes is not None else 0.0
    # Normalize minutes to a [0, 1] factor; hard cap at 36 = starter benchmark.
    m_factor = max(0.0, min(m / 36.0, 1.0))
    # Blend: 70% usage, 30% role-stability from minutes.
    return round(u * (0.70 + 0.30 * m_factor), 3)


async def _nba_provider(db, player_name: str) -> Tuple[Score, Source]:
    if not player_name:
        return None, _UNAVAILABLE
    key = player_name.strip()
    if not key:
        return None, _UNAVAILABLE
    try:
        # Primary: nba_master_hub_2026.advanced_stats
        doc = await db.nba_master_hub_2026.find_one(
            {"$or": [
                {"display_name": key},
                {"display_name_bdl": key},
                {"normalized_name": key.lower()},
            ]},
            {"_id": 0, "advanced_stats": 1},
        )
        adv = (doc or {}).get("advanced_stats") or {}
        usage = adv.get("usage_percentage") or adv.get("usg_pct")
        mins = adv.get("minutes_per_game")
        score = _nba_blend(usage, mins)
        if score is not None:
            return score, "nba_hub"
    except Exception as e:
        logger.debug(f"[USAGE] nba_hub lookup failed for {key}: {e}")

    try:
        # Secondary: star_usage_cache (less coverage, star-only)
        cdoc = await db.star_usage_cache.find_one(
            {"player_name": key},
            {"_id": 0, "usage_percentage": 1, "usage_pct": 1},
        )
        if cdoc:
            usage = cdoc.get("usage_percentage") or cdoc.get("usage_pct")
            score = _nba_blend(usage, None)
            if score is not None:
                return score, "star_usage_cache"
    except Exception as e:
        logger.debug(f"[USAGE] star_usage_cache lookup failed for {key}: {e}")

    return None, _UNAVAILABLE


# ---------------------------------------------------------------------------
# MLB provider — no usage % for batting/pitching in this pipeline (yet)
# ---------------------------------------------------------------------------
async def _mlb_provider(db, player_name: str) -> Tuple[Score, Source]:
    return None, _UNAVAILABLE


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
_PROVIDERS: Dict[str, Provider] = {
    "nba": _nba_provider,
    "mlb": _mlb_provider,
    # "nfl": _nfl_provider,  # future – plug in without touching callers
}


def register_provider(sport: str, provider: Provider) -> None:
    """Register/override a provider for a given sport (tests, future sports)."""
    _PROVIDERS[sport.lower()] = provider


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
async def get_player_usage_score(
    db, sport: Optional[str], player_name: Optional[str]
) -> Tuple[Score, Source]:
    """Return (usage_score, source) for a player in a given sport."""
    if not sport or not player_name:
        return None, _UNAVAILABLE
    provider = _PROVIDERS.get(sport.lower())
    if provider is None:
        return None, _UNAVAILABLE
    try:
        return await provider(db, player_name)
    except Exception as e:
        logger.debug(f"[USAGE] provider error sport={sport}: {e}")
        return None, _UNAVAILABLE


async def rank_teammates_by_usage(
    db, sport: str, teammates: list
) -> list:
    """Return teammates sorted by usage score DESC.

    Each input dict is mutated in-place to carry canonical fields:
        usage_score         -> float | None
        usage_source        -> str
        usage_rank          -> 1-based deterministic rank

    Ties are broken alphabetically on `player_name` for determinism (so the
    same input always produces the same order — no loop-order surprises).
    Returns the sorted list (same dicts, new order).
    """
    if not teammates:
        return teammates
    for t in teammates:
        if not isinstance(t, dict):
            continue
        score, source = await get_player_usage_score(db, sport, t.get("player_name"))
        t["usage_score"] = score
        t["usage_source"] = source
    ranked = sorted(
        [t for t in teammates if isinstance(t, dict)],
        key=lambda t: (
            -(t.get("usage_score") or -1.0),  # usage DESC, unresolved sinks
            (t.get("player_name") or "").lower(),  # deterministic tiebreak
        ),
    )
    for i, t in enumerate(ranked, 1):
        t["usage_rank"] = i
    return ranked


__all__ = [
    "get_player_usage_score",
    "rank_teammates_by_usage",
    "register_provider",
]

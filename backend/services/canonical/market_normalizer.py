"""Universal market-key normalizer (sport-agnostic SSOT).

Bridges book-specific / alt-ladder / standard-market naming variants
to the canonical `stat_family` used by `services.scoring.canonical_stats`.

This is the SINGLE entry point used by the canonical-prop builder to
resolve "what stat is this prop about?" — replacing the scattered
mini-maps in `mlb_feature_cache._STAT_FAMILY_MAP`,
`mlb_adapter._resolve_mlb_family`, and the replay engine's internal
alias dict.

Sport-agnostic: rules per sport are pluggable. No hard-coded MLB-only
shortcuts in the core function.
"""
from __future__ import annotations
from typing import Dict, Optional, Tuple

from services.scoring.canonical_stats import (
    market_to_stat_map, stat_family as _canonical_stat_family,
)


# Book-specific market aliases that don't go through the Odds-API path.
# Maps {sport: {raw_market_alias: canonical_odds_api_market}}.
# This is intentionally tiny — most MLB markets are emitted by the
# Odds API directly. Add aliases here only when a book uses a
# distinct key (e.g. PrizePicks-style `player_hits` vs Odds API's
# `batter_hits`).
_MARKET_ALIASES: Dict[str, Dict[str, str]] = {
    "mlb": {
        "player_hits":       "batter_hits",
        "player_total_bases": "batter_total_bases",
        "player_strikeouts":  "batter_strikeouts",
        "player_home_runs":   "batter_home_runs",
        "player_rbis":        "batter_rbis",
        "player_runs":        "batter_runs_scored",
        "hits_alt":           "batter_hits_alternate",
        "total_bases_alt":    "batter_total_bases_alternate",
    },
    "nba": {},  # NBA odds-api keys already canonical; no aliases needed.
    "nfl": {},
}


def _strip_alt_suffix(market: str) -> Tuple[str, bool]:
    """Returns (root, is_alternate). `is_alternate` is True if the
    market name ends with `_alternate` (the universal Odds-API alt
    suffix). Other alt indicators are handled via `_MARKET_ALIASES`
    which map them onto the canonical `_alternate` form first."""
    m = (market or "").lower().strip()
    if m.endswith("_alternate"):
        return m[: -len("_alternate")], True
    return m, False


def normalize_market(
    sport: str, market: str,
) -> Tuple[Optional[str], str, bool]:
    """Return (stat_family, canonical_market_key, is_alternate).

    - `stat_family`: e.g. `"hits"`, `"total_bases"`, `"batter_strikeouts"`.
       None when unknown (caller must fail-closed, NOT silently default).
    - `canonical_market_key`: the canonical Odds-API-shaped market name
       AFTER alias resolution AND `_alternate` stripping. Used by the
       canonical prop key to collapse std + alt onto the same prop.
       Example: `batter_hits_alternate` → `batter_hits`.
    - `is_alternate`: True iff the raw market is alt-ladder.
    """
    sport_lc = (sport or "").lower()
    raw = (market or "").lower().strip()
    if not raw:
        return None, raw, False
    # Step 1: book-specific alias → odds-api form
    aliases = _MARKET_ALIASES.get(sport_lc, {})
    canonical_raw = aliases.get(raw, raw)
    # Step 2: strip _alternate suffix
    root, is_alt = _strip_alt_suffix(canonical_raw)
    # Step 3: ask the SSOT canonical_stats registry for the family
    market_map = market_to_stat_map(sport_lc)
    stat_type = market_map.get(root) or market_map.get(canonical_raw)
    if not stat_type:
        return None, root, is_alt
    family = _canonical_stat_family(sport_lc, stat_type, strict=False)
    return family, root, is_alt


__all__ = ["normalize_market"]

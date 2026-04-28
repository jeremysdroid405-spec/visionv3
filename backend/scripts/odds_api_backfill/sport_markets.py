"""
Sport-specific market configuration for the historical Odds API backfill.

Each entry maps a `sport_key` (the Odds API canonical id) to:
  * `markets`  — list of market keys to request
  * `family`   — market_key → canonical stat_family code
  * `combos`   — set of stat_family codes that are combo markets
  * `validation_required_families` — families whose presence after a
                                      single-slate ingest proves the
                                      sport's full coverage materialized
"""
from __future__ import annotations

from typing import Dict, FrozenSet, List


# ---------------------------------------------------------------------------
# basketball_nba — UNCHANGED from the original NBA-only list
# ---------------------------------------------------------------------------
_NBA_MARKETS: List[str] = [
    "player_points_rebounds_assists",
    "player_points_rebounds_assists_alternate",
    "player_points_alternate",
    "player_rebounds_alternate",
    "player_assists_alternate",
    "player_threes_alternate",
    "player_points_rebounds_alternate",
    "player_points_assists_alternate",
    "player_rebounds_assists_alternate",
    "player_blocks",
    "player_steals",
    "player_double_double",
]

_NBA_FAMILY: Dict[str, str] = {
    "player_points":              "PTS",
    "player_points_alternate":    "PTS",
    "player_rebounds":            "REB",
    "player_rebounds_alternate":  "REB",
    "player_assists":             "AST",
    "player_assists_alternate":   "AST",
    "player_threes":              "THREES",
    "player_threes_alternate":    "THREES",
    "player_blocks":              "BLK",
    "player_blocks_alternate":    "BLK",
    "player_steals":              "STL",
    "player_steals_alternate":    "STL",
    "player_turnovers":           "TURNOVERS",
    "player_double_double":       "DOUBLE_DOUBLE",
    "player_points_rebounds":               "PTS_REB",
    "player_points_rebounds_alternate":     "PTS_REB",
    "player_points_assists":                "PTS_AST",
    "player_points_assists_alternate":      "PTS_AST",
    "player_rebounds_assists":              "REB_AST",
    "player_rebounds_assists_alternate":    "REB_AST",
    "player_points_rebounds_assists":            "PRA",
    "player_points_rebounds_assists_alternate":  "PRA",
}

_NBA_COMBOS: FrozenSet[str] = frozenset({"PRA", "PTS_REB", "PTS_AST", "REB_AST"})


# ---------------------------------------------------------------------------
# baseball_mlb — placeholder per user spec
# ---------------------------------------------------------------------------
_MLB_MARKETS: List[str] = [
    "batter_hits",
    "batter_total_bases",
    "batter_home_runs",
    "batter_rbis",
    "batter_runs_scored",
    "batter_hits_runs_rbis",
    "pitcher_strikeouts",
    "pitcher_hits_allowed",
    "pitcher_walks",
    "pitcher_earned_runs",
]

_MLB_FAMILY: Dict[str, str] = {
    "batter_hits":           "HITS",
    "batter_total_bases":    "TOTAL_BASES",
    "batter_home_runs":      "HOME_RUNS",
    "batter_rbis":           "RBIS",
    "batter_runs_scored":    "RUNS",
    "batter_hits_runs_rbis": "HITS_RUNS_RBIS",
    "pitcher_strikeouts":    "K",
    "pitcher_hits_allowed":  "HITS_ALLOWED",
    "pitcher_walks":         "WALKS",
    "pitcher_earned_runs":   "ER",
}

_MLB_COMBOS: FrozenSet[str] = frozenset({"HITS_RUNS_RBIS"})


# ---------------------------------------------------------------------------
# americanfootball_nfl / icehockey_nhl — placeholder hooks. Empty market
# lists keep the CLI flag valid but error gracefully if invoked before
# being populated.
# ---------------------------------------------------------------------------
_NFL_MARKETS: List[str] = []
_NHL_MARKETS: List[str] = []


# ---------------------------------------------------------------------------
# Master registry
# ---------------------------------------------------------------------------
SPORT_CONFIG: Dict[str, Dict] = {
    "basketball_nba": {
        "markets": _NBA_MARKETS,
        "family":  _NBA_FAMILY,
        "combos":  _NBA_COMBOS,
        # Single-slate validation pass criteria. Family codes that MUST
        # appear after one slate ingest for the backfill to be considered
        # working for that sport.
        "validation_required_families": frozenset({"PRA", "PTS"}),
    },
    "baseball_mlb": {
        "markets": _MLB_MARKETS,
        "family":  _MLB_FAMILY,
        "combos":  _MLB_COMBOS,
        "validation_required_families": frozenset({"HITS", "HOME_RUNS"}),
    },
    "americanfootball_nfl": {
        "markets": _NFL_MARKETS,
        "family":  {},
        "combos":  frozenset(),
        "validation_required_families": frozenset(),
    },
    "icehockey_nhl": {
        "markets": _NHL_MARKETS,
        "family":  {},
        "combos":  frozenset(),
        "validation_required_families": frozenset(),
    },
}

SUPPORTED_SPORTS = tuple(SPORT_CONFIG.keys())
DEFAULT_SPORT = "basketball_nba"


def markets_for(sport_key: str) -> List[str]:
    cfg = SPORT_CONFIG.get(sport_key)
    if not cfg:
        raise ValueError(f"Unsupported sport_key: {sport_key!r}. "
                          f"Supported: {SUPPORTED_SPORTS}")
    if not cfg["markets"]:
        raise ValueError(f"sport_key {sport_key!r} has no markets configured "
                          f"yet. Populate `sport_markets.py` first.")
    return list(cfg["markets"])


def market_to_family(sport_key: str, market_key: str) -> str:
    """Canonical stat_family for `(sport_key, market_key)`.
    Falls back to upper-cased market_key when no mapping exists so we
    never lose data for a brand-new market the API ships."""
    cfg = SPORT_CONFIG.get(sport_key) or {}
    return cfg.get("family", {}).get(market_key, market_key.upper())


def is_combo(sport_key: str, market_key: str) -> bool:
    fam = market_to_family(sport_key, market_key)
    cfg = SPORT_CONFIG.get(sport_key) or {}
    return fam in cfg.get("combos", frozenset())


def is_alternate(market_key: str) -> bool:
    """Sport-agnostic — every Odds API market that supports `_alternate`
    uses the same suffix convention."""
    return market_key.endswith("_alternate")


def required_validation_families(sport_key: str) -> FrozenSet[str]:
    cfg = SPORT_CONFIG.get(sport_key) or {}
    return cfg.get("validation_required_families", frozenset())


__all__ = [
    "DEFAULT_SPORT", "SUPPORTED_SPORTS", "SPORT_CONFIG",
    "markets_for", "market_to_family", "is_combo", "is_alternate",
    "required_validation_families",
]

"""Universal Gate Engine — threshold configuration.

Shape:

    THRESHOLDS[sport][tier][stat_family] = {
        "coverage_gate":  {"min_books": int},
        "hit_rate_gate":  {"min": float, "window": "l20|l10|l5|default"},
        "tp_gate":        {"min": float, "under_floor": Optional[float]},
        "cv_gate":        {"max": float},
        "edge_gate":      {"min": float},
        "ceiling_gate":   {"min": float},
        "context_gate":   {"vetoes": [str, ...]}   # list of veto keys
    }

Any gate whose config is absent is not evaluated.
Adding a new sport is a pure data change.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


TIERS_ORDERED = ("safe_haven", "front_lines", "war_zone")


# --------------------------------------------------------------------------
# Stat family aliases (normalize adapter-specific stat names)
# --------------------------------------------------------------------------
STAT_FAMILY_ALIASES: Dict[str, Dict[str, str]] = {
    "nba": {
        "PTS": "pts", "REB": "reb", "AST": "ast", "PRA": "pra",
        "STL": "stl", "BLK": "blk", "3PM": "threes", "TO": "turnovers",
    },
    "mlb": {
        "hits": "hits", "total_bases": "total_bases",
        "hits+runs+rbis": "hits_runs_rbis",
        "rbis": "rbis", "runs": "runs",
        "pitching_outs": "pitching_outs",
        "pitcher_strikeouts": "pitcher_strikeouts",
        "earned_runs": "earned_runs",
    },
    "nfl": {
        # Ready-to-fill scaffold. The engine already supports NFL once
        # the adapter + thresholds land.
        "receptions": "receptions",
        "rushing_yards": "rushing_yards",
        "receiving_yards": "receiving_yards",
        "passing_yards": "passing_yards",
    },
}


def resolve_stat_family(sport: str, raw_stat: Optional[str]) -> str:
    """Return the canonical stat family for a sport/raw-stat pair.

    Falls back to the lower-cased raw stat so a new stat type still
    routes somewhere (it will land on the sport's ``_default`` config
    if no direct entry exists).
    """
    if not raw_stat:
        return "_default"
    alias_map = STAT_FAMILY_ALIASES.get((sport or "").lower(), {})
    if raw_stat in alias_map:
        return alias_map[raw_stat]
    return raw_stat.strip().lower().replace(" ", "_")


# --------------------------------------------------------------------------
# Gate thresholds — config-driven, sport × tier × stat_family
# --------------------------------------------------------------------------
# CV thresholds for NBA Safe Haven are stat-dependent; we read the
# canonical cap via `services.scoring.cv_caps.resolve_cv_cap` inside
# the engine rather than duplicating it here.
_NBA_SAFE_HAVEN_BASE = {
    "coverage_gate": {"min_books": 1},
    "hit_rate_gate": {"min": 75.0, "window": "default"},
    "tp_gate":       {"min": 70.0, "under_floor": 75.0},
    "cv_gate":       {"max": 0.50},  # PTS/PRA default; overridden per-stat by resolve_cv_cap
    "edge_gate":     {"min": 8.0},
}
_NBA_FRONT_LINES_BASE = {
    "coverage_gate": {"min_books": 1},
    "hit_rate_gate": {"min": 60.0, "window": "default"},
    "tp_gate":       {"min": 55.0, "under_floor": 65.0},
    "cv_gate":       {"max": 0.75},
    "edge_gate":     {"min": 5.0},
}
_NBA_WAR_ZONE_BASE = {
    "coverage_gate": {"min_books": 1},
    "cv_gate":       {"min_cv_floor": 0.45},   # inverted: cv must be >= floor
    "ceiling_gate":  {"min": 20.0},
    "edge_gate":     {"min": 10.0},
}


# MLB safe-haven per-stat gates preserved from services/mlb_tier_sorter.py
_MLB_SAFE_HAVEN: Dict[str, Dict[str, Any]] = {
    "hits":              {"cv_max": 0.60, "hr_min": 80.0, "edge_min": 15.0, "tp_min": 70.0},
    "total_bases":       {"cv_max": 0.75, "hr_min": 75.0, "edge_min": 20.0, "tp_min": 70.0},
    "hits_runs_rbis":    {"cv_max": 0.55, "hr_min": 80.0, "edge_min": 18.0, "tp_min": 70.0},
    "rbis":              {"cv_max": 0.55, "hr_min": 80.0, "edge_min": 18.0, "tp_min": 70.0},
    "runs":              {"cv_max": 0.55, "hr_min": 80.0, "edge_min": 18.0, "tp_min": 70.0},
    "pitching_outs":     {"cv_max": 0.30, "hr_min": 85.0, "edge_min": 8.0,  "tp_min": 80.0},
    "pitcher_strikeouts":{"cv_max": 0.45, "hr_min": 75.0, "edge_min": 12.0, "tp_min": 75.0},
    "earned_runs":       {"cv_max": 0.40, "hr_min": 75.0, "edge_min": 10.0, "tp_min": 75.0},
    "_default":          {"cv_max": 0.60, "hr_min": 80.0, "edge_min": 15.0, "tp_min": 70.0},
}
_MLB_FRONT_LINES: Dict[str, Dict[str, Any]] = {
    "hits":              {"cv_max": 0.85, "hr_min": 65.0, "edge_min": 10.0, "tp_min": 58.0},
    "total_bases":       {"cv_max": 0.95, "hr_min": 60.0, "edge_min": 15.0, "tp_min": 58.0},
    "hits_runs_rbis":    {"cv_max": 0.75, "hr_min": 65.0, "edge_min": 12.0, "tp_min": 58.0},
    "rbis":              {"cv_max": 0.75, "hr_min": 65.0, "edge_min": 12.0, "tp_min": 58.0},
    "runs":              {"cv_max": 0.75, "hr_min": 65.0, "edge_min": 12.0, "tp_min": 58.0},
    "pitching_outs":     {"cv_max": 0.50, "hr_min": 70.0, "edge_min": 6.0,  "tp_min": 70.0},
    "pitcher_strikeouts":{"cv_max": 0.60, "hr_min": 65.0, "edge_min": 10.0, "tp_min": 65.0},
    "earned_runs":       {"cv_max": 0.55, "hr_min": 65.0, "edge_min": 8.0,  "tp_min": 65.0},
    "_default":          {"cv_max": 0.85, "hr_min": 65.0, "edge_min": 10.0, "tp_min": 58.0},
}
_MLB_WAR_ZONE: Dict[str, Dict[str, Any]] = {
    "hits":              {"ceiling_min": 35.0, "edge_min": 30.0},
    "total_bases":       {"ceiling_min": 35.0, "edge_min": 30.0},
    "hits_runs_rbis":    {"ceiling_min": 35.0, "edge_min": 30.0},
    "rbis":              {"ceiling_min": 35.0, "edge_min": 30.0},
    "runs":              {"ceiling_min": 35.0, "edge_min": 30.0},
    "pitcher_strikeouts":{"ceiling_min": 30.0, "edge_min": 25.0},
    "_default":          {"ceiling_min": 35.0, "edge_min": 30.0},
}

_MLB_GOBLIN_LINE_OVERRIDE = {
    "cv_max": 1.10, "hr_min": 75.0, "edge_min": -9999.0, "tp_min": 60.0,
}


def _mlb_thresholds(per_stat: Dict[str, Dict[str, Any]], *, war_zone: bool = False) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for family, vals in per_stat.items():
        if war_zone:
            out[family] = {
                "coverage_gate": {"min_books": 1},
                "ceiling_gate":  {"min": vals["ceiling_min"]},
                "edge_gate":     {"min": vals["edge_min"]},
            }
        else:
            out[family] = {
                "coverage_gate": {"min_books": 1},
                "cv_gate":       {"max": vals["cv_max"]},
                "hit_rate_gate": {"min": vals["hr_min"], "window": "default"},
                "edge_gate":     {"min": vals["edge_min"]},
                "tp_gate":       {"min": vals["tp_min"]},
            }
    return out


THRESHOLDS: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]] = {
    "nba": {
        "safe_haven":  {"_default": _NBA_SAFE_HAVEN_BASE},
        "front_lines": {"_default": _NBA_FRONT_LINES_BASE},
        "war_zone":    {"_default": _NBA_WAR_ZONE_BASE},
    },
    "mlb": {
        "safe_haven":  _mlb_thresholds(_MLB_SAFE_HAVEN),
        "front_lines": _mlb_thresholds(_MLB_FRONT_LINES),
        "war_zone":    _mlb_thresholds(_MLB_WAR_ZONE, war_zone=True),
    },
    # Drop NFL adapter in place and start tuning here — engine works
    # as-soon-as the table is populated.
    "nfl": {
        "safe_haven":  {},
        "front_lines": {},
        "war_zone":    {},
    },
}


def resolve_thresholds(
    sport: str, tier: str, stat_family: str,
) -> Dict[str, Dict[str, Any]]:
    """Return the active threshold block for (sport, tier, stat_family).

    Falls back from explicit stat_family → sport-tier ``_default`` → {}.
    """
    by_sport = THRESHOLDS.get((sport or "").lower(), {})
    by_tier = by_sport.get(tier, {})
    if stat_family in by_tier:
        return by_tier[stat_family]
    return by_tier.get("_default", {})


# Odds-bucket routing — engine uses this to derive the TARGET tier for a
# pick from its reference odds. Same config treatment as gates.
ODDS_BUCKETS: Dict[str, Dict[str, Any]] = {
    "nba": {
        "safe_haven_max":  -250,
        "war_zone_min":     150,
        # anything strictly between the two is front_lines
    },
    "mlb": {
        "safe_haven_max":  -240,
        "war_zone_min":     150,
    },
    "nfl": {
        "safe_haven_max":  -250,
        "war_zone_min":     150,
    },
}


def resolve_target_tier(sport: str, reference_odds: Optional[int]) -> Optional[str]:
    """Map (sport, reference_odds) → target tier name, or None if no odds."""
    if reference_odds is None:
        return None
    cfg = ODDS_BUCKETS.get((sport or "").lower(), ODDS_BUCKETS["nba"])
    if reference_odds <= cfg["safe_haven_max"]:
        return "safe_haven"
    if reference_odds >= cfg["war_zone_min"]:
        return "war_zone"
    return "front_lines"

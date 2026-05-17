"""PrizePicks playability registry — hardcoded SSOT (Phase A fallback).

Used by `apply_production_eligibility` (and the future Phase B
`HistoricalInputProvider`) ONLY when the prop dict does not carry a
trustworthy `playable_on_pp` / `pp_layer` projection (i.e. historical
/ test inputs that pre-date PrizePicks ingestion).

Live production path is **unaffected** by this registry — live props
carry `pp_layer` set by `universal_odds_sync._normalize_market_data`
and the live filter trusts that field directly.

Contract:
  • Keyed by `(sport, stat_family, side)` → bool.
  • Returns True iff PrizePicks is structurally known to list that
    side for that stat family.
  • Fails CLOSED for unknown families — i.e. returns False — so
    historical mode never invents playability.

Sources of truth for the listings below:
  • Live MLB snapshot 2026-05-17 (3,551 non-playable + 1,268
    playable rows, side breakdown confirmed in audit
    `/app/backend/audits/PHASE6_PHASE2_REPORT_2026_05_17.md` and the
    follow-up live-vs-replay parity audit).
  • Live NBA SSOT contract documented in `coverage_filter.py:200-227`
    and verified via `prop_scores` collection.
  • NFL: registry SCAFFOLD only — empty until NFL goes live.

NB: This registry encodes the STRUCTURAL listing contract — i.e.
"PP categorically does not list X UNDER". Per-slate variations
(e.g. PP listing pitcher_strikeouts on some days and not others)
are handled by the live `pp_layer` field; the registry is only the
fail-closed baseline.
"""
from __future__ import annotations

from typing import Dict, FrozenSet, Tuple


# Each value is the set of sides PrizePicks lists for that stat
# family. Missing keys → False (fail closed).
SPORT_PP_SIDE_REGISTRY: Dict[str, Dict[str, FrozenSet[str]]] = {
    "mlb": {
        # Batter markets — both sides
        "hits":            frozenset({"OVER", "UNDER"}),
        "total_bases":     frozenset({"OVER", "UNDER"}),
        "hits_runs_rbis":  frozenset({"OVER", "UNDER"}),
        "runs":            frozenset({"OVER", "UNDER"}),
        "singles":         frozenset({"OVER", "UNDER"}),
        "batter_walks":    frozenset({"OVER", "UNDER"}),
        # Batter markets — OVER only on PP
        "rbis":            frozenset({"OVER"}),
        "home_runs":       frozenset({"OVER"}),
        "doubles":         frozenset({"OVER"}),
        # Batter strikeouts — PP lists this family OVER-only. The
        # MLB live adapter emits `stat_family="batter_strikeouts"`
        # (canonical) AND `stat_family="strikeouts"` (alias used by
        # the replay model). Register both so the registry is
        # alias-insensitive for the Phase A fallback.
        "batter_strikeouts": frozenset({"OVER"}),
        "strikeouts":        frozenset({"OVER"}),
        # Pitcher markets — both sides on PP when listed at all.
        # PP lists pitcher_strikeouts conditionally per slate; the
        # registry is the structural baseline only — actual
        # per-snapshot playability is enforced by the live
        # `pp_layer` field when available.
        "pitcher_strikeouts": frozenset({"OVER", "UNDER"}),
        "pitcher_walks":      frozenset({"OVER", "UNDER"}),
        "earned_runs":        frozenset({"OVER", "UNDER"}),
    },
    "nba": {
        # NBA: PP lists both sides for all of the standard stat
        # families exposed in the canonical pool. Sourced from
        # `services/scoring/adapters/nba_scoring.py` + production
        # `nba_prop_scores` rows over the 2026-04 audit window.
        "points":             frozenset({"OVER", "UNDER"}),
        "rebounds":           frozenset({"OVER", "UNDER"}),
        "assists":            frozenset({"OVER", "UNDER"}),
        "threes":             frozenset({"OVER", "UNDER"}),
        "steals":             frozenset({"OVER", "UNDER"}),
        "blocks":             frozenset({"OVER", "UNDER"}),
        "turnovers":          frozenset({"OVER", "UNDER"}),
        "points_rebounds":          frozenset({"OVER", "UNDER"}),
        "points_assists":           frozenset({"OVER", "UNDER"}),
        "rebounds_assists":         frozenset({"OVER", "UNDER"}),
        "points_rebounds_assists":  frozenset({"OVER", "UNDER"}),
        "steals_blocks":            frozenset({"OVER", "UNDER"}),
        "free_throws_made":         frozenset({"OVER", "UNDER"}),
        "field_goals_made":         frozenset({"OVER", "UNDER"}),
        # Fantasy points — PP-listed on both sides historically.
        "fantasy_points":           frozenset({"OVER", "UNDER"}),
        # PP-only stat families (no sportsbook backbone) — these
        # never enter the canonical pool today; included here so
        # the registry is a complete SSOT.
        "minutes":            frozenset({"OVER", "UNDER"}),
    },
    "nfl": {
        # NFL scaffold — registry intentionally empty. Fail-closed
        # behaviour means historical/test NFL inputs are filtered
        # out entirely until this is populated when NFL goes live.
    },
}


def is_pp_playable_side(
    sport: str, stat_family: str | None, side: str | None,
) -> bool:
    """Return True iff PrizePicks structurally lists this side for
    this `(sport, stat_family)` per the hardcoded registry.

    Fails CLOSED:
      • Unknown sport → False.
      • Unknown stat_family for a known sport → False.
      • Missing side / stat_family → False.
    """
    if not sport or not stat_family or not side:
        return False
    fam_map = SPORT_PP_SIDE_REGISTRY.get(sport.lower())
    if not fam_map:
        return False
    allowed = fam_map.get(stat_family)
    if not allowed:
        return False
    return side.upper() in allowed


__all__ = ["SPORT_PP_SIDE_REGISTRY", "is_pp_playable_side"]

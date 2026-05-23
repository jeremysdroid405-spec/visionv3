"""
NFL stat-family ↔ SGO stat_id mapping.

Single source of truth for two things:

    NFL_FAMILY_ALIASES
        SGO stat_id strings (and common variants we've seen in raw
        SGO payloads) → canonical NFL stat_family used throughout
        the research pipeline.

    NFL_FAMILY_TO_PLAYER_STATS
        Canonical NFL stat_family → ordered list of keys to look up
        on a normalized `sgo_player_stats.stats` dict when resolving
        outcomes. The first non-None hit wins.

Both tables are intentionally small and explicit. New stat_ids from
SGO appear as `?` in the probe report; once we observe them in real
NFL data, add an alias here — that's the SOLE extension point.

Canonical families (mirror the user's spec):

    pass_yards, pass_attempts, pass_completions, pass_touchdowns,
    interceptions, rush_yards, rush_attempts, rush_touchdowns,
    receptions, receiving_yards, receiving_touchdowns,
    receiving_targets, longest_reception,
    field_goals_made, extra_points_made
"""
from __future__ import annotations
from typing import Dict, List, Tuple

# ── Canonical family list (order is the report ordering) ───────────────
NFL_FAMILIES: Tuple[str, ...] = (
    "pass_yards",
    "pass_attempts",
    "pass_completions",
    "pass_touchdowns",
    "interceptions",
    "rush_yards",
    "rush_attempts",
    "rush_touchdowns",
    "receptions",
    "receiving_yards",
    "receiving_touchdowns",
    "receiving_targets",
    "longest_reception",
    "field_goals_made",
    "extra_points_made",
)


# ── SGO stat_id (and common variants) → canonical family ───────────────
# Keys are lowercased before lookup. Multiple variants per family is
# fine; we just need at least one to match.
NFL_FAMILY_ALIASES: Dict[str, str] = {
    # Passing
    "passing_yards":       "pass_yards",
    "pass_yards":          "pass_yards",
    "passingyards":        "pass_yards",
    "qb_passing_yards":    "pass_yards",
    "passing_attempts":    "pass_attempts",
    "pass_attempts":       "pass_attempts",
    "passingattempts":     "pass_attempts",
    "passing_completions": "pass_completions",
    "pass_completions":    "pass_completions",
    "passingcompletions":  "pass_completions",
    "passing_touchdowns":  "pass_touchdowns",
    "pass_touchdowns":     "pass_touchdowns",
    "passingtouchdowns":   "pass_touchdowns",
    "passing_tds":         "pass_touchdowns",
    "passing_interceptions": "interceptions",
    "interceptions":       "interceptions",
    "passinginterceptions": "interceptions",
    "ints":                "interceptions",
    "qb_ints":             "interceptions",
    # Rushing
    "rushing_yards":       "rush_yards",
    "rush_yards":          "rush_yards",
    "rushingyards":        "rush_yards",
    "rushing_attempts":    "rush_attempts",
    "rush_attempts":       "rush_attempts",
    "rushingattempts":     "rush_attempts",
    "carries":             "rush_attempts",
    "rushing_touchdowns":  "rush_touchdowns",
    "rush_touchdowns":     "rush_touchdowns",
    "rushingtouchdowns":   "rush_touchdowns",
    "rush_tds":            "rush_touchdowns",
    # Receiving
    "receptions":          "receptions",
    "rec":                 "receptions",
    "receiving_yards":     "receiving_yards",
    "rec_yards":           "receiving_yards",
    "receivingyards":      "receiving_yards",
    "receiving_touchdowns": "receiving_touchdowns",
    "rec_touchdowns":      "receiving_touchdowns",
    "receivingtouchdowns": "receiving_touchdowns",
    "rec_tds":             "receiving_touchdowns",
    "receiving_targets":   "receiving_targets",
    "targets":             "receiving_targets",
    "receivingtargets":    "receiving_targets",
    "longest_reception":   "longest_reception",
    "rec_longest":         "longest_reception",
    "longest_rec":         "longest_reception",
    # Kicking
    "field_goals_made":    "field_goals_made",
    "fgm":                 "field_goals_made",
    "fieldgoalsmade":      "field_goals_made",
    "field_goals":         "field_goals_made",
    "extra_points_made":   "extra_points_made",
    "xpm":                 "extra_points_made",
    "extrapointsmade":     "extra_points_made",
}


def canonical_family(stat_id: str) -> str | None:
    """Lowercased lookup with snake/camel/dash insensitivity."""
    if not stat_id:
        return None
    s = (stat_id or "").strip().lower().replace("-", "_")
    return (NFL_FAMILY_ALIASES.get(s)
            or NFL_FAMILY_ALIASES.get(s.replace("_", "")))


# ── Canonical family → ordered player_stats lookup keys ───────────────
# Used by build_historical_outcomes to resolve `actual_value` from a
# normalized `sgo_player_stats.stats` dict. List order = preference.
NFL_FAMILY_TO_PLAYER_STATS: Dict[str, List[str]] = {
    "pass_yards":           ["pass_yards", "passing_yards", "passingYards"],
    "pass_attempts":        ["pass_attempts", "passing_attempts", "passingAttempts"],
    "pass_completions":     ["pass_completions", "passing_completions", "passingCompletions"],
    "pass_touchdowns":      ["pass_touchdowns", "passing_touchdowns", "passingTouchdowns"],
    "interceptions":        ["interceptions", "passing_interceptions", "passingInterceptions"],
    "rush_yards":           ["rush_yards", "rushing_yards", "rushingYards"],
    "rush_attempts":        ["rush_attempts", "rushing_attempts", "rushingAttempts", "carries"],
    "rush_touchdowns":      ["rush_touchdowns", "rushing_touchdowns", "rushingTouchdowns"],
    "receptions":           ["receptions"],
    "receiving_yards":      ["receiving_yards", "receivingYards"],
    "receiving_touchdowns": ["receiving_touchdowns", "receivingTouchdowns"],
    "receiving_targets":    ["receiving_targets", "targets", "receivingTargets"],
    "longest_reception":    ["longest_reception", "longestReception", "rec_longest"],
    "field_goals_made":     ["field_goals_made", "fieldGoalsMade", "fgm"],
    "extra_points_made":    ["extra_points_made", "extraPointsMade", "xpm"],
}

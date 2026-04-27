"""
Shared stat-family normalization (NBA + MLB).
=============================================

ONE canonical normalizer used everywhere:
  • routes/ferrari_tiers.py          (NBA picks merge)
  • routes/player.py                 (player detail)
  • services/mlb_cached_board_builder.py (MLB board enrichment)
  • validation script + regression tests

Goals (per 2026-04-27 routing-fix task):
  • Aliases collapse to ONE token per family — but **never** across
    pitcher/batter, combo/base, OVER/UNDER, or different lines.
  • Function is idempotent: passing the canonical token in returns
    the same canonical token.
  • Display label aligns with canonical token (so the UI shows
    "P+R" not "player_points_rebounds_alternate").
  • Case-insensitive.
  • Returns the original uppercased input for unknown stats so the
    aliasing is strictly additive.

NOT in scope:
  • Model scoring / LOM / probability / TP engine / gates / thresholds.
  • Frontend label translation (the UI reads the same canonical token
    we return here, so no FE change required).
"""
from __future__ import annotations

from typing import Optional

# ---------------------------------------------------------------------------
# NBA — Odds-API market names → canonical compact token.
# Compact tokens are what `nba_cached_board` already stores, so picks
# from `nba_prop_scores` (which leak raw market keys) line up after this
# normalization.
# ---------------------------------------------------------------------------
_NBA_ALIAS = {
    # Base markets
    "PLAYER_POINTS":                            "PTS",
    "PLAYER_POINTS_ALTERNATE":                  "PTS",
    "PLAYER_REBOUNDS":                          "REB",
    "PLAYER_REBOUNDS_ALTERNATE":                "REB",
    "PLAYER_ASSISTS":                           "AST",
    "PLAYER_ASSISTS_ALTERNATE":                 "AST",
    "PLAYER_THREES":                            "3PM",
    "PLAYER_THREES_ALTERNATE":                  "3PM",
    "PLAYER_BLOCKS":                            "BLK",
    "PLAYER_BLOCKS_ALTERNATE":                  "BLK",
    "PLAYER_STEALS":                            "STL",
    "PLAYER_STEALS_ALTERNATE":                  "STL",
    "PLAYER_TURNOVERS":                         "TO",
    "PLAYER_TURNOVERS_ALTERNATE":               "TO",
    # Combos
    "PLAYER_POINTS_ASSISTS":                    "P+A",
    "PLAYER_POINTS_ASSISTS_ALTERNATE":          "P+A",
    "PLAYER_POINTS_REBOUNDS":                   "P+R",
    "PLAYER_POINTS_REBOUNDS_ALTERNATE":         "P+R",
    "PLAYER_REBOUNDS_ASSISTS":                  "R+A",
    "PLAYER_REBOUNDS_ASSISTS_ALTERNATE":        "R+A",
    "PLAYER_POINTS_REBOUNDS_ASSISTS":           "PRA",
    "PLAYER_POINTS_REBOUNDS_ASSISTS_ALTERNATE": "PRA",
    "PLAYER_BLOCKS_STEALS":                     "BLK+STL",
    "PLAYER_BLOCKS_STEALS_ALTERNATE":           "BLK+STL",
    # Already-canonical compact tokens — pass through unchanged
    "PTS": "PTS", "REB": "REB", "AST": "AST", "3PM": "3PM",
    "BLK": "BLK", "STL": "STL", "TO": "TO",
    "P+A": "P+A", "P+R": "P+R", "R+A": "R+A",
    "PRA": "PRA", "BLK+STL": "BLK+STL",
    # Common label spellings
    "PTS+AST":  "P+A",
    "PTS+REB":  "P+R",
    "REB+AST":  "R+A",
    "PTS+REB+AST": "PRA",
    "BLK+STL_": "BLK+STL",
    "BLOCKS+STEALS": "BLK+STL",
    # Q1 / quarter-period markets — preserve as separate families,
    # uppercase only (do NOT collapse to PTS).
    "PLAYER_POINTS_Q1":            "PTS_Q1",
    "PLAYER_POINTS_ALTERNATE_Q1":  "PTS_Q1",
    "PLAYER_REBOUNDS_Q1":          "REB_Q1",
    "PLAYER_REBOUNDS_ALTERNATE_Q1":"REB_Q1",
    "PLAYER_ASSISTS_Q1":           "AST_Q1",
    "PLAYER_ASSISTS_ALTERNATE_Q1": "AST_Q1",
    # Fantasy / shot detail markets — keep distinct (do NOT collapse)
    "PLAYER_FANTASY_POINTS":            "PLAYER_FANTASY_POINTS",
    "PLAYER_FANTASY_POINTS_ALTERNATE":  "PLAYER_FANTASY_POINTS",
    "PLAYER_FIELD_GOALS":               "PLAYER_FIELD_GOALS",
    "PLAYER_FIELD_GOALS_ALTERNATE":     "PLAYER_FIELD_GOALS",
    "PLAYER_TWOS":                      "PLAYER_TWOS",
    "PLAYER_TWOS_ALTERNATE":            "PLAYER_TWOS",
    "PLAYER_TWOS_ATTEMPTS":             "PLAYER_TWOS_ATTEMPTS",
    "PLAYER_TWOS_ATTEMPTS_ALTERNATE":   "PLAYER_TWOS_ATTEMPTS",
    "PLAYER_THREES_ATTEMPTS":           "PLAYER_THREES_ATTEMPTS",
    "PLAYER_THREES_ATTEMPTS_ALTERNATE": "PLAYER_THREES_ATTEMPTS",
    "PLAYER_FREES_MADE":                "FTM",
    "PLAYER_FREES_MADE_ALTERNATE":      "FTM",
    "PLAYER_FREES_ATTEMPTS":            "FTA",
    "PLAYER_FREES_ATTEMPTS_ALTERNATE":  "FTA",
    "FGM": "FGM", "FTM": "FTM", "FTA": "FTA",
}

# ---------------------------------------------------------------------------
# MLB — display labels (and lower-case market_key aliases) → canonical
# display token. We keep human-readable display labels here because the
# MLB pipeline already uses them everywhere (mlb_live_props.stat_type,
# mlb_cached_board, mlb_prop_scores). Pitcher and batter strikeouts MUST
# remain different families.
# ---------------------------------------------------------------------------
_MLB_ALIAS = {
    # Batter
    "HITS":                       "Hits",
    "BATTER_HITS":                "Hits",
    "TOTAL_BASES":                "Total Bases",
    "BATTER_TOTAL_BASES":         "Total Bases",
    "RBIS":                       "RBIs",
    "BATTER_RBIS":                "RBIs",
    "RUNS":                       "Runs",
    "BATTER_RUNS":                "Runs",
    "STOLEN_BASES":               "Stolen Bases",
    "BATTER_STOLEN_BASES":        "Stolen Bases",
    "HOME_RUNS":                  "Home Runs",
    "BATTER_HOME_RUNS":           "Home Runs",
    "BATTER_WALKS":               "Batter Walks",
    "BATTER_STRIKEOUTS":          "Batter Strikeouts",
    "BATTER_SINGLES":             "Singles",
    "SINGLES":                    "Singles",
    "BATTER_DOUBLES":             "Doubles",
    "DOUBLES":                    "Doubles",
    "BATTER_TRIPLES":             "Triples",
    "TRIPLES":                    "Triples",
    # Pitcher (DO NOT collide with batter strikeouts)
    "PITCHER_STRIKEOUTS":         "Pitcher Strikeouts",
    "PITCHER_WALKS":              "Walks Allowed",
    "WALKS_ALLOWED":              "Walks Allowed",
    "PITCHER_HITS_ALLOWED":       "Hits Allowed",
    "HITS_ALLOWED":               "Hits Allowed",
    "PITCHER_EARNED_RUNS":        "Earned Runs",
    "EARNED_RUNS":                "Earned Runs",
    "PITCHER_OUTS":               "Pitcher Outs",
    "PITCHER_RECORD_A_WIN":       "Pitcher Record a Win",
    # Combos — collapse all spellings to ONE display token per combo
    "HITS_RUNS_RBIS":             "Hits+Runs+RBIs",
    "HRR":                        "Hits+Runs+RBIs",
    "HITS+RUNS+RBIS":             "Hits+Runs+RBIs",
    "HITS RUNS RBIS":             "Hits+Runs+RBIs",
    "HITS, RUNS, RBIS":           "Hits+Runs+RBIs",
    "BATTER_HITS_RUNS_RBIS":      "Hits+Runs+RBIs",
    "HITS_RUNS":                  "Hits+Runs",
    "HITS+RUNS":                  "Hits+Runs",
    "TOTAL_BASES_RUNS_RBIS":      "Total Bases+Runs+RBIs",
    "TOTAL BASES+RUNS+RBIS":      "Total Bases+Runs+RBIs",
    "TOTAL BASES+RUNS+RBIS_ALTERNATE": "Total Bases+Runs+RBIs",
    # Already-canonical display labels — pass through
    "HITS+RUNS+RBIS_DISPLAY":     "Hits+Runs+RBIs",
}

# Display labels (with spaces / plus signs) preserved — uppercase keys
# match `stat_type.upper()`, but tokens themselves keep their punctuation.
for _label in (
    "Hits", "Total Bases", "RBIs", "Runs", "Stolen Bases", "Home Runs",
    "Batter Walks", "Batter Strikeouts", "Singles", "Doubles", "Triples",
    "Pitcher Strikeouts", "Walks Allowed", "Hits Allowed", "Earned Runs",
    "Pitcher Outs", "Pitcher Record a Win",
    "Hits+Runs+RBIs", "Hits+Runs", "Total Bases+Runs+RBIs",
):
    _MLB_ALIAS[_label.upper()] = _label


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
SPORT_NBA = "nba"
SPORT_MLB = "mlb"


def canonical_stat_family(stat: Optional[str], sport: Optional[str] = None) -> str:
    """
    Return the canonical, display-aligned stat-family token.

    - Case-insensitive lookup.
    - Idempotent (canonical token in → canonical token out).
    - Unknown stats are returned uppercased so this is strictly additive.
    - `sport` is optional; if provided, the corresponding alias map is
      checked first. If absent, we try BOTH maps (NBA first, then MLB)
      to remain backward-compatible with callers that don't carry sport.
    """
    if not stat:
        return ""
    key = str(stat).strip()
    if not key:
        return ""
    upper = key.upper()
    if sport == SPORT_NBA:
        return _NBA_ALIAS.get(upper, upper)
    if sport == SPORT_MLB:
        # Preserve original spacing for already-canonical MLB tokens.
        return _MLB_ALIAS.get(upper, key)
    # No sport → try NBA first, then MLB. If the input matches MLB's
    # display tokens, return them as-is (with spacing preserved).
    if upper in _NBA_ALIAS:
        return _NBA_ALIAS[upper]
    if upper in _MLB_ALIAS:
        return _MLB_ALIAS[upper]
    return upper


def is_combo_stat(canonical: str) -> bool:
    """True if the canonical token represents a combo stat family."""
    if not canonical:
        return False
    c = canonical.upper().replace(" ", "")
    return any(token in c for token in (
        "+", "PRA",
    ))


def is_pitcher_stat(canonical: str) -> bool:
    """True for pitcher-side MLB families (avoids batter/pitcher collision)."""
    if not canonical:
        return False
    c = canonical.lower()
    return c in {
        "pitcher strikeouts", "walks allowed", "hits allowed",
        "earned runs", "pitcher outs", "pitcher record a win",
    }


def is_batter_stat(canonical: str) -> bool:
    """True for batter-side MLB families."""
    if not canonical:
        return False
    c = canonical.lower()
    return c in {
        "hits", "total bases", "rbis", "runs", "stolen bases", "home runs",
        "batter walks", "batter strikeouts", "singles", "doubles", "triples",
        "hits+runs+rbis", "hits+runs", "total bases+runs+rbis",
    }


def build_canonical_key(
    sport: str,
    event_id: Optional[str],
    player_name: Optional[str],
    stat_type: Optional[str],
    line: Optional[float],
    side: Optional[str],
) -> str:
    """
    Stable canonical key used for cross-pipeline joins.

    Format: `{sport}|{event_id}|{player_name}|{canonical_stat}|{line}|{SIDE}`

    `event_id` falls back to "_" if absent. Side is uppercased and forced
    to {OVER, UNDER}. canonical_stat uses the sport-aware normalizer.
    """
    sp = (sport or "").lower()
    eid = event_id or "_"
    pname = (player_name or "").strip()
    stat = canonical_stat_family(stat_type, sport=sp)
    try:
        line_str = f"{float(line)}" if line is not None else ""
    except (TypeError, ValueError):
        line_str = str(line or "")
    side_u = (side or "").strip().upper()
    if side_u not in ("OVER", "UNDER"):
        side_u = "OVER"
    return f"{sp}|{eid}|{pname}|{stat}|{line_str}|{side_u}"


__all__ = [
    "SPORT_NBA", "SPORT_MLB",
    "canonical_stat_family", "build_canonical_key",
    "is_combo_stat", "is_pitcher_stat", "is_batter_stat",
]

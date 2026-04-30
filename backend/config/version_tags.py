"""Single source of truth for `version_tag` strings written to / read
from `{sport}_prop_scores`.

WHY THIS EXISTS
---------------
Before 2026-04-30, `version_tag` string literals like `"final-mlb-rt"`
and `"final-nba-rt"` were scattered across 22+ files in services/ and
routes/. Every rename (e.g., the `-rt-rt` → `-shadow` cutover we just
did) silently drifted at least one caller out of sync. The NBA
realtime engine was dead for days because of exactly this pattern of
string-literal drift.

This module is the ONE place any of those tags live. Every caller
must import from here. A lint test (`tests/test_version_tag_literals.py`)
fails the build if a raw `final-<sport>-...` literal is reintroduced
anywhere in services/ or routes/.

CONVENTIONS
-----------
- `{SPORT}_LIVE`     — canonical tag the UI reader pins to. Hourly
                       master_sync rebuilds this with `mode=replace`.
- `{SPORT}_SHADOW`   — append-only real-time audit trail written
                       by `services/board/engine.py::on_new_props`.
                       Used by the fast-path-vs-full-rebuild
                       backtester. Never wiped by the rebuild.
- `{SPORT}_BASELINE` — the `final-{sport}` baseline (no `-rt` suffix).
                       Written by `master_sync` as a separate snapshot
                       for drift-audit comparisons. Sport-specific
                       legacy callers may still produce this.

USAGE
-----
    from config.version_tags import MLB_LIVE, NBA_LIVE, for_sport

    # Direct:
    docs = await db.mlb_prop_scores.find({"version_tag": MLB_LIVE})

    # Sport-agnostic:
    tag = for_sport("mlb")           # "final-mlb-rt"
    tag = for_sport("nba", shadow=True)  # "final-nba-rt-shadow"
    tag = for_sport("mlb", baseline=True)  # "final-mlb"

ADDING A NEW SPORT
------------------
1. Add entries to `_LIVE_BY_SPORT` / `_SHADOW_BY_SPORT` / `_BASELINE_BY_SPORT`.
2. Export `{SPORT}_LIVE` / `{SPORT}_SHADOW` / `{SPORT}_BASELINE` constants.
3. Run `tests/test_version_tag_literals.py` — it iterates every
   declared sport automatically.
"""
from __future__ import annotations

from typing import Dict

# --------------------------------------------------------------------
# Canonical per-sport tags. ADD NEW SPORTS HERE ONLY.
# --------------------------------------------------------------------

_LIVE_BY_SPORT: Dict[str, str] = {
    "mlb": "final-mlb-rt",
    "nba": "final-nba-rt",
}

_SHADOW_BY_SPORT: Dict[str, str] = {
    "mlb": "final-mlb-rt-shadow",
    "nba": "final-nba-rt-shadow",
}

_BASELINE_BY_SPORT: Dict[str, str] = {
    "mlb": "final-mlb",
    "nba": "final-nba",
}

# Module-level constants — preferred for grep/IDE jump-to-definition.
MLB_LIVE = _LIVE_BY_SPORT["mlb"]
MLB_SHADOW = _SHADOW_BY_SPORT["mlb"]
MLB_BASELINE = _BASELINE_BY_SPORT["mlb"]

NBA_LIVE = _LIVE_BY_SPORT["nba"]
NBA_SHADOW = _SHADOW_BY_SPORT["nba"]
NBA_BASELINE = _BASELINE_BY_SPORT["nba"]

# Ordered list of every tag produced by this module, useful for tests
# and any "prune every non-canonical tag" sweep.
ALL_KNOWN_TAGS = tuple(sorted(
    set(_LIVE_BY_SPORT.values())
    | set(_SHADOW_BY_SPORT.values())
    | set(_BASELINE_BY_SPORT.values())
))

# Supported sport identifiers. Importers can iterate this instead of
# hardcoding `["mlb", "nba"]`.
SUPPORTED_SPORTS = tuple(sorted(_LIVE_BY_SPORT.keys()))


# --------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------

def for_sport(sport: str, *, shadow: bool = False, baseline: bool = False) -> str:
    """Return the canonical tag for a given sport.

    Args:
        sport: "mlb", "nba", etc. (case-insensitive).
        shadow: If True, return the `-shadow` backtest tag.
        baseline: If True, return the baseline tag (no `-rt` suffix).
            Mutually exclusive with `shadow`.

    Raises:
        ValueError: unknown sport, or both flags set.
    """
    if shadow and baseline:
        raise ValueError(
            "for_sport: `shadow` and `baseline` are mutually exclusive"
        )
    s = (sport or "").lower()
    if baseline:
        table = _BASELINE_BY_SPORT
    elif shadow:
        table = _SHADOW_BY_SPORT
    else:
        table = _LIVE_BY_SPORT
    if s not in table:
        raise ValueError(
            f"for_sport: unknown sport {sport!r} "
            f"(known: {sorted(table.keys())})"
        )
    return table[s]


def shadow_for(live_tag: str) -> str:
    """Given a live tag, return its paired shadow tag.

    Used by `services/board/engine.py` to compute the dual-write pair
    without hardcoding string math.
    """
    for sport, live in _LIVE_BY_SPORT.items():
        if live == live_tag:
            return _SHADOW_BY_SPORT[sport]
    # Fall back to appending the suffix — safer than raising here
    # because callers often wrap this in a best-effort shadow write.
    return f"{live_tag}-shadow"


def is_live_tag(tag: str) -> bool:
    """True iff `tag` is one of the canonical live tags."""
    return tag in _LIVE_BY_SPORT.values()


def is_shadow_tag(tag: str) -> bool:
    return tag in _SHADOW_BY_SPORT.values()


def sport_of(tag: str) -> str:
    """Reverse lookup: which sport owns this tag?"""
    for sport, live in _LIVE_BY_SPORT.items():
        if tag == live:
            return sport
    for sport, shadow in _SHADOW_BY_SPORT.items():
        if tag == shadow:
            return sport
    for sport, baseline in _BASELINE_BY_SPORT.items():
        if tag == baseline:
            return sport
    raise ValueError(f"sport_of: unrecognized tag {tag!r}")


# Sport → live-tag map, for callers that need the full dict.
LIVE_TAG_BY_SPORT = dict(_LIVE_BY_SPORT)
SHADOW_TAG_BY_SPORT = dict(_SHADOW_BY_SPORT)
BASELINE_TAG_BY_SPORT = dict(_BASELINE_BY_SPORT)

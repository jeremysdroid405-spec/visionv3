"""
Canonical Collection Naming — Phase A (authoritative, config-first)
===================================================================

This module is the single source of truth for collection names used by
the universal board engine (services/board/*) and all NEW multi-sport
code. It co-exists with the older `config/db_config.py` (which has 50+
callers today); the two files will converge once the Phase B/C/D rename
migration completes and the old aliases retire.

Principle (per Phase 6.5 naming audit, 2026-04-18):
    every concept that is board-system state must have the canonical
    name `{sport}_{concept}` for every registered sport.

For Phase A, we accept that some legacy collections don't yet carry the
canonical name (e.g. NBA's `dg_live_props`, `dg_cached_board`).
`resolve(sport, concept)` is the read-through resolver: it returns the
collection name that is CURRENTLY the authoritative store for that
concept, so every caller goes through one function and can be migrated
collection-by-collection without breaking other callers.

Adding a sport later:
    1. add the sport to `SUPPORTED_SPORTS`
    2. (optional) override a concept in `SPORT_OVERRIDES[sport]` only if
       that sport's collection has a non-canonical legacy name
    3. done — the universal board engine picks it up automatically.

Adding a concept:
    1. add to `CANONICAL_CONCEPTS`
    2. (optional) add legacy aliases for sports that haven't migrated yet
"""
from __future__ import annotations

from typing import Dict, List


# ---------------------------------------------------------------------------
# Registered sports (must match services/board/adapters/__init__.py::REGISTRY)
# ---------------------------------------------------------------------------
SUPPORTED_SPORTS: List[str] = ["nba", "mlb"]


# ---------------------------------------------------------------------------
# Canonical concept catalog
# ---------------------------------------------------------------------------
# Every concept the board system speaks of. The canonical name for each
# is always `{sport}_{concept}`. Sports that have a legacy name for this
# concept override below in SPORT_OVERRIDES.
CANONICAL_CONCEPTS: List[str] = [
    # board core
    "live_props",            # raw odds-API prop inventory
    "prop_scores",           # master pool (scored + tiered + active flag)
    "cached_board",          # enrichment overlay (vision_intel, badges, context)
    # historical
    "historical_logs",       # long-term stat history
    "player_mapping",        # odds-API ↔ upstream player identity
    "player_badges",         # sport-specific badge catalog
    # data feeds
    "advanced_stats",        # upstream advanced stats cache
    "career_stats",          # career / season totals
    "context_engine",        # per-game context cache
    "master_hub",            # rolled-up player stats store
    # optional
    "breaking_news",
    "social_signals",
    "flagged_players",
    "daily_insights",
    "events_cache",
    "odds_cache",
    "locked_games",
    "master_roster",
    "player_stats",
    "referee_assignments",
    "ticker_cache",
    "ticker_headlines",
    "oracle_apex_analyzed",
    "calibration_runs",
    "scoring_discarded",
    # score-path intermediate (kept sport-prefixed to avoid cross-sport collisions)
    "scoring_scored",
    "parlay_builder",
]


# ---------------------------------------------------------------------------
# Legacy overrides (Phase A — explicit, one line per non-canonical collection)
# ---------------------------------------------------------------------------
# For each (sport, concept) whose CURRENT storage name is NOT the canonical
# `{sport}_{concept}`, list the legacy name here. The resolver returns the
# legacy name so readers/writers keep working unchanged.
#
# As each legacy collection gets renamed via the Phase B/C/D dual-write
# playbook, delete the corresponding line below — nothing else in the
# codebase needs to change.
SPORT_OVERRIDES: Dict[str, Dict[str, str]] = {
    "nba": {
        # Core board collections — still on legacy dg_* names
        "live_props":              "dg_live_props",
        "cached_board":             "dg_cached_board",
        "breaking_news":            "dg_breaking_news",
        "social_signals":           "dg_social_signals",
        "flagged_players":          "dg_flagged_players",
        "daily_insights":           "dg_daily_insights",
        "events_cache":             "dg_events_cache",
        "odds_cache":               "dg_odds_cache",
        "locked_games":             "dg_locked_games",
        "master_roster":            "dg_master_roster",
        "player_stats":             "dg_player_stats",
        "parlay_builder":           "dg_parlay_builder",
        # Ferrari-branded un-prefixed legacy writers (still being written
        # by unified_pipeline._atomic_publish; drops are Phase 5 Step 6)
        "scoring_scored":           "ferrari_scored",
        "scoring_discarded":        "ferrari_discarded",
        # BDL-sourced NBA-only data — scheduled to dual-write in Phase C
        "advanced_stats":           "bdl_advanced_stats",
        "historical_logs":          "bdl_historical_game_logs",
        "player_mapping":           "bdl_player_mapping",
        "player_badges":            "bdl_player_badges",
        # Un-prefixed NBA-only collections
        "oracle_apex_analyzed":     "oracle_apex_analyzed",
        "referee_assignments":      "referee_assignments",
        "ticker_cache":             "ticker_cache",
        "ticker_headlines":         "ticker_headlines",
        # prop_scores is already canonical — nba_prop_scores (no override needed)
    },
    "mlb": {
        # MLB is mostly canonical. The retired tier-storage collections
        # (mlb_safe_haven etc.) don't appear here because they're NOT
        # concepts the board system consumes any more (board is a live
        # query now). They'll be dropped in Phase 5 Step 6.
        # cached_board, prop_scores, historical_logs, etc. are already
        # mlb_* — nothing to override.
    },
}


class UnknownSportError(KeyError):
    """Raised when a sport outside SUPPORTED_SPORTS is queried."""


class UnknownConceptError(KeyError):
    """Raised when a concept outside CANONICAL_CONCEPTS is queried."""


def resolve(sport: str, concept: str) -> str:
    """Return the collection name currently used as the authoritative
    store for this (sport, concept) pair.

    Phase A: returns the legacy name if the sport has an override;
    otherwise returns the canonical `{sport}_{concept}`. This is the ONE
    function every NEW caller goes through. Legacy callers that hardcode
    names can migrate incrementally by switching to this resolver."""
    s = (sport or "").strip().lower()
    c = (concept or "").strip().lower()
    if s not in SUPPORTED_SPORTS:
        raise UnknownSportError(sport)
    if c not in CANONICAL_CONCEPTS:
        raise UnknownConceptError(concept)
    overrides = SPORT_OVERRIDES.get(s, {})
    return overrides.get(c, f"{s}_{c}")


def canonical_name(sport: str, concept: str) -> str:
    """Return the pure canonical name without applying any legacy override.
    Useful for migration tooling that needs to know 'where should this
    collection eventually live?' vs. 'where does it live today?'."""
    s = (sport or "").strip().lower()
    c = (concept or "").strip().lower()
    if s not in SUPPORTED_SPORTS:
        raise UnknownSportError(sport)
    if c not in CANONICAL_CONCEPTS:
        raise UnknownConceptError(concept)
    return f"{s}_{c}"


def has_legacy_override(sport: str, concept: str) -> bool:
    """True when this (sport, concept) pair is currently using a
    non-canonical legacy collection name. Drives migration dashboards."""
    return canonical_name(sport, concept) != resolve(sport, concept)


def migration_status() -> Dict[str, Dict[str, Dict[str, str]]]:
    """Compact report suitable for an observability endpoint / audit
    document. Structure:
        { sport: { concept: {"current": ..., "canonical": ..., "migrated": bool } } }
    """
    out: Dict[str, Dict[str, Dict[str, str]]] = {}
    for sport in SUPPORTED_SPORTS:
        out[sport] = {}
        for concept in CANONICAL_CONCEPTS:
            current = resolve(sport, concept)
            canon = canonical_name(sport, concept)
            out[sport][concept] = {
                "current": current,
                "canonical": canon,
                "migrated": current == canon,
            }
    return out


__all__ = [
    "SUPPORTED_SPORTS",
    "CANONICAL_CONCEPTS",
    "SPORT_OVERRIDES",
    "UnknownSportError",
    "UnknownConceptError",
    "resolve",
    "canonical_name",
    "has_legacy_override",
    "migration_status",
]

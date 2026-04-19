"""
Canonical collection-name registry (sport-aware).

Wave 0 of the NBA rebuild. Current values are the live collection names
(pre-rename), so importing `COLL(concept, sport)` everywhere does not change
behavior today. Subsequent rename waves flip ONE mapping at a time so the
cutover is atomic per concept.

Usage
-----
from services.config.collection_names import COLL

coll = db[COLL("live_props", "nba")]      # -> db["dg_live_props"] today
                                          # -> db["nba_live_props"] after Wave 2

Rules
-----
1. Every writer and reader of a sport-specific store MUST resolve its
   collection name through `COLL(concept, sport)`. No bare string literals.
2. Shared multi-sport stores use `COLL.shared(concept)`. These carry a
   `sport` field on each document; there is no sport-specific sibling.
3. Concept keys are stable; their current-name mapping is the ONLY thing
   that changes during rebuild waves.

Conventions
-----------
Concept keys are lowercase snake_case describing the domain concept, not
the current collection name (e.g. "live_props", not "dg_live_props").
"""
from __future__ import annotations

from typing import Dict, List, Tuple


# ---------------------------------------------------------------------------
# Wave 1 SHADOW-WRITE PILOT
# ---------------------------------------------------------------------------
# Concepts listed here get DUAL-written during the Wave 1 shadow phase.
# Reads remain on the primary (current physical name). Writes fan out to
# BOTH the primary AND the named shadow collection so the shadow fills up
# for verification before Wave 2 flips reads.
#
# Format: (concept, sport) -> shadow_physical_name
#
# To add a concept to shadow-write:
#   1. Add a row here.
#   2. Restart backend. The scheduler's shadow_divergence_monitor job will
#      pick it up automatically and start logging divergence to
#      `board_drift_ledger` every 60s.
#   3. Observe for the configured duration.
#   4. On greenlight, flip the primary in `_SPORT_COLLECTIONS` below, then
#      remove the row from this map.
# ---------------------------------------------------------------------------
_SHADOW_WRITES: Dict[Tuple[str, str], str] = {
    # Wave 2 complete: events_cache · NBA has been flipped to
    # `nba_events_cache` and the shadow phase is retired. The old
    # primary (`dg_events_cache`) has been renamed to
    # `dg_events_cache_backup` and is eligible for drop after the
    # observation window closes.

    # Wave 2 complete: odds_cache · NBA has been flipped to
    # `nba_odds_cache` and the shadow phase is retired. The old
    # primary (`dg_odds_cache`) has been renamed to
    # `dg_odds_cache_backup` and is eligible for drop after the
    # observation window closes.

    # Wave 2 complete: master_roster · NBA has been flipped to
    # `nba_master_roster` and the shadow phase is retired. The old
    # primary (`dg_master_roster`) has been renamed to
    # `dg_master_roster_backup` and is eligible for drop after the
    # observation window closes.

    # Wave 2 complete: odds_mapping · NBA has been flipped to
    # `nba_odds_api_mapping_master` and the shadow phase is retired.
    # The old primary (`odds_api_mapping_master`) has been renamed to
    # `odds_api_mapping_master_backup` and is eligible for drop after
    # the observation window closes.

    # Wave 2 complete (frozen-archive): player_stats_agg · NBA has
    # been flipped to `nba_player_stats` and the shadow phase is
    # retired. The old primary (`dg_player_stats`) has been renamed
    # to `dg_player_stats_backup` and is eligible for drop after the
    # observation window closes. NOTE: this concept has no active
    # writer in the current codebase (the `RosterService.player_stats`
    # handle was never assigned) — the rename merely finalises an
    # archival snapshot under its correctly-prefixed name.

    # Wave 2 complete: referee_assignments · NBA has been flipped to
    # `nba_referee_assignments` and the shadow phase is retired. The
    # old primary (`referee_assignments`) has been renamed to
    # `referee_assignments_backup` and is eligible for drop after
    # the observation window closes.
}


# ---------------------------------------------------------------------------
# Sport-specific concept map. Each concept maps `{sport: current_name}`.
# Current names = live collection names today. Renames = edit right here.
# ---------------------------------------------------------------------------
_SPORT_COLLECTIONS: Dict[str, Dict[str, str]] = {
    # ---- Scoring & board output --------------------------------------------
    "prop_scores":          {"nba": "nba_prop_scores",
                             "mlb": "mlb_prop_scores"},
    "prop_scores_archive":  {"nba": "nba_prop_scores_archive_stale_tags",
                             "mlb": "mlb_prop_scores_archive_stale_tags"},

    # ---- Player identity / hub ---------------------------------------------
    "master_hub":           {"nba": "nba_master_hub_2026",
                             "mlb": "mlb_master_hub_2026"},
    "master_roster":        {"nba": "nba_master_roster",
                             "mlb": "mlb_master_roster"},
    "context_flags":        {"nba": "nba_context_engine",
                             "mlb": "mlb_context_engine"},
    "career_backstop":      {"nba": "nba_career_stats",
                             "mlb": "mlb_career_stats"},

    # ---- Historical game data ----------------------------------------------
    # New concept: introduced in rebuild. `master_hub.history` today serves
    # this; Wave 3 migrates the heavy history payload out of the hub.
    "historical_data":      {"nba": "nba_master_hub_2026",   # TEMP: still in hub
                             "mlb": "mlb_master_hub_2026"},
    "player_stats_agg":     {"nba": "nba_player_stats",
                             "mlb": "mlb_player_stats"},

    # ---- Ingest caches -----------------------------------------------------
    "live_props":           {"nba": "dg_live_props",
                             "mlb": "mlb_live_props"},
    "odds_cache":           {"nba": "nba_odds_cache",
                             "mlb": "mlb_odds_cache"},
    "events_cache":         {"nba": "nba_events_cache",
                             "mlb": "mlb_events_cache"},
    "odds_mapping":         {"nba": "nba_odds_api_mapping_master",  # NBA-only today
                             "mlb": "mlb_odds_api_mapping_master"},  # future

    # ---- Contextual caches (currently NBA-only in code) --------------------
    "defensive_momentum_cache": {"nba": "defensive_momentum_cache",
                                 "mlb": "mlb_defensive_momentum_cache"},
    "star_usage_cache":         {"nba": "star_usage_cache",
                                 "mlb": "mlb_star_usage_cache"},

    # ---- Board caches (UI-facing) ------------------------------------------
    "board_cache":          {"nba": "dg_cached_board",
                             "mlb": "mlb_cached_board"},
    "board_cache_temp":     {"nba": "dg_cached_board_temp",
                             "mlb": "mlb_cached_board_temp"},

    # ---- Board read-models (new in rebuild; placeholders for now) ----------
    # These are introduced in Wave 5. Today they do not exist; dereferencing
    # them before Wave 5 raises an explicit error.
    "board_active":         {"nba": None,
                             "mlb": None},
    "board_injured":        {"nba": None,
                             "mlb": None},
    "board_overlays":       {"nba": None,
                             "mlb": None},

    # ---- Misc sport-specific -----------------------------------------------
    "line_history":         {"nba": "line_history",          # currently in hub DB
                             "mlb": "mlb_line_history"},
    "referee_assignments":  {"nba": "nba_referee_assignments",
                             "mlb": "mlb_referee_assignments"},
    "calibration_snapshots": {"nba": "nba_calibration_runs",
                              "mlb": "mlb_calibration_runs"},
}


# ---------------------------------------------------------------------------
# Shared, unprefixed, multi-sport stores. Each doc carries a `sport` field.
# ---------------------------------------------------------------------------
_SHARED_COLLECTIONS: Dict[str, str] = {
    "injuries":             "injuries_normalized",  # rename to "injuries" in Wave 4
    "live_injuries":        "live_injuries",         # collapses into injuries in Wave 4
    "board_drift_ledger":   "board_drift_ledger",
    "users":                "users",
    "ticker_cache":         "ticker_cache",
    "ticker_headlines":     "ticker_headlines",
    "breaking_news_cache":  "breaking_news_cache",
    "live_scores_cache":    "live_scores_cache",
    "spotrac_contracts_cache": "spotrac_contracts_cache",
    # Observability
    "sync_log":             "dg_sync_log",  # currently in hub DB
}


class _CollectionResolver:
    """Callable + namespace so `COLL("live_props", "nba")` and
    `COLL.shared("injuries")` both work."""

    def __call__(self, concept: str, sport: str) -> str:
        sport = (sport or "").lower()
        try:
            mapping = _SPORT_COLLECTIONS[concept]
        except KeyError:
            raise KeyError(
                f"Unknown sport-specific concept {concept!r}. "
                f"Known: {sorted(_SPORT_COLLECTIONS.keys())}"
            )
        name = mapping.get(sport)
        if name is None:
            raise KeyError(
                f"Concept {concept!r} has no live collection for sport "
                f"{sport!r} yet. Check rebuild wave status."
            )
        return name

    @staticmethod
    def shared(concept: str) -> str:
        try:
            return _SHARED_COLLECTIONS[concept]
        except KeyError:
            raise KeyError(
                f"Unknown shared concept {concept!r}. "
                f"Known: {sorted(_SHARED_COLLECTIONS.keys())}"
            )

    @staticmethod
    def all_mapping() -> Dict[str, Dict[str, str]]:
        """Return a copy of the full concept → collection mapping for
        debugging / migration scripts. Read-only."""
        return {
            "sport_specific": {k: dict(v) for k, v in _SPORT_COLLECTIONS.items()},
            "shared": dict(_SHARED_COLLECTIONS),
        }

    # -----------------------------------------------------------------
    # Wave 1 shadow-write support
    # -----------------------------------------------------------------
    def writes_to(self, concept: str, sport: str) -> List[str]:
        """Return the list of physical collection names a write to
        (concept, sport) must fan out to.

        During normal operation this is a single-element list [primary].
        During the Wave 1 shadow phase, concepts registered in
        `_SHADOW_WRITES` return [primary, shadow].
        """
        primary = self(concept, sport)
        shadow = _SHADOW_WRITES.get((concept, (sport or "").lower()))
        if shadow and shadow != primary:
            return [primary, shadow]
        return [primary]

    def handle(self, db, concept: str, sport: str):
        """Return a collection-like object suitable for both reads AND
        writes. If the (concept, sport) pair is NOT shadow-mapped, returns
        the raw Motor collection (zero overhead). If it IS shadow-mapped,
        returns a `ShadowWriter` that fans mutations out to [primary,
        shadow] and delegates reads to the primary.
        """
        names = self.writes_to(concept, sport)
        if len(names) == 1:
            return db[names[0]]
        # Import lazily to avoid a module-load cycle during test collection.
        from services.config.shadow_writer import ShadowWriter
        return ShadowWriter(db[names[0]], [db[n] for n in names[1:]])

    @staticmethod
    def active_shadows() -> Dict[Tuple[str, str], str]:
        """Return a copy of the currently-active shadow-write mapping."""
        return dict(_SHADOW_WRITES)


COLL = _CollectionResolver()

# Stable export aliases for legacy modules transitioning off hard-coded names.
# Example:
#     from services.config.collection_names import LIVE_PROPS_NBA
#     props = db[LIVE_PROPS_NBA].find(...)
LIVE_PROPS_NBA              = COLL("live_props", "nba")
ODDS_CACHE_NBA              = COLL("odds_cache", "nba")
EVENTS_CACHE_NBA            = COLL("events_cache", "nba")
MASTER_ROSTER_NBA           = COLL("master_roster", "nba")
MASTER_HUB_NBA              = COLL("master_hub", "nba")
CONTEXT_FLAGS_NBA           = COLL("context_flags", "nba")
CAREER_BACKSTOP_NBA         = COLL("career_backstop", "nba")
PROP_SCORES_NBA             = COLL("prop_scores", "nba")
BOARD_CACHE_NBA             = COLL("board_cache", "nba")
BOARD_CACHE_TEMP_NBA        = COLL("board_cache_temp", "nba")
DEFENSIVE_MOMENTUM_NBA      = COLL("defensive_momentum_cache", "nba")
STAR_USAGE_NBA              = COLL("star_usage_cache", "nba")
ODDS_MAPPING_NBA            = COLL("odds_mapping", "nba")
PLAYER_STATS_AGG_NBA        = COLL("player_stats_agg", "nba")
HISTORICAL_DATA_NBA         = COLL("historical_data", "nba")

INJURIES                    = COLL.shared("injuries")
LIVE_INJURIES               = COLL.shared("live_injuries")
BOARD_DRIFT_LEDGER          = COLL.shared("board_drift_ledger")

"""Unit tests for replay schema declarations (Phase 0).

No DB connection. We validate the INDEX_SPECS structure and the
DATASET_LINEAGE_VALUE quarantine sentinel.
"""
from __future__ import annotations

from pymongo import ASCENDING, DESCENDING

from services.replay.schema import (
    DATASET_LINEAGE_VALUE,
    INDEX_SPECS,
    REPLAY_COLLECTIONS,
)
from services.replay.markets import (
    REPLAY_NBA_MARKETS,
    REPLAY_BOOK_WHITELIST_PHASE1,
    REPLAY_REGIONS_PHASE1,
)


# ----- Lineage sentinel ----------------------------------------------------

def test_dataset_lineage_value_is_quarantined_label():
    """Must be exactly 'historical_replay' so the existing forward-testing
    lineage filter (which whitelists only legacy_vk / modern_ssot) keeps
    replay docs out of official reporting."""
    assert DATASET_LINEAGE_VALUE == "historical_replay"


# ----- Collection list ----------------------------------------------------

def test_eleven_replay_collections_with_replay_prefix():
    assert len(REPLAY_COLLECTIONS) == 11
    for name in REPLAY_COLLECTIONS:
        assert name.startswith("replay_"), name


def test_no_duplicate_collection_names():
    assert len(set(REPLAY_COLLECTIONS)) == len(REPLAY_COLLECTIONS)


def test_required_collections_present():
    required = {
        "replay_events",
        "replay_odds_snapshots",
        "replay_props_normalized",
        "replay_results",
        "replay_runs",
        "replay_evaluations",
        "replay_outcomes",
        "replay_gate_sweeps",
        "replay_market_movements",
        "replay_calibration_reports",
    }
    assert required.issubset(set(REPLAY_COLLECTIONS))


# ----- Index spec shape ---------------------------------------------------

def test_every_collection_has_index_spec():
    assert set(INDEX_SPECS.keys()) == set(REPLAY_COLLECTIONS)


def test_every_index_spec_has_name_and_keys():
    for coll, specs in INDEX_SPECS.items():
        assert specs, f"{coll} has no index specs"
        for s in specs:
            assert "name" in s and "keys" in s, (coll, s)
            assert isinstance(s["name"], str) and s["name"], (coll, s)
            assert isinstance(s["keys"], list) and s["keys"], (coll, s)


def test_index_keys_use_pymongo_direction_constants():
    for coll, specs in INDEX_SPECS.items():
        for s in specs:
            for field, direction in s["keys"]:
                assert isinstance(field, str) and field, (coll, s)
                assert direction in (ASCENDING, DESCENDING), (coll, s, direction)


def test_unique_indexes_present_where_required():
    """Each table that must enforce 1-row-per-natural-key carries unique."""
    must_have_unique = {
        "replay_events":           "uniq_sport_event_id",
        "replay_odds_snapshots":   "uniq_event_market_label",
        "replay_props_normalized":
            "uniq_event_label_book_market_player_line_side",
        "replay_results":          "uniq_event_player",
        "replay_feature_cache":    "uniq_player_asof",
        "replay_calibration_reports": "uniq_run",
    }
    for coll, idx_name in must_have_unique.items():
        match = [s for s in INDEX_SPECS[coll]
                  if s["name"] == idx_name and s.get("unique")]
        assert match, f"{coll} missing unique index {idx_name}"


def test_index_names_unique_within_collection():
    for coll, specs in INDEX_SPECS.items():
        names = [s["name"] for s in specs]
        assert len(set(names)) == len(names), (coll, names)


# ----- Markets / books whitelist ------------------------------------------

def test_phase1_books_in_canonical_order():
    assert REPLAY_BOOK_WHITELIST_PHASE1 == [
        "draftkings", "fanduel", "betonlineag", "williamhill_us", "betmgm",
    ]


def test_phase1_no_pinnacle_yet():
    """User directive: skip Pinnacle in Phase 1."""
    assert "pinnacle" not in REPLAY_BOOK_WHITELIST_PHASE1


def test_phase1_us_region_only():
    assert REPLAY_REGIONS_PHASE1 == ["us"]


def test_nba_markets_include_all_confirmed_alt_keys():
    """The 2026-05-09 audit confirmed these alt keys; replay must request them."""
    must_include = {
        "player_points_alternate",
        "player_rebounds_alternate",
        "player_assists_alternate",
        "player_threes_alternate",
        "player_points_rebounds_assists_alternate",
        "player_points_rebounds_alternate",
        "player_points_assists_alternate",
        "player_rebounds_assists_alternate",
    }
    assert must_include.issubset(set(REPLAY_NBA_MARKETS))


def test_nba_markets_no_duplicates():
    assert len(set(REPLAY_NBA_MARKETS)) == len(REPLAY_NBA_MARKETS)

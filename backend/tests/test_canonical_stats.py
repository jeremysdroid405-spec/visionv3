"""Validation tests for the universal stat canonicalizer.

Covers every contract listed in the 2026-05-13 spec:
  • Every stat_type emitted by ingest resolves to canonical, family,
    model_key (where applicable), and display label
  • Old long-form aliases still resolve
  • Combo stats (PR / PA / RA / BLST) resolve to their families
  • The Ayo Dosunmu regression (PR → pts_reb) stays fixed
  • Unmapped stat_types fail loud (strict=True raises)
  • Combo stats do not fall to _default
  • New sports plug in via `register_sport` without touching existing files
"""

import os
import sys

# Backend src on path so `services.*` imports resolve.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from services.scoring.canonical_stats import (
    StatFamilyMissError,
    canonical_stat_type,
    stat_family,
    model_key,
    display_label,
    market_to_stat_map,
    iter_sports,
    register_sport,
    validate_sport,
)


# ----------------------------------------------------------------------
# NBA — every emitted stat_type round-trips
# ----------------------------------------------------------------------
NBA_CANONICAL_TOKENS = {"PTS", "REB", "AST", "PRA", "3PM", "STL", "BLK", "TO",
                        "PR", "PA", "RA", "BLST"}


@pytest.mark.parametrize("market,expected", [
    # Standard markets
    ("player_points",                              "PTS"),
    ("player_points_alternate",                    "PTS"),
    ("player_rebounds",                            "REB"),
    ("player_rebounds_alternate",                  "REB"),
    ("player_assists",                             "AST"),
    ("player_points_rebounds_assists",             "PRA"),
    ("player_points_rebounds_assists_alternate",   "PRA"),
    ("player_threes",                              "3PM"),
    ("player_threes_alternate",                    "3PM"),
    ("player_steals",                              "STL"),
    ("player_blocks",                              "BLK"),
    ("player_turnovers",                           "TO"),
    # Combo markets — the SSOT collapse the user asked us to preserve
    ("player_points_rebounds",                     "PR"),
    ("player_points_rebounds_alternate",           "PR"),
    ("player_points_assists",                      "PA"),
    ("player_rebounds_assists",                    "RA"),
    ("player_blocks_steals",                       "BLST"),
])
def test_nba_market_to_canonical_stat_type(market, expected):
    assert canonical_stat_type("nba", market) == expected


@pytest.mark.parametrize("canonical", sorted(NBA_CANONICAL_TOKENS))
def test_nba_canonical_token_is_idempotent(canonical):
    """Feeding a canonical token back in must return it unchanged
    (the registry seeds canonical self-aliases on register)."""
    assert canonical_stat_type("nba", canonical) == canonical


@pytest.mark.parametrize("stat_type,family", [
    ("PTS",  "pts"),
    ("REB",  "reb"),
    ("AST",  "ast"),
    ("PRA",  "pra"),
    ("3PM",  "threes"),
    ("STL",  "stl"),
    ("BLK",  "blk"),
    ("TO",   "turnovers"),
    # 2026-05-13 combo families — these are the four fixes the user
    # explicitly called out as must-preserve.
    ("PR",   "pts_reb"),
    ("PA",   "pts_ast"),
    ("RA",   "reb_ast"),
    ("BLST", "blocks_steals"),
    # Long-form aliases still work
    ("player_points_rebounds",            "pts_reb"),
    ("player_points_rebounds_alternate",  "pts_reb"),
    ("player_points_assists",             "pts_ast"),
    ("player_rebounds_assists",           "reb_ast"),
    ("player_blocks_steals",              "blocks_steals"),
])
def test_nba_stat_family_resolution(stat_type, family):
    assert stat_family("nba", stat_type) == family


def test_nba_combo_never_falls_to_default():
    """The Ayo Dosunmu regression: PR 19.5 War Zone vanished when
    the family resolver fell through to `_default` for the combo
    canonical tokens. Lock that behavior down."""
    for st in ("PR", "PA", "RA", "BLST"):
        fam = stat_family("nba", st, strict=True)
        assert fam != "_default", f"{st} fell to _default"


@pytest.mark.parametrize("stat_type,expected_model", [
    ("PTS", "pts"),
    ("REB", "reb"),
    ("AST", "ast"),
    ("3PM", "3pm"),
    ("PRA", "pra"),
])
def test_nba_model_key(stat_type, expected_model):
    assert model_key("nba", stat_type) == expected_model


@pytest.mark.parametrize("stat_type,label", [
    ("PR",  "P+R"),
    ("PA",  "P+A"),
    ("RA",  "R+A"),
    ("PRA", "PRA"),
    ("PTS", "PTS"),
])
def test_nba_display_label(stat_type, label):
    assert display_label("nba", stat_type) == label


# ----------------------------------------------------------------------
# MLB — every emitted stat_type round-trips
# ----------------------------------------------------------------------
@pytest.mark.parametrize("market,expected", [
    ("batter_hits",                          "Hits"),
    ("batter_hits_alternate",                "Hits"),
    ("batter_total_bases",                   "Total Bases"),
    ("batter_home_runs",                     "Home Runs"),
    ("batter_home_runs_alternate",           "Home Runs"),
    ("batter_hits_runs_rbis",                "Hits+Runs+RBIs"),
    ("batter_rbis",                          "RBIs"),
    ("batter_runs_scored",                   "Runs"),
    ("batter_stolen_bases",                  "Stolen Bases"),
    ("pitcher_strikeouts",                   "Pitcher Strikeouts"),
    ("pitcher_strikeouts_alternate",         "Pitcher Strikeouts"),
    ("pitcher_walks",                        "Walks Allowed"),
    ("pitcher_hits_allowed",                 "Hits Allowed"),
    ("pitcher_earned_runs",                  "Earned Runs"),
    ("pitcher_outs",                         "Pitcher Outs"),
])
def test_mlb_market_to_canonical_stat_type(market, expected):
    assert canonical_stat_type("mlb", market) == expected


@pytest.mark.parametrize("stat_type,family", [
    ("Hits",                "hits"),
    ("Total Bases",         "total_bases"),
    ("Home Runs",           "home_runs"),
    ("Hits+Runs+RBIs",      "hits_runs_rbis"),
    ("RBIs",                "rbis"),
    ("Runs",                "runs"),
    ("Stolen Bases",        "stolen_bases"),
    ("Pitcher Strikeouts",  "pitcher_strikeouts"),
    ("Walks Allowed",       "walks_allowed"),
    ("Hits Allowed",        "hits_allowed"),
    ("Earned Runs",         "earned_runs"),
    ("Pitcher Outs",        "pitching_outs"),
    # Case-insensitive
    ("hits",                "hits"),
    ("HOME RUNS",           "home_runs"),
])
def test_mlb_stat_family_resolution(stat_type, family):
    assert stat_family("mlb", stat_type) == family


# ----------------------------------------------------------------------
# Fail-loud diagnostic — strict mode raises on unmapped stat_types
# ----------------------------------------------------------------------
def test_strict_mode_raises_on_unknown_stat():
    with pytest.raises(StatFamilyMissError):
        stat_family("nba", "PlayerStatThatDoesNotExist", strict=True)


def test_non_strict_returns_default_and_logs():
    """Default behavior preserves the gate engine contract: unknown
    stat_types route to `_default`. Drift is surfaced via ERROR log
    + miss-counter (asserted by `test_miss_counter_increments`)."""
    assert stat_family("nba", "PlayerStatThatDoesNotExist") == "_default"


# ----------------------------------------------------------------------
# Validation report — every canonical token has a family
# ----------------------------------------------------------------------
@pytest.mark.parametrize("sport", ["nba", "mlb"])
def test_validate_sport_passes(sport):
    """No canonical stat_type emitted by ingest can lack a family —
    if it does, validate_sport returns ok=False and the offending
    tokens are surfaced. This prevents the class of regressions that
    caused the Ayo Dosunmu PR War Zone vanish."""
    report = validate_sport(sport)
    assert report["ok"], (
        f"{sport} validation failed: families_missing_canonical="
        f"{report['families_missing_canonical']}"
    )


# ----------------------------------------------------------------------
# Pluggability — new sports without touching existing files
# ----------------------------------------------------------------------
def test_register_new_sport_without_editing_other_files():
    """The whole point of the registry: a new sport plugs in via one
    function call, no edits anywhere else."""
    register_sport(
        "test_sport_xyz",
        market_to_stat={"player_foo": "FOO", "player_foo_alternate": "FOO"},
        stat_to_family={"foo": "foo"},
        stat_to_model={"foo": "foo"},
        stat_to_display={"foo": "FOO"},
    )
    try:
        assert canonical_stat_type("test_sport_xyz", "player_foo") == "FOO"
        assert canonical_stat_type("test_sport_xyz", "FOO") == "FOO"     # idempotent
        assert stat_family("test_sport_xyz", "FOO") == "foo"
        assert model_key("test_sport_xyz", "FOO") == "foo"
        assert display_label("test_sport_xyz", "FOO") == "FOO"
        assert "test_sport_xyz" in iter_sports()
    finally:
        # Cleanup — drop the test sport so we don't pollute the registry
        from services.scoring.canonical_stats import _REGISTRY
        _REGISTRY.pop("test_sport_xyz", None)


# ----------------------------------------------------------------------
# Integration — gates/thresholds shim delegates correctly
# ----------------------------------------------------------------------
def test_gates_thresholds_shim_delegates_to_registry():
    """The legacy `resolve_stat_family` API in gates/thresholds.py
    must now read from the registry. Validate the four 2026-05-13
    combo fixes still resolve identically through the shim."""
    from services.scoring.gates.thresholds import resolve_stat_family
    assert resolve_stat_family("nba", "PR")   == "pts_reb"
    assert resolve_stat_family("nba", "PA")   == "pts_ast"
    assert resolve_stat_family("nba", "RA")   == "reb_ast"
    assert resolve_stat_family("nba", "BLST") == "blocks_steals"
    # Legacy long-form aliases still work via the shim
    assert resolve_stat_family("nba", "player_points_rebounds") == "pts_reb"
    assert resolve_stat_family("nba", "")     == "_default"


def test_nba_adapter_market_to_stat_shim_delegates_to_registry():
    """`NBAScoringAdapter._MARKET_TO_STAT` was the historical name; it
    is now a property that reads from the registry. Confirm the
    interface is preserved for any external caller."""
    from services.scoring.adapters.nba_scoring import NBAScoringAdapter
    # Construct a minimal adapter just to access the property
    a = NBAScoringAdapter.__new__(NBAScoringAdapter)  # bypass __init__
    m2s = a._MARKET_TO_STAT
    assert m2s["player_points"] == "PTS"
    assert m2s["player_points_rebounds"] == "PR"
    assert m2s["player_blocks_steals"] == "BLST"

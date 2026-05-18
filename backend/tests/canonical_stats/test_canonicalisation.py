"""Regression tests for stat-family canonicalisation (2026-05-18).

Verifies that the SSOT canonical_stats module is the single source for
stat-family resolution across all downstream callers, and that legacy
aliases (`"strikeouts"`, `"pitcher_walks"`) consistently resolve to
their canonical forms (`"batter_strikeouts"`, `"walks_allowed"`).
"""
from __future__ import annotations

import sys
import pytest

sys.path.insert(0, "/app/backend")

from services.scoring.canonical_stats import (  # noqa: E402
    market_to_family, canonical_family, stat_family,
    canonical_stat_type, iter_sports,
)
from services.scoring import canonical_stats  # noqa: E402  – triggers MLB registration
from services.replay.mlb_feature_cache import (  # noqa: E402
    market_to_stat_family, family_to_model_key,
    _STAT_FAMILY_MAP, _STAT_FIELD_MAP, _PITCHER_FAMILIES,
)


# ── canonical_stats — read-side normalization ──────────────────────
class TestCanonicalFamily:
    """`canonical_family` resolves any legacy alias to canonical form."""

    def test_strikeouts_alias_resolves_to_batter_strikeouts(self):
        assert canonical_family("mlb", "strikeouts") == "batter_strikeouts"

    def test_pitcher_walks_alias_resolves_to_walks_allowed(self):
        assert canonical_family("mlb", "pitcher_walks") == "walks_allowed"

    def test_already_canonical_returns_unchanged(self):
        assert canonical_family("mlb", "batter_strikeouts") \
               == "batter_strikeouts"
        assert canonical_family("mlb", "walks_allowed") \
               == "walks_allowed"
        assert canonical_family("mlb", "hits") == "hits"

    def test_pitcher_strikeouts_NOT_aliased_to_batter(self):
        """`pitcher_strikeouts` is its own family; must not collapse."""
        assert canonical_family("mlb", "pitcher_strikeouts") \
               == "pitcher_strikeouts"

    def test_case_insensitive(self):
        assert canonical_family("mlb", "STRIKEOUTS") \
               == "batter_strikeouts"
        assert canonical_family("mlb", "Pitcher_Walks") \
               == "walks_allowed"
        assert canonical_family("mlb", "Hits") == "hits"

    def test_unknown_sport_passes_through(self):
        # No registry → return input unchanged (not raise).
        assert canonical_family("xyz", "strikeouts") == "strikeouts"

    def test_strict_mode_raises_on_unknown(self):
        from services.scoring.canonical_stats import StatFamilyMissError
        with pytest.raises(StatFamilyMissError):
            canonical_family("mlb", "totally_unknown_family", strict=True)

    def test_empty_input_safe(self):
        assert canonical_family("mlb", "") == ""
        assert canonical_family("mlb", None) == ""


# ── canonical_stats — write-side (market_to_family) ────────────────
class TestMarketToFamily:
    """`market_to_family` is the canonical writer used by every layer."""

    @pytest.mark.parametrize("market,expected", [
        ("batter_hits",          "hits"),
        ("batter_total_bases",   "total_bases"),
        ("batter_runs_scored",   "runs"),
        ("batter_rbis",          "rbis"),
        ("batter_strikeouts",    "batter_strikeouts"),
        ("pitcher_strikeouts",   "pitcher_strikeouts"),
        ("pitcher_walks",        "walks_allowed"),
        ("pitcher_earned_runs",  "earned_runs"),
        ("pitcher_hits_allowed", "hits_allowed"),
        ("pitcher_outs",         "pitching_outs"),
    ])
    def test_market_to_family_canonical(self, market, expected):
        assert market_to_family("mlb", market) == expected

    def test_alternate_market_suffix_stripped(self):
        # `_alternate` markets must resolve to the same family as base.
        assert market_to_family("mlb", "batter_strikeouts_alternate") \
               == market_to_family("mlb", "batter_strikeouts")

    def test_market_to_family_idempotent_on_canonical(self):
        # Feeding a canonical family back in must round-trip.
        assert market_to_family("mlb", "batter_strikeouts") \
               == "batter_strikeouts"
        assert market_to_family("mlb", "walks_allowed") == "walks_allowed"

    def test_market_to_family_legacy_alias(self):
        # `strikeouts` (the legacy raw value) must still resolve.
        assert market_to_family("mlb", "strikeouts") \
               == "batter_strikeouts"

    def test_empty_input_returns_default(self):
        assert market_to_family("mlb", None) == "_default"
        assert market_to_family("mlb", "") == "_default"

    def test_unknown_market_returns_default(self):
        # Soft failure: log + return _default. No raise.
        result = market_to_family("mlb", "totally_unknown_market_xyz")
        assert result == "_default"


# ── mlb_feature_cache — writes canonical names ────────────────────
class TestMlbFeatureCacheCanonicalisation:
    """Verify cache layer writes canonical family names downstream."""

    def test_stat_family_map_uses_canonical(self):
        # Smoking-gun rows.
        assert _STAT_FAMILY_MAP["batter_strikeouts"] == "batter_strikeouts"
        assert _STAT_FAMILY_MAP["pitcher_walks"] == "walks_allowed"

    def test_stat_family_map_no_legacy_emissions(self):
        # NO entry must emit the legacy short forms.
        for v in _STAT_FAMILY_MAP.values():
            assert v not in ("strikeouts", "pitcher_walks"), \
                f"_STAT_FAMILY_MAP still emits legacy {v!r}"

    def test_market_to_stat_family_writes_canonical(self):
        assert market_to_stat_family("batter_strikeouts") \
               == "batter_strikeouts"
        assert market_to_stat_family("pitcher_walks") == "walks_allowed"

    def test_market_to_stat_family_strips_alternate(self):
        assert market_to_stat_family("batter_strikeouts_alternate") \
               == "batter_strikeouts"

    def test_stat_field_map_keys_are_canonical(self):
        # Statcast column lookup must accept canonical family tokens.
        assert _STAT_FIELD_MAP["batter_strikeouts"] == "strikeouts"
        assert _STAT_FIELD_MAP["walks_allowed"] == "pitcher_walks"
        assert _STAT_FIELD_MAP["pitcher_strikeouts"] \
               == "pitcher_strikeouts"

    def test_pitcher_families_use_canonical(self):
        assert "walks_allowed" in _PITCHER_FAMILIES
        assert "hits_allowed" in _PITCHER_FAMILIES
        assert "pitcher_walks" not in _PITCHER_FAMILIES
        assert "pitcher_hits_allowed" not in _PITCHER_FAMILIES
        assert "pitcher_strikeouts" in _PITCHER_FAMILIES


# ── mlb_feature_cache — model-key boundary translator ──────────────
class TestFamilyToModelKey:
    """`family_to_model_key` translates canonical → legacy at the
    model-call boundary ONLY. Idempotent for legacy inputs."""

    @pytest.mark.parametrize("canonical,model_key", [
        ("batter_strikeouts",   "strikeouts"),
        ("walks_allowed",       "pitcher_walks"),
        ("hits_allowed",        "hits_allowed"),
        ("hits_runs_rbis",      "hits+runs+rbis"),
        ("pitching_outs",       "pitcher_outs"),
    ])
    def test_canonical_to_legacy(self, canonical, model_key):
        assert family_to_model_key(canonical) == model_key

    @pytest.mark.parametrize("model_key", [
        "hits", "total_bases", "runs", "rbis", "pitcher_strikeouts",
        "earned_runs", "home_runs", "doubles",
        "stolen_bases",
    ])
    def test_already_legacy_passes_through(self, model_key):
        assert family_to_model_key(model_key) == model_key

    def test_none_safe(self):
        assert family_to_model_key(None) is None
        assert family_to_model_key("") == ""


# ── SSOT consistency — registry vs cache module ────────────────────
class TestRegistryConsistencyAcrossModules:
    """Both canonical_stats AND mlb_feature_cache must agree on the
    canonical form for every family. No drift permitted."""

    @pytest.mark.parametrize("market", [
        "batter_hits", "batter_total_bases", "batter_runs_scored",
        "batter_rbis", "batter_strikeouts", "pitcher_strikeouts",
        "pitcher_walks", "pitcher_earned_runs", "pitcher_outs",
    ])
    def test_market_to_family_agrees_across_modules(self, market):
        """The cache module's `market_to_stat_family` MUST match the
        SSOT `market_to_family` for the same input."""
        ssot = market_to_family("mlb", market)
        cache_layer = market_to_stat_family(market)
        assert ssot == cache_layer, (
            f"Drift detected for {market!r}: "
            f"canonical_stats={ssot!r}  mlb_feature_cache={cache_layer!r}"
        )


# ── No downstream consumer should see legacy raw names ─────────────
class TestNoDownstreamDriftFromLegacy:
    """Read-side: when legacy data exists in the DB (pre-fix rows
    with `stat_family="strikeouts"`), downstream consumers must
    still resolve to canonical via `canonical_family`."""

    def test_actual_for_handles_legacy_stat_family(self):
        from services.replay.mlb_replay_gate_eval import _actual_for
        # Path 1 — adapter contract: actuals dict keyed by family name.
        actuals_by_family = {
            "p1": {"batter_strikeouts": 2.0, "walks_allowed": 1.0,
                   "hits": 3.0},
        }
        # Both legacy and canonical inputs resolve.
        assert _actual_for(actuals_by_family, "p1", "strikeouts") == 2.0
        assert _actual_for(actuals_by_family, "p1", "pitcher_walks") == 1.0
        assert _actual_for(actuals_by_family, "p1", "batter_strikeouts") == 2.0
        assert _actual_for(actuals_by_family, "p1", "walks_allowed") == 1.0
        assert _actual_for(actuals_by_family, "p1", "hits") == 3.0
        # Path 2 — legacy statcast-column-keyed actuals dict still
        # resolves via the fallback chain.
        actuals_by_field = {
            "p2": {"strikeouts": 2.0, "pitcher_walks": 1.0, "hits": 3.0},
        }
        assert _actual_for(actuals_by_field, "p2", "strikeouts") == 2.0
        assert _actual_for(actuals_by_field, "p2", "batter_strikeouts") == 2.0
        assert _actual_for(actuals_by_field, "p2", "pitcher_walks") == 1.0
        assert _actual_for(actuals_by_field, "p2", "walks_allowed") == 1.0
        assert _actual_for(actuals_by_field, "p2", "hits") == 3.0

    def test_build_game_logs_handles_legacy_stat_family(self):
        from services.replay.mlb_replay_engine import _build_game_logs
        # Legacy stat_family in a cache row must produce log entries
        # keyed by the correct statcast column.
        cache_row = {
            "stat_family": "strikeouts",  # legacy alias
            "stat_values": [2.0, 0.0, 1.0],
            "pa_values":   [4.0, 3.0, 4.0],
            "dates":       ["2026-05-04", "2026-05-03", "2026-05-02"],
        }
        logs = _build_game_logs(cache_row)
        assert len(logs) == 3
        assert all("strikeouts" in log for log in logs), \
            "Statcast field name must be `strikeouts` for batter_K"
        assert logs[0]["strikeouts"] == 2.0


# ── Sport-agnostic guard — no hardcoded MLB-only logic ─────────────
class TestSportAgnosticInterface:
    """The canonical helpers MUST work for any registered sport.
    Adding NBA or NFL must not require touching this module."""

    def test_iter_sports_includes_mlb(self):
        sports = list(iter_sports())
        assert "mlb" in sports

    def test_unknown_sport_safe(self):
        # Pass-through behaviour, never raise.
        assert canonical_family("nfl", "strikeouts") == "strikeouts"
        assert market_to_family("nfl", "anything") == "_default"

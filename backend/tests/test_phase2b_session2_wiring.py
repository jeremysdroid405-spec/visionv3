"""Phase 2B Session 2 — wiring tests for the opposing-lineup
end-to-end path from `feature_hydration.py` through the model's
`_build_friction_features` and `predict()` entry points.

Locks the contract that:
  1. `_build_friction_features` accepts `opposing_lineup` and
     `sc_batter_cache` kwargs and emits the canonical 21-feature
     CATEGORY 9 block on every call.
  2. `predict()` threads `opposing_lineup` through to the builder.
  3. The hydration block's inline `rolling_14` payload is honoured
     (live path) over the external cache (training path) without
     requiring a `game_date` lookup.
  4. Batter props (stat not in pitcher set) still emit the lineup
     block in imputed form — feature vector shape is invariant
     across stat-family.
"""
from __future__ import annotations

import importlib
import inspect

import pytest

from services.mlb_lineup_features import (
    PHASE2B_LINEUP_FEATURE_NAMES,
    build_lineup_features,
)


# ─── Feature builder signature contract ─────────────────────────
class TestFeatureBuilderSignature:
    def test_build_friction_features_accepts_phase2b_kwargs(self):
        """`_build_friction_features` must accept the two new
        Phase-2B kwargs without raising TypeError. Locks the
        signature so future refactors can't drop them silently.
        """
        from services.mlb_high_friction_model import MLBHighFrictionModel
        sig = inspect.signature(
            MLBHighFrictionModel._build_friction_features
        )
        assert "opposing_lineup" in sig.parameters
        assert "sc_batter_cache" in sig.parameters
        # Both default to None — older callers stay working.
        assert sig.parameters["opposing_lineup"].default is None
        assert sig.parameters["sc_batter_cache"].default is None

    def test_predict_threads_opposing_lineup(self):
        """`predict()` exposes `opposing_lineup` so the live scoring
        adapter can forward `prop["opposing_lineup"]` straight through.
        """
        from services.mlb_high_friction_model import MLBHighFrictionModel
        sig = inspect.signature(MLBHighFrictionModel.predict)
        assert "opposing_lineup" in sig.parameters
        assert sig.parameters["opposing_lineup"].default is None


# ─── Inline rolling-14 path (live wiring) ───────────────────────
class TestInlineRollingPath:
    """When the caller decorates each batter dict with an inline
    `rolling_14` block, `build_lineup_features` must use it WITHOUT
    consulting `sc_batter_cache` and WITHOUT a `game_date`. This is
    the live-prediction wiring path: `feature_hydration.py` attaches
    `rolling_14` per batter and the model never needs the heavyweight
    cache structure at prediction time.
    """

    def _lineup_with_inline(self):
        return [
            {"batter_id": 1, "stand": "R", "rolling_14": {
                "k_rate": 0.20, "bb_rate": 0.08, "wOBA": 0.340,
                "xwOBA": 0.330, "hard_hit_rate": 0.42, "barrel_rate": 0.09,
            }},
            {"batter_id": 2, "stand": "L", "rolling_14": {
                "k_rate": 0.30, "bb_rate": 0.10, "wOBA": 0.300,
                "xwOBA": 0.295, "hard_hit_rate": 0.35, "barrel_rate": 0.07,
            }},
        ]

    def test_inline_rolling_resolves_strength_without_cache(self):
        f = build_lineup_features(
            lineup=self._lineup_with_inline(),
            game_date=None,            # ← no date needed
            pitcher_throws="R",
            sc_batter_cache=None,      # ← no external cache
        )
        assert f["lineup_strength_is_imputed"] == 0.0
        assert f["lineup_k_rate_14d"] == pytest.approx(0.25)
        assert f["lineup_woba_14d"] == pytest.approx(0.320)

    def test_inline_rolling_overrides_external_cache(self):
        """When both inputs are available, inline wins (no leakage
        risk + cheaper)."""
        cache = {
            1: {"2024-06-15": {"rolling_14": {"k_rate": 0.99}}},
        }
        f = build_lineup_features(
            lineup=self._lineup_with_inline(),
            game_date="2024-06-15",
            pitcher_throws="R",
            sc_batter_cache=cache,
        )
        # Inline k_rate was 0.20 + 0.30 ⇒ mean 0.25. If cache had
        # been honoured for batter 1, the mean would shift toward
        # 0.99 — assert we stayed on the inline value.
        assert f["lineup_k_rate_14d"] == pytest.approx(0.25)


# ─── Hydration helper — inline decoration ───────────────────────
class TestHydrationDecorator:
    def test_attach_inline_rolling_resolves_as_of(self):
        fh = importlib.import_module("services.feature_hydration")
        attach = fh._attach_inline_rolling_to_lineup
        cache = {
            100: {
                "2024-06-01": {"rolling_14": {"k_rate": 0.10}},
                "2024-06-10": {"rolling_14": {"k_rate": 0.25}},
            },
        }
        lineup = [{"batter_id": 100, "stand": "R"}]
        decorated = attach(lineup, "2024-06-15", cache)
        assert decorated[0]["rolling_14"]["k_rate"] == 0.25

    def test_attach_skips_when_no_cache_entry(self):
        fh = importlib.import_module("services.feature_hydration")
        decorated = fh._attach_inline_rolling_to_lineup(
            [{"batter_id": 999, "stand": "L"}],
            "2024-06-15", {},
        )
        assert "rolling_14" not in decorated[0]

    def test_attach_returns_new_list_does_not_mutate(self):
        fh = importlib.import_module("services.feature_hydration")
        original = [{"batter_id": 100, "stand": "R"}]
        cache = {100: {"2024-06-15": {"rolling_14": {"k_rate": 0.5}}}}
        decorated = fh._attach_inline_rolling_to_lineup(
            original, "2024-06-15", cache,
        )
        # Caller's dicts are NOT mutated.
        assert "rolling_14" not in original[0]
        assert decorated[0] is not original[0]
        assert decorated[0]["rolling_14"]["k_rate"] == 0.5


# ─── Pitcher-stat type registry contract ────────────────────────
class TestPitcherStatRegistry:
    def test_canonical_pitcher_stat_types(self):
        fh = importlib.import_module("services.feature_hydration")
        expected = {
            "Pitcher Strikeouts",
            "Pitcher Outs",
            "Earned Runs",
            "Hits Allowed",
            "Walks Allowed",
            "Pitcher Walks",
        }
        assert set(fh._PITCHER_STAT_TYPES) == expected

    def test_batter_stat_not_in_pitcher_registry(self):
        fh = importlib.import_module("services.feature_hydration")
        for s in ("Hits", "Home Runs", "Total Bases", "RBIs",
                  "Hits+Runs+RBIs", "Batter Strikeouts"):
            assert s not in fh._PITCHER_STAT_TYPES


# ─── Score-doc allowlist propagation ────────────────────────────
class TestScoreDocAllowlist:
    def test_opposing_lineup_size_in_score_output_fields(self):
        from services.scoring.prop_scores_store import _SCORE_OUTPUT_FIELDS
        assert "opposing_lineup_size" in _SCORE_OUTPUT_FIELDS

    def test_lineup_payload_itself_not_in_allowlist(self):
        """The full lineup list is volatile and rebuildable — only
        the size diagnostic propagates onto score docs.
        """
        from services.scoring.prop_scores_store import _SCORE_OUTPUT_FIELDS
        assert "opposing_lineup" not in _SCORE_OUTPUT_FIELDS


# ─── Schema invariance across stat types ───────────────────────
class TestSchemaInvariance:
    def test_empty_lineup_emits_all_21_features_imputed(self):
        f = build_lineup_features(
            lineup=None, game_date=None, pitcher_throws=None,
        )
        # All 21 features present.
        for name in PHASE2B_LINEUP_FEATURE_NAMES:
            assert name in f, f"missing feature: {name}"
        # All four imputed flags raised.
        for flag in ("lineup_size_is_imputed",
                     "lineup_handedness_is_imputed",
                     "lineup_strength_is_imputed",
                     "matchup_exposure_is_imputed"):
            assert f[flag] == 1.0

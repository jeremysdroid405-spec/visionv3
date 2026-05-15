"""Phase 2B — Unit tests for opposing-lineup feature aggregation.

Locks the feature schema, the imputation contract, the handedness
counting math, and the matchup-interaction logic. These tests must
pass BEFORE the retrain worker is run so the train-predict feature
vector cannot drift in shape between sessions.
"""
from __future__ import annotations

import pytest

from services.mlb_lineup_features import (
    PHASE2B_LINEUP_FEATURE_NAMES,
    build_lineup_features,
)


def _lineup(*hands):
    """Build a lineup list from a sequence of stands ('L', 'R', 'S', None)."""
    return [
        {"batter_id": 100 + i, "stand": h,
         "n_pitches": 0, "first_appearance_order": i + 1}
        for i, h in enumerate(hands)
    ]


# ─── Schema contract ─────────────────────────────────────────────
class TestSchemaContract:
    def test_empty_lineup_returns_all_imputed_flags_raised(self):
        f = build_lineup_features(lineup=None, game_date=None,
                                  pitcher_throws=None)
        # Every declared feature is present.
        for k in PHASE2B_LINEUP_FEATURE_NAMES:
            assert k in f, f"missing feature: {k}"
        # All four imputed flags raised.
        for flag in ("lineup_size_is_imputed",
                     "lineup_handedness_is_imputed",
                     "lineup_strength_is_imputed",
                     "matchup_exposure_is_imputed"):
            assert f[flag] == 1.0

    def test_feature_count_locked(self):
        # 21 declared features — bumping requires a model-version bump.
        assert len(PHASE2B_LINEUP_FEATURE_NAMES) == 21

    def test_typical_lineup_has_no_extra_keys(self):
        f = build_lineup_features(
            lineup=_lineup("L", "R", "R", "S", "L", "R", "R", "L", "R"),
            game_date="2024-06-15", pitcher_throws="R",
        )
        assert set(f.keys()) == set(PHASE2B_LINEUP_FEATURE_NAMES)


# ─── Handedness counting ─────────────────────────────────────────
class TestHandednessCounts:
    def test_all_righties_against_righty(self):
        f = build_lineup_features(
            lineup=_lineup("R", "R", "R", "R", "R", "R", "R", "R", "R"),
            game_date=None, pitcher_throws="R",
        )
        assert f["projected_rhh_count"] == 9
        assert f["projected_lhh_count"] == 0
        assert f["pct_rhh"] == 1.0
        assert f["lineup_handedness_is_imputed"] == 0.0
        # All same-hand vs righty pitcher.
        assert f["lineup_same_hand_count"] == 9
        assert f["lineup_opposite_hand_count"] == 0

    def test_typical_mix_against_righty(self):
        # 3L / 5R / 1S vs RHP → 5 same-hand (R), 4 opposite-hand (L+S)
        f = build_lineup_features(
            lineup=_lineup("L", "L", "L", "R", "R", "R", "R", "R", "S"),
            game_date=None, pitcher_throws="R",
        )
        assert f["projected_lhh_count"] == 3
        assert f["projected_rhh_count"] == 5
        assert f["projected_switch_count"] == 1
        assert f["lineup_same_hand_count"] == 5
        assert f["lineup_opposite_hand_count"] == 4
        assert f["lineup_pct_same_hand"] == pytest.approx(5 / 9)
        assert f["lineup_pct_opposite_hand"] == pytest.approx(4 / 9)

    def test_switch_hitters_always_count_opposite_hand(self):
        # 9 switch-hitters vs LHP → all 9 platoon-advantage (bat R) → opposite
        f = build_lineup_features(
            lineup=_lineup("S", "S", "S", "S", "S", "S", "S", "S", "S"),
            game_date=None, pitcher_throws="L",
        )
        assert f["projected_switch_count"] == 9
        assert f["lineup_same_hand_count"] == 0
        assert f["lineup_opposite_hand_count"] == 9
        # Same setup vs RHP also opposite
        f2 = build_lineup_features(
            lineup=_lineup("S", "S", "S", "S", "S", "S", "S", "S", "S"),
            game_date=None, pitcher_throws="R",
        )
        assert f2["lineup_opposite_hand_count"] == 9

    def test_unknown_stands_do_not_break_aggregation(self):
        # 5 known + 4 unknown. Counts use known only.
        f = build_lineup_features(
            lineup=_lineup("L", "R", "R", None, None, None, None, "L", "S"),
            game_date=None, pitcher_throws="R",
        )
        assert f["lineup_size"] == 9
        assert f["projected_lhh_count"] == 2
        assert f["projected_rhh_count"] == 2
        assert f["projected_switch_count"] == 1
        # 5 known out of 9 → >= half known → not imputed
        assert f["lineup_handedness_is_imputed"] == 0.0

    def test_mostly_unknown_lineup_keeps_handedness_imputed(self):
        f = build_lineup_features(
            lineup=_lineup("L", None, None, None, None, None, None, None, None),
            game_date=None, pitcher_throws="R",
        )
        assert f["lineup_handedness_is_imputed"] == 1.0


# ─── Matchup exposure ───────────────────────────────────────────
class TestMatchupExposure:
    def test_matchup_imputed_when_pitcher_throws_unknown(self):
        f = build_lineup_features(
            lineup=_lineup("L", "R", "R", "L"),
            game_date=None, pitcher_throws=None,
        )
        assert f["matchup_exposure_is_imputed"] == 1.0
        assert f["lineup_same_hand_count"] == 0

    def test_matchup_not_imputed_when_pitcher_known(self):
        f = build_lineup_features(
            lineup=_lineup("L", "R", "R", "L"),
            game_date=None, pitcher_throws="L",
        )
        assert f["matchup_exposure_is_imputed"] == 0.0
        # Vs LHP: same = LHH count = 2; opposite = RHH + S = 2
        assert f["lineup_same_hand_count"] == 2
        assert f["lineup_opposite_hand_count"] == 2


# ─── Lineup strength rolling 14 ──────────────────────────────────
class TestLineupStrength:
    def test_strength_imputed_when_no_cache(self):
        f = build_lineup_features(
            lineup=_lineup("R", "R", "R", "R", "R", "R", "R", "R", "R"),
            game_date="2024-06-15", pitcher_throws="R",
            sc_batter_cache=None,
        )
        assert f["lineup_strength_is_imputed"] == 1.0
        assert f["lineup_k_rate_14d"] == 0.0

    def test_strength_means_over_lineup_with_full_coverage(self):
        cache = {
            100: {"2024-06-15": {"rolling_14": {
                "k_rate": 0.20, "bb_rate": 0.08, "wOBA": 0.350,
                "xwOBA": 0.330, "hard_hit_rate": 0.40, "barrel_rate": 0.08,
            }}},
            101: {"2024-06-15": {"rolling_14": {
                "k_rate": 0.30, "bb_rate": 0.10, "wOBA": 0.300,
                "xwOBA": 0.290, "hard_hit_rate": 0.30, "barrel_rate": 0.06,
            }}},
        }
        f = build_lineup_features(
            lineup=[
                {"batter_id": 100, "stand": "R"},
                {"batter_id": 101, "stand": "L"},
            ],
            game_date="2024-06-15", pitcher_throws="R",
            sc_batter_cache=cache,
        )
        assert f["lineup_k_rate_14d"] == pytest.approx(0.25)
        assert f["lineup_bb_rate_14d"] == pytest.approx(0.09)
        assert f["lineup_woba_14d"] == pytest.approx(0.325)
        assert f["lineup_xwoba_14d"] == pytest.approx(0.310)
        assert f["lineup_strength_is_imputed"] == 0.0

    def test_strength_as_of_lookup_uses_latest_prior_date(self):
        cache = {
            200: {
                "2024-06-01": {"rolling_14": {"k_rate": 0.10}},
                "2024-06-10": {"rolling_14": {"k_rate": 0.25}},
                "2024-06-20": {"rolling_14": {"k_rate": 0.50}},
            },
        }
        # Game on 2024-06-15 → should use 2024-06-10's rolling (k=0.25),
        # not the future 2024-06-20 (k=0.50) — strict leakage prevention.
        f = build_lineup_features(
            lineup=[{"batter_id": 200, "stand": "R"}],
            game_date="2024-06-15", pitcher_throws="R",
            sc_batter_cache=cache,
        )
        assert f["lineup_k_rate_14d"] == pytest.approx(0.25)


# ─── Lineup size handling ───────────────────────────────────────
class TestLineupSize:
    def test_partial_lineup_records_actual_size(self):
        f = build_lineup_features(
            lineup=_lineup("L", "R", "R"),
            game_date=None, pitcher_throws="R",
        )
        assert f["lineup_size"] == 3
        assert f["lineup_size_is_imputed"] == 0.0

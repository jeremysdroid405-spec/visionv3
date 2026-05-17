"""Olson predict() vs replay_one() μ parity + feature-vector parity.

Locks in the 2026-05-17 hydration fix. Any silent regression that
re-introduces the μ ≈ 7.9 inflation will fail this test.
"""
import pandas as pd
import pytest

from services.replay.mlb_replay_engine import (
    _build_player_dict, _build_game_logs, _opp_team_from_event,
    _derive_batter_hand_from_hub, replay_one,
)
from tests.replay.conftest import OLSON_DATE, OLSON_STAT

# Tolerances — when both paths use IDENTICAL inputs (sc cache,
# opp_pitcher_throws), they MUST be byte-identical. We allow a tiny
# float epsilon for safety only.
EPSILON = 1e-6
# Expected μ range when replay uses cache's as-of Statcast — empirical
# from the 05-05 rebuild + Path A Task 2d trace.
EXPECTED_MU_LOW  = 2.5
EXPECTED_MU_HIGH = 3.6


def test_olson_replay_one_mu_within_expected_band(
    model, olson_cache_row, olson_odds_row, olson_hub_extras,
):
    """The post-hydration replay should produce μ in [2.5, 3.6] for
    Olson total_bases @ line 1.5. Pre-fix μ was 7.78. Live predict
    with full hydration is ~2.25; the band accounts for the cache's
    pinned Statcast bundle vs live latest."""
    out = replay_one(model, olson_cache_row, olson_odds_row,
                     hub_extras=olson_hub_extras)
    assert out is not None
    mu = out["projection_mu"]
    assert EXPECTED_MU_LOW <= mu <= EXPECTED_MU_HIGH, (
        f"Olson replay μ={mu} outside [{EXPECTED_MU_LOW}, "
        f"{EXPECTED_MU_HIGH}] — hydration regression likely"
    )
    # Hard ceiling: must NEVER produce the legacy inflation.
    assert mu < 4.5, f"μ={mu} ≥ 4.5 — inflation regression"


def test_olson_predict_eq_replay_one_byte_identical(
    model, olson_cache_row, olson_hub_extras, olson_odds_row, olson_hub,
):
    """When predict() and replay_one() receive IDENTICAL feature
    inputs, the resulting 222-column feature vector must be byte-
    identical (proves the fix is structural, not coincidence)."""
    cache_row = olson_cache_row
    hub_extras = olson_hub_extras
    odds_row = olson_odds_row

    # Build "replay-equivalent for live": feed predict()'s builder with
    # the SAME inputs that replay_one builds.
    opp, is_away = _opp_team_from_event(
        cache_row, odds_row.get("home_team") or "",
        odds_row.get("away_team") or "")
    park_replay = cache_row.get("team") if not is_away else opp
    sc_replay = cache_row.get("statcast_self_as_of")

    pa_cache = model._get_pa_cache()
    pa_replay = pa_cache.batter_features(
        int(cache_row["player_id"]), OLSON_DATE) if pa_cache else None

    bh = _derive_batter_hand_from_hub(hub_extras)
    opp_throws = cache_row.get("opp_pitcher_throws")  # None — no probable in cache

    # PATH A — predict()-shaped build with hub-data player + same SC + same PA
    hub_logs = model._filter_logs_before(
        olson_hub.get("bdl_game_logs") or [], OLSON_DATE)
    feats_predict_eq = model._build_friction_features(
        olson_hub, hub_logs, OLSON_STAT,
        opponent=opp, park_team=park_replay,
        dk_odds=None, line=1.5,
        statcast_features=sc_replay,
        pitcher_statcast_features=None,
        pa_batter_features=pa_replay,
        pa_pitcher_features=None,
        batter_hand=bh,
        opp_pitcher_throws=opp_throws,
    )

    # PATH B — replay_one()'s synth path
    player_synth = _build_player_dict(cache_row, hub_extras=hub_extras)
    logs_synth = _build_game_logs(cache_row)
    feats_replay = model._build_friction_features(
        player_synth, logs_synth, OLSON_STAT,
        opponent=opp, park_team=park_replay,
        dk_odds=None, line=1.5,
        statcast_features=sc_replay,
        pitcher_statcast_features=None,
        pa_batter_features=pa_replay,
        pa_pitcher_features=None,
        batter_hand=bh,
        opp_pitcher_throws=opp_throws,
    )

    train_cols = model.feature_cols[OLSON_STAT]
    diffs = []
    for c in train_cols:
        a = float(feats_predict_eq.get(c, 0.0) or 0.0)
        b = float(feats_replay.get(c, 0.0) or 0.0)
        if abs(a - b) > EPSILON:
            diffs.append((c, a, b))
    assert not diffs, (
        f"feature vector diverged in {len(diffs)} columns "
        f"out of {len(train_cols)}: first 5 = {diffs[:5]}"
    )


def test_olson_xgboost_score_is_realistic(
    model, olson_cache_row, olson_hub_extras, olson_odds_row,
):
    """Direct XGBoost re-scoring on the post-fix feature vector must
    land in the realistic batter total_bases range."""
    out = replay_one(model, olson_cache_row, olson_odds_row,
                     hub_extras=olson_hub_extras)
    raw = out["raw_prediction"]
    assert 1.5 <= raw <= 4.0, (
        f"raw XGBoost output {raw} outside realistic band — model "
        f"or feature regression"
    )

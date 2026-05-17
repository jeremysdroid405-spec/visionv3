"""Hub-extras hydration assertions.

Locks in that the platoon/home-away/handedness/PA blocks actually
make it into the replay feature vector when `hub_extras` is supplied.
"""
import pytest
from services.replay.mlb_replay_engine import (
    _build_player_dict, _build_game_logs, replay_one,
)
from tests.replay.conftest import OLSON_STAT


def test_player_dict_carries_platoon_splits(olson_cache_row, olson_hub_extras):
    p = _build_player_dict(olson_cache_row, hub_extras=olson_hub_extras)
    assert p.get("vs_left"), "vs_left did NOT propagate from hub_extras"
    assert p.get("vs_right"), "vs_right did NOT propagate from hub_extras"
    # Olson is a power hitter with 400+ at_bats vs RHP
    assert (p["vs_right"].get("at_bats") or 0) > 100, (
        f"vs_right.at_bats={p['vs_right'].get('at_bats')} suspiciously low"
    )


def test_player_dict_carries_home_away_splits(olson_cache_row, olson_hub_extras):
    p = _build_player_dict(olson_cache_row, hub_extras=olson_hub_extras)
    assert p.get("home_splits"), "home_splits did NOT propagate"
    assert p.get("away_splits"), "away_splits did NOT propagate"


def test_player_dict_carries_bats_throws(olson_cache_row, olson_hub_extras):
    p = _build_player_dict(olson_cache_row, hub_extras=olson_hub_extras)
    bt = p.get("bats_throws")
    assert bt, "bats_throws did NOT propagate"
    # Olson bats Left/Throws Right
    assert bt.lower().startswith("left"), f"unexpected bats_throws={bt}"


def test_player_dict_without_hub_extras_is_safe(olson_cache_row):
    """Passing hub_extras=None must NOT raise and must NOT inject the
    keys (so the imputed flags fire correctly downstream)."""
    p = _build_player_dict(olson_cache_row, hub_extras=None)
    for k in ("vs_left", "vs_right", "home_splits", "away_splits"):
        assert k not in p, f"unexpected key {k} when hub_extras=None"


def test_full_replay_feature_block_populated(
    model, olson_cache_row, olson_hub_extras, olson_odds_row,
):
    """Sanity: after replay_one() runs, the feature vector returned by
    `_build_friction_features` should have NON-ZERO platoon/home-away
    columns (the blocks would be 0/imputed without hydration)."""
    # Re-build the feature vector the way replay_one does
    from services.replay.mlb_replay_engine import (
        _opp_team_from_event, _derive_batter_hand_from_hub,
    )
    opp, is_away = _opp_team_from_event(
        olson_cache_row,
        olson_odds_row.get("home_team") or "",
        olson_odds_row.get("away_team") or "")
    park = olson_cache_row.get("team") if not is_away else opp
    player = _build_player_dict(olson_cache_row, hub_extras=olson_hub_extras)
    logs = _build_game_logs(olson_cache_row)
    bh = _derive_batter_hand_from_hub(olson_hub_extras)
    pa_cache = model._get_pa_cache()
    pa = pa_cache.batter_features(
        int(olson_cache_row["player_id"]), "2026-05-06") if pa_cache else None
    feats = model._build_friction_features(
        player, logs, OLSON_STAT,
        opponent=opp, park_team=park, dk_odds=None, line=1.5,
        statcast_features=olson_cache_row.get("statcast_self_as_of"),
        pa_batter_features=pa,
        batter_hand=bh,
        opp_pitcher_throws=olson_cache_row.get("opp_pitcher_throws"),
    )
    assert feats is not None
    # Platoon non-zero
    assert (feats.get("vs_rhp_ab") or 0) > 100, (
        f"vs_rhp_ab={feats.get('vs_rhp_ab')} — platoon hydration failed"
    )
    assert feats.get("platoon_split_is_imputed") == 0, (
        "platoon_split_is_imputed=1 after hydration — block did NOT fire"
    )
    # Home-away non-zero
    assert feats.get("home_away_split_is_imputed") == 0, (
        "home_away_split_is_imputed=1 after hydration — block did NOT fire"
    )
    # Handedness one-hot — Olson is LHH
    assert feats.get("batter_is_lhh") == 1.0, (
        f"batter_is_lhh={feats.get('batter_is_lhh')} — handedness wrong"
    )
    assert feats.get("batter_hand_is_imputed") == 0
    # PA block — at least one season-window feature non-zero
    assert (feats.get("pa_b_pa_season_plate_appearances") or 0) > 0, (
        "pa_b_pa_season_plate_appearances=0 — PA cache hydration failed"
    )

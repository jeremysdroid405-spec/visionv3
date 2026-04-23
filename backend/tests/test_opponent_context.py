"""Regression tests for `services/features/opponent_context.py` (2026-04-23).

Guards:
  * FEATURE_SCHEMA shape + count
  * Build from an in-memory fake data source
  * Leakage: target game's own stats never contribute to its features
  * Rest days: correct delta, capped at 6, defaults to 3 for openers
  * Back-to-back: rest_days == 0 → back_to_back_flag == 1
  * Home flag resolves from context row when available
  * Zero-safe fallback when (team_id, game_id) missing
  * Opponent ID resolution + symmetric A↔B mapping
"""
from __future__ import annotations

from datetime import date

import pytest

from services.features.opponent_context import (
    FEATURE_SCHEMA,
    OpponentContextStore,
    resolve_opponent_team_id,
)


def _make_store() -> OpponentContextStore:
    """Construct a store with three synthetic games between team 10 and 20.
    game 1:   team 10 scores 100 pts, team 20 scores 90 pts, home team 10
    game 2:   team 10 vs 30 (unrelated but builds a longer sequence)
    game 3:   team 10 vs 20, our "target"
    """
    team_game_allowed = {
        (10, 1): {"pts": 100.0, "reb": 40.0, "ast": 25.0, "fg3m": 12.0, "game_date": date(2024, 11, 1)},
        (20, 1): {"pts": 90.0,  "reb": 42.0, "ast": 23.0, "fg3m": 10.0, "game_date": date(2024, 11, 1)},
        (10, 2): {"pts": 110.0, "reb": 44.0, "ast": 27.0, "fg3m": 14.0, "game_date": date(2024, 11, 3)},
        (30, 2): {"pts": 105.0, "reb": 45.0, "ast": 22.0, "fg3m": 11.0, "game_date": date(2024, 11, 3)},
        (10, 3): {"pts": 120.0, "reb": 46.0, "ast": 30.0, "fg3m": 15.0, "game_date": date(2024, 11, 10)},
        (20, 3): {"pts": 95.0,  "reb": 41.0, "ast": 24.0, "fg3m":  9.0, "game_date": date(2024, 11, 10)},
    }
    team_game_context = {
        (10, 1): {"pace": 100.0, "defensive_rating": 110.0, "offensive_rating": 120.0,
                  "is_home": True,  "opponent_team_id": 20, "game_date": date(2024, 11, 1)},
        (20, 1): {"pace": 98.0,  "defensive_rating": 115.0, "offensive_rating": 110.0,
                  "is_home": False, "opponent_team_id": 10, "game_date": date(2024, 11, 1)},
        (10, 2): {"pace": 101.0, "defensive_rating": 108.0, "offensive_rating": 122.0,
                  "is_home": False, "opponent_team_id": 30, "game_date": date(2024, 11, 3)},
        (30, 2): {"pace": 102.0, "defensive_rating": 120.0, "offensive_rating": 112.0,
                  "is_home": True,  "opponent_team_id": 10, "game_date": date(2024, 11, 3)},
        (10, 3): {"pace": 103.0, "defensive_rating": 106.0, "offensive_rating": 124.0,
                  "is_home": True,  "opponent_team_id": 20, "game_date": date(2024, 11, 10)},
        (20, 3): {"pace": 99.0,  "defensive_rating": 117.0, "offensive_rating": 108.0,
                  "is_home": False, "opponent_team_id": 10, "game_date": date(2024, 11, 10)},
    }
    team_games_sorted = {
        10: [(date(2024, 11, 1), 1), (date(2024, 11, 3), 2), (date(2024, 11, 10), 3)],
        20: [(date(2024, 11, 1), 1), (date(2024, 11, 10), 3)],
        30: [(date(2024, 11, 3), 2)],
    }
    opponent_map = {
        1: {10: 20, 20: 10},
        2: {10: 30, 30: 10},
        3: {10: 20, 20: 10},
    }
    league = {
        "pts": 100.0, "reb": 43.0, "ast": 25.0, "fg3m": 11.0,
    }
    return OpponentContextStore(
        team_game_allowed=team_game_allowed,
        team_game_context=team_game_context,
        team_games_sorted=team_games_sorted,
        league_avg_allowed=league,
        opponent_map=opponent_map,
        seasons=(2024,),
    )


def test_schema_shape():
    assert len(FEATURE_SCHEMA) == 14
    # Required names from the spec
    required = {
        "opp_pts_allowed_L10", "opp_reb_allowed_L10",
        "opp_ast_allowed_L10", "opp_3pm_allowed_L10",
        "opp_pts_allowed_vs_avg", "opp_reb_allowed_vs_avg",
        "opp_ast_allowed_vs_avg", "opp_3pm_allowed_vs_avg",
        "opp_def_rating", "opp_pace", "team_pace",
        "home_flag", "rest_days", "back_to_back_flag",
    }
    assert set(FEATURE_SCHEMA) == required


def test_resolve_opponent_team_id_symmetric():
    store = _make_store()
    assert resolve_opponent_team_id(store, 10, 1) == 20
    assert resolve_opponent_team_id(store, 20, 1) == 10
    assert resolve_opponent_team_id(store, 99, 1) is None
    assert resolve_opponent_team_id(store, 10, 9999) is None


def test_no_same_game_leakage_for_target():
    """When we request features for game_id=3 (team 10 vs team 20),
    the opp_*_allowed_L10 for team 20 must average team 20's PRIOR
    games (only game 1), NOT game 3 itself."""
    store = _make_store()
    feats = store.get_features(
        team_id=10, opponent_team_id=20, game_id=3, game_date=date(2024, 11, 10),
    )
    # Team 20 played only game 1 before game 3. game 1 team 20 allowed:
    # pts=100 (team 10 scored 100 on them).
    assert feats["opp_pts_allowed_L10"] == pytest.approx(100.0, abs=0.1)
    assert feats["opp_reb_allowed_L10"] == pytest.approx(40.0, abs=0.1)
    assert feats["opp_ast_allowed_L10"] == pytest.approx(25.0, abs=0.1)
    assert feats["opp_3pm_allowed_L10"] == pytest.approx(12.0, abs=0.1)


def test_relative_strength_vs_league_avg():
    store = _make_store()
    feats = store.get_features(
        team_id=10, opponent_team_id=20, game_id=3, game_date=date(2024, 11, 10),
    )
    # opp_pts_allowed_L10 == 100.0, league avg == 100.0 → delta == 0
    assert feats["opp_pts_allowed_vs_avg"] == pytest.approx(0.0, abs=0.1)
    # reb allowed 40, league 43 → -3
    assert feats["opp_reb_allowed_vs_avg"] == pytest.approx(-3.0, abs=0.1)


def test_rest_days_delta_and_cap():
    store = _make_store()
    # Team 10 last played Nov 3, now playing Nov 10 → 6 days rest (7-1)
    feats = store.get_features(
        team_id=10, opponent_team_id=20, game_id=3, game_date=date(2024, 11, 10),
    )
    assert feats["rest_days"] == 6.0

    # Team 20 last played Nov 1, now playing Nov 10 → cap at 6
    feats20 = store.get_features(
        team_id=20, opponent_team_id=10, game_id=3, game_date=date(2024, 11, 10),
    )
    assert feats20["rest_days"] == 6.0
    assert feats20["back_to_back_flag"] == 0.0


def test_back_to_back_flag_fires_on_zero_rest():
    store = _make_store()
    # Manually poke the store: team 10 played game_id=100 yesterday.
    store.team_games_sorted[10].insert(
        0, (date(2024, 10, 31), 100)
    )
    store.team_game_allowed[(10, 100)] = {
        "pts": 100.0, "reb": 40.0, "ast": 25.0, "fg3m": 10.0,
        "game_date": date(2024, 10, 31),
    }
    # Re-sort
    store.team_games_sorted[10].sort(key=lambda x: (x[0], x[1]))
    feats = store.get_features(
        team_id=10, opponent_team_id=20, game_id=1, game_date=date(2024, 11, 1),
    )
    assert feats["rest_days"] == 0.0
    assert feats["back_to_back_flag"] == 1.0


def test_home_flag_from_context_row():
    store = _make_store()
    # Team 10 is home in game 3
    feats_home = store.get_features(
        team_id=10, opponent_team_id=20, game_id=3, game_date=date(2024, 11, 10),
    )
    assert feats_home["home_flag"] == 1.0
    # Team 20 is away
    feats_away = store.get_features(
        team_id=20, opponent_team_id=10, game_id=3, game_date=date(2024, 11, 10),
    )
    assert feats_away["home_flag"] == 0.0


def test_explicit_is_home_override():
    """Live scoring passes is_home explicitly; must win over context row."""
    store = _make_store()
    feats = store.get_features(
        team_id=10, opponent_team_id=20, game_id=3, game_date=date(2024, 11, 10),
        is_home=False,
    )
    assert feats["home_flag"] == 0.0


def test_zero_safe_fallback_for_missing_ids():
    store = _make_store()
    feats = store.get_features(
        team_id=None, opponent_team_id=None, game_id=None,
    )
    assert feats["opp_pts_allowed_L10"] == 0.0
    assert feats["opp_def_rating"] == 0.0
    assert feats["home_flag"] == 0.0
    assert feats["rest_days"] == 3.0  # default for "unknown" situation
    assert feats["back_to_back_flag"] == 0.0


def test_store_built_from_mongo_is_nonempty():
    """End-to-end smoke test — requires live MongoDB with BDL logs."""
    import os
    from pymongo import MongoClient
    if "MONGO_URL" not in os.environ:
        pytest.skip("MONGO_URL not set")
    try:
        with open("/app/backend/.env") as f:
            for line in f:
                if line.startswith("MONGO_URL="):
                    os.environ["MONGO_URL"] = line.split("=", 1)[1].strip()
                elif line.startswith("DB_NAME="):
                    os.environ["DB_NAME"] = line.split("=", 1)[1].strip()
    except Exception:
        pass
    c = MongoClient(os.environ["MONGO_URL"])[os.environ.get("DB_NAME", "pick_vision")]
    from services.features.opponent_context import build_opponent_context_store
    store = build_opponent_context_store(c, seasons=[2023, 2024])
    summary = store.summary()
    assert summary["teams_indexed"] >= 25
    assert summary["team_games_rows"] >= 1000
    assert summary["league_avg_allowed"]["pts"] > 90

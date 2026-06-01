"""Unit tests for reshape_team_props_to_replay + optimizer prop_type wiring."""
from __future__ import annotations
import sys
sys.path.insert(0, "/app/backend")

import pytest

from scripts.sgo.reshape_team_props_to_replay import (
    project_hit_rates_from_team_features,
    assemble_replay_row,
    upsert_filter,
    PIPELINE_VERSION,
    SSOT_SOURCE,
)
# Optimizer helper (now exposed at module level)
from routes.emergent_admin.optimizer import _prop_type_clause


# ───── project_hit_rates_from_team_features ─────
class TestProjectHitRates:
    _tf = {
        "win_rate_l5":           0.6,
        "win_rate_l10":          0.55,
        "win_rate_season":       0.52,
        "spread_cover_rate_l10": 0.48,
        "ou_hit_rate_l10":       0.50,
        "cv_points_scored":      0.42,
    }

    def test_h2h_uses_win_rates(self):
        r = project_hit_rates_from_team_features("h2h", self._tf)
        assert r == {"hit_rate_l5": 0.6, "hit_rate_l10": 0.55,
                       "hit_rate_l20": 0.52}

    def test_spread_uses_only_l10_cover_rate(self):
        r = project_hit_rates_from_team_features("spread", self._tf)
        assert r == {"hit_rate_l5": None, "hit_rate_l10": 0.48,
                       "hit_rate_l20": None}

    def test_game_total_uses_only_l10_ou(self):
        r = project_hit_rates_from_team_features("game_total", self._tf)
        assert r == {"hit_rate_l5": None, "hit_rate_l10": 0.50,
                       "hit_rate_l20": None}

    def test_team_total_returns_all_none(self):
        r = project_hit_rates_from_team_features("team_total", self._tf)
        assert r == {"hit_rate_l5": None, "hit_rate_l10": None,
                       "hit_rate_l20": None}

    def test_unknown_category_returns_all_none(self):
        r = project_hit_rates_from_team_features("futures", self._tf)
        assert r == {"hit_rate_l5": None, "hit_rate_l10": None,
                       "hit_rate_l20": None}

    def test_null_features(self):
        r = project_hit_rates_from_team_features("h2h", None)
        assert r == {"hit_rate_l5": None, "hit_rate_l10": None,
                       "hit_rate_l20": None}


# ───── assemble_replay_row ─────
def _mk_prop(**overrides):
    base = {
        "sport": "mlb", "event_id": "E1",
        "team_id": "mlb_bos", "opponent_team_id": "mlb_nyy",
        "game_date": "2024-07-05", "commence_time": "2024-07-05T17:00:00Z",
        "home_away": "home",
        "market_key": "spread-home-game-bos", "market_category": "spread",
        "market_name": "Run Line",
        "side": "HOME", "sideID": "home",
        "line": -1.5, "book": "pinnacle", "odds": -110,
        "is_alternate": False, "periodID": "game",
        "statID": "points",
        "outcome": "WIN", "hit": True, "outcome_numeric": 1,
        "push": False, "outcome_resolved": True,
        "team_features": {
            "win_rate_l5": 0.6, "win_rate_l10": 0.55,
            "win_rate_season": 0.52, "spread_cover_rate_l10": 0.48,
            "ou_hit_rate_l10": 0.50, "cv_points_scored": 0.42,
        },
        "opponent_features": {
            "win_rate_l5": 0.4, "win_rate_l10": 0.45,
            "win_rate_season": 0.48, "cv_points_scored": 0.39,
        },
    }
    base.update(overrides)
    return base


class TestAssembleReplayRow:
    def test_canonical_player_schema_fields_populated(self):
        row = assemble_replay_row(_mk_prop())
        # Identity / routing
        assert row["league_id"] == "MLB"
        assert row["sport"] == "mlb"
        assert row["prop_type"] == "team"
        assert row["team_id"] == "mlb_bos"
        assert row["opponent_team_id"] == "mlb_nyy"
        assert row["player_name"] is None
        assert row["player_name_normalized"] is None
        # Bet
        assert row["market"] == "spread-home-game-bos"
        assert row["stat_family"] == "spread"
        assert row["side"] == "HOME"
        assert row["line"] == -1.5
        assert row["book"] == "pinnacle"
        assert row["odds"] == -110
        assert row["odds_bucket"] == "odds_-200_-100"
        # Priors mapped from spread market
        assert row["hit_rate_l5"] is None
        assert row["hit_rate_l10"] == 0.48
        assert row["hit_rate_l20"] is None
        assert row["cv"] == 0.42
        # Scoring fields not computed (Phase 4)
        assert row["tp"] is None
        assert row["edge"] is None
        assert row["vision_score"] is None
        # Tier flags inert
        assert row["safe_haven_pass"] is False
        assert row["front_lines_pass"] is False
        assert row["war_zone_pass"] is False
        assert row["selected_tier"] is None
        # Outcome
        assert row["outcome_resolved"] is True
        assert row["outcome_numeric"] == 1
        assert row["hit"] is True
        # Provenance
        assert row["pipeline_version"] == PIPELINE_VERSION
        assert row["ssot_source"] == SSOT_SOURCE
        assert row["scored_at"].tzinfo is not None

    def test_odds_bucket_via_shared_helper(self):
        for odds, expected in [
            (-300, "odds_lt_-200"),
            (-150, "odds_-200_-100"),
            (-50,  "odds_-100_-0"),
            (100,  "odds_+0_+150"),
            (200,  "odds_+150_+300"),
            (500,  "odds_+300p"),
            (None, "odds_na"),
        ]:
            row = assemble_replay_row(_mk_prop(odds=odds))
            assert row["odds_bucket"] == expected, f"{odds} → {expected}"

    def test_h2h_mapping(self):
        row = assemble_replay_row(_mk_prop(market_category="h2h", line=None))
        assert row["hit_rate_l5"] == 0.6
        assert row["hit_rate_l10"] == 0.55
        assert row["hit_rate_l20"] == 0.52

    def test_game_total_mapping(self):
        row = assemble_replay_row(_mk_prop(market_category="game_total"))
        assert row["hit_rate_l5"] is None
        assert row["hit_rate_l10"] == 0.50
        assert row["hit_rate_l20"] is None

    def test_null_team_features_yields_null_priors(self):
        row = assemble_replay_row(_mk_prop(team_features=None))
        assert row["cv"] is None
        assert row["hit_rate_l5"] is None
        assert row["hit_rate_l10"] is None
        assert row["hit_rate_l20"] is None
        # But the row still has identity + outcome
        assert row["event_id"] == "E1"
        assert row["outcome_numeric"] == 1


# ───── upsert_filter ─────
class TestUpsertFilter:
    def test_composite_key_uses_team_id_not_player_name(self):
        row = assemble_replay_row(_mk_prop())
        f = upsert_filter(row)
        assert "team_id" in f
        assert "player_name_normalized" not in f
        assert f == {
            "prop_type": "team",
            "event_id": "E1", "team_id": "mlb_bos",
            "market": "spread-home-game-bos",
            "line": -1.5, "side": "HOME", "book": "pinnacle",
            "pipeline_version": PIPELINE_VERSION,
        }


# ───── optimizer's prop_type clause ─────
class TestPropTypeClause:
    def test_player_default_excludes_team_via_ne(self):
        assert _prop_type_clause("player") == {"prop_type": {"$ne": "team"}}

    def test_team_filters_to_team_only(self):
        assert _prop_type_clause("team") == {"prop_type": "team"}

    def test_all_returns_empty_clause(self):
        assert _prop_type_clause("all") == {}

    def test_unknown_value_defaults_to_player(self):
        # Defensive — any unexpected string should default to player
        assert _prop_type_clause("invalid") == {"prop_type": {"$ne": "team"}}
        assert _prop_type_clause("") == {"prop_type": {"$ne": "team"}}

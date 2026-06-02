"""Unit tests for the league-aware namespace helpers in
`scripts/sgo/historical_full_pipeline_replay`.

Verifies the 2026-06-02 fix that removed the MLB hardcoding of:
  * runner runs/outputs collection names
  * sport tag on legacy mirror rows
"""
from __future__ import annotations
import pytest

import scripts.sgo.historical_full_pipeline_replay as h


class TestLeagueToSport:
    @pytest.mark.parametrize("league,sport", [
        ("MLB",   "mlb"),
        ("NBA",   "nba"),
        ("NFL",   "nfl"),
        ("NCAAF", "ncaaf"),
    ])
    def test_known_leagues_map_to_canonical_sport(self, league, sport):
        assert h.LEAGUE_TO_SPORT[league] == sport


class TestRunnerNamespace:
    def test_mlb_runs(self):
        assert h._runner_runs_coll("MLB") == \
            "mlb_propvision_full_pipeline_runs"

    def test_mlb_outputs(self):
        assert h._runner_outputs_coll("MLB") == \
            "mlb_propvision_full_pipeline_outputs"

    def test_nba_runs(self):
        assert h._runner_runs_coll("NBA") == \
            "nba_propvision_full_pipeline_runs"

    def test_nba_outputs(self):
        assert h._runner_outputs_coll("NBA") == \
            "nba_propvision_full_pipeline_outputs"

    def test_nfl_outputs(self):
        assert h._runner_outputs_coll("NFL") == \
            "nfl_propvision_full_pipeline_outputs"

    def test_case_insensitive(self):
        assert h._runner_outputs_coll("nba") == \
            "nba_propvision_full_pipeline_outputs"

    def test_unknown_league_raises(self):
        with pytest.raises(ValueError):
            h._runner_runs_coll("MLS")


class TestNamespaceShape:
    """Pin the {sport}_{namespace}_{kind} contract so future renames
    surface immediately."""

    def test_kind_placement(self):
        for league in ("MLB", "NBA"):
            for kind in ("runs", "outputs", "cards"):
                name = h._runner_ns(league, kind=kind)
                sport = h.LEAGUE_TO_SPORT[league]
                assert name.startswith(f"{sport}_")
                assert name.endswith(f"_{kind}")
                assert h.OUTPUT_NAMESPACE in name

    def test_kinds_are_distinct(self):
        runs = h._runner_ns("NBA", kind="runs")
        outs = h._runner_ns("NBA", kind="outputs")
        cards = h._runner_ns("NBA", kind="cards")
        assert len({runs, outs, cards}) == 3

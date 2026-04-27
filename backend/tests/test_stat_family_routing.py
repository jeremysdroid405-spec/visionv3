"""
Regression tests for shared stat-family normalization.
======================================================
Locks the cached_board ↔ scoring routing contract so:
  • Same player+stat+different lines stay separate
  • OVER vs UNDER stay separate
  • HRR ≠ Hits, Pitcher Strikeouts ≠ Batter Strikeouts
  • Combo stats ≠ base stats
  • Alt Total Bases lines stay separate
  • The normalizer is idempotent
"""
from __future__ import annotations
import sys
import pytest

sys.path.insert(0, "/app/backend")

from services.scoring.stat_family import (
    canonical_stat_family,
    build_canonical_key,
    is_pitcher_stat,
    is_batter_stat,
    is_combo_stat,
)


# ---------- NBA ----------
class TestNBANormalization:
    def test_alt_market_collapses_to_base(self):
        assert canonical_stat_family("player_points_alternate", "nba") == "PTS"
        assert canonical_stat_family("PLAYER_POINTS_ALTERNATE", "nba") == "PTS"

    def test_combo_market_collapses_to_combo_token(self):
        assert canonical_stat_family("player_points_assists", "nba") == "P+A"
        assert canonical_stat_family("PLAYER_POINTS_ASSISTS_ALTERNATE", "nba") == "P+A"
        assert canonical_stat_family("PLAYER_POINTS_REBOUNDS_ASSISTS", "nba") == "PRA"
        assert canonical_stat_family("player_points_rebounds_assists_alternate", "nba") == "PRA"
        assert canonical_stat_family("player_blocks_steals", "nba") == "BLK+STL"

    def test_combo_does_not_collapse_to_base(self):
        # P+R must NOT equal PTS
        assert canonical_stat_family("player_points_rebounds", "nba") != \
               canonical_stat_family("player_points", "nba")
        # PRA must NOT equal P+R
        assert canonical_stat_family("player_points_rebounds_assists", "nba") != \
               canonical_stat_family("player_points_rebounds", "nba")

    def test_idempotent(self):
        for token in ("PTS", "REB", "AST", "PRA", "P+R", "BLK+STL"):
            assert canonical_stat_family(token, "nba") == token
            assert canonical_stat_family(canonical_stat_family(token, "nba"),
                                         "nba") == token

    def test_q1_markets_kept_separate(self):
        # Q1 / first-quarter PTS must NOT collapse to PTS
        assert canonical_stat_family("player_points_q1", "nba") == "PTS_Q1"
        assert canonical_stat_family("player_points_q1", "nba") != "PTS"


# ---------- MLB ----------
class TestMLBNormalization:
    def test_pitcher_vs_batter_strikeouts_distinct(self):
        ps = canonical_stat_family("Pitcher Strikeouts", "mlb")
        bs = canonical_stat_family("Batter Strikeouts", "mlb")
        assert ps == "Pitcher Strikeouts"
        assert bs == "Batter Strikeouts"
        assert ps != bs

    def test_pitcher_strikeout_aliases(self):
        for alias in ("pitcher_strikeouts", "PITCHER_STRIKEOUTS",
                      "Pitcher Strikeouts"):
            assert canonical_stat_family(alias, "mlb") == "Pitcher Strikeouts"

    def test_batter_strikeout_aliases(self):
        for alias in ("batter_strikeouts", "BATTER_STRIKEOUTS",
                      "Batter Strikeouts"):
            assert canonical_stat_family(alias, "mlb") == "Batter Strikeouts"

    def test_hits_runs_rbis_collapses_to_one_token(self):
        assert canonical_stat_family("HRR", "mlb") == "Hits+Runs+RBIs"
        assert canonical_stat_family("Hits+Runs+RBIs", "mlb") == "Hits+Runs+RBIs"
        assert canonical_stat_family("hits_runs_rbis", "mlb") == "Hits+Runs+RBIs"
        assert canonical_stat_family("HITS RUNS RBIS", "mlb") == "Hits+Runs+RBIs"

    def test_hrr_does_not_collapse_to_hits(self):
        hits = canonical_stat_family("Hits", "mlb")
        hrr = canonical_stat_family("Hits+Runs+RBIs", "mlb")
        assert hits == "Hits"
        assert hrr == "Hits+Runs+RBIs"
        assert hits != hrr

    def test_total_bases_distinct_from_combo(self):
        tb = canonical_stat_family("Total Bases", "mlb")
        tb_combo = canonical_stat_family("Total Bases+Runs+RBIs", "mlb")
        assert tb != tb_combo

    def test_pitcher_vs_batter_helpers(self):
        assert is_pitcher_stat("Pitcher Strikeouts") is True
        assert is_pitcher_stat("Hits Allowed") is True
        assert is_batter_stat("Batter Strikeouts") is True
        assert is_batter_stat("Hits") is True
        # No overlap
        assert is_pitcher_stat("Hits") is False
        assert is_batter_stat("Pitcher Strikeouts") is False


# ---------- Canonical key ----------
class TestCanonicalKey:
    def test_alt_lines_stay_separate(self):
        # Same player + stat + side, different lines → different keys
        k1 = build_canonical_key("nba", "evt1", "Joel Embiid", "PTS", 24.5, "OVER")
        k2 = build_canonical_key("nba", "evt1", "Joel Embiid", "PTS", 29.5, "OVER")
        k3 = build_canonical_key("nba", "evt1", "Joel Embiid", "PTS", 19.5, "OVER")
        assert k1 != k2 != k3
        assert len({k1, k2, k3}) == 3

    def test_over_under_stay_separate(self):
        ko = build_canonical_key("nba", "e1", "X", "PTS", 20.5, "OVER")
        ku = build_canonical_key("nba", "e1", "X", "PTS", 20.5, "UNDER")
        assert ko != ku

    def test_combo_vs_base_stay_separate(self):
        # PRA (combo) and PTS (base) for same player+line+side → different
        k_combo = build_canonical_key("nba", "e1", "X",
                                       "player_points_rebounds_assists",
                                       30.5, "OVER")
        k_base = build_canonical_key("nba", "e1", "X", "player_points",
                                      30.5, "OVER")
        assert k_combo != k_base

    def test_pitcher_batter_keys_distinct(self):
        kp = build_canonical_key("mlb", "e1", "Shohei Ohtani",
                                 "Pitcher Strikeouts", 7.5, "OVER")
        kb = build_canonical_key("mlb", "e1", "Shohei Ohtani",
                                 "Batter Strikeouts", 7.5, "OVER")
        assert kp != kb

    def test_hrr_vs_hits_keys_distinct(self):
        kh = build_canonical_key("mlb", "e1", "Aaron Judge",
                                 "Hits", 1.5, "OVER")
        kr = build_canonical_key("mlb", "e1", "Aaron Judge",
                                 "Hits+Runs+RBIs", 1.5, "OVER")
        assert kh != kr

    def test_alt_total_bases_lines_separate(self):
        ks = [build_canonical_key("mlb", "e1", "X", "Total Bases", L, "OVER")
              for L in (0.5, 1.5, 2.5, 3.5)]
        assert len(set(ks)) == 4

    def test_canonical_normalizes_inputs(self):
        # Raw market key produces same canonical key as compact token
        k_raw = build_canonical_key("nba", "e1", "X",
                                     "player_points_rebounds_alternate",
                                     30.5, "OVER")
        k_token = build_canonical_key("nba", "e1", "X", "P+R", 30.5, "OVER")
        assert k_raw == k_token


# ---------- Combo helper ----------
class TestComboHelper:
    def test_combo_helper_marks_combos(self):
        assert is_combo_stat("PRA") is True
        assert is_combo_stat("P+R") is True
        assert is_combo_stat("Hits+Runs+RBIs") is True
        assert is_combo_stat("BLK+STL") is True

    def test_combo_helper_rejects_base(self):
        assert is_combo_stat("PTS") is False
        assert is_combo_stat("Hits") is False
        assert is_combo_stat("Pitcher Strikeouts") is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

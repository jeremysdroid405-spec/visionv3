"""Unit tests for MLB empirical-Bayes post-shrinkage helper."""
from __future__ import annotations

import os
import pytest

from services.scoring import mlb_eb_shrinkage as ebs


# ----- Fake DB + hub row ------------------------------------------------

class _FakeColl:
    def __init__(self, rows):
        self._rows = rows

    def find_one(self, q, proj=None):
        # Minimal $or handler — return first matching row.
        ors = q.get("$or") or [q]
        for row in self._rows:
            for clause in ors:
                ok = True
                for k, v in clause.items():
                    if row.get(k) != v:
                        ok = False
                        break
                if ok:
                    return row
        return None


class _FakeDB:
    def __init__(self, rows):
        self._c = _FakeColl(rows)

    def __getitem__(self, name):
        return self._c


def _logs(counts, stat_key="home_runs", include_non_batter=0):
    """Build `bdl_game_logs` with batter-AB games having the given
    per-game counts for `stat_key`. `include_non_batter` pitchers
    without at_bats are appended to test the filter."""
    out = []
    for v in counts:
        out.append({
            "at_bats": 4, "plate_appearances": 4,
            "hits": v if stat_key == "hits" else 1,
            "runs": v if stat_key == "runs" else 0,
            "rbis": v if stat_key == "rbis" else 0,
            "home_runs": v if stat_key == "home_runs" else 0,
            "total_bases": v if stat_key == "total_bases" else 0,
        })
    for _ in range(include_non_batter):
        out.append({"at_bats": 0, "plate_appearances": 0,
                    "hits": 0, "runs": 0, "rbis": 0})
    return out


# ----- Tests ------------------------------------------------------------

def setup_function(_):
    ebs.reset_cache()
    os.environ.pop("MLB_HF_EB_SHRINKAGE_ENABLED", None)


def test_flag_off_by_default_skips():
    db = _FakeDB([{
        "bdl_player_id": 1, "bdl_game_logs": _logs([0] * 25, "home_runs"),
    }])
    shrunk, audit = ebs.apply_eb_shrinkage(db, 1, "home_runs", 0.8)
    assert shrunk is None
    assert audit["eb_shrinkage_applied"] is False
    assert audit["eb_skip_reason"] == "flag_off"
    assert audit["raw_hf_projection"] == 0.8


def test_flag_on_applies_home_runs_shrinkage():
    os.environ["MLB_HF_EB_SHRINKAGE_ENABLED"] = "true"
    logs = _logs([0] * 25, "home_runs")  # 25 games, mean=0
    db = _FakeDB([{"bdl_player_id": 42, "bdl_game_logs": logs}])
    shrunk, audit = ebs.apply_eb_shrinkage(db, 42, "home_runs", 1.0)
    # w_model=0.3, w_player=0.7, career_mean=0 → 0.3*1.0 + 0.7*0 = 0.3
    assert shrunk == pytest.approx(0.3, abs=1e-6)
    assert audit["eb_shrinkage_applied"] is True
    assert audit["eb_player_career_mean"] == 0.0
    assert audit["eb_weight_model"] == 0.3
    assert audit["eb_weight_player"] == 0.7
    assert audit["eb_career_sample_n"] == 25


def test_flag_on_rbis_different_weights():
    os.environ["MLB_HF_EB_SHRINKAGE_ENABLED"] = "true"
    logs = _logs([1] * 30, "rbis")  # career mean = 1.0
    db = _FakeDB([{"bdl_id": 99, "bdl_game_logs": logs}])
    shrunk, audit = ebs.apply_eb_shrinkage(db, 99, "rbis", 2.0)
    # w_model=0.4 -> 0.4*2.0 + 0.6*1.0 = 1.4
    assert shrunk == pytest.approx(1.4, abs=1e-4)
    assert audit["eb_weight_model"] == 0.4
    assert audit["eb_weight_player"] == 0.6


def test_hits_runs_rbis_composite_stat():
    os.environ["MLB_HF_EB_SHRINKAGE_ENABLED"] = "true"
    # Each game has hits=1, runs=0, rbis=0 → H+R+RBI=1 per game
    logs = []
    for _ in range(25):
        logs.append({"at_bats": 4, "plate_appearances": 4,
                     "hits": 1, "runs": 0, "rbis": 0})
    db = _FakeDB([{"bdl_player_id": 7, "bdl_game_logs": logs}])
    shrunk, audit = ebs.apply_eb_shrinkage(db, 7, "hits+runs+rbis", 3.0)
    # w_model=0.6, w_player=0.4. career_mean=1. 0.6*3 + 0.4*1 = 2.2
    assert shrunk == pytest.approx(2.2, abs=1e-4)
    assert audit["eb_shrinkage_applied"] is True


def test_stat_outside_whitelist_skipped():
    os.environ["MLB_HF_EB_SHRINKAGE_ENABLED"] = "true"
    logs = _logs([1] * 25, "hits")
    db = _FakeDB([{"bdl_player_id": 1, "bdl_game_logs": logs}])
    shrunk, audit = ebs.apply_eb_shrinkage(db, 1, "hits", 1.5)
    assert shrunk is None
    assert audit["eb_skip_reason"] == "stat_not_whitelisted"
    assert audit["eb_shrinkage_applied"] is False


def test_missing_bdl_id_skipped():
    os.environ["MLB_HF_EB_SHRINKAGE_ENABLED"] = "true"
    db = _FakeDB([])
    shrunk, audit = ebs.apply_eb_shrinkage(db, None, "home_runs", 0.5)
    assert shrunk is None
    assert audit["eb_skip_reason"] == "missing_bdl_id"


def test_insufficient_games_skipped():
    os.environ["MLB_HF_EB_SHRINKAGE_ENABLED"] = "true"
    logs = _logs([1] * 5, "home_runs")  # 5 < MIN_CAREER_GAMES=20
    db = _FakeDB([{"bdl_player_id": 1, "bdl_game_logs": logs}])
    shrunk, audit = ebs.apply_eb_shrinkage(db, 1, "home_runs", 0.5)
    assert shrunk is None
    assert audit["eb_skip_reason"].startswith("insufficient_games_5")
    assert audit["eb_career_sample_n"] == 5


def test_non_batter_games_filtered_out():
    os.environ["MLB_HF_EB_SHRINKAGE_ENABLED"] = "true"
    # Only 15 batter games, even though 25 total logs.
    logs = _logs([1] * 15, "home_runs", include_non_batter=10)
    db = _FakeDB([{"bdl_player_id": 1, "bdl_game_logs": logs}])
    shrunk, audit = ebs.apply_eb_shrinkage(db, 1, "home_runs", 0.5)
    assert shrunk is None
    assert audit["eb_career_sample_n"] == 15
    assert "insufficient_games" in audit["eb_skip_reason"]


def test_no_raw_projection_skipped():
    os.environ["MLB_HF_EB_SHRINKAGE_ENABLED"] = "true"
    db = _FakeDB([])
    shrunk, audit = ebs.apply_eb_shrinkage(db, 1, "home_runs", None)
    assert shrunk is None
    assert audit["eb_skip_reason"] == "no_raw_projection"
    assert audit["raw_hf_projection"] is None


def test_negative_shrinkage_floored_to_zero():
    os.environ["MLB_HF_EB_SHRINKAGE_ENABLED"] = "true"
    logs = _logs([0] * 25, "home_runs")
    db = _FakeDB([{"bdl_player_id": 1, "bdl_game_logs": logs}])
    # Raw proj = -0.5 should floor to 0.0 (0.3*-0.5 + 0.7*0 = -0.15 → 0.0)
    shrunk, audit = ebs.apply_eb_shrinkage(db, 1, "home_runs", -0.5)
    assert shrunk == 0.0
    assert audit["eb_shrinkage_applied"] is True


def test_player_lookup_cache_hit():
    os.environ["MLB_HF_EB_SHRINKAGE_ENABLED"] = "true"
    hits = {"n": 0}

    class CountingColl(_FakeColl):
        def find_one(self, q, proj=None):
            hits["n"] += 1
            return super().find_one(q, proj)

    class DB:
        def __init__(self):
            self._c = CountingColl([{
                "bdl_player_id": 1,
                "bdl_game_logs": _logs([1] * 25, "rbis"),
            }])
        def __getitem__(self, _): return self._c

    db = DB()
    ebs.apply_eb_shrinkage(db, 1, "rbis", 2.0)
    ebs.apply_eb_shrinkage(db, 1, "rbis", 1.5)
    # Both calls use same player → only 1 Mongo hit.
    assert hits["n"] == 1


def test_weights_for_each_whitelisted_stat():
    # Regression: the four weights stay at spec until we explicitly tune.
    assert ebs.weights_for("home_runs") == (0.30, 0.70)
    assert ebs.weights_for("rbis") == (0.40, 0.60)
    assert ebs.weights_for("total_bases") == (0.50, 0.50)
    assert ebs.weights_for("hits+runs+rbis") == (0.60, 0.40)


def test_normalize_stat_aliases():
    assert ebs._normalize_stat("HR") == "home_runs"
    assert ebs._normalize_stat("RBI") == "rbis"
    assert ebs._normalize_stat("TB") == "total_bases"
    assert ebs._normalize_stat("hits+runs+rbi") == "hits+runs+rbis"
    assert ebs._normalize_stat("HRR") == "hits+runs+rbis"
    # Unknown passes through.
    assert ebs._normalize_stat("strikeouts") == "strikeouts"


def test_stat_supported_predicate():
    assert ebs.stat_supported("home_runs") is True
    assert ebs.stat_supported("HR") is True
    assert ebs.stat_supported("hits") is False
    assert ebs.stat_supported("pitcher_strikeouts") is False

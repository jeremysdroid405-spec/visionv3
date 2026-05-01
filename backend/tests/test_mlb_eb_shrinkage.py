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
    """Back-compat shim so existing tests that pass a 'db'-like object
    keep working. In production code the helper takes a `master_hub`
    pymongo collection directly."""
    def __init__(self, rows):
        self._c = _FakeColl(rows)

    def __getitem__(self, name):
        return self._c


def _fake_hub(rows):
    """Return a fake sync pymongo-like collection with the given rows."""
    return _FakeColl(rows)


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
    hub = _fake_hub([{
        "bdl_player_id": 1, "bdl_game_logs": _logs([0] * 25, "home_runs"),
    }])
    shrunk, audit = ebs.apply_eb_shrinkage(hub, 1, "home_runs", 0.8)
    assert shrunk is None
    assert audit["eb_shrinkage_applied"] is False
    assert audit["eb_skip_reason"] == "flag_off"
    assert audit["raw_hf_projection"] == 0.8


def test_flag_on_applies_home_runs_shrinkage():
    os.environ["MLB_HF_EB_SHRINKAGE_ENABLED"] = "true"
    logs = _logs([0] * 25, "home_runs")  # 25 games, mean=0
    hub = _fake_hub([{"bdl_player_id": 42, "bdl_game_logs": logs}])
    shrunk, audit = ebs.apply_eb_shrinkage(hub, 42, "home_runs", 1.0)
    # w_model=0.3, w_player=0.7, career_mean=0 → 0.3*1.0 + 0.7*0 = 0.3
    assert shrunk == pytest.approx(0.3, abs=1e-6)
    assert audit["eb_shrinkage_applied"] is True
    assert audit["eb_player_career_mean"] == 0.0
    assert audit["eb_weight_model"] == 0.3
    assert audit["eb_weight_player"] == 0.7
    assert audit["eb_career_sample_n"] == 20  # capped at CAP_AT_GAMES


def test_flag_on_rbis_different_weights():
    os.environ["MLB_HF_EB_SHRINKAGE_ENABLED"] = "true"
    logs = _logs([1] * 30, "rbis")  # career mean = 1.0
    hub = _fake_hub([{"bdl_id": 99, "bdl_game_logs": logs}])
    shrunk, audit = ebs.apply_eb_shrinkage(hub, 99, "rbis", 2.0)
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
    hub = _fake_hub([{"bdl_player_id": 7, "bdl_game_logs": logs}])
    shrunk, audit = ebs.apply_eb_shrinkage(hub, 7, "hits+runs+rbis", 3.0)
    # w_model=0.6, w_player=0.4. career_mean=1. 0.6*3 + 0.4*1 = 2.2
    assert shrunk == pytest.approx(2.2, abs=1e-4)
    assert audit["eb_shrinkage_applied"] is True


def test_stat_outside_whitelist_skipped():
    os.environ["MLB_HF_EB_SHRINKAGE_ENABLED"] = "true"
    logs = _logs([1] * 25, "hits")
    hub = _fake_hub([{"bdl_player_id": 1, "bdl_game_logs": logs}])
    shrunk, audit = ebs.apply_eb_shrinkage(hub, 1, "hits", 1.5)
    assert shrunk is None
    assert audit["eb_skip_reason"] == "stat_not_whitelisted"
    assert audit["eb_shrinkage_applied"] is False


def test_missing_bdl_id_skipped():
    os.environ["MLB_HF_EB_SHRINKAGE_ENABLED"] = "true"
    hub = _fake_hub([])
    shrunk, audit = ebs.apply_eb_shrinkage(hub, None, "home_runs", 0.5)
    assert shrunk is None
    assert audit["eb_skip_reason"] == "missing_bdl_id"


def test_insufficient_games_skipped():
    """Below MIN_GAMES_FOR_SHRINK (3) we still skip — n=1/n=2 is too
    noisy to derive a reliable career-mean prior."""
    os.environ["MLB_HF_EB_SHRINKAGE_ENABLED"] = "true"
    logs = _logs([1] * 2, "home_runs")  # 2 < MIN_GAMES_FOR_SHRINK=3
    hub = _fake_hub([{"bdl_player_id": 1, "bdl_game_logs": logs}])
    shrunk, audit = ebs.apply_eb_shrinkage(hub, 1, "home_runs", 0.5)
    assert shrunk is None
    assert audit["eb_skip_reason"].startswith("insufficient_games_2")
    assert audit["eb_career_sample_n"] == 2


def test_non_batter_games_filtered_out():
    """Non-batter games (no AB / no PA) MUST be excluded from the
    career-mean count — even if 25 total logs exist, we count only
    the batter games."""
    os.environ["MLB_HF_EB_SHRINKAGE_ENABLED"] = "true"
    # 1 batter game, 10 non-batter — 1 < MIN_GAMES_FOR_SHRINK so skip.
    logs = _logs([1] * 1, "home_runs", include_non_batter=10)
    hub = _fake_hub([{"bdl_player_id": 1, "bdl_game_logs": logs}])
    shrunk, audit = ebs.apply_eb_shrinkage(hub, 1, "home_runs", 0.5)
    assert shrunk is None
    assert audit["eb_career_sample_n"] == 1
    assert "insufficient_games" in audit["eb_skip_reason"]


# ─── Sample-size ramp (2026-04-30) ──────────────────────────────────
# When n is between MIN_GAMES_FOR_SHRINK (3) and CAP_AT_GAMES (20),
# the player-side weight scales linearly with sample size. At/above
# CAP_AT_GAMES, behaviour matches the pre-2026-04-30 static weights.

def test_ramp_at_n18_partial_player_weight():
    """Bleday case: 18 games, HRRBI. Static weights = (model 0.6,
    player 0.4). Ramp = 18/20 = 0.9. So effective weights:
        w_player = 0.4 * 0.9 = 0.36
        w_model  = 1.0 - 0.36 = 0.64
    Pre-2026-04-30 this case was SKIPPED (n<20) — locking in that the
    safety-net engages at n=18 was the entire point of the change.
    """
    os.environ["MLB_HF_EB_SHRINKAGE_ENABLED"] = "true"
    # 18 games, each with 1 hit + 0 runs + 0 rbis → career mean = 1.0
    logs = []
    for _ in range(18):
        logs.append({"at_bats": 4, "plate_appearances": 4,
                     "hits": 1, "runs": 0, "rbis": 0})
    hub = _fake_hub([{"bdl_player_id": 99, "bdl_game_logs": logs}])
    shrunk, audit = ebs.apply_eb_shrinkage(hub, 99, "hits+runs+rbis", 6.55)
    assert audit["eb_shrinkage_applied"] is True, (
        f"At n=18 EB MUST engage (was the 2026-04-30 fix). "
        f"audit={audit}"
    )
    assert audit["eb_career_sample_n"] == 18
    assert audit["eb_weight_ramp"] == pytest.approx(0.9, abs=1e-6)
    assert audit["eb_weight_player"] == pytest.approx(0.36, abs=1e-4)
    assert audit["eb_weight_model"] == pytest.approx(0.64, abs=1e-4)
    # Shrunk = 0.64 * 6.55 + 0.36 * 1.0 = 4.192 + 0.36 = 4.552
    assert shrunk == pytest.approx(4.552, abs=1e-3)


def test_ramp_saturates_at_n20_matches_static_weights():
    """At n>=CAP_AT_GAMES, ramp=1.0 so effective weights match the
    static `_WEIGHTS` table exactly. This is the backwards-compat
    guarantee: no behaviour change for players already at the cap.

    Note: `eb_career_sample_n` reflects the ROLLING window size, so
    even though we feed 30 logs, the audit reports n=20 (CAP_AT_GAMES).
    """
    os.environ["MLB_HF_EB_SHRINKAGE_ENABLED"] = "true"
    logs = _logs([1] * 30, "rbis")  # all 30 games yield mean=1.0
    hub = _fake_hub([{"bdl_player_id": 5, "bdl_game_logs": logs}])
    shrunk, audit = ebs.apply_eb_shrinkage(hub, 5, "rbis", 2.0)
    # rbis static: w_model=0.4, w_player=0.6 → 0.4*2.0 + 0.6*1.0 = 1.4
    assert audit["eb_career_sample_n"] == ebs.CAP_AT_GAMES, (
        f"Sample n MUST cap at CAP_AT_GAMES={ebs.CAP_AT_GAMES} "
        f"(rolling window). Got {audit['eb_career_sample_n']}."
    )
    assert audit["eb_weight_ramp"] == pytest.approx(1.0, abs=1e-6)
    assert audit["eb_weight_model"] == pytest.approx(0.4, abs=1e-4)
    assert audit["eb_weight_player"] == pytest.approx(0.6, abs=1e-4)
    assert shrunk == pytest.approx(1.4, abs=1e-4)


# ─── Rolling-20 window (2026-04-30 + user note) ─────────────────────
# CAP_AT_GAMES is BOTH the ramp saturation point AND the size of the
# rolling window used to compute the prior. As new games come in, the
# oldest game falls out of the window — the prior reflects CURRENT
# form, not a snapshot frozen at the first 20 games of the season.

def test_rolling_window_uses_most_recent_games():
    """A player with 30 games: 10 OLD games at value=5, 20 RECENT
    games at value=1. The rolling window must use the 20 RECENT games
    (mean = 1), NOT the full 30 games (mean would be ~2.33), and NOT
    the FIRST 20 (which would mix in old data).
    """
    os.environ["MLB_HF_EB_SHRINKAGE_ENABLED"] = "true"
    # Build logs in DESC date order (newest first), matching the
    # production collection's natural ordering. 20 recent at v=1,
    # 10 old at v=5.
    logs = []
    # Recent games (today downward) — sorted DESC by date string.
    for i in range(20):
        logs.append({
            "at_bats": 4, "plate_appearances": 4,
            "rbis": 1, "hits": 0, "runs": 0,
            "home_runs": 0, "total_bases": 0,
            "date": f"2026-05-{i+1:02d}T20:00:00Z",
        })
    # Older games — should NOT enter the window.
    for i in range(10):
        logs.append({
            "at_bats": 4, "plate_appearances": 4,
            "rbis": 5, "hits": 0, "runs": 0,
            "home_runs": 0, "total_bases": 0,
            "date": f"2026-03-{i+1:02d}T20:00:00Z",
        })
    hub = _fake_hub([{"bdl_player_id": 11, "bdl_game_logs": logs}])
    shrunk, audit = ebs.apply_eb_shrinkage(hub, 11, "rbis", 2.0)
    # career_mean over last-20 window = 1.0 (NOT 2.33 if all 30 used).
    assert audit["eb_player_career_mean"] == pytest.approx(1.0, abs=1e-6), (
        f"Rolling window MUST use the last {ebs.CAP_AT_GAMES} games. "
        f"Got mean={audit['eb_player_career_mean']} (expected 1.0). "
        "If this fails the prior is contaminated by stale games."
    )
    assert audit["eb_career_sample_n"] == ebs.CAP_AT_GAMES


def test_rolling_window_sorts_when_logs_in_asc_order():
    """Defensive: even if `bdl_game_logs` arrives in ASC order
    (oldest first) — e.g. a future ingest change — the helper must
    still extract the LATEST 20, not the FIRST 20.
    """
    os.environ["MLB_HF_EB_SHRINKAGE_ENABLED"] = "true"
    logs = []
    # ASC order: 10 OLD (v=5) first, then 20 RECENT (v=1).
    for i in range(10):
        logs.append({
            "at_bats": 4, "plate_appearances": 4,
            "rbis": 5, "hits": 0, "runs": 0,
            "home_runs": 0, "total_bases": 0,
            "date": f"2026-03-{i+1:02d}T20:00:00Z",
        })
    for i in range(20):
        logs.append({
            "at_bats": 4, "plate_appearances": 4,
            "rbis": 1, "hits": 0, "runs": 0,
            "home_runs": 0, "total_bases": 0,
            "date": f"2026-05-{i+1:02d}T20:00:00Z",
        })
    hub = _fake_hub([{"bdl_player_id": 12, "bdl_game_logs": logs}])
    _, audit = ebs.apply_eb_shrinkage(hub, 12, "rbis", 2.0)
    assert audit["eb_player_career_mean"] == pytest.approx(1.0, abs=1e-6), (
        "Window MUST sort by date DESC before slicing — without the "
        "explicit sort the first 20 games (old, v=5) would be used."
    )


def test_rolling_window_excludes_pitching_only_appearances():
    """Pitching-only appearances (no AB, no PA) MUST be excluded
    from the rolling window — even if they're chronologically among
    the last 20 events for the player.
    """
    os.environ["MLB_HF_EB_SHRINKAGE_ENABLED"] = "true"
    logs = []
    # 5 pitching-only appearances (recent) + 15 batter games (older).
    for i in range(5):
        logs.append({
            "at_bats": 0, "plate_appearances": 0,
            "innings_pitched": 1.0, "earned_runs": 0,
            "date": f"2026-05-{i+1:02d}T20:00:00Z",
        })
    for i in range(15):
        logs.append({
            "at_bats": 4, "plate_appearances": 4,
            "rbis": 2, "hits": 0, "runs": 0,
            "home_runs": 0, "total_bases": 0,
            "date": f"2026-04-{i+1:02d}T20:00:00Z",
        })
    hub = _fake_hub([{"bdl_player_id": 13, "bdl_game_logs": logs}])
    shrunk, audit = ebs.apply_eb_shrinkage(hub, 13, "rbis", 2.0)
    # Window = 15 batter games at v=2 → mean=2.0
    assert audit["eb_career_sample_n"] == 15
    assert audit["eb_player_career_mean"] == pytest.approx(2.0, abs=1e-6)


# ─── Sample-size ramp (2026-04-30) ──────────────────────────────────


def test_ramp_at_n5_low_player_weight():
    """Early-season case: only 5 games. Ramp = 5/20 = 0.25. For HR
    (static w_player=0.7) the effective player weight is just 0.175.
    The career-mean barely affects the result — by design, since we
    don't trust 5-game samples to dominate the model."""
    os.environ["MLB_HF_EB_SHRINKAGE_ENABLED"] = "true"
    logs = _logs([0] * 5, "home_runs")  # mean=0
    hub = _fake_hub([{"bdl_player_id": 3, "bdl_game_logs": logs}])
    shrunk, audit = ebs.apply_eb_shrinkage(hub, 3, "home_runs", 1.0)
    assert audit["eb_shrinkage_applied"] is True
    assert audit["eb_career_sample_n"] == 5
    assert audit["eb_weight_ramp"] == pytest.approx(0.25, abs=1e-6)
    # w_player = 0.7 * 0.25 = 0.175, w_model = 0.825
    # Shrunk = 0.825*1.0 + 0.175*0.0 = 0.825
    assert shrunk == pytest.approx(0.825, abs=1e-4)


def test_ramp_at_n3_minimum_floor_engages():
    """At exactly n=MIN_GAMES_FOR_SHRINK=3, EB engages with a tiny
    ramp. Below this (n<=2) it skips — see
    `test_insufficient_games_skipped`."""
    os.environ["MLB_HF_EB_SHRINKAGE_ENABLED"] = "true"
    logs = _logs([1] * 3, "rbis")  # exactly 3 games → just at floor
    hub = _fake_hub([{"bdl_player_id": 4, "bdl_game_logs": logs}])
    shrunk, audit = ebs.apply_eb_shrinkage(hub, 4, "rbis", 2.0)
    assert audit["eb_shrinkage_applied"] is True
    assert audit["eb_career_sample_n"] == 3
    assert audit["eb_weight_ramp"] == pytest.approx(0.15, abs=1e-6)


def test_ramp_audit_field_persisted():
    """`eb_weight_ramp` MUST be in the audit dict whenever shrinkage
    applies — `recompute.py` and `prop_scores_store.py` rely on this
    key existing for downstream observability."""
    os.environ["MLB_HF_EB_SHRINKAGE_ENABLED"] = "true"
    logs = _logs([1] * 10, "home_runs")
    hub = _fake_hub([{"bdl_player_id": 1, "bdl_game_logs": logs}])
    _, audit = ebs.apply_eb_shrinkage(hub, 1, "home_runs", 0.5)
    assert "eb_weight_ramp" in audit
    assert isinstance(audit["eb_weight_ramp"], float)


def test_no_raw_projection_skipped():
    os.environ["MLB_HF_EB_SHRINKAGE_ENABLED"] = "true"
    hub = _fake_hub([])
    shrunk, audit = ebs.apply_eb_shrinkage(hub, 1, "home_runs", None)
    assert shrunk is None
    assert audit["eb_skip_reason"] == "no_raw_projection"
    assert audit["raw_hf_projection"] is None


def test_negative_shrinkage_floored_to_zero():
    os.environ["MLB_HF_EB_SHRINKAGE_ENABLED"] = "true"
    logs = _logs([0] * 25, "home_runs")
    hub = _fake_hub([{"bdl_player_id": 1, "bdl_game_logs": logs}])
    # Raw proj = -0.5 should floor to 0.0 (0.3*-0.5 + 0.7*0 = -0.15 → 0.0)
    shrunk, audit = ebs.apply_eb_shrinkage(hub, 1, "home_runs", -0.5)
    assert shrunk == 0.0
    assert audit["eb_shrinkage_applied"] is True


def test_player_lookup_cache_hit():
    os.environ["MLB_HF_EB_SHRINKAGE_ENABLED"] = "true"
    hits = {"n": 0}

    class CountingColl(_FakeColl):
        def find_one(self, q, proj=None):
            hits["n"] += 1
            return super().find_one(q, proj)

    hub = CountingColl([{
        "bdl_player_id": 1,
        "bdl_game_logs": _logs([1] * 25, "rbis"),
    }])
    ebs.apply_eb_shrinkage(hub, 1, "rbis", 2.0)
    ebs.apply_eb_shrinkage(hub, 1, "rbis", 1.5)
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

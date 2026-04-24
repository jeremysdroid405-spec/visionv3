"""Tests for services/features/distribution_profile (2026-04-24)."""
from __future__ import annotations

import pytest

from services.features import distribution_profile as dp


def _mk_logs(pts_values, game_id_start=1000):
    """Build minimal game-log dicts with just the stats we need. The
    module tolerates a descending OR ascending order; we emit ascending
    here."""
    return [
        {
            "game_id": game_id_start + i,
            "pts": v, "reb": 5, "ast": 3, "fg3m": 1,
        }
        for i, v in enumerate(pts_values)
    ]


def test_schema_count_is_123():
    assert len(dp.FEATURE_SCHEMA) == 123


def test_empty_history_emits_priors():
    out = dp.build([])
    assert len(out) == len(dp.FEATURE_SCHEMA)
    for k, v in out.items():
        if "hit_" in k:
            assert v == dp.SHRINKAGE_PRIOR
        else:
            assert v == 0.0


def test_zero_rate_matches_1_minus_hit_1():
    # 60% zeros, 40% positive (exactly 10)
    pts = [0]*6 + [10]*4
    logs = _mk_logs(pts)
    out = dp.build(logs)
    # career window — no shrinkage
    zero = out["pts_zero_rate_career"]
    hit1 = out["pts_hit_1_rate_career"]
    assert zero == pytest.approx(0.6, abs=1e-9)
    assert hit1 == pytest.approx(0.4, abs=1e-9)
    assert zero + hit1 == pytest.approx(1.0, abs=1e-9)


def test_hit_thresholds_monotone_non_increasing():
    """For any valid rate sequence, hit_N_rate must be non-increasing
    in N (a harder threshold cannot be hit more often)."""
    pts = [0, 3, 7, 12, 18, 22, 27, 33, 40, 8, 15, 26]
    logs = _mk_logs(pts)
    out = dp.build(logs)
    for window in ("L20", "L50", "career"):
        prev = 1.0
        for thr in sorted(dp.THRESHOLDS["pts"]):
            v = out[f"pts_hit_{thr}_rate_{window}"]
            assert v <= prev + 1e-9
            prev = v


def test_l20_uses_last_20_games():
    pts = list(range(40))     # ascending
    logs = _mk_logs(pts)
    out = dp.build(logs)
    # Last 20 values are 20..39. pts_hit_15_rate_L20 must be 1.0 (all >= 15)
    # even though career-wide pts_hit_15_rate_career should be lower.
    assert out["pts_hit_15_rate_L20"] > out["pts_hit_15_rate_career"]
    # All last-20 values >= 15 → career shrink-free, L20 shrink ON.
    # Raw rate = 20/20 = 1.0; shrunk rate = (20 + 1.5) / (20 + 3) ≈ 0.935
    assert out["pts_hit_15_rate_L20"] == pytest.approx(0.9348, abs=0.01)
    assert out["pts_hit_15_rate_career"] == pytest.approx(25/40, abs=1e-9)


def test_shrinkage_pulls_extreme_l20_toward_prior():
    # 20 zeros. Raw zero_rate = 1.0; shrunk = (20 + 1.5) / 23 ≈ 0.935
    pts = [0]*20
    logs = _mk_logs(pts)
    out = dp.build(logs)
    assert out["pts_zero_rate_L20"] == pytest.approx(0.9348, abs=0.01)
    # Career and L50 windows (raw, no shrinkage) show the full 1.0.
    assert out["pts_zero_rate_career"] == pytest.approx(1.0)


def test_threes_stat_key_reads_fg3m():
    """The `threes` family must read from fg3m (BDL field name)."""
    logs = [{"game_id": i, "pts": 0, "reb": 0, "ast": 0, "fg3m": v}
            for i, v in enumerate([0, 1, 0, 2, 0, 3, 0, 1, 0, 4])]
    out = dp.build(logs)
    # career: 5 zeros / 10 = 0.5; hit_1_rate = 0.5; hit_3_rate = 2/10 = 0.2
    assert out["threes_zero_rate_career"] == pytest.approx(0.5)
    assert out["threes_hit_1_rate_career"] == pytest.approx(0.5)
    assert out["threes_hit_3_rate_career"] == pytest.approx(0.2)


def test_pra_synthesised_from_components():
    logs = [{"game_id": i, "pts": 10, "reb": 5, "ast": 3, "fg3m": 1}
            for i in range(10)]
    out = dp.build(logs)
    # pra = 18 per game → hit_15 = 1.0, hit_20 = 0.0
    assert out["pra_hit_15_rate_career"] == pytest.approx(1.0)
    assert out["pra_hit_20_rate_career"] == pytest.approx(0.0)


def test_below_min_games_emits_priors_per_window():
    logs = _mk_logs([10, 15, 20])  # only 3 games — below MIN_WINDOW_GAMES
    out = dp.build(logs)
    # career window also < MIN_WINDOW_GAMES → priors
    for thr in dp.THRESHOLDS["pts"]:
        assert out[f"pts_hit_{thr}_rate_career"] == dp.SHRINKAGE_PRIOR
    assert out["pts_zero_rate_career"] == 0.0


def test_descending_order_is_auto_flipped():
    pts_asc = list(range(40))
    logs_asc = _mk_logs(pts_asc)
    logs_desc = list(reversed(logs_asc))
    out_asc = dp.build(logs_asc)
    out_desc = dp.build(logs_desc)
    for key in dp.FEATURE_SCHEMA:
        assert out_asc[key] == pytest.approx(out_desc[key], abs=1e-9), (
            f"mismatch on {key}: asc={out_asc[key]} desc={out_desc[key]}"
        )

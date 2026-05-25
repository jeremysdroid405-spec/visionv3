"""
Pin the critical 2026-05-24 fix: _score must use n_graded (wins+losses),
NOT n_bets (total rows), for the sample-size penalty.

Background: The operator caught a "100% HR / n=58 / ROI=13.2%" stored
result that re-traced to n_graded=6, wins=6, losses=0. The display
said n=58 because that was the row count in the cell, but only 6
rows had outcomes. _score was treating the 58 like a real
58-bet sample for the sample-size penalty calculation, which made
ungradable-dominated combos beat genuinely-graded combos with much
larger settled samples.

After this fix, _score derives `n` from wins+losses, so the
sample-size penalty correctly reflects statistical confidence.
"""
from __future__ import annotations
import sys
sys.path.insert(0, "/app/backend")

from routes.emergent_admin.optimizer import _score


def test_score_uses_settled_not_n_bets_for_sample_size():
    """The exact bug case: n_bets=58, n_graded=6, wins=6, losses=0
    must produce LOWER score than the same metrics with wins=58,
    losses=0, n_bets=58 — because n=6 has far less statistical
    weight than n=58."""
    metrics_thin = {
        "n_bets": 58, "n_graded": 6, "wins": 6, "losses": 0,
        "pushes": 0, "hit_rate": 1.0, "roi": 0.13,
        "calibration_delta": 0.0, "daily_consistency": 0.8,
        "max_drawdown_units": 0.0,
    }
    metrics_thick = {
        "n_bets": 58, "n_graded": 58, "wins": 58, "losses": 0,
        "pushes": 0, "hit_rate": 1.0, "roi": 0.13,
        "calibration_delta": 0.0, "daily_consistency": 0.8,
        "max_drawdown_units": 0.0,
    }
    s_thin  = _score(metrics_thin,  "balanced", baseline_n=10)
    s_thick = _score(metrics_thick, "balanced", baseline_n=10)
    assert s_thick > s_thin, (
        f"58 truly-settled bets must outscore 6 settled bets at the "
        f"same HR/ROI. Got thin={s_thin:.4f}, thick={s_thick:.4f}. "
        f"If they're equal, the sample-size penalty is using n_bets "
        f"not settled.")


def test_score_with_only_n_bets_unchanged_for_legacy_cells():
    """Legacy cells without `n_graded` should still work — fall
    through to using n_bets as the sample size (best-effort)."""
    legacy = {
        "n_bets": 30, "hit_rate": 0.60, "roi": 0.10,
        "calibration_delta": 0.0, "daily_consistency": 0.5,
        "max_drawdown_units": 0.0,
    }
    s = _score(legacy, "balanced", baseline_n=10)
    assert s is not None and s > 0


def test_score_returns_none_when_no_graded_rows():
    """If n_graded=0, score must be None (cell can't be ranked)."""
    m = {"n_bets": 100, "n_graded": 0, "wins": 0, "losses": 0,
           "hit_rate": None, "roi": None}
    assert _score(m, "balanced", baseline_n=10) is None


def test_sample_penalty_kicks_in_below_baseline():
    """6 settled bets vs 10 baseline = ~0.05 score drop. The exact
    bug result. Confirms the penalty IS being applied to thin
    samples now."""
    thin = {
        "n_bets": 6, "n_graded": 6, "wins": 6, "losses": 0,
        "hit_rate": 1.0, "roi": 0.13,
        "calibration_delta": 0.0, "daily_consistency": 0.8,
        "max_drawdown_units": 0.0,
    }
    at_baseline = {**thin, "n_bets": 10, "n_graded": 10,
                          "wins": 10, "losses": 0}
    s_thin  = _score(thin,        "balanced", baseline_n=10)
    s_bln   = _score(at_baseline, "balanced", baseline_n=10)
    # Tiny but non-zero penalty differential
    assert s_bln > s_thin
    assert (s_bln - s_thin) > 0.04

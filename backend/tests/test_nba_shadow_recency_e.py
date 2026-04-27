"""Unit tests for the NBA Shadow Recipe E projection layer.

Covers:
  - μ_E formula (renormalised weights = 1.0).
  - Stat-value extraction across PTS / REB / AST / PRA / combos.
  - Shadow never replaces μ_current — caller-side μ stays the same.
  - Audit fields stamped (or `applied=False` when skipped).
  - Skipped for non-volume stats (STL/BLK/etc.).
  - Skipped when fewer than 3 game logs carry the target stat.
"""
import pytest
from services.scoring.adapters.nba_scoring import NBAScoringAdapter


def _logs(rows):
    """rows = list of (date, min, pts, reb, ast)."""
    return [
        {"date": d, "min": m, "pts": p, "reb": r, "ast": a}
        for (d, m, p, r, a) in rows
    ]


def _adapter(pid, logs):
    a = NBAScoringAdapter()
    a._logs_by_id = {pid: logs}
    a._logs_loaded = True
    return a


def test_compute_shadow_E_basic_pts():
    # 10 games, 30 pts each. L3=L10mean=L10med=30. μ_model=20.
    # μ_E = (0.5*30 + 0.2*30 + 0.1*30 + 0.1*20)/0.9
    #     = (15 + 6 + 3 + 2)/0.9 = 26/0.9 ≈ 28.89
    logs = _logs([(f"2026-04-{15+i:02d}", 30, 30, 5, 5) for i in range(10)])
    res = NBAScoringAdapter._compute_shadow_recency_E(
        logs=logs, stat_type="PTS", mu_model=20.0, before_date=None,
    )
    assert res is not None
    assert res["L3"] == pytest.approx(30.0)
    assert res["L10MED"] == pytest.approx(30.0)
    assert res["L10"] == pytest.approx(30.0)
    expected = (0.5 * 30 + 0.2 * 30 + 0.1 * 30 + 0.1 * 20) / 0.9
    assert res["mu_E"] == pytest.approx(expected, rel=1e-3)


def test_compute_shadow_E_pra_sums_components():
    # 5 games each with pts=20, reb=5, ast=5 → PRA=30.
    logs = _logs([(f"2026-04-{20+i:02d}", 30, 20, 5, 5) for i in range(5)])
    res = NBAScoringAdapter._compute_shadow_recency_E(
        logs=logs, stat_type="PRA", mu_model=25.0, before_date=None,
    )
    assert res is not None
    assert res["L3"] == pytest.approx(30.0)
    expected = (0.5 * 30 + 0.2 * 30 + 0.1 * 30 + 0.1 * 25) / 0.9
    assert res["mu_E"] == pytest.approx(expected, rel=1e-3)


def test_compute_shadow_E_recency_decay():
    # L3 = 25 (decreased), L10 mean = 35 (heavier earlier games).
    logs = _logs([
        ("2026-04-25", 28, 25, 5, 5),
        ("2026-04-23", 28, 25, 5, 5),
        ("2026-04-21", 28, 25, 5, 5),
        ("2026-04-19", 36, 35, 6, 6),
        ("2026-04-17", 36, 35, 6, 6),
        ("2026-04-15", 36, 35, 6, 6),
        ("2026-04-13", 36, 35, 6, 6),
        ("2026-04-11", 36, 35, 6, 6),
        ("2026-04-09", 36, 35, 6, 6),
        ("2026-04-07", 36, 35, 6, 6),
    ])
    res = NBAScoringAdapter._compute_shadow_recency_E(
        logs=logs, stat_type="PTS", mu_model=32.0, before_date=None,
    )
    assert res["L3"] == pytest.approx(25.0)
    # L10 mean = (25*3 + 35*7)/10 = 32. L10 median = 35.
    assert res["L10"]    == pytest.approx(32.0)
    assert res["L10MED"] == pytest.approx(35.0)
    # Heavy L3 weight pulls μ toward 25.
    assert res["mu_E"] < 32.0


def test_shadow_does_not_replace_mu_current():
    """Critical invariant: shadow returns mu_current unchanged."""
    logs = _logs([(f"2026-04-{20+i:02d}", 30, 25, 6, 6) for i in range(10)])
    a = _adapter(123, logs)
    prop = {"commence_time": "2026-05-01T00:00:00Z"}
    out = a._maybe_apply_shadow_recency_E("PTS", 123, prop, 22.5)
    assert out == 22.5  # unchanged
    # Audit fields stamped
    assert prop["mu_recency_E_applied"] is False
    assert prop["mu_recency_E"] is not None
    assert "delta_mu_E_vs_A" in prop


def test_shadow_skipped_for_non_volume_stat():
    logs = _logs([(f"2026-04-{20+i:02d}", 30, 25, 6, 6) for i in range(5)])
    a = _adapter(999, logs)
    prop = {}
    out = a._maybe_apply_shadow_recency_E("STL", 999, prop, 1.5)
    assert out == 1.5
    assert prop["mu_recency_E_applied"] is False
    assert "mu_recency_E" not in prop


def test_shadow_skipped_when_logs_too_thin():
    # Only 2 games → below _SHADOW_E_MIN_SAMPLES = 3.
    logs = _logs([
        ("2026-04-25", 28, 18, 4, 4),
        ("2026-04-23", 28, 18, 4, 4),
    ])
    res = NBAScoringAdapter._compute_shadow_recency_E(
        logs=logs, stat_type="PTS", mu_model=20.0, before_date=None,
    )
    assert res is None


def test_shadow_skipped_when_logs_missing():
    a = _adapter(111, [])
    prop = {}
    out = a._maybe_apply_shadow_recency_E("PTS", 111, prop, 25.0)
    assert out == 25.0
    assert prop["mu_recency_E_applied"] is False


def test_shadow_skipped_when_mu_model_none():
    logs = _logs([(f"2026-04-{20+i:02d}", 28, 18, 4, 4) for i in range(5)])
    res = NBAScoringAdapter._compute_shadow_recency_E(
        logs=logs, stat_type="PTS", mu_model=None, before_date=None,
    )
    assert res is None


def test_shadow_filters_logs_before_capture():
    """Logs at or after `before_date` must be filtered out (no leakage)."""
    logs = _logs([
        ("2026-05-01", 30, 50, 10, 10),  # post-game (filtered)
        ("2026-04-25", 28, 18, 4, 4),
        ("2026-04-23", 28, 18, 4, 4),
        ("2026-04-21", 28, 18, 4, 4),
        ("2026-04-19", 28, 18, 4, 4),
    ])
    res = NBAScoringAdapter._compute_shadow_recency_E(
        logs=logs, stat_type="PTS", mu_model=20.0, before_date="2026-05-01",
    )
    assert res is not None
    assert res["L3"] == pytest.approx(18.0)  # post-game value excluded


def test_delta_mu_e_sign():
    """delta = mu_E − mu_current."""
    logs = _logs([(f"2026-04-{20+i:02d}", 28, 18, 4, 4) for i in range(5)])
    a = _adapter(222, logs)
    prop = {}
    a._maybe_apply_shadow_recency_E("PTS", 222, prop, 25.0)
    expected_delta = round(prop["mu_recency_E"] - 25.0, 4)
    assert prop["delta_mu_E_vs_A"] == pytest.approx(expected_delta)

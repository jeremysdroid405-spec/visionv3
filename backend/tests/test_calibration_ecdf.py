"""Tests for `apply_empirical_cdf_probability` + fallback chain
(services/scoring/calibration.py — 2026-04-23)."""
from __future__ import annotations

import os
import pickle
from pathlib import Path

import numpy as np
import pytest

from services.scoring import calibration
from services.probability import ecdf as universal_ecdf_mod


@pytest.fixture(autouse=True)
def _reset_env_and_caches(monkeypatch, tmp_path):
    monkeypatch.setattr(calibration, "PROB_CALIBRATOR_DIR", str(tmp_path))
    monkeypatch.delenv(calibration.FLAG_ENV, raising=False)
    monkeypatch.delenv(calibration.PROB_FLAG_ENV, raising=False)
    monkeypatch.delenv(calibration.PROB_STATS_ENV, raising=False)
    monkeypatch.delenv(calibration.ECDF_FLAG_ENV, raising=False)
    monkeypatch.delenv(calibration.ECDF_STATS_ENV, raising=False)
    calibration.reset_prob_calibrator_cache()
    calibration.reset_ecdf_cache()
    # Redirect the universal-ECDF singleton to an empty tmp root so
    # the legacy-fallback code path exercised by these tests doesn't
    # shadow-read real migrated artifacts from the default root.
    universal_ecdf_mod._SINGLETON = universal_ecdf_mod.UniversalECDFProbability(
        root=str(tmp_path / "universal_ecdf_empty"),
    )
    yield
    calibration.reset_prob_calibrator_cache()
    calibration.reset_ecdf_cache()
    universal_ecdf_mod.reset_universal_ecdf_singleton()


def _write_ecdf(tmp_path: Path, stat: str,
                residuals_by_bucket: dict,
                edges: np.ndarray | None = None):
    payload = {
        "stat": stat.upper(),
        "version": "NBA_VK2_ECDF_v1_test",
        "bucket_edges": edges if edges is not None else np.array(
            [-np.inf, 5.0, 10.0, np.inf]
        ),
        "sorted_residuals_by_bucket": {
            k: np.sort(v) for k, v in residuals_by_bucket.items()
        },
        "bucket_ns": {k: len(v) for k, v in residuals_by_bucket.items()},
        "min_bucket_n": min(len(v) for v in residuals_by_bucket.values()),
        "n_buckets": len(edges) - 1 if edges is not None else 3,
        "source_sigma": 2.0,
        "training_rows": sum(len(v) for v in residuals_by_bucket.values()),
    }
    path = tmp_path / f"prob_ecdf_{stat.lower()}.pkl"
    with open(path, "wb") as f:
        pickle.dump(payload, f)
    return path


def _mk_residuals(n: int, mean: float = 0.0, std: float = 1.0, seed: int = 0):
    rng = np.random.default_rng(seed)
    return rng.normal(mean, std, n)


def test_ecdf_missing_pkl_returns_none(monkeypatch):
    assert calibration.apply_empirical_cdf_probability("PTS", 10.0, 9.5) is None


def test_ecdf_basic_lookup_matches_gaussian_for_normal_residuals(
    monkeypatch, tmp_path,
):
    """With ~N(0,1) residuals, ECDF P(y > line) should match the
    Gaussian analytic very closely."""
    residuals = {
        0: _mk_residuals(10_000, mean=0.0, std=1.0, seed=1),
        1: _mk_residuals(10_000, mean=0.0, std=1.0, seed=2),
        2: _mk_residuals(10_000, mean=0.0, std=1.0, seed=3),
    }
    _write_ecdf(tmp_path, "PTS", residuals)

    # projection=10, line=10 ⇒ needed=0 ⇒ P(ε > 0) = 0.5
    out = calibration.apply_empirical_cdf_probability("PTS", 10.0, 10.0)
    assert out is not None
    assert out["p_over"] == pytest.approx(0.5, abs=0.02)

    # projection=10, line=11 ⇒ needed=1 ⇒ P(ε > 1) ≈ 0.1587
    out = calibration.apply_empirical_cdf_probability("PTS", 10.0, 11.0)
    assert out["p_over"] == pytest.approx(0.1587, abs=0.02)


def test_ecdf_heavy_right_tail_beats_gaussian(monkeypatch, tmp_path):
    """ECDF must reproduce the *empirical* tail of the residual
    distribution exactly — that is the correctness property that
    matters for this layer. The VK2 distribution audit showed real
    residuals are right-skewed with heavy tails; this test verifies
    the lookup logic faithfully reads those tails back."""
    rng = np.random.default_rng(42)
    # Clearly right-skewed, clearly heavier than Gaussian. Mean-centre
    # so the residuals look like VK2 residuals (mean ≈ 0).
    exp_samples = rng.exponential(scale=1.0, size=10_000)
    right_skewed = exp_samples - exp_samples.mean()
    # Empirical tail at the queried point (projection=10, line=10.5 ⇒
    # needed=0.5). Whatever that empirical value is, ECDF must match it.
    expected_tail = float(np.mean(right_skewed > 0.5))

    residuals = {0: right_skewed, 1: right_skewed, 2: right_skewed}
    _write_ecdf(tmp_path, "3PM", residuals)
    out = calibration.apply_empirical_cdf_probability("3PM", 10.0, 10.5)
    assert out is not None
    assert out["p_over"] == pytest.approx(expected_tail, abs=0.005)


def test_ecdf_flag_disabled(monkeypatch, tmp_path):
    residuals = {0: _mk_residuals(500), 1: _mk_residuals(500),
                 2: _mk_residuals(500)}
    _write_ecdf(tmp_path, "PTS", residuals)

    monkeypatch.setenv(calibration.ECDF_FLAG_ENV, "0")
    assert calibration.apply_empirical_cdf_probability(
        "PTS", 10.0, 9.5,
    ) is None


def test_ecdf_master_flag_disabled(monkeypatch, tmp_path):
    residuals = {0: _mk_residuals(500), 1: _mk_residuals(500),
                 2: _mk_residuals(500)}
    _write_ecdf(tmp_path, "PTS", residuals)

    monkeypatch.setenv(calibration.FLAG_ENV, "0")
    monkeypatch.setenv(calibration.ECDF_FLAG_ENV, "1")
    assert calibration.apply_empirical_cdf_probability(
        "PTS", 10.0, 9.5,
    ) is None


def test_ecdf_stats_whitelist(monkeypatch, tmp_path):
    residuals = {0: _mk_residuals(500), 1: _mk_residuals(500),
                 2: _mk_residuals(500)}
    _write_ecdf(tmp_path, "PTS", residuals)
    _write_ecdf(tmp_path, "AST", residuals)

    monkeypatch.setenv(calibration.ECDF_STATS_ENV, "AST, 3PM")
    assert calibration.apply_empirical_cdf_probability(
        "PTS", 10.0, 9.5,
    ) is None
    out = calibration.apply_empirical_cdf_probability("AST", 10.0, 9.5)
    assert out is not None


def test_ecdf_small_bucket_returns_none_forcing_fallback(monkeypatch, tmp_path):
    """When the selected bucket has < 20 residuals, ECDF returns None
    to force the caller to fall back. Critical safety guard."""
    residuals = {
        0: _mk_residuals(500),       # plenty
        1: _mk_residuals(5),         # too few
        2: _mk_residuals(500),
    }
    _write_ecdf(
        tmp_path, "PTS", residuals,
        edges=np.array([-np.inf, 5.0, 10.0, np.inf]),
    )
    # projection=7.0 falls into bucket 1 (too few residuals) → None
    assert calibration.apply_empirical_cdf_probability(
        "PTS", 7.0, 7.5,
    ) is None
    # projection=3.0 falls into bucket 0 (plenty) → succeeds
    assert calibration.apply_empirical_cdf_probability(
        "PTS", 3.0, 3.5,
    ) is not None


def test_ecdf_bucket_assignment(monkeypatch, tmp_path):
    residuals = {
        0: _mk_residuals(500, mean=0.0, seed=1),
        1: _mk_residuals(500, mean=0.0, seed=2),
        2: _mk_residuals(500, mean=0.0, seed=3),
    }
    edges = np.array([-np.inf, 5.0, 10.0, np.inf])
    _write_ecdf(tmp_path, "PTS", residuals, edges=edges)

    out0 = calibration.apply_empirical_cdf_probability("PTS", 3.0, 3.5)
    out1 = calibration.apply_empirical_cdf_probability("PTS", 7.0, 7.5)
    out2 = calibration.apply_empirical_cdf_probability("PTS", 15.0, 15.5)
    assert out0["bucket"] == 0
    assert out1["bucket"] == 1
    assert out2["bucket"] == 2


def test_ecdf_clamps_to_unit_interval(monkeypatch, tmp_path):
    residuals = {0: _mk_residuals(500, mean=0.0, std=1.0),
                 1: _mk_residuals(500, mean=0.0, std=1.0),
                 2: _mk_residuals(500, mean=0.0, std=1.0)}
    _write_ecdf(tmp_path, "PTS", residuals)
    # Extreme line far below projection → everything is an "over"
    out = calibration.apply_empirical_cdf_probability("PTS", 10.0, -50.0)
    assert 0.99 <= out["p_over"] <= 1.0
    # Extreme line far above projection → never an "over"
    out = calibration.apply_empirical_cdf_probability("PTS", 10.0, 50.0)
    assert 0.0 <= out["p_over"] <= 0.01


def test_ecdf_available_accessor(monkeypatch, tmp_path):
    assert not calibration.ecdf_available("PTS")
    residuals = {0: _mk_residuals(500), 1: _mk_residuals(500),
                 2: _mk_residuals(500)}
    _write_ecdf(tmp_path, "PTS", residuals)
    calibration.reset_ecdf_cache()
    assert calibration.ecdf_available("PTS")


def test_ecdf_none_inputs(monkeypatch, tmp_path):
    residuals = {0: _mk_residuals(500), 1: _mk_residuals(500),
                 2: _mk_residuals(500)}
    _write_ecdf(tmp_path, "PTS", residuals)
    assert calibration.apply_empirical_cdf_probability(
        "PTS", None, 9.5,
    ) is None
    assert calibration.apply_empirical_cdf_probability(
        "PTS", 10.0, None,
    ) is None

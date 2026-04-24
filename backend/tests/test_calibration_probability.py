"""Tests for the isotonic probability calibration layer
(`apply_probability_calibration` in services/scoring/calibration.py).

Scope: serving-side behaviour only — calibrator pkls are trained by
`scripts/train_prob_calibrators.py` and these tests operate on either
the loaded pkl (if present) or an in-memory stub.
"""
from __future__ import annotations

import os
import pickle
from pathlib import Path

import numpy as np
import pytest
from sklearn.isotonic import IsotonicRegression

from services.scoring import calibration


@pytest.fixture(autouse=True)
def _reset_caches(monkeypatch, tmp_path):
    """Isolate every test: redirect the pkl dir to a tmp path, clear
    any env vars that could leak from /app/backend/.env, and drop any
    cached calibrators between cases."""
    monkeypatch.setattr(calibration, "PROB_CALIBRATOR_DIR", str(tmp_path))
    monkeypatch.delenv(calibration.FLAG_ENV, raising=False)
    monkeypatch.delenv(calibration.PROB_FLAG_ENV, raising=False)
    monkeypatch.delenv(calibration.PROB_STATS_ENV, raising=False)
    calibration.reset_prob_calibrator_cache()
    yield
    calibration.reset_prob_calibrator_cache()


def _write_stub_calibrator(tmp_path: Path, stat: str, iso: IsotonicRegression):
    payload = {
        "stat": stat.upper(),
        "version": "NBA_VK2_ISOTONIC_v1_test",
        "calibrator": iso,
        "n_pairs": 0,
    }
    path = tmp_path / f"prob_calibrator_{stat.lower()}.pkl"
    with open(path, "wb") as f:
        pickle.dump(payload, f)


def test_missing_pkl_passes_through(monkeypatch, tmp_path):
    monkeypatch.delenv(calibration.FLAG_ENV, raising=False)
    assert calibration.apply_probability_calibration("PTS", 0.7) == 0.7
    assert not calibration.prob_calibrator_available("PTS")


def test_loaded_calibrator_transforms_value(monkeypatch, tmp_path):
    monkeypatch.delenv(calibration.FLAG_ENV, raising=False)
    # Build an isotonic that maps x → x * 0.5 (monotone, well-defined)
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    xs = np.linspace(0.0, 1.0, 50)
    iso.fit(xs, xs * 0.5)
    _write_stub_calibrator(tmp_path, "PTS", iso)

    out = calibration.apply_probability_calibration("PTS", 0.8)
    assert out == pytest.approx(0.4, abs=0.02)
    assert calibration.prob_calibrator_available("PTS")


def test_flag_disabled_skips_calibration(monkeypatch, tmp_path):
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    xs = np.linspace(0.0, 1.0, 50)
    iso.fit(xs, xs * 0.5)
    _write_stub_calibrator(tmp_path, "AST", iso)

    monkeypatch.setenv(calibration.FLAG_ENV, "0")
    assert calibration.apply_probability_calibration("AST", 0.9) == 0.9


def test_prob_flag_disabled_skips_calibration_but_intercept_still_works(monkeypatch, tmp_path):
    """Operators can disable prob calibration independently while
    keeping the projection intercept shift active."""
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    xs = np.linspace(0.0, 1.0, 50)
    iso.fit(xs, xs * 0.5)
    _write_stub_calibrator(tmp_path, "PTS", iso)

    monkeypatch.delenv(calibration.FLAG_ENV, raising=False)
    monkeypatch.setenv(calibration.PROB_FLAG_ENV, "0")
    # prob path is skipped
    assert calibration.apply_probability_calibration("PTS", 0.9) == 0.9
    # intercept still applied
    assert calibration.apply_projection_intercept("PTS", 10.0) == pytest.approx(9.906)


def test_prob_flag_requires_master_flag(monkeypatch, tmp_path):
    """Even if prob flag is ON, master flag being OFF disables prob."""
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    xs = np.linspace(0.0, 1.0, 50)
    iso.fit(xs, xs * 0.5)
    _write_stub_calibrator(tmp_path, "PTS", iso)

    monkeypatch.setenv(calibration.FLAG_ENV, "0")
    monkeypatch.setenv(calibration.PROB_FLAG_ENV, "1")
    assert not calibration.prob_calibration_flag_enabled()
    assert calibration.apply_probability_calibration("PTS", 0.9) == 0.9


def test_prob_stats_whitelist_enforced(monkeypatch, tmp_path):
    """The per-stat whitelist restricts calibration to listed stats
    only. Stats not in the whitelist return raw p_over unchanged."""
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    xs = np.linspace(0.0, 1.0, 50)
    iso.fit(xs, xs * 0.5)
    _write_stub_calibrator(tmp_path, "PTS", iso)
    _write_stub_calibrator(tmp_path, "REB", iso)

    monkeypatch.delenv(calibration.FLAG_ENV, raising=False)
    monkeypatch.delenv(calibration.PROB_FLAG_ENV, raising=False)
    monkeypatch.setenv(calibration.PROB_STATS_ENV, "REB, AST, 3PM")
    # REB is whitelisted → transformed
    assert calibration.apply_probability_calibration("REB", 0.9) == pytest.approx(0.45, abs=0.02)
    # PTS is NOT whitelisted → raw passthrough
    assert calibration.apply_probability_calibration("PTS", 0.9) == 0.9


def test_prob_stats_whitelist_empty_string_allows_all(monkeypatch, tmp_path):
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    xs = np.linspace(0.0, 1.0, 50)
    iso.fit(xs, xs * 0.5)
    _write_stub_calibrator(tmp_path, "PTS", iso)

    monkeypatch.delenv(calibration.FLAG_ENV, raising=False)
    monkeypatch.setenv(calibration.PROB_STATS_ENV, "  ")
    assert calibration.apply_probability_calibration("PTS", 0.9) == pytest.approx(0.45, abs=0.02)


def test_none_input_passthrough(monkeypatch, tmp_path):
    monkeypatch.delenv(calibration.FLAG_ENV, raising=False)
    assert calibration.apply_probability_calibration("PTS", None) is None


def test_output_is_clamped_to_unit_interval(monkeypatch, tmp_path):
    # An isotonic that maps slightly outside [0, 1] would be pathological
    # but we still defend. We fit it to produce > 1 then confirm clamp.
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=2.0)
    xs = np.linspace(0.0, 1.0, 10)
    iso.fit(xs, xs * 2.0)
    _write_stub_calibrator(tmp_path, "PRA", iso)

    monkeypatch.delenv(calibration.FLAG_ENV, raising=False)
    out = calibration.apply_probability_calibration("PRA", 0.9)
    assert 0.0 <= out <= 1.0


def test_independent_caches_per_stat(monkeypatch, tmp_path):
    iso_pts = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    iso_pts.fit(np.linspace(0.0, 1.0, 10), np.linspace(0.0, 1.0, 10))

    iso_reb = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    iso_reb.fit(np.linspace(0.0, 1.0, 10), np.linspace(0.0, 0.5, 10))
    _write_stub_calibrator(tmp_path, "PTS", iso_pts)
    _write_stub_calibrator(tmp_path, "REB", iso_reb)

    monkeypatch.delenv(calibration.FLAG_ENV, raising=False)
    assert calibration.apply_probability_calibration("PTS", 0.8) == pytest.approx(0.8, abs=0.02)
    assert calibration.apply_probability_calibration("REB", 0.8) == pytest.approx(0.4, abs=0.02)

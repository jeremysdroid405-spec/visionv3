"""Tests for `services/scoring/calibration.py` — VK2 projection
intercept shift (audit-derived, 2026-04-23)."""
from __future__ import annotations

import os

import pytest

from services.scoring import calibration


def test_pts_pra_receive_negative_shift():
    assert calibration.PROJECTION_INTERCEPTS["PTS"] == pytest.approx(-0.094)
    assert calibration.PROJECTION_INTERCEPTS["PRA"] == pytest.approx(-0.103)


def test_reb_ast_3pm_receive_zero_shift():
    for stat in ("REB", "AST", "3PM"):
        assert calibration.PROJECTION_INTERCEPTS[stat] == 0.0


def test_apply_shift_enabled(monkeypatch):
    monkeypatch.delenv(calibration.FLAG_ENV, raising=False)
    assert calibration.calibration_flag_enabled()
    # PTS: 10.0 - 0.094 = 9.906
    assert calibration.apply_projection_intercept("PTS", 10.0) == pytest.approx(9.906)
    # PRA: 25.0 - 0.103 = 24.897
    assert calibration.apply_projection_intercept("PRA", 25.0) == pytest.approx(24.897)
    # REB / AST / 3PM should be unchanged (zero shift)
    for stat in ("REB", "AST", "3PM"):
        assert calibration.apply_projection_intercept(stat, 4.0) == 4.0


def test_flag_disabled_returns_input(monkeypatch):
    for val in ("0", "false", "FALSE", "off", "no"):
        monkeypatch.setenv(calibration.FLAG_ENV, val)
        assert not calibration.calibration_flag_enabled()
        # No shift applied
        assert calibration.apply_projection_intercept("PTS", 10.0) == 10.0
        assert calibration.apply_projection_intercept("PRA", 25.0) == 25.0


def test_flag_enabled_literal_true(monkeypatch):
    for val in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv(calibration.FLAG_ENV, val)
        assert calibration.calibration_flag_enabled()


def test_clamp_non_negative(monkeypatch):
    monkeypatch.delenv(calibration.FLAG_ENV, raising=False)
    # A very small projection shouldn't go negative after subtraction
    assert calibration.apply_projection_intercept("PRA", 0.05) == 0.0


def test_none_projection_passthrough(monkeypatch):
    monkeypatch.delenv(calibration.FLAG_ENV, raising=False)
    assert calibration.apply_projection_intercept("PTS", None) is None


def test_unknown_stat_type_passthrough(monkeypatch):
    monkeypatch.delenv(calibration.FLAG_ENV, raising=False)
    # A stat not in the intercept table should pass through unchanged.
    assert calibration.apply_projection_intercept("STL", 2.0) == 2.0


def test_intercept_for_known_and_unknown():
    assert calibration.intercept_for("PTS") == pytest.approx(-0.094)
    assert calibration.intercept_for("STL") == 0.0

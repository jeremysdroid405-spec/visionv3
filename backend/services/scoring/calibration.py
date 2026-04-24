"""
VK2 Calibration Layer (2026-04-23)

Post-prediction corrections for the production VK2 stack, derived from
the 2026-04-23 calibration audit (`reports/vk2_calibration_audit.md`).

Contents:
  - PROJECTION_INTERCEPTS: per-stat additive shift applied to VK2's
    point projection. Only PTS and PRA receive a non-zero shift
    (audit found +0.094 and +0.103 mean over-projection respectively);
    REB / AST / 3PM are globally calibrated and receive no shift.

  - apply_projection_intercept(stat_type, projection) -> projection
    Returns the calibrated projection, or the input unchanged when the
    feature flag `VK2_CALIBRATION_ENABLED` is disabled.

  - apply_probability_calibration(stat_type, raw_p_over) -> calibrated
    Per-stat isotonic-regression calibration loaded lazily from
    `/app/backend/models/prob_calibrator_{stat}.pkl`. Rewrites only the
    Gaussian-CDF `p_over`; projection and sigma are untouched.
    Gated by the same `VK2_CALIBRATION_ENABLED` flag.

  - calibration_flag_enabled() -> bool
    Reads `VK2_CALIBRATION_ENABLED` from env (default: enabled).

This module must NOT modify gates directly. Probability changes flow
through the scored-doc `p_over` field and reach gates via the existing
downstream pipeline.
"""
from __future__ import annotations

import logging
import os
import pickle
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Source: `reports/vk2_calibration_audit.md` § Headline (2024 held-out).
# Convention: shift = -mean_bias, so we subtract. Positive mean bias
# (over-projection) → subtract a positive amount.
PROJECTION_INTERCEPTS: Dict[str, float] = {
    "PTS":  -0.094,   # audit bias +0.0945 → shift -0.094
    "REB":   0.0,     # audit bias +0.0050 (noise; no shift)
    "AST":   0.0,     # audit bias +0.0170 (noise; no shift)
    "3PM":   0.0,     # audit bias -0.0029 (noise; no shift)
    "PRA":  -0.103,   # audit bias +0.1033 → shift -0.103
}

# Feature flag — the intercept shift is ON by default; operators can
# flip it off live via env var without code changes. Set to any of
# "0", "false", "off", "no" (case-insensitive) to disable.
FLAG_ENV = "VK2_CALIBRATION_ENABLED"
# Separate flag for the probability-calibration layer so operators can
# A/B the two corrections independently. Falls back to the master
# flag when unset.
PROB_FLAG_ENV = "VK2_PROB_CALIBRATION_ENABLED"
# Optional per-stat whitelist — comma-separated stat keys
# (e.g. "REB,AST,3PM"). When set, only listed stats pass through the
# isotonic calibrator; all others return raw p_over. Unset (default)
# means every stat that has a loaded pkl gets calibrated.
PROB_STATS_ENV = "VK2_PROB_CALIBRATION_STATS"
_DISABLED_VALUES = {"0", "false", "off", "no"}

PROB_CALIBRATOR_DIR = "/app/backend/models"
PROB_CALIBRATOR_TEMPLATE = "prob_calibrator_{stat}.pkl"
_prob_calibrators: Dict[str, Optional[Any]] = {}
_prob_calibrator_load_attempted: Dict[str, bool] = {}


def calibration_flag_enabled() -> bool:
    raw = os.environ.get(FLAG_ENV)
    if raw is None:
        return True
    return raw.strip().lower() not in _DISABLED_VALUES


def prob_calibration_flag_enabled() -> bool:
    """Probability-calibration gate. Requires BOTH the master
    calibration flag AND the prob-specific flag to be enabled. The
    prob flag defaults to ON when unset; operators can disable it
    independently (e.g. to keep the intercept shift while rolling
    back probability calibration)."""
    if not calibration_flag_enabled():
        return False
    raw = os.environ.get(PROB_FLAG_ENV)
    if raw is None:
        return True
    return raw.strip().lower() not in _DISABLED_VALUES


def _prob_calibration_stat_allowed(stat_type: str) -> bool:
    """Honour the optional per-stat whitelist
    (`VK2_PROB_CALIBRATION_STATS`). When unset → every stat allowed.
    When set → only the listed stats pass; others return raw p_over.
    Comparison is case-insensitive, whitespace-stripped."""
    raw = os.environ.get(PROB_STATS_ENV)
    if raw is None or not raw.strip():
        return True
    allowed = {s.strip().upper() for s in raw.split(",") if s.strip()}
    return stat_type.upper() in allowed


def apply_projection_intercept(
    stat_type: str, projection: Optional[float],
) -> Optional[float]:
    """Return the calibrated projection.

    - Returns the input unchanged when the flag is disabled, when the
      projection is None, or when `stat_type` has no entry in the
      intercept table.
    - Otherwise adds `PROJECTION_INTERCEPTS[stat_type]` (which may be
      negative) and clamps to >= 0 since no NBA counting stat can be
      negative.
    """
    if projection is None:
        return projection
    if not calibration_flag_enabled():
        return projection
    delta = PROJECTION_INTERCEPTS.get(stat_type)
    if not delta:
        return projection
    shifted = float(projection) + float(delta)
    return max(0.0, shifted)


def intercept_for(stat_type: str) -> float:
    """Read-only accessor used by audit / observability endpoints."""
    return float(PROJECTION_INTERCEPTS.get(stat_type, 0.0))


# ---------------------------------------------------------------------------
# Isotonic probability calibrators — lazy-loaded from pkl once per process.
# ---------------------------------------------------------------------------
def _load_prob_calibrator(stat_type: str) -> Optional[Any]:
    key = stat_type.lower()
    if _prob_calibrator_load_attempted.get(key):
        return _prob_calibrators.get(key)
    _prob_calibrator_load_attempted[key] = True
    path = os.path.join(
        PROB_CALIBRATOR_DIR,
        PROB_CALIBRATOR_TEMPLATE.format(stat=key),
    )
    if not os.path.exists(path):
        logger.info(
            f"[CALIBRATION] no prob-calibrator pkl for {stat_type} at {path}; "
            f"raw Gaussian p_over will be used."
        )
        _prob_calibrators[key] = None
        return None
    try:
        with open(path, "rb") as f:
            payload = pickle.load(f)
        iso = payload.get("calibrator")
        if iso is None:
            logger.warning(
                f"[CALIBRATION] pkl for {stat_type} has no 'calibrator' key; "
                f"skipping."
            )
            _prob_calibrators[key] = None
            return None
        _prob_calibrators[key] = iso
        logger.info(
            f"[CALIBRATION] loaded isotonic calibrator for {stat_type} "
            f"(version={payload.get('version')}, "
            f"n_pairs={payload.get('n_pairs', '?')})"
        )
        return iso
    except Exception as e:
        logger.warning(
            f"[CALIBRATION] failed to load prob-calibrator for {stat_type}: {e}"
        )
        _prob_calibrators[key] = None
        return None


def apply_probability_calibration(
    stat_type: str, raw_p_over: Optional[float],
) -> Optional[float]:
    """Pass the raw Gaussian-CDF p_over through the stat's isotonic
    calibrator. Returns the input unchanged when:
      - the flag is disabled,
      - the input is None,
      - no calibrator is available for the stat.
    Clamps the output to [0, 1] for safety.
    """
    if raw_p_over is None:
        return raw_p_over
    if not prob_calibration_flag_enabled():
        return raw_p_over
    if not _prob_calibration_stat_allowed(stat_type):
        return raw_p_over
    iso = _load_prob_calibrator(stat_type)
    if iso is None:
        return raw_p_over
    try:
        calibrated = float(iso.transform([float(raw_p_over)])[0])
    except Exception as e:
        logger.warning(
            f"[CALIBRATION] isotonic transform failed for {stat_type}: {e}"
        )
        return raw_p_over
    return max(0.0, min(1.0, calibrated))


def prob_calibrator_available(stat_type: str) -> bool:
    """Read-only accessor — True when a calibrator pkl is loaded for
    the stat. Used by observability."""
    return _load_prob_calibrator(stat_type) is not None


def reset_prob_calibrator_cache() -> None:
    """Test helper: drop the cached calibrators so the next call
    reloads the pkls fresh."""
    _prob_calibrators.clear()
    _prob_calibrator_load_attempted.clear()

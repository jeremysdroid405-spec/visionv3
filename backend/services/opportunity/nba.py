"""NBA Opportunity Adapter (2026-04-23).

Produces the universal `OpportunityOutput` contract for NBA. Wraps two
already-trained artifacts without retraining:

  1. `models/expected_minutes.pkl` — 12-feat minutes regressor
     → `expected_opportunity` (expected minutes tonight)
  2. `models/low_minutes_classifier.pkl` — 15-feat binary classifier
     → `opportunity_risk_score` (prob of min_played <= 12)

Bucketing (aligned with the roster-role thresholds validated in the
2026-04-23 minutes-threshold analysis, cutoff=26 as the
recommended separator between stable role and minutes-risk):
    high   : expected_minutes >= 26
    medium : 16 <= expected_minutes < 26
    low    : expected_minutes < 16

Confidence = 1 - normalized(sigma_minutes * (1 + 2*risk)). Tight
prediction + low risk → confidence ≈ 1. Noisy prediction or high
DNP risk → confidence ≈ 0.

Nothing in this module touches projections, gates, or scoring.
"""
from __future__ import annotations

import logging
import math
import os
import pickle
from dataclasses import dataclass
from datetime import date as _date, datetime
from typing import Any, Dict, List, Optional

import numpy as np

from .base import (
    OpportunityAdapter,
    OpportunityOutput,
    PlayerContext,
    bucket_from_value,
)

logger = logging.getLogger(__name__)


MINUTES_MODEL_PATH = "/app/backend/models/expected_minutes.pkl"
LOW_MIN_CLASSIFIER_PATH = "/app/backend/models/low_minutes_classifier.pkl"

# Bucket thresholds (from 2026-04-23 minutes-threshold analysis).
HIGH_MIN_THRESHOLD = 26.0
LOW_MIN_THRESHOLD = 16.0
SIGMA_MINUTES_REFERENCE = 8.74   # from expected_minutes.pkl training run
CONFIDENCE_SCALE = 12.0          # normalization scale for confidence; picked so
                                 #   sigma=4 + risk=0.1 → confidence ~0.62,
                                 #   sigma=10 + risk=0.7 → confidence ~0.04


def _parse_minutes(value):
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        if ":" in s:
            try:
                mm, ss = s.split(":")
                return float(mm) + float(ss) / 60.0
            except Exception:
                return None
        try:
            return float(s)
        except Exception:
            return None
    try:
        return float(value)
    except Exception:
        return None


def _mean_std(vals):
    if not vals:
        return 0.0, 0.0
    arr = np.asarray(vals, dtype=np.float32)
    return float(arr.mean()), (float(arr.std(ddof=1)) if len(arr) >= 2 else 0.0)


class NBAOpportunityAdapter(OpportunityAdapter):
    sport = "nba"

    def __init__(self) -> None:
        self._minutes_payload: Optional[Dict[str, Any]] = None
        self._classifier_payload: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------
    # Lazy loaders — adapters are cheap to construct but expensive to
    # hydrate. Callers that predict in a loop get one-time load cost.
    # ------------------------------------------------------------------
    def _load(self) -> None:
        if self._minutes_payload is None:
            if not os.path.exists(MINUTES_MODEL_PATH):
                raise RuntimeError(
                    f"expected_minutes model missing: {MINUTES_MODEL_PATH}"
                )
            with open(MINUTES_MODEL_PATH, "rb") as f:
                self._minutes_payload = pickle.load(f)
        if self._classifier_payload is None:
            if not os.path.exists(LOW_MIN_CLASSIFIER_PATH):
                raise RuntimeError(
                    f"low_minutes classifier missing: {LOW_MIN_CLASSIFIER_PATH}"
                )
            with open(LOW_MIN_CLASSIFIER_PATH, "rb") as f:
                self._classifier_payload = pickle.load(f)

    @property
    def minutes_model_version(self) -> Optional[str]:
        if self._minutes_payload is None:
            return None
        return self._minutes_payload.get("version")

    @property
    def classifier_version(self) -> Optional[str]:
        if self._classifier_payload is None:
            return None
        return self._classifier_payload.get("version")

    # ------------------------------------------------------------------
    # Feature derivation
    # ------------------------------------------------------------------
    def _derive_base_stats(self, history_logs):
        """Common rolling minutes stats used by both sub-models."""
        if len(history_logs) < 5:
            return None
        mins_series = []
        for g in history_logs[:20]:
            m = _parse_minutes(g.get("min"))
            mins_series.append(m if m is not None else 0.0)
        m_L3, _    = _mean_std(mins_series[:3])
        m_L5, _    = _mean_std(mins_series[:5])
        m_L10, s10 = _mean_std(mins_series[:10])
        m_L20, s20 = _mean_std(mins_series[:20])
        last10 = mins_series[:10]
        last20 = mins_series[:20]
        return {
            "mins_series": mins_series,
            "m_L3": m_L3, "m_L5": m_L5, "m_L10": m_L10, "m_L20": m_L20,
            "s_L10": s10, "s_L20": s20,
            "trend": m_L5 - m_L20,
            "games_played_L10": float(sum(1 for m in last10 if m > 0)),
            "games_started_L10": float(sum(1 for m in last10 if m >= 20)),
            "min_floor_L20": float(min(last20)) if last20 else 0.0,
            "min_ceiling_L20": float(max(last20)) if last20 else 0.0,
        }

    def _minutes_feature_vector(self, base, situational) -> np.ndarray:
        """Feature vector matching `models/expected_minutes.pkl`
        (strict 12-feat v2 schema). See
        `scripts/train_expected_minutes.py::FEATURE_SCHEMA`."""
        m_L5 = base["m_L5"]
        feats = {
            "min_played_L3_mean":     base["m_L3"],
            "min_played_L5_mean":     m_L5,
            "min_played_L10_mean":    base["m_L10"],
            "min_played_L20_mean":    base["m_L20"],
            "min_played_L10_std":     base["s_L10"],
            "min_played_L20_std":     base["s_L20"],
            "min_trend_L5_vs_L20":    base["trend"],
            "starter_flag":           1.0 if m_L5 >= 28.0 else 0.0,
            "rotation_flag":          1.0 if (18.0 <= m_L5 < 28.0) else 0.0,
            "bench_flag":             1.0 if m_L5 < 18.0 else 0.0,
            "games_played_last_10":   base["games_played_L10"],
            "games_started_last_10":  base["games_started_L10"],
        }
        schema = self._minutes_payload["features"]
        row = np.asarray([[feats.get(c, 0.0) for c in schema]], dtype=np.float32)
        return row

    def _classifier_feature_vector(self, base, situational) -> np.ndarray:
        """15-feat vector matching `models/low_minutes_classifier.pkl` schema."""
        m_L5 = base["m_L5"]
        feats = {
            "min_played_L3_mean":    base["m_L3"],
            "min_played_L5_mean":    m_L5,
            "min_played_L10_mean":   base["m_L10"],
            "min_played_L20_mean":   base["m_L20"],
            "min_played_L10_std":    base["s_L10"],
            "min_played_L20_std":    base["s_L20"],
            "min_trend_L5_vs_L20":   base["trend"],
            "games_played_last_10":  base["games_played_L10"],
            "games_started_last_10": base["games_started_L10"],
            "starter_flag":          1.0 if m_L5 >= 28.0 else 0.0,
            "rotation_flag":         1.0 if (18.0 <= m_L5 < 28.0) else 0.0,
            "bench_flag":            1.0 if m_L5 < 18.0 else 0.0,
            "home_flag":             float(situational.get("home_flag", 0.0)),
            "rest_days":             float(situational.get("rest_days", 3.0)),
            "back_to_back_flag":     float(situational.get("back_to_back_flag", 0.0)),
        }
        schema = self._classifier_payload["features"]
        row = np.asarray([[feats.get(c, 0.0) for c in schema]], dtype=np.float32)
        return row

    # ------------------------------------------------------------------
    # Prediction
    # ------------------------------------------------------------------
    def _empty_output(self, ctx: PlayerContext, reason: str) -> OpportunityOutput:
        """Zero-safe fallback when history is insufficient — returns a
        medium-bucket sample the downstream model has seen during
        training with the same default policy."""
        return OpportunityOutput(
            sport=self.sport,
            player_id=str(ctx.player_id),
            bdl_player_id=ctx.bdl_player_id,
            expected_opportunity=0.0,
            opportunity_type="minutes",
            opportunity_bucket="low",
            opportunity_risk_score=1.0,
            opportunity_confidence=0.0,
            model_version=None,
            features_used=None,
            extras={"reason": reason},
        )

    def predict(self, ctx: PlayerContext) -> OpportunityOutput:
        self._load()
        base = self._derive_base_stats(ctx.history_logs)
        if base is None:
            return self._empty_output(ctx, "insufficient_history")

        # Minutes model
        m_row = self._minutes_feature_vector(base, ctx.situational)
        m_row_s = self._minutes_payload["scaler"].transform(m_row)
        expected_minutes = float(self._minutes_payload["model"].predict(m_row_s)[0])
        expected_minutes = max(0.0, min(48.0, expected_minutes))

        # Low-minutes classifier
        c_row = self._classifier_feature_vector(base, ctx.situational)
        c_row_s = self._classifier_payload["scaler"].transform(c_row)
        risk = float(
            self._classifier_payload["model_low_12"].predict_proba(c_row_s)[0, 1]
        )
        risk = max(0.0, min(1.0, risk))

        # Bucket (tied to the 2026-04-23 threshold analysis recommendation).
        bucket = bucket_from_value(
            expected_minutes, HIGH_MIN_THRESHOLD, LOW_MIN_THRESHOLD,
        )

        # Confidence — tight minutes + low risk → near 1.
        # uncertainty = sigma * (1 + 2*risk). sigma~8.7 in the strict
        # global training set; per-player sigma isn't cheap to recover,
        # so we scale by the observed std_L10 as a proxy when available.
        per_player_sigma = base["s_L10"] if base["s_L10"] > 0 else SIGMA_MINUTES_REFERENCE
        uncertainty = per_player_sigma * (1.0 + 2.0 * risk)
        confidence = math.exp(-uncertainty / CONFIDENCE_SCALE)
        confidence = max(0.0, min(1.0, confidence))

        return OpportunityOutput(
            sport=self.sport,
            player_id=str(ctx.player_id),
            bdl_player_id=ctx.bdl_player_id,
            expected_opportunity=round(expected_minutes, 3),
            opportunity_type="minutes",
            opportunity_bucket=bucket,
            opportunity_risk_score=round(risk, 4),
            opportunity_confidence=round(confidence, 4),
            model_version=(
                f"{self.minutes_model_version}|{self.classifier_version}"
            ),
            features_used=[
                *self._minutes_payload["features"],
                *self._classifier_payload["features"],
            ],
            extras={
                "min_L5_mean":  round(base["m_L5"], 3),
                "min_L10_mean": round(base["m_L10"], 3),
                "sigma_minutes_proxy": round(per_player_sigma, 3),
            },
        )

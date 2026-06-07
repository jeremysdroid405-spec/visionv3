"""Universal Line-Outcome Model (LOM) — calibrated P(actual > line).

Replaces ECDF bucket lookup as the preferred probability translator in
the MLB / NFL adapter ladder when a per-(sport, stat_family) artifact
exists. Falls through to ECDF, then Gaussian, when the LOM artifact is
absent for the requested family.

v1 (2026-05) — features intentionally exclude `market_implied_prob` and
`odds_bucket` so the LOM-derived `edge_pct` stays independent from
market pricing. Adding either feature would short-circuit the model
into echoing the market.

Artifact contract:
    {
        "model": fitted CalibratedClassifierCV (returns over_prob),
        "feature_cols": [str, ...],
        "stat_family": str,
        "sport": str,
        "version": "v1-no-market",
        "trained_at": iso datetime,
        "n_rows": int,
        "n_train": int, "n_test": int,
        "brier": float,
        "log_loss": float,
        "reliability": [{lo, hi, n, avg_pred, actual_rate, error}, ...],
    }

Threading: lazy-loaded singleton; per-(sport, family) cache is guarded
by a `threading.Lock` so concurrent first-load races collapse to one
disk read.
"""
from __future__ import annotations

import os
import pickle
import threading
from typing import Any, Dict, Optional, Tuple


LOM_ARTIFACT_DIR = "/var/www/app/backend/models/probability/lom"


def _safe_family_path(family: str) -> str:
    # `hits+runs+rbis` → `hits_runs_rbis` so it's a legal filename.
    return family.lower().replace("+", "_").replace(" ", "_")


class UniversalLineOutcomeModel:
    """Singleton-friendly LOM accessor."""

    def __init__(self) -> None:
        self._cache: Dict[Tuple[str, str], Optional[Dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def _load(self, sport: str, stat_family: str) -> Optional[Dict[str, Any]]:
        key = (sport.lower(), _safe_family_path(stat_family))
        with self._lock:
            if key in self._cache:
                return self._cache[key]
            path = os.path.join(LOM_ARTIFACT_DIR, key[0], f"{key[1]}.pkl")
            if not os.path.exists(path):
                self._cache[key] = None
                return None
            try:
                with open(path, "rb") as f:
                    artifact = pickle.load(f)
                self._cache[key] = artifact
                return artifact
            except Exception:
                self._cache[key] = None
                return None

    def predict_proba_over(
        self,
        *,
        sport: str,
        stat_family: str,
        projection: Optional[float],
        line: Optional[float],
        sigma: Optional[float] = None,
        hit_rate_at_line: Optional[float] = None,   # 0..100 percent
        hit_rate_sample_size: Optional[int] = None,
        cv: Optional[float] = None,
        avg_hit_margin: Optional[float] = None,
        avg_miss_margin: Optional[float] = None,
    ) -> Optional[float]:
        """Returns ``P(actual > line)`` ∈ [0, 1] or None.

        UNDER probability = 1 - over (callers do that flip).
        """
        artifact = self._load(sport, stat_family)
        if artifact is None or projection is None or line is None:
            return None
        try:
            sigma_safe = max(float(sigma), 0.1) if sigma is not None else 0.5
            line_distance = float(projection) - float(line)
            line_distance_ratio = line_distance / sigma_safe
            feats: Dict[str, float] = {
                "projection": float(projection),
                "line": float(line),
                "line_distance": line_distance,
                "line_distance_ratio": line_distance_ratio,
                "hr_at_line": (
                    float(hit_rate_at_line)
                    if hit_rate_at_line is not None else 0.0
                ),
                "hr_sample_size": (
                    float(hit_rate_sample_size)
                    if hit_rate_sample_size is not None else 0.0
                ),
                "cv": float(cv) if cv is not None else 0.0,
                "avg_hit_margin": (
                    float(avg_hit_margin)
                    if avg_hit_margin is not None else 0.0
                ),
                "avg_miss_margin": (
                    float(avg_miss_margin)
                    if avg_miss_margin is not None else 0.0
                ),
                # Missing-flags so the model sees "no data" distinctly
                # from a legitimate zero. v1 keeps these binary.
                "hr_missing": 0.0 if hit_rate_at_line is not None else 1.0,
                "margin_missing": 0.0 if avg_hit_margin is not None else 1.0,
            }
            import numpy as np
            cols = artifact["feature_cols"]
            x = np.array([[feats.get(c, 0.0) for c in cols]])
            p = float(artifact["model"].predict_proba(x)[0, 1])
            # Clamp to (0, 1) — isotonic can output exact 0 / 1 and the
            # downstream ladder treats 1.0 as "saturated", which is
            # exactly what we're trying to eliminate. Squeeze edges.
            p = min(max(p, 0.001), 0.999)
            return p
        except Exception:
            return None


_LOM_SINGLETON: Optional[UniversalLineOutcomeModel] = None


def get_universal_lom() -> UniversalLineOutcomeModel:
    global _LOM_SINGLETON
    if _LOM_SINGLETON is None:
        _LOM_SINGLETON = UniversalLineOutcomeModel()
    return _LOM_SINGLETON


__all__ = ["UniversalLineOutcomeModel", "get_universal_lom"]

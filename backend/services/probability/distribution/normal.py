"""
Normal-CDF distribution.
========================

`p_over = 1 − Φ((line − μ) / σ)`

σ is derived from the player's coefficient of variation (CV) applied
to a per-family **effective μ**:

    cv_used      = max(player_cv, cv_floor[family])
    effective_mu = max(|μ|, mu_floor[family])
    if config.mu_floor_capped:
        effective_mu = min(effective_mu, |line|)
    σ            = max(cv_used × effective_mu, sigma_min_absolute)

Notes
-----
- The μ-floor is applied **only** to σ scaling. The z-score numerator
  uses the raw μ (`line − μ`), so the projection itself is never
  altered.
- `mu_floor_capped=True` (default for low-line event families on most
  sports) prevents σ from blowing past the line on rare-event 0.5-line
  props.
- No "force-below-50%" override — the bare CDF result is returned and
  clamped to [0.01, 0.99] only.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .base import (
    Distribution, DistributionConfig, ProbabilityResult, clamp_p,
)


@dataclass(frozen=True)
class NormalCDFConfig(DistributionConfig):
    name: str = "normal_cdf"
    cv_floor: float = 0.50
    mu_floor: float = 0.50
    sigma_min_absolute: float = 0.20
    mu_floor_capped: bool = False  # cap effective_mu at |line|


def _normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


class NormalCDFDistribution(Distribution):
    name = "normal_cdf"

    def __init__(self, config: NormalCDFConfig):
        self.config = config

    def compute(
        self,
        sport: str,
        stat_family: str,
        mu: Optional[float],
        line: Optional[float],
        cv: Optional[float] = None,
        sigma: Optional[float] = None,
        extras: Optional[Dict[str, Any]] = None,
    ) -> Optional[ProbabilityResult]:
        if mu is None or line is None:
            return None
        try:
            mu_f = float(mu)
            line_f = float(line)
        except (TypeError, ValueError):
            return None

        cfg = self.config

        # ---- Explicit-sigma fast path -----------------------------------
        # NBA / future sports already supply an empirical residual σ from
        # the projection model; honour it and skip the CV-derived path.
        if sigma is not None and sigma > 0:
            sigma_v = max(float(sigma), cfg.sigma_min_absolute)
            z = (line_f - mu_f) / sigma_v
            p_under = _normal_cdf(z)
            p_over = 1.0 - p_under
            p_over, clamped = clamp_p(p_over)
            p_under = 1.0 - p_over
            return ProbabilityResult(
                p_over=round(p_over, 6),
                p_under=round(p_under, 6),
                distribution="normal_cdf",
                sport=sport, stat_family=stat_family, line=line_f,
                selector_reason="normal_cdf explicit_sigma",
                mu=round(mu_f, 4),
                sigma=round(sigma_v, 4),
                cv=round(cv, 4) if cv is not None else None,
                sigma_source="explicit_empirical",
                effective_mu=round(mu_f, 4),
                mu_floor_applied=False,
                mu_floor_capped=False,
                cv_floor_applied=False,
                clamped=clamped,
            )

        # ---- σ resolution (CV-derived path) -----------------------------
        cv_floor_applied = False
        if cv is not None and not math.isnan(cv) and cv > 0:
            if cv >= cfg.cv_floor:
                cv_used = float(cv)
                sigma_source = "cv_derived_from_l10"
            else:
                cv_used = cfg.cv_floor
                sigma_source = "cv_floor"
                cv_floor_applied = True
        else:
            cv_used = cfg.cv_floor
            sigma_source = "stat_family_default"
            cv_floor_applied = True

        raw_mu = abs(mu_f)
        effective_mu = max(raw_mu, cfg.mu_floor)
        mu_floor_applied = effective_mu > raw_mu
        mu_floor_capped = False
        # The line-cap only fires when the μ-floor was binding. When
        # raw_mu already exceeds μ_floor (continuous high-volume stats
        # like Hits μ=0.78 vs line=0.5) we keep the natural σ scaling;
        # capping there would shrink σ below the true dispersion.
        if cfg.mu_floor_capped and mu_floor_applied:
            cap = abs(line_f)
            if effective_mu > cap:
                effective_mu = cap
                mu_floor_capped = True

        if mu_floor_applied:
            sigma_source = sigma_source + "+mu_floor"
        if mu_floor_capped:
            sigma_source = sigma_source + "+capped_at_line"

        sigma_v = max(cv_used * effective_mu, cfg.sigma_min_absolute)

        # ---- bare CDF ----------------------------------------------------
        z = (line_f - mu_f) / sigma_v
        p_under = _normal_cdf(z)
        p_over = 1.0 - p_under
        p_over, clamped = clamp_p(p_over)
        p_under = 1.0 - p_over

        reason = (
            f"normal_cdf cv_floor={cfg.cv_floor} mu_floor={cfg.mu_floor}"
            + (" capped" if cfg.mu_floor_capped else "")
        )

        return ProbabilityResult(
            p_over=round(p_over, 6),
            p_under=round(p_under, 6),
            distribution="normal_cdf",
            sport=sport,
            stat_family=stat_family,
            line=line_f,
            selector_reason=reason,
            mu=round(mu_f, 4),
            sigma=round(sigma_v, 4),
            cv=round(cv, 4) if cv is not None else None,
            sigma_source=sigma_source,
            effective_mu=round(effective_mu, 4),
            mu_floor_applied=mu_floor_applied,
            mu_floor_capped=mu_floor_capped,
            cv_floor_applied=cv_floor_applied,
            clamped=clamped,
        )


__all__ = ["NormalCDFConfig", "NormalCDFDistribution"]

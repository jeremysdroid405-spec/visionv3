"""
MLB stat-family calibration tables for the universal probability engine.

Selection rules per family:
- Continuous / high-volume counts  → Normal CDF (with μ-floor + line cap).
- Rare 0.5-line event families     → Poisson at line ≤ 0.5; Negative
                                     Binomial at higher lines (multi-event tails).
- Strict binary events             → Bernoulli (none today; reserved).

Floor values come from the 2024 MLB residual analysis already encoded
in the legacy `distribution_layer.py`; structure preserved so the
prior MU_FLOOR / CV_FLOOR semantics carry forward exactly.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..base import Distribution
from ..normal import NormalCDFDistribution, NormalCDFConfig
from ..bernoulli import BernoulliDistribution, BernoulliConfig
from ..poisson import PoissonDistribution, PoissonConfig
from ..negative_binomial import NegativeBinomialDistribution, NegativeBinomialConfig
from ..registry import FamilySpec


# ---------------------------------------------------------------------------
# Convenience constructors — keep the per-family table compact below.
# ---------------------------------------------------------------------------
def _normal(
    cv_floor: float, mu_floor: float, *,
    sigma_min: float = 0.20, capped: bool = True,
) -> NormalCDFDistribution:
    return NormalCDFDistribution(NormalCDFConfig(
        cv_floor=cv_floor, mu_floor=mu_floor,
        sigma_min_absolute=sigma_min,
        mu_floor_capped=capped,
    ))


def _poisson(lambda_min: float = 1e-3, lambda_max: float = 25.0) -> PoissonDistribution:
    return PoissonDistribution(PoissonConfig(
        lambda_min=lambda_min, lambda_max=lambda_max,
    ))


def _nb(cv_floor: float, lambda_min: float = 1e-3, lambda_max: float = 25.0) -> NegativeBinomialDistribution:
    return NegativeBinomialDistribution(NegativeBinomialConfig(
        cv_floor=cv_floor, lambda_min=lambda_min, lambda_max=lambda_max,
    ))


# ---------------------------------------------------------------------------
# Per-family selectors.
#
# Convention: 0.5-line means "did event happen at least once" → Poisson
# is the natural model. 1.5+ lines need a multi-event tail; we use NB
# with the player's CV to honour over-dispersion. Continuous-count
# families (Hits, Total Bases, HRR, Pitcher Strikeouts) stay on Normal
# CDF because Var ≈ Mean breaks down at the volumes involved.
# ---------------------------------------------------------------------------
def _rare_event_selector(
    line: float, mu: Optional[float], cv: Optional[float], extras: Optional[Dict[str, Any]],
) -> Distribution:
    """Used by HR / SB / Doubles / Triples — Poisson at 0.5, NB beyond."""
    try:
        ln = float(line)
    except (TypeError, ValueError):
        ln = 0.5
    if ln <= 0.5:
        return _poisson(lambda_min=1e-3, lambda_max=5.0)
    return _nb(cv_floor=0.80, lambda_min=1e-3, lambda_max=8.0)


def _runs_rbis_selector(
    line: float, mu: Optional[float], cv: Optional[float], extras: Optional[Dict[str, Any]],
) -> Distribution:
    """RBIs at 0.5 → Poisson (true event count). Multi-RBI lines → NB."""
    try:
        ln = float(line)
    except (TypeError, ValueError):
        ln = 0.5
    if ln <= 0.5:
        return _poisson(lambda_min=1e-3, lambda_max=5.0)
    return _nb(cv_floor=0.85, lambda_min=1e-3, lambda_max=8.0)


def _runs_selector(
    line: float, mu: Optional[float], cv: Optional[float], extras: Optional[Dict[str, Any]],
) -> Distribution:
    """Runs at 0.5 — Normal CDF with floor cap.
    Calibration showed Poisson hurt Runs because the HF model
    sometimes under-projects μ (0.02 for confirmed leadoff hitters);
    Normal's μ_floor lifts these props off the clamp where Poisson
    cannot.  Multi-Run lines (1.5+) → NB.
    """
    try:
        ln = float(line)
    except (TypeError, ValueError):
        ln = 0.5
    if ln <= 0.5:
        return _normal(cv_floor=0.85, mu_floor=0.5, capped=True)
    return _nb(cv_floor=0.85, lambda_min=1e-3, lambda_max=8.0)


# ---------------------------------------------------------------------------
# MLB family table.
# Keys are CANONICAL display tokens — registry normalises to lowercase
# and underscores them.
# ---------------------------------------------------------------------------
MLB_FAMILIES: Dict[str, FamilySpec] = {
    # ----- Continuous / high-volume batter stats ------------------------
    "Hits": FamilySpec(
        default=_normal(cv_floor=0.55, mu_floor=0.5, capped=True),
        notes="L20 hits is roughly continuous; μ_floor=0.5 with line cap "
              "stops σ collapse on 0.5 lines.",
    ),
    "Total Bases": FamilySpec(
        default=_normal(cv_floor=0.65, mu_floor=1.0, capped=True),
        notes="Lines mostly 1.5+; Normal CDF with μ_floor capped at line.",
    ),
    "Hits+Runs+RBIs": FamilySpec(
        default=_normal(cv_floor=0.55, mu_floor=0.5, capped=True),
        notes="Composite count; Normal CDF safe given Var/Mean ≈ 1.5-2.",
    ),
    "Singles": FamilySpec(
        default=_normal(cv_floor=0.65, mu_floor=0.5, capped=True),
        notes="Lines 0.5/1.5; Normal with capped μ_floor.",
    ),
    # ----- Rare batter event stats (Poisson@0.5 / NB above) -------------
    "Home Runs": FamilySpec(
        default=_poisson(lambda_min=1e-3, lambda_max=3.0),
        selector=_rare_event_selector,
        notes="HR is rare-event count; Poisson for 0.5, NB for 1.5+.",
    ),
    "Stolen Bases": FamilySpec(
        default=_poisson(lambda_min=1e-3, lambda_max=2.0),
        selector=_rare_event_selector,
        notes="SB rare; Poisson at 0.5.",
    ),
    "Doubles": FamilySpec(
        default=_poisson(lambda_min=1e-3, lambda_max=2.0),
        selector=_rare_event_selector,
        notes="Doubles rare on 0.5 lines.",
    ),
    "Triples": FamilySpec(
        default=_poisson(lambda_min=1e-4, lambda_max=1.0),
        selector=_rare_event_selector,
        notes="Triples extremely rare; Poisson dominates.",
    ),
    # ----- Bursty batter counts (RBIs / Runs at 0.5 → Poisson) ----------
    "RBIs": FamilySpec(
        default=_poisson(lambda_min=1e-3, lambda_max=5.0),
        selector=_runs_rbis_selector,
        notes="RBIs at 0.5: Poisson; multi-RBI lines: NB.",
    ),
    "Runs": FamilySpec(
        default=_normal(cv_floor=0.85, mu_floor=0.5, capped=True),
        selector=_runs_selector,
        notes="Runs at 0.5: Normal CDF with capped μ_floor (HF model "
              "sometimes under-projects μ; Poisson has no floor "
              "fallback). Multi-Run lines → NB.",
    ),
    # ----- Pitcher families (continuous counts) -------------------------
    "Pitcher Strikeouts": FamilySpec(
        default=_normal(cv_floor=0.30, mu_floor=2.0, capped=False),
        notes="K count is roughly Normal at 4-12 K; μ_floor not capped "
              "(line ≥ 3.5 typical → cap meaningless).",
    ),
    "Pitcher Outs": FamilySpec(
        default=_normal(cv_floor=0.18, mu_floor=12.0, capped=False),
        notes="Outs count for starters; CV very tight (~0.18). HF model "
              "currently under-projects — separate issue.",
    ),
    "Earned Runs": FamilySpec(
        default=_normal(cv_floor=0.85, mu_floor=1.5, capped=True),
        notes="ER bursty; Normal with floor cap.",
    ),
    "Hits Allowed": FamilySpec(
        default=_normal(cv_floor=0.55, mu_floor=2.5, capped=False),
        notes="Hits allowed roughly Normal; lines 5-9 typical.",
    ),
    "Walks Allowed": FamilySpec(
        default=_normal(cv_floor=0.65, mu_floor=1.0, capped=True),
        notes="BB allowed; lines 1.5/2.5.",
    ),
    # ----- PrizePicks-only stat families (TP-blocked today) -------------
    "Batter Strikeouts": FamilySpec(
        default=_normal(cv_floor=0.45, mu_floor=0.5, capped=True),
        notes="PP-only family; once TP path opens up, audit this.",
    ),
    "Batter Walks": FamilySpec(
        default=_normal(cv_floor=0.85, mu_floor=0.5, capped=True),
        notes="PP-only family; once TP path opens up, audit this.",
    ),
}


__all__ = ["MLB_FAMILIES"]

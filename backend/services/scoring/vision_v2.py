"""
Vision Score v2 — MLR-Based, Directional, Independent Decision Engine
=====================================================================

A side-aware, component-driven confidence score derived from the MLR
scoring stack outputs. Replaces v1's "positive-edge × p_model × stability
× confidence × percentile" composite with an explainable, weighted sum
of seven directional components.

Critical guarantees:
  * SIDE-AWARE — `direction_margin` flips sign for OVER vs UNDER.
  * MODEL-DRIVEN — the dominant signal is `direction_strength`
    (sigmoid of direction_margin/sigma), NOT a percentile of vision_raw.
  * EXPLAINABLE — every component is persisted alongside the final score
    so we can audit why ANY pick scored what it scored.
  * COEXISTS WITH V1 — runs in parallel; never overwrites
    `vision_score`. New fields all live under the `vision_v2_*` namespace.

NOT changed by this module:
  * projection model
  * sigma / volatility model
  * TP calculation
  * edge_pct
  * gates / tier routing
  * frontend
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional

# Feature flag — read once at import time. The composer always computes
# v2 (so backtests / debug endpoints can read the score) but only
# downstream consumers gated on this flag will surface it to users.
import os
VISION_V2_ENABLED: bool = os.environ.get("VISION_V2_ENABLED", "false").lower() in ("1", "true", "yes")


# --------------------------------------------------------------------- #
# Default weights — exposed for tuning. Sum of positive weights = 1.00. #
# Volatility penalty subtracts AFTER the weighted sum (clamped 0..1).   #
# --------------------------------------------------------------------- #
DEFAULT_WEIGHTS: Dict[str, float] = {
    "probability":       0.30,
    "projection":        0.25,
    "edge":              0.20,
    "consistency":       0.10,
    "context":           0.10,
    "market_confidence": 0.05,
    # subtracted, not added:
    "volatility_penalty": 0.15,   # max amount this can shave
}


# --------------------------------------------------------------------- #
# Helpers — pure numeric, no I/O.                                        #
# --------------------------------------------------------------------- #
def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _num(v: Any) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) else None


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


# ===================================================================== #
# STEP 1 — Directional foundation                                        #
# ===================================================================== #
def compute_direction(
    *, projection: Optional[float], line: Optional[float],
    sigma: Optional[float], side: str,
) -> Dict[str, Optional[float]]:
    """direction_margin / direction_strength / direction_alignment.

    direction_margin (raw, signed):
        OVER  →  projection - line
        UNDER →  line - projection

    direction_strength (0-1, sigmoid):
        sigmoid(margin / max(sigma, 0.5))

    direction_alignment (-1..+1):
        2 * direction_strength - 1
        = +1 when model strongly agrees with the side
        = -1 when model strongly disagrees
        =  0 when projection sits exactly on the line
    """
    pj = _num(projection)
    ln = _num(line)
    sg = _num(sigma)
    side_norm = (side or "").upper()

    if pj is None or ln is None:
        return {"direction_margin": None,
                "direction_strength": None,
                "direction_alignment": None}

    margin = (pj - ln) if side_norm == "OVER" else (ln - pj)
    s = sg if (sg is not None and sg > 0) else 0.5  # avoid div/0
    strength = _sigmoid(margin / s)
    alignment = 2.0 * strength - 1.0
    return {
        "direction_margin":   round(margin, 4),
        "direction_strength": round(strength, 4),
        "direction_alignment": round(alignment, 4),
    }


# ===================================================================== #
# STEP 2 — Per-component 0..1 scores                                     #
# ===================================================================== #
def _probability_component(p: Optional[float]) -> float:
    """Calibrated against the 0.5 baseline. Random = 0; 100% conf = 1.

    Below 0.5 → score 0 (a sub-coinflip "pick" should not boost vision).
    """
    if p is None:
        return 0.0
    p = _clip01(float(p))
    if p <= 0.5:
        return 0.0
    return _clip01((p - 0.5) / 0.5)   # 0.5→0, 1.0→1


def _projection_component(direction_strength: Optional[float]) -> float:
    """Pulls direction_strength → 0..1. Acts as the model-vs-line
    separation signal: a projection 2σ above the line scores ~0.88."""
    if direction_strength is None:
        return 0.0
    return _clip01(float(direction_strength))


def _edge_component(edge_pct: Optional[float]) -> float:
    """Normalized vs a 0..30 pp band. Caps at 30 so a single huge-edge
    outlier can't dominate vision."""
    if edge_pct is None:
        return 0.0
    return _clip01(float(edge_pct) / 30.0)


def _consistency_component(cv: Optional[float],
                           hit_rate: Optional[float]) -> float:
    """Blend of inverse-CV and hit_rate.

    inverse_cv:   linear from CV=0 (1.0) to CV=1.0 (0.0)
    hr_component: linear from HR=50 (0) to HR=100 (1)
    """
    cv_score = 0.5
    if cv is not None:
        cv_score = _clip01(1.0 - float(cv))
    hr_score = 0.5
    if hit_rate is not None:
        hr_score = _clip01((float(hit_rate) - 50.0) / 50.0)
    return _clip01(0.5 * cv_score + 0.5 * hr_score)


def _context_component(*, injury_context: Optional[Any],
                       usage_spike: Optional[Any],
                       matchup_strength: Optional[float],
                       pace_factor: Optional[float],
                       side: str) -> float:
    """Combines injury / usage / matchup / pace into a 0..1 score.

    Each input is converted to a -1..+1 directional signal where +1
    favors the picked side. Missing inputs are 0 (neutral).
    """
    side_norm = (side or "").upper()
    sign = 1.0 if side_norm == "OVER" else -1.0

    # injury_context — heuristic: a positive `usage_vacuum_factor` on
    # the prop's team is OVER-favorable.
    inj = 0.0
    if isinstance(injury_context, dict):
        uv = injury_context.get("usage_vacuum_factor")
        if isinstance(uv, (int, float)):
            inj = _clip01((float(uv) - 1.0) / 0.5) * sign
    elif isinstance(injury_context, (int, float)):
        inj = _clip01((float(injury_context) - 1.0) / 0.5) * sign

    # usage_spike — boolean (True/False) or 0..1 magnitude
    sp = 0.0
    if isinstance(usage_spike, bool):
        sp = 0.5 * sign if usage_spike else 0.0
    elif isinstance(usage_spike, (int, float)):
        sp = _clip01(float(usage_spike)) * sign

    # matchup_strength — already side-aware in upstream, treat as 0..1
    ms = 0.0
    if isinstance(matchup_strength, (int, float)):
        ms = _clip01(float(matchup_strength) - 0.5) * 2.0 * sign

    # pace_factor — > 1 is OVER-favorable (more possessions / runs)
    pc = 0.0
    if isinstance(pace_factor, (int, float)):
        pc = _clip01((float(pace_factor) - 1.0) / 0.2) * sign

    # Combine → recenter into 0..1.
    raw = (inj + sp + ms + pc) / 4.0
    return _clip01(0.5 + 0.5 * raw)


def _market_confidence_component(*, books_count: Optional[int],
                                 tp_books_used: Optional[int],
                                 tp_source: Optional[str]) -> float:
    """Higher when more books are quoting + a real devig was possible.

    books_count       — 1 → 0.2, 2 → 0.5, 3+ → 1.0
    tp_books_used     — adds confidence when devig used ≥2 books
    tp_source         — "devig" full credit; "one_sided" half credit
    """
    bc = books_count or 0
    bc_score = {0: 0.0, 1: 0.2, 2: 0.5}.get(int(bc), 1.0)

    used = tp_books_used or 0
    used_score = _clip01(float(used) / 3.0)

    src = (tp_source or "").lower()
    src_score = 1.0 if src == "devig" else (0.5 if src == "one_sided" else 0.3)

    return _clip01(0.4 * bc_score + 0.4 * used_score + 0.2 * src_score)


def _volatility_penalty(*, cv: Optional[float],
                        hit_rate_sample_size: Optional[int]) -> float:
    """0..1 penalty (subtracted by `volatility_penalty` weight × this).

    cv > 0.55 starts penalizing; sample size < 10 also penalizes.
    """
    cv_pen = 0.0
    if cv is not None:
        cv_pen = _clip01((float(cv) - 0.55) / 0.45)   # 0.55→0, 1.0→1

    n = hit_rate_sample_size
    sample_pen = 0.0
    if n is not None and n < 10:
        sample_pen = _clip01((10 - int(n)) / 10.0)

    return _clip01(max(cv_pen, sample_pen))


# ===================================================================== #
# STEP 3 + 4 + 5 — Composite                                             #
# ===================================================================== #
def compute_vision_v2(
    *,
    side: str,
    projection: Optional[float],
    line: Optional[float],
    sigma: Optional[float],
    p_true_active: Optional[float],
    tp: Optional[float],
    edge_pct: Optional[float],
    cv: Optional[float],
    hit_rate: Optional[float],
    hit_rate_sample_size: Optional[int] = None,
    stat_family: Optional[str] = None,
    prop_type: Optional[str] = None,
    books_count: Optional[int] = None,
    tp_books_used: Optional[int] = None,
    tp_source: Optional[str] = None,
    injury_context: Optional[Any] = None,
    usage_spike: Optional[Any] = None,
    matchup_strength: Optional[float] = None,
    pace_factor: Optional[float] = None,
    weights: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Returns a dict with the final score plus every component for
    audit. Pure function — no I/O, fully deterministic.
    """
    w = dict(DEFAULT_WEIGHTS)
    if weights:
        w.update(weights)

    # Step 1 — directional foundation
    dr = compute_direction(projection=projection, line=line,
                           sigma=sigma, side=side)
    direction_strength = dr["direction_strength"]
    direction_alignment = dr["direction_alignment"]

    # Step 2 — components
    prob_c = _probability_component(p_true_active)
    proj_c = _projection_component(direction_strength)
    edge_c = _edge_component(edge_pct)
    cons_c = _consistency_component(cv, hit_rate)
    ctx_c  = _context_component(
        injury_context=injury_context, usage_spike=usage_spike,
        matchup_strength=matchup_strength, pace_factor=pace_factor,
        side=side,
    )
    mkt_c  = _market_confidence_component(
        books_count=books_count, tp_books_used=tp_books_used,
        tp_source=tp_source,
    )
    vol_p  = _volatility_penalty(cv=cv, hit_rate_sample_size=hit_rate_sample_size)

    # Step 3 — weighted sum
    raw = (
        w["probability"]       * prob_c
        + w["projection"]      * proj_c
        + w["edge"]            * edge_c
        + w["consistency"]     * cons_c
        + w["context"]         * ctx_c
        + w["market_confidence"] * mkt_c
    )
    # Step 4 — directional enforcement.  Asymmetric gate on
    # direction_strength:
    #   • alignment ≥ 0  (right side or on the line) → FULL credit
    #   • alignment < 0  (wrong side) → quadratic crush proportional
    #     to (2 × direction_strength)² — picks 1σ on the wrong side
    #     keep ≤ ~50% of their composite, picks 2σ wrong side keep
    #     ≤ ~10%, etc.
    # Multiplicative so a wrong-side pick cannot earn a high vision_v2
    # even with great component scores.
    ds = direction_strength if direction_strength is not None else 0.0
    if ds >= 0.5:
        dir_gate = 1.0
    else:
        dir_gate = (max(0.0, ds) * 2.0) ** 2
    raw_after_dir = raw * dir_gate

    # Volatility penalty
    raw_final = raw_after_dir - w["volatility_penalty"] * vol_p
    raw_final = _clip01(raw_final)

    # Step 5 — scale to 0..100
    vision_v2 = round(raw_final * 100.0, 2)

    return {
        # final
        "vision_score_v2":              vision_v2,
        "vision_v2_direction_margin":   dr["direction_margin"],
        "vision_v2_direction_strength": dr["direction_strength"],
        "vision_direction_alignment":   direction_alignment,
        # weighted components (already weighted, so they sum to vision_v2/100 + penalty)
        "vision_probability_component":       round(prob_c, 4),
        "vision_projection_component":        round(proj_c, 4),
        "vision_edge_component":              round(edge_c, 4),
        "vision_consistency_component":       round(cons_c, 4),
        "vision_context_component":           round(ctx_c, 4),
        "vision_market_confidence_component": round(mkt_c, 4),
        "vision_volatility_penalty":          round(vol_p, 4),
        "vision_v2_dir_gate":                 round(dir_gate, 4),
        "vision_v2_weights":                  w,
    }


__all__ = [
    "VISION_V2_ENABLED",
    "DEFAULT_WEIGHTS",
    "compute_direction",
    "compute_vision_v2",
]

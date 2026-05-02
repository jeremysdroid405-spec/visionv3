"""Phase 2 — Conditional (heteroscedastic) sigma lookup (SCAFFOLD).

Status: DESIGN DRAFT — not wired into scoring yet.
Phase 1 (additive μ debias) shipped 2026-05-02 in
`config/nba_projection_calibration.py`. This module is the next
step: replace the single-sigma-per-stat constant with a bucketed
lookup keyed on prop context.

Why we need it
--------------
Post-debias audit (2026-05-02):

    Stat  n    mean(z)   std(z)   |z|>2
    PRA   127  +0.003    0.897    0.8%   (σ slightly too wide — 0.9×)
    PTS   95   −0.003    0.830    1.1%   (σ too wide — 0.83×)
    REB   29   −0.009    1.248    10.3%  (σ too narrow, fat tails)
    AST   21   +0.000    1.160    4.8%   (σ slightly too narrow)

One σ per stat can't be right for all matchups. A slam-dunk matchup
needs a tight σ; a chaos game (minute volatility, blowout potential,
rotational instability) needs a wide σ.

Design
------
σ(prop) = base_σ[stat] × Π multiplier(feature_bucket)

Where multipliers are estimated from historical residuals grouped by
feature bucket, clipped to [0.5, 2.0] to prevent overfit.

Feature buckets (proposed)
--------------------------
1. minutes_bucket : (0-15, 15-22, 22-28, 28-34, 34+)
2. starter_flag   : (starter, bench)
3. dvp_rank       : (top5, top10, mid, bot10, bot5) by opponent def vs stat
4. line_bucket    : (low / mid / high) by stat-specific quartile
5. home_away      : (home, away)
6. rest_days      : (b2b, 1-day, 2+day)

Build procedure
---------------
1. Pull all settled residuals for the stat (last 90 days).
2. For each feature axis, compute std(residual) per bucket.
3. Multiplier = bucket_std / overall_std for that stat.
4. Clip to [0.5, 2.0], round to 2dp.
5. Persist to a YAML or Mongo collection so values are hot-reloadable.

Open questions
--------------
- Cross-feature interaction (e.g. starter × DvP) — additive multiplier
  product is the v1 simplification. If post-V1 audits show systematic
  miscalibration, upgrade to a small gradient-boost-on-residuals model.
- REB-specific: 10.3% |z|>2 tail hints at bimodal distribution
  (normal-rotation vs usage-spike nights). May need a mixture model
  rather than a single Gaussian.

DO NOT ship this until the lookup tables are built from real data.
A placeholder dict of 1.0s would be a pure no-op — fine — but any
invented multipliers will silently corrupt `p_model`.
"""
from typing import Dict, Optional, Tuple


# Base sigmas (from VK model test RMSE) — kept as the anchor; multipliers
# in MULTIPLIER_TABLES scale THESE values.
BASE_SIGMAS: Dict[str, float] = {
    "PTS":  5.80,
    "PRA":  7.92,
    "REB":  2.41,
    "AST":  1.70,
    "3PM":  1.10,
}


# Placeholder — all 1.0 until real tables are built.
# Structure: { stat : { feature : { bucket : multiplier } } }
MULTIPLIER_TABLES: Dict[str, Dict[str, Dict[str, float]]] = {
    # Populate via `scripts/build_nba_sigma_buckets.py` — TO BUILD.
}


def sigma_for_prop(
    stat_key: Optional[str],
    minutes_bucket: Optional[str] = None,
    starter_flag: Optional[str] = None,
    dvp_rank: Optional[str] = None,
    line_bucket: Optional[str] = None,
    home_away: Optional[str] = None,
    rest_days: Optional[str] = None,
) -> Tuple[Optional[float], Dict[str, float]]:
    """Heteroscedastic sigma lookup — v0 stub.

    Returns ``(sigma, multipliers_applied)``. Until MULTIPLIER_TABLES
    is populated, returns base sigma unchanged.
    """
    if stat_key is None:
        return None, {}
    key = str(stat_key).upper()
    base = BASE_SIGMAS.get(key)
    if base is None:
        return None, {}
    mults: Dict[str, float] = {}
    feature_values = {
        "minutes_bucket": minutes_bucket,
        "starter_flag":   starter_flag,
        "dvp_rank":       dvp_rank,
        "line_bucket":    line_bucket,
        "home_away":      home_away,
        "rest_days":      rest_days,
    }
    for feature, value in feature_values.items():
        if value is None:
            continue
        table = MULTIPLIER_TABLES.get(key, {}).get(feature, {})
        mult = table.get(value)
        if mult is not None:
            mults[feature] = float(mult)
    if not mults:
        return base, {}
    total_mult = 1.0
    for m in mults.values():
        total_mult *= m
    # Safety clip — one feature can at most double or halve σ; combined
    # features capped at [0.4, 2.5]× of base.
    total_mult = max(0.4, min(2.5, total_mult))
    return float(base) * total_mult, mults

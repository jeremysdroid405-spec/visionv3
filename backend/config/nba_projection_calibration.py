"""NBA Projection Calibration — per-stat additive debias.

Source: `/app/backend/forward_test_outcomes` audit 2026-05-02.
Sample: 272 settled OVER picks (tier-qualified), all stats.

Mean residual per stat (actual − projection):

    Stat   n     mean(actual-proj)   median
    PRA   127         −3.774         −5.200
    PTS    95         −3.418         −3.700
    REB    29         −1.421         −1.900
    AST    21         −1.200         −2.400

Every stat shows the model is over-projecting. Applying an additive
debias (subtract the mean residual from projection) brings the empirical
bias to ~0 on the same sample by construction, and cuts edge_pct
overstatement roughly in half overnight.

Caveats (important — read before editing these values):

1. Sample is of QUALIFIED picks only (tier ∈ {SH, FL, WZ} at the time
   of capture). The bias measured is the bias on the subset the model
   gets graded on, which is what we care about — but it is NOT the
   bias on a random sample of all props.
2. REB and AST samples are small (29, 21). Confidence intervals are
   wide. Consider these provisional until more settled picks accumulate.
3. When a new NBA model is trained, RE-RUN the audit and update these
   values. Do NOT keep applying a legacy debias to a re-calibrated model.
4. Debias is applied to the FINAL projection (after all recency / rate
   / availability layers). Subtracting before those layers would compound.

Values are intentionally mean (not median) — mean minimizes MSE and is
less aggressive than the right-skewed medians. Swap to median if the
post-debias audit still shows bias.
"""
from typing import Dict, Optional

# Per-stat additive debias — subtract from projection.
# Keys match `model_key` in `nba_scoring.py` (uppercase).
NBA_PROJECTION_DEBIAS: Dict[str, float] = {
    "PTS":  3.4,    # mean 3.418, round to 1dp for stability
    "PRA":  3.8,    # mean 3.774
    "REB":  1.4,    # mean 1.421  — small-sample
    "AST":  1.2,    # mean 1.200  — small-sample
    "3PM":  0.0,    # no settled data yet → no debias
}

# Per-stat sample size at time of fit. Surfaced on the score doc so
# operators can see when a debias is sample-thin.
NBA_DEBIAS_SAMPLE_SIZE: Dict[str, int] = {
    "PTS":  95,
    "PRA":  127,
    "REB":  29,
    "AST":  21,
    "3PM":  0,
}


def apply_debias(stat_key: Optional[str],
                 projection: Optional[float]) -> "tuple[Optional[float], Optional[float]]":
    """Apply additive debias to a projection.

    Returns
    -------
    (debiased_projection, debias_applied)
        debiased_projection : projection − debias  (or the original
            projection if stat_key / projection is None, or the stat
            has no debias entry).
        debias_applied      : the amount subtracted (0.0 when no-op),
            so callers can persist it on the score doc for auditability.
    """
    if stat_key is None or projection is None:
        return projection, None
    key = str(stat_key).upper()
    debias = NBA_PROJECTION_DEBIAS.get(key)
    if debias is None or debias == 0.0:
        return projection, 0.0
    return float(projection) - float(debias), float(debias)

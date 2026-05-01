"""Bayesian shrinkage for Statcast rate features.

WHY THIS EXISTS
---------------
On 2026-04-30 we discovered the HF MLB model produced wildly inflated
projections (Bleday: μ=6.74 vs L20-mean=2.0 — 3.4× off) for players
with tiny rolling Statcast windows. Root cause:

    Bleday rolling_7 had 2 PA, 1 batted-ball event:
        wOBA          = 1.35   (one HR-weighted PA averaged → impossible)
        barrel_rate   = 1.0    (1 barrel out of 1 BB = 100%)
        hard_hit_rate = 1.0
        xwOBA         = 1.15

These are **mathematically impossible** values (wOBA caps at ~0.6 for
elite hitters, barrel_rate caps at ~25% for MLB leaders). They occur
ONLY in tiny-sample rolling windows where 1 outlier event dominates
the mean. The model was trained on healthy averages — feeding it
extreme out-of-distribution values caused the trees to extrapolate
unpredictably.

THE FIX
-------
Before computing any rate `X / N`, shrink the count toward the league
average using a Bayesian-style prior:

        shrunk_rate = (X + league_avg * prior_n) / (N + prior_n)

When N is large, `shrunk_rate ≈ X/N` (the data dominates).
When N is small, `shrunk_rate ≈ league_avg` (the prior dominates).
The transition is smooth and continuous — no hard cliffs.

This is applied at BOTH training time and inference time so the
features the model sees are always on the same scale and bounded
within the realistic MLB distribution.

WHAT THIS MODULE PROVIDES
-------------------------
    * `LEAGUE_AVERAGES`: production-grade constants sourced from
      public MLB averages (documented per source in the constant).
    * `shrink_rate(observed, n_observed, league_avg, prior_n)`:
      core helper; pure function, fully unit-testable.
    * `bayes_shrink_rolling_window(window_dict)`: takes a
      Statcast rolling-window dict (the shape stored in
      `mlb_statcast_player_features`) and returns the same shape
      with every rate field shrunk.

INVARIANTS (locked in via tests)
--------------------------------
INV-BS1: At N=0, shrunk = league_avg exactly.
INV-BS2: At N=∞, shrunk → observed_rate (limit).
INV-BS3: Shrunk rate is bounded: min(observed, league_avg) - eps
         ≤ shrunk ≤ max(observed, league_avg) + eps.
INV-BS4: For Bleday's actual data (N=1, observed=1.0 barrel_rate),
         shrunk barrel_rate ≤ 0.25 (within MLB realistic range).
INV-BS5: Calling shrink with `prior_n=0` returns the raw observed
         rate (escape hatch — tests can disable shrinkage).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# ─── League-average constants ────────────────────────────────────────
# Sources & rationale per field. These are the priors the model
# shrinks toward when sample size is small. Rebuilt every off-season.
#
# Sources:
#   Baseball Savant Leaderboards (2024 full season MLB averages):
#     https://baseballsavant.mlb.com/leaderboard/expected_statistics
#   FanGraphs Standard MLB Averages:
#     https://library.fangraphs.com/offense/woba/
#
# All values are 2024 MLB AVERAGES (not medians or weighted means)
# for the simplest, most explainable prior.
LEAGUE_AVERAGES: Dict[str, float] = {
    # Plate-discipline / contact rates (per swing or per PA)
    "k_rate":            0.225,   # 22.5% K rate (2024 league)
    "whiff_rate":        0.245,   # 24.5% whiffs per swing
    "contact_rate":      0.755,   # 75.5% contact per swing
    # Batted-ball quality (per batted-ball event, NOT per PA)
    "barrel_rate":       0.080,   # 8.0% barrels per BBE
    "hard_hit_rate":     0.395,   # 39.5% hard-hit (≥95mph) per BBE
    "sweet_spot_rate":   0.330,   # 33.0% sweet-spot (8-32° LA) per BBE
    # Outcome metrics — wOBA-family (per PA, batted-ball weighted)
    "wOBA":              0.315,   # 2024 league wOBA
    "xwOBA":             0.315,   # league xwOBA tracks wOBA closely
    # Velocity / launch angle (per BBE) — these are means, not rates,
    # but the same shrinkage formula applies (with N=BBE).
    "avg_exit_velocity": 89.0,    # mph
    "avg_launch_angle":  12.0,    # degrees
    # Pitching mirrors (used by pitcher features). Mostly mirrors of
    # the above with a flip — pitchers ALLOW these.
    "k_pct_against":     0.225,
    "bb_pct_against":    0.085,
    "barrel_pct_against": 0.080,
    "hard_hit_pct_against": 0.395,
}

# Per-rate prior strength. A rate with `prior_n=30` says "treat
# observed data as combined with 30 PAs of league-average evidence."
# Larger prior_n = stronger shrinkage; smaller = weaker.
#
# Tuning rationale: 30 PAs ≈ 1 week of starter playing time. Tight
# enough that a player with 100+ PAs in the window shows mostly their
# own rate, loose enough that 1-2 PA windows are dominated by the
# league average — exactly the behavior we want.
DEFAULT_PRIOR_N: Dict[str, int] = {
    # PA-denominated rates — moderate prior since PA samples are
    # small relative to season-totals (a 7-day window has ~25-30 PAs).
    "k_rate":            30,
    "whiff_rate":        30,
    "contact_rate":      30,
    "wOBA":              30,
    "xwOBA":             30,
    # BBE-denominated rates — STRONGER prior because BBE samples
    # are ~3-4× smaller than PA (most PAs don't end in a batted ball).
    # Without a stronger prior, a player with 1 BBE in the window
    # would still get ~50% weight on that single observation.
    "barrel_rate":       15,
    "hard_hit_rate":     15,
    "sweet_spot_rate":   15,
    "avg_exit_velocity": 15,
    "avg_launch_angle":  15,
    # Pitching mirrors (use against-the-pitcher PA samples)
    "k_pct_against":     30,
    "bb_pct_against":    30,
    "barrel_pct_against": 15,
    "hard_hit_pct_against": 15,
}


# ─── Core shrinkage formula ──────────────────────────────────────────
def shrink_rate(
    observed_rate: Optional[float],
    n_observed: Optional[int],
    league_avg: float,
    prior_n: int,
) -> float:
    """Bayesian shrinkage of an observed rate toward a league prior.

    Math:
        shrunk = (observed * n_observed + league_avg * prior_n)
                 / (n_observed + prior_n)

    This is equivalent to a Beta(α=league_avg×prior_n, β=...) prior
    on a binomial observation, but is general enough to apply to
    any rate (per-PA, per-BBE, per-game) by choosing prior_n
    appropriately.

    Edge cases:
      * `n_observed` None or 0 → returns `league_avg`
      * `observed_rate` None → treat as 0.0 (no observed events
        → all weight on prior, which collapses to league_avg).
      * `prior_n=0` → escape hatch, returns raw observed_rate
        (or league_avg if observed is None/n=0).
    """
    if prior_n == 0:
        if observed_rate is None or n_observed in (None, 0):
            return league_avg
        return float(observed_rate)
    if n_observed in (None, 0):
        return float(league_avg)
    if observed_rate is None:
        observed_rate = 0.0
    n = float(n_observed)
    p = float(prior_n)
    return (observed_rate * n + league_avg * p) / (n + p)


def bayes_shrink_rolling_window(
    window: Dict[str, Any],
    *,
    pa_field: str = "plate_appearances",
    bb_field: str = "batted_ball_events",
    custom_prior_n: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """Shrink every rate in a rolling-window dict toward league avg.

    Input shape (from `mlb_statcast_player_features.rolling_*`):
        {
          "plate_appearances": int,
          "batted_ball_events": int,
          "wOBA": float, "xwOBA": float,
          "k_rate": float, "whiff_rate": float, "contact_rate": float,
          "barrel_rate": float, "hard_hit_rate": float,
          "sweet_spot_rate": float,
          "avg_exit_velocity": float, "avg_launch_angle": float,
        }

    Output shape: same dict with every rate replaced by its shrunk
    counterpart. Sample-size fields (`plate_appearances`,
    `batted_ball_events`) are passed through unchanged.

    The denominator (PA or BBE) is selected per-feature based on
    Statcast convention:
      * PA-denominated: k_rate, whiff_rate, contact_rate, wOBA, xwOBA
      * BBE-denominated: barrel_rate, hard_hit_rate, sweet_spot_rate,
                         avg_exit_velocity, avg_launch_angle
    """
    if not window:
        return window or {}

    pa_n = window.get(pa_field) or 0
    bb_n = window.get(bb_field) or 0
    priors = {**DEFAULT_PRIOR_N, **(custom_prior_n or {})}

    # Map field name → which sample-size to use as the denominator.
    PA_FIELDS = (
        "k_rate", "whiff_rate", "contact_rate",
        "wOBA", "xwOBA",
        "k_pct_against", "bb_pct_against",
    )
    BBE_FIELDS = (
        "barrel_rate", "hard_hit_rate", "sweet_spot_rate",
        "avg_exit_velocity", "avg_launch_angle",
        "barrel_pct_against", "hard_hit_pct_against",
    )

    out = dict(window)
    for f, n in [(f, pa_n) for f in PA_FIELDS] + \
                [(f, bb_n) for f in BBE_FIELDS]:
        if f not in out and f not in LEAGUE_AVERAGES:
            continue
        if f not in LEAGUE_AVERAGES:
            continue
        out[f] = shrink_rate(
            observed_rate=out.get(f),
            n_observed=n,
            league_avg=LEAGUE_AVERAGES[f],
            prior_n=priors.get(f, 30),
        )
    return out


__all__ = [
    "LEAGUE_AVERAGES",
    "DEFAULT_PRIOR_N",
    "shrink_rate",
    "bayes_shrink_rolling_window",
]

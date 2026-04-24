# MLB Projection Residual Audit — Rare-Event Stats
Generated: 2026-04-24 03:43:04 UTC  •  source: `mlb_prop_scores@final-mlb-rt` (live projections) × `mlb_master_hub_2026.bdl_game_logs` (actuals, batter-ABs only)

Read-only diagnosis. No model changes, no caps, no ECDF tweaks.
Compares the HF model's live-board projections to the actual historical per-game distribution for the same player pool.

## 1. Live projection vs historical actual distribution

### `home_runs`

- **Live projections**: n = **76**  mean = **0.233**  median = 0.115  p10/p50/p90 = 0.035 / 0.115 / 0.790  max = 1.490
- **Historical actuals** (batter-AB games only): n = 5,132  mean = **0.118**  median = 0.000  zero-rate = **0.890**  over-1 rate = 0.008  over-2 rate = 0.000  max = 3.000
- **Mean projection bias**: proj − actual = **+0.115**  (+97.1% vs actual mean)
- **Projection tail**: P(proj>1) = 0.013  P(proj>1.5) = 0.000  P(proj>2) = 0.000
- **Actual tail**:     P(actual>1) = 0.008  P(actual>1.5) = 0.008  P(actual>2) = 0.000

Projection histogram (all live projections):

| proj bucket | count | share |
|---|---:|---:|
| 0-0.25 | 54 | 75.0% |
| 0.25-0.5 | 5 | 6.9% |
| 0.5-0.75 | 3 | 4.2% |
| 0.75-1 | 9 | 12.5% |
| 1-1.5 | 1 | 1.4% |
| 1.5-2 | 0 | 0.0% |
| 2-3 | 0 | 0.0% |
| 3+ | 0 | 0.0% |

Actual outcome histogram (batter-AB games only):

| actual | count | share |
|---|---:|---:|
| 0 | 4,566 | 89.0% |
| 1 | 527 | 10.3% |
| 2 | 38 | 0.7% |
| 3 | 1 | 0.0% |
| 4+ | 0 | 0.0% |

### `rbis`

- **Live projections**: n = **249**  mean = **0.665**  median = 0.540  p10/p50/p90 = 0.258 / 0.540 / 1.198  max = 3.040
- **Historical actuals** (batter-AB games only): n = 5,132  mean = **0.450**  median = 0.000  zero-rate = **0.708**  over-1 rate = 0.108  over-2 rate = 0.036  max = 5.000
- **Mean projection bias**: proj − actual = **+0.215**  (+47.7% vs actual mean)
- **Projection tail**: P(proj>1) = 0.145  P(proj>1.5) = 0.072  P(proj>2) = 0.052
- **Actual tail**:     P(actual>1) = 0.108  P(actual>1.5) = 0.108  P(actual>2) = 0.036

Projection histogram (all live projections):

| proj bucket | count | share |
|---|---:|---:|
| 0-0.25 | 22 | 8.8% |
| 0.25-0.5 | 91 | 36.5% |
| 0.5-0.75 | 70 | 28.1% |
| 0.75-1 | 30 | 12.0% |
| 1-1.5 | 18 | 7.2% |
| 1.5-2 | 5 | 2.0% |
| 2-3 | 12 | 4.8% |
| 3+ | 1 | 0.4% |

Actual outcome histogram (batter-AB games only):

| actual | count | share |
|---|---:|---:|
| 0 | 3,632 | 70.8% |
| 1 | 946 | 18.4% |
| 2 | 371 | 7.2% |
| 3 | 125 | 2.4% |
| 4+ | 58 | 1.1% |

### `total_bases`

- **Live projections**: n = **474**  mean = **1.676**  median = 1.330  p10/p50/p90 = 0.640 / 1.330 / 3.340  max = 6.880
- **Historical actuals** (batter-AB games only): n = 5,104  mean = **1.345**  median = 1.000  zero-rate = **0.419**  over-1 rate = 0.325  over-2 rate = 0.188  max = 13.000
- **Mean projection bias**: proj − actual = **+0.331**  (+24.6% vs actual mean)
- **Projection tail**: P(proj>1) = 0.679  P(proj>1.5) = 0.428  P(proj>2) = 0.283
- **Actual tail**:     P(actual>1) = 0.325  P(actual>1.5) = 0.325  P(actual>2) = 0.188

Projection histogram (all live projections):

| proj bucket | count | share |
|---|---:|---:|
| 0-0.25 | 10 | 2.1% |
| 0.25-0.5 | 17 | 3.6% |
| 0.5-0.75 | 52 | 11.0% |
| 0.75-1 | 67 | 14.2% |
| 1-1.5 | 122 | 25.8% |
| 1.5-2 | 71 | 15.0% |
| 2-3 | 61 | 12.9% |
| 3+ | 73 | 15.4% |

Actual outcome histogram (batter-AB games only):

| actual | count | share |
|---|---:|---:|
| 0 | 2,139 | 41.9% |
| 1 | 1,306 | 25.6% |
| 2 | 701 | 13.7% |
| 3 | 278 | 5.4% |
| 4+ | 680 | 13.3% |

### `hits+runs+rbis`

- **Live projections**: n = **611**  mean = **1.937**  median = 1.850  p10/p50/p90 = 1.030 / 1.850 / 2.940  max = 5.310
- **Historical actuals** (batter-AB games only): n = 5,132  mean = **1.696**  median = 1.000  zero-rate = **0.349**  over-1 rate = 0.429  over-2 rate = 0.275  max = 12.000
- **Mean projection bias**: proj − actual = **+0.241**  (+14.2% vs actual mean)
- **Projection tail**: P(proj>1) = 0.907  P(proj>1.5) = 0.678  P(proj>2) = 0.429
- **Actual tail**:     P(actual>1) = 0.429  P(actual>1.5) = 0.429  P(actual>2) = 0.275

Projection histogram (all live projections):

| proj bucket | count | share |
|---|---:|---:|
| 0-0.25 | 1 | 0.2% |
| 0.25-0.5 | 7 | 1.1% |
| 0.5-0.75 | 12 | 2.0% |
| 0.75-1 | 37 | 6.1% |
| 1-1.5 | 137 | 22.4% |
| 1.5-2 | 153 | 25.0% |
| 2-3 | 208 | 34.0% |
| 3+ | 56 | 9.2% |

Actual outcome histogram (batter-AB games only):

| actual | count | share |
|---|---:|---:|
| 0 | 1,792 | 34.9% |
| 1 | 1,136 | 22.1% |
| 2 | 794 | 15.5% |
| 3 | 588 | 11.5% |
| 4+ | 822 | 16.0% |

## 2. Structural probe per stat

Each row compares the **live projection** of each player to that player's own **career mean** (from their historical game log) and historical **zero-rate** (fraction of batter games where the stat was 0). Corr_L5 measures whether projections track a player's last-5-games deviation from career mean — a proxy for recency-bias.

| stat | players | career mean (player-avg) | player zero-rate (avg) | mean(proj − career) | median(proj − career) | corr(L5-deviation, proj-deviation) |
|------|--------:|-------------------------:|-----------------------:|--------------------:|----------------------:|-----------------------------------:|
| `home_runs` | 76 | 0.158 | 0.853 | **0.074** | -0.039 | -0.080 |
| `rbis` | 249 | 0.454 | 0.703 | **0.211** | 0.065 | 0.043 |
| `total_bases` | 474 | 1.349 | 0.418 | **0.327** | 0.084 | -0.040 |
| `hits+runs+rbis` | 611 | 1.679 | 0.351 | **0.258** | 0.199 | 0.048 |

## 3. Failure-mode checklist (per stat)

Symptoms read from the numbers above:

### `home_runs`

- ❌ **Over-projects vs population mean**: proj_mean = 0.233 vs actual_mean = 0.118  (bias = +0.115, +97.1%)
- ✅ Low recency bias: corr = -0.080

### `rbis`

- ❌ **Over-projects vs population mean**: proj_mean = 0.665 vs actual_mean = 0.450  (bias = +0.215, +47.7%)
- ❌ **Per-player inflation**: on average each player's projection sits +0.211 above their own career mean — model regresses UP toward league rate rather than DOWN toward personal rate
- ❌ **Discrete-event blindness**: median actual = 0 (majority of batter games produce 0 for this stat), yet median projection = 0.54. Model treats the count as continuous and smears probability mass across a space that's ~71% zeros
- ✅ Low recency bias: corr = 0.043

### `total_bases`

- ❌ **Over-projects vs population mean**: proj_mean = 1.676 vs actual_mean = 1.345  (bias = +0.331, +24.6%)
- ❌ **Per-player inflation**: on average each player's projection sits +0.327 above their own career mean — model regresses UP toward league rate rather than DOWN toward personal rate
- ✅ Low recency bias: corr = -0.040

### `hits+runs+rbis`

- ✅ Population mean aligns with actuals (bias = +0.241).
- ❌ **Per-player inflation**: on average each player's projection sits +0.258 above their own career mean — model regresses UP toward league rate rather than DOWN toward personal rate
- ✅ Low recency bias: corr = 0.048

## 4. Observations + hypothesis (read-only)

Combining the bias, tail overshoot, per-player inflation, discrete-event blindness, and recency-bias signals above gives the following hypothesis for WHY the projections are wrong — none of which require touching the ECDF layer:

1. **Base rate is not being respected on zero-heavy stats.** If actual `P(actual = 0) > 70%` for a stat like `home_runs` or `rbis` but the model's median projection is well above zero, the XGBoost regression head is treating the count as a continuous quantity and smearing probability mass across a range that is *dominated* by zeros in reality. A regression loss (MSE) trained on a distribution with mode=0 and a long right tail will systematically overshoot the mode.

2. **Park factor + opponent-K-rate multipliers compound the signal multiplicatively.** Every projection is `raw_pred × park_factor × opp_k_rate`. For a hitter like Brandon Marsh in a hitter-friendly park (`COL` → HR×1.32) with a K-prone opponent (`ARI` → K×1.14), the multipliers stack. The raw predict may be reasonable, but the post-multiplier pushes rare-event tails past physical limits.

3. **Volatility floor inflates sigma but not projection.** The model applies `std_dev = l10_avg * 0.35` when CV < 0.35 on rare events. This widens the Gaussian probability curve (and is what caused the Gaussian OVER-gate false triggers the ECDF cutover fixed). Not a projection bug per se, but worth flagging since the floor interacts with the projection.

4. **No shrinkage toward player career rate.** If `mean(proj − career_rate)` above is meaningfully positive, the model is pulling projections UPWARD away from each player's personal baseline — the opposite of what Bayesian shrinkage would do. Combined with the multiplicative park/opp factors this creates the projection outliers seen on the live board.

5. **Recent-hot-streak echo.** A high `corr(L5-hot, proj-deviation)` indicates the model is reading a few recent good games as signal rather than regression-to-mean noise.

No projection model change applied. This report is diagnostic only, per user instruction.
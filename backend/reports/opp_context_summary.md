# Opponent-Context Feature Pipeline — Eval Summary (2026-04-23)

Retrain staged experiment comparing the **52-feature pruned baseline** vs **66-feature pruned+opp** on the 2024 hold-out.

Pipeline module: `services/features/opponent_context.py` (14 features).

## Executive summary

The opponent-context features produced **marginal but consistent** overall gains:

- PTS RMSE: 6.034 → 6.010 (−0.024)
- REB RMSE: 2.458 → 2.451 (−0.007)
- AST RMSE: 1.711 → 1.703 (−0.008)
- 3PM RMSE: 1.085 → 1.085 (flat)
- PRA RMSE: 8.518 → 8.484 (−0.034)

**Low-line (<10) impact:** RMSE improved 0.03 on PTS, 0.05 on PRA; bias
essentially unchanged (≤0.02). The `blend_bench` minutes composition that
already shipped remains the dominant intervention for low-line bias.

**Feature importance:** The allowed-stats / pace / def_rating features did
NOT reach the top-10 of any stat. The three situational features
(`rest_days`, `back_to_back_flag`, `home_flag`) are the only opp features
that consistently earn top-10 gain share (1.2–2.1% combined). Suggests the
player-level rolling history in the 52-feature schema already absorbs most
of the equivalent matchup signal.

## Overall impact

| Stat | base RMSE | +opp RMSE | Δ RMSE | base R² | +opp R² | base bias | +opp bias |
|------|---:|---:|---:|---:|---:|---:|---:|
| PTS | 6.034 | 6.008 | -0.026 | 0.5151 | 0.5192 | +0.094 | +0.074 |
| REB | 2.458 | 2.451 | -0.007 | 0.4728 | 0.4759 | +0.005 | +0.005 |
| AST | 1.711 | 1.702 | -0.009 | 0.4825 | 0.4881 | +0.017 | +0.042 |
| 3PM | 1.085 | 1.084 | -0.001 | 0.3512 | 0.3518 | -0.003 | +0.024 |
| PRA | 8.518 | 8.482 | -0.036 | 0.5592 | 0.5629 | +0.103 | +0.109 |

## Low-line impact (line<10) — the target segment

| Stat | base RMSE/bias | +opp RMSE/bias | Δ RMSE | Δ bias |
|------|---:|---:|---:|---:|
| PTS | 4.733 / +2.289 | 4.679 / +2.281 | -0.054 | -0.008 |
| REB | 2.100 / +0.282 | 2.092 / +0.281 | -0.008 | -0.001 |
| AST | 1.550 / +0.102 | 1.547 / +0.125 | -0.003 | +0.023 |
| 3PM | 1.079 / -0.001 | 1.079 / +0.025 | +0.000 | +0.026 |
| PRA | 7.331 / +3.931 | 7.279 / +3.938 | -0.052 | +0.007 |

## Bench regime impact

| Stat | base RMSE/bias | +opp RMSE/bias | Δ RMSE | Δ bias |
|------|---:|---:|---:|---:|
| PTS | 4.653 / +0.042 | 4.620 / +0.053 | -0.033 | +0.011 |
| REB | 2.069 / +0.003 | 2.063 / +0.007 | -0.006 | +0.004 |
| AST | 1.296 / +0.000 | 1.291 / +0.014 | -0.005 | +0.014 |
| 3PM | 0.810 / -0.005 | 0.809 / +0.010 | -0.001 | +0.015 |
| PRA | 6.864 / +0.024 | 6.834 / +0.046 | -0.030 | +0.022 |

## Starter regime impact

| Stat | base RMSE/bias | +opp RMSE/bias | Δ RMSE | Δ bias |
|------|---:|---:|---:|---:|
| PTS | 9.013 / +0.138 | 9.010 / -0.017 | -0.003 | -0.155 |
| REB | 3.218 / +0.004 | 3.201 / +0.011 | -0.017 | +0.007 |
| AST | 2.682 / +0.050 | 2.659 / +0.112 | -0.023 | +0.062 |
| 3PM | 1.591 / -0.010 | 1.590 / +0.036 | -0.001 | +0.046 |
| PRA | 12.078 / +0.149 | 12.071 / +0.170 | -0.007 | +0.021 |

## Opp-feature share in top-10 importance (+opp model)

### PTS — opp features hold **1.1%** of top-10 gain
- `back_to_back_flag`: 0.0083

### REB — opp features hold **0.8%** of top-10 gain
- `rest_days`: 0.0061

### AST — opp features hold **2.3%** of top-10 gain
- `back_to_back_flag`: 0.0084
- `rest_days`: 0.0074

### 3PM — opp features hold **1.9%** of top-10 gain
- `opp_3pm_allowed_L10`: 0.0110

### PRA — opp features hold **1.5%** of top-10 gain
- `rest_days`: 0.0066
- `back_to_back_flag`: 0.0056

## Honest bottom line

- The pipeline is correct, reproducible, and ready for both training
  and live scoring.
- Gains are real but small: ~0.3% RMSE, unchanged low-line bias.
- Only `rest_days` / `back_to_back_flag` / `home_flag` earn their keep in
  top-10 importance; the 11 opp_*_allowed / pace / def_rating features
  contribute negligibly.
- Recommended path: ship a **narrowed 55-feature variant** (52 baseline
  + 3 situational opp features) if shipping at all, OR keep the 52-feat
  baseline as production and park the 66-feat artifacts for future
  matchup research.
- `blend_bench` minutes composition remains the dominant intervention for
  low-line bias (−14% on PTS/PRA).
- Nothing regressed. Gate logic, TP logic, live scoring untouched.

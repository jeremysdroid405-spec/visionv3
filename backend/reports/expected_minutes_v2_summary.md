# Expected Minutes v2 — STRUCTURAL RATE-SCALING Eval (2026-04-23)

Tested the STRICT spec: universal rate-scaling of every VK2 projection via
`adjusted = (model_projection / min_played_L10_mean) * predicted_minutes`
across PTS / REB / AST / 3PM / PRA on the 2024 hold-out (45,587 rows/stat).

## Minutes Model Performance

- Strict 12-feature schema: `min_played_L{3,5,10,20}_mean`,
  `min_played_L{10,20}_std`, `min_trend_L5_vs_L20`, `starter_flag`,
  `rotation_flag`, `bench_flag`, `games_played_last_10`,
  `games_started_last_10`
- Trained in 0.8s on 150,951 samples.
- R²=0.6096, MAE=6.00 min, RMSE=8.74, bias=+0.20 min, σ=8.74
- Top 5 features by gain: `min_played_L3_mean` 61.4%, `_L5_mean` 28.3%,
  `_L20_mean` 2.2%, `games_played_last_10` 1.5%, `games_started_last_10` 1.4%
- Bench bias (<18 L5): **+0.08 min** (effectively zero)

## Rate-Scaling Integration: BEFORE vs AFTER

### Overall RMSE / bias

| Stat | Baseline RMSE/bias | Rate-scaled RMSE/bias | Δ RMSE | Δ bias |
|------|------:|------:|------:|------:|
| PTS | 6.034 / +0.09 | 6.288 / -0.02 | +0.254 | -0.116 |
| REB | 2.458 / +0.01 | 2.543 / -0.03 | +0.085 | -0.033 |
| AST | 1.711 / +0.02 | 1.763 / -0.01 | +0.052 | -0.029 |
| 3PM | 1.085 / -0.00 | 1.105 / -0.01 | +0.020 | -0.012 |
| PRA | 8.518 / +0.10 | 8.972 / -0.07 | +0.454 | -0.172 |

### TARGET SEGMENT: Low-line props (stat < 10)

| Stat | Baseline RMSE/bias | Rate-scaled RMSE/bias | Δ RMSE | Δ bias |
|------|------:|------:|------:|------:|
| PTS | 4.733 / +2.29 | 4.832 / +2.23 | +0.099 | -0.055 |
| REB | 2.1 / +0.28 | 2.172 / +0.26 | +0.072 | -0.026 |
| AST | 1.55 / +0.10 | 1.595 / +0.07 | +0.045 | -0.027 |
| 3PM | 1.079 / -0.00 | 1.099 / -0.01 | +0.020 | -0.013 |
| PRA | 7.331 / +3.93 | 7.501 / +3.85 | +0.170 | -0.082 |

### TARGET SEGMENT: Bench players (min_L10 < 20)

| Stat | Baseline RMSE/bias | Rate-scaled RMSE/bias | Δ RMSE | Δ bias |
|------|------:|------:|------:|------:|
| PTS | 4.653 / +0.04 | 5.062 / +0.16 | +0.409 | +0.118 |
| REB | 2.069 / +0.00 | 2.203 / +0.06 | +0.134 | +0.052 |
| AST | 1.296 / +0.00 | 1.387 / +0.02 | +0.091 | +0.024 |
| 3PM | 0.81 / -0.01 | 0.844 / +0.01 | +0.034 | +0.014 |
| PRA | 6.864 / +0.02 | 7.567 / +0.23 | +0.703 | +0.203 |

### Starter regime (L10 >= 28) — regression guard

| Stat | Baseline RMSE/bias | Rate-scaled RMSE/bias | Δ RMSE | Δ bias |
|------|------:|------:|------:|------:|
| PTS | 8.822 / +0.18 | 8.884 / -0.70 | +0.062 | -0.874 |
| REB | 3.193 / +0.01 | 3.211 / -0.27 | +0.018 | -0.279 |
| AST | 2.589 / +0.05 | 2.598 / -0.16 | +0.009 | -0.206 |
| 3PM | 1.568 / -0.00 | 1.573 / -0.10 | +0.005 | -0.095 |
| PRA | 11.895 / +0.20 | 12.0 / -1.16 | +0.105 | -1.356 |

## Conclusion — REVERT

**RMSE gets WORSE in every segment for every stat.** The documented +2 to +4 bias
on low-line props is NOT an "opportunity vs efficiency" problem that can be fixed
by rate-scaling. Low-line props are mostly DNP-tailed distributions where the
true target is ~1–2 stats but the rolling mean is higher; rate-scaling just pushes
the noise around.

### Why rate-scaling fails at aggregate

1. VK2 baseline bias is already ≈ 0 on bench/rotation/starter segments.
2. The minutes model has RMSE = 8.74 min → multiplying by per-min rate of 0.4–1.0
   injects ±3 to ±8 stat-units of noise, which is far larger than the original
   projection error (~4–8 RMSE).
3. The starter bias moves DOWN by 0.2–1.4 stat units (OVER-correction). The bench
   bias gets slightly worse (+0.05 to +0.20). Net: harms both ends.

### What WORKED and should stay

The surgical `blend_bench` composition already shipped (2026-04-23):
- Applies ONLY to PTS / PRA (not REB/AST/3PM where it hurts)
- Applies ONLY when `min_played_L10 < 20` (bench regime, ~2% of props)
- Live board: 97 props composed, mean |Δ|=1.43, material negative and positive
  shifts aligned with expectations.
- Offline: 14% reduction in low-line PTS/PRA bias; 0.8% RMSE improvement on PRA.

### Recommendation

**REVERT** — do NOT integrate universal rate-scaling into `nba_scoring.py`.
The current surgical `blend_bench` production path is the better architecture.

Keep these new artifacts as infrastructure for future experiments:

- `scripts/train_expected_minutes.py` — cleaner, strict 12-feature minutes trainer
  (use as drop-in replacement for the 15-feature model inside `blend_bench` if
  desired; both give R²≈0.61 so it is a wash).
- `scripts/eval_expected_minutes_v2.py` — regression harness for any future
  composition experiment.
- `models/expected_minutes.pkl` — strict v2 artifact.
- `reports/expected_minutes_eval.json` — full segmented numbers for reference.

**No production code was modified.** Gate logic, TP logic, live scoring: untouched.
Ferrari endpoints remain HTTP 200.

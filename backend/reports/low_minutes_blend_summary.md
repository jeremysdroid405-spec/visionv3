# Low-Minutes / DNP-Risk Blend — Structural Experiment (2026-04-23)

Built a dedicated binary classifier for `minutes_played <= 12` and evaluated
two blend formulations against the 52-feature VK2 baseline on the 2024
hold-out.

## Step 1 — Classifier Quality

**Features (15):** core minutes rolling {L3,L5,L10,L20}_{mean,std}, trend,
games_played/started_last_10, starter/rotation/bench flags, home_flag,
rest_days, back_to_back_flag. Source: `scripts/train_low_minutes_classifier.py`.

**Performance (2024 hold-out):**
- AUC = **0.9124** (excellent)
- AUC bench segment = 0.879, rotation = 0.758, starter = 0.665
- AUC declining-minutes segment = 0.872
- Average precision = 0.919, Brier = 0.1163
- Calibration: pred_mean 0.53 vs actual pos_rate 0.51 (well-calibrated)
- Top features by gain: `min_played_L3_mean` 67.3%, `_L5_mean` 18.0%,
  `_L10_std` 2.0%, `_L20_mean` 1.9%, `games_played_last_10` 1.6%

**Artifact:** `/app/backend/models/low_minutes_classifier.pkl` (includes
both `minutes<=12` and `minutes<=8` variants).

## Step 2 — Blend formula tested

```
final_projection = (1 - p_low) * baseline_projection
                 + p_low * low_minutes_projection

low_minutes_projection = per-player mean of stat in games with min <= 12
                         (EB-shrunk toward league prior, K=5)
                         computed from 2020-2023 only (leakage-safe)
```

Two variants:
- **universal**: blend applied to all stats, all samples
- **gated_narrow**: PTS/PRA only, fires only when `p_low >= 0.5`

## Step 3 — Segmented Results (RMSE / bias)

### PTS

| Segment | baseline | universal | gated_narrow |
|---------|---------:|----------:|-------------:|
| PTS <10 | 4.733 / +2.29 | 3.965 / +1.18 | 4.495 / +1.61 |
| bench (L10<20) | 4.653 / +0.04 | 5.109 / -1.34 | 5.009 / -0.94 |
| rotation (L10 18-28) | 7.253 / +0.22 | 7.428 / -1.34 | 7.272 / +0.10 |
| starter (L10>=28) | 8.822 / +0.18 | 8.857 / -0.81 | 8.822 / +0.18 |
| declining (L5-L20<-2) | 6.296 / +0.13 | 6.661 / -1.51 | 6.574 / -0.90 |
| overall | 6.034 / +0.09 | 6.313 / -1.23 | 6.224 / -0.57 |
| PTS 10-20 | 6.139 / -3.15 | 7.352 / -4.85 | 6.907 / -3.77 |
| PTS >=20 | 11.562 / -9.32 | 13.581 / -11.44 | 12.390 / -9.93 |

### PRA

| Segment | baseline | universal | gated_narrow |
|---------|---------:|----------:|-------------:|
| PRA <10 | 7.331 / +3.93 | 5.832 / +2.32 | 6.742 / +2.85 |
| bench (L10<20) | 6.864 / +0.02 | 7.689 / -2.21 | 7.513 / -1.56 |
| rotation (L10 18-28) | 10.139 / +0.31 | 10.493 / -2.18 | 10.180 / +0.12 |
| starter (L10>=28) | 11.895 / +0.20 | 11.962 / -1.33 | 11.895 / +0.20 |
| declining (L5-L20<-2) | 9.147 / +0.19 | 9.791 / -2.41 | 9.653 / -1.43 |
| overall | 8.518 / +0.10 | 9.049 / -2.03 | 8.880 / -0.96 |
| PRA 10-20 | 6.493 / -1.25 | 7.793 / -3.87 | 7.657 / -2.39 |
| PRA >=20 | 11.977 / -8.26 | 14.734 / -11.28 | 13.326 / -9.23 |

### REB / AST / 3PM (gated_narrow correctly leaves untouched)

| Stat | Segment | base | gated_narrow |
|------|---------|-----:|-------------:|
| REB | overall | 2.458 / +0.01 | 2.458 / +0.01 |
| REB | REB <10 | 2.100 / +0.28 | 2.100 / +0.28 |
| REB | bench (L10<20) | 2.069 / +0.00 | 2.069 / +0.00 |
| AST | overall | 1.711 / +0.02 | 1.711 / +0.02 |
| AST | AST <10 | 1.550 / +0.10 | 1.550 / +0.10 |
| AST | bench (L10<20) | 1.296 / +0.00 | 1.296 / +0.00 |
| 3PM | overall | 1.085 / -0.00 | 1.085 / -0.00 |
| 3PM | 3PM <10 | 1.079 / -0.00 | 1.079 / -0.00 |
| 3PM | bench (L10<20) | 0.810 / -0.01 | 0.810 / -0.01 |

## Target-segment wins

| Metric | PTS <10 | PRA <10 |
|--------|--------:|--------:|
| Baseline RMSE                 | 4.73  | 7.33  |
| Gated-narrow RMSE             | 4.50  | 6.74  |
| Δ RMSE                        | **−4.9%** | **−8.0%** |
| Baseline bias                 | +2.29 | +3.93 |
| Gated-narrow bias             | +1.61 | +2.85 |
| Δ bias                        | **−30%** | **−27%** |

## Collateral regressions (why aggregate suffers)

| Stat | Overall Δ RMSE | bench Δ RMSE | L>=20 Δ RMSE | starter Δ RMSE |
|------|---:|---:|---:|---:|
| PTS | +0.190 | +0.356 | +0.828 | +0.000 |
| PRA | +0.362 | +0.649 | +1.349 | +0.000 |

### What happens with concrete examples

Gate fires on **70% of PTS<10 actual cases** (correct) but also on
**9.8% of PTS>=20 cases** (false positives). Examples:

**Wins (PTS<10 actual, blend pulled projection DOWN correctly):**
- Player L10=7.0, actual=0, baseline=26.80 → gated **3.94** (Δ −22.86) ✓
- Player L10=3.6, actual=0, baseline=26.15 → gated **5.35** (Δ −20.80) ✓
- Player L10=9.9, actual=0, baseline=23.04 → gated **2.79** (Δ −20.25) ✓

**Losses (PTS>=20 actual, gate fired falsely, projection pushed DOWN when
the player actually had a big game):**
- Player L10=19.6, actual=**39**, baseline=26.89 → gated **4.53** (Δ −22.36) ✗
- Player L10=6.6, actual=**27**, baseline=24.21 → gated **3.09** (Δ −21.12) ✗
- Player L10=17.9, actual=**27**, baseline=24.56 → gated **4.31** (Δ −20.25) ✗

The wins and losses are symmetric in magnitude; on aggregate the losses
outweigh the wins because PTS>=20 samples are 2× more common than PTS<10.

## Success-criteria scorecard

| Criterion | Met? |
|-----------|:----:|
| Low-line bias meaningfully reduced on PTS<10 & PRA<10 | ✅ |
| PTS<10 and PRA<10 RMSE improved | ✅ |
| Bench-player error improved | ❌ (bench PTS RMSE +0.36, PRA +0.65) |
| No major regression on starters | ✅ (starter untouched) |
| No major regression on high-line segments | ❌ (PTS>=20 +0.83, PRA>=20 +1.35) |

## Recommendation — **REVERT / ITERATE**

Per the spec's explicit instruction ("If results are flat or worse:
say so clearly, do NOT wire it into production"), I am **not wiring** the
blend into `services/scoring/adapters/nba_scoring.py`.

The classifier is excellent (AUC 0.91) and the approach is structurally
correct for the target segment, but the 15-feature probability-based
blend applied universally re-injects variance on the high-line tail
that destroys aggregate performance. Two of five success criteria fail.

### What DID work, what DIDN'T

**Worked:** classifier quality, bias reduction on target segments.

**Didn't work:** probabilistic blend formula. The issue is that the
classifier correctly identifies players at risk of low minutes, but we
don't know ex-ante whether their NEXT game will be a low-minute DNP
or a normal game — a bench player has ~20–30% of games with normal
minutes, and on those games they score normally.

## Artifacts preserved for future research

- `scripts/train_low_minutes_classifier.py` — 15-feature classifier trainer
- `scripts/eval_low_minutes_blend.py` — blend evaluation harness
- `models/low_minutes_classifier.pkl` — trained classifier (low_12 + low_8)
- `reports/low_minutes_classifier_eval.json` — classifier metrics
- `reports/low_minutes_blend_eval.json` — full segmented results

## What's still live in production

- 52-feature VK2 baseline (untouched).
- Existing `blend_bench` composition for PTS/PRA in bench regime (still
  live, still validated: 14% low-line bias reduction, no aggregate
  regressions). This remains the production-validated intervention.
- All 74 regression tests pass. Ferrari endpoints HTTP 200.

## Optional next direction

The right way to use this classifier may be at the **scoring layer**,
not the projection layer: use `p_low` as an asymmetric signal to widen
the confidence interval (sigma) on low-minute-risk players, which would
hurt OVER picks on bench lines (correct) without affecting UNDER picks
or projections on normal players. That's a different change and is
NOT what this task asked for.

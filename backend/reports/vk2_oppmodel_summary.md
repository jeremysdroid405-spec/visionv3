# VK2 + Opportunity-Model Integration — Evaluation Summary (2026-04-23)

## Objective
Inject the Universal Opportunity Model (`services/opportunity/nba.py`)
outputs into VK2 as 4 training features, retrain, evaluate. The
opportunity model is **strictly a feature generator** — it never
overrides, scales, or blends with VK2 output. VK2 remains the only
projection model.

## What shipped (code)

1. `scripts/retrain_nba_vk2.py` — added `--opportunity` flag (requires
   `--pruned`) that extends the 52-feature baseline with 4 features:

   | Feature                 | Source                                       |
   |-------------------------|----------------------------------------------|
   | `opp_expected_minutes`  | `models/expected_minutes.pkl` (12-feat reg.) |
   | `opp_risk_score`        | `models/low_minutes_classifier.pkl` prob     |
   | `opp_bucket_high`       | 1 if expected_minutes >= 26                  |
   | `opp_bucket_low`        | 1 if expected_minutes <  16                  |

   Output pkl: `vk2_{stat}_oppmodel.pkl` — sibling to production,
   never auto-promoted.

2. `(player_id, game_id)` → feature cache in the retrain script
   (4× speedup on matrix build across the 5 stats).

3. `scripts/evaluate_vk2_oppmodel.py` — head-to-head eval vs the
   production 52-feat pruned baseline. Writes
   `reports/vk2_oppmodel_eval.md`.

## Headline metrics (2024 held-out test)

| Stat | Base52 R²/RMSE/MAE  | OppModel56 R²/RMSE/MAE | Global Δ RMSE |
|------|---------------------|------------------------|---------------|
| PTS  | 0.5151 / 6.034 / 4.040 | 0.5106 / 6.062 / 4.040 | +0.028 |
| REB  | 0.4728 / 2.458 / 1.653 | 0.4718 / 2.460 / 1.650 | +0.002 |
| AST  | 0.4825 / 1.711 / 1.084 | 0.4816 / 1.713 / 1.082 | +0.002 |
| 3PM  | 0.3512 / 1.085 / 0.671 | 0.3513 / 1.085 / 0.670 |  0.000 |
| PRA  | 0.5592 / 8.519 / 5.773 | 0.5533 / 8.575 / 5.781 | +0.056 |

Global metrics are essentially **neutral to slightly regressed**. The
interesting signal is in the segmented view.

## Segmented results — does low-line bias improve?

### Bench (min_played_L5_mean < 18, n≈27.8k)
- **MAE drops for every stat** (PTS -0.021, REB -0.005, AST -0.005,
  3PM -0.002, PRA -0.027).
- RMSE roughly flat. Absolute |bias| slightly worse (+~0.01) for
  PTS/REB/AST but directionally correct.

### Low-line (predicted projection < sport-level cutoff)
- MAE drops for every stat.
- Bias mean **flips sign** from small positive (over-prediction) to
  small negative (under-prediction) on PTS/REB/AST/PRA low lines —
  directionally, the systemic low-line over-prediction bias is
  neutralised, but the magnitude overshoots slightly.

### Starters (min_played_L5_mean >= 28, n≈8.0k) — **REGRESSION**
- PRA:  MAE +0.078, RMSE +0.122, |bias| +0.136 — most concerning
- PTS:  MAE +0.047, RMSE +0.070, |bias| +0.094
- REB/AST/3PM: flat (< +0.01)

## Feature importance — are opp features actually used?

**Yes — every opp feature appears in top-20 for every stat.**

| Stat | `opp_expected_minutes` | `opp_bucket_low` | `opp_bucket_high` | `opp_risk_score` |
|------|------------------------|------------------|-------------------|------------------|
| PTS  | **#1** (0.331)         | #4  (0.026)      | #7  (0.019)       | #5  (0.024)      |
| REB  | #4  (0.066)            | #5  (0.042)      | #8  (0.015)       | #6  (0.022)      |
| AST  | #4  (0.055)            | #3  (0.058)      | #6  (0.026)       | #8  (0.011)      |
| 3PM  | #4  (0.024)            | #3  (0.041)      | #5  (0.016)       | #8  (0.012)      |
| PRA  | #2  (0.160)            | **#1** (0.630)   | #5  (0.026)       | #7  (0.006)      |

The model learns that when `opp_bucket_low=1`, PRA projections should
collapse — that single binary feature gets 63% of PRA importance. This
is the strongest single confirmation that the opportunity layer carries
real signal.

## Leakage note

Both source models (`expected_minutes.pkl`, `low_minutes_classifier.pkl`)
were trained on the full 2020-2024 range, the same range VK2 trains on.
For VK2 training samples (2020-2023) the opportunity model had in-sample
exposure to each target's minutes; for the 2024 test mask the
opportunity model also saw 80% of 2024 during its own training. This
means the training-time opportunity-feature importance is biased high,
and the test-set starter regression is likely understated vs what a
genuine OOF pipeline would show. To fully de-bias, the opportunity
models would need to be retrained with strict temporal folds before the
next evaluation round.

## Promotion decision

**DO NOT auto-promote to production.**

Reasons:
- Global RMSE regresses slightly on PTS and PRA.
- Starter RMSE regresses non-trivially on PRA and PTS.
- Low-line bench wins are small (<1% MAE) and tied to a potential
  leakage overestimate.

The `vk2_{stat}_oppmodel.pkl` pkls live in `models/` alongside the
production `vk2_{stat}.pkl` but are not referenced by any adapter.
Same inert-experimental pattern as the earlier rate-scaling / low-min
classifier experiments.

## Next steps (recommendations)

1. Retrain `expected_minutes.pkl` + `low_minutes_classifier.pkl` with
   strict temporal holdout excluding 2024 entirely, then re-evaluate
   the VK2 +oppmodel schema for a clean measurement.
2. Before any promotion, tighten the schema to a *single* opportunity
   feature (`opp_expected_minutes`) and re-run — the one-hot bucket
   features might be over-fitting the training distribution.
3. Consider a role-conditional promotion policy: serve the oppmodel56
   projections ONLY when `opp_bucket_low=1` (the bucket where
   projections improve), fall back to base52 for everyone else.

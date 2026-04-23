# Expected-Minutes Model — Evaluation Summary (2026-04-23)

## What was done

1. **Rollback:** `scripts/retrain_nba_vk2.py` reverted from the 59-feat
   `usage` schema + 69-feat `minutes` schema back to the **52-feat
   fixed-mins pruned baseline**. `--usage` and `--minutes` CLI flags,
   `USAGE_FEATURES`, `MINUTES_FEATURES`, and the in-model per-minute /
   `expected_minutes` / `usage_trend` / `fga_per_min` feature blocks
   in `build_features` were all deleted.
2. **Production swap:** the five `vk2_{stat}_usage59.pkl` artifacts
   previously served from `/app/backend/models/vk2_{stat}.pkl` were
   moved to `models/archive_usage59/` and the
   `archive_fixedmins_52feat/` 52-feat baselines copied into their
   place. Backend re-load verified — Ferrari endpoints return HTTP 200.
3. **New model:** `scripts/train_nba_minutes_model.py` trains a
   15-feature XGBRegressor that predicts **next-game minutes** per
   player, weighted by recency (2024 = test hold-out). Artifact:
   `/app/backend/models/nba_expected_minutes.pkl`.
4. **Segmented evaluation:**
   `scripts/eval_minutes_composition.py` compares four predictors on
   the 2024 hold-out across the target segments
   (`PRA<10`, `PTS<10`, `bench`, `declining`). Report:
   `/app/backend/reports/vk2_expected_minutes_segmented.json`.

## Expected-minutes model performance

```
R²=0.611   MAE=5.99 min   RMSE=8.73 min   bias=-0.21 min   σ=8.72

Segment metrics (2024 hold-out):
  bench (min_L10<20)       n=30,312   bias +0.07   RMSE 7.83
  starter (min_L10>=30)    n=5,357    bias +0.48   RMSE 10.05
  declining (L3-L10<-2)    n=11,730   bias +0.15   RMSE 10.25

Top features by gain:
  min_L3_mean      71.7%
  min_L5_mean      17.9%
  min_L20_mean      2.0%
  min_L10_mean      1.5%
  min_dnp_rate_L20  0.8%
```

## Rollback confirmed the primary regression is gone

The +2-to-+4 **bench** bias that the user documented was introduced by
the usage/minutes feature additions. After rollback, the baseline is
nearly unbiased on the bench and declining regimes:

| Stat | Baseline bias (bench) | Baseline bias (declining) |
|------|----------------------:|--------------------------:|
| PTS  | +0.04                 | +0.13                     |
| REB  | +0.00                 | +0.04                     |
| AST  | +0.00                 | +0.02                     |
| 3PM  | -0.01                 | +0.00                     |
| PRA  | +0.02                 | +0.19                     |

## Residual issue: low-line (line<10) bias remains

The 52-feat fixed-mins baseline still over-predicts on `line<10`. The
expected-minutes model composition (`blend_bench`: use
`predicted_minutes × historical per-min rate` in the bench regime,
baseline otherwise) reduces it without hurting RMSE:

| Stat | Segment | baseline RMSE/bias | blend_bench RMSE/bias | Δ bias |
|------|---------|--------------------:|----------------------:|-------:|
| PTS  | line<10 | 4.73 / **+2.29**   | 4.73 / +1.96          | -14%   |
| PRA  | line<10 | 7.33 / **+3.93**   | **7.22** / +3.39      | -14%, **RMSE also improves** |
| REB  | line<10 | 2.10 / +0.28       | 2.14 / +0.19          | -32%   |
| AST  | line<10 | 1.55 / +0.10       | 1.57 / +0.05          | -50%   |
| 3PM  | line<10 | 1.08 / -0.00       | 1.10 / -0.03          | n/a    |

## Interpretation and recommended path forward

- The **structural rollback alone** eliminates the documented
  over-prediction on bench players at the segment level.
- The **residual `line<10` over-prediction** is a different regime
  problem — actual targets are ~1.5–2.0 (DNP-heavy tail) and even a
  well-calibrated model on bench minutes (L10~15) projects more than
  that. The minutes-model composition chips ~14% off PRA and PTS
  line<10 bias with no RMSE penalty.
- For stats where baseline bias is already near zero (REB/AST/3PM),
  the composed predictor slightly over-corrects. The
  **`blend_bench` strategy is recommended for PTS and PRA only**;
  REB/AST/3PM should stay on the baseline.
- **NOT APPLIED IN PRODUCTION YET.** The scoring adapter
  (`services/scoring/adapters/nba_scoring.py`) still reads only the
  VK2 baseline. Wiring the minutes model into the adapter is a
  deliberate P0 follow-up gated on user approval, so we don't change
  the live pick surface without a sign-off.

## Files of record

- `/app/backend/scripts/retrain_nba_vk2.py`        — rolled-back trainer
- `/app/backend/scripts/train_nba_minutes_model.py` — NEW minutes trainer
- `/app/backend/scripts/eval_minutes_composition.py` — NEW segmented eval
- `/app/backend/models/nba_expected_minutes.pkl`   — NEW artifact
- `/app/backend/models/vk2_{pts,reb,ast,3pm,pra}.pkl` — 52-feat baseline (swapped)
- `/app/backend/models/archive_usage59/`           — archived 59-feat usage models
- `/app/backend/reports/vk2_expected_minutes_segmented.json` — full eval JSON
- `/app/backend/tests/test_expected_minutes_model.py` — 10 regression tests (all pass)

# VK2 Production Calibration / Bias Audit
Generated: 2026-04-23 23:41:39 UTC

Models audited: production `vk2_{pts,reb,ast,3pm,pra}.pkl` (52-feat pruned). Held-out = 2024 test mask (sample_weight==1.0 in training).

Directional convention: **bias = projection − actual**. **Positive bias ⇒ VK2 over-projects; negative ⇒ under-projects.**

## Headline (2024 held-out)

| Stat | n | mean pred | mean actual | bias mean | bias median | MAE | RMSE | σ_residual |
|------|---|-----------|-------------|-----------|-------------|-----|------|------------|
| PTS | 45587 | 6.647 | 6.552 | +0.0945 | +0.5930 | 4.0396 | 6.0342 | 6.034 |
| REB | 45587 | 2.543 | 2.538 | +0.0050 | +0.2772 | 1.6534 | 2.4577 | 2.458 |
| AST | 45587 | 1.535 | 1.518 | +0.0170 | +0.1534 | 1.0844 | 1.7111 | 1.711 |
| 3PM | 45587 | 0.775 | 0.778 | -0.0029 | +0.0741 | 0.6712 | 1.0850 | 1.085 |
| PRA | 45587 | 10.712 | 10.608 | +0.1033 | +0.7944 | 5.7731 | 8.5185 | 8.518 |

## PTS

**Global:** n=45587  pred=6.647  actual=6.552  bias_mean=+0.0945  bias_median=+0.5930  MAE=4.0396  RMSE=6.0342

### 5. Bias by line-bucket (deciles of projection)
| bucket | range | n | bias mean | bias median | MAE |
|--------|-------|---|-----------|-------------|-----|
| 0 | [-0.34, 0.33] | 4431 | +0.0120 | +0.3114 | 0.5834 |
| 1 | [0.33, 0.84] | 4687 | +0.0204 | +0.5327 | 0.9869 |
| 2 | [0.84, 1.51] | 4558 | -0.0043 | +1.0568 | 1.8465 |
| 3 | [1.51, 2.93] | 4559 | -0.0471 | +1.7431 | 2.9204 |
| 4 | [2.93, 4.74] | 4558 | +0.0840 | +2.0765 | 3.8508 |
| 5 | [4.74, 6.85] | 4559 | -0.0899 | +1.2270 | 4.6978 |
| 6 | [6.86, 8.99] | 4559 | -0.1948 | +0.5219 | 5.1677 |
| 7 | [8.99, 11.7] | 4558 | +0.1942 | +0.5652 | 5.6308 |
| 8 | [11.7, 16.46] | 4559 | +0.3879 | +0.4298 | 6.6774 |
| 9 | [16.46, 32.63] | 4559 | +0.5824 | -0.2840 | 8.0233 |

### 6. Bias by minutes bucket
| bucket | n | bias mean | bias median | MAE |
|--------|---|-----------|-------------|-----|
| bench | 27835 | +0.0523 | +0.6198 | 2.7412 |
| rotation | 9722 | +0.1443 | +0.5950 | 5.6579 |
| starter | 8030 | +0.1806 | +0.0274 | 6.5813 |

### 7. Bias by tier (proxy: p33=1.89, p66=8.26)
| tier | n | bias mean | bias median | MAE |
|------|---|-----------|-------------|-----|
| low | 15194 | +0.0015 | +0.4720 | 1.2793 |
| medium | 15194 | -0.0618 | +1.8934 | 4.2158 |
| high | 15199 | +0.3437 | +0.3343 | 6.6229 |

### 8. Bias by model OVER vs UNDER its own L10 baseline
| stance | n | bias mean | bias median | MAE |
|--------|---|-----------|-------------|-----|
| model_says_over | 27736 | +0.2295 | +0.4846 | 3.4159 |
| model_says_under | 17851 | -0.1153 | +0.9259 | 5.0087 |

### 9-10. Actual vs projected over-rate by symbolic line
| line | n | projected P(over) | actual over-rate | gap |
|------|---|-------------------|------------------|-----|
| 6.5 | 8651 | 0.498 | 0.436 | +0.062 |
| 9.5 | 8775 | 0.487 | 0.456 | +0.030 |
| 12.5 | 7805 | 0.468 | 0.434 | +0.033 |
| 15.5 | 6546 | 0.465 | 0.467 | -0.002 |
| 19.5 | 5324 | 0.448 | 0.474 | -0.025 |
| 24.5 | 3251 | 0.348 | 0.392 | -0.044 |

### Correction simulations (before / after)
**(A) Global intercept shift**  y' = y − -0.0945
- baseline: MAE=4.0396 RMSE=6.0342 bias_mean=+0.0945
- after   : MAE=4.0088 RMSE=6.0334 bias_mean=+0.0000

**(B) Line-bucket correction** (fit 80% / val 20% inside 2024)
- val baseline: MAE=3.9889 RMSE=5.9118 bias_mean=+0.0461
- val after   : MAE=3.9997 RMSE=5.9160 bias_mean=-0.0615
- bucket_bias: {'0': 0.0214, '1': -0.0004, '2': -0.0199, '3': -0.101, '4': 0.0944, '5': -0.059, '6': -0.2271, '7': 0.2016, '8': 0.4629, '9': 0.6935}

**(C) Minutes-bucket correction** (fit 80% / val 20%)
- val after   : MAE=3.9735 RMSE=5.9129 bias_mean=-0.0612
- bias by bucket: {'bench': 0.047, 'rotation': 0.1802, 'starter': 0.225}

**(D) Probability-only recalibration** (σ rescale, projection unchanged)
- model σ=6.034  empirical σ=6.034  ratio=1.000
| line | proj P(over) [new σ] | actual over-rate | gap |
|------|-----------------------|------------------|-----|
| 6.5 | 0.498 | 0.436 | +0.062 |
| 9.5 | 0.487 | 0.456 | +0.030 |
| 12.5 | 0.468 | 0.434 | +0.033 |
| 15.5 | 0.465 | 0.467 | -0.002 |
| 19.5 | 0.448 | 0.474 | -0.025 |
| 24.5 | 0.348 | 0.392 | -0.044 |

## REB

**Global:** n=45587  pred=2.543  actual=2.538  bias_mean=+0.0050  bias_median=+0.2772  MAE=1.6534  RMSE=2.4577

### 5. Bias by line-bucket (deciles of projection)
| bucket | range | n | bias mean | bias median | MAE |
|--------|-------|---|-----------|-------------|-----|
| 0 | [-0.17, 0.15] | 4514 | +0.0096 | +0.1477 | 0.2671 |
| 1 | [0.15, 0.39] | 4604 | -0.0060 | +0.2699 | 0.5028 |
| 2 | [0.39, 0.68] | 4558 | +0.0017 | +0.4668 | 0.8194 |
| 3 | [0.68, 1.3] | 4559 | -0.0522 | +0.7748 | 1.2914 |
| 4 | [1.3, 2.08] | 4558 | -0.0253 | +0.7301 | 1.6641 |
| 5 | [2.08, 2.75] | 4559 | -0.0120 | +0.3832 | 1.8549 |
| 6 | [2.75, 3.39] | 4559 | +0.0101 | +0.2069 | 1.9974 |
| 7 | [3.39, 4.16] | 4558 | -0.0239 | +0.3988 | 2.2058 |
| 8 | [4.16, 5.65] | 4559 | +0.0534 | +0.2693 | 2.5581 |
| 9 | [5.65, 15.0] | 4559 | +0.0951 | +0.0550 | 3.3705 |

### 6. Bias by minutes bucket
| bucket | n | bias mean | bias median | MAE |
|--------|---|-----------|-------------|-----|
| bench | 27835 | +0.0162 | +0.2959 | 1.2309 |
| rotation | 9722 | -0.0126 | +0.1754 | 2.2635 |
| starter | 8030 | -0.0123 | +0.1244 | 2.3794 |

### 7. Bias by tier (proxy: p33=0.85, p66=3.16)
| tier | n | bias mean | bias median | MAE |
|------|---|-----------|-------------|-----|
| low | 15194 | -0.0014 | +0.2426 | 0.5896 |
| medium | 15194 | -0.0171 | +0.6817 | 1.7183 |
| high | 15199 | +0.0336 | +0.2270 | 2.6519 |

### 8. Bias by model OVER vs UNDER its own L10 baseline
| stance | n | bias mean | bias median | MAE |
|--------|---|-----------|-------------|-----|
| model_says_over | 26867 | +0.0341 | +0.2195 | 1.3981 |
| model_says_under | 18720 | -0.0366 | +0.4467 | 2.0197 |

### 9-10. Actual vs projected over-rate by symbolic line
| line | n | projected P(over) | actual over-rate | gap |
|------|---|-------------------|------------------|-----|
| 2.5 | 26418 | 0.474 | 0.398 | +0.075 |
| 3.5 | 21316 | 0.456 | 0.407 | +0.049 |
| 4.5 | 16861 | 0.411 | 0.374 | +0.037 |
| 5.5 | 10844 | 0.400 | 0.386 | +0.014 |
| 7.5 | 4036 | 0.427 | 0.429 | -0.001 |
| 9.5 | 2319 | 0.406 | 0.457 | -0.051 |

### Correction simulations (before / after)
**(A) Global intercept shift**  y' = y − -0.0050
- baseline: MAE=1.6534 RMSE=2.4577 bias_mean=+0.0050
- after   : MAE=1.6517 RMSE=2.4577 bias_mean=+0.0000

**(B) Line-bucket correction** (fit 80% / val 20% inside 2024)
- val baseline: MAE=1.6337 RMSE=2.4221 bias_mean=-0.0135
- val after   : MAE=1.6360 RMSE=2.4223 bias_mean=-0.0232
- bucket_bias: {'0': 0.0109, '1': -0.0062, '2': 0.0001, '3': -0.0486, '4': -0.0364, '5': -0.0133, '6': 0.0011, '7': 0.0067, '8': 0.0717, '9': 0.1109}

**(C) Minutes-bucket correction** (fit 80% / val 20%)
- val after   : MAE=1.6279 RMSE=2.4221 bias_mean=-0.0231
- bias by bucket: {'bench': 0.0193, 'rotation': -0.0077, 'starter': -0.0025}

**(D) Probability-only recalibration** (σ rescale, projection unchanged)
- model σ=2.458  empirical σ=2.458  ratio=1.000
| line | proj P(over) [new σ] | actual over-rate | gap |
|------|-----------------------|------------------|-----|
| 2.5 | 0.474 | 0.398 | +0.075 |
| 3.5 | 0.456 | 0.407 | +0.049 |
| 4.5 | 0.411 | 0.374 | +0.037 |
| 5.5 | 0.400 | 0.386 | +0.014 |
| 7.5 | 0.427 | 0.429 | -0.001 |
| 9.5 | 0.406 | 0.457 | -0.051 |

## AST

**Global:** n=45587  pred=1.535  actual=1.518  bias_mean=+0.0170  bias_median=+0.1534  MAE=1.0844  RMSE=1.7111

### 5. Bias by line-bucket (deciles of projection)
| bucket | range | n | bias mean | bias median | MAE |
|--------|-------|---|-----------|-------------|-----|
| 0 | [-0.07, 0.08] | 4442 | +0.0112 | +0.0754 | 0.1329 |
| 1 | [0.08, 0.17] | 4676 | -0.0079 | +0.1080 | 0.2245 |
| 2 | [0.17, 0.33] | 4558 | -0.0128 | +0.2302 | 0.4330 |
| 3 | [0.33, 0.58] | 4559 | -0.0387 | +0.3910 | 0.6966 |
| 4 | [0.58, 0.96] | 4558 | -0.0414 | +0.6203 | 0.9074 |
| 5 | [0.96, 1.34] | 4559 | -0.0438 | +0.2243 | 1.0850 |
| 6 | [1.34, 1.79] | 4559 | +0.0147 | +0.4492 | 1.2481 |
| 7 | [1.79, 2.62] | 4558 | +0.0822 | +0.3696 | 1.5277 |
| 8 | [2.62, 4.11] | 4559 | +0.0867 | +0.2710 | 2.0199 |
| 9 | [4.11, 12.02] | 4559 | +0.1203 | +0.1841 | 2.5665 |

### 6. Bias by minutes bucket
| bucket | n | bias mean | bias median | MAE |
|--------|---|-----------|-------------|-----|
| bench | 27835 | -0.0018 | +0.1407 | 0.6941 |
| rotation | 9722 | +0.0648 | +0.3240 | 1.5254 |
| starter | 8030 | +0.0245 | +0.1758 | 1.9033 |

### 7. Bias by tier (proxy: p33=0.39, p66=1.62)
| tier | n | bias mean | bias median | MAE |
|------|---|-----------|-------------|-----|
| low | 15194 | -0.0055 | +0.1052 | 0.2982 |
| medium | 15194 | -0.0311 | +0.4427 | 0.9896 |
| high | 15199 | +0.0877 | +0.3110 | 1.9651 |

### 8. Bias by model OVER vs UNDER its own L10 baseline
| stance | n | bias mean | bias median | MAE |
|--------|---|-----------|-------------|-----|
| model_says_over | 27579 | +0.0445 | +0.1127 | 0.9255 |
| model_says_under | 18008 | -0.0250 | +0.2649 | 1.3278 |

### 9-10. Actual vs projected over-rate by symbolic line
| line | n | projected P(over) | actual over-rate | gap |
|------|---|-------------------|------------------|-----|
| 1.5 | 39489 | 0.388 | 0.253 | +0.135 |
| 2.5 | 24878 | 0.359 | 0.286 | +0.074 |
| 3.5 | 14675 | 0.378 | 0.339 | +0.039 |
| 4.5 | 8828 | 0.397 | 0.383 | +0.013 |
| 6.5 | 3427 | 0.328 | 0.370 | -0.042 |
| 8.5 | 874 | 0.343 | 0.412 | -0.069 |

### Correction simulations (before / after)
**(A) Global intercept shift**  y' = y − -0.0170
- baseline: MAE=1.0844 RMSE=1.7111 bias_mean=+0.0170
- after   : MAE=1.0778 RMSE=1.7110 bias_mean=+0.0000

**(B) Line-bucket correction** (fit 80% / val 20% inside 2024)
- val baseline: MAE=1.0812 RMSE=1.7009 bias_mean=+0.0089
- val after   : MAE=1.0840 RMSE=1.7008 bias_mean=-0.0105
- bucket_bias: {'0': 0.0158, '1': -0.0111, '2': -0.011, '3': -0.0394, '4': -0.0456, '5': -0.0558, '6': 0.024, '7': 0.0888, '8': 0.0864, '9': 0.1387}

**(C) Minutes-bucket correction** (fit 80% / val 20%)
- val after   : MAE=1.0809 RMSE=1.7014 bias_mean=-0.0102
- bias by bucket: {'bench': -0.0063, 'rotation': 0.0798, 'starter': 0.0335}

**(D) Probability-only recalibration** (σ rescale, projection unchanged)
- model σ=1.711  empirical σ=1.711  ratio=1.000
| line | proj P(over) [new σ] | actual over-rate | gap |
|------|-----------------------|------------------|-----|
| 1.5 | 0.388 | 0.253 | +0.135 |
| 2.5 | 0.359 | 0.286 | +0.074 |
| 3.5 | 0.378 | 0.339 | +0.039 |
| 4.5 | 0.397 | 0.383 | +0.013 |
| 6.5 | 0.328 | 0.370 | -0.042 |
| 8.5 | 0.343 | 0.412 | -0.069 |

## 3PM

**Global:** n=45587  pred=0.775  actual=0.778  bias_mean=-0.0029  bias_median=+0.0741  MAE=0.6712  RMSE=1.0850

### 5. Bias by line-bucket (deciles of projection)
| bucket | range | n | bias mean | bias median | MAE |
|--------|-------|---|-----------|-------------|-----|
| 0 | [-0.26, 0.03] | 3389 | -0.0083 | +0.0093 | 0.0331 |
| 1 | [0.03, 0.05] | 5496 | -0.0004 | +0.0346 | 0.0714 |
| 2 | [0.05, 0.11] | 4791 | -0.0010 | +0.0729 | 0.1453 |
| 3 | [0.11, 0.23] | 4559 | -0.0129 | +0.1528 | 0.3003 |
| 4 | [0.23, 0.46] | 4558 | -0.0136 | +0.2931 | 0.5286 |
| 5 | [0.46, 0.77] | 4559 | -0.0192 | +0.5078 | 0.7726 |
| 6 | [0.77, 1.13] | 4559 | -0.0192 | +0.1049 | 0.9304 |
| 7 | [1.13, 1.51] | 4558 | -0.0523 | +0.2729 | 1.1224 |
| 8 | [1.51, 2.0] | 4559 | +0.0063 | +0.5314 | 1.2687 |
| 9 | [2.0, 5.12] | 4559 | +0.0897 | +0.2391 | 1.5258 |

### 6. Bias by minutes bucket
| bucket | n | bias mean | bias median | MAE |
|--------|---|-----------|-------------|-----|
| bench | 27835 | -0.0026 | +0.0675 | 0.4076 |
| rotation | 9722 | -0.0085 | +0.1284 | 1.0022 |
| starter | 8030 | +0.0027 | +0.1501 | 1.1843 |

### 7. Bias by tier (proxy: p33=0.14, p66=1.00)
| tier | n | bias mean | bias median | MAE |
|------|---|-----------|-------------|-----|
| low | 15196 | -0.0033 | +0.0346 | 0.1029 |
| medium | 15192 | -0.0112 | +0.2460 | 0.6357 |
| high | 15199 | +0.0057 | +0.2443 | 1.2749 |

### 8. Bias by model OVER vs UNDER its own L10 baseline
| stance | n | bias mean | bias median | MAE |
|--------|---|-----------|-------------|-----|

### 9-10. Actual vs projected over-rate by symbolic line
| line | n | projected P(over) | actual over-rate | gap |
|------|---|-------------------|------------------|-----|
| 0.5 | 43713 | 0.545 | 0.331 | +0.213 |
| 1.5 | 45421 | 0.283 | 0.206 | +0.078 |
| 2.5 | 22127 | 0.205 | 0.224 | -0.019 |
| 3.5 | 9280 | 0.125 | 0.202 | -0.077 |

### Correction simulations (before / after)
**(A) Global intercept shift**  y' = y − +0.0029
- baseline: MAE=0.6712 RMSE=1.0850 bias_mean=-0.0029
- after   : MAE=0.6725 RMSE=1.0850 bias_mean=-0.0000

**(B) Line-bucket correction** (fit 80% / val 20% inside 2024)
- val baseline: MAE=0.6659 RMSE=1.0806 bias_mean=-0.0096
- val after   : MAE=0.6694 RMSE=1.0802 bias_mean=-0.0084
- bucket_bias: {'0': -0.0089, '1': -0.0003, '2': -0.0035, '3': -0.0209, '4': -0.0165, '5': -0.0164, '6': -0.0184, '7': -0.0455, '8': 0.0214, '9': 0.0947}

**(C) Minutes-bucket correction** (fit 80% / val 20%)
- val after   : MAE=0.6673 RMSE=1.0806 bias_mean=-0.0084
- bias by bucket: {'bench': -0.004, 'rotation': -0.0026, 'starter': 0.0102}

**(D) Probability-only recalibration** (σ rescale, projection unchanged)
- model σ=1.085  empirical σ=1.085  ratio=1.000
| line | proj P(over) [new σ] | actual over-rate | gap |
|------|-----------------------|------------------|-----|
| 0.5 | 0.545 | 0.331 | +0.213 |
| 1.5 | 0.283 | 0.206 | +0.078 |
| 2.5 | 0.205 | 0.224 | -0.019 |
| 3.5 | 0.125 | 0.202 | -0.077 |

## PRA

**Global:** n=45587  pred=10.712  actual=10.608  bias_mean=+0.1033  bias_median=+0.7944  MAE=5.7731  RMSE=8.5185

### 5. Bias by line-bucket (deciles of projection)
| bucket | range | n | bias mean | bias median | MAE |
|--------|-------|---|-----------|-------------|-----|
| 0 | [0.14, 0.57] | 4430 | +0.0486 | +0.5305 | 0.9524 |
| 1 | [0.57, 1.43] | 4688 | -0.0553 | +0.9028 | 1.6987 |
| 2 | [1.43, 2.5] | 4558 | -0.0043 | +1.7305 | 2.9229 |
| 3 | [2.5, 4.99] | 4559 | -0.2357 | +2.7657 | 4.7337 |
| 4 | [5.0, 8.24] | 4558 | +0.1080 | +2.4259 | 6.0792 |
| 5 | [8.24, 11.54] | 4559 | -0.3826 | +0.5741 | 6.9314 |
| 6 | [11.54, 14.94] | 4559 | +0.0508 | +0.2932 | 7.2162 |
| 7 | [14.94, 18.96] | 4558 | +0.3630 | +0.3867 | 7.9339 |
| 8 | [18.96, 25.32] | 4559 | +0.4162 | -0.2917 | 8.8833 |
| 9 | [25.32, 56.19] | 4559 | +0.7273 | -1.1790 | 10.3582 |

### 6. Bias by minutes bucket
| bucket | n | bias mean | bias median | MAE |
|--------|---|-----------|-------------|-----|
| bench | 27835 | +0.0466 | +0.9028 | 4.2278 |
| rotation | 9722 | +0.2021 | +0.1972 | 7.9038 |
| starter | 8030 | +0.1802 | -0.6022 | 8.5500 |

### 7. Bias by tier (proxy: p33=3.18, p66=13.88)
| tier | n | bias mean | bias median | MAE |
|------|---|-----------|-------------|-----|
| low | 15194 | -0.0183 | +0.7995 | 2.0867 |
| medium | 15194 | -0.1544 | +1.8068 | 6.3454 |
| high | 15199 | +0.4825 | -0.2932 | 8.8862 |

### 8. Bias by model OVER vs UNDER its own L10 baseline
| stance | n | bias mean | bias median | MAE |
|--------|---|-----------|-------------|-----|
| model_says_over | 26756 | +0.2563 | +0.5706 | 4.8776 |
| model_says_under | 18831 | -0.1140 | +1.2399 | 7.0455 |

### 9-10. Actual vs projected over-rate by symbolic line
| line | n | projected P(over) | actual over-rate | gap |
|------|---|-------------------|------------------|-----|
| 15.5 | 9395 | 0.491 | 0.474 | +0.016 |
| 22.5 | 8003 | 0.455 | 0.468 | -0.013 |
| 29.5 | 5856 | 0.436 | 0.473 | -0.037 |
| 36.5 | 3567 | 0.340 | 0.407 | -0.066 |
| 45.5 | 1139 | 0.210 | 0.285 | -0.075 |

### Correction simulations (before / after)
**(A) Global intercept shift**  y' = y − -0.1033
- baseline: MAE=5.7731 RMSE=8.5185 bias_mean=+0.1033
- after   : MAE=5.7448 RMSE=8.5178 bias_mean=-0.0000

**(B) Line-bucket correction** (fit 80% / val 20% inside 2024)
- val baseline: MAE=5.6916 RMSE=8.3071 bias_mean=+0.0213
- val after   : MAE=5.7229 RMSE=8.3125 bias_mean=-0.1019
- bucket_bias: {'0': 0.0614, '1': -0.0786, '2': -0.0013, '3': -0.3628, '4': 0.1885, '5': -0.3335, '6': 0.0163, '7': 0.2814, '8': 0.6237, '9': 0.8456}

**(C) Minutes-bucket correction** (fit 80% / val 20%)
- val after   : MAE=5.6823 RMSE=8.3089 bias_mean=-0.1032
- bias by bucket: {'bench': 0.0439, 'rotation': 0.256, 'starter': 0.2417}

**(D) Probability-only recalibration** (σ rescale, projection unchanged)
- model σ=8.518  empirical σ=8.518  ratio=1.000
| line | proj P(over) [new σ] | actual over-rate | gap |
|------|-----------------------|------------------|-----|
| 15.5 | 0.491 | 0.474 | +0.016 |
| 22.5 | 0.455 | 0.468 | -0.013 |
| 29.5 | 0.436 | 0.473 | -0.037 |
| 36.5 | 0.340 | 0.407 | -0.066 |
| 45.5 | 0.211 | 0.285 | -0.075 |

## Overall Recommendation
- One or more stats show global mean bias > 0.10 — VK2 IS globally over-projecting.
- Bench-bucket bias exceeds 0.05 — localised low-minute overshoot persists.
- Probability calibration gap > 3 pp at some lines — σ rescale will help.

## KEEP / REJECT per correction

| stat | A global | B line-bucket | C minutes-bucket | D prob-only | A Δ | D σ-ratio |
|------|----------|---------------|------------------|-------------|-----|-----------|
| PTS | KEEP | REJECT | REJECT | REJECT | -0.0945 | 1.000 |
| REB | KEEP | REJECT | REJECT | REJECT | -0.0050 | 1.000 |
| AST | KEEP | REJECT | REJECT | REJECT | -0.0170 | 1.000 |
| 3PM | KEEP | REJECT | REJECT | REJECT | +0.0029 | 1.000 |
| PRA | KEEP | REJECT | REJECT | REJECT | -0.1033 | 1.000 |

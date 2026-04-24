# vk2_{stat}_distprofile (175-feat) vs production base52 — eval
Generated: 2026-04-24 01:28:59 UTC

Sibling experiment. No production change. All metrics on the 2024 held-out slice.

## Global

| Stat | MAE base | MAE dp | Δ MAE | RMSE base | RMSE dp | Δ RMSE | bias_median base | bias_median dp |
|------|---------:|-------:|------:|----------:|--------:|-------:|-----------------:|----------------:|
| PTS | 4.0396 | 4.0399 | +0.0003 | 6.0342 | 6.0356 | +0.0015 | +0.593 | +0.593 |
| REB | 1.6534 | 1.6536 | +0.0002 | 2.4577 | 2.4587 | +0.0011 | +0.277 | +0.272 |
| AST | 1.0844 | 1.0811 | -0.0034 | 1.7111 | 1.7033 | -0.0079 | +0.153 | +0.152 |
| 3PM | 0.6712 | 0.6712 | +0.0000 | 1.0850 | 1.0841 | -0.0009 | +0.074 | +0.074 |
| PRA | 5.7731 | 5.7765 | +0.0034 | 8.5185 | 8.5201 | +0.0016 | +0.794 | +0.783 |

## PTS

**Global base  :** MAE=4.0396  RMSE=6.0342  bias_mean=+0.0945  bias_median=+0.5930  n=45587
**Global dp175:** MAE=4.0399  RMSE=6.0356  bias_mean=+0.0980  bias_median=+0.5926  n=45587

### Low-line segments (pred < 1.5× threshold)
| threshold | n | MAE base | MAE dp | bias base | bias dp | Δ MAE | Δ |bias| |
|-----------|---|----------|--------|-----------|---------|-------|----------|
| 1 | 13700 | 1.1517 | 1.1497 | +0.0194 | +0.0142 | -0.0020 | -0.0053 |
| 5 | 28698 | 2.6044 | 2.5980 | +0.0226 | +0.0066 | -0.0064 | -0.0160 |
| 10 | 39890 | 3.4884 | 3.4851 | +0.0472 | +0.0380 | -0.0033 | -0.0092 |

### Bench / starter segments
| segment | n | MAE base | MAE dp | bias base | bias dp | Δ MAE | Δ |bias| |
|---------|---|----------|--------|-----------|---------|-------|----------|
| bench | 27835 | 2.7412 | 2.7417 | +0.0523 | +0.0690 | +0.0005 | +0.0167 |
| starter | 8030 | 6.5813 | 6.5803 | +0.1806 | +0.1238 | -0.0011 | -0.0568 |

### Top-15 distribution-profile features by importance
| rank | name | importance |
|-----:|------|-----------:|
| 2 | `pts_hit_20_rate_L50` | 0.1004 |
| 3 | `pts_hit_10_rate_career` | 0.0850 |
| 4 | `pts_hit_20_rate_L20` | 0.0585 |
| 5 | `pts_hit_25_rate_L50` | 0.0529 |
| 6 | `pts_hit_15_rate_career` | 0.0412 |
| 8 | `pts_hit_10_rate_L50` | 0.0349 |
| 9 | `pra_hit_30_rate_L50` | 0.0294 |
| 10 | `pts_hit_15_rate_L20` | 0.0164 |
| 11 | `pra_hit_35_rate_L50` | 0.0162 |
| 12 | `pts_hit_10_rate_L20` | 0.0149 |
| 13 | `pts_hit_25_rate_L20` | 0.0124 |
| 14 | `pts_hit_15_rate_L50` | 0.0110 |
| 15 | `pra_hit_35_rate_L20` | 0.0091 |
| 16 | `pts_hit_30_rate_L20` | 0.0081 |
| 17 | `pra_hit_15_rate_L50` | 0.0078 |

## REB

**Global base  :** MAE=1.6534  RMSE=2.4577  bias_mean=+0.0050  bias_median=+0.2772  n=45587
**Global dp175:** MAE=1.6536  RMSE=2.4587  bias_mean=+0.0061  bias_median=+0.2715  n=45587

### Low-line segments (pred < 1.5× threshold)
| threshold | n | MAE base | MAE dp | bias base | bias dp | Δ MAE | Δ |bias| |
|-----------|---|----------|--------|-----------|---------|-------|----------|
| 1 | 19317 | 0.7669 | 0.7653 | -0.0037 | -0.0044 | -0.0016 | +0.0006 |
| 3 | 37818 | 1.3653 | 1.3653 | -0.0026 | -0.0034 | +0.0000 | +0.0008 |
| 5 | 43488 | 1.5562 | 1.5564 | -0.0011 | -0.0013 | +0.0002 | +0.0002 |

### Bench / starter segments
| segment | n | MAE base | MAE dp | bias base | bias dp | Δ MAE | Δ |bias| |
|---------|---|----------|--------|-----------|---------|-------|----------|
| bench | 27835 | 1.2309 | 1.2316 | +0.0162 | +0.0223 | +0.0008 | +0.0061 |
| starter | 8030 | 2.3794 | 2.3827 | -0.0123 | -0.0201 | +0.0033 | +0.0077 |

### Top-15 distribution-profile features by importance
| rank | name | importance |
|-----:|------|-----------:|
| 1 | `reb_hit_10_rate_career` | 0.1584 |
| 3 | `reb_hit_10_rate_L50` | 0.1087 |
| 6 | `reb_hit_7_rate_L20` | 0.0361 |
| 7 | `reb_hit_7_rate_L50` | 0.0359 |
| 8 | `reb_hit_12_rate_career` | 0.0298 |
| 10 | `reb_hit_5_rate_L50` | 0.0194 |
| 11 | `reb_hit_12_rate_L50` | 0.0169 |
| 12 | `reb_hit_5_rate_career` | 0.0102 |
| 13 | `reb_hit_10_rate_L20` | 0.0092 |
| 14 | `pts_hit_5_rate_career` | 0.0082 |
| 15 | `reb_hit_3_rate_career` | 0.0069 |
| 16 | `reb_hit_5_rate_L20` | 0.0068 |
| 17 | `reb_hit_7_rate_career` | 0.0061 |
| 18 | `reb_hit_1_rate_L20` | 0.0056 |
| 20 | `pts_hit_30_rate_career` | 0.0052 |

## AST

**Global base  :** MAE=1.0844  RMSE=1.7111  bias_mean=+0.0170  bias_median=+0.1534  n=45587
**Global dp175:** MAE=1.0811  RMSE=1.7033  bias_mean=+0.0154  bias_median=+0.1520  n=45587

### Low-line segments (pred < 1.5× threshold)
| threshold | n | MAE base | MAE dp | bias base | bias dp | Δ MAE | Δ |bias| |
|-----------|---|----------|--------|-----------|---------|-------|----------|
| 1 | 29110 | 0.6158 | 0.6152 | -0.0172 | -0.0189 | -0.0005 | +0.0017 |
| 2 | 37993 | 0.8257 | 0.8253 | +0.0016 | -0.0008 | -0.0004 | -0.0008 |
| 4 | 44407 | 1.0319 | 1.0295 | +0.0147 | +0.0109 | -0.0024 | -0.0038 |

### Bench / starter segments
| segment | n | MAE base | MAE dp | bias base | bias dp | Δ MAE | Δ |bias| |
|---------|---|----------|--------|-----------|---------|-------|----------|
| bench | 27835 | 0.6941 | 0.6940 | -0.0018 | +0.0003 | -0.0002 | -0.0015 |
| starter | 8030 | 1.9033 | 1.8904 | +0.0245 | +0.0106 | -0.0129 | -0.0138 |

### Top-15 distribution-profile features by importance
| rank | name | importance |
|-----:|------|-----------:|
| 3 | `ast_hit_6_rate_career` | 0.0506 |
| 5 | `ast_hit_8_rate_career` | 0.0453 |
| 6 | `ast_hit_6_rate_L50` | 0.0292 |
| 7 | `ast_hit_4_rate_career` | 0.0264 |
| 8 | `ast_hit_2_rate_L20` | 0.0254 |
| 9 | `ast_hit_10_rate_L50` | 0.0247 |
| 10 | `ast_hit_4_rate_L50` | 0.0236 |
| 11 | `ast_hit_8_rate_L50` | 0.0207 |
| 12 | `pts_hit_1_rate_L50` | 0.0148 |
| 15 | `ast_hit_6_rate_L20` | 0.0083 |
| 16 | `ast_hit_8_rate_L20` | 0.0082 |
| 17 | `pra_zero_rate_L20` | 0.0075 |
| 18 | `ast_hit_4_rate_L20` | 0.0056 |
| 19 | `ast_hit_2_rate_career` | 0.0049 |
| 21 | `ast_hit_10_rate_L20` | 0.0047 |

## 3PM

**Global base  :** MAE=0.6712  RMSE=1.0850  bias_mean=-0.0029  bias_median=+0.0741  n=45587
**Global dp175:** MAE=0.6712  RMSE=1.0841  bias_mean=-0.0009  bias_median=+0.0742  n=45587

### Low-line segments (pred < 1.5× threshold)
| threshold | n | MAE base | MAE dp | bias base | bias dp | Δ MAE | Δ |bias| |
|-----------|---|----------|--------|-----------|---------|-------|----------|
| 1 | 36230 | 0.4846 | 0.4844 | -0.0105 | -0.0115 | -0.0002 | +0.0010 |
| 2 | 44966 | 0.6567 | 0.6566 | -0.0047 | -0.0045 | -0.0000 | -0.0002 |

### Bench / starter segments
| segment | n | MAE base | MAE dp | bias base | bias dp | Δ MAE | Δ |bias| |
|---------|---|----------|--------|-----------|---------|-------|----------|
| bench | 27835 | 0.4076 | 0.4074 | -0.0026 | -0.0016 | -0.0002 | -0.0010 |
| starter | 8030 | 1.1843 | 1.1875 | +0.0027 | +0.0062 | +0.0032 | +0.0035 |

### Top-15 distribution-profile features by importance
| rank | name | importance |
|-----:|------|-----------:|
| 3 | `threes_hit_3_rate_L50` | 0.0332 |
| 4 | `threes_hit_2_rate_L50` | 0.0293 |
| 5 | `threes_hit_2_rate_career` | 0.0227 |
| 6 | `threes_hit_4_rate_career` | 0.0193 |
| 7 | `pts_zero_rate_career` | 0.0139 |
| 8 | `pra_zero_rate_L50` | 0.0114 |
| 9 | `pra_zero_rate_L20` | 0.0101 |
| 10 | `threes_hit_1_rate_L50` | 0.0100 |
| 11 | `threes_zero_rate_L50` | 0.0094 |
| 12 | `threes_hit_2_rate_L20` | 0.0088 |
| 14 | `pra_hit_1_rate_L20` | 0.0071 |
| 15 | `pra_hit_1_rate_L50` | 0.0070 |
| 16 | `threes_hit_3_rate_L20` | 0.0069 |
| 17 | `pra_hit_10_rate_career` | 0.0066 |
| 18 | `pra_hit_40_rate_L50` | 0.0065 |

## PRA

**Global base  :** MAE=5.7731  RMSE=8.5185  bias_mean=+0.1033  bias_median=+0.7944  n=45587
**Global dp175:** MAE=5.7765  RMSE=8.5201  bias_mean=+0.1012  bias_median=+0.7829  n=45587

### Low-line segments (pred < 1.5× threshold)
| threshold | n | MAE base | MAE dp | bias base | bias dp | Δ MAE | Δ |bias| |
|-----------|---|----------|--------|-----------|---------|-------|----------|
| 10 | 31948 | 4.3668 | 4.3686 | -0.0623 | -0.0645 | +0.0018 | +0.0022 |
| 15 | 39368 | 5.0786 | 5.0800 | +0.0326 | +0.0307 | +0.0014 | -0.0019 |
| 20 | 43058 | 5.4724 | 5.4741 | +0.0533 | +0.0503 | +0.0017 | -0.0030 |

### Bench / starter segments
| segment | n | MAE base | MAE dp | bias base | bias dp | Δ MAE | Δ |bias| |
|---------|---|----------|--------|-----------|---------|-------|----------|
| bench | 27835 | 4.2278 | 4.2284 | +0.0466 | +0.0707 | +0.0005 | +0.0240 |
| starter | 8030 | 8.5500 | 8.5538 | +0.1802 | +0.1003 | +0.0038 | -0.0799 |

### Top-15 distribution-profile features by importance
| rank | name | importance |
|-----:|------|-----------:|
| 1 | `pra_hit_30_rate_career` | 0.2857 |
| 2 | `pra_hit_35_rate_career` | 0.1128 |
| 4 | `pra_hit_30_rate_L50` | 0.1065 |
| 5 | `pra_hit_25_rate_career` | 0.0656 |
| 6 | `pra_hit_35_rate_L50` | 0.0385 |
| 7 | `pra_hit_25_rate_L50` | 0.0268 |
| 9 | `pra_hit_35_rate_L20` | 0.0190 |
| 10 | `pra_hit_30_rate_L20` | 0.0189 |
| 11 | `pra_hit_25_rate_L20` | 0.0105 |
| 12 | `pra_hit_15_rate_L50` | 0.0100 |
| 13 | `pra_hit_20_rate_L50` | 0.0075 |
| 15 | `pra_hit_20_rate_career` | 0.0062 |
| 16 | `reb_hit_1_rate_L20` | 0.0056 |
| 17 | `pra_hit_20_rate_L20` | 0.0056 |
| 18 | `pra_hit_40_rate_L20` | 0.0053 |

## Verdict

- Distribution-profile features do NOT move global MAE meaningfully on any stat (|Δ| < 0.005 everywhere).
- Inspect the per-stat low-line segment tables for the actual signal: the thesis is that zero-rate-aware features fix low-line bias even when global MAE is unchanged.
- Sibling pkls are INERT — production `vk2_{stat}.pkl` files are untouched.
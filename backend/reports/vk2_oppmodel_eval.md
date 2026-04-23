# VK2 +oppmodel (56-feat) vs pruned52 — head-to-head

Generated: 2026-04-23 22:54:12 UTC

Opportunity model = strictly feature generator (expected_minutes, risk, bucket one-hot). VK2 remains the ONLY projection model. Comparing 2024 test-set (held-out) performance, with focus on low-line bias and bench-vs-starter stability.

## PTS

### Global (n=45587)
- **base52 **: MAE=4.0396  RMSE=6.0342  bias=+0.0945
- **oppmodel56**: MAE=4.0399  RMSE=6.0621  bias=+0.1025
- Δ (opp-base): MAE +0.0002  RMSE +0.0279  |bias| +0.0080

### Low-line (predicted < 10.0, n=33879)
- **base52 **: MAE=3.0378  RMSE=4.6448  bias=+0.0216
- **oppmodel56**: MAE=3.0178  RMSE=4.6382  bias=-0.0375
- Δ (opp-base): MAE -0.0200  RMSE -0.0066  |bias| +0.0159

### bench (L5<18min) (n=27835)
- base52 : MAE=2.7412  RMSE=4.4924  bias=+0.0523
- opp56  : MAE=2.7205  RMSE=4.4815  bias=+0.0590
- Δ: MAE -0.0207  RMSE -0.0109  |bias| +0.0068

### starters (L5>=28min) (n=8030)
- base52 : MAE=6.5813  RMSE=8.5692  bias=+0.1806
- opp56  : MAE=6.6284  RMSE=8.6391  bias=+0.2747
- Δ: MAE +0.0470  RMSE +0.0699  |bias| +0.0941

### Feature importance (opportunity features)
- `opp_expected_minutes` rank #1/56 importance=0.3310
- `opp_risk_score` rank #5/56 importance=0.0239
- `opp_bucket_high` rank #7/56 importance=0.0185
- `opp_bucket_low` rank #4/56 importance=0.0256
- opportunity features in top-20: ['opp_expected_minutes', 'opp_risk_score', 'opp_bucket_high', 'opp_bucket_low']

_eval PTS took 297.5s_

## REB

### Global (n=45587)
- **base52 **: MAE=1.6534  RMSE=2.4577  bias=+0.0050
- **oppmodel56**: MAE=1.6498  RMSE=2.4600  bias=+0.0102
- Δ (opp-base): MAE -0.0036  RMSE +0.0023  |bias| +0.0051

### Low-line (predicted < 4.0, n=35610)
- **base52 **: MAE=1.3057  RMSE=1.9624  bias=+0.0079
- **oppmodel56**: MAE=1.3005  RMSE=1.9620  bias=-0.0118
- Δ (opp-base): MAE -0.0052  RMSE -0.0003  |bias| +0.0039

### bench (L5<18min) (n=27835)
- base52 : MAE=1.2309  RMSE=1.9940  bias=+0.0162
- opp56  : MAE=1.2259  RMSE=1.9949  bias=+0.0243
- Δ: MAE -0.0049  RMSE +0.0009  |bias| +0.0081

### starters (L5>=28min) (n=8030)
- base52 : MAE=2.3794  RMSE=3.1415  bias=-0.0123
- opp56  : MAE=2.3822  RMSE=3.1469  bias=+0.0124
- Δ: MAE +0.0028  RMSE +0.0054  |bias| +0.0000

### Feature importance (opportunity features)
- `opp_expected_minutes` rank #4/56 importance=0.0659
- `opp_risk_score` rank #6/56 importance=0.0222
- `opp_bucket_high` rank #8/56 importance=0.0146
- `opp_bucket_low` rank #5/56 importance=0.0415
- opportunity features in top-20: ['opp_expected_minutes', 'opp_risk_score', 'opp_bucket_high', 'opp_bucket_low']

_eval REB took 103.7s_

## AST

### Global (n=45587)
- **base52 **: MAE=1.0844  RMSE=1.7111  bias=+0.0170
- **oppmodel56**: MAE=1.0823  RMSE=1.7126  bias=+0.0205
- Δ (opp-base): MAE -0.0021  RMSE +0.0014  |bias| +0.0035

### Low-line (predicted < 3.0, n=38066)
- **base52 **: MAE=0.8319  RMSE=1.3085  bias=+0.0045
- **oppmodel56**: MAE=0.8272  RMSE=1.3088  bias=-0.0096
- Δ (opp-base): MAE -0.0048  RMSE +0.0003  |bias| +0.0051

### bench (L5<18min) (n=27835)
- base52 : MAE=0.6941  RMSE=1.2338  bias=-0.0018
- opp56  : MAE=0.6889  RMSE=1.2331  bias=+0.0003
- Δ: MAE -0.0052  RMSE -0.0008  |bias| -0.0016

### starters (L5>=28min) (n=8030)
- base52 : MAE=1.9033  RMSE=2.5243  bias=+0.0245
- opp56  : MAE=1.9089  RMSE=2.5264  bias=+0.0590
- Δ: MAE +0.0056  RMSE +0.0021  |bias| +0.0346

### Feature importance (opportunity features)
- `opp_expected_minutes` rank #4/56 importance=0.0553
- `opp_risk_score` rank #8/56 importance=0.0110
- `opp_bucket_high` rank #6/56 importance=0.0256
- `opp_bucket_low` rank #3/56 importance=0.0581
- opportunity features in top-20: ['opp_expected_minutes', 'opp_risk_score', 'opp_bucket_high', 'opp_bucket_low']

_eval AST took 102.9s_

## 3PM

### Global (n=45587)
- **base52 **: MAE=0.6712  RMSE=1.0850  bias=-0.0029
- **oppmodel56**: MAE=0.6698  RMSE=1.0849  bias=+0.0003
- Δ (opp-base): MAE -0.0015  RMSE -0.0000  |bias| -0.0026

### Low-line (predicted < 1.5, n=36268)
- **base52 **: MAE=0.4880  RMSE=0.8433  bias=-0.0104
- **oppmodel56**: MAE=0.4847  RMSE=0.8425  bias=-0.0186
- Δ (opp-base): MAE -0.0033  RMSE -0.0009  |bias| +0.0082

### bench (L5<18min) (n=27835)
- base52 : MAE=0.4076  RMSE=0.7678  bias=-0.0026
- opp56  : MAE=0.4056  RMSE=0.7670  bias=-0.0013
- Δ: MAE -0.0020  RMSE -0.0008  |bias| -0.0013

### starters (L5>=28min) (n=8030)
- base52 : MAE=1.1843  RMSE=1.5516  bias=+0.0027
- opp56  : MAE=1.1855  RMSE=1.5515  bias=+0.0225
- Δ: MAE +0.0012  RMSE -0.0000  |bias| +0.0198

### Feature importance (opportunity features)
- `opp_expected_minutes` rank #4/56 importance=0.0240
- `opp_risk_score` rank #8/56 importance=0.0117
- `opp_bucket_high` rank #5/56 importance=0.0162
- `opp_bucket_low` rank #3/56 importance=0.0410
- opportunity features in top-20: ['opp_expected_minutes', 'opp_risk_score', 'opp_bucket_high', 'opp_bucket_low']

_eval 3PM took 102.6s_

## PRA

### Global (n=45587)
- **base52 **: MAE=5.7731  RMSE=8.5185  bias=+0.1033
- **oppmodel56**: MAE=5.7811  RMSE=8.5749  bias=+0.1223
- Δ (opp-base): MAE +0.0080  RMSE +0.0564  |bias| +0.0190

### Low-line (predicted < 20.0, n=37477)
- **base52 **: MAE=4.9306  RMSE=7.2679  bias=+0.0365
- **oppmodel56**: MAE=4.9184  RMSE=7.2693  bias=-0.0532
- Δ (opp-base): MAE -0.0122  RMSE +0.0014  |bias| +0.0167

### bench (L5<18min) (n=27835)
- base52 : MAE=4.2278  RMSE=6.7099  bias=+0.0466
- opp56  : MAE=4.2004  RMSE=6.7049  bias=+0.0686
- Δ: MAE -0.0274  RMSE -0.0050  |bias| +0.0220

### starters (L5>=28min) (n=8030)
- base52 : MAE=8.5500  RMSE=11.4483  bias=+0.1802
- opp56  : MAE=8.6284  RMSE=11.5704  bias=+0.3158
- Δ: MAE +0.0784  RMSE +0.1221  |bias| +0.1356

### Feature importance (opportunity features)
- `opp_expected_minutes` rank #2/56 importance=0.1602
- `opp_risk_score` rank #7/56 importance=0.0056
- `opp_bucket_high` rank #5/56 importance=0.0262
- `opp_bucket_low` rank #1/56 importance=0.6300
- opportunity features in top-20: ['opp_expected_minutes', 'opp_risk_score', 'opp_bucket_high', 'opp_bucket_low']

_eval PRA took 102.6s_

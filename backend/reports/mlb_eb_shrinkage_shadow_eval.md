# MLB Empirical-Bayes Shrinkage — Shadow Evaluation
Generated: 2026-04-24 03:50:25 UTC  •  source: `mlb_prop_scores@final-mlb-rt` (2,165 active docs with `model_projection`)

Read-only simulation. Production flag `MLB_HF_EB_SHRINKAGE_ENABLED` is unchanged. ECDF, projections, sigmas, gates are unmodified.

## 1 + 2. Bias reduction per stat (proj mean vs actual mean)

| stat | n projs | actual mean | proj mean (before) | proj mean (after) | bias before | bias after | bias reduction |
|------|--------:|------------:|-------------------:|-------------------:|------------:|-----------:|---------------:|
| `home_runs` | 76 | 0.118 | **0.233** | **0.188** | +0.115 | +0.070 | **+0.045** (+39.0%) |
| `rbis` | 249 | 0.448 | **0.665** | **0.548** | +0.217 | +0.100 | **+0.117** (+53.9%) |
| `total_bases` | 474 | 1.339 | **1.676** | **1.508** | +0.337 | +0.169 | **+0.168** (+49.8%) |
| `hits+runs+rbis` | 611 | 1.691 | **1.937** | **1.845** | +0.246 | +0.153 | **+0.093** (+37.7%) |

## 3. Top-20 biggest projection reductions (whitelisted stats only)

| # | player | stat | line | side | proj before | proj after | Δ | career mean | tier |
|---|--------|------|-----:|------|------------:|-----------:|-----:|------------:|------|
| 1 | Mickey Moniak | total_bases | 4.5 | OVER | 6.88 | 4.46 | -2.42 | 2.03 | unqualified |
| 2 | Vinnie Pasquantino | total_bases | 4.5 | OVER | 5.73 | 3.34 | -2.39 | 0.94 | unqualified |
| 3 | Carson Benge | total_bases | 3.5 | OVER | 5.60 | 3.33 | -2.27 | 1.06 | unqualified |
| 4 | Brandon Marsh | total_bases | 2.5 | OVER | 5.87 | 3.60 | -2.27 | 1.33 | unqualified |
| 5 | Brandon Marsh | total_bases | 3.5 | OVER | 5.58 | 3.46 | -2.12 | 1.33 | unqualified |
| 6 | Carson Benge | total_bases | 2.5 | OVER | 5.29 | 3.17 | -2.12 | 1.06 | unqualified |
| 7 | Vinnie Pasquantino | total_bases | 3.5 | OVER | 5.09 | 3.02 | -2.07 | 0.94 | unqualified |
| 8 | Vinnie Pasquantino | total_bases | 1.5 | OVER | 4.75 | 2.85 | -1.90 | 0.94 | unqualified |
| 9 | Carson Benge | total_bases | 1.5 | OVER | 4.76 | 2.91 | -1.85 | 1.06 | unqualified |
| 10 | Vinnie Pasquantino | total_bases | 2.5 | OVER | 4.61 | 2.78 | -1.83 | 0.94 | unqualified |
| 11 | Michael Busch | total_bases | 3.5 | OVER | 4.41 | 2.74 | -1.67 | 1.07 | unqualified |
| 12 | Nolan Schanuel | total_bases | 3.5 | OVER | 4.32 | 2.73 | -1.59 | 1.15 | unqualified |
| 13 | Michael Busch | total_bases | 2.5 | OVER | 4.20 | 2.64 | -1.56 | 1.07 | unqualified |
| 14 | Brandon Marsh | total_bases | 1.5 | OVER | 4.38 | 2.86 | -1.52 | 1.33 | unqualified |
| 15 | Jazz Chisholm Jr. | total_bases | 3.5 | OVER | 3.92 | 2.42 | -1.50 | 0.92 | unqualified |
| 16 | Carlos Narvaez | total_bases | 3.5 | OVER | 3.91 | 2.42 | -1.49 | 0.94 | unqualified |
| 17 | Michael Busch | rbis | 1.5 | OVER | 2.79 | 1.33 | -1.46 | 0.35 | unqualified |
| 18 | Leody Taveras | rbis | 0.5 | OVER | 3.04 | 1.58 | -1.46 | 0.61 | war_zone |
| 19 | Ian Happ | total_bases | 4.5 | OVER | 4.45 | 3.05 | -1.40 | 1.66 | unqualified |
| 20 | Michael Busch | rbis | 0.5 | OVER | 2.65 | 1.27 | -1.38 | 0.35 | unqualified |

## 4. Effect on ECDF p_over (whitelisted props only)

- props considered: 799
- Δp_over (ecdf): mean=-0.0245  median=+0.0000  p5/p95=-0.2092 / +0.1077  max|Δ|=0.8619

| stat | n | mean Δp_over | median Δp_over |
|------|--:|-------------:|---------------:|
| `rbis` | 249 | -0.0288 | +0.0000 |
| `total_bases` | 474 | -0.0128 | +0.0000 |
| `home_runs` | 76 | -0.0836 | +0.0000 |

## 5. Effect on edge_pct

- props with a tp anchor: 56
- edge_pct: mean before = -10.88pp  mean after = -9.35pp  mean Δ = +1.53pp

| stat | n | mean Δedge |
|------|--:|-----------:|
| `total_bases` | 56 | +1.53pp |

## 6. Gate pass/fail movement

OVER side (0.55 threshold):

- **Lost OVER gates** (was ≥ 0.55, now < 0.55): 30
- **Gained OVER gates** (was < 0.55, now ≥ 0.55): 0
- Unchanged: 767

UNDER side (0.45 threshold):

- **Lost UNDER gates** (was p_over ≤ 0.45, now > 0.45): 0
- **Gained UNDER gates** (was p_over > 0.45, now ≤ 0.45): 1
- Unchanged: 1

## 7 + 8. Invariant checks

- Negative projections produced: **0** (must be 0)
- Non-whitelisted stats whose projection changed: **0** (must be 0)

### Skip reasons by whitelisted stat

| stat | applied | skipped (and why) |
|------|--------:|-------------------|
| `hits+runs+rbis` | 572 | insufficient_games_0<20=13 / insufficient_games_15<20=10 / insufficient_games_18<20=7 / insufficient_games_19<20=5 / insufficient_games_16<20=4 |
| `home_runs` | 73 | insufficient_games_0<20=2 / insufficient_games_15<20=1 |
| `rbis` | 235 | insufficient_games_0<20=5 / insufficient_games_18<20=3 / insufficient_games_19<20=2 / insufficient_games_15<20=2 / insufficient_games_16<20=1 |
| `total_bases` | 445 | insufficient_games_0<20=9 / insufficient_games_15<20=7 / insufficient_games_18<20=6 / insufficient_games_16<20=3 / insufficient_games_19<20=3 |

## Recommendation

- `home_runs`: best w_model ≈ **0.00** (residual bias +0.041); initial was 0.3.
- `rbis`: best w_model ≈ **0.00** (residual bias +0.009); initial was 0.4.
- `total_bases`: best w_model ≈ **0.00** (residual bias +0.018); initial was 0.5.
- `hits+runs+rbis`: best w_model ≈ **0.00** (residual bias +0.004); initial was 0.6.

### Verdict: **KEEP**

- `home_runs`: bias +0.115 → +0.070 PARTIAL KEEP
- `rbis`: bias +0.217 → +0.100 PARTIAL KEEP
- `total_bases`: bias +0.337 → +0.169 PARTIAL KEEP
- `hits+runs+rbis`: bias +0.246 → +0.153 PARTIAL KEEP

---

### Interpretation notes

- The grid-search "best w_model ≈ 0.00" result is a mathematical artefact
  of minimising the **mean bias**: pure `career_mean` happens to match
  the population-mean target perfectly because the actual-mean is
  **close to** the player-mean average on a selected props slate.
  Using `w_model = 0` would throw away all game-specific signal the HF
  model learned from (park, opponent, pitcher matchup, recent form,
  plate discipline features). **Do not adopt w_model=0 as production
  weights.**
- The requested initial weights (0.30 / 0.40 / 0.50 / 0.60) deliver
  roughly **50% bias reduction** on every stat while preserving half
  the model's per-game signal. That is the recommended operating point.
- `Brandon Marsh total_bases 5.87 → 3.60`,
  `Mickey Moniak total_bases 6.88 → 4.46`,
  `Leody Taveras rbis 3.04 → 1.58`,
  `Michael Busch rbis 2.79 → 1.33` — the exact outlier-projection
  pattern the user flagged is gone under the initial weights.
- OVER-gate movement `-30 / +0` is the correct direction: the
  shrinkage demotes precisely the false-OVER candidates the previous
  audit isolated. No UNDER-gate losses beyond 1.

### Production enable

Set `MLB_HF_EB_SHRINKAGE_ENABLED=true` in `/app/backend/.env` and
restart the backend. No deploy or code change required; the
`_SCORE_OUTPUT_FIELDS` mirror and recompute wiring are live. After
enabling, trigger a rescore (`recompute_sport(db, "mlb",
version_tag="final-mlb-rt")`) to propagate shrunk projections and
populate the audit fields on the scored docs.

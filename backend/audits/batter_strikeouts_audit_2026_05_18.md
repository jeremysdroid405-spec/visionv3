# Batter Strikeouts Audit — Witt vs Garcia

_Generated 2026-05-16T20:33:45.122194+00:00_


Line: 0.5  Side: OVER  Stat: Batter Strikeouts



## 1. Persisted score-doc snapshot (latest, alternate, OVER 0.5)

| Field | Bobby Witt Jr. | Maikel Garcia |
|---|---|---|
| computed_at | 2026-05-16T20:16:09.399596+00:00 | 2026-05-16T20:16:09.399596+00:00 |
| routed_tier | war_zone | war_zone |
| tier | unqualified | war_zone |
| vision_score | 4.1000 | 100.0000 |
| tp | 32.8000 | 27.8000 |
| fair_prob | 0.3279 | 0.2778 |
| edge | — | — |
| predicted | — | — |
| mu_raw_model_projection | 0.3187 | 1.1949 |
| std_dev | — | — |
| z_score | — | — |
| prob_over | — | — |
| market_class | alternate | alternate |
| is_alternate_market | True | True |


## 2. Raw L20 game logs (strikeouts per game)


### Bobby Witt Jr.

team=KC, bat_side=None, in_lineup=None, n_logs_total=47

| # | Date | K | PA | AB | Opp |
|---|---|---|---|---|---|
| 1 | 2026-05-06 | 1 | 4 | 4 | CLE |
| 2 | 2026-05-05 | 1 | 4 | 4 | CLE |
| 3 | 2026-05-04 | 1 | 4 | 4 | CLE |
| 4 | 2026-05-03 | 3 | 5 | 5 | SEA |
| 5 | 2026-05-03 | 1 | 5 | 5 | SEA |
| 6 | 2026-05-02 | 1 | 5 | 4 | SEA |
| 7 | 2026-04-30 | 1 | 5 | 3 | OAK |
| 8 | 2026-04-30 | 1 | 4 | 4 | OAK |
| 9 | 2026-04-29 | 0 | 5 | 5 | OAK |
| 10 | 2026-04-26 | 0 | 5 | 5 | LAA |
| 11 | 2026-04-25 | 0 | 6 | 5 | LAA |
| 12 | 2026-04-24 | 0 | 4 | 4 | LAA |
| 13 | 2026-04-22 | 1 | 5 | 5 | BAL |
| 14 | 2026-04-21 | 0 | 5 | 3 | BAL |
| 15 | 2026-04-20 | 1 | 6 | 5 | BAL |
| 16 | 2026-04-19 | 1 | 4 | 3 | NYY |
| 17 | 2026-04-18 | 1 | 3 | 3 | NYY |
| 18 | 2026-04-17 | 1 | 4 | 4 | NYY |
| 19 | 2026-04-16 | 2 | 5 | 5 | DET |
| 20 | 2026-04-15 | 1 | 4 | 4 | DET |

**Averages:** L5=1.4000  L10=1.0000  L20=0.9000

**Hit-rate >0.5:** L5=100.0%  L10=80.0%  L20=75.0%

**PA avg:** L5=4.40  L10=4.60


### Maikel Garcia

team=KC, bat_side=None, in_lineup=None, n_logs_total=44

| # | Date | K | PA | AB | Opp |
|---|---|---|---|---|---|
| 1 | 2026-05-06 | 0 | 4 | 3 | CLE |
| 2 | 2026-05-05 | 1 | 4 | 3 | CLE |
| 3 | 2026-05-04 | 0 | 4 | 4 | CLE |
| 4 | 2026-05-03 | 0 | 5 | 5 | SEA |
| 5 | 2026-05-03 | 1 | 5 | 4 | SEA |
| 6 | 2026-05-02 | 1 | 5 | 4 | SEA |
| 7 | 2026-04-30 | 0 | 5 | 5 | OAK |
| 8 | 2026-04-30 | 1 | 4 | 4 | OAK |
| 9 | 2026-04-29 | 1 | 5 | 5 | OAK |
| 10 | 2026-04-26 | 0 | 2 | 1 | LAA |
| 11 | 2026-04-22 | 1 | 3 | 3 | BAL |
| 12 | 2026-04-21 | 1 | 5 | 3 | BAL |
| 13 | 2026-04-20 | 1 | 6 | 6 | BAL |
| 14 | 2026-04-19 | 2 | 4 | 4 | NYY |
| 15 | 2026-04-18 | 1 | 4 | 4 | NYY |
| 16 | 2026-04-17 | 1 | 4 | 3 | NYY |
| 17 | 2026-04-16 | 1 | 5 | 5 | DET |
| 18 | 2026-04-15 | 1 | 4 | 4 | DET |
| 19 | 2026-04-14 | 1 | 4 | 4 | DET |
| 20 | 2026-04-12 | 0 | 5 | 4 | CHW |

**Averages:** L5=0.4000  L10=0.5000  L20=0.7500

**Hit-rate >0.5:** L5=40.0%  L10=50.0%  L20=70.0%

**PA avg:** L5=4.40  L10=4.30



## 3. Live predict() reproduction (clean re-run)

Model pickle: `mlb_hf_strikeouts.pkl`  norm_stat: `strikeouts`  (no μ-override fires for this stat — not in `_ACTIVE_BASELINE`, not pitcher).

| Field | Bobby Witt Jr. | Maikel Garcia |
|---|---|---|
| raw_pred_from_model | 0.3191 | 1.2112 |
| park_factor | 1.0000 | 1.0000 |
| final_pred_mu | 0.3191 | 1.2112 |
| std_dev_used | 0.8165 | 0.5270 |
| z_score | 0.2216 | -1.3494 |
| prob_over_pct | 41.2311 | 91.1402 |
| imputed_features_count | 10 | 10 |


## 4. Key feature values side-by-side

| Feature | Bobby Witt Jr. | Maikel Garcia |
|---|---|---|
| l3_avg | 1.0000 | 0.3333 |
| l5_avg | 1.4000 | 0.4000 |
| l10_avg | 1.0000 | 0.5000 |
| l20_avg | 0.9000 | 0.7500 |
| ewma_l5 | 1.5000 | 0.5625 |
| ewma_l10 | 0.5606 | 0.4967 |
| ewma_l20 | 1.0198 | 0.7954 |
| ewma_trend | 1.6758 | 0.1324 |
| std_dev_l5 | 0.8944 | 0.5477 |
| std_dev_l10 | 0.8165 | 0.5270 |
| cv_l5 | 0.6389 | 1.3693 |
| cv_l10 | 0.8165 | 1.0541 |
| l5_max | 3.0000 | 1.0000 |
| l10_max | 3.0000 | 1.0000 |
| l5_min | 1.0000 | 0.0000 |
| l10_min | 0.0000 | 0.0000 |
| range_l5 | 2.0000 | 1.0000 |
| range_l10 | 3.0000 | 1.0000 |
| hit_rate_l5 | 100.0000 | 40.0000 |
| hit_rate_l10 | 80.0000 | 50.0000 |
| current_hit_streak | 8 | 0 |
| current_miss_streak | 0 | 1 |
| line | 0.5000 | 0.5000 |
| line_vs_l5 | -0.9000 | 0.1000 |
| line_vs_l10 | -0.5000 | 0.0000 |
| line_vs_ewma | -0.0606 | 0.0033 |
| line_vs_median | -0.5000 | 0.0000 |
| line_difficulty | -0.6124 | 0.0000 |
| park_factor | 1.0000 | 1.0000 |
| park_k_factor | 1.0000 | 1.0000 |
| opp_k_rate | 1.1000 | 1.1000 |
| vs_lhp_avg | 0.3282 | 0.3136 |
| vs_rhp_avg | 0.2866 | 0.2788 |
| vs_lhp_k_rate | 0.2290 | 0.1695 |
| vs_rhp_k_rate | 0.1931 | 0.1342 |
| platoon_k_split | 0.0359 | 0.0353 |
| platoon_split_is_imputed | 0 | 0 |
| vs_lhp_is_imputed | 0 | 0 |
| vs_rhp_is_imputed | 0 | 0 |
| home_avg | 0.2862 | 0.2799 |
| away_avg | 0.3041 | 0.2914 |
| home_away_split | -0.0179 | -0.0115 |
| home_away_split_is_imputed | 0 | 0 |
| expected_pa_l10 | 4.6000 | 4.3000 |
| expected_pa_is_imputed | 0 | 0 |
| sc_b_r7_k_rate | 0.1434 | 0.2120 |
| sc_b_r14_k_rate | 0.1853 | 0.2359 |
| sc_b_r30_k_rate | 0.1772 | 0.1866 |
| sc_b_r7_whiff_rate | 0.2052 | 0.2426 |
| sc_b_r7_contact_rate | 0.7948 | 0.7574 |
| sc_batter_is_imputed | 0 | 0 |
| pa_b_pa14_k_rate | 0.0000 | 0.2143 |
| pa_b_pa30_k_rate | 0.0333 | 0.2333 |
| pa_b_pa7_k_rate | 0.0000 | 0.1429 |
| pa_b_pa30_whiff_rate | 0.1695 | 0.2051 |
| pa_batter_is_imputed | 0 | 0 |


## 5. Imputed-feature counts


### Bobby Witt Jr.

`imputed_count = 10`

<details><summary>Imputed list</summary>

```

batter_hand
lineup_handedness
lineup_size
lineup_strength
matchup
matchup_exposure
opp_pitcher_quality
opp_pitcher_throws
pa_pitcher
sc_pitcher

```

</details>


### Maikel Garcia

`imputed_count = 10`

<details><summary>Imputed list</summary>

```

batter_hand
lineup_handedness
lineup_size
lineup_strength
matchup
matchup_exposure
opp_pitcher_quality
opp_pitcher_throws
pa_pitcher
sc_pitcher

```

</details>

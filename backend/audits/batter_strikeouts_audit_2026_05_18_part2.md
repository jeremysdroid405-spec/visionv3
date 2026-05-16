# Batter Strikeouts Audit — Part 2: Feature-Swap Probe
_Generated 2026-05-16T20:43:37.143353+00:00_

## 1. Data staleness gap
- Newest `mlb_statcast_player_features.game_date`: **2026-04-26**
- **Bobby Witt Jr.**: newest BDL log = `2026-05-06` | latest Statcast doc available = `2026-04-26` (frozen)
- **Maikel Garcia**: newest BDL log = `2026-05-06` | latest Statcast doc available = `2026-04-26` (frozen)

**Gap: 10 calendar days / ~9-10 games of BDL data NOT reflected in any Statcast / PA feature.**

## 2. Baseline inference (matches Part 1)
- Bobby Witt Jr.: raw_pred = **0.7160**
- Maikel Garcia: raw_pred = **1.6340**

## 3. Swap probe: hold BDL identity, swap Statcast/PA features
Each row holds the player's own BDL game logs, vs-LHP/RHP splits, park team, etc. — only the Statcast (`sc_b_*`) and PA-windowed (`pa_b_*`) feature blocks are sourced from the *other* player.

| Variant | μ |
|---|---|
| Witt BDL × **Garcia** Statcast/PA | **1.0092** |
| Garcia BDL × **Witt** Statcast/PA | **0.4616** |

Baseline: Witt μ=0.7160  |  Garcia μ=1.6340

## 4. Manual repair probe: override SC K-rate features with BDL-derived recent K rate
Replaces ALL `sc_b_r{7,14,30}_k_rate`, `sc_b_season_k_rate`, `pa_b_pa{7,14,30}_k_rate`, `pa_b_pa_season_k_rate` with the player's actual L14 BDL K-per-PA. All other features unchanged.

| Player | L14 K/PA (BDL) | original SC r14_k_rate | μ original | μ with SC→L14 K rate |
|---|---|---|---|---|
| Bobby Witt Jr. | 0.1667 | 0.16363636363636364 | 0.7160 | **0.5785** |
| Maikel Garcia | 0.1639 | 0.24390243902439024 | 1.6340 | **0.2401** |

## 5. One-feature-group-at-a-time swap (Witt → Garcia)
Starts from Witt baseline. Swaps in Garcia's value for the named feature group only, keeps all others as Witt's. Shows the marginal impact of each block.

| Swap group | n_feats | μ (Witt baseline = 0.7160) |
|---|---|---|
| l3_avg+l5_avg+l10_avg+l20_avg | 4 | 0.6773 |
| ewma_l5+ewma_l10+ewma_l20+ewma_trend | 4 | 0.6628 |
| hit_rate_l5+hit_rate_l10 | 2 | 0.7061 |
| current_hit_streak+current_miss_streak | 2 | 0.7816 |
| line_vs_*+line_difficulty | 5 | 0.6133 |
| std_dev_l5+l10+cv_l5+l10+range_l5+l10+l5_max+l10_max+l5_min+l10_min | 12 | 0.6886 |
| vs_lhp_*+vs_rhp_*+platoon_* | 21 | 0.7174 |
| sc_b_r7_* | 11 | 0.8491 |
| sc_b_r14_* | 11 | 0.9243 |
| sc_b_r30_* | 11 | 0.6991 |
| sc_b_season_* | 11 | 0.8116 |
| pa_b_pa7_* | 12 | 0.7160 |
| pa_b_pa14_* | 12 | 0.7160 |
| pa_b_pa30_* | 12 | 0.7160 |
| pa_b_pa_season_* | 12 | 0.7160 |
| expected_pa_l10 | 2 | 0.7202 |

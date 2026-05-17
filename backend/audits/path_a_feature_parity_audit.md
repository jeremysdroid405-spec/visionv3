# Path A Task 1 — Feature Parity Audit

_Generated 2026-05-17T04:28:08.158332Z — batters=30, pitchers=30_

## Per-stat-family schema coverage

| stat_family | train cols | live emitted | replay emitted | replay+ emitted | high-gap (≥25%) |
|---|---:|---:|---:|---:|---:|
| hits | 222 | 243 | 243 | 243 | 1 |
| total_bases | 222 | 243 | 243 | 243 | 1 |
| runs | 222 | 243 | 243 | 243 | 1 |
| rbis | 222 | 243 | 243 | 243 | 1 |
| home_runs | 208 | 243 | 243 | 243 | 0 |
| pitcher_strikeouts | 243 | 243 | 243 | 243 | 2 |
| pitcher_walks | 243 | 243 | 243 | 243 | 2 |
| earned_runs | 243 | 243 | 243 | 243 | 2 |

## Category averages (mean population %)

| category | feats | live avg | replay avg | replay+ avg | live−replay gap |
|---|---:|---:|---:|---:|---:|
| pa_batter | 392 | 14.3% | 2.0% | 16.5% | **12.3** |
| pa_pitcher | 200 | 5.0% | 4.0% | 5.0% | **1.0** |
| other | 140 | 59.0% | 58.7% | 58.7% | **0.3** |
| batter_handedness | 28 | 25.0% | 25.0% | 25.0% | **0.0** |
| opp_pitcher_quality | 49 | 28.6% | 28.6% | 28.6% | **0.0** |
| matchup_interaction | 14 | 0.0% | 0.0% | 0.0% | **0.0** |
| environment_park | 56 | 85.7% | 85.7% | 85.7% | **0.0** |
| statcast_pitcher | 168 | 1.0% | 1.0% | 1.0% | **0.0** |
| opposing_lineup | 42 | 21.4% | 21.4% | 21.4% | **0.0** |
| workload | 16 | 66.8% | 67.0% | 67.0% | **-0.2** |
| platoon_splits | 168 | 19.1% | 20.0% | 20.0% | **-0.9** |
| rolling_windows | 192 | 62.0% | 64.2% | 64.2% | **-2.2** |
| statcast_batter | 352 | 13.9% | 16.3% | 16.3% | **-2.4** |
| market_alignment | 8 | 79.7% | 82.3% | 82.3% | **-2.6** |

## Top-20 high-gap features per family

### hits

| feature | category | live % | replay % | gap | imputed flag |
|---|---|---:|---:|---:|---|
| `opp_pitcher_throws_r` | opp_pitcher_quality | 100.0% | 0.0% | **100.0** | opp_pitcher_quality_is_imputed |

### total_bases

| feature | category | live % | replay % | gap | imputed flag |
|---|---|---:|---:|---:|---|
| `opp_pitcher_throws_r` | opp_pitcher_quality | 100.0% | 0.0% | **100.0** | opp_pitcher_quality_is_imputed |

### runs

| feature | category | live % | replay % | gap | imputed flag |
|---|---|---:|---:|---:|---|
| `opp_pitcher_throws_r` | opp_pitcher_quality | 100.0% | 0.0% | **100.0** | opp_pitcher_quality_is_imputed |

### rbis

| feature | category | live % | replay % | gap | imputed flag |
|---|---|---:|---:|---:|---|
| `opp_pitcher_throws_r` | opp_pitcher_quality | 100.0% | 0.0% | **100.0** | opp_pitcher_quality_is_imputed |

### pitcher_strikeouts

| feature | category | live % | replay % | gap | imputed flag |
|---|---|---:|---:|---:|---|
| `lineup_size` | opposing_lineup | 100.0% | 0.0% | **100.0** | — |
| `opp_pitcher_throws_r` | opp_pitcher_quality | 100.0% | 0.0% | **100.0** | opp_pitcher_quality_is_imputed |

### pitcher_walks

| feature | category | live % | replay % | gap | imputed flag |
|---|---|---:|---:|---:|---|
| `lineup_size` | opposing_lineup | 100.0% | 0.0% | **100.0** | — |
| `opp_pitcher_throws_r` | opp_pitcher_quality | 100.0% | 0.0% | **100.0** | opp_pitcher_quality_is_imputed |

### earned_runs

| feature | category | live % | replay % | gap | imputed flag |
|---|---|---:|---:|---:|---|
| `lineup_size` | opposing_lineup | 100.0% | 0.0% | **100.0** | — |
| `opp_pitcher_throws_r` | opp_pitcher_quality | 100.0% | 0.0% | **100.0** | opp_pitcher_quality_is_imputed |


## Restoration priority (recommended order)

Ranked by `live − replay` gap × number of stat families affected:

| rank | category | weighted gap score |
|---|---|---:|
| 1 | opp_pitcher_quality | 700.0 |
| 2 | opposing_lineup | 300.0 |
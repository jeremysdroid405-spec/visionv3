# Database Inventory Report
_Generated: 2026-05-30T05:24:33.728628+00:00_

## Executive Summary

- **Collections audited:** 22
- **Collections present:** 17
- **Collections missing:** 5
- **Total documents:** 4,550,066
- **Total storage:** 426.9 MB
- **Total index storage:** 310.1 MB
- **Sports observed:** mlb, nba, nfl
- **Seasons observed:** 2024, 2025, 2026

### Dataset Status

| sport | matchups | team props | player props | graded outcomes | model-ready? |
|---|---|---|---|---|---|
| MLB | 5,401 | 1,768,165 | 0 | 0 | ⚠️ |
| NBA | 2,416 | 1,136,293 | 0 | 0 | ⚠️ |
| NFL | 659 | 316,474 | 1,188,943 | 0 | ⚠️ |

**Status legend:** ✅ matchups + props + outcomes  •  ⚠️ matchups + props, NO outcomes  •  ❌ missing core data

**Per user instruction:** all data above is acquisition-only — modeling, grading, and UI work are frozen.

## 1. Collection Inventory

| collection | docs | storage | indexes | sports | earliest | latest |
|---|---|---|---|---|---|---|
| sgo_pp_research_core | 0 | 12.0 KB | 6 | — | — | — |
| sgo_pp_research_core_enriched | 0 | 24.0 KB | 1 | — | — | — |
| sgo_pp_research_outcomes | 0 | 12.0 KB | 13 | — | — | — |
| sgo_pp_research_model_features | 0 | 4.0 KB | 11 | — | — | — |
| sgo_pp_research_model_predictions | _missing_ | — | — | — | — | — |
| sgo_props_raw | _missing_ | — | — | — | — | — |
| sgo_replay_alt_odds_raw | 0 | 12.0 KB | 5 | — | — | — |
| sgo_book_consensus | _missing_ | — | — | — | — | — |
| sgo_odds_outcomes | _missing_ | — | — | — | — | — |
| sgo_player_stats | 1 | 36.0 KB | 9 | — | 2099-01-01 | 2099-01-01 |
| sgo_team_stats | _missing_ | — | — | — | — | — |
| mlb_prop_scores | 89,141 | 63.7 MB | 10 | mlb | 2026-05-18T22:41:00Z | 2026-05-19T23:46:00Z |
| nba_prop_scores | 42,561 | 79.5 MB | 10 | nba | 2026-05-18T00:10:00Z | 2026-05-20T00:10:00Z |
| nfl_player_historical_props | 1,188,943 | 82.0 MB | 1 | nfl | 2024-09-06 | 2026-02-08 |
| team_matchups | 7,817 | 1.2 MB | 3 | mlb, nba | 2024-02-01 | 2026-02-08 |
| team_historical_props | 2,904,172 | 179.6 MB | 3 | mlb, nba | 2024-07-05 | 2026-02-08 |
| team_live_props | 286 | 64.0 KB | 3 | mlb | 2025-06-15 | 2025-06-15 |
| nfl_matchups | 659 | 112.0 KB | 4 | nfl | 2024-02-11 | 2026-02-08 |
| nfl_historical_props | 316,474 | 19.3 MB | 4 | nfl | 2024-08-24 | 2026-02-08 |
| team_prop_outcomes | 0 | 4.0 KB | 3 | — | — | — |
| historical_acquire_runs | 10 | 1.3 MB | 5 | mlb, nba, nfl | 2026-05-29 23:19:01.835000 | 2026-05-30 05:15:16.867000 |
| team_odds_ingest_runs | 2 | 48.0 KB | 8 | mlb | 2026-05-29 05:31:46.763000 | 2026-05-29 05:39:14.512000 |

### Index details

- **sgo_pp_research_core** (6 indexes, 32.0 KB idx storage):  _id_, game_date_1, league_id_1, player_id_1, pp_anchor_pk, stat_id_1
- **sgo_pp_research_core_enriched** (1 indexes, 24.0 KB idx storage):  _id_
- **sgo_pp_research_outcomes** (13 indexes, 156.0 KB idx storage):  _id_, edge_vs_consensus_1, game_date_1, grading_version_1, has_valid_devig_1, hit_1, league_id_1, outcome_1, outcome_anchor_pk, outcome_resolved_1, player_id_1, stat_family_1, stat_id_1
- **sgo_pp_research_model_features** (11 indexes, 44.0 KB idx storage):  _id_, edge_vs_consensus_1, feature_anchor_pk, feature_ready_1, feature_version_1, game_date_1, has_valid_devig_1, league_id_1, player_id_1, stat_family_1, stat_id_1
- **sgo_replay_alt_odds_raw** (5 indexes, 60.0 KB idx storage):  _id_, alt_odds_compound_unique_v2, event_id_1, game_date_1, snapshot_iso_1
- **sgo_player_stats** (9 indexes, 324.0 KB idx storage):  _id_, event_player, league_date_event, league_date_event_player, league_event_player, league_id_1_game_date_1, league_player_date, player_name_1_game_date_1, source_1
- **mlb_prop_scores** (10 indexes, 5.1 MB idx storage):  _id_, idx_computed_at_desc, idx_game_start_active, idx_pp_utility_desc, idx_tier, idx_tier_active_vision, idx_vision_score_desc, ttl_at_7d_nonlive_ix, ttl_purge_at_ephemeral_ix, uniq_canonical_version
- **nba_prop_scores** (10 indexes, 1.4 MB idx storage):  _id_, idx_computed_at_desc, idx_game_start_active, idx_pp_utility_desc, idx_tier, idx_tier_active_vision, idx_vision_score_desc, ttl_at_7d_nonlive_ix, ttl_purge_at_ephemeral_ix, uniq_canonical_version
- **nfl_player_historical_props** (1 indexes, 14.7 MB idx storage):  _id_
- **team_matchups** (3 indexes, 764.0 KB idx storage):  _id_, ix_matchup_date_sport, ix_matchup_event_id_unique
- **team_historical_props** (3 indexes, 257.1 MB idx storage):  _id_, ix_hist_prop_compound_unique, ix_hist_prop_team_market_date
- **team_live_props** (3 indexes, 88.0 KB idx storage):  _id_, ix_live_prop_compound_unique, ix_live_prop_date_sport
- **nfl_matchups** (4 indexes, 104.0 KB idx storage):  _id_, ix_nfl_matchup_date, ix_nfl_matchup_sport_event_unique, ix_nfl_matchup_status
- **nfl_historical_props** (4 indexes, 29.8 MB idx storage):  _id_, ix_nfl_hist_prop_compound_unique, ix_nfl_hist_prop_date, ix_nfl_hist_prop_market_date
- **team_prop_outcomes** (3 indexes, 12.0 KB idx storage):  _id_, ix_outcome_compound_unique, ix_outcome_date_sport
- **historical_acquire_runs** (5 indexes, 180.0 KB idx storage):  _id_, ix_hist_acquire_run_id_unique, ix_hist_acquire_sport, ix_hist_acquire_started_at, ix_hist_acquire_status
- **team_odds_ingest_runs** (8 indexes, 288.0 KB idx storage):  _id_, ix_dry_run, ix_finished_at, ix_live_write_allowed, ix_run_id_unique, ix_sport, ix_started_at, ix_status

## 2. Sport Coverage

### MLB

- Seasons present: 2024, 2025
- Date range: 2024-07-05 → 2025-10-21
- Event counts by collection:
  - `team_historical_props`: 3,565
  - `nfl_historical_props`: 0
  - `nfl_player_historical_props`: 0
  - `team_live_props`: 1
  - `team_matchups`: 5,401
  - `nfl_matchups`: 0
- Matchup counts:
  - `team_matchups`: 5,401
- Team-prop counts:
  - `team_historical_props`: 1,767,879
  - `team_live_props`: 286
- Player-prop counts:
  - `sgo_pp_research_core_enriched`: 0
  - `sgo_pp_research_core`: 0
- Outcome coverage:
  - `team_prop_outcomes`: graded=0 ungraded=0
  - `sgo_pp_research_outcomes`: graded=0 ungraded=0

### NBA

- Seasons present: 2024, 2025, 2026
- Date range: 2024-10-10 → 2026-02-08
- Event counts by collection:
  - `team_historical_props`: 1,711
  - `nfl_historical_props`: 0
  - `nfl_player_historical_props`: 0
  - `team_live_props`: 0
  - `team_matchups`: 2,416
  - `nfl_matchups`: 0
- Matchup counts:
  - `team_matchups`: 2,416
- Team-prop counts:
  - `team_historical_props`: 1,136,293
  - `team_live_props`: 0
- Player-prop counts:
  - `sgo_pp_research_core_enriched`: 0
  - `sgo_pp_research_core`: 0
- Outcome coverage:
  - `team_prop_outcomes`: graded=0 ungraded=0
  - `sgo_pp_research_outcomes`: graded=0 ungraded=0

### NFL

- Seasons present: 2024, 2025, 2026
- Date range: 2024-08-24 → 2026-02-08
- Event counts by collection:
  - `team_historical_props`: 0
  - `nfl_historical_props`: 606
  - `nfl_player_historical_props`: 567
  - `team_live_props`: 0
  - `team_matchups`: 0
  - `nfl_matchups`: 659
- Matchup counts:
  - `team_matchups`: 0
  - `nfl_matchups`: 659
- Team-prop counts:
  - `team_historical_props`: 0
  - `team_live_props`: 0
  - `nfl_historical_props`: 316,474
- Player-prop counts:
  - `nfl_player_historical_props`: 1,188,943
  - `sgo_pp_research_core_enriched`: 0
  - `sgo_pp_research_core`: 0
- Outcome coverage:
  - `team_prop_outcomes`: graded=0 ungraded=0
  - `sgo_pp_research_outcomes`: graded=0 ungraded=0

## 3. Market / Prop Inventory

### team_historical_props

- Distinct markets : 250
- Distinct books   : 67
- Distinct players : 0
- Distinct teams   : 61
- Top markets:
  - `points-home-game-ml-home`: 213,548
  - `points-away-game-ml-away`: 213,513
  - `points-all-game-ou-under`: 168,618
  - `points-all-game-ou-over`: 168,466
  - `points-home-game-sp-home`: 167,748
  - `points-away-game-sp-away`: 167,686
  - `points-all-1ix5-ou-under`: 51,630
  - `points-all-1ix5-ou-over`: 51,532
  - `points-home-1ix5-sp-home`: 51,253
  - `points-away-1ix5-sp-away`: 51,148
- Top books:
  - `pinnacle`: 306,386
  - `draftkings`: 174,819
  - `fanduel`: 160,947
  - `ballybet`: 142,715
  - `betmgm`: 137,623
  - `hardrockbet`: 132,226
  - `bovada`: 131,524
  - `betrivers`: 128,560
  - `fanatics`: 125,745
  - `espnbet`: 113,702

### nfl_historical_props

- Distinct markets : 123
- Distinct books   : 64
- Distinct players : 0
- Distinct teams   : 33
- Top markets:
  - `points-away-game-ml-away`: 20,059
  - `points-home-game-ml-home`: 20,051
  - `points-home-game-sp-home`: 16,655
  - `points-away-game-sp-away`: 16,648
  - `points-all-game-ou-under`: 16,283
  - `points-all-game-ou-over`: 16,273
  - `points-home-1h-sp-home`: 6,695
  - `points-away-1h-sp-away`: 6,677
  - `points-all-1h-ou-under`: 6,485
  - `points-all-1h-ou-over`: 6,475
- Top books:
  - `hardrockbet`: 22,016
  - `draftkings`: 21,452
  - `fanduel`: 18,477
  - `bet365`: 17,769
  - `ballybet`: 15,972
  - `bovada`: 15,556
  - `pinnacle`: 15,258
  - `betparx`: 14,843
  - `betrivers`: 14,080
  - `bookmakereu`: 13,287

### nfl_player_historical_props

- Distinct markets : 26,124
- Distinct books   : 26
- Distinct players : 1,572
- Distinct teams   : 0
- Top markets:
  - `receiving_yards-HUNTER_HENRY_1_NFL-game-ou-over`: 438
  - `receiving_receptions-HUNTER_HENRY_1_NFL-game-ou-over`: 435
  - `receiving_yards-HUNTER_HENRY_1_NFL-game-ou-under`: 417
  - `passing_yards-MATTHEW_STAFFORD_1_NFL-game-ou-over`: 415
  - `receiving_yards-RASHID_SHAHEED_1_NFL-game-ou-over`: 413
  - `receiving_yards-STEFON_DIGGS_1_NFL-game-ou-over`: 406
  - `receiving_receptions-RASHID_SHAHEED_1_NFL-game-ou-over`: 406
  - `receiving_receptions-STEFON_DIGGS_1_NFL-game-ou-over`: 405
  - `receiving_yards-DALTON_SCHULTZ_1_NFL-game-ou-over`: 404
  - `receiving_yards-DJ_MOORE_1_NFL-game-ou-over`: 402
- Top books:
  - `draftkings`: 102,075
  - `underdog`: 94,572
  - `hardrockbet`: 92,593
  - `fanduel`: 83,877
  - `bovada`: 79,868
  - `prizepicks`: 79,544
  - `espnbet`: 75,905
  - `fanatics`: 70,438
  - `betmgm`: 69,725
  - `betrivers`: 62,681

### team_live_props

- Distinct markets : 6
- Distinct books   : 55
- Distinct players : 0
- Distinct teams   : 3
- Top markets:
  - `points-away-game-ml-away`: 55
  - `points-home-game-ml-home`: 55
  - `points-all-game-ou-under`: 44
  - `points-all-game-ou-over`: 44
  - `points-away-game-sp-away`: 44
  - `points-home-game-sp-home`: 44
- Top books:
  - `betonline`: 6
  - `grosvenor`: 6
  - `sugarhouse`: 6
  - `ladbrokes`: 6
  - `betparx`: 6
  - `draftkings`: 6
  - `betmgm`: 6
  - `1xbet`: 6
  - `everygame`: 6
  - `lowvig`: 6

### team_matchups

- Distinct markets : 0
- Distinct books   : 0
- Distinct players : 0
- Distinct teams   : 0
- Top markets:
  - `None`: 7,817
- Top books:
  - `None`: 7,817

### nfl_matchups

- Distinct markets : 0
- Distinct books   : 0
- Distinct players : 0
- Distinct teams   : 0
- Top markets:
  - `None`: 659
- Top books:
  - `None`: 659

### sgo_pp_research_core

- Distinct markets : 0
- Distinct books   : 0
- Distinct players : 0
- Distinct teams   : 0

### sgo_pp_research_core_enriched

- Distinct markets : 0
- Distinct books   : 0
- Distinct players : 0
- Distinct teams   : 0

### sgo_pp_research_outcomes

- Distinct markets : 0
- Distinct books   : 0
- Distinct players : 0
- Distinct teams   : 0

### mlb_prop_scores

- Distinct markets : 0
- Distinct books   : 0
- Distinct players : 0
- Distinct teams   : 0
- Top markets:
  - `None`: 11,600
- Top books:
  - `None`: 11,600

### nba_prop_scores

- Distinct markets : 0
- Distinct books   : 0
- Distinct players : 0
- Distinct teams   : 0
- Top markets:
  - `None`: 3,811
- Top books:
  - `None`: 3,811

## 4. Book Coverage

- **team_historical_props** — distinct books per sport: mlb=63, nba=62
- **nfl_historical_props** — distinct books per sport: nfl=64
- **nfl_player_historical_props** — distinct books per sport: nfl=26
- **team_live_props** — distinct books per sport: mlb=55
- **team_matchups** — distinct books per sport: mlb=1, nba=1
- **nfl_matchups** — distinct books per sport: nfl=1
- **mlb_prop_scores** — distinct books per sport: mlb=1
- **nba_prop_scores** — distinct books per sport: nba=1

## 5-7. Players, Teams, Outcomes Summary

Player/team distinct counts and outcome coverage are summarised under §2 and §3. Outcome grading data was specifically queried across `team_prop_outcomes`, `sgo_pp_research_outcomes`, `sgo_odds_outcomes`.

## 8. Data Quality Warnings

- **team_historical_props**:
  - null_player_id: 2,904,172
  - null_line: 946,304
- **nfl_historical_props**:
  - null_player_id: 316,474
  - null_line: 94,880
- **nfl_player_historical_props**:
  - null_team_id: 1,188,943
  - null_line: 256,210
- **team_live_props**:
  - null_player_id: 286
  - null_line: 110
- **team_matchups**:
  - null_player_id: 7,817
  - null_team_id: 7,817
  - null_book: 7,817
  - null_odds: 7,817
  - null_line: 7,817
  - null_market: 7,817
- **nfl_matchups**:
  - null_player_id: 659
  - null_team_id: 659
  - null_book: 659
  - null_odds: 659
  - null_line: 659
  - null_market: 659
- **mlb_prop_scores**:
  - null_game_date: 11,600
  - null_player_id: 11,600
  - null_team_id: 11,600
  - null_book: 11,600
  - null_odds: 11,600
  - null_market: 11,600
- **nba_prop_scores**:
  - null_game_date: 3,811
  - null_player_id: 3,811
  - null_team_id: 3,811
  - null_book: 3,811
  - null_odds: 3,811
  - null_market: 3,811

## 9. Acquisition Runs

### historical_acquire_runs  (10 most recent)

| run_id | sport | window | status | rows | duration | started |
|---|---|---|---|---|---|---|
| cbe7f433 | nfl | 2025-10-06 → 2026-02-09 | succeeded | 899,601 | 138.3s | 2026-05-30 05:15:16 |
| 7630bea0 | nfl | 2024-09-08 → 2024-09-08 | guard_closed | 0 | — | 2026-05-30 01:03:32 |
| 91712a52 | nba | 2024-02-01 → 2026-02-09 | succeeded | 1,136,293 | 864.8s | 2026-05-30 00:26:26 |
| bec96155 | mlb | 2024-03-01 → 2026-02-09 | succeeded | 1,767,879 | 1133.5s | 2026-05-30 00:03:39 |
| 6c188e0a | nfl | 2024-02-10 → 2026-02-09 | succeeded | 316,474 | 370.9s | 2026-05-29 23:31:56 |
| 2e91a2f6 | nfl | 2023-02-11 → 2023-02-13 | dry_run | 0 | 0.3s | 2026-05-29 23:24:50 |
| 6b8f494a | nfl | 2023-12-28 → 2024-01-01 | dry_run | 0 | 0.7s | 2026-05-29 23:24:48 |
| 19fe4d7a | nfl | 2025-01-13 → 2025-01-15 | dry_run | 255 | 0.6s | 2026-05-29 23:24:34 |
| f938350b | nfl | 2024-09-05 → 2024-09-09 | dry_run | 4,042 | 3.1s | 2026-05-29 23:24:21 |
| 265374a6 | nfl | 2024-09-05 → 2024-09-05 | guard_closed | 0 | — | 2026-05-29 23:19:01 |

### team_odds_ingest_runs  (2 most recent)

| run_id | sport | window | status | rows | duration | started |
|---|---|---|---|---|---|---|
| c543c6a9 | mlb | 2026-05-29 → 2026-05-29 | succeeded | 286 | 0.0s | 2026-05-29 05:39:14 |
| 7501cbb5 | mlb | 2026-05-29 → 2026-05-29 | dry_run | 0 | 0.0s | 2026-05-29 05:31:46 |

---

_Read-only audit; no Mongo writes performed._

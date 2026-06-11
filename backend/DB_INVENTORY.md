# DB_INVENTORY.md
Generated: 2026-06-11 — pick_vision database — 233 collections total

---

## Summary

| Class | Count | Notes |
|-------|-------|-------|
| CANONICAL | 116 | Active SSOT — keep |
| STALE | 80 | Dead pipeline artifacts — safe to drop |
| UNKNOWN | 37 | Flagged for manual review |

Collections with resolved `outcome_numeric` in [0,1]:
- `sgo_pp_research_outcomes` (1,022,207 rows)
- `team_historical_outcomes` (1,893,834 rows)
- `team_model_prop_features` (1,798,949 rows)
- `player_model_prop_features` (730,081 rows)
- `sgo_ncaaf_research_outcomes` (127,663 rows)

---

## Active Script Collection References

### scripts/sgo/ (backtesting pipeline)

```
team_matchups                    → historical_gate_replay_grid.py
research_grid_runs               → historical_gate_replay_grid.py
research_grid_results            → historical_gate_replay_grid.py
candidate_gate_configs           → historical_gate_replay_grid.py
team_model_prop_features         → historical_gate_replay_grid.py, build_team_prop_features.py, reshape_team_props_to_replay.py
player_model_prop_features       → historical_gate_replay_grid.py, build_player_prop_features.py, reshape_player_props_to_replay.py
team_model_features              → build_nba_team_advanced_features.py, build_team_features.py, build_team_prop_features.py
odds_api_team_h2h                → historical_gate_replay_grid.py, ingest_odds_api_team_h2h.py
mlb_statcast_pitcher_features    → historical_gate_replay_grid.py
mlb_master_hub_2026              → historical_gate_replay_grid.py
sgo_propvision_full_pipeline_replay → audit_pipeline_parity.py, mirror_player_replay_to_unified.py,
                                      build_team_prop_features.py, reshape_team_props_to_replay.py,
                                      reshape_player_props_to_replay.py
sgo_pp_research_core             → build_historical_outcomes.py, build_historical_consensus_probabilities.py, reshape_sgo_to_replay_odds.py
sgo_pp_research_core_enriched    → build_historical_model_features.py, build_historical_outcomes.py, reshape_sgo_to_replay_odds.py
sgo_pp_research_outcomes         → build_player_features.py, build_player_prop_features.py
sgo_pp_research_model_features   → score_historical_model.py, build_historical_model_features.py
sgo_pp_research_model_predictions→ score_historical_model.py
player_model_features            → build_player_features.py, build_player_prop_features.py
team_historical_outcomes         → build_team_features.py, build_team_prop_features.py, audit_pipeline_parity.py
sgo_player_stats                 → build_historical_model_features.py, build_historical_outcomes.py, audit_ncaaf_*, reshape_ncaaf_*
sgo_events                       → reshape_ncaaf_to_legacy_sgo.py
sgo_players                      → reshape_ncaaf_to_legacy_sgo.py, audit_ncaaf_*, ingest_historical_player_stats.py
sgo_props_raw                    → reshape_ncaaf_to_legacy_sgo.py
ncaaf_player_historical_props    → audit_ncaaf_*.py, reshape_ncaaf_to_legacy_sgo.py
ncaaf_matchups                   → reshape_ncaaf_to_legacy_sgo.py
sgo_ncaaf_research_outcomes      → audit_ncaaf_*.py, diagnose_ncaaf_*.py
bdl_advanced_stats               → build_nba_team_advanced_features.py
bdl_nba_game_boxscores           → ingest_bdl_nba_game_boxscores.py
bdl_mlb_game_boxscores           → ingest_bdl_mlb_game_boxscores.py
bdl_mlb_team_season_stats        → ingest_bdl_mlb_team_season_stats.py
bdl_nba_team_season_stats        → ingest_bdl_nba_team_season_stats.py
mlb_statcast_pitcher_features    → historical_gate_replay_grid.py
sgo_replay_alt_odds_raw          → run_sgo_production_replay.py, mlb_replay_build_feature_cache.py
historical_odds_full             → odds_api_backfill/orchestrator.py, odds_api_backfill/validate_slate.py
```

### services/ (production API)

```
nba_master_hub_2026              → services/ (8 references)
nba_cached_board                 → services/ (7 references)
nba_prop_scores                  → services/ (6 references)
replay_props_normalized          → services/ (5 references)
nba_live_props                   → services/ (5 references)
dg_raw_odds_markets              → services/ (5 references — STALE, DG engine deleted)
bdl_historical_game_logs         → services/ (5 references)
bdl_advanced_stats               → services/bdl_advanced_stats_fetcher.py
active_transitions               → services/board/set_active.py (TTL-indexed audit log)
nba_context_engine               → services/badge_resolver.py
defensive_momentum_cache         → services/defensive_momentum_service.py, vegas_pro_model.py, vegas_killer_model.py
league_roster                    → services/stats_manager_bdl.py
mlb_vacuum_alerts                → services/mlb_injury_vacuum_service.py
historical_acquire_runs          → workers/team/historical_ingest.py, workers/team/historical_player_ingest.py
optimizer_run_results            → routes/emergent_admin/optimizer.py
optimizer_runs                   → routes/emergent_admin/optimizer.py, scripts/research/run_optimizer_cli.py
forward_test_snapshots           → services/forward_testing_service.py, services/cron_scheduler.py
forward_test_outcomes            → services/forward_testing_service.py, scripts/nba_tier_hit_rate_monitor.py
forward_test_metrics             → services/forward_testing_service.py
shadow_vk_snapshots              → services/cron_scheduler.py
replay_outcomes                  → scripts/run_outcome_resolver.py, scripts/run_full_replay_chunked.py
replay_ingest_progress           → scripts/validate_replay_ingest.py
replay_odds_snapshots            → scripts/validate_replay_ingest.py
historical_odds                  → scripts/backtest_real_lines.py, scripts/backtest_tier_qualified.py (LEGACY)
```

---

## Full Collection Inventory

### Operational — Live Production

| Collection | Count | Date Range | Key Fields | Outcomes | Class |
|-----------|-------|-----------|------------|----------|-------|
| `nba_live_props` | 18,432 | 2026-06-11 | sport, player_id, prop_type, clean_odds | no | CANONICAL |
| `mlb_live_props` | 5,360 | 2026-06-11 | sport, player_id, prop_type | no | CANONICAL |
| `team_live_props` | 0 | — | — | no | CANONICAL |
| `nba_cached_board` | 219 | 2026-06-11 | sport, player_id | no | CANONICAL |
| `mlb_cached_board` | 110 | 2026-06-11 | sport, player_id | no | CANONICAL |
| `nfl_cached_board` | 0 | — | — | no | CANONICAL |
| `nba_prop_scores` | 18,432 | 2026-06-11 | sport, player_id | no | CANONICAL |
| `mlb_prop_scores` | 6,127 | 2026-06-10..06-11 | sport, player_id | no | CANONICAL |
| `nfl_prop_scores` | 0 | — | — | no | CANONICAL |
| `team_prop_scores` | 0 | — | — | no | CANONICAL |
| `ferrari_tiers` | 0 | — | — | no | CANONICAL |
| `nba_master_hub_2026` | 530 | — | player_id | no | CANONICAL |
| `mlb_master_hub_2026` | 6,673 | — | player_id | no | CANONICAL |
| `nba_master_roster` | 636 | — | player_id | no | CANONICAL |
| `injuries_normalized` | 77 | — | sport | no | CANONICAL |
| `live_injuries` | 65 | — | sport | no | CANONICAL |
| `espn_injuries` | 69 | — | — | no | CANONICAL |
| `espn_news` | 109 | — | — | no | CANONICAL |
| `users` | 4 | — | — | no | CANONICAL |
| `scheduler_jobs` | 1 | — | — | no | CANONICAL |
| `sync_log` | 1,459 | — | — | no | CANONICAL |
| `sync_history` | 6,892 | — | — | no | CANONICAL |
| `sync_locks` | 0 | — | — | no | CANONICAL |
| `adaptive_sync_heartbeat` | 1 | — | — | no | CANONICAL |
| `board_state` | 0 | — | — | no | CANONICAL |
| `board_state_events` | 0 | — | — | no | CANONICAL |
| `board_drift_ledger` | 2 | — | — | no | CANONICAL |
| `active_transitions` | 6,437,069 | — | sport | no | CANONICAL |
| `delta_dirty_queue` | 0 | — | — | no | CANONICAL |
| `system_state` | 3 | — | — | no | CANONICAL |
| `breaking_news_cache` | 5 | — | — | no | CANONICAL |
| `ticker_cache` | 4 | — | — | no | CANONICAL |
| `ticker_headlines` | 9 | — | — | no | CANONICAL |
| `odds_api_call_log` | 0 | — | — | no | CANONICAL |
| `odds_delta_state` | 34 | — | — | no | CANONICAL |
| `odds_api_props` | 23,143 | — | sport | no | CANONICAL |
| `line_history` | 2,855,717 | — | sport | no | CANONICAL |
| `nba_line_history` | 0 | — | — | no | CANONICAL |
| `line_movements` | 22 | — | — | no | CANONICAL |
| `market_moves` | 22 | — | — | no | CANONICAL |
| `market_moves_snapshots` | 2 | — | — | no | CANONICAL |
| `replay_props_normalized` | 3,520,527 | — | sport | no | CANONICAL |
| `player_photos` | 502 | — | — | no | CANONICAL |
| `pp_projection_id_cache` | 1,074 | — | — | no | CANONICAL |
| `star_usage_cache` | 85 | — | — | no | CANONICAL |
| `spotrac_contracts_cache` | 463 | — | — | no | CANONICAL |
| `emergent_admin_audit_log` | 0 | — | — | no | CANONICAL |
| `emergent_admin_jobs` | 0 | — | — | no | CANONICAL |
| `error_log` | 0 | — | — | no | CANONICAL |
| `todays_games` | 0 | — | — | no | CANONICAL |
| `nba_pick_history` | 0 | — | — | no | CANONICAL |
| `mlb_pick_history` | 148 | 2026-04-27..04-29 | — | no | CANONICAL |

### Operational — NBA Specific

| Collection | Count | Date Range | Key Fields | Outcomes | Class |
|-----------|-------|-----------|------------|----------|-------|
| `nba_context_engine` | 99 | — | player_id | no | CANONICAL |
| `nba_player_context_features` | 18,815 | 2026-04-26..04-27 | player_id, stat_type | no | CANONICAL |
| `nba_career_stats` | 164 | — | player_id | no | UNKNOWN |
| `nba_player_stats` | 101 | — | player_id | no | UNKNOWN |
| `nba_props` | 1,519 | — | sport, player_id | no | UNKNOWN |
| `nba_pra_projection_audit` | 21,021 | — | player_id | no | CANONICAL |
| `nba_referee_assignments` | 3,420 | — | — | no | CANONICAL |
| `nba_calibration_runs` | 2 | — | — | no | UNKNOWN |
| `nba_odds_api_mapping_master` | 530 | — | player_id | no | CANONICAL |
| `odds_api_mapping_master` | 4 | — | — | no | CANONICAL |
| `dvp_rankings` | 150 | — | sport | no | CANONICAL |
| `league_roster` | 320 | — | player_id | no | CANONICAL |
| `defensive_momentum_cache` | 30 | — | sport | no | CANONICAL |

### Operational — MLB Specific

| Collection | Count | Date Range | Key Fields | Outcomes | Class |
|-----------|-------|-----------|------------|----------|-------|
| `mlb_lineups` | 0 | — | — | no | CANONICAL |
| `mlb_projected_lineups` | 600 | 2026-04-28..06-11 | sport | no | CANONICAL |
| `mlb_player_identity_map` | 983 | 2026-04-28..05-04 | sport, player_id | no | CANONICAL |
| `mlb_vacuum_alerts` | 3 | — | — | no | CANONICAL |
| `mlb_statcast_raw` | 1,602,169 | — | — | no | CANONICAL |
| `mlb_statcast_player_features` | 27,302 | — | player_id | no | CANONICAL |
| `mlb_statcast_pitcher_features` | 4,895 | — | player_id | no | CANONICAL |
| `mlb_standard` | 225 | — | sport, player_id | no | STALE |
| `mlb_historical_logs` | 6,645 | 2026-04-10 only | player_id | no | STALE |
| `mlb_vacuum_alerts` | 3 | — | — | no | CANONICAL |

### BDL (BallDontLie) Data

| Collection | Count | Date Range | Key Fields | Outcomes | Class |
|-----------|-------|-----------|------------|----------|-------|
| `bdl_advanced_stats` | 147,249 | 2020-12-22..2025-06-22 | player_id | no | CANONICAL |
| `bdl_historical_game_logs` | 201,626 | 2020-12-22..2025-06-22 | player_id | no | CANONICAL |
| `bdl_mlb_historical_game_logs` | 56,785 | — | player_id | no | CANONICAL |
| `bdl_nba_game_boxscores` | 16,481 | 2024-07-05..2026-06-10 | — | no | CANONICAL |
| `bdl_mlb_game_boxscores` | 5,783 | 2024-04-01..2026-06-08 | — | no | CANONICAL |
| `bdl_nba_team_game_features` | 13,254 | 2024-07-05..2026-06-10 | — | no | CANONICAL |
| `bdl_mlb_team_game_features` | 8,854 | 2024-04-01..2026-06-08 | — | no | CANONICAL |
| `bdl_nba_team_season_stats` | 750 | — | — | no | CANONICAL |
| `bdl_mlb_team_season_stats` | 4,914 | — | — | no | CANONICAL |
| `bdl_player_badges` | 3,052 | — | player_id | no | CANONICAL |
| `bdl_player_mapping` | 4,228 | — | player_id | no | CANONICAL |
| `bdl_injuries` | 12,406 | — | — | no | CANONICAL |
| `bdl_mlb_plays` | 0 | — | — | no | CANONICAL |
| `bdl_mlb_plays_status` | 1,886 | — | — | no | CANONICAL |

### Odds API Team Data

| Collection | Count | Date Range | Key Fields | Outcomes | Class |
|-----------|-------|-----------|------------|----------|-------|
| `odds_api_team_h2h` | 7,427 | 2024-07-01..2026-06-11 | sport, league_id, clean_odds | no | CANONICAL |
| `team_matchups` | 7,164 | 2024-07-01..2026-06-19 | sport | no | CANONICAL |
| `team_master_hub` | 92 | — | sport, league_id | no | CANONICAL |
| `team_odds_ingest_runs` | 3 | — | — | no | CANONICAL |

### SGO Raw Pipeline

| Collection | Count | Date Range | Key Fields | Outcomes | Class |
|-----------|-------|-----------|------------|----------|-------|
| `sgo_props_raw` | 19,610,180 | — | league_id | no | CANONICAL |
| `sgo_events` | 3,162,085 | — | sport, league_id | no | CANONICAL |
| `sgo_players` | 23,148 | — | sport | no | CANONICAL |
| `sgo_player_stats` | 1,327,461 | — | sport, league_id, player_id | no | CANONICAL |
| `sgo_player_master` | 21,908 | — | sport | no | CANONICAL |
| `sgo_team_stats` | 71,396 | — | sport, league_id | no | CANONICAL |
| `sgo_book_consensus` | 2,864,487 | — | sport, league_id | no | CANONICAL |
| `sgo_meta_leagues` | 40 | — | league_id | no | CANONICAL |
| `sgo_meta_sports` | 16 | — | sport | no | CANONICAL |
| `sgo_ingest_status` | 48 | — | — | no | CANONICAL |
| `sgo_results` | 3,218,694 | — | sport, league_id | no | CANONICAL |
| `sgo_raw_responses` | 0 | — | — | no | STALE |
| `sgo_replay_alt_odds_raw` | 4,969,082 | 2025-05-01..2026-05-30 | sport, game_date, line, book | no | CANONICAL |
| `sgo_odds_outcomes` | 0 | — | — | no | STALE |

### SGO Player Prop Research Pipeline

| Collection | Count | Date Range | Key Fields | Outcomes | Class |
|-----------|-------|-----------|------------|----------|-------|
| `sgo_pp_research_core` | 1,118,776 | — | sport, league_id, player_id, stat_family, prop_type | no | CANONICAL |
| `sgo_pp_research_core_enriched` | 1,118,776 | — | sport, league_id, player_id | no | CANONICAL |
| `sgo_pp_research_outcomes` | 1,022,207 | 2025-05-01..2026-04-15 | sport, league_id, player_id, outcome_numeric, stat_family, prop_type | **YES** | CANONICAL |
| `sgo_pp_research_model_features` | 1,022,207 | — | sport, league_id, player_id, stat_family | no | CANONICAL |
| `sgo_pp_research_model_predictions` | 1,022,207 | — | sport, player_id | no | CANONICAL |
| `player_model_features` | 549,019 | 2025-05-01..2026-04-15 | sport, player_id, stat_family | no | CANONICAL |
| `player_model_prop_features` | 730,081 | 2025-10-21..2026-04-15 | sport, player_id, stat_family, outcome_numeric | **YES** | CANONICAL |

### SGO NCAAF Research Pipeline

| Collection | Count | Date Range | Key Fields | Outcomes | Class |
|-----------|-------|-----------|------------|----------|-------|
| `ncaaf_matchups` | 3,524 | — | sport, league_id | no | CANONICAL |
| `ncaaf_player_historical_props` | 1,176,264 | — | sport, player_id, stat_family, prop_type | no | CANONICAL |
| `ncaaf_historical_props` | 0 | — | — | no | CANONICAL |
| `sgo_ncaaf_research_core` | 111,461 | — | sport, league_id, player_id | no | CANONICAL |
| `sgo_ncaaf_research_model_features` | 127,663 | — | sport, player_id | no | CANONICAL |
| `sgo_ncaaf_research_outcomes` | 127,663 | 2025-08-23..2026-01-20 | sport, player_id, outcome_numeric | **YES** | CANONICAL |

### Team Model Backtesting Pipeline

| Collection | Count | Date Range | Key Fields | Outcomes | Class |
|-----------|-------|-----------|------------|----------|-------|
| `team_historical_props` | 4,097,313 | — | sport, league_id, market_category | no | CANONICAL |
| `team_historical_outcomes` | 1,893,834 | 2024-07-05..2026-05-30 | sport, league_id, outcome_numeric, market_category | **YES** | CANONICAL |
| `team_model_features` | 13,648 | 2024-07-05..2026-06-11 | sport, league_id | no | CANONICAL |
| `team_model_prop_features` | 1,798,949 | 2024-07-05..2026-05-30 | sport, league_id, outcome_numeric, market_category, clean_odds | **YES** | CANONICAL |
| `sgo_propvision_full_pipeline_replay` | 5,059,904 | — | sport, league_id, market_category, clean_odds, implied_probability | no | CANONICAL |

### Player Historical Props (Per-Book Raw Lines)

| Collection | Count | Date Range | Key Fields | Outcomes | Class |
|-----------|-------|-----------|------------|----------|-------|
| `nba_player_historical_props` | 8,156,972 | 2024-10-05..2026-05-29 | player_id, market_key, side, line | no | CANONICAL |
| `mlb_player_historical_props` | 17,525,376 | 2024-03-20..2026-05-30 | player_id, market_key, side, line | no | CANONICAL |
| `nfl_player_historical_props` | 1,217,013 | — | player_id, market_key, side, line | no | CANONICAL |

### Historical Odds (Odds API Backfill)

| Collection | Count | Date Range | Key Fields | Outcomes | Class |
|-----------|-------|-----------|------------|----------|-------|
| `historical_odds_full` | 460,465 | — | sport, league_id, line, bookmaker | no | CANONICAL |
| `historical_odds` | 112,314 | — | player_id | no | STALE |

### NFL Pipeline

| Collection | Count | Date Range | Key Fields | Outcomes | Class |
|-----------|-------|-----------|------------|----------|-------|
| `nfl_matchups` | 6,419 | — | sport, league_id | no | CANONICAL |
| `nfl_historical_props` | 1,218,218 | — | sport, league_id, market_category | no | CANONICAL |
| `nfl_player_historical_props` | 1,217,013 | — | player_id | no | CANONICAL |
| `nfl_cached_board` | 0 | — | — | no | CANONICAL |
| `nfl_prop_scores` | 0 | — | — | no | CANONICAL |

### Research Grid & Optimizer

| Collection | Count | Date Range | Key Fields | Outcomes | Class |
|-----------|-------|-----------|------------|----------|-------|
| `research_grid_results` | 332,845 | — | sport, market_category | no | CANONICAL |
| `research_grid_runs` | 2,186 | — | sport | no | CANONICAL |
| `candidate_gate_configs` | 4,278 | — | sport, market_category | no | CANONICAL |
| `candidate_thresholds` | 0 | — | — | no | CANONICAL |
| `optimizer_run_results` | 4,033,129 | — | tier, stat_family, odds_bucket | no | CANONICAL |
| `optimizer_runs` | 97 | — | — | no | CANONICAL |

### Forward Testing & Live Replay

| Collection | Count | Date Range | Key Fields | Outcomes | Class |
|-----------|-------|-----------|------------|----------|-------|
| `forward_test_snapshots` | 8,740 | — | sport | no | CANONICAL |
| `forward_test_outcomes` | 510 | — | sport | no | CANONICAL |
| `forward_test_metrics` | 18 | — | sport | no | CANONICAL |
| `shadow_vk_snapshots` | 493 | — | — | no | CANONICAL |
| `replay_outcomes` | 275,906 | — | sport, market_type | no | CANONICAL |
| `replay_ingest_progress` | 1,464 | 2024-02-16..2026-05-09 | sport, game_date | no | CANONICAL |
| `replay_odds_snapshots` | 24,475 | — | market_key | no | CANONICAL |
| `replay_vk2_cache` | 113 | — | — | no | STALE |
| `replay_evaluations` | 1,220,060 | — | — | no | STALE |
| `replay_results` | 0 | — | — | no | STALE |

### Team Pipeline Audit

| Collection | Count | Date Range | Key Fields | Outcomes | Class |
|-----------|-------|-----------|------------|----------|-------|
| `historical_acquire_runs` | 88 | — | — | no | CANONICAL |
| `sgo_propvision_full_pipeline_replay_diff` | 0 | — | — | no | STALE |

---

## STALE Collections (Safe to Drop — Pending Confirmation)

### DemonGoblin Engine (deleted 2026-04-22)

All `dg_*` collections. Never written to again. Total: 26 collections.

| Collection | Count | Reason |
|-----------|-------|--------|
| `dg_breaking_news` | 50 | DG deleted |
| `dg_cached_board` | 49 | DG deleted |
| `dg_cached_board_temp` | 49 | DG deleted |
| `dg_daily_insights` | 5 | DG deleted |
| `dg_dynamic_lines` | 105 | DG deleted |
| `dg_events_cache` | 0 | DG deleted |
| `dg_flagged_players` | 0 | DG deleted |
| `dg_front_lines` | 3 | DG deleted |
| `dg_goblin_recon` | 0 | DG deleted |
| `dg_goblin_vault` | 0 | DG deleted |
| `dg_injuries` | 65 | DG deleted |
| `dg_live_props` | 2,697 | DG deleted |
| `dg_locked_games` | 0 | DG deleted |
| `dg_market_catalog_cache` | 7,614 | DG deleted |
| `dg_master_roster` | 502 | DG deleted |
| `dg_odds_cache` | 0 | DG deleted |
| `dg_parlay_builder` | 0 | DG deleted |
| `dg_player_data` | 0 | DG deleted |
| `dg_player_stats` | 0 | DG deleted |
| `dg_radar_picks` | 0 | DG deleted |
| `dg_raw_odds_markets` | 13,547 | DG deleted |
| `dg_social_signals` | 0 | DG deleted |
| `dg_static_shell` | 0 | DG deleted |
| `dg_stats_cache` | 0 | DG deleted |
| `dg_sync_log` | 1 | DG deleted |
| `dg_sync_status` | 2 | DG deleted |
| `dg_trending` | 0 | DG deleted |

### Old Ferrari / Elite Tier Staging

Replaced by `nba_cached_board` / `mlb_cached_board`.

| Collection | Count | Reason |
|-----------|-------|--------|
| `ferrari_discarded` | 0 | Replaced |
| `ferrari_front_lines` | 0 | Replaced |
| `ferrari_parlays` | 0 | Replaced |
| `ferrari_picks` | 15 | Replaced |
| `ferrari_safe_haven` | 0 | Replaced |
| `ferrari_scored` | 0 | Replaced |
| `ferrari_war_zone` | 0 | Replaced |
| `elite_front_lines` | 1 | Replaced |
| `elite_safe_haven` | 2 | Replaced |
| `elite_war_zone` | 0 | Replaced |
| `mlb_elite_front_lines` | 4 | Replaced |
| `mlb_elite_safe_haven` | 6 | Replaced |
| `mlb_elite_war_zone` | 1 | Replaced |
| `nba_board` | 120 | Superseded by nba_cached_board |
| `nba_tier_monitor` | 0 | Superseded |
| `mlb_demons` | 3,888 | Old DG-era MLB tier, no active writers |
| `mlb_goblins` | 1,550 | Old DG-era MLB tier, no active writers |

### Old Sport-Specific Replay Pipelines

Unified into `sgo_propvision_full_pipeline_replay`.

| Collection | Count | Reason |
|-----------|-------|--------|
| `nba_propvision_full_pipeline_outputs` | 3,898,274 | Superseded |
| `nba_propvision_full_pipeline_cards` | 0 | Superseded |
| `nba_propvision_full_pipeline_runs` | 0 | Superseded |
| `mlb_propvision_full_pipeline_outputs` | 735,190 | Superseded |
| `mlb_propvision_full_pipeline_cards` | 0 | Superseded |
| `mlb_propvision_full_pipeline_runs` | 2,587 | Superseded |
| `mlb_replay_feature_cache` | 15,787 | Superseded |
| `mlb_replay_feature_status` | 92 | Superseded |
| `mlb_replay_gate_results` | 1,402,377 | Superseded |
| `mlb_replay_model_outputs` | 544,949 | Superseded |
| `mlb_replay_model_status` | 1 | Superseded |
| `mlb_production_replay_outputs` | 2,006,669 | Superseded |
| `nba_replay_model_outputs` | 3,710,997 | Superseded |
| `nba_replay_model_status` | 1 | Superseded |
| `team_replay_model_outputs` | 0 | Superseded |
| `team_replay_outputs` | 0 | Superseded |
| `sgo_propvision_full_pipeline_replay_diff` | 0 | Debug diff, not SSOT |
| `replay_evaluations` | 1,220,060 | Old output; replaced by research_grid_results |
| `replay_results` | 0 | Old output |
| `replay_vk2_cache` | 113 | Old VK2 inference cache |

### Other Stale Artifacts

| Collection | Count | Reason |
|-----------|-------|--------|
| `_tmp_mlb_safe_haven_5b969583` | 10 | Temp scratch |
| `_tmp_mlb_safe_haven_d7506657` | 10 | Temp scratch |
| `backtest_game_logs` | 6,455 | Old NBA-only backtesting (2025-02..03); superseded by bdl_historical_game_logs |
| `sgo_raw_responses` | 0 | Raw HTTP cache; no active writers |
| `board_state_shadow` | 2 | A/B shadow board; no active writers |
| `board_state_shadow_events` | 0 | A/B shadow events; no active writers |
| `historical_odds` | 112,314 | Legacy NBA-only Odds API store; superseded by historical_odds_full |
| `pp_payout_structure_tests` | 0 | One-off test collection |
| `mlb_test_outputs` | 0 | One-off test collection |
| `mlb_prodreplay_serial_counter` | 1 | Serial counter for deleted MLB prod replay pipeline |
| `nba_prodreplay_serial_counter` | 1 | Serial counter for deleted NBA prod replay pipeline |
| `oracle_apex_analyzed` | 59,748 | oracle_apex prefix not in any active script |
| `nba_events_cache` | 0 | Stale events cache |
| `nba_odds_cache` | 0 | Stale odds cache |
| `odds_event_props_cache` | 8 | Stale event props cache |
| `live_scores_cache` | 1 | Stale live scores cache |
| `sgo_odds_outcomes` | 0 | Empty; superseded by sgo_pp_research_outcomes |
| `mlb_standard` | 225 | Only in _archive_mlb_v1/; no active scripts |
| `mlb_historical_logs` | 6,645 | Single-day snapshot (2026-04-10); superseded by bdl_mlb_historical_game_logs |
| `contract_violations` | 0 | No active writers or readers found |
| `team_context` | 0 | Empty; no active writers |
| `team_features` | 0 | Empty; no active writers |
| `team_injuries` | 0 | Empty; no active writers |
| `team_projections` | 0 | Empty; no active writers |
| `team_prop_outcomes` | 0 | Empty; superseded by team_historical_outcomes |

---

## UNKNOWN Collections (Manual Review Required)

| Collection | Count | Date Range | Notes |
|-----------|-------|-----------|-------|
| `nba_career_stats` | 164 | — | No script refs found outside audit scripts; may be orphaned |
| `nba_player_stats` | 101 | — | No script refs (spotrac_contract_service.py uses it as DB_NAME fallback, not a collection) |
| `nba_props` | 1,519 | — | No active writer found |
| `nba_calibration_runs` | 2 | — | 2 docs; likely STALE |
| `sgo_replay_alt_odds_raw` | 4,969,082 | 2025-05-01..2026-05-30 | Used by run_sgo_production_replay.py — CANONICAL if that script is active |
| `replay_engine_progress` | 1 | — | 1 doc; unclear if the engine that writes this is still running |
| `historical_acquire_runs` | 88 | — | Active writer in workers/team/historical_ingest.py — likely CANONICAL |
| `league_roster` | 320 | — | Used by stats_manager_bdl.py — likely CANONICAL but may overlap with nba_master_roster |
| `defensive_momentum_cache` | 30 | — | Used by vegas_pro_model.py, vegas_killer_model.py — CANONICAL |

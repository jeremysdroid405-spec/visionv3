# Dedupe-Key Audit (live MongoDB)
_Generated: 2026-05-30T17:50:49.090891+00:00_

- Collections scanned: 162
- With ≥1 unique index: 60
- **With volatile-field in unique key (BROKEN): 14**
- With run-id-like in unique key: 7

## ⚠️ Broken — volatile field in unique key

| Collection | n_docs | Index | Full unique key | Volatile field(s) |
|---|---:|---|---|---|
| `historical_odds_full` | 460,465 | `uniq_sport_event_market_snapshot_book_player_line_side` | `(sport_key, event_id, market_key, snapshot_time, bookmaker, player, line, side)` | snapshot_time |
| `mlb_replay_audit` | 42 | `audit_compound_unique` | `(game_date, tier, snapshot_iso, gate_config_version, scoring_config_version)` | snapshot_iso |
| `mlb_replay_gate_results` | 1,256,868 | `gate_results_compound_unique` | `(game_date, event_id, player_name_normalized, market, line, side, book, snapshot_iso, scoring_config_version, gate_config_version)` | snapshot_iso |
| `mlb_replay_model_status` | 44 | `replay_status_unique` | `(game_date, snapshot_iso, scoring_config_version)` | snapshot_iso |
| `nba_player_historical_props` | 0 | `ix_nba_player_hist_compound_unique` | `(event_id, player_id, market, line, side, book, snapshot_iso)` | snapshot_iso |
| `nfl_historical_props` | 316,474 | `ix_nfl_hist_prop_compound_unique` | `(event_id, team_id, market, line, side, book, snapshot_iso)` | snapshot_iso |
| `nfl_player_historical_props` | 1,188,943 | `ix_nfl_player_hist_compound_unique` | `(event_id, player_id, market, line, side, book, snapshot_iso)` | snapshot_iso |
| `replay_odds_snapshots` | 23,971 | `uniq_event_market_label` | `(event_id, market_key, snapshot_label)` | snapshot_label |
| `replay_vk2_cache` | 314,658 | `uniq_event_snap_can_side` | `(event_id, snapshot_label, canonical_key, side)` | snapshot_label |
| `sgo_replay_alt_odds_raw` | 0 | `alt_odds_compound_unique_v2` | `(sport, game_date, event_id, player_name_normalized, market, line, side, book, snapshot_iso)` | snapshot_iso |
| `team_historical_props` | 2,904,172 | `ix_hist_prop_compound_unique` | `(event_id, team_id, market, line, side, book, snapshot_iso)` | snapshot_iso |
| `team_live_props` | 286 | `ix_live_prop_compound_unique` | `(event_id, team_id, market, line, side, book, snapshot_iso)` | snapshot_iso |
| `team_prop_scores` | 0 | `ix_score_compound_unique` | `(event_id, team_id, market, line, side, book, snapshot_iso, model_version, gate_config_version)` | snapshot_iso |
| `team_replay_outputs` | 0 | `ix_replay_compound_unique` | `(event_id, team_id, market, line, side, book, snapshot_iso, model_version, gate_config_version)` | snapshot_iso |

## ⚠️ Broken — run-id-like in unique key

| Collection | n_docs | Index | Full unique key | run-id field(s) |
|---|---:|---|---|---|
| `historical_acquire_runs` | 41 | `ix_hist_acquire_run_id_unique` | `(run_id)` | run_id |
| `mlb_production_replay_outputs` | 2,069,791 | `prod_replay_outputs_compound_unique` | `(replay_serial, event_id, player_name_normalized, market, line, side, book)` | replay_serial |
| `mlb_production_replay_runs` | 73 | `serial_1` | `(serial)` | serial |
| `mlb_replay_audit` | 42 | `serial_1` | `(serial)` | serial |
| `mlb_test_cards` | 2,601 | `test_cards_serial_rank_unique` | `(replay_serial, rank)` | replay_serial |
| `mlb_test_runs` | 156 | `serial_1` | `(serial)` | serial |
| `team_odds_ingest_runs` | 2 | `ix_run_id_unique` | `(run_id)` | run_id |

## Clean — collections with safe unique keys

| Collection | n_docs | Index | Unique key |
|---|---:|---|---|
| `bdl_advanced_stats` | 147,249 | `player_id_1_game_id_1` | `(player_id, game_id)` |
| `bdl_advanced_stats` | 147,249 | `game_id_1_player_id_1` | `(game_id, player_id)` |
| `bdl_historical_game_logs` | 201,626 | `game_id_1_player_id_1` | `(game_id, player_id)` |
| `bdl_mlb_historical_game_logs` | 1,656 | `bdl_mlb_raw_pid_game_unique` | `(bdl_player_id, game_id)` |
| `board_state` | 2,359 | `board_state_identity_uq` | `(sport, tier, side, canonical_key)` |
| `board_state_shadow` | 872 | `shadow_board_state_identity_uq` | `(sport, tier, side, canonical_key)` |
| `dg_daily_insights` | 127 | `player_name_1` | `(player_name)` |
| `emergent_admin_jobs` | 50 | `job_id_1` | `(job_id)` |
| `mlb_pick_history` | 148 | `uniq_date_player_stat_line_side_book` | `(game_date, player, stat_family, line, side, bookmaker)` |
| `mlb_player_historical_props` | 100,888 | `ix_mlb_player_hist_compound_unique` | `(event_id, player_id, market, line, side, book)` |
| `mlb_projected_lineups` | 271 | `evt_team_uq` | `(event_id, team_abbr)` |
| `mlb_prop_scores` | 89,141 | `uniq_canonical_version` | `(canonical_key, version_tag)` |
| `mlb_replay_feature_cache` | 0 | `feature_cache_compound_unique` | `(sport, game_date, player_name_normalized, stat_family, source_version)` |
| `mlb_replay_feature_status` | 0 | `feature_status_unique` | `(game_date, source_version)` |
| `mlb_statcast_pitcher_features` | 47,021 | `uniq_pitcher_date` | `(pitcher_id, game_date)` |
| `mlb_statcast_player_features` | 114,859 | `uniq_player_date` | `(player_id, game_date)` |
| `mlb_statcast_raw` | 1,674,505 | `uniq_game_ab_pitch` | `(game_pk, at_bat_number, pitch_number)` |
| `nba_calibration_runs` | 2 | `uniq_snap_id` | `(snapshot_id)` |
| `nba_live_props` | 0 | `_composite_key_1` | `(_composite_key)` |
| `nba_master_hub_2026` | 560 | `bdl_id_1` | `(bdl_id)` |
| `nba_master_hub_2026` | 560 | `nba_id_1` | `(nba_id)` |
| `nba_odds_api_mapping_master` | 548 | `bdl_id_1` | `(bdl_id)` |
| `nba_pick_history` | 5,926 | `uniq_player_stat_line_date_side` | `(player, stat, line, game_date, side)` |
| `nba_player_context_features` | 17,531 | `player_event_stat_unique` | `(bdl_player_id, event_id, stat_type)` |
| `nba_prop_scores` | 42,561 | `uniq_canonical_version` | `(canonical_key, version_tag)` |
| `nfl_matchups` | 659 | `ix_nfl_matchup_sport_event_unique` | `(sport, event_id)` |
| `player_photos` | 282 | `player_name_1` | `(player_name)` |
| `pp_projection_id_cache` | 1 | `ix_league_id` | `(league_id)` |
| `replay_ingest_progress` | 1,438 | `uniq_sport_event_window` | `(sport_key, event_id, window_label)` |
| `sgo_pp_research_core` | 0 | `pp_anchor_pk` | `(event_id, player_id, stat_id, side, line, period_id)` |
| `sgo_pp_research_model_features` | 0 | `feature_anchor_pk` | `(event_id, player_id, stat_id, side, line, period_id)` |
| `sgo_pp_research_outcomes` | 0 | `outcome_anchor_pk` | `(event_id, player_id, stat_id, side, line, period_id)` |
| `shadow_vk_snapshots` | 493 | `shadow_uniq_key` | `(sport, player_name, stat_type, capture_date)` |
| `sync_locks` | 0 | `lock_key_uq` | `(lock_key)` |
| `team_context` | 0 | `ix_context_compound_unique` | `(event_id, team_id)` |
| `team_features` | 0 | `ix_features_compound_unique` | `(event_id, team_id, market, feature_set_version)` |
| `team_injuries` | 0 | `ix_injury_compound_unique` | `(event_id, team_id, player_id, reported_at)` |
| `team_master_hub` | 92 | `ix_team_id_unique` | `(team_id)` |
| `team_matchups` | 7,817 | `ix_matchup_event_id_unique` | `(event_id)` |
| `team_projections` | 0 | `ix_projection_compound_unique` | `(event_id, team_id, market, model_version)` |
| `team_prop_outcomes` | 0 | `ix_outcome_compound_unique` | `(event_id, team_id, market, line, side)` |
| `users` | 0 | `email_1` | `(email)` |

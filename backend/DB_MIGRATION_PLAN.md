# DB_MIGRATION_PLAN.md
Generated: 2026-06-11 — pick_vision database

**DO NOT EXECUTE** — Human review required before any action.
Each section must be confirmed individually before running.

---

## Section 1 — DROP: DemonGoblin Engine (26 collections)

The DemonGoblinEngine was deleted 2026-04-22. All `dg_*` collections have no active writers.
`dg_raw_odds_markets` has 5 service references but in dead code paths (the routes that called them
were deleted along with the engine).

**Total rows to delete: ~24,700**

```python
# Confirm before running. Verify no active writers via:
# grep -rn "dg_" /var/www/app/backend/routes/ --include="*.py" | grep -v "archive" | grep -v ".pyc"
collections_to_drop = [
    "dg_breaking_news",       # 50 rows
    "dg_cached_board",        # 49 rows
    "dg_cached_board_temp",   # 49 rows
    "dg_daily_insights",      # 5 rows
    "dg_dynamic_lines",       # 105 rows
    "dg_events_cache",        # 0 rows
    "dg_flagged_players",     # 0 rows
    "dg_front_lines",         # 3 rows
    "dg_goblin_recon",        # 0 rows
    "dg_goblin_vault",        # 0 rows
    "dg_injuries",            # 65 rows
    "dg_live_props",          # 2,697 rows
    "dg_locked_games",        # 0 rows
    "dg_market_catalog_cache",# 7,614 rows
    "dg_master_roster",       # 502 rows
    "dg_odds_cache",          # 0 rows
    "dg_parlay_builder",      # 0 rows
    "dg_player_data",         # 0 rows
    "dg_player_stats",        # 0 rows
    "dg_radar_picks",         # 0 rows
    "dg_raw_odds_markets",    # 13,547 rows
    "dg_social_signals",      # 0 rows
    "dg_static_shell",        # 0 rows
    "dg_stats_cache",         # 0 rows
    "dg_sync_log",            # 1 row
    "dg_sync_status",         # 2 rows
    "dg_trending",            # 0 rows
]
```

**Scripts to update after drop:** None — engine routes were deleted.

---

## Section 2 — DROP: Old Ferrari / Elite Tier Staging (17 collections)

Replaced by `nba_cached_board` / `mlb_cached_board`. No active writers.

**Total rows to delete: ~5,637**

```python
collections_to_drop = [
    "ferrari_discarded",      # 0 rows
    "ferrari_front_lines",    # 0 rows
    "ferrari_parlays",        # 0 rows
    "ferrari_picks",          # 15 rows
    "ferrari_safe_haven",     # 0 rows
    "ferrari_scored",         # 0 rows
    "ferrari_war_zone",       # 0 rows
    "elite_front_lines",      # 1 row
    "elite_safe_haven",       # 2 rows
    "elite_war_zone",         # 0 rows
    "mlb_elite_front_lines",  # 4 rows
    "mlb_elite_safe_haven",   # 6 rows
    "mlb_elite_war_zone",     # 1 row
    "nba_board",              # 120 rows — superseded by nba_cached_board
    "nba_tier_monitor",       # 0 rows
    "mlb_demons",             # 3,888 rows
    "mlb_goblins",            # 1,550 rows
]
```

**Caution:** `ferrari_tiers` is NOT in this list — it is still the named route collection (routes/ferrari_tiers.py reads from it).

---

## Section 3 — DROP: Old Sport-Specific Replay Pipeline (20 collections)

These are all superseded by the unified `sgo_propvision_full_pipeline_replay`.
The `nba_propvision_full_pipeline_outputs` alone has 3.9M rows — this frees significant disk.

**Total rows to delete: ~13,537,097**

```python
collections_to_drop = [
    "nba_propvision_full_pipeline_outputs",  # 3,898,274 rows
    "nba_propvision_full_pipeline_cards",    # 0 rows
    "nba_propvision_full_pipeline_runs",     # 0 rows
    "mlb_propvision_full_pipeline_outputs",  # 735,190 rows
    "mlb_propvision_full_pipeline_cards",    # 0 rows
    "mlb_propvision_full_pipeline_runs",     # 2,587 rows
    "mlb_replay_feature_cache",              # 15,787 rows
    "mlb_replay_feature_status",             # 92 rows
    "mlb_replay_gate_results",               # 1,402,377 rows
    "mlb_replay_model_outputs",              # 544,949 rows
    "mlb_replay_model_status",               # 1 row
    "mlb_production_replay_outputs",         # 2,006,669 rows
    "nba_replay_model_outputs",              # 3,710,997 rows
    "nba_replay_model_status",              # 1 row
    "team_replay_model_outputs",             # 0 rows
    "team_replay_outputs",                   # 0 rows
    "sgo_propvision_full_pipeline_replay_diff", # 0 rows
    "replay_evaluations",                    # 1,220,060 rows
    "replay_results",                        # 0 rows
    "replay_vk2_cache",                      # 113 rows
]
```

**Scripts to update after drop:** None — these collections are output targets, not sources.

---

## Section 4 — DROP: Other Stale Artifacts (24 collections)

**Total rows to delete: ~1,415,519**

```python
collections_to_drop = [
    "_tmp_mlb_safe_haven_5b969583",  # 10 rows
    "_tmp_mlb_safe_haven_d7506657",  # 10 rows
    "backtest_game_logs",             # 6,455 rows — old NBA-only, superseded by bdl_historical_game_logs
    "sgo_raw_responses",              # 0 rows
    "board_state_shadow",             # 2 rows
    "board_state_shadow_events",      # 0 rows
    "historical_odds",                # 112,314 rows — legacy, superseded by historical_odds_full
    "pp_payout_structure_tests",      # 0 rows
    "mlb_test_outputs",               # 0 rows
    "mlb_prodreplay_serial_counter",  # 1 row
    "nba_prodreplay_serial_counter",  # 1 row
    "oracle_apex_analyzed",           # 59,748 rows
    "nba_events_cache",               # 0 rows
    "nba_odds_cache",                 # 0 rows
    "odds_event_props_cache",         # 8 rows
    "live_scores_cache",              # 1 row
    "sgo_odds_outcomes",              # 0 rows
    "mlb_standard",                   # 225 rows — only in _archive_mlb_v1/
    "mlb_historical_logs",            # 6,645 rows — single-day snapshot, superseded by bdl_mlb_historical_game_logs
    "contract_violations",            # 0 rows
    "team_context",                   # 0 rows
    "team_features",                  # 0 rows
    "team_injuries",                  # 0 rows
    "team_projections",               # 0 rows
    "team_prop_outcomes",             # 0 rows
]
```

**Caution for `historical_odds`:** Confirm `scripts/backtest_real_lines.py` and `scripts/backtest_tier_qualified.py` are not being used. Both scripts read from it.

---

## Section 5 — Proposed Naming Convention

Current naming is inconsistent across four different patterns:
1. `sgo_pp_research_outcomes` — source prefix + pipeline stage
2. `nba_player_historical_props` — sport prefix + description
3. `bdl_advanced_stats` — data source prefix
4. `team_historical_outcomes` — entity prefix + stage

**Proposed convention:** `{sport}_{data_type}_{source_or_stage}`

Where:
- `{sport}` = `nba` | `mlb` | `nfl` | `ncaaf` | `team` | `shared`
- `{data_type}` = what the data IS (props, outcomes, features, boxscores, odds)
- `{source_or_stage}` = where it came from or which pipeline stage

### Proposed renames

These are proposals only — execution requires updating all scripts that reference each name.

| Current Name | Proposed Name | Rationale | Scripts to Update |
|---|---|---|---|
| `sgo_props_raw` | `shared_props_raw_sgo` | Source clarity | reshape_ncaaf_to_legacy_sgo.py |
| `sgo_events` | `shared_events_sgo` | Source clarity | reshape_ncaaf_to_legacy_sgo.py |
| `sgo_players` | `shared_players_sgo` | Source clarity | reshape_ncaaf_*.py, ingest_historical_player_stats.py |
| `sgo_player_stats` | `shared_player_stats_sgo` | Source clarity | build_historical_*.py, reshape_ncaaf_*.py |
| `sgo_book_consensus` | `shared_book_consensus_sgo` | Source clarity | — |
| `sgo_pp_research_core` | `shared_props_research_core` | Pipeline stage clarity | build_historical_*.py, reshape_sgo_to_replay_odds.py |
| `sgo_pp_research_core_enriched` | `shared_props_research_enriched` | Pipeline stage clarity | build_historical_*.py, reshape_sgo_to_replay_odds.py |
| `sgo_pp_research_outcomes` | `shared_props_outcomes` | Outcomes SSOT | build_player_features.py, build_player_prop_features.py |
| `sgo_pp_research_model_features` | `shared_props_model_features` | Feature set clarity | score_historical_model.py, build_historical_model_features.py |
| `sgo_pp_research_model_predictions` | `shared_props_model_predictions` | Prediction output | score_historical_model.py |
| `sgo_replay_alt_odds_raw` | `shared_odds_alt_raw_sgo` | Distinguish from consensus | run_sgo_production_replay.py, mlb_replay_build_feature_cache.py |
| `sgo_propvision_full_pipeline_replay` | `shared_replay_ssot` | Clearest SSOT name | audit_pipeline_parity.py, mirror_player_replay_to_unified.py, build_team_prop_features.py, reshape_*.py |
| `sgo_ncaaf_research_core` | `ncaaf_props_research_core` | Sport prefix | audit_ncaaf_*.py |
| `sgo_ncaaf_research_model_features` | `ncaaf_props_model_features` | Sport prefix | audit_ncaaf_*.py |
| `sgo_ncaaf_research_outcomes` | `ncaaf_props_outcomes` | Sport prefix | audit_ncaaf_*.py, diagnose_ncaaf_*.py |
| `team_historical_props` | `team_props_raw` | Stage clarity | — |
| `team_historical_outcomes` | `team_props_outcomes` | Stage clarity | build_team_features.py, build_team_prop_features.py, audit_pipeline_parity.py |
| `team_model_features` | `team_features_rolling` | Content clarity | build_nba_team_advanced_features.py, build_team_features.py, build_team_prop_features.py |
| `team_model_prop_features` | `team_props_features` | Parallel to player naming | historical_gate_replay_grid.py, build_team_prop_features.py, reshape_team_props_to_replay.py |
| `player_model_features` | `shared_player_features_rolling` | Parallel to team naming | build_player_features.py, build_player_prop_features.py |
| `player_model_prop_features` | `shared_player_props_features` | Parallel to team naming | historical_gate_replay_grid.py, build_player_prop_features.py, reshape_player_props_to_replay.py |
| `nba_player_historical_props` | `nba_props_raw_sgo` | Source clarity | player_historical_acquire.py, ingest_historical_player_stats.py, dedupe_player_historical_snapshots.py, workers/team/historical_player_ingest.py |
| `mlb_player_historical_props` | `mlb_props_raw_sgo` | Source clarity | same as above (mlb variant) |
| `nfl_player_historical_props` | `nfl_props_raw_sgo` | Source clarity | same as above (nfl variant) |
| `historical_odds_full` | `shared_odds_history_odds_api` | Source clarity | odds_api_backfill/*.py |
| `bdl_historical_game_logs` | `nba_box_scores_bdl` | Sport + type clarity | services/ (5 references) |
| `bdl_advanced_stats` | `nba_advanced_stats_bdl` | Sport + source clarity | build_nba_team_advanced_features.py, services/bdl_advanced_stats_fetcher.py |
| `research_grid_results` | `grid_results` | Simpler | historical_gate_replay_grid.py |
| `research_grid_runs` | `grid_runs` | Simpler | historical_gate_replay_grid.py |

### Lower-priority renames (optional)

These are clear enough already; only rename if the above renames are adopted for consistency:

| Current Name | Proposed Name |
|---|---|
| `replay_outcomes` | `shared_replay_outcomes` |
| `forward_test_snapshots` | `shared_forward_test_snapshots` |
| `optimizer_run_results` | `shared_optimizer_results` |

---

## Section 6 — Do NOT Rename (High Impact, Low Value)

These collections have too many references or are part of live production routes:

- `nba_master_hub_2026` — 8 service references, SSOT for NBA identity; rename needs COLL() registry update
- `mlb_master_hub_2026` — same as above
- `nba_cached_board` / `mlb_cached_board` — live read path; rename breaks ferrari_tiers route
- `nba_live_props` / `mlb_live_props` — active ingest targets; rename breaks multiple schedulers
- `nba_prop_scores` / `mlb_prop_scores` — read by board_service
- `injuries_normalized` / `live_injuries` — injury_service hardcoded references

---

## Section 7 — Merge Candidates (Not Recommended Without Schema Audit)

The following pairs are potentially mergeable but require a schema match confirmation first:

| Candidate A | Candidate B | Risk |
|---|---|---|
| `nba_player_historical_props` (8.2M) | `sgo_props_raw` (19.6M) | Different schemas — A is market-level, B is book-odds-level. Do NOT merge. |
| `bdl_historical_game_logs` (201K) | `bdl_nba_game_boxscores` (16K) | Different granularity — logs are per-player, boxscores are per-game. Do NOT merge. |
| `league_roster` (320) | `nba_master_roster` (636) | Both contain player roster data; schema review required before merge. |

---

## Drop Summary

| Section | Collections | Rows Freed |
|---------|-------------|-----------|
| 1 — DG Engine | 27 | ~24,700 |
| 2 — Ferrari/Elite | 17 | ~5,637 |
| 3 — Old Replay Pipeline | 20 | ~13,537,097 |
| 4 — Other Stale | 25 | ~1,415,519 |
| **Total** | **89** | **~14,982,953** |

Disk freed: estimated 15–25 GB depending on index overhead.

---

## Execution Order (when ready)

1. Confirm Section 1 (DG) — no live routes depend on dg_* (easiest, lowest risk)
2. Confirm Section 2 (Ferrari/Elite) — verify ferrari_tiers.py does NOT read these
3. Confirm Section 3 (Old Replay) — largest disk savings, but most rows
4. Confirm Section 4 (Other stale) — verify backtest_real_lines.py is retired
5. Rename pass (Section 5) — only after all drops are confirmed and scripts are updated

Each section should be a separate session with a MongoDB backup checkpoint before proceeding.

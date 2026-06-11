# HANDOFF
Generated: 2026-06-11T05:02:50.302807+00:00

## KEY RULES
- `rescore=False` always
- `game_total` / `team_total`: OVER-only training; UNDER = `1 - tp` at inference
- `h2h`: HOME-only training; AWAY gets feature swap + `1 - HOME_tp` at inference
- Training filter: `game_date >= 2025-01-01` AND `implied_probability != None`
- Grid dedup key: `(event_id, side, market_category, line)`
- Replay source: `sgo_propvision_full_pipeline_replay` where `clean_odds IS NOT NULL`

## KNOWN ISSUES
### P1 [BROKEN] game_total ROI inflation
**Detail:** min(implied) picks outlier low-implied alternate lines. 54% hit with +107% ROI impossible.
**Fix:** Add implied bounds (0.35, 0.65) filter in historical_gate_replay_grid.py _load_team()

### P2 [BROKEN] NBA h2h
**Detail:** Grid shows -12% to -27% ROI. May need more training data (64K vs 172K rows).
**Fix:** Investigate after game_total fix

### P3 [TODO] Run optimizer
**Detail:** No team optimizer exists. Grid results inspected manually.
**Fix:** Adapt historical_gate_replay_grid.py candidate_gate_configs into live router

## MODEL STATUS

| Sport | Market | AUC | Samples | Trained | SH n | SH hit% | SH ROI |
|-------|--------|-----|---------|---------|------|---------|--------|
| mlb | game_total | 0.7391 | 100,000 | 2026-06-10 | 1738 | 79.8% | +48.7% |
| mlb | h2h | 0.8921 | 100,000 | 2026-06-10 | 8568 | 91.4% | +19.3% |
| mlb | spread | 0.7745 | 100,000 | 2026-06-10 | 6221 | 81.7% | +12.1% |
| mlb | team_total | 0.6784 | 80,503 | 2026-06-10 | 1176 | 74.6% | +28.4% |
| nba | game_total | 0.7074 | 8,544 | 2026-06-11 | 386 | 71.0% | +29.4% |
| nba | h2h | 0.8071 | 16,480 | 2026-06-11 | 1109 | 92.8% | +6.2% |
| nba | spread | 0.6650 | 18,051 | 2026-06-11 | 1104 | 66.8% | +20.7% |
| nba | team_total | 0.7258 | 8,054 | 2026-06-11 | 538 | 72.3% | +31.1% |
| nfl | game_total | 0.6369 | 30,592 | 2026-06-01 | 4530 | 54.9% | -6.3% |
| nfl | h2h | 0.9998 | 37,785 | 2026-06-01 | 3962 | 81.4% | -2.5% |
| nfl | spread | 0.9548 | 30,032 | 2026-06-01 | 3984 | 62.5% | -1.1% |
| nfl | team_total | 0.8901 | 16,532 | 2026-06-01 | 2216 | 56.7% | -5.1% |

## GRID RESULTS

### 2024 season (2024-07-01 → 2024-11-01)

| Market | n | hit% | ROI | prob_min | edge_min | Flag |
|--------|---|------|-----|----------|----------|------|
| game_total | 119 | 70.6% | +89.3% | 0.75 | 0.01 |  |
| spread | 466 | 82.4% | +63.6% | 0.75 | — |  |
| h2h | 283 | 83.7% | +224.3% | 0.75 | 0.1 |  |
| team_total | 91 | 91.2% | +52.8% | 0.75 | 0.02 |  |

### 2025 season (2025-04-01 → 2025-10-01)

| Market | n | hit% | ROI | prob_min | edge_min | Flag |
|--------|---|------|-----|----------|----------|------|
| game_total | 722 | 81.7% | +167.8% | 0.75 | 0.02 |  |
| spread | 1210 | 88.6% | +73.3% | 0.75 | — |  |
| h2h | 683 | 96.6% | +89.1% | 0.75 | 0.1 |  |
| team_total | 123 | 56.1% | +214.5% | 0.7 | — | SUSPECT |

### 2026 season YTD (2026-04-01 → 2026-06-08)

| Market | n | hit% | ROI | prob_min | edge_min | Flag |
|--------|---|------|-----|----------|----------|------|
| game_total | 258 | 68.6% | +121.1% | 0.65 | — |  |
| spread | 337 | 87.2% | +72.1% | 0.75 | — |  |
| h2h | 104 | 72.1% | +25.0% | 0.75 | — |  |
| team_total | 59 | 69.5% | -22.6% | 0.75 | — |  |

## COLLECTION HEALTH

| Collection | Count | Min Date | Max Date |
|------------|-------|----------|----------|
| team_matchups | 7,164 | 2024-07-01 | 2026-06-19 |
| team_historical_outcomes | 1,893,834 | 2024-07-05 | 2026-05-30 |
| team_model_features | 13,588 | 2024-07-05 | 2026-06-10 |
| team_model_prop_features | 1,798,949 | 2024-07-05 | 2026-05-30 |
| sgo_propvision_full_pipeline_replay | 4,944,404 | 2024-07-05 | 2026-05-30 |
| research_grid_results | 232,280 | None | None |
| bdl_mlb_game_boxscores | 5,783 | 2024-04-01 | 2026-06-08 |
| odds_api_team_h2h | 7,427 | 2024-07-01 | 2026-06-11 |

## SESSION LOG
- 2026-06-10 23:13 UTC  fixed='pipeline parity audit added'

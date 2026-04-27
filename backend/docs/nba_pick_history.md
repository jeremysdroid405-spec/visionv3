# NBA Pick History — Forward-Testing System

Persistent forward-testing for picks selected by the UniversalGateEngine.

## Architecture

```
       ┌──────────────────────────────┐
       │   recompute.recompute_sport  │
       │   ↓                          │
       │   compute_scoring_stack ────►│  every prop scored
       │   ↓                          │
       │   _apply_vision_score …      │  slate-percentile vision_score
       │   ↓                          │
       │   _reevaluate_tiers_post_… ──│  authoritative tier assignment
       │   ↓                          │
       │   ★ pick_history.log_… ─────►│  selected picks → nba_pick_history
       │   ↓                          │
       │   write_versioned_scores     │  prop_scores collection
       └──────────────────────────────┘

       Cron 09:35 UTC daily:
       update_nba_pick_results ─────► hit/result/actual fields
```

The hook lives in `services/scoring/recompute.py:684` (after
`_reevaluate_tiers_post_vision`), is wrapped in a try/except so a
logger failure can never break scoring.

## Schema (`nba_pick_history` collection)

| Field | Type | Description |
|---|---|---|
| `timestamp` | datetime | When the row was first inserted (UTC). |
| `game_date` | string | YYYY-MM-DD of the game. |
| `player`, `stat`, `line`, `side` | str/float | Identity tuple. |
| `tier` | str | safe_haven / front_lines / war_zone. |
| `mu`, `sigma`, `p_model` | float | Production model values. |
| `tp`, `edge`, `vision_score` | float | Market and selection signals. |
| `expected_minutes`, `availability_status` | float / str | Rate-layer audit. |
| `rfa_penalty_applied`, `rfa_penalty_factor` | bool / float | RFA audit. |
| `book_odds`, `devig_source` | int / str | Reference market. |
| `board_fingerprint` | str | Stable hash of the slate (audit trail). |
| `result`, `actual`, `hit` | str / float / bool | Filled by the result updater. |
| `model_version` | str | e.g. `nba_v3_100_0_rfa_0.85`. |
| `canonical_key`, `event_id` | str | Re-link to source score doc. |

### Indexes

| Name | Fields | Unique |
|---|---|---|
| `uniq_player_stat_line_date_side` | `(player, stat, line, game_date, side)` | ✅ yes |
| `hit_date` | `(hit, game_date)` | no |
| `model_version` | `(model_version,)` | no |
| `tier_date` | `(tier, game_date)` | no |

## Idempotency contract

Re-running a slate's recompute upserts on the unique index:
- **First write:** all schema fields including `result`/`actual`/`hit`=`null` via `$setOnInsert`.
- **Subsequent writes:** every model field is refreshed via `$set`, but `result`, `actual`, `hit`, and `timestamp` are preserved (`$setOnInsert` only). A graded row never gets ungraded.

## Stat families (canonical, post-2026-05)

The hook normalizes raw market names (e.g. `player_points_alternate`)
to canonical stat families before logging. Analytics group cleanly
on these:

```
PTS    REB    AST    THREES    PRA    PTS_REB    PTS_AST    REB_AST
STL    BLK    TURNOVERS
```

## Result updater

```bash
# Grade everything still ungraded
python -m scripts.update_nba_pick_results

# Dry run — report only
python -m scripts.update_nba_pick_results --dry

# Only grade rows on or after a specific date
python -m scripts.update_nba_pick_results --since 2026-04-01
```

Logs join against (in priority order):
1. `nba_master_hub_2026.bdl_game_logs` (current season)
2. `bdl_historical_game_logs` (5 prior seasons)
3. `nba_player_game_logs` (legacy)

## CRON

`services/cron_scheduler.py` registers a job at **09:35 UTC** (5 min
after the existing `forward_test_daily_resolve` so master-hub logs
are guaranteed fresh). Idempotent — only `hit is null` rows are
touched per run.

## Analytics queries

```python
from services.forward_test.pick_history import (
    query_overall, query_by_stat, query_by_tier,
    query_by_edge_bucket, query_by_availability, query_by_side,
)

await query_overall(db)
# → {'key': None, 'n': 650, 'wins': 296, 'losses': 354,
#    'win_rate': 45.54, 'roi_110': -13.06}

await query_by_tier(db)
# → [{'key': 'safe_haven',  'n': 106, 'win_rate': 73.58, 'roi_110':  40.48},
#    {'key': 'front_lines', 'n': 299, 'win_rate': 53.51, 'roi_110':   2.16},
#    {'key': 'war_zone',    'n': 245, 'win_rate': 23.67, 'roi_110': -54.81}]

await query_by_stat(db)         # PTS/REB/AST/THREES/PRA/...
await query_by_edge_bucket(db)  # 0–5%, 5–10%, 10–15%, 15%+
await query_by_availability(db) # FULL_GO, RFA, MR, DNP_RISK, ...
await query_by_side(db)         # OVER vs UNDER

# Filter by model version
await query_overall(db, model_version="nba_v3_100_0_rfa_0.85")
```

Every aggregator returns:

```python
{"key": str|None, "n": int, "wins": int, "losses": int,
 "win_rate": float,           # %, 2-decimal
 "roi_110": float}            # %, 2-decimal, standard sportsbook payout
```

## Testing

```bash
cd /app/backend
python -m pytest tests/test_pick_history.py -v
# ✓ 8/8 passing
```

The smoke test seeded 1,817 picks from `nba_prop_scores` +
`nba_prop_scores_archive_stale_tags`, graded 650 of them, and
validated every analytics surface returns canonical-shaped rows.

## Files

| File | Purpose |
|---|---|
| `services/forward_test/__init__.py` | package marker |
| `services/forward_test/pick_history.py` | logger + analytics |
| `services/scoring/recompute.py` | hook (1 try/except block, ~20 lines) |
| `scripts/update_nba_pick_results.py` | CLI grader |
| `services/cron_scheduler.py` | nightly grade job |
| `tests/test_pick_history.py` | unit + integration tests |
| `docs/nba_pick_history.md` | this file |

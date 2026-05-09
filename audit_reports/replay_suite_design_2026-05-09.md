# PropVision Ultimate Historical Replay Test Suite — Design Doc
**Status:** DESIGN ONLY · no production patches · no live gates touched
**Date:** 2026-05-09
**Author:** E1 (handoff continuation)
**Audience:** PropVision team — pre-implementation review

> Build the strongest possible historical validation system that can answer
> *“is this product ready for paying users?”* and reproduce that answer on
> every model / gate / scoring change.

---

## 0. Executive overview

We have:

- **5,000,000** Odds API credits (≈ 4.41M still available as of audit B run).
- An existing **historical odds ingest pipeline** (`backend/scripts/odds_api_backfill/`, collection `historical_odds_full`, multi-sport, deduping). It already covers 3 snapshot windows.
- An existing **scoring stack** (`services/scoring/scoring_stack.py::compute_scoring_stack`) that already emits the canonical `vision_score / vision_score_v2 / tier / cv / hit_rate_l5/10/20 / p_true_active / edge_vs_fair / reference_book / reference_odds / EV` per prop. **All replay must call this exact code path** (no forks, no copies).
- An existing **forward-testing lineage** boundary at `2026-04-25` (`services/forward_testing_lineage.py`) that we will keep untouched and **never mix with replay output**.

What we **don’t** have yet:

- Snapshot plan with the full 8-window pregame ladder (we currently capture 3).
- A **resolver** that turns Odds API events + box scores into actual game results.
- A **replay engine** that scores stored snapshots against current code on demand.
- A **comparison tool** that diffs two replay runs.
- Versioned runs (`replay_run_id` + git commit + config hashes).

This doc designs all four. We **ingest historical odds and box scores once**, then **replay many times** as code evolves.

---

## 1. Architecture (the big picture)

```
                ┌────────────────────────────────────────────────────────────┐
                │                       INGEST LAYER (ONCE)                  │
                │                                                            │
   Odds API ───▶│  odds_api_backfill (existing, extended)                   │
                │     • 8-window snapshot plan                              │
                │     • all confirmed markets (PTS/REB/AST/3PM + 4 combos +  │
                │       all `_alternate`)                                   │
                │     • books: dk, fd, betonlineag, williamhill_us, mgm,     │
                │       pinnacle (when present)                             │
                │     • writes: replay_odds_snapshots (NEW, isolated from   │
                │       historical_odds_full so we never collide with the   │
                │       existing backfill)                                  │
                │                                                            │
   Box scores ─▶│  replay_results_ingester (NEW)                            │
                │     • bdl/statsapi/etc → actual final stats per player     │
                │     • writes: replay_results                              │
                └────────────────────────────────────────────────────────────┘
                                         │
                                         ▼
                ┌────────────────────────────────────────────────────────────┐
                │                  NORMALIZATION LAYER (ONCE)                │
                │                                                            │
                │  replay_normalizer (NEW)                                   │
                │     • produces one canonical-prop doc per (event, player,  │
                │       stat_type, line, side, snapshot, book) tuple         │
                │     • carries reference-odds chain consensus (DK→FD→…)    │
                │     • writes: replay_props_normalized                     │
                └────────────────────────────────────────────────────────────┘
                                         │
                                         ▼
                ┌────────────────────────────────────────────────────────────┐
                │              REPLAY SCORING LAYER (MANY)                   │
                │                                                            │
                │  replay_engine (NEW)                                       │
                │     • iterates replay_props_normalized                     │
                │     • calls services.scoring.scoring_stack.compute_        │
                │       scoring_stack(...) WITH CURRENT CODE                 │
                │     • records every emitted dimension + gate-fail reasons  │
                │     • writes: replay_runs (run header)                    │
                │     • writes: replay_evaluations (one row per scored prop)│
                │                                                            │
                │  replay_resolver (NEW)                                     │
                │     • joins evaluations with replay_results                │
                │     • computes hit/miss/push, ROI, CLV, calibration gap    │
                │     • writes: replay_outcomes                             │
                └────────────────────────────────────────────────────────────┘
                                         │
                                         ▼
                ┌────────────────────────────────────────────────────────────┐
                │                 ANALYTICS LAYER (MANY)                     │
                │                                                            │
                │  replay_reports (NEW)                                      │
                │     • 10 test suites (tier perf / WZ longshot / direction  │
                │       gate / reference odds / timing / CLV / injury /      │
                │       alt-ladder calibration / model calibration / gate    │
                │       sensitivity)                                        │
                │     • writes: replay_calibration_reports,                  │
                │       replay_gate_sweeps, replay_market_movements          │
                │                                                            │
                │  compare_replay_runs (NEW)                                 │
                │     • diffs two run ids                                    │
                │     • outputs human-readable + JSON delta                  │
                └────────────────────────────────────────────────────────────┘
```

### Design principles

1. **Decouple ingest from replay.** Snapshots + results are facts; replays are functions of code. We must be able to rebuild every metric without re-querying The Odds API.
2. **No forks of scoring.** Replay calls `compute_scoring_stack()` directly. If a code change breaks compatibility, the replay run should fail loudly — never silently regress.
3. **No mutation of live collections.** `prop_scores`, `*_cached_board`, `forward_test_*`, `historical_odds_full` are **read-only** to the replay engine. New collections are prefixed `replay_*`.
4. **Lineage isolation.** Replay outcomes carry `dataset_lineage="historical_replay"`. The forward-testing lineage filter (`MODERN_SSOT_CUTOFF`) **must continue to exclude replay docs** — we will add an explicit lineage tag and update the filter to ignore anything not tagged `legacy_vk` / `modern_ssot`.
5. **Versioning is non-negotiable.** Every run captures `git_commit`, scoring/gate config hashes, code version, model version, replay date range, markets included, books included, snapshot windows included.
6. **Idempotency.** Re-running the same `(replay_run_id, config_fingerprint)` is a no-op. Re-running with a new fingerprint creates a new run.

---

## 2. Collection schema

Eight new collections, all prefixed `replay_*`, all isolated from live data.

### 2.1 `replay_events`
Catalog of historical NBA/MLB/NFL games we have ingested.

| field | type | notes |
|---|---|---|
| `_id` | str | `"{sport_key}|{event_id}"` |
| `sport_key` | str | `basketball_nba` / `baseball_mlb` / `americanfootball_nfl` |
| `event_id` | str | Odds API uuid hex |
| `commence_time` | datetime | UTC |
| `game_date` | str | `YYYY-MM-DD` UTC |
| `home_team` / `away_team` | str | |
| `season` | str | derived (`2024-25` etc.) |
| `season_type` | str | `regular` / `playoff` |
| `result_status` | str | `pending` / `final` / `postponed` |
| `result_ingested_at` | datetime |
| `ingested_at` | datetime |

Indexes: `(sport_key, game_date)`, `(sport_key, commence_time)`.

### 2.2 `replay_odds_snapshots`
Raw payload-level archive — keeps the full bookmaker × market payload for every snapshot. **One doc per (event, snapshot_label, market)**, *not* per row, so we can re-flatten with new logic without re-querying.

| field | type | notes |
|---|---|---|
| `event_id` | str | |
| `sport_key` | str | |
| `market_key` | str | |
| `snapshot_label` | str | `t-24h` / `t-12h` / `t-6h` / `t-3h` / `t-90m` / `t-60m` / `t-30m` / `close` |
| `requested_ts` | datetime | what we asked for |
| `returned_ts` | datetime | API’s nearest `timestamp` |
| `previous_ts` / `next_ts` | datetime | from envelope |
| `region` | str | `us` |
| `bookmakers` | list[dict] | full untouched payload |
| `payload_hash` | str | sha256 of canonical JSON |
| `credits_charged` | int | from `x-requests-last` |
| `ingested_at` | datetime |

Indexes: unique `(event_id, market_key, snapshot_label)`; `(sport_key, snapshot_label)`; `(payload_hash)`.

### 2.3 `replay_props_normalized`
Flat row layer derived from raw snapshots. **Recreatable any time** by replaying the normalizer. One row per `(event, snapshot, market, book, player, line, side)`.

| field | type |
|---|---|
| `event_id`, `sport_key`, `commence_time`, `home_team`, `away_team` | … |
| `snapshot_label`, `snapshot_ts`, `minutes_before_start` | int (negative = pre-tip) |
| `bookmaker` | str |
| `market_key`, `is_alternate`, `is_combo`, `stat_family` | … |
| `player`, `line`, `side`, `odds_american`, `implied_probability` | … |
| `last_update` | from book payload |
| `canonical_key` | `"{sport}|{market}|{player}|{line}"` (mirrors prod) |
| `normalized_at` | datetime |
| `normalizer_version` | str |

Indexes: `(event_id, snapshot_label, bookmaker, market_key, player, line, side)` unique; `(canonical_key, snapshot_label)`; `(player, snapshot_label)`.

### 2.4 `replay_results`
Player-game actual outcomes.

| field | type |
|---|---|
| `event_id`, `sport_key`, `commence_time`, `game_date` | |
| `player`, `player_id`, `team` | canonical lower-case |
| `did_play` | bool |
| `dnp_reason` | str (injury / coach / ejection / null) |
| `pts`, `reb`, `ast`, `threes`, `stl`, `blk`, `to`, `pra`, `pr`, `pa`, `ra` | int |
| `mlb_*` | sport-specific |
| `nfl_*` | sport-specific |
| `ingested_at`, `result_source` | str |

Indexes: unique `(event_id, player)`; `(sport_key, game_date)`; `(player, sport_key)`.

### 2.5 `replay_runs`
Run header (one doc per replay invocation).

| field | type |
|---|---|
| `_id` | str | uuid |
| `run_name` | str | human label, e.g. `vk2_combo_fix_v1` |
| `created_at` | datetime |
| `git_commit` | str | `subprocess.check_output(['git','rev-parse','HEAD'])` |
| `git_dirty` | bool | true if uncommitted changes |
| `code_version` | str | e.g. PRD `Stabilization Status` line, hashed |
| `scoring_config_hash` | str | sha256 of relevant config files |
| `gate_config_hash` | str | sha256 of `gates/thresholds.py + overrides.py` |
| `odds_source_version` | str | normalizer_version actually consumed |
| `range_start` / `range_end` | datetime |
| `sport_keys` | list[str] |
| `markets` | list[str] |
| `books` | list[str] |
| `snapshot_windows` | list[str] |
| `selected_snapshot_for_scoring` | str | which snapshot is the “canonical” evaluation point per run (default `t-90m`); other snapshots scored separately |
| `notes` | str |
| `evaluations_count` | int |
| `outcomes_count` | int |
| `dataset_lineage` | str | always `"historical_replay"` |
| `status` | str | `running` / `complete` / `failed` |

Indexes: `(created_at desc)`, `(run_name)`.

### 2.6 `replay_evaluations`
One scored prop per row.

| field | type | notes |
|---|---|---|
| `replay_run_id` | str | FK |
| `event_id`, `commence_time`, `sport_key` | … |
| `snapshot_label`, `snapshot_ts`, `minutes_before_start` | … |
| `canonical_key` | str |
| `player`, `stat_type`, `line`, `recommendation`, `direction`, `is_alternate`, `is_combo` | … |
| `reference_book`, `reference_odds`, `reference_chain_used` | str (e.g. `dk` or `dk→fd`) |
| `dk_odds`, `fd_odds`, `mgm_odds`, `bol_odds`, `pinnacle_odds`, `wh_odds` | int (when present) |
| `pp_odds` | always null (replay does not use PP odds) |
| `model_projection` | float |
| `p_true_active`, `p_true_method` | … |
| `vision_score`, `vision_score_v2` | … |
| `cv` | float |
| `hit_rate_l5/10/20` | float |
| `tier`, `tier_reason` | … |
| `gate_pass` | bool |
| `gate_fail_reasons` | list[str] |
| `edge_vs_fair` | float |
| `ev_per_dollar` | float |
| `evaluated_at` | datetime |

Indexes: `(replay_run_id, tier)`, `(replay_run_id, canonical_key)`, `(replay_run_id, snapshot_label)`.

### 2.7 `replay_outcomes`
Resolved settlement per evaluation.

| field | type |
|---|---|
| `replay_run_id`, `event_id`, `canonical_key`, `snapshot_label`, `bookmaker`, `side`, `line` | … |
| `actual_stat_value` | float |
| `outcome` | `hit` / `miss` / `push` / `void_dnp` |
| `realized_payout_per_dollar` | float (American → decimal − 1, push/void = 0) |
| `clv_implied_prob` | float | closing snapshot fair prob |
| `clv_ev` | float | edge of our entry vs closing line |
| `closing_line_value_pct` | float |
| `line_movement` | float | line at entry – line at close |
| `odds_movement` | int | odds at entry – odds at close (book-specific) |
| `model_calibration_gap` | float | `p_true_active – realized_indicator` |
| `resolved_at` | datetime |

Indexes: `(replay_run_id, outcome)`, `(replay_run_id, tier_at_eval)` (denormalized copy for fast aggregation).

### 2.8 `replay_gate_sweeps`, `replay_market_movements`, `replay_calibration_reports`
Generated artefacts of the analytics layer. Free-form schema, all keyed by `replay_run_id`. Documented in §6.

---

## 3. API credit cost estimate

### Per-event cost (NBA)

12 markets currently configured for NBA (see `_NBA_MARKETS`):

```
PRA                              + PRA_alt
PTS_alt, REB_alt, AST_alt, 3PM_alt
PTS_REB_alt, PTS_AST_alt, REB_AST_alt
BLK, STL, DD
```

Plus the four base markets (PTS, REB, AST, 3PM) we normally pull live = **16 markets**.

Per `/v4/historical/.../events/{id}/odds` call: **10 credits × markets × regions**.

The existing client **already bundles markets per call** (see `client.py` docstring), so per event per snapshot = **160 credits** (16 × 10 × 1 region) — *not* 160 calls.

**Per slate (8 events average × 8 snapshot windows × 16 markets):**

```
events_listing : 1 credit × 8 windows = 8 credits
events_odds    : 8 events × 8 windows × 16 markets × 10 = 10,240 credits
─────────────────────────────────────────────────────────────────────
                                                  ≈ 10,250 / slate
```

### Per-month cost (NBA, ~15 game days × ~10 events per day during regular season)

| target | events | credits |
|---|---|---|
| 1 day (8 events) | 8 | ~10,250 |
| 1 week | ~50 | ~64,000 |
| 30 days (MVP) | ~150 | **~192,000 credits** |
| Full 2024-25 NBA season (≈ 1,400 games) | 1,400 | ~1,800,000 credits |
| Full 2023-05-03 → today (~ 2 seasons + post-2023 playoffs) | ~2,800 | ~3,600,000 credits |

### Trim levers (in order of preference)
1. **Drop the 24h window** → −12.5% cost. Pre-game lines barely move that early; not worth it for snapshot evaluation.
2. **Drop alt ladders we don’t score on** (e.g. THREES_alt rarely hits WZ) → −20%.
3. **Use 6 windows instead of 8** (drop 12h + 90m) → −25%.
4. **Skip `replay_odds_snapshots` retention of full bookmakers payload** for non-pivotal windows (keep only the books we score on). Storage saving — not credits.

### Recommendation
- **MVP:** 30 days NBA, **6 windows** (`t-24h`, `t-6h`, `t-3h`, `t-60m`, `t-30m`, `close`), **all 16 markets** → ~144,000 credits.
- **Full Phase 1:** 180 days NBA (whole season) at 6 windows → ~860k credits.
- **Phase 2 MLB / Phase 3 NFL** are estimated separately when those configs are populated.

We have plenty of credits. **The bottleneck is wallclock time, not budget.** With the existing async client (20-RPS semaphore), 192k credits at ~80 ms per call ≈ 13 hours of ingest. Comfortable overnight job.

---

## 4. Replay loader design

### 4.1 Snapshot plan
`backend/services/replay/snapshot_plan.py` (NEW) — pure function returning the timestamps to query for a given `commence_time`:

```
WINDOWS = [
    ("t-24h",  -24*60),
    ("t-12h",  -12*60),
    ("t-6h",    -6*60),
    ("t-3h",    -3*60),
    ("t-90m",   -90),
    ("t-60m",   -60),
    ("t-30m",   -30),
    ("close",   -5),     # 5m before tip — closest-to-close that books still post
]
```

`snapshot_for(commence_time, label)` → ISO8601 Z, suitable for The Odds API.

### 4.2 Ingest loop
Reuse `OddsAPIClient` and `_flatten_event_odds` from `odds_api_backfill/`. New writer targets `replay_odds_snapshots` (raw envelope) **and** `replay_props_normalized` (flat rows). Idempotent via the unique compound index.

```python
for event in list_events(date):
    for label, offset in WINDOWS:
        snap_ts = event.commence_time + timedelta(minutes=offset)
        for batch_of_markets in batched(MARKETS, n=16):
            payload = client.get_historical_event_odds(
                sport, event.id, batch_of_markets, regions=["us"], snap_ts)
            store_raw_snapshot(payload, event, label)
            store_normalized_rows(payload, event, label)
```

### 4.3 Result resolver
`backend/services/replay/result_ingester.py` (NEW). Pulls actual game stats:
- **NBA**: `nba_master_hub_2026.player_game_logs` if it exists; else BallDontLie historical endpoint (already integrated for rolling windows).
- **MLB**: `mlb_master_hub_2026.player_game_logs` likewise.
- **NFL**: TBD in phase 3.

Idempotent upsert into `replay_results` keyed by `(event_id, player)`.

### 4.4 Replay engine
`backend/services/replay/engine.py` (NEW). Loads canonical props from `replay_props_normalized` for a given `(date_range, sport, snapshot_label)` and calls **the live `compute_scoring_stack()`** with adapter-specific inputs. Emits `replay_evaluations`. **No copy of scoring code.**

Replay loop (per run):
```python
run = create_run_header(name=…, git_commit=…, …)
for evt in events_in_range:
    rolling_features = build_features_at(evt.commence_time - 90m)  # mu, hr, vk2, cv
    for snap in WINDOWS:
        for prop in props(evt, snap):
            scored = compute_scoring_stack(prop, rolling_features, ...)
            insert(replay_evaluations, {run, evt, snap, prop, scored})
finalize_run(run)
```

### 4.5 Resolver
`backend/services/replay/resolver.py` (NEW). For each `replay_evaluation`, look up the actual stat from `replay_results`, compute `hit/miss/push`, payout, CLV (vs closing snapshot), calibration gap. Writes `replay_outcomes`.

### 4.6 Critical: feature-set determinism
The hardest correctness problem in any replay system is **as-of-time leakage**. We MUST compute rolling features (`hit_rate_l5/10/20`, `vk2`, `mu`) using **only games whose `commence_time < eval_snapshot_ts`**.

We will:
- Keep the feature builders in `backend/services/replay/features.py` (NEW), wrapping current production feature code with an `as_of_ts` cutoff parameter.
- Cache feature snapshots per `(player, as_of_ts_to_minute)` in a `replay_feature_cache` collection (idempotent, content-hashed).
- Add 4 mutation tests that assert: feeding a feature builder with games strictly after the cutoff returns *byte-identical* results to feeding it before (i.e. no leakage path).

This is the single biggest risk in the whole system; it gets a dedicated test file and a dedicated review.

---

## 5. First MVP script plan

### Phase A — Schema + scaffolding (this PR, no execution)
- `backend/services/replay/__init__.py`
- `backend/services/replay/schema.py` — `ensure_indexes(db)` for all 8 collections
- `backend/services/replay/snapshot_plan.py` — windows + helper
- `backend/services/replay/markets.py` — book whitelist, market whitelist
- `backend/services/replay/run_header.py` — versioning helper
- `backend/scripts/run_replay.py` — CLI stub that prints planned actions, **no API calls yet**
- `backend/scripts/compare_replay_runs.py` — CLI stub
- `backend/tests/test_replay_schema.py` — index assertions
- `backend/tests/test_replay_snapshot_plan.py` — window math
- `backend/tests/test_replay_run_header.py` — versioning fingerprints

### Phase B — Ingest (next PR)
- `backend/services/replay/ingest_odds.py` — wraps existing `OddsAPIClient`, fans out 8 windows
- `backend/services/replay/ingest_results.py` — pulls box scores
- `backend/services/replay/normalizer.py` — flatten raw → `replay_props_normalized`
- Integration test: ingest 1 event, 2 windows, 1 market → ~20 credits to verify shape

### Phase C — Replay engine (next-next PR)
- `backend/services/replay/features.py` (with leakage tests)
- `backend/services/replay/engine.py`
- `backend/services/replay/resolver.py`
- Integration test: 1 event end-to-end, mock results, no API calls

### Phase D — Analytics & 10 suites (one PR per suite if needed)
- `backend/services/replay/reports/tier_perf.py`
- `backend/services/replay/reports/wz_longshot.py`
- `backend/services/replay/reports/direction_gate.py`
- `backend/services/replay/reports/reference_odds.py`
- `backend/services/replay/reports/timing.py`
- `backend/services/replay/reports/clv.py`
- `backend/services/replay/reports/injury_timing.py`
- `backend/services/replay/reports/alt_ladder_calibration.py`
- `backend/services/replay/reports/model_calibration.py`
- `backend/services/replay/reports/gate_sensitivity.py`

### Phase E — Comparison tool
- `backend/services/replay/compare.py` — `compare_replay_runs(run_a, run_b)` returns a structured diff doc
- `backend/scripts/compare_replay_runs.py` — CLI front-end + Markdown report writer
- Outputs to `/app/audit_reports/replay_diffs/{run_a}_vs_{run_b}.md`

---

## 6. Validation report template

Every replay run produces a single Markdown report (committed to `/app/audit_reports/replay_runs/{run_id}.md`) with these sections:

```
# Replay Run {run_name} ({run_id[:8]})
- created_at, git_commit, git_dirty
- range_start..range_end | sport(s) | markets | books | windows
- evaluations: N | outcomes: M | settled: K
- wallclock: HH:MM:SS

## 1. Tier performance
| tier | n | hit_rate | ROI | EV_predicted | EV_realized | Δ |
|safe_haven|...

## 2. War Zone longshot suite
strict-direction vs relaxed-direction vs longshot exception
HR buckets, odds buckets, EV buckets, CV buckets

## 3. Direction gate suite
strict μ>line | tail-prob | EV-only | hybrid | longshot exception

## 4. Reference odds suite
dk-first | fd-first | dk+fd consensus | best | worst | multi-book devig

## 5. Timing suite
per-window {t-24h..close} per-tier hit-rate, ROI, CLV, n

## 6. CLV suite
% beat close | mean CLV | tier-wise correlation(CLV, ROI)

## 7. Injury timing suite
events with injury delta within 6h pre-tip:
  pre-vs-post line/odds movement, tier change, model edge change

## 8. Alt ladder calibration suite
per (player, stat) ladder depth, model-tail-prob vs realized-tail-rate

## 9. Model calibration suite
predicted-prob bucket | n | realized rate | calibration_gap | reliability slope

## 10. Gate sensitivity suite
per-gate sweep (HR / CV / edge / v2 / direction / book-coverage):
  pick count, hit rate, ROI, FP, FN

## Summary scorecard
- safe_haven roi → green/red
- front_lines roi → green/red
- war_zone roi → green/red
- model calibration mae → green/red
- recommendation (single line)
```

JSON sibling at `replay_calibration_reports/{run_id}.json` for diffing.

---

## 7. Rollout phases

| phase | scope | success exit criteria |
|---|---|---|
| **0. Scaffolding (this PR)** | schema, snapshot_plan, run_header, CLI stubs, schema tests | all unit tests green; `python scripts/run_replay.py --plan-only` prints a coherent execution plan; **no DB writes, no API calls** |
| **1. NBA MVP ingest** | 30 days × 6 windows × 16 markets (≈ 144k credits) | full coverage, deduped, all 8 collections populated, idempotent rerun cost = 0 credits |
| **2. Replay engine + resolver** | re-score MVP slice with current code, resolve outcomes, generate v0 reports | one full run completes in < 30 min wallclock; outcomes diff vs forward-test on overlap window is explainable |
| **3. 10-suite analytics** | per-suite reports | every suite has at least 1 mutation test asserting a known regression is caught |
| **4. Comparison tool** | `compare_replay_runs(a, b)` | round-trip tested by running base & candidate on same data with identical config → diff = empty (±tolerance) |
| **5. Phase 1 full-season NBA backfill** | season 2023-05-03 → today, 6 windows | first answer to “is product ready for paying users?” |
| **6. Phase 2 MLB** | populate `_MLB_MARKETS` mapping in schema, repeat | feature parity with NBA |
| **7. Phase 3 NFL** | populate NFL markets | feature parity |

---

## 8. Failure risks (and how we kill each)

| risk | severity | mitigation |
|---|---|---|
| **As-of-time feature leakage** (replay sees future games) | catastrophic — invalidates every conclusion | feature builder accepts mandatory `as_of_ts`; mutation tests assert no leakage; replay engine refuses to score without `as_of_ts` |
| **Reference-odds chain divergence** vs prod | high — turns CLV into garbage | replay reuses production `_pick_reference_odds()` directly. No fork. |
| **Result ingester missing players (DNP)** | high — biases hit-rate up or down depending on logic | `replay_results` distinguishes `did_play=False` from missing data; replay marks as `void_dnp` not `miss`; suite #1 reports `n_missing` separately |
| **Single-sided alt outcomes from books** (DK PRA-alt = Over only) | medium — under-counts populations | normalizer marks rows with `is_one_sided=True`; suite #8 reports per-book one-sidedness; we don’t synthesise the missing side |
| **Model drift between ingest time and replay time** | medium — non-replicable runs | `replay_runs.scoring_config_hash` + `gate_config_hash`; comparison tool surfaces hash diffs in red |
| **Forward-test contamination** | high — leaks legacy vk_* into replay scorecard | replay docs carry `dataset_lineage="historical_replay"`; forward-testing lineage filter explicitly excludes anything not in `{"legacy_vk", "modern_ssot"}` |
| **Credit blowout** | low (5M pool, plenty of headroom) but worth a guard | client already enforces `min_remaining_credits` floor; orchestrator emits a hard halt at run-level cap |
| **Idempotency bugs (re-ingest creates dups)** | medium | unique compound indexes on raw snapshot + normalized rows; integration test asserts second run inserts 0 |
| **Replay results lag** (boxscores not yet in DB) | medium | resolver flags `result_status=pending`; rerun completes them later without re-ingesting odds |
| **Comparison tool false positives from non-determinism** | medium | scoring_stack must be deterministic given identical inputs; mutation test seeds RNG and asserts byte-identical outputs across two runs on same data |

---

## 9. Exact commands to run (post-implementation)

After Phase 0–4 land:

```bash
# A. Sanity check the plan (no API, no DB)
python /app/backend/scripts/run_replay.py \
    --plan-only \
    --range 2024-01-01:2024-02-01 \
    --sport nba

# B. Ingest historical odds + box scores (one-time, ~144k credits for 30 days)
python /app/backend/scripts/run_replay.py \
    --ingest \
    --range 2024-01-01:2024-02-01 \
    --sport nba \
    --windows t-24h,t-6h,t-3h,t-60m,t-30m,close \
    --markets all \
    --books dk,fd,betonlineag,williamhill_us,mgm,pinnacle

# C. Replay scoring against ingested data
python /app/backend/scripts/run_replay.py \
    --replay \
    --range 2024-01-01:2024-02-01 \
    --sport nba \
    --markets all \
    --run-name "vk2_combo_fix_v1" \
    --notes "post combo VK2 routing fix; baseline measurement"

# D. Make a code change, replay again
git checkout -b experiment/wz-direction-gate-relaxed
# ...edit gates...
python /app/backend/scripts/run_replay.py \
    --replay \
    --range 2024-01-01:2024-02-01 \
    --sport nba \
    --markets all \
    --run-name "wz_direction_relaxed_v1"

# E. Diff
python /app/backend/scripts/compare_replay_runs.py \
    --base   {vk2_combo_fix_v1_run_id} \
    --candidate {wz_direction_relaxed_v1_run_id}
```

Output of (E) is the **single number that tells us whether to keep the change**: ROI delta per tier, calibration delta, pick-volume delta, and a one-line verdict.

---

## 10. Recommended minimum window for meaningful conclusions

Empirical priors:

| tier | typical hit rate | typical odds | half-life of an ROI signal |
|---|---|---|---|
| safe_haven | ~70% | -300 to -180 | ~150 picks for ±2 pp 95% CI |
| front_lines | ~60% | -150 to +120 | ~300 picks |
| war_zone | ~45–55% | +100 to +400 | ~600 picks |
| longshots | ~25–35% | +300 to +800 | ~1,000 picks |

PropVision currently produces ~30 SH + 30 FL + 15 WZ qualified per slate post-stabilization. So:

- **Safe Haven:** 5 slates → 150 picks → meaningful ROI direction signal
- **Front Lines:** 10 slates → 300 picks → meaningful
- **War Zone:** 40 slates → 600 picks → meaningful
- **War Zone longshots / direction-fail experiments:** 60+ slates → 900–1,000 picks → meaningful

**Recommendation:** **30 game days minimum (~ 240 NBA games)** for the MVP. That gives WZ ~600 picks — enough to settle the “is War Zone profitable?” question with reasonable confidence. **Full season (180 days) is what answers the “is product ready for paying users?” question.**

---

## 11. What this design explicitly does NOT do

- **Does not change live gates.** Not a single threshold moves until a replay run says it should.
- **Does not change live scoring.** The scoring stack is the dependency, not the target.
- **Does not change live board.** Ingest writes only `replay_*` collections.
- **Does not overwrite forward-test data.** `forward_test_outcomes` stays exactly as it is.
- **Does not use current odds for past games.** Snapshot timestamps are strictly historical.
- **Does not use PrizePicks placeholder odds.** Replay reads The Odds API books only; PP utility runs in a separate code path that we don’t exercise here.
- **Does not use legacy vk_* code.** Replay always calls `compute_scoring_stack` (vk2 + the post-2026-04-25 stack).

Every replay output carries `dataset_lineage="historical_replay"`; every UI/endpoint that surfaces results must show that label and refuse to mix replay data with live forward-testing data.

---

## 12. Open questions for sign-off

1. **Snapshot ladder confirm:** are 6 windows (`t-24h, t-6h, t-3h, t-60m, t-30m, close`) acceptable for MVP, or do you insist on the full 8 (adds 12h + 90m at +33% credits)?
2. **Books:** I have proven DK + FD + BOL + WilliamHill historically. **MGM was not present** in the 2024-03-01 sample (we’ll grab whatever the API returns; treat MGM coverage as best-effort). **Pinnacle is not a US-region book** for The Odds API — we’ll need `regions=eu,us` to include it (+10 credits per market per event). Do you want Pinnacle in the MVP, knowing it doubles the per-event cost?
3. **Result source priority:** for NBA, prefer (a) BallDontLie historical, (b) `nba_master_hub_2026.player_game_logs`, or (c) both with cross-validation? (b) is likely already populated for recent dates only; (a) goes back further.
4. **Run-level credit cap:** I propose hardcoding **300,000 credits** as the orchestrator-level kill switch (≈ 1.5× MVP budget). OK?
5. **Scoring snapshot of record:** which window’s evaluation is the “canonical” one used in tier-performance scorecards? I propose **`t-90m`** because that matches when we currently materialize `cached_board` for live decisions; alternative is `close`.

Once these five are answered, Phase 0 (scaffolding) can be committed in a few hours. Phase 1 ingest is a one-night job. Phase 2–4 are the meat of the build.

---

## Appendix A — Why we’re separate from `historical_odds_full`

The existing backfill collection has a 3-window plan and is used by a different (now-frozen) audit process. Mixing the 8-window replay into it would:

1. Pollute its idempotency promise (same key, different snapshot windows).
2. Force the replay engine to filter on `snapshot_label IN ('t-24h', 't-6h', ...)`, leaking awareness of the other system into ours.

Cleaner: own collection, mirror schema, share the client. If at some future date the team wants to merge them, that’s a one-time copy job — not an architectural coupling we have to live with forever.

---

## Appendix B — Versioning fingerprint algorithm

```python
def compute_run_fingerprint(repo_root: Path) -> dict:
    return {
        "git_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo_root).decode().strip(),
        "git_dirty": bool(subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=repo_root).decode().strip()),
        "scoring_config_hash": _sha256_files([
            "backend/services/scoring/scoring_stack.py",
            "backend/services/scoring/adapters/nba_scoring.py",
            "backend/services/scoring/adapters/mlb_scoring.py",
            "backend/services/scoring/tp_engine.py",
            "backend/services/scoring/calibration.py",
            "backend/services/scoring/vision_v2.py",
        ]),
        "gate_config_hash": _sha256_files([
            "backend/services/scoring/gates/thresholds.py",
            "backend/services/scoring/gates/overrides.py",
            "backend/services/scoring/gates/engine.py",
        ]),
    }
```

Two runs are guaranteed comparable iff `scoring_config_hash` AND `gate_config_hash` differ in *exactly* the way the experiment is meant to test. The comparison tool surfaces the hash diff in its first paragraph so we never compare apples and oranges by accident.

---

**END OF DESIGN DOC.** Awaiting sign-off on §12 (open questions) before committing Phase 0 scaffolding.

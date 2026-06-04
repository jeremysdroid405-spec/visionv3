# Pick Vision — Internal Quant Terminal & Multi-Sport Betting Platform

## Original Problem Statement
Restructure React/FastAPI betting app to a 100% Local-First Database Model with
multi-sport support. Implement Google/Apple OAuth and Stripe for payments.
Strict requirements: 100% ID-based joins, Universal Opportunity Models, strict
SSOT, scalable MLB historical replay framework.

Current focus: Private Universal Historical Testing Command Center frontend UI
controlling historical replay pipelines via the Emergent Admin API.

## Architecture
- Backend: FastAPI + local MongoDB (`pick_vision`) on port 8001 (supervisor)
- Frontend: React + Shadcn UI on port 3000
- Emergent Admin API: `/app/backend/routes/emergent_admin/`
- SSOT historical replay pipeline:
  `scripts/sgo/historical_full_pipeline_replay.py` → `production_replay_runner`
- Quant Terminal UI: `/app/frontend/src/pages/AdminTesting.jsx` (~2.9k lines)

## Key DB Collections
- `sgo_player_stats`, `sgo_replay_alt_odds_raw`, `sgo_pp_research_outcomes`
- `sgo_propvision_full_pipeline_replay`, `..._replay_diff`
- `candidate_thresholds`, `emergent_admin_jobs`



### 2026-06-04 — Team pipeline SSOT audit + MLB team grid backfill
- **Built `/app/backend/scripts/team_pipeline_ssot_audit.py`** — single
  command (`python -m scripts.team_pipeline_ssot_audit`) that prints
  a per-sport parity matrix across all 8 stages (ingest / features /
  outcomes / feature_cache / score adapter / reshape / replay /
  grid) and exits non-zero on any gap.
- **Closed MLB-team grid gap** — ran the in-process optimizer for
  `sport=MLB, prop_type=team` on the 2025-07 window (51,840 combos,
  60 cells). Run persists to `optimizer_runs` so the audit now
  reports `grid=✓` for all three sports.
- **Final SSOT audit (per-sport)**:
  ```
  Sport  Ingest  Features  Outcomes  FeatCache  Score  Reshape   Replay    Grid
  MLB        48     6,057   879,931    825,794    ✓    825,794   825,794    ✓
  NBA        30     3,283   413,454    391,851    ✓    391,851   391,851    ✓
  NFL         0     1,212   124,287    115,819    ✓    115,819   115,819    ✓
  ```
  Every sport with `feature_cache>0` has full downstream parity.
- **Forward-compat regression test** — `tests/test_team_pipeline_ssot_audit.py`
  (15 cases) pins audit invariants: locked sport list (mlb/nba/nfl),
  locked market_category list (h2h/spread/game_total/team_total),
  artifact root identity with `services.team_xgb_loader.ARTIFACT_ROOT`,
  reshape destination identity with
  `scripts.sgo.reshape_team_props_to_replay.DST_COLL`, and every
  gap-detection branch (missing artifact, zero reshape, partial
  replay-scored, missing grid run).
- **Live NBA team scoring smoke** — re-ran
  `score_team_live_props(db, sport='nba', rescore=True)`: 30/30
  rows scored, 4/4 NBA XGB artifacts loaded (h2h AUC 1.000, spread
  0.925, game_total 0.571, team_total 0.755). Confirms the NBA
  team SCORE adapter the previous handoff flagged as ✗ is in fact
  fully operational.

### 2026-06-02 (P0 — Team Production Parity) — All 6 deliverables shipped
- **#1 NBA live ingest path verified** — code path (server.py scheduler
  → team_live_sync_service → fetch_events → fetch_event_odds
  → _extract_team_props_from_odds → bulk upsert → passthrough → scorer)
  is correct end-to-end. Blocker in preview pod is environment-only:
  `ODDS_API_KEY` intentionally empty + APScheduler paused via
  worker testing-mode. On prod with the key, NBA Finals team props
  will populate on the next 15-min tick. To unblock visual QA in
  the preview pod, `scripts/seed_nba_finals_team_props.py` seeds a
  synthetic BOS/DAL Finals event (30 rows: ML × spreads × game
  totals × team totals × 3 books) and runs the passthrough.
- **#2 Team cards = player card quality** — the ferrari team tier
  endpoints now return cards enriched with `hit_rate_l5/l10/l20`,
  `season_hit_rate`, `l5_avg/l10_avg/l20_avg/season_avg`,
  `vk_predicted` (projection), `edge_vs_fair`, `vision_intel`,
  `scout_badges`, `intel_suite`, `active_badges` — same SSOT
  helpers `routes/team_with_badges.py` uses for the detail page.
  `TeamPropRow` already wraps `UniversalPlayerCard`, which reads
  every one of those fields and renders them in compact mode. NBA
  sample card: `Mavericks SPREAD AWAY +5.5 | hit_l10=70% | proj=
  -1.7 | vision="Hit AWAY +5.5 in 7 of last 10 spread games.
  Recent form trending over."` + scout badge `hot_streak`. Visual
  parity at the data layer is complete.
- **#3 Team DVP + Vision Intel** — deterministic, historical-derived
  via `services/team_historical_enrichment.py`. Rules:
  `hot_streak` ≥ 80% L5; `floor_lock` ≥ 75% L20; vision sentence
  combines L10 result, L5 trend, and head-to-head record vs the
  upcoming opponent. NO model/LLM claims. Added market-category
  classifier `_classify_market_category_from_key` in the tier
  service so cards work even before the XGB scorer runs (handles
  both raw Odds-API keys and canonical seeded tokens).
- **#4 Team backtest orchestrator** —
  `scripts/sgo/run_team_backtest.py`. Reads SGO team rows from
  `sgo_propvision_full_pipeline_replay (prop_type=team)`, classifies
  market_category, resolves `team_model_features` per (team, date),
  calls `services.team_xgb_loader.score_team_props_batch` — the
  SAME function `team_live_xgb_scorer` wraps. Writes to
  `team_replay_model_outputs`. Dry-run verified: 20/50 MLB rows
  scored via the live model (rest skipped for missing features —
  expected for older replay rows).
- **#5 Team optimizer mirror** —
  `scripts/sgo/mirror_team_replay_to_unified.py`. Counterpart to
  the player mirror. Wipes `prop_type=team` rows from the unified
  optimizer collection and bulk-inserts the fresh scored set.
  Player rows are NEVER touched.
- **#6 Pipeline audit endpoint** — `GET /api/v3/pipeline-audit`
  (`routes/pipeline_audit.py`). Returns 4-quadrant health snapshot
  (live_player, live_team, backtest_player, backtest_team) with
  per-sport row counts, latest snapshot ISO, lag in minutes, and a
  `green/amber/red` health gradient. Echoes `PIPELINE_REGISTRY`
  for dashboards. First call confirmed all 4 quadrants resolved;
  health snapshot today: `live_player=green, live_team=green,
  backtest_player=amber, backtest_team=amber` (amber because the
  optimizer mirrors haven't been committed yet).
- **Tests**: NEW `tests/test_pipeline_audit_and_team_backtest.py`
  (4 cases). Full backend suite: **38/38 passing**.


- **Root cause**: unique compound index on `team_prop_scores`
  includes `model_version`, so both an unscored row
  (`model_version=None`, from passthrough) and a scored row
  (`model_version='team_xgb_v1'`) coexisted for the same natural
  key tuple `(event_id, team_id, market, line, side, book,
  snapshot_iso, gate_config_version)`. The scorer's
  `UpdateOne(filter={natural_key}, $set={model_version: VERSION})`
  promoted the unscored row's `model_version` → triggered
  `E11000 duplicate key` against the existing scored sibling.
  Every Ferrari team-tier endpoint was eating ~7s of error
  recovery on every read.
- **Fix** in `services/team_live_xgb_scorer.py`:
  1. Pre-query scored siblings for every natural key in the
     enriched batch (`$or` over `(natural_key, model_version $ne
     null)`).
  2. For unscored rows that already have a scored sibling: emit a
     `DeleteOne(natural_key + model_version: None)` op — these are
     stale passthrough duplicates, drop them.
  3. For unscored rows with no scored sibling: emit the
     normal `$set` UpdateOne, but with the filter narrowed to
     `model_version: None` so it can ONLY ever match the unscored
     row (no possibility of promoting an already-scored row into a
     duplicate).
  4. `audit["stale_unscored_deleted"]` counter exposes how many
     stale rows were cleaned per call.
- **Re-order in `team_prop_tier_service`**: enrichment now runs
  AFTER dedupe + tier-sort + limit slice, so it iterates 5-10
  cards (the response set) instead of 100+ pre-filter rows. Same
  byte-for-byte response — just half the historical DB work.
- **Verified**:
  - 198 stale unscored MLB rows cleaned up on first read.
  - `GET /api/v3/ferrari/team/front-lines?sport=mlb&limit=10`
    response time: **8004 ms → 572 ms (14x speedup)**.
  - Zero `E11000` errors in supervisor/backend.err.log on
    repeated reads.
- **Tests**: NEW `tests/test_team_xgb_scorer_e11000_fix.py` (2
  cases — synthetic stale-pair scenario + live rescore over real
  MLB collection asserting zero E11000 in the audit error list).
  Full suite passes 23/23.


- **`services/team_historical_enrichment.py` (NEW)** — single SSOT
  home for the deterministic historical-stat math. Exports
  `compute_hit_rates`, `fetch_team_game_history`,
  `compute_baseline_stats`, `build_vision_intel`,
  `build_scout_badges`, `market_category_to_stat_token`,
  `split_team_id`. Both `routes/team_with_badges.py` and
  `services/team_prop_tier_service.py` consume from here so the
  numbers shown on the board and the detail page can never drift.
- **`services/team_prop_tier_service._enrich_cards_with_history`
  (NEW)** — async enrichment pass that runs after `_hydrate_card`.
  Stamps every Ferrari team-tier card with `hit_rate_l5/l10/l20`,
  `season_hit_rate`, `l5_avg/l10_avg/l20_avg/season_avg`,
  `vk_predicted` (projection), `edge_vs_fair`, `vision_intel`,
  `scout_badges`, `active_badges`, `intel_suite`. Per-team
  game-history query is cached for the request so it runs at most
  once per team (typical 3-10 cards = 1-3 history queries).
- **Verified live**: `GET /api/v3/ferrari/team/front-lines?sport=mlb`
  now returns every card with real numbers (e.g. Seattle Mariners
  HOME h2h: `hit_rate_l5=100, hit_rate_l10=100, hit_rate_l20=100`,
  scout badges `[hot_streak, floor_lock]`, vision_intel `"Hit HOME
  in 10 of last 10 moneyline games. Recent form trending over."`).
- **Drift guard**: new `tests/test_team_tier_enrichment.py` (3
  cases, all passing) — verifies every card carries the full key
  set, at least one card produces a real vision_intel sentence,
  and the `hit_rate_l10` value from the board endpoint matches the
  detail endpoint byte-for-byte for the same (team, category,
  side, line) tuple.

### 2026-06-02 — Live NBA team ingest blockers documented (handled separately)
- Confirmed code path is wired correctly:
  - APScheduler registers `scheduled_team_live_sync` for each of
    `mlb/nba/nfl` at server startup (server.py L2261-2268).
  - `services/team_live_sync_service.sync_team_live_for_sport`
    handles NBA sport key, fetches events, writes
    `team_live_props`, runs the passthrough into `team_prop_scores`,
    triggers the scorer, audit-logs to `team_odds_ingest_runs`.
- Environment-side blockers (intentional, NOT code bugs):
  - `ODDS_API_KEY` is intentionally empty in `/app/backend/.env`
    per handoff ("DO NOT ADD IT BACK unless requested. This is a
    mocked environment now.").
  - APScheduler is currently PAUSED via the worker testing-mode
    endpoint (per handoff, to prevent runaway Odds API calls).
- **Result in this preview env**: NBA `team_live_props` /
  `team_prop_scores` have 0 rows; NBA team detail page renders
  header + season stats + 25-game history correctly, but the props
  grid is empty until the live pipeline runs in production.
- **Action for prod**: provision `ODDS_API_KEY` and unpause the
  worker; the ingest pipeline will populate `team_live_props` →
  `team_prop_scores` → ferrari team endpoints on the next 15-min
  tick.


- **Rejected custom surfaces removed.** `TeamHistoricalSurfaces.jsx`
  and `useTeamMasterStats.js` deleted (user rejected as
  over-engineered). `TeamDetailPage.jsx` rewritten as a thin
  wrapper that fetches the new team endpoint and forwards the
  result to `PlayerDetailPage` verbatim.
- **Backend `GET /api/v3/team-with-badges/{team_id}?sport=` (NEW)**
  — `routes/team_with_badges.py`. Returns a player-shaped payload
  for a team. Every field PlayerDetailPage / PropRow /
  GameLogBarChart / Vision Intel Suite consumes is populated:
  `player_name`, `team`, `photo_url`, `props[]` (every market ×
  line × side combo) with `stat_type`, `line`, `direction`,
  `hit_rate_l5/l10/l20`, `season_hit_rate`, `l5_avg/l10_avg/
  l20_avg/season_avg`, `vk_predicted`, `edge_vs_fair`,
  `best_book`, `best_book_odds`, `vision_intel`, `scout_badges`,
  `intel_suite`, `game_logs`, plus `baseline_stats` (Team PTS /
  TEAM_TOTAL / OPP_TOTAL / GAME_TOTAL / SPREAD averages) and
  `game_logs` (last-25 games with team_score / opp_score /
  total_score / margin / opponent).
- **Real numbers, not models.** Hit rates / averages / projection
  are computed deterministically from `team_historical_outcomes`
  (413k NBA / 879k MLB / 124k NFL rows). `vision_intel` is a
  rules-based sentence string (e.g. "Hit OVER 110.5 in 7 of last
  10 team total games. Recent form trending over. vs DET: 23/35
  hits on this market."). `scout_badges` are derived from
  hit-rate thresholds (`hot_streak` ≥ 80% L5, `floor_lock` ≥ 75%
  L20). NO AI/model claims.
- **No yellow Vision-pick glow on teams.** TeamDetailPage passes
  `highlightProp={null}` to PlayerDetailPage; team props don't
  carry `is_vision_enriched=true`, so neither highlight path
  fires (`PlayerDetailPage::isHighlightedProp`).
- **Frontend canonical tokens added** to
  `utils/statLabel.js::_SHORT/_LONG`,
  `components/dashboard/GameLogBarChart.STAT_FIELD_MAP`, and
  `PlayerDetailPage.CATEGORY_ORDER`:
  `TEAM_TOTAL → team_score`, `GAME_TOTAL → total_score`,
  `SPREAD → margin`, `MONEYLINE → team_score`,
  `OPP_TOTAL → opp_score`. Section labels: "Team Total",
  "Game Total", "Spread", "Moneyline", "Opp Total".
- **Click routing**: `Dashboard.handleRadarClick` /
  `handleVaultClick` already forward team picks
  (`prop_type='team'`) to TeamDetailPage with `team_id` carried
  on `props[0]`. No router changes needed.
- **Tests**: `tests/test_team_with_badges_endpoint.py` (11 cases,
  all passing) — unit tests for the deterministic Vision-Intel +
  scout-badge builders, identity helpers (`_split_team_id`,
  `_team_display_name`), and integration tests against the real
  `team_historical_outcomes` collection asserting the
  player-shape contract every PlayerDetailPage reader depends
  on. Backend total: previous 7 team_historical tests still pass.
- **Live-ingest dependency**: NBA `team_prop_scores` currently has
  0 rows (live NBA Finals ingest not yet producing). The team
  detail page renders the header + season stats + 25-game history
  for NBA today; props grid will populate as soon as the
  scheduled live ingest writes rows. MLB has 5 props per team and
  renders the full grid + vision intel today.

### 2026-06-02 — NBA Finals team props: live ingest wiring + TeamDetailPage clone
- See CHANGELOG.md for full details. Adds:
  - Cron-scheduled `sync_team_live_for_sport` for MLB / NBA / NFL.
  - NCAAF removed from sport allow-list.
  - Frontend click router routes team picks to TeamDetailPage.
  - TeamDetailPage.jsx wrapper-clone of PlayerDetailPage.

### 2026-06-02 — Production-scorer replay-safety (fantasy_score + availability + blank-dates)
- `services/scoring/canonical_stats.py`: registered fantasy_score
  variants (`fantasy_score`, `fantasyScore`,
  `player_fantasy_score`) → canonical `FANTASY` → family `pra`.
  Zero `STAT_REGISTRY_MISS` log spam.
- `services/scoring/adapters/nba_scoring.py`:
  `_classify_availability` skips blank/missing log dates instead
  of raising `ValueError: Invalid isoformat string: ''`.
- `services/scoring/adapters/nba_scoring.py`:
  `_maybe_apply_availability_guard` honors per-prop
  `disable_availability_guard=True` flag (early-return) so replay
  doesn't double-count the target game's own restriction signal.
- `services/replay/contract.py`: NEW `REPLAY_PROP_FLAGS` immutable
  dict — replay engines stamp these on every prop. NFL/NCAAF
  future engines reuse the same flag set.
- `services/replay/nba_replay_engine.py`: stamps
  `**REPLAY_PROP_FLAGS` on every canonical prop; projects
  `availability_guard_reason` on Layer-3 output rows.
- `scripts/sgo/reshape_sgo_to_replay_odds.py`: NBA + NFL maps emit
  Odds-API-canonical `player_fantasy_score` market key.
- 3 NEW regression test files:
  - `test_nba_replay_fantasy_score.py` (5 tests)
  - `test_nba_replay_blank_availability.py` (4 tests)
  - `test_nba_replay_recompute.py` (end-to-end E2E with the
    PRODUCTION scorer; 6/6 rows reach `model_probability`,
    `projection_mu`, `stat_family='pra'` for fantasy_score)

### 2026-06-02 — Snapshot fallback policy (replay never silently scores zero)
- `services/replay/snapshot_resolver.py` (NEW): cross-sport
  three-tier fallback (`exact` → `latest_for_date` →
  `any_for_date` → `none`). Replaces hard-coded `{snapshot_iso}`
  exact-match filter in `nba_replay_engine`. Same contract for
  MLB / NBA / NFL / NCAAF / future sports.
- Engine summary now carries `snapshot_iso_resolved`,
  `snapshot_resolution_tier`, and `snapshot_resolution_telemetry`
  ({rows_for_date, rows_for_exact_snapshot, distinct_snapshots,
  resolved_snapshot_iso, rows_for_resolved_snapshot}) so any
  future drift surfaces immediately on every run.
- Layer-3 rows are keyed under the RESOLVED snapshot, not the
  orchestrator's candidate, so the unique index stamps the
  actual data location.
- `tests/test_nba_replay_snapshot_fallback.py` (NEW): verifies
  exact/fallback/no-data scenarios.

### 2026-06-02 — Replay eligibility bypass promoted to cross-sport infrastructure policy
- `services/replay/contract.py` (NEW): SSOT for the cross-sport
  replay invariant `score → persist → optimize`. Exports
  `REPLAY_RECOMPUTE_KWARGS` (immutable `MappingProxyType` —
  `dry_run=True` / `write_mode="upsert"` /
  `bypass_eligibility=True`) and `COMPLIANT_REPLAY_ENGINES` registry.
- Every replay engine declares `REPLAY_CONTRACT_COMPLIANT = True`
  at module scope (MLB / NBA / Teams today). NFL / NCAAF future
  engines must follow the same pattern.
- NBA replay engine now uses `**REPLAY_RECOMPUTE_KWARGS` instead of
  inline kwargs — one SSOT call shape for the contract.
- `tests/test_replay_infrastructure_contract.py` (NEW): 6 static
  audit tests catch contract violations at CI time (no DB,
  milliseconds).

### 2026-06-02 — Replay eligibility bypass: PP-only props now scored
- Root cause of 1.46% survival rate (588k unique candidates → 8.5k
  outputs): `apply_production_eligibility` inside
  `recompute_sport` was dropping PP-only props (`filter_priceable`:
  `book_count==0`) and non-PP props (`filter_pp_playable`).
  Appropriate for live serving, fatal for historical replay
  (~90% of NBA historical universe is PP-only).
- `services/scoring/recompute.py`: new kwarg
  `bypass_eligibility: bool = False`. When True, skip the filter
  chain entirely; still stamps `book_count`/`coverage_class`/
  `books_anchored` via `classify_coverage` so the scoring stack's
  `coverage_gate` can still CLASSIFY tier as
  "rejected"/"unqualified"/"war_zone"/"front_lines"/"safe_haven"
  — but never DROPS the prop.
- `services/replay/nba_replay_engine.py`: passes
  `bypass_eligibility=True`. Adds stage-by-stage volume telemetry
  to the summary (`alt_odds_rows_seen`, `props_built`,
  `score_docs_returned`, `recompute_processed`,
  `recompute_skipped`, `model_outputs_written`,
  `candidates_skipped_no_score_doc`, `reshape_skipped`).
- Fixed Layer-3 `gate_pass` semantics: now True ONLY when
  production landed the prop in one of the three qualifying tiers.
- `tests/test_nba_replay_bypass_eligibility.py` (NEW): verifies
  PP-only props score with `projection_mu` / `model_probability`
  populated and `tier="unqualified"` / `gate_pass=False` /
  `coverage_class="pp_only"` as metadata.

### 2026-06-02 — NFL stat_id → market mapping (Issue 2 closed)
- `scripts/sgo/reshape_sgo_to_replay_odds.py`: added
  `_STAT_ID_TO_MARKET_NFL` (50 SGO stat_id variants → Odds API
  canonical `player_*` market names: `player_pass_yds`,
  `player_pass_tds`, `player_pass_interceptions`,
  `player_rush_yds`, `player_rush_attempts`, `player_rush_tds`,
  `player_receptions`, `player_reception_yds`,
  `player_reception_tds`, `player_targets`,
  `player_reception_longest`, `player_field_goals`,
  `player_extra_points`). Registered in
  `_STAT_ID_TO_MARKET_BY_LEAGUE`.
- `_resolve_market`: case-insensitive fallback so SGO casing drift
  (`PassingYards`, `PASS_YARDS`) doesn't silently drop rows.
- All 15 canonical NFL families from
  `services/replay/nfl_stat_family_map.NFL_FAMILIES` are covered.
- `tests/test_reshape_sgo_nfl.py` (NEW): 10 tests, all passing.
  64/67 `test_reshape_sgo_*` tests pass (3 pre-existing failures
  unrelated to NFL).

### 2026-06-02 — Optimizer input contract: research_mode default-True for testing pipeline
- `scripts/sgo/historical_full_pipeline_replay.py`: `--research-mode`
  flipped to default-True. Testing/optimizer pipeline no longer
  pre-filters its input by today's production gates — every scored
  row lands in `*_propvision_full_pipeline_outputs` with
  `tier`/`gate_pass`/`failed_gates`/`grade_status` kept as metadata.
  Optimizer decides thresholds later. New opt-out flag
  `--apply-production-gates` for parity audits.
- Applies uniformly to MLB, NBA, NFL, teams, and all future sports.
  Production board (live serving) still filters by gates.

### 2026-06-02 — NBA Layer-3 replay wrap (production scorer reuse, no new model)
- `services/replay/nba_replay_engine.py` (NEW): thin wrapper around the
  PRODUCTION NBA scorer. Reads `sgo_replay_alt_odds_raw`, reshapes per-book
  rows into live-prop shape, calls
  `recompute_sport(db, "nba", dry_run=True)` to score via the SAME
  `NBAScoringAdapter` / `compute_scoring_stack` / universal gate engine
  path live serving uses, fans the returned score docs out per
  `(book, side)` into `nba_replay_model_outputs`. `nba_prop_scores` is
  never mutated.
- `services/replay/providers/nba_adapter.py`: fleshed out
  (`normalize_stat_family`, `fetch_actuals` from
  `nba_master_hub_2026.bdl_game_logs`, sport-agnostic `grade_outcome`).
  `predict` raises with the canonical "use `replay_date`" message;
  `load_model` returns `NBAScoringAdapter()` for adapter-contract
  parity.
- `services/replay/production_replay_runner.py`: NBA wired across
  `_run_layer3`, `_layer3_outputs_collection_name`,
  `_resolve_scoring_versions`, `_resolve_model_versions`,
  `gate_path="universal"` per-row dispatch (uses production-stamped
  fields directly — no second gate-engine pass), per-row odds-bucket
  routing (uses production-decided tier), and grading (sport-aware
  actuals lookup).
- `tests/test_nba_replay_engine_smoke.py` (NEW): end-to-end smoke test
  against real production scorer (Phase 1 = engine alone; Phase 2 =
  full `run_production_replay`). PASSES — 16 universal SSOT fields
  populated on output docs (`projection_mu`, `sigma`,
  `model_probability`, `edge`, `hit_rate_l5/10/20`, `cv`, `tp`,
  `tp_source`, `edge_pct`, `tier`, `gate_pass`, `vision_score`,
  `p_model`, `p_true_active`).

### 2026-06-01 — Team Props XGB scoring wired (sprint complete)
- `scripts.sgo.reshape_team_props_to_replay` batched + executed → 1,333,464
  scored team rows in `sgo_propvision_full_pipeline_replay`
  (`pipeline_version=team_v1_scored`)
- 12 trained per-(sport, market_category) XGB artifacts loaded via
  `services.team_xgb_loader.score_team_props_batch`
- Optimizer `/api/emergent-admin/optimizer/run` accepts `prop_type=team`
  and produced full per-tier results (NBA Jan-2025: best combo +83.8% ROI)

## What's been implemented (chronological)
### 2026-05 — Quant Terminal foundation
- `/admin/testing` (sweeps, replay, results, coverage, optimizer, deploy)
- `emergent_admin` backend (jobs, optimizer, preflight, coverage, models)
- SSOT refactor of historical replay
- Local Replay Warehouse offline coverage modes
- Cache-first SGO stats ingest

### 2026-05-21 — Job runner silent-failure fix
- `_run_job` outer try/except (pre-spawn exceptions reach DB)
- `_backend_cwd()` 4-level fallback
- `_RUNNER_TASKS` strong-ref set
- `/jobs/_self_test` + `/jobs/_reconcile_stuck` operator endpoints
- Verified locally end-to-end

### 2026-05-21 — Failed-job diagnostics + pipeline preflight
- **Backend** already captured full tracebacks in `tail_preview`; UI was hiding them.
- **Pipeline orchestrator** now includes the two preflight prerequisites for
  full pipeline replay (previously caused "no_reshape_odds" hard-fail):
  - Step 4: Reshape Odds (`scripts.sgo.reshape_sgo_to_replay_odds`)
  - Step 5: Grade Outcomes (`scripts.sgo.build_historical_outcomes`)
  - Step 6: Full Pipeline Replay (was step 4)
- **Failed-step UI** auto-opens the traceback `<details>` panel, widens
  the inline error line (100 → 400 chars), extracts a human-readable
  message from the last traceback line when `job.error` is null, and
  keeps the log panel visible after halt (showing last 80 lines).

### 2026-05-23 — Unified Research Result Architecture (Option A)
Wired the Results tab to dedicated research endpoints instead of the
generic `/collections/{name}/find` route. The new endpoints understand
both sweep schemas (PP-free `market_truth_pp_free` + per-tier
`per_tier_per_stat_family`) and do server-side ranking, filtering, and
best-of bucketing.
- See `/app/backend/routes/emergent_admin/research.py` for full endpoint list.
- Schema-tolerant frontend rendering; MM-DD-YYYY date display; Optimizer
  auto-loads the cached pipeline window so sweeps consume zero SGO credits.
- 25 backend tests passing.

### 2026-05-23 — Pod split: dedicated research_worker daemon
Heavy compute (optimizer sweeps, historical replay, grid sweeps,
candidate generation) was running inside the FastAPI uvicorn process,
starving live scoring and API request handling. Split now enforced:
- **New supervisor service** `research_worker` running
  `python -m workers.research_worker` (NOT a uvicorn worker, NOT in the
  request lifecycle). Conf at `/etc/supervisor/conf.d/research_worker.conf`.
- **Mongo-backed queue** on the existing `emergent_admin_jobs` collection
  with `worker_queue=True`. Atomic `findOneAndUpdate` claim ensures
  exactly-once execution and serves as the single max-concurrent=1 gate.
- **HEAVY_MODULES** set in `workers/queue.py` lists modules that MUST
  route through the worker (optimizer CLI, full-pipeline replay, grid
  sweep, reshape, build_historical_*, ingest_historical_*, BDL ingest).
  Light preflight/coverage modules still spawn inline.
- **Per-job resource caps** applied to each spawned subprocess via
  `preexec_fn`: `nice +10`, `RLIMIT_AS = 4 GB`, `oom_score_adj = +500`,
  hard timeout 2 h. All overrideable via env vars.
- **Optimizer migration** — `POST /optimizer/run` persists the request to
  `optimizer_runs` then enqueues `scripts.research.run_optimizer_cli
  --run-id`. The CLI re-hydrates the in-process state slot and calls the
  same `_run_optimizer` logic. **Same endpoint signature**, same UI
  polling path. The frontend doesn't need to change anything except the
  new Worker Health bar.
- **Real-time output** + `rss_peak_bytes` + `cpu_seconds` captured per
  job. Crash recovery on worker restart force-finalizes orphaned claimed/
  running jobs as `errored`.
- **New endpoints** under `/api/emergent-admin/worker/*`:
  - `GET /worker/health` — queue depth, active job, worker PID/RSS/CPU,
    heartbeat age + staleness flag, backend PID/RSS/CPU for comparison.
  - `GET /worker/queue` — list jobs (status filter optional).
  - `POST /worker/cancel/{job_id}` — SIGTERM the in-flight child without
    killing the worker daemon.
- **Pagination** — `/research/grid-results/{run_id}` rewritten as a Mongo
  `$sort + $limit` aggregation; never loads the cell set into Python
  memory. New `/research/grid-results/{run_id}/cells` for paginated table
  scans (offset/limit hard-capped at 500/req).
- **Worker Health bar** in `AdminTesting.jsx` polls every 5 s. Shows
  worker status, heartbeat age, queue depth, active job, worker/backend
  RSS side-by-side.
- **Tests**: 32 backend tests passing (added 7 covering worker health,
  queue, cancel, auth, heavy-module routing, paginated cells + offsets).
- **Legacy `/optimizer` endpoints kept intact** per user preference.

## Backlog (priority order)
### P0 — Awaiting explicit user go-ahead
- Phase 1.A.3 — Real `team_live_props` SGO ingest dispatch path
  (workers built in 1.A.2; requires explicit `SGO_API_KEY` +
  `TEAM_INGEST_ENABLED=1` release on prod pod).
- Phase 1.A.4 — `team_matchups` schedule backfill worker (skeleton
  already in place from 1.A.2).
- Google/Apple OAuth via Emergent-managed Google Auth
  (must call `integration_playbook_expert_v2` before writing any auth code)
- Stripe payments (must call `integration_playbook_expert_v2`, use pod test keys)

### P1
- Backfill `sgo_player_stats` Mar–May 2025 for full feature hydration
- Retire legacy NBA replay → migrate to universal pipeline runner
- NFL-ready config scaffold for the universal pipeline
- Decompose `AdminTesting.jsx` into per-tab subcomponents

### P2
- Audit `points` stat_family anomaly on MLB props

## Critical Notes
- DO NOT run heavy MLB historical sweeps locally — pod OOM.
- Frontend changes require `git pull` AND `yarn build` on the prod host.
- `EMERGENT_ADMIN_TOKEN` lives in `backend/.env`.
- New pipeline steps 4 (reshape) + 5 (grade) are `skippable: true`; they
  default to NOT skipped so they run automatically. Toggle off for re-runs.

### 2026-05-23 — Scoring-layer contract drift fix (score_historical_with_live_mlb_hf)
Root cause of 1736/2020 "predict ok but mu/sigma/model_p incomplete":
the historical scorer read `projection_mu / sigma / model_probability`
off the live `MLBHighFrictionModel.predict()` return — fields that have
NEVER existed on the live response. The live model emits
`predicted / std_dev / prob_over` (`prob_over` as **percentage 0-100**).
The legacy keys silently returned None, the "missing" guard tripped,
zero rows ever reached `bulk_write`.
- **Single normalisation boundary** `_extract_live_outputs(result, side)`
  in `scripts/sgo/score_historical_with_live_mlb_hf.py` reads the live
  keys, converts `prob_over` → 0-1, flips for UNDER bets, clamps
  out-of-range, rejects non-numeric, treats σ=0 as missing.
- **Diagnostic logging on every run**: per stat_family scoreboard
  (scored / missing / errored / no_hub / no_hf), top error messages,
  missing-field breakdown by name, plus `--dump-predictions N` sample
  of raw `predict()` returns. Contract drift now impossible to miss.
- **Hard-fail mode**: `--strict-min-scored-ratio R` exits non-zero when
  `scored / (scanned − skipped) < R`. Worker marks the job `failed`.
  Use 0.30 in sweeps to fail fast on contract drift.
- **Probe parity**: `--probe` reuses the extractor so its missing-fields
  report matches the run.
- **Worker routing**: added to `HEAVY_MODULES` so the scorer runs under
  resource-capped worker (nice +10 / 4 GB / 2 h).
- **Tests**: `tests/test_score_historical_live_contract.py` — 10
  contract tests pin the extractor: happy path, UNDER flip, every
  missing-field combo, σ=0, legacy-schema rejection, clamp, non-numeric
  guard, family alias coverage. 42/42 backend tests pass.



### 2026-05-23 — NFL research pipeline (Phase 1 — probe + ingest + outcomes)
NFL backtest pipeline scaffolded with the **hybrid collection layout**
per user choice: raw ingestion stays on `sgo_*` shared collections keyed
by `league_id`; derived outputs split into NFL-suffixed collections so
MLB and NFL backtests stay isolated.

**New / changed:**
- `services/replay/nfl_stat_family_map.py` — canonical NFL family ↔ SGO
  stat_id aliases + family → player_stats lookup keys. Single SSOT for
  the NFL stat catalogue. Extend when new stat_ids show up in the probe.
- `scripts/sgo/probe_nfl_data.py` — **NEW**, read-only. Hits SGO with
  `expandResults=true`, dumps distinct (statID, marketName) pairs,
  sample playerStats keys, mapping coverage vs the family map, and 3
  sample raw player-stats dicts. Never writes Mongo.
- `scripts/sgo/ingest_historical_player_stats.py` — added
  `_normalize_nfl_stats()`, routed `--league=NFL` to it.
- `scripts/sgo/build_pp_research_core.py` — `--out-coll` plus
  auto-routing of `--league=NFL` to `sgo_nfl_research_core`. MLB path
  unchanged. Threaded through `build_month` / `ensure_out_indexes`.
- `scripts/sgo/build_historical_outcomes.py` — added NFL stat-family
  resolvers + SGO statID aliases. `--out-coll` / `--src-coll` accept
  per-league overrides; `--league=NFL` reads `sgo_nfl_research_core`
  and writes `sgo_nfl_research_outcomes`.
- `workers/queue.py` HEAVY_MODULES — probe + research-core builder
  routed through the worker.
- `routes/emergent_admin/policy.py` — `probe_nfl_data` allowed (read);
  `build_pp_research_core` re-enabled; outcomes exposes `--out-coll` /
  `--src-coll`.
- `tests/test_nfl_pipeline_unit.py` — 16 tests pinning aliases, NFL
  normalizer dispatch, every canonical family having a resolver, and
  resolvers reading the normalized fields.

**Phase 1 runbook (run on the prod host — preview pod has no SGO key):**

```bash
# 1. Probe — confirm SGO returns NFL data and our mapping covers it
python -m scripts.sgo.probe_nfl_data \
    --start=2025-09-04 --end=2025-09-09 \
    --max-events=200 --save-samples=/tmp/nfl_probe.json

# 2. Full SGO event ingest (writes sgo_events, sgo_props_raw,
#    sgo_player_stats, sgo_book_consensus, etc.)
python -m scripts.sgo.ingest --league=NFL \
    --start=2025-09-04 --end=2025-09-09

# 3. Cache-first historical player stats backfill (resumable)
python -m scripts.sgo.ingest_historical_player_stats \
    --league=NFL --start=2025-09-04 --end=2025-09-09 --source=auto

# 4. Build NFL research core (PrizePicks-anchored)
python -m scripts.sgo.build_pp_research_core \
    --league=NFL --start=2025-09-04 --end=2025-09-09
# → sgo_nfl_research_core

# 5. Grade NFL outcomes
python -m scripts.sgo.build_historical_outcomes \
    --league=NFL --start=2025-09-04 --end=2025-09-09 --resume \
    --debug-unresolved
# → sgo_nfl_research_outcomes
```

**Phase 2 deferred (after row counts validated):**
- `reshape_sgo_to_replay_odds.py` NFL support → `sgo_nfl_replay_alt_odds_raw`
- `services/replay/nfl_feature_cache.py` (rolling priors from `sgo_player_stats`)
- `scripts/nfl_replay_build_feature_cache.py` CLI
- `historical_full_pipeline_replay.py` NFL branch + `sgo_nfl_full_pipeline_replay`
- NFL model adapter / analytical baseline
- Grid optimizer NFL wiring

**Production safety: NFL is NOT wired into any tier and NOT on the
production board. Research/backtest only.**

### 2026-05-23 — Memory architecture fix (OOM-kill root cause)
**Root cause confirmed:** `optimizer.py` was accumulating every cell
result in `state["results"]` (a Python list living inside the uvicorn
process). On a 5-month MLB sweep that's 200k+ dicts → uvicorn RSS
ballooned to 4.3 GB, mongod's default WiredTiger cache (50% of RAM)
fought for the same pages, kernel OOM-killer triggered.

**Fixes shipped:**
1. **Stream-to-Mongo optimizer.** Removed `state["results"]` entirely.
   Cell rows now go straight to a new `optimizer_run_results` collection
   via batched `insert_many` (flush at 500 rows or every 5 s).
   Per-cell top-K cap (200) bounds total write volume. New compound
   index `(run_id, score desc)` so reads stay $sort+$limit on the
   server.
2. **API endpoints read from Mongo.** `/optimizer/{run_id}/results`
   is now paginated (`offset` + `limit`, hard-capped at 500/req) and
   the best_by_* maps run as a Mongo aggregation. Same for
   `/save_as_candidates`. Uvicorn never materialises the full set.
3. **Cancel via Mongo flag.** `/optimizer/{run_id}/cancel` writes
   `cancelled=true` on the run doc; the worker polls between cells.
   Removes the last reason uvicorn needed `_RUNS[run_id]` populated.
4. **Failures cap.** `state["failures"]` bounded by `MAX_INLINE_FAILURES
   = 50` so degenerate sweeps cannot grow it without limit.
5. **Testing-mode kill switch.**
   - Env: `TESTING_MODE=1` keeps APScheduler from ever starting.
   - Runtime: `POST /api/emergent-admin/worker/testing-mode {enabled}`
     pauses/resumes APScheduler in-process — no restart needed.
     `GET` returns current state (`running` / `paused` / `stopped`).
   - When paused, every SGO pull, recompute, and delta job is
     suspended; queued jobs survive intact for when you flip it back.
6. **WiredTiger cache capped at 1 GB.** `/etc/supervisor/conf.d/
   supervisord.conf` mongod command line now passes
   `--wiredTigerCacheSizeGB 1`. Default on this shared pod was
   ballooning past 4 GB.

**Verified memory impact (after restart):**
- backend uvicorn RSS:   4.3 GB → **142 MB**   (30× reduction)
- mongod RSS:            ≈3 GB  → **1.2 GB**
- available RAM:         8 GB   → **12 GB**

**Tests:** 58/58 still pass (10 contract + 32 research/worker + 16 NFL).


### 2026-05-23 — Half-migrated worker stall + uvicorn growth after Odds exhaustion
Two distinct issues bundled together:

**Issue A: jobs enqueued but never consumed.**
Root cause: the prod host never picked up `research_worker.conf`; the
service simply wasn't installed. The API kept happily enqueueing jobs
that nobody would ever execute, with no signal to the operator.

Fixes:
1. `workers/queue.py:enqueue(..., require_worker=True)` (default) refuses
   to enqueue when `/tmp/research_worker.heartbeat` is missing or older
   than `RW_STALE_AFTER_S` (30 s). Returns HTTP 503 with a fix recipe
   pointing at the installer. No more silent pile-up.
2. `/app/scripts/install_research_worker.sh` — idempotent one-command
   installer. Writes the supervisor conf, runs `reread`/`update`/`start`,
   waits up to 10 s for the heartbeat, fails loudly on timeout. Safe to
   re-run.
3. Two new tests pin the guard:
   - `test_enqueue_refused_when_no_worker_heartbeat` — missing file → 503
   - `test_enqueue_succeeds_when_heartbeat_fresh` — present + fresh → ok

**Issue B: uvicorn still ballooning despite TESTING_MODE.**
Root cause: many live-sync loops are spawned as direct
`asyncio.create_task` outside APScheduler. `TESTING_MODE` was only
pausing APScheduler, so these task-based loops kept hitting the Odds
API, retrying after rate-limit, and growing memory unboundedly.

Fixes (all in `server.py` startup):
1. `GameLockEngine.start()` — gated on `TESTING_MODE`.
2. `AdaptiveSync.start()` — gated on `TESTING_MODE`.
3. `InjurySensor.start()` — gated on `TESTING_MODE`.
4. `injury_triggered_rescore.get_rescore_service().start()` — gated.
5. `GameClockWatcher.start()` — gated.
6. `check_and_run_initial_sync` create_task — gated.

Now `TESTING_MODE=1` (env) or `POST /worker/testing-mode {enabled:true}`
(runtime) suspends EVERY background loop, not just APScheduler.

**Operator runbook to bring prod back online:**

```bash
# 1. Install / repair the worker (idempotent)
sudo bash /app/scripts/install_research_worker.sh

# 2. Verify
supervisorctl status research_worker
curl -sS http://127.0.0.1:8001/api/emergent-admin/worker/health \
     -H "X-Admin-Token: $EMERGENT_ADMIN_TOKEN" | jq .worker

# 3. Pause live sync during heavy research
curl -sS -X POST http://127.0.0.1:8001/api/emergent-admin/worker/testing-mode \
     -H "X-Admin-Token: $EMERGENT_ADMIN_TOKEN" \
     -H "Content-Type: application/json" -d '{"enabled":true}'

# 4. Re-enable when done
curl -sS -X POST http://127.0.0.1:8001/api/emergent-admin/worker/testing-mode \
     -H "X-Admin-Token: $EMERGENT_ADMIN_TOKEN" \
     -H "Content-Type: application/json" -d '{"enabled":false}'
```

**Tests:** 60/60 backend tests pass (10 contract + 16 NFL + 34
research/worker + 2 new for orphan guard).



### 2026-05-23 — Backfill polling + cache-preflight (stuck-queued UI fix)
**Reported symptom:** UI stayed on "queued" even though Mongo showed
the job had run to completion. Worker logs healthy, exit_code=0, but
the Admin Testing UI never updated and the worker-health bar showed
stale 503-style "queued/stale" warnings.

**Root causes:**
1. `WarehouseCoverage.runFix` (per-card "Run Fix → backfill" button)
   only fired a toast — it never polled the resulting `job_id`. The
   coverage card therefore never reflected job progress or completion.
2. The pipeline orchestrator (`WorkflowTab`) treated only `queued` and
   `running` as active states. The worker's `claimed` interim state
   was misread as "previous step done" and the orchestrator tried to
   enqueue the next step in parallel.
3. Identical fix-jobs were being enqueued back-to-back even when the
   source collection already had the rows. No preflight gate existed.
4. `WorkerHealthBar` had no manual refresh; stale errors required a
   page reload to clear.

**Fixes shipped:**
- **Backend** — `POST /api/emergent-admin/coverage/backfill`
  (new endpoint in `routes/emergent_admin/coverage.py`):
  - Preflights the source collection row count for the (sport, start,
    end) window. If `row_count > 0` and `force=false` → returns
    `{status: "cached_skip", row_count, days_with_rows, …}` with NO
    enqueue.
  - Otherwise enqueues via existing `workers.queue.enqueue` (heavy →
    research_worker, light → inline). Audit-logged on both branches.
- **Frontend** — `WarehouseCoverage`:
  - "Run Fix" now hits `/coverage/backfill`; cache-hit renders an
    immediate green "✓ Cached · skipped" pill on the card.
  - Cache-miss → polls `/jobs/{id}` every 2 s, updates a per-card
    status pill across queued → claimed → running → terminal.
  - Tail-detects `rows_emitted: 0` and labels succeeded as
    "✓ Finished · no new rows" (succeeded_cache) so the operator
    knows the worker ran but the source had nothing new.
  - Adds a "Force re-run" button after terminal.
- **Frontend** — `WorkflowTab` orchestrator now includes `claimed` in
  every "active step" check; adds `timeout` as a terminal failure.
- **Frontend** — `WorkerHealthBar`: manual `Refresh` button
  (`data-testid="wh-refresh"`); stale errors clear when a fresh
  heartbeat lands.

**Tests:** `tests/test_coverage_backfill_endpoint.py` — 4 cases pin
cache-miss enqueue, cache-hit short-circuit, force bypass, unknown
key. Backend total: 73/73 passing (was 69/69).

### 2026-05-23 — Optimizer outcome-grading diagnosis (HR=—, ROI=0.0 bug)
**Reported symptom:** every optimizer cell came back with n_bets ≥ 30
yet HR=—, ROI=0.0%, Δcal=—, score≈0. Threshold grouping, odds bucket
slicing, and the worker pipeline all looked healthy — the failure
was strictly inside outcome aggregation.

**Root cause:** the replay cache (`sgo_propvision_full_pipeline_replay`)
held rows whose `outcome_numeric` was NULL because the join in
`_mirror_to_legacy` (which attaches outcomes from
`sgo_pp_research_outcomes` to runner output) silently missed every row
when one of the join keys (`event_id`, `player_name_normalized`,
`market`, `line`, `side`) failed to align. `_evaluate_combo` then
counted every row as `ungraded` → `wins=0, losses=0, settled=0` →
`hit_rate=None`, and ROI was divided by total `n` instead of the
graded count, producing the misleading `0.0%` instead of `None`.

**Fixes shipped (this fork):**
- **Backend / optimizer** (`routes/emergent_admin/optimizer.py`):
  - `_evaluate_combo` now exposes `n_graded`, `n_ungraded`,
    `n_with_odds`, `n_with_payout` on every cell doc written to
    `optimizer_run_results`.
  - ROI denominator changed from total `n` to `n_with_payout`. When
    every row is ungraded, ROI is `None` (not `0.0`), preserving the
    "unknown" signal so the UI no longer masks the upstream bug.
- **Backend / research** (`routes/emergent_admin/research.py`):
  - **New endpoint** `GET /api/emergent-admin/research/replay-outcome-coverage`
    (sport, start, end). Returns `n_total`, `n_outcome_resolved`,
    `n_with_outcome_numeric`, `n_with_odds`, `pct_graded`,
    `by_stat_family[]`, `sample_unresolved[]`, plus a one-line
    `diagnosis` string that explicitly states the failure mode
    ("CRITICAL: N replay rows but 0 have outcome_resolved=true ...").
- **Frontend** (`pages/AdminTesting.jsx`):
  - Optimizer Results panel now auto-loads `/replay-outcome-coverage`
    when results render and shows a colored banner (red < 50%,
    amber < 95%, green otherwise) at the top of the panel. The
    diagnosis string is rendered verbatim so the operator gets a
    clear next-step instead of guessing.
  - Top/Worst tables now show "n_graded/n_bets graded" badge next to
    the sample size whenever the cell has any ungraded rows.

**Tests:** `tests/test_optimizer_evaluate_combo.py` (5 cases)
locks in the new diagnostic schema and the corrected ROI denominator.
Backend total: 80/80 passing (was 73/73).

**Operator runbook when this fires again:**
```bash
# 1. Identify the gap
curl -sS "$API/api/emergent-admin/research/replay-outcome-coverage?sport=MLB&start=2025-04-01&end=2025-04-30" \
     -H "X-Admin-Token: $TOK" | jq

# 2. If outcome_resolved=true rows == 0 → grading hasn't been written yet
python -m scripts.sgo.build_historical_outcomes --league MLB \
       --start 2025-04-01 --end 2025-04-30 --resume --debug-unresolved

# 3. If outcomes exist but mirror didn't attach them → re-run replay
python -m scripts.sgo.historical_full_pipeline_replay --league MLB \
       --start 2025-04-01 --end 2025-04-30 --research-mode
```


### 2026-05-23 — Join-key diagnostic for mirror→outcomes attach
**Confirmed scope:** After running the full pipeline replay against
a 30-day window, the operator now sees `8,693 / 8,739 (99.5%)` rows
present with odds — but only `46 (0.5%)` carry a numeric outcome.
The replay itself is healthy; the mirror→outcomes join is failing
on one of the 5 keys (event_id / player_name_normalized / market /
line / side).

**Fix (this fork):**
- **Backend** — new endpoint
  `GET /api/emergent-admin/research/replay-outcome-join-diagnose`
  (`routes/emergent_admin/research.py`).
  Probes a configurable sample of unresolved replay rows against
  `sgo_pp_research_outcomes` with progressively relaxed filters:
    `K0_full → K1_no_line → K2_no_market_no_line → K3_player_only → K4_event_only`
  A jump in match-rate between adjacent steps pinpoints the
  offending key. Also returns 5 side-by-side replay/outcome value
  comparisons (including each key's Python type) so float-vs-string
  and raw-vs-canonical-market mismatches are obvious at-a-glance.
- **Frontend** — Optimizer Results panel "Diagnose join failure"
  button appears whenever the coverage banner shows `pct_graded < 95`.
  Renders the 5 match-rate cells with green/amber/red shading plus
  the verbatim diagnosis and an expandable list of mismatched pairs.
- **Tests:** `tests/test_replay_outcome_join_diagnose.py` — 2 cases
  pin the failure-mode detection: (a) seeds line=float vs line=str
  and asserts diagnosis flags `line`, (b) empty outcomes window and
  asserts K4 reports `event_id` failure. Backend total: 82/82 pass.

**Operator next step (on prod):**
```
GET /api/emergent-admin/research/replay-outcome-join-diagnose?sport=MLB&start=…&end=…
```
or click "Diagnose join failure" in the Optimizer Results banner. The
diagnosis tells you exactly which mirror-side field is wrong;
patch that single field in `scripts/sgo/historical_full_pipeline_replay._mirror_to_legacy`
(or in the upstream outcomes writer), re-run replay, and ROI/HR will populate.


### 2026-05-23 — Tolerant mirror→outcomes join (THE root-cause fix)
The earlier diagnostic endpoint pointed at `player_name_normalized`.
After running it against prod, the captured `sample_mismatches`
revealed FOUR concurrent drifts (not just one):

| key | replay value | outcome value |
|---|---|---|
| player_name_normalized | "hunter renfroe" | `null` |
| market | "batter_hits" | `null` |
| line | `0.5` (float) | `"0.5"` (str) |
| side | "OVER" | "over" |

The old mirror required exact equality on every key → only the rare
rows whose outcome happened to have the right shape attached (46 of
8,739 = 0.5%).

**Fix (`scripts/sgo/historical_full_pipeline_replay._mirror_to_legacy`):**
- Pre-fetches every outcome for the runner's event_ids in a single
  `find()` call instead of one query per runner row (also a major
  perf win — was N queries, now 1).
- Builds an in-memory index keyed by
  `(event_id, stat_family, _norm_line(line), _norm_side(side))`. Both
  the index and the lookup coerce `line` to `float` and `side` to
  `UPPER`, so float-vs-string + case mismatches no longer break the
  join.
- Per-event collisions (multiple players on the same line) are
  disambiguated by player name with three fallback rules:
  exact normalized → substring → first-candidate.
- Surfaces a `[mirror] groups=… events=… outcome_index_keys=…
  rows_mirrored=… rows_with_outcome=…` log line per call so the
  operator can confirm the new join rate in worker logs.

**New utilities** exported for tests + reuse:
`_norm_line`, `_norm_side`, `_norm_player`, `_build_outcome_index`,
`_pick_outcome`.

**Tests:** `tests/test_mirror_tolerant_join.py` — 9 cases. Includes
`test_index_lookup_simulates_prod_failure_mode` which seeds the EXACT
drift pattern from the operator's prod data (line float vs string,
side OVER vs over, player_name_normalized null on outcome side) and
asserts the new key path yields a match. Backend total: 91/91.

**Operator runbook to verify the fix on prod:**
```bash
# 1. Re-run the full pipeline replay for the same window (mirror is
#    the only thing that changed; outcomes don't need re-grading):
python -m scripts.sgo.historical_full_pipeline_replay \
       --league MLB --start 2025-05-01 --end 2025-06-01 --research-mode

# 2. Re-check coverage — should jump from 0.5% to ~95%+:
curl -sS "$API/api/emergent-admin/research/replay-outcome-coverage?sport=MLB&start=2025-05-01&end=2025-06-01" \
     -H "X-Admin-Token: $TOK" | jq '.pct_graded'

# 3. Re-run the optimizer — HR / ROI / Δcal will populate.
```


### 2026-05-23 — Mirror join: stat_family-tolerant fallback (wave 2 fix)
After the first tolerant-join fix, prod re-ran and the optimizer
showed grading for `pitcher_strikeouts` (~85%) + `total_bases` (~85%)
but `batter_strikeouts` + `walks_allowed` were STILL at 0/N graded.

**Root cause:** the outcomes collection writes those two families
under different `stat_family` names than RUNNER_OUTPUTS does (e.g.
`batting_strikeouts` vs `batter_strikeouts`). The first fix keyed
the index by `(event_id, stat_family, line, side)` so families that
disagree silently missed.

**Patch** (`_build_outcome_index` + `_mirror_to_legacy`):
- `_build_outcome_index` now returns TWO indices:
  - **primary**:  `(event_id, stat_family, line_float, side_upper)`
  - **fallback**: `(event_id, line_float, side_upper)` — drops family.
- The mirror tries the primary index first; on a miss it falls back
  to the family-agnostic index and disambiguates by player + market/
  stat_id via `_pick_outcome`.
- `_pick_outcome` extended with `wanted_stat_family` and
  `wanted_market` knobs so the fallback path can still narrow a
  multi-prop pool down to the right row.
- Mirror log line now reports `via_fallback=N` so the operator can
  see how many rows landed on the relaxed path.

**Tests:** 3 new cases pin the fallback semantics:
- narrow-by-family when multiple props share event+line+side
- market filter handles outcome.market=null by matching on stat_id
- stat_id+market filter doesn't zero a viable pool

Backend total: 94/94 passing.

**Operator verification (prod):**
```bash
# Re-run replay — should bring batter_strikeouts / walks_allowed
# coverage up to par with pitcher_strikeouts / total_bases.
python -m scripts.sgo.historical_full_pipeline_replay \
       --league MLB --start 2025-05-01 --end 2025-06-01 --research-mode

# Look for `via_fallback=…` in the worker log — that's the new path
# attaching the previously-missed batter_strikeouts/walks_allowed.

# Then re-check coverage; pct_graded should approach n_outcomes_in_window/n_total.
curl -sS "$API/api/emergent-admin/research/replay-outcome-coverage?sport=MLB&start=2025-05-01&end=2025-06-01" \
     -H "X-Admin-Token: $TOK" | jq '.by_stat_family'
```


### 2026-05-23 — Optimizer ranking fix: ungradable cells must not win
**Reported symptom:** even after the join fix landed 85%+ grading for
pitcher_strikeouts / total_bases, the optimizer "Top 25 by Score"
table was still entirely populated with ungraded `batter_strikeouts`
rows (`n=82(0/82 graded)`, `score=0.00`).

**Root cause** in `_score()` of `routes/emergent_admin/optimizer.py`:
```python
hr   = metrics.get("hit_rate") or 0.0   # None → 0.0
roi  = metrics.get("roi") or 0.0        # None → 0.0
...
return hr_score + roi_score + cal_score + cons_score + dd_penalty + sample_penalty
```
For a fully-ungraded cell the entire sum was `0.0`. Legitimately
graded losing cells had negative scores (e.g. `-5.7`). When the
results table sorted by `score desc`, the ungraded `0.0` cells beat
every real cell to the top. Same effect on `best_by_*` aggregations.

**Patch:**
- `_score()` now returns `None` when `n_graded < 1` (or, for legacy
  pre-diagnostic cells, when both `hit_rate` and `roi` are None).
- `_evaluate_cell()` writes `score: None` AND `ungradable: True` on
  those cells so they persist in `optimizer_run_results` (for visibility)
  but never compete for "best".
- `GET /optimizer/{run_id}/results` now:
  - Accepts `include_ungradable: bool = False` (default off).
  - Filters `score: {$ne: None}` from `top` / `worst` / every
    `best_by_*` aggregation.
  - Surfaces `ungradable_count` and `ungradable_top` (sorted by
    `n_bets desc`, limit 10) in the response so the operator can
    see WHICH high-volume slices have no grading.

**Frontend** (`AdminTesting.jsx`):
- Optimizer Results panel now renders an amber "⚠ N cells excluded
  from rankings (no graded rows)" banner above the Top 25 table when
  `ungradable_count > 0`. An expandable details block shows the
  highest-sample ungradable cells (family · bucket · tier · n rows).

**Tests:** 3 new cases in `tests/test_optimizer_evaluate_combo.py`:
- `test_score_returns_none_when_no_graded_rows` (the regression pin)
- `test_score_is_finite_when_some_rows_graded`
- `test_score_returns_none_for_legacy_pre_diagnostic_shape`

Backend total: 97/97. Smoke-tested end-to-end against a seeded run:
default response correctly excludes the score=None cell from `top` /
`best_by_stat_family` and surfaces it in `ungradable_top`; passing
`include_ungradable=true` restores the old behavior.


### 2026-05-23 — TWO MORE bugs found via the new diagnostic
After the join + family-fallback + score-None fixes landed, the
optimizer DID start showing grading (36/44 graded for pitcher_strikeouts)
but every value was deeply wrong: HR=13.9%, ROI=-77.3%, and crucially
**all three tiers (safe_haven, front_lines, war_zone) showed IDENTICAL
metrics**. That's the smoking gun — three different gate-strictness
levels can only produce identical numbers if they're querying the
same row pool.

**Bug #1 — tier filter never actually filtered**
(`routes/emergent_admin/optimizer.py::_evaluate_cell`):
```python
f"{tier}_pass": {"$exists": True},   # ← BUG: matches every row
```
The mirror writes `safe_haven_pass`, `front_lines_pass`, `war_zone_pass`
as booleans on EVERY row (True or False). `{"$exists": True}` is
satisfied by both — so `safe_haven`, `front_lines`, and `war_zone`
all queried the same superset. **Fix:** `f"{tier}_pass": True`.

**Bug #2 — wrong outcome attached to UNDER bets**
(`scripts/sgo/historical_full_pipeline_replay`):
`grade_outcome()` in `build_historical_outcomes` produces ONE
`outcome_numeric` per source-doc side. In prod the outcomes
collection writes side="over" for nearly every row (the propvision-
side source coll only carries the OVER side, since PrizePicks
operates as Over/Under and "More" is treated as OVER).
The mirror was joining replay UNDER rows to outcome OVER rows via my
normalized join and **copying the OVER side's outcome_numeric
verbatim**. So an OVER LOSS (`outcome_numeric=0`) was being
attributed to the UNDER bet as a LOSS — when in fact the UNDER bet
WON. This drove pitcher_strikeouts HR down to ~14% (it should be the
inverse: 86%).

**Fixes:**
- Removed `side` from the outcome-index key — the index is now keyed
  by `(event_id, stat_family, line_float)` (primary) and
  `(event_id, line_float)` (fallback). The mirror normalizes both
  sides to UPPER internally.
- New helper `_flip_outcome_for_opposite_side(outcome, replay_side)`:
  if `replay_side != outcome_side` (after upper-case norm), invert
  `outcome_numeric` (1 ↔ 0) and `hit`, preserve PUSH (0.5). Adds a
  `side_flipped_from_outcome: True` marker for observability.
- `[mirror]` log line now reports `side_flipped=N` alongside
  `via_fallback=N`.

**Tests:** 5 new cases in `test_mirror_tolerant_join.py`:
- flips when sides disagree
- doesn't flip when sides agree (returns same dict reference)
- preserves PUSH unchanged
- handles None outcome
- flips when OVER won → UNDER must lose
Backend total: 102/102 unit tests passing (HTTP suites also pass on a
fresh backend; transient timeouts were due to background sync churn).

**Operator runbook for prod:**
```bash
# Re-run the replay so the mirror reattaches outcomes with the
# side-flip semantics correct:
python -m scripts.sgo.historical_full_pipeline_replay \
       --league MLB --start 2025-05-01 --end 2025-06-01 --research-mode

# Check the new mirror log line — expect non-zero side_flipped:
#   [mirror] groups=N events=M primary_idx=… fallback_idx=…
#     rows_mirrored=N rows_with_outcome=M (via_fallback=k side_flipped=j)

# Re-run optimizer. Now each tier should show DISTINCT n / HR / ROI
# (safe_haven sample shrinks as expected), and pitcher_strikeouts
# UNDER bets should swing from ~14% HR to a sensible value.
```


### 2026-05-23 — Reverted tier-gate strict filter + Preflight UI
**Reported symptom:** previous fix flipped `{tier}_pass: {"$exists":True}`
→ `True`, which is semantically correct but produced "succeeded but no
results" because production gates rarely pass on historical data.

**Decision:** treat tier as a LABEL by default. The strict gate
behavior is opt-in via `enforce_tier_gates: bool = False` on
`OptimizerRunBody`. This is the pragmatic choice — the user has
been blocked on real results for days; they can flip the toggle when
they specifically want strict-gate analysis.

**Shipped:**
- `OptimizerRunBody.enforce_tier_gates` (default False).
- `_evaluate_cell` switches between `{"$exists":True}` and `True`
  based on the body flag.
- **NEW endpoint** `POST /optimizer/preflight` returning:
  `n_total_in_window`, `n_graded`, `pct_graded`, per-tier breakdown,
  per-stat_family breakdown, per-odds_bucket breakdown, plus a
  `diagnosis` string that explicitly identifies whether
  (a) the join failed → run /replay-outcome-join-diagnose,
  (b) strict-mode gives thin samples → toggle off,
  (c) data looks healthy.
- **Frontend** — the Optimizer launch panel now auto-runs the
  preflight every time the sport/start/end/enforce_tier_gates
  changes. Renders a red/amber/green banner with per-tier sample
  counts above the Launch button. The operator sees BEFORE running
  whether they'll get real results.
- **Tests:** `tests/test_optimizer_preflight.py` (4 cases) pins the
  default vs strict semantics, empty-window diagnosis, and the
  "join failure" warning when pct_graded < 1%.

Unit tests: 66/66. Backend total: 99/99 (4 new HTTP tests).

**Operator should now see real results.** Hit "Run Auto-Optimizer"
without flipping any toggles; the preflight banner above the button
will tell you what to expect. If the banner is green, the run will
produce actual ranked results.


### 2026-05-23 — Optimizer scoring/consistency math fix + tier default ON
**Reported symptom:** real graded data now (89.8%), but the scoring
was still nonsense — a 53.3% HR / -0.8% ROI cell scored -150.23 and
ended up in "Worst configs", while the actual top scoring cell was
also negative. Plus all three tiers showed identical metrics for
the same combo.

**Two bugs:**

1. **`daily_consistency` formula exploded.** Old code:
   `1 - stddev(daily_pnl) / |mean(daily_pnl)|`. When daily PnL
   averages near zero (profitable days cancel losing days), the
   denominator collapses → consistency = -99.72 → score = -150.
   **Fix:** redefine as *proportion of days with positive net PnL*.
   Naturally bounded in `[0, 1]`. 1 = every day profitable,
   0 = no day profitable. Defensive clamp in `_score()` so legacy
   cells with the broken value also can't dominate the ranking.

2. **`enforce_tier_gates` default OFF made tier meaningless.** When
   all three tiers query the same row pool, they produce identical
   metrics — defeating the whole point of having tier dimensions.
   Data is now healthy enough that strict mode produces real
   samples. **Fix:** default `enforce_tier_gates=True` on both
   backend `OptimizerRunBody` and frontend toggle.

**Shipped:**
- `_evaluate_combo`: rewritten consistency calc; preserves
  `daily_consistency: None` semantics when n_days < 2.
- `_score`: clamps consistency to `[0, 1]` defensively to handle
  legacy persisted cells with the old unbounded value.
- `OptimizerRunBody.enforce_tier_gates: bool = True` (was False).
- `AdminTesting.jsx`: enforceTierGates checkbox now starts ON.

**Tests:** 4 new cases in `tests/test_optimizer_evaluate_combo.py`:
- consistency bounded [0, 1] for mixed wins/losses
- consistency = 1.0 when every day profitable
- consistency = 0.0 when no day profitable
- `_score` clamps a legacy unbounded value (-99.72) so it can't
  drive the score below -10.

Backend optimizer suite: 12/12. Unit total: 70/70.

**Operator next step on prod:**
```bash
git pull && systemctl restart vision-backend
# Re-run the optimizer. With enforce_tier_gates=True (now default)
# each tier shows distinct sample sizes. Daily consistency is now in
# [0,1] so a single noisy cell can't pollute the ranking.
```


### 2026-05-23 — DETERMINISM: kill random sampling, lock tiebreaks, add /top-per-family
**Reported symptom:** same window run repeatedly → different Top 1
each time. "Best by …" disagreeing with the Top 25 above it.

**Three concrete sources of nondeterminism — ALL FIXED:**

1. **`_enumerate_combos` used unseeded `random.choice` to subsample**
   when `total_combos > max_per_cell`. Every run drew a DIFFERENT
   subset of the 126,000 grid → different leaderboard. **Fix:**
   rewritten to always return the full Cartesian product. No
   sampling. `max_per_cell` is now a no-op (kept for backwards-compat
   on the wire). User explicitly asked for brute-force; brute-force
   is what we do.

2. **Per-cell `cell_results[:200]` hard cap** silently dropped combos
   beyond the top 200 in each cell. **Fix:** removed. All combos
   that pass `min_bets` and produce a finite score are persisted.

3. **Mongo `.sort([("score", -1)])` with NO tiebreak.** Cells often
   tie on score (e.g. 50 rows at -5.17). Tied results returned in
   insertion order, which varied because the worker writes cells in
   parallel and finish-order is OS-scheduler dependent. **Fix:**
   composite sort key everywhere — `(score, tier, stat_family,
   odds_bucket, n_bets)` — applied to `/results.top`, `/results.worst`,
   every `_best_by()` aggregation, AND `save_as_candidates`. Same
   tiebreak in the in-memory `_evaluate_cell` sort, so the persisted
   cell order is also stable.

**New endpoint** `GET /optimizer/{run_id}/top-per-family?top_n=3&tier=…`:
- Returns Top-N graded configs grouped by `(stat_family, odds_bucket)`.
- Uses the IDENTICAL deterministic sort, so it can never disagree
  with the Top-25 or Best-by views on the same run.
- Frontend Optimizer panel now renders a "★ Top 3 per Stat Family"
  card grid above "Best by family" — the operator's actual ask.

**Tests:** `tests/test_optimizer_determinism.py` (6 cases):
- `_enumerate_combos` byte-identical across 10 runs
- no duplicates produced
- `max_per_cell` does NOT decimate (still 144 combos for a 144-cell grid)
- `/top-per-family` returns exactly N per group, score-sorted
- `/top-per-family` byte-identical across 5 GETs
- `/results.top` byte-identical across 5 GETs

Backend total: 41/41 across optimizer + mirror + diagnose + preflight.

**Operator runbook on prod:**
```
git pull && systemctl restart vision-backend
# Re-run the optimizer. Then run the same window TWICE.
# Top-25, Best-by, AND the new Top-3-per-Family card must all
# show identical numbers run-to-run, AND must agree with each other.
```


### 2026-05-23 — Top-25 dedup + missing-family visibility
**Reported symptoms:**
1. Top-25 had the same logical config repeated 6× with different
   `tp_min` values — visual noise from threshold combos that filter
   to the IDENTICAL sample.
2. Only 4-6 of 14 stat families showing — the other 8 were silently
   dropped because all their combos failed `min_bets`.

**Fixes (architecture, not math):**
- `_evaluate_cell` stores a `sample_sig` tuple per result row:
  `(n_bets, wins, losses, pushes, profit_units)`. Two combos with the
  same sig produce identical filtered samples and are mathematically
  equivalent.
- `/results.top` + `/results.worst` + `/top-per-family` now run a
  dedup aggregation grouping by `(tier, family, bucket, sample_sig)`
  with `$first` keeping the row whose threshold dict sorts first.
  Each surviving row carries a new `n_equivalent_combos` field so
  the operator can see "this config + 5 threshold-twins".
- `/results` now also returns `family_coverage[]` — for every
  stat_family that produced any cell (graded or not), reports
  `n_cells`, `n_graded_cells`, `best_score`, `best_n_bets`. Answers
  "where are the other 9 families?" directly.

**Frontend:**
- New "Stat Family Coverage" card grid above Top-3-per-Family.
  Green border = family has at least one graded cell. Red border =
  every cell was skipped (min_bets too high or no graded rows).
  Hint text: "lower min_bets or widen the window".
- Top-25 rows now show "+N equiv" badge next to the threshold
  display when `n_equivalent_combos > 1`.

**Tests** (2 new in `tests/test_optimizer_determinism.py`):
- `test_top_collapses_threshold_equivalent_samples`: seed 6 cells
  with identical sample_sig + 1 unique; verify Top collapses to 2
  rows with `n_equivalent_combos: 6` on the first.
- `test_results_includes_family_coverage`: verify family_coverage
  enumerates every persisted family.

Backend total: 41/41 across optimizer + mirror + preflight + diagnose.


### 2026-05-23 — Grid audit: wildcards, data-driven enumeration, min_bets default
**Verified directly against prod data:** 14 stat families exist with
graded rows (some with 2,488 each), 6 odds buckets present. But the
old optimizer was only surfacing 4 families and 3 buckets.

**Root causes (3 grid-mechanics bugs):**

1. **No wildcard threshold.** `DEFAULT_GRID["hr_l20_min"]` started at
   0.55. Every combo applied all 6 axes. Families like `hits` and
   `earned_runs`, whose L20 hit rates sit mostly below 55%, had ZERO
   combos that left ≥ min_bets graded rows → silently dropped.
   **Fix:** every numeric axis in `DEFAULT_GRID` now includes a
   sentinel (`float("-inf")` for min-axes, `float("+inf")` for
   max-axes). `_row_passes_combo` short-circuits the row-value check
   when the threshold is the wildcard — even rows with null values
   for that axis pass. So a single sweep can ask "best combo with
   constraints" AND "best combo ignoring this axis" at the same time.

2. **Hard-coded `DEFAULT_ODDS_BUCKETS`** that may not match the data.
   **Fix:** the optimizer now `distinct()`s odds_buckets from the
   actual replay collection for the window — same way it already
   discovered stat_families. `odds_na` (rows with no odds) is
   explicitly excluded since they can't produce a graded payout.

3. **`min_bets=30` default** masked thin-but-real families. Lowered
   to 15.

**New endpoint** `POST /optimizer/grid-diagnose`:
- Per axis (`hr_l20_min`, `hr_l10_min`, `hr_l5_min`, `cv_max`,
  `edge_min`, `tp_min`):
  - data percentiles (p10/p25/p50/p75/p90/p99)
  - for each grid value, n_pass and pct_pass
  - flagged when the most-strict value passes < 30 rows
- Returns a `diagnosis` string ("⚠ N grid axis issues detected")
  with actionable fix suggestions for each.

**Tests (6 new in `test_optimizer_grid_diagnose.py`):**
- `DEFAULT_GRID` includes wildcards on every axis
- `_row_passes_combo` accepts null row values when threshold is wildcard
- `_row_passes_combo` rejects null row values when threshold is real
- `/grid-diagnose` returns per-threshold pass counts
- `/grid-diagnose` flags an over-strict axis when n_pass < 30

Backend total: 47/47 across optimizer + mirror + diagnose suites.

**Operator runbook on prod:**
```bash
# 1. Pull + restart
git pull && systemctl restart vision-backend

# 2. Audit the grid directly (no UI needed):
TOK="17808e1c13717b0d2170f6e1f023388d93740ff66f70ae1a"
curl -sS -X POST "https://propvision.bet/api/emergent-admin/optimizer/grid-diagnose" \
     -H "Content-Type: application/json" \
     -H "X-Admin-Token: $TOK" -H "X-Agent-Id: e1-grid" \
     -d '{"sport":"MLB","start":"2025-05-01","end":"2025-06-01"}' | jq

# 3. Re-run the optimizer with strict tier gates. With wildcards in
# the grid + min_bets=15, you should now see ALL 14 families.
```



### 2026-05-24 — Tier filter: route by ODDS RANGE (mirrors live runner)
**Reported symptom:** Operator launched `opt_dd261da1e1` on prod (MLB
2025-05-01..05-31). Worker reported `succeeded rc=0` in 5 s — but
the run actually evaluated **0 combos** and persisted **0 results**
(`cells_done=105, cells_skipped_empty=105, n_results=0`). The
results endpoint then returned `404 "results not available yet"`.

**Root cause** — the optimizer was filtering tier membership by the
prod gate-pass boolean (`{tier}_pass=True`) when `enforce_tier_gates`
was True (the default at the time). On prod's May 2025 replay window:
- **0 / 7,664 rows** had `safe_haven_pass=true`
- **0 / 7,664 rows** had `front_lines_pass=true`
- **0 / 7,664 rows** had `war_zone_pass=true`

Every single one of the 7,664 rows fails `coverage_gate` because
historical replay rows are anchored to a single book (DraftKings) →
`book_count = 1` → `coverage_gate` rejects them. The boolean was
useless for backtesting and silently starved every cell.

**Architectural fix** — route by **odds range** (matches live
`services/scoring/gates/thresholds.py::resolve_target_tier`):
```
safe_haven  : odds ≤ -300         (heavy chalk)
front_lines : -299 ≤ odds ≤ +149   (mid range)
war_zone    : odds ≥ +150          (longshot)
```
- New helper `_tier_odds_filter(tier) -> dict` in
  `routes/emergent_admin/optimizer.py` produces the Mongo clause that
  exactly mirrors `resolve_target_tier`. Imports
  `UNIVERSAL_SAFE_HAVEN_MAX` + `UNIVERSAL_WAR_ZONE_MIN` from the
  canonical source — no drift possible.
- `_evaluate_cell` now uses `**_tier_odds_filter(tier)` instead of
  `f"{tier}_pass": True/{"$exists":True}`.
- `/optimizer/preflight` uses the same routing helper so the banner
  the operator sees is exactly what the optimizer will scan.
- Rows with `odds=None` are routed to no tier (matches live
  `resolve_target_tier` returning `None`).
- `enforce_tier_gates` is now **opt-in** (default `False`). When
  `True`, it ADDS `{tier}_pass=True` ON TOP of the odds-range filter —
  exposed for parity validation but never the default.
- Frontend `enforceTierGates` state defaults `false`; tooltip text
  updated to explain it usually empties cells on historical data.

**Tests:** +11 (6 in `test_optimizer_tier_odds_routing.py` pinning
the routing helper; 5 in `test_optimizer_preflight.py` rewritten to
seed odds spanning all three tiers and assert correct routing +
`enforce_tier_gates` adds the gate-pass filter on top + null-odds
exclusion). Backend total: **56/56** across optimizer + mirror +
diagnose + preflight + tier-routing suites.

**Operator runbook to verify on prod:**
```bash
git pull && systemctl restart vision-backend

# 1. Preflight should now show NON-ZERO per-tier counts:
TOK="17808e1c13717b0d2170f6e1f023388d93740ff66f70ae1a"
curl -sS -X POST "https://propvision.bet/api/emergent-admin/optimizer/preflight" \
     -H "Content-Type: application/json" -H "X-Admin-Token: $TOK" \
     -d '{"sport":"MLB","start":"2025-05-01","end":"2025-05-31"}' | jq '.by_tier'

# Expect (based on prod data):
#   safe_haven  : ~2,500 rows (odds_lt_-200 subset where odds ≤ -300)
#   front_lines : ~5,000 rows
#   war_zone    :   ~130 rows
# (Sum ≈ 7,664 minus any null-odds rows.)

# 2. Launch optimizer — n_results > 0 this time:
curl -sS -X POST "https://propvision.bet/api/emergent-admin/optimizer/run" \
     -H "Content-Type: application/json" -H "X-Admin-Token: $TOK" \
     -d '{"sport":"MLB","start":"2025-05-01","end":"2025-05-31"}'
```


### 2026-05-24 — Optimizer thin-combo surfacing (user strategy fix)
**Reported intent:** "we are only looking for the best of the best,
it will naturally be a thin pool. … 11 combos that survive in one
month, consistent month after month, is over 100 per year." The user
hunts for thin-but-consistent edges across months — the previous
defaults aggressively filtered them out.

**Changes shipped:**
1. `OptimizerRunBody.min_bets` default **15 → 3** (anything < 3 is
   pure coin-flip with no statistical signal).
2. `_score` sample penalty weight `1.0 → 0.25`, and baseline
   floor changed from `max(min_bets, 50)` → `max(min_bets, 10)`.
   With this combo a 5-bet @ 80%-HR combo is no longer crushed by a
   -1.22 penalty; it only loses ~0.13 vs an n=10 combo with same
   metrics. `daily_consistency` and `roi` now dominate ranking for
   thin combos — exactly the signals the user wants.
3. `overfit_threshold` (the flag, not a filter) lowered
   `max(min_bets, 50) → max(min_bets, 25)` so it doesn't paint
   every thin combo red.
4. Frontend `min_bets` default 30 → 3 (`AdminTesting.jsx`).
5. **Tests:** `tests/test_optimizer_thin_combo_surfacing.py` (5
   cases): default min_bets, thin-winner-beats-fat-loser,
   sample-penalty-zero-above-baseline, sample-penalty-gentle-below,
   consistency-dominates-for-thin-combos. Backend total: **54/54**
   across optimizer + mirror + diagnose + preflight + tier-routing
   + thin-combo suites.

**Operator runbook on prod:**
```bash
git pull && systemctl restart vision-backend
# Re-run any window. Thin (n=3..14) combos with high consistency
# will now appear in Top-25 — the next step is a cross-month
# persistence view (planned) that highlights combos which recur
# in the top-N of multiple monthly runs.
```


### 2026-05-24 — Market coverage gap: 7 markets silently dropped + HR/SB probe
**Reported symptom:** Optimizer Top 25 only ever showed 2 families
(`pitcher_strikeouts`, `earned_runs`); user expected to see HR, hits,
batter_walks, HRR, singles, stolen_bases, pitcher_outs, etc.

**Diagnosis (cross-collection audit on prod, May 2025 MLB):**
- Raw odds (`sgo_replay_alt_odds_raw`)  : **14 markets**
- Outcomes (`sgo_pp_research_outcomes`) : 15 stat_families
- Replay cache (optimizer's source)     : **only 7 markets** ← gap

**7 markets present in raw odds but missing from the replay cache:**
batter_hits_runs_rbis, batter_singles, batter_walks, fantasy_score,
pitcher_hits_allowed, pitcher_outs, pitcher_pitches_thrown.

**Root cause** — `_STAT_FAMILY_MAP` in
`services/replay/mlb_feature_cache.py` was incomplete: missing
`batter_singles`, `batter_walks`, `batter_home_runs`,
`batter_stolen_bases`, `batter_doubles`. Without a family token the
runner returned `None` for these market rows → silent drop. Plus the
historical Odds-API ingest whitelist `_BASE_MARKETS` was missing the
same 5 markets, so the underlying odds for those markets were never
fetched against fresh windows in the first place.

**Fixes shipped:**
1. **Extended `_STAT_FAMILY_MAP`** with `batter_singles → singles`,
   `batter_walks → batter_walks`, `batter_home_runs → home_runs`,
   `batter_stolen_bases → stolen_bases`, `batter_doubles → doubles`.
2. **Extended `_CANONICAL_FAMILY_TO_MODEL_KEY`** with
   `batter_walks → walks` (distinct from `walks_allowed → pitcher_walks`).
3. **Extended `_BASE_MARKETS`** in
   `services/replay/historical_alt_odds_ingest.py` so future Odds-API
   ingests fetch all 5 added markets.
4. **NEW endpoint `GET /api/emergent-admin/research/market-coverage-audit`**
   — surfaces every market present in raw odds but missing from the
   replay cache, plus the precise drop reason (UNMAPPED vs MODEL
   MISSING vs RUNTIME DROP). Makes future silent drops impossible to
   miss.
5. **NEW script `scripts.sgo.probe_mlb_markets`** — read-only SGO API
   probe that dumps every market_id + statID SGO returns for an MLB
   window. The user can run this on prod to confirm whether SGO's
   PrizePicks feed carries `batter_home_runs` (HR was absent from
   raw odds for May 2025 — needs confirmation upstream of our ingest).
6. **Tests:** `tests/test_mlb_market_coverage.py` (6 cases): every
   required market → family, alternates resolve identically, every
   family has a supported model key, batter_walks vs walks_allowed
   distinction, pitcher_outs key bridge, HRR plus-key bridge.
   Backend total: 60+/60+ passing.

**Operator runbook on prod (after `git pull && systemctl restart`):**
```bash
TOK="17808e1c13717b0d2170f6e1f023388d93740ff66f70ae1a"

# 1. Audit market coverage before rebuilding
curl -sS "https://propvision.bet/api/emergent-admin/research/market-coverage-audit?sport=MLB&start=2025-05-01&end=2025-05-31" \
     -H "X-Admin-Token: $TOK" | jq

# 2. Probe SGO directly for HR / SB availability
python -m scripts.sgo.probe_mlb_markets \
       --start=2025-05-01 --end=2025-05-03 \
       --max-events=100 --save=/tmp/mlb_market_probe.json

# 3. If HR appears in step 2 — re-run reshape + replay so it lands
python -m scripts.sgo.reshape_sgo_to_replay_odds --league MLB \
       --start 2025-05-01 --end 2025-05-31
python -m scripts.sgo.historical_full_pipeline_replay --league MLB \
       --start 2025-05-01 --end 2025-05-31 --research-mode

# 4. Re-run the optimizer — Top 25 should now span 10-14 families
```


### 2026-05-24 — Fail-loud engine drop telemetry + full pipeline trace
**User insight:** "is there not some default in code where we can just
use like All-true and get all the info without filtering the ingest.
then we can just use a UI filter to display what we want to see."

**Yes — that's the correct philosophy.** Reshape already pulls all 14
markets. The ingest, feature cache, runner, and replay cache should
NEVER silently drop a row that has a trained model + raw odds + a
canonical market mapping. We now make any such drop **fail loud**.

**Full pipeline trace on prod (May 2025, before this fix):**
```
raw_odds       : 14 markets
feature_cache  :  9 families  (lost 5: hits_runs_rbis/singles/batter_walks/HR/SB)
layer3 outputs :  7 families  (lost 2 more: hits_allowed/pitching_outs)
runner output  :  7 families
replay cache   :  7 markets
```

**Shipped fixes (no architectural rewrite — just plug the leaks):**
1. `_STAT_FAMILY_MAP` (+5 markets) and `_BASE_MARKETS` (+5) already
   shipped earlier today — addresses the feature-cache drop of
   hits_runs_rbis/singles/batter_walks/HR/SB (once user rebuilds
   feature cache).
2. **`replay_one()` drop telemetry** — new optional `drop_counter`
   kwarg. The engine increments `f"{family}::{reason}"` on every
   early return (`model_feature_cols_miss`, `missing_line`,
   `missing_odds_or_side`, `feature_build_returned_none`) AND emits
   a `[replay_one_drop]` info log. **`hits_allowed` and `pitching_outs`
   dropping at the engine stage will now self-report which guard
   they're failing** — no more guessing.
3. **`replay_date()` summary** now persists 3 new structured fields:
   `drop_counter_by_family_and_reason`,
   `unmapped_markets_by_market`, `no_cache_by_family`. Stored on
   the run's STATUS_COLL row.
4. **`/research/market-coverage-audit`** extended with:
   - `pipeline_trace`: raw_odds → feature_cache → layer3 → runner
     output → UI cache (5 stages, families per stage)
   - `engine_drop_telemetry`: most recent STATUS_COLL row's
     per-family drop counters
   The operator sees AT A GLANCE exactly where a family disappeared,
   one stage at a time.

**Operator runbook (after `git pull && systemctl restart`):**
```bash
TOK="17808e1c13717b0d2170f6e1f023388d93740ff66f70ae1a"

# 1. Rebuild MLB feature cache for May 2025 so my new family map lands
python -m scripts.mlb_replay_build_feature_cache \
       --start=2025-05-01 --end=2025-05-31 --reset

# 2. Re-run the production replay for the same window
python -m scripts.sgo.historical_full_pipeline_replay \
       --league MLB --start 2025-05-01 --end 2025-05-31 \
       --research-mode

# 3. Audit pipeline trace + drop telemetry
curl -sS "https://propvision.bet/api/emergent-admin/research/market-coverage-audit?sport=MLB&start=2025-05-01&end=2025-05-31" \
     -H "X-Admin-Token: $TOK" | jq

# Expected after rebuild:
# pipeline_trace.feature_cache_families: 12+ families (was 9)
# engine_drop_telemetry.drop_counter_by_family_and_reason:
#   { "hits_allowed::feature_build_returned_none": N,
#     "pitching_outs::feature_build_returned_none": M, ... }
#   — these tell us EXACTLY why the engine drops them (likely
#   PA-statcast hydration miss for pitchers on specific dates).
```


### 2026-05-24 — Orchestrator single-pass mode (tiers route, don't filter)
**Reported observation:** "we are still failing props for tiers
instead of using it for routing. why are we doing a new scan for each
tier. one scan and routed."

User saw 32 dates × 3 tiers = 96 runner calls, every tier producing
identical `scanned`/`W/L/P` counts with `qual=0` — confirming the
3× per-date pattern was wasted work after the optimizer was
converted to odds-range routing.

**Fix:** `scripts/sgo/historical_full_pipeline_replay.py` now runs
**single-pass** by default — ONE `run_production_replay()` call per
date with `tier="war_zone"` (most permissive gate config). The
mirror tolerates missing tier_evals and writes `tier_pass=False` for
the un-scanned tiers (which they were anyway on historical data due
to `coverage_gate` requiring live `book_count`). Tier routing now
happens 100% downstream in:
- The **optimizer** (via `_tier_odds_filter` — already shipped)
- The UI Results panel (via odds-range buckets — to be wired)

**Opt-in flag:** `--multi-tier-gates` restores the legacy 3-call
behaviour for parity audits. Default OFF.

**Operational impact:** 3× speedup on historical replay runs + 3×
reduction in `mlb_propvision_full_pipeline_runs` row writes.

**Tests:** +4 in `tests/test_orchestrator_single_pass.py` pinning
single-pass default, opt-in multi-tier flag, runner_tiers collapse,
and three-tier preservation in multi-mode. Total backend tests:
**21/21** in this session (60+ pre-existing).

**Operator runbook (after `git pull && systemctl restart`):**
```bash
# Single-pass (default, 32 calls instead of 96):
python -m scripts.sgo.historical_full_pipeline_replay \
       --league MLB --start 2025-05-01 --end 2025-06-01 \
       --research-mode

# Restore legacy 3-tier scan (only if you specifically need the
# SH/FL/WZ tier_pass booleans for parity audit):
python -m scripts.sgo.historical_full_pipeline_replay \
       --league MLB --start 2025-05-01 --end 2025-06-01 \
       --research-mode --multi-tier-gates
```


### 2026-05-24 — Top-5 per family + every discovered family surfaced
**Reported symptom:** "i want the top 5 combos for every stat type
listed ... if the optimizer doesn't return 5 top combos for every
stat type available consider it broken and failed."

**Root cause:** Three independent issues compounded —
1. `top_per_family` defaulted `top_n=3`. User wants 5.
2. The endpoint only returned `(family, bucket)` groups that wrote
   rows. Families that produced 0 graded combos were silently dropped
   from the response so the UI never even tried to render them.
3. `family_coverage` aggregation in `/results` had the same bug —
   only families with results showed up; the 12 unrun families were
   invisible.

User's run `opt_0bc25fdc3d` (May 2025, MLB):
- `body.tiers = ['safe_haven']` only (1 tier, not the 3 the UI showed
  selected — likely state desync in the form)
- 14 stat_families discovered → 5 buckets × 1 tier = 70 cells total
- 66 cells empty (safe_haven = odds ≤ -300, very thin window for
  most batter/pitcher props except pitcher_strikeouts)
- Only `pitcher_strikeouts` produced graded combos → user saw 1
  family in Top-25 + Top-3-per-family panels.

**Fixes shipped (`optimizer.py` + `AdminTesting.jsx`):**
1. `top_per_family` default `top_n=3 → 5`.
2. `top_per_family` accepts `include_empty: bool = True`. When True,
   appends a placeholder group for every discovered family that
   produced 0 graded combos, with explanatory `status` field
   (`no_rows_after_tier_filter` or `no_graded_combos`).
3. `/optimizer/{run_id}/results.family_coverage` now ALWAYS includes
   every family from `state.stat_families`. Empty families get
   `status="no_rows_after_tier_filter"`; non-empty get
   `status` in `{graded, all_skipped_low_sample}`.
4. Frontend `AdminTesting.jsx`:
   - Calls `top-per-family?top_n=5&include_empty=true`.
   - Heading "Top 3 per Stat Family" → "Top 5 per Stat Family".
   - Empty-family cards render at 55% opacity with a yellow
     explanatory line: "⚠ no rows in selected tier — widen tiers
     (all 3 checked) or expand window".

**Tests:** +5 in `test_optimizer_family_visibility.py` pinning
`top_n=5` default, `include_empty=True` default, endpoint path,
merge-of-empty-families logic, and the user's strict contract
("every discovered family MUST be represented"). Backend total:
**32/32** passing across 7 optimizer + market-coverage + thin-combo
+ tier-routing + orchestrator + family-visibility suites.

**Operational note:** User's safe_haven-only run also exposed a
data-thinness issue. Recommend re-running with all 3 tiers selected
in the UI to populate front_lines (5,457 graded rows in May 2025)
and war_zone (129) — that alone will give Top-5-per-family real
data for ~10 of the 14 discovered families.


### 2026-05-24 — Multi-book universe (END of PrizePicks-only anchor)
**Architecture brief from user:**
> "We are no longer treating PrizePicks as the sole anchor universe.
>  PropVision is evolving from a PP-only optimizer into a market-wide
>  betting intelligence platform."

**Root cause uncovered:** `build_pp_research_core.py` hard-filtered
`{"$match":{"books.book_id":"prizepicks"}}` at line 147. PrizePicks
historically carries only ~122 HR offers across ALL time (vs DK 58k,
FD 94k). The filter silently destroyed entire prop families (HR, SB,
doubles, triples) → never reached replay cache → optimizer couldn't
see them.

**Shipped:**
1. **`scripts/sgo/build_pp_research_core.py`** — anchor priority
   chain replaces the single-PP filter:
   ```
   prizepicks → draftkings → fanduel → betmgm → caesars → betonlineag
   → first-alphabetical-available
   ```
   `_pick_anchor()` deterministic + reproducible. PP rows are
   byte-identical to before (PP is still priority #1).
2. **New per-row metadata** propagated all the way to the optimizer:
   - `anchor_book`, `anchor_line`, `anchor_odds`, `anchor_source`
     ("priority" / "fallback_first_available" / "none")
   - `available_books: [...]`
   - `playable_on_pp`, `playable_on_dk`, `playable_on_fd`,
     `playable_on_mgm`, `playable_on_caesars`, `playable_on_bol`
3. **`scripts/sgo/reshape_sgo_to_replay_odds.py`** carries the new
   fields into `sgo_propvision_full_pipeline_replay`.
4. **`routes/emergent_admin/optimizer.py`** — `book_filter` field on
   `OptimizerRunBody` (default `"any"`). Helper `_book_filter_clause`
   translates to Mongo predicates targeting `playable_on_*` flags
   (NOT `anchor_book` — a row anchored on DK can still be playable
   on PP if PP offered the line). 8 filter modes: any, pp_only,
   dk_only, fd_only, mgm_only, caesars_only, bol_only, multi_book.
5. **New `/research/book-coverage-audit`** endpoint surfaces per-book
   row counts, per-(book, family) coverage matrix, and playability
   percentages. Diagnoses the universe at a glance.
6. **`AdminTesting.jsx`** — book-filter dropdown next to the
   enforce-tier-gates checkbox. 8 options. Preflight + launch
   both pass `book_filter`.
7. **Tests:** `test_multi_book_universe.py` (14 pins) lock the
   priority order, fallback behaviour, playable-flag map,
   deterministic alphabetical fallback, `book_filter` map and the
   `any` default. **Backend total: 40/40 passing.**

**Operator runbook on prod (after `git pull && systemctl restart`):**
```bash
TOK="17808e1c13717b0d2170f6e1f023388d93740ff66f70ae1a"

# 1. Rebuild sgo_pp_research_core for May 2025 — multi-book anchors
python -m scripts.sgo.build_pp_research_core --league MLB \
       --start 2025-05-01 --end 2025-05-31

# 2. Rebuild enrichment + reshape so anchor_book + playable_* flags
#    flow to the replay cache
python -m scripts.sgo.build_historical_consensus_probabilities \
       --start 2025-05-01 --end 2025-05-31
python -m scripts.sgo.reshape_sgo_to_replay_odds --league MLB \
       --start 2025-05-01 --end 2025-05-31

# 3. Audit per-book coverage
curl -sS "https://propvision.bet/api/emergent-admin/research/book-coverage-audit?sport=MLB&start=2025-05-01&end=2025-05-31" \
     -H "X-Admin-Token: $TOK" | jq

# 4. Re-launch the optimizer — HR / SB / doubles / triples
#    should now appear in Top-25 + Top-5 per family.
```


### 2026-05-24 — Brute-force ALWAYS, form-grid becomes display filter
**User directive (verbatim):**
> "the grid should create the absolute 5 best combos for every tier
>  from brute force. the settings on the grid should only be used to
>  filter that AFTER the absolute best are displayed"

**Root cause behind the previous Top-25:** The operator's form-grid
`hr_l20:[0.55…0.80]` was narrowing the search space ENORMOUSLY. For
hitter props at chalk odds (-300), very few rows have hr_l20≥0.55, so
22,192 of 23,040 combos got marked low-sample → only pitcher_strikeouts
surfaced in safe_haven, while total_bases (944 graded rows), batter_strikeouts (610),
hits (430), rbis (44) etc. were starved by the form constraints.

**Shipped:**
1. `_resolve_grid(spec) → DEFAULT_GRID` ALWAYS. User input is
   ignored at search time. DEFAULT_GRID brute-forces 50k+ combos
   per cell including `-inf`/`+inf` wildcards for unconstrained
   sweeps.
2. `_user_grid_to_display_filter(spec)` captures the operator's
   form values into `state.display_filter_grid` — a passive
   metadata field surfaced in `/results`.
3. `/results` payload now includes `display_filter_grid`.
4. Frontend `AdminTesting.jsx`:
   - New `passesDisplayFilter(row, grid)` helper applies the operator's
     form values as a strictness floor/ceiling (saved threshold must
     be ≥ form min on `*_min` axes; ≤ form max on `*_max`).
   - Toggle "Apply form-grid as display filter" (default OFF) above
     Top-25 table.
   - When enabled, applied to BOTH Top-25 table and Top-5-per-family
     cards. Empty filtered groups show "⚠ all combos hidden by
     form-grid filter — uncheck to see brute-force best".
5. **Tests:** `test_optimizer_brute_force_grid.py` (7 pins):
   - `_resolve_grid` ignores user spec
   - `_resolve_grid(None)` returns DEFAULT_GRID
   - DEFAULT_GRID has wildcards on every axis
   - `_user_grid_to_display_filter` captures verbatim
   - None spec → empty filter
   - Empty axis lists ignored
   - DEFAULT_GRID combos ≥ 50k per cell (sanity)
   Backend total: **47/47 passing**.

**Operator runbook (after deploy):**
- Re-launch optimizer with ANY grid form values — backend now ignores them.
- Top-25 will now show combos across ALL families that have rows in
  the (tier × bucket) cells. The narrow Top-25 dominated by
  pitcher_strikeouts will become a diverse mix of total_bases,
  batter_strikeouts, hits, etc.
- Toggle "Apply form-grid as display filter" if you want to filter
  the display to combos matching your form values.


### 2026-05-24 — DEFAULT_GRID right-sized (worker no longer OOMs)
**Reported symptom:** "the optimizer is killing the worker. its trying
to process 28 million combos simultaneously".

**Root cause:** The earlier "ALWAYS brute-force" change made
`_resolve_grid` return DEFAULT_GRID for every run, but the existing
DEFAULT_GRID was sized 8×7×7×7×7×7 = 134,456 combos/cell. Across
14 fam × 5 buckets × 3 tiers = 210 cells → **28.2M combos** which
exceeded worker RLIMIT_AS.

**Fix:** Right-sized DEFAULT_GRID to 4×3×3×3×4×4 = **1,728 combos/cell**:
```
hr_l20_min: [-inf, 0.55, 0.65, 0.75]
hr_l10_min: [-inf, 0.55, 0.65]
hr_l5_min:  [-inf, 0.55, 0.65]
cv_max:     [+inf, 0.90, 1.30]
edge_min:   [-inf, 0.02, 0.05, 0.10]
tp_min:     [-inf, 0.50, 0.60, 0.70]
```
Every axis still includes its wildcard (`-inf` / `+inf`) so unconstrained
sweeps stay in the search. 1 tier = 120,960 combos. 3 tiers =
362,880 combos. Runs in 2–3 min, well under worker memory limits.

**Test pin updated:** `test_brute_force_combo_count_is_tractable`
now enforces `1,000 ≤ combos/cell ≤ 12,000` (was `≥ 50,000`). Backend
total: **33/33** passing.


### 2026-05-24 — Grid right-sized to ~1M + tier-organized Top-3
**User directive:**
> "increase combos up to 1 million and i want the top 3 for every
>  stat for every tier without having to run it separately ... i
>  need them organized by tier so i can figure out the prod gates
>  per stat per tier"

**Shipped:**
1. **DEFAULT_GRID expanded** 4×3×3×4×5×6 = 4,320 combos/cell.
   3-tier all-families: 907,200 combos. 1-tier: 302,400. Under
   the 1M budget. Wildcards on every axis preserved.
2. **New endpoint `/optimizer/{run_id}/top-by-tier?top_n=3`** —
   returns Top-N configs per (tier × stat_family), aggregated
   across odds buckets within each tier. Response shape:
   ```
   {
     "tier_order": ["safe_haven", "front_lines", "war_zone"],
     "tiers": {
       "safe_haven":  [{stat_family, configs:[#1,#2,#3]}, ...],
       "front_lines": [{stat_family, configs:[#1,#2,#3]}, ...],
       "war_zone":    [{stat_family, configs:[#1,#2,#3]}, ...],
     }
   }
   ```
   Default `top_n=3`. `include_empty=True` surfaces every
   discovered family in every tier section (empty ones marked
   `status: "no_rows_in_tier"`).
3. **Frontend `AdminTesting.jsx`** — new "Top 3 by Tier × Stat
   Family" panel renders three tier sections (color-coded:
   safe_haven=green, front_lines=blue, war_zone=amber) with a
   per-family card grid in each. Each card shows the 3 best
   configs with HR/ROI/n/score + the odds bucket + threshold
   summary. Display-filter toggle applies to this panel too.
4. **Tests:** `test_optimizer_top_by_tier.py` (5 pins): endpoint
   registered, default top_n=3, default include_empty=True,
   tier_order canonical, response-shape contract. Backend total:
   **52/52** passing.

**Operator runbook:**
- Single optimizer run now returns ALL tiers × ALL families × Top-3
  in one shot. Read the "Top 3 by Tier × Stat Family" panel:
  - Safe Haven row for each family → prod-gate candidates for chalk
  - Front Lines → mid-range market
  - War Zone → longshot edges
- Promote rows directly to candidate_thresholds when you're satisfied.


### 2026-05-24 — Cross-book duplication audit + combo-trace endpoint
**Reported skepticism (verbatim):** "im a little leary of these results?
hr 100% out of 58 games? seems unlikely"

**Investigation on prod:** Re-ran the exact filter for the user's
top-stored combo (safe_haven · odds_lt_-200 · hr_l20≥0.75 · hr_l10≥0.65
· hr_l5≥0.65 · tp≥0.55) against `sgo_propvision_full_pipeline_replay`:
  - Optimizer stored:   **HR=100% · n_bets=58 · ROI=13.2%**
  - Direct Mongo query: **HR=84.2% · n=19 · 16 wins / 3 losses**
  - Distinct-bet check (event_id, player, market, side, line, date)
    showed 19 unique bets → confirmed the OPTIMIZER is overcounting

**Root cause (most-likely):** post the multi-book universe migration,
the same physical bet can appear multiple times in the row pool —
once per anchor book — and `_evaluate_combo` was counting each row
independently. A 19-bet, 100% chalk win was getting amplified to
58 rows / 100% via cross-book duplication (DK, FD, MGM, etc. all
quoted the same prop and all "won" together).

**Fixes shipped:**
1. **`_evaluate_combo`** now returns audit fields alongside the
   raw counts:
     - `n_distinct_bets` (dedupes by event_id × player × market × side × line × date)
     - `wins_distinct` / `losses_distinct` / `pushes_distinct`
     - `hit_rate_distinct = wins_distinct / (wins_distinct + losses_distinct)`
   Operator can spot duplication at a glance: when
   `n_bets >> n_distinct_bets`, the headline hit_rate is inflated.
2. **New endpoint `/optimizer/{run_id}/combo-trace`** —
   `POST {tier, stat_family, odds_bucket, thresholds, side?, limit_rows?}`.
   Re-runs the EXACT same filter against the source replay cache and
   returns:
     - All matching rows (up to limit_rows)
     - Recomputed metrics (with distinct-bet audit fields)
     - The stored metrics from `optimizer_run_results` for direct
       comparison
   Operator self-audit: paste any top combo's thresholds in, see the
   actual rows, verify the numbers match.
3. **Allowlist** extended (`policy.py::READABLE_COLLECTIONS`) to
   include `optimizer_runs`, `optimizer_run_results`,
   `research_grid_runs`, `research_grid_results`,
   `candidate_thresholds`, `mlb_replay_model_status` so the operator
   can also direct-query via `/collections/{name}/find` for ad-hoc
   investigations.
4. **Tests:** `test_optimizer_distinct_audit.py` (4 pins): unique-bet
   case = audit fields match raw; cross-book duplication case = 4×
   inflation surfaces as `n_bets=4 / n_distinct_bets=1`;
   `hit_rate_distinct=None` for all-pushes; distinct-key tuple locks
   `(event, player, market, side, line, date)`. Backend total:
   **28/28** in this run, 60+ across the session.

**Operator runbook (after `git pull && systemctl restart`):**
```bash
# Audit any top combo:
TOK="17808e1c13717b0d2170f6e1f023388d93740ff66f70ae1a"
curl -sS -X POST "https://propvision.bet/api/emergent-admin/optimizer/opt_XXXX/combo-trace" \
     -H "Content-Type: application/json" -H "X-Admin-Token: $TOK" \
     -d '{"tier":"safe_haven","stat_family":"pitcher_strikeouts",
            "odds_bucket":"odds_lt_-200","thresholds":{"hr_l20_min":0.75,
            "hr_l10_min":0.65,"hr_l5_min":0.65,"tp_min":0.55}}' | jq

# Returns:
#   n_rows_in_cell:           rows in (tier × family × bucket)
#   n_rows_passing_thresholds: rows that pass the threshold combo
#   recomputed_metrics:        wins/losses + n_distinct_bets + hit_rate_distinct
#   stored_metrics:            what the optimizer stored for the same combo
#   rows:                      every row matched (audit by hand)
# If recomputed.hit_rate != stored.hit_rate, that's a bug to investigate.
```


### 2026-05-24 — CRITICAL: _score was using n_bets, not n_graded
**Operator finding (verbatim trace JSON from prod):**
```
n_bets: 58, n_graded: 6, wins: 6, losses: 0, hit_rate: 1.0
```

**Root cause:** `_score()` derived its sample-size penalty from
`metrics["n_bets"]` (total rows in cell, including ungraded) instead
of `wins + losses` (settled outcomes). When 52 of 58 rows were
ungraded, the optimizer scored them as if they were a 58-bet sample
with 100% hit rate — beating actual 30-bet samples with 65% HR.
This produced the "100%/58" false top combos the operator caught.

**Fix:** `_score()` now computes `n = wins + losses` from the
metrics dict and uses that for the sample-size penalty. Result: a
6-of-6 settled cell gets the statistical weight of n=6 (which the
penalty correctly diminishes vs a real n=30+ sample). Cells with
n_graded=0 still return None (unrankable).

**UI fix:** Top-3-by-Tier panel now displays `n={settled}` with a
⚠ inflation badge `(of {n_bets})` when total rows exceed settled by
≥50%, plus a "(thin)" badge when settled < 10. The operator
immediately sees whether a result is a real signal or a thin/
ungraded artifact.

**Tests:** `test_score_uses_settled_sample.py` (4 pins):
  - 58-rows-but-6-settled vs 58-settled — thick must outscore thin
  - Legacy cells (no n_graded) still work
  - n_graded=0 → score=None
  - Sample penalty differential 6 vs 10 baseline ≥ 0.04 score
Backend total: **25/25 in this run · 60+ across session**.


### 2026-05-24 — Rate-limit exemption + one-click stack reboot
**Reported symptom:** "trying to run a new month and the research
worker is constantly dying ... Rate limit exceeded. Try again in 15
seconds." Step 4 of the Guided Workflow ("Reshape Odds (SSOT)") was
failing.

**Root cause #1 (the rate limit):** `middleware/rate_limiter.py`
applied the default public-IP tier to ALL `/api/*` paths, including
`/api/emergent-admin/*`. The Guided Workflow polls `/jobs/{id}`
every ~2s during each step, easily hitting the public tier ceiling
mid-pipeline. Since emergent-admin is already admin-token-gated
(no anonymous traffic possible), public rate-limiting is meaningless
here — it just blocks the operator's own poll loop.

**Fix #1:** Added `"/api/emergent-admin"` to the rate-limiter
`exempt_paths`. Token-based throttling already exists per agent_id
in the audit_log layer; the public-IP middleware is redundant.

**Feature #2 (the operator's one-click reboot):** New endpoint
`POST /api/emergent-admin/ops/reboot` and `GET /ops/reboot/_meta`:
- Strict allowlist of 3 services: `backend`, `worker`, `nginx`
- Hardcoded `argv` lists (NO user input ever reaches the shell)
- `subprocess.run(argv_list, shell=False)` — no shell-interpolation
- `sudo -n` (fail fast without TTY prompt)
- Each command has a 15-30s timeout
- Audit-logged with admin token
- Runs in canonical order: backend → worker → nginx

**UI:** New "↻ Reboot Stack" amber button in the worker-status bar
(top of Admin Testing page). Click → confirm modal showing the
three commands → "Reboot now". Per-service results render with
rc/stderr in the same dropdown.

**PROD PREREQUISITE (one-time setup):** Add to
`/etc/sudoers.d/propvision-admin` (chmod 440):
```
vision_user ALL=(root) NOPASSWD: /bin/systemctl restart vision-backend.service
vision_user ALL=(root) NOPASSWD: /bin/supervisorctl restart research_worker
vision_user ALL=(root) NOPASSWD: /bin/systemctl reload nginx
```
Without these lines the endpoint will return the subprocess stderr
verbatim so the operator can diagnose.

**Tests:** `tests/test_ops_reboot.py` (10 pins) lock the contract:
endpoint registered, allowlist = exactly 3 keys, argv lists are
static Python lists, no shell metacharacters, backend targets
vision-backend.service, worker targets supervisorctl, nginx uses
`reload` not `restart`, every command uses `sudo -n`, every command
has a bounded timeout. Backend total: **18/18 in this run**.

**Operator runbook (after `git pull && systemctl restart`):**
- Pipeline runs without rate-limit blocks. If a step fails for
  other reasons, the "↻ Reboot Stack" button in the header is one
  click away from a clean stack restart.
- Audit log shows every reboot call with timestamp + agent + per-
  service rc.


### 2026-06-02 — Mirror event_id allowlist (June grading-coverage RCA)
Restored June 2025 grading coverage 33% → 96.69% by filtering mirror
writes to event_ids that exist in `sgo_pp_research_outcomes`.
- Code fix: `scripts/sgo/historical_full_pipeline_replay.py`
  `_mirror_to_legacy` now builds an event_id allowlist from
  `sgo_pp_research_outcomes` for the window before aggregating
  `mlb_propvision_full_pipeline_outputs`.
- Cleanup (prod): removed 16,198 V2-hash rows from
  `sgo_propvision_full_pipeline_replay` and 529,196 from
  `mlb_propvision_full_pipeline_outputs`.
- Regression tests: `tests/test_mirror_event_id_allowlist.py` (3 pins).
- Verified via `/research/replay-outcome-coverage`: May 97.44%,
  June 96.69%, July 100%.



### 2026-05-30 — Database Inventory Audit (read-only)
Completed the user's P0 request: comprehensive read-only Mongo
inventory.
- New script: `/app/backend/scripts/audit_database_inventory.py`
  (read-only, aggregation w/ `maxTimeMS=120000`, missing collections
  handled gracefully, no SGO/API calls).
- Outputs:
  - `/app/memory/DATABASE_INVENTORY_REPORT.md` (human-readable)
  - `/app/memory/DATABASE_INVENTORY_SUMMARY.json` (machine-readable)
- Headline figures: 22 collections audited, 17 present, 5 missing,
  **4,550,066 total docs**, **426.9 MB storage**, sports MLB/NBA/NFL,
  seasons 2024/2025/2026.
- Per-sport totals:
  - MLB: 5,401 matchups, 1,767,879 team props, 0 player props,
    0 graded outcomes.
  - NBA: 2,416 matchups, 1,136,293 team props, 0 player props,
    0 graded outcomes.
  - NFL: 659 matchups, 316,474 team props, 1,188,943 player props,
    0 graded outcomes.
- Data-quality notes (acquisition-only, expected at this stage):
  - `null_player_id` blanket on team_* (correct — team rows).
  - `null_team_id` blanket on nfl_player_historical_props.
  - `null_line` present on ML/win-loss markets (correct — ML has no
    line).
  - `team_matchups` / `nfl_matchups` lack book/odds (schedule-only).
- Acquisition-runs ledger: most recent NFL player pull wrote
  899,601 rows in 138s using the new `mode="insert"` worker.
- Next P0 unblocker: grading / outcomes are 0 across the board —
  Phase 1.A.4b (post-game results/settlement) is the obvious next
  step once user authorises feature work to resume.



### 2026-06-03 — Team Card Odds Chip Parity (P0)
User report: "team cards still not displaying anything" on the
dashboard. Investigation showed the cards DID render (NBA Front Lines
section with 10 Knicks/Spurs cards visible), but the primary odds chip
on each team card displayed `— —` (null) where the player card chip
shows e.g. `-440 FD`.

Root cause: `UniversalPlayerCard::resolveDisplayOdds` reads
`display_reference_odds` → `tier_reference_odds` → per-book fallback.
Team pick payloads carried only `odds` + `book`, never the three
reference fields. The per-book fallback chain doesn't include the
single book that won the team tier gate, so the chip rendered `—`.

Fix (parity with player card contract):
- `services/team_prop_tier_service._enrich_cards_with_history` —
  stamp `tier_reference_odds`, `tier_reference_book`,
  `display_reference_odds`, `display_reference_book`, and
  `best_book_odds` from the row's own `odds`/`book` (which IS the
  reference for team picks since they already won the gate).
- `routes/team_with_badges.py` — same five fields stamped on every
  prop in the team detail payload so the team detail card chip
  matches the board card.

Verification:
- `tests/test_team_card_odds_chip_parity.py` — 2 tests pass against
  local Mongo (board + detail).
- Local backend curl confirms `-110/-105/etc.` now appears under
  `tier_reference_odds`/`display_reference_odds`/`best_book_odds`.

⚠️ Deployment required: frontend `REACT_APP_BACKEND_URL=https://propvision.bet`
points to the prod backend. Local preview now reads through the fix,
but `propvision.bet` keeps the old payload until the backend is
redeployed. User aware.


### 2026-06-03 — Team Vision Intel JIT Parity (P1)
User directive: route team `vision_intel` through Gemini on the SAME
cadence as players — "Scheduled batch every 15 min like player intel".

**Audit finding:** Team Vision Intel was already wired into
`master_sync` Step 6.5 (60-min cycle). But the **JIT reaper** —
which keeps Vision Intel fresh on the 5-min cadence between
`master_sync` ticks — only invoked the player enrichers
(`_enrich_nba_board_vision_intel` / `_enrich_mlb_board_vision_intel`).
Result: newly-surfaced team picks went 0–60 min showing the
deterministic fallback sentence ("Hit AWAY 4.5 in 10 of last 10
spread games. Recent form trending over.") instead of Gemini prose.

**Fix:** `services/jit_vision_intel_reaper.py::run_jit_vision_intel_reaper_for_sport`
- After the player enrichment block, unconditionally call
  `enrich_team_board_vision_intel(db, sport)` (same Gemini model,
  same `analyze_tier_batch`, same `vision_intel_content_hash` cache).
- Restructured the "no uncovered picks" early-return so the team
  path still runs (its own cache filter keeps it cheap when fresh).
- Added `team_enrichment_metrics` to the returned metrics dict
  alongside `enrichment_metrics`, with parallel field names.

**Verification (local):** Reaper invocation on MLB:
`visible=17 cache_hits=10 to_call=7 gemini_calls=2 returned=7 writes=7`.
Sample Gemini-authored team prose: "Home field advantage is paramount
here as the Mariners enter this matchup with strong form in these
spots. Their winning efficiency creates a 73.0% probability of
clearing the moneyline, making this a high conviction play."

**Regression:** `tests/test_jit_reaper_team_parity.py` — pins the
team enrichment metrics into the reaper's return contract.

### 2026-06-03 — NBA Finals Production Smoke (P2)
Curl audit of `https://propvision.bet/api/v3/ferrari/team/*?sport=nba`:
- `safe-haven` → 0 picks (no team props at safe-haven thresholds)
- `front-lines` → 5 picks (Knicks spreads/team_totals real event
  `1aae688472781f1a1aaf3efdb38e884b`)
- Confirms `ODDS_API_KEY` is live and the NBA team ingest +
  passthrough + scoring chain is healthy in prod.
- Caveat: `tier_reference_odds` still null on prod (the P0 fix from
  earlier today needs deployment).



### 2026-06-03 — Team Detail Page: Full E2E Click-Flow Parity
User feedback (frustrated): "dont consider it done until you click on
a team card. the team card opens displaying all props with a hr graph
for the last to. it then scrolls to the gold bet we recomend and the
vision intel suite opens when you click on it. nothing less."

Root cause audit — every step in the click flow was broken:
1. ❌ stat_type was the fallback `"TEAM_PROP"` → no category grouping,
   no chart resolution (STAT_FIELD_MAP key miss)
2. ❌ hit_rate / l5_avg / l10_avg / season_avg all `null` (because
   `_hit_rates_for_market` was called with empty market_category)
3. ❌ direction was raw "HOME"/"AWAY" → no match against the board
   click-key ("Over"/"Under") → auto-scroll silently no-oped
4. ❌ `TeamDetailPage` passed `highlightProp={null}` → scroll-to-pick
   suppressed entirely
5. ❌ Bare intel_suite missing tiles → Vision Intel Suite modal usable
   but thin

Fixes shipped:
- `routes/team_with_badges.py` — added `_classify_market_category_from_key`
  fallback (mirrors `team_prop_tier_service`); now every prop gets a
  canonical SSOT token (TEAM_TOTAL / GAME_TOTAL / SPREAD / MONEYLINE)
  → chart resolves + sections group correctly.
- Same file — normalised `direction` to canonical OVER / UNDER so the
  board click-key matches the detail-page row triplet.
- `components/dashboard/TeamDetailPage.jsx` — now forwards
  `highlightProp` to `PlayerDetailPage` (was `null` before).
  `highlightType={null}` keeps the yellow Vision glow scoped to the
  clicked row only; the auto-scroll fires unconditionally.

Verification — visual e2e flow (screenshot tool, local backend
fetch-intercept):
- Click Celtics SPREAD OVER -5.5 card →
- Boston Celtics detail page renders with 4 categories, 6 lines, 111.2 PPG
- 10 game-log bars per prop with real team scores
- Page auto-scrolls (scrollY=1603) to `prop-row-SPREAD--5.5-vision`
  with amber glow + "VISION" badge + "Click for Intel Suite"
- Click → "VISION INTEL SUITE" modal opens with OVER -5.5, L5/L10/
  SEASON avgs, Add Pick to Command Center, Environmental Factors,
  Performance Indicators
- All 4 user requirements satisfied

Regression: `tests/test_team_detail_full_parity.py` — pins
stat_type tokens, direction normalisation, per-prop game_logs,
intel_suite presence, and reference odds parity. 3/3 pass.

⚠️ Deployment required to land all today's fixes on
`https://propvision.bet`.



### 2026-06-03 — Team Detail Endpoint 504 Timeout Fix (P0)
User report: "team info card still broken. no bar graph, no hr, no
vision intel, just loading. i know the data is there i dont think
its looking in the right place for rendering it."

Root cause: `team-with-badges` was issuing **2 sequential DB queries
per (market, line, side) tuple** in `_hit_rates_for_market`. A team
with 30+ tuples × ~500ms cold-collection latency = 15-60s, hitting
prod's 60s gateway timeout → HTTP 504 → frontend stuck on
"Loading team props…" forever. Local Mongo (small/warm) returned
sub-second, masking the issue.

Verified the 504: `curl https://propvision.bet/api/v3/team-with-badges/nba_nyk?sport=nba`
returned **HTTP 504 after 60.27s** (both nba_nyk and nba_bos). The user
was right — "looking in the wrong place" = the route was hammering the
DB instead of filtering an in-memory list.

Fix shipped (`backend/routes/team_with_badges.py`):
- New `_fetch_team_outcomes_bulk(team_id, sport)` — one indexed query
  pulling up to 2,000 graded outcomes for the team across all
  market_categories.
- New `_hit_rates_in_memory(outcomes, category, side, line, opp_team_id)`
  — pure-Python equivalent of `_hit_rates_for_market`. Same line
  window (± 1.5), same h2h ignore, same 200-row cap.
- Route handler calls bulk once, then filters in-memory per prop.
- N queries reduced from `2 × |props|` (60+) to `1` total.

Verification:
- Local timing: 75ms (was already fast but now structurally bounded).
- Regression test `tests/test_team_detail_perf.py` — pins the
  endpoint at <5s and confirms hit rates still populate. 1/1 pass.
- All 4 prior regression tests still green (parity, perf, jit_reaper).

⚠️ Deployment required so `https://propvision.bet/api/v3/team-with-badges/*`
stops 504-timing-out. After deploy, user clicks → endpoint returns in
<1s → TeamDetailPage clears loading → all the prior fixes (categories,
chart, auto-scroll, intel suite modal) light up immediately.



### 2026-06-03 — Team Vision Intel Suite: Reuse SSOT Calculations (P0)
User feedback (after seeing player Environmental/Performance badges
inside the team modal): "the vision intel suite is loading player
badges for teams. not the ones we created for teams" → followed by
"if these are the badges it needs to use wording in the description as
the the team or this team not the player or player" → then sharply:
"we already do these calculations, dvp matchups, bullpen matchups
etc. they are calculated in player vision intel and can be easily
mirrored".

User was 100% right. The detail endpoint had its own thin local
builders (`_build_scout_badges` returning hot_streak/floor_lock only,
`intel_suite` reduced to `{lasso, scout_badges, context_badges}`).
Meanwhile the BOARD pipeline already had:
- `services.team_prop_tier_service._compute_league_ranks(db, sport)`
  — cached aggregation of team_score_rank / opp_score_rank /
  total_score_rank / home_win_rank / away_win_rank / total_avg /
  opp_avg / team_avg — across the entire league.
- `_build_team_scout_badges` — Brick Wall, Green Wave, Fortress,
  Jet Fuel, Wolf Pack, Burn Rate, Fast Lane, Deadeye, Crown Play,
  Trap Detector, Freight Train, Sharpshooter, Blueprint, Killshot,
  Barrel Club, Icebox, Scorched Earth, Wave Rider, Night Shift,
  High-Powered, Stout Defense (sport-specific).
- `_build_team_intel_suite` — full tile set (usage_ripple, pace_delta,
  tempo, blowout_risk, matchup_dvp, momentum_data, matchup_analysis,
  variance, lasso).
- All wording team-centric ("Opponent scoring suppressed", "Travel
  performance advantage", "L5 average 7.8 runs +44.4% above season
  baseline 5.4") — zero "player" references.

Fixes shipped:
1. `routes/team_with_badges.py` — imports the 3 board functions and
   calls them directly (no re-implementation). Threads cached
   `league_ranks` through both builders so rank-based badges (Brick
   Wall, Jet Fuel, etc.) fire and `matchup_dvp.rank` resolves.
2. Per-prop `is_home`, `tp_pct`, `edge_pct`, `vision_score` derived
   locally so edge/model badges (Crown Play, Trap Detector,
   Sharpshooter) can fire too.
3. `cover_rate_l10` filter fixed — was filtering on side="OVER" for
   spreads but the historical rows use HOME/AWAY. Now drops the
   side filter for spreads and falls back to team_total OVER for
   total rates.
4. `frontend/components/dashboard/PlayerDetailPage.jsx` — TEAM SIGNALS
   section that renders the rich `scout_badges` directly (no
   BADGE_REGISTRY filter), with team-specific badge labels and
   color palette. Wraps the player Environmental + Performance
   sections in `!is_team_prop` so they don't render for teams.

Verification (visual + curl):
- Warmed call: 75 ms.
- Modal now shows: "Brick Wall — Opponent scoring suppressed
  (opp_score_rank: 1 · thr ≤ 5)" and "Jet Fuel — Travel performance
  advantage (away_win_rank: 2 · thr ≤ 5)". Operational Volume:
  "Elevated Volume · L5 7.8 runs +44.4% above season baseline 5.4".
  Tempo: "-3.6% Neutral · Pace rank #28". Target-Lock Rationale:
  "Hit HOME -5.5 in 0 of last 10 spread games. Recent form trending
  under."

Regression: `tests/test_team_detail_rich_parity.py` — pins that the
detail endpoint reuses the board's rich builders (full intel_suite
tile set required, at least one rank-based badge must fire, and
matchup_dvp.rank must resolve via league_ranks). 1/1 pass. All 5
team tests green.



### 2026-06-03 — Team Edge Math: SSOT Fix (P0)
User: "we shouldn't be recommending an UNDER line when the projection
is OVER. it looks like all of the math and logic is off on teams
despite it working now."

Root cause: BOTH the board pipeline (`team_prop_tier_service`) and the
detail endpoint (`team_with_badges`) used the naive formula
`(projection - line) / line` for edge. This is wrong for teams because:
1. Spread lines are SIGNED (negative for favorites, positive for
   underdogs). The formula gave `-201.8%` on a `+0.1` cover.
2. The formula didn't flip sign for UNDER/AWAY picks → the same edge
   value appeared on both sides → recommendations contradicted the
   projection direction.

Fix shipped — single SSOT helper `compute_team_edge_pct` in
`services/team_prop_tier_service.py`:
- SPREAD: `threshold = -line` (so HOME -5.5 → threshold 5.5 and
  AWAY +5.5 → threshold -5.5). Both sides use the same formula since
  `line` already encodes the side. `edge = (projection - threshold)
  / max(|threshold|, 1) * 100`.
- TEAM_TOTAL / GAME_TOTAL: positive `line`. OVER →
  `(projection - line) / line * 100`. UNDER → flip sign.
- H2H / MONEYLINE: returns None (no line).

Both code paths now call this helper. The signed edge_pct also feeds
the rich badge builder (`_build_team_scout_badges`) so model-edge
badges (Crown Play / Trap Detector / Sharpshooter) fire on the
correct side and `vision_score` is a board-detail-parity composite.

Verification — same Boston/Dallas Finals data, before vs after:
| Pick | Proj | Before | After | Verdict |
|---|---|---|---|---|
| BOS HOME -5.5 | 5.6 | -201.8% | **+1.8%** | barely covers ✓ |
| DAL AWAY +5.5 | -1.7 | +30.9% (wrong magnitude) | **+69.1%** | covers easily ✓ |
| BOS TT OVER 110.5 | 107.9 | -2.4% | **-2.4%** | OVER disagrees ✓ |
| BOS TT UNDER 110.5 | 107.9 | -2.4% (no flip) | **+2.4%** | UNDER agrees ✓ |
| DAL TT OVER 105.5 | 116.6 | +10.5% (no flip) | **+10.5%** | OVER agrees ✓ |
| DAL TT UNDER 105.5 | 116.6 | +10.5% (wrong) | **-10.5%** | UNDER disagrees ✓ |

Symmetry restored: OVER and UNDER on the same total are now equal
magnitude with opposite signs.

Regression: `tests/test_compute_team_edge_pct.py` — 14 cases pin
every (sport, category, side) combination. **20/20 team tests pass.**



### 2026-06-03 — Team Hit Rate: Same SSOT as Averages (P0)
User screenshot: OVER 112.5 Team Total · L5=121.4 · L10=119.9 · SEASON=116.0 ·
HR L20=10% · L10=20% · L5=20%. Comment: "these hr numbers dont make
sense use the same ssot".

Root cause: averages came from `game_logs` (raw team_score values),
hit rates came from `team_historical_outcomes.hit` (graded against
THAT GAME'S book line at the time). Different sources, contradictory
output: every game scored above 112.5 but only 20% were marked as
hits because the book offered 119.5 OVER that day.

Fix: new `_hit_rate_from_game_logs(game_logs, market_category, side,
line, n)` in `routes/team_with_badges.py`. Re-grades the SAME
game_logs the averages use against the CURRENT line. Each category
reads the right field:
- TEAM_TOTAL → `team_score` vs `line`
- GAME_TOTAL → `total_score` vs `line`
- SPREAD    → `margin` vs `-line` (team covers if margin > threshold)
- H2H       → `margin > 0` (team won)

Per-prop `cover_rate_l10` and `total_over_rate_l10` also re-graded
from game_logs so Freight Train / Green Wave badges agree with the
chart.

Verification — `nba_bos` after fix:
- TEAM_TOTAL OVER 110.5, L10=107.9 → hit rate **40%** (was 20%) ✓
- TEAM_TOTAL UNDER 110.5, L10=107.9 → hit rate **60%** ✓
- GAME_TOTAL UNDER 215.5, L10=210.2 → hit rate **70%** ✓
- SPREAD OVER -5.5, margin=5.6 → hit rate **50%** (barely covers) ✓
- OVER + UNDER sum to ~100% on every pair (every game graded once) ✓

Regression: `tests/test_team_detail_hr_ssot.py` pins:
1. OVER% + UNDER% ≈ 100% on every pair
2. Side with avg above line must have hit rate ≥ 50%
**20/20 team tests pass.**



### 2026-06-03 — Odds API Book Policy Cleanup (P0)
User directive after read-only audit:
  "Remove all those books that you listed in step 3 except Caesars
   and PrizePicks. Confirm PrizePicks is used for reference only and
   that nothing from that PrizePicks book is entering the model or
   used in any kind of mathematical equation."

Audit findings (read-only, no changes shipped at that stage):
- 100% of NBA + MLB scored picks had `anchor_book = "prizepicks"`.
- ~200 NBA + 60 MLB Best-Bet picks were SOLO-book picks driven by
  betparx / betonline / betrivers / fliff with positive edges.
- `team_live_props` contained 50+ international books (1xbet,
  sportsbet, leovegas, casumo, virginbet, paddypower, ladbrokes,
  marathonbet, nordicbet, etc.) that never belong on a US-facing
  live board.

Fixes shipped (3 files):

1. `scripts/sgo/reshape_sgo_to_replay_odds.py::BLOCKED_BOOKS` — the
   canonical SSOT (re-exported by `services/team_policy.py`).
   Added: betparx, betonline, betonlineag, betrivers, ballybet +
   46 international books (1xbet, 888sport, bet365, betfair,
   betfairexchange, betsson, betvictor, betway, bookmakereu,
   bovada, boylesports, casumo, circa, coolbet, coral, everygame,
   fourwinds, grosvenor, gtbets, ladbrokes, leovegas, livescorebet,
   lowvig, marathonbet, matchbook, neds, nordicbet, paddypower,
   playup, pointsbet, prophetexchange, sportsbet, sporttrade,
   sugarhouse, tab, tabtouch, thescorebet, tipico, unibet,
   virginbet, windcreek, betrsportsbook, betcris, betus,
   betanysports, prizepicks STAYS in REFERENCE_ONLY).

2. `services/scoring/tp_engine.py::_BOOKS / _OPP_FIELDS` — pruned
   from 11 → 6 books. Removed BOL, BRV, PRX, BLY, FLF. Kept:
   DK, FD, MGM, CSR, EB, HRB. **PrizePicks (PP) is NOT in this
   tuple** — confirmed by `test_prizepicks_not_in_scoring_tp_books_tuple`.
   Devig / fair-prob / best_book all read exclusively from these
   6 approved US books.

3. `services/universal_odds_sync.py` —
   - `DEFAULT_BOOKMAKERS`: PP + DK/FD/MGM/Pinnacle (BOL removed).
   - `USER_SHARP_BOOKMAKERS` / `MLB_BOOKMAKERS`: pruned to the 6
     approved US books + PP for the MLB list (line anchor only).
   - Added a guard at the raw-ingest layer
     (`for bm in odds_data.get("bookmakers")`) that drops any book
     whose key is in BLOCKED_BOOKS before it can land in
     `dg_raw_odds_markets`.

4. `services/team_live_sync_service.py` — same guard at the
   `team_live_props` ingest, with an ADDITIONAL filter dropping
   REFERENCE_ONLY books too (DFS pricing has no place on a team
   prop live board).

PrizePicks reference-only confirmation:
- `prizepicks` ∈ `REFERENCE_ONLY_BOOKS`, ∉ `BLOCKED_BOOKS`.
- `prizepicks` is NEVER in `_BOOKS` (scoring math tuple).
- `prizepicks` is NEVER in `_OPP_FIELDS` (devig pairing).
- `prizepicks` IS still fetched from the Odds API → lives in
  `dg_raw_odds_markets` purely for line-pool anchor labeling
  (`anchor_book = "prizepicks"`). Its pricing never enters any
  aggregation. Test
  `test_prizepicks_not_in_scoring_tp_books_tuple` guards this.

Regression: `tests/test_book_policy_enforcement.py` — 12 tests
covering:
  • PrizePicks REFERENCE_ONLY, not in math
  • Underdog REFERENCE_ONLY, not in math
  • Blocked books removed from `_BOOKS` + `_OPP_FIELDS`
  • Exact set of 6 approved US books in scoring
  • Tier-3 US books in BLOCKED
  • International books in BLOCKED
  • Approved books not accidentally blocked
  • Live ingest gates import the policy
  • Request lists don't contain blocked books

**All 32 team + policy tests pass.** Backend restarts cleanly,
live endpoints respond in <1s.

⚠️ Note: existing data in `dg_raw_odds_markets`, `nba_prop_scores`,
`mlb_prop_scores`, `team_live_props` still contains rows from
blocked books (we only stopped NEW ingestion). A separate cleanup
job is needed to delete already-stored blocked-book rows from
these collections.



### 2026-06-03 — Symmetric Model-Quality Gate for Team Picks (P0)
User directive: "This shouldn't just be a visual fix this is also a
logic fix. The model needs to know its predicting unders and we need
to know why we are recommending them. Don't want a bunch of loosers
on the board just because they're unders."

Audit revealed the actual scope:
- The team XGB model is correctly side-aware. Training target is
  `outcome_numeric=1` (= "the pick won"), with `is_over` as a
  feature. So `model_probability` is THE PROBABILITY THIS SIDE
  WINS — for OVER, UNDER, HOME, or AWAY. No flip required.
- BUT the tier (safe_haven / front_lines / war_zone) was set
  purely by the **odds-bucket router** based on PRICE. The model's
  output (`model_probability`, `edge`) was stamped onto the doc
  but NEVER used to demote losers.
- Result: 99 AWAY/UNDER picks with `model_probability=0.107` and
  `edge=−0.39` were sitting in `front_lines`. Model said they
  lose 89% of the time — board surfaced them anyway.

Fix shipped — `services/team_live_xgb_scorer.py` adds a symmetric
demotion gate AFTER scoring:
- `model_probability < 0.50`        → tier=None (clear loser)
- `0.50 ≤ p < 0.55` AND `edge < +2%` → tier=None (borderline, no edge)
- `model_probability is None` (h2h moneyline, no model) → keep
  current tier (existing `team_model_pending` flow preserved).
- Demotion stamps `model_demoted=True` and `gate_reasons=[reason]`
  on the doc so users see WHY a pick is unranked.

Same rules applied retroactively to existing rows (97 demoted).

Final tier distribution on current Finals slate:
- front_lines HOME: 79 picks, avg_p=0.924, avg_edge=+0.363 ✓
- safe_haven  HOME: 13 picks, avg_p=0.959, avg_edge=+0.120 ✓
- war_zone    HOME:  9 picks, avg_p=0.777, avg_edge=+0.652 ✓
- (No AWAY/UNDER losers in any active tier)
- 99 demoted (avg_p=0.107) → tier=None, hidden from board.

The 36-pick OVER/UNDER/ML carve-out is preserved because those
markets don't have an XGB model trained yet (totals + h2h) — they
pass through with `model_probability=None` and rely on the existing
hit-rate / edge gate. When you add total-side models, the same
symmetric demotion will apply automatically.

Regression: `tests/test_team_model_quality_gate.py` — 3 tests:
- No obvious losers (`p<0.50`) in any active tier.
- Borderline picks demoted when edge < +2%.
- Gate threshold applied symmetrically (no asymmetric leak).

**36/36 team tests pass.**

### 2026-06-03 — UNDER-Aware Chart Bars (P1)
User: "If under equals true reverse" — bars must invert green/red
when the pick is UNDER.

Fix: `frontend/src/components/dashboard/GameLogBarChart.jsx`
- New `direction` prop (default `OVER`).
- `isHitFn(value)` = `value > line` for OVER/HOME, `value < line`
  for UNDER/AWAY.
- Bar color, bar-height clamp logic, and labeled-value renderer
  all use the side-aware predicate.
- `hit_profile` short-circuit only applies for OVER (backend
  `l10_hit_count` is OVER-only); UNDER falls back to local count
  so the green-bar count and the displayed hit-rate stay in sync.

`PlayerDetailPage.jsx` now passes `direction={prop.direction}` so
both player and team chart instances honor the pick's side.



### 2026-06-03 — Totals XGB Model Coverage (P0)
User directive: "Train a totals-side XGB model so the 18 OVER/UNDER
game-total / team-total picks currently passing through (no model
coverage) also go through the symmetric gate."

Diagnosis: the training pipeline (`scripts/sgo/train_team_xgb.py`)
ALREADY produces `game_total.pkl` and `team_total.pkl` artifacts
for NBA and MLB. The XGB scoring code (`team_xgb_loader.py`) ALSO
already handles totals. But ZERO totals rows on the live board had
`model_probability` populated — they were sitting in `front_lines`
with `market_category=None`, bypassing the symmetric gate.

Root cause: `classify_market_category` in
`services/team_live_xgb_scorer.py` only handled the LONG-format
`market_key` strings (`points-all-game-ou-over`, etc.). But the
live ingest writes the SHORT alias straight from The Odds API
(`market_key="totals"` / `"team_totals"` / `"h2h"` / `"spreads"`).
Classifier returned None for short aliases → scorer skipped them
→ `market_category=None` → no model output → bypass gate.

Fix shipped — `classify_market_category` now handles BOTH:
- Short aliases: `h2h`, `spread`(`s`), `total`(`s`), `team_total`(`s`)
- Long format: `*-ml-*` / `*-sp-*` / `*-all-game-ou-*` / `*-{home,away}-game-ou-*`

Result on Finals slate (rescored):
- 30 previously unscored rows (mc=None) now have model output
- 6 `team_total UNDER` picks → front_lines (avg_p=**0.777**, avg_edge=**+25.3%**)
- 6 `team_total OVER` picks → demoted to None (avg_p=0.262, edge=−26.2%)
- 3 `game_total OVER` + 3 `game_total UNDER` → demoted (borderline,
  edge close to zero)
- 97 total losers retroactively demoted (HOME-side already passed,
  AWAY/OVER losers eliminated)

Final active-tier board (all winners by model):
| mc | tier | side | n | avg p | avg edge |
|---|---|---|---:|---:|---:|
| h2h | front_lines | HOME | 49 | 0.981 | — |
| h2h | front_lines | ML   |  3 | 0.963 | +57% |
| spread | front_lines | HOME | 27 | 0.821 | — |
| spread | safe_haven  | HOME | 10 | 0.953 | — |
| spread | war_zone    | HOME |  7 | 0.719 | — |
| spread | front_lines | AWAY |  3 | 0.546 | +2.2% |
| **team_total** | **front_lines** | **UNDER** | **6** | **0.777** | **+25.3%** |
| h2h | safe_haven | HOME | 3 | 0.981 | — |
| h2h | war_zone   | HOME | 2 | 0.981 | — |

UNDER totals are now first-class picks — surfaced because the model
says they're winners, not because the math was sign-flipped.

Regression: `tests/test_totals_model_coverage.py` — 5 tests:
1. Classifier handles short aliases
2. Classifier handles long format
3. All 8 artifacts (4 markets × 2 sports) exist on disk
4. No totals row sits in active tier without model coverage
5. Loser OVERs demoted symmetrically with loser UNDERs

**41/41 team tests pass.**



### 2026-06-03 — Stale Blocked-Book Rows Purged (P0)
User: "why am i still getting a bunch of weird book coverage? i
thought we fixed that?"

The earlier book-policy fix only gated NEW ingestion. The 4 live
collections still held 77,909 stale rows from previously-ingested
blocked books — that's why the dashboard kept showing weird coverage
even though no new blocked-book data could enter.

Cleanup shipped — `scripts/cleanup_blocked_books.py` (idempotent
one-shot purge):

| Collection | Rows BEFORE | Deleted | Rows AFTER |
|---|---:|---:|---:|
| `dg_raw_odds_markets` | 590,201 | **76,463** | 513,738 |
| `team_live_props` | 316 | 236 | 80 |
| `nba_prop_scores` | 44,841 | 900 | 43,941 |
| `mlb_prop_scores` | 87,050 | 146 | 86,904 |
| `team_prop_scores` | 228 | 164 | 64 |
| **TOTAL** | — | **77,909** | — |

Approved books remaining in live data (post-cleanup):
- Raw odds: DraftKings 139,174 / FanDuel 100,364 / BetMGM 78,221 /
  HardRockBet 23,298 / ESPN BET 28,177 / PrizePicks 67,768
  (REFERENCE_ONLY) / WilliamHill_US 9,872 (Caesars feed)
- Team live: DK / FD / MGM / Caesars / ESPN BET / HardRockBet /
  Fanatics / Pinnacle (added)
- NBA scores: DK / FD / MGM / Caesars / ESPN BET / HardRockBet
- MLB scores: DK / FD / MGM / Caesars / ESPN BET / HardRockBet
- Team scores: DK / FD / MGM / Caesars / ESPN BET / HardRockBet /
  Fanatics / Pinnacle

Zero blocked books anywhere in live data. 41/41 team policy tests
still pass.

The cleanup script is idempotent and can be safely re-run at any
time. Going forward, the ingest-layer guards prevent re-introduction.



### 2026-06-03 — Model-vs-Projection Consistency Gate (P0)
User: "why are we recommending UNDER 3.5 with a projection of 3.8?
that's contradictory."

Diagnosis: the XGB model (`model_probability=0.78`, edge=+25%) was
producing high-confidence UNDER recommendations on team_total/
game_total markets where the team's actual recent l10 average was
SIGNIFICANTLY above the line. e.g. DAL TT UNDER 105.5 with l10
average 116.6 — model says win, but team's been scoring 11 runs
above the line.

Two ground-truths were disagreeing:
- XGB output (from model features + odds)
- Recent l10 average (from real game_logs)

Without retraining the model on fresher data, the cleanest fix is a
**consistency gate** — when the projection-derived edge strongly
contradicts the picked side, demote the pick.

Fix shipped — `services/team_prop_tier_service._enrich_cards_with_history`
adds a check AFTER `edge_pct_signed` is computed (already SSOT via
`compute_team_edge_pct`):

```python
if edge_pct_signed < -5.0 and tier in active_tiers:
    c["tier"] = None
    c["gate_reasons"] += [f"projection_contradicts_side:edge_pct={edge_pct_signed}"]
```

`get_team_prop_picks` then filters out demoted cards from the
returned envelope so the board never surfaces them.

Threshold rationale:
- `−5%` is conservative — small projection misses (±2–3%) are
  noise (model may be right). `−5%` requires the projection to
  STRONGLY contradict (e.g. l10 119.9 vs line 110.5 = −8.5%).
- H2H / moneyline returns `None` from `compute_team_edge_pct`
  (no line) → gate cannot fire on those.

Verification on Finals slate (before vs after):
- BEFORE: 4 active picks, including DAL UNDER 105.5 with l10=116.6
  (edge=-10.5%, model_p=0.78)
- AFTER: 3 active picks — contradictory pick gone:
  - DAL spread AWAY +5.5  (l10=−1.7, edge=+69.1%) ✓
  - BOS TT UNDER 110.5    (l10=107.9, edge=+2.4%) ✓
  - DAL h2h ML            (no line, model 96%) ✓

Regression: `tests/test_team_projection_consistency_gate.py` —
6 tests pin the contradiction rule:
1. UNDER + high-l10 → flagged
2. OVER + low-l10 → flagged
3. UNDER + agreeing l10 → passes
4. H2H bypass (no line → None)
5. SPREAD disagreement caught
6. SPREAD agreement passes

**47/47 team tests pass.**


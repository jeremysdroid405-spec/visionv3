# PRD — Living Document

## Problem Statement (verbatim)

Restructure React/FastAPI betting app to a 100% Local-First Database Model
with multi-sport support. Implement Google/Apple OAuth and Stripe for
payments. PRODUCT REQUIREMENTS: 100% ID-based joins. Universal Opportunity
Models and Probability modeling (ECDF / Line-Outcome Models) to fix
discrete zero-heavy props. FULL FEATURE ACTIVATION PROJECT for NBA and MLB.

## Architecture

- React frontend + FastAPI backend + MongoDB
- Local-First database: every external API call goes through a CRON-managed
  ingestion layer. Read paths are local-only.
- Universal Probability Engine (sport-agnostic, gates + thresholds in
  `services/scoring/gates/thresholds.py`)
- Three-tier output: Safe Haven / Front Lines / War Zone via odds-routed +
  gate-evaluated UniversalGateEngine.

## Active model configuration (2026-04-29)

- `NBA_RATE_BLEND_MODE = 100_0`  → PTS/PRA fully on rate × min layer
- `NBA_RFA_MINUTES_PENALTY = 0.85` → RFA picks get 0.85x expected minutes
- VK2 primary stats: AST, REB, 3PM (legacy VK still used for PTS, PRA)
- Tier routing universal across sports: ref_odds ≤ -240 → SH, -239..+149 →
  FL, ≥ +150 → WZ.

## Recent work (changelog)

### 2026-04-29 — FINAL Safe Haven Conditional Override Spec (NBA)
**Why**: Safe Haven was over-rejecting strong props. Specific picks
(Avdija PTS 19.5, M.Robinson REB 3.5, Embiid AST 2.5, etc.) had
elite vision and elite L20 hit rates but missed by tiny margins on
HR or CV. Spec required a tightly-scoped conditional override layer
that rescues HR / CV failures only — never market_structure / TP /
edge.

**Shipped**:
- `services/scoring/gates/overrides.py` (NEW) — universal,
  config-driven Safe-Haven override module. Implements 4 spec rules:
    1. **Elite Vision** (HR override): VS≥90 AND CV≤0.35 → relax
       hit_rate floor to 75.
    2. **REB / 3PM CV relax**: family ∈ {reb, threes} AND HR≥85 →
       cv cap raised to 0.60.
    3. **AST CV relax**: family=ast AND HR≥85 → cv cap raised to 0.50.
    4. **PTS dominance CV BYPASS** (CV-only): family=pts AND HR≥90 AND
       L20_avg/line ≥ 1.75 → CV failure ignored. All other gates must
       still pass.
  - Strictly HR / CV only — `_OVERRIDABLE_GATES` is frozenset.
    market_structure_gate / tp_gate / edge_gate / coverage_gate /
    vision_score_gate are NEVER touched by the override layer.
  - At most ONE rule fires per pick (vision path / stat-structure
    path / dominance path — never stacked). Verified by dedicated
    test `test_only_one_override_fires_per_pick`.
  - Audit row `__override_applied__` stamped on every rescued pick.
- `services/scoring/gates/engine.py` — single 14-line hook after the
  main gate loop. Triggered ONLY when the active config carries a
  sentinel `__safe_haven_overrides__` block; other tiers/sports see
  zero behaviour change.
- `services/scoring/gates/thresholds.py` — added the
  `__safe_haven_overrides__` config block to NBA Safe Haven only.
- `services/scoring/metrics_builder.py` — pipes `mu_recency_blend_l20`
  through `extras` (uses existing escape hatch — no new fields on
  NormalizedMetrics, no payload changes).

**Validation**:
- `tests/test_safe_haven_overrides.py` — **15 tests, all green**:
    1. Avdija PTS via vision rule ✅
    2. Robinson REB via REB CV rule ✅
    3. Castle 3PM via 3PM CV rule ✅
    4. Embiid AST via AST CV rule ✅
    5. George PTS via PTS dominance bypass ✅
    6. Random low-HR / low-vision still fails ✅
    + 9 invariance tests: market_structure never overridden, vision
      below 85 still fails when only CV bypass applies, no stacking,
      Front Lines unaffected, MLB unaffected, audit row stamped, etc.
- Full regression: **120/120 passing** across all 7 active suites.

**Live impact (post-recompute)**:
- Safe Haven candidates: 380 (ref_odds ≤ −240)
- BEFORE override: ~12 picks passing
- AFTER override: **15 picks passing** (3 rescued by stat_structure rule)
  - Mitchell Robinson REB 3.5 (HR=100, CV=0.509, VS=97.7) — REB rule
  - Joel Embiid 3PM 0.5 (HR=95, CV=0.583, VS=87.0) — 3PM rule
  - Nikola Vucevic REB 2.5 (HR=95, CV=0.487, VS=85.5) — REB rule
- Net SH tier reason distribution:
    `gate_hit_rate_fail`: 287, `gate_vision_score_fail`: 51,
    `gate_cv_fail`: 18 (was 34 — override consumed 16 CV-only fails),
    `gates_passed`: 15, `gate_market_structure_fail`: 9.

**No model state touched**: scoring formulas, μ, σ, ranking_score_v2,
tier-routing, pick-selection — all unchanged. Verified by
`git diff --stat`: only `engine.py`, `thresholds.py`,
`metrics_builder.py` modified (68 lines total, all in the gate
evaluation layer).

### 2026-04-29 — Universal Board Observability + Longevity Metrics
**Why**: With persisted `board_state` in place, surface (a) board health
(counts / fill % / churn) and (b) per-pick longevity ("on board 6h+")
to the UI and ops dashboards. Required to be sport-agnostic and to
NEVER touch publish logic.

**Shipped**:
- `services/board/publisher.py` (extended):
    * Persist `last_seen_at` on every reconcile (alongside the
      preserved `first_seen_at`).
    * New `board_state_events` collection (TTL 7 days) recording
      `insertion` / `removal` events with `(sport, tier, side,
      canonical_key, occurred_at)` for hourly churn counters.
    * `stamp_longevity_on_picks(db, sport, tier, picks)` — universal
      stamp adding `on_board_seconds`, `on_board_minutes`,
      `on_board_label` ("on board 6h+" / "3h+" / "1h+" / null).
    * `board_health_report(db)` — full per-(sport, tier, side)
      observability snapshot. Discovers sports from persisted state
      automatically — adding a new sport surfaces here with zero code
      change.
    * `_classify_status` — `healthy` / `underfilled` / `stale` /
      `high_churn`. High-churn requires actual *replacement* (≥5
      removals OR ≥3 removals + ≥3 insertions); pure first-fill is
      `healthy`/`underfilled`.
- `routes/health_sync.py` — new `GET /api/health/board` endpoint
  serving the report.
- `routes/ferrari_tiers.py` — `_serve_ferrari_tier` now calls
  `stamp_longevity_on_picks` immediately after the dashboard-card
  contract; longevity fields are present on every Safe Haven, Front
  Lines, and War Zone pick across NBA / MLB.

**Validation**:
- `tests/test_board_observability.py` — 11 tests covering all 5 spec
  proofs (A–E) + classifier + label thresholds + cross-sport (NHL):
    A. lifetime grows monotonically across 3 reconciles
    B. new pick starts at < 2 s
    C. removed pick disappears from board + records removal event
    D. Front Lines OVER ↔ UNDER full independence (state, first_seen_at,
       events untouched on the other side)
    E. health endpoint counts / capacity / fill_pct correct across
       all 4 buckets

**Live verification**:
- `/api/health/board` returns 12 buckets (NBA + MLB × 4 tier-side
  combos) with full metric set, accurate counts, and correct status
  classification.
- Sample pick payload contains `on_board_seconds: 792`,
  `on_board_minutes: 13`, `on_board_label: null` (under 1h).

**No publish logic changed**: insertion rule, ranking, scoring, gates,
tier-routing, pick-selection — all untouched. Verified by a clean run
of `tests/test_board_publisher.py` (14/14) + `test_contract_enforcer.py`
(18/18) post-change.

### 2026-04-29 — Universal Stable Board Publisher (cross-sport)
**Why**: Every API read of the tier boards used to fire a fresh top-N
sort over the volatile `{sport}_prop_scores` collection. The delta
engine rewrites those scores on every odds tick, so a small change to
`vision_score` could re-rank the entire dashboard. The user reported
"the same query 5 minutes apart returned two completely different
top-20 lists" — that's the symptom of a publish layer that has no
persistent state.

**Shipped**:
- `services/board/publisher.py` — universal stable publisher used by
  every sport (NBA, MLB, NFL/NHL future). Two modes:
    * **Fill mode** (board < capacity): full re-rank allowed.
    * **Stable mode** (board at capacity): existing picks keep their
      slots; a new candidate may enter ONLY if it outranks the current
      last pick; on entry it inserts at TRUE rank (insertion sort,
      never forced to #1).
- `board_state` collection persisted with unique index on
  `(sport, tier, side, canonical_key)`. Fields:
  `rank, first_seen_at, last_updated_at, score_snapshot, active,
  invalidation_reason`.
- Universal capacities (defined once in `TIER_CONFIG`):
    * Safe Haven: 10 (combined)
    * Front Lines: 10 OVER + 10 UNDER (split, 20 total)
    * War Zone: 10 (combined)
- Deterministic sort tuple (single source of truth):
    `ranking_score DESC → vision_score DESC → edge_pct DESC → canonical_key ASC`
- `services/board/reader.py::get_board()` transparently reconciles +
  reads from `board_state`. Public API unchanged. Falls back to legacy
  fresh-sort on any reconciliation error.
- Indexes bootstrapped at startup via `ensure_indexes(db)`.

**Validation**:
- `tests/test_board_publisher.py` — **14 tests covering all 7 spec
  cases** (A-G): SH-with-4-picks fill, FL OVER/UNDER independent fill,
  full-board insertion at TRUE rank, candidate-worse-than-last
  rejection, 5-min-apart majority-retention, zero-full-replacement,
  rank-tuple lockdown, TIER_CONFIG lockdown, and end-to-end persistence
  including a brand-new "nhl" sport reconciling with no code change.
- **Live API stability proof**: NBA Safe Haven SHA-256 identical
  across 60 s on the unfrozen production endpoint (no read locks).
- `board_state` populated correctly across all 4 active
  sport×tier×side combinations:
    NBA: SH=10, FL OVER=10/UNDER=6, WZ=9
    MLB: SH=10, FL OVER=10/UNDER=7, WZ=4

**No model state touched**: scoring, μ, σ, gates, thresholds,
tier-routing, and pick-selection are unchanged. The publisher is a
pure publish-layer reordering / persistence concern.

### 2026-04-29 — Permanent runtime contract enforcement (STRICT MODE)
**Why**: "Fixes" to NBA/MLB dashboards kept regressing because contracts
(card shape, lineup-opp row shape, hit-profile parity, ticker freshness)
lived only in code review. User requested hard runtime gates with health
counters and CI tests that fail the build.

**Shipped**:
- `services/contract_enforcer.py` — 5 validators wired into the live API:
  1. `enforce_pick_card_contract` → `_serve_ferrari_tier` (NBA + MLB,
     all 3 tiers). Drops picks missing identity/display fields. Counter:
     `invalid_pick_card_count_last_24h`.
  2. `enforce_hit_profile_parity` → same path. Rewrites stale `hit_rate`
     to empirical L10 (`l10_hit_count / l10_total`) and counts
     `hit_profile_mismatch_count_last_24h`. Locks down the Vucevic-class
     bug permanently.
  3. `enforce_lineup_opportunity_contract` → `/api/v3/mlb/vacuum/live-alerts`.
     Drops "+0 lineup spots / +0 AB" placeholder rows. Counter:
     `suppressed_lineup_opportunity_count_last_24h`.
  4. `enforce_ticker_freshness` → `/api/live/scores` (NBA + MLB).
     Drops finals + past-start scheduled games. Counter:
     `past_game_ticket_suppressed_count_last_24h`.
  5. Logo-keying violation slot reserved for future sport-collision audit.
- `GET /api/health/contracts` (in `routes/health_sync.py`) — returns the
  six 24h counters + `missing_required_card_fields_by_sport` aggregate.
  Status flips to `warning` when any counter > 0.
- TTL-24h `contract_violations` collection bootstrapped at startup
  (`server.py` startup hook).
- `tests/test_contract_enforcer.py` — 18 CI tests **fail the build** on
  regression. Includes:
    * Frozen Vucevic P+R 9.5 fixture (model 75% vs empirical 5/10 → must
      auto-rewrite to 50.0).
    * Lineup-opportunity zero-row + missing-beneficiary suppression.
    * Past-game / final ticker suppression.
    * In-play kept regardless of past commence_time.
    * 24h counter payload-shape lockdown.
    * Required-keys lockdown (PICK_CARD_REQUIRED_KEYS frozen).
    * Model-field non-mutation guarantee.

**No model state touched**: scoring formulas, μ, σ, gates, thresholds,
tier-routing, and pick-selection logic are completely unchanged.
Verified by `test_enforcer_does_not_touch_model_fields`.

### 2026-04-28 — Odds API historical backfill ACTIVATED + 30-day NBA ingest
- User provisioned `ODDS_API_KEY` in `/app/backend/.env`.
- **Two latent bugs fixed in the dormant module on first activation:**
  1. `orchestrator._iso()` — The Odds API historical endpoint rejects
     `+00:00` timezone format with `INVALID_HISTORICAL_TIMESTAMP`.
     Now appends `Z` shortcut: `.replace("+00:00", "Z")`.
  2. `client.get_historical_event_odds()` — historical responses wrap
     odds in `{"timestamp": ..., "data": {...}}`. The flattener was
     reading bookmakers from the unwrapped envelope, dropping every
     row. Client now unwraps `data` to match the live-endpoint shape
     used elsewhere.
- **Gate 2 single-slate validation PASSED** (2026-03-29, 17,903 rows,
  all 7 checks green incl. PRA, alt-line, combo, SH/WZ routing).
- **Full 30-day NBA backfill complete** — 30 slates, 3 snapshots:
  - 460,465 NBA rows in `historical_odds_full`
    (427,532 alternate-line, 154,241 combo)
  - 26 game-dates 2026-03-29 → 2026-04-26
  - 8 books (FanDuel/DraftKings/BetOnline/Fanatics/Bovada/+3)
  - 10 stat families: PTS, PRA, REB, THREES, AST, PTS_REB, PTS_AST,
    REB_AST, BLK, STL
  - 24,720 credits used (of ~108K estimate — many late-season slates
    had ≤3 games), 0 errors, 0 rate-limits, 4.31M credits remaining.
- Unblocks: backtest re-run vs `historical_odds_full` (Safe Haven /
  War Zone tiers should now route correctly with alt-line + combo
  coverage).

### 2026-05 — Persistent forward-testing system
- New collection `nba_pick_history` with unique index on
  `(player, stat, line, game_date, side)`.
- Logger hook in `services/scoring/recompute.py` fires after
  `_reevaluate_tiers_post_vision` — read-only to model behavior, idempotent
  via `$setOnInsert` on outcome fields.
- Result updater script `scripts/update_nba_pick_results.py` joins
  ungraded rows against `nba_master_hub_2026.bdl_game_logs` +
  `bdl_historical_game_logs` + `nba_player_game_logs`. CLI flags:
  `--dry`, `--since`.
- Cron scheduled at 09:35 UTC nightly (5 min after master-hub sync).
- Analytics surface in `services/forward_test/pick_history.py`:
  `query_overall`, `query_by_stat`, `query_by_tier`,
  `query_by_edge_bucket`, `query_by_availability`, `query_by_side`.
- 8/8 unit tests passing. Smoke-tested end-to-end on live data:
  650 picks graded, edge-bucket sweet spot (10–15% bucket) confirmed at
  66% win rate / +25.9% ROI.

### 2026-04-29 — Production μ + σ historical backtest
- Built `/tmp/nba_propvision_backtest_full_prod.py` with full VK2 model
  driving μ for AST/REB/3PM (was using L10-mean as proxy, gave inflated
  numbers). Result: 53.3% / +1.7% ROI on 152 picks across 20 slates.
- Confirmed: gates alone are roughly break-even; the 77.2% live forward-test
  number comes from the post-gate stack (anchor + vision intel + market
  moves + operator selection).

### 2026-04-29 — RFA × 0.85 + 100/0 rate blend promotions
- Both flags promoted to production behind env vars.
- 272-pick curated forward-test: 77.2% hit rate (Δ +17.3 pts vs pre-cutover).
- Net +47 flips, 8.8:1 misses-avoided to hits-lost asymmetry.

### 2026-04-29 — Universal Dashboard Pick Card Contract: VISUAL VERIFICATION
- Confirmed via screenshots that the 8-field contract (`player_name`,
  `team`, `stat_line`, `big_pick_text`, `projection`, `hit_rate`, `avg`,
  `short_sentence`) renders identically across NBA and MLB dashboards
  for Safe Haven, Front Lines, and War Zone tiers.
- `UniversalPlayerCard.jsx` contains zero sport-specific rendering logic.

### 2026-04-29 — Card AVG dashes ELIMINATED (universal `bdl_game_logs` backfill)
- Root cause: contract upstream only stamped `avg` from
  `season_avg`/`l20_avg`/`l10_avg`/`l5_avg`/`eb_player_career_mean` —
  none of which exist on MLB `_mlb_score_doc` or NBA combo-stat picks.
- Fix: added `_backfill_avg_from_game_logs` to
  `services/dashboard_card_contract.py`. ONE batch query against
  `{sport}_master_hub_2026.bdl_game_logs` (the same source the player-
  detail page reads), then computes L10 mean per pick using a
  stat-type → log-field map covering NBA primaries + combos
  (`P+A`, `P+R`, `P+R+A`, `R+A`, `S+B`) and MLB batter/pitcher stats.
- Same pass also backfills `team` from `master_hub.team_abbr` when
  picks arrive with a null team identity.
- Verified with `sort=gap` (the param the dashboard uses): 0/43 picks
  missing avg or team across all 6 sport-tier combos.
- Permanent: pure read-side normalizer, no model/gate touch, idempotent.
  Survives sync rebuilds because the contract runs on every API
  response (it does not write to `mlb_prop_scores` / `nba_prop_scores`).

### 2026-04-29 — Team chip on every Pick Card (compact mode)
- `UniversalPlayerCard.jsx` compact-mode header now renders a
  monospace team-abbr chip (e.g. `TOR`, `PHI`, `BAL`) right next to the
  player name. Reads the contract `team` field — sport-agnostic.

### 2026-04-29 — MLB Live Injury Advantage: re-routed to universal engine
**Symptom**: NBA "Live Injury Advantage" populated; MLB equivalent
permanently empty even with 165 normalized MLB injuries on disk.
**Fix**: rotation gate parity (`bdl_game_logs_count` fallback) +
re-routed `/api/v3/mlb/vacuum/live-alerts` to call universal
`compute_injury_advantages(_db, "mlb")`.

### 2026-04-29 — MLB Injury Context End-to-End Plumbing (extended)
**Audit found 4 broken layers**:
1. `feature_hydration._build_injury_summary` — `OUT_STATUSES` only
   knew NBA codes; MLB's `IL_SHORT/IL_STANDARD/IL_EXTENDED` and
   `DAY_TO_DAY` fell through → every MLB team reported 0 outs.
2. `mlb_scoring.score_props` — never copied `team_injury_context` to
   the score row (NBA already did this).
3. `mlb_vision_intel._build_batch_prompt` — Gemini prompt had no
   injury field → AI never mentioned injury impact for MLB.
4. `injury_triggered_rescore._on_event` — hard-bailed on every
   non-NBA event; MLB injury_change never triggered a recompute.
**Fix**: surgical plumbing at all 4 layers (no scoring/gates/μ/σ
touched). Sport dispatch via `_VERSION_TAG_BY_SPORT` and per-sport
adapter selection in the rescore worker. Live-injuries hydration
re-run produced 12,889/14,320 mlb_live_props rows with
`team_injury_context.out_count > 0`. Recompute produced 5,967/6,290
final-mlb-rt rows carrying `injury_context`. End-to-end trace:
HOU's Christian Walker pick now exposes `team_out_count: 10,
out_players: [Jake Meyers, Tatsuya Imai, Hunter Brown, ...]`.
NBA `final-nba-rt`: 3,859/3,859 still have injury_context
(no regression).

### 2026-04-29 — Live Scores Ticker filter contract
**Symptom**: ticker showed yesterday's final games and games whose
commence_time was already past.
**Fix** (both `/api/live/scores` paths — NBA via BDL `/box_scores/live`
and MLB via BDL `/mlb/v1/games`): drop rows where `status_code == 3`
(Final) or `commence_time < now (UTC)`, except in-play (`status_code
== 2`). All timestamps normalized via
`datetime.fromisoformat(...).replace(tzinfo=timezone.utc)` so
frontend and backend share one base. NBA: 3 upcoming games kept.
MLB: 11 upcoming/in-play games kept. Zero finals on either ticker.

**Root cause** (audit, two layers):
1. `/api/v3/mlb/vacuum/live-alerts` was wired to the legacy
   `MLBInjuryVacuumService` which refetched BDL/ESPN on demand and
   filtered against hardcoded `MLB_STAR_PROFILES` /
   `MLB_BENEFICIARY_MAPPINGS`. It bypassed the canonical
   `injuries_normalized` collection entirely.
2. The universal `compute_injury_advantages` engine's rotation-gate
   `_is_rotation_relevant` for MLB only read `hub.games_played`, which
   `mlb_master_hub_2026` populates on **only 12% of records** —
   fail-closed for the other 88% (incl. Mookie Betts, Juan Soto).
   For NBA the equivalent `advanced_stats.games_played` is on 98%.

**Fix** (plumbing only, no thresholds/scoring/UI touched):
- `services/injury_advantage.py::_is_rotation_relevant` — for MLB,
  also accept `bdl_game_logs_count` / `total_game_logs` as the
  rotation-recency signal. Same `MIN_GP_FOR_VACUUM = 5`.
- `routes/mlb_vacuum.py::live-alerts` — body replaced to call
  `compute_injury_advantages(_db, "mlb")` (same engine NBA uses) and
  reshape rows to the dashboard's legacy field names
  (`injured_team`, `time_ago`, `is_late_scratch`, etc.).
  Provenance flag `engine: universal_injury_advantage` in payload.

**Validation**: bumped Raisel Iglesias (ATL, IL_STANDARD) status_changed_at
to NOW → universal engine returns 10 beneficiary alerts with all legacy
UI fields populated (Austin Riley +5 min, Dominic Smith +3.5 min, etc.).
NBA payload unchanged (6 alerts as before).

### 2026-04-29 — Universal Sport-Aware Team Logo Contract
**Symptom**: NBA cards rendered MLB team logos for BOS, ATL, CLE, DET,
HOU, MIA, MIL, MIN, PHI, TOR (and vice-versa) — every league-collision
abbreviation was wrong on one side.

**Root cause**: `frontend/src/components/dashboard/constants.js`
exported `TEAM_LOGOS = { ...NBA_TEAM_LOGOS, ...MLB_TEAM_LOGOS }`. The
JS spread merged the dicts; on collisions MLB ALWAYS won the key (last
spread takes precedence). All callers — `Dashboard.jsx::PlayerHeadshot`,
the live-scoreboard logo `<img>`s, and `PlayerDetailPage.jsx` — keyed
this dict by `team` only, with no sport context. NFL/NHL/Soccer would
have collided too (CAR, NY, LA, SF, ARI).

**Fix** (sport-aware logo lookup — pure plumbing, no scoring touched):
- `constants.js`:
  - Removed cross-sport `TEAM_LOGOS` spread.
  - Added `NFL_TEAM_LOGOS`, `NHL_TEAM_LOGOS`, `SOCCER_TEAM_LOGOS`
    placeholder maps + a `_LOGO_MAPS` registry keyed by sport.
  - Exported `getTeamLogo(sport, team, team_logo_url=null)` —
    resolution: backend `team_logo_url` → sport-keyed map → null.
- `Dashboard.jsx`: `PlayerHeadshot` accepts `sport` + `teamLogoUrl`
  props; live-scoreboard logos read `getTeamLogo(game.sport ||
  currentSport, team)`; all 3 `<PlayerHeadshot>` callsites pass sport.
- `PlayerDetailPage.jsx`: same sport-aware refactor.
- `UniversalPlayerCard.jsx`: removed inline duplicate NBA/MLB dicts,
  imports the shared `getTeamLogo`. Card already had `playerSport`
  derived from `player.sport`.
- Backend `services/dashboard_card_contract.py`: every pick stamped
  with `sport: 'nba' | 'mlb'` so the universal contract carries the
  routing key the frontend needs.

**Validated** (live preview, demo mode):
- NBA dashboard: 25/25 logos resolve to `cdn.nba.com/...`.
  BOS → Celtics, ATL → Hawks, TOR → Raptors, DET → Pistons. **0 MLB logos**.
- MLB dashboard: 25/25 logos resolve to `espncdn.com/.../mlb/...`.
  BOS → Red Sox, ARI → Diamondbacks, DET → Tigers, LAA → Angels,
  WSH → Nationals. **0 NBA logos**.
- Future sports already routed: NFL CAR will show Panthers, NHL CAR
  will show Hurricanes (when those maps are populated).


**Root cause**: `frontend/src/components/dashboard/constants.js`
exported `TEAM_LOGOS = { ...NBA_TEAM_LOGOS, ...MLB_TEAM_LOGOS }`. The
JS spread merged the dicts; on collisions MLB ALWAYS won the key (last
spread takes precedence). All callers — `Dashboard.jsx::PlayerHeadshot`,
the live-scoreboard logo `<img>`s, and `PlayerDetailPage.jsx` — keyed
this dict by `team` only, with no sport context. NFL/NHL/Soccer would
have collided too (CAR, NY, LA, SF, ARI).

**Fix** (sport-aware logo lookup — pure plumbing, no scoring touched):
- `constants.js`:
  - Removed cross-sport `TEAM_LOGOS` spread.
  - Added `NFL_TEAM_LOGOS`, `NHL_TEAM_LOGOS`, `SOCCER_TEAM_LOGOS`
    placeholder maps + a `_LOGO_MAPS` registry keyed by sport.
  - Exported `getTeamLogo(sport, team, team_logo_url=null)` —
    resolution: backend `team_logo_url` → sport-keyed map → null.
- `Dashboard.jsx`: `PlayerHeadshot` accepts `sport` + `teamLogoUrl`
  props; live-scoreboard logos read `getTeamLogo(game.sport ||
  currentSport, team)`; all 3 `<PlayerHeadshot>` callsites pass sport.
- `PlayerDetailPage.jsx`: same sport-aware refactor.
- `UniversalPlayerCard.jsx`: removed inline duplicate NBA/MLB dicts,
  imports the shared `getTeamLogo`. Card already had `playerSport`
  derived from `player.sport`.
- Backend `services/dashboard_card_contract.py`: every pick stamped
  with `sport: 'nba' | 'mlb'` so the universal contract carries the
  routing key the frontend needs.

**Validated** (live preview, demo mode):
- NBA dashboard: 25/25 logos resolve to `cdn.nba.com/...`.
  BOS → Celtics, ATL → Hawks, TOR → Raptors, DET → Pistons. **0 MLB logos**.
- MLB dashboard: 25/25 logos resolve to `espncdn.com/.../mlb/...`.
  BOS → Red Sox, ARI → Diamondbacks, DET → Tigers, LAA → Angels,
  WSH → Nationals. **0 NBA logos**.
- Future sports already routed: NFL CAR will show Panthers, NHL CAR
  will show Hurricanes (when those maps are populated).


**Symptom**: NBA "Live Injury Advantage" populated; MLB equivalent
permanently empty even with 165 normalized MLB injuries on disk.

**Root cause** (audit, two layers):
1. `/api/v3/mlb/vacuum/live-alerts` was wired to the legacy
   `MLBInjuryVacuumService` which refetched BDL/ESPN on demand and
   filtered against hardcoded `MLB_STAR_PROFILES` /
   `MLB_BENEFICIARY_MAPPINGS`. It bypassed the canonical
   `injuries_normalized` collection entirely.
2. The universal `compute_injury_advantages` engine's rotation-gate
   `_is_rotation_relevant` for MLB only read `hub.games_played`, which
   `mlb_master_hub_2026` populates on **only 12% of records** —
   fail-closed for the other 88% (incl. Mookie Betts, Juan Soto).
   For NBA the equivalent `advanced_stats.games_played` is on 98%.

**Fix** (plumbing only, no thresholds/scoring/UI touched):
- `services/injury_advantage.py::_is_rotation_relevant` — for MLB,
  also accept `bdl_game_logs_count` / `total_game_logs` as the
  rotation-recency signal. Same `MIN_GP_FOR_VACUUM = 5`.
- `routes/mlb_vacuum.py::live-alerts` — body replaced to call
  `compute_injury_advantages(_db, "mlb")` (same engine NBA uses) and
  reshape rows to the dashboard's legacy field names
  (`injured_team`, `time_ago`, `is_late_scratch`, etc.).
  Provenance flag `engine: universal_injury_advantage` in payload.

**Validation**: bumped Raisel Iglesias (ATL, IL_STANDARD) status_changed_at
to NOW → universal engine returns 10 beneficiary alerts with all legacy
UI fields populated (Austin Riley +5 min, Dominic Smith +3.5 min, etc.).
NBA payload unchanged (6 alerts as before).

## P0 / P1 / P2 backlog

### P0
- AST stat-level gate tightening (41.9% historical, lossy at population).
- Investigate 15%+ edge bucket inflation (54% win rate vs 66% in 10–15%).
- 0 Safe Haven historical picks — verify gate is reachable in practice.
- Decision: PRA / FULL_GO over-projection residual fix.

### P1
- 7-day shadow forward-test for War Zone recalibration.
- Recalibrate MLB Front Lines gates (blocked on user thresholds).
- PP-only stat-families TP calculation fix (PrizePicks hardcoded -137).
- **Re-run `/tmp/nba_propvision_curated_v3.py` against the freshly
  populated `historical_odds_full` (460k rows, 26 dates) to surface
  Safe Haven / War Zone tier hits the legacy `historical_odds`
  collection couldn't.**
- Backfill historical game logs to close 2025-07 → 2026-02 coverage gap
  (would roughly double the historical replay sample).

### P2
- Emergent Google Auth + Stripe payments.
- `Dashboard.jsx` prop-drilling refactor.
- Cross-line synthetic UNDER pairing for alt-market devig.

### Future / Backlog
- NFL config scaffold for Universal Probability Engine.
- STL/BLK/Double-Double model training.
- Per-minute VK retrain (parked — user explicitly said not yet).

## Mocked / known limitations

- PrizePicks odds hardcoded at -137 placeholder (P1 to fix).
- `historical_odds` covers standard markets only — no alt lines, no PRA,
  no STL/BLK in the historical replay set.
- Live `team_total` and `sharp_implied` not stored historically; VK
  predictions silently default to baseline (115 / 50%) for those.

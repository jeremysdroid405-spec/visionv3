# Changelog

## 2026-04-25 — Vision Intel / Gemini Wired Back in (Active Board Only, Capped)

**Goal:** All Vision Intel summaries shown to users were deterministic fallback templates from `_generate_vision_fallback` (e.g. *"Jalen Duren is hammering PTS at an 85% L10 over clip — projection of 20.2 sits above the 11.5 line for a +8.7 edge to ride."*). Gemini producer (`VisionIntelService.analyze_tier_batch`) was functional but completely unwired since the 2026-04-22 consolidation deleted UnifiedPipeline / optimized_sync_engine. `scheduled_hourly_vision_intel_sync` was defined in `server.py` but never registered with APScheduler. Coverage of real Gemini text on the slate: 0 / 3,829 score docs.

**Approved scope (Option A, narrow):** Wire Gemini back into `master_sync.py` as Step 6, restricted to active board picks only (Safe Haven + Front Lines + War Zone) with content-hash cache and 75-pick cap. Pure UI decoration.

**Files changed (3, exactly per the directive's "tiny passthrough" allowance):**

1. **`backend/services/master_sync.py`** — added Step 6 hook + helper `_enrich_nba_board_vision_intel` (~200 LOC). Behavior:
   - Pulls `nba_prop_scores` where `version_tag=final-nba-rt`, `tier ∈ {safe_haven, front_lines, war_zone}`, `active=True`.
   - Computes `_vision_intel_content_hash(pick)` (existing helper from `routes/ferrari_tiers.py`); skips picks whose stored hash matches and `vision_intel` is non-empty (cache hit).
   - Caps remaining new/changed picks at `MAX_BOARD_VISION_INTEL_PICKS=75`. Priority order when capped: `war_zone → front_lines → safe_haven`.
   - Groups by tier, calls `vis.analyze_tier_batch(picks, tier_name, strict=True)` once per tier.
   - Persists `vision_intel`, `vision_intel_content_hash`, `vision_intel_generated_at` onto matching `nba_prop_scores` docs (keyed by `canonical_key`).
   - Mirrors `vision_intel` into `nba_cached_board.props[*]` via `arrayFilters` keyed by `(stat_type, line, direction)`.
   - Returns metrics: `board_picks_total, cache_hits, cache_miss_to_call, after_cap_to_call, tiers_called, gemini_returned, gemini_empty_or_failed, score_docs_written, cached_board_writes, fallback_in_db_after`.

2. **`backend/routes/ferrari_tiers.py`** — single-block (~12 LOC) passthrough in `_merge_score_with_board` so `score.get("vision_intel")` / `vision_intel_content_hash` / `vision_intel_generated_at` flow into the pick payload when `nba_cached_board` lacks a matching line entry (slate drift). Score-side text wins only when present; cached_board overlay can still set it via the existing path.

3. **`backend/routes/player.py`** — three lines added to `_score_to_prop` whitelist: `vision_intel, vision_intel_content_hash, vision_intel_generated_at`. Same rationale as the Step 4 momentum_data passthrough.

**Did NOT touch (per directive):**
Scoring · gates · UniversalGateEngine · thresholds · ECDF · recompute · odds sync · frontend · `_enrich_under_picks_with_gemini` (orphaned, left untouched).

**Verification (live, 2026-04-25):**

| Metric | Before | After (run 1) | After (run 2) |
|---|---|---|---|
| `nba_prop_scores` board picks with `vision_intel` populated | 0 / 163 | 75 / 163 (cap binding) | **148 / 148 active board** |
| `vision_intel_content_hash` populated | 0 | 75 | 148 |
| `vision_intel_generated_at` populated | 0 | 75 | 148 |
| Cache hits on 2nd run | n/a | n/a | **140** (FL+WZ from run 1 short-circuited) |
| Gemini calls | 0 | **2** (1 per tier × 2 tiers: WZ 15 + FL 60) | **1** (just SH 8) |
| `gemini_returned / selected` per tier | n/a | WZ 15/15, FL 60/60 | SH 8/8 |
| Coverage of `/api/v3/ferrari/{safe-haven,front-lines,war-zone}` payloads | gemini=0/24, fallback=24/24 | WZ 7/7 + FL 10/10 + SH 0/7 (SH capped out) | **WZ 7/7 + FL 10/10 + SH 7/7 = 24/24 Gemini** |
| Score doc `vision_intel_content_hash` populated on Ferrari payload | 0/24 | n/a | **24/24** |
| Cap held | n/a | 75 | 8 (well under 75) |
| Ferrari tier counts (gating signal) | safe-haven=7, front-lines=10, war-zone=7 | unchanged | unchanged ← scoring untouched |

**Sample Gemini outputs (real, not template):**
- Reed Sheppard PTS 16.5 UNDER: *"High volatility metrics with a CV of 0.53 make the under a solid play here. Regression is expected as he struggles to maintain this high level of scoring output."*
- VJ Edgecombe REB 8.5 UNDER: *"Minutes are capped for his rotation, which will inherently limit his rebounding opportunities. The under is the logical choice here."*
- LeBron James P+A 33.5 UNDER: *"Aging legs and a tight rotation suggest his production will dip tonight. The under is the sharp move for this veteran stud."*
- Jalen Duren PTS 11.5 OVER: *"L10 hit rate confirms he is finding his rhythm and should find easy buckets against this interior rotation. Ride the hot hand while he continues to hunt points inside."*

**UI validation (Playwright, Jalen Duren detail):**
- TARGET-LOCK RATIONALE renders genuine Gemini text (verified no fallback fingerprints: `is hammering` / `sits above the` / `to ride` count = 0 on page)
- "Powered by Vision Intel" badge present
- TEMPO panel still rendering Step 5 output ("-1.2 Possessions / Standard Tempo")
- DEFENSIVE MOMENTUM still rendering Step 4 output (SZN/L10/L5/Composite)
- Bar charts, hit rates, header stats — all unchanged



**Goal:** `intel_suite.pace_delta` was stale legacy data (every team showing flat `team_pace=98.0, opp_pace=98.0, tempo_label="Neutral Pace", display="0.0"`) written by deleted `optimized_sync_engine` / `cached_board_builder_service` ≈2026-04-21. Coverage of the stale signature: 122 cached_board docs. Score docs had no `intel_suite.pace_delta` at all. PlayerDetailPage TEMPO panel showed "0.0 / Neutral Pace" for every player.

**Approved scope (Option A only):** Wire `IntelSuiteCalculator._calculate_pace_delta` back into `master_sync.py` as Step 5 read-side enrichment. Pure UI decoration — does not affect projections, gates, ECDF, tiers, recompute math, or thresholds.

**Files changed (1):**
- **`backend/services/master_sync.py`**
  - Added Step 5 hook (NBA-only) after Step 4 momentum: calls `_enrich_nba_pace_delta(db)` and reports metrics under `steps.5_pace_delta_enrichment_nba`.
  - Added inline helper `_enrich_nba_pace_delta` (~170 LOC) that:
    - Builds `event_id → (home_abbr, away_abbr)` and `team_abbr → today_opponent_abbr` maps from `nba_live_props`.
    - Builds `bdl_player_id → team_abbr` map from `nba_master_hub_2026`.
    - Walks `nba_prop_scores` at `version_tag=final-nba-rt`, derives `(team_abbr, opponent_abbr)` per doc, dedupes by pair.
    - Calls `IntelSuiteCalculator._calculate_pace_delta(team, opp, None)` once per pair (pure synchronous static-table lookup, no I/O).
    - Bulk-writes `{$set: {"intel_suite.pace_delta": pd}}` onto matching score docs (creates `intel_suite` sub-doc when absent — no other intel_suite fields touched).
    - Mirrors line/direction/stat-agnostically into `nba_cached_board.props[*].intel_suite.pace_delta` via `arrayFilters` (one update per cached_board player doc).
  - Reuses the `_NBA_TEAM_NAME_TO_ABBR` and `_to_team_abbr` constants added in the momentum step.

**Did NOT touch (per directive):**
Scoring · gates · UniversalGateEngine · thresholds · ECDF · recompute · Ferrari tier logic · `_score_to_prop` whitelist · frontend · `routes/player.py` overlay (already passes `intel_suite` through via `_BOARD_ENRICHMENT_FIELDS`).

**Verification (live, 2026-04-24):**

| Metric | Before | After |
|---|---|---|
| `nba_cached_board` docs with stale `tempo_label="Neutral Pace"` | 122 | **13** (remaining = stale-slate players not playing today) |
| `nba_prop_scores` with `intel_suite.pace_delta` @ final-nba-rt | 0 / 3,829 | **3,829 / 3,829 (100%)** |
| `_enrich_nba_pace_delta` metrics | n/a | `props_total=3829, props_enriched=3829, props_skipped=0, pairs_total=16, pairs_computed=16, pairs_failed=0, cached_board_updates=109, cached_board_skipped=13` |
| Jalen Duren PTS 11.5 OVER `intel_suite.pace_delta` (`/player-with-badges`) | `{display:"0.0", tempo_label:"Neutral Pace", expected_game_pace:"98.0"}` | `{possessions:-1.2, display:"-1.2 Possessions", pace_factor:1.0, tempo_label:"Standard Tempo", team_pace:96.0, opponent_pace:96.5, expected_game_pace:96.2}` |
| Ferrari tier counts (gating signal) | safe-haven=7, front-lines=10, war-zone=7 | unchanged (7 / 10 / 7) ← scoring untouched |
| Per-tier fresh pace coverage on Ferrari payload | n/a | safe-haven 7/7, front-lines 8/10, war-zone 7/7 |

**UI validation (Playwright on preview, Jalen Duren detail):**
- TEMPO panel: **"-1.2 Possessions / Standard Tempo"** (current per-team value)
- VARIANCE: 65% Variable (unchanged, fresh)
- DEFENSIVE MOMENTUM: SZN #15 / L10 #5 / L5 #6 / Composite #10 (current, from Step 4)
- ACTIVE FOR: HOME COOKIN' badge (current)
- 0 "Neutral Pace" sightings, 1 "Standard Tempo" rendered
- Bar charts, hit rates, header stats, target-lock rationale all unchanged



**Goal:** `momentum_data` was orphaned since the legacy `optimized_sync_engine` was deleted on 2026-04-22. Coverage was 8/5,754 cached props (0.14%) and 0/3,731 score docs. PlayerDetailPage `MomentumTrackerFull` could not render for Jalen Duren PTS 11.5 OVER (or 99.86% of the slate).

**Approved scope (Option 1 only):** Wire the existing `DefensiveMomentumService.calculate_momentum_modifier` back into `master_sync.py` as a read-side enrichment Step 4. Pure UI decoration — does not affect projections, gates, ECDF, tiers, recompute math, or thresholds.

**Files changed (3):**
1. **`backend/services/master_sync.py`**
   - Added Step 4 hook (NBA-only) after scoring (Step 3): calls `_enrich_nba_momentum(db)` and reports metrics under `steps.4_momentum_enrichment_nba`.
   - Added inline helper `_enrich_nba_momentum` (~140 LOC) that:
     - Builds `event_id → (home_abbr, away_abbr)` and `team_abbr → today_opponent_abbr` maps from `nba_live_props`.
     - Builds `bdl_player_id → team_abbr` map from `nba_master_hub_2026`.
     - Calls `momentum.ensure_cache()` once (loads/builds the existing `defensive_momentum_cache` Mongo collection).
     - Walks `nba_prop_scores` at `version_tag=final-nba-rt`, derives canonical `(opponent_abbr, stat_family)` per doc, dedupes by pair, computes `momentum_data` once per pair, and bulk-writes via `UpdateMany` keyed by `canonical_key`.
     - **Mirrors** `momentum_data` into `nba_cached_board.props[*]` using `arrayFilters` keyed by `stat_type` (line-and-direction-agnostic — momentum_data depends only on `(opp, stat)`). Resolves opponent from `team_to_opp_today` (NOT cached_board's stale `event_id`).
     - Adds new `_NBA_TEAM_NAME_TO_ABBR` and `_STAT_FAMILY_ALIAS` constants for translation.
2. **`backend/routes/player.py`**
   - One-line addition to `_score_to_prop` whitelist: `"momentum_data": doc.get("momentum_data")`. Required because score docs now carry the field but the read function was stripping it. This is the "any small helper if absolutely needed" exception — without it, score-doc-only paths (where cached_board lacks the line, e.g. Duren PTS 11.5) cannot surface momentum_data to the UI.

**Did NOT touch (per directive):**
Scoring · gates · UniversalGateEngine · thresholds · ECDF / probability layer · recompute · Ferrari tier logic · `_score_to_prop` field semantics for scoring fields · `defensive_momentum_cache` UI usage · routes_archive · Tank01 fallbacks.

**Verification (live, 2026-04-24):**

| Metric | Before | After |
|---|---|---|
| `nba_prop_scores` momentum_data coverage @ final-nba-rt | 0 / 3,731 | **3,731 / 3,731 (100%)** |
| `nba_cached_board.props[*]` momentum_data coverage | 8 / 6,005 | **5,322 / 6,005 (88.6%)** — remainder is stale-slate players |
| `/api/v3/player-with-badges/Jalen%20Duren` momentum_data per prop | 0 / 53 | **53 / 53 (100%)** including PTS 11.5 OVER (composite=10.2, season=15, l5=6) |
| `/api/v3/ferrari/safe-haven` picks with momentum_data | 1 / 7 | **4 / 7** (gap is line-drift between cached_board & pick lines — not in scope) |
| Ferrari tier counts (gating signal) | safe-haven=7, front-lines=10, war-zone=7 | unchanged (7 / 10 / 7) ← scoring untouched |
| `_enrich_nba_momentum` metrics | n/a | `props_total=3731, props_enriched=3731, props_skipped=0, pairs_total=159, pairs_computed=159, pairs_failed=0, cached_board_updates=1579, cached_board_skipped=13` |

**UI validation (Playwright, 5 fresh clicks):**
- Jalen Duren · Nikola Jokic · James Harden · Stephon Castle · Donovan Clingan
- **5/5 detail pages render `MomentumTrackerFull`** with full SZN/L10/L5 ranks + Composite Rank
- **0 `matchup-dvp-fallback` boxes** rendered (rule from prior commit holds)
- 5/5 Vision Intel Suite intact (Environmental Factors + Performance Indicators + Target-Lock Rationale)
- Bar charts, hit rates, header stats, Add Pick to Command Center button — all unchanged



**Audit identified two surgical fixes:**

### Fix A — `momentum_data` overlay through `/player-with-badges`
**File:** `backend/routes/player.py` (1 line)
- Added `"momentum_data"` to `_BOARD_ENRICHMENT_FIELDS` tuple.
- Pure projection of the existing `nba_cached_board.props[*].momentum_data` field through the existing line-level overlay path (lines 376–387).
- **Did not touch:** `_score_to_prop`, scoring, gates, ECDF, Ferrari tier logic, dvp_service, intel_suite_calculator, defensive_momentum_service. The cached_board is already populated by upstream scoring; this just stops dropping the field on read.
- **Verified:** `curl /api/v3/player-with-badges/Nikola%20Jokic` now returns `momentum_data` populated for Jokic AST 7.5 (composite_rank=6.9, season_rank=5, l5_rank=8). Coverage = 1/51 props (rest are upstream cached_board gaps, not this overlay's scope).

### Fix B — Prefer `bdl_game_logs` when richer (no Tank01-shape fallbacks)
**File:** `frontend/src/components/dashboard/PlayerDetailPage.jsx` (~6 LOC inside the existing master-hub secondary fetch handler, lines ~628–660)
- When master-hub returns BOTH `game_logs` (Tank01-shape) and `bdl_game_logs` (BDL-shape), prefer `bdl_game_logs` if `game_logs[0]` lacks usable opponent identity (no `opponent_team_id`, no `matchup`, empty `opp`) OR `bdl_game_logs.length > game_logs.length`.
- BDL logs carry `opponent_team_id` (resolved by existing `TEAM_ID_TO_ABBR` map in `GameLogBarChart`). No new fallback fields, no Tank01 patches.
- **Did not touch:** `GameLogBarChart` opponent-resolver semantics. Reverted the speculative `game.opp` / `game.home` / `.toUpperCase()` additions — selection happens upstream where it should.

### Files changed (final, total)
- `backend/routes/player.py` — 1 addition to `_BOARD_ENRICHMENT_FIELDS`
- `frontend/src/components/dashboard/PlayerDetailPage.jsx` — secondary-fetch handler enhanced to choose richer game-log array
- `frontend/src/components/dashboard/GameLogBarChart.jsx` — REVERTED to pre-session state (speculative `game.opp` / `game.home` removed)

### Validation (Playwright on preview, 5 fresh clicks)
- 5/5 detail pages render bar charts and Vision Intel Suite
- **0 "???" labels across all 5 detail views** (Fix B working — Amen-class players whose `game_logs` array is empty now read `bdl_game_logs` with `opponent_team_id`)
- 1 `momentum-tracker-full` instance (Jokic — Fix A working; the only player on slate with cached_board momentum_data)
- 4 `matchup-dvp-fallback` instances (current `intel_suite._calculate_matchup_dvp` source — not legacy)
- 0 legacy "DEFENSIVE MOMENTUM" header text sightings
- 0 "No game data" placeholders
- No 404s



**User report:** Clicked detail view was using stale/legacy badge sources;
defensive momentum appearing "wrong/legacy"; badges not populating.

**Root cause trace (backend + frontend, read-only where instructed):**

| Signal | Current SoT (Ferrari tier payload @ root) | `/player-with-badges` response | Frontend behavior BEFORE fix |
|---|---|---|---|
| `momentum_data` | Full 22-key object (scoring output from `services/defensive_momentum_service.py`) | `None` on every prop | Merge did NOT copy → `MomentumTrackerFull` never rendered |
| `scout_badges` | list @ root | `None` @ root | Merge included this field (OK) |
| `context_badges` | list of `{badge_key, display, icon, color, description}` @ root | list (same) | `intel_suite.context_badges` read only — MISSED root copy |
| `active_badges` | `None` (not set) | `None` | Not merged |
| `intel_suite.defensive_momentum` | **not emitted** (legacy) | **not emitted** | UI had fallback block reading it → dead "N/A" branch |
| `intel_suite.matchup_dvp` | full current DVP object | full current DVP object | OK (used) |
| `has_momentum_modifier`, `momentum_modifier`, `crew_chief`, `whistle_*`, `vacuum_*` | @ root (Ferrari) | `None` | Not merged |

**Legacy references found:**
- `PlayerDetailPage.jsx` lines 1167–1199 (removed) — read `selectedVisionProp.intel_suite.defensive_momentum` and rendered a "DEFENSIVE MOMENTUM" card. The backend no longer emits this key (it was replaced by the root-level `momentum_data` object emitted by Ferrari scoring). The branch always produced "N/A" but masked the correct fallback.
- Environmental Factors "ACTIVE FOR …" summary checked only `intel_suite.context_badges`, which is empty for NBA (context_badges live at root).

**Current (authoritative) sources used:**
- Defensive momentum → `pick.momentum_data` (NBA scoring) OR `pick.intel_suite.matchup_dvp` (fallback). Both come from `services/defensive_momentum_service.py` / Ferrari enrichment. No other source is read.
- Scout badges → `pick.scout_badges` (root) OR `pick.intel_suite.scout_badges`.
- Context badges → `pick.context_badges` (root) + `pick.intel_suite.context_badges` (MLB string array).
- `defensive_momentum_cache` Mongo collection is referenced ONLY by `vegas_killer_model.py`, `vegas_pro_model.py`, `vegas_regression_model.py`, `defensive_momentum_service.py` (authoritative writer). Not read by `ferrari_tiers.py`, `player.py`, or any frontend path.

**Files changed (frontend only):**
- `frontend/src/components/dashboard/PlayerDetailPage.jsx`
  1. Merge block now copies `momentum_data`, `active_badges`, `context_badges`, `has_momentum_modifier`, `momentum_modifier`, `crew_chief`, `whistle_class`, `whistle_modifier`, `has_whistle_modifier`, `point_lift`, `lift_label`, `lift_type`, `has_vacuum_modifier`, `vacuum_modifier`, `vacuum_data`, `matchup_analysis`, `opponent`, `opponent_abbr` from the clicked Ferrari pick into the refetched prop.
  2. Removed the legacy `intel_suite.defensive_momentum` render branch.
  3. Environmental Factors grid now unions root-level `context_badges` with `intel_suite.context_badges` for `isActive` detection and custom descriptions.
  4. "ACTIVE FOR {player}" summary now renders whenever `active_badges`, root `context_badges`, OR `intel_suite.context_badges` has entries.

**Before/After payload — Jokic AST 7.5 (Safe Haven):**
- `[A] Ferrari`: momentum_data=dict(22), context_badges=list(1), intel_suite.matchup_dvp="Rank #10 vs. Playmakers"
- `[B] /player-with-badges` (raw, before fix): momentum_data=None, scout_badges=None, active_badges=None → UI showed blank "DEFENSIVE MOMENTUM: N/A"
- `[C] Post-merge (after fix)`: momentum_data=dict(22), context_badges=list(1), intel_suite.matchup_dvp="Rank #10 vs. Playmakers" → UI renders `MomentumTrackerFull` when momentum_data present, else current matchup_dvp. Legacy path gone.

**Validation (Playwright on preview):**
- Safe Haven #0 (Jalen Duren PTS 11.5): ENVIRONMENTAL FACTORS ✓, PERFORMANCE INDICATORS ✓, "ACTIVE FOR JALEN DUREN:" ✓ (shows HOME COOKIN' pill), "DEFENSIVE MOMENTUM" heading = 0 sightings, current `matchup-dvp-fallback` = 1 (vs ORL Rank #13 Medium), no "No game data", bar charts present.
- Identical merge path used by Front Lines (`handleRadarClick`) and War Zone (`handleRadarClick`) — covered by the same fix.



**Context:** Clicking a Safe Haven / Front Lines / War Zone card opened a
detail view with broken bar charts ("No game data"), missing hit rates,
missing L5/L10/Season averages, and raw market keys in category headers
(e.g. `PLAYER_POINTS_ASSISTS_ALTERNATE`). Backend payload was already
healthy — the break was purely frontend contract mismatches on the inner
click-path components.

**Diff (3 files, frontend-only — no backend touched):**
- `frontend/src/components/dashboard/PlayerDetailPage.jsx`
  - Import + apply `normalizeFerrariPicks` to the direct XHR response from
    `/api/v3/player-with-badges/:name` (props previously bypassed the
    adapter that runs on the Ferrari tier hooks, so `stat_type_extracted`,
    `direction`, `market`, and synthetic `chart_data` were all `null`).
  - Added a secondary fetch to `/api/v3/master-hub/player/:bdl_id` to
    populate `game_logs` (bar chart input) and `baseline_stats` (PPG/RPG/
    APG header strip). `/player-with-badges` does not include these; the
    master-hub endpoint does.
- `frontend/src/utils/normalizeFerrariPick.js`
  - Extended adapter with a `MARKET_TO_SHORT` inverse map so `stat_type`
    values arriving as raw market strings (e.g.
    `player_points_assists_alternate`) collapse to short codes (`PA`, `PR`,
    `PRA`, …). Enables both category grouping and the GameLogBarChart
    `STAT_FIELD_MAP` lookup.
  - Market derivation now also considers the collapsed short code when
    `pick.market` is null.
- `frontend/src/components/dashboard/GameLogBarChart.jsx`
  - Fallback parses opponent abbreviation + home/away flag from the
    `matchup` string (`DET vs. NYK` / `DET @ NYK`) when
    `opponent_team_id` / `home_game` are absent in BDL/Tank01 game logs.

**Verification (live, 2026-04-24, Playwright on preview env):**
- Safe Haven click (Jalen Duren): 46 prop rows, 1 VISION-highlighted prop,
  Intel Suite modal opens with Environmental Factors + Performance
  Indicators, bar charts render with values (24/19/17/12/16/15/6/24/8/21),
  opponent labels, L5/L10 hit + averages visible, PPG/RPG/APG/STL/BLK
  header populated.
- War Zone click (Shaedon Sharpe): 30 prop rows, 1 VISION highlight,
  category headers now show `PTS+AST` (not raw market key), PRA/PR/PA
  charts all render — "No game data" count = 0.
- Auto-scroll to highlighted prop + gold-glow ring both working.



## 2026-04-21 — Injury-Rank Phase 2: usage-sorted beneficiaries (multi-sport)

**Context:** `services/injury_advantage.py::compute_injury_advantages` was
ranking injury beneficiaries by `my_index`, i.e. whichever order teammates
happened to appear in `board_picks`. This made DEN's Christian Braun rank
"primary" over Cameron Johnson purely by incidental iteration order.

**Diff (4 files + 1 test file):**
- `services/usage_resolver.py` — NEW. Multi-sport provider registry:
  NBA reads `nba_master_hub_2026.advanced_stats` (usage% × minutes blend),
  MLB/NFL return `(None, "unavailable")` and plug in via `register_provider`.
  Public API: `rank_teammates_by_usage(db, sport, teammates)` — alphabetical
  tiebreak on equal usage for determinism.
- `services/injury_advantage.py` — build a per-team usage-sorted ranking
  up front, replacing `my_index = next(...)` with `team_rank_map[team][name]`.
  Emits `usage_rank` + `usage_source` per advantage.
- `routes/vacuum.py::/v3/vacuum/live-alerts` — surface `usage_rank` +
  `usage_source` on each alert payload.
- `tests/test_usage_resolver.py` — 13 tests: blend function bounds,
  unknown-sport/MLB fallback, deterministic tiebreak, loop-order immunity,
  future-NFL plug-in proof.

**Verification (live, 2026-04-21):**
- DEN beneficiaries now correctly ordered:
  `Cameron Johnson (usage 13.74, primary) > Christian Braun (13.70, secondary)`
  (was reversed under loop-order).
- All 5 live alerts carry `usage_source: "nba_hub"`.
- All 6 Ferrari endpoints (NBA/MLB × 3 tiers) → HTTP 200.
- 105/105 pytest passing.

## 2026-04-21 — Stat-aware CV caps for Safe Haven eligibility

**Context:** Single `max_cv=0.50` on Safe Haven was rejecting high-hit-rate
low-line props (Ajay Mitchell AST 1.5 @ 95% HR, Vucevic REB 3.5 @ 90% HR,
etc.) because CV = σ/μ is structurally higher for small-mean stats.

**Diff (2 code + 1 test file):**
- `services/scoring/cv_caps.py` — NEW sport-agnostic module with
  `CV_CAP_BY_STAT` (PTS/PRA=0.50, AST/REB=0.60, STL/BLK=0.65, 3PM=0.55,
  combos=0.50/0.55) + `DEFAULT_CV_CAP=0.50` for MLB/NFL/unknowns.
- `services/scoring/adapters/nba_scoring.py::check_safe_haven_gates` —
  resolve cap from `prop["stat_type"]` and override `self.SAFE_HAVEN["max_cv"]`
  per-call.
- `tests/test_cv_caps.py` — 24 tests including contract locks on the 13
  audited CV-only rejects.

**Verification (live on today's board):**
- 7 picks newly admitted to Safe Haven (3 AST, 4 REB) — exactly the
  audited candidates.
- Safe Haven Top-10 now: 5 PTS, 3 AST, 2 REB (was PTS-dominated).
- 2 PRA picks (Vucevic 10.5 / 11.5) remain correctly rejected (PRA cap
  stays 0.50 per spec).
- Extreme CV outliers (≥0.70) remain rejected on every stat.

## 2026-04-21 — Stat-aware α in `ranking_score_v2`

**Context:** Single α=0.40 suppressed low-line AST/REB/STL/BLK props in the
`sort=gap` board because `line^0.40` barely shrinks for small lines while
big-raw-gap PTS/PRA UNDERs monopolized the Top-10.

**Diff (1 code + 1 test file):**
- `services/scoring/recompute.py` — added `ALPHA_BY_STAT` map +
  `_DEFAULT_ALPHA=0.50` + `_resolve_alpha()` helper; refactored
  `_compute_ranking_score_v2` to take `stat_type` kwarg; caller passes
  `ctx.stat_type`.
- `tests/test_ranking_alpha.py` — 14 tests (cap map, rank-position
  improvement, regression guards, backwards-compat, future NFL plug-in).

**Verification (live):**
- All low-line AST/REB/STL/BLK picks climbed 20–45 positions in the
  global sort.
- PTS/PRA picks identical (α stays 0.40 for those regimes).
- Ferrari endpoints all 200.

## 2026-04-21 — Canonical multi-sport DvP rank pipeline

**Context:** Scored docs had `defensive_rank`, `momentum_data.dvp_rank`,
and `opponent_defensive_rank` all ≈ `None`. Gemini filled the gap with
"24th-ranked Spurs" hallucination (coincidentally matching the stale
`config.settings.DVP_RANKINGS["PTS"]["SAS"]=24` from last season).

**Diff (5 files + 1 test file):**
- `services/defensive_rank_resolver.py` — NEW. Shared multi-sport resolver
  + provider registry + prewarm hook. NBA strict-BDL (rejects
  `DvPDataSource.STATIC_FALLBACK`); MLB/NFL unavailable.
- `services/scoring/recompute.py` — Phase 4b writer: calls
  `ensure_provider_warm(sport)` once, resolves per-prop via
  `get_opponent_defensive_rank`, writes canonical 3 fields on every
  score doc.
- `services/scoring/prop_scores_store.py` — added 3 fields to
  `_SCORE_OUTPUT_FIELDS` whitelist.
- `services/unified_pipeline.py` — `_run_gemini_enrichment` now reads
  only the canonical field; dropped `momentum_data` / static fallbacks.
- `routes/ferrari_tiers.py` — NBA merger copies canonical fields to
  API response.
- `tests/test_defensive_rank_resolver.py` — 13 tests including
  `test_static_DVP_RANKINGS_is_NEVER_a_source`.

**Verification (live):**
- `nba_prop_scores` (final-nba-rt): 2177/2177 carry canonical fields.
- `mlb_prop_scores`: 4544/4544 carry canonical fields (honestly `unavailable`).
- Ferrari responses: 10/10 picks per tier carry new fields.
- Literal "24th" across all NBA board responses: **0**.
- Shaedon Sharpe vs SAS: `opponent_defensive_rank=16, source=bdl_live`.

## 2026-04-21 — Market Gap ("Book Spread") multi-sport disagreement signal

**Context:** Backend exposed per-book prices but no aggregated measure of
sportsbook disagreement — a premium sharp-betting signal.

**Diff (3 code + 2 UI + 1 test file):**
- `services/market_gap.py` — NEW sport-agnostic `compute_market_gap` +
  `annotate_market_gap` helpers. Thresholds configurable via env
  (`MARKET_GAP_MEDIUM=50`, `MARKET_GAP_HIGH=100`).
- `routes/ferrari_tiers.py::_serve_ferrari_tier` — one-line wire-in,
  single sport-agnostic choke point.
- `components/dashboard/MarketGapBadge.jsx` — NEW shared React badge +
  detail row. Muted zinc, no glow, no animation.
- `components/dashboard/UniversalPlayerCard.jsx` + `PlayerDetailPage.jsx`
  — inline + expanded placements.
- `tests/test_market_gap.py` — 13 tests.

## 2026-04-21 — Switch Gemini model to `gemini-flash-lite-latest`

All callers + tests updated; 1 outlier at `gemini-2.5-flash`
consolidated; `backend/.env::GEMINI_MODEL` updated. No stale model
references remain.



## 2026-02-20 — Upgrade `ranking_score_v2` formula to α=0.40 blend

**Context:** Shadow α-sweep audit (`/tmp/alpha_sweep.py` →
`/tmp/magic_formula_report.md`) compared the raw_gap vs gap_pct extremes
plus 7 intermediate α values. α=0.40 delivered the highest Top-25 WR
(76%) and +71.2% real-odds ROI of any formula tested this session while
allowing 3 small-line props into Top-10 (vs 10/10 under pure gap_pct,
0/10 under raw_gap). Lets Cade Cunningham AST 7.5 and Nikola Jokic
AST 7.5 re-enter the visible slate instead of being buried.

**Diff (1 file, ~15 LOC):**
- `services/scoring/recompute.py::_compute_ranking_score_v2` —
  new signature `(projection, line, recommendation, p_model=None)`.
  Formula: `round((raw_gap / max(line, 1.0) ** 0.40) * p_model, 6)`.
  Helper call in the score-doc construction now passes `ctx.p_model`.

**Verification (post-recompute, 2,741 NBA scores replaced):**
- Expected match confirmed per row:
  - Cade Cunningham AST 7.5 OVER → `+1.6425` ✓
  - Cade Cunningham AST 8.5 OVER → `+1.0971` ✓
  - Daniss Jenkins AST 1.5 OVER → `+1.9658` ✓
  - Shaedon Sharpe PTS 7.5 OVER → `+4.2029` ✓
  - Nikola Jokic AST 7.5 OVER  → `+1.4986` ✓
  - Nikola Jokic AST 8.5 OVER  → `+0.9535` ✓
- `?sort=gap` Safe Haven top-10: all big-line PTS (Sharpe 7.5, Allen 9.5,
  Scoot 7.5, Duren 14.5, Merrill PRA 9.5, Braun 7.5, Grant 7.5, Brown 19.5,
  Edwards 19.5, J. Green 14.5). **0 small-line ≤3.5 picks in the top-10.**
- Top-30 distribution under `?sort=gap`:
  - Jenkins AST 1.5 at **#14** (was #1 under old gap_pct formula)
  - Cade AST 7.5 at **#18** (was #43 under old gap_pct formula)
  - Jokic AST 7.5 at **#19**
  - 5 / 30 small-line ≤3.5 picks — present but not dominating.
- Default sort unchanged: Jenkins AST 1.5 still #1 (uses `vision_score`).
- Endpoint smoke: all HTTP 200 (nba & mlb ferrari, odds/props, live/scores,
  scheduler-status).
- Hot-reload note: `recompute.py` is imported at module load by the FastAPI
  route; helper changes require a supervisor restart (not just reload).
  Single restart + re-recompute resolved.


## 2026-02-20 — Frontend toggle for Projection Gap ranking (NBA)

**Diff (2 files, ~65 LOC):**
- `frontend/src/hooks/useLiveOdds.js` — `fetchWarZone`, `fetchSafeHaven`,
  `fetchFrontLines` now accept an optional `sort` argument and append
  `&sort=<value>` to the URL when set. `useWarZone`, `useSafeHaven`,
  `useFrontLines` accept `sort` via their `options` object, thread it
  into the query key so cache separates the two orderings, and forward
  it to the fetcher.
- `frontend/src/pages/Dashboard.jsx`:
  - New `nbaRankingMode` state (`'default'` | `'gap'`) persisted to
    `localStorage` under key `nbaRankingMode` so the user's choice
    survives refreshes.
  - The 3 NBA tier hooks receive `{ sort: nbaRankingMode === 'gap' ? 'gap' : null }`.
  - A small pill-toggle (`data-testid="nba-ranking-toggle"` with
    `nba-ranking-default-btn` and `nba-ranking-gap-btn`) renders only
    when `currentSport === 'nba'`, positioned above the Safe Haven
    section. Defaults to "Default".

**Verification (live Ferrari demo dashboard, 2026-02-20):**
- Default top-5 Safe Haven (unchanged): Daniss Jenkins AST 1.5, Cade
  Cunningham AST 7.5, Jalen Duren PTS 14.5, Shaedon Sharpe PTS 7.5,
  Nikola Jokic AST 8.5.
- After "Projection Gap" click: Daniss Jenkins AST 1.5 (+1.66),
  Shaedon Sharpe PTS 7.5 (+1.31), Scoot Henderson PTS 7.5 (+1.06),
  Christian Braun PTS 7.5 (+0.97), Jarrett Allen PTS 9.5 (+0.97) — same
  strict DESC `ranking_score_v2` order the API returns for `?sort=gap`.
- Pill toggle stays selected after refresh (localStorage persistence).
- MLB dashboard unaffected — the toggle is gated behind
  `currentSport === 'nba'`.

**Default ranking still unchanged on the wire.** The opt-in is purely
user-driven.


## 2026-02-20 — Projection-gap ranking (`ranking_score_v2`) behind `?sort=gap` toggle

**Context:** Shadow audit (`/tmp/projection_gap_ranking_report.md`) showed that
ranking surfaced picks by `gap / max(line,1.0)` produced Top-25 AST at 80% WR
and +149% real-odds ROI — strictly dominating the current `vision_score` sort.
Ships the new ranking signal **behind a toggle** — default sort remains
unchanged pending live A/B.

**Diff (5 files, ~50 LOC total):**
- `services/scoring/recompute.py` — new `_compute_ranking_score_v2(projection,
  line, recommendation)` helper; persisted on every scored prop.
- `services/scoring/prop_scores_store.py` — added `ranking_score_v2` to the
  `_SCORE_OUTPUT_FIELDS` allow-list (otherwise silently stripped on write).
- `services/board/reader.py::get_board` — new optional `sort_key_override`
  parameter. When set to `"ranking_score_v2"`, the query also excludes
  null-ranking rows so the DESC sort doesn't float nulls to the top.
- `routes/ferrari_tiers.py`:
  - `_dedupe_picks_by_player(..., sort=...)` — NBA safe_haven / front_lines /
    war_zone endpoints pass `sort` through; rank tuple becomes
    `(ranking_score_v2,)` when `sort == "gap"`.
  - `_get_nba_tier_picks_from_scores(..., sort=...)` — forwards to
    `get_board(sort_key_override=...)`.
  - `get_ferrari_safe_haven / front_lines / war_zone` — accept new
    `sort: Optional[str] = Query(None)` query param; pass through.
  - `_merge_score_with_board` — now copies `ranking_score_v2` from the score
    doc into the UI pick dict.

**Verification (post-recompute, 2,717 NBA scores):**
- `ranking_score_v2` present on 2,450 / 2,717 rows (90.2%). The 267 nulls are
  rows where VK1 produces no `model_projection` (non-model stat_type such as
  certain PRA markets or rows with missing history).
- Endpoint behavior (live board, 2026-02-20):
  - `GET /api/v3/ferrari/safe-haven?sport=nba&limit=10` — unchanged default
    (Daniss Jenkins AST 1.5, Cade Cunningham AST 7.5, Jalen Duren PTS 14.5 …)
  - `GET /api/v3/ferrari/safe-haven?sport=nba&limit=10&sort=gap` — strict DESC
    by `ranking_score_v2` (Daniss Jenkins +1.660, Shaedon Sharpe +1.313, Scoot
    Henderson +1.057, Christian Braun +0.968, Jarrett Allen +0.967, Jerami
    Grant +0.952, Tari Eason +0.931, Sam Merrill +0.884, Wendell Carter
    +0.822, Evan Mobley +0.692).
  - Front Lines `?sort=gap` elevates small-line multi-projection plays (Daniss
    Jenkins REB 1.5 = +1.407, Cameron Johnson AST 1.5 = +1.093).
  - War Zone `?sort=gap` unchanged small-set but slightly re-ordered.
- Invalid sort values silently fall back to default (tested with
  `?sort=bogus` → default ordering).
- MLB endpoints unaffected (the `sort` param only activates when
  `sport == "nba"`; all paths pass `sort=None` for MLB).
- All other endpoints healthy: `/api/v3/odds/props`, `/api/live/scores`,
  `/api/v3/scheduler-status`, both MLB Ferrari endpoints — HTTP 200.

**Default sort is NOT flipped.** The new signal is opt-in only via the
`?sort=gap` query param. Promotion to default blocked pending live user
comparison.


## 2026-02-20 — Restore VK1 profitable signal as default NBA `p_model`

**Context:** Forensic audit revealed VK1 was computed but discarded: 95.5% of
`nba_prop_scores` used `p_true_method="hit_rate"` (L10 rolling), 0% used VK1.
The historically profitable `confidence_threshold=55.0` filter from
`backtest_real_lines.json` (AST 62.44% WR / +19.26% ROI / 4,249 bets) had no
equivalent in the live pipeline.

**Diff (2 files, ~6 LOC):**
- `services/scoring/adapters/nba_scoring.py:497` — default `p_true_method`
  `"hit_rate"` → `"model"` (VK1 regression as `p_model`).
- `services/scoring/scoring_stack.py::compute_tier` — new `p_model < 0.55 →
  tier="unqualified"` gate, fires after anchor veto, before tier gates.

**Verification (post-recompute, `version_tag=final-nba-rt`, 2,688 props):**
- `p_true_method` distribution: `model=2421, hit_rate=171 (fallback), none=96`
- Tier distribution: `safe_haven=39, front_lines=102, war_zone=14, unqualified=2533`
- 55% gate fires: 1,196
- AST surfaced: 31 → **40 (+29%)**; top of AST surface is now Cade Cunningham,
  Jokic, Harden, Brunson (model-elevated) instead of trivial 1.5/3.5-line picks.
- Traps correctly demoted: Tatum REB 9.5 (p=0.375), KAT REB 10.5 (p=0.503) now
  `unqualified` with reason `p_model<0.55`.
- Endpoints healthy: `/api/v3/ferrari/{safe-haven,front-lines,war-zone}` HTTP 200;
  `/api/v3/odds/props`, `/api/live/scores`, `/api/v3/scheduler-status` all 200.

**VK2 status:** kept on disk, still callable via explicit `p_true_method="vk2"`.
5-path audit (`/tmp/path_compare_report.md`) showed VK2's 102 advanced-stat
features contribute <1pp ROI; simplified VK2 returns +29.79% vs VK1's +30.12%.
No routing advantage to VK2 by default.

**Expected downstream impact:** `edge_pct` now reflects real model edge (not
`hit_rate − tp`); tier assignments now driven by VK1 p_over (Gaussian over MAE)
matching the historical backtest that produced the original +19.26% AST ROI.


## 2026-04-19 — Wave 0 Batch 12 plumbing (long-tail sweep + odds_mapping closure)
- Registry reconciliation: `odds_mapping` was **already** present in registry at
  `collection_names.py:70` as per-sport NBA concept. Batch 11 mis-used
  `COLL.shared("odds_mapping")` — correct call is `COLL("odds_mapping","nba")`.
  **No code change to registry needed.**
- Routed 3 deferred refs in `server.py:1316-1318` (boot-time odds_mapping indexes).
- 14 Batch 12 files + server.py routed through `COLL(...)`:
  `services/historical_data_fetcher.py`, `services/mlb_vegas_killer_model.py`,
  `services/optimized_sync_engine.py`, `services/bdl_game_logs_sync_batched.py`,
  `services/photo_storage_service.py`, `services/master_hub_sync.py`,
  `services/vision_ai_service.py`, `services/oracle_apex_service.py` (2nd-pass),
  `services/bdl_game_logs_sync.py`, `services/stats_enrichment_service.py`,
  `services/intel_suite_calculator.py`, `services/probability_score_service.py`,
  `services/mlb_deep_ingestion.py`, `services/ferrari_tier_service.py` (2nd-pass).
- 17 code-level literals removed → 17 `COLL(...)` call-sites added (3 server.py
  + 14 Batch 12). 12 imports added (2 files already had imports from prior batches).
- In-scope hardcoded-ref count in these files: **17 → 0**.
- Regression suite: 80 passed / 1 skipped / 0 failed — matches baseline.
- Live smoke after restart: all 10 endpoints HTTP 200. Boot-time odds_mapping
  index creation (3 calls) executed cleanly via new COLL routing.
  Backend uptime clean. 0 new errors.
- Global broader-scanner residual: **43 → 29 refs across 28 files**.
- Breakdown: 14 runtime (Batch 13 candidates), 13 scripts/* (non-runtime),
  2 `scripts/layer_audit.py` (user excluded), 1 registry self-reference.
- **Wave 0 progress: 56 files plumbed, 93% of baseline refs eliminated.**
- Audit: `/app/memory/wave0_batch12_audit.md`.


## 2026-04-19 — Wave 0 Batch 11 plumbing (second-pass + long-tail)
- 14 files routed through `services/config/collection_names.py::COLL`:
  `server.py` (second-pass), `services/mlb_tier_sorter.py`,
  `services/vegas_killer_model.py` (second-pass),
  `services/forward_testing_service.py`,
  `services/vegas_pro_model.py` (second-pass), `services/mlb_badge_system.py`,
  `services/vegas_regression_model.py` (second-pass),
  `services/bdl_enhanced_data.py` (second-pass),
  `services/mlb_tier_service.py` (second-pass),
  `services/live_injury_micro_sync.py`, `services/bdl_player_badge_service.py`,
  `services/injury_sensor.py`, `routes/vision.py`,
  `repositories/board_repo.py` (second-pass).
- In-scope concepts: `master_hub` (NBA+MLB), `board_cache` (NBA+MLB), `live_props`
  (NBA+MLB), `context_flags` (NBA), `injuries` (shared), `live_scores_cache` (shared).
- 20 code-level literals removed → 20 `COLL(...)` call-sites added. 7 imports added
  (7 files already had import from prior batches).
- **Stop & Report**: `odds_mapping` concept NOT in registry. Per strict-refactor
  rules, did not add registry entry. 3 `odds_api_mapping_master` refs in
  `server.py:1316-1318` deferred pending user approval to add
  `"odds_mapping": "odds_api_mapping_master"` to `_SHARED_COLLECTIONS`.
- Batch 11 in-scope residuals in these files: **20 → 0**.
- Regression suite: 80 passed / 1 skipped / 0 failed — matches baseline.
- Live smoke after restart: NBA + MLB Ferrari, master-hub lookup, live scores,
  vision/status all HTTP 200. Backend uptime clean. 0 new errors.
  (Pre-existing 404 on `/v3/vision/player/{slug}` — `player_router` not mounted
  in `server.py`; NOT a regression from this batch.)
- Global in-scope broader-scanner residual: **66 → 43 refs across 42 files**.
- Deferred refs awaiting registry entry: 3 (`odds_api_mapping_master`).
- Audit: `/app/memory/wave0_batch11_audit.md`.


## 2026-04-19 — Wave 0 Batch 10 plumbing (broader scanner sweep)
- 9 files routed through `services/config/collection_names.py::COLL`:
  `routes/master_hub.py`, `routes/ai_context.py`, `routes/live.py`,
  `routes/cached_data.py`, `services/injury_triggered_rescore.py`,
  `services/board_intelligence_service.py`, `routes/ferrari_tiers.py`,
  `services/team_stats_service.py`, `routes/qa_testing.py`.
- In-scope concepts: `master_hub` (NBA+MLB), `board_cache` (NBA+MLB),
  `ticker_cache` (shared), `injuries` (shared).
- 44 code-level literals removed → 44 `COLL(...)` call-sites added. 7 imports added
  (2 files already had import from prior batches).
- Broader scanner now authoritative (covers `db.X`, `db["X"]`, `_db.X`, `_db["X"]`,
  `engine.db.X`, `self.db[...]` variants).
- Second-pass completed on `board_intelligence_service.py` (4 refs added since Batch 2).
- In-scope hardcoded-ref count in these files: **44 → 0**.
- Regression suite: 80 passed / 1 skipped / 0 failed — matches baseline.
- Live smoke after restart: NBA + MLB Ferrari + master-hub player lookup +
  ai-context + live scores/news + command search all HTTP 200.
  `master-hub/player/name/Luka Doncic` returned real 8-key document via new
  bracket-access COLL routing. Backend uptime clean. 0 new errors.
- Global broader-scanner residual: **107 → 66 refs across 56 files** (41-ref
  reduction, 38% drop). Remaining refs are long-tail (mostly 1–2 per file).
- Audit: `/app/memory/wave0_batch10_audit.md`.


## 2026-04-19 — Wave 0 Batch 9 plumbing (resolvers/repos/routes/utility services)
- 10 files routed through `services/config/collection_names.py::COLL`:
  `services/picks/player_stats_resolver.py`, `advanced_analytics.py`,
  `repositories/player_repo.py`, `repositories/sync_repo.py`,
  `routes/command.py`, `routes/mlb_ripple.py`,
  `services/usage_spike_detector.py`, `services/props_service.py`,
  `services/injury_service.py`, `services/injury_advantage.py`.
- In-scope concepts: `master_hub` (NBA+MLB), `master_roster`, `board_cache`,
  `sync_log` (shared), `injuries` (shared), `live_scores_cache` (shared).
- 13 code-level literals removed → 13 `COLL(...)` call-sites added. 10 imports added.
- First-time routing of bracket-access pattern `_db["mlb_master_hub_2026"]` and
  `db["injuries_normalized"]` — both handled correctly via `COLL(...)`.
- In-scope hardcoded-ref count in these files: **13 → 0**.
- Regression suite: 80 passed / 1 skipped / 0 failed — matches baseline.
- Live smoke after restart: NBA + MLB Ferrari, NBA + MLB player search,
  MLB ripple alerts all HTTP 200. Backend uptime clean. 0 new errors.
- Global code-level residual (apples-to-apples attr-access pattern): 59 → **48 refs**.
- Broader scanner added (now covers `db["..."]` bracket-access and `_db` variable
  patterns that were invisible before) — surfaces 107 total refs across 65 files
  as honest accounting for Batch 10 scope. This is not a regression; it's
  newly-surfaced visibility.
- Audit: `/app/memory/wave0_batch9_audit.md`.


## 2026-04-19 — Wave 0 Batch 8 plumbing (adapter/writer/sync orchestration)
- 9 files routed through `services/config/collection_names.py::COLL`:
  `services/odds_api_service.py`, `services/sync_service.py`,
  `services/badge_resolver.py`, `services/mlb_high_friction_model.py`,
  `services/tier_builder_service.py`, `services/ssot_data_layer.py`,
  `services/sync_orchestration_service.py`, `services/bdl_comprehensive_sync.py`,
  `services/insights_sync_service.py`.
- In-scope concepts: `master_hub` (NBA+MLB), `board_cache`, `live_props` (MLB),
  `events_cache`, `odds_cache`, `sync_log` (shared), `context_flags` (new this batch).
- 18 code-level literals removed → 18 `COLL(...)` call-sites added. 9 imports added.
- `context_flags` concept first used this batch — registry resolves it to
  `nba_context_engine` for NBA sport.
- `_archive_mlb_v1/` not touched per exclusion.
- Out-of-scope refs reported and left: `dg_radar_picks`, `dg_goblin_vault`,
  `dg_front_lines`, `dg_parlay_builder`, `dg_goblin_recon`, `dg_daily_insights`,
  `mlb_historical_logs`, `dg_verification_failures`, `dg_player_data`,
  `dg_trending` (none in registry).
- In-scope hardcoded-ref count in these files: **18 → 0**.
- Regression suite: 80 passed / 1 skipped / 0 failed — matches baseline.
- Live smoke after restart: all 9 NBA + MLB endpoints HTTP 200.
  Backend uptime clean. 0 new errors.
- Global code-level residual: 81 → **59 refs across 47 files**.
- Audit: `/app/memory/wave0_batch8_audit.md`.


## 2026-04-19 — Wave 0 Batch 7 plumbing (MLB pipeline + roster services)
- 8 files routed through `services/config/collection_names.py::COLL`:
  `services/mlb_tier_service.py`, `services/mlb_lineup_ripple_service.py`,
  `services/mlb_master_sync.py`, `services/mlb_oracle_apex_service.py`,
  `services/roster_service.py`, `services/roster_sync_service.py`,
  `services/data_integrity_service.py`, `services/photo_service.py`.
- In-scope concepts: `master_hub` (NBA + MLB), `master_roster` (NBA),
  `board_cache` (NBA + MLB), `live_props` (MLB), `sync_log` (shared).
- 30 code-level literals removed → 30 `COLL(...)` call-sites added. 8 imports added.
- This batch closes the 2 out-of-scope residuals from Batch 6 in
  `data_integrity_service.py` (`master_roster` + `sync_log`).
- Full MLB pipeline now plumbed end-to-end: mlb_master_sync (writer) +
  mlb_tier_service + mlb_oracle_apex_service + mlb_lineup_ripple_service (readers).
- Out-of-scope refs reported and left: `nba_context_engine` in mlb_tier_service
  (not in priority list/registry), `dg_flagged_players`, `dg_verification_failures`
  (not in registry).
- In-scope hardcoded-ref count in these files: **30 → 0**.
- Regression suite: 80 passed / 1 skipped / 0 failed — matches baseline.
- Live smoke after restart: NBA Ferrari endpoints + MLB tier endpoints all HTTP 200,
  MLB endpoints returned 60KB+ payloads (real data through COLL routing).
  Backend uptime clean. 0 new errors.
- Global code-level residual: 103 → **81 refs across 58 files**.
- Audit: `/app/memory/wave0_batch7_audit.md`.


## 2026-04-19 — Wave 0 Batch 6 plumbing (injury/live/social/cache/integrity)
- 5 files routed through `services/config/collection_names.py::COLL`:
  `services/injury_vacuum_service.py`, `services/engines/live_scores_engine.py`,
  `services/engines/social_signal_engine.py`, `services/rolling_cache_manager.py`,
  `services/data_integrity_service.py`.
- In-scope concepts: `star_usage_cache`, `master_hub` (NBA + MLB), `board_cache`
  (NBA + MLB), `injuries` (shared), `live_scores_cache` (shared), `ticker_cache`
  (shared), `ticker_headlines` (shared), `breaking_news_cache` (shared).
- 23 code-level literals removed → 23 `COLL(...)` call-sites added. 5 imports added.
- Cross-driver pattern validated: `db[COLL(...)]` subscript works identically
  on both PyMongo sync (`sync_db`) and Motor async (`self.db`) clients.
- Out-of-scope refs reported and intentionally left: `dg_master_roster` +
  `dg_sync_log` in data_integrity_service.py (concepts not in Batch 6 priority list),
  plus `injury_log`, `vacuum_alerts`, `bdl_injuries`, `dg_social_signals`,
  `dg_player_news_cache`, `dg_verification_failures` (not in registry).
- In-scope hardcoded-ref count in these files: **23 → 0**.
- Regression suite: 80 passed / 1 skipped / 0 failed — matches baseline.
- Live smoke after full restart: all Ferrari endpoints + live-scores + vacuum/updates
  HTTP 200. Backend uptime clean. 0 new errors.
- Global code-level residual (broad pattern incl. MLB variants): 103 refs across 66 files.
- Audit: `/app/memory/wave0_batch6_audit.md`.


## 2026-04-19 — Wave 0 Batch 5 plumbing (intel/enrichment/repos)
- 5 files routed through `services/config/collection_names.py::COLL`:
  `services/market_moves_engine.py`, `services/oracle_apex_service.py`,
  `services/vision_intel_enrichment_service.py`, `services/headshot_service.py`,
  `repositories/board_repo.py`.
- In-scope concepts: `board_cache`, `master_hub`, `injuries` (shared),
  `live_scores_cache` (shared).
- 13 code-level literals removed → 13 `COLL(...)` call-sites added. 5 imports added.
- Out-of-scope refs reported and intentionally left: 2× `dg_live_props`
  (oracle_apex L429 + board_repo L20 — `live_props` not in Batch 5 priority list),
  plus `ferrari_scored`, `oracle_apex_picks` (not in registry).
- In-scope hardcoded-ref count in these files: **13 → 0**.
- Regression suite: 80 passed / 1 skipped / 0 failed — matches baseline.
- Live smoke after restart: all Ferrari endpoints + market-moves read path HTTP 200,
  supervisor clean, 0 new errors in backend.err.log.
- Global code-level residual (broader pattern incl. live_scores_cache/ticker/news):
  **85 refs across 53 files**.
- Audit: `/app/memory/wave0_batch5_audit.md`.


## 2026-04-19 — Wave 0 Batch 4 plumbing (boot/indexes/scheduler/legacy engine)
- 4 files routed through `services/config/collection_names.py::COLL`:
  `server.py`, `scripts/init_database.py`, `routes/scheduler.py`,
  `services/engines/demon_goblin_engine.py`.
- In-scope concepts: `board_cache`, `board_cache_temp`, `master_hub`,
  `master_roster`, `live_props`, `odds_cache`, `events_cache`, `sync_log`,
  `ticker_headlines` (shared).
- 44 code-level literals removed → 44 `COLL(...)` call-sites added. 4 imports added.
- Boot-time index creation block (22 `create_index` calls in `server.py`) fully
  routed through `COLL(...)` — confirmed healthy on full `supervisorctl restart`.
- Response-shape preserved: JSON response keys in `/v3/init-database` left
  unchanged (labels only, not DB access).
- `routes_archive/roster.py` excluded per scope.
- In-scope hardcoded-ref count in these files: **44 → 0**.
- Regression suite: 80 passed / 1 skipped / 0 failed — matches baseline.
- Live smoke after restart: all Ferrari endpoints + `/v3/scheduler-status` HTTP 200,
  scheduler running with 22 jobs, 0 errors in backend.err.log.
- Global code-level residual: 153 → **109 refs across 70 files**.
- Audit: `/app/memory/wave0_batch4_audit.md`.


## 2026-04-19 — Wave 0 Batch 3 plumbing (core pipeline, no renames)
- 3 files routed through `services/config/collection_names.py::COLL`:
  `services/ferrari_tier_service.py`, `services/picks_getter_service.py`,
  `services/cached_board_builder_service.py`.
- In-scope concepts: `board_cache`, `board_cache_temp`, `master_hub`,
  `master_roster`, `events_cache`, `odds_cache`, `sync_log`.
- 18 code-level literals removed → 18 `COLL(...)` call-sites added. 3 imports added.
- Includes atomic `renameCollection` admin command (cached_board_builder L390-391)
  now routed through `COLL(...)` — behavior preserved.
- In-scope hardcoded-ref count in these files: **18 → 0** (docstring/log prose untouched).
- Out-of-scope refs reported and intentionally left: `ferrari_*` legacy collections
  (no registry concept), `nba_context_engine` (excluded from user's priority list),
  `dg_radar_picks/goblin_vault/front_lines/parlay_builder/goblin_recon/player_data/`
  `daily_insights/flagged_players` (not in registry).
- Regression suite: 80 passed / 1 skipped / 0 failed — matches baseline.
- Live smoke: `/api/v3/ferrari/{safe-haven,front-lines,war-zone}` all HTTP 200.
  Backend hot-reloaded cleanly; 0 new errors in supervisor log.
- Global code-level residual count: 170 refs across 75 files (down from ~188 pre-Batch-3).
- Audit: `/app/memory/wave0_batch3_audit.md`.


## 2026-04-19 — Wave 0 Batch 2 plumbing (ingest layer, no renames)
- 5 files routed through `services/config/collection_names.py::COLL`:
  `services/odds_sync_service.py`, `services/sharp_edge_calculator.py`,
  `services/board_intelligence_service.py`, `services/bdl_enhanced_data.py`,
  `services/bdl_stats_calculator.py`.
- In-scope concepts: `live_props`, `master_roster`, `odds_cache`, `master_hub`.
- 9 code-level literals removed → 9 `COLL(...)` call-sites added. 5 imports added.
- In-scope hardcoded-ref count in these files: **9 → 0** (docstring prose untouched).
- Regression suite: 80 passed / 1 skipped / 0 failed — matches baseline.
- Live smoke: `/api/v3/ferrari/{safe-haven,front-lines,war-zone}` all HTTP 200.
- Resolver parity verified: physical names unchanged → zero behavior change.
- Audit: `/app/memory/wave0_batch2_audit.md`.


## 2026-04-19 — Wave 0 Batch 1 plumbing (no renames)
- 9 files routed through `services/config/collection_names.py::COLL`:
  `db/collections.py`, `config/collections.py`, `services/nba_career_service.py`,
  `services/defensive_momentum_service.py`, `services/vegas_killer_model.py`,
  `services/vegas_regression_model.py`, `services/vegas_pro_model.py`,
  `services/context_badge_service.py`, `routes/cached_data.py` (context_engine only).
- Regression suite: 80 passed / 1 skipped / 0 failed (incl. `test_collection_names.py` 61/61).
- Live `/api/v3/ferrari/front-lines?sport=nba` returns HTTP 200 post-restart.
- No renames, no data migrations, no behavior changes. Indirection only.
- Residual scan: `/app/memory/wave0_batch1_residuals.md`.



## 2026-04-19 — Decision-Layer Integrity (Sengun AST 6.5 audit response)
Five ordered fixes after the user-flagged Sengun card exposed systemic
decision-layer contradictions between badges / tile / narrative / direction:

1. **Floor Lock window + tooltip honesty** (`services/intel_suite_calculator.py`)
   - Switched `values[-10:]` → `values[:10]` in both `_generate_scout_badges`
     and `_calculate_stability_index`. `history.2025_season` is stored
     newest-first, so the prior index took the OLDEST 10 games.
   - Floor Lock now requires `hit_rate >= 90` (matches its public tooltip).
     Previously fired on `std<=2.0 AND mean>line` at 70% hit rates.
2. **Canonical projection contract** (`services/intel_suite_calculator.py::_generate_vision_insight`)
   - Narrative now quotes `board_pick.vk_predicted` (the same field the UI
     tile binds to in `UniversalPlayerCard.jsx:508`) instead of
     `lasso.projection`. Lasso projection exposed as a separate
     `lasso_projection` field — never conflated.
3. **Model-disagreement flag**
   - `vision_insight.models_disagree = True` when `|vk−lasso|/line > 0.10`
     and the two models sit on opposite sides of the line. Narrative
     explicitly calls out disagreement instead of writing a one-sided thesis.
4. **PP-anchor direction veto** (`services/scoring/scoring_stack.py`)
   - New `_model_contradicts_anchor(prop, side)` runs inside `compute_tier`
     BEFORE tier dispatch. Rejects an OVER when all three contradict the
     anchor side: `vk_edge<0`, `hit_rate_over<50`, `l10_avg<line` (symmetric
     for UNDER). Sets `tier="unqualified"`,
     `tier_reason="model_contradicts_anchor: …"`.
5. **"Clear Read" → "Model Fit"** (`components/ui/BadgePill.jsx`)
   - Relabelled to precisely reflect the gate (Lasso R² ≥ 0.35 on held-out
     games). Tooltip sentiment changed from "positive / lean in" to
     "neutral / model-fit quality only — NOT a prop recommendation".
6. **War Zone gate review** (`services/scoring/adapters/nba_scoring.py`)
   - Documented comment: war_zone gates stay market-facing; directional
     evidence handled by Fix #4. Separation of concerns enforced in code
     comments; no threshold changes.

**Expected live effect**: on the next scoring pass (next `odds_sync` or
hourly rebuild), the Sengun AST 6.5 OVER pick drops from `war_zone` to
`unqualified` and vanishes from the board. No retroactive mutation of
existing score docs.

**Regression suite added**: `tests/test_decision_layer_sengun.py` (7 tests).
Total passing: 19 (12 pre-existing + 7 new).


## 2026-04-19 — Tier Integrity + Post-Dedup Backfill Verified
- **Bug fix**: player duplicates across a single tier (same player ×N on
  alternate lines or across stat families). Confirmed on NBA Safe Haven
  (Amen Thompson ×3) and MLB War Zone (James Wood ×2).
- **Fix at two levels**:
  - `services/board/reader.py::get_board` — over-fetch `cap*6`, walk sorted
    stream, keep first per normalized `player_name`, stop at `cap` distinct
    players. Reader is the single entry point for NBA boards.
  - `routes/ferrari_tiers.py::_dedupe_picks_by_player` — route-level
    fallback wired into all 5 tier exit points; covers MLB legacy
    collections (`mlb_safe_haven` / `mlb_front_lines` / `mlb_war_zone`)
    that bypass the reader. Ranks by `(vision_score, pp_utility, |edge|)`.
- **Post-dedup backfill audit** (2026-04-19 00:00 UTC):
  | Tier (sport)     | pool rows | distinct | surfaced | reason if <cap          |
  |------------------|----------:|---------:|---------:|-------------------------|
  | NBA safe_haven   |         3 |        1 |        1 | pool exhausted          |
  | NBA front_lines  |         9 |        4 |        3 | 1 × JIT injury (Durant) |
  | NBA war_zone     |         2 |        2 |        2 | pool exhausted          |
  | MLB safe_haven   |        10 |       10 |       10 | FULL                    |
  | MLB front_lines  |        10 |       10 |       10 | FULL                    |
  | MLB war_zone     |        10 |        9 |        9 | 1 × dedup (James Wood)  |
  Invariant `surfaced == min(cap, distinct − injury_excluded)` holds for
  every tier. No hidden exclusions.
- **Regression suite**: `/app/backend/tests/test_tier_integrity.py`
  (8 tests). Total backend regression surface now 13 passing.


## 2026-04-18 — Priority realignment (user directive)

## 2026-04-19 — Step 6 A/B Comparator Isolation + Prop-Scores Hygiene
- **Hygiene pass**: archived 15 stale experimental version_tags (41,793 docs)
  from `nba_prop_scores` → `nba_prop_scores_archive_stale_tags`; same for MLB
  (10,090 docs → `mlb_prop_scores_archive_stale_tags`). Canonical tags
  (`final-nba`, `final-mlb`) are the only writable tags left on the live
  collections. Indexes rebuilt via `ensure_indexes()`.
- **Stray `live` tag plugged**: `write_prop_scores()` (MLB legacy wrapper) now
  writes to `final-mlb` instead of `live`, eliminating the off-window writer.
- **Real-time engine → shadow tag**: `services/board/engine.py` now upserts
  under `{canonical}-rt` (`final-nba-rt` / `final-mlb-rt`). Legacy full-rebuild
  continues writing under the canonical tag. No writer collision on the live
  tag during the 48h window.
- **Drift auditor cross-tag**: `_audit_one_window` now queries BOTH tags per
  ledger entry. Report surfaces `rt_tag`, `legacy_tag`, `rt_present`,
  `legacy_present`, `rt_presence_ratio`, `legacy_presence_ratio`, and each
  divergence sample carries `rt_ledger`, `rt_materialized`, `legacy_current`
  side-by-side.
- **Reader unchanged**: live board readers (ferrari routes, optimized_sync,
  injury_triggered_rescore) stay pinned to `final-{sport}`.
- **Verified**: ledger flushed; direct `recompute_sport(tag=final-nba-rt)`
  wrote 3 docs to the shadow tag (visible under the new `-rt` distribution);
  audit report confirms `rt_presence_ratio=1.0` against `legacy_presence_ratio=0.0`
  for the seeded keys. Regression tests (5) still pass.


- Per user: next priority is Universal Board Migration + Legacy Writer
  Retirement (Step 6). No new features until that lands.
- New file `/app/memory/ROADMAP.md` created with P0/P1/P2 and explicit
  acceptance gates for Step 6.
- 48h observation clock deferred pending decision on blocker 1a (A/B
  comparator race between `write_versioned_scores(mode="replace")` in the
  legacy rebuild and `mode="upsert"` in the real-time engine, both sharing
  `version_tag=final-nba`). Raw evidence captured in ROADMAP.md §1a.



## 2026-04-18 — Canonical Stat-Window Contract (Ferrari h10_rate Clobber Fix)
- **Root cause**: `ferrari_tiers.py:1059-1072` was overwriting `prop["h10_rate"]`
  with `score.hit_rate_over` (L20/p_true-derived, 20-game window). Chart binding
  (`GameLogBarChart` over `game_logs[:10]`) stayed correct while the "L10 Hit"
  tile displayed L20 math under an "L10" label.
- **Evidence trail**: Grayson Allen PTS OVER 7.5 — chart 9/10 = 90%, tile 95%.
  Scope: 3/3 front-lines picks + 3/4 safe-haven picks were affected pre-fix.
- **Fix**: Stopped writing `score.hit_rate_over/under` into `h10_rate`. Model-
  derived rates moved to a separate namespace: `model_hit_rate_over`,
  `model_hit_rate_under`, `model_hit_rate_active`.
- **Invariant guard**: `_assert_canonical_hit_rate_invariant(prop)` + 
  `_guard_board_picks(picks)` installed at the exit of all Ferrari endpoints
  (front-lines, safe-haven, war-zone, oracle-apex). Asserts
  `h{5,10,20}_rate == round(l{5,10,20}_hits / window * 100, 1)`; on mismatch
  logs a `[CANONICAL_GUARD]` warning and auto-corrects from the count field.
- **Regression test**: `/app/backend/tests/test_hit_rate_canonical.py` (5 tests,
  all passing).
- **Files**: `routes/ferrari_tiers.py`, `tests/test_hit_rate_canonical.py`.



## 2026-04-16 — Non-Manual MLB Path Verified + CV Standardization
- Traced full non-manual MLB pipeline end-to-end: `scheduled_safety` → Coordinator → UnifiedPipeline(MLBAdapter) → Atomic Publish → Gemini enrichment
- Raw log trace: run_id `a8fa4aa3`, 2171 props → SH=2, FL=10, WZ=10, 22/22 Gemini enriched in 16.1s
- Fixed CV calculation inconsistency: standardized all `np.std()` calls to use `ddof=1` (sample std dev) across `oracle_apex_service.py`, `intel_suite_calculator.py`
- Files already using `ddof=1`: `vegas_regression_model.py`
- Discovered DB_NAME mismatch: server uses `pick_vision` (from .env), not `propvision`
- Added test endpoint `POST /api/v3/mlb/test-scheduled-sync` for non-manual path verification

## 2026-04-16 — Event-Driven Sync Architecture Complete
- Wired legacy data sync paths to emit BoardEvent coordinator triggers
- MLB sync engine, Ferrari tier service, MLB tier service all emit `scored_data_refresh`
- Closed the "open-door" publish gap
- Market Moves exit-reason classification (prop_removed, displaced_by_higher, etc.)
- 3-part prop identity keys (player|stat|line) for alternate lines

## 2026-04-14 - MLB Deep Ingestion Complete
- Built async 15x parallel ingestion engine
- Ingested 777 active MLB players with 149,989 game logs (3 seasons)
- Fixed dk_odds parameter mismatch in rolling_cache_manager.py

## 2026-04-14 - NBA Deep Ingestion + Advanced Overlay Complete
- NBA async ingestion: 559 players, 112,778 game logs, 181.4s, 0 errors
- Advanced Overlay: 15 metrics merged into every NBA game log

## 2026-04-14 - Automated Feature Discovery + Lasso Prediction Engine
- AutoFE pipeline: 366 engineered features → Lasso L1 selection
- 3 models trained for MLB, NBA

## 2026-04-22 — HARD CONSOLIDATION (P0)

Single universal path for sync / scoring / gate. No fallbacks, no
parallel branches, no legacy shims.

### Canonical path (the ONLY path now)
1. **Sync:** `services/universal_odds_sync.py :: sync_sport_props(sport)` →
   writes `{sport}_live_props`.
2. **Scoring:** `services/scoring/recompute.py :: recompute_sport(sport, tag)`
   driven by `services/scoring/adapters/{nba,mlb}_scoring.py` and gated by
   `services/scoring/scoring_stack.py`. Writes `{sport}_prop_scores` at
   `final-{sport}` AND `final-{sport}-rt`.
3. **Orchestration:** `services/master_sync.py :: run_master_sync(sport)` —
   sport-agnostic; `RebuildCoordinator.dispatch_master_sync(sport)` wraps
   it in the `UpstreamSyncLock`.
4. **Board reads:** `services/board/reader.py` reads
   `{sport}_prop_scores` at `final-{sport}-rt`.

### Files deleted (43 files, ≈ 10,000+ LOC)

Services:
- `services/engines/demon_goblin_engine.py`
- `services/odds_sync_service.py`
- `services/optimized_sync_engine.py`
- `services/nba_master_sync.py`
- `services/ferrari_tier_service.py`
- `services/mlb_tier_service.py`
- `services/oracle_apex_service.py`
- `services/mlb_oracle_apex_service.py`
- `services/cached_board_builder_service.py`
- `services/unified_pipeline.py`
- `services/adapters/nba_adapter.py`
- `services/adapters/mlb_adapter.py`
- `services/pipeline/master_steps.py`
- `services/sync_orchestration_service.py`
- `services/sync_service.py`
- `services/tier_builder_service.py`
- `services/prop_processor_service.py`
- `services/parlay_builder_service.py`
- `services/props_service.py`
- `services/roster_service.py`
- `services/stats_api_service.py`
- `services/stats_enrichment_service.py`
- `services/insights_sync_service.py`
- `services/data_integrity_service.py`
- `services/mlb_pipeline.py`
- `services/mlb_sync_engine.py`

Routes:
- `routes/picks.py`, `routes/parlays.py`, `routes/board.py`
- `routes/board_intel_v2.py`, `routes/cached_data.py`
- `routes/core_v3.py`, `routes/intel_sync.py`, `routes/legacy.py`
- `routes/roster_sync.py`, `routes/scheduler.py`, `routes/tiers.py`

Tests / Scripts:
- `tests/test_v3_api_refactoring.py`, `tests/test_v3_refactoring_phase3.py`
- `tests/test_v3_new_services.py`, `tests/test_odds_sync_standard_market.py`
- `tests/test_war_zone_cv_floor_removed.py`, `tests/test_betmgm_lookup.py`
- `scripts/validate_consensus.py`

### Callers rewired
- `services/rebuild_coordinator.py :: dispatch_master_sync` →
  `services/master_sync.run_master_sync(sport)` for NBA AND MLB (same
  code path, no `if sport == "nba"` branch).
- `services/rebuild_coordinator.py :: _execute_rebuild` → same.
- `server.py` → deleted `DemonGoblinEngine` import, init, and
  `register_all_routes(demon_goblin_engine, …)` kwargs. Adaptive sync
  callback rewired to `run_master_sync(db, "nba")`.
- `services/engines/adaptive_sync_engine.py` callback target is now
  the universal master sync.
- `routes/__init__.py` rewritten to remove all engine DI and drop
  references to the 11 deleted route modules.
- `routes/ferrari_tiers.py`:
  - Removed `get_ferrari_tier_service`, `get_service()`.
  - `/v3/ferrari/rebuild` → `run_master_sync(db, sport)`.
  - `/v3/ferrari/oracle-apex` → HTTP 410 Gone.
  - `/v3/ferrari/safe-haven?legacy=true` → HTTP 410 Gone.
  - `/v3/ferrari/all` now reads via `_serve_ferrari_tier` (universal).
- `server.py` MLB-health rebuild → `run_master_sync(db, "mlb")`.
- `scripts/init_database.py` → `run_master_sync` + `universal_odds_sync`.

### Data-path fixes caught during consolidation
- `services/scoring/prop_scores_store.py :: write_versioned_scores`
  now dedupes by `canonical_key` in replace mode. Upstream had rare
  collisions from unknown markets with empty stat_type.
- `services/scoring/adapters/nba_scoring.py :: build_context` now
  reads `stat_type` / `market_key` / `pp_layer` / `dk_layer` /
  `fd_layer` / `bol_layer` directly — the universal sync writes those
  fields, the old DemonGoblinEngine wrote `market` + `sharp_market`.

### Acceptance verification
- `POST /api/nba/sync/master` → 202, completes in ~116 s,
  `success=true, errors=[]`. `final-nba-rt` populated with tier
  picks.
- `POST /api/mlb/sync/master` → 202, completes in ~38 s,
  `success=true, errors=[]`. `final-mlb-rt` populated.
- All 6 Ferrari endpoints HTTP 200 with populated tier picks:
  NBA safe_haven=10 / front_lines=10 / war_zone=1; MLB
  safe_haven=0 / front_lines=0 / war_zone=10 (MLB off-hours).
- `GET /api/v3/admin/delta/engine-status` → 200.
- Grep proof: zero live references to `DemonGoblinEngine`,
  `NBAMasterSync`, `UnifiedPipeline`, `NBAAdapter`, `MLBAdapter`,
  `get_ferrari_tier_service`, `get_oracle_apex_service`,
  `get_mlb_tier_service`, `run_optimized_sync`, `sync_odds_to_mongo`.
- 108 / 108 consolidation-relevant pytest tests pass.

### Temporarily broken (known — rewire if UI needs them)
- Frontend endpoints served by deleted routes now return 404:
  `/api/v3/war-zone`, `/api/v3/goblin-vault`, `/api/v3/front-lines`,
  `/api/v3/player-with-badges/{name}`, `/api/v3/cached-props`.
  The canonical replacements live under `/api/v3/ferrari/*` and
  work. The frontend should be updated to use them.


## 2026-04-22 — Phase 2: Frontend Rewire + Legacy Collection Drop

Direct follow-up to the HARD CONSOLIDATION. No compatibility paths.

### Restored on the universal path
- `GET /api/v3/player-with-badges/{name}?sport={sport}` — new `routes/player.py`, reads `{sport}_prop_scores @ final-{sport}-rt` + `{sport}_master_hub_2026`. Returns player metadata + props + derived demons/goblins buckets.
- `GET /api/v3/board?sport={sport}` — also in `routes/player.py`. Board-wide player list grouped from `{sport}_prop_scores`. Replaces the deleted `/api/v3/cached-props`.

### Frontend rewired
- `useLiveOdds.js::fetchLiveOdds` → `/api/v3/board` (canonical).
- `useMostPopularBets` + `useTrapGraveyard` hooks + their `fetchMostPopularBets` / `fetchTrapGraveyard` helpers **deleted** (zero production callers). Replacement endpoints are intentionally NOT coming back; filter the universal board client-side if needed.
- All other fetches already pointed at `/api/v3/ferrari/*` or the universal sport endpoints.

### Legacy tier collections dropped (all writers already deleted)
Dropped via `db.drop_collection`:
- `ferrari_safe_haven`, `ferrari_front_lines`, `ferrari_war_zone`
- `ferrari_discarded`, `ferrari_scored`
- `mlb_ferrari_safe_haven`, `mlb_ferrari_front_lines`, `mlb_ferrari_war_zone`
- `mlb_safe_haven`, `mlb_front_lines`, `mlb_war_zone`
- `elite_safe_haven`, `elite_front_lines`, `elite_war_zone`
- `oracle_apex_analyzed`

(15 collections.)

### Callers rewired onto the canonical scored table
- `services/forward_testing_service.py` — reads `{sport}_prop_scores @ final-{sport}-rt` filtered by `tier`. Legacy `TIER_COLLECTIONS` map deleted.
- `services/injury_advantage.py` — same. Legacy `TIER_COLLECTIONS` + per-collection `TIER_LABELS` compressed.
- `services/market_moves_engine.py` — `_read_live_board` and the `ferrari_scored` candidate-pool fallback both rewritten to read `{sport}_prop_scores`.
- `routes/ferrari_tiers.py` — `/v3/mlb/ferrari/hrr-picks` rewired to read `mlb_prop_scores @ final-mlb-rt, tier=war_zone` instead of the deleted `mlb_war_zone` collection.
- `server.py` MLB health check reads `mlb_prop_scores` counts and invokes `run_master_sync(db, "mlb")` on empty.
- `scripts/init_database.py` already rewired in Phase 1.

### Files deleted (Phase 2)
- `services/board_intelligence_service.py` (legacy enrichment writer into `dg_cached_board`)
- `services/background_worker.py` (dead; only caller was the above)
- `routes/mlb_tiers.py` (duplicate with `ferrari_tiers.py` MLB endpoints)

### Stubbed / gutted
- `services/engines/adaptive_sync_engine.py`:
  - removed the fire-and-forget `run_board_intelligence_enrichment` call;
  - `_check_mlb_lineups` reduced to a documented no-op (was writing into the deleted `mlb_ferrari_safe_haven`).
- `config/db_config.py` and `config/collections.py` — legacy tier-name mappings stripped.

### Acceptance verification
- All 6 Ferrari endpoints + `/v3/board` + `/v3/player-with-badges` + `/v3/mlb/sharp/*` + `/v3/mlb/ferrari/hrr-picks` return HTTP 200 after collection drops.
- NBA: Safe Haven 10 / Front Lines 10 / War Zone 1; MLB: 0 / 0 / 10 (off-hours).
- `/api/v3/board?sport=nba` returns 119 players / 3859 total props with hub-enriched team/position/photo.
- Frontend landing loads clean (no white-screen, no console errors).
- 108 / 108 consolidation-relevant pytest tests pass.
- Grep proof: zero live references to any deleted tier collection in production code.


## 2026-04-22 — Phase 3: Universal Gate Engine

Gating is now a single engine with a single schema. Sport adapters only
compute normalized metrics.

### New package — `services/scoring/gates/`
- `schema.py` — `NormalizedMetrics`, `GateDetail`, `GateEvalResult`,
  `ReasonCode` (canonical reason codes: `gate_coverage_fail`,
  `gate_hit_rate_fail`, `gate_tp_fail`, `gate_tp_unavailable`,
  `gate_cv_fail`, `gate_edge_fail`, `gate_ceiling_fail`,
  `gate_context_fail`).
- `thresholds.py` — pure config: `THRESHOLDS[sport][tier][stat_family]`
  with stat aliases + odds-bucket-to-target-tier mapping. NBA + MLB
  populated from the old `_NBAGateSorter` / `MLBTierSorter` tables.
  NFL scaffold in place (drop in a stat family → works end-to-end).
- `engine.py` — `UniversalGateEngine.evaluate(metrics)` returns a
  `GateEvalResult`. Gate types: `coverage_gate`, `hit_rate_gate`,
  `tp_gate` (side-aware — UNDER uses `p_model_pct` floor), `cv_gate`
  (supports both `max` and `min_cv_floor`), `edge_gate`,
  `ceiling_gate`, `context_gate`.

### Sport-specific gate code DELETED
- `_NBAGateSorter.check_safe_haven_gates` / `check_front_lines_gates` /
  `check_war_zone_gates` / `_check` — removed. Class reduced to a thin
  sport-identity carrier.
- `MLBTierSorter.check_safe_haven_gates` / `check_front_lines_gates` /
  `check_war_zone_gates` — removed. `MLBTierSorter` now exists only as
  a carrier of MLB stat utilities consumed by `mlb_scoring.py`.
- `scoring_stack.compute_tier` fully rewritten — no sport-specific
  branches; looks up thresholds from `resolve_thresholds(sport, tier,
  stat_family)` and delegates to `UniversalGateEngine`.

### Persistence
- `prop_scores_store._SCORE_OUTPUT_FIELDS` gained `gate_eval`. Every
  scored prop now carries the full canonical gate output on the score
  doc so any UI / admin consumer can explain a pick's gating in the
  same structure regardless of sport.

### Verified
- NBA master sync: 116 s, success=true, tier dist
  `{'unqualified': 3294, 'front_lines': 34, 'safe_haven': 16, 'war_zone': 9}`
  (same shape as pre-refactor).
- MLB master sync: 88 s, success=true, tiers populated.
- All 6 Ferrari endpoints HTTP 200.
- `gate_eval` persists with canonical shape — verified on a live NBA
  safe_haven doc (stat_family=`reb`, passed_gates=5, failed_gates=0).
- 16 new unit tests in `tests/test_universal_gate_engine.py` (engine
  pass/fail paths, UNDER side-aware TP, CV cap override, NFL scaffold,
  unknown-gate-type forward compatibility, per-sport identical output
  schema). 124 / 124 regression tests pass.
- Grep proof: zero `def check_*_gates` methods, zero `SAFE_HAVEN_GATES`
  / `FRONT_LINES_GATES` / `WAR_ZONE_GATES` module-level dicts in live
  code.

### Adding a new sport now
1. Add `stat_family` alias block in `STAT_FAMILY_ALIASES[<sport>]`.
2. Fill `THRESHOLDS[<sport>][<tier>][<stat_family>]` with the gate dict.
3. Add an `ODDS_BUCKETS[<sport>]` entry.
4. Ship a scoring adapter that emits `NormalizedMetrics`.
No new gate-evaluation code.


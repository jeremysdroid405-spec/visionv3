# Field Ownership Audit — PropVision — 2026-05-03

Format: per-field blocks in the structure requested.
Scope: 20 fields + 7 cross-cutting systems.
Evidence: `file.py:line → function_name` from static code audit.
No patches applied.

---

## FIELD: opponent

**OWNER:** `nba_live_props.opponent_team` / `mlb_live_props.opponent_team`

**WRITERS:**
- `services/universal_odds_sync.py:1022 → _build_prop_record` — writes opponent_team at odds ingest
- `services/mlb_cached_board_builder.py:470 → run_mlb_board_build` — writes `opponent` onto cached_board from game log
- `services/context_badge_service.py:159-161 → get_todays_games` — writes `opponent` into badge docs from cached_board
- `services/picks_getter_service.py:1260 → get_goblin_vault` — copies opponent onto pick dicts
- `services/picks_getter_service.py:1700 → get_front_lines` — copies opponent onto pick dicts
- `services/picks_getter_service.py:2338 → get_cached_player` — copies opponent from `player_doc`
- `routes/ferrari_tiers.py:1486-1523 → _get_nba_tier_picks_from_scores` — live_props override layer (added 2026-05-03)

**READERS:**
- `services/dvp_service.py:955 → apply_dvp_to_prop` — reads with `.get("opponent") or .get("opponent_team")`
- `services/vegas_regression_model.py:536 → predict_batch` — reads with `.get('opponent') or .get('opponent_abbr')`
- `services/simulation_service.py:199 → _process_leg` — reads `.get("opponent", "")`
- `services/mlb_vision_intel.py:173 → _build_batch_prompt` — reads `.get('opponent', 'TBD')`
- `routes/ferrari_tiers.py:599 → enrich_mlb_intel_suite` — reads `opposing_team or opponent or ...` (4-way fallback)
- `routes/ferrari_tiers.py:1291 → merge` — reads `opponent_defensive_rank` from score doc
- `routes/player.py:431,463 → get_player_with_badges` — reads from `score_docs[0]` then falls back to `board_fallback.opponent`

**FALLBACKS:**
- `nba_cached_board.opponent` — player-grain, locked to `locked_event_id`, goes stale across schedule rolls
- `master_hub.opponent` — hub-level, updated at master_sync time
- `player_context_features.opponent_team` — parallel computation
- Multiple readers have `.get("opponent") or .get("opponent_team") or .get("opponent_abbr")` chains

**PROBLEM:**
- Stale `nba_cached_board.locked_event_id` produced the 2026-05-03 "Dylan Harper SAS vs POR" bug (actual matchup was MIN).
- 4 parallel writers; no source is declared authoritative.
- Reader-side merge at `ferrari_tiers.py:1486` was a 2026-05-03 patch that overrides cached_board with live_props, but other readers still trust cached_board.

**FIX:**
- Delete `opponent` writes from `mlb_cached_board_builder.py:470`
- Delete `opponent` writes from `context_badge_service.py:159-161`
- All readers query `live_props.opponent_team` by canonical_key only
- Return `null` if missing; frontend renders "—"
- Remove 4-way `.get() or .get()` fallback chains

**STATUS:** FRAGILE — read path patched today, writers still leak into multiple collections.

---

## FIELD: player_name

**OWNER:** `master_hub.display_name` (by `bdl_player_id`)

**WRITERS:**
- `services/odds_api_service.py:511` — ingest, uses raw name from Odds API
- `services/bdl_universal_sync.py` — writes `display_name` on master_hub
- `services/bdl_game_logs_sync.py` — writes `player_name` on game_logs (independent of master)
- `services/context_badge_service.py:159,356` — writes into badge docs
- `services/feature_hydration.py:393` — writes onto hydrated props
- `services/picks_getter_service.py:1258, 1698, 2308, 2336, 2575, 2738, 2751, 2784, 2919` — 9 separate write sites
- `services/sharp_edge_calculator.py:300, 403` — writes onto sharp-edge docs
- `services/dashboard_card_contract.py` — normalizes at card-build time

**READERS:**
- 40+ files. Every service that touches a pick reads `player_name`.

**FALLBACKS:**
- `display_name` vs `player_name` used interchangeably
- Normalization to lowercase-no-apostrophe in some paths (`De'Aaron Fox` → `deaaron fox`), not others
- Game-log `player_name` sometimes overrides master_hub `display_name` at enrichment time

**PROBLEM:**
- No canonical capitalization / apostrophe handling
- Can produce duplicate player cards when normalized vs raw names collide
- Traded players may be enriched with wrong display name for 24h until master_hub re-sync

**FIX:**
- Canonical owner = `master_hub.display_name` by `bdl_player_id`
- All collections reference by `bdl_player_id` only
- Display name derived at read time via a shared `resolve_display_name(bdl_id)` helper
- Delete 9 write sites in `picks_getter_service.py`

**STATUS:** FRAGILE

---

## FIELD: team

**OWNER:** `master_hub.team_abbr`

**WRITERS:**
- `services/universal_odds_sync.py:1022` — writes `team` onto live_props at ingest
- `services/bdl_universal_sync.py` — writes `team_abbr` on master_hub
- `services/context_badge_service.py:160` — writes into badge docs
- `services/feature_hydration.py:115, 392` — writes onto hydrated docs
- `services/picks_getter_service.py:298, 320, 1259, 1699, 2337` — 5 write sites
- `services/usage_spike_detector.py:112, 184` — writes onto usage-spike records

**READERS:**
- 50+ files. Every display / scoring / badge service reads it.

**FALLBACKS:**
- `team`, `team_abbr`, `team_name` used interchangeably
- `team` is 3-letter abbr in some docs, full name in others (`team_name`)
- Frontend normalizes with `team || team_abbr || team_name`

**PROBLEM:**
- Traded players show last-known team for up to 24h (cached_board + master_hub drift)
- Full name vs abbreviation inconsistency across docs

**FIX:**
- Owner: `live_props.team` (ingested with the prop — always current)
- Drop `cached_board.team` writes
- Drop `master_hub.team` reads from pick-display paths (keep for master-hub admin views only)
- Return `null` if missing

**STATUS:** FRAGILE

---

## FIELD: game_id / event_id

**OWNER:** `live_props.event_id`

**WRITERS:**
- `services/universal_odds_sync.py:1207 → _persist_prop` — writes event_id at ingest
- `services/master_sync.py:691` — propagates in enrichment queries
- `services/scoring/recompute.py:61 → _coerce_score_ctx_from_live` — copies into ScoringContext
- `services/scoring/recompute.py:433, 767 → recompute_sport` — writes onto score doc
- `services/scoring/adapters/nba_scoring.py:205, 2580` — reads raw_prop event_id
- `routes/ferrari_tiers.py:3387` — `"game_id": game.get("game_id")` (debug path only)

**READERS:**
- `services/delta/detector.py:119` — reads for delta key diffing
- 25+ files read at enrichment / display / history

**FALLBACKS:**
- None — `event_id` is propagated cleanly through the pipeline.

**PROBLEM:**
- None discovered.

**FIX:**
- N/A

**STATUS:** CLEAN

---

## FIELD: stat_type

**OWNER:** `live_props.stat_type` (raw from Odds API)

**WRITERS:**
- `services/universal_odds_sync.py:1022` — writes raw stat_type at ingest
- `services/master_sync.py:691` — propagates in queries
- `services/sharp_edge_calculator.py:302, 404` — writes onto sharp-edge docs
- `services/picks_getter_service.py:1265, 1705, 2372` — 3 write sites; `2372` does `stat_type_extracted or stat_type`
- `services/vegas_killer_model.py:1458, 1656, 1800` — writes onto model output
- `services/dvp_service.py:782` — writes onto DvP lookup
- `services/mlb_high_friction_model.py:1070` — writes onto friction model output

**READERS:**
- Every scoring / gate / display path.

**FALLBACKS:**
- Normalization layers:
  - `Hits+Runs+RBIs` (display)
  - `H+R+RBI` (internal)
  - `hits_runs_rbis` (snake_case stored)
  - `batter_hits_runs_rbis` (Odds API native)
- `stat_type_extracted` used as fallback when `stat_type` is the composite raw string

**PROBLEM:**
- `services/intel_suite_calculator.py:303 → _calculate_stability_index` calls `_extract_stat_values(active_logs, stat_type)` which fails on composite types → returns empty list → std_dev=0. Source of today's variance-tile bug on MLB H+R+RBI picks.
- 4 string forms for the same stat silently coexist; serializer/deserializer can disagree.

**FIX:**
- Canonical snake_case stored value (`hits_runs_rbis`)
- Display mapper at frontend only
- `_extract_stat_values` must handle composite stats explicitly (split on `+`, sum component values)

**STATUS:** FRAGILE — broken specifically for composite stat types.

---

## FIELD: line

**OWNER:** `live_props.line` (PrizePicks line from Odds API)

**WRITERS:**
- `services/universal_odds_sync.py:1022` — writes from Odds API payload
- `services/scoring/adapters/nba_scoring.py` + `mlb_scoring.py` — propagate into score doc

**READERS:**
- 100+ call sites. Scoring, gates, display, history, ticker.

**FALLBACKS:**
- Per-book line fields: `pp_line`, `dk_line`, `fd_line`, `mgm_line`, `bol_line` — separate truth per book for book-line drift detection. Not a fallback for the canonical `line`; coexists.

**PROBLEM:**
- None discovered at the field level.

**FIX:**
- N/A

**STATUS:** CLEAN

---

## FIELD: side / recommendation

**OWNER:** `live_props.recommendation` (`OVER` | `UNDER`)

**WRITERS:**
- `services/universal_odds_sync.py:1022` — writes at ingest
- `services/scoring/adapters/nba_scoring.py` + `mlb_scoring.py` — propagate

**READERS:**
- Everywhere. Also read as `direction` (alias) and `side` (normalized).

**FALLBACKS:**
- `direction` field is a second name for the same value
- `side` used in board_publisher
- Some code paths default to `OVER` when value missing

**PROBLEM:**
- 3 names (`recommendation`, `direction`, `side`) for same value — reader-side coercion layer
- Default-to-OVER assumption in `picks_getter_service.py` hides ingest errors

**FIX:**
- Canonical = `side ∈ {OVER, UNDER}`, Pydantic-enum-validated
- Drop `direction` alias
- Null = explicit ingest failure, do not default

**STATUS:** FRAGILE

---

## FIELD: p_true (stored as p_true_active)

**OWNER:** `prop_scores.p_true_active`

**WRITERS:**
- `services/scoring/recompute.py:444 → recompute_sport` — writes `p_true_active = ctx.p_model`

**READERS:**
- `services/scoring/metrics_builder.py:158 → build_metrics` — reads for metrics
- Gates engine, display layers, ranking computations

**FALLBACKS:**
- None. `p_true_active = None` means unscorable; readers handle explicitly.

**PROBLEM:**
- None discovered.

**FIX:**
- N/A

**STATUS:** CLEAN

---

## FIELD: edge (stored as edge_vs_fair)

**OWNER:** `prop_scores.edge_vs_fair`

**WRITERS:**
- `services/scoring/scoring_stack.py:285 → compute_vision_score` — writes `edge_vs_fair = round(edge, 4)`

**READERS:**
- `services/scoring/gates/engine.py` — edge_gate evaluator
- `routes/ferrari_tiers.py` — display / filter
- `services/board/publisher.py` — read for snapshot
- 20+ other files

**FALLBACKS:**
- Aliases: `vk_edge`, `true_edge`, `edge`, `edge_pct`, `edge_percentage`
- Historical values for related-but-different edge computations
- Frontend displays different aliases on different screens

**PROBLEM:**
- Users see different edge values on the same pick across screens (Goblin Vault vs Safe Haven vs Player Detail)
- Aliases are not guaranteed equal to `edge_vs_fair`

**FIX:**
- Canonical name = `edge`, value = `p_model - fair_prob`
- Compute once, persist once, display one name everywhere
- Delete alias fields `vk_edge`, `true_edge`, `edge_pct`, `edge_percentage` from score doc

**STATUS:** FRAGILE

---

## FIELD: hit_rate

**OWNER:** `prop_scores.hit_rate_over` / `prop_scores.hit_rate_under` (L20 window)

**WRITERS:**
- `services/scoring/adapters/nba_scoring.py:2464-2467, 3261-3262 → score` — writes L20 hit rates
- `services/scoring/adapters/mlb_scoring.py:598-599 → score` — writes L20 hit rates

**READERS:**
- `services/scoring/gates/engine.py` — hit_rate_gate evaluator
- `routes/ferrari_tiers.py:226-232 → _generate_vision_fallback` — side-aware pull chain
- `routes/ferrari_tiers.py:267-275` — displays as "L10" label while reading L20 field
- 15+ other readers

**FALLBACKS:**
- Windows: `h10_rate`, `l10_rate`, `l20_rate`, `hit_rate_over`, `hit_rate_under`, `h10_hit_rate`, `true_hit_rate`
- `_generate_vision_fallback` chain: `h10_rate → l10_rate → h10_hit_rate → hit_rate_over`

**PROBLEM:**
- Fallback labels `hit_rate_over` (L20) as "L10" in the template — surfaces as "95% L10" when actual L10 is 90% (recurring bug)
- 7 different window-named aliases

**FIX:**
- Window-explicit fields only: `hit_rate_l5`, `hit_rate_l10`, `hit_rate_l20` (per side)
- Delete generic `hit_rate_over` / `hit_rate_under`
- Template references specific window, never "hit_rate" alone

**STATUS:** FRAGILE — recurring display-vs-stored-window mismatch

---

## FIELD: cv

**OWNER:** `prop_scores.cv` (std_dev / mean of L30 game logs)

**WRITERS:**
- `services/scoring/adapters/nba_scoring.py:2499 → score` — writes `cv = std / mean`
- `services/scoring/adapters/mlb_scoring.py:195, 329, 490, 593 → score` — writes

**READERS:**
- `services/scoring/gates/thresholds.py` — cv_gate caps
- `frontend/src/components/dashboard/PlayerDetailPage.jsx` — Variance tile (2026-05-03 patch reads `cv` instead of `stability_index`)
- `services/intel_suite_calculator.py:306-327 → _calculate_stability_index` — **INDEPENDENT re-computation**, disagrees with cv

**FALLBACKS:**
- `cv_raw` (alias)
- `volatility_score` (derived scaled score)
- `volatility_label` (derived label)
- `intel_suite.stability_index.std_dev` — independently computed, ignores stored cv

**PROBLEM:**
- Two CV sources on one pick. `stability_index` returns std_dev=0 for composite MLB stats; `cv` on same pick is correct (0.79).
- Today's frontend patch bound Variance UI to `cv` directly; `stability_index` still computed server-side for other surfaces.

**FIX:**
- Delete `_calculate_stability_index` — engine computes `cv` once at scoring time
- Intel suite reads `cv` field, derives labels if needed
- Remove `std_dev` duplicate computation

**STATUS:** BROKEN — frontend patched 2026-05-03; server-side `stability_index` still produces wrong values on other surfaces.

---

## FIELD: ranking_score_v2

**OWNER:** `prop_scores.ranking_score_v2`

**WRITERS:**
- `services/scoring/recompute.py:493 → recompute_sport` — writes `ranking_score_v2 = _compute_ranking_score_v2(raw_gap, line, p_model)` (see `recompute.py:137-150`)

**READERS:**
- `routes/player.py:101 → get_player` — reads for display
- `routes/player.py:306, 315, 516 → get_players_by_tier` — `.sort([("ranking_score_v2", -1)])`
- `routes/ferrari_tiers.py:149, 1197, 1722 → _get_*_tier_picks_from_scores` — reads for merge
- `routes/ferrari_tiers.py:1376, 1604 → get_*_tier` — sort override key
- `routes/vacuum.py:276 → vacuum_alerts` — sort key
- `services/market_moves_engine.py:265 → get_market_moves` — projection field

**FALLBACKS:**
- `routes/ferrari_tiers.py:149-150` — falls back to `vision_score` when ranking_score_v2 is None
- Other readers tolerate None implicitly (`sort` places nulls last)

**PROBLEM:**
- Fallback to `vision_score` produces inconsistent sort order across tabs (some use v2, some fallback to vision)
- No test asserts `ranking_score_v2` is always populated post-recompute

**FIX:**
- Make `ranking_score_v2` mandatory in `prop_scores` Pydantic schema
- Drop the `vision_score` fallback in `ferrari_tiers.py:149`
- If compute fails, drop the pick (do not tier it)

**STATUS:** FRAGILE

---

## FIELD: tier

**OWNER:** `prop_scores.tier` ∈ `{safe_haven, front_lines, war_zone, unqualified}`

**WRITERS:**
- `services/scoring/tiering.py` — sets via gate evaluation
- `services/scoring/gates/engine.py` — evaluates gates, returns tier string
- `services/scoring/recompute.py` — persists via `ctx.tier`

**READERS:**
- `services/board/reader.py:47` — reads for board publish
- `routes/ferrari_tiers.py` — all tier endpoints filter by this

**FALLBACKS:**
- None — `unqualified` is the explicit fallback value.

**PROBLEM:**
- None discovered.

**FIX:**
- N/A

**STATUS:** CLEAN

---

## FIELD: active

**OWNER:** `prop_scores.active` (bool) + `live_props.active` (bool)

**WRITERS (prop_scores):**
- `services/scoring/recompute.py:439 → recompute_sport` — writes `active: True` unconditionally
- `services/board/scanner.py:39, 43 → scan_sport` — flips True/False based on tipoff
- `services/board/publisher.py:198, 209, 218, 220 → _persist` — flips True/False per publisher lifecycle
- `services/board/shadow_publisher.py:112, 122, 129, 131, 152 → _persist` — mirrors publisher for shadow board
- `services/scoring/tiering.py:78 → mark_retired_inactive` — flips False when live prop retires
- `services/badge_resolver.py:561 → add_flag` — writes True when adding badge flag
- `services/badge_resolver.py:576 → deactivate_flag` — writes False on flag deactivation

**WRITERS (live_props):**
- `services/master_sync.py` — flips False on game start
- `services/odds_api_service.py` — writes True at ingest

**READERS:**
- 20+ files. Two DIFFERENT QUERY PATTERNS used inconsistently:
  - `{"active": True}` (strict)
  - `{"active": {"$ne": False}}` (lenient; matches missing field too)

**FALLBACKS:**
- Missing `active` field behaves differently per query pattern (see above)

**PROBLEM:**
- **Root cause of 2026-05-03 watcher freeze.** Delta detector used `{"active": {"$ne": False}}` for reading scorable live keys but set-diffed against ALL scored rt keys including `active=False` stale rows. Masked 195 MLB keys silently for 18h.
- 8 independent writers with no single contract on when True vs False is correct.
- No audit of active-flip transitions.

**FIX:**
- Single helper `set_active(canonical_key, state, reason, timestamp)` — all 8 writers route through it
- Audit log collection `active_transitions` captures every flip with reason
- Readers standardize on ONE query pattern (`active: True` strict); missing is an invariant violation
- Write an invariant test: every canonical_key in `live_props.active=True` must have matching `prop_scores.active=True` under current `version_tag`

**STATUS:** BROKEN — detector patched 2026-05-03; root cause (multi-writer no-contract) remains.

---

## FIELD: computed_at / scored_at

**OWNER:** `prop_scores.computed_at` (written)

**WRITERS:**
- `services/scoring/prop_scores_store.py:452 → write_versioned_scores` — `computed_at = datetime.now(timezone.utc).isoformat()`
- `services/scoring/prop_scores_store.py:464, 498, 595` — writes at persist

**`scored_at` WRITERS:**
- **NONE.** Field is never populated anywhere in the codebase.

**READERS:**
- `services/board/drift_audit.py:105, 244, 273, 285, 389, 531, 538, 546` — reads `computed_at`
- `routes/health_sync.py:91 → _probe_prop_scores` — reads `scored_at` (always returns null)
- `services/shadow/shadow_capture_service.py:112, 191, 253` — reads `computed_at`
- `services/injury_advantage.py:485` — writes `computed_at` onto injury docs (separate collection)

**FALLBACKS:**
- N/A — `scored_at` simply doesn't exist; probe silently returns null.

**PROBLEM:**
- `/api/health/sync` probes `last_scored_at` for both sports
- Probe returns `null` for both → freshness monitoring silently dead for months
- Makes the entire `/api/health/*` surface non-functional for the most important metric (staleness)

**FIX (ONE-LINE):**
- Either write `scored_at = computed_at` in `prop_scores_store.py:452` block
- Or rename probe field to `computed_at` in `routes/health_sync.py:91`

**STATUS:** BROKEN — one-line fix candidate.

---

## FIELD: vision_intel

**OWNER (de jure):** `prop_scores.vision_intel`
**OWNER (de facto):** Nobody — 4 writer paths, 3 fallback cascade, MLB has zero real writers.

**WRITERS:**
- `services/vision_intel_service.py:553, 564 → _merge_intel_to_prop` — NBA Gemini path; writes real or fallback
- `services/mlb_vision_intel.py:378, 389, 441 → _merge_intel_to_prop` — MLB Gemini path; **NOT WIRED into `mlb_master_sync`** so never runs in live pipeline
- `services/master_sync.py:1355, 1424-1426 → _enrich_nba_board_vision_intel` — NBA-only; persists Gemini output to prop_scores
- `services/normalize_to_intel_mapping.py:224, 226` — writes via mapping
- `routes/ferrari_tiers.py:337 → _ferrari_response_for_tier` — reads from cache, writes onto pick at response time
- `routes/ferrari_tiers.py:1128 → _get_nba_tier_picks_from_scores` — override layer
- `routes/ferrari_tiers.py:2273 → _augment_mlb_picks` — `pick["vision_intel"] = _generate_vision_fallback(pick)` — the template lie

**READERS:**
- Frontend `UniversalPlayerCard.jsx`, `PlayerDetailPage.jsx`
- Backend card-contract at `services/dashboard_card_contract.py:177`
- `routes/player.py:122`

**FALLBACKS:**
- Level 1: `prop_scores.vision_intel` (Gemini NBA only)
- Level 2: `overlay_enrichment_cache` reads `/app/backend/data/{sport}_master_active_cache.json` (mtime 2026-04-23, stale)
- Level 3: `_generate_vision_fallback` produces "the math backs the over" template
- Level 4: Frontend shows empty

**PROBLEM:**
- 4 writer paths with no ownership.
- MLB: literally no live Gemini writer. Every MLB summary is template.
- NBA: Gemini writes, but template can overwrite at read time.
- Stale JSON file from Apr 23 overrides live score-doc fields via `overlay_enrichment_cache`.
- User cannot distinguish Gemini-authored from template.

**FIX:**
- Universal Vision Intel engine — already scoped in `/app/memory/VISION_INTEL_REFACTOR_SCOPE.md`
- Delete `_generate_vision_fallback` + `overlay_enrichment_cache` + stale JSON
- Single writer: `services/vision_intel/engine.py` writes structured `{badges, summary}` at master_sync time
- `null` when engine can't produce output; frontend renders "Vision unavailable"

**STATUS:** BROKEN (MLB is 100% template); FRAGILE (NBA has 4-layer override chain)

---

## FIELD: photo_url / headshot_url

**OWNER:** `master_hub.photo_url`

**WRITERS:**
- `services/bdl_universal_sync.py` — writes `photo_url` / `headshot_url` on master_hub at sync
- `services/picks_getter_service.py:278-320 → _get_photo_cache` — builds in-memory cache
- `services/picks_getter_service.py:1264, 1527` — writes onto pick dicts from cache

**READERS:**
- Frontend `PlayerHeadshot` component
- `routes/ferrari_tiers.py` — all tier reads
- `routes/player.py` — player detail

**FALLBACKS:**
- `photo_url` (canonical)
- `headshot_url` (alias)
- `/static/player-headshots/{nba_id}.png` (file-system fallback if no URL)

**PROBLEM:**
- `services/picks_getter_service.py:237` holds a **MODULE-GLOBAL `_photo_cache` dict with no TTL, no invalidation**. First bad lookup persists for lifetime of backend process.
- Traded players keep old team's headshot until pod restart.

**FIX (ONE-LINE CANDIDATE):**
- Delete the module-global `_photo_cache`
- Read master_hub directly each request (indexed lookup, ~1ms)
- OR add TTL+invalidation-on-master-sync

**STATUS:** FRAGILE — one-line fix candidate (delete the cache).

---

## FIELD: pp_projection_id

**OWNER:** `pp_projection_id_cache` collection (TTL 15 min)

**WRITERS:**
- `services/pp_multiplier_lab.py:1010 → seed_projection_ids_from_scraper` — external Chrome scraper path
- `services/pp_multiplier_lab.py:329` — writes during projection build
- `services/pp_multiplier_lab.py:554` — writes `"standard"` default when scraper missing

**READERS:**
- `services/pp_multiplier_lab.py:735 → get_projection_id` — preferred source
- `routes/pp_multiplier_lab.py:155, 190 → multiplier_lab_*` endpoints

**FALLBACKS:**
- `"standard"` default when cache miss

**PROBLEM:**
- External Chrome scraper is out-of-band. If runner is down, cache expires, all projections get `"standard"` → demons/goblins look like standards → multiplier math wrong.
- No visible alarm when scraper is stale.

**FIX:**
- Surface PP scraper health in `/api/health/sync`
- Hard-fail PP-multi-leg pricing when scraper age > 30 min
- Default to `null`, not `"standard"`

**STATUS:** FRAGILE — external dependency, no health surface.

---

## FIELD: odds_type

**OWNER:** `pp_projection_id_cache.odds_type` normalized by `pp_multiplier_lab.py:_norm_odds_type`

**WRITERS:**
- `services/pp_multiplier_lab.py:329 → build_projection` — writes from scraper's `attrs.odds_type`
- `services/pp_multiplier_lab.py:554 → _fallback_projection` — defaults to `"standard"`

**READERS:**
- `services/pp_multiplier_lab.py:234 → _norm_odds_type` — normalizer
- `services/pp_multiplier_lab.py:254, 702, 705` — grouping / filtering
- MongoDB index `ix_proj_odds_type` (line 135-136)

**FALLBACKS:**
- Defaults to `"standard"` when raw value missing

**PROBLEM:**
- Same as `pp_projection_id` — tied to external scraper.

**FIX:**
- Same as `pp_projection_id`. Surface freshness; null on miss.

**STATUS:** FRAGILE

---

## FIELD: start_time / game_start_utc / commence_time

**OWNER:** `live_props.game_start_utc` (datetime) + `live_props.commence_time` (ISO string)

**WRITERS:**
- `services/universal_odds_sync.py:1220 → _persist_prop` — writes `commence_time` string
- `services/scoring/recompute.py:67, 442 → _coerce_score_ctx_from_live, recompute_sport` — propagates `game_start_utc` into score doc
- `routes/live.py:211, 411, 629, 738, 980, 1059 → live_scores_endpoints` — writes `start_time` onto ticker rows (NBA + MLB, live and fallback paths)

**READERS:**
- `services/board/scanner.py` — uses for tipoff detection
- `routes/ferrari_tiers.py:1486-1523 → _get_nba_tier_picks_from_scores` — exposes as ISO string to API
- `frontend/UniversalPlayerCard.jsx` — parses ISO for matchup row (2026-05-03)
- `routes/live.py` — ticker

**FALLBACKS:**
- 3 parallel names: `game_start_utc` (datetime), `commence_time` (string), `start_time` (ticker-only alias)
- Some readers do `datetime.fromisoformat(str(...).replace("Z", "+00:00"))` chains
- Pick card fix today stringifies `game_start_utc` at API boundary → frontend re-parses

**PROBLEM:**
- Three names for the same value
- String/datetime coexistence; readers do unnecessary round-trips

**FIX:**
- Store ONE datetime field (`game_start_utc`)
- Serialize to ISO at API boundary only
- Delete `commence_time` and `start_time` aliases from score docs (keep `commence_time` on live_props only if it's the raw Odds API string we need to preserve)

**STATUS:** FRAGILE

---

# Cross-cutting audit

## SYSTEM: _SCORE_OUTPUT_FIELDS allowlist

**LOCATION:** `services/scoring/prop_scores_store.py:21`

**PURPOSE:** Hand-maintained tuple of ~200 field names the score-doc writer will persist.

**WRITERS (of allowlist):** Developer commits — field must be added manually on every schema change.

**READERS:**
- `services/scoring/prop_scores_store.py:374 → _project_score_doc` — filters writes via `for k in _SCORE_OUTPUT_FIELDS`
- `services/scoring/recompute.py:507` — uses for filtering
- `services/scoring/adapters/base.py:182` — documents the contract

**PROBLEM:**
- SILENTLY DROPS any field not in allowlist. No log. No warning. No error.
- Fields dropped silently this session until manually added: `hetero_sigma_base`, `hetero_sigma_adjusted`, `hetero_sigma_multipliers`.
- Drift between the 3 allowlists (`_SCORE_OUTPUT_FIELDS`, `_IDENTITY_FIELDS`, `_UNIVERSAL_POOL_FIELDS`) is an accident waiting to happen.

**FIX (ONE-LINE PATCH POSSIBLE):**
- Add `logger.warning` in `_project_score_doc` when a key from `context_out` is not in the allowlist.
- Proper fix: Pydantic model; writing a non-schema field raises `ValidationError`.

**STATUS:** BROKEN (silent-drop mechanism).

---

## SYSTEM: Score doc allowlists (plural!)

**LOCATIONS:**
- `services/scoring/prop_scores_store.py:21` → `_SCORE_OUTPUT_FIELDS` (~200 fields)
- `services/scoring/prop_scores_store.py:353` → `_IDENTITY_FIELDS` (8 fields)
- `services/scoring/prop_scores_store.py:361` → `_UNIVERSAL_POOL_FIELDS` (4 fields: active, inactive_reason, active_changed_at, game_start_utc)

**PROBLEM:**
- 3 allowlists maintained separately
- A field in `_UNIVERSAL_POOL_FIELDS` but not in `_SCORE_OUTPUT_FIELDS` can disagree
- No single source of truth for "what belongs on a score doc"

**FIX:**
- Replace all 3 with single Pydantic `ScoreDocument` model
- `_IDENTITY_FIELDS` / `_UNIVERSAL_POOL_FIELDS` become model method partitions

**STATUS:** FRAGILE

---

## SYSTEM: Vision Intel fallback cascade

**LEVELS (in order of preference at read time):**
1. `prop_scores.vision_intel` — Gemini-authored, NBA only
2. `overlay_enrichment_cache` — reads `/app/backend/data/{sport}_master_active_cache.json` (Apr 23, stale)
3. `_generate_vision_fallback` — templated string `"{player} is hammering {stat}..."`
4. Frontend shows empty

**PROBLEM:**
- Each layer silently takes over when the one above is empty
- User has no way to distinguish real from template
- MLB never reaches Level 1 (no writer), goes Level 2 (stale) → Level 3 (template)

**FIX:**
- See Vision Intel Refactor scope
- Single writer, single reader, null on miss

**STATUS:** BROKEN

---

## SYSTEM: Cached JSON enrichment files

**FILES:**
- `/app/backend/data/nba_master_active_cache.json` — mtime `2026-04-23 00:40` (10 days stale)
- `/app/backend/data/mlb_master_active_cache.json` — mtime `2026-04-23 00:25` (10 days stale)

**LOADED BY:** `routes/ferrari_tiers.py:281-337 → overlay_enrichment_cache`

**CALLED BY:** `ferrari_tiers.py:2265, 4170, 4209, 4247` — every tier read + MLB batch endpoints

**WRITERS:** **None found in codebase.** Files are orphaned.

**PROBLEM:**
- No live writer — whatever used to populate these is gone
- Content overrides live score-doc fields (per function name "overlay")
- Every tier read pulls 10-day-old enrichment

**FIX:**
- Delete the JSON files
- Delete `overlay_enrichment_cache` + all 4 call sites
- Verify nothing depends on fields only those files provide

**STATUS:** BROKEN (dead code silently overriding live data)

---

## SYSTEM: dg_cached_board usage

**COLLECTION ROW COUNT:** 0 (verified via Mongo count)

**REFERENCED IN (14 files):**
- `repositories/board_repo.py:21-23`
- `routes/command.py:232`
- `routes/player.py:164, 189, 202, 216, 345, 364, 441`
- `routes/ferrari_tiers.py:852, 943, 1010, 1479-1480, 1584, 1614, 1851, 1966, 3239, 3251, 3257, 3267, 3280`
- `services/picks_getter_service.py:8, 231`
- `services/mlb_cached_board_builder.py:21, 25`

**PROBLEM:**
- Dead read path. Collection is empty; code treats zero-match as "no enrichment needed".
- Every `player.py` endpoint calls `_build_nba_cached_board_index` which queries an empty collection — wasted query on every player detail page load.

**FIX:**
- Delete `dg_cached_board` references
- If any reader genuinely depends on a field from it, re-source from `live_props` or `master_hub`
- Delete `nba_cached_board` writes unless confirmed consumer exists

**STATUS:** BROKEN (dead code)

---

## SYSTEM: version_tag usage

**ACTIVE TAGS OBSERVED:**
- `final-nba-rt` — canonical live NBA tag (~2,400 active docs)
- `final-mlb-rt` — canonical live MLB tag
- `final-mlb-rt-shadow` — parallel run, NOT read by API
- `stage2-verify-nba` / `stage2-verify-mlb` — historical debugging tags (144k docs in MLB)
- `recompute-<timestamp>-<hash>` — per-recompute snapshots, never cleaned up

**WRITERS:**
- `services/scoring/recompute.py` — writes caller-provided version_tag
- `routes/scores.py:85 → recompute_all` — default tag per version variant
- Manual admin calls

**READERS:**
- `routes/ferrari_tiers.py` — defaults to `final-nba-rt` / `final-mlb-rt`
- `services/delta/detector.py` — reads `rt_tag` param
- `services/board/*` — various

**PROBLEM:**
- Free-form string, 5+ active tags, no enum
- No TTL cleanup — `recompute-*` tags accumulate indefinitely (144k docs in MLB)
- Reader defaults to `final-mlb-rt`; if a recompute writes to a different tag accidentally, reader silently returns empty
- Multi-session agents have repeatedly confused which tag is authoritative

**FIX:**
- Enum in Pydantic: `VersionTag = Literal["final-nba-rt", "final-mlb-rt", ...]`
- TTL cleanup job: drop `recompute-*` and `stage*` tags older than 48h
- Contract test: every read path specifies tag explicitly, no defaults

**STATUS:** FRAGILE

---

## SYSTEM: Active-row writers

**WRITERS WRITING `active: True`:**
- `services/scoring/recompute.py:439 → recompute_sport`
- `services/scoring/tiering.py:78 → mark_retired_inactive` (writes False; shown for completeness)
- `services/board/scanner.py:39 → scan_sport`
- `services/board/publisher.py:198, 218 → _persist`
- `services/board/publisher.py:493 → stamp_longevity_on_picks`
- `services/board/shadow_publisher.py:112, 129, 152 → _persist`
- `services/badge_resolver.py:561 → add_flag`

**WRITERS WRITING `active: False`:**
- `services/board/scanner.py:43 → scan_sport`
- `services/board/publisher.py:209, 220 → _persist`
- `services/board/shadow_publisher.py:122, 131 → _persist`
- `services/scoring/tiering.py:78 → mark_retired_inactive`
- `services/badge_resolver.py:576 → deactivate_flag`

**READER PATTERNS (inconsistent):**
- `{"active": True}` — strict match
- `{"active": {"$ne": False}}` — lenient (matches True + missing)
- Both patterns coexist across 20+ reader files

**PROBLEM:**
- No contract on when True vs False is correct
- No audit of flip transitions
- Today's watcher bug: delta detector used lenient pattern for live_props reads but set-diffed against ALL rt keys (both active and inactive) → stale `active=False` rt rows masked 195 scorable MLB keys

**FIX:**
- Single `set_active(db, coll, canonical_key, state, reason, timestamp)` helper
- `active_transitions` audit collection capturing every flip
- All readers use strict `{"active": True}` pattern
- Invariant test: every canonical_key in `live_props.active=True` has matching `prop_scores.active=True` under current version_tag

**STATUS:** BROKEN — detector patched 2026-05-03; multi-writer problem remains.

---

## SYSTEM: Scheduler / watcher freshness logic

**SCHEDULERS:**
- `hourly_nba_master_sync` — `scheduler_jobs._id = "hourly_nba_master_sync"`
- `hourly_mlb_master_sync` — same pattern
- `nba_l5l10_batch_{1..5}` — daily at 09:00-09:08 UTC
- `services/scheduled_sports.py` — registers sport-specific jobs

**WATCHERS:**
- `services/delta/detector.py → detect_deltas` — called every 20s (NBA) / 30s (MLB) per `services/pipeline/delta_steps.py`
- Watermark-based progression on `live_props.updated_at`

**FRESHNESS PROBES (readers):**
- `routes/health_sync.py:69 → _probe_live_props` — `sync_age_sec = now - max(live_props.updated_at)`
- `routes/health_sync.py:91 → _probe_prop_scores` — reads `scored_at` (returns null, see FIELD above)
- `routes/health_sync.py:209 → _probe_delta_engine` — reads `delta_watermarks` collection
- `routes/health_sync.py:219 → _probe_watchers` — reads watcher heartbeat

**PROBLEM:**
- MLB master_sync actual last-run: 2026-05-03 05:14 UTC (18.5h ago at audit time)
- `hourly_mlb_master_sync` next-run: 2026-05-04 00:13 UTC (+50 min)
- Scheduler rows look healthy but jobs silently skipped/failed for 18h — no visible log entries, no alarm
- `/api/health/sync` returns `overall_status: "critical"` right now — nothing acts on it, no UI surface

**FIX:**
- Root-cause investigation: why `hourly_mlb_master_sync` skipped 18 runs
- Alarm when scheduler_age > (interval × 2)
- Surface `/api/health/sync` status in admin UI (temp, per user 2026-05-03 request)

**STATUS:** BROKEN

---

# Top 10 SSOT violations — ranked by impact

1. **`_SCORE_OUTPUT_FIELDS` silent drops** — loses data without warning; burned us 3x this session. (BROKEN)
2. **`active` multi-writer no-contract** — root cause of today's watcher freeze. (BROKEN)
3. **`vision_intel` 4-writer cascade** — MLB 100% templated. (BROKEN)
4. **`scored_at` never written** — kills health endpoint. (BROKEN; one-line fix.)
5. **Stale JSON overlay_enrichment_cache** — 10-day-old data overriding live. (BROKEN)
6. **`dg_cached_board` queries empty collection in 14 files** — dead code treated as "no enrichment". (BROKEN)
7. **`version_tag` free-form, no cleanup** — 144k stage-tagged MLB docs; easy to silently freeze readers. (FRAGILE)
8. **`opponent` 4-source merge** — today's Dylan Harper bug; writers still leak. (FRAGILE, read-patched)
9. **`hit_rate` 7 window-named aliases** — recurring "L10 says 95% but real L10 is 90%" bug. (FRAGILE)
10. **`photo_url` zombie module cache** — stale headshots for process lifetime. (FRAGILE; one-line fix.)

---

# Proposed patch plan — exact file:line

## Tier 0 — Obvious one-line fixes (await approval)

1. **`services/scoring/prop_scores_store.py:452` — write `scored_at` alongside `computed_at`.**
   Delta: add `scored_at = computed_at` below the existing line. 1 line. Unblocks `/api/health/sync`.

2. **`services/scoring/prop_scores_store.py:374 → _project_score_doc` — log on allowlist drop.**
   Delta: change silent `for k in _SCORE_OUTPUT_FIELDS` filter to warn when `context_out` contains keys NOT in the allowlist. 3 lines.

3. **`services/picks_getter_service.py:237 → _photo_cache module-global** — delete cache, read master_hub each request.**
   Delta: remove dict init + cache read/write paths in lines 237, 278-320, 376-399. ~12 lines deleted.

## Tier 1 — Data-layer cleanup (1 session each)

4. Fix `active` contract — `services/board/set_active.py` helper + `active_transitions` audit collection. Migrate 8 call sites.
5. Consolidate `version_tag` — enum + TTL cleanup job.
6. Delete `dg_cached_board` reads — 14 files.
7. Delete `*_master_active_cache.json` reads — `ferrari_tiers.py:281-337 → overlay_enrichment_cache` + 4 call sites.

## Tier 2 — Schema contracts (2 sessions)

8. Pydantic `ScoreDocument` replaces 3 allowlists.
9. Pydantic `LivePropDocument` for ingest.
10. Per-field ownership enforcement — delete duplicate writers for `opponent`, `player_name`, `team`, `hit_rate`, `edge`.

## Tier 3 — Vision Intel refactor

11. Ship per `/app/memory/VISION_INTEL_REFACTOR_SCOPE.md`.
12. Delete `_generate_vision_fallback`, `overlay_enrichment_cache`, stale JSONs.

## Tier 4 — Observability

13. Admin `/admin/health` page reading `/api/health/sync` + `/api/health/contracts` + `/api/health/board`.
14. Admin-only red/amber/green status pill in dashboard header.
15. Weekly regression test on cron — asserts every cross-service contract.

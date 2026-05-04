# NBA/MLB Betting Analytics — Product Requirements

## Original Problem Statement
Restructure React/FastAPI betting app into a 100% Local-First, ID-based multi-sport analytics engine with Universal Opportunity and Probability models. Every scoring/model change must be backed by regression + mutation tests. Integrate Google/Apple OAuth and Stripe.

## Architecture
- **Frontend:** React + Shadcn UI; reads `/api/v3/ferrari/*` tier endpoints.
- **Backend:** FastAPI. Scoring in `services/scoring/` with per-sport adapters feeding a universal gate engine.
- **Board flow:** `*_live_props` → `NBAScoringAdapter` / `MLBScoringAdapter` → `*_prop_scores` (tiered) → `services/board/reader.py` → ferrari_tiers routes.
- **Watcher:** `services/delta/detector.py` runs every 20s (NBA) / 30s (MLB), diffs `live_props` vs `*_prop_scores @ active=True`, queues dirty keys for `_engine_p_over` re-scoring.

## Done in this session
- **Heteroscedastic Sigma (NBA Phase 2 — shipped):** Bucket multipliers (minutes × line) built from 272 settled outcomes; sigma rescaled per prop. 33 regression tests + 3 mutation tests, all green.
- **Reverted Phase 1 additive debias** (was overcorrecting volume stars).
- **Lowered NBA SH gates:** `vision_score_gate.min` 85→80; `edge_gate.min` 0.01→0.0.
- **Pick card matchup row:** "vs OPP · TipTime" — backend exposes `game_start_utc` + `event_id` via `_merge_score_with_board`; frontend renders compact row in `UniversalPlayerCard.jsx`.
- **Live ticker fix:** `routes/live.py` — keeps recent Finals (12h window), augments with today/tomorrow scheduled tipoffs. NBA + MLB.
- **Watcher bug fix (P0):** `services/delta/detector.py` — `new_keys` set-diff now subtracts only `active=True` rt keys. Stale `active=False` rows no longer mask scorable canonical_keys. Verified: MLB rescored 195 silently-blocked picks immediately after fix.
- **Variance tile (frontend):** `PlayerDetailPage.jsx` Variance now reads `volatility_score`/`cv` instead of broken `intel_suite.stability_index` (which returned std_dev=0 for composite MLB stat types).
- **SSOT Enforcement Phase 1 (2026-05-04):** shipped `services/field_ownership/` (registry+accessors+validators). Migrated `scored_at` (100% populated both sports) + `opponent` (0 team==opp violations). 12 contract tests green.
- **SSOT Enforcement Tier F (2026-05-04) — TTL self-prune LIVE:**
  - `ttl_at_7d_nonlive_ix` Mongo TTL index on `{nba,mlb}_prop_scores.ttl_at` with 7-day `expireAfterSeconds`. `_project_score_doc` stamps `ttl_at` ONLY when `version_tag` ∉ `_LIVE_VERSION_TAGS` (`final-nba`, `final-mlb`, `final-nba-rt`, `final-mlb-rt`). Live docs never carry the field → immune by absence.
  - Live exclusion proof: 0 leaked live docs. NBA 5,012 TTL-eligible (4,920 will expire next sweep, 92 in 7-day window). MLB 33,729 TTL-eligible (2,923 >7d).
  - Rollback one-liner: `db.{sport}_prop_scores.dropIndex("ttl_at_7d_nonlive_ix")`. No scheduler, no new cleanup service — Mongo handles it.
  - 5 remaining Tier F deletions (`edge_pct`/`vk_edge` stamping, `hit_rate_over` migration, `direction` deletion, `master_active_cache.json`, `dg_cached_board`) explicitly blocked — each has 7-34 reader sites requiring dedicated migration sessions.
  - **117/117 tests green, permanent repair ~85% complete. Six-tier SSOT campaign complete.**
- **SSOT Tier F #2 (2026-05-04) — `hit_rate_over` → `hit_rate_l20` reader migration:**
  - 11 canonical-first reader sites (routes/debug_snapshots, routes/player, routes/ferrari_tiers, services/dashboard_card_contract, services/scoring/metrics_builder) now read `hit_rate_l20` first, `hit_rate_over` only as fallback for pre-dual-write docs.
  - DB parity verified: 4,815 dual-written docs checked, 0 mismatches; 105 contract tests green.
  - Deletion deferred: 41,000 legacy-only docs (`6,727 NBA + 34,272 MLB`) must age out via 7-day TTL before readers can drop the fallback. Re-check parity in ~8 days.
- **SSOT Tier F #1 (2026-05-04) — `direction` alias stamping deleted:**
  - Dropped `"direction"` writes from all response-building paths: `_merge_score_with_board` (+ defensive `prop.pop("direction")` after board-entry clone because `nba_cached_board.props[]` still carries legacy alias), MLB prop-merge block, MLB detail-page backfill, top-5 goblins response, `picks_getter_service.{goblin_vault, front_lines, cached_player}`, `mlb_cached_board_builder._enrich_prop`.
  - Migrated 8 backend readers to `recommendation → side → direction` canonical-first order (ferrari_tiers resolver + 4-tuple lookup + content-hash + UNDER filter + UNDER-rewire, dashboard_card_contract, board/adapters/base, market_moves_engine).
  - Removed `"direction"` from `PICK_CARD_REQUIRED_KEYS` contract lockdown; new regression test `TestDirectionAliasStampingRemoved` asserts live API never returns `direction` key (6 endpoints × 40 picks confirmed 0).
  - Contract-enforcer `missing_or_null=['direction']` errors eliminated from backend logs.
  - **109/109 hit-rate + field-ownership tests green. Tier F remaining: `direction` reader-fallback deletion (Tier G once frontend purges `pick.direction`); `edge_pct`/`vk_edge` API stamping removal; `dg_cached_board` drop; `ScoreDocument` `extra="forbid"` flip.**
- **SSOT Tier F #2 (2026-05-04) — `edge_pct` / `vk_edge` / `true_edge` API alias deletion:**
  - Deleted 9 response-level alias stamps across `ferrari_tiers.py` (7), `player.py` (1), `debug_snapshots.py` (1).
  - Migrated 4 backend reader sites (rank tiebreaker, `lasso_high_edge` badge, HRR war-zone Mongo filter, value_score) to canonical `edge_vs_fair` only; 0 alias `.get()` reads remain in routes or response-building services.
  - Defensive `.pop("edge_pct"/"vk_edge"/"true_edge")` on the NBA `_merge_score_with_board` board-entry clone and MLB `dict(sc)` base shape to guarantee no upstream leakage.
  - Debug endpoints migrated too: `/api/v3/debug/safe-haven-rejects` sort key, `/api/v3/debug/shadow_board/compare` projection + `avg_edge_vs_fair` metric + rank-tuple metadata.
  - New regression test `TestEdgeAliasStampingRemoved` — 6 live-API parametrized cases assert zero alias leakage and 100% canonical field presence.
  - Live smoke (6 endpoints × 41 picks): `edge_pct=0 vk_edge=0 true_edge=0 edge_vs_fair=41/41`.
  - **115/115 tests green.**
- **SSOT Tier F #3 (2026-05-04, Option C) — legacy `dg_cached_board*` cleanup:**
  - Full migration off `nba_cached_board` / `mlb_cached_board` is a multi-session Option-D plan (18 readers + 8 writers; collections hold unique enrichment data not present elsewhere). Out-of-scope for Tier F.
  - This session: dropped orphaned `dg_cached_board_temp` (122 docs, last write 2026-04-23, zero active readers/writers), removed the `board_cache_temp` mapping + `BOARD_CACHE_TEMP_NBA` constant + `server.py` startup index-creation call so it cannot be recreated, renamed `self.dg_cached_board` → `self.cached_board` in `board_intelligence_engine.py`, and migrated every misleading "live data source" docstring/comment to the canonical `nba_cached_board` name.
  - New regression test `TestDgCachedBoardRetired` (4 cases): asserts both legacy collections are absent in Mongo, live cached_board collections still exist, and a static AST-style scan finds zero `db["dg_cached_board*"].find/.update/...` patterns in non-archive/non-test code.
  - **119/119 tests green.** Architecture clearly documented: `dg_cached_board*` is gone forever; `nba_cached_board` / `mlb_cached_board` remain live until Option-D phased migration.
- **SSOT Tier F #4 (2026-05-04) — `ScoreDocument` strict mode LIVE:**
  - Flipped `model_config.extra` from `"allow"` → `"forbid"`. Silent score-doc field drift is now structurally impossible.
  - Added 108 `Optional[…] = None` declarations grouped by domain (distribution probability layer, ECDF/calibration, NBA availability guard, NBA rate × minutes, NBA recency μ blend, NBA shadow projections, hetero σ, per-stat debias, RFA minutes, MLB Empirical-Bayes, MLB pitcher/batter μ overrides, LOM audit, war-zone CV, ceiling rate).
  - `SSOT_PYDANTIC_STRICT` env default flipped to `true`; semantics clarified — with `extra="forbid"` LIVE the schema raises on its own, the env flag governs only the `validate_score_document` re-raise vs WARN-and-continue mode (latter is an emergency escape hatch).
  - New parity-guard suite (`tests/test_score_document_parity.py`, 5 cases): `extra="forbid"` lock, projected ⊆ declared, declared-extras count tracked in `_ALLOWED_DECLARED_EXTRAS` (currently 9), required-fields check, live-DB scan finds 0 undeclared on 4,000 docs.
  - Fixed `test_schema_accepts_valid_doc` inversion — undeclared fields now MUST raise `ValidationError`.
  - New `/api/health/score-document-schema-parity` read-only probe — returns `parity_ok`, `extras_setting`, `declared_count`, `projected_count`, `missing_declarations`, `declared_extras`. Zero schedulers / writers.
  - Verified: ~900 prepared docs through dry-run NBA+MLB at multiple limits — 0 ValidationErrors. Live recompute NBA(20)/MLB(20) wrote 30/30 docs. Backend logs clean post-flip. **124/124 tests green.**
  - **Six-tier SSOT campaign complete.** Score-doc write boundary is now strictly typed and CI-locked.
- **`vision_score == 0.0` false-zero fix (2026-05-04) — NARROW Path B promotion:**
  - Single-function patch in `backend/services/scoring/recompute.py::_apply_vision_score_normalization`: when v1 percentile collapses to 0.0 (negative-edge picks) AND `vision_score_v2 > 0`, substitute v2 into `vision_score`. Picks with v1>0 keep their slate-percentile so `vision_score_gate` selectivity (calibrated to v1's `min: 80` SH / `min: 60` WZ) is unchanged.
  - First attempt (unconditional v2 promotion) collapsed NBA Safe Haven / War Zone counts to 0/0; reverted to narrow form per user direction.
  - Result on `final-nba-rt`: Safe Haven 0→12, War Zone 0→8, Front Lines 48 unchanged. `vision_score==0.0` 860→249. Maxey/Harden/Embiid PRA / Dylan Harper now pass gates with vs ≥86. **135/135 tests green; API smoke NBA SH=7 FL=12 WZ=6, MLB SH=10 FL=13 WZ=9.**
- **`momentum_data` SSOT registration (2026-05-04) — NBA only, bounded patch:**
  - Added FieldSpec entry to `services/field_ownership/registry.py`: owner=`prop_scores`, writer=`master_sync._enrich_nba_momentum`, fallback=NONE, null_policy=return_null, with full join-chain documentation (`bdl_player_id → master_hub.team_abbr → opponent_abbr → defensive_momentum_cache`).
  - Declared `momentum_data: Optional[Dict[str, Any]] = None` on `ScoreDocument` (was implicit through `_SCORE_OUTPUT_FIELDS` only); added to `_ALLOWED_DECLARED_EXTRAS` since the writer is `master_sync` post-recompute, not the projector.
  - `_enrich_nba_momentum` INFO log extended: `total_candidates`, `enriched_count`, `skipped_count`, `coverage_pct`, bucketed `skip_reasons` (no_bdl_id, no_team_lookup, no_event_match, no_canonical_key, no_stat_type, team_not_in_event, momentum_calc_failed). Skip-reason counters were pre-existing; only the surfacing changed.
  - Live: NBA coverage 99.1 % → **99.42 %** (14 missing, all `no_bdl_id`). `/api/health/score-document-schema-parity` reports `parity_ok=true`, declared_extras count 9 → 10. **123/123 tests green.**
  - **Out of scope** (documented in registry notes as missing feature, not bug): MLB momentum writer, `nba_cached_board.props[]` mirror coverage (~88.6 %), dedicated `/api/health/sync` momentum section.

## Open issues (priority)
- **P0** Vision Intel universal refactor — full scope in `/app/memory/VISION_INTEL_REFACTOR_SCOPE.md`. Nullification phase shipped (Phase 2); engine refactor remains.
- **P1** `vision_score == 0.0` data corruption for legit Safe Haven candidates (Tyrese Maxey et al.) — artificially fails `vision_score_gate`.
- **P1** Pitcher Strikeouts L20 fallback missing.
- **P1** PP-Only alt-line TP calculation (DK ladder fair-odds) — blocks Sunday-morning MLB slates from appearing.
- **P1** Inactive-player UNDER inflation (`min projection > 0` filter).
- **P1** MLB Debias audit — must use heteroscedastic-sigma pattern, NOT additive (lesson from NBA Phase 1 mistake).
- **P1** Decompose Dashboard.jsx + picks_getter_service.py.
- **P1** SSOT Tier B: `hit_rate_l5/l10/l20` rename, `edge` alias cleanup, `cv` parallel-compute delete, `ranking_score_v2` drop vision_score fallback.

## Upcoming
- P0 Google/Apple OAuth (Emergent-managed).
- P0 Stripe payments (test keys in pod env).
- P1 NFL config scaffold (heteroscedastic + Vision Intel adapters).
- P1 STL/BLK/Double-Double NBA model training.
- P1 User-facing error/stale states.

## Key files referenced this session
- `services/scoring/gates/thresholds.py` — gate config.
- `services/scoring/adapters/nba_scoring.py` — `_engine_p_over` applies hetero σ.
- `services/scoring/recompute.py` — `score_doc_writer`; allowlist via `prop_scores_store.py:_SCORE_OUTPUT_FIELDS`.
- `services/delta/detector.py` — watcher (just fixed).
- `routes/ferrari_tiers.py:_get_nba_tier_picks_from_scores` — final live_props matchup override.
- `routes/live.py` — ticker.
- `config/nba_sigma_heteroscedastic.py` + `nba_projection_calibration.py`.
- `frontend/src/components/dashboard/UniversalPlayerCard.jsx` — matchup row.
- `frontend/src/components/dashboard/PlayerDetailPage.jsx` — variance tile.

## Hard-learned constraints (READ FIRST)
- `_SCORE_OUTPUT_FIELDS` in `prop_scores_store.py` is a strict allowlist — new audit fields written by adapters silently drop unless added there.
- Additive μ-debias on a stat-wide constant overcorrects volume stars. NEVER again — use heteroscedastic σ buckets or magnitude-gated additive.
- Watcher `new_keys` MUST subtract `active=True` rt keys only. Set-diffing against ALL rt keys silently freezes the board on slate rolls.
- `nba_master_active_cache.json` / `mlb_master_active_cache.json` are stale static files (Apr 23). Treat as deprecated; the universal Vision Intel refactor should remove dependency on them.
- The user is highly critical and demands real, verified fixes via terminal output. No "clear cache" suggestions. No template-language responses.

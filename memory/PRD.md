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
- **SSOT Enforcement Tier E (2026-05-04) — FINAL CLEANUP:**
  - Frontend edge aliases fully migrated: `vk_edge` / `edge_pct` / `true_edge` → canonical `edge_vs_fair`. Back-compat fallbacks removed from `UniversalPlayerCard.jsx` (2 sites) + `PlayerDetailPage.jsx` (3 sites). Zero live readers remain.
  - `true_edge` DELETED from code (0 readers, 0 writers). `_overlay_enrichment_cache_legacy` function body DELETED (~100 LOC).
  - **Strict Pydantic FLIPPED LIVE**: `SSOT_PYDANTIC_STRICT=true`. Fresh recompute: NBA 1732 + MLB 1044 docs written, 0 ValidationErrors, 0 supervisor log entries.
  - **169,561 stale `version_tag` rows purged** across `nba_prop_scores` + `mlb_prop_scores` (recompute-*, stage2-verify-*, universal-tp-*, final-*-rt-shadow). -79% row count. 4 stale JSON cache backup files deleted.
  - **117/117 tests green, permanent repair ~80% complete**. Five-tier campaign done; remaining surface polish + Vision Intel refactor tracked as Tier F.

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

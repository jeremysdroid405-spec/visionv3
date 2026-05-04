# Field Ownership Audit — 2026-05-03

**Scope:** 20 user-visible fields + 6 cross-cutting systems.
**Method:** Code grep + Mongo schema inspection. No patches applied except where flagged.
**Status codes:** `CLEAN` (single writer, single reader, no fallback) · `FRAGILE` (multiple writers OR fallback can lie) · `BROKEN` (demonstrably produces wrong values in prod today) · `UNKNOWN` (not enough data without runtime test).

---

## Summary — top 10 SSOT violations ranked by user impact

| # | Violation | Impact | Evidence |
|---|---|---|---|
| 1 | **`_SCORE_OUTPUT_FIELDS` allowlist silently drops adapter fields** | New scoring outputs vanish without warning. Burned us 3x this session. | `prop_scores_store.py:21` + `recompute.py:507` filter `if k in _SCORE_OUTPUT_FIELDS`. No log on drop. |
| 2 | **Vision intel has 4 parallel writers + 3 fallback cascades** | MLB summaries are 100% templated (confirmed this session). | `vision_intel_service.py:553-564` + `mlb_vision_intel.py:378-389` + `ferrari_tiers.py:201 _generate_vision_fallback` + `overlay_enrichment_cache` reads stale JSON. |
| 3 | **`opponent` has 4 write sources, 1 read path merges them with override logic** | Dylan Harper SAS-vs-POR bug today. | `cached_board.opponent` + `master_hub.opponent` + `live_props.opponent_team` + `player_context_features.opponent_team`. Today's fix trusts `live_props` at read time but doesn't delete the others. |
| 4 | **`active=True` has 8 independent writers, no contract on meaning** | Board freezes (today's watcher bug). | `scoring/recompute.py` sets True; `board/scanner.py` sets False; `board/publisher.py` sets both; `tiering.py` sets False; `badge_resolver.py` sets both. No invariant test. |
| 5 | **`version_tag` is free-form string, 5+ active tags in MLB** | `final-mlb-rt` frozen while `final-mlb-rt-shadow` gets updates. | `stage2-verify-mlb`, `recompute-<timestamp>-<hash>`, `final-mlb-rt-shadow`, `final-mlb-rt`, `final-mlb`. No enum. Reader defaults to `final-mlb-rt` and silently returns empty if stale. |
| 6 | **`computed_at` populated but `scored_at` NULL everywhere** | Health endpoint reports `last_scored_at: null` → probe dead → no staleness alerts. | Only writer is `prop_scores_store.py:389` which writes `computed_at`. Nothing writes `scored_at`. |
| 7 | **`photo_url` has in-memory cache that persists bad lookups** | Stale headshots; users see wrong player photos. | `picks_getter_service.py:237-399` module-global `_photo_cache` dict. No TTL, no invalidation on master_hub update. |
| 8 | **Master sync MLB hasn't fired for 18h but no visible alarm** | Odds 18h stale right now. | `scheduler_jobs` row exists; `next_run` past; `health/sync` returns `critical`; no UI surface. |
| 9 | **`dg_cached_board` table empty but still queried in 14 files** | Silent zero-match reads produce empty enrichment; code path treats "not in cache" as "no enrichment needed". | `dg_cached_board.count = 0` yet `picks_getter_service.py`, `routes/command.py`, `routes/ferrari_tiers.py:943`, etc. all still read it. |
| 10 | **`mlb_master_active_cache.json` / `nba_master_active_cache.json` are static JSON files from Apr 23** | Stale enrichment cascades override fresh data silently via `overlay_enrichment_cache`. | `ferrari_tiers.py:284`. File mtime = 2026-04-23. Called on every tier read. |

---

## Per-field audit

### FIELD: opponent
- **OWNER (de facto):** `nba_live_props.opponent_team` / `mlb_live_props.opponent_team`
- **WRITERS (writes or propagates):**
  - `services/universal_odds_sync.py:1022` — writes opponent_team at sync time (live_props)
  - `services/context_badge_service.py:154,161` — reads from cached_board, writes into badge docs
  - `services/picks_getter_service.py:1260,1700,2338` — copies opponent onto pick dicts
  - `services/mlb_cached_board_builder.py:470` — writes from game log
  - `routes/ferrari_tiers.py:1486-1523` — live_props override (added today)
- **READERS:** 30+ files across services/dvp_service.py, simulation_service.py, vegas models, etc.
- **FALLBACKS:** `cached_board.opponent` → `master_hub.opponent` → `live_props.opponent_team` (the one today's fix reads)
- **PROBLEM:** 4 sources. Readers pick whichever exists first. Stale `cached_board.locked_event_id` override produced the SAS-vs-POR bug we fixed today.
- **FIX:** Delete `cached_board.opponent` writes. Delete `master_hub.opponent` reads. Single query: `live_props.opponent_team`. Missing = `None`, not fallback.
- **STATUS:** `FRAGILE` (patched today for read path, writers still leak)

### FIELD: player_name
- **OWNER:** `master_hub.display_name` (canonical) → propagated to `live_props.player_name`
- **WRITERS:** 12 files write it (live_props sync, recompute, publisher, card_contract, game_logs, cached_board_builder, etc.)
- **READERS:** 40+ files
- **FALLBACKS:** Multiple. `display_name` vs `player_name` used interchangeably. `game_log.player_name` sometimes overrides master.
- **PROBLEM:** No canonical capitalization (`"De'Aaron Fox"` vs `"Deaaron Fox"`). Can produce duplicate player cards.
- **FIX:** Single canonical map from `bdl_player_id → display_name` at master_hub. All other collections reference by ID, display name derived at read time.
- **STATUS:** `FRAGILE`

### FIELD: team
- **OWNER:** `master_hub.team_abbr`
- **WRITERS:** live_props sync, master_hub_sync, cached_board_builder, game_logs sync
- **READERS:** 50+ files use `team`, `team_abbr`, or `team_name` interchangeably
- **FALLBACKS:** `team` (3-letter abbr), `team_abbr` (duplicate), `team_name` (full name). All coexist on pick dicts.
- **PROBLEM:** Traded players show last-known team for up to 24h because cached_board lock doesn't update mid-day.
- **FIX:** Read team from `live_props.team` (ingested with the prop). Drop `cached_board.team`.
- **STATUS:** `FRAGILE`

### FIELD: game_id / event_id
- **OWNER:** `live_props.event_id` (single ingest at odds sync time)
- **WRITERS:** `universal_odds_sync.py:1207` + `master_sync.py:691` + propagated by `recompute.py:61,433,767`
- **READERS:** 25+ files
- **FALLBACKS:** None meaningful — this field is actually clean.
- **PROBLEM:** None discovered. `event_id` in score doc reliably matches live_props event.
- **FIX:** N/A
- **STATUS:** `CLEAN`

### FIELD: stat_type
- **OWNER:** `live_props.stat_type` (raw from Odds API)
- **WRITERS:** odds sync writes it. Scoring passes through.
- **READERS:** Everywhere.
- **FALLBACKS:** Multiple normalization layers — `Hits+Runs+RBIs` vs `H+R+RBI` vs `hits_runs_rbis`. Backend and frontend normalize differently.
- **PROBLEM:** `intel_suite_calculator.py:_extract_stat_values` fails on composite stat types → returns empty list → std_dev=0 (today's variance bug).
- **FIX:** Single `stat_type` canonicalizer used by all producers + consumers. Composite stats get explicit splitter.
- **STATUS:** `FRAGILE`

### FIELD: line
- **OWNER:** `live_props.line` (PrizePicks line from Odds API)
- **WRITERS:** odds sync.
- **READERS:** Everywhere. Scoring, gates, frontend, history.
- **FALLBACKS:** `pp_line` / `dk_line` / `fd_line` per-book. Scoring fans out to compare; dashboard shows `line` (PP).
- **PROBLEM:** None discovered at the field level. Book-line drift handled by cached_board_index line-matching logic.
- **FIX:** N/A
- **STATUS:** `CLEAN`

### FIELD: side / recommendation
- **OWNER:** `live_props.recommendation` (`OVER` / `UNDER`)
- **WRITERS:** odds sync. Recompute preserves it.
- **READERS:** Everywhere.
- **FALLBACKS:** `direction` field is a second name for same value. Used interchangeably in UNDER-side fallback logic.
- **PROBLEM:** Reader confusion — some code paths treat `None` as OVER (`picks_getter_service.py`).
- **FIX:** Rename once to `side`. Enforce enum `{OVER, UNDER}`. Pydantic validates.
- **STATUS:** `FRAGILE`

### FIELD: p_true / p_true_active
- **OWNER:** `prop_scores.p_true_active` (= `ctx.p_model` at scoring time)
- **WRITERS:** `recompute.py:444` only.
- **READERS:** 15+ files.
- **FALLBACKS:** None. `p_true_active=None` means unscorable; reader handles explicitly.
- **PROBLEM:** None discovered.
- **FIX:** N/A
- **STATUS:** `CLEAN`

### FIELD: edge / edge_vs_fair
- **OWNER:** `prop_scores.edge_vs_fair` (= `p_model - fair_prob`)
- **WRITERS:** `scoring_stack.py:285` only.
- **READERS:** 20+ files. Gates, sort, display, filters.
- **FALLBACKS:** `vk_edge`, `true_edge`, `edge`, `edge_pct`, `edge_percentage` — multiple historical names for related values, not always identical.
- **PROBLEM:** Display inconsistency — some screens show `edge_vs_fair`, some show `vk_edge`, some show `edge_pct`. Users see different numbers on same pick.
- **FIX:** One field (`edge`). Deprecate aliases. Compute at read.
- **STATUS:** `FRAGILE`

### FIELD: hit_rate
- **OWNER:** `prop_scores.hit_rate_over` / `hit_rate_under` (L20 from game logs)
- **WRITERS:** `nba_scoring.py:2464-2467` + `mlb_scoring.py:598-599`
- **READERS:** Gates, intel_suite, fallback summaries, frontend.
- **FALLBACKS:** `h10_rate`, `l10_rate`, `h10_hit_rate`, `l20_rate`. Different stat files measure from different windows.
- **PROBLEM:** `_generate_vision_fallback` labels `h10_rate` as "L10" but falls through to `hit_rate_over` (which is L20) — surfaces as "95% L10" when actual L10 is 90% (prior session bug, supposedly fixed).
- **FIX:** Window-explicit fields. `hit_rate_l5`, `hit_rate_l10`, `hit_rate_l20`. No generic `hit_rate`.
- **STATUS:** `FRAGILE`

### FIELD: cv
- **OWNER:** `prop_scores.cv` (= std_dev / mean from L30 game logs)
- **WRITERS:** `nba_scoring.py:2499` + `mlb_scoring.py:195`
- **READERS:** gates/thresholds.py (cv_gate), frontend Variance tile (today's fix), volatility badges.
- **FALLBACKS:** `cv_raw`, `volatility_score` (derived). `intel_suite.stability_index.std_dev` computes its own independent std_dev and disagrees with cv.
- **PROBLEM:** `stability_index` returns std_dev=0 / "Unknown" for composite MLB stats while `cv` is correct on the same pick (today's bug). Two CV sources on one pick.
- **FIX:** Delete `stability_index` computation. Variance UI reads `cv` / `volatility_score` only (today's frontend patch).
- **STATUS:** `BROKEN` (variance UI patched today; `stability_index` still computed server-side for other surfaces)

### FIELD: ranking_score_v2
- **OWNER:** `prop_scores.ranking_score_v2`
- **WRITERS:** scoring pipeline (derived from p_true, edge, vision).
- **READERS:** `player.py:306`, `ferrari_tiers.py:149,1197,1722`, `vacuum.py:276`, `market_moves_engine.py:265`.
- **FALLBACKS:** `vision_score` used when `ranking_score_v2` is None.
- **PROBLEM:** Fallback to `vision_score` produces inconsistent sort order across tabs — some screens sort by one, some by the other.
- **FIX:** Always produce `ranking_score_v2` at scoring time. If null, drop the pick (not fallback).
- **STATUS:** `FRAGILE`

### FIELD: tier
- **OWNER:** `prop_scores.tier` (one of `safe_haven` | `front_lines` | `war_zone` | `unqualified`)
- **WRITERS:** `tiering.py`, `gates/engine.py`. Single path.
- **READERS:** Board reader, ferrari_tiers routes.
- **FALLBACKS:** None — unqualified is explicit.
- **PROBLEM:** None discovered.
- **FIX:** N/A
- **STATUS:** `CLEAN`

### FIELD: active
- **OWNER:** `prop_scores.active` + `live_props.active`
- **WRITERS (prop_scores):** `recompute.py:439` (True), `scanner.py:43` (False), `publisher.py:209,220` (False), `tiering.py:78` (False), `badge_resolver.py:561,576` (both).
- **WRITERS (live_props):** master_sync flips False on game start.
- **READERS:** 20+ files. Each tests `{"active": True}` OR `{"active": {"$ne": False}}` inconsistently.
- **FALLBACKS:** N/A — but the TWO DIFFERENT QUERY PATTERNS above behave differently when `active` is missing.
- **PROBLEM:** The core watcher bug we fixed today. Multiple writers with no contract on when active=True vs False means stale False rows mask fresh work.
- **FIX:** Single `set_active(canonical_key, state, reason, timestamp)` helper. All writes go through it. All writes are audited in `active_transitions` collection. Readers standardize on ONE query pattern.
- **STATUS:** `BROKEN` (patched today, root-cause not eliminated)

### FIELD: computed_at / scored_at
- **OWNER:** `prop_scores.computed_at` (written) — `scored_at` NEVER WRITTEN
- **WRITERS:** `prop_scores_store.py:389,464,498,595`. Only `computed_at`.
- **READERS:** `health_sync.py` probes `last_scored_at` — **NULL in prod right now**.
- **FALLBACKS:** N/A
- **PROBLEM:** `scored_at` is the health endpoint's freshness field. Nothing writes it. Health endpoint shows `last_scored_at: null` for both sports. **This makes the entire freshness monitoring system non-functional.**
- **FIX:** **ONE-LINE FIX. Write `scored_at` alongside `computed_at` in `prop_scores_store.py`.** Or rename `computed_at` → `scored_at` everywhere and update the probe.
- **STATUS:** `BROKEN` — **FLAGGED AS OBVIOUS ONE-LINE FIX BELOW**

### FIELD: vision_intel
- **OWNER:** `prop_scores.vision_intel` (written by `master_sync:_enrich_nba_board_vision_intel`)
- **WRITERS:**
  - `services/vision_intel_service.py:553,564` (NBA Gemini path)
  - `services/mlb_vision_intel.py:378,389` (MLB Gemini path, NOT WIRED into mlb_master_sync)
  - `services/master_sync.py:1355,1424-1426` (persistence)
  - `routes/ferrari_tiers.py:337,1128,2273` (overrides at read time — `_generate_vision_fallback`)
- **READERS:** Frontend card + PlayerDetailPage.
- **FALLBACKS:**
  - If score doc has `vision_intel=None` → `overlay_enrichment_cache` reads stale JSON
  - If stale JSON misses → `_generate_vision_fallback` generates template
  - If template check fails → card shows empty
- **PROBLEM:** 4 writer sources, 3-level fallback cascade. MLB has ZERO real writers (mlb_vision_intel is not called from master_sync). Every MLB summary is template.
- **FIX:** This IS the Vision Intel Universal Refactor (scoped doc already exists).
- **STATUS:** `BROKEN` for MLB; `FRAGILE` for NBA

### FIELD: photo_url / headshot_url
- **OWNER:** `master_hub.photo_url` (canonical)
- **WRITERS:** bdl_universal_sync writes master_hub. `picks_getter_service.py:290-319` derives URL at read.
- **READERS:** Pick cards, player detail page.
- **FALLBACKS:** `/static/player-headshots/{nba_id}.png` local fallback if no photo_url.
- **PROBLEM:** `picks_getter_service.py:237` keeps a MODULE-GLOBAL `_photo_cache` dict. Never invalidates. Bad lookups persist for lifetime of backend process. Traded players keep old team, wrong photos.
- **FIX:** Delete module cache. Read master_hub fresh each request (cheap; master_hub is indexed).
- **STATUS:** `FRAGILE`

### FIELD: pp_projection_id
- **OWNER:** `pp_projection_id_cache` collection (TTL 15 min)
- **WRITERS:** `pp_multiplier_lab.py:1010` (PP Chrome scraper path).
- **READERS:** `pp_multiplier_lab.py:735`, `routes/pp_multiplier_lab.py:155-190`.
- **FALLBACKS:** `"standard"` default when missing.
- **PROBLEM:** The Chrome scraper is out-of-band — requires an external runner. If runner is down, cache expires, all projections get `standard` → demons/goblins look like standards → multiplier math wrong.
- **FIX:** Surface PP scraper health in `/api/health/sync`. Hard-fail PP-multi-leg pricing when scraper stale.
- **STATUS:** `FRAGILE` (external dependency; no health surface)

### FIELD: odds_type
- **OWNER:** `pp_multiplier_lab.py:_norm_odds_type` normalizes to `{goblin, demon, standard}`.
- **WRITERS:** PP scraper writes raw; normalizer runs at projection-build time.
- **READERS:** Multiplier lab only.
- **FALLBACKS:** Defaults to `"standard"` if missing.
- **PROBLEM:** Same as `pp_projection_id` — external scraper freshness.
- **FIX:** Same as pp_projection_id.
- **STATUS:** `FRAGILE`

### FIELD: start_time / game_start_utc / commence_time
- **OWNER:** `live_props.commence_time` (ISO string from odds API) + `live_props.game_start_utc` (parsed datetime)
- **WRITERS:** odds sync writes both. Recompute propagates `game_start_utc` to score doc.
- **READERS:** 20+ files. Some read `commence_time` (string), some read `game_start_utc` (datetime).
- **FALLBACKS:** `start_time` on ticker rows is a third name, constructed from home-team game. Used only by frontend ticker.
- **PROBLEM:** Three names for the same value. String vs datetime coexistence — some readers do `.isoformat()`, some do `datetime.fromisoformat()`, some do both unnecessarily (today's pick card fix stringifies then frontend parses).
- **FIX:** Store ONE datetime field (`game_start_utc`). Serialize to ISO at API boundary. Delete `commence_time` and `start_time` aliases from score docs.
- **STATUS:** `FRAGILE`

---

## Cross-cutting audit

### `_SCORE_OUTPUT_FIELDS` (strict allowlist)
- Lives in `services/scoring/prop_scores_store.py:21`.
- 200+ hand-maintained field names.
- `_project_score_doc` at line 370 filters writes via `for k in _SCORE_OUTPUT_FIELDS`.
- **SILENTLY DROPS** any field not in allowlist. No log, no warning, no error.
- Fields dropped silently THIS SESSION: `hetero_sigma_base`, `hetero_sigma_adjusted`, `hetero_sigma_multipliers` until I added them. Probably more currently dropping.
- **FIX:** Pydantic `ScoreDocument` model. Writing a non-schema field raises `ValidationError`. Allowlist becomes derived from schema.

### Score doc allowlists (plural!)
- `_SCORE_OUTPUT_FIELDS` (200+ fields)
- `_IDENTITY_FIELDS` (8 fields) — identity columns
- `_UNIVERSAL_POOL_FIELDS` (4 fields) — `active`, `inactive_reason`, `active_changed_at`, `game_start_utc`
- **3 allowlists, maintained separately.** Drift between them is an accident waiting to happen.

### Vision Intel fallback cascade
1. `prop_scores.vision_intel` (Gemini-written, NBA only)
2. → `overlay_enrichment_cache` (stale JSON file, Apr 23)
3. → `_generate_vision_fallback` (templated string)
4. → UI shows empty / template

**Each layer silently takes over when the one above is empty.** User has no way to distinguish Gemini-authored from template.

### Cached JSON enrichment files
- `/app/backend/data/nba_master_active_cache.json` — mtime 2026-04-23 (10 days stale)
- `/app/backend/data/mlb_master_active_cache.json` — mtime 2026-04-23 (10 days stale)
- Loaded by `overlay_enrichment_cache` on every tier read.
- **No writer exists in current codebase.** Files are orphaned — whatever used to build them is gone.
- Their content overrides live score-doc fields (per the function's name "overlay").

### `dg_cached_board` usage
- Collection row count: **0**
- Referenced in: 14 files
- Example reader: `routes/command.py:232`, `routes/ferrari_tiers.py:943`, `picks_getter_service.py:8,231`
- **Dead read.** Never populated. Readers treat zero-match as "no enrichment". No one throws an error.

### `version_tag` audit
Active tags observed in `nba_prop_scores` + `mlb_prop_scores`:
- `final-nba-rt` — canonical live read tag (78k docs, NBA)
- `final-mlb-rt` — canonical live read tag
- `final-mlb-rt-shadow` — runs in parallel, not read by API
- `stage2-verify-nba` / `stage2-verify-mlb` — historical debugging tags, 144k MLB docs
- `recompute-<ts>-<hash>` — per-recompute snapshots, never cleaned up
- **5+ tags, no TTL, no enum.** Tag drift between writer and reader silently freezes the board.

### Active-row writers
8 writers, 0 contract. Detailed above in SSOT violation #4.

### Scheduler / watcher freshness
- `hourly_nba_master_sync` next-run: 2026-05-04 00:13 UTC (+50 min from audit time)
- `hourly_mlb_master_sync` next-run: 2026-05-04 00:13 UTC
- Last MLB master_sync actual run: **2026-05-03 05:14 UTC (18.5h ago)**
- `hourly_mlb_master_sync` job exists in `scheduler_jobs`, shows no log entries for 18h
- No alert fires. `/api/health/sync` returns `critical` but nothing acts on it.

---

## Proposed patch plan (in order)

### Tier 0 — Obvious one-line fixes (I can apply now if you approve)
1. **Write `scored_at` at score time** — `prop_scores_store.py`. Unblocks the health probe, no regression risk. 1 line.
2. **Log on `_SCORE_OUTPUT_FIELDS` drops** — `prop_scores_store.py:374`. Change silent drop to `logger.warning("field X not in allowlist")`. 3 lines. Makes future silent drops visible.
3. **Delete `_photo_cache` module global** — `picks_getter_service.py:237`. Remove cache, read master_hub directly. ~5 lines.

### Tier 1 — Data-layer cleanup (1 focused session)
4. **Fix `active` row contract** — one `set_active()` helper, audit trail, invariant test.
5. **Consolidate `version_tag` to enum + TTL** — drop `stage2-verify-*`, `recompute-*` after 24h, enforce enum in scoring code.
6. **Delete `dg_cached_board` reads** — remove 14 files' references; if a field was coming from it, read from `live_props` instead.
7. **Delete `*_master_active_cache.json` reads** — orphaned files, no writer exists.

### Tier 2 — Schema contracts (2 focused sessions)
8. **Pydantic `ScoreDocument` model** — replaces `_SCORE_OUTPUT_FIELDS`. Writes validate. No silent drops.
9. **Pydantic `LivePropDocument` model** — same for ingest.
10. **Single-source-of-truth enforcement** — for each of the 20 fields, declare authoritative source, delete duplicate writers.

### Tier 3 — Vision Intel refactor (already scoped)
11. Ship the universal vision intel engine per `/app/memory/VISION_INTEL_REFACTOR_SCOPE.md`.
12. Delete `_generate_vision_fallback`, `overlay_enrichment_cache`, stale JSON files.

### Tier 4 — Observability surfacing
13. Admin `/admin/health` page reading `/api/health/sync` + `/api/health/contracts`.
14. Red/amber/green status pill in dashboard header (admin-only flag).
15. Weekly stale-data regression test run on cron.

---

## Evidence before we change anything

This doc IS the evidence. Every line references a specific file:line.

Requesting your approval to apply **Tier 0 only** (3 one-line fixes, zero regression risk).
Tier 1+ require separate focused sessions per the scope notes.

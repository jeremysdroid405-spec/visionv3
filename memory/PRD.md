# PropVision - Product Requirements Document

## Core Architecture: Event-Driven Sync v2 (All Phases Complete)

### Sync Architecture
- **EventBus** → **RebuildCoordinator** → **UnifiedPipeline(SportAdapter)** → Atomic Publish
- Both NBA and MLB route through the single authoritative publish path
- Per-sport mode (live/shadow), per-trigger-class toggles, dedup, cooldown, rate limiting
- Market Moves diff with exit-reason classification post-publish
- Gemini batch enrichment post-publish (non-blocking)

### Phase Status
- Phase 1: Foundation (EventBus, Coordinator, Budget Manager) — COMPLETE
- Phase 2: NBA Migration — COMPLETE
- Phase 3: MLB Migration — COMPLETE
- Phase 4: Event-Driven Activation (Watchers) — COMPLETE
- Phase 5: Legacy Cleanup — PENDING user approval

## Injury Normalization Layer (COMPLETE)

### Source: BallDontLie API (structural authority)
- NBA: `/nba/v1/player_injuries` → 181 injuries, 5 status tiers
- MLB: `/mlb/v1/player_injuries` → 143 injuries, 5 status tiers + IL designations

### Field Classification — Structural vs Display-Only Firewall
Every injury record in `injuries_normalized` enforces a hard separation:

**STRUCTURAL (top-level)**: sport, player_name, bdl_id, team, team_id, position, status, tier_level, risk, color, return_date, injury_date, source, synced_at, first_seen_at, status_changed_at

**DISPLAY_ONLY (nested)**: raw_status, description, short_comment, injury_type, injury_detail, injury_side

### Dynamic Recency Window (Live Injury Advantage)
| Game State | Window | Rationale |
|-----------|--------|-----------|
| Default | 12h | Capture today's slate changes |
| Within 2h of tipoff | 6h | Late scratch zone |
| Game live | 2h | Minimal — stale injuries irrelevant |
| Game finished | Skipped | Not considered active |

## Multi-Source Injury Sensor (COMPLETE)

### Source Trust Hierarchy
| Source | Role | Provides |
|--------|------|----------|
| BDL | STRUCTURAL AUTHORITY | Player IDs, return dates, injury detail |
| ESPN | TIMING AUTHORITY (NBA) | Status changes first |
| NBA Official PDF | TIMING AUTHORITY (NBA) | League-mandated status changes |

## CV Calculation Standard (UNIFIED — April 16, 2026)
- **All CV calculations now use `np.std(ddof=1)` (sample standard deviation)**
- Files standardized: `oracle_apex_service.py`, `vegas_regression_model.py`, `intel_suite_calculator.py`
- Rationale: For L10 samples (N=10), sample std dev is statistically correct

## Database Configuration
- **DB_NAME**: `pick_vision` (set in backend/.env)
- Server uses `os.environ['DB_NAME']` → `pick_vision`
- Key collections in `pick_vision`:
  - NBA tiers: `elite_safe_haven`, `elite_front_lines`, `elite_war_zone`
  - MLB tiers: `mlb_safe_haven`, `mlb_front_lines`, `mlb_war_zone`
  - Master hubs: `nba_master_hub_2026` (559), `mlb_master_hub_2026` (777)
  - Injuries: `injuries_normalized` (326)

## Key API Endpoints
- `POST /api/nba/sync/master` — NBA coordinator → pipeline
- `POST /api/mlb/sync/master` — MLB coordinator → pipeline
- `GET /api/v3/ferrari/safe-haven?sport=nba` — NBA Safe Haven picks
- `GET /api/v3/mlb/ferrari/safe-haven` — MLB Safe Haven picks
- `GET /api/v2/coordinator/status` — Coordinator observability
- `GET /api/v3/command-center/ticker` — News ticker
- `POST /api/v3/mlb/test-scheduled-sync` — Non-manual MLB sync test

## Canonical Layered Odds Architecture (COMPLETE — April 17, 2026)

### Strict Exact-Match Layering (No Fuzzy Matching)
- **Anchor**: PrizePicks creates canonical props keyed by `sport|event_id|player|stat|line|side`
- **References**: DraftKings and BetMGM attach ONLY when their `line` and `side` exactly match the PP anchor
- **Flattened schema** in `mlb_live_props`: `canonical_key`, `pp_layer`, `dk_layer`, `mgm_layer`, `sharp_layer`
- **Hard audit proven** — 0 line mismatches across 4944 props, 0 duplicate canonical keys

### Layer Coverage (live verification)
| Layer Composition | Count | % |
|---|---|---|
| PP + DK + MGM | 3070 | 62% |
| PP + DK only | 877 | 18% |
| PP + MGM only | 633 | 13% |
| PP only | 364 | 7% |

### Market Consensus & Anomaly Split (validated on new layered data)
| Consensus Source | Count | % |
|---|---|---|
| dk+mgm (both books on same line) | 2669 | 55% |
| dk_only (MGM line mismatch excluded) | 1144 | 23% |
| mgm_only (DK line mismatch excluded) | 551 | 11% |
| neutral_baseline (no reference book) | 461 | 9% |

- DK line mismatches (excluded from consensus): 113
- MGM line mismatches (excluded from consensus): 555
- True market disagreement (both on same line, ≠ probabilities): 414
- Strong consensus (disagreement < 0.03): 1528

### Vision Score Calibration (percentile distribution)
P25=23.7  P50=49.1  P75=74.6  P95=94.9 — well-spread across 0–100

## Scoring Stack — Three Independent Dimensions (LOCKED — April 17, 2026)

### Design
Three DECOUPLED scores per canonical prop, persisted to dedicated `mlb_prop_scores` collection.
No scoring-stack field lives in `mlb_cached_board` or tier collections.

| Dimension | Purpose | Null-case |
|---|---|---|
| `vision_score` (0–100) | Platform-agnostic pick quality. Sharp-first fair prob: pinnacle > consensus(dk,mgm) > dk > mgm. Never reads PP data. | `null` with `quality_source="insufficient_market"` |
| `tier` | Risk bucket from reference odds (dk → mgm fallback) + existing `MLBTierSorter` gates | `"unqualified"` with `tier_reason="no_reference_market"` |
| `pp_utility` (0–100 + category) | PP-specific leg usefulness for parlay construction. Components: availability, line_fairness, model_alignment, edge_confidence, multiplier_value (reserved). | Category: `pp_fair` \| `pp_exclusive` \| `pp_scam` \| `pp_premium`* \| `pp_discount`* |

*`pp_premium` / `pp_discount` are RESERVED — emitted only when a real multiplier source exists (`pp_combo_multiplier`, `pp_label`, or `pp_multiplier_model`). PP American odds are NEVER treated as a payout multiplier.

### Storage
- Collection: `mlb_prop_scores` (unique index on `canonical_key`)
- Writer: `/app/backend/services/scoring/prop_scores_store.py`
- Scoring fields stripped from in-memory props post-write so downstream `mlb_cached_board` / tier writers cannot persist them.

### Live validation (2026-04-17)
- 4825 score docs; 297 PP-exclusive vision_score=null; pinnacle=0, consensus=3173, dk=753, mgm=602
- tiers: 50 safe_haven / 24 front_lines / 48 war_zone / 4703 unqualified (297 no_ref + 4406 failed gates)
- pp_utility: 4513 fair / 297 exclusive / 15 scam / 0 premium / 0 discount (no real multiplier source yet)
- cached_board leak check: 0 scoring fields leaked

### Key Files
- `services/scoring/scoring_stack.py` — pure scoring functions (three dimensions)
- `services/scoring/prop_scores_store.py` — writer + stripper
- `services/adapters/mlb_adapter.py::enrich_and_score` — composes stack + persists

## Scoring Recompute Framework (COMPLETE — April 17, 2026)

System-level, sport-agnostic infrastructure to rebuild `{sport}_prop_scores`
from existing live props — NO odds-sync, NO mutation of live props or
cached_board, NO parlay/UI logic.

### Endpoints
- `POST /api/scores/recompute` — all supported sports (or `sports` array in body)
- `POST /api/scores/recompute/{sport}` — single sport (ignores `sports` array)
- `GET  /api/scores/supported-sports` — list of supported sport keys

### Request body
```
{ "sports": ["mlb","nba"], "version_tag": "...", "dry_run": false,
  "limit": null, "override_config": { "vision_score":{}, "tier":{}, "pp_utility":{} } }
```

### Architecture
- `services/scoring/adapters/base.py` — `ScoringAdapter` + `ScoringContext`
- `services/scoring/adapters/mlb_scoring.py` — MLB (uses `MLBTierSorter` + XGBoost)
- `services/scoring/adapters/nba_scoring.py` — NBA (uses `_NBAGateSorter`; real PP multiplier label from `is_demon`/`is_goblin`/`prop_type`)
- `services/scoring/recompute.py` — sport-agnostic orchestrator
- `services/scoring/prop_scores_store.py` — versioned writer
- `routes/scores.py` — FastAPI endpoints

### Versioning
Every score doc: `canonical_key`, `sport`, `version_tag`, `computed_at`.
Collection indexes: unique `(canonical_key, version_tag)` + secondary
on `vision_score desc`, `tier`, `pp_utility desc`, `computed_at desc`.
Duplicate `(canonical_key, version_tag)` inserts rejected by MongoDB.

### Live validation (2026-04-17)
- MLB: 4944 processed → 4944 written, 0 live-props mutation, 0 cached_board leakage
- NBA: 2854 processed → 2854 written, 0 live-props mutation, 0 cached_board leakage
- Dry-run verified: `written=0` when `dry_run=true`
- A/B test: same canonical_key coexists across 4+ version_tags
- MLB quality_source: pinnacle 410 / consensus 2837 / dk 865 / mgm 483 / insufficient 349
- NBA quality_source: `betonline` (sharp) 1622 / consensus 274 / dk 304 / mgm 91 / insufficient 541
- NBA pp_utility: real PP multiplier label feeds 1085 `pp_premium` + 609 `pp_discount` (no fakery from odds)

### Query Endpoint (April 17, 2026)
`GET /api/scores/{sport}` — read-only QA inspection against `{sport}_prop_scores`.

Query params: `version_tag` (auto-latest if omitted), `min_vision`, `max_vision`,
`tier`, `pp_utility_category`, `quality_source`, `player_name`, `stat_type`,
`limit` (1-1000, default 50), `offset`, `sort_by`, `sort_dir`.

Returns: `total_matching`, `returned`, `summary` (by_tier / by_pp_utility_category /
by_quality_source / vision_score_null), `results[]`. Zero mutation.

### NBA Tier Gating Fix (April 17, 2026)
NBA CV now computed directly from `nba_master_hub_2026.bdl_game_logs` (L20 window,
ddof=1 sample std). Before: 0% qualified. After: 2055/2854 (72%) have computed CV,
143 qualified (40 safe_haven / 103 front_lines / 0 war_zone — war_zone still needs
elevated ceiling_rate gates). Hit_rate + ceiling_rate also recomputed from
game logs. Decoupling preserved — vision_score, tier, pp_utility semantics unchanged.

## NBA War Zone Defaults (LOCKED — April 17, 2026)
`_NBAGateSorter.WAR_ZONE = {"min_cv": 0.45, "min_ceiling_rate": 20, "min_edge": 10}`
(varA, validated: 42 war_zone props / 2847, 100% `pp_premium`, zero cannibalization
of safe_haven/front_lines, all entrants migrated from `unqualified` only.)

## Simulation Endpoints (COMPLETE — April 17, 2026)
- `POST /api/scores/simulate` — system-level read-only
- `POST /api/scores/simulate/{sport}` — per-sport
Request body identical to recompute (`sports`, `limit`, `override_config`).
Returns `mode=simulation`, `persisted=false`, `tier_distribution`,
`quality_source_distribution`, `pp_category_distribution`, `top_samples[]`
(top 10 by vision_score). Zero persistence — `{sport}_prop_scores` and live
prop collections provably unchanged byte-for-byte pre/post simulation.

## Multi-Variant Scoring Compare Endpoint (COMPLETE — April 17, 2026)
- `POST /api/scores/simulate/compare` — system-level (all supported sports or `sports` array)
- `POST /api/scores/simulate/compare/{sport}` — per-sport (ignores `sports`)

Request body:
```json
{
  "sports": ["mlb","nba"],
  "limit": 500,
  "variants": [
    {"name":"baseline","override_config":{}},
    {"name":"cv_040","override_config":{"tier":{"war_zone":{"min_cv":0.40}}}},
    {"name":"cv_050","override_config":{"tier":{"war_zone":{"min_cv":0.50}}}}
  ]
}
```

First variant = baseline (all comparisons relative to it). Response includes:
- `variant_results[name]`: tier / quality_source / pp_category distributions + top_samples
- `tier_counts_table`: rows=tier, cols=variant
- `tier_deltas_vs_baseline`: per-tier delta per variant
- `war_zone_movers_vs_baseline`: entered / left (first 50 canonical_keys each)
- `tier_canonical_key_overlap_vs_baseline`: Jaccard + shared + only_in_* per tier
- `top_sample_overlap_vs_baseline`: top-N overlap counts
- `summary`: `most_adds_qualified_variant`, `most_removes_qualified_variant`,
  `highest_overlap_with_baseline_variant`, `clean_migration_variants`
  (variants that don't remove any baseline pick from existing tiers)

Read-only enforced: mlb_prop_scores, nba_prop_scores, mlb_cached_board,
dg_cached_board, and both `live_props.fetched_at` timestamps byte-identical
pre/post compare call. Error paths validated: empty variants → 400,
duplicate names → 400, unsupported sport → 400.

Route-ordering compat: `/simulate/{sport}` explicitly dispatches `sport='compare'`
to `/simulate/compare` since FastAPI path matching is first-match.

## Hard Reference-Odds Admission Bands (LOCKED — April 17, 2026)

`services/scoring/scoring_stack.py` — `compute_tier()` enforces 3-band
hard admission. A prop is evaluated by quality gates (vision_score, CV,
hit_rate, edge, ceiling) ONLY after it falls into the correct band.
If gates fail inside a band, the prop goes to `unqualified` — no
cross-tier fallthrough.

| Tier | Reference-odds band |
|---|---|
| Safe Haven | `ref_odds <= -240` |
| Front Lines | `-239 <= ref_odds <= +149` |
| War Zone | `ref_odds >= +150` |

Constants `_REF_SAFE_HAVEN_MAX = -240` and `_REF_WAR_ZONE_MIN = 150`
are the single source of truth; sport adapters MUST NOT override them.

**Validation (2026-04-17, NBA)**:
- Band violations in any tier: 0 / 2847 props
- Duplicate (player, stat, line) groups spanning qualified tiers: 0
- Christian Braun PTS 9.5 OVER (ref=dk@-190, FL band) → `front_lines` ✓
- Christian Braun PTS 7.5 OVER (ref=dk@-381, SH band, edge-gate fail) → `unqualified` ✓ (no fallthrough to FL)
- Christian Braun REB 7.5 OVER (ref=dk@+630, WZ band, ceiling-gate fail) → `unqualified` ✓
- Christian Braun PTS 8.5 OVER (no ref) → `unqualified` (no_reference_market) ✓

## Calibration Snapshot Persistence (COMPLETE — April 17, 2026)

Audit trail for scoring calibration experiments. POST endpoints run compare
variants read-only and persist lean summary docs to `{sport}_calibration_runs`.
GET endpoints retrieve history.

**Endpoints**
- `POST /api/scores/calibration-snapshot` — system-level (one doc per sport)
- `POST /api/scores/calibration-snapshot/{sport}` — single sport
- `GET /api/scores/calibration-snapshots/{sport}` — list w/ filters (`label_contains`,
  `start_date`, `end_date`, `limit`, `offset`)
- `GET /api/scores/calibration-snapshots/{sport}/{snapshot_id}` — fetch full doc

**Request body** (same shape as simulate/compare + `label` + `notes`):
```json
{"sports":["nba"],"limit":500,"label":"...","notes":"...","variants":[{"name":"baseline","override_config":{}},...]}
```

**Storage**: per-sport collections `{sport}_calibration_runs`; lean docs (~10KB each), no full prop dumps. Indexes: `uniq(snapshot_id)`, `created_at desc`, `label`.

**Stored fields**: `snapshot_id` (uuid hex), `sport`, `created_at`, `label`, `notes`, `limit_applied`, `source_timestamps` (`live_props_fetched_at`, `score_collection_latest`), `baseline_variant`, `variants_meta[]` (per-variant: override_config, processed, skipped, duration_ms, tier/quality/pp_category distributions, 3-sample `top_samples_lean`), `tier_counts_table`, `tier_deltas_vs_baseline`, `war_zone_movers_vs_baseline` (counts + 10-key samples), `tier_canonical_key_overlap_vs_baseline`, `top_sample_overlap_vs_baseline`, `summary`.

**Validated zero mutation**: mlb/nba prop_scores, mlb/dg cached_board, mlb/dg live_props.fetched_at all UNCHANGED after two snapshot creations. Only `nba_calibration_runs` grew (0 → 2 docs as expected).

## Upcoming Tasks
- P1: Google/Apple OAuth (via `integration_playbook_expert_v2`)
- P1: Stripe payments (via `integration_playbook_expert_v2`)
- P2: Wind Tunnel weather API
- P2: Dashboard.jsx refactor
- Phase 5: Drop corrupt/legacy collections — pending user approval:
  - Injury: `live_injuries`, `dg_injuries`, `bdl_injuries`
  - Elite tier (now deprecated after April 18 migration): `elite_safe_haven`, `elite_front_lines`, `elite_war_zone`

## April 18, 2026 — UNDER Pipeline Rebuild (3 layers, production-shipped)

**Layer 1: UNDER tp math** (`services/scoring/adapters/nba_scoring.py:601`)
Flipped `tp` to `100 - tp_over` for UNDER picks. Market-implied probability is
now correctly side-aware.

**Layer 2: UNDER gate_tp replacement** (`services/scoring/scoring_stack.py` +
`_NBAGateSorter.UNDER_TP_FLOORS`). UNDER `gate_tp` replaced with a
model-confidence floor (path c, BALANCED): safe_haven 75%, front_lines 65%,
war_zone 60%. OVER behaviour fully preserved. Result: 21 UNDERs now qualify
for front_lines (none for safe_haven/war_zone due to routing/ceiling gates
which remain OVER-semantic — tracked as separate scope).

**Layer 3: UNDER badge + Gemini** (`routes/ferrari_tiers.py` +
`services/vision_intel_service.py`)
- `_apply_under_badge_rewire()`: strips OVER-only badges from UNDER picks,
  re-derives `floor_lock` / `lasso_high_edge` from score-doc fields.
- `_enrich_under_picks_with_gemini()`: JIT parallel single-prop Gemini calls
  via `GOOGLE_API_KEY` + `gemini-3-flash-preview` (same key/model as OVERs).
  `VisionIntelService.analyze_prop_strict()` returns None on missing
  prop_id echo — prevents fallback pollution in the cache.
- Cached on `nba_prop_scores.vision_intel` keyed by `canonical_key`; TTL
  compares `vision_intel_generated_at` vs `computed_at`.
- Prompt extended with direction-aware DvP interpretation +
  prop-isolation instruction. `prop_id` uses `canonical_key` to eliminate
  cross-prop contamination.

## April 18, 2026 — Ferrari Tier Migration to `nba_prop_scores`
NBA Dashboard tier endpoints (`/api/v3/ferrari/safe-haven`, `/front-lines`, `/war-zone`)
were rewired to read from `nba_prop_scores` where `version_tag='final-nba'` and `tier`
matches, sorted DESC by `vision_score`. Enrichment (intel_suite, hit rates, headshots,
team/opponent, prices) overlayed from `dg_cached_board` via natural key
(event_id, player_name, stat_type, line, direction). Legacy `elite_*` collections
no longer feed the UI.

Verification (hard-audit mode):
- DB: safe_haven=21, front_lines=54 (48 OVER + 6 UNDER), war_zone=39, all tagged `final-nba`
- Board join coverage: 110/114 (96.5%); 4 misses fall back gracefully
- Live API: 0 duplicates at (player, stat, line, direction); strict tier isolation;
  DESC sort confirmed; UNDER props now surface in front_lines (e.g. Desmond Bane REB 6.5
  UNDER vs 99.3, Brandon Miller PRA 28.5 UNDER vs 99.0, James Harden PRA 34.5 UNDER)
- Frontend: Dashboard renders cleanly; Christian Braun duplicate ghost from legacy
  `elite_safe_haven` gone (count: 0)

Key helper: `_get_nba_tier_picks_from_scores(tier, limit)` +
`_build_nba_board_lookup()` + `_merge_score_with_board()` in
`/app/backend/routes/ferrari_tiers.py`.

## Key Files
| File | Purpose |
|------|---------|
| `services/rebuild_coordinator.py` | Event-driven coordinator |
| `services/event_bus.py` | Async pub/sub for BoardEvents |
| `services/watchers.py` | Injury, game clock, odds delta watchers |
| `services/unified_pipeline.py` | Shared pipeline framework |
| `services/adapters/nba_adapter.py` | NBA pipeline adapter |
| `services/adapters/mlb_adapter.py` | MLB pipeline adapter |
| `services/market_moves_engine.py` | Board diff and exit classification |
| `services/injury_normalization.py` | Structural/display firewall |
| `services/injury_sensor.py` | Multi-source polling and merge |
| `services/oracle_apex_service.py` | NBA Safe Haven scoring |
| `services/mlb_tier_sorter.py` | MLB tier scoring |

## APScheduler Persistence (Phase 2 — COMPLETE Apr 18, 2026)
- **Problem**: interval jobs in `MongoDBJobStore` had their `next_run_time`
  overwritten on every backend restart because `add_job(..., replace_existing=True)`
  unconditionally resets persisted trigger state. In production this delayed
  hourly syncs by up to 60 min and the 5-min `live_injury_check` by up to 5 min
  after every redeploy.
- **Fix**: `_register_interval_job()` helper in `server.py` queries
  `scheduler_jobs` in MongoDB directly (scheduler.get_job() is not reliable
  pre-start — it only consults pending jobs). If the job id is already
  persisted, skip re-registration so next_run_time survives. Cron jobs keep
  using `replace_existing=True` (deterministic next_run_time from cron
  expression).
- **Hard verification**: BEFORE vs AFTER restart dump — 5 of 6 interval jobs
  showed exactly 0.0s drift; 6th (`live_injury_check`) fired legitimately at
  its preserved timestamp `05:36:19.597429` (confirmed by APScheduler's own
  "scheduled at" log line) and auto-advanced one interval.

## Phase 3 — Targeted Injury-Triggered Rescore (COMPLETE Apr 18, 2026)
- **Problem**: without targeted rescore, any single injury change had to wait
  for the next hourly full sync (up to 60 min + full-slate recompute of 2185
  props) before the Dashboard reacted.
- **Fix**: `services/injury_triggered_rescore.py` now subscribes to
  `BoardEvent(injury_change)` on the event bus and, for each high-severity
  NBA change, executes a scoped recompute:
    1. resolves impacted players = injured player(s) + same-team teammates
       via `dg_cached_board` (canonical "players with props today" source;
       `dg_live_props` has no `team` field)
    2. monkey-patches `NBAScoringAdapter.load_live_props` to return ONLY the
       impacted player set, then calls `services.scoring.recompute.recompute()`
       — identical scoring stack, identical gate logic, just scoped
    3. patches `dg_cached_board` for each impacted player: refreshes
       `injury_status` (self) + `injured_teammates` (OUT/DOUBTFUL same-team)
       + `synced_at` / `last_injury_rescore_at`
  Fixed pre-existing latent bugs in the service:
  `NBAScoring` → `NBAScoringAdapter` (import), missing `db=` / stray
  `config={}` kwarg in `recompute()` call.
- **Hard verification** (Fred VanVleet HOU trigger): all 4 assertions
  passed — 7/7 HOU board docs patched, 158/158 HOU prop_scores advanced
  `computed_at`, 0 ATL control leakage on either collection. End-to-end
  latency 2.2 s vs ~60 min full-sync wait. Live `/api/v3/ferrari/*`
  endpoints return HOU picks with the exact fresh `computed_at`
  timestamp — no hourly sync required.
- **Regression harness**: `/app/backend/tests/phase3_injury_rescore_verify.py`.

## Injury Rescore Observability Endpoint (Internal, Apr 18, 2026)
- `GET /api/injury-rescore-stats` — read-only snapshot of
  `InjuryTriggeredRescore.stats()`. Returns `events_received`, `recomputes`,
  `last_latency_ms`, `last_players_patched_count`, `last_trigger`.
- `GET /api/full-sync-stats` — read-only snapshot of the last full NBA
  rebuild, sourced from `RebuildCoordinator._metrics[last_publish_counts][nba]`
  (zero new persistence, no hot-path impact). Returns `last_full_sync_at`,
  `last_full_sync_duration_ms`, `last_full_sync_props_written`, `last_trigger`
  (includes `event_type` + `source` so hourly-scheduled runs are
  distinguishable from manual / injury-driven full rebuilds).
- Both endpoints share one auth helper: `X-Admin-Token` must match env
  `ADMIN_DEBUG_TOKEN`. Env unset ⇒ **503 (disabled by default)**; missing/wrong
  header ⇒ **401**. Off-by-default in any environment where the operator
  hasn't opted in.

## Phase 6.5 Step A — Canonical-Naming Config Layer (Apr 18, 2026)
- **New module**: `config/collections.py` — single source of truth for
  collection names across the universal board engine. Exports
  `resolve(sport, concept)`, `canonical_name(sport, concept)`,
  `has_legacy_override()`, `migration_status()`, plus the full
  `SUPPORTED_SPORTS` / `CANONICAL_CONCEPTS` / `SPORT_OVERRIDES` dicts.
  Each legacy collection (NBA `dg_*`, `bdl_*`, `ferrari_*`) is declared
  once in `SPORT_OVERRIDES`; the resolver transparently returns the
  legacy name so no reader breaks. A migration retires a collection by
  deleting one line from `SPORT_OVERRIDES`.
- **Wiring**: `services/board/adapters/base.py::SportBoardAdapter` now
  uses `resolve()` internally for `live_props_collection`,
  `scores_collection`, `cached_board_collection`. NBA + MLB adapters no
  longer hardcode any collection name — they set only `sport` +
  `version_tag`. Adding NFL is literally a 3-field class declaration.
- **Observability**: `GET /api/collection-migration-status` (same
  `X-Admin-Token` gate) reports the state of all 54 (sport, concept)
  pairs — `current` vs `canonical`, `migrated: bool`, plus a rollup
  summary. Drives Phase B/C/D migrations collection-by-collection.
- **Coexistence**: the older `config/db_config.py::get_collection_name`
  (50+ existing callers) is UNCHANGED. `config/collections.py` is the
  NEW authoritative layer for the universal board engine; the two will
  converge once Phase B/C/D complete.
- **Hard verification**:
  1. `resolve()` returns correct current names for every pair —
     NBA `live_props` → `dg_live_props` (LEGACY), NBA `prop_scores` →
     `nba_prop_scores` (CANONICAL), MLB all three → canonical.
  2. All 6 tier reader routes serve 200 with identical pick counts
     before and after the adapter rewire.
  3. Migration endpoint returns `{total_pairs: 54, migrated: 32,
     pending: 22}` — zero DB I/O, zero side-effects.
- **Current migration state**: 32/54 pairs already on canonical names.
  22 pending across NBA's `dg_*`, `bdl_*`, `ferrari_*` legacy namespaces
  — each to be retired via the Phase B/C/D dual-write playbook
  documented in `/app/memory/COLLECTION_NAMING_AUDIT.md`.

## Universal Multi-Sport Board Engine — Phase 6 Steps 1-4 (COMPLETE Apr 18, 2026)
- **Engine vs adapter split**: `services/board/` introduced as the
  universal layer. Sport-agnostic modules (`reader.py`, `scanner.py`,
  `adapters/__init__.py` registry) never branch on sport. Each sport lives
  in `services/board/adapters/<sport>.py` implementing `SportBoardAdapter`
  (sport/version_tag/collection names, tier sort key, capacity, canonical
  identity, game-start extraction, score_batch reserved for Step 5).
- **Universal pool schema additions**: every `{sport}_prop_scores`
  document now carries `active`, `inactive_reason`, `active_changed_at`,
  `game_start_utc` — set at scoring time in `services/scoring/recompute.py`
  + persisted by `services/scoring/prop_scores_store.py`. Two new
  compound indexes added automatically on every scores collection:
  `idx_tier_active_vision` (covers the universal board query) and
  `idx_game_start_active` (covers the 60s scanner update).
- **Universal reader**: `services/board/reader.get_board(db, sport, tier,
  limit)` is the ONE read path every tier endpoint now uses. NBA's helper
  `_get_nba_tier_picks_from_scores` delegates to it; MLB's three tier
  handlers in `routes/ferrari_tiers.py` query it directly (replacing their
  reads from `mlb_safe_haven/front_lines/war_zone` storage collections).
  All 6 tier endpoints — `/api/v3/ferrari/*` (NBA) and
  `/api/v3/mlb/ferrari/*` (MLB) — return 200 with the same JSON shape as
  before (hard-verified).
- **Universal 60s game-start scanner**: `services/board/scanner.scan_all`
  iterates `registered_sports()` and flips every `active=True` prop whose
  `game_start_utc <= now` to `active=False` with `inactive_reason=
  'game_started'`. Single indexed update_many per sport per tick.
  Registered in `server.py` as interval job `universal_game_start_scanner`
  (60 seconds); survives restarts via MongoDBJobStore.
- **Hard verification (all 5 checks)**:
  1. All 6 reader routes (3 NBA + 3 MLB) return 200 + populated picks
     through `get_board()` — confirmed by log trace + direct HTTP probes.
  2. Response JSON shape unchanged pre/post — identical top-level keys.
  3. Simulated synthetic tipped-off prop on both NBA + MLB → scanner
     flipped both to `active=False`, `inactive_reason=game_started`,
     `active_changed_at` timestamped. Universal reader excluded both in
     a follow-up query.
  4. Scanner worked for BOTH registered sports in the same tick
     (`{nba: {last_flips: 1}, mlb: {last_flips: 1}}`).
  5. Post-restore board counts + top-3 orderings unchanged for every
     sport × tier (NBA 3/5/1 from prior Phase 3 HOU-only state, MLB
     10/10/10 limit=10; 20/20/20 limit=20).
- **Future sport enablement**: adding NFL = 2 files
  (`services/board/adapters/nfl.py` + one line in the registry). Scanner,
  reader, observability loop automatically cover it. No engine changes.

## MLB Hourly Refresh Job — Shipped (Apr 18, 2026)
- **New scheduled job**: `scheduled_hourly_mlb_full_sync()` in `server.py`
  mirrors `scheduled_hourly_full_sync` but publishes `sport='mlb'`. Registered
  via the same `_register_interval_job()` helper used for NBA, persistent via
  `MongoDBJobStore`, survives restarts (`next_run_time` preserved by our
  Phase-2 fix). Existing `mlb_daily_refresh` (09:23 UTC cron) left in place
  as a deterministic daily anchor.
- **Hard verification**:
  - Boot logs show `Registering new interval job 'hourly_mlb_full_sync'` on
    first restart, `Preserving persisted interval job 'hourly_mlb_full_sync'`
    on the next — restart-safe as designed.
  - Manual trigger through the live backend produced one full MLB pipeline
    (72.1 s, run_id `c11e6d2d`), wrote 30 picks across `mlb_safe_haven`,
    `mlb_front_lines`, `mlb_war_zone`.
  - `/api/v3/mlb/ferrari/*` all return 200 with `synced_at ≈ 1 min` old
    (was 10.5h before).
  - `/api/full-sync-stats?sport=mlb` + combined payload both populated.
  - NBA interval jobs unaffected — all 6 preserved their `next_run_time`
    across the restart.

## Full-Sync Observability — Dual-Sport + MLB Staleness Root Cause (Apr 18, 2026)
- **Endpoint upgrade**: `GET /api/full-sync-stats` now supports:
  - `?sport=nba` → NBA-only payload
  - `?sport=mlb` → MLB-only payload
  - no param → combined `{"nba": {...}, "mlb": {...}}`
  - invalid sport → 401/503/200 unchanged; adds 400 for bad `sport` value.
  Same `X-Admin-Token` gate, still zero persistence (reads from
  `RebuildCoordinator._metrics["last_publish_counts"][sport]`).
- **MLB staleness RCA** (the NBA vs MLB schedule asymmetry):
  - NBA has 6 interval jobs (5-min live_injury_check + 30-min social + 4x
    60-min syncs) hitting the coordinator every cycle.
  - MLB has **ONE** scheduled job — `mlb_daily_refresh` CronTrigger(hour=9,
    minute=23 UTC) — publishing `BoardEvent(sport='mlb',
    event_type='scheduled_safety')`. Between daily crons the ONLY way MLB
    gets refreshed is ad-hoc `injury_change` events picked up by
    `RebuildCoordinator.handle_event` (which performs FULL MLB rebuilds).
  - Evidence today: only 2 MLB pipeline runs — 07:15:09 (`scheduler_daily_mlb`)
    and 18:13:30 (`injury_sensor` for Justin Slaten). 10h58m gap in between.
  - `mlb_live_props` is the deepest stale layer (22.2h — Odds-API MLB sync
    is once-daily, no interval refresh).
- **User-facing impact (confirmed)**:
  - `/api/v3/mlb/ferrari/safe-haven|front-lines|war-zone` receive 2258/2246/2246
    hits respectively — these serve from `mlb_safe_haven` etc. and DO suffer
    stale reads during the ~10h gaps between scheduled refreshes.
  - `/api/v3/mlb/vacuum/live-alerts` (1871 hits) is refreshed by every injury
    event so is typically fresher.

## Vision Intel Prompt Refresh — "The books" Ban (COMPLETE Apr 18, 2026)
- **Problem**: every Gemini-generated summary leaned on "the books" / "books are" /
  "printing money" / "metronome" / "begging us" openers. Feedback was unanimous
  that every prop read the same.
- **Fix (3 surgical edits)**:
  - `services/gemini_scout_engine.py::SYSTEM_PROMPT` — removed "call out the books"
    directive, replaced "The books are sleeping" example, dropped "metronome" RL
    hook, added a tail-block of ABSOLUTE RULES listing every banned phrase + a
    mandatory-variety clause. (Placement at the END of the prompt matters —
    Gemini's RL weights the tail-most constraints heaviest.)
  - `services/gemini_scout_engine.py::_fallback()` — removed the hard-coded
    "edge the books haven't priced in" sentence; now uses a neutral edge
    framing.
  - `services/gemini_scout_engine.py` — added `_contains_banned()`,
    `_sanitize_banned()`, `_rewrite_if_banned()` as a safety net: every returned
    summary is scanned; offenders get ONE targeted Gemini rewrite, then a
    deterministic text-level sanitizer as the final fallback. Wired into BOTH
    single-prop and batch generation paths.
  - `services/vision_intel_service.py::VISION_INTEL_BATCH_PROMPT` — matching
    ABSOLUTE RULES block added for the NBA UNDER path.
  - `services/mlb_vision_intel.py::MLB_VISION_INTEL_BATCH_PROMPT` — matching
    ABSOLUTE RULES block added (removed "the line is disrespectful", "book is
    sleeping", "printing money" hooks).
- **Hard verification** — after clearing both the JSON cache file and all
  `vision_intel` fields on `dg_cached_board` + `nba_prop_scores`, then
  restarting backend to flush the rolling_cache_manager's in-memory dict and
  forcing a fresh rebuild:
  - 9/9 unique NBA summaries contain **zero banned phrases** (was 9/9 before).
  - Opening 3-word frequency shows maximum distribution (1x or 2x per opener,
    no opener dominating).
  - All three Ferrari tier endpoints return 200 with populated vision_intel.
  - GEMINI_ENRICH log shows `success=30 failed=0` every rebuild (pipeline
    itself unchanged).
- **Operator note**: cache invalidation is required to flip existing props.
  The safety-net validator covers FUTURE generations; stale entries in the
  file are preserved until the rolling_cache_manager sees a new prop_id. If
  you ever need to force a wholesale refresh, remove
  `/app/backend/data/nba_master_active_cache.json` + clear `vision_intel` on
  `dg_cached_board` + restart backend + trigger a coordinator rebuild. This
  is documented here for repeatability.

## Phase 5 Step 1 — Kill dead Gemini writes to `elite_*` (COMPLETE Apr 18, 2026)
- **Problem**: `unified_pipeline._run_gemini_enrichment` was doing ~32 UPDATE
  ops per full rebuild into `elite_safe_haven/front_lines/war_zone` to stamp
  `vision_intel` / `vision_summary` fields. The Dashboard never reads
  `vision_intel` from these collections — OVER-side comes from the
  `nba_master_active_cache.json` overlay, UNDER-side from `nba_prop_scores`
  (written by `routes/ferrari_tiers._enrich_under_picks_with_gemini`).
  Every one of these DB writes was dead weight.
- **Fix**: removed the `update_one` loop in `_run_gemini_enrichment` while
  preserving the success/failed stats counter and the JSON cache write
  (which is what the Dashboard actually reads). No reader changes,
  `_atomic_publish` elite_* writer left intact (Steps 2-3 scope).
- **Hard verification** (post-fix rebuild):
  - MongoDB profiler level 2, clean capture window → elite_safe_haven
    UPDATE=0, elite_front_lines UPDATE=0, elite_war_zone UPDATE=0
    (previously 12/10/10).
  - `/api/v3/ferrari/{safe-haven, front-lines, war-zone}` all return 200
    with picks; every pick carries non-empty `vision_intel` (320-408 chars
    on first-3-per-tier sample).
  - GEMINI_ENRICH log still shows `success=30 failed=0 cache=...json` per
    rebuild (no regressions in the enrichment pipeline itself).
  - `/api/full-sync-stats` reflects latest rebuild with 30 props written
    across tier collections; `event-bus/stats` shows market_moves subscriber
    still attached.
- **Cleanup**: profiler restored to level 0, `system.profile` back to 1MB
  default cap, `orphan_audit_capture` one-shot snapshot dropped. Audit
  document preserved at `/app/memory/PHASE5_ORPHAN_AUDIT.md`.

## Canonical Collection Health Check — one-shot startup audit (Apr 18, 2026)
- **Module**: `services/board/health_check.py::run_canonical_collection_health_check`.
- **Wired**: `server.py` startup event, non-blocking (try/except so a
  Mongo hiccup never blocks boot).
- **Audit scope**: every (sport, concept) pair in `config/collections.py`.
  Four warning classes, each emitted with a distinct prefix for grep /
  alerting:
  - `OVERRIDE_MISSING` — `SPORT_OVERRIDES` points at a legacy coll that
    does not exist in the DB (silent-divergence trap).
  - `CANONICAL_BLEED` — both legacy and canonical carry data; reads
    still route to legacy (partially-migrated writer).
  - `CANONICAL_READY` — canonical exists populated, legacy still
    authoritative; migration ready to cut over by flipping one line
    in `SPORT_OVERRIDES`.
  - `LEGACY_EMPTY` — override points at an empty coll with no
    canonical counterpart (dead writer path).
- **Hard verification** (logged + sibling hash assertions):
  1. Clean run: 0 warnings, 32/54 pairs canonical, 22 pending (expected
     overrides, all with data) — INFO-level summary line.
  2. Synthetic seed of two bleed conditions (populated `nba_live_props`
     and `nba_cached_board` alongside their legacy counterparts) →
     exactly 2 CANONICAL_BLEED warnings emitted with raw doc counts.
  3. Synthetic OVERRIDE_MISSING + CANONICAL_READY conditions → exactly
     one warning of each class emitted with the expected structured
     findings.
  4. Post-cleanup re-run → back to 0 warnings. All transitions
     observable end-to-end.
- **Runtime cost**: one `listCollections` + up to ~44 `estimatedCount`
  calls, bounded by `SUPPORTED_SPORTS × CANONICAL_CONCEPTS`. Pure
  log-only. Fires once per pod boot (uvicorn reload produces two fires
  which is expected).

## Universal Real-Time Ingest Engine — Step 5 (Apr 18, 2026)
- **New module**: `services/board/engine.py`. One sport-agnostic handler
  `on_new_props(db, sport, canonical_keys)` scores ONLY the supplied
  canonical keys and UPSERTs them into `{sport}_prop_scores`. The
  universal board reader surfaces them instantly — no atomic swap, no
  full rebuild, no per-sport branching.
- **Writer contract**: `services/scoring/prop_scores_store.py::write_versioned_scores`
  now accepts `mode={"replace","upsert"}`. Replace (default) preserves
  the full-recompute semantics (delete_many then insert_many). Upsert
  issues per-doc `update_one({canonical_key, version_tag}, $set=doc,
  upsert=True)` — NEVER destroys sibling rows under the same
  version_tag. This is what makes scoped real-time ingest safe.
- **Scoper**: `services/scoring/recompute.py::recompute_sport` gained
  a `props: Optional[List[Dict]]` kwarg. When supplied, the adapter's
  `load_live_props` is bypassed entirely and the caller's filtered set
  is scored. This was necessary because `get_scoring_adapter()` returns
  fresh instances per call (monkey-patching the instance in the engine
  did not propagate). The engine now pre-filters raw props in its own
  frame (matching adapter-built `canonical_key` against the target
  set) and passes the filtered list directly into `recompute_sport`.
- **Subscriber**: `subscribe_new_props_handler(db)` wired at startup
  in `server.py`. Single subscription listens for
  `BoardEvent(event_type='new_props', sport=..., metadata={'canonical_keys': [...]})`.
  Per-sport asyncio.Lock serializes concurrent scoped recomputes for
  the same sport.
- **Observability**: new endpoint `GET /api/board-engine-stats`
  (same `X-Admin-Token` gate as other admin endpoints). Reports per
  sport: `events_received`, `events_processed`, `events_skipped`,
  `props_upserted`, `last_event_at`, `last_source`, `last_keys_count`,
  `last_written`, `last_skipped`, `last_duration_ms`, `last_error`.
- **Hard e2e verification**
  (`/app/backend/tests/step5_realtime_ingest_verify.py`):
  - NBA: 1 canonical key → 1 raw live prop matched → 1 doc upserted to
    `nba_prop_scores`. Total docs 158 → 159. 158 siblings
    byte-identical (sha256 of all other-key docs matched pre/post).
    `active=True`, `tier=unqualified`, `vision_score=0.0`. Ingest
    16.5s end-to-end.
  - MLB: 1 canonical key (`mlb|...|Nico Hoerner|Hits|2.5|OVER`),
    pre-existing score row. `computed_at` advanced from
    `19:49:21` → `19:51:14`. Total docs 4944 → 4944. 4943 siblings
    byte-identical. `active=True`. Ingest 41s end-to-end
    (dominated by VK model inference on the full-slate pre-filter;
    latency optimisation tracked as a follow-up).
  - Live-backend `GET /api/board-engine-stats` returns 200 with
    per-sport zero counters (verification ran in a separate process
    with its own engine state — expected).
- **Known follow-ups (not blocking)**:
  1. ~~Pre-filter optimisation~~ — **SHIPPED Apr 18, 2026**. See
     "Step 5 Hot-Path Optimisation" below.
  2. `vision_score` percentile-rank normalization during upsert-mode
     runs against the current batch only (1 doc → percentile=100).
     Should compute against the existing pool's `vision_score_raw`
     distribution for accurate positioning. Documented in the engine
     module docstring.
  3. `odds_sync_service.py` → emit `BoardEvent('new_props', ...)`
     with the delta canonical_keys after each sync. Deferred to the
     Step 6 session (legacy-writer retirement) since firing alongside
     the current full-rebuild coordinator would duplicate work. Wiring
     this is gated by the 48h observation window per user directive.

## Step 5 Hot-Path Optimisation — `adapter.canonical_key()` fast filter (Apr 18, 2026)

### Problem
The initial Step 5 ingest path called `scoring_adapter.build_context()`
on every raw live prop to reconstruct `canonical_key` for scoped
filtering. `build_context` triggers VegasKiller model inference, game
log preloads, and advanced stats lookups — all of which the scoped
filter does not need. Per-event latency: **NBA 16.5 s, MLB 41 s**.

### Fix (3 surgical edits)
1. `services/board/adapters/nba.py::NBABoardAdapter.canonical_key()`
   — pure-string reconstruction of `canonical_key` mirroring the NBA
   scoring adapter's format exactly. Uses the same
   `_NBA_STAT_TYPE_MAP` (duplicated locally with a comment flag
   anchoring it to the scoring adapter). Zero DB I/O, zero model
   inference.
2. `services/board/adapters/mlb.py::MLBBoardAdapter.canonical_key()`
   — reads the pre-computed `canonical_key` field on the prop
   (present on MLB live docs) with a reconstruction fallback.
3. `services/board/engine.py::on_new_props()` — rewritten filter
   phase:
   - Parse target canonical_keys → extract `event_id` and
     `player_name` subsets.
   - DB-side narrow query: `live_props.find({'event_id': {'$in': [...]},
     'player_name': {'$in': [...]}})` cuts the scan from O(N_total) to
     O(N_matched_group).
   - Iterate returned docs calling `board_adapter.canonical_key(p)` —
     ~0.001 ms per prop — and keep only those in the requested set.
   - Defensive fallback: any prop whose fast-path returns `None` is
     re-attempted via `scoring_adapter.build_context()` (belt +
     suspenders).

### Hard verification
| Metric | Pre (build_context filter) | Post (canonical_key filter) | Speedup |
|---|---|---|---|
| NBA 1 key | 16,563 ms | 124 ms | **133×** |
| MLB 1 key | 40,998 ms | 333 ms | **123×** |
| NBA 300-prop canonical_key parity | — | 300/300 match | — |
| MLB 300-prop canonical_key parity | — | 300/300 match | — |

Warm-cache scaling:
| Batch | NBA | MLB |
|---|---|---|
| 5 keys  | 952 ms (cold) | 458 ms |
| 20 keys | 248 ms (warm) — 12 ms/prop | 548 ms — 27 ms/prop |

Correctness preserved in every run:
- NBA bs=1/5/20: total docs stable at 159, sibling sha256 hash
  byte-identical pre/post.
- MLB bs=1/5/20: total docs stable at 4944, sibling sha256 hash
  byte-identical pre/post.
- `active=True` / `computed_at` advanced on all upserted docs.
- Post-restart 60s scanner log line
  (`[GAME_START_SCANNER] mlb: 21 props → inactive (game_started)`)
  confirms scanner-engine integration unchanged.
- All 6 tier endpoints return 200 with pre-existing counts
  (NBA 3/5/1, MLB 0/0/0 as expected at 20:02 UTC with all today's
  MLB games having tipped).

### Target met
"Sub-1s or as close as realistically possible" → achieved for NBA
(124 ms) and MLB (333 ms) on single-key events. Sub-1s also at 20-key
warm batches (248 ms NBA, 548 ms MLB). The remaining latency is pure
scoring work (VK model / MLB HF model inference on matched props) —
there is no filter overhead left to cut.

## Step 5 A/B Observation — `odds_sync` → `new_props` Emission + Drift Audit (Apr 18, 2026)

### What shipped
Wired real-time `new_props` emission into every sport's odds-sync
path and added a drift-audit ledger that lets the 48h Step 6
observation window run as a live A/B between the real-time path and
the legacy full-rebuild coordinator.

### Components
1. `services/board/delta_publisher.py`
   - `capture_live_props_keys(db, sport)` — reads the current
     canonical_keys from `{sport}_live_props` via the board adapter's
     hot-path `canonical_key()`. Same identity function the engine
     uses — zero format divergence.
   - `publish_new_props_delta(sport, pre_keys, post_keys, source)` —
     computes `post - pre`, emits `BoardEvent('new_props', ...)` only
     when the delta is non-empty AND smaller than the guardrail
     (`_MAX_DELTA_FOR_REALTIME=500`). Full wipe-reinserts are logged
     with `reason='delta_too_large'` and skipped; the legacy
     coordinator handles them.

2. `services/board/drift_audit.py`
   - Per-sport bounded ring buffer (500 entries / sport, ~100 KB RAM).
   - `record_realtime_upsert(sport, score_docs, source)` — engine
     appends one snapshot per upsert capturing
     (canonical_key, tier_rt, vision_score_rt, quality_source_rt,
      computed_at_rt, active_rt, recorded_at, source).
   - `audit(db, sport, limit)` — one round-trip `find({version_tag,
     canonical_key: $in})`, classifies each ledger entry as
     `converged | tier_changed | vision_score_drift | missing |
     inactive`. Vision-score drift threshold: 1.0 points on the
     0-100 scale.
   - `snapshot(sport)` — raw ledger dump for observability.

3. `services/scoring/recompute.py` — `recompute_sport` now returns
   `score_docs` in its payload when `write_mode="upsert"` so the
   engine can feed the ledger without a second read.

4. `services/board/engine.py::on_new_props()` — post-upsert hook
   calls `drift_audit.record_realtime_upsert(...)`. Failures are
   logged, never raised.

5. `services/odds_sync_service.py` (NBA path) — snapshots
   `capture_live_props_keys(db, 'nba')` BEFORE `delete_many`, then
   `publish_new_props_delta(...)` AFTER `insert_many`. Circuit-breaker
   branch still short-circuits before the hook so a preserved-data
   early-return doesn't emit stray events.

6. `services/universal_odds_sync.py` (MLB + future sports via
   `MLBMasterSync.STEP 1 → odds_service.sync_sport_props(sport)`) —
   same pre/post snapshot + delta publish, embedded inside the
   sport-agnostic universal sync. Adding NFL ingest automatically
   inherits `new_props` emission.

7. `routes/admin.py::GET /api/board-drift-audit` — admin-gated
   observability endpoint. `?sport=nba|mlb` scopes to one sport; no
   param returns both. Returns `{ledger: {...}, audit: {...}}` per
   sport. Zero DB writes.

### Hard verification
(`/app/backend/tests/step5_ab_observation_verify.py` — run end-to-end
for NBA + MLB)

| Check | NBA | MLB | Result |
|---|---|---|---|
| [1] Delta publisher — 3 synthetic keys added to pre-set | emitted=True, added=3 | emitted=True, added=3 | ✅ |
| [2] Guardrail — 600-key synthetic delta | emitted=False, reason=`delta_too_large` | emitted=False, reason=`delta_too_large` | ✅ |
| [3] E2E chain — publish → event_bus → engine.on_new_props → upsert | 924 ms, 3 upserts, `computed_at` advanced, `active=True`, 3 ledger entries with `source='verify_e2e'` | 415 ms, same | ✅ |
| [4a] Audit right after RT upsert | converged=3, divergent=0 | converged=3, divergent=0 | ✅ |
| [4b] Simulated legacy divergence (direct Mongo write on 2/3 keys) | key[0] tier: unqualified → safe_haven; key[1] vs: 0.0 → 42.0; key[2] untouched | same | ✅ |
| [4c] Audit detects drift | converged=1, tier_changed=1, vision_score_drift=1, divergence_ratio=0.667 | converged=1, tier_changed=1, vision_score_drift=1, divergence_ratio=0.667 | ✅ |

Detected `divergence_samples` include the raw RT vs current
payloads (tier, vision_score, quality_source, computed_at) so
divergences are actionable without further queries.

### What this gives us
- The 48h observation window is now a live A/B: legacy coordinator
  overwrites real-time writes, drift_audit reports exactly how often
  they disagree and on which keys / fields.
- No user-visible change: readers still go through
  `services/board/reader.py::get_board()`; frontend untouched.
- No Step 6 retirement: legacy writers (`_atomic_publish` elite_*,
  `mlb_safe_haven/front_lines/war_zone`) still firing. Phase 5 Steps
  2-7 untouched. Nothing retired this pass.
- Safety guardrails:
  - `delta_too_large` skip prevents full wipe-reinsert floods.
  - Engine's per-sport asyncio.Lock serialises scoped recomputes.
  - All delta-publisher + drift-audit code paths use try/except
    around the event bus and log-only on failures — odds_sync
    latency and correctness are unchanged if any of this fails.

### Observability
- `/api/board-engine-stats` — real-time ingest counters per sport.
- `/api/board-drift-audit?sport=nba|mlb` — A/B convergence report.
- Structured log prefixes: `[DELTA_PUB]`, `[BOARD_ENGINE]`,
  `[DRIFT_AUDIT]` — greppable independently.

## Drift Ledger Persistence — 72h TTL + Rolling Windows (Apr 18, 2026)

### What shipped
Upgraded the drift audit from in-process memory to a hybrid (memory +
MongoDB) architecture so the 48h Step 6 observation window survives
backend restarts and produces a real historical convergence record.

### Components
1. **Collection**: `board_drift_ledger` (new).
   - Doc shape: `{sport, canonical_key, source, observed_at (native
     UTC datetime), tier_rt, vision_score_rt, quality_source_rt,
     computed_at_rt, active_rt, version_tag}`.
   - Append-only from the engine's hot path; no other writers.
   - Indexes:
     - `ttl_observed_at_72h`: `{observed_at:1}` with
       `expireAfterSeconds=259200` — auto-expiry after 72 h.
     - `idx_sport_observed_at_desc`: covers every rolling-window query.
     - `idx_ck_observed_at_desc`: per-key drift history for
       ad-hoc debug lookups.
   - Ensured at startup via
     `services/board/drift_audit.ensure_persistent_indexes(db)` —
     idempotent, drops+recreates the TTL index if a prior version
     had a different `expireAfterSeconds` so semantics never drift.

2. **Writer**: `services/board/drift_audit.persist_entries(db, sport,
   score_docs, source)` — one `insert_many(ordered=False)` per event
   (1 round-trip regardless of batch size). Called synchronously
   from `engine.on_new_props()` after `record_realtime_upsert`.
   Measured ≤ 5 ms / event in the verifier. Never raises.

3. **Reader**: `services/board/drift_audit.audit_persisted(db, sport,
   windows)` — for each rolling window (`1h / 6h / 24h / 48h`),
   pulls the entries in that slice, batch-fetches current score
   docs in one `find({canonical_key: $in})`, and classifies each
   entry as `converged | tier_changed | vision_score_drift |
   missing | inactive`. Returns counts + a capped list of
   divergence samples.

4. **Endpoint** (`routes/admin.py::GET /api/board-drift-audit`) now
   returns two sections per sport:
   - `in_memory`: current-process ring buffer + its audit
     (rebuilds on restart — still the authority for the most-recent
     ≤500 events).
   - `persisted`: `{collection, ttl_seconds, total_entries_72h,
     latest_observed_at, windows: {1h, 6h, 24h, 48h}}`.
   - Structure with `?sport=` param: `{sport, in_memory, persisted}`.
   - Without sport: `{by_sport: {nba: {...}, mlb: {...}}}`.

### Hard verification
(`/app/backend/tests/drift_ledger_persist_verify.py`)

| Step | NBA | MLB |
|---|---|---|
| [1] TTL index exists with `expireAfterSeconds=259200` | ✅ `ttl_observed_at_72h` | same |
| [2] Engine synchronously writes to `board_drift_ledger`; doc carries all 9 required fields + native datetime `observed_at` | 3 docs written, 1010 ms publish+handle (cold caches) | 3 docs written, 412 ms |
| [3] `audit_persisted` surfaces rolling windows 1h/6h/24h/48h | entries=3 in every window, ratio=0.0 | same |
| [4] In-memory flush does NOT affect persisted history | `in_memory.count=0`, `persisted.1h.entries=3` | same |
| [5] Simulated tier change (direct Mongo mutation on one RT key) detected by persisted audit | `tier_changed=1, ratio=0.333`, sample shows RT `tier='unqualified'` → current `tier='safe_haven'` | same |

Live backend after restart:
```
GET /api/board-drift-audit?sport=nba
  in_memory.ledger.count = 0             (fresh process)
  persisted.total_entries_72h = 3        (SURVIVED RESTART)
  persisted.windows.1h = {entries=3, converged=2, tier_changed=1,
                          vs_drift=0, missing=0, ratio=0.333}
  persisted.windows.{6h,24h,48h} identical to 1h (all events <1h old)
```

All 6 tier endpoints + 5 admin endpoints return 200. Lint clean.

### Latency impact
- Engine hot path: +1 `insert_many` round-trip ≈ 1-2 ms (measured
  NBA 1010 ms → 1010 ms cold; sub-ms in warm runs).
- Startup: +1 `ensure_persistent_indexes` call, ≤50 ms, non-blocking.
- Endpoint: adds one aggregation pipeline per sport; scales O(entries
  in 48h window). At current volume (~low-hundreds/day expected)
  response time stays sub-100 ms.

### Step 6 gating mechanism
The 48h window now has a measurable SLA:
```
persisted.windows.48h.divergence_ratio ≤ 0.05
```
applied per sport. Operator queries
`GET /api/board-drift-audit` after 48 h; when both sports satisfy
the SLA, legacy writers can be retired in Step 6.

## Observability endpoints (cumulative)
- `/api/board-engine-stats` — real-time ingest counters per sport.
- `/api/board-drift-audit[?sport=nba|mlb]` — A/B convergence report
  with in-memory + persisted (72h TTL) sections.
- `/api/injury-rescore-stats` — targeted injury rescore service.
- `/api/full-sync-stats[?sport=nba|mlb]` — last full rebuild.
- `/api/collection-migration-status` — Phase A naming migration.
- Structured log prefixes: `[DELTA_PUB]`, `[BOARD_ENGINE]`,
  `[DRIFT_AUDIT]`, `[COLL_HEALTH]`, `[GAME_START_SCANNER]` —
  each greppable independently.

## Remaining Roadmap
- **P1**: Step 6 — retire legacy per-sport board writers after the
  48h observation window (started Apr 18 2026 20:02 UTC). Acceptance
  criterion: `/api/board-drift-audit` reports divergence_ratio ≤ 0.05
  across 24+ h of organic odds-sync + coordinator runs for both sports.
- **P1**: `vision_score` pool-wide percentile normalization in
  upsert mode (rank against existing pool distribution instead of
  single-batch).
- **P1**: Phase 5 Step 2 — migrate `market_moves_engine` +
  `injury_advantage` readers off `elite_*`.
- **P1**: Phase 5 Step 3 — retire `_atomic_publish` elite_* writer.
- **P1**: Phase 5 Step 4 — drop `live_injuries` + reroute
  `/api/v3/injuries/live` to `injuries_normalized`.
- **P1**: Phase 5 Step 5 — migrate `usage_spike_detector` + legacy readers
  off `dg_injuries`; stop the injury_service dual-write.
- **P1**: Phase 5 Step 6 — migrate 10 `bdl_injuries` readers to
  `injuries_normalized`; stop `bdl_enhanced_data.sync_injuries()` write.
- **P1**: Phase 5 Step 7 — final collection drops (48-hour observation per
  collection).
- **P1**: Google/Apple OAuth (Emergent-managed).
- **P1**: Stripe payments (pod test keys).
- **P2**: Phase 3.5 — extend injury rescore scope to opponent-team props
  (defensive assignment changes).
- **P2**: Wind Tunnel weather integration for MLB friction.

---
*Last Updated: April 18, 2026*

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

## Remaining Roadmap
- **P1**: Phase 4 — rip deprecated writers out of `live_injuries`,
  `dg_injuries`, `bdl_injuries` pipelines.
- **P1**: Phase 5 — drop legacy collections (`elite_safe_haven`,
  `elite_front_lines`, `elite_war_zone`, `live_injuries`, `dg_injuries`,
  `bdl_injuries`) once no readers remain.
- **P1**: Google/Apple OAuth (Emergent-managed).
- **P1**: Stripe payments (pod test keys).
- **P2**: Phase 3.5 — extend injury rescore scope to opponent-team props
  (defensive assignment changes).
- **P2**: Wind Tunnel weather integration for MLB friction.

---
*Last Updated: April 18, 2026*

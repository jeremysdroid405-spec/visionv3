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

## Upcoming Tasks
- P1: Google/Apple OAuth (via `integration_playbook_expert_v2`)
- P1: Stripe payments (via `integration_playbook_expert_v2`)
- P2: Wind Tunnel weather API
- P2: Dashboard.jsx refactor
- Phase 5: Drop corrupt/legacy collections (`live_injuries`, `dg_injuries`, `bdl_injuries`) — pending user approval

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

---
*Last Updated: April 17, 2026*

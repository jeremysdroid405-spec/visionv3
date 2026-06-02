# Prop Vision — Master Architecture Directive

**Status:** Standing directive (2026-05-08). Binds all future development.
**Precedence:** Overrides any conflicting guidance in PRD.md, handoff summaries, or ad-hoc patches.

---

## Core Principle

There must never be:
- multiple independent board builders
- multiple tier assignment systems
- multiple cached_board writers
- multiple freshness realities
- duplicated sport-specific pipelines that diverge over time

---

## Canonical Architecture

```
INGESTION
    ↓
LIVE PROPS  ({sport}_live_props)
    ↓
SCORING ENGINE  (services/scoring/recompute + delta/detector)
    ↓
prop_scores[version_tag=final-{sport}-rt]      ← CANONICAL LIVE BOARD SOURCE
    ↓
UNIFIED BOARD SNAPSHOT PUBLISHER
    services/board_snapshot_publisher.py::publish_board_snapshot
    ↓
{sport}_cached_board                            ← materialized view
    ↓
API  (routes/ferrari_tiers.py, routes/master_hub.py, …)
    ↓
UI
```

### Invariants
- **Tier assignment** lives ONLY on `prop_scores[final-{sport}-rt]`. No downstream component recalculates, invents, overrides, or mirrors tier values.
- **cached_board** is a materialized/enriched mirror. It NEVER has independent business logic.
- **Delta Engine** may trigger publishing. It may NOT manually patch board docs.
- **master_sync** must use the SAME publisher path. No duplicate builders.
- **Board freshness** derives exclusively from `prop_scores[final-{sport}-rt]` timestamps through the unified publisher.

---

## Universalization Rule

Order of preference when building any new component:

1. **Universal first** — one implementation serving all sports.
2. **Sport-configurable second** — one implementation with per-sport config (YAML / `SCHEDULED_SPORTS` / adapters).
3. **Sport-specific only when mathematically or data-required** (weather/park for MLB, pace/rotation for NBA, drive/usage for NFL, sport-specific stat families, sport-specific badge adapters).

### NOT allowed
- Separate MLB cached_board builder (or NBA equivalent).
- Separate tier pipelines per sport.
- Duplicated route logic per sport.
- Duplicated freshness systems.
- Duplicated scoring write paths.

### Pre-fork checklist (MUST answer before creating any new sport-specific pipeline)
1. Why can't this be config-driven?
2. Why can't this be adapter-driven?
3. Why can't this be data-driven?

If the answer is weak, DO NOT fork.

---

## Mandatory Consolidation Rules

### 1. ONE Board Publisher
`services/board_snapshot_publisher.py::publish_board_snapshot` is the ONLY board snapshot builder/writer.

**Retire:**
- `services/mlb_cached_board_builder.py`
- Any NBA-specific board builders
- Legacy cached_board rebuilders
- Duplicate overlay publishers

### 2. ONE Freshness Reality
Visible board freshness must derive from `prop_scores[final-{sport}-rt]` via the unified publisher.

**Never again:**
- fresh scores + stale board snapshot
- stale cached_board timestamps masking fresh data
- independent freshness paths

### 3. ONE Tier Source
Tier assignment lives ONLY on `prop_scores[final-{sport}-rt]`. No downstream component may recalculate, invent, override, or maintain parallel tier collections.

### 4. ONE Enrichment Model
`cached_board` MAY enrich with: photos, injuries, badges, context, intel, overlays.

`cached_board` MUST NEVER alter: tier, score, recommendation, vision_score, gate outcome.

### 5. No Sport-Specific Forks Without Justification
See pre-fork checklist above.

---

## P1 Cleanup Targets (gated on stabilization sign-off)

1. Retire `services/mlb_cached_board_builder.py` and the duplicate MLB board-rebuild path. Replace every call site with `publish_board_snapshot(db, "mlb")`. Verify NBA + MLB use the identical publisher.
2. Audit remaining duplicated writers, duplicated builders, stale legacy collections, multi-source truths, route-specific board logic, hidden overlay writers. Document or retire each.
3. Produce `SYSTEM_OWNERSHIP.md` (next section) with the complete ownership map.

---

## SYSTEM_OWNERSHIP.md deliverable contract

Must document for every major entity:
- Canonical source
- Single writer
- Allowed readers
- Allowed enrichment layers
- Freshness ownership
- Scheduler ownership
- Cache ownership

Template:

```markdown
### Entity: {name}
- Canonical source: {collection / service}
- Single writer: {module::function}
- Readers (allowed): {list}
- Enrichment (allowed): {list of modules and what fields each may set}
- Freshness stamp owner: {module}
- Scheduler owner: {job id or loop}
- Cache: {location / TTL}
```

---

## Non-Negotiable Design Rule

> If two systems can disagree about freshness, tiers, scores, board state, active picks, or visibility — the architecture is wrong.

The system must converge toward:
- ONE truth
- ONE publish path
- ONE freshness reality
- ONE board pipeline

Without sacrificing: performance, enrichment, failure isolation, sport-specific intelligence.

---

## Enforcement

- Every future PR / patch that touches scoring, board, tiers, or freshness MUST be checked against this directive.
- Every `integration_playbook_expert_v2` or feature call that implies new sport-specific pipelines MUST first run the pre-fork checklist.
- `services/board_snapshot_publisher.py::publish_board_snapshot` is the designated chokepoint. Bypassing it is a bug.


---

## Pipeline Contract (locked 2026-06-02)

There are **exactly TWO pipelines**: PLAYER and TEAM. Each has a
LIVE mode and a BACKTEST mode. Both modes share the same predictor
and the same Vision pipeline; only the input/output endpoints
change.

### Pipeline 1 — Player

```
LIVE                                BACKTEST / REPLAY
─────────────────────────           ─────────────────────────
live player props                   SGO historical player props
        │                                   │
        ▼                                   ▼
PLAYER PREDICTION MODEL    == SAME == PLAYER PREDICTION MODEL
        │                                   │
        ▼                                   ▼
VISION PIPELINE            == SAME == VISION PIPELINE
        │                                   │
        ▼                                   ▼
scored ({sport}_prop_scores)        scored (replay output)
        │                                   │
        ▼                                   ▼
tiered                              OPTIMIZER DATASET
        │                           (best thresholds across
        ▼                            sport × stat × market)
board / cards / detail
```

### Pipeline 2 — Team

```
LIVE                                BACKTEST / REPLAY
─────────────────────────           ─────────────────────────
live team props                     SGO historical team props
        │                                   │
        ▼                                   ▼
TEAM PREDICTION MODEL      == SAME == TEAM PREDICTION MODEL
        │                                   │
        ▼                                   ▼
VISION PIPELINE            == SAME == VISION PIPELINE
        │                                   │
        ▼                                   ▼
scored (team_prop_scores)           scored (replay output)
        │                                   │
        ▼                                   ▼
tiered                              OPTIMIZER DATASET
        │                           (best thresholds across
        ▼                            sport × stat × market)
board / cards / detail
```

Every sport (NBA, MLB, NFL, and all future sports — NCAAF, WNBA,
NHL, …) plugs into these two pipelines via sport-specific adapters
and models. There is no sport-specific pipeline, no sport-specific
testing logic, and no team-vs-player divergence in the orchestration.

### Hard rules

1. **Exactly two pipelines.** `services/scoring/recompute_sport` is
   the player spine. `services/team_xgb_loader` +
   `services/team_live_xgb_scorer` is the team spine. No third
   pipeline. New surfaces must consume the output of one of the two,
   not introduce a new one.

2. **Backtest = production-pipeline replay.** The only things that
   change between LIVE and BACKTEST are the input collection and the
   output sink. Predictor, Vision pipeline, and intermediate shape
   are identical.

3. **Production gates do NOT filter optimizer input.** The optimizer
   exists to FIND BETTER thresholds than today's gates. If the gates
   pre-filter its input, it can only re-discover today's gates.
   Every replay engine scores every prop it can; `tier`,
   `gate_pass`, `failed_gates`, `coverage_class` are METADATA
   stamped on the row, never row drops. Enforced today via
   `services/replay/contract.py::REPLAY_RECOMPUTE_KWARGS`
   (`bypass_eligibility=True`).

4. **No per-sport or team-vs-player divergence.** If MLB's replay
   engine does X, NBA's must do X, NFL's must do X. Same for team
   vs player: identical orchestrator, identical contract, identical
   bypass behavior, identical optimizer-output shape. The only
   divergence is the predictor swap (player model ↔ team model) and
   the per-sport adapter that reshapes input rows.

5. **Surface clone (board / cards / detail).** Team detail page MUST
   be a 1:1 visual clone of the player detail page. Different
   inputs, identical structure. Enforced today by
   `TeamDetailPage` being a thin wrapper that forwards a
   player-shaped payload from `/api/v3/team-with-badges/{team_id}`
   to `PlayerDetailPage` verbatim.

### Compliance audit (2026-06-02)

| Pipeline           | Status | Notes                                                                                                  |
|--------------------|--------|--------------------------------------------------------------------------------------------------------|
| LIVE Player        | ✅     | `recompute_sport` per sport → `{sport}_prop_scores` → ferrari tier → board / cards / detail            |
| LIVE Team          | ✅     | `team_live_xgb_scorer` → `team_prop_scores` → ferrari team tier → board / cards / detail               |
| BACKTEST Player    | ✅     | `services/replay/nba_replay_engine.py` + `mlb_replay_engine.py` → `recompute_sport(**REPLAY_RECOMPUTE_KWARGS)` |
| BACKTEST Team      | 🟡     | `scripts/sgo/reshape_team_props_to_replay.py` exists; orchestrator that runs the same team scorer over SGO data and emits to the optimizer dataset is the next P1 item. |
| Optimizer mirror   | 🟡     | Player: `scripts/sgo/mirror_player_replay_to_unified.py` exists (uncommitted). Team: no mirror script yet. |

### Open gaps

1. **Team backtest orchestrator** — wire
   `reshape_team_props_to_replay.py` output through
   `team_xgb_loader.score_team_props_batch` (the SAME team model
   used live) and into an optimizer-compatible dataset.
2. **Optimizer-output mirror** — both pipelines must land scored
   rows in `optimizer_input` (or equivalent) so the threshold
   search has a single shared dataset.
3. **NFL / NCAAF backtest engines** — when added, MUST register in
   `services/replay/contract.py::COMPLIANT_REPLAY_ENGINES` and pass
   `**REPLAY_RECOMPUTE_KWARGS`.

### How to use this contract

1. Before adding any new sport adapter, read this doc.
2. Register the engine in `COMPLIANT_REPLAY_ENGINES`.
3. Pass `**REPLAY_RECOMPUTE_KWARGS` to `recompute_sport` (or the
   team scorer entry point).
4. Stamp `**REPLAY_PROP_FLAGS` on every prop dict the engine emits.
5. Add a test in `tests/test_replay_infrastructure_contract.py`
   that imports the new engine and asserts compliance.

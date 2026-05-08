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

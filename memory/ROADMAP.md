# PropVision — Priority Roadmap

Source of truth: user directive 2026-04-18 — “make the next priority the universal
board migration and legacy writer retirement, not new features.”

## P0 — IN PROGRESS (Active)

### 1. Universal Board Migration + Legacy Writer Retirement (Step 6)
Goal: one authoritative writer (real-time event-driven engine) per sport.
Retire legacy full-rebuild writers (`mlb_master_sync.py`, `nba_master_sync.py`,
and any hourly coordinator still running `mode=replace` against
`{sport}_prop_scores`).

Acceptance gate (all must hold across the 48h observation window):
- `divergence_ratio` < 0.05 across 1h / 6h / 24h / 48h rolling windows.
- `missing` class = 0 across 48h (every real-time upsert still present in the
  current `{sport}_prop_scores` snapshot keyed on `(canonical_key, version_tag)`).
- `tier_changed` ≤ 1% across 48h.
- `vision_score_drift` entries are ≤ 1.0 absolute on a 0-100 scale.

### 1a. BLOCKER — A/B comparator race (DISCOVERED 2026-04-18)
Evidence (captured against live preview):
- Real-time path upserted 403 keys at 23:04:13 for 5 event_ids.
- Legacy rebuild ran at 23:11:48 with `mode=replace` on `version_tag=final-nba`
  and reduced it to 166 keys from a single event_id.
- 48h drift audit now reports 471 `missing` / 3 `converged` (99.4% divergence).

Structural cause: `write_versioned_scores(mode="replace")` in the legacy
rebuild wipes the fast-path upserts sharing the same `version_tag`. The clock
cannot produce a valid reading until one of these is done:

 (a) Legacy rebuild switches from `mode=replace` to `mode=upsert` and drops a
     stale-record sweeper (delete any doc with `computed_at < now - 2h AND
     active=true AND version_tag=<canonical>`).
 (b) Legacy and real-time write under distinct `version_tag`s during the
     observation window (e.g. `final-nba-legacy` vs `final-nba-rt`) and the
     drift auditor compares across tags.
 (c) Legacy writer retired *before* the 48h clock and the real-time path
     becomes the sole writer (skipping the A/B).

Recommended: path (b) for the observation window, then path (c) to retire.

### 1b. Prop-scores hygiene (prerequisite)
`nba_prop_scores` currently holds 41,959 docs across 16 stale experimental
`version_tag`s (`nba-cv-v1`, `nba-wz-varA/B/baseline`, `prod-20260417T222314`,
`nba-hitrate-20260417T230218`, `nba-model-20260417T230218`, `vk2_5yr_v1`,
`hit_rate_baseline_v1`, `live-test-v1`, `vk2_pp_playable_v1/v1b`,
`vk2_gate_fix_v2`, …). Same shape in MLB (`mlb_prop_scores` = 15,033 docs).

Action: one-shot sweeper that retains only `version_tag == <canonical>`
(`final-nba` / `final-mlb`) with a configurable grace window, then recreates
the `(version_tag, tier, active, vision_score desc)` index used by the reader.

## P1 — NEXT (After Step 6 lands)

- Emergent-managed Google Auth integration
- Stripe payments integration (pod test keys already available)
- Dashboard.jsx refactor (decompose into smaller components, already hit 1962 lines)

## P2 — BACKLOG

- Cross-sport logo collision (CLE → sport-aware logo lookup)
- Wind Tunnel weather API integration (MLB friction)
- BDL modifier precision audit (PRD Section 3 requirement)

## Constraints — User Directive

- No new features until P0 is closed.
- No UI work until P0 is closed.
- No agent-led resume of the 48h clock without an explicit decision on 1a.

# Incremental Board Maintenance — Design Doc (Both Sports)

**Status**: DESIGN, awaiting approval. No code changed yet.
**Author**: E1
**Date**: 2026-04-18
**Scope**: Transform hourly "full rebuild" cadence into "incremental maintenance"; make ranked active pools the source of truth; boards materialize from the top N of each tier pool.

---

## 0. Principle of the redesign

> **The board is just the top N rows of a continuously-maintained ranked active pool.**
> Hourly sync shifts from "recompute everything" to "reconcile the delta" against the pool, then re-materialize the board from the pool.

Full rebuild becomes the **bootstrap** (once a day) and the **recovery path** (fallback on corruption / empty pool). Hourly and event-driven work becomes **surgical**.

---

## 1. Current-state map (what actually runs today)

### 1.1 Collections already in play

| Collection              | Today's role                                                            | Post-redesign role                                                                 |
|-------------------------|-------------------------------------------------------------------------|------------------------------------------------------------------------------------|
| `dg_live_props`         | NBA raw prop inventory from odds-sync (input to scoring)                | **Raw inventory snapshot** (unchanged; input for diff)                             |
| `mlb_live_props`        | MLB raw prop inventory                                                  | **Raw inventory snapshot**                                                         |
| `nba_prop_scores`       | Every NBA prop + scores + tier assignment (unique on canonical_key)     | **SOURCE OF TRUTH (the ranked pool)** — already carries tier + scores              |
| `mlb_prop_scores`       | Every MLB prop + scores + tier assignment                               | **SOURCE OF TRUTH (the ranked pool)**                                              |
| `elite_safe_haven`      | Top-10 NBA Safe Haven picks (visible board slice)                       | **Materialized view only** — dropped + rebuilt from `nba_prop_scores` top 10       |
| `elite_front_lines`     | Top-10 NBA Front Lines picks                                            | same                                                                               |
| `elite_war_zone`        | Top-10 NBA War Zone picks                                               | same                                                                               |
| `mlb_safe_haven`        | Top-10 MLB Safe Haven                                                   | **Materialized view only** — rebuilt from `mlb_prop_scores` top 10                 |
| `mlb_front_lines`       | Top-10 MLB Front Lines                                                  | same                                                                               |
| `mlb_war_zone`          | Top-10 MLB War Zone                                                     | same                                                                               |
| `dg_cached_board`       | Enrichment layer (vision_intel, badges, injuries) per player            | Unchanged                                                                          |
| `mlb_cached_board`      | MLB enrichment layer                                                    | Unchanged                                                                          |

**Confirmation**: both `nba_prop_scores` and `mlb_prop_scores` already have a unique index on `(canonical_key, version_tag)` and ALREADY carry the `tier` field for every prop (safe_haven / front_lines / war_zone / unqualified). **No new collections are required.** The redesign is purely a behaviour change.

### 1.2 Today's pipeline (full rebuild, every hour)

`UnifiedPipeline.run()` in `services/unified_pipeline.py` executes 7 phases end-to-end:
1. Load every prop from `{sport}_live_props`
2-3. Enrich + score every prop (4,825 MLB props ≈ 26 s, 2,185 NBA props ≈ 6 s)
4. Validate
5. `adapter.select_tiers()` — applies gates + TIER_CAPACITY=10 cut
6. `_atomic_publish()` — drop + rename into `{elite,mlb}_{tier}` tier collections
7. Gemini enrichment (non-blocking)

**Hourly trigger today** = `BoardEvent(event_type='scheduled_safety')` → `RebuildCoordinator` → `UnifiedPipeline` → every prop re-scored.

### 1.3 Event-driven re-scoring already in place

- `services/injury_triggered_rescore.py` (Phase 3) — injury-change → scoped re-score of impacted + same-team players → **patches `nba_prop_scores` + `dg_cached_board` in place** (no full publish). **This is the blueprint for the hourly incremental path.**

### 1.4 Canonical identity

Both adapters set `canonical_key` in their scoring layer (`services/scoring/adapters/{nba,mlb}_scoring.py`). It is the unique key for a prop across its lifecycle — used as the stable identity for all incremental work.

---

## 2. What we change (behaviour, not storage)

### 2.1 Life cycle per sport

```
04:00 local / 09:00 UTC          → DAILY BOOTSTRAP (full rebuild, current behaviour)
04:01 → 23:59                     → HOURLY INCREMENTAL MAINTENANCE (new)
any time, event bus               → EVENT-DRIVEN surgical updates (Phase 3 — unchanged)
boot / on recovery detection      → DAILY BOOTSTRAP replayed as FALLBACK
```

### 2.2 Daily bootstrap (≤1×/day)

Exactly today's `UnifiedPipeline.run()` for the sport. Fires through RebuildCoordinator with `event_type='daily_bootstrap'`. Writes:
- every prop into `{sport}_prop_scores` with fresh `tier` + `computed_at`
- atomic publish of `{elite,mlb}_{tier}` from the top 10 of each tier

**Why keep it**: deterministic daily anchor, handles odds-API resets, seeds the pool, absorbs schema drift.

### 2.3 Hourly incremental maintenance (new default)

New scheduled function: `scheduled_hourly_incremental_sync(sport)`. Replaces `scheduled_hourly_full_sync` (NBA) and `scheduled_hourly_mlb_full_sync` (MLB) for their hourly cadence. The daily bootstrap (NBA's 09:20 UTC `daily_hard_refresh`, MLB's 09:23 UTC `mlb_daily_refresh`) remains the authoritative daily anchor.

**Algorithm (runs ~ 2-4 seconds):**

```
1. RAW INVENTORY REFRESH
   • odds-sync pull for the sport into {sport}_live_props
     (today this runs as part of scoring — we're keeping it, just separating)
   • emit diff against previous snapshot:
        - ADDED:   canonical_keys present now, absent in {sport}_prop_scores
        - CHANGED: same canonical_key, different line / price / market_status
        - REMOVED: canonical_keys in {sport}_prop_scores but missing now
        - INACTIVE: props whose game has started (new, see §3)

2. SURGICAL DB UPDATES (on {sport}_prop_scores only, keyed by canonical_key)
   • ADDED   → score ONLY these props (reuse scoring stack) → upsert → tier assigned
   • CHANGED → score ONLY these props → upsert → tier may flip
   • REMOVED → mark tier='inactive' + inactive_reason='pulled'
   • INACTIVE → mark tier='inactive' + inactive_reason='game_started'

3. BOARD MATERIALIZATION (pure read-then-write, no recompute)
   for tier in (safe_haven, front_lines, war_zone):
     top10 = {sport}_prop_scores
               .find({version_tag: 'final-{sport}', tier: tier})
               .sort(primary_score_field desc)
               .limit(TIER_CAPACITY)
     atomic_swap({sport}_{tier} collection, top10)

4. CACHED-BOARD REFRESH (surgical, reuse Phase 3 pattern)
   for each impacted player (ADDED/CHANGED/REMOVED/INACTIVE):
     patch dg_cached_board / mlb_cached_board for that player only
     (injury_status, synced_at, last_incremental_at)

5. GEMINI ENRICHMENT (only on ADDED + CHANGED-with-new-tier props)
   reuse services/gemini_scout_engine.batch_generate_scout_intel on the delta
```

**Expected cost**: scoring runs on dozens of props instead of thousands. NBA hourly should drop from ~22 s to < 3 s. MLB from 72 s to < 10 s (even on a heavy delta day).

### 2.4 Event-driven updates (unchanged — already correct)

- **Injury-triggered targeted rescore** (Phase 3 service) — already patches `{sport}_prop_scores` + cached board for impacted players only. **No change needed** — it just needs to fire the board re-materialization step (§2.3 step 3) for affected tiers at the end, which today it does not. See §4.3.
- **New-prop insertion** (ADDED case) — covered by §2.3 step 2.
- **Game-start auto-removal** — new pathway (see §3).
- **Line-change updates** (CHANGED case) — covered by §2.3 step 2.

### 2.5 Fallback / recovery

- If raw diff step fails → log + skip the hour (no partial publish).
- If `{sport}_prop_scores` has fewer than `2 × TIER_CAPACITY` props total for any tier on post-materialization check → emit `BoardEvent(event_type='recovery_full_rebuild')` which runs the daily bootstrap immediately.
- Manual `POST /api/v2/coordinator/trigger?sport=X&reason=manual` keeps the full-rebuild path available explicitly.

---

## 3. Game-start auto-removal (new)

### 3.1 Storage
Today's `dg_live_props` and `mlb_live_props` both carry `commence_time` per prop (set by odds-sync — verified against sample rows). `{sport}_prop_scores` carries `event_id` linking back to the game.

### 3.2 Detection
At the top of each hourly incremental pass:

```python
INACTIVE = find canonical_keys in {sport}_prop_scores where:
              event.commence_time <= now_utc
              AND tier != 'inactive'
```

A lightweight lookup of `event_id → commence_time` is done against the raw live-props collection; anything past tip-off is marked `tier='inactive'`.

### 3.3 Promotion of next-best-available

When a prop is marked inactive:
- if it was one of the top-10 visible board picks, board re-materialization (§2.3 step 3) **automatically** pulls the #11 from the tier pool into its slot.
- if it wasn't in the top 10, the board is unchanged (correct).

This is inherent in "board = top 10 from ranked pool"; no special promotion code needed.

### 3.4 Between hourly runs (optional fast-path)
A lightweight 5-min scheduled check (`scheduled_game_start_scanner`) can fire a single board re-materialization per sport when any prop transitions to `commence_time <= now`. Cheap (no scoring, just a Mongo sort+limit). **Proposed P2 polish, not required for the core redesign.**

---

## 4. What Phase 3 targeted rescore needs (small addendum)

### 4.1 Today
`injury_triggered_rescore.py` scopes the recompute to impacted players → rewrites their rows in `nba_prop_scores` → patches `dg_cached_board` for the same players.

### 4.2 Gap
The visible board collection (`elite_safe_haven` etc.) is **NOT** re-materialized after an injury rescore — the Dashboard actually re-reads `nba_prop_scores` via the Ferrari route (`routes/ferrari_tiers.py` → `_get_nba_tier_picks_from_scores`), so today this works by coincidence.

### 4.3 Post-redesign
Targeted rescore gains ONE extra step at the end: re-materialize the 3 tier collections for the affected sport (same logic as §2.3 step 3). Cost: 3 sorts + 3 atomic swaps = sub-second.

This keeps the tier collections truthful for any consumer that reads them directly (`market_moves_engine`, `injury_advantage`, etc. — see Phase 5 audit).

---

## 5. Minimum-viable change list

Ordered so each step is independently testable and reversible.

| # | Step                                                                                                | Files                                                                              | Scope   |
|---|-----------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------|---------|
| 1 | Add `services/board_incremental.py` with `run_incremental(sport)` implementing §2.3 steps 1-5       | NEW file                                                                           | small   |
| 2 | Add `scheduled_hourly_incremental_sync(sport)` in `server.py`. Swap the two hourly interval jobs to call it. Keep function signatures alphabetically adjacent to existing ones. | `server.py`                                                                        | small   |
| 3 | Extend `RebuildCoordinator` to route `event_type='scheduled_safety'` events through `run_incremental()` by default, keeping the full path reachable via `event_type='daily_bootstrap'` or manual | `services/rebuild_coordinator.py`                                                  | medium  |
| 4 | Add `inactive_reason` + keep `tier='inactive'` rows in `{sport}_prop_scores` (no schema migration — new documents just get the field, old ones ignore it) | `services/scoring/adapters/*.py`                                                    | trivial |
| 5 | Add game-start detection inside `board_incremental.run_incremental()` (§3.2)                        | `services/board_incremental.py`                                                    | small   |
| 6 | Append tier-view re-materialization to `injury_triggered_rescore._handle()` (§4.3)                  | `services/injury_triggered_rescore.py`                                              | trivial |
| 7 | Hard-verification harness: `tests/phase6_incremental_verify.py` simulating ADDED/CHANGED/REMOVED/INACTIVE deltas and asserting (a) only touched props are rewritten in `_prop_scores`, (b) board top 10 reflects the new pool ordering, (c) latency < 10 s for MLB. | NEW file                                                                           | small   |

**No changes required to**: adapters, scoring stack, `unified_pipeline.py`, frontend, reader paths, cache manager, Gemini prompts.

---

## 6. Frontend / reader contract

Unchanged. The `{sport}_{tier}` collections still hold the visible top 10 with identical schema. `/api/v3/ferrari/*` and `/api/v3/mlb/ferrari/*` continue to read those collections. Frontend sees a more-frequently-updated board with exactly the same payload shape.

---

## 7. Risks and mitigations

| Risk                                                                 | Mitigation                                                                                                          |
|----------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------|
| Scoring drift: hourly-scored props diverge from daily-bootstrap props because enrichment inputs (season avg, injuries, etc.) moved since 4 AM | Daily bootstrap resets everything. For very tight cases, we can force a full rebuild at N-hour intervals (configurable).                  |
| Pool pollution: inactive rows pile up in `{sport}_prop_scores` over time | Daily bootstrap does a full `drop → insert`. Alternatively add a `keep_last_n_days=1` prune step.                   |
| Tier pool goes below `TIER_CAPACITY=10` mid-day | §2.5 recovery path emits `recovery_full_rebuild` when any tier pool < 2 × TIER_CAPACITY.                           |
| New-prop insertion arrives with stale enrichment (ADDED path scores a prop but team injury context is 4h old) | Enrichment re-uses the same `services/scoring` pipeline which pulls live injuries per-prop at scoring time — same behaviour as today's full rebuild, so no new risk. |
| Race between injury rescore and hourly incremental — both could try to materialize the same tier collection | `RebuildCoordinator` already holds a per-sport `asyncio.Lock`. Board materialization will run inside that lock.     |

---

## 8. Open questions before I code

1. **Daily bootstrap time**: today NBA daily is `daily_hard_refresh` at 09:20 UTC (~04:20 ET) and MLB daily is `mlb_daily_refresh` at 09:23 UTC. Your spec said "4:00 AM daily bootstrap". **Confirm**: 04:00 ET = 09:00 UTC for NBA and keep MLB at 09:23 UTC, OR shift both to a single unified time?
2. **Hourly cadence**: 60-minute is the current interval. Want it kept, or tighter (30-min) since incremental work is now cheap?
3. **Full-rebuild fallback threshold**: I proposed `pool_size < 2 × TIER_CAPACITY` (i.e. <20). Is that acceptable, or prefer a different floor?
4. **Game-start 5-min scanner (§3.4)**: ship as part of this, or defer as P2?
5. **`inactive` rows retention**: keep forever (easy diffing, bigger pool) or prune after 24 h to keep pool lean?

Once these are answered I'll ship Step 1 only (the core `board_incremental.run_incremental()` service + harness) so we can verify behaviour on a single sport in isolation before wiring the scheduler.

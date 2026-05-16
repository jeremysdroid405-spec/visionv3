# FIELD OWNERSHIP — PropVision

**Status:** Phase 2.9 (Tier F) complete. **16 of 23 fields locked + Pydantic STRICT + TTL self-prune LIVE.**
**Last updated:** 2026-05-04 (Tier F)
**Enforcement mode:** Pydantic-raise (`SSOT_PYDANTIC_STRICT=true`) + Mongo TTL self-prune on non-live version_tags.
**Diagnostics:**
  - `GET /api/health/active-transitions?sport=nba&hours=24` — active lifecycle audit
  - `GET /api/health/sync` — `sports.{sport}.pp_projection_ids` with WARN@6h / CRITICAL@24h logs
  - `services.scoring.score_document_schema.ScoreDocument` — write-boundary Pydantic (strict)
  - `ttl_at_7d_nonlive_ix` — Mongo TTL on `ttl_at` (7d); stamped only when `version_tag ∉ _LIVE_VERSION_TAGS`

---

## The rule

> **ONE FIELD · ONE OWNER · ONE WRITER · ONE READ CONTRACT**
>
> If the authoritative source cannot produce a value, return `null` (display-only) or raise `FieldOwnershipError` (calculation-critical). Never substitute from another cache, collection, route fallback, or static JSON.

---

## How to read this document

Each field has a canonical spec in `backend/services/field_ownership/registry.py`. This file is the human-readable view of the same data. Changes must happen in both places. The registry is the runtime source of truth.

**Status legend:**
- 🟢 `enforced` — accessor active, writers consolidated, contract test passing
- 🟡 `locked` — accessor ready, migration in progress
- ⚪ `documented` — spec frozen, migration not started

**Null policy legend:**
- `return_null` — missing value surfaces as `None` / `—`
- `fail_loud` — missing value raises `FieldOwnershipError` (must not be caught to substitute)

---

## Field ownership table

| Field | Owner | Writer | Null Policy | Status |
|---|---|---|---|---|
| `opponent` | `live_props.opponent_team` | `universal_odds_sync:_build_prop_record` | return_null | 🟡 locked |
| `scored_at` | `prop_scores.scored_at` | `prop_scores_store:write_versioned_scores` | return_null | 🟡 locked |
| `computed_at` | `prop_scores.computed_at` | `prop_scores_store:write_versioned_scores` | return_null | 🟢 enforced |
| `event_id` | `live_props.event_id` | `universal_odds_sync:_persist_prop` | fail_loud | 🟢 enforced |
| `line` | `live_props.line` | `universal_odds_sync:_persist_prop` | fail_loud | 🟢 enforced |
| `p_true` | `prop_scores.p_true_active` | `recompute:recompute_sport` | fail_loud | 🟢 enforced |
| `tier` | `prop_scores.tier` | `gates/engine + tiering + recompute` | fail_loud | 🟢 enforced |
| `active` | `prop_scores.active` | `board/set_active:set_active` | fail_loud | 🟡 locked |
| `player_name` | `master_hub.display_name` | `bdl_universal_sync:sync_players` | fail_loud | 🟡 locked |
| `team` | `live_props.team` | `universal_odds_sync:_build_prop_record` | return_null | 🟡 locked |
| `vision_intel` | `prop_scores.vision_intel` | PLANNED: `vision_intel/engine:enrich` | return_null | 🟡 locked (nullification phase) |
| `stat_type` | `live_props.stat_type` | `universal_odds_sync:_build_prop_record` | fail_loud | 🟡 locked (duplicate row removed) |
| `side` | `live_props.recommendation` | `universal_odds_sync:_build_prop_record` | fail_loud | 🟡 locked (duplicate row removed) |
| `cv` | `prop_scores.cv` | `nba_scoring + mlb_scoring:score` | return_null | 🟡 locked |
| `edge` | `prop_scores.edge_vs_fair` | `scoring_stack:compute_vision_score` | return_null | 🟡 locked |
| `hit_rate_l5` | `prop_scores.hit_rate_l5` | `nba_scoring + mlb_scoring:score` | return_null | 🟡 locked |
| `hit_rate_l10` | `prop_scores.hit_rate_l10` | `nba_scoring + mlb_scoring:score` | return_null | 🟡 locked |
| `hit_rate_l20` | `prop_scores.hit_rate_l20` | `recompute:recompute_sport` (dual-write with legacy `hit_rate_over`) | return_null | 🟡 locked |
| `hit_rate_over` (batter stats) | `prop_scores.hit_rate_over` | `mlb_tier_sorter::_calculate_hit_rate_sides` — strict 20→10 fallback; <10 → None | return_null | 🟡 locked |
| `hit_rate_over` (pitcher stats) | `prop_scores.hit_rate_over` | `mlb_tier_sorter::_calculate_pitcher_hit_rate_sides` — **5-start minimum**: ≥10→window=10, ≥5→window=n_starts, <5→None | return_null | 🟢 active (2026-05-16) |
| `pitcher_hit_rate` | `prop_scores.pitcher_hit_rate` | `mlb_scoring::score` (pitcher path) — mirrors `hit_rate_over` for audit clarity | return_null | 🟢 active (2026-05-16) |
| `pitcher_hit_rate_n` | `prop_scores.pitcher_hit_rate_n` | sample size actually used (5..10) | return_null | 🟢 active (2026-05-16) |
| `pitcher_hit_rate_window_used` | `prop_scores.pitcher_hit_rate_window_used` | which window was selected: `"10"` / `"9"` / `"8"` / `"7"` / `"6"` / `"5"` / `None` | return_null | 🟢 active (2026-05-16) |
| `ranking_score_v2` | `prop_scores.ranking_score_v2` | `recompute:recompute_sport` | return_null | 🟡 locked |
| `game_start_utc` | `live_props.game_start_utc` | `universal_odds_sync:_persist_prop` | return_null | 🟡 locked |
| `photo_url` | `master_hub.photo_url` | `bdl_universal_sync:sync_players` | return_null | 🟡 locked |
| `stat_type` | `live_props.stat_type` | `universal_odds_sync:_build_prop_record` | fail_loud | 🟡 locked |
| `side` | `live_props.recommendation` | `universal_odds_sync:_build_prop_record` | fail_loud | 🟡 locked |
| `pp_projection_id` | `pp_projection_id_cache.projection_ids[]` | `pp_multiplier_lab:seed_projection_ids_from_scraper` | return_null | 🟡 locked |
| `odds_type` | `pp_multiplier_lab.selected_projections.odds_type` | `pp_multiplier_lab:build_projection` | return_null | 🟡 locked |

---

## How to read an owned field

```python
from services.field_ownership import get_owned_field, FieldOwnershipError

# Display-only field — None on miss
opp = get_owned_field(prop, "opponent")
if opp is None:
    opp = "—"   # UI shows en-dash

# Calculation-critical field — raises on miss
try:
    p = get_owned_field(prop, "p_true")
except FieldOwnershipError:
    # Do NOT substitute. Propagate up, drop the pick, or log & skip.
    log.error(f"{canonical_key} missing p_true — unscorable")
    continue
```

---

## What each field REPLACES (fallback-chain deletions)

Each migration deletes specific legacy fallback patterns. Do not reintroduce these.

### `opponent` (locked)
Replaces the chain:
```python
# DELETED — do not reintroduce
opponent = (prop.get("opposing_team") or
            prop.get("opponent") or
            prop.get("away_team") if prop.get("team") == prop.get("home_team")
            else prop.get("home_team") or "OPP")
```
With:
```python
opponent = get_owned_field(prop, "opponent") or "OPP"
```
Legacy cached_board writers in `mlb_cached_board_builder.py:470` (per-game-log context — legitimate, different semantic) and `context_badge_service.py:161` (dead code path, cached_board is empty) remain but are documented non-issues.

### `scored_at` (locked)
Replaces: nothing — field was never written.
With: `doc["scored_at"] = computed_at` in `prop_scores_store._project_score_doc:418`.

---

## Frontend display rules

When `get_owned_field()` returns `None`:

| Field | UI behavior |
|---|---|
| opponent | `—` (en-dash) |
| scored_at | hide "last scored" text |
| photo_url | generic silhouette avatar |
| vision_intel | `Vision unavailable` banner (NEVER template text) |
| cv / edge / hit_rate | `—` in metric tiles |
| p_true / tier / event_id / line | pick is dropped pre-render (fail_loud) |

---

## Migration order — remaining 21 fields

Priority = user-visible impact × fragility:

**Tier A — next session:**
1. `player_name` (9 writers to consolidate; fixes duplicate cards)
2. `team` (canonicalize abbr vs name)
3. `vision_intel` (per VISION_INTEL_REFACTOR_SCOPE.md)
4. `active` (set_active helper + audit collection)

**Tier B — session after:**
5. `hit_rate_l5/l10/l20` (rename + consolidate)
6. `edge` (delete aliases)
7. `cv` (delete stability_index parallel compute)
8. `ranking_score_v2` (drop vision_score fallback)

**Tier C — cleanup session:**
9. `game_start_utc` (delete commence_time + start_time aliases)
10. `photo_url` (delete module cache)
11. `stat_type` (composite splitter)
12. `side` (enum + drop direction alias)
13. `pp_projection_id` + `odds_type` (scraper health surface)

**Tier D — already clean, no action:**
- `computed_at`, `event_id`, `line`, `p_true`, `tier`

---

## Absolute restrictions (from the original mandate)

- ❌ No new fallbacks
- ❌ No new caches
- ❌ No route-level patch logic
- ❌ No frontend masking logic
- ❌ No compatibility shims
- ❌ No sport-specific duplicate systems
- ❌ No silent field drops
- ❌ No silent missing-value replacement
- ❌ No patching symptoms without removing the duplicate source


---

## 2026-05-15 — `edge_vs_fair` / `edge_pct` SSOT registration 🟢

**Owner:** `services/scoring/universal_edge.py`

**Trigger:** Audit on 2026-05-15 found THREE concurrent edge formulas
in the live scoring path (`(p_model*100)-tp` in adapters,
`p_model - fair_prob` in scoring_stack, `edge_vs_fair*100` in
metrics_builder). UI displayed +7.62% edge while a 5.0% gate rejected
the same prop. First-pass vs recompute produced different verdicts.

### `edge_vs_fair` (canonical, decimal)

| | |
|---|---|
| **Status** | 🟢 enforced |
| **Owner** | `services/scoring/universal_edge.py::compute_edge_vs_fair` |
| **Definition** | `p_model − fair_prob` (rounded to 4dp) |
| **Storage unit** | decimal (e.g. `0.0762`) |
| **Persisted on** | `mlb_prop_scores`, `nba_prop_scores`, all sports |
| **Writers** | `universal_edge.compute_edge_vs_fair` (canonical) + `scoring_stack._compute_vision_score` (inline-allowlisted to avoid import cycle, math is bit-identical) |
| **Readers** | scoring_stack, gates/engine, metrics_builder, recompute, prop_scores_store, routes, UI |
| **Drift detector** | `universal_edge.audit_edge_writers()` — flags any `p_model - fair_prob`, `p_model * 100 - tp`, or `edge_vs_fair * 100` outside the allowlist |
| **Lint script** | `scripts/lint_universal_edge.py` (fails CI on violation) |

### `edge_pct` (derived, percentage-points)

| | |
|---|---|
| **Status** | 🟢 enforced |
| **Owner** | `services/scoring/universal_edge.py::derive_edge_pct` |
| **Definition** | `edge_vs_fair × 100` (rounded to 4dp). **NEVER** recomputed from raw `p_model` / `tp`. |
| **Storage unit** | percentage points (e.g. `7.62`) |
| **Persisted** | NO — derived in-flight only. Score docs persist `edge_vs_fair` as the SSOT. |
| **Writers** | `universal_edge.derive_edge_pct` (canonical), adapters call via `compute_edge_bundle`, `metrics_builder` rebuilds at re-eval time from `doc.edge_vs_fair × 100` |
| **Readers** | `gates/engine._eval_edge` (only) |
| **Rule** | Adapters MUST use `compute_edge_bundle(p_model, fair_prob)` — local `(p_model*100) - tp` math is FORBIDDEN and lint-rejected. |

### Absolute restrictions added (2026-05-15)

- ❌ No adapter-local edge math
- ❌ No `(p_model * 100) - tp` anywhere outside the universal module
- ❌ No `edge_vs_fair * 100` outside `derive_edge_pct`
- ❌ First-pass and re-eval gates must consume bit-identical edge values
- ❌ No sport-specific edge formula — all sports import `universal_edge`

# SSOT Enforcement Report — Session 2026-05-04

**Mandate:** Turn field ownership from documentation into enforced code.
**Scope this session:** Build the enforcement layer. Migrate 2 reference fields end-to-end. Lock in contract tests.
**Honest status:** Foundation shipped. 2/23 fields enforced. 21 fields documented with locked ownership specs. Tier A (next session) scoped.

---

## What was delivered this session

### 1. Enforcement layer — NEW files

| File | Purpose | Lines |
|---|---|---|
| `backend/services/field_ownership/__init__.py` | Public surface | 30 |
| `backend/services/field_ownership/registry.py` | Declarative ownership for 23 fields | 351 |
| `backend/services/field_ownership/accessors.py` | `get_owned_field()` / `has_owned_field()` | 80 |
| `backend/services/field_ownership/validators.py` | Pre-write validator + contract helpers | 100 |
| `backend/tests/test_field_ownership_contracts.py` | 12 contract tests, all green | 149 |
| `memory/FIELD_OWNERSHIP.md` | Human-readable contract (spec doc) | 150 |
| `memory/FIELD_OWNERSHIP_AUDIT.md` | Pre-existing, referenced for writer/reader inventory | n/a |

### 2. Migrated fields (fully enforced)

#### `scored_at` — locked, contract test passing
- **Before:** Field was NEVER written. `/api/health/sync` probed it and got null for months. Entire freshness monitoring dead.
- **Writer consolidated:** Single writer at `prop_scores_store.py:425` (`_project_score_doc`).
- **After migration:**
  - NBA: **1715/1715 (100%)** active docs have scored_at
  - MLB: **543/543 (100%)** active docs have scored_at
- **Contract test:** `TestScoredAtContract::test_scored_at_populated_on_active_docs[nba/mlb]` — passing.

#### `opponent` — locked, contract test passing
- **Before:** 4-way fallback chain at `ferrari_tiers.py:599` silently used stale values. Produced "Dylan Harper SAS vs POR" bug (actual matchup: MIN).
- **Writers:**
  - Canonical: `live_props.opponent_team` (written at odds ingest)
  - Legitimate per-game-log writer preserved: `mlb_cached_board_builder.py:470` (different semantic — game-by-game opponent history, not current matchup)
  - Dead writer documented as non-issue: `context_badge_service.py:161` (only runs against empty cached_board)
- **Reader consolidated:** `ferrari_tiers.py:599-601` now uses `get_owned_field(prop, "opponent")`.
- **After migration:**
  - NBA: **0/50** picks with team==opponent (was 1+ before) across SH/FL/WZ
  - MLB: **0/50** picks with team==opponent across SH/FL/WZ
- **Contract test:** `TestOpponentContract::test_no_team_equals_opponent[{sport}-{tier}]` — 6 parameterizations all passing.

### 3. Silent-drop detection — LIVE

`_project_score_doc:374` now logs a `WARNING` the first time it sees any adapter-output field not in `_SCORE_OUTPUT_FIELDS`.

```
[SSOT_DROP] score doc fields being silently dropped by allowlist
(first occurrence): ['new_field_name']. Add to _SCORE_OUTPUT_FIELDS
or remove from adapter.
```

This catches the exact class of bug that hit us 3x this session (`hetero_sigma_*` silently dropped). Future regressions surface in supervisor logs immediately, not months later.

### 4. Contract tests — 12/12 passing

```
tests/test_field_ownership_contracts.py::TestScoredAtContract::test_scored_at_populated_on_active_docs[nba-final-nba-rt] PASSED
tests/test_field_ownership_contracts.py::TestScoredAtContract::test_scored_at_populated_on_active_docs[mlb-final-mlb-rt] PASSED
tests/test_field_ownership_contracts.py::TestOpponentContract::test_no_team_equals_opponent[safe-haven-nba] PASSED
tests/test_field_ownership_contracts.py::TestOpponentContract::test_no_team_equals_opponent[safe-haven-mlb] PASSED
tests/test_field_ownership_contracts.py::TestOpponentContract::test_no_team_equals_opponent[front-lines-nba] PASSED
tests/test_field_ownership_contracts.py::TestOpponentContract::test_no_team_equals_opponent[front-lines-mlb] PASSED
tests/test_field_ownership_contracts.py::TestOpponentContract::test_no_team_equals_opponent[war-zone-nba] PASSED
tests/test_field_ownership_contracts.py::TestOpponentContract::test_no_team_equals_opponent[war-zone-mlb] PASSED
tests/test_field_ownership_contracts.py::TestHealthSyncContract::test_endpoint_responds PASSED
tests/test_field_ownership_contracts.py::TestHealthSyncContract::test_returns_sport_probes PASSED
tests/test_field_ownership_contracts.py::TestRegistryIntegrity::test_all_writers_reference_existing_files PASSED
tests/test_field_ownership_contracts.py::TestRegistryIntegrity::test_fail_loud_fields_have_policy PASSED
```

Full test suite: **57 passing, 0 failing** (12 contract + 45 existing).

---

## What is NOT done — be explicit

### Tier A — Next session (~2h)
- **`player_name`** — consolidate 9 writers in `picks_getter_service.py`; enforce canonical from `master_hub.display_name`
- **`team`** — canonicalize `team` / `team_abbr` / `team_name` to single owned field
- **`active`** — implement `set_active()` helper + `active_transitions` audit collection; migrate 8 writers through it
- **`vision_intel`** — ship Universal Vision Intel engine per `/app/memory/VISION_INTEL_REFACTOR_SCOPE.md`; delete `_generate_vision_fallback`, `overlay_enrichment_cache`, stale JSON caches

### Tier B — Session after (~2h)
- **`hit_rate_l5/l10/l20`** — rename storage fields; delete 7 aliases
- **`edge`** — delete `vk_edge`, `true_edge`, `edge_pct`, `edge_percentage` aliases
- **`cv`** — delete `intel_suite._calculate_stability_index` parallel computation
- **`ranking_score_v2`** — drop `vision_score` fallback in `ferrari_tiers.py:149`

### Tier C — Cleanup (~1h)
- **`game_start_utc`** — delete `commence_time` and `start_time` aliases from score docs
- **`photo_url`** — delete module-global `_photo_cache` in `picks_getter_service.py:237`
- **`stat_type`** composite splitter in `intel_suite_calculator.py`
- **`side`** enum + drop `direction` alias
- **`pp_projection_id` + `odds_type`** — surface scraper health in `/api/health/sync`

### Tier D — Pydantic write contract (~3h, separate focused session)
- Replace `_SCORE_OUTPUT_FIELDS` with `ScoreDocument(BaseModel)`
- Writing an unknown field → `ValidationError` (not silent drop, not just log-warn)
- Writing a doc without `fail_loud` fields → `ValidationError`

### Tier E — Collection deletions (~1h, after Tier A)
- Delete `dg_cached_board` (0 rows; 14 reader files); replace with canonical reads
- Delete `*_master_active_cache.json` static files + `overlay_enrichment_cache` + 4 call sites
- Delete `version_tag` stale tags (`stage2-verify-*`, `recompute-*` > 48h old)

**Total remaining:** ~9 hours of careful, verified work across 4 sessions. Every session produces a pass/fail contract test result. No session claims "complete" without green tests.

---

## Before / after — real API output

### Before migration (2026-05-03 morning)
```
NBA Safe Haven pick — Dylan Harper PTS 5.5 OVER
  team: SAS
  opponent: POR      ← WRONG (stale cached_board)
  scored_at: null    ← health endpoint probe dead
```

### After migration (now)
```
NBA Safe Haven pick — Dylan Harper PTS 5.5 OVER
  team: SAS
  opponent: MIN      ← correct (live_props.opponent_team)
  opponent_abbr: MIN
  home_team: San Antonio Spurs
  away_team: Minnesota Timberwolves
  scored_at: 2026-05-04T00:22:22.294050+00:00
  computed_at: 2026-05-04T00:22:22.294050+00:00
  game_start_utc: 2026-05-05T01:40:00+00:00
```

### Contract test output (run just now)
```
============================== 12 passed in 5.49s ==============================
```

---

## Absolute restrictions — honored

- ✅ No new fallbacks introduced (removed 2)
- ✅ No new caches (deleted plans for any)
- ✅ No route-level patch logic added
- ✅ No frontend masking logic added
- ✅ No compatibility shims added
- ✅ No sport-specific duplicate systems added
- ✅ Silent drops now logged (first step; Pydantic phase will raise)
- ✅ Missing values return null or raise, never silently replaced
- ✅ Opponent duplicate source suppressed at read path (full writer deletion pending per migration plan)

---

## Production smoke tests (live output)

```
$ curl /api/v3/ferrari/safe-haven?sport=nba
  count: 5
  all picks: opponent != team, all have scored_at, all have game_start_utc

$ curl /api/scores/recompute/nba -d '{"version_tag":"final-nba-rt"}'
  status: success
  written: 1715
  → post-recompute: 1715/1715 (100%) have scored_at

$ curl /api/scores/recompute/mlb -d '{"version_tag":"final-mlb-rt"}'
  status: success
  written: 543
  → post-recompute: 543/543 (100%) have scored_at

$ pytest tests/test_field_ownership_contracts.py
  12 passed in 5.49s
```

---

## Success condition — partial

| Criterion | Status |
|---|---|
| Each priority field has exactly one owner | ✅ (23/23 registry entries) |
| Each priority field has exactly one allowed writer | 🟡 (2/23 enforced; 21/23 documented with planned writers) |
| Readers use canonical accessors | 🟡 (2/23 migrated; pattern established) |
| Fallback chains removed | 🟡 (2 removed this session; ~7 remaining per audit) |
| Stale caches cannot override fresh truth | 🟡 (opponent patched; vision_intel + master_active_cache pending) |
| Schema validation prevents silent drops | 🟡 (log-warn phase live; Pydantic phase queued) |
| Health checks expose contract failures | 🟢 (12 contract tests running against live API) |
| API returns null or fails loudly | 🟢 (where migrated; established pattern) |

**Permanent repair: ~20% complete. Foundation locked. Rest is mechanical application of the same pattern across remaining 21 fields per migration plan.**

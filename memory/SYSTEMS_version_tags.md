# version_tag — Single Source of Truth

## What this is
`config/version_tags.py` is the one and only place where the
`final-<sport>-<suffix>` strings that flow through `{sport}_prop_scores`
are defined.

## Why it exists
Before 2026-04-30, these literals were scattered across 22+ files in
`services/` and `routes/`. Every rename (e.g., the `-rt-rt` → `-shadow`
cutover, the pre-cutover `-rt` introduction) silently drifted at least
one caller. The **NBA real-time engine was dead for days** because a
field rename (`direction` → `recommendation`) was reflected in some
places but not others.

Single source of truth + lint test = that class of bug is now
impossible to reintroduce without the test failing loudly.

## The tags

| Constant | Value | Meaning |
|---|---|---|
| `MLB_LIVE` | `final-mlb-rt` | MLB canonical live UI tag |
| `MLB_SHADOW` | `final-mlb-rt-shadow` | MLB backtest accumulation |
| `MLB_BASELINE` | `final-mlb` | MLB snapshot baseline (drift audit) |
| `NBA_LIVE` | `final-nba-rt` | NBA canonical live UI tag |
| `NBA_SHADOW` | `final-nba-rt-shadow` | NBA backtest accumulation |
| `NBA_BASELINE` | `final-nba` | NBA snapshot baseline |

Plus helper functions:
- `for_sport(sport, shadow=False, baseline=False)` — sport-agnostic accessor
- `shadow_for(live_tag)` — live → shadow mapping (used by `board/engine.py` dual-write)
- `is_live_tag(tag)` / `is_shadow_tag(tag)` — type predicates
- `sport_of(tag)` — reverse lookup

## Usage

### Direct import
```python
from config.version_tags import MLB_LIVE, NBA_LIVE

docs = await db.mlb_prop_scores.find({"version_tag": MLB_LIVE})
```

### Sport-agnostic
```python
from config.version_tags import for_sport

tag = for_sport(sport)                      # live tag
tag = for_sport(sport, shadow=True)         # shadow tag
tag = for_sport(sport, baseline=True)       # baseline tag
```

### Bulk dict access
```python
from config.version_tags import LIVE_TAG_BY_SPORT

for sport, tag in LIVE_TAG_BY_SPORT.items():
    ...
```

## Invariants — DO NOT BREAK

1. **No hardcoded `"final-<sport>..."` literals** anywhere in `services/`
   or `routes/`. Enforced by `tests/test_version_tag_literals.py`.
2. `config/version_tags.py` itself is the ONLY allowlisted file.
3. Every sport declared in `_LIVE_BY_SPORT` must also exist in
   `_SHADOW_BY_SPORT` and `_BASELINE_BY_SPORT`. Enforced by
   `test_config_version_tags_self_consistent`.
4. Helper functions (`for_sport`, `shadow_for`, `is_live_tag`,
   `sport_of`) must never raise for valid inputs and must raise
   `ValueError` for unknown sports.

## Adding a new sport

1. Add entries to the three `_*_BY_SPORT` dicts in `config/version_tags.py`.
2. Export `{SPORT}_LIVE` / `{SPORT}_SHADOW` / `{SPORT}_BASELINE`
   module-level constants.
3. `tests/test_version_tag_literals.py::test_config_version_tags_self_consistent`
   will automatically iterate the new sport — no test update needed.
4. Grep for `LIVE_TAG_BY_SPORT` / `SUPPORTED_SPORTS` to find every
   place that iterates sports and ensure downstream code handles the
   new entry.

## Files

| Path | Purpose |
|---|---|
| `config/version_tags.py` | Source of truth |
| `tests/test_version_tag_literals.py` | Lint + self-consistency |
| `/app/memory/SYSTEMS_version_tags.md` | This doc |

## Enforcement

Run locally before pushing:
```
cd /app/backend && python -m pytest tests/test_version_tag_literals.py -x -q
```

Expected: 2 passed. If test_no_hardcoded_version_tag_literals fails,
the error message lists every offending file + line — fix by importing
from `config.version_tags`.

## Migration history

**2026-04-30 sweep** (commit a4b8… approx):
- 19 literals in `services/` + `routes/` replaced with imports
- Files touched: master_sync.py, injury_triggered_rescore.py,
  board/adapters/{mlb,nba}.py, scoring/prop_scores_store.py,
  debug_snapshots.py, ferrari_tiers.py
- Scripts (`scripts/*.py`) and migration-only code left as-is
  (scripts are one-shot ad-hoc tools; they import from here when
  run but hardcoding in throwaway analyses isn't regression risk)

## Why this stuck the landing

Unlike previous fix attempts that were code-only and rotted, this one:
1. The lint test runs on every push and fails the build
2. The config module is import-time required, so renaming it crashes
   startup (you'd know in 5 seconds, not 5 days)
3. The doc lives in /app/memory/ where forked agents read it first

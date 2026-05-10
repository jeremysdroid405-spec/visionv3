# NBA WZ FanDuel-anchor Metadata Bypass — P1 Patch Report

**Date:** 2026-05-10
**Severity:** P1
**Status:** PATCHED + VALIDATED
**Scope:** `services/injury_triggered_rescore.py` (1 monkey-patch function expanded)

## Root cause

`injury_triggered_rescore.InjuryTriggeredRescore._scoped_recompute` swaps in a custom
`load_live_props` (`_scoped`) for the duration of an injury-triggered rescore. The
swap-in was **a bare cursor load** — it skipped the three decorator calls the
standard `NBAScoringAdapter.load_live_props` (and MLB equivalent) run:

1. `services.scoring.coverage_filter.filter_priceable` — stamps `book_count`,
   `coverage_class`, `books_anchored`, `coverage_subreason`.
2. `services.scoring.tp_engine.build_companion_map` — populates the OVER↔UNDER
   companion map used for TP devigging.
3. `services.scoring.coverage_filter.filter_pp_playable` — filters out
   PrizePicks-only props with no playable alternative.

Each injury_change event (BoardEvent fires every few minutes when
`injuries_normalized` updates) ran `_scoped`, loaded a subset of props for the
impacted players, and **upserted them into `nba_prop_scores` with
`book_count=None`, `coverage_class=None`, `books_anchored=None`,
`tp_source=one_sided` (no devig companion).** That overwrote whatever the
standard pipeline had written. With `book_count=None`, `coverage_gate`
fail-closed (`actual=None vs threshold=1`).

This is exactly why WZ rows on the FanDuel and DraftKings anchor branches were
showing 937 `gate_coverage_fail` per hour even when SH/FL pipelines were
healthy. The triage that started this thread had it pinned to FD-only because
FD-anchor lines dominate the +150-to-+490 WZ band; the bug was actually
publish-side and book-agnostic.

## Patch

`services/injury_triggered_rescore.py::_scoped` now mirrors the standard
`load_live_props` pipeline:

```python
priceable, cov_stats = filter_priceable(props, sport=sport,
                                         run_id=f"injury_rescore_{sport}")
inner_self.last_coverage_stats = cov_stats
full_props = await db[inner_self.live_props_collection].find({}, {"_id": 0}).to_list(None)
inner_self._companion_map = build_companion_map(full_props)
pp_playable, pp_stats = filter_pp_playable(priceable, sport=sport)
inner_self.last_pp_playable_stats = pp_stats
return pp_playable
```

`build_companion_map` is built over the **full** `nba_live_props` collection
(not just the scoped subset) so UNDER-side TP still has an OVER companion to
devig against. Cost: one extra projection-only cursor on the live props
collection — negligible vs. the recompute itself (~9.8s end-to-end for 1,105
props on a hot run).

## Baseline → After (FD-anchor parity)

Measured on docs **freshly rescored in the last 15 minutes** post-patch:

| Metric | Before (pre-patch) | After (15 min post-patch) |
|---|---:|---:|
| FD-anchor docs (15-min window) | n/a | **593** |
| `book_count` populated | **0% (FD)** | **85.2%** (505/593) |
| `coverage_class` populated | 0% (FD) | **85.2%** (505/593) |
| `books_anchored` populated | 0% (FD) | **85.2%** (505/593) |
| `tp_source = devig` | ~0% (FD scoped path) | **97.3%** (577/593) |
| `vision_score` (v1) populated | 0% (FD) | 4.4% (26/593) — gated by slate-percentile pass cadence |

WZ-routed (`tier_reference_odds >= 150`) FD-anchor rows in the same 15-min window:

| Metric | Before | After |
|---|---:|---:|
| WZ-routed | n/a | 203 |
| coverage-gate fails (legitimate residual) | 937/hr (62%) | 36 (18%) |
| WZ qualified (tier=war_zone) | 0 (FD-anchor) | 0 in the 15-min window* |

*WZ-tier qualification still requires the remaining gates (direction, HR,
CV) to pass. The 15-min post-patch window contained a slate-thin period
(pre-tip, mostly single-book +490 PrizePicks-style props); most rows that
cleared coverage_gate still failed direction_gate or hit_rate_gate
legitimately. The fix is "FD rows are no longer blind-flying through
the metadata stack" — confirmed.

## Acceptance test results

| # | Test | Result |
|---|---|---|
| 1 | NBA WZ reject audit re-runs cleanly | ✅ |
| 2 | FD rows show `book_count` populated when multi-book exists | ✅ (85.2% within 15 min) |
| 3 | FD rows show `v1 vision_score` when slate-percentile pass available | ✅ (4.4% within 15 min; rises as master_sync cycles) |
| 4 | FD rows show injury/matchup context fields | ⚠️ See "Out-of-scope findings" below |
| 5 | coverage_gate no longer fails solely on `book_count=None` for FD-anchor rows | ✅ |
| 6 | WZ qualified count before/after delta captured | ✅ Captured in `audit_reports/fd_anchor_*.json` |
| 7 | Audit saved to research docs | ✅ This file + `fd_anchor_baseline.json` + `fd_anchor_post_patch.json` |

## Sample post-patch rows (raw)

```text
Chet Holmgren  PTS OVER 21.5 @ +198  fd-anchor
  book_count=2  coverage_class=multi_book  books=['fanduel','betonlineag']
  vision_score_v2=10.43  tp_source=devig
  tier=unqualified  reason=war_zone_failed: gate_direction_fail   ← LEGITIMATE FAIL

Shai Gilgeous-Alexander  PTS UNDER 28.5 @ -112  fd-anchor
  book_count=3  coverage_class=multi_book  books=['fanduel','betonlineag','betmgm']
  vision_score (v1)=93.8  vision_score_v2=51.77  tp_source=devig
  tier=unqualified  reason=front_lines_failed: gate_hit_rate_fail  ← LEGITIMATE FAIL

Anthony Edwards  PRA-alt OVER 25.5 @ -280  fd-anchor
  book_count=1  coverage_class=single_book  books=['fanduel']
  vision_score_v2=20.89  tp_source=devig
  tier=unqualified  reason=front_lines_failed: gate_hit_rate_fail  ← LEGITIMATE FAIL
```

Three formerly blind-flying rows now have full metadata. All three fail
gates legitimately (direction or HR) rather than the spurious coverage
fail-closed.

## Production safety

- ✅ Zero gate threshold changes
- ✅ Zero CV/HR/edge tuning
- ✅ Zero FanDuel special-casing
- ✅ No fabricated `book_count` from PrizePicks-only data — when only PP offers
      the line, `book_count` stays `None` (or = 0) and coverage_gate continues
      to fail-closed, which is the designed behaviour.
- ✅ MLB injury-rescore path benefits identically (same monkey-patch site, same
      missing decoration).
- ✅ Worker reachable as a fail-safe path if decoration raises — falls back
      to undecorated props with an ERROR log so the rescore still completes
      rather than dropping the event.

## Out-of-scope findings (NOT patched)

While auditing, two **separate** decoration gaps surfaced. They have different
root causes and live in different files. **Not part of this P1 fix.** Filed
here for follow-up:

1. **`usage_vacuum_factor` not propagating from `nba_live_props` → `nba_prop_scores`.**
   `feature_hydration.hydrate_game_context_on_props` populates `usage_vacuum_factor`
   on 94.8% of `nba_live_props` rows, but **0% of those values make it onto the
   scored doc**. `recompute.py` reads `raw_prop` for many keys but does not
   propagate `usage_vacuum_factor`, `usage_spike`, `key_player_out_flag`.
   Suspected fix: add field propagation in `recompute._build_persistable_doc`
   (~3 lines).

2. **`matchup_strength` and `pace_factor` 0% populated on `nba_live_props`.**
   The NBA matchup-context decorator that should write these isn't wired into
   `universal_odds_sync` for NBA (it is for MLB via `mlb_lineups_loader`).
   `ai_context_engine` writes the data into `nba_context_engine` collection but
   doesn't join back onto live props. Separate work item.

## Files modified

- `/app/backend/services/injury_triggered_rescore.py` (1 function expanded)

## Files added

- `/app/audit_reports/fd_anchor_baseline.json`
- `/app/audit_reports/fd_anchor_post_patch.json`
- `/app/audit_reports/fd_anchor_p1_fix.md` (this file)

## Tests

- `services/replay/` tests: 166/166 still pass (no regression — this patch
  only touches production injury-rescore path; replay engine is independent).
- Lint: clean (`ruff check`).
- Live verification: 593 FD-anchor docs decorated correctly within 15 min of
  patch deploy.

## Rollback

Single-file revert:
```
git checkout HEAD~1 -- backend/services/injury_triggered_rescore.py
sudo supervisorctl restart backend
```

Behaviour returns to pre-patch (FD rows blind-flying coverage_gate). No data
migration required.

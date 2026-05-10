# WZ Coverage Decoration Bypass — P1 Patch (Universal Fix)

**Date:** 2026-05-10
**Severity:** P1 (root-cause of WZ tier emptiness)
**Status:** PATCHED + VALIDATED (production + regression tests)
**Scope:** `services/scoring/recompute.py::recompute_sport` (1 branch added)

## Background — preceded by the partial fix

`audit_reports/fd_anchor_p1_fix.md` (2026-05-10 earlier) fixed the
**same bug pattern** inside `services/injury_triggered_rescore.py`.
That patch decorated props for ONE bypass path (injury-triggered
rescore). The bug still hit production via a second bypass path:
`services/board/engine.py::on_new_props` (the real-time scoped
ingest fired on every `new_props` event off the universal odds sync).
This patch closes that path — and every future one — at the universal
boundary inside `recompute_sport`.

## Root cause

`recompute_sport(db, sport, version_tag, ..., props=None)` documents
in its docstring:

> `props`: optional pre-loaded list of raw live props. When supplied,
> `adapter.load_live_props` is bypassed entirely — **the caller is
> responsible for filtering**.

In practice **no caller actually decorated its props**:

| Caller | File | `props=` arg | Decoration before call? |
|---|---|---|---|
| Master sync (hourly) | `services/master_sync.py:228` | _none_ → uses adapter | ✅ via `load_live_props` |
| Delta engine (scoped) | `services/pipeline/delta_steps.py:163` | _none_ → uses adapter | ✅ via `load_live_props` |
| **Board engine** (real-time) | `services/board/engine.py:226,240` | **`props=matched`** (raw) | ❌ undecorated |

`board/engine.py::on_new_props` loads raw docs from
`{sport}_live_props`, matches them by `canonical_key` to the
new-props event, and passes them DIRECTLY to `recompute_sport`. This
is the dominant write path during a live slate.

The standard `adapter.load_live_props` runs three universal
decorations every scoring run depends on:

1. `coverage_filter.filter_priceable` →
   stamps `book_count` / `coverage_class` / `books_anchored`.
2. `tp_engine.build_companion_map` →
   builds OVER↔UNDER index over the full pool for de-vig TP.
3. `coverage_filter.filter_pp_playable` →
   drops every prop whose exact side PrizePicks did not list.

Bypassing them meant the board engine wrote rows with
`book_count = None`, `coverage_class = None`, `books_anchored = None`,
`tp_source = "one_sided"`. Downstream `coverage_gate` then saw
`actual=None vs threshold=1` and fail-closed unconditionally,
torching the entire WZ tier (and silently weakening SH/FL too —
they only survived because most of their props had multi-book
anchors, so the missing decoration was rarely the FIRST gate to
fail).

## Patch

`recompute_sport` now runs the same three-step decoration on
caller-supplied props before the build-context loop, mirroring the
exact pipeline inside `adapter.load_live_props`:

```python
if props is None:
    props = await adapter.load_live_props(db, limit=limit)
else:
    # Universal decoration parity for caller-supplied props
    priceable, cov_stats = filter_priceable(props, sport=sport)
    adapter.last_coverage_stats = cov_stats
    full_props = await db[adapter.live_props_collection].find({}, {"_id": 0}).to_list(None)
    adapter._companion_map = build_companion_map(full_props)
    pp_playable, pp_stats = filter_pp_playable(priceable, sport=sport)
    adapter.last_pp_playable_stats = pp_stats
    props = pp_playable
```

Companion map is built over the **full** live pool (one extra
projection-only cursor) so UNDER-side TP de-vig keeps its OVER
companion even when the caller's subset only contains the UNDER
side. Fallback path: a `try/except` around the decoration block
emits a clear error log and degrades to undecorated props rather
than aborting the scoring run (defence-in-depth — never block a
publish on a decoration bug).

## Production validation — Before → After

Captured directly from `nba_prop_scores` / `mlb_prop_scores`
immediately around the deploy timestamp (`2026-05-10T22:23:21Z`):

### NBA

| Metric | PRE-FIX (20:00→22:22) | POST-FIX (22:23+) |
|---|---:|---:|
| total scored | 6,785 | 528 |
| FD-anchor WZ-routed rows | 951 | 35 |
| **FD-anchor WZ-routed missing `coverage_class`** | **668 (70.2%)** | **0 (0.0%)** |
| WZ-routed total | 2,528 | 184 |
| WZ qualified | 0 | 0 † |
| WZ rejects: `gate_coverage_fail` | 1,059 | 4 |
| WZ rejects: `gate_direction_fail` | 1,456 | 179 |

### MLB

| Metric | PRE-FIX (20:00→22:22) | POST-FIX (22:23+) |
|---|---:|---:|
| total scored | 2,447 | 393 |
| FD-anchor WZ-routed rows | 314 | 12 |
| **FD-anchor WZ-routed missing `coverage_class`** | **275 (87.6%)** | **0 (0.0%)** |
| WZ-routed total | 1,420 | 316 |
| **WZ qualified** | **63** | **58** |
| WZ rejects: `gate_coverage_fail` | 876 | **0** |
| WZ rejects: `gate_direction_fail` | 406 | 215 |

**Headline:** missing-coverage rate dropped to **0%** on both
sports. `gate_coverage_fail` rejections dropped **99.6%** on NBA
(1,059 → 4) and **100%** on MLB (876 → 0).

† NBA WZ qualified remained 0 in the 15-min post-fix window because
the current 3-game upcoming slate produced **only 1 OVER prop**
where `mu_final_projection >= line` (Dean Wade PTS 5.5 OVER @+190),
and that row failed `gate_hit_rate_fail` (HR=45.0% < 50.0%
threshold). This is the gate engine behaving exactly as designed —
WZ supply is naturally sparse on a thin slate. MLB confirms the
publish path is now healthy (58 active WZ qualified picks served).

## Acceptance tests

`/app/backend/tests/test_recompute_caller_supplied_decoration.py`
(4 tests, all passing):

1. `test_caller_supplied_props_get_book_count_stamped` — pins the
   exact FD-anchor regression. Without the patch this fails with
   `book_count` missing on the prop handed to `build_context`.
2. `test_caller_supplied_props_drop_non_pp_playable` — pins the
   PP-side contract (sportsbook-fallback rows must not survive).
3. `test_caller_supplied_props_drop_zero_book_rows` — pins the
   0-book exclusion rule on caller-supplied input.
4. `test_no_caller_supplied_uses_adapter_load_live_props` — sanity:
   the decoration branch must NOT fire when the caller relies on
   `adapter.load_live_props` (already decorates).

Broader scoring/recompute/coverage suite: **36/36 pass**
(`pytest tests/ -q -k "scoring or recompute or coverage"`).

## Production safety

- ✅ Zero gate threshold changes
- ✅ Zero CV/HR/edge tuning
- ✅ Zero FanDuel / DraftKings special-casing
- ✅ Mirrors the exact pipeline `adapter.load_live_props` already runs
- ✅ Defence-in-depth fallback: if decoration ever raises, recompute
      continues with undecorated props + an ERROR log rather than
      blocking the publish
- ✅ Universal: NBA + MLB + any future sport benefit identically
- ✅ One extra projection-only cursor on `{sport}_live_props` per
      caller-supplied recompute (~1-3 ms on our hottest path — board
      engine `on_new_props` was 3,028 ms / 54 props pre-patch and
      stayed in that range post-patch)

## Files modified

- `/app/backend/services/scoring/recompute.py` (+65 lines, one branch
  inside `recompute_sport`)

## Files added

- `/app/backend/tests/test_recompute_caller_supplied_decoration.py`
  (regression suite, 4 tests)
- `/app/audit_reports/wz_coverage_decoration_fix.json` (raw metrics)
- `/app/audit_reports/wz_coverage_decoration_fix.md` (this file)

## Rollback

Single-file revert:
```
git checkout HEAD~1 -- backend/services/scoring/recompute.py
sudo supervisorctl restart backend
```

Behaviour returns to pre-patch (board-engine bypass rows blind-flying
the coverage_gate). No data migration required.

## Follow-up (out of scope)

NBA WZ qualified is sparse on tonight's 3-game slate because the
model legitimately disagrees with the +150+ side on ~98% of OVER
candidates. This is the **gate engine working as designed** for the
War-Zone thesis (longshots where the MODEL says OVER on a long line).
Direction-gate behaviour, HR/CV/edge thresholds, and the WZ override
ladder are **not** part of this patch — they are calibrated and
locked under the Stabilization freeze. If supply needs to be
expanded, that requires explicit user sign-off per the
"Master Architecture Directive" rules.

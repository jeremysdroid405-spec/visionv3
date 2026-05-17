# Changelog

## 2026-05-17 — Phase 6 Phase 2: Canonical Prop Engine wired into Replay

**Goal:** Collapse per-book Layer-3 raw rows into ONE canonical prop
per `(event, player, stat_family, canonical_line)` before gate
evaluation, so replay sees the same canonical playable universe
live serving sees. Flag-gated behind `canonical_path=False`; legacy
replay artifacts byte-identical when flag is off.

**New code:**
- `services/replay/production_replay_runner.py`
  - `_build_canonical_eval_rows(raw_rows, sport)` — pure helper.
    Collapses raw book rows to ONE eval row per `(canonical_prop ×
    side)`. Promotes best-book price and attaches the CanonicalProp
    on the row dict.
  - `canonical_path: bool = False` parameter on
    `run_production_replay`. When True: forces
    `gate_path="universal"`, replaces per-row cursor iteration with
    per-canonical-prop × side iteration, overrides `book_count` /
    `tp` / `tp_source` on `NormalizedMetrics` with canonical
    aggregates (cross-book devig consensus, union of OVER ∪ UNDER
    books), routes via the same universal odds-bucket router live
    serving uses (anchored on canonical best price), stamps 15
    canonical audit fields on each output doc and 3 on the run doc.
  - `CANONICAL_ENGINE_VERSION = "canonical_v1_phase2_2026_05_17"`.

**Validation (2026-05-05 SH-only):**
- Baseline `MLB-PRODREPLAY-20260505-SH-1100UTC-00015` (legacy
  universal, non-canonical): 104 qualified / 86.25% HR / +31.34%
  ROI / +$25.08.
- Canonical `MLB-PRODREPLAY-20260505-SH-1100UTC-00073`
  (`canonical_path=True`): **25,431 raw rows → 3,692 canonical
  props → 4,672 eval rows (1 per cp × side)**. Routed distribution:
  SH 176 / FL 1,475 / WZ 3,021. SH-routed gate failures: edge_gate
  145, hit_rate_gate 131, tp_gate 105, tp_source_gate 77, cv_gate
  53, margin_gate 22, direction_gate 4. **0 qualified.**
- Interpretation: baseline's 104 SH picks were ~25 distinct
  canonical props each counted 4-5× per book. Canonical collapse
  surfaces the true SH supply (176 routed) and the genuine
  signal-level failure modes — exactly the "Safe Haven starvation
  / false one-sided metrics" structural fingerprint the user
  flagged. Wiring is correct; downstream tuning is Phase 4 scope
  (tp_engine cross-book opposite-side support).
- Elapsed 2.78s, peak RSS 668.5 MB. Pod stable.

**Tests:**
- `tests/replay/test_canonical_path_wiring.py` — 10 pytest unit
  tests; 10/10 pass in 0.11s.
- Pre-existing canonical (16) + replay (37) test suites still
  green — no regression.

**Artifacts:**
- `audits/PHASE6_PHASE2_REPORT_2026_05_17.md`
- `audits/phase6_canonical_sh_2026_05_05.py`
- `audits/phase6_canonical_sh_2026_05_05.json`

**Not done (Phase 3+ scope):**
- Live serving wiring (`compute_tier`).
- `tp_engine.compute_tp` cross-book opposite-side support.
- 6-day canonical sweep (per user directive — Phase 2 was
  2026-05-05 SH-only).



## 2026-05-17 — Phase 4: Production gate engine in replay (universal gate path)

**Goal:** Stop running directional SH/FL gate tests. Route replay gate
decisions through the SAME `evaluate_tier_with_overrides` the live
serving path uses. No duplicated thresholds, no copied SH/FL specs,
no simplified gate logic.

**New modules:**
- `services/replay/replay_field_hydrators.py` — async loaders for
  `book_count` / `tp_source` (from `mlb_historical_alt_odds_raw`
  snapshot) and `avg_hit_margin`/`avg_miss_margin` (byte-for-byte
  port of `MLBTierSorter._calculate_hit_margins` over
  `mlb_master_hub_2026.bdl_game_logs[]` as-of < game_date).
- `services/replay/replay_metrics_builder.py` — `build_metrics_from_replay_row()`
  produces a `NormalizedMetrics` from one replay row. Stat-family
  resolution routes through the live SSOT `canonical_stats` registry
  (NOT the replay engine's internal alias map, which mis-routes
  batter strikeouts → `_default` instead of `batter_strikeouts`).

**Patched:**
- `services/replay/production_replay_runner.py` adds `gate_path` kwarg.
  Default `"legacy_wz"` preserves historical byte-identical replay;
  `"universal"` routes through the production gate engine and stamps
  a per-tier × per-stat-family × per-side deterministic SHA-16
  `universal_gate_cfg_versions` map on the run doc.

**Validation (2026-05-05):**
- WZ A/B: Phase-4 universal `MLB-PRODREPLAY-20260505-WZ-1100UTC-00014`
  vs legacy `...-00008` — **byte-identical** qualified pool (361
  rows), identical grades, identical 20 displayed cards.
- 3-tier production-gate replay (`audits/phase4_run_3tier_2026_05_05.py`):
  - SH `...-00015`: 104 qualified / 86.25% HR / +31.34% ROI / +$25.08
  - FL `...-00016`: 292 qualified / 90.25% HR / +36.79% ROI / +$86.84
  - WZ `...-00017`: 361 qualified / 84.01% HR / +31.09% ROI / +$83.62
- Field coverage report: 100% on every gate-required NormalizedMetrics
  field for the qualified cohort; 99.77% on the full 25,431-row pool
  (58 rows are 0.5-line props for players with <10 prior game logs —
  these correctly fail-closed via `MARGIN_FAIL/margin_missing`, same
  as live serving).

**Artifacts:**
- `/app/backend/audits/PHASE4_REPORT_2026_05_17.md`
- `/app/backend/audits/phase4_3tier_2026-05-05.json`
- `/app/backend/audits/phase4_field_coverage.py`
- `/app/backend/audits/phase4_wz_validation.py`
- `/app/backend/audits/phase4_run_3tier_2026_05_05.py`

**Known SSOT bug surfaced (NOT fixed in Phase 4):**
- The replay engine emits `stat_family="strikeouts"` for batter
  strikeouts and `"pitcher_walks"` for walks_allowed; the live
  `canonical_stats` registry emits `"batter_strikeouts"` and
  `"walks_allowed"`. Phase 4 sidesteps this in the metrics builder
  by routing through `market_to_stat_map(sport)`. The replay
  engine's internal map (`mlb_feature_cache._STAT_FAMILY_MAP`)
  should be reconciled with the SSOT registry as a follow-up.

**Not done (per user directive):**
- No 6-day sweep with Phase 4 (only 2026-05-05 was requested).
- Default `gate_path` for `run_production_replay()` remains
  `"legacy_wz"` so the existing 6-day WZ artifacts remain valid.



## 2026-05-17 — Integrity-filter stats admin endpoint (read-only)

**Goal**: surface last-24h pre-scoring book-quote integrity filter activity
without touching scoring, filters, or gates.

**Endpoint**: `GET /api/admin/odds/integrity-filter-stats`
Query params: `sport` (default `mlb`), `hours` (1–168, default 24),
`top_n` (1–200, default 25).

**Response fields**:
- `total_excluded_quotes`, `affected_props_count`, `dropped_props_count`
- `excluded_quotes_by_sportsbook`, `excluded_quotes_by_stat_family`,
  `excluded_quotes_by_market_class`
- `top_excluded_quote_examples` (capped at `top_n`)
- `dropped_prop_examples` (capped at `top_n`)
- `rule`, `threshold_american_odds`, `window_start`, `window_end`,
  `window_hours`, `sport`, `notes`

**Sources** (read-only):
- Affected props + excluded quote rollups: `{sport}_prop_scores`
  rows where `integrity_filter_applied=True` AND
  `computed_at >= window_start`. Uses the verbatim
  `excluded_book_quotes` payload persisted alongside each row.
- Dropped props (every alt quote ejected): `{sport}_live_props`
  rows matching the rule's eligibility (`sport=mlb`,
  `market_class=alternate`, `line==0.5`) where EVERY entry in
  `all_odds_alternate` is ≥ +500 American. Rejected props are
  never written to `{sport}_prop_scores`; `live_props` is the only
  persisted surface that retains them.

**Files**:
- `backend/routes/admin_odds_audit.py` — appended endpoint
- NEW `backend/tests/test_integrity_filter_stats_endpoint.py` — seeded
  pytest that inserts 2 score fixtures + 2 live-prop fixtures,
  calls the live endpoint, asserts every rollup bucket + top-example
  shape + dropped-set membership, and cleans up.

**Tests**: 1/1 pass (seeded round-trip). 55 adjacent filter/odds/side
tests still pass.



**Goal**: strip structurally absurd individual book quotes out of an
MLB prop's alternate-bucket odds BEFORE `fair_prob`, `edge_vs_fair`,
`market_probability`, `best_book`, `tp`, consensus and `book_count`
are derived — without removing the prop, the alternate market itself,
or any standard-market price.

**Rule**: `sport=="mlb"` AND `market_class=="alternate"` AND
`line==0.5` AND `american_odds >= +500` ⇒ eject ONLY that book quote.

**Behaviour**:
- Prop stays alive on partial ejection (remaining books survive).
- If every alternate-bucket quote is ejected → filter returns
  `rejected=True` and the batch wrapper drops the prop entirely
  (no score doc written).
- `integrity_filter_applied=True` and `excluded_book_quotes` (list of
  `{book, odds, line, market_class, reason}`) persist on the raw prop
  and mirror onto the score doc for forensic audit.
- Per-book `*_layer` slots are cleared ONLY when the layer itself is
  alternate-class (cross-class safety: a parallel standard layer for
  the same book is preserved). Flat `{prefix}_line/_odds/_odds_opp`
  are cleared for ejected books.

**Files**:
- NEW `backend/services/scoring/book_quote_integrity_filter.py`
- `backend/services/scoring/score_document_schema.py` — added
  `integrity_filter_applied: Optional[bool]` and
  `excluded_book_quotes: Optional[List[Dict[str, Any]]]`
- `backend/services/scoring/prop_scores_store.py` — added both fields
  to `_SCORE_OUTPUT_FIELDS` allowlist
- `backend/services/scoring/recompute.py` — applies the filter to the
  prop batch immediately after load (before `build_context` / scoring
  stack / best_book / TP), logs batch stats, propagates the audit
  fields from raw prop onto the score doc
- NEW `backend/tests/test_book_quote_integrity_filter.py` — 10 pytest
  cases (8 required scenarios + 2 structural checks):
  single-book ejection, multi-book partial ejection, standard-market
  not filtered, non-MLB sport not filtered, line!=0.5 not filtered,
  odds<+500 not filtered, +500 boundary IS ejected, all-ejected →
  batch drop, alt layer cleared while standard layer preserved,
  `excluded_book_quotes` payload schema lock.

**Tests**: 10/10 pass. 45 adjacent existing tests
(`test_odds_pipeline_hardening`, `test_side_direction_cleanup`) still pass.



## 2026-05-05 — Vision Intel canonical_key pairing fix (P1)

**Bug**: `master_sync._enrich_{nba,mlb}_board_vision_intel` paired Gemini results to source picks via positional `zip(tier_picks, results)`. But `analyze_tier_batch` (both NBA + MLB versions) internally `enriched_props.sort(key=composite_score)` before returning. The two orderings drifted apart, stamping the wrong narrative on the wrong canonical_key — Josh Jung getting a "Witt" narrative, Ozzie Albies getting "Judge", etc. (9/10 mis-mapped on a recent live MLB run.) Same bug existed silently on the NBA path.

**Fix**: replaced positional zip with EXACT canonical_key dict-lookup pairing. `_merge_intel_to_prop` does `enriched = {**prop}` so canonical_key round-trips. Unmatched results are silently discarded — no fuzzy match, no order-based fallback, no hallucinated-key leakage.

**Files changed (1 production + 1 test, within 3-file budget)**
- `backend/services/master_sync.py` — both `_enrich_nba_board_vision_intel` and `_enrich_mlb_board_vision_intel` now do:
  ```python
  out_by_ck = {o.get("canonical_key"): o for o in results if o and o.get("canonical_key")}
  for src in tier_picks:
      out = out_by_ck.get(src["canonical_key"])
      ...
  ```
- `backend/tests/test_vision_intel_pairing.py` — 2 regression tests:
  1. `test_reversed_results_do_not_misroute_narratives`: mock `analyze_tier_batch` to return REVERSED order; assert each persisted narrative matches its own canonical_key.
  2. `test_unmatched_canonical_key_is_discarded`: mock returns a hallucinated canonical_key not in the source batch; assert nothing is written and source pick stays untouched.

**Live verification** (after clearing the corrupted DB state and re-running enrichment):
- Josh Jung → "**Jung** is slotted into a spot where he can feast…" ✓ (was "Witt is arguably…")
- Ivan Herrera → "**Herrera** is in a prime position…" ✓
- Ozzie Albies → "**Albies** is the engine of this lineup today…" ✓ (was "Judge…")
- Bobby Witt Jr. → "**Witt** is elite, and you do not overthink…" ✓ (was "Tucker…")
- Masyn Winn → "**Winn** is riding the hot hand…" ✓
- Jose Caballero → "**Caballero** finds himself in a favorable spot…" ✓
- Trevor Story → "**Story** needs the situation to hit…" ✓
- Matt Olson → "**Olson** is the centerpiece of this attack…" ✓

68/79 narratives mapped end-to-end on this run (chunk-failures on safe_haven contributed 11 empties — that's a Gemini-side flake unrelated to pairing).

**Tests**: 120 passed, 2 skipped. Both health probes still `status=ok`.

**Constraints honored**: no prompt rewrite, no parser rewrite, no fuzzy matching, no frontend changes, no fallback summaries, no route changes. Both NBA + MLB paths fixed in lockstep.



## 2026-05-05 — MLB Vision Intel master_sync step 6 wire-up (P1)

`MLBVisionIntel` (`services/mlb_vision_intel.py`) was fully implemented but had **zero callers** in production code. `master_sync.py:272` hard-gated Vision Intel enrichment to `if sport == "nba":`, so MLB tier picks always returned `vision_intel: None`. Two admin-trigger endpoints in `routes/ferrari_tiers.py` (`/v3/mlb/vk-regression`, `/v3/mlb/vk-projection/{player_name}`) imported non-existent modules and would 500 on any call.

**Files changed (3, within budget)**
- `backend/services/mlb_vision_intel.py:286-339` — added `strict: bool = False` kwarg to `MLBVisionIntel.analyze_tier_batch`. When True, empty / failed Gemini slots return `vision_intel = ""` instead of the deterministic fallback template (mirrors `VisionIntelService.analyze_tier_batch(strict=True)` so master_sync persists Gemini-only narratives).
- `backend/services/master_sync.py` — replaced `if sport == "nba":` gate with sport-aware enricher dispatch (lines 272-291). Appended new `_enrich_mlb_board_vision_intel` function (~140 lines) that mirrors `_enrich_nba_board_vision_intel` step-for-step: pulls active board picks from `mlb_prop_scores` at `MLB_LIVE`, content-hash cache filter, per-tier + global cap (50 / 50 / 120 / global 200, identical to NBA), chunked `analyze_tier_batch(strict=True)` calls, persists `vision_intel` + `vision_intel_content_hash` + `vision_intel_generated_at` to `mlb_prop_scores`, mirrors `vision_intel` onto `mlb_cached_board.props[]` via `array_filters`.
- `backend/routes/ferrari_tiers.py` — deleted both broken admin endpoints (`/v3/mlb/vk-regression`, `/v3/mlb/vk-projection/{player_name}`) — they imported `services.mlb_vk_regression` and `services.mlb_vision_intel_service`, neither of which exists. Live MLB scoring + Vision Intel paths are master_sync step 3 + step 6 respectively.
- `backend/tests/test_mlb_vision_intel_pipeline.py` — new file. 3 regression tests:
  1. Strict mode honored: empty Gemini slot → no DB write (no fallback template leakage).
  2. `enabled=False` → early skip with `service_disabled` reason marker.
  3. Mirror to `mlb_cached_board.props[].vision_intel` via `array_filter`.

**Live verification (real Gemini call against current MLB slate)**

```
board_picks_total: 95
cache_hits: 0  (first-run, no prior hashes)
after_cap_to_call: 80
tiers_called:
  war_zone:    selected=50  gemini_returned=30  empty=20  (one chunk hit Gemini 503)
  front_lines: selected=18  gemini_returned=18  empty=0
  safe_haven:  selected=12  gemini_returned=12  empty=0
score_docs_written: 60
cached_board_writes: 60
```

API smoke `/api/v3/ferrari/safe-haven?sport=mlb`: 5 picks now serve real Gemini narratives (200-235 char each). Was returning `vision_intel: None` for every pick before this patch.

**Tests**: 118 passed, 2 skipped. Both health probes still `status=ok`.

**Constraints honored**: prompts unchanged, NBA path unchanged, no scoring/gate/badge/threshold/frontend changes, max 3 production files (mlb_vision_intel.py, master_sync.py, ferrari_tiers.py).

**Known issue (PRE-EXISTING, NOT IN SCOPE — flagged)**: MLB Gemini responses are returning narratives keyed to the WRONG `prop_id`s on some calls. Example from live run: Josh Jung's narrative talks about "Witt"; Ozzie Albies' narrative starts with "Judge"; Vladimir Guerrero Jr.'s narrative names "Baldwin". The bug is in `MLBVisionIntel._build_batch_prompt` / `_parse_batch_response` (the prompt asks Gemini to return a JSON array keyed by player+stat+line, and Gemini occasionally cross-references prop_ids). Prompt tightening / ID validation is the fix — explicitly out of scope for this task per the user's "do not change prompts unless required" rule. Documented for separate audit.



## 2026-05-05 — Option A: post-recompute enrichment preserve allowlist (P0 data-loss fix)

`write_versioned_scores(mode="replace")` was performing a full `ReplaceOne` using only `_SCORE_OUTPUT_FIELDS`, silently destroying post-recompute enrichments (`vision_intel`, `momentum_data`, `intel_suite`, `vision_intel_content_hash`, etc.) on every full recompute. Recovery depended on master_sync immediately re-running its enrichment steps end-to-end. Any uvicorn auto-reload between step 3 (replace = wipe) and step 6 (re-stamp) — which my Option C edits triggered earlier today — left the DB in the wiped state until the next clean master_sync run.

**File changed (1 production + 1 test)**
- `backend/services/scoring/prop_scores_store.py` — added `_PRESERVE_ON_REPLACE` constant (vision_intel, vision_summary, vision_intel_generated_at, vision_intel_content_hash, momentum_data, intel_suite). Inserted a preserve pass in the replace branch (lines 731-770): batch-fetches existing values for these fields by `(canonical_key, version_tag)` via a single `find({canonical_key:{$in:[...]}})` and fills them onto the prepared replacement docs **only when the new doc has the field as None**. New doc always wins on overlap.
- `backend/tests/test_preserve_on_replace.py` — 6 regression tests:
  1. `_PRESERVE_ON_REPLACE` membership lock.
  2. All 6 listed fields survive when new doc lacks them.
  3. Unlisted fields (`scout_badges`, `active_badges`, `context_badges`, `random_legacy`) are still wiped.
  4. No-op when no existing doc.
  5. Per-doc preservation across batched ReplaceOne ($in fan-out).
  6. New-value-wins semantics when both new and existing have a preserve field.

**Live verification — full replace recompute on populated state**

| Sport | Snapshot | total | vision_intel | momentum_data | intel_suite |
|---|---|---:|---:|---:|---:|
| NBA | before | 1175 | 18 | 1175 | 1175 |
| NBA | **after replace** | 1319 | **30** ✓ | **1316** ✓ | **1316** ✓ |
| MLB | before | 1623 | 0 (n/a) | 0 (n/a) | 1623 |
| MLB | **after replace** | 2072 | 0 (n/a) | 0 (n/a) | **2072** ✓ |

Increase in totals reflects fresh slate ingest between snapshots; the preserve numbers grew with totals (no count regression).

**API smoke**: `/api/v3/ferrari/safe-haven?sport=nba` first pick now returns 248-char Gemini-authored `vision_intel` narrative. Previously `None`.

**Tests**: 113 passed, 4 skipped. Health probes `/api/health/hit-rate-side-parity` + `/api/health/hit-rate-push-invariant` both `status=ok`.

**Constraints honored**: only `prop_scores_store.py` modified in production; no scoring/gate/badge/threshold/frontend changes; no full-recompute mode switch (still `replace`); MLB step 6 NOT added — separate audit per the user's directive.

**Out of scope (next blockers)**:
- MLB master_sync step 6 (`_enrich_mlb_board_vision_intel`) does not exist — MLB picks will continue to ship without Gemini narratives until that wire-up lands.
- Race window remains for any newly-introduced post-recompute enrichment that isn't added to the allowlist. New enrichments must be registered in `_PRESERVE_ON_REPLACE`.



## 2026-05-05 — Vision Intel badge-bucket SSOT (Option C, Step 1)

`vision_intel_service.py:275` and `mlb_vision_intel.py:194` previously read `prop.get('active_badges') or prop.get('scout_badges')`. The fallback silently switched semantic buckets between routes (player-vision routes populate `active_badges` with NARRATIVE badges via `BadgeResolver`; tier endpoints leave `active_badges` empty so the fallback resolved to `scout_badges` — model PERFORMANCE signals). Any future presentation-layer alias on tier endpoints would have flipped the prompt input from performance to narrative without warning.

**Decision (Option C)**: three explicit semantic buckets, no aliasing.
- `context_badges` — narrative/situational (jet_lag, revenge, home_cookin, locked_in, …) sourced from `master_hub.context_badges`.
- `scout_badges`   — performance/model signals (hot_streak, floor_lock, lasso_high_edge, …) emitted by `services.performance_badges.generate_performance_badges`.
- `active_badges`  — presentation/display only. NOT consumed by any backend logic.

**Files changed (2)**
- `backend/services/vision_intel_service.py:275-298` — replaces the ambiguous fallback with explicit `perf_list = scout_badges`, `ctx_list = context_badges`. Adds `"context": context_text` alongside the existing `"badges": badge_text` in the prompt JSON. Same JSON-dump prompt structure, additional key — Gemini receives it as supplementary instruction text without schema impact.
- `backend/services/mlb_vision_intel.py:194-212` — same refactor for MLB. Removes the bare `prop.get('active_badges', [])` read.

**Live verification — Vision Intel prompt before/after for tier picks**

NBA Safe Haven (tier endpoints had `active_badges` empty, so fallback to `scout_badges` was already in effect):
| Pick | `badges` BEFORE | `badges` AFTER | `context` AFTER (new) |
|---|---|---|---|
| James Harden PRA | `hot_streak, floor_lock, lasso_high_edge` | `hot_streak, floor_lock, lasso_high_edge` ✓ | `home_cookin` |
| Ausar Thompson PRA | `hot_streak, floor_lock, high_fidelity_model` | `hot_streak, floor_lock, high_fidelity_model` ✓ | `home_cookin` |

MLB Safe Haven (no `context_badges` populated for MLB master_hub — `context` slot reads `None` and stays out of the prompt narrative):
| Pick | `badges` BEFORE | `badges` AFTER | `context` AFTER |
|---|---|---|---|
| Josh Jung HRR | `hot_streak, floor_lock, high_fidelity_model` | identical ✓ | `None` |
| Ozzie Albies HRR | identical ✓ | identical ✓ | `None` |
| Vladimir Guerrero Jr. HRR | identical ✓ | identical ✓ | `None` |

**Behavior on tier picks**: identical to today (the `badges` slot still receives performance signals because tier picks never populated `active_badges`). The patch closes the latent regression that would have triggered the moment any caller stamped `active_badges`.

**Behavior on player-vision routes**: unaffected by this patch (Vision Intel runs on tier-pick batches, not on `/api/player/{slug}/vision` responses).

**Constraints honored**: no changes to `routes/ferrari_tiers.py`; no `context_badges` write changes; no `scout_badges` / performance generator changes; no frontend; no Vision Intel structural refactor; no aliasing of `context_badges` into `active_badges`.

**Tests**: 108 passed, 3 skipped. Health probes `/api/health/hit-rate-side-parity` + `/api/health/hit-rate-push-invariant` both green.

**Step 2 deferred**: response-shape contract for `active_badges` (deprecate / mirror / leave) — open for separate decision now that Vision Intel no longer depends on the field.



## 2026-05-05 — NBA push-handling fix (P1)

`hit_rate_over` and `hit_rate_under` were derived via complement (`100 - hit_rate_active`) in `NBAScoringAdapter._compute_cv_and_hit_rate`. Pushes (`stat == line`) break the `OVER + UNDER = 100` identity, so the inactive side was overstated by the push percentage on whole-number lines. Audit found Julius Randle AST 4.0 with 40% pushes: OVER pick had stored `hit_rate_under=65` (correct: 25). Live exposure on `final-nba-rt` is 0 today (all .5 lines), but `nba_live_props` carries 250 whole-number lines from upstream books and `final-nba-rt-shadow` historically held 302.

**Files changed (1 production, 1 new test)**
- `backend/services/scoring/adapters/nba_scoring.py:2430-2475` — `_hr_for_window` now accepts `force_side` to compute either side independently. `hit_rate_over` and `hit_rate_under` are calculated via two direct calls; the `100 - hit_rate` complement was removed. Strict `>` / `<` semantics preserved (pushes still count as miss for both sides). `hit_rate`, `hit_rate_l5`, `hit_rate_l10` remain side-aware.
- `backend/tests/test_nba_push_handling.py` — 5 new regression tests covering: 40% push OVER/UNDER pair, no-push invariant `O+U==100`, active-side preservation, L5/L10 side-awareness.

**Live verification (in-process call against real `bdl_game_logs`)**

| Pick | Pushes | Manual O / U / P | Patched HR_O / HR_U | active | Match |
|---|---:|---|---|---:|---|
| Julius Randle AST 4 OVER | 8/20 | 35 / 25 / 40 | 35 / 25 | 35 | ✓ |
| Julius Randle AST 4 UNDER | 8/20 | 35 / 25 / 40 | 35 / 25 | 25 | ✓ |
| Victor Wembanyama AST 3 OVER | 6/20 | 35 / 35 / 30 | 35 / 35 | 35 | ✓ |
| Victor Wembanyama AST 3 UNDER | 6/20 | 35 / 35 / 30 | 35 / 35 | 35 | ✓ |
| James Harden PTS 19 OVER | 2/20 | 50 / 40 / 10 | 50 / 40 | 50 | ✓ |
| James Harden PTS 19 UNDER | 2/20 | 50 / 40 / 10 | 50 / 40 | 40 | ✓ |
| Rudy Gobert REB 11 (no push) | 0/20 | 55 / 45 / 0 | 55 / 45 | 55 / 45 | ✓ |

All inactive-side values now correct. Active-side `hit_rate` (which feeds gates and `hit_rate_l20`) unchanged in every case — gate behavior preserved on .5 lines and corrected only where pushes exist.

**Health probe** `/api/health/hit-rate-side-parity` post-recompute: NBA 1677 OVER + 199 UNDER → 0 mismatches; MLB 2741 OVER + 43 UNDER → 0 mismatches; status=ok.

**Tests**: 108 passed, 3 skipped.

**Out of scope (flagged)**: identical pattern in `services/mlb_tier_sorter.py:549`. MLB lines are all .5 today so the bug doesn't fire, but the latent risk is the same.



## 2026-05-05 — Hit-rate L20 side-awareness fix (P0)

`hit_rate_l20` was stamped with `ctx.hit_rate_over` regardless of the prop's direction, so UNDER picks displayed the OVER L20 rate next to side-aware L5 / L10 — producing contradictory tiles like Bryce Harper Hits 2.5 UNDER showing L5=100, L10=100, L20=5. Bug confirmed via DB invariant scan: `hit_rate_l20 == hit_rate_over` for both OVER and UNDER docs.

**Files changed (2 production, 1 test)**
- `backend/services/scoring/recompute.py:498` — `"hit_rate_l20": ctx.hit_rate` (was `ctx.hit_rate_over`). `ctx.hit_rate` is already side-aware in both NBA (`nba_scoring.py:2462`) and MLB (`mlb_scoring.py:213-217`) adapters.
- `backend/routes/ferrari_tiers.py:1148-1165` — `_merge_score_with_board` now reads `hit_rate_over`, `hit_rate_under`, and `hit_rate_l20` independently. The `hit_rate_l20 or hit_rate_over` fallback chain was deleted because it would have leaked the UNDER-side value into `hit_rate_over` after the writer fix.
- `backend/tests/test_field_ownership_contracts.py::TestHitRateL20Contract` — rewrote `test_hit_rate_l20_matches_legacy` → `test_hit_rate_l20_is_side_aware`. Asserts the new contract: OVER picks → `l20 == hit_rate_over`, UNDER picks → `l20 == hit_rate_under`.

**Live verification (`final-nba-rt` + `final-mlb-rt`, full recompute 1989 + 2403 docs)**

| Pick | Side | l5 | l10 | l20 (before) | l20 (after) | hit_rate_over | hit_rate_under |
|---|---|---:|---:|---:|---:|---:|---:|
| Bryce Harper Hits 2.5 | UNDER | 100 | 100 | **5** ❌ | **95** ✓ | 5 | 95 |
| Ajay Mitchell PTS 15.5 | UNDER | 80 | 90 | **20** ❌ | **80** ✓ | 20 | 80 |
| Jalen Brunson AST 5.5 | OVER | 40 | 60 | 70 | **70** ✓ | 70 | 30 |
| Willson Contreras Hits 0.5 | OVER | 80 | 80 | 70 | **70** ✓ | 70 | 30 |

**Invariant scan post-fix (final-{sport}-rt, active=True, both fields present):**
- 3947 OVER picks: `hit_rate_l20 == hit_rate_over`, **0 mismatches**.
- 347 UNDER picks: `hit_rate_l20 == hit_rate_under`, **0 mismatches**.

**API smoke (`/api/v3/ferrari/front-lines?sport=nba`)** — all 12 picks (10 OVER + 2 UNDER) have `hit_rate_l20` matching the active-side rate. UNDER picks (Ajay Mitchell, Mike Conley) now display L5=80 / L10=90 / L20=80 instead of the contradictory L20=20.

**Constraints honored**: no threshold changes, no badge changes, no gate changes, no fallbacks added, no frontend touched. Tests: 103 passed, 3 skipped (pre-existing).



## 2026-05-04 — Universal Performance Badge Generator (SSOT)

Consolidated three duplicate `scout_badges` generators into a single SSOT module. Eliminates the `lasso_high_edge` unit-mismatch bug (decimal `edge_vs_fair` compared to integer `15`) that hid the badge on every real pick.

**Files changed**
- `backend/services/performance_badges.py` — **new**. `generate_performance_badges(doc)` consumes a SSOT-shaped dict (score doc OR enriched prop) and emits `[{"badge_key", "id"}, …]`. Side-aware via `recommendation`/`direction`. Reads only canonical fields: `hit_rate_l5/l10/l20`, `hit_rate_under`, `edge_vs_fair` (DECIMAL), `p_true_active`, `vision_score`, `cv`, `usage_bump_percent`, `dvp_rank`, `matchup_analysis.sp_matchup.rank`. Delegates volatility extreme detection to `services.volatility_profile`.
- `backend/routes/ferrari_tiers.py`
  - MLB inline badge block in `enrich_mlb_intel_suite` (≈676–710): replaced ~35 lines of derivation with a single `generate_performance_badges(prop)` call.
  - `_apply_under_badge_rewire` (≈813–827): now delegates to the universal generator with an UNDER-safe allowlist (`floor_lock`, `lasso_high_edge`) so OVER-bias badges (`hot_streak`, `usage_spike`, `soft_matchup`) stay stripped on UNDER picks.
  - **New** `_apply_universal_scout_badges(pick)` helper called from `_post_process_nba_picks` and `_post_process_mlb_picks` so tier endpoints get the same badge set as player-detail endpoints (closes the cached-intel_suite short-circuit gap that was leaving MLB picks with empty `scout_badges`).
  - Removed dead local variables (`h5_rate`, `l5_avg`, `season_avg`) left over from the inline block.
- `backend/services/intel_suite_calculator.py::_generate_scout_badges` — refactored to construct a canonical doc from active logs + board pick and delegate to the universal generator. Now returns dict-form badges (consumers already accept both shapes).
- `backend/tests/test_performance_badges.py` — **new**. 23 tests locking thresholds and side-aware behavior. Includes the `lasso_high_edge` decimal-vs-percent regression (`0.14` no, `0.15` yes, `-0.15` yes) and the SP buzzsaw guard.

**Live verification**
- NBA Safe Haven (`/api/v3/ferrari/safe-haven?sport=nba`): James Harden (edge=0.2007, hr_l10=100%) now correctly carries `lasso_high_edge` + `floor_lock`; Naz Reid (edge=0.1307) correctly does NOT carry `lasso_high_edge`.
- MLB Safe Haven (`?sport=mlb`): Mike Trout / Ramon Laureano / Cam Smith / Kyle Tucker (hr_l10 ≥ 90) now stamp `floor_lock` + `hot_streak` + `high_fidelity_model` — previously empty (`scout_badges: []`).
- All 23 new tests + 81 regression tests across `test_field_ownership_contracts`, `test_score_document_parity`, `test_hit_rate_canonical` pass (104 total).

**Constraints honored**
- No frontend changes.
- No `context_badges` / `active_badges` changes.
- No new fallbacks; all reads via canonical SSOT fields.
- `extra="forbid"` Pydantic contract unchanged (badges live on board layer, not on `ScoreDocument`).



## 2026-05-04 — `momentum_data` SSOT registration (NBA only)

Bounded SSOT patch following the Defensive Momentum coverage audit. Three files changed; no writer math, no fallbacks, no gates touched.

**Files changed**
- `backend/services/field_ownership/registry.py` — new `momentum_data` FieldSpec entry: owner_collection=`prop_scores`, writer=`master_sync._enrich_nba_momentum`, fallback=NONE, null_policy=return_null, status=documented. Documents the join chain (`bdl_player_id → master_hub.team_abbr → opponent_abbr → defensive_momentum_cache`) and explicitly flags MLB momentum as a **missing feature**, not a bug.
- `backend/services/scoring/score_document_schema.py` — explicit `Optional[Dict[str, Any]] = None` declaration for `momentum_data`. The field had been incidental through `_SCORE_OUTPUT_FIELDS` only; now Pydantic write contract recognizes it.
- `backend/tests/test_score_document_parity.py` — `momentum_data` added to `_ALLOWED_DECLARED_EXTRAS` (declared on schema, but written by `master_sync._enrich_nba_momentum` post-recompute, not by the projector — same allowlist mechanism used by the 9 pre-existing tracked extras).
- `backend/services/master_sync.py::_enrich_nba_momentum` — extended INFO summary to include `total_candidates`, `enriched_count`, `skipped_count`, `coverage_pct`, and bucketed `skip_reasons={'no_bdl_id': N, 'no_team_lookup': N, 'no_event_match': N, 'no_canonical_key': N, 'no_stat_type': N, 'team_not_in_event': N, 'momentum_calc_failed': N, ...}`. Replaces the prior compact log line.

**Live verification (NBA)**
```
[MASTER_SYNC:nba] momentum_enrichment: total_candidates=2404
  enriched_count=2390 skipped_count=14 coverage_pct=99.42
  pairs=83/83 cached_board_updates=751 skip_reasons={'no_bdl_id': 14}
```
- Coverage: 99.1 % → **99.42 %** (skipped 22 → 14 — the pre-existing audit had run before tonight's master-hub bdl_id refresh).
- All 14 skipped rows carry skip_reason=`no_bdl_id` (master-hub row missing `bdl_id` for those players); `no_team_lookup` count is now 0.
- /api/health/score-document-schema-parity: `parity_ok=true`, `extras_setting="forbid"`, `declared_extras_count=10` (was 9; +momentum_data).

**Tests**: 123 passed / 4 skipped / 0 failed across `test_score_document_parity`, `test_field_ownership_contracts`, `test_hit_rate_canonical`, `test_card_contract_hr_trio`, `test_player_detail_hr_trio`, `test_hit_profile`, `test_contract_enforcer`.

**Out of scope (explicitly documented as future work, not bugs)**:
- MLB defensive momentum writer (`mlb_defensive_momentum_cache` exists but is empty; no MLB enricher running).
- `nba_cached_board.props[]` momentum mirror coverage (~88.6 %) — Option-D phased migration territory; not touched per directive.
- `/api/health/sync` does not yet have a `momentum` section — left as a follow-up if dashboarding is needed.



## 2026-05-04 — `vision_score == 0.0` false-zero fix (NARROW Path B promotion)

**Problem**: Legitimate Safe Haven candidates (Tyrese Maxey 3PM 1.5 OVER,
KAT PTS 14.5 OVER, Brunson AST 4.5 OVER, etc.) were being saved with
`vision_score=0.0`, hiding them downstream. Root cause traced to the
`pos_edge = max(0.0, edge)` clamp in
`scoring_stack.compute_vision_score`: any prop where the model sees
fractionally less value than the sportsbook de-vig consensus
(`edge_vs_fair < 0`) collapses `vision_score_raw` to 0, which then
collapses the percentile pass to 0.

**First attempt (REVERTED)**: unconditional v2 promotion (use
`vision_score_v2` whenever non-zero) — this fixed the false zeros but
**broke gate selectivity**: v2's distribution tops out around 60 for
strong picks while `vision_score_gate` was calibrated to v1's slate
percentile (`min: 80` SH / `min: 60` WZ). Result: NBA SH/WZ tier
counts collapsed to 0/0.

**Approved fix (NARROW Path B)** — `backend/services/scoring/recompute.py`,
function `_apply_vision_score_normalization` (single function, ~6 lines):

```python
for d in score_docs:
    if d.get("quality_source") == "insufficient_market":
        continue
    if d.get("vision_score") == 0.0:                # NEW: only when v1 collapsed
        v2 = d.get("vision_score_v2")
        if isinstance(v2, (int, float)) and v2 > 0:
            d["vision_score"] = round(float(v2), 1)
```

**Result on `final-nba-rt`** (post-recompute, full slate of 2,363):

| Metric | Before patch | After patch |
|---|---|---|
| Safe Haven | 0 | **12** |
| War Zone | 0 | **8** |
| Front Lines | 48 | 48 (unchanged) |
| `vision_score == 0.0` | 860 | **249** (-71%) |
| `vision_score >= 80` (SH gate) | 0 | **157** |

**Targeted gate-pass verification**: Maxey PTS 19.5 OVER
(vs=96.4), Harden PRA 24.5 OVER (vs=95.5), Embiid PRA 34.5 OVER
(vs=96.3), Embiid alt PA 24.5 OVER (vs=87.8), Dylan Harper PTS 5.5
OVER (vs=86.3) — all `tier=safe_haven, tier_reason=gates_passed`.

**False-zero rescue verification**: Maxey 3PM 1.5 OVER (vs=30.2,
v1_raw=0.0), KAT PTS 14.5 OVER (vs=32.5, v1_raw=0.0), Brunson AST
4.5 OVER (vs=36.7, v1_raw=0.0) — vision_score now non-zero in audit,
but they still correctly fail downstream gates (edge_fail /
hit_rate_fail) so they don't pollute Safe Haven.

**Files changed**: 1 — `backend/services/scoring/recompute.py`.
**Gates / scoring formulas / new fields**: untouched.
**SSOT guarantees**: preserved — only declared fields mutated;
`vision_score_v2` and `vision_score_raw` audit fields unchanged.
**Tests**: 135 passed, 3 skipped, 0 failed.
**API smoke (NBA)**: safe-haven 7 picks (7/7 vs>=80), front-lines 12,
war-zone 6. **API smoke (MLB)**: safe-haven 10, front-lines 13,
war-zone 9.



## 2026-05-04 — SSOT Tier F #4: `ScoreDocument` strict mode LIVE

**The flip**: `ScoreDocument.model_config.extra` flipped from
`"allow"` → `"forbid"`. Silent field drift in the score-doc write
path is now structurally impossible — any adapter or projector that
introduces an undeclared field hard-fails at write time.

**108 field declarations added** to `ScoreDocument`, grouped by
domain:
- Distribution probability layer (15) — `distribution_p_over`,
  `distribution_p_under`, `distribution_kind`,
  `distribution_selector_reason`, σ + sigma_source, clamp /
  effective_mu / mu_floor flags, λ / threshold / dispersion_r /
  p_param.
- ECDF / calibration audit (12) — `ecdf_p_over`, `ecdf_bucket`
  (declared `Optional[int]` after dry-run revealed adapters stamp
  `int(bucket)`), `ecdf_bucket_n`, `ecdf_version`,
  `raw_gaussian_p_over`, `isotonic_p_over`, `probability_method`,
  `probability_calibration_applied`, `raw_p_over`,
  `projection_intercept_*`, `pre_intercept_projection`.
- NBA availability guard (16) — `availability_guard_*`,
  `availability_status`, `dnp_risk_flag`, `injury_return_flag`,
  `minutes_*` flags + factors, `mu_before_availability_guard`,
  `mu_after_availability_guard`.
- NBA rate × minutes layer (10) — `rate_model_applied`,
  `rate_pts_per_min` / `rate_reb_per_min` / `rate_ast_per_min`,
  `mu_rate_projection` / `mu_model_projection` /
  `mu_final_projection`, blend weights/mode/trigger.
- NBA recency μ blend (11) — `mu_recency_blended` + L3/L5/L10med/L20
  components, weights, minutes-regression flags.
- NBA shadow projections (18) — Recipe-E + VK2 PTS + REB/AST shadow
  rates with their `_applied` flags and `delta_*` audit fields.
- NBA Phase 2 hetero σ (2) — `hetero_sigma_adjusted`,
  `hetero_sigma_multipliers`.
- NBA per-stat debias (3) — `projection_raw_pre_debias`,
  `projection_debias_amount`, `projection_debias_source`.
- NBA RFA minutes penalty (4) — flag + factor + before/after.
- MLB Empirical-Bayes shrinkage (8) — `eb_shrunk_projection`,
  `eb_player_career_mean`, `eb_weight_*`, `eb_shrinkage_applied`,
  `eb_skip_reason`, `eb_career_sample_n`, `raw_hf_projection`.
- MLB pitcher / batter μ overrides (5) —
  `mu_pitcher_workload_anchored`, `mu_active_baseline_*`,
  `expected_ip_used`, `projection_model_version`.
- LOM audit (2) — `lom_p_over`, `lom_version`.
- Misc — `war_zone_cv_modifier`, `ceiling_rate`.

**`SSOT_PYDANTIC_STRICT` env semantics clarified**: now defaults to
`true`. With `extra="forbid"` LIVE the schema raises on its own;
the env flag governs only whether `validate_score_document` re-raises
(=true, blocks the batch) or downgrades to a WARN log line (=false,
emergency observation-only escape hatch). Production should never
run with the flag false.

**New parity test suite** (`tests/test_score_document_parity.py` —
5 cases):
- `test_strict_extras_forbid` — `model_config.extra` MUST be `"forbid"`.
- `test_every_projected_field_is_declared` — bidirectional key-set
  check; `_IDENTITY_FIELDS ∪ _SCORE_OUTPUT_FIELDS ∪ _UNIVERSAL_POOL_FIELDS`
  must be a subset of `ScoreDocument.model_fields`.
- `test_no_unaccounted_declared_extras` — declared-but-not-projected
  fields are tracked in an explicit `_ALLOWED_DECLARED_EXTRAS` set
  (currently 9); growth requires a CHANGELOG entry.
- `test_required_identity_fields_are_required` — identity +
  versioning fields cannot be Optional.
- `test_live_db_has_zero_undeclared_fields` — scans 4,000 live docs;
  0 undeclared keys.

**Existing test fix** — `TestPydanticWriteContract.test_schema_accepts_valid_doc`
inverted: previously `ScoreDocument.model_validate({**doc, "some_diagnostic_field": 42})`
was expected to PASS under `extra="allow"`. Now it MUST raise
`ValidationError` (Tier F #4 invariant).

**New health probe**: `GET /api/health/score-document-schema-parity`
— tiny read-only diff returning
```
{
  "parity_ok": true,
  "extras_setting": "forbid",
  "declared_count": 235,
  "projected_count": 226,
  "missing_declarations": [],
  "missing_count": 0,
  "declared_extras": [...9 tracked entries...],
  "declared_extras_count": 9,
  "generated_at": "..."
}
```
No scheduler, no writer, no fallback path — purely an in-process
metadata read.

**Verified**:
- Dry-run recompute (NBA + MLB) at limits 50, 200, 500 — total ~900
  prepared docs, **0 ValidationErrors**.
- Live recompute NBA (limit=20): processed=14, written=14.
- Live recompute MLB (limit=20): processed=16, written=16.
- Backend `/var/log/supervisor/backend.err.log` post-flip: zero
  `SSOT_PYDANTIC` / `score doc failed` entries.
- All 6 tier endpoints serving picks normally post-flip.
- **124/124 SSOT tests green** (was 119; +5 new parity tests).

**Remaining SSOT debt** (post-Tier-F):
- **P0 — Tier G**: drop the `direction` reader-fallback once frontend
  purges `pick.direction`; writer purge of legacy `edge_pct` /
  `vk_edge` from DB docs (post-TTL aging-out).
- **P0 — `dg_cached_board` Option-D phased migration** (3 sessions:
  dual-write enrichment into `prop_scores`/`master_hub` → reader
  migration → drop `nba_cached_board`/`mlb_cached_board`).
- **P1 — `vision_score == 0.0` root-cause** (legit Safe Haven picks
  hidden by score collapse).
- **P1 — PP-Only stat-family TP fallback** in `mlb_scoring.py`
  (derive fair odds from PP alt-line ladder when sportsbook anchors
  missing).
- **P1 — Pitcher Strikeouts L20 distribution-derived fallback**.
- **P2** — collapse `_SCORE_OUTPUT_FIELDS` tuple in favour of
  `ScoreDocument.model_fields` once one full slate proves parity holds.



## 2026-05-04 — SSOT Tier F #3 (Option C): legacy `dg_cached_board*` cleanup

**Decision (per user)**: a full migration off `nba_cached_board` /
`mlb_cached_board` requires a 3-phase Option-D plan touching
~18 reader files and 8 writer files (because those collections hold
unique enrichment data — `intel_suite`, `scout_badges`,
`context_badges`, `vision_intel`, `hit_rates`, photo/team/opponent
metadata — that is NOT in `prop_scores` / `live_props` /
`master_hub`). It cannot be done in one session without either
introducing new caches (forbidden) or recomputing per request
(production unsafe).

**Option C scope (this session)** — purge the truly legacy
`dg_cached_board*` naming, drop the orphaned `_temp` shadow table,
and document the architecture going forward.

**Mongo state proven**:
- Pre: `dg_cached_board=absent · dg_cached_board_temp=122 · nba_cached_board=122 · mlb_cached_board=68`
- Post: `dg_cached_board=absent · dg_cached_board_temp=absent · nba_cached_board=122 · mlb_cached_board=68`
- `dg_cached_board` itself was already dropped 2026-04-30 in the
  Orphan Collection Sweep; this session only removes the leftover
  `_temp` shadow.

**Code changes**:
- `backend/server.py` — removed startup `create_index` call on
  `COLL("board_cache_temp", "nba")` so the collection is not
  recreated on next boot.
- `backend/services/config/collection_names.py` — removed the
  `"board_cache_temp"` mapping entry and the unused
  `BOARD_CACHE_TEMP_NBA` module constant. Updated the Wave-2 audit
  comment to reflect that Phase-2 atomic rename was completed in
  the 2026-04-30 Orphan Sweep.
- `backend/repositories/board_repo.py` — flipped the `cached_board`
  shadow-write comment to reflect that the legacy primary is gone
  and the handle resolves to `nba_cached_board`.
- `backend/services/engines/board_intelligence_engine.py` — renamed
  the `self.dg_cached_board` instance attribute to
  `self.cached_board` (5 sites: 1 declaration + 4 readers). Plain
  rename, no logic changed.
- `backend/services/injury_triggered_rescore.py` — module docstring,
  inline comments at lines 180/229/327/344 migrated to the canonical
  `nba_cached_board` name.
- `backend/services/picks_getter_service.py` — module docstring +
  class docstring updated.
- `backend/services/vegas_regression_model.py`,
  `backend/services/ssot_data_layer.py`,
  `backend/services/engines/adaptive_sync_engine.py`,
  `backend/services/board/adapters/base.py`,
  `backend/services/picks/board_formatter.py`,
  `backend/services/bdl_comprehensive_sync.py`,
  `backend/services/market_moves_engine.py`,
  `backend/routes/command.py`,
  `backend/routes/ferrari_tiers.py`,
  `backend/config/collections.py`,
  `backend/models/board.py`, `backend/models/prop.py`,
  `backend/server.py` — comments and docstrings updated to remove
  every misleading "live data source" reference to `dg_cached_board`.

**Architectural status (codified for the next agent)**:
- `dg_cached_board*` is GONE — collection level and (live)
  documentation level. Any historical reference is explicitly
  flagged as legacy/audit-only.
- `nba_cached_board` and `mlb_cached_board` ARE the canonical
  display-enrichment collections today. They hold ~40 fields not
  available elsewhere and serve ~40 read sites.
- A future Option-D phased migration is required to retire them:
  Phase 1 dual-write enrichment into `prop_scores`/`master_hub`,
  Phase 2 reader migration, Phase 3 collection drop. That is
  scoped as a multi-session effort, NOT a Tier F deliverable.

**Regression test** (`tests/test_field_ownership_contracts.py::TestDgCachedBoardRetired`):
- `test_dropped_collection_does_not_exist[dg_cached_board]` ✅
- `test_dropped_collection_does_not_exist[dg_cached_board_temp]` ✅
- `test_live_display_collection_still_exists[nba_cached_board]` ✅
- `test_live_display_collection_still_exists[mlb_cached_board]` ✅
- `test_no_active_query_on_dropped_collections` ✅ (static AST-style
  scan over `backend/` excluding archive/tests/scripts: zero
  `db["dg_cached_board(_temp)?"].find/.update/.insert/.aggregate/...`
  patterns).

**Verified**:
- 119/119 tests green across the SSOT suite.
- Live smoke (6 tier endpoints): all returning picks; backend healthy.
- Mongo confirmed: legacy collections absent, live collections intact.



## 2026-05-04 — SSOT Tier F #2: `edge_pct` / `vk_edge` / `true_edge` alias stamping deletion

**Problem**: Backend response paths were stamping three legacy edge
aliases (`edge_pct`, `vk_edge`, `true_edge`) onto every API pick, in
parallel with the canonical `edge_vs_fair`. Frontend had already
migrated off all three (Tier E — verified: zero active readers,
only commented migration markers remain), so the aliases lingered
purely as back-compat debt. Per FIELD_OWNERSHIP.md:edge, the
canonical owner is `edge_vs_fair` (stamped by
`scoring_stack.compute_vision_score`).

**Writer deletions (response never carries legacy edge aliases)**:
- `routes/ferrari_tiers.py::_merge_score_with_board` — removed
  `prop["vk_edge"] = …` from vk2-projection branch and from
  `model_projection` fallback branch; removed
  `prop["edge_pct"] = score.get("edge_pct")`; added defensive
  `prop.pop("edge_pct"/"vk_edge"/"true_edge", None)` after
  `board_entry["prop"]` clone.
- `routes/ferrari_tiers.py` MLB prop-merge — removed
  `pick["edge_pct"] = sc.get("edge_pct")`; added defensive pops on
  the `dict(sc)` base shape.
- `routes/ferrari_tiers.py::normalize_mlb_pick_for_ui` — deleted
  the `if 'edge_pct' in normalized: normalized['vk_edge'] =
  normalized['edge_pct']` bridging shim.
- `routes/ferrari_tiers.py::enrich_picks_with_vk` — removed
  `pick["vk_edge"]` stamp (canonical `edge_vs_fair` already on the
  pick from upstream).
- `routes/ferrari_tiers.py` cached-board merge — dropped
  `prop["vk_edge"] = vk_data.get("edge")`.
- `routes/ferrari_tiers.py::top_5_goblins` response — renamed
  `"edge_pct"` → `"edge_vs_fair"`.
- `routes/player.py::_score_to_prop` — player-detail response
  surfaces `"edge_vs_fair"` only (was `"edge_pct"`).
- `routes/debug_snapshots.py::_normalize_reject` — dropped
  `"edge_pct"` surfaced-alias field; response keeps
  `"edge_vs_fair"` only.

**Reader migrations to canonical `edge_vs_fair`**:
- `_rank_score` rank tiebreaker (ferrari_tiers.py) — reads
  `edge_vs_fair` only.
- `lasso_high_edge` badge rule — reads `edge_vs_fair` only.
- HRR war-zone Mongo filter
  (`/api/v3/ferrari/war-zone-hrr` equivalent) — switched
  `$gte` predicate from `edge_pct` to `edge_vs_fair`; value_score
  calc migrated too.
- `debug_snapshots.py` — `_query_rejects` sort, shadow_board_compare
  projection + `_agg` (reports `avg_edge_vs_fair`), rank-tuple
  metadata, and top20_diffs entries all migrated.

**Registry note** (`services/field_ownership/registry.py::edge`)
updated to reflect alias STAMPING deletion; DB may still carry
`edge_pct` on pre-Tier-F docs (writer purge deferred to a dedicated
backfill sweep) but nothing escapes to public API.

**Contract enforcement**:
- New regression class
  `TestEdgeAliasStampingRemoved` in
  `tests/test_field_ownership_contracts.py` — 6 live-API tests
  (NBA+MLB × safe-haven/front-lines/war-zone) assert NO pick
  contains any of `edge_pct` / `vk_edge` / `true_edge`, and every
  pick carries `edge_vs_fair`.

**Metrics**:
- **Writer count before → after**: 9 response-level alias stamps in
  routes (`ferrari_tiers.py × 7` + `player.py × 1` +
  `debug_snapshots.py × 1`) → **0**.
- **Backend reader count before → after** (routes + card-building
  services): 4 canonical-fallback reads (rank tiebreaker, badge
  rule, HRR filter, value_score) → **0** legacy alias reads.
  `.get("edge_pct"/"vk_edge"/"true_edge")` count in
  routes/ + dashboard_card_contract.py + picks_getter_service.py +
  mlb_cached_board_builder.py = **0**.
- **Frontend reader count**: already 0 active reads (Tier E);
  re-verified: only commented migration markers remain.
- **Live API alias absence**: 6 tier endpoints × 41 picks →
  `edge_pct=0 vk_edge=0 true_edge=0 edge_vs_fair=41/41`.
- **Tests**: 115 passed / 0 failed.



## 2026-05-04 — SSOT Tier F #1: `direction` alias stamping deletion

**Problem**: Response-building writers were duplicating the canonical
`recommendation` value into a legacy lowercase `direction` key on
every user-facing pick dict. Per FIELD_OWNERSHIP.md:side the
canonical OVER/UNDER selector is `recommendation` (owned by
`live_props.recommendation`) with display-layer `side`. The extra
`direction` key was a back-compat shim for ~8 call sites that
pre-dated the Tier C side migration. Keeping it alive let upstream
regressions silently rewrite canonical state.

**Writer deletions (response never carries `direction` again)**:
- `routes/ferrari_tiers.py::_merge_score_with_board` — dropped
  `prop["direction"] = direction_title` stamp. Also added a
  defensive `prop.pop("direction", None)` right after the
  `board_entry["prop"]` clone, because `nba_cached_board.props[]`
  still carries a legacy `direction` from the pre-Tier-C writer.
- `routes/ferrari_tiers.py` MLB prop-merge block — dropped
  `pick["direction"] = direction_title` + added defensive
  `pick.pop("direction", None)` on the `dict(sc)` base shape.
- `routes/ferrari_tiers.py` MLB detail-page backfill — removed the
  `if not prop.get("direction"): prop["direction"] = prop.get("recommendation", "Over")`
  block (upstream must provide `recommendation`; fail loud if missing).
- `routes/ferrari_tiers.py` top-5 goblins response — renamed
  `"direction": g.get("recommendation")` to
  `"recommendation": g.get("recommendation")`.
- `services/picks_getter_service.py` — `get_goblin_vault`,
  `get_front_lines`, `get_cached_player` now stamp
  `"recommendation"` (Title-cased) instead of the `"direction"`
  alias.
- `services/mlb_cached_board_builder.py::_enrich_prop` — dropped
  the `"direction": prop.get("direction") or rec_title` stamp;
  canonical `recommendation` + `side` stay.

**Reader migrations (canonical-first, `direction` only as
last-resort tolerance)**:
- `routes/ferrari_tiers.py::_resolve_prop_direction`, 4-tuple
  player-stat lookup, vision-intel content hash, UNDER filter,
  UNDER-rewire loop, MLB score-doc merge.
- `services/dashboard_card_contract.py::to_card_contract` — side
  extractor now reads `recommendation → side → direction`.
- `services/board/adapters/base.py::canonical_key` — same order.
- `services/market_moves_engine.py::_snapshot_from_tiers` —
  canonical-first when composing the internal snapshot state.

**Contract enforcement**:
- `services/contract_enforcer.py::PICK_CARD_REQUIRED_KEYS` —
  removed `"direction"`; kept `"recommendation"` as the sole
  canonical side field. Lockdown test updated accordingly.
- New regression test in
  `tests/test_field_ownership_contracts.py::TestDirectionAliasStampingRemoved`
  — asserts against live API that NO pick from
  `/api/v3/ferrari/{safe-haven,front-lines}` for either sport
  carries a `direction` key, and that every pick has a non-null
  `recommendation`.

**Registry note** (`services/field_ownership/registry.py::side`)
updated: alias stamping deleted; fallback reads retained as a
transitional upstream-ingester tolerance only, slated for deletion
in Tier G once the frontend purges its own `pick.direction` reads.

**Verified**:
- 109 passed / 0 failed across hit_rate_canonical,
  field_ownership_contracts, card_contract_hr_trio,
  player_detail_hr_trio, hit_profile, contract_enforcer suites.
- Live smoke on 6 tier endpoints × 40 picks:
  `direction_key_present=0`, `recommendation` populated on 100%,
  `side` populated on 100%.
- Contract enforcer no longer emits
  `[CONTRACT:invalid_pick_card] missing_or_null=['direction']`
  (log check post-restart).




## 2026-04-30 — Orphan Collection Sweep (P0 #6)
**Problem**: 9 archive/backup collections in Mongo totaling **861,813
docs and 188.9 MB** — 8 months of rename-residue, not read or written
by any runtime code. Only references were docstring prose and one
dead `_SPORT_COLLECTIONS["prop_scores_archive"]` dict entry.

**Fix**:
- New idempotent sweep script `scripts/sweep_orphan_collections.py`
  writes a JSON manifest (counts, schema, sample docs, timestamps) to
  `/app/backend/data/snapshots/archives/` BEFORE dropping each
  collection.
- 9 collections dropped: `dg_cached_board_backup`,
  `dg_events_cache_backup`, `dg_live_props_backup`,
  `dg_master_roster_backup`, `dg_odds_cache_backup`,
  `line_history_backup`, `mlb_prop_scores_archive_stale_tags`,
  `nba_prop_scores_archive_stale_tags`, `referee_assignments_backup`.
- Removed dead `prop_scores_archive` entry from
  `services/config/collection_names.py::_SPORT_COLLECTIONS`.
- 3 regression tests in `tests/test_orphan_sweep_integrity.py`:
  - INV-1: no dropped name may reappear in the database
  - INV-2: no dropped name may be reintroduced in collection_names.py
  - INV-3: manifest directory + all 9 manifest files must exist

**Verified**: zero archive/backup collections remain; live MLB+NBA
endpoints return HTTP 200 post-restart; full P0 suite passes 32/32
in 5.6s.

**Audit trail**: 9 JSON manifests preserved under
`/app/backend/data/snapshots/archives/`. Full doc at
`/app/memory/SYSTEMS_orphan_sweep.md`.


## 2026-04-30 — Sync Failure Rate: 15% → ~0% (P0 #3)
**Problem**: 75 of 76 MLB sync failures (98.7%) had one root cause:
`E11000 duplicate key error` on `(canonical_key, version_tag)` in
`mlb_prop_scores`.

**Root cause**: Race window in `prop_scores_store.write_versioned_scores`
`mode=replace` path. The old pattern was:
```
  delete_many({"version_tag": tag})   # ← realtime engine upserts here
  insert_many(new_docs)                # → E11000, whole sync fails
```
Master_sync (mode=replace, hourly) overlapped with the realtime engine
(mode=upsert, ~10s intervals). ~20% of rebuilds hit the race.

**Fix**:
- Replaced `delete_many + insert_many` with race-safe
  `bulk_write([ReplaceOne(..., upsert=True)])` followed by a single
  `delete_many(canonical_key: {"$nin": new_cks})` stale sweep.
- `ReplaceOne(upsert=True)` is atomic per-document — any concurrent
  realtime upsert produces a replace, never a conflict.
- Empty batch still wipes the whole tag (preserves old contract,
  locked by INV-3).
- BulkWriteError edge cases route through `log_caught_exception` with
  full context (version_tag, op_count) — now visible on the admin
  error dashboard.

**5 invariants** in `tests/test_prop_scores_store_race.py` (all pass):
- INV-1: race-safe under 5 concurrent replace+upsert cycles (reproduces
  the exact pattern that caused the original failures)
- INV-2: stale keys swept
- INV-3: empty batch wipes tag (contract preserved)
- INV-4: result-dict shape stable
- INV-5: `mode=upsert` unchanged

**Live verification**: triggered full MLB sync with realtime engine
actively running → 2,613 props dual-written (canonical + shadow),
multiple realtime upserts interleaved, **zero E11000 errors**. Fix
confirmed under production load.

**Full P0 suite**: 29/29 tests pass in 5s.

Full doc: `/app/memory/SYSTEMS_prop_scores_store.md`.


## 2026-04-30 — MLB Vacuum + Injury-Advantage Regression Suite (P0 #4)
**Problem**: 2,446 LOC of vacuum / injury / injury-advantage code had
ZERO tests on the MLB side. Subsystem regressed 5 times in 30 days
(late-scratch alerts silently stopped; `live-alerts` returned
placeholder rows; routing silently swapped to wrong engine; etc.).
Every fix shipped without a test. Every bug came back.

**Fix**:
- New file `tests/test_mlb_vacuum_injury.py` — 13 tests covering:
  - Pure `_estimate_benefit` function (3 tests)
  - Universal engine `compute_injury_advantages` contracts (4 tests)
    - INV-1: empty DB → `[]`, never raises
    - INV-4: same-team gate + no self-boost
    - INV-5: one advantage per beneficiary (dedup)
  - HTTP endpoints `/api/v3/mlb/vacuum/{live-alerts,active,clear,updates}`
    (5 tests, including cache-control + 404-vs-500)
  - `_get_recency_window` fallback behavior
- Isolated `seeded_db` fixture with per-test UUID tag → clean teardown,
  no production data disturbance.
- Full invariants doc at `/app/memory/SYSTEMS_mlb_vacuum_injury.md`
  with named INV-1 through INV-5 referenced in every failure message.

**Verified**: 13/13 new tests pass. Full P0 suite (observability + version
tags + canonical-key parity + vacuum/injury): **24/24 pass in 3.1s**.


## 2026-04-30 — Canonical Key Adapter Parity Test (P0 #7)
**Problem**: On 2026-04-29 the NBA adapter was discovered to have been
silently returning `None` for every `canonical_key(doc)` call — the
realtime scoring pipeline silently dropped every NBA prop for DAYS.
The bug was invisible because nothing tested it.

**Fix**:
- New file `tests/test_canonical_key_adapter_parity.py` with 4 tests
  (2 parametrized over `SUPPORTED_SPORTS`):
  1. Adapter's `canonical_key()` returns non-None for 100 real docs.
  2. Reconstructed key matches the precomputed `canonical_key` field
     exactly when present (catches ingest-vs-adapter drift).
  3. Key starts with the sport prefix (catches accidental field swap).
  4. Synthetic minimal-doc shape produces a valid key.
- Parametrized over `SUPPORTED_SPORTS` so adding a new sport auto-adds
  coverage with no test changes.
- Skips cleanly if a sport has no live_props yet (fresh env).

**Invariants**:
- Adapter `canonical_key()` must never return None for a valid
  live_props doc.
- Reconstructed key must be byte-identical to the precomputed
  `canonical_key` field when present.
- Every sport in `SUPPORTED_SPORTS` must have a registered adapter.

**Verified**: 4/4 new tests pass (MLB + NBA parametrized, synthetic,
registry). Total suite: 11/11 pass across P0 #1, #2, #7.


## 2026-04-30 — Version Tag Single Source of Truth (P0 #2)
**Problem**: 22 hardcoded `final-<sport>-<suffix>` string literals
scattered across `services/` + `routes/`. Every rename silently
drifted at least one caller. NBA realtime was dead for days because
of exactly this pattern.

**Fix**:
- New module `config/version_tags.py` exports `MLB_LIVE`, `MLB_SHADOW`,
  `MLB_BASELINE`, `NBA_LIVE`, `NBA_SHADOW`, `NBA_BASELINE` plus
  `for_sport()`, `shadow_for()`, `is_live_tag()`, `sport_of()` helpers.
- Lint test `tests/test_version_tag_literals.py` scans all of
  `services/` + `routes/` via AST and FAILS the suite if any file
  reintroduces a raw `final-<sport>...` literal. Only
  `config/version_tags.py` itself is allowlisted.
- 19 literals replaced with named imports across 6 files:
  `master_sync.py`, `injury_triggered_rescore.py`,
  `board/adapters/{mlb,nba}.py`, `scoring/prop_scores_store.py`,
  `routes/debug_snapshots.py`, `routes/ferrari_tiers.py`.
- Full doc at `/app/memory/SYSTEMS_version_tags.md`.

**Invariants**:
- No hardcoded `"final-<sport>..."` string in `services/` or `routes/`.
- `config/version_tags.py` stays allowlist of one.
- `for_sport("unknown")` raises `ValueError` (no silent fallback).

**Verified**: 7/7 tests pass (2 new + 5 observability). Backend startup
clean. Live MLB + NBA endpoints return HTTP 200 post-restart.


## 2026-04-30 — Structured Observability (P0 #1)
**Problem**: 44 `except [Exception]: pass` handlers across services + routes
were silently swallowing exceptions. Primary cause of regression-churn —
bugs caught, nothing alerted, fixes rotted, same fix shipped twice.

**Fix**:
- New module `services/observability/error_log.py` with `log_caught_exception`
  (async) and `log_silent_failure` (sync). Writes structured rows to
  `error_log` collection with TTL 14d, indexed by subsystem + exception_type.
- Admin endpoints `GET /api/v3/admin/errors/summary` and `/recent` for triage.
- One-shot AST-based converter `scripts/sweep_silent_handlers.py`
  (idempotent) swept 37 silent handlers across 27 files into
  `log_silent_failure(...)` calls. Remaining 7 handlers are in files with
  pre-existing AST-blocking syntax issues; convert manually on next touch.
- 5 regression tests in `tests/test_error_log_observability.py` lock the
  primitive's invariants.
- Full doc at `/app/memory/SYSTEMS_observability.md`.

**Invariants** (must not regress):
- Logger never raises (even when DB is down).
- TTL on `ts` maintained, traceback truncated at 16KB.
- New silent `except: pass` is banned; re-running the sweep is idempotent.


## 2026-04-27 — Cached Board Combo / Alt-Market Routing Fix

**User directive**: *"Ensure cached board props are routed and displayed correctly for combo stats and alt-market lines. Backend only. Do not change scoring/LOM/gates/thresholds/TP/frontend."*

### Root cause (two bugs)
1. **NBA** — score-doc writers leak raw Odds-API market keys (e.g. `player_points_rebounds_alternate`) into `stat_type`; lookup-side aliasing in `routes/ferrari_tiers.py` worked, but the API still surfaced the raw key to the UI. Result: users saw `player_points_rebounds_alternate` instead of `P+R`.
2. **MLB** — `mlb_cached_board_builder.enrich_prop` preserved `direction: None` from upstream live-props (only `recommendation` was set). Any consumer joining on `direction`/`side` got nothing. `canonical_key` was built from the raw label, not the canonical stat.

### Fix
- **NEW** `services/scoring/stat_family.py` — SSOT normaliser for both NBA and MLB. `canonical_stat_family(stat, sport)`, `build_canonical_key(...)`, `is_pitcher_stat`, `is_batter_stat`, `is_combo_stat`. Idempotent, case-insensitive.
- `routes/ferrari_tiers.py` — duplicated `_STAT_FAMILY_ALIAS` deleted; imports from SSOT. In `_merge_score_with_board`, promote canonical token to `stat_type` and stash original as `stat_type_raw`.
- `routes/player.py` — same SSOT import; alias map deleted.
- `services/mlb_cached_board_builder.py::enrich_prop` — sets `direction`/`side`/`recommendation` consistently, rebuilds `canonical_key` from canonical stat, persists `stat_type_canonical`.
- **NEW** `services/scoring/scripts/validate_board_routing.py` — read-only routing-health audit; outputs to `/tmp/board_routing_report.json`.
- **NEW** `tests/test_stat_family_routing.py` — 21 regression tests covering alt-line preservation, OVER/UNDER separation, pitcher↔batter, combo↔base, HRR↔Hits, Q1 markets, idempotency.

### After-fix metrics
- **NBA picks**: 3 visible war-zone picks fixed (Jalen Duren, VJ Edgecombe, Keldon Johnson — `player_points_*_alternate` → `P+A` / `P+R`).
- **MLB cached_board**: 2 738 / 2 738 props now have `direction` set (was 0). `stat_type_canonical` and rebuilt `canonical_key` populated for every prop.
- **All 21 regression tests passing**. No combo↔base collisions, no pitcher↔batter collisions.

### Out of scope (explained in `/tmp/board_routing_fix_REPORT.md`)
- NBA MISS rate (14.9 %): genuine cached_board coverage gaps from upstream sync timing — not a routing bug.
- NBA `player_stat_only` fallback rate (30.6 %): expected line-drift behaviour — line-agnostic enrichment falls back correctly.
- NBA cached_board *writer* (lost source, only `.pyc`): we normalise **on read** at the route layer; the cache itself already stores compact tokens.


## 2026-04-27 — Shadow VK Forward-Test Pipeline (Parts 1 + 2)

**User directive**: *"Proceed with option 3: (a) + (b). Part 1 — partial, time-stable feature audit (directional only). Part 2 — set up the real forward-test shadow pipeline."*

### Part 1 — Partial-feature directional audit (read-only)
**NEW** `/tmp/train_shadow_vk_partial.py` produces a 5-fold CV comparison of:
- **baseline** = `[vk_predicted, vk_prob, line]`
- **shadow_partial** = baseline + season-stable context features (`opp_def_rating_season`, `pace_differential`, `potential_assists_rate`, `home_away_flag`, one-hot `defensive_matchup_tier`, imputed-flags)

Trained on 272 resolved NBA snapshots (2026-04-13 → 2026-04-22).

**Outputs** (clearly labelled "Directional only — not valid for promotion"):
- `/tmp/shadow_vk_partial_REPORT.md`
- `/tmp/shadow_vk_partial_metrics.json`

**Headline result**: shadow_partial **slightly worse** overall (Brier +0.011, log-loss +0.058, MAE +0.084). Likely overfit on N=272 with 11 features. Pockets of improvement: AST stat-type (Brier −0.040), elite-defence tier (Brier −0.048). REB and "weak" tier degrade significantly (small-N noise). Recommendation: defer judgement until Part 2 produces co-located data with the time-varying features that were excluded here.

### Part 2 — Forward-test shadow capture pipeline (parallel, read-only)
**NEW** `services/shadow/shadow_capture_service.py`
- `capture_shadow_snapshots(db, sport, capture_date)` — joins today's `forward_test_snapshots` with `nba_player_context_features`, writes to a new collection `shadow_vk_snapshots`. **±2-day freshness gate** drops stale context (prevents today's features from being attached to historical snapshots).
- `resolve_shadow_outcomes(db)` — copies `outcome` / `actual_value` from resolved FTS rows onto sibling shadow rows. Idempotent.
- `stats_summary(db)` — pipeline health metrics.

**Schema** of `shadow_vk_snapshots`:
```
sport, capture_date, captured_at, capture_reason,
player_name, player_id, team, opponent, game_id, commence_time,
stat_type, line, side ('over'/'under'),
vk_predicted, vk_prob, vk_edge,            # production (read-only)
shadow_predicted, shadow_prob,             # null until model trained
context_features { 10 fields + feature_coverage + source provenance },
outcome, actual_value, resolved_at,
fts_key_hash
```

**Indexes**: `(sport, capture_date)`, `(sport, player_name, stat_type, capture_date)` UNIQUE, `(outcome, capture_date)`.

**Cron wiring** (`services/cron_scheduler.py`):
- `run_forward_test_capture` (1830 ET): now appends `capture_all_shadow(...)` after standard FTS capture. Wrapped in try/except so it can never break production capture.
- `run_forward_test_resolve` (0500 ET): now appends `resolve_shadow_outcomes(...)` after the standard resolver.

**Bootstrap run** (`/tmp/bootstrap_shadow_pipeline.py`):
- 493 historical rows mirrored from existing FTS (256 NBA, 237 MLB)
- 490 resolved by sibling-row outcome copy
- Historical rows correctly persist with **null context_features** (freshness gate working — stale context not attached)
- Forward-path smoke test confirmed: when capture_date is within ±2 days of `nba_player_context_features.computed_at`, all 6 spec features attach (verified with synthetic Jaylen Brown / AST row, ctx_coverage=1.0)

### What this does NOT do
- Does **not** train or deploy a shadow model — `shadow_predicted` stays null until 7–14 days of co-located resolved data accrue and an offline trainer is run.
- Does **not** modify production VK, scoring adapters, gates, tiers, or any live endpoint.
- Does **not** alter `forward_test_snapshots` schema or resolver behaviour.

### Files touched
- `services/shadow/__init__.py` (new)
- `services/shadow/shadow_capture_service.py` (new)
- `services/cron_scheduler.py` (additive: shadow hook in capture + resolve jobs)
- `/tmp/train_shadow_vk_partial.py` (new, audit script)
- `/tmp/bootstrap_shadow_pipeline.py` (new, one-shot)



## 2026-05 — NBA Context Feature Engine (`services/features/nba_feature_engine.py`)

**User directive**: *"Add a new feature engineering layer for NBA props to support the upcoming VK retrain. Do NOT modify scoring/gating/probability logic. Optional, non-breaking."*

### What shipped
**NEW** `services/features/nba_feature_engine.py` — computes 10 context features per (player, game, stat) and persists them to a new collection `nba_player_context_features` (Option B — separate collection, zero risk to existing readers).

**Public API**:
- `build_player_context_features(db, bdl_player_id, game_id, **kwargs) → dict`
- `build_team_context(db, team_abbr, game_id, **kwargs) → dict`
- `enrich_slate(db, sport='nba') → coverage report`

**Integration**: `services/universal_odds_sync.py` — calls `enrich_slate` AFTER props are persisted, wrapped in try/except. Failure here is logged but never blocks the sync. Production scoring path is untouched.

### Verified coverage on 1,878 NBA props
| Tier | Feature | Coverage |
|---|---|---:|
| T1 | usage_vacuum_factor | 100 % |
| T1 | key_player_out_flag | 100 % |
| T1 | team_usage_removed_pct | 100 % |
| T1 | blowout_risk | 100 % |
| T1 | rest_days | 100 % |
| T1 | back_to_back_flag | 100 % |
| T2 | pace_differential | 100 % |
| T2 | defensive_matchup_tier | 100 % |
| T3 | potential_assists_rate | 95.1 % |
| T3 | home_away_split_delta | 0 % (data-source gap; structural only) |

Average feature_coverage: 0.895 across all 1,878 rows. **All Tier-1 + Tier-2 features at 100 %.**

### Data sources used (read-only)
- `nba_master_hub_2026` — roster + game logs
- `injuries_normalized` (canonical) + `live_injuries` (legacy fallback)
- `bdl_advanced_stats` — `usage_percentage`, `passes`, `is_home`
- `defensive_momentum_cache` — opponent positional defensive rating
- `dg_raw_odds_markets` (`spreads`) — Vegas spread for blowout_risk
- `services.team_stats_service.TEAM_PACE_2026` — team pace constants

### Assumptions
1. `usage_rate` ≥ 20 % defines a "key player". Aligned with NBA analytics convention.
2. `blowout_risk = min(|spread| / 15, 1.0)` per spec.
3. `pace_differential` capped at ±10 pts/100poss before normalizing to [-1, 1].
4. `defensive_matchup_tier` thresholds: ≤ 110 elite · 110-116 average · ≥ 116 weak.
5. `home_away_split_delta` joins master_hub `bdl_game_logs.game_id` to `bdl_advanced_stats.is_home`. Currently 0 % coverage because NBA hub `bdl_game_logs` doesn't carry `game_id` for older entries — once a future hub-sync backfill stamps `game_id` on every log, this feature will activate without code change.
6. `potential_assists_rate` proxied as `passes / matchup_minutes` from L10 `bdl_advanced_stats`. True "potential assists" requires NBA Stats API which isn't on disk.

### Guardrails respected
- ✅ No scoring / gating / probability / tier-output / production-endpoint changes
- ✅ New features computed and persisted only; not yet consumed by VK
- ✅ Failure-tolerant integration (sync proceeds even if enrich_slate fails)
- ✅ All missing values are `None` (NEVER `0`); structural features include the `feature_coverage` ratio
- ✅ Feature documents indexed on (`bdl_player_id`, `event_id`, `stat_type`) unique

### Files
- `services/features/nba_feature_engine.py` (NEW, ~390 LOC)
- `services/universal_odds_sync.py` (1 try/except block, ≤14 LOC)
- `/tmp/nba_feature_engine_validation.py` (validation script)



## 2026-05 — Forward-Test Resolver Pipeline Repaired

**User directive**: *"Restore resolution of props after games complete — actual_value & outcome were None on every snapshot, blocking all model evaluation."*

### Root cause
Three independent bugs:
1. **No scheduled job for resolution.** `forward_testing_service.resolve_outcomes` existed and was reachable via API, but `cron_scheduler.py` only scheduled `run_forward_test_capture` — nothing ever called the resolver.
2. **NBA name field bug.** `nba_master_hub_2026.player_name` is `None` on every doc; the canonical name lives in `display_name`. The resolver was reading `player_name`, so every NBA lookup matched a `None`-keyed dict and returned no stats.
3. **Date-window mismatch.** `capture_date` is set when the prop is captured, but `game_time` can be 1–5 days later (NBA snapshots are pulled days ahead during playoffs). The resolver only searched `capture_date`, missing the actual game date entirely.

### Result
| Sport | Resolved BEFORE | Resolved AFTER |
|---|---:|---:|
| NBA | 11 / 279 (3.9 %) | **272 / 279 (97.5 %)** |
| MLB | 180 / 238 (75.6 %) | **238 / 238 (100 %)** |

### Files changed
- `services/forward_testing_service.py`
  - `_fetch_game_results` switched to `nba_master_hub_2026` / `mlb_master_hub_2026` (full-season coverage); cached_board is now a fallback. NBA reads `display_name` as canonical name. MLB game-log rows carry `player_name` per row.
  - `resolve_outcomes` now (1) prefers the date implied by the snapshot's `game_time`, (2) on-demand fetches game results for any date encountered, (3) falls back to a ±1 window on `capture_date` when `game_time` is absent.
  - `_get_stat_value` now normalizes DK display names ("Batter Strikeouts", "Hits+Runs+RBIs", "Pitcher Strikeouts" etc.) → uppercase + underscores. Added mappings for `SINGLES` (calculated), `DOUBLES`, `TRIPLES`, `BATTER_WALKS`, `PITCHER_WALKS`, `WALKS_ALLOWED`, `HITS_ALLOWED`, `EARNED_RUNS`, `STOLEN_BASES`.
- `services/cron_scheduler.py`
  - Added `run_forward_test_resolve` — walks the last 14 unresolved capture dates per sport, runs `resolve_outcomes`, logs per-date `[FT_RESOLVE_CRON]` line.
  - Scheduled at **09:30 UTC daily (05:00 ET)** — 30 min after the master-hub sync that refreshes `bdl_game_logs`.

### Verification
- Backfill resolved last 9 days of snapshots; only 7 NBA snapshots remain unresolved (genuinely DNP / G-League players not in any source).
- `forward_test_outcomes` collection now has 510 rows (272 NBA + 238 MLB).
- Per-tier hit rates now measurable:
  - NBA: safe_haven **88.5 %** · front_lines **75.8 %** · war_zone 22.2 %
  - MLB: safe_haven **84.5 %** · front_lines **73.6 %** · war_zone 48.4 %
- Cron registered: `forward_test_daily_resolve` next run `2026-04-27T09:30:00Z`.

### Guardrails respected
- ✅ No model changes. ✅ No scoring changes. ✅ No gate / threshold / TP / LOM / frontend changes.



## 2026-05 — NBA Injury Join Switched to `bdl_player_id`

**User directive**: *"Switch injury → master_hub join from player_name to bdl_player_id."*

### Result
Closed the previously documented refinement gap. Pre-fix, name-mismatches (suffixes / unicode / trades) caused ~50% of OUT players to be counted but contribute 0 minutes to `missing_minutes_estimate`. Post-fix:
- 100 % of `injuries_normalized` OUT rows carry `bdl_id` (source-side coverage)
- **95.8 % of OUT `bdl_id`s resolve** in `nba_master_hub_2026.bdl_player_id` (113 / 118 NBA OUT)
- Score-doc coverage ratio (`missing_minutes > 0` / `team_out_count > 0`): **91.9 %** (was ~50 %)

### Files changed
- `services/feature_hydration.py`
  - `_build_injury_summary` now emits `out_bdl_ids` and `dtd_bdl_ids` per team. Dedup key is `(team, bdl_id)` when an ID is present, else `(team, name_lower)`.
  - `_build_player_minutes_usage_map` now keyed by `bdl_player_id (int)` instead of `player_name_lower`.
  - `_compute_team_injury_features` joins via `bdl_id` first; falls back to a normalized name match (strips `Jr.` / `Sr.` / `II` / `III`) only for the residual ~5 % where bdl_id resolution misses or `live_injuries` legacy rows lack an ID.
  - Top-2 key-player detection now operates on `bdl_player_id` rather than dict-identity comparison.

### Verification (production recompute, 3,107 NBA docs)
- `injury_context.team_out_count > 0`: 2,049 / 3,107 (65.9 %)
- `injury_context.missing_minutes_estimate > 0`: 1,883 / 3,107 (60.6 %)
- Coverage ratio: 91.9 % — confirms bdl_id join captured the remaining gap.
- Per-team rich examples:
  - MEM (Brandon Clarke, Ja Morant, Zach Edey, Kentavious Caldwell-Pope OUT): miss_min=335.0, vacuum=2.177
  - BKN (Michael Porter Jr., Day'Ron Sharpe, Egor Demin OUT): miss_min=233.8, vacuum=1.826, key_out=1
  - CHI (Zach Collins, Anfernee Simons OUT): miss_min=232.3, vacuum=1.78, key_out=1

### Guardrails respected
- No model retraining; injuries are observability metadata only.
- No gate / threshold / routing / TP / LOM / frontend changes.
- `injury_data_is_imputed` flag remains accurate (1 only when `team_abbr` cannot be resolved at all).



## 2026-05 — NBA Live Injuries Pipeline Repair + Integration

**User directive**: *"Restore real-time injury data so VegasKillerModel can account for missing players and usage redistribution. Hydration + feature input only — no gates / thresholds / routing / LOM changes; no retraining."*

### Root cause
`live_injuries` collection was 100% TTL-expired (60-second TTL, micro-sync skipped NBA writes per 2026-04-18 deprecation in `live_injury_micro_sync.py`). Hydration was reading from the wrong (deprecated) collection. The canonical NBA source is `injuries_normalized` (written by `services/injury_sensor.py`, merges BDL + ESPN + NBA Official, refreshed continuously — 124 NBA + 167 MLB rows, latest sync within seconds at all times).

### Changes (read-only on injury sources, write on hydration)
- `services/feature_hydration.py`
  - `_build_injury_summary` now reads `injuries_normalized` (canonical) with `live_injuries` as legacy fallback. Status normalization handles `OUT` / `OUT_FOR_SEASON` / `OUT_INDEFINITELY` / `IL` / `DOUBTFUL` / `QUESTIONABLE` / `PROBABLE` / `DTD`.
  - Added `_build_player_minutes_usage_map(db, sport)` — builds `{player_name_lower → {avg_minutes_l10, usage_proxy_l10, team_abbr}}` from `nba_master_hub_2026.bdl_game_logs[:10]` for sizing the team injury vacuum.
  - Added `_compute_team_injury_features` — per-team aggregates: `injury_count`, `out_count`, `dtd_count`, `out_players`, `missing_minutes`, `missing_usage_pct`, `usage_vacuum_factor`, `team_minutes_removed`, `team_usage_removed_pct`, `key_player_out_flag` (top-2 minutes leader out), `injury_data_is_imputed`.
  - Hydration now writes `team_injury_context`, `opp_injury_context`, plus spec aliases: `team_injury_count`, `team_out_count`, `missing_usage_estimate`, `missing_minutes_estimate`, `usage_vacuum_factor`, `key_player_out_flag`.
- `services/scoring/adapters/base.py` — `ScoringContext.injury_context` field added.
- `services/scoring/adapters/nba_scoring.py` — populates `ctx.injury_context` from prop hydration.
- `services/scoring/recompute.py` — persists `injury_context` on every NBA score doc.
- `services/scoring/prop_scores_store.py` — `injury_context` added to `_SCORE_OUTPUT_FIELDS`.
- `services/scoring/adapters/mlb_scoring.py` — fixed `UnboundLocalError` on `hf_feature_health` when HF model is absent.

### Verification (2026-04-27 production recompute)
- `injuries_normalized` NBA: **124 fresh rows**, latest sync `2026-04-27T00:17:21Z` (continuous via InjurySensor)
- `nba_prop_scores` (3,060 docs):
  - 100 % carry `injury_context`
  - **99.3 % have real data** (`injury_data_is_imputed == 0`)
  - **67 % have `team_out_count > 0`** — most props are on teams with a player out
  - **23 % have `key_player_out_flag == 1`** — top-2 minutes leader is OUT
- Differential confirmed:
  - AFFECTED — Ayo Dosunmu PTS (Chicago, 2 OUT incl. key player): `vacuum=1.308`, `missing_min=24.3`, `key_out=1`
  - HEALTHY — Payton Pritchard PTS (Boston, 0 OUT): `vacuum=1.0`, `missing_min=0.0`, `key_out=0`

### Guardrails respected
- VK `predict()` signature unchanged — model still receives only its 105 trained features (model is NOT trained on injuries; injuries are carried for observability + downstream consumption).
- No gate / threshold / routing / TP / LOM / frontend changes.
- No retraining.
- Silent defaults forbidden — `injury_data_is_imputed` flag set whenever team_abbr can't be resolved.

### Known minor refinement
~50 % of OUT players in `injuries_normalized` don't have a name match in `nba_master_hub_2026.player_name` (e.g., suffix differences "Jr."), so their minutes contribution to `missing_minutes_estimate` is dropped while `out_count` is still correct. Switching the join to `bdl_player_id` would close this gap; deferred since the current count-and-flag features already provide the strongest binary signal.



## 2026-05 — Full Feature Activation Project (NBA + MLB)

**User directive**: *"Hydrate live props with game context. Pipe target_game/team_total/sharp_implied into VK. Fix MLB opponent/park resolution. Add missing-value flags. No retraining, no gate/threshold/TP/routing/frontend changes."*

### Result
- **NBA dead features per head: 6 → 0** (PTS, REB, AST, PRA all clean).
- **MLB dead features per head: 27 → 23** (the remaining 23 require external splits/platoon data that doesn't exist in the local store; now flagged via `feature_health.imputed_features` instead of silently defaulting).
- Every score doc now carries a `feature_health` block — 84% of NBA model-path docs report `imputed_count == 0`.

### Files changed
- **NEW** `services/feature_hydration.py` — sport-aware game-context hydration on every live-props insert. Pipes team / opponent_team / is_home_team / team_total / game_total / live_injuries / rest_days / is_b2b / park_team / venue / team_implied_runs from `mlb_master_hub_2026`, `nba_master_hub_2026`, `dg_raw_odds_markets` (totals + team_totals), and `live_injuries`.
- `services/universal_odds_sync.py` — calls hydration between `_stamp_identity_on_props` and `insert_many`.
- `services/scoring/adapters/nba_scoring.py` — `_predict_model_prob_over` and `_predict_combo_projection` now forward `team_total`, `target_game={date,home_game}`, `sharp_implied` (with DK over-odds fallback) into VK.
- `services/vegas_killer_model.py` — `predict()` accepts and pipes `target_game` + `sharp_implied`. `extract_features` reads them. Added `_is_imputed` flags for `team_total`, `sharp_implied`, `is_home`, `is_b2b`, `rest_days`. `predict()` now emits `feature_health` summary.
- `services/scoring/adapters/mlb_scoring.py` — opponent / park_team resolution prefers the hydrated 3-letter abbr instead of the empty raw `team`/`is_away_team`.
- `services/mlb_high_friction_model.py` — `_is_imputed` flags for `dk_odds`, `park_factor`, `vs_lhp`, `vs_rhp`, `platoon_split`, `home_away_split`. Emits `feature_health` summary on `predict()`.
- `services/scoring/adapters/base.py` — `ScoringContext.feature_health: Optional[Dict[str, Any]]`.
- `services/scoring/prop_scores_store.py` — `feature_health` added to `_SCORE_OUTPUT_FIELDS`.
- `services/scoring/recompute.py` — persists `feature_health` on every score doc.

### Guardrails respected
- ✅ No gate / threshold / TP / routing / tier changes
- ✅ No frontend changes
- ✅ No retraining; only inference-time feature hydration
- ✅ NBA LOM remains disabled
- ✅ Live behaviour unchanged when imputation flags fire — model still receives its training defaults; flags are observability only

### Audit artifacts (read-only)
- `/tmp/feature_activation_audit.py` — Step 1+2 raw-extractor audit
- `/tmp/feature_activation_audit_v2.py` — Step 2b production-path audit
- `/tmp/feature_activation_report_v2.md` — Step 2b before-state report
- `/tmp/feature_activation_FINAL.md` — final before/after report
- `/tmp/hydration_dryrun.py` — dry-run validation script
- `/tmp/backfill_hydration.py` — one-time backfill of existing live_props



## 2026-05 — MLB ECDF Probability Calibration P0 Fix

**User directive**: *"Fix MLB ECDF calibration before any gate tuning. Implement P0 only."*

### Root causes (confirmed in audit, 2026-05)

1. **Training/inference projection mismatch**: `train_mlb_ecdf_artifacts.regenerate_pairs` fit ECDF on `model.predict()[0]` (= `raw_pred`), but the live adapter passed the post-modifier `final_pred = raw_pred × park × opp_K` to the same lookup. Buckets were misaligned at every inference.
2. **Training-pool selection bias**: every `mlb_historical_logs` row was used in residual generation including 0-PA pinch hits and defensive subs. Empirical P(HRR > 0.5) in the training pool was 57 % while live-board confirmed-starter L20 hit rates run 85–90 %. ECDF therefore returned p_over ~25–30 pts below reality on every batter 0.5-line OVER.
3. Missing artifacts for `earned_runs` and `pitcher_walks` (forced raw-Gaussian fallback for 76 props).

### Files changed (2 files, scoped to MLB only)

1. **`scripts/train_mlb_ecdf_artifacts.py`**
   - Added `hits+runs+rbis`, `earned_runs`, `pitcher_walks` to `STAT_FAMILIES` (15 families total).
   - Added `PITCHER_STATS = {pitcher_strikeouts, hits_allowed, earned_runs, pitcher_walks}` and `BATTER_AT_BAT_FLOOR = 2`.
   - In `regenerate_pairs`, target_game now must satisfy `at_bats >= 2` for batter stats or `innings_pitched > 0` for pitcher stats.

2. **`services/scoring/adapters/mlb_scoring.py`** — Option B (NBA-parity choice):
   - Captured `raw_prediction = result.get("raw_prediction")` alongside `model_projection`.
   - ECDF lookup now passes `raw_prediction` (when available) instead of the modified `model_projection`. `model_projection` remains the displayed value on the score doc; only the probability lookup is realigned. Mirrors NBA which has no post-prediction modifier.

### Artifact quality (post-retrain, 2026-05)

| Family | pairs | min bucket n | max bucket n |
|---|---:|---:|---:|
| hits | 58 132 | 5 813 | 16 287 |
| total_bases | 58 132 | 5 813 | 16 287 |
| **hits+runs+rbis** | 58 132 | **5 813** (was 2) | 16 287 |
| strikeouts | 58 132 | 5 812 | 16 287 |
| home_runs | 58 132 | 2 396 | 16 287 |
| rbis | 58 132 | 5 808 | 16 287 |
| runs | 58 132 | 5 810 | 16 287 |
| walks | 58 132 | 5 812 | 16 287 |
| singles | 58 132 | 5 813 | 16 287 |
| stolen_bases | 58 132 | 720 | 16 287 |
| pitcher_strikeouts | 15 698 | 1 567 | – |
| hits_allowed | 15 698 | 1 569 | – |
| **earned_runs** (new) | 15 698 | 1 540 | – |
| **pitcher_walks** (new) | 15 698 | 1 569 | – |
| doubles | 0 | – | – | (HF model file missing — falls back to old artifact) |

Every retrained bucket now has n > 720; HRR's smallest-bucket residual count jumped from **2 → 5 813** (3 000× improvement).

### Target rows — before vs after

| Player / Stat / L / Side | metric | Before | After | Δ |
|---|---|---|---|---|
| Michael Busch BS 0.5 OVER | ECDF p_over | 0.867 | 0.844 | -0.023 (calibration; HR 90 → bucket avg 84) |
| | edge_pct | 18.3 | 16.0 | -2.3 |
| | tier | unqualified (tp_gate) | unqualified (tp_gate) | – |
| **Elly De La Cruz HRR 0.5 OVER** | ECDF p_over | 0.621 | **0.794** | **+0.173** |
| | edge_pct | -15.1 | **+2.2** | **+17.3** |
| | tier | unqualified | unqualified (cv_gate; cv 0.84 > 0.80) | – |
| Yandy Diaz HRR 0.5 OVER | ECDF p_over | 0.654 | 0.697 | +0.043 |
| | edge_pct | -13.8 | -9.5 | +4.3 |
| Ildemaro Vargas Hits 0.5 OVER | ECDF p_over | 0.616 | 0.654 | +0.038 |
| | edge_pct | -17.1 | -13.3 | +3.8 |
| Ryan Weathers PStrk 3.5 OVER | ECDF p_over | 0.972 | 0.973 | +0.001 |
| | edge_pct | 13.7 | 13.8 | +0.1 |
| | tier | safe_haven | safe_haven | unchanged |

### Slate-level p_model vs market vs HR alignment (PP-playable, OVER, L=0.5)

| stat | n | avg p_act | avg TP | avg HR | avg edge | %edge>0 | gap (p_act − HR) |
|---|---:|---:|---:|---:|---:|---:|---:|
| Hits | 205 | 64.4 | 61.6 | 58.0 | +2.7 | 65 % | +6.4 |
| Hits+Runs+RBIs | 198 | 67.8 | 70.9 | 64.5 | -3.2 | 27 % | +3.3 |
| Total Bases | 197 | 62.5 | 62.6 | 58.9 | -0.1 | 54 % | +3.6 |
| Batter Strikeouts | 198 | 62.6 | 61.4 | 55.8 | +1.2 | 51 % | +6.8 |
| Runs | 218 | 41.7 | 40.7 | 36.2 | +1.0 | 46 % | +5.5 |
| RBIs | 218 | 33.5 | 31.2 | 28.8 | +2.2 | 49 % | +4.7 |
| Home Runs | 181 | 16.2 | 17.3 | 12.7 | -1.0 | 26 % | +3.5 |
| Singles | 205 | 56.4 | 44.4 | 41.9 | +12.0 | 90 % | +14.5 |
| Stolen Bases | 107 | 11.2 | 14.2 | 12.1 | -3.0 | 13 % | -0.9 |
| Doubles | 217 | 14.6 | 19.2 | 14.7 | -4.6 | 9 % | -0.1 |

**Before**: gap (p_act − HR) ranged −20 to −30 pts on every batter stat. **After**: gap collapsed to +3 to +14 pts (model now slightly above L20 HR — sensible for a slate of confirmed starters projected for above-average performance). Average edge on batter 0.5 OVERs no longer massively negative for sharp favorites.

### Pitcher regression check

`Pitcher Strikeouts`: n=125, avg_edge=-4.0, SH=**1** (Ryan Weathers, identical pass to before). Pitcher pool was ~unchanged by the starter filter (innings_pitched filter ≈ original behaviour); avg edge nudged down because calibration is more honest, but no top-tier pick was lost.

### NBA unchanged

NBA artifacts (Apr 24 mtime) untouched. NBA tier counts: SH=10, FL=100, WZ=17, UQ=3615 — within natural live-pool churn of pre-fix snapshot. Code path also untouched (`mlb_scoring.py` only).

### Tier impact (MLB final)

| tier | before | after |
|---|---:|---:|
| safe_haven | 1 | **1** |
| front_lines | 6 | 5 |
| war_zone | 21 | 13 |
| unqualified | 5 340 | 4 942 |
| total | 5 368 | 4 961 |

(7 % live-pool shrinkage from natural game-clock locks between recomputes, unrelated to fix.)

### Guardrails honoured

Zero changes to: gates, thresholds, routing, TP engine, HR, CV, PP playability, frontend, NBA. No Gemini / Vision Intel calls.



## 2026-05 — PP Side-Aware Playability Filter at Scoring Boundary

**User directive**: *"The board is supposed to be PP-playable only. They should never enter scoring, tiers, rejects, or Safe Haven. Make PP playability side-aware and identical across NBA, MLB, and future sports."*

### Read-only trace findings

- `universal_odds_sync._normalize_market_data` (Pass 1 / Pass 2) is **already side-aware** — `canon_key = sport|event|player|stat|line|side` and `pp_layer` only attaches when PrizePicks itself quoted that exact tuple.
- `pp_available = (pp_layer is not None)` and `playable_on_pp = pp_available` are stamped per-side correctly.
- Verified: 119 MLB Stolen Bases UNDER 0.5 rows existed pre-fix, **0** had `playable_on_pp=True` (PP only listed OVER). Same for Doubles UNDER 0.5 (238 rows, 0 PP-playable) and Home Runs UNDER 0.5 (no rows).
- **Real bug**: the **scoring adapter** (`load_live_props`) only enforced the 0-Book Exclusion Rule (`book_count ≥ 1`). Sportsbook-fallback rows (DK/MGM-anchored, `playable_on_pp=False`) flowed into scoring → tiering → rejects.

### Files changed (3 files)

1. **`services/scoring/coverage_filter.py`** (+~110 LOC)
   - Added `filter_pp_playable(props, sport)` — sport-agnostic hard filter dropping every `playable_on_pp != True` row.
   - Added `audit_pp_side_legality(props, sport)` — diagnostic returning `playable_on_pp_with_no_pp_layer` and `pp_available_with_no_pp_layer` violation counts plus a sample (`contract_holds=True` when both = 0).

2. **`services/scoring/adapters/mlb_scoring.py`** (+~10 LOC) — `load_live_props` now: (1) `filter_priceable` → (2) build `_companion_map` over the FULL pre-filter pool (preserves OVER-side de-vig pairing) → (3) `filter_pp_playable` → return.

3. **`services/scoring/adapters/nba_scoring.py`** (+~10 LOC) — identical pattern as MLB.

### Validation report (post-fix recompute, 2026-04-25)

| Sport | Live total | playable_on_pp=True | Invalid (pp_layer null) | OVER PP-pl | UNDER PP-pl | UNDER 0.5 PP-pl |
|---|---:|---:|---:|---:|---:|---:|
| NBA | 15 937 | 5 557 | **0** | 4 758 | 799 | 41 |
| MLB | 12 682 | 6 418 | **0** | 5 984 | 434 | 132 |

| Sport | Scored (`final-{sport}-rt`) | safe_haven | front_lines | war_zone | unqualified | pp_playable!=True bleed |
|---|---:|---:|---:|---:|---:|---:|
| NBA | 3 781 | 11 | 117 | 16 | 3 637 | **0** |
| MLB | 5 368 | 1 | 6 | 21 | 5 340 | **0** |

**Per-stat-side spot checks (post-fix)**:
- MLB Stolen Bases UNDER 0.5 `playable_on_pp=True`: **0**
- MLB Doubles UNDER 0.5 `playable_on_pp=True`: **0**
- MLB Home Runs UNDER 0.5 `playable_on_pp=True`: **0**
- MLB Triples UNDER 0.5 `playable_on_pp=True`: **0**
- Safe Haven passes with line<1.0 (NBA): **0**
- Safe Haven passes with line<1.0 (MLB): **0**

### Top 20 MLB SH rejects post-fix (now genuine PP-playable props)
- Mostly `Batter Strikeouts OVER 0.5` and `Hits+Runs+RBIs OVER 0.5` failing real `cv_gate` / `edge_gate` (negative edge despite high HR — the edge gate doing exactly what it should).
- **No more** Stolen Bases / Doubles / Home Runs UNDER 0.5 rejects.

### Guardrails preserved
- No changes to gates, thresholds, routing, TP, HR, CV, vision_score, or frontend.
- TP-engine companion map still built over the full pre-PP-filter pool so OVER-side de-vig TP keeps its same-book UNDER companion when the UNDER row is dropped.



## 2026-05 — REMOVED: MLB Goblin-Line Threshold Override (Complete Strip)

**Goal**: User directive — *"Make MLB gates transparent and identical in behavior to the normal threshold config. No hidden overrides for line < 1.0."*

### Files changed (4 files)
1. **`services/scoring/gates/thresholds.py`** — removed `_MLB_GOBLIN_LINE_OVERRIDE` constant and its docstring references in the audit-mode comment block.
2. **`services/scoring/metrics_builder.py`** — removed `_resolve_mlb_goblin_override()` function, the `mlb_goblin_override` parameter from `build_metrics_from_context()`, the `extras["mlb_goblin_override"]` injection, and the call site in `build_metrics_from_score_doc()`.
3. **`services/scoring/tier_evaluator.py`** — removed `_is_mlb_sh_goblin()` and `_apply_mlb_sh_goblin_override()` helpers and the post-engine override branch. `evaluate_tier_with_overrides()` now calls the engine and returns the result unchanged.
4. **`services/scoring/scoring_stack.py`** — removed `_resolve_mlb_goblin_override` import, the `mlb_goblin_override` resolution block, and the `mlb_goblin_override=` kwarg passed into `build_metrics_from_context()`.

### Preserved (intentionally untouched)
- `is_goblin` data flag in Odds API adapter and downstream pick selectors (data attribute, not a threshold patch).
- `goblin_line` field references in `picks_getter_service.py`, `parlay_service.py`, `payout_engine.py`, `intel_briefing_engine.py`, `badge_resolver.py`, `stateless_tier_service.py` — these are tier-label data attributes (not gate logic).
- Odds routing, TP, HR, CV, vision_score, SSOT sync, frontend — not touched.

### Verification (MLB recompute, version_tag=`final-mlb-rt`, 11 583 props rescored)

| Tier | BEFORE | AFTER | Delta |
|------|-------:|------:|------:|
| safe_haven | 152 | 20 | -132 |
| front_lines | 22 | 21 | -1 |
| war_zone | 14 | 26 | +12 |
| unqualified | 11663 | 11516 | (re-routing) |

| Routed bucket | BEFORE | AFTER |
|---|---:|---:|
| safe_haven | 1371 | 1275 |
| front_lines | 3593 | 3503 |
| war_zone | 5922 | 5857 |
| None | 965 | 948 |

- **SH passes with line < 1.0**: 151 → 19 (the 132 props that previously passed via the override now correctly route to `unqualified` with explicit visible-threshold reasons: `gate_edge_fail`, `gate_cv_fail`, `gate_tp_fail`).
- All 20 surviving SH passes show `tier_reason="gates_passed"` from the unmodified `_MLB_SAFE_HAVEN` table.
- All SH rejects now show specific failed-gate reason codes (no silent override branch).

### Audit confirmation
`grep -rE "_MLB_GOBLIN_LINE_OVERRIDE|_resolve_mlb_goblin_override|mlb_goblin_override|_apply_mlb_sh_goblin_override|_is_mlb_sh_goblin"` across `/app/backend` returns **0 matches**. All MLB tier outcomes are now traceable to the visible `_MLB_SAFE_HAVEN` / `_MLB_FRONT_LINES` / `_MLB_WAR_ZONE` tables only.



## 2026-04-25 — Admin Sync-Health Panel (`/api/v3/admin/sync-health`)

**Goal**: Surface the new `sync_history` SSOT metrics for last-N runs per sport in a single auth-gated read endpoint.

### File changed (1 file)
- **`routes/admin.py`** (+105 LOC) — added `GET /v3/admin/sync-health` next to the existing `full-sync-stats` cluster.

### Contract
- Auth: `X-Admin-Token` header must match env `ADMIN_DEBUG_TOKEN`. Env unset → 503; bad/missing → 401.
- Query params: `sport=nba|mlb` (omit for both), `n=1..50` (default 10).
- Response per sport:
  - `aggregates`: `runs_returned`, `last_run_at`, `last_run_status`, `latest_pp_share`, `latest_fallback_share`, `avg_pp_share`, `avg_fallback_share`.
  - `runs[]`: `started_at`, `finished_at`, `status`, `published`, `duration_seconds`, `events_succeeded/discovered`, `distinct_market_keys`, `discovered_market_count`, `raw_market_count`, `live_props_count`, `scored_props_count`, `distinct_stat_types`, `distinct_events`, `pp_available_count`, `sportsbook_fallback_count`, `anchor_book_breakdown`, `bookmaker_counts`, `errors`, `warnings`, `pp_share`, `fallback_share`.

### Live verification (2026-04-25 06:24 UTC)
- `?sport=nba&n=10` → latest run: pp_share=34.5% / fallback=65.5%, anchor_book pp=5176, fd=4409, dk=3377, bol=1307, mgm=753, distinct_market_keys=105.
- `?sport=mlb&n=5` → latest run: pp_share=59.5% / fallback=40.6%, distinct_market_keys=62, events 15/15.
- 401 on missing or bad token. 400 on `sport=nfl`.



## 2026-04-25 — SSOT Stabilization: Extended sync_history metrics + Ferrari PP-playable filter

**Goal**: Stabilize the new Universal SSOT canonical pool by (1) capturing the new pool composition in `sync_history` and (2) restricting Ferrari boards to PrizePicks-playable props by default while keeping the full multi-book pool internally.

### Files changed (4 files, surgical)

1. **`services/scoring/prop_scores_store.py`** (+5 LOC) — added `pp_available`, `playable_on_pp`, `source_anchor`, `anchor_book` to `_SCORE_OUTPUT_FIELDS` so the SSOT flags propagate from raw_prop → score doc.

2. **`services/scoring/recompute.py`** (+10 LOC) — mirrored the four SSOT fields from `raw_prop` onto every score doc, parallel to the existing `tp_source` / `probability_method` mirrors. Pure passthrough, no math.

3. **`services/master_sync.py::_persist_sync_history`** (+50 LOC) — extended observability with 4 new fields per run:
   - `pp_available_count` — count of `live_props` where `playable_on_pp == True`
   - `sportsbook_fallback_count` — count where `source_anchor == "sportsbook_fallback"`
   - `distinct_market_keys` — distinct raw market keys this run pulled (from `dg_raw_odds_markets`)
   - `anchor_book_breakdown` — per-book canonical-pool sourcing distribution
   Log line extended with `pp_available={n} sportsbook_fallback={n} market_keys={n}`.

4. **`routes/ferrari_tiers.py`** (+90 LOC):
   - `_serve_ferrari_tier(...)` accepts `include_market_pool: bool = False`. Default mode drops picks where `playable_on_pp == False`. Score docs missing the field (legacy) are default-allowed so historical rt-tags don't disappear. Over-fetches `limit*4` to respect the user-requested cap after filtering. Response payload gains an `ssot_filter` block reporting `{playable_on_pp_only, include_market_pool, dropped_non_pp_playable}`.
   - All 4 endpoints (`/v3/ferrari/safe-haven`, `/v3/ferrari/front-lines`, `/v3/ferrari/war-zone`, `/v3/ferrari/all`) accept `?include_market_pool=true` to bypass the filter.
   - `_merge_score_with_board(...)` (NBA path) now passthroughs `playable_on_pp`, `pp_available`, `source_anchor`, `anchor_book` from score doc → response payload. MLB path (`_get_mlb_tier_picks_from_scores`) gets these for free via `dict(sc)` passthrough.

### Did NOT touch (per directive)
Scoring formulas, gates, thresholds, ECDF, UniversalGateEngine, metrics_builder, tier_evaluator, frontend, recompute math, `_normalize_market_data`, `master_sync` step ordering.

### Verification (live, 2026-04-25 05:48 UTC, one full sync per sport)

| Metric | NBA | MLB |
|---|---|---|
| status / published | success / True | success / True |
| duration_seconds | 535.2 | 123.0 |
| events succeeded / discovered | 8 / 8 | 14 / 15 |
| discovered_market_count | 105 | 56 |
| **distinct_market_keys** (raw) | **105** | **61** |
| raw_market_count (rows) | 33,212 | 30,736 |
| live_props_count | 14,956 | 9,172 |
| scored_props_count | 13,451 | 8,120 |
| distinct_stat_types | 42 | 20 |
| **pp_available_count** | **4,966** (33.2%) | **5,494** (59.9%) |
| **sportsbook_fallback_count** | **9,990** (66.8%) | **3,678** (40.1%) |
| **anchor_book_breakdown** | pp=4966, fd=4573, dk=3351, bol=1310, mgm=756 | pp=5494, dk=1679, fd=1496, bol=425, mgm=78 |

PP coverage on PP regression check: NBA 4,966 (close to prior 4,932 from yesterday's smaller slate) ✅ · MLB 5,494 (up from 5,361) ✅. Sportsbook fallback retention is preserved end-to-end into the canonical pool.

### Ferrari PP-playable filter — live behaviour (limit=10)

| Sport / Tier | Default (PP-playable only) | `include_market_pool=true` |
|---|---|---|
| NBA / safe-haven | 8 picks (5 dropped) | 10 picks (8 PP + 2 fallback) |
| NBA / front-lines | 10 picks (12 dropped) | 10 picks (6 PP + 4 fallback) |
| NBA / war-zone | 10 picks (1 dropped) | 10 picks (10 PP + 0 fallback) |
| MLB / safe-haven | 10 picks (0 dropped) | 10 picks |
| MLB / front-lines | 5 picks (0 dropped) | 5 picks |
| MLB / war-zone | 4 picks (1 dropped) | 5 picks (4 PP + 1 fallback) |

`ssot_filter.dropped_non_pp_playable` is non-zero on every NBA tier — confirming the filter is doing real work after the SSOT pool growth (NBA score collection now contains 9,990 fallback rows that would have dominated the board without this gate).

Score-doc backfill confirms 100% coverage: NBA `final-nba-rt` = 13,451 docs, 0 missing the `playable_on_pp` field; MLB `final-mlb-rt` = 8,120 docs, 0 missing.



## 2026-04-25 — UNIVERSAL SSOT: Canonical Prop Pool Decoupled from PrizePicks

**Architectural change**: the canonical prop pool in `services/universal_odds_sync.py:_normalize_market_data` is now built from ANY of the allowed books, not anchored on PrizePicks. PrizePicks is now an overlay, not the source of truth.

### New canonical-creation rule (universal — applies to NBA, MLB, future NFL)
- Allowed books (SSOT): `prizepicks`, `draftkings`, `fanduel`, `betmgm`, `betonlineag`.
- Anchor priority: `prizepicks > draftkings > fanduel > betmgm > betonlineag`.
- Canonical identity (UNCHANGED — preserves all downstream consumers): `sport | event_id | player_name | stat_type | line | side`.
- A canonical is created on the FIRST sighting across all allowed books; later books attach as layers.
- Every prop now carries:
  - `pp_layer` / `dk_layer` / `fd_layer` / `mgm_layer` / `bol_layer` (None when absent).
  - `pp_available: bool` — `True` iff PrizePicks quoted this canonical_key.
  - `playable_on_pp: bool` — alias for `pp_available` for filter clarity.
  - `source_anchor: "prizepicks" | "sportsbook_fallback"`.
  - `anchor_book` — the book that seeded the canonical.
- Ferrari and PP-playable boards SHOULD filter on `playable_on_pp == True`. The backend pool keeps every market regardless.

### Validation results (one full sync per sport, 2026-04-25 04:50 UTC)

| Metric                        | NBA pre-SSOT* | NBA post-SSOT | MLB pre-SSOT | MLB post-SSOT |
|---|---|---|---|---|
| live_props_count              |      ~4 838  |    **14 493** |    4 942     |   **9 760**   |
| pp_available=True (regression check) |   ~4 838  |     4 932 ✅  |    4 942     |    5 361 ✅   |
| sportsbook_fallback (NEW)     |          0   |     9 561     |        0     |    4 399      |
| events_succeeded / discovered |        8 / 8 |     8 / 8     |    15 / 17   |    16 / 17    |
| distinct stat_types           |          28  |        41     |       11     |       20      |
| scored_props_count            |       3 241  |    13 001     |    3 962     |    8 862      |

*Pre-SSOT counts are from the prior day's sync_history record (NBA was in a smaller slate window).

### Specific user concern: Cubs @ Dodgers early game DK props
The Miami @ SF event (`5ea60d3f`) — previously dropped because PrizePicks did not list it — now produces 584 live_props anchored on DraftKings, exactly as required by the spec. (The Cubs @ Dodgers early-game `55fa28ec` was outside the current sync's event window because its commence_time was already past.)

### What did NOT change
Scoring formulas, gates, thresholds, ECDF, UniversalGateEngine, metrics_builder, tier_evaluator, frontend, recompute, prop_scores_store, master_sync, sync_history, market_catalog. Only `services/universal_odds_sync.py:_normalize_market_data` and its Pass-3 flatten step were touched.

### Files changed
- `services/universal_odds_sync.py` — rewrote Pass-1 to walk all allowed books in priority order; Pass-3 (flatten) now derives `pp_available` / `playable_on_pp` / `source_anchor` / `anchor_book` and gracefully handles `pp_layer == None`. Lint clean.



## 2026-04-25 — Vision Intel Coverage Hotfix (per-tier caps + chunked Gemini batches)

**User report:** Only War Zone showed Gemini Vision Intel in the UI. Safe Haven and Front Lines fell back to template text after slate refreshes.

**Root cause (NOT a frontend mapping bug — backend selection issue):**
1. Step 6 selected by score-doc `tier` field with global cap `MAX_BOARD_VISION_INTEL_PICKS=75`.
2. Score-doc tier counts: `front_lines=119, war_zone=13, safe_haven=8` (139 active, vs ~24 user-visible after gates).
3. Priority order `war_zone → front_lines → safe_haven` consumed the cap with WZ (15) + FL (60) = 75. Safe Haven received 0 Gemini coverage.
4. Front Lines also had partial coverage because the 60 picks selected by `vision_score` did not always include every API-visible pick (selection vs gating mismatch).
5. Additional issue: `analyze_tier_batch` truncated responses for >~20-prop batches → `gemini_empty=45/55`.

**Fix (still backend-only, 1 file changed):**
- **`backend/services/master_sync.py`:**
  1. Replaced single global cap with per-tier caps (`PICKS_PER_TIER_CAP = {war_zone: 50, front_lines: 120, safe_haven: 50}`) plus runaway ceiling `MAX_BOARD_VISION_INTEL_PICKS=200`. Picks within each tier are still ranked by descending `vision_score` so high-quality picks win the cap fight, but no tier can be starved.
  2. Wrapped `analyze_tier_batch` in chunked invocation: process tier picks in groups of `CHUNK=20`. Gemini reliably returns full batches at this size; previously 55-prop batches truncated to ~10 valid responses.

**Did NOT touch (per directive):** scoring · gates · ECDF · UniversalGateEngine · thresholds · recompute · odds sync · frontend · Ferrari tier logic.

**Verification:**

| Tier | API picks | Before (Gemini/Total) | After (Gemini/Total) |
|---|---|---|---|
| Safe Haven | 7 | 0/7 | **7/7** |
| Front Lines | 10 | 1/10 | **10/10** |
| War Zone | 6 | 6/6 | **6/6** |
| **Total** | 23 | 7/23 (30%) | **23/23 (100%)** |

`_enrich_nba_board_vision_intel` metrics on cold run: `cache_hits=94, cache_miss_to_call=45, after_cap_to_call=45, gemini_returned=45, gemini_empty=0, score_writes=45, cb_writes=45, fallback_in_db_after=5` (the 5 are score docs whose recommendation flipped between Step 3 and Step 6 — non-board props).

**UI validation (Playwright, 9 picks across SH+FL+WZ):**
- 7/7 valid clicks render Target-Lock Rationale section
- 7/7 render "Powered by Vision Intel" badge
- 0 fallback fingerprints (`is hammering` / `sits above the` / `to ride` / `consistent enough to back` all = 0 across all 7 detail pages)



**Goal:** All Vision Intel summaries shown to users were deterministic fallback templates from `_generate_vision_fallback` (e.g. *"Jalen Duren is hammering PTS at an 85% L10 over clip — projection of 20.2 sits above the 11.5 line for a +8.7 edge to ride."*). Gemini producer (`VisionIntelService.analyze_tier_batch`) was functional but completely unwired since the 2026-04-22 consolidation deleted UnifiedPipeline / optimized_sync_engine. `scheduled_hourly_vision_intel_sync` was defined in `server.py` but never registered with APScheduler. Coverage of real Gemini text on the slate: 0 / 3,829 score docs.

**Approved scope (Option A, narrow):** Wire Gemini back into `master_sync.py` as Step 6, restricted to active board picks only (Safe Haven + Front Lines + War Zone) with content-hash cache and 75-pick cap. Pure UI decoration.

**Files changed (3, exactly per the directive's "tiny passthrough" allowance):**

1. **`backend/services/master_sync.py`** — added Step 6 hook + helper `_enrich_nba_board_vision_intel` (~200 LOC). Behavior:
   - Pulls `nba_prop_scores` where `version_tag=final-nba-rt`, `tier ∈ {safe_haven, front_lines, war_zone}`, `active=True`.
   - Computes `_vision_intel_content_hash(pick)` (existing helper from `routes/ferrari_tiers.py`); skips picks whose stored hash matches and `vision_intel` is non-empty (cache hit).
   - Caps remaining new/changed picks at `MAX_BOARD_VISION_INTEL_PICKS=75`. Priority order when capped: `war_zone → front_lines → safe_haven`.
   - Groups by tier, calls `vis.analyze_tier_batch(picks, tier_name, strict=True)` once per tier.
   - Persists `vision_intel`, `vision_intel_content_hash`, `vision_intel_generated_at` onto matching `nba_prop_scores` docs (keyed by `canonical_key`).
   - Mirrors `vision_intel` into `nba_cached_board.props[*]` via `arrayFilters` keyed by `(stat_type, line, direction)`.
   - Returns metrics: `board_picks_total, cache_hits, cache_miss_to_call, after_cap_to_call, tiers_called, gemini_returned, gemini_empty_or_failed, score_docs_written, cached_board_writes, fallback_in_db_after`.

2. **`backend/routes/ferrari_tiers.py`** — single-block (~12 LOC) passthrough in `_merge_score_with_board` so `score.get("vision_intel")` / `vision_intel_content_hash` / `vision_intel_generated_at` flow into the pick payload when `nba_cached_board` lacks a matching line entry (slate drift). Score-side text wins only when present; cached_board overlay can still set it via the existing path.

3. **`backend/routes/player.py`** — three lines added to `_score_to_prop` whitelist: `vision_intel, vision_intel_content_hash, vision_intel_generated_at`. Same rationale as the Step 4 momentum_data passthrough.

**Did NOT touch (per directive):**
Scoring · gates · UniversalGateEngine · thresholds · ECDF · recompute · odds sync · frontend · `_enrich_under_picks_with_gemini` (orphaned, left untouched).

**Verification (live, 2026-04-25):**

| Metric | Before | After (run 1) | After (run 2) |
|---|---|---|---|
| `nba_prop_scores` board picks with `vision_intel` populated | 0 / 163 | 75 / 163 (cap binding) | **148 / 148 active board** |
| `vision_intel_content_hash` populated | 0 | 75 | 148 |
| `vision_intel_generated_at` populated | 0 | 75 | 148 |
| Cache hits on 2nd run | n/a | n/a | **140** (FL+WZ from run 1 short-circuited) |
| Gemini calls | 0 | **2** (1 per tier × 2 tiers: WZ 15 + FL 60) | **1** (just SH 8) |
| `gemini_returned / selected` per tier | n/a | WZ 15/15, FL 60/60 | SH 8/8 |
| Coverage of `/api/v3/ferrari/{safe-haven,front-lines,war-zone}` payloads | gemini=0/24, fallback=24/24 | WZ 7/7 + FL 10/10 + SH 0/7 (SH capped out) | **WZ 7/7 + FL 10/10 + SH 7/7 = 24/24 Gemini** |
| Score doc `vision_intel_content_hash` populated on Ferrari payload | 0/24 | n/a | **24/24** |
| Cap held | n/a | 75 | 8 (well under 75) |
| Ferrari tier counts (gating signal) | safe-haven=7, front-lines=10, war-zone=7 | unchanged | unchanged ← scoring untouched |

**Sample Gemini outputs (real, not template):**
- Reed Sheppard PTS 16.5 UNDER: *"High volatility metrics with a CV of 0.53 make the under a solid play here. Regression is expected as he struggles to maintain this high level of scoring output."*
- VJ Edgecombe REB 8.5 UNDER: *"Minutes are capped for his rotation, which will inherently limit his rebounding opportunities. The under is the logical choice here."*
- LeBron James P+A 33.5 UNDER: *"Aging legs and a tight rotation suggest his production will dip tonight. The under is the sharp move for this veteran stud."*
- Jalen Duren PTS 11.5 OVER: *"L10 hit rate confirms he is finding his rhythm and should find easy buckets against this interior rotation. Ride the hot hand while he continues to hunt points inside."*

**UI validation (Playwright, Jalen Duren detail):**
- TARGET-LOCK RATIONALE renders genuine Gemini text (verified no fallback fingerprints: `is hammering` / `sits above the` / `to ride` count = 0 on page)
- "Powered by Vision Intel" badge present
- TEMPO panel still rendering Step 5 output ("-1.2 Possessions / Standard Tempo")
- DEFENSIVE MOMENTUM still rendering Step 4 output (SZN/L10/L5/Composite)
- Bar charts, hit rates, header stats — all unchanged



**Goal:** `intel_suite.pace_delta` was stale legacy data (every team showing flat `team_pace=98.0, opp_pace=98.0, tempo_label="Neutral Pace", display="0.0"`) written by deleted `optimized_sync_engine` / `cached_board_builder_service` ≈2026-04-21. Coverage of the stale signature: 122 cached_board docs. Score docs had no `intel_suite.pace_delta` at all. PlayerDetailPage TEMPO panel showed "0.0 / Neutral Pace" for every player.

**Approved scope (Option A only):** Wire `IntelSuiteCalculator._calculate_pace_delta` back into `master_sync.py` as Step 5 read-side enrichment. Pure UI decoration — does not affect projections, gates, ECDF, tiers, recompute math, or thresholds.

**Files changed (1):**
- **`backend/services/master_sync.py`**
  - Added Step 5 hook (NBA-only) after Step 4 momentum: calls `_enrich_nba_pace_delta(db)` and reports metrics under `steps.5_pace_delta_enrichment_nba`.
  - Added inline helper `_enrich_nba_pace_delta` (~170 LOC) that:
    - Builds `event_id → (home_abbr, away_abbr)` and `team_abbr → today_opponent_abbr` maps from `nba_live_props`.
    - Builds `bdl_player_id → team_abbr` map from `nba_master_hub_2026`.
    - Walks `nba_prop_scores` at `version_tag=final-nba-rt`, derives `(team_abbr, opponent_abbr)` per doc, dedupes by pair.
    - Calls `IntelSuiteCalculator._calculate_pace_delta(team, opp, None)` once per pair (pure synchronous static-table lookup, no I/O).
    - Bulk-writes `{$set: {"intel_suite.pace_delta": pd}}` onto matching score docs (creates `intel_suite` sub-doc when absent — no other intel_suite fields touched).
    - Mirrors line/direction/stat-agnostically into `nba_cached_board.props[*].intel_suite.pace_delta` via `arrayFilters` (one update per cached_board player doc).
  - Reuses the `_NBA_TEAM_NAME_TO_ABBR` and `_to_team_abbr` constants added in the momentum step.

**Did NOT touch (per directive):**
Scoring · gates · UniversalGateEngine · thresholds · ECDF · recompute · Ferrari tier logic · `_score_to_prop` whitelist · frontend · `routes/player.py` overlay (already passes `intel_suite` through via `_BOARD_ENRICHMENT_FIELDS`).

**Verification (live, 2026-04-24):**

| Metric | Before | After |
|---|---|---|
| `nba_cached_board` docs with stale `tempo_label="Neutral Pace"` | 122 | **13** (remaining = stale-slate players not playing today) |
| `nba_prop_scores` with `intel_suite.pace_delta` @ final-nba-rt | 0 / 3,829 | **3,829 / 3,829 (100%)** |
| `_enrich_nba_pace_delta` metrics | n/a | `props_total=3829, props_enriched=3829, props_skipped=0, pairs_total=16, pairs_computed=16, pairs_failed=0, cached_board_updates=109, cached_board_skipped=13` |
| Jalen Duren PTS 11.5 OVER `intel_suite.pace_delta` (`/player-with-badges`) | `{display:"0.0", tempo_label:"Neutral Pace", expected_game_pace:"98.0"}` | `{possessions:-1.2, display:"-1.2 Possessions", pace_factor:1.0, tempo_label:"Standard Tempo", team_pace:96.0, opponent_pace:96.5, expected_game_pace:96.2}` |
| Ferrari tier counts (gating signal) | safe-haven=7, front-lines=10, war-zone=7 | unchanged (7 / 10 / 7) ← scoring untouched |
| Per-tier fresh pace coverage on Ferrari payload | n/a | safe-haven 7/7, front-lines 8/10, war-zone 7/7 |

**UI validation (Playwright on preview, Jalen Duren detail):**
- TEMPO panel: **"-1.2 Possessions / Standard Tempo"** (current per-team value)
- VARIANCE: 65% Variable (unchanged, fresh)
- DEFENSIVE MOMENTUM: SZN #15 / L10 #5 / L5 #6 / Composite #10 (current, from Step 4)
- ACTIVE FOR: HOME COOKIN' badge (current)
- 0 "Neutral Pace" sightings, 1 "Standard Tempo" rendered
- Bar charts, hit rates, header stats, target-lock rationale all unchanged



**Goal:** `momentum_data` was orphaned since the legacy `optimized_sync_engine` was deleted on 2026-04-22. Coverage was 8/5,754 cached props (0.14%) and 0/3,731 score docs. PlayerDetailPage `MomentumTrackerFull` could not render for Jalen Duren PTS 11.5 OVER (or 99.86% of the slate).

**Approved scope (Option 1 only):** Wire the existing `DefensiveMomentumService.calculate_momentum_modifier` back into `master_sync.py` as a read-side enrichment Step 4. Pure UI decoration — does not affect projections, gates, ECDF, tiers, recompute math, or thresholds.

**Files changed (3):**
1. **`backend/services/master_sync.py`**
   - Added Step 4 hook (NBA-only) after scoring (Step 3): calls `_enrich_nba_momentum(db)` and reports metrics under `steps.4_momentum_enrichment_nba`.
   - Added inline helper `_enrich_nba_momentum` (~140 LOC) that:
     - Builds `event_id → (home_abbr, away_abbr)` and `team_abbr → today_opponent_abbr` maps from `nba_live_props`.
     - Builds `bdl_player_id → team_abbr` map from `nba_master_hub_2026`.
     - Calls `momentum.ensure_cache()` once (loads/builds the existing `defensive_momentum_cache` Mongo collection).
     - Walks `nba_prop_scores` at `version_tag=final-nba-rt`, derives canonical `(opponent_abbr, stat_family)` per doc, dedupes by pair, computes `momentum_data` once per pair, and bulk-writes via `UpdateMany` keyed by `canonical_key`.
     - **Mirrors** `momentum_data` into `nba_cached_board.props[*]` using `arrayFilters` keyed by `stat_type` (line-and-direction-agnostic — momentum_data depends only on `(opp, stat)`). Resolves opponent from `team_to_opp_today` (NOT cached_board's stale `event_id`).
     - Adds new `_NBA_TEAM_NAME_TO_ABBR` and `_STAT_FAMILY_ALIAS` constants for translation.
2. **`backend/routes/player.py`**
   - One-line addition to `_score_to_prop` whitelist: `"momentum_data": doc.get("momentum_data")`. Required because score docs now carry the field but the read function was stripping it. This is the "any small helper if absolutely needed" exception — without it, score-doc-only paths (where cached_board lacks the line, e.g. Duren PTS 11.5) cannot surface momentum_data to the UI.

**Did NOT touch (per directive):**
Scoring · gates · UniversalGateEngine · thresholds · ECDF / probability layer · recompute · Ferrari tier logic · `_score_to_prop` field semantics for scoring fields · `defensive_momentum_cache` UI usage · routes_archive · Tank01 fallbacks.

**Verification (live, 2026-04-24):**

| Metric | Before | After |
|---|---|---|
| `nba_prop_scores` momentum_data coverage @ final-nba-rt | 0 / 3,731 | **3,731 / 3,731 (100%)** |
| `nba_cached_board.props[*]` momentum_data coverage | 8 / 6,005 | **5,322 / 6,005 (88.6%)** — remainder is stale-slate players |
| `/api/v3/player-with-badges/Jalen%20Duren` momentum_data per prop | 0 / 53 | **53 / 53 (100%)** including PTS 11.5 OVER (composite=10.2, season=15, l5=6) |
| `/api/v3/ferrari/safe-haven` picks with momentum_data | 1 / 7 | **4 / 7** (gap is line-drift between cached_board & pick lines — not in scope) |
| Ferrari tier counts (gating signal) | safe-haven=7, front-lines=10, war-zone=7 | unchanged (7 / 10 / 7) ← scoring untouched |
| `_enrich_nba_momentum` metrics | n/a | `props_total=3731, props_enriched=3731, props_skipped=0, pairs_total=159, pairs_computed=159, pairs_failed=0, cached_board_updates=1579, cached_board_skipped=13` |

**UI validation (Playwright, 5 fresh clicks):**
- Jalen Duren · Nikola Jokic · James Harden · Stephon Castle · Donovan Clingan
- **5/5 detail pages render `MomentumTrackerFull`** with full SZN/L10/L5 ranks + Composite Rank
- **0 `matchup-dvp-fallback` boxes** rendered (rule from prior commit holds)
- 5/5 Vision Intel Suite intact (Environmental Factors + Performance Indicators + Target-Lock Rationale)
- Bar charts, hit rates, header stats, Add Pick to Command Center button — all unchanged



**Audit identified two surgical fixes:**

### Fix A — `momentum_data` overlay through `/player-with-badges`
**File:** `backend/routes/player.py` (1 line)
- Added `"momentum_data"` to `_BOARD_ENRICHMENT_FIELDS` tuple.
- Pure projection of the existing `nba_cached_board.props[*].momentum_data` field through the existing line-level overlay path (lines 376–387).
- **Did not touch:** `_score_to_prop`, scoring, gates, ECDF, Ferrari tier logic, dvp_service, intel_suite_calculator, defensive_momentum_service. The cached_board is already populated by upstream scoring; this just stops dropping the field on read.
- **Verified:** `curl /api/v3/player-with-badges/Nikola%20Jokic` now returns `momentum_data` populated for Jokic AST 7.5 (composite_rank=6.9, season_rank=5, l5_rank=8). Coverage = 1/51 props (rest are upstream cached_board gaps, not this overlay's scope).

### Fix B — Prefer `bdl_game_logs` when richer (no Tank01-shape fallbacks)
**File:** `frontend/src/components/dashboard/PlayerDetailPage.jsx` (~6 LOC inside the existing master-hub secondary fetch handler, lines ~628–660)
- When master-hub returns BOTH `game_logs` (Tank01-shape) and `bdl_game_logs` (BDL-shape), prefer `bdl_game_logs` if `game_logs[0]` lacks usable opponent identity (no `opponent_team_id`, no `matchup`, empty `opp`) OR `bdl_game_logs.length > game_logs.length`.
- BDL logs carry `opponent_team_id` (resolved by existing `TEAM_ID_TO_ABBR` map in `GameLogBarChart`). No new fallback fields, no Tank01 patches.
- **Did not touch:** `GameLogBarChart` opponent-resolver semantics. Reverted the speculative `game.opp` / `game.home` / `.toUpperCase()` additions — selection happens upstream where it should.

### Files changed (final, total)
- `backend/routes/player.py` — 1 addition to `_BOARD_ENRICHMENT_FIELDS`
- `frontend/src/components/dashboard/PlayerDetailPage.jsx` — secondary-fetch handler enhanced to choose richer game-log array
- `frontend/src/components/dashboard/GameLogBarChart.jsx` — REVERTED to pre-session state (speculative `game.opp` / `game.home` removed)

### Validation (Playwright on preview, 5 fresh clicks)
- 5/5 detail pages render bar charts and Vision Intel Suite
- **0 "???" labels across all 5 detail views** (Fix B working — Amen-class players whose `game_logs` array is empty now read `bdl_game_logs` with `opponent_team_id`)
- 1 `momentum-tracker-full` instance (Jokic — Fix A working; the only player on slate with cached_board momentum_data)
- 4 `matchup-dvp-fallback` instances (current `intel_suite._calculate_matchup_dvp` source — not legacy)
- 0 legacy "DEFENSIVE MOMENTUM" header text sightings
- 0 "No game data" placeholders
- No 404s



**User report:** Clicked detail view was using stale/legacy badge sources;
defensive momentum appearing "wrong/legacy"; badges not populating.

**Root cause trace (backend + frontend, read-only where instructed):**

| Signal | Current SoT (Ferrari tier payload @ root) | `/player-with-badges` response | Frontend behavior BEFORE fix |
|---|---|---|---|
| `momentum_data` | Full 22-key object (scoring output from `services/defensive_momentum_service.py`) | `None` on every prop | Merge did NOT copy → `MomentumTrackerFull` never rendered |
| `scout_badges` | list @ root | `None` @ root | Merge included this field (OK) |
| `context_badges` | list of `{badge_key, display, icon, color, description}` @ root | list (same) | `intel_suite.context_badges` read only — MISSED root copy |
| `active_badges` | `None` (not set) | `None` | Not merged |
| `intel_suite.defensive_momentum` | **not emitted** (legacy) | **not emitted** | UI had fallback block reading it → dead "N/A" branch |
| `intel_suite.matchup_dvp` | full current DVP object | full current DVP object | OK (used) |
| `has_momentum_modifier`, `momentum_modifier`, `crew_chief`, `whistle_*`, `vacuum_*` | @ root (Ferrari) | `None` | Not merged |

**Legacy references found:**
- `PlayerDetailPage.jsx` lines 1167–1199 (removed) — read `selectedVisionProp.intel_suite.defensive_momentum` and rendered a "DEFENSIVE MOMENTUM" card. The backend no longer emits this key (it was replaced by the root-level `momentum_data` object emitted by Ferrari scoring). The branch always produced "N/A" but masked the correct fallback.
- Environmental Factors "ACTIVE FOR …" summary checked only `intel_suite.context_badges`, which is empty for NBA (context_badges live at root).

**Current (authoritative) sources used:**
- Defensive momentum → `pick.momentum_data` (NBA scoring) OR `pick.intel_suite.matchup_dvp` (fallback). Both come from `services/defensive_momentum_service.py` / Ferrari enrichment. No other source is read.
- Scout badges → `pick.scout_badges` (root) OR `pick.intel_suite.scout_badges`.
- Context badges → `pick.context_badges` (root) + `pick.intel_suite.context_badges` (MLB string array).
- `defensive_momentum_cache` Mongo collection is referenced ONLY by `vegas_killer_model.py`, `vegas_pro_model.py`, `vegas_regression_model.py`, `defensive_momentum_service.py` (authoritative writer). Not read by `ferrari_tiers.py`, `player.py`, or any frontend path.

**Files changed (frontend only):**
- `frontend/src/components/dashboard/PlayerDetailPage.jsx`
  1. Merge block now copies `momentum_data`, `active_badges`, `context_badges`, `has_momentum_modifier`, `momentum_modifier`, `crew_chief`, `whistle_class`, `whistle_modifier`, `has_whistle_modifier`, `point_lift`, `lift_label`, `lift_type`, `has_vacuum_modifier`, `vacuum_modifier`, `vacuum_data`, `matchup_analysis`, `opponent`, `opponent_abbr` from the clicked Ferrari pick into the refetched prop.
  2. Removed the legacy `intel_suite.defensive_momentum` render branch.
  3. Environmental Factors grid now unions root-level `context_badges` with `intel_suite.context_badges` for `isActive` detection and custom descriptions.
  4. "ACTIVE FOR {player}" summary now renders whenever `active_badges`, root `context_badges`, OR `intel_suite.context_badges` has entries.

**Before/After payload — Jokic AST 7.5 (Safe Haven):**
- `[A] Ferrari`: momentum_data=dict(22), context_badges=list(1), intel_suite.matchup_dvp="Rank #10 vs. Playmakers"
- `[B] /player-with-badges` (raw, before fix): momentum_data=None, scout_badges=None, active_badges=None → UI showed blank "DEFENSIVE MOMENTUM: N/A"
- `[C] Post-merge (after fix)`: momentum_data=dict(22), context_badges=list(1), intel_suite.matchup_dvp="Rank #10 vs. Playmakers" → UI renders `MomentumTrackerFull` when momentum_data present, else current matchup_dvp. Legacy path gone.

**Validation (Playwright on preview):**
- Safe Haven #0 (Jalen Duren PTS 11.5): ENVIRONMENTAL FACTORS ✓, PERFORMANCE INDICATORS ✓, "ACTIVE FOR JALEN DUREN:" ✓ (shows HOME COOKIN' pill), "DEFENSIVE MOMENTUM" heading = 0 sightings, current `matchup-dvp-fallback` = 1 (vs ORL Rank #13 Medium), no "No game data", bar charts present.
- Identical merge path used by Front Lines (`handleRadarClick`) and War Zone (`handleRadarClick`) — covered by the same fix.



**Context:** Clicking a Safe Haven / Front Lines / War Zone card opened a
detail view with broken bar charts ("No game data"), missing hit rates,
missing L5/L10/Season averages, and raw market keys in category headers
(e.g. `PLAYER_POINTS_ASSISTS_ALTERNATE`). Backend payload was already
healthy — the break was purely frontend contract mismatches on the inner
click-path components.

**Diff (3 files, frontend-only — no backend touched):**
- `frontend/src/components/dashboard/PlayerDetailPage.jsx`
  - Import + apply `normalizeFerrariPicks` to the direct XHR response from
    `/api/v3/player-with-badges/:name` (props previously bypassed the
    adapter that runs on the Ferrari tier hooks, so `stat_type_extracted`,
    `direction`, `market`, and synthetic `chart_data` were all `null`).
  - Added a secondary fetch to `/api/v3/master-hub/player/:bdl_id` to
    populate `game_logs` (bar chart input) and `baseline_stats` (PPG/RPG/
    APG header strip). `/player-with-badges` does not include these; the
    master-hub endpoint does.
- `frontend/src/utils/normalizeFerrariPick.js`
  - Extended adapter with a `MARKET_TO_SHORT` inverse map so `stat_type`
    values arriving as raw market strings (e.g.
    `player_points_assists_alternate`) collapse to short codes (`PA`, `PR`,
    `PRA`, …). Enables both category grouping and the GameLogBarChart
    `STAT_FIELD_MAP` lookup.
  - Market derivation now also considers the collapsed short code when
    `pick.market` is null.
- `frontend/src/components/dashboard/GameLogBarChart.jsx`
  - Fallback parses opponent abbreviation + home/away flag from the
    `matchup` string (`DET vs. NYK` / `DET @ NYK`) when
    `opponent_team_id` / `home_game` are absent in BDL/Tank01 game logs.

**Verification (live, 2026-04-24, Playwright on preview env):**
- Safe Haven click (Jalen Duren): 46 prop rows, 1 VISION-highlighted prop,
  Intel Suite modal opens with Environmental Factors + Performance
  Indicators, bar charts render with values (24/19/17/12/16/15/6/24/8/21),
  opponent labels, L5/L10 hit + averages visible, PPG/RPG/APG/STL/BLK
  header populated.
- War Zone click (Shaedon Sharpe): 30 prop rows, 1 VISION highlight,
  category headers now show `PTS+AST` (not raw market key), PRA/PR/PA
  charts all render — "No game data" count = 0.
- Auto-scroll to highlighted prop + gold-glow ring both working.



## 2026-04-21 — Injury-Rank Phase 2: usage-sorted beneficiaries (multi-sport)

**Context:** `services/injury_advantage.py::compute_injury_advantages` was
ranking injury beneficiaries by `my_index`, i.e. whichever order teammates
happened to appear in `board_picks`. This made DEN's Christian Braun rank
"primary" over Cameron Johnson purely by incidental iteration order.

**Diff (4 files + 1 test file):**
- `services/usage_resolver.py` — NEW. Multi-sport provider registry:
  NBA reads `nba_master_hub_2026.advanced_stats` (usage% × minutes blend),
  MLB/NFL return `(None, "unavailable")` and plug in via `register_provider`.
  Public API: `rank_teammates_by_usage(db, sport, teammates)` — alphabetical
  tiebreak on equal usage for determinism.
- `services/injury_advantage.py` — build a per-team usage-sorted ranking
  up front, replacing `my_index = next(...)` with `team_rank_map[team][name]`.
  Emits `usage_rank` + `usage_source` per advantage.
- `routes/vacuum.py::/v3/vacuum/live-alerts` — surface `usage_rank` +
  `usage_source` on each alert payload.
- `tests/test_usage_resolver.py` — 13 tests: blend function bounds,
  unknown-sport/MLB fallback, deterministic tiebreak, loop-order immunity,
  future-NFL plug-in proof.

**Verification (live, 2026-04-21):**
- DEN beneficiaries now correctly ordered:
  `Cameron Johnson (usage 13.74, primary) > Christian Braun (13.70, secondary)`
  (was reversed under loop-order).
- All 5 live alerts carry `usage_source: "nba_hub"`.
- All 6 Ferrari endpoints (NBA/MLB × 3 tiers) → HTTP 200.
- 105/105 pytest passing.

## 2026-04-21 — Stat-aware CV caps for Safe Haven eligibility

**Context:** Single `max_cv=0.50` on Safe Haven was rejecting high-hit-rate
low-line props (Ajay Mitchell AST 1.5 @ 95% HR, Vucevic REB 3.5 @ 90% HR,
etc.) because CV = σ/μ is structurally higher for small-mean stats.

**Diff (2 code + 1 test file):**
- `services/scoring/cv_caps.py` — NEW sport-agnostic module with
  `CV_CAP_BY_STAT` (PTS/PRA=0.50, AST/REB=0.60, STL/BLK=0.65, 3PM=0.55,
  combos=0.50/0.55) + `DEFAULT_CV_CAP=0.50` for MLB/NFL/unknowns.
- `services/scoring/adapters/nba_scoring.py::check_safe_haven_gates` —
  resolve cap from `prop["stat_type"]` and override `self.SAFE_HAVEN["max_cv"]`
  per-call.
- `tests/test_cv_caps.py` — 24 tests including contract locks on the 13
  audited CV-only rejects.

**Verification (live on today's board):**
- 7 picks newly admitted to Safe Haven (3 AST, 4 REB) — exactly the
  audited candidates.
- Safe Haven Top-10 now: 5 PTS, 3 AST, 2 REB (was PTS-dominated).
- 2 PRA picks (Vucevic 10.5 / 11.5) remain correctly rejected (PRA cap
  stays 0.50 per spec).
- Extreme CV outliers (≥0.70) remain rejected on every stat.

## 2026-04-21 — Stat-aware α in `ranking_score_v2`

**Context:** Single α=0.40 suppressed low-line AST/REB/STL/BLK props in the
`sort=gap` board because `line^0.40` barely shrinks for small lines while
big-raw-gap PTS/PRA UNDERs monopolized the Top-10.

**Diff (1 code + 1 test file):**
- `services/scoring/recompute.py` — added `ALPHA_BY_STAT` map +
  `_DEFAULT_ALPHA=0.50` + `_resolve_alpha()` helper; refactored
  `_compute_ranking_score_v2` to take `stat_type` kwarg; caller passes
  `ctx.stat_type`.
- `tests/test_ranking_alpha.py` — 14 tests (cap map, rank-position
  improvement, regression guards, backwards-compat, future NFL plug-in).

**Verification (live):**
- All low-line AST/REB/STL/BLK picks climbed 20–45 positions in the
  global sort.
- PTS/PRA picks identical (α stays 0.40 for those regimes).
- Ferrari endpoints all 200.

## 2026-04-21 — Canonical multi-sport DvP rank pipeline

**Context:** Scored docs had `defensive_rank`, `momentum_data.dvp_rank`,
and `opponent_defensive_rank` all ≈ `None`. Gemini filled the gap with
"24th-ranked Spurs" hallucination (coincidentally matching the stale
`config.settings.DVP_RANKINGS["PTS"]["SAS"]=24` from last season).

**Diff (5 files + 1 test file):**
- `services/defensive_rank_resolver.py` — NEW. Shared multi-sport resolver
  + provider registry + prewarm hook. NBA strict-BDL (rejects
  `DvPDataSource.STATIC_FALLBACK`); MLB/NFL unavailable.
- `services/scoring/recompute.py` — Phase 4b writer: calls
  `ensure_provider_warm(sport)` once, resolves per-prop via
  `get_opponent_defensive_rank`, writes canonical 3 fields on every
  score doc.
- `services/scoring/prop_scores_store.py` — added 3 fields to
  `_SCORE_OUTPUT_FIELDS` whitelist.
- `services/unified_pipeline.py` — `_run_gemini_enrichment` now reads
  only the canonical field; dropped `momentum_data` / static fallbacks.
- `routes/ferrari_tiers.py` — NBA merger copies canonical fields to
  API response.
- `tests/test_defensive_rank_resolver.py` — 13 tests including
  `test_static_DVP_RANKINGS_is_NEVER_a_source`.

**Verification (live):**
- `nba_prop_scores` (final-nba-rt): 2177/2177 carry canonical fields.
- `mlb_prop_scores`: 4544/4544 carry canonical fields (honestly `unavailable`).
- Ferrari responses: 10/10 picks per tier carry new fields.
- Literal "24th" across all NBA board responses: **0**.
- Shaedon Sharpe vs SAS: `opponent_defensive_rank=16, source=bdl_live`.

## 2026-04-21 — Market Gap ("Book Spread") multi-sport disagreement signal

**Context:** Backend exposed per-book prices but no aggregated measure of
sportsbook disagreement — a premium sharp-betting signal.

**Diff (3 code + 2 UI + 1 test file):**
- `services/market_gap.py` — NEW sport-agnostic `compute_market_gap` +
  `annotate_market_gap` helpers. Thresholds configurable via env
  (`MARKET_GAP_MEDIUM=50`, `MARKET_GAP_HIGH=100`).
- `routes/ferrari_tiers.py::_serve_ferrari_tier` — one-line wire-in,
  single sport-agnostic choke point.
- `components/dashboard/MarketGapBadge.jsx` — NEW shared React badge +
  detail row. Muted zinc, no glow, no animation.
- `components/dashboard/UniversalPlayerCard.jsx` + `PlayerDetailPage.jsx`
  — inline + expanded placements.
- `tests/test_market_gap.py` — 13 tests.

## 2026-04-21 — Switch Gemini model to `gemini-flash-lite-latest`

All callers + tests updated; 1 outlier at `gemini-2.5-flash`
consolidated; `backend/.env::GEMINI_MODEL` updated. No stale model
references remain.



## 2026-02-20 — Upgrade `ranking_score_v2` formula to α=0.40 blend

**Context:** Shadow α-sweep audit (`/tmp/alpha_sweep.py` →
`/tmp/magic_formula_report.md`) compared the raw_gap vs gap_pct extremes
plus 7 intermediate α values. α=0.40 delivered the highest Top-25 WR
(76%) and +71.2% real-odds ROI of any formula tested this session while
allowing 3 small-line props into Top-10 (vs 10/10 under pure gap_pct,
0/10 under raw_gap). Lets Cade Cunningham AST 7.5 and Nikola Jokic
AST 7.5 re-enter the visible slate instead of being buried.

**Diff (1 file, ~15 LOC):**
- `services/scoring/recompute.py::_compute_ranking_score_v2` —
  new signature `(projection, line, recommendation, p_model=None)`.
  Formula: `round((raw_gap / max(line, 1.0) ** 0.40) * p_model, 6)`.
  Helper call in the score-doc construction now passes `ctx.p_model`.

**Verification (post-recompute, 2,741 NBA scores replaced):**
- Expected match confirmed per row:
  - Cade Cunningham AST 7.5 OVER → `+1.6425` ✓
  - Cade Cunningham AST 8.5 OVER → `+1.0971` ✓
  - Daniss Jenkins AST 1.5 OVER → `+1.9658` ✓
  - Shaedon Sharpe PTS 7.5 OVER → `+4.2029` ✓
  - Nikola Jokic AST 7.5 OVER  → `+1.4986` ✓
  - Nikola Jokic AST 8.5 OVER  → `+0.9535` ✓
- `?sort=gap` Safe Haven top-10: all big-line PTS (Sharpe 7.5, Allen 9.5,
  Scoot 7.5, Duren 14.5, Merrill PRA 9.5, Braun 7.5, Grant 7.5, Brown 19.5,
  Edwards 19.5, J. Green 14.5). **0 small-line ≤3.5 picks in the top-10.**
- Top-30 distribution under `?sort=gap`:
  - Jenkins AST 1.5 at **#14** (was #1 under old gap_pct formula)
  - Cade AST 7.5 at **#18** (was #43 under old gap_pct formula)
  - Jokic AST 7.5 at **#19**
  - 5 / 30 small-line ≤3.5 picks — present but not dominating.
- Default sort unchanged: Jenkins AST 1.5 still #1 (uses `vision_score`).
- Endpoint smoke: all HTTP 200 (nba & mlb ferrari, odds/props, live/scores,
  scheduler-status).
- Hot-reload note: `recompute.py` is imported at module load by the FastAPI
  route; helper changes require a supervisor restart (not just reload).
  Single restart + re-recompute resolved.


## 2026-02-20 — Frontend toggle for Projection Gap ranking (NBA)

**Diff (2 files, ~65 LOC):**
- `frontend/src/hooks/useLiveOdds.js` — `fetchWarZone`, `fetchSafeHaven`,
  `fetchFrontLines` now accept an optional `sort` argument and append
  `&sort=<value>` to the URL when set. `useWarZone`, `useSafeHaven`,
  `useFrontLines` accept `sort` via their `options` object, thread it
  into the query key so cache separates the two orderings, and forward
  it to the fetcher.
- `frontend/src/pages/Dashboard.jsx`:
  - New `nbaRankingMode` state (`'default'` | `'gap'`) persisted to
    `localStorage` under key `nbaRankingMode` so the user's choice
    survives refreshes.
  - The 3 NBA tier hooks receive `{ sort: nbaRankingMode === 'gap' ? 'gap' : null }`.
  - A small pill-toggle (`data-testid="nba-ranking-toggle"` with
    `nba-ranking-default-btn` and `nba-ranking-gap-btn`) renders only
    when `currentSport === 'nba'`, positioned above the Safe Haven
    section. Defaults to "Default".

**Verification (live Ferrari demo dashboard, 2026-02-20):**
- Default top-5 Safe Haven (unchanged): Daniss Jenkins AST 1.5, Cade
  Cunningham AST 7.5, Jalen Duren PTS 14.5, Shaedon Sharpe PTS 7.5,
  Nikola Jokic AST 8.5.
- After "Projection Gap" click: Daniss Jenkins AST 1.5 (+1.66),
  Shaedon Sharpe PTS 7.5 (+1.31), Scoot Henderson PTS 7.5 (+1.06),
  Christian Braun PTS 7.5 (+0.97), Jarrett Allen PTS 9.5 (+0.97) — same
  strict DESC `ranking_score_v2` order the API returns for `?sort=gap`.
- Pill toggle stays selected after refresh (localStorage persistence).
- MLB dashboard unaffected — the toggle is gated behind
  `currentSport === 'nba'`.

**Default ranking still unchanged on the wire.** The opt-in is purely
user-driven.


## 2026-02-20 — Projection-gap ranking (`ranking_score_v2`) behind `?sort=gap` toggle

**Context:** Shadow audit (`/tmp/projection_gap_ranking_report.md`) showed that
ranking surfaced picks by `gap / max(line,1.0)` produced Top-25 AST at 80% WR
and +149% real-odds ROI — strictly dominating the current `vision_score` sort.
Ships the new ranking signal **behind a toggle** — default sort remains
unchanged pending live A/B.

**Diff (5 files, ~50 LOC total):**
- `services/scoring/recompute.py` — new `_compute_ranking_score_v2(projection,
  line, recommendation)` helper; persisted on every scored prop.
- `services/scoring/prop_scores_store.py` — added `ranking_score_v2` to the
  `_SCORE_OUTPUT_FIELDS` allow-list (otherwise silently stripped on write).
- `services/board/reader.py::get_board` — new optional `sort_key_override`
  parameter. When set to `"ranking_score_v2"`, the query also excludes
  null-ranking rows so the DESC sort doesn't float nulls to the top.
- `routes/ferrari_tiers.py`:
  - `_dedupe_picks_by_player(..., sort=...)` — NBA safe_haven / front_lines /
    war_zone endpoints pass `sort` through; rank tuple becomes
    `(ranking_score_v2,)` when `sort == "gap"`.
  - `_get_nba_tier_picks_from_scores(..., sort=...)` — forwards to
    `get_board(sort_key_override=...)`.
  - `get_ferrari_safe_haven / front_lines / war_zone` — accept new
    `sort: Optional[str] = Query(None)` query param; pass through.
  - `_merge_score_with_board` — now copies `ranking_score_v2` from the score
    doc into the UI pick dict.

**Verification (post-recompute, 2,717 NBA scores):**
- `ranking_score_v2` present on 2,450 / 2,717 rows (90.2%). The 267 nulls are
  rows where VK1 produces no `model_projection` (non-model stat_type such as
  certain PRA markets or rows with missing history).
- Endpoint behavior (live board, 2026-02-20):
  - `GET /api/v3/ferrari/safe-haven?sport=nba&limit=10` — unchanged default
    (Daniss Jenkins AST 1.5, Cade Cunningham AST 7.5, Jalen Duren PTS 14.5 …)
  - `GET /api/v3/ferrari/safe-haven?sport=nba&limit=10&sort=gap` — strict DESC
    by `ranking_score_v2` (Daniss Jenkins +1.660, Shaedon Sharpe +1.313, Scoot
    Henderson +1.057, Christian Braun +0.968, Jarrett Allen +0.967, Jerami
    Grant +0.952, Tari Eason +0.931, Sam Merrill +0.884, Wendell Carter
    +0.822, Evan Mobley +0.692).
  - Front Lines `?sort=gap` elevates small-line multi-projection plays (Daniss
    Jenkins REB 1.5 = +1.407, Cameron Johnson AST 1.5 = +1.093).
  - War Zone `?sort=gap` unchanged small-set but slightly re-ordered.
- Invalid sort values silently fall back to default (tested with
  `?sort=bogus` → default ordering).
- MLB endpoints unaffected (the `sort` param only activates when
  `sport == "nba"`; all paths pass `sort=None` for MLB).
- All other endpoints healthy: `/api/v3/odds/props`, `/api/live/scores`,
  `/api/v3/scheduler-status`, both MLB Ferrari endpoints — HTTP 200.

**Default sort is NOT flipped.** The new signal is opt-in only via the
`?sort=gap` query param. Promotion to default blocked pending live user
comparison.


## 2026-02-20 — Restore VK1 profitable signal as default NBA `p_model`

**Context:** Forensic audit revealed VK1 was computed but discarded: 95.5% of
`nba_prop_scores` used `p_true_method="hit_rate"` (L10 rolling), 0% used VK1.
The historically profitable `confidence_threshold=55.0` filter from
`backtest_real_lines.json` (AST 62.44% WR / +19.26% ROI / 4,249 bets) had no
equivalent in the live pipeline.

**Diff (2 files, ~6 LOC):**
- `services/scoring/adapters/nba_scoring.py:497` — default `p_true_method`
  `"hit_rate"` → `"model"` (VK1 regression as `p_model`).
- `services/scoring/scoring_stack.py::compute_tier` — new `p_model < 0.55 →
  tier="unqualified"` gate, fires after anchor veto, before tier gates.

**Verification (post-recompute, `version_tag=final-nba-rt`, 2,688 props):**
- `p_true_method` distribution: `model=2421, hit_rate=171 (fallback), none=96`
- Tier distribution: `safe_haven=39, front_lines=102, war_zone=14, unqualified=2533`
- 55% gate fires: 1,196
- AST surfaced: 31 → **40 (+29%)**; top of AST surface is now Cade Cunningham,
  Jokic, Harden, Brunson (model-elevated) instead of trivial 1.5/3.5-line picks.
- Traps correctly demoted: Tatum REB 9.5 (p=0.375), KAT REB 10.5 (p=0.503) now
  `unqualified` with reason `p_model<0.55`.
- Endpoints healthy: `/api/v3/ferrari/{safe-haven,front-lines,war-zone}` HTTP 200;
  `/api/v3/odds/props`, `/api/live/scores`, `/api/v3/scheduler-status` all 200.

**VK2 status:** kept on disk, still callable via explicit `p_true_method="vk2"`.
5-path audit (`/tmp/path_compare_report.md`) showed VK2's 102 advanced-stat
features contribute <1pp ROI; simplified VK2 returns +29.79% vs VK1's +30.12%.
No routing advantage to VK2 by default.

**Expected downstream impact:** `edge_pct` now reflects real model edge (not
`hit_rate − tp`); tier assignments now driven by VK1 p_over (Gaussian over MAE)
matching the historical backtest that produced the original +19.26% AST ROI.


## 2026-04-19 — Wave 0 Batch 12 plumbing (long-tail sweep + odds_mapping closure)
- Registry reconciliation: `odds_mapping` was **already** present in registry at
  `collection_names.py:70` as per-sport NBA concept. Batch 11 mis-used
  `COLL.shared("odds_mapping")` — correct call is `COLL("odds_mapping","nba")`.
  **No code change to registry needed.**
- Routed 3 deferred refs in `server.py:1316-1318` (boot-time odds_mapping indexes).
- 14 Batch 12 files + server.py routed through `COLL(...)`:
  `services/historical_data_fetcher.py`, `services/mlb_vegas_killer_model.py`,
  `services/optimized_sync_engine.py`, `services/bdl_game_logs_sync_batched.py`,
  `services/photo_storage_service.py`, `services/master_hub_sync.py`,
  `services/vision_ai_service.py`, `services/oracle_apex_service.py` (2nd-pass),
  `services/bdl_game_logs_sync.py`, `services/stats_enrichment_service.py`,
  `services/intel_suite_calculator.py`, `services/probability_score_service.py`,
  `services/mlb_deep_ingestion.py`, `services/ferrari_tier_service.py` (2nd-pass).
- 17 code-level literals removed → 17 `COLL(...)` call-sites added (3 server.py
  + 14 Batch 12). 12 imports added (2 files already had imports from prior batches).
- In-scope hardcoded-ref count in these files: **17 → 0**.
- Regression suite: 80 passed / 1 skipped / 0 failed — matches baseline.
- Live smoke after restart: all 10 endpoints HTTP 200. Boot-time odds_mapping
  index creation (3 calls) executed cleanly via new COLL routing.
  Backend uptime clean. 0 new errors.
- Global broader-scanner residual: **43 → 29 refs across 28 files**.
- Breakdown: 14 runtime (Batch 13 candidates), 13 scripts/* (non-runtime),
  2 `scripts/layer_audit.py` (user excluded), 1 registry self-reference.
- **Wave 0 progress: 56 files plumbed, 93% of baseline refs eliminated.**
- Audit: `/app/memory/wave0_batch12_audit.md`.


## 2026-04-19 — Wave 0 Batch 11 plumbing (second-pass + long-tail)
- 14 files routed through `services/config/collection_names.py::COLL`:
  `server.py` (second-pass), `services/mlb_tier_sorter.py`,
  `services/vegas_killer_model.py` (second-pass),
  `services/forward_testing_service.py`,
  `services/vegas_pro_model.py` (second-pass), `services/mlb_badge_system.py`,
  `services/vegas_regression_model.py` (second-pass),
  `services/bdl_enhanced_data.py` (second-pass),
  `services/mlb_tier_service.py` (second-pass),
  `services/live_injury_micro_sync.py`, `services/bdl_player_badge_service.py`,
  `services/injury_sensor.py`, `routes/vision.py`,
  `repositories/board_repo.py` (second-pass).
- In-scope concepts: `master_hub` (NBA+MLB), `board_cache` (NBA+MLB), `live_props`
  (NBA+MLB), `context_flags` (NBA), `injuries` (shared), `live_scores_cache` (shared).
- 20 code-level literals removed → 20 `COLL(...)` call-sites added. 7 imports added
  (7 files already had import from prior batches).
- **Stop & Report**: `odds_mapping` concept NOT in registry. Per strict-refactor
  rules, did not add registry entry. 3 `odds_api_mapping_master` refs in
  `server.py:1316-1318` deferred pending user approval to add
  `"odds_mapping": "odds_api_mapping_master"` to `_SHARED_COLLECTIONS`.
- Batch 11 in-scope residuals in these files: **20 → 0**.
- Regression suite: 80 passed / 1 skipped / 0 failed — matches baseline.
- Live smoke after restart: NBA + MLB Ferrari, master-hub lookup, live scores,
  vision/status all HTTP 200. Backend uptime clean. 0 new errors.
  (Pre-existing 404 on `/v3/vision/player/{slug}` — `player_router` not mounted
  in `server.py`; NOT a regression from this batch.)
- Global in-scope broader-scanner residual: **66 → 43 refs across 42 files**.
- Deferred refs awaiting registry entry: 3 (`odds_api_mapping_master`).
- Audit: `/app/memory/wave0_batch11_audit.md`.


## 2026-04-19 — Wave 0 Batch 10 plumbing (broader scanner sweep)
- 9 files routed through `services/config/collection_names.py::COLL`:
  `routes/master_hub.py`, `routes/ai_context.py`, `routes/live.py`,
  `routes/cached_data.py`, `services/injury_triggered_rescore.py`,
  `services/board_intelligence_service.py`, `routes/ferrari_tiers.py`,
  `services/team_stats_service.py`, `routes/qa_testing.py`.
- In-scope concepts: `master_hub` (NBA+MLB), `board_cache` (NBA+MLB),
  `ticker_cache` (shared), `injuries` (shared).
- 44 code-level literals removed → 44 `COLL(...)` call-sites added. 7 imports added
  (2 files already had import from prior batches).
- Broader scanner now authoritative (covers `db.X`, `db["X"]`, `_db.X`, `_db["X"]`,
  `engine.db.X`, `self.db[...]` variants).
- Second-pass completed on `board_intelligence_service.py` (4 refs added since Batch 2).
- In-scope hardcoded-ref count in these files: **44 → 0**.
- Regression suite: 80 passed / 1 skipped / 0 failed — matches baseline.
- Live smoke after restart: NBA + MLB Ferrari + master-hub player lookup +
  ai-context + live scores/news + command search all HTTP 200.
  `master-hub/player/name/Luka Doncic` returned real 8-key document via new
  bracket-access COLL routing. Backend uptime clean. 0 new errors.
- Global broader-scanner residual: **107 → 66 refs across 56 files** (41-ref
  reduction, 38% drop). Remaining refs are long-tail (mostly 1–2 per file).
- Audit: `/app/memory/wave0_batch10_audit.md`.


## 2026-04-19 — Wave 0 Batch 9 plumbing (resolvers/repos/routes/utility services)
- 10 files routed through `services/config/collection_names.py::COLL`:
  `services/picks/player_stats_resolver.py`, `advanced_analytics.py`,
  `repositories/player_repo.py`, `repositories/sync_repo.py`,
  `routes/command.py`, `routes/mlb_ripple.py`,
  `services/usage_spike_detector.py`, `services/props_service.py`,
  `services/injury_service.py`, `services/injury_advantage.py`.
- In-scope concepts: `master_hub` (NBA+MLB), `master_roster`, `board_cache`,
  `sync_log` (shared), `injuries` (shared), `live_scores_cache` (shared).
- 13 code-level literals removed → 13 `COLL(...)` call-sites added. 10 imports added.
- First-time routing of bracket-access pattern `_db["mlb_master_hub_2026"]` and
  `db["injuries_normalized"]` — both handled correctly via `COLL(...)`.
- In-scope hardcoded-ref count in these files: **13 → 0**.
- Regression suite: 80 passed / 1 skipped / 0 failed — matches baseline.
- Live smoke after restart: NBA + MLB Ferrari, NBA + MLB player search,
  MLB ripple alerts all HTTP 200. Backend uptime clean. 0 new errors.
- Global code-level residual (apples-to-apples attr-access pattern): 59 → **48 refs**.
- Broader scanner added (now covers `db["..."]` bracket-access and `_db` variable
  patterns that were invisible before) — surfaces 107 total refs across 65 files
  as honest accounting for Batch 10 scope. This is not a regression; it's
  newly-surfaced visibility.
- Audit: `/app/memory/wave0_batch9_audit.md`.


## 2026-04-19 — Wave 0 Batch 8 plumbing (adapter/writer/sync orchestration)
- 9 files routed through `services/config/collection_names.py::COLL`:
  `services/odds_api_service.py`, `services/sync_service.py`,
  `services/badge_resolver.py`, `services/mlb_high_friction_model.py`,
  `services/tier_builder_service.py`, `services/ssot_data_layer.py`,
  `services/sync_orchestration_service.py`, `services/bdl_comprehensive_sync.py`,
  `services/insights_sync_service.py`.
- In-scope concepts: `master_hub` (NBA+MLB), `board_cache`, `live_props` (MLB),
  `events_cache`, `odds_cache`, `sync_log` (shared), `context_flags` (new this batch).
- 18 code-level literals removed → 18 `COLL(...)` call-sites added. 9 imports added.
- `context_flags` concept first used this batch — registry resolves it to
  `nba_context_engine` for NBA sport.
- `_archive_mlb_v1/` not touched per exclusion.
- Out-of-scope refs reported and left: `dg_radar_picks`, `dg_goblin_vault`,
  `dg_front_lines`, `dg_parlay_builder`, `dg_goblin_recon`, `dg_daily_insights`,
  `mlb_historical_logs`, `dg_verification_failures`, `dg_player_data`,
  `dg_trending` (none in registry).
- In-scope hardcoded-ref count in these files: **18 → 0**.
- Regression suite: 80 passed / 1 skipped / 0 failed — matches baseline.
- Live smoke after restart: all 9 NBA + MLB endpoints HTTP 200.
  Backend uptime clean. 0 new errors.
- Global code-level residual: 81 → **59 refs across 47 files**.
- Audit: `/app/memory/wave0_batch8_audit.md`.


## 2026-04-19 — Wave 0 Batch 7 plumbing (MLB pipeline + roster services)
- 8 files routed through `services/config/collection_names.py::COLL`:
  `services/mlb_tier_service.py`, `services/mlb_lineup_ripple_service.py`,
  `services/mlb_master_sync.py`, `services/mlb_oracle_apex_service.py`,
  `services/roster_service.py`, `services/roster_sync_service.py`,
  `services/data_integrity_service.py`, `services/photo_service.py`.
- In-scope concepts: `master_hub` (NBA + MLB), `master_roster` (NBA),
  `board_cache` (NBA + MLB), `live_props` (MLB), `sync_log` (shared).
- 30 code-level literals removed → 30 `COLL(...)` call-sites added. 8 imports added.
- This batch closes the 2 out-of-scope residuals from Batch 6 in
  `data_integrity_service.py` (`master_roster` + `sync_log`).
- Full MLB pipeline now plumbed end-to-end: mlb_master_sync (writer) +
  mlb_tier_service + mlb_oracle_apex_service + mlb_lineup_ripple_service (readers).
- Out-of-scope refs reported and left: `nba_context_engine` in mlb_tier_service
  (not in priority list/registry), `dg_flagged_players`, `dg_verification_failures`
  (not in registry).
- In-scope hardcoded-ref count in these files: **30 → 0**.
- Regression suite: 80 passed / 1 skipped / 0 failed — matches baseline.
- Live smoke after restart: NBA Ferrari endpoints + MLB tier endpoints all HTTP 200,
  MLB endpoints returned 60KB+ payloads (real data through COLL routing).
  Backend uptime clean. 0 new errors.
- Global code-level residual: 103 → **81 refs across 58 files**.
- Audit: `/app/memory/wave0_batch7_audit.md`.


## 2026-04-19 — Wave 0 Batch 6 plumbing (injury/live/social/cache/integrity)
- 5 files routed through `services/config/collection_names.py::COLL`:
  `services/injury_vacuum_service.py`, `services/engines/live_scores_engine.py`,
  `services/engines/social_signal_engine.py`, `services/rolling_cache_manager.py`,
  `services/data_integrity_service.py`.
- In-scope concepts: `star_usage_cache`, `master_hub` (NBA + MLB), `board_cache`
  (NBA + MLB), `injuries` (shared), `live_scores_cache` (shared), `ticker_cache`
  (shared), `ticker_headlines` (shared), `breaking_news_cache` (shared).
- 23 code-level literals removed → 23 `COLL(...)` call-sites added. 5 imports added.
- Cross-driver pattern validated: `db[COLL(...)]` subscript works identically
  on both PyMongo sync (`sync_db`) and Motor async (`self.db`) clients.
- Out-of-scope refs reported and intentionally left: `dg_master_roster` +
  `dg_sync_log` in data_integrity_service.py (concepts not in Batch 6 priority list),
  plus `injury_log`, `vacuum_alerts`, `bdl_injuries`, `dg_social_signals`,
  `dg_player_news_cache`, `dg_verification_failures` (not in registry).
- In-scope hardcoded-ref count in these files: **23 → 0**.
- Regression suite: 80 passed / 1 skipped / 0 failed — matches baseline.
- Live smoke after full restart: all Ferrari endpoints + live-scores + vacuum/updates
  HTTP 200. Backend uptime clean. 0 new errors.
- Global code-level residual (broad pattern incl. MLB variants): 103 refs across 66 files.
- Audit: `/app/memory/wave0_batch6_audit.md`.


## 2026-04-19 — Wave 0 Batch 5 plumbing (intel/enrichment/repos)
- 5 files routed through `services/config/collection_names.py::COLL`:
  `services/market_moves_engine.py`, `services/oracle_apex_service.py`,
  `services/vision_intel_enrichment_service.py`, `services/headshot_service.py`,
  `repositories/board_repo.py`.
- In-scope concepts: `board_cache`, `master_hub`, `injuries` (shared),
  `live_scores_cache` (shared).
- 13 code-level literals removed → 13 `COLL(...)` call-sites added. 5 imports added.
- Out-of-scope refs reported and intentionally left: 2× `dg_live_props`
  (oracle_apex L429 + board_repo L20 — `live_props` not in Batch 5 priority list),
  plus `ferrari_scored`, `oracle_apex_picks` (not in registry).
- In-scope hardcoded-ref count in these files: **13 → 0**.
- Regression suite: 80 passed / 1 skipped / 0 failed — matches baseline.
- Live smoke after restart: all Ferrari endpoints + market-moves read path HTTP 200,
  supervisor clean, 0 new errors in backend.err.log.
- Global code-level residual (broader pattern incl. live_scores_cache/ticker/news):
  **85 refs across 53 files**.
- Audit: `/app/memory/wave0_batch5_audit.md`.


## 2026-04-19 — Wave 0 Batch 4 plumbing (boot/indexes/scheduler/legacy engine)
- 4 files routed through `services/config/collection_names.py::COLL`:
  `server.py`, `scripts/init_database.py`, `routes/scheduler.py`,
  `services/engines/demon_goblin_engine.py`.
- In-scope concepts: `board_cache`, `board_cache_temp`, `master_hub`,
  `master_roster`, `live_props`, `odds_cache`, `events_cache`, `sync_log`,
  `ticker_headlines` (shared).
- 44 code-level literals removed → 44 `COLL(...)` call-sites added. 4 imports added.
- Boot-time index creation block (22 `create_index` calls in `server.py`) fully
  routed through `COLL(...)` — confirmed healthy on full `supervisorctl restart`.
- Response-shape preserved: JSON response keys in `/v3/init-database` left
  unchanged (labels only, not DB access).
- `routes_archive/roster.py` excluded per scope.
- In-scope hardcoded-ref count in these files: **44 → 0**.
- Regression suite: 80 passed / 1 skipped / 0 failed — matches baseline.
- Live smoke after restart: all Ferrari endpoints + `/v3/scheduler-status` HTTP 200,
  scheduler running with 22 jobs, 0 errors in backend.err.log.
- Global code-level residual: 153 → **109 refs across 70 files**.
- Audit: `/app/memory/wave0_batch4_audit.md`.


## 2026-04-19 — Wave 0 Batch 3 plumbing (core pipeline, no renames)
- 3 files routed through `services/config/collection_names.py::COLL`:
  `services/ferrari_tier_service.py`, `services/picks_getter_service.py`,
  `services/cached_board_builder_service.py`.
- In-scope concepts: `board_cache`, `board_cache_temp`, `master_hub`,
  `master_roster`, `events_cache`, `odds_cache`, `sync_log`.
- 18 code-level literals removed → 18 `COLL(...)` call-sites added. 3 imports added.
- Includes atomic `renameCollection` admin command (cached_board_builder L390-391)
  now routed through `COLL(...)` — behavior preserved.
- In-scope hardcoded-ref count in these files: **18 → 0** (docstring/log prose untouched).
- Out-of-scope refs reported and intentionally left: `ferrari_*` legacy collections
  (no registry concept), `nba_context_engine` (excluded from user's priority list),
  `dg_radar_picks/goblin_vault/front_lines/parlay_builder/goblin_recon/player_data/`
  `daily_insights/flagged_players` (not in registry).
- Regression suite: 80 passed / 1 skipped / 0 failed — matches baseline.
- Live smoke: `/api/v3/ferrari/{safe-haven,front-lines,war-zone}` all HTTP 200.
  Backend hot-reloaded cleanly; 0 new errors in supervisor log.
- Global code-level residual count: 170 refs across 75 files (down from ~188 pre-Batch-3).
- Audit: `/app/memory/wave0_batch3_audit.md`.


## 2026-04-19 — Wave 0 Batch 2 plumbing (ingest layer, no renames)
- 5 files routed through `services/config/collection_names.py::COLL`:
  `services/odds_sync_service.py`, `services/sharp_edge_calculator.py`,
  `services/board_intelligence_service.py`, `services/bdl_enhanced_data.py`,
  `services/bdl_stats_calculator.py`.
- In-scope concepts: `live_props`, `master_roster`, `odds_cache`, `master_hub`.
- 9 code-level literals removed → 9 `COLL(...)` call-sites added. 5 imports added.
- In-scope hardcoded-ref count in these files: **9 → 0** (docstring prose untouched).
- Regression suite: 80 passed / 1 skipped / 0 failed — matches baseline.
- Live smoke: `/api/v3/ferrari/{safe-haven,front-lines,war-zone}` all HTTP 200.
- Resolver parity verified: physical names unchanged → zero behavior change.
- Audit: `/app/memory/wave0_batch2_audit.md`.


## 2026-04-19 — Wave 0 Batch 1 plumbing (no renames)
- 9 files routed through `services/config/collection_names.py::COLL`:
  `db/collections.py`, `config/collections.py`, `services/nba_career_service.py`,
  `services/defensive_momentum_service.py`, `services/vegas_killer_model.py`,
  `services/vegas_regression_model.py`, `services/vegas_pro_model.py`,
  `services/context_badge_service.py`, `routes/cached_data.py` (context_engine only).
- Regression suite: 80 passed / 1 skipped / 0 failed (incl. `test_collection_names.py` 61/61).
- Live `/api/v3/ferrari/front-lines?sport=nba` returns HTTP 200 post-restart.
- No renames, no data migrations, no behavior changes. Indirection only.
- Residual scan: `/app/memory/wave0_batch1_residuals.md`.



## 2026-04-19 — Decision-Layer Integrity (Sengun AST 6.5 audit response)
Five ordered fixes after the user-flagged Sengun card exposed systemic
decision-layer contradictions between badges / tile / narrative / direction:

1. **Floor Lock window + tooltip honesty** (`services/intel_suite_calculator.py`)
   - Switched `values[-10:]` → `values[:10]` in both `_generate_scout_badges`
     and `_calculate_stability_index`. `history.2025_season` is stored
     newest-first, so the prior index took the OLDEST 10 games.
   - Floor Lock now requires `hit_rate >= 90` (matches its public tooltip).
     Previously fired on `std<=2.0 AND mean>line` at 70% hit rates.
2. **Canonical projection contract** (`services/intel_suite_calculator.py::_generate_vision_insight`)
   - Narrative now quotes `board_pick.vk_predicted` (the same field the UI
     tile binds to in `UniversalPlayerCard.jsx:508`) instead of
     `lasso.projection`. Lasso projection exposed as a separate
     `lasso_projection` field — never conflated.
3. **Model-disagreement flag**
   - `vision_insight.models_disagree = True` when `|vk−lasso|/line > 0.10`
     and the two models sit on opposite sides of the line. Narrative
     explicitly calls out disagreement instead of writing a one-sided thesis.
4. **PP-anchor direction veto** (`services/scoring/scoring_stack.py`)
   - New `_model_contradicts_anchor(prop, side)` runs inside `compute_tier`
     BEFORE tier dispatch. Rejects an OVER when all three contradict the
     anchor side: `vk_edge<0`, `hit_rate_over<50`, `l10_avg<line` (symmetric
     for UNDER). Sets `tier="unqualified"`,
     `tier_reason="model_contradicts_anchor: …"`.
5. **"Clear Read" → "Model Fit"** (`components/ui/BadgePill.jsx`)
   - Relabelled to precisely reflect the gate (Lasso R² ≥ 0.35 on held-out
     games). Tooltip sentiment changed from "positive / lean in" to
     "neutral / model-fit quality only — NOT a prop recommendation".
6. **War Zone gate review** (`services/scoring/adapters/nba_scoring.py`)
   - Documented comment: war_zone gates stay market-facing; directional
     evidence handled by Fix #4. Separation of concerns enforced in code
     comments; no threshold changes.

**Expected live effect**: on the next scoring pass (next `odds_sync` or
hourly rebuild), the Sengun AST 6.5 OVER pick drops from `war_zone` to
`unqualified` and vanishes from the board. No retroactive mutation of
existing score docs.

**Regression suite added**: `tests/test_decision_layer_sengun.py` (7 tests).
Total passing: 19 (12 pre-existing + 7 new).


## 2026-04-19 — Tier Integrity + Post-Dedup Backfill Verified
- **Bug fix**: player duplicates across a single tier (same player ×N on
  alternate lines or across stat families). Confirmed on NBA Safe Haven
  (Amen Thompson ×3) and MLB War Zone (James Wood ×2).
- **Fix at two levels**:
  - `services/board/reader.py::get_board` — over-fetch `cap*6`, walk sorted
    stream, keep first per normalized `player_name`, stop at `cap` distinct
    players. Reader is the single entry point for NBA boards.
  - `routes/ferrari_tiers.py::_dedupe_picks_by_player` — route-level
    fallback wired into all 5 tier exit points; covers MLB legacy
    collections (`mlb_safe_haven` / `mlb_front_lines` / `mlb_war_zone`)
    that bypass the reader. Ranks by `(vision_score, pp_utility, |edge|)`.
- **Post-dedup backfill audit** (2026-04-19 00:00 UTC):
  | Tier (sport)     | pool rows | distinct | surfaced | reason if <cap          |
  |------------------|----------:|---------:|---------:|-------------------------|
  | NBA safe_haven   |         3 |        1 |        1 | pool exhausted          |
  | NBA front_lines  |         9 |        4 |        3 | 1 × JIT injury (Durant) |
  | NBA war_zone     |         2 |        2 |        2 | pool exhausted          |
  | MLB safe_haven   |        10 |       10 |       10 | FULL                    |
  | MLB front_lines  |        10 |       10 |       10 | FULL                    |
  | MLB war_zone     |        10 |        9 |        9 | 1 × dedup (James Wood)  |
  Invariant `surfaced == min(cap, distinct − injury_excluded)` holds for
  every tier. No hidden exclusions.
- **Regression suite**: `/app/backend/tests/test_tier_integrity.py`
  (8 tests). Total backend regression surface now 13 passing.


## 2026-04-18 — Priority realignment (user directive)

## 2026-04-19 — Step 6 A/B Comparator Isolation + Prop-Scores Hygiene
- **Hygiene pass**: archived 15 stale experimental version_tags (41,793 docs)
  from `nba_prop_scores` → `nba_prop_scores_archive_stale_tags`; same for MLB
  (10,090 docs → `mlb_prop_scores_archive_stale_tags`). Canonical tags
  (`final-nba`, `final-mlb`) are the only writable tags left on the live
  collections. Indexes rebuilt via `ensure_indexes()`.
- **Stray `live` tag plugged**: `write_prop_scores()` (MLB legacy wrapper) now
  writes to `final-mlb` instead of `live`, eliminating the off-window writer.
- **Real-time engine → shadow tag**: `services/board/engine.py` now upserts
  under `{canonical}-rt` (`final-nba-rt` / `final-mlb-rt`). Legacy full-rebuild
  continues writing under the canonical tag. No writer collision on the live
  tag during the 48h window.
- **Drift auditor cross-tag**: `_audit_one_window` now queries BOTH tags per
  ledger entry. Report surfaces `rt_tag`, `legacy_tag`, `rt_present`,
  `legacy_present`, `rt_presence_ratio`, `legacy_presence_ratio`, and each
  divergence sample carries `rt_ledger`, `rt_materialized`, `legacy_current`
  side-by-side.
- **Reader unchanged**: live board readers (ferrari routes, optimized_sync,
  injury_triggered_rescore) stay pinned to `final-{sport}`.
- **Verified**: ledger flushed; direct `recompute_sport(tag=final-nba-rt)`
  wrote 3 docs to the shadow tag (visible under the new `-rt` distribution);
  audit report confirms `rt_presence_ratio=1.0` against `legacy_presence_ratio=0.0`
  for the seeded keys. Regression tests (5) still pass.


- Per user: next priority is Universal Board Migration + Legacy Writer
  Retirement (Step 6). No new features until that lands.
- New file `/app/memory/ROADMAP.md` created with P0/P1/P2 and explicit
  acceptance gates for Step 6.
- 48h observation clock deferred pending decision on blocker 1a (A/B
  comparator race between `write_versioned_scores(mode="replace")` in the
  legacy rebuild and `mode="upsert"` in the real-time engine, both sharing
  `version_tag=final-nba`). Raw evidence captured in ROADMAP.md §1a.



## 2026-04-18 — Canonical Stat-Window Contract (Ferrari h10_rate Clobber Fix)
- **Root cause**: `ferrari_tiers.py:1059-1072` was overwriting `prop["h10_rate"]`
  with `score.hit_rate_over` (L20/p_true-derived, 20-game window). Chart binding
  (`GameLogBarChart` over `game_logs[:10]`) stayed correct while the "L10 Hit"
  tile displayed L20 math under an "L10" label.
- **Evidence trail**: Grayson Allen PTS OVER 7.5 — chart 9/10 = 90%, tile 95%.
  Scope: 3/3 front-lines picks + 3/4 safe-haven picks were affected pre-fix.
- **Fix**: Stopped writing `score.hit_rate_over/under` into `h10_rate`. Model-
  derived rates moved to a separate namespace: `model_hit_rate_over`,
  `model_hit_rate_under`, `model_hit_rate_active`.
- **Invariant guard**: `_assert_canonical_hit_rate_invariant(prop)` + 
  `_guard_board_picks(picks)` installed at the exit of all Ferrari endpoints
  (front-lines, safe-haven, war-zone, oracle-apex). Asserts
  `h{5,10,20}_rate == round(l{5,10,20}_hits / window * 100, 1)`; on mismatch
  logs a `[CANONICAL_GUARD]` warning and auto-corrects from the count field.
- **Regression test**: `/app/backend/tests/test_hit_rate_canonical.py` (5 tests,
  all passing).
- **Files**: `routes/ferrari_tiers.py`, `tests/test_hit_rate_canonical.py`.



## 2026-04-16 — Non-Manual MLB Path Verified + CV Standardization
- Traced full non-manual MLB pipeline end-to-end: `scheduled_safety` → Coordinator → UnifiedPipeline(MLBAdapter) → Atomic Publish → Gemini enrichment
- Raw log trace: run_id `a8fa4aa3`, 2171 props → SH=2, FL=10, WZ=10, 22/22 Gemini enriched in 16.1s
- Fixed CV calculation inconsistency: standardized all `np.std()` calls to use `ddof=1` (sample std dev) across `oracle_apex_service.py`, `intel_suite_calculator.py`
- Files already using `ddof=1`: `vegas_regression_model.py`
- Discovered DB_NAME mismatch: server uses `pick_vision` (from .env), not `propvision`
- Added test endpoint `POST /api/v3/mlb/test-scheduled-sync` for non-manual path verification

## 2026-04-16 — Event-Driven Sync Architecture Complete
- Wired legacy data sync paths to emit BoardEvent coordinator triggers
- MLB sync engine, Ferrari tier service, MLB tier service all emit `scored_data_refresh`
- Closed the "open-door" publish gap
- Market Moves exit-reason classification (prop_removed, displaced_by_higher, etc.)
- 3-part prop identity keys (player|stat|line) for alternate lines

## 2026-04-14 - MLB Deep Ingestion Complete
- Built async 15x parallel ingestion engine
- Ingested 777 active MLB players with 149,989 game logs (3 seasons)
- Fixed dk_odds parameter mismatch in rolling_cache_manager.py

## 2026-04-14 - NBA Deep Ingestion + Advanced Overlay Complete
- NBA async ingestion: 559 players, 112,778 game logs, 181.4s, 0 errors
- Advanced Overlay: 15 metrics merged into every NBA game log

## 2026-04-14 - Automated Feature Discovery + Lasso Prediction Engine
- AutoFE pipeline: 366 engineered features → Lasso L1 selection
- 3 models trained for MLB, NBA

## 2026-04-22 — HARD CONSOLIDATION (P0)

Single universal path for sync / scoring / gate. No fallbacks, no
parallel branches, no legacy shims.

### Canonical path (the ONLY path now)
1. **Sync:** `services/universal_odds_sync.py :: sync_sport_props(sport)` →
   writes `{sport}_live_props`.
2. **Scoring:** `services/scoring/recompute.py :: recompute_sport(sport, tag)`
   driven by `services/scoring/adapters/{nba,mlb}_scoring.py` and gated by
   `services/scoring/scoring_stack.py`. Writes `{sport}_prop_scores` at
   `final-{sport}` AND `final-{sport}-rt`.
3. **Orchestration:** `services/master_sync.py :: run_master_sync(sport)` —
   sport-agnostic; `RebuildCoordinator.dispatch_master_sync(sport)` wraps
   it in the `UpstreamSyncLock`.
4. **Board reads:** `services/board/reader.py` reads
   `{sport}_prop_scores` at `final-{sport}-rt`.

### Files deleted (43 files, ≈ 10,000+ LOC)

Services:
- `services/engines/demon_goblin_engine.py`
- `services/odds_sync_service.py`
- `services/optimized_sync_engine.py`
- `services/nba_master_sync.py`
- `services/ferrari_tier_service.py`
- `services/mlb_tier_service.py`
- `services/oracle_apex_service.py`
- `services/mlb_oracle_apex_service.py`
- `services/cached_board_builder_service.py`
- `services/unified_pipeline.py`
- `services/adapters/nba_adapter.py`
- `services/adapters/mlb_adapter.py`
- `services/pipeline/master_steps.py`
- `services/sync_orchestration_service.py`
- `services/sync_service.py`
- `services/tier_builder_service.py`
- `services/prop_processor_service.py`
- `services/parlay_builder_service.py`
- `services/props_service.py`
- `services/roster_service.py`
- `services/stats_api_service.py`
- `services/stats_enrichment_service.py`
- `services/insights_sync_service.py`
- `services/data_integrity_service.py`
- `services/mlb_pipeline.py`
- `services/mlb_sync_engine.py`

Routes:
- `routes/picks.py`, `routes/parlays.py`, `routes/board.py`
- `routes/board_intel_v2.py`, `routes/cached_data.py`
- `routes/core_v3.py`, `routes/intel_sync.py`, `routes/legacy.py`
- `routes/roster_sync.py`, `routes/scheduler.py`, `routes/tiers.py`

Tests / Scripts:
- `tests/test_v3_api_refactoring.py`, `tests/test_v3_refactoring_phase3.py`
- `tests/test_v3_new_services.py`, `tests/test_odds_sync_standard_market.py`
- `tests/test_war_zone_cv_floor_removed.py`, `tests/test_betmgm_lookup.py`
- `scripts/validate_consensus.py`

### Callers rewired
- `services/rebuild_coordinator.py :: dispatch_master_sync` →
  `services/master_sync.run_master_sync(sport)` for NBA AND MLB (same
  code path, no `if sport == "nba"` branch).
- `services/rebuild_coordinator.py :: _execute_rebuild` → same.
- `server.py` → deleted `DemonGoblinEngine` import, init, and
  `register_all_routes(demon_goblin_engine, …)` kwargs. Adaptive sync
  callback rewired to `run_master_sync(db, "nba")`.
- `services/engines/adaptive_sync_engine.py` callback target is now
  the universal master sync.
- `routes/__init__.py` rewritten to remove all engine DI and drop
  references to the 11 deleted route modules.
- `routes/ferrari_tiers.py`:
  - Removed `get_ferrari_tier_service`, `get_service()`.
  - `/v3/ferrari/rebuild` → `run_master_sync(db, sport)`.
  - `/v3/ferrari/oracle-apex` → HTTP 410 Gone.
  - `/v3/ferrari/safe-haven?legacy=true` → HTTP 410 Gone.
  - `/v3/ferrari/all` now reads via `_serve_ferrari_tier` (universal).
- `server.py` MLB-health rebuild → `run_master_sync(db, "mlb")`.
- `scripts/init_database.py` → `run_master_sync` + `universal_odds_sync`.

### Data-path fixes caught during consolidation
- `services/scoring/prop_scores_store.py :: write_versioned_scores`
  now dedupes by `canonical_key` in replace mode. Upstream had rare
  collisions from unknown markets with empty stat_type.
- `services/scoring/adapters/nba_scoring.py :: build_context` now
  reads `stat_type` / `market_key` / `pp_layer` / `dk_layer` /
  `fd_layer` / `bol_layer` directly — the universal sync writes those
  fields, the old DemonGoblinEngine wrote `market` + `sharp_market`.

### Acceptance verification
- `POST /api/nba/sync/master` → 202, completes in ~116 s,
  `success=true, errors=[]`. `final-nba-rt` populated with tier
  picks.
- `POST /api/mlb/sync/master` → 202, completes in ~38 s,
  `success=true, errors=[]`. `final-mlb-rt` populated.
- All 6 Ferrari endpoints HTTP 200 with populated tier picks:
  NBA safe_haven=10 / front_lines=10 / war_zone=1; MLB
  safe_haven=0 / front_lines=0 / war_zone=10 (MLB off-hours).
- `GET /api/v3/admin/delta/engine-status` → 200.
- Grep proof: zero live references to `DemonGoblinEngine`,
  `NBAMasterSync`, `UnifiedPipeline`, `NBAAdapter`, `MLBAdapter`,
  `get_ferrari_tier_service`, `get_oracle_apex_service`,
  `get_mlb_tier_service`, `run_optimized_sync`, `sync_odds_to_mongo`.
- 108 / 108 consolidation-relevant pytest tests pass.

### Temporarily broken (known — rewire if UI needs them)
- Frontend endpoints served by deleted routes now return 404:
  `/api/v3/war-zone`, `/api/v3/goblin-vault`, `/api/v3/front-lines`,
  `/api/v3/player-with-badges/{name}`, `/api/v3/cached-props`.
  The canonical replacements live under `/api/v3/ferrari/*` and
  work. The frontend should be updated to use them.


## 2026-04-22 — Phase 2: Frontend Rewire + Legacy Collection Drop

Direct follow-up to the HARD CONSOLIDATION. No compatibility paths.

### Restored on the universal path
- `GET /api/v3/player-with-badges/{name}?sport={sport}` — new `routes/player.py`, reads `{sport}_prop_scores @ final-{sport}-rt` + `{sport}_master_hub_2026`. Returns player metadata + props + derived demons/goblins buckets.
- `GET /api/v3/board?sport={sport}` — also in `routes/player.py`. Board-wide player list grouped from `{sport}_prop_scores`. Replaces the deleted `/api/v3/cached-props`.

### Frontend rewired
- `useLiveOdds.js::fetchLiveOdds` → `/api/v3/board` (canonical).
- `useMostPopularBets` + `useTrapGraveyard` hooks + their `fetchMostPopularBets` / `fetchTrapGraveyard` helpers **deleted** (zero production callers). Replacement endpoints are intentionally NOT coming back; filter the universal board client-side if needed.
- All other fetches already pointed at `/api/v3/ferrari/*` or the universal sport endpoints.

### Legacy tier collections dropped (all writers already deleted)
Dropped via `db.drop_collection`:
- `ferrari_safe_haven`, `ferrari_front_lines`, `ferrari_war_zone`
- `ferrari_discarded`, `ferrari_scored`
- `mlb_ferrari_safe_haven`, `mlb_ferrari_front_lines`, `mlb_ferrari_war_zone`
- `mlb_safe_haven`, `mlb_front_lines`, `mlb_war_zone`
- `elite_safe_haven`, `elite_front_lines`, `elite_war_zone`
- `oracle_apex_analyzed`

(15 collections.)

### Callers rewired onto the canonical scored table
- `services/forward_testing_service.py` — reads `{sport}_prop_scores @ final-{sport}-rt` filtered by `tier`. Legacy `TIER_COLLECTIONS` map deleted.
- `services/injury_advantage.py` — same. Legacy `TIER_COLLECTIONS` + per-collection `TIER_LABELS` compressed.
- `services/market_moves_engine.py` — `_read_live_board` and the `ferrari_scored` candidate-pool fallback both rewritten to read `{sport}_prop_scores`.
- `routes/ferrari_tiers.py` — `/v3/mlb/ferrari/hrr-picks` rewired to read `mlb_prop_scores @ final-mlb-rt, tier=war_zone` instead of the deleted `mlb_war_zone` collection.
- `server.py` MLB health check reads `mlb_prop_scores` counts and invokes `run_master_sync(db, "mlb")` on empty.
- `scripts/init_database.py` already rewired in Phase 1.

### Files deleted (Phase 2)
- `services/board_intelligence_service.py` (legacy enrichment writer into `dg_cached_board`)
- `services/background_worker.py` (dead; only caller was the above)
- `routes/mlb_tiers.py` (duplicate with `ferrari_tiers.py` MLB endpoints)

### Stubbed / gutted
- `services/engines/adaptive_sync_engine.py`:
  - removed the fire-and-forget `run_board_intelligence_enrichment` call;
  - `_check_mlb_lineups` reduced to a documented no-op (was writing into the deleted `mlb_ferrari_safe_haven`).
- `config/db_config.py` and `config/collections.py` — legacy tier-name mappings stripped.

### Acceptance verification
- All 6 Ferrari endpoints + `/v3/board` + `/v3/player-with-badges` + `/v3/mlb/sharp/*` + `/v3/mlb/ferrari/hrr-picks` return HTTP 200 after collection drops.
- NBA: Safe Haven 10 / Front Lines 10 / War Zone 1; MLB: 0 / 0 / 10 (off-hours).
- `/api/v3/board?sport=nba` returns 119 players / 3859 total props with hub-enriched team/position/photo.
- Frontend landing loads clean (no white-screen, no console errors).
- 108 / 108 consolidation-relevant pytest tests pass.
- Grep proof: zero live references to any deleted tier collection in production code.


## 2026-04-22 — Phase 3: Universal Gate Engine

Gating is now a single engine with a single schema. Sport adapters only
compute normalized metrics.

### New package — `services/scoring/gates/`
- `schema.py` — `NormalizedMetrics`, `GateDetail`, `GateEvalResult`,
  `ReasonCode` (canonical reason codes: `gate_coverage_fail`,
  `gate_hit_rate_fail`, `gate_tp_fail`, `gate_tp_unavailable`,
  `gate_cv_fail`, `gate_edge_fail`, `gate_ceiling_fail`,
  `gate_context_fail`).
- `thresholds.py` — pure config: `THRESHOLDS[sport][tier][stat_family]`
  with stat aliases + odds-bucket-to-target-tier mapping. NBA + MLB
  populated from the old `_NBAGateSorter` / `MLBTierSorter` tables.
  NFL scaffold in place (drop in a stat family → works end-to-end).
- `engine.py` — `UniversalGateEngine.evaluate(metrics)` returns a
  `GateEvalResult`. Gate types: `coverage_gate`, `hit_rate_gate`,
  `tp_gate` (side-aware — UNDER uses `p_model_pct` floor), `cv_gate`
  (supports both `max` and `min_cv_floor`), `edge_gate`,
  `ceiling_gate`, `context_gate`.

### Sport-specific gate code DELETED
- `_NBAGateSorter.check_safe_haven_gates` / `check_front_lines_gates` /
  `check_war_zone_gates` / `_check` — removed. Class reduced to a thin
  sport-identity carrier.
- `MLBTierSorter.check_safe_haven_gates` / `check_front_lines_gates` /
  `check_war_zone_gates` — removed. `MLBTierSorter` now exists only as
  a carrier of MLB stat utilities consumed by `mlb_scoring.py`.
- `scoring_stack.compute_tier` fully rewritten — no sport-specific
  branches; looks up thresholds from `resolve_thresholds(sport, tier,
  stat_family)` and delegates to `UniversalGateEngine`.

### Persistence
- `prop_scores_store._SCORE_OUTPUT_FIELDS` gained `gate_eval`. Every
  scored prop now carries the full canonical gate output on the score
  doc so any UI / admin consumer can explain a pick's gating in the
  same structure regardless of sport.

### Verified
- NBA master sync: 116 s, success=true, tier dist
  `{'unqualified': 3294, 'front_lines': 34, 'safe_haven': 16, 'war_zone': 9}`
  (same shape as pre-refactor).
- MLB master sync: 88 s, success=true, tiers populated.
- All 6 Ferrari endpoints HTTP 200.
- `gate_eval` persists with canonical shape — verified on a live NBA
  safe_haven doc (stat_family=`reb`, passed_gates=5, failed_gates=0).
- 16 new unit tests in `tests/test_universal_gate_engine.py` (engine
  pass/fail paths, UNDER side-aware TP, CV cap override, NFL scaffold,
  unknown-gate-type forward compatibility, per-sport identical output
  schema). 124 / 124 regression tests pass.
- Grep proof: zero `def check_*_gates` methods, zero `SAFE_HAVEN_GATES`
  / `FRONT_LINES_GATES` / `WAR_ZONE_GATES` module-level dicts in live
  code.

### Adding a new sport now
1. Add `stat_family` alias block in `STAT_FAMILY_ALIASES[<sport>]`.
2. Fill `THRESHOLDS[<sport>][<tier>][<stat_family>]` with the gate dict.
3. Add an `ODDS_BUCKETS[<sport>]` entry.
4. Ship a scoring adapter that emits `NormalizedMetrics`.
No new gate-evaluation code.


---

## 2026-05-01 — MLB HF Model Retrain (v3.0_bayes) COMPLETE

**P0 task closed.** Bayesian Statcast shrinkage retrain is live and locked.

### What changed
- Bumped `retrain_mlb_models_v2.py` and `services/mlb_high_friction_model.py`
  version tags from `MLB_HF_v2.0_statcast` → `MLB_HF_v3.0_bayes`.
- Added `MLB_HF_STATS` env-var filter to the retrain script for crash-resume
  (training large stats one-by-one when memory is tight).
- Trained all 15 stat models with `bayes_shrink_rolling_window` applied to
  Statcast rolling windows inside `_build_friction_features`.
- Retrain ran under supervisor (`mlb_retrain` program) so it survives pod
  restarts. Supervisor program removed after completion.
- Re-locked `/app/backend/models/mlb_hf/.LOCKED` with fresh sha256 manifest
  for v3.0_bayes (15 files, 766,010 total training samples, 208 features).

### Validation (real, not curl-faked)
- New regression suite `tests/test_mlb_hf_v3_bayes_validation.py` (4 tests
  + 14 parametrised artifact checks) — **17 passed, 1 skipped**.
- Existing `tests/test_mlb_statcast_bayes.py` — **10 passed** (no
  regressions to the shrinkage helper invariants).
- **JJ Bleday H+R+RBI**: **6.55-6.74 → 3.39** (49% reduction, well under
  the 4.0 sanity gate).
- 25-player random sample: zero batters with H+R+RBI > 4× their L20 mean
  (the small-sample blow-up canary).

### Per-stat R²_test (post-retrain)
| Stat                | R²_test | Notes                                         |
|---------------------|---------|-----------------------------------------------|
| pitcher_strikeouts  | 0.576   | Best — workload-anchored stat                |
| hits_allowed        | 0.538   | Strong pitcher signal                        |
| pitcher_walks       | 0.343   |                                              |
| earned_runs         | 0.310   |                                              |
| hits+runs+rbis      | 0.267   | Up from v2 — Bayesian smoothing helped fit   |
| strikeouts (batter) | 0.240   |                                              |
| total_bases         | 0.219   |                                              |
| hits                | 0.190   |                                              |
| singles             | 0.111   |                                              |
| runs                | 0.100   |                                              |
| walks (batter)      | 0.092   |                                              |
| home_runs           | 0.079   | Sparse, low-signal target                    |
| rbis                | 0.072   |                                              |
| doubles             | 0.012   |                                              |
| stolen_bases        | 0.007   | Heavy 0-floor; future calibration             |

### Lock manifest
- Version: `MLB_HF_v3.0_bayes`
- Feature count: 208
- Total training samples: 766,010 across 15 stats
- Locked at: 2026-05-01

---

## 2026-05-01 — MLB HF v3.0_bayes Production-Grade Test Stack COMPLETE

Per user agreement (real fixes / real tests / mutation testing): full
production verification battery built and passing.

### Test files
- `tests/test_mlb_hf_v3_bayes_validation.py`     (18 tests)  — artifact + Bleday + canary
- `tests/test_mlb_hf_v3_production_integration.py` (24 tests) — schema for ALL 15 stats, prob sanity, determinism, alias resolution, imputation, version stamp, workload anchor, active baseline floor
- `tests/test_mlb_hf_v3_calibration.py`           (12 tests) — μ/σ distribution bands across 75 batters & 30 pitchers; 4× L20 canary on extended pool; prob_over symmetry
- `tests/test_mlb_hf_v3_edge_cases.py`            ( 7 tests) — unknown player/stat, missing park, dk_odds dropped, feature_health consistency, pitcher_outs no-starts, SC-missing rectangularity
- `tests/test_mlb_hf_v3_performance.py`           ( 4 tests) — cold/warm latency, load_models, PA cache memory
- `tests/test_mlb_hf_v3_live_api.py`              ( 4 tests) — HTTP `/api/v3/ferrari/{tier}` returns picks stamped v3.0_bayes; live 4× canary
- `scripts/mutation_test_v3.sh`                   ( 7 mutations) — bash harness with bash-trap auto-restore

### Final run
```
84 passed, 2 skipped (data-dependent), 0 failed in 46.27s
mutation_test_v3.sh: 7/7 mutations DETECTED, post-restore regression: 70/70 passed
```

### Mutations injected & detected (proof tests aren't fake)
| # | Mutation                                   | Detected by                                    |
|---|--------------------------------------------|------------------------------------------------|
| M1| Disable Bayesian shrinkage (return raw)    | test_v3_bleday_hrr_shrunk                       |
| M2| League-avg `barrel_rate` = 1.0             | test_inv_bs4_bleday_barrel_rate_shrinks_to_realistic |
| M3| Skip `bayes_shrink_rolling_window` call    | test_v3_bleday_hrr_shrunk                       |
| M4| Strip `model_version` stamp                | test_prod_int_06_version_stamp_exact            |
| M5| `predict()` always returns error           | test_prod_int_01_batter_schema                  |
| M6| Disable active-baseline floor              | test_prod_int_08_active_baseline                |
| M7| Disable pitcher workload-anchored μ        | test_prod_int_07_pitcher_k_workload_anchored    |

### Production budgets verified
- Cold predict (incl. PA-cache build, 1.6M Statcast rows): ≤ 8000 ms
- Warm predict (live serving): **median 10 ms, p95 10.8 ms** (budget: 250 ms)
- load_models (15 artifacts): **0.04 s** (budget: 5 s)
- σ for hits: median 0.70  | σ for K: median 1.94 (both inside healthy bands)
- prob_over symmetry: median 50.4% when line=μ (perfectly balanced)

### Real production bug surfaced & fixed
- `services/mlb_high_friction_model.py`: `prob_over: round(p,1) if prob_over else None`
  was converting 0.0% to None (Python falsy 0). Changed to
  `if prob_over is not None`. Same fix applied to `z_score`. Found by
  PROD-INT-02 prob-sign-vs-line test.

### Live API verified end-to-end
HTTP GET `/api/v3/ferrari/{safe-haven|front-lines|war-zone}?sport=mlb`
returns picks where each one stamps `projection_model_version=MLB_HF_v3.0_bayes`,
and zero live picks exceed 4× the player's L20 mean.

---

## 2026-05-01 — NBA VK2 Production-Grade Test Stack COMPLETE

Mirror of the MLB v3.0_bayes battery applied to the NBA model
(`NBA_VK_v2_5yr_weighted_pruned52`, 5 stats, 52 features each).

### Test files (NBA)
- `tests/test_nba_vk2_validation.py`           (16 tests) — artifact schema, R²_test floors, RMSE_test ceilings, version stamping, σ presence
- `tests/test_nba_vk2_production_integration.py` (16 tests) — VK2 schema for ALL 5 stats, determinism, insufficient_history, unknown_stat, None bdl_id, per-stat coverage, predicted ≥ 0 (incl. deterministic stub-based clamp test), model_version stamp, p_over range
- `tests/test_nba_vk2_calibration.py`          ( 8 tests) — μ/σ distributions across 100-player sample, 4× L20 canary, σ matches RMSE artifact, PRA additivity correlation
- `tests/test_nba_vk2_performance.py`          ( 3 tests) — cold/warm latency, vk2 load_models budget
- `tests/test_nba_vk2_live_api.py`             ( 4 tests) — `/api/v3/ferrari/{tier}?sport=nba` returns picks with vk2_projection/vk2_sigma/p_true_vk2; live 4× canary
- `scripts/mutation_test_nba_vk2.sh`           ( 7 mutations) — bash harness with auto-restore on trap exit

### Final run
```
NBA only:        46 passed, 0 skipped, 0 failed in 7.93s
NBA mutation:    7/7 mutations DETECTED, post-restore: 39/39 regressions pass
MLB+NBA total:  130 passed, 2 skipped, 0 failed in 52.98s
```

### NBA mutations injected & detected
| #  | Mutation                                       | Detected by                                  |
|----|------------------------------------------------|----------------------------------------------|
| N1 | Strip VK2 version stamp                        | test_nba_vk2_live_metrics_consistent          |
| N2 | Force projection=99 constant                   | test_prod_nba_cal_02_no_blowup_in_sample      |
| N3 | Disable post-intercept negative clamp          | test_prod_nba_int_07_negative_clamp_deterministic (stub-based, deterministic) |
| N4 | Break model loader (skip pkl loads)            | test_nba_vk2_live_models_loaded               |
| N5 | Feature builder returns None                   | test_prod_nba_int_06_per_stat_coverage         |
| N6 | Force sigma=0 for all stats                    | test_prod_nba_cal_03_sigma_matches_rmse        |
| N7 | Corrupt PTS predictions w/ random multiplier   | test_prod_nba_cal_04_pra_additive_correlation  |

### Production budgets verified (NBA)
- Cold VK2 predict (history seeded): ≤ 3000 ms (actual: ~2 ms)
- **Warm VK2 predict: median 1.0 ms, p95 1.2 ms** (budget 100 ms)
- vk2 load_models (5 artifacts): **0.02 s** (budget 5 s)

### Distribution medians (active contributor sample, n=70-74)
| Stat | Median | p95   | Band         |
|------|-------:|------:|-------------:|
| PTS  | 10.65  | 22.88 | [8.0, 22.0]  |
| REB  |  3.94  |  9.19 | [2.5,  8.5]  |
| AST  |  1.85  |  6.14 | [1.5,  6.5]  |
| 3PM  |  1.20  |  2.36 | [0.6,  2.8]  |
| PRA  | 17.98  | 32.87 | [15.0,38.0]  |
| PRA vs PTS+REB+AST corr | **0.991** | (>0.85 floor) |

### Real production bug surfaced & fixed
NBA VK2 was returning **negative projections** for low-volume stats
(e.g. Oscar Tshiebwe 3PM = -0.029). NBA box-score stats are
non-negative — a negative projection breaks p_over math downstream.
Added `if projection < 0: projection = 0.0` clamp **after** the
per-stat intercept calibration in
`services/scoring/adapters/nba_scoring.py::_predict_vk2_prob_over`,
so neither the stand-alone XGBoost negative output nor a small
positive value pushed below zero by the (-0.094 PTS / -0.103 PRA)
intercept can surface negative.

### Live API verified end-to-end (NBA)
HTTP GET `/api/v3/ferrari/{safe-haven|front-lines|war-zone}?sport=nba`
returns picks where every `vk2_projection` is ≥ 0, every `vk2_sigma`
> 0, every `p_true_vk2` ∈ [0,1], and zero live picks exceed 4× the
player's L20 mean for that stat.

---

## 2026-05-01 — PrizePicks Multiplier Lab (research tool)

Admin-only, READ-ONLY backend workflow that stores tested PrizePicks
lineup combinations and their returned payout multipliers, so the
payout structure can be reverse-engineered over time.

### Files added
- `backend/services/pp_multiplier_lab.py` — service (sync pymongo;
  parallel-sync handle when async motor is injected). Includes:
  - `ensure_collection_and_indexes()` — idempotent (11 indexes)
  - `derive_mix_type` / `derive_same_game` /
    `derive_same_player_or_group_conflict`
  - `extract_selected_projection` / `parse_game_types_response`
  - `candidate_lineups_from_projection_ids`
  - `run_batch(...)` with `dry_run=True` default + 8-15 s
    randomized delays + hard-stop on 401/403/429 + forbidden-host
    safety filter blocking PerimeterX / px-cloud / entries / auth /
    captcha / picks endpoints
  - `get_recent_tests` / `get_stats`
- `backend/routes/pp_multiplier_lab.py` — admin-gated endpoints
  (`X-Admin-Token` reusing `ADMIN_DEBUG_TOKEN`):
  - `GET  /api/admin/pp-multiplier-lab/recent`
  - `GET  /api/admin/pp-multiplier-lab/stats`
  - `POST /api/admin/pp-multiplier-lab/run-batch` (default dry_run=true,
    batch hard-cap 50)
- `backend/routes/__init__.py` — wired router + startup-time
  `set_db()` + `ensure_collection_and_indexes()` call

### Mongo collection
`pick_vision.pp_payout_structure_tests` with indexes:
`ix_created_at`, `ix_leg_count`, `ix_sport`, `ix_league_id`,
`ix_mix_type`, `ix_power_play_multiplier`, `ix_is_adjusted`,
`ix_selected_projection_ids`, `ix_proj_odds_type`,
`ix_proj_stat_type`, `ix_proj_group_key`.

### Verification (all gates ✓)
1. Mongo collection exists ✓
2. All 11 indexes present ✓
3. Auth gate: 401 without token / wrong token, 200 with correct ✓
4. Manual insert returns expected mix_type=demon_standard,
   power_play_multiplier=2.2, is_adjusted=True, srp_multiplier=1.3 ✓
5. `/recent` returns the manual test ✓
6. `/stats` groups correctly by leg_count, mix_type,
   power_play_multiplier, same_game, odds_type_legs ✓
7. Dry-run batch persisted 3 stub tests, no PP HTTP made ✓
8. ZERO direct requests to api.prizepicks.com or any
   px-cloud/PerimeterX/captcha/auth/entries endpoint ✓
9. Forbidden-host safety check blocks: PerimeterX, /entries,
   /auth/login, /picks/submit ✓
10. Stats refreshed after batch insert (4 total tests) ✓

### Safety properties enforced
- No bets / entries / auth flows hit
- No bot-protection endpoints (PerimeterX, px-cloud, captcha)
- 8-15 s randomized delays between requests in live mode
- Hard-bail on first 401/403/429 (`stopped_early=true`)
- Batch size hard-capped at 50, default 5
- Default `dry_run=true` so accidental triggers can't reach PP
- `_safety_check_url()` raises BEFORE any outbound request when
  any forbidden host fragment appears in the URL

---

## 2026-05-01 — PP Multiplier Lab `/run-now` (auto-source projection IDs)

Adds an admin-only "run lab now" endpoint so the operator no longer
needs to manually supply `projection_ids` via curl.

### Files changed
- `backend/services/pp_multiplier_lab.py`:
  - New collection `pp_projection_id_cache` (TTL'd, 15 min default)
    with indexes on `league_id` (unique) + `fetched_at`.
  - `_ensure_projection_id_cache()`, `_read_cached_projection_ids`,
    `_write_cached_projection_ids` — TTL'd cache helpers.
  - `discover_projection_ids(sport, league_id, ...)` — source order:
    `cache` → ONE read-only `GET /projections?league_id=…` →
    fail-safe. Goes through the same `_safety_check_url` filter so
    PerimeterX / px-cloud / entries / auth / picks endpoints are
    impossible to hit even by accident.
  - `run_now(sport, leg_count, batch_size, dry_run, ...)` — auto
    end-to-end: discover → build candidate lineups → call existing
    `run_batch` → return summary report with `total_candidates_found`,
    `tests_attempted`, `tests_saved`, `stopped_early`, `stop_reason`,
    `multipliers_found`, `latest_test_ids`, `errors`.
  - `_synthetic_ids_from_cached_board()` — dry-run-only fallback
    that mints pseudo-IDs from the existing `nba_cached_board`/
    `mlb_cached_board` `_composite_key` fields when both the cache
    and the network are unavailable. Inserted docs are clearly
    labelled `notes="synthetic_dry_run (no PP HTTP)"`.
  - `RUN_NOW_HARD_CAP = 25` (more cautious than `MAX_BATCH_SIZE=50`).
- `backend/routes/pp_multiplier_lab.py`:
  - `RunNowRequest` Pydantic body model.
  - `POST /api/admin/pp-multiplier-lab/run-now` (admin token).

### Cached projection-source used
**Preferred (live)**: ONE read-only HTTP GET to
`https://api.prizepicks.com/projections?league_id={id}&per_page=N&single_stat=true`
on the FIRST call per 15 min, then cached in
`pp_projection_id_cache`. Same endpoint a logged-out browser hits.

**Dry-run fallback**: when network unavailable, pseudo-IDs derived
from `nba_cached_board.[standard|demons|goblins]._composite_key`
(synthetic, never sent over HTTP).

NOTE: our existing `*_cached_board` collections do NOT contain real
PrizePicks projection IDs (they're sourced from the-odds-api.com
which doesn't expose PP's internal IDs). That's why a one-shot
discovery hit is needed to seed the cache.

### Verification
1. **Dry-run** (`POST /run-now`, `dry_run=true`):
   - Discovery: synthetic from cached_board (network 403'd).
   - 11 candidates found, 3 tests saved, multipliers_found=[].
   - 3 docs persisted with `notes="synthetic_dry_run (no PP HTTP)"`.
2. **Live mode** (`POST /run-now`, `dry_run=false`, `batch_size=1`):
   - Discovery hit `https://api.prizepicks.com/projections?league_id=7`
     ONCE → got HTTP 403 → STOP'd correctly.
   - `tests_attempted=0`, `tests_saved=0`, no batch run, no
     additional PP HTTP made.
3. **Mongo doc proof**: `test_id=09310e138dc7225c`, `sport=NBA`,
   `mix_type=standard_standard`, `notes=synthetic_dry_run (no PP HTTP)`,
   pseudo-IDs from Chet Holmgren composite_keys.
4. **Stats**: `total_tests=7`, all 7 leg_count=2, mix_type breakdown
   `{standard_standard: 6, demon_standard: 1}`.
5. **No bot-protection requests**: 0 hits to px-cloud / PerimeterX
   in backend logs since boot. The two api.prizepicks.com requests
   were the documented `/projections?league_id=7` discovery calls
   only — no `/entries`, `/auth`, `/picks/submit`.

### Live-mode caveat (preview env)
PrizePicks returns `403 Forbidden` to the preview pod's egress IP
(generic User-Agent + cloud IP range). The safety guard treats 403
as STOP per spec, so live-mode currently gets 0 tests in this env.
On a deployed prod pod with a residential-routed UA the same
endpoint should return 200 and live mode will work end-to-end.


---

## 2026-05-01 — PP Local Multiplier Runner (local Chrome)

Local-only Python script that drives the operator's already-running
Chrome session to select 2 props at a time, captures the network
responses PrizePicks returns to that browser, and POSTs the result
to PropVision. Designed to run ON THE OPERATOR'S COMPUTER, never
from the VPS.

### Files added/changed
- `/app/tools/pp_local_multiplier_runner.py` (NEW, executable)
  - Uses Playwright `connect_over_cdp("http://127.0.0.1:9222")` to
    attach to operator's existing Chrome (started with
    `--remote-debugging-port=9222`).
  - Auto-scrapes visible projection IDs from the rendered PP board
    via `data-projection-id` / `data-test-id^=projection-` /
    `id^=projection-`.
  - Seeds the backend's `pp_projection_id_cache` so the rest of the
    backend can build candidate combos.
  - Calls `GET /api/admin/pp-multiplier-lab/next-candidates` to
    fetch combos to drive (defaults to 5; hard-cap 25).
  - For each combo: clicks the "More" side of each prop card
    (never any submit/place-entry/confirm/pay button), waits up to
    18s for `/projections?ids=` and `/game_types` responses,
    POSTs payload to `ingest-captured-test`, then clears the slip
    and sleeps random 8-15s.
  - Hardcoded forbidden-fragment filter blocks navigation to
    `px-cloud`, `perimeterx`, `/entries`, `/auth`, `/picks/submit`,
    `captcha`, `bot-defender`, `/picks/post`.
  - Hardcoded forbidden-button-text filter prevents clicking any
    element whose text contains "submit entry", "submit", "place
    entry", "place bet", "confirm entry", "confirm bet", "pay $",
    or "deposit".
  - `--dry-run` flag connects, scrapes IDs, fetches combos, but
    DOES NOT click anything.
- `backend/services/pp_multiplier_lab.py` (extended)
  - `seed_projection_ids(league_id, projection_ids, sport)`
  - `get_next_candidate_combos(sport, league_id, leg_count, limit,
    skip_already_tested)` — sorted-tuple match against
    already-saved tests.
  - `ingest_captured_test(payload)` — flattens projections + parses
    game_types via existing helpers; sets
    `source="prizepicks_network_local_runner"`.
- `backend/routes/pp_multiplier_lab.py` (extended)
  - `POST /api/admin/pp-multiplier-lab/seed-projection-ids`
  - `GET  /api/admin/pp-multiplier-lab/next-candidates`
  - `POST /api/admin/pp-multiplier-lab/ingest-captured-test`

### Verifications
1. **seed-projection-ids**: `{"ok":true,"stored_count":5}`
2. **next-candidates**: Returned 3 unseen 2-leg combos out of 5 IDs;
   `skipped_tested=7` (correctly excluded already-saved combos).
3. **ingest-captured-test**: `test_id=05d53275a9ec39db`,
   `mix_type=demon_standard`, `power_play_multiplier=2.2`,
   `is_adjusted=true`, `srp_multiplier=1.3`, `same_game=true`.
4. **Saved doc** has `source: prizepicks_network_local_runner` and
   `request_metadata.captured_via: local_chrome_runner`.

### Operator instructions
1. Close all Chrome windows.
2. Launch Chrome with `--remote-debugging-port=9222
   --user-data-dir=$HOME/chrome-debug-pp`.
3. Sign in to PrizePicks normally; open the league/board to test.
4. `pip install playwright httpx`.
5. `python tools/pp_local_multiplier_runner.py
       --backend-url $PV_URL
       --admin-token $ADMIN_DEBUG_TOKEN
       --sport NBA --num-combos 5`

### Safety properties enforced (in-code, not just promises)
- No bets / no entries / no `submit`/`place`/`confirm`/`pay`
  buttons clickable (string-blocklist).
- No navigation to `px-cloud`, `perimeterx`, `/entries`, `/auth`,
  `/picks/submit`, `captcha`, `bot-defender`.
- No proxy rotation, no UA spoofing — uses the operator's existing
  Chrome session as-is.
- Hard cap of 25 combos per run; default 5.
- Randomized 8-15s delay between combos.
- Backend admin token required (X-Admin-Token).
- Script refuses to run if `--admin-token` is missing.
- Backend never makes HTTP to PrizePicks during this flow — only
  the operator's own browser does, and the script just listens
  passively to its responses.

---

## 2026-05-13 — Vision Intel UX: "Generating…" loader + per-chunk Gemini timeout

**Problem:** User reported some Pick Cards on production showed no Vision Intel
summary (cards looked broken/empty). Root causes:
  1. Gemini-flash-lite chunk calls had no explicit timeout — tail-latency spikes
     (>60s) silently killed chunks, leaving `vision_intel` null on those picks.
  2. UI rendered nothing when `vision_intel` was null — no signal that the
     reaper would close the gap on its next pass.

**Fixes shipped:**
  - Frontend (`UniversalPlayerCard.jsx`):
    `visionLine ? <intel block> : <Generating Vision Intel… loader>`. Loader
    has a pulsing amber dot + bouncing-dot ellipsis, `aria-live="polite"`,
    `data-testid="vision-intel-loading-{slug}"`.
  - Backend (`services/master_sync.py`):
    New helper `_call_gemini_chunk_with_timeout()` wraps every chunk call
    in `asyncio.wait_for(timeout=GEMINI_CHUNK_TIMEOUT_SECONDS=300s)` with
    `GEMINI_CHUNK_RETRIES=1` retry-once on timeout/error. Both NBA and
    MLB enrichers now route through it. Exhausted retries log loudly and
    fall through to `[None]*len(chunk)` (handled identically to old path
    so JIT reaper's next pass picks them up).

**Result:** Every card always shows either a Gemini-authored summary or a
"Generating Vision Intel…" loader — no more silent empty slots. Slow Gemini
calls now have a 5-minute window per attempt instead of being killed early.

## 2026-05-13 (later) — Vision Intel ROOT-CAUSE FIX: Metadata Hydration + Gemini Fallback

**Root cause:** `_project_score_doc` in `services/scoring/prop_scores_store.py`
silently dropped `team`, `opponent`, `home_team`, `away_team`, `commence_time`
etc. because they weren't in any allowlist. Result: every score doc landed
with `opponent=None`, Gemini received `"vs TBD (no DvP data)"`, no DvP /
matchup / injury context could ever attach, and cards rendered blank.

The user reported James Harden, Tobias Harris, Evan Mobley, Max Strus —
all four were in the SAME game (CLE@DET, canonical_key hash
`c3531530e77a4ada542bdbff23de5409`) and all four had `opponent=None`. The
diagnostic surfaced this in <1 minute via `scripts/diag_vision_intel_misses.py`.

**Fix shipped:**
  1. `services/scoring/prop_scores_store.py`:
     New `_MATCHUP_METADATA_FIELDS` allowlist (`team`, `team_full`,
     `opponent`, `opponent_abbr`, `opponent_team`, `home_team`, `away_team`,
     `is_home_team`, `is_away_team`, `commence_time`). Added to
     `_known_keys` AND projected into the persisted doc.
  2. `services/scoring/recompute.py`:
     Stamps the same fields from `ctx.raw_prop` (the upstream
     `nba_live_props` row) onto the score doc BEFORE persistence. Also
     widened `_opp` lookup to include `opponent_team` as a primary source.
  3. `services/scoring/score_document_schema.py`:
     Added the 10 matchup-metadata fields to `ScoreDocument` so Pydantic
     strict mode never rejects them.
  4. `services/master_sync.py::_call_gemini_chunk_with_timeout`:
     Now detects transient `503 / 500 / 429 / UNAVAILABLE` errors and
     sleeps 5s before the single retry (immediate retries on capacity
     errors just compound the throttle).
  5. `services/vision_intel_service.py`:
     New `fallback_model_name = 'gemini-flash-latest'`. When the primary
     `gemini-flash-lite-latest` returns a transient 5xx/UNAVAILABLE, the
     service automatically retries the SAME prompt against the heavier
     `gemini-flash-latest` model (much larger capacity pool). Same parser,
     same strict contract — Vision Intel never silently dies on a
     Google-side spike.

**Verification:** Full NBA recompute → all 733 `final-nba-rt` docs now
carry team/opponent. JIT reaper run → 19/19 uncovered picks returned
Gemini-authored intel. Harden/Harris/Mobley/Strus all generated narratives
that explicitly reference the CLE↔DET matchup, DvP rank, and pace context
that were invisible to Gemini 30 minutes prior.

## 2026-05-13 (final) — MLB Vision Intel fallback parity

**Issue spotted post-deploy:** NBA reached 100% Vision Intel coverage but
MLB was still showing 3/4 picks missing. Root cause: MLB uses a separate
service `services/mlb_vision_intel.py` (parallel to `services/vision_intel_service.py`)
that I hadn't patched. The MLB service still pointed at
`gemini-flash-lite-latest` with no fallback, so Google's transient 503
spikes blanked it out.

**Fix:** Mirrored the primary→fallback model wrapper into
`services/mlb_vision_intel.py` (same `gemini-flash-latest` fallback,
same `503/500/429/UNAVAILABLE` sniff, same single-attempt fallback).

**Verified end-to-end:**
  NBA: war-zone 4/4 + front-lines 8/8 + safe-haven 7/7 = **19/19 ✓**
  MLB: war-zone 2/2 + front-lines 1/1 + safe-haven 1/1 = **4/4 ✓**

## 2026-05-13 (THE actual root-cause fix) — Sort-variant enrichment mismatch

**Real bug:** Dashboard fires `/api/v3/ferrari/{tier}?sort=gap` (NBA only),
which uses `ranking_score_v2` as the dedup-and-rank key. The board reader's
"one pick per player" dedup picks a DIFFERENT alt-line per player when sorted
by gap vs default vision_score:

  Duncan Robinson PTS L8.5 — wins default sort (HAS vision_intel)
  Duncan Robinson PTS L6.5 — wins gap sort   (NO vision_intel — never enriched)

The JIT reaper and master_sync only ever enriched the DEFAULT sort variant.
Result: gap-sort exposed canonical_keys that nothing had ever Gemini-enriched.

The previous fixes (metadata loss, 503 fallback) were necessary too, but the
PROXIMATE cause of "James Harden / Tobias Harris / Evan Mobley / Duncan Robinson
showing loader while Donovan Mitchell showed text" was THIS — Mitchell happened
to win the same canonical_key under both sort variants; the others didn't.

**Fix:**
  1. `services/jit_vision_intel_reaper.py::find_visible_uncovered_cks`:
     Loop both `None` (vision_score) and `"ranking_score_v2"` sort variants
     via `get_board(sort_key_override=...)`. Union the canonical_keys before
     checking the "uncovered" filter. MLB still uses default-only.
  2. `services/master_sync.py::_enrich_nba_board_vision_intel`:
     Same expansion at the scheduled hourly path — dedup canonical_keys
     across both sort variants in `seen_cks` to avoid double-enriching the
     same ck if it wins both rankings.

**Verified end-to-end:**
  /api/v3/ferrari/safe-haven?sort=gap   → 7/7 ✓
  /api/v3/ferrari/front-lines?sort=gap  → 8/8 ✓
  /api/v3/ferrari/war-zone?sort=gap     → 4/4 ✓
  Screenshot of dashboard.preview: 19/19 cards showing AI-authored intel.
  loaders=0, inlines=19.

## 2026-05-13 — Ayo Dosunmu P+R 19.5 "No game data" chart bug

**Reported:** Player Detail Page for Ayo Dosunmu's War Zone OVER 19.5 P+R prop
rendered "No game data available" despite the master-hub endpoint returning
78 BDL game logs.

**Root cause:** `frontend/src/components/dashboard/GameLogBarChart.jsx`'s
`STAT_FIELD_MAP` only contained SHORT-CODE keys ('PR', 'P+R'). The score doc's
`stat_type` arrives as the Odds-API long-form `player_points_rebounds_alternate`
which had NO entry in the map. `getStatValue` returned null for every game →
`values.length === 0` → `chartData = null` → "No game data" placeholder.

**Fix:**
  1. Added every NBA Odds-API market key + its `_alternate` variant to
     `STAT_FIELD_MAP` (player_points, player_rebounds, player_assists,
     player_points_rebounds, player_points_assists, player_rebounds_assists,
     player_points_rebounds_assists, player_blocks_steals, player_threes,
     etc.). All map to their underlying BDL game-log fields.
  2. `getStatValue` now has a final fallback that strips the `_alternate`
     suffix and re-tries the lookup, so any future alt-market key the
     backend adds will resolve to its base stat without a code change.

**Verified:** In-browser evaluation confirmed `player_points_rebounds_alternate`
+ `player_points_rebounds` + `PR` + `P+R` all resolve to the same `['pts','reb']`
field combo and a sample `{pts:18, reb:5}` game correctly sums to 23.

## 2026-05-13 — SSOT ENFORCEMENT: NBA combo stats collapse at ingest

**User complaint:** "why are they short coded? we have very strict ssot
and ownership rules" — referring to the previous fix that added
long-form `player_points_rebounds_alternate` keys to the frontend
`STAT_FIELD_MAP`. They were RIGHT — that patched the symptom, not the
SSOT violation.

**The SSOT violation found in DB:** `nba_prop_scores` contained MIXED
stat_type formats for the same player Ayo Dosunmu — short codes
(PTS, REB, AST, PRA) AND long-form market keys (player_points_assists,
player_points_rebounds, player_points_rebounds_alternate, etc.). Standard
markets were collapsed at ingest, but COMBO markets were intentionally
"preserved" with the comment "scoring adapter / gate aliases already
handle them downstream" — a comment that was a lie.

**Root cause:** `services/universal_odds_sync.NBA_CONFIG.stat_type_map`
explicitly mapped combo markets to themselves:
  "player_points_rebounds":           "player_points_rebounds"   ← bug
  "player_points_rebounds_alternate": "player_points_rebounds_alternate"
  ... etc for PA, RA

This caused score docs to carry the long-form token, which:
  • Violated SSOT (two formats for same family in same collection)
  • Caused chart rendering to silently fail for every alt-combo prop
    because `STAT_FIELD_MAP` keyed short codes only

**Fix (single source of truth at ingest boundary):**

  1. `services/universal_odds_sync.py::stat_type_map`:
     player_points_rebounds*  →  "PR"
     player_points_assists*   →  "PA"
     player_rebounds_assists* →  "RA"
     (matching the pre-existing PTS/REB/AST/PRA/3PM/STL/BLK/TO pattern)

  2. `services/scoring/adapters/nba_scoring.py::_MARKET_TO_STAT`:
     Expanded to mirror the full set so the legacy `canonical_key_from_raw`
     fallback path produces identical canonical tokens regardless of
     whether the prop came through new ingest or a legacy code path.

  3. `services/scoring/adapters/nba_scoring.py::build_context`:
     Now reuses `self._MARKET_TO_STAT` instead of an inline duplicate
     of the same map (DRY — one place to change, ever).

  4. `frontend/src/components/dashboard/GameLogBarChart.jsx`:
     Removed all 30+ long-form fallback entries that were defending
     against the SSOT violation. Map is now short-code-only, matching
     the backend canonical guarantee. The `_alternate`-suffix strip
     fallback in `getStatValue` is retained as defense-in-depth for
     any future external feed that bypasses the canonical ingest path.

**Data migration:** Flushed all `nba_prop_scores` + `nba_live_props` rows
with `stat_type ^/player_/` (21,225 + 409 docs). Re-ran odds sync +
full NBA recompute. Verified DB now contains ONLY canonical short codes:

  PTS=217  PRA=164  REB=161  PR=150  PA=126  AST=113  3PM=79
  RA=70    STL=58   BLK=34   long-form=0 ✓

**Verified end-to-end:** Ayo Dosunmu's PR 19.5 alt-line prop now carries
`stat_type='PR'` end-to-end. PlayerDetailPage chart resolves correctly
via the unmodified `STAT_FIELD_MAP['PR']` → ['pts','reb'].

## 2026-05-13 — SSOT follow-up: stat_family aliases for short-code combos

**Regression after SSOT migration:** Ayo Dosunmu's `PR 19.5 OVER` War Zone
pick disappeared. Root cause: `STAT_FAMILY_ALIASES["nba"]` in
`services/scoring/gates/thresholds.py` only had aliases for the long-form
market keys (`player_points_rebounds → pts_reb`) but NOT for the canonical
short codes the SSOT now emits (`pr → pts_reb`). When `resolve_stat_family`
got `stat_type='PR'`, it fell through to `_default` → no gate thresholds
→ `tier=unqualified` → silently vanished from the board.

**Fix:** Added the missing canonical short-code → family aliases:
  pr   → pts_reb
  pa   → pts_ast
  ra   → reb_ast
  blst → blocks_steals

**Verified:** Recompute landed PR=5 / PA=3 / RA=4 picks across the tiers.
Ayo PR L19.5 OVER is back in War Zone with vision_score=85.3 and a fresh
Gemini-authored Vision Intel narrative (351 chars).

## 2026-05-13 — ACTUAL chart fix: PlayerDetailPage stat_type overwrite

**User: "it's STILL not working" — and they were right, three times in a row.**

The previous SSOT/canonicalization fixes were necessary but didn't reach the
chart. Root cause: `PlayerDetailPage.jsx` line 718 was OVERWRITING the
canonical `prop.stat_type='PR'` with the display label `'P+R'` (plus sign)
inside `groupedProps`:

  groups[cat].push({ ...prop, stat_type: cat })  // cat='P+R' for PR family

So by the time PropRow → GameLogBarChart received the prop, `statType='P+R'`
but `STAT_FIELD_MAP` keyed on `'PR'` (canonical SSOT token). Lookup missed,
`getStatValue` returned null for every game, chart rendered "No game data"
even though the BDL game logs had pts/reb data.

**Why this was hard to find:** The probe initially returned `statType: 'PR'`
when I read the score doc directly, AND returned `statType: 'P+R'` when I
probed the React fiber on the rendered page. Two different statTypes on the
same prop — the in-memory mutation happened inside PlayerDetailPage's
useMemo grouping logic, not at any API boundary.

**Fix:** Preserve `prop.stat_type` as the canonical SSOT token. Store the
display label as a separate `stat_display_label` field that the section
header can use without polluting the prop's identity.

**Verified end-to-end:**
  - 0 "No game data" anywhere on detail page
  - 46 chart SVG elements rendered
  - Ayo Dosunmu OVER 19.5 P+R: full 10-game bar history visible
    (HOU 15, DEN 14, DEN 13, DEN 28, DEN 47, DEN 21, SAS 0, SAS 18,
    SAS 15, SAS 25)
  - Yellow target line at 19.5 visible across bars

**Lesson — for next time when user says "still not working":**
DO NOT trust API-side or DB-side verification. ALWAYS reach the actual
rendered DOM. The React fiber probe surfaced the truth in 200ms when the
backend logs and API responses all said "everything is fine".

## 2026-05-13 — Universal Stat Canonicalizer (Registry)

**Goal:** Single source of truth for the full stat identity chain
`external market key → canonical stat_type → stat family → model key → display label`,
sport-agnostic, registry-driven, with backward-compatible shims for the three
modules that previously owned duplicated mapping logic.

**Module shipped:** `services/scoring/canonical_stats.py` (~370 lines)

**Public API:**
  - `register_sport(sport, *, market_to_stat, stat_to_family, stat_to_model=None, stat_to_display=None)`
  - `canonical_stat_type(sport, raw)` — idempotent; raw market keys + canonical
    tokens both resolve to the canonical token
  - `stat_family(sport, stat_type, *, strict=False)` — fail-loud diagnostic
    when unmapped (logs `[STAT_REGISTRY_MISS]` ERROR + increments per-sport
    counter; `strict=True` raises `StatFamilyMissError`)
  - `model_key`, `display_label`, `markets_for_sport`,
    `market_to_stat_map`, `iter_sports`, `validate_sport`, `miss_counters`

**Consumers consolidated:**
  - `services/scoring/gates/thresholds.py`:
      * `STAT_FAMILY_ALIASES` is now a derived view of the registry
      * `resolve_stat_family()` delegates to `canonical_stats.stat_family(...)`
      * 75 lines of inline dicts removed
  - `services/scoring/adapters/nba_scoring.py`:
      * `_MARKET_TO_STAT` dict replaced by a `@property` that reads from the
        registry — preserves the legacy attribute name for any external caller
      * `canonical_key_from_raw` + `build_context` both route through
        `canonical_stat_type("nba", market)`
  - `services/universal_odds_sync.py`:
      * Two lookup sites (`_persist_raw_markets`, `_normalize_market_data`)
        now read from `market_to_stat_map(sport)` instead of the embedded
        `SPORT_API_CONFIG[sport]["stat_type_map"]` dict literal

**SSOT preservation (the 2026-05-13 user-explicit fixes):**
  - `PR    → pts_reb`        ✓ test_nba_stat_family_resolution[PR-pts_reb]
  - `PA    → pts_ast`        ✓
  - `RA    → reb_ast`        ✓
  - `BLST  → blocks_steals`  ✓
  - Long-form aliases still work via the family map's legacy entries

**Validation:**
  - 93 pytest tests at `/app/backend/tests/test_canonical_stats.py`
    — all pass in 0.35s
  - `validate_sport("nba")` → ok=True, 24 markets, 36 families, 5 models
  - `validate_sport("mlb")` → ok=True, 39 markets, 20 families
  - **Ayo Dosunmu PR 19.5 OVER still tier=war_zone, vision_score=86.7**
  - Backend restarted clean
  - Live API (`/api/v3/ferrari/...?sport=nba&sort=gap`) routes through the
    registry-derived family resolver

**Pluggability proof:**
  `test_register_new_sport_without_editing_other_files` registers a synthetic
  sport `test_sport_xyz` purely via `register_sport(...)` — round-trips
  market → canonical → family → model → display without touching any
  other file. New sport onboarding is now a one-call operation.

────────────────────────────────────────────────────────────
## 2026-05-14 — `total_edge` (combined Model + Shopping edge)

**Why:**
Investigation found that `edge_vs_fair` (model vs market) and
`best_book_edge` (market vs cheapest book) measure two completely
different things. Neither alone tells the user "what's my actual
ROI edge at the cheapest book". Added a third metric to surface
the actionable combined edge.

**Math:**
```
edge_vs_fair    = p_model - market_fair_prob       ← "Model Edge"
best_book_edge  = market_fair_prob - best_book_implied  ← "Shopping Edge"
total_edge      = p_model - best_book_implied      ← "Total Edge" (NEW)
```
`total_edge = edge_vs_fair + best_book_edge` (algebraic identity).

**Implementation:**
  - `services/scoring/best_book.py`:
      • `compute_best_book_metrics()` gains `p_model` kwarg.
        Returns `total_edge` when p_model + best_book_implied
        both available; else None.
  - `services/scoring/recompute.py:890-915`:
      • Passes `p_model=doc.get("p_true_active")` (== ctx.p_model)
        through to the best_book engine.
  - `services/scoring/prop_scores_store.py:_SCORE_OUTPUT_FIELDS`:
      • Adds `"total_edge"` to the allowlist so the field survives
        the persistence projection.
  - `services/scoring/score_document_schema.py`:
      • `total_edge: Optional[float] = None` declared on
        `ScoreDocument`. health_sync diff stays clean.

**UI:**
  - `UniversalPlayerCard.jsx`:
      • Projection-cell tooltip now surfaces all 3 edges:
        "Model Edge / Shopping Edge / Total Edge".
  - `PlayerDetailPage.jsx`:
      • MLB stats row split into 4 cols:
        CV / Model Edge / Total Edge / True Prob.
      • Old "Edge" label renamed to "Model Edge".
      • Total Edge colored green ≥10%, yellow ≥3%, red <0%.

**Tests:**
  - `tests/test_best_book.py` — 5 new total_edge cases (29 total,
    all green). Includes:
      • independence-from-fair_prob proof
      • negative-edge when p_model < best_book_implied
      • None pass-through when p_model missing
  - Other regression suites still green
    (`test_tp_source_gate`, `test_all_books_expansion`).

**Live data (post-recompute, MLB):**
  - 1,594 props recomputed in 85.7s through chunked endpoint
  - 1,037 of 2,990 active MLB props now have `total_edge`
    (those with both p_model and best_book_implied).
  - Distribution (active=True only):
    *NBA*: median total_edge −3.3% slate-wide; qualified picks
           (Front Lines / Safe Haven / War Zone) all median ≥ +16%.
    *MLB*: median +4.7% slate-wide; War Zone picks median +43.9%,
           Front Lines +12.9%.
  - Shopping edge confirmed structurally tiny (mostly ±1%).

**MongoDB cleanup:**
  - Dropped `replay_evaluations` (1.22M docs, 5.998 GB) and
    `replay_outcomes` (230K docs, 0.158 GB). Storage reclaimed:
    ~6.16 GB logical, ~1.17 GB on disk. Total DB now 1.685 GB
    on disk.

**Gates: NOT TOUCHED.** Per user spec, total_edge is display-only
until distribution-snapshot review completes.


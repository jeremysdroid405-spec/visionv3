# Prop Vision — Product Requirements Document

## Original Problem Statement
Restructure React/FastAPI betting app to a 100% Local-First Database Model with multi-sport support. Implement Google/Apple OAuth and Stripe for payments. PRODUCT REQUIREMENTS: 100% ID-based joins. Universal Opportunity Models and Probability modeling. Enforce regression and mutation tests for all backend logic. FIX PROPVISION PERMANENTLY — SSOT ENFORCEMENT / FIELD OWNERSHIP HARDENING. Transition architecture to strict Single Source of Truth for all user-visible fields.

**ACTIVE DIRECTIVE: PROP VISION STABILIZATION PLAN**
Freeze all feature/UI work until the system is permanently stabilized via the 6-phase plan.

## Stabilization Status
- Phase 4B SSOT cleanup — DONE
- §3 Tier freshness stamping (`board_freshness.py`) — DONE
- §4 Detection source freshness SLO logic — DONE
- §6 Vision Intel coverage (universe alignment + dual-tag mirror writes) — DONE
- JIT Vision Intel Reaper (5-min cadence) — DONE
- Universal Badge Architecture (`badge_enrichment.py`, `mlb_environmental_badges.py`) — DONE
- 0-write guard in `prop_scores_store.write_versioned_scores` — DONE
- **Breaking News Ticker stabilization (2026-05-08) — DONE**
- **Combo-family VK2 routing fix (2026-05-09) — DONE**
- **WZ coverage decoration bypass fix (2026-05-10) — DONE**
  - Root cause: `services/board/engine.py::on_new_props` (real-time scoped ingest, dominant publish path) loaded raw `{sport}_live_props` and passed them directly to `recompute_sport(props=matched)`, bypassing `adapter.load_live_props`'s universal 3-step decoration (`filter_priceable` + `build_companion_map` + `filter_pp_playable`). Every real-time-written row landed in `{sport}_prop_scores` with `book_count=None`/`coverage_class=None`/`books_anchored=None`, and downstream `coverage_gate` fail-closed on `actual=None vs threshold=1`, torching WZ tier supply.
  - Fix: `recompute_sport` now applies the canonical 3-step decoration on caller-supplied props before the build-context loop (mirroring `load_live_props` exactly). Companion map built over the full live pool. Defence-in-depth fallback on decoration failure.
  - Production validation (NBA): FD-anchor missing `coverage_class` 70.2% → **0%**; `gate_coverage_fail` 1,059 → 4 (99.6% reduction).
  - Production validation (MLB): FD-anchor missing `coverage_class` 87.6% → **0%**; `gate_coverage_fail` 876 → **0**; WZ qualified 63 (pre-fix) → 58 (post-fix, same slate).
  - 4 regression tests (`tests/test_recompute_caller_supplied_decoration.py`); 36/36 scoring/coverage/recompute tests pass.
  - Audit: `/app/audit_reports/wz_coverage_decoration_fix.md`.
  - Note: NBA WZ qualified remained 0 on the post-fix slate because the model legitimately disagrees with the +150+ side on ~98% of OVER candidates (only 1 row across 3 games had proj/line >= 1.0, and that one failed `gate_hit_rate_fail` at HR=45% < 50% threshold). Coverage decoration is no longer the bottleneck; supply is now gate-limited as designed.

### Replay Test Suite
- Phase 0-2 (snapshot plan, 30-day NBA ingest, resolver, replay engine) — DONE
- **Phase 2.5 step 1 — Historical VK2 wired (2026-05-09) — DONE**
  - `services/replay/vk2_historical.py` reuses production model pickles +
    `nba_vk2_features.build_features` (no fork). PlayerIdResolver, leakage-
    gated history & adv-stat slices, single-stat predictor, combo synth.
- **Stage A→B→C fast-iteration architecture (2026-05-09) — DONE**
  - Stage-A immutable, Stage-B `replay_vk2_cache` carries every expensive
    payload, Stage-C `scoring_only.run_scoring_only` re-scores in <5min/500k rows.
- **Historical Matchup / Pace / DvP layer (2026-05-09) — DONE**
  - `services/replay/matchup.py`; `matchup_blob` persisted on Stage-B cache.
- **Historical Injury / Usage layer — Part 3 of Safe Haven Fix (2026-05-09) — DONE**
  - `services/replay/injury_history.py` reconstructs OUT lists, `usage_vacuum_factor`,
    `usage_spike` magnitude/flag, `key_player_out_flag`, `rotation_compression`
    strictly from `bdl_historical_game_logs` (no live injury source needed).
  - Production formula match: `usage_vacuum_factor = 1 + Σ(out_usage_l10) / Σ(top13_usage_l10)`
    using the production `(fga + 0.44·fta + tov)/min · 36` proxy.
  - Stage-B cache rows now carry `injury_blob` + flat `usage_vacuum_factor` /
    `usage_spike_flag` shortcuts; Stage-C reads them and stamps
    `prop["usage_vacuum_factor"]` / `prop["usage_spike"]` exactly as production.
  - `cache.injury_pipeline_hash()` now content-hashes the new module → invalidation
    rule wired (`injury_blob` field).
  - 12 new pytest tests; 64/64 replay tests pass.
  - End-to-end smoke: 304/308 props `injury_full`, avg vacuum 1.065, 21
    spike-flagged. `parity_warnings` no longer mention injury or matchup.
  - Audit: `/app/audit_reports/replay_injury_persistence_arch.md`.
  - **Next**: full-window replay + Safe Haven debug script to confirm
    activation. Production scoring/gates UNTOUCHED; replay-only writes.
  - `services/replay/engine.py` accepts `enable_vk2=True`; stamps
    `vk2_projection / vk2_sigma / vk2_p_over / vk2_model_version /
    vk2_feature_hash / vk2_adv_coverage_l10`; passes `p_model = vk2_p_over`
    (NOT TP) to `compute_scoring_stack`. Unsupported families
    (BLK / STL / TURNOVERS) marked `vk2_unsupported_family` — no VK1 fallback.
  - First end-to-end run (run_id `vk2_full_30d_1778310068`):
    - 517,864 candidates, 2,013 qualified (0.39%), 399 settled qualified picks.
    - Front Lines: 179 picks, HR 83.2%, ROI **+41.1%/u**, +$73.54.
    - War Zone:   220 picks, HR 62.7%, ROI **+69.2%/u**, +$152.17.
    - Safe Haven: 0 picks (Feb-2024 has zero `bdl_advanced_stats` — VK2
      vision_score gate (>= 80) compresses without adv features).
    - Combined: 399 picks, HR 71.9%, ROI **+56.6%/u**, +$225.71.
  - Before/after vs prior partial-parity run: qualified count 0 → 399.
  - Tests: `tests/test_replay_vk2_historical.py` (13 tests, all passing).
  - Reports: `/app/audit_reports/replay_publication_vk2_full.md`,
    `/app/audit_reports/replay_vk2_before_after.md`,
    `/app/audit_reports/vk2_production_map.md`.
  - **NOT production sign-off** — injury / matchup / pace still stubbed;
    SH coverage requires adv_stats backfill for the replay window.
- **Forward-testing lineage boundary (2026-05-09) — COMMITTED**
  - PA / PR / RA combo synth now uses VK2 component μ instead of legacy VK1.
  - Allen PA alt 9.5: μ 21.89 → 12.95 (verified live).
  - Aggregate Δμ: PA −1.51, PR −0.92, RA −0.54 (n=687 combo props rescored).
  - 60 combo props now correctly fail direction-gate; 1 was fake-passing WZ (Allen PA alt 14.5).
  - Post-fix sensitivity: 0 WZ rejects in vision_score_v2 [55,60) — floor move 60→55 yields zero gain. Floor stays at 60.
  - Tests: `tests/test_combo_synth_vk2_routing.py` (8 tests, all passing — incl. mutation guard).
  - Audit: `/app/audit_reports/wz_alt_line_projection_audit_2026-05-09.md`.
- **Forward-testing lineage boundary (2026-05-09) — COMMITTED**
  - `MODERN_SSOT_CUTOFF = 2026-04-25`. New `services/forward_testing_lineage.py` provides `lineage_filter`, `merge_filter`, `lineage_metadata`.
  - Endpoints `/v3/forward-test/{performance,daily,calibration,status}` excludes legacy `vk_*` rows by default; `?include_legacy=true` opt-in returns mixed-generation warning.
  - Every reporting response carries `dataset_lineage` block (generation, cutoff, row counts, excluded count, warning).
  - 17 new tests in `tests/test_forward_testing_lineage.py` (68/68 stabilization tests passing).
  - No raw historical data deleted, mutated, or backfilled; no scoring/gates/tiers/settlement/odds-routing changes.
  - Audit: `/app/audit_reports/fl_goblin_lineage_findings_2026-05-09.md`.
- **NBA reference-odds chain port (2026-05-09) — COMMITTED**
  - `_pick_reference_odds` NBA branch extended from `dk → mgm` to `dk → fd → mgm → bol`.
  - 18 new tests (`tests/test_reference_odds_chain.py`); 51/51 stabilization tests passing.
  - `no_reference_market` rejects: 1234 → 0 on common keys (full recovery).
  - Tiered (common keys): 29 → 63 (+34, +117% supply).
  - 35 newly tiered props this slate: 28 via new FD chain link, 1 via BOL, 6 via dk via slate refresh.
  - 5/5 random regression check: already-tiered props preserved byte-for-byte (same tier/refBk/refO).
  - MLB chain untouched and regression-tested.
  - Audit: `/app/audit_reports/no_reference_market_deep_audit_2026-05-09.md`,
            `/app/audit_reports/refchain_port_diff_2026-05-09.txt`
- **War Zone OVER gate adjustment (2026-05-09) — COMMITTED**
  - HR floor 55 → 50 (`_NBA_WAR_ZONE_BASE.hit_rate_gate.min`)
  - CV-cap ladder armed: tier 2 (HR≥70 + edge>0 → CV≤1.15), tier 3 (HR≥80 + edge≥5 → CV≤1.50)
  - Direction / coverage / edge / vision_score / market_structure gates UNCHANGED
  - Tests: `tests/test_war_zone_over_cv_ladder.py` (21 tests, all passing)
  - First-slate impact: 0 ladder rescues (slate had no HR≥70 candidates) — patch is mathematically sound but supply-bound.
  - **Monitoring directive active**: `/app/backend/scripts/wz_slate_monitor.py` logs every slate to `/app/audit_reports/wz_slate_monitor.jsonl`. Reassess only after **3 normal slates** if WZ qualified stays below 8–10.
- 30-min double-pass sign-off SLO — BLOCKED on upstream odds API blackout (Props 0)

## Architecture
- Frontend: React + Shadcn UI
- Backend: FastAPI + APScheduler + MongoDB
- Core pipeline: `universal_odds_sync` → `live_props` → `delta_dirty_queue` → `detector` → `delta_steps` (rescore) → `master_sync` (hourly board build & VI enrichment)
- Ticker pipeline: scheduler → `routes/live.py::sync_news_headlines` (NBA daily 9:26 UTC) and `sync_mlb_news_headlines` (MLB hourly :32) → `ticker_cache` collection

## Key Endpoints
- `/api/v3/ferrari/all`
- `/api/v3/board`
- `/api/live/news?sport=nba|mlb`
- `/api/live/scores?sport=nba|mlb`

## Key Collections
- `ticker_cache`
- `nba_prop_scores`, `mlb_prop_scores`
- `nba_cached_board`, `mlb_cached_board`
- `scheduler_jobs`

## Backlog (Frozen until stabilization sign-off)
### P0 — Blocked on stabilization sign-off
- Implement Emergent-managed Google OAuth (must use `integration_playbook_expert_v2`)
- Implement Stripe payments (must use `integration_playbook_expert_v2` with pod test keys)

### P0 — Environmental
- Wait for upstream odds API to exit blackout, then run final 30-min double-pass SLO

### P1 — After stabilization sign-off (gated by `ARCHITECTURE.md` directive)
- **Consolidation #1**: Retire `services/mlb_cached_board_builder.py` + duplicate MLB board-rebuild path. Replace call sites with `publish_board_snapshot(db, "mlb")`. Verify NBA + MLB run identical publisher.
- **Consolidation #2**: Audit + document/retire remaining duplicate writers, builders, legacy collections, multi-source truths, route-specific board logic, hidden overlay writers.
- **Consolidation #3**: Produce `/app/memory/SYSTEM_OWNERSHIP.md` per the template in `ARCHITECTURE.md` — canonical source / single writer / allowed readers / enrichment layers / freshness owner / scheduler owner / cache owner for every major entity.
- Decompose `Dashboard.jsx` (2,000+ lines) and `picks_getter_service.py` (3,200+ lines).
- NFL config scaffold (must follow universalization rule — config-first, not a new pipeline).
- STL/BLK/Double-Double model training for NBA.
- MLB `ContextBadgeService` fix (deferred).

### P2
- Forward-test resolver dashboard
- Universal Vision Intel Refactor (YAML configs)

## Recent Changelog
### 2026-05-15 — System-wide ephemeral data cleanup utility (orphan TTL)
- **Problem**: Stale orphan score docs from past slates were never being purged. Audit on 2026-05-15 showed 5,581 of 12,619 MLB FL OVER "rejects" (44%) were orphan docs whose `canonical_key` was no longer in `mlb_live_props` — contaminating gate calibration audits. Same pattern on NBA.
- **Solution**: Two-step active/inactive lifecycle with grace-period TTL purge — never delete a current-slate doc, always give 24h debug window after marking inactive. Universal, sport-agnostic, config-driven.
- **New files**:
  - `services/cleanup/__init__.py`
  - `services/cleanup/ephemeral_collections.py` — central per-sport config: `live_collection`, `canonical_key_field`, `grace_hours`, `collections`. Includes `PROTECTED_COLLECTIONS` blocklist that refuses any accidental inclusion of resolved-outcome / backtest / multiplier-lab / model-performance collections.
  - `services/cleanup/ephemeral_cleanup.py` — 5 entrypoints: `ensure_ttl_indexes`, `get_live_canonical_keys`, `mark_orphan_docs`, `restore_active_docs`, `run_ephemeral_cleanup` + `status_report`. Default `dry_run=True`. Live-props-empty safety abort (`force=True` to override). Supports flat-canonical-key collections and nested-key collections (e.g. cached_board with `props[]`).
  - `routes/admin_ephemeral_cleanup.py` — 3 admin endpoints behind `X-Admin-Token` (`ADMIN_DEBUG_TOKEN` env var):
    - `GET  /api/v3/admin/ephemeral-cleanup/status`
    - `POST /api/v3/admin/ephemeral-cleanup/run?sport=&dry_run=&force=`
    - `POST /api/v3/admin/ephemeral-cleanup/ensure-indexes`
  - `tests/test_ephemeral_cleanup.py` — 7 unit tests (mongomock-motor) covering: TTL index creation, live-empty safety abort, force override, end-to-end mark→restore lifecycle, dry-run non-mutation, protected-collection rejection, status-report shape. **All 7 PASS.**
- **Server integration**:
  - `server.py` startup: ensures TTL indexes on all configured ephemeral collections.
  - `recompute.py` post-success: invokes `run_ephemeral_cleanup(dry_run=False)` per sport after every real (non-dry-run) recompute pass; honours the live-props-empty safety abort.
  - `routes/player.py::/v3/board`: now filters `active=True` — orphan docs invisible to the board.
- **TTL contract**: Mongo TTL index on `ttl_purge_at` field (`expireAfterSeconds=0`). Active docs leave `ttl_purge_at=null` and are NEVER touched by TTL. Inactive orphans get `ttl_purge_at = now + grace_hours` and are physically removed by Mongo when the clock passes. Grace = 24h default per sport.
- **Live results (real run)**:
  - Indexes: ensured on `mlb_prop_scores`, `mlb_cached_board`, `nba_prop_scores`, `nba_cached_board` (+ `nfl_*` for future).
  - First pass marked 100 MLB + 0 NBA new orphans inactive (most legacy inactives were already deactivated by older code paths). Restored 6,474 MLB + 2,207 NBA docs whose canonical_keys had reappeared on the slate.
  - Legacy-inactive backfill stamped `ttl_purge_at` on **106,884 MLB + 34,831 NBA** pre-existing inactive orphan docs that lacked TTL — total **141,815 stale docs scheduled for auto-purge in 24h.**
  - Board sanity: `/api/v3/board?sport=mlb` returns 10 players / 47 props; `?sport=nba` returns 10/89. Safe Haven for both sports returns picks. Gate calibration audits now read only `active=True` so the 5,581 orphan contamination is gone permanently.
- **What this is NOT**: a simple `older_than` TTL on `updated_at`. Active docs never get a TTL field stamped. Nothing is ever hard-deleted by this utility — Mongo's TTL index does the physical removal.
- **Protected collections explicitly excluded**: resolved outcomes, settled bets, backtests, replay datasets, multiplier lab runs, model performance, master hub, game logs, training datasets, betting logs, drift audits, contract violations, sync locks. Adding any of these to the config raises `RuntimeError` at iter time.

### 2026-05-15 — NBA/MLB pick-card visual parity: universal `display_reference_*` (CONSENSUS label) — WIDENED to 2+ books
- **User report**: NBA pick cards displayed the book name (DK / FD / MGM) after the odds; MLB cards displayed "CONSENSUS". Visual asymmetry on the dashboard board.
- **Root cause**: `scoring_stack._pick_reference_odds` deliberately picks a single book for NBA (gates were calibrated against single-book reference odds; changing `tier_reference_odds` would silently re-route tiers). MLB's chain starts with a DK+FD consensus step, so MLB cards naturally show "CONSENSUS".
- **Fix (display-only — gates / routing UNTOUCHED)**:
  - Added `_pick_display_reference_odds()` in `scoring_stack.py` — UNIVERSAL "if 2+ of {DK, FD, MGM, CSR, BOL} quote → CONSENSUS (mean of implied probs); else single-book fallback". Same rule applied to NBA and MLB.
  - `compute_tier()` stamps `display_reference_book` + `display_reference_odds` on every score-doc path.
  - `score_document_schema.py`: added the two new optional fields.
  - `prop_scores_store._SCORE_DOC_FIELDS`: whitelisted for projection.
  - `ferrari_tiers.py`: passthrough into the tier API payload.
  - Frontend `resolveDisplayOdds` in `UniversalPlayerCard.jsx`: prefers `display_reference_*` over `tier_reference_*`, falls back gracefully on older docs.
  - **One-shot backfill** executed against existing score docs: derived consensus from `{sport}_live_props` per-book odds, mass-updated `{sport}_prop_scores`. Singletons mirrored to `tier_reference_*` so no doc ships with null display fields.
- **Live verification (post-backfill)**:
  - NBA Safe Haven: 8/10 picks show CONSENSUS (2 are FD-only and correctly fall back).
  - NBA Front Lines: 10/10 picks show CONSENSUS visible on dashboard.
  - NBA War Zone: 2/4 picks show CONSENSUS.
  - DB totals: NBA 2,775 consensus-stamped (7.2% of 38,548 score docs), MLB 38,680 (33.3% of 116,141). Single-book picks correctly keep their book name.
  - UI screenshot confirms: NBA + MLB cards now visually indistinguishable on the CONSENSUS / book-label dimension.
- **Gate / tier invariance**: `tier_reference_book` / `tier_reference_odds` byte-for-byte unchanged. `resolve_target_tier` reads the same single-book reference; no gate behavior change.

### 2026-05-15 — Universal Consensus / Best Bet chip on PlayerDetailPage PropRow (NBA/MLB pick card parity)
- **User directive**: "The nba and mlb ui should be identical. Add consensus to the pick card page. Mirror mlb."
- **Scope**: `frontend/src/components/dashboard/PlayerDetailPage.jsx::PropRow` — added a sport-agnostic edge-metrics strip that renders inline on every prop row below the Lasso Projection bar.
- **Fields shown** (only when present on the score doc — graceful degrade):
  - `Consensus`: `edge_vs_fair * 100` (model vs market devigged fair) — color: green >15%, yellow >5%, red <-5%, zinc neutral.
  - `Best Bet`: `total_edge * 100` (model vs best book) — color: emerald >10%, amber >3%, red <0; raw-one-sided source flagged with trailing `*`.
  - `Book`: `best_book` (label-mapped) + `best_book_odds` (signed American).
- **Live verification**:
  - NBA detail (Dylan Harper): 36 consensus chips rendered across 48 props (e.g. OVER 7.5 PTS → `CONSENSUS +21.9%`).
  - MLB detail (Ozzie Albies): 22 consensus chips rendered across 22 props (e.g. OVER 0.5 HITS → `CONSENSUS +17.8%`).
- **Not changed**: no backend, no gates, no thresholds. Pure presentation; the same data already feeds the Vision Intel Suite modal block at line ~1693 of the same file. No new fetch paths.
- **Lint**: clean (`mcp_lint_javascript`).

### 2026-05-13 — "Pull from all books" expansion: 4 books → 11 books (MLB multi-book 58% → 70.6%)
- **Root context**: post the 2026-05-08 projection-store fix the audit revealed MLB was actually 81% multi-book (not 30% as previously believed). User directive: "pull from all books. we are already paying for the call. we want maximum coverage."
- **Live API probe** showed 14 books returning MLB data; 6 with non-trivial coverage (>=80 outcomes per event) were unused: ESPN BET, Hard Rock Bet, BetRivers, BetParx, Bally Bet, Fliff. All in `regions=us` — **zero additional Odds API credit cost**.
- **Plumbing changes** (single pass, all production):
  - `universal_odds_sync.py`:
    - `BOOKMAKER_CONFIG`: added 6 new entries (region=us).
    - `MLB_BOOKMAKERS`, `USER_SHARP_BOOKMAKERS`, `SPORT_API_CONFIG.{nba,mlb}.bookmakers`: extended from 6 → 12 books each.
    - `_normalize_market_data` Pass 1 (layer slots), Pass 2 (assignment + opp_field map), Pass 3 (flatten): added 6 new books with short codes `eb`/`hrb`/`brv`/`prx`/`bly`/`flf`.
    - `ALLOWED_BOOKS` whitelist extended to all 12 books.
  - `coverage_filter._BOOK_FIELDS`: 5 → 11 entries (book_count now ranges 0..11).
  - `tp_engine._BOOKS` + `_OPP_FIELDS`: 4 → 11 books for de-vig probability averaging across the full book set.
  - `prop_scores_store._BOOK_LAYER_FIELDS`: added layer/line/odds/odds_opp for all 6 new books (preserves through score-doc projection).
  - `recompute.py` `_book_k` mirror loop: extended to 6 new book field tuples.
- **Live verification (post-sync, 25 MLB events)**:
  - Total props synced: 14,252 → **16,865** (+2,613 new props from broader book coverage).
  - Bookmaker breakdown: DK 9,280 / MGM 9,254 / **ESPN BET 8,956** / **Hard Rock 7,860** / PP 7,001 / FD 5,722 / **Fliff 5,528** / Caesars 4,301 / BOL 3,812 / **BetParx 3,365** / **BallyBet 1,945** / **BetRivers 1,778**.
  - Coverage class transition: pp_only 10.1% → **6.5%** | single_book 31.7% → 22.9% | **multi_book 58.2% → 70.6%**.
  - 485 props now have ALL 11 books anchoring; 1,474 have 9–11 books (sharp consensus zone).
- **Tests**: `tests/test_all_books_expansion.py` (6 new regressions) + `tests/test_coverage_filter.py` (3 new Caesars tests) — 21 new passing tests. 175+ legacy tests still pass.
- **Audit**: `/app/audit_reports/mlb_vs_nba_gate_audit_2026-05-13.md` (covers pre-expansion baseline).

### 2026-05-13 — Caesars (williamhill_us) added to book-anchor counter (MLB lift +871 props)
- **Root cause**: `coverage_filter._BOOK_FIELDS` only recognized 4 books (DK / FD / BetOnline / BetMGM). Caesars (`csr_layer` + `csr_odds`) was wired into `universal_odds_sync` on 2026-05-11 and persisted on 4,379 MLB props (30.2% of live pool), but never counted toward `book_count` or `books_anchored`.
- **Fix**: added `("williamhill_us", "caesars_price", "csr_odds")` to `_BOOK_FIELDS`. Updated docstring to reflect 0..5 book range.
- **Projected impact on the 14,499 live MLB props**:
  - `pp_only` (dropped): 1,465 → **1,190 (−275 props rescued into scoring)**
  - `single_book`: 4,603 → 4,282
  - `multi_book` (devig-eligible): 8,431 → **9,027 (+596 props gained 2nd quote)**
  - Total upgraded: 871 MLB props.
- **Tests**: 3 new Caesars regressions added (`test_classify_caesars_counts_as_anchor`, `test_classify_caesars_legacy_field`, `test_classify_all_five_books_including_caesars`). 30/30 coverage / decoration / Caesars-chain tests pass.
- **Audit**: `/app/audit_reports/mlb_vs_nba_gate_audit_2026-05-13.md`.

### 2026-05-09 — Replay Phase 2.5 partial-parity 30-day run COMPLETE — honest gap analysis
- **TP engine + reference odds + coverage classifier wired** into the replay path: `services/replay/engine.py` now imports `compute_tp`, `_pick_reference_odds`, `classify_coverage` from production scoring with ZERO forks. Refactor: group by `canonical_key` (not side) → score both sides → paired-book layer construction → flat `{prefix}_odds`/`{prefix}_odds_opp` populated → TP fires → coverage gate passes → real production tier decisions emerge.
- **30-day NBA replay** (2024-02-01 → 2024-03-01, t-30m, run_id `a1aeb71a6ef046baae4fb56deef06667`): **503,200 evaluations** scored end-to-end across all 18 markets and 5 Phase-1 books. **97.2% reached `feature_completeness="partial"`** (TP fired); 2.8% capped at `minimal` (rare no_reference_market cases). 0 leakage blocks, 0 feature/scoring failures.
- **Outcomes settled**: 134,636 unique (61,597 hit / 69,594 miss / 3,445 void). Baseline PnL on unqualified picks = −16,152 units (−12.0% ROI). Note: `replay_outcomes` unique key currently lacks `event_id` → cross-event canonical_key collisions cap unique outcomes at 134k; known schema bug, 10-line fix queued for next session.
- **All 503,200 evaluations classified `tier=unqualified` — exactly as predicted with partial features.** Top failure reasons: `gate_hit_rate_fail` 158k (FL), `gate_direction_fail` 254k across SH/FL/WZ. Without VK2, the replay μ is a rolling average and systematically loses direction-fight to lines.
- **Honest confidence statement** committed to `/app/audit_reports/replay_phase25_30day_FINAL.md`: trustworthy for leakage / infrastructure / TP math / chronology integrity; NOT trustworthy for tier ROI / calibration / gate optimization / WZ longshot validation / deployment confidence. Directional signal only on which gates fire for which reasons.
- **Phase 2.5 parity matrix** (clear what's wired vs. blocked): TP ✅, ref-odds ✅, coverage ✅, gate engine ✅, leakage ✅, L5/L10/L20+μ+σ+CV ✅, VK2 ⚠️ blocker, injury timeline ⚠️ blocker (needs new data source), matchup/pace ⚠️ feasible from BDL.
- **Operational hardening**: log-pruner committed (`scripts/prune_rotated_logs.sh`); 3 transient mongod restarts during the run due to `/app` log volume hitting 100% (cleared each time without progress loss thanks to chunked writes + idempotent design).
- **Tests**: 112 replay tests passing (added 8 engine + 32 resolver math + 14 ingester + 18 leakage; refactored engine tests for canonical_key grouping). 178 total including stabilization suite.
- **Production untouched**: replay-only writes; live scoring/gates/board/forward-test code paths unmutated; lineage sentinel `historical_replay` enforced.

### 2026-05-09 — Replay Phase 2 contract-proving skeleton + log-pruner committed
- **Result Resolver** (`services/replay/result_ingester.py`): pulls NBA per-game stats from `bdl_historical_game_logs` + `nba_master_hub_2026.player_game_logs` with cross-validation; writes `replay_results` keyed `(event_id, player_norm)`. **2,894 result rows resolved in 1.5s**, 100% BDL coverage; hub schema returned 0 (separate Phase-2.5 fix). Mismatch rows flagged `validation_status="mismatch"` with full source-A/B preserved — never silently overwritten.
- **Replay Engine** (`services/replay/engine.py`): calls **production `compute_scoring_stack()` directly** — zero scoring forks. Wires reference-odds chain via real book layers from `replay_props_normalized`; mandatory `assert_pregame_only()` + `assert_no_future_games()` gates before every feature build. Smoke run on 200 offers from 2024-02-05: **0 leakage blocks, 0 scoring failures, 341 evaluations persisted in 1.39s**, end-to-end production-scoring path validated.
- **Honest parity tracking**: every evaluation is stamped `feature_completeness="minimal"` because as-of-time builders for **VK2 / model_sigma / injury usage / matchup / pace / TP engine / avg hit-miss margin** are PARITY-TODOs requiring Phase-2.5 historical timeline ingest. The skeleton refuses to claim full parity until those are wired. Tier reason `no_reference_market` on 100% of skeleton evaluations correctly reflects this.
- **Outcome Resolver** (`services/replay/resolver.py` + `scripts/run_outcome_resolver.py`): pure-functional settlement math (`settle`, `realized_payout`, `implied_probability`, `calibration_gap`, `closing_line_value`, `build_outcome_row`). Smoke pipeline end-to-end: 341 evals resolved in 0.16s → 140 hit / 194 miss / 7 void_dnp / **PnL -80.86 units** (random unqualified picks → negative ROI as expected; meaningful signal once real tiers populate).
- **Tests**: 46 settlement-math + ingester tests, 8 engine integration tests, 18 leakage tests, 38 schema/snapshot/run-header tests = **110 replay tests passing in 0.52s**. Mutation-style leakage tests prove `build_as_of_features()` rejects future games even if the BDL filter is bypassed.
- **Log-pruner** (`scripts/prune_rotated_logs.sh`): deletes rotated `*.log.[N]*` files older than 24h (configurable via `--max-age`); dry-run mode (`--dry-run`); never touches active logs; supervisor/cron-safe; idempotent. Tested + working — prevents recurrence of the disk-full → MongoDB crash sequence we hit during the 30-day ingest.
- **Production untouched**: only `replay_*` collections written. Live scoring/gates/board/forward-test code paths NOT mutated. 124 prior tests still pass; combined replay + stabilization suite = **178 tests passing**.
- New collections populated: `replay_results` (2,894 rows / ~0.5 MB), `replay_evaluations` (645 rows from 2 smoke runs / ~3 MB), `replay_outcomes` (341 rows / ~0.2 MB).

### 2026-05-09 — Replay Phase 1 30-day NBA ingest COMPLETE + Phase 2 integrity gates
- **Range**: 2024-02-01 → 2024-03-01 UTC (23 game-days, 6 All-Star break days). **183 events, 24,475 snapshot docs, 3,520,527 normalized rows** (2.38M alt-line + 1.53M combo). **205,880 credits** spent (4.13M remaining of 5M pool). **18m 35s wallclock**. Single-attempt clean completion after MongoDB pressure fix.
- **All 18 markets** present, **all 5 Phase-1 books** present (FD 1.14M / BOL 766k / DK 746k / Caesars 611k / MGM 256k). 8-window snapshot ladder fully populated.
- **Zero anomalies**: `duplicate_anomaly` PASS, `malformed_threshold` PASS, `book_whitelist_compliance` PASS, `chronology_intact` PASS. 500-row random pregame audit: 0 violations.
- **Hardened ingest layer**: `services/replay/{ingest_progress,ingest_telemetry,full_ingest,leakage_checks}.py`, `scripts/{run_full_ingest,run_full_ingest_loop,validate_replay_ingest}.py`. Resumable (`replay_ingest_progress` collection with status lifecycle); idempotent re-runs; chunked 500-op bulk_writes prevent MongoDB index-maintenance pressure; bypass-tenacity 404 path saves ~5× retry cost.
- **Phase 2 integrity tests COMMITTED**: `tests/test_replay_leakage.py` (18 passing) covering as-of-time leakage, pregame-only assertion, 8-window chronology monotonicity, envelope-chain integrity. `services/replay/leakage_checks.py` provides reusable check functions for the future replay engine.
- Engineering note: cleared rotated `/var/log/mongodb.out.log.[1-9]` and `/var/log/supervisor/*.log.[1-9]` to free ~1 GB on the shared 9.8 GB `/app` volume after disk-full caused mongod to crash mid-write. Documented for future cleanup automation.
- **Storage footprint**: 3.09 GB across 3 replay collections (data 2.83 GB + indexes 0.26 GB). Live collections + forward-test data UNTOUCHED.
- Reports: `/app/audit_reports/replay_full_ingest_2024-02-01_to_2024-03-01_FINAL.md`, `/app/audit_reports/replay_full_ingest_2024-02-01_to_2024-03-01.json`, `/app/audit_reports/replay_full_ingest_validation.json`.

### 2026-05-09 — Replay Phase 1 NBA Canary (5 events × 8 windows × 18 markets) — PASSED
- Built `services/replay/{odds_fetch,normalizer,canary_events,ingest_odds}.py` and `scripts/run_canary.py`. Writes ONLY to `replay_odds_snapshots` + `replay_props_normalized`. No production touch.
- Initial run (2024-03-01 NBA slate, 5 events): 6,650 credits / 36.6s wallclock / 39 of 40 calls 200-OK / 1 t-24h SnapshotNotAvailable (events not yet listed by books) handled gracefully without retry waste / 0 errors.
- **665 snapshot docs** (per (event, market, window)) and **100,013 normalized rows** written.
- All 18 NBA markets returned data; bottom-3 markets: `player_rebounds_assists` (2502), `player_steals` (2574), `player_points_assists` (2597). Top-3: `player_points_alternate` (13,959), `player_points_rebounds_assists_alternate` (11,986), `player_rebounds_alternate` (10,879).
- All 5 Phase-1 books returned data: FanDuel (33,061), BetOnline (22,612), DraftKings (20,176), Caesars/williamhill_us (16,350), MGM (7,814). MGM is sparser as expected.
- **Duplicate groups: 0** (unique compound index `uniq_event_label_book_market_player_line_side` enforced).
- **Idempotency verified**: rerun of identical canary produced 0 net inserts, 0 net snapshot inserts, 0 duplicate groups (still 100,013 / 665). 102,158 modifications + 665 snapshot mods (`$set` is the no-op upsert path).
- 20-row random sample shows realistic distributions (varied lines, both Over/Under, alt + non-alt, all 5 books represented). `implied_probability` correctly computed from American odds.
- New module file `services/replay/odds_fetch.py` defines `SnapshotNotAvailable` and bypasses the existing tenacity retry on 404 (saves ~5× wasted credits when an event isn't listed at the requested snapshot ts).
- Outputs: `/app/audit_reports/replay_canary_initial.json`, `/app/audit_reports/replay_canary_rerun.json`.

### 2026-05-09 — Replay Test Suite Phase 0 (scaffolding only, no DB / no API)
- Design doc committed: `/app/audit_reports/replay_suite_design_2026-05-09.md` (664 lines, 10 deliverables + 2 appendices). Approved decisions: 8-window snapshot ladder (`t-24h, t-12h, t-6h, t-3h, t-90m, t-60m, t-30m, close`); Phase-1 books = DK/FD/BetOnline/Caesars/MGM (Pinnacle deferred); 1M-credit hard kill switch per ingest run; per-tier canonical snapshot SH=close, FL=t-60m, WZ=t-30m; result source = BallDontLie + nba_master_hub_2026 cross-validation.
- Files added: `backend/services/replay/{__init__.py,snapshot_plan.py,markets.py,schema.py,run_header.py}`, `backend/scripts/{run_replay.py,compare_replay_runs.py}`, `backend/tests/{test_replay_snapshot_plan.py,test_replay_run_header.py,test_replay_schema.py}`.
- Schema: 11 isolated `replay_*` collections with declared INDEX_SPECS; `dataset_lineage="historical_replay"` quarantine sentinel keeps replay outputs out of forward-test reporting.
- Versioning: `compute_run_fingerprint()` produces `git_commit + git_dirty + scoring_config_hash + gate_config_hash` (covers 10 scoring files + 4 gate files); deterministic, order-independent, missing-file safe.
- 38/38 new tests pass (0.50s); 68/68 prior stabilization tests still pass; `git status` confirms zero modifications to existing files. No DB writes. No API calls.

### 2026-05-09 — The Odds API HISTORICAL alt-prop audit (READ-ONLY)
- Goal: confirm exact recipe to fetch historical NBA alternate player-prop ladders (incl. combos) for any date ≥ 2023-05-03 from The Odds API.
- 31 credits spent (cap was 35). All three probed alt-market keys returned 200 with non-empty ladders on a 2024-03-01 NBA event.
- Validated keys: `player_points_alternate` (6 books), `player_points_rebounds_assists_alternate` (4 books), `player_points_rebounds_alternate` (3 books). Naming rule confirmed: `<live_key>_alternate`. PA/RA combos not probed — pattern strongly implies same shape but each needs a 10-credit confirmation.
- Recipe: `GET /v4/historical/sports/basketball_nba/events?date=…` (1 credit) → pick eventId → `GET /v4/historical/sports/basketball_nba/events/{eventId}/odds?regions=us&markets={ONE_ALT_KEY}&oddsFormat=american&date=…` (10 credits per market per region per event). 5-minute snapshot cadence; envelope ships `timestamp/previous_timestamp/next_timestamp`.
- Gotchas documented: single-sided alt outcomes (DK PRA-alt is Over-only), per-market book coverage shrinks (PTS-alt=6, PRA-alt=4, PR-alt=3), date floor 2023-05-03, never bundle markets in a single call.
- Cost model: ~641 credits per NBA slate × 8 alt markets, ~98k credits per full season at 4 markets ~321/slate.
- NO production patches. NO scoring/gates touched. NO storage. Read-only script: `backend/scripts/odds_api_historical_audit.py`.
- Deliverables: `/app/audit_reports/odds_api_historical_audit_2026-05-09.md` (consolidated) + `/app/audit_reports/odds_api_historical_audit_2026-05-09/` (raw payloads + machine summary).

### 2026-05-08 — Universal injury redistribution model (math rebuild)
**Problem**: card outputs were unrealistic (Wemby +12% on a David Jones OUT, LeBron +15 mins, SGA +12% on JWill OUT). Root cause was `_get_beneficiaries` applying flat per-rank constants (+15 mins / +12% usage / +10 mins / +8% usage / +5 mins / +5% usage) that did not depend on injured magnitude or absorber saturation.
- **New helper** `services/injury_vacuum_service.py::_compute_redistribution(injured, teammates)`. Sport-agnostic two-layer model:
  - **Layer 1 (Minutes)**: weight = mins_headroom × mpg_proximity × bench_factor × position_match. Per-player saturation cap = `8.0 × (1 − baseline_mpg/40)^0.7`. Hard ceilings: `MAX_INDIVIDUAL_MPG=40`, `INDIVIDUAL_MIN_SHARE_CAP=0.45`.
  - **Layer 2 (Usage)**: usage_share × elasticity, where `elasticity = (usage_headroom/MAX_INDIVIDUAL_USAGE)^1.5`. Saturated alphas absorb a fraction of what bench-rotation players absorb per share.
- **Output exposure**: every card now carries `baseline_minutes`, `projected_minutes`, `minutes_delta`, `baseline_usage`, `projected_usage`, `usage_delta`, `redistribution_share`, `elasticity_factor`. Display-side `minutes_bump` / `usage_bump` are back-compat but reflect modeled deltas.
- **Cross-sport safety**: helper returns zero-delta scaffolds when canonical fields (`minutes_per_game`, `usage_percentage`) are absent. MLB beneficiary code (separate path) is unchanged; opt-in only.
- **Verified live (NBA, 2026-05-08)**:
  - Wemby (David Jones OUT, primary): **+15 → +0.9 mins, +12% → +0.2% usage**.
  - SGA (JWill OUT, primary): **+15 → +2.1 mins, +12% → +0.1% usage**.
  - LeBron (Luka OUT, primary): **+15 → +1.8 mins, +12% → +0.5% usage**.
  - Kobe Bufkin (Luka OUT, tertiary bench): **+5 → +6.9 mins, +5% → +4.6% usage** (correctly absorbs more minutes than LBJ).
- **6 regression pytest cases pass** (`tests/test_injury_redistribution.py`): low-rotation injury (alpha minimal), secondary-star OUT (alpha dampened), minutes ceiling (≤40), usage elasticity (saturated < open-canvas), no noise flood (shares sum to 1, ceilings respected), cross-sport zero-delta scaffold.
- **Files**: `backend/services/injury_vacuum_service.py`, `backend/tests/test_injury_redistribution.py` (new). NO frontend, NO scoring, NO tier, NO cached_board, NO ticker, NO Vision Intel touch.

### 2026-05-08 — Live Injury Advantage star-qualifier widening
**Audit-only finding** (no caches involved): cards aren't stale — they're being **filtered out** by an overly-strict 22% usage gate in `InjuryVacuumService._is_star_player`. The recompute path is correct; it just rejects rotation regulars whose injury is genuine breaking news. Smoking gun: OG Anunoby (NYK, OUT, usage=19.4%, mpg=33+) was being silently dropped while Luka Doncic (36.8%) and Jalen Williams (25.2%) passed.
- **Single change** in `services/injury_vacuum_service.py`:
  - Lowered `SECONDARY_ALPHA_THRESHOLD` from 22.0 → 18.0.
  - Added `ROTATION_MINUTES_THRESHOLD = 24.0` — admit on `usage_percentage >= 18` OR `minutes_per_game >= 24`.
  - Both `nba_star_usage_cache` and `nba_master_hub_2026.advanced_stats` lookup paths use the same dual-axis admission. New `alpha_tier="rotation"` for the minutes-only path so downstream code can distinguish.
- **Verified live**: card count went from 6 alerts (2 distinct injured) → 15 alerts (5 distinct injured) within a single restart. NEW visible: **OG Anunoby (NYK)**, **Kevin Huerter (DET)**, **David Jones (SAS)**. Luka Doncic + Jalen Williams retained. No noise flood (DiVincenzo / Sorber correctly filtered as expected).
- **Acceptance**: 6 of 6 user criteria pass (OG visible, existing cards retained, no flood, API live, freshness badge still 7s, no duplicates).
- 5 unit tests (`tests/test_injury_advantage_qualifier.py`) lock down the new admission rules.

### 2026-05-08 — Live Injury Advantage freshness pipeline (5 fixes)
Audit identified 6 stale stages; user approved 5 (deferred legacy-writer cleanup #5 to 48h post-rollout).
- **#1 Cadence fallback** (`services/injury_sensor.py::_get_cadence`). When `live_scores_cache.games` is stale, fall back to a `{sport}_cached_board` activity probe — return `CADENCE_ACTIVE (120s)` when any sport has live cached_board props. Sport-agnostic. NBA was stuck at IDLE=300s mid-season pre-fix.
- **#2 Severity gate lowered** (`services/injury_triggered_rescore.py::_on_event`). Accept `high` AND `medium`. Q→OUT (`status_escalated`, tier_delta=1), `return_date_shifted`, de-escalations now trigger live rescore + cached_board patch. Pre-fix, all medium events were dropped.
- **#3 _patch_cached_board hardening** (same file). Off-board injured players contribute their `team` from `injuries_normalized` (so teammates still patch). Update filter accepts `bdl_id` fallback so name-format drift can't silently no-op.
- **#4 Per-player dedup** (`services/injury_sensor.py::_emit_changes`). Dedup key changed from `sport|team` to `sport|player_key`. Sibling injuries within the 5-min recency window are no longer suppressed.
- **#6 Wire-level freshness signal** (`routes/vacuum.py`, `routes/mlb_vacuum.py`). Live-alerts response ships `served_at`, `oldest_source_synced_at`, `newest_source_synced_at`, `source_age_seconds`. Frontend `LiveInjuryAdvantageSection` renders an "Updated Xs ago" badge with green/amber/red banding (<2min / <5min / older).
- **Verified live**: NBA cached_board patches firing within seconds of ESPN sensor poll (116/150 docs with `last_injury_rescore_at`). NBA + MLB endpoints both ship `source_age_seconds=17`. 6 regression pytest cases pass (`tests/test_injury_freshness.py`).
- **Files**: `backend/services/injury_sensor.py`, `backend/services/injury_triggered_rescore.py`, `backend/routes/vacuum.py`, `backend/routes/mlb_vacuum.py`, `frontend/src/pages/Dashboard.jsx`, `backend/tests/test_injury_freshness.py` (new).
- **Deferred**: #5 (kill `live_injury_micro_sync`, `scheduled_hourly_injury_sync`, `scheduled_live_injury_check`) — review window ≥48h.

### 2026-05-08 — Universal stat-label adapter
- New `frontend/src/utils/statLabel.js`. `getStatLabel('player_points_rebounds_assists')` → `'PRA'`, `getStatLabel('batter_hits_runs_rbis')` → `'H+R+RBI'`, etc. Strips alternate suffixes, humanizes unknowns. ONE helper used by Command Center, Player Detail, board cards, MarketMoves, simulation toasts.
- Migrated `UniversalPlayerCard.jsx`, `lib/PickVisionUtils.jsx`, `PlayerDetailPage.jsx`, `CommandPost.jsx`, `Dashboard.jsx`, `MarketMoves.jsx`. 12 frontend Jest tests pass.

### 2026-05-08 — Universal Command Center analytics (volatility + correlation)
- **Per-leg volatility** now derives from canonical `cv` (clamp 0..2.0). Side-aware hit-rate spread fallback when `cv` is null. CV-scaled label thresholds (LOW <0.20, MED 0.20–0.45, HIGH ≥0.45). Pre-fix metric was bounded ~0.5 from `|h10-h5|` noise — war-zone vs safe-haven indistinguishable.
- **Correlation** rewritten as pairwise rules over canonical leg fields (`player_name`, `event_id`, `team`, `sport`): `same_player → 0.85`, `same_game → 0.40`, `same_team(+sport) → 0.20`, else `0`. Aggregate ρ = mean. Penalty = 1 − clamp(ρ × 0.5, 0, 0.5). Response ships `correlation_score`, `correlation_kind`, per-leg `cv`, `volatility_source`.
- **Frontend** Correlation card shows `correlation_kind` text (Independent · 0% / Same Game · -X% / Same Team · -X% / Same Player · -X%). Never blank.
- **Verified (5 new pytest)**: Brunson+Edwards low-CV→high-CV: vol 0.43 → 1.13. Brunson AST 2.5+9.5: kind=`same_player` score 0.85 pen 42.5%. Brunson+Embiid same event: kind=`same_game` score 0.40 pen 20%. Mixed NBA+MLB: kind=`none` score 0 pen 0%.

### 2026-05-08 — Universal Command Center prop source
- **New helper**: `services/command_center_props.py::get_command_center_props(db, sport, *, player_name=None, canonical_key=None)` — sport-agnostic SSOT reader. Reads canonical rows from `{sport}_prop_scores` filtered to `version_tag=final-{sport}-rt` AND `active=True`. ONE adapter (`_to_canonical_prop`). No cached_board, no stat-level joins, no sport-specific branching beyond the collection name. Adding NFL = one entry in `SCHEDULED_SPORTS`.
- **New route**: `GET /api/command/props?sport={sport}&player_name={name}` (also accepts `canonical_key=...`). Validates sport via `SCHEDULED_SPORTS`. Returns `{success, sport, version_tag, player_name, meta, props[]}`. 400 on unknown sport / missing params, 404 on unknown player.
- **Canonical row contract** (no legacy aliases): `canonical_key, sport, player_name, stat_type, line, recommendation, direction, hit_rate_l5, hit_rate_l10, hit_rate_l20, hit_rate_over, hit_rate_under, p_true_active, edge_vs_fair, vision_score, cv, team, opponent, tier, tier_reason, pp_odds, dk_odds, fd_odds, bol_odds, mgm_odds, tier_reference_book, tier_reference_odds, event_id, game_start_utc, bdl_player_id, is_home`. `h5_rate / h10_rate / hit_rate / hit_rates` are NEVER read or emitted on this path.
- **Frontend**: `useCommandCenterProps` hook in `useLiveOdds.js`. CommandPost replaces the legacy `usePlayerProfile` → `.map(line => ({...}))` reshape with verbatim canonical rows from the new endpoint. New `buildCanonicalLeg` helper forwards canonical fields ONLY to `/api/command/simulate` for both inline-add and Quick-Add (`pendingLeg`) paths.
- **Verified (testing agent + 8 pytest cases)**:
  - Brunson NBA AST: 20 alt rows, 8 distinct `hit_rate_l10` values (0/10/30/40/50/60/70/90) — original "all 90%" smearing bug eliminated.
  - Contreras MLB: 25 canonical rows, all `mlb|`-prefixed `canonical_key`, `version_tag=final-mlb-rt`.
  - Mixed NBA+MLB simulation: convergence rate 64.0%, grade C — works.
  - Frontend CommandPost smoke: search → profile shows varying L10 chips → click adds canonical leg. Network tab confirms `/api/command/props` (not `/api/v3/player-with-badges`).
- **Files**: `backend/services/command_center_props.py` (new), `backend/routes/command.py`, `backend/tests/test_command_center_props.py` (new), `frontend/src/hooks/useLiveOdds.js`, `frontend/src/components/dashboard/CommandPost.jsx`.

### 2026-05-08 — Breaking News Ticker stabilization patch
- Swapped ESPN RSS (HTTP 202, bot-fenced) for ESPN public JSON news API (`site.api.espn.com/apis/site/v2/sports/{basketball/nba|baseball/mlb}/news`)
- Fixed CBS Sports regex to tolerate both `<![CDATA[...]]>` and plain `<title>` wrappers (was capturing 0/36 headlines)
- Removed dead Bleacher Report feed block (HTTP 404)
- Added `mlb_ticker_sync` scheduler job — MLB news was frozen at 2026-04-11 because no MLB ticker job was registered
- Added default `User-Agent` + `Accept` headers via `TICKER_HTTP_HEADERS` to both ticker httpx clients
- Files: `backend/routes/live.py`, `backend/server.py`

### 2026-05-08 — Cached_board materialization (architecture fix)
- **New SSOT**: `{sport}_cached_board` is now a materialized view of `{sport}_prop_scores[version_tag=final-{sport}-rt]`. No independent tier logic; tier assignments carried verbatim from -rt.
- **New service**: `services/board_snapshot_publisher.py::publish_board_snapshot(db, sport)` — single writer. Upsert-only (never deletes), preserves doc-level enrichment (photo_url, injury_status, context_badges, etc.), empties stale players' `props[]` without removing the doc, bails on empty source (zero-wipe guarantee).
- **Delta Engine integration**: added `PublishBoardSnapshotStep` (pos 5 in `DEFAULT_DELTA_STEPS`). Runs only when `written>0` or `retired_modified>0`; skips when upstream lock is held; failure-isolated.
- **master_sync integration**: step 7 replaced (`stamp_cached_board_freshness` → `publish_board_snapshot`). Single build path; metrics key renamed `7_cached_board_snapshot_publish`.
- **Verified (natural delta ticks, no manual triggers)**:
  - NBA tick rescored 153 props → publisher rebuilt 64 players, emptied 76 stale, from 3,074 active -rt props in 1.18s.
  - MLB tick rescored 212 props → publisher rebuilt 318 players, emptied 0 stale, from 6,603 active -rt props in 4.29s.
  - SLO §3 Tier Freshness now PASSES naturally (was FAIL 12.5-hour staleness before patch).
  - API tier endpoints (`/api/v3/ferrari/all`) continue to serve enriched picks unchanged.
- **Tests**: `tests/test_board_snapshot_publisher.py` — 7 passing: source=final-{sport}-rt, rebuild-on-written, skip-on-zero-writes, empty-source-preserves, master_sync-uses-same-path, freshness-matches-rt-timestamps, no-independent-tier-assignment; plus stale-player-emptied-not-deleted, ingestion-fields-preserved-via-canonical-key-merge.
- **Files**: `backend/services/board_snapshot_publisher.py` (new), `backend/services/pipeline/delta_steps.py`, `backend/services/master_sync.py`, `backend/tests/test_board_snapshot_publisher.py` (new).

### 2026-05-08 — Ticker 15-min cadence + source-protection patch
- NBA `ticker_sync` cadence: `CronTrigger(minute='0,15,30,45')` (was daily 9:26 UTC)
- MLB `mlb_ticker_sync` cadence: `CronTrigger(minute='5,20,35,50')` (was hourly :32) — offset 5 min from NBA so fetches never overlap
- Added `TICKER_PROTECTED_STATUS = {202, 403, 429}`; both sync functions log `WARNING` per source on protected/empty responses
- Both sync functions now track `external_count` and **skip the upsert** when `external_count == 0` and a last-good cache exists — preserves cache through transient blackouts
- `get_breaking_news` is now cache-only: removed `_fetch_news_fallback` helper; cold cache returns empty list instead of triggering request-path HTTP. Scheduler is the sole writer of `ticker_cache`.
- Verified: healthy cycle = 15 NBA / 11 MLB headlines; simulated blackout (ESPN→202, CBS→403) preserved baseline cache untouched (`preserved_cache:True`).
- Files: `backend/routes/live.py`, `backend/server.py`

### 2026-05-14 — `total_edge` (Model + Shopping combined) + replay cleanup
- **Investigation conclusion**: `edge_vs_fair` (model alpha) and `best_book_edge` (shopping alpha) are correctly computed but measure two different things and share only the `fair_prob` operand. Documented in CHANGELOG.
- **New field `total_edge`** on `ScoreDocument` (Pydantic) + `_SCORE_OUTPUT_FIELDS` allowlist + `compute_best_book_metrics(p_model=…)` kwarg. Formula: `p_model − best_book_implied`. Side-aware via `ctx.p_model` (== `doc.p_true_active`). Independent of `fair_prob`.
- **Display only** — NOT fed into gates. Distribution-snapshot required before any gate touch (per user spec).
- **UI labels renamed**: `UniversalPlayerCard.jsx` tooltip now shows "Model Edge / Shopping Edge / Total Edge"; `PlayerDetailPage.jsx` MLB stats row split into 4 cols (CV / Model Edge / Total Edge / True Prob).
- **Distribution snapshot** (active=True only):
  - **NBA** (n=290): median total_edge −3.3% slate-wide. Qualified tiers all ≥+16% median (FL +21.4%, SH +16.0%, WZ +35.3%). Unqualified median −4.6%.
  - **MLB** (n=1,536): median +4.7% slate-wide. WZ median +43.9%, FL +12.9%, SH +9.8%. 39.5% of props show total_edge ≥+10%.
- **Tests**: 5 new total_edge cases in `tests/test_best_book.py` (29 total, all pass). Includes mathematical proof that `total_edge` is independent of `fair_prob`.
- **MongoDB cleanup**: Dropped `replay_evaluations` (1.22M docs / 5.998 GB) and `replay_outcomes` (230K docs / 0.158 GB). DB now 1.685 GB on disk.
- **Files**:
  - `backend/services/scoring/best_book.py`
  - `backend/services/scoring/recompute.py` (best-book loop passes `p_model`)
  - `backend/services/scoring/prop_scores_store.py` (allowlist)
  - `backend/services/scoring/score_document_schema.py` (schema field)
  - `backend/tests/test_best_book.py` (5 new tests)
  - `frontend/src/components/dashboard/UniversalPlayerCard.jsx` (3-edge tooltip)
  - `frontend/src/components/dashboard/PlayerDetailPage.jsx` (Model / Total Edge cells)

## Files of Reference
- `backend/routes/live.py` — ticker sync + endpoints
- `backend/server.py` — scheduler config
- `backend/services/board_freshness.py`
- `backend/services/master_sync.py`
- `backend/services/jit_vision_intel_reaper.py`
- `backend/services/badge_enrichment.py`
- `backend/services/mlb_environmental_badges.py`
- `backend/services/scoring/prop_scores_store.py`
- `backend/scripts/production_readiness_slo_check.py`
- `backend/routes/ferrari_tiers.py`

## Test Credentials
N/A (no auth integrations live yet; blocked on stabilization).

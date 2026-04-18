# Board-System Naming Symmetry Audit (Phase 6.5)

**Status**: AUDIT ONLY — no code changes, no drops. Awaiting approval for migration plan.
**Date**: 2026-04-18

---

## 0. Principle (per user directive)

Every concept the board system uses must be named `{sport}_{concept}` and exist identically for every sport. No one-off collection may serve as a source of truth for a single sport.

---

## 1. Canonical naming scheme (target state)

**Per-sport core** (required for every sport; adding NFL = register these seven names for `nfl`):

| Concept                     | Canonical name                 | What it holds                                                  |
|-----------------------------|--------------------------------|----------------------------------------------------------------|
| Raw prop inventory          | `{sport}_live_props`           | Every prop pulled from odds providers, unscored                |
| Master pool (scored)        | `{sport}_prop_scores`          | Every scored prop, with tier + universal lifecycle fields      |
| Enrichment overlay          | `{sport}_cached_board`         | Injuries, badges, vision_intel cache, player context           |
| Historical game logs        | `{sport}_historical_logs`      | Long-term stat history, used by scoring models                 |
| Player mapping / metadata   | `{sport}_player_mapping`       | Odds-API ↔ BDL ↔ internal identity                             |
| Injury feed (normalized)    | `{sport}_injuries_normalized`  | Tier-levelled, dedup'd injuries (multi-source → one truth)     |
| Player badges               | `{sport}_player_badges`        | Sport-specific badge catalog (deep_water, volume_trend, …)     |

**Per-sport optional** (only if the sport needs it):

| Concept                  | Canonical name            | Notes                                           |
|--------------------------|---------------------------|-------------------------------------------------|
| Career / season stats    | `{sport}_career_stats`    | NBA already has this; MLB equivalent missing    |
| Advanced stats cache     | `{sport}_advanced_stats`  | NBA via BDL; MLB equivalent missing             |
| Context engine cache     | `{sport}_context_engine`  | NBA already has this; MLB equivalent missing    |
| Master hub (rolled-up)   | `{sport}_master_hub_2026` | Both NBA + MLB already have this ✓              |
| Calibration runs         | `{sport}_calibration_runs`| NBA only; MLB equivalent should exist           |

**Cross-sport / single-sport-agnostic** (NOT per-sport):

| Collection                  | Reason it stays shared                                   |
|-----------------------------|----------------------------------------------------------|
| `scheduler_jobs`            | APScheduler store, no sport semantics                    |
| `users`                     | Auth                                                     |
| `injuries_normalized`       | OPTIONAL — can stay shared if it carries `sport` field (it does); OR be split into `{sport}_injuries_normalized`. Recommend split for symmetry. |
| `referee_assignments`       | NBA-only concept; rename to `nba_referee_assignments`    |
| `line_history`, `line_movements`, `historical_odds` | Multi-sport by row (carry `sport` field). Acceptable to share. |
| `odds_api_mapping_master`   | Cross-sport mapping table; acceptable to share           |
| `forward_test_snapshots`, `backtest_game_logs` | Research artifacts; not in runtime board path |
| `market_moves`, `market_moves_snapshots` | Already shared across sports with a `sport` field |

---

## 2. Violations in the current codebase

### 2.1 Elite-branded NBA-only collections (hard violation)

| Current name          | Problem                                                                 | Target                                                              |
|-----------------------|-------------------------------------------------------------------------|---------------------------------------------------------------------|
| `elite_safe_haven`    | NBA-only board storage; MLB equivalent is `mlb_safe_haven` (asymmetric) | **DELETE** — replaced by live reader in Phase 6 Steps 1-4           |
| `elite_front_lines`   | Same                                                                    | **DELETE**                                                          |
| `elite_war_zone`      | Same                                                                    | **DELETE**                                                          |

These are also P5 Step-3 targets already audited.

### 2.2 `dg_*` NBA-only namespace (soft violation — NBA reserves the `dg_` prefix for itself)

| Current name              | Canonical target                |
|---------------------------|---------------------------------|
| `dg_live_props`           | `nba_live_props`                |
| `dg_cached_board`         | `nba_cached_board`              |
| `dg_cached_board_temp`    | `nba_cached_board_temp`         |
| `dg_injuries`             | delete (superseded by `injuries_normalized`; already audited in P5) |
| `dg_daily_insights`       | `nba_daily_insights`            |
| `dg_flagged_players`      | `nba_flagged_players`           |
| `dg_breaking_news`        | `nba_breaking_news`             |
| `dg_social_signals`       | `nba_social_signals`            |
| `dg_player_stats`         | `nba_player_stats`              |
| `dg_master_roster`        | `nba_master_roster`             |
| `dg_goblin_recon`         | `nba_goblin_recon`              |
| `dg_locked_games`         | `nba_locked_games`              |
| `dg_events_cache`         | `nba_events_cache`              |
| `dg_odds_cache`           | `nba_odds_cache` (or shared `odds_cache` — see §2.6)                |
| `dg_sync_log`, `dg_sync_status`, `dg_parlay_builder`, `dg_static_shell` | rename with `nba_` prefix   |

**18 collections** all marked with `dg_` instead of `nba_`. Every one of these has an MLB equivalent named `mlb_*` or NO MLB equivalent at all — which means every time a sport is added, it either has to re-invent a `{sport}_live_props` or copy NBA's `dg_*` special cases.

### 2.3 MLB-only `mlb_elite_*` and `mlb_ferrari_*` duplicates (hard violation)

| Current name                | Problem                                                  | Target                  |
|-----------------------------|----------------------------------------------------------|-------------------------|
| `mlb_elite_safe_haven`      | Dead legacy copy of `mlb_safe_haven`                     | **DELETE**              |
| `mlb_elite_front_lines`     | Dead legacy copy of `mlb_front_lines`                    | **DELETE**              |
| `mlb_elite_war_zone`        | Dead legacy copy of `mlb_war_zone`                       | **DELETE**              |
| `mlb_ferrari_safe_haven`    | Dead copy of `mlb_safe_haven` (different ingest path)    | **DELETE**              |
| `mlb_ferrari_front_lines`   | Dead copy of `mlb_front_lines`                           | **DELETE**              |
| `mlb_ferrari_war_zone`      | Dead copy of `mlb_war_zone`                              | **DELETE**              |
| `mlb_gemini_cache_safe_haven`, `..._front_lines`, `..._war_zone` | Orphan Gemini cache (1 row each; not read) | **DELETE** |
| `mlb_goblins`, `mlb_demons`, `mlb_standard`, `mlb_ferrari_*` | MLB-specific tier vocabulary that NBA does not use | Review — if kept, rename to `mlb_pool_{concept}` to avoid confusion with the universal-tier vocabulary. |

**Also `mlb_safe_haven`, `mlb_front_lines`, `mlb_war_zone`** → these are the MLB tier-board storage we just replaced with the universal reader. They are now dead-weight writes; mark for retirement in Step 6.

### 2.4 `bdl_*` namespace (asymmetric but internally justified)

| Current name                | Justification                                              | Target                            |
|-----------------------------|------------------------------------------------------------|-----------------------------------|
| `bdl_advanced_stats`        | BDL is an NBA-only upstream data source                    | `nba_advanced_stats` (rename; keep contents) |
| `bdl_historical_game_logs`  | NBA-only via BDL                                           | `nba_historical_logs` (align with the already-named `mlb_historical_logs`) |
| `bdl_injuries`              | NBA-only, superseded by `injuries_normalized`              | **DELETE** (already P5 audit target) |
| `bdl_player_badges`         | NBA-only                                                   | `nba_player_badges`               |
| `bdl_player_mapping`        | NBA-only                                                   | `nba_player_mapping` (mirrors the missing `mlb_player_mapping`) |

### 2.5 Un-prefixed top-level collections (mixed violation)

| Current name           | Problem                                                 | Target                                   |
|------------------------|---------------------------------------------------------|------------------------------------------|
| `ferrari_safe_haven`   | Old NBA board snapshot (brand-named)                    | **DELETE** (superseded by live reader)   |
| `ferrari_front_lines`, `ferrari_war_zone` | Same                                | **DELETE**                               |
| `ferrari_discarded`    | Research artifact; NBA scoring discards                 | `nba_scoring_discarded`                  |
| `ferrari_picks`        | Snapshot of NBA picks                                   | **DELETE** (superseded by live reader)   |
| `ferrari_scored`       | Snapshot                                                | **DELETE**                               |
| `ferrari_parlays`      | Empty (0 rows)                                           | **DELETE**                               |
| `breaking_news_cache`  | Generic; only used by NBA path                          | `nba_breaking_news_cache` OR keep if truly shared |
| `defensive_momentum_cache` | NBA                                                   | `nba_defensive_momentum_cache`           |
| `dvp_rankings`         | NBA                                                     | `nba_dvp_rankings`                       |
| `star_usage_cache`     | NBA                                                     | `nba_star_usage_cache`                   |
| `live_injuries`        | Unused writer path, dormant                              | **DELETE** (already P5 audit target)     |
| `espn_injuries`        | Cross-sport upstream feed (raw)                         | Keep — it's an ingestion feed, not board  |
| `espn_news`            | Cross-sport upstream feed                               | Keep                                     |
| `oracle_apex_analyzed` | NBA-only                                                | `nba_oracle_apex_analyzed`               |
| `referee_assignments`  | NBA-only                                                | `nba_referee_assignments`                |
| `backtest_game_logs`   | Research                                                | `backtest_game_logs` is fine (research namespace) |
| `forward_test_snapshots` | Research                                              | fine                                     |
| `spotrac_contracts_cache` | NBA-only                                             | `nba_spotrac_contracts_cache`            |
| `ticker_cache`, `ticker_headlines` | NBA-only                                     | `nba_ticker_cache`, `nba_ticker_headlines` |
| `odds_api_mapping_master`, `odds_api_props` | Cross-sport                           | Keep as shared (rows carry `sport`)      |
| `line_history`, `line_movements`, `historical_odds` | Cross-sport by row           | Keep as shared                           |
| `market_moves`, `market_moves_snapshots` | Cross-sport by row                      | Keep as shared                           |
| `users`, `player_photos`, `scheduler_jobs`, `live_scores_cache` | Shared runtime     | Keep                                     |

### 2.6 Summary count of violations

| Class                                                     | Count | Action            |
|-----------------------------------------------------------|-------|-------------------|
| NBA-only named with `dg_` or bare prefix (rename)          | 18    | rename to `nba_*` |
| NBA-only named with `bdl_` (data-source rename)            | 4     | rename to `nba_*` |
| `elite_*` legacy tier boards (NBA)                         | 3     | DELETE            |
| `mlb_elite_*` / `mlb_ferrari_*` / `mlb_gemini_cache_*` dead copies | 9 | DELETE            |
| `ferrari_*` un-prefixed legacy snapshots                   | 7     | DELETE (6) + rename (1) |
| `live_injuries`, `bdl_injuries`, `dg_injuries` injury legacies | 3  | DELETE (after Step 6.7 reader migrations) |
| Shared collections (correctly un-prefixed)                 | 11    | Keep              |

---

## 3. Why this matters now (not optional polish)

- Every reader that still references `dg_live_props` / `dg_cached_board` / `elite_*` is **per-sport coupled** — can't be reused by MLB, and can't generalize to NFL. The universal board engine shipped in Steps 1-4 has to special-case NBA (`dg_cached_board`) vs MLB (`mlb_cached_board`) inside the adapters; fixing this collapses that special case.
- The MLB dead-copy clutter (`mlb_elite_*`, `mlb_ferrari_*`) confuses freshness audits and Phase 5 decisions.
- Adding NFL today would require re-deciding NBA's schema quirks per collection — that's exactly the duplication the user's directive banned.

---

## 4. Migration plan (4 phases, each independently verifiable + reversible)

### Phase A — Define canonical config (0 runtime risk)

- Introduce `config/collections.py::COLLECTION_NAMES[sport][concept]`. Single dictionary.
- Engine + adapters + all new readers read collection names from this config.
- Legacy names remain in place as fallbacks via `COLLECTION_ALIASES`. Zero writes change.
- Test: every NBA/MLB reader still returns 200; no data drift.

### Phase B — Rename NBA's `dg_*` collections (one at a time, read-through)

For each `dg_*` target (e.g. `dg_live_props` → `nba_live_props`):
1. Stand up the new name in config; leave the old name as alias.
2. Switch WRITERS first — every writer now writes to `nba_live_props` AND `dg_live_props` (dual-write) for a 24 h bake-in.
3. After bake-in: switch all READERS to `nba_live_props`.
4. Turn off the `dg_live_props` dual-write.
5. Drop `dg_live_props` after 48 h observation.

This is the heaviest chunk (18 collections × the 5-step process). Can be batched: do the board-critical five first (`dg_live_props`, `dg_cached_board`, `dg_events_cache`, `dg_odds_cache`, `dg_daily_insights`), then the rest.

### Phase C — Rename BDL-sourced collections

Same dual-write playbook: `bdl_advanced_stats` → `nba_advanced_stats`, `bdl_historical_game_logs` → `nba_historical_logs`, `bdl_player_badges` → `nba_player_badges`, `bdl_player_mapping` → `nba_player_mapping`. `bdl_injuries` is already slated for deletion.

### Phase D — Kill dead legacies

- `elite_safe_haven/front_lines/war_zone` — already Phase 5 Step 6/7 targets.
- `mlb_elite_*`, `mlb_ferrari_*`, `mlb_gemini_cache_*` — confirm no runtime readers then drop.
- `ferrari_safe_haven/front_lines/war_zone/picks/scored/discarded/parlays` — confirm no runtime readers then drop (or rename `ferrari_discarded` → `nba_scoring_discarded`).
- `live_injuries`, `dg_injuries`, `bdl_injuries` — already Phase 5 audit targets.
- `mlb_safe_haven/front_lines/war_zone` — retired writers (Phase 5 Step 6). Confirm no runtime readers, then drop.

### Phase E (final) — Re-anchor adapter contract

Once every collection is canonical:
- `SportBoardAdapter.scores_collection`, `.live_props_collection`, `.cached_board_collection` all resolve through `COLLECTION_NAMES`. Each adapter sets `self.sport = 'nba'` and the rest is derived.
- Adding NFL becomes literally:
  ```python
  class NFLBoardAdapter(SportBoardAdapter):
      sport = "nfl"
      version_tag = "final-nfl"
      # every collection name auto-resolves to nfl_*
  ```

---

## 5. What I recommend shipping FIRST

**Phase A only.** Introduce the canonical config, thread it through the universal engine + adapters + the six reader routes, and alias every current name. **Zero data moves; zero rename risk; zero reader regression.**

After Phase A lands, we pick up Phases B→D one collection at a time, each on the dual-write playbook so every migration is reversible via an env toggle.

---

## 6. Open questions before I touch code

1. **Scope of first PR**: (a) ship Phase A only, (b) Phase A + Phase D dead-collection drops (aggressive), (c) full A→D in sequence.
2. **`bdl_*` rename**: (a) rename to `nba_*` as proposed, (b) keep `bdl_*` because the BDL upstream source name has value for debugging, (c) duplicate — write to both during transition.
3. **MLB retired tier collections** (`mlb_safe_haven/front_lines/war_zone`): drop immediately (aggressive) or keep until Step 6 retires the `_atomic_publish` writer.
4. **Shared vs per-sport for `injuries_normalized`**: keep shared with a `sport` field, or split into `{sport}_injuries_normalized` per the strict symmetry rule?

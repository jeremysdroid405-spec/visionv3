# Collection Responsibility Map

## Authoritative Collections (Source of Truth)

| Collection | Purpose | Type | Read Paths | Write Paths | Action |
|------------|---------|------|------------|-------------|--------|
| `nba_master_hub_2026` | Player identity, stats, game logs, photos | **Authoritative** | `cached_data.py`, `master_hub.py`, `picks_getter_service.py`, `badge_resolver.py` | `bdl_comprehensive_sync.py`, `nba_master_hub.py` | **KEEP** |
| `dg_master_roster` | Active roster, player-team mapping | **Authoritative** | `player_repo.py`, `roster_service.py`, `odds_api_mapper.py` | `roster_service.py`, `roster_sync.py` | **KEEP** |
| `bdl_player_mapping` | BDL API ID ↔ player name | **Authoritative** | `bdl_player_mapping.py` | `bdl_player_mapping.py` | **KEEP** |
| `odds_api_mapping_master` | Odds API name normalization | **Authoritative** | `odds_api_mapper.py`, `picks_getter_service.py` | `odds_api_mapper.py` | **KEEP** |
| `player_photos` | Headshot URLs (ESPN CDN) | **Authoritative** | `picks_getter_service.py`, `image_proxy.py` | `picks_getter_service.py`, `nba_master_hub.py` | **KEEP** |
| `dvp_rankings` | Defense vs Position rankings | **Authoritative** | `dvp_service.py`, `intel_suite_calculator.py` | `dvp_service.py` | **KEEP** |
| `users` | User accounts | **Authoritative** | `auth.py` | `auth.py` | **KEEP** |

## Derived/Computed Collections (Rebuilt on Sync)

| Collection | Purpose | Type | Read Paths | Write Paths | Action |
|------------|---------|------|------------|-------------|--------|
| `dg_cached_board` | Enriched picks for frontend | **Derived** | `board_repo.py`, `cached_data.py`, `picks_getter_service.py` | `cached_board_builder_service.py`, `picks_getter_service.py` | **KEEP** |
| `dg_live_props` | Live betting props from Odds API | **Derived** | `live.py`, `props_service.py` | `odds_sync_service.py`, `props_service.py` | **KEEP** |
| `dg_parlay_builder` | Pre-built parlay combinations | **Derived** | `parlays.py`, `picks_getter_service.py` | `parlay_builder_service.py` | **KEEP** |
| `dg_goblin_recon` | Safe parlay recommendations | **Derived** | `cached_data.py` | `picks_getter_service.py` | **KEEP** |
| `dg_goblin_vault` | Safe Haven picks | **Derived** | `cached_data.py` | `tier_builder_service.py` | **KEEP** |
| `dg_front_lines` | Mixed tier picks | **Derived** | `cached_data.py` | `tier_builder_service.py` | **KEEP** |
| `dg_radar_picks` | War Zone picks | **Derived** | `cached_data.py` | `tier_builder_service.py` | **KEEP** (currently empty) |

## Cache Collections (Ephemeral)

| Collection | Purpose | Type | Read Paths | Write Paths | Action |
|------------|---------|------|------------|-------------|--------|
| `dg_odds_cache` | Raw Odds API responses | **Cache** | `odds_sync_service.py` | `odds_sync_service.py` | **KEEP** |
| `dg_events_cache` | NBA events/games | **Cache** | `game_lock_engine.py`, `live.py` | `odds_sync_service.py` | **KEEP** |
| `dg_stats_cache` | Player stats cache | **Cache** | `stats_enrichment_service.py` | `stats_enrichment_service.py` | **KEEP** |
| `dg_static_shell` | Static UI shell data | **Cache** | `cached_data.py` | `sync_service.py` | **KEEP** |
| `ticker_cache` | News ticker data | **Cache** | `scheduler.py` | `scheduler.py` | **KEEP** |
| `spotrac_contracts_cache` | Contract data for badges | **Cache** | `spotrac_contract_service.py` | `spotrac_contract_service.py` | **KEEP** |

## Status/Logging Collections

| Collection | Purpose | Type | Read Paths | Write Paths | Action |
|------------|---------|------|------------|-------------|--------|
| `dg_sync_status` | Current sync state | **Status** | `sync_service.py`, `core_v3.py` | `sync_service.py` | **KEEP** |
| `dg_sync_log` | Sync history log | **Status** | `sync_repo.py`, multiple services | `sync_repo.py`, multiple services | **KEEP** |
| `sync_log` | Alternate sync log | **Status** | None (only 1 doc) | None active | **DEPRECATE** (stale) |

## Context/Intel Collections

| Collection | Purpose | Type | Read Paths | Write Paths | Action |
|------------|---------|------|------------|-------------|--------|
| `nba_context_engine` | AI context analysis | **Derived** | `ai_context_engine.py`, `master_hub.py` | `ai_context_engine.py` | **KEEP** |
| `nba_career_stats` | Career milestones | **Derived** | `badge_resolver.py` | `nba_official_sync.py` | **KEEP** |
| `dg_breaking_news` | Breaking news | **Cache** | `scheduler.py` | `scheduler.py` | **KEEP** |
| `dg_daily_insights` | Daily player insights | **Derived** | `player_repo.py` | `intel_briefing_engine.py` | **KEEP** |
| `dg_trending` | Trending players | **Derived** | `social.py` | `social_signal_engine.py` | **KEEP** |
| `dg_flagged_players` | Flagged/suspended | **Status** | `picks_getter_service.py` | `admin.py` | **KEEP** |
| `dg_locked_games` | Started games | **Status** | `game_lock_engine.py` | `game_lock_engine.py` | **KEEP** |

---

## Duplicate Collection Analysis

### `bdl_injuries` vs `dg_injuries`

| Aspect | `bdl_injuries` | `dg_injuries` |
|--------|----------------|---------------|
| **Doc Count** | 25 | 0 |
| **Source** | BallDontLie API | ESPN API |
| **Write Paths** | `injury_service.py` | `injury_service.py` |
| **Read Paths** | `scheduler.py`, `live.py`, `cached_data.py`, `picks_getter_service.py`, `demon_goblin_engine.py` | `picks_getter_service.py` (combined read) |
| **Status** | **ACTIVE** - contains data | **EMPTY** - ESPN sync disabled/broken |

**Recommendation**: **KEEP BOTH** - They serve different sources (BDL vs ESPN). Currently only `bdl_injuries` has data. The code is designed to merge both in `picks_getter_service.py`. When ESPN sync is fixed, `dg_injuries` will populate.

### `sync_log` vs `dg_sync_log`

| Aspect | `sync_log` | `dg_sync_log` |
|--------|------------|---------------|
| **Doc Count** | 1 | 13 |
| **Write Paths** | None active | `sync_repo.py`, `sync_service.py`, `cached_board_builder_service.py`, etc. |
| **Read Paths** | `demon_goblin_engine.py` (single read) | `sync_repo.py`, multiple services |
| **Status** | **STALE** - only 1 legacy doc | **ACTIVE** - primary sync log |

**Recommendation**: **DEPRECATE `sync_log`** - It has a single stale document. All active code uses `dg_sync_log`. The one read in `demon_goblin_engine.py` should be updated to use `dg_sync_log`.

---

## Migration Plan

1. **`sync_log` → `dg_sync_log`**: Update the single reference in `demon_goblin_engine.py:1189` to read from `dg_sync_log`. Then `sync_log` can be safely ignored.

2. **`dg_injuries`**: Keep but monitor. If ESPN sync is re-enabled, it will populate. Currently `bdl_injuries` is the active source.

3. **No other migrations needed** - collections are used correctly.

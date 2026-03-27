# picks_getter_service.py Modularization Plan

## Current State

- **File**: `/app/backend/services/picks_getter_service.py`
- **Lines**: 2866
- **Class**: `PicksGetterService`
- **Dependencies**: Reads from 8+ MongoDB collections, uses multiple external services

---

## Identified Subdomains

### 1. Hit Rate Calculator (`hit_rate_service.py`)
**Methods to extract:**
- `_calculate_h5_hit_rate()`
- `_calculate_h10_hit_rate()`
- `_calculate_l25_hit_rate()`
- `_calculate_l5_avg()`
- `_calculate_l10_avg()`
- `calculate_hit_rates()` (inner function at line 537)
- `calc_stats()` (inner function at line 556, 699)

**Purpose**: Centralize all hit rate calculations into a pure stateless service.

**Estimated size**: ~300 lines

---

### 2. Player Stats Resolver (`player_stats_resolver.py`)
**Methods to extract:**
- `_get_player_stats()`
- `_get_master_player()`
- `_get_master_player_by_name()`
- `_get_player_lookup()`
- `_get_player_by_id()`
- `_get_player_by_name()`
- `_get_season_avg()`
- `_normalize_stat_key()`
- `_enrich_player_with_master_hub_stats()`

**Purpose**: Handle all player stats lookups and enrichment.

**Estimated size**: ~400 lines

---

### 3. Photo Service (`photo_service.py`)
**Methods to extract:**
- `_load_photo_cache()`
- `_enrich_picks_with_photos()`
- Photo cache management logic

**Purpose**: Handle player photo URL resolution and caching.

**Estimated size**: ~150 lines

---

### 4. Tier Builder (`tier_builder.py`)
**Methods to extract:**
- `get_war_zone()` → ~260 lines
- `get_goblin_vault()` → ~260 lines
- `get_front_lines()` → ~300 lines

**Purpose**: Build War Zone (demons), Safe Haven (goblins), Front Lines (mixed) picks.

**Estimated size**: ~800 lines

**Note**: This overlaps with existing `tier_builder_service.py` - may need to consolidate.

---

### 5. Parlay Builder (`parlay_builder.py`)
**Methods to extract:**
- `get_parlay_builder()` → ~140 lines
- `get_goblin_recon()` → ~180 lines
- `get_multi_team_picks()` (inner function)
- `calculate_combined_probability()` (inner function)

**Purpose**: Generate parlay recommendations.

**Estimated size**: ~350 lines

---

### 6. Board Formatter (`board_formatter.py`)
**Methods to extract:**
- `get_cached_board()`
- `get_cached_player()`
- `_clean_object_ids()`
- `_flatten_hit_rates_to_props()`
- `get_most_popular_bets()`

**Purpose**: Format data for frontend consumption.

**Estimated size**: ~300 lines

---

### 7. Insights Generator (`insights_generator.py`)
**Methods to extract:**
- `_add_insights_to_pick()`
- `_add_player_insights()`

**Purpose**: Generate AI/contextual insights for picks and players.

**Estimated size**: ~150 lines

---

### 8. Game Utilities (`game_utils.py`)
**Functions to extract (already at module level):**
- `_get_game_status()`
- `_filter_played_games()`
- `_get_opponent_from_game()`
- `_normalize_name()`
- `did_play()`
- `_get_game_info()`
- `_get_injured_players()`

**Purpose**: Utility functions for game state, player names, injuries.

**Estimated size**: ~200 lines

---

## Proposed New Structure

```
/backend/services/
├── picks/                          # NEW: Picks subdirectory
│   ├── __init__.py
│   ├── hit_rate_service.py        # Hit rate calculations
│   ├── player_stats_resolver.py   # Player stats lookups
│   ├── photo_service.py           # Photo URL resolution
│   ├── tier_builder.py            # War Zone/Safe Haven/Front Lines
│   ├── parlay_builder.py          # Parlay recommendations
│   ├── board_formatter.py         # Frontend data formatting
│   ├── insights_generator.py      # AI/contextual insights
│   └── game_utils.py              # Utility functions
│
├── picks_getter_service.py        # EXISTING: Thin wrapper/facade
```

---

## Migration Strategy

### Phase 1: Extract Utilities (Safe)
1. Create `services/picks/game_utils.py` with module-level functions
2. Import into `picks_getter_service.py`
3. Verify no breakage

### Phase 2: Extract Hit Rate Calculator (Safe)
1. Create `services/picks/hit_rate_service.py` as stateless class
2. Add delegation from `PicksGetterService`
3. Keep original methods as thin wrappers
4. Verify no breakage

### Phase 3: Extract Photo Service (Safe)
1. Create `services/picks/photo_service.py`
2. Move cache management and photo enrichment
3. Inject as dependency
4. Verify no breakage

### Phase 4: Extract Player Stats Resolver (Medium Risk)
1. Create `services/picks/player_stats_resolver.py`
2. Requires DB access - inject via constructor
3. Update all internal calls to use resolver
4. Verify no breakage

### Phase 5: Extract Tier Builder (Medium Risk)
1. Create `services/picks/tier_builder.py`
2. Depends on hit rate, stats, and photo services
3. Compose with existing services
4. Verify War Zone, Safe Haven, Front Lines still work

### Phase 6: Extract Parlay Builder (Low Risk)
1. Create `services/picks/parlay_builder.py`
2. Depends on tier builder output
3. Simple extraction
4. Verify parlays still work

### Phase 7: Extract Board Formatter (Low Risk)
1. Create `services/picks/board_formatter.py`
2. Simple formatting logic
3. Verify cached_board endpoint works

### Phase 8: Final Cleanup
1. `picks_getter_service.py` becomes thin facade
2. Remove duplicate code
3. Update imports across codebase
4. Document new structure

---

## Dependency Graph

```
game_utils.py (no deps)
      ↓
hit_rate_service.py (uses game_utils)
      ↓
player_stats_resolver.py (uses MongoDB)
      ↓
photo_service.py (uses MongoDB)
      ↓
tier_builder.py (uses hit_rate, stats, photo)
      ↓
parlay_builder.py (uses tier_builder)
      ↓
board_formatter.py (uses tier_builder, stats)
      ↓
insights_generator.py (uses AI services)
      ↓
picks_getter_service.py (facade - delegates to all)
```

---

## Risk Mitigation

1. **Keep original file intact** during migration
2. **Add feature flags** if needed to toggle old vs new
3. **Test each extraction** before proceeding
4. **Use composition** rather than inheritance
5. **Maintain backwards compatibility** in public methods

---

## Estimated Effort

| Phase | Lines | Risk | Time |
|-------|-------|------|------|
| Phase 1 (Utils) | ~200 | Low | 30 min |
| Phase 2 (Hit Rate) | ~300 | Low | 45 min |
| Phase 3 (Photo) | ~150 | Low | 30 min |
| Phase 4 (Stats) | ~400 | Medium | 1 hr |
| Phase 5 (Tiers) | ~800 | Medium | 1.5 hr |
| Phase 6 (Parlay) | ~350 | Low | 45 min |
| Phase 7 (Board) | ~300 | Low | 45 min |
| Phase 8 (Cleanup) | - | Low | 1 hr |
| **Total** | - | - | **~7 hrs** |

---

## Notes

- **Do not start until Phase 2-5 of the main restructuring are complete**
- Verify backend stability after each phase
- Run existing tests after each extraction
- May need to create new tests for extracted modules

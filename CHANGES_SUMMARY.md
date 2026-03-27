# Restructuring Changes Summary

## Date: 2026-03-27

---

## Phase 2: Structure Cleanup ✅

### Engine Files Moved to `/backend/services/engines/`

| File | From | To |
|------|------|-----|
| `adaptive_sync_engine.py` | `/backend/` | `/backend/services/engines/` |
| `ai_context_engine.py` | `/backend/` | `/backend/services/engines/` |
| `board_intelligence_engine.py` | `/backend/` | `/backend/services/engines/` |
| `demon_goblin_engine.py` | `/backend/` | `/backend/services/engines/` |
| `demon_tracker_engine.py` | `/backend/` | `/backend/services/engines/` |
| `game_lock_engine.py` | `/backend/` | `/backend/services/engines/` |
| `intel_briefing_engine.py` | `/backend/` | `/backend/services/engines/` |
| `live_scores_engine.py` | `/backend/` | `/backend/services/engines/` |
| `nba_master_hub.py` | `/backend/` | `/backend/services/engines/` |
| `payout_engine.py` | `/backend/` | `/backend/services/engines/` |
| `social_signal_engine.py` | `/backend/` | `/backend/services/engines/` |

### Service Files Moved to `/backend/services/`

| File | From | To |
|------|------|-----|
| `injury_service.py` | `/backend/` | `/backend/services/` |
| `odds_api_mapper.py` | `/backend/` | `/backend/services/` |
| `raw_stat_fetcher.py` | `/backend/` | `/backend/services/` |
| `stats_manager_bdl.py` | `/backend/` | `/backend/services/` |
| `vision_ai_service.py` | `/backend/` | `/backend/services/` |

### New Files Created

| File | Purpose |
|------|---------|
| `/backend/services/engines/__init__.py` | Engine module exports |
| `/backend/models/__init__.py` | Model package exports |
| `/backend/models/player.py` | Player Pydantic schemas |
| `/backend/models/prop.py` | Prop Pydantic schemas |
| `/backend/models/sync.py` | Sync status Pydantic schemas |
| `/backend/models/board.py` | Board/parlay Pydantic schemas |
| `/backend/models/user.py` | User auth Pydantic schemas |

### Import Updates

Updated imports in all files that referenced moved modules:
- `server.py`
- All files in `routes/`
- All files in `services/`
- All files in `services/engines/`

---

## Phase 3: Collection Normalization ✅

### Documentation Created

| Document | Purpose |
|----------|---------|
| `COLLECTION_RESPONSIBILITY_MAP.md` | Collection ownership, read/write paths, actions |

### Collection Analysis Results

**Authoritative Collections** (keep unchanged):
- `nba_master_hub_2026` - Player SSOT
- `dg_master_roster` - Roster SSOT
- `bdl_player_mapping` - BDL ID mapping
- `odds_api_mapping_master` - Odds API mapping
- `player_photos` - Photo metadata
- `dvp_rankings` - DVP data
- `users` - User accounts

**Derived Collections** (rebuilt on sync):
- `dg_cached_board` - Frontend display data
- `dg_live_props` - Live odds
- `dg_parlay_builder` - Parlay recommendations

**Cache Collections** (ephemeral):
- `dg_*_cache` - Various caches
- `ticker_cache` - News ticker

**Duplicate Collection Analysis**:
- `bdl_injuries` vs `dg_injuries`: **KEEP BOTH** - different sources
- `sync_log` vs `dg_sync_log`: **DEPRECATE sync_log** - stale, code uses dg_sync_log

---

## Phase 5: API Cleanup ✅

### Documentation Created

| Document | Purpose |
|----------|---------|
| `API_SURFACE.md` | Complete API endpoint documentation |

### Documented Endpoints

- **Core V3**: 10 endpoints (board, players, search, sync)
- **Player Detail**: 6 endpoints (badges, intel suite)
- **Tiers & Parlays**: 5 endpoints (war zone, safe haven, etc.)
- **Board Intelligence**: 8 endpoints (scheduler, sync)
- **Adaptive Sync**: 6 endpoints
- **Command Hub**: 4 endpoints
- **Auth**: 2 endpoints
- **Admin**: 12 endpoints
- **Live Data**: 8 endpoints
- **Master Hub**: 3 endpoints

---

## Phase 6: Git-safe Restructuring ✅

### Files Updated

| File | Changes |
|------|---------|
| `/backend/.env.example` | Complete documentation of all env vars |
| `/README.md` | Complete setup instructions |

### New Documentation

| Document | Purpose |
|----------|---------|
| `CODEBASE_AUDIT.md` | Full audit of current state |
| `PICKS_GETTER_MODULARIZATION_PLAN.md` | Plan for splitting large service |
| `DEPLOYMENT_GUIDE.md` | Production deployment instructions |
| `NBA_MASTER_HUB_ARCHITECTURE.md` | Why sync fails on fresh servers |

---

## picks_getter_service.py Modularization Plan ✅

Created detailed plan for Option C (parallel implementation):

1. Extract utilities to `services/picks/game_utils.py`
2. Extract hit rate calculator to `services/picks/hit_rate_service.py`
3. Extract photo service to `services/picks/photo_service.py`
4. Extract player stats resolver to `services/picks/player_stats_resolver.py`
5. Extract tier builder to `services/picks/tier_builder.py`
6. Extract parlay builder to `services/picks/parlay_builder.py`
7. Extract board formatter to `services/picks/board_formatter.py`
8. Reduce original to thin facade

**Status**: Plan documented, not yet implemented (deferred per user instruction)

---

## Verification

```bash
# Backend status
curl http://localhost:8001/api/v3/board
# Returns: 88 players, 1041 props ✅

# All imports working
python3 -c "from services.engines import DemonGoblinEngine" ✅
```

---

## Files Changed (Git Summary)

### Moved (18 files)
- `/backend/*.py` engines → `/backend/services/engines/`
- `/backend/*.py` services → `/backend/services/`

### Created (14 files)
- `/backend/services/engines/__init__.py`
- `/backend/models/__init__.py`
- `/backend/models/player.py`
- `/backend/models/prop.py`
- `/backend/models/sync.py`
- `/backend/models/board.py`
- `/backend/models/user.py`
- `/CODEBASE_AUDIT.md`
- `/COLLECTION_RESPONSIBILITY_MAP.md`
- `/API_SURFACE.md`
- `/PICKS_GETTER_MODULARIZATION_PLAN.md`
- `/README.md` (updated)
- `/backend/.env.example` (updated)
- `/CHANGES_SUMMARY.md`

### Modified (50+ files)
- All files with engine/service imports updated
- Import paths changed from `from X` to `from services.X` or `from services.engines.X`

---

## Next Steps

1. **Phase 4: Sync Pipeline** - Deferred per user instruction
2. **picks_getter_service.py modularization** - Plan ready, implementation deferred
3. **Push to GitHub** - Use "Save to Github" feature
4. **Production deployment** - Follow `DEPLOYMENT_GUIDE.md`

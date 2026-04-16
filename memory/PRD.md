# PropVision - Product Requirements Document

## Sync Architecture v2

### Phase 1: Foundation (COMPLETE)
- Event bus, rebuild coordinator, budget manager, observability endpoints

### Phase 2: NBA Migration (COMPLETE)
- All NBA live board writes route through coordinator → UnifiedPipeline(NBAAdapter) → elite_*

### Phase 3: MLB Migration (COMPLETE)
- All MLB live board writes route through coordinator → UnifiedPipeline(MLBAdapter) → mlb_*
- Both sports now use identical architecture

**One Architecture, Two Sports — Verified:**
```
ANY trigger (manual, hourly, daily, startup)
  → Event Bus → BoardEvent
    → Rebuild Coordinator (dedup 30s, cooldown 60s, per-sport lock)
      → UnifiedPipeline(SportAdapter)
        → Phase 1-5: Load, Enrich, Score, Validate, Select
        → Phase 6: Atomic publish to live tier collections
        → Phase 6b: Market Moves snapshot diff
        → Phase 7: Gemini non-blocking enrichment
```

**Rewired triggers (both sports):**
| Trigger | Source | Path |
|---------|--------|------|
| `initial_autonomous_sync` | startup | coordinator (live) + legacy fallback |
| `scheduled_hourly_full_sync` | hourly | coordinator (live) + legacy fallback |
| `scheduled_daily_sync` step 6 | daily 4:20 AM | coordinator (live) + legacy fallback |
| `scheduled_mlb_daily_sync` | daily 4:23 AM | coordinator (live) + legacy fallback |
| `POST /api/nba/sync/master` | manual | coordinator (live) |
| `POST /api/nba/sync/elite-top-10` | manual | coordinator (live) |
| `POST /api/mlb/sync/master` | manual | coordinator (live) |
| `POST /api/v3/mlb/ferrari-pipeline` | manual | coordinator (live) |
| `POST /api/v3/mlb/rebuild` | manual | coordinator (live) |
| `POST /api/v2/coordinator/trigger` | manual | coordinator (live) |

**Legacy paths disabled as live publishers (preserved as fallback):**
- `demon_goblin_engine` (NBA)
- `mlb_sync_engine` (MLB)
- `mlb_master_sync` (MLB)
- `mlb_tier_service` (MLB)

**Validation results:**
| Sport | Pipeline | Duration | Safe Haven | Front Lines | War Zone |
|-------|----------|----------|-----------|-------------|----------|
| NBA | run_id=67ed190d | 22.7s | 10 | 10 | 10 |
| MLB | run_id=bc735c44 | 16.0s | 2 | 10 | 10 |

### Phase 4: Event-Driven Activation (NEXT)
- OddsDeltaEngine: budget-aware polling with hot/warm/cold pools
- InjuryWatcher: normalized change detection
- GameClockWatcher: lock window detection
- Board reacts to meaningful events, not just cron timing

### Phase 5: Cleanup (FUTURE)
- Retire legacy files to archive
- Drop orphaned collections
- Consolidate scheduler (20 jobs → 6)
- Consolidate endpoints (17 POST → 4)

## Observability Endpoints
| Endpoint | Purpose |
|----------|---------|
| `GET /api/v2/coordinator/status` | Mode, locks, counters, last publish per sport |
| `GET /api/v2/odds/budget` | Budget tracking per sport/pool |
| `GET /api/v2/event-bus/stats` | Throughput metrics |
| `POST /api/v2/coordinator/trigger` | Manual rebuild via coordinator |

## Key Files
| File | Purpose |
|------|---------|
| `services/event_bus.py` | Async pub/sub for BoardEvents |
| `services/rebuild_coordinator.py` | Dedup, scope, lock, dispatch |
| `services/odds_budget_manager.py` | Budget tracking |
| `services/unified_pipeline.py` | 7-phase pipeline framework |
| `services/market_moves_engine.py` | Board-diff engine |
| `services/adapters/nba_adapter.py` | NBA scoring + tier selection |
| `services/adapters/mlb_adapter.py` | MLB scoring + tier selection |

---
*Last Updated: April 17, 2026*

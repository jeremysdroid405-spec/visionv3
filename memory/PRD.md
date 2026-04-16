# PropVision - Product Requirements Document

## Sync Architecture v2

### Phase 1: Foundation (COMPLETE)
- Event bus, rebuild coordinator, budget manager
- Observability endpoints
- Shadow mode alongside existing system

### Phase 2: NBA Migration (COMPLETE)
All NBA live board writes now route through ONE authoritative path:

```
ANY NBA trigger (manual, hourly, daily, startup)
  → Event Bus → BoardEvent
    → Rebuild Coordinator (dedup, scope classify, lock)
      → UnifiedPipeline(NBAAdapter)
        → Phase 1-5: Load, Enrich, Score, Validate, Select
        → Phase 6: Atomic publish → elite_safe_haven / elite_front_lines / elite_war_zone
        → Phase 6b: Market Moves snapshot diff
        → Phase 7: Gemini non-blocking enrichment
```

**Rewired triggers:**
| Trigger | Source | Status |
|---------|--------|--------|
| `initial_autonomous_sync` | startup | Coordinator (live) with legacy fallback |
| `scheduled_hourly_full_sync` | hourly | Coordinator (live) with legacy fallback |
| `scheduled_daily_sync` step 6 | daily 4:20 AM | Coordinator (live) with legacy fallback |
| `POST /api/nba/sync/master` | manual | Coordinator (live) |
| `POST /api/nba/sync/elite-top-10` | manual | Coordinator (live) |
| `POST /api/v2/coordinator/trigger?sport=nba` | manual | Coordinator (live) |

**demon_goblin_engine**: Preserved but disabled as live publisher. Available as fallback if coordinator fails.

**Verified:**
- Coordinator: NBA=LIVE, MLB=SHADOW
- Pipeline: UnifiedPipeline(NBAAdapter) run_id=67ed190d, 22.7s
- Publish: elite_safe_haven=10, elite_front_lines=10, elite_war_zone=10
- Market Moves: Tied to every publish via Phase 6b
- MLB: Untouched (shadow mode)
- Dedup: Working (rapid-fire → deduped within 30s window)
- Cooldown: 60s between same-sport rebuilds (bypass for manual)
- Fallback: Legacy demon_goblin if coordinator fails

### Phase 3: MLB Migration (NEXT)
Same pattern — set MLB to live mode, route all MLB syncs through coordinator.

### Phase 4: Event-Driven Activation (FUTURE)
OddsDeltaEngine, InjuryWatcher, GameClockWatcher feed into coordinator.

### Phase 5: Cleanup (FUTURE)
Retire legacy files, drop orphaned collections.

## Core Architecture

### Live Tier Collections (Single Source of Truth)
| Sport | Safe Haven | Front Lines | War Zone |
|-------|-----------|-------------|----------|
| NBA | elite_safe_haven | elite_front_lines | elite_war_zone |
| MLB | mlb_safe_haven | mlb_front_lines | mlb_war_zone |

### Observability Endpoints
| Endpoint | Purpose |
|----------|---------|
| `GET /api/v2/coordinator/status` | Mode, locks, counters, last publish |
| `GET /api/v2/odds/budget` | Budget tracking per sport/pool |
| `GET /api/v2/event-bus/stats` | Throughput metrics |
| `POST /api/v2/coordinator/trigger` | Manual rebuild via coordinator |

### Key Files
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

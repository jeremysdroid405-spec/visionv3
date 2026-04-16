# PropVision - Product Requirements Document

## Sync Architecture v2 — All Phases

### Phase 1: Foundation (COMPLETE) — Event bus, coordinator, budget manager
### Phase 2: NBA Migration (COMPLETE) — All NBA through coordinator → pipeline
### Phase 3: MLB Migration (COMPLETE) — All MLB through coordinator → pipeline

### Phase 4: Event-Driven Activation (COMPLETE)

**Watchers (staged activation):**
| Watcher | Status | Interval | Trigger Type | Notes |
|---------|--------|----------|-------------|-------|
| InjuryWatcher | ACTIVE | 120s | `injury_change` (high) | Tracks 181 players, diffs status changes |
| GameClockWatcher | ACTIVE | 300s | `game_lock` (medium) | 30-min lock window, dedupes per game |
| OddsDeltaWatcher | STANDBY | 600s | `odds_delta` (medium/high) | Enable via admin after volume observation |

**Coordinator safeguards:**
| Safeguard | Value | Bypass |
|-----------|-------|--------|
| Dedup window | 30 seconds | — |
| Per-sport cooldown | 60 seconds | Manual triggers |
| Max rebuilds/hr/sport | 12 | Manual triggers |
| Per-trigger-class toggle | Independently on/off | — |

**Admin endpoints:**
| Endpoint | Purpose |
|----------|---------|
| `GET /api/v2/watchers/status` | Per-watcher stats + coordinator summary |
| `POST /api/v2/watchers/toggle` | Enable/disable any watcher at runtime |
| `GET /api/v2/coordinator/status` | Full metrics: events, scopes, durations, rate limits |

**One Architecture, Two Sports — Verified:**
```
Event Sources (InjuryWatcher, GameClockWatcher, OddsDeltaWatcher, Manual, Scheduler)
  → Event Bus → BoardEvent
    → Rebuild Coordinator
      ├── Dedup (30s window)
      ├── Trigger class gate (per-type on/off)
      ├── Scope classification (NO-OP / TARGETED / FULL)
      ├── Cooldown check (60s per sport)
      ├── Rate limit check (12/hr per sport)
      ├── Lock check (no concurrent rebuilds per sport)
      └── Dispatch → UnifiedPipeline(SportAdapter)
            → Phase 1-5: Load, Enrich, Score, Validate, Select
            → Phase 6: Atomic publish to live tier collections
            → Phase 6b: Market Moves snapshot diff
            → Phase 7: Gemini non-blocking enrichment
```

### Phase 5: Cleanup (NEXT)
- Retire legacy files to archive
- Drop orphaned collections
- Consolidate scheduler (20 jobs → 6)

## Key Files
| File | Purpose | Lines |
|------|---------|-------|
| `services/event_bus.py` | Async pub/sub | 105 |
| `services/rebuild_coordinator.py` | Dedup, scope, lock, rate limit, dispatch | 298 |
| `services/odds_budget_manager.py` | Budget tracking | 193 |
| `services/watchers.py` | InjuryWatcher, GameClockWatcher, OddsDeltaWatcher | 285 |
| `services/unified_pipeline.py` | 7-phase pipeline | 544 |
| `services/market_moves_engine.py` | Board-diff engine | 200 |

---
*Last Updated: April 17, 2026*

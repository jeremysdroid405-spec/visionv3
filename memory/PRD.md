# PropVision - Product Requirements Document

## Original Problem Statement
Restructure the React/FastAPI betting app to a 100% Local-First Database Model, integrating multi-sport support (NBA/MLB). Implement automated feature engineering, generative AI scout intelligence, and a unified pipeline architecture for production reliability.

## Sync Architecture v2 — Phase 1 Complete

### New Foundation Modules (Shadow Mode)
| Module | Lines | Purpose |
|--------|-------|---------|
| `services/event_bus.py` | 105 | Async pub/sub for BoardEvents |
| `services/rebuild_coordinator.py` | 256 | Event dedup, scope classification, lock management |
| `services/odds_budget_manager.py` | 193 | 5M/month Odds API budget tracking with hot/warm/cold pools |

### Observability Endpoints
| Endpoint | Purpose |
|----------|---------|
| `GET /api/v2/coordinator/status` | Mode, locks, event/scope/rebuild counters |
| `GET /api/v2/odds/budget` | Daily/hourly usage, per-sport allocation, recommended intervals |
| `GET /api/v2/event-bus/stats` | Throughput: published, delivered, by type/sport |
| `POST /api/v2/coordinator/trigger` | Manual rebuild trigger via event bus |

### Shadow Mode Behavior
- Coordinator receives events from scheduled jobs + manual triggers
- Classifies scope (NO_OP / TARGETED / FULL)
- Logs what it WOULD dispatch — does NOT actually run pipelines
- Existing scheduler continues to own all live publishes
- Dedup within 30s window, 60s cooldown between same-sport rebuilds

### Event Emission Points (Phase 1)
- `scheduled_hourly_full_sync` → emits `scheduled_safety` event
- `scheduled_mlb_daily_sync` → emits `scheduled_safety` event
- `scheduled_live_injury_check` → emits `injury_change` event on vacuum triggers
- `POST /api/v2/coordinator/trigger` → emits `manual` event

### Backward Compatibility
- All existing scheduler jobs unchanged
- All existing endpoints unchanged
- All existing collections unchanged
- Foundation modules are additive-only — zero disruption to live board

## Core Architecture (Existing)

### Unified Pipeline Framework
```
UnifiedPipeline (shared framework)
  ├── Phase 1: LOAD        — Read board, flatten, deduplicate
  ├── Phase 2-3: ENRICH    — Stats + scoring (adapter-specific)
  ├── Phase 4: VALIDATE    — Attach validation metadata
  ├── Phase 5: SELECT      — Tier classification + gate checks
  ├── Phase 6: PUBLISH     — Atomic writes (temp + rename)
  ├── Phase 6b: MARKET MOVES — Board-diff tracking (shared engine)
  └── Phase 7: INTEL       — Gemini enrichment (non-blocking)
```

### Live Tier Collections
| Sport | Safe Haven | Front Lines | War Zone |
|-------|-----------|-------------|----------|
| NBA | elite_safe_haven | elite_front_lines | elite_war_zone |
| MLB | mlb_safe_haven | mlb_front_lines | mlb_war_zone |

## Pending Tasks
- **Phase 2**: NBA migration — route all NBA syncs through coordinator → UnifiedPipeline
- **Phase 3**: MLB migration — same for MLB
- **Phase 4**: Event-driven activation — OddsDeltaEngine, InjuryWatcher, GameClockWatcher
- **Phase 5**: Cleanup — retire legacy files, drop orphaned collections
- P1: Google OAuth integration
- P1: Stripe payments integration

---
*Last Updated: April 17, 2026*

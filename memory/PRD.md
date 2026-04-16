# PropVision - Product Requirements Document

## Core Architecture: Event-Driven Sync v2 (All Phases Complete)

### Sync Architecture
- **EventBus** → **RebuildCoordinator** → **UnifiedPipeline(SportAdapter)** → Atomic Publish
- Both NBA and MLB route through the single authoritative publish path
- Per-sport mode (live/shadow), per-trigger-class toggles, dedup, cooldown, rate limiting
- Market Moves diff with exit-reason classification post-publish
- Gemini batch enrichment post-publish (non-blocking)

### Phase Status
- Phase 1: Foundation (EventBus, Coordinator, Budget Manager) — COMPLETE
- Phase 2: NBA Migration — COMPLETE
- Phase 3: MLB Migration — COMPLETE
- Phase 4: Event-Driven Activation (Watchers) — COMPLETE
- Phase 5: Legacy Cleanup — PENDING user approval

## Injury Normalization Layer (COMPLETE)

### Source: BallDontLie API (structural authority)
- NBA: `/nba/v1/player_injuries` → 181 injuries, 5 status tiers
- MLB: `/mlb/v1/player_injuries` → 143 injuries, 5 status tiers + IL designations

### Field Classification — Structural vs Display-Only Firewall
Every injury record in `injuries_normalized` enforces a hard separation:

**STRUCTURAL (top-level)**: sport, player_name, bdl_id, team, team_id, position, status, tier_level, risk, color, return_date, injury_date, source, synced_at, first_seen_at, status_changed_at

**DISPLAY_ONLY (nested)**: raw_status, description, short_comment, injury_type, injury_detail, injury_side

### Dynamic Recency Window (Live Injury Advantage)
| Game State | Window | Rationale |
|-----------|--------|-----------|
| Default | 12h | Capture today's slate changes |
| Within 2h of tipoff | 6h | Late scratch zone |
| Game live | 2h | Minimal — stale injuries irrelevant |
| Game finished | Skipped | Not considered active |

## Multi-Source Injury Sensor (COMPLETE)

### Source Trust Hierarchy
| Source | Role | Provides |
|--------|------|----------|
| BDL | STRUCTURAL AUTHORITY | Player IDs, return dates, injury detail |
| ESPN | TIMING AUTHORITY (NBA) | Status changes first |
| NBA Official PDF | TIMING AUTHORITY (NBA) | League-mandated status changes |

## CV Calculation Standard (UNIFIED — April 16, 2026)
- **All CV calculations now use `np.std(ddof=1)` (sample standard deviation)**
- Files standardized: `oracle_apex_service.py`, `vegas_regression_model.py`, `intel_suite_calculator.py`
- Rationale: For L10 samples (N=10), sample std dev is statistically correct

## Database Configuration
- **DB_NAME**: `pick_vision` (set in backend/.env)
- Server uses `os.environ['DB_NAME']` → `pick_vision`
- Key collections in `pick_vision`:
  - NBA tiers: `elite_safe_haven`, `elite_front_lines`, `elite_war_zone`
  - MLB tiers: `mlb_safe_haven`, `mlb_front_lines`, `mlb_war_zone`
  - Master hubs: `nba_master_hub_2026` (559), `mlb_master_hub_2026` (777)
  - Injuries: `injuries_normalized` (326)

## Key API Endpoints
- `POST /api/nba/sync/master` — NBA coordinator → pipeline
- `POST /api/mlb/sync/master` — MLB coordinator → pipeline
- `GET /api/v3/ferrari/safe-haven?sport=nba` — NBA Safe Haven picks
- `GET /api/v3/mlb/ferrari/safe-haven` — MLB Safe Haven picks
- `GET /api/v2/coordinator/status` — Coordinator observability
- `GET /api/v3/command-center/ticker` — News ticker
- `POST /api/v3/mlb/test-scheduled-sync` — Non-manual MLB sync test

## Upcoming Tasks
- P1: Google/Apple OAuth (via `integration_playbook_expert_v2`)
- P1: Stripe payments (via `integration_playbook_expert_v2`)
- P2: Wind Tunnel weather API
- P2: Dashboard.jsx refactor
- Phase 5: Drop corrupt/legacy collections (pending user approval)

## Key Files
| File | Purpose |
|------|---------|
| `services/rebuild_coordinator.py` | Event-driven coordinator |
| `services/event_bus.py` | Async pub/sub for BoardEvents |
| `services/watchers.py` | Injury, game clock, odds delta watchers |
| `services/unified_pipeline.py` | Shared pipeline framework |
| `services/adapters/nba_adapter.py` | NBA pipeline adapter |
| `services/adapters/mlb_adapter.py` | MLB pipeline adapter |
| `services/market_moves_engine.py` | Board diff and exit classification |
| `services/injury_normalization.py` | Structural/display firewall |
| `services/injury_sensor.py` | Multi-source polling and merge |
| `services/oracle_apex_service.py` | NBA Safe Haven scoring |
| `services/mlb_tier_sorter.py` | MLB tier scoring |

---
*Last Updated: April 16, 2026*

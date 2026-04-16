# PropVision - Product Requirements Document

## Sync Architecture v2 — All Phases Complete

### Phase 1: Foundation (COMPLETE) — Event bus, coordinator, budget manager
### Phase 2: NBA Migration (COMPLETE) — All NBA through coordinator -> pipeline  
### Phase 3: MLB Migration (COMPLETE) — All MLB through coordinator -> pipeline
### Phase 4: Event-Driven Activation (COMPLETE) — Watchers active

## Injury Normalization Layer (COMPLETE)

### Source: BallDontLie API (structural authority)
- NBA: `/nba/v1/player_injuries` -> 181 injuries, 5 status tiers
- MLB: `/mlb/v1/player_injuries` -> 143 injuries, 5 status tiers + IL designations

### Normalized Status Hierarchy
| Tier Level | NBA Status | MLB Status | Risk | Color |
|-----------|-----------|-----------|------|-------|
| 1 | PROBABLE | PATERNITY, BEREAVEMENT | LOW | green |
| 2 | QUESTIONABLE | DAY_TO_DAY | MEDIUM | yellow |
| 3 | DOUBTFUL | IL_SHORT (7-day, 10-day) | HIGH | orange |
| 4 | OUT | IL_STANDARD (15-day), SUSPENDED | HIGH | red |
| 5 | OUT_FOR_SEASON | IL_EXTENDED (60-day) | CRITICAL | red |

### Collection: `injuries_normalized`
| Field | Type | Description |
|-------|------|-------------|
| sport | string | "nba" / "mlb" |
| player_name | string | Full name |
| bdl_id | int | BDL player ID (direct hub join) |
| status | string | Normalized tier name |
| tier_level | int | 1-5 severity |
| risk | string | LOW/MEDIUM/HIGH/CRITICAL |
| return_date | string | Expected return (YYYY-MM-DD) |
| injury_date | string | When injury occurred (MLB only) |
| injury_type | string | Category (MLB only) |
| injury_detail | string | Specific injury (MLB only) |
| injury_side | string | Body side (MLB only) |
| description | string | Context |

## Multi-Source Injury Sensor (COMPLETE)

### Architecture
```
  Source Adapters (BDL, ESPN, NBA Official)
    -> Sensor Loop (dynamic cadence per sport)
      -> Multi-Source Merge (strict precedence rules)
        -> Normalizer (shared status hierarchy)
          -> Change Detector (tier change, return shift, new/cleared)
            -> Event Emitter -> Coordinator -> Targeted Pipeline Rebuild
```

### Source Trust Hierarchy
| Source | Role | Provides | Does NOT provide |
|--------|------|----------|------------------|
| BDL | STRUCTURAL AUTHORITY | Player IDs, return dates, injury detail | Fast timing |
| ESPN | TIMING AUTHORITY (NBA) | Status changes first | Player IDs, return dates |
| NBA Official PDF | TIMING AUTHORITY (NBA) | League-mandated status changes | Player IDs, return dates |

### Critical Rule: Live Injury Advantage Input
The Live Injury Advantage engine (`injury_advantage.py`) MUST ONLY read from:
- BDL-derived normalized injuries in `injuries_normalized`
- OR the merged normalized state (which is BDL-only by design)

Timing sources (ESPN, NBA Official) NEVER inject records into `injuries_normalized`.
They only annotate BDL records with timing disagreement signals.

### InjuryWatcher/Sensor Triggers
Triggers only on:
- `tier_level` changed (status escalation/de-escalation)
- `return_date` shifted
- New injury appeared

Does NOT trigger on: description text changes, same-tier updates.

### Key Files
| File | Purpose |
|------|---------|
| `services/injury_normalization.py` | Fetch, normalize, persist, compare |
| `services/injury_sensor.py` | Multi-source polling, merge, diff, emit |
| `services/injury_sources/bdl_source.py` | BDL adapter (structural authority) |
| `services/injury_sources/espn_source.py` | ESPN adapter (timing authority) |
| `services/injury_sources/nba_official_source.py` | NBA PDF adapter (timing authority) |
| `services/injury_advantage.py` | Board-scoped, recency-gated advantage engine |

---
*Last Updated: April 16, 2026*

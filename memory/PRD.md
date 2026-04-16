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

### Field Classification — Structural vs Display-Only Firewall

Every injury record in `injuries_normalized` enforces a hard separation:

**STRUCTURAL (top-level) — Logic-safe fields ONLY**
| Field | Type | Description |
|-------|------|-------------|
| sport | string | "nba" / "mlb" |
| player_name | string | Full name |
| bdl_id | int | BDL player ID |
| team | string | Team abbreviation |
| team_id | int | BDL team ID |
| position | string | Player position |
| status | string | Normalized tier name (OUT, QUESTIONABLE, etc.) |
| tier_level | int | 1-5 severity |
| risk | string | LOW/MEDIUM/HIGH/CRITICAL |
| color | string | UI color hint derived from tier |
| return_date | string | Expected return (YYYY-MM-DD) |
| injury_date | string | When injury occurred (MLB only) |
| source | string | Adapter origin ("bdl") |
| synced_at | string | ISO timestamp |
| first_seen_at | string | When first observed |
| status_changed_at | string | When tier last changed |

**DISPLAY_ONLY (nested under `display_only`) — Narrative fields, NEVER trigger logic**
| Field | Type | Description |
|-------|------|-------------|
| raw_status | string | Original BDL text before normalization |
| description | string | Free-text injury narrative |
| short_comment | string | Truncated narrative |
| injury_type | string | Free-text category (e.g. "Knee") |
| injury_detail | string | Free-text detail (e.g. "ACL Tear") |
| injury_side | string | Free-text body side (e.g. "Left") |

### Input Firewall API
```python
from services.injury_normalization import firewall_for_logic, extract_display

# Logic consumer: strips ALL narrative fields
safe_record = firewall_for_logic(db_record)

# Display consumer: reads quarantined namespace
display = db_record.get("display_only", {})
desc = display.get("description", "")
```

### Consumers Audited
| Consumer | Uses Structural Only | Verified |
|----------|---------------------|----------|
| injury_sensor._detect_changes | tier_level, return_date, status | YES |
| injury_sensor._make_change | player_name, team, bdl_id, status, tier_level | YES |
| injury_sensor._emit_changes | team, change_type, tier_delta | YES |
| injury_normalization.is_meaningful_change | tier_level, return_date | YES |
| injury_advantage._get_meaningful_injuries | sport, tier_level, status_changed_at | YES |
| injury_advantage.compute | team, player_name, tier_level | YES |
| injury_vacuum_service | display_only.short_comment (display) | YES |
| injury_service (legacy compat) | display_only.description (display) | YES |

### Normalized Status Hierarchy
| Tier Level | NBA Status | MLB Status | Risk | Color |
|-----------|-----------|-----------|------|-------|
| 1 | PROBABLE | PATERNITY, BEREAVEMENT | LOW | green |
| 2 | QUESTIONABLE | DAY_TO_DAY | MEDIUM | yellow |
| 3 | DOUBTFUL | IL_SHORT (7-day, 10-day) | HIGH | orange |
| 4 | OUT | IL_STANDARD (15-day), SUSPENDED | HIGH | red |
| 5 | OUT_FOR_SEASON | IL_EXTENDED (60-day) | CRITICAL | red |

## Multi-Source Injury Sensor (COMPLETE)

### Architecture
```
  Source Adapters (BDL, ESPN, NBA Official)
    -> Sensor Loop (dynamic cadence per sport)
      -> Multi-Source Merge (strict precedence rules)
        -> Normalizer (structural/display split)
          -> Change Detector (structural fields only)
            -> Event Emitter -> Coordinator -> Targeted Pipeline Rebuild
```

### Source Trust Hierarchy
| Source | Role | Provides | Does NOT provide |
|--------|------|----------|------------------|
| BDL | STRUCTURAL AUTHORITY | Player IDs, return dates, injury detail | Fast timing |
| ESPN | TIMING AUTHORITY (NBA) | Status changes first | Player IDs, return dates |
| NBA Official PDF | TIMING AUTHORITY (NBA) | League-mandated status changes | Player IDs, return dates |

### Critical Rule: Live Injury Advantage Input
The Live Injury Advantage engine MUST ONLY read from:
- BDL-derived normalized injuries in `injuries_normalized`
- OR the merged normalized state (which is BDL-only by design)

Timing sources (ESPN, NBA Official) NEVER inject records into `injuries_normalized`.
Narrative fields NEVER participate in trigger logic.

### Dynamic Recency Window (Live Injury Advantage)
| Game State | Window | Rationale |
|-----------|--------|-----------|
| Default (no games nearby) | 12 hours | Capture today's slate changes |
| Within 2h of tipoff | 6 hours | Late scratch zone — only very recent |
| Game live (started < 4h ago) | 2 hours | Minimal — stale injuries irrelevant |
| Game finished (> 4h ago) | Skipped | Not considered active |

Exposed via `GET /api/v3/vacuum/live-alerts?sport=nba` as `recency_window_hours`.

### Key Files
| File | Purpose |
|------|---------|
| `services/injury_normalization.py` | Firewall, field classification, normalize, persist |
| `services/injury_sensor.py` | Multi-source polling, merge, diff, emit |
| `services/injury_sources/bdl_source.py` | BDL adapter (structural authority) |
| `services/injury_sources/espn_source.py` | ESPN adapter (timing authority) |
| `services/injury_sources/nba_official_source.py` | NBA PDF adapter (timing authority) |
| `services/injury_advantage.py` | Board-scoped, recency-gated advantage engine |

---
*Last Updated: April 16, 2026*

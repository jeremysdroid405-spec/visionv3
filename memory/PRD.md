# PropVision - Product Requirements Document

## Sync Architecture v2 — All Phases Complete

### Phase 1: Foundation (COMPLETE) — Event bus, coordinator, budget manager
### Phase 2: NBA Migration (COMPLETE) — All NBA through coordinator → pipeline  
### Phase 3: MLB Migration (COMPLETE) — All MLB through coordinator → pipeline
### Phase 4: Event-Driven Activation (COMPLETE) — Watchers active

## Injury Normalization Layer (COMPLETE)

### Source: BallDontLie API (replaced ESPN)
- NBA: `/nba/v1/player_injuries` → 181 injuries, 5 status tiers
- MLB: `/mlb/v1/player_injuries` → 143 injuries, 5 status tiers + IL designations

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

### InjuryWatcher — Meaningful Changes Only
Triggers only on:
- `tier_level` changed (status escalation/de-escalation)
- `return_date` shifted
- New injury appeared

Does NOT trigger on: description text changes, same-tier updates.

### Key Files
| File | Purpose |
|------|---------|
| `services/injury_normalization.py` | Fetch, normalize, persist, compare |
| `services/watchers.py` InjuryWatcher | BDL polling → meaningful change detection |
| `services/injury_service.py` | Legacy compat wrapper (writes dg_injuries) |
| `services/injury_vacuum_service.py` | Reads from injuries_normalized |

---
*Last Updated: April 17, 2026*

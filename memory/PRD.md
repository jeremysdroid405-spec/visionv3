# PropVision - Product Requirements Document

## Sync Architecture v2 — Phases 1-4 Complete

### Phase 1: Foundation (COMPLETE) — Event bus, coordinator, budget manager
### Phase 2: NBA Migration (COMPLETE) — All NBA through coordinator → pipeline
### Phase 3: MLB Migration (COMPLETE) — All MLB through coordinator → pipeline
### Phase 4: Event-Driven Activation (COMPLETE) — Watchers active, OddsDelta in controlled mode

## Volatility System — Single Source of Truth

### Architecture
All volatility interpretation routes through ONE shared function:
```python
from services.volatility_profile import get_volatility_profile
profile = get_volatility_profile(cv=0.55, stat_type="Hits", line=0.5)
# profile.score, profile.label, profile.is_extreme, profile.badge_key, profile.family
```

### Prop Family Thresholds

| Family | Stat Types | Line Range | Moderate | High | Extreme |
|--------|-----------|-----------|----------|------|---------|
| **mlb_binary** | Hits, Runs, RBIs, HR, SB, Earned Runs, Walks | ≤ 1.5 | 0.55 | 0.80 | 1.00 |
| **mlb_counting** | Total Bases, Batter K, Pitcher K, H+R+RBI, Doubles | any | 0.45 | 0.65 | 0.85 |
| **nba_low_line** | AST, REB, STL, BLK, 3PM, TO | ≤ 4.5 | 0.50 | 0.70 | 0.90 |
| **nba_mid_line** | PTS, REB, AST, STL, BLK, PA, PR | 4.5-15 | 0.40 | 0.60 | 0.80 |
| **nba_high_line** | PRA, PTS, P+A, P+R, Fantasy | > 15 | 0.30 | 0.50 | 0.70 |
| **default** | Unknown prop types | any | 0.40 | 0.60 | 0.80 |

### What Changed
- **Before**: Flat CV > 0.70 threshold for all props → Hits 0.5 (CV 0.77) falsely flagged extreme
- **After**: Family-aware thresholds → Hits 0.5 (CV 0.77) correctly labeled `moderate` in `mlb_binary` family (extreme = 1.00)

### Consumers Updated
| Location | What | Before | After |
|----------|------|--------|-------|
| `ferrari_tiers.py` overlay | `volatility_score`, `volatility_label`, `volatility_family` | Not set | Set from shared profile for ALL picks |
| `ferrari_tiers.py` scout badge | `volatility_extreme` badge | `cv > 0.70` | `vol_profile.is_extreme` |
| `ferrari_tiers.py` intel reasons | Confidence = SPECULATIVE | `cv > 0.70` | `vol_profile.label in ("high", "extreme")` |
| `ferrari_tiers.py` overlay | Badge reconciliation | Stale cache could contradict | Cache badges reconciled with profile |
| `nba_adapter.py` gate check | CV fail gate | `cv > 0.90` | `vol.label == "extreme"` |
| `mlb_adapter.py` Safe Haven gates | CV fail gate | `cv > 1.10` (binary) / `cv > max_cv` | `vol.is_extreme` (binary) / `vol.label in ("high","extreme")` |

### Key Files
| File | Purpose |
|------|---------|
| `services/volatility_profile.py` | Single source of truth — `get_volatility_profile()` |
| `services/event_bus.py` | Async pub/sub |
| `services/rebuild_coordinator.py` | Dedup, scope, lock, rate limit, dispatch |
| `services/odds_budget_manager.py` | Budget tracking |
| `services/watchers.py` | InjuryWatcher, GameClockWatcher, OddsDeltaWatcher |
| `services/unified_pipeline.py` | 7-phase pipeline |

---
*Last Updated: April 17, 2026*

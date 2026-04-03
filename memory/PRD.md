# PropVision - Product Requirements Document

## Overview
PropVision is a sports analytics platform for NBA player props, providing data-driven insights for betting decisions.

---

## Latest Update (2026-04-03): PropVision v7 - True Probability Engine

### MAJOR ARCHITECTURE OVERHAUL

The system has been upgraded from ranking picks by "Sharp Line Edge" to calculating a **True Probability** score that blends multiple factors for maximum edge extraction.

### True Probability Formula (0-100%)

```
True_Prob = (
    Historical_Consistency × 0.45 +    # Recent form is king
    Sharp_Market_Signal × 0.25 +       # Sharp money knows  
    Statistical_Floor × 0.15 +          # Safety net analysis
    Contextual_Modifiers × 0.15        # Game environment
)
```

**Component Breakdown:**

| Component | Weight | Formula |
|-----------|--------|---------|
| Historical Consistency | 45% | (L3 × 0.40) + (L5 × 0.35) + (L10 × 0.25) |
| Sharp Market Signal | 25% | Sharp_Implied × Separation_Confidence |
| Statistical Floor | 15% | Cushion + Mode_Proximity - Variance_Penalty |
| Contextual Modifiers | 15% | DvP(+/-8) + Whistle(+/-5) + Vacuum(+/-5) + Blowout(-10) |

### Hard Kill Switches (Auto-Disqualify)

| Kill | Threshold | Reason |
|------|-----------|--------|
| L3 Cold | < 33% | Player is ice cold (0/3 or 1/3 recent) |
| L5 Cold | < 40% | Confirmed cold streak |
| No Sharp Edge | < 52% implied | Vegas doesn't see value |
| Line > Median | Line above season median | Against the statistical grain |
| Blowout + PTS/PRA | HIGH risk + scoring stat | Bench minutes risk |

### Tier Classification (by True Probability)

| Tier | True Probability | Description |
|------|------------------|-------------|
| Safe Haven | ≥ 72% | Elite locks - highest confidence |
| Front Lines | 62-71% | Strong plays - good value |
| War Zone | 52-61% | Value bets - edge exists but risk |
| Below Threshold | < 52% | Eliminated from consideration |

### Diversified Parlay Optimizer (NEW)

**Output per tier:**
- 5 optimized parlays (2-leg through 6-leg)
- Total: 15 parlays across all tiers

**Diversification Constraints:**
| Rule | Limit | Purpose |
|------|-------|---------|
| Max Player Appearances | 2 per tier | Avoid correlated loss |
| Max Team per Parlay | 2 | Diversify game risk |
| Max Stat Type per Parlay | 3 | Spread across categories |

**Parlay Selection:**
1. Generate all valid combinations (2-6 legs)
2. Filter by diversification rules
3. Calculate EV: `(Combined_Prob × Payout) - (1 - Combined_Prob)`
4. Select top EV-positive parlays respecting appearance limits

### New API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v3/ferrari/parlays` | GET | Get all optimized parlays |
| `/api/v3/ferrari/parlays?tier=safe_haven` | GET | Filter parlays by tier |

### Files Modified

- **MAJOR REWRITE**: `/app/backend/services/ferrari_tier_service.py`
  - Replaced Power Score formula with True Probability engine
  - Added L3/L5/L10 granular hit rate calculation
  - Integrated `TrueProbabilityEngine` from v7 module
  - Added `DiversifiedParlayOptimizer` for parlay generation
  - Added parlay storage to `ferrari_parlays` collection

- **NEW**: `/app/backend/services/propvision_v7_engine.py`
  - `TrueProbabilityEngine` class - calculates true probability
  - `DiversifiedParlayOptimizer` class - builds EV-positive parlays
  - Helper functions for L3/L5/L10 hit rates, median, mode, std_dev

- **MODIFIED**: `/app/backend/routes/ferrari_tiers.py`
  - Added `/v3/ferrari/parlays` endpoint

### Database Changes

| Collection | Description |
|------------|-------------|
| `ferrari_parlays` | NEW - Stores optimized parlays with EV and picks |

### Example API Response

```json
{
  "total_parlays": 13,
  "parlays_by_tier": {
    "safe_haven": 5,
    "front_lines": 4,
    "war_zone": 4
  },
  "safe_haven_parlays": [
    {
      "parlay_id": "safe_haven_1",
      "legs": 6,
      "expected_value": 9.634,
      "combined_probability": 25.94,
      "picks": [
        {"player_name": "Player A", "stat_type": "REB", "line": 2.5, "true_probability": 82.07}
      ]
    }
  ]
}
```

---

## Previous Updates

### Vision Intel Suite Standardization (2026-04-03)

Every pick now contains standardized fields:
- `blowout_risk`, `context_badges`, `defensive_momentum`
- `matchup_dvp`, `tempo/pace_delta`, `stability_index`
- `variance`, `target_lock_rationale`, `usage_ripple`
- `vision_insight`, `vision_summary`, `whistle_data`

### Badge Engine Migration to BDL (2026-04-03)

Migrated from stats.nba.com to BallDontLie Advanced Stats API for reliable badge generation.

---

## Core Architecture

### Backend Stack
- **FastAPI** - REST API framework
- **MongoDB** - Primary database (pick_vision)
- **Motor** - Async MongoDB driver

### Frontend Stack  
- **React** - UI framework
- **Tailwind CSS** - Styling
- **Shadcn/UI** - Component library

### Data Sources
- **BallDontLie API** - Player stats, game logs, advanced stats
- **PrizePicks API** - Props and lines
- **Odds API** - Sharp market prices
- **ESPN/NBA.com** - Referee assignments

---

## Key Collections

| Collection | Purpose |
|------------|---------|
| `dg_cached_board` | Cached player props board |
| `ferrari_safe_haven` | Top 10 Safe Haven picks |
| `ferrari_front_lines` | Top 10 Front Lines picks |
| `ferrari_war_zone` | Top 10 War Zone picks |
| `ferrari_scored` | All scored props before tier selection |
| `ferrari_parlays` | Optimized parlay combinations |

---

## Backlog

### P0 - Critical
- [x] PropVision v7 True Probability Engine
- [x] Diversified Parlay Optimizer
- [ ] AI Vision Summary Fix (Gemini returning null)

### P1 - High Priority
- [ ] Frontend parlay display component
- [ ] Parlay builder UI with manual selection

### P2 - Medium Priority
- [ ] Google OAuth integration
- [ ] Stripe payments integration

### P3 - Future
- [ ] Mobile responsive optimization
- [ ] Push notifications for picks
- [ ] Historical performance tracking

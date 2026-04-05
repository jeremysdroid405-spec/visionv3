# PropVision - Product Requirements Document

## Overview
PropVision is a sports analytics platform for NBA player props, providing data-driven insights for betting decisions.

---

## Latest Update (2026-04-05): PropVision v7.2 - Mode Edge Board Score Formula

### VERIFIED WORKING (Manual Testing)
- ✅ All 3 tiers populate (Safe Haven: 10, Front Lines: 10, War Zone: 5)
- ✅ War Zone contains ONLY Demons (`is_demon=True`)
- ✅ Safe Haven contains ONLY Goblins (`is_goblin=True`)
- ✅ `mode_edge` correctly calculated as `Mode - Line`
- ✅ `line_below_avg_bonus` uses actual cushion points (Season_Avg - Line)
- ✅ Sample Standard Deviation uses (N-1) denominator (Bessel's correction)
- ✅ BallDontLie is SSOT for all stats (no `dg_player_stats` fallbacks)
- ✅ Moderate Demons allowed in Front Lines (PP Edge >= 15% OR Hit Rate >= 65%)

### v7.2 Board Score Formula (ADDITIVE)
```
Board_Score = True_Probability + Sharp_Implied + PP_Edge + L5_Rate + L10_Rate 
            + Line_Below_Avg_Bonus + Mode_Edge - Penalties

Components:
- True_Probability: Weighted (Historical 45% + Sharp 25% + Floor 15% + Context 15%)
- Sharp_Implied: What smart money says (38%+ minimum)
- PP_Edge: Edge over PrizePicks break-even (positive = value)
- L5_Rate: Last 5 games hit rate (0-100%)
- L10_Rate: Last 10 games hit rate (0-100%)
- Line_Below_Avg_Bonus: Actual points of cushion (Season_Avg - Line)
  - Example: Avg 17.1, Line 9.5 → Bonus = 7.6 points
- Mode_Edge: How far the most frequent outcome beats the line (Mode - Line)
  - Example: Mode 22, Line 19.5 → Mode Edge = +2.5
- Penalties: Variance (-10 if std_dev > 6.0), DvP tiers, Blowout risk
```

### Tier Rules
| Tier | Who Goes Here |
|------|---------------|
| Safe Haven | Top Goblins only (alternate lines with edge) |
| Front Lines | Mid Goblins + "Safe" Demons (PP Edge >= 15% OR Hit Rate >= 65%) |
| War Zone | High Risk Demons only (PP Edge < 10% AND L10 <= 60%) |

---

## Previous Update (2026-04-04): PropVision v7.1 - Edge-First Board Score Formula

### v7.1 Board Score Formula (DEPRECATED)
```
Board_Score = Sharp_Implied + PP_Edge + Hit_Rate_Avg - Penalties
```

### Demon vs Goblin Edge Calculation
| Type | Break-even | Edge Formula |
|------|------------|--------------|
| Goblin (-137) | 57.8% | Sharp_Implied - 57.8% |
| Demon (+100) | 50% | Hit_Rate_Avg - 50% |

### Key Fixes in v7.1
1. **Stale Data Fix**: `run_bdl_game_logs_sync_batched` now runs in Phase 0 of optimized sync
2. **Hit Rate Source**: Uses `dg_cached_board.hit_rates` (fresh from sync) instead of calculating from stale `dg_player_stats`
3. **Demon Exemptions**: Demons are exempt from L3 < 33%, Season Median, and Trap Risk hard kills
4. **Deduplication**: A player can appear in BOTH Safe Haven (Goblin) AND War Zone (Demon)

---

## Previous Update (2026-04-03): PropVision v7 - True Probability Engine

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

### P0 - Critical (COMPLETED)
- [x] PropVision v7 True Probability Engine
- [x] Diversified Parlay Optimizer
- [x] v7.1 Edge-First Board Score Formula
- [x] Stale Game Logs Fix (BDL sync integrated)
- [x] L5/L10 Hit Rate Display on Frontend

### P1 - High Priority
- [ ] AI Vision Summary Fix (Gemini returning null for some picks)
- [ ] Frontend parlay display component
- [ ] Parlay builder UI with manual selection

### P2 - Medium Priority
- [ ] Google OAuth integration (Emergent-managed)
- [ ] Stripe payments integration (test keys in pod)

### P3 - Future
- [ ] Mobile responsive optimization
- [ ] Push notifications for picks
- [ ] Historical performance tracking
- [ ] Rebuild endpoint timeout optimization (currently ~120s)

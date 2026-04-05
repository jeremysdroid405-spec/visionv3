# PropVision - Product Requirements Document

## Overview
PropVision is a sports analytics platform for NBA player props, providing data-driven insights for betting decisions.

---

## Latest Update (2026-04-05): V2 Advanced Stats Integration

### MAJOR MILESTONE: Real Process Stats from BDL V2 API

The Vegas Killer model now uses **REAL PROCESS STATS** from the BallDontLie V2 Advanced Stats API instead of proxy calculations!

#### What Changed
- Created `bdl_advanced_stats_fetcher.py` to pull V2 stats from BDL API (GOAT tier access)
- Created `/api/v3/bdl-advanced/*` routes for fetching and managing advanced stats
- Integrated V2 stats into `vegas_killer_model.py` via `get_v2_features()` method
- Predictions now show `"data_source": "V2_ADVANCED"` when using real data

#### V2 Stats Available (100+ metrics)
| Category | Key Metrics |
|----------|-------------|
| **Efficiency** | usage_percentage, true_shooting_percentage, effective_field_goal_percentage |
| **Pace** | pace, pace_per_40, possessions |
| **Matchup** | matchup_fg_pct, matchup_player_points, matchup_3pt_pct, matchup_minutes |
| **Tracking** | touches, passes, speed, distance |
| **Shot Quality** | contested_fg_pct, uncontested_fg_pct, contested_shots |
| **Hustle** | deflections, loose_balls_recovered_total, charges_drawn |
| **Playmaking** | assist_percentage, assist_ratio, assist_to_turnover |
| **Impact** | pie (Player Impact Estimate), offensive_rating, defensive_rating, net_rating |

#### API Endpoints Added
| Endpoint | Purpose |
|----------|---------|
| `GET /api/v3/bdl-advanced/status` | Check stored stats status |
| `POST /api/v3/bdl-advanced/fetch-season/{season}` | Fetch season V2 stats |
| `POST /api/v3/bdl-advanced/fetch-multiple` | Fetch multiple seasons |
| `GET /api/v3/bdl-advanced/player/{player_id}` | Get player stats by ID |
| `GET /api/v3/bdl-advanced/player-by-name/{name}` | Get stats by name |
| `GET /api/v3/bdl-advanced/features/{name}/{stat}` | Extract features for prediction |

#### Example Prediction Response
```json
{
  "player_name": "LeBron James",
  "predicted": 17.99,
  "v2_advanced_stats": {
    "usage_rate": 0.241,
    "true_shooting": 0.513,
    "pace": 101.93,
    "touches": 78.8,
    "matchup_fg_pct": 0.447
  },
  "data_source": "V2_ADVANCED"
}
```

---

## Previous Update (2026-04-05): Vegas Killer V2 - Process Stats with Real Data

### NEW FEATURES ADDED
1. **Real Pace Data** - All 30 NBA teams with actual pace (possessions/48)
   - IND fastest (102.8), MIA slowest (95.8)
   - Pace delta and pace multiplier calculated per matchup
   
2. **Enhanced Matchup Features**
   - `opp_l10_pts_allowed` - Recent defensive trend
   - `opp_l5_pts_allowed` - Very recent trend
   - `pace_multiplier` - Scoring opportunity adjustment

3. **Market Features**
   - `line_cushion` - How far average beats the line
   - `line_cushion_pct` - Percentage cushion
   - `team_total_share` - Player's expected % of team scoring

### FEATURE COUNT: 47 Features
| Category | Count | Key Features |
|----------|-------|--------------|
| Opportunity | 7 | USG%, Minutes, FGA, FTr |
| Efficiency | 8 | eFG%, TS%, 3PT Rate |
| Matchup | 8 | Def Rating, Pace, Pts Allowed |
| Environment | 5 | Rest, B2B, Home/Away |
| Baseline | 9 | L3/L5/L10/Season Avg |
| Market | 6 | Line, Team Total, Cushion |

### STATSMODELS FINDINGS
**Significant Predictors (P < 0.05):**
1. `season_avg` (coef=0.508) - True talent
2. `touches_proxy` (coef=0.344) - Opportunity
3. `minutes_l5` (coef=0.208) - Playing time
4. `l3_avg` (coef=0.096) - Hot streak
5. `rest_days` (coef=-0.059) - Fatigue

**Not Significant:** Efficiency (eFG%, TS%), Matchup (pace, def rating)
- This tells us: Raw opportunity matters more than efficiency for predicting OUTPUT

### FILES ADDED/UPDATED
- `/app/backend/services/team_stats_service.py` (Team pace data)
- `/app/backend/services/vegas_killer_model.py` (Enhanced features)

---

## Previous: Vegas Killer Model - Process-Based Prediction

### THE PARADIGM SHIFT
**OLD WAY**: "Did he hit 8/10 times?" (Box Score thinking)
**VEGAS WAY**: "What conditions allow scoring?" (Process thinking)

### FEATURE CATEGORIES (38 Total Features)
| Category | Features | Purpose |
|----------|----------|---------|
| **Opportunity** | USG%, Minutes, FGA, FTr | Volume - how many shots? |
| **Efficiency** | eFG%, TS%, 3PT Rate | Quality - how well does he shoot? |
| **Matchup** | Opp DRtg, Pace, Pts Allowed | Friction - who is he playing? |
| **Environment** | Rest, Home/Away, B2B | Fatigue - is he tired? |
| **Baseline** | L3/L5/L10/Season Avg | Historical performance |
| **Market** | Line, Team Total | What Vegas thinks |

### MODEL PERFORMANCE (Ensemble: Ridge + GBM)
| Stat | MAE | R² | Features |
|------|-----|-----|----------|
| PTS | 4.77 | 0.52 | 36 |
| REB | 1.91 | 0.42 | 36 |
| AST | 1.40 | 0.49 | 36 |
| 3PM | 0.93 | 0.31 | 36 |
| PRA | 6.19 | 0.57 | 36 |

### API ENDPOINTS
| Endpoint | Purpose |
|----------|---------|
| `GET /api/v3/vegas-killer/status` | Model status |
| `POST /api/v3/vegas-killer/train` | Train models |
| `POST /api/v3/vegas-killer/predict` | Single prediction |
| `GET /api/v3/vegas-killer/predict-tier/{tier}` | Tier predictions |
| `GET /api/v3/vegas-killer/feature-breakdown` | Feature documentation |
| `GET /api/v3/vegas-killer/compare-all` | Full comparison |

### CURRENT RESULTS
- Safe Haven: 100% Strong Over (model confirms all picks)
- War Zone Trap: Miles Bridges PRA @ 24.5 → 32.2% P(Over) = STRONG UNDER

### FILES
- `/app/backend/services/vegas_killer_model.py` (38-feature model)
- `/app/backend/routes/vegas_killer.py` (API)
- `/app/backend/models/vegas_killer_*.pkl` (saved models)

---

## Previous: Vegas Pro Model - ML Regression Stack

### THE PRO STACK
| Tool | Purpose |
|------|---------|
| BallDontLie API | Data extraction (game logs, stats) |
| statsmodels | Feature significance analysis (P-values) |
| scikit-learn | Ridge regression model training |
| pandas + numpy | Data transformation |

### MODEL PERFORMANCE
| Stat | MAE | R² | Features |
|------|-----|-----|----------|
| PTS | 4.77 pts | 0.52 | 11 |
| REB | 1.92 | 0.42 | 8 |
| AST | 1.39 | 0.49 | 9 |
| 3PM | 0.94 | 0.31 | 7 |
| PRA | 6.18 | 0.57 | 14 |

### KEY FINDINGS FROM STATSMODELS
**Significant Features (P < 0.05):**
- `l3_avg` - Most recent form matters most
- `minutes_l5` - Playing time is crucial
- `rest_days` - B2B games hurt performance
- `cv_l10` - Volatility coefficient

**NOT Significant (drop these):**
- `is_home` - Home court doesn't predict scoring
- `mode`, `median` - Distribution shape less predictive than averages
- `opp_def_rank` - Needs better data quality

### API ENDPOINTS
| Endpoint | Purpose |
|----------|---------|
| `GET /api/v3/pro-model/status` | Check trained models |
| `POST /api/v3/pro-model/train` | Train/retrain models |
| `POST /api/v3/pro-model/predict` | Single prediction |
| `GET /api/v3/pro-model/predict-tier/{tier}` | Predict entire tier |
| `GET /api/v3/pro-model/compare-approaches` | Board Score vs ML |
| `POST /api/v3/pro-model/analyze-features/{stat}` | P-value analysis |

### FILES ADDED
- `/app/backend/services/vegas_pro_model.py` (ML pipeline)
- `/app/backend/routes/pro_model.py` (API endpoints)
- `/app/backend/models/` (saved model pickles)

---

## Previous Update (2026-04-05): Vegas Regression Model - Alternative Prediction System

### NEW: Multiple Linear Regression Model
Built a parallel prediction system to reverse-engineer Vegas line-setting methodology.

**Key Insight**: The existing Board Score approach is backward-looking ("how often did he hit this line?"). The Regression approach is forward-looking ("what will he score tonight?").

### Regression Model Formula
```
Predicted_Stat = (L5_Avg × 0.50) + (L10_Avg × 0.30) + (Season_Avg × 0.20)
               + Matchup_Adjustment
               + Minutes_Adjustment
               + Trend_Adjustment
               + Home/Away_Adjustment
               + Rest_Day_Adjustment
```

### Comparison Results (Current Board)
- Safe Haven: 100% agreement (Regression confirms all picks)
- Front Lines: 80% agreement
- War Zone: 33% agreement (expected - high risk plays)

### New API Endpoints
| Endpoint | Purpose |
|----------|---------|
| `POST /api/v3/regression/predict` | Single player prediction |
| `GET /api/v3/regression/compare/{tier}` | Compare Board Score vs Regression |
| `GET /api/v3/regression/compare/all` | All tiers comparison |
| `GET /api/v3/regression/flags` | Find potential traps (disagreements) |

### Files Added
- `/app/backend/services/vegas_regression_model.py` (Core regression logic)
- `/app/backend/routes/regression.py` (API endpoints)

---

## Previous Update (2026-04-05): PropVision v7.2 - Mode Edge Board Score Formula

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

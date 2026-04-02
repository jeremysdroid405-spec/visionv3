# Ferrari+ 5-Phase Pick Selection Pipeline
## Complete Technical Specification & Build Guide

---

## Table of Contents
1. [System Overview](#system-overview)
2. [Data Architecture](#data-architecture)
3. [Phase 1: Data Sourcing](#phase-1-data-sourcing)
4. [Phase 2: Global Kill Switch](#phase-2-global-kill-switch)
5. [Phase 3: Tier Classification](#phase-3-tier-classification)
6. [Phase 4: Sorting Algorithm](#phase-4-sorting-algorithm)
7. [Phase 5: Deduplication](#phase-5-deduplication)
8. [Mathematical Formulas](#mathematical-formulas)
9. [Why This Approach](#why-this-approach)

---

## System Overview

The Ferrari+ Pipeline is a **multi-stage filtering and ranking system** designed to identify high-value NBA player props by combining:

- **Sharp Market Analysis** (Bovada, DraftKings, FanDuel pricing)
- **Statistical Consistency** (Hit rates, mode, median, mean)
- **Contextual Intelligence** (DVP matchups, AI narrative scoring)
- **Line Value Detection** (Delta between PrizePicks and standard lines)

### Core Philosophy
> "If it's in the app, it must have proven Bovada separation."

The system progressively filters ~2,600 raw props down to exactly **30 elite plays** (10 per tier) by applying increasingly strict quality gates.

```
RAW PROPS (2,600+)
    ↓ Phase 2: Kill Switch (-60%)
FILTERED (1,000+)
    ↓ Phase 3: Tier Gates (-80%)
QUALIFIED (200)
    ↓ Phase 4: Sorting
RANKED (200)
    ↓ Phase 5: Deduplication
OUTPUT (30)
```

---

## Data Architecture

### Collections Used

| Collection | Purpose | Source |
|------------|---------|--------|
| `dg_cached_board` | Enriched player props with sharp market data | Sync Pipeline |
| `dvp_rankings` | Defense vs Position rankings by team | BallDontLie |
| `nba_master_hub_2026` | AI context scores per player | Gemini Analysis |
| `dg_player_stats` | Game-by-game logs (last 68 games) | BallDontLie |
| `ferrari_safe_haven` | Output: Safe Haven picks | Pipeline |
| `ferrari_front_lines` | Output: Front Lines picks | Pipeline |
| `ferrari_war_zone` | Output: War Zone picks | Pipeline |
| `ferrari_discarded` | Debug: Props killed by Phase 2 | Pipeline |

### Input Prop Structure
```json
{
  "player_name": "Nikola Jokic",
  "team": "DEN",
  "opponent": "LAL",
  "props": [
    {
      "stat_type": "PRA",
      "line": 39.5,
      "anchor_line": 49.5,
      "price": -137,
      "is_demon": false,
      "is_goblin": true,
      "sharp_market": {
        "sharp_price": -640,
        "sharp_source": "bovada",
        "bovada_price": -640,
        "draftkings_price": -550,
        "fanduel_price": -520,
        "dk_fd_average": -535,
        "is_alternate": true
      },
      "hit_rates": {
        "l10_rate": 80,
        "l5_rate": 100,
        "l10_hit_count": 8,
        "l5_avg": 52.4,
        "l10_avg": 51.9,
        "season_avg": 51.5
      }
    }
  ]
}
```

---

## Phase 1: Data Sourcing

### 1.1 Sharp Market Data (The Odds API)

**Purpose**: Establish the "true" market price for each prop by comparing PrizePicks to sharp bookmakers.

**Sources**:
- **Bovada**: Primary reference for alternate lines (better alt coverage)
- **DraftKings + FanDuel**: Primary reference for standard lines (averaged)

**API Parameters**:
```
regions=us,eu
bookmakers=prizepicks,bovada,draftkings,fanduel
markets=player_points,player_assists,player_rebounds,
        player_points_alternate,player_assists_alternate,
        player_rebounds_alternate
includeMultipliers=true
```

**Why Bovada for Alternates?**
Bovada offers deeper alternate line coverage than other US books. Their prices on alternate lines reflect sharp market sentiment more accurately.

**Why DK/FD Average for Standard?**
DraftKings and FanDuel are high-volume books with tight spreads on standard lines. Averaging them reduces single-book variance.

### 1.2 DVP Rankings

**Purpose**: Identify weak defenses to target with player props.

**Structure**:
```json
{
  "type": "dvp_rankings",
  "rankings": {
    "PTS": {"BOS": 1, "ATL": 20, "WAS": 30},
    "AST": {"BOS": 3, "ATL": 15, "WAS": 28},
    "REB": {"BOS": 5, "ATL": 10, "WAS": 25}
  }
}
```

**Interpretation**:
- Rank 1 = **Weakest** defense (allows most of that stat)
- Rank 30 = **Strongest** defense

**Why DVP Matters**:
A player facing a Rank 1 defense in their stat type has a structural advantage. The defense historically allows high production at that position.

### 1.3 AI Context Scores

**Purpose**: Capture narrative factors that affect player performance.

**Score Range**: 0-100

**Factors Considered** (via Gemini analysis):
- Revenge games
- Rest days
- Injury recovery arcs
- Usage rate changes
- Team context (trades, lineup changes)

**Why AI Context**:
Statistical models miss narrative edges. A player returning from injury might be usage-capped even if healthy. AI context captures these soft factors.

### 1.4 Game Logs (Mode/Median/Mean)

**Purpose**: Understand player consistency beyond simple averages.

**Data Extracted**:
- Last 10 games per player
- Stats: PTS, AST, REB, 3PM, BLK, STL, PRA

**Calculations**:
```python
# Mode: Most frequent value (rounded to 0.5)
# Only returned if a value appears 2+ times
l10_mode = 25.0  # Player hit exactly 25 multiple times

# Median: Middle value when sorted
l10_median = 24.5  # Half games above, half below

# Mean: Simple average
l10_mean = 24.8  # Sum / Count
```

**Why All Three?**
- **Mode** reveals the player's "typical" game
- **Median** is robust to outliers (one 50-point game doesn't skew it)
- **Mean** is useful but can be inflated by outliers

---

## Phase 2: Global Kill Switch

### Purpose
Eliminate props with insufficient market separation. If the sharp books and PrizePicks agree on probability, there's no edge.

### Kill Conditions

#### 2.1 Dead Zone
```
IF sharp_price BETWEEN -148 AND -137:
    DISCARD (no real separation)
```

**Why -148 to -137?**
This range represents near-identical implied probabilities:
- -148 = 59.7% implied
- -137 = 57.8% implied
- Difference = ~2% (not actionable)

#### 2.2 Separation Threshold

**Formula**:
```
Separation = |Implied_Sharp - Implied_PP| / Implied_PP × 100

IF Separation < 15%:
    DISCARD
```

**Implied Probability Conversion**:
```
IF odds < 0:
    implied = |odds| / (|odds| + 100)
ELSE:
    implied = 100 / (odds + 100)

Examples:
  -137 → 137 / 237 = 0.578 (57.8%)
  -250 → 250 / 350 = 0.714 (71.4%)
  +200 → 100 / 300 = 0.333 (33.3%)
```

**Example Calculation**:
```
PP: -137 (57.8%)
Sharp: -250 (71.4%)

Separation = |0.714 - 0.578| / 0.578 × 100
           = 0.136 / 0.578 × 100
           = 23.5%

23.5% > 15% → PASSES KILL SWITCH
```

**Why 15%?**
Below 15% separation, the edge is too small to overcome:
- Vig/juice
- Variance
- Execution risk

15% provides meaningful edge while not being so strict that no props qualify.

---

## Phase 3: Tier Classification

### Overview
Props that survive Phase 2 are classified into one of three tiers based on their characteristics.

### 3.1 Safe Haven (Elite Goblins)

**Target**: Ultra-high probability locks with massive market separation.

**Gates**:
```
sharp_price <= -250
AND
l10_hit_rate >= 80%
```

**Why These Thresholds?**
- **Sharp <= -250**: The sharp book implies 71%+ probability. This is a "heavy favorite" in betting terms.
- **L10 >= 80%**: The player has hit this line 8+ times in last 10 games. Consistency is proven.

**What This Captures**:
Alternate lines where PrizePicks is offering a much easier line than standard, AND the player consistently produces above that line.

### 3.2 Front Lines (Battleground)

**Target**: High-value standard plays against weak defenses.

**Gates**:
```
sharp_price BETWEEN -245 AND -149
AND
dvp_rank <= 10
AND
l10_hit_rate >= 70%
```

**Why These Thresholds?**
- **Sharp -245 to -149**: Moderate favorites. Not ultra-safe, but real edge exists.
- **DVP <= 10**: Only target the bottom third of defenses. Structural advantage.
- **L10 >= 70%**: Player is hitting 7/10 games. Reliable but not as locked as Safe Haven.

**What This Captures**:
Players with good consistency facing weak defenses. The DVP filter ensures we're not betting on players facing elite defenses.

### 3.3 War Zone (Elite Demons)

**Target**: High-payout longshots where the real odds are better than PrizePicks implies.

**Gates**:
```
sharp_price >= +500
AND
ai_context_score > 40
AND
l10_hits >= 2
```

**Why These Thresholds?**
- **Sharp >= +500**: The sharp book says this is a 5:1 or better proposition. PrizePicks at +100 (1:1) means huge value if it hits.
- **AI Context > 40**: The narrative context is neutral-to-positive. We're not betting on injured or benched players.
- **L10 Hits >= 2**: Safety net. The player HAS hit this line recently, even if infrequently.

**What This Captures**:
Demon lines (PrizePicks even odds) where the sharp market suggests the line is achievable 15-20% of the time, not the 50% PrizePicks implies.

---

## Phase 4: Sorting Algorithm

### Primary Sort: Line Delta

**Formula**:
```
Line Delta = PP Line - Anchor Line (standard line)
```

**Interpretation**:
- **Negative Delta** = PP line is BELOW standard (easier prop)
- **Positive Delta** = PP line is ABOVE standard (harder prop)

**Example**:
```
Nikola Jokic PRA
  PP Line: 39.5
  Anchor (Standard): 49.5
  Delta: 39.5 - 49.5 = -10.0

Interpretation: The line is 10 points BELOW his standard line.
               This is a significant edge.
```

**Why Sort by |Delta|?**
Bigger delta = bigger edge vs the standard market. A -10 delta means you're getting a 10-point head start.

### Secondary Sorts (Tiebreakers)

| Tier | Sort 1 | Sort 2 | Sort 3 |
|------|--------|--------|--------|
| Safe Haven | \|Line Delta\| ↓ | L10 Rate ↓ | - |
| Front Lines | \|Line Delta\| ↓ | DVP Rank ↑ | L10 Rate ↓ |
| War Zone | \|Line Delta\| ↓ | AI Context ↓ | L10 Rate ↓ |

**Implementation**:
```python
# Safe Haven
candidates.sort(key=lambda x: (
    -abs(x["line_delta"]),  # Biggest delta first
    -x["l10_rate"]          # Then highest hit rate
))

# Front Lines
candidates.sort(key=lambda x: (
    -abs(x["line_delta"]),  # Biggest delta first
    x["dvp_rank"],          # Then weakest defense (lowest rank)
    -x["l10_rate"]          # Then highest hit rate
))

# War Zone
candidates.sort(key=lambda x: (
    -abs(x["line_delta"]),  # Biggest delta first
    -x["ai_context_score"], # Then best narrative
    -x["l10_rate"]          # Then highest hit rate
))
```

---

## Phase 5: Deduplication

### Rule: One Player Per Tier

**Why?**
- Diversification: Don't overexpose to a single player's variance
- Correlation: Multiple props on the same player are correlated
- User Experience: More variety in the picks shown

### Implementation

**Priority Order**:
1. Safe Haven (highest priority)
2. Front Lines
3. War Zone (lowest priority)

**Logic**:
```python
used_players = set()

# Process Safe Haven first
for pick in safe_haven_sorted:
    if pick.player_name not in used_players:
        used_players.add(pick.player_name)
        safe_haven_output.append(pick)
        if len(safe_haven_output) >= 10:
            break

# Process Front Lines (excluding Safe Haven players)
for pick in front_lines_sorted:
    if pick.player_name not in used_players:
        used_players.add(pick.player_name)
        front_lines_output.append(pick)
        if len(front_lines_output) >= 10:
            break

# Process War Zone (excluding Safe Haven + Front Lines players)
for pick in war_zone_sorted:
    if pick.player_name not in used_players:
        used_players.add(pick.player_name)
        war_zone_output.append(pick)
        if len(war_zone_output) >= 10:
            break
```

**Example**:
```
LeBron James qualifies for both Safe Haven and War Zone.
→ Assigned to Safe Haven (higher priority)
→ War Zone slot goes to next qualifying player
```

---

## Mathematical Formulas

### Implied Probability
```
American Odds → Implied Probability

Negative odds (favorite):
  P = |odds| / (|odds| + 100)
  
Positive odds (underdog):
  P = 100 / (odds + 100)

Examples:
  -200 → 200/300 = 66.7%
  -137 → 137/237 = 57.8%
  +150 → 100/250 = 40.0%
  +500 → 100/600 = 16.7%
```

### Separation Percentage
```
Separation = |P_sharp - P_pp| / P_pp × 100

Where:
  P_sharp = Implied probability from sharp book
  P_pp = Implied probability from PrizePicks

Example:
  PP: -137 → 57.8%
  Sharp: -250 → 71.4%
  
  Separation = |0.714 - 0.578| / 0.578 × 100
             = 23.5%
```

### Line Delta
```
Delta = Line_PP - Line_Standard

Example:
  PP Line: 19.5
  Standard Line: 27.5
  
  Delta = 19.5 - 27.5 = -8.0
  
Interpretation: 8 points BELOW standard = significant edge
```

### Hit Rate
```
L10 Rate = Games_Over / 10 × 100
L5 Rate = Games_Over / 5 × 100

Example:
  Last 10 games: 25, 28, 22, 30, 19, 24, 26, 31, 27, 23
  Line: 24.5
  
  Games Over: 7 (28, 30, 26, 31, 27, 25)
  L10 Rate = 7/10 × 100 = 70%
```

### Mode Calculation
```python
def calculate_mode(values):
    # Round to nearest 0.5 for grouping
    rounded = [round(v * 2) / 2 for v in values]
    
    # Count occurrences
    counts = Counter(rounded)
    
    # Get most common
    mode_value, mode_count = counts.most_common(1)[0]
    
    # Only return if appears 2+ times
    return mode_value if mode_count >= 2 else None
```

### Median Calculation
```python
def calculate_median(values):
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    
    if n % 2 == 0:
        # Even: average of two middle values
        return (sorted_vals[n//2 - 1] + sorted_vals[n//2]) / 2
    else:
        # Odd: middle value
        return sorted_vals[n//2]
```

---

## Why This Approach

### Why Sharp Market Comparison?
Sharp books (Bovada, DraftKings, FanDuel) have:
- Higher limits attracting professional bettors
- Tighter spreads due to competition
- More accurate probability estimates

If a sharp book prices something as -250 (71%) and PrizePicks prices it at -137 (58%), there's a **13%+ probability gap**. That's exploitable value.

### Why Tiered Classification?
Different prop types require different strategies:
- **Safe Haven**: High-probability grinds. Small edge, high hit rate.
- **Front Lines**: Value plays with context (DVP) validation.
- **War Zone**: Longshots where the payout exceeds the true odds.

A single filter can't capture all three edge types effectively.

### Why Line Delta as Primary Sort?
The delta represents **how much easier your line is** compared to standard. A -10 delta means:
- Standard line: 49.5
- Your line: 39.5
- You need 10 FEWER points/assists/rebounds to win

This is the purest measure of line value.

### Why 80/70/40 Thresholds?
- **80% (Safe Haven)**: Ensures ultra-consistency. 8/10 is a pattern, not luck.
- **70% (Front Lines)**: Still reliable, but allows more candidates for DVP filtering.
- **40 AI (War Zone)**: Low bar because AI scores are sparse. Only filters out negative narratives.

### Why One Player Per Tier?
Correlation risk. If you have 3 LeBron props and LeBron sits out, you lose all 3. Spreading across players provides:
- Natural hedging
- Better user experience
- Reduced variance

---

## File Reference

| File | Purpose |
|------|---------|
| `/app/backend/services/ferrari_tier_service.py` | Core 5-Phase Pipeline logic |
| `/app/backend/services/odds_api_service.py` | Sharp book data fetching |
| `/app/backend/services/odds_sync_service.py` | Props enrichment with sharp data |
| `/app/backend/routes/ferrari_tiers.py` | API endpoints |
| `/app/frontend/src/hooks/useLiveOdds.js` | Frontend data fetching |
| `/app/memory/FERRARI_METHODOLOGY.md` | This document |

---

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v3/ferrari/rebuild` | POST | Execute 5-Phase Pipeline |
| `/api/v3/ferrari/all` | GET | Get all 30 picks |
| `/api/v3/ferrari/safe-haven` | GET | Get Safe Haven (10 picks) |
| `/api/v3/ferrari/front-lines` | GET | Get Front Lines (10 picks) |
| `/api/v3/ferrari/war-zone` | GET | Get War Zone (10 picks) |
| `/api/v3/ferrari/discarded` | GET | Get Phase 2 kills (debug) |

---

*Document Version: 1.0*
*Last Updated: 2026-04-01*
*Pipeline: Ferrari+ 5-Phase*

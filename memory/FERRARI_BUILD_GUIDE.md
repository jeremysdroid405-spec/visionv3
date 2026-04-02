# Ferrari v4 Pipeline - FINAL ARCHITECTURAL LOCK
## Complete Technical Specification & Build Guide

---

## Version History
| Version | Date | Changes |
|---------|------|---------|
| v4 | 2026-04-02 | Final lock: 5% absolute edge, Median Anchor, New tier windows |
| v3 | 2026-04-01 | 5-Phase Pipeline with DVP/AI context |
| v2 | 2026-04-01 | Tier isolation, sharp market integration |
| v1 | 2026-03-31 | Initial implementation |

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

### Philosophy
> "Math so tight you can copy-paste with 100% confidence."

The Ferrari v4 Pipeline is the **final architectural lock** for pick selection. Every prop must pass two mathematical gates:

1. **5% Absolute Edge**: Sharp implied probability must differ from PrizePicks by at least 5 percentage points
2. **Median Anchor**: PrizePicks line must be at or below the player's season median

### Pipeline Flow
```
RAW PROPS (2,006)
    ↓ Kill Switch: Edge < 5% (-245)
    ↓ Kill Switch: Line > Median (-806)
    ↓ Kill Switch: No sharp data (-623)
FILTERED (332)
    ↓ Tier Classification
QUALIFIED (331)
    ↓ Sorting (Line Delta → L10 Rate)
RANKED (331)
    ↓ Deduplication (One per player)
OUTPUT (30)
```

---

## Data Architecture

### Collections

| Collection | Purpose |
|------------|---------|
| `dg_cached_board` | Enriched props with sharp market data |
| `dg_player_stats` | Game logs for median/mode/mean |
| `ferrari_safe_haven` | Output: Safe Haven (10 picks) |
| `ferrari_front_lines` | Output: Front Lines (10 picks) |
| `ferrari_war_zone` | Output: War Zone (10 picks) |
| `ferrari_discarded` | Debug: Killed props |

---

## Phase 1: Data Sourcing

### 1.1 Sharp Market Data
- **Source**: The Odds API
- **Books**: Bovada (alternates), DraftKings + FanDuel average (standard)
- **Markets**: player_points, player_assists, player_rebounds + alternates

### 1.2 Season Median (NEW in v4)
- **Source**: BallDontLie game logs (full season)
- **Calculation**: Median of all games played this season
- **Purpose**: Anchor for line validation

```python
# Example: Kawhi Leonard PRA
games = [35, 42, 28, 45, 38, 40, 33, ...]  # All season games
season_median = median(games)  # 37.0

# PP Line: 29.5
# 29.5 <= 37.0 ✓ PASSES MEDIAN ANCHOR
```

### 1.3 L10 Stats
- Mode, Median, Mean from last 10 games
- Used for additional context, not filtering

---

## Phase 2: Global Kill Switch

### 2.1 Absolute Edge (5% Minimum)

**Formula**:
```
Absolute Edge = |Sharp_Implied - PP_Implied|

Where:
  PP_Implied = 57.8% (standard -137 line)
  Sharp_Implied = american_to_implied(sharp_price)

IF Absolute Edge < 5%:
    KILL
```

**Example**:
```
Sharp: -250 → 71.4% implied
PP: -137 → 57.8% implied

Absolute Edge = |71.4% - 57.8%| = 13.6%
13.6% >= 5% ✓ PASSES
```

**Why 5% Absolute?**
- Relative percentages can be misleading
- 5% absolute represents real, actionable edge
- Accounts for juice/vig in pricing

### 2.2 Median Anchor

**Rule**:
```
IF PP_Line > Season_Median:
    KILL
```

**Example**:
```
Player: Kawhi Leonard PRA
Season Median: 37.0
PP Line: 29.5

29.5 <= 37.0 ✓ PASSES

# Counter-example:
PP Line: 39.5
39.5 > 37.0 ✗ KILLED
```

**Why Median Anchor?**
- Median is robust to outliers
- Ensures the line is at or below "typical" performance
- Prevents betting on inflated lines

---

## Phase 3: Tier Classification

### NEW v4 Windows

| Tier | Sharp Price Window | Description |
|------|-------------------|-------------|
| **Safe Haven** | ≤ -250 | Ultra-high probability locks |
| **Front Lines** | -245 to -115 | Strong favorites with edge |
| **War Zone** | -114 to +500 | Value plays and longshots |

### Classification Logic
```python
if sharp_price <= -250:
    → SAFE HAVEN
elif -245 <= sharp_price <= -115:
    → FRONT LINES
elif -114 <= sharp_price <= 500:
    → WAR ZONE
else:
    → UNCLASSIFIED (outside all windows)
```

### Implied Probability Reference
| Sharp Price | Implied | Tier |
|------------|---------|------|
| -500 | 83.3% | Safe Haven |
| -250 | 71.4% | Safe Haven |
| -245 | 71.0% | Front Lines |
| -150 | 60.0% | Front Lines |
| -115 | 53.5% | Front Lines |
| -114 | 53.3% | War Zone |
| +100 | 50.0% | War Zone |
| +300 | 25.0% | War Zone |
| +500 | 16.7% | War Zone |

---

## Phase 4: Sorting Algorithm

### Primary: Line Delta
```
Line Delta = PP_Line - Anchor_Line

Bigger |Delta| = More edge
```

### Secondary: L10 Hit Rate
```
Higher L10 Rate = More consistent
```

### Implementation
```python
candidates.sort(key=lambda x: (
    -abs(x["line_delta"]),  # Biggest delta first
    -x["l10_rate"]          # Then highest hit rate
))
```

### Example Sorting
```
Kawhi Leonard: Delta -8.0, L10 80%  → Rank 1
Luka Doncic: Delta -8.0, L10 70%    → Rank 2
Lamelo Ball: Delta -7.0, L10 90%    → Rank 3
```

---

## Phase 5: Deduplication

### Rule: One Player Per Tier

**Priority**: Safe Haven > Front Lines > War Zone

```python
used_players = set()

# Safe Haven first
for pick in safe_haven_sorted:
    if pick.player_name not in used_players:
        used_players.add(pick.player_name)
        output.append(pick)

# Front Lines (excluding Safe Haven players)
# War Zone (excluding Safe Haven + Front Lines players)
```

### Output Cap: 30 Total Plays
- 10 Safe Haven
- 10 Front Lines
- 10 War Zone

---

## Mathematical Formulas

### Implied Probability
```
Negative odds: P = |odds| / (|odds| + 100)
Positive odds: P = 100 / (odds + 100)

-137 → 137/237 = 57.8%
-250 → 250/350 = 71.4%
+200 → 100/300 = 33.3%
```

### Absolute Edge
```
Edge = |P_sharp - P_pp|
Edge = |P_sharp - 0.578|

Example: Sharp -250
Edge = |0.714 - 0.578| = 0.136 = 13.6%
```

### Line Delta
```
Delta = PP_Line - Anchor_Line

Example: PP 29.5, Anchor 37.5
Delta = 29.5 - 37.5 = -8.0
```

### Season Median
```python
def median(values):
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n % 2 == 0:
        return (sorted_vals[n//2 - 1] + sorted_vals[n//2]) / 2
    return sorted_vals[n//2]
```

---

## Why This Approach

### Why 5% Absolute Edge?
- **Clarity**: Easy to understand (5 percentage points)
- **Actionable**: Represents real value after juice
- **Consistent**: Same threshold across all odds ranges

### Why Median Anchor?
- **Robustness**: Median ignores outlier games
- **Consistency**: Represents "typical" performance
- **Safety**: Only bet on lines below typical output

### Why New Tier Windows?
- **Safe Haven (≤ -250)**: 71%+ implied = high confidence
- **Front Lines (-245 to -115)**: 53-71% = solid favorites
- **War Zone (-114 to +500)**: Below 53% = value hunting

### Why Line Delta Sorting?
- **Pure Value**: Bigger delta = bigger edge vs standard
- **Mathematical**: Objective measure of line value
- **Predictive**: Easier lines = higher hit probability

---

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v3/ferrari/rebuild` | POST | Execute v4 Pipeline |
| `/api/v3/ferrari/all` | GET | All 30 picks |
| `/api/v3/ferrari/safe-haven` | GET | 10 Safe Haven picks |
| `/api/v3/ferrari/front-lines` | GET | 10 Front Lines picks |
| `/api/v3/ferrari/war-zone` | GET | 10 War Zone picks |
| `/api/v3/ferrari/discarded` | GET | Killed props (debug) |

---

## File Reference

| File | Purpose |
|------|---------|
| `/app/backend/services/ferrari_tier_service.py` | v4 Pipeline logic |
| `/app/backend/routes/ferrari_tiers.py` | API endpoints |
| `/app/memory/FERRARI_BUILD_GUIDE.md` | This document |

---

*Pipeline: Ferrari v4 - FINAL ARCHITECTURAL LOCK*
*Last Updated: 2026-04-02*

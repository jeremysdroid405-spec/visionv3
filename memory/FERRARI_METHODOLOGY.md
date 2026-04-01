# Ferrari+ Hybrid Model - Pick Selection Methodology

## Overview
The Ferrari+ Hybrid Model is a multi-phase filtering and ranking system that identifies the "Best of the Best" player props by combining sharp market pricing, statistical analysis, and contextual intelligence.

---

## Phase 1: Data Sourcing

### Primary Odds Source: The Odds API
- **PrizePicks props**: Standard + alternate lines
- **Sharp Book odds**: Bovada, DraftKings, FanDuel
- **Markets**: `player_points`, `player_assists`, `player_rebounds`, `player_points_alternate`, `player_assists_alternate`, `player_rebounds_alternate`
- **Regions**: `us, eu`
- **Parameters**: `includeMultipliers=true`

### Stats Source: BallDontLie API
- Last 10 games per player (game logs)
- Season averages
- Used for hit rate calculations and mode/median/mean

### DVP Rankings: Internal Database
- Defense vs Position rankings by team
- Rank 1-30 per stat type (1 = weakest defense)

### AI Context: Gemini-Powered Analysis
- Player narrative context scores (0-100)
- Captures momentum, matchup history, injury context

---

## Phase 2: Global Filters (Kill Switches)

### Filter 1: Dead Zone
| Condition | Action |
|-----------|--------|
| Sharp price between -148 and -137 | **DISCARDED** |

**Reason**: No real market separation - these are "mid" plays.

### Filter 2: 15% Separation Kill-Switch
| Formula | Threshold |
|---------|-----------|
| `\|PP_implied - Sharp_implied\| / Sharp_implied × 100` | < 15% = **DISCARDED** |

**Example**: PP -137 vs Sharp -150 = 8.7% separation → **KILL**

---

## Phase 3: Tier Classification (Elite Filters)

### 🛡️ SAFE HAVEN (Elite Goblins)

| Gate | Requirement |
|------|-------------|
| **Sharp Price** | ≤ -250 |
| **L10 Hit Rate** | ≥ 80% |

**Example**: Nikola Jokic PRA @ 39.5
- Sharp: -640 ✓
- L10 Rate: 80% ✓
- Mode: 54.0 | Median: 53.5

---

### ⚔️ FRONT LINES (Battleground)

| Gate | Requirement |
|------|-------------|
| **Sharp Price** | Between -245 and -149 (exclusive window) |
| **DVP Rank** | ≤ 10 (weak defenses only) |

**Example**: Tyler Herro PRA @ 28.5 vs BOS
- Sharp: -215 ✓
- DVP Rank: 1 ✓ (Boston weak vs PRA)

---

### 🔥 WAR ZONE (Elite Demons)

| Gate | Requirement |
|------|-------------|
| **Sharp Price** | ≥ +500 |
| **AI Context Score** | > 40 |
| **L10 Hits** | ≥ 2 (safety net) |

**Example**: DeMar DeRozan AST @ 7.5
- Sharp: +850 ✓
- AI Context: 50 ✓
- L10 Hits: 3 ✓

---

## Phase 4: Sorting Methodology

### Primary Sort: Line Delta
```
Line Delta = PP Line - Anchor Line (standard line)
```
- **Bigger |Delta|** = More edge vs standard market
- **Example**: Line 19.5, Anchor 27.5 → Delta = -8.0 (8 points below standard!)

### Tiebreakers by Tier

| Tier | Sort 1 | Sort 2 | Sort 3 |
|------|--------|--------|--------|
| **Safe Haven** | \|Line Delta\| ↓ | L10 Hit Rate ↓ | - |
| **Front Lines** | \|Line Delta\| ↓ | DVP Rank ↑ | L10 Hit Rate ↓ |
| **War Zone** | \|Line Delta\| ↓ | AI Context ↓ | L10 Hit Rate ↓ |

---

## Phase 5: Cross-Tier Deduplication

**Rule**: One pick per player across ALL tiers

**Priority Order**:
1. Safe Haven (highest)
2. Front Lines
3. War Zone (lowest)

**Example**: If LeBron qualifies for Safe Haven and War Zone:
- He appears **ONLY** in Safe Haven
- War Zone slot goes to next qualified player

---

## Phase 6: Final Output (Top 10 Per Tier)

### Data Fields Returned

#### Player Info
- `player_name`, `team`, `opponent`, `position`, `game_time`

#### Prop Details
- `stat_type`, `line`, `anchor_line`, `price`, `direction`
- `is_demon`, `is_goblin`, `is_alternate`

#### Sharp Market
- `sharp_price`, `sharp_source` (bovada / dk_fd_avg)
- `bovada_price`, `draftkings_price`, `fanduel_price`

#### Ferrari+ Metrics
- `line_delta` - PP line vs standard
- `separation_pct` - Implied probability gap
- `dvp_rank` - Opponent defense weakness
- `ai_context_score` - Narrative context

#### Statistical Analysis
- `l10_rate`, `l5_rate` - Hit percentages
- `l10_hits` - Games over line
- `l5_avg`, `l10_avg`, `season_avg` - Averages
- `l10_mode` - Most frequent value (L10)
- `l10_median` - Middle value (L10)
- `l10_mean` - Average (L10)

---

## Example Pick Breakdown

### SAFE HAVEN PICK: Nikola Jokic PRA @ 39.5

**Why Qualified:**
- Sharp Price: -640 (≤ -250 ✓)
- L10 Hit Rate: 80% (≥ 80% ✓)

**Why Ranked #1:**
- Line Delta: -10.0 (39.5 - 49.5 = biggest edge)
- Consistency: Mode 54, Median 53.5, Mean 51.9 (all above line)

**Verdict**: Line 39.5 is 10+ points BELOW his typical output. **Strong lock.**

---

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/v3/ferrari/all` | All tiers in one response |
| `GET /api/v3/ferrari/safe-haven` | Elite goblins (Top 10) |
| `GET /api/v3/ferrari/front-lines` | Battleground picks (Top 10) |
| `GET /api/v3/ferrari/war-zone` | Elite demons (Top 10) |
| `GET /api/v3/ferrari/discarded` | Props killed by filters |
| `POST /api/v3/ferrari/rebuild` | Manual tier rebuild |

---

## Files Reference

| File | Purpose |
|------|---------|
| `/app/backend/services/ferrari_tier_service.py` | Core Ferrari+ logic |
| `/app/backend/routes/ferrari_tiers.py` | API endpoints |
| `/app/frontend/src/hooks/useLiveOdds.js` | Frontend data fetching |

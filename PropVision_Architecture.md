# PropVision Architecture Documentation

> **Version:** 2026 Season  
> **Last Updated:** April 2026  
> **System:** PropVision AI - Sports Betting Intelligence Platform

---

## Table of Contents

1. [The Assembly Line Flow](#1-the-assembly-line-flow)
2. [The Oracle Gate Logic](#2-the-oracle-gate-logic)
3. [The JIT Injury Beneficiary Tracker](#3-the-jit-injury-beneficiary-tracker)
4. [The Vision Intel AI](#4-the-vision-intel-ai)

---

## 1. The Assembly Line Flow

The PropVision data pipeline operates as a **6-step assembly line** that transforms raw market data into actionable betting intelligence.

### Pipeline Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        PROPVISION ASSEMBLY LINE                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  STEP 1          STEP 2           STEP 3          STEP 4                    │
│  ┌──────┐       ┌──────────┐     ┌──────────┐    ┌──────────┐               │
│  │BOARD │ ───▶  │VK EDGE   │ ──▶ │ORACLE    │ ──▶│VISION    │               │
│  │SCRAPE│       │CALCULATE │     │APEX GATE │    │INTEL AI  │               │
│  └──────┘       └──────────┘     └──────────┘    └──────────┘               │
│     │               │                 │               │                      │
│     ▼               ▼                 ▼               ▼                      │
│  PrizePicks     Raw Cushion      3-Gate Filter   Gemini 3.1              │
│  The Odds API   Edge Math        Tier Assignment LLM Analysis               │
│  BallDontLie    Matchup Mod      Safe/Front/War  Human Rationale            │
│                                                                              │
│  STEP 5              STEP 6                                                  │
│  ┌──────────┐       ┌──────────────┐                                        │
│  │TIER      │ ───▶  │FRONTEND      │                                        │
│  │STORAGE   │       │DELIVERY      │                                        │
│  └──────────┘       └──────────────┘                                        │
│     │                    │                                                   │
│     ▼                    ▼                                                   │
│  MongoDB             React Dashboard                                         │
│  Collections         Real-time UI                                           │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Step-by-Step Breakdown

#### Step 1: Board Scrape & Data Ingestion

**Sources:**
- **The Odds API**: DraftKings lines, Pinnacle sharp lines, cross-market odds
- **BallDontLie API**: Player game logs, season stats, team data
- **PrizePicks Board**: Current prop offerings, goblin/demon classifications

**Output Collections:**
```
NBA: dg_cached_board, nba_master_hub_2026
MLB: mlb_cached_board, mlb_master_hub_2026
```

**Key Fields Captured:**
- `player_name`, `team`, `opponent`
- `stat_type`, `line`, `dk_odds`
- `is_goblin`, `is_demon`, `is_lineup_confirmed`
- `game_logs[]` (L5, L10, L20 performance)

---

#### Step 2: VK Edge Calculation

The **Vegas Killer (VK) Model** calculates the mathematical edge for each prop.

**Formula:**
```
VK_Edge = VK_Predicted_Value - Prop_Line
Adjusted_VK = Raw_VK × Matchup_Modifier × Tempo_Modifier
```

**Matchup Modifier** (from `mlb_matchup_math.py`):
- Favorable matchup (Easy): 1.05 - 1.15
- Neutral matchup: 1.0
- Tough matchup (Brutal): 0.85 - 0.95

**Tempo Modifier** (Plate Appearance Volume):
- **Hitters**: Based on batting order, home/away status, team OBP rank
- **Pitchers**: Based on P/PA efficiency, bullpen rest days

**Outputs:**
- `vk_predicted`: Model's projected stat value
- `vk_edge`: Raw cushion above/below line
- `vk_prob_over`: Probability of clearing the line
- `cv`: Coefficient of Variation (consistency metric)

---

#### Step 3: Oracle Apex Gating

All props pass through the **3-Gate Oracle Apex System** for tier assignment.

```
        ┌─────────────────────────────────────────────┐
        │              ORACLE APEX FILTER              │
        ├─────────────────────────────────────────────┤
        │                                              │
        │   INPUT: All enriched props (~5000+)         │
        │                    │                         │
        │                    ▼                         │
        │          ┌─────────────────┐                │
        │          │   PRE-FILTER    │                │
        │          │ • DK Odds Range │                │
        │          │ • Prop Type     │                │
        │          │ • Lineup Status │                │
        │          └────────┬────────┘                │
        │                   │                         │
        │          ┌────────▼────────┐                │
        │          │    GATE 1       │                │
        │          │   Hit Rate %    │                │
        │          │  (L20 Sample)   │                │
        │          └────────┬────────┘                │
        │                   │                         │
        │          ┌────────▼────────┐                │
        │          │    GATE 2       │                │
        │          │   CV / Variance │                │
        │          │  (Consistency)  │                │
        │          └────────┬────────┘                │
        │                   │                         │
        │          ┌────────▼────────┐                │
        │          │    GATE 3       │                │
        │          │   Edge + TP     │                │
        │          │ (Math Threshold)│                │
        │          └────────┬────────┘                │
        │                   │                         │
        │                   ▼                         │
        │   OUTPUT: Qualified picks (~10-30 per tier) │
        │                                              │
        └─────────────────────────────────────────────┘
```

See [Section 2](#2-the-oracle-gate-logic) for complete threshold dictionaries.

---

#### Step 4: Vision Intel AI Summary

Qualified picks are enriched with **Gemini 3.1 Pro** AI analysis.

**Process:**
1. Batch props by tier (Safe Haven, Front Lines, War Zone)
2. Send structured data to Gemini with sport-specific system prompt
3. Receive JSON array with intel scores, verdicts, and rationales
4. Cache results in MongoDB to avoid redundant API calls

**JIT Diff Check:**
- Before Gemini call, system checks if cached intel exists
- Only NEW props (delta) are sent to Gemini
- Reduces token usage by 80-90%

---

#### Step 5: Tier Storage

Qualified picks are written to sport-specific MongoDB collections:

```javascript
// NBA Collections
ferrari_safe_haven     // Strictest tier - bank picks
ferrari_front_lines    // Moderate tier - value plays  
ferrari_war_zone       // Riskiest tier - ceiling demons

// MLB Collections
mlb_safe_haven         // Strictest tier
mlb_front_lines        // Moderate tier
mlb_war_zone           // Riskiest tier
```

**Atomic Upsert Pattern:**
```python
await collection.update_one(
    {"player_name": pick["player_name"], "stat_type": pick["stat_type"]},
    {"$set": pick},
    upsert=True
)
```

---

#### Step 6: Frontend Delivery

**API Endpoints:**
```
GET /api/v3/ferrari/safe-haven?sport=nba|mlb
GET /api/v3/ferrari/front-lines?sport=nba|mlb
GET /api/v3/ferrari/war-zone?sport=nba|mlb
```

**Response Payload:**
```json
{
  "picks": [
    {
      "player_name": "Tyrese Maxey",
      "stat_type": "PTS",
      "line": 24.5,
      "vk_predicted": 28.3,
      "vk_edge": 3.8,
      "h20_rate": 90,
      "cv": 0.18,
      "intel_score": 9,
      "vision_intel": "Maxey cooking at home with 90% L10...",
      "tier": "safe_haven"
    }
  ],
  "count": 10,
  "synced_at": "2026-04-13T00:00:00Z"
}
```

---

## 2. The Oracle Gate Logic

The Oracle Apex system uses a **3-Gate qualification filter** with sport-specific and stat-specific thresholds.

### Gate Overview

| Gate | Name | What It Measures | Pass Criteria |
|------|------|------------------|---------------|
| 1 | Hit Rate | Historical line-clearing % | >= X/20 games |
| 2 | CV | Coefficient of Variation | <= Max CV |
| 3 | Edge + TP | Mathematical advantage | >= Min Edge AND >= Min TP% |

---

### NBA Threshold Dictionaries

#### Safe Haven (Strictest - Bank Plays)

**Pre-Filter:** DK Odds <= -240, GOBLIN only, Lineup confirmed

| Stat | Max CV | Min Hit Rate | Min Edge | Min TP |
|------|--------|--------------|----------|--------|
| PTS  | 0.22   | 18/20 (90%)  | +2.0     | 75%    |
| REB  | 0.35   | 16/20 (80%)* | +1.5     | 75%    |
| AST  | 0.35   | 15/20 (75%)  | +2.0     | 75%    |
| PRA  | 0.20   | 18/20 (90%)  | +2.0     | 75%    |

*REB Buffer Rule: 14/20 OK if L20 Mean >= Line + 2.5

#### Front Lines (Moderate - Value Plays)

**Pre-Filter:** DK Odds between -145 and -239, No DEMON, Lineup confirmed

| Stat | Max CV | Min Hit Rate | Min Edge | Min TP |
|------|--------|--------------|----------|--------|
| PTS  | 0.28   | 14/20 (70%)  | +1.5     | 55%    |
| REB  | 0.40   | 12/20 (60%)* | +1.5     | 55%    |
| AST  | 0.40   | 12/20 (60%)  | +1.5     | 55%    |
| PRA  | 0.25   | 14/20 (70%)  | +1.5     | 55%    |

*REB Buffer Rule: 10/20 OK if L5 Mean >= Line + 1.5

#### War Zone (Riskiest - Ceiling Demons)

**Pre-Filter:** DK Odds >= +140 OR is_demon, Lineup confirmed

| Stat | Max CV | Min Hit Rate | Min Edge | Min TP |
|------|--------|--------------|----------|--------|
| PTS  | 0.85   | 7/20 (35%)   | N/A*     | 40%    |
| REB  | 1.00   | 7/20 (35%)   | N/A*     | 40%    |
| AST  | 1.00   | 7/20 (35%)   | N/A*     | 40%    |
| PRA  | 0.75   | 7/20 (35%)   | N/A*     | 40%    |

*War Zone has no edge requirement - we want ceiling plays

**Special Rules:**
- **Volatility Fast-Track:** CV > 1.0 auto-passes Gate 2 (boom/bust welcome)
- **Demon Override:** `is_demon=True` bypasses DK odds floor

---

### MLB Threshold Dictionaries

#### Safe Haven (Strictest)

**Pre-Filter:** DK Odds <= -240, GOBLIN only, Lineup confirmed, Pinnacle TP >= 70%

| Stat | Max CV | Min L20 Hits | Min Edge | Min TP |
|------|--------|--------------|----------|--------|
| HITS | 0.60   | 16/20 (80%)  | +0.30    | 70%    |
| TB   | 0.75   | 15/20 (75%)  | +0.45    | 70%    |
| K    | 0.45   | 15/20 (75%)  | +1.00    | 75%    |
| OUTS | 0.30   | 17/20 (85%)  | +1.50    | 80%    |
| HRR  | 0.55   | 16/20 (80%)  | +0.45    | 70%    |
| HR   | 1.20   | 10/20 (50%)  | +0.15    | 55%    |

#### Front Lines (Moderate)

**Pre-Filter:** DK Odds between -145 and -239, No DEMON, Pinnacle TP >= 58%

| Stat | Max CV | Min L20 Hits | Min Edge |
|------|--------|--------------|----------|
| HITS | 0.85   | 13/20 (65%)  | +0.20    |
| TB   | 0.95   | 12/20 (60%)  | +0.30    |
| K    | 0.60   | 13/20 (65%)  | +0.80    |
| OUTS | 0.50   | 14/20 (70%)  | +1.00    |
| HRR  | 0.75   | 13/20 (65%)  | +0.30    |
| HR   | 1.40   | 7/20 (35%)   | +0.10    |

**Recency Override:** If L20 fails but L10 >= 80% (8/10), force PASS Gate 1

#### War Zone (Riskiest)

**Pre-Filter:** DK Odds >= +150 OR is_demon, Pinnacle TP < 45% OK

| Stat | Max CV | Min L20 Hits | Min Edge |
|------|--------|--------------|----------|
| HITS | 1.10   | 8/20 (40%)   | +0.40    |
| TB   | 1.25   | 7/20 (35%)   | +0.60    |
| K    | 0.85   | 10/20 (50%)  | +1.50    |
| OUTS | 0.70   | 12/20 (60%)  | +2.00    |
| HRR  | 1.00   | 9/20 (45%)   | +0.60    |
| HR   | 1.80   | 4/20 (20%)   | +0.20    |

**Special Rules:**
- **L15 Ceiling Check:** Must have cleared line 2x in L15 (demonstrated spikes)
- **Volatility Fast-Track:** CV > 1.0 auto-passes Gate 2
- **HR Power Bypass:** If HR prop fails hit rate but has L10 HRs >= 2 OR ISO > .200, force PASS

---

### Percentage-Based Hit Rate (Dynamic Sample Size)

For early-season or rookies with fewer than 20 games:

```python
games_played = min(player_games, 20)
required_hits = int(cfg["min_l20"] * games_played / 20)

# Example: Player has 10 games, Safe Haven HITS requires 16/20
# required_hits = 16 * 10 / 20 = 8 hits needed
```

---

## 3. The JIT Injury Beneficiary Tracker

The **Just-In-Time (JIT) Injury System** monitors injuries in real-time and identifies betting opportunities when star players are ruled out.

### Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                   JIT INJURY MICRO-SYNC                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│   ┌──────────────┐     60-second      ┌──────────────────┐     │
│   │  BDL/ESPN    │ ─────polling────▶  │  live_injuries   │     │
│   │  Injury API  │                    │  Collection      │     │
│   └──────────────┘                    └────────┬─────────┘     │
│                                                 │               │
│                                                 ▼               │
│                                    ┌────────────────────┐       │
│                                    │   VACUUM SERVICE   │       │
│                                    │                    │       │
│                                    │ • Identify Stars   │       │
│                                    │   (Usage > 25%)    │       │
│                                    │                    │       │
│                                    │ • Find Beneficiary │       │
│                                    │   Teammates        │       │
│                                    │                    │       │
│                                    │ • Calculate Boost  │       │
│                                    │   Modifiers        │       │
│                                    └────────┬───────────┘       │
│                                             │                   │
│                                             ▼                   │
│                              ┌──────────────────────────┐       │
│                              │   ACTIVE PROP GATE       │       │
│                              │                          │       │
│                              │ Does beneficiary have    │       │
│                              │ an active prop on        │       │
│                              │ today's board?           │       │
│                              │                          │       │
│                              │   YES ──▶ Show Alert     │       │
│                              │   NO  ──▶ Filter Out     │       │
│                              └──────────────────────────┘       │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Key Components

#### 1. Live Injury Micro-Sync (`live_injury_micro_sync.py`)

```python
# Configuration
POLLING_INTERVAL_SECONDS = 60
CACHE_TTL_SECONDS = 60

# Collection: live_injuries
{
    "player_name": "Joel Embiid",
    "sport": "nba",
    "status": "OUT",
    "injury": "Knee Management",
    "updated_at": ISODate("2026-04-13T00:00:00Z"),
    "expires_at": ISODate("2026-04-13T00:01:00Z")
}
```

#### 2. Vacuum Service (`injury_vacuum_service.py`)

**Star Identification:**
- Usage Rate > 25% = Star player
- When star is OUT/DOUBTFUL, trigger "Usage Vacuum"

**Beneficiary Calculation:**
```python
# Primary beneficiaries: Same position, will absorb usage
# Secondary beneficiaries: Teammates with complementary roles

vacuum_data = {
    "injured_player": "Joel Embiid",
    "team": "PHI",
    "usage_rate": 33.2,
    "beneficiaries": [
        {
            "name": "Tyrese Maxey",
            "rank": "primary",
            "boost_modifier": 1.15,  # 15% boost to projections
            "projections": {
                "PTS": "+3.2",
                "AST": "+1.5"
            }
        }
    ]
}
```

#### 3. Active Prop Gate (`vacuum.py`)

**The Critical Filter:**
```python
# Only show alerts where beneficiary has ACTIVE prop on today's board
active_players_on_board = set()

async for player_doc in cached_board.find({}, {"player_name": 1}):
    normalized = player_doc.get("player_name").strip().lower()
    active_players_on_board.add(normalized)

# Filter beneficiaries
for beneficiary in vacuum.get("beneficiaries", []):
    normalized_name = beneficiary["name"].strip().lower()
    
    if normalized_name not in active_players_on_board:
        filtered_count += 1
        continue  # Skip - no actionable betting value
    
    # Include in alerts - this is a LIVE opportunity
    alerts.append(build_alert(beneficiary))
```

**Why This Matters:**
- Injuries without active beneficiary props = noise
- Active Prop Gate ensures every alert is **actionable**
- No "dead" alerts cluttering the UI

### API Endpoints

```
GET /api/v3/vacuum/live-alerts          # NBA alerts with Active Prop Gate
GET /api/v3/mlb/vacuum/live-alerts      # MLB alerts with Active Prop Gate
GET /api/v3/injuries/live?sport=nba     # Raw injury feed
POST /api/v3/vacuum/check               # Manual injury refresh
```

### Frontend Integration

```javascript
// useLiveInjuries.js hook
const { alerts, loading } = useLiveInjuries(sport);

// Returns filtered alerts only:
[
  {
    "id": "joel-embiid-tyrese-maxey",
    "beneficiary_name": "Tyrese Maxey",
    "injured_player": "Joel Embiid",
    "boost_modifier": 1.15,
    "active_props": ["PTS 24.5", "AST 5.5"],
    "time_ago": "15 mins ago"
  }
]
```

---

## 4. The Vision Intel AI

The Vision Intel system transforms cold statistics into **human-readable betting rationale** using Gemini 3.1 Pro.

### System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     VISION INTEL ENGINE                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌────────────────┐    ┌────────────────┐    ┌──────────────┐  │
│  │ Qualified Props │──▶│ Batch Builder  │──▶│ Gemini 3.1   │  │
│  │ (Post-Oracle)   │    │ (JSON Payload) │    │ Pro API      │  │
│  └────────────────┘    └────────────────┘    └──────┬───────┘  │
│                                                      │          │
│                                                      ▼          │
│                                           ┌──────────────────┐  │
│                                           │ Parse Response   │  │
│                                           │ • intel_score    │  │
│                                           │ • verdict        │  │
│                                           │ • vision_intel   │  │
│                                           │ • risk_factor    │  │
│                                           └────────┬─────────┘  │
│                                                    │            │
│                                                    ▼            │
│                                           ┌──────────────────┐  │
│                                           │ MongoDB Cache    │  │
│                                           │ (Avoid Redundant │  │
│                                           │  API Calls)      │  │
│                                           └──────────────────┘  │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### NBA Lead Scout Prompt

**File:** `vision_intel_service.py`

```python
VISION_INTEL_BATCH_PROMPT = """## Role
You are the **Lead NBA Scout** for PropVision. Your job is to write a gritty, 
2-to-3 sentence scouting report explaining to a DFS bettor why we are locking 
in this specific PrizePicks prop.

**Tone:** Speak like a human sharp. Use basketball betting slang (e.g., 
'smash spot', 'usage bump', 'blowout risk', 'green light', 'riding the hot hand', 
'lock-down matchup'). DO NOT sound like a robot reading a spreadsheet. 
Never just list the raw percentages.

## Input Context
You will receive a data package containing:
1. **Model Stats:** VK Predicted Value, VK Edge, and VK Probability.
2. **Technical Gates:** Results of the 3-Gate qualification (Hit Rate, CV, Edge).
3. **Situational Intel:** Defense vs Position (DvP) matchup ranking, blowout risk, badges.
4. **Market Context:** Current DraftKings odds and prop classification (Goblin/Demon).

## CRITICAL: DvP Matchup Interpretation
The "defense" field shows the OPPONENT's defensive ranking vs that stat type:
- Rank #1-5 = OPPONENT is ELITE defender → BAD for player (flag as concern)
- Rank #6-15 = OPPONENT is solid → Challenging matchup
- Rank #16-25 = OPPONENT is weak → Favorable for player
- Rank #26-30 = OPPONENT is terrible → SMASH spot (boost confidence)

## Output Format (Strict JSON Array)
[
  {
    "prop_id": "PlayerName_STAT_Line",
    "intel_score": 7,
    "verdict": "CHALK",
    "vision_intel_summary": "Maxey cooking at home with 90% L10. Houston's 
                            perimeter D (#28) is a sieve. Lock the over.",
    "risk_factor": "Low",
    "adjusted_confidence": 0.82
  }
]

## Scoring Guidelines
- **intel_score 8-10**: Elite spot. Matchup + numbers + situation all align. CHALK.
- **intel_score 6-7**: Solid edge with minor concerns. VALUE.
- **intel_score 4-5**: Mixed signals. Lean VALUE but watch it.
- **intel_score 1-3**: Red flags override the math. TRAP.

## Automatic TRAP Triggers
- Elite DvP matchup (#1-5) against the stat type
- Blowout risk HIGH for volume stats (PTS, PRA)
- Line set at/above season average with negative cushion
- CV > 0.40 for non-combo stats indicates boom/bust volatility

## CRITICAL INSTRUCTION
Do NOT mention or reference L3 (last 3 games) hit rates or data. This data is 
NOT provided. Only reference data fields that exist in the PROPS DATA.

IMPORTANT: Return ONLY the JSON array. No markdown, no code blocks, no extra text."""
```

### MLB Lead Scout Prompt

**File:** `mlb_vision_intel.py`

```python
MLB_VISION_INTEL_BATCH_PROMPT = """## Role
You are the Lead MLB Scout for PropVision. Your job is to write a gritty, 
2-to-3 sentence scouting report explaining to a bettor why we are locking 
in this specific prop line.

## Tone
Speak like a human sharp. Use baseball betting slang (e.g., 'smash spot', 
'fade', 'trap', 'riding the hot hand', 'terrible bullpen', 'gas can', 
'meat on the mound', 'printing money', 'soft landing', 'volume play'). 
DO NOT sound like a robot reading a spreadsheet.

## The Data Translation Key (CRITICAL)

**matchup_multiplier** (DVP/pitcher matchup):
- High (>1.1): "smash spot", "soft landing", "gas can on the mound"
- Low (<0.9): "fade" territory, ace on the mound

**tempo_multiplier** (plate appearance volume):
- High (>1.05): "volume"—plenty of plate appearances, "ABs will pile up"
- Low (<0.95): limited PAs, bad lineup spot, "home team 9th inning risk"

**vk_edge** (projection vs line cushion):
- High (>0.5): "the line is disrespectful", "free money", "book is sleeping"
- Moderate (0.2-0.5): "comfortable edge", "solid value", "math works"
- Low (<0.2): "thin edge", "need the situation to hit"

**h10** (hit rate last 10 games):
- High (>70%): "riding the hot hand", "locked in", "can't miss right now"
- Low (<50%): struggles, cold streak, "due for regression"

**is_goblin**: Safe play - "chalky for a reason", "safe haven"
**is_demon**: Ceiling play - "boom or bust", "when it hits, it pays"

## Output Format
[
  {
    "prop_id": "PlayerName_STAT_Line",
    "intel_score": 8,
    "verdict": "CHALK",
    "vision_intel_summary": "Your 2-3 sentence gritty scouting report here.",
    "risk_factor": "Low",
    "adjusted_confidence": 0.85
  }
]

## Risk Assessment
- **Low**: Perfect storm - weak pitcher, volume, cushion. Lock it.
- **Medium**: Edge exists but one factor is sus.
- **High**: Red flags. The math says yes but your gut says no.

IMPORTANT: Return ONLY the JSON array. No markdown, no code blocks."""
```

### Verdicts & Scoring

| Verdict | Intel Score | Meaning |
|---------|-------------|---------|
| CHALK   | 8-10        | Lock it. All systems go. |
| VALUE   | 5-7         | Good edge, minor concerns. Worth a play. |
| TRAP    | 1-4         | Math looks good but context says no. Fade. |

### JIT Diff Check (Token Optimization)

Before calling Gemini, the system checks for existing cached intel:

```python
async def _jit_diff_check(self, new_picks, cached_intel):
    """
    Only send NEW picks to Gemini.
    Cached picks retain their existing vision_intel.
    """
    delta_picks = []
    
    for pick in new_picks:
        key = f"{pick['player_name']}|{pick['stat_type']}|{pick['line']}"
        
        if key in cached_intel:
            # Reuse cached intel
            pick['vision_intel'] = cached_intel[key]['vision_intel']
            pick['intel_score'] = cached_intel[key]['intel_score']
        else:
            # New pick - needs Gemini analysis
            delta_picks.append(pick)
    
    return delta_picks
```

**Result:** 80-90% reduction in Gemini API calls during typical syncs.

---

## Appendix: Key File References

| Component | File Path |
|-----------|-----------|
| NBA Oracle Apex | `/app/backend/services/oracle_apex_service.py` |
| MLB Oracle Apex | `/app/backend/services/mlb_oracle_apex_service.py` |
| NBA Vision Intel | `/app/backend/services/vision_intel_service.py` |
| MLB Vision Intel | `/app/backend/services/mlb_vision_intel.py` |
| Injury Micro-Sync | `/app/backend/services/live_injury_micro_sync.py` |
| Vacuum Service | `/app/backend/services/injury_vacuum_service.py` |
| Vacuum Routes | `/app/backend/routes/vacuum.py` |
| Sync Engine | `/app/backend/services/optimized_sync_engine.py` |
| Ferrari Routes | `/app/backend/routes/ferrari_tiers.py` |

---

*Generated by PropVision Architecture Documentation System*

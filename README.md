# PropVision

**The Edge Factory for PrizePicks.**

PropVision is a real-time betting intelligence terminal engineered to systematically exploit inefficiencies in PrizePicks' fixed-payout prop markets. While books adjust lines in real-time, PropVision pre-computes edges using pace, usage, matchup data, and tempo—delivering mathematically-proven plays before the market corrects.

This isn't a tips sheet. It's a quantitative trading desk for player props.

---

## The Problem We Solve

PrizePicks offers fixed payouts regardless of implied probability. A 2-leg parlay pays 3x whether you're combining two -400 favorites or two +150 longshots. Most bettors treat all props equally.

**We don't.**

PropVision identifies props where the true probability exceeds what the payout implies—then ranks them by mathematical certainty. The result: a curated board of edge-positive plays, updated in real-time.

---

## Core Features

### 1. Dual-Engine Dashboard

Seamless sport switching between dedicated **NBA** and **MLB** analytical models—each calibrated for sport-specific variance patterns.

```
┌─────────────────────────────────────────────────────────┐
│  [NBA]  [MLB]                              Live Sync ●  │
├─────────────────────────────────────────────────────────┤
│                                                         │
│   SAFE HAVEN          FRONT LINES         WAR ZONE     │
│   ━━━━━━━━━━          ━━━━━━━━━━━         ━━━━━━━━     │
│   Bank Plays          Value Hunting       Ceiling Rips │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

**NBA Engine:** Optimized for points, rebounds, assists, and PRA combos. Factors in pace, usage rate, defense vs. position (DvP), and blowout risk.

**MLB Engine:** Calibrated for hits, total bases, pitcher strikeouts, and outs recorded. Incorporates pitcher matchups, lineup position, team OBP, and bullpen fatigue.

One click. Complete context switch. Zero data bleed.

---

### 2. Tiered Intelligence — The Oracle

Every prop on the board passes through **The Oracle**—a 3-gate qualification system that filters thousands of lines down to ~30 elite plays.

#### Safe Haven — *The Bank Plays*
```
"If this doesn't hit, something is broken."
```
- **DK Odds:** ≤ -240 (heavily juiced favorites)
- **Hit Rate:** 80-90% over last 20 games
- **CV:** Ultra-low variance (consistency kings)
- **Edge:** Model projects significant cushion above line

These are the -400 favorites that PrizePicks still pays 3x on. Mathematical free money when stacked correctly.

#### Front Lines — *Trend Chasing*
```
"The numbers are moving in our direction."
```
- **DK Odds:** -145 to -239 (moderate juice)
- **Hit Rate:** 60-70% with recent uptick (L10 recency override)
- **Edge:** Positive cushion with favorable matchup

Players heating up. Usage bumps from injuries. Soft matchups the books haven't fully priced. This is where sharp money lives.

#### War Zone — *Demon Ceiling Spikes*
```
"When it hits, we eat. When it misses, we knew the risk."
```
- **DK Odds:** ≥ +150 (plus-money longshots)
- **Profile:** High-variance boom/bust players
- **Ceiling Check:** Must have cleared line 2x in L15 (proven spikes)
- **HR Power Bypass:** Home run props with L10 HRs ≥ 2 or ISO > .200

Demon props exist for a reason—they're volatile. But volatility works both ways. War Zone isolates the demons with *demonstrated* ceiling potential, not just prayer.

---

### 3. Pre-Computation Edge

By the time lines move, we've already identified the inefficiency.

**What We Factor Before You See The Pick:**

| Factor | NBA | MLB |
|--------|-----|-----|
| **Pace/Tempo** | Game pace, possessions per 48 | Plate appearances, lineup turnover |
| **Usage/Volume** | Usage rate, minutes projection | Batting order, team OBP rank |
| **Matchup** | DvP ranking (defense vs. position) | Pitcher handedness, bullpen strength |
| **Situational** | Back-to-back, home/away | Wind direction, day/night splits |
| **Injury Vacuum** | Teammate OUT = usage spike | Lineup changes, IL movements |

**The Math:**
```
Adjusted_Projection = Raw_VK × Matchup_Modifier × Tempo_Modifier

Where:
- Matchup_Modifier: 0.85 (brutal) to 1.15 (smash spot)
- Tempo_Modifier: 0.90 (limited volume) to 1.10 (pace-up game)
```

This isn't back-of-napkin analysis. It's systematic edge extraction.

---

### 4. Vision Intel — AI Scouting Reports

Numbers tell the story. **Vision Intel** writes the headline.

Every qualified pick receives a Gemini 3.1-powered scouting report that translates mathematical edges into human-readable betting rationale.

**Example Output:**
```json
{
  "player": "Tyrese Maxey",
  "stat": "PTS",
  "line": 24.5,
  "vision_intel": "Maxey cooking at home with 90% L10. Houston's perimeter D 
                   (#28) is a sieve—this is a smash spot. Lock the over.",
  "intel_score": 9,
  "verdict": "CHALK"
}
```

**The Tone:** Gritty. Sharp. No robot spreadsheet readings.

**The Verdicts:**
- **CHALK** — Lock it. All systems go.
- **VALUE** — Edge is real, minor concerns. Worth a play.
- **TRAP** — Math looks good but context says no. Fade it.

Vision Intel doesn't replace the math—it contextualizes it. When the model says "play" but the matchup screams "trap," you'll know.

---

### 5. Live Injury Advantage

Injuries create edges. But only if you act fast.

**The JIT Injury System:**
- 60-second polling loop monitoring injury feeds
- Automatic beneficiary identification (who absorbs the usage?)
- **Active Prop Gate:** Only surfaces alerts where the beneficiary has a live prop on today's board

```
┌────────────────────────────────────────────────────────┐
│  🔴 INJURY ADVANTAGE                     15 mins ago  │
├────────────────────────────────────────────────────────┤
│                                                        │
│  Joel Embiid OUT (Knee)                               │
│  ───────────────────────                              │
│  BENEFICIARY: Tyrese Maxey                            │
│  BOOST: +15% projection modifier                      │
│  ACTIVE PROPS: PTS 24.5 • AST 5.5                     │
│                                                        │
│  [View Analysis]                                       │
│                                                        │
└────────────────────────────────────────────────────────┘
```

No noise. No dead alerts. Every injury notification is **actionable**.

---

## The Stack

| Layer | Technology |
|-------|------------|
| Frontend | React + Tailwind CSS + Shadcn/UI |
| Backend | FastAPI + Python |
| Database | MongoDB (sport-isolated collections) |
| AI | Gemini 3.1 Pro (Vision Intel) |
| Data | The Odds API • BallDontLie API |

---

## Who This Is For

- **DFS Grinders** maximizing PrizePicks flex plays
- **Quantitative Bettors** who want systematic edge identification
- **Sharp Followers** looking for pre-market inefficiencies
- **Bankroll Managers** who need tiered risk allocation

---

## Who This Isn't For

- Gamblers looking for "locks" (nothing is locked)
- Bettors who don't understand variance
- Anyone expecting 100% win rates

PropVision identifies +EV plays. Variance still exists. Bankroll management still matters. But over volume, math wins.

---

## Quick Start

```bash
# 1. Navigate to the application
https://your-propvision-instance.com

# 2. Select your sport (NBA/MLB)

# 3. Review tiered picks
   - Safe Haven: Stack for floor
   - Front Lines: Cherry-pick value
   - War Zone: Sprinkle for ceiling

# 4. Check Vision Intel for context

# 5. Build your slip
```

---

## The Philosophy

> *"The market is efficient until it isn't. Our job is to find the gaps before they close."*

PrizePicks doesn't adjust payouts based on probability. They offer a fixed structure that assumes bettors pick randomly. PropVision exploits that assumption systematically.

We don't predict winners. We identify mathematical inefficiencies and let volume do the rest.

---

**PropVision** — *Where math meets market.*

---

<p align="center">
  <i>Built for sharps. Powered by data. Fueled by edge.</i>
</p>

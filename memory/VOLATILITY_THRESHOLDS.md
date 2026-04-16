# Volatility System — Threshold Reference

## Architecture

All volatility interpretation flows through one shared function:

```python
from services.volatility_profile import get_volatility_profile

profile = get_volatility_profile(cv, stat_type, line)
# Returns: cv_raw, score, label, is_extreme, badge_key, family, thresholds
```

Every consumer reads from this function. No scattered overrides exist anywhere in the codebase.

### Outputs

| Field | Type | Description |
|-------|------|-------------|
| `cv_raw` | float | Normalized CV (always decimal, never percentage) |
| `score` | float | 0–100 normalized score (100 = at or above extreme threshold) |
| `label` | string | `low`, `moderate`, `high`, or `extreme` |
| `is_extreme` | bool | True only when label = extreme |
| `badge_key` | string/null | `"volatility_extreme"` when extreme, else null |
| `family` | string | Prop family used for threshold selection |
| `thresholds` | dict | The exact thresholds applied: `{moderate, high, extreme}` |

### Label Assignment Rule

```
CV < moderate threshold  →  "low"
CV >= moderate threshold →  "moderate"
CV >= high threshold     →  "high"
CV >= extreme threshold  →  "extreme"  →  badge fires
```

Score = `min(100, (CV / extreme_threshold) × 100)`

---

## Prop Families

### MLB Binary (`mlb_binary`)

**Applies to:** Hits, Runs, RBIs, Home Runs, Stolen Bases, Earned Runs, Walks, HBP, Singles  
**Line range:** ≤ 1.5

These are binary/Bernoulli outcomes where high raw CV is structurally normal, not a signal of instability.

| CV Range | Label | Score Range | Badge | Interpretation |
|----------|-------|-------------|-------|----------------|
| 0.000 – 0.549 | low | 0.0 – 54.9 | — | Consistent for a binary prop |
| 0.550 – 0.799 | moderate | 55.0 – 79.9 | — | Normal variance for low-line |
| 0.800 – 0.999 | high | 80.0 – 99.9 | — | Elevated but expected for binary |
| 1.000+ | extreme | 100.0 | volatility_extreme | Genuinely unstable even for a binary prop |

**Thresholds:** `moderate=0.55, high=0.80, extreme=1.00`

---

### MLB Counting (`mlb_counting`)

**Applies to:** Total Bases, Batter Strikeouts, Pitcher Strikeouts, Hits+Runs+RBIs, Doubles  
**Line range:** Any (or Hits/Runs/etc. when line > 1.5)

Mid-range counting stats where moderate variance is common.

| CV Range | Label | Score Range | Badge | Interpretation |
|----------|-------|-------------|-------|----------------|
| 0.000 – 0.449 | low | 0.0 – 52.9 | — | Very consistent |
| 0.450 – 0.649 | moderate | 52.9 – 76.5 | — | Normal game-to-game variance |
| 0.650 – 0.849 | high | 76.5 – 99.9 | — | Boom-bust potential |
| 0.850+ | extreme | 100.0 | volatility_extreme | Unreliable outcome distribution |

**Thresholds:** `moderate=0.45, high=0.65, extreme=0.85`

---

### NBA Low Line (`nba_low_line`)

**Applies to:** AST, REB, STL, BLK, 3PM, Turnovers  
**Line range:** ≤ 4.5

Low-count NBA stats where variance is naturally higher.

| CV Range | Label | Score Range | Badge | Interpretation |
|----------|-------|-------------|-------|----------------|
| 0.000 – 0.499 | low | 0.0 – 55.5 | — | Rock solid for a low-line stat |
| 0.500 – 0.699 | moderate | 55.6 – 77.7 | — | Normal for assists/rebounds at low lines |
| 0.700 – 0.899 | high | 77.8 – 99.9 | — | Inconsistent, caution warranted |
| 0.900+ | extreme | 100.0 | volatility_extreme | Highly unreliable |

**Thresholds:** `moderate=0.50, high=0.70, extreme=0.90`

---

### NBA Mid Line (`nba_mid_line`)

**Applies to:** PTS, REB, AST, STL, BLK, 3PM, Turnovers, PA, PR  
**Line range:** 4.5 – 15.0

Mid-range NBA props where moderate consistency is expected.

| CV Range | Label | Score Range | Badge | Interpretation |
|----------|-------|-------------|-------|----------------|
| 0.000 – 0.399 | low | 0.0 – 49.9 | — | Very consistent scorer/rebounder |
| 0.400 – 0.599 | moderate | 50.0 – 74.9 | — | Normal game-to-game fluctuation |
| 0.600 – 0.799 | high | 75.0 – 99.9 | — | Volatile — matchup dependent |
| 0.800+ | extreme | 100.0 | volatility_extreme | Boom-or-bust, unreliable |

**Thresholds:** `moderate=0.40, high=0.60, extreme=0.80`

---

### NBA High Line (`nba_high_line`)

**Applies to:** PRA, PTS, P+A, P+R, Pts+Reb, Pts+Ast, Pts+Reb+Ast, Fantasy Score  
**Line range:** > 15.0

High-line combo stats where even small CV indicates real inconsistency.

| CV Range | Label | Score Range | Badge | Interpretation |
|----------|-------|-------------|-------|----------------|
| 0.000 – 0.299 | low | 0.0 – 42.8 | — | Elite consistency at volume |
| 0.300 – 0.499 | moderate | 42.9 – 71.3 | — | Normal for a 25+ PRA line |
| 0.500 – 0.699 | high | 71.4 – 99.9 | — | Significant variance risk |
| 0.700+ | extreme | 100.0 | volatility_extreme | Unreliable despite high volume |

**Thresholds:** `moderate=0.30, high=0.50, extreme=0.70`

---

### Default (unknown prop types)

**Applies to:** Any stat_type not matched by the families above.

| CV Range | Label | Score Range | Badge |
|----------|-------|-------------|-------|
| 0.000 – 0.399 | low | 0.0 – 49.9 | — |
| 0.400 – 0.599 | moderate | 50.0 – 74.9 | — |
| 0.600 – 0.799 | high | 75.0 – 99.9 | — |
| 0.800+ | extreme | 100.0 | volatility_extreme |

**Thresholds:** `moderate=0.40, high=0.60, extreme=0.80`

---

## Family Selection Logic

Props are matched to families by `stat_type` (case-insensitive) and `line`:

```
1. If stat_type ∈ mlb_binary.stat_types AND line ≤ 1.5  →  mlb_binary
2. If stat_type ∈ mlb_counting.stat_types              →  mlb_counting
3. If stat_type ∈ nba_low_line.stat_types AND line ≤ 4.5 →  nba_low_line
4. If stat_type ∈ nba_mid_line.stat_types AND line ≤ 15  →  nba_mid_line
5. If stat_type ∈ nba_high_line.stat_types               →  nba_high_line
6. Otherwise                                              →  default
```

Priority is top-down. A stat like "Hits" at line 0.5 matches `mlb_binary` (rule 1). The same "Hits" at line 2.5 would match `mlb_counting` (rule 2).

## CV Normalization

Raw CV is always normalized to decimal before threshold comparison:

```
If CV > 5.0  →  treated as percentage scale, divided by 100
If CV < 0    →  treated as invalid, returns "unknown" profile
```

This handles legacy data stored as percentages (e.g., 35.5 → 0.355).

---

## Downstream Consumers

Every location that interprets CV routes through `get_volatility_profile`:

| Consumer | Uses |
|----------|------|
| `ferrari_tiers.py` — `overlay_enrichment_cache` | Sets `volatility_score/label/family` on ALL picks. Reconciles `scout_badges` |
| `ferrari_tiers.py` — `enrich_mlb_intel_suite` | Badge decision + confidence classification |
| `nba_adapter.py` — tier gate checks | Fails props where `vol.label == "extreme"` |
| `mlb_adapter.py` — Safe Haven gates | Fails binary props where `vol.is_extreme`, standard where `label ∈ (high, extreme)` |

No other code path assigns volatility_score, volatility_label, or the volatility_extreme badge.

---

*Source of truth: `/app/backend/services/volatility_profile.py`*  
*This document: `/app/memory/VOLATILITY_THRESHOLDS.md`*  
*Last verified: April 17, 2026*

# Front Lines ROI Audit — Read-Only Findings
**Date:** 2026-05-09
**Status:** ROOT CAUSE IDENTIFIED. NO PATCHES APPLIED.
**Verdict:** The reported `-14.5% ROI on 75.8% HR` is **not real**. It is caused by two compounding bugs (one in capture pipeline, one in ROI math) — not a calibration / settlement / model failure.

---

## TL;DR (1-line root cause)

> **68 of 95 (71.6%) historical FL picks are PrizePicks "goblins"** — alt-line PP-house products with no sportsbook quote stored. The ROI calculator treats `missing odds + miss = −1u` but `missing odds + hit = 0u`, which artificially manufactures a `−23.5%` ROI bias on every goblin pick. **When picks are filtered to those with proper sportsbook odds, FL ROI is `+8.35%` on `+74.1%` HR — exactly what you'd expect.**

---

## 1 · Front Lines bucket config (file/line)

`backend/services/scoring/gates/thresholds.py`, lines 700–702 + 724–737:

```python
UNIVERSAL_SAFE_HAVEN_MAX: int = -300
UNIVERSAL_WAR_ZONE_MIN:   int = +150

def resolve_target_tier(sport, reference_odds):
    if reference_odds is None:                 return None         # → unqualified
    if reference_odds <= UNIVERSAL_SAFE_HAVEN_MAX:  return "safe_haven"
    if reference_odds >= UNIVERSAL_WAR_ZONE_MIN:    return "war_zone"
    return "front_lines"                                            # -299..+149
```

| | User-stated intended | Production (2026-04-29 cutover) | Pre-2026-04-25 |
|---|---|---|---|
| FL lower | -249 | **-299** | -249 |
| FL upper | +150 | **+149** | +149 |

The **production routing band today is `-299 to +149`** (one cent tighter on the WZ side, 50 cents looser on the SH side than what you stated as "intended"). Routing inputs use `tier_reference_odds` which post-2026-05-09 chain port can be DK / FD / MGM / BOL. For pre-cutover historical data the reference field was `dk_odds`.

**No routing bug found** — every historical FL pick with stored odds (n=27) is in `[-247, -170]`, which fits both the current and pre-cutover bands.

---

## 2 · Historical FL stats

```
NBA tier=front_lines settled                : 95
  with dk_odds                              : 27   (28.4%)
  missing dk_odds                           : 68   (71.6%)
  outcome counts                            : hits=72  misses=23  pushes=0
  HR (hits / decided)                       : 75.79%
  avg dk_odds (n=27 known only)             : -217.3
  median dk_odds (n=27)                     : -222
  min / max dk_odds                         : -247 / -170
  count outside [-249, +150] (user-intended): 0
  count outside [-299, +149] (production)   : 0
  ROI source field                          : `dk_odds` (no `tier_reference_odds` on legacy snapshots)
```

## 3 · ROI recalculation under three handling modes

| Mode | n | HR | Net Units | **ROI** |
|---|---:|---:|---:|---:|
| **A** – drop missing-odds picks (defensible) | 27 | 74.1% | **+2.25u** | **+8.35%** ✅ |
| **B** – CURRENT BUG (miss=−1, hit=0 for missing) | 95 | 75.8% | **−13.75u** | **−14.47%** ❌ |
| **C** – impute missing as FL avg (−217) | 95 | 75.8% | **+10.18u** | **+10.72%** ✅ |

The reported `-14.5% ROI` matches Mode B exactly — confirming the ROI calculator is using the buggy missing-odds handling.

### Sampling-bias check (does missing-odds correlate with worse outcomes?)
```
has_odds : n=27  HR=74.1%
no_odds  : n=68  HR=76.5%
```

Difference is well within sampling noise on these sizes. **The missing-odds picks are not systematically losing more often** — the apparent under-performance is purely an artifact of the asymmetric ROI math on missing odds.

---

## 4 · Bucket breakdown

```
  bucket          n  hits miss  HR%   avg_odds  net    ROI%
  [-249, -200]   23   18    5  78.3   -222.8   +3.11  +13.5
  [-199, -150]    4    2    2  50.0   -185.5   -0.85  -21.3
  [-149, -100]    0
  [-99, +100]     0
  [+101, +149]    0
  [+150, +199]    0
  > +199          0
  odds=None      68   52   16  76.5   —        -16.00 -23.5  ← THE BUG SOURCE
```

Two things to note:
1. The `[-199,-150]` bucket of 4 picks has 50% HR — TINY sample, no signal.
2. The `[-149, +149]` portion of the FL band has **zero historical picks**. Old NBA picks were all heavy chalk (-200 or longer) or PP goblins. The "front lines mid-range" was never actually populated under the legacy ranker.

---

## 5 · Tier-routing correctness

```
FL props outside [-249, +150] (user-stated)       : 0
FL props outside [-299, +149] (production)        : 0
```

**No tier-routing bug.** Every FL pick with known sportsbook odds was correctly bucketed. The 68 missing-odds picks are PrizePicks-only goblins with no sportsbook quote at all — they were tier-routed via a different code path (PP-tier mapping / `dk_tier` field on `full_prop_data`), not via American odds.

---

## 6 · Outcome correctness — 20-random-loss spot check

```
20 of 20 verified : computed outcome matches stored outcome
0 mismatches
```

Sample:

| Player | Stat | Line | Side | Actual | Stored | Computed |
|---|---|---:|---|---:|:---:|:---:|
| Keldon Johnson | PRA | 13.5 | OVER | 11.0 | miss | miss ✓ |
| Coby White | PRA | 16.5 | OVER | 4.0 | miss | miss ✓ |
| Nikola Jokic | PRA | 46.5 | OVER | 45.0 | miss | miss ✓ |
| Nickeil Alexander-Walker | PRA | 22.5 | OVER | 22.0 | miss | miss ✓ |
| (16 more, all consistent) | | | | | | |

**No settlement bug.** Outcomes are matched correctly against `actual_value` vs `line` per side.

Note: 5 of the 20 losses are on a tied line (`actual == line` → miss because line is .5-padded so tie is impossible; 22.0 vs 22.5 is miss not push). Settlement math is correct.

---

## 7 · Odds-source consistency

```
outer dk_odds  (`forward_test_outcomes.dk_odds`) ==
inner dk_odds  (`full_prop_data.dk_odds`)        : 27 of 27 (no drift)
```

**No "stale odds vs current odds" issue.** Where odds are stored, they're consistent across both fields.

The right ROI source going forward depends on the prop type:

| Prop source | Correct ROI odds field |
|---|---|
| Sportsbook-anchored (DK / FD / MGM / BOL) | `tier_reference_odds` (post-chain-port; was `dk_odds` historically) |
| **PrizePicks goblin** (downgraded line) | **`pp_payout` / `pp_multiplier`** (e.g., 0.5x for typical goblin) |
| **PrizePicks demon** (upgraded line) | **`pp_payout` / `pp_multiplier`** (e.g., 2× or 3×) |
| PrizePicks standard | PP base payout (1× minus juice) |

The 68 goblins in this dataset have NO sportsbook quote and need PP payout to compute ROI honestly. The current ROI math has no awareness of PP payouts, so it falls back to `dk_odds=None` → bug.

---

## 8 · Model-calibration cross-check (separate finding)

```
n with vk_prob_over           : 95
avg model-predicted HR        : 85.6%
realized HR                   : 75.8%
calibration delta             : -9.8pp
```

The model is **9.8pp overconfident** on FL props. This is a **separate calibration issue** (not the ROI bug) and should be tracked, but it does NOT explain the -14.5% ROI — the model overconfidence affects pick selection, not unit-PnL on settled picks.

---

## 9 · Root cause classification (per directive checklist)

| Candidate cause | Verdict | Evidence |
|---|---|---|
| **ROI math bug** | ✅ **PRIMARY** | Missing odds → miss=−1, hit=0 (asymmetric). Manufactures −23.5% bias on goblins. |
| **Wrong odds field** | ✅ **CONTRIBUTING** | PP goblins/demons need PP payout, not DK odds. Stack only knows `dk_odds`. |
| **Stale / missing odds** | ✅ Confirmed for 68 of 95 picks | All 68 missing-odds picks are `is_goblin=True`. None are sportsbook-quoted. |
| **Tier bucket bug** | ❌ Rejected | All known-odds FL picks fit `[-249, +150]` and `[-299, +149]` |
| **Settlement bug** | ❌ Rejected | 20/20 random losses verified correct |
| **Genuinely unprofitable despite HR** | ❌ Rejected | n=27 with proper odds: HR 74.1%, **ROI +8.35%** |

---

## 10 · Recommended fix (not implemented)

**Two-part patch (subject to your approval):**

### A. Coverage fix — capture PP payout
- On every snapshot, persist `pp_payout` / `pp_multiplier` for goblins/demons next to `dk_odds`.
- Schema additions: `forward_test_outcomes.pp_payout_multiplier`, `pp_payout_format` (`goblin_0_5x` / `demon_2x` / `demon_3x` / `standard`).
- Backfill: re-resolve goblin picks against the pre-locked PP payout snapshot at capture time.

### B. ROI math fix — outcome-aware fallback chain
```python
def fl_pnl(outcome, dk_odds, pp_multiplier, ref_odds):
    if outcome == 'push': return 0.0
    if outcome == 'miss': return -1.0      # full unit lost regardless of odds source
    # outcome == 'hit'
    if dk_odds is not None:
        return _hit_pnl_from_american(dk_odds)
    if pp_multiplier is not None:          # PP goblin/demon path
        return pp_multiplier - 1.0
    if ref_odds is not None:               # post-2026-05-09 chain port fallback
        return _hit_pnl_from_american(ref_odds)
    # No way to know payout — exclude from ROI numerator AND denominator.
    return None  # caller drops the pick from sample
```

This makes "miss with no odds" symmetric with "hit with no odds": both are excluded if the payout truly cannot be determined.

### C. Regression tests
- 20-prop fixture that includes 5 goblins, 5 demons, 5 sportsbook-anchored, 5 missing-everything → assert ROI matches hand-calculated truth.
- Mutation guard: drop the `pp_multiplier` branch → assert sample ROI returns to the buggy `-14.5%` shape (proves the test catches the regression).

---

## 11 · Constraints honored
- ✅ No code changes (read-only audit)
- ✅ No threshold / gate / scoring changes
- ✅ No SH / FL / WZ / UNDER tuning proposed
- ✅ Recommended fix is **isolated to ROI calculation + capture schema**, not the model
- ✅ Did not overclaim — `n=27` known-odds is TINY sample; the +8.35% ROI is "consistent with sportsbook chalk on a 75% HR base", not a confirmed long-run figure

---

## 12 · Awaiting your decision

a. ✅ **Approve the two-part fix** (capture PP payout + outcome-aware ROI chain) and add the regression tests.
b. 🔍 **Backfill audit first** — go through goblin payout history (PP exposes 0.5× multiplier publicly) and recompute the FL ROI using the actual goblin payouts to get a true historical number before patching. *Recommended if you want a real n=95 ROI figure.*
c. 🛑 **Hold** — no fix; document the finding and move on.
d. 📝 Other directive.

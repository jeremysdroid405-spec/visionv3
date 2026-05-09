# NBA War Zone Longshot Mode Audit
**Date:** 2026-05-09
**Mode:** READ-ONLY. No patches applied.
**Universe:** 637 NBA WZ-routed OVER rejects (player props only) that fail `direction_gate`.

---

## TL;DR

- **The hypothesis is correct.** Among direction-fail rejects there is a ~27-prop pocket of genuine positive-EV model-vs-market disagreements (avg EV per $1 = +$0.41, avg HR 47%, avg odds +259, avg CV 0.53).
- **The user's proposed criteria as-written admit 0 props.** The blocker is `vision_score_v2 ≥ 50` — direction-fail props are mathematically incompatible with that threshold (v2 includes a `(μ − line) / σ` term that is **negative by construction** when direction fails, dragging v2 to single digits).
- **Recommendation: implement `war_zone_longshot_gate` with `v2 ≥ 10`** (NOT 50) and the rest of the criteria unchanged. This admits 27 props with strong EV / HR profile and surfaces the longshots cleanly.
- **No safe haven, no front lines, no UNDER side, no scoring math touched.** Fully isolated to a new conditional rescue path inside the WZ tier.

---

## 1 · Per-criterion pass count (each evaluated alone, n = 637)

| Criterion | passes alone | % of pool |
|---|---:|---:|
| `ref_odds ≥ +150` | 637 | 100.0 |
| `p_true / implied non-null` | 626 | 98.3 |
| `edge ≥ 5pp` | 122 | 19.2 |
| `EV > +0.10` | 158 | 24.8 |
| `HR_l10 ≥ 40` | 98 | 15.4 |
| `CV ≤ 1.25` | 594 | 93.2 |
| **`vision_score_v2 ≥ 50`** | **0** | **0.0** ⛔ |
| `book_count ≥ 1` | 594 | 93.2 |
| `not no_reference_market` | 637 | 100.0 |

**Single criterion that no direction-fail reject passes: `v2 ≥ 50`.** This is mathematically inevitable. `vision_score_v2` is a weighted sum that includes the directional-margin component `(μ − line) / σ`. When `μ < line` (the literal definition of direction-fail), that component goes negative and crushes v2 into single digits. Any v2 floor ≥ 30 is a hard veto on the entire longshot rescue concept.

## 2 · Cumulative pass count (criteria applied in spec order)

```
start                         : 637
after `ref_odds >= +150`      : 637
after `p_true non-null`       : 626  (-11)
after `edge >= 5pp`           : 122  (-504)   ← biggest single drop
after `EV > +0.10`            : 122  (no drop, EV already >0.10 if edge>=5pp at +150 odds)
after `HR_l10 >= 40`          :  43  (-79)
after `CV <= 1.25`            :  41  (-2)
after `v2 >= 50`              :   0  (-41)    ← spec-as-written kills the survivors
after `book_count >= 1`       :   0
after `not no_reference`      :   0
```

## 3 · 2-D sensitivity table (edge_pp × v2 floor)

```
                v2≥50   v2≥40   v2≥30   v2≥20   v2≥10   v2≥0
edge ≥ 5.0pp    0       0       3       14      27      41
edge ≥ 4.0pp    0       0       3       14      27      41
edge ≥ 3.0pp    0       0       3       14      29      45
edge ≥ 0.0pp    0       0       3       14      29      45
```

**The edge floor is essentially a no-op below 5pp** (all your other filters bite harder). The lever that matters is the v2 floor, and even at v2 ≥ 0 only 41 props admit — meaning the EV / HR / CV gates are doing the actual work.

## 4 · Admission pool by HR bucket and odds bucket (full pool)

### By odds
| bucket | n | avg p_true | avg implied | avg EV | avg CV | positive-EV n |
|---|---:|---:|---:|---:|---:|---:|
| [+100,+150) | 0 | — | — | — | — | — |
| [+150,+200) | 109 | 0.308 | 0.364 | −0.156 | 0.58 | 37 |
| [+200,+300) | 167 | 0.239 | 0.293 | −0.184 | 0.58 | 53 |
| [+300,+500) | 145 | 0.185 | 0.207 | **−0.111** | 0.61 | 53 |
| [+500+) | 216 | 0.095 | 0.119 | −0.211 | 0.64 | 61 |

Average EV is negative across all odds buckets, but each bucket contains a meaningful **positive-EV minority** (33–43 % of each bucket has EV > 0). The audit has to find them, not retain the whole bucket.

### By HR_l10
| bucket | n | avg p_true | avg implied | avg EV | avg CV | positive-EV n |
|---|---:|---:|---:|---:|---:|---:|
| [0,30) | 422 | 0.142 | 0.196 | −0.274 | 0.61 | 97 |
| [30,40) | 111 | 0.256 | 0.277 | −0.047 | 0.60 | 49 |
| [40,50) | 63 | 0.297 | 0.283 | **+0.099** | 0.60 | 35 |
| [50,60) | 20 | 0.352 | 0.343 | **+0.051** | 0.58 | 9 |
| [60,70) | 12 | 0.394 | 0.326 | **+0.257** | 0.72 | 10 |
| [70+) | 3 | 0.378 | 0.332 | **+0.147** | 0.78 | 3 |

**HR ≥ 40 is the inflection point** — average EV goes positive. The HR ≥ 40 floor in the user's spec is well-chosen; this is what saves the audit from the `[0,30)` longshot junk pile.

## 5 · Counterfactual — admitted at `v2 ≥ 10 + edge ≥ 5pp` (other criteria unchanged)

| metric | value |
|---|---|
| Admitted | **27** props (out of 637 direction-fail rejects, 4.2 %) |
| avg EV per $1 | **+$0.413** |
| avg HR_l10 | **46.7 %** |
| avg ref_odds | **+259** |
| avg CV | **0.53** |
| avg p_true | **0.349** |
| avg implied | **0.225** |
| avg edge | **+12.4 pp** |

## 6 · Top 25 admitted (sorted by EV per $1 descending)

| # | Player | Stat | L | refO | refBk | HR10 | CV | μ−L | p_true | imp | edge_pp | v2 | EV/$1 |
|---:|---|---|---:|---:|:---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | Deandre Ayton | PRA | 24.5 | +410 | dk | 40 | 0.43 | −0.85 | 0.429 | 0.196 | +23.3 | 28.4 | **+1.190** |
| 2 | Max Strus | PR | 19.5 | +484 | dk | 40 | 0.56 | −3.10 | 0.320 | 0.171 | +14.9 | 17.5 | +0.869 |
| 3 | Donovan Mitchell | REB | 5.5 | +307 | dk | 40 | 0.41 | −0.98 | 0.450 | 0.246 | +20.4 | 16.8 | +0.832 |
| 4 | Deandre Ayton | PTS | 14.5 | +543 | dk | 40 | 0.55 | −3.73 | 0.252 | 0.156 | +9.7 | 12.1 | +0.623 |
| 5 | Max Strus | PA | 14.5 | +253 | dk | 40 | 0.67 | −0.68 | 0.456 | 0.283 | +17.2 | 25.7 | +0.609 |
| 6 | Deandre Ayton | PA | 14.5 | +391 | dk | 40 | 0.53 | −3.14 | 0.310 | 0.204 | +10.7 | 15.8 | +0.525 |
| 7 | Alex Caruso | REB | 3.5 | +173 | dk | 60 | 0.54 | −0.45 | 0.550 | 0.366 | +18.4 | 23.6 | +0.502 |
| 8 | Cason Wallace | PR | 11.5 | +198 | fd | 40 | 0.51 | −0.01 | 0.499 | 0.336 | +16.4 | 35.9 | +0.488 |
| 9 | Tobias Harris | PRA | 30.5 | +220 | fd | 40 | 0.30 | −0.85 | 0.452 | 0.312 | +13.9 | 30.5 | +0.446 |
| 10 | Tobias Harris | PTS | 22.5 | +270 | fd | 40 | 0.38 | −1.38 | 0.378 | 0.270 | +10.8 | 22.0 | +0.399 |
| 11 | Dean Wade | REB | 4.5 | +173 | dk | 50 | 0.51 | −0.38 | 0.500 | 0.366 | +13.4 | 24.9 | +0.365 |
| 12 | Isaiah Stewart II | REB | 3.5 | +170 | dk | 40 | 0.60 | −0.91 | 0.500 | 0.370 | +13.0 | 13.8 | +0.350 |
| 13 | Cason Wallace | REB | 3.5 | +195 | dk | 60 | 0.57 | −0.40 | 0.450 | 0.339 | +11.1 | 23.6 | +0.328 |
| 14 | LeBron James | PTS | 25.5 | +225 | fd | 50 | 0.32 | −0.97 | 0.407 | 0.308 | +10.0 | 24.6 | +0.324 |
| 15 | Marcus Smart | AST | 5.5 | +278 | dk | 60 | 0.66 | −1.04 | 0.350 | 0.265 | +8.5 | 11.0 | +0.323 |
| 16 | Marcus Smart | RA | 9.5 | +350 | fd | 50 | 0.47 | −1.73 | 0.291 | 0.222 | +6.9 | 14.1 | +0.310 |
| 17 | Deandre Ayton | PTS | 11.5 | +189 | dk | 40 | 0.55 | −0.73 | 0.448 | 0.346 | +10.2 | 28.7 | +0.295 |
| 18 | Luke Kennard | PR | 14.5 | +225 | dk | 40 | 0.61 | −1.93 | 0.398 | 0.308 | +9.0 | 18.6 | +0.292 |
| 19 | De'Aaron Fox | 3PM | 1.5 | +220 | dk | 60 | 0.88 | −0.17 | 0.400 | 0.312 | +8.8 | 11.4 | +0.280 |
| 20 | Luke Kennard | PA | 14.5 | +276 | dk | 40 | 0.61 | −2.89 | 0.339 | 0.266 | +7.3 | 14.0 | +0.274 |
| 21 | Rui Hachimura | PTS | 15.5 | +152 | fd | 50 | 0.42 | −0.01 | 0.499 | 0.397 | +10.2 | 32.9 | +0.257 |
| 22 | Max Strus | PRA | 19.5 | +296 | dk | 40 | 0.50 | −1.95 | 0.317 | 0.253 | +6.5 | 12.6 | +0.257 |
| 23 | Marcus Smart | RA | 8.5 | +205 | fd | 70 | 0.47 | −0.73 | 0.408 | 0.328 | +8.0 | 23.1 | +0.244 |
| 24 | Luke Kornet | PR | 9.5 | +290 | dk | 40 | 0.54 | −3.58 | 0.313 | 0.256 | +5.7 | 14.2 | +0.222 |
| 25 | Marcus Smart | PA | 18.5 | +188 | fd | 50 | 0.48 | −1.68 | 0.406 | 0.347 | +5.9 | 21.9 | +0.170 |

## 7 · Worst-looking admitted (lowest EV at v2 ≥ 10 + edge ≥ 5pp)

The admitted minimum is `Smart PA 18.5 OVER` (#25 above) — EV +0.170, HR 50%, edge +5.9pp, μ=16.82 (line 18.5, off by 1.68). **No obvious junk admissions.** No HR<40 longshots leak through (HR floor is doing its job). No extreme `μ−line` outliers (worst is Kornet PR 9.5 at μ=5.92, off by 3.58 — still a real-game possibility for a 24-min rotation player).

## 8 · Top absolute-EV picks the spec correctly REJECTS

These look attractive but are correctly excluded by the HR ≥ 40 floor (HR < 30 longshots):

```
Luke Kennard  AST  6.5 +2900  HR=15  EV=+3.50  ← spec rejects ✓ (HR < 40)
Keldon Johnson AST 2.5 +1200  HR=10  EV=+1.60  ← spec rejects ✓
Jake LaRavia  REB  5.5 +1140  HR=20  EV=+1.48  ← spec rejects ✓
Marcus Smart  STL  3.5  +850  HR=30  EV=+1.38  ← spec rejects ✓
Luke Kennard  AST  4.5  +880  HR=10  EV=+0.96  ← spec rejects ✓
```

The HR ≥ 40 floor is **load-bearing**. Without it, the gate becomes a lottery-ticket farm. With it, the admitted set sticks to mid-frequency events that just had a bad recent dip below the line.

---

## 9 · Decision

> **Per the directive's decision rule:** _"If the added candidates show positive EV and reasonable historical support, propose a separate `war_zone_longshot_gate`."_

The 27 admitted candidates show:
- **avg EV per $1 = +$0.413** (strongly positive)
- **avg HR_l10 = 47 %** (genuine historical support)
- **avg edge = +12.4pp** vs implied (model-vs-market disagreement, not noise)
- avg μ−line = −1.66 (small directional miss, not flagrant — the longshot hypothesis is exactly that small μ-misses on plus-odds props are profitable)
- 0 obvious junk additions

**Recommendation: implement `war_zone_longshot_gate` as a NEW rescue path** with these criteria:

```python
WZ_LONGSHOT_RESCUE = {
    # Only rescues `direction_gate` failures. All other gate failures
    # (coverage / hit_rate / edge / vision / market_structure) hard-fail.
    "applies_to_failed_gates": {"direction_gate"},
    "min_reference_odds":      150,        # WZ band (unchanged)
    "min_edge_pp":             5.0,        # p_true - implied >= 5pp
    "min_ev_per_dollar":       0.10,       # EV per $1 stake > +$0.10
    "min_hit_rate_l10":        40.0,       # HR_l10 >= 40 %
    "max_cv":                  1.25,       # CV cap
    "min_vision_score_v2":     10.0,       # v2 >= 10 (NOT 50 — see audit)
    "min_book_count":          1,          # at least 1 sportsbook anchor
    "require_reference_market": True,      # no_reference_market is False
}
```

**Note on the v2 floor change:** v2 ≥ 50 is mathematically incompatible with direction-fail props because v2 includes a `(μ − line) / σ` component that goes negative when μ < line. v2 ≥ 10 still excludes truly broken model output (raw v2 ≈ 0 means projection / sigma / hit-rate / TP all collapsed) while admitting the genuine "small directional miss on plus odds" pocket the longshot mode is designed for.

**Audit isolation:** This rescue path is OVER-only, war_zone-only, only triggers when the **sole** failed gate is `direction_gate`. Direction failures combined with edge/coverage/HR/CV/v2/market-structure failures do NOT rescue. Safe Haven, Front Lines, UNDER, and scoring math are untouched.

## 10 · Constraints honored
- ✅ No code changes (read-only audit)
- ✅ No threshold changes
- ✅ No relaxation of Safe Haven / Front Lines / UNDER
- ✅ No removal of direction gate globally — only proposed conditional rescue
- ✅ No PrizePicks-as-truth, no synthetic odds
- ✅ Recommendation is **scoped to a new opt-in gate**, not a replacement of the direction gate

## 11 · What I will NOT do without explicit approval
- Apply the proposed gate
- Modify `vision_score_v2` formula or floor anywhere else
- Touch SH / FL / UNDER configurations
- Wire the rescue into UNDER side without a separate audit

## 12 · Awaiting your decision

a. ✅ **Implement `war_zone_longshot_gate`** with the criteria above (v2 ≥ 10 instead of 50). Add tests, recompute, deliver before/after diff with newly admitted props.
b. 🔍 **Tighten further first** — e.g., HR ≥ 50, EV > +0.20, or odds ≥ +200 — then implement.
c. 🛑 **Hold** — keep strict direction gate. The 27-prop admission is too risky on this slate alone.
d. 📊 **Validate against settled history first** — pull last 30 days of settled NBA props, replay the proposed gate, report actual win rate vs predicted EV, then decide.
e. 📝 Other directive.

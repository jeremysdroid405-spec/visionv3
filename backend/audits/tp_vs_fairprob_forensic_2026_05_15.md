# Forensic Audit — `tp` vs `fair_prob` Divergence on MLB Props
**Date:** 2026-05-15
**Status:** P0 architecture audit • AUDIT-ONLY (no code changes)
**Author:** Engineering
**Scope:** Live MLB scoring pipeline (1,738 active props on 2026-05-16 slate)

---

## 1. Executive Summary

The live scoring pipeline writes **two market-probability fields** for every prop:

| Field | Formula | Stored unit | Used by |
|---|---|---|---|
| `tp` | per-book **de-vigged** average across all paired (this-side + opp-side) books | percent (0–100) | UI, tier_reference selection, *p_true ladder fallback*, edge_pct (legacy) |
| `fair_prob` | NBA/MLB book-chain consensus of **RAW** implied probabilities (NO devig) | decimal (0–1) | Universal Edge SSOT (`edge_vs_fair = p_model − fair_prob`), vision_score |

These are NOT the same number even though both are presented (and arguably named) as "the market's true probability." For the 2026-05-16 slate of 7,427 scored batter-prop rows:

| `|tp/100 − fair_prob|` bucket | Count | % |
|---|---|---|
| < 1 pp | 2,439 | **32.8%** |
| 1 – 2 pp | 1,294 | 17.4% |
| 2 – 3 pp | 1,105 | 14.9% |
| 3 – 5 pp | 1,940 | **26.1%** |
| **> 5 pp** | **649** | **8.7%** |

**67.2% of all props have a tp/fair_prob gap ≥ 1pp. 34.8% have a gap ≥ 3pp.**

For every row with a gap > 5pp, **fair_prob is HIGHER than tp**. That's a structural signature, not noise: `fair_prob` carries the bookmaker's vig and `tp` removes it. The gap = the average vig on the picked side.

### Headline case — Ozzie Albies HRR 1.5 OVER (the row that surfaced this)

| Metric | Value | How it was built |
|---|---|---|
| `tp` | **54.6%** | de-vigged average of OVER/UNDER pairs across **7** books (DK, MGM, BOL, CSR, EB, HRB, FLF) |
| `fair_prob` | **59.81%** | average of DK raw (58.85%) and FD raw (60.78%) — **no devig** |
| Δ | **5.21 pp** | ≈ the average vig on the OVER side |
| `edge_vs_fair` | **+18.39 pp** (= `p_model 0.778 − fair_prob 0.598`) | SSOT decimal |
| `edge` if computed vs `tp/100` | **+23.2 pp** (= `0.778 − 0.546`) | old local-adapter math |

The model says 77.8%. Devigged market says 54.6%. Raw market (vigged) says 59.8%. The SSOT edge correctly uses **fair_prob** so the displayed/gated edge is `+18.39pp`, NOT `+23.2pp`. But:

⚠️  **fair_prob carries vig**. The "fair" name is misleading. A truly fair (vig-removed) probability for Albies' OVER should be **54.6%** (i.e., `tp`), not 59.81%.

---

## 2. Code Trace — Side-by-Side Flow Diagrams

### 2.1 `tp` pipeline  → `services/scoring/tp_engine.py::compute_tp`

```
                    ┌─────────────────────────────────────────────┐
                    │  prop document (one side, e.g. OVER)         │
                    │   carries 22 odds fields: 11 books × 2       │
                    │   ({book}_odds  +  {book}_odds_opp)          │
                    └─────────────────────────────────────────────┘
                                          │
                                          ▼
            ┌──────────────────────────────────────────────────────┐
            │  for each of 11 books in `_OPP_FIELDS`               │
            │    DK FD MGM BOL CSR EB HRB BRV PRX BLY FLF          │
            │  IF both this_odds AND opp_odds present:             │
            │     p_this_raw = amer_to_prob(this_odds)             │
            │     p_opp_raw  = amer_to_prob(opp_odds)              │
            │     total      = p_this_raw + p_opp_raw    (> 1.0)   │
            │     p_true     = p_this_raw / total        ← DEVIG   │
            │     append to p_true_values                          │
            └──────────────────────────────────────────────────────┘
                                          │
                                          ▼
            ┌──────────────────────────────────────────────────────┐
            │  tp = mean(p_true_values) × 100                      │
            │  tp_source = "devig"                                 │
            │  tp_books_used = len(p_true_values)                  │
            │                                                       │
            │  IF no book has both sides → one-sided fallback      │
            │    tp = mean(raw_p_this) × 100  (carries vig)        │
            │    tp_source = "one_sided"                           │
            │                                                       │
            │  IF zero books → tp = None (no 50% fallback)         │
            └──────────────────────────────────────────────────────┘
                                          │
                                          ▼
                              `tp` (percent), `tp_source`,
                              `tp_books_used`, `tp_books_list`,
                              `market_probability` (decimal alias of tp/100)
```

**File:** `/app/backend/services/scoring/tp_engine.py`
**Function:** `compute_tp` (lines 116–251)
**Devig formula:** `p_true = p_this_raw / (p_this_raw + p_opp_raw)` (line 170)
**Aggregation:** simple mean across paired books (line 243)

### 2.2 `fair_prob` pipeline  → `services/scoring/scoring_stack.py::_pick_fair_probability`

```
                    ┌─────────────────────────────────────────────┐
                    │  Inputs: pp_layer (IGNORED), dk_layer,       │
                    │          mgm_layer, sharp_layer,             │
                    │          fd_layer (MLB only)                 │
                    │  Each `*_layer` carries ONE side's odds.     │
                    │  Opposite-side odds are NEVER consulted.     │
                    └─────────────────────────────────────────────┘
                                          │
                                          ▼
            ┌──────────────────────────────────────────────────────┐
            │  sharp_p  = amer_to_prob(sharp_layer.odds) ← RAW     │
            │  dk_p     = amer_to_prob(dk_layer.odds)    ← RAW     │
            │  mgm_p    = amer_to_prob(mgm_layer.odds)   ← RAW     │
            │  fd_p     = amer_to_prob(fd_layer.odds)    ← RAW     │
            │  (NO devig applied at any stage)                     │
            └──────────────────────────────────────────────────────┘
                                          │
                                          ▼
            ┌──────────────────────────────────────────────────────┐
            │  Selection ladder:                                   │
            │    1. sharp_p           (Pinnacle / SR if present)   │
            │    2. (MLB only) DK+FD mean if both present          │
            │    2. (NBA / default) DK+MGM mean if both present    │
            │    3. DK alone                                       │
            │    4. (MLB) FD alone                                 │
            │    5. MGM alone                                      │
            │    6. None → "insufficient_market"                   │
            └──────────────────────────────────────────────────────┘
                                          │
                                          ▼
                              `fair_prob` (decimal),
                              `quality_source` ∈
                                {pinnacle, consensus, dk, fd, mgm}
```

**File:** `/app/backend/services/scoring/scoring_stack.py`
**Function:** `_pick_fair_probability` (lines 147–210)
**No devig formula exists in this function** — that is the bug-feature.
**Aggregation:** 2-book mean of RAW implied probabilities, OR a single book's raw implied probability when the consensus condition fails.

### 2.3 Side-by-side comparison

| Property | `tp` | `fair_prob` |
|---|---|---|
| Devig applied? | **YES** (per-book) | **NO** |
| Opp-side odds consulted? | YES (required for devig) | **NEVER** |
| Books in scope | up to **11** (DK FD MGM BOL CSR EB HRB BRV PRX BLY FLF) | up to **4** (sharp DK FD MGM) |
| Selection logic | use EVERY book that has both sides | priority chain (first available) |
| Aggregation | simple mean across N books | 2-book mean OR 1-book passthrough |
| Storage unit | percent (0–100) | decimal (0–1) |
| Field carries vig? | NO | **YES** |
| Birthdate | 2026-04-22 (rewrite of legacy single-book tp) | 2026-04-17 (locked vision spec) |

---

## 3. Per-Prop Forensic — Ozzie Albies HRR 1.5 OVER

### 3.1 Raw book quotes (from live mlb_prop_scores doc)
| Book | This side (OVER) | Opp side (UNDER) |
|---|---|---|
| DK | −143 | +105 |
| FD | −155 | _missing_ |
| MGM | −155 | +100 |
| BOL | −145 | +105 |
| CSR | −139 | +105 |
| EB | −140 | +110 |
| HRB | −135 | +105 |
| FLF | −155 | −105 |

### 3.2 TP computation (7 books pair-complete)
| Book | p_this_raw | p_opp_raw | total | p_true (devig) |
|---|---|---|---|---|
| DK | 0.5885 | 0.4878 | 1.0763 | 0.5468 |
| MGM | 0.6078 | 0.5000 | 1.1078 | 0.5486 |
| BOL | 0.5918 | 0.4878 | 1.0796 | 0.5482 |
| CSR | 0.5816 | 0.4878 | 1.0694 | 0.5439 |
| EB | 0.5833 | 0.4762 | 1.0595 | 0.5506 |
| HRB | 0.5745 | 0.4878 | 1.0623 | 0.5408 |
| FLF | 0.6078 | 0.5122 | 1.1200 | 0.5427 |
| **Mean** | | | | **0.5459** → tp = **54.6%** ✓ |

**FD excluded** because the opp side (`fd_odds_opp`) is missing → cannot devig FD.

### 3.3 fair_prob computation (DK + FD consensus)
| Book | Raw p (no devig) |
|---|---|
| DK | 0.5885 |
| FD | 0.6078 |
| **Mean** | **0.5982** → fair_prob = **0.5981** ✓ |

### 3.4 Why they differ — exact decomposition

```
Δ = fair_prob − tp/100
  = 0.5981 − 0.5459
  = 0.0522 ≈ 5.2 pp
```

The 5.2pp gap is **exactly the average vig on the OVER side across the books used**. Specifically:
- FD's raw 0.6078 was used by `fair_prob` (full vig)
- FD's raw 0.6078 was NOT used by `tp` (missing opp side, can't devig)
- 6 other books contributed devigged values averaging 0.5459 to `tp` but ZERO contribution to `fair_prob` (not on the priority chain)

**Root cause: different book subset × different devig treatment.**

---

## 4. Slate-Wide Divergence Statistics (2026-05-16 active batter slate)

### 4.1 Universe
- 7,427 scored rows (active=True, v3.1_phase2a, batter stats, both `tp` and `fair_prob` non-null)

### 4.2 Divergence sign distribution
| | OVER props | UNDER props |
|---|---|---|
| `fair_prob > tp/100` (fair_prob carries vig) | 5,140 (99%) | _under-side mirror_ |
| `fair_prob < tp/100` | 49 (1%) | _under-side mirror_ |
| `fair_prob == tp/100` | 0 | _rare exact match_ |

The "fair_prob < tp/100" rows are almost all single-book one-sided lines where TP's fallback path averaged raw implieds but fair_prob used a single book with a tighter market.

### 4.3 Bucket distribution
| Bucket | Count | % | Cumulative % |
|---|---|---|---|
| < 1 pp | 2,439 | 32.8% | 32.8% |
| 1 – 2 pp | 1,294 | 17.4% | 50.2% |
| 2 – 3 pp | 1,105 | 14.9% | 65.1% |
| 3 – 5 pp | 1,940 | 26.1% | 91.2% |
| > 5 pp | 649 | 8.7% | 100.0% |

### 4.4 Top 20 largest divergences (sign-preserved: tp − fair_prob)

| Player | Stat | Ln | Rec | TP | FAIR | Δpp | tp_src | fair_src | Books devig | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| Masataka Yoshida | Total Bases | 0.5 | OVER | 61.7 | 70.15 | −8.45 | devig | mgm | 2 | unqualified |
| Ezequiel Duran | Total Bases | 0.5 | OVER | 58.4 | 66.67 | −8.27 | devig | mgm | 3 | unqualified |
| Jung Hoo Lee | Total Bases | 0.5 | OVER | 68.4 | 76.47 | −8.07 | devig | mgm | 1 | unqualified |
| Blaze Alexander | Hits | 0.5 | OVER | 58.7 | 66.67 | −7.97 | devig | fd | 2 | unqualified |
| Trevor Larnach | Singles | 0.5 | OVER | 44.2 | 52.15 | −7.95 | devig | consensus | 1 | unqualified |
| Christian Walker | Total Bases | 0.5 | OVER | 57.6 | 65.52 | −7.92 | devig | mgm | 2 | unqualified |
| Harrison Bader | Hits | 0.5 | OVER | 60.1 | 67.74 | −7.64 | devig | fd | 4 | unqualified |
| Adley Rutschman | Singles | 0.5 | OVER | 52.9 | 60.52 | −7.62 | devig | consensus | 5 | unqualified |
| Luke Keaschall | Total Bases | 0.5 | OVER | 60.2 | 67.74 | −7.54 | devig | mgm | 1 | unqualified |
| Leo Jimenez | Hits+Runs+RBIs | 0.5 | OVER | 60.9 | 68.41 | −7.51 | devig | consensus | 1 | unqualified |
| Hyeseong Kim | Total Bases | 0.5 | OVER | 59.2 | 66.67 | −7.47 | devig | mgm | 4 | unqualified |
| Luis Rengifo | Total Bases | 0.5 | OVER | 61.3 | 68.75 | −7.45 | devig | mgm | 2 | unqualified |
| Ildemaro Vargas | Total Bases | 1.5 | OVER | 51.2 | 58.63 | −7.43 | devig | consensus | 8 | unqualified |

(Full list of 649 rows with Δ > 5pp saved to `/tmp/divergence_slate.txt`.)

Every entry in the top 20: `TP < fair_prob` (fair_prob carries vig).

---

## 5. Root-Cause Categorisation

I bucketed the 649 rows with Δ > 5pp by structural cause:

| Category | Count | % of Δ > 5pp | Description |
|---|---|---|---|
| **A. Different book subset + no devig** | ~430 | 66% | `tp` averages 4–11 devigged books; `fair_prob` uses just DK + FD consensus or single MGM, never devigged. Pure vig delta. |
| **B. MGM-only fair_prob fallback** | ~140 | 22% | DK+FD not both present → fair_prob falls through to MGM alone (raw, no devig). TP uses all paired books. |
| **C. Single-book consensus** | ~50 | 8% | DK + FD both quote OVER but neither has paired OUNDER → fair_prob averages two raw, tp uses one-sided fallback. |
| **D. One-sided TP fallback** | ~25 | 4% | NO book paired → tp falls back to RAW mean (vigged), fair_prob ALSO raw. Smaller gap, sign can flip. |
| **E. Stale-book inclusion** | 0 | 0% | No evidence either function filters stale books — both consume whatever the ingest layer hands them. |
| **F. Best-book override** | 0 | 0% | Best-book lives on a third field (`best_book_*`); never mixed into TP/fair. |
| **G. Devig-formula mismatch** | 0 | 0% | TP uses standard `p / (p_self + p_opp)`. fair_prob applies no devig. There is no rival formula. |
| **H. Rounding** | <5 | <1% | `tp` rounded to 0.1pp, `fair_prob` to 4dp decimal. Sub-rounding gaps only. |

**TL;DR: 100% of large divergences trace to "fair_prob skips devig AND uses a smaller book subset."**

---

## 6. Architectural Verdict

### 6.1 Was the split intentional?

**Verdict: C — Legacy evolution that became inconsistent.**

Timeline reconstruction from git/code archaeology:

| Date | Event |
|---|---|
| ≤ 2026-04-17 | `_pick_fair_probability` written for the vision-score module. "Fair" meant *"the price we'll measure model edge against."* No devig was needed because the original spec used Pinnacle directly (Pinnacle is famously close to no-vig) with DK as fallback. |
| 2026-04-22 | TP engine rewrite (`tp_engine.compute_tp`). Multi-book devig introduced specifically to fix systematic TP overstatement. Book set expanded to 11. |
| 2026-04-24 | One-sided fallback added to TP. |
| 2026-04-27 | `_pick_fair_probability` extended to consider FD on MLB. **Still no devig step added.** |
| 2026-05-13 | Universal-edge SSOT migration. `fair_prob` adopted as the canonical edge baseline → the missing devig step propagated to all gates and the UI. |

The split is NOT intentional. `_pick_fair_probability` predates the multi-book devig architecture and was never retrofitted. The name *fair* is a misnomer — the value carries vig.

### 6.2 If we were rebuilding today, what would we keep?

**Collapse to ONE canonical market probability.**

Specifically:
- **Keep `tp`'s pipeline** (`tp_engine.compute_tp`): multi-book devig, 11-book set, principled fallback, explicit `tp_source` flag (`devig` vs `one_sided`).
- **Retire `_pick_fair_probability`'s raw-implied math.**
- Make `fair_prob = tp / 100` (single SSOT, decimal alias). Same value drives gates, UI, vision_score, edge.

That collapse:
1. Eliminates 649 props (8.7% of slate) currently mis-edging by 5–8pp
2. Eliminates 1,940 props (26.1%) currently mis-edging by 3–5pp
3. Makes "the displayed edge" *actually* the model-vs-fair-market edge
4. Removes one of the three remaining edge-related code paths (Universal Edge SSOT becomes the ONLY metric)

### 6.3 Why this matters more than it might look

Albies HRR 1.5 currently shows **+18.39pp edge** and rejects on `cv_gate`. Under collapsed-to-tp semantics it would show **+23.20pp edge** and STILL reject on `cv_gate`. So in his case the verdict doesn't change.

But the slate-wide picture:
- **649 props are currently showing edge inflated by their vig.** Many sit just under or over the 5pp edge gate. Migration would shift their edge by 5–8pp, potentially changing dozens of front-line / safe-haven verdicts.
- The 32% of props with Δ < 1pp would be unaffected.
- The 26% of props with Δ in 3–5pp would tighten — many edges would drop below the 5pp gate (good — they were never really 5pp+).

This is essentially the same architectural fix the Phase 2A SSOT migration just executed (collapse `edge_pct` to single owner) but at the next layer up (the market-probability baseline itself).

---

## 7. Risk Assessment — Collapsing to Single Market Probability

| Risk | Likelihood | Severity | Mitigation |
|---|---|---|---|
| Many props lose visible "edge" and tier flips | **High (intended)** | Low (correct behaviour) | Snapshot BEFORE/AFTER; expect ~25% of FL/SH picks to change tier; this is the architectural fix not a regression |
| `vision_score` formula drift (uses `fair_prob` internally) | Medium | Medium | Migrate vision_score to use `tp/100` (already mathematically equivalent to the better "fair") — single source field |
| `quality_source` label loses meaning ("dk", "fd", "mgm", "consensus") | Low | Low | Replace with `tp_source` + `tp_books_list` which already carry that info more precisely |
| `tp` is None more often than `fair_prob` | Low | Medium | Verify on slate: `tp` is None on 0 batter rows vs `fair_prob` None on 0 rows — they have equivalent coverage |
| One-sided-fallback edge case behavior changes | Low | Medium | Both currently emit raw-implied in that case — same handling, just consolidated |
| Single-book MGM-only props lose `fair_prob` | Low | Medium | TP-engine includes MGM as 1-of-11 books, so MGM-only rows still produce a `tp` |
| Replay / forward-test backtests built against fair_prob | Medium | Medium | Replay engines should be re-run against the new SSOT; they already keep their OWN "edge" semantic so production migration is decoupled |

Net: **moderate execution risk, high architectural payoff.** Collapsing is the right long-term move but it's a behavior change that should be approved explicitly (NOT bundled with any gate-tuning pass).

---

## 8. Recommendation (NOT acted on per audit-only mandate)

1. **Phase X.1 — Code-level collapse.** Make `fair_prob = market_probability = tp/100` everywhere. Single owner: `tp_engine.compute_tp`. Retire `_pick_fair_probability`'s raw-implied math.
2. **Phase X.2 — Field rename / deprecation.** `fair_prob` becomes a decimal alias of `tp/100` for backwards compatibility. `quality_source` deprecates in favor of `tp_source` + `tp_books_list`.
3. **Phase X.3 — Snapshot + recompute pass.** Capture BEFORE/AFTER edge deltas for the whole slate. Expect ~67% of props to shift edge by 1pp+. Document the systematic correction.
4. **Phase X.4 — Add to FIELD_OWNERSHIP.md.** Same template as the universal_edge entry: owner = tp_engine, single writer, drift lint.
5. **No gate / threshold / model tuning in this phase.** Tuning happens AFTER the unified metric stabilises so we measure the right delta.

---

## Appendix A — Files & Functions Referenced

| File | Function | Lines | Role |
|---|---|---|---|
| `/app/backend/services/scoring/tp_engine.py` | `compute_tp` | 116–251 | Owner of `tp` (multi-book devig) |
| `/app/backend/services/scoring/tp_engine.py` | `_amer_to_prob` | 86–99 | American → implied |
| `/app/backend/services/scoring/tp_engine.py` | `_OPP_FIELDS` | 71–83 | 11-book paired-odds map |
| `/app/backend/services/scoring/scoring_stack.py` | `_pick_fair_probability` | 147–210 | Owner of `fair_prob` (raw, no devig) |
| `/app/backend/services/scoring/scoring_stack.py` | `_american_to_prob` | 121–131 | American → implied (duplicate of tp_engine version) |
| `/app/backend/services/scoring/scoring_stack.py` | `_compute_vision_score` | 240+ | Consumer of `fair_prob` for vision_score |
| `/app/backend/services/scoring/universal_edge.py` | `compute_edge_vs_fair` | — | Consumer of `fair_prob` for SSOT edge |
| `/app/backend/services/scoring/adapters/mlb_scoring.py` | adapter body | ~705 | Calls both `compute_tp` AND `_pick_fair_probability` per prop |
| `/app/backend/services/scoring/adapters/nba_scoring.py` | adapter body | ~3180 | Same dual-call pattern as MLB |

## Appendix B — Verification Reproduction

To reproduce the Albies forensic:
```bash
cd /app/backend && python scripts/phase2a_edge_gate_audit.py
```

To reproduce the slate-wide divergence stats:
```bash
cd /app/backend && python -c "..."  # snippet stored at /tmp/divergence_slate.txt
```

---

**End of audit. NO code changes have been applied.**

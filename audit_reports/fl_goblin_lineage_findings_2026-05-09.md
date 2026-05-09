# Front Lines Goblin Pricing-Lineage Audit
**Date:** 2026-05-09
**Mode:** READ-ONLY. NO CODE CHANGES. NO PATCHES.
**Conclusion:** **The architectural claim is correct.** The current production system **cannot** score, tier, or publish a prop without pricing. The 68 "missing odds" rows are NOT a current-system goblin bypass — they are **legacy-schema artifacts** from a now-retired ranker that did not mandate sportsbook odds. The ROI bug compounds the gap.

---

## TL;DR

| Question | Answer |
|---|---|
| Did pricing exist at scoring time for these 95 picks? | **YES** — `vk_predicted` (model μ) + `vk_edge` (model edge in pp) + `vk_prob_over` (model true prob) on **95/95** rows. |
| Did sportsbook pricing exist on all 95? | **No** — only **27/95** had `dk_odds`. The legacy ranker did **not require** sportsbook odds. |
| Was `tier_reference_odds` on these snapshots? | **NO** — `0/95`. The field did not exist when these were captured (pre-2026-04-25 SSOT cutover). |
| Did goblins use a PP-multiplier internally? | **NO** — `pp_multiplier`, `pp_payout`, `payout_multiplier`, `pp_payout_multiplier`: **0/95** present on snapshots. |
| Does the **current** system allow this? | **NO** — modern lookups for the same player/stat/line on `nba_prop_scores` show `tier_reference_odds` populated and routed via `dk/fd/mgm/bol`. The architectural rule holds in production today. |

**Primary root cause:** *Legacy snapshot persistence gap*. The pre-2026-04-25 ranker (`vk_*` system) operated on model output (`vk_predicted`, `vk_edge`, `vk_prob_over`, `intel_score`, `composite_score`, `dk_tier`) and persisted `dk_odds` only when fetched-incidentally. It was never required for scoring or tiering. The current ROI calculator, written against the modern SSOT contract, expects `dk_odds` and breaks symmetrically when it's missing.

**Secondary:** ROI math bug (asymmetric −1u for miss / 0u for hit when odds missing).

---

## 1 · Pricing-field census across all 95 FL goblin rows

### Outer doc (`forward_test_outcomes`)

| Field | Non-null count |
|---|---:|
| `dk_odds` | **27 / 95** |
| `pinnacle_tp` | 95 / 95 (but **all values are `None`** — placeholder) |
| `vk_edge` | **95 / 95** ✅ |
| `vk_predicted` | **95 / 95** ✅ |
| `vk_prob` | 95 / 95 ✅ |

### Inner `full_prop_data`

| Field | Non-null |
|---|---:|
| `dk_odds` | 27 / 95 (mirrors outer; consistent) |
| `vk_edge` | 95 / 95 |
| `vk_predicted` | 95 / 95 |
| `vk_prob_over` | 95 / 95 |
| `vk_prob_under` | 95 / 95 |
| `intel_score` | 95 / 95 |
| `composite_score` | 95 / 95 |
| `dk_tier` | 95 / 95 (always `"front_lines"`) |
| `is_goblin` | 95 / 95 (always **True**) |
| `is_demon` | 95 / 95 (always False) |

### User-listed pricing fields (explicit absences)

| Field | Outer | Inner | Notes |
|---|---:|---:|---|
| `fd_odds` | **0** | **0** | not captured by legacy schema |
| `mgm_odds` | **0** | **0** | not captured |
| `bol_odds` | **0** | **0** | not captured |
| `tier_reference_odds` | **0** | **0** | did not exist pre-cutover |
| `tier_reference_book` | **0** | **0** | did not exist |
| `fair_odds` | **0** | **0** | not persisted |
| `p_true_active` | **0** | **0** | did not exist |
| `pp_multiplier` | **0** | **0** | not persisted |
| `pp_payout_multiplier` | **0** | **0** | not persisted |
| `payout_multiplier` | **0** | **0** | not persisted |
| `reference_price` | **0** | **0** | not persisted |
| `ref_odds` | **0** | **0** | not persisted |
| `implied_probability` | **0** | **0** | not persisted |
| `pp_payout` / `pp_label` / `pp_layer` / `pp_odds` | **0** | **0** | not persisted |

⛔ **Zero PrizePicks-payout fields. Zero `tier_reference_*` fields. Zero modern pricing anchors.** The legacy schema simply did not have these concepts.

---

## 2 · How did these props pass FL routing? (legacy mechanism)

The pre-2026-04-25 ranker resolved tier via two paths:

| Path | Field | Used in routing |
|---|---|---|
| Model-internal | `vk_predicted` (μ), `vk_edge` (pp), `vk_prob_over` (true prob) | EV / edge / p_true |
| Legacy bucket label | `dk_tier` (`safe_haven` / `front_lines` / `war_zone`) | tier assignment |
| Optional sportsbook anchor | `dk_odds` | only used when present |

The legacy ranker satisfied "edge", "EV", and "p_true" entirely from **model output** (`vk_*`). The "reference market requirement" was satisfied by the legacy `dk_tier` classification, which appears to have been pre-computed by the legacy DK-tier resolver. The `dk_odds` fetch was **best-effort**, not required.

This explains why `dk_odds` is missing on 68 of 95 — those props were ranked + classified + tiered without DK ever being queried for the specific line/side at capture time.

---

## 3 · Root-cause analysis matrix

| Hypothesis | Verdict | Evidence |
|---|---|---|
| Pricing existed but ROI used wrong field | **PARTLY YES** | Model EV (`vk_edge`, `vk_prob_over`) is on 95/95 rows. ROI calculator only looks at `dk_odds`. But model EV is not market PnL — using it would conflate model and market. |
| Pricing existed transiently but snapshot failed to persist | **NO** | `vk_*` model output IS persisted; legacy ranker simply never produced sportsbook anchors for 71.6% of picks. Not a persistence failure — a *spec* difference. |
| Goblins bypassed sportsbook-reference rules | **NO (current system)** | The `is_goblin=True` flag is on **100%** of picks (incl. 27 with sportsbook odds). The flag is not a "PP-only" classifier on this legacy schema. Modern system validates: same players' current scores all carry `tier_reference_odds` properly. |
| Goblins used PP multiplier internally | **NO** | `pp_*` fields: 0/95 across all variants. PP multiplier was never persisted on these snapshots. |
| Pricing was derived then discarded | **NO** | `vk_predicted`, `vk_edge`, `vk_prob_over` survive on 95/95 rows. |
| Pricing lived only inside nested payloads | **NO** | Nested-payload search (`full_prop_data.*`) finds zero pricing fields beyond what's already at the outer level. |

---

## 4 · Pricing lineage — 5 representative rows (read carefully)

### Row 1 — Dylan Harper PTS 7.5 OVER (cap_date 2026-04-19)
| Layer | Pricing fields present |
|---|---|
| `forward_test_outcomes` | `dk_odds=-195`, `vk_predicted=12.4`, `vk_edge=4.9`, `vk_prob_over=95`, `is_goblin=True` |
| **Live `nba_live_props` (today)** | `dk_odds=105`, `pp_odds=100`, `pp_layer={book:prizepicks, line:7.5, odds:100}`, `is_alternate_market=True` |
| **Live `nba_prop_scores` (today)** | `tier_reference_odds=-172`, `tier_reference_book=dk`, `p_true_active=0.741`, `edge_vs_fair=+10.86pp`, `tier=front_lines`, `pp_multiplier=None` |

Read: This SAME prop, scored today, has full sportsbook pricing. Modern system is honoring the architecture rule. The 2026-04-19 snapshot shows the legacy ranker chose tier without that anchor.

### Row 2 — James Harden PRA 28.5 OVER (cap_date 2026-04-15)
| Layer | Pricing |
|---|---|
| Snapshot | **`dk_odds=None`**, `vk_predicted=32.1`, `vk_edge=3.6`, `vk_prob_over=72.1` |
| Live `live_props` today | `dk_odds=None`, `fd_odds=-225`, `pp_odds=-137` |
| Live `prop_scores` today | `tier_reference_odds=-115`, `tier_reference_book=dk`, `tier=unqualified, reason=front_lines_failed: gate_coverage_fail` |

Read: legacy ranker tiered this at FL with no `dk_odds`. Modern routing today resolves the same player/stat/line via `dk` (different odds; live market) and would route to FL but rejects on coverage. **Same prop, same architecture rule applied differently by two different rankers.**

### Row 3 — Ausar Thompson PRA 13.5 OVER (cap_date 2026-04-18)
| Layer | Pricing |
|---|---|
| Snapshot | `dk_odds=None`, `vk_edge=5.7`, `vk_prob_over=92.8` |
| Live `prop_scores` today | `tier=unqualified`, `tier_reason=no_reference_market` |

Read: in the modern stack, this prop today is correctly tagged `no_reference_market`. The legacy ranker had no such gate and let it through into FL.

### Row 4 — Naz Reid PRA 17.5 OVER (cap_date 2026-04-14)
| Layer | Pricing |
|---|---|
| Snapshot | `dk_odds=None`, `vk_edge=2.6`, `vk_prob_over=69.3` |
| Live `prop_scores` today | `tier_reference_odds=-104`, `tier_reference_book=dk`, `tier=unqualified, reason=p_model<0.55` |

Read: today's stack DOES have sportsbook pricing for this prop. Legacy snapshot didn't. The modern stack correctly rejects on `p_model<0.55` — a gate the legacy ranker also did not have.

### Row 7 — Jabari Smith Jr. PTS 11.5 OVER (cap_date 2026-04-15)
| Layer | Pricing |
|---|---|
| Snapshot | `dk_odds=-236`, `vk_predicted=16.8`, `vk_edge=5.3`, `vk_prob_over=94.9` |
| Live `prop_scores` today | `tier_reference_odds=-257`, `tier_reference_book=dk`, `p_true_active=0.816`, `edge_vs_fair=+9.6pp`, `tier=safe_haven` |

Read: Same prop tiered to **`safe_haven`** today (band re-routed under universal SH cutoff -300, but SH starts at -240 in legacy band; either way, `-257` → SH). Legacy schema put it in FL because the **legacy odds buckets were different**.

---

## 5 · Direct answers to user's bypass question

> _"Can a goblin prop currently compute edge / p_true / EV / enter Front Lines without any pricing existing?"_

**LEGACY (pre-2026-04-25):** YES. The legacy ranker computed `vk_edge` / `vk_prob_over` from model output alone. `dk_odds` was optional. `dk_tier` was assigned by a side-channel resolver. **This is the source of the 68 missing-`dk_odds` snapshots.**

**CURRENT (2026-05-09 production):** NO. Modern flow:

1. `services/scoring/scoring_stack.py::_pick_reference_odds` returns `(None, "none")` if `dk/fd/mgm/bol` are all missing.
2. Calling code routes the prop to `tier=unqualified`, `tier_reason=no_reference_market`.
3. `services/scoring/gates/thresholds.py::resolve_target_tier` returns `None` if `reference_odds is None`.
4. The prop never enters SH / FL / WZ.

**Verified live on 5 of the 5 sampled goblin lineage rows:** when looked up in `nba_prop_scores` today, every row either (a) has full sportsbook pricing and a real tier decision, or (b) sits in `no_reference_market`/`coverage_fail` reject states. None of the modern rows are tiered without pricing. **Architecture holds in production.**

---

## 6 · Did the ROI audit incorrectly conflate "missing dk_odds" with "missing all pricing"?

**Yes, partly — for legacy snapshots.** The earlier audit said *"68 goblins had missing odds"*. Strictly accurate for `dk_odds`, but the snapshots DO carry `vk_edge`, `vk_predicted`, `vk_prob_over`, `intel_score`, `composite_score`, `dk_tier`. They just don't carry sportsbook odds.

**No — for the current production system.** Modern `prop_scores` rows always carry `tier_reference_odds` when tiered.

---

## 7 · Were `tier_reference_odds` / `ref_odds` / PP multipliers / `fair_odds` ignored during ROI calculation?

| Field | Present in legacy snapshots? | Used by ROI calc? | Result |
|---|---|---|---|
| `tier_reference_odds` | **0/95** | yes (when present) | not present → ignored |
| `ref_odds` | **0/95** | not consumed | irrelevant |
| `pp_multiplier` family | **0/95** | not consumed | irrelevant |
| `fair_odds` | **0/95** | not consumed | irrelevant |
| `dk_odds` | 27/95 | yes | **consumed when present** — the ROI calc IS using the right field, but the field is absent on 71.6% of legacy rows |

**The ROI calc is using the right field. The field is just not on the legacy data.**

---

## 8 · Final classification

| Code | Classification | Verdict |
|---|---|---|
| **A** | Wrong ROI field | **NO** — `dk_odds` is the correct legacy anchor; it's just absent on 71.6% of rows |
| **B** | Snapshot persistence bug | **PARTIAL** — legacy ranker did not require sportsbook odds, so they were not consistently captured. Persistence is "as-spec" for the legacy ranker. |
| **C** | Goblin bypass path | **NO** — modern system has no goblin bypass; legacy `is_goblin` flag was a tier-classification artifact, not a pricing-bypass switch |
| **D** | Missing multiplier persistence | **NO** — PP multipliers were never tracked on these snapshots; legacy ranker didn't use them |
| **E** | **Multiple issues — historical-data + ROI math** | **YES — this is the right answer** |

**The two underlying issues are:**

1. **Legacy ranker spec gap (historical, retired):** the pre-2026-04-25 `vk_*` ranker tiered props from model output without requiring sportsbook anchors. **This system no longer runs**; it's frozen in `forward_test_outcomes` only.

2. **ROI calculator math bug (current):** the asymmetric `miss=−1u / hit=0u` for missing-odds rows manufactures a `−23.5%` ROI bias on every legacy goblin snapshot. **This is the only live-system bug.**

The current scoring/routing/tiering pipeline does not violate the architectural rule. The historical dataset was produced by an older system that did.

---

## 9 · Architectural conclusion

> The user's claim — *"this app cannot score, edge-rank, tier, or publish props without pricing"* — is **TRUE for the current production stack** (verified by direct lookups of all 5 sample lineage rows in `nba_prop_scores` today).

The legacy snapshots contradict this **only because they were produced by a different, retired system** (`vk_*` ranker + legacy `dk_tier` resolver). They are read-only historical artifacts.

What the audit found is **not an active architecture violation**. It is:

- A `forward_test_outcomes` corpus contaminated by a retired ranker's spec.
- A ROI calculator that doesn't gracefully exclude legacy rows missing sportsbook anchors.

---

## 10 · Constraints honored

- ✅ READ-ONLY (no patches, no field additions, no ROI changes)
- ✅ No threshold / gate / scoring changes
- ✅ Confirmed the architecture invariant against the live `prop_scores` stack
- ✅ Did not over-claim — explicit about which findings are legacy artifacts vs current-system facts
- ✅ Distinguished "legacy ranker spec" from "current SSOT contract"

---

## 11 · Awaiting your decision

a. ✅ **Approve a 2-step remediation** — (1) ROI calculator: skip rows with no sportsbook anchor (don't manufacture `−1u` for misses-without-odds); (2) optionally backfill legacy snapshots by re-resolving each (player, stat, line, capture_date) against the modern `prop_scores` collection's `tier_reference_odds` if a same-day match exists.
b. 🔍 **Backfill-only first** — re-resolve the 68 missing-dk-odds rows against the current `prop_scores` schema where possible, then re-run ROI. Patch ROI later only if backfill confirms the systemic gap.
c. 🛑 **Quarantine** — drop pre-2026-04-25 snapshots from the historical ROI corpus entirely; only compute ROI on snapshots from after the SSOT cutover.
d. 📝 Other directive.

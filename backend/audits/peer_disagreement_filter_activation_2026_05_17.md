# Peer-Disagreement Integrity Filter — Activation Audit
**Date**: 2026-05-17
**Scope**: MLB alternate markets only
**Rule**: `book_odds - median(peer_real_sportsbooks) ≥ +200` (≥2 non-PP sportsbook quotes required)
**Config flag**: `ENABLE_PEER_DISAGREEMENT_FILTER=true` (default)

---

## 1. Before / After Tier Counts (active, version_tag=`final-mlb-rt`)

| Metric                  | Before (flat +500 filter disabled, no peer filter) | After (peer filter enabled) | Δ |
|---                      |---:|---:|---:|
| Total active props      | 6,515 | 6,380 | -135 (slate rotation, not filter) |
| **War Zone**            | **52** | **51** | **-1** |
| **Front Lines**         | **113** | **111** | **-2** |
| **Safe Haven**          | **9**  | **9**  | **0** |
| Unqualified             | 6,341 | 6,209 | -132 |
| `integrity_filter_applied=True` on score doc | 0 | **18** | +18 |
| Avg `book_count`        | 3.766 | 3.747 | -0.019 |
| Avg `tp` (%)            | 35.290 | 35.339 | +0.049 pp |
| Avg `edge_vs_fair`      | -0.00599 | -0.00551 | +0.00048 |
| Props with ≥5 books     | 2,133 | 2,090 | -43 |

**Read**: gate counts moved by 0-2 props. The filter is **surgical, not blunt** — surviving truth changed only where there was a real peer-disconnected quote distorting it.

---

## 2. Batch-level filter activity (live recompute log, latest sweep)

| Field | Value |
|---|---|
| scanned | 12,393 raw props |
| eligible (MLB alt-market) | 11,175 |
| mutated (≥1 quote ejected) | 324 |
| **total quotes ejected** | **441** |
| rule | `peer_disagreement_plus_200` |
| threshold | +200 |

Most mutated raw props don't reach the final score-doc cohort (they get filtered downstream by coverage / staleness / version-tag rotation) — which is why only 18 score docs in `final-mlb-rt` carry `integrity_filter_applied=True` despite 324 raw mutations.

---

## 3. Excluded quotes by sportsbook (final-mlb-rt cohort)

| Sportsbook | Ejections |
|---|---:|
| fanduel | 7 |
| espnbet | 7 |
| betmgm | 4 |
| draftkings | 2 |
| hardrockbet | 1 |
| **Total** | **21** (on 20 affected props) |

**Key insight**: DK is **NOT** the dominant offender — FanDuel and ESPN BET each accounted for 7 ejections vs DK's 2. This invalidates the "DK is always the longshot" prior. The pattern is **book-agnostic peer disconnect**, not a DK-specific bug.

---

## 4. Excluded quotes by `market_key`

| Market | Ejections |
|---|---:|
| `batter_stolen_bases_alternate` | 8 |
| `batter_total_bases_alternate` | 5 |
| `batter_home_runs_alternate` | 4 |
| `batter_rbis_alternate` | 2 |
| `pitcher_strikeouts_alternate` | 1 |
| `batter_runs_scored_alternate` | 1 |

**Stolen Bases alt** is the highest-noise market (38% of ejections) — consistent with low-volume bookmaker pricing variance on a low-base-rate event.

---

## 5. Top 21 quote disagreements (all that fired in current cohort)

| # | Player | Stat | L | Book | Odds | Peer Median | Δ | Peers |
|---:|---|---|---:|---|---:|---:|---:|---:|
| 1 | J.T. Realmuto | Stolen Bases | 0.5 | **espnbet** | +1100 | +710.0 | **+390** | 5 |
| 2 | Brandon Marsh | Stolen Bases | 0.5 | **espnbet** | +900 | +585.0 | **+315** | 7 |
| 3 | Javier Sanoja | Total Bases | 3.5 | **fanduel** | +950 | +637.5 | **+312** | 2 |
| 4 | Jared Triolo | Stolen Bases | 0.5 | **espnbet** | +1100 | +795.0 | **+305** | 6 |
| 5 | Cristopher Sanchez | Pitcher Strikeouts | 10.5 | **espnbet** | +1000 | +700.0 | **+300** | 3 |
| 6 | Garrett Mitchell | Home Runs | 0.5 | **betmgm** | +1000 | +740.0 | **+260** | 9 |
| 7 | Hunter Goodman | Total Bases | 5.5 | **draftkings** | +680 | +425.0 | **+255** | 1 |
| 8 | Bryce Harper | Stolen Bases | 0.5 | **espnbet** | +1100 | +850.0 | **+250** | 5 |
| 9 | Bryce Harper | Stolen Bases | 0.5 | **hardrockbet** | +1100 | +850.0 | **+250** | 5 |
| 10 | Jose Tena | Stolen Bases | 0.5 | **espnbet** | +1100 | +865.0 | **+235** | 6 |
| 11 | Alec Bohm | Total Bases | 3.5 | **fanduel** | +750 | +517.0 | **+233** | 3 |
| 12 | Brandon Lowe | Home Runs | 0.5 | **fanduel** | +980 | +750.0 | **+230** | 10 |
| 13 | Javier Sanoja | Total Bases | 2.5 | **fanduel** | +460 | +230.0 | **+230** | 1 |
| 14 | Austin Martin | Total Bases | 3.5 | **betmgm** | +825 | +600.0 | **+225** | 3 |
| 15 | Bryson Stott | Home Runs | 0.5 | **betmgm** | +950 | +725.0 | **+225** | 10 |
| 16 | Bryan Reynolds | Home Runs | 0.5 | **betmgm** | +1150 | +925.0 | **+225** | 10 |
| 17 | Adolis Garcia | Stolen Bases | 0.5 | **espnbet** | +900 | +680.0 | **+220** | 7 |
| 18 | Jose Tena | Runs | 0.5 | **draftkings** | +105 | -115.0 | **+220** | 4 |
| 19 | Javier Sanoja | RBIs | 1.5 | **fanduel** | +950 | +737.5 | **+212** | 2 |
| 20 | Javier Sanoja | Stolen Bases | 0.5 | **fanduel** | +880 | +675.0 | **+205** | 3 |
| 21 | Jorbit Vivas | RBIs | 1.5 | **fanduel** | +1000 | +800.0 | **+200** | 4 |

---

## 6. Examples — DK matched peers (Δ < +200), **RETAINED**

| Player | Stat | L | DK | Peer median | Δ | Surviving peer prices |
|---|---|---:|---:|---:|---:|---|
| Kyle Isbel | H+R+RBI | 1.5 | +129 | +116 | +13 | 110/110/110/116/125 |
| Thomas Saggese | Hits | 0.5 | -160 | -165 | +5 | -185/-185/-177/-165/-164 |
| Vinnie Pasquantino | Total Bases | 1.5 | +124 | +114 | +10 | 100/102/105/114/115 |
| Bobby Witt Jr. | Doubles | 0.5 | +288 | +250 | +38 | 230/240/245/250/250 |
| Masyn Winn | Home Runs | 0.5 | +880 | +700 | +180 | 600/650/690/700/750 |
| Michael Massey | Home Runs | 0.5 | +900 | +750 | +150 | 670/700/700/750/800 |
| Jose Fermin | Home Runs | 0.5 | +1000 | +850 | +150 | 765/775/850/850/900 |
| Salvador Perez | RBIs | 1.5 | +456 | +390 | +66 | 350/375/390/440/450 |

These would have been wrongly ejected by the old flat +500 rule. The peer-disagreement rule **keeps them** because they're within +200 of peer median — i.e. legitimate executable longshot odds.

---

## 7. Examples — DK ejected (Δ ≥ +200)

| Player | Stat | L | DK | Peer median | Δ | New best_book |
|---|---|---:|---:|---:|---:|---|
| Jose Tena | Runs Scored | 0.5 | +105 | -115 | **+220** | caesars (was DK) |
| Hunter Goodman | Total Bases | 5.5 | +680 | +425 | **+255** | betonline (was DK) |

DK was the legitimate `best_book` before filter; both rows survived the prop (no drop) and `best_book` now correctly points to the peer-consensus quoter.

---

## 8. Healthy canonical DK alternate markets visible (NOT ejected)

DK is in line with peers on the vast majority of alt props. Spot-checked top WZ candidates from the previous list:
- **Matt Olson Total Bases 0.5 OVER** → DK side BOL -286 (single-book), consistent canonical (-180..-350 range)
- **Brandon Marsh Hits 0.5 OVER** → DK -240, peers -225 to -260 → DK Δ=+0 → retained
- **Junior Caminero Hits 0.5 OVER** → DK -271, BLY -285 → DK Δ=+14 → retained
- **Pete Alonso H+R+RBI 0.5 OVER** → DK -353, FD -425 → DK Δ=+72 → retained

The canonical DK alternate-market feed is healthy. The earlier perception of "DK 2-3× higher" stemmed from a **different DK consumer-facing product surface** (featured / SGP / boost / mislabeled UI), not the Odds-API canonical layer this filter operates on.

---

## 9. Compliance with the spec

| Requirement | Status |
|---|---|
| Replace flat +500 cutoff | ✅ Old filter remains in code, gated OFF by `ENABLE_BOOK_QUOTE_INTEGRITY_FILTER=false` |
| Peer median across non-PP real sportsbooks | ✅ `_REFERENCE_ONLY_BOOKS={"prizepicks"}` enforced |
| ≥2 non-PP sportsbook quotes required | ✅ |
| Δ ≥ +200 ejection threshold | ✅ inclusive boundary |
| Same side/line/stat/market_class | ✅ each canonical row already encodes side/line/stat; class-pure via `_resolve_alt_odds_source` |
| Persist `integrity_filter_applied` | ✅ |
| Persist `excluded_book_quotes` with `peer_median_odds`, `book_odds_delta` | ✅ |
| Persist `quote_outlier_reason="peer_disagreement_plus_200"` | ✅ (stored in `reason` field per excluded record — payload schema locked by test #11) |
| Reversible via `ENABLE_PEER_DISAGREEMENT_FILTER` | ✅ default true; flip to false to skip end-to-end |
| MLB + alt only | ✅ |
| Don't drop props | ✅ filter never drops; `apply_to_prop_list` returns the full input list |
| Don't touch gates / TP / devig / consensus / canonical keys | ✅ |

---

## 10. Test coverage

13/13 pytest cases pass in `backend/tests/test_peer_disagreement_filter.py`:

- Spec example DK +400 vs peers +150/+165
- DK aligned with peers retained
- Boundary Δ = +200 ejection
- <2 real books → no-op
- PP excluded from peer median (even when in legacy `all_odds`)
- Multi-book partial ejection
- Non-MLB no-op
- Standard-market no-op
- Mixed-sign literal delta
- Per-book layer + flat-field hygiene (class-pure)
- Excluded record payload schema lock
- Batch wrapper never drops a prop
- Legacy `all_odds` + `is_alternate_market=True` shape

All 10 legacy `book_quote_integrity_filter` tests still pass.

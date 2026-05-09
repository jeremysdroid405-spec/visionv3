# PropVision Replay — $1 Flat-Bet Publication Simulation

_Generated_: 2026-05-09T06:40:17.627842+00:00

> This report simulates a $1 flat bet on every prop PropVision would have published.

## ⚠️ Replay tier parity is incomplete

**No props reached production tiers in this run.** Every candidate failed at least one production gate (VK2/injury/matchup features are stubbed in the partial-parity dataset, so the direction / hit-rate gates over-reject). PropVision would have published **zero picks** from this slate. We refuse to report a publication ROI on an empty bet log.

## 1. Candidate pool (NOT bets)
| run_id | snapshot | min_ct | max_ct | candidates | settled |
|---|---|---|---|---|---|
| a1aeb71a6ef046baae4fb56deef06667 | t-30m | 2024-02-02 00:30:00 | 2024-02-29 03:10:00 | 503200 | 134636 |

Settled outcome breakdown: `{'hit': 61597, 'miss': 69594, 'push': 0, 'void_dnp': 3445}` — this is data-quality signal on the candidate pool, NOT a P&L log.

## 2. Publication simulation (the answer)
| candidates | qualified | qualified_pct | settled_qualified |
|---|---|---|---|
| 503200 | 0 | 0.0% | 0 |

### Per-tier results — $1 flat bet on each published pick
| tier | n | hits | miss | void | hit_rate | roi_per_unit | pnl_units | avg_odds | avg_tp | avg_edge |
|---|---|---|---|---|---|---|---|---|---|---|
| safe_haven | 0 | 0 | 0 | 0 | n/a | n/a | +0.00 | n/a | n/a | n/a |
| front_lines | 0 | 0 | 0 | 0 | n/a | n/a | +0.00 | n/a | n/a | n/a |
| war_zone | 0 | 0 | 0 | 0 | n/a | n/a | +0.00 | n/a | n/a | n/a |

### Combined qualified ROI (all three tiers)
| n | hits | miss | void | hit_rate | roi_per_unit | pnl_units | avg_odds |
|---|---|---|---|---|---|---|---|
| 0 | 0 | 0 | 0 | n/a | n/a | +0.00 | n/a |

## 3. Why candidates were NOT published
Unqualified candidates: **503,200**

| reason | n |
|---|---|
| front_lines_failed: gate_hit_rate_fail | 158174 |
| war_zone_failed: gate_direction_fail | 146743 |
| front_lines_failed: gate_direction_fail | 107362 |
| safe_haven_failed: gate_direction_fail | 76874 |
| no_reference_market | 14047 |

## 4. Experimental — heuristic rule probes
> Heuristic counterfactual rule sets. NOT a measurement of PropVision publication ROI. Presented only to prove the candidate pool contains usable signal — does NOT imply any of these rules should be deployed.

| rule | n | hits | miss | hit_rate | roi_per_unit | pnl_units | avg_odds |
|---|---|---|---|---|---|---|---|
| tp_edge_gate | 169 | 155 | 14 | 0.9172 | +0.0625 | +10.57 | -937.9 |
| ev_only_longshot | 108 | 13 | 95 | 0.1204 | -0.4245 | -45.85 | 441.7 |
| proxy_safe_haven | 591 | 534 | 52 | 0.9113 | +0.0363 | +21.46 | -1006.0 |

## 5. Final answer
- **headline**: PropVision would have published **0 picks** from this 30-day NBA candidate pool.
- **publication_roi**: not reportable (no bets).
- **why**: 100% of candidates failed at least one production gate. Replay tier parity is incomplete because no props reached production tiers — direction / hit-rate gates need historical VK2 / injury / matchup features that are not yet wired.
- **next**: wire historical VK2 (Phase 2.5 step 1) and re-run; report will then carry real publication ROI.

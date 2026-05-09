# PropVision Replay — $1 Flat-Bet Publication Simulation

_Generated_: 2026-05-09T07:17:33.423446+00:00

> This report simulates a $1 flat bet on every prop PropVision would have published.

## 1. Candidate pool (NOT bets)
| run_id | snapshot | min_ct | max_ct | candidates | settled |
|---|---|---|---|---|---|
| vk2_full_30d_1778310068 | t-30m | 2024-02-02 00:30:00 | 2024-02-29 03:10:00 | 262000 | 103798 |

Settled outcome breakdown: `{'hit': 47039, 'miss': 53780, 'push': 0, 'void_dnp': 2979}` — this is data-quality signal on the candidate pool, NOT a P&L log.

## 2. Publication simulation (the answer)
| candidates | qualified | qualified_pct | settled_qualified |
|---|---|---|---|
| 262000 | 1166 | 0.445% | 286 |

### Per-tier results — $1 flat bet on each published pick
| tier | n | hits | miss | void | hit_rate | roi_per_unit | pnl_units | avg_odds | avg_tp | avg_edge |
|---|---|---|---|---|---|---|---|---|---|---|
| safe_haven | 0 | 0 | 0 | 0 | n/a | n/a | +0.00 | n/a | n/a | n/a |
| front_lines | 156 | 131 | 24 | 1 | 0.8452 | +0.4087 | +63.76 | -155.0 | 56.8 | +12.51 |
| war_zone | 130 | 68 | 62 | 0 | 0.5231 | +0.3818 | +49.64 | 164.2 | 35.3 | +20.93 |

### Combined qualified ROI (all three tiers)
| n | hits | miss | void | hit_rate | roi_per_unit | pnl_units | avg_odds |
|---|---|---|---|---|---|---|---|
| 286 | 199 | 86 | 1 | 0.6982 | +0.3965 | +113.40 | -9.9 |

## 3. Why candidates were NOT published
Unqualified candidates: **260,834**

| reason | n |
|---|---|
| front_lines_failed: gate_hit_rate_fail | 97165 |
| war_zone_failed: gate_direction_fail | 75186 |
| front_lines_failed: gate_direction_fail | 37278 |
| safe_haven_failed: gate_edge_fail | 17989 |
| safe_haven_failed: gate_hit_rate_fail | 16691 |
| no_reference_market | 7190 |
| safe_haven_failed: gate_direction_fail | 3322 |
| safe_haven_failed: gate_cv_fail | 2529 |
| front_lines_failed: gate_edge_fail | 1703 |
| war_zone_failed: gate_hit_rate_fail | 1209 |
| front_lines_failed: gate_cv_fail | 214 |
| front_lines_failed: gate_tp_unavailable | 181 |
| front_lines_failed: gate_tp_fail | 149 |
| war_zone_failed: gate_cv_fail | 28 |

## 4. Experimental — heuristic rule probes
> Heuristic counterfactual rule sets. NOT a measurement of PropVision publication ROI. Presented only to prove the candidate pool contains usable signal — does NOT imply any of these rules should be deployed.

| rule | n | hits | miss | hit_rate | roi_per_unit | pnl_units | avg_odds |
|---|---|---|---|---|---|---|---|
| tp_edge_gate | 6425 | 5113 | 1284 | 0.7993 | +0.0919 | +590.36 | -445.1 |
| ev_only_longshot | 2142 | 752 | 1384 | 0.3521 | +0.4699 | +1006.43 | 386.6 |
| proxy_safe_haven | 4120 | 3569 | 532 | 0.8703 | +0.0526 | +216.51 | -747.4 |

## 5. Final answer
- **headline**: PropVision would have published **286 picks**.
- **combined_roi_per_unit**: +0.3965
- **combined_pnl_units**: +113.40
- **combined_hit_rate**: 0.6982

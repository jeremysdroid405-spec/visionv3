# PropVision Replay — $1 Flat-Bet Publication Simulation

_Generated_: 2026-05-09T07:48:22.812748+00:00

> This report simulates a $1 flat bet on every prop PropVision would have published.

## 1. Candidate pool (NOT bets)
| run_id | snapshot | min_ct | max_ct | candidates | settled |
|---|---|---|---|---|---|
| vk2_full_30d_1778310068 | t-30m | 2024-02-02 00:30:00 | 2024-02-29 03:10:00 | 517864 | 135644 |

Settled outcome breakdown: `{'hit': 61839, 'miss': 70324, 'push': 0, 'void_dnp': 3481}` — this is data-quality signal on the candidate pool, NOT a P&L log.

## 2. Publication simulation (the answer)
| candidates | qualified | qualified_pct | settled_qualified |
|---|---|---|---|
| 517864 | 2013 | 0.3887% | 399 |

### Per-tier results — $1 flat bet on each published pick
| tier | n | hits | miss | void | hit_rate | roi_per_unit | pnl_units | avg_odds | avg_tp | avg_edge |
|---|---|---|---|---|---|---|---|---|---|---|
| safe_haven | 0 | 0 | 0 | 0 | n/a | n/a | +0.00 | n/a | n/a | n/a |
| front_lines | 179 | 149 | 30 | 0 | 0.8324 | +0.4108 | +73.54 | -146.6 | 56.0 | +13.73 |
| war_zone | 220 | 138 | 82 | 0 | 0.6273 | +0.6917 | +152.17 | 165.6 | 35.1 | +21.51 |

### Combined qualified ROI (all three tiers)
| n | hits | miss | void | hit_rate | roi_per_unit | pnl_units | avg_odds |
|---|---|---|---|---|---|---|---|
| 399 | 287 | 112 | 0 | 0.7193 | +0.5657 | +225.71 | 25.6 |

## 3. Why candidates were NOT published
Unqualified candidates: **515,851**

| reason | n |
|---|---|
| front_lines_failed: gate_hit_rate_fail | 194944 |
| war_zone_failed: gate_direction_fail | 148490 |
| front_lines_failed: gate_direction_fail | 72593 |
| safe_haven_failed: gate_edge_fail | 35709 |
| safe_haven_failed: gate_hit_rate_fail | 32224 |
| no_reference_market | 14497 |
| safe_haven_failed: gate_direction_fail | 6623 |
| safe_haven_failed: gate_cv_fail | 4534 |
| front_lines_failed: gate_edge_fail | 3073 |
| war_zone_failed: gate_hit_rate_fail | 2232 |
| front_lines_failed: gate_cv_fail | 337 |
| front_lines_failed: gate_tp_unavailable | 292 |
| front_lines_failed: gate_tp_fail | 253 |
| war_zone_failed: gate_cv_fail | 50 |

## 4. Experimental — heuristic rule probes
> Heuristic counterfactual rule sets. NOT a measurement of PropVision publication ROI. Presented only to prove the candidate pool contains usable signal — does NOT imply any of these rules should be deployed.

| rule | n | hits | miss | hit_rate | roi_per_unit | pnl_units | avg_odds |
|---|---|---|---|---|---|---|---|
| tp_edge_gate | 8058 | 6458 | 1591 | 0.8023 | +0.0927 | +747.17 | -456.1 |
| ev_only_longshot | 2410 | 800 | 1606 | 0.3325 | +0.3345 | +806.24 | 338.2 |
| proxy_safe_haven | 5097 | 4476 | 618 | 0.8787 | +0.0563 | +287.19 | -776.1 |

## 5. Final answer
- **headline**: PropVision would have published **399 picks**.
- **combined_roi_per_unit**: +0.5657
- **combined_pnl_units**: +225.71
- **combined_hit_rate**: 0.7193

# PropVision Replay — $1 Flat-Bet Publication Simulation

_Generated_: 2026-05-09T06:54:43.811010+00:00

> This report simulates a $1 flat bet on every prop PropVision would have published.

## 1. Candidate pool (NOT bets)
| run_id | snapshot | min_ct | max_ct | candidates | settled |
|---|---|---|---|---|---|
| vk2_smoke3_1778309625 | t-30m | 2024-02-02 00:30:00 | 2024-02-02 03:00:00 | 13639 | 13639 |

Settled outcome breakdown: `{'hit': 6446, 'miss': 7080, 'push': 0, 'void_dnp': 113}` — this is data-quality signal on the candidate pool, NOT a P&L log.

## 2. Publication simulation (the answer)
| candidates | qualified | qualified_pct | settled_qualified |
|---|---|---|---|
| 13639 | 79 | 0.5792% | 79 |

### Per-tier results — $1 flat bet on each published pick
| tier | n | hits | miss | void | hit_rate | roi_per_unit | pnl_units | avg_odds | avg_tp | avg_edge |
|---|---|---|---|---|---|---|---|---|---|---|
| safe_haven | 0 | 0 | 0 | 0 | n/a | n/a | +0.00 | n/a | n/a | n/a |
| front_lines | 60 | 53 | 7 | 0 | 0.8833 | +0.4505 | +27.03 | -178.5 | 58.6 | +11.47 |
| war_zone | 19 | 13 | 6 | 0 | 0.6842 | +0.8216 | +15.61 | 164.3 | 36.9 | +18.73 |

### Combined qualified ROI (all three tiers)
| n | hits | miss | void | hit_rate | roi_per_unit | pnl_units | avg_odds |
|---|---|---|---|---|---|---|---|
| 79 | 66 | 13 | 0 | 0.8354 | +0.5397 | +42.64 | -96.1 |

## 3. Why candidates were NOT published
Unqualified candidates: **13,560**

| reason | n |
|---|---|
| front_lines_failed: gate_hit_rate_fail | 5515 |
| war_zone_failed: gate_direction_fail | 3865 |
| front_lines_failed: gate_direction_fail | 1698 |
| safe_haven_failed: gate_edge_fail | 920 |
| safe_haven_failed: gate_hit_rate_fail | 750 |
| no_reference_market | 424 |
| safe_haven_failed: gate_direction_fail | 166 |
| safe_haven_failed: gate_cv_fail | 73 |
| front_lines_failed: gate_edge_fail | 69 |
| war_zone_failed: gate_hit_rate_fail | 65 |
| front_lines_failed: gate_tp_unavailable | 9 |
| front_lines_failed: gate_cv_fail | 6 |

## 4. Experimental — heuristic rule probes
> Heuristic counterfactual rule sets. NOT a measurement of PropVision publication ROI. Presented only to prove the candidate pool contains usable signal — does NOT imply any of these rules should be deployed.

| rule | n | hits | miss | hit_rate | roi_per_unit | pnl_units | avg_odds |
|---|---|---|---|---|---|---|---|
| tp_edge_gate | 956 | 703 | 253 | 0.7354 | -0.0052 | -4.99 | -407.2 |
| ev_only_longshot | 312 | 83 | 229 | 0.2660 | +0.2234 | +69.71 | 361.0 |
| proxy_safe_haven | 610 | 519 | 91 | 0.8508 | +0.0357 | +21.80 | -666.8 |

## 5. Final answer
- **headline**: PropVision would have published **79 picks**.
- **combined_roi_per_unit**: +0.5397
- **combined_pnl_units**: +42.64
- **combined_hit_rate**: 0.8354

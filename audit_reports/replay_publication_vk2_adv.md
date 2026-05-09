# PropVision Replay — $1 Flat-Bet Publication Simulation

_Generated_: 2026-05-09T13:43:31.347379+00:00

> This report simulates a $1 flat bet on every prop PropVision would have published.

## 1. Candidate pool (NOT bets)
| run_id | snapshot | min_ct | max_ct | candidates | settled |
|---|---|---|---|---|---|
| vk2_full_adv_1778313861 | t-30m | 2024-02-02 00:30:00 | 2024-02-29 03:10:00 | 462410 | 126977 |

Settled outcome breakdown: `{'hit': 57219, 'miss': 66523, 'push': 0, 'void_dnp': 3235}` — this is data-quality signal on the candidate pool, NOT a P&L log.

## 2. Publication simulation (the answer)
| candidates | qualified | qualified_pct | settled_qualified |
|---|---|---|---|
| 462410 | 1432 | 0.3097% | 1100 |

### Per-tier results — $1 flat bet on each published pick
| tier | n | hits | miss | void | hit_rate | roi_per_unit | pnl_units | avg_odds | avg_tp | avg_edge |
|---|---|---|---|---|---|---|---|---|---|---|
| safe_haven | 0 | 0 | 0 | 0 | n/a | n/a | +0.00 | n/a | n/a | n/a |
| front_lines | 932 | 786 | 146 | 0 | 0.8433 | +0.3598 | +335.38 | -180.4 | 59.2 | +11.63 |
| war_zone | 168 | 115 | 53 | 0 | 0.6845 | +0.8579 | +144.12 | 170.6 | 34.9 | +21.97 |

### Combined qualified ROI (all three tiers)
| n | hits | miss | void | hit_rate | roi_per_unit | pnl_units | avg_odds |
|---|---|---|---|---|---|---|---|
| 1100 | 901 | 199 | 0 | 0.8191 | +0.4359 | +479.50 | -126.8 |

## 3. Why candidates were NOT published
Unqualified candidates: **460,978**

| reason | n |
|---|---|
| front_lines_failed: gate_hit_rate_fail | 179180 |
| war_zone_failed: gate_direction_fail | 132548 |
| front_lines_failed: gate_direction_fail | 59144 |
| safe_haven_failed: gate_edge_fail | 32454 |
| safe_haven_failed: gate_hit_rate_fail | 28822 |
| no_reference_market | 12920 |
| safe_haven_failed: gate_direction_fail | 6563 |
| safe_haven_failed: gate_cv_fail | 3198 |
| front_lines_failed: gate_edge_fail | 2857 |
| war_zone_failed: gate_hit_rate_fail | 2143 |
| front_lines_failed: gate_tp_fail | 470 |
| front_lines_failed: gate_cv_fail | 348 |
| front_lines_failed: gate_tp_unavailable | 263 |
| war_zone_failed: gate_cv_fail | 68 |

## 4. Experimental — heuristic rule probes
> Heuristic counterfactual rule sets. NOT a measurement of PropVision publication ROI. Presented only to prove the candidate pool contains usable signal — does NOT imply any of these rules should be deployed.

| rule | n | hits | miss | hit_rate | roi_per_unit | pnl_units | avg_odds |
|---|---|---|---|---|---|---|---|
| tp_edge_gate | 8163 | 6505 | 1655 | 0.7972 | +0.0930 | +759.54 | -442.5 |
| ev_only_longshot | 2226 | 749 | 1473 | 0.3371 | +0.3909 | +870.14 | 339.8 |
| proxy_safe_haven | 5102 | 4389 | 712 | 0.8604 | +0.0388 | +197.89 | -763.9 |

## 5. Final answer
- **headline**: PropVision would have published **1100 picks**.
- **combined_roi_per_unit**: +0.4359
- **combined_pnl_units**: +479.50
- **combined_hit_rate**: 0.8191

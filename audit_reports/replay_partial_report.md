# Replay Partial-Parity Test Report
_Generated_: 2026-05-09T06:21:14.418643+00:00

> ⚠️ **PARTIAL-PARITY**. This report is a TEST REPORT, not production sign-off. VK2 / injury / matchup features are stubbed; see PRD changelog 2026-05-09 for full gap matrix.

## 1. Dataset summary
| run_id | snapshot | min_ct | max_ct | evals | outcomes |
|---|---|---|---|---|---|
| a1aeb71a6ef046baae4fb56deef06667 | t-30m | 2024-02-02 00:30:00 | 2024-02-29 03:10:00 | 503200 | 134636 |

Outcome breakdown: {'hit': 61597, 'miss': 69594, 'push': 0, 'void_dnp': 3445}

Feature completeness: {'partial': 489153, 'minimal': 14047}

Known parity gaps: ['VK2 historical projection not wired (PARITY-TODO P5)', 'Injury timeline not ingested (PARITY-TODO P4)', 'Matchup/pace as-of-time not wired (PARITY-TODO P3)']

_Confidence_: **high** — Counts are direct DB reads; no inference involved.

## 2. Gate distribution
### Top rejection reasons
| reason | n |
|---|---|
| front_lines_failed: gate_hit_rate_fail | 158174 |
| war_zone_failed: gate_direction_fail | 146743 |
| front_lines_failed: gate_direction_fail | 107362 |
| safe_haven_failed: gate_direction_fail | 76874 |
| no_reference_market | 14047 |

_Confidence_: **high** — Direct count of production gate-engine outputs.

## 3. Counterfactual rule sets
| rule | n | hits | miss | hit_rate | roi_per_unit | pnl_units | avg_odds | avg_tp | avg_edge |
|---|---|---|---|---|---|---|---|---|---|
| production_gates | 134636 | 61597 | 69594 | 0.4695 | -0.12 | -16152.12 | -43.27 | 48.11 | -2.49 |
| relaxed_direction | 62142 | 38608 | 22038 | 0.6366 | -0.0443 | -2755.92 | -441.22 | 63.33 | -2.99 |
| ev_only_longshot | 108 | 13 | 95 | 0.1204 | -0.4245 | -45.85 | 441.67 | 20.81 | 5.98 |
| hr_cv_gate | 0 | 0 | 0 | None | None | 0.0 | None | None | None |
| tp_edge_gate | 169 | 155 | 14 | 0.9172 | 0.0625 | 10.57 | -937.89 | 85.88 | 3.61 |
| war_zone_longshot_proposal | 0 | 0 | 0 | None | None | 0.0 | None | None | None |

_Confidence_: **medium** — Hit/PnL math is exact; the rule predicates are heuristics (not VK2-aware), so 'profitable' here means historically profitable for THIS rule, NOT for current production tiers.

## 4. Standard vs alternate vs combo
### By is_alternate
| is_alternate | n | hits | miss | hit_rate | roi_per_unit | pnl_units | avg_odds |
|---|---|---|---|---|---|---|---|
| True | 74222 | 32567 | 39784 | 0.4501 | -0.1552 | -11522.49 | -37.8 |
| False | 60414 | 29030 | 29810 | 0.4934 | -0.0766 | -4629.63 | -49.9 |

### By is_combo
| is_combo | n | hits | miss | hit_rate | roi_per_unit | pnl_units | avg_odds |
|---|---|---|---|---|---|---|---|
| True | 67778 | 32396 | 33698 | 0.4902 | -0.1064 | -7211.89 | -99.3 |
| False | 66858 | 29201 | 35896 | 0.4486 | -0.1337 | -8940.23 | 13.6 |

### By stat_family
| stat_family | n | hits | miss | hit_rate | roi_per_unit | pnl_units | avg_odds |
|---|---|---|---|---|---|---|---|
| PTS | 27023 | 11546 | 14735 | 0.4393 | -0.1405 | -3797.6 | 61.7 |
| PRA | 24786 | 10914 | 13163 | 0.4533 | -0.1284 | -3183.04 | 36.1 |
| PTS_REB | 18063 | 9249 | 8358 | 0.5253 | -0.0812 | -1466.96 | -205.3 |
| REB | 15863 | 6936 | 8471 | 0.4502 | -0.1231 | -1952.71 | 8.0 |
| PTS_AST | 14833 | 7680 | 6891 | 0.5271 | -0.0873 | -1295.21 | -234.5 |
| REB_AST | 10096 | 4553 | 5286 | 0.4628 | -0.1255 | -1266.68 | -43.7 |
| AST | 9904 | 4604 | 5104 | 0.4742 | -0.1141 | -1130.36 | -132.7 |
| THREES | 8246 | 3684 | 4371 | 0.4574 | -0.1157 | -953.92 | -121.2 |
| BLK | 3190 | 1150 | 1932 | 0.3731 | -0.2816 | -898.15 | 477.4 |
| STL | 2632 | 1281 | 1283 | 0.4996 | -0.0788 | -207.49 | -37.1 |

_Confidence_: **high** — Direct hit/PnL by group; no inference.

## 5. Odds-bucket performance
| odds_bucket | n | hit_rate | roi_per_unit | pnl_units | avg_tp | avg_edge |
|---|---|---|---|---|---|---|
| +100..+149 | 18286 | 0.4063 | -0.1207 | -2207.1 | 43.58 | -2.77 |
| +150..+199 | 7222 | 0.3064 | -0.1656 | -1195.84 | 34.35 | -2.23 |
| +200..+299 | 9375 | 0.2328 | -0.1959 | -1836.38 | 27.53 | -1.49 |
| +300..+499 | 5779 | 0.1464 | -0.2877 | -1662.81 | 19.96 | -0.34 |
| +500+ | 11330 | 0.0503 | -0.4452 | -5043.98 | 8.63 | 0.04 |
| neg | 82644 | 0.6041 | -0.0509 | -4206.01 | 59.92 | -3.05 |

_Confidence_: **medium** — ROI by odds bucket is mathematically clean, BUT relies on the partial-parity feature set; a fully-featured run could shift bucket compositions.

## 6. Snapshot-timing performance
| snapshot_label | n | hits | miss | hit_rate | roi_per_unit | pnl_units | avg_odds |
|---|---|---|---|---|---|---|---|
| t-30m | 134636 | 61597 | 69594 | 0.4695 | -0.12 | -16152.12 | -43.3 |

_Confidence_: **medium** — If the run was filtered to a single snapshot (e.g. t-30m), this section will report 1 row — re-run engine across more windows to populate.

## 7. Direction-fail-but-profitable (μ vs TP probe)
`{"n": 992, "hits": 879, "miss": 109, "hit_rate": 0.8897, "roi_per_unit": 0.0438, "pnl_units": 43.41, "avg_odds": -960.54, "avg_tp": 86.43, "avg_edge": 3.54}`

_Confidence_: **medium** — Probes whether production direction gate is too strict for partial-feature replay; not a production-tier verdict.

## 8. Proxy tiers (HEURISTIC — NOT official tiers)
| proxy | n | hits | miss | hit_rate | roi_per_unit | pnl_units | avg_odds |
|---|---|---|---|---|---|---|---|
| proxy_safe_haven | 591 | 534 | 52 | 0.9113 | 0.0363 | 21.46 | -1005.99 |
| proxy_front_lines | 2 | 2 | 0 | 1.0 | 0.7704 | 1.54 | -130.0 |
| proxy_war_zone | 0 | 0 | 0 | None | None | 0.0 | None |
| proxy_war_zone_longshot | 2 | 0 | 2 | 0.0 | -1.0 | -2.0 | 342.5 |

_Confidence_: **low** — These are HEURISTIC proxy rules, not production tiers. Without VK2/injury/matchup, they cannot be claimed as production-faithful tier definitions.

## 10. Final answer
- **what_we_can_trust_today**: Replay infrastructure, leakage gates, TP math, ref-odds chain, outcome settlement (134k unique rows). Production gate execution path is faithful.
- **what_we_cannot_trust_yet**: Tier ROI (100% unqualified due to missing VK2/injury/matchup). Direction signal (μ is BDL-rolling only). Production tier claims of any kind.
- **best_counterfactual_rule**: tp_edge_gate → ROI +0.0625 on n=169
- **obviously_bad_areas**: odds buckets with worst ROI: +500+(-0.445, n=11330), +300..+499(-0.288, n=5779), +200..+299(-0.196, n=9375)
- **longshot_mode_signal**: ev_only_longshot ROI=-0.4245 on n=108 | wz_longshot_proposal ROI=n/a
- **war_zone_concept_status**: proxy_war_zone ROI=n/a on n=0 | proxy_war_zone_longshot ROI=-1.0000 on n=2 — DIRECTIONAL ONLY (proxy rules ≠ production tiers)
- **headline**: PARTIAL-PARITY: replay infrastructure works; ROI claims require VK2/injury/matchup wiring before deployment.

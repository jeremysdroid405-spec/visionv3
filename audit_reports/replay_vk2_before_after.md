# Replay Before/After — VK2 Wiring
_Generated_: 2026-05-09T07:49:43.953844+00:00

- **Before**: `a1aeb71a6ef046baae4fb56deef06667` (no historical VK2; VK2 fields stamped from rolling-μ feature_set as legacy placeholder).
- **After**:  `vk2_full_30d_1778310068` (historical VK2 wired end-to-end; production gates fed VK2 projections).

## Headline
| metric | before | after | delta |
|---|---|---|---|
| candidates | 503,200 | 517,864 | +14,664 |
| qualified | 0 | 2,013 | +2,013 |
| qualified_pct | 0.0% | 0.3887% | — |

## Publications by tier ($1 flat bet)
| tier | n_before | n_after | hr_after | roi_after | pnl_after |
|---|---|---|---|---|---|
| safe_haven | 0 | 0 | n/a | n/a | 0.0 |
| front_lines | 0 | 179 | 0.8324 | 0.4108 | 73.5405 |
| war_zone | 0 | 220 | 0.6273 | 0.6917 | 152.17 |
| **combined** | 0 | 399 | 0.7193 | 0.5657 | 225.71 |

## Feature completeness
| label | before | after |
|---|---|---|
| minimal | 14,047 | 392 |
| partial | 489,153 | 39,155 |
| vk2_full | 0 | 224 |
| vk2_partial | 0 | 478,093 |

## Top fail reasons (after)
| reason | n |
|---|---|
| front_lines_failed: gate_hit_rate_fail | 194,944 |
| war_zone_failed: gate_direction_fail | 148,490 |
| front_lines_failed: gate_direction_fail | 72,593 |
| safe_haven_failed: gate_edge_fail | 35,709 |
| safe_haven_failed: gate_hit_rate_fail | 32,224 |
| no_reference_market | 14,497 |
| safe_haven_failed: gate_direction_fail | 6,623 |
| safe_haven_failed: gate_cv_fail | 4,534 |
| front_lines_failed: gate_edge_fail | 3,073 |
| war_zone_failed: gate_hit_rate_fail | 2,232 |

## Notes
- This is the **first** end-to-end replay run with historical VK2 wired. Production gates received real VK2 projections; no fallback to legacy VK1.
- Injury / matchup / pace features remain stubbed — see `audit_reports/vk2_production_map.md`.
- Safe Haven generates 0 picks because the Feb-2024 window has zero `bdl_advanced_stats` rows; without advanced features VK2 vision-scores compress and the SH vision_score_gate (>= 80) rejects every candidate. This is a data-coverage issue, not a model issue.
- This is **NOT** production sign-off. The Front Lines / War Zone numbers below should be reproduced on a later 30-day window where adv_stats are present before any deployment decision.

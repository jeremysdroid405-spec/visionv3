# Replay Before/After — VK2 Wiring
_Generated_: 2026-05-09T13:44:32.268635+00:00

- **Before**: `vk2_full_30d_1778310068` (no historical VK2; VK2 fields stamped from rolling-μ feature_set as legacy placeholder).
- **After**:  `vk2_full_adv_1778313861` (historical VK2 wired end-to-end; production gates fed VK2 projections).

## Headline
| metric | before | after | delta |
|---|---|---|---|
| candidates | 517,864 | 462,410 | -55,454 |
| qualified | 2,013 | 1,432 | -581 |
| qualified_pct | 0.3887% | 0.3097% | — |

## Publications by tier ($1 flat bet)
| tier | n_before | n_after | hr_after | roi_after | pnl_after |
|---|---|---|---|---|---|
| safe_haven | 0 | 0 | n/a | n/a | 0.0 |
| front_lines | 179 | 932 | 0.8433 | 0.3598 | 335.3789 |
| war_zone | 220 | 168 | 0.6845 | 0.8579 | 144.12 |
| **combined** | 399 | 1100 | 0.8191 | 0.4359 | 479.5 |

## Feature completeness
| label | before | after |
|---|---|---|
| minimal | 392 | 377 |
| partial | 39,155 | 35,017 |
| vk2_full | 224 | 419,915 |
| vk2_partial | 478,093 | 7,101 |

## Top fail reasons (after)
| reason | n |
|---|---|
| front_lines_failed: gate_hit_rate_fail | 179,180 |
| war_zone_failed: gate_direction_fail | 132,548 |
| front_lines_failed: gate_direction_fail | 59,144 |
| safe_haven_failed: gate_edge_fail | 32,454 |
| safe_haven_failed: gate_hit_rate_fail | 28,822 |
| no_reference_market | 12,920 |
| safe_haven_failed: gate_direction_fail | 6,563 |
| safe_haven_failed: gate_cv_fail | 3,198 |
| front_lines_failed: gate_edge_fail | 2,857 |
| war_zone_failed: gate_hit_rate_fail | 2,143 |

## Notes
- This is the **first** end-to-end replay run with historical VK2 wired. Production gates received real VK2 projections; no fallback to legacy VK1.
- Injury / matchup / pace features remain stubbed — see `audit_reports/vk2_production_map.md`.
- Safe Haven generates 0 picks because the Feb-2024 window has zero `bdl_advanced_stats` rows; without advanced features VK2 vision-scores compress and the SH vision_score_gate (>= 80) rejects every candidate. This is a data-coverage issue, not a model issue.
- This is **NOT** production sign-off. The Front Lines / War Zone numbers below should be reproduced on a later 30-day window where adv_stats are present before any deployment decision.

# Phase 4 — Universal Production Gate Engine in Replay

**Date:** 2026-05-17
**Sport:** MLB
**Date replayed:** 2026-05-05  (single date, per user directive)
**Path:** Replay-row → NormalizedMetrics → `evaluate_tier_with_overrides`
          (the SAME function the live serving path calls)
**No duplicated thresholds. No simplified SH/FL logic. No directional artifacts.**

---

## 1. Architecture

Phase 4 introduces TWO new modules and one surgical patch:

| File | Role |
|---|---|
| `services/replay/replay_field_hydrators.py` | Async loaders for the 3 fields the replay row does not carry: `book_count` & `tp_source` (from `mlb_historical_alt_odds_raw` for the snapshot), `avg_hit_margin`/`avg_miss_margin` (from `mlb_master_hub_2026.bdl_game_logs[]` as-of < game_date). Margin computer is a byte-for-byte port of `MLBTierSorter._calculate_hit_margins`. |
| `services/replay/replay_metrics_builder.py` | Pure function `build_metrics_from_replay_row(...)` that produces a `NormalizedMetrics` from a replay row + hydrated context. Stat-family resolution routes through the live SSOT `canonical_stats` registry — replay's internal `_STAT_FAMILY_MAP` is NOT used (it would mis-route `strikeouts` → `_default` instead of `batter_strikeouts`). No silent defaults. |
| `services/replay/production_replay_runner.py` | Patched: new `gate_path={"legacy_wz","universal"}` keyword. Default stays `"legacy_wz"` so historical runs are unchanged. When `"universal"`, every gate decision goes through `evaluate_tier_with_overrides`. Per-tier `gate_config_version` is a deterministic SHA-16 of the resolved threshold cfg per `(stat_family, side)`. |

No changes to `services/scoring/gates/*` (the live gate engine). No new threshold tables. No SH/FL spec copied.

## 2. Field coverage report (read-only scan)

Source: `audits/phase4_field_coverage.py` over `MLB-PRODREPLAY-20260505-WZ-1100UTC-00008`.

### Qualified cohort (gate_pass=True, 361 rows)

| Field | Populated | Notes |
|---|---:|---|
| `sport`, `tier`, `stat_family`, `side` | 361/361 (100%) | Set by builder |
| `reference_book`, `reference_odds` | 361/361 | From row.book / row.odds |
| `book_count` | 361/361 | From snapshot inventory; min=1 since the row itself proves a book |
| `tp_source` | 361/361 | 155 devig / 206 one_sided |
| `is_alt` | 361/361 | From row.is_alternate |
| `p_model_pct`, `tp`, `edge_pct` | 361/361 | ×100 conversion from decimal |
| `hit_rate`, `hit_rate_l5/l10/l20` | 361/361 | Already in pp on the row |
| `cv`, `line` | 361/361 | Pass-through |
| `extras.projection` | 361/361 | From row.projection_mu |
| `avg_hit_margin` (line==0.5 only) | 345/345 (100%) | 16 non-0.5 rows skip this; all 345 of the 0.5 rows have ≥10 prior games |
| `vision_score` | 0/361 | **Correctly None** — MLB has no vision gate |
| `ceiling_rate` | 0/361 | **Correctly None** — MLB WZ rewrite removed ceiling_gate; SH/FL never had it |
| `hit_rate_sample_size` | 0/361 | Documented but `_eval_hit_rate` does not consume it; same as live MLB |
| `context_vetoes`, `blowout_risk`, `lineup_confirmed`, `injury_flag` | empty | No MLB tier uses `context_gate` |

### Full pool (gate_pass=any, 25,431 rows)

Same 100% coverage on every gate-required field, with one exception:
**58 of the 25,431 rows** (0.23%) are 0.5-line props where the player has fewer than 10 prior game logs as-of 2026-05-05. For those rows, `avg_hit_margin = None`. This is identical to live serving behaviour — the margin computer in `MLBTierSorter._calculate_hit_margins` returns `(None, None)` under the same condition. The gate engine then fails closed on those rows with `MARGIN_FAIL / "margin_missing"`. **No silent defaulting.**

Per-stat-family distribution of the 58 fail-closed-on-margin rows:
| family | missing / 0.5-line total |
|---|---|
| rbis | 21 / 2,594 (0.8%) |
| hits | 16 / 3,127 (0.5%) |
| runs | 10 / 1,983 (0.5%) |
| total_bases | 9 / 1,764 (0.5%) |
| earned_runs | 1 / 3 (33%) |
| batter_strikeouts | 1 / 331 (0.3%) |

## 3. WZ A/B validation (Phase 4 universal vs legacy WZ)

Source: `audits/phase4_wz_validation.py`.

Both runs cover the SAME 25,431 candidate rows for `2026-05-05` / `war_zone`.

|  | LEGACY (`...-00008`) | PHASE 4 UNIVERSAL (`...-00014`) | Δ |
|---|---:|---:|---:|
| Qualified rows | 361 | 361 | **0** |
| Wins / Losses / Pushes / Ungraded | 226 / 43 / 0 / 92 | 226 / 43 / 0 / 92 | **0** |
| Stake (units) | 269.00 | 269.00 | 0.00 |
| Profit (units) | +83.6190 | +83.6190 | **+0.0000** |
| Hit rate | 84.0149% | 84.0149% | **+0.0000%** |
| ROI | +31.0851% | +31.0851% | **+0.0000%** |
| Displayed cards (top-20) | 20 | 20 | **0** (same keys, same ranks) |
| only-in-LEGACY qualified | — | — | 0 |
| only-in-NEW qualified | — | — | 0 |

**Result: byte-identical.** The Phase-4 universal path produces the exact same qualified pool, exact same grades, exact same cards as the legacy WZ spec on the same date. Universal `gate_config_version` for war_zone:

* OVER side: `mlb_war_zone_universal_d03744aaad78b922`
* UNDER side: `mlb_war_zone_universal_58d5690616745e93`

The two SHA-distinct configs reflect the OVER vs UNDER threshold tables resolved by `resolve_thresholds`.

## 4. Three-tier production-gate replay — 2026-05-05

Source: `audits/phase4_run_3tier_2026_05_05.py`. JSON artifact: `/app/backend/audits/phase4_3tier_2026-05-05.json`.

### Run records

| Tier | Serial | rows_qualified | cards_displayed | gate_path |
|---|---|---:|---:|---|
| safe_haven | `MLB-PRODREPLAY-20260505-SH-1100UTC-00015` | 104 | 20 | universal |
| front_lines | `MLB-PRODREPLAY-20260505-FL-1100UTC-00016` | 292 | 20 | universal |
| war_zone | `MLB-PRODREPLAY-20260505-WZ-1100UTC-00017` | 361 | 20 | universal |

### Qualified-pool aggregates ($1/row flat)

| Tier | n | W | L | U | HR | ROI | P&L |
|---|---:|---:|---:|---:|---:|---:|---:|
| safe_haven | 104 | 69 | 11 | 24 | **86.25%** | **+31.34%** | **+$25.08** |
| front_lines | 292 | 213 | 23 | 56 | **90.25%** | **+36.79%** | **+$86.84** |
| war_zone | 361 | 226 | 43 | 92 | **84.01%** | **+31.09%** | **+$83.62** |

### Displayed-card aggregates (top-20 per tier, $1/card flat)

| Tier | cards | W | L | P | U | HR | ROI | P&L |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| safe_haven | 20 | 11 | 1 | 0 | 8 | **91.67%** | **+24.93%** | **+$4.99** |
| front_lines | 20 | 13 | 1 | 0 | 6 | **92.86%** | **+30.25%** | **+$6.05** |
| war_zone | 20 | 9 | 2 | 0 | 9 | **81.82%** | **+17.31%** | **+$3.46** |

### Per-tier × per-odds-bucket (qualified pool)

| Tier | bucket | n | W | L | HR | ROI | P&L |
|---|---|---:|---:|---:|---:|---:|---:|
| safe_haven | plus_med | 1 | 0 | 0 | — | 0% | $0.00 |
| safe_haven | minus_low | 1 | 1 | 0 | 100% | +91.74% | +$0.92 |
| safe_haven | minus_med | 10 | 10 | 0 | 100% | +73.39% | +$7.34 |
| safe_haven | minus_heavy | 69 | 42 | 6 | 87.50% | +33.86% | +$16.25 |
| safe_haven | minus_xx | 23 | 16 | 5 | 76.19% | +2.69% | +$0.56 |
| front_lines | minus_low | 3 | 3 | 0 | 100% | +92.91% | +$2.79 |
| front_lines | minus_med | 30 | 29 | 0 | 100% | +79.30% | +$23.00 |
| front_lines | minus_heavy | 176 | 125 | 3 | 97.66% | +48.66% | +$62.29 |
| front_lines | minus_xx | 83 | 56 | 20 | 73.68% | -1.63% | **-$1.24** |
| war_zone | plus_med | 18 | 11 | 7 | 61.11% | +33.72% | +$6.07 |
| war_zone | minus_low | 3 | 3 | 0 | 100% | +92.89% | +$2.79 |
| war_zone | minus_med | 32 | 30 | 0 | 100% | +79.35% | +$23.81 |
| war_zone | minus_heavy | 220 | 131 | 9 | 93.57% | +43.41% | +$60.77 |
| war_zone | minus_xx | 88 | 51 | 27 | 65.38% | -12.58% | **-$9.81** |

### Per-tier × per-stat-family (qualified pool)

| Tier | family | n | W | L | HR | ROI | P&L |
|---|---|---:|---:|---:|---:|---:|---:|
| safe_haven | total_bases | 57 | 32 | 5 | 86.49% | +38.94% | +$14.41 |
| safe_haven | hits | 43 | 37 | 6 | 86.05% | +24.81% | +$10.67 |
| safe_haven | strikeouts | 4 | 0 | 0 | — | 0% | $0.00 (BDL gap) |
| front_lines | hits | 137 | 123 | 14 | 89.78% | +35.17% | +$48.18 |
| front_lines | total_bases | 136 | 84 | 9 | 90.32% | +36.39% | +$33.85 |
| front_lines | runs | 5 | 5 | 0 | 100% | +85.59% | +$4.28 |
| front_lines | pitcher_walks | 1 | 1 | 0 | 100% | +52.63% | +$0.53 |
| front_lines | strikeouts | 13 | 0 | 0 | — | 0% | $0.00 (BDL gap) |
| war_zone | total_bases | 178 | 102 | 22 | 82.26% | +31.18% | +$38.66 |
| war_zone | hits | 151 | 115 | 21 | 84.56% | +27.73% | +$37.72 |
| war_zone | runs | 5 | 5 | 0 | 100% | +85.59% | +$4.28 |
| war_zone | earned_runs | 3 | 3 | 0 | 100% | +81.11% | +$2.43 |
| war_zone | pitcher_walks | 1 | 1 | 0 | 100% | +52.63% | +$0.53 |
| war_zone | strikeouts | 23 | 0 | 0 | — | 0% | $0.00 (BDL gap) |

### Biggest winning / losing stat family per tier (by P&L on qualified pool)

| Tier | Biggest winner | Biggest "loser" |
|---|---|---|
| safe_haven | **`total_bases`** (n=57, +$14.41) | `strikeouts` (n=4, $0.00 — **ungraded due to BDL log gap, not a real loss**) |
| front_lines | **`hits`** (n=137, +$48.18) | `strikeouts` (n=13, $0.00 — **ungraded due to BDL log gap, not a real loss**) |
| war_zone | **`total_bases`** (n=178, +$38.66) | `strikeouts` (n=23, $0.00 — **ungraded due to BDL log gap, not a real loss**) |

Caveat: **no MLB stat family had a NET-NEGATIVE P&L in any tier on 2026-05-05.** The ostensible "biggest loser" in every tier is batter strikeouts, which is fully ungraded because the BDL game-logs collection for 2026-05-05 is missing batter-K outcomes (the same gap noted in the prior 6-day report). If you want a "loser by raw odds bucket": `war_zone minus_xx` at -$9.81 P&L on 88 picks, then `front_lines minus_xx` at -$1.24 on 83 picks.

## 5. What we are NOT claiming

* No 6-day sweep was run with Phase 4. WZ proved byte-identical, but SH and FL are NEW data — they have no legacy A/B baseline.
* SH `tp_source_gate` rejected 206 one-sided picks from the qualified pool (some of which the WZ-only path was qualifying as WZ-grade with high HR). This is the production gate engine doing exactly what it does in live serving — rejecting `tp_source=one_sided` props that don't pass the narrow override (HR_L20≥90, HR_L5≥80, edge≥5pp, CV≤0.70). This explains why SH has 104 qualified vs WZ's 361.
* The 58 fail-closed-on-margin rows in the full pool are not graded by Phase 4. They will be by live serving once the BDL log gap is filled.

## 6. Files added / changed

```
+ services/replay/replay_field_hydrators.py        (new, 215 lines)
+ services/replay/replay_metrics_builder.py        (new, 138 lines)
~ services/replay/production_replay_runner.py      (gate_path param + universal path, ~80 added lines)
+ audits/phase4_field_coverage.py                  (new, 161 lines)
+ audits/phase4_wz_validation.py                   (new, 175 lines)
+ audits/phase4_run_3tier_2026_05_05.py            (new, 197 lines)
+ audits/PHASE4_REPORT_2026_05_17.md               (this file)
+ audits/phase4_3tier_2026-05-05.json              (consolidated machine-readable artifact)
```

## 7. Run-doc audit pins (per-tier)

Each new run doc carries:

* `gate_path = "universal"`
* `universal_gate_cfg_versions` — map of `{sport}|{tier}|{stat_family}|{side}` → deterministic SHA-16 (e.g. `mlb_war_zone_universal_d03744aaad78b922`). Same cfg always yields same pin.
* `production_pipeline_version`, `adapter_version`, `feature_cache_version`, `scoring_config_version`, `model_versions.mlb_high_friction_model` (unchanged across the three serials — only the threshold cfg differs by tier).
* `git_commit_sha` — captured at run time.

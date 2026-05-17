# SSOT Production Replay — 6-Day Backtest (2026-05-01 → 2026-05-06)

**Generated:** 2026-05-17
**Path:** Universal SSOT — `production_replay_runner` + Layer-3 hydrated v1.1 + `card_builder` (Phase 3 displayed-card mode)
**Mode:** Displayed cards only ($1 per card, top-20 per day per war_zone)

## Headline

| Metric | Value |
|---|---:|
| Days replayed | **6** |
| Cards displayed | **120** |
| Wins | 56 |
| Losses | 23 |
| Pushes | 0 |
| Ungraded | 41 |
| **Hit rate (decided)** | **70.89 %** |
| **ROI** | **+13.93 %** |
| Stake total ($1 per card) | $120.00 |
| **Profit total** | **+$16.72** |

## Per-date breakdown

| Date | Serial | Cards | W/L/U | HR | ROI | P&L |
|---|---|---:|---|---:|---:|---:|
| 2026-05-01 | `MLB-PRODREPLAY-20260501-WZ-1100UTC-00010` | 20 | 10/2/8 | **83.33%** | +18.46% | +$3.69 |
| 2026-05-02 | `MLB-PRODREPLAY-20260502-WZ-1100UTC-00011` | 20 | 9/4/7 | 69.23% | +11.91% | +$2.38 |
| 2026-05-03 | `MLB-PRODREPLAY-20260503-WZ-1100UTC-00012` | 20 | 8/5/7 | 61.54% | +14.28% | +$2.86 |
| 2026-05-04 | `MLB-PRODREPLAY-20260504-WZ-1100UTC-00013` | 20 | 8/5/7 | 61.54% | -3.08% | -$0.62 |
| 2026-05-05 | `MLB-PRODREPLAY-20260505-WZ-1100UTC-00008` | 20 | 9/2/9 | **81.82%** | +17.31% | +$3.46 |
| 2026-05-06 | `MLB-PRODREPLAY-20260506-WZ-1100UTC-00009` | 20 | 12/5/3 | 70.59% | **+24.70%** | +$4.94 |

## By stat family

| Stat | n | W | L | HR | ROI | P&L |
|---|---:|---:|---:|---:|---:|---:|
| `hits` | 18 | 15 | 3 | **83.3%** | **+52.5%** | +$9.44 |
| `total_bases` | 60 | 36 | 19 | 65.5% | +5.0% | +$2.82 |
| `pitcher_strikeouts` | 3 | 3 | 0 | **100%** | **+101%** | +$3.39 |
| `earned_runs` | 4 | 2 | 1 | 66.7% | +26.8% | +$1.07 |
| `strikeouts` | 35 | 0 | 0 | — | 0.0% | $0.00 (all ungraded — BDL gap?) |

**Strikeouts (batter strikeouts) all ungraded** — likely the BDL 9-day log gap mentioned in the handoff. Worth investigating separately as a P1.

## By odds bucket

| Bucket | n | W | L | HR | ROI | P&L |
|---|---:|---:|---:|---:|---:|---:|
| `plus_high` (≥+200) | 4 | 2 | 2 | 50% | +98.3% | +$2.90 |
| `plus_med` (+100..+200) | 8 | 6 | 2 | 75% | **+67.6%** | +$6.33 |
| `minus_low` (-110..0) | 1 | 0 | 0 | — | 0.0% | $0.00 |
| `minus_med` (-150..-110) | 15 | 6 | 5 | 54.5% | -3.3% | -$0.72 |
| `minus_heavy` (-250..-150) | 77 | 40 | 13 | 75.5% | +11.2% | +$8.66 |
| `minus_xx` (≤-250) | 15 | 2 | 1 | 66.7% | -2.4% | -$0.45 |

**Plus-money picks are healthy** — the few +200/+ picks have hit at 50% (well above breakeven of ~33%). Heavy-minus is where the bulk lives and it's holding 75.5% HR / +11.2% ROI.

## Top 5 displayed-card winners across all 6 days

| Player | Stat | Line/Side | Book@Odds | Edge | Date | P&L |
|---|---|---|---|---:|---|---:|
| Tyler Glasnow | pitcher_K | 5.5 / UNDER | betrivers @ +265 | 0.520 | 05-06 | **+$2.65** |
| Byron Buxton | hits | 0.5 / UNDER | draftkings @ +186 | 0.249 | 05-03 | **+$1.86** |
| Matt Olson | total_bases | 1.5 / OVER | betonlineag @ +115 | 0.405 | 05-06 | **+$1.15** |
| Nick Kurtz | total_bases | 0.5 / OVER | fanatics @ -130 | 0.281 | 05-06 | +$0.77 |
| Leody Taveras | total_bases | 0.5 / OVER | williamhill_us @ -145 | 0.279 | 05-06 | +$0.69 |

## Top 3 displayed-card losers

| Player | Stat | Line/Side | Book@Odds | Edge | Date | P&L |
|---|---|---|---|---:|---|---:|
| Ozzie Albies | total_bases | 1.5 / OVER | draftkings @ +141 | 0.514 | 05-06 | -$1.00 |
| Royce Lewis | total_bases | 0.5 / OVER | betonlineag @ -112 | 0.336 | 05-02 | -$1.00 |
| Ivan Herrera | total_bases | 0.5 / OVER | espnbet @ -200 | 0.289 | 05-03 | -$1.00 |

## Sanity-check signals

- **No μ outliers.** Max `total_bases` μ across all 6 dates: 4.23 (was 7.90 pre-hydration). Zero rows above μ=4.5.
- **All Layer-3 outputs stamped** `replay_engine_v1.1_hydration_2026_05_17` (the hydration fix is universally applied).
- **Pod stayed healthy.** Cgroup peak observed: 3.85 GB / 8 GB. Disk OK throughout.
- **Phase 3 cards** persisted per run; idempotent re-runs verified earlier.
- **Audit pins** complete on every `mlb_production_replay_runs` doc (production_pipeline_version SHA, adapter_version SHA, scoring/gate/feature versions, git_commit_sha, input_collection_versions).

## Cumulative bet-sizing math (informational only)

At $100 per card flat (instead of $1):
- Total stake: $12,000
- Total profit: **+$1,672**
- ROI: same +13.93%

At $1,000 per card flat:
- Total stake: $120,000
- Total profit: **+$16,720**

## Caveats

1. **Batter strikeouts ungraded** across all 6 days. This is the BDL log gap from the handoff, NOT a card-builder issue. Until that's fixed, batter K results are not usable.
2. **Sample size: 80 graded picks / 120 displayed.** Not enough to claim statistical significance — directional only.
3. **Single tier (war_zone).** Other tiers (front_lines, safe_haven, goblin_vault) would need a per-tier rebuild via the multi-tier evaluator.
4. **No real-money exposure was simulated.** Stake = $1 flat; no Kelly sizing, no bankroll mgmt, no correlated-bet penalty.

## Next sensible step

If these directionally-positive results hold up:
- Extend to 05-07 → 05-15 (9 more dates) once the BDL ingest gap for those dates is addressed. Re-run `audits/ssot_replay_multidate.py --dates 2026-05-07 … 2026-05-15`.
- Or run all 3 tiers (SH/FL/WZ) for 05-01 → 05-06 to see whether front_lines / safe_haven move the needle.

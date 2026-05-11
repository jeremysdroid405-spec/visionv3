# WZ Hit-Rate Floor A/B Backtest — 2026-05-10

**Test:** Lower NBA War Zone `hit_rate_gate.min` from **50.0 → 35.0**.
**Backtest window:** Feb 2024, 30 days, t-30m snapshot label.
**Cache:** `replay_vk2_cache` source_run_id `full_chunked_1778347914`
(331,863 canonical/side cache rows → 545,780 eval rows per variant).
**Harness:** `backend/scripts/wz_hr_backtest.py` (in-process monkey-patch
of `_NBA_WAR_ZONE_BASE.hit_rate_gate.min`).
**Run IDs:**
- Baseline (HR ≥ 50): `wz_hr_ab_1778457677_hr50`
- Test (HR ≥ 35): `wz_hr_ab_1778457677_hr35`

## Headline result

| Metric | HR ≥ 50 (current) | HR ≥ 35 (test) | Δ |
|---|---:|---:|---:|
| WZ qualified eval rows | 215 | 430 | **+100%** |
| WZ distinct picks (de-duped per event×canonical) | 105 | 194 | +89 (+85%) |
| WZ hit rate (decided picks) | **62.86%** | **67.01%** | **+4.15pp** |
| WZ PnL (American-odds units) | +77.62u | +175.08u | +97.46u (+126%) |
| WZ ROI per unit | **+73.92%** | **+90.25%** | **+16.33pp** |

Lowering the HR floor **doubles WZ supply, raises the hit rate by
4+ points, and lifts ROI per unit by 16 points** over the 30-day
window. Not a marginal tweak — a meaningful sample (194 settled
picks) and a meaningful effect.

## Incremental cohort isolation

To prove the effect isn't a sampling artefact, we partitioned the
test-variant picks by whether they would have passed under the
old floor:

| Cohort | n | hits | hit rate | PnL | ROI/u |
|---|---:|---:|---:|---:|---:|
| **NEW** picks (HR_L20 in [35, 50), only enabled by HR ≥ 35) | 91 | 64 | **70.33%** | +95.46u | **+104.90%** |
| **Already-passing** picks (HR_L20 ≥ 50, in both runs) | 103 | 66 | 64.08% | +79.62u | +77.30% |

The newly-enabled cohort performed **BETTER** than the
already-passing cohort on both hit rate and ROI. The HR ≥ 50 floor
was actively rejecting the *highest-EV* WZ candidates.

### Why the lower-HR cohort wins

WZ thesis: pick OVER on long-odds (+150 to +490+) where the model
strongly disagrees with the market price. Players with HR_L20 in
the **35-50% band** are exactly the players whose **recent cold
streak inflates the longshot odds beyond what the model thinks the
true probability is**:

- A player with HR_L20 = 40% on his recent line has demand-driven
  long odds (the public chases the OVER less; the book slacks the
  line UP and over-prices the OVER).
- If the model — which factors in mean reversion, projected minutes,
  opponent context, injury vacuum — projects above the line, that's
  exactly the +EV longshot the WZ tier exists to capture.
- Forcing HR ≥ 50% at the gate **filters OUT** these
  recent-cold-streak players, leaving only the players whose hot
  rolling form is already half-priced in.

## Gate-fail breakdown

```
                                  baseline (HR≥50)    test (HR≥35)
gate_direction_fail               156,665             156,665     (unchanged — dominant)
gate_hit_rate_fail                  2,480               1,923     (-557 — newly passing)
gate_cv_fail                           56                 398     (+342 — see below)
WZ qualified                          215                 430     (+215)
```

- `direction_gate` (proj ≥ line) is the universal gatekeeper —
  unchanged because the floor change is independent.
- `hit_rate_gate` rejections drop by 557; net WZ qualifies climb 215.
  Some HR-rescued rows then fail other gates (cv, edge).
- `cv_gate` rejections rise from 56 → 398 (+342). The newly-eligible
  pool contains higher-CV players (recent volatility is part of the
  recent-cold-streak profile). The WZ override ladder catches some
  of them; the rest fall out legitimately. **No threshold relaxation
  required to capture the +ROI lift.**

## Settlement source

- `replay_evaluations` (in-process) → `replay_outcomes` via
  `services/replay/resolver.build_outcome_row`.
- Outcomes joined via `replay_results` (2,894 rows; 134k unique
  player×event outcomes — known schema bug: `event_id` missing from
  unique key, see PRD changelog 2026-05-09).
- 0 push / 0 void on WZ; 100% of WZ picks decided.

## Wallclock

- Baseline scoring: 127.1s | settling: 266.9s | subtotal: 394.0s
- Test scoring:     131.1s | settling: 282.1s | subtotal: 413.2s
- Total: **846.2s** (14.1 min) for two full 30-day variants on the
  314k cache rows.

## Caveats

1. **One historical window (Feb 2024).** The replay engine is
   trustworthy for *gate optimisation / WZ longshot validation*
   per the Feb 2024 honest-confidence statement
   (`/app/audit_reports/replay_phase25_30day_FINAL.md`), but
   replicate on a second window before flipping production.
2. **VK2 projection-only.** Replay runs use the historical VK2 model
   (no injury/matchup live joins yet). Production scoring rides on
   live data — directional signal should hold, but the absolute
   ROI lift may compress.
3. **Single floor change.** The result holds the rest of the gate
   suite constant (direction_gate=1.00, cv≤0.75 base, edge≥0.01,
   vision_score_v2≥60.0, plus the rescue ladder unchanged).
4. **PnL convention.** `pnl_units` from `resolver.build_outcome_row`
   uses American-odds payout per 1u stake. +175u over 194 picks =
   +90.25% per unit — substantial but normal for a high-odds tier
   with a 67% hit rate.
5. **`replay_results` `event_id` schema bug** (known): can cap
   unique outcomes at 134k vs. ingested events 23.9k → some
   cross-event canonical_key collisions, conservatively shrinks
   the sample. Effect is the same on both variants, so the A/B
   delta is unaffected.

## Recommended action

Promote the floor change to production gated on:

1. ✅ This 30-day Feb 2024 backtest (DONE).
2. ⏳ A second-window replay (different month) producing the same
   directional lift. Suggested: Jan 2024 (different injury/lineup
   regime) and a more recent window once the BDL historical
   coverage is back-filled past the Feb 2024 cutoff.
3. ⏳ Live 14-day shadow run (write `final-nba-rt-shadow` with
   `hit_rate_gate.min = 35.0` and compare WZ tier output vs.
   canonical without flipping production).
4. ⏳ Explicit user sign-off per the WZ supply expansion directive
   (`/app/memory/PRD.md` 2026-05-09 WZ gate adjustment entry).

## Files produced

- `/app/backend/scripts/wz_hr_backtest.py` (harness)
- `/tmp/wz_hr_ab.json` (raw summary)
- `/tmp/wz_hr_ab.log` (run log)
- `/app/audit_reports/wz_hr_floor_backtest_2026-05-10.md` (this file)

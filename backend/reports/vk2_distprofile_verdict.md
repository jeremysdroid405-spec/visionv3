# Distribution-Profile Sibling Experiment — Verdict (2026-04-24)

## What was built
- `services/features/distribution_profile.py` — 123-feature builder.
  Per stat × threshold × window (L20 / L50 / career):
  `hit_N_rate` and an explicit `zero_rate`. History-only (no leakage);
  L20 Bayes-shrunk (α=3, prior=0.5); L50 / career use raw empirical
  rates. Auto-flips descending history to ascending.
- `--dist_profile` flag added to `scripts/retrain_nba_vk2.py`.
  Mutually exclusive with `--opponent` / `--opportunity`. Writes
  siblings to `models/vk2_{stat}_distprofile.pkl` (175-feat: 52 pruned
  + 123 distribution profile).
- 10 new unit tests in `tests/test_distribution_profile.py`
  (all 111 VK2/calibration/opportunity/distribution tests passing).
- `scripts/evaluate_vk2_distprofile.py` — head-to-head vs production
  base52 on the 2024 held-out slice. Output:
  `reports/vk2_distprofile_eval.md`.

## Global headline — no meaningful MAE movement

| Stat | base MAE | dp MAE | Δ MAE | base RMSE | dp RMSE | Δ RMSE |
|------|---------:|-------:|------:|----------:|--------:|-------:|
| PTS  | 4.0396 | 4.0399 | +0.0003 | 6.0342 | 6.0356 | +0.0015 |
| REB  | 1.6534 | 1.6536 | +0.0002 | 2.4577 | 2.4587 | +0.0011 |
| AST  | 1.0844 | **1.0811** | **−0.0034** | 1.7111 | 1.7033 | **−0.0079** |
| 3PM  | 0.6712 | 0.6712 |  0.0000 | 1.0850 | 1.0841 | −0.0009 |
| PRA  | 5.7731 | 5.7765 | +0.0034 | 8.5185 | 8.5201 | +0.0016 |

AST is the only stat with a materially better MAE (−0.003). Others
are within ±0.004 — effectively noise on a 45k-sample test.

## Feature importance — distribution-profile features dominate

| Stat | Rank #1 DP feature | Importance |
|------|---------------------|-----------:|
| PTS  | `pts_hit_20_rate_L50`   | 0.100  (model rank #2) |
| REB  | `reb_hit_10_rate_career`| **0.158 (#1)** |
| AST  | `ast_hit_6_rate_career` | 0.051  (#3) |
| 3PM  | `threes_hit_3_rate_L50` | 0.033  (#3) |
| PRA  | `pra_hit_30_rate_career`| **0.286 (#1)** |

Zero-rate features DO appear in top rankings:
- 3PM model: `pts_zero_rate_career` (rank #7, 0.014),
  `pra_zero_rate_L50` (#8, 0.011), `threes_zero_rate_L50` (#11, 0.009)
- AST model: `pra_zero_rate_L20` (#17, 0.008)

**Interpretation:** the features carry real signal and are heavily
used by XGBoost. But because they're highly correlated with the
existing rolling-mean features (pts_L10_mean, reb_L10_mean, …), the
tree model mostly **swaps** importance — it uses them instead of the
rolling means without unlocking new predictive power.

## Segment results — where the signal actually shows

### Starter segment (min_played_L5 ≥ 28) — real improvement

| Stat | base |bias| | dp |bias| | Δ|bias| |
|------|-----------:|---------:|---------:|
| PTS  | 0.181 | **0.124** | **−0.057** |
| AST  | 0.025 | 0.011 | −0.014 |
| PRA  | 0.180 | **0.100** | **−0.080** |
| REB  | 0.012 | 0.020 | +0.008 |
| 3PM  | 0.003 | 0.006 | +0.004 |

**PTS and PRA starter over-projection shrinks meaningfully** — from
+0.18 down to +0.10/+0.12. This is the opposite of what the
opportunity-model sibling did (which hurt starters). Distribution-profile
features capture "how often does this starter actually go HAM" and
correctly dampen the over-projection on starters who historically
have a wider distribution than their rolling mean suggests.

### Bench segment (min_played_L5 < 18) — small regression

| Stat | base |bias| | dp |bias| | Δ|bias| |
|------|-----------:|---------:|---------:|
| PTS  | 0.052 | 0.069 | +0.017 |
| REB  | 0.016 | 0.022 | +0.006 |
| PRA  | 0.047 | 0.071 | +0.024 |

Bench |bias| grows by 0.01-0.02 — roughly trading the starter win.

### Low-line segments — flat or marginally positive

| Stat | Threshold | Δ MAE | Δ |bias| |
|------|-----------|------:|---------:|
| PTS  | ≤5  | −0.006 | **−0.016** |
| PTS  | ≤10 | −0.003 | −0.009 |
| AST  | ≤4  | −0.002 | −0.004 |
| 3PM  | any | ≈ 0 | ≈ 0 |
| REB  | any | ≈ 0 | ≈ 0 |
| PRA  | any | +0.002 | mixed |

Contra the thesis, **zero-rate-aware features do NOT dramatically fix
low-line bias**. The biggest low-line win is PTS (|bias| −0.016) but
it's still in the noise band of a 28k-sample segment. The hypothesis
"the biggest missing signal on low props is how often the player
records zero" wasn't borne out — ECDF probability and the intercept
shift (already shipped) are doing the low-line lifting, not the
projection itself.

## KEEP / REJECT recommendation

**REJECT — keep `vk2_{stat}_distprofile.pkl` INERT.**

Reasons:
1. Global MAE doesn't move on any stat by more than 0.003 — no
   headline win to motivate a production cutover.
2. The starter-bias improvement (Δ|bias| −0.06 on PTS, −0.08 on PRA)
   is interesting but it comes with a bench regression of comparable
   magnitude, so the net is neutral over the full slate.
3. XGBoost prefers the new features over the rolling means (high
   importance rankings) but the output quality is unchanged — so
   they're redundant information carriers, not new information.
4. The real low-line wins already live in the ECDF probability layer
   (shipped 2026-04-23) at 91-99% weighted-|gap| improvement.
   Duplicating that effort inside VK2 projections isn't worth the
   operational complexity of a 175-feature production schema.

## What would change this verdict

1. **Prop-conditional feature selection.** At inference time, pass
   VK2 only the hit-rate feature whose threshold matches the current
   prop line (e.g. pass `pts_hit_20_rate_L50` only when scoring a
   PTS line of 20.5). This would let the model treat it as a
   calibrated prior rather than a noisy near-duplicate of the rolling
   mean. Requires non-trivial changes to the scoring adapter.
2. **Distribution-profile-only probability layer.** Skip the
   projection entirely and use hit-rate features directly to compute
   P(over). That's a different approach (closer to a Bayesian
   prior-blending); evaluate separately if pursued.
3. **Rare-event stats.** STL / BLK / Double-Double aren't in the
   current VK2 stat set but are the pure "zero-rate dominates"
   case. When those models ship, revisit this builder for those
   stats.

## Files
- `services/features/distribution_profile.py` (new)
- `scripts/retrain_nba_vk2.py` (added `--dist_profile`)
- `scripts/evaluate_vk2_distprofile.py` (new)
- `tests/test_distribution_profile.py` (new, 10 tests)
- `reports/vk2_distprofile_eval.md` (per-stat detail)
- `models/vk2_{stat}_distprofile.pkl` (5 sibling pkls, INERT)

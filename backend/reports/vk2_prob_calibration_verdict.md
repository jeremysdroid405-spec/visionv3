# VK2 Probability Calibration — Verdict Summary (2026-04-23)

## What was built
- **Isotonic calibrator per stat** trained on 2024 held-out data, saved as
  `/app/backend/models/prob_calibrator_{pts,reb,ast,3pm,pra}.pkl`.
- **Serving hook** in `services/scoring/adapters/nba_scoring.py`
  (`_predict_vk2_prob_over`) — calibrator applies AFTER Gaussian-CDF raw
  p_over, BEFORE the scored-doc write.
- **Three feature flags** (fail-safe: default ON, explicit opt-out):
  - `VK2_CALIBRATION_ENABLED` (master — controls intercept + prob)
  - `VK2_PROB_CALIBRATION_ENABLED` (controls only prob calibration)
  - `VK2_PROB_CALIBRATION_STATS` (comma-separated whitelist, e.g.
    `"REB,AST,3PM"` → only those stats get probability calibration)

## Projection unchanged — verified
Per spec, `model_projection` is NEVER modified by the probability
calibrator. The isotonic layer only rewrites `p_over`. 17 unit tests
enforce this boundary
(`tests/test_calibration_intercept.py`, `tests/test_calibration_probability.py`).

## Tier movement — zero
Tiers are assigned from **market odds** via
`services/scoring/gates/thresholds.py::resolve_target_tier`, not from
model probability. Probability calibration cannot move a prop between
`safe_haven / front_lines / war_zone`. It only alters gate pass/fail
and edge magnitude inside a tier.

## Weighted-by-volume calibration improvement

`Σ n · |gap|` over every symbolic-line row in
`reports/vk2_prob_calibration.md`:

| Stat | Σn·|gap| raw | Σn·|gap| calibrated | improvement | verdict |
|------|--------------|----------------------|-------------|---------|
| PTS  | 2,553  | 2,519  | **+1.4%** | **tie** (intercept shift already covers PTS) |
| REB  | 4,042  | 1,889  | **+53.3%** | **calibrator WIN** |
| AST  | 8,097  | 3,667  | **+54.7%** | **calibrator WIN** |
| 3PM  | 14,215 | 5,378  | **+62.2%** | **calibrator WIN** |
| PRA  | 1,034  | 1,196  | **−15.7%** | **raw wins** |

## Line-by-line pattern

- **Low lines (high volume):** dramatic wins.
  - 3PM @ 0.5: raw gap **+0.213** → calibrated **+0.056** (3.8× better)
  - AST @ 1.5: raw gap **+0.135** → calibrated **+0.046** (2.9× better)
  - REB @ 2.5: raw gap +0.075 → calibrated +0.028 (2.7× better)
- **Mid lines:** near-zero raw gap; calibrator slightly worsens (but n-weight dominates wins).
- **High lines (low volume):** calibrator worsens.
  - PTS @ 29.5: raw −0.088 → calibrated −0.146
  - AST @ 10.5: raw −0.106 → calibrated −0.157
  - These rows are < 1% of training volume → don't dominate the weighted view.

Root cause: 1-D isotonic pools (p_over, actual_over) pairs across line
regions. Overlapping raw probs (`p_over=0.45` can come from line=4.5
OR line=12.5) force a single monotonic function to compromise. Low-line
sample mass dominates, so mid-high probabilities get bent downward.

## Edge impact (ΔEdge = calibrated − raw)

| Stat | line | ΔEdge on OVER | ΔEdge on UNDER |
|------|------|---------------|-----------------|
| PTS  | 4.5  | −0.053 | +0.053 |
| PTS  | 6.5  | −0.053 | +0.053 |
| REB  | 2.5  | −0.047 | +0.047 |
| AST  | 1.5  | −0.089 | +0.089 |
| 3PM  | 0.5  | −0.158 | +0.158 |
| 3PM  | 1.5  | −0.072 | +0.072 |
| PRA  | 15.5 | −0.011 | +0.011 |

Directional read: raw Gaussian was **over-confident on OVERs at low
lines**. Calibrator deflates that — OVER edge shrinks, UNDER edge
grows by the same amount. This closes real calibration gaps and hurts
edge hunters who were arbitraging the old over-confidence.

## KEEP / REJECT recommendation

| Stat | Recommendation | Why |
|------|----------------|-----|
| PTS  | **Intercept yes, prob no** | +1.4% prob improvement is noise; the -0.094 intercept already fixes PTS's only audit issue. |
| REB  | **Prob calibrator KEEP** | +53% weighted improvement; low-line |gap| drops 2-3×. |
| AST  | **Prob calibrator KEEP** | +55% weighted improvement; worst raw gap (+0.135) closes to +0.046. |
| 3PM  | **Prob calibrator KEEP** | +62% weighted improvement; the smoking-gun gap (3PM@0.5 was +21pp) closes to +5.6pp. |
| PRA  | **Prob calibrator REJECT** | −16% weighted — calibrator trades mid-line gain for high-line loss. |

### Recommended production config

Add to `/app/backend/.env`:
```
VK2_PROB_CALIBRATION_STATS=REB,AST,3PM
```

Leaves the intercept shift active (PTS and PRA) via the master
`VK2_CALIBRATION_ENABLED` flag, and restricts probability calibration
to the three stats where the isotonic layer is a clear net win.

## Files touched / added
- **Added:** `services/scoring/calibration.py`
- **Added:** `scripts/train_prob_calibrators.py`
- **Added:** `models/prob_calibrator_{pts,reb,ast,3pm,pra}.pkl`
- **Modified:** `services/scoring/adapters/nba_scoring.py`
  (one block inside `_predict_vk2_prob_over`; intercept + isotonic
  hooks, no other logic touched)
- **Added:** `tests/test_calibration_intercept.py` (9 tests)
- **Added:** `tests/test_calibration_probability.py` (8 tests)
- **Reports:** `reports/vk2_prob_calibration.md`, this file

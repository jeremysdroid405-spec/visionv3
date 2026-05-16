# Phase 2B v3.2 — Pitcher-Prop Gate-Tuning Audit
**Date:** 2026-05-16
**Slate:** 27 events (today's MLB games)
**Model version:** `MLB_HF_v3.2_phase2b`
**Lineup coverage:** 44 / 84 active v3.2 pitcher props have real `opposing_lineup` (hot-hydrated via `scripts/phase2b_hot_hydrate_live_props.py`)

## Headline finding

**42 v3.2 pitcher props sit in the FL bucket (ref_odds −299 to +149) — ALL fail at least one gate. Zero pitcher picks currently tier into `front_lines`.**

### Reject reason distribution

| Reason | Count |
|---|---:|
| `gate_direction_fail` | 21 |
| `gate_cv_fail` | 12 |
| `gate_hit_rate_fail` | 9 |
| (no `gates_passed`) | 0 |

## Direction fails — these are the model speaking

21 of 42 pitcher props fail direction. The pattern is clear: **on `Hits Allowed OVER` picks, v3.2 nearly always projects fewer hits than the line** — and on `Pitcher Outs` picks the model often projects opposite of the line. This is the model expressing a *real* disagreement with the book, NOT a gate-tuning problem.

Examples (all OVER picks, model says proj < line):

| Player | Stat | Line | v3.2 proj | Diff | Edge |
|---|---|---:|---:|---:|---:|
| Merrill Kelly | Hits Allowed OVER | 6.5 | 3.93 | −2.57 | −37.30% |
| Merrill Kelly | Pitcher K OVER | 4.5 | 2.40 | −2.10 | −34.29% |
| Kyle Freeland | Hits Allowed OVER | 7.5 | 4.79 | −2.71 | (—) |
| Aaron Civale | Hits Allowed OVER | 5.5 | 4.83 | −0.67 | −18.81% |
| Tyler Mahle | Hits Allowed UNDER | 5.5 | 6.07 | +0.57 | −10.06% |

**Recommendation:** Don't tune `direction_gate` for pitchers — strict-inequality enforcement is correct, and v3.2 is actually disagreeing with the book here. Consider auto-flipping to the OPPOSITE recommendation in the UI (these are real UNDER edges hiding behind OVER tickets).

## CV fails — current pitcher CV caps are too tight

12 props fail CV. The current MLB FL `pitcher_strikeouts.cv_max` and `hits_allowed.cv_max` are set conservatively. Several CV-fails have **strong edge** AND **decent HR**:

| Player | Stat | Line | proj | CV | HR | Edge |
|---|---|---:|---:|---:|---:|---:|
| Jack Kochanowicz | Hits Allowed UNDER | 5.5 | 4.43 | 0.449 | 0 | **+14.14%** |
| Randy Vasquez | Pitcher K OVER | 4.5 | 5.23 | 0.523 | 40 | +12.44% |
| Kyle Freeland | Pitcher K OVER | 3.5 | 5.23 | 0.631 | — | **+26.23%** |
| Jack Kochanowicz | Pitcher K OVER | 3.5 | 4.34 | 0.581 | 60 | +9.02% |
| Aaron Civale | Pitcher K OVER | 3.5 | 4.95 | 0.557 | 50 | +3.31% |

**Recommendation candidates:**
1. **Raise `pitcher_strikeouts.cv_max` from current → ~0.60** — this would unlock ~5 picks with strong edge, including the Kyle Freeland +26% K3.5 OVER which is a clean miss right now.
2. **Raise `hits_allowed.cv_max` to ~0.50** — Kochanowicz Hits Allowed UNDER (cv 0.45, edge +14%) would clear.
3. **Note:** several CV-fails have HR=None (Kyle Freeland, Merrill Kelly) — pitcher hit-rate calc has gaps for binary K/H outcomes. Worth a separate fix.

## Hit-rate fails — pitcher HR metric is unreliable for `Pitcher Outs`

9 props fail HR. 6 of them are `Pitcher Outs` props where HR is either None or low (20-40%). The HR metric was designed for binary batter outcomes — for continuous-volume pitcher stats (outs, K's), the over/under historical hit-rate is inherently noisier and less informative.

| Player | Stat | HR | Edge |
|---|---|---:|---:|
| Merrill Kelly | Hits Allowed UNDER | None | **+30.30%** |
| Merrill Kelly | Pitcher K UNDER | None | +27.89% |
| Aaron Civale | Pitcher Outs OVER | 30 | +10.99% |
| Jack Kochanowicz | Pitcher Outs OVER | 40 | +9.76% |

**Recommendation candidates:**
1. **Lower `pitcher_outs.hr_min` from 70 → 50** — would unlock the 3 strong-edge picks with HR 20-40 above.
2. **Add `hr_null_pass=True` for pitcher stats** — when HR cannot be reliably computed (typical for some pitcher-stat combinations), don't gate on HR, rely on projection + edge + CV.
3. Merrill Kelly Hits Allowed UNDER +30% edge with HR=None is the kind of pick the system should NOT be rejecting.

## Concrete unlock — single most impactful change

If you want ONE change to maximize pitcher-prop tier-promotion this slate without diluting quality:

```python
# In /app/backend/services/scoring/gates/thresholds.py::_MLB_FRONT_LINES
"pitcher_strikeouts":  {"cv_max": 0.60, "hr_min": 55.0, "edge_min": 4.0, "tp_min": 50.0},
"pitcher_outs":        {"cv_max": 0.45, "hr_min": 50.0, "edge_min": 4.0, "tp_min": 50.0, "hr_null_pass": True},
"hits_allowed":        {"cv_max": 0.50, "hr_min": 55.0, "edge_min": 4.0, "tp_min": 50.0},
```

(Current values would need to be confirmed; this is a starting point based on observed reject thresholds.)

## How to validate

1. Apply threshold change.
2. Re-run chunked recompute: `POST /api/scores/recompute/mlb/chunked` (~140s).
3. Audit FL tier count for pitcher props — should jump from 0 → ~8-12.
4. Spot-check that newly tiered picks have edge_vs_fair > 4%, HR > 50, CV reasonable.

## Workflow for future tuning

The hot-hydrate script is fully repeatable:

```bash
# 1. Backfill opposing_lineup on live_props (runs ~40s, 368 pitcher props/slate).
cd /app/backend && python scripts/phase2b_hot_hydrate_live_props.py

# 2. Trigger chunked recompute (runs ~140s, ~3.5k props rescored).
curl -X POST "$REACT_APP_BACKEND_URL/api/scores/recompute/mlb/chunked"

# 3. Wait for completion, then audit:
curl "$REACT_APP_BACKEND_URL/api/scores/recompute/mlb/chunked/status"
```

After every slate flush, the hot-hydrate script picks up only the new
unhydrated pitcher props (idempotent — `$or: [exists:false, null]`
filter on `opposing_lineup`). Pass `--reset-all` to force rebuild on
the full set.

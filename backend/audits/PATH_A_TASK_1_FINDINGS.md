# Path A Task 1 — Feature Parity Audit Findings (2026-05-17)

## TL;DR — the handoff hypothesis is REFUTED

> Handoff claim: "~150 features (platoon splits, opp_pitcher, lineup, PA
> windows) are missing during inference but were present in training,
> forcing XGBoost to fatally over-weight `sc_b_r7_wOBA`, inflating
> `total_bases` μ by 2.5×."

The audit + Olson direct trace show:

1. **Zero trained features are structurally missing** at inference. The
   builder (`_build_friction_features`) emits every column in
   `feature_cols[stat]` for all 8 sampled families.
2. **Live and replay code paths produce IDENTICAL μ** for Olson
   `total_bases` 2026-05-06 (μ=2.25 in both). Δμ = 0.0000.
3. Only **6 of 222** features differ between live and replay builds:
   `batter_hand_is_imputed`, `batter_is_lhh`, `matchup_is_imputed`,
   `opp_pitcher_throws_is_imputed`, `opp_pitcher_throws_r`,
   `opposite_hand_matchup` — and they do not move μ for Olson.

So Path A Tasks 2–7 (restoration of "missing" pipelines) **cannot be the
fix** for the inflation reported in the handoff.

## Where the real gaps are

The audit DID find population gaps, just not structural-missing ones.
Ranked by `(live − replay) × stat-families affected`:

| rank | category | weighted gap score | nature |
|---|---|---:|---|
| 1 | `opp_pitcher_quality` (Phase-2A throws) | 700 | hydrated live by `feature_hydration.py`; never in replay |
| 2 | `opposing_lineup` (`lineup_size`) | 300 | pitcher families only; same story |
| 3 | `pa_batter` | 12.3% avg | low pop in BOTH live and replay (training-data sparsity) |
| 4 | `pa_pitcher` | 1.0% avg | low pop in BOTH paths |
| 5 | `statcast_pitcher` | 1.0% in both | training-data sparsity, not pipeline |

Gaps #1 and #2 are real **live↔replay** divergences (worth fixing for
backtest parity). Gaps #3-#5 are **training-data sparsity** that affects
live AND replay equally — restoration cannot help; only retraining on
denser data would.

## What's actually causing the Olson 7.9μ blowout?

Not investigated yet in this audit. Hypotheses worth probing next:

- **H1**: `mlb_replay_engine.replay_one()` calls `_build_friction_features`
  with a different `statcast_features` payload (`cache_row["statcast_self_as_of"]`)
  than `predict()` (which calls `_get_batter_sc_latest(player)`). If the
  cached SC bundle is malformed/missing on 05-06, μ could inflate.
- **H2**: Doubleheader cross-midnight game-log mis-attribution (already
  flagged in the handoff as Earlier Issue #1). The 7.9μ run may have
  scored against the wrong game's actuals.
- **H3**: The 7.9μ figure came from a stale/buggy earlier sweep, before
  Phase 2a/2b leakage guards landed.
- **H4**: `park_factor` multiplier — `mu = raw_pred * park_factor` in
  replay; if park lookup keys differ between live and replay, μ could
  scale.

## Artifacts

- `audits/path_a_feature_parity_audit.py` — auditor (read-only)
- `audits/path_a_feature_parity_audit.json` — full parity matrix
- `audits/path_a_feature_parity_audit.md` — human-readable summary
- `audits/path_a_feature_parity_audit.csv` — spreadsheet form
- `audits/path_a_olson_trace.py` — direct μ trace
- This file — findings summary

## Recommendation

Stop Path A restoration work. The premise is wrong. Decide between:

- **Option A** — Investigate real root cause of the 7.9μ replay
  signature (start with `replay_one` vs `predict()` divergence on Olson
  05-06 specifically, then doubleheader audit). Read-only.
- **Option B** — Skip Path A entirely; proceed to Phase 3 (production
  card extraction). It is independent of Path A and stabilises replay
  scope (cards vs qualified pool). The 8/8 smoke tests for Phase 2c
  already make this safe to ship.
- **Option C** — Restore only Gap #1 + Gap #2 (`opp_pitcher_throws_*`,
  `opposing_lineup`) in the replay path. These ARE real live-vs-replay
  divergences and worth closing for backtest parity — but they will
  NOT meaningfully change μ for batter total_bases (Olson trace proved
  this).

# Replay Markets — OVER/UNDER Coverage Rule

**Decision date:** 2026-05-16
**Decided by:** user
**Status:** PERMANENT

## Empirical finding

The Odds API's **historical** `_alternate` markets return **OVER side only**.
Verified against 1,815 raw outcomes across 7 alt markets on 2026-05-05
(`/app/backend/audits/alt_ladder_audit_b30fec61cfbf.json`):

| Market | Raw outcomes | Players with OVER+UNDER |
|---|---:|---|
| batter_hits_alternate | 408 | 0 / 19 |
| batter_total_bases_alternate | 659 | 0 / 19 |
| batter_runs_scored_alternate | 163 | 0 / 18 |
| batter_rbis_alternate | 258 | 0 / 19 |
| batter_hits_runs_rbis_alternate | 284 | 0 / 18 |
| batter_strikeouts_alternate | 43 | 0 / 18 |
| pitcher_strikeouts_alternate | 156 | 0 / 2 |

The corresponding **standard** (non-`_alternate`) markets DO carry both sides
but on a narrower set of lines (typically a single "main" line per player).

## The rule

| Market type | Sides available | Layer 3 / 4 treatment |
|---|---|---|
| `*_alternate` | **OVER only** (real prices) | Evaluate OVER picks at every rung of the ladder. UNDER is NEVER synthesised at this market type. |
| Standard (e.g. `batter_hits`) | **OVER + UNDER** (real prices) | Evaluate both sides as separate replay rows. |

## Explicitly NOT allowed in Layer 3

- ❌ Deriving UNDER alt prices via no-vig fair-line conversion from the OVER price.
- ❌ Mirroring an OVER+price into a synthetic UNDER row.
- ❌ Pricing UNDER alt lines off the model's `1 - p_over` and the average book vig.

Reason: synthetic UNDER pricing contaminates replay ROI and edge metrics.
Backtested EV must reflect a market the bettor could actually have hit.

## Where this is enforced

- `services/replay/historical_alt_odds_ingest.py::_normalize_event` —
  iterates only outcomes whose `name` is `"Over"` or `"Under"` as
  returned by the API. We **do not** synthesise the missing side.
- Layer 3 (`mlb_replay_engine.py`, when built) **must** branch its
  candidate sourcing on `market_class`:
    - `is_alternate=True` → OVER-only candidates.
    - `is_alternate=False` → OVER + UNDER candidates.

## Future opt-in (NOT enabled)

If we ever want a synthetic-UNDER experimental mode, it must be:
1. Behind an explicit `enable_synthetic_under_alts=True` config flag.
2. Tagged on every output row as `pricing_source="synthetic"`.
3. Quarantined into its own backtest run (separate `mlb_replay_backtest_runs`
   row) so synthetic ROI never mixes with real-pricing ROI.
4. Default OFF.

Until then: real prices only.

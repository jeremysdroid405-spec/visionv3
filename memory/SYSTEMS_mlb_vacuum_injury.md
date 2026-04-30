# MLB Vacuum + Injury-Advantage — Invariants & Regression Tests

## What this is
A regression-test suite locking down 3,828 LOC of vacuum / injury /
injury-advantage code that had ZERO tests on the MLB side prior to
2026-04-30. This is the subsystem that has regressed most frequently
(5 separate re-fixes in the prior 30 days). This suite is the
immune system for it.

## Why it exists
The subsystem had the worst test-coverage-to-churn ratio in the codebase:

| File | LOC | Tests before 2026-04-30 |
|---|---|---|
| `routes/mlb_vacuum.py` | 344 | 0 |
| `services/mlb_injury_vacuum_service.py` | 721 | 0 |
| `services/live_injury_micro_sync.py` | 424 | 0 |
| `services/injury_advantage.py` | 507 | 0 |
| `services/injury_triggered_rescore.py` | 450 | 0 |
| **Total** | **2,446 LOC** | **0** |

Every fix shipped without a test. Every bug came back.

## The 5 locked-in invariants

### INV-1: `compute_injury_advantages(db, sport)` tolerates empty state
Empty `{sport}_prop_scores` OR empty `injuries_normalized` → returns `[]`.
Never raises. Enforced by `test_inv1_compute_injury_advantages_empty_db_returns_empty`.

### INV-2: Every `live-alerts` row has a real lineup delta
Alert rows with neither `lineup_delta >= 1.0` nor `projected_ab_delta >= 0.5`
MUST be dropped before serialization. Placeholder `None` / `0` values do
NOT pass the gate. Enforced by `test_inv2_live_alerts_drop_rows_without_real_deltas`.

### INV-3: Endpoint shape is stable
`GET /api/v3/mlb/vacuum/live-alerts` ALWAYS returns:
  - `success` (bool)
  - `alerts` (list)
  - `count` (int, == `len(alerts)`)
  - `timestamp` (ISO string)

Even on error, even on empty state. Never omits these keys. Enforced
by `test_inv3_live_alerts_shape_always_present`.

### INV-4: Same-team gate + no self-boost
- Advantages fire ONLY when injured player's team == beneficiary's team.
- An injured player NEVER appears as his own beneficiary.

Enforced by `test_inv4_same_team_required` and `test_inv4_no_self_boost`.

### INV-5: One advantage per beneficiary
A beneficiary with N stat-lines on the board yields at most 1 advantage
(best line). Enforced by `test_inv5_dedup_per_beneficiary`.

## Other contract tests

### `_estimate_benefit` pure function
- Tier 3 + primary: returns non-zero bump.
- Unknown tier or unknown rank: returns `{"minutes_bump": 0, "usage_bump": 0}` (never raises).

### HTTP contracts
- `GET /v3/mlb/vacuum/active` returns `{count, vacuums, timestamp}`.
- All 3 GET endpoints send `Cache-Control: no-cache, no-store` headers
  (mobile staleness bug regressed twice).
- `POST /v3/mlb/vacuum/clear/<nonexistent>` returns 404, not 500.

### `_get_recency_window`
Falls back to `RECENCY_DEFAULT_HOURS` when `live_scores_cache` is empty
or missing. Never raises, never returns None.

## File locations

| Path | Purpose |
|---|---|
| `tests/test_mlb_vacuum_injury.py` | The 13-test regression suite |
| `/app/memory/SYSTEMS_mlb_vacuum_injury.md` | This doc |

## How the test fixtures work

`seeded_db` fixture creates an isolated per-test state:
- 2 MLB prop scores (beneficiary on team TST, unrelated player on team ZZZ)
- 1 injury row in `injuries_normalized` (injured player on team TST)
- 3 master_hub rows (gives teams + GP so rotation-relevance passes)
- All tagged with `_test_tag=test_pa_<uuid>` so teardown deletes only
  test data, never production rows

Tests that need extra seed state (e.g., `test_inv5_dedup_per_beneficiary`
adds 2 more stat-lines) extend within the fixture's lifetime.

## Running

```bash
cd /app/backend && python -m pytest tests/test_mlb_vacuum_injury.py -v
```

Expected: 13 passed in ~5s. If ANY fail, a P0 regression has landed.
DO NOT skip or xfail the test — fix the code.

## Why these fixes will stick

1. **Invariants are named** (INV-1 through INV-5) with explicit assertion
   messages referencing them. A breaking PR is easy to diagnose.
2. **Tests hit both the engine (`compute_injury_advantages`) AND the HTTP
   surface (`/api/v3/mlb/vacuum/...`)** — catches regressions at either
   layer.
3. **Fixtures clean up on teardown** — test data can't pollute later runs.
4. **Empty-state tested explicitly** — the most common silent breakage
   (returning `None`/`[]`/raising on empty) is caught on the first run.
5. **HTTP-level cache-control + 404-vs-500 tested** — two of the most
   recurrent UX-impacting regressions.

## Next actions (if/when these tests fail)

| Failing test | Likely root cause |
|---|---|
| INV-1 empty-state | Someone added a required field lookup without a null-safe path |
| INV-2 deltas | Someone loosened the `_row_qualifies` filter or moved it |
| INV-3 shape | `live-alerts` error branch omits a key again |
| INV-4 same-team | Injury/pick join broken (team rename, master_hub schema change) |
| INV-5 dedup | `seen_players` set broken or replaced with a different structure |
| 404 test | Route exception handler catches too broadly → returns 500 |
| cache-control | Headers removed or middleware changed |

In ALL cases: fix the ROOT cause, not the test. The test encodes the
contract. If the contract changed, update the test AND the docstring
here, AND add a CHANGELOG entry explaining the new contract.

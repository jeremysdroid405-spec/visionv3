# Phase 1.A.3 — Team Odds Snapshot Loop Design

**Status:** DRAFT — design only, no code shipped. Sign-off required
before any real SGO call.

**Owner module (future):** `workers/team/team_odds_ingest.py`
(currently skeleton-only from Phase 1.A.2).

**SSOT references:**
- Schema: `/app/memory/TEAM_PROPS_ARCHITECTURE.md` §1.2 (`team_live_props`)
- Policy: `backend/services/team_master_hub/ingest_policy.py`
- Worker base: `backend/workers/team/base.py`

---

## 1. State machine

```
            ┌────────────────────────────────────────────────────────┐
            │                                                        │
            ▼                                                        │
   ┌──────────────┐    ┌───────────────┐    ┌────────────┐    ┌──────────────┐    ┌──────────────┐    ┌────────────┐
   │     idle     │ ─▶ │ claim_window  │ ─▶ │   fetch    │ ─▶ │  normalize   │ ─▶ │    write     │ ─▶ │   settle   │
   └──────────────┘    └───────────────┘    └────────────┘    └──────────────┘    └──────────────┘    └────────────┘
        ▲                    │                  │                  │                    │                   │
        │                    ▼                  ▼                  ▼                    ▼                   │
        └────────────────  abort  ◀──────  retry/backoff ────────  drop  ───────  dup-key-skip  ────────────┘
```

Loop tick = one (sport × event_id × snapshot) request. The worker
holds a per-sport cadence governor that produces ticks at the
`max_rpm_per_sport` rate from the policy.

---

## 2. Per-state policy hooks

| State | Policy hook | Behavior |
|---|---|---|
| `idle` | `dispatch_guard_ok()` | Fail-closed: if either env var missing, the loop never leaves `idle`. Heartbeat keeps emitting; no SGO call. |
| `idle → claim_window` | `dry_run_default()` | If `True`, the entire downstream tick is a no-op enumerator (logs the planned URL + would-be unique key, never hits the network). |
| `claim_window` | `max_rpm_per_sport[sport]` | Token-bucket gate. If exceeded, sleep until next 60-s window opens. |
| `fetch` | `retry_count`, `next_backoff_seconds(attempt)` | On HTTP 429 / 5xx / network error: schedule retry. Worst-case wait per request = 0.5+1+2+4+8 = 15.5 s. |
| `fetch` | `should_abort_on_error_rate(n_errors, n_requests)` | Worker tracks a rolling (sport-scoped) counter. Once `n_requests ≥ 20` and error rate ≥ 25%, **abort the whole pass** and emit a structured failure log + audit row. |
| `normalize` | `is_book_blocked(book)` | Hard-drop the row. Logged at INFO with reason. Never reaches `write`. |
| `normalize` | `is_book_reference_only(book)` | Row is INGESTED into `team_live_props` (so the operator can see the line) but tagged `reference_only=True` so downstream optimizer math skips it — same pattern as player side. |
| `normalize` | `should_abort_on_market_explosion(observed, expected)` | After the per-tick payload is fully normalized, if observed markets ≥ 3× the expected per-sport baseline (min 5), **abort the pass**. Prevents silent ingest of new SGO market IDs we haven't mapped. |
| `write` | `dry_run_default()` (re-checked) | Re-evaluated immediately before any `bulk_write`. If True ⇒ skip the write, emit `[dry_run_write]` log only. |
| `write` | n/a (Mongo) | Idempotent `bulk_write(ordered=False)` of `UpdateOne(filter=<unique key>, $set=..., $setOnInsert=..., upsert=True)`. Duplicate-key collisions on the unique index are *expected* on quiet snapshots (line unchanged) and are silently absorbed by `ordered=False`. |
| `settle` | n/a | Emit one structured log per tick: counts, latency, dropped-blocked, dropped-explosion, retries, write outcome. Persist to `team_odds_ingest_runs` (NEW collection — to be added in 1.A.3, NOT now). |
| `settle → idle` | cadence governor | Sleep until next token-bucket slot. |

---

## 3. Write target — `team_live_props` (§1.2)

| Field | Source | Notes |
|---|---|---|
| `event_id`        | SGO event payload | shared with player pipeline |
| `team_id`         | resolver via `team_master_hub.display_names` | seeded in 1.A.1 |
| `market`          | normalizer (canonical name) | from `_PLANNED_ENDPOINTS[sport].markets` |
| `line`            | SGO outcome.point | float, never str |
| `side`            | normalizer (`OVER`/`UNDER`) | upper-case, never lowercase |
| `book`            | SGO bookmaker.key | lower-case |
| `odds`            | SGO outcome.price | int American odds |
| `is_alternate`    | derived | true when `market` includes `_alternate` |
| `snapshot_iso`    | worker clock at fetch start | UTC ISO-8601 |
| `commence_time`   | SGO event.commence_time | |
| `game_date`       | derived from `commence_time` (UTC date) | |
| `home_away`       | `team_matchups` lookup | populated post-1.A.4; null pre-1.A.4 |
| `reference_only`  | `is_book_reference_only(book)` | optimizer-side gate |
| `sport`           | constructor arg | |
| `ingested_at`     | server clock at write | UTC dt |

## 4. Compound unique key (§14.5 multi-book invariant)

```
(event_id, team_id, market, line, side, book, snapshot_iso)
```

This is **already created by `ensure_team_collections`** in 1.A.2
under index name `ix_live_prop_compound_unique`. The snapshot loop
must filter on the FULL key in every `UpdateOne` — never a subset —
to preserve multi-book history. Regression test pinned in
`test_team_collections_phase_1_a_2.py::test_unique_index_blocks_duplicate_multi_book_row`.

---

## 5. Dry-run behavior

When `dry_run_default()` returns True (the default in preview and
even on prod until `TEAM_INGEST_LIVE=1` is explicitly set):

- SGO **HTTP requests are still executed** (so we can detect schema
  drift, 401s, and rate-limit issues without committing data).
  *Override:* `TEAM_INGEST_OFFLINE=1` env var skips the HTTP call
  entirely. Reserved for first-day smoke testing on prod.
- After normalization, the worker logs `[dry_run_tick] sport=… ev=…
  candidate_writes=N blocked=K refs=R explosion_aborted=False`.
- **No `team_live_props` write occurs.**
- The settle row in `team_odds_ingest_runs` carries `mode="dry_run"`
  so downstream dashboards filter cleanly.

---

## 6. Failure behavior

| Failure | Action |
|---|---|
| `dispatch_guard_ok() == False` | Worker logs once at INFO per 60 s, keeps heartbeating, **never advances past `idle`**. |
| `team_master_hub` empty for the sport | Hard-abort with `reason=master_hub_empty`. Operator must run the Phase 1.A.1 seed first. |
| Single fetch error (timeout, 5xx, 429) | Retry per `next_backoff_seconds`; max `retry_count` attempts; if still failing, count toward error-rate window. |
| Error rate ≥ 25% on ≥ 20 reqs | **Abort the entire pass.** Emit structured log + audit row with verbatim `should_abort_on_error_rate` reason. Worker re-enters `idle`; next pass starts fresh. |
| Observed markets ≥ 3× expected (≥ 5 observed) | **Abort the entire pass** with verbatim `should_abort_on_market_explosion` reason. Highest-priority signal: SGO added new market IDs and we'd otherwise pollute `team_live_props` with un-graded rows. |
| Mongo `bulk_write` `DuplicateKeyError` on a single row | Already absorbed by `ordered=False` + `upsert`. Counted into a per-tick `n_dup_collisions` metric but never escalates. |
| Mongo connection lost mid-write | Bubble up; worker abort + restart by supervisor; next pass retries idempotently. |

---

## 7. Audit / log fields

**Per-tick INFO log line (one per snapshot):**
```
[team_odds_tick] sport=mlb event_id=… mode=<dry_run|live>
                 status=<ok|aborted_rate|aborted_explosion|guard_closed>
                 n_fetched=… n_normalized=… n_blocked=… n_refs=…
                 n_writes=… n_dup_collisions=… latency_ms=…
                 retries=…
```

**Per-pass row → `team_odds_ingest_runs` (NEW coll, lands in 1.A.3):**
```
run_id, sport, started_at, ended_at, mode (dry_run|live),
n_ticks, n_events, n_writes, n_blocked, n_refs,
n_aborted_rate, n_aborted_explosion,
n_dup_collisions, n_retries,
status (succeeded|aborted_rate|aborted_explosion|guard_closed|errored),
diagnosis (verbatim kill-switch reason if aborted),
worker_version, policy_snapshot (full policy_summary() output)
```

The full `policy_snapshot` lets us reconstruct, months later, the
exact RPM / retry / backoff config a given pass ran under — same
lesson as the player-side `model_version` + `gate_config_version`
audit.

---

## 8. Conditions that ALLOW live writes (ALL required)

1. `SGO_API_KEY` env var set
2. `TEAM_INGEST_ENABLED=1`
3. `TEAM_INGEST_LIVE=1`     ← flips `dry_run_default()` to False
4. `team_master_hub` has ≥ 1 doc for the worker's sport
5. `team_live_props` index spec matches `COMPOUND_UNIQUE_KEYS["team_live_props"]`
   (re-verified at startup)
6. `policy_diff()` returns either `is_default=true` OR every override
   is logged as a startup INFO line (visibility — overrides allowed,
   but never invisible)

Any failure short-circuits the worker back to `idle` with the
specific failure reason in the heartbeat row.

---

## 9. Conditions that ABORT live writes mid-pass

1. **Error rate ≥ 25%** over ≥ 20 requests in the rolling window
   (`should_abort_on_error_rate`).
2. **Market explosion** — observed ≥ 3× expected and observed ≥ 5
   (`should_abort_on_market_explosion`).
3. **Master hub mutated mid-pass** — if `team_master_hub.count_documents`
   for the sport drops to 0 during the pass, abort with
   `reason=master_hub_drained`. Defends against an accidental
   coll-drop fat-finger.
4. **Operator kill switch** — `team_odds_ingest_runs.cancel_requested=true`
   for the current `run_id` flips the loop into `idle` at the next
   state-machine boundary (checked between `fetch → normalize → write`).
5. **`dispatch_guard_ok()` flips to False mid-pass** (e.g. operator
   unsets `TEAM_INGEST_ENABLED`): the next state transition aborts
   with `reason=guard_closed_mid_pass`.

---

## 10. Testing plan BEFORE any real SGO call

**Tier 1 — pure unit (must pass first):**
- Sport-agnostic state-machine transitions, every branch + abort path
- Token-bucket cadence math (rpm cap obeyed)
- Backoff math against retry path
- Kill-switch decisions echo `ingest_policy.should_abort_*` exactly
- `home_away` left null when `team_matchups` empty (pre-1.A.4 contract)

**Tier 2 — Mongo integration (no network):**
- Idempotent bulk_write semantics: 2-pass with same payload →
  `modified_count=0` on the second pass (mirrors Phase 1.A.1 pattern)
- Multi-book preservation: 11 books on the same `(event_id, team_id,
  market, line, side, snapshot_iso)` → 11 distinct rows
- `is_book_blocked` row is dropped, never written
- `is_book_reference_only` row IS written with `reference_only=True`
- `team_master_hub` resolver path: unknown team name ⇒ row skipped
  with `reason=team_id_unresolved` (never written)

**Tier 3 — synthetic SGO payload replay (still no network):**
- Replay a snapshot of recorded SGO responses (committed to
  `backend/tests/fixtures/team_odds/`) and assert the exact rows
  written match a golden snapshot
- Replay a payload with a market we haven't mapped → assert
  market-explosion abort fires correctly
- Replay a 50%-error mixed payload → assert error-rate abort fires
  AFTER min-sample threshold

**Tier 4 — dry-run live ingest (HTTP allowed, NO writes):**
- `TEAM_INGEST_ENABLED=1`, `SGO_API_KEY=<test_or_real>`,
  `TEAM_INGEST_LIVE` UNSET → real SGO calls happen, dry-run log
  lines emit, `team_live_props.count_documents({}) == 0` afterwards.
- Confirm the kill-switches behave on real upstream data.

**Tier 5 — single-event live ingest (writes enabled, narrow scope):**
- `TEAM_INGEST_LIVE=1` on preview pod ONLY, with `--event-id`
  flag to limit to ONE event for ONE sport
- Verify `team_live_props` rows match the SGO payload via
  `_resolve_team_id` round-trip
- Verify multi-book preservation (≥ 5 books per
  (event/market/line/side))
- Operator manually drops the rows after audit

**Tier 6 — bounded production run (gated):**
- Only after Tiers 1-5 pass and the operator signs off on the
  golden snapshots, lift the `--event-id` constraint to a single
  sport+date window.
- Auto-aborts via the kill switches are now the safety net.

**Promotion to full prod ingest** requires:
- All 6 tiers green
- A diff between policy_diff() and a snapshot taken at Tier 4
- One full week of Tier 5/6 runs with zero unexpected aborts

---

## Sign-off

This document is informational only. No code lands until the
operator approves §§1-10. After approval:
1. Implement Tier 1 + Tier 2 tests (still no network).
2. Implement the worker loop against those tests.
3. Stop. Get re-approval before Tier 3+.

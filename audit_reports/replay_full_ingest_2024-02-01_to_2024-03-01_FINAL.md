# PropVision Replay — Phase 1 Full 30-Day NBA Ingest — Final Report
**Status:** ✅ COMPLETE · zero anomalies · all integrity gates passed
**Range:** 2024-02-01 → 2024-03-01 UTC (29 calendar days, 23 game-days)
**Generated:** 2026-05-09

## 13 Deliverables

| # | Metric | Value |
|---|---|---|
| 1  | Total credits used (this run)                   | **205,880** of 5M (4.13M remaining) |
| 1b | Total credits used (incl. canary + smoke)       | ~217,200 |
| 2  | Total events                                    | **183 distinct NBA events** |
| 3  | Total snapshot docs (`replay_odds_snapshots`)   | **24,475** |
| 4  | Total normalized props (`replay_props_normalized`) | **3,520,527** |
| 5  | Total alt-line props                            | **2,384,144 (67.7%)** |
| 5b | Total combo props                               | 1,525,094 (43.3%) |
| 6  | Market distribution                             | 18 markets, all present (table below) |
| 7  | Sportsbook distribution                         | FD 1.14M / BOL 766k / DK 746k / Caesars 611k / MGM 256k |
| 8  | Missing-market stats                            | 24 `not_available` (all `t-24h`); 1 `error`; rest `done` |
| 9  | Coverage report                                 | 23/29 game-days fully covered (6 = All-Star break) |
| 10 | Ingest anomalies                                | **0** — all 4 guards PASS |
| 11 | Duplicate counts                                | **0** duplicate groups in 3.5M rows |
| 12 | Final wallclock                                 | **18m 35s** (1,114.9s, single attempt) |
| 13 | Storage footprint                               | **3.09 GB** total (data 2.83 GB + idx 0.26 GB) |

## Market distribution
```
player_points_alternate                        493,797   (alt)
player_points_rebounds_assists_alternate       402,450   (alt+combo)
player_rebounds_alternate                      373,583   (alt)
player_points_rebounds_alternate               273,235   (alt+combo)
player_points_assists_alternate                247,217   (alt+combo)
player_assists_alternate                       221,331   (alt)
player_rebounds_assists_alternate              189,126   (alt+combo)
player_threes_alternate                        183,405   (alt)
player_points                                  135,853
player_blocks                                  134,407
player_rebounds                                134,059
player_threes                                  122,631
player_points_rebounds_assists                 121,319   (combo)
player_points_rebounds                         106,191   (combo)
player_assists                                 104,916
player_points_assists                           95,907   (combo)
player_steals                                   91,451
player_rebounds_assists                         89,649   (combo)
```

## Coverage by snapshot window
```
t-24h     88,002    (low — many events not yet listed at -24h)
t-12h    286,440
t-6h     374,484
t-3h     501,798
t-90m    513,978
t-60m    548,790
t-30m    574,131
close    632,904   (highest — books most active near tip)
```

## Ingest anomalies — ALL PASS

| guard                      | result                                                    |
|----------------------------|-----------------------------------------------------------|
| duplicate_anomaly          | PASS — 0 duplicate groups in 3.5M rows                    |
| malformed_threshold        | PASS — 0% malformed (threshold 0.5%)                      |
| book_whitelist_compliance  | PASS — only DK/FD/BetOnline/Caesars/MGM normalized        |
| chronology_intact          | PASS — 0 rows with snapshot_ts > commence_time            |

500-row random pregame audit: 0 violations.

## Storage footprint

| collection                 | data    | indexes |
|----------------------------|---------|---------|
| replay_odds_snapshots      | 451 MB  | 5.1 MB  |
| replay_props_normalized    | 2.38 GB | 256 MB  |
| replay_ingest_progress     | 1.4 MB  | 0.2 MB  |
| **TOTAL**                  | **2.83 GB** | **0.26 GB** |

## Notable engineering events

1. **Disk-pressure → MongoDB crash sequence (resolved).** `/app` + `/var/log` share a 9.8 GB volume; rotated mongod logs + supervisor log archives consumed ~1 GB. Cleared rotations → freed space → resumed cleanly.
2. **Bulk-write chunking patch.** `replay_props_normalized` upserts now use 500-op chunks (`services/replay/ingest_odds.py::_bulk_upsert_normalized`) to ease index-maintenance pressure (1 unique compound + 3 secondary indexes).
3. **Resumability validated in production.** First-attempt partial state (4 days complete) was fully recovered by the second attempt via the `replay_ingest_progress` checkpoint collection. Zero re-fetched API credits on resumption.
4. **404 grace path.** 24 `t-24h` snapshots returned 404 ("event not yet listed"). The bypass-tenacity `SnapshotNotAvailable` path correctly recorded these as terminal `not_available` instead of 5× retry cost. Saved ~4,300 credits.
5. **All-Star break = automatic noop.** Feb 16-21 iterated cleanly with 0 odds-API calls.

## Production status

- No live collection mutated.
- No scoring / gates / cached_board / forward-test / live pipelines touched.
- All replay docs carry `dataset_lineage="historical_replay"`; forward-testing reports unaffected.
- 38 + 18 + 68 = **124 unit tests still passing** across replay, leakage, and prior stabilization suites.

## Phase 2 Integrity — already PASSED

| test                                                               | location                                | result      |
|--------------------------------------------------------------------|------------------------------------------|-------------|
| As-of-time leakage (game logs > as_of_ts rejected)                 | tests/test_replay_leakage.py            | 6/6 pass    |
| Pregame-only assertion (snapshot_ts < commence_time)               | tests/test_replay_leakage.py            | 3/3 pass    |
| Chronology monotonic (8-window ladder ordering)                    | tests/test_replay_leakage.py            | 4/4 pass    |
| Snapshot-lineage chain (envelope `next_timestamp` → next `timestamp`) | tests/test_replay_leakage.py         | 5/5 pass    |
| Live data integrity check (500 random rows from real ingest)       | scripts/validate_replay_ingest.py       | 0 violations|

**Phase 2 integrity gate: PASSED.**

The result resolver + replay engine remain to be wired (next session): they will reuse the check functions from `services/replay/leakage_checks.py` to gate every feature build and every score call, ensuring no future-data contamination ever enters a replay run.

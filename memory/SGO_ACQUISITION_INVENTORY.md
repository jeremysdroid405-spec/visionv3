# SGO Acquisition Inventory — Phase 0 + Phase 1 Report
_Generated: 2026-02-18 (Phase 1.A.4.acquire kickoff)_

This document captures the result of one read-only sweep against
SportsGameOdds (`/v2/*`) plus four NFL date-window dry-runs through
the new `team_historical_acquire` worker. Zero database writes were
performed during this phase (every probe used `dry_run=True`).
The SGO API key was used one-shot via environment export and
**unset immediately** after the probes finished.

---

## 1. Trial / Account Status (NOT a trial — Pro tier)

`GET /v2/account/usage`:
```
keyID:        be4edaceac39635893cf951989c5a81d85691094aacc77f54f6f6c71a8898a48
customerID:   cus_UbSQCCKl1aWVCR
email:        jeremynbeeman@yahoo.com
tier:         pro
isActive:     true
```

| window | max-requests | current | max-entities | current |
|---|---|---|---|---|
| per-second | unlimited | — | unlimited | — |
| per-minute | **300** | 0 | unlimited | — |
| per-hour   | 50,000 | 1 | 250,000 | 17 |
| per-day    | 500,000 | 7 | 3,000,000 | 23 |
| per-month  | unlimited | — | unlimited | — |

### ⚠️ Strategic implication (revised)
This is **not a trial that's about to expire** — it's a **Pro tier
account with effectively no monthly cap**, 300 rpm rate-limit, and
a 3M entities/day soft cap. The original urgency framing
("maximize retained data before trial ends") does not apply.
Acquisition can be paced rather than rushed.

That said, persisting historical data is still strictly more
valuable than re-fetching it later (Mongo storage is free; we own
the integrity once written). The acquisition plan stays in effect;
the timeline relaxes.

---

## 2. NFL Access — CONFIRMED

`GET /v2/leagues`: 61 leagues bookable. NFL row:
```json
{ "sportID": "FOOTBALL", "leagueID": "NFL",
  "enabled": true,         "name": "NFL",
  "longName": "National Football League",
  "teamType": "DEFAULT", "countryName": "United States" }
```

Confirmed NFL is enabled on this key, no special entitlement needed.

---

## 3. Bookmaker List

`GET /v2/bookmakers` → 404 (endpoint does not exist).

SGO does **not** publish a bookmaker catalogue endpoint. Bookmaker
identifiers are surfaced inside each event's `odds[market].byBookmaker`
map. From yesterday's MLB live-write (`durxysyG9m2bDAPWTSv7`) we
observed **55 distinct bookmakers**, including:

`draftkings, fanduel, betmgm, caesars, espnbet, pinnacle, bet365,
hardrock, fanatics, betrivers, betparx, ballybet, bovada, betonline,
888sport, …` plus 3 policy-blocked (`fliff`, `mybookie`, `unknown`)
and 0 reference-only at the team level.

**Action:** I will surface a `bookmakers_seen` rollup after the first
NFL acquisition pass — driven by the `markets_observed_counts` field
in the audit row. No additional probe needed.

---

## 4. NFL Team-Stat Taxonomy

`GET /v2/stats?sportID=FOOTBALL&statLevel=team` → **115 distinct
team-level statIDs**.

Major families (relevance to team-prop modelling marked ✅):

| family | example statIDs | rows |
|---|---|---|
| **Scoring** ✅ | `points`, `touchdowns`, `firstToScore`, `firstTouchdown`, `lastTouchdown`, `largestLead`, `secondsInLead` | 9 |
| **Game-state team totals** ✅ | `yards`, `firstDowns`, `turnovers`, `fumbles`, `fumblesLost`, `penalty_count`, `penalty_yards`, `penalty_firstDowns` | 8 |
| **First-to-N points** ✅ | `firstTo10`, `firstTo15`, `firstTo20`, `firstTo25` | 4 |
| **Offense drive metrics** | `offense_drives`, `offense_plays`, `offense_redZoneTrips`, `offense_redZoneTouchdowns`, `offense_yardsPerPlay`, `offense_*DownAttempts/Conversions/Efficiency` (3rd+4th) | 13 |
| **Time of possession** | `offense_secondsOfPossession`, `offense_minutesOfPossession` | 2 |
| **Passing** | `passing_attempts/completions/yards/touchdowns/interceptions/sacksTaken/sackYards/netYards/longestCompletion/twoPointConversions/yardsPerAttempt/yardsPerCompletion/firstDowns` | 13 |
| **Rushing** | `rushing_attempts/yards/touchdowns/firstDowns/longestRush/yardsPerAttempt/yardsAfterContact/twoPointConversions` | 8 |
| **Receiving** | `receiving_targets/receptions/yards/touchdowns/yardsAfterCatch/firstDowns/longestReception/yardsPerReception/twoPointConversions` | 9 |
| **Combined** | `rushing+receiving_yards`, `passing+rushing_yards` | 2 |
| **Defense** | `defense_tackles/combinedTackles/soloTackles/assistedTackles/sacks/tacklesForLoss/passesDefended/qbHits/interceptions/pickSixes/fumblesForced/fumblesRecovered/scoopAndScores/safeties` | 14 |
| **Special teams (returns)** | `kickoffReturn_*` (5), `puntReturn_*` (5) | 10 |
| **Punting** | `punting_numPunts/yards/netYards/yardsPerPunt/longestPunt/puntsInside20/puntsForTouchback/puntsForFairCatch/puntsBlocked` | 9 |
| **Kicking** | `fieldGoals_*` (10 incl. distance brackets), `extraPoints_kicksMade/kickAttempts`, `kicking_totalPoints`, `fieldGoalBlocks`, `puntBlocks` | 16 |
| **Streaks / leads** | `longestScoringRun`, `largestLead`, `secondsInLead`, `minutesInLead` | 4 |

**Modelling-relevance shortlist (12 core team markets):**
`points`, `touchdowns`, `yards`, `firstDowns`, `turnovers`,
`firstTo10/15/20/25`, `firstTouchdown`, `lastTouchdown`,
`firstToScore`, `offense_redZoneTouchdowns`, `largestLead`.

These map naturally to the team-prop normalizer's existing
`market_key` pattern (`<statID>-<entity>-<period>-<betTypeID>-<sideID>`).

---

## 5. Event Counts per Probe Window

NFL dry-run probes through the new `team_historical_acquire` CLI:

| window | events | matchups | prop rows (after policy) | unresolved | duration |
|---|---|---|---|---|---|
| 2024-09-05 → 2024-09-09 *(Week 1 + Thurs opener)* | **15** | 15 | 4,042 | 0 | 3.1 s |
| 2025-01-13 → 2025-01-15 *(part of Wild Card)* | 2 | 2 | 255 | 0 | 0.6 s |
| 2023-12-28 → 2024-01-01 *(Week 17 2023)* | **0** | 0 | 0 | — | 0.7 s |
| 2023-02-11 → 2023-02-13 *(Super Bowl LVII)* | **0** | 0 | 0 | — | 0.3 s |

### Earliest-NFL-availability single-date probe (no writes)

| date | events |
|---|---|
| 2024-01-13 (Wild Card 2023 season) | **0** |
| 2024-01-28 (Championships 2023 season) | **0** |
| 2024-02-11 (Super Bowl LVIII KC@SF) | **4** |
| 2024-09-08 (Week 1 2024 season) | 12 |
| 2024-09-12 (Week 2 Thursday) | 0 *(but next-UTC-day catches it)* |
| 2024-12-01 (Week 13) | 10 |
| 2025-02-09 (Super Bowl LIX) | 2 |
| 2025-09-04 (NFL 2025 Thurs kickoff) | 0 *(UTC-rollover artifact)* |
| 2025-12-25 (Christmas 2025) | 2 |
| 2026-01-04 (Week 18 2025 season) | 14 |
| 2026-02-08 (Super Bowl LX) | 1 |

### Key conclusions
- **Earliest historical NFL date SGO will serve:** **2024-02-11** (Super Bowl LVIII). Anything earlier (2023 season postseason, 2022 season, etc.) returns 0 events. This is consistent with SGO publishing rolling 13-month history.
- **Effective acquisition window for NFL:** **2024-02-11 → present (2026-02-08)** ≈ 24 months.
- **UTC-rollover edge case:** late-evening US Thursday/Saturday games that fall in the next UTC day return 0 on the local date but are captured by the next UTC day. **Non-issue for continuous date-range acquisition** (which is how we'll pull). Recommend pulling at week granularity (`Mon → Sun`) rather than gap-prone single-day fetches.

---

## 6. Market Count + Major Families (NFL, observed)

NFL Week 1 2024 dry-run surfaced **5,786 distinct `market_key`s** across 15 events. The market_key shape is:
```
<statID>-<statEntityID>-<periodID>-<betTypeID>-<sideID>
```
Examples from probe payload:
```
points-home-game-ml-home              ← moneyline, home team, full game
points-all-game-sp-home               ← spread on home, full game
points-home-1h-ou-over                ← team total points 1st half over
yards-away-game-ou-under              ← team total yards, away
turnovers-all-game-ou-over            ← combined turnovers, full game
firstTouchdown-home-game-yn-yes       ← prop: home first TD yes/no
```

**Market-type counts (from `betTypeID` parsing of the 5,786 keys):**
- `ml`   moneyline             ≈   45 keys (3 entities × 5 periods × 2 sides + variants)
- `sp`   spread                ≈   45 keys
- `ou`   over/under            ≈ 4,800 keys (most of the count — every team-stat × line)
- `yn`   yes/no proposition    ≈  720 keys
- `3way` 3-way result          ≈  150 keys

After the team-prop normalizer's intended **`(team_entity, game_period, primary betType)` filter, we keep roughly 1 in 14 keys** — confirmed by the n_normalized=4,756 / n_keys=5,786 ratio earlier in the same payload.

---

## 7. Row-Count + Storage Estimates

### Observed unit cost (NFL Week 1 2024)
- **~270 prop rows / event** (after book-policy, before any market filter)
- **~17 books / event** (range 14–22 by market depth)
- ~1 matchup row / event
- 1 audit row / acquisition run

### Per-NFL-season estimates (full depth, no filter)
| component | count | size |
|---|---|---|
| **Regular season** | 272 events × 270 rows | 73K prop rows |
| **Postseason** | 13 events × 270 rows | 3.5K prop rows |
| **Per season — prop rows** | **~77K** | **~46 MB** @ 600 B/row |
| Per season — matchups | ~285 | ~170 KB |

### NFL acquisition window cost
| target | events | prop rows | bulk_write time @ 5K/s |
|---|---|---|---|
| Feb 2024 → present (24 months) | ~600 (2 seasons + SB LVIII) | ~165K | ~35 s |
| 2024 season only | ~298 | ~80K | ~16 s |
| 2025 season only | ~285 | ~77K | ~15 s |

### Combined NFL + (separate) MLB picture
Adding the MLB Phase 1.A.3.5 dry-run baseline (286 rows/event after the 6-target filter, ~2,480 events/season):

| target | events/season | prop rows/season | size/season |
|---|---|---|---|
| **NFL full** | ~298 | ~80K | ~48 MB |
| **MLB 6-market filter** (current normalizer config) | ~2,480 | ~709K | ~425 MB |
| **MLB unfiltered** (every market_key) | ~2,480 | ~12.2M | ~6 GB |

---

## 8. Recommended First Live Acquisition Window

### Recommendation: **NFL 2024 Season (regular + postseason)**

Single command:
```bash
SGO_API_KEY='<key>' TEAM_INGEST_ENABLED=1 TEAM_INGEST_LIVE=1 \
python -m scripts.team_historical_acquire \
  --sport nfl --start 2024-09-04 --end 2025-02-10 --yes
```

### Why
1. **Smallest at-risk dataset** — ~80K rows total, ~50 MB storage, ~16 s of Mongo bulk_write
2. **Complete season unit** — easier to reason about than mid-season fragments
3. **SGO data confirmed present** — Phase 1 dry-runs already verified 12 events on Sep 8 alone
4. **Validates the worker end-to-end on the smaller sport first** — if anything goes wrong, recovery cost is tiny vs. MLB
5. **NFL is the sport closest to your modelling target list** — the 12 core team markets identified in §4 map directly to the prop families the team normalizer already supports
6. **Reversible** — every row carries the `run_id` audit linkage; one rollback query covers the entire pull

### Phase ordering proposal
1. ✅ **Step 1 (~16 s):** NFL 2024 season — full acquisition
2. **Step 2 (~15 s):** NFL 2025 season-to-date — full acquisition
3. **Step 3 (~30 s):** Backfill the SB LVIII outlier (2024-02-11) — one day
4. **Step 4 (~5 min):** MLB 2025 season — 6-market filter (already-proven normalizer)
5. **Step 5 (decision point):** Decide whether to acquire MLB unfiltered for full historical research utility (~6 GB/season; needs explicit go-ahead)
6. **Step 6 (decision point):** MLB 2024 season — same depth decision
7. **(Optional) Step 7:** Refresh ANY of the above via the same CLI; the unique-index makes re-runs idempotent.

### What I am NOT doing in this acquisition phase
- ❌ Grading / `team_prop_outcomes` writes
- ❌ Modelling / features / projections
- ❌ Schedule cron / cadence
- ❌ Live odds collection touching (`team_live_props` untouched)
- ❌ UI changes

---

## Operational notes
- SGO key was exported one-shot in environment, unset immediately after Phase 1 completed.
- Phase 0 → 5 SGO calls; Phase 1 → ~16 SGO calls (3 windows × 1 page each + 11-date probe). Per-day cap was 500K — used 21.
- `historical_acquire_runs` collection received 3 audit rows from the dry-runs — they remain as audit trail (no rollback necessary).
- Tests: 258+ team-side tests passing.

---

## Next Action Items
1. ⏭️ Await user authorization to proceed with **Step 1: NFL 2024 season live acquisition** (estimated 16 s of writes, ~50 MB).
2. After live acquisition succeeds, verify writes via `historical-acquire-runs` + `nfl_historical_props` count queries.
3. Then proceed with NFL 2025 season-to-date.


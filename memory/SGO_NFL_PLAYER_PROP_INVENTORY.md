# SGO NFL Player-Prop Inventory — Phase 3 Report
_Generated: 2026-02-18 (Phase 1.A.4.acquire / Phase 3)_

Read-only sampling pass against `/v2/events?startsAfter=…&startsBefore=…`
for the NFL date with the densest event count (2024-09-08, Week 1
Sunday — 12 events). Zero database writes.

---

## 1. Earliest / Latest dates available
Per Phase 2 of the original NFL probe: **2024-02-11 → present (2026-02-08)**.
The same 24-month window applies for player props (player props ride
on the same `/v2/events` payload — single fetch returns team + player
markets together).

## 2. Seasons available
- 2023 season postseason (only the **SB LVIII**, 2024-02-11)
- **2024 season** — preseason → SB LIX (full season)
- **2025 season** — preseason → SB LX (full season, completes today 2026-02-08)

## 3. Books available (player props specifically)

From the 2024-09-08 sample (12 events, 4,524 player-prop market_keys):
**47 distinct bookmaker IDs surfaced**:

```
888sport, ballybet, bet365, betfairexchange, betmgm, betonline, betparx,
betrivers, betsson, betway, bluebet, bookmakereu, bovada, caesars,
casumo, circa, coolbet, draftkings, espnbet, fanatics, fanduel, fliff*,
fourwinds, grosvenor, gtbets, hardrockbet, leovegas, livescorebet,
matchbook, mrgreen, mybookie*, nordicbet, paddypower, pinnacle,
pointsbet, prizepicks**, prophetexchange, si, sportsbetting_ag,
sporttrade, superbook, tab, tabtouch, thescorebet, underdog**, unibet,
unknown***
```
- `*` blocked under existing book policy
- `**` reference-only books (no real money, simulated lines)
- `***` aggregator closing-line bucket — populates when other books have closed/settled

## 4. Total rows estimate
From single-event sampling:

| metric | observed (Sep 8 2024, 12 events) | per-event |
|---|---|---|
| player-prop market_keys | 4,524 | **377 avg / 448 max / 316 min** |
| team-prop market_keys (comparison) | 892 | 74 avg |
| total byBookmaker outcomes scanned | 19,427 | ~1,619 |

NFL has 606 distinct events in our acquired 24-month window. If
player-prop density per event holds:
- **Raw player-prop rows (no policy):** ~606 × ~1,500 outcomes = **~900K – 1.2M rows**
- After ~12% book-policy block: ~800K – 1.05M rows

Real number depends heavily on how many books archive closing lines
vs how many roll up to `unknown`. Closed historical markets often
collapse to a single `unknown` outcome (one entry per market).
Live/future events show 10–30 books per market.

## 5. Unique market count
From the same 12-event sample:
- **4,524 distinct player-prop market_keys** (one event-day)
- ~377 distinct **player_id-keyed market positions** per event
- 23 distinct **stat-family prefixes** (see §7)

Across the full 606-event 24-month window the unique key count will
be much larger (each player adds their own family of keys) — but
the **23 stat families remain constant** across the entire NFL
player-prop universe.

## 6. Standard vs Alternate market availability

SGO does NOT split standard vs alternate at the market_key level.
The `isAlternate` flag lives **inside each byBookmaker outcome**
(boolean). One market_key can host multiple lines via the same
`statEntityID`+`betTypeID`+`sideID` triplet but different `line`
values across `byBookmaker` entries. The team-prop normalizer
already handles this via the `(event_id, team_id, market, **line**,
side, book, snapshot)` compound unique key — same pattern will
apply for player props with `team_id` replaced by `player_id`.

**Observed in sample:** every market_key has both standard
(`isMainLine=true`) AND alternate lines surfaced from the same book.
The byBookmaker dict can carry multiple book entries each with
their own line — our normalizer correctly emits one row per
(book, line) tuple already.

## 7. Stat families (23 distinct, NFL player props)

Sorted by frequency (one Sunday slate):

| stat family | freq | bet types |
|---|---|---|
| `touchdowns` | 1,114 | yn (any TD scorer), ou (count) |
| `firstTouchdown` | 546 | yn (yes/no) |
| `lastTouchdown` | 486 | yn (yes/no) |
| `receiving_yards` | 278 | ou |
| `passing_interceptions` | 266 | ou |
| `receiving_receptions` | 244 | ou |
| `defense_combinedTackles` | 238 | ou |
| `receiving_longestReception` | 236 | ou |
| `rushing+receiving_yards` | 202 | ou (combined) |
| `rushing_yards` | 162 | ou |
| `defense_sacks` | 130 | ou |
| `rushing_attempts` | 118 | ou |
| `rushing_longestRush` | 100 | ou |
| `points` | 42 | yn (kicking) |
| `fieldGoals_made` | 42 | ou |
| `kicking_totalPoints` | 42 | ou |
| `passing_touchdowns` | 40 | ou |
| `passing_attempts` | 40 | ou |
| `passing_completions` | 40 | ou |
| `passing+rushing_yards` | 40 | ou |
| `passing_yards` | 40 | ou |
| `passing_longestCompletion` | 40 | ou |
| `extraPoints_kicksMade` | 38 | ou |

Bet-type breakdown (whole sample): `ou`=2,818, `yn`=2,146,
`ml`=192, `sp`=168, `eo`=50, `ml3way`=42.

## 8. Player ID shape

Critical structural observation. Each player-prop market carries
**both** identifiers:
- `statEntityID` = `"MILES_SANDERS_1_NFL"` (upper-cased name + id + league)
- `playerID`     = `"MILES_SANDERS_1_NFL"` (same string)

The `_1_NFL` suffix is SGO's canonical disambiguation tag (handles
collisions like "Tyler Smith" → multiple players). This is what the
existing player-side `sgo_players` collection already uses as its
primary key. **We can store `playerID` verbatim** — no master-hub
lookup needed at acquisition time (different from team props where
we go name→team_id via master hub).

Sample raw market shape:
```json
{
  "oddID": "receiving_receptions-MILES_SANDERS_1_NFL-game-ou-over",
  "opposingOddID": "receiving_receptions-MILES_SANDERS_1_NFL-game-ou-under",
  "marketName": "Miles Sanders Receptions Over/Under",
  "statID": "receiving_receptions",
  "statEntityID": "MILES_SANDERS_1_NFL",
  "playerID": "MILES_SANDERS_1_NFL",
  "periodID": "game",
  "betTypeID": "ou",
  "sideID": "over",
  "started": true, "ended": true, "cancelled": false,
  "fairOddsAvailable": false, "bookOddsAvailable": false,
  "fairOdds": "-151", "bookOdds": "-180",
  "fairOverUnder": "0.5", "bookOverUnder": "0.5",
  "byBookmaker": {
    "unknown": {
      "bookmakerID": "unknown",
      "odds": "-180", "overUnder": "0.5",
      "available": false, "isMainLine": true,
      "lastUpdatedAt": "2025-01-31T12:28:07Z"
    }
  }
}
```

For settled past events, `byBookmaker` collapses to a single
`unknown` entry (the closing-aggregate). For live/future events it
contains real bookmaker entries (10–30+ books).

---

## Phase 4 Design Recommendation

### New collection: `nfl_player_historical_props`
```
Unique key: (event_id, player_id, market, line, side, book, snapshot_iso)
Indexes:
  - the compound unique above
  - game_date
  - market (+game_date secondary)
  - player_id (+game_date secondary)
```

### New normalizer
Lives next to `_normalize.py` as `_normalize_player.py`:
- Same input: SGO `/v2/events` payload
- Filter: `statEntityID NOT IN ('home','away','all','game')` (i.e., player-level)
- Output row: `{event_id, sport, league, player_id, player_name?, market, line, side, book, odds, snapshot_iso, ingested_at, game_date, commence_time, is_alternate, statID, statEntityID, periodID, betTypeID, sideID, reference_only}`

### New worker `historical_player_ingest.py`
Same pattern as `historical_ingest.py`:
- Sport-aware routing (Phase 4: NFL only; MLB/NBA extensions later if desired)
- Acquire-all mode (no stat-family filter)
- Streaming flush every 50K props (proven OOM-safe)
- run_id-scoped audit
- Lenient: NO master-hub lookup; we trust SGO's `playerID` string

### Expected acquisition footprint (NFL player props, 24 months)
- ~900K – 1.05M rows
- ~540 MB – 630 MB storage @ 600B/row
- Runtime: ~10–15 min (similar to MLB scale)

### What I'm NOT doing in Phase 4
- ❌ Player master hub seeding (separate slice)
- ❌ Player_name → player_id lookup (use SGO ID verbatim)
- ❌ Player stats grading (`sgo_player_stats` already covered by existing player-stats pipeline — separate slice)
- ❌ Cross-sport player-prop (NFL only this phase)
- ❌ UI changes


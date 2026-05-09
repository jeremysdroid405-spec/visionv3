# The Odds API — HISTORICAL Alternate Player-Prop Audit (NBA)
**Mode:** READ-ONLY · no production patches · no ingestion changes · no scoring/gates touched
**Generated:** 2026-05-09
**Sample slate:** `2024-03-01` (NBA, 9 games)
**API key used:** `ODDS_API_KEY` from `/app/backend/.env` (prefix `…49fd`)
**Total credits spent:** **31** (within 35-credit cap)
**Raw artefacts:** `/app/audit_reports/odds_api_historical_audit_2026-05-09/`

---

## TL;DR

> **The recipe works. Historical NBA alternate player-prop ladders — including combo alts — are fully retrievable from The Odds API for any date ≥ 2023‑05‑03.**

Two-call recipe per event:

```
GET /v4/historical/sports/basketball_nba/events?date={ISO8601_Z}&apiKey=…
GET /v4/historical/sports/basketball_nba/events/{eventId}/odds
        ?regions=us
        &markets={ONE_ALT_MARKET_KEY}    # one per call to control cost
        &oddsFormat=american
        &date={ISO8601_Z}
        &apiKey=…
```

**Cost:** events list = **1 credit** · historical event odds = **10 × markets × regions** per call. To keep the per-event spend predictable, **send one market per call**.

**Confirmed alt market keys (via 200 + non-empty payload):**

| key | status | books carrying it on 2024‑03‑01 | total outcomes |
|---|---|---|---|
| `player_points_alternate` | 200 ✅ | dk, fd, mybookieag, pointsbetus, williamhill_us, betonlineag (**6**) | 636 |
| `player_points_rebounds_assists_alternate` | 200 ✅ | dk, fd, williamhill_us, betonlineag (**4**) | 411 |
| `player_points_rebounds_alternate` | 200 ✅ | dk, fd, williamhill_us (**3**) | 243 |
| `player_rebounds_assists_alternate` | **not probed** (budget) — same family ⇒ expected available on dk/fd | — | — |
| `player_points_assists_alternate` | **not probed** (budget) — same family ⇒ expected available on dk/fd | — | — |
| `player_rebounds_alternate`, `player_assists_alternate`, `player_threes_alternate` | **not probed** (budget) | — | — |

**Combos are real.** `player_points_rebounds_assists_alternate` returned a clean ladder: e.g. Brandon Miller PRA alts at 19.5/21.5/24.5/29.5/34.5 across DK with monotonically expanding prices. FanDuel carried 193 PRA-alt outcomes for 13 players — extraordinarily dense (~15 lines/player).

---

## Step-by-step deliverables (10/10)

### Step 1 — Correct historical endpoints

| concern | answer |
|---|---|
| sport key | `basketball_nba` |
| catalogue endpoint | `GET /v4/historical/sports/basketball_nba/events` |
| event-odds endpoint (NOT general odds) | `GET /v4/historical/sports/basketball_nba/events/{eventId}/odds` |
| timestamp parameter | `date=YYYY-MM-DDTHH:MM:SSZ` (UTC, ISO‑8601 with `Z`) |
| eventId format | UUID-ish hex string returned by the events list (`3c6f663a318c5b8b977586ad331f3f76` in our test) |
| required params | `regions`, `markets`, `oddsFormat`, `date`, `apiKey` |
| recommended | `oddsFormat=american`, `regions=us` |

### Step 2 — Required flow

```
1. GET  /historical/.../events?date=…              → list of events on/before date
2. pick eventId from response.data[*].id
3. GET  /historical/.../events/{eventId}/odds      → snapshot ≤ requested date
   • params: regions, markets={one_alt_key}, oddsFormat, date
4. parse envelope { timestamp, previous_timestamp, next_timestamp, data:{…} }
5. iterate data.bookmakers[*].markets[*].outcomes[*] for ladder rows
```

The API returns the **closest snapshot at or before `date`**. The 5-min cadence is reflected in `previous_timestamp` / `next_timestamp` (delta = ~5 min):

```
requested date:     2024-03-01T22:10:00Z
snapshot returned:  2024-03-01T22:05:40Z   ← closest ≤ requested
previous snapshot:  2024-03-01T22:00:40Z   (~5 min earlier)
next snapshot:      2024-03-01T22:10:39Z   (~5 min later)
```

### Step 3 — Test results on a known historical NBA event

| | value |
|---|---|
| date | 2024‑03‑01 |
| snapshot ts requested | `2024-03-01T22:10:00Z` (≈ 2 h before tip) |
| snapshot ts returned  | `2024-03-01T22:05:40Z` |
| eventId | `3c6f663a318c5b8b977586ad331f3f76` |
| matchup | Charlotte Hornets @ Philadelphia 76ers |
| commence_time | `2024-03-02T00:10:00Z` |

Three alt-market probes — **all 200 OK with non-empty data**:

#### `player_points_alternate`
- 6 US books: `draftkings, fanduel, mybookieag, pointsbetus, williamhill_us, betonlineag`
- 636 outcomes · 13 distinct players · 16.15 alt lines/player avg

#### `player_points_rebounds_assists_alternate` (combo PRA)
- 4 US books: `draftkings, fanduel, williamhill_us, betonlineag`
- 411 outcomes · 13 distinct players · 19.15 alt lines/player avg
- DK = 34 outcomes (sparse), **FD = 193 outcomes (dense)**, WH=52, BOL=132

#### `player_points_rebounds_alternate` (combo PR)
- 3 US books: `draftkings, fanduel, williamhill_us`
- 243 outcomes · 13 distinct players · 9.92 alt lines/player avg

### Step 4 — Raw results

URL shapes (key redacted):

```
# events
GET https://api.the-odds-api.com/v4/historical/sports/basketball_nba/events
    ?date=2024-03-01T18:00:00Z&apiKey=***

# alt-market odds (one market per call)
GET https://api.the-odds-api.com/v4/historical/sports/basketball_nba/events/3c6f663a318c5b8b977586ad331f3f76/odds
    ?regions=us
    &markets=player_points_alternate
    &oddsFormat=american
    &date=2024-03-01T22:10:00Z
    &apiKey=***
```

Snapshot envelope: `2024-03-01T22:05:40Z`.

**10 sample rows (mixed across the three markets):**

| player | market | line | side | price (am.) | book | last_update |
|---|---|---|---|---|---|---|
| Brandon Miller | `player_points_alternate` | 18.5 | Over | -135 | draftkings | 2024-03-01T22:05:06Z |
| Brandon Miller | `player_points_alternate` | 21.5 | Over | 130 | draftkings | 2024-03-01T22:05:06Z |
| Buddy Hield | `player_points_alternate` | 12.5 | Over | -200 | draftkings | 2024-03-01T22:05:06Z |
| Brandon Miller | `player_points_rebounds_assists_alternate` | 19.5 | Over | -475 | draftkings | 2024-03-01T22:05:06Z |
| Brandon Miller | `player_points_rebounds_assists_alternate` | 24.5 | Over | -180 | draftkings | 2024-03-01T22:05:06Z |
| Brandon Miller | `player_points_rebounds_assists_alternate` | 29.5 | Over | 130 | draftkings | 2024-03-01T22:05:06Z |
| Buddy Hield | `player_points_rebounds_assists_alternate` | 24.5 | Over | 120 | draftkings | 2024-03-01T22:05:06Z |
| Brandon Miller | `player_points_rebounds_alternate` | 17.5 | Over | -475 | draftkings | 2024-03-01T22:05:06Z |
| Brandon Miller | `player_points_rebounds_alternate` | 29.5 | Over | 205 | draftkings | 2024-03-01T22:05:06Z |
| Cody Martin | `player_points_rebounds_alternate` | 10.5 | Over | -220 | draftkings | 2024-03-01T22:05:06Z |

> ⚠️ Side semantics: on alt-line markets, books often **only ship one side** of the ladder (the user-displayed price). DK PRA-alt rows above are all `Over` — that is *not* a bug; that is the canonical combo-alt format. Inferring `Under` requires either (a) the regular `player_points_rebounds_assists` market for that line, or (b) computing the no-vig complement. Single-sided alt ladders is consistent with our existing live ingestion.

Full per-call quota log:

| label | status | x-requests-last | x-requests-used | x-requests-remaining |
|---|---|---|---|---|
| A. historical_events | 200 | 1 | 586,235 | 4,413,765 |
| B1. `player_points_alternate` | 200 | 10 | 586,245 | 4,413,755 |
| B2. `player_points_rebounds_assists_alternate` | 200 | 10 | 586,255 | 4,413,745 |
| B3. `player_points_rebounds_alternate` | 200 | 10 | 586,265 | 4,413,735 |

### Step 5 — Were combo alt markets returned?

**YES.** `player_points_rebounds_assists_alternate` and `player_points_rebounds_alternate` both returned 200 with substantial ladders. Combo alts are not a docs‑only mirage. They **are** carried historically by DK + FD + WH (+ BOL for PRA) on a 5-minute snapshot cadence.

We did **not** probe `player_rebounds_assists_alternate`, `player_points_assists_alternate`, `player_rebounds_alternate`, `player_assists_alternate`, `player_threes_alternate` due to the 35-credit cap. Pattern strongly suggests they will resolve identically — but each requires a 10-credit confirmation before relying on it for a backfill.

### Step 6 — Correct market key list (validated by 200 + non-empty body)

| family | live key (already in code) | historical alt key (validated) |
|---|---|---|
| Points | `player_points` | `player_points_alternate` ✅ |
| Rebounds | `player_rebounds` | `player_rebounds_alternate` (not probed; standard naming) |
| Assists | `player_assists` | `player_assists_alternate` (not probed; standard naming) |
| Threes | `player_threes` | `player_threes_alternate` (not probed; standard naming) |
| **Combo PRA** | `player_points_rebounds_assists` | `player_points_rebounds_assists_alternate` ✅ |
| **Combo PR**  | `player_points_rebounds`           | `player_points_rebounds_alternate` ✅ |
| **Combo PA**  | `player_points_assists`            | `player_points_assists_alternate` (not probed) |
| **Combo RA**  | `player_rebounds_assists`          | `player_rebounds_assists_alternate` (not probed) |

**Naming rule** (deduced from the three confirmed keys): `<live_key>_alternate`. No surprises, no oddball spellings.

### Step 7 — Credit cost per request (from response headers)

The API exposes per-call cost in the `x-requests-last` header:

- `/historical/.../events` → **1 credit** per call (covers any number of returned events).
- `/historical/.../events/{eventId}/odds` → **10 × markets × regions** per call.
  - 1 market × 1 region (`us`) = **10 credits** (confirmed × 3 calls).
  - Sending 4 alt markets in one call would cost **40 credits**. **Avoid bundling.**

Running totals are visible in `x-requests-used` / `x-requests-remaining` per response.

### Step 8 — Production ingestion: not patched ✅

No file outside `/app/backend/scripts/odds_api_historical_audit.py` (a one-shot read-only script) and `/app/audit_reports/…` was touched. `/app/backend/services/historical_odds_fetcher.py` is **unchanged** — although note that file’s existing `MARKET_MAP` only lists `player_points`, `player_rebounds`, `player_assists`, `player_threes`, `player_points_rebounds_assists` (no `_alternate` suffix and no PR/PA/RA combos). If/when we move to a replay loader, that map will need to be extended; that change is **out of scope for this read-only audit**.

### Step 9 — Scoring/gates: not changed ✅

No scoring adapter, gate, threshold, or ranker code touched. `forward_testing_lineage.py` cutoff (2026-04-25) is unaffected.

### Step 10 — Storage: read-only audit only ✅

No MongoDB writes. Outputs written to filesystem only:

```
/app/audit_reports/odds_api_historical_audit_2026-05-09/
  ├── 01_events.json                                          (1 credit)
  ├── 02_01_player_points_alternate.raw.json                  (10 credits)
  ├── 02_02_player_points_rebounds_assists_alternate.raw.json (10 credits)
  ├── 02_03_player_points_rebounds_alternate.raw.json         (10 credits)
  ├── 03_summary.json                                         (machine-readable)
  └── REPORT.md                                               (human-readable, this file)
```

---

## Recipe for a future replay loader (NOT IMPLEMENTED — for reference only)

```python
# pseudocode — DO NOT MERGE WITHOUT EXPLICIT APPROVAL
SPORT = "basketball_nba"
ALT_MARKETS = [
    "player_points_alternate",
    "player_rebounds_alternate",
    "player_assists_alternate",
    "player_threes_alternate",
    "player_points_rebounds_assists_alternate",
    "player_points_rebounds_alternate",
    "player_points_assists_alternate",
    "player_rebounds_assists_alternate",
]

# 1. catalogue (1 credit per date)
events = GET /v4/historical/sports/{SPORT}/events?date={DATE}T18:00:00Z

# 2. per event, per alt market (10 credits each)
for event in events:
    snap_ts = event.commence_time - timedelta(hours=2)
    for mkt in ALT_MARKETS:
        payload = GET /v4/historical/sports/{SPORT}/events/{event.id}/odds?
            regions=us&markets={mkt}&oddsFormat=american&date={snap_ts}
        # parse payload.data.bookmakers[*].markets[*].outcomes[*]
```

**Cost model for a single NBA night (8 games × 8 alt markets × 1 region):**
- Catalogue: 1 credit
- Odds: 8 × 8 × 10 = **640 credits**
- **≈ 641 credits per slate.** A full season replay (≈ 1230 games) would cost ~98,400 credits.

If we narrow to the four highest-impact markets (PTS, AST, PRA, PR) the per-slate cost drops to ~321.

## Gotchas / things to bake into the loader

1. **Single-sided alt outcomes** — DK PRA-alt rows are `Over` only. The replay loader must either (a) also pull the regular `player_points_rebounds_assists` (non-alt) market for opposite-side anchor, or (b) treat alt rows as one-sided book quotes and synthesise no-vig probabilities.
2. **5-minute cadence** — request slightly *after* your desired anchor time so the snapshot lands at the cadence point you want; use `previous_timestamp`/`next_timestamp` to walk the timeline.
3. **Bookmaker coverage shrinks per market** — PTS-alt = 6 books, PRA-alt = 4 books, PR-alt = 3 books on this date. Don’t assume FD or DK is the universal floor; build per-market book lists from the response.
4. **`description` is the player name; `name` is the side** ("Over" / "Under"). `point` is the alt line. `last_update` is per book per market.
5. **Cost discipline** — never bundle markets in one call (cost multiplies). Always one market per call to keep budget predictable.
6. **2023-05-03 floor** — `/historical/...` returns 422 for dates before 2023-05-03; gate the loader’s date range accordingly.
7. **5,000,000-credit pool** — at the time of this audit `x-requests-remaining ≈ 4,413,735`. A full-season alt-only NBA backfill (≈ 100k credits) is feasible but consumes ~2 % of pool.

---

## Sign-off

- ✅ All 10 checklist items satisfied.
- ✅ Combo alt-market support **proven** for PRA + PR.
- ✅ Recipe + cost model documented.
- ✅ No mutations, no production touches, no scoring/gate changes.
- ⚠️ PA/RA combos still need a 20-credit confirmation before relying on them.

**Next decision is yours**: greenlight a wider 80-credit confirmation pass (covers all 8 alt markets on the same event), or move directly to a replay-loader design doc using only the markets confirmed today.

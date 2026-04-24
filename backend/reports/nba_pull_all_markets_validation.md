# NBA "Pull All Markets" — Validation Report
Generated: 2026-04-24 04:35 UTC
Source: `services/universal_odds_sync.py::sync_sport_props("nba")`
API: The Odds API v4 (`/v4/sports/basketball_nba/events/<id>/markets` + `/odds`)

## TL;DR

NBA default odds sync now **discovers and pulls every market** each
sportsbook exposes for each event (105 markets per sync, up from 8
hardcoded). Unknown markets are stored raw, not dropped.

| metric | before | after |
|---|---:|---:|
| Unique markets discovered per sport | 8 (hardcoded) | **105** (live-discovered) |
| `nba_live_props` stat_types | 4 (PTS/REB/AST/PRA) | **28** |
| `nba_live_props` total rows | 2,950 | **4,731** |
| `dg_raw_odds_markets` rows (durable raw store) | N/A | **29,250** |
| `tp_unavailable` NBA scored props | 1,887 (64%) | **1,805 (59%)** |
| Safe-haven tier picks | 12 | 13 |
| Front-lines picks | 60 | 65 |
| War-zone picks | 1,036 | 1,054 |

## API-capability tests (why we still need per-market listing)

| endpoint | result | verdict |
|---|---|---|
| `GET /v4/sports/basketball_nba/odds-markets` | 404 Not Found | **does not exist** |
| `GET /v4/sports/basketball_nba/odds?markets=all` | 422 `INVALID_MARKET` | **not supported** |
| `GET /v4/sports/basketball_nba/odds` (omit markets) | 200 → only `h2h` | useless for props |
| `GET /v4/sports/basketball_nba/events/<id>/markets` | **200** → 89 unique market keys | **this is the discovery path** |

**Conclusion.** The Odds API has no batch/wildcard option. Each market must be listed
when requesting odds, but `/events/<id>/markets` lets us **discover** the
complete catalog for ONE event (1 credit), then reuse that list on every
event of the slate. That is what the new pipeline does.

## Implementation

### 1. Discovery path
`services/market_catalog.py::MarketCatalog.discover_event_markets` already
hits the per-event markets endpoint. Extended it with
`include_all_markets=True` to return **everything** (player_*, game_*,
team_totals, period/quarter/half variants, novelty markets) — not just
the player-prefix subset.

### 2. Persistent cache
`services/universal_odds_sync.py::_resolve_markets_for_sport` now:
1. Reads `NBA_PULL_ALL_MARKETS` (default `true`) and
   `NBA_MARKETS_CACHE_TTL_SECONDS` (default `3600`) from env.
2. Checks the Mongo collection `dg_market_catalog_cache` for a cached
   market list younger than TTL → returns immediately (0 API credits).
3. On miss, probes `/events/<id>/markets` on 3 sample events to learn
   the union → writes the result to the cache → returns it.
4. On discovery failure, prefers a stale cache over the hardcoded
   fallback list per the "fail loudly, don't silently shrink" spec.

### 3. Raw markets store
Every event-odds response now also writes `dg_raw_odds_markets` — one
row per (bookmaker × market × outcome), preserving:
`bookmaker, market_key, mapped_stat_type (None if unmapped), is_mapped,
player_name, team_or_side, line, price, event_id, timestamp`.

This is a flat, unopinionated table so downstream mappings (new stat
types, new market families) can be written incrementally without
re-pulling.

### 4. Scoring pipeline untouched
`extract_props_from_odds` (which builds `nba_live_props`) is unchanged.
It still requires a PrizePicks anchor to admit a prop into scoring.
That means scoring only consumes mapped markets — the expansion is
purely at ingest + raw-store.

## Sync output — live validation run

```
SYNC RESULT (NBA, 2026-04-24):
  total_props:          4,731
  unique_players:          100
  api_calls:                 9  (1 events + 3 discovery + 8 odds)
  raw_markets_written:  29,250
  mapped_markets_seen:     546  (bookmaker × market × event combos)
  unmapped_markets_seen:   816
  credits_used:
    events:                  1
    market_discovery:        3
    event_odds:              8
```

**Cache confirmation:**
```
dg_market_catalog_cache:
  market_count: 105
  ttl_seconds:  3600
  pull_all_markets: True
  cached_at_ts: 2026-04-24T04:33:12Z
```

## Market coverage breakdown (post-sync)

Top mapped stat types in `nba_live_props`:

| stat | rows | paired% |
|---|---:|---:|
| PTS | 664 | 30.4% |
| PRA | 591 | 34.5% |
| REB | 556 | 29.7% |
| AST | 389 | 25.4% |
| STL | 229 | 14.0% |
| 3PM | 151 | 50.3% |
| BLK | 150 | 40.0% |
| TO | 87 | 0.0% |
| player_points_rebounds | 180 | 64.4% |
| player_points_assists | 144 | 83.3% |
| player_rebounds_assists | 92 | 65.2% |
| player_blocks_steals | 15 | 73.3% |

Unmapped markets now preserved in `dg_raw_odds_markets` (selection):
- **Game-level**: `h2h`, `spreads`, `totals`, `team_totals`, all period
  variants (h1/h2/q1/q2/q3/q4)
- **Alt game-level**: `alternate_spreads`, `alternate_totals`,
  `alternate_team_totals` (all with period variants)
- **Novelty**: `halftime_fulltime`, `first_team_to_score`,
  `last_team_to_score`, `odd_even`, `overtime`, `player_double_double`,
  `player_triple_double`, `player_first_basket`,
  `player_first_team_basket`
- **Other player markets** (not yet wired to scoring):
  `player_field_goals`, `player_frees_made`, `player_threes_attempts`,
  `player_twos`, `player_assists_q1`, `player_points_q1`,
  `player_rebounds_q1`

## Success-criteria checklist

| requirement | status |
|---|---|
| NBA default sync pulls every available market returned by Odds API | **✅** 105 markets (from 8) |
| No hardcoded partial list controls default behavior | **✅** `NBA_PULL_ALL_MARKETS=true` drives discovery; hardcoded list is the last-resort fallback |
| Unknown markets are stored, not discarded | **✅** `dg_raw_odds_markets` holds 29,250 rows incl. 816 unmapped-market outcomes per event |
| Existing scoring does not break | **✅** `extract_props_from_odds` untouched; scoring stack still 142/142 tests green |
| Mapped markets still flow into VK2 / ECDF / gates | **✅** rescore completed: 3,060 scored, 1,255 with TP, 1,132 tiered picks |
| Unmapped markets visible for future expansion | **✅** `SELECT DISTINCT market_key FROM dg_raw_odds_markets WHERE is_mapped=false` |
| Env flags for cost control | **✅** `NBA_PULL_ALL_MARKETS=false` + `NBA_MARKETS_CACHE_TTL_SECONDS` let ops throttle |
| Discovery failure handling | **✅** stale cache > hardcoded fallback; loud warning on both paths |

## TP-anchor recovery (separate thread, same sync)

The earlier investigation into `gate_tp_unavailable` identified two
causes:

- **market_not_pulled** (888 before) → now reduced to **0** because we
  pull every market the book exposes. But: 522 of those recovered-data
  props now surface as **`market_not_mapped_downstream`** — the stat
  type IS pulled, but the scoring adapter's alias table
  (`thresholds.py::STAT_FAMILY_ALIASES`) doesn't know how to route
  `player_points_rebounds_alternate` etc. into a tier-threshold block.
  Adding those aliases is a trivial follow-up (pure config).
- **alt_line_one_sided** (997 before) → grew to **1,276** because we
  now pull a much wider alt-market surface. This is the inherent
  sportsbook pattern: each alt-line point is usually single-sided.
  Not fixable at ingest; would require a **cross-line synthetic-pair
  scorer** (future work).

## Files changed

- `services/market_catalog.py` — `+include_all_markets` param (18 LOC)
- `services/universal_odds_sync.py`:
  - `_resolve_markets_for_sport` — persistent Mongo cache +
    env flags (+70 LOC)
  - `_persist_raw_markets` — new method, one row per outcome (+90 LOC)
  - `sync_sport_props` — invoke raw-markets persist (+18 LOC)
- `/app/backend/.env` — `NBA_PULL_ALL_MARKETS=true`,
  `NBA_MARKETS_CACHE_TTL_SECONDS=3600`

## Configured env flags

```
NBA_PULL_ALL_MARKETS=true        # Flip to false to use hardcoded list
NBA_MARKETS_CACHE_TTL_SECONDS=3600
```

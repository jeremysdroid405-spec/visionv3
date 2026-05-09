# The Odds API — Historical Alt-Prop Audit (NBA, read-only)
_Generated_: 2026-05-09T03:31:05.709038+00:00
_Sample slate_: **2024-03-01** (event id `3c6f663a318c5b8b977586ad331f3f76`)
_Snapshot ts_: `2024-03-01T22:10:00Z` (≈ 2h before tip 2024-03-02T00:10:00Z)

## Endpoint shapes (URLs redacted)
```
# events list (cost: 1 credit)
GET https://api.the-odds-api.com/v4/historical/sports/basketball_nba/events?date=2024-03-01T18:00:00Z&apiKey=***

# alt-market historical event odds (cost: 10 × markets × regions)
GET https://api.the-odds-api.com/v4/historical/sports/basketball_nba/events/{eventId}/odds?regions=us&markets={ONE_MARKET_KEY}&oddsFormat=american&date=2024-03-01T22:10:00Z&apiKey=***
```

## Events list result
- events returned: **9**

## Per-market findings
| market_key | status | x-requests-last | snapshot_returned | books_with_market | total_outcomes | distinct_players | sample_density |
|---|---|---|---|---|---|---|---|
| `player_points_alternate` | 200 | 10 | `2024-03-01T22:05:40Z` | 6/6 | 636 | 13 | 16.15 alt lines/player |
| `player_points_rebounds_assists_alternate` | 200 | 10 | `2024-03-01T22:05:40Z` | 4/4 | 411 | 13 | 19.15 alt lines/player |
| `player_points_rebounds_alternate` | 200 | 10 | `2024-03-01T22:05:40Z` | 3/3 | 243 | 13 | 9.92 alt lines/player |

## Per-market sample rows (first 10 per market)

### `player_points_alternate`
| player | market | line | side | price (am.) | book | last_update |
|---|---|---|---|---|---|---|
| Brandon Miller | `player_points_alternate` | 18.5 | Over | -135 | draftkings | 2024-03-01T22:05:06Z |
| Brandon Miller | `player_points_alternate` | 18.5 | Under | -105 | draftkings | 2024-03-01T22:05:06Z |
| Brandon Miller | `player_points_alternate` | 20.5 | Over | 115 | draftkings | 2024-03-01T22:05:06Z |
| Brandon Miller | `player_points_alternate` | 20.5 | Under | -155 | draftkings | 2024-03-01T22:05:06Z |
| Brandon Miller | `player_points_alternate` | 21.5 | Over | 130 | draftkings | 2024-03-01T22:05:06Z |
| Brandon Miller | `player_points_alternate` | 21.5 | Under | -185 | draftkings | 2024-03-01T22:05:06Z |
| Buddy Hield | `player_points_alternate` | 12.5 | Over | -200 | draftkings | 2024-03-01T22:05:06Z |
| Buddy Hield | `player_points_alternate` | 12.5 | Under | 140 | draftkings | 2024-03-01T22:05:06Z |
| Buddy Hield | `player_points_alternate` | 13.5 | Over | -160 | draftkings | 2024-03-01T22:05:06Z |
| Buddy Hield | `player_points_alternate` | 13.5 | Under | 110 | draftkings | 2024-03-01T22:05:06Z |

### `player_points_rebounds_assists_alternate`
| player | market | line | side | price (am.) | book | last_update |
|---|---|---|---|---|---|---|
| Brandon Miller | `player_points_rebounds_assists_alternate` | 19.5 | Over | -475 | draftkings | 2024-03-01T22:05:06Z |
| Brandon Miller | `player_points_rebounds_assists_alternate` | 21.5 | Over | -310 | draftkings | 2024-03-01T22:05:06Z |
| Brandon Miller | `player_points_rebounds_assists_alternate` | 24.5 | Over | -180 | draftkings | 2024-03-01T22:05:06Z |
| Brandon Miller | `player_points_rebounds_assists_alternate` | 29.5 | Over | 130 | draftkings | 2024-03-01T22:05:06Z |
| Brandon Miller | `player_points_rebounds_assists_alternate` | 34.5 | Over | 290 | draftkings | 2024-03-01T22:05:06Z |
| Buddy Hield | `player_points_rebounds_assists_alternate` | 17.5 | Over | -380 | draftkings | 2024-03-01T22:05:06Z |
| Buddy Hield | `player_points_rebounds_assists_alternate` | 19.5 | Over | -235 | draftkings | 2024-03-01T22:05:06Z |
| Buddy Hield | `player_points_rebounds_assists_alternate` | 21.5 | Over | -150 | draftkings | 2024-03-01T22:05:06Z |
| Buddy Hield | `player_points_rebounds_assists_alternate` | 24.5 | Over | 120 | draftkings | 2024-03-01T22:05:06Z |
| Buddy Hield | `player_points_rebounds_assists_alternate` | 29.5 | Over | 320 | draftkings | 2024-03-01T22:05:06Z |

### `player_points_rebounds_alternate`
| player | market | line | side | price (am.) | book | last_update |
|---|---|---|---|---|---|---|
| Brandon Miller | `player_points_rebounds_alternate` | 17.5 | Over | -475 | draftkings | 2024-03-01T22:05:06Z |
| Brandon Miller | `player_points_rebounds_alternate` | 19.5 | Over | -295 | draftkings | 2024-03-01T22:05:06Z |
| Brandon Miller | `player_points_rebounds_alternate` | 29.5 | Over | 205 | draftkings | 2024-03-01T22:05:06Z |
| Brandon Miller | `player_points_rebounds_alternate` | 34.5 | Over | 450 | draftkings | 2024-03-01T22:05:06Z |
| Buddy Hield | `player_points_rebounds_alternate` | 14.5 | Over | -330 | draftkings | 2024-03-01T22:05:06Z |
| Buddy Hield | `player_points_rebounds_alternate` | 15.5 | Over | -255 | draftkings | 2024-03-01T22:05:06Z |
| Buddy Hield | `player_points_rebounds_alternate` | 17.5 | Over | -160 | draftkings | 2024-03-01T22:05:06Z |
| Buddy Hield | `player_points_rebounds_alternate` | 19.5 | Over | 100 | draftkings | 2024-03-01T22:05:06Z |
| Buddy Hield | `player_points_rebounds_alternate` | 24.5 | Over | 275 | draftkings | 2024-03-01T22:05:06Z |
| Cody Martin | `player_points_rebounds_alternate` | 10.5 | Over | -220 | draftkings | 2024-03-01T22:05:06Z |

## Calls log (with quota headers)
| label | status | x-requests-last | x-requests-used | x-requests-remaining |
|---|---|---|---|---|
| A. historical_events | 200 | 1 | 586235 | 4413765 |
| B1. alt_market::player_points_alternate | 200 | 10 | 586245 | 4413755 |
| B2. alt_market::player_points_rebounds_assists_alternate | 200 | 10 | 586255 | 4413745 |
| B3. alt_market::player_points_rebounds_alternate | 200 | 10 | 586265 | 4413735 |

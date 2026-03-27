# API Surface Documentation

## Overview

All API endpoints are prefixed with `/api` by the ingress controller.
Base URL: `https://your-domain.com/api`

---

## Core V3 Endpoints (Primary)

### Board & Picks

| Method | Endpoint | Description | Source |
|--------|----------|-------------|--------|
| GET | `/v3/board` | Get cached board with all players and props | `routes/core_v3.py` |
| GET | `/v3/status` | Get sync status | `routes/core_v3.py` |
| GET | `/v3/players` | Get all players from master hub | `routes/core_v3.py` |
| GET | `/v3/player/{player_name}` | Get single player details | `routes/core_v3.py` |
| GET | `/v3/demons` | Get demon (high-risk) picks | `routes/core_v3.py` |
| GET | `/v3/goblins` | Get goblin (safe) picks | `routes/core_v3.py` |
| GET | `/v3/search` | Search players | `routes/core_v3.py` |
| GET | `/v3/trending` | Get trending players | `routes/core_v3.py` |
| GET | `/v3/most-popular-bets` | Get popular bets | `routes/core_v3.py` |
| POST | `/v3/sync` | Trigger full sync | `routes/core_v3.py` |

### Player Detail (Enriched)

| Method | Endpoint | Description | Source |
|--------|----------|-------------|--------|
| GET | `/v3/player-with-badges/{player_name}` | Get player with intel suite & context badges | `routes/cached_data.py` |
| GET | `/v3/hydrated-board` | Get full hydrated board | `routes/cached_data.py` |
| GET | `/v3/cached-props` | Get cached props | `routes/cached_data.py` |
| GET | `/v3/static-shell` | Get static UI shell | `routes/cached_data.py` |
| GET | `/v3/live-lines` | Get live betting lines | `routes/cached_data.py` |
| GET | `/v3/test-badges/{player_name}` | Test badge generation | `routes/cached_data.py` |

### Tiers & Parlays

| Method | Endpoint | Description | Source |
|--------|----------|-------------|--------|
| GET | `/v3/war-zone` | Get War Zone (demon) picks | `routes/tiers.py` |
| GET | `/v3/safe-haven` | Get Safe Haven (goblin) picks | `routes/tiers.py` |
| GET | `/v3/front-lines` | Get Front Lines (mixed) picks | `routes/tiers.py` |
| GET | `/v3/parlay-builder` | Get parlay recommendations | `routes/cached_data.py` |
| GET | `/v3/goblin-recon` | Get safe parlay recommendations | `routes/cached_data.py` |

---

## Board Intelligence

| Method | Endpoint | Description | Source |
|--------|----------|-------------|--------|
| GET | `/v3/board-intel/status` | Get board intel status | `routes/board_intel_v2.py` |
| POST | `/v3/board-intel/primary-sync` | Run primary sync | `routes/board_intel_v2.py` |
| POST | `/v3/board-intel/delta-refresh` | Run delta refresh | `routes/board_intel_v2.py` |
| POST | `/v3/board-intel/start-scheduler` | Start background scheduler | `routes/board_intel_v2.py` |
| POST | `/v3/board-intel/stop-scheduler` | Stop background scheduler | `routes/board_intel_v2.py` |
| POST | `/v3/board-intel/early-bird` | Run early bird sync | `routes/board_intel_v2.py` |
| GET | `/v3/live-ticker` | Get news ticker | `routes/board_intel_v2.py` |
| GET | `/v3/scouting-projections` | Get scouting projections | `routes/board_intel_v2.py` |

---

## Adaptive Sync

| Method | Endpoint | Description | Source |
|--------|----------|-------------|--------|
| GET | `/v3/sync-status` | Get detailed sync status | `routes/adaptive_sync.py` |
| GET | `/v3/stale-intel-check` | Check for stale data | `routes/adaptive_sync.py` |
| GET | `/v3/intel-freshness` | Get data freshness metrics | `routes/adaptive_sync.py` |
| POST | `/v3/priority-refresh` | Trigger priority refresh | `routes/adaptive_sync.py` |
| POST | `/v3/adaptive-sync/start` | Start adaptive sync engine | `routes/adaptive_sync.py` |
| POST | `/v3/adaptive-sync/stop` | Stop adaptive sync engine | `routes/adaptive_sync.py` |

---

## Command Hub

| Method | Endpoint | Description | Source |
|--------|----------|-------------|--------|
| POST | `/command/simulate` | Simulate parlay | `routes/command.py` |
| GET | `/command/search` | Search for players (command post) | `routes/command.py` |
| GET | `/command/profile/{player_name}` | Get player profile | `routes/command.py` |
| GET | `/command/grades` | Get pick grades | `routes/command.py` |

---

## Authentication

| Method | Endpoint | Description | Source |
|--------|----------|-------------|--------|
| POST | `/auth/signup` | Register new user | `routes/auth.py` |
| POST | `/auth/login` | User login | `routes/auth.py` |

---

## Admin

| Method | Endpoint | Description | Source |
|--------|----------|-------------|--------|
| GET | `/admin/cache-status` | Get cache status | `routes/admin.py` |
| GET | `/admin/roster-status` | Get roster sync status | `routes/admin.py` |
| GET | `/admin/rate-limit-status` | Get rate limit status | `routes/admin.py` |
| GET | `/admin/todays-games` | Get today's games | `routes/admin.py` |
| GET | `/admin/dvp-status` | Get DVP sync status | `routes/admin.py` |
| GET | `/admin/dvp-rankings` | Get DVP rankings | `routes/admin.py` |
| GET | `/admin/dvp-analysis/{opponent_team}/{stat_type}` | Get DVP analysis | `routes/admin.py` |
| POST | `/admin/trigger-daily-sync` | Trigger daily sync | `routes/admin.py` |
| POST | `/admin/sync-rosters` | Sync rosters | `routes/admin.py` |
| POST | `/admin/clear-all-cache` | Clear all caches | `routes/admin.py` |
| POST | `/admin/dvp-refresh` | Refresh DVP data | `routes/admin.py` |

---

## Live Data

| Method | Endpoint | Description | Source |
|--------|----------|-------------|--------|
| GET | `/live/status` | Get live data status | `routes/live.py` |
| GET | `/live/events` | Get live events | `routes/live.py` |
| GET | `/live/event/{event_id}/odds` | Get odds for event | `routes/live.py` |
| GET | `/live/props` | Get live props | `routes/live.py` |
| GET | `/live/demons` | Get demon picks | `routes/live.py` |
| GET | `/live/player/{player_name}` | Get player live data | `routes/live.py` |
| GET | `/live/search` | Search live data | `routes/live.py` |
| POST | `/live/sync` | Trigger live sync | `routes/live.py` |

---

## Board (Legacy)

| Method | Endpoint | Description | Source |
|--------|----------|-------------|--------|
| GET | `/board/cached-props` | Get cached props | `routes/board.py` |
| GET | `/board/cached-player/{player_name}` | Get cached player | `routes/board.py` |
| GET | `/board/players` | Get all players | `routes/board.py` |
| GET | `/board/player/{player_name}` | Get single player | `routes/board.py` |
| GET | `/board/search` | Search players | `routes/board.py` |
| GET | `/board/board` | Get board | `routes/board.py` |
| GET | `/board/trending` | Get trending | `routes/board.py` |
| GET | `/board/static-shell` | Get static shell | `routes/board.py` |
| GET | `/board/hydrated-board` | Get hydrated board | `routes/board.py` |
| GET | `/board/photo-stats` | Get photo stats | `routes/board.py` |
| POST | `/board/sync-photos` | Sync player photos | `routes/board.py` |

---

## Master Hub

| Method | Endpoint | Description | Source |
|--------|----------|-------------|--------|
| GET | `/hub/player/{player_name}` | Get player from master hub | `routes/master_hub.py` |
| POST | `/hub/sync` | Sync master hub | `routes/master_hub.py` |
| POST | `/hub/bdl-sync` | Sync BDL data | `routes/master_hub.py` |

---

## Proxy

| Method | Endpoint | Description | Source |
|--------|----------|-------------|--------|
| GET | `/proxy/nba-headshot/{player_id}` | Proxy NBA headshot | `routes/image_proxy.py` |
| GET | `/proxy/espn-headshot/{espn_id}` | Proxy ESPN headshot | `routes/image_proxy.py` |

---

## Frontend Usage

The frontend should use `REACT_APP_BACKEND_URL` environment variable:

```javascript
// DataService.js
const API_URL = process.env.REACT_APP_BACKEND_URL;

// Example calls
const board = await fetch(`${API_URL}/api/v3/board`);
const player = await fetch(`${API_URL}/api/v3/player-with-badges/LeBron%20James`);
const warZone = await fetch(`${API_URL}/api/v3/war-zone`);
```

---

## Response Formats

### Board Response
```json
{
  "success": true,
  "synced_at": "2026-03-27T00:39:50.842622+00:00",
  "players_count": 88,
  "total_props": 1041,
  "players": [...]
}
```

### Player Response
```json
{
  "player": {
    "player_name": "LeBron James",
    "team": "LAL",
    "position": "SF",
    "baseline_stats": {...},
    "bdl_game_logs": [...],
    "props": [...]
  }
}
```

### Tier Response (War Zone/Safe Haven)
```json
{
  "success": true,
  "tier": "war_zone",
  "picks": [...],
  "generated_at": "2026-03-27T00:39:50Z"
}
```

# NBA Master Hub Architecture & Why It Fails on Fresh Servers

## Overview

The `nba_master_hub_2026` collection is the **Single Source of Truth (SSOT)** for all player statistics in PropVision. Every component reads from this collection:

```
┌─────────────────────────────────────────────────────────────────┐
│                     DATA FLOW ARCHITECTURE                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│   BallDontLie API ──┐                                           │
│   (548 players)     │                                           │
│   • /players        │                                           │
│   • /season_averages├──► nba_master_hub_2026 ──► ALL FEATURES   │
│   • /stats (logs)   │       (SSOT)                              │
│                     │         │                                  │
│   NBA.com API ──────┘         ├──► Player Detail Page           │
│   • L5/L10 stats              ├──► Parlay Builder               │
│                               ├──► War Zone / Safe Haven        │
│                               ├──► AI Vision Analysis           │
│                               └──► Game Log Bar Charts          │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## The Sync Process

### What Happens During Sync

The `BDLComprehensiveSyncService` in `/app/backend/services/bdl_comprehensive_sync.py` does:

1. **Fetch All Active Players** (~548 players with teams)
   ```
   GET https://api.balldontlie.io/v1/players/active
   ```

2. **For EACH Player** (in batches of 10):
   - Fetch profile data (height, weight, team, position)
   - Fetch season averages (pts, reb, ast, fg%, etc.)
   - Fetch individual game logs (last 100 games)
   - Lookup NBA.com ID and fetch L5/L10 official stats
   - Merge all data and store in `nba_master_hub_2026`

3. **Rate Limiting**:
   - 0.5 second delay between batches
   - Progress logged every 50 players

### Time Required
```
548 players ÷ 10 per batch = 55 batches
55 batches × 0.5s delay = ~28 seconds minimum
+ API response times (~1-2s per batch) = ~2-3 minutes total

BUT: If any API times out or fails, the entire sync can take 10-30 minutes
```

---

## Why It Fails on Fresh Servers

### Problem 1: Empty Database = No Fallback

When the database is empty:
- The board endpoint `/api/v3/board` queries `nba_master_hub_2026`
- Returns 0 players → Empty board → "No picks available"
- **Every feature depends on this data existing first**

### Problem 2: BDL API Rate Limits

BallDontLie has strict rate limits:
- Free tier: 30 requests/minute
- Even with a paid key, syncing 548 players requires ~1,650+ API calls
- If you hit rate limits, the sync partially completes or fails silently

### Problem 3: Sync Timeout on Serverless/Weak VPS

The sync endpoint `/api/v3/hub/bdl-sync` can take 5-10 minutes:
- Nginx default timeout: 60 seconds
- VPS might kill long-running processes
- Result: Sync starts but never finishes

### Problem 4: MongoDB Atlas Network Latency

If using Atlas (remote MongoDB):
- Each player insert = round-trip to Atlas
- 548 inserts × 100ms latency = 55 seconds just for writes
- Combined with API calls = timeout city

### Problem 5: Chicken-and-Egg Problem

```
App needs data to show picks
    ↓
Data comes from sync
    ↓
Sync requires API keys + working MongoDB
    ↓
If MongoDB is Atlas, needs IP whitelist
    ↓
If IP not whitelisted, sync fails
    ↓
Database stays empty
    ↓
App shows nothing
```

---

## The Collection Structure

Each document in `nba_master_hub_2026`:

```javascript
{
  "_id": ObjectId,
  "display_name": "Karl-Anthony Towns",
  "bdl_id": 449,                    // BallDontLie ID
  "nba_id": 1626157,                // NBA.com ID
  "espn_id": "3136195",             // ESPN ID (for photos)
  "team": "New York Knicks",
  "team_abbreviation": "NYK",
  "position": "C",
  "jersey_number": "32",
  
  // Core stats (used for pick calculations)
  "baseline_stats": {
    "szn_avg": { "pts": 20.0, "reb": 11.8, "ast": 2.9, ... },
    "l10_avg": { "pts": 20.1, "reb": 11.2, "ast": 3.1, ... },
    "l5_avg":  { "pts": 18.0, "reb": 12.8, "ast": 2.6, ... },
    "games_played": 72
  },
  
  // Raw game logs (used for bar charts)
  "bdl_game_logs": [
    { "game_date": "2026-03-25", "pts": 21, "reb": 14, "opponent_team_id": 19, ... },
    { "game_date": "2026-03-23", "pts": 26, "reb": 16, "opponent_team_id": 30, ... },
    // ... last 100 games
  ],
  
  "last_synced": "2026-03-26T00:00:00Z"
}
```

---

## Solution: Database Export/Import

Since the sync is unreliable on fresh servers, the **fastest solution** is:

1. Export working database from Emergent preview environment
2. Import directly to production MongoDB
3. Skip the slow API sync entirely

### Export (from working server):
```bash
mongodump --db=pick_vision --out=/tmp/dump
tar -czvf propvision_db.tar.gz dump/
```

### Import (to production):
```bash
wget https://your-url/propvision_db.tar.gz
tar -xzvf propvision_db.tar.gz
mongorestore --db=pick_vision dump/pick_vision/
```

### After Import:
- Live odds sync still works (calls Odds API, doesn't depend on BDL)
- Player data already populated
- Bar charts work (game_logs present)
- All features functional

---

## Long-Term Fix

For future fresh deployments, you could:

1. **Create a seed data file** - JSON dump of essential collections
2. **Chunked sync endpoint** - Sync 50 players at a time via multiple calls
3. **Background worker** - Celery/RQ task that syncs over 30 minutes without timeout
4. **Cache BDL responses** - Store raw API responses in Redis to avoid re-fetching

---

## Summary

| Issue | Root Cause | Solution |
|-------|-----------|----------|
| Empty board | No data in `nba_master_hub_2026` | Import database dump |
| Sync timeout | 548 players × 3 API calls each | Use pre-populated database |
| Atlas timeout | Network latency + IP whitelist | Use local MongoDB |
| Rate limits | BDL API restrictions | Import existing data |

**Bottom line**: The architecture assumes the database is already populated. Fresh servers need the data imported, not synced.

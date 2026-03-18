# BDL API Audit & Optimization Report

## Complete BDL API Endpoints Available

### Player Data
| Endpoint | Description | Currently Used? |
|----------|-------------|-----------------|
| `/nba/v1/players` | Get all players (paginated) | ✅ Yes |
| `/nba/v1/players/{id}` | Get specific player by ID | ✅ Yes |
| `/nba/v1/players/active` | Get active players only | ❌ **Should use instead of /players** |
| `/nba/v1/player_injuries` | Player injury reports | ❌ **HIGH VALUE - Add this!** |

### Stats & Averages
| Endpoint | Description | Currently Used? |
|----------|-------------|-----------------|
| `/nba/v1/season_averages` | Basic season averages (single player) | ✅ Yes |
| `/nba/v1/season_averages/{type}` | Advanced season averages by category | ❌ **Available types: general, clutch, defense, shooting** |
| `/nba/v1/stats` | Individual game logs | ✅ Yes (for L5/L10) |
| `/nba/v2/stats/advanced` | Advanced stats (PIE, ratings, hustle) | ❌ **HIGH VALUE - Add this!** |
| `/nba/v1/leaders` | Stat leaders by category | ❌ Could use for rankings |

### Team Data
| Endpoint | Description | Currently Used? |
|----------|-------------|-----------------|
| `/nba/v1/teams` | All teams | ✅ Yes |
| `/nba/v1/teams/{id}` | Specific team | ❌ Not needed |
| `/nba/v1/team_season_averages/{category}` | Team stats | ❌ **Could use for DvP instead of manual config** |
| `/nba/v1/standings` | Team standings | ❌ Could be useful |

### Games & Live Data
| Endpoint | Description | Currently Used? |
|----------|-------------|-----------------|
| `/nba/v1/games` | Game schedules | ❌ Using The Odds API instead |
| `/nba/v1/games/{id}` | Specific game | ❌ |
| `/nba/v1/box_scores` | Box scores by date | ❌ |
| `/nba/v1/box_scores/live` | Live box scores | ❌ Could use for live updates |
| `/nba/v1/lineups` | Game lineups (2025+) | ❌ **HIGH VALUE - Starting lineups!** |
| `/nba/v1/plays` | Play-by-play data | ❌ |

### Betting & Contracts
| Endpoint | Description | Currently Used? |
|----------|-------------|-----------------|
| `/nba/v2/odds` | Game betting odds | ❌ Using The Odds API |
| `/nba/v2/odds/player_props` | **Player prop odds from DraftKings, FanDuel, etc.** | ❌ **CRITICAL - This is better than The Odds API!** |
| `/nba/v1/contracts/players` | Player contracts | ❌ Could use for "pay_day" badge |
| `/nba/v1/contracts/players/aggregate` | Multi-year contract data | ❌ |

---

## ISSUES FOUND: Manual Calculations That Should Use BDL

### 1. Season Averages ✅ FIXED
**Problem**: Was calculating from game logs
**Solution**: Now using `/season_averages` endpoint directly

### 2. L5/L10 Averages - KEEP CALCULATING
**Reason**: BDL doesn't provide L5/L10 directly. Must calculate from `/stats` game logs.
**Current**: ✅ Correct approach

### 3. Hit Rates - KEEP CALCULATING  
**Reason**: BDL doesn't provide hit rate against a line. Must calculate from game logs.
**Current**: ✅ Correct approach

### 4. PRA/PR/PA/RA Combo Stats - KEEP CALCULATING
**Reason**: BDL doesn't provide combined stats. Must calculate.
**Current**: ✅ Correct approach

---

## CRITICAL ISSUE: Using Names Instead of BDL IDs

### Current Flow (BROKEN)
```
PrizePicks player name → Search BDL by name → Get ID → Fetch stats
```
**Problems:**
1. Name mismatches: "Jabari Smith Jr" vs "Jabari Smith Jr."
2. Multiple API calls per player
3. Players not found: 11 players failing in sync

### Proposed Flow (OPTIMAL)
```
Sync all BDL players once → Store bdl_id in master_hub → 
PrizePicks name → Lookup bdl_id from cache → Fetch stats by ID
```

---

## ACTION ITEMS

### Priority 1: Fix Name-to-ID Resolution
1. Build a complete player name → BDL ID mapping table
2. Store `bdl_id` in `nba_master_hub_2026` for every player
3. Update sync to use `player_id` instead of name search

### Priority 2: Add Missing High-Value Endpoints
1. **`/player_injuries`** - Add to context badges (gassed, deep_water)
2. **`/v2/odds/player_props`** - Compare with The Odds API for better data
3. **`/lineups`** - Use for "starting lineup" context
4. **`/v2/stats/advanced`** - Get PIE, usage%, defensive rating

### Priority 3: Replace Manual Configs
1. **Team pace data** - Use `/team_season_averages/tracking` instead of config
2. **DvP rankings** - Use `/team_season_averages/defense` instead of config

---

## BDL ID Mapping Strategy

### Step 1: Sync All Active Players
```python
# Fetch all active players and store mapping
GET /nba/v1/players/active?per_page=100
```

### Step 2: Create Name Aliases
```python
NAME_ALIASES = {
    "Jabari Smith Jr": ["Jabari Smith Jr.", "Jabari Smith"],
    "Bones Hyland": ["Nah'Shon Hyland"],
    "G.G. Jackson": ["Gregory Jackson II"],
    # etc.
}
```

### Step 3: Update Sync to Use IDs
```python
# In bdl_comprehensive_sync.py
async def sync_prizepicks_players(self):
    # Get all player names from PrizePicks
    pp_names = await self._get_prizepicks_player_names()
    
    # Lookup BDL IDs from mapping table
    for name in pp_names:
        bdl_id = await self._get_bdl_id_for_name(name)
        if bdl_id:
            await self.sync_player_to_master_hub(bdl_id)
```

---

## Files Requiring Changes

1. `/app/backend/services/bdl_comprehensive_sync.py`
   - Add player name → ID mapping
   - Switch to ID-based sync
   - Add `/players/active` endpoint

2. `/app/backend/utils/player_lookup.py`
   - Store `bdl_id` in lookup cache

3. `/app/backend/routes/scheduler.py`
   - Add injury sync job
   - Add lineup sync job

4. `/app/backend/services/` (NEW FILES)
   - `bdl_injuries_service.py` - Sync injuries
   - `bdl_lineups_service.py` - Sync lineups
   - `bdl_advanced_stats_service.py` - Advanced stats

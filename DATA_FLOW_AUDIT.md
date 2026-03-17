# DATA FLOW ARCHITECTURE AUDIT
## PickVision - March 17, 2026

---

## TASK 1: CURRENT DATA FLOW REVEALED

### Answer: **A - LEGACY (Sync-Dependent)**

The `/api/v3/goblin-vault` and `/api/v3/war-zone` endpoints are **NOT stateless**. They pull from pre-populated MongoDB collections that require background sync jobs.

### Exact Data Flow:

```
REQUEST: GET /api/v3/goblin-vault
    ↓
routes/tiers.py (line 61):
    result = await engine.get_goblin_vault()
    ↓
services/picks_getter_service.py (line 131):
    picks = await self.goblin_vault.find({}, {"_id": 0}).sort("vault_score", -1).to_list(10)
    ↓
MongoDB Collection: dg_goblin_vault
    ↓
PROBLEM: Collection is EMPTY unless sync job has run
```

### Collections Being Read (NOT Live APIs):
- `dg_goblin_vault` - Pre-calculated top 10 goblin picks
- `dg_radar_picks` - Pre-calculated top 10 demon picks  
- `dg_front_lines` - Pre-calculated front lines picks
- `dg_cached_board` - All player props (source of truth for sync)

---

## TASK 2: SYNC DEPENDENCY AUDIT

### Sync Jobs Found:

| File | Purpose | Schedule |
|------|---------|----------|
| `services/cron_scheduler.py` | Daily NBA stats sync | 0400 EST |
| `services/nba_official_sync.py` | NBA API player stats | On-demand |
| `services/tier_builder_service.py` | Builds goblin_vault/radar_picks | After board sync |
| `services/sync_service.py` | Legacy sync orchestration | On-demand |
| `services/odds_sync_service.py` | Odds API -> cached_board | Scheduled intervals |

### Why They Still Exist:
1. **Legacy Architecture**: System was designed as "sync-then-serve"
2. **Rate Limiting**: NBA API has strict rate limits (0.6s per player)
3. **Cost**: Odds API charges per request, so caching saves money
4. **Performance**: Pre-calculation means fast response times

### The Problem:
- Data goes stale between syncs
- Empty collections on fresh environments
- No "Open Door" reactivity - user sees old data

---

## TASK 3: THE "PROPS" DISCONNECT

### Where Props Array Is Lost:

**File: `/app/backend/services/tier_builder_service.py`**
**Lines: 555-620**

When building goblin vault picks, the service creates individual pick objects with **ONE prop per object**, NOT an array of all props:

```python
# Line 555-580: Creates SINGLE-PROP pick object
return {
    "player_name": player_name,
    "stat_type": goblin_stat,        # ← ONE stat type
    "goblin_line": goblin_line,      # ← ONE line
    "direction": goblin_direction,    # ← ONE direction
    # ... NO "props" array here!
}
```

### Expected by Frontend (`TacticalPlayerCard.jsx`, line 224):
```javascript
const { 
    player_name, 
    props = []    // ← EXPECTS ARRAY OF ALL PROPS
} = player;
```

### The Mismatch:
- **Backend sends**: Single pick with one stat_type/line
- **Frontend expects**: Player object with props[] array containing ALL lines

### Fix Location:
The `props` array should be populated in `picks_getter_service.py` when returning picks, by joining against `dg_cached_board` to get ALL player props.

---

## TASK 4: TRANSITION PLAN

### New Stateless Endpoint Created:
**File: `/app/backend/services/stateless_tier_service.py`**

This service implements true "Open Door" policy:

```
REQUEST HITS → FETCH LIVE ODDS → FETCH LIVE GAME LOGS → CALCULATE IN-MEMORY → RETURN
```

### To Enable Stateless Mode:

Add route in `/app/backend/routes/tiers.py`:

```python
@router.get("/v3/goblin-vault-live")
async def get_goblin_vault_live(limit: int = Query(10, ge=1, le=50)):
    """
    STATELESS Goblin Vault - fetches live data on every request.
    No database caching. True Open Door policy.
    """
    from services.stateless_tier_service import get_stateless_tier_service
    service = get_stateless_tier_service()
    return await service.get_goblin_vault_live(limit=limit)
```

### Trade-offs:

| Stateless (Open Door) | Legacy (Sync) |
|----------------------|---------------|
| ✅ Always fresh data | ❌ Stale between syncs |
| ✅ No empty collections | ✅ Fast response (cached) |
| ❌ Slower (API calls) | ✅ Low API costs |
| ❌ Rate limit concerns | ❌ Requires sync jobs |

---

## RECOMMENDED HYBRID APPROACH

Instead of fully stateless, use **cache-on-demand**:

1. Check if cached data exists and is fresh (< 5 min old)
2. If fresh, return cached
3. If stale/empty, fetch live and cache results
4. Return live data immediately while caching in background

This gives the "Open Door" experience without hammering APIs on every request.

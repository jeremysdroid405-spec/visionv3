# PropVision Sync Architecture v2 — Design Document

## Status: PROPOSED — Awaiting Approval Before Implementation

---

## 1. PROPOSED NEW ARCHITECTURE

### Core Principle
**One pipeline, two sports.** Every board publish — NBA or MLB — flows through the same orchestration, the same publish mechanism, the same diff engine, the same cache strategy. Sport-specific logic lives exclusively in adapters.

### System Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                     EVENT SOURCES (Watchers)                     │
│                                                                  │
│  OddsDeltaEngine    InjuryWatcher    GameClockWatcher   Manual  │
│  (budget-aware)     (ESPN/BDL)       (lock windows)     Trigger │
└──────────┬──────────────┬────────────────┬───────────────┬──────┘
           │              │                │               │
           ▼              ▼                ▼               ▼
┌─────────────────────────────────────────────────────────────────┐
│                    REBUILD COORDINATOR                           │
│                                                                  │
│  • Ingests events from all sources                              │
│  • Dedupes noisy/repeated triggers                              │
│  • Classifies: NO-OP / TARGETED / FULL                          │
│  • Enforces sport-level lock (no overlapping runs)              │
│  • Budget throttle gate (Odds API daily/hourly cap)             │
│  • Emits rebuild jobs to the pipeline                           │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                    UNIFIED PIPELINE                              │
│                    (existing framework, extended)                │
│                                                                  │
│  Phase 1: LOAD     — Read raw board, flatten, dedupe            │
│  Phase 2: ENRICH   — Stats, hit rates, CV (adapter)            │
│  Phase 3: SCORE    — Probability, edge, board score (adapter)  │
│  Phase 4: VALIDATE — Attach validation metadata                 │
│  Phase 5: SELECT   — Tier classification + gate checks          │
│  Phase 6: PUBLISH  — Atomic write (temp + rename)              │
│  Phase 6b: DIFF    — Market Moves snapshot diff                 │
│  Phase 7: GEMINI   — Non-blocking AI enrichment                │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                  LIVE BOARD (6 collections)                      │
│                                                                  │
│  NBA: elite_safe_haven / elite_front_lines / elite_war_zone    │
│  MLB: mlb_safe_haven  / mlb_front_lines  / mlb_war_zone       │
│                                                                  │
│  ← ONLY source for all serve-time endpoints                     │
│  ← ONLY written by UnifiedPipeline.atomic_publish               │
└─────────────────────────────────────────────────────────────────┘
```

### Event Flow

```
1. OddsDeltaEngine polls Odds API (budget-aware intervals)
2. Compares new snapshot vs last → generates delta events
3. Meaningful delta? → emits OddsChanged(sport, affected_players, delta_type)
4. RebuildCoordinator receives event
5. Coordinator checks: is this sport already rebuilding? → if yes, queue/skip
6. Coordinator determines scope: TARGETED (just affected props) or FULL
7. Coordinator calls UnifiedPipeline.run(adapter, scope)
8. Pipeline executes Phase 1-7
9. Phase 6: Atomic publish to live collections
10. Phase 6b: Market Moves diff
11. Phase 7: Gemini batch enrichment (non-blocking)
```

---

## 2. FILE / MODULE PLAN

### NEW FILES (create)

| File | Purpose |
|------|---------|
| `services/rebuild_coordinator.py` | Central event ingestion, dedup, scope classification, lock management |
| `services/odds_delta_engine.py` | Budget-aware Odds API polling, snapshot diffing, delta event emission |
| `services/odds_budget_manager.py` | Daily/hourly/peak budget tracking, hot/warm/cold pool classification |
| `services/event_bus.py` | Lightweight in-process event bus (asyncio.Queue-based) |
| `services/watchers/injury_watcher.py` | Normalized injury change detection for both sports |
| `services/watchers/game_clock_watcher.py` | Game approaching lock / game state changes |

### MODIFY (keep, refactor)

| File | Changes |
|------|---------|
| `services/unified_pipeline.py` | Add targeted recalc support (scope parameter). Already has correct phase structure. |
| `services/adapters/nba_adapter.py` | No changes to scoring logic. Add targeted load_board variant. |
| `services/adapters/mlb_adapter.py` | Same as NBA adapter. |
| `services/market_moves_engine.py` | Already correct architecture. No changes needed. |
| `services/gemini_scout_engine.py` | No changes. Already non-blocking batch model. |
| `services/rolling_cache_manager.py` | Refactor to target visible board + near-board only. Remove full-universe enrichment. |
| `services/universal_odds_sync.py` | Extract delta logic into odds_delta_engine. Keep as raw fetcher only. |
| `services/live_injury_micro_sync.py` | Rewire to emit events to RebuildCoordinator instead of writing directly. |
| `routes/ferrari_tiers.py` | Remove legacy rebuild endpoints. All manual syncs route through coordinator. |
| `server.py` | Replace 20 scheduler jobs with coordinator-driven scheduling. Startup wires watchers → coordinator → pipeline. |

### RETIRE / QUARANTINE (move to `legacy_archive/`)

| File | Reason |
|------|--------|
| `services/mlb_sync_engine.py` (985 lines) | Duplicate MLB publish path. Replaced by UnifiedPipeline + MLBAdapter. |
| `services/mlb_master_sync.py` (470 lines) | Duplicate MLB publish path with `_store_tier_results`. |
| `services/mlb_tier_service.py` (2684 lines) | Massive legacy MLB tier builder. Scoring logic extracted to MLBAdapter; `atomic_upsert_tier` replaced by UnifiedPipeline publish. |
| `services/optimized_sync_engine.py` | Writes to old `ferrari_*` collections. Replaced by coordinator + pipeline. |
| `services/ferrari_tier_service.py` | Legacy NBA Ferrari builder. Scoring already in NBAAdapter. |
| `services/tier_builder_service.py` | Legacy NBA tier builder (writes `dg_front_lines`, `dg_radar_picks`). |
| `services/sync_orchestration_service.py` | Legacy orchestrator. Replaced by RebuildCoordinator. |
| `services/engines/demon_goblin_engine.py` | Legacy NBA orchestrator. Replaced by coordinator + NBAAdapter pipeline. |

### LEGACY COLLECTIONS TO DROP (after migration verified)

| Collection | Replacement |
|------------|------------|
| `ferrari_safe_haven` | `elite_safe_haven` |
| `ferrari_front_lines` | `elite_front_lines` |
| `ferrari_war_zone` | `elite_war_zone` |
| `mlb_ferrari_safe_haven` | `mlb_safe_haven` |
| `mlb_ferrari_front_lines` | `mlb_front_lines` |
| `mlb_ferrari_war_zone` | `mlb_war_zone` |
| `dg_front_lines` | `elite_front_lines` |
| `dg_radar_picks` | `elite_war_zone` |
| `goblin_vault` | `elite_safe_haven` |

---

## 3. SHARED COMPONENTS

### RebuildCoordinator (`services/rebuild_coordinator.py`)

```python
class RebuildCoordinator:
    """
    Central nervous system for board refreshes.
    
    Responsibilities:
    - Receive events from all watchers
    - Deduplicate within configurable window (e.g. 30s)
    - Classify rebuild scope (NO_OP, TARGETED, FULL)
    - Enforce per-sport lock (no concurrent rebuilds)
    - Enforce budget throttle (Odds API)
    - Dispatch to UnifiedPipeline
    """
    
    async def handle_event(self, event: BoardEvent)
    async def _classify_scope(self, event) -> RebuildScope
    async def _execute_rebuild(self, sport, scope, affected_picks)
```

**Event Types:**
```python
class BoardEvent:
    sport: str              # "nba" | "mlb"
    event_type: str         # "odds_delta" | "injury_change" | "game_lock" | "manual" | "scheduled_safety"
    severity: str           # "high" | "medium" | "low"
    affected_players: list  # optional — for targeted recalc
    source: str             # "odds_delta_engine" | "injury_watcher" | "manual_api" | "scheduler"
    timestamp: datetime
```

**Scope Classification:**
```
HIGH severity + board picks affected     → FULL rebuild
MEDIUM severity + board picks affected   → TARGETED recalc
LOW severity + no board picks affected   → NO-OP
Any severity + no data change            → NO-OP (deduped)
Manual trigger                           → FULL rebuild (bypass throttle)
```

### OddsDeltaEngine (`services/odds_delta_engine.py`)

```python
class OddsDeltaEngine:
    """
    Budget-aware Odds API poller with snapshot diffing.
    
    Polling strategy:
    - HOT pool (board + near-board): every 5 min during peak, 15 min off-peak
    - WARM pool (active slate): every 30 min
    - COLD pool (inactive): once per day or on-demand
    
    Only emits events when delta is meaningful.
    """
    
    async def poll_cycle(self, sport: str)
    def _compute_delta(self, old_snapshot, new_snapshot) -> List[OddsDelta]
    def _is_meaningful(self, delta: OddsDelta) -> bool
```

**Meaningful Delta Thresholds:**
```
Line change: >= 0.5 points
Odds change: >= 15 cents (-110 → -125)
Prop appeared/disappeared: always meaningful
Book opened/closed: always meaningful
```

### OddsBudgetManager (`services/odds_budget_manager.py`)

```python
class OddsBudgetManager:
    """
    Tracks Odds API call budget.
    
    5M calls/month = ~166,666/day = ~6,944/hour
    
    Allocation strategy:
    - NBA: 55% during NBA season, 30% during offseason
    - MLB: 45% during MLB season, 70% during offseason
    - Reserve: 10% emergency buffer
    
    Peak windows (more calls):
    - 5 PM - 11 PM ET (games in progress)
    
    Off-peak (fewer calls):
    - 12 AM - 10 AM ET
    """
    
    def can_poll(self, sport: str) -> bool
    def record_calls(self, sport: str, count: int)
    def get_interval(self, sport: str, pool: str) -> int  # seconds
```

**Daily Budget Example (both sports active):**
```
Total: 166,666 calls/day
NBA allocation: 83,333 (50%)
MLB allocation: 83,333 (50%)

Per sport breakdown:
  HOT pool:  60% → 50,000 calls/day → ~2,083/hour → poll every ~1.7s
  WARM pool: 30% → 25,000 calls/day → ~1,042/hour → poll every ~3.5s
  COLD pool: 10% → 8,333 calls/day  → ~347/hour   → poll every ~10s

Peak multiplier (5-11 PM ET): 2x allocation
Off-peak: 0.5x allocation
```

### Validation Model (shared, already exists — enforce everywhere)

```python
VALIDATION_SCHEMA = {
    "has_market_data": bool,    # DK/PP odds present
    "has_hit_rates": bool,      # L5/L10 hit rates calculated
    "has_context": bool,        # intel_suite populated
    "has_mlr": bool,            # Lasso/VK model ran
    "has_gemini": bool,         # AI summary generated
    "is_fully_validated": bool, # all required layers present
}
```

### RollingCacheTargetSelector (refactored from rolling_cache_manager)

```python
class RollingCacheTargetSelector:
    """
    Determines which props deserve background enrichment.
    
    Priority tiers:
    1. Currently on visible board (Safe Haven / Front Lines / War Zone)
    2. Near-board candidates (top 5 props per tier that almost qualified)
    3. Props for games starting within 2 hours
    4. Nothing else
    
    Shared for NBA and MLB.
    """
    
    async def get_targets(self, sport: str) -> List[dict]
```

---

## 4. SPORT ADAPTERS

The existing adapter pattern is correct. Each adapter implements:

```python
class SportAdapter(ABC):
    sport: str                          # "nba" | "mlb"
    tier_collections: Dict[str, str]    # tier → collection name
    
    async def load_board(self, db) -> List[Dict]
    async def enrich_and_score(self, props, db) -> List[Dict]
    def select_tiers(self, scored_props) -> Dict[str, List[Dict]]
    async def enrich_intel(self, tiers, db) -> Dict[str, List[Dict]]
```

**NBA-specific (in NBAAdapter):**
- BDL stats + NBA.com L5/L10
- VegasKiller XGBoost model
- Blowout risk, vacuum/momentum, DvP matchup
- Referee Whistle Matrix

**MLB-specific (in MLBAdapter):**
- BDL game logs
- Lasso regression models
- SP matchup, tempo modifier
- Hitter/pitcher classification

**What does NOT go in adapters:**
- Pipeline phase orchestration
- Atomic publish logic
- Market Moves diffing
- Gemini batch calls
- Budget management
- Event handling

---

## 5. DEPLOYMENT PLAN (Safe Migration Sequence)

### Phase 1: Foundation (no visible change)
1. Create `event_bus.py`, `rebuild_coordinator.py`, `odds_budget_manager.py`
2. Create `odds_delta_engine.py` (wraps existing `universal_odds_sync.py`)
3. Create `watchers/injury_watcher.py` (wraps existing injury polling)
4. Wire coordinator startup in `server.py` alongside existing scheduler
5. **Both old and new paths run in parallel** — coordinator logs what it WOULD do but doesn't publish yet

### Phase 2: NBA Migration
1. Route `scheduled_hourly_full_sync` through coordinator → UnifiedPipeline (NBAAdapter)
2. Route `scheduled_daily_sync` through coordinator
3. Route `initial_autonomous_sync` through coordinator
4. Verify `elite_*` collections update correctly
5. Remove `demon_goblin_engine` from startup
6. Drop legacy collections (`ferrari_*`, `dg_front_lines`, etc.)

### Phase 3: MLB Migration
1. Route `scheduled_mlb_daily_sync` through coordinator → UnifiedPipeline (MLBAdapter)
2. Remove `mlb_sync_engine` and `mlb_master_sync` write paths
3. Verify `mlb_*` collections update correctly
4. Move retired files to `legacy_archive/`

### Phase 4: Event-Driven Activation
1. Enable OddsDeltaEngine polling with budget manager
2. Enable InjuryWatcher event emission
3. Enable GameClockWatcher for lock-window detection
4. Reduce scheduler to safety-only refreshes
5. Verify Market Moves fires on every publish

### Phase 5: Cleanup
1. Consolidate `server.py` scheduler (20 jobs → ~5)
2. Consolidate `ferrari_tiers.py` endpoints (16 POSTs → ~4)
3. Archive retired services
4. Drop orphaned collections
5. Update PRD

---

## 6. VALIDATION CHECKLIST

After each phase, run:

```bash
# 1. Board publishes correctly
API_URL=$(grep REACT_APP_BACKEND_URL /app/frontend/.env | cut -d '=' -f2)
curl -s "$API_URL/api/v3/ferrari/safe-haven?sport=nba" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'NBA SH: {d[\"count\"]} picks, status={d[\"status\"]}')"
curl -s "$API_URL/api/v3/ferrari/safe-haven?sport=mlb" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'MLB SH: {d[\"count\"]} picks, status={d[\"status\"]}')"

# 2. Both sports use same publish architecture
grep "MARKET_MOVES" /var/log/supervisor/backend.err.log | tail -10
# Should show BOTH NBA and MLB entries from UnifiedPipeline

# 3. Legacy paths no longer active
grep "dg_front_lines\|dg_radar_picks\|goblin_vault\|ferrari_safe_haven" /var/log/supervisor/backend.err.log | tail -5
# Should return NOTHING

# 4. Market Moves works from actual board changes
curl -s "$API_URL/api/v3/ferrari/market-moves" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Events: {d[\"count\"]}')"

# 5. Live board reacts faster than hourly
# Trigger manual sync, verify board updates within seconds
time curl -s -X POST "$API_URL/api/v2/coordinator/trigger?sport=nba&reason=manual"

# 6. API budget tracked
curl -s "$API_URL/api/v2/odds/budget" | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin), indent=2))"

# 7. No endpoint lag from background enrichment
time curl -s "$API_URL/api/v3/ferrari/safe-haven?sport=nba" > /dev/null
# Should be < 200ms
```

---

## 7. NEW SCHEDULER (Post-Migration)

| Job | Interval | Purpose |
|-----|----------|---------|
| `safety_refresh` | Every 30 min | Full rebuild if no event-driven publish in last 30 min |
| `odds_poll_hot` | Every 5 min (peak) / 15 min (off-peak) | Poll board + near-board props |
| `odds_poll_warm` | Every 30 min | Poll active slate |
| `injury_check` | Every 5 min | Check ESPN + BDL for changes |
| `daily_maintenance` | 4:00 AM ET | BDL game logs sync, roster refresh, stale data cleanup |
| `gemini_batch` | After each publish | Non-blocking AI enrichment for new/changed board picks |

**Removed (20 → 6):**
- 5x NBA L5/L10 batch jobs (BDL game logs already provide this)
- Hourly full sync (replaced by event-driven)
- Hourly badge sync (folded into pipeline Phase 2)
- Hourly injury sync (replaced by injury_watcher)
- Hourly referee sync (folded into pipeline context)
- Half-hourly social sync (low value, folded into daily)
- Hourly vision intel sync (Gemini runs post-publish)
- Weekly roster sync (folded into daily maintenance)
- Forward test capture (unchanged, keep as-is)
- Ticker sync (folded into daily maintenance)

---

## 8. NEW API SURFACE (Post-Migration)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v2/coordinator/trigger` | POST | Manual rebuild trigger (sport, reason) |
| `/api/v2/coordinator/status` | GET | Current coordinator state, last events, lock status |
| `/api/v2/odds/budget` | GET | Budget tracker: daily/hourly usage, remaining |
| `/api/v2/odds/delta` | GET | Recent odds deltas (debugging) |
| `/api/v3/ferrari/safe-haven` | GET | Unchanged — reads live collections |
| `/api/v3/ferrari/front-lines` | GET | Unchanged — reads live collections |
| `/api/v3/ferrari/war-zone` | GET | Unchanged — reads live collections |
| `/api/v3/ferrari/market-moves` | GET | Unchanged — reads market_moves collection |

**Retired endpoints:**
- `POST /api/v3/ferrari/rebuild` → replaced by coordinator trigger
- `POST /api/nba/sync/master` → replaced by coordinator trigger
- `POST /api/nba/sync/elite-top-10` → folded into pipeline
- `POST /api/mlb/sync/master` → replaced by coordinator trigger
- `POST /api/v3/mlb/rebuild` → replaced by coordinator trigger
- `POST /api/v3/mlb/ferrari-pipeline` → dead code, remove

---

## SUMMARY

| Dimension | Current | Target |
|-----------|---------|--------|
| Publish paths (NBA) | 3+ (legacy + unified) | 1 (UnifiedPipeline only) |
| Publish paths (MLB) | 3+ (tier_service + master_sync + unified) | 1 (UnifiedPipeline only) |
| Scheduler jobs | 20 | 6 |
| POST sync endpoints | 17 | 1 (coordinator trigger) |
| Board freshness trigger | Hourly cron | Event-driven (seconds) |
| Odds API strategy | Poll everything every hour | Budget-aware hot/warm/cold pools |
| Market Moves trigger | Some publish paths | Every publish |
| Legacy collections | 9 active | 0 |
| Shared architecture | Partial | 100% identical NBA/MLB |

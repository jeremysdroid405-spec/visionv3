# Universal Live-Board Engine — Multi-Sport Design (v3, LOCKED REQUIREMENTS)

**Status**: DESIGN v3. Locks: board = live query; one engine for all sports; sport = adapter only. Awaiting approval.
**Date**: 2026-04-18

---

## 0. Locked principles (from user directives)

1. Board is a **LIVE QUERY** against a ranked master pool, never a stored table.
2. The **master pool** is the single source of truth, per sport.
3. Real-time ingest: prop in → scored → pool → board reflects instantly.
4. Hourly sync reconciles drift only, never rebuilds.
5. Daily 4 AM run is cleanup/safety, never required for visibility.
6. **ONE universal board engine. Sports are adapters.**
7. Must scale to 10+ sports without re-writing the board lifecycle per sport.

---

## 1. Engine ↔ Adapter contract (the split line)

The engine owns **lifecycle + state + materialisation**. The adapter owns **sport meaning**.

### 1.1 Universal engine responsibilities (sport-agnostic, written ONCE)

| Engine module                 | Responsibility                                                           |
|-------------------------------|--------------------------------------------------------------------------|
| `services/board/engine.py`    | Orchestrates: ingest → score via adapter → upsert pool → drift sync → game-start sweep. Holds per-sport asyncio.Lock. |
| `services/board/pool.py`      | Master-pool CRUD. `upsert_prop(sport, canonical_key, doc)`, `mark_inactive(sport, canonical_key, reason)`, `get_top_n(sport, tier, n)`, `diff(sport, live_prop_keys)`. ONE piece of code, pure sport parameter. |
| `services/board/identity.py`  | Canonical prop identity: `canonical_key(sport, prop) -> str`. Delegates to adapter's `build_canonical_key()` but the engine owns the contract. |
| `services/board/events.py`    | Event dispatcher: `new_props`, `changed_props`, `removed_props`, `injury_change`, `game_started`. Routes to adapter.score_batch(). |
| `services/board/scanner.py`   | Universal `game_start_scanner` loop. Reads `game_start_utc` field from the pool. Works for any sport. |
| `services/board/drift_sync.py`| Universal hourly drift sync. Diffs `{sport}_live_props` vs `{sport}_prop_scores`, routes deltas to events. |
| `services/board/bootstrap.py` | Universal daily bootstrap. Runs full adapter.score_all() once per sport per day. Works for any sport. |
| `services/board/reader.py`    | Single read-path: `get_board(sport, tier, limit)`. Used by every route handler across all sports. |

### 1.2 Sport-adapter responsibilities (one file per sport)

Every sport implements the `SportBoardAdapter` ABC in `services/board/adapters/<sport>.py`. Only sport-specific things live here:

```python
class SportBoardAdapter(ABC):
    # ------- IDENTITY -------
    sport: str                                    # 'nba', 'mlb', 'nfl', 'nhl', 'ncaaf', ...
    live_props_collection: str                    # raw inventory
    scores_collection: str                        # master pool
    cached_board_collection: str                  # enrichment overlay
    version_tag: str                              # 'final-nba', 'final-mlb', ...
    tier_names: tuple[str, ...]                   # ('safe_haven','front_lines','war_zone')
    sort_key_for_tier(tier: str) -> str           # 'vk_prob_over' / 'vk_edge' / 'true_edge'
    capacity_for_tier(tier: str) -> int           # usually 10, overridable per tier per sport

    # ------- IDENTITY FUNCTIONS -------
    @abstractmethod
    def build_canonical_key(prop: Dict) -> str
    """Universal shape: f'{player_name}|{stat_type}|{line}|{direction}|{event_date}' by default.
    Overridable per sport if needed (team props, totals, etc.)."""

    @abstractmethod
    def extract_game_start(prop: Dict) -> datetime | None
    """Pull commence_time / game_time. NBA: 'commence_time' field. MLB: same. NFL: 'game_time_epoch'."""

    # ------- SCORING (the heavy lifting stays per-sport) -------
    @abstractmethod
    async def score_batch(db, canonical_keys: List[str]) -> List[Dict]
    """Score only these props. Returns a list of pool-ready docs with
    (canonical_key, tier, score fields, active=True, game_start_utc).
    NBA delegates to existing VK2/MLR stack. MLB delegates to its HRR model.
    Each sport owns the math; the engine owns the orchestration."""

    @abstractmethod
    def classify_tier(scored: Dict) -> str
    """Apply sport's tier gates. Returns one of tier_names + 'unqualified'.
    Pure function — no DB access."""

    # ------- ENRICHMENT (optional hook) -------
    async def enrich_post_score(db, scored: List[Dict]) -> None
    """Non-blocking side effects: Gemini, cached board patch, badge sync.
    Default: no-op. Each sport overrides only if it wants sport-specific enrichment."""
```

Every single orchestration file in §1.1 is sport-agnostic: it takes `sport` as a string, calls `get_adapter(sport).<thing>()`, and never branches on `if sport == 'nba'`.

---

## 2. Universal schema (one shape for every sport)

The master pool `{sport}_prop_scores` per sport uses the **same schema** — only value contents differ:

```python
{
    "canonical_key": "Jalen Brunson|PTS|28.5|Over|2026-04-18",   # built by adapter
    "sport": "nba",                                               # redundant with collection, but kept for cross-sport queries
    "version_tag": "final-nba",                                   # adapter.version_tag
    "event_id": "…",
    "player_name": "…",
    "stat_type": "…",
    "line": 28.5,
    "direction": "OVER",
    "game_start_utc": datetime(…),                                # REQUIRED — adapter.extract_game_start(prop)
    "active": True,                                               # UNIVERSAL field
    "inactive_reason": None,                                      # UNIVERSAL: 'pulled' | 'game_started' | 'manual'
    "active_changed_at": datetime(…),                             # UNIVERSAL
    "tier": "front_lines" | "safe_haven" | "war_zone" | "unqualified",  # UNIVERSAL tier vocabulary
    "tier_reason": "…",                                           # sport-specific gate result message
    # ——— sport-specific fields below this line ———
    "vision_score": …, "vk_prob_over": …, "vk_edge": …, "pp_utility": …,
    "computed_at": datetime(…),
    "updated_at": datetime(…),
}
```

### 2.1 Universal fields enforced by the engine
- `canonical_key`, `sport`, `version_tag`, `active`, `inactive_reason`, `active_changed_at`, `tier`, `game_start_utc`, `computed_at`, `updated_at`.

### 2.2 Sport-specific fields are free-form
Everything a sport's scoring math produces lives in the same document. Reader routes always project universal fields + whatever extras they need — no cross-sport coupling.

### 2.3 Universal indexes (one script applied to every sport's scores collection)
- `uniq_canonical_version` — `{canonical_key:1, version_tag:1}` unique (already exists)
- `idx_tier_active` — `{tier:1, active:1, vision_score:-1}` covering the board query
- `idx_game_start_active` — `{active:1, game_start_utc:1}` for the scanner
- `idx_updated_at_desc` — `{updated_at:-1}` for observability

Adding a new sport = `mongo init_indexes('nfl_prop_scores')` + register adapter. No engine changes.

---

## 3. Universal board reader (one query, every sport)

```python
# services/board/reader.py
async def get_board(db, sport: str, tier: str, limit: int | None = None) -> List[Dict]:
    adapter = get_adapter(sport)
    now_utc = datetime.now(timezone.utc)
    limit = limit or adapter.capacity_for_tier(tier)
    return await db[adapter.scores_collection].find(
        {
            "version_tag": adapter.version_tag,
            "tier": tier,
            "active": {"$ne": False},
            "game_start_utc": {"$gt": now_utc},       # belt-and-suspenders
        },
        {"_id": 0},
    ).sort(adapter.sort_key_for_tier(tier), -1).limit(limit).to_list(length=limit)
```

Every existing tier endpoint (`/api/v3/ferrari/*`, `/api/v3/mlb/ferrari/*`, and every future sport's equivalent) calls `get_board(sport, tier)` and returns the result. **One reader, N sports.**

---

## 4. Universal ingest & lifecycle (one engine, N sports)

### 4.1 Real-time ingest (universal code)

```python
# services/board/engine.py::on_new_props
async def on_new_props(sport: str, canonical_keys: List[str]):
    adapter = get_adapter(sport)
    scored = await adapter.score_batch(db, canonical_keys)
    for doc in scored:
        doc["active"] = True
        doc["updated_at"] = datetime.now(timezone.utc)
        doc.setdefault("game_start_utc", adapter.extract_game_start(doc))
        await pool.upsert(sport, doc["canonical_key"], doc)
    await adapter.enrich_post_score(db, scored)  # non-blocking
```

Any sport's raw-odds sync writes to its `{sport}_live_props`, then fires `BoardEvent('new_props', sport=…, canonical_keys=…)`. The engine handles the rest.

### 4.2 Drift sync (universal code, every hour)

```python
# services/board/drift_sync.py::run_drift_sync
async def run_drift_sync(sport: str):
    adapter = get_adapter(sport)
    live_keys = {adapter.build_canonical_key(p) async for p in db[adapter.live_props_collection].find({}, {"_id":0})}
    pool_keys = {doc["canonical_key"] async for doc in db[adapter.scores_collection].find({"version_tag": adapter.version_tag}, {"canonical_key":1, "_id":0})}
    missed  = live_keys - pool_keys        # never scored
    removed = pool_keys - live_keys        # pulled from market
    # CHANGED detection uses per-prop hash of (line, price) — identical for all sports
    ...
    if missed:
        await on_new_props(sport, list(missed))
    for ck in removed:
        await pool.mark_inactive(sport, ck, reason='pulled')
```

### 4.3 Game-start scanner (universal, every 60 s)

```python
# services/board/scanner.py
async def scan_game_starts():
    for sport in REGISTERED_SPORTS:
        adapter = get_adapter(sport)
        now_utc = datetime.now(timezone.utc)
        result = await db[adapter.scores_collection].update_many(
            {"active": True, "game_start_utc": {"$lte": now_utc}},
            {"$set": {"active": False, "inactive_reason": "game_started", "active_changed_at": now_utc}},
        )
        if result.modified_count:
            logger.info(f"[SCANNER] {sport}: {result.modified_count} props → inactive")
```

Zero NBA-specific logic. Add NFL? Register its adapter; the scanner automatically includes it.

### 4.4 Daily bootstrap (universal, 4 AM local)

```python
# services/board/bootstrap.py::run_bootstrap
async def run_bootstrap(sport: str):
    adapter = get_adapter(sport)
    all_keys = [adapter.build_canonical_key(p) async for p in db[adapter.live_props_collection].find({}, {"_id":0})]
    scored = await adapter.score_batch(db, all_keys)   # same path as real-time
    for doc in scored:
        await pool.upsert(sport, doc["canonical_key"], doc)
    # prune old inactive rows >48h
    await db[adapter.scores_collection].delete_many({"active": False, "active_changed_at": {"$lt": now_utc - 48h}})
```

---

## 5. Migration map — what's today vs. what becomes universal

| Area                     | Today                                                                                          | Post-redesign                                                                                     |
|--------------------------|------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------|
| Scoring                  | Per-sport (`services/scoring/adapters/nba_scoring.py`, `mlb_scoring.py`) — already plug-in.    | Keep. Wrapped by new adapter interface.                                                           |
| Tier classification      | NBA-specific gates in `adapters/nba_adapter.py::select_tiers` (lines 354-415). MLB-specific gates in `adapters/mlb_adapter.py`. | Per-sport `classify_tier()` method. Universal vocabulary (`safe_haven`, `front_lines`, `war_zone`).|
| Pipeline orchestration   | `services/unified_pipeline.py` — 7 phases, sport-parameterised but publish is sport-specific.  | Replaced by universal `services/board/engine.py`. `unified_pipeline.py` retained for bootstrap only. |
| Board storage            | `elite_*` (NBA) + `mlb_{tier}` (MLB) tier collections, atomically swapped.                     | Retired. Board = live query against `{sport}_prop_scores`.                                        |
| Board reader route       | `routes/ferrari_tiers.py` NBA handlers already query `nba_prop_scores`. MLB handlers query `mlb_{tier}` storage collections. | Both point to `services/board/reader.get_board(sport, tier)`. New sports just register an adapter + add a route group that reuses the same reader.|
| Game-start handling      | None (props stay on board until next full rebuild).                                            | Universal 60s scanner per sport. Belt-and-suspenders reader filter.                               |
| Hourly sync              | Full UnifiedPipeline rebuild per sport.                                                        | Universal `run_drift_sync(sport)` — delta only.                                                   |
| Real-time ingest         | Odds-sync writes `{sport}_live_props`. No event emission. Scoring happens only on hourly/daily. | Odds-sync writes `{sport}_live_props` + emits `new_props` event → universal `on_new_props(sport)` handler scores immediately. |
| New-sport enablement     | Add scoring adapter + add full NBA-style pipeline wrapper + add tier collections + atomic publish + add reader route + wire scheduler. | Implement `SportBoardAdapter` in one file + register. Engine + scanner + bootstrap + drift + reader automatically cover it. |

---

## 6. Registry pattern (how sports plug in)

A single dict in `services/board/adapters/__init__.py`:

```python
from .nba import NBABoardAdapter
from .mlb import MLBBoardAdapter

REGISTRY: dict[str, SportBoardAdapter] = {
    "nba": NBABoardAdapter(),
    "mlb": MLBBoardAdapter(),
}

def get_adapter(sport: str) -> SportBoardAdapter:
    try:
        return REGISTRY[sport.lower()]
    except KeyError:
        raise UnknownSportError(sport)

def registered_sports() -> list[str]:
    return list(REGISTRY.keys())
```

Adding NFL → one file + one line. The scheduler, scanner, drift-sync, reader, observability endpoint all iterate `registered_sports()` — zero code changes to the engine.

---

## 7. Reader-endpoint universalisation (optional follow-up)

Today we have `/api/v3/ferrari/*` (NBA) and `/api/v3/mlb/ferrari/*` (MLB). For a 10-sport future we could unify to `/api/v3/{sport}/board/{tier}` backed by a single handler — but this is a frontend contract change, so **I'll keep the current per-sport routes for now** and only consolidate if you want me to. The engine is already universal; the URL shape is a separate decision.

---

## 8. Observability (universal, auto-scales to every sport)

`/api/board-stats` (existing `/api/full-sync-stats` gets renamed/aliased):

```json
{
  "nba": {
    "pool_size": {"safe_haven": N, "front_lines": N, "war_zone": N, "unqualified": N, "inactive": N},
    "last_ingest_at": "…", "last_ingest_new_props": N,
    "last_drift_sync_at": "…", "last_drift_counts": {...},
    "last_game_start_scan_at": "…", "last_game_start_flips": N,
    "last_bootstrap_at": "…"
  },
  "mlb": { … },
  "nfl": { … }    ← appears automatically when NFL adapter registers
}
```

Loop is `for sport in registered_sports(): …` — no sport-specific branches anywhere.

---

## 9. Implementation order (each step ship-able, reversible, universal)

| Step | Ships                                                                                  | Files                                                    | Scope   |
|------|----------------------------------------------------------------------------------------|----------------------------------------------------------|---------|
| 1    | `services/board/` skeleton: `SportBoardAdapter` ABC, `REGISTRY`, `pool.py`, `reader.py`. Register NBA + MLB adapters wrapping today's scoring. No behaviour change yet — this is just the new module graph. | NEW `services/board/*`; NEW `services/board/adapters/{nba,mlb}.py` | medium  |
| 2    | Add universal fields (`active`, `game_start_utc`, `inactive_reason`, `active_changed_at`) to every upsert-path in `services/scoring/recompute.py`. Adapters set them. | `services/scoring/recompute.py`, existing scoring adapters  | small   |
| 3    | Replace the 6 tier-reader routes (NBA × 3 + MLB × 3) with `services/board/reader.get_board()`. `/api/v3/ferrari/*` and `/api/v3/mlb/ferrari/*` now return the live pool slice. | `routes/ferrari_tiers.py`, `routes/mlb_tiers.py`         | small   |
| 4    | Universal `game_start_scanner` (60 s interval) iterating `registered_sports()`.        | NEW `services/board/scanner.py`; `server.py`             | small   |
| 5    | Universal `drift_sync` replaces hourly full-rebuild for NBA + MLB.                     | NEW `services/board/drift_sync.py`; `server.py`          | medium  |
| 6    | Universal `on_new_props` event + wire into `odds_sync_service` so ingest is real-time. | NEW `services/board/engine.py`; `odds_sync_service.py`   | medium  |
| 7    | Retire `_atomic_publish` → dead code removal + drop `elite_*` / `mlb_{tier}` storage collections after 48 h. | `services/unified_pipeline.py`                           | small   |
| 8    | Regression harness hitting every registered sport; proves ingest < 1 s to board, game-start flip, drift sync, recovery. | NEW `tests/board_engine_verify.py`                       | small   |

**The ORDER of steps 1-3 is critical**: once Step 3 lands, every reader is already sport-agnostic. Adding NFL after that is 2 files: adapter + routes that delegate to `get_board('nfl', tier)`.

---

## 10. Risks + explicit mitigations

| Risk                                                                     | Mitigation                                                                                                                             |
|--------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------|
| Adapter contract drift — sports diverge over time                        | `SportBoardAdapter` ABC; unit test `tests/board_engine_contract.py` asserts every registered adapter implements every abstract method. |
| Per-sport tier-capacity differences (NFL might want SH=15)               | `capacity_for_tier(tier)` is adapter-level, not a global constant.                                                                      |
| Sort-key differences (war_zone uses `true_edge`, safe_haven `vk_prob_over`, NFL might want something else) | `sort_key_for_tier(tier)` is adapter-level.                                                                                             |
| Tier vocabulary mismatch (sport wants extra tier)                        | `tier_names` is an adapter property. Engine iterates over it. Universal query accepts any tier string the adapter declares.            |
| Canonical-key collisions across sports                                   | `{sport}_prop_scores` is per-sport; unique index is per collection. Cross-sport queries (if any) filter by `sport` field.              |
| Scoring latency on a burst of new props                                  | `score_batch()` signature is vectorised; adapter can parallelise internally. Engine awaits the batch as a whole.                        |

---

## 11. What I'll ship first (awaiting your yes)

**Steps 1 + 2 + 3**:
- Universal adapter skeleton (module graph, registry, `get_board` reader).
- Universal pool fields (`active`, `game_start_utc`, etc.) populated at scoring time.
- All 6 existing tier-reader routes redirected to the universal reader.

This lands the full universal-engine foundation + already decouples every reader from per-sport storage collections. Steps 4-7 then layer in the real-time and sweep behaviours on top of a stable universal base. Step 8 proves it for every registered sport.

**Confirm to ship**: (a) yes, Steps 1-3 now as described, (b) broaden first cut to also include the 60 s game-start scanner (Step 4), (c) different approach.

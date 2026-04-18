# Phase 5 Orphan Writer Audit — Data-Driven Removal Plan

**Audit window**: 2026-04-18T06:15:03Z → 2026-04-18T06:30:04Z (15 min)
**Method**: MongoDB profiler level 2 (every op captured) + static grep across `/app/backend` + traffic analysis of `/var/log/supervisor/backend.out.log`.
**Constraint**: read-only, no code changes, no collection drops.

---

## 1. Runtime evidence — every op against the 6 targets (15-min window, merged capture)

| Collection                 | INSERT | UPDATE | DELETE | DROP | RENAME | FIND | TOTAL |
|----------------------------|:------:|:------:|:------:|:----:|:------:|:----:|:-----:|
| elite_safe_haven           |   0    |   12   |   0    |  1   |   0    |  5   |  18   |
| elite_front_lines          |   0    |   10   |   0    |  1   |   0    |  5   |  16   |
| elite_war_zone             |   0    |   10   |   0    |  1   |   0    |  5   |  16   |
| **live_injuries**          |   0    |   0    |   0    |  0   |   0    |  0   |   0   |
| dg_injuries                |   2    |   0    |   2    |  0   |   0    |  0   |   4   |
| bdl_injuries               |   1    |   0    |   1    |  0   |   0    |  0   |   2   |
| _tmp_elite_* (swap intermediate) | 3 |   0   |   0    |  0   |   3    |  0   |   6   |

### Atomic swap sequence (proves who writes to `elite_*`)
```
06:25:57.515  ns=elite_safe_haven             cmd=drop
06:25:57.516  ns=_tmp_elite_safe_haven_d0fced9c  cmd=renameCollection → elite_safe_haven
06:25:57.526  ns=elite_front_lines            cmd=drop
06:25:57.526  ns=_tmp_elite_front_lines_d0fced9c cmd=renameCollection → elite_front_lines
06:25:57.536  ns=elite_war_zone               cmd=drop
06:25:57.536  ns=_tmp_elite_war_zone_d0fced9c   cmd=renameCollection → elite_war_zone
```
All three `elite_*` collections go through an atomic drop+rename on every full rebuild.

---

## 2. Writer inventory (static, every active write path)

| Collection        | Writer file(s) + line                                           | Trigger                              | Status                                      |
|-------------------|-----------------------------------------------------------------|--------------------------------------|---------------------------------------------|
| elite_safe_haven  | `services/unified_pipeline.py:453-456` (`insert_many` → `drop` → `rename`) — dynamic target via `NBAAdapter.tier_collections` (`services/adapters/nba_adapter.py:35-37`) | every `RebuildCoordinator` full rebuild (hourly scheduled_safety, injury high-sev, manual)      | **ACTIVE + DEAD WEIGHT** (reads, but see §3 – no hot reader) |
| elite_front_lines | same                                                            | same                                 | **ACTIVE + DEAD WEIGHT**                    |
| elite_war_zone    | same                                                            | same                                 | **ACTIVE + DEAD WEIGHT**                    |
| elite_* vision_intel updates (12+10+10 in window) | `services/unified_pipeline.py:_run_gemini_enrichment` (~line 540+) | every full rebuild                   | **ACTIVE + DEAD WEIGHT** (Dashboard reads `vision_intel` from `nba_prop_scores` at `routes/ferrari_tiers.py:1194`, NOT from elite_*) |
| live_injuries     | `services/live_injury_micro_sync.py:61` (`delete_many sport=nba`), `:136` (`update_one`), `:154` (`delete_many expires_at<now`) | polled only if `start_micro_loop()` runs — but `start_micro_loop` is **never invoked** anywhere in the repo. The writer path is reachable only via manual `POST /api/v3/injuries/live/sync`. | **LEGACY + MOSTLY DORMANT** (308 stale rows present from prior manual syncs; no scheduled writer) |
| dg_injuries       | `services/injury_service.py:55` (`self.injuries_collection.delete_many({})`) + `:83` (`insert_many legacy_records`) — aliased via `self.injuries_collection = db.dg_injuries` at `:35` | `injury_service.sync_injuries()` called from `server.py` scheduler (`scheduled_live_injury_check` every 5 min, `scheduled_hourly_injury_sync` every 60 min, daily step 3) + `POST /api/v3/injuries/sync` | **ACTIVE + NECESSARY for legacy readers** (see §3) |
| bdl_injuries      | `services/bdl_enhanced_data.py:84` (`delete_many`) + `:132` (`insert_many`) — `BDLEnhancedDataService.sync_injuries()` | daily scheduler step 3 + `AdaptiveSyncEngine._sync_injuries` + `POST /api/v3/sync-injuries`    | **ACTIVE + NECESSARY for legacy readers** (see §3) |

---

## 3. Reader inventory (who depends on each collection)

| Collection        | Reader                                           | Hot path? | Evidence                                         |
|-------------------|--------------------------------------------------|-----------|--------------------------------------------------|
| elite_safe_haven  | `services/market_moves_engine.py:40` (`TIER_COLLECTIONS` → `diff_and_update_from_tiers`) — called from `unified_pipeline.py:302` and `rebuild_coordinator.py:244` | YES (5 finds in window) | Market Moves diff engine reads these to classify entries/exits/movers |
| elite_safe_haven  | `services/injury_advantage.py:47` (read via `compute_injury_advantages`) — called from `routes/vacuum.py:191` | Yes (lighter)  | Injury Advantage panel computations               |
| elite_safe_haven  | `services/nba_master_sync.py:152` (`count_documents` for health stats) | low                | count-only, not content read                     |
| elite_safe_haven  | Legacy `/api/v3/safe-haven` (via `picks_getter_service`) | **effectively cold** — **4 hits** across the entire log vs **18,811 hits** to `/api/v3/ferrari/safe-haven` | traffic breakdown above |
| elite_front_lines | same pattern                                     | same      | 5 hits total on legacy, 5,632 on ferrari/war-zone equivalents |
| elite_war_zone    | same pattern                                     | same      | 2 hits total on legacy                           |
| live_injuries     | `routes/injuries.py:46` via `get_live_injuries()` at `services/live_injury_micro_sync.py:380` | **Light** — 0 hits in window, 901 cumulative (cross entire log) | Serves `/api/v3/injuries/live` but returns stale data (writer dormant) |
| dg_injuries       | `services/live_injury_micro_sync.py:186` (`dg_injuries.find`) — in `_fetch_nba_injuries` fallback | deprecated | used only if the dormant loop runs             |
| dg_injuries       | `services/picks_getter_service.py:343` (`espn_cursor`) | **cold**  | picks_getter is invoked only by legacy `/api/v3/war-zone` etc. (11 hits vs 18,811 on new) |
| dg_injuries       | `services/usage_spike_detector.py:55` (`dg_injuries.find status in [Out,Doubtful]`) — called from `services/board_intelligence_service.py:266` | **HOT** | Usage spike detection is part of every board-intelligence rebuild cycle |
| bdl_injuries      | `services/ferrari_tier_service.py:2239` (deep_water badge) | Warm      | Ferrari tier badge computation                   |
| bdl_injuries      | `services/mlb_tier_service.py:2463` (deep_water badge MLB) | Warm      | same for MLB                                     |
| bdl_injuries      | `routes/cached_data.py:861` (`deep_water`)       | Warm      | Cached data badge path                           |
| bdl_injuries      | `routes/live.py:511` (live headlines)            | Warm      | `/api/v3/live-headlines` pulls injuries         |
| bdl_injuries      | `services/injury_vacuum_service.py:683`          | HOT       | Vacuum service injury filter                     |
| bdl_injuries      | `services/mlb_lineup_ripple_service.py:462`      | HOT       | MLB ripple service                               |
| bdl_injuries      | `services/injury_service.py:304, :397`           | HOT       | feeds alerts / team summaries                    |
| bdl_injuries      | `services/engines/demon_goblin_engine.py:960` (`fetch_injuries`) | **cold**  | demon_goblin is legacy fallback only             |
| bdl_injuries      | `services/picks_getter_service.py:336`           | **cold**  | same as above                                    |
| bdl_injuries      | `services/bdl_enhanced_data.py:169, :177`        | warm      | same service that writes — self-consistent     |
| bdl_injuries      | `routes/injuries.py` (via `injury_service`)      | warm      | `/api/v3/injuries/*`                             |

---

## 4. Classification per the task

### ACTIVE + NECESSARY — do NOT remove yet
- **`bdl_injuries`** — deeply wired into live paths: Ferrari badges (deep_water), injury_vacuum, mlb_ripple, live headlines, injury_service. 10 distinct reader files in hot paths. Writer `bdl_enhanced_data.sync_injuries()` runs on schedule and on several manual endpoints. **Cannot be dropped** until all hot-path readers migrate to `injuries_normalized`.
- **`dg_injuries`** — has a truly hot reader: `usage_spike_detector.py` (part of board intelligence). Writer `injury_service.sync_injuries()` runs every 5 min via `scheduled_live_injury_check`. **Cannot be dropped** until `usage_spike_detector` is migrated to `injuries_normalized`.

### ACTIVE + DEAD WEIGHT — safe to kill the WRITER (but keep the collection temporarily)
- **`elite_safe_haven`, `elite_front_lines`, `elite_war_zone`** writer in `unified_pipeline._atomic_publish` + `_run_gemini_enrichment`:
  - The Dashboard migrated to reading from `nba_prop_scores`. The `elite_*` drops & renames happen every full rebuild but the picks inside them are not the authoritative source anymore.
  - HOWEVER, two live consumers still depend on them: `market_moves_engine` (diff) and `injury_advantage` (panel). If we kill the writer without migrating those, we break Market Moves events and the Injury Advantage surface.
  - **Safe step**: either migrate those readers to read `nba_prop_scores` (filtered by tier), or keep the writer but stop the `_run_gemini_enrichment` updates (they're pure dead weight — nobody reads them back).

### LEGACY + SAFE TO REMOVE (writers first, collection after confirmation window)
- **`live_injuries`** — writer runs only if `start_micro_loop()` is called; grep confirms **it is never called**. The 308 rows present are stale holdover from prior manual triggers. The reader `/api/v3/injuries/live` still exists but serves stale data. Removing this collection requires either (a) migrating the `/api/v3/injuries/live` endpoint to `injuries_normalized` or (b) deprecating the endpoint entirely (traffic is 901 cumulative / 0 in capture window — likely stale frontend polls).

---

## 5. Which readers still depend on each deprecated collection (concise cross-ref)

```
elite_safe_haven    ← market_moves_engine (HOT)
                    ← injury_advantage (warm)
                    ← nba_master_sync (count-only)
                    ← picks_getter_service (cold)

elite_front_lines   ← same set as above
elite_war_zone      ← same set as above

live_injuries       ← routes/injuries.py GET /live  (stale-serving)
                    ← live_injury_micro_sync.get_live_injuries  (only via that route)

dg_injuries         ← usage_spike_detector  (HOT — board intelligence)
                    ← live_injury_micro_sync._fetch_nba_injuries  (dormant)
                    ← picks_getter_service                        (cold)

bdl_injuries        ← ferrari_tier_service          (warm — deep_water badge)
                    ← mlb_tier_service              (warm)
                    ← injury_vacuum_service         (HOT)
                    ← mlb_lineup_ripple_service     (HOT)
                    ← injury_service                (HOT)
                    ← routes/live.py                (warm — headlines)
                    ← routes/cached_data.py         (warm — deep_water)
                    ← bdl_enhanced_data (self-read) (warm)
                    ← picks_getter_service          (cold)
                    ← engines/demon_goblin_engine   (cold)
```

---

## 6. Recommended shutdown order for Phase 5 (data-driven)

Each step is a separate merge, with its own regression fence.

### Step 1 — Dead-weight writer cleanup (smallest blast radius)
1. **Stop Gemini writing to `elite_*`** — in `services/unified_pipeline.py:_run_gemini_enrichment` redirect the `vision_intel` / `vision_summary` update target from `col_map[tier]` to `nba_prop_scores` (version_tag=final-nba, keyed by canonical_key). The Dashboard already reads vision_intel from there — this removes 32+ wasted UPDATE ops per rebuild.
2. **Verify**: after 1 rebuild, confirm `elite_*` UPDATE count drops to 0 while `/api/v3/ferrari/*` responses continue to carry `vision_intel`.

### Step 2 — Migrate `market_moves_engine` off `elite_*`
1. Change `services/market_moves_engine.TIER_COLLECTIONS` for NBA to read from `nba_prop_scores` grouped by `tier` field (same projection; indexed path exists).
2. Update `injury_advantage.TIER_COLLECTIONS` the same way.
3. **Verify**: `/api/v2/event-bus/stats` shows `market_moves` events continue to flow with the same counts pre/post.

### Step 3 — Retire the `_atomic_publish` elite_* writer
1. In `services/adapters/nba_adapter.py`, remove the `tier_collections` entry for NBA **(or) reroute it to `nba_prop_scores`** behind a versioned "publish shape" field so legacy consumers can still be read during a grace window.
2. **Verify**: no runtime writes to `elite_*` in a fresh 15-min profiler capture; Ferrari Dashboard still responds 200 with identical picks.

### Step 4 — Drop `live_injuries`
1. Remove `routes/injuries.py` `GET /live` + `POST /live/sync` OR reroute the GET to `injuries_normalized` with the same response shape.
2. Remove `services/live_injury_micro_sync.fetch_live_injuries` (write path) + `get_live_injuries` (read path).
3. Remove `init_live_injury_service` from `routes/__init__.py:84`.
4. **Verify**: `/api/v3/injuries/live?sport=mlb` either 404 or returns injuries_normalized payload — no callers receive a null dict.

### Step 5 — Retire `dg_injuries`
1. Migrate the ONLY hot reader (`services/usage_spike_detector.py:55`) to read `injuries_normalized` with `tier_level >= 4` instead of `status in [Out, Doubtful]`.
2. Migrate `live_injury_micro_sync._fetch_nba_injuries` fallback (already dormant).
3. Migrate `picks_getter_service:343` (cold path).
4. Remove the dual-write inside `services/injury_service.sync_injuries()` (lines 53-83) that writes the `legacy_records` to `self.injuries_collection` (= `dg_injuries`).
5. **Verify**: board intelligence rebuild still marks the same players as usage-spiked (diff the spike output before/after).

### Step 6 — Retire `bdl_injuries` (highest blast radius — do LAST)
1. Migrate every reader listed in §3 to `injuries_normalized`. The normalized schema already carries all the fields bdl_injuries reads (status, severity, team, player_name, injury_type, description). Biggest single change is `injury_vacuum_service` + `mlb_lineup_ripple_service` — they'll need re-testing for tier/sport filtering.
2. Stop `bdl_enhanced_data.sync_injuries()` writing to `bdl_injuries` (line 84 `delete_many` + 132 `insert_many`). Replace with calls to the injury_normalization layer.
3. **Verify**: deep_water badge still appears for the same players; vacuum alerts unchanged.

### Step 7 — Collection drops (FINAL, after a 48-hour observation window per collection)
In order: `live_injuries` → `elite_war_zone` → `elite_front_lines` → `elite_safe_haven` → `dg_injuries` → `bdl_injuries`.
After each drop, re-run the 15-min profiler capture and confirm ZERO ops land on the dropped namespace (including failed finds/updates from missed readers).

---

## 7. Environment state (post-audit, nothing changed)
- Profiler was enabled at level 2 for this audit. I left it enabled at level 2 for ongoing observation; can lower to `profile 0` on request.
- `orphan_audit_capture` collection exists in MongoDB with the earlier-window snapshot; can be dropped on request.
- No source files modified. `/app/memory/PRD.md` will be updated by the finish summary.

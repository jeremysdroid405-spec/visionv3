# Wave 0 Batch 1 — Residual Hardcoded References (post-plumbing)

Total: 439 refs across 116 files

| file | line | collection | snippet |
|---|---:|---|---|
| `backend/advanced_analytics.py` | 356 | nba_master_hub_2026 | `NOTE: This uses cached injury data from nba_master_hub_2026.` |
| `backend/advanced_analytics.py` | 360 | nba_master_hub_2026 | `cursor = self.db.nba_master_hub_2026.find({` |
| `backend/config/collections.py` | 16 | dg_cached_board | `canonical name (e.g. NBA's `dg_live_props`, `dg_cached_board`).` |
| `backend/config/collections.py` | 16 | dg_live_props | `canonical name (e.g. NBA's `dg_live_props`, `dg_cached_board`).` |
| `backend/config/db_config.py` | 69 | dg_cached_board | `"cached_board": "dg_cached_board",` |
| `backend/config/db_config.py` | 70 | dg_live_props | `"live_props": "dg_live_props",` |
| `backend/config/db_config.py` | 78 | nba_master_hub_2026 | `"master_hub": "nba_master_hub_2026",` |
| `backend/config/db_config.py` | 94 | dg_cached_board | `get_collection_name('cached_board', 'nba')  -> 'dg_cached_board'` |
| `backend/config/settings.py` | 57 | dg_odds_cache | `"odds_cache": "dg_odds_cache",` |
| `backend/config/settings.py` | 58 | dg_cached_board | `"cached_board": "dg_cached_board",` |
| `backend/models/board.py` | 4 | dg_cached_board | `Pydantic models for dg_cached_board - the frontend-ready player data.` |
| `backend/models/board.py` | 18 | dg_cached_board | `This is the main model stored in dg_cached_board.` |
| `backend/models/board.py` | 72 | dg_cached_board | `Represents the complete dg_cached_board collection state.` |
| `backend/models/board.py` | 100 | dg_cached_board | `source: str = "dg_cached_board"` |
| `backend/models/player.py` | 4 | dg_master_roster | `Pydantic models for player data in nba_master_hub_2026 and dg_master_roster.` |
| `backend/models/player.py` | 4 | nba_master_hub_2026 | `Pydantic models for player data in nba_master_hub_2026 and dg_master_roster.` |
| `backend/models/player.py` | 90 | nba_master_hub_2026 | `Player model representing a document in nba_master_hub_2026.` |
| `backend/models/player.py` | 130 | dg_master_roster | `Player model for dg_master_roster collection.` |
| `backend/models/prop.py` | 4 | dg_cached_board | `Pydantic models for betting props in dg_live_props and dg_cached_board.` |
| `backend/models/prop.py` | 4 | dg_live_props | `Pydantic models for betting props in dg_live_props and dg_cached_board.` |
| `backend/models/prop.py` | 28 | dg_live_props | `Betting prop model for dg_live_props collection.` |
| `backend/models/sync.py` | 4 | dg_sync_log | `Pydantic models for sync status tracking in dg_sync_status and dg_sync_log.` |
| `backend/models/sync.py` | 64 | dg_sync_log | `Sync log entry for dg_sync_log collection.` |
| `backend/repositories/board_repo.py` | 19 | dg_cached_board | `self.cached_board = BaseRepository(db.dg_cached_board)` |
| `backend/repositories/board_repo.py` | 20 | dg_live_props | `self.live_props = BaseRepository(db.dg_live_props)` |
| `backend/repositories/player_repo.py` | 19 | dg_master_roster | `self.master_roster = BaseRepository(db.dg_master_roster)` |
| `backend/repositories/sync_repo.py` | 19 | dg_sync_log | `self.sync_log = BaseRepository(db.dg_sync_log)` |
| `backend/routes/ai_context.py` | 34 | nba_master_hub_2026 | `3. Update nba_master_hub_2026 with ai_context_score and ai_context_reason` |
| `backend/routes/ai_context.py` | 61 | nba_master_hub_2026 | `total = await _db.nba_master_hub_2026.count_documents({})` |
| `backend/routes/ai_context.py` | 62 | nba_master_hub_2026 | `with_context = await _db.nba_master_hub_2026.count_documents({"ai_context_score": {"$exists": True}})` |
| `backend/routes/ai_context.py` | 64 | nba_master_hub_2026 | `positive = await _db.nba_master_hub_2026.count_documents({"ai_context_score": {"$gt": 0.6}})` |
| `backend/routes/ai_context.py` | 65 | nba_master_hub_2026 | `neutral = await _db.nba_master_hub_2026.count_documents({"ai_context_score": {"$gte": 0.4, "$lte": 0.6}})` |
| `backend/routes/ai_context.py` | 66 | nba_master_hub_2026 | `negative = await _db.nba_master_hub_2026.count_documents({"ai_context_score": {"$lt": 0.4}})` |
| `backend/routes/ai_context.py` | 68 | nba_master_hub_2026 | `latest = await _db.nba_master_hub_2026.find_one(` |
| `backend/routes/ai_context.py` | 95 | nba_master_hub_2026 | `player = await _db.nba_master_hub_2026.find_one(` |
| `backend/routes/cached_data.py` | 316 | dg_cached_board | `async for doc in engine.db.dg_cached_board.aggregate(pipeline):` |
| `backend/routes/cached_data.py` | 320 | dg_cached_board | `latest = await engine.db.dg_cached_board.find_one(` |
| `backend/routes/cached_data.py` | 941 | nba_master_hub_2026 | `master_hub = db.nba_master_hub_2026` |
| `backend/routes/cached_data.py` | 964 | dg_cached_board | `raw_doc = await db.dg_cached_board.find_one(` |
| `backend/routes/cached_data.py` | 1038 | dg_cached_board | `cached_player = await db.dg_cached_board.find_one(` |
| `backend/routes/command.py` | 113 | nba_master_hub_2026 | `- NBA: Searches nba_master_hub_2026` |
| `backend/routes/command.py` | 193 | nba_master_hub_2026 | `"source": "nba_master_hub_2026"` |
| `backend/routes/command.py` | 214 | dg_live_props | `- Fetches ALL available props from dg_live_props` |
| `backend/routes/command.py` | 232 | dg_cached_board | `all_props = await db.dg_cached_board.find(` |
| `backend/routes/ferrari_tiers.py` | 1003 | dg_cached_board | `Re-built on every call — dg_cached_board is ~126 player docs, negligible.` |
| `backend/routes/ferrari_tiers.py` | 1017 | dg_cached_board | `async for player_doc in _db.dg_cached_board.find({}):` |
| `backend/routes/ferrari_tiers.py` | 1048 | dg_cached_board | `optional matching board entry (from `dg_cached_board`).` |
| `backend/routes/ferrari_tiers.py` | 1303 | dg_cached_board | `wrote into `dg_cached_board`. UNDER picks never went through Gemini, so` |
| `backend/routes/ferrari_tiers.py` | 2238 | dg_cached_board | `- sport=nba: Syncs NBA collections (dg_cached_board, ferrari_* tiers)` |
| `backend/routes/ferrari_tiers.py` | 2478 | dg_live_props | `- Saves to: dg_live_props` |
| `backend/routes/ferrari_tiers.py` | 2526 | dg_live_props | `**NBA**: Returns props from dg_live_props` |
| `backend/routes/ferrari_tiers.py` | 2586 | nba_master_hub_2026 | `- NBA: nba_master_hub_2026` |
| `backend/routes/injuries.py` | 31 | live_injuries | `async def get_live_injuries(sport: str = Query(None, description="Filter by sport: nba or mlb")):` |
| `backend/routes/injuries.py` | 46 | live_injuries | `return await _live_injury_service.get_live_injuries(sport)` |
| `backend/routes/injuries.py` | 60 | live_injuries | `return await _live_injury_service.fetch_live_injuries()` |
| `backend/routes/master_hub.py` | 237 | nba_master_hub_2026 | `This ensures ALL players have fresh stats in nba_master_hub_2026,` |
| `backend/routes/master_hub.py` | 293 | nba_master_hub_2026 | `player = await _db.nba_master_hub_2026.find_one({"bdl_id": bdl_id}, {"_id": 0})` |
| `backend/routes/master_hub.py` | 315 | nba_master_hub_2026 | `player = await _db.nba_master_hub_2026.find_one({"bdl_id": bdl_id}, {"nba_id": 1, "display_name": 1})` |
| `backend/routes/master_hub.py` | 359 | nba_master_hub_2026 | `players = await _db.nba_master_hub_2026.find({` |
| `backend/routes/master_hub.py` | 387 | nba_master_hub_2026 | `"remaining": await _db.nba_master_hub_2026.count_documents({` |
| `backend/routes/master_hub.py` | 487 | nba_master_hub_2026 | `doc = await _db.nba_master_hub_2026.find_one(` |
| `backend/routes/master_hub.py` | 522 | nba_master_hub_2026 | `doc = await _db.nba_master_hub_2026.find_one(` |
| `backend/routes/master_hub.py` | 542 | nba_master_hub_2026 | `Returns the complete baseline_stats object as stored in nba_master_hub_2026.` |
| `backend/routes/master_hub.py` | 551 | nba_master_hub_2026 | `doc = await _db.nba_master_hub_2026.find_one(` |
| `backend/routes/master_hub.py` | 558 | nba_master_hub_2026 | `doc = await _db.nba_master_hub_2026.find_one(` |
| `backend/routes/odds_mapper.py` | 4 | nba_master_hub_2026 | `Permanent mapping between Odds API V4 player names and nba_master_hub_2026 player_ids` |
| `backend/routes/odds_mapper.py` | 54 | nba_master_hub_2026 | `Returns the full player data from nba_master_hub_2026.` |
| `backend/routes/odds_mapper.py` | 126 | nba_master_hub_2026 | `REBUILD the Odds API Mapper from nba_master_hub_2026.` |
| `backend/routes/odds_mapper.py` | 129 | nba_master_hub_2026 | `1. Reading all players from nba_master_hub_2026` |
| `backend/routes/odds_mapper.py` | 135 | nba_master_hub_2026 | `- After any mass update to nba_master_hub_2026` |
| `backend/routes/odds_mapper.py` | 154 | nba_master_hub_2026 | `player_id: The player_id from nba_master_hub_2026` |
| `backend/routes/qa_testing.py` | 39 | dg_cached_board | `cached_board = _db.dg_cached_board` |
| `backend/routes/qa_testing.py` | 123 | dg_cached_board | `cached_board = _db.dg_cached_board` |
| `backend/routes/roster_sync.py` | 129 | dg_master_roster | `This endpoint returns from dg_master_roster.` |
| `backend/routes/roster_sync.py` | 131 | nba_master_hub_2026 | `queries from nba_master_hub_2026 (the Single Source of Truth).` |
| `backend/routes/roster_sync.py` | 206 | nba_master_hub_2026 | `[DEPRECATED] Use BDL game logs from nba_master_hub_2026 instead.` |
| `backend/routes/scheduler.py` | 125 | nba_master_hub_2026 | `await db.nba_master_hub_2026.create_index("display_name")` |
| `backend/routes/scheduler.py` | 126 | nba_master_hub_2026 | `await db.nba_master_hub_2026.create_index("bdl_id")` |
| `backend/routes/scheduler.py` | 127 | dg_cached_board | `await db.dg_cached_board.create_index("player_name")` |
| `backend/routes/scheduler.py` | 140 | nba_master_hub_2026 | `"nba_master_hub_2026": await db.nba_master_hub_2026.count_documents({}),` |
| `backend/routes/scheduler.py` | 141 | dg_cached_board | `"dg_cached_board": await db.dg_cached_board.count_documents({}),` |
| `backend/routes/scheduler.py` | 216 | nba_master_hub_2026 | `Updates nba_master_hub_2026 with L5, L10, and season averages` |
| `backend/routes/scheduler.py` | 351 | nba_master_hub_2026 | `players = await db.nba_master_hub_2026.find({` |
| `backend/routes/scheduler.py` | 375 | nba_master_hub_2026 | `remaining = await db.nba_master_hub_2026.count_documents({` |
| `backend/routes/vacuum.py` | 178 | injuries_normalized | `1. A meaningful injury (tier >= 3) exists on injuries_normalized` |
| `backend/routes/vision.py` | 172 | nba_master_hub_2026 | `player = await _db.nba_master_hub_2026.find_one(` |
| `backend/routes_archive/bdl_advanced.py` | 172 | nba_master_hub_2026 | `hub = db['nba_master_hub_2026']` |
| `backend/routes_archive/bdl_advanced.py` | 254 | nba_master_hub_2026 | `hub = db['nba_master_hub_2026']` |
| `backend/routes_archive/headshots.py` | 164 | nba_master_hub_2026 | `This updates nba_master_hub_2026 to point to local headshot files.` |
| `backend/routes_archive/regression.py` | 83 | nba_master_hub_2026 | `hub = db['nba_master_hub_2026']` |
| `backend/routes_archive/regression.py` | 180 | nba_master_hub_2026 | `hub = db['nba_master_hub_2026']` |
| `backend/routes_archive/regression.py` | 276 | nba_master_hub_2026 | `hub = db['nba_master_hub_2026']` |
| `backend/routes_archive/roster.py` | 57 | nba_master_hub_2026 | `Source: nba_master_hub_2026 (Single Source of Truth)` |
| `backend/routes_archive/roster.py` | 106 | nba_master_hub_2026 | `cursor = db.nba_master_hub_2026.find(query, projection)` |
| `backend/routes_archive/roster.py` | 113 | nba_master_hub_2026 | `total = await db.nba_master_hub_2026.count_documents(query)` |
| `backend/routes_archive/roster.py` | 124 | nba_master_hub_2026 | `"source_collection": "nba_master_hub_2026",` |
| `backend/routes_archive/roster.py` | 158 | nba_master_hub_2026 | `Source: nba_master_hub_2026 with mapping completeness filters` |
| `backend/routes_archive/roster.py` | 179 | nba_master_hub_2026 | `cursor = db.nba_master_hub_2026.find(query, {` |
| `backend/routes_archive/roster.py` | 200 | nba_master_hub_2026 | `total = await db.nba_master_hub_2026.count_documents(query)` |
| `backend/routes_archive/roster.py` | 203 | nba_master_hub_2026 | `total_active = await db.nba_master_hub_2026.count_documents({})` |
| `backend/routes_archive/roster.py` | 209 | nba_master_hub_2026 | `"source_collection": "nba_master_hub_2026",` |
| `backend/routes_archive/roster.py` | 239 | dg_cached_board | `Source: dg_cached_board (derived cache, rebuilt on each sync)` |
| `backend/routes_archive/roster.py` | 283 | dg_cached_board | `players = await db.dg_cached_board.aggregate(pipeline).to_list(None)` |
| `backend/routes_archive/roster.py` | 286 | dg_cached_board | `total = await db.dg_cached_board.count_documents(query)` |
| `backend/routes_archive/roster.py` | 289 | dg_cached_board | `sync_doc = await db.dg_cached_board.find_one({}, {"synced_at": 1}, sort=[("synced_at", -1)])` |
| `backend/routes_archive/roster.py` | 303 | dg_cached_board | `props_stats = await db.dg_cached_board.aggregate(props_pipeline).to_list(1)` |
| `backend/routes_archive/roster.py` | 310 | dg_cached_board | `"source_collection": "dg_cached_board",` |
| `backend/routes_archive/roster.py` | 349 | nba_master_hub_2026 | `full_active_count = await db.nba_master_hub_2026.count_documents({})` |
| `backend/routes_archive/roster.py` | 352 | nba_master_hub_2026 | `mapped_count = await db.nba_master_hub_2026.count_documents({` |
| `backend/routes_archive/roster.py` | 358 | nba_master_hub_2026 | `with_photos = await db.nba_master_hub_2026.count_documents({` |
| `backend/routes_archive/roster.py` | 363 | dg_cached_board | `live_today_count = await db.dg_cached_board.count_documents({})` |
| `backend/routes_archive/roster.py` | 364 | dg_cached_board | `live_with_props = await db.dg_cached_board.count_documents({` |
| `backend/routes_archive/roster.py` | 369 | nba_master_hub_2026 | `hub_sync = await db.nba_master_hub_2026.find_one({}, {"synced_at": 1}, sort=[("synced_at", -1)])` |
| `backend/routes_archive/roster.py` | 370 | dg_cached_board | `board_sync = await db.dg_cached_board.find_one({}, {"synced_at": 1}, sort=[("synced_at", -1)])` |
| `backend/routes_archive/roster.py` | 373 | nba_master_hub_2026 | `teams = await db.nba_master_hub_2026.distinct("team")` |
| `backend/routes_archive/roster.py` | 380 | nba_master_hub_2026 | `"source": "nba_master_hub_2026",` |
| `backend/routes_archive/roster.py` | 388 | nba_master_hub_2026 | `"source": "nba_master_hub_2026 (filtered)",` |
| `backend/routes_archive/roster.py` | 395 | dg_cached_board | `"source": "dg_cached_board",` |
| `backend/routes_archive/vegas_killer.py` | 315 | dg_cached_board | `board = db['dg_cached_board']` |
| `backend/scripts/auto_feature_discovery.py` | 233 | nba_master_hub_2026 | `hub_name = "mlb_master_hub_2026" if sport == "mlb" else "nba_master_hub_2026"` |
| `backend/scripts/backtest_real_lines.py` | 49 | nba_master_hub_2026 | `self.hub = db['nba_master_hub_2026']` |
| `backend/scripts/backtest_vegas_killer.py` | 57 | nba_master_hub_2026 | `self.hub = db['nba_master_hub_2026']` |
| `backend/scripts/ensure_indexes.py` | 30 | dg_master_roster | `"dg_master_roster": [` |
| `backend/scripts/ensure_indexes.py` | 38 | nba_master_hub_2026 | `"nba_master_hub_2026": [` |
| `backend/scripts/ensure_indexes.py` | 47 | dg_live_props | `"dg_live_props": [` |
| `backend/scripts/ensure_indexes.py` | 57 | dg_cached_board | `"dg_cached_board": [` |
| `backend/scripts/ensure_indexes.py` | 81 | dg_sync_log | `"dg_sync_log": [` |
| `backend/scripts/ensure_indexes.py` | 94 | dg_events_cache | `"dg_events_cache": [` |
| `backend/scripts/fix_nba_ghost_season.py` | 28 | nba_master_hub_2026 | `hub = db.nba_master_hub_2026` |
| `backend/scripts/forward_test.py` | 102 | nba_master_hub_2026 | `player = db['nba_master_hub_2026'].find_one({'bdl_id': player_id})` |
| `backend/scripts/full_retrain_10.py` | 124 | nba_master_hub_2026 | `hub_name = "mlb_master_hub_2026" if sport == "mlb" else "nba_master_hub_2026"` |
| `backend/scripts/full_retrain_10.py` | 189 | nba_master_hub_2026 | `hub = db.nba_master_hub_2026` |
| `backend/scripts/init_database.py` | 182 | nba_master_hub_2026 | `await db.nba_master_hub_2026.create_index("display_name")` |
| `backend/scripts/init_database.py` | 183 | nba_master_hub_2026 | `await db.nba_master_hub_2026.create_index("bdl_id")` |
| `backend/scripts/init_database.py` | 184 | nba_master_hub_2026 | `await db.nba_master_hub_2026.create_index("team")` |
| `backend/scripts/init_database.py` | 185 | nba_master_hub_2026 | `logger.info("✓ nba_master_hub_2026 indexes created")` |
| `backend/scripts/init_database.py` | 188 | dg_cached_board | `await db.dg_cached_board.create_index("player_name")` |
| `backend/scripts/init_database.py` | 189 | dg_cached_board | `await db.dg_cached_board.create_index([("player_name", 1), ("commence_time", 1)])` |
| `backend/scripts/init_database.py` | 190 | dg_cached_board | `logger.info("✓ dg_cached_board indexes created")` |
| `backend/scripts/init_database.py` | 211 | nba_master_hub_2026 | `hub_count = await db.nba_master_hub_2026.count_documents({})` |
| `backend/scripts/init_database.py` | 212 | nba_master_hub_2026 | `checks.append(("nba_master_hub_2026", hub_count, hub_count > 0))` |
| `backend/scripts/init_database.py` | 215 | nba_master_hub_2026 | `with_logs = await db.nba_master_hub_2026.count_documents({"bdl_game_logs": {"$exists": True, "$ne": []}})` |
| `backend/scripts/init_database.py` | 219 | dg_cached_board | `board_count = await db.dg_cached_board.count_documents({})` |
| `backend/scripts/init_database.py` | 220 | dg_cached_board | `checks.append(("dg_cached_board", board_count, board_count > 0))` |
| `backend/scripts/nba_advanced_overlay.py` | 5 | nba_master_hub_2026 | `Enriches each game log in nba_master_hub_2026.history arrays` |
| `backend/scripts/nba_advanced_overlay.py` | 121 | nba_master_hub_2026 | `hub = db.nba_master_hub_2026` |
| `backend/scripts/nba_async_ingestion.py` | 134 | nba_master_hub_2026 | `hub = db.nba_master_hub_2026` |
| `backend/scripts/phase2_retrain.py` | 33 | nba_master_hub_2026 | `hub_name = "mlb_master_hub_2026" if sport == "mlb" else "nba_master_hub_2026"` |
| `backend/scripts/retrain_vegas_killer.py` | 51 | nba_master_hub_2026 | `hub_count = db['nba_master_hub_2026'].count_documents({})` |
| `backend/server.py` | 714 | nba_master_hub_2026 | `players_needing = await db.nba_master_hub_2026.count_documents({` |
| `backend/server.py` | 729 | nba_master_hub_2026 | `players = await db.nba_master_hub_2026.find({` |
| `backend/server.py` | 929 | nba_master_hub_2026 | `nba_master_hub_2026.bdl_game_logs for each player.` |
| `backend/server.py` | 1306 | dg_cached_board | `await db.dg_cached_board.create_index([("player_name", ASCENDING)], background=True)` |
| `backend/server.py` | 1307 | dg_cached_board | `await db.dg_cached_board.create_index([("team", ASCENDING)], background=True)` |
| `backend/server.py` | 1308 | dg_cached_board | `await db.dg_cached_board.create_index([("synced_at", DESCENDING)], background=True)` |
| `backend/server.py` | 1309 | dg_cached_board | `await db.dg_cached_board.create_index([("props.stat_type", ASCENDING)], background=True)` |
| `backend/server.py` | 1312 | nba_master_hub_2026 | `await db.nba_master_hub_2026.create_index([("display_name", ASCENDING)], background=True)` |
| `backend/server.py` | 1313 | nba_master_hub_2026 | `await db.nba_master_hub_2026.create_index([("bdl_id", ASCENDING)], unique=True, sparse=True, background=True)` |
| `backend/server.py` | 1314 | nba_master_hub_2026 | `await db.nba_master_hub_2026.create_index([("nba_id", ASCENDING)], sparse=True, background=True)` |
| `backend/server.py` | 1315 | nba_master_hub_2026 | `await db.nba_master_hub_2026.create_index([("team", ASCENDING)], background=True)` |
| `backend/server.py` | 1332 | dg_cached_board | `await db.dg_cached_board.create_index([("player_name", ASCENDING), ("nba_id", ASCENDING)], background=True)` |
| `backend/server.py` | 1333 | dg_cached_board | `await db.dg_cached_board.create_index([("is_active", ASCENDING), ("props.stat_type", ASCENDING)], background=True)` |
| `backend/server.py` | 1334 | nba_master_hub_2026 | `await db.nba_master_hub_2026.create_index([("player_name", ASCENDING), ("nba_id", ASCENDING)], background=True)` |
| `backend/server.py` | 1335 | nba_master_hub_2026 | `await db.nba_master_hub_2026.create_index([("is_active", ASCENDING), ("team", ASCENDING)], background=True)` |
| `backend/server.py` | 1338 | dg_cached_board | `await db.dg_cached_board.create_index([` |
| `backend/server.py` | 1343 | dg_cached_board | `await db.dg_cached_board.create_index([` |
| `backend/server.py` | 1349 | dg_cached_board | `await db.dg_cached_board_temp.create_index([("player_name", ASCENDING)], background=True)` |
| `backend/server.py` | 1349 | dg_cached_board_temp | `await db.dg_cached_board_temp.create_index([("player_name", ASCENDING)], background=True)` |
| `backend/server.py` | 1997 | nba_master_hub_2026 | `master_hub_count = await db.nba_master_hub_2026.count_documents({})` |
| `backend/server.py` | 1998 | dg_cached_board | `cached_board_count = await db.dg_cached_board.count_documents({})` |
| `backend/server.py` | 2056 | nba_master_hub_2026 | `sample_player = await db.nba_master_hub_2026.find_one(` |
| `backend/services/adapters/nba_adapter.py` | 44 | dg_cached_board | `NOTE: The raw dg_cached_board is processed by ferrari_tier_service` |
| `backend/services/badge_resolver.py` | 160 | nba_context_engine | `Fetches flags from nba_context_engine collection and maps them` |
| `backend/services/badge_resolver.py` | 166 | nba_context_engine | `self.context_engine = db.nba_context_engine` |
| `backend/services/badge_resolver.py` | 167 | nba_master_hub_2026 | `self.master_hub = db.nba_master_hub_2026` |
| `backend/services/bdl_comprehensive_sync.py` | 13 | nba_master_hub_2026 | `Stored in: pick_vision.nba_master_hub_2026.baseline_stats` |
| `backend/services/bdl_comprehensive_sync.py` | 93 | nba_master_hub_2026 | `Pulls ALL available data and stores it in nba_master_hub_2026.` |
| `backend/services/bdl_comprehensive_sync.py` | 98 | nba_master_hub_2026 | `self.master_hub = db.nba_master_hub_2026` |
| `backend/services/bdl_comprehensive_sync.py` | 301 | nba_master_hub_2026 | `Returns complete document to be stored in nba_master_hub_2026.` |
| `backend/services/bdl_comprehensive_sync.py` | 525 | nba_master_hub_2026 | `Sync a single player's complete data to nba_master_hub_2026.` |
| `backend/services/bdl_comprehensive_sync.py` | 618 | nba_master_hub_2026 | `4. Update nba_master_hub_2026 for all players` |
| `backend/services/bdl_comprehensive_sync.py` | 1411 | dg_cached_board | `1. Have props in dg_cached_board` |
| `backend/services/bdl_comprehensive_sync.py` | 1415 | dg_cached_board | `active_players = await self.db.dg_cached_board.distinct("player_name")` |
| `backend/services/bdl_enhanced_data.py` | 138 | nba_context_engine | `result = await self.db.nba_context_engine.update_one(` |
| `backend/services/bdl_enhanced_data.py` | 202 | nba_master_hub_2026 | `players = await self.db.nba_master_hub_2026.find(` |
| `backend/services/bdl_enhanced_data.py` | 256 | nba_master_hub_2026 | `await self.db.nba_master_hub_2026.update_one(` |
| `backend/services/bdl_game_logs_sync.py` | 33 | nba_master_hub_2026 | `self.hub = db["nba_master_hub_2026"]` |
| `backend/services/bdl_game_logs_sync_batched.py` | 43 | nba_master_hub_2026 | `self.hub = db["nba_master_hub_2026"]` |
| `backend/services/bdl_player_badge_service.py` | 135 | nba_master_hub_2026 | `self.master_hub = db.nba_master_hub_2026` |
| `backend/services/bdl_stats_calculator.py` | 4 | nba_master_hub_2026 | `Recalculates baseline_stats from bdl_game_logs stored in nba_master_hub_2026.` |
| `backend/services/bdl_stats_calculator.py` | 51 | nba_master_hub_2026 | `3. Updates baseline_stats in nba_master_hub_2026` |
| `backend/services/bdl_stats_calculator.py` | 59 | nba_master_hub_2026 | `players = await db.nba_master_hub_2026.find(` |
| `backend/services/bdl_stats_calculator.py` | 211 | nba_master_hub_2026 | `await db.nba_master_hub_2026.update_one(` |
| `backend/services/board/adapters/base.py` | 11 | dg_cached_board | ``dg_live_props` / `dg_cached_board`) until Phase B/C/D migrations` |
| `backend/services/board/adapters/base.py` | 11 | dg_live_props | ``dg_live_props` / `dg_cached_board`) until Phase B/C/D migrations` |
| `backend/services/board_intelligence_service.py` | 62 | dg_cached_board | `cached_board = db.dg_cached_board` |
| `backend/services/board_intelligence_service.py` | 272 | dg_cached_board | `async for player in db.dg_cached_board.find({}, {"props.event_id": 1}):` |
| `backend/services/board_intelligence_service.py` | 281 | dg_odds_cache | `odds_doc = await db.dg_odds_cache.find_one({"event_id": event_id})` |
| `backend/services/board_intelligence_service.py` | 360 | dg_odds_cache | `cached = await db.dg_odds_cache.find_one({` |
| `backend/services/board_intelligence_service.py` | 532 | dg_cached_board | `player = await db.dg_cached_board.find_one(` |
| `backend/services/board_intelligence_service.py` | 537 | dg_cached_board | `logger.warning(f"[BOARD_INTEL] Player {pick['player_name']} not found in dg_cached_board")` |
| `backend/services/board_intelligence_service.py` | 678 | dg_cached_board | `update_result = await db.dg_cached_board.update_one(` |
| `backend/services/cached_board_builder_service.py` | 33 | nba_master_hub_2026 | `- Mapper returns full player data from nba_master_hub_2026` |
| `backend/services/cached_board_builder_service.py` | 34 | dg_cached_board | `- ZERO-DOWNTIME: Writes to dg_cached_board_temp, then atomic rename` |
| `backend/services/cached_board_builder_service.py` | 34 | dg_cached_board_temp | `- ZERO-DOWNTIME: Writes to dg_cached_board_temp, then atomic rename` |
| `backend/services/cached_board_builder_service.py` | 49 | dg_cached_board | `self.cached_board = db.dg_cached_board` |
| `backend/services/cached_board_builder_service.py` | 50 | dg_cached_board | `self.cached_board_temp = db.dg_cached_board_temp  # Shadow table` |
| `backend/services/cached_board_builder_service.py` | 50 | dg_cached_board_temp | `self.cached_board_temp = db.dg_cached_board_temp  # Shadow table` |
| `backend/services/cached_board_builder_service.py` | 51 | dg_sync_log | `self.sync_log = db.dg_sync_log` |
| `backend/services/cached_board_builder_service.py` | 52 | dg_master_roster | `self.master_roster = db.dg_master_roster` |
| `backend/services/cached_board_builder_service.py` | 77 | nba_master_hub_2026 | `3. Mapper returns full player data from nba_master_hub_2026` |
| `backend/services/cached_board_builder_service.py` | 78 | dg_cached_board | `4. Store everything in dg_cached_board` |
| `backend/services/cached_board_builder_service.py` | 390 | dg_cached_board | `"renameCollection": f"{self.db.name}.dg_cached_board_temp",` |
| `backend/services/cached_board_builder_service.py` | 390 | dg_cached_board_temp | `"renameCollection": f"{self.db.name}.dg_cached_board_temp",` |
| `backend/services/cached_board_builder_service.py` | 391 | dg_cached_board | `"to": f"{self.db.name}.dg_cached_board"` |
| `backend/services/cached_board_builder_service.py` | 760 | nba_master_hub_2026 | `hub_cursor = self.db.nba_master_hub_2026.find(` |
| `backend/services/cached_board_builder_service.py` | 887 | nba_master_hub_2026 | `- nba_id MUST come from nba_master_hub_2026 (SSOT)` |
| `backend/services/cron_scheduler.py` | 13 | nba_master_hub_2026 | `NBA Official API → 0400 CRON → nba_master_hub_2026 → All App Components` |
| `backend/services/cron_scheduler.py` | 23 | nba_master_hub_2026 | `4. All other components read from nba_master_hub_2026 ONLY` |
| `backend/services/data_integrity_service.py` | 32 | dg_cached_board | `self.cached_board = db.dg_cached_board` |
| `backend/services/data_integrity_service.py` | 33 | dg_master_roster | `self.master_roster = db.dg_master_roster` |
| `backend/services/data_integrity_service.py` | 34 | dg_sync_log | `self.sync_log = db.dg_sync_log` |
| `backend/services/engines/adaptive_sync_engine.py` | 114 | dg_cached_board | `self.cached_board_collection = "dg_cached_board"` |
| `backend/services/engines/adaptive_sync_engine.py` | 117 | nba_master_hub_2026 | `self.master_hub_collection = "nba_master_hub_2026"` |
| `backend/services/engines/adaptive_sync_engine.py` | 662 | dg_cached_board | `Update the dg_cached_board collection with PrizePicks odds data.` |
| `backend/services/engines/ai_context_engine.py` | 6 | nba_master_hub_2026 | `and updates the nba_master_hub_2026 with AI-derived context scores.` |
| `backend/services/engines/ai_context_engine.py` | 39 | nba_master_hub_2026 | `3. Updates nba_master_hub_2026 with ai_context_score and ai_context_reason` |
| `backend/services/engines/ai_context_engine.py` | 44 | nba_master_hub_2026 | `self.master_hub = db.nba_master_hub_2026` |
| `backend/services/engines/ai_context_engine.py` | 228 | nba_master_hub_2026 | `Loop through all active players in nba_master_hub_2026 and update` |
| `backend/services/engines/ai_context_engine.py` | 237 | nba_master_hub_2026 | `logger.info("[AI_CONTEXT] Starting context score update for nba_master_hub_2026...")` |
| `backend/services/engines/board_intelligence_engine.py` | 81 | dg_cached_board | `self.dg_cached_board = self.db["dg_cached_board"]` |
| `backend/services/engines/board_intelligence_engine.py` | 208 | dg_cached_board | `async for board in self.dg_cached_board.find({"type": "main_board"}):` |
| `backend/services/engines/board_intelligence_engine.py` | 436 | dg_cached_board | `async for player in self.dg_cached_board.find({"board.players": {"$exists": True}}):` |
| `backend/services/engines/board_intelligence_engine.py` | 504 | dg_cached_board | `async for player in self.dg_cached_board.find({"board.players": {"$exists": True}}):` |
| `backend/services/engines/board_intelligence_engine.py` | 516 | dg_cached_board | `async for player in self.dg_cached_board.find({"board.players": {"$exists": True}}):` |
| `backend/services/engines/demon_goblin_engine.py` | 419 | dg_events_cache | `self.events_cache = db.dg_events_cache` |
| `backend/services/engines/demon_goblin_engine.py` | 420 | dg_odds_cache | `self.odds_cache = db.dg_odds_cache` |
| `backend/services/engines/demon_goblin_engine.py` | 423 | dg_sync_log | `self.sync_log = db.dg_sync_log` |
| `backend/services/engines/demon_goblin_engine.py` | 428 | dg_live_props | `self.live_props = db.dg_live_props  # Master props collection (deduplicated)` |
| `backend/services/engines/demon_goblin_engine.py` | 434 | dg_cached_board | `self.cached_board = db.dg_cached_board  # Full cached board for frontend` |
| `backend/services/engines/demon_goblin_engine.py` | 435 | dg_master_roster | `self.master_roster = db.dg_master_roster  # SOURCE OF TRUTH: Player-to-team mapping` |
| `backend/services/engines/demon_goblin_engine.py` | 437 | nba_master_hub_2026 | `self.master_hub = db.nba_master_hub_2026  # BDL SSOT for all player stats` |
| `backend/services/engines/game_lock_engine.py` | 49 | dg_cached_board | `self.cached_board = db.dg_cached_board` |
| `backend/services/engines/intel_briefing_engine.py` | 53 | dg_cached_board | `self.cached_board = db.dg_cached_board` |
| `backend/services/engines/nba_master_hub.py` | 92 | nba_master_hub_2026 | `COLLECTION_NAME = "nba_master_hub_2026"` |
| `backend/services/engines/social_signal_engine.py` | 81 | dg_cached_board | `cursor = self.db.dg_cached_board.find({}, {"player_name": 1})` |
| `backend/services/engines/social_signal_engine.py` | 133 | nba_master_hub_2026 | `hub_player = await self.db.nba_master_hub_2026.find_one({` |
| `backend/services/engines/social_signal_engine.py` | 151 | dg_cached_board | `board_player = await self.db.dg_cached_board.find_one({` |
| `backend/services/ferrari_tier_service.py` | 184 | dg_cached_board | `self.cached_board = db.dg_cached_board` |
| `backend/services/ferrari_tier_service.py` | 186 | nba_master_hub_2026 | `self.master_hub = db.nba_master_hub_2026` |
| `backend/services/ferrari_tier_service.py` | 519 | nba_master_hub_2026 | `master_hub = self.db.nba_master_hub_2026` |
| `backend/services/ferrari_tier_service.py` | 539 | nba_master_hub_2026 | `logger.info(f"[BDL-SSOT] Loaded context data for {len(context_data)} players from nba_master_hub_2026")` |
| `backend/services/ferrari_tier_service.py` | 1393 | dg_cached_board | `cached_board = self.db.dg_cached_board` |
| `backend/services/ferrari_tier_service.py` | 2262 | nba_context_engine | `context_engine = self.db['nba_context_engine']` |
| `backend/services/ferrari_tier_service.py` | 2337 | nba_master_hub_2026 | `async for player in self.db.nba_master_hub_2026.find(` |
| `backend/services/forward_testing_service.py` | 362 | dg_cached_board | `collection = self.db["dg_cached_board"]` |
| `backend/services/headshot_service.py` | 210 | nba_master_hub_2026 | `cursor = self.db.nba_master_hub_2026.find(` |
| `backend/services/headshot_service.py` | 272 | nba_master_hub_2026 | `- nba_master_hub_2026.photo_url` |
| `backend/services/headshot_service.py` | 286 | nba_master_hub_2026 | `cursor = self.db.nba_master_hub_2026.find(` |
| `backend/services/headshot_service.py` | 302 | nba_master_hub_2026 | `await self.db.nba_master_hub_2026.update_one(` |
| `backend/services/historical_data_fetcher.py` | 44 | nba_master_hub_2026 | `self.hub = db['nba_master_hub_2026']` |
| `backend/services/injury_advantage.py` | 8 | injuries_normalized | `This engine reads ONLY from injuries_normalized (BDL-derived).` |
| `backend/services/injury_advantage.py` | 154 | injuries_normalized | `cursor = db["injuries_normalized"].find(` |
| `backend/services/injury_normalization.py` | 18 | injuries_normalized | `Writes to: `injuries_normalized` collection (replaces dg_injuries + bdl_injuries)` |
| `backend/services/injury_normalization.py` | 40 | injuries_normalized | `COLLECTION_NAME = "injuries_normalized"` |
| `backend/services/injury_service.py` | 36 | dg_cached_board | `self.cached_board = db.dg_cached_board` |
| `backend/services/injury_service.py` | 43 | injuries_normalized | `Writes to both `injuries_normalized` (new canonical) and `dg_injuries` (legacy compat).` |
| `backend/services/injury_sources/nba_official_source.py` | 15 | injuries_normalized | `injuries_normalized directly.` |
| `backend/services/injury_sources/nba_official_source.py` | 354 | injuries_normalized | `to injuries_normalized. BDL remains the sole structural authority.` |
| `backend/services/injury_triggered_rescore.py` | 10 | dg_cached_board | `- `dg_cached_board`                           — refresh injury_status +` |
| `backend/services/injury_triggered_rescore.py` | 16 | injuries_normalized | `injuries_normalized  (written by InjurySensor; merges BDL + ESPN + NBA` |
| `backend/services/injury_triggered_rescore.py` | 160 | dg_cached_board | `slate. `dg_cached_board` is the canonical "players with props` |
| `backend/services/injury_triggered_rescore.py` | 162 | dg_live_props | `teammates there. `dg_live_props` has no `team` field — historical` |
| `backend/services/injury_triggered_rescore.py` | 177 | dg_cached_board | `async for doc in self._db.dg_cached_board.find(` |
| `backend/services/injury_triggered_rescore.py` | 190 | dg_cached_board | `async for doc in self._db.dg_cached_board.find(` |
| `backend/services/injury_triggered_rescore.py` | 211 | dg_cached_board | `3. targeted dg_cached_board patch → injury_status,` |
| `backend/services/injury_triggered_rescore.py` | 299 | dg_cached_board | `on each impacted player doc in `dg_cached_board`.` |
| `backend/services/injury_triggered_rescore.py` | 301 | injuries_normalized | `Source of truth: `injuries_normalized` (written by InjurySensor).` |
| `backend/services/injury_triggered_rescore.py` | 322 | dg_cached_board | `async for doc in self._db.dg_cached_board.find(` |
| `backend/services/injury_triggered_rescore.py` | 337 | injuries_normalized | `async for rec in self._db.injuries_normalized.find(` |
| `backend/services/injury_triggered_rescore.py` | 398 | dg_cached_board | `result = await self._db.dg_cached_board.update_one(` |
| `backend/services/injury_vacuum_service.py` | 195 | star_usage_cache | `db_star = sync_db.star_usage_cache.find_one(` |
| `backend/services/injury_vacuum_service.py` | 246 | nba_master_hub_2026 | `hub_player = sync_db.nba_master_hub_2026.find_one(` |
| `backend/services/injury_vacuum_service.py` | 266 | nba_master_hub_2026 | `"source": "nba_master_hub_2026"` |
| `backend/services/injury_vacuum_service.py` | 310 | star_usage_cache | `teammates = list(sync_db.star_usage_cache.find(` |
| `backend/services/injury_vacuum_service.py` | 321 | nba_master_hub_2026 | `hub_data = sync_db.nba_master_hub_2026.find_one(` |
| `backend/services/injury_vacuum_service.py` | 336 | nba_master_hub_2026 | `hub_teammates = list(sync_db.nba_master_hub_2026.find(` |
| `backend/services/injury_vacuum_service.py` | 359 | nba_master_hub_2026 | `'source': 'nba_master_hub_2026'` |
| `backend/services/injury_vacuum_service.py` | 433 | star_usage_cache | `"source": teammate.get('source', 'star_usage_cache')` |
| `backend/services/injury_vacuum_service.py` | 452 | nba_master_hub_2026 | `For dynamic model, we fetch baseline stats from nba_master_hub_2026.` |
| `backend/services/injury_vacuum_service.py` | 463 | nba_master_hub_2026 | `player = sync_db.nba_master_hub_2026.find_one(` |
| `backend/services/injury_vacuum_service.py` | 628 | injuries_normalized | `Fetch the latest NBA injury report from injuries_normalized (BDL sourced).` |
| `backend/services/injury_vacuum_service.py` | 647 | injuries_normalized | `cursor = self.db.injuries_normalized.find(query, {"_id": 0})` |
| `backend/services/injury_vacuum_service.py` | 650 | injuries_normalized | `logger.warning(f"[VacuumService] injuries_normalized query failed: {db_err}")` |
| `backend/services/injury_vacuum_service.py` | 672 | injuries_normalized | `logger.info(f"[VacuumService] Found {len(injuries)} injuries from injuries_normalized (BDL)")` |
| `backend/services/injury_vacuum_service.py` | 1059 | star_usage_cache | `Dynamic Usage Model v3.0 - loads from star_usage_cache collection.` |
| `backend/services/injury_vacuum_service.py` | 1070 | star_usage_cache | `stars = list(sync_db.star_usage_cache.find(` |
| `backend/services/insights_sync_service.py` | 26 | nba_master_hub_2026 | `BDL (nba_master_hub_2026) is the ONLY source for player stats.` |
| `backend/services/insights_sync_service.py` | 33 | dg_cached_board | `self.cached_board = db.dg_cached_board` |
| `backend/services/insights_sync_service.py` | 34 | nba_master_hub_2026 | `self.master_hub = db.nba_master_hub_2026  # BDL SSOT` |
| `backend/services/insights_sync_service.py` | 45 | nba_master_hub_2026 | `Calculates advanced analytics using BDL data from nba_master_hub_2026.` |
| `backend/services/intel_suite_calculator.py` | 115 | nba_master_hub_2026 | `self.master_hub = db.nba_master_hub_2026` |
| `backend/services/live_injury_micro_sync.py` | 9 | live_injuries | `- Dedicated `live_injuries` collection with 60-second max cache` |
| `backend/services/live_injury_micro_sync.py` | 26 | live_injuries | `LIVE_INJURIES_COLLECTION = "live_injuries"` |
| `backend/services/live_injury_micro_sync.py` | 40 | live_injuries | `Writes to dedicated live_injuries collection for JIT checks.` |
| `backend/services/live_injury_micro_sync.py` | 45 | live_injuries | `self.live_injuries = db[LIVE_INJURIES_COLLECTION]` |
| `backend/services/live_injury_micro_sync.py` | 61 | live_injuries | `r = await self.live_injuries.delete_many({"sport": "nba"})` |
| `backend/services/live_injury_micro_sync.py` | 65 | injuries_normalized | `"NBA rows from live_injuries (canonical: injuries_normalized)"` |
| `backend/services/live_injury_micro_sync.py` | 65 | live_injuries | `"NBA rows from live_injuries (canonical: injuries_normalized)"` |
| `backend/services/live_injury_micro_sync.py` | 89 | live_injuries | `await self.fetch_live_injuries()` |
| `backend/services/live_injury_micro_sync.py` | 95 | live_injuries | `async def fetch_live_injuries(self) -> Dict[str, Any]:` |
| `backend/services/live_injury_micro_sync.py` | 100 | live_injuries | `Writes directly to live_injuries collection.` |
| `backend/services/live_injury_micro_sync.py` | 136 | live_injuries | `await self.live_injuries.update_one(` |
| `backend/services/live_injury_micro_sync.py` | 154 | live_injuries | `await self.live_injuries.delete_many({` |
| `backend/services/live_injury_micro_sync.py` | 312 | injuries_normalized | `Reads from injuries_normalized (authoritative BDL-derived source).` |
| `backend/services/live_injury_micro_sync.py` | 315 | injuries_normalized | `injury = await self.db.injuries_normalized.find_one(` |
| `backend/services/live_injury_micro_sync.py` | 380 | live_injuries | `async def get_live_injuries(self, sport: str = None) -> Dict[str, Any]:` |
| `backend/services/live_injury_micro_sync.py` | 390 | live_injuries | `injuries = await self.live_injuries.find(` |
| `backend/services/market_moves_engine.py` | 11 | injuries_normalized | `injury_repriced      — Player has tier 3+ injury in injuries_normalized` |
| `backend/services/market_moves_engine.py` | 191 | injuries_normalized | `inj_cursor = db.injuries_normalized.find(` |
| `backend/services/market_moves_engine.py` | 221 | dg_cached_board | `board_cursor = db.dg_cached_board.find(` |
| `backend/services/market_moves_engine.py` | 244 | nba_master_hub_2026 | `hub_cursor = db.nba_master_hub_2026.find(` |
| `backend/services/master_hub_sync.py` | 4 | nba_master_hub_2026 | `Daily sync job to populate nba_master_hub_2026 with:` |
| `backend/services/master_hub_sync.py` | 48 | nba_master_hub_2026 | `self.master_hub = db.nba_master_hub_2026` |
| `backend/services/mlb_tier_service.py` | 2486 | nba_context_engine | `context_engine = self.db['nba_context_engine']` |
| `backend/services/nba_official_sync.py` | 101 | nba_master_hub_2026 | `self.hub = db.nba_master_hub_2026` |
| `backend/services/odds_api_mapper.py` | 5 | nba_master_hub_2026 | `Permanent mapping between nba_master_hub_2026 and The Odds API V4 player name strings.` |
| `backend/services/odds_api_mapper.py` | 28 | nba_master_hub_2026 | `MASTER_HUB_COLLECTION = "nba_master_hub_2026"` |
| `backend/services/odds_api_mapper.py` | 83 | nba_master_hub_2026 | `Permanent mapping between Odds API V4 player names and nba_master_hub_2026 player_ids.` |
| `backend/services/odds_api_mapper.py` | 134 | nba_master_hub_2026 | `Returns the full player object from nba_master_hub_2026.` |
| `backend/services/odds_api_mapper.py` | 159 | nba_master_hub_2026 | `IMPORTANT: Merges mapping docs with nba_master_hub_2026 data to ensure` |
| `backend/services/odds_api_mapper.py` | 232 | nba_master_hub_2026 | `REBUILD MAPPING - Generate odds_api_mapping_master from nba_master_hub_2026.` |
| `backend/services/odds_api_mapper.py` | 235 | nba_master_hub_2026 | `1. Reading all players from nba_master_hub_2026` |
| `backend/services/odds_api_mapper.py` | 241 | nba_master_hub_2026 | `- After any mass update to nba_master_hub_2026` |
| `backend/services/odds_api_mapper.py` | 258 | nba_master_hub_2026 | `logger.info("[ODDS_MAPPER] Step A: Reading from nba_master_hub_2026...")` |
| `backend/services/odds_api_mapper.py` | 303 | nba_master_hub_2026 | `"source": "nba_master_hub_2026"` |
| `backend/services/odds_api_service.py` | 55 | dg_events_cache | `self.events_cache = db.dg_events_cache` |
| `backend/services/odds_api_service.py` | 56 | dg_odds_cache | `self.odds_cache = db.dg_odds_cache` |
| `backend/services/odds_sync_service.py` | 32 | dg_live_props | `self.live_props = db.dg_live_props` |
| `backend/services/odds_sync_service.py` | 33 | dg_master_roster | `self.master_roster = db.dg_master_roster` |
| `backend/services/optimized_sync_engine.py` | 38 | nba_master_hub_2026 | `"master_hub": "nba_master_hub_2026",` |
| `backend/services/optimized_sync_engine.py` | 39 | dg_cached_board | `"cached_board": "dg_cached_board",  # Legacy NBA naming (no prefix for backwards compat)` |
| `backend/services/optimized_sync_engine.py` | 40 | dg_live_props | `"live_props": "dg_live_props",` |
| `backend/services/optimized_sync_engine.py` | 311 | dg_cached_board | `from `dg_cached_board` so the downstream `enrich_pick_with_cache`` |
| `backend/services/optimized_sync_engine.py` | 328 | dg_cached_board | `async for pd in db.dg_cached_board.find(` |
| `backend/services/optimized_sync_engine.py` | 643 | dg_cached_board | `5. Update dg_cached_board with enriched intel_suite data` |
| `backend/services/oracle_apex_service.py` | 428 | dg_cached_board | `self.cached_board = db.dg_cached_board` |
| `backend/services/oracle_apex_service.py` | 429 | dg_live_props | `self.live_props = db.dg_live_props` |
| `backend/services/oracle_apex_service.py` | 430 | nba_master_hub_2026 | `self.master_hub = db.nba_master_hub_2026` |
| `backend/services/photo_service.py` | 11 | nba_master_hub_2026 | `1. ESPN IDs already in nba_master_hub_2026 (from BDL sync)` |
| `backend/services/photo_service.py` | 30 | nba_master_hub_2026 | `self.master_hub = db.nba_master_hub_2026` |
| `backend/services/photo_service.py` | 31 | dg_cached_board | `self.cached_board = db.dg_cached_board` |
| `backend/services/photo_storage_service.py` | 20 | nba_master_hub_2026 | `self.master_hub = db.nba_master_hub_2026` |
| `backend/services/picks/board_formatter.py` | 22 | dg_cached_board | `Reads from dg_cached_board and formats responses` |
| `backend/services/picks/board_formatter.py` | 28 | dg_cached_board | `self.cached_board = db.dg_cached_board` |
| `backend/services/picks/photo_service.py` | 28 | nba_master_hub_2026 | `self.master_hub = db.nba_master_hub_2026` |
| `backend/services/picks/player_stats_resolver.py` | 22 | nba_master_hub_2026 | `Uses nba_master_hub_2026 as the primary source for:` |
| `backend/services/picks/player_stats_resolver.py` | 30 | nba_master_hub_2026 | `self.master_hub = db.nba_master_hub_2026` |
| `backend/services/picks/player_stats_resolver.py` | 31 | dg_master_roster | `self.master_roster = db.dg_master_roster` |
| `backend/services/picks_getter_service.py` | 7 | nba_master_hub_2026 | `- PIPE 1: nba_master_hub_2026 (stats vault, populated by 0400 CRON)` |
| `backend/services/picks_getter_service.py` | 8 | dg_cached_board | `- PIPE 2: dg_cached_board (live lines, populated by Odds API polling)` |
| `backend/services/picks_getter_service.py` | 227 | nba_master_hub_2026 | `- Stats from nba_master_hub_2026 (PIPE 1)` |
| `backend/services/picks_getter_service.py` | 228 | dg_cached_board | `- Lines from dg_cached_board (PIPE 2)` |
| `backend/services/picks_getter_service.py` | 246 | dg_cached_board | `self.cached_board = db.dg_cached_board  # Active Lines` |
| `backend/services/picks_getter_service.py` | 249 | dg_sync_log | `self.sync_log = db.dg_sync_log` |
| `backend/services/picks_getter_service.py` | 250 | dg_events_cache | `self.events_cache = db.dg_events_cache` |
| `backend/services/picks_getter_service.py` | 251 | dg_odds_cache | `self.odds_cache = db.dg_odds_cache` |
| `backend/services/picks_getter_service.py` | 254 | nba_master_hub_2026 | `self.master_hub = db.nba_master_hub_2026` |
| `backend/services/picks_getter_service.py` | 301 | dg_master_roster | `roster_cursor = self.db.dg_master_roster.find(` |
| `backend/services/picks_getter_service.py` | 2279 | nba_master_hub_2026 | `Stats (L5/L10/SZN) come EXCLUSIVELY from nba_master_hub_2026.baseline_stats.` |
| `backend/services/picks_getter_service.py` | 2428 | nba_master_hub_2026 | `All stats come from nba_master_hub_2026 (PIPE 1).` |
| `backend/services/picks_getter_service.py` | 2679 | nba_master_hub_2026 | `ALL data comes from nba_master_hub_2026 (PIPE 1).` |
| `backend/services/picks_getter_service.py` | 2757 | nba_master_hub_2026 | `All stats come from nba_master_hub_2026 (PIPE 1).` |
| `backend/services/probability_score_service.py` | 284 | nba_master_hub_2026 | `self.master_hub = db.nba_master_hub_2026` |
| `backend/services/props_service.py` | 74 | nba_master_hub_2026 | `self.master_hub = db.nba_master_hub_2026` |
| `backend/services/props_service.py` | 128 | nba_master_hub_2026 | `Calculate hit rates for a prop using BDL game logs from nba_master_hub_2026.` |
| `backend/services/rolling_cache_manager.py` | 630 | dg_cached_board | `board_doc = await self.db.dg_cached_board.find_one(` |
| `backend/services/rolling_cache_manager.py` | 652 | nba_master_hub_2026 | `hub = self.db.nba_master_hub_2026` |
| `backend/services/rolling_cache_manager.py` | 783 | dg_cached_board | `collection = db.dg_cached_board` |
| `backend/services/roster_service.py` | 71 | dg_master_roster | `self.master_roster = db.dg_master_roster` |
| `backend/services/roster_service.py` | 72 | nba_master_hub_2026 | `self.master_hub = db.nba_master_hub_2026  # BDL SSOT` |
| `backend/services/roster_service.py` | 231 | dg_cached_board | `cached_board = self.db.dg_cached_board` |
| `backend/services/roster_sync_service.py` | 41 | nba_master_hub_2026 | `self.master_hub = db.nba_master_hub_2026` |
| `backend/services/roster_sync_service.py` | 197 | dg_cached_board | `player_names = await self.db.dg_cached_board.distinct("player_name")` |
| `backend/services/scoring/adapters/nba_scoring.py` | 2 | dg_live_props | `NBA Scoring Adapter — reads dg_live_props (NBA's canonical layered-equivalent` |
| `backend/services/scoring/adapters/nba_scoring.py` | 6 | dg_live_props | `used by MLB. Instead, dg_live_props stores PP as the primary row with a` |
| `backend/services/scoring/adapters/nba_scoring.py` | 173 | dg_live_props | `return "dg_live_props"` |
| `backend/services/scoring/adapters/nba_scoring.py` | 181 | dg_cached_board | `return "dg_cached_board"` |
| `backend/services/scoring/adapters/nba_scoring.py` | 390 | nba_master_hub_2026 | `hub = db["nba_master_hub_2026"]` |
| `backend/services/scoring/calibration_store.py` | 31 | dg_live_props | `_LIVE_PROPS_BY_SPORT = {"mlb": "mlb_live_props", "nba": "dg_live_props"}` |
| `backend/services/sharp_edge_calculator.py` | 189 | dg_odds_cache | `cached = await self.db.dg_odds_cache.find_one({` |
| `backend/services/sidecar/hook_bait_detector.py` | 57 | nba_master_hub_2026 | `self.master_hub = db.nba_master_hub_2026` |
| `backend/services/ssot_data_layer.py` | 14 | nba_master_hub_2026 | `│  PIPE 1: Stats Vault (nba_master_hub_2026)                                 │` |
| `backend/services/ssot_data_layer.py` | 19 | dg_cached_board | `│  PIPE 2: Live Wire (dg_cached_board / Active_Lines)                        │` |
| `backend/services/ssot_data_layer.py` | 47 | nba_master_hub_2026 | `player statistics. All stats flow through nba_master_hub_2026.` |
| `backend/services/ssot_data_layer.py` | 76 | nba_master_hub_2026 | `self.master_hub = db.nba_master_hub_2026` |
| `backend/services/ssot_data_layer.py` | 77 | dg_cached_board | `self.active_lines = db.dg_cached_board` |
| `backend/services/stats_enrichment_service.py` | 51 | nba_master_hub_2026 | `1. Check nba_master_hub_2026 (BDL SSOT) first` |
| `backend/services/stats_enrichment_service.py` | 58 | nba_master_hub_2026 | `self.master_hub = db.nba_master_hub_2026` |
| `backend/services/stats_enrichment_service.py` | 71 | nba_master_hub_2026 | `Enrich props with hit rates from BDL game logs in nba_master_hub_2026.` |
| `backend/services/stats_enrichment_service.py` | 74 | nba_master_hub_2026 | `logger.info(f"[STATS ENRICHMENT] Loading stats for {len(player_names)} players from BDL (nba_master_hub_2026)...")` |
| `backend/services/sync_orchestration_service.py` | 34 | dg_cached_board | `self.dg_cached_board = db.dg_cached_board` |
| `backend/services/sync_orchestration_service.py` | 38 | dg_sync_log | `self.sync_log = db.dg_sync_log` |
| `backend/services/sync_orchestration_service.py` | 224 | dg_cached_board | `existing_board = await self.dg_cached_board.find_one({"type": "main_board"})` |
| `backend/services/sync_orchestration_service.py` | 323 | dg_cached_board | `await self.dg_cached_board.update_one(` |
| `backend/services/sync_orchestration_service.py` | 393 | nba_master_hub_2026 | `"photo_source": "nba_master_hub_2026",` |
| `backend/services/sync_service.py` | 33 | dg_cached_board | `self.cached_board = db.dg_cached_board` |
| `backend/services/sync_service.py` | 39 | dg_sync_log | `self.sync_log = db.dg_sync_log` |
| `backend/services/team_stats_service.py` | 200 | nba_master_hub_2026 | `hub = self.db['nba_master_hub_2026']` |
| `backend/services/team_stats_service.py` | 312 | dg_cached_board | `cached_board = self.db['dg_cached_board']` |
| `backend/services/team_stats_service.py` | 362 | nba_master_hub_2026 | `hub = self.db['nba_master_hub_2026']` |
| `backend/services/tier_builder_service.py` | 76 | dg_sync_log | `self.sync_log = db.dg_sync_log` |
| `backend/services/tier_builder_service.py` | 107 | nba_master_hub_2026 | `cursor = self.db.nba_master_hub_2026.find(` |
| `backend/services/universal_odds_sync.py` | 18 | dg_live_props | `- NBA: dg_live_props (legacy name)` |
| `backend/services/usage_spike_detector.py` | 84 | nba_master_hub_2026 | `players = await self.db.nba_master_hub_2026.find(` |
| `backend/services/vegas_killer_model.py` | 1189 | nba_master_hub_2026 | `hub = self.db['nba_master_hub_2026']` |
| `backend/services/vegas_killer_model.py` | 1697 | nba_master_hub_2026 | `hub = self.db['nba_master_hub_2026']` |
| `backend/services/vegas_pro_model.py` | 12 | nba_master_hub_2026 | `1. EXTRACT: Pull game logs from nba_master_hub_2026` |
| `backend/services/vegas_pro_model.py` | 157 | nba_master_hub_2026 | `hub = self.db['nba_master_hub_2026']` |
| `backend/services/vegas_pro_model.py` | 562 | nba_master_hub_2026 | `hub = self.db['nba_master_hub_2026']` |
| `backend/services/vegas_regression_model.py` | 26 | nba_master_hub_2026 | `- nba_master_hub_2026.bdl_game_logs: Historical game-by-game stats` |
| `backend/services/vegas_regression_model.py` | 27 | defensive_momentum_cache | `- defensive_momentum_cache: Opponent defensive rankings` |
| `backend/services/vegas_regression_model.py` | 28 | dg_cached_board | `- dg_cached_board: Current props with lines` |
| `backend/services/vegas_regression_model.py` | 597 | nba_master_hub_2026 | `hub = db['nba_master_hub_2026']` |
| `backend/services/vision_ai_service.py` | 66 | dg_cached_board | `self.cached_board = db.dg_cached_board` |
| `backend/services/vision_intel_enrichment_service.py` | 26 | dg_cached_board | `cached_board = db.dg_cached_board` |
| `backend/services/vision_intel_enrichment_service.py` | 160 | dg_cached_board | `player = await db.dg_cached_board.find_one(` |
| `backend/services/vision_intel_enrichment_service.py` | 207 | dg_cached_board | `update_result = await db.dg_cached_board.update_one(` |
| `backend/services/vk_model_enforcement.py` | 116 | nba_master_hub_2026 | `collection = _db_reference.nba_master_hub_2026` |
| `backend/services/vk_model_enforcement.py` | 285 | nba_master_hub_2026 | `1. Lookup actual L10 Standard Deviation from nba_master_hub_2026 (or MLB)` |
| `backend/services/watchers.py` | 204 | dg_live_props | `for sport, col_name in [("nba", "dg_live_props"), ("mlb", "mlb_live_props")]:` |
| `backend/services/watchers.py` | 309 | dg_cached_board | `col = "dg_cached_board" if sport == "nba" else "mlb_cached_board"` |
| `backend/tests/phase3_injury_rescore_verify.py` | 7 | dg_cached_board | `- dg_cached_board for every HOU player + 1 control (non-HOU) player.` |
| `backend/tests/phase3_injury_rescore_verify.py` | 48 | dg_cached_board | `async for d in db.dg_cached_board.find(` |
| `backend/tests/phase3_injury_rescore_verify.py` | 92 | dg_cached_board | `[d["player_name"] async for d in db.dg_cached_board.find(` |
| `backend/tests/phase3_injury_rescore_verify.py` | 96 | dg_cached_board | `ctrl_doc = await db.dg_cached_board.find_one(` |
| `backend/tests/phase3_injury_rescore_verify.py` | 179 | dg_cached_board | `print("== dg_cached_board: HOU (impacted) ==")` |
| `backend/tests/phase3_injury_rescore_verify.py` | 201 | dg_cached_board | `print(f"== dg_cached_board: {ctrl_team} (control, MUST be untouched) ==")` |
| `backend/utils/player_lookup.py` | 40 | nba_master_hub_2026 | `players = await db.nba_master_hub_2026.find(` |

"""
Odds Sync Service
=================
Extracted from services.engines.demon_goblin_engine.py for modularity.

Handles the main sync orchestration:
- Fetching events and odds from The Odds API
- Normalizing data (team names, player names)
- Deduplication and storage to MongoDB
- Building the cached board
"""
from typing import Dict, List, Any, Callable
from datetime import datetime, timezone
import asyncio
import logging

from motor.motor_asyncio import AsyncIOMotorDatabase

from services.config.collection_names import COLL

logger = logging.getLogger(__name__)


class OddsSyncService:
    """
    Service for orchestrating the main odds sync process.
    
    This is THE ONLY API CALL flow - fetches from Odds API
    and stores normalized, deduplicated data to MongoDB.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        # Wave 1 shadow-writes: route through COLL.handle so mutations fan
        # out to the registered shadow (`nba_live_props`) during migration.
        self.live_props = COLL.handle(db, "live_props", "nba")
        self.master_roster = db[COLL("master_roster", "nba")]
        
        # Cache for master roster
        self._master_roster_cache: Dict[str, str] = {}
    
    async def sync_odds_to_mongo(
        self,
        # Callbacks to engine methods
        get_current_date: Callable,
        load_master_roster_cache: Callable,
        fetch_todays_events: Callable,
        fetch_prizepicks_odds: Callable,
        extract_prizepicks_props: Callable,
        normalize_team_name: Callable,
        sanitize_player_name: Callable,
        extract_stat_type: Callable,
        enrich_props_with_stats: Callable,
        build_cached_board: Callable,
        sync_master_roster: Callable,
        fetch_sharp_book_odds: Callable = None,  # Phase 2: Sharp books (DraftKings/FanDuel)
        build_ferrari_tiers: Callable = None,  # Phase 3: Ferrari tier filtering
        store_static_shell: Callable = None  # Static shell cache update
    ) -> Dict[str, Any]:
        """
        THE ONLY API CALL - Single batch fetch to MongoDB.
        
        DATABASE NORMALIZATION (v2.0):
        1. Team names converted to 3-letter abbreviations
        2. Player names sanitized and normalized
        3. Composite key for deduplication
        4. UPSERT mode
        """
        sync_start = datetime.now(timezone.utc)
        current_date = get_current_date()
        
        logger.info("=" * 70)
        logger.info("[SYNC_ODDS_TO_MONGO] Starting normalized batch sync v2.0...")
        logger.info(f"[SYNC_ODDS_TO_MONGO] Date: {current_date}")
        logger.info("=" * 70)
        
        results = {
            "success": True,
            "synced_at": sync_start.isoformat(),
            "events_count": 0,
            "total_props": 0,
            "unique_players": 0,
            "standard_count": 0,
            "demons_count": 0,
            "goblins_count": 0,
            "api_calls_made": 0,
            "duplicates_prevented": 0,
            "names_normalized": 0,
            "teams_normalized": 0,
            "errors": []
        }
        
        try:
            # Step 0: Load Master Roster cache for team lookups
            await load_master_roster_cache()
            
            # Check if master roster exists
            roster_count = await self.master_roster.count_documents({})
            if roster_count == 0:
                logger.warning("[SYNC_ODDS_TO_MONGO] Master roster is empty! Running initial sync...")
                await sync_master_roster()
            else:
                logger.info(f"[SYNC_ODDS_TO_MONGO] Master roster loaded: {roster_count} players")
            
            # Step 1: Fetch events (1 API call)
            events = await fetch_todays_events()
            results["events_count"] = len(events)
            results["api_calls_made"] += 1
            
            if not events:
                logger.warning("[SYNC_ODDS_TO_MONGO] No events found")
                results["success"] = False
                results["errors"].append("No NBA events found")
                return results
            
            # Step 2: Fetch odds for ALL events in PARALLEL (batched)
            all_props = []
            seen_players_raw = set()
            seen_players_normalized = set()
            
            # Filter valid events
            valid_events = [(e.get("id"), e) for e in events if e.get("id")]
            
            if valid_events:
                logger.info(f"[SYNC_ODDS_TO_MONGO] Fetching odds for {len(valid_events)} events in PARALLEL...")
                
                # Create tasks for parallel execution
                async def fetch_event_odds(event_id: str, event_info: dict):
                    """Fetch odds for a single event."""
                    try:
                        return await fetch_prizepicks_odds(event_id, event_info)
                    except Exception as e:
                        logger.error(f"[SYNC_ODDS_TO_MONGO] Error fetching {event_id}: {e}")
                        return None
                
                # Execute all fetches in parallel
                tasks = [fetch_event_odds(eid, einfo) for eid, einfo in valid_events]
                odds_results = await asyncio.gather(*tasks, return_exceptions=True)
                
                results["api_calls_made"] += len(valid_events)
                
                # Process results
                for odds_data in odds_results:
                    if isinstance(odds_data, Exception):
                        continue
                    if odds_data:
                        props = extract_prizepicks_props(odds_data)
                        all_props.extend(props)
                        
                        for prop in props:
                            seen_players_raw.add(prop.get("player_name"))
                
                # Phase 2: Fetch Sharp Book odds (DraftKings, FanDuel, BetOnline) in parallel
                # - DraftKings: Primary reference (72% coverage)
                # - FanDuel: Secondary reference (45% coverage)
                # - BetOnline: Tertiary reference (38% coverage, best for alternates)
                # - Combined: 90% coverage of all PrizePicks lines
                sharp_prices = {}  # {(player, market, line, direction): {draftkings, fanduel, betonline}}
                
                if fetch_sharp_book_odds:
                    logger.info(f"[SYNC_ODDS_TO_MONGO] Fetching Sharp Book odds (DraftKings/FanDuel/BetOnline)...")
                    
                    async def fetch_sharp_odds(event_id: str, event_info: dict):
                        try:
                            return await fetch_sharp_book_odds(event_id, event_info)
                        except Exception as e:
                            logger.debug(f"[SHARP_BOOKS] Error fetching {event_id}: {e}")
                            return None
                    
                    sharp_tasks = [fetch_sharp_odds(eid, einfo) for eid, einfo in valid_events]
                    sharp_results = await asyncio.gather(*sharp_tasks, return_exceptions=True)
                    
                    sharp_count = sum(1 for r in sharp_results if r and not isinstance(r, Exception) and r.get("bookmakers"))
                    results["api_calls_made"] += len(valid_events)
                    results["sharp_books_fetched"] = sharp_count
                    logger.info(f"[SYNC_ODDS_TO_MONGO] Sharp Books: {sharp_count}/{len(valid_events)} events with data")
                    
                    # Build sharp price lookup table
                    for sharp_data in sharp_results:
                        if isinstance(sharp_data, Exception) or not sharp_data:
                            continue
                        
                        for bm in sharp_data.get("bookmakers", []):
                            bm_key = bm.get("key", "")
                            if bm_key not in ["draftkings", "fanduel", "betonlineag", "betmgm"]:
                                continue
                            
                            for market in bm.get("markets", []):
                                market_key = market.get("key", "")
                                # Normalize to the alternate-market namespace so a
                                # STANDARD-market outcome at the same line is reachable
                                # via the alt lookup used during merge.  PrizePicks props
                                # are always keyed on the alternate market, but several
                                # books (notably BetOnline) expose many prop lines ONLY
                                # on the standard market.  Without this normalization we
                                # silently dropped those prices. (Fix 2026-04-21.)
                                is_std = not market_key.endswith("_alternate")
                                alt_key = (
                                    f"{market_key}_alternate" if is_std else market_key
                                )

                                for outcome in market.get("outcomes", []):
                                    player_name = outcome.get("description", "")
                                    line = outcome.get("point", 0)
                                    direction = (outcome.get("name", "") or "over").lower()
                                    price = outcome.get("price")

                                    # Store under BOTH the native key and the alt key so
                                    # either lookup resolves.  Alt-native data must
                                    # ALWAYS win over standard-duplicated data when both
                                    # books offer the same line.
                                    keys = [(player_name, market_key, line, direction)]
                                    if is_std and alt_key != market_key:
                                        keys.append((player_name, alt_key, line, direction))

                                    for lookup_key in keys:
                                        if lookup_key not in sharp_prices:
                                            sharp_prices[lookup_key] = {
                                                "draftkings_price": None,
                                                "fanduel_price": None,
                                                "betonline_price": None,
                                                "betmgm_price": None,
                                            }
                                        # Only overwrite if the existing slot is empty;
                                        # alt-native entries take precedence when both
                                        # exist for the same (player, market, line).
                                        cur = sharp_prices[lookup_key]
                                        if bm_key == "draftkings" and (cur["draftkings_price"] is None or not is_std):
                                            cur["draftkings_price"] = price
                                        elif bm_key == "fanduel" and (cur["fanduel_price"] is None or not is_std):
                                            cur["fanduel_price"] = price
                                        elif bm_key == "betonlineag" and (cur["betonline_price"] is None or not is_std):
                                            cur["betonline_price"] = price
                                        elif bm_key == "betmgm" and (cur["betmgm_price"] is None or not is_std):
                                            cur["betmgm_price"] = price
                    
                    logger.info(f"[SYNC_ODDS_TO_MONGO] Built sharp price lookup: {len(sharp_prices)} unique props")
                
                # Merge sharp prices into PrizePicks props with nested sharp_market object
                for prop in all_props:
                    player_name = prop.get("player_name", "")
                    market_key = prop.get("market", "")
                    line = prop.get("line", 0)
                    direction = (prop.get("direction", "") or "over").lower()
                    is_alternate = "_alternate" in market_key
                    
                    lookup_key = (player_name, market_key, line, direction)
                    sharp_data = sharp_prices.get(lookup_key, {}) or {}

                    # Per-book fallback: if the alt-market lookup is missing
                    # a book, fall back to the standard-market lookup at the
                    # same line.  Matches the "pulling standard lines as well"
                    # requirement (2026-04-21).
                    if is_alternate:
                        std_key = (
                            player_name,
                            market_key.replace("_alternate", ""),
                            line,
                            direction,
                        )
                        std_data = sharp_prices.get(std_key, {}) or {}
                        merged = {
                            "draftkings_price": sharp_data.get("draftkings_price")
                                or std_data.get("draftkings_price"),
                            "fanduel_price": sharp_data.get("fanduel_price")
                                or std_data.get("fanduel_price"),
                            "betonline_price": sharp_data.get("betonline_price")
                                or std_data.get("betonline_price"),
                            "betmgm_price": sharp_data.get("betmgm_price")
                                or std_data.get("betmgm_price"),
                        }
                        sharp_data = merged

                    draftkings_price = sharp_data.get("draftkings_price")
                    fanduel_price = sharp_data.get("fanduel_price")
                    betonline_price = sharp_data.get("betonline_price")
                    betmgm_price = sharp_data.get("betmgm_price")
                    
                    # Calculate sort_price: Use first available from DK > FD > BOL > MGM
                    # This provides variable odds for sorting (vs PrizePicks flat -137)
                    sort_price = None
                    sort_source = None
                    if draftkings_price is not None:
                        sort_price = draftkings_price
                        sort_source = "draftkings"
                    elif fanduel_price is not None:
                        sort_price = fanduel_price
                        sort_source = "fanduel"
                    elif betonline_price is not None:
                        sort_price = betonline_price
                        sort_source = "betonline"
                    elif betmgm_price is not None:
                        sort_price = betmgm_price
                        sort_source = "betmgm"
                    
                    # Build nested sharp_market object
                    prop["sharp_market"] = {
                        "draftkings_price": draftkings_price,
                        "fanduel_price": fanduel_price,
                        "betonline_price": betonline_price,
                        "betmgm_price": betmgm_price,
                        "sort_price": sort_price,
                        "sort_source": sort_source,
                        "is_alternate": is_alternate
                    }
                    
                    # Also keep flat fields for backwards compatibility
                    prop["draftkings_price"] = draftkings_price
                    prop["fanduel_price"] = fanduel_price
                    prop["betonline_price"] = betonline_price
                    prop["betmgm_price"] = betmgm_price
                    prop["sort_price"] = sort_price
                    prop["sort_source"] = sort_source
            
            # Step 3: Normalize all props
            logger.info(f"[NORMALIZATION] Processing {len(all_props)} props...")
            normalized_props = []
            
            for prop in all_props:
                # Normalize team names
                original_home = prop.get("home_team", "")
                original_away = prop.get("away_team", "")
                
                prop["home_team"] = normalize_team_name(original_home)
                prop["away_team"] = normalize_team_name(original_away)
                prop["home_team_full"] = original_home
                prop["away_team_full"] = original_away
                
                if prop["home_team"] != original_home:
                    results["teams_normalized"] += 1
                
                # Normalize player names
                original_name = prop.get("player_name", "")
                normalized_name = sanitize_player_name(original_name)
                
                if normalized_name != original_name:
                    results["names_normalized"] += 1
                    logger.debug(f"[NORMALIZE] '{original_name}' → '{normalized_name}'")
                
                prop["player_name"] = normalized_name
                prop["player_name_raw"] = original_name
                
                seen_players_normalized.add(normalized_name)
                
                # Extract stat type for composite key
                market = prop.get("market", "")
                stat_type = extract_stat_type(market)
                
                # Create composite key
                composite_key = f"{normalized_name}|{stat_type}|{prop.get('line', 0)}|{prop.get('direction', '')}|{current_date}"
                prop["_composite_key"] = composite_key
                prop["stat_type_extracted"] = stat_type
                prop["game_date"] = current_date
                prop["synced_at"] = sync_start.isoformat()
                
                normalized_props.append(prop)
            
            results["unique_players"] = len(seen_players_normalized)
            logger.info(f"[NORMALIZATION] Normalized {results['names_normalized']} names, {results['teams_normalized']} teams")
            logger.info(f"[NORMALIZATION] Raw players: {len(seen_players_raw)} → Normalized: {len(seen_players_normalized)}")
            
            # Step 4: Enrich props with hit rates
            logger.info(f"[SYNC_ODDS_TO_MONGO] Enriching {len(seen_players_normalized)} players with BallDontLie stats...")
            enriched_props = await enrich_props_with_stats(normalized_props, list(seen_players_normalized))
            results["stats_enriched"] = len([p for p in enriched_props if p.get("hit_rates")])
            
            # Step 5: Wipe and insert deduplicated data
            # CIRCUIT BREAKER: Don't wipe if we have very few props
            if enriched_props:
                # Check existing count first
                existing_count = await self.live_props.count_documents({})
                
                if len(enriched_props) < 30 and existing_count > len(enriched_props) * 2:
                    logger.warning(f"[CIRCUIT BREAKER] Only {len(enriched_props)} props from API, existing has {existing_count}. Preserving existing data!")
                    results["circuit_breaker"] = {
                        "triggered": True,
                        "reason": f"API returned only {len(enriched_props)} props, existing has {existing_count}",
                        "action": "Preserved existing live_props collection"
                    }
                    results["success"] = True
                    results["total_props"] = existing_count
                    results["preserved"] = True
                    return results
                
                # Phase 6 Step 5 — snapshot pre-wipe canonical_keys so
                # we can emit a `new_props` delta event after the
                # reinsert. Captured via the NBA board adapter to stay
                # consistent with the universal engine's key format.
                pre_keys: set = set()
                try:
                    from services.board.delta_publisher import capture_live_props_keys
                    pre_keys = await capture_live_props_keys(self.db, "nba")
                except Exception as _e:
                    logger.warning(f"[DELTA_PUB] NBA pre-snapshot skipped: {_e}")

                deleted = await self.live_props.delete_many({})
                logger.info(f"[CLEANUP] Wiped {deleted.deleted_count} old records")
                
                # Deduplicate using composite key
                deduplicated = {}
                for prop in enriched_props:
                    key = prop.get("_composite_key", "")
                    if key:
                        if key in deduplicated:
                            results["duplicates_prevented"] += 1
                        deduplicated[key] = prop
                
                # Insert deduplicated props
                props_list = list(deduplicated.values())
                now_utc = datetime.now(timezone.utc)
                for prop in props_list:
                    prop.pop("_id", None)
                    # Delta engine (D5, 2026-04-21): datetime stamp so
                    # `{updated_at: {$gt: watermark}}` range queries work
                    # for NBA the same way they do for MLB (parity with
                    # universal_odds_sync which stamps this field too).
                    prop["updated_at"] = now_utc
                
                if props_list:
                    try:
                        await self.live_props.create_index("_composite_key", unique=True, sparse=True)
                    except Exception:
                        pass
                    
                    await self.live_props.insert_many(props_list)

                # Phase 6 Step 5 — post-insert snapshot + delta publish
                # (non-blocking; fire-and-forget so odds_sync latency
                # is unchanged). Guardrailed internally to skip if the
                # delta is absurdly large (full wipe-reinsert).
                try:
                    from services.board.delta_publisher import (
                        capture_live_props_keys, publish_new_props_delta,
                    )
                    post_keys = await capture_live_props_keys(self.db, "nba")
                    await publish_new_props_delta(
                        sport="nba",
                        pre_keys=pre_keys,
                        post_keys=post_keys,
                        source="odds_sync_service",
                    )
                except Exception as _e:
                    logger.warning(f"[DELTA_PUB] NBA delta emit skipped: {_e}")
                
                results["total_props"] = len(props_list)
                results["standard_count"] = sum(1 for p in props_list if p.get("prop_type") == "standard")
                results["demons_count"] = sum(1 for p in props_list if p.get("is_demon"))
                results["goblins_count"] = sum(1 for p in props_list if p.get("is_goblin"))
                
                logger.info(f"[SYNC_ODDS_TO_MONGO] Stored {len(props_list)} clean, deduplicated props")
                logger.info(f"[SYNC_ODDS_TO_MONGO] Duplicates prevented: {results['duplicates_prevented']}")
                
                # =================================================================
                # STEP 5.5: ORACLE APEX SCAN (BEFORE cached board)
                # =================================================================
                # Scan ALL props with Vegas Killer model to get predictions,
                # hit rates, CV, and Oracle Apex qualification BEFORE filtering
                # This ensures tier distribution has full analysis data
                # =================================================================
                logger.info("[ORACLE_APEX] Running VK scan on ALL props before cached board...")
                try:
                    from services.oracle_apex_service import get_oracle_apex_service
                    from services.vegas_killer_model import VegasKillerModel
                    from pymongo import MongoClient
                    import os
                    
                    # VK model needs sync pymongo, not async Motor
                    sync_client = MongoClient(os.environ.get('MONGO_URL'))
                    sync_db = sync_client[os.environ.get('DB_NAME', 'pick_vision')]
                    
                    # Load VK model with sync db
                    vk_model = VegasKillerModel(sync_db)
                    vk_model.load_models()
                    
                    # But Oracle Apex service needs async Motor db for queries
                    oracle_service = get_oracle_apex_service(self.db, vk_model)
                    apex_scan_result = await oracle_service.scan_all_props_for_distribution()
                    
                    if apex_scan_result.get('success'):
                        analyzed_props = apex_scan_result.get('all_props', [])
                        apex_stats = apex_scan_result.get('stats', {})
                        
                        # Store to oracle_apex_analyzed collection for tier building
                        oracle_analyzed_coll = self.db.oracle_apex_analyzed
                        await oracle_analyzed_coll.delete_many({})
                        if analyzed_props:
                            await oracle_analyzed_coll.insert_many(analyzed_props)
                        
                        results["oracle_apex_scan"] = {
                            "total": apex_stats.get('total', 0),
                            "safe_haven_qualified": apex_stats.get('safe_haven_qualified', 0),
                            "has_vk_data": apex_stats.get('has_vk_data', 0),
                        }
                        logger.info(f"[ORACLE_APEX] Scan complete: {apex_stats}")
                    else:
                        logger.warning(f"[ORACLE_APEX] Scan failed: {apex_scan_result.get('error')}")
                except Exception as apex_err:
                    logger.error(f"[ORACLE_APEX] Error during scan: {apex_err}")
            
                # Step 6: Build cached board
                await build_cached_board(props_list, sync_start)
                
                # Step 6b: Update static shell cache for hydrated board endpoint
                # NOTE: Static shell is built AFTER Ferrari tiers so it contains only
                # the best picks with full intel_suite enrichment
                
                # Step 7: Build Ferrari tiers (Bovada separation filtering)
                if build_ferrari_tiers:
                    try:
                        ferrari_results = await build_ferrari_tiers(sync_start)
                        results["ferrari_tiers"] = {
                            "safe_haven": ferrari_results.get("safe_haven", {}).get("count", 0),
                            "front_lines": ferrari_results.get("front_lines", {}).get("count", 0),
                            "war_zone": ferrari_results.get("war_zone", {}).get("count", 0),
                            "discarded": ferrari_results.get("props_discarded", 0)
                        }
                        logger.info(
                            f"[FERRARI] Built: SH={results['ferrari_tiers']['safe_haven']}, "
                            f"FL={results['ferrari_tiers']['front_lines']}, "
                            f"WZ={results['ferrari_tiers']['war_zone']}, "
                            f"Discarded={results['ferrari_tiers']['discarded']}"
                        )
                        
                        # Step 7b: NOW build static shell from Ferrari-approved picks
                        if store_static_shell:
                            try:
                                # Gather all Ferrari-approved picks
                                ferrari_players = []
                                for coll_name in ['ferrari_safe_haven', 'ferrari_front_lines', 'ferrari_war_zone']:
                                    docs = await self.db[coll_name].find({}, {"_id": 0}).to_list(20)
                                    ferrari_players.extend(docs)
                                
                                # Group by player for static shell
                                players_dict = {}
                                for pick in ferrari_players:
                                    pname = pick.get("player_name")
                                    if not pname:
                                        continue
                                    if pname not in players_dict:
                                        players_dict[pname] = {
                                            "player_name": pname,
                                            "team": pick.get("team"),
                                            "position": pick.get("position"),
                                            "photo_url": pick.get("photo_url"),
                                            "headshot_url": pick.get("headshot_url"),
                                            "props": []
                                        }
                                    # Add the pick as a prop
                                    players_dict[pname]["props"].append(pick)
                                
                                players_list = list(players_dict.values())
                                await store_static_shell(players_list, [])
                                logger.info(f"[SYNC_ODDS_TO_MONGO] Static shell updated with {len(players_list)} Ferrari-approved players, {len(ferrari_players)} picks")
                            except Exception as shell_err:
                                logger.error(f"[SYNC_ODDS_TO_MONGO] Static shell update failed: {shell_err}")
                                
                    except Exception as fe:
                        logger.error(f"[FERRARI] Build failed: {fe}")
            
        except Exception as e:
            logger.error(f"[SYNC_ODDS_TO_MONGO] Error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            results["success"] = False
            results["errors"].append(str(e))
        
        duration = (datetime.now(timezone.utc) - sync_start).total_seconds()
        results["duration_seconds"] = duration
        
        logger.info("=" * 70)
        logger.info(f"[SYNC_ODDS_TO_MONGO] COMPLETE (Normalized v2.0)")
        logger.info(f"  Duration: {duration:.1f}s")
        logger.info(f"  API Calls Made: {results['api_calls_made']}")
        logger.info(f"  Props Stored: {results['total_props']}")
        logger.info(f"  Props Enriched: {results.get('stats_enriched', 0)}")
        logger.info(f"  Players: {results['unique_players']}")
        logger.info(f"  Names Normalized: {results['names_normalized']}")
        logger.info(f"  Teams Normalized: {results['teams_normalized']}")
        logger.info(f"  Duplicates Prevented: {results['duplicates_prevented']}")
        logger.info(f"  Standard: {results['standard_count']} | Demons: {results['demons_count']} | Goblins: {results['goblins_count']}")
        logger.info("=" * 70)
        
        return results

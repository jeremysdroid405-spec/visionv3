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

logger = logging.getLogger(__name__)


class OddsSyncService:
    """
    Service for orchestrating the main odds sync process.
    
    This is THE ONLY API CALL flow - fetches from Odds API
    and stores normalized, deduplicated data to MongoDB.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.live_props = db.dg_live_props
        self.master_roster = db.dg_master_roster
        
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
                
                # Phase 2: Fetch Sharp Book odds (Bovada, DraftKings, FanDuel) in parallel
                # - Bovada: Primary sharp reference for ALTERNATE lines
                # - DraftKings/FanDuel: Sharp reference for STANDARD lines
                sharp_prices = {}  # {(player, market, line, direction): {bovada, draftkings, fanduel}}
                
                if fetch_sharp_book_odds:
                    logger.info(f"[SYNC_ODDS_TO_MONGO] Fetching Sharp Book odds (Bovada/DraftKings/FanDuel)...")
                    
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
                            if bm_key not in ["bovada", "draftkings", "fanduel"]:
                                continue
                            
                            for market in bm.get("markets", []):
                                market_key = market.get("key", "")
                                for outcome in market.get("outcomes", []):
                                    player_name = outcome.get("description", "")
                                    line = outcome.get("point", 0)
                                    direction = (outcome.get("name", "") or "over").lower()
                                    price = outcome.get("price")
                                    
                                    lookup_key = (player_name, market_key, line, direction)
                                    if lookup_key not in sharp_prices:
                                        sharp_prices[lookup_key] = {
                                            "bovada_price": None,
                                            "draftkings_price": None,
                                            "fanduel_price": None
                                        }
                                    
                                    if bm_key == "bovada":
                                        sharp_prices[lookup_key]["bovada_price"] = price
                                    elif bm_key == "draftkings":
                                        sharp_prices[lookup_key]["draftkings_price"] = price
                                    elif bm_key == "fanduel":
                                        sharp_prices[lookup_key]["fanduel_price"] = price
                    
                    logger.info(f"[SYNC_ODDS_TO_MONGO] Built sharp price lookup: {len(sharp_prices)} unique props")
                
                # Merge sharp prices into PrizePicks props with nested sharp_market object
                for prop in all_props:
                    player_name = prop.get("player_name", "")
                    market_key = prop.get("market", "")
                    line = prop.get("line", 0)
                    direction = (prop.get("direction", "") or "over").lower()
                    is_alternate = "_alternate" in market_key
                    
                    lookup_key = (player_name, market_key, line, direction)
                    sharp_data = sharp_prices.get(lookup_key, {})
                    
                    bovada_price = sharp_data.get("bovada_price")
                    draftkings_price = sharp_data.get("draftkings_price")
                    fanduel_price = sharp_data.get("fanduel_price")
                    
                    # Calculate DK/FD average for standard lines
                    dk_fd_avg = None
                    if draftkings_price is not None and fanduel_price is not None:
                        dk_fd_avg = round((draftkings_price + fanduel_price) / 2)
                    elif draftkings_price is not None:
                        dk_fd_avg = draftkings_price
                    elif fanduel_price is not None:
                        dk_fd_avg = fanduel_price
                    
                    # Determine sharp_price based on line type
                    # ALTERNATE lines: Bovada is primary
                    # STANDARD lines: DraftKings/FanDuel average
                    if is_alternate:
                        sharp_price = bovada_price if bovada_price is not None else dk_fd_avg
                        sharp_source = "bovada" if bovada_price is not None else ("dk_fd_avg" if dk_fd_avg is not None else None)
                    else:
                        sharp_price = dk_fd_avg if dk_fd_avg is not None else bovada_price
                        sharp_source = "dk_fd_avg" if dk_fd_avg is not None else ("bovada" if bovada_price is not None else None)
                    
                    # Build nested sharp_market object
                    prop["sharp_market"] = {
                        "bovada_price": bovada_price,
                        "draftkings_price": draftkings_price,
                        "fanduel_price": fanduel_price,
                        "dk_fd_average": dk_fd_avg,
                        "sharp_price": sharp_price,
                        "sharp_source": sharp_source,
                        "is_alternate": is_alternate
                    }
                    
                    # Also keep flat fields for backwards compatibility
                    prop["bovada_price"] = bovada_price
                    prop["draftkings_price"] = draftkings_price
                    prop["fanduel_price"] = fanduel_price
                    prop["sharp_price"] = sharp_price
                    prop["sharp_source"] = sharp_source
            
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
            if enriched_props:
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
                for prop in props_list:
                    prop.pop("_id", None)
                
                if props_list:
                    try:
                        await self.live_props.create_index("_composite_key", unique=True, sparse=True)
                    except Exception:
                        pass
                    
                    await self.live_props.insert_many(props_list)
                
                results["total_props"] = len(props_list)
                results["standard_count"] = sum(1 for p in props_list if p.get("prop_type") == "standard")
                results["demons_count"] = sum(1 for p in props_list if p.get("is_demon"))
                results["goblins_count"] = sum(1 for p in props_list if p.get("is_goblin"))
                
                logger.info(f"[SYNC_ODDS_TO_MONGO] Stored {len(props_list)} clean, deduplicated props")
                logger.info(f"[SYNC_ODDS_TO_MONGO] Duplicates prevented: {results['duplicates_prevented']}")
            
                # Step 6: Build cached board
                await build_cached_board(props_list, sync_start)
                
                # Step 6b: Update static shell cache for hydrated board endpoint
                if store_static_shell:
                    try:
                        # Get players with full data from cached board
                        cached_board_docs = await self.db.dg_cached_board.find(
                            {},
                            {"_id": 0, "player_name": 1, "team": 1, "position": 1, 
                             "photo_url": 1, "headshot_url": 1, "nba_id": 1, "bdl_id": 1,
                             "season_avg": 1, "baseline_stats": 1, "props": 1}
                        ).to_list(500)
                        
                        players_list = cached_board_docs if cached_board_docs else []
                        trending = []  # Can be populated from most popular bets
                        await store_static_shell(players_list, trending)
                        logger.info(f"[SYNC_ODDS_TO_MONGO] Static shell updated with {len(players_list)} players")
                    except Exception as shell_err:
                        logger.error(f"[SYNC_ODDS_TO_MONGO] Static shell update failed: {shell_err}")
                
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

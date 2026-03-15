"""
Sync Orchestration Service
==========================
Extracted from demon_goblin_engine.py for modularity.

Handles the main sync orchestration workflows:
- Full sync (run_full_sync) - 3-Pillar complete sync
- Delta sync (run_delta_sync) - Odds-only updates
"""
from typing import Dict, List, Any, Set, Callable
from datetime import datetime, timezone
import asyncio
import logging

from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)


class SyncOrchestrationService:
    """
    Service for orchestrating sync workflows.
    
    Handles:
    - Full 3-Pillar sync with verification
    - Delta sync for odds-only updates
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.dg_cached_board = db.dg_cached_board
        self.verification_failures = db.dg_verification_failures
    
    async def run_full_sync(
        self,
        # Engine state
        current_date: str,
        prizepicks_region: str,
        prizepicks_bookmaker: str,
        # Callback methods
        fetch_todays_events: Callable,
        fetch_prizepicks_odds: Callable,
        extract_prizepicks_props: Callable,
        fetch_injuries: Callable,
        fetch_news: Callable,
        process_player_prop: Callable,
        ensure_odds_mapper_loaded: Callable,
        get_odds_mapper: Callable,
        build_cached_board: Callable,
        build_war_zone: Callable,
        build_front_lines: Callable,
        build_goblin_vault: Callable,
        build_parlay_builder: Callable,
        build_goblin_recon: Callable
    ) -> Dict[str, Any]:
        """
        Execute the full three-pillar sync with PrizePicks data.
        
        Pillar 1: Fetch events and PrizePicks odds
        Pillar 2: Process stats from BallDontLie
        Pillar 3: Fetch injuries from Tank01
        """
        sync_start = datetime.now(timezone.utc)
        
        logger.info("=" * 70)
        logger.info(f"DEMON & GOBLIN ENGINE v3.0 - PRIZEPICKS SYNC")
        logger.info(f"Date: {current_date}")
        logger.info(f"Region: {prizepicks_region} | Bookmaker: {prizepicks_bookmaker}")
        logger.info("=" * 70)
        
        results = {
            "success": True,
            "sync_date": current_date,
            "sync_time": sync_start.isoformat(),
            "events_count": 0,
            "total_props": 0,
            "unique_players": 0,
            "standard_count": 0,
            "demons_count": 0,
            "goblins_count": 0,
            "stats_fetched": 0,
            "injuries_found": 0,
            "goblin_warnings": 0,
            "verification_stats": {
                "verified_count": 0,
                "failed_count": 0,
                "naji_safeguard_failures": 0,
                "hallucinations_detected": 0,
                "discrepancies_found": 0
            },
            "errors": [],
            "duration": 0
        }
        
        try:
            # Clear previous verification failures
            await self.verification_failures.delete_many({"sync_date": current_date})
            logger.info("[TRUTH ENGINE] Cleared previous verification failures")
            
            # ===== PILLAR 1: FETCH EVENTS AND PRIZEPICKS ODDS =====
            logger.info("\n[PILLAR 1] Fetching NBA events and PrizePicks lines...")
            
            events = await fetch_todays_events()
            results["events_count"] = len(events)
            
            if not events:
                results["success"] = False
                results["errors"].append("No NBA events found")
                return results
            
            all_props = []
            all_players: Set[str] = set()
            
            for event in events:
                event_id = event.get("id")
                if event_id:
                    odds_data = await fetch_prizepicks_odds(event_id, event)
                    if odds_data:
                        props = extract_prizepicks_props(odds_data)
                        all_props.extend(props)
                        for p in props:
                            all_players.add(p.get("player_name", ""))
                    await asyncio.sleep(0.3)
            
            results["total_props"] = len(all_props)
            results["unique_players"] = len(all_players)
            results["standard_count"] = sum(1 for p in all_props if p.get("prop_type") == "standard")
            results["demons_count"] = sum(1 for p in all_props if p.get("is_demon"))
            results["goblins_count"] = sum(1 for p in all_props if p.get("is_goblin"))
            
            logger.info(f"\n[PILLAR 1] PRIZEPICKS DATA COMPLETE:")
            logger.info(f"  Total Props: {len(all_props)}")
            logger.info(f"  Unique Players: {len(all_players)}")
            logger.info(f"  STANDARD: {results['standard_count']}")
            logger.info(f"  DEMONS: {results['demons_count']}")
            logger.info(f"  GOBLINS: {results['goblins_count']}")
            
            # ===== PILLAR 3: FETCH INJURIES =====
            logger.info("\n[PILLAR 3] Fetching injury data...")
            injuries = await fetch_injuries()
            await fetch_news()
            results["injuries_found"] = len(injuries)
            
            # ===== PILLAR 2: PROCESS STATS =====
            logger.info("\n[PILLAR 2] Processing stats...")
            
            # Deduplicate props
            unique_props = {}
            for prop in all_props:
                key = f"{prop['player_name']}|{prop['market']}|{prop['line']}|{prop['direction']}"
                if key not in unique_props:
                    unique_props[key] = prop
            
            processed_props = []
            prop_list = list(unique_props.values())
            batch_size = 50
            
            logger.info(f"  Processing {len(prop_list)} unique props...")
            
            for i in range(0, len(prop_list), batch_size):
                batch = prop_list[i:i+batch_size]
                
                for prop in batch:
                    try:
                        processed = await process_player_prop(prop)
                        processed_props.append(processed)
                        
                        if processed.get("bdl_player_id"):
                            results["stats_fetched"] += 1
                        
                        if processed.get("has_goblin_warning"):
                            results["goblin_warnings"] += 1
                        
                        # Track verification stats
                        if processed.get("source_verified"):
                            results["verification_stats"]["verified_count"] += 1
                        else:
                            status = processed.get("verification_status", "")
                            if status == "NAJI_SAFEGUARD_FAILED":
                                results["verification_stats"]["naji_safeguard_failures"] += 1
                                results["verification_stats"]["failed_count"] += 1
                            elif status == "HALLUCINATION_DETECTED":
                                results["verification_stats"]["hallucinations_detected"] += 1
                                results["verification_stats"]["failed_count"] += 1
                            elif status == "DISCREPANCY":
                                results["verification_stats"]["discrepancies_found"] += 1
                                results["verification_stats"]["failed_count"] += 1
                        
                        await asyncio.sleep(0.1)
                        
                    except Exception as e:
                        results["errors"].append(f"Prop error: {str(e)[:50]}")
                
                logger.info(f"  Processed {min(i+batch_size, len(prop_list))}/{len(prop_list)} props")
            
            # ===== STORE RESULTS =====
            logger.info("\n[STORAGE] Organizing data by player...")
            
            mapper_ready = await ensure_odds_mapper_loaded()
            odds_mapper = get_odds_mapper()
            
            if not mapper_ready or not odds_mapper:
                logger.error("[STORAGE] OddsApiMapper not available!")
                results["errors"].append("OddsApiMapper initialization failed")
            else:
                logger.info("[STORAGE] OddsApiMapper loaded")
            
            # Group props by player
            unique_player_names = list(all_players)
            player_data_map = {}
            
            if odds_mapper:
                player_data_map = await odds_mapper.lookupBatch(unique_player_names)
            
            # Build player dictionary
            players_dict = await self._build_players_dict(
                processed_props, player_data_map, sync_start
            )
            
            # Build cached board and derived collections
            await build_cached_board(processed_props, sync_start)
            await build_war_zone(players_dict, sync_start)
            await build_front_lines(players_dict, sync_start)
            await build_goblin_vault(players_dict, sync_start)
            await build_parlay_builder(players_dict, sync_start)
            await build_goblin_recon(players_dict, sync_start)
            
        except Exception as e:
            logger.error(f"[FULL SYNC] Error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            results["success"] = False
            results["errors"].append(str(e))
        
        results["duration"] = (datetime.now(timezone.utc) - sync_start).total_seconds()
        
        # Calculate verification rate
        total_verified = results["verification_stats"]["verified_count"]
        total_failed = results["verification_stats"]["failed_count"]
        total_checked = total_verified + total_failed
        results["verification_stats"]["verification_rate"] = (
            round((total_verified / total_checked * 100), 1) if total_checked > 0 else 0
        )
        
        logger.info("=" * 70)
        logger.info(f"SYNC COMPLETE - Duration: {results['duration']:.1f}s")
        logger.info(f"  Props: {results['total_props']} | Players: {results['unique_players']}")
        logger.info(f"  Verified: {total_verified} | Failed: {total_failed}")
        logger.info("=" * 70)
        
        return results
    
    async def run_delta_sync(
        self,
        current_date: str,
        fetch_todays_events: Callable,
        fetch_prizepicks_odds: Callable,
        build_war_zone: Callable,
        build_front_lines: Callable,
        build_goblin_vault: Callable
    ) -> Dict[str, Any]:
        """
        DELTA SYNC - Odds-only update for Delta Refreshes.
        
        Updates line and price values for existing players without
        re-fetching stats or regenerating Vision AI.
        """
        sync_start = datetime.now(timezone.utc)
        
        logger.info("─" * 70)
        logger.info(f"DELTA SYNC - ODDS ONLY UPDATE")
        logger.info(f"Date: {current_date}")
        logger.info("─" * 70)
        
        results = {
            "success": True,
            "sync_type": "delta",
            "sync_date": current_date,
            "sync_time": sync_start.isoformat(),
            "lines_updated": 0,
            "new_players": [],
            "removed_players": [],
            "errors": []
        }
        
        try:
            # Get existing players
            existing_board = await self.dg_cached_board.find_one({"type": "main_board"})
            existing_players = set()
            if existing_board and "board" in existing_board:
                for p in existing_board["board"].get("players", []):
                    existing_players.add(p.get("player_name", ""))
            
            # Fetch fresh odds
            logger.info("\n[DELTA] Fetching fresh odds...")
            events = await fetch_todays_events()
            
            if not events:
                results["success"] = False
                results["errors"].append("No NBA events found")
                return results
            
            all_props = []
            all_players = set()
            
            for event in events:
                props = await fetch_prizepicks_odds(event)
                if props:
                    all_props.extend(props)
                    for prop in props:
                        all_players.add(prop.get("player_name", ""))
            
            logger.info(f"[DELTA] Fetched {len(all_props)} props for {len(all_players)} players")
            
            # Identify changes
            new_players = all_players - existing_players
            removed_players = existing_players - all_players
            
            results["new_players"] = list(new_players)
            results["removed_players"] = list(removed_players)
            
            # Update odds in cached board
            if existing_board and "board" in existing_board:
                players_list = existing_board["board"].get("players", [])
                
                props_by_player = {}
                for prop in all_props:
                    pname = prop.get("player_name", "")
                    if pname not in props_by_player:
                        props_by_player[pname] = []
                    props_by_player[pname].append(prop)
                
                for player in players_list:
                    pname = player.get("player_name", "")
                    if pname in props_by_player:
                        new_props = props_by_player[pname]
                        
                        for old_prop in player.get("props", []):
                            for new_prop in new_props:
                                if (old_prop.get("market") == new_prop.get("market") and
                                    old_prop.get("direction") == new_prop.get("direction")):
                                    old_prop["line"] = new_prop.get("line", old_prop.get("line"))
                                    old_prop["price"] = new_prop.get("price", old_prop.get("price"))
                                    results["lines_updated"] += 1
                                    break
                
                if removed_players:
                    players_list = [p for p in players_list if p.get("player_name") not in removed_players]
                    existing_board["board"]["players"] = players_list
                
                existing_board["board"]["delta_updated_at"] = sync_start.isoformat()
                await self.dg_cached_board.update_one(
                    {"type": "main_board"},
                    {"$set": existing_board}
                )
            
            logger.info(f"[DELTA] Updated {results['lines_updated']} lines")
            
            # Rebuild tier collections
            if existing_board and "board" in existing_board:
                players_list = existing_board["board"].get("players", [])
                player_data = {p.get("player_name", ""): p for p in players_list if p.get("player_name")}
                
                if player_data:
                    logger.info("[DELTA] Rebuilding tier collections...")
                    try:
                        await build_war_zone(player_data, sync_start)
                        await build_front_lines(player_data, sync_start)
                        await build_goblin_vault(player_data, sync_start)
                    except Exception as e:
                        logger.error(f"[DELTA] Rebuild error: {e}")
            
        except Exception as e:
            logger.error(f"[DELTA] Sync error: {e}")
            results["success"] = False
            results["errors"].append(str(e))
        
        results["duration"] = (datetime.now(timezone.utc) - sync_start).total_seconds()
        logger.info(f"[DELTA] Completed in {results['duration']:.1f}s")
        logger.info("─" * 70)
        
        return results
    
    async def _build_players_dict(
        self,
        processed_props: List[Dict],
        player_data_map: Dict[str, Any],
        sync_time: datetime
    ) -> Dict[str, Dict]:
        """Build players dictionary from processed props."""
        players_dict = {}
        
        for prop in processed_props:
            player_name = prop.get("player_name", "")
            
            if player_name not in players_dict:
                hub_player = player_data_map.get(player_name, {})
                
                players_dict[player_name] = {
                    "player_name": player_name,
                    "player_id": hub_player.get("player_id") if hub_player else None,
                    "tank01_player_id": hub_player.get("tank01_id") if hub_player else None,
                    "team": hub_player.get("team") if hub_player else prop.get("home_team"),
                    "photo_url": hub_player.get("headshot_url") if hub_player else None,
                    "position": hub_player.get("position") if hub_player else None,
                    "is_verified": hub_player is not None,
                    "props": [],
                    "demons": [],
                    "goblins": [],
                    "standard": [],
                    "synced_at": sync_time.isoformat()
                }
            
            players_dict[player_name]["props"].append(prop)
            
            if prop.get("is_demon"):
                players_dict[player_name]["demons"].append(prop)
            elif prop.get("is_goblin"):
                players_dict[player_name]["goblins"].append(prop)
            else:
                players_dict[player_name]["standard"].append(prop)
        
        return players_dict

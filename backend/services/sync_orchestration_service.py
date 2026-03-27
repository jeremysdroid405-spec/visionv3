"""
Sync Orchestration Service
==========================
Extracted from services.engines.demon_goblin_engine.py for modularity.

Handles the main sync orchestration workflows:
- Full sync (run_full_sync) - 3-Pillar complete sync
- Delta sync (run_delta_sync) - Odds-only updates

Design: Uses engine reference for method delegation instead of callbacks.
"""
from typing import Dict, List, Any, Set, TYPE_CHECKING
from datetime import datetime, timezone
import asyncio
import logging

from motor.motor_asyncio import AsyncIOMotorDatabase

if TYPE_CHECKING:
    from services.engines.demon_goblin_engine import DemonGoblinEngine

logger = logging.getLogger(__name__)


class SyncOrchestrationService:
    """
    Service for orchestrating sync workflows.
    
    Requires engine reference to be set via set_engine() after initialization.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.dg_cached_board = db.dg_cached_board
        self.verification_failures = db.dg_verification_failures
        self.player_data = db.dg_player_data
        self.trending_cache = db.dg_trending
        self.sync_log = db.dg_sync_log
        self._engine = None
    
    def set_engine(self, engine: "DemonGoblinEngine"):
        """Set engine reference for method delegation."""
        self._engine = engine
    
    async def run_full_sync(
        self,
        current_date: str,
        prizepicks_region: str,
        prizepicks_bookmaker: str
    ) -> Dict[str, Any]:
        """
        Execute the full three-pillar sync with PrizePicks data.
        
        Pillar 1: Fetch events and PrizePicks odds
        Pillar 2: Process stats from BallDontLie
        Pillar 3: Fetch injuries from BDL
        """
        if not self._engine:
            raise RuntimeError("Engine not set. Call set_engine() first.")
        
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
            logger.info("[TRUTH ENGINE] Cleared previous verification failures for today")
            
            # ===== PILLAR 1: FETCH EVENTS AND PRIZEPICKS ODDS =====
            logger.info("\n[PILLAR 1] Fetching NBA events and PrizePicks lines...")
            logger.info(f"  Using region={prizepicks_region}, bookmaker={prizepicks_bookmaker}")
            
            events = await self._engine.fetch_todays_events()
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
                    odds_data = await self._engine.fetch_prizepicks_odds(event_id, event)
                    if odds_data:
                        props = self._engine.extract_prizepicks_props(odds_data)
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
            logger.info(f"  STANDARD (Main Markets): {results['standard_count']}")
            logger.info(f"  DEMONS (Alternate +100): {results['demons_count']}")
            logger.info(f"  GOBLINS (Alternate ≠+100): {results['goblins_count']}")
            
            # ===== PILLAR 3: FETCH INJURIES FIRST =====
            logger.info("\n[PILLAR 3] Fetching injury data from BDL...")
            injuries = await self._engine.fetch_injuries()
            await self._engine.fetch_news()
            results["injuries_found"] = len(injuries)
            
            # ===== FAST PATH: Build cached board directly =====
            # Skip the slow per-prop processing loop - it was doing API calls for EACH prop
            # The cached_board builder does the same work but optimized
            
            logger.info(f"\n[PILLAR 2] Building cached board from {len(all_props)} props...")
            results["total_props"] = len(all_props)
            results["unique_players"] = len(all_players)
            results["standard_count"] = sum(1 for p in all_props if p.get("prop_type") == "standard")
            results["demons_count"] = sum(1 for p in all_props if p.get("is_demon"))
            results["goblins_count"] = sum(1 for p in all_props if p.get("is_goblin"))
            
            logger.info(f"  Props: {results['total_props']} | Players: {results['unique_players']}")
            logger.info(f"  Standard: {results['standard_count']} | Demons: {results['demons_count']} | Goblins: {results['goblins_count']}")
            
            # Build the centralized cached board (THE FAST PATH)
            try:
                board_result = await self._engine._build_cached_board(all_props, sync_start)
                results["records_updated"] = board_result.get("players_count", 0)
                logger.info(f"[CACHED_BOARD] Built successfully with {results['records_updated']} players")
            except Exception as e:
                logger.error(f"[CACHED_BOARD] Error building: {e}")
                import traceback
                logger.error(traceback.format_exc())
                results["errors"].append(f"Cached board error: {str(e)}")
            
            # Log sync result
            await self.sync_log.insert_one({
                "sync_date": current_date,
                "sync_time": sync_start.isoformat(),
                "results": results,
                "completed_at": datetime.now(timezone.utc).isoformat()
            })
            
        except Exception as e:
            logger.error(f"Sync error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            results["success"] = False
            results["errors"].append(str(e))
        
        results["duration"] = (datetime.now(timezone.utc) - sync_start).total_seconds()
        
        # Calculate verification rate
        total_verifiable = results["verification_stats"]["verified_count"] + results["verification_stats"]["failed_count"]
        verification_rate = (results["verification_stats"]["verified_count"] / total_verifiable * 100) if total_verifiable > 0 else 0
        results["verification_stats"]["verification_rate"] = round(verification_rate, 2)
        
        self._log_sync_complete(results)
        
        return results
    
    async def run_delta_sync(self, current_date: str) -> Dict[str, Any]:
        """
        DELTA SYNC - Odds-only update for Delta Refreshes.
        
        Updates line and price values for existing players without
        re-fetching stats or regenerating Vision AI.
        """
        if not self._engine:
            raise RuntimeError("Engine not set. Call set_engine() first.")
        
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
            # Get existing players before update
            existing_board = await self.dg_cached_board.find_one({"type": "main_board"})
            existing_players = set()
            if existing_board and "board" in existing_board:
                for p in existing_board["board"].get("players", []):
                    existing_players.add(p.get("player_name", ""))
            
            # Fetch fresh events and odds
            logger.info("\n[DELTA] Fetching fresh odds from PrizePicks...")
            events = await self._engine.fetch_todays_events()
            
            if not events:
                results["success"] = False
                results["errors"].append("No NBA events found")
                return results
            
            all_props = []
            all_players = set()
            
            for event in events:
                event_id = event.get("id")
                if event_id:
                    odds_data = await self._engine.fetch_prizepicks_odds(event_id, event)
                    if odds_data:
                        props = self._engine.extract_prizepicks_props(odds_data)
                        if props:
                            all_props.extend(props)
                            for prop in props:
                                all_players.add(prop.get("player_name", ""))
                    await asyncio.sleep(0.3)
            
            logger.info(f"[DELTA] Fetched {len(all_props)} props for {len(all_players)} players")
            
            # Identify new and removed players
            new_players = all_players - existing_players
            removed_players = existing_players - all_players
            
            results["new_players"] = list(new_players)
            results["removed_players"] = list(removed_players)
            
            if new_players:
                logger.info(f"[DELTA] New players: {list(new_players)[:5]}...")
            if removed_players:
                logger.info(f"[DELTA] Removed players: {list(removed_players)[:5]}...")
            
            # Update existing players' odds
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
                        
                        # Update standard props
                        for old_prop in player.get("props", []):
                            for new_prop in new_props:
                                if (old_prop.get("market") == new_prop.get("market") and
                                    old_prop.get("direction") == new_prop.get("direction")):
                                    old_prop["line"] = new_prop.get("line", old_prop.get("line"))
                                    old_prop["price"] = new_prop.get("price", old_prop.get("price"))
                                    results["lines_updated"] += 1
                                    break
                        
                        # Update demons
                        for old_demon in player.get("demons", []):
                            for new_prop in new_props:
                                if (old_demon.get("market") == new_prop.get("market") and
                                    old_demon.get("direction") == new_prop.get("direction") and
                                    new_prop.get("is_demon")):
                                    old_demon["line"] = new_prop.get("line", old_demon.get("line"))
                                    old_demon["price"] = new_prop.get("price", old_demon.get("price"))
                                    results["lines_updated"] += 1
                                    break
                        
                        # Update goblins
                        for old_goblin in player.get("goblins", []):
                            for new_prop in new_props:
                                if (old_goblin.get("market") == new_prop.get("market") and
                                    old_goblin.get("direction") == new_prop.get("direction") and
                                    new_prop.get("is_goblin")):
                                    old_goblin["line"] = new_prop.get("line", old_goblin.get("line"))
                                    old_goblin["price"] = new_prop.get("price", old_goblin.get("price"))
                                    results["lines_updated"] += 1
                                    break
                
                # Remove players whose lines were pulled
                if removed_players:
                    players_list = [p for p in players_list if p.get("player_name") not in removed_players]
                    existing_board["board"]["players"] = players_list
                
                # Update the board
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
                    logger.info("[DELTA] Rebuilding War Zone, Front Lines, and Goblin Vault...")
                    try:
                        await self._engine._build_war_zone(player_data, sync_start)
                        logger.info("[WAR ZONE] Rebuilt with fresh data")
                    except Exception as e:
                        logger.error(f"[WAR ZONE] Rebuild error: {e}")
                    
                    try:
                        await self._engine._build_front_lines(player_data, sync_start)
                        logger.info("[FRONT LINES] Rebuilt with fresh data")
                    except Exception as e:
                        logger.error(f"[FRONT LINES] Rebuild error: {e}")
                    
                    try:
                        await self._engine._build_goblin_vault(player_data, sync_start)
                        logger.info("[GOBLIN VAULT] Rebuilt with fresh data")
                    except Exception as e:
                        logger.error(f"[GOBLIN VAULT] Rebuild error: {e}")
            
        except Exception as e:
            logger.error(f"[DELTA] Sync error: {e}")
            results["success"] = False
            results["errors"].append(str(e))
        
        results["duration"] = (datetime.now(timezone.utc) - sync_start).total_seconds()
        
        logger.info(f"[DELTA] Sync completed in {results['duration']:.1f}s")
        logger.info("─" * 70)
        
        return results
    
    def _build_player_data_dict(
        self,
        processed_props: List[Dict],
        player_hub_data: Dict[str, Any],
        player_popularity: Dict[str, int]
    ) -> Dict[str, Dict]:
        """Build player data dictionary from processed props."""
        player_data = {}
        
        for prop in processed_props:
            player_name = prop.get("player_name", "Unknown")
            
            if player_name not in player_data:
                hub_player = player_hub_data.get(player_name)
                
                if hub_player:
                    player_data[player_name] = {
                        "player_name": player_name,
                        "player_id": hub_player.get("player_id"),
                        "espn_id": hub_player.get("espn_id"),
                        "nba_id": hub_player.get("nba_id"),
                        "bdl_id": hub_player.get("bdl_id"),
                        "team": hub_player.get("team") or prop.get("bdl_team", ""),
                        "team_name": hub_player.get("team_name"),
                        "photo_url": hub_player.get("headshot_url"),
                        "headshot_url": hub_player.get("headshot_url"),
                        "photo_source": "nba_master_hub_2026",
                        "photo_locked": hub_player.get("photo_locked", True),
                        "position": hub_player.get("position") or prop.get("position", ""),
                        "jersey": hub_player.get("jersey"),
                        "season_avg": hub_player.get("stats", {}).get("season_avg", {}),
                        "injury_info": prop.get("injury_info", {}),
                        "popularity_order": player_popularity.get(player_name, 999),
                        "props": [],
                        "standard": [],
                        "demons": [],
                        "goblins": [],
                        "has_goblin_warning": False,
                        "has_new_injury": False,
                        "is_mapper_matched": True,
                        "is_verified": True
                    }
                else:
                    player_data[player_name] = {
                        "player_name": player_name,
                        "player_id": None,
                        "team": prop.get("bdl_team", ""),
                        "position": prop.get("position", ""),
                        "photo_url": None,
                        "headshot_url": None,
                        "injury_info": prop.get("injury_info", {}),
                        "popularity_order": player_popularity.get(player_name, 999),
                        "props": [],
                        "standard": [],
                        "demons": [],
                        "goblins": [],
                        "has_goblin_warning": False,
                        "has_new_injury": False,
                        "is_mapper_matched": False,
                        "is_verified": False
                    }
            
            # Add prop to player
            player_data[player_name]["props"].append(prop)
            
            # Classify into appropriate bucket
            if prop.get("is_demon"):
                player_data[player_name]["demons"].append(prop)
            elif prop.get("is_goblin"):
                player_data[player_name]["goblins"].append(prop)
            else:
                player_data[player_name]["standard"].append(prop)
            
            if prop.get("has_goblin_warning"):
                player_data[player_name]["has_goblin_warning"] = True
            
            # Calculate opponent
            if not player_data[player_name].get("opponent"):
                home_team = prop.get("home_team")
                away_team = prop.get("away_team")
                player_team = player_data[player_name].get("team")
                
                if player_team and home_team and away_team:
                    if player_team == home_team:
                        player_data[player_name]["opponent"] = away_team
                        player_data[player_name]["opponent_abbr"] = away_team
                    elif player_team == away_team:
                        player_data[player_name]["opponent"] = home_team
                        player_data[player_name]["opponent_abbr"] = home_team
                    else:
                        player_data[player_name]["opponent"] = away_team if home_team else None
                        player_data[player_name]["opponent_abbr"] = away_team if home_team else None
        
        return player_data
    
    def _build_trending_10(self, player_data: Dict[str, Dict]) -> List[Dict]:
        """Build trending 10 list from player data."""
        trending_list = []
        
        for name, data in player_data.items():
            demons_count = len(data.get("demons", []))
            goblins_count = len(data.get("goblins", []))
            special_count = demons_count + goblins_count
            
            if special_count == 0:
                continue
            
            popularity_order = data.get("popularity_order", 999)
            injury_info = data.get("injury_info", {})
            has_injury = injury_info.get("has_injury", False)
            
            score = popularity_order - (special_count * 2)
            if has_injury:
                score += 20
            
            best_prop = None
            best_hit_rate = 0
            for prop in data.get("props", []):
                if prop.get("is_demon") or prop.get("is_goblin"):
                    hit_rates = prop.get("hit_rates") or {}
                    l10 = hit_rates.get("l10") or {}
                    hit_rate = l10.get("hit_rate", 0) or 0
                    if hit_rate > best_hit_rate:
                        best_hit_rate = hit_rate
                        best_prop = prop
            
            trending_list.append({
                "player_name": name,
                "team": data.get("team", ""),
                "position": data.get("position", ""),
                "nba_id": data.get("nba_id"),
                "popularity_score": score,
                "popularity_order": popularity_order,
                "demons_count": demons_count,
                "goblins_count": goblins_count,
                "total_props": len(data.get("props", [])),
                "injury_info": injury_info,
                "has_new_injury": has_injury,
                "best_prop": best_prop,
                "best_hit_rate": best_hit_rate
            })
        
        trending_list.sort(key=lambda x: x["popularity_score"])
        return trending_list[:10]
    
    def _log_sync_complete(self, results: Dict[str, Any]):
        """Log sync completion summary."""
        logger.info("\n" + "=" * 70)
        logger.info(f"""
DEMON & GOBLIN SYNC COMPLETE - PRIZEPICKS EDITION
==================================================
Duration: {results['duration']:.1f}s
Date: {results['sync_date']}

PILLAR 1 - PRIZEPICKS (us_dfs region):
  Events: {results['events_count']}
  Total Props: {results['total_props']}
  Unique Players: {results['unique_players']}
  
CLASSIFICATION (Market-Based):
  STANDARD (Main Markets): {results['standard_count']}
  DEMONS (Alternate +100): {results['demons_count']}
  GOBLINS (Alternate ≠+100): {results['goblins_count']}
  
PILLAR 2 - BALLDONTLIE:
  Stats Fetched: {results['stats_fetched']}
  
PILLAR 3 - TANK01:
  Injuries Found: {results['injuries_found']}
  Goblin Warnings: {results['goblin_warnings']}

V3.1 TRUTH ENGINE - DATA INTEGRITY:
  Verified Props: {results['verification_stats']['verified_count']}
  Failed Props: {results['verification_stats']['failed_count']}
  Naji Safeguard Failures: {results['verification_stats']['naji_safeguard_failures']}
  Hallucinations Detected: {results['verification_stats']['hallucinations_detected']}
  Discrepancies Found: {results['verification_stats']['discrepancies_found']}
  Verification Rate: {results['verification_stats']['verification_rate']}%
""")
        logger.info("=" * 70)

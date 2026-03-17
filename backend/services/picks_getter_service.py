"""
Picks Getter Service
====================
SSOT ARCHITECTURE: This service reads from MongoDB ONLY.

NO external API calls are made here. All stats come from:
- PIPE 1: nba_master_hub_2026 (stats vault, populated by 0400 CRON)
- PIPE 2: dg_cached_board (live lines, populated by Odds API polling)

Handles fetching picks data from MongoDB for all tier endpoints:
- War Zone (Demons)
- Goblin Vault (Safe plays)
- Front Lines (Mixed)
- Parlay Builder
- Goblin Recon
- Cached Board & Player lookups
"""
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone, timedelta
import logging

from motor.motor_asyncio import AsyncIOMotorDatabase
from thefuzz import fuzz

# CONSOLIDATED: Use shared player lookup utility
from utils.player_lookup import get_player_by_id, get_player_by_name as shared_get_player_by_name

logger = logging.getLogger(__name__)


class PicksGetterService:
    """
    SSOT-Compliant Picks Service.
    
    CRITICAL: This service reads from MongoDB ONLY.
    - Stats from nba_master_hub_2026 (PIPE 1)
    - Lines from dg_cached_board (PIPE 2)
    
    NO external API calls. NO secondary internal APIs.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        
        # PIPE 2: Live Lines (Odds API destination)
        self.radar_picks = db.dg_radar_picks
        self.goblin_vault = db.dg_goblin_vault
        self.front_lines = db.dg_front_lines
        self.parlay_builder = db.dg_parlay_builder
        self.goblin_recon = db.dg_goblin_recon
        self.cached_board = db.dg_cached_board  # Active Lines
        self.player_data = db.dg_player_data
        self.daily_insights = db.dg_daily_insights
        self.sync_log = db.dg_sync_log
        self.events_cache = db.dg_events_cache
        self.odds_cache = db.dg_odds_cache
        
        # PIPE 1: Stats Vault (Tank01 CRON destination)
        self.master_hub = db.nba_master_hub_2026
    
    async def _get_player_lookup(self) -> Dict[str, Dict]:
        """
        SSOT PIPE 1: Get player lookup from shared utility.
        Uses consolidated lookup from /app/backend/utils/player_lookup.py
        """
        return await build_player_lookup(self.db)
    
    async def _get_player_by_id(self, player_id: str) -> Dict:
        """
        PRIMARY: Get player data from master hub by player_id.
        """
        return await get_player_by_id(self.db, player_id)
    
    async def _get_player_by_name(self, player_name: str) -> Dict:
        """
        FALLBACK: Get player data from master hub by name.
        Use _get_player_by_id when possible.
        """
        return await shared_get_player_by_name(self.db, player_name)
    
    async def _get_master_player(self, pick: Dict) -> Dict:
        """
        Get player from master hub - tries player_id first, then name.
        ALL photos and stats come from master hub.
        """
        # PRIMARY: Try player_id first
        player_id = pick.get('player_id') or pick.get('tank01_player_id') or pick.get('nba_player_id')
        if player_id:
            player = await self._get_player_by_id(str(player_id))
            if player:
                return player
        
        # FALLBACK: Try by name
        player_name = pick.get('player_name')
        if player_name:
            return await self._get_player_by_name(player_name)
        
        return None
    
    async def _get_player_stats(self, player_name: str, stat_type: str, line: float) -> Dict:
        """
        Get L5/L10/Season stats from master hub for a player.
        Used to enrich War Zone and Goblin Vault picks.
        """
        player = await self._get_player_by_name(player_name)
        if not player:
            return {"h5_rate": 0, "h10_rate": 0, "season_avg": 0, "l5_avg": 0, "l10_avg": 0}
        
        game_logs = player.get("game_logs", [])
        if not game_logs:
            return {"h5_rate": 0, "h10_rate": 0, "season_avg": 0, "l5_avg": 0, "l10_avg": 0}
        
        # Sort by date descending
        game_logs = sorted(game_logs, key=lambda x: x.get("game_date", ""), reverse=True)
        
        # Map stat type to game log field
        stat_map = {
            "PTS": "pts", "REB": "reb", "AST": "ast", "STL": "stl", "BLK": "blk",
            "3PM": "fg3m", "TO": "tov", "PRA": "pra", "P+R": "pts_reb",
            "P+A": "pts_ast", "R+A": "reb_ast"
        }
        stat_key = stat_map.get(stat_type, stat_type.lower())
        
        # Calculate for different windows
        l5 = game_logs[:5]
        l10 = game_logs[:10]
        season = game_logs
        
        def calc_stats(logs):
            if not logs:
                return 0, 0
            values = []
            hits = 0
            for g in logs:
                val = g.get(stat_key, 0) or 0
                # Handle combined stats
                if stat_type == "PRA":
                    val = (g.get("pts", 0) or 0) + (g.get("reb", 0) or 0) + (g.get("ast", 0) or 0)
                elif stat_type == "P+R":
                    val = (g.get("pts", 0) or 0) + (g.get("reb", 0) or 0)
                elif stat_type == "P+A":
                    val = (g.get("pts", 0) or 0) + (g.get("ast", 0) or 0)
                elif stat_type == "R+A":
                    val = (g.get("reb", 0) or 0) + (g.get("ast", 0) or 0)
                values.append(val)
                if val > line:
                    hits += 1
            avg = sum(values) / len(values) if values else 0
            hit_rate = (hits / len(values) * 100) if values else 0
            return avg, hit_rate
        
        l5_avg, h5_rate = calc_stats(l5)
        l10_avg, h10_rate = calc_stats(l10)
        season_avg, _ = calc_stats(season)
        
        return {
            "h5_rate": round(h5_rate, 1),
            "h10_rate": round(h10_rate, 1),
            "season_avg": round(season_avg, 1),
            "l5_avg": round(l5_avg, 1),
            "l10_avg": round(l10_avg, 1),
            "photo_url": player.get("headshot_url") or player.get("photo_url")
        }
    
    async def get_war_zone(self) -> Dict[str, Any]:
        """
        Get the War Zone - High-risk DEMON picks from PrizePicks.
        
        PRIZEPICKS ARCHITECTURE:
        - Reads DEMON props directly from dg_cached_board
        - DEMON = alternate market lines with +100 odds (boosted/hard)
        - Enriched with L5/L10/Season stats from master hub
        """
        # Fetch DEMON props directly from cached board (PrizePicks data)
        demon_props = await self.cached_board.find(
            {"is_demon": True, "bookmaker": "prizepicks"},
            {"_id": 0}
        ).sort("last_updated", -1).to_list(100)
        
        # Group by player and build picks
        players_demons = {}
        for prop in demon_props:
            player_name = prop.get("player_name")
            if not player_name:
                continue
            
            if player_name not in players_demons:
                players_demons[player_name] = {
                    "player_name": player_name,
                    "team": prop.get("away_team") or prop.get("home_team"),
                    "opponent": prop.get("home_team") if prop.get("away_team") else prop.get("away_team"),
                    "game_id": prop.get("game_id"),
                    "props": [],
                    "demon_count": 0,
                    "is_demon": True,
                    "tier_label": "DEMON",
                    "prizepicks_type": "boosted_hard"
                }
            
            players_demons[player_name]["props"].append({
                "stat_type": prop.get("stat_type_extracted"),
                "line": prop.get("line"),
                "odds": prop.get("price"),
                "direction": prop.get("direction"),
                "market": prop.get("market"),
                "is_demon": True,
                "tier_label": "DEMON"
            })
            players_demons[player_name]["demon_count"] += 1
        
        # Sort by demon count and convert to list
        picks = sorted(players_demons.values(), key=lambda x: x.get("demon_count", 0), reverse=True)[:10]
        
        # Enrich with stats from master hub and add primary stat
        for pick in picks:
            if pick.get("props"):
                primary = pick["props"][0]
                pick["stat_type"] = primary.get("stat_type")
                pick["line"] = primary.get("line")
                pick["odds"] = primary.get("odds")
                
                # Fetch stats from master hub
                player_stats = await self._get_player_stats(pick["player_name"], pick["stat_type"], pick["line"])
                pick.update(player_stats)
        
        sync_meta = await self.sync_log.find_one({"type": "cached_board"})
        
        return {
            "success": True,
            "synced_at": sync_meta.get("synced_at") if sync_meta else datetime.now(timezone.utc).isoformat(),
            "picks_count": len(picks),
            "picks": picks,
            "source": "prizepicks",
            "tier": "demon",
            "description": "High-risk boosted lines from PrizePicks"
        }
    
    async def get_goblin_vault(self) -> Dict[str, Any]:
        """
        Get the Goblin Vault - Safe GOBLIN picks from PrizePicks.
        
        PRIZEPICKS ARCHITECTURE:
        - Reads GOBLIN props directly from dg_cached_board
        - GOBLIN = alternate market lines with odds != +100 (discount/promo)
        - Enriched with L5/L10/Season stats from master hub
        """
        # Fetch GOBLIN props directly from cached board (PrizePicks data)
        goblin_props = await self.cached_board.find(
            {"is_goblin": True, "bookmaker": "prizepicks"},
            {"_id": 0}
        ).sort("last_updated", -1).to_list(100)
        
        # Group by player and build picks
        players_goblins = {}
        for prop in goblin_props:
            player_name = prop.get("player_name")
            if not player_name:
                continue
            
            if player_name not in players_goblins:
                players_goblins[player_name] = {
                    "player_name": player_name,
                    "team": prop.get("away_team") or prop.get("home_team"),
                    "opponent": prop.get("home_team") if prop.get("away_team") else prop.get("away_team"),
                    "game_id": prop.get("game_id"),
                    "props": [],
                    "goblin_count": 0,
                    "is_goblin": True,
                    "tier_label": "GOBLIN",
                    "prizepicks_type": "discount_promo"
                }
            
            players_goblins[player_name]["props"].append({
                "stat_type": prop.get("stat_type_extracted"),
                "line": prop.get("line"),
                "odds": prop.get("price"),
                "direction": prop.get("direction"),
                "market": prop.get("market"),
                "is_goblin": True,
                "tier_label": "GOBLIN"
            })
            players_goblins[player_name]["goblin_count"] += 1
        
        # Sort by goblin count and convert to list
        picks = sorted(players_goblins.values(), key=lambda x: x.get("goblin_count", 0), reverse=True)[:10]
        
        # Enrich with stats from master hub and add primary stat
        for pick in picks:
            if pick.get("props"):
                primary = pick["props"][0]
                pick["stat_type"] = primary.get("stat_type")
                pick["line"] = primary.get("line")
                pick["odds"] = primary.get("odds")
                
                # Fetch stats from master hub
                player_stats = await self._get_player_stats(pick["player_name"], pick["stat_type"], pick["line"])
                pick.update(player_stats)
        
        sync_meta = await self.sync_log.find_one({"type": "cached_board"})
        
        return {
            "success": True,
            "synced_at": sync_meta.get("synced_at") if sync_meta else datetime.now(timezone.utc).isoformat(),
            "picks_count": len(picks),
            "picks": picks,
            "source": "prizepicks",
            "tier": "goblin",
            "description": "Safe discount lines from PrizePicks"
        }
    
    async def get_front_lines(self) -> Dict[str, Any]:
        """
        Get THE FRONT LINES - STANDARD (main) lines from PrizePicks.
        
        PRIZEPICKS ARCHITECTURE:
        - Reads STANDARD props directly from dg_cached_board
        - STANDARD = main market lines (no "_alternate" suffix)
        - These are the primary PrizePicks lines
        """
        # Fetch STANDARD props directly from cached board (PrizePicks data)
        standard_props = await self.cached_board.find(
            {"tier_label": "STANDARD", "bookmaker": "prizepicks"},
            {"_id": 0}
        ).sort("last_updated", -1).to_list(100)
        
        # Group by player and build picks
        players_standards = {}
        for prop in standard_props:
            player_name = prop.get("player_name")
            if not player_name:
                continue
            
            if player_name not in players_standards:
                players_standards[player_name] = {
                    "player_name": player_name,
                    "team": prop.get("away_team") or prop.get("home_team"),
                    "opponent": prop.get("home_team") if prop.get("away_team") else prop.get("away_team"),
                    "game_id": prop.get("game_id"),
                    "props": [],
                    "standard_count": 0,
                    "is_goblin": False,
                    "is_demon": False,
                    "tier_label": "STANDARD",
                    "prizepicks_type": "standard_line"
                }
            
            players_standards[player_name]["props"].append({
                "stat_type": prop.get("stat_type_extracted"),
                "line": prop.get("line"),
                "odds": prop.get("price"),
                "direction": prop.get("direction"),
                "market": prop.get("market"),
                "is_goblin": False,
                "is_demon": False,
                "tier_label": "STANDARD"
            })
            players_standards[player_name]["standard_count"] += 1
        
        # Sort by standard count and convert to list
        picks = sorted(players_standards.values(), key=lambda x: x.get("standard_count", 0), reverse=True)[:10]
        
        # Enrich with stats from master hub and add primary stat
        for pick in picks:
            if pick.get("props"):
                primary = pick["props"][0]
                pick["stat_type"] = primary.get("stat_type")
                pick["line"] = primary.get("line")
                pick["odds"] = primary.get("odds")
                
                # Fetch stats from master hub
                player_stats = await self._get_player_stats(pick["player_name"], pick["stat_type"], pick["line"])
                pick.update(player_stats)
        
        sync_meta = await self.sync_log.find_one({"type": "cached_board"})
        
        return {
            "success": True,
            "synced_at": sync_meta.get("synced_at") if sync_meta else datetime.now(timezone.utc).isoformat(),
            "picks_count": len(picks),
            "picks": picks,
            "source": "prizepicks",
            "tier": "standard",
            "description": "Main PrizePicks lines (STRIKE zone)"
        }
    
    async def get_parlay_builder(self) -> Dict[str, Any]:
        """
        Get the Parlay Builder (Gauntlet) parlays from MongoDB.
        Data is PRE-ENRICHED during sync. Just reads and returns with AI insights.
        """
        doc = await self.parlay_builder.find_one({}, {"_id": 0})
        
        if not doc:
            return {
                "success": False,
                "message": "No parlay data. Run /api/v3/sync first.",
                "parlays": {}
            }
        
        # Add AI insights to parlay picks
        parlays = doc.get("parlays", {})
        for parlay_key, parlay_data in parlays.items():
            picks = parlay_data.get("picks", [])
            for pick in picks:
                insight = await self.daily_insights.find_one(
                    {"player_name": pick.get('player_name')},
                    {"_id": 0, "insight_summary": 1, "ai_confidence_rating": 1}
                )
                if insight:
                    pick['insight_summary'] = insight.get('insight_summary', '')
                    pick['ai_confidence_rating'] = insight.get('ai_confidence_rating', 50)
        
        return {
            "success": True,
            "synced_at": doc.get("synced_at"),
            "total_demons_analyzed": doc.get("total_demons_analyzed", 0),
            "games_with_correlation": doc.get("games_with_correlation", 0),
            "parlays": parlays,
            "algorithm": {
                "description": "Whale Scoring + Correlation Filter",
                "whale_score": "(H10 × 0.6) + (H5 × 0.4) × heat_boost",
                "correlation": "Same-game pairing for 4-6 picks"
            }
        }
    
    async def get_goblin_recon(self) -> Dict[str, Any]:
        """
        Get the Goblin Recon (Safe Haven) parlays from MongoDB.
        Data is PRE-ENRICHED during sync. Just reads and returns with AI insights.
        """
        doc = await self.goblin_recon.find_one({}, {"_id": 0})
        
        if not doc:
            return {
                "success": False,
                "message": "No Recon data. Run /api/v3/sync first.",
                "parlays": {}
            }
        
        # Add AI insights to parlay picks
        parlays = doc.get("parlays", {})
        for parlay_key, parlay_data in parlays.items():
            picks = parlay_data.get("picks", [])
            for pick in picks:
                insight = await self.daily_insights.find_one(
                    {"player_name": pick.get('player_name')},
                    {"_id": 0, "insight_summary": 1, "ai_confidence_rating": 1}
                )
                if insight:
                    pick['insight_summary'] = insight.get('insight_summary', '')
                    pick['ai_confidence_rating'] = insight.get('ai_confidence_rating', 50)
        
        return {
            "success": True,
            "synced_at": doc.get("synced_at"),
            "total_candidates": doc.get("total_candidates", 0),
            "recon_locks": doc.get("recon_locks", 0),
            "games_available": doc.get("games_available", 0),
            "parlays": parlays,
            "algorithm": {
                "name": "Floor Scoring",
                "description": "Maximum win probability using high-consistency picks",
                "min_hit_rate": "88%+",
                "flex_play": "6-Pick Fortress designed for PrizePicks Flex"
            }
        }
    
    async def get_cached_board(self) -> Dict[str, Any]:
        """
        Get the CACHED board from MongoDB.
        NO API CALLS - reads only from database.
        """
        sync_meta = await self.sync_log.find_one({"type": "cached_board"})
        
        if not sync_meta:
            return {
                "success": False,
                "synced_at": None,
                "message": "No cached data. Run /api/v3/sync first.",
                "players": [],
                "trending": []
            }
        
        # Get all players from cached_board (exclude _id)
        players = await self.cached_board.find({}, {"_id": 0}).sort("rank", 1).to_list(500)
        
        # Clean any remaining ObjectIds
        for player in players:
            self._clean_object_ids(player)
        
        # Get trending (top 10)
        trending = players[:10] if players else []
        
        return {
            "success": True,
            "synced_at": sync_meta.get("synced_at"),
            "players_count": len(players),
            "total_props": sync_meta.get("total_props", 0),
            "players": players,
            "trending": trending
        }
    
    async def get_cached_player(self, player_name: str) -> Dict[str, Any]:
        """
        Get a single player from the CACHED board or player_data.
        Also includes advanced analytics insights.
        NO API CALLS - reads only from database.
        
        Stats (L5/L10/SZN) come EXCLUSIVELY from nba_master_hub_2026.baseline_stats.
        """
        # Try dg_cached_board first (has opponent data)
        player = await self.cached_board.find_one(
            {"player_name": player_name},
            {"_id": 0}
        )
        
        if player:
            self._clean_object_ids(player)
            await self._enrich_player_with_master_hub_stats(player)
            await self._add_player_insights(player)
            return {"success": True, "player": player, "source": "cached_board"}
        
        # Try case-insensitive search in cached_board
        player = await self.cached_board.find_one(
            {"player_name": {"$regex": f"^{player_name}$", "$options": "i"}},
            {"_id": 0}
        )
        
        if player:
            self._clean_object_ids(player)
            await self._enrich_player_with_master_hub_stats(player)
            await self._add_player_insights(player)
            return {"success": True, "player": player, "source": "cached_board"}
        
        # Fallback: Try player_data (exact match)
        player = await self.player_data.find_one(
            {"player_name": player_name},
            {"_id": 0}
        )
        
        if player:
            self._clean_object_ids(player)
            await self._enrich_player_with_master_hub_stats(player)
            await self._add_player_insights(player)
            return {"success": True, "player": player, "source": "player_data"}
        
        # Try case-insensitive in player_data
        player = await self.player_data.find_one(
            {"player_name": {"$regex": f"^{player_name}$", "$options": "i"}},
            {"_id": 0}
        )
        
        if player:
            self._clean_object_ids(player)
            await self._enrich_player_with_master_hub_stats(player)
            await self._add_player_insights(player)
            return {"success": True, "player": player, "source": "player_data"}
        
        # Fuzzy search in both collections
        all_players_pd = await self.player_data.find({}, {"player_name": 1, "_id": 0}).to_list(500)
        all_players_cb = await self.cached_board.find({}, {"player_name": 1, "_id": 0}).to_list(500)
        all_players = all_players_pd + all_players_cb
        
        best_match = None
        best_score = 0
        match_source = None
        for p in all_players:
            score = fuzz.ratio(player_name.lower(), p["player_name"].lower())
            if score > best_score and score > 70:
                best_score = score
                best_match = p["player_name"]
                match_source = "player_data" if p in all_players_pd else "cached_board"
        
        if best_match:
            collection = self.player_data if match_source == "player_data" else self.cached_board
            player = await collection.find_one(
                {"player_name": best_match},
                {"_id": 0}
            )
            self._clean_object_ids(player)
            await self._enrich_player_with_master_hub_stats(player)
            await self._add_player_insights(player)
            return {"success": True, "player": player, "matched_name": best_match, "source": match_source}
        
        return {
            "success": False,
            "message": "Lines loading... Player not in cache.",
            "player": None
        }
    
    async def _enrich_player_with_master_hub_stats(self, player: Dict) -> None:
        """
        SSOT INTERSECTION: Join PIPE 1 stats with PIPE 2 lines.
        
        All stats come from nba_master_hub_2026 (PIPE 1).
        Lookup by player_id FIRST, then name as fallback.
        """
        if not player:
            return
        
        # SSOT: Get stats from master hub by player_id FIRST
        hub_player = await self._get_master_player(player)
        
        if not hub_player:
            player_name = player.get("player_name", "")
            logger.debug(f"[SSOT] No master hub data for: {player_name}")
            return
        
        # PIPE 1: Get baseline_stats and game_logs from master hub
        baseline_stats = hub_player.get("baseline_stats", {})
        game_logs = hub_player.get("game_logs", [])
        
        # Add structural data - PHOTOS FROM MASTER HUB ONLY
        player["baseline_stats"] = baseline_stats
        player["photo_url"] = hub_player.get("headshot_url")
        player["headshot_url"] = hub_player.get("headshot_url")
        
        # Import the coupled stats calculator (uses game_logs from PIPE 1)
        from services.stats_service import calculate_coupled_stats
        
        # Import intel calculator for radar picks
        from services.intel_suite_calculator import get_intel_calculator
        intel_calculator = get_intel_calculator(self.db)
        
        # INTERSECTION: Enrich PIPE 2 lines with PIPE 1 stats
        props = player.get("props", [])
        for prop in props:
            stat_type = prop.get("stat_type_extracted", "") or prop.get("stat_type", "")
            line_value = prop.get("line", 0)  # Line comes from PIPE 2
            
            # Normalize stat type for lookup (P+R -> PR, etc.)
            stat_key = stat_type
            norm_map = {"P+R": "PR", "P+A": "PA", "R+A": "RA"}
            stat_key = norm_map.get(stat_type, stat_type)
            
            # Calculate COUPLED stats from PIPE 1 game_logs
            if game_logs and line_value > 0:
                coupled = calculate_coupled_stats(game_logs, stat_type, line_value)
                
                # Use coupled stats for L5 and L10 (guaranteed consistent)
                prop["l5_avg"] = coupled["l5"]["avg"]
                prop["l10_avg"] = coupled["l10"]["avg"]
                prop["l5_hit_rate"] = coupled["l5"]["hit_rate"]
                prop["l10_hit_rate"] = coupled["l10"]["hit_rate"]
                prop["l5_games_over"] = coupled["l5"]["games_over"]
                prop["l10_games_over"] = coupled["l10"]["games_over"]
                prop["l5_total_games"] = coupled["l5"]["total_games"]
                prop["l10_total_games"] = coupled["l10"]["total_games"]
                
                # Season avg from coupled calculation (or fallback to baseline)
                prop["season_avg"] = coupled["season"]["avg"] or baseline_stats.get(stat_key, {}).get("season_avg")
                prop["season_hit_rate"] = coupled["season"]["hit_rate"]
                
                # Mark stats source
                prop["stats_coupled"] = True
                prop["stats_source"] = "ssot_game_logs"
            else:
                # Fallback to baseline_stats if no game logs
                stat_data = baseline_stats.get(stat_key, {})
                prop["l5_avg"] = stat_data.get("l5_avg")
                prop["l10_avg"] = stat_data.get("l10_avg")
                prop["season_avg"] = stat_data.get("season_avg")
                prop["stats_coupled"] = False
                prop["stats_source"] = "ssot_baseline"
            
            # If this is a radar pick (demon or goblin), add full intel_suite
            is_radar = prop.get("is_demon") or prop.get("is_goblin") or prop.get("is_radar_pick")
            if is_radar:
                intel_suite = await intel_calculator.calculate_intel_suite(
                    player_name=player_name,
                    stat_type=stat_key,
                    line=prop.get("line", 0),
                    direction=prop.get("direction", "over"),
                    opponent=player.get("opponent"),
                    board_pick=prop  # Pass prop as board_pick for additional context
                )
                prop["intel_suite"] = intel_suite
        
        logger.debug(f"[SSOT] Enriched {len(props)} props for {player_name}")
    
    async def get_most_popular_bets(self) -> Dict[str, Any]:
        """
        Get Top 20 Most Popular BETS (specific props, not just players)
        Returns actual bet lines with ticket volume/popularity scoring
        Includes Standard, Demon, and Goblin lines
        Auto-purges games that have already started
        
        Pulls from dg_radar_picks, dg_goblin_vault, and dg_front_lines collections
        """
        try:
            now = datetime.now(timezone.utc)
            popular_bets = []
            
            # STRATEGY: Get bets from tiered picks collections
            # These have already been processed with hit rates and season_avg
            
            # Get from War Zone (Demons)
            radar_picks = await self.radar_picks.find({}, {"_id": 0}).to_list(20)
            for pick in radar_picks:
                popular_bets.append({
                    "player_name": pick.get("player_name", ""),
                    "player_id": pick.get("player_id") or pick.get("tank01_player_id"),
                    "team": pick.get("team", ""),
                    "stat_type": pick.get("stat_type", ""),
                    "line": pick.get("demon_line") or pick.get("line"),
                    "line_type": "demon",
                    "is_demon": True,
                    "is_goblin": False,
                    "direction": pick.get("direction", "over").lower(),
                    "h10_rate": pick.get("h10_rate", 0),
                    "h5_rate": pick.get("h5_rate", 0),
                    "h10_over": pick.get("h10_over", 0),
                    "h10_games": pick.get("h10_games", 10),
                    "h5_over": pick.get("h5_over", 0),
                    "h5_games": pick.get("h5_games", 5),
                    "season_avg": pick.get("season_avg"),
                    "gap_pct": pick.get("gap_pct", 0),
                    "popularity_score": pick.get("radar_score", 0) or pick.get("demon_score", 0),
                    "odds": pick.get("demon_odds") or pick.get("odds"),
                    "commence_time": pick.get("commence_time"),
                    "source": "war_zone"
                })
            
            # Get from Safe Haven (Goblins)
            vault_picks = await self.goblin_vault.find({}, {"_id": 0}).to_list(20)
            for pick in vault_picks:
                popular_bets.append({
                    "player_name": pick.get("player_name", ""),
                    "player_id": pick.get("player_id") or pick.get("tank01_player_id"),
                    "team": pick.get("team", ""),
                    "stat_type": pick.get("stat_type", ""),
                    "line": pick.get("goblin_line") or pick.get("line"),
                    "line_type": "goblin",
                    "is_demon": False,
                    "is_goblin": True,
                    "direction": pick.get("direction", "over").lower(),
                    "h10_rate": pick.get("h10_rate", 0),
                    "h5_rate": pick.get("h5_rate", 0),
                    "h10_over": pick.get("h10_over", 0),
                    "h10_games": pick.get("h10_games", 10),
                    "h5_over": pick.get("h5_over", 0),
                    "h5_games": pick.get("h5_games", 5),
                    "season_avg": pick.get("season_avg"),
                    "gap_pct": pick.get("gap_pct", 0),
                    "popularity_score": pick.get("vault_score", 0) or pick.get("goblin_score", 0),
                    "odds": pick.get("goblin_odds") or pick.get("odds"),
                    "commence_time": pick.get("commence_time"),
                    "source": "safe_haven"
                })
            
            # Get from Front Lines (Mixed)
            front_picks = await self.front_lines.find({}, {"_id": 0}).to_list(20)
            for pick in front_picks:
                is_demon = pick.get("is_demon", False)
                is_goblin = pick.get("is_goblin", False)
                popular_bets.append({
                    "player_name": pick.get("player_name", ""),
                    "team": pick.get("team", ""),
                    "photo_url": pick.get("photo_url", ""),
                    "stat_type": pick.get("stat_type", ""),
                    "line": pick.get("demon_line") if is_demon else pick.get("goblin_line") if is_goblin else pick.get("line"),
                    "line_type": "demon" if is_demon else "goblin" if is_goblin else "standard",
                    "is_demon": is_demon,
                    "is_goblin": is_goblin,
                    "direction": pick.get("direction", "over").lower(),
                    "h10_rate": pick.get("h10_rate", 0),
                    "h5_rate": pick.get("h5_rate", 0),
                    "h10_over": pick.get("h10_over", 0),
                    "h10_games": pick.get("h10_games", 10),
                    "h5_over": pick.get("h5_over", 0),
                    "h5_games": pick.get("h5_games", 5),
                    "season_avg": pick.get("season_avg"),
                    "gap_pct": pick.get("gap_pct", 0),
                    "popularity_score": pick.get("front_lines_score", 0),
                    "odds": pick.get("odds"),
                    "commence_time": pick.get("commence_time"),
                    "source": "front_lines"
                })
            
            # Sort by popularity/score and dedupe
            seen = set()
            unique_bets = []
            for bet in sorted(popular_bets, key=lambda x: x.get("popularity_score", 0), reverse=True):
                key = f"{bet['player_name']}_{bet['stat_type']}_{bet['line']}"
                if key not in seen:
                    seen.add(key)
                    unique_bets.append(bet)
            
            # ===== PLAYER DATA & STATS ENRICHMENT from nba_master_hub_2026 =====
            # ALL player data and stats come from master hub by player_id
            for bet in unique_bets[:20]:
                master_player = await self._get_master_player(bet)
                if master_player:
                    stat_type = bet.get('stat_type', '')
                    # Player identity & photo FROM MASTER HUB
                    bet['player_id'] = master_player.get('player_id')
                    bet['nba_id'] = master_player.get('nba_id') or master_player.get('nba_player_id')
                    bet['espn_id'] = master_player.get('espn_id')
                    bet['photo_url'] = master_player.get('headshot_url')
                    bet['headshot_url'] = master_player.get('headshot_url')
                    
                    # Baseline stats for this prop category
                    baseline_stats = master_player.get('baseline_stats', {})
                    if stat_type and baseline_stats:
                        cat_stats = baseline_stats.get(stat_type, {})
                        if cat_stats:
                            bet['l5_avg'] = cat_stats.get('l5_avg')
                            bet['l10_avg'] = cat_stats.get('l10_avg')
                            bet['season_avg'] = cat_stats.get('season_avg')
                    
                    bet['baseline_stats'] = baseline_stats
            
            return {
                "status": "live" if unique_bets else "awaiting_action",
                "bets": unique_bets[:20],
                "total_available": len(unique_bets),
                "timestamp": now.isoformat()
            }
            
        except Exception as e:
            logger.error(f"[MOST_POPULAR] Error: {e}")
            return {
                "status": "error",
                "bets": [],
                "error": str(e)
            }
    
    # ==================== PRIVATE HELPER METHODS ====================
    
    async def _add_insights_to_pick(self, pick: Dict) -> None:
        """
        SSOT: Enrich a single pick with stats from master hub.
        
        ALL data comes from nba_master_hub_2026 (PIPE 1).
        Lookup is by player_id FIRST, then by name as fallback.
        """
        # SSOT: Get player data from master hub by ID first
        master_player = await self._get_master_player(pick)
        if master_player:
            # Structural data (protected fields)
            pick['player_id'] = master_player.get('player_id')
            pick['nba_id'] = master_player.get('nba_id') or master_player.get('nba_player_id')
            pick['espn_id'] = master_player.get('espn_id')
            # PHOTOS FROM MASTER HUB ONLY
            pick['headshot_url'] = master_player.get('headshot_url')
            pick['photo_url'] = master_player.get('headshot_url')
            if not pick.get('team'):
                pick['team'] = master_player.get('team')
            if not pick.get('position'):
                pick['position'] = master_player.get('position')
            
            # PIPE 1: Get stats from master hub
            baseline_stats = master_player.get('baseline_stats', {})
            game_logs = master_player.get('game_logs', [])
            stat_type = pick.get('stat_type', '')
            line_value = pick.get('line') or pick.get('demon_line') or pick.get('goblin_line') or 0
            
            # Calculate coupled stats from PIPE 1 game_logs
            if game_logs and stat_type and line_value > 0:
                from services.stats_service import calculate_coupled_stats
                coupled = calculate_coupled_stats(game_logs, stat_type, line_value)
                
                # Use coupled stats (guaranteed consistent hit rate + avg)
                pick['l5_avg'] = coupled["l5"]["avg"]
                pick['l10_avg'] = coupled["l10"]["avg"]
                pick['season_avg'] = coupled["season"]["avg"] or baseline_stats.get(stat_type, {}).get('season_avg')
                pick['l5_hit_rate'] = coupled["l5"]["hit_rate"]
                pick['l10_hit_rate'] = coupled["l10"]["hit_rate"]
                pick['l5_games_over'] = coupled["l5"]["games_over"]
                pick['l10_games_over'] = coupled["l10"]["games_over"]
                pick['stats_coupled'] = True
                pick['stats_source'] = 'ssot_game_logs'
            elif stat_type and baseline_stats:
                # Fallback to baseline_stats if no game logs
                cat_stats = baseline_stats.get(stat_type, {})
                if cat_stats:
                    pick['l5_avg'] = cat_stats.get('l5_avg')
                    pick['l10_avg'] = cat_stats.get('l10_avg')
                    pick['season_avg'] = cat_stats.get('season_avg')
                pick['stats_coupled'] = False
                pick['stats_source'] = 'ssot_baseline'
            
            # Store full baseline_stats for frontend access
            pick['baseline_stats'] = baseline_stats
        
        # Get player_name for legacy lookups (daily_insights, etc)
        player_name = pick.get('player_name', '')
        
        # Get old insight_summary from daily_insights
        if player_name:
            insight = await self.daily_insights.find_one(
                {"player_name": player_name},
                {"_id": 0, "insight_summary": 1, "ai_confidence_rating": 1}
            )
            if insight:
                pick['insight_summary'] = insight.get('insight_summary', '')
                pick['ai_confidence_rating'] = insight.get('ai_confidence_rating', 50)
            else:
                # Fallback: Calculate AI confidence from pillar_4_context (0-1) -> (0-100)
                pillar_4 = pick.get('pillar_4_context', 0.5)
                pick['ai_confidence_rating'] = int(pillar_4 * 100)
            
            # Get new intel_briefing from cached_board
            board_entry = await self.cached_board.find_one(
                {"player_name": player_name},
                {"_id": 0, "intel_briefing": 1}
            )
            if board_entry and board_entry.get('intel_briefing'):
                pick['intel_briefing'] = board_entry.get('intel_briefing')
    
    async def _add_player_insights(self, player: Dict) -> None:
        """
        SSOT: Add stats and insights from master hub to a player dict.
        All stats come from nba_master_hub_2026 (PIPE 1).
        """
        if not player or not player.get("player_name"):
            return
        
        player_name = player.get("player_name")
        
        # SSOT: Get player data from master hub ONLY
        master_player = await self._get_player_by_name(player_name)
        if master_player:
            # Structural data (protected fields)
            player['player_id'] = master_player.get('player_id')
            player['nba_id'] = master_player.get('nba_id')
            player['espn_id'] = master_player.get('espn_id')
            player['headshot_url'] = master_player.get('headshot_url')
            player['photo_url'] = master_player.get('headshot_url')
            if not player.get('team'):
                player['team'] = master_player.get('team')
            if not player.get('position'):
                player['position'] = master_player.get('position')
            
            # ===== BASELINE STATS from master hub =====
            player['baseline_stats'] = master_player.get('baseline_stats', {})
        
        insights = await self.daily_insights.find_one(
            {"player_name": player_name},
            {"_id": 0, "player_name": 0, "team": 0, "synced_at": 0}
        )
        
        if insights:
            player["insights"] = insights
        else:
            # Provide default insights if not calculated yet
            player["insights"] = {
                "schedule_density_factor": 1.0,
                "pace_adjustment_factor": 1.0,
                "usage_bump_percent": 0,
                "volatility_score": "Low",
                "volatility_stddev": 0,
                "insight_summary": "",
                "ai_confidence_rating": 50,
                "is_back_to_back": False,
                "is_three_in_four": False,
                "days_rest": 2,
                "injured_teammates": []
            }
    
    def _clean_object_ids(self, player: Dict) -> None:
        """Remove all ObjectId fields from nested arrays to prevent serialization errors."""
        for key in ["props", "demons", "goblins", "standard"]:
            if key in player and isinstance(player[key], list):
                for item in player[key]:
                    if isinstance(item, dict):
                        item.pop("_id", None)

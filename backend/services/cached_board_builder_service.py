"""
Cached Board Builder Service
============================
Extracted from demon_goblin_engine.py for modularity.

Handles building the centralized cached board from props data.
All tier sections (War Zone, Goblin Vault, Front Lines) read from here.
"""
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
import logging

from motor.motor_asyncio import AsyncIOMotorDatabase
from services.utils_service import sanitize_player_name

logger = logging.getLogger(__name__)


class CachedBoardBuilderService:
    """
    Service for building the centralized cached board.
    
    Architecture v4.0: Odds API Mapper Integration
    - Props come from Odds API with player names in 'description' field
    - Uses Odds API Mapper to get player_id directly
    - Mapper returns full player data from nba_master_hub_2026
    - Stores everything in dg_cached_board
    """
    
    def __init__(self, db: AsyncIOMotorDatabase, tier_builder_service, parlay_builder_service):
        self.db = db
        self.tier_builder_service = tier_builder_service
        self.parlay_builder_service = parlay_builder_service
        
        # Collection references
        self.cached_board = db.dg_cached_board
        self.sync_log = db.dg_sync_log
        self.player_stats = db.dg_player_stats
        self.master_roster = db.dg_master_roster
        self.flagged_players = db.dg_flagged_players
        
        # Mapper reference - must be set externally
        self._odds_mapper = None
    
    def set_odds_mapper(self, mapper):
        """Set the Odds API Mapper instance"""
        self._odds_mapper = mapper
    
    async def build_cached_board(
        self,
        props: List[Dict],
        sync_time: datetime,
        ensure_mapper_loaded_callback=None
    ) -> Dict[str, Any]:
        """
        Build the centralized cached board from props.
        
        This is THE ONLY place where player data enrichment happens.
        All sections (War Zone, Goblin Recon, Gauntlet, Safe Haven) read from here.
        
        Data Flow (v4.0 - Mapper-based):
        1. Props come from Odds API with player names in 'description' field
        2. For each prop, use Odds API Mapper to get player_id directly
        3. Mapper returns full player data from nba_master_hub_2026
        4. Store everything in dg_cached_board
        5. No more fuzzy name matching - exact lookup via permanent mapping
        """
        if not props:
            return {"success": True, "message": "No props to build", "players_count": 0}
        
        logger.info(f"[CACHED_BOARD] Building centralized board from {len(props)} props...")
        
        # Check if mapper is available
        if ensure_mapper_loaded_callback:
            mapper_ready = await ensure_mapper_loaded_callback()
            if not mapper_ready or self._odds_mapper is None:
                logger.error("[CACHED_BOARD] Odds API Mapper not available - falling back to legacy lookup")
                return await self.build_cached_board_legacy(props, sync_time)
        elif self._odds_mapper is None:
            logger.error("[CACHED_BOARD] Odds API Mapper not set - falling back to legacy lookup")
            return await self.build_cached_board_legacy(props, sync_time)
        
        logger.info("[CACHED_BOARD] Odds API Mapper loaded and ready")
        
        # STEP 2: BATCH LOOKUP ALL PLAYER NAMES
        unique_player_names = set(prop.get("player_name", "Unknown") for prop in props)
        logger.info(f"[CACHED_BOARD] Looking up {len(unique_player_names)} unique players via Mapper")
        
        # Batch lookup via mapper
        player_data_map = await self._odds_mapper.lookupBatch(list(unique_player_names))
        
        # Count matches
        matched_count = sum(1 for v in player_data_map.values() if v is not None)
        unmatched_count = len(unique_player_names) - matched_count
        logger.info(f"[CACHED_BOARD] Mapper results: {matched_count} matched, {unmatched_count} unmatched")
        
        # STEP 3: LOAD SUPPLEMENTARY DATA
        stats_map = await self._load_stats_map()
        signals_map = await self._load_signals_map()
        
        # STEP 4: BUILD PLAYER DICT
        players_dict, unmatched_players = await self._build_players_dict(
            props, player_data_map, stats_map, signals_map, sync_time
        )
        
        # Flag unmatched players
        if unmatched_players:
            logger.warning(f"[CACHED_BOARD] {len(unmatched_players)} players not found in Odds API Mapper: {unmatched_players[:5]}...")
            await self._flag_unmatched_players(unmatched_players, sync_time)
        
        # STEP 5: STORE IN CACHED_BOARD
        await self.cached_board.delete_many({})
        
        sorted_players = sorted(
            players_dict.values(),
            key=lambda x: len(x["props"]),
            reverse=True
        )
        
        for idx, player in enumerate(sorted_players):
            player["rank"] = idx + 1
        
        if sorted_players:
            await self.cached_board.insert_many(sorted_players)
        
        # Store sync metadata
        verified_count = sum(1 for p in sorted_players if p.get("is_verified"))
        mapper_matched = sum(1 for p in sorted_players if p.get("is_mapper_matched"))
        
        await self.sync_log.update_one(
            {"type": "cached_board"},
            {"$set": {
                "type": "cached_board",
                "synced_at": sync_time.isoformat(),
                "players_count": len(sorted_players),
                "verified_count": verified_count,
                "mapper_matched_count": mapper_matched,
                "unverified_count": len(sorted_players) - verified_count,
                "total_props": sum(len(p["props"]) for p in sorted_players),
                "lookup_method": "odds_api_mapper_v4"
            }},
            upsert=True
        )
        
        logger.info(f"[CACHED_BOARD] Built board: {len(sorted_players)} players ({mapper_matched} mapper-matched, {verified_count} verified)")
        
        # Build derived collections
        await self._build_derived_collections(players_dict, sync_time)
        
        return {
            "success": True,
            "players_count": len(sorted_players),
            "verified_count": verified_count,
            "mapper_matched_count": mapper_matched
        }
    
    async def build_cached_board_legacy(
        self,
        props: List[Dict],
        sync_time: datetime
    ) -> Dict[str, Any]:
        """
        LEGACY FALLBACK: Name-based lookup method.
        Used only if Odds API Mapper fails to initialize.
        """
        logger.warning("[CACHED_BOARD_LEGACY] Using legacy name-based lookup (Mapper unavailable)")
        
        # Load master roster into memory
        master_roster_map = {}
        roster_cursor = self.master_roster.find({}, {"_id": 0})
        async for player in roster_cursor:
            normalized = player.get("normalized_name", "").lower()
            if normalized:
                master_roster_map[normalized] = player
        
        logger.info(f"[CACHED_BOARD_LEGACY] Loaded {len(master_roster_map)} players from master roster")
        
        # Load player stats
        stats_map = await self._load_stats_map()
        signals_map = await self._load_signals_map()
        
        # Group props by player and enrich
        players_dict = {}
        unmatched_players = []
        
        for prop in props:
            player_name = prop.get("player_name", "Unknown")
            normalized_name = sanitize_player_name(player_name).lower()
            
            if player_name not in players_dict:
                roster_player = master_roster_map.get(normalized_name)
                
                # Try suffix variations
                if not roster_player:
                    for suffix in [" jr", " iii", " ii", " iv", " sr"]:
                        clean_name = normalized_name.replace(suffix, "").strip()
                        roster_player = master_roster_map.get(clean_name)
                        if roster_player:
                            break
                
                if not roster_player:
                    unmatched_players.append(player_name)
                    players_dict[player_name] = self._create_unmatched_player(
                        player_name, prop, sync_time
                    )
                else:
                    players_dict[player_name] = self._create_matched_player_legacy(
                        player_name, roster_player, stats_map, signals_map, normalized_name, sync_time
                    )
            
            # Add prop to player
            self._add_prop_to_player(players_dict[player_name], prop)
        
        if unmatched_players:
            logger.warning(f"[CACHED_BOARD_LEGACY] {len(unmatched_players)} players not in master roster: {unmatched_players[:5]}...")
        
        await self.cached_board.delete_many({})
        
        sorted_players = sorted(
            players_dict.values(),
            key=lambda x: len(x["props"]),
            reverse=True
        )
        
        for idx, player in enumerate(sorted_players):
            player["rank"] = idx + 1
        
        if sorted_players:
            await self.cached_board.insert_many(sorted_players)
        
        verified_count = sum(1 for p in sorted_players if p.get("is_verified"))
        await self.sync_log.update_one(
            {"type": "cached_board"},
            {"$set": {
                "type": "cached_board",
                "synced_at": sync_time.isoformat(),
                "players_count": len(sorted_players),
                "verified_count": verified_count,
                "unverified_count": len(sorted_players) - verified_count,
                "total_props": sum(len(p["props"]) for p in sorted_players),
                "lookup_method": "legacy_name_based"
            }},
            upsert=True
        )
        
        logger.info(f"[CACHED_BOARD_LEGACY] Built board: {len(sorted_players)} players ({verified_count} verified)")
        
        # Build derived collections
        await self._build_derived_collections(players_dict, sync_time)
        
        return {
            "success": True,
            "players_count": len(sorted_players),
            "verified_count": verified_count,
            "lookup_method": "legacy"
        }
    
    # ==================== PRIVATE HELPER METHODS ====================
    
    async def _load_stats_map(self) -> Dict[str, Any]:
        """Load player stats indexed by normalized name"""
        stats_map = {}
        stats_cursor = self.player_stats.find({}, {"_id": 0})
        async for stat in stats_cursor:
            player_name = stat.get("player_name", "")
            normalized = sanitize_player_name(player_name).lower()
            if normalized:
                stats_map[normalized] = stat
        logger.info(f"[CACHED_BOARD] Loaded {len(stats_map)} player stats")
        return stats_map
    
    async def _load_signals_map(self) -> Dict[str, Any]:
        """Load social signals indexed by player name"""
        signals_map = {}
        try:
            signals_cursor = self.db.dg_social_signals.find({}, {"_id": 0})
            async for signal in signals_cursor:
                player_name = signal.get("player_name", "")
                if player_name:
                    signals_map[player_name.lower()] = signal
        except Exception as e:
            logger.warning(f"[CACHED_BOARD] Could not load social signals: {e}")
        return signals_map
    
    async def _build_players_dict(
        self,
        props: List[Dict],
        player_data_map: Dict[str, Any],
        stats_map: Dict[str, Any],
        signals_map: Dict[str, Any],
        sync_time: datetime
    ) -> tuple:
        """Build players dictionary from props with mapper data"""
        players_dict = {}
        unmatched_players = []
        
        for prop in props:
            player_name = prop.get("player_name", "Unknown")
            
            if player_name not in players_dict:
                hub_player = player_data_map.get(player_name)
                
                if not hub_player:
                    unmatched_players.append(player_name)
                    players_dict[player_name] = self._create_unmatched_player(
                        player_name, prop, sync_time
                    )
                else:
                    players_dict[player_name] = self._create_matched_player(
                        player_name, hub_player, stats_map, signals_map, sync_time
                    )
            
            # Add prop to player
            self._add_prop_to_player(players_dict[player_name], prop)
        
        return players_dict, unmatched_players
    
    def _create_unmatched_player(
        self,
        player_name: str,
        prop: Dict,
        sync_time: datetime
    ) -> Dict[str, Any]:
        """Create player dict for unmatched player"""
        return {
            "player_name": player_name,
            "player_id": None,
            "tank01_player_id": None,
            "team": prop.get("home_team") or prop.get("away_team") or "UNK",
            "photo_url": None,
            "headshot_url": None,
            "nba_com_id": None,
            "espn_id": None,
            "position": None,
            "l10_stats": {},
            "is_verified": False,
            "is_mapper_matched": False,
            "props": [],
            "demons": [],
            "goblins": [],
            "synced_at": sync_time.isoformat()
        }
    
    def _create_matched_player(
        self,
        player_name: str,
        hub_player: Dict,
        stats_map: Dict[str, Any],
        signals_map: Dict[str, Any],
        sync_time: datetime
    ) -> Dict[str, Any]:
        """Create player dict from mapper/hub data"""
        player_id = hub_player.get("player_id")
        normalized_name = sanitize_player_name(player_name).lower()
        
        player_stats = stats_map.get(normalized_name, {})
        social = signals_map.get(player_name.lower(), {})
        
        hub_stats = hub_player.get("stats", {})
        season_avg = hub_stats.get("season_avg", {})
        
        return {
            # Primary identifiers
            "player_name": player_name,
            "player_id": player_id,
            "tank01_player_id": hub_player.get("tank01_id"),
            "nba_com_id": hub_player.get("nba_id"),
            "espn_id": hub_player.get("espn_id"),
            
            # Team info
            "team": hub_player.get("team"),
            "team_name": hub_player.get("team_name"),
            "team_logo_url": None,
            
            # Photo
            "photo_url": hub_player.get("headshot_url"),
            "headshot_url": hub_player.get("headshot_url"),
            "photo_source": "nba_master_hub_2026",
            
            # Player info
            "position": hub_player.get("position"),
            "jersey_number": hub_player.get("jersey"),
            
            # Stats
            "season_avg": season_avg,
            "games_played": season_avg.get("gp", player_stats.get("games_played", 0)),
            "l10_stats": {
                "pts": player_stats.get("pts_avg_l10", season_avg.get("pts", 0)),
                "reb": player_stats.get("reb_avg_l10", season_avg.get("reb", 0)),
                "ast": player_stats.get("ast_avg_l10", season_avg.get("ast", 0)),
                "pts_reb_ast": player_stats.get("pra_avg_l10", 0),
            },
            "l5_stats": {
                "pts": player_stats.get("pts_avg_l5", 0),
                "reb": player_stats.get("reb_avg_l5", 0),
                "ast": player_stats.get("ast_avg_l5", 0),
            },
            
            # Social signals
            "volatility_flag": social.get("volatility_flag", False),
            "revenge_game": social.get("revenge_game", False),
            "injury_status": hub_player.get("injury", {}).get("status") or social.get("injury_status"),
            
            # Verification
            "is_verified": True,
            "is_mapper_matched": True,
            
            # Props containers
            "props": [],
            "demons": [],
            "goblins": [],
            "synced_at": sync_time.isoformat()
        }
    
    def _create_matched_player_legacy(
        self,
        player_name: str,
        roster_player: Dict,
        stats_map: Dict[str, Any],
        signals_map: Dict[str, Any],
        normalized_name: str,
        sync_time: datetime
    ) -> Dict[str, Any]:
        """Create player dict from legacy roster data"""
        player_stats = stats_map.get(normalized_name, {})
        social = signals_map.get(player_name.lower(), {})
        
        return {
            "player_name": player_name,
            "tank01_player_id": roster_player.get("tank01_player_id"),
            "nba_com_id": roster_player.get("nba_com_id"),
            "espn_id": roster_player.get("espn_id"),
            "team": roster_player.get("team_abbreviation"),
            "team_name": roster_player.get("team_name"),
            "team_logo_url": roster_player.get("team_logo_url"),
            "photo_url": roster_player.get("photo_url"),
            "photo_source": roster_player.get("photo_source"),
            "position": roster_player.get("position"),
            "jersey_number": roster_player.get("jersey_number"),
            "games_played": player_stats.get("games_played", 0),
            "l10_stats": {
                "pts": player_stats.get("pts_avg_l10", 0),
                "reb": player_stats.get("reb_avg_l10", 0),
                "ast": player_stats.get("ast_avg_l10", 0),
                "pts_reb_ast": player_stats.get("pra_avg_l10", 0),
            },
            "l5_stats": {
                "pts": player_stats.get("pts_avg_l5", 0),
                "reb": player_stats.get("reb_avg_l5", 0),
                "ast": player_stats.get("ast_avg_l5", 0),
            },
            "volatility_flag": social.get("volatility_flag", False),
            "revenge_game": social.get("revenge_game", False),
            "injury_status": social.get("injury_status"),
            "is_verified": True,
            "props": [],
            "demons": [],
            "goblins": [],
            "synced_at": sync_time.isoformat()
        }
    
    def _add_prop_to_player(self, player: Dict, prop: Dict) -> None:
        """Add prop to player and calculate opponent"""
        player["props"].append(prop)
        
        if prop.get("is_demon"):
            player["demons"].append(prop)
        elif prop.get("is_goblin"):
            player["goblins"].append(prop)
        
        # Track standard props
        if not prop.get("is_demon") and not prop.get("is_goblin"):
            if "standard" not in player:
                player["standard"] = []
            player["standard"].append(prop)
        
        # Calculate opponent (do once per player)
        if not player.get("opponent"):
            home_team = prop.get("home_team")
            away_team = prop.get("away_team")
            player_team = player.get("team")
            
            if player_team and home_team and away_team:
                if player_team == home_team:
                    player["opponent"] = away_team
                    player["opponent_abbr"] = away_team
                elif player_team == away_team:
                    player["opponent"] = home_team
                    player["opponent_abbr"] = home_team
                else:
                    player["opponent"] = away_team if home_team else None
                    player["opponent_abbr"] = away_team if home_team else None
    
    async def _flag_unmatched_players(
        self,
        unmatched_players: List[str],
        sync_time: datetime
    ) -> None:
        """Flag unmatched players for manual review"""
        for player_name in unmatched_players[:20]:
            await self.flagged_players.update_one(
                {"player_name": player_name},
                {"$set": {
                    "player_name": player_name,
                    "flagged_at": sync_time.isoformat(),
                    "reason": "Not found in odds_api_mapping_master",
                    "source": "odds_api_v4"
                }},
                upsert=True
            )
    
    async def _build_derived_collections(
        self,
        players_dict: Dict[str, Dict],
        sync_time: datetime
    ) -> None:
        """Build derived collections (War Zone, Goblin Vault, etc.)"""
        await self.tier_builder_service.build_war_zone(players_dict, sync_time)
        await self.tier_builder_service.build_goblin_vault(players_dict, sync_time)
        await self.tier_builder_service.build_front_lines(players_dict, sync_time)
        await self.parlay_builder_service.build_parlay_builder(players_dict, sync_time)
        await self.parlay_builder_service.build_goblin_recon(players_dict, sync_time)

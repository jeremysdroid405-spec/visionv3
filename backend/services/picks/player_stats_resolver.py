"""
Player Stats Resolver
=====================
Service for resolving player stats from master hub and game logs.
Extracted from picks_getter_service.py for modularity.
"""

import logging
from typing import Dict, List, Optional, Any
from motor.motor_asyncio import AsyncIOMotorDatabase

from .game_utils import normalize_name, normalize_stat_key
from .hit_rate_service import HitRateCalculator

logger = logging.getLogger(__name__)


class PlayerStatsResolver:
    """
    Resolves player stats from master hub and game logs.
    
    Uses nba_master_hub_2026 as the primary source for:
    - Player identity
    - Baseline stats (season avg, L10, L5)
    - Game logs for hit rate calculations
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.master_hub = db.nba_master_hub_2026
        self.master_roster = db.dg_master_roster
        self._player_cache: Dict[str, Dict] = {}
    
    async def get_player(self, player_name: str) -> Optional[Dict]:
        """
        Get player data from master hub.
        
        Args:
            player_name: Player's name
            
        Returns:
            Player document or None
        """
        if not player_name:
            return None
        
        # Check cache
        cache_key = normalize_name(player_name)
        if cache_key in self._player_cache:
            return self._player_cache[cache_key]
        
        # Query master hub
        player = await self.master_hub.find_one(
            {"display_name": {"$regex": f"^{player_name}$", "$options": "i"}},
            {"_id": 0}
        )
        
        if player:
            self._player_cache[cache_key] = player
        
        return player
    
    async def get_player_by_id(self, player_id: int) -> Optional[Dict]:
        """Get player by BDL ID."""
        player = await self.master_hub.find_one(
            {"bdl_id": player_id},
            {"_id": 0}
        )
        return player
    
    async def get_game_logs(self, player_name: str, limit: int = 25) -> List[Dict]:
        """
        Get game logs for a player.
        
        Args:
            player_name: Player's name
            limit: Max number of games to return
            
        Returns:
            List of game logs (newest first)
        """
        player = await self.get_player(player_name)
        if not player:
            return []
        
        game_logs = player.get("bdl_game_logs", [])
        return game_logs[:limit] if game_logs else []
    
    def get_season_avg(self, player: Dict, stat_type: str) -> Optional[float]:
        """
        Get season average for a stat type from player data.
        
        Args:
            player: Player document
            stat_type: Stat type (PTS, REB, AST, etc.)
            
        Returns:
            Season average or None
        """
        if not player:
            return None
        
        baseline = player.get("baseline_stats", {})
        if not baseline:
            return None
        
        stat_upper = stat_type.upper()
        stat_key = normalize_stat_key(stat_type)
        
        # Try direct lookup in baseline_stats
        stat_data = baseline.get(stat_upper, {})
        if isinstance(stat_data, dict):
            szn_avg = stat_data.get("season_avg") or stat_data.get("szn_avg")
            if szn_avg is not None:
                return float(szn_avg)
        
        # Try szn_avg nested object
        szn_avg_obj = baseline.get("szn_avg", {})
        if isinstance(szn_avg_obj, dict):
            avg = szn_avg_obj.get(stat_key) or szn_avg_obj.get(stat_type.lower())
            if avg is not None:
                return float(avg)
        
        return None
    
    def get_l10_avg(self, player: Dict, stat_type: str) -> Optional[float]:
        """Get L10 average from player data."""
        if not player:
            return None
        
        baseline = player.get("baseline_stats", {})
        stat_key = normalize_stat_key(stat_type)
        
        # Check l10_avg object
        l10_obj = baseline.get("l10_avg", {})
        if isinstance(l10_obj, dict):
            avg = l10_obj.get(stat_key) or l10_obj.get(stat_type.lower())
            if avg is not None:
                return float(avg)
        
        # Check stat-specific data
        stat_data = baseline.get(stat_type.upper(), {})
        if isinstance(stat_data, dict):
            avg = stat_data.get("l10_avg")
            if avg is not None:
                return float(avg)
        
        return None
    
    def get_l5_avg(self, player: Dict, stat_type: str) -> Optional[float]:
        """Get L5 average from player data."""
        if not player:
            return None
        
        baseline = player.get("baseline_stats", {})
        stat_key = normalize_stat_key(stat_type)
        
        # Check l5_avg object
        l5_obj = baseline.get("l5_avg", {})
        if isinstance(l5_obj, dict):
            avg = l5_obj.get(stat_key) or l5_obj.get(stat_type.lower())
            if avg is not None:
                return float(avg)
        
        # Check stat-specific data
        stat_data = baseline.get(stat_type.upper(), {})
        if isinstance(stat_data, dict):
            avg = stat_data.get("l5_avg")
            if avg is not None:
                return float(avg)
        
        return None
    
    async def get_full_stats(
        self, 
        player_name: str, 
        stat_type: str, 
        line: float
    ) -> Dict[str, Any]:
        """
        Get complete stats for a player/stat/line combination.
        
        Args:
            player_name: Player's name
            stat_type: Stat type
            line: The betting line
            
        Returns:
            Complete stats dictionary
        """
        player = await self.get_player(player_name)
        if not player:
            return {"error": "player_not_found"}
        
        # Get season average from baseline
        season_avg = self.get_season_avg(player, stat_type)
        
        # Get game logs
        game_logs = player.get("bdl_game_logs", [])
        
        # Calculate stats using HitRateCalculator
        stats = HitRateCalculator.calculate_full_stats(
            game_logs=game_logs,
            stat_type=stat_type,
            line=line,
            season_avg=season_avg
        )
        
        # Add player context
        stats["player_name"] = player.get("display_name")
        stats["team"] = player.get("team_abbreviation") or player.get("team")
        stats["position"] = player.get("position")
        
        return stats
    
    async def enrich_pick_with_stats(self, pick: Dict) -> Dict:
        """
        Enrich a pick with full stats from master hub.
        
        Args:
            pick: Pick dictionary with player_name, stat_type, line
            
        Returns:
            Pick with stats added
        """
        player_name = pick.get("player_name")
        stat_type = pick.get("stat_type") or pick.get("stat_type_extracted")
        line = pick.get("line")
        
        if not all([player_name, stat_type, line]):
            return pick
        
        try:
            stats = await self.get_full_stats(player_name, stat_type, float(line))
            
            # Merge stats into pick
            pick["l5_avg"] = stats.get("l5_avg")
            pick["l10_avg"] = stats.get("l10_avg")
            pick["season_avg"] = stats.get("season_avg")
            pick["h5_rate"] = stats.get("h5_rate")
            pick["h10_rate"] = stats.get("h10_rate")
            pick["margin_season"] = stats.get("margin_season")
            pick["std_dev_l10"] = stats.get("std_dev_l10")
            
        except Exception as e:
            logger.warning(f"[STATS] Failed to enrich pick for {player_name}: {e}")
        
        return pick
    
    def clear_cache(self) -> None:
        """Clear the player cache."""
        self._player_cache = {}
        logger.info("[STATS] Player cache cleared")

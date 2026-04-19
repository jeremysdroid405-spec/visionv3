"""
SOCIAL SIGNAL ENGINE - News Sentiment & Revenge Game Detection
==============================================================
Detects:
1. Volatility flags (injuries, DNP, trades, suspensions, etc.)
2. Revenge games (player vs former team)

Uses:
- ESPN API for injury/news data
- Master Hub data for player history
- Cached board data for game matchups

Author: PickVision AI v3.2
"""

import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Optional

from services.config.collection_names import COLL

logger = logging.getLogger(__name__)

# Keyword Lists for Volatility Detection
REDUCED_USAGE_KEYWORDS = [
    "out", "rest", "dnp", "inactive", "sidelined", "minutes restriction",
    "load management", "injury", "questionable", "doubtful", "ruled out"
]

VOLATILITY_KEYWORDS = [
    "traded", "waived", "personal reasons", "divorce", "suspended", 
    "internal matter", "disciplinary", "fine", "benched", "demoted",
    "trade request", "unhappy", "frustrated"
]

# Keywords that indicate rumor/speculation (should be filtered out)
RUMOR_KEYWORDS = [
    "rumor", "speculation", "reportedly", "sources say", "allegedly",
    "could be", "might be", "may be", "unconfirmed"
]


class SocialSignalEngine:
    """
    Engine for detecting social signals that affect betting confidence.
    
    Uses ESPN + BDL for injury and news data.
    
    Features:
    1. News Sentiment Analysis - Detects volatility from injury reports
    2. Revenge Game Detection - Identifies players facing former teams
    """
    
    def __init__(self, db):
        self.db = db
        self.social_signals = db.dg_social_signals
        self.player_news_cache = db.dg_player_news_cache
        self.last_sync = None
    
    async def sync_social_signals(self, player_names: List[str] = None) -> Dict[str, Any]:
        """
        Main sync function - fetches news and detects revenge games.
        
        Uses master hub + ESPN for injury data.
        """
        sync_start = datetime.now(timezone.utc)
        logger.info("[SOCIAL SIGNAL] Starting social signal sync...")
        
        results = {
            "success": True,
            "volatility_flags": 0,
            "revenge_games": 0,
            "players_checked": 0,
            "errors": []
        }
        
        try:
            # Get players from cached board or master hub
            if player_names:
                players_to_check = player_names
            else:
                cursor = COLL.handle(self.db, "board_cache", "nba").find({}, {"player_name": 1})
                docs = await cursor.to_list(length=500)
                players_to_check = [d["player_name"] for d in docs]
            
            results["players_checked"] = len(players_to_check)
            
            # Check for volatility using injury data from master hub
            for player_name in players_to_check:
                try:
                    signal = await self._check_player_signals(player_name)
                    if signal:
                        if signal.get("volatility_flag"):
                            results["volatility_flags"] += 1
                        if signal.get("revenge_game"):
                            results["revenge_games"] += 1
                        
                        # Upsert signal to collection
                        await self.social_signals.update_one(
                            {"player_name": player_name},
                            {"$set": {
                                **signal,
                                "updated_at": datetime.now(timezone.utc).isoformat()
                            }},
                            upsert=True
                        )
                except Exception as e:
                    results["errors"].append(f"{player_name}: {str(e)}")
            
            self.last_sync = sync_start
            results["sync_duration"] = (datetime.now(timezone.utc) - sync_start).total_seconds()
            
        except Exception as e:
            logger.error(f"[SOCIAL SIGNAL] Sync error: {e}")
            results["success"] = False
            results["errors"].append(str(e))
        
        logger.info(f"[SOCIAL SIGNAL] Sync complete - {results['volatility_flags']} volatility, {results['revenge_games']} revenge")
        return results
    
    async def _check_player_signals(self, player_name: str) -> Optional[Dict[str, Any]]:
        """Check signals for a single player using master hub data."""
        signal = {
            "player_name": player_name,
            "volatility_flag": False,
            "volatility_reason": None,
            "revenge_game": False,
            "revenge_team": None,
            "injury_status": None
        }
        
        try:
            # Get player from master hub
            hub_player = await self.db[COLL("master_hub", "nba")].find_one({
                "display_name": player_name
            })
            
            if not hub_player:
                return signal
            
            # Check injury status from master hub
            injury = hub_player.get("injury", {})
            if injury:
                injury_status = injury.get("status", "").lower()
                if injury_status in ["out", "doubtful", "questionable"]:
                    signal["volatility_flag"] = True
                    signal["volatility_reason"] = f"Injury: {injury.get('description', injury_status)}"
                    signal["injury_status"] = injury_status
            
            # Check for revenge game
            # Get today's opponent from cached board
            board_player = await COLL.handle(self.db, "board_cache", "nba").find_one({
                "player_name": player_name
            })
            
            if board_player:
                opponent = board_player.get("opponent")
                previous_teams = hub_player.get("previous_teams", [])
                
                if opponent and opponent in previous_teams:
                    signal["revenge_game"] = True
                    signal["revenge_team"] = opponent
            
        except Exception as e:
            logger.warning(f"[SOCIAL SIGNAL] Error checking {player_name}: {e}")
        
        return signal
    
    async def get_player_signal(self, player_name: str) -> Optional[Dict[str, Any]]:
        """Get cached social signal for a player."""
        return await self.social_signals.find_one(
            {"player_name": player_name},
            {"_id": 0}
        )
    
    async def get_all_signals(self) -> List[Dict[str, Any]]:
        """Get all cached social signals."""
        cursor = self.social_signals.find({}, {"_id": 0})
        return await cursor.to_list(length=500)
    
    async def get_volatility_players(self) -> List[Dict[str, Any]]:
        """Get all players with active volatility flags."""
        cursor = self.social_signals.find(
            {"volatility_flag": True},
            {"_id": 0}
        )
        return await cursor.to_list(length=100)
    
    async def get_revenge_games(self) -> List[Dict[str, Any]]:
        """Get all players with active revenge game flags."""
        cursor = self.social_signals.find(
            {"revenge_game": True},
            {"_id": 0}
        )
        return await cursor.to_list(length=100)


# Singleton instance
_social_signal_engine: Optional[SocialSignalEngine] = None


def get_social_signal_engine(db) -> SocialSignalEngine:
    """Get or create the SocialSignalEngine singleton."""
    global _social_signal_engine
    if _social_signal_engine is None:
        _social_signal_engine = SocialSignalEngine(db)
    return _social_signal_engine

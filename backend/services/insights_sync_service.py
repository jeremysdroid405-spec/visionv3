"""
Insights Sync Service
=====================
Extracted from services.engines.demon_goblin_engine.py for modularity.

Handles:
- Daily insights synchronization
- Player insights calculation
- Advanced analytics storage
"""
from typing import Dict, List, Any, Optional, Tuple, TYPE_CHECKING
from datetime import datetime, timezone
import logging

from motor.motor_asyncio import AsyncIOMotorDatabase

if TYPE_CHECKING:
    from services.engines.demon_goblin_engine import DemonGoblinEngine

from services.config.collection_names import COLL

logger = logging.getLogger(__name__)


class InsightsSyncService:
    """
    Service for syncing and calculating player insights.
    BDL (nba_master_hub_2026) is the ONLY source for player stats.
    
    Requires engine reference to be set via set_engine() after initialization.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.cached_board = db[COLL("board_cache", "nba")]
        self.master_hub = db[COLL("master_hub", "nba")]  # BDL SSOT
        self.daily_insights = db.dg_daily_insights
        self._engine = None
    
    def set_engine(self, engine: "DemonGoblinEngine"):
        """Set engine reference for method delegation."""
        self._engine = engine
    
    async def sync_daily_insights(self) -> Dict[str, Any]:
        """
        Sync daily insights for all players with active props.
        Calculates advanced analytics using BDL data from nba_master_hub_2026.
        Should be run daily at 8:00 AM EST.
        """
        if not self._engine:
            raise RuntimeError("Engine not set. Call set_engine() first.")
        
        sync_start = datetime.now(timezone.utc)
        logger.info("[INSIGHTS SYNC] Starting daily insights calculation using BDL SSOT...")
        
        insights_calculated = 0
        errors = []
        
        try:
            # Get all players from cached board
            players = await self.cached_board.find({}, {"_id": 0}).to_list(None)
            
            if not players:
                return {"success": True, "insights_calculated": 0, "message": "No players to process"}
            
            logger.info(f"[INSIGHTS SYNC] Processing {len(players)} players...")
            
            for player in players:
                try:
                    player_name = player.get("player_name", "")
                    team = player.get("team", "")
                    
                    # Get opponent from props
                    opponent = ""
                    if player.get("props"):
                        first_prop = player["props"][0]
                        opponent = first_prop.get("opponent", first_prop.get("away_team", ""))
                    
                    # Get stats from BDL master hub
                    stats_doc = await self.master_hub.find_one(
                        {"display_name": player_name},
                        {"_id": 0, "bdl_game_logs": 1, "baseline_stats": 1}
                    )
                    
                    game_stats = []
                    if stats_doc:
                        game_stats = stats_doc.get("games", [])[:10]
                    
                    # Calculate insights
                    insights = await self.calculate_player_insights(
                        player_name=player_name,
                        team=team,
                        opponent=opponent,
                        game_stats=game_stats,
                        stat_type="pts"
                    )
                    
                    # Add metadata
                    insights["player_name"] = player_name
                    insights["team"] = team
                    insights["opponent"] = opponent
                    insights["synced_at"] = sync_start.isoformat()
                    
                    # Store in MongoDB
                    await self.daily_insights.update_one(
                        {"player_name": player_name},
                        {"$set": insights},
                        upsert=True
                    )
                    
                    insights_calculated += 1
                    
                except Exception as e:
                    errors.append(f"{player.get('player_name', 'Unknown')}: {str(e)}")
            
            # Create indexes
            await self.daily_insights.create_index("player_name", unique=True)
            await self.daily_insights.create_index("team")
            
            duration = (datetime.now(timezone.utc) - sync_start).total_seconds()
            logger.info(f"[INSIGHTS SYNC] Completed: {insights_calculated} players in {duration:.1f}s")
            
            return {
                "success": True,
                "insights_calculated": insights_calculated,
                "duration_seconds": duration,
                "errors": errors[:5],
                "synced_at": sync_start.isoformat()
            }
            
        except Exception as e:
            logger.error(f"[INSIGHTS SYNC] Failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "insights_calculated": insights_calculated
            }
    
    async def calculate_player_insights(
        self,
        player_name: str,
        team: str,
        opponent: str,
        game_stats: List[Dict],
        stat_type: str = "pts"
    ) -> Dict[str, Any]:
        """
        Calculate all advanced analytics for a player.
        """
        if not self._engine:
            raise RuntimeError("Engine not set. Call set_engine() first.")
        
        # Extract stat values
        stat_key_map = {
            "pts": "pts", "points": "pts",
            "reb": "reb", "rebounds": "reb",
            "ast": "ast", "assists": "ast",
            "fg3m": "fg3m", "3pm": "fg3m", "threes": "fg3m"
        }
        stat_key = stat_key_map.get(stat_type.lower(), "pts")
        
        recent_values = []
        for game in game_stats[:10]:
            val = game.get(stat_key, 0)
            if val is not None:
                recent_values.append(float(val))
        
        # Calculate volatility
        volatility, stddev = self._engine.calculate_volatility(recent_values)
        
        # Calculate pace factor
        pace_factor = self._engine.calculate_pace_factor(team, opponent) if opponent else 1.0
        
        # Get injured teammates (simplified)
        injured_teammates = []
        
        # Calculate usage bump
        usage_bump, injured_stars = self._engine.calculate_usage_bump(player_name, team, injured_teammates)
        
        # Schedule density (simplified)
        days_rest = 2
        is_b2b = False
        is_3in4 = False
        density_factor = 1.0
        
        # Generate summary
        summary = self._engine.generate_insight_summary(
            player_name=player_name,
            pace_factor=pace_factor,
            usage_bump=usage_bump,
            volatility=volatility,
            days_rest=days_rest,
            is_b2b=is_b2b,
            is_3in4=is_3in4,
            injured_teammates=injured_stars,
            opponent=opponent or "TBD"
        )
        
        # Calculate confidence
        confidence = self._engine.calculate_confidence_rating(density_factor, volatility, len(recent_values))
        
        return {
            "schedule_density_factor": density_factor,
            "pace_adjustment_factor": pace_factor,
            "usage_bump_percent": usage_bump,
            "volatility_score": volatility,
            "volatility_stddev": stddev,
            "insight_summary": summary,
            "ai_confidence_rating": confidence,
            "is_back_to_back": is_b2b,
            "is_three_in_four": is_3in4,
            "days_rest": days_rest,
            "injured_teammates": injured_stars
        }
    
    async def get_player_insights(self, player_name: str) -> Optional[Dict[str, Any]]:
        """Get cached insights for a player."""
        doc = await self.daily_insights.find_one(
            {"player_name": player_name},
            {"_id": 0}
        )
        return doc

"""
Picks Getter Service
====================
Extracted from demon_goblin_engine.py for modularity.

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

logger = logging.getLogger(__name__)


class PicksGetterService:
    """
    Service for fetching pre-computed picks from MongoDB.
    
    All data is PRE-ENRICHED during sync. These methods perform
    NO runtime calculations - just read and return.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        
        # Collection references
        self.radar_picks = db.dg_radar_picks
        self.goblin_vault = db.dg_goblin_vault
        self.front_lines = db.dg_front_lines
        self.parlay_builder = db.dg_parlay_builder
        self.goblin_recon = db.dg_goblin_recon
        self.cached_board = db.dg_cached_board
        self.player_data = db.dg_player_data
        self.daily_insights = db.dg_daily_insights
        self.sync_log = db.dg_sync_log
        self.events_cache = db.dg_events_cache
        self.odds_cache = db.dg_odds_cache
    
    async def get_war_zone(self) -> Dict[str, Any]:
        """
        Get the War Zone top 10 picks from MongoDB.
        Data is PRE-ENRICHED during sync. Just reads and returns with AI insights.
        """
        picks = await self.radar_picks.find({}, {"_id": 0}).sort("radar_score", -1).to_list(10)
        
        # Add AI insights
        for pick in picks:
            await self._add_insights_to_pick(pick)
        
        sync_meta = await self.sync_log.find_one({"type": "cached_board"})
        
        return {
            "success": True,
            "synced_at": sync_meta.get("synced_at") if sync_meta else None,
            "picks_count": len(picks),
            "picks": picks,
            "algorithm": {
                "description": "Weighted Probability + Line Gap",
                "formula": "Score = P / Gap_Ratio",
                "hit_probability": "(H10 × 0.6) + (H5 × 0.4)",
                "min_probability": "60%"
            }
        }
    
    async def get_goblin_vault(self) -> Dict[str, Any]:
        """
        Get the Goblin Vault top 10 safe plays from MongoDB.
        Data is PRE-ENRICHED during sync. Just reads and returns with AI insights.
        """
        picks = await self.goblin_vault.find({}, {"_id": 0}).sort("vault_score", -1).to_list(10)
        
        # Add AI insights
        for pick in picks:
            await self._add_insights_to_pick(pick)
        
        sync_meta = await self.sync_log.find_one({"type": "cached_board"})
        
        return {
            "success": True,
            "synced_at": sync_meta.get("synced_at") if sync_meta else None,
            "picks_count": len(picks),
            "picks": picks,
            "algorithm": {
                "name": "GOD-TIER 4-Pillar Formula",
                "description": "4-Pillar weighted scoring with 80% hit rate bouncer",
                "formula": "vault_score = (consistency * 0.50) + (vegas * 0.20) + (dvp * 0.15) + (context * 0.15)",
                "pillars": {
                    "pillar_1": "Base Consistency (50%) = (L10 * 0.6) + (L5 * 0.4)",
                    "pillar_2": "Vegas Implied Prob (20%) = odds conversion",
                    "pillar_3": "DvP Matchup (15%) = 0.5 neutral placeholder",
                    "pillar_4": "AI Context Shift (15%) = from AiContextEngine"
                },
                "hard_filter": "L10 Hit Frequency >= 80%"
            }
        }
    
    async def get_front_lines(self) -> Dict[str, Any]:
        """
        Get THE FRONT LINES - Mild Goblins + Mild Demons (5-18% gap from standard).
        Uses GOD-TIER 4-Pillar Formula. Data is PRE-ENRICHED and PRE-SHUFFLED.
        """
        picks = await self.front_lines.find({}, {"_id": 0}).to_list(10)
        
        # Add AI insights
        for pick in picks:
            await self._add_insights_to_pick(pick)
        
        sync_meta = await self.sync_log.find_one({"type": "cached_board"})
        
        # Count breakdown
        demon_count = len([p for p in picks if p.get("is_demon")])
        goblin_count = len([p for p in picks if p.get("is_goblin")])
        
        return {
            "success": True,
            "synced_at": sync_meta.get("synced_at") if sync_meta else None,
            "picks_count": len(picks),
            "demon_count": demon_count,
            "goblin_count": goblin_count,
            "picks": picks,
            "algorithm": {
                "name": "GOD-TIER 4-Pillar Formula (Mild Zone)",
                "description": "Same 4-Pillar scoring as Safe Haven, targeting mild alternates",
                "formula": "vault_score = (consistency * 0.50) + (vegas * 0.20) + (dvp * 0.15) + (context * 0.15)",
                "proximity_filter": {
                    "min_gap": "5% from standard",
                    "max_gap": "18% from standard",
                    "excluded": "Standard lines (0%) and extremes (>20%)"
                },
                "pillars": {
                    "pillar_1": "Base Consistency (50%)",
                    "pillar_2": "Vegas Implied Prob (20%)",
                    "pillar_3": "DvP Matchup (15%)",
                    "pillar_4": "AI Context Shift (15%)"
                },
                "ranking": "Bullet system (2-6 bullets based on rank)"
            }
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
        """
        # Try dg_cached_board first (has opponent data)
        player = await self.cached_board.find_one(
            {"player_name": player_name},
            {"_id": 0}
        )
        
        if player:
            self._clean_object_ids(player)
            await self._add_player_insights(player)
            return {"success": True, "player": player, "source": "cached_board"}
        
        # Try case-insensitive search in cached_board
        player = await self.cached_board.find_one(
            {"player_name": {"$regex": f"^{player_name}$", "$options": "i"}},
            {"_id": 0}
        )
        
        if player:
            self._clean_object_ids(player)
            await self._add_player_insights(player)
            return {"success": True, "player": player, "source": "cached_board"}
        
        # Fallback: Try player_data (exact match)
        player = await self.player_data.find_one(
            {"player_name": player_name},
            {"_id": 0}
        )
        
        if player:
            self._clean_object_ids(player)
            await self._add_player_insights(player)
            return {"success": True, "player": player, "source": "player_data"}
        
        # Try case-insensitive in player_data
        player = await self.player_data.find_one(
            {"player_name": {"$regex": f"^{player_name}$", "$options": "i"}},
            {"_id": 0}
        )
        
        if player:
            self._clean_object_ids(player)
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
            await self._add_player_insights(player)
            return {"success": True, "player": player, "matched_name": best_match, "source": match_source}
        
        return {
            "success": False,
            "message": "Lines loading... Player not in cache.",
            "player": None
        }
    
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
                    "team": pick.get("team", ""),
                    "photo_url": pick.get("photo_url", ""),
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
                    "team": pick.get("team", ""),
                    "photo_url": pick.get("photo_url", ""),
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
        """Add AI insights to a single pick."""
        player_name = pick.get('player_name')
        if not player_name:
            return
        
        # Get old insight_summary from daily_insights
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
        """Fetch and add insights data to a player dict."""
        if not player or not player.get("player_name"):
            return
        
        insights = await self.daily_insights.find_one(
            {"player_name": player["player_name"]},
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

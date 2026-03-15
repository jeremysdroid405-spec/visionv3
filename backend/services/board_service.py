"""
Board Service - Cached Board and Props Management
=================================================
High-level service for board and props operations.
Uses repository layer for data access.
"""
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
import logging

from repositories import RepositoryManager

logger = logging.getLogger(__name__)


class BoardService:
    """Service for managing cached board and props"""
    
    def __init__(self, repo: RepositoryManager):
        self.repo = repo
    
    # ==================== CACHED BOARD ====================
    
    async def get_cached_board(self) -> Dict[str, Any]:
        """Get full cached board for frontend"""
        players = await self.repo.board.get_cached_board()
        
        sync_status = await self.repo.sync.get_sync_status("cached_board")
        
        return {
            "success": True,
            "synced_at": sync_status.get("synced_at") if sync_status else None,
            "players_count": len(players),
            "players": players
        }
    
    async def get_player_details(self, player_name: str) -> Dict[str, Any]:
        """Get details for a specific player"""
        player = await self.repo.board.get_player_from_board(player_name)
        
        if not player:
            return {"success": False, "error": f"Player not found: {player_name}"}
        
        # Get additional insights
        insight = await self.repo.players.get_player_insights(player_name)
        if insight:
            player["insights"] = insight
        
        return {
            "success": True,
            "player": player
        }
    
    async def search_players(self, query: str, limit: int = 10) -> Dict[str, Any]:
        """Search players on board"""
        players = await self.repo.players.search_players(query, limit)
        
        return {
            "success": True,
            "query": query,
            "count": len(players),
            "players": players
        }
    
    async def get_players_by_team(self, team: str) -> Dict[str, Any]:
        """Get all players for a team"""
        players = await self.repo.board.get_board_by_team(team)
        
        return {
            "success": True,
            "team": team,
            "count": len(players),
            "players": players
        }
    
    # ==================== BOARD STATUS ====================
    
    async def get_board_status(self) -> Dict[str, Any]:
        """Get board status and statistics"""
        board_count = await self.repo.board.get_board_count()
        props_count = await self.repo.board.get_props_count()
        sync_status = await self.repo.sync.get_sync_status("cached_board")
        
        # Calculate time since last sync
        time_display = "Unknown"
        if sync_status and sync_status.get("synced_at"):
            synced_at = sync_status["synced_at"]
            if isinstance(synced_at, datetime):
                delta = datetime.now(timezone.utc) - synced_at
                hours = delta.seconds // 3600
                minutes = (delta.seconds % 3600) // 60
                time_display = f"Last Synced: {hours:02d}:{minutes:02d}"
        
        return {
            "success": True,
            "board_players": board_count,
            "live_props": props_count,
            "sync_status": sync_status.get("status") if sync_status else "unknown",
            "time_since_sync_display": time_display,
            "last_sync": sync_status.get("synced_at") if sync_status else None
        }
    
    # ==================== BOARD OPERATIONS ====================
    
    async def save_cached_board(self, players: List[Dict]) -> int:
        """Save cached board"""
        count = await self.repo.board.save_cached_board(players)
        
        # Update sync log
        await self.repo.sync.update_sync_status(
            "cached_board",
            status="success",
            details={"players_count": count}
        )
        
        return count
    
    async def clear_board(self) -> int:
        """Clear cached board"""
        return await self.repo.board.cached_board.delete_many()
    
    # ==================== PROPS BY GAME ====================
    
    async def get_props_grouped_by_game(self) -> Dict[str, Any]:
        """Get props grouped by game for parlay building"""
        props_by_game = await self.repo.board.get_props_by_game()
        
        return {
            "success": True,
            "games_count": len(props_by_game),
            "games": props_by_game
        }

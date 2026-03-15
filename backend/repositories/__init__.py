"""
Repositories Module - Database Abstraction Layer
=================================================
Provides clean separation between business logic and data access.

Usage:
    from repositories import RepositoryManager
    
    repo = RepositoryManager(db)
    picks = await repo.picks.get_war_zone_picks()
    player = await repo.players.get_player_team("LeBron James")
"""
from .base import BaseRepository
from .picks_repo import PicksRepository
from .board_repo import BoardRepository
from .player_repo import PlayerRepository
from .sync_repo import SyncRepository


class RepositoryManager:
    """
    Central manager for all repositories.
    
    Provides unified access to all data operations.
    """
    
    def __init__(self, db):
        """Initialize all repositories with database connection"""
        self.db = db
        
        # Initialize repositories
        self.picks = PicksRepository(db)
        self.board = BoardRepository(db)
        self.players = PlayerRepository(db)
        self.sync = SyncRepository(db)
    
    def clear_all_caches(self):
        """Clear caches across all repositories"""
        self.players.clear_cache()
    
    async def get_health_status(self) -> dict:
        """Get health status of all collections"""
        return {
            "picks": {
                "war_zone": await self.picks.get_war_zone_count(),
                "goblin_vault": await self.picks.get_goblin_vault_count(),
                "front_lines": await self.picks.get_front_lines_count()
            },
            "board": {
                "cached_players": await self.board.get_board_count(),
                "live_props": await self.board.get_props_count()
            },
            "players": {
                "roster_count": await self.players.get_roster_count(),
                "insights_count": await self.players.get_insights_count()
            },
            "sync": await self.sync.get_sync_summary()
        }


# Export for easy importing
__all__ = [
    'BaseRepository',
    'PicksRepository',
    'BoardRepository',
    'PlayerRepository',
    'SyncRepository',
    'RepositoryManager'
]

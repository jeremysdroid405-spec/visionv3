"""Live provider implementations — pass-through skeletons.

Phase 1 NOTE: these are skeletons. They define the methods but do NOT
yet replace any production read. Phase 2 will wire them into the actual
production functions.

The current behavior of these methods is to delegate to the same Mongo
queries production already performs. This guarantees that injecting
`LiveInputProvider()` into a refactored production function in Phase 2
produces byte-identical output to today's live behavior.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from services.replay.providers.base import (
    IInputProvider, IOddsProvider, IFeatureProvider, IStatcastProvider,
    ILineupProvider, PipelineMode,
)


class LiveOddsProvider(IOddsProvider):
    def __init__(self, db: AsyncIOMotorDatabase):
        self._db = db

    async def list_props(self, *, sport: str, game_date: str,
                          snapshot_iso: Optional[str] = None) -> List[Dict[str, Any]]:
        # Phase 2: read from `mlb_prop_scores` matching live `recompute_sport`
        raise NotImplementedError("LiveOddsProvider.list_props — Phase 2")

    async def list_events(self, *, sport: str, game_date: str) -> List[Dict[str, Any]]:
        raise NotImplementedError("LiveOddsProvider.list_events — Phase 2")


class LiveFeatureProvider(IFeatureProvider):
    def __init__(self, db: AsyncIOMotorDatabase):
        self._db = db

    async def get_player_features(self, *, player_name_normalized: str,
                                    stat_family: str,
                                    as_of_date: str) -> Optional[Dict[str, Any]]:
        # Phase 2: build feature row from master_hub bdl_game_logs[]
        # filtered to as_of_date.
        raise NotImplementedError("LiveFeatureProvider.get_player_features — Phase 2")

    async def get_game_logs(self, *, player_name_normalized: str,
                              as_of_date: str,
                              limit: int = 30) -> List[Dict[str, Any]]:
        raise NotImplementedError("LiveFeatureProvider.get_game_logs — Phase 2")


class LiveStatcastProvider(IStatcastProvider):
    def __init__(self, db: AsyncIOMotorDatabase):
        self._db = db

    async def get_batter_statcast(self, *, player_id: Any,
                                    as_of_date: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError("LiveStatcastProvider.get_batter_statcast — Phase 2")

    async def get_pitcher_statcast(self, *, player_id: Any,
                                     as_of_date: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError("LiveStatcastProvider.get_pitcher_statcast — Phase 2")


class LiveLineupProvider(ILineupProvider):
    def __init__(self, db: AsyncIOMotorDatabase):
        self._db = db

    async def get_opp_pitcher(self, *, event_id: str, as_of_date: str,
                                home_team: str, away_team: str,
                                is_away: bool) -> Optional[Dict[str, Any]]:
        # Phase 2: read from `mlb_live_lineup_feed`
        raise NotImplementedError("LiveLineupProvider.get_opp_pitcher — Phase 2")

    async def get_opposing_lineup(self, *, event_id: str, as_of_date: str,
                                    opp_team: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError("LiveLineupProvider.get_opposing_lineup — Phase 2")


def build_live_input_provider(db: AsyncIOMotorDatabase) -> IInputProvider:
    return IInputProvider(
        mode=PipelineMode.LIVE,
        odds=LiveOddsProvider(db),
        features=LiveFeatureProvider(db),
        statcast=LiveStatcastProvider(db),
        lineup=LiveLineupProvider(db),
        as_of_date=None, snapshot_iso=None,
    )

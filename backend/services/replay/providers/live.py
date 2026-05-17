"""Universal live providers — sport-agnostic via SportReplayAdapter.

Phase 1 NOTE: these are still skeletons. Phase 2 will wire them into
the actual production functions. Default `feature_provider=None` in
production callers preserves byte-identical live behavior today.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from services.replay.providers.base import (
    IInputProvider, IOddsProvider, IFeatureProvider, IStatcastProvider,
    ILineupProvider, PipelineMode,
)
from services.replay.providers.sport_adapter import SportReplayAdapter


class UniversalLiveOddsProvider(IOddsProvider):
    def __init__(self, db: AsyncIOMotorDatabase, *,
                 adapter: SportReplayAdapter):
        self._db = db
        self._adapter = adapter

    async def list_props(self, *, sport: str, game_date: str,
                          snapshot_iso: Optional[str] = None) -> List[Dict[str, Any]]:
        raise NotImplementedError("LiveOddsProvider.list_props — Phase 2c+")

    async def list_events(self, *, sport: str, game_date: str) -> List[Dict[str, Any]]:
        raise NotImplementedError("LiveOddsProvider.list_events — Phase 2c+")


class UniversalLiveFeatureProvider(IFeatureProvider):
    def __init__(self, db: AsyncIOMotorDatabase, *,
                 adapter: SportReplayAdapter):
        self._db = db
        self._adapter = adapter

    async def get_player_features(self, *, player_name_normalized: str,
                                    stat_family: str,
                                    as_of_date: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError("LiveFeatureProvider — Phase 2c+")

    async def get_game_logs(self, *, player_name_normalized: str,
                              as_of_date: str,
                              limit: int = 30) -> List[Dict[str, Any]]:
        raise NotImplementedError("LiveFeatureProvider — Phase 2c+")


class UniversalLiveStatcastProvider(IStatcastProvider):
    def __init__(self, db: AsyncIOMotorDatabase, *,
                 adapter: SportReplayAdapter):
        self._db = db
        self._adapter = adapter

    async def get_batter_statcast(self, *, player_id: Any,
                                    as_of_date: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError("LiveStatcastProvider — Phase 2c+")

    async def get_pitcher_statcast(self, *, player_id: Any,
                                     as_of_date: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError("LiveStatcastProvider — Phase 2c+")


class UniversalLiveLineupProvider(ILineupProvider):
    def __init__(self, db: AsyncIOMotorDatabase, *,
                 adapter: SportReplayAdapter):
        self._db = db
        self._adapter = adapter

    async def get_opp_pitcher(self, **kwargs: Any) -> Optional[Dict[str, Any]]:
        raise NotImplementedError("LiveLineupProvider — Phase 2c+")

    async def get_opposing_lineup(self, **kwargs: Any) -> Optional[Dict[str, Any]]:
        raise NotImplementedError("LiveLineupProvider — Phase 2c+")


def build_universal_live_provider(
    db: AsyncIOMotorDatabase, *,
    adapter: SportReplayAdapter,
) -> IInputProvider:
    return IInputProvider(
        mode=PipelineMode.LIVE,
        odds=UniversalLiveOddsProvider(db, adapter=adapter),
        features=UniversalLiveFeatureProvider(db, adapter=adapter),
        statcast=UniversalLiveStatcastProvider(db, adapter=adapter),
        lineup=UniversalLiveLineupProvider(db, adapter=adapter),
        as_of_date=None, snapshot_iso=None,
    )

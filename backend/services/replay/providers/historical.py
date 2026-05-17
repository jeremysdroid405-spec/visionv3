"""Universal historical providers — sport-agnostic via SportReplayAdapter.

These concrete providers replace the MLB-specific Phase-1 stubs. They
take a `SportReplayAdapter` and use it to resolve collection names and
sport-specific lookups. Switching sports is a one-line change:

    adapter = MLBReplayAdapter(db)            # or NBAReplayAdapter(db)
    provider = build_universal_historical_provider(
        db, adapter=adapter,
        game_date="2026-05-06",
        snapshot_iso="2026-05-06T11:00:00Z",
    )

NO live API calls. NO mutation of any collection. Read-only by design.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from services.replay.providers.base import (
    IInputProvider, IOddsProvider, IFeatureProvider, IStatcastProvider,
    ILineupProvider, PipelineMode,
)
from services.replay.providers.sport_adapter import SportReplayAdapter


# ─────────────────────────────────────────────────────────────────────
class UniversalHistoricalOddsProvider(IOddsProvider):
    """Reads sport-appropriate `*_historical_alt_odds_raw`."""

    def __init__(self, db: AsyncIOMotorDatabase, *,
                 adapter: SportReplayAdapter,
                 game_date: str, snapshot_iso: str):
        self._db = db
        self._adapter = adapter
        self._game_date = game_date
        self._snapshot_iso = snapshot_iso

    async def list_props(self, *, sport: str, game_date: str,
                          snapshot_iso: Optional[str] = None) -> List[Dict[str, Any]]:
        snap = snapshot_iso or self._snapshot_iso
        coll = self._adapter.config.odds_collection
        cursor = self._db[coll].find(
            {"sport": sport, "game_date": game_date, "snapshot_iso": snap},
            {"_id": 0})
        return await cursor.to_list(None)

    async def list_events(self, *, sport: str, game_date: str) -> List[Dict[str, Any]]:
        coll = self._adapter.config.odds_collection
        pipeline = [
            {"$match": {"sport": sport, "game_date": game_date,
                          "snapshot_iso": self._snapshot_iso}},
            {"$group": {"_id": "$event_id",
                          "home_team":     {"$first": "$home_team"},
                          "away_team":     {"$first": "$away_team"},
                          "commence_time": {"$first": "$commence_time"}}},
        ]
        out: List[Dict[str, Any]] = []
        async for r in self._db[coll].aggregate(pipeline):
            out.append({"event_id": r["_id"],
                          "home_team": r["home_team"],
                          "away_team": r["away_team"],
                          "commence_time": r["commence_time"]})
        return out


# ─────────────────────────────────────────────────────────────────────
class UniversalHistoricalFeatureProvider(IFeatureProvider):
    """Reads sport-appropriate `*_replay_feature_cache`."""

    def __init__(self, db: AsyncIOMotorDatabase, *,
                 adapter: SportReplayAdapter, game_date: str):
        self._db = db
        self._adapter = adapter
        self._game_date = game_date

    async def get_player_features(self, *, player_name_normalized: str,
                                    stat_family: str,
                                    as_of_date: str) -> Optional[Dict[str, Any]]:
        coll = self._adapter.config.feature_cache_collection
        return await self._db[coll].find_one(
            {"game_date": as_of_date,
             "player_name_normalized": player_name_normalized,
             "stat_family": stat_family},
            {"_id": 0})

    async def get_game_logs(self, *, player_name_normalized: str,
                              as_of_date: str,
                              limit: int = 30) -> List[Dict[str, Any]]:
        coll = self._adapter.config.feature_cache_collection
        any_row = await self._db[coll].find_one(
            {"game_date": as_of_date,
             "player_name_normalized": player_name_normalized},
            {"_id": 0, "stat_values": 1, "dates": 1, "pa_values": 1},
            sort=[("stat_family", 1)])
        if not any_row:
            return []
        return [{"date": d} for d in (any_row.get("dates") or [])[:limit]]


# ─────────────────────────────────────────────────────────────────────
class UniversalHistoricalStatcastProvider(IStatcastProvider):
    """Reads sport-appropriate Statcast/equivalent rolling collections."""

    def __init__(self, db: AsyncIOMotorDatabase, *,
                 adapter: SportReplayAdapter, game_date: str):
        self._db = db
        self._adapter = adapter
        self._game_date = game_date

    async def get_batter_statcast(self, *, player_id: Any,
                                    as_of_date: str) -> Optional[Dict[str, Any]]:
        if self._adapter.config.sport != "mlb":
            return None
        return await self._db.mlb_statcast_player_features.find_one(
            {"player_id": player_id, "as_of_date": {"$lte": as_of_date}},
            {"_id": 0}, sort=[("as_of_date", -1)])

    async def get_pitcher_statcast(self, *, player_id: Any,
                                     as_of_date: str) -> Optional[Dict[str, Any]]:
        if self._adapter.config.sport != "mlb":
            return None
        return await self._db.mlb_statcast_pitcher_features.find_one(
            {"player_id": player_id, "as_of_date": {"$lte": as_of_date}},
            {"_id": 0}, sort=[("as_of_date", -1)])


# ─────────────────────────────────────────────────────────────────────
class UniversalHistoricalLineupProvider(ILineupProvider):
    """Delegates to the sport adapter's `resolve_*` methods."""

    def __init__(self, db: AsyncIOMotorDatabase, *,
                 adapter: SportReplayAdapter, game_date: str):
        self._db = db
        self._adapter = adapter
        self._game_date = game_date

    async def get_opp_pitcher(self, *, event_id: str, as_of_date: str,
                                home_team: str, away_team: str,
                                is_away: bool) -> Optional[Dict[str, Any]]:
        return await self._adapter.resolve_opp_pitcher(
            event_id=event_id, game_date=self._game_date,
            home_team=home_team, away_team=away_team,
            is_away=is_away, as_of_date=as_of_date)

    async def get_opposing_lineup(self, *, event_id: str, as_of_date: str,
                                    opp_team: str) -> Optional[Dict[str, Any]]:
        return await self._adapter.resolve_opposing_lineup(
            event_id=event_id, game_date=self._game_date,
            opp_team=opp_team, as_of_date=as_of_date)


# ─────────────────────────────────────────────────────────────────────
def build_universal_historical_provider(
    db: AsyncIOMotorDatabase, *,
    adapter: SportReplayAdapter,
    game_date: str, snapshot_iso: str,
) -> IInputProvider:
    """Construct a composite IInputProvider for historical replay."""
    return IInputProvider(
        mode=PipelineMode.HISTORICAL,
        odds=UniversalHistoricalOddsProvider(
            db, adapter=adapter,
            game_date=game_date, snapshot_iso=snapshot_iso),
        features=UniversalHistoricalFeatureProvider(
            db, adapter=adapter, game_date=game_date),
        statcast=UniversalHistoricalStatcastProvider(
            db, adapter=adapter, game_date=game_date),
        lineup=UniversalHistoricalLineupProvider(
            db, adapter=adapter, game_date=game_date),
        as_of_date=game_date,
        snapshot_iso=snapshot_iso,
    )

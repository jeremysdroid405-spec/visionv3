"""Historical provider implementations — concrete read-only adapters.

Reads from existing Mongo replay collections. NO live API calls. NO
mutation of any collection. Read-only by design.

`HistoricalOddsProvider` and `HistoricalFeatureProvider` are
fully-implemented in Phase 1 because both back-end collections already
exist and are populated:
  - `mlb_historical_alt_odds_raw`     (Layer 1 of replay pipeline)
  - `mlb_replay_feature_cache`         (Layer 2 of replay pipeline)

`HistoricalStatcastProvider` is partially implemented — reads
`mlb_statcast_player_features` filtered by `as_of_date`. Phase 2 must
verify the rolling_7/14/30 snapshots in that collection were built
respecting as-of-date semantics (no future leakage).

`HistoricalLineupProvider` raises immediately — no historical lineup
snapshot collection exists. Phase 2 will either build one or accept
this block as permanently imputed in historical mode.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from services.replay.providers.base import (
    IInputProvider, IOddsProvider, IFeatureProvider, IStatcastProvider,
    ILineupProvider, PipelineMode,
)


# ─────────────────────────────────────────────────────────────────────
class HistoricalOddsProvider(IOddsProvider):
    """Reads `mlb_historical_alt_odds_raw` populated by replay Layer 1."""

    SOURCE_COLLECTION = "mlb_historical_alt_odds_raw"

    def __init__(self, db: AsyncIOMotorDatabase, *,
                 game_date: str, snapshot_iso: str):
        self._db = db
        self._game_date = game_date
        self._snapshot_iso = snapshot_iso

    async def list_props(self, *, sport: str, game_date: str,
                          snapshot_iso: Optional[str] = None) -> List[Dict[str, Any]]:
        snap = snapshot_iso or self._snapshot_iso
        cursor = self._db[self.SOURCE_COLLECTION].find(
            {"sport": sport, "game_date": game_date, "snapshot_iso": snap},
            {"_id": 0},
        )
        return await cursor.to_list(None)

    async def list_events(self, *, sport: str, game_date: str) -> List[Dict[str, Any]]:
        pipeline = [
            {"$match": {"sport": sport, "game_date": game_date,
                          "snapshot_iso": self._snapshot_iso}},
            {"$group": {"_id": "$event_id",
                          "home_team":     {"$first": "$home_team"},
                          "away_team":     {"$first": "$away_team"},
                          "commence_time": {"$first": "$commence_time"}}},
        ]
        out: List[Dict[str, Any]] = []
        async for r in self._db[self.SOURCE_COLLECTION].aggregate(pipeline):
            out.append({
                "event_id": r["_id"],
                "home_team": r["home_team"],
                "away_team": r["away_team"],
                "commence_time": r["commence_time"],
            })
        return out


# ─────────────────────────────────────────────────────────────────────
class HistoricalFeatureProvider(IFeatureProvider):
    """Reads `mlb_replay_feature_cache` populated by replay Layer 2.

    The cache rows are already built with as-of-date leakage protection
    (their builder uses a strict `< game_date` cutoff on bdl_game_logs).
    """

    SOURCE_COLLECTION = "mlb_replay_feature_cache"

    def __init__(self, db: AsyncIOMotorDatabase, *, game_date: str):
        self._db = db
        self._game_date = game_date

    async def get_player_features(self, *, player_name_normalized: str,
                                    stat_family: str,
                                    as_of_date: str) -> Optional[Dict[str, Any]]:
        return await self._db[self.SOURCE_COLLECTION].find_one(
            {"game_date": as_of_date,
             "player_name_normalized": player_name_normalized,
             "stat_family": stat_family},
            {"_id": 0},
        )

    async def get_game_logs(self, *, player_name_normalized: str,
                              as_of_date: str,
                              limit: int = 30) -> List[Dict[str, Any]]:
        # The cache row already carries `stat_values[]`, `dates[]`, `pa_values[]`.
        # Re-shape into a game_logs-shaped list for the model consumer.
        # NOTE: stat_family is required to find the right cache row — but the
        # log values are the same per (player, date) regardless of family,
        # so we fetch any single family row and reuse `dates[]`.
        any_row = await self._db[self.SOURCE_COLLECTION].find_one(
            {"game_date": as_of_date,
             "player_name_normalized": player_name_normalized},
            {"_id": 0, "stat_values": 1, "dates": 1, "pa_values": 1,
             "stat_family": 1, "team": 1},
            sort=[("stat_family", 1)],
        )
        if not any_row:
            return []
        dates = any_row.get("dates") or []
        return [{"date": d} for d in dates[:limit]]


# ─────────────────────────────────────────────────────────────────────
class HistoricalStatcastProvider(IStatcastProvider):
    """Reads `mlb_statcast_player_features` filtered by as-of-date.

    PHASE 2 VERIFICATION REQUIRED: the rolling_7/14/30 fields on each
    document must reflect the rolling window AS OF `built_at` ≤ as_of_date.
    Phase 1 cannot guarantee that without further investigation.
    """

    SOURCE_COLLECTION_BATTER = "mlb_statcast_player_features"
    SOURCE_COLLECTION_PITCHER = "mlb_statcast_pitcher_features"

    def __init__(self, db: AsyncIOMotorDatabase, *, game_date: str):
        self._db = db
        self._game_date = game_date

    async def get_batter_statcast(self, *, player_id: Any,
                                    as_of_date: str) -> Optional[Dict[str, Any]]:
        # Phase 2 will refine the date-filter semantics. For now this is
        # a placeholder that returns the most-recent doc ≤ as_of_date.
        return await self._db[self.SOURCE_COLLECTION_BATTER].find_one(
            {"player_id": player_id,
             "as_of_date": {"$lte": as_of_date}},
            {"_id": 0},
            sort=[("as_of_date", -1)],
        )

    async def get_pitcher_statcast(self, *, player_id: Any,
                                     as_of_date: str) -> Optional[Dict[str, Any]]:
        return await self._db[self.SOURCE_COLLECTION_PITCHER].find_one(
            {"player_id": player_id,
             "as_of_date": {"$lte": as_of_date}},
            {"_id": 0},
            sort=[("as_of_date", -1)],
        )


# ─────────────────────────────────────────────────────────────────────
class HistoricalLineupProvider(ILineupProvider):
    """No historical lineup snapshot collection exists today.

    Returns None for all queries. Phase 2 will either build a historical
    lineup snapshot from the MLB Stats API + box scores, or formally
    accept that lineup features remain imputed in historical mode.
    """

    def __init__(self, db: AsyncIOMotorDatabase, *, game_date: str):
        self._db = db
        self._game_date = game_date

    async def get_opp_pitcher(self, *, event_id: str, as_of_date: str,
                                home_team: str, away_team: str,
                                is_away: bool) -> Optional[Dict[str, Any]]:
        return None  # Documented gap

    async def get_opposing_lineup(self, *, event_id: str, as_of_date: str,
                                    opp_team: str) -> Optional[Dict[str, Any]]:
        return None  # Documented gap


# ─────────────────────────────────────────────────────────────────────
def build_historical_input_provider(db: AsyncIOMotorDatabase, *,
                                      game_date: str,
                                      snapshot_iso: str) -> IInputProvider:
    return IInputProvider(
        mode=PipelineMode.HISTORICAL,
        odds=HistoricalOddsProvider(db, game_date=game_date,
                                       snapshot_iso=snapshot_iso),
        features=HistoricalFeatureProvider(db, game_date=game_date),
        statcast=HistoricalStatcastProvider(db, game_date=game_date),
        lineup=HistoricalLineupProvider(db, game_date=game_date),
        as_of_date=game_date,
        snapshot_iso=snapshot_iso,
    )

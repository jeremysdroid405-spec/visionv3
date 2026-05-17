"""Abstract provider interfaces for the production replay harness.

These interfaces are the seam Phase 2 will plumb through:
    `predict(..., input_provider: IInputProvider = LiveInputProvider())`

Phase 1 defines them; no production caller yet references them.
"""
from __future__ import annotations
import abc
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol


class PipelineMode(str, Enum):
    LIVE = "live"
    HISTORICAL = "historical"


# ─────────────────────────────────────────────────────────────────────
# Odds provider — replaces the live `mlb_prop_scores` reads
# ─────────────────────────────────────────────────────────────────────
class IOddsProvider(abc.ABC):
    """Yields prop rows (one per event × market × line × side × book).

    In LIVE mode this delegates to the live `mlb_prop_scores` collection
    that `universal_odds_sync` populates. In HISTORICAL mode this reads
    from `mlb_historical_alt_odds_raw`.
    """

    @abc.abstractmethod
    async def list_props(self, *, sport: str, game_date: str,
                          snapshot_iso: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return all prop rows for a single date×snapshot."""
        ...

    @abc.abstractmethod
    async def list_events(self, *, sport: str, game_date: str) -> List[Dict[str, Any]]:
        """Return event metadata (event_id, home, away, commence_time)."""
        ...


# ─────────────────────────────────────────────────────────────────────
# Feature provider — replaces live `bdl_game_logs` reads in `predict()`
# ─────────────────────────────────────────────────────────────────────
class IFeatureProvider(abc.ABC):
    """Yields rolling-window stat features for a player as-of a date.

    In LIVE mode this reads `mlb_master_hub_2026.bdl_game_logs[]` directly
    (which by definition contains everything up to NOW). In HISTORICAL
    mode this reads pre-built `mlb_replay_feature_cache` rows that already
    enforce as-of-date leakage protection.
    """

    @abc.abstractmethod
    async def get_player_features(self, *, player_name_normalized: str,
                                    stat_family: str,
                                    as_of_date: str) -> Optional[Dict[str, Any]]:
        """Return a feature_cache-shaped dict or None if not available."""
        ...

    @abc.abstractmethod
    async def get_game_logs(self, *, player_name_normalized: str,
                              as_of_date: str,
                              limit: int = 30) -> List[Dict[str, Any]]:
        """Return recent game logs strictly BEFORE as_of_date."""
        ...


# ─────────────────────────────────────────────────────────────────────
# Statcast provider — for sc_b_* / sc_p_* feature blocks
# ─────────────────────────────────────────────────────────────────────
class IStatcastProvider(abc.ABC):
    @abc.abstractmethod
    async def get_batter_statcast(self, *, player_id: Any,
                                    as_of_date: str) -> Optional[Dict[str, Any]]:
        """Return rolling_7/14/30/season Statcast bundle as-of date."""
        ...

    @abc.abstractmethod
    async def get_pitcher_statcast(self, *, player_id: Any,
                                     as_of_date: str) -> Optional[Dict[str, Any]]:
        ...


# ─────────────────────────────────────────────────────────────────────
# Lineup provider — known gap; historical lineup snapshots do not exist
# ─────────────────────────────────────────────────────────────────────
class ILineupProvider(abc.ABC):
    @abc.abstractmethod
    async def get_opp_pitcher(self, *, event_id: str, as_of_date: str,
                                home_team: str, away_team: str,
                                is_away: bool) -> Optional[Dict[str, Any]]:
        ...

    @abc.abstractmethod
    async def get_opposing_lineup(self, *, event_id: str, as_of_date: str,
                                    opp_team: str) -> Optional[Dict[str, Any]]:
        ...


# ─────────────────────────────────────────────────────────────────────
# Composite input provider — the single seam injected into production
# ─────────────────────────────────────────────────────────────────────
@dataclass
class IInputProvider:
    """Bundle of providers passed through the pipeline.

    Holding a single composite reduces the kwarg explosion across
    production function signatures. Phase 2 refactors will look like:

        async def predict(..., input_provider: IInputProvider = None):
            input_provider = input_provider or get_default_live_provider()
            features = await input_provider.features.get_player_features(...)
    """
    mode: PipelineMode
    odds: IOddsProvider
    features: IFeatureProvider
    statcast: IStatcastProvider
    lineup: ILineupProvider
    # Identity fields — used to derive replay serials + audit pins
    as_of_date: Optional[str] = None
    snapshot_iso: Optional[str] = None

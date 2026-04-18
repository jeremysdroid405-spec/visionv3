"""Sport registry — add a new sport by adding a line below."""
from __future__ import annotations
from typing import Dict, List

from services.board.adapters.base import SportBoardAdapter
from services.board.adapters.nba import NBABoardAdapter
from services.board.adapters.mlb import MLBBoardAdapter


class UnknownSportError(KeyError):
    """Raised when a sport is requested that is not registered."""


# ONE registry. Adding a new sport = one import + one line.
REGISTRY: Dict[str, SportBoardAdapter] = {
    "nba": NBABoardAdapter(),
    "mlb": MLBBoardAdapter(),
}


def get_adapter(sport: str) -> SportBoardAdapter:
    key = (sport or "").strip().lower()
    if key not in REGISTRY:
        raise UnknownSportError(sport)
    return REGISTRY[key]


def registered_sports() -> List[str]:
    return list(REGISTRY.keys())


__all__ = [
    "REGISTRY",
    "UnknownSportError",
    "SportBoardAdapter",
    "get_adapter",
    "registered_sports",
]

"""
Sport calibration tables — registers every sport into the singleton
distribution registry on import.

Adding a new sport
------------------
1. Create a `calibration/{sport}.py` exposing `{SPORT}_FAMILIES`.
2. Import + register here.
"""
from __future__ import annotations

from ..registry import get_registry
from .mlb import MLB_FAMILIES
from .nba import NBA_FAMILIES
from .nfl import NFL_FAMILIES


_registry = get_registry()
_registry.register_sport("mlb", MLB_FAMILIES)
_registry.register_sport("nba", NBA_FAMILIES)
_registry.register_sport("nfl", NFL_FAMILIES)


__all__: list[str] = []

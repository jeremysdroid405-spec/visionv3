"""MLB Production Replay Harness — Provider package.

Phase 1 scaffolding only. NO production code is modified by this package.

Provider pattern:
    Production functions in Phase 2 will accept an `input_provider: IInputProvider`
    kwarg with `LiveInputProvider()` as the default. Replay paths inject
    `HistoricalInputProvider(game_date=..., snapshot_iso=...)` instead.

Currently exported skeletons only — all read methods raise NotImplementedError
unless explicitly implemented in `historical.py`.
"""
from services.replay.providers.base import (
    IInputProvider, IOddsProvider, IFeatureProvider, IStatcastProvider,
    ILineupProvider, PipelineMode,
)

__all__ = [
    "IInputProvider", "IOddsProvider", "IFeatureProvider",
    "IStatcastProvider", "ILineupProvider", "PipelineMode",
]

"""Universal Replay Provider package.

Phase 1+2 scaffolding for a sport-agnostic historical-replay harness.

Public API:

    from services.replay.providers import (
        # Composite + interface types
        IInputProvider, PipelineMode,
        # Sport adapter base + concrete adapters
        SportReplayAdapter, MLBReplayAdapter,
        NBAReplayAdapter, NFLReplayAdapter,
        # Composite provider factory
        build_universal_historical_provider,
        build_universal_live_provider,
        # Audit helpers
        compute_production_pipeline_version,
        snapshot_input_collection_versions,
        next_replay_serial,
        runs_collection_name, outputs_collection_name, cards_collection_name,
    )

Design summary:
  - One `SportReplayAdapter` subclass per sport (MLB built; NBA/NFL skeleton)
  - Universal *Provider classes consume a SportReplayAdapter so they are sport-agnostic
  - The harness orchestrator (Phase 2c) only ever sees the abstract interface
"""
from services.replay.providers.base import (
    IInputProvider, IOddsProvider, IFeatureProvider, IStatcastProvider,
    ILineupProvider, PipelineMode,
)
from services.replay.providers.sport_adapter import (
    SportReplayAdapter, SportFixedConfig,
)
from services.replay.providers.mlb_adapter import MLBReplayAdapter
from services.replay.providers.nba_adapter import NBAReplayAdapter
from services.replay.providers.nfl_adapter import NFLReplayAdapter

from services.replay.providers.historical import (
    UniversalHistoricalOddsProvider,
    UniversalHistoricalFeatureProvider,
    UniversalHistoricalStatcastProvider,
    UniversalHistoricalLineupProvider,
    build_universal_historical_provider,
)
from services.replay.providers.live import (
    UniversalLiveOddsProvider,
    UniversalLiveFeatureProvider,
    UniversalLiveStatcastProvider,
    UniversalLiveLineupProvider,
    build_universal_live_provider,
)
from services.replay.providers.audit import (
    compute_production_pipeline_version,
    snapshot_input_collection_versions,
    next_replay_serial,
    git_commit_sha,
    utc_now,
    runs_collection_name,
    outputs_collection_name,
    cards_collection_name,
    serial_counter_collection_name,
)

__all__ = [
    # Interfaces
    "IInputProvider", "IOddsProvider", "IFeatureProvider",
    "IStatcastProvider", "ILineupProvider", "PipelineMode",
    # Sport adapter base + concretes
    "SportReplayAdapter", "SportFixedConfig",
    "MLBReplayAdapter", "NBAReplayAdapter", "NFLReplayAdapter",
    # Universal providers
    "UniversalHistoricalOddsProvider", "UniversalHistoricalFeatureProvider",
    "UniversalHistoricalStatcastProvider", "UniversalHistoricalLineupProvider",
    "UniversalLiveOddsProvider", "UniversalLiveFeatureProvider",
    "UniversalLiveStatcastProvider", "UniversalLiveLineupProvider",
    "build_universal_historical_provider", "build_universal_live_provider",
    # Audit helpers
    "compute_production_pipeline_version",
    "snapshot_input_collection_versions",
    "next_replay_serial", "git_commit_sha", "utc_now",
    "runs_collection_name", "outputs_collection_name",
    "cards_collection_name", "serial_counter_collection_name",
]

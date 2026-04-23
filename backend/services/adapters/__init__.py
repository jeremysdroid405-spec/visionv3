"""Sport adapters package.

All legacy sport adapters (NBAAdapter, MLBAdapter) were deleted as
part of the 2026-04-22 HARD CONSOLIDATION. The universal master sync
path (`services.master_sync.run_master_sync`) + the scoring adapters
under `services.scoring.adapters` are the only sport-specific code
paths remaining.
"""

__all__: list[str] = []

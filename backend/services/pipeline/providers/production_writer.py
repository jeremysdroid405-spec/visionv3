"""ProductionOutputWriter — routes pipeline run outputs to production-replay
collections (back-compat with the existing replay path).

NOTE: This writer is the BACK-COMPAT shim for Phase B. Live mode
(`mode="live"` + `output_namespace="production"`) is NOT wired
yet — that wiring lives in Phase C / D when we replace the
live `recompute_sport` callers. For now `ProductionOutputWriter`
exists as the descriptor + namespace for `mode="historical"` +
`output_namespace="production"` runs, which mirror the current
`MLB-PRODREPLAY-…` replay artifacts.

Writes to:
    {sport}_production_replay_runs
    {sport}_production_replay_outputs
    {sport}_production_replay_cards
"""
from __future__ import annotations

from typing import Any, Dict

from services.pipeline.providers.base import IOutputWriter


class ProductionOutputWriter(IOutputWriter):
    """Production-namespace writer (back-compat with replay path)."""
    output_namespace = "production_replay"

    def __init__(self):
        self.name = "ProductionOutputWriter"

    def describe(self) -> Dict[str, Any]:
        return {
            "writer": self.name,
            "output_namespace": self.output_namespace,
            "writes_to": [
                "{sport}_production_replay_runs",
                "{sport}_production_replay_outputs",
                "{sport}_production_replay_cards",
            ],
        }


__all__ = ["ProductionOutputWriter"]

"""TestOutputWriter — routes pipeline run outputs to test collections.

The writer itself does not call any persistence APIs directly —
persistence is done inside `services.replay.production_replay_runner.
run_production_replay`, which now accepts an `output_namespace`
parameter (Phase B). This class is the descriptor + namespace
selector the runner passes through to that function.

Writes to:
    {sport}_test_runs
    {sport}_test_outputs
    {sport}_test_cards

Index names are derived from the namespace so they don't collide
with the production_replay indexes.
"""
from __future__ import annotations

from typing import Any, Dict

from services.pipeline.providers.base import IOutputWriter


class TestOutputWriter(IOutputWriter):
    """Test-namespace writer."""
    output_namespace = "test"

    def __init__(self):
        self.name = "TestOutputWriter"

    def describe(self) -> Dict[str, Any]:
        return {
            "writer": self.name,
            "output_namespace": self.output_namespace,
            "writes_to": [
                "{sport}_test_runs",
                "{sport}_test_outputs",
                "{sport}_test_cards",
            ],
        }


__all__ = ["TestOutputWriter"]

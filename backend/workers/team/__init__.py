"""
Team-side workers (Phase 1.A.2 skeletons).

All three workers are SPORT-AGNOSTIC: the `sport` argument is passed
to the constructor. Real ingest dispatch is double-gated:

    1. `SGO_API_KEY` env var present, AND
    2. `TEAM_INGEST_ENABLED=1` env var present.

If either guard is missing the workers raise `TeamIngestDisabled`
on any method that would call SGO or write to the live ingest path.

Every worker exposes:
    - `requires_sgo_key()` classmethod → True
    - `dispatch_guard_ok()` classmethod → (ok, reasons)
    - `probe()` — read-only, returns the planned shape only
    - `dry_run_*()` — enumerates the work a real run would do
      without touching the network or any Mongo collection

NOTHING in this package may write to a player-side collection
(`sgo_*`, `mlb_*`, `nba_*`, `nfl_*`, `pp_*`). The regression test
`tests/test_team_workers_skeletons.py::test_workers_never_touch_*`
locks this in.
"""
from .base import (
    SUPPORTED_SPORTS,
    TeamIngestDisabled,
    TeamWorkerBase,
    dispatch_guard_ok,
    requires_sgo_key,
)
from .team_matchups_ingest import TeamMatchupsIngestWorker
from .team_odds_ingest import TeamOddsIngestWorker
from .team_outcomes_grader import TeamOutcomesGrader

__all__ = [
    "SUPPORTED_SPORTS",
    "TeamIngestDisabled",
    "TeamWorkerBase",
    "TeamOddsIngestWorker",
    "TeamOutcomesGrader",
    "TeamMatchupsIngestWorker",
    "dispatch_guard_ok",
    "requires_sgo_key",
]

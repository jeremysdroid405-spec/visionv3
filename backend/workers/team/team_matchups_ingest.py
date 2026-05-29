"""
Team matchups ingest worker — Phase 1.A.2 SKELETON ONLY.

Real ingest into `team_matchups` is NOT IMPLEMENTED. The worker
exposes:

    - `probe()`              → planned SGO schedule endpoint
                                 (read-only metadata)
    - `dry_run_ingest(...)`  → enumerates the schedule rows a real
                                 run would write WITHOUT touching SGO
                                 or `team_matchups`

Architecture: §1.2 (`team_matchups`).
"""
from __future__ import annotations

from typing import Any, Dict

from .base import TeamWorkerBase

_PLANNED_SCHEDULE_ENDPOINTS: Dict[str, str] = {
    "mlb": "/v2/schedule?league=MLB",
    "nba": "/v2/schedule?league=NBA",
    "nfl": "/v2/schedule?league=NFL",
}


class TeamMatchupsIngestWorker(TeamWorkerBase):
    """Daily schedule ingest worker (skeleton).

    Phase 1.A.2 scope:
      - no SGO calls
      - no `team_matchups` writes
    """

    WORKER_KEY = "team_matchups_ingest"

    def probe(self) -> Dict[str, Any]:
        ok, reasons = self.dispatch_guard_ok()
        return {
            "worker":           self.WORKER_KEY,
            "sport":            self.sport,
            "requires_sgo_key": self.requires_sgo_key(),
            "dispatch_allowed": ok,
            "dispatch_reasons": reasons,
            "would_write":      "team_matchups",
            "planned_endpoint": _PLANNED_SCHEDULE_ENDPOINTS[self.sport],
        }

    def dry_run_ingest(
        self,
        start_date: str,
        end_date: str,
    ) -> Dict[str, Any]:
        """Enumerate a scheduled ingest pass — no SGO, no Mongo."""
        return {
            "worker":           self.WORKER_KEY,
            "sport":            self.sport,
            "mode":             "dry_run",
            "would_write":      "team_matchups",
            "planned_endpoint": _PLANNED_SCHEDULE_ENDPOINTS[self.sport],
            "start_date":       start_date,
            "end_date":         end_date,
            "note": (
                "Phase 1.A.2 skeleton — no SGO call, no schedule "
                "row enumerated. Real ingest lands in Phase 1.A.4."
            ),
        }

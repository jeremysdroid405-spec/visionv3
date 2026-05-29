"""
Team outcomes grader worker — Phase 1.A.2 SKELETON ONLY.

Real grading into `team_prop_outcomes` is NOT IMPLEMENTED. The
worker exposes:

    - `probe()`                → planned grading inputs + target
                                   collection (read-only metadata)
    - `dry_run_grade(...)`     → enumerates grading work without
                                   resolving any actual outcome
    - `_resolve_team_id(...)`  → READ-ONLY lookup against
                                   `team_master_hub` only

Architecture: §1.2 (`team_prop_outcomes`).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import TeamWorkerBase

# Markets we will need to grade in Phase 1.B (informational only).
_PLANNED_MARKETS: Dict[str, List[str]] = {
    "mlb": [
        "team_total_runs", "team_total_hits", "first_inning_runs",
        "first_five_innings_total",
    ],
    "nba": [
        "team_total_points", "first_quarter_total", "first_half_total",
    ],
    "nfl": [
        "team_total_points", "first_half_total",
        "team_total_passing_yards", "team_total_rushing_yards",
    ],
}


class TeamOutcomesGrader(TeamWorkerBase):
    """Post-game grading worker (skeleton).

    Phase 1.A.2 scope:
      - no SGO box-score calls
      - no `team_prop_outcomes` writes
      - read-only `team_master_hub` lookups via `_resolve_team_id`
    """

    WORKER_KEY = "team_outcomes_grader"

    def probe(self) -> Dict[str, Any]:
        ok, reasons = self.dispatch_guard_ok()
        return {
            "worker":           self.WORKER_KEY,
            "sport":            self.sport,
            "requires_sgo_key": self.requires_sgo_key(),
            "dispatch_allowed": ok,
            "dispatch_reasons": reasons,
            "would_write":      "team_prop_outcomes",
            "planned_markets":  _PLANNED_MARKETS[self.sport],
        }

    def dry_run_grade(
        self,
        candidate_event_ids: List[str] | None = None,
    ) -> Dict[str, Any]:
        """Enumerate the grading work — no Mongo write, no SGO call."""
        events = list(candidate_event_ids or [])
        return {
            "worker":             self.WORKER_KEY,
            "sport":              self.sport,
            "mode":               "dry_run",
            "would_write":        "team_prop_outcomes",
            "planned_markets":    _PLANNED_MARKETS[self.sport],
            "n_candidate_events": len(events),
            "event_ids":          events[:50],
            "note": (
                "Phase 1.A.2 skeleton — no resolver evaluation, "
                "no actual outcome attached. Real grading lands in "
                "Phase 1.B."
            ),
        }

    async def _resolve_team_id(
        self, db, *, team_name: str,
    ) -> Optional[str]:
        """Read-only lookup against `team_master_hub` — Phase 1.A.1's
        seeded collection. Never writes. Never calls SGO.

        Matches on `display_names.full / short / abbrev / market`
        for the worker's sport.
        """
        if not team_name:
            return None
        needle = team_name.strip()
        doc = await db["team_master_hub"].find_one(
            {
                "sport": self.sport,
                "$or": [
                    {"display_names.full":   needle},
                    {"display_names.short":  needle},
                    {"display_names.abbrev": needle},
                    {"display_names.market": needle},
                ],
            },
            {"_id": 0, "team_id": 1},
        )
        return doc.get("team_id") if doc else None

"""
Team odds ingest worker — Phase 1.A.2 SKELETON ONLY.

Real ingest into `team_live_props` is NOT IMPLEMENTED. The worker
exposes:

    - `probe()`               → planned SGO endpoint + market list
                                  for the worker's sport (read-only
                                  metadata, no network call)
    - `dry_run_promote(...)`  → enumerates what a real promotion
                                  from `team_live_props` →
                                  `team_historical_props` would do
                                  WITHOUT touching either collection

Architecture: §1.2 (`team_live_props` / `team_historical_props`).
"""
from __future__ import annotations

from typing import Any, Dict, List

from .base import TeamWorkerBase

# Planned SGO endpoints + markets per sport. NEVER hit at probe time.
# Used by the orchestrator UI (later) to show the operator what a
# real run would request.
_PLANNED_ENDPOINTS: Dict[str, Dict[str, Any]] = {
    "mlb": {
        "sgo_path": "/v2/events",
        "league_filter": "MLB",
        "markets": [
            "team_total_runs",
            "team_total_hits",
            "first_inning_runs",
            "first_five_innings_total",
        ],
    },
    "nba": {
        "sgo_path": "/v2/events",
        "league_filter": "NBA",
        "markets": [
            "team_total_points",
            "first_quarter_total",
            "first_half_total",
        ],
    },
    "nfl": {
        "sgo_path": "/v2/events",
        "league_filter": "NFL",
        "markets": [
            "team_total_points",
            "first_half_total",
            "team_total_passing_yards",
            "team_total_rushing_yards",
        ],
    },
}


class TeamOddsIngestWorker(TeamWorkerBase):
    """Real-time `team_live_props` ingest worker (skeleton).

    Phase 1.A.2 scope:
      - no SGO calls
      - no Mongo writes
      - no live snapshot polling
    """

    WORKER_KEY = "team_odds_ingest"

    def probe(self) -> Dict[str, Any]:
        """Return planned (SGO path, markets) for the worker's sport.

        ZERO network calls. ZERO Mongo writes. Used by Phase 1.A.2
        smoke tests + the admin status endpoint to confirm the
        worker mapping is wired without touching prod.
        """
        cfg = _PLANNED_ENDPOINTS[self.sport]
        ok, reasons = self.dispatch_guard_ok()
        return {
            "worker":        self.WORKER_KEY,
            "sport":         self.sport,
            "requires_sgo_key": self.requires_sgo_key(),
            "dispatch_allowed": ok,
            "dispatch_reasons": reasons,
            "planned":       cfg,
        }

    def dry_run_promote(
        self,
        candidate_event_ids: List[str] | None = None,
    ) -> Dict[str, Any]:
        """Enumerate what a real `live → historical` promotion would
        do for the given candidate event_ids.

        SKELETON: no DB read, no DB write. Just echoes the input
        and the target collection so the orchestrator can wire the
        UI without dispatching real work.
        """
        events = list(candidate_event_ids or [])
        return {
            "worker":        self.WORKER_KEY,
            "sport":         self.sport,
            "mode":          "dry_run",
            "would_read":    "team_live_props",
            "would_write":   "team_historical_props",
            "n_candidate_events": len(events),
            "event_ids":     events[:50],   # truncated preview
            "note": (
                "Phase 1.A.2 skeleton — no Mongo read or write "
                "performed. Real promotion lands in Phase 1.A.3."
            ),
        }

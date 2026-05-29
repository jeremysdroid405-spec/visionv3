"""
Shared base for Phase 1.A.2 team workers.

Defines the double dispatch guard (`TEAM_INGEST_ENABLED` + `SGO_API_KEY`)
and the `TeamWorkerBase` ABC with sport routing.
"""
from __future__ import annotations

import os
from abc import ABC
from typing import Tuple

SUPPORTED_SPORTS = frozenset({"mlb", "nba", "nfl"})


class TeamIngestDisabled(RuntimeError):
    """Raised when a worker method that would call SGO or perform
    a real write is invoked while the dispatch guard is closed.

    The guard is intentionally fail-closed: missing env var ⇒
    no dispatch, no exceptions swallowed, no silent no-op.
    """


def requires_sgo_key() -> bool:
    """Single source of truth for the orchestrator: team workers
    cannot run a real ingest pass without an SGO key release.
    """
    return True


def dispatch_guard_ok() -> Tuple[bool, list]:
    """Return (allowed, reasons). The reasons list enumerates which
    env vars are missing — useful for the admin status endpoint.
    """
    reasons: list[str] = []
    if not os.environ.get("SGO_API_KEY"):
        reasons.append("SGO_API_KEY env var is missing")
    if os.environ.get("TEAM_INGEST_ENABLED", "0") != "1":
        reasons.append("TEAM_INGEST_ENABLED is not set to '1'")
    return (len(reasons) == 0, reasons)


class TeamWorkerBase(ABC):
    """Abstract base — every concrete team worker subclasses this
    and exposes `probe()` and the `dry_run_*` enumerator(s).

    Sport is taken as a constructor arg (option B from the user-
    approved scope). Sport-specific branching is deferred until
    real ingest logic proves where it is needed.
    """

    #: Concrete subclasses set this to a stable identifier the
    #: orchestrator can route by.
    WORKER_KEY: str = "team_worker_base"

    def __init__(self, sport: str) -> None:
        sport_l = (sport or "").lower()
        if sport_l not in SUPPORTED_SPORTS:
            raise ValueError(
                f"unsupported sport: {sport!r}. "
                f"Supported: {sorted(SUPPORTED_SPORTS)}"
            )
        self.sport: str = sport_l

    # ── Dispatch guard helpers ────────────────────────────────────
    @classmethod
    def requires_sgo_key(cls) -> bool:
        return requires_sgo_key()

    @classmethod
    def dispatch_guard_ok(cls) -> Tuple[bool, list]:
        return dispatch_guard_ok()

    def _require_dispatch(self) -> None:
        ok, reasons = dispatch_guard_ok()
        if not ok:
            raise TeamIngestDisabled(
                f"team ingest is disabled: {'; '.join(reasons)}. "
                "Phase 1.A.2 is preview-only — both "
                "TEAM_INGEST_ENABLED=1 and SGO_API_KEY must be "
                "explicitly released before real dispatch."
            )

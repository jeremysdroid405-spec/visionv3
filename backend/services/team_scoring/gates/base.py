"""
GateAdapter — shared CONTRACT for the hybrid scoring layer (§4.3).

Both player-prop and team-prop scoring paths will eventually
implement this ABC with their own threshold tables. Phase 1.A.0
ships ONLY the contract here. No team implementation. No player-side
code modification.

Architecture: /app/memory/TEAM_PROPS_ARCHITECTURE.md §4.3 + §11.10.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# §4.3 — Three universal tiers, identical across player and team
# pipelines. Routing by odds value happens in the universal
# tier router, NOT here. This list pins the canonical tier names so
# they can never drift.
TIER_NAMES = ("safe_haven", "front_lines", "war_zone")


@dataclass(frozen=True)
class GateDecision:
    """Output of every gate evaluation.

    Identical shape on player + team paths so the optimizer can
    consume both without branching (§14.1 forward-compat).
    """
    safe_haven_pass: bool
    front_lines_pass: bool
    war_zone_pass: bool
    selected_tier: Optional[str]    # one of TIER_NAMES, or None if no tier passes
    failed_gates: List[str]         # human-readable reasons (e.g. ["edge_too_low"])

    def __post_init__(self) -> None:
        if self.selected_tier is not None and self.selected_tier not in TIER_NAMES:
            raise ValueError(
                f"selected_tier={self.selected_tier!r} must be one of "
                f"{TIER_NAMES} or None"
            )


class GateAdapter(ABC):
    """Sport- and entity-aware gate engine.

    Each implementation owns its own threshold table. Player and
    team versions are deliberately separate so a team-side bug
    cannot regress player gates.
    """

    #: Implementation identifier — e.g. "player_universal_v3" or
    #: "team_mlb_runs_v1". Persisted on the scored row as
    #: `gate_config_version` for replay reproducibility.
    gate_config_version: str = ""

    @abstractmethod
    def evaluate(self, row: Dict[str, Any]) -> GateDecision:
        """Return tier-pass decisions for ONE scored row.

        The row dict must carry at minimum:
          tp, edge, vision_score, hit_rate_l20, hit_rate_l10,
          cv, book_count, sharp_anchor_count,
          line_movement_open_to_close
        (See §11.10 for the full schema.)
        """
        raise NotImplementedError

"""
TeamTPAdapter — contract for converting a TeamProjection + book quotes
into a True Probability with provenance.

Architecture: /app/memory/TEAM_PROPS_ARCHITECTURE.md §3 + §11.9.

Phase 1.A.0 ships the ABC + dataclass only. No math.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List

from services.team_projections import TeamProjection

# §11.9 step 5 — tp_source provenance. Every TP result must declare
# how it was computed.
TP_SOURCES = frozenset({
    "model",   # pure model output (rare; cold-start regime)
    "blend",   # α·model + (1-α)·fair (typical production)
    "market",  # fall back to multi-book devigged fair_p
})


@dataclass(frozen=True)
class TeamTPResult:
    """Output of every TP computation."""
    tp: float                       # bounded [0, 1]
    tp_source: str                  # one of TP_SOURCES
    model_probability: float        # bounded [0, 1]
    fair_probability: float         # bounded [0, 1]
    implied_probability: float      # bounded [0, 1]
    edge: float                     # tp - implied_probability
    n_books_for_devig: int
    n_reference_only_skipped: int   # §14.3 audit field
    alpha_used: float               # the blend weight in [0.2, 0.8]

    def __post_init__(self) -> None:
        for name, val in (("tp", self.tp),
                          ("model_probability", self.model_probability),
                          ("fair_probability", self.fair_probability),
                          ("implied_probability", self.implied_probability)):
            if not (0.0 <= val <= 1.0):
                raise ValueError(
                    f"{name}={val} must be in [0, 1] — scale-mix bug? "
                    f"(lesson learned from player-side percent-vs-prob TP-scale incident)"
                )
        if self.tp_source not in TP_SOURCES:
            raise ValueError(
                f"tp_source={self.tp_source!r} not in TP_SOURCES={sorted(TP_SOURCES)}"
            )
        if not (0.2 <= self.alpha_used <= 0.8):
            raise ValueError(
                f"alpha_used={self.alpha_used} must be bounded in [0.2, 0.8] "
                f"per §11.9 — neither model nor market may dominate on thin data."
            )
        if self.n_books_for_devig < 0 or self.n_reference_only_skipped < 0:
            raise ValueError("book counters cannot be negative")


class TeamTPAdapter(ABC):
    """Compute TP for one (event, team, market, line, side) given
    its projection + live book quotes.

    Subclasses live under `services/team_tp/` and are dispatched by
    (sport, market) in the runtime registry. Reference-only books
    (PrizePicks, Underdog) MUST be excluded from devig math — same
    policy enforced on the player side via `team_policy`.
    """

    @abstractmethod
    def compute(
        self,
        *,
        projection: TeamProjection,
        line: float,
        side: str,
        book_quotes: List[Dict[str, Any]],
    ) -> TeamTPResult:
        """Return a TeamTPResult.

        `book_quotes` is the list of per-book quotes available at
        score time. Implementations must:
          1. Compute `model_probability` from `projection` + (line, side).
          2. Filter `book_quotes` against `team_policy.BLOCKED_BOOKS`
             and `team_policy.REFERENCE_ONLY_BOOKS`.
          3. Compute `fair_probability` via multi-book devig over
             the filtered set.
          4. Compute `tp` via α-weighted blend (§11.9 step 3).
          5. Record `tp_source` provenance.
        """
        raise NotImplementedError

"""Sport-agnostic Opportunity Model base interface (2026-04-23).

Every sport implements a small `OpportunityAdapter` that produces one
canonical `OpportunityOutput` per player per game. The output is the
**opportunity signal** — expected playing time / usage surface — which
downstream stat models consume as a feature, NOT as a replacement.

Mapping:
  NBA : opportunity_type='minutes', value = expected minutes played
  MLB : opportunity_type='plate_appearances' (scaffold), value = expected PA
  NFL : opportunity_type='snaps' (scaffold), value = expected snap share

Consumer contract:
  {
    "sport": "nba|mlb|nfl",
    "player_id": "<sport-native id>",
    "bdl_player_id": <int|None>,   # only meaningful for NBA
    "expected_opportunity": number,
    "opportunity_type": "minutes|plate_appearances|snaps|...",
    "opportunity_bucket": "high|medium|low",
    "opportunity_risk_score": number,   # 0..1 probability of a low-opportunity outcome
    "opportunity_confidence": number,   # 0..1; 1 = tight prediction, 0 = very uncertain
  }

Adapters must implement:
  predict(player_ctx) -> OpportunityOutput
  predict_batch(contexts) -> List[OpportunityOutput]

Nothing in this module touches projections, gates, or scoring.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Literal, Optional


Sport = Literal["nba", "mlb", "nfl"]
OpportunityType = Literal[
    "minutes",            # NBA
    "plate_appearances",  # MLB
    "innings",            # MLB pitcher
    "snaps",              # NFL
    "routes",             # NFL receiver
    "carries",            # NFL RB
    "targets",            # NFL receiver
]
OpportunityBucket = Literal["high", "medium", "low"]


@dataclass
class OpportunityOutput:
    sport: Sport
    player_id: str
    bdl_player_id: Optional[int]
    expected_opportunity: float
    opportunity_type: OpportunityType
    opportunity_bucket: OpportunityBucket
    opportunity_risk_score: float
    opportunity_confidence: float
    # Optional metadata captured for audit / admin UI.
    model_version: Optional[str] = None
    features_used: Optional[List[str]] = None
    # Free-form sport-specific extras; kept in the contract for
    # forward-compat (NFL will want snap-share vs snap-count etc.).
    extras: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # Always ensure numeric outputs are JSON-safe.
        d["expected_opportunity"] = float(d["expected_opportunity"])
        d["opportunity_risk_score"] = float(d["opportunity_risk_score"])
        d["opportunity_confidence"] = float(d["opportunity_confidence"])
        return d


@dataclass
class PlayerContext:
    """Input to an adapter.

    Sport-agnostic keys:
      sport, player_id, bdl_player_id (NBA), game_id, game_date.
    Sport-specific extras go into `history_logs` and `situational`.
    """
    sport: Sport
    player_id: str
    game_id: Optional[int] = None
    game_date: Optional[Any] = None
    bdl_player_id: Optional[int] = None
    team_id: Optional[int] = None
    opponent_team_id: Optional[int] = None
    # `history_logs` is chronological-DESCENDING (newest first) — same
    # convention as VK2 / minutes / classifier trainers.
    history_logs: List[Dict[str, Any]] = field(default_factory=list)
    situational: Dict[str, float] = field(default_factory=dict)


class OpportunityAdapter(ABC):
    """Abstract base class. Every sport adapter must implement these."""

    sport: Sport

    @abstractmethod
    def predict(self, ctx: PlayerContext) -> OpportunityOutput:
        """Return one OpportunityOutput for a single player context."""

    def predict_batch(self, contexts: List[PlayerContext]) -> List[OpportunityOutput]:
        """Default: just map predict. Sport adapters can override for
        batch-prediction performance."""
        return [self.predict(c) for c in contexts]


def bucket_from_value(
    expected: float, high_threshold: float, low_threshold: float,
) -> OpportunityBucket:
    """Generic helper — high/medium/low bucket from a scalar value and
    two thresholds (adapters pass sport-specific cutoffs)."""
    if expected >= high_threshold:
        return "high"
    if expected < low_threshold:
        return "low"
    return "medium"

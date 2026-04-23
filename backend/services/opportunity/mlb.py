"""MLB Opportunity Adapter — SCAFFOLD ONLY (2026-04-23).

Intended design (not yet implemented):
  * Hitter: `expected_plate_appearances` derived from lineup spot
    (1-9), opposing starter TBF rate, pinch-hit probability.
  * Pitcher: `expected_innings` derived from role (SP/RP),
    rolling IP trend, pitch-count limits, rest.

The adapter conforms to `OpportunityAdapter` so the scoring layer
can discover and feed MLB opportunity features once implemented.
Until then, `predict()` returns a medium-bucket / zero-confidence
sentinel so downstream stat models ignore it.
"""
from __future__ import annotations

from .base import OpportunityAdapter, OpportunityOutput, PlayerContext


class MLBOpportunityAdapter(OpportunityAdapter):
    sport = "mlb"

    def predict(self, ctx: PlayerContext) -> OpportunityOutput:
        return OpportunityOutput(
            sport=self.sport,
            player_id=str(ctx.player_id),
            bdl_player_id=None,
            expected_opportunity=0.0,
            opportunity_type="plate_appearances",
            opportunity_bucket="low",
            opportunity_risk_score=1.0,
            opportunity_confidence=0.0,
            model_version="MLB_OPPORTUNITY_SCAFFOLD",
            features_used=[],
            extras={"status": "not_implemented"},
        )

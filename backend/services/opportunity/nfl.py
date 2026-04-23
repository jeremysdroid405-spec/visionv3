"""NFL Opportunity Adapter — SCAFFOLD ONLY (2026-04-23).

Intended design (not yet implemented):
  * RB: expected_carries + expected_targets.
  * WR/TE: expected_routes + expected_targets + snap_share.
  * QB: expected_dropbacks.
  * Defense: stat-specific usage (not position-minutes).

The adapter conforms to `OpportunityAdapter` so the scoring layer
can discover and feed NFL opportunity features once implemented.
Until then, `predict()` returns a medium-bucket / zero-confidence
sentinel so downstream stat models ignore it.
"""
from __future__ import annotations

from .base import OpportunityAdapter, OpportunityOutput, PlayerContext


class NFLOpportunityAdapter(OpportunityAdapter):
    sport = "nfl"

    def predict(self, ctx: PlayerContext) -> OpportunityOutput:
        return OpportunityOutput(
            sport=self.sport,
            player_id=str(ctx.player_id),
            bdl_player_id=None,
            expected_opportunity=0.0,
            opportunity_type="snaps",
            opportunity_bucket="low",
            opportunity_risk_score=1.0,
            opportunity_confidence=0.0,
            model_version="NFL_OPPORTUNITY_SCAFFOLD",
            features_used=[],
            extras={"status": "not_implemented"},
        )

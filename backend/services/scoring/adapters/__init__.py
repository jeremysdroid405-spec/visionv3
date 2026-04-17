"""
Scoring Adapters — Sport-Specific Scoring Bridges
==================================================
Each sport adapter:
  - Declares its live props collection name
  - Declares its output scores collection name
  - Normalizes a raw live prop into a standard "scoring context"
    (canonical_key, layer dicts, p_model, cv, hit_rate, edge_pct, tp, etc.)
  - Provides a tier sorter with the three check_*_gates methods
    required by services.scoring.scoring_stack.compute_tier

The orchestration layer (services.scoring.recompute) is 100% sport-agnostic
and composes these adapters to recompute and persist scoring stacks.
"""
from services.scoring.adapters.base import ScoringAdapter, ScoringContext
from services.scoring.adapters.mlb_scoring import MLBScoringAdapter
from services.scoring.adapters.nba_scoring import NBAScoringAdapter

# Registry of supported sports
SCORING_ADAPTERS = {
    "mlb": MLBScoringAdapter,
    "nba": NBAScoringAdapter,
}

SUPPORTED_SPORTS = tuple(SCORING_ADAPTERS.keys())


def get_scoring_adapter(sport: str) -> ScoringAdapter:
    sport = (sport or "").lower()
    if sport not in SCORING_ADAPTERS:
        raise ValueError(
            f"Unsupported sport '{sport}'. Supported: {SUPPORTED_SPORTS}"
        )
    return SCORING_ADAPTERS[sport]()


__all__ = [
    "ScoringAdapter", "ScoringContext",
    "MLBScoringAdapter", "NBAScoringAdapter",
    "SCORING_ADAPTERS", "SUPPORTED_SPORTS", "get_scoring_adapter",
]

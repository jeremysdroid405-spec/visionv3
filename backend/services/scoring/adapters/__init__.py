"""
Scoring Adapters — Sport-Specific METRIC NORMALIZATION Bridges
===============================================================
Each sport adapter:
  - Declares its live props collection name
  - Declares its output scores collection name
  - Normalizes a raw live prop into a standard ``ScoringContext``
    (canonical_key, layer dicts, p_model, cv, hit_rate, edge_pct, tp,
    ceiling_rate, book_count, etc.)

Adapters contain ONLY metric-normalization logic. Every eligibility
decision — including tier gates, reason codes, and per-stat / per-tier
thresholds — is handled by the single Universal Gate Engine in
`services.scoring.gates`. Adding a new sport is a pure config change
(drop thresholds into `services.scoring.gates.thresholds.THRESHOLDS`)
plus one metric-normalization adapter.
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

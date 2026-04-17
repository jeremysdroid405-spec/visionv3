"""
ScoringAdapter base class — sport-agnostic contract.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ScoringContext:
    """Standardized scoring context produced by a sport adapter."""
    canonical_key: str
    sport: str
    event_id: Optional[str]
    player_name: Optional[str]
    stat_type: Optional[str]
    line: Optional[float]
    recommendation: Optional[str]  # "OVER" / "UNDER"
    # Layer objects (all optional)
    pp_layer: Optional[Dict[str, Any]] = None
    dk_layer: Optional[Dict[str, Any]] = None
    mgm_layer: Optional[Dict[str, Any]] = None
    sharp_layer: Optional[Dict[str, Any]] = None
    # Model + stats inputs
    p_model: Optional[float] = None          # final model probability (0-1)
    cv: Optional[float] = None
    hit_rate: Optional[float] = None
    edge_pct: Optional[float] = None
    tp: Optional[float] = None
    ceiling_rate: Optional[float] = None
    books_available_count: int = 0
    # Passthrough full prop for tier-gate evaluation
    raw_prop: Dict[str, Any] = field(default_factory=dict)
    # Multiplier hints (for pp_utility when real data exists)
    pp_combo_multiplier: Optional[float] = None
    pp_label: Optional[str] = None           # goblin | standard | demon
    pp_multiplier_model: Optional[float] = None


class ScoringAdapter(ABC):
    """Sport-specific scoring adapter contract."""

    # ---------------------------------------------------------
    # Metadata
    # ---------------------------------------------------------
    @property
    @abstractmethod
    def sport(self) -> str:
        """Lowercase sport key, e.g. 'mlb', 'nba'."""

    @property
    @abstractmethod
    def live_props_collection(self) -> str:
        """Mongo collection with the canonical (or canonicalizable) live props."""

    @property
    @abstractmethod
    def scores_collection(self) -> str:
        """Output collection name, e.g. 'mlb_prop_scores'."""

    @property
    @abstractmethod
    def cached_board_collection(self) -> str:
        """Collection that must NOT be mutated by recompute (used for leak checks)."""

    # ---------------------------------------------------------
    # Behaviour
    # ---------------------------------------------------------
    @abstractmethod
    async def load_live_props(
        self, db, limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Read-only load from the sport's live props collection."""

    @abstractmethod
    async def build_context(
        self, db, prop: Dict[str, Any], config: Dict[str, Any]
    ) -> Optional[ScoringContext]:
        """Normalize a raw prop into a ScoringContext. Return None to skip."""

    @abstractmethod
    def get_sorter(self, db) -> Any:
        """
        Return an object exposing:
          check_safe_haven_gates(prop, cv, hit_rate, edge_pct, tp)
          check_front_lines_gates(prop, cv, hit_rate, edge_pct, tp)
          check_war_zone_gates(prop, cv, ceiling_rate, edge_pct)
        Each returns (passed: bool, reason: str, gate_results: dict).
        """

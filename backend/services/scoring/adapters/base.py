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
    # Diagnostic-only: alternative p_true candidates (not used by scoring_stack)
    p_true_hit_rate: Optional[float] = None
    p_true_model: Optional[float] = None
    p_true_method: Optional[str] = None       # "hit_rate" | "model" | "vk2"
    model_projection: Optional[float] = None  # raw regressor output (stat units)
    model_sigma: Optional[float] = None       # residual SD used for CDF conversion
    # VK2 (5-year adv-stat) diagnostics
    p_true_vk2: Optional[float] = None
    vk2_projection: Optional[float] = None
    vk2_sigma: Optional[float] = None
    vk2_error: Optional[str] = None
    # Side-aware hit-rate diagnostics (the one passed to gates is stored in hit_rate)
    hit_rate_over: Optional[float] = None
    hit_rate_under: Optional[float] = None


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
